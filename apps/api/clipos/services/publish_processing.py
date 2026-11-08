from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import (
    GateCheckpoint,
    JobState,
    MembershipStatus,
    PublishEventKind,
    PublishState,
    PublishVisibility,
    Role,
)
from clipos.core.errors import ClipOSError
from clipos.core.security import Principal
from clipos.gateways.publishing import (
    PublishingAdapterError,
    PublishingAdapterRegistry,
    PublishMetadata,
    ReconciliationStatus,
    RemotePublication,
    TokenEnvelopeError,
    UploadChunkResult,
)
from clipos.models import (
    BackgroundJob,
    Publication,
    QualityEvaluation,
    RenderOutput,
    TenantMembership,
    User,
)
from clipos.publishing_schemas import PublicationCreate
from clipos.queue import QueueBroker, QueueMessage
from clipos.services.publishing import PUBLISH_JOB_KIND, PublishingService
from clipos.services.quality import QualityGateService
from clipos.storage.base import ObjectStorage


class PublishProcessor:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        registry: PublishingAdapterRegistry,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.registry = registry
        self.settings = settings

    async def process_job(self, message: QueueMessage) -> None:
        async with self.session_factory() as session:
            publication = await session.scalar(
                select(Publication)
                .where(
                    Publication.background_job_id == message.job_id,
                    Publication.tenant_id == message.tenant_id,
                )
                .with_for_update()
            )
            if publication is None:
                await self._fail_orphan_background(session, message)
                return
            if publication.state in {PublishState.SUCCEEDED, PublishState.CANCELLED}:
                return
            if publication.state == PublishState.NEEDS_RECONCILIATION:
                await self._reconcile(session, publication)
                return
            if publication.state != PublishState.QUEUED:
                return
            if publication.cancellation_requested:
                await self._cancel_before_remote(session, publication)
                return
            if publication.attempt_count >= publication.max_attempts:
                await self._fail(
                    session,
                    publication,
                    category="retry_budget_exhausted",
                    message="publication retry budget is exhausted",
                    retryable=False,
                    manual_next_step=(
                        "Create a new release only after reviewing the failure evidence."
                    ),
                )
                return
            publication.attempt_count += 1
            publication.started_at = publication.started_at or datetime.now(UTC)
            await self._set_state(
                session,
                publication,
                state=PublishState.VALIDATING,
                progress=max(1, publication.progress_percent),
                stage="validating",
                kind=PublishEventKind.VALIDATION_STARTED,
                message="Fresh authorization, gate, destination, token, and output checks started.",
            )
            background = await session.get(BackgroundJob, publication.background_job_id)
            if background is not None:
                background.state = JobState.RUNNING
                background.attempts = publication.attempt_count
                background.started_at = publication.started_at
                background.heartbeat_at = datetime.now(UTC)
            await session.commit()
            await self._execute(session, publication)

    async def _execute(self, session: AsyncSession, publication: Publication) -> None:
        try:
            principal = await self._principal(session, publication)
            evaluation = await session.scalar(
                select(QualityEvaluation).where(
                    QualityEvaluation.id == publication.quality_evaluation_id,
                    QualityEvaluation.tenant_id == publication.tenant_id,
                    QualityEvaluation.render_job_id == publication.render_job_id,
                )
            )
            if evaluation is None:
                raise ClipOSError(
                    "quality_evaluation_missing",
                    "the bound quality evaluation is unavailable",
                    status_code=409,
                )
            if publication.visibility == PublishVisibility.PUBLIC:
                request = PublicationCreate(
                    connection_id=publication.connection_id,
                    render_job_id=publication.render_job_id,
                    quality_evaluation_id=publication.quality_evaluation_id,
                    idempotency_key=publication.idempotency_key,
                    visibility=publication.visibility,
                    title=publication.title,
                    description=publication.description,
                    tags=publication.tags,
                    made_for_kids=publication.made_for_kids,
                    category_id=publication.category_id,
                    public_approval_id=publication.public_approval_id,
                )
                await PublishingService._validate_public_approval(
                    session,
                    principal=principal,
                    request=request,
                    settings=self.settings,
                )
            verification = await QualityGateService.verify(
                session,
                principal=principal,
                evaluation_id=evaluation.id,
                checkpoint=GateCheckpoint.EXTERNAL_UPLOAD,
                expected_destination=evaluation.destination,
                expected_territory=evaluation.territory,
                settings=self.settings,
                storage=self.storage,
            )
            if not verification.accepted:
                await self._fail(
                    session,
                    publication,
                    category="mandatory_gate_denied",
                    message="the fresh pre-upload quality and rights gate was denied",
                    retryable=False,
                    manual_next_step=(
                        "Remediate the gate evidence and create a newly approved release."
                    ),
                    details={"failure_reasons": verification.failure_reasons},
                )
                return
            publication.upload_gate_verification_id = verification.id
            await PublishingService.append_event(
                session,
                publication=publication,
                kind=PublishEventKind.GATE_VERIFIED,
                from_state=publication.state,
                message="Fresh pre-upload quality and rights gate passed.",
                event_data={"verification_id": str(verification.id)},
            )
            await session.commit()

            grant = await PublishingService.usable_access_grant(
                session,
                tenant_id=publication.tenant_id,
                connection_id=publication.connection_id,
                settings=self.settings,
                registry=self.registry,
            )
            adapter = self.registry.get(publication.provider)
            destination = await adapter.validate_destination(access_token=grant.access_token)
            if destination.provider_destination_id != publication.provider_destination_id:
                raise ClipOSError(
                    "destination_changed",
                    "the authorized provider destination changed before upload",
                    status_code=409,
                )
            output = await session.scalar(
                select(RenderOutput).where(
                    RenderOutput.id == publication.render_output_id,
                    RenderOutput.tenant_id == publication.tenant_id,
                    RenderOutput.render_job_id == publication.render_job_id,
                    RenderOutput.is_current.is_(True),
                )
            )
            if output is None:
                raise ClipOSError(
                    "render_output_stale",
                    "the approved render output is no longer current",
                    status_code=409,
                )
            path = await self.storage.path_for_read(publication.tenant_id, output.storage_key)
            info = await self.storage.head(publication.tenant_id, output.storage_key)
            if (
                info is None
                or info.checksum_sha256 != output.checksum_sha256
                or info.size != output.size_bytes
                or info.size != publication.content_length
            ):
                raise ClipOSError(
                    "render_output_integrity_failed",
                    "the approved render output failed its final integrity check",
                    status_code=409,
                )
            if publication.cancellation_requested:
                await self._cancel_before_remote(session, publication)
                return
            session_uri = PublishingService.decrypt_upload_session(
                self.settings, publication=publication
            )
            if session_uri is None:
                upload_session = await adapter.initiate_upload(
                    access_token=grant.access_token,
                    metadata=PublishMetadata(
                        title=publication.title,
                        description=publication.description,
                        tags=tuple(publication.tags),
                        visibility=publication.visibility,
                        made_for_kids=publication.made_for_kids,
                        category_id=publication.category_id,
                    ),
                    content_type=publication.content_type,
                    content_length=publication.content_length,
                )
                session_uri = upload_session.session_uri
                publication.upload_session_envelope = PublishingService.encrypt_upload_session(
                    self.settings,
                    publication=publication,
                    session_uri=session_uri,
                )
                publication.upload_started_at = datetime.now(UTC)
                await self._set_state(
                    session,
                    publication,
                    state=PublishState.UPLOADING,
                    progress=max(5, publication.progress_percent),
                    stage="uploading",
                    kind=PublishEventKind.UPLOAD_INITIATED,
                    message="Provider resumable upload session initiated.",
                    details=upload_session.sanitized_metadata,
                )
                await session.commit()
            else:
                await self._set_state(
                    session,
                    publication,
                    state=PublishState.UPLOADING,
                    progress=max(5, publication.progress_percent),
                    stage="resuming_upload",
                    kind=PublishEventKind.UPLOAD_INITIATED,
                    message="Resuming the existing provider upload session.",
                )
                await session.commit()
            remote = await self._upload_file(
                session,
                publication=publication,
                path=path,
                access_token=grant.access_token,
                session_uri=session_uri,
            )
            if remote is None:
                return
            publication.provider_publication_id = remote.provider_publication_id
            publication.provider_url = remote.provider_url
            publication.provider_created_at = datetime.now(UTC)
            publication.provider_processing_status = "uploaded"
            publication.sanitized_provider_response = remote.sanitized_response
            await self._set_state(
                session,
                publication,
                state=PublishState.PROCESSING,
                progress=95,
                stage="provider_processing",
                kind=PublishEventKind.PROVIDER_PROCESSING,
                message="Upload completed; provider processing status is being reconciled.",
                details={"provider_publication_id": publication.provider_publication_id},
            )
            await session.commit()
            await self._poll_processing(
                session,
                publication=publication,
                access_token=grant.access_token,
            )
        except PublishingAdapterError as exc:
            await self._adapter_failure(session, publication, exc)
        except TokenEnvelopeError as exc:
            await self._fail(
                session,
                publication,
                category="upload_session_unreadable",
                message="the encrypted upload session could not be authenticated",
                retryable=False,
                manual_next_step="Reconcile provider state before creating a new release.",
                uncertain=True,
            )
            raise ClipOSError(
                "upload_session_unreadable",
                "encrypted upload session state is unavailable",
                status_code=409,
            ) from exc
        except ClipOSError as exc:
            await self._fail(
                session,
                publication,
                category=exc.code,
                message=exc.message,
                retryable=False,
                manual_next_step="Resolve the validation failure before creating a new release.",
            )

    async def _upload_file(
        self,
        session: AsyncSession,
        *,
        publication: Publication,
        path: Path,
        access_token: str,
        session_uri: str,
    ) -> UploadChunkResult | None:
        adapter = self.registry.get(publication.provider)
        offset = publication.committed_bytes
        with path.open("rb") as handle:
            handle.seek(offset)
            while offset < publication.content_length:
                if publication.cancellation_requested:
                    await self._needs_reconciliation(
                        session,
                        publication,
                        category="cancellation_during_upload",
                        message="Cancellation was requested after remote upload activity began.",
                    )
                    return None
                chunk = handle.read(
                    min(
                        self.settings.publishing_upload_chunk_bytes,
                        publication.content_length - offset,
                    )
                )
                if not chunk:
                    raise ClipOSError(
                        "render_output_truncated",
                        "the render output ended before its verified content length",
                        status_code=409,
                    )
                result = await adapter.upload_chunk(
                    access_token=access_token,
                    session_uri=session_uri,
                    chunk=chunk,
                    offset=offset,
                    total_bytes=publication.content_length,
                )
                if (
                    result.committed_bytes < offset
                    or result.committed_bytes > publication.content_length
                ):
                    raise ClipOSError(
                        "provider_progress_invalid",
                        "the provider returned an invalid committed upload range",
                        status_code=502,
                    )
                offset = result.committed_bytes
                publication.committed_bytes = offset
                publication.progress_percent = min(
                    90,
                    max(5, int((offset / publication.content_length) * 90)),
                )
                publication.stage = "uploading"
                await PublishingService.append_event(
                    session,
                    publication=publication,
                    kind=PublishEventKind.UPLOAD_PROGRESS,
                    from_state=publication.state,
                    message="Provider confirmed resumable upload progress.",
                    event_data={
                        "committed_bytes": offset,
                        "content_length": publication.content_length,
                    },
                )
                background = await session.get(BackgroundJob, publication.background_job_id)
                if background is not None:
                    background.heartbeat_at = datetime.now(UTC)
                await session.commit()
                if result.complete:
                    return result
                handle.seek(offset)
        await self._needs_reconciliation(
            session,
            publication,
            category="final_response_missing",
            message="All bytes were committed but no final provider identifier was confirmed.",
        )
        return None

    async def _poll_processing(
        self,
        session: AsyncSession,
        *,
        publication: Publication,
        access_token: str,
    ) -> None:
        if not publication.provider_publication_id:
            await self._needs_reconciliation(
                session,
                publication,
                category="provider_id_missing",
                message="Provider processing cannot be checked without a publication identifier.",
            )
            return
        adapter = self.registry.get(publication.provider)
        for poll in range(self.settings.publishing_processing_max_polls):
            remote = await adapter.get_processing_status(
                access_token=access_token,
                provider_publication_id=publication.provider_publication_id,
            )
            publication.last_reconciled_at = datetime.now(UTC)
            publication.provider_processing_status = remote.processing_status
            publication.sanitized_provider_response = remote.sanitized_response
            if remote.failure_reason:
                await self._fail(
                    session,
                    publication,
                    category="provider_processing_failed",
                    message=remote.failure_reason,
                    retryable=False,
                    manual_next_step=(
                        "Inspect the provider rejection and create a corrected release."
                    ),
                )
                return
            if remote.status == ReconciliationStatus.ABSENT:
                await self._needs_reconciliation(
                    session,
                    publication,
                    category="provider_publication_missing",
                    message="The provider no longer returned the uploaded publication.",
                )
                return
            if remote.status == ReconciliationStatus.UNKNOWN:
                if poll + 1 >= self.settings.publishing_processing_max_polls:
                    break
            elif remote.processing_status in {"processed", "succeeded"}:
                await self._succeed(session, publication, remote)
                return
            elif remote.processing_status in {"failed", "rejected", "deleted"}:
                await self._fail(
                    session,
                    publication,
                    category="provider_processing_failed",
                    message="the provider reported a terminal processing failure",
                    retryable=False,
                    manual_next_step="Inspect the provider status and create a corrected release.",
                )
                return
            if poll + 1 < self.settings.publishing_processing_max_polls:
                await asyncio.sleep(self.settings.publishing_processing_poll_seconds)
        await self._needs_reconciliation(
            session,
            publication,
            category="provider_processing_pending",
            message="Provider processing did not reach a terminal state within this worker run.",
        )

    async def _reconcile(self, session: AsyncSession, publication: Publication) -> None:
        try:
            grant = await PublishingService.usable_access_grant(
                session,
                tenant_id=publication.tenant_id,
                connection_id=publication.connection_id,
                settings=self.settings,
                registry=self.registry,
            )
            session_uri = PublishingService.decrypt_upload_session(
                self.settings, publication=publication
            )
            remote = await self.registry.get(publication.provider).reconcile(
                access_token=grant.access_token,
                provider_publication_id=publication.provider_publication_id,
                session_uri=session_uri,
                total_bytes=publication.content_length,
            )
            publication.last_reconciled_at = datetime.now(UTC)
            await PublishingService.append_event(
                session,
                publication=publication,
                kind=PublishEventKind.RECONCILIATION,
                from_state=publication.state,
                message=f"Provider reconciliation returned {remote.status.value}.",
                event_data={
                    "status": remote.status.value,
                    "processing_status": remote.processing_status,
                    "committed_bytes": remote.committed_bytes,
                },
            )
            if remote.status == ReconciliationStatus.UNKNOWN:
                publication.stage = "reconciliation_required"
                publication.manual_next_step = (
                    "Provider state remains uncertain. Retry reconciliation later; do not submit a "
                    "new release."
                )
                await session.commit()
                return
            if remote.status == ReconciliationStatus.ABSENT:
                if publication.cancellation_requested:
                    publication.upload_session_envelope = None
                    await self._cancel_before_remote(session, publication)
                    return
                publication.upload_session_envelope = None
                publication.committed_bytes = 0
                publication.state = PublishState.QUEUED
                publication.stage = "reconciled_absent_retry_queued"
                publication.retryable = True
                background = await session.get(BackgroundJob, publication.background_job_id)
                if background is not None:
                    background.state = JobState.QUEUED
                await session.commit()
                await self.process_job(
                    QueueMessage(
                        job_id=publication.background_job_id,
                        tenant_id=publication.tenant_id,
                        kind=PUBLISH_JOB_KIND,
                    )
                )
                return
            if remote.committed_bytes is not None:
                publication.committed_bytes = remote.committed_bytes
            if remote.provider_publication_id:
                publication.provider_publication_id = remote.provider_publication_id
                publication.provider_url = remote.provider_url
                publication.provider_processing_status = remote.processing_status
            if remote.status == ReconciliationStatus.IN_PROGRESS:
                if publication.cancellation_requested:
                    publication.stage = "remote_upload_exists_cancellation_unconfirmed"
                    publication.manual_next_step = (
                        "The provider still has a resumable upload. Wait for expiry or inspect the "
                        "provider before creating another release."
                    )
                    await session.commit()
                    return
                publication.state = PublishState.QUEUED
                publication.stage = "resume_after_reconciliation"
                publication.retryable = True
                background = await session.get(BackgroundJob, publication.background_job_id)
                if background is not None:
                    background.state = JobState.QUEUED
                await session.commit()
                await self.process_job(
                    QueueMessage(
                        job_id=publication.background_job_id,
                        tenant_id=publication.tenant_id,
                        kind=PUBLISH_JOB_KIND,
                    )
                )
                return
            if remote.failure_reason:
                await self._fail(
                    session,
                    publication,
                    category="provider_processing_failed",
                    message=remote.failure_reason,
                    retryable=False,
                    manual_next_step=(
                        "Inspect the provider rejection before creating a new release."
                    ),
                )
                return
            if remote.processing_status in {"processed", "succeeded"}:
                await self._succeed(session, publication, remote)
                return
            publication.state = PublishState.PROCESSING
            publication.stage = "provider_processing"
            publication.progress_percent = max(publication.progress_percent, 95)
            await session.commit()
            await self._poll_processing(
                session,
                publication=publication,
                access_token=grant.access_token,
            )
        except PublishingAdapterError as exc:
            await self._adapter_failure(session, publication, exc)
        except (ClipOSError, TokenEnvelopeError) as exc:
            await self._fail(
                session,
                publication,
                category=getattr(exc, "code", "reconciliation_state_invalid"),
                message=getattr(
                    exc,
                    "message",
                    "encrypted reconciliation state could not be authenticated",
                ),
                retryable=False,
                uncertain=True,
                manual_next_step=(
                    "Do not retry the upload until provider state is manually checked."
                ),
            )

    async def _succeed(
        self, session: AsyncSession, publication: Publication, remote: RemotePublication
    ) -> None:
        source = publication.state
        publication.state = PublishState.SUCCEEDED
        publication.stage = "succeeded"
        publication.progress_percent = 100
        publication.committed_bytes = publication.content_length
        publication.provider_publication_id = (
            remote.provider_publication_id or publication.provider_publication_id
        )
        publication.provider_url = remote.provider_url or publication.provider_url
        publication.provider_processing_status = remote.processing_status or "processed"
        publication.sanitized_provider_response = remote.sanitized_response
        publication.analytics_handoff = {
            "schema": "clipos-analytics-handoff.v1",
            "provider": publication.provider.value,
            "provider_publication_id": publication.provider_publication_id,
            "provider_url": publication.provider_url,
            "publication_id": str(publication.id),
            "visibility": publication.visibility.value,
            "published_at": datetime.now(UTC).isoformat(),
            "phase_7_status": "implemented",
        }
        publication.failure_category = None
        publication.failure_message = None
        publication.retryable = False
        publication.uncertain_remote_result = False
        publication.finished_at = datetime.now(UTC)
        await PublishingService.append_event(
            session,
            publication=publication,
            kind=PublishEventKind.SUCCEEDED,
            from_state=source,
            message="Provider upload and processing completed successfully.",
            event_data={
                "provider_publication_id": publication.provider_publication_id,
                "provider_url": publication.provider_url,
                "analytics_handoff_ready": True,
            },
        )
        background = await session.get(BackgroundJob, publication.background_job_id)
        if background is not None:
            background.state = JobState.SUCCEEDED
            background.result = {
                "publication_id": str(publication.id),
                "provider_publication_id": publication.provider_publication_id,
                "provider_url": publication.provider_url,
            }
            background.finished_at = publication.finished_at
            background.heartbeat_at = publication.finished_at
            background.failure_category = None
            background.failure_message = None
        await session.commit()

    async def _adapter_failure(
        self,
        session: AsyncSession,
        publication: Publication,
        exc: PublishingAdapterError,
    ) -> None:
        if exc.uncertain_remote_result:
            await self._needs_reconciliation(
                session,
                publication,
                category=exc.category,
                message=exc.safe_message,
                manual_next_step=exc.manual_next_step,
            )
            return
        await self._fail(
            session,
            publication,
            category=exc.category,
            message=exc.safe_message,
            retryable=exc.retryable,
            manual_next_step=exc.manual_next_step,
            retry_after_seconds=exc.retry_after_seconds,
        )

    async def _needs_reconciliation(
        self,
        session: AsyncSession,
        publication: Publication,
        *,
        category: str,
        message: str,
        manual_next_step: str | None = None,
    ) -> None:
        source = publication.state
        publication.state = PublishState.NEEDS_RECONCILIATION
        publication.stage = "reconciliation_required"
        publication.failure_category = category[:100]
        publication.failure_message = message[:500]
        publication.retryable = False
        publication.uncertain_remote_result = True
        publication.manual_next_step = manual_next_step or (
            "Run reconciliation before retrying so a successful remote upload is not duplicated."
        )
        await PublishingService.append_event(
            session,
            publication=publication,
            kind=PublishEventKind.RECONCILIATION,
            from_state=source,
            message=message,
            event_data={"category": category, "remote_success_possible": True},
        )
        background = await session.get(BackgroundJob, publication.background_job_id)
        if background is not None:
            background.state = JobState.NEEDS_REVIEW
            background.failure_category = category[:100]
            background.failure_message = message[:500]
            background.finished_at = datetime.now(UTC)
            background.heartbeat_at = background.finished_at
        await session.commit()

    async def _fail(
        self,
        session: AsyncSession,
        publication: Publication,
        *,
        category: str,
        message: str,
        retryable: bool,
        manual_next_step: str | None,
        retry_after_seconds: int | None = None,
        uncertain: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        source = publication.state
        publication.state = PublishState.NEEDS_RECONCILIATION if uncertain else PublishState.FAILED
        publication.stage = "reconciliation_required" if uncertain else "failed"
        publication.failure_category = category[:100]
        publication.failure_message = message[:500]
        publication.retryable = retryable and not uncertain
        publication.uncertain_remote_result = uncertain
        publication.manual_next_step = manual_next_step
        publication.retry_after_seconds = retry_after_seconds
        publication.finished_at = datetime.now(UTC) if not uncertain else None
        await PublishingService.append_event(
            session,
            publication=publication,
            kind=(PublishEventKind.RECONCILIATION if uncertain else PublishEventKind.FAILED),
            from_state=source,
            message=message,
            event_data={"category": category, **(details or {})},
        )
        background = await session.get(BackgroundJob, publication.background_job_id)
        if background is not None:
            background.state = JobState.NEEDS_REVIEW if uncertain else JobState.FAILED
            background.failure_category = category[:100]
            background.failure_message = message[:500]
            background.finished_at = datetime.now(UTC)
            background.heartbeat_at = background.finished_at
        await session.commit()

    async def _set_state(
        self,
        session: AsyncSession,
        publication: Publication,
        *,
        state: PublishState,
        progress: int,
        stage: str,
        kind: PublishEventKind,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        source = publication.state
        publication.state = state
        publication.progress_percent = progress
        publication.stage = stage
        await PublishingService.append_event(
            session,
            publication=publication,
            kind=kind,
            from_state=source,
            message=message,
            event_data=details,
        )

    async def _cancel_before_remote(self, session: AsyncSession, publication: Publication) -> None:
        source = publication.state
        publication.state = PublishState.CANCELLED
        publication.stage = "cancelled"
        publication.finished_at = datetime.now(UTC)
        await PublishingService.append_event(
            session,
            publication=publication,
            kind=PublishEventKind.CANCELLED,
            from_state=source,
            message="Publication cancelled before a confirmed remote publication existed.",
        )
        background = await session.get(BackgroundJob, publication.background_job_id)
        if background is not None:
            background.state = JobState.CANCELLED
            background.finished_at = publication.finished_at
        await session.commit()

    @staticmethod
    async def _principal(session: AsyncSession, publication: Publication) -> Principal:
        row = (
            await session.execute(
                select(User, TenantMembership)
                .join(
                    TenantMembership,
                    TenantMembership.user_id == User.id,
                )
                .where(
                    User.id == publication.requested_by_user_id,
                    User.is_active.is_(True),
                    TenantMembership.tenant_id == publication.tenant_id,
                    TenantMembership.status == MembershipStatus.ACTIVE,
                )
            )
        ).one_or_none()
        if row is None:
            raise ClipOSError(
                "requester_authorization_revoked",
                "the requesting user or tenant membership is no longer active",
                status_code=409,
            )
        user, membership = row
        principal = Principal(
            user_id=user.id,
            tenant_id=publication.tenant_id,
            role=membership.role,
            email=user.email,
            is_platform_admin=user.is_platform_admin,
        )
        principal.require(Role.REVIEWER)
        return principal

    @staticmethod
    async def _fail_orphan_background(session: AsyncSession, message: QueueMessage) -> None:
        job = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == message.job_id,
                BackgroundJob.tenant_id == message.tenant_id,
                BackgroundJob.kind == PUBLISH_JOB_KIND,
            )
            .with_for_update()
        )
        if job is None:
            return
        job.state = JobState.FAILED
        job.failure_category = "publication_record_missing"
        job.failure_message = "publication background job has no domain record"
        job.finished_at = datetime.now(UTC)
        await session.commit()


