"""Stable boundary contracts reserved for later phases.

These schemas define ownership and lifecycle vocabulary only. They are not implementations
of ingestion, discovery, rendering, publishing, analytics, sales, or orchestration.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from clipos.core.enums import AssetState, CandidateState


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OwnedRecord(StrictContract):
    id: UUID
    tenant_id: UUID
    created_at: datetime
    updated_at: datetime


class RenderState(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    RENDERING = "rendering"
    VALIDATING_OUTPUT = "validating_output"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class PublishState(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    CANCELLED = "cancelled"


class AssetContract(OwnedRecord):
    state: AssetState
    source_type: str
    storage_key: str
    checksum_sha256: str
    rights_note: str | None = None


class TranscriptSegmentContract(StrictContract):
    id: UUID
    asset_id: UUID
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def time_order(self) -> "TranscriptSegmentContract":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be after start_seconds")
        return self


class ClipCandidateContract(OwnedRecord):
    asset_id: UUID
    state: CandidateState
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    algorithm_version: str
    evidence: dict[str, Any]

    @model_validator(mode="after")
    def time_order(self) -> "ClipCandidateContract":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be after start_seconds")
        return self


class RenderedClipContract(OwnedRecord):
    candidate_id: UUID
    state: RenderState
    editing_plan_version: str
    storage_key: str | None = None
    checksum_sha256: str | None = None


class PublishJobContract(OwnedRecord):
    rendered_clip_id: UUID
    destination: str
    state: PublishState
    approval_request_id: UUID
    provider_content_id: str | None = None


class CampaignContract(OwnedRecord):
    name: str
    status: str
    objective: str
    budget_minor_units: int | None = Field(default=None, ge=0)


class OpportunityContract(OwnedRecord):
    source: str
    external_key: str
    review_state: str
    evidence: dict[str, Any]
    score_version: str
