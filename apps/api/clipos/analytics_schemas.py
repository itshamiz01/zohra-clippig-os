from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from clipos.core.enums import (
    AnalyticsCollectionState,
    ExperimentState,
    MetricAvailability,
    ProviderMode,
    PublishingProvider,
    RecommendationKind,
    RecommendationState,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyticsCapabilityResponse(StrictSchema):
    provider: PublishingProvider
    mode: ProviderMode
    supported_metrics: list[str]
    limitations: list[str]
    source: str


class CollectionCreate(StrictSchema):
    provider: PublishingProvider | None = None
    idempotency_key: str = Field(min_length=16, max_length=128)


class CollectionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    requested_by_user_id: UUID | None
    background_job_id: UUID | None
    provider: PublishingProvider | None
    state: AnalyticsCollectionState
    adapter_mode: ProviderMode
    publications_considered: int
    snapshots_written: int
    metrics_reported: int
    metrics_unavailable: int
    failure_category: str | None
    failure_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CollectionRunList(StrictSchema):
    items: list[CollectionRunResponse]
    total: int


class MetricSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    publication_id: UUID
    collection_run_id: UUID | None
    provider: PublishingProvider
    provider_publication_id: str
    metric: str
    value: float | None
    unit: str
    availability: MetricAvailability
    window_start: datetime
    window_end: datetime
    source: str
    metric_definition_version: str
    fetched_at: datetime


class PublicationPerformanceResponse(StrictSchema):
    publication_id: UUID
    provider: PublishingProvider
    provider_publication_id: str | None
    provider_url: str | None
    campaign_reference: str | None
    title: str
    metrics: list[MetricSnapshotResponse]
    availability_counts: dict[str, int]


class PublicationPerformanceSummary(StrictSchema):
    publication_id: UUID
    provider: PublishingProvider
    provider_publication_id: str | None
    campaign_reference: str | None
    title: str
    views: float | None
    views_availability: MetricAvailability | None
    metrics: list[MetricSnapshotResponse]


class PerformanceList(StrictSchema):
    items: list[PublicationPerformanceSummary]
    total: int


class CampaignMetricAggregate(StrictSchema):
    metric: str
    unit: str
    aggregation: str
    value: float | None
    reported_publications: int
    unavailable_publications: int


class CampaignSummaryResponse(StrictSchema):
    campaign_reference: str
    publication_count: int
    publication_ids: list[UUID]
    metrics: list[CampaignMetricAggregate]
    gaps: list[str] = Field(default_factory=list)


class CostBreakdownEntry(StrictSchema):
    provider: str
    model: str
    operation: str
    event_count: int
    succeeded: int
    failed: int
    estimated_cost: Decimal
    currency: str


class CostSummaryResponse(StrictSchema):
    total_estimated_cost: Decimal
    currency: str
    event_count: int
    succeeded: int
    failed: int
    publication_count: int
    cost_per_publication: Decimal | None
    by_provider_operation: list[CostBreakdownEntry]
    attribution_note: str


class ScoreComponentResponse(StrictSchema):
    key: str
    score: float | None
    weight: float
    weighted_score: float | None
    applicable: bool
    algorithm_version: str


class PredictedVsActualResponse(StrictSchema):
    publication_id: UUID
    candidate_id: UUID | None
    predicted_overall_score: float | None
    score_components: list[ScoreComponentResponse]
    actual_metrics: list[MetricSnapshotResponse]
    lineage_complete: bool
    gaps: list[str]


class ExperimentCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(min_length=1, max_length=5000)
    success_metric: str = Field(min_length=1, max_length=80)
    eligibility: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    notes: str = Field(default="", max_length=5000)


class ExperimentTransition(StrictSchema):
    target_state: ExperimentState


class ExperimentVariantCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=5000)
    candidate_id: UUID | None = None
    publication_id: UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ExperimentVariantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    experiment_id: UUID
    name: str
    description: str
    candidate_id: UUID | None
    publication_id: UUID | None
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ExperimentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    created_by_user_id: UUID
    name: str
    hypothesis: str
    state: ExperimentState
    eligibility: dict[str, Any]
    success_metric: str
    success_criteria: dict[str, Any]
    starts_at: datetime | None
    ends_at: datetime | None
    notes: str
    created_at: datetime
    updated_at: datetime
    variants: list[ExperimentVariantResponse] = Field(default_factory=list)


class ExperimentList(StrictSchema):
    items: list[ExperimentResponse]
    total: int


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    kind: RecommendationKind
    state: RecommendationState
    title: str
    rationale: str
    evidence: dict[str, Any]
    algorithm_version: str
    created_by: str
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime


class RecommendationList(StrictSchema):
    items: list[RecommendationResponse]
    total: int


class RecommendationReview(StrictSchema):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=1000)
