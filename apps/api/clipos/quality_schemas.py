from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from clipos.core.enums import (
    DestinationPlatform,
    EvidenceSource,
    GateCheckpoint,
    GateOutcome,
    GateReviewAction,
    MaterialClearance,
    QualityCheckKind,
    QualityCheckStatus,
    QualitySeverity,
    RightsBasis,
    RightsEvidenceStatus,
    RightsScope,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThirdPartyMaterialState(StrictSchema):
    music: MaterialClearance
    footage: MaterialClearance
    logos: MaterialClearance
    likeness_or_voice: MaterialClearance
    personal_data: MaterialClearance
    sensitive_information: MaterialClearance


class RightsEvidenceCreate(StrictSchema):
    asset_id: UUID
    scope: RightsScope
    candidate_id: UUID | None = None
    render_job_id: UUID | None = None
    basis: RightsBasis
    status: RightsEvidenceStatus
    source_owner: str = Field(min_length=2, max_length=300)
    evidence_type: Literal[
        "ownership_record",
        "written_consent",
        "license",
        "contract",
        "public_domain_assessment",
        "other",
    ]
    evidence_reference: str = Field(min_length=3, max_length=500)
    evidence_checksum_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    permission_recorded_at: datetime
    effective_from: datetime
    expires_at: datetime | None = None
    no_expiration_asserted: bool = False
    allowed_platforms: list[DestinationPlatform] = Field(min_length=1, max_length=10)
    allowed_territories: list[str] = Field(min_length=1, max_length=250)
    commercial_use_allowed: bool
    editing_allowed: bool
    ai_processing_allowed: bool
    attribution_required: bool
    attribution_text: str | None = Field(default=None, max_length=4000)
    license_terms: str = Field(min_length=3, max_length=20_000)
    restrictions: str = Field(min_length=3, max_length=20_000)
    third_party_material: ThirdPartyMaterialState
    campaign_reference: str | None = Field(default=None, max_length=160)

    @field_validator("permission_recorded_at", "effective_from", "expires_at")
    @classmethod
    def dates_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("rights timestamps must include a timezone")
        return value

    @field_validator("source_owner", "evidence_reference", "license_terms", "restrictions")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("attribution_text", "campaign_reference")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("allowed_platforms")
    @classmethod
    def platforms_must_be_unique(
        cls, value: list[DestinationPlatform]
    ) -> list[DestinationPlatform]:
        if len(set(value)) != len(value):
            raise ValueError("allowed_platforms must not contain duplicates")
        return value

    @field_validator("allowed_territories")
    @classmethod
    def normalize_territories(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in value]
        if any(
            item != "WORLDWIDE" and (len(item) != 2 or not item.isascii() or not item.isalpha())
            for item in normalized
        ):
            raise ValueError("territories must be ISO alpha-2 codes or WORLDWIDE")
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed_territories must not contain duplicates")
        if "WORLDWIDE" in normalized and len(normalized) != 1:
            raise ValueError("WORLDWIDE cannot be combined with country codes")
        return normalized

    @model_validator(mode="after")
    def validate_scope_and_terms(self) -> RightsEvidenceCreate:
        if self.scope == RightsScope.ASSET and (
            self.candidate_id is not None or self.render_job_id is not None
        ):
            raise ValueError("asset-scoped evidence cannot name a candidate or render")
        if self.scope == RightsScope.CANDIDATE and (
            self.candidate_id is None or self.render_job_id is not None
        ):
            raise ValueError("candidate-scoped evidence requires only candidate_id")
        if self.scope == RightsScope.RENDER and self.render_job_id is None:
            raise ValueError("render-scoped evidence requires render_job_id")
        if self.expires_at is None and not self.no_expiration_asserted:
            raise ValueError("an expiration or explicit no-expiration assertion is required")
        if self.expires_at is not None and self.no_expiration_asserted:
            raise ValueError("expiration and no-expiration assertion are mutually exclusive")
        if self.expires_at is not None and self.expires_at <= self.effective_from:
            raise ValueError("expires_at must be after effective_from")
        if self.attribution_required and not self.attribution_text:
            raise ValueError("required attribution needs non-blank attribution_text")
        if not self.attribution_required and self.attribution_text:
            raise ValueError("attribution_text is accepted only when attribution is required")
        if self.status == RightsEvidenceStatus.VALID and self.basis == RightsBasis.UNKNOWN:
            raise ValueError("unknown permission basis cannot be marked valid")
        return self


class RightsRevocationRequest(StrictSchema):
    reason: str = Field(min_length=10, max_length=4000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return " ".join(value.split())


class RightsEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    asset_id: UUID
    candidate_id: UUID | None
    render_job_id: UUID | None
    recorded_by_user_id: UUID
    revoked_by_user_id: UUID | None
    scope: RightsScope
    basis: RightsBasis
    status: RightsEvidenceStatus
    source_owner: str
    evidence_type: str
    evidence_reference: str
    evidence_checksum_sha256: str | None
    permission_recorded_at: datetime
    effective_from: datetime
    expires_at: datetime | None
    no_expiration_asserted: bool
    allowed_platforms: list[str]
    allowed_territories: list[str]
    commercial_use_allowed: bool
    editing_allowed: bool
    ai_processing_allowed: bool
    attribution_required: bool
    attribution_text: str | None
    license_terms: str
    restrictions: str
    third_party_material: dict[str, str]
    campaign_reference: str | None
    evidence_version: str
    revoked_at: datetime | None
    revocation_reason: str | None
    created_at: datetime
    updated_at: datetime


class QualityEvaluationCreate(StrictSchema):
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    rights_evidence_id: UUID | None = None
    destination: DestinationPlatform
    territory: str = Field(min_length=2, max_length=32)
    campaign_reference: str | None = Field(default=None, max_length=160)

    @field_validator("territory")
    @classmethod
    def normalize_territory(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "WORLDWIDE" and (
            len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha()
        ):
            raise ValueError("territory must be an ISO alpha-2 code or WORLDWIDE")
        return normalized

    @field_validator("campaign_reference")
    @classmethod
    def normalize_campaign(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class QualityCheckResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    evaluation_id: UUID
    kind: QualityCheckKind
    status: QualityCheckStatus
    severity: QualitySeverity
    evidence_source: EvidenceSource
    evidence: dict[str, object]
    confidence: float
    check_version: str
    remediation: str
    hard_block: bool
    created_at: datetime


class QualityReviewRequest(StrictSchema):
    action: GateReviewAction
    reason: str = Field(min_length=20, max_length=4000)
    acknowledged_check_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("acknowledged_check_ids")
    @classmethod
    def acknowledgements_must_be_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("acknowledged_check_ids must not contain duplicates")
        return value


class QualityReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    evaluation_id: UUID
    reviewer_user_id: UUID
    action: GateReviewAction
    decision_source: EvidenceSource
    from_outcome: GateOutcome
    to_outcome: GateOutcome
    reason: str
    acknowledged_check_ids: list[str]
    created_at: datetime


class GateVerificationRequest(StrictSchema):
    checkpoint: GateCheckpoint
    expected_destination: DestinationPlatform
    expected_territory: str = Field(min_length=2, max_length=32)

    @field_validator("expected_territory")
    @classmethod
    def normalize_territory(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "WORLDWIDE" and (
            len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha()
        ):
            raise ValueError("territory must be an ISO alpha-2 code or WORLDWIDE")
        return normalized


class GateVerificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    evaluation_id: UUID
    checkpoint: GateCheckpoint
    accepted: bool
    observed_input_hash: str
    failure_reasons: list[str]
    evidence_payload: dict[str, object]
    evidence_signature: str
    verified_at: datetime
    valid_until: datetime


class QualityEvaluationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    render_job_id: UUID
    render_output_id: UUID
    candidate_id: UUID
    asset_id: UUID
    rights_evidence_id: UUID | None
    requested_by_user_id: UUID
    destination: DestinationPlatform
    territory: str
    campaign_reference: str | None
    outcome: GateOutcome
    automated_outcome: GateOutcome
    evaluation_version: str
    policy_version: str
    evaluated_at: datetime
    created_at: datetime
    updated_at: datetime


class QualityEvaluationDetail(StrictSchema):
    evaluation: QualityEvaluationSummary
    input_hash: str
    evidence_hash: str
    model_assistance_used: bool
    policy_explanation: list[str]
    missing_evidence: list[str]
    remediation: list[str]
    hard_block_reasons: list[str]
    approval_evidence: dict[str, object] | None
    approval_signature: str | None
    approval_expires_at: datetime | None
    checks: list[QualityCheckResponse]
    rights: RightsEvidenceResponse | None
    rights_state: str
    decisions: list[QualityReviewDecisionResponse]
    verifications: list[GateVerificationResponse]
    stale: bool
    stale_reasons: list[str]
    legal_clearance: Literal[False] = False
    privacy_note: str = (
        "Only operational references and recorder IDs are shown; raw permission documents and "
        "unnecessary personal data are not stored in this phase."
    )
    legal_note: str = (
        "A passing quality gate is operational evidence, not legal advice or legal clearance."
    )


class QualityEvaluationList(StrictSchema):
    items: list[QualityEvaluationSummary]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
