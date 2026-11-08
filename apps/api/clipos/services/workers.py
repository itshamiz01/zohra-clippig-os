from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import JobState
from clipos.models import BackgroundJob, SystemControl
from clipos.queue import QueueBroker, QueueMessage
from clipos.services.analytics import ANALYTICS_JOB_KIND
from clipos.services.analytics_processing import AnalyticsProcessor, AnalyticsWorker
from clipos.services.asset_processing import AssetProcessingService, AssetWorker
from clipos.services.publish_processing import PublishProcessor, PublishWorker
from clipos.services.publishing import PUBLISH_JOB_KIND
from clipos.services.rendering import RENDER_JOB_KIND, RenderProcessor, RenderWorker

logger = logging.getLogger(__name__)
_ASSET_JOB_KIND = "asset.process"


class ClipOSWorker:
    """Shared durable dispatcher for the single media pipeline and its job kinds."""

    def __init__(
        self,
        *,
        asset_processor: AssetProcessingService,
        render_processor: RenderProcessor,
        publish_processor: PublishProcessor,
        analytics_processor: AnalyticsProcessor,
        session_factory: async_sessionmaker[AsyncSession],
        queue: QueueBroker,
        settings: Settings,
    ) -> None:
        self.asset_processor = asset_processor
        self.render_processor = render_processor
        self.publish_processor = publish_processor
        self.analytics_processor = analytics_processor
        self.session_factory = session_factory
        self.queue = queue
        self.settings = settings
        self.asset_recovery = AssetWorker(
            processor=asset_processor,
            session_factory=session_factory,
            queue=queue,
            settings=settings,
        )
        self.render_recovery = RenderWorker(
            processor=render_processor,
            session_factory=session_factory,
            settings=settings,
        )
        self.publish_recovery = PublishWorker(
            processor=publish_processor,
            session_factory=session_factory,
            queue=queue,
            settings=settings,
        )
        self.analytics_recovery = AnalyticsWorker(
            processor=analytics_processor,
            session_factory=session_factory,
            queue=queue,
            settings=settings,
        )
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            message = await self.queue.dequeue(timeout_seconds=1)
            if message is None:
                message = await self.asset_recovery.recover_queued_job()
            if message is None:
                message = await self.render_recovery.recover_queued_job()
            if message is None:
                message = await self.publish_recovery.recover_queued_job()
            if message is None:
                message = await self.analytics_recovery.recover_queued_job()
            if message is None:
                await asyncio.sleep(self.settings.worker_poll_seconds)
                continue
            if await self._is_paused():
                await asyncio.sleep(self.settings.worker_poll_seconds)
                continue
            try:
                await self.process(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("media worker recovered after an unexpected job failure")

    async def process(self, message: QueueMessage) -> None:
        if message.kind == _ASSET_JOB_KIND:
            await self.asset_processor.process_job(message)
        elif message.kind == RENDER_JOB_KIND:
            await self.render_processor.process_job(message)
        elif message.kind == PUBLISH_JOB_KIND:
            await self.publish_processor.process_job(message)
        elif message.kind == ANALYTICS_JOB_KIND:
            await self.analytics_processor.process_job(message)
        else:
            await self._fail_unsupported(message)

    def stop(self) -> None:
        self._stop.set()
        self.asset_recovery.stop()

    async def _is_paused(self) -> bool:
        async with self.session_factory() as session:
            paused = await session.scalar(
                select(SystemControl.paused).where(SystemControl.key == "global")
            )
            return bool(paused)

    async def _fail_unsupported(self, message: QueueMessage) -> None:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == message.job_id,
                    BackgroundJob.tenant_id == message.tenant_id,
                    BackgroundJob.state == JobState.QUEUED,
                )
                .with_for_update()
            )
            if job is None:
                return
            now = datetime.now(UTC)
            job.state = JobState.FAILED
            job.failure_category = "unsupported_job_kind"
            job.failure_message = "no worker is registered for this internal job kind"
            job.finished_at = now
            job.heartbeat_at = now
            await session.commit()
