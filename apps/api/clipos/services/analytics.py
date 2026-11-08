from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import mean
from typing import TypedDict, cast
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from clipos.core.enums import (
    AnalyticsCollectionState,
    AuditOutcome,
    ExperimentState,
    JobState,
    MetricAvailability,
    PublishingProvider,
    PublishState,
    RecommendationKind,
    RecommendationState,
)
from clipos.core.errors import ClipOSError, ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.gateways.analytics import (
    ADDITIVE_METRICS,
    CANONICAL_METRIC_NAMES,
    AnalyticsAdapterRegistry,
    AnalyticsConnectionContext,
    MetricResult,
    MetricWindow,
)
from clipos.gateways.analytics.errors import AnalyticsAdapterError
from clipos.models import (
    AnalyticsCollectionRun,
    AnalyticsMetricSnapshot,
    BackgroundJob,
    CandidateScoreComponent,
    ClipCandidate,
    Experiment,
    ExperimentVariant,
    Publication,
    PublishingConnection,
    Recommendation,
    RenderJob,
    RenderPlan,
    UsageCostEvent,
)
from clipos.models.entities import utcnow
from clipos.services.audit import AuditService
from clipos.services.jobs import JobService

ANALYTICS_JOB_KIND = "analytics.collect"
LEARNING_LOOP_ALGORITHM_VERSION = "learning-loop.v1"
RECOMMENDATION_GENERATOR_ID = "system:learning-loop"

# Recommendation rules v1 are deliberately conservative: a rule fires only
# with at least this many publications per compared group and at least this
# relative difference in mean views. Anything weaker stays unreported
# rather than becoming a low-evidence suggestion.
_MIN_GROUP_SAMPLE = 3
_MIN_OUTPERFORMANCE_RATIO = 1.2
_SHORT_DURATION_MS = 40_000

_EXPERIMENT_TRANSITIONS: dict[ExperimentState, frozenset[ExperimentState]] = {
    ExperimentState.DRAFT: frozenset({ExperimentState.RUNNING, ExperimentState.CANCELLED}),
    ExperimentState.RUNNING: frozenset(
        {ExperimentState.PAUSED, ExperimentState.COMPLETED, ExperimentState.CANCELLED}
    ),
    ExperimentState.PAUSED: frozenset(
        {ExperimentState.RUNNING, ExperimentState.COMPLETED, ExperimentState.CANCELLED}
    ),
    ExperimentState.COMPLETED: frozenset(),
    ExperimentState.CANCELLED: frozenset(),
}


class PublicationPerformanceResult(TypedDict):
    publication: Publication
    metrics: list[AnalyticsMetricSnapshot]
    availability_counts: dict[str, int]


class PublicationPerformanceSummary(TypedDict):
    publication: Publication
    metrics: list[AnalyticsMetricSnapshot]
    views: float | None
    views_availability: MetricAvailability | None


class CampaignMetricResult(TypedDict):
    metric: str
    unit: str
    aggregation: str
    value: float | None
    reported_publications: int
    unavailable_publications: int


class CampaignSummaryResult(TypedDict):
    campaign_reference: str
    publication_count: int
    publication_ids: list[UUID]
    metrics: list[CampaignMetricResult]


class CostBreakdown(TypedDict):
    provider: str
    model: str
    operation: str
    event_count: int
    succeeded: int
    failed: int
    estimated_cost: Decimal
    currency: str


class CostSummaryResult(TypedDict):
    total_estimated_cost: Decimal
    currency: str
    event_count: int
    succeeded: int
    failed: int
    publication_count: int
    cost_per_publication: Decimal | None
    by_provider_operation: list[CostBreakdown]
    attribution_note: str


class ScoreComponentResult(TypedDict):
    key: str
    score: float | None
    weight: float
    weighted_score: float | None
    applicable: bool
    algorithm_version: str


class PredictedVsActualResult(TypedDict):
    publication_id: UUID
    candidate_id: UUID | None
    predicted_overall_score: float | None
    score_components: list[ScoreComponentResult]
    actual_metrics: list[AnalyticsMetricSnapshot]
    lineage_complete: bool
    gaps: list[str]


@dataclass(frozen=True, slots=True)
class PublicationAnalysis:
    """One publication's reported views joined with its render lineage."""

    publication_id: UUID
    views: float
    duration_ms: int | None
    caption_mode: str | None
    aspect_ratio: str | None
    hook_score: float | None
    published_hour: int


@dataclass(slots=True)
class _MetricBucket:
    unit: str
    values: list[float] = field(default_factory=list)
    view_weights: list[float | None] = field(default_factory=list)
    reported_publications: int = 0
    unavailable_publications: int = 0


@dataclass(frozen=True, slots=True)
class _RecommendationCandidate:
    kind: RecommendationKind
    title: str
    rationale: str
    evidence: dict[str, object]


