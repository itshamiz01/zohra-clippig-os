from __future__ import annotations

import difflib
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from clipos.core.enums import (
    AuditOutcome,
    JobState,
    OpportunityHandoffState,
    OpportunitySourceKind,
    OpportunitySourceStatus,
    OpportunityState,
    OpportunitySyncState,
    Role,
    SuppressionMatchType,
)
from clipos.core.errors import ClipOSError, ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.gateways.opportunities import (
    OpportunityAdapterRegistry,
    OpportunitySourceContext,
    SourcedOpportunity,
)
from clipos.gateways.opportunities.errors import OpportunityAdapterError
from clipos.gateways.opportunities.normalize import (
    compute_dedup_key,
    normalize_handle,
    normalize_url,
    url_host,
)
from clipos.models import (
    BackgroundJob,
    Opportunity,
    OpportunityHandoff,
    OpportunityScoreComponent,
    OpportunitySource,
    OpportunitySuppression,
    OpportunitySyncRun,
)
from clipos.models.entities import utcnow
from clipos.opportunities.content_debt import (
    DEFAULT_MOMENTS_PER_EPISODE,
    ContentDebt,
    ContentDebtSignals,
    score_content_debt,
)
from clipos.opportunities.scoring import SCORE_VERSION, OpportunitySignals, score_opportunity
from clipos.services.audit import AuditService
from clipos.services.jobs import JobService

OPPORTUNITY_JOB_KIND = "opportunity.sync"
OPPORTUNITY_HANDOFF_VERSION = "opportunity-handoff.v1"

# Fuzzy duplicate detection is deliberately conservative: only very
# similar creator/title text is flagged, and a flag is all that happens —
# a human decides whether two records are the same creator. Nothing is
# ever auto-merged on a fuzzy match.
_FUZZY_THRESHOLD = 0.86
_FUZZY_SCAN_LIMIT = 500

_TRANSITIONS: dict[OpportunityState, frozenset[OpportunityState]] = {
    OpportunityState.NEW: frozenset(
        {
            OpportunityState.REVIEWING,
            OpportunityState.SHORTLISTED,
            OpportunityState.REJECTED,
            OpportunityState.SUPPRESSED,
        }
    ),
    OpportunityState.REVIEWING: frozenset(
        {
            OpportunityState.SHORTLISTED,
            OpportunityState.REJECTED,
            OpportunityState.SUPPRESSED,
        }
    ),
    OpportunityState.SHORTLISTED: frozenset(
        {
            OpportunityState.REVIEWING,
            OpportunityState.REJECTED,
            OpportunityState.SUPPRESSED,
        }
    ),
    OpportunityState.REJECTED: frozenset({OpportunityState.REVIEWING}),
    OpportunityState.SUPPRESSED: frozenset(),
}
_REVIEWER_ONLY_TARGETS = frozenset(
    {OpportunityState.SHORTLISTED, OpportunityState.REJECTED, OpportunityState.SUPPRESSED}
)

_HANDOFF_TRANSITIONS: dict[OpportunityHandoffState, frozenset[OpportunityHandoffState]] = {
    OpportunityHandoffState.DRAFT: frozenset({OpportunityHandoffState.READY}),
    OpportunityHandoffState.READY: frozenset(),
}


def opportunity_capabilities(registry: OpportunityAdapterRegistry) -> list[dict[str, object]]:
    capabilities: list[dict[str, object]] = []
    for kind in OpportunitySourceKind:
        adapter = registry.get(kind)
        capabilities.append(
            {
                "kind": adapter.capabilities.kind,
                "mode": adapter.capabilities.mode,
                "limitations": list(adapter.capabilities.limitations),
                "source": adapter.source,
            }
        )
    return capabilities


