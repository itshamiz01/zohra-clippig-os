from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_session, require_role
from clipos.core.enums import OpportunityState, Role
from clipos.core.security import Principal
from clipos.models import Opportunity
from clipos.opportunity_schemas import (
    ContentDebtResponse,
    HandoffResponse,
    HandoffTransition,
    OpportunityCapabilityResponse,
    OpportunityDetailResponse,
    OpportunityList,
    OpportunityResponse,
    OpportunityTransition,
    SourceCreate,
    SourceList,
    SourceResponse,
    SourceUpdate,
    SuppressionCreate,
    SuppressionList,
    SuppressionResponse,
    SyncCreate,
    SyncRunList,
    SyncRunResponse,
)
from clipos.queue import QueueMessage
from clipos.services.opportunities import (
    OpportunityService,
    content_debt_for_row,
    opportunity_capabilities,
)

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


def _content_debt(item: Opportunity) -> ContentDebtResponse:
    return ContentDebtResponse.model_validate(asdict(content_debt_for_row(item)))


@router.get("/capabilities", response_model=list[OpportunityCapabilityResponse])
async def list_capabilities(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> list[OpportunityCapabilityResponse]:
    del principal
    return [
        OpportunityCapabilityResponse.model_validate(item)
        for item in opportunity_capabilities(request.app.state.opportunity_adapters)
    ]


@router.post(
    "/sources",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(
    payload: SourceCreate,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> SourceResponse:
    source = await OpportunityService.create_source(
        session,
        principal=principal,
        registry=request.app.state.opportunity_adapters,
        kind=payload.kind,
        name=payload.name,
        config=payload.config,
    )
    return SourceResponse.model_validate(source)


@router.get("/sources", response_model=SourceList)
async def list_sources(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> SourceList:
    sources = await OpportunityService.list_sources(
        session,
        principal.tenant_id,
        request.app.state.opportunity_adapters,
        limit=limit,
    )
    return SourceList(
        items=[SourceResponse.model_validate(item) for item in sources],
        total=len(sources),
    )


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> SourceResponse:
    source = await OpportunityService.get_source(
        session,
        principal.tenant_id,
        source_id,
        request.app.state.opportunity_adapters,
    )
    return SourceResponse.model_validate(source)


@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: UUID,
    payload: SourceUpdate,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> SourceResponse:
    source = await OpportunityService.update_source(
        session,
        principal=principal,
        registry=request.app.state.opportunity_adapters,
        source_id=source_id,
        name=payload.name,
        status=payload.status,
        config=payload.config,
    )
    return SourceResponse.model_validate(source)


@router.post(
    "/sources/{source_id}/syncs",
    response_model=SyncRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_sync(
    source_id: UUID,
    payload: SyncCreate,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> SyncRunResponse:
    run, job, created = await OpportunityService.queue_sync(
        session,
        principal=principal,
        source_id=source_id,
        idempotency_key=payload.idempotency_key,
        records=payload.records,
        max_import_records=request.app.state.settings.opportunity_max_import_records,
    )
    await session.commit()
    if created:
        await request.app.state.queue.enqueue(
            QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind)
        )
    await session.refresh(run)
    return SyncRunResponse.model_validate(run)


@router.get("/sync-runs", response_model=SyncRunList)
async def list_sync_runs(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> SyncRunList:
    runs = await OpportunityService.list_sync_runs(session, principal.tenant_id, limit=limit)
    return SyncRunList(
        items=[SyncRunResponse.model_validate(run) for run in runs],
        total=len(runs),
    )


@router.get("/sync-runs/{run_id}", response_model=SyncRunResponse)
async def get_sync_run(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> SyncRunResponse:
    run = await OpportunityService.get_sync_run(session, principal.tenant_id, run_id)
    return SyncRunResponse.model_validate(run)


@router.post(
    "/suppressions",
    response_model=SuppressionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_suppression(
    payload: SuppressionCreate,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> SuppressionResponse:
    suppression = await OpportunityService.create_suppression(
        session,
        principal=principal,
        match_type=payload.match_type,
        match_value=payload.match_value,
        reason=payload.reason,
    )
    return SuppressionResponse.model_validate(suppression)


@router.get("/suppressions", response_model=SuppressionList)
async def list_suppressions(
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> SuppressionList:
    suppressions = await OpportunityService.list_suppressions(
        session, principal.tenant_id, limit=limit
    )
    return SuppressionList(
        items=[SuppressionResponse.model_validate(item) for item in suppressions],
        total=len(suppressions),
    )


@router.delete("/suppressions/{suppression_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_suppression(
    suppression_id: UUID,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> None:
    await OpportunityService.delete_suppression(
        session, principal=principal, suppression_id=suppression_id
    )


@router.post(
    "/handoffs/{handoff_id}/transitions",
    response_model=HandoffResponse,
)
async def transition_handoff(
    handoff_id: UUID,
    payload: HandoffTransition,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> HandoffResponse:
    handoff = await OpportunityService.transition_handoff(
        session,
        principal=principal,
        handoff_id=handoff_id,
        target_state=payload.target_state,
    )
    return HandoffResponse.model_validate(handoff)


@router.get("", response_model=OpportunityList)
async def list_opportunities(
    state: OpportunityState | None = None,
    source_id: UUID | None = None,
    platform: str | None = Query(default=None, max_length=80),
    min_score: float | None = Query(default=None, ge=0, le=100),
    sort: str = Query(default="score", pattern="^(score|recent)$"),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> OpportunityList:
    opportunities = await OpportunityService.list_opportunities(
        session,
        principal.tenant_id,
        state=state,
        source_id=source_id,
        platform=platform,
        min_score=min_score,
        sort=sort,
        limit=limit,
    )
    items = []
    for item in opportunities:
        response = OpportunityResponse.model_validate(item)
        response.content_debt = _content_debt(item)
        items.append(response)
    return OpportunityList(items=items, total=len(items))


@router.get("/{opportunity_id}", response_model=OpportunityDetailResponse)
async def get_opportunity(
    opportunity_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> OpportunityDetailResponse:
    opportunity = await OpportunityService.get_opportunity(
        session, principal.tenant_id, opportunity_id
    )
    response = OpportunityDetailResponse.model_validate(opportunity)
    response.content_debt = _content_debt(opportunity)
    return response


@router.post("/{opportunity_id}/transitions", response_model=OpportunityResponse)
async def transition_opportunity(
    opportunity_id: UUID,
    payload: OpportunityTransition,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> OpportunityResponse:
    opportunity = await OpportunityService.transition_opportunity(
        session,
        principal=principal,
        opportunity_id=opportunity_id,
        target_state=payload.target_state,
    )
    return OpportunityResponse.model_validate(opportunity)


@router.post(
    "/{opportunity_id}/handoff",
    response_model=HandoffResponse,
    status_code=status.HTTP_201_CREATED,
)
async def build_handoff(
    opportunity_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> HandoffResponse:
    handoff = await OpportunityService.build_handoff(
        session, principal=principal, opportunity_id=opportunity_id
    )
    return HandoffResponse.model_validate(handoff)


@router.get("/{opportunity_id}/handoff", response_model=HandoffResponse)
async def get_opportunity_handoff(
    opportunity_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> HandoffResponse:
    handoff = await OpportunityService.get_handoff_for_opportunity(
        session, principal.tenant_id, opportunity_id
    )
    return HandoffResponse.model_validate(handoff)