def _day_floor(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _collection_window(publication: Publication, now: datetime) -> MetricWindow:
    """Day-grained lifetime window for one publication.

    The window is bucketed to whole UTC days so repeated collections on
    the same day hit the snapshot dedup key instead of piling up
    near-identical rows, while each new day produces a fresh snapshot.
    """
    handoff = publication.analytics_handoff or {}
    published = _parse_timestamp(handoff.get("published_at")) or publication.created_at
    start = _day_floor(published)
    end = _day_floor(now)
    if end <= start:
        end = start + timedelta(days=1)
    return MetricWindow(start=start, end=end)


def _evidence_fingerprint(rule: str, publication_ids: list[UUID], *parts: object) -> str:
    material = "|".join(
        [rule, *(str(item) for item in sorted(publication_ids)), *(str(p) for p in parts)]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _group_stats(
    groups: dict[str, list[PublicationAnalysis]],
) -> dict[str, tuple[list[UUID], float]]:
    return {
        name: (
            [member.publication_id for member in members],
            float(mean(member.views for member in members)),
        )
        for name, members in groups.items()
        if members
    }


class AnalyticsService:
    """Phase 7 analytics, experimentation and bounded learning loop.

    Isolation contract: this service writes only to the Phase 7 tables
    (analytics collection runs, metric snapshots, experiments, variants
    and recommendations) plus audit events and its own background jobs.
    It reads publications, render lineage, score components and usage
    costs, but has no write path to rights, quality, gate, approval,
    policy/feature-flag or budget records; recommendations are advisory
    until a human reviews them, and nothing here applies one.
    """

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------
    @staticmethod
    async def create_collection_run(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        requested_by_user_id: UUID | None,
        provider: PublishingProvider | None,
        background_job_id: UUID | None = None,
    ) -> AnalyticsCollectionRun:
        run = AnalyticsCollectionRun(
            tenant_id=tenant_id,
            requested_by_user_id=requested_by_user_id,
            background_job_id=background_job_id,
            provider=provider,
            state=AnalyticsCollectionState.QUEUED,
        )
        session.add(run)
        await session.flush()
        return run

    @staticmethod
    async def queue_collection(
        session: AsyncSession,
        *,
        principal: Principal,
        provider: PublishingProvider | None,
        idempotency_key: str,
    ) -> tuple[AnalyticsCollectionRun, BackgroundJob, bool]:
        """Create a collection run plus its durable background job.

        Mirrors the Phase 6 publishing pattern: the domain endpoint owns
        job creation for its reserved kind, and the generic jobs endpoint
        rejects the kind.
        """
        job, created = await JobService.create(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            kind=ANALYTICS_JOB_KIND,
            idempotency_key=idempotency_key,
            payload={"provider": provider.value if provider else None},
            max_attempts=3,
        )
        run = await session.scalar(
            select(AnalyticsCollectionRun).where(
                AnalyticsCollectionRun.background_job_id == job.id,
                AnalyticsCollectionRun.tenant_id == principal.tenant_id,
            )
        )
        if run is None:
            run = await AnalyticsService.create_collection_run(
                session,
                tenant_id=principal.tenant_id,
                requested_by_user_id=principal.user_id,
                provider=provider,
                background_job_id=job.id,
            )
        return run, job, created

    @staticmethod
    async def collect_metrics_for_tenant(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        requested_by_user_id: UUID | None,
        registry: AnalyticsAdapterRegistry,
        provider: PublishingProvider | None = None,
    ) -> AnalyticsCollectionRun:
        """Synchronous collection: create a run and execute it inline."""
        run = await AnalyticsService.create_collection_run(
            session,
            tenant_id=tenant_id,
            requested_by_user_id=requested_by_user_id,
            provider=provider,
        )
        await session.commit()
        return await AnalyticsService.execute_collection(session, run=run, registry=registry)

    @staticmethod
    async def execute_collection(
        session: AsyncSession,
        *,
        run: AnalyticsCollectionRun,
        registry: AnalyticsAdapterRegistry,
    ) -> AnalyticsCollectionRun:
        now = utcnow()
        run.state = AnalyticsCollectionState.RUNNING
        run.started_at = now
        await session.commit()

        statement = select(Publication).where(
            Publication.tenant_id == run.tenant_id,
            Publication.state == PublishState.SUCCEEDED,
            Publication.analytics_handoff.is_not(None),
            Publication.provider_publication_id.is_not(None),
        )
        if run.provider is not None:
            statement = statement.where(Publication.provider == run.provider)
        publications = list(
            (await session.scalars(statement.order_by(Publication.created_at))).all()
        )
        run.publications_considered = len(publications)
        if publications:
            run.adapter_mode = registry.get(publications[0].provider).mode

        failures = 0
        for publication in publications:
            adapter = registry.get(publication.provider)
            connection = await AnalyticsService._connection_context(session, publication)
            window = _collection_window(publication, utcnow())
            try:
                results = await adapter.fetch_publication_metrics(
                    connection=connection,
                    provider_publication_id=str(publication.provider_publication_id),
                    window=window,
                )
            except AnalyticsAdapterError as exc:
                failures += 1
                if run.failure_category is None:
                    run.failure_category = exc.category[:100]
                    run.failure_message = exc.safe_message[:500]
                continue
            for result in results:
                await AnalyticsService._persist_snapshot(
                    session,
                    run=run,
                    publication=publication,
                    source=adapter.source,
                    window=window,
                    result=result,
                )
                if result.availability in {
                    MetricAvailability.REPORTED,
                    MetricAvailability.STALE,
                }:
                    run.metrics_reported += 1
                else:
                    run.metrics_unavailable += 1

        if publications and failures == len(publications):
            run.state = AnalyticsCollectionState.FAILED
        elif failures:
            run.state = AnalyticsCollectionState.PARTIALLY_SUCCEEDED
        else:
            run.state = AnalyticsCollectionState.SUCCEEDED
        run.finished_at = utcnow()
        AuditService.record(
            session,
            tenant_id=run.tenant_id,
            actor_user_id=run.requested_by_user_id,
            action="analytics.collection_completed",
            resource_type="analytics_collection_run",
            resource_id=str(run.id),
            outcome=(
                AuditOutcome.FAILURE
                if run.state == AnalyticsCollectionState.FAILED
                else AuditOutcome.SUCCESS
            ),
            event_data={
                "state": run.state.value,
                "adapter_mode": run.adapter_mode.value,
                "publications_considered": run.publications_considered,
                "snapshots_written": run.snapshots_written,
                "metrics_reported": run.metrics_reported,
                "metrics_unavailable": run.metrics_unavailable,
                "failure_category": run.failure_category,
            },
        )
        await session.commit()
        await session.refresh(run)
        return run

    @staticmethod
    async def _connection_context(
        session: AsyncSession, publication: Publication
    ) -> AnalyticsConnectionContext:
        connection = await session.get(PublishingConnection, publication.connection_id)
        scopes: frozenset[str] = frozenset()
        destination = publication.provider_destination_id
        connection_id: UUID | None = publication.connection_id
        if connection is not None and connection.tenant_id == publication.tenant_id:
            raw_scopes = connection.granted_scopes
            if isinstance(raw_scopes, list):
                scopes = frozenset(str(item) for item in raw_scopes)
            destination = connection.provider_destination_id
        else:
            connection_id = None
        return AnalyticsConnectionContext(
            provider=publication.provider,
            connection_id=connection_id,
            provider_destination_id=destination,
            granted_scopes=scopes,
        )

    @staticmethod
    async def _persist_snapshot(
        session: AsyncSession,
        *,
        run: AnalyticsCollectionRun,
        publication: Publication,
        source: str,
        window: MetricWindow,
        result: MetricResult,
    ) -> None:
        existing = await session.scalar(
            select(AnalyticsMetricSnapshot).where(
                AnalyticsMetricSnapshot.publication_id == publication.id,
                AnalyticsMetricSnapshot.metric == result.metric,
                AnalyticsMetricSnapshot.window_start == window.start,
                AnalyticsMetricSnapshot.window_end == window.end,
                AnalyticsMetricSnapshot.source == source,
            )
        )
        if existing is not None:
            # Dedup hit: refresh the stored truth in place instead of
            # writing a second row for the same logical snapshot.
            existing.value = result.value
            existing.availability = result.availability
            existing.unit = result.unit
            existing.metric_definition_version = result.definition_version
            existing.collection_run_id = run.id
            existing.fetched_at = utcnow()
            return
        session.add(
            AnalyticsMetricSnapshot(
                tenant_id=run.tenant_id,
                publication_id=publication.id,
                collection_run_id=run.id,
                provider=publication.provider,
                provider_publication_id=str(publication.provider_publication_id),
                metric=result.metric,
                value=result.value,
                unit=result.unit,
                availability=result.availability,
                window_start=window.start,
                window_end=window.end,
                source=source,
                metric_definition_version=result.definition_version,
                fetched_at=utcnow(),
            )
        )
        run.snapshots_written += 1
        await session.flush()

    @staticmethod
    async def get_collection_run(
        session: AsyncSession, tenant_id: UUID, run_id: UUID
    ) -> AnalyticsCollectionRun:
        run = await session.scalar(
            select(AnalyticsCollectionRun).where(
                AnalyticsCollectionRun.id == run_id,
                AnalyticsCollectionRun.tenant_id == tenant_id,
            )
        )
        if run is None:
            raise NotFoundError("analytics collection run not found")
        return run

    @staticmethod
    async def list_collection_runs(
        session: AsyncSession, tenant_id: UUID, *, limit: int = 50
    ) -> list[AnalyticsCollectionRun]:
        return list(
            (
                await session.scalars(
                    select(AnalyticsCollectionRun)
                    .where(AnalyticsCollectionRun.tenant_id == tenant_id)
                    .order_by(AnalyticsCollectionRun.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    # ------------------------------------------------------------------
    # Performance queries
    # ------------------------------------------------------------------
    @staticmethod
    async def _get_publication(
        session: AsyncSession, tenant_id: UUID, publication_id: UUID
    ) -> Publication:
        publication = await session.scalar(
            select(Publication).where(
                Publication.id == publication_id, Publication.tenant_id == tenant_id
            )
        )
        if publication is None:
            raise NotFoundError("publication not found")
        return publication

    @staticmethod
    async def _latest_snapshots(
        session: AsyncSession, tenant_id: UUID, publication_id: UUID
    ) -> dict[str, AnalyticsMetricSnapshot]:
        rows = (
            await session.scalars(
                select(AnalyticsMetricSnapshot)
                .where(
                    AnalyticsMetricSnapshot.tenant_id == tenant_id,
                    AnalyticsMetricSnapshot.publication_id == publication_id,
                )
                .order_by(AnalyticsMetricSnapshot.fetched_at.desc())
            )
        ).all()
        latest: dict[str, AnalyticsMetricSnapshot] = {}
        for row in rows:
            latest.setdefault(row.metric, row)
        return latest

    @staticmethod
    async def publication_performance(
        session: AsyncSession, tenant_id: UUID, publication_id: UUID
    ) -> PublicationPerformanceResult:
        publication = await AnalyticsService._get_publication(session, tenant_id, publication_id)
        latest = await AnalyticsService._latest_snapshots(session, tenant_id, publication_id)
        availability_counts: dict[str, int] = {}
        for snapshot in latest.values():
            key = snapshot.availability.value
            availability_counts[key] = availability_counts.get(key, 0) + 1
        return {
            "publication": publication,
            "metrics": [latest[name] for name in sorted(latest)],
            "availability_counts": availability_counts,
        }

    @staticmethod
    async def list_performance(
        session: AsyncSession, tenant_id: UUID, *, limit: int = 50
    ) -> list[PublicationPerformanceSummary]:
        publications = list(
            (
                await session.scalars(
                    select(Publication)
                    .where(
                        Publication.tenant_id == tenant_id,
                        Publication.state == PublishState.SUCCEEDED,
                    )
                    .order_by(Publication.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )
        results: list[PublicationPerformanceSummary] = []
        for publication in publications:
            latest = await AnalyticsService._latest_snapshots(session, tenant_id, publication.id)
            views = latest.get("views")
            results.append(
                {
                    "publication": publication,
                    "metrics": [latest[name] for name in sorted(latest)],
                    "views": views.value if views is not None else None,
                    "views_availability": views.availability if views is not None else None,
                }
            )
        return results

    @staticmethod
    async def campaign_summary(
        session: AsyncSession, tenant_id: UUID, campaign_reference: str
    ) -> CampaignSummaryResult:
        publications = list(
            (
                await session.scalars(
                    select(Publication)
                    .where(
                        Publication.tenant_id == tenant_id,
                        Publication.campaign_reference == campaign_reference,
                        Publication.state == PublishState.SUCCEEDED,
                    )
                    .order_by(Publication.created_at)
                )
            ).all()
        )
        per_metric: dict[str, _MetricBucket] = {}
        for publication in publications:
            latest = await AnalyticsService._latest_snapshots(session, tenant_id, publication.id)
            views_snapshot = latest.get("views")
            views_value: float | None = None
            if (
                views_snapshot is not None
                and views_snapshot.value is not None
                and views_snapshot.availability
                in {MetricAvailability.REPORTED, MetricAvailability.STALE}
            ):
                views_value = views_snapshot.value
            for metric_name, snapshot in latest.items():
                bucket = per_metric.setdefault(metric_name, _MetricBucket(unit=snapshot.unit))
                if (
                    snapshot.availability in {MetricAvailability.REPORTED, MetricAvailability.STALE}
                    and snapshot.value is not None
                ):
                    bucket.values.append(snapshot.value)
                    bucket.view_weights.append(views_value)
                    bucket.reported_publications += 1
                else:
                    bucket.unavailable_publications += 1
        metrics: list[CampaignMetricResult] = []
        for metric_name in sorted(per_metric):
            bucket = per_metric[metric_name]
            value: float | None = None
            if bucket.values:
                if metric_name in ADDITIVE_METRICS:
                    value = float(sum(bucket.values))
                    aggregation = "sum"
                elif metric_name == "average_view_duration_seconds" and any(
                    weight for weight in bucket.view_weights
                ):
                    weighted_total = sum(
                        item * (weight or 0.0)
                        for item, weight in zip(bucket.values, bucket.view_weights, strict=True)
                    )
                    weight_total = sum(weight or 0.0 for weight in bucket.view_weights)
                    value = (
                        weighted_total / weight_total
                        if weight_total
                        else float(mean(bucket.values))
                    )
                    aggregation = "weighted_average_by_views"
                else:
                    value = float(mean(bucket.values))
                    aggregation = "mean"
            else:
                aggregation = "sum" if metric_name in ADDITIVE_METRICS else "mean"
            metrics.append(
                {
                    "metric": metric_name,
                    "unit": bucket.unit,
                    "aggregation": aggregation,
                    "value": value,
                    "reported_publications": bucket.reported_publications,
                    "unavailable_publications": bucket.unavailable_publications,
                }
            )
        return {
            "campaign_reference": campaign_reference,
            "publication_count": len(publications),
            "publication_ids": [publication.id for publication in publications],
            "metrics": metrics,
        }

    @staticmethod
    async def cost_summary(session: AsyncSession, tenant_id: UUID) -> CostSummaryResult:
        rows = (
            await session.execute(
                select(
                    UsageCostEvent.provider,
                    UsageCostEvent.model,
                    UsageCostEvent.operation,
                    func.count(UsageCostEvent.id),
                    func.coalesce(func.sum(UsageCostEvent.estimated_cost), Decimal("0")),
                    func.coalesce(func.sum(case((UsageCostEvent.success, 1), else_=0)), 0),
                )
                .where(UsageCostEvent.tenant_id == tenant_id)
                .group_by(UsageCostEvent.provider, UsageCostEvent.model, UsageCostEvent.operation)
                .order_by(UsageCostEvent.provider, UsageCostEvent.model, UsageCostEvent.operation)
            )
        ).all()
        by_provider: list[CostBreakdown] = []
        total_cost = Decimal("0")
        total_events = 0
        total_succeeded = 0
        for provider, model, operation, count, cost, succeeded in rows:
            event_count = cast(int, count)
            succeeded_count = cast(int, succeeded or 0)
            estimated = cast(Decimal, cost)
            by_provider.append(
                {
                    "provider": str(provider),
                    "model": str(model),
                    "operation": str(operation),
                    "event_count": event_count,
                    "succeeded": succeeded_count,
                    "failed": event_count - succeeded_count,
                    "estimated_cost": estimated,
                    "currency": "USD",
                }
            )
            total_cost += estimated
            total_events += event_count
            total_succeeded += succeeded_count
        publication_count = int(
            await session.scalar(
                select(func.count(Publication.id)).where(
                    Publication.tenant_id == tenant_id,
                    Publication.state == PublishState.SUCCEEDED,
                )
            )
            or 0
        )
        return {
            "total_estimated_cost": total_cost,
            "currency": "USD",
            "event_count": total_events,
            "succeeded": total_succeeded,
            "failed": total_events - total_succeeded,
            "publication_count": publication_count,
            "cost_per_publication": (total_cost / publication_count if publication_count else None),
            "by_provider_operation": by_provider,
            "attribution_note": (
                "Usage cost events are recorded per provider operation and are not "
                "attributed to individual publications; cost_per_publication is an "
                "indicative tenant-level average only."
            ),
        }

    @staticmethod
    async def _render_plan_for(
        session: AsyncSession, tenant_id: UUID, publication: Publication
    ) -> RenderPlan | None:
        render_job = await session.get(RenderJob, publication.render_job_id)
        if render_job is None or render_job.tenant_id != tenant_id:
            return None
        plan = await session.get(RenderPlan, render_job.render_plan_id)
        if plan is None or plan.tenant_id != tenant_id:
            return None
        return plan

    @staticmethod
    async def predicted_vs_actual(
        session: AsyncSession, tenant_id: UUID, publication_id: UUID
    ) -> PredictedVsActualResult:
        publication = await AnalyticsService._get_publication(session, tenant_id, publication_id)
        gaps: list[str] = []
        plan = await AnalyticsService._render_plan_for(session, tenant_id, publication)
        candidate: ClipCandidate | None = None
        components: list[CandidateScoreComponent] = []
        if plan is None:
            gaps.append("render lineage unavailable for this publication")
        else:
            found = await session.get(ClipCandidate, plan.candidate_id)
            if found is None or found.tenant_id != tenant_id:
                gaps.append("clip candidate unavailable for this publication")
            else:
                candidate = found
                components = list(
                    (
                        await session.scalars(
                            select(CandidateScoreComponent)
                            .where(
                                CandidateScoreComponent.candidate_id == candidate.id,
                                CandidateScoreComponent.tenant_id == tenant_id,
                            )
                            .order_by(CandidateScoreComponent.key)
                        )
                    ).all()
                )
        latest = await AnalyticsService._latest_snapshots(session, tenant_id, publication_id)
        if not latest:
            gaps.append("no analytics snapshots collected for this publication yet")
        return {
            "publication_id": publication.id,
            "candidate_id": candidate.id if candidate is not None else None,
            "predicted_overall_score": candidate.overall_score if candidate is not None else None,
            "score_components": [
                {
                    "key": component.key,
                    "score": component.score,
                    "weight": component.weight,
                    "weighted_score": component.weighted_score,
                    "applicable": component.applicable,
                    "algorithm_version": component.algorithm_version,
                }
                for component in components
            ],
            "actual_metrics": [latest[name] for name in sorted(latest)],
            "lineage_complete": plan is not None and candidate is not None,
            "gaps": gaps,
        }

    # ------------------------------------------------------------------
    # Experiments
    # ------------------------------------------------------------------
    @staticmethod
    async def create_experiment(
        session: AsyncSession,
        *,
        principal: Principal,
        name: str,
        hypothesis: str,
        success_metric: str,
        eligibility: dict[str, object],
        success_criteria: dict[str, object],
        starts_at: datetime | None,
        ends_at: datetime | None,
        notes: str,
    ) -> Experiment:
        if success_metric not in CANONICAL_METRIC_NAMES:
            raise ClipOSError(
                "invalid_success_metric",
                f"success_metric must be one of the canonical analytics metrics: "
                f"{', '.join(sorted(CANONICAL_METRIC_NAMES))}",
                status_code=422,
            )
        if starts_at is not None and ends_at is not None and ends_at <= starts_at:
            raise ClipOSError(
                "invalid_experiment_window",
                "ends_at must be after starts_at",
                status_code=422,
            )
        experiment = Experiment(
            tenant_id=principal.tenant_id,
            created_by_user_id=principal.user_id,
            name=name,
            hypothesis=hypothesis,
            state=ExperimentState.DRAFT,
            eligibility=dict(eligibility),
            success_metric=success_metric,
            success_criteria=dict(success_criteria),
            starts_at=starts_at,
            ends_at=ends_at,
            notes=notes,
        )
        session.add(experiment)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="analytics.experiment_created",
            resource_type="experiment",
            resource_id=str(experiment.id),
            outcome=AuditOutcome.SUCCESS,
            event_data={"success_metric": success_metric},
        )
        await session.commit()
        # Re-fetch with variants eager-loaded so response serialization
        # never triggers a lazy load.
        return await AnalyticsService.get_experiment(session, principal.tenant_id, experiment.id)

    @staticmethod
    async def get_experiment(
        session: AsyncSession, tenant_id: UUID, experiment_id: UUID
    ) -> Experiment:
        experiment = await session.scalar(
            select(Experiment)
            .where(Experiment.id == experiment_id, Experiment.tenant_id == tenant_id)
            .options(selectinload(Experiment.variants))
        )
        if experiment is None:
            raise NotFoundError("experiment not found")
        return experiment

    @staticmethod
    async def list_experiments(
        session: AsyncSession, tenant_id: UUID, *, limit: int = 50
    ) -> list[Experiment]:
        return list(
            (
                await session.scalars(
                    select(Experiment)
                    .where(Experiment.tenant_id == tenant_id)
                    .options(selectinload(Experiment.variants))
                    .order_by(Experiment.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )

    @staticmethod
    async def transition_experiment(
        session: AsyncSession,
        *,
        principal: Principal,
        experiment_id: UUID,
        target_state: ExperimentState,
    ) -> Experiment:
        experiment = await AnalyticsService.get_experiment(
            session, principal.tenant_id, experiment_id
        )
        allowed = _EXPERIMENT_TRANSITIONS[experiment.state]
        if target_state not in allowed:
            raise ConflictError(
                f"experiment cannot move from {experiment.state.value} to "
                f"{target_state.value}; allowed: "
                f"{', '.join(sorted(item.value for item in allowed)) or 'none (terminal state)'}"
            )
        if target_state == ExperimentState.RUNNING and not experiment.variants:
            raise ConflictError("experiment needs at least one variant before it can run")
        source = experiment.state
        experiment.state = target_state
        if target_state == ExperimentState.RUNNING and experiment.starts_at is None:
            experiment.starts_at = utcnow()
        if target_state == ExperimentState.COMPLETED and experiment.ends_at is None:
            experiment.ends_at = utcnow()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="analytics.experiment_transitioned",
            resource_type="experiment",
            resource_id=str(experiment.id),
            outcome=AuditOutcome.SUCCESS,
            event_data={"from_state": source.value, "to_state": target_state.value},
        )
        await session.commit()
        # Re-fetch with variants eager-loaded: refresh() would expire the
        # collection and response serialization cannot lazy-load it.
        return await AnalyticsService.get_experiment(session, principal.tenant_id, experiment.id)

    @staticmethod
    async def add_variant(
        session: AsyncSession,
        *,
        principal: Principal,
        experiment_id: UUID,
        name: str,
        description: str,
        candidate_id: UUID | None,
        publication_id: UUID | None,
        config: dict[str, object],
    ) -> ExperimentVariant:
        experiment = await AnalyticsService.get_experiment(
            session, principal.tenant_id, experiment_id
        )
        if experiment.state in {ExperimentState.COMPLETED, ExperimentState.CANCELLED}:
            raise ConflictError("variants cannot be added to a terminal experiment")
        if candidate_id is None and publication_id is None:
            raise ClipOSError(
                "variant_link_required",
                "a variant must link to a clip candidate or a publication",
                status_code=422,
            )
        if candidate_id is not None:
            candidate = await session.scalar(
                select(ClipCandidate).where(
                    ClipCandidate.id == candidate_id,
                    ClipCandidate.tenant_id == principal.tenant_id,
                )
            )
            if candidate is None:
                raise NotFoundError("clip candidate not found")
        if publication_id is not None:
            await AnalyticsService._get_publication(session, principal.tenant_id, publication_id)
        duplicate = await session.scalar(
            select(ExperimentVariant).where(
                ExperimentVariant.experiment_id == experiment.id,
                ExperimentVariant.name == name,
            )
        )
        if duplicate is not None:
            raise ConflictError("an experiment variant with this name already exists")
        variant = ExperimentVariant(
            tenant_id=principal.tenant_id,
            experiment_id=experiment.id,
            name=name,
            description=description,
            candidate_id=candidate_id,
            publication_id=publication_id,
            config=dict(config),
        )
        session.add(variant)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="analytics.experiment_variant_added",
            resource_type="experiment",
            resource_id=str(experiment.id),
            outcome=AuditOutcome.SUCCESS,
            event_data={"variant_name": name},
        )
        await session.commit()
        await session.refresh(variant)
        return variant

    # ------------------------------------------------------------------
    # Recommendations (bounded learning loop)
    # ------------------------------------------------------------------
    @staticmethod
    async def _analysis_rows(session: AsyncSession, tenant_id: UUID) -> list[PublicationAnalysis]:
        publications = list(
            (
                await session.scalars(
                    select(Publication)
                    .where(
                        Publication.tenant_id == tenant_id,
                        Publication.state == PublishState.SUCCEEDED,
                        Publication.analytics_handoff.is_not(None),
                    )
                    .order_by(Publication.created_at)
                )
            ).all()
        )
        rows: list[PublicationAnalysis] = []
        for publication in publications:
            latest = await AnalyticsService._latest_snapshots(session, tenant_id, publication.id)
            views_snapshot = latest.get("views")
            if (
                views_snapshot is None
                or views_snapshot.value is None
                or views_snapshot.availability
                not in {MetricAvailability.REPORTED, MetricAvailability.STALE}
            ):
                continue
            plan = await AnalyticsService._render_plan_for(session, tenant_id, publication)
            hook_score: float | None = None
            if plan is not None:
                component = await session.scalar(
                    select(CandidateScoreComponent).where(
                        CandidateScoreComponent.candidate_id == plan.candidate_id,
                        CandidateScoreComponent.tenant_id == tenant_id,
                        CandidateScoreComponent.key == "hook_clarity",
                    )
                )
                if component is not None and component.applicable:
                    hook_score = component.score
            handoff = publication.analytics_handoff or {}
            published = _parse_timestamp(handoff.get("published_at")) or publication.created_at
            rows.append(
                PublicationAnalysis(
                    publication_id=publication.id,
                    views=float(views_snapshot.value),
                    duration_ms=plan.duration_ms if plan is not None else None,
                    caption_mode=plan.caption_mode if plan is not None else None,
                    aspect_ratio=plan.aspect_ratio if plan is not None else None,
                    hook_score=hook_score,
                    published_hour=published.astimezone(UTC).hour,
                )
            )
        return rows

    @staticmethod
    def _comparison_candidate(
        *,
        kind: RecommendationKind,
        rule: str,
        groups: dict[str, tuple[list[UUID], float]],
        better_label: str,
        title: str,
        rationale_template: str,
    ) -> _RecommendationCandidate | None:
        eligible = {
            name: stats for name, stats in groups.items() if len(stats[0]) >= _MIN_GROUP_SAMPLE
        }
        if better_label not in eligible or len(eligible) < 2:
            return None
        better_ids, better_mean = eligible[better_label]
        other_ids: list[UUID] = []
        other_group_means: list[float] = []
        group_stats: dict[str, dict[str, object]] = {}
        for name, (ids, group_mean) in groups.items():
            group_stats[name] = {"publications": len(ids), "mean_views": round(group_mean, 2)}
        for name, (ids, group_mean) in eligible.items():
            if name == better_label:
                continue
            other_ids.extend(ids)
            other_group_means.extend([group_mean] * len(ids))
        if not other_group_means:
            return None
        other_mean = float(mean(other_group_means))
        if other_mean <= 0 or better_mean / other_mean < _MIN_OUTPERFORMANCE_RATIO:
            return None
        all_ids = better_ids + other_ids
        evidence: dict[str, object] = {
            "rule": rule,
            "algorithm_version": LEARNING_LOOP_ALGORITHM_VERSION,
            "metric": "views",
            "group_stats": group_stats,
            "better_group": better_label,
            "better_mean_views": round(better_mean, 2),
            "comparison_mean_views": round(other_mean, 2),
            "ratio": round(better_mean / other_mean, 3),
            "minimum_group_sample": _MIN_GROUP_SAMPLE,
            "minimum_ratio": _MIN_OUTPERFORMANCE_RATIO,
            "publication_ids": [str(item) for item in all_ids],
            "fingerprint": _evidence_fingerprint(
                rule, all_ids, better_label, round(better_mean, 1), round(other_mean, 1)
            ),
        }
        return _RecommendationCandidate(
            kind=kind,
            title=title,
            rationale=rationale_template.format(
                better=better_label,
                better_mean=round(better_mean, 1),
                other_mean=round(other_mean, 1),
                ratio=round(better_mean / other_mean, 2),
                n_better=len(better_ids),
                n_other=len(other_ids),
            ),
            evidence=evidence,
        )

    @staticmethod
    async def generate_recommendations(
        session: AsyncSession,
        *,
        principal: Principal,
    ) -> list[Recommendation]:
        """Generate deterministic, evidence-cited recommendations.

        Rules v1 compare stored, reported views across duration, caption,
        posting-time and format groups, and check whether predicted hook
        clarity is inverted against actual views. Every generated row is
        PROPOSED: generation never approves, applies or mutates anything
        outside the recommendations table.
        """
        rows = await AnalyticsService._analysis_rows(session, principal.tenant_id)
        candidates: list[_RecommendationCandidate] = []

        duration_groups_map: dict[str, list[PublicationAnalysis]] = {"short": [], "long": []}
        for row in rows:
            if row.duration_ms is None:
                continue
            duration_groups_map[
                "short" if row.duration_ms <= _SHORT_DURATION_MS else "long"
            ].append(row)
        duration_stats = _group_stats(duration_groups_map)
        if len(duration_stats) == 2:
            better = max(duration_stats, key=lambda name: duration_stats[name][1])
            candidate = AnalyticsService._comparison_candidate(
                kind=RecommendationKind.DURATION,
                rule="duration_group_views_v1",
                groups=duration_stats,
                better_label=better,
                title=(
                    "Favour short clips (<=40s) in upcoming plans"
                    if better == "short"
                    else "Favour longer clips (>40s) in upcoming plans"
                ),
                rationale_template=(
                    "Publications in the {better} duration group averaged {better_mean} "
                    "views across {n_better} publications versus {other_mean} across "
                    "{n_other} in the other group (ratio {ratio}). Consider biasing "
                    "future render plans toward the {better} group and re-measuring."
                ),
            )
            if candidate is not None:
                candidates.append(candidate)

        caption_groups_map: dict[str, list[PublicationAnalysis]] = {
            "captioned": [],
            "uncaptioned": [],
        }
        for row in rows:
            if row.caption_mode is None:
                continue
            caption_groups_map[
                "uncaptioned" if row.caption_mode.lower() in {"none", ""} else "captioned"
            ].append(row)
        candidate = AnalyticsService._comparison_candidate(
            kind=RecommendationKind.CAPTIONS,
            rule="caption_group_views_v1",
            groups=_group_stats(caption_groups_map),
            better_label="captioned",
            title="Keep captions enabled on upcoming clips",
            rationale_template=(
                "Captioned publications averaged {better_mean} views across "
                "{n_better} publications versus {other_mean} across {n_other} "
                "uncaptioned ones (ratio {ratio}). Keep captions on by default "
                "and re-measure after the next batch."
            ),
        )
        if candidate is not None:
            candidates.append(candidate)

        def _time_bucket(hour: int) -> str:
            if 5 <= hour < 12:
                return "morning"
            if 12 <= hour < 18:
                return "afternoon"
            if 18 <= hour < 23:
                return "evening"
            return "night"

        time_groups_map: dict[str, list[PublicationAnalysis]] = {}
        for row in rows:
            time_groups_map.setdefault(_time_bucket(row.published_hour), []).append(row)
        time_stats = _group_stats(time_groups_map)
        if len(time_stats) >= 2:
            better_time = max(time_stats, key=lambda name: time_stats[name][1])
            candidate = AnalyticsService._comparison_candidate(
                kind=RecommendationKind.POSTING_TIME,
                rule="posting_time_group_views_v1",
                groups=time_stats,
                better_label=better_time,
                title=f"Bias posting schedule toward the {better_time} window (UTC)",
                rationale_template=(
                    "Publications posted in the {better} window averaged {better_mean} "
                    "views across {n_better} publications versus {other_mean} across "
                    "{n_other} in other windows (ratio {ratio}). Consider scheduling "
                    "the next batch in the {better} window and re-measuring."
                ),
            )
            if candidate is not None:
                candidates.append(candidate)

        format_groups_map: dict[str, list[PublicationAnalysis]] = {}
        for row in rows:
            if row.aspect_ratio is None:
                continue
            format_groups_map.setdefault(row.aspect_ratio, []).append(row)
        format_stats = _group_stats(format_groups_map)
        if len(format_stats) >= 2:
            better_format = max(format_stats, key=lambda name: format_stats[name][1])
            candidate = AnalyticsService._comparison_candidate(
                kind=RecommendationKind.FORMAT,
                rule="format_group_views_v1",
                groups=format_stats,
                better_label=better_format,
                title=f"Favour the {better_format} format in upcoming plans",
                rationale_template=(
                    "Publications in the {better} format averaged {better_mean} views "
                    "across {n_better} publications versus {other_mean} across "
                    "{n_other} in other formats (ratio {ratio})."
                ),
            )
            if candidate is not None:
                candidates.append(candidate)

        hook_rows = [row for row in rows if row.hook_score is not None]
        if len(hook_rows) >= 2 * _MIN_GROUP_SAMPLE:
            scores = sorted(row.hook_score for row in hook_rows if row.hook_score is not None)
            median = scores[len(scores) // 2]
            high = [row for row in hook_rows if (row.hook_score or 0.0) >= median]
            low = [row for row in hook_rows if (row.hook_score or 0.0) < median]
            if len(high) >= _MIN_GROUP_SAMPLE and len(low) >= _MIN_GROUP_SAMPLE:
                high_mean = float(mean(row.views for row in high))
                low_mean = float(mean(row.views for row in low))
                if high_mean > 0 and low_mean / high_mean >= _MIN_OUTPERFORMANCE_RATIO:
                    ids = [row.publication_id for row in hook_rows]
                    candidates.append(
                        _RecommendationCandidate(
                            kind=RecommendationKind.HOOK,
                            title=("Review hook selection: predicted hook clarity is inverted"),
                            rationale=(
                                f"Candidates with below-median predicted hook clarity "
                                f"averaged {round(low_mean, 1)} views across {len(low)} "
                                f"publications, versus {round(high_mean, 1)} across "
                                f"{len(high)} above-median ones. The discovery hook "
                                "score is not predicting audience response on this "
                                "tenant's data; review hook selection before trusting "
                                "it for ranking."
                            ),
                            evidence={
                                "rule": "hook_clarity_inversion_v1",
                                "algorithm_version": LEARNING_LOOP_ALGORITHM_VERSION,
                                "metric": "views",
                                "group_stats": {
                                    "below_median_hook": {
                                        "publications": len(low),
                                        "mean_views": round(low_mean, 2),
                                    },
                                    "above_median_hook": {
                                        "publications": len(high),
                                        "mean_views": round(high_mean, 2),
                                    },
                                },
                                "median_hook_score": median,
                                "ratio": round(low_mean / high_mean, 3),
                                "minimum_group_sample": _MIN_GROUP_SAMPLE,
                                "minimum_ratio": _MIN_OUTPERFORMANCE_RATIO,
                                "publication_ids": [str(item) for item in ids],
                                "fingerprint": _evidence_fingerprint(
                                    "hook_clarity_inversion_v1",
                                    ids,
                                    round(low_mean, 1),
                                    round(high_mean, 1),
                                ),
                            },
                        )
                    )

        created: list[Recommendation] = []
        existing = list(
            (
                await session.scalars(
                    select(Recommendation).where(
                        Recommendation.tenant_id == principal.tenant_id,
                        Recommendation.state.in_(
                            {RecommendationState.PROPOSED, RecommendationState.APPROVED}
                        ),
                    )
                )
            ).all()
        )
        existing_fingerprints = {
            str(item.evidence.get("fingerprint"))
            for item in existing
            if isinstance(item.evidence, dict)
        }
        for candidate_data in candidates:
            fingerprint = str(candidate_data.evidence.get("fingerprint"))
            if fingerprint in existing_fingerprints:
                continue
            recommendation = Recommendation(
                tenant_id=principal.tenant_id,
                kind=candidate_data.kind,
                state=RecommendationState.PROPOSED,
                title=candidate_data.title,
                rationale=candidate_data.rationale,
                evidence=candidate_data.evidence,
                algorithm_version=LEARNING_LOOP_ALGORITHM_VERSION,
                created_by=RECOMMENDATION_GENERATOR_ID,
            )
            session.add(recommendation)
            created.append(recommendation)
        if created:
            await session.flush()
            AuditService.record(
                session,
                tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id,
                action="analytics.recommendations_generated",
                resource_type="recommendation",
                resource_id=None,
                outcome=AuditOutcome.SUCCESS,
                event_data={
                    "created": len(created),
                    "kinds": sorted({item.kind.value for item in created}),
                    "algorithm_version": LEARNING_LOOP_ALGORITHM_VERSION,
                },
            )
        await session.commit()
        for recommendation in created:
            await session.refresh(recommendation)
        return created

    @staticmethod
    async def list_recommendations(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        state: RecommendationState | None = None,
        limit: int = 100,
    ) -> list[Recommendation]:
        statement = select(Recommendation).where(Recommendation.tenant_id == tenant_id)
        if state is not None:
            statement = statement.where(Recommendation.state == state)
        return list(
            (
                await session.scalars(
                    statement.order_by(Recommendation.created_at.desc()).limit(limit)
                )
            ).all()
        )

    @staticmethod
    async def review_recommendation(
        session: AsyncSession,
        *,
        principal: Principal,
        recommendation_id: UUID,
        approve: bool,
        note: str | None,
    ) -> Recommendation:
        recommendation = await session.scalar(
            select(Recommendation).where(
                Recommendation.id == recommendation_id,
                Recommendation.tenant_id == principal.tenant_id,
            )
        )
        if recommendation is None:
            raise NotFoundError("recommendation not found")
        if recommendation.state != RecommendationState.PROPOSED:
            raise ConflictError(
                f"recommendation is {recommendation.state.value}; only proposed "
                "recommendations can be reviewed"
            )
        # Approval records human agreement only. There is deliberately no
        # transition to APPLIED in Phase 7: applying a recommendation
        # would change discovery/render behaviour and needs its own
        # reviewed change process.
        recommendation.state = (
            RecommendationState.APPROVED if approve else RecommendationState.REJECTED
        )
        recommendation.reviewed_by_user_id = principal.user_id
        recommendation.reviewed_at = utcnow()
        recommendation.review_note = note
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="analytics.recommendation_reviewed",
            resource_type="recommendation",
            resource_id=str(recommendation.id),
            outcome=AuditOutcome.SUCCESS,
            event_data={
                "decision": "approved" if approve else "rejected",
                "kind": recommendation.kind.value,
            },
        )
        await session.commit()
        await session.refresh(recommendation)
        return recommendation


def analytics_capabilities(registry: AnalyticsAdapterRegistry) -> list[dict[str, object]]:
    capabilities: list[dict[str, object]] = []
    for provider in PublishingProvider:
        adapter = registry.get(provider)
        capabilities.append(
            {
                "provider": adapter.capabilities.provider.value,
                "mode": adapter.capabilities.mode.value,
                "supported_metrics": list(adapter.capabilities.supported_metrics),
                "limitations": list(adapter.capabilities.limitations),
                "source": adapter.source,
            }
        )
    return capabilities


def job_state_for_run(state: AnalyticsCollectionState) -> JobState:
    if state in {
        AnalyticsCollectionState.SUCCEEDED,
        AnalyticsCollectionState.PARTIALLY_SUCCEEDED,
    }:
        return JobState.SUCCEEDED
    return JobState.FAILED