class OpportunityService:
    """Phase 8 opportunity hunting and commercial fit.

    Isolation contract: this service writes only to the Phase 8 tables
    (sources, sync runs, opportunities, score components, suppressions
    and handoffs) plus audit events and its own background jobs. There is
    deliberately no outreach surface anywhere in Phase 8: no email, no
    direct messages, no contact-form submission — a public contact route
    is stored as a text label for later human-led phases only.
    """

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    @staticmethod
    async def create_source(
        session: AsyncSession,
        *,
        principal: Principal,
        registry: OpportunityAdapterRegistry,
        kind: OpportunitySourceKind,
        name: str,
        config: dict[str, Any],
    ) -> OpportunitySource:
        _validate_source_config(kind, config)
        source = OpportunitySource(
            tenant_id=principal.tenant_id,
            created_by_user_id=principal.user_id,
            kind=kind,
            name=name,
            config=config,
            status=OpportunitySourceStatus.ENABLED,
            adapter_mode=registry.get(kind).mode,
        )
        session.add(source)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="opportunity.source_created",
            resource_type="opportunity_source",
            resource_id=str(source.id),
            event_data={"kind": kind.value, "adapter_mode": source.adapter_mode.value},
        )
        await session.commit()
        await session.refresh(source)
        return source

    @staticmethod
    async def list_sources(
        session: AsyncSession,
        tenant_id: UUID,
        registry: OpportunityAdapterRegistry,
        *,
        limit: int = 100,
    ) -> list[OpportunitySource]:
        rows = list(
            (
                await session.scalars(
                    select(OpportunitySource)
                    .where(OpportunitySource.tenant_id == tenant_id)
                    .order_by(desc(OpportunitySource.created_at))
                    .limit(min(limit, 200))
                )
            ).all()
        )
        for source in rows:
            # Report the adapter mode that would serve the source now,
            # not a stale snapshot from creation time.
            source.adapter_mode = registry.get(source.kind).mode
        return rows

    @staticmethod
    async def get_source(
        session: AsyncSession,
        tenant_id: UUID,
        source_id: UUID,
        registry: OpportunityAdapterRegistry | None = None,
    ) -> OpportunitySource:
        source = await session.scalar(
            select(OpportunitySource).where(
                OpportunitySource.id == source_id,
                OpportunitySource.tenant_id == tenant_id,
            )
        )
        if source is None:
            raise NotFoundError("opportunity source")
        if registry is not None:
            source.adapter_mode = registry.get(source.kind).mode
        return source

    @staticmethod
    async def update_source(
        session: AsyncSession,
        *,
        principal: Principal,
        registry: OpportunityAdapterRegistry,
        source_id: UUID,
        name: str | None,
        status: OpportunitySourceStatus | None,
        config: dict[str, Any] | None,
    ) -> OpportunitySource:
        source = await OpportunityService.get_source(session, principal.tenant_id, source_id)
        if config is not None:
            _validate_source_config(source.kind, config)
            source.config = config
        if name is not None:
            source.name = name
        if status is not None:
            source.status = status
        source.adapter_mode = registry.get(source.kind).mode
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="opportunity.source_updated",
            resource_type="opportunity_source",
            resource_id=str(source.id),
            event_data={"status": source.status.value},
        )
        await session.commit()
        await session.refresh(source)
        return source

    # ------------------------------------------------------------------
    # Sync (import)
    # ------------------------------------------------------------------
    @staticmethod
    async def create_sync_run(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        source_id: UUID,
        requested_by_user_id: UUID | None,
        background_job_id: UUID | None = None,
    ) -> OpportunitySyncRun:
        run = OpportunitySyncRun(
            tenant_id=tenant_id,
            source_id=source_id,
            requested_by_user_id=requested_by_user_id,
            background_job_id=background_job_id,
            state=OpportunitySyncState.QUEUED,
        )
        session.add(run)
        await session.flush()
        return run

    @staticmethod
    async def queue_sync(
        session: AsyncSession,
        *,
        principal: Principal,
        source_id: UUID,
        idempotency_key: str,
        records: list[dict[str, Any]] | None,
        max_import_records: int,
    ) -> tuple[OpportunitySyncRun, BackgroundJob, bool]:
        """Create a sync run plus its durable background job.

        Mirrors the Phase 6/7 pattern: the domain endpoint owns job
        creation for its reserved kind, and the generic jobs endpoint
        rejects the kind.
        """
        source = await OpportunityService.get_source(session, principal.tenant_id, source_id)
        if source.status != OpportunitySourceStatus.ENABLED:
            raise ConflictError("opportunity source is disabled")
        if source.kind == OpportunitySourceKind.SEED_LIST and not records:
            raise ClipOSError(
                "seed_list_payload_missing",
                "seed list sync requires a non-empty records payload",
                status_code=422,
            )
        if records is not None and len(records) > max_import_records:
            raise ClipOSError(
                "opportunity_import_too_large",
                f"import carries {len(records)} records; the cap is {max_import_records}",
                status_code=422,
            )
        job, created = await JobService.create(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            kind=OPPORTUNITY_JOB_KIND,
            idempotency_key=idempotency_key,
            payload={"source_id": str(source.id), "records": records or []},
            max_attempts=3,
        )
        run = await session.scalar(
            select(OpportunitySyncRun).where(
                OpportunitySyncRun.background_job_id == job.id,
                OpportunitySyncRun.tenant_id == principal.tenant_id,
            )
        )
        if run is None:
            run = await OpportunityService.create_sync_run(
                session,
                tenant_id=principal.tenant_id,
                source_id=source.id,
                requested_by_user_id=principal.user_id,
                background_job_id=job.id,
            )
        return run, job, created

    @staticmethod
    async def sync_source(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        requested_by_user_id: UUID | None,
        registry: OpportunityAdapterRegistry,
        source: OpportunitySource,
        import_payload: list[dict[str, Any]] | None,
    ) -> OpportunitySyncRun:
        """Synchronous sync: create a run and execute it inline."""
        run = await OpportunityService.create_sync_run(
            session,
            tenant_id=tenant_id,
            source_id=source.id,
            requested_by_user_id=requested_by_user_id,
        )
        await session.commit()
        return await OpportunityService.execute_sync(
            session, run=run, registry=registry, import_payload=import_payload
        )

    @staticmethod
    async def execute_sync(
        session: AsyncSession,
        *,
        run: OpportunitySyncRun,
        registry: OpportunityAdapterRegistry,
        import_payload: list[dict[str, Any]] | None,
    ) -> OpportunitySyncRun:
        run.state = OpportunitySyncState.RUNNING
        run.started_at = utcnow()
        await session.commit()

        source = await session.scalar(
            select(OpportunitySource).where(
                OpportunitySource.id == run.source_id,
                OpportunitySource.tenant_id == run.tenant_id,
            )
        )
        if source is None:  # pragma: no cover - the run cannot outlive its source
            run.state = OpportunitySyncState.FAILED
            run.failure_category = "opportunity_source_missing"
            run.failure_message = "sync run references a source that no longer exists"
            run.finished_at = utcnow()
            await session.commit()
            return run

        adapter = registry.get(source.kind)
        run.adapter_mode = adapter.mode
        context = OpportunitySourceContext(
            source_id=source.id,
            tenant_id=run.tenant_id,
            kind=source.kind,
            name=source.name,
            config=source.config,
        )
        try:
            records = await adapter.fetch_opportunities(
                context=context, import_payload=import_payload
            )
        except OpportunityAdapterError as exc:
            run.state = OpportunitySyncState.FAILED
            run.failure_category = exc.category[:100]
            run.failure_message = exc.safe_message[:500]
            run.finished_at = utcnow()
            AuditService.record(
                session,
                tenant_id=run.tenant_id,
                actor_user_id=run.requested_by_user_id,
                action="opportunity.sync_completed",
                resource_type="opportunity_sync_run",
                resource_id=str(run.id),
                outcome=AuditOutcome.FAILURE,
                event_data={
                    "state": run.state.value,
                    "adapter_mode": run.adapter_mode.value,
                    "failure_category": run.failure_category,
                },
            )
            await session.commit()
            await session.refresh(run)
            return run

        run.records_received = len(records)
        suppressions = await OpportunityService._tenant_suppressions(session, run.tenant_id)
        for record in records:
            await OpportunityService._import_record(
                session, run=run, source=source, record=record, suppressions=suppressions
            )
        run.state = OpportunitySyncState.SUCCEEDED
        run.finished_at = utcnow()
        source.last_synced_at = run.finished_at
        source.adapter_mode = adapter.mode
        AuditService.record(
            session,
            tenant_id=run.tenant_id,
            actor_user_id=run.requested_by_user_id,
            action="opportunity.sync_completed",
            resource_type="opportunity_sync_run",
            resource_id=str(run.id),
            event_data={
                "state": run.state.value,
                "adapter_mode": run.adapter_mode.value,
                "records_received": run.records_received,
                "records_created": run.records_created,
                "records_merged": run.records_merged,
                "duplicates_flagged": run.duplicates_flagged,
                "suppressed_skipped": run.suppressed_skipped,
            },
        )
        await session.commit()
        await session.refresh(run)
        return run

    @staticmethod
    async def _import_record(
        session: AsyncSession,
        *,
        run: OpportunitySyncRun,
        source: OpportunitySource,
        record: SourcedOpportunity,
        suppressions: list[OpportunitySuppression],
    ) -> None:
        creator_handle = normalize_handle(record.creator_handle)
        canonical_url = normalize_url(record.canonical_url)
        dedup_key = compute_dedup_key(
            platform=record.platform,
            creator_handle=creator_handle,
            canonical_url=canonical_url,
            external_key=record.external_key,
        )
        match = _suppression_match(
            suppressions,
            creator_handle=creator_handle,
            canonical_url=canonical_url,
            title=record.title,
            creator_name=record.creator_name,
            niche=record.niche,
        )
        existing = await OpportunityService._find_exact(
            session,
            tenant_id=run.tenant_id,
            source_id=source.id,
            external_key=record.external_key,
            dedup_key=dedup_key,
        )
        if match is not None:
            run.suppressed_skipped += 1
            if existing is not None and existing.state != OpportunityState.SUPPRESSED:
                existing.state = OpportunityState.SUPPRESSED
                existing.sync_run_id = run.id
            return
        if existing is not None:
            _apply_record(
                existing,
                record,
                creator_handle=creator_handle,
                canonical_url=canonical_url,
                dedup_key=dedup_key,
            )
            existing.sync_run_id = run.id
            await session.flush()
            await OpportunityService._persist_score(session, existing)
            run.records_merged += 1
            return
        fuzzy = await OpportunityService._find_fuzzy(
            session,
            tenant_id=run.tenant_id,
            title=record.title,
            creator_name=record.creator_name,
        )
        opportunity = Opportunity(
            tenant_id=run.tenant_id,
            source_id=source.id,
            sync_run_id=run.id,
            external_key=record.external_key,
            title=record.title,
            creator_name=record.creator_name,
            creator_handle=creator_handle,
            canonical_url=canonical_url,
            platform=record.platform,
            contact_route_label=record.contact_route_label,
            evidence=_evidence_for(record),
            niche=record.niche,
            language=record.language,
            audience_band=record.audience_band,
            posting_cadence=record.posting_cadence,
            library_episode_count=record.library_episode_count,
            library_long_form_hours=record.library_long_form_hours,
            existing_shorts_count=record.existing_shorts_count,
            moments_per_episode_assumption=record.moments_per_episode,
            dedup_key=dedup_key,
            possible_duplicate_of_id=fuzzy.id if fuzzy is not None else None,
            state=OpportunityState.NEW,
        )
        session.add(opportunity)
        await session.flush()
        await OpportunityService._persist_score(session, opportunity)
        run.records_created += 1
        if fuzzy is not None:
            run.duplicates_flagged += 1

    @staticmethod
    async def _find_exact(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        source_id: UUID,
        external_key: str,
        dedup_key: str,
    ) -> Opportunity | None:
        # dedup_key carries no unique constraint (identity is tenant-wide
        # while uniqueness is per source), so take the earliest match
        # rather than assuming at most one row can exist.
        by_dedup = await session.scalar(
            select(Opportunity)
            .where(
                Opportunity.tenant_id == tenant_id,
                Opportunity.dedup_key == dedup_key,
            )
            .order_by(Opportunity.created_at)
            .limit(1)
        )
        if by_dedup is not None:
            return by_dedup
        return await session.scalar(
            select(Opportunity).where(
                Opportunity.tenant_id == tenant_id,
                Opportunity.source_id == source_id,
                Opportunity.external_key == external_key,
            )
        )

    @staticmethod
    async def _find_fuzzy(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        title: str,
        creator_name: str | None,
    ) -> Opportunity | None:
        rows = list(
            (
                await session.scalars(
                    select(Opportunity)
                    .where(
                        Opportunity.tenant_id == tenant_id,
                        Opportunity.state != OpportunityState.SUPPRESSED,
                    )
                    .order_by(desc(Opportunity.created_at))
                    .limit(_FUZZY_SCAN_LIMIT)
                )
            ).all()
        )
        probe = _similarity_text(creator_name or title)
        if not probe:
            return None
        for row in rows:
            candidate = _similarity_text(row.creator_name or row.title)
            if candidate and _text_similarity(probe, candidate) >= _FUZZY_THRESHOLD:
                return row
        return None

    @staticmethod
    async def list_sync_runs(
        session: AsyncSession, tenant_id: UUID, *, limit: int = 50
    ) -> list[OpportunitySyncRun]:
        return list(
            (
                await session.scalars(
                    select(OpportunitySyncRun)
                    .where(OpportunitySyncRun.tenant_id == tenant_id)
                    .order_by(desc(OpportunitySyncRun.created_at))
                    .limit(min(limit, 200))
                )
            ).all()
        )

    @staticmethod
    async def get_sync_run(
        session: AsyncSession, tenant_id: UUID, run_id: UUID
    ) -> OpportunitySyncRun:
        run = await session.scalar(
            select(OpportunitySyncRun).where(
                OpportunitySyncRun.id == run_id,
                OpportunitySyncRun.tenant_id == tenant_id,
            )
        )
        if run is None:
            raise NotFoundError("opportunity sync run")
        return run

    # ------------------------------------------------------------------
    # Opportunities
    # ------------------------------------------------------------------
    @staticmethod
    async def list_opportunities(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        state: OpportunityState | None = None,
        source_id: UUID | None = None,
        platform: str | None = None,
        min_score: float | None = None,
        sort: str = "score",
        limit: int = 50,
    ) -> list[Opportunity]:
        statement = select(Opportunity).where(Opportunity.tenant_id == tenant_id)
        if state is not None:
            statement = statement.where(Opportunity.state == state)
        else:
            # Suppressed opportunities leave the working list by default;
            # they remain visible when explicitly requested by state.
            statement = statement.where(Opportunity.state != OpportunityState.SUPPRESSED)
        if source_id is not None:
            statement = statement.where(Opportunity.source_id == source_id)
        if platform is not None:
            statement = statement.where(Opportunity.platform == platform)
        if min_score is not None:
            statement = statement.where(Opportunity.overall_score >= min_score)
        if sort == "recent":
            statement = statement.order_by(desc(Opportunity.created_at))
        else:
            statement = statement.order_by(
                Opportunity.overall_score.is_(None),
                desc(Opportunity.overall_score),
                desc(Opportunity.created_at),
            )
        statement = statement.options(selectinload(Opportunity.score_components))
        return list((await session.scalars(statement.limit(min(limit, 200)))).all())

    @staticmethod
    async def get_opportunity(
        session: AsyncSession, tenant_id: UUID, opportunity_id: UUID
    ) -> Opportunity:
        opportunity = await session.scalar(
            select(Opportunity)
            .where(
                Opportunity.id == opportunity_id,
                Opportunity.tenant_id == tenant_id,
            )
            .options(selectinload(Opportunity.score_components))
        )
        if opportunity is None:
            raise NotFoundError("opportunity")
        return opportunity

    @staticmethod
    async def transition_opportunity(
        session: AsyncSession,
        *,
        principal: Principal,
        opportunity_id: UUID,
        target_state: OpportunityState,
    ) -> Opportunity:
        if target_state in _REVIEWER_ONLY_TARGETS:
            # Shortlisting, rejection and suppression are review
            # decisions; editors may only move records into review.
            principal.require(Role.REVIEWER)
        opportunity = await OpportunityService.get_opportunity(
            session, principal.tenant_id, opportunity_id
        )
        source_state = opportunity.state
        if target_state not in _TRANSITIONS[source_state]:
            raise ConflictError(
                f"opportunity cannot transition from {source_state.value} to {target_state.value}"
            )
        opportunity.state = target_state
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="opportunity.transitioned",
            resource_type="opportunity",
            resource_id=str(opportunity.id),
            event_data={"from": source_state.value, "to": target_state.value},
        )
        await session.commit()
        await session.refresh(opportunity)
        return opportunity

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    @staticmethod
    async def _persist_score(session: AsyncSession, opportunity: Opportunity) -> None:
        scored = score_opportunity(_signals_for_row(opportunity))
        existing = await session.scalars(
            select(OpportunityScoreComponent).where(
                OpportunityScoreComponent.opportunity_id == opportunity.id
            )
        )
        for component_row in existing.all():
            await session.delete(component_row)
        await session.flush()
        for component in scored.components:
            session.add(
                OpportunityScoreComponent(
                    tenant_id=opportunity.tenant_id,
                    opportunity_id=opportunity.id,
                    key=component.key,
                    label=component.label,
                    meaning=component.meaning,
                    score=component.score,
                    weight=component.weight,
                    weighted_score=component.weighted_score,
                    applicable=component.applicable,
                    evidence=component.evidence,
                    missing_behavior=component.missing_behavior,
                    scoring_version=SCORE_VERSION,
                )
            )
        opportunity.overall_score = scored.overall_score
        opportunity.score_version = SCORE_VERSION
        await session.flush()

    # ------------------------------------------------------------------
    # Suppressions
    # ------------------------------------------------------------------
    @staticmethod
    async def create_suppression(
        session: AsyncSession,
        *,
        principal: Principal,
        match_type: SuppressionMatchType,
        match_value: str,
        reason: str,
    ) -> OpportunitySuppression:
        normalized = _normalize_suppression_value(match_type, match_value)
        if normalized is None:
            raise ClipOSError(
                "suppression_value_invalid",
                f"match value {match_value!r} is not valid for {match_type.value}",
                status_code=422,
            )
        existing = await session.scalar(
            select(OpportunitySuppression).where(
                OpportunitySuppression.tenant_id == principal.tenant_id,
                OpportunitySuppression.match_type == match_type,
                OpportunitySuppression.match_value == normalized,
            )
        )
        if existing is not None:
            raise ConflictError("an identical suppression already exists")
        suppression = OpportunitySuppression(
            tenant_id=principal.tenant_id,
            match_type=match_type,
            match_value=normalized,
            reason=reason,
            created_by_user_id=principal.user_id,
        )
        session.add(suppression)
        await session.flush()
        # Creating a suppression immediately hides matching records that
        # are already in the working list.
        applied = 0
        rows = list(
            (
                await session.scalars(
                    select(Opportunity).where(
                        Opportunity.tenant_id == principal.tenant_id,
                        Opportunity.state != OpportunityState.SUPPRESSED,
                    )
                )
            ).all()
        )
        for row in rows:
            if (
                _suppression_match(
                    [suppression],
                    creator_handle=row.creator_handle,
                    canonical_url=row.canonical_url,
                    title=row.title,
                    creator_name=row.creator_name,
                    niche=row.niche,
                )
                is not None
            ):
                row.state = OpportunityState.SUPPRESSED
                applied += 1
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="opportunity.suppression_created",
            resource_type="opportunity_suppression",
            resource_id=str(suppression.id),
            event_data={
                "match_type": match_type.value,
                "match_value": normalized,
                "opportunities_suppressed": applied,
            },
        )
        await session.commit()
        await session.refresh(suppression)
        return suppression

    @staticmethod
    async def list_suppressions(
        session: AsyncSession, tenant_id: UUID, *, limit: int = 100
    ) -> list[OpportunitySuppression]:
        return list(
            (
                await session.scalars(
                    select(OpportunitySuppression)
                    .where(OpportunitySuppression.tenant_id == tenant_id)
                    .order_by(desc(OpportunitySuppression.created_at))
                    .limit(min(limit, 200))
                )
            ).all()
        )

    @staticmethod
    async def delete_suppression(
        session: AsyncSession, *, principal: Principal, suppression_id: UUID
    ) -> None:
        suppression = await session.scalar(
            select(OpportunitySuppression).where(
                OpportunitySuppression.id == suppression_id,
                OpportunitySuppression.tenant_id == principal.tenant_id,
            )
        )
        if suppression is None:
            raise NotFoundError("opportunity suppression")
        await session.delete(suppression)
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="opportunity.suppression_deleted",
            resource_type="opportunity_suppression",
            resource_id=str(suppression_id),
            event_data={
                "match_type": suppression.match_type.value,
                "match_value": suppression.match_value,
            },
        )
        await session.commit()

    @staticmethod
    async def _tenant_suppressions(
        session: AsyncSession, tenant_id: UUID
    ) -> list[OpportunitySuppression]:
        return list(
            (
                await session.scalars(
                    select(OpportunitySuppression).where(
                        OpportunitySuppression.tenant_id == tenant_id
                    )
                )
            ).all()
        )

    # ------------------------------------------------------------------
    # Phase 9 handoff packages (draft only — nothing is sent)
    # ------------------------------------------------------------------
    @staticmethod
    async def build_handoff(
        session: AsyncSession, *, principal: Principal, opportunity_id: UUID
    ) -> OpportunityHandoff:
        opportunity = await OpportunityService.get_opportunity(
            session, principal.tenant_id, opportunity_id
        )
        if opportunity.state == OpportunityState.SUPPRESSED:
            raise ConflictError("a suppressed opportunity cannot be packaged for Phase 9")
        existing = await session.scalar(
            select(OpportunityHandoff).where(
                OpportunityHandoff.opportunity_id == opportunity.id,
                OpportunityHandoff.tenant_id == principal.tenant_id,
            )
        )
        if existing is not None and existing.state == OpportunityHandoffState.READY:
            raise ConflictError(
                "this opportunity already has a ready handoff; it is not silently rewritten"
            )
        payload = _handoff_payload(opportunity)
        if existing is None:
            existing = OpportunityHandoff(
                tenant_id=principal.tenant_id,
                opportunity_id=opportunity.id,
                payload=payload,
                payload_version=OPPORTUNITY_HANDOFF_VERSION,
                state=OpportunityHandoffState.DRAFT,
                created_by_user_id=principal.user_id,
            )
            session.add(existing)
        else:
            existing.payload = payload
            existing.payload_version = OPPORTUNITY_HANDOFF_VERSION
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="opportunity.handoff_created",
            resource_type="opportunity_handoff",
            resource_id=str(existing.id),
            event_data={
                "opportunity_id": str(opportunity.id),
                "payload_version": OPPORTUNITY_HANDOFF_VERSION,
                "outreach_status": "not_sent",
            },
        )
        await session.commit()
        await session.refresh(existing)
        return existing

    @staticmethod
    async def get_handoff(
        session: AsyncSession, tenant_id: UUID, handoff_id: UUID
    ) -> OpportunityHandoff:
        handoff = await session.scalar(
            select(OpportunityHandoff).where(
                OpportunityHandoff.id == handoff_id,
                OpportunityHandoff.tenant_id == tenant_id,
            )
        )
        if handoff is None:
            raise NotFoundError("opportunity handoff")
        return handoff

    @staticmethod
    async def get_handoff_for_opportunity(
        session: AsyncSession, tenant_id: UUID, opportunity_id: UUID
    ) -> OpportunityHandoff:
        await OpportunityService.get_opportunity(session, tenant_id, opportunity_id)
        handoff = await session.scalar(
            select(OpportunityHandoff).where(
                OpportunityHandoff.opportunity_id == opportunity_id,
                OpportunityHandoff.tenant_id == tenant_id,
            )
        )
        if handoff is None:
            raise NotFoundError("opportunity handoff")
        return handoff

    @staticmethod
    async def transition_handoff(
        session: AsyncSession,
        *,
        principal: Principal,
        handoff_id: UUID,
        target_state: OpportunityHandoffState,
    ) -> OpportunityHandoff:
        handoff = await OpportunityService.get_handoff(session, principal.tenant_id, handoff_id)
        source_state = handoff.state
        if target_state not in _HANDOFF_TRANSITIONS[source_state]:
            raise ConflictError(
                f"handoff cannot transition from {source_state.value} to {target_state.value}"
            )
        handoff.state = target_state
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="opportunity.handoff_transitioned",
            resource_type="opportunity_handoff",
            resource_id=str(handoff.id),
            event_data={"from": source_state.value, "to": target_state.value},
        )
        await session.commit()
        await session.refresh(handoff)
        return handoff


