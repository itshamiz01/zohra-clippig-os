from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from clipos.api.routes import (
    analytics,
    approvals,
    assets,
    auth,
    diagnostics,
    discovery,
    health,
    jobs,
    publishing,
    quality,
    rendering,
)
from clipos.config import Settings, get_settings
from clipos.core.enums import ProviderMode
from clipos.core.errors import register_error_handlers
from clipos.core.logging import configure_logging
from clipos.core.middleware import (
    CorrelationIdMiddleware,
    MemoryRateLimiter,
    RateLimitMiddleware,
    RedisRateLimiter,
    RequestSizeMiddleware,
    SecurityHeadersMiddleware,
)
from clipos.database import Base, build_engine, build_session_factory
from clipos.gateways.analytics import build_analytics_adapters
from clipos.gateways.models import build_model_gateway
from clipos.gateways.publishing import build_publishing_adapters
from clipos.gateways.transcription import build_transcription_gateway
from clipos.media.tooling import MediaTooling
from clipos.models import SystemControl  # noqa: F401 - registers all model metadata
from clipos.queue import InMemoryQueueBroker, QueueBroker, RedisQueueBroker
from clipos.services.analytics_processing import AnalyticsProcessor
from clipos.services.asset_processing import AssetProcessingService
from clipos.services.providers import ProviderStatusService
from clipos.services.publish_processing import PublishProcessor
from clipos.services.rendering import RenderProcessor
from clipos.services.system_control import SystemControlService
from clipos.services.workers import ClipOSWorker
from clipos.storage import LocalObjectStorage


def create_app(runtime_settings: Settings | None = None) -> FastAPI:
    settings = runtime_settings or get_settings()
    configure_logging(settings.log_level)
    rate_limiter = (
        RedisRateLimiter(settings.redis_url.get_secret_value(), settings.rate_limit_per_minute)
        if settings.queue_backend == "redis"
        else MemoryRateLimiter(settings.rate_limit_per_minute)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory

        if settings.auto_create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        queue: QueueBroker
        if settings.queue_backend == "redis":
            queue = RedisQueueBroker(settings.redis_url.get_secret_value())
        else:
            queue = InMemoryQueueBroker()
        app.state.queue = queue

        if settings.storage_backend == "local":
            storage = LocalObjectStorage(settings.storage_local_root)
            app.state.storage = storage
            app.state.storage_root = storage.root
        else:
            app.state.storage = None
            app.state.storage_root = None

        app.state.model_gateway = build_model_gateway(settings)
        app.state.transcription_gateway = build_transcription_gateway(settings)
        app.state.publishing_adapters = build_publishing_adapters(settings)
        app.state.analytics_adapters = build_analytics_adapters(settings)
        media_tools = MediaTooling(settings)
        app.state.media_tools = media_tools
        media_status = await media_tools.check()

        async with session_factory() as session:
            await SystemControlService.get(session)
            await ProviderStatusService.seed(session, settings)
            await ProviderStatusService.update_runtime_status(
                session,
                provider="ffmpeg",
                capability="media_probe_and_audio_extraction",
                mode=(ProviderMode.REAL_TESTED if media_status.available else ProviderMode.BLOCKED),
                error_category=media_status.error_category,
            )
            await ProviderStatusService.update_runtime_status(
                session,
                provider="ffmpeg",
                capability="media_rendering",
                mode=(
                    ProviderMode.REAL_TESTED if media_status.render_ready else ProviderMode.BLOCKED
                ),
                error_category=(
                    media_status.error_category
                    or (None if media_status.render_ready else "render_capability_missing")
                ),
            )

        asset_processor = AssetProcessingService(
            session_factory=session_factory,
            storage=app.state.storage,
            media_tools=media_tools,
            transcription=app.state.transcription_gateway,
            settings=settings,
        )
        render_processor = RenderProcessor(
            session_factory=session_factory,
            storage=app.state.storage,
            media_tools=media_tools,
            settings=settings,
        )
        publish_processor = PublishProcessor(
            session_factory=session_factory,
            storage=app.state.storage,
            registry=app.state.publishing_adapters,
            settings=settings,
        )
        analytics_processor = AnalyticsProcessor(
            session_factory=session_factory,
            registry=app.state.analytics_adapters,
            settings=settings,
        )
        app.state.asset_processor = asset_processor
        app.state.render_processor = render_processor
        app.state.publish_processor = publish_processor
        app.state.analytics_processor = analytics_processor
        worker = ClipOSWorker(
            asset_processor=asset_processor,
            render_processor=render_processor,
            publish_processor=publish_processor,
            analytics_processor=analytics_processor,
            session_factory=session_factory,
            queue=queue,
            settings=settings,
        )
        app.state.worker = worker
        app.state.asset_worker = worker
        worker_task = (
            asyncio.create_task(worker.run(), name="clipos-embedded-media-worker")
            if settings.embedded_worker_enabled
            else None
        )

        yield

        if worker_task:
            worker.stop()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        await app.state.transcription_gateway.close()
        await app.state.model_gateway.close()
        await app.state.publishing_adapters.close()
        await app.state.analytics_adapters.close()
        await queue.close()
        await rate_limiter.close()
        await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Safety-first Phase 7 media control plane with provider-neutral publishing contracts, "
            "encrypted OAuth credentials, a resumable YouTube adapter, private-first releases, "
            "mandatory fresh quality/rights gates, exact public-visibility approval, durable job "
            "control, and reconciliation-before-retry. Phase 7 adds truthful analytics "
            "collection (missing metrics are never invented zeros), experiments, and a bounded "
            "learning loop whose recommendations require human review and cannot mutate "
            "rights, quality, policy or budget state. Unrelated providers remain disabled and "
            "real provider verification is reported separately from mocked evidence."
        ),
        lifespan=lifespan,
        docs_url=None if settings.environment.value == "production" else "/docs",
        redoc_url=None,
    )

    # Middleware is executed in reverse registration order in Starlette.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "X-RateLimit-Remaining"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
    app.add_middleware(
        RequestSizeMiddleware,
        max_bytes=settings.max_request_bytes,
        max_upload_bytes=settings.max_upload_request_bytes,
    )
    app.add_middleware(CorrelationIdMiddleware)

    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(jobs.router, prefix=settings.api_prefix)
    app.include_router(approvals.router, prefix=settings.api_prefix)
    app.include_router(diagnostics.router, prefix=settings.api_prefix)
    app.include_router(assets.router, prefix=settings.api_prefix)
    app.include_router(discovery.router, prefix=settings.api_prefix)
    app.include_router(rendering.router, prefix=settings.api_prefix)
    app.include_router(quality.router, prefix=settings.api_prefix)
    app.include_router(publishing.router, prefix=settings.api_prefix)
    app.include_router(analytics.router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "name": settings.app_name,
            "version": settings.version,
            "phase": 7,
            "status": "analytics_experiments_bounded_learning",
            "health": "/healthz",
            "readiness": "/readyz",
        }

    return app


app = create_app()
