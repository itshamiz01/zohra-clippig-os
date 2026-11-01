from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from clipos.config import Settings
from clipos.media.tooling import ToolStatus
from clipos.queue.base import QueueBroker
from clipos.schemas import HealthComponent, HealthResponse


async def health_response(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.version,
        environment=settings.environment.value,
    )


async def readiness_response(
    *,
    settings: Settings,
    engine: AsyncEngine,
    queue: QueueBroker,
    storage_root: Path | None,
    media_status: ToolStatus,
) -> tuple[HealthResponse, int]:
    components: dict[str, HealthComponent] = {}
    ready = True
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        components["database"] = HealthComponent(status="ok", required=True)
    except Exception:
        components["database"] = HealthComponent(
            status="error", required=True, detail="database check failed"
        )
        ready = False

    queue_required = settings.queue_backend == "redis" or settings.redis_required
    try:
        queue_ok = await queue.ping()
        components["queue"] = HealthComponent(
            status="ok" if queue_ok else "error",
            required=queue_required,
            detail=None if queue_ok else "queue ping failed",
        )
        if queue_required and not queue_ok:
            ready = False
    except Exception:
        components["queue"] = HealthComponent(
            status="error", required=queue_required, detail="queue check failed"
        )
        if queue_required:
            ready = False

    if settings.storage_backend == "local":
        storage_ok = bool(
            storage_root
            and await asyncio.to_thread(storage_root.exists)
            and await asyncio.to_thread(storage_root.is_dir)
        )
        components["storage"] = HealthComponent(
            status="ok" if storage_ok else "error",
            required=True,
            detail=None if storage_ok else "local storage root unavailable",
        )
        ready = ready and storage_ok
    else:
        components["storage"] = HealthComponent(
            status="unverified",
            required=True,
            detail="S3 adapter is not implemented in the current phase",
        )
        ready = False

    components["media_tools"] = HealthComponent(
        status="ok" if media_status.available else "error",
        required=True,
        detail=(
            media_status.ffmpeg_version
            if media_status.available
            else f"FFmpeg/FFprobe check failed: {media_status.error_category or 'unknown'}"
        ),
    )
    if not media_status.available:
        ready = False

    transcription_configured = settings.transcription_provider != "disabled"
    components["transcription"] = HealthComponent(
        status="unverified" if transcription_configured else "disabled",
        required=False,
        detail=(
            "configuration present; readiness does not send user media to a provider"
            if transcription_configured
            else "assets with audio will stop in needs_review until a provider is configured"
        ),
    )

    model_configured = settings.model_provider != "disabled"
    components["model_gateway"] = HealthComponent(
        status="unverified" if model_configured else "disabled",
        required=False,
        detail=(
            "configuration present; no live provider call is made by readiness"
            if model_configured
            else "no model provider configured"
        ),
    )
    # Asynchronous provider features report truthful status but do not gate the core API.

    return (
        HealthResponse(
            status="ready" if ready else "not_ready",
            version=settings.version,
            environment=settings.environment.value,
            components=components,
        ),
        200 if ready else 503,
    )
