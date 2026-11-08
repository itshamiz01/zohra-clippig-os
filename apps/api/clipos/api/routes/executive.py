from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_session, require_role
from clipos.core.enums import (
    ExecutiveControlMode,
    ExecutivePlanState,
    ExecutiveProposalState,
    Role,
)
from clipos.core.errors import ClipOSError
from clipos.core.security import Principal
from clipos.executive_schemas import (
    ActionSpecResponse,
    AgentDefinitionCreate,
    AgentDefinitionList,
    AgentDefinitionResponse,
    AgentDefinitionUpdate,
    AgentRunList,
    AgentRunResponse,
    ExecutiveControlResponse,
    ExecutiveControlUpdate,
    PlanCreate,
    PlanDetailResponse,
    PlanExecuteResponse,
    PlanList,
    PlannerRunRequest,
    PlanResponse,
    ProposalCreate,
    ProposalList,
    ProposalResponse,
    ProposalReview,
)
from clipos.services.executive import ExecutiveService
from clipos.services.executive_actions import ACTION_REGISTRY, ACTION_SCHEMA_VERSION

router = APIRouter(prefix="/executive", tags=["executive"])


def _require_executive_enabled(request: Request) -> None:
    if not request.app.state.settings.executive_enabled:
        raise ClipOSError(
            "executive_disabled",
            "the executive layer is disabled by configuration "
            "(CLIPOS_EXECUTIVE_ENABLED=false); planning and execution refuse",
            status_code=409,
        )


