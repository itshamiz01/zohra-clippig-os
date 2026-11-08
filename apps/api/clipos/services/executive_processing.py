from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import (
    AgentRunState,
    ApprovalStatus,
    AuditOutcome,
    ExecutiveActionState,
    ExecutiveControlMode,
    ExecutivePlanState,
    JobState,
    MembershipStatus,
    OpportunitySyncState,
)
from clipos.core.errors import ClipOSError
from clipos.core.security import Principal
from clipos.gateways.analytics import AnalyticsAdapterRegistry
from clipos.gateways.opportunities import OpportunityAdapterRegistry
from clipos.models import (
    AgentRun,
    ApprovalRequest,
    BackgroundJob,
    ExecutiveAction,
    ExecutivePlan,
    SystemControl,
    TenantMembership,
    User,
)
from clipos.queue import QueueBroker, QueueMessage
from clipos.services.analytics import AnalyticsService
from clipos.services.audit import AuditService
from clipos.services.crm import CrmService
from clipos.services.executive import EXECUTIVE_JOB_KIND, ExecutiveService
from clipos.services.executive_actions import (
    ACTION_REGISTRY,
    CreateCrmDraftPayload,
    CreateInternalTaskPayload,
    GenerateRecommendationsPayload,
    NoopReportPayload,
    RunAnalyticsCollectionPayload,
    SyncOpportunitySourcePayload,
    validate_action_payload,
)
from clipos.services.opportunities import OpportunityService


async def dispatch_with_timeout(
    awaitable_factory: Callable[[], Awaitable[dict[str, Any]]], timeout_seconds: float
) -> dict[str, Any]:
    """Run one dispatch under the agent contract's timeout.

    The domain call runs in its own task so a timeout cancels it rather
    than merely abandoning the wait; the caller records the run as
    TIMED_OUT and halts the plan.
    """
    async with asyncio.timeout(timeout_seconds):
        return await awaitable_factory()


