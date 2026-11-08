from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_session, require_role
from clipos.core.enums import CrmStage, Role, SalesAssetState
from clipos.core.security import Principal
from clipos.crm_schemas import (
    AccountCreate,
    AccountCreateResponse,
    AccountDetailResponse,
    AccountList,
    AccountResponse,
    AccountUpdate,
    ContactCreate,
    ContactCreateResponse,
    ContactList,
    ContactResponse,
    ContactUpdate,
    CrmCapabilityResponse,
    DraftCreate,
    DraftList,
    DraftResponse,
    DraftReview,
    IntakeResponse,
    MergeRequest,
    OnboardingResponse,
    OnboardingUpdate,
    PipelineEventResponse,
    RevenueCreate,
    RevenueList,
    RevenueResponse,
    RevenueSummaryResponse,
    StageTransition,
    SuppressContact,
    TaskCreate,
    TaskList,
    TaskResponse,
    TaskUpdate,
)
from clipos.models import CrmContact
from clipos.services.crm import CrmService, crm_capabilities

router = APIRouter(prefix="/crm", tags=["crm"])


@router.get("/capabilities", response_model=list[CrmCapabilityResponse])
async def list_capabilities(
    request: Request,
    principal: Principal = Depends(get_principal),
) -> list[CrmCapabilityResponse]:
    del principal
    return [
        CrmCapabilityResponse.model_validate(item)
        for item in crm_capabilities(
            request.app.state.comms_adapters, request.app.state.payments_adapters
        )
    ]


