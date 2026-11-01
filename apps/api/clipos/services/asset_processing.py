from __future__ import annotations

import asyncio
import logging
import time
from contextvars import Token
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import AssetState, AuditOutcome, JobState, ProviderMode, TranscriptState
from clipos.core.logging import correlation_id_var
from clipos.gateways.transcription import TranscriptionGateway, TranscriptionRequest
from clipos.gateways.transcription.errors import (
    TranscriptionDisabledError,
    TranscriptionError,
)
from clipos.media.errors import MediaPipelineError
from clipos.media.tooling import MediaTooling, ProbeResult
from clipos.models import (
    Asset,
    BackgroundJob,
    MediaMetadata,
    SystemControl,
    UsageCostEvent,
)
from clipos.queue import QueueBroker, QueueMessage
from clipos.services.audit import AuditService
from clipos.services.providers import ProviderStatusService
from clipos.services.transcripts import TranscriptService
from clipos.storage.base import ObjectStorage

_ASSET_JOB_KIND = "asset.process"
logger = logging.getLogger(__name__)


class _ProcessingCancelled(RuntimeError):
    pass


class AssetProcessingService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        media_tools: MediaTooling,
        transcription: TranscriptionGateway,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.media_tools = media_tools
        self.transcription = transcription
        self.settings = settings

    async def process_job(self, message: QueueMessage) -> None:
        started = time.monotonic()
        asset_id: UUID | None = None
        correlation_token: Token[str] | None = None
        try:
            async with self.session_factory() as session:
                job = await session.scalar(
                    select(BackgroundJob)
                    .where(
                        BackgroundJob.id == message.job_id,
                        BackgroundJob.tenant_id == message.tenant_id,
                    )
                    .with_for_update()
                )
                if job is None or job.kind != _ASSET_JOB_KIND or job.state != JobState.QUEUED:
                    return
                correlation_token = correlation_id_var.set(job.correlation_id)
                try:
                    asset_id = UUID(str(job.payload["asset_id"]))
                except (KeyError, TypeError, ValueError):
                    await self._finish(
                        session,
                        job=job,
                        asset=None,
                        job_state=JobState.FAILED,
                        asset_state=None,
                        category="invalid_job_payload",
                        message="asset processing job payload is invalid",
                    )
                    return
                asset = await session.scalar(
                    select(Asset)
                    .where(Asset.id == asset_id, Asset.tenant_id == message.tenant_id)
                    .with_for_update()
                )
                if asset is None or asset.processing_job_id != job.id:
                    await self._finish(
                        session,
                        job=job,
                        asset=asset,
                        job_state=JobState.FAILED,
                        asset_state=AssetState.FAILED if asset else None,
                        category="asset_job_mismatch",
                        message="asset processing job does not match its asset",
                    )
                    return
                if job.attempts >= job.max_attempts:
                    await self._finish(
                        session,
                        job=job,
                        asset=asset,
                        job_state=JobState.NEEDS_REVIEW,
                        asset_state=AssetState.NEEDS_REVIEW,
                        category="retry_budget_exhausted",
                        message="asset processing retry budget is exhausted",
                    )
                    return
                if asset.cancellation_requested or asset.state == AssetState.CANCELLED:
                    await self._finish_cancelled(session, job, asset)
                    return
                job.state = JobState.RUNNING
                job.attempts += 1
                job.started_at = datetime.now(UTC)
                job.heartbeat_at = job.started_at
                job.finished_at = None
                asset.state = AssetState.EXTRACTING_METADATA
                asset.processing_stage = "extracting_metadata"
                asset.progress_percent = 20
                asset.error_category = None
                asset.error_message = None
                AuditService.record(
                    session,
                    tenant_id=asset.tenant_id,
                    actor_user_id=None,
                    action="asset.processing_started",
                    resource_type="asset",
                    resource_id=str(asset.id),
                    event_data={"attempt": job.attempts},
                )
                await session.commit()

            await asyncio.to_thread(
                self.settings.upload_temp_root.mkdir,
                parents=True,
                exist_ok=True,
                mode=0o700,
            )
            with TemporaryDirectory(
                prefix="clipos-process-", dir=self.settings.upload_temp_root
            ) as directory:
                workdir = Path(directory)
                source_path = workdir / f"source{asset.safe_extension}"
                await self.storage.copy_to(asset.tenant_id, asset.storage_key, source_path)
                await self._heartbeat(message.job_id, asset.tenant_id)
                probe = await self.media_tools.probe(source_path)
                await self._persist_probe(asset.id, asset.tenant_id, probe)
                await self._heartbeat(message.job_id, asset.tenant_id)
                await self._check_cancelled(asset.id, asset.tenant_id)
                if not probe.has_audio:
                    async with self.session_factory() as session:
                        job, current = await self._load_for_finish(
                            session, message.job_id, asset.id, asset.tenant_id
                        )
                        await self._finish(
                            session,
                            job=job,
                            asset=current,
                            job_state=JobState.NEEDS_REVIEW,
                            asset_state=AssetState.NEEDS_REVIEW,
                            category="no_audio_stream",
                            message="media has no audio stream to transcribe",
                        )
                    return

                audio_path = workdir / "audio-16k.wav"
                await self.media_tools.extract_audio(
                    source_path, audio_path, probe.duration_seconds
                )
                await self._heartbeat(message.job_id, asset.tenant_id)
                audio_key = f"assets/{asset.id}/derived/audio-16k.wav"
                audio_info = await self.storage.put_file(
                    asset.tenant_id,
                    audio_key,
                    audio_path,
                    content_type="audio/wav",
                )
                async with self.session_factory() as session:
                    current = await self._load_asset(session, asset.id, asset.tenant_id)
                    current.derived_audio_storage_key = audio_key
                    current.derived_audio_checksum_sha256 = audio_info.checksum_sha256
                    current.state = AssetState.TRANSCRIBING
                    current.processing_stage = "transcribing"
                    current.progress_percent = 65
                    current_job = await session.scalar(
                        select(BackgroundJob).where(
                            BackgroundJob.id == message.job_id,
                            BackgroundJob.tenant_id == asset.tenant_id,
                        )
                    )
                    if current_job:
                        current_job.heartbeat_at = datetime.now(UTC)
                    await session.commit()
                await self._check_cancelled(asset.id, asset.tenant_id)

                transcription_started = time.monotonic()
                result = await self.transcription.transcribe(
                    TranscriptionRequest(
                        audio_path=audio_path,
                        duration_seconds=probe.duration_seconds,
                    )
                )
                latency_ms = round((time.monotonic() - transcription_started) * 1000)
                async with self.session_factory() as session:
                    current = await self._load_asset(session, asset.id, asset.tenant_id)
                    if current.cancellation_requested:
                        raise _ProcessingCancelled
                    transcript = await TranscriptService.persist(
                        session,
                        tenant_id=asset.tenant_id,
                        asset_id=asset.id,
                        result=result,
                        provider=self.transcription.adapter.provider_name,
                        model=self.transcription.adapter.model_name,
                        settings=self.settings,
                    )
                    session.add(
                        UsageCostEvent(
                            tenant_id=asset.tenant_id,
                            provider=self.transcription.adapter.provider_name,
                            model=self.transcription.adapter.model_name,
                            operation="transcription",
                            input_units=round(probe.duration_seconds),
                            output_units=len(result.text),
                            estimated_cost=Decimal("0"),
                            latency_ms=latency_ms,
                            success=True,
                            correlation_id=job.correlation_id,
                        )
                    )
                    current.progress_percent = 100
                    current.processing_stage = (
                        "ready" if transcript.state == TranscriptState.READY else "needs_review"
                    )
                    current.state = (
                        AssetState.READY
                        if transcript.state == TranscriptState.READY
                        else AssetState.NEEDS_REVIEW
                    )
                    current.error_category = (
                        None if transcript.state == TranscriptState.READY else "low_confidence"
                    )
                    current.error_message = (
                        None
                        if transcript.state == TranscriptState.READY
                        else "transcript confidence is missing or below the review threshold"
                    )
                    current_job = await session.scalar(
                        select(BackgroundJob)
                        .where(
                            BackgroundJob.id == message.job_id,
                            BackgroundJob.tenant_id == asset.tenant_id,
                        )
                        .with_for_update()
                    )
                    assert current_job is not None
                    current_job.state = (
                        JobState.SUCCEEDED
                        if transcript.state == TranscriptState.READY
                        else JobState.NEEDS_REVIEW
                    )
                    current_job.result = {
                        "asset_id": str(asset.id),
                        "transcript_id": str(transcript.id),
                        "transcript_state": transcript.state.value,
                    }
                    current_job.finished_at = datetime.now(UTC)
                    current_job.heartbeat_at = current_job.finished_at
                    AuditService.record(
                        session,
                        tenant_id=asset.tenant_id,
                        actor_user_id=None,
                        action="asset.processing_completed",
                        resource_type="asset",
                        resource_id=str(asset.id),
                        event_data={
                            "state": current.state.value,
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                            "transcript_id": str(transcript.id),
                        },
                    )
                    await session.commit()
                await self._record_transcription_status(ProviderMode.REAL_TESTED)
        except _ProcessingCancelled:
            if asset_id:
                async with self.session_factory() as session:
                    job, asset = await self._load_for_finish(
                        session, message.job_id, asset_id, message.tenant_id
                    )
                    await self._finish_cancelled(session, job, asset)
        except TranscriptionDisabledError:
            if asset_id:
                await self._fail_by_ids(
                    message,
                    asset_id,
                    JobState.NEEDS_REVIEW,
                    AssetState.NEEDS_REVIEW,
                    "transcription_disabled",
                    "transcription provider is disabled",
                )
        except TranscriptionError as exc:
            await self._record_transcription_status(ProviderMode.BLOCKED, exc.category)
            if asset_id:
                await self._fail_by_ids(
                    message,
                    asset_id,
                    JobState.FAILED,
                    AssetState.FAILED,
                    exc.category,
                    "transcription provider failed",
                )
        except MediaPipelineError as exc:
            if asset_id:
                await self._fail_by_ids(
                    message,
                    asset_id,
                    JobState.NEEDS_REVIEW if exc.needs_review else JobState.FAILED,
                    AssetState.NEEDS_REVIEW if exc.needs_review else AssetState.FAILED,
                    exc.category,
                    exc.safe_message,
                )
        except OSError:
            if asset_id:
                await self._fail_by_ids(
                    message,
                    asset_id,
                    JobState.FAILED,
                    AssetState.FAILED,
                    "storage_processing_failure",
                    "private media storage failed during processing",
                )
        except Exception:
            if asset_id:
                await self._fail_by_ids(
                    message,
                    asset_id,
                    JobState.FAILED,
                    AssetState.FAILED,
                    "unexpected_processing_error",
                    "asset processing failed unexpectedly",
                )
            raise
        finally:
            if correlation_token is not None:
                correlation_id_var.reset(correlation_token)

    async def _heartbeat(self, job_id: UUID, tenant_id: UUID) -> None:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.tenant_id == tenant_id,
                    BackgroundJob.state == JobState.RUNNING,
                )
            )
            if job:
                job.heartbeat_at = datetime.now(UTC)
                await session.commit()

    async def _record_transcription_status(
        self,
        mode: ProviderMode,
        error_category: str | None = None,
    ) -> None:
        provider = self.transcription.adapter.provider_name
        if provider == "disabled" or provider != self.settings.transcription_provider:
            return
        try:
            async with self.session_factory() as session:
                await ProviderStatusService.update_runtime_status(
                    session,
                    provider=provider,
                    capability="audio_transcription",
                    mode=mode,
                    error_category=error_category,
                )
        except Exception:
            logger.warning("could not persist transcription provider runtime status")

    async def _persist_probe(self, asset_id: UUID, tenant_id: UUID, probe: ProbeResult) -> None:
        async with self.session_factory() as session:
            asset = await self._load_asset(session, asset_id, tenant_id)
            metadata = await session.scalar(
                select(MediaMetadata).where(
                    MediaMetadata.asset_id == asset_id,
                    MediaMetadata.tenant_id == tenant_id,
                )
            )
            values = {
                "format_name": probe.format_name,
                "duration_seconds": probe.duration_seconds,
                "bit_rate": probe.bit_rate,
                "has_video": probe.has_video,
                "has_audio": probe.has_audio,
                "video_codec": probe.video_codec,
                "audio_codec": probe.audio_codec,
                "width": probe.width,
                "height": probe.height,
                "frame_rate": probe.frame_rate,
                "audio_channels": probe.audio_channels,
                "audio_sample_rate": probe.audio_sample_rate,
                "probe_version": probe.probe_version,
                "probe_payload": probe.raw,
                "verified_at": datetime.now(UTC),
            }
            if metadata is None:
                metadata = MediaMetadata(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    **values,
                )
                session.add(metadata)
            else:
                for key, value in values.items():
                    setattr(metadata, key, value)
            asset.media_kind = probe.media_kind
            asset.metadata_verified = True
            asset.progress_percent = 40
            asset.processing_stage = "metadata_verified"
            await session.commit()

    async def _check_cancelled(self, asset_id: UUID, tenant_id: UUID) -> None:
        async with self.session_factory() as session:
            asset = await self._load_asset(session, asset_id, tenant_id)
            if asset.cancellation_requested:
                raise _ProcessingCancelled

    @staticmethod
    async def _load_asset(session: AsyncSession, asset_id: UUID, tenant_id: UUID) -> Asset:
        asset = await session.scalar(
            select(Asset)
            .where(Asset.id == asset_id, Asset.tenant_id == tenant_id)
            .with_for_update()
        )
        if asset is None:
            raise RuntimeError("asset disappeared during processing")
        return asset

    @staticmethod
    async def _load_for_finish(
        session: AsyncSession, job_id: UUID, asset_id: UUID, tenant_id: UUID
    ) -> tuple[BackgroundJob, Asset]:
        job = await session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == job_id, BackgroundJob.tenant_id == tenant_id)
            .with_for_update()
        )
        asset = await AssetProcessingService._load_asset(session, asset_id, tenant_id)
        if job is None:
            raise RuntimeError("processing job disappeared")
        return job, asset

    async def _fail_by_ids(
        self,
        message: QueueMessage,
        asset_id: UUID,
        job_state: JobState,
        asset_state: AssetState,
        category: str,
        safe_message: str,
    ) -> None:
        async with self.session_factory() as session:
            job, asset = await self._load_for_finish(
                session, message.job_id, asset_id, message.tenant_id
            )
            await self._finish(
                session,
                job=job,
                asset=asset,
                job_state=job_state,
                asset_state=asset_state,
                category=category,
                message=safe_message,
            )

    @staticmethod
    async def _finish(
        session: AsyncSession,
        *,
        job: BackgroundJob,
        asset: Asset | None,
        job_state: JobState,
        asset_state: AssetState | None,
        category: str,
        message: str,
    ) -> None:
        now = datetime.now(UTC)
        job.state = job_state
        job.failure_category = category[:100]
        job.failure_message = message[:500]
        job.finished_at = now
        job.heartbeat_at = now
        if asset and asset_state:
            asset.state = asset_state
            asset.processing_stage = asset_state.value
            asset.error_category = category[:100]
            asset.error_message = message[:500]
            if asset_state in {AssetState.NEEDS_REVIEW, AssetState.FAILED}:
                asset.progress_percent = min(asset.progress_percent, 95)
            AuditService.record(
                session,
                tenant_id=asset.tenant_id,
                actor_user_id=None,
                action="asset.processing_stopped",
                resource_type="asset",
                resource_id=str(asset.id),
                outcome=(
                    AuditOutcome.FAILURE
                    if asset_state == AssetState.FAILED
                    else AuditOutcome.SUCCESS
                ),
                event_data={"state": asset_state.value, "category": category},
            )
        await session.commit()

    @staticmethod
    async def _finish_cancelled(session: AsyncSession, job: BackgroundJob, asset: Asset) -> None:
        job.state = JobState.CANCELLED
        job.finished_at = datetime.now(UTC)
        job.heartbeat_at = job.finished_at
        asset.state = AssetState.CANCELLED
        asset.processing_stage = "cancelled"
        asset.error_category = None
        asset.error_message = None
        AuditService.record(
            session,
            tenant_id=asset.tenant_id,
            actor_user_id=None,
            action="asset.processing_cancelled",
            resource_type="asset",
            resource_id=str(asset.id),
        )
        await session.commit()


