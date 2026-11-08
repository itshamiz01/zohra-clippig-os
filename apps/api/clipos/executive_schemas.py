from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from clipos.core.enums import (
    AgentDefinitionStatus,
    AgentRole,
    AgentRunState,
    AutonomyClass,
    ExecutiveActionKind,
    ExecutiveActionState,
    ExecutiveControlMode,
    ExecutivePlanState,
    ExecutiveProposalKind,
    ExecutiveProposalState,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Action registry (read-only view)
# ---------------------------------------------------------------------------
class ActionSpecResponse(StrictSchema):
    kind: ExecutiveActionKind
    autonomy_class: AutonomyClass
    agent_role: AgentRole
    cost_estimate_minor: int
    description: str
    schema_version: str
    payload_fields: list[str]


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------
class AgentContractSchema(StrictSchema):
    """One agent contract. ``allowed_action_kinds`` can only name
    members of the closed registry enum, so a contract can narrow the
    registry for an agent but can never widen it.

    ``cost_budget_per_run_minor`` is literal for a defined agent: an
    action whose estimate exceeds it is blocked, so a budget of 0 lets
    only zero-cost actions run. Only the built-in default used when no
    definition exists is uncapped."""

    allowed_action_kinds: list[ExecutiveActionKind] = Field(min_length=1)
    input_schema_version: str = Field(min_length=1, max_length=80)
    output_schema_version: str = Field(min_length=1, max_length=80)
    cost_budget_per_run_minor: int = Field(ge=0)
    timeout_seconds: int = Field(ge=1, le=600)
    max_steps: int = Field(ge=1, le=100)


class AgentDefinitionCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=120)
    role: AgentRole
    contract: AgentContractSchema
    status: AgentDefinitionStatus = AgentDefinitionStatus.DISABLED


class AgentDefinitionUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    contract: AgentContractSchema | None = None
    status: AgentDefinitionStatus | None = None


class AgentDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    is_system: bool
    name: str
    role: AgentRole
    contract: dict[str, Any]
    status: AgentDefinitionStatus
    version: int
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AgentDefinitionList(StrictSchema):
    items: list[AgentDefinitionResponse]
    total: int


# ---------------------------------------------------------------------------
# Executive controls (kill switch + ceilings)
# ---------------------------------------------------------------------------
class ExecutiveControlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    mode: ExecutiveControlMode
    daily_cost_ceiling_minor: int | None
    per_action_cost_ceiling_minor: int | None
    updated_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ExecutiveControlUpdate(StrictSchema):
    mode: ExecutiveControlMode | None = None
    daily_cost_ceiling_minor: int | None = Field(default=None, ge=0)
    per_action_cost_ceiling_minor: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Plans and actions
# ---------------------------------------------------------------------------
class ExecutiveActionCreate(StrictSchema):
    # ``kind`` stays a string here on purpose: the service resolves it
    # against the closed registry so unknown kinds and forbidden kinds
    # produce distinct, precise refusals instead of a generic 422.
    kind: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class PlanCreate(StrictSchema):
    title: str = Field(min_length=1, max_length=300)
    objective: str = Field(min_length=1, max_length=4000)
    actions: list[ExecutiveActionCreate] = Field(min_length=1, max_length=50)


class PlannerRunRequest(StrictSchema):
    """The deterministic planner takes no instructions: it reads stored
    tenant state only. The request body exists for forward compatibility
    and is intentionally empty — free text is never planner input."""


class ExecutiveActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    plan_id: UUID
    sequence: int
    kind: ExecutiveActionKind
    payload: dict[str, Any]
    autonomy_class: AutonomyClass
    status: ExecutiveActionState
    cost_estimate_minor: int
    cost_incurred_minor: int
    result: dict[str, Any] | None
    failure_category: str | None
    error: str | None
    agent_definition_id: UUID | None
    executed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    title: str
    objective: str
    status: ExecutivePlanState
    created_by: str
    created_by_user_id: UUID | None
    rationale: str
    evidence: dict[str, Any]
    total_estimated_cost_minor: int
    approval_request_id: UUID | None
    approved_by_user_id: UUID | None
    approved_at: datetime | None
    execution_job_id: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    failure_category: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime


class PlanDetailResponse(PlanResponse):
    actions: list[ExecutiveActionResponse]


class PlanList(StrictSchema):
    items: list[PlanResponse]
    total: int


class PlanExecuteResponse(StrictSchema):
    plan: PlanResponse
    job_id: UUID
    job_state: str


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    agent_id: UUID | None
    agent_name: str
    agent_role: AgentRole
    plan_id: UUID | None
    action_id: UUID | None
    status: AgentRunState
    steps_used: int
    cost_incurred_minor: int
    usage_cost_event_id: UUID | None
    started_at: datetime
    finished_at: datetime | None
    failure_category: str | None
    error: str | None


class AgentRunList(StrictSchema):
    items: list[AgentRunResponse]
    total: int


# ---------------------------------------------------------------------------
# Self-improvement proposals (decision records only — no apply path)
# ---------------------------------------------------------------------------
class ProposalCreate(StrictSchema):
    kind: ExecutiveProposalKind
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=4000)
    change_payload: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)


class ProposalReview(StrictSchema):
    decision: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=1000)


class ProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    kind: ExecutiveProposalKind
    title: str
    summary: str
    change_payload: dict[str, Any]
    state: ExecutiveProposalState
    version: int
    created_by: str
    created_by_user_id: UUID | None
    reviewed_by_user_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime


class ProposalList(StrictSchema):
    items: list[ProposalResponse]
    total: int
