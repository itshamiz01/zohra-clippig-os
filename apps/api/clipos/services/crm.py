from __future__ import annotations

import difflib
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from clipos.core.enums import (
    AuditOutcome,
    CrmStage,
    CrmTaskState,
    MetricAvailability,
    OnboardingState,
    OpportunityHandoffState,
    RevenueKind,
    RevenueState,
    Role,
    SalesAssetKind,
    SalesAssetState,
)
from clipos.core.errors import ClipOSError, ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.gateways.comms import CommsAdapterRegistry
from clipos.gateways.opportunities.normalize import normalize_handle, normalize_url
from clipos.gateways.payments import PaymentsAdapterRegistry
from clipos.models import (
    AnalyticsMetricSnapshot,
    CrmAccount,
    CrmContact,
    CrmPipelineEvent,
    CrmTask,
    OnboardingRecord,
    Opportunity,
    OpportunityHandoff,
    RevenueRecord,
    SalesAssetDraft,
    TenantMembership,
)
from clipos.models.entities import utcnow
from clipos.services.audit import AuditService
from clipos.services.opportunities import OpportunityService

DRAFT_TEMPLATE_VERSION = "sales-draft-v1"
ONBOARDING_CHECKLIST_VERSION = "onboarding-checklist-v1"

# The forward pipeline order. ``rejected`` and ``lost`` are terminal
# side-states reachable from any non-terminal stage; ``retained`` is the
# terminal success state. Moving backwards is allowed only with a
# recorded reason; terminal stages have no outgoing transitions.
_STAGE_ORDER: dict[CrmStage, int] = {
    stage: index
    for index, stage in enumerate(
        [
            CrmStage.PROSPECT,
            CrmStage.QUALIFIED_LEAD,
            CrmStage.CONVERSATION,
            CrmStage.DISCOVERY_CALL,
            CrmStage.PROPOSAL,
            CrmStage.CONTRACT_REVIEW,
            CrmStage.CLIENT,
            CrmStage.CAMPAIGN,
            CrmStage.DELIVERABLE,
            CrmStage.INVOICED,
            CrmStage.RETAINED,
        ]
    )
}
_TERMINAL_STAGES = frozenset({CrmStage.REJECTED, CrmStage.LOST, CrmStage.RETAINED})
_SIDE_STAGES = frozenset({CrmStage.REJECTED, CrmStage.LOST})
_CLIENT_STAGES = frozenset(
    {
        CrmStage.CLIENT,
        CrmStage.CAMPAIGN,
        CrmStage.DELIVERABLE,
        CrmStage.INVOICED,
        CrmStage.RETAINED,
    }
)

_DRAFT_TRANSITIONS: dict[SalesAssetState, frozenset[SalesAssetState]] = {
    SalesAssetState.DRAFT: frozenset({SalesAssetState.IN_REVIEW, SalesAssetState.ARCHIVED}),
    SalesAssetState.IN_REVIEW: frozenset(
        {SalesAssetState.APPROVED, SalesAssetState.REJECTED, SalesAssetState.ARCHIVED}
    ),
    SalesAssetState.APPROVED: frozenset({SalesAssetState.ARCHIVED}),
    SalesAssetState.REJECTED: frozenset({SalesAssetState.ARCHIVED}),
    SalesAssetState.ARCHIVED: frozenset(),
}
_REVIEWER_ONLY_DRAFT_TARGETS = frozenset({SalesAssetState.APPROVED, SalesAssetState.REJECTED})

# Which citation types can support each draft kind. A draft whose stored
# evidence contains none of the required types cannot be created: the
# generator may only assert facts that are cited to stored records.
_REQUIRED_CITATION_TYPES: dict[SalesAssetKind, frozenset[str]] = {
    SalesAssetKind.OUTREACH_MESSAGE: frozenset({"opportunity", "opportunity_handoff"}),
    SalesAssetKind.PROPOSAL_OUTLINE: frozenset(
        {"opportunity", "opportunity_handoff", "revenue_record"}
    ),
    SalesAssetKind.FOLLOW_UP: frozenset(),
    SalesAssetKind.DISCOVERY_AGENDA: frozenset(),
    SalesAssetKind.ONBOARDING_CHECKLIST: frozenset(),
}

_TASK_TRANSITIONS: dict[CrmTaskState, frozenset[CrmTaskState]] = {
    CrmTaskState.OPEN: frozenset({CrmTaskState.DONE, CrmTaskState.CANCELLED}),
    CrmTaskState.DONE: frozenset({CrmTaskState.OPEN, CrmTaskState.CANCELLED}),
    CrmTaskState.CANCELLED: frozenset(),
}

_ONBOARDING_TRANSITIONS: dict[OnboardingState, frozenset[OnboardingState]] = {
    OnboardingState.NOT_STARTED: frozenset({OnboardingState.IN_PROGRESS, OnboardingState.BLOCKED}),
    OnboardingState.IN_PROGRESS: frozenset({OnboardingState.COMPLETED, OnboardingState.BLOCKED}),
    OnboardingState.BLOCKED: frozenset({OnboardingState.IN_PROGRESS}),
    OnboardingState.COMPLETED: frozenset(),
}

DEFAULT_ONBOARDING_CHECKLIST: tuple[tuple[str, str], ...] = (
    ("account_setup", "Client account, seats and access confirmed"),
    ("brand_assets_received", "Brand assets and channel guidelines received"),
    ("content_calendar_agreed", "Content calendar and approval flow agreed"),
    ("first_campaign_briefed", "First campaign briefed with source material"),
    ("reporting_cadence_set", "Reporting cadence and review meeting set"),
)

# Fuzzy duplicate detection mirrors Phase 8: conservative threshold,
# flags only, never an automatic merge.
_FUZZY_THRESHOLD = 0.86
_FUZZY_SCAN_LIMIT = 500

# Revenue summary semantics. Commitment buckets count records that have
# actually been issued (drafts and voids excluded); money buckets count
# only records in ``paid`` state. Kinds are never added into each other.
_COMMITMENT_STATES = frozenset({RevenueState.ISSUED, RevenueState.PAID, RevenueState.OVERDUE})
REVENUE_SUMMARY_DEFINITIONS: dict[str, str] = {
    "quoted": "sum of quote records in issued/paid/overdue state; drafts and voids excluded",
    "contracted": (
        "sum of contract records in issued/paid/overdue state; drafts and voids excluded"
    ),
    "invoiced": ("sum of invoice records in issued/paid/overdue state; drafts and voids excluded"),
    "collected": "sum of payment records in paid state only; quotes and invoices never count",
    "refunded": "sum of refund records in paid state only",
    "recurring": (
        "sum of recurring records in issued/paid/overdue state; reported separately, "
        "never added to collected"
    ),
    "campaign_payout": (
        "sum of campaign_payout records in paid state only; money collected for a "
        "v2.1 campaign, reported separately and never added to collected"
    ),
}


def crm_capabilities(
    comms: CommsAdapterRegistry, payments: PaymentsAdapterRegistry
) -> list[dict[str, object]]:
    """Truthful Phase 9 gateway states for the capabilities endpoint."""
    comms_adapter = comms.get()
    payments_adapter = payments.get()
    return [
        {
            "domain": "communications",
            "provider": comms_adapter.provider,
            "mode": comms_adapter.mode,
            "source": comms_adapter.source,
            "channels": list(comms_adapter.capabilities.channels),
            "limitations": list(comms_adapter.capabilities.limitations),
        },
        {
            "domain": "payments",
            "provider": payments_adapter.provider,
            "mode": payments_adapter.mode,
            "source": payments_adapter.source,
            "channels": [],
            "limitations": list(payments_adapter.capabilities.limitations),
        },
    ]


