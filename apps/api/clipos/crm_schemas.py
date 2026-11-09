from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from clipos.core.enums import (
    CrmStage,
    CrmTaskState,
    OnboardingState,
    ProviderMode,
    RevenueKind,
    RevenueState,
    SalesAssetKind,
    SalesAssetState,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CrmCapabilityResponse(StrictSchema):
    domain: str
    provider: str
    mode: ProviderMode
    source: str
    channels: list[str]
    limitations: list[str]


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------
class ContactCreate(StrictSchema):
    display_name: str = Field(min_length=1, max_length=300)
    organisation: str | None = Field(default=None, max_length=300)
    role_title: str | None = Field(default=None, max_length=200)
    contact_route_label: str | None = Field(default=None, max_length=300)


class ContactUpdate(StrictSchema):
    display_name: str | None = Field(default=None, min_length=1, max_length=300)
    organisation: str | None = Field(default=None, max_length=300)
    role_title: str | None = Field(default=None, max_length=200)
    contact_route_label: str | None = Field(default=None, max_length=300)


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    display_name: str
    organisation: str | None
    role_title: str | None
    contact_route_label: str | None
    source_opportunity_id: UUID | None
    dedup_key: str
    possible_duplicate_of_id: UUID | None
    suppressed: bool
    suppression_reason: str | None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class ContactList(StrictSchema):
    items: list[ContactResponse]
    total: int


class ContactCreateResponse(StrictSchema):
    contact: ContactResponse
    created: bool


class SuppressContact(StrictSchema):
    reason: str = Field(min_length=1, max_length=500)


class MergeRequest(StrictSchema):
    duplicate_id: UUID


# ---------------------------------------------------------------------------
# Accounts and pipeline
# ---------------------------------------------------------------------------
class AccountCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=300)
    owner_user_id: UUID | None = None
    primary_contact_id: UUID | None = None
    next_action: str | None = Field(default=None, max_length=500)
    next_action_at: datetime | None = None
    notes_summary: str | None = None


class AccountUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    owner_user_id: UUID | None = None
    primary_contact_id: UUID | None = None
    next_action: str | None = Field(default=None, max_length=500)
    next_action_at: datetime | None = None
    notes_summary: str | None = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    stage: CrmStage
    owner_user_id: UUID | None
    primary_contact_id: UUID | None
    source_opportunity_id: UUID | None
    source_handoff_id: UUID | None
    dedup_key: str
    possible_duplicate_of_id: UUID | None
    next_action: str | None
    next_action_at: datetime | None
    notes_summary: str | None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class AccountList(StrictSchema):
    items: list[AccountResponse]
    total: int


class AccountCreateResponse(StrictSchema):
    account: AccountResponse
    created: bool


class IntakeResponse(StrictSchema):
    account: AccountResponse
    created: bool


class StageTransition(StrictSchema):
    target_stage: CrmStage
    reason: str | None = Field(default=None, max_length=500)


class PipelineEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    account_id: UUID | None
    contact_id: UUID | None
    event_kind: str
    from_stage: CrmStage | None
    to_stage: CrmStage | None
    actor_user_id: UUID | None
    reason: str | None
    created_at: datetime


class AccountDetailResponse(AccountResponse):
    primary_contact: ContactResponse | None = None
    pipeline_events: list[PipelineEventResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------
class DraftCreate(StrictSchema):
    account_id: UUID
    kind: SalesAssetKind
    contact_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1)


class DraftReview(StrictSchema):
    target_state: SalesAssetState
    note: str | None = Field(default=None, max_length=1000)


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    account_id: UUID
    contact_id: UUID | None
    kind: SalesAssetKind
    title: str
    body: str
    evidence: dict[str, Any]
    status: SalesAssetState
    template_version: str
    created_by: str
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime


class DraftList(StrictSchema):
    items: list[DraftResponse]
    total: int


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
class TaskCreate(StrictSchema):
    title: str = Field(min_length=1, max_length=300)
    due_at: datetime | None = None
    assignee_user_id: UUID | None = None


class TaskUpdate(StrictSchema):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due_at: datetime | None = None
    status: CrmTaskState | None = None
    assignee_user_id: UUID | None = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    account_id: UUID
    title: str
    due_at: datetime | None
    status: CrmTaskState
    assignee_user_id: UUID | None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class TaskList(StrictSchema):
    items: list[TaskResponse]
    total: int


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------
class RevenueCreate(StrictSchema):
    kind: RevenueKind
    amount_minor_units: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    status: RevenueState = RevenueState.DRAFT
    external_reference: str | None = Field(default=None, max_length=200)
    occurred_at: datetime
    notes: str | None = Field(default=None, max_length=500)


class RevenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    account_id: UUID
    kind: RevenueKind
    amount_minor_units: int
    currency: str
    status: RevenueState
    external_reference: str | None
    occurred_at: datetime
    notes: str | None
    campaign_id: UUID | None = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class RevenueList(StrictSchema):
    items: list[RevenueResponse]
    total: int


class RevenueCurrencyTotals(StrictSchema):
    currency: str
    quoted: int
    contracted: int
    invoiced: int
    collected: int
    refunded: int
    recurring: int
    campaign_payout: int = 0


class RevenueSummaryResponse(StrictSchema):
    account_id: UUID
    currencies: list[RevenueCurrencyTotals]
    definitions: dict[str, str]


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------
class OnboardingUpdate(StrictSchema):
    item_states: dict[str, bool] | None = None
    status: OnboardingState | None = None
    milestone_label: str | None = Field(default=None, min_length=1, max_length=300)


class OnboardingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    account_id: UUID
    checklist: dict[str, Any]
    milestones: list[Any]
    status: OnboardingState
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
