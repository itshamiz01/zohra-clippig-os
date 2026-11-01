from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from clipos.core.enums import (
    CandidateReviewAction,
    CandidateState,
    DiscoveryRunState,
    DiscoveryStrategy,
    ProviderMode,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoveryRequest(StrictSchema):
    strategy: DiscoveryStrategy = DiscoveryStrategy.DETERMINISTIC
    target_profile: str = Field(default="general", min_length=1, max_length=40)
    min_duration_seconds: float | None = Field(default=None, ge=3, le=300)
    target_duration_seconds: float | None = Field(default=None, ge=3, le=300)
    max_duration_seconds: float | None = Field(default=None, ge=5, le=600)
    padding_seconds: float | None = Field(default=None, ge=0, le=10)
    max_candidates: int | None = Field(default=None, ge=1, le=50)
    audience_hint: str | None = Field(default=None, max_length=240)
    topic_hints: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=12
    )

    @field_validator("target_profile")
    @classmethod
    def normalize_target_profile(cls, value: str) -> str:
        normalized = " ".join(value.casefold().split())
        if not normalized:
            raise ValueError("target_profile cannot be blank")
        return normalized

    @model_validator(mode="after")
    def duration_order(self) -> DiscoveryRequest:
        supplied = (
            self.min_duration_seconds,
            self.target_duration_seconds,
            self.max_duration_seconds,
        )
        if all(item is not None for item in supplied):
            minimum, target, maximum = supplied
            assert minimum is not None and target is not None and maximum is not None
            if not minimum <= target <= maximum:
                raise ValueError("durations must satisfy minimum <= target <= maximum")
        return self


class DiscoveryRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    asset_id: UUID
    transcript_id: UUID
    requested_by_user_id: UUID
    state: DiscoveryRunState
    strategy: DiscoveryStrategy
    input_hash: str
    algorithm_version: str
    score_version: str
    configuration: dict[str, object]
    provider: str | None
    model: str | None
    provider_mode: ProviderMode
    model_verified: bool
    model_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: Decimal
    latency_ms: int | None
    candidate_count: int
    failure_category: str | None
    failure_message: str | None
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DiscoveryRunListResponse(StrictSchema):
    items: list[DiscoveryRunResponse]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


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
    algorithm_version: str


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    asset_id: UUID
    transcript_id: UUID
    discovery_run_id: UUID
    parent_candidate_id: UUID | None
    state: CandidateState
    rank: int
    start_ms: int
    end_ms: int
    speech_start_ms: int
    speech_end_ms: int
    start_segment_ordinal: int
    end_segment_ordinal: int
    transcript_excerpt: str
    language: str | None
    transcript_confidence: float | None
    recommendation_confidence: float
    overall_score: float
    rationale: str
    topics: list[str]
    risk_flags: list[str]
    model_provider: str | None
    model: str | None
    model_request_id: str | None
    model_verified: bool
    word_timestamps_available: bool
    algorithm_version: str
    score_version: str
    boundary_revision: int
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CandidateReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    previous_candidate_id: UUID | None
    reviewer_user_id: UUID
    action: CandidateReviewAction
    from_state: CandidateState
    to_state: CandidateState
    reason: str
    previous_bounds: dict[str, object] | None
    new_bounds: dict[str, object] | None
    created_at: datetime


class CandidateDetailResponse(StrictSchema):
    candidate: CandidateResponse
    scores: list[ScoreComponentResponse]
    reviews: list[CandidateReviewResponse]


class DiscoveryResultResponse(StrictSchema):
    run: DiscoveryRunResponse
    candidates: list[CandidateResponse]
    idempotent: bool


class CandidateListResponse(StrictSchema):
    items: list[CandidateResponse]
    total: int
    offset: int
    limit: int


class CandidateReviewRequest(StrictSchema):
    action: Literal[CandidateReviewAction.APPROVE, CandidateReviewAction.REJECT]
    reason: str = Field(min_length=5, max_length=2000)


class CandidateBoundaryUpdate(StrictSchema):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=2000)

    @model_validator(mode="after")
    def ordered_bounds(self) -> CandidateBoundaryUpdate:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be after start_ms")
        return self


class ContextSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ordinal: int
    start_ms: int
    end_ms: int
    text: str
    normalized_text: str
    confidence: float | None
    speaker_label: str | None
    selected: bool = False


class CandidateContextResponse(StrictSchema):
    candidate_id: UUID
    asset_id: UUID
    duration_ms: int
    context_start_ms: int
    context_end_ms: int
    segments: list[ContextSegmentResponse]


class ModelOptionSelection(StrictSchema):
    option_id: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class ModelDiscoverySelection(StrictSchema):
    selections: list[ModelOptionSelection] = Field(default_factory=list, max_length=50)