class CrmService:
    """Phase 9 sales assistant, CRM, onboarding and revenue operations.

    Isolation and safety contract: this service writes only to the
    Phase 9 tables plus audit events. It has no send path — the comms
    and payments gateways are disabled (or mocked in tests) and are
    never called from here; approving a draft changes only the draft's
    own state and grants no sending capability. Drafts are assembled
    exclusively from cited stored evidence, suppressed contacts are
    refused new drafts, and revenue kinds are reported strictly
    separately so a quote can never masquerade as collected money.
    """

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------
    @staticmethod
    async def get_contact(session: AsyncSession, tenant_id: UUID, contact_id: UUID) -> CrmContact:
        contact = await session.scalar(
            select(CrmContact).where(
                CrmContact.id == contact_id,
                CrmContact.tenant_id == tenant_id,
            )
        )
        if contact is None:
            raise NotFoundError("CRM contact")
        return contact

    @staticmethod
    async def list_contacts(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        include_suppressed: bool = True,
        limit: int = 100,
    ) -> list[CrmContact]:
        query = select(CrmContact).where(CrmContact.tenant_id == tenant_id)
        if not include_suppressed:
            query = query.where(CrmContact.suppressed.is_(False))
        rows = await session.scalars(query.order_by(desc(CrmContact.created_at)).limit(limit))
        return list(rows.all())

    @staticmethod
    async def create_contact(
        session: AsyncSession,
        *,
        principal: Principal,
        display_name: str,
        organisation: str | None,
        role_title: str | None,
        contact_route_label: str | None,
    ) -> tuple[CrmContact, bool]:
        dedup_key = _contact_dedup_key(display_name=display_name, organisation=organisation)
        existing = await session.scalar(
            select(CrmContact).where(
                CrmContact.tenant_id == principal.tenant_id,
                CrmContact.dedup_key == dedup_key,
            )
        )
        if existing is not None:
            _refresh_contact(
                existing,
                organisation=organisation,
                role_title=role_title,
                contact_route_label=contact_route_label,
            )
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="crm.contact_merged",
                resource_type="crm_contact",
                resource_id=str(existing.id),
                event_data={"dedup_key": dedup_key, "via": "exact_match_on_create"},
            )
            await session.commit()
            await session.refresh(existing)
            return existing, False
        contact = CrmContact(
            tenant_id=principal.tenant_id,
            display_name=display_name,
            organisation=organisation,
            role_title=role_title,
            contact_route_label=contact_route_label,
            dedup_key=dedup_key,
            created_by_user_id=principal.user_id,
        )
        session.add(contact)
        await session.flush()
        contact.possible_duplicate_of_id = await CrmService._find_fuzzy_contact(session, contact)
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.contact_created",
            resource_type="crm_contact",
            resource_id=str(contact.id),
            event_data={
                "dedup_key": dedup_key,
                "possible_duplicate_of_id": (
                    str(contact.possible_duplicate_of_id)
                    if contact.possible_duplicate_of_id
                    else None
                ),
            },
        )
        await session.commit()
        await session.refresh(contact)
        return contact, True

    @staticmethod
    async def update_contact(
        session: AsyncSession,
        *,
        principal: Principal,
        contact_id: UUID,
        display_name: str | None,
        organisation: str | None,
        role_title: str | None,
        contact_route_label: str | None,
    ) -> CrmContact:
        contact = await CrmService.get_contact(session, principal.tenant_id, contact_id)
        if display_name is not None:
            contact.display_name = display_name
        if organisation is not None:
            contact.organisation = organisation
        if (display_name is not None or organisation is not None) and (
            contact.dedup_key.startswith("name:")
        ):
            # Name-based identity follows the name; handle/URL identity
            # from an opportunity intake is stable and is kept.
            contact.dedup_key = _contact_dedup_key(
                display_name=contact.display_name, organisation=contact.organisation
            )
        if role_title is not None:
            contact.role_title = role_title
        if contact_route_label is not None:
            contact.contact_route_label = contact_route_label
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.contact_updated",
            resource_type="crm_contact",
            resource_id=str(contact.id),
        )
        await session.commit()
        await session.refresh(contact)
        return contact

    @staticmethod
    async def suppress_contact(
        session: AsyncSession,
        *,
        principal: Principal,
        contact_id: UUID,
        reason: str,
    ) -> CrmContact:
        principal.require(Role.REVIEWER)
        contact = await CrmService.get_contact(session, principal.tenant_id, contact_id)
        contact.suppressed = True
        contact.suppression_reason = reason
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.contact_suppressed",
            resource_type="crm_contact",
            resource_id=str(contact.id),
            event_data={"reason": reason},
        )
        await session.commit()
        await session.refresh(contact)
        return contact

    @staticmethod
    async def unsuppress_contact(
        session: AsyncSession,
        *,
        principal: Principal,
        contact_id: UUID,
    ) -> CrmContact:
        principal.require(Role.REVIEWER)
        contact = await CrmService.get_contact(session, principal.tenant_id, contact_id)
        contact.suppressed = False
        contact.suppression_reason = None
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.contact_unsuppressed",
            resource_type="crm_contact",
            resource_id=str(contact.id),
        )
        await session.commit()
        await session.refresh(contact)
        return contact

    @staticmethod
    async def merge_contacts(
        session: AsyncSession,
        *,
        principal: Principal,
        contact_id: UUID,
        duplicate_id: UUID,
    ) -> CrmContact:
        """Reviewed merge: fold ``duplicate_id`` into ``contact_id``.

        Only a human reviewer can merge a fuzzy-flagged pair; the merge
        is recorded as a pipeline event and an audit event. Suppression
        is sticky: if either record is suppressed the survivor stays
        suppressed, so an opt-out can never be merged away.
        """
        principal.require(Role.REVIEWER)
        if contact_id == duplicate_id:
            raise ConflictError("a contact cannot be merged into itself")
        primary = await CrmService.get_contact(session, principal.tenant_id, contact_id)
        duplicate = await CrmService.get_contact(session, principal.tenant_id, duplicate_id)
        _refresh_contact(
            primary,
            organisation=duplicate.organisation,
            role_title=duplicate.role_title,
            contact_route_label=duplicate.contact_route_label,
        )
        if duplicate.suppressed and not primary.suppressed:
            primary.suppressed = True
            primary.suppression_reason = duplicate.suppression_reason
        await session.execute(
            update(CrmAccount)
            .where(CrmAccount.primary_contact_id == duplicate.id)
            .values(primary_contact_id=primary.id)
        )
        await session.execute(
            update(SalesAssetDraft)
            .where(SalesAssetDraft.contact_id == duplicate.id)
            .values(contact_id=primary.id)
        )
        await session.execute(
            update(CrmPipelineEvent)
            .where(CrmPipelineEvent.contact_id == duplicate.id)
            .values(contact_id=primary.id)
        )
        await session.execute(
            update(CrmContact)
            .where(CrmContact.possible_duplicate_of_id == duplicate.id)
            .values(possible_duplicate_of_id=primary.id)
        )
        session.add(
            CrmPipelineEvent(
                tenant_id=principal.tenant_id,
                contact_id=primary.id,
                event_kind="merge",
                actor_user_id=principal.user_id,
                reason=(f"reviewed merge: contact {duplicate.id} merged into contact {primary.id}"),
            )
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.contact_merged",
            resource_type="crm_contact",
            resource_id=str(primary.id),
            event_data={"merged_contact_id": str(duplicate.id), "via": "reviewed_merge"},
        )
        await session.flush()
        await session.delete(duplicate)
        await session.commit()
        await session.refresh(primary)
        return primary

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------
    @staticmethod
    async def get_account(session: AsyncSession, tenant_id: UUID, account_id: UUID) -> CrmAccount:
        account = await session.scalar(
            select(CrmAccount).where(
                CrmAccount.id == account_id,
                CrmAccount.tenant_id == tenant_id,
            )
        )
        if account is None:
            raise NotFoundError("CRM account")
        return account

    @staticmethod
    async def list_accounts(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        stage: CrmStage | None = None,
        limit: int = 100,
    ) -> list[CrmAccount]:
        query = select(CrmAccount).where(CrmAccount.tenant_id == tenant_id)
        if stage is not None:
            query = query.where(CrmAccount.stage == stage)
        rows = await session.scalars(query.order_by(desc(CrmAccount.created_at)).limit(limit))
        return list(rows.all())

    @staticmethod
    async def create_account(
        session: AsyncSession,
        *,
        principal: Principal,
        name: str,
        owner_user_id: UUID | None,
        primary_contact_id: UUID | None,
        next_action: str | None,
        next_action_at: datetime | None,
        notes_summary: str | None,
    ) -> tuple[CrmAccount, bool]:
        if owner_user_id is not None:
            await CrmService._require_tenant_member(
                session, principal.tenant_id, owner_user_id, "owner_user_id"
            )
        if primary_contact_id is not None:
            await CrmService.get_contact(session, principal.tenant_id, primary_contact_id)
        dedup_key = _account_dedup_key(name)
        existing = await session.scalar(
            select(CrmAccount).where(
                CrmAccount.tenant_id == principal.tenant_id,
                CrmAccount.dedup_key == dedup_key,
                CrmAccount.source_opportunity_id.is_(None),
            )
        )
        if existing is not None:
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="crm.account_merged",
                resource_type="crm_account",
                resource_id=str(existing.id),
                event_data={"dedup_key": dedup_key, "via": "exact_match_on_create"},
            )
            await session.commit()
            await session.refresh(existing)
            return existing, False
        account = CrmAccount(
            tenant_id=principal.tenant_id,
            name=name,
            stage=CrmStage.PROSPECT,
            owner_user_id=owner_user_id if owner_user_id is not None else principal.user_id,
            primary_contact_id=primary_contact_id,
            dedup_key=dedup_key,
            next_action=next_action,
            next_action_at=next_action_at,
            notes_summary=notes_summary,
            created_by_user_id=principal.user_id,
        )
        session.add(account)
        await session.flush()
        account.possible_duplicate_of_id = await CrmService._find_fuzzy_account(session, account)
        session.add(
            CrmPipelineEvent(
                tenant_id=principal.tenant_id,
                account_id=account.id,
                event_kind="created",
                from_stage=None,
                to_stage=CrmStage.PROSPECT,
                actor_user_id=principal.user_id,
                reason="account created",
            )
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.account_created",
            resource_type="crm_account",
            resource_id=str(account.id),
            event_data={"dedup_key": dedup_key},
        )
        await session.commit()
        await session.refresh(account)
        return account, True

    @staticmethod
    async def update_account(
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        name: str | None,
        owner_user_id: UUID | None,
        primary_contact_id: UUID | None,
        next_action: str | None,
        next_action_at: datetime | None,
        notes_summary: str | None,
    ) -> CrmAccount:
        account = await CrmService.get_account(session, principal.tenant_id, account_id)
        if owner_user_id is not None:
            await CrmService._require_tenant_member(
                session, principal.tenant_id, owner_user_id, "owner_user_id"
            )
            account.owner_user_id = owner_user_id
        if primary_contact_id is not None:
            await CrmService.get_contact(session, principal.tenant_id, primary_contact_id)
            account.primary_contact_id = primary_contact_id
        if name is not None:
            account.name = name
            if account.source_opportunity_id is None:
                account.dedup_key = _account_dedup_key(name)
        if next_action is not None:
            account.next_action = next_action
        if next_action_at is not None:
            account.next_action_at = next_action_at
        if notes_summary is not None:
            account.notes_summary = notes_summary
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.account_updated",
            resource_type="crm_account",
            resource_id=str(account.id),
        )
        await session.commit()
        await session.refresh(account)
        return account

    @staticmethod
    async def transition_stage(
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        target_stage: CrmStage,
        reason: str | None,
    ) -> CrmAccount:
        account = await CrmService.get_account(session, principal.tenant_id, account_id)
        current = account.stage
        if target_stage == current:
            raise ConflictError(f"account is already in stage {current.value}")
        if current in _TERMINAL_STAGES:
            raise ConflictError(f"account in terminal stage {current.value} cannot transition")
        clean_reason = (reason or "").strip() or None
        if target_stage in _SIDE_STAGES and clean_reason is None:
            raise ClipOSError(
                "stage_reason_required",
                f"moving an account to {target_stage.value} requires a reason",
                status_code=422,
            )
        if (
            target_stage in _STAGE_ORDER
            and current in _STAGE_ORDER
            and _STAGE_ORDER[target_stage] < _STAGE_ORDER[current]
            and clean_reason is None
        ):
            raise ClipOSError(
                "stage_reason_required",
                "moving an account backwards in the pipeline requires a reason",
                status_code=422,
            )
        account.stage = target_stage
        session.add(
            CrmPipelineEvent(
                tenant_id=principal.tenant_id,
                account_id=account.id,
                event_kind="stage_transition",
                from_stage=current,
                to_stage=target_stage,
                actor_user_id=principal.user_id,
                reason=clean_reason,
            )
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.account_stage_transitioned",
            resource_type="crm_account",
            resource_id=str(account.id),
            event_data={
                "from": current.value,
                "to": target_stage.value,
                "reason": clean_reason,
            },
        )
        await session.commit()
        await session.refresh(account)
        return account

    @staticmethod
    async def merge_accounts(
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        duplicate_id: UUID,
    ) -> CrmAccount:
        """Reviewed merge: fold ``duplicate_id`` into ``account_id``.

        The duplicate's drafts, tasks, revenue records and pipeline
        history are re-pointed to the survivor; the merge itself is
        recorded as a pipeline event on the survivor plus an audit
        event. Accounts are never auto-merged on a fuzzy match.
        """
        principal.require(Role.REVIEWER)
        if account_id == duplicate_id:
            raise ConflictError("an account cannot be merged into itself")
        primary = await CrmService.get_account(session, principal.tenant_id, account_id)
        duplicate = await CrmService.get_account(session, principal.tenant_id, duplicate_id)
        primary_onboarding = await CrmService._onboarding_or_none(session, primary.id)
        duplicate_onboarding = await CrmService._onboarding_or_none(session, duplicate.id)
        if primary_onboarding is not None and duplicate_onboarding is not None:
            raise ConflictError(
                "both accounts have onboarding records; resolve them manually before merging"
            )
        if primary.owner_user_id is None:
            primary.owner_user_id = duplicate.owner_user_id
        if primary.primary_contact_id is None:
            primary.primary_contact_id = duplicate.primary_contact_id
        if primary.next_action is None:
            primary.next_action = duplicate.next_action
            primary.next_action_at = duplicate.next_action_at
        for model in (SalesAssetDraft, CrmTask, RevenueRecord, OnboardingRecord):
            await session.execute(
                update(model).where(model.account_id == duplicate.id).values(account_id=primary.id)
            )
        await session.execute(
            update(CrmPipelineEvent)
            .where(CrmPipelineEvent.account_id == duplicate.id)
            .values(account_id=primary.id)
        )
        await session.execute(
            update(CrmAccount)
            .where(CrmAccount.possible_duplicate_of_id == duplicate.id)
            .values(possible_duplicate_of_id=primary.id)
        )
        session.add(
            CrmPipelineEvent(
                tenant_id=principal.tenant_id,
                account_id=primary.id,
                event_kind="merge",
                from_stage=duplicate.stage,
                to_stage=primary.stage,
                actor_user_id=principal.user_id,
                reason=(f"reviewed merge: account {duplicate.id} merged into account {primary.id}"),
            )
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.account_merged",
            resource_type="crm_account",
            resource_id=str(primary.id),
            event_data={"merged_account_id": str(duplicate.id), "via": "reviewed_merge"},
        )
        await session.flush()
        await session.delete(duplicate)
        await session.commit()
        await session.refresh(primary)
        return primary

    @staticmethod
    async def list_pipeline_events(
        session: AsyncSession, tenant_id: UUID, account_id: UUID, *, limit: int = 100
    ) -> list[CrmPipelineEvent]:
        await CrmService.get_account(session, tenant_id, account_id)
        rows = await session.scalars(
            select(CrmPipelineEvent)
            .where(
                CrmPipelineEvent.tenant_id == tenant_id,
                CrmPipelineEvent.account_id == account_id,
            )
            .order_by(CrmPipelineEvent.created_at, CrmPipelineEvent.id)
            .limit(limit)
        )
        return list(rows.all())

    # ------------------------------------------------------------------
    # Phase 8 handoff intake
    # ------------------------------------------------------------------
    @staticmethod
    async def intake_handoff(
        session: AsyncSession, *, principal: Principal, handoff_id: UUID
    ) -> tuple[CrmAccount, bool]:
        """Take one ready Phase 8 handoff into the CRM, exactly once.

        Intake is synchronous database work over already-stored records
        — no external call exists to queue — so it deliberately does not
        create a background job. Repeating intake for the same handoff
        (or for the same opportunity) returns the existing account.
        """
        handoff = await OpportunityService.get_handoff(session, principal.tenant_id, handoff_id)
        existing = await session.scalar(
            select(CrmAccount).where(
                CrmAccount.tenant_id == principal.tenant_id,
                CrmAccount.source_handoff_id == handoff.id,
            )
        )
        if existing is not None:
            return existing, False
        if handoff.state != OpportunityHandoffState.READY:
            raise ConflictError(
                "only a ready opportunity handoff can be taken into the CRM; "
                f"this handoff is {handoff.state.value}"
            )
        opportunity = await OpportunityService.get_opportunity(
            session, principal.tenant_id, handoff.opportunity_id
        )
        existing_by_opportunity = await session.scalar(
            select(CrmAccount).where(
                CrmAccount.tenant_id == principal.tenant_id,
                CrmAccount.source_opportunity_id == opportunity.id,
            )
        )
        if existing_by_opportunity is not None:
            return existing_by_opportunity, False
        payload_opportunity = handoff.payload.get("opportunity") or {}
        contact, contact_created = await CrmService._get_or_create_intake_contact(
            session,
            principal=principal,
            opportunity=opportunity,
            payload_opportunity=payload_opportunity,
        )
        account = CrmAccount(
            tenant_id=principal.tenant_id,
            name=str(payload_opportunity.get("title") or opportunity.title),
            stage=CrmStage.PROSPECT,
            owner_user_id=principal.user_id,
            primary_contact_id=contact.id,
            source_opportunity_id=opportunity.id,
            source_handoff_id=handoff.id,
            dedup_key=f"opportunity:{opportunity.dedup_key}",
            next_action="Review the opportunity evidence and open a conversation draft",
            created_by_user_id=principal.user_id,
        )
        session.add(account)
        await session.flush()
        session.add(
            CrmPipelineEvent(
                tenant_id=principal.tenant_id,
                account_id=account.id,
                event_kind="intake",
                from_stage=None,
                to_stage=CrmStage.PROSPECT,
                actor_user_id=principal.user_id,
                reason=(f"Phase 9 intake from {handoff.payload_version} handoff {handoff.id}"),
            )
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.intake_completed",
            resource_type="crm_account",
            resource_id=str(account.id),
            event_data={
                "handoff_id": str(handoff.id),
                "opportunity_id": str(opportunity.id),
                "contact_id": str(contact.id),
                "contact_created": contact_created,
            },
        )
        await session.commit()
        await session.refresh(account)
        return account, True

    @staticmethod
    async def _get_or_create_intake_contact(
        session: AsyncSession,
        *,
        principal: Principal,
        opportunity: Opportunity,
        payload_opportunity: dict[str, Any],
    ) -> tuple[CrmContact, bool]:
        dedup_key = _contact_dedup_key(
            display_name=str(
                payload_opportunity.get("creator_name") or payload_opportunity.get("title") or ""
            ),
            organisation=None,
            handle=payload_opportunity.get("creator_handle"),
            canonical_url=payload_opportunity.get("canonical_url"),
        )
        existing = await session.scalar(
            select(CrmContact).where(
                CrmContact.tenant_id == principal.tenant_id,
                CrmContact.dedup_key == dedup_key,
            )
        )
        if existing is not None:
            _refresh_contact(
                existing,
                organisation=None,
                role_title=None,
                contact_route_label=payload_opportunity.get("contact_route_label"),
            )
            if existing.source_opportunity_id is None:
                existing.source_opportunity_id = opportunity.id
            await session.flush()
            return existing, False
        creator_name = payload_opportunity.get("creator_name")
        title = payload_opportunity.get("title") or opportunity.title
        contact = CrmContact(
            tenant_id=principal.tenant_id,
            display_name=str(creator_name or title),
            organisation=(str(title) if creator_name and str(title) != str(creator_name) else None),
            contact_route_label=payload_opportunity.get("contact_route_label"),
            source_opportunity_id=opportunity.id,
            dedup_key=dedup_key,
            created_by_user_id=principal.user_id,
        )
        session.add(contact)
        await session.flush()
        contact.possible_duplicate_of_id = await CrmService._find_fuzzy_contact(session, contact)
        return contact, True

    # ------------------------------------------------------------------
    # Sales asset drafts
    # ------------------------------------------------------------------
    @staticmethod
    async def get_draft(session: AsyncSession, tenant_id: UUID, draft_id: UUID) -> SalesAssetDraft:
        draft = await session.scalar(
            select(SalesAssetDraft).where(
                SalesAssetDraft.id == draft_id,
                SalesAssetDraft.tenant_id == tenant_id,
            )
        )
        if draft is None:
            raise NotFoundError("sales asset draft")
        return draft

    @staticmethod
    async def list_drafts(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        account_id: UUID | None = None,
        status: SalesAssetState | None = None,
        limit: int = 100,
    ) -> list[SalesAssetDraft]:
        query = select(SalesAssetDraft).where(SalesAssetDraft.tenant_id == tenant_id)
        if account_id is not None:
            query = query.where(SalesAssetDraft.account_id == account_id)
        if status is not None:
            query = query.where(SalesAssetDraft.status == status)
        rows = await session.scalars(query.order_by(desc(SalesAssetDraft.created_at)).limit(limit))
        return list(rows.all())

    @staticmethod
    async def create_draft(
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        kind: SalesAssetKind,
        contact_id: UUID | None,
        title: str | None,
        body: str | None,
    ) -> SalesAssetDraft:
        account = await CrmService.get_account(session, principal.tenant_id, account_id)
        contact: CrmContact | None = None
        if contact_id is not None:
            contact = await CrmService.get_contact(session, principal.tenant_id, contact_id)
        elif account.primary_contact_id is not None:
            contact = await CrmService.get_contact(
                session, principal.tenant_id, account.primary_contact_id
            )
        if contact is not None and contact.suppressed:
            # Opt-out enforcement: the refusal itself is audited with a
            # denied outcome before the error is raised.
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="crm.draft_blocked_suppressed",
                resource_type="crm_contact",
                resource_id=str(contact.id),
                outcome=AuditOutcome.DENIED,
                event_data={
                    "account_id": str(account.id),
                    "kind": kind.value,
                    "suppression_reason": contact.suppression_reason,
                },
            )
            await session.commit()
            raise ClipOSError(
                "contact_suppressed",
                "this contact has opted out (suppressed); no new sales drafts "
                "may be created for them",
                status_code=409,
            )
        if kind == SalesAssetKind.ONBOARDING_CHECKLIST and account.stage not in _CLIENT_STAGES:
            raise ConflictError(
                "onboarding checklist drafts require a client-stage account; "
                f"this account is {account.stage.value}"
            )
        citations = await CrmService._collect_evidence(session, account)
        if not citations:
            raise ClipOSError(
                "insufficient_evidence",
                "no stored evidence can be cited for this account, so no draft "
                "may be created: drafts may only assert cited facts",
                status_code=422,
            )
        required = _REQUIRED_CITATION_TYPES[kind]
        if required and not any(citation["type"] in required for citation in citations):
            raise ClipOSError(
                "insufficient_evidence",
                f"a {kind.value} draft requires at least one citation of type "
                f"{sorted(required)}; this account has none",
                status_code=422,
                details={"citation_types_present": [citation["type"] for citation in citations]},
            )
        if body is None:
            generated_title, generated_body = _render_draft(
                kind, account=account, contact=contact, citations=citations
            )
            final_title = title or generated_title
            final_body = generated_body
            created_by = "system"
        else:
            final_title = title or f"{kind.value.replace('_', ' ').title()} — {account.name}"
            final_body = body
            created_by = str(principal.user_id)
        draft = SalesAssetDraft(
            tenant_id=principal.tenant_id,
            account_id=account.id,
            contact_id=contact.id if contact is not None else None,
            kind=kind,
            title=final_title,
            body=final_body,
            evidence={
                "citations": citations,
                "generator": created_by,
                "assembled_at": utcnow().isoformat(),
            },
            status=SalesAssetState.DRAFT,
            template_version=DRAFT_TEMPLATE_VERSION,
            created_by=created_by,
        )
        session.add(draft)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.draft_created",
            resource_type="sales_asset_draft",
            resource_id=str(draft.id),
            event_data={
                "account_id": str(account.id),
                "kind": kind.value,
                "citation_types": [citation["type"] for citation in citations],
                "created_by": created_by,
            },
        )
        await session.commit()
        await session.refresh(draft)
        return draft

    @staticmethod
    async def review_draft(
        session: AsyncSession,
        *,
        principal: Principal,
        draft_id: UUID,
        target_state: SalesAssetState,
        note: str | None,
    ) -> SalesAssetDraft:
        """Review a draft. Approval changes only the draft's own state:
        there is no send path in Phase 9, so approval grants no sending
        capability and nothing is transmitted."""
        if target_state in _REVIEWER_ONLY_DRAFT_TARGETS:
            principal.require(Role.REVIEWER)
        draft = await CrmService.get_draft(session, principal.tenant_id, draft_id)
        source_state = draft.status
        if target_state not in _DRAFT_TRANSITIONS[source_state]:
            raise ConflictError(
                f"draft cannot transition from {source_state.value} to {target_state.value}"
            )
        draft.status = target_state
        if target_state in {SalesAssetState.APPROVED, SalesAssetState.REJECTED}:
            draft.reviewed_by_user_id = principal.user_id
            draft.reviewed_at = utcnow()
            draft.review_note = note
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.draft_reviewed",
            resource_type="sales_asset_draft",
            resource_id=str(draft.id),
            event_data={"from": source_state.value, "to": target_state.value},
        )
        await session.commit()
        await session.refresh(draft)
        return draft

    @staticmethod
    async def _collect_evidence(session: AsyncSession, account: CrmAccount) -> list[dict[str, Any]]:
        """Assemble the citation list a draft for ``account`` may use.

        Only stored records are cited: the source opportunity and its
        score, the intake handoff, the account's revenue records, its
        latest pipeline event, and — when the account came from an
        opportunity and the tenant has reported publication metrics —
        an explicitly tenant-scoped analytics summary. Nothing is
        inferred or fabricated.
        """
        citations: list[dict[str, Any]] = []
        if account.source_opportunity_id is not None:
            opportunity = await session.scalar(
                select(Opportunity)
                .options(selectinload(Opportunity.score_components))
                .where(
                    Opportunity.id == account.source_opportunity_id,
                    Opportunity.tenant_id == account.tenant_id,
                )
            )
            if opportunity is not None:
                components = list(opportunity.score_components)
                citations.append(
                    {
                        "type": "opportunity",
                        "id": str(opportunity.id),
                        "label": f"Opportunity '{opportunity.title}' (Phase 8)",
                        "detail": {
                            "overall_score": opportunity.overall_score,
                            "score_version": opportunity.score_version,
                            "niche": opportunity.niche,
                            "audience_band": opportunity.audience_band,
                            "posting_cadence": opportunity.posting_cadence,
                            "contact_route_label": opportunity.contact_route_label,
                            "components": [
                                {
                                    "key": component.key,
                                    "score": component.score,
                                    "weight": component.weight,
                                    "applicable": component.applicable,
                                }
                                for component in sorted(components, key=lambda item: item.key)
                            ],
                        },
                    }
                )
                analytics = await CrmService._analytics_citation(session, account)
                if analytics is not None:
                    citations.append(analytics)
        if account.source_handoff_id is not None:
            handoff = await session.scalar(
                select(OpportunityHandoff).where(
                    OpportunityHandoff.id == account.source_handoff_id,
                    OpportunityHandoff.tenant_id == account.tenant_id,
                )
            )
            if handoff is not None:
                citations.append(
                    {
                        "type": "opportunity_handoff",
                        "id": str(handoff.id),
                        "label": f"Phase 9 intake package ({handoff.payload_version})",
                        "detail": {
                            "payload_version": handoff.payload_version,
                            "state": handoff.state.value,
                        },
                    }
                )
        revenue_rows = await session.scalars(
            select(RevenueRecord)
            .where(
                RevenueRecord.tenant_id == account.tenant_id,
                RevenueRecord.account_id == account.id,
            )
            .order_by(desc(RevenueRecord.occurred_at))
            .limit(5)
        )
        for record in revenue_rows.all():
            citations.append(
                {
                    "type": "revenue_record",
                    "id": str(record.id),
                    "label": (
                        f"{record.kind.value} {record.amount_minor_units} "
                        f"{record.currency} ({record.status.value})"
                    ),
                    "detail": {
                        "kind": record.kind.value,
                        "status": record.status.value,
                        "amount_minor_units": record.amount_minor_units,
                        "currency": record.currency,
                        "occurred_at": record.occurred_at.isoformat(),
                    },
                }
            )
        latest_event = await session.scalar(
            select(CrmPipelineEvent)
            .where(
                CrmPipelineEvent.tenant_id == account.tenant_id,
                CrmPipelineEvent.account_id == account.id,
            )
            .order_by(desc(CrmPipelineEvent.created_at), desc(CrmPipelineEvent.id))
            .limit(1)
        )
        if latest_event is not None:
            citations.append(
                {
                    "type": "pipeline_event",
                    "id": str(latest_event.id),
                    "label": f"Pipeline event: {latest_event.event_kind}",
                    "detail": {
                        "event_kind": latest_event.event_kind,
                        "from_stage": (
                            latest_event.from_stage.value if latest_event.from_stage else None
                        ),
                        "to_stage": (
                            latest_event.to_stage.value if latest_event.to_stage else None
                        ),
                        "at": latest_event.created_at.isoformat(),
                    },
                }
            )
        return citations

    @staticmethod
    async def _analytics_citation(
        session: AsyncSession, account: CrmAccount
    ) -> dict[str, Any] | None:
        """Tenant-scoped analytics summary, cited only when real stored
        snapshots exist. Labelled as tenant scope in the citation itself
        so a draft can never present it as this account's own metrics."""
        rows = await session.scalars(
            select(AnalyticsMetricSnapshot)
            .where(
                AnalyticsMetricSnapshot.tenant_id == account.tenant_id,
                AnalyticsMetricSnapshot.metric == "views",
                AnalyticsMetricSnapshot.availability == MetricAvailability.REPORTED,
            )
            .order_by(desc(AnalyticsMetricSnapshot.fetched_at))
            .limit(1000)
        )
        latest_by_publication: dict[UUID, float] = {}
        for snapshot in rows.all():
            if snapshot.publication_id not in latest_by_publication:
                if snapshot.value is not None:
                    latest_by_publication[snapshot.publication_id] = snapshot.value
        if not latest_by_publication:
            return None
        values = list(latest_by_publication.values())
        return {
            "type": "analytics_summary",
            "id": "tenant-publications",
            "label": "Tenant publication analytics (Phase 7, tenant scope)",
            "detail": {
                "scope": "tenant_publications",
                "publications_with_reported_views": len(values),
                "mean_latest_views": round(sum(values) / len(values), 1),
            },
        }

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------
    @staticmethod
    async def create_task(
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        title: str,
        due_at: datetime | None,
        assignee_user_id: UUID | None,
    ) -> CrmTask:
        await CrmService.get_account(session, principal.tenant_id, account_id)
        if assignee_user_id is not None:
            await CrmService._require_tenant_member(
                session, principal.tenant_id, assignee_user_id, "assignee_user_id"
            )
        task = CrmTask(
            tenant_id=principal.tenant_id,
            account_id=account_id,
            title=title,
            due_at=due_at,
            status=CrmTaskState.OPEN,
            assignee_user_id=assignee_user_id,
            created_by_user_id=principal.user_id,
        )
        session.add(task)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.task_created",
            resource_type="crm_task",
            resource_id=str(task.id),
            event_data={"account_id": str(account_id)},
        )
        await session.commit()
        await session.refresh(task)
        return task

    @staticmethod
    async def list_tasks(
        session: AsyncSession, tenant_id: UUID, account_id: UUID, *, limit: int = 100
    ) -> list[CrmTask]:
        await CrmService.get_account(session, tenant_id, account_id)
        rows = await session.scalars(
            select(CrmTask)
            .where(CrmTask.tenant_id == tenant_id, CrmTask.account_id == account_id)
            .order_by(desc(CrmTask.created_at))
            .limit(limit)
        )
        return list(rows.all())

    @staticmethod
    async def update_task(
        session: AsyncSession,
        *,
        principal: Principal,
        task_id: UUID,
        title: str | None,
        due_at: datetime | None,
        status: CrmTaskState | None,
        assignee_user_id: UUID | None,
    ) -> CrmTask:
        task = await session.scalar(
            select(CrmTask).where(
                CrmTask.id == task_id,
                CrmTask.tenant_id == principal.tenant_id,
            )
        )
        if task is None:
            raise NotFoundError("CRM task")
        if status is not None and status != task.status:
            if status not in _TASK_TRANSITIONS[task.status]:
                raise ConflictError(
                    f"task cannot transition from {task.status.value} to {status.value}"
                )
            task.status = status
        if title is not None:
            task.title = title
        if due_at is not None:
            task.due_at = due_at
        if assignee_user_id is not None:
            await CrmService._require_tenant_member(
                session, principal.tenant_id, assignee_user_id, "assignee_user_id"
            )
            task.assignee_user_id = assignee_user_id
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.task_updated",
            resource_type="crm_task",
            resource_id=str(task.id),
        )
        await session.commit()
        await session.refresh(task)
        return task

    # ------------------------------------------------------------------
    # Revenue records
    # ------------------------------------------------------------------
    @staticmethod
    async def create_revenue_record(
        session: AsyncSession,
        *,
        principal: Principal,
        account_id: UUID,
        kind: RevenueKind,
        amount_minor_units: int,
        currency: str,
        status: RevenueState,
        external_reference: str | None,
        occurred_at: datetime,
        notes: str | None,
        campaign_id: UUID | None = None,
    ) -> RevenueRecord:
        await CrmService.get_account(session, principal.tenant_id, account_id)
        record = RevenueRecord(
            tenant_id=principal.tenant_id,
            account_id=account_id,
            kind=kind,
            amount_minor_units=amount_minor_units,
            currency=currency.upper(),
            status=status,
            external_reference=external_reference,
            occurred_at=occurred_at,
            notes=notes,
            campaign_id=campaign_id,
            created_by_user_id=principal.user_id,
        )
        session.add(record)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.revenue_recorded",
            resource_type="revenue_record",
            resource_id=str(record.id),
            event_data={
                "account_id": str(account_id),
                "kind": kind.value,
                "status": status.value,
                "amount_minor_units": amount_minor_units,
                "currency": currency.upper(),
                "campaign_id": str(campaign_id) if campaign_id is not None else None,
            },
        )
        await session.commit()
        await session.refresh(record)
        return record

    @staticmethod
    async def list_revenue_records(
        session: AsyncSession, tenant_id: UUID, account_id: UUID, *, limit: int = 100
    ) -> list[RevenueRecord]:
        await CrmService.get_account(session, tenant_id, account_id)
        rows = await session.scalars(
            select(RevenueRecord)
            .where(
                RevenueRecord.tenant_id == tenant_id,
                RevenueRecord.account_id == account_id,
            )
            .order_by(desc(RevenueRecord.occurred_at))
            .limit(limit)
        )
        return list(rows.all())

    @staticmethod
    async def revenue_summary(
        session: AsyncSession, tenant_id: UUID, account_id: UUID
    ) -> dict[str, Any]:
        """Per-currency totals with the revenue kinds kept strictly
        separate: a quote is reported as quoted, never as collected."""
        await CrmService.get_account(session, tenant_id, account_id)
        rows = await session.scalars(
            select(RevenueRecord).where(
                RevenueRecord.tenant_id == tenant_id,
                RevenueRecord.account_id == account_id,
            )
        )
        buckets: dict[str, dict[str, int]] = {}
        for record in rows.all():
            bucket = buckets.setdefault(
                record.currency,
                {
                    "quoted": 0,
                    "contracted": 0,
                    "invoiced": 0,
                    "collected": 0,
                    "refunded": 0,
                    "recurring": 0,
                    "campaign_payout": 0,
                },
            )
            if record.kind == RevenueKind.QUOTE and record.status in _COMMITMENT_STATES:
                bucket["quoted"] += record.amount_minor_units
            elif record.kind == RevenueKind.CONTRACT and record.status in _COMMITMENT_STATES:
                bucket["contracted"] += record.amount_minor_units
            elif record.kind == RevenueKind.INVOICE and record.status in _COMMITMENT_STATES:
                bucket["invoiced"] += record.amount_minor_units
            elif record.kind == RevenueKind.PAYMENT and record.status == RevenueState.PAID:
                bucket["collected"] += record.amount_minor_units
            elif record.kind == RevenueKind.REFUND and record.status == RevenueState.PAID:
                bucket["refunded"] += record.amount_minor_units
            elif record.kind == RevenueKind.RECURRING and record.status in _COMMITMENT_STATES:
                bucket["recurring"] += record.amount_minor_units
            elif record.kind == RevenueKind.CAMPAIGN_PAYOUT and record.status == RevenueState.PAID:
                bucket["campaign_payout"] += record.amount_minor_units
        return {
            "account_id": str(account_id),
            "currencies": [
                {"currency": currency, **buckets[currency]} for currency in sorted(buckets)
            ],
            "definitions": REVENUE_SUMMARY_DEFINITIONS,
        }

    # ------------------------------------------------------------------
    # Onboarding
    # ------------------------------------------------------------------
    @staticmethod
    async def _onboarding_or_none(
        session: AsyncSession, account_id: UUID
    ) -> OnboardingRecord | None:
        return await session.scalar(
            select(OnboardingRecord).where(OnboardingRecord.account_id == account_id)
        )

    @staticmethod
    async def create_onboarding(
        session: AsyncSession, *, principal: Principal, account_id: UUID
    ) -> OnboardingRecord:
        account = await CrmService.get_account(session, principal.tenant_id, account_id)
        if account.stage not in _CLIENT_STAGES:
            raise ConflictError(
                f"onboarding requires a client-stage account; this account is {account.stage.value}"
            )
        if await CrmService._onboarding_or_none(session, account.id) is not None:
            raise ConflictError("this account already has an onboarding record")
        record = OnboardingRecord(
            tenant_id=principal.tenant_id,
            account_id=account.id,
            checklist={
                "version": ONBOARDING_CHECKLIST_VERSION,
                "items": [
                    {"key": key, "label": label, "done": False}
                    for key, label in DEFAULT_ONBOARDING_CHECKLIST
                ],
            },
            milestones=[],
            status=OnboardingState.NOT_STARTED,
            created_by_user_id=principal.user_id,
        )
        session.add(record)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.onboarding_created",
            resource_type="onboarding_record",
            resource_id=str(record.id),
            event_data={"account_id": str(account.id)},
        )
        await session.commit()
        await session.refresh(record)
        return record

    @staticmethod
    async def get_onboarding(
        session: AsyncSession, tenant_id: UUID, account_id: UUID
    ) -> OnboardingRecord:
        await CrmService.get_account(session, tenant_id, account_id)
        record = await session.scalar(
            select(OnboardingRecord).where(
                OnboardingRecord.tenant_id == tenant_id,
                OnboardingRecord.account_id == account_id,
            )
        )
        if record is None:
            raise NotFoundError("onboarding record")
        return record

    @staticmethod
    async def update_onboarding(
        session: AsyncSession,
        *,
        principal: Principal,
        record_id: UUID,
        item_states: dict[str, bool] | None,
        status: OnboardingState | None,
        milestone_label: str | None,
    ) -> OnboardingRecord:
        record = await session.scalar(
            select(OnboardingRecord).where(
                OnboardingRecord.id == record_id,
                OnboardingRecord.tenant_id == principal.tenant_id,
            )
        )
        if record is None:
            raise NotFoundError("onboarding record")
        if item_states:
            # Copy each item: mutating the loaded JSON dicts in place
            # would compare equal to the stored value and never flush.
            items = [dict(item) for item in record.checklist.get("items", [])]
            known = {item["key"] for item in items}
            unknown = sorted(set(item_states) - known)
            if unknown:
                raise ClipOSError(
                    "unknown_checklist_item",
                    f"unknown onboarding checklist items: {', '.join(unknown)}",
                    status_code=422,
                )
            for item in items:
                if item["key"] in item_states:
                    item["done"] = bool(item_states[item["key"]])
            record.checklist = {**record.checklist, "items": items}
        if milestone_label:
            record.milestones = [
                *record.milestones,
                {
                    "label": milestone_label,
                    "at": utcnow().isoformat(),
                    "recorded_by": str(principal.user_id),
                },
            ]
        if status is not None and status != record.status:
            if status not in _ONBOARDING_TRANSITIONS[record.status]:
                raise ConflictError(
                    f"onboarding cannot transition from {record.status.value} to {status.value}"
                )
            if status == OnboardingState.COMPLETED:
                items = record.checklist.get("items", [])
                if not items or not all(item.get("done") for item in items):
                    raise ConflictError(
                        "onboarding cannot be completed until every checklist item is done"
                    )
            record.status = status
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="crm.onboarding_updated",
            resource_type="onboarding_record",
            resource_id=str(record.id),
            event_data={"account_id": str(record.account_id)},
        )
        await session.commit()
        await session.refresh(record)
        return record

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def _require_tenant_member(
        session: AsyncSession, tenant_id: UUID, user_id: UUID, field_name: str
    ) -> None:
        membership = await session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
            )
        )
        if membership is None:
            raise ClipOSError(
                "not_a_tenant_member",
                f"{field_name} must reference an active member of this tenant",
                status_code=422,
            )

    @staticmethod
    async def _find_fuzzy_contact(session: AsyncSession, contact: CrmContact) -> UUID | None:
        rows = await session.scalars(
            select(CrmContact)
            .where(
                CrmContact.tenant_id == contact.tenant_id,
                CrmContact.id != contact.id,
            )
            .order_by(desc(CrmContact.created_at))
            .limit(_FUZZY_SCAN_LIMIT)
        )
        target = _similarity_text(f"{contact.display_name} {contact.organisation or ''}")
        for candidate in rows.all():
            other = _similarity_text(f"{candidate.display_name} {candidate.organisation or ''}")
            if _text_similarity(target, other) >= _FUZZY_THRESHOLD:
                return candidate.id
        return None

    @staticmethod
    async def _find_fuzzy_account(session: AsyncSession, account: CrmAccount) -> UUID | None:
        rows = await session.scalars(
            select(CrmAccount)
            .where(
                CrmAccount.tenant_id == account.tenant_id,
                CrmAccount.id != account.id,
            )
            .order_by(desc(CrmAccount.created_at))
            .limit(_FUZZY_SCAN_LIMIT)
        )
        target = _similarity_text(account.name)
        for candidate in rows.all():
            if _text_similarity(target, _similarity_text(candidate.name)) >= _FUZZY_THRESHOLD:
                return candidate.id
        return None