# ---------------------------------------------------------------------------
# Phase 8 handoff intake
# ---------------------------------------------------------------------------
@router.post(
    "/intake/{handoff_id}",
    response_model=IntakeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def intake_handoff(
    handoff_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> IntakeResponse:
    account, created = await CrmService.intake_handoff(
        session, principal=principal, handoff_id=handoff_id
    )
    return IntakeResponse(account=AccountResponse.model_validate(account), created=created)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------
@router.post(
    "/contacts",
    response_model=ContactCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact(
    payload: ContactCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ContactCreateResponse:
    contact, created = await CrmService.create_contact(
        session,
        principal=principal,
        display_name=payload.display_name,
        organisation=payload.organisation,
        role_title=payload.role_title,
        contact_route_label=payload.contact_route_label,
    )
    return ContactCreateResponse(contact=ContactResponse.model_validate(contact), created=created)


@router.get("/contacts", response_model=ContactList)
async def list_contacts(
    include_suppressed: bool = True,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ContactList:
    contacts = await CrmService.list_contacts(
        session,
        principal.tenant_id,
        include_suppressed=include_suppressed,
        limit=limit,
    )
    return ContactList(
        items=[ContactResponse.model_validate(item) for item in contacts],
        total=len(contacts),
    )


@router.get("/contacts/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ContactResponse:
    contact = await CrmService.get_contact(session, principal.tenant_id, contact_id)
    return ContactResponse.model_validate(contact)


@router.patch("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: UUID,
    payload: ContactUpdate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ContactResponse:
    contact = await CrmService.update_contact(
        session,
        principal=principal,
        contact_id=contact_id,
        display_name=payload.display_name,
        organisation=payload.organisation,
        role_title=payload.role_title,
        contact_route_label=payload.contact_route_label,
    )
    return ContactResponse.model_validate(contact)


@router.post("/contacts/{contact_id}/suppress", response_model=ContactResponse)
async def suppress_contact(
    contact_id: UUID,
    payload: SuppressContact,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> ContactResponse:
    contact = await CrmService.suppress_contact(
        session, principal=principal, contact_id=contact_id, reason=payload.reason
    )
    return ContactResponse.model_validate(contact)


@router.post("/contacts/{contact_id}/unsuppress", response_model=ContactResponse)
async def unsuppress_contact(
    contact_id: UUID,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> ContactResponse:
    contact = await CrmService.unsuppress_contact(
        session, principal=principal, contact_id=contact_id
    )
    return ContactResponse.model_validate(contact)


@router.post("/contacts/{contact_id}/merge", response_model=ContactResponse)
async def merge_contact(
    contact_id: UUID,
    payload: MergeRequest,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> ContactResponse:
    contact = await CrmService.merge_contacts(
        session,
        principal=principal,
        contact_id=contact_id,
        duplicate_id=payload.duplicate_id,
    )
    return ContactResponse.model_validate(contact)


# ---------------------------------------------------------------------------
# Accounts and pipeline
# ---------------------------------------------------------------------------
@router.post(
    "/accounts",
    response_model=AccountCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    payload: AccountCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> AccountCreateResponse:
    account, created = await CrmService.create_account(
        session,
        principal=principal,
        name=payload.name,
        owner_user_id=payload.owner_user_id,
        primary_contact_id=payload.primary_contact_id,
        next_action=payload.next_action,
        next_action_at=payload.next_action_at,
        notes_summary=payload.notes_summary,
    )
    return AccountCreateResponse(account=AccountResponse.model_validate(account), created=created)


@router.get("/accounts", response_model=AccountList)
async def list_accounts(
    stage: CrmStage | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AccountList:
    accounts = await CrmService.list_accounts(
        session, principal.tenant_id, stage=stage, limit=limit
    )
    return AccountList(
        items=[AccountResponse.model_validate(item) for item in accounts],
        total=len(accounts),
    )


@router.get("/accounts/{account_id}", response_model=AccountDetailResponse)
async def get_account(
    account_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AccountDetailResponse:
    account = await CrmService.get_account(session, principal.tenant_id, account_id)
    events = await CrmService.list_pipeline_events(session, principal.tenant_id, account_id)
    contact: CrmContact | None = None
    if account.primary_contact_id is not None:
        contact = await CrmService.get_contact(
            session, principal.tenant_id, account.primary_contact_id
        )
    base = AccountResponse.model_validate(account)
    return AccountDetailResponse(
        **base.model_dump(),
        primary_contact=(ContactResponse.model_validate(contact) if contact is not None else None),
        pipeline_events=[PipelineEventResponse.model_validate(event) for event in events],
    )


@router.patch("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: UUID,
    payload: AccountUpdate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> AccountResponse:
    account = await CrmService.update_account(
        session,
        principal=principal,
        account_id=account_id,
        name=payload.name,
        owner_user_id=payload.owner_user_id,
        primary_contact_id=payload.primary_contact_id,
        next_action=payload.next_action,
        next_action_at=payload.next_action_at,
        notes_summary=payload.notes_summary,
    )
    return AccountResponse.model_validate(account)


@router.post("/accounts/{account_id}/transitions", response_model=AccountResponse)
async def transition_account_stage(
    account_id: UUID,
    payload: StageTransition,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> AccountResponse:
    account = await CrmService.transition_stage(
        session,
        principal=principal,
        account_id=account_id,
        target_stage=payload.target_stage,
        reason=payload.reason,
    )
    return AccountResponse.model_validate(account)


@router.post("/accounts/{account_id}/merge", response_model=AccountResponse)
async def merge_account(
    account_id: UUID,
    payload: MergeRequest,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> AccountResponse:
    account = await CrmService.merge_accounts(
        session,
        principal=principal,
        account_id=account_id,
        duplicate_id=payload.duplicate_id,
    )
    return AccountResponse.model_validate(account)


# ---------------------------------------------------------------------------
# Drafts (draft-only: no route here can transmit anything)
# ---------------------------------------------------------------------------
@router.post(
    "/drafts",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(
    payload: DraftCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    draft = await CrmService.create_draft(
        session,
        principal=principal,
        account_id=payload.account_id,
        kind=payload.kind,
        contact_id=payload.contact_id,
        title=payload.title,
        body=payload.body,
    )
    return DraftResponse.model_validate(draft)


@router.get("/drafts", response_model=DraftList)
async def list_drafts(
    account_id: UUID | None = None,
    draft_status: SalesAssetState | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DraftList:
    drafts = await CrmService.list_drafts(
        session,
        principal.tenant_id,
        account_id=account_id,
        status=draft_status,
        limit=limit,
    )
    return DraftList(
        items=[DraftResponse.model_validate(item) for item in drafts],
        total=len(drafts),
    )


@router.get("/drafts/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    draft = await CrmService.get_draft(session, principal.tenant_id, draft_id)
    return DraftResponse.model_validate(draft)


@router.post("/drafts/{draft_id}/review", response_model=DraftResponse)
async def review_draft(
    draft_id: UUID,
    payload: DraftReview,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    draft = await CrmService.review_draft(
        session,
        principal=principal,
        draft_id=draft_id,
        target_state=payload.target_state,
        note=payload.note,
    )
    return DraftResponse.model_validate(draft)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@router.post(
    "/accounts/{account_id}/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    account_id: UUID,
    payload: TaskCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    task = await CrmService.create_task(
        session,
        principal=principal,
        account_id=account_id,
        title=payload.title,
        due_at=payload.due_at,
        assignee_user_id=payload.assignee_user_id,
    )
    return TaskResponse.model_validate(task)


@router.get("/accounts/{account_id}/tasks", response_model=TaskList)
async def list_tasks(
    account_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskList:
    tasks = await CrmService.list_tasks(session, principal.tenant_id, account_id, limit=limit)
    return TaskList(
        items=[TaskResponse.model_validate(item) for item in tasks],
        total=len(tasks),
    )


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    task = await CrmService.update_task(
        session,
        principal=principal,
        task_id=task_id,
        title=payload.title,
        due_at=payload.due_at,
        status=payload.status,
        assignee_user_id=payload.assignee_user_id,
    )
    return TaskResponse.model_validate(task)


# ---------------------------------------------------------------------------
# Revenue records
# ---------------------------------------------------------------------------
@router.post(
    "/accounts/{account_id}/revenue",
    response_model=RevenueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_revenue_record(
    account_id: UUID,
    payload: RevenueCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> RevenueResponse:
    record = await CrmService.create_revenue_record(
        session,
        principal=principal,
        account_id=account_id,
        kind=payload.kind,
        amount_minor_units=payload.amount_minor_units,
        currency=payload.currency,
        status=payload.status,
        external_reference=payload.external_reference,
        occurred_at=payload.occurred_at,
        notes=payload.notes,
    )
    return RevenueResponse.model_validate(record)


@router.get("/accounts/{account_id}/revenue", response_model=RevenueList)
async def list_revenue_records(
    account_id: UUID,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RevenueList:
    records = await CrmService.list_revenue_records(
        session, principal.tenant_id, account_id, limit=limit
    )
    return RevenueList(
        items=[RevenueResponse.model_validate(item) for item in records],
        total=len(records),
    )


@router.get("/accounts/{account_id}/revenue-summary", response_model=RevenueSummaryResponse)
async def get_revenue_summary(
    account_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RevenueSummaryResponse:
    summary = await CrmService.revenue_summary(session, principal.tenant_id, account_id)
    return RevenueSummaryResponse.model_validate(summary)


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------
@router.post(
    "/accounts/{account_id}/onboarding",
    response_model=OnboardingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_onboarding(
    account_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> OnboardingResponse:
    record = await CrmService.create_onboarding(session, principal=principal, account_id=account_id)
    return OnboardingResponse.model_validate(record)


@router.get("/accounts/{account_id}/onboarding", response_model=OnboardingResponse)
async def get_onboarding(
    account_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> OnboardingResponse:
    record = await CrmService.get_onboarding(session, principal.tenant_id, account_id)
    return OnboardingResponse.model_validate(record)


@router.patch("/onboarding/{record_id}", response_model=OnboardingResponse)
async def update_onboarding(
    record_id: UUID,
    payload: OnboardingUpdate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> OnboardingResponse:
    record = await CrmService.update_onboarding(
        session,
        principal=principal,
        record_id=record_id,
        item_states=payload.item_states,
        status=payload.status,
        milestone_label=payload.milestone_label,
    )
    return OnboardingResponse.model_validate(record)