# ---------------------------------------------------------------------------
# Action registry (read-only)
# ---------------------------------------------------------------------------
@router.get("/actions", response_model=list[ActionSpecResponse])
async def list_action_registry(
    principal: Principal = Depends(get_principal),
) -> list[ActionSpecResponse]:
    del principal
    return [
        ActionSpecResponse(
            kind=spec.kind,
            autonomy_class=spec.autonomy_class,
            agent_role=spec.agent_role,
            cost_estimate_minor=spec.cost_estimate_minor,
            description=spec.description,
            schema_version=ACTION_SCHEMA_VERSION,
            payload_fields=sorted(spec.payload_model.model_fields),
        )
        for spec in ACTION_REGISTRY.values()
    ]


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------
@router.post(
    "/agents",
    response_model=AgentDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_definition(
    payload: AgentDefinitionCreate,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionResponse:
    definition = await ExecutiveService.create_agent_definition(
        session,
        principal=principal,
        name=payload.name,
        role=payload.role,
        contract=payload.contract,
        status=payload.status,
    )
    return AgentDefinitionResponse.model_validate(definition)


@router.get("/agents", response_model=AgentDefinitionList)
async def list_agent_definitions(
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionList:
    items = await ExecutiveService.list_agent_definitions(session, principal.tenant_id, limit=limit)
    return AgentDefinitionList(
        items=[AgentDefinitionResponse.model_validate(item) for item in items],
        total=len(items),
    )


@router.get("/agents/{definition_id}", response_model=AgentDefinitionResponse)
async def get_agent_definition(
    definition_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionResponse:
    definition = await ExecutiveService.get_agent_definition(
        session, principal.tenant_id, definition_id
    )
    return AgentDefinitionResponse.model_validate(definition)


@router.patch("/agents/{definition_id}", response_model=AgentDefinitionResponse)
async def update_agent_definition(
    definition_id: UUID,
    payload: AgentDefinitionUpdate,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionResponse:
    definition = await ExecutiveService.update_agent_definition(
        session,
        principal=principal,
        definition_id=definition_id,
        name=payload.name,
        contract=payload.contract,
        status=payload.status,
    )
    return AgentDefinitionResponse.model_validate(definition)


# ---------------------------------------------------------------------------
# Controls: kill switch + ceilings (owner only for writes)
# ---------------------------------------------------------------------------
@router.get("/controls", response_model=ExecutiveControlResponse)
async def get_controls(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ExecutiveControlResponse:
    control = await ExecutiveService.get_control(session, principal.tenant_id)
    return ExecutiveControlResponse.model_validate(control)


@router.put("/controls", response_model=ExecutiveControlResponse)
async def update_controls(
    payload: ExecutiveControlUpdate,
    principal: Principal = Depends(require_role(Role.OWNER)),
    session: AsyncSession = Depends(get_session),
) -> ExecutiveControlResponse:
    control = await ExecutiveService.update_control(
        session,
        principal=principal,
        changes=payload.model_dump(exclude_unset=True),
    )
    return ExecutiveControlResponse.model_validate(control)


@router.post("/controls/pause", response_model=ExecutiveControlResponse)
async def pause_executive(
    principal: Principal = Depends(require_role(Role.OWNER)),
    session: AsyncSession = Depends(get_session),
) -> ExecutiveControlResponse:
    control = await ExecutiveService.set_control_mode(
        session, principal=principal, mode=ExecutiveControlMode.PAUSED
    )
    return ExecutiveControlResponse.model_validate(control)


@router.post("/controls/resume", response_model=ExecutiveControlResponse)
async def resume_executive(
    principal: Principal = Depends(require_role(Role.OWNER)),
    session: AsyncSession = Depends(get_session),
) -> ExecutiveControlResponse:
    control = await ExecutiveService.set_control_mode(
        session, principal=principal, mode=ExecutiveControlMode.NORMAL
    )
    return ExecutiveControlResponse.model_validate(control)


# ---------------------------------------------------------------------------
# Planner and plans
# ---------------------------------------------------------------------------
@router.post(
    "/planner/run",
    response_model=PlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def run_planner(
    request: Request,
    payload: PlannerRunRequest | None = None,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PlanDetailResponse:
    del payload
    _require_executive_enabled(request)
    plan = await ExecutiveService.run_planner(session, principal=principal)
    return PlanDetailResponse.model_validate(plan)


@router.post(
    "/plans",
    response_model=PlanDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_plan(
    payload: PlanCreate,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PlanDetailResponse:
    _require_executive_enabled(request)
    plan = await ExecutiveService.create_plan(
        session,
        principal=principal,
        title=payload.title,
        objective=payload.objective,
        actions=[{"kind": action.kind, "payload": action.payload} for action in payload.actions],
    )
    return PlanDetailResponse.model_validate(plan)


@router.get("/plans", response_model=PlanList)
async def list_plans(
    state: ExecutivePlanState | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PlanList:
    plans = await ExecutiveService.list_plans(
        session, principal.tenant_id, status=state, limit=limit
    )
    return PlanList(items=[PlanResponse.model_validate(plan) for plan in plans], total=len(plans))


@router.get("/plans/{plan_id}", response_model=PlanDetailResponse)
async def get_plan(
    plan_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PlanDetailResponse:
    plan = await ExecutiveService.get_plan(session, principal.tenant_id, plan_id)
    return PlanDetailResponse.model_validate(plan)


@router.post("/plans/{plan_id}/submit", response_model=PlanDetailResponse)
async def submit_plan(
    plan_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PlanDetailResponse:
    _require_executive_enabled(request)
    plan = await ExecutiveService.submit_plan(session, principal=principal, plan_id=plan_id)
    return PlanDetailResponse.model_validate(plan)


@router.post(
    "/plans/{plan_id}/execute",
    response_model=PlanExecuteResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_plan(
    plan_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PlanExecuteResponse:
    _require_executive_enabled(request)
    plan, job = await ExecutiveService.queue_execution(
        session,
        principal=principal,
        plan_id=plan_id,
        queue=request.app.state.queue,
    )
    return PlanExecuteResponse(
        plan=PlanResponse.model_validate(plan), job_id=job.id, job_state=job.state.value
    )


@router.post("/plans/{plan_id}/cancel", response_model=PlanDetailResponse)
async def cancel_plan(
    plan_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PlanDetailResponse:
    plan = await ExecutiveService.cancel_plan(session, principal=principal, plan_id=plan_id)
    return PlanDetailResponse.model_validate(plan)


@router.get("/plans/{plan_id}/runs", response_model=AgentRunList)
async def list_plan_runs(
    plan_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentRunList:
    runs = await ExecutiveService.list_agent_runs(
        session, principal.tenant_id, plan_id, limit=limit
    )
    return AgentRunList(
        items=[AgentRunResponse.model_validate(run) for run in runs], total=len(runs)
    )


# ---------------------------------------------------------------------------
# Self-improvement proposals (decision records only)
# ---------------------------------------------------------------------------
@router.post(
    "/proposals",
    response_model=ProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    payload: ProposalCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ProposalResponse:
    proposal = await ExecutiveService.create_proposal(
        session,
        principal=principal,
        kind=payload.kind,
        title=payload.title,
        summary=payload.summary,
        change_payload=payload.change_payload,
        version=payload.version,
    )
    return ProposalResponse.model_validate(proposal)


@router.get("/proposals", response_model=ProposalList)
async def list_proposals(
    state: ExecutiveProposalState | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ProposalList:
    proposals = await ExecutiveService.list_proposals(
        session, principal.tenant_id, state=state, limit=limit
    )
    return ProposalList(
        items=[ProposalResponse.model_validate(item) for item in proposals],
        total=len(proposals),
    )


@router.get("/proposals/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(
    proposal_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ProposalResponse:
    proposal = await ExecutiveService.get_proposal(session, principal.tenant_id, proposal_id)
    return ProposalResponse.model_validate(proposal)


@router.post("/proposals/{proposal_id}/review", response_model=ProposalResponse)
async def review_proposal(
    proposal_id: UUID,
    payload: ProposalReview,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> ProposalResponse:
    proposal = await ExecutiveService.review_proposal(
        session,
        principal=principal,
        proposal_id=proposal_id,
        decision=ExecutiveProposalState(payload.decision),
        note=payload.note,
    )
    return ProposalResponse.model_validate(proposal)
