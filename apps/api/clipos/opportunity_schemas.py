from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from clipos.core.enums import (
    OpportunityHandoffState,
    OpportunitySourceKind,
    OpportunitySourceStatus,
    OpportunityState,
    OpportunitySyncState,
    ProviderMode,
    SuppressionMatchType,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpportunityCapabilityResponse(StrictSchema):
    kind: OpportunitySourceKind
    mode: ProviderMode
    limitations: list[str]
    source: str


class SourceCreate(StrictSchema):
    kind: OpportunitySourceKind
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: OpportunitySourceStatus | None = None
    config: dict[str, Any] | None = None


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    created_by_user_id: UUID
    kind: OpportunitySourceKind
    name: str
    config: dict[str, Any]
    status: OpportunitySourceStatus
    adapter_mode: ProviderMode
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SourceList(StrictSchema):
    items: list[SourceResponse]
    total: int


class SyncCreate(StrictSchema):
    idempotency_key: str = Field(min_length=16, max_length=128)
    records: list[dict[str, Any]] | None = Field(default=None, max_length=5000)


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    source_id: UUID
    requested_by_user_id: UUID | None
    background_job_id: UUID | None
    state: OpportunitySyncState
    adapter_mode: ProviderMode
    records_received: int
    records_created: int
    records_merged: int
    duplicates_flagged: int
    suppressed_skipped: int
    failure_category: str | None
    failure_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SyncRunList(StrictSchema):
    items: list[SyncRunResponse]
    total: int


class ScoreComponentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    meaning: str
    score: float | None
    weight: float
    weighted_score: float
    applicable: bool
    evidence: list[str]
    missing_behavior: str
    scoring_version: str


class OpportunityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    source_id: UUID
    sync_run_id: UUID | None
    external_key: str
    title: str
    creator_name: str | None
    creator_handle: str | None
    canonical_url: str | None
    platform: str | None
    contact_route_label: str | None
    niche: str | None
    language: str | None
    audience_band: str | None
    posting_cadence: str | None
    state: OpportunityState
    overall_score: float | None
    score_version: str
    possible_duplicate_of_id: UUID | None
    created_at: datetime
    updated_at: datetime


class OpportunityDetailResponse(OpportunityResponse):
    evidence: dict[str, Any]
    score_components: list[ScoreComponentResponse]


class OpportunityList(StrictSchema):
    items: list[OpportunityResponse]
    total: int


class OpportunityTransition(StrictSchema):
    target_state: OpportunityState


class SuppressionCreate(StrictSchema):
    match_type: SuppressionMatchType
    match_value: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)


class SuppressionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    match_type: SuppressionMatchType
    match_value: str
    reason: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class SuppressionList(StrictSchema):
    items: list[SuppressionResponse]
    total: int


class HandoffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    opportunity_id: UUID
    payload: dict[str, Any]
    payload_version: str
    state: OpportunityHandoffState
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class HandoffTransition(StrictSchema):
    target_state: OpportunityHandoffState
