from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from clipos.core.enums import (
    AgentRole,
    AutonomyClass,
    ExecutiveActionKind,
    PublishingProvider,
    SalesAssetKind,
)
from clipos.core.errors import ClipOSError

"""The closed Phase 10 executive action registry.

This module is the single source of truth for what the executive layer
may dispatch. The registry is deliberately small and every member is
internal and reversible:

- ``generate_recommendations`` — run the Phase 7 deterministic learning
  loop; it only ever creates PROPOSED recommendations.
- ``run_analytics_collection`` — run a Phase 7 collection pass; read-only
  against providers, writes truthful snapshots.
- ``sync_opportunity_source`` — run a Phase 8 source sync; imports
  records for human review, contacts nobody.
- ``create_crm_draft`` — create a Phase 9 evidence-cited sales draft;
  drafts cannot be sent (Phase 9 has no send path).
- ``create_internal_task`` — create a Phase 9 CRM follow-up task.
- ``noop_report`` — compile a status report from stored state; writes
  nothing outside the action's own result.

Publishing, outreach/sending, payments, deletion, permission changes,
budget changes and autonomy changes are NOT in the registry and cannot
be added by data: plan payloads are validated against the strict
pydantic schemas below at creation time and again immediately before
dispatch, so an unknown kind, an unknown payload field or a mistyped
identifier fails closed. Free text found in assets, transcripts or
opportunity records is data for these schemas — it is never parsed as
an instruction and cannot widen the registry.
"""

ACTION_SCHEMA_VERSION = "executive-action-v1"

#: Kinds that are named explicitly in refusal messages so an operator (or
#: a prompt-injected record) asking for one gets a precise answer: these
#: are not "unknown", they are forbidden by design in Phase 10.
FORBIDDEN_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "publish",
        "publish_video",
        "public_publish",
        "send",
        "send_email",
        "send_message",
        "outreach",
        "charge",
        "payment",
        "refund",
        "delete",
        "delete_record",
        "permission_change",
        "grant_permission",
        "budget_change",
        "autonomy_change",
        "approve",
        "self_approve",
    }
)


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerateRecommendationsPayload(_StrictPayload):
    """No parameters: the Phase 7 generator reads stored tenant state."""


class RunAnalyticsCollectionPayload(_StrictPayload):
    provider: PublishingProvider | None = None


class SyncOpportunitySourcePayload(_StrictPayload):
    source_id: UUID
    # Seed-list sources import operator-supplied records; the cap mirrors
    # the Phase 8 import cap so a plan cannot smuggle an unbounded import.
    records: list[dict[str, Any]] | None = Field(default=None, max_length=500)


class CreateCrmDraftPayload(_StrictPayload):
    account_id: UUID
    kind: SalesAssetKind = SalesAssetKind.FOLLOW_UP
    contact_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    # The body is never accepted from a plan: drafts are assembled by the
    # Phase 9 deterministic generator from cited evidence only.


class CreateInternalTaskPayload(_StrictPayload):
    account_id: UUID
    title: str = Field(min_length=1, max_length=300)
    due_at: datetime | None = None
    assignee_user_id: UUID | None = None


class NoopReportPayload(_StrictPayload):
    note: str | None = Field(default=None, max_length=500)


@dataclass(frozen=True)
class ActionSpec:
    kind: ExecutiveActionKind
    autonomy_class: AutonomyClass
    agent_role: AgentRole
    payload_model: type[BaseModel]
    # Internal budget minor units. These are orchestration accounting
    # units for ceilings and agent budgets — deterministic internal
    # actions consume no provider spend, so the numbers are deliberately
    # small, fixed and disclosed rather than measured.
    cost_estimate_minor: int
    description: str