def _validate_source_config(kind: OpportunitySourceKind, config: dict[str, Any]) -> None:
    if kind == OpportunitySourceKind.RSS_FEED:
        feed_url = config.get("feed_url")
        if not isinstance(feed_url, str) or not feed_url.strip():
            raise ClipOSError(
                "rss_feed_url_missing",
                "an rss_feed source requires config.feed_url",
                status_code=422,
            )
        if normalize_url(feed_url.strip()) is None:
            raise ClipOSError(
                "rss_feed_url_invalid",
                "config.feed_url must be an absolute http(s) URL",
                status_code=422,
            )


def _evidence_for(record: SourcedOpportunity) -> dict[str, Any]:
    return {
        "provenance": {
            name: provenance.as_dict()
            for name, provenance in sorted(record.field_provenance.items())
        },
        "signals": {
            "sample_item_count": record.sample_item_count,
            "recent_views_band": record.recent_views_band,
            "third_party_compilation": record.third_party_compilation,
            "library_episode_count": record.library_episode_count,
            "library_long_form_hours": record.library_long_form_hours,
            "existing_shorts_count": record.existing_shorts_count,
            "moments_per_episode": record.moments_per_episode,
        },
        "source_record": record.raw,
    }


def _apply_record(
    opportunity: Opportunity,
    record: SourcedOpportunity,
    *,
    creator_handle: str | None,
    canonical_url: str | None,
    dedup_key: str,
) -> None:
    """Refresh an exactly-matched opportunity from the newest record.

    Newly observed values win; a field the new record does not carry
    keeps its stored value instead of being erased by an absence.
    """
    opportunity.title = record.title
    if record.creator_name is not None:
        opportunity.creator_name = record.creator_name
    if creator_handle is not None:
        opportunity.creator_handle = creator_handle
    if canonical_url is not None:
        opportunity.canonical_url = canonical_url
    if record.platform is not None:
        opportunity.platform = record.platform
    if record.contact_route_label is not None:
        opportunity.contact_route_label = record.contact_route_label
    if record.niche is not None:
        opportunity.niche = record.niche
    if record.language is not None:
        opportunity.language = record.language
    if record.audience_band is not None:
        opportunity.audience_band = record.audience_band
    if record.posting_cadence is not None:
        opportunity.posting_cadence = record.posting_cadence
    if record.library_episode_count is not None:
        opportunity.library_episode_count = record.library_episode_count
    if record.library_long_form_hours is not None:
        opportunity.library_long_form_hours = record.library_long_form_hours
    if record.existing_shorts_count is not None:
        opportunity.existing_shorts_count = record.existing_shorts_count
    if record.moments_per_episode is not None:
        opportunity.moments_per_episode_assumption = record.moments_per_episode
    opportunity.dedup_key = dedup_key
    opportunity.evidence = _evidence_for(record)


