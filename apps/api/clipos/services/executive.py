from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.core.enums import (
    ActionClass,
    AgentDefinitionStatus,
    AgentRole,
    ApprovalStatus,
    AuditOutcome,
    AutonomyClass,
    CrmTaskState,
    ExecutiveActionKind,
    ExecutiveActionState,
    ExecutiveControlMode,
    ExecutivePlanState,
    ExecutiveProposalState,
    JobState,
    OpportunitySourceKind,
    OpportunitySourceStatus,
    OpportunityState,
    PublishState,
    RecommendationState,
    Role,
)
from clipos.core.errors import ClipOSError, ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.executive_schemas import AgentContractSchema
from clipos.models import (
    AgentDefinition,
    AgentRun,
    AnalyticsCollectionRun,
    AnalyticsMetricSnapshot,
    ApprovalRequest,
    BackgroundJob,
    CrmAccount,
    CrmTask,
    ExecutiveAction,
    ExecutiveControl,
    ExecutivePlan,
    ExecutiveProposal,
    Opportunity,
    OpportunitySource,
    Publication,
    Recommendation,
    SystemControl,
    UsageCostEvent,
)
from clipos.queue import QueueBroker, QueueMessage
from clipos.schemas import ApprovalCreate
from clipos.services.analytics import AnalyticsService
from clipos.services.approvals import ApprovalService
from clipos.services.audit import AuditService
from clipos.services.executive_actions import (
    ACTION_REGISTRY,
    ACTION_SCHEMA_VERSION,
    parse_action_kind,
    validate_action_payload,
)
from clipos.services.jobs import JobService

EXECUTIVE_JOB_KIND = "executive.execute"
PLANNER_VERSION = "executive-planner-v1"

_STALE_AFTER = timedelta(hours=24)
_HIGH_SCORE = 70.0


@dataclass(frozen=True)
class ResolvedAgent:
    """The agent contract an action will run under: a tenant definition
    first, then a system definition, then the built-in default."""

    definition: AgentDefinition | None
    name: str
    role: AgentRole
    contract: AgentContractSchema


def default_contract() -> AgentContractSchema:
    return AgentContractSchema(
        allowed_action_kinds=list(ACTION_REGISTRY),
        input_schema_version=ACTION_SCHEMA_VERSION,
        output_schema_version=ACTION_SCHEMA_VERSION,
        cost_budget_per_run_minor=0,
        timeout_seconds=120,
        max_steps=25,
    )