ACTION_REGISTRY: dict[ExecutiveActionKind, ActionSpec] = {
    ExecutiveActionKind.GENERATE_RECOMMENDATIONS: ActionSpec(
        kind=ExecutiveActionKind.GENERATE_RECOMMENDATIONS,
        autonomy_class=AutonomyClass.AUTO_INTERNAL,
        agent_role=AgentRole.ANALYST,
        payload_model=GenerateRecommendationsPayload,
        cost_estimate_minor=2,
        description=(
            "Run the Phase 7 deterministic recommendation generator; "
            "creates PROPOSED recommendations for human review only."
        ),
    ),
    ExecutiveActionKind.RUN_ANALYTICS_COLLECTION: ActionSpec(
        kind=ExecutiveActionKind.RUN_ANALYTICS_COLLECTION,
        autonomy_class=AutonomyClass.AUTO_INTERNAL,
        agent_role=AgentRole.ANALYST,
        payload_model=RunAnalyticsCollectionPayload,
        cost_estimate_minor=5,
        description=(
            "Run a Phase 7 analytics collection pass over succeeded "
            "publications; missing metrics are recorded as unavailable, "
            "never invented."
        ),
    ),
    ExecutiveActionKind.SYNC_OPPORTUNITY_SOURCE: ActionSpec(
        kind=ExecutiveActionKind.SYNC_OPPORTUNITY_SOURCE,
        autonomy_class=AutonomyClass.APPROVAL_REQUIRED,
        agent_role=AgentRole.RESEARCHER,
        payload_model=SyncOpportunitySourcePayload,
        cost_estimate_minor=3,
        description=(
            "Sync one Phase 8 opportunity source; imports records for "
            "human review and contacts nobody."
        ),
    ),
    ExecutiveActionKind.CREATE_CRM_DRAFT: ActionSpec(
        kind=ExecutiveActionKind.CREATE_CRM_DRAFT,
        autonomy_class=AutonomyClass.APPROVAL_REQUIRED,
        agent_role=AgentRole.EXECUTOR,
        payload_model=CreateCrmDraftPayload,
        cost_estimate_minor=2,
        description=(
            "Create one Phase 9 evidence-cited sales draft; drafts are "
            "stored for human review and cannot be sent."
        ),
    ),
    ExecutiveActionKind.CREATE_INTERNAL_TASK: ActionSpec(
        kind=ExecutiveActionKind.CREATE_INTERNAL_TASK,
        autonomy_class=AutonomyClass.AUTO_INTERNAL,
        agent_role=AgentRole.EXECUTOR,
        payload_model=CreateInternalTaskPayload,
        cost_estimate_minor=1,
        description="Create one Phase 9 CRM follow-up task on an account.",
    ),
    ExecutiveActionKind.NOOP_REPORT: ActionSpec(
        kind=ExecutiveActionKind.NOOP_REPORT,
        autonomy_class=AutonomyClass.AUTO_INTERNAL,
        agent_role=AgentRole.ANALYST,
        payload_model=NoopReportPayload,
        cost_estimate_minor=0,
        description=(
            "Compile a status report from stored tenant state; writes "
            "nothing outside the action result."
        ),
    ),
}


def parse_action_kind(raw: str) -> ExecutiveActionKind:
    """Resolve a client-supplied kind string against the closed registry."""
    try:
        return ExecutiveActionKind(raw)
    except ValueError:
        if raw in FORBIDDEN_ACTION_KINDS:
            raise ClipOSError(
                "executive_action_forbidden",
                f"action kind {raw!r} is forbidden by design in Phase 10: the "
                "executive may only dispatch internal, reversible actions, and "
                "publishing, sending, payments, deletion, permission, budget "
                "and autonomy changes are never in the registry",
                status_code=422,
            ) from None
        raise ClipOSError(
            "executive_action_unknown",
            f"action kind {raw!r} is not in the closed executive action "
            f"registry ({ACTION_SCHEMA_VERSION}); allowed kinds are "
            f"{sorted(kind.value for kind in ACTION_REGISTRY)}",
            status_code=422,
        ) from None


def validate_action_payload(kind: ExecutiveActionKind, payload: dict[str, Any]) -> BaseModel:
    """Validate one action payload against its strict registry schema.

    Used at plan creation and again immediately before dispatch, so a
    payload that was valid when written but has since been tampered
    with (or written by an older schema) fails closed at execution.
    """
    spec = ACTION_REGISTRY[kind]
    try:
        return spec.payload_model.model_validate(payload)
    except ValidationError as exc:
        safe_errors = [
            {
                "location": [str(item) for item in error.get("loc", [])],
                "message": error.get("msg", "invalid value"),
                "type": error.get("type", "validation_error"),
            }
            for error in exc.errors()
        ]
        raise ClipOSError(
            "executive_action_payload_invalid",
            f"payload for action kind {kind.value!r} does not match the "
            f"closed registry schema ({ACTION_SCHEMA_VERSION})",
            status_code=422,
            details={"errors": safe_errors},
        ) from None
