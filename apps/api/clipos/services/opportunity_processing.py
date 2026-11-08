from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import JobState, OpportunitySyncState
from clipos.gateways.opportunities import OpportunityAdapterRegistry
from clipos.models import BackgroundJob, OpportunitySyncRun
from clipos.queue import QueueBroker, QueueMessage
from clipos.services.opportunities import (
    OPPORTUNITY_JOB_KIND,
    OpportunityService,
    job_state_for_sync,
)


class OpportunityProcessor:
    """Executes queued opportunity sync jobs.

    Sync is idempotent — exact matches merge, suppressions are
    re-checked, and scores are recomputed deterministically — so claiming
    is a simple state transition: a job that is not QUEUED when the
    processor sees it has already been handled.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        registry: OpportunityAdapterRegistry,
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
                    BackgroundJob.kind == OPPORTUNITY_JOB_KIND,
                )
                .with_for_update()
            )
            if job is None or job.state != JobState.QUEUED:
                return
            run = await session.scalar(
                select(OpportunitySyncRun).where(
                    OpportunitySyncRun.background_job_id == job.id,
                    OpportunitySyncRun.tenant_id == message.tenant_id,
                )
            )
            if run is None:
                job.state = JobState.FAILED
                job.failure_category = "opportunity_run_missing"
                job.failure_message = "opportunity background job has no sync run record"
                job.finished_at = datetime.now(UTC)
                job.heartbeat_at = job.finished_at
                await session.commit()
                return
            job.state = JobState.RUNNING
            job.attempts += 1
            job.started_at = datetime.now(UTC)
            job.heartbeat_at = job.started_at
            payload = dict(job.payload or {})
            await session.commit()

        records = payload.get("records")
        import_payload: list[dict[str, Any]] | None = records if isinstance(records, list) else None
        async with self.session_factory() as session:
            run = await session.scalar(
                select(OpportunitySyncRun).where(
                    OpportunitySyncRun.id == run.id,
                    OpportunitySyncRun.tenant_id == message.tenant_id,
                )
            )
            job = await session.get(BackgroundJob, message.job_id)
            if run is None or job is None:
                return
            completed = await OpportunityService.execute_sync(
                session, run=run, registry=self.registry, import_payload=import_payload
            )
            job.state = job_state_for_sync(completed.state)
            job.result = {
                "sync_run_id": str(completed.id),
                "sync_state": completed.state.value,
                "adapter_mode": completed.adapter_mode.value,
                "records_received": completed.records_received,
                "records_created": completed.records_created,
                "records_merged": completed.records_merged,
                "duplicates_flagged": completed.duplicates_flagged,
                "suppressed_skipped": completed.suppressed_skipped,
            }
            job.failure_category = completed.failure_category
            job.failure_message = completed.failure_message
            job.finished_at = completed.finished_at or datetime.now(UTC)
            job.heartbeat_at = job.finished_at
            await session.commit()


class OpportunityWorker:
    def __init__(
        self,
        *,
        processor: OpportunityProcessor,
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
                    BackgroundJob.kind == OPPORTUNITY_JOB_KIND,
                    BackgroundJob.state == JobState.QUEUED,
                )
                .order_by(BackgroundJob.created_at)
                .limit(1)
            )
            if row is not None:
                return QueueMessage(job_id=row.id, tenant_id=row.tenant_id, kind=row.kind)
            # Sync is idempotent (exact-match merge, suppression re-check,
            # deterministic rescoring), so a stale RUNNING job orphaned
            # by a worker restart is safe to requeue — there is no
            # uncertain remote side effect to reconcile first.
            stale_before = datetime.now(UTC) - timedelta(
                seconds=self.settings.worker_stale_job_seconds
            )
            stale = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == OPPORTUNITY_JOB_KIND,
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
                select(OpportunitySyncRun).where(OpportunitySyncRun.background_job_id == stale.id)
            )
            if run is not None and run.state == OpportunitySyncState.RUNNING:
                run.state = OpportunitySyncState.QUEUED
            await session.commit()
            return QueueMessage(job_id=stale.id, tenant_id=stale.tenant_id, kind=stale.kind)