def _signals_for_row(opportunity: Opportunity) -> OpportunitySignals:
    signals = opportunity.evidence.get("signals", {}) if opportunity.evidence else {}
    sample = signals.get("sample_item_count", 0)
    return OpportunitySignals(
        platform=opportunity.platform,
        niche=opportunity.niche,
        language=opportunity.language,
        audience_band=opportunity.audience_band,
        posting_cadence=opportunity.posting_cadence,
        contact_route_label=opportunity.contact_route_label,
        sample_item_count=sample if isinstance(sample, int) else 0,
        recent_views_band=signals.get("recent_views_band"),
        third_party_compilation=bool(signals.get("third_party_compilation", False)),
    )


def content_debt_for_row(opportunity: Opportunity) -> ContentDebt:
    """Compute the v2.1 content-debt score for one stored opportunity.

    Deterministic and read-only: the score is derived entirely from the
    recorded intake columns and the persisted commercial score
    components, so it can be recomputed on every read and always
    reflects the newest recorded facts. The opportunity's
    ``score_components`` relationship must be loaded by the caller.
    """
    commercial: dict[str, float | None] = {
        component.key: component.score
        for component in opportunity.score_components
        if component.key in {"audience_fit", "performance_fit"}
    }
    signals = ContentDebtSignals(
        library_episode_count=opportunity.library_episode_count,
        library_long_form_hours=opportunity.library_long_form_hours,
        existing_shorts_count=opportunity.existing_shorts_count,
        moments_per_episode=(
            opportunity.moments_per_episode_assumption or DEFAULT_MOMENTS_PER_EPISODE
        ),
        audience_band=opportunity.audience_band,
        posting_cadence=opportunity.posting_cadence,
        contact_route_label=opportunity.contact_route_label,
        niche=opportunity.niche,
        commercial_component_scores=commercial,
    )
    return score_content_debt(signals)


