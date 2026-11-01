from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import shutil
import time
from contextvars import Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import (
    AssetState,
    AuditOutcome,
    CandidateState,
    CaptionMode,
    JobState,
    RenderCropStrategy,
    RenderState,
)
from clipos.core.errors import ClipOSError, ConflictError, NotFoundError
from clipos.core.logging import correlation_id_var
from clipos.core.security import Principal
from clipos.media.errors import MediaPipelineError, MediaProcessCancelledError
from clipos.media.tooling import MediaTooling, ProbeResult, RenderSpec
from clipos.models import (
    Asset,
    BackgroundJob,
    ClipCandidate,
    MediaMetadata,
    RenderCaptionSegment,
    RenderDefect,
    RenderJob,
    RenderOutput,
    RenderPlan,
    SystemControl,
    Tenant,
    TenantRenderProfile,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from clipos.queue import QueueBroker, QueueMessage
from clipos.render_schemas import (
    RenderDefectCreate,
    RenderPlanCreateRequest,
    TenantRenderProfileUpdate,
)
from clipos.rendering import CaptionCue, CaptionLayoutError, build_caption_cues, cues_to_srt
from clipos.services.audit import AuditService
from clipos.storage.base import ObjectStorage

RENDER_JOB_KIND = "render.execute"
PLAN_VERSION = "render-plan.v1"
RENDERER_VERSION = "ffmpeg-renderer.v1"
logger = logging.getLogger(__name__)

_TERMINAL_RENDER_STATES = {
    RenderState.SUCCEEDED,
    RenderState.FAILED,
    RenderState.CANCELLED,
    RenderState.NEEDS_REVIEW,
}
_ACTIVE_RENDER_STATES = {
    RenderState.VALIDATING,
    RenderState.RENDERING,
    RenderState.VALIDATING_OUTPUT,
}

_PRESETS: dict[str, dict[str, Any]] = {
    "tiktok_vertical": {
        "width": 1080,
        "height": 1920,
        "frame_rate": 30.0,
        "profile": "TikTok-compatible vertical export; destination acceptance is not guaranteed",
    },
    "instagram_reels": {
        "width": 1080,
        "height": 1920,
        "frame_rate": 30.0,
        "profile": (
            "Instagram Reels-compatible vertical export; destination acceptance is not guaranteed"
        ),
    },
    "youtube_shorts": {
        "width": 1080,
        "height": 1920,
        "frame_rate": 30.0,
        "profile": (
            "YouTube Shorts-compatible vertical export; destination acceptance is not guaranteed"
        ),
    },
    "vertical_1080p": {
        "width": 1080,
        "height": 1920,
        "frame_rate": 30.0,
        "profile": "general 9:16 vertical short-form",
    },
    "vertical_720p": {
        "width": 720,
        "height": 1280,
        "frame_rate": 30.0,
        "profile": "reduced-resource 9:16 vertical short-form",
    },
    "square_1080p": {
        "width": 1080,
        "height": 1080,
        "frame_rate": 30.0,
        "profile": "general 1:1 square export",
    },
    "landscape_1080p": {
        "width": 1920,
        "height": 1080,
        "frame_rate": 30.0,
        "profile": "general 16:9 landscape export",
    },
}


@dataclass(frozen=True, slots=True)
class PlanCreationOutcome:
    plan: RenderPlan
    captions: list[RenderCaptionSegment]
    render_job: RenderJob
    idempotent: bool


@dataclass(frozen=True, slots=True)
class RenderDetail:
    plan: RenderPlan
    captions: list[RenderCaptionSegment]
    render_job: RenderJob
    outputs: list[RenderOutput]
    defects: list[RenderDefect]


@dataclass(frozen=True, slots=True)
class RenderListRow:
    render_job: RenderJob
    plan: RenderPlan
    current_output: RenderOutput | None


@dataclass(frozen=True, slots=True)
class ResolvedPreset:
    width: int
    height: int
    frame_rate: float
    aspect_ratio: str
    safe_area: dict[str, float]


class RenderingService:
    @staticmethod
    def capabilities(settings: Settings, media_tools: MediaTooling) -> dict[str, Any]:
        status = media_tools.status
        capabilities = set(status.capabilities)
        presets = [
            {
                "name": name,
                "width": values["width"],
                "height": values["height"],
                "frame_rate": values["frame_rate"],
                "profile": values["profile"],
            }
            for name, values in _PRESETS.items()
        ]
        presets.append(
            {
                "name": "custom",
                "width": None,
                "height": None,
                "frame_rate": None,
                "profile": "bounded custom landscape, square, or portrait export",
            }
        )
        return {
            "enabled": settings.rendering_enabled,
            "ffmpeg_available": status.available,
            "render_ready": bool(settings.rendering_enabled and status.render_ready),
            "ffmpeg_version": status.ffmpeg_version,
            "ffprobe_version": status.ffprobe_version,
            "capabilities": sorted(capabilities),
            "presets": presets,
            "real_features": [
                "H.264/AAC MP4 export",
                "center crop and fit-with-padding reframing",
                "server-generated SRT sidecars",
                "post-render FFprobe validation and SHA-256 provenance",
                "bounded FFmpeg execution with timeout and cancellation",
                "speech-oriented loudness/true-peak handling",
            ],
            "conditional_features": [
                "subtitle burn-in when the FFmpeg subtitles/libass filter is present",
                "tenant-configured text branding when the FFmpeg drawtext filter is present",
            ],
            "unavailable_features": [
                "face detection or face tracking",
                "logo/image uploads and third-party music",
                "AI generative edits",
                "publishing or platform delivery",
            ],
            "limitations": list(status.render_limitations),
        }

    @staticmethod
    async def get_profile(session: AsyncSession, tenant_id: UUID) -> TenantRenderProfile | None:
        return await session.scalar(
            select(TenantRenderProfile).where(TenantRenderProfile.tenant_id == tenant_id)
        )

    @staticmethod
    async def update_profile(
        session: AsyncSession,
        *,
        principal: Principal,
        request: TenantRenderProfileUpdate,
    ) -> TenantRenderProfile:
        profile = await session.scalar(
            select(TenantRenderProfile)
            .where(TenantRenderProfile.tenant_id == principal.tenant_id)
            .with_for_update()
        )
        values = request.model_dump()
        if profile is None:
            profile = TenantRenderProfile(
                tenant_id=principal.tenant_id,
                updated_by_user_id=principal.user_id,
                **values,
            )
            session.add(profile)
        else:
            profile.updated_by_user_id = principal.user_id
            for key, value in values.items():
                setattr(profile, key, value)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="render_profile.updated",
            resource_type="tenant_render_profile",
            resource_id=str(profile.id),
            event_data={
                "branding_enabled": request.branding_enabled,
                "branding_position": request.branding_position,
            },
        )
        await session.commit()
        await session.refresh(profile)
        return profile

    @staticmethod
    async def create_plan(
        session: AsyncSession,
        *,
        principal: Principal,
        candidate_id: UUID,
        request: RenderPlanCreateRequest,
        settings: Settings,
        media_tools: MediaTooling,
        queue: QueueBroker,
    ) -> PlanCreationOutcome:
        request_hash = RenderingService._request_hash(candidate_id, request)
        existing = await session.scalar(
            select(RenderPlan).where(
                RenderPlan.tenant_id == principal.tenant_id,
                RenderPlan.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash or existing.candidate_id != candidate_id:
                raise ConflictError("idempotency key was already used for a different render plan")
            return await RenderingService._creation_outcome(session, existing, idempotent=True)

        await RenderingService._require_running_control(session)
        if not settings.rendering_enabled:
            raise ClipOSError(
                "rendering_disabled",
                "rendering is disabled by runtime configuration",
                status_code=503,
            )
        if not media_tools.status.available:
            await media_tools.check()
        if not media_tools.status.render_ready:
            raise ClipOSError(
                "renderer_unavailable",
                "FFmpeg rendering binaries or required capabilities are unavailable",
                status_code=503,
                details={"limitations": list(media_tools.status.render_limitations)},
            )

        candidate = await session.scalar(
            select(ClipCandidate).where(
                ClipCandidate.id == candidate_id,
                ClipCandidate.tenant_id == principal.tenant_id,
            )
        )
        if candidate is None:
            raise NotFoundError("candidate")
        if candidate.state != CandidateState.APPROVED:
            raise ConflictError("only an approved candidate may create a production render plan")
        asset = await session.scalar(
            select(Asset).where(
                Asset.id == candidate.asset_id,
                Asset.tenant_id == principal.tenant_id,
            )
        )
        metadata = await session.scalar(
            select(MediaMetadata).where(
                MediaMetadata.asset_id == candidate.asset_id,
                MediaMetadata.tenant_id == principal.tenant_id,
            )
        )
        transcript = await session.scalar(
            select(Transcript).where(
                Transcript.id == candidate.transcript_id,
                Transcript.tenant_id == principal.tenant_id,
            )
        )
        if asset is None or metadata is None or transcript is None:
            raise ClipOSError(
                "render_source_incomplete",
                "approved candidate source metadata or transcript is missing",
                status_code=422,
            )
        if asset.state != AssetState.READY or not asset.metadata_verified:
            raise ConflictError("source asset must be ready with verified metadata")
        if not asset.permission_confirmed:
            raise ConflictError("source permission must remain confirmed")
        if not metadata.has_video:
            raise ClipOSError(
                "source_video_required",
                "Phase 4 video rendering requires a source video stream",
                status_code=422,
            )
        if not metadata.has_audio:
            raise ClipOSError(
                "source_audio_required",
                "Phase 4 production rendering requires a source audio stream",
                status_code=422,
            )
        duration_ms = candidate.end_ms - candidate.start_ms
        if duration_ms <= 0 or duration_ms > round(settings.render_max_duration_seconds * 1000):
            raise ClipOSError(
                "render_duration_limit",
                "approved candidate duration is invalid or exceeds the render limit",
                status_code=422,
            )
        source_duration_ms = round(metadata.duration_seconds * 1000)
        if candidate.start_ms < 0 or candidate.end_ms > source_duration_ms:
            raise ClipOSError(
                "render_bounds_invalid",
                "approved candidate timestamps fall outside verified source duration",
                status_code=422,
            )

        preset = RenderingService._resolve_preset(request, settings)
        if request.captions.mode in {CaptionMode.BURN_IN, CaptionMode.BOTH} and (
            "subtitles" not in media_tools.status.capabilities
        ):
            raise ClipOSError(
                "subtitle_filter_unavailable",
                "caption burn-in was requested but FFmpeg subtitles support is unavailable",
                status_code=422,
            )
        branding_snapshot = await RenderingService._branding_snapshot(
            session,
            principal.tenant_id,
            requested=request.include_tenant_branding,
            media_tools=media_tools,
        )
        captions = await RenderingService._build_captions(
            session,
            tenant_id=principal.tenant_id,
            candidate=candidate,
            request=request,
            settings=settings,
        )
        await session.scalar(
            select(Tenant.id).where(Tenant.id == principal.tenant_id).with_for_update()
        )
        active_count = int(
            await session.scalar(
                select(func.count(RenderJob.id)).where(
                    RenderJob.tenant_id == principal.tenant_id,
                    RenderJob.state.in_([RenderState.QUEUED, *_ACTIVE_RENDER_STATES]),
                )
            )
            or 0
        )
        if active_count >= settings.render_max_queued_per_tenant:
            raise ClipOSError(
                "tenant_render_queue_limit",
                "tenant render queue has reached its configured limit",
                status_code=429,
            )

        now = datetime.now(UTC)
        plan_id = uuid4()
        background_job_id = uuid4()
        render_job_id = uuid4()
        applied_crop = (
            RenderCropStrategy.CENTER_CROP
            if request.crop_strategy == RenderCropStrategy.SMART_FACE_FALLBACK
            else request.crop_strategy
        )
        fallback_reason = (
            "face detection/tracking is unavailable; deterministic center crop was applied"
            if request.crop_strategy == RenderCropStrategy.SMART_FACE_FALLBACK
            else None
        )
        caption_max_chars = (
            request.captions.max_characters_per_line or settings.render_caption_max_chars_per_line
        )
        caption_max_lines = request.captions.max_lines or settings.render_caption_max_lines
        target_lufs = request.audio.target_lufs or settings.render_target_lufs
        true_peak_db = request.audio.true_peak_db or settings.render_true_peak_db
        limitations = [
            "no face detection or tracking; crop strategy is deterministic",
            "platform preset names describe technical targets, not platform acceptance",
            "no third-party music, logo upload, publishing, or external delivery",
        ]
        if not candidate.word_timestamps_available and request.captions.mode != CaptionMode.NONE:
            limitations.append(
                "captions use segment-level timing because word timestamps are unavailable"
            )
        if fallback_reason:
            limitations.append(fallback_reason)
        plan = RenderPlan(
            id=plan_id,
            tenant_id=principal.tenant_id,
            candidate_id=candidate.id,
            asset_id=asset.id,
            transcript_id=transcript.id,
            requested_by_user_id=principal.user_id,
            plan_version=PLAN_VERSION,
            renderer_version=RENDERER_VERSION,
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
            preset=request.preset,
            source_start_ms=candidate.start_ms,
            source_end_ms=candidate.end_ms,
            duration_ms=duration_ms,
            source_boundary_revision=candidate.boundary_revision,
            aspect_ratio=preset.aspect_ratio,
            width=preset.width,
            height=preset.height,
            frame_rate=preset.frame_rate,
            crop_strategy_requested=request.crop_strategy.value,
            crop_strategy_applied=applied_crop.value,
            smart_crop_used=False,
            crop_fallback_reason=fallback_reason,
            caption_mode=request.captions.mode.value,
            caption_style={
                "style": request.captions.style,
                "max_characters_per_line": caption_max_chars,
                "max_lines": caption_max_lines,
                "timing_source": (
                    "none"
                    if request.captions.mode == CaptionMode.NONE
                    else captions[0].timing_source
                ),
            },
            safe_area=preset.safe_area,
            overlays=([{"kind": "tenant_text_branding"}] if branding_snapshot else []),
            branding_snapshot=branding_snapshot,
            audio_settings={
                "normalization": request.audio.normalization.value,
                "target_lufs": target_lufs,
                "true_peak_db": true_peak_db,
                "channels": settings.render_output_audio_channels,
                "sample_rate": settings.render_output_audio_sample_rate,
                "transformation_is_explicit": True,
            },
            video_codec=request.video_codec,
            audio_codec=request.audio_codec,
            container=request.container,
            output_storage_prefix=f"renders/{plan_id}",
            source_time_mapping={
                "mapping_type": "linear_offset",
                "playback_rate": 1.0,
                "formula": "source_ms = output_ms + source_start_ms",
                "output_start_ms": 0,
                "output_end_ms": duration_ms,
                "source_start_ms": candidate.start_ms,
                "source_end_ms": candidate.end_ms,
            },
            provenance={
                "source_asset_id": str(asset.id),
                "source_checksum_sha256": asset.checksum_sha256,
                "transcript_id": str(transcript.id),
                "candidate_id": str(candidate.id),
                "candidate_state_at_plan": candidate.state.value,
                "candidate_boundary_revision": candidate.boundary_revision,
                "rights_note_recorded": bool(asset.rights_note),
                "permission_confirmed": asset.permission_confirmed,
                "created_at": now.isoformat(),
            },
            limitations=limitations,
        )
        background_job = BackgroundJob(
            id=background_job_id,
            tenant_id=principal.tenant_id,
            kind=RENDER_JOB_KIND,
            idempotency_key=f"render:{plan_id}",
            state=JobState.QUEUED,
            payload={"render_job_id": str(render_job_id), "render_plan_id": str(plan_id)},
            max_attempts=settings.render_max_attempts,
            correlation_id=correlation_id_var.get(),
        )
        render_job = RenderJob(
            id=render_job_id,
            tenant_id=principal.tenant_id,
            render_plan_id=plan_id,
            background_job_id=background_job_id,
            requested_by_user_id=principal.user_id,
            state=RenderState.QUEUED,
            progress_percent=0,
            stage="queued",
            max_attempts=settings.render_max_attempts,
            renderer_version=RENDERER_VERSION,
        )
        try:
            # Explicitly establish parents before dependent rows. The models intentionally avoid
            # mutable ORM relationships, so flush ordering is made portable rather than implicit.
            session.add_all([plan, background_job])
            await session.flush()
            session.add(render_job)
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raced = await session.scalar(
                select(RenderPlan).where(
                    RenderPlan.tenant_id == principal.tenant_id,
                    RenderPlan.idempotency_key == request.idempotency_key,
                )
            )
            if raced is None:
                raise
            if raced.request_hash != request_hash:
                raise ConflictError("render plan idempotency conflict") from exc
            return await RenderingService._creation_outcome(session, raced, idempotent=True)
        session.add_all(
            [
                RenderCaptionSegment(
                    tenant_id=principal.tenant_id,
                    render_plan_id=plan_id,
                    ordinal=cue.ordinal,
                    source_start_ms=cue.source_start_ms,
                    source_end_ms=cue.source_end_ms,
                    output_start_ms=cue.output_start_ms,
                    output_end_ms=cue.output_end_ms,
                    text=cue.text,
                    line_count=cue.line_count,
                    character_count=cue.character_count,
                    timing_source=cue.timing_source,
                )
                for cue in captions
            ]
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="render.plan_created",
            resource_type="render_plan",
            resource_id=str(plan_id),
            event_data={
                "candidate_id": str(candidate.id),
                "preset": request.preset,
                "caption_mode": request.captions.mode.value,
                "branding": bool(branding_snapshot),
            },
        )
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raced = await session.scalar(
                select(RenderPlan).where(
                    RenderPlan.tenant_id == principal.tenant_id,
                    RenderPlan.idempotency_key == request.idempotency_key,
                )
            )
            if raced is None:
                raise
            if raced.request_hash != request_hash:
                raise ConflictError("render plan idempotency conflict") from exc
            return await RenderingService._creation_outcome(session, raced, idempotent=True)
        await queue.enqueue(
            QueueMessage(
                job_id=background_job_id,
                tenant_id=principal.tenant_id,
                kind=RENDER_JOB_KIND,
            )
        )
        await session.refresh(plan)
        await session.refresh(render_job)
        caption_rows = await RenderingService._caption_rows(session, plan.id)
        return PlanCreationOutcome(plan, caption_rows, render_job, False)

    @staticmethod
    async def get_detail(
        session: AsyncSession, tenant_id: UUID, render_job_id: UUID
    ) -> RenderDetail:
        render_job = await RenderingService._get_job(session, tenant_id, render_job_id)
        plan = await session.scalar(
            select(RenderPlan).where(
                RenderPlan.id == render_job.render_plan_id,
                RenderPlan.tenant_id == tenant_id,
            )
        )
        if plan is None:
            raise NotFoundError("render plan")
        captions = await RenderingService._caption_rows(session, plan.id)
        outputs = list(
            (
                await session.scalars(
                    select(RenderOutput)
                    .where(
                        RenderOutput.render_job_id == render_job.id,
                        RenderOutput.tenant_id == tenant_id,
                    )
                    .order_by(RenderOutput.attempt_number.desc())
                )
            ).all()
        )
        defects = list(
            (
                await session.scalars(
                    select(RenderDefect)
                    .where(
                        RenderDefect.render_job_id == render_job.id,
                        RenderDefect.tenant_id == tenant_id,
                    )
                    .order_by(RenderDefect.created_at.desc())
                )
            ).all()
        )
        return RenderDetail(plan, captions, render_job, outputs, defects)

    @staticmethod
    async def list_jobs(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        state: RenderState | None,
        candidate_id: UUID | None,
        offset: int,
        limit: int,
    ) -> tuple[list[RenderListRow], int]:
        criteria = [RenderJob.tenant_id == tenant_id]
        if state is not None:
            criteria.append(RenderJob.state == state)
        if candidate_id is not None:
            criteria.append(RenderPlan.candidate_id == candidate_id)
        base = (
            select(RenderJob, RenderPlan)
            .join(RenderPlan, RenderPlan.id == RenderJob.render_plan_id)
            .where(*criteria)
        )
        count_query = (
            select(func.count(RenderJob.id))
            .join(RenderPlan, RenderPlan.id == RenderJob.render_plan_id)
            .where(*criteria)
        )
        total = int(await session.scalar(count_query) or 0)
        rows = list(
            (
                await session.execute(
                    base.order_by(RenderJob.created_at.desc()).offset(offset).limit(limit)
                )
            ).all()
        )
        results: list[RenderListRow] = []
        for render_job, plan in rows:
            current = await session.scalar(
                select(RenderOutput)
                .where(
                    RenderOutput.render_job_id == render_job.id,
                    RenderOutput.tenant_id == tenant_id,
                    RenderOutput.is_current.is_(True),
                )
                .order_by(RenderOutput.attempt_number.desc())
                .limit(1)
            )
            results.append(RenderListRow(render_job, plan, current))
        return results, total

    @staticmethod
    async def cancel(
        session: AsyncSession, *, principal: Principal, render_job_id: UUID
    ) -> RenderJob:
        render_job = await session.scalar(
            select(RenderJob)
            .where(
                RenderJob.id == render_job_id,
                RenderJob.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if render_job is None:
            raise NotFoundError("render")
        if render_job.state in _TERMINAL_RENDER_STATES:
            raise ConflictError(f"render is already {render_job.state.value}")
        background = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == render_job.background_job_id,
                BackgroundJob.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        render_job.cancellation_requested = True
        if render_job.state == RenderState.QUEUED:
            now = datetime.now(UTC)
            render_job.state = RenderState.CANCELLED
            render_job.stage = "cancelled"
            render_job.finished_at = now
            if background is not None:
                background.state = JobState.CANCELLED
                background.finished_at = now
                background.heartbeat_at = now
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="render.cancellation_requested",
            resource_type="render_job",
            resource_id=str(render_job.id),
            event_data={"state": render_job.state.value},
        )
        await session.commit()
        await session.refresh(render_job)
        return render_job

    @staticmethod
    async def retry(
        session: AsyncSession,
        *,
        principal: Principal,
        render_job_id: UUID,
        queue: QueueBroker,
    ) -> RenderJob:
        await RenderingService._require_running_control(session)
        render_job = await session.scalar(
            select(RenderJob)
            .where(
                RenderJob.id == render_job_id,
                RenderJob.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if render_job is None:
            raise NotFoundError("render")
        if render_job.state not in {
            RenderState.FAILED,
            RenderState.NEEDS_REVIEW,
            RenderState.CANCELLED,
        }:
            raise ConflictError("only failed, cancelled, or needs-review renders can be retried")
        if render_job.attempt_count >= render_job.max_attempts:
            raise ConflictError("render retry budget is exhausted")
        boundary_defect = await session.scalar(
            select(RenderDefect.id).where(
                RenderDefect.render_job_id == render_job.id,
                RenderDefect.tenant_id == principal.tenant_id,
                RenderDefect.meaning_or_boundary_changed.is_(True),
            )
        )
        if boundary_defect is not None:
            raise ConflictError(
                "meaning or boundary defects require a new candidate revision and approval"
            )
        background = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == render_job.background_job_id,
                BackgroundJob.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if background is None:
            raise ConflictError("render background job is missing")
        await session.execute(
            update(RenderOutput)
            .where(
                RenderOutput.render_job_id == render_job.id,
                RenderOutput.tenant_id == principal.tenant_id,
                RenderOutput.is_current.is_(True),
            )
            .values(is_current=False)
        )
        render_job.state = RenderState.QUEUED
        render_job.stage = "queued_for_retry"
        render_job.progress_percent = 0
        render_job.cancellation_requested = False
        render_job.failure_category = None
        render_job.failure_message = None
        render_job.failure_details = {}
        render_job.started_at = None
        render_job.finished_at = None
        render_job.elapsed_ms = None
        background.state = JobState.QUEUED
        background.failure_category = None
        background.failure_message = None
        background.result = None
        background.started_at = None
        background.finished_at = None
        background.heartbeat_at = None
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="render.retry_queued",
            resource_type="render_job",
            resource_id=str(render_job.id),
            event_data={"next_attempt": render_job.attempt_count + 1},
        )
        await session.commit()
        await queue.enqueue(
            QueueMessage(
                job_id=background.id,
                tenant_id=principal.tenant_id,
                kind=RENDER_JOB_KIND,
            )
        )
        await session.refresh(render_job)
        return render_job

    @staticmethod
    async def report_defect(
        session: AsyncSession,
        *,
        principal: Principal,
        render_job_id: UUID,
        request: RenderDefectCreate,
    ) -> tuple[RenderJob, RenderDefect]:
        render_job = await session.scalar(
            select(RenderJob)
            .where(
                RenderJob.id == render_job_id,
                RenderJob.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if render_job is None:
            raise NotFoundError("render")
        if render_job.state != RenderState.SUCCEEDED:
            raise ConflictError("a defect can be reported only for a succeeded render")
        output = await session.scalar(
            select(RenderOutput).where(
                RenderOutput.render_job_id == render_job.id,
                RenderOutput.tenant_id == principal.tenant_id,
                RenderOutput.is_current.is_(True),
            )
        )
        if output is None:
            raise ConflictError("render has no current validated output")
        defect = RenderDefect(
            tenant_id=principal.tenant_id,
            render_job_id=render_job.id,
            render_output_id=output.id,
            reported_by_user_id=principal.user_id,
            category=request.category.value,
            description=request.description,
            meaning_or_boundary_changed=request.meaning_or_boundary_changed,
            requested_resolution=request.requested_resolution,
        )
        session.add(defect)
        render_job.state = RenderState.NEEDS_REVIEW
        render_job.stage = "defect_reported"
        render_job.failure_category = "reported_render_defect"
        render_job.failure_message = "a human reviewer reported a render defect"
        background = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == render_job.background_job_id,
                BackgroundJob.tenant_id == principal.tenant_id,
            )
        )
        if background is not None:
            background.state = JobState.NEEDS_REVIEW
            background.failure_category = "reported_render_defect"
            background.failure_message = "a human reviewer reported a render defect"
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="render.defect_reported",
            resource_type="render_job",
            resource_id=str(render_job.id),
            event_data={
                "category": request.category.value,
                "meaning_or_boundary_changed": request.meaning_or_boundary_changed,
                "output_id": str(output.id),
            },
        )
        await session.commit()
        await session.refresh(render_job)
        await session.refresh(defect)
        return render_job, defect

    @staticmethod
    async def current_output(
        session: AsyncSession, tenant_id: UUID, render_job_id: UUID
    ) -> RenderOutput:
        await RenderingService._get_job(session, tenant_id, render_job_id)
        output = await session.scalar(
            select(RenderOutput)
            .where(
                RenderOutput.render_job_id == render_job_id,
                RenderOutput.tenant_id == tenant_id,
                RenderOutput.is_current.is_(True),
            )
            .order_by(RenderOutput.attempt_number.desc())
            .limit(1)
        )
        if output is None:
            raise NotFoundError("render output")
        return output

    @staticmethod
    async def _creation_outcome(
        session: AsyncSession, plan: RenderPlan, *, idempotent: bool
    ) -> PlanCreationOutcome:
        render_job = await session.scalar(
            select(RenderJob).where(
                RenderJob.render_plan_id == plan.id,
                RenderJob.tenant_id == plan.tenant_id,
            )
        )
        if render_job is None:
            raise ConflictError("render plan is missing its durable job")
        captions = await RenderingService._caption_rows(session, plan.id)
        return PlanCreationOutcome(plan, captions, render_job, idempotent)

    @staticmethod
    async def _caption_rows(
        session: AsyncSession, render_plan_id: UUID
    ) -> list[RenderCaptionSegment]:
        return list(
            (
                await session.scalars(
                    select(RenderCaptionSegment)
                    .where(RenderCaptionSegment.render_plan_id == render_plan_id)
                    .order_by(RenderCaptionSegment.ordinal)
                )
            ).all()
        )

    @staticmethod
    async def _get_job(session: AsyncSession, tenant_id: UUID, render_job_id: UUID) -> RenderJob:
        render_job = await session.scalar(
            select(RenderJob).where(
                RenderJob.id == render_job_id,
                RenderJob.tenant_id == tenant_id,
            )
        )
        if render_job is None:
            raise NotFoundError("render")
        return render_job

    @staticmethod
    async def _build_captions(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        candidate: ClipCandidate,
        request: RenderPlanCreateRequest,
        settings: Settings,
    ) -> list[CaptionCue]:
        if request.captions.mode == CaptionMode.NONE:
            return []
        words = list(
            (
                await session.scalars(
                    select(TranscriptWord)
                    .where(
                        TranscriptWord.tenant_id == tenant_id,
                        TranscriptWord.transcript_id == candidate.transcript_id,
                        TranscriptWord.end_ms > candidate.start_ms,
                        TranscriptWord.start_ms < candidate.end_ms,
                    )
                    .order_by(TranscriptWord.ordinal)
                )
            ).all()
        )
        segments = list(
            (
                await session.scalars(
                    select(TranscriptSegment)
                    .where(
                        TranscriptSegment.tenant_id == tenant_id,
                        TranscriptSegment.transcript_id == candidate.transcript_id,
                        TranscriptSegment.end_ms > candidate.start_ms,
                        TranscriptSegment.start_ms < candidate.end_ms,
                    )
                    .order_by(TranscriptSegment.ordinal)
                )
            ).all()
        )
        try:
            return build_caption_cues(
                words=words,
                segments=segments,
                clip_start_ms=candidate.start_ms,
                clip_end_ms=candidate.end_ms,
                max_characters_per_line=(
                    request.captions.max_characters_per_line
                    or settings.render_caption_max_chars_per_line
                ),
                max_lines=request.captions.max_lines or settings.render_caption_max_lines,
                max_cue_ms=round(settings.render_caption_max_cue_seconds * 1000),
                max_segments=settings.render_max_caption_segments,
            )
        except CaptionLayoutError as exc:
            raise ClipOSError(
                "caption_layout_invalid",
                str(exc),
                status_code=422,
            ) from exc

    @staticmethod
    async def _branding_snapshot(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        requested: bool,
        media_tools: MediaTooling,
    ) -> dict[str, Any]:
        if not requested:
            return {}
        if "drawtext" not in media_tools.status.capabilities:
            raise ClipOSError(
                "branding_filter_unavailable",
                "tenant text branding was requested but FFmpeg drawtext is unavailable",
                status_code=422,
            )
        profile = await RenderingService.get_profile(session, tenant_id)
        if profile is None or not profile.branding_enabled or not profile.branding_text:
            raise ClipOSError(
                "tenant_branding_not_configured",
                "tenant text branding must be configured and enabled before it can be rendered",
                status_code=422,
            )
        return {
            "kind": "tenant_text",
            "text": profile.branding_text,
            "position": profile.branding_position,
            "opacity": profile.branding_opacity,
            "font_size": profile.branding_font_size,
            "profile_id": str(profile.id),
            "profile_updated_at": profile.updated_at.isoformat(),
        }

    @staticmethod
    def _resolve_preset(request: RenderPlanCreateRequest, settings: Settings) -> ResolvedPreset:
        if request.preset == "custom":
            assert request.width is not None and request.height is not None
            width, height = request.width, request.height
            frame_rate = request.frame_rate or 30.0
        else:
            values = _PRESETS[request.preset]
            width = int(values["width"])
            height = int(values["height"])
            frame_rate = request.frame_rate or float(values["frame_rate"])
        if width > min(settings.render_max_width, settings.max_media_dimension) or height > min(
            settings.render_max_height, settings.max_media_dimension
        ):
            raise ClipOSError(
                "render_dimension_limit",
                "requested render dimensions exceed the configured limit",
                status_code=422,
            )
        if width * height > settings.render_max_pixels:
            raise ClipOSError(
                "render_pixel_limit",
                "requested render pixel count exceeds the configured resource limit",
                status_code=422,
            )
        if frame_rate > settings.render_max_frame_rate:
            raise ClipOSError(
                "render_frame_rate_limit",
                "requested frame rate exceeds the configured limit",
                status_code=422,
            )
        divisor = math.gcd(width, height)
        aspect_ratio = f"{width // divisor}:{height // divisor}"
        if height > width:
            safe = {"top": 0.08, "right": 0.08, "bottom": 0.15, "left": 0.08}
        elif height == width:
            safe = {"top": 0.07, "right": 0.07, "bottom": 0.1, "left": 0.07}
        else:
            safe = {"top": 0.05, "right": 0.05, "bottom": 0.08, "left": 0.05}
        return ResolvedPreset(width, height, frame_rate, aspect_ratio, safe)

    @staticmethod
    def _request_hash(candidate_id: UUID, request: RenderPlanCreateRequest) -> str:
        payload = {
            "candidate_id": str(candidate_id),
            "plan_version": PLAN_VERSION,
            "renderer_version": RENDERER_VERSION,
            "request": request.model_dump(mode="json", exclude={"idempotency_key"}),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    async def _require_running_control(session: AsyncSession) -> None:
        paused = await session.scalar(
            select(SystemControl.paused).where(SystemControl.key == "global")
        )
        if paused:
            raise ClipOSError(
                "system_paused",
                "global processing is paused; an owner must resume it before rendering",
                status_code=409,
            )


class RenderProcessor:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: ObjectStorage,
        media_tools: MediaTooling,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.media_tools = media_tools
        self.settings = settings

    async def process_job(self, message: QueueMessage) -> None:
        started = time.monotonic()
        render_job_id: UUID | None = None
        stored_keys: list[str] = []
        correlation_token: Token[str] | None = None
        try:
            async with self.session_factory() as session:
                background = await session.scalar(
                    select(BackgroundJob)
                    .where(
                        BackgroundJob.id == message.job_id,
                        BackgroundJob.tenant_id == message.tenant_id,
                    )
                    .with_for_update()
                )
                if (
                    background is None
                    or background.kind != RENDER_JOB_KIND
                    or background.state != JobState.QUEUED
                ):
                    return
                correlation_token = correlation_id_var.set(background.correlation_id)
                try:
                    render_job_id = UUID(str(background.payload["render_job_id"]))
                except (KeyError, TypeError, ValueError):
                    background.state = JobState.FAILED
                    background.failure_category = "invalid_job_payload"
                    background.failure_message = "render job payload is invalid"
                    background.finished_at = datetime.now(UTC)
                    await session.commit()
                    return
                render_job = await session.scalar(
                    select(RenderJob)
                    .where(
                        RenderJob.id == render_job_id,
                        RenderJob.tenant_id == message.tenant_id,
                        RenderJob.background_job_id == background.id,
                    )
                    .with_for_update()
                )
                if render_job is None or render_job.state != RenderState.QUEUED:
                    return
                if render_job.cancellation_requested:
                    await self._finish_cancelled(session, background, render_job, started)
                    return
                if render_job.attempt_count >= render_job.max_attempts:
                    await self._finish_failure(
                        session,
                        background=background,
                        render_job=render_job,
                        category="retry_budget_exhausted",
                        message="render retry budget is exhausted",
                        needs_review=True,
                        retryable=False,
                        started=started,
                    )
                    return
                await session.scalar(
                    select(Tenant.id).where(Tenant.id == message.tenant_id).with_for_update()
                )
                active = int(
                    await session.scalar(
                        select(func.count(RenderJob.id)).where(
                            RenderJob.tenant_id == message.tenant_id,
                            RenderJob.id != render_job.id,
                            RenderJob.state.in_(list(_ACTIVE_RENDER_STATES)),
                        )
                    )
                    or 0
                )
                if active >= self.settings.render_max_active_per_tenant:
                    return
                now = datetime.now(UTC)
                background.state = JobState.RUNNING
                background.attempts += 1
                background.started_at = now
                background.heartbeat_at = now
                background.finished_at = None
                render_job.state = RenderState.VALIDATING
                render_job.stage = "validating_plan_and_source"
                render_job.progress_percent = 5
                render_job.attempt_count += 1
                render_job.started_at = now
                render_job.finished_at = None
                render_job.elapsed_ms = None
                render_job.failure_category = None
                render_job.failure_message = None
                render_job.failure_details = {}
                plan = await session.scalar(
                    select(RenderPlan).where(
                        RenderPlan.id == render_job.render_plan_id,
                        RenderPlan.tenant_id == message.tenant_id,
                    )
                )
                if plan is None:
                    await self._finish_failure(
                        session,
                        background=background,
                        render_job=render_job,
                        category="render_plan_missing",
                        message="render plan is missing",
                        needs_review=False,
                        retryable=False,
                        started=started,
                    )
                    return
                asset = await session.scalar(
                    select(Asset).where(
                        Asset.id == plan.asset_id,
                        Asset.tenant_id == message.tenant_id,
                    )
                )
                if asset is None:
                    await self._finish_failure(
                        session,
                        background=background,
                        render_job=render_job,
                        category="render_source_missing",
                        message="source asset is missing",
                        needs_review=False,
                        retryable=False,
                        started=started,
                    )
                    return
                await session.commit()

            await asyncio.to_thread(
                self.settings.render_temp_root.mkdir,
                parents=True,
                exist_ok=True,
                mode=0o700,
            )
            self._check_disk_capacity(source_size_bytes=asset.size_bytes)
            with TemporaryDirectory(
                prefix="clipos-render-", dir=self.settings.render_temp_root
            ) as directory:
                workdir = Path(directory)
                source_path = workdir / f"source{asset.safe_extension}"
                try:
                    source_info = await self.storage.copy_to(
                        asset.tenant_id, asset.storage_key, source_path
                    )
                except FileNotFoundError as exc:
                    raise MediaPipelineError(
                        "render_source_missing",
                        "private source media is missing from storage",
                        retryable=False,
                    ) from exc
                if (
                    source_info.checksum_sha256 != asset.checksum_sha256
                    or source_info.size != asset.size_bytes
                ):
                    await asyncio.to_thread(source_path.unlink, missing_ok=True)
                    raise MediaPipelineError(
                        "render_source_integrity_changed",
                        "private source bytes no longer match the approved asset provenance",
                        needs_review=True,
                        retryable=False,
                    )
                source_probe = await self.media_tools.probe(source_path)
                self._validate_source(plan, source_probe)
                await self._set_rendering(render_job_id, asset.tenant_id)

                caption_rows = await self._load_captions(plan.id, asset.tenant_id)
                caption_file: Path | None = None
                sidecar_text: str | None = None
                if plan.caption_mode != CaptionMode.NONE.value:
                    cues = [self._cue_from_row(item) for item in caption_rows]
                    sidecar_text = cues_to_srt(cues)
                    srt_path = workdir / "captions.srt"
                    await asyncio.to_thread(srt_path.write_text, sidecar_text, encoding="utf-8")
                    if plan.caption_mode in {
                        CaptionMode.BURN_IN.value,
                        CaptionMode.BOTH.value,
                    }:
                        caption_file = srt_path
                branding_file: Path | None = None
                if plan.branding_snapshot:
                    branding_file = workdir / "tenant-brand.txt"
                    await asyncio.to_thread(
                        branding_file.write_text,
                        str(plan.branding_snapshot["text"]),
                        encoding="utf-8",
                    )

                output_path = workdir / "render.mp4"
                last_progress = -1
                last_cancel_check = 0.0
                cached_cancelled = False

                async def progress(fraction: float) -> None:
                    nonlocal last_progress
                    percent = min(88, max(20, 20 + round(fraction * 68)))
                    if percent > last_progress:
                        last_progress = percent
                        await self._progress(render_job_id, asset.tenant_id, percent)

                async def cancelled() -> bool:
                    nonlocal last_cancel_check, cached_cancelled
                    now_value = time.monotonic()
                    if now_value - last_cancel_check < 0.5:
                        return cached_cancelled
                    last_cancel_check = now_value
                    cached_cancelled = await self._is_cancelled(render_job_id, asset.tenant_id)
                    return cached_cancelled

                audio = plan.audio_settings
                brand = plan.branding_snapshot
                await self.media_tools.render_clip(
                    RenderSpec(
                        source=source_path,
                        destination=output_path,
                        start_seconds=plan.source_start_ms / 1000,
                        duration_seconds=plan.duration_ms / 1000,
                        width=plan.width,
                        height=plan.height,
                        frame_rate=plan.frame_rate,
                        crop_strategy=plan.crop_strategy_applied,
                        caption_file=caption_file,
                        caption_style=str(plan.caption_style.get("style", "high_contrast_bottom")),
                        safe_bottom_fraction=float(plan.safe_area.get("bottom", 0.1)),
                        branding_file=branding_file,
                        branding_position=str(brand.get("position", "top_right")),
                        branding_opacity=float(brand.get("opacity", 0.85)),
                        branding_font_size=int(brand.get("font_size", 36)),
                        normalize_audio=audio.get("normalization") == "speech_loudness",
                        target_lufs=float(audio.get("target_lufs", -16)),
                        true_peak_db=float(audio.get("true_peak_db", -1.5)),
                        audio_sample_rate=int(audio.get("sample_rate", 48000)),
                        audio_channels=int(audio.get("channels", 2)),
                        x264_preset=self.settings.render_x264_preset,
                        crf=self.settings.render_crf,
                        max_output_bytes=self.settings.render_max_output_bytes,
                    ),
                    progress_callback=progress,
                    cancellation_requested=cancelled,
                )
                await self._set_validating_output(render_job_id, asset.tenant_id)
                output_probe = await self.media_tools.probe(output_path)
                validation = await asyncio.to_thread(
                    self._validate_output, plan, output_probe, output_path
                )
                attempt = render_job.attempt_count
                unique = uuid4().hex
                output_key = f"{plan.output_storage_prefix}/attempt-{attempt:02d}-{unique}.mp4"
                output_info = await self.storage.put_file(
                    asset.tenant_id,
                    output_key,
                    output_path,
                    content_type="video/mp4",
                )
                stored_keys.append(output_key)
                sidecar_key: str | None = None
                sidecar_checksum: str | None = None
                if sidecar_text is not None and plan.caption_mode in {
                    CaptionMode.SIDECAR.value,
                    CaptionMode.BOTH.value,
                }:
                    sidecar_key = f"{plan.output_storage_prefix}/attempt-{attempt:02d}-{unique}.srt"
                    sidecar_info = await self.storage.put(
                        asset.tenant_id,
                        sidecar_key,
                        sidecar_text.encode("utf-8"),
                        content_type="application/x-subrip; charset=utf-8",
                    )
                    sidecar_checksum = sidecar_info.checksum_sha256
                    stored_keys.append(sidecar_key)
                await self._finish_success(
                    render_job_id=render_job_id,
                    tenant_id=asset.tenant_id,
                    output_key=output_key,
                    output_checksum=output_info.checksum_sha256,
                    output_size=output_info.size,
                    sidecar_key=sidecar_key,
                    sidecar_checksum=sidecar_checksum,
                    probe=output_probe,
                    validation=validation,
                    started=started,
                )
                stored_keys.clear()
        except MediaProcessCancelledError:
            if render_job_id is not None:
                await self._finish_cancelled_by_id(render_job_id, message.tenant_id, started)
        except MediaPipelineError as exc:
            if render_job_id is not None:
                await self._finish_failure_by_id(
                    render_job_id,
                    message.tenant_id,
                    category=exc.category,
                    message=exc.safe_message,
                    needs_review=exc.needs_review,
                    retryable=exc.retryable,
                    started=started,
                )
        except OSError:
            if render_job_id is not None:
                await self._finish_failure_by_id(
                    render_job_id,
                    message.tenant_id,
                    category="render_storage_or_disk_failure",
                    message="render storage or temporary disk processing failed",
                    needs_review=False,
                    retryable=True,
                    started=started,
                )
        except Exception:
            if render_job_id is not None:
                await self._finish_failure_by_id(
                    render_job_id,
                    message.tenant_id,
                    category="unexpected_render_error",
                    message="render failed unexpectedly",
                    needs_review=False,
                    retryable=False,
                    started=started,
                )
            logger.exception("render processor contained an unexpected failure")
        finally:
            for key in stored_keys:
                try:
                    await self.storage.delete(message.tenant_id, key)
                except Exception:
                    logger.warning("could not remove an orphaned failed render object")
            if correlation_token is not None:
                correlation_id_var.reset(correlation_token)

    def _check_disk_capacity(self, *, source_size_bytes: int) -> None:
        usage = shutil.disk_usage(self.settings.render_temp_root)
        # The temporary work directory holds a full private source copy and the bounded output.
        # Reserve both before copying source bytes so a job cannot intentionally fill the mount.
        required = (
            self.settings.render_min_free_disk_bytes
            + source_size_bytes
            + self.settings.render_max_output_bytes
        )
        if usage.free < required:
            raise MediaPipelineError(
                "insufficient_render_disk",
                "insufficient temporary disk capacity for the bounded render",
                retryable=True,
            )

    @staticmethod
    def _validate_source(plan: RenderPlan, probe: ProbeResult) -> None:
        if not probe.has_video:
            raise MediaPipelineError(
                "source_video_missing", "source no longer contains a video stream"
            )
        if not probe.has_audio:
            raise MediaPipelineError(
                "source_audio_missing", "source no longer contains an audio stream"
            )
        if plan.source_end_ms > round(probe.duration_seconds * 1000):
            raise MediaPipelineError(
                "source_duration_changed",
                "source duration no longer contains the approved candidate window",
                needs_review=True,
            )

    def _validate_output(
        self, plan: RenderPlan, probe: ProbeResult, output_path: Path
    ) -> dict[str, Any]:
        size = output_path.stat().st_size
        expected_duration = plan.duration_ms / 1000
        duration_delta = abs(probe.duration_seconds - expected_duration)
        if size <= 0 or size > self.settings.render_max_output_bytes:
            raise MediaPipelineError(
                "output_size_invalid", "rendered output size failed validation"
            )
        if not probe.has_video:
            raise MediaPipelineError(
                "output_video_missing", "rendered output has no video stream", needs_review=True
            )
        if not probe.has_audio:
            raise MediaPipelineError(
                "output_audio_missing", "rendered output has no audio stream", needs_review=True
            )
        if probe.width != plan.width or probe.height != plan.height:
            raise MediaPipelineError(
                "output_dimensions_mismatch",
                "rendered output dimensions do not match the editing plan",
                needs_review=True,
            )
        if duration_delta > self.settings.render_duration_tolerance_seconds:
            raise MediaPipelineError(
                "output_duration_mismatch",
                "rendered output duration differs from the approved window beyond tolerance",
                needs_review=True,
            )
        if probe.video_codec != "h264" or probe.audio_codec != "aac":
            raise MediaPipelineError(
                "output_codec_mismatch",
                "rendered output codecs do not match the editing plan",
                needs_review=True,
            )
        if not any(item in probe.format_name.split(",") for item in {"mp4", "mov"}):
            raise MediaPipelineError(
                "output_format_mismatch",
                "rendered output container is not a valid MP4 family format",
                needs_review=True,
            )
        if probe.frame_rate is None or abs(probe.frame_rate - plan.frame_rate) > 0.5:
            raise MediaPipelineError(
                "output_frame_rate_mismatch",
                "rendered output frame rate does not match the editing plan",
                needs_review=True,
            )
        expected_channels = int(plan.audio_settings.get("channels", 2))
        expected_rate = int(plan.audio_settings.get("sample_rate", 48000))
        if probe.audio_channels != expected_channels or probe.audio_sample_rate != expected_rate:
            raise MediaPipelineError(
                "output_audio_layout_mismatch",
                "rendered output audio channel or sample-rate transformation is invalid",
                needs_review=True,
            )
        return {
            "passed": True,
            "expected_duration_seconds": expected_duration,
            "actual_duration_seconds": probe.duration_seconds,
            "duration_delta_seconds": duration_delta,
            "duration_tolerance_seconds": self.settings.render_duration_tolerance_seconds,
            "expected_dimensions": [plan.width, plan.height],
            "actual_dimensions": [probe.width, probe.height],
            "expected_frame_rate": plan.frame_rate,
            "actual_frame_rate": probe.frame_rate,
            "expected_streams": ["video", "audio"],
            "actual_streams": [
                "video" if probe.has_video else None,
                "audio" if probe.has_audio else None,
            ],
            "size_bytes": size,
        }

    async def _load_captions(self, plan_id: UUID, tenant_id: UUID) -> list[RenderCaptionSegment]:
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(RenderCaptionSegment)
                        .where(
                            RenderCaptionSegment.render_plan_id == plan_id,
                            RenderCaptionSegment.tenant_id == tenant_id,
                        )
                        .order_by(RenderCaptionSegment.ordinal)
                    )
                ).all()
            )

    @staticmethod
    def _cue_from_row(row: RenderCaptionSegment) -> CaptionCue:
        return CaptionCue(
            ordinal=row.ordinal,
            source_start_ms=row.source_start_ms,
            source_end_ms=row.source_end_ms,
            output_start_ms=row.output_start_ms,
            output_end_ms=row.output_end_ms,
            text=row.text,
            line_count=row.line_count,
            character_count=row.character_count,
            timing_source=row.timing_source,
        )

    async def _set_rendering(self, render_job_id: UUID, tenant_id: UUID) -> None:
        async with self.session_factory() as session:
            render_job, background = await self._load_pair(session, render_job_id, tenant_id)
            if render_job.cancellation_requested:
                raise MediaProcessCancelledError()
            render_job.state = RenderState.RENDERING
            render_job.stage = "ffmpeg_rendering"
            render_job.progress_percent = 20
            background.heartbeat_at = datetime.now(UTC)
            await session.commit()

    async def _set_validating_output(self, render_job_id: UUID, tenant_id: UUID) -> None:
        async with self.session_factory() as session:
            render_job, background = await self._load_pair(session, render_job_id, tenant_id)
            if render_job.cancellation_requested:
                raise MediaProcessCancelledError()
            render_job.state = RenderState.VALIDATING_OUTPUT
            render_job.stage = "probing_and_validating_output"
            render_job.progress_percent = 92
            background.heartbeat_at = datetime.now(UTC)
            await session.commit()

    async def _progress(self, render_job_id: UUID, tenant_id: UUID, percent: int) -> None:
        async with self.session_factory() as session:
            render_job, background = await self._load_pair(session, render_job_id, tenant_id)
            if render_job.state == RenderState.RENDERING and percent > render_job.progress_percent:
                render_job.progress_percent = min(88, percent)
                background.heartbeat_at = datetime.now(UTC)
                await session.commit()

    async def _is_cancelled(self, render_job_id: UUID, tenant_id: UUID) -> bool:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(RenderJob.cancellation_requested, BackgroundJob.state)
                    .join(
                        BackgroundJob,
                        BackgroundJob.id == RenderJob.background_job_id,
                    )
                    .where(RenderJob.id == render_job_id, RenderJob.tenant_id == tenant_id)
                )
            ).one_or_none()
            return bool(row is None or row[0] or row[1] == JobState.CANCELLED)

    async def _finish_success(
        self,
        *,
        render_job_id: UUID,
        tenant_id: UUID,
        output_key: str,
        output_checksum: str,
        output_size: int,
        sidecar_key: str | None,
        sidecar_checksum: str | None,
        probe: ProbeResult,
        validation: dict[str, Any],
        started: float,
    ) -> None:
        async with self.session_factory() as session:
            render_job, background = await self._load_pair(session, render_job_id, tenant_id)
            plan = await session.scalar(
                select(RenderPlan).where(
                    RenderPlan.id == render_job.render_plan_id,
                    RenderPlan.tenant_id == tenant_id,
                )
            )
            if plan is None:
                raise RuntimeError("render plan disappeared before output persistence")
            if render_job.cancellation_requested:
                raise MediaProcessCancelledError()
            await session.execute(
                update(RenderOutput)
                .where(
                    RenderOutput.render_job_id == render_job.id,
                    RenderOutput.tenant_id == tenant_id,
                    RenderOutput.is_current.is_(True),
                )
                .values(is_current=False)
            )
            output = RenderOutput(
                tenant_id=tenant_id,
                render_job_id=render_job.id,
                render_plan_id=plan.id,
                attempt_number=render_job.attempt_count,
                is_current=True,
                storage_key=output_key,
                checksum_sha256=output_checksum,
                size_bytes=output_size,
                content_type="video/mp4",
                sidecar_storage_key=sidecar_key,
                sidecar_checksum_sha256=sidecar_checksum,
                format_name=probe.format_name,
                duration_seconds=probe.duration_seconds,
                video_codec=probe.video_codec or "unknown",
                audio_codec=probe.audio_codec or "unknown",
                width=probe.width or 0,
                height=probe.height or 0,
                frame_rate=probe.frame_rate or 0,
                audio_channels=probe.audio_channels,
                audio_sample_rate=probe.audio_sample_rate,
                probe_version=probe.probe_version,
                probe_payload=probe.raw,
                validation=validation,
                provenance={
                    **plan.provenance,
                    "render_plan_id": str(plan.id),
                    "render_job_id": str(render_job.id),
                    "attempt_number": render_job.attempt_count,
                    "source_time_mapping": plan.source_time_mapping,
                    "crop_strategy_requested": plan.crop_strategy_requested,
                    "crop_strategy_applied": plan.crop_strategy_applied,
                    "smart_crop_used": plan.smart_crop_used,
                    "caption_mode": plan.caption_mode,
                    "branding_snapshot": plan.branding_snapshot,
                },
                renderer_version=RENDERER_VERSION,
            )
            session.add(output)
            await session.flush()
            now = datetime.now(UTC)
            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            render_job.state = RenderState.SUCCEEDED
            render_job.stage = "succeeded"
            render_job.progress_percent = 100
            render_job.elapsed_ms = elapsed_ms
            render_job.finished_at = now
            render_job.failure_category = None
            render_job.failure_message = None
            render_job.failure_details = {}
            background.state = JobState.SUCCEEDED
            background.finished_at = now
            background.heartbeat_at = now
            background.result = {
                "render_job_id": str(render_job.id),
                "render_plan_id": str(plan.id),
                "render_output_id": str(output.id),
                "attempt_number": render_job.attempt_count,
                "checksum_sha256": output_checksum,
                "size_bytes": output_size,
            }
            background.failure_category = None
            background.failure_message = None
            AuditService.record(
                session,
                tenant_id=tenant_id,
                actor_user_id=None,
                action="render.succeeded",
                resource_type="render_job",
                resource_id=str(render_job.id),
                event_data={
                    "output_id": str(output.id),
                    "attempt": render_job.attempt_count,
                    "elapsed_ms": elapsed_ms,
                    "size_bytes": output_size,
                    "renderer_version": RENDERER_VERSION,
                },
            )
            await session.commit()

    async def _finish_cancelled_by_id(
        self, render_job_id: UUID, tenant_id: UUID, started: float
    ) -> None:
        async with self.session_factory() as session:
            render_job, background = await self._load_pair(session, render_job_id, tenant_id)
            await self._finish_cancelled(session, background, render_job, started)

    @staticmethod
    async def _finish_cancelled(
        session: AsyncSession,
        background: BackgroundJob,
        render_job: RenderJob,
        started: float,
    ) -> None:
        now = datetime.now(UTC)
        render_job.state = RenderState.CANCELLED
        render_job.stage = "cancelled"
        render_job.finished_at = now
        render_job.elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        render_job.failure_category = None
        render_job.failure_message = None
        render_job.failure_details = {}
        background.state = JobState.CANCELLED
        background.finished_at = now
        background.heartbeat_at = now
        background.failure_category = None
        background.failure_message = None
        AuditService.record(
            session,
            tenant_id=render_job.tenant_id,
            actor_user_id=None,
            action="render.cancelled",
            resource_type="render_job",
            resource_id=str(render_job.id),
            event_data={"attempt": render_job.attempt_count},
        )
        await session.commit()

    async def _finish_failure_by_id(
        self,
        render_job_id: UUID,
        tenant_id: UUID,
        *,
        category: str,
        message: str,
        needs_review: bool,
        retryable: bool,
        started: float,
    ) -> None:
        async with self.session_factory() as session:
            render_job, background = await self._load_pair(session, render_job_id, tenant_id)
            await self._finish_failure(
                session,
                background=background,
                render_job=render_job,
                category=category,
                message=message,
                needs_review=needs_review,
                retryable=retryable,
                started=started,
            )

    @staticmethod
    async def _finish_failure(
        session: AsyncSession,
        *,
        background: BackgroundJob,
        render_job: RenderJob,
        category: str,
        message: str,
        needs_review: bool,
        retryable: bool,
        started: float,
    ) -> None:
        exhausted = render_job.attempt_count >= render_job.max_attempts
        target_review = needs_review or (retryable and exhausted)
        now = datetime.now(UTC)
        render_job.state = RenderState.NEEDS_REVIEW if target_review else RenderState.FAILED
        render_job.stage = render_job.state.value
        render_job.finished_at = now
        render_job.elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
        render_job.failure_category = category[:100]
        render_job.failure_message = message[:500]
        render_job.failure_details = {
            "retryable": retryable,
            "retry_budget_exhausted": exhausted,
            "attempt": render_job.attempt_count,
        }
        background.state = JobState.NEEDS_REVIEW if target_review else JobState.FAILED
        background.finished_at = now
        background.heartbeat_at = now
        background.failure_category = category[:100]
        background.failure_message = message[:500]
        AuditService.record(
            session,
            tenant_id=render_job.tenant_id,
            actor_user_id=None,
            action="render.stopped",
            resource_type="render_job",
            resource_id=str(render_job.id),
            outcome=AuditOutcome.FAILURE,
            event_data={
                "state": render_job.state.value,
                "category": category,
                "retryable": retryable,
                "attempt": render_job.attempt_count,
            },
        )
        await session.commit()

    @staticmethod
    async def _load_pair(
        session: AsyncSession, render_job_id: UUID, tenant_id: UUID
    ) -> tuple[RenderJob, BackgroundJob]:
        render_job = await session.scalar(
            select(RenderJob)
            .where(RenderJob.id == render_job_id, RenderJob.tenant_id == tenant_id)
            .with_for_update()
        )
        if render_job is None:
            raise RuntimeError("render job disappeared")
        background = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.id == render_job.background_job_id,
                BackgroundJob.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if background is None:
            raise RuntimeError("render background job disappeared")
        return render_job, background


