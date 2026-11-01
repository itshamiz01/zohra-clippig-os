from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.core.enums import AuditOutcome, JobState
from clipos.core.errors import ConflictError, NotFoundError
from clipos.core.logging import correlation_id_var
from clipos.models import BackgroundJob
from clipos.services.audit import AuditService

_ALLOWED_TRANSITIONS: dict[JobState, set[JobState]] = {
    JobState.QUEUED: {JobState.RUNNING, JobState.CANCELLED, JobState.FAILED, JobState.NEEDS_REVIEW},
    JobState.RUNNING: {
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.NEEDS_REVIEW,
    },
    JobState.FAILED: {JobState.QUEUED, JobState.NEEDS_REVIEW},
    JobState.NEEDS_REVIEW: {JobState.QUEUED, JobState.CANCELLED},
    JobState.SUCCEEDED: set(),
    JobState.CANCELLED: set(),
}


class JobService:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        actor_user_id: UUID,
        kind: str,
        idempotency_key: str,
        payload: dict[str, Any],
        max_attempts: int,
    ) -> tuple[BackgroundJob, bool]:
        existing = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.tenant_id == tenant_id,
                BackgroundJob.kind == kind,
                BackgroundJob.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing, False
        job = BackgroundJob(
            tenant_id=tenant_id,
            kind=kind,
            idempotency_key=idempotency_key,
            payload=payload,
            max_attempts=max_attempts,
            correlation_id=correlation_id_var.get(),
        )
        session.add(job)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            raced = await session.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.tenant_id == tenant_id,
                    BackgroundJob.kind == kind,
                    BackgroundJob.idempotency_key == idempotency_key,
                )
            )
            if raced is not None:
                return raced, False
            raise
        AuditService.record(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action="job.created",
            resource_type="background_job",
            resource_id=str(job.id),
            event_data={"kind": kind, "idempotency_key": idempotency_key},
        )
        await session.commit()
        await session.refresh(job)
        return job, True

    @staticmethod
    async def get(session: AsyncSession, tenant_id: UUID, job_id: UUID) -> BackgroundJob:
        job = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id, BackgroundJob.tenant_id == tenant_id
            )
        )
        if job is None:
            raise NotFoundError("job")
        return job

    @staticmethod
    async def list(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        state: JobState | None = None,
        limit: int = 100,
    ) -> list[BackgroundJob]:
        query = select(BackgroundJob).where(BackgroundJob.tenant_id == tenant_id)
        if state:
            query = query.where(BackgroundJob.state == state)
        query = query.order_by(desc(BackgroundJob.created_at)).limit(min(limit, 200))
        return list((await session.scalars(query)).all())

    @staticmethod
    async def transition(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        job_id: UUID,
        target: JobState,
        actor_user_id: UUID | None,
        result: dict[str, Any] | None = None,
        failure_category: str | None = None,
        failure_message: str | None = None,
    ) -> BackgroundJob:
        job = await session.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.id == job_id, BackgroundJob.tenant_id == tenant_id)
            .with_for_update()
        )
        if job is None:
            raise NotFoundError("job")
        source = job.state
        if target not in _ALLOWED_TRANSITIONS[source]:
            AuditService.record(
                session,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                action="job.transition_denied",
                resource_type="background_job",
                resource_id=str(job.id),
                outcome=AuditOutcome.DENIED,
                event_data={"from": source.value, "to": target.value},
            )
            await session.commit()
            raise ConflictError(f"job cannot transition from {source.value} to {target.value}")
        if target == JobState.RUNNING and job.attempts >= job.max_attempts:
            raise ConflictError("job retry budget exhausted")
        now = datetime.now(UTC)
        job.state = target
        if target == JobState.RUNNING:
            job.attempts += 1
            job.started_at = now
            job.heartbeat_at = now
            job.finished_at = None
        if target in {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
            JobState.NEEDS_REVIEW,
        }:
            job.finished_at = now
            job.heartbeat_at = now
        if target == JobState.SUCCEEDED:
            job.result = result or {}
            job.failure_category = None
            job.failure_message = None
        elif target == JobState.FAILED:
            job.failure_category = (failure_category or "unknown")[:100]
            job.failure_message = (failure_message or "job failed")[:500]
        elif target == JobState.QUEUED:
            job.finished_at = None
            job.heartbeat_at = None
            job.failure_category = None
            job.failure_message = None
        AuditService.record(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action="job.transitioned",
            resource_type="background_job",
            resource_id=str(job.id),
            event_data={"from": source.value, "to": target.value},
        )
        await session.commit()
        await session.refresh(job)
        return job

    @staticmethod
    async def retry(
        session: AsyncSession, *, tenant_id: UUID, job_id: UUID, actor_user_id: UUID
    ) -> BackgroundJob:
        job = await JobService.get(session, tenant_id, job_id)
        if job.state not in {JobState.FAILED, JobState.NEEDS_REVIEW}:
            raise ConflictError("only failed or needs-review jobs can be retried")
        if job.attempts >= job.max_attempts:
            raise ConflictError("job retry budget exhausted")
        return await JobService.transition(
            session,
            tenant_id=tenant_id,
            job_id=job_id,
            target=JobState.QUEUED,
            actor_user_id=actor_user_id,
        )
