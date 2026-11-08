from __future__ import annotations

import asyncio
import signal

from clipos.config import get_settings
from clipos.database import Base, build_engine, build_session_factory
from clipos.gateways.analytics import build_analytics_adapters
from clipos.gateways.opportunities import build_opportunity_adapters
from clipos.gateways.publishing import build_publishing_adapters
from clipos.gateways.transcription import build_transcription_gateway
from clipos.media.tooling import MediaTooling
from clipos.queue import RedisQueueBroker
from clipos.services.analytics_processing import AnalyticsProcessor
from clipos.services.asset_processing import AssetProcessingService
from clipos.services.executive_processing import ExecutiveProcessor
from clipos.services.opportunity_processing import OpportunityProcessor
from clipos.services.publish_processing import PublishProcessor
from clipos.services.rendering import RenderProcessor
from clipos.services.workers import ClipOSWorker
from clipos.storage import LocalObjectStorage


async def run_worker() -> None:
    settings = get_settings()
    if settings.queue_backend == "memory":
        raise RuntimeError(
            "a standalone worker cannot share an in-memory queue; enable the embedded "
            "development worker or configure Redis"
        )
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    if settings.auto_create_schema:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    storage = LocalObjectStorage(settings.storage_local_root)
    queue = RedisQueueBroker(settings.redis_url.get_secret_value())
    transcription = build_transcription_gateway(settings)
    publishing_adapters = build_publishing_adapters(settings)
    analytics_adapters = build_analytics_adapters(settings)
    opportunity_adapters = build_opportunity_adapters(settings)
    media_tools = MediaTooling(settings)
    await media_tools.check()
    asset_processor = AssetProcessingService(
        session_factory=factory,
        storage=storage,
        media_tools=media_tools,
        transcription=transcription,
        settings=settings,
    )
    render_processor = RenderProcessor(
        session_factory=factory,
        storage=storage,
        media_tools=media_tools,
        settings=settings,
    )
    publish_processor = PublishProcessor(
        session_factory=factory,
        storage=storage,
        registry=publishing_adapters,
        settings=settings,
    )
    analytics_processor = AnalyticsProcessor(
        session_factory=factory,
        registry=analytics_adapters,
        settings=settings,
    )
    opportunity_processor = OpportunityProcessor(
        session_factory=factory,
        registry=opportunity_adapters,
        settings=settings,
    )
    executive_processor = ExecutiveProcessor(
        session_factory=factory,
        analytics_registry=analytics_adapters,
        opportunity_registry=opportunity_adapters,
        settings=settings,
    )
    worker = ClipOSWorker(
        asset_processor=asset_processor,
        render_processor=render_processor,
        publish_processor=publish_processor,
        analytics_processor=analytics_processor,
        opportunity_processor=opportunity_processor,
        executive_processor=executive_processor,
        session_factory=factory,
        queue=queue,
        settings=settings,
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, worker.stop)
    try:
        await worker.run()
    finally:
        await transcription.close()
        await publishing_adapters.close()
        await analytics_adapters.close()
        await opportunity_adapters.close()
        await queue.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
