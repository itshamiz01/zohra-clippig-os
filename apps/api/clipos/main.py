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
    crm,
    diagnostics,
    discovery,
    executive,
    health,
    jobs,
    opportunities,
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
from clipos.gateways.comms import build_comms_adapters
from clipos.gateways.models import build_model_gateway
from clipos.gateways.opportunities import build_opportunity_adapters
from clipos.gateways.payments import build_payments_adapters
from clipos.gateways.publishing import build_publishing_adapters
from clipos.gateways.transcription import build_transcription_gateway
from clipos.media.tooling import MediaTooling
from clipos.models import SystemControl  # noqa: F401 - registers all model metadata
from clipos.queue import InMemoryQueueBroker, QueueBroker, RedisQueueBroker
from clipos.services.analytics_processing import AnalyticsProcessor
from clipos.services.asset_processing import AssetProcessingService
from clipos.services.executive_processing import ExecutiveProcessor
from clipos.services.opportunity_processing import OpportunityProcessor
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
        app.state.opportunity_adapters = build_opportunity_adapters(settings)
        app.state.comms_adapters = build_comms_adapters(settings)
        app.state.payments_adapters = build_payments_adapters(settings)
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
        opportunity_processor = OpportunityProcessor(
            session_factory=session_factory,
            registry=app.state.opportunity_adapters,
            settings=settings,
        )
        executive_processor = ExecutiveProcessor(
            session_factory=session_factory,
            analytics_registry=app.state.analytics_adapters,
            opportunity_registry=app.state.opportunity_adapters,
            settings=settings,
        )
        app.state.asset_processor = asset_processor
        app.state.render_processor = render_processor
        app.state.publish_processor = publish_processor
        app.state.analytics_processor = analytics_processor
        app.state.opportunity_processor = opportunity_processor
        app.state.executive_processor = executive_processor
        worker = ClipOSWorker(
            asset_processor=asset_processor,
            render_processor=render_processor,
            publish_processor=publish_processor,
            analytics_processor=analytics_processor,
            opportunity_processor=opportunity_processor,
            executive_processor=executive_processor,
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
        await app.state.opportunity_adapters.close()
        await app.state.comms_adapters.close()
        await app.state.payments_adapters.close()
        await queue.close()
        await rate_limiter.close()
        await engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=(
            "Safety-first Phase 10 media, sales and executive control plane with provider-neutral "
            "publishing contracts, "
            "encrypted OAuth credentials, a resumable YouTube adapter, private-first releases, "
            "mandatory fresh quality/rights gates, exact public-visibility approval, durable job "
            "control, and reconciliation-before-retry. Phase 7 adds truthful analytics "
            "collection (missing metrics are never invented zeros), experiments, and a bounded "
            "learning loop whose recommendations require human review and cannot mutate "
            "rights, quality, policy or budget state. Phase 8 adds opportunity hunting: "
            "tenant-scoped sources (operator seed lists, SSRF-guarded RSS, disabled search), "
            "conservative dedup with human-reviewed duplicate flags, suppressions, explainable "
            "commercial-fit scoring, and draft-only Phase 9 handoff packages. Phase 8 has no "
            "outreach surface: nothing contacts a creator. Phase 9 adds the sales assistant "
            "and CRM: idempotent intake from ready handoffs, a stage-validated pipeline with "
            "append-only history, conservative contact/account dedup with reviewed merges, "
            "opt-out suppression enforced at draft creation, evidence-cited draft sales "
            "assets with human review and no send path, tasks, onboarding, and revenue "
            "records whose kinds are reported strictly separately. Communications and "
            "payments gateways are disabled — no provider has been chosen — so nothing is "
            "sent and no money moves. Phase 10 adds the executive layer: registered agent "
            "contracts, a deterministic planner that cites the trusted state it consumed, "
            "and plan execution restricted to a closed registry of internal, reversible "
            "actions (recommendations, analytics collection, opportunity sync, CRM drafts "
            "and tasks, status reports), gated by the existing approvals system, per-tenant "
            "cost ceilings and a kill switch that is re-checked between actions, on top of "
            "the platform pause. Self-improvement proposals can be recorded and approved "
            "but never applied from inside the system. Unrelated providers remain disabled "
            "and real provider verification is reported separately from mocked evidence."
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
    app.include_router(opportunities.router, prefix=settings.api_prefix)
    app.include_router(crm.router, prefix=settings.api_prefix)
    app.include_router(executive.router, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "name": settings.app_name,
            "version": settings.version,
            "phase": 10,
            "status": "executive_internal_actions_only_no_external_surface",
            "health": "/healthz",
            "readiness": "/readyz",
        }

    return app


app = create_app()