def _refresh_contact(
    contact: CrmContact,
    *,
    organisation: str | None,
    role_title: str | None,
    contact_route_label: str | None,
) -> None:
    """Newly supplied fields win; absent fields keep stored values."""
    if organisation:
        contact.organisation = organisation
    if role_title:
        contact.role_title = role_title
    if contact_route_label:
        contact.contact_route_label = contact_route_label


def _contact_dedup_key(
    *,
    display_name: str,
    organisation: str | None,
    handle: str | None = None,
    canonical_url: str | None = None,
) -> str:
    """Identity for a contact, mirroring Phase 8's opportunity identity:
    normalised handle first, then canonical URL, then name+organisation."""
    normalized_handle = normalize_handle(handle)
    if normalized_handle:
        return f"handle:{normalized_handle}"
    normalized_url = normalize_url(canonical_url)
    if normalized_url:
        return f"url:{normalized_url}"
    name = " ".join(display_name.split()).casefold()
    org = " ".join((organisation or "").split()).casefold()
    return f"name:{name}|{org}"


def _account_dedup_key(name: str) -> str:
    return f"name:{' '.join(name.split()).casefold()}"


def _similarity_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _citation(citations: list[dict[str, Any]], citation_type: str) -> dict[str, Any] | None:
    for citation in citations:
        if citation["type"] == citation_type:
            return citation
    return None


