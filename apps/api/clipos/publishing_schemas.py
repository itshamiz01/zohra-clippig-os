from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from clipos.core.enums import (
    OAuthConnectionState,
    ProviderMode,
    PublishEventKind,
    PublishingProvider,
    PublishState,
    PublishVisibility,
)
from clipos.schemas import ApprovalResponse


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityFeatures(StrictSchema):
    oauth: bool
    resumable_upload: bool
    processing_status: bool
    privacy_status: bool
    metadata: bool
    thumbnails: bool
    cancellation: bool
    reconciliation: bool
    analytics_handoff: bool
    supported_visibilities: list[PublishVisibility]


class PublishingCapabilityResponse(StrictSchema):
    provider: PublishingProvider
    display_name: str
    mode: ProviderMode
    configured: bool
    real_oauth_verified: bool
    real_upload_verified: bool
    required_scopes: list[str]
    required_configuration: list[str]
    features: CapabilityFeatures
    limitations: list[str]
    evidence_note: str


class OAuthStartResponse(StrictSchema):
    provider: PublishingProvider
    authorization_url: str
    redirect_uri: str
    required_scopes: list[str]
    expires_at: datetime
    state_is_one_time: bool = True
    pkce_enabled: bool = True


class PublishingConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    provider: PublishingProvider
    state: OAuthConnectionState
    provider_destination_id: str
    destination_display_name: str
    granted_scopes: list[str]
    access_token_expires_at: datetime
    has_refresh_token: bool
    last_refreshed_at: datetime | None
    last_validated_at: datetime | None
    revoked_at: datetime | None
    reconnect_required_at: datetime | None
    last_error_category: str | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime


class OAuthCallbackResponse(StrictSchema):
    connected: bool
    provider: PublishingProvider
    message: str
    connection: PublishingConnectionResponse | None = None


class PublicationCreate(StrictSchema):
    connection_id: UUID
    render_job_id: UUID
    quality_evaluation_id: UUID
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    visibility: PublishVisibility = PublishVisibility.PRIVATE
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    made_for_kids: bool = False
    category_id: str = Field(default="22", pattern=r"^[0-9]{1,3}$")
    public_approval_id: UUID | None = None

    @field_validator("title", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        normalized = [" ".join(item.split()) for item in value]
        if any(not item or len(item) > 60 for item in normalized):
            raise ValueError("tags must contain 1 to 60 visible characters")
        if len({item.casefold() for item in normalized}) != len(normalized):
            raise ValueError("tags must not contain duplicates")
        if sum(len(item) for item in normalized) > 500:
            raise ValueError("combined tag text must not exceed 500 characters")
        return normalized


class PublicApprovalCreate(StrictSchema):
    release: PublicationCreate
    rationale: str = Field(min_length=20, max_length=4000)
    expires_at: datetime | None = None

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return " ".join(value.split())


class PublicApprovalResponse(StrictSchema):
    approval: ApprovalResponse
    payload_hash: str
    binding_note: str


class PublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    requested_by_user_id: UUID
    connection_id: UUID
    render_job_id: UUID
    render_output_id: UUID
    quality_evaluation_id: UUID
    background_job_id: UUID
    public_approval_id: UUID | None
    acceptance_gate_verification_id: UUID
    upload_gate_verification_id: UUID | None
    provider: PublishingProvider
    provider_destination_id: str
    idempotency_key: str
    state: PublishState
    visibility: PublishVisibility
    title: str
    description: str
    tags: list[str]
    made_for_kids: bool
    category_id: str
    territory: str
    campaign_reference: str | None
    progress_percent: int
    stage: str
    content_type: str
    content_length: int
    committed_bytes: int
    provider_publication_id: str | None
    provider_url: str | None
    provider_processing_status: str | None
    sanitized_provider_response: dict[str, Any]
    analytics_handoff: dict[str, Any] | None
    cancellation_requested: bool
    attempt_count: int
    max_attempts: int
    retryable: bool
    uncertain_remote_result: bool
    failure_category: str | None
    failure_message: str | None
    manual_next_step: str | None
    retry_after_seconds: int | None
    started_at: datetime | None
    upload_started_at: datetime | None
    provider_created_at: datetime | None
    finished_at: datetime | None
    last_reconciled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicationEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    publication_id: UUID
    sequence: int
    kind: PublishEventKind
    from_state: PublishState | None
    to_state: PublishState
    progress_percent: int
    stage: str
    message: str
    event_data: dict[str, Any]
    created_at: datetime


class PublicationList(StrictSchema):
    items: list[PublicationResponse]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
