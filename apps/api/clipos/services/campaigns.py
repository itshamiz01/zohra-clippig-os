"""v2.1 Campaign Hunter: campaigns, profitability, payouts.

Campaigns are operator-supplied facts. Intake is a manual create or an
owner-supplied seed import — no external campaign source is connected
and nothing scrapes one (Whop or anywhere else). Profitability is
computed only from recorded inputs: a campaign without a CPM rate or
an expected-views figure is *unassessable*, with the missing inputs
named, rather than being scored on invented numbers. Qualification is
gated on a recorded rights basis plus a computable assessment (or an
explicit, recorded override). Money actually collected for a campaign
exists only as a Phase 9 revenue record of kind ``campaign_payout``;
expected money lives only on assessments, and the two are reported
side by side, never combined.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from clipos.config import Settings
from clipos.core.enums import (
    CampaignAssessmentState,
    CampaignRightsBasis,
    CampaignSourceKind,
    CampaignStatus,
    RevenueKind,
    RevenueState,
)
from clipos.core.errors import ClipOSError, ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.models import (
    Campaign,
    CampaignAssessment,
    CampaignPublication,
    CampaignRequirement,
    Publication,
    RevenueRecord,
)
from clipos.models.entities import utcnow
from clipos.services.audit import AuditService
from clipos.services.crm import CrmService

_TERMINAL_STATUSES = frozenset(
    {CampaignStatus.COMPLETED, CampaignStatus.REJECTED, CampaignStatus.EXPIRED}
)

_TRANSITIONS: dict[CampaignStatus, frozenset[CampaignStatus]] = {
    CampaignStatus.CANDIDATE: frozenset(
        {CampaignStatus.QUALIFIED, CampaignStatus.REJECTED, CampaignStatus.EXPIRED}
    ),
    CampaignStatus.QUALIFIED: frozenset(
        {
            CampaignStatus.ACTIVE,
            CampaignStatus.REJECTED,
            CampaignStatus.EXPIRED,
        }
    ),
    CampaignStatus.ACTIVE: frozenset(
        {
            CampaignStatus.SUBMITTED,
            CampaignStatus.COMPLETED,
            CampaignStatus.REJECTED,
            CampaignStatus.EXPIRED,
        }
    ),
    CampaignStatus.SUBMITTED: frozenset(
        {CampaignStatus.COMPLETED, CampaignStatus.REJECTED, CampaignStatus.EXPIRED}
    ),
    CampaignStatus.COMPLETED: frozenset(),
    CampaignStatus.REJECTED: frozenset(),
    CampaignStatus.EXPIRED: frozenset(),
}


def campaign_is_expired(campaign: Campaign, *, now: datetime | None = None) -> bool:
    """Read-time expiry: a non-terminal campaign whose deadline has
    passed reads as expired without mutating its stored status."""
    if campaign.status in _TERMINAL_STATUSES:
        return campaign.status == CampaignStatus.EXPIRED
    if campaign.deadline_at is None:
        return False
    deadline = campaign.deadline_at
    if deadline.tzinfo is None:
        # SQLite does not preserve tzinfo on read; stored values are UTC.
        deadline = deadline.replace(tzinfo=UTC)
    return deadline < (now or utcnow())


def _request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CampaignService:
    """Tenant-scoped campaign CRUD, import, qualification, assessment
    and payout attribution. Every mutating method records an audit
    event; money movement is delegated to the Phase 9 revenue service."""

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    @staticmethod
    async def get_campaign(
        session: AsyncSession, tenant_id: UUID, campaign_id: UUID, *, detail: bool = False
    ) -> Campaign:
        statement = select(Campaign).where(
            Campaign.id == campaign_id, Campaign.tenant_id == tenant_id
        )
        if detail:
            statement = statement.options(
                selectinload(Campaign.requirements),
                selectinload(Campaign.publication_links),
                selectinload(Campaign.assessments),
            )
        campaign = await session.scalar(statement)
        if campaign is None:
            raise NotFoundError("campaign")
        return campaign

    @staticmethod
    async def list_campaigns(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        status: CampaignStatus | None = None,
        platform: str | None = None,
        limit: int = 100,
    ) -> list[Campaign]:
        statement = select(Campaign).where(Campaign.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(Campaign.status == status)
        if platform is not None:
            statement = statement.where(Campaign.platform == platform)
        statement = statement.order_by(desc(Campaign.created_at))
        return list((await session.scalars(statement.limit(min(limit, 200)))).all())

    @staticmethod
    async def latest_assessment(
        session: AsyncSession, tenant_id: UUID, campaign_id: UUID
    ) -> CampaignAssessment | None:
        return await session.scalar(
            select(CampaignAssessment)
            .where(
                CampaignAssessment.tenant_id == tenant_id,
                CampaignAssessment.campaign_id == campaign_id,
            )
            .order_by(desc(CampaignAssessment.created_at))
            .limit(1)
        )

    @staticmethod
    async def list_assessments(
        session: AsyncSession, tenant_id: UUID, campaign_id: UUID, *, limit: int = 50
    ) -> list[CampaignAssessment]:
        await CampaignService.get_campaign(session, tenant_id, campaign_id)
        rows = await session.scalars(
            select(CampaignAssessment)
            .where(
                CampaignAssessment.tenant_id == tenant_id,
                CampaignAssessment.campaign_id == campaign_id,
            )
            .order_by(desc(CampaignAssessment.created_at))
            .limit(min(limit, 200))
        )
        return list(rows.all())

    # ------------------------------------------------------------------
    # Create / update / import
    # ------------------------------------------------------------------
    @staticmethod
    async def create_campaign(
        session: AsyncSession,
        *,
        principal: Principal,
        fields: dict[str, Any],
        requirements: list[dict[str, Any]],
        source_kind: CampaignSourceKind = CampaignSourceKind.MANUAL,
        idempotency_key: str | None = None,
    ) -> tuple[Campaign, bool]:
        request_hash: str | None = None
        if idempotency_key is not None:
            request_hash = _request_hash({"fields": fields, "requirements": requirements})
            existing = await session.scalar(
                select(Campaign).where(
                    Campaign.tenant_id == principal.tenant_id,
                    Campaign.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ConflictError("campaign idempotency key was reused with different input")
                return existing, False
        if fields.get("crm_account_id") is not None:
            await CrmService.get_account(session, principal.tenant_id, fields["crm_account_id"])
        campaign = Campaign(
            tenant_id=principal.tenant_id,
            created_by_user_id=principal.user_id,
            source_kind=source_kind,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            currency=str(fields["currency"]).upper(),
            **{
                key: value
                for key, value in fields.items()
                if key not in {"currency", "crm_account_id"}
            },
            crm_account_id=fields.get("crm_account_id"),
        )
        session.add(campaign)
        await session.flush()
        for requirement in requirements:
            session.add(
                CampaignRequirement(
                    tenant_id=principal.tenant_id, campaign_id=campaign.id, **requirement
                )
            )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="campaign.created",
            resource_type="campaign",
            resource_id=str(campaign.id),
            event_data={
                "source_kind": source_kind.value,
                "platform": campaign.platform,
                "rights_basis": campaign.rights_basis.value,
            },
        )
        await session.commit()
        await session.refresh(campaign)
        return campaign, True

    @staticmethod
    async def update_campaign(
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        updates: dict[str, Any],
    ) -> Campaign:
        campaign = await CampaignService.get_campaign(session, principal.tenant_id, campaign_id)
        if updates.get("crm_account_id") is not None:
            await CrmService.get_account(session, principal.tenant_id, updates["crm_account_id"])
        changed: dict[str, Any] = {}
        for key, value in updates.items():
            if value is not None and getattr(campaign, key) != value:
                setattr(campaign, key, value)
                changed[key] = str(value)
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="campaign.updated",
            resource_type="campaign",
            resource_id=str(campaign.id),
            event_data={"changed_fields": sorted(changed)},
        )
        await session.commit()
        await session.refresh(campaign)
        return campaign

    @staticmethod
    async def import_campaigns(
        session: AsyncSession,
        *,
        principal: Principal,
        records: list[dict[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Synchronous seed import of owner-supplied listings.

        A record whose ``source_reference`` already exists in the
        tenant is skipped as a duplicate (the listing was already
        imported); re-running the whole import with the same
        idempotency key replays to the same outcome because every
        previously created record is then a duplicate.
        """
        created: list[UUID] = []
        skipped: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            reference = record.get("source_reference")
            if reference:
                duplicate = await session.scalar(
                    select(Campaign.id).where(
                        Campaign.tenant_id == principal.tenant_id,
                        Campaign.source_reference == reference,
                    )
                )
                if duplicate is not None:
                    skipped.append(
                        {"index": index, "reason": "duplicate source_reference already imported"}
                    )
                    continue
            requirements = record.pop("requirements", [])
            campaign, _ = await CampaignService._create_imported(
                session,
                principal=principal,
                fields=record,
                requirements=requirements,
                source_reference=reference,
            )
            created.append(campaign.id)
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="campaign.imported",
            resource_type="campaign_import",
            resource_id=None,
            event_data={
                "idempotency_key": idempotency_key,
                "created": len(created),
                "skipped": len(skipped),
            },
        )
        await session.commit()
        return {"created": created, "skipped": skipped, "replayed": False}

    @staticmethod
    async def _create_imported(
        session: AsyncSession,
        *,
        principal: Principal,
        fields: dict[str, Any],
        requirements: list[dict[str, Any]],
        source_reference: str | None,
    ) -> tuple[Campaign, bool]:
        fields = {key: value for key, value in fields.items() if key != "source_reference"}
        campaign = Campaign(
            tenant_id=principal.tenant_id,
            created_by_user_id=principal.user_id,
            source_kind=CampaignSourceKind.SEED_IMPORT,
            source_reference=source_reference,
            currency=str(fields.pop("currency", "USD")).upper(),
            **fields,
        )
        session.add(campaign)
        await session.flush()
        for requirement in requirements:
            session.add(
                CampaignRequirement(
                    tenant_id=principal.tenant_id, campaign_id=campaign.id, **requirement
                )
            )
        await session.flush()
        return campaign, True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    async def transition_campaign(
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        target_status: CampaignStatus,
        override_reason: str | None = None,
    ) -> Campaign:
        campaign = await CampaignService.get_campaign(session, principal.tenant_id, campaign_id)
        source_status = campaign.status
        if target_status not in _TRANSITIONS[source_status]:
            raise ConflictError(
                f"campaign cannot transition from {source_status.value} to {target_status.value}"
            )
        override_used = False
        if source_status == CampaignStatus.CANDIDATE and target_status == CampaignStatus.QUALIFIED:
            if campaign.rights_basis == CampaignRightsBasis.UNKNOWN:
                raise ClipOSError(
                    "campaign_rights_unknown",
                    "a campaign with an unknown rights basis cannot be qualified; "
                    "record the rights basis first",
                    status_code=422,
                )
            latest = await CampaignService.latest_assessment(
                session, principal.tenant_id, campaign.id
            )
            assessable = latest is not None and latest.state == CampaignAssessmentState.ASSESSABLE
            if not assessable:
                if not override_reason or not override_reason.strip():
                    missing = (
                        ", ".join(latest.missing_inputs)
                        if latest is not None
                        else "no assessment recorded"
                    )
                    raise ClipOSError(
                        "campaign_unassessable",
                        "campaign has no computable profitability assessment "
                        f"({missing}); qualify with an explicit override_reason or "
                        "record the missing inputs and reassess",
                        status_code=422,
                    )
                override_used = True
                campaign.qualification_override_reason = override_reason.strip()
        campaign.status = target_status
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="campaign.status_changed",
            resource_type="campaign",
            resource_id=str(campaign.id),
            event_data={
                "from": source_status.value,
                "to": target_status.value,
                "assessment_override": override_used,
            },
        )
        await session.commit()
        await session.refresh(campaign)
        return campaign

    # ------------------------------------------------------------------
    # Profitability
    # ------------------------------------------------------------------
    @staticmethod
    async def assess_profitability(
        session: AsyncSession,
        *,
        principal: Principal,
        settings: Settings,
        campaign_id: UUID,
        expected_views: int | None = None,
        editing_cost_minor: int | None = None,
        model_cost_minor: int | None = None,
        distribution_cost_minor: int | None = None,
        risk_percent: int | None = None,
    ) -> CampaignAssessment:
        """Persist one immutable profitability assessment.

        Expected gross = expected_views × CPM / 1000 (rounded half up),
        capped at the campaign's recorded maximum payout when one is
        set. Risk adjustment = risk_percent of gross. Missing CPM or
        expected views produces an ``unassessable`` row naming the
        missing inputs — never an estimate.
        """
        campaign = await CampaignService.get_campaign(session, principal.tenant_id, campaign_id)
        views = expected_views if expected_views is not None else campaign.expected_views
        editing = (
            editing_cost_minor
            if editing_cost_minor is not None
            else settings.campaign_editing_cost_minor
        )
        model = (
            model_cost_minor if model_cost_minor is not None else settings.campaign_model_cost_minor
        )
        distribution = (
            distribution_cost_minor
            if distribution_cost_minor is not None
            else settings.campaign_distribution_cost_minor
        )
        risk = risk_percent if risk_percent is not None else settings.campaign_risk_percent

        missing: list[str] = []
        if campaign.cpm_rate_minor is None:
            missing.append("cpm_rate_minor")
        if views is None:
            missing.append("expected_views")

        gross: int | None = None
        risk_adjustment: int | None = None
        profit: int | None = None
        capped = False
        if not missing:
            assert campaign.cpm_rate_minor is not None and views is not None
            gross = (views * campaign.cpm_rate_minor + 500) // 1000
            if campaign.max_payout_minor is not None and gross > campaign.max_payout_minor:
                gross = campaign.max_payout_minor
                capped = True
            risk_adjustment = (gross * risk + 50) // 100
            profit = gross - editing - model - distribution - risk_adjustment

        assessment = CampaignAssessment(
            tenant_id=principal.tenant_id,
            campaign_id=campaign.id,
            created_by_user_id=principal.user_id,
            state=(
                CampaignAssessmentState.UNASSESSABLE
                if missing
                else CampaignAssessmentState.ASSESSABLE
            ),
            missing_inputs=missing,
            expected_views=views,
            cpm_rate_minor=campaign.cpm_rate_minor,
            currency=campaign.currency,
            gross_revenue_minor=gross,
            capped_by_max_payout=capped,
            editing_cost_minor=editing,
            model_cost_minor=model,
            distribution_cost_minor=distribution,
            risk_percent=risk,
            risk_adjustment_minor=risk_adjustment,
            expected_profit_minor=profit,
        )
        session.add(assessment)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="campaign.assessed",
            resource_type="campaign",
            resource_id=str(campaign.id),
            event_data={
                "assessment_id": str(assessment.id),
                "state": assessment.state.value,
                "missing_inputs": missing,
                "expected_profit_minor": profit,
                "currency": campaign.currency,
            },
        )
        await session.commit()
        await session.refresh(assessment)
        return assessment

    # ------------------------------------------------------------------
    # Publication links and payouts
    # ------------------------------------------------------------------
    @staticmethod
    async def link_publication(
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        publication_id: UUID,
    ) -> CampaignPublication:
        campaign = await CampaignService.get_campaign(session, principal.tenant_id, campaign_id)
        publication = await session.scalar(
            select(Publication).where(
                Publication.id == publication_id,
                Publication.tenant_id == principal.tenant_id,
            )
        )
        if publication is None:
            raise NotFoundError("publication")
        existing = await session.scalar(
            select(CampaignPublication).where(
                CampaignPublication.campaign_id == campaign.id,
                CampaignPublication.publication_id == publication_id,
            )
        )
        if existing is not None:
            raise ConflictError("publication is already linked to this campaign")
        link = CampaignPublication(
            tenant_id=principal.tenant_id,
            campaign_id=campaign.id,
            publication_id=publication_id,
            created_by_user_id=principal.user_id,
        )
        session.add(link)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="campaign.publication_linked",
            resource_type="campaign",
            resource_id=str(campaign.id),
            event_data={"publication_id": str(publication_id)},
        )
        await session.commit()
        await session.refresh(link)
        return link

    @staticmethod
    async def record_payout(
        session: AsyncSession,
        *,
        principal: Principal,
        campaign_id: UUID,
        amount_minor_units: int,
        currency: str,
        account_id: UUID | None = None,
        external_reference: str | None = None,
        occurred_at: datetime | None = None,
        notes: str | None = None,
    ) -> RevenueRecord:
        """Record money actually collected for a campaign.

        The payout exists only as a Phase 9 revenue record (kind
        ``campaign_payout``, status ``paid``) created through the CRM
        revenue service — there is no parallel campaign money table.
        The currency must match the campaign's recorded currency.
        """
        campaign = await CampaignService.get_campaign(session, principal.tenant_id, campaign_id)
        if currency.upper() != campaign.currency:
            raise ClipOSError(
                "campaign_payout_currency_mismatch",
                f"payout currency {currency.upper()} does not match the campaign "
                f"currency {campaign.currency}",
                status_code=422,
            )
        resolved_account = account_id or campaign.crm_account_id
        if resolved_account is None:
            raise ClipOSError(
                "campaign_payout_account_required",
                "recording a payout requires a CRM account: link one to the "
                "campaign (crm_account_id) or provide account_id",
                status_code=422,
            )
        record = await CrmService.create_revenue_record(
            session,
            principal=principal,
            account_id=resolved_account,
            kind=RevenueKind.CAMPAIGN_PAYOUT,
            amount_minor_units=amount_minor_units,
            currency=currency,
            status=RevenueState.PAID,
            external_reference=external_reference,
            occurred_at=occurred_at or utcnow(),
            notes=notes,
            campaign_id=campaign.id,
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="campaign.payout_recorded",
            resource_type="campaign",
            resource_id=str(campaign.id),
            event_data={
                "revenue_record_id": str(record.id),
                "amount_minor_units": amount_minor_units,
                "currency": currency.upper(),
            },
        )
        await session.commit()
        return record

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    @staticmethod
    async def summary(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
        """Campaign economics in the analytics summary style: counts by
        (effective) status plus expected vs collected per campaign."""
        campaigns = await CampaignService.list_campaigns(session, tenant_id, limit=200)
        now = utcnow()
        counts: dict[str, int] = {}
        expired_open = 0
        economics: list[dict[str, Any]] = []
        for campaign in campaigns:
            expired = campaign_is_expired(campaign, now=now)
            effective = CampaignStatus.EXPIRED if expired else campaign.status
            counts[effective.value] = counts.get(effective.value, 0) + 1
            if expired and campaign.status != CampaignStatus.EXPIRED:
                expired_open += 1
            latest = await CampaignService.latest_assessment(session, tenant_id, campaign.id)
            expected = (
                latest.expected_profit_minor
                if latest is not None and latest.state == CampaignAssessmentState.ASSESSABLE
                else None
            )
            payouts = await session.scalars(
                select(RevenueRecord).where(
                    RevenueRecord.tenant_id == tenant_id,
                    RevenueRecord.campaign_id == campaign.id,
                    RevenueRecord.kind == RevenueKind.CAMPAIGN_PAYOUT,
                    RevenueRecord.status == RevenueState.PAID,
                )
            )
            payout_rows = payouts.all()
            linked = await session.scalar(
                select(func.count())
                .select_from(CampaignPublication)
                .where(CampaignPublication.campaign_id == campaign.id)
            )
            economics.append(
                {
                    "campaign_id": campaign.id,
                    "name": campaign.name,
                    "status": effective,
                    "expired": expired,
                    "currency": campaign.currency,
                    "expected_profit_minor": expected,
                    "collected_minor": sum(row.amount_minor_units for row in payout_rows),
                    "payout_count": len(payout_rows),
                    "linked_publications": linked or 0,
                }
            )
        return {
            "counts_by_status": counts,
            "expired_open": expired_open,
            "campaigns": economics,
        }
