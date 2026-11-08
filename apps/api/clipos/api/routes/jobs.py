from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_session, require_role
from clipos.core.enums import JobState, Role
from clipos.core.errors import ClipOSError, ConflictError
from clipos.core.security import Principal
from clipos.models import RenderJob
from clipos.queue import QueueMessage
from clipos.schemas import JobCreate, JobResponse
from clipos.services.analytics import ANALYTICS_JOB_KIND
from clipos.services.jobs import JobService
from clipos.services.publishing import PUBLISH_JOB_KIND
from clipos.services.rendering import RENDER_JOB_KIND, RenderingService

router = APIRouter(prefix="/jobs", tags=["jobs"])

_EXTERNAL_ACTION_JOB_TERMS = {
    "publish",
    "publishing",
    "post",
    "upload",
    "delivery",
    "external",
    "outreach",
    "contract",
    "payment",
    "social",
}


def _looks_like_external_action(kind: str) -> bool:
    segments = {item for item in kind.replace("-", ".").replace("_", ".").split(".") if item}
    return bool(segments & _EXTERNAL_ACTION_JOB_TERMS)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    state: JobState | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[JobResponse]:
    jobs = await JobService.list(session, principal.tenant_id, state=state, limit=limit)
    return [JobResponse.model_validate(job) for job in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    return JobResponse.model_validate(await JobService.get(session, principal.tenant_id, job_id))


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_internal_job(
    payload: JobCreate,
    request: Request,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    if payload.kind in {"asset.process", RENDER_JOB_KIND, PUBLISH_JOB_KIND, ANALYTICS_JOB_KIND}:
        raise ClipOSError(
            "reserved_job_kind",
            "media job kinds can be created only through their validated domain endpoints",
            status_code=422,
        )
    if _looks_like_external_action(payload.kind):
        raise ClipOSError(
            "external_action_unavailable",
            "publishing jobs must use the gated Phase 6 publishing domain endpoint; outreach, "
            "contracts, and payments remain unavailable, and generic jobs cannot bypass controls",
            status_code=422,
        )
    job, created = await JobService.create(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        kind=payload.kind,
        idempotency_key=payload.idempotency_key,
        payload=payload.payload,
        max_attempts=payload.max_attempts,
    )
    if created:
        await request.app.state.queue.enqueue(
            QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)
        )
    return JobResponse.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    job = await JobService.get(session, principal.tenant_id, job_id)
    if job.kind == PUBLISH_JOB_KIND:
        raise ClipOSError(
            "publishing_job_domain_endpoint_required",
            "publication jobs can be cancelled only through the publishing endpoint",
            status_code=409,
        )
    if job.kind == ANALYTICS_JOB_KIND:
        raise ClipOSError(
            "analytics_job_domain_endpoint_required",
            "analytics collection jobs are managed through the analytics endpoint; "
            "collection is short and read-only, so trigger a new collection instead",
            status_code=409,
        )
    if job.kind == RENDER_JOB_KIND:
        render_job_id = await session.scalar(
            select(RenderJob.id).where(
                RenderJob.background_job_id == job.id,
                RenderJob.tenant_id == principal.tenant_id,
            )
        )
        if render_job_id is None:
            raise ConflictError("render background job has no render record")
        await RenderingService.cancel(session, principal=principal, render_job_id=render_job_id)
        job = await JobService.get(session, principal.tenant_id, job_id)
    else:
        job = await JobService.transition(
            session,
            tenant_id=principal.tenant_id,
            job_id=job_id,
            target=JobState.CANCELLED,
            actor_user_id=principal.user_id,
        )
    return JobResponse.model_validate(job)


@router.post("/{job_id}/retry", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def retry_job(
    job_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    job = await JobService.get(session, principal.tenant_id, job_id)
    if job.kind == PUBLISH_JOB_KIND:
        raise ClipOSError(
            "publishing_job_domain_endpoint_required",
            "publication jobs can be retried only through the publishing endpoint",
            status_code=409,
        )
    if job.kind == ANALYTICS_JOB_KIND:
        raise ClipOSError(
            "analytics_job_domain_endpoint_required",
            "analytics collection jobs are retried by triggering a new collection "
            "through the analytics endpoint",
            status_code=409,
        )
    if job.kind == RENDER_JOB_KIND:
        render_job_id = await session.scalar(
            select(RenderJob.id).where(
                RenderJob.background_job_id == job.id,
                RenderJob.tenant_id == principal.tenant_id,
            )
        )
        if render_job_id is None:
            raise ConflictError("render background job has no render record")
        await RenderingService.retry(
            session,
            principal=principal,
            render_job_id=render_job_id,
            queue=request.app.state.queue,
        )
        job = await JobService.get(session, principal.tenant_id, job_id)
    else:
        job = await JobService.retry(
            session,
            tenant_id=principal.tenant_id,
            job_id=job_id,
            actor_user_id=principal.user_id,
        )
        await request.app.state.queue.enqueue(
            QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)
        )
    return JobResponse.model_validate(job)