def _evidence_footer(citations: list[dict[str, Any]]) -> str:
    lines = ["", "Evidence cited for every factual statement above:"]
    for citation in citations:
        lines.append(f"- [{citation['type']}:{citation['id']}] {citation['label']}")
    return "\n".join(lines)


def _render_draft(
    kind: SalesAssetKind,
    *,
    account: CrmAccount,
    contact: CrmContact | None,
    citations: list[dict[str, Any]],
) -> tuple[str, str]:
    """Deterministic template rendering over cited facts only.

    Every fact about the prospect comes from a citation detail; anything
    not cited is simply not asserted. The body ends with the citation
    list so a reviewer can check each statement against its source.
    """
    opportunity = _citation(citations, "opportunity")
    opp_detail: dict[str, Any] = opportunity["detail"] if opportunity else {}
    contact_name = contact.display_name if contact is not None else account.name
    route = opp_detail.get("contact_route_label") or (
        contact.contact_route_label if contact is not None else None
    )
    niche = opp_detail.get("niche")
    audience_band = opp_detail.get("audience_band")
    cadence = opp_detail.get("posting_cadence")
    score = opp_detail.get("overall_score")
    footer = _evidence_footer(citations)

    if kind == SalesAssetKind.OUTREACH_MESSAGE:
        facts: list[str] = []
        if niche:
            facts.append(f"your focus on {niche} content")
        if audience_band:
            facts.append(f"an audience in the {audience_band} band")
        if cadence:
            facts.append(f"a {cadence} posting cadence")
        fact_sentence = ""
        if facts:
            fact_sentence = (
                " From the public signals we reviewed, we noted " + ", ".join(facts) + "."
            )
        score_sentence = (
            f" Our internal commercial-fit review scored this opportunity "
            f"{score:g}/100 ({opp_detail.get('score_version')})."
            if score is not None
            else ""
        )
        route_sentence = (
            f" We are writing via the public route you publish ({route})." if route else ""
        )
        body = (
            f"Hello {contact_name},\n\n"
            "We help long-form creators turn their existing videos into "
            "short-form clips, with human review on every edit."
            f"{fact_sentence}{score_sentence}\n\n"
            "If you are open to it, we would welcome a short discovery call "
            f"to see whether a clipping pilot fits {account.name}."
            f"{route_sentence}\n\n"
            "No obligation — a simple no is a complete answer."
            f"{footer}"
        )
        return f"Outreach draft — {account.name}", body

    if kind == SalesAssetKind.FOLLOW_UP:
        latest = _citation(citations, "pipeline_event")
        stage_sentence = f"The account currently sits at stage '{account.stage.value}'."
        last_sentence = ""
        if latest is not None:
            detail = latest["detail"]
            last_sentence = (
                f" The most recent recorded step was '{detail['event_kind']}' on {detail['at']}."
            )
        next_sentence = (
            f" The recorded next action is: {account.next_action}." if account.next_action else ""
        )
        body = (
            f"Hello {contact_name},\n\n"
            "Following up on our earlier conversation — we are checking in "
            "to agree the next step."
            f"{stage_sentence}{last_sentence}{next_sentence}\n\n"
            "Happy to work around your schedule for a brief call."
            f"{footer}"
        )
        return f"Follow-up draft — {account.name}", body

    if kind == SalesAssetKind.PROPOSAL_OUTLINE:
        revenue = [citation for citation in citations if citation["type"] == "revenue_record"]
        commercial_lines = [
            f"- Existing recorded {item['detail']['kind']}: "
            f"{item['detail']['amount_minor_units']} {item['detail']['currency']} "
            f"({item['detail']['status']})"
            for item in revenue
        ]
        commercials = (
            "\n".join(commercial_lines)
            if commercial_lines
            else (
                "No figures are quoted in this outline: pricing is confirmed "
                "only after a discovery call, and no number appears here "
                "without a stored record behind it."
            )
        )
        fit_lines: list[str] = []
        if niche:
            fit_lines.append(f"- Content niche: {niche}")
        if audience_band:
            fit_lines.append(f"- Publicly shown audience band: {audience_band}")
        if cadence:
            fit_lines.append(f"- Posting cadence: {cadence}")
        if score is not None:
            fit_lines.append(
                f"- Commercial-fit score: {score:g}/100 ({opp_detail.get('score_version')})"
            )
        fit = "\n".join(fit_lines) if fit_lines else "- See cited evidence below."
        body = (
            f"Proposal outline — {account.name}\n\n"
            "Objective: a short-form clipping pilot built from existing "
            "long-form content, with human review before anything is "
            "published.\n\n"
            f"What we know (cited):\n{fit}\n\n"
            "Proposed scope: source review, clip selection, editing, "
            "quality and rights checks, and scheduled delivery.\n\n"
            f"Commercials (recorded facts only):\n{commercials}"
            f"{footer}"
        )
        return f"Proposal outline — {account.name}", body

    if kind == SalesAssetKind.DISCOVERY_AGENDA:
        body = (
            f"Discovery call agenda — {account.name}\n\n"
            "1. Your current long-form workflow and publishing goals (10 min)\n"
            "2. Which back-catalogue content is eligible for clipping, and "
            "rights position (10 min)\n"
            "3. Review workflow: who approves clips before publication (5 min)\n"
            "4. Pilot shape, timelines and success measures (5 min)\n\n"
            f"Context from cited evidence: stage '{account.stage.value}'"
            + (f"; niche {niche}" if niche else "")
            + (f"; posting cadence {cadence}" if cadence else "")
            + "."
            f"{footer}"
        )
        return f"Discovery agenda — {account.name}", body

    # ONBOARDING_CHECKLIST
    items = "\n".join(f"- [ ] {label}" for _key, label in DEFAULT_ONBOARDING_CHECKLIST)
    body = (
        f"Onboarding checklist — {account.name}\n\n"
        f"Welcome aboard. This checklist mirrors the onboarding record "
        f"(version {ONBOARDING_CHECKLIST_VERSION}) and is worked through "
        "with your account owner.\n\n"
        f"{items}"
        f"{footer}"
    )
    return f"Onboarding checklist — {account.name}", body