def _today_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class ExecutiveService:
    """Phase 10 executive layer: agent contracts, deterministic
    planning, approval-gated internal execution, kill switches and
    self-improvement proposals.

    Authority boundary: this service can create plans whose actions all
    come from the closed registry in ``executive_actions`` — internal,
    reversible record-keeping only. It has no publish, send, payment,
    delete, permission, budget-change or autonomy-change surface, and
    its proposals can be approved but never applied from inside the
    system. Execution itself lives in ``executive_processing`` and
    re-validates everything this service validated at write time.
    """

    # ------------------------------------------------------------------
    # Controls (kill switch + ceilings)
    # ------------------------------------------------------------------
    @staticmethod
    async def get_or_create_control(session: AsyncSession, tenant_id: UUID) -> ExecutiveControl:
        control = await session.scalar(
            select(ExecutiveControl).where(ExecutiveControl.tenant_id == tenant_id)
        )
        if control is None:
            control = ExecutiveControl(tenant_id=tenant_id)
            session.add(control)
            await session.flush()
        return control

    @staticmethod
    async def get_control(session: AsyncSession, tenant_id: UUID) -> ExecutiveControl:
        control = await ExecutiveService.get_or_create_control(session, tenant_id)
        await session.commit()
        await session.refresh(control)
        return control

    @staticmethod
    async def update_control(
        session: AsyncSession,
        *,
        principal: Principal,
        changes: dict[str, Any],
    ) -> ExecutiveControl:
        principal.require(Role.OWNER)
        control = await ExecutiveService.get_or_create_control(session, principal.tenant_id)
        applied: dict[str, Any] = {}
        if "mode" in changes:
            control.mode = changes["mode"]
            applied["mode"] = control.mode.value
        if "daily_cost_ceiling_minor" in changes:
            control.daily_cost_ceiling_minor = changes["daily_cost_ceiling_minor"]
            applied["daily_cost_ceiling_minor"] = control.daily_cost_ceiling_minor
        if "per_action_cost_ceiling_minor" in changes:
            control.per_action_cost_ceiling_minor = changes["per_action_cost_ceiling_minor"]
            applied["per_action_cost_ceiling_minor"] = control.per_action_cost_ceiling_minor
        control.updated_by_user_id = principal.user_id
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.control_updated",
            resource_type="executive_control",
            resource_id=str(control.id),
            event_data=applied,
        )
        await session.commit()
        await session.refresh(control)
        return control

    @staticmethod
    async def set_control_mode(
        session: AsyncSession,
        *,
        principal: Principal,
        mode: ExecutiveControlMode,
    ) -> ExecutiveControl:
        """The kill switch: pause or resume this tenant's executive.

        Owner-only and audited. Pausing never edits plan rows directly —
        the execution engine observes the mode between actions and stops
        the plan at the next action boundary.
        """
        principal.require(Role.OWNER)
        control = await ExecutiveService.get_or_create_control(session, principal.tenant_id)
        control.mode = mode
        control.updated_by_user_id = principal.user_id
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action=(
                "executive.paused" if mode == ExecutiveControlMode.PAUSED else "executive.resumed"
            ),
            resource_type="executive_control",
            resource_id=str(control.id),
            event_data={"mode": mode.value},
        )
        await session.commit()
        await session.refresh(control)
        return control

    @staticmethod
    async def platform_paused(session: AsyncSession) -> bool:
        paused = await session.scalar(
            select(SystemControl.paused).where(SystemControl.key == "global")
        )
        return bool(paused)

    # ------------------------------------------------------------------
    # Agent definitions
    # ------------------------------------------------------------------
    @staticmethod
    async def create_agent_definition(
        session: AsyncSession,
        *,
        principal: Principal,
        name: str,
        role: AgentRole,
        contract: AgentContractSchema,
        status: AgentDefinitionStatus,
    ) -> AgentDefinition:
        principal.require(Role.ADMIN)
        existing = await session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.tenant_id == principal.tenant_id,
                AgentDefinition.name == name,
            )
        )
        if existing is not None:
            raise ConflictError(f"an agent definition named {name!r} already exists")
        definition = AgentDefinition(
            tenant_id=principal.tenant_id,
            is_system=False,
            name=name,
            role=role,
            contract=contract.model_dump(mode="json"),
            status=status,
            version=1,
            created_by_user_id=principal.user_id,
        )
        session.add(definition)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.agent_created",
            resource_type="agent_definition",
            resource_id=str(definition.id),
            event_data={"name": name, "role": role.value, "status": status.value},
        )
        await session.commit()
        await session.refresh(definition)
        return definition

    @staticmethod
    async def list_agent_definitions(
        session: AsyncSession, tenant_id: UUID, *, limit: int = 100
    ) -> list[AgentDefinition]:
        rows = await session.scalars(
            select(AgentDefinition)
            .where((AgentDefinition.tenant_id == tenant_id) | (AgentDefinition.tenant_id.is_(None)))
            .order_by(AgentDefinition.name)
            .limit(min(limit, 200))
        )
        return list(rows.all())

    @staticmethod
    async def get_agent_definition(
        session: AsyncSession, tenant_id: UUID, definition_id: UUID
    ) -> AgentDefinition:
        definition = await session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.id == definition_id,
                (AgentDefinition.tenant_id == tenant_id) | (AgentDefinition.tenant_id.is_(None)),
            )
        )
        if definition is None:
            raise NotFoundError("agent definition")
        return definition

    @staticmethod
    async def update_agent_definition(
        session: AsyncSession,
        *,
        principal: Principal,
        definition_id: UUID,
        name: str | None,
        contract: AgentContractSchema | None,
        status: AgentDefinitionStatus | None,
    ) -> AgentDefinition:
        principal.require(Role.ADMIN)
        definition = await ExecutiveService.get_agent_definition(
            session, principal.tenant_id, definition_id
        )
        if definition.tenant_id != principal.tenant_id:
            raise ConflictError("system agent definitions cannot be edited by a tenant")
        if name is not None:
            definition.name = name
        if contract is not None:
            definition.contract = contract.model_dump(mode="json")
            definition.version += 1
        if status is not None:
            definition.status = status
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.agent_updated",
            resource_type="agent_definition",
            resource_id=str(definition.id),
            event_data={"version": definition.version, "status": definition.status.value},
        )
        await session.commit()
        await session.refresh(definition)
        return definition

    @staticmethod
    async def resolve_agent(
        session: AsyncSession, tenant_id: UUID, role: AgentRole
    ) -> ResolvedAgent:
        for scope in ("tenant", "system"):
            query = select(AgentDefinition).where(
                AgentDefinition.role == role,
                AgentDefinition.status == AgentDefinitionStatus.ENABLED,
            )
            if scope == "tenant":
                query = query.where(AgentDefinition.tenant_id == tenant_id)
            else:
                query = query.where(
                    AgentDefinition.tenant_id.is_(None), AgentDefinition.is_system.is_(True)
                )
            definition = await session.scalar(query.order_by(AgentDefinition.created_at))
            if definition is not None:
                try:
                    contract = AgentContractSchema.model_validate(definition.contract)
                except ValidationError:
                    # A stored contract that no longer parses fails
                    # closed: the definition is unusable, not widened.
                    continue
                return ResolvedAgent(
                    definition=definition,
                    name=definition.name,
                    role=role,
                    contract=contract,
                )
        return ResolvedAgent(
            definition=None,
            name=f"builtin-{role.value}",
            role=role,
            contract=default_contract(),
        )

    # ------------------------------------------------------------------
    # Plan creation (manual + planner share this validated path)
    # ------------------------------------------------------------------
    @staticmethod
    async def create_plan(
        session: AsyncSession,
        *,
        principal: Principal,
        title: str,
        objective: str,
        actions: list[dict[str, Any]],
        rationale: str = "",
        evidence: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> ExecutivePlan:
        plan = ExecutivePlan(
            tenant_id=principal.tenant_id,
            title=title,
            objective=objective,
            status=ExecutivePlanState.DRAFT,
            created_by=created_by or str(principal.user_id),
            created_by_user_id=principal.user_id,
            rationale=rationale,
            evidence=evidence or {},
        )
        session.add(plan)
        await session.flush()
        total = 0
        for sequence, raw in enumerate(actions, start=1):
            kind = parse_action_kind(str(raw.get("kind", "")))
            payload = raw.get("payload") or {}
            if not isinstance(payload, dict):
                raise ClipOSError(
                    "executive_action_payload_invalid",
                    "action payload must be a JSON object",
                    status_code=422,
                )
            validated = validate_action_payload(kind, payload)
            spec = ACTION_REGISTRY[kind]
            total += spec.cost_estimate_minor
            session.add(
                ExecutiveAction(
                    tenant_id=principal.tenant_id,
                    plan_id=plan.id,
                    sequence=sequence,
                    kind=kind,
                    payload=validated.model_dump(mode="json"),
                    autonomy_class=spec.autonomy_class,
                    status=ExecutiveActionState.PENDING,
                    cost_estimate_minor=spec.cost_estimate_minor,
                )
            )
        plan.total_estimated_cost_minor = total
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.plan_created",
            resource_type="executive_plan",
            resource_id=str(plan.id),
            event_data={
                "title": title,
                "action_count": len(actions),
                "total_estimated_cost_minor": total,
                "planner_version": (evidence or {}).get("planner_version"),
            },
        )
        await session.commit()
        return await ExecutiveService.get_plan(session, principal.tenant_id, plan.id)

    @staticmethod
    async def get_plan(session: AsyncSession, tenant_id: UUID, plan_id: UUID) -> ExecutivePlan:
        plan = await session.scalar(
            select(ExecutivePlan).where(
                ExecutivePlan.id == plan_id, ExecutivePlan.tenant_id == tenant_id
            )
        )
        if plan is None:
            raise NotFoundError("executive plan")
        # Eagerly load actions in order for detail responses.
        await session.refresh(plan, attribute_names=["actions"])
        return plan

    @staticmethod
    async def list_plans(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        status: ExecutivePlanState | None = None,
        limit: int = 100,
    ) -> list[ExecutivePlan]:
        query = select(ExecutivePlan).where(ExecutivePlan.tenant_id == tenant_id)
        if status is not None:
            query = query.where(ExecutivePlan.status == status)
        rows = await session.scalars(
            query.order_by(desc(ExecutivePlan.created_at)).limit(min(limit, 200))
        )
        return list(rows.all())

    @staticmethod
    async def list_agent_runs(
        session: AsyncSession, tenant_id: UUID, plan_id: UUID, *, limit: int = 100
    ) -> list[AgentRun]:
        await ExecutiveService.get_plan(session, tenant_id, plan_id)
        rows = await session.scalars(
            select(AgentRun)
            .where(AgentRun.tenant_id == tenant_id, AgentRun.plan_id == plan_id)
            .order_by(AgentRun.started_at)
            .limit(min(limit, 200))
        )
        return list(rows.all())

    # ------------------------------------------------------------------
    # Validation and cost accounting
    # ------------------------------------------------------------------
    @staticmethod
    async def executive_incurred_on(
        session: AsyncSession, tenant_id: UUID, day_start: datetime
    ) -> int:
        total = await session.scalar(
            select(func.coalesce(func.sum(ExecutiveAction.cost_incurred_minor), 0)).where(
                ExecutiveAction.tenant_id == tenant_id,
                ExecutiveAction.executed_at >= day_start,
                ExecutiveAction.status == ExecutiveActionState.SUCCEEDED,
            )
        )
        return int(total or 0)

    @staticmethod
    async def validate_plan(session: AsyncSession, plan: ExecutivePlan) -> list[dict[str, Any]]:
        """Re-validate a stored plan end to end.

        Runs at submit time and again inside the execution engine, so a
        plan that was valid when written but whose payloads, ceilings or
        agent contracts have since changed fails closed with named
        problems instead of partially running.
        """
        problems: list[dict[str, Any]] = []
        control = await ExecutiveService.get_or_create_control(session, plan.tenant_id)
        incurred_today = await ExecutiveService.executive_incurred_on(
            session, plan.tenant_id, _today_start(datetime.now(UTC))
        )
        projected = incurred_today
        for action in sorted(plan.actions, key=lambda item: item.sequence):
            if action.status not in {
                ExecutiveActionState.PENDING,
                ExecutiveActionState.SUCCEEDED,
            }:
                continue
            try:
                validate_action_payload(action.kind, action.payload)
            except ClipOSError as exc:
                problems.append(
                    {
                        "action_id": str(action.id),
                        "sequence": action.sequence,
                        "category": exc.code,
                        "message": exc.message,
                    }
                )
                continue
            spec = ACTION_REGISTRY[action.kind]
            if action.autonomy_class != spec.autonomy_class:
                problems.append(
                    {
                        "action_id": str(action.id),
                        "sequence": action.sequence,
                        "category": "autonomy_class_mismatch",
                        "message": (
                            f"stored autonomy class {action.autonomy_class.value} does "
                            f"not match the registry class {spec.autonomy_class.value}"
                        ),
                    }
                )
            if (
                control.per_action_cost_ceiling_minor is not None
                and action.cost_estimate_minor > control.per_action_cost_ceiling_minor
            ):
                problems.append(
                    {
                        "action_id": str(action.id),
                        "sequence": action.sequence,
                        "category": "per_action_cost_ceiling_exceeded",
                        "message": (
                            f"action estimate {action.cost_estimate_minor} exceeds the "
                            f"per-action ceiling {control.per_action_cost_ceiling_minor}"
                        ),
                    }
                )
            if action.status == ExecutiveActionState.PENDING:
                projected += action.cost_estimate_minor
                resolved = await ExecutiveService.resolve_agent(
                    session, plan.tenant_id, spec.agent_role
                )
                if (
                    resolved.definition is not None
                    and action.kind not in resolved.contract.allowed_action_kinds
                ):
                    problems.append(
                        {
                            "action_id": str(action.id),
                            "sequence": action.sequence,
                            "category": "agent_kind_not_allowed",
                            "message": (
                                f"agent {resolved.name!r} contract does not allow "
                                f"action kind {action.kind.value}"
                            ),
                        }
                    )
        if (
            control.daily_cost_ceiling_minor is not None
            and projected > control.daily_cost_ceiling_minor
        ):
            problems.append(
                {
                    "category": "daily_cost_ceiling_exceeded",
                    "message": (
                        f"projected daily executive cost {projected} exceeds the "
                        f"daily ceiling {control.daily_cost_ceiling_minor}"
                    ),
                }
            )
        return problems

    @staticmethod
    async def plan_requires_approval(
        session: AsyncSession, plan: ExecutivePlan, control: ExecutiveControl
    ) -> bool:
        if control.mode == ExecutiveControlMode.HUMAN_APPROVAL_ALL:
            return True
        return any(
            action.autonomy_class == AutonomyClass.APPROVAL_REQUIRED
            and action.status == ExecutiveActionState.PENDING
            for action in plan.actions
        )

    # ------------------------------------------------------------------
    # Approval submission and cancellation
    # ------------------------------------------------------------------
    @staticmethod
    async def submit_plan(
        session: AsyncSession, *, principal: Principal, plan_id: UUID
    ) -> ExecutivePlan:
        plan = await ExecutiveService.get_plan(session, principal.tenant_id, plan_id)
        if plan.status != ExecutivePlanState.DRAFT:
            raise ConflictError(
                f"only a draft plan can be submitted; this plan is {plan.status.value}"
            )
        problems = await ExecutiveService.validate_plan(session, plan)
        if problems:
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="executive.plan_validation_failed",
                resource_type="executive_plan",
                resource_id=str(plan.id),
                outcome=AuditOutcome.DENIED,
                event_data={"problems": problems},
            )
            await session.commit()
            raise ClipOSError(
                "executive_plan_invalid",
                "plan failed validation and cannot be submitted",
                status_code=422,
                details={"problems": problems},
            )
        control = await ExecutiveService.get_or_create_control(session, principal.tenant_id)
        if await ExecutiveService.plan_requires_approval(session, plan, control):
            plan_id = plan.id
            approval = await ApprovalService.create(
                session,
                principal=principal,
                request=ApprovalCreate(
                    action_class=ActionClass.INTERNAL_REVIEW,
                    action_type="executive_plan.execute",
                    resource_type="executive_plan",
                    resource_id=str(plan_id),
                    rationale=(
                        f"Execute executive plan {plan.title!r} "
                        f"({len(plan.actions)} internal actions, estimated "
                        f"{plan.total_estimated_cost_minor} budget minor units). "
                        "All actions are internal and reversible; the registry "
                        "contains no external action."
                    ),
                    action_payload={
                        "plan_id": str(plan_id),
                        "action_count": len(plan.actions),
                        "total_estimated_cost_minor": plan.total_estimated_cost_minor,
                    },
                ),
            )
            # ApprovalService.create commits, which expires the plan
            # instance; re-fetch before mutating it.
            plan = await ExecutiveService.get_plan(session, principal.tenant_id, plan_id)
            plan.approval_request_id = approval.id
            plan.status = ExecutivePlanState.PENDING_APPROVAL
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="executive.plan_submitted",
                resource_type="executive_plan",
                resource_id=str(plan.id),
                event_data={"approval_request_id": str(approval.id)},
            )
            await session.commit()
        else:
            plan.status = ExecutivePlanState.APPROVED
            plan.approved_by_user_id = principal.user_id
            plan.approved_at = datetime.now(UTC)
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="executive.plan_auto_approved",
                resource_type="executive_plan",
                resource_id=str(plan.id),
                event_data={
                    "reason": (
                        "all actions are auto-internal and inside every ceiling; "
                        "no approval-required action is present"
                    )
                },
            )
            await session.commit()
        await session.refresh(plan, attribute_names=["actions"])
        return plan

    @staticmethod
    async def cancel_plan(
        session: AsyncSession, *, principal: Principal, plan_id: UUID
    ) -> ExecutivePlan:
        plan = await ExecutiveService.get_plan(session, principal.tenant_id, plan_id)
        if plan.status not in {
            ExecutivePlanState.DRAFT,
            ExecutivePlanState.PENDING_APPROVAL,
            ExecutivePlanState.APPROVED,
            ExecutivePlanState.PAUSED,
        }:
            raise ConflictError(f"a {plan.status.value} plan cannot be cancelled")
        plan.status = ExecutivePlanState.CANCELLED
        for action in plan.actions:
            if action.status == ExecutiveActionState.PENDING:
                action.status = ExecutiveActionState.SKIPPED
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.plan_cancelled",
            resource_type="executive_plan",
            resource_id=str(plan.id),
        )
        await session.commit()
        await session.refresh(plan, attribute_names=["actions"])
        return plan

    # ------------------------------------------------------------------
    # Execution queueing (the engine itself is in executive_processing)
    # ------------------------------------------------------------------
    @staticmethod
    async def queue_execution(
        session: AsyncSession,
        *,
        principal: Principal,
        plan_id: UUID,
        queue: QueueBroker,
    ) -> tuple[ExecutivePlan, BackgroundJob]:
        plan = await ExecutiveService.get_plan(session, principal.tenant_id, plan_id)
        control = await ExecutiveService.get_or_create_control(session, principal.tenant_id)
        if control.mode == ExecutiveControlMode.PAUSED:
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="executive.execution_blocked",
                resource_type="executive_plan",
                resource_id=str(plan.id),
                outcome=AuditOutcome.DENIED,
                event_data={"reason": "tenant_executive_paused"},
            )
            await session.commit()
            raise ClipOSError(
                "executive_paused",
                "this tenant's executive is paused (kill switch); resume it "
                "before queueing execution",
                status_code=409,
            )
        if plan.status == ExecutivePlanState.PENDING_APPROVAL:
            approval = (
                await session.get(ApprovalRequest, plan.approval_request_id)
                if plan.approval_request_id
                else None
            )
            if approval is None or approval.status != ApprovalStatus.APPROVED:
                raise ClipOSError(
                    "executive_approval_pending",
                    "this plan is waiting for a human approval decision through "
                    "the approvals system",
                    status_code=409,
                )
            plan.status = ExecutivePlanState.APPROVED
            plan.approved_by_user_id = approval.decided_by_user_id
            plan.approved_at = approval.decided_at
        if plan.status == ExecutivePlanState.DRAFT:
            # An all-auto plan inside every ceiling may be executed
            # directly: executing records the same auto-approval submit
            # would have recorded. Anything else must go through submit
            # and the approvals system first.
            problems = await ExecutiveService.validate_plan(session, plan)
            if problems:
                raise ClipOSError(
                    "executive_plan_invalid",
                    "plan failed validation and cannot be executed",
                    status_code=422,
                    details={"problems": problems},
                )
            if await ExecutiveService.plan_requires_approval(session, plan, control):
                raise ClipOSError(
                    "executive_approval_required",
                    "this plan contains approval-required actions (or the tenant "
                    "is in human-approval-all mode); submit it for approval first",
                    status_code=409,
                )
            plan.status = ExecutivePlanState.APPROVED
            plan.approved_by_user_id = principal.user_id
            plan.approved_at = datetime.now(UTC)
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="executive.plan_auto_approved",
                resource_type="executive_plan",
                resource_id=str(plan.id),
                event_data={"reason": "auto-approved at execution queueing"},
            )
        if plan.status not in {ExecutivePlanState.APPROVED, ExecutivePlanState.PAUSED}:
            raise ConflictError(
                f"a {plan.status.value} plan cannot be executed; submit it for approval first"
            )
        if plan.approval_request_id is not None:
            approval = await session.get(ApprovalRequest, plan.approval_request_id)
            if approval is None or approval.status != ApprovalStatus.APPROVED:
                raise ClipOSError(
                    "executive_approval_invalid",
                    "the plan's approval is no longer approved, so it cannot execute",
                    status_code=409,
                )
        elif await ExecutiveService.plan_requires_approval(session, plan, control):
            raise ClipOSError(
                "executive_approval_required",
                "this plan contains approval-required actions (or the tenant is "
                "in human-approval-all mode) and has no approved approval "
                "request; submit it first",
                status_code=409,
            )
        problems = await ExecutiveService.validate_plan(session, plan)
        if problems:
            raise ClipOSError(
                "executive_plan_invalid",
                "plan failed validation and cannot be executed",
                status_code=422,
                details={"problems": problems},
            )
        job, _created = await JobService.create(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            kind=EXECUTIVE_JOB_KIND,
            idempotency_key=f"executive-plan-{plan.id}-{uuid4().hex}",
            payload={"plan_id": str(plan.id)},
            max_attempts=3,
        )
        # JobService.create commits, expiring the plan instance; keep
        # the id in a local and only assign (never read) attributes.
        plan_id = plan.id
        plan.execution_job_id = job.id
        plan.execution_requested_by_user_id = principal.user_id
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.plan_execution_queued",
            resource_type="executive_plan",
            resource_id=str(plan_id),
            event_data={"job_id": str(job.id)},
        )
        await session.commit()
        await queue.enqueue(QueueMessage(job_id=job.id, tenant_id=job.tenant_id, kind=job.kind))
        plan = await ExecutiveService.get_plan(session, principal.tenant_id, plan_id)
        return plan, job

    # ------------------------------------------------------------------
    # Deterministic planner v1
    # ------------------------------------------------------------------
    @staticmethod
    async def gather_evidence(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
        """Read trusted state for the planner, each section timestamped.

        Everything here is a stored record: analytics runs and
        snapshots, opportunity rows and scores, CRM pipeline rows,
        usage cost events and job states. Free text inside those
        records (titles, notes) is carried as data only — the planner
        never parses it as an instruction, so a hostile string in an
        opportunity title can at most appear verbatim inside this
        evidence JSON; it cannot add an action to a plan.
        """
        now = datetime.now(UTC)

        def stamp(section: dict[str, Any]) -> dict[str, Any]:
            return {"as_of": now.isoformat(), **section}

        latest_run = await session.scalar(
            select(AnalyticsCollectionRun)
            .where(AnalyticsCollectionRun.tenant_id == tenant_id)
            .order_by(desc(AnalyticsCollectionRun.created_at))
            .limit(1)
        )
        latest_snapshot_at = await session.scalar(
            select(func.max(AnalyticsMetricSnapshot.fetched_at)).where(
                AnalyticsMetricSnapshot.tenant_id == tenant_id
            )
        )
        proposed_recommendations = int(
            await session.scalar(
                select(func.count(Recommendation.id)).where(
                    Recommendation.tenant_id == tenant_id,
                    Recommendation.state == RecommendationState.PROPOSED,
                )
            )
            or 0
        )
        succeeded_publications = int(
            await session.scalar(
                select(func.count(Publication.id)).where(
                    Publication.tenant_id == tenant_id,
                    Publication.state == PublishState.SUCCEEDED,
                )
            )
            or 0
        )
        collection_age_hours: float | None = None
        if latest_run is not None:
            reference = latest_run.finished_at or latest_run.created_at
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=UTC)
            collection_age_hours = round((now - reference).total_seconds() / 3600, 2)
        analytics = stamp(
            {
                "latest_collection_run": (
                    {
                        "id": str(latest_run.id),
                        "state": latest_run.state.value,
                        "adapter_mode": latest_run.adapter_mode.value,
                        "finished_at": (
                            latest_run.finished_at.isoformat() if latest_run.finished_at else None
                        ),
                    }
                    if latest_run is not None
                    else None
                ),
                "collection_age_hours": collection_age_hours,
                "latest_snapshot_fetched_at": (
                    latest_snapshot_at.isoformat() if latest_snapshot_at else None
                ),
                "proposed_recommendations": proposed_recommendations,
                "succeeded_publications": succeeded_publications,
            }
        )

        opportunity_counts: dict[str, int] = {}
        for state_value, count in (
            await session.execute(
                select(Opportunity.state, func.count(Opportunity.id))
                .where(Opportunity.tenant_id == tenant_id)
                .group_by(Opportunity.state)
            )
        ).all():
            key = (
                state_value.value if isinstance(state_value, OpportunityState) else str(state_value)
            )
            opportunity_counts[key] = int(count)
        top_opportunity = await session.scalar(
            select(Opportunity)
            .where(
                Opportunity.tenant_id == tenant_id,
                Opportunity.overall_score.is_not(None),
                Opportunity.overall_score >= _HIGH_SCORE,
                Opportunity.state.in_([OpportunityState.NEW, OpportunityState.SHORTLISTED]),
            )
            .order_by(desc(Opportunity.overall_score))
            .limit(1)
        )
        sources = list(
            (
                await session.scalars(
                    select(OpportunitySource)
                    .where(
                        OpportunitySource.tenant_id == tenant_id,
                        OpportunitySource.status == OpportunitySourceStatus.ENABLED,
                    )
                    .order_by(OpportunitySource.created_at)
                )
            ).all()
        )
        stale_sources: list[dict[str, Any]] = []
        seed_sources: list[dict[str, Any]] = []
        for source in sources:
            last_synced = source.last_synced_at
            if last_synced is not None and last_synced.tzinfo is None:
                last_synced = last_synced.replace(tzinfo=UTC)
            entry = {
                "id": str(source.id),
                "name": source.name,
                "kind": source.kind.value,
                "last_synced_at": last_synced.isoformat() if last_synced else None,
            }
            if source.kind == OpportunitySourceKind.SEED_LIST:
                # Seed lists import operator-supplied records; the
                # planner cannot invent the payload, so it never
                # proposes their sync.
                seed_sources.append(entry)
            elif last_synced is None or now - last_synced >= _STALE_AFTER:
                stale_sources.append(entry)
        opportunities = stamp(
            {
                "counts_by_state": opportunity_counts,
                "top_opportunity": (
                    {
                        "id": str(top_opportunity.id),
                        "title": top_opportunity.title,
                        "overall_score": top_opportunity.overall_score,
                        "state": top_opportunity.state.value,
                    }
                    if top_opportunity is not None
                    else None
                ),
                "stale_sources": stale_sources,
                "seed_list_sources_require_operator_payload": seed_sources,
            }
        )

        stage_counts: dict[str, int] = {}
        for stage_value, count in (
            await session.execute(
                select(CrmAccount.stage, func.count(CrmAccount.id))
                .where(CrmAccount.tenant_id == tenant_id)
                .group_by(CrmAccount.stage)
            )
        ).all():
            key = stage_value.value if hasattr(stage_value, "value") else str(stage_value)
            stage_counts[key] = int(count)
        overdue_tasks = list(
            (
                await session.scalars(
                    select(CrmTask)
                    .where(
                        CrmTask.tenant_id == tenant_id,
                        CrmTask.status == CrmTaskState.OPEN,
                        CrmTask.due_at.is_not(None),
                        CrmTask.due_at < now,
                    )
                    .order_by(CrmTask.due_at)
                    .limit(10)
                )
            ).all()
        )
        crm = stamp(
            {
                "accounts_by_stage": stage_counts,
                "overdue_tasks": [
                    {
                        "id": str(task.id),
                        "account_id": str(task.account_id),
                        "title": task.title,
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                    }
                    for task in overdue_tasks
                ],
            }
        )

        cost = await AnalyticsService.cost_summary(session, tenant_id)
        provider_today = await session.scalar(
            select(func.coalesce(func.sum(UsageCostEvent.estimated_cost), 0)).where(
                UsageCostEvent.tenant_id == tenant_id,
                UsageCostEvent.created_at >= _today_start(now),
            )
        )
        control = await ExecutiveService.get_or_create_control(session, tenant_id)
        costs = stamp(
            {
                "provider_cost_total": str(cost["total_estimated_cost"]),
                "provider_cost_currency": cost["currency"],
                "provider_cost_today": str(provider_today or 0),
                "executive_incurred_today_minor": (
                    await ExecutiveService.executive_incurred_on(
                        session, tenant_id, _today_start(now)
                    )
                ),
                "daily_cost_ceiling_minor": control.daily_cost_ceiling_minor,
                "per_action_cost_ceiling_minor": control.per_action_cost_ceiling_minor,
            }
        )

        job_counts: dict[str, int] = {}
        for job_state_value, job_count in (
            await session.execute(
                select(BackgroundJob.state, func.count(BackgroundJob.id))
                .where(BackgroundJob.tenant_id == tenant_id)
                .group_by(BackgroundJob.state)
            )
        ).all():
            key = (
                job_state_value.value
                if isinstance(job_state_value, JobState)
                else str(job_state_value)
            )
            job_counts[key] = int(job_count)
        queue = stamp({"jobs_by_state": job_counts})

        return {
            "planner_version": PLANNER_VERSION,
            "generated_at": now.isoformat(),
            "analytics": analytics,
            "opportunities": opportunities,
            "crm": crm,
            "costs": costs,
            "queue": queue,
            "controls": {"mode": control.mode.value},
        }

    @staticmethod
    async def run_planner(session: AsyncSession, *, principal: Principal) -> ExecutivePlan:
        """Build one plan from trusted state with fixed, cited rules.

        Rules v1 (each firing is named in the plan rationale):
        1. Analytics collection is stale (>24h or never ran)
           → ``run_analytics_collection``.
        2. Succeeded publications exist but no PROPOSED recommendation
           is waiting → ``generate_recommendations``.
        3. An enabled non-seed source is stale → ``sync_opportunity_source``
           (at most three).
        4. An account has an overdue open task → ``create_crm_draft``
           follow-up (at most three distinct accounts).
        5. Always close with ``noop_report`` so every plan ends in an
           explicit, harmless status compilation.
        """
        evidence = await ExecutiveService.gather_evidence(session, principal.tenant_id)
        actions: list[dict[str, Any]] = []
        rationale_lines: list[str] = []

        analytics = evidence["analytics"]
        age = analytics["collection_age_hours"]
        if age is None or age >= _STALE_AFTER.total_seconds() / 3600:
            actions.append(
                {"kind": ExecutiveActionKind.RUN_ANALYTICS_COLLECTION.value, "payload": {}}
            )
            rationale_lines.append(
                "rule collection_stale_v1: latest analytics collection is "
                + ("missing" if age is None else f"{age}h old")
                + "; a refresh is internal and read-only."
            )
        if analytics["succeeded_publications"] > 0 and analytics["proposed_recommendations"] == 0:
            actions.append(
                {"kind": ExecutiveActionKind.GENERATE_RECOMMENDATIONS.value, "payload": {}}
            )
            rationale_lines.append(
                "rule recommendations_empty_v1: succeeded publications exist and "
                "no PROPOSED recommendation is waiting for review."
            )
        for source in evidence["opportunities"]["stale_sources"][:3]:
            actions.append(
                {
                    "kind": ExecutiveActionKind.SYNC_OPPORTUNITY_SOURCE.value,
                    "payload": {"source_id": source["id"]},
                }
            )
            rationale_lines.append(
                f"rule source_stale_v1: source {source['name']!r} "
                f"({source['kind']}) last synced {source['last_synced_at'] or 'never'}; "
                "sync imports records for human review and contacts nobody."
            )
        seen_accounts: set[str] = set()
        for task in evidence["crm"]["overdue_tasks"]:
            account_id = task["account_id"]
            if account_id in seen_accounts:
                continue
            seen_accounts.add(account_id)
            actions.append(
                {
                    "kind": ExecutiveActionKind.CREATE_CRM_DRAFT.value,
                    "payload": {
                        "account_id": account_id,
                        "kind": "follow_up",
                        "title": f"Follow-up: {task['title']}"[:300],
                    },
                }
            )
            rationale_lines.append(
                f"rule overdue_task_v1: task {task['title']!r} was due "
                f"{task['due_at']}; a follow-up draft is prepared for human "
                "review (drafts cannot be sent)."
            )
            if len(seen_accounts) >= 3:
                break
        actions.append(
            {
                "kind": ExecutiveActionKind.NOOP_REPORT.value,
                "payload": {"note": f"{PLANNER_VERSION} closing status report"},
            }
        )
        rationale_lines.append(
            "rule closing_report_v1: every planner pass ends with a status report."
        )

        now = datetime.now(UTC)
        plan = await ExecutiveService.create_plan(
            session,
            principal=principal,
            title=f"Executive plan {now:%Y-%m-%d} — {len(actions)} actions",
            objective=(
                "Deterministic executive pass: refresh stale internal state, "
                "prepare follow-ups for human review, and report. Internal, "
                "reversible actions only — nothing is published, sent, "
                "charged or deleted."
            ),
            actions=actions,
            rationale="\n".join(rationale_lines),
            evidence=evidence,
            created_by="system",
        )
        return plan

    # ------------------------------------------------------------------
    # Self-improvement proposals (decision records; never self-applying)
    # ------------------------------------------------------------------
    @staticmethod
    async def create_proposal(
        session: AsyncSession,
        *,
        principal: Principal,
        kind: Any,
        title: str,
        summary: str,
        change_payload: dict[str, Any],
        version: int,
    ) -> ExecutiveProposal:
        proposal = ExecutiveProposal(
            tenant_id=principal.tenant_id,
            kind=kind,
            title=title,
            summary=summary,
            change_payload=change_payload,
            state=ExecutiveProposalState.PROPOSED,
            version=version,
            created_by=str(principal.user_id),
            created_by_user_id=principal.user_id,
        )
        session.add(proposal)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.proposal_created",
            resource_type="executive_proposal",
            resource_id=str(proposal.id),
            event_data={"kind": kind.value, "version": version},
        )
        await session.commit()
        await session.refresh(proposal)
        return proposal

    @staticmethod
    async def list_proposals(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        state: ExecutiveProposalState | None = None,
        limit: int = 100,
    ) -> list[ExecutiveProposal]:
        query = select(ExecutiveProposal).where(ExecutiveProposal.tenant_id == tenant_id)
        if state is not None:
            query = query.where(ExecutiveProposal.state == state)
        rows = await session.scalars(
            query.order_by(desc(ExecutiveProposal.created_at)).limit(min(limit, 200))
        )
        return list(rows.all())

    @staticmethod
    async def get_proposal(
        session: AsyncSession, tenant_id: UUID, proposal_id: UUID
    ) -> ExecutiveProposal:
        proposal = await session.scalar(
            select(ExecutiveProposal).where(
                ExecutiveProposal.id == proposal_id,
                ExecutiveProposal.tenant_id == tenant_id,
            )
        )
        if proposal is None:
            raise NotFoundError("executive proposal")
        return proposal

    @staticmethod
    async def review_proposal(
        session: AsyncSession,
        *,
        principal: Principal,
        proposal_id: UUID,
        decision: ExecutiveProposalState,
        note: str | None,
    ) -> ExecutiveProposal:
        """Record a human decision on a proposal.

        Approval changes only this row. There is deliberately no apply
        step: an approved proposal takes effect only when a human makes
        the corresponding configuration change separately, under its
        own audit trail.
        """
        principal.require(Role.REVIEWER)
        if decision not in {
            ExecutiveProposalState.APPROVED,
            ExecutiveProposalState.REJECTED,
        }:
            raise ClipOSError(
                "executive_proposal_decision_invalid",
                "a proposal review decision must be approved or rejected",
                status_code=422,
            )
        proposal = await ExecutiveService.get_proposal(session, principal.tenant_id, proposal_id)
        if proposal.state != ExecutiveProposalState.PROPOSED:
            raise ConflictError(f"a {proposal.state.value} proposal cannot be reviewed again")
        proposal.state = decision
        proposal.reviewed_by_user_id = principal.user_id
        proposal.reviewed_at = datetime.now(UTC)
        proposal.review_note = note
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action=f"executive.proposal_{decision.value}",
            resource_type="executive_proposal",
            resource_id=str(proposal.id),
            event_data={"applied": False, "note": note},
        )
        await session.commit()
        await session.refresh(proposal)
        return proposal
