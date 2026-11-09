from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from clipos.core.enums import (
    CampaignAssessmentState,
    CampaignRightsBasis,
    CampaignSourceKind,
    CampaignStatus,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequirementInput(StrictSchema):
    kind: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    required: bool = True


class RequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    kind: str
    description: str
    required: bool


class CampaignCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=300)
    platform: str = Field(min_length=1, max_length=80)
    sponsor_label: str | None = Field(default=None, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)
    cpm_rate_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    max_payout_minor: int | None = Field(default=None, ge=0)
    budget_minor: int | None = Field(default=None, ge=0)
    minimum_views: int | None = Field(default=None, ge=0)
    deadline_at: datetime | None = None
    content_restrictions: str | None = Field(default=None, max_length=10000)
    account_requirements: str | None = Field(default=None, max_length=10000)
    rights_basis: CampaignRightsBasis = CampaignRightsBasis.UNKNOWN
    submission_process: str | None = Field(default=None, max_length=10000)
    expected_views: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=10000)
    crm_account_id: UUID | None = None
    requirements: list[RequirementInput] = Field(default_factory=list, max_length=50)
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=128)


class CampaignUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    sponsor_label: str | None = Field(default=None, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)
    cpm_rate_minor: int | None = Field(default=None, ge=0)
    max_payout_minor: int | None = Field(default=None, ge=0)
    budget_minor: int | None = Field(default=None, ge=0)
    minimum_views: int | None = Field(default=None, ge=0)
    deadline_at: datetime | None = None
    content_restrictions: str | None = Field(default=None, max_length=10000)
    account_requirements: str | None = Field(default=None, max_length=10000)
    rights_basis: CampaignRightsBasis | None = None
    submission_process: str | None = Field(default=None, max_length=10000)
    expected_views: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=10000)
    crm_account_id: UUID | None = None


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    created_by_user_id: UUID
    name: str
    platform: str
    sponsor_label: str | None
    source_kind: CampaignSourceKind
    source_reference: str | None
    cpm_rate_minor: int | None
    currency: str
    max_payout_minor: int | None
    budget_minor: int | None
    minimum_views: int | None
    deadline_at: datetime | None
    content_restrictions: str | None
    account_requirements: str | None
    rights_basis: CampaignRightsBasis
    submission_process: str | None
    status: CampaignStatus
    expected_views: int | None
    notes: str | None
    crm_account_id: UUID | None
    qualification_override_reason: str | None
    # Computed on read, never stored: true when the deadline has passed
    # and the campaign is not in a terminal state.
    expired: bool = False
    created_at: datetime
    updated_at: datetime


class AssessRequest(StrictSchema):
    """Per-assessment overrides; omitted inputs fall back to the
    recorded CLIPOS_CAMPAIGN_* defaults."""

    expected_views: int | None = Field(default=None, ge=0)
    editing_cost_minor: int | None = Field(default=None, ge=0)
    model_cost_minor: int | None = Field(default=None, ge=0)
    distribution_cost_minor: int | None = Field(default=None, ge=0)
    risk_percent: int | None = Field(default=None, ge=0, le=100)


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    created_by_user_id: UUID
    state: CampaignAssessmentState
    missing_inputs: list[str]
    expected_views: int | None
    cpm_rate_minor: int | None
    currency: str
    gross_revenue_minor: int | None
    capped_by_max_payout: bool
    editing_cost_minor: int
    model_cost_minor: int
    distribution_cost_minor: int
    risk_percent: int
    risk_adjustment_minor: int | None
    expected_profit_minor: int | None
    created_at: datetime


class AssessmentList(StrictSchema):
    items: list[AssessmentResponse]
    total: int


class CampaignDetailResponse(CampaignResponse):
    requirements: list[RequirementResponse] = Field(default_factory=list)
    publication_ids: list[UUID] = Field(default_factory=list)
    latest_assessment: AssessmentResponse | None = None


class CampaignList(StrictSchema):
    items: list[CampaignResponse]
    total: int


class CampaignTransition(StrictSchema):
    target_status: CampaignStatus
    # Required to qualify a campaign whose latest assessment is
    # unassessable (or absent); ignored for every other transition.
    override_reason: str | None = Field(default=None, min_length=1, max_length=1000)


class ImportRecord(StrictSchema):
    """One owner-supplied campaign listing for the seed import.

    Mirrors the manual create fields; ``source_reference`` carries the
    listing's provenance (URL or label) and doubles as the import
    dedup key within the tenant when present.
    """

    name: str = Field(min_length=1, max_length=300)
    platform: str = Field(min_length=1, max_length=80)
    sponsor_label: str | None = Field(default=None, max_length=300)
    source_reference: str | None = Field(default=None, max_length=1000)
    cpm_rate_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    max_payout_minor: int | None = Field(default=None, ge=0)
    budget_minor: int | None = Field(default=None, ge=0)
    minimum_views: int | None = Field(default=None, ge=0)
    deadline_at: datetime | None = None
    content_restrictions: str | None = Field(default=None, max_length=10000)
    account_requirements: str | None = Field(default=None, max_length=10000)
    rights_basis: CampaignRightsBasis = CampaignRightsBasis.UNKNOWN
    submission_process: str | None = Field(default=None, max_length=10000)
    expected_views: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=10000)
    requirements: list[RequirementInput] = Field(default_factory=list, max_length=50)


class CampaignImport(StrictSchema):
    idempotency_key: str = Field(min_length=16, max_length=128)
    records: list[ImportRecord] = Field(min_length=1, max_length=200)


class ImportSkipped(StrictSchema):
    index: int
    reason: str


class CampaignImportResponse(StrictSchema):
    created: list[UUID]
    skipped: list[ImportSkipped]
    replayed: bool = False


class LinkPublicationRequest(StrictSchema):
    publication_id: UUID


class CampaignPublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    campaign_id: UUID
    publication_id: UUID
    created_by_user_id: UUID
    created_at: datetime


class PayoutRequest(StrictSchema):
    amount_minor_units: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")
    # Defaults to the campaign's linked CRM account when omitted.
    account_id: UUID | None = None
    external_reference: str | None = Field(default=None, max_length=200)
    occurred_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=500)


class CampaignEconomics(StrictSchema):
    """Expected vs collected for one campaign. The two figures are
    different kinds of fact and are never combined: expected comes from
    the latest assessable profitability assessment, collected from
    paid ``campaign_payout`` revenue records only."""

    campaign_id: UUID
    name: str
    status: CampaignStatus
    expired: bool
    currency: str
    expected_profit_minor: int | None
    collected_minor: int
    payout_count: int
    linked_publications: int


class CampaignSummaryResponse(StrictSchema):
    counts_by_status: dict[str, int]
    expired_open: int
    campaigns: list[CampaignEconomics]