def _similarity_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _text_similarity(left: str, right: str) -> float:
    tokens_left = set(left.split())
    tokens_right = set(right.split())
    if tokens_left and tokens_left == tokens_right:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _normalize_suppression_value(match_type: SuppressionMatchType, value: str) -> str | None:
    if match_type == SuppressionMatchType.CREATOR_HANDLE:
        return normalize_handle(value)
    if match_type == SuppressionMatchType.DOMAIN:
        cleaned = value.strip().casefold()
        for prefix in ("https://", "http://"):
            cleaned = cleaned.removeprefix(prefix)
        host = cleaned.split("/")[0].split(":")[0].strip()
        return host or None
    cleaned_keyword = " ".join(value.casefold().split())
    return cleaned_keyword or None


def _suppression_match(
    suppressions: list[OpportunitySuppression],
    *,
    creator_handle: str | None,
    canonical_url: str | None,
    title: str,
    creator_name: str | None,
    niche: str | None,
) -> OpportunitySuppression | None:
    host = url_host(canonical_url)
    haystack = " ".join(part for part in (title, creator_name, niche) if part).casefold()
    for suppression in suppressions:
        if suppression.match_type == SuppressionMatchType.CREATOR_HANDLE:
            if creator_handle is not None and creator_handle == suppression.match_value:
                return suppression
        elif suppression.match_type == SuppressionMatchType.DOMAIN:
            if host is not None and (
                host == suppression.match_value or host.endswith("." + suppression.match_value)
            ):
                return suppression
        elif suppression.match_type == SuppressionMatchType.KEYWORD:
            if suppression.match_value in haystack:
                return suppression
    return None