class ExecutiveProcessor:
    """Executes queued executive plans, one internal action at a time.

    Execution is deliberately conservative:

    - everything the write path validated is re-validated here — plan
      payloads against the closed registry schemas, autonomy classes,
      ceilings, agent contracts, the linked approval's live status and
      both pause layers (tenant kill switch and platform SystemControl);
    - the pause layers are re-read between every pair of actions, so
      flipping the kill switch mid-plan stops it at the next boundary
      with the remaining actions left PENDING and resumable;
    - the first failed action halts the plan: later actions are marked
      SKIPPED, never attempted;
    - dispatch goes through the existing domain services with a
      principal reconstructed from the user who requested execution
      (mirroring the Phase 6 publish processor), so domain role checks
      and audit attribution stay real.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        analytics_registry: AnalyticsAdapterRegistry,
        opportunity_registry: OpportunityAdapterRegistry,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.analytics_registry = analytics_registry
        self.opportunity_registry = opportunity_registry
        self.settings = settings

    # ------------------------------------------------------------------
    # Job entry point
    # ------------------------------------------------------------------
    async def process_job(self, message: QueueMessage) -> None:
        async with self.session_factory() as session:
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == message.job_id,
                    BackgroundJob.tenant_id == message.tenant_id,
                    BackgroundJob.kind == EXECUTIVE_JOB_KIND,
                )
                .with_for_update()
            )
            if job is None or job.state != JobState.QUEUED:
                return
            plan = await session.scalar(
                select(ExecutivePlan).where(
                    ExecutivePlan.execution_job_id == job.id,
                    ExecutivePlan.tenant_id == message.tenant_id,
                )
            )
            if plan is None:
                job.state = JobState.FAILED
                job.failure_category = "executive_plan_missing"
                job.failure_message = "executive background job has no plan record"
                job.finished_at = datetime.now(UTC)
                job.heartbeat_at = job.finished_at
                await session.commit()
                return
            job.state = JobState.RUNNING
            job.attempts += 1
            job.started_at = datetime.now(UTC)
            job.heartbeat_at = job.started_at
            plan_id = plan.id
            await session.commit()

        plan = await self.execute_plan(plan_id, message.tenant_id)

        async with self.session_factory() as session:
            job = await session.get(BackgroundJob, message.job_id)
            if job is None:
                return
            if plan.status == ExecutivePlanState.COMPLETED:
                job.state = JobState.SUCCEEDED
            else:
                job.state = JobState.FAILED
            job.result = {
                "plan_id": str(plan.id),
                "plan_status": plan.status.value,
                "actions_succeeded": sum(
                    1 for a in plan.actions if a.status == ExecutiveActionState.SUCCEEDED
                ),
                "actions_failed": sum(
                    1 for a in plan.actions if a.status == ExecutiveActionState.FAILED
                ),
                "actions_skipped": sum(
                    1 for a in plan.actions if a.status == ExecutiveActionState.SKIPPED
                ),
            }
            job.failure_category = plan.failure_category
            job.failure_message = plan.failure_message
            job.finished_at = datetime.now(UTC)
            job.heartbeat_at = job.finished_at
            await session.commit()

    # ------------------------------------------------------------------
    # Execution engine
    # ------------------------------------------------------------------
    async def execute_plan(self, plan_id: UUID, tenant_id: UUID) -> ExecutivePlan:
        async with self.session_factory() as session:
            plan = await session.scalar(
                select(ExecutivePlan)
                .where(ExecutivePlan.id == plan_id, ExecutivePlan.tenant_id == tenant_id)
                .with_for_update()
            )
            if plan is None:
                raise ClipOSError("not_found", "executive plan not found", status_code=404)
            await session.refresh(plan, attribute_names=["actions"])
            if plan.status not in {
                ExecutivePlanState.APPROVED,
                ExecutivePlanState.PAUSED,
                ExecutivePlanState.EXECUTING,
            }:
                return plan

            blocked = await self._pause_block(session, plan)
            if blocked is not None:
                plan.status = ExecutivePlanState.PAUSED
                plan.failure_category = blocked
                plan.failure_message = (
                    "execution is paused by the tenant kill switch"
                    if blocked == "tenant_executive_paused"
                    else "execution is paused by the platform system control"
                )
                await session.commit()
                return plan

            if not self.settings.executive_enabled:
                plan.status = ExecutivePlanState.FAILED
                plan.failure_category = "executive_disabled"
                plan.failure_message = (
                    "the executive layer is disabled by configuration "
                    "(CLIPOS_EXECUTIVE_ENABLED=false)"
                )
                plan.finished_at = datetime.now(UTC)
                await session.commit()
                return plan

            gate_failure = await self._execution_gate(session, plan)
            if gate_failure is not None:
                category, message, target = gate_failure
                plan.status = target
                plan.failure_category = category
                plan.failure_message = message
                plan.finished_at = datetime.now(UTC)
                await session.commit()
                return plan

            principal = await self._principal_for_plan(session, plan)
            if principal is None:
                plan.status = ExecutivePlanState.FAILED
                plan.failure_category = "executor_authorization_revoked"
                plan.failure_message = (
                    "the user who requested execution is no longer an active "
                    "tenant member, so domain actions cannot be attributed"
                )
                plan.finished_at = datetime.now(UTC)
                await session.commit()
                return plan

            plan.status = ExecutivePlanState.EXECUTING
            if plan.started_at is None:
                plan.started_at = datetime.now(UTC)
            plan.failure_category = None
            plan.failure_message = None
            AuditService.record(
                session,
                tenant_id=plan.tenant_id,
                actor_user_id=principal.user_id,
                action="executive.plan_execution_started",
                resource_type="executive_plan",
                resource_id=str(plan.id),
                event_data={"action_count": len(plan.actions)},
            )
            await session.commit()

        halted = False
        while True:
            async with self.session_factory() as session:
                plan = await session.scalar(
                    select(ExecutivePlan)
                    .where(ExecutivePlan.id == plan_id, ExecutivePlan.tenant_id == tenant_id)
                    .with_for_update()
                )
                if plan is None:  # pragma: no cover - plan was loaded above
                    raise ClipOSError("not_found", "executive plan not found", status_code=404)
                await session.refresh(plan, attribute_names=["actions"])
                blocked = await self._pause_block(session, plan)
                if blocked is not None:
                    plan.status = ExecutivePlanState.PAUSED
                    plan.failure_category = blocked
                    plan.failure_message = (
                        "execution paused between actions by a pause layer; "
                        "remaining actions stay pending and resumable"
                    )
                    AuditService.record(
                        session,
                        tenant_id=plan.tenant_id,
                        actor_user_id=None,
                        action="executive.plan_paused_mid_execution",
                        resource_type="executive_plan",
                        resource_id=str(plan.id),
                        event_data={"reason": blocked},
                    )
                    await session.commit()
                    return plan
                pending = [
                    action
                    for action in sorted(plan.actions, key=lambda item: item.sequence)
                    if action.status == ExecutiveActionState.PENDING
                ]
                if not pending:
                    plan.status = (
                        ExecutivePlanState.FAILED if halted else ExecutivePlanState.COMPLETED
                    )
                    plan.finished_at = datetime.now(UTC)
                    AuditService.record(
                        session,
                        tenant_id=plan.tenant_id,
                        actor_user_id=None,
                        action=("executive.plan_failed" if halted else "executive.plan_completed"),
                        resource_type="executive_plan",
                        resource_id=str(plan.id),
                    )
                    await session.commit()
                    return plan
                action = pending[0]
                failure = await self._execute_action(session, plan, action, principal)
                await session.commit()
                if failure:
                    halted = True
                    # Mark every later pending action SKIPPED and loop
                    # once more to finalise the plan as FAILED.
                    async with self.session_factory() as skip_session:
                        stored = await skip_session.scalar(
                            select(ExecutivePlan)
                            .where(
                                ExecutivePlan.id == plan_id,
                                ExecutivePlan.tenant_id == tenant_id,
                            )
                            .with_for_update()
                        )
                        if stored is not None:
                            await skip_session.refresh(stored, attribute_names=["actions"])
                            for later in stored.actions:
                                if later.status == ExecutiveActionState.PENDING:
                                    later.status = ExecutiveActionState.SKIPPED
                            await skip_session.commit()

    async def _pause_block(self, session: AsyncSession, plan: ExecutivePlan) -> str | None:
        control = await ExecutiveService.get_or_create_control(session, plan.tenant_id)
        if control.mode == ExecutiveControlMode.PAUSED:
            return "tenant_executive_paused"
        platform = await session.scalar(
            select(SystemControl.paused).where(SystemControl.key == "global")
        )
        if platform:
            return "platform_paused"
        return None

    async def _execution_gate(
        self, session: AsyncSession, plan: ExecutivePlan
    ) -> tuple[str, str, ExecutivePlanState] | None:
        """Re-validate approval and plan contents at execution time."""
        control = await ExecutiveService.get_or_create_control(session, plan.tenant_id)
        if plan.approval_request_id is not None:
            approval = await session.get(ApprovalRequest, plan.approval_request_id)
            if approval is None or approval.status != ApprovalStatus.APPROVED:
                return (
                    "approval_not_valid",
                    "the plan's approval request is no longer approved",
                    ExecutivePlanState.FAILED,
                )
            expires_at = approval.expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= datetime.now(UTC):
                    return (
                        "approval_not_valid",
                        "the plan's approval request has expired",
                        ExecutivePlanState.FAILED,
                    )
        elif await ExecutiveService.plan_requires_approval(session, plan, control):
            return (
                "approval_required",
                "the plan requires a human approval that is not on record "
                "(approval-required actions present, or human-approval-all mode)",
                ExecutivePlanState.PENDING_APPROVAL,
            )
        problems = await ExecutiveService.validate_plan(session, plan)
        if problems:
            first = problems[0]
            return (
                str(first.get("category", "plan_validation_failed")),
                f"plan failed execution-time validation: {first.get('message', '')}",
                ExecutivePlanState.FAILED,
            )
        return None

    async def _principal_for_plan(
        self, session: AsyncSession, plan: ExecutivePlan
    ) -> Principal | None:
        user_id = plan.execution_requested_by_user_id or plan.created_by_user_id
        if user_id is None:
            return None
        row = (
            await session.execute(
                select(User, TenantMembership)
                .join(TenantMembership, TenantMembership.user_id == User.id)
                .where(
                    User.id == user_id,
                    User.is_active.is_(True),
                    TenantMembership.tenant_id == plan.tenant_id,
                    TenantMembership.status == MembershipStatus.ACTIVE,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        user, membership = row
        return Principal(
            user_id=user.id,
            tenant_id=plan.tenant_id,
            role=membership.role,
            email=user.email,
            is_platform_admin=user.is_platform_admin,
        )

    # ------------------------------------------------------------------
    # One action
    # ------------------------------------------------------------------
    async def _execute_action(
        self,
        session: AsyncSession,
        plan: ExecutivePlan,
        action: ExecutiveAction,
        principal: Principal,
    ) -> bool:
        """Execute one pending action inside ``session``.

        Returns True when the action failed and the plan must halt.
        The caller commits.
        """
        spec = ACTION_REGISTRY[action.kind]
        try:
            validate_action_payload(action.kind, action.payload)
        except ClipOSError as exc:
            return await self._fail_action(
                session,
                plan=plan,
                action=action,
                run=None,
                category=exc.code,
                message=exc.message,
            )
        resolved = await ExecutiveService.resolve_agent(session, plan.tenant_id, spec.agent_role)
        contract = resolved.contract
        if resolved.definition is not None and action.kind not in contract.allowed_action_kinds:
            return await self._fail_action(
                session,
                plan=plan,
                action=action,
                run=None,
                category="agent_kind_not_allowed",
                message=(
                    f"agent {resolved.name!r} contract does not allow "
                    f"action kind {action.kind.value}"
                ),
            )
        succeeded_for_agent = await session.scalars(
            select(AgentRun).where(
                AgentRun.tenant_id == plan.tenant_id,
                AgentRun.plan_id == plan.id,
                AgentRun.agent_role == resolved.role,
                AgentRun.status == AgentRunState.SUCCEEDED,
            )
        )
        steps_used_by_agent = len(list(succeeded_for_agent.all()))
        if steps_used_by_agent >= contract.max_steps:
            return await self._fail_action(
                session,
                plan=plan,
                action=action,
                run=None,
                category="agent_step_cap_exceeded",
                message=(
                    f"agent {resolved.name!r} already used {steps_used_by_agent} "
                    f"steps in this plan; its contract cap is {contract.max_steps}"
                ),
            )
        if (
            resolved.definition is not None
            and action.cost_estimate_minor > contract.cost_budget_per_run_minor
        ):
            # A defined agent's budget is literal: a budget of 0 means no
            # costed action may run under it. Only the built-in default
            # (no definition at all) is uncapped.
            return await self._fail_action(
                session,
                plan=plan,
                action=action,
                run=None,
                category="agent_budget_exceeded",
                message=(
                    f"action estimate {action.cost_estimate_minor} exceeds agent "
                    f"{resolved.name!r} per-run budget {contract.cost_budget_per_run_minor}"
                ),
            )

        run = AgentRun(
            tenant_id=plan.tenant_id,
            agent_id=resolved.definition.id if resolved.definition else None,
            agent_name=resolved.name,
            agent_role=resolved.role,
            plan_id=plan.id,
            action_id=action.id,
            status=AgentRunState.RUNNING,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        action.status = ExecutiveActionState.RUNNING
        action.agent_definition_id = resolved.definition.id if resolved.definition else None
        await session.flush()

        try:
            result = await dispatch_with_timeout(
                lambda: self._dispatch_action(
                    session, plan=plan, action=action, principal=principal
                ),
                contract.timeout_seconds,
            )
        except TimeoutError:
            run.status = AgentRunState.TIMED_OUT
            run.failure_category = "agent_timeout"
            run.error = (
                f"dispatch exceeded the agent contract timeout of {contract.timeout_seconds}s"
            )
            run.finished_at = datetime.now(UTC)
            return await self._fail_action(
                session,
                plan=plan,
                action=action,
                run=run,
                category="agent_timeout",
                message=run.error,
            )
        except ClipOSError as exc:
            run.status = AgentRunState.FAILED
            run.failure_category = exc.code
            run.error = exc.message[:500]
            run.finished_at = datetime.now(UTC)
            return await self._fail_action(
                session,
                plan=plan,
                action=action,
                run=run,
                category=exc.code,
                message=exc.message,
            )
        except Exception as exc:  # noqa: BLE001 - domain surprises fail the action, not the worker
            run.status = AgentRunState.FAILED
            run.failure_category = "dispatch_error"
            run.error = f"{type(exc).__name__}: {exc}"[:500]
            run.finished_at = datetime.now(UTC)
            return await self._fail_action(
                session,
                plan=plan,
                action=action,
                run=run,
                category="dispatch_error",
                message=run.error,
            )

        action.status = ExecutiveActionState.SUCCEEDED
        action.result = result
        action.cost_incurred_minor = action.cost_estimate_minor
        action.executed_at = datetime.now(UTC)
        run.status = AgentRunState.SUCCEEDED
        run.steps_used = 1
        run.cost_incurred_minor = action.cost_estimate_minor
        run.finished_at = datetime.now(UTC)
        AuditService.record(
            session,
            tenant_id=plan.tenant_id,
            actor_user_id=principal.user_id,
            action="executive.action_succeeded",
            resource_type="executive_action",
            resource_id=str(action.id),
            event_data={
                "plan_id": str(plan.id),
                "kind": action.kind.value,
                "agent": resolved.name,
                "cost_incurred_minor": action.cost_incurred_minor,
            },
        )
        return False

    async def _fail_action(
        self,
        session: AsyncSession,
        *,
        plan: ExecutivePlan,
        action: ExecutiveAction,
        run: AgentRun | None,
        category: str,
        message: str,
    ) -> bool:
        action.status = ExecutiveActionState.FAILED
        action.failure_category = category
        action.error = message[:500]
        plan.failure_category = category
        plan.failure_message = message[:500]
        AuditService.record(
            session,
            tenant_id=plan.tenant_id,
            actor_user_id=None,
            action="executive.action_failed",
            resource_type="executive_action",
            resource_id=str(action.id),
            outcome=AuditOutcome.FAILURE,
            event_data={"plan_id": str(plan.id), "category": category},
        )
        return True

    # ------------------------------------------------------------------
    # Dispatch: the ONLY place registry kinds meet domain services
    # ------------------------------------------------------------------
    async def _dispatch_action(
        self,
        session: AsyncSession,
        *,
        plan: ExecutivePlan,
        action: ExecutiveAction,
        principal: Principal,
    ) -> dict[str, Any]:
        payload = validate_action_payload(action.kind, action.payload)
        if isinstance(payload, RunAnalyticsCollectionPayload):
            run = await AnalyticsService.collect_metrics_for_tenant(
                session,
                tenant_id=plan.tenant_id,
                requested_by_user_id=principal.user_id,
                registry=self.analytics_registry,
                provider=payload.provider,
            )
            return {
                "collection_run_id": str(run.id),
                "collection_state": run.state.value,
                "adapter_mode": run.adapter_mode.value,
                "publications_considered": run.publications_considered,
                "snapshots_written": run.snapshots_written,
                "metrics_reported": run.metrics_reported,
                "metrics_unavailable": run.metrics_unavailable,
            }
        if isinstance(payload, SyncOpportunitySourcePayload):
            source = await OpportunityService.get_source(session, plan.tenant_id, payload.source_id)
            sync_run = await OpportunityService.sync_source(
                session,
                tenant_id=plan.tenant_id,
                requested_by_user_id=principal.user_id,
                registry=self.opportunity_registry,
                source=source,
                import_payload=payload.records,
            )
            if sync_run.state == OpportunitySyncState.FAILED:
                raise ClipOSError(
                    "opportunity_sync_failed",
                    sync_run.failure_message or "opportunity sync run failed",
                    status_code=409,
                )
            return {
                "sync_run_id": str(sync_run.id),
                "sync_state": sync_run.state.value,
                "adapter_mode": sync_run.adapter_mode.value,
                "records_received": sync_run.records_received,
                "records_created": sync_run.records_created,
                "records_merged": sync_run.records_merged,
                "duplicates_flagged": sync_run.duplicates_flagged,
                "suppressed_skipped": sync_run.suppressed_skipped,
            }
        if isinstance(payload, GenerateRecommendationsPayload):
            created = await AnalyticsService.generate_recommendations(session, principal=principal)
            return {
                "recommendations_created": len(created),
                "recommendation_ids": [str(item.id) for item in created],
            }
        if isinstance(payload, CreateCrmDraftPayload):
            draft = await CrmService.create_draft(
                session,
                principal=principal,
                account_id=payload.account_id,
                kind=payload.kind,
                contact_id=payload.contact_id,
                title=payload.title,
                body=None,
            )
            return {
                "draft_id": str(draft.id),
                "draft_status": draft.status.value,
                "citations": len(draft.evidence.get("citations", [])),
            }
        if isinstance(payload, CreateInternalTaskPayload):
            task = await CrmService.create_task(
                session,
                principal=principal,
                account_id=payload.account_id,
                title=payload.title,
                due_at=payload.due_at,
                assignee_user_id=payload.assignee_user_id,
            )
            return {"task_id": str(task.id), "task_status": task.status.value}
        if isinstance(payload, NoopReportPayload):
            evidence = await ExecutiveService.gather_evidence(session, plan.tenant_id)
            return {
                "note": payload.note,
                "report": {
                    "analytics": evidence["analytics"],
                    "opportunities": evidence["opportunities"],
                    "crm": evidence["crm"],
                    "costs": evidence["costs"],
                    "queue": evidence["queue"],
                },
            }
        raise ClipOSError(  # pragma: no cover - registry and schemas are exhaustive
            "executive_action_unknown",
            f"no dispatch is registered for action kind {action.kind.value}",
            status_code=422,
        )


class ExecutiveWorker:
    """Recovery for executive jobs, mirroring the Phase 7/8 workers.

    Requeueing a stale RUNNING job is safe because execution is
    resumable by construction: actions that already SUCCEEDED are never
    re-run, and the engine re-validates everything before continuing.
    """

    def __init__(
        self,
        *,
        processor: ExecutiveProcessor,
        session_factory: async_sessionmaker[AsyncSession],
        queue: QueueBroker,
        settings: Settings,
    ) -> None:
        self.processor = processor
        self.session_factory = session_factory
        self.queue = queue
        self.settings = settings

    async def recover_queued_job(self) -> QueueMessage | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == EXECUTIVE_JOB_KIND,
                    BackgroundJob.state == JobState.QUEUED,
                )
                .order_by(BackgroundJob.created_at)
                .limit(1)
            )
            if row is not None:
                return QueueMessage(job_id=row.id, tenant_id=row.tenant_id, kind=row.kind)
            stale_before = datetime.now(UTC) - timedelta(
                seconds=self.settings.worker_stale_job_seconds
            )
            stale = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == EXECUTIVE_JOB_KIND,
                    BackgroundJob.state == JobState.RUNNING,
                    BackgroundJob.updated_at < stale_before,
                )
                .order_by(BackgroundJob.updated_at)
                .limit(1)
                .with_for_update()
            )
            if stale is None:
                return None
            stale.state = JobState.QUEUED
            stale.failure_category = "worker_restart_requeued"
            plan = await session.scalar(
                select(ExecutivePlan).where(ExecutivePlan.execution_job_id == stale.id)
            )
            if plan is not None and plan.status == ExecutivePlanState.EXECUTING:
                plan.status = ExecutivePlanState.APPROVED
            await session.commit()
            return QueueMessage(job_id=stale.id, tenant_id=stale.tenant_id, kind=stale.kind)

    def stop(self) -> None:
        pass
