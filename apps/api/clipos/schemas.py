from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from clipos.core.enums import (
    ActionClass,
    ApprovalStatus,
    AuditOutcome,
    JobState,
    ProviderMode,
    Role,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    tenant_slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class PrincipalResponse(BaseModel):
    user_id: UUID
    tenant_id: UUID
    role: Role
    email: EmailStr
    is_platform_admin: bool


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=2, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    idempotency_key: str = Field(min_length=8, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)


class JobResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    kind: str
    idempotency_key: str
    state: JobState
    result: dict[str, Any] | None
    failure_category: str | None
    failure_message: str | None
    attempts: int
    max_attempts: int
    correlation_id: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_class: ActionClass
    action_type: str = Field(min_length=2, max_length=160)
    resource_type: str = Field(min_length=2, max_length=100)
    resource_id: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=10, max_length=4000)
    action_payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def expiry_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=4000)


class ApprovalResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None
    action_class: ActionClass
    action_type: str
    resource_type: str
    resource_id: str
    payload_hash: str
    rationale: str
    status: ApprovalStatus
    decision_reason: str | None
    expires_at: datetime | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AuditEventResponse(ORMModel):
    id: UUID
    tenant_id: UUID | None
    actor_user_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: AuditOutcome
    correlation_id: str
    event_data: dict[str, Any]
    created_at: datetime


class ConfigurationItem(BaseModel):
    name: str
    configured: bool
    required: bool
    secret: bool


class ProviderStatusResponse(ORMModel):
    provider: str
    capability: str
    mode: ProviderMode
    required_variable_names: list[str]
    last_checked_at: datetime | None
    last_error_category: str | None


class SystemControlResponse(ORMModel):
    key: str
    paused: bool
    reason: str
    updated_by_user_id: UUID | None
    updated_at: datetime


class SystemControlUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=500)


class HealthComponent(BaseModel):
    status: str
    required: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    components: dict[str, HealthComponent] = Field(default_factory=dict)
