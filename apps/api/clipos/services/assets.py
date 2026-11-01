from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.config import Settings
from clipos.core.enums import AssetSource, AssetState, JobState, Role
from clipos.core.errors import ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.media.errors import MediaPipelineError, UploadValidationError
from clipos.media.signatures import detect_media_signature, sanitize_display_filename
from clipos.models import Asset, BackgroundJob, Transcript
from clipos.queue import QueueBroker, QueueMessage
from clipos.services.audit import AuditService
from clipos.services.jobs import JobService
from clipos.storage.base import ObjectStorage

_ASSET_JOB_KIND = "asset.process"
logger = logging.getLogger(__name__)


class AsyncUpload(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StagedUpload:
    path: Path
    size: int
    checksum_sha256: str
    header: bytes


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    asset: Asset
    duplicate: bool


async def stage_upload(upload: AsyncUpload, settings: Settings) -> StagedUpload:
    await asyncio.to_thread(
        settings.upload_temp_root.mkdir, parents=True, exist_ok=True, mode=0o700
    )
    fd, temporary_name = tempfile.mkstemp(prefix="clipos-upload-", dir=settings.upload_temp_root)
    path = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    header = bytearray()
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            while True:
                try:
                    chunk = await upload.read(settings.upload_chunk_bytes)
                except Exception as exc:
                    raise UploadValidationError(
                        "upload_interrupted", "media upload was interrupted"
                    ) from exc
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise UploadValidationError(
                        "upload_too_large", "media file exceeds the configured upload limit"
                    )
                if len(header) < 512:
                    header.extend(chunk[: 512 - len(header)])
                digest.update(chunk)
                await asyncio.to_thread(output.write, chunk)
            await asyncio.to_thread(output.flush)
            await asyncio.to_thread(os.fsync, output.fileno())
        if size == 0:
            raise UploadValidationError("empty_upload", "media file is empty")
        return StagedUpload(path, size, digest.hexdigest(), bytes(header))
    except UploadValidationError:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise
    except OSError as exc:
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise MediaPipelineError(
            "temporary_storage_failure",
            "temporary upload storage failed",
            retryable=True,
        ) from exc
    finally:
        await upload.close()


class AssetService:
    @staticmethod
    async def ingest_local_upload(
        session: AsyncSession,
        *,
        storage: ObjectStorage,
        principal: Principal,
        upload: AsyncUpload,
        permission_confirmed: bool,
        rights_note: str,
        settings: Settings,
    ) -> IngestionOutcome:
        principal.require(Role.EDITOR)
        if not permission_confirmed:
            raise UploadValidationError(
                "permission_not_confirmed",
                "upload requires confirmation that the source is authorized",
            )
        if len(rights_note.strip()) < 10:
            raise UploadValidationError(
                "rights_note_required", "provide a meaningful source permission note"
            )

        staged = await stage_upload(upload, settings)
        try:
            detected = detect_media_signature(
                staged.header,
                filename=upload.filename,
                declared_content_type=upload.content_type,
            )
            existing = await session.scalar(
                select(Asset).where(
                    Asset.tenant_id == principal.tenant_id,
                    Asset.checksum_sha256 == staged.checksum_sha256,
                )
            )
            if existing:
                AuditService.record(
                    session,
                    tenant_id=principal.tenant_id,
                    actor_user_id=principal.user_id,
                    action="asset.duplicate_detected",
                    resource_type="asset",
                    resource_id=str(existing.id),
                    event_data={"checksum_sha256": staged.checksum_sha256},
                )
                await session.commit()
                return IngestionOutcome(existing, True)

            asset_id = uuid4()
            storage_key = f"assets/{asset_id}/original{detected.safe_extension}"
            asset = Asset(
                id=asset_id,
                tenant_id=principal.tenant_id,
                submitted_by_user_id=principal.user_id,
                source=AssetSource.LOCAL_UPLOAD,
                state=AssetState.VALIDATING,
                media_kind=detected.kind_hint,
                original_filename=sanitize_display_filename(upload.filename),
                safe_extension=detected.safe_extension,
                declared_content_type=(upload.content_type or "")[:160] or None,
                detected_content_type=detected.content_type,
                size_bytes=staged.size,
                checksum_sha256=staged.checksum_sha256,
                storage_key=storage_key,
                permission_confirmed=True,
                rights_note=rights_note.strip(),
                progress_percent=5,
                processing_stage="validating",
            )
            session.add(asset)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                raced = await session.scalar(
                    select(Asset).where(
                        Asset.tenant_id == principal.tenant_id,
                        Asset.checksum_sha256 == staged.checksum_sha256,
                    )
                )
                if raced:
                    return IngestionOutcome(raced, True)
                raise

            try:
                stored = await storage.put_file(
                    principal.tenant_id,
                    storage_key,
                    staged.path,
                    content_type=detected.content_type,
                    expected_checksum=staged.checksum_sha256,
                )
            except Exception as exc:
                await session.rollback()
                raise MediaPipelineError(
                    "object_storage_failure",
                    "private object storage failed while saving the upload",
                    retryable=True,
                ) from exc
            if stored.size != staged.size:
                await session.rollback()
                try:
                    await storage.delete(principal.tenant_id, storage_key)
                except Exception:
                    logger.warning("failed to clean up an uncommitted storage object")
                raise MediaPipelineError(
                    "storage_size_mismatch", "stored object size does not match upload"
                )
            asset.state = AssetState.QUEUED
            asset.progress_percent = 10
            asset.processing_stage = "queued"
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="asset.uploaded",
                resource_type="asset",
                resource_id=str(asset.id),
                event_data={
                    "source": AssetSource.LOCAL_UPLOAD.value,
                    "size_bytes": asset.size_bytes,
                    "checksum_sha256": asset.checksum_sha256,
                    "permission_confirmed": True,
                },
            )
            try:
                await session.commit()
            except Exception as exc:
                await session.rollback()
                try:
                    await storage.delete(principal.tenant_id, storage_key)
                except Exception:
                    logger.warning("failed to clean up an uncommitted storage object")
                raise MediaPipelineError(
                    "asset_persistence_failure",
                    "the upload could not be recorded",
                    retryable=True,
                ) from exc
            await session.refresh(asset)
            return IngestionOutcome(asset, False)
        finally:
            await asyncio.to_thread(staged.path.unlink, missing_ok=True)

    @staticmethod
    async def create_processing_job(
        session: AsyncSession,
        *,
        asset: Asset,
        actor_user_id: UUID,
        queue: QueueBroker,
    ) -> BackgroundJob:
        job, _ = await JobService.create(
            session,
            tenant_id=asset.tenant_id,
            actor_user_id=actor_user_id,
            kind=_ASSET_JOB_KIND,
            idempotency_key=f"asset:{asset.id}:process:v1",
            payload={"asset_id": str(asset.id)},
            max_attempts=3,
        )
        current = await AssetService.get(session, asset.tenant_id, asset.id)
        if current.processing_job_id != job.id:
            current.processing_job_id = job.id
            await session.commit()
            await session.refresh(current)
        if job.state == JobState.QUEUED:
            try:
                await queue.enqueue(
                    QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)
                )
            except Exception as exc:
                raise MediaPipelineError(
                    "queue_unavailable",
                    "the asset is durably queued but the queue broker is unavailable",
                    retryable=True,
                ) from exc
        return job

    @staticmethod
    async def get(session: AsyncSession, tenant_id: UUID, asset_id: UUID) -> Asset:
        asset = await session.scalar(
            select(Asset).where(Asset.id == asset_id, Asset.tenant_id == tenant_id)
        )
        if asset is None:
            raise NotFoundError("asset")
        return asset

    @staticmethod
    async def list(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        state: AssetState | None = None,
        query_text: str | None = None,
        limit: int = 100,
    ) -> list[Asset]:
        query = select(Asset).where(Asset.tenant_id == tenant_id)
        if state:
            query = query.where(Asset.state == state)
        if query_text:
            pattern = f"%{query_text.strip()[:200]}%"
            transcript_match = select(Transcript.asset_id).where(
                Transcript.tenant_id == tenant_id,
                Transcript.normalized_text.ilike(pattern),
            )
            query = query.where(
                or_(Asset.original_filename.ilike(pattern), Asset.id.in_(transcript_match))
            )
        query = query.order_by(Asset.created_at.desc()).limit(min(limit, 200))
        return list((await session.scalars(query)).all())

    @staticmethod
    async def cancel(session: AsyncSession, *, principal: Principal, asset_id: UUID) -> Asset:
        principal.require(Role.EDITOR)
        asset = await session.scalar(
            select(Asset)
            .where(Asset.id == asset_id, Asset.tenant_id == principal.tenant_id)
            .with_for_update()
        )
        if asset is None:
            raise NotFoundError("asset")
        if asset.state in {AssetState.READY, AssetState.CANCELLED}:
            raise ConflictError(f"asset in {asset.state.value} state cannot be cancelled")
        asset.cancellation_requested = True
        if asset.state in {AssetState.RECEIVED, AssetState.VALIDATING, AssetState.QUEUED}:
            asset.state = AssetState.CANCELLED
            asset.processing_stage = "cancelled"
            if asset.processing_job_id:
                job = await session.scalar(
                    select(BackgroundJob)
                    .where(
                        BackgroundJob.id == asset.processing_job_id,
                        BackgroundJob.tenant_id == principal.tenant_id,
                    )
                    .with_for_update()
                )
                if job and job.state in {JobState.QUEUED, JobState.NEEDS_REVIEW}:
                    job.state = JobState.CANCELLED
                    job.finished_at = datetime.now(UTC)
                    job.heartbeat_at = job.finished_at
        else:
            asset.processing_stage = "cancellation_requested"
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="asset.cancellation_requested",
            resource_type="asset",
            resource_id=str(asset.id),
            event_data={"state": asset.state.value},
        )
        await session.commit()
        await session.refresh(asset)
        return asset

    @staticmethod
    async def retry(
        session: AsyncSession,
        *,
        principal: Principal,
        asset_id: UUID,
        queue: QueueBroker,
    ) -> Asset:
        principal.require(Role.EDITOR)
        asset = await session.scalar(
            select(Asset)
            .where(Asset.id == asset_id, Asset.tenant_id == principal.tenant_id)
            .with_for_update()
        )
        if asset is None:
            raise NotFoundError("asset")
        if asset.state not in {AssetState.FAILED, AssetState.NEEDS_REVIEW}:
            raise ConflictError("only failed or needs-review assets can be retried")
        if not asset.processing_job_id:
            raise ConflictError("asset has no processing job")
        job = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == asset.processing_job_id,
                BackgroundJob.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if job is None or job.state not in {JobState.FAILED, JobState.NEEDS_REVIEW}:
            raise ConflictError("asset processing job is not retryable")
        if job.attempts >= job.max_attempts:
            raise ConflictError("asset processing retry budget exhausted")
        job.state = JobState.QUEUED
        job.failure_category = None
        job.failure_message = None
        job.finished_at = None
        job.heartbeat_at = None
        asset.state = AssetState.QUEUED
        asset.processing_stage = "queued"
        asset.progress_percent = max(10, min(asset.progress_percent, 90))
        asset.error_category = None
        asset.error_message = None
        asset.cancellation_requested = False
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="asset.retry_queued",
            resource_type="asset",
            resource_id=str(asset.id),
            event_data={"attempts": job.attempts, "max_attempts": job.max_attempts},
        )
        await session.commit()
        try:
            await queue.enqueue(QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind))
        except Exception as exc:
            raise MediaPipelineError(
                "queue_unavailable",
                "the asset is durably queued but the queue broker is unavailable",
                retryable=True,
            ) from exc
        await session.refresh(asset)
        return asset