class PublishWorker:
    def __init__(
        self,
        *,
        processor: PublishProcessor,
        session_factory: async_sessionmaker[AsyncSession],
        queue: QueueBroker,
        settings: Settings,
    ) -> None:
        self.processor = processor
        self.session_factory = session_factory
        self.queue = queue
        self.settings = settings

    async def recover_queued_job(self) -> QueueMessage | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(BackgroundJob, Publication)
                    .join(Publication, Publication.background_job_id == BackgroundJob.id)
                    .where(
                        BackgroundJob.kind == PUBLISH_JOB_KIND,
                        Publication.state == PublishState.QUEUED,
                        BackgroundJob.state == JobState.QUEUED,
                    )
                    .order_by(BackgroundJob.created_at)
                    .limit(1)
                )
            ).one_or_none()
            if row is not None:
                job, publication = row
                return QueueMessage(
                    job_id=job.id,
                    tenant_id=publication.tenant_id,
                    kind=PUBLISH_JOB_KIND,
                )
            stale_before = datetime.now(UTC) - timedelta(
                seconds=self.settings.worker_stale_job_seconds
            )
            stale = await session.scalar(
                select(Publication)
                .where(
                    Publication.state.in_(
                        {
                            PublishState.VALIDATING,
                            PublishState.UPLOADING,
                            PublishState.PROCESSING,
                        }
                    ),
                    Publication.updated_at < stale_before,
                )
                .order_by(Publication.updated_at)
                .limit(1)
                .with_for_update()
            )
            if stale is None:
                return None
            source = stale.state
            if stale.provider_publication_id or stale.upload_session_envelope:
                stale.state = PublishState.NEEDS_RECONCILIATION
                stale.stage = "worker_restart_reconciliation"
                stale.uncertain_remote_result = True
                stale.manual_next_step = "Worker restart detected; reconcile before retrying."
                background = await session.get(BackgroundJob, stale.background_job_id)
                if background is not None:
                    background.state = JobState.NEEDS_REVIEW
            else:
                stale.state = PublishState.QUEUED
                stale.stage = "worker_restart_retry_queued"
                background = await session.get(BackgroundJob, stale.background_job_id)
                if background is not None:
                    background.state = JobState.QUEUED
            await PublishingService.append_event(
                session,
                publication=stale,
                kind=PublishEventKind.RECONCILIATION,
                from_state=source,
                message="Stale worker state recovered after restart.",
                event_data={"remote_success_possible": bool(stale.upload_session_envelope)},
            )
            await session.commit()
            return QueueMessage(
                job_id=stale.background_job_id,
                tenant_id=stale.tenant_id,
                kind=PUBLISH_JOB_KIND,
            )