class AssetWorker:
    def __init__(
        self,
        *,
        processor: AssetProcessingService,
        session_factory: async_sessionmaker[AsyncSession],
        queue: QueueBroker,
        settings: Settings,
    ) -> None:
        self.processor = processor
        self.session_factory = session_factory
        self.queue = queue
        self.settings = settings
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            message = await self.queue.dequeue(timeout_seconds=1)
            if message is None:
                message = await self._recover_queued_job()
            if message is None:
                await asyncio.sleep(self.settings.worker_poll_seconds)
                continue
            if await self._is_paused():
                await asyncio.sleep(self.settings.worker_poll_seconds)
                continue
            try:
                await self.processor.process_job(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("asset worker recovered after an unexpected processing failure")

    def stop(self) -> None:
        self._stop.set()

    async def _is_paused(self) -> bool:
        async with self.session_factory() as session:
            paused = await session.scalar(
                select(SystemControl.paused).where(SystemControl.key == "global")
            )
            return bool(paused)

    async def recover_queued_job(self) -> QueueMessage | None:
        return await self._recover_queued_job()

    async def _recover_queued_job(self) -> QueueMessage | None:
        async with self.session_factory() as session:
            recovered = await self._recover_stale_job(session)
            if recovered:
                return recovered
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == _ASSET_JOB_KIND,
                    BackgroundJob.state == JobState.QUEUED,
                )
                .order_by(BackgroundJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            return QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)

    async def _recover_stale_job(self, session: AsyncSession) -> QueueMessage | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.worker_stale_job_seconds)
        job = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.kind == _ASSET_JOB_KIND,
                BackgroundJob.state == JobState.RUNNING,
                or_(
                    BackgroundJob.heartbeat_at < cutoff,
                    and_(
                        BackgroundJob.heartbeat_at.is_(None),
                        BackgroundJob.started_at < cutoff,
                    ),
                ),
            )
            .order_by(BackgroundJob.heartbeat_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        token = correlation_id_var.set(job.correlation_id)
        try:
            return await self._resolve_stale_job(session, job, cutoff)
        finally:
            correlation_id_var.reset(token)

    async def _resolve_stale_job(
        self,
        session: AsyncSession,
        job: BackgroundJob,
        cutoff: datetime,
    ) -> QueueMessage | None:
        try:
            asset_id = UUID(str(job.payload["asset_id"]))
        except (KeyError, TypeError, ValueError):
            job.state = JobState.FAILED
            job.failure_category = "invalid_job_payload"
            job.failure_message = "stale asset job payload is invalid"
            job.finished_at = datetime.now(UTC)
            job.heartbeat_at = job.finished_at
            await session.commit()
            return None
        asset = await session.scalar(
            select(Asset)
            .where(Asset.id == asset_id, Asset.tenant_id == job.tenant_id)
            .with_for_update(skip_locked=True)
        )
        if asset is None or asset.processing_job_id != job.id:
            job.state = JobState.FAILED
            job.failure_category = "asset_job_mismatch"
            job.failure_message = "stale processing job does not match an asset"
            job.finished_at = datetime.now(UTC)
            job.heartbeat_at = job.finished_at
            await session.commit()
            return None
        if asset.cancellation_requested:
            await self.processor._finish_cancelled(session, job, asset)
            return None
        if job.attempts >= job.max_attempts:
            await self.processor._finish(
                session,
                job=job,
                asset=asset,
                job_state=JobState.NEEDS_REVIEW,
                asset_state=AssetState.NEEDS_REVIEW,
                category="retry_budget_exhausted",
                message="stale processing job exhausted its retry budget",
            )
            return None
        job.state = JobState.QUEUED
        job.started_at = None
        job.heartbeat_at = None
        job.failure_category = None
        job.failure_message = None
        asset.state = AssetState.QUEUED
        asset.processing_stage = "recovered_after_worker_loss"
        asset.progress_percent = min(asset.progress_percent, 90)
        asset.error_category = None
        asset.error_message = None
        AuditService.record(
            session,
            tenant_id=asset.tenant_id,
            actor_user_id=None,
            action="asset.processing_recovered",
            resource_type="asset",
            resource_id=str(asset.id),
            event_data={"attempts": job.attempts, "stale_before": cutoff.isoformat()},
        )
        await session.commit()
        return QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)
