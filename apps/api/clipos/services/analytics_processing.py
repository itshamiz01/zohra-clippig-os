from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import AnalyticsCollectionState, JobState
from clipos.gateways.analytics import AnalyticsAdapterRegistry
from clipos.models import AnalyticsCollectionRun, BackgroundJob
from clipos.queue import QueueBroker, QueueMessage
from clipos.services.analytics import ANALYTICS_JOB_KIND, AnalyticsService, job_state_for_run


class AnalyticsProcessor:
    """Executes queued analytics collection jobs.

    Collection is read-only against providers and idempotent (snapshot
    dedup), so claiming is a simple state transition: a job that is not
    QUEUED when the processor sees it has already been handled.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        registry: AnalyticsAdapterRegistry,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.settings = settings

    async def process_job(self, message: QueueMessage) -> None:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == message.job_id,
                    BackgroundJob.tenant_id == message.tenant_id,
                    BackgroundJob.kind == ANALYTICS_JOB_KIND,
                )
                .with_for_update()
            )
            if job is None or job.state != JobState.QUEUED:
                return
            run = await session.scalar(
                select(AnalyticsCollectionRun).where(
                    AnalyticsCollectionRun.background_job_id == job.id,
                    AnalyticsCollectionRun.tenant_id == message.tenant_id,
                )
            )
            if run is None:
                job.state = JobState.FAILED
                job.failure_category = "analytics_run_missing"
                job.failure_message = "analytics background job has no collection run record"
                job.finished_at = datetime.now(UTC)
                job.heartbeat_at = job.finished_at
                await session.commit()
                return
            job.state = JobState.RUNNING
            job.attempts += 1
            job.started_at = datetime.now(UTC)
            job.heartbeat_at = job.started_at
            await session.commit()

        async with self.session_factory() as session:
            run = await session.scalar(
                select(AnalyticsCollectionRun).where(
                    AnalyticsCollectionRun.id == run.id,
                    AnalyticsCollectionRun.tenant_id == message.tenant_id,
                )
            )
            job = await session.get(BackgroundJob, message.job_id)
            if run is None or job is None:
                return
            completed = await AnalyticsService.execute_collection(
                session, run=run, registry=self.registry
            )
            job.state = job_state_for_run(completed.state)
            job.result = {
                "collection_run_id": str(completed.id),
                "collection_state": completed.state.value,
                "adapter_mode": completed.adapter_mode.value,
                "publications_considered": completed.publications_considered,
                "snapshots_written": completed.snapshots_written,
                "metrics_reported": completed.metrics_reported,
                "metrics_unavailable": completed.metrics_unavailable,
            }
            job.failure_category = completed.failure_category
            job.failure_message = completed.failure_message
            job.finished_at = completed.finished_at or datetime.now(UTC)
            job.heartbeat_at = job.finished_at
            await session.commit()


class AnalyticsWorker:
    def __init__(
        self,
        *,
        processor: AnalyticsProcessor,
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
            row = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == ANALYTICS_JOB_KIND,
                    BackgroundJob.state == JobState.QUEUED,
                )
                .order_by(BackgroundJob.created_at)
                .limit(1)
            )
            if row is not None:
                return QueueMessage(job_id=row.id, tenant_id=row.tenant_id, kind=row.kind)
            # Collection is idempotent and read-only, so a stale RUNNING
            # job orphaned by a worker restart is safe to requeue — unlike
            # publishing, there is no uncertain remote side effect.
            stale_before = datetime.now(UTC) - timedelta(
                seconds=self.settings.worker_stale_job_seconds
            )
            stale = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == ANALYTICS_JOB_KIND,
                    BackgroundJob.state == JobState.RUNNING,
                    BackgroundJob.updated_at < stale_before,
                )
                .order_by(BackgroundJob.updated_at)
                .limit(1)
                .with_for_update()
            )
            if stale is None:
                return None
            stale.state = JobState.QUEUED
            stale.failure_category = "worker_restart_requeued"
            run = await session.scalar(
                select(AnalyticsCollectionRun).where(
                    AnalyticsCollectionRun.background_job_id == stale.id
                )
            )
            if run is not None and run.state == AnalyticsCollectionState.RUNNING:
                run.state = AnalyticsCollectionState.QUEUED
            await session.commit()
            return QueueMessage(job_id=stale.id, tenant_id=stale.tenant_id, kind=stale.kind)