def _handoff_payload(opportunity: Opportunity) -> dict[str, Any]:
    return {
        "schema": OPPORTUNITY_HANDOFF_VERSION,
        "tenant_id": str(opportunity.tenant_id),
        "opportunity_id": str(opportunity.id),
        "prepared_at": utcnow().isoformat(),
        "prepared_by": "clipos-phase-8",
        "phase_9_status": "not_implemented",
        "outreach_status": "not_sent",
        "opportunity": {
            "external_key": opportunity.external_key,
            "title": opportunity.title,
            "creator_name": opportunity.creator_name,
            "creator_handle": opportunity.creator_handle,
            "canonical_url": opportunity.canonical_url,
            "platform": opportunity.platform,
            "contact_route_label": opportunity.contact_route_label,
            "niche": opportunity.niche,
            "language": opportunity.language,
            "audience_band": opportunity.audience_band,
            "posting_cadence": opportunity.posting_cadence,
            "state": opportunity.state.value,
        },
        "score": {
            "overall": opportunity.overall_score,
            "version": opportunity.score_version,
            "components": [
                {
                    "key": component.key,
                    "label": component.label,
                    "score": component.score,
                    "weight": component.weight,
                    "weighted_score": component.weighted_score,
                    "applicable": component.applicable,
                    "evidence": component.evidence,
                    "scoring_version": component.scoring_version,
                }
                for component in sorted(opportunity.score_components, key=lambda item: item.key)
            ],
        },
        "evidence": opportunity.evidence,
    }


def job_state_for_sync(state: OpportunitySyncState) -> JobState:
    return {
        OpportunitySyncState.SUCCEEDED: JobState.SUCCEEDED,
        OpportunitySyncState.PARTIALLY_SUCCEEDED: JobState.SUCCEEDED,
        OpportunitySyncState.FAILED: JobState.FAILED,
        OpportunitySyncState.RUNNING: JobState.RUNNING,
        OpportunitySyncState.QUEUED: JobState.QUEUED,
    }[state]