class RenderWorker:
    def __init__(
        self,
        *,
        processor: RenderProcessor,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.processor = processor
        self.session_factory = session_factory
        self.settings = settings

    async def recover_queued_job(self) -> QueueMessage | None:
        async with self.session_factory() as session:
            recovered = await self._recover_stale_job(session)
            if recovered is not None:
                return recovered
            background = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == RENDER_JOB_KIND,
                    BackgroundJob.state == JobState.QUEUED,
                )
                .order_by(BackgroundJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if background is None:
                return None
            return QueueMessage(
                job_id=background.id,
                tenant_id=background.tenant_id,
                kind=background.kind,
            )

    async def _recover_stale_job(self, session: AsyncSession) -> QueueMessage | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.worker_stale_job_seconds)
        background = await session.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.kind == RENDER_JOB_KIND,
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
        if background is None:
            return None
        render_job = await session.scalar(
            select(RenderJob)
            .where(
                RenderJob.background_job_id == background.id,
                RenderJob.tenant_id == background.tenant_id,
            )
            .with_for_update(skip_locked=True)
        )
        if render_job is None:
            background.state = JobState.FAILED
            background.failure_category = "render_job_missing"
            background.failure_message = "stale background job has no render job"
            background.finished_at = datetime.now(UTC)
            await session.commit()
            return None
        if render_job.cancellation_requested:
            await self.processor._finish_cancelled(
                session, background, render_job, time.monotonic()
            )
            return None
        if render_job.attempt_count >= render_job.max_attempts:
            await self.processor._finish_failure(
                session,
                background=background,
                render_job=render_job,
                category="retry_budget_exhausted",
                message="stale render exhausted its retry budget",
                needs_review=True,
                retryable=False,
                started=time.monotonic(),
            )
            return None
        background.state = JobState.QUEUED
        background.started_at = None
        background.heartbeat_at = None
        background.finished_at = None
        background.failure_category = None
        background.failure_message = None
        render_job.state = RenderState.QUEUED
        render_job.stage = "recovered_after_worker_loss"
        render_job.progress_percent = 0
        render_job.started_at = None
        render_job.finished_at = None
        render_job.failure_category = None
        render_job.failure_message = None
        render_job.failure_details = {}
        AuditService.record(
            session,
            tenant_id=render_job.tenant_id,
            actor_user_id=None,
            action="render.recovered",
            resource_type="render_job",
            resource_id=str(render_job.id),
            event_data={"attempts": render_job.attempt_count, "stale_before": cutoff.isoformat()},
        )
        await session.commit()
        return QueueMessage(
            job_id=background.id,
            tenant_id=background.tenant_id,
            kind=background.kind,
        )
