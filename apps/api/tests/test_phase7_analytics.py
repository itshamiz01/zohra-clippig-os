from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from clipos.config import Settings
from clipos.core.enums import (
    JobState,
    MetricAvailability,
    PublishingProvider,
)
from clipos.gateways.analytics import (
    AnalyticsAdapterRegistry,
    AnalyticsConnectionContext,
    FakeAnalyticsAdapter,
    MetricResult,
    MetricWindow,
    YouTubeAnalyticsAdapter,
)
from clipos.gateways.analytics.errors import AnalyticsAdapterError
from clipos.models import (
    AnalyticsMetricSnapshot,
    ApprovalRequest,
    AuditEvent,
    BackgroundJob,
    CandidateScoreComponent,
    FeatureFlag,
    GateVerification,
    Publication,
    QualityEvaluation,
    Recommendation,
    RenderJob,
    RenderPlan,
    RightsEvidence,
    UsageCostEvent,
)
from clipos.queue import QueueMessage
from clipos.services.analytics import ANALYTICS_JOB_KIND, AnalyticsService
from tests.test_phase6_publishing import create_ready_release, process_publication

pytestmark = pytest.mark.integration

REMOTE_ID = "video-phase6"


def install_fake_analytics(app: Any, **kwargs: Any) -> AnalyticsAdapterRegistry:
    registry = AnalyticsAdapterRegistry(
        [FakeAnalyticsAdapter(provider, **kwargs) for provider in PublishingProvider]
    )
    app.state.analytics_adapters = registry
    app.state.analytics_processor.registry = registry
    app.state.worker.analytics_processor.registry = registry
    return registry


async def succeeded_publication(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    *,
    key: str,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    (
        client,
        app,
        ids,
        _fake,
        _owner,
        _editor,
        _reviewer,
        _render_ids,
        _evaluation,
        publication,
    ) = await create_ready_release(app_client, auth_headers, media_files, key=key)
    await process_publication(app, publication)
    detail = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}",
        headers=await auth_headers("reviewer"),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["state"] == "succeeded"
    assert detail.json()["analytics_handoff"]["phase_7_status"] == "implemented"
    return client, app, ids, detail.json()


async def snapshot_rows(app: Any, tenant_id: UUID) -> list[AnalyticsMetricSnapshot]:
    async with app.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(AnalyticsMetricSnapshot).where(
                        AnalyticsMetricSnapshot.tenant_id == tenant_id
                    )
                )
            ).all()
        )


async def clone_publication(
    app: Any,
    source_id: UUID,
    *,
    campaign_reference: str | None = None,
    published_hour: int | None = None,
) -> UUID:
    async with app.state.session_factory() as session:
        source = await session.get(Publication, source_id)
        assert source is not None
        job = BackgroundJob(
            tenant_id=source.tenant_id,
            kind="publication.upload",
            idempotency_key=f"clone-{uuid4().hex}",
            state=JobState.SUCCEEDED,
            payload={},
            max_attempts=3,
            attempts=1,
            correlation_id="phase7-test-clone",
        )
        session.add(job)
        await session.flush()
        values = {
            column.name: getattr(source, column.name)
            for column in Publication.__table__.columns
            if column.name not in {"id", "background_job_id", "idempotency_key"}
        }
        values["background_job_id"] = job.id
        values["idempotency_key"] = f"clone-{uuid4().hex}"
        if campaign_reference is not None:
            values["campaign_reference"] = campaign_reference
        if published_hour is not None:
            handoff = dict(source.analytics_handoff or {})
            base = datetime.now(UTC).replace(hour=published_hour, minute=0, second=0, microsecond=0)
            handoff["published_at"] = base.isoformat()
            values["analytics_handoff"] = handoff
        clone = Publication(**values)
        session.add(clone)
        await session.commit()
        return clone.id


async def insert_snapshot(
    app: Any,
    *,
    tenant_id: UUID,
    publication_id: UUID,
    metric: str,
    value: float | None,
    availability: MetricAvailability = MetricAvailability.REPORTED,
    unit: str = "count",
) -> None:
    async with app.state.session_factory() as session:
        session.add(
            AnalyticsMetricSnapshot(
                tenant_id=tenant_id,
                publication_id=publication_id,
                provider=PublishingProvider.YOUTUBE,
                provider_publication_id=REMOTE_ID,
                metric=metric,
                value=value,
                unit=unit,
                availability=availability,
                window_start=datetime(2026, 11, 1, tzinfo=UTC),
                window_end=datetime(2026, 11, 8, tzinfo=UTC),
                source="test_seed",
                metric_definition_version="clipos-metrics.v1",
                fetched_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def test_disabled_adapter_is_truthful_and_never_invents_zeros(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-disabled"
    )
    owner = await auth_headers("owner")
    tenant_id = UUID(publication["tenant_id"])

    capabilities = await client.get("/api/v1/analytics/capabilities", headers=owner)
    assert capabilities.status_code == 200, capabilities.text
    modes = {item["provider"]: item["mode"] for item in capabilities.json()}
    assert modes == {
        "youtube": "disabled",
        "meta": "disabled",
        "tiktok": "disabled",
        "linkedin": "disabled",
    }

    async with app.state.session_factory() as session:
        run = await AnalyticsService.collect_metrics_for_tenant(
            session,
            tenant_id=tenant_id,
            requested_by_user_id=UUID(publication["requested_by_user_id"]),
            registry=app.state.analytics_adapters,
        )
    assert run.state.value == "succeeded"
    assert run.adapter_mode.value == "disabled"
    assert run.publications_considered == 1
    assert run.metrics_reported == 0
    assert run.metrics_unavailable == 9

    rows = await snapshot_rows(app, tenant_id)
    assert len(rows) == 9
    assert {row.availability for row in rows} == {MetricAvailability.UNAVAILABLE}
    assert all(row.value is None for row in rows)
    assert {row.source for row in rows} == {"disabled_adapter"}

    performance = await client.get(
        f"/api/v1/analytics/publications/{publication['id']}/performance", headers=owner
    )
    assert performance.status_code == 200, performance.text
    body = performance.json()
    assert body["availability_counts"] == {"unavailable": 9}
    views = next(item for item in body["metrics"] if item["metric"] == "views")
    assert views["value"] is None
    assert views["availability"] == "unavailable"


async def test_fake_collection_end_to_end_missing_vs_zero_and_lineage(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-fake-e2e"
    )
    install_fake_analytics(app)
    editor = await auth_headers("editor")
    owner = await auth_headers("owner")

    created = await client.post(
        "/api/v1/analytics/collections",
        headers=editor,
        json={"provider": "youtube", "idempotency_key": "phase7-collect-0001"},
    )
    assert created.status_code == 202, created.text
    run = created.json()
    assert run["state"] == "queued"
    assert run["background_job_id"]

    # The analytics job kind is reserved for its domain endpoint.
    generic = await client.post(
        "/api/v1/jobs",
        headers=owner,
        json={
            "kind": ANALYTICS_JOB_KIND,
            "idempotency_key": "phase7-generic-bypass-01",
            "payload": {},
        },
    )
    assert generic.status_code == 422, generic.text
    assert generic.json()["error"]["code"] == "reserved_job_kind"

    # A viewer cannot trigger collection.
    viewer = await auth_headers("viewer")
    forbidden = await client.post(
        "/api/v1/analytics/collections",
        headers=viewer,
        json={"provider": "youtube", "idempotency_key": "phase7-collect-viewer"},
    )
    assert forbidden.status_code == 403, forbidden.text

    await app.state.worker.process(
        QueueMessage(
            job_id=UUID(run["background_job_id"]),
            tenant_id=UUID(run["tenant_id"]),
            kind=ANALYTICS_JOB_KIND,
        )
    )
    finished = await client.get(f"/api/v1/analytics/collection-runs/{run['id']}", headers=owner)
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["state"] == "succeeded"
    assert body["adapter_mode"] == "mocked"
    assert body["publications_considered"] == 1
    assert body["snapshots_written"] == 9
    assert body["metrics_reported"] == 8
    assert body["metrics_unavailable"] == 1

    job = await client.get(f"/api/v1/jobs/{run['background_job_id']}", headers=owner)
    assert job.json()["state"] == "succeeded"
    assert job.json()["result"]["snapshots_written"] == 9

    performance = await client.get(
        f"/api/v1/analytics/publications/{publication['id']}/performance", headers=owner
    )
    assert performance.status_code == 200, performance.text
    metrics = {item["metric"]: item for item in performance.json()["metrics"]}
    assert metrics["views"]["availability"] == "reported"
    assert metrics["views"]["value"] > 0
    assert metrics["views"]["source"] == "fake_analytics_adapter"
    # Missing is not zero: the fake provider contract does not expose
    # conversions, so the value must be null, never an invented 0.
    assert metrics["conversions"]["availability"] == "missing"
    assert metrics["conversions"]["value"] is None

    # The seeded-render fixture bypasses discovery scoring, so persist
    # score components directly to exercise the predicted-vs-actual join.
    async with app.state.session_factory() as session:
        pub = await session.get(Publication, UUID(publication["id"]))
        assert pub is not None
        render_job = await session.get(RenderJob, pub.render_job_id)
        assert render_job is not None
        plan = await session.get(RenderPlan, render_job.render_plan_id)
        assert plan is not None
        for key, score in (("hook_clarity", 0.8), ("narrative_payoff", 0.6)):
            session.add(
                CandidateScoreComponent(
                    tenant_id=pub.tenant_id,
                    candidate_id=plan.candidate_id,
                    key=key,
                    label=key.replace("_", " ").title(),
                    meaning="Seeded for the Phase 7 predicted-vs-actual join test.",
                    score=score,
                    weight=0.1,
                    weighted_score=score * 0.1,
                    applicable=True,
                    evidence={"source": "phase7-test-seed"},
                    missing_behavior="Seeded component; no missing behaviour.",
                    algorithm_version="discovery-scoring.v1",
                )
            )
        await session.commit()

    predicted = await client.get(
        f"/api/v1/analytics/publications/{publication['id']}/predicted-vs-actual",
        headers=owner,
    )
    assert predicted.status_code == 200, predicted.text
    body = predicted.json()
    assert body["lineage_complete"] is True
    assert body["candidate_id"]
    assert body["predicted_overall_score"] is not None
    components = {item["key"]: item for item in body["score_components"]}
    assert components["hook_clarity"]["score"] == 0.8
    assert components["narrative_payoff"]["weighted_score"] == pytest.approx(0.06)
    assert len(body["actual_metrics"]) == 9
    assert body["gaps"] == []


async def test_collection_dedup_refresh_and_unavailable_states(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    _client, app, _ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-dedup"
    )
    tenant_id = UUID(publication["tenant_id"])
    actor = UUID(publication["requested_by_user_id"])

    stale_registry = install_fake_analytics(app, stale_ids=frozenset({REMOTE_ID}))
    async with app.state.session_factory() as session:
        first = await AnalyticsService.collect_metrics_for_tenant(
            session, tenant_id=tenant_id, requested_by_user_id=actor, registry=stale_registry
        )
    assert first.snapshots_written == 9
    rows = await snapshot_rows(app, tenant_id)
    views = next(row for row in rows if row.metric == "views")
    assert views.availability == MetricAvailability.STALE
    assert views.value is not None

    # Re-collecting the same day/window/source dedups in place.
    async with app.state.session_factory() as session:
        second = await AnalyticsService.collect_metrics_for_tenant(
            session, tenant_id=tenant_id, requested_by_user_id=actor, registry=stale_registry
        )
    assert second.snapshots_written == 0
    assert len(await snapshot_rows(app, tenant_id)) == 9

    # A permission change refreshes the same rows truthfully.
    denied_registry = install_fake_analytics(app, permission_denied_ids=frozenset({REMOTE_ID}))
    async with app.state.session_factory() as session:
        third = await AnalyticsService.collect_metrics_for_tenant(
            session, tenant_id=tenant_id, requested_by_user_id=actor, registry=denied_registry
        )
    assert third.metrics_reported == 0
    assert third.metrics_unavailable == 9
    rows = await snapshot_rows(app, tenant_id)
    assert len(rows) == 9
    assert {row.availability for row in rows} == {MetricAvailability.PERMISSION_DENIED}
    assert all(row.value is None for row in rows)


async def test_youtube_analytics_skeleton_is_unverified_and_fails_truthfully(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            database_url="sqlite+aiosqlite:///:memory:",
            youtube_analytics_enabled=True,
        )
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        youtube_analytics_enabled=True,
        youtube_client_id="client-id",
        youtube_client_secret=SecretStr("client-secret"),
        publishing_token_encryption_key=SecretStr("x" * 40),
    )
    adapter = YouTubeAnalyticsAdapter(settings)
    assert adapter.mode.value == "unverified"
    assert adapter.capabilities.mode.value == "unverified"
    with pytest.raises(AnalyticsAdapterError) as raised:
        await adapter.fetch_publication_metrics(
            connection=AnalyticsConnectionContext(
                provider=PublishingProvider.YOUTUBE,
                connection_id=None,
                provider_destination_id="channel-1",
            ),
            provider_publication_id=REMOTE_ID,
            window=MetricWindow(
                start=datetime(2026, 11, 1, tzinfo=UTC),
                end=datetime(2026, 11, 8, tzinfo=UTC),
            ),
        )
    assert raised.value.category == "analytics_unverified"
    await adapter.close()

    _client, app, _ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-youtube-skeleton"
    )
    registry = AnalyticsAdapterRegistry(
        [YouTubeAnalyticsAdapter(settings)]
        + [
            FakeAnalyticsAdapter(provider)
            for provider in PublishingProvider
            if provider is not PublishingProvider.YOUTUBE
        ]
    )
    async with app.state.session_factory() as session:
        run = await AnalyticsService.collect_metrics_for_tenant(
            session,
            tenant_id=UUID(publication["tenant_id"]),
            requested_by_user_id=UUID(publication["requested_by_user_id"]),
            registry=registry,
        )
    assert run.state.value == "failed"
    assert run.failure_category == "analytics_unverified"
    assert await snapshot_rows(app, UUID(publication["tenant_id"])) == []
    await registry.close()


async def test_analytics_tenant_isolation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-isolation"
    )
    install_fake_analytics(app)
    owner = await auth_headers("owner")
    bravo = await auth_headers("bravo_owner")

    created = await client.post(
        "/api/v1/analytics/collections",
        headers=owner,
        json={"idempotency_key": "phase7-isolation-collect"},
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    await app.state.analytics_processor.process_job(
        QueueMessage(
            job_id=UUID(created.json()["background_job_id"]),
            tenant_id=UUID(created.json()["tenant_id"]),
            kind=ANALYTICS_JOB_KIND,
        )
    )

    for path in (
        f"/api/v1/analytics/publications/{publication['id']}/performance",
        f"/api/v1/analytics/publications/{publication['id']}/predicted-vs-actual",
        f"/api/v1/analytics/collection-runs/{run_id}",
    ):
        response = await client.get(path, headers=bravo)
        assert response.status_code == 404, (path, response.text)

    bravo_runs = await client.get("/api/v1/analytics/collection-runs", headers=bravo)
    assert bravo_runs.json()["total"] == 0
    bravo_performance = await client.get(
        "/api/v1/analytics/publications/performance", headers=bravo
    )
    assert bravo_performance.json()["total"] == 0
    bravo_recommendations = await client.get("/api/v1/analytics/recommendations", headers=bravo)
    assert bravo_recommendations.json()["total"] == 0

    # Tenant B can collect for itself; it simply has no publications.
    bravo_created = await client.post(
        "/api/v1/analytics/collections",
        headers=bravo,
        json={"idempotency_key": "phase7-isolation-bravo"},
    )
    assert bravo_created.status_code == 202, bravo_created.text
    await app.state.analytics_processor.process_job(
        QueueMessage(
            job_id=UUID(bravo_created.json()["background_job_id"]),
            tenant_id=UUID(bravo_created.json()["tenant_id"]),
            kind=ANALYTICS_JOB_KIND,
        )
    )
    bravo_run = await client.get(
        f"/api/v1/analytics/collection-runs/{bravo_created.json()['id']}", headers=bravo
    )
    assert bravo_run.json()["publications_considered"] == 0
    assert bravo_run.json()["state"] == "succeeded"


async def test_cost_summary_maths(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    tenant_id = ids["tenant_a"]
    events = [
        ("openai", "model-x", "generation", Decimal("1.50"), True),
        ("openai", "model-x", "generation", Decimal("2.25"), True),
        ("openai", "model-x", "generation", Decimal("0.75"), False),
        ("openai", "model-y", "transcription", Decimal("4.00"), True),
    ]
    async with app.state.session_factory() as session:
        for provider, model, operation, cost, success in events:
            session.add(
                UsageCostEvent(
                    tenant_id=tenant_id,
                    provider=provider,
                    model=model,
                    operation=operation,
                    input_units=100,
                    output_units=50,
                    estimated_cost=cost,
                    currency="USD",
                    latency_ms=10,
                    success=success,
                    failure_category=None if success else "provider_error",
                    correlation_id="phase7-cost-test",
                )
            )
        await session.commit()

    response = await client.get("/api/v1/analytics/costs", headers=owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["total_estimated_cost"]) == Decimal("8.50")
    assert body["event_count"] == 4
    assert body["succeeded"] == 3
    assert body["failed"] == 1
    assert body["publication_count"] == 0
    assert body["cost_per_publication"] is None
    generation = next(
        item for item in body["by_provider_operation"] if item["operation"] == "generation"
    )
    assert generation["event_count"] == 3
    assert generation["succeeded"] == 2
    assert generation["failed"] == 1
    assert Decimal(generation["estimated_cost"]) == Decimal("4.50")
    assert "not" in body["attribution_note"]


async def test_campaign_summary_rollup(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-campaign"
    )
    owner = await auth_headers("owner")
    tenant_id = UUID(publication["tenant_id"])
    publication_ids = [UUID(publication["id"])]
    for _ in range(2):
        publication_ids.append(
            await clone_publication(app, publication_ids[0], campaign_reference="spring-launch")
        )
    async with app.state.session_factory() as session:
        original = await session.get(Publication, publication_ids[0])
        assert original is not None
        original.campaign_reference = "spring-launch"
        await session.commit()
    for publication_id, views in zip(publication_ids, (100.0, 200.0, 300.0), strict=True):
        await insert_snapshot(
            app, tenant_id=tenant_id, publication_id=publication_id, metric="views", value=views
        )
    for publication_id, likes in zip(publication_ids, (10.0, 20.0, None), strict=True):
        await insert_snapshot(
            app,
            tenant_id=tenant_id,
            publication_id=publication_id,
            metric="likes",
            value=likes,
            availability=(
                MetricAvailability.REPORTED if likes is not None else MetricAvailability.UNAVAILABLE
            ),
        )

    response = await client.get(
        "/api/v1/analytics/campaigns/summary",
        headers=owner,
        params={"campaign_reference": "spring-launch"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["publication_count"] == 3
    metrics = {item["metric"]: item for item in body["metrics"]}
    assert metrics["views"]["aggregation"] == "sum"
    assert metrics["views"]["value"] == 600.0
    assert metrics["views"]["reported_publications"] == 3
    assert metrics["likes"]["value"] == 30.0
    assert metrics["likes"]["reported_publications"] == 2
    assert metrics["likes"]["unavailable_publications"] == 1

    unknown = await client.get(
        "/api/v1/analytics/campaigns/summary",
        headers=owner,
        params={"campaign_reference": "no-such-campaign"},
    )
    assert unknown.status_code == 200
    assert unknown.json()["publication_count"] == 0
    assert unknown.json()["gaps"]


async def test_experiment_lifecycle_validation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, _app, _ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-experiments"
    )
    editor = await auth_headers("editor")
    viewer = await auth_headers("viewer")
    bravo = await auth_headers("bravo_owner")

    payload = {
        "name": "Hook wording A/B",
        "hypothesis": "Question hooks retain more viewers than statement hooks.",
        "success_metric": "views",
        "eligibility": {"min_duration_seconds": 10},
        "success_criteria": {"lift_percent": 15},
    }
    forbidden = await client.post("/api/v1/analytics/experiments", headers=viewer, json=payload)
    assert forbidden.status_code == 403, forbidden.text

    invalid = await client.post(
        "/api/v1/analytics/experiments",
        headers=editor,
        json={**payload, "success_metric": "virality"},
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["error"]["code"] == "invalid_success_metric"

    created = await client.post("/api/v1/analytics/experiments", headers=editor, json=payload)
    assert created.status_code == 201, created.text
    experiment = created.json()
    assert experiment["state"] == "draft"
    experiment_id = experiment["id"]

    cross_tenant = await client.get(f"/api/v1/analytics/experiments/{experiment_id}", headers=bravo)
    assert cross_tenant.status_code == 404

    # DRAFT -> COMPLETED is not an allowed transition.
    invalid_transition = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/transitions",
        headers=editor,
        json={"target_state": "completed"},
    )
    assert invalid_transition.status_code == 409, invalid_transition.text

    # RUNNING requires at least one variant.
    no_variant = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/transitions",
        headers=editor,
        json={"target_state": "running"},
    )
    assert no_variant.status_code == 409, no_variant.text

    unlinked = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/variants",
        headers=editor,
        json={"name": "control"},
    )
    assert unlinked.status_code == 422, unlinked.text

    missing_publication = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/variants",
        headers=editor,
        json={"name": "control", "publication_id": str(uuid4())},
    )
    assert missing_publication.status_code == 404, missing_publication.text

    variant = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/variants",
        headers=editor,
        json={
            "name": "control",
            "publication_id": publication["id"],
            "config": {"hook_style": "statement"},
        },
    )
    assert variant.status_code == 201, variant.text
    duplicate = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/variants",
        headers=editor,
        json={"name": "control", "publication_id": publication["id"]},
    )
    assert duplicate.status_code == 409, duplicate.text

    running = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/transitions",
        headers=editor,
        json={"target_state": "running"},
    )
    assert running.status_code == 200, running.text
    assert running.json()["state"] == "running"
    assert running.json()["starts_at"] is not None
    assert len(running.json()["variants"]) == 1

    paused = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/transitions",
        headers=editor,
        json={"target_state": "paused"},
    )
    assert paused.status_code == 200
    completed = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/transitions",
        headers=editor,
        json={"target_state": "completed"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["ends_at"] is not None
    terminal = await client.post(
        f"/api/v1/analytics/experiments/{experiment_id}/transitions",
        headers=editor,
        json={"target_state": "running"},
    )
    assert terminal.status_code == 409


async def _policy_table_counts(app: Any, tenant_id: UUID) -> dict[str, int]:
    async with app.state.session_factory() as session:
        counts = {}
        for label, model in (
            ("gate_verifications", GateVerification),
            ("quality_evaluations", QualityEvaluation),
            ("rights_evidence", RightsEvidence),
            ("feature_flags", FeatureFlag),
            ("approval_requests", ApprovalRequest),
            ("usage_cost_events", UsageCostEvent),
            ("publications", Publication),
        ):
            counts[label] = int(
                await session.scalar(
                    select(func.count(model.id)).where(model.tenant_id == tenant_id)
                )
                or 0
            )
        flags = (
            await session.scalars(select(FeatureFlag).where(FeatureFlag.tenant_id == tenant_id))
        ).all()
        counts["feature_flag_states"] = sum(1 for flag in flags if flag.enabled)
        return counts


async def test_recommendation_generation_evidence_review_and_isolation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ids, publication = await succeeded_publication(
        app_client, auth_headers, media_files, key="phase7-recommendations"
    )
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    tenant_id = UUID(publication["tenant_id"])

    publication_ids = [UUID(publication["id"])]
    for hour in (9, 10, 20, 21, 19):
        publication_ids.append(
            await clone_publication(app, publication_ids[0], published_hour=hour)
        )
    # Original publication joins the morning group at hour 8.
    async with app.state.session_factory() as session:
        original = await session.get(Publication, publication_ids[0])
        assert original is not None
        handoff = dict(original.analytics_handoff or {})
        base = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
        handoff["published_at"] = base.isoformat()
        original.analytics_handoff = handoff
        await session.commit()
    morning_views = (1000.0, 1200.0, 1100.0)
    evening_views = (100.0, 120.0, 110.0)
    ordered = [
        publication_ids[0],
        publication_ids[1],
        publication_ids[2],
        publication_ids[5],
        publication_ids[3],
        publication_ids[4],
    ]
    for publication_id, views in zip(ordered, morning_views + evening_views, strict=True):
        await insert_snapshot(
            app, tenant_id=tenant_id, publication_id=publication_id, metric="views", value=views
        )

    before = await _policy_table_counts(app, tenant_id)

    generated = await client.post("/api/v1/analytics/recommendations/generate", headers=editor)
    assert generated.status_code == 201, generated.text
    items = generated.json()["items"]
    assert len(items) == 1, items
    recommendation = items[0]
    assert recommendation["kind"] == "posting_time"
    assert recommendation["state"] == "proposed"
    assert recommendation["created_by"] == "system:learning-loop"
    assert recommendation["algorithm_version"] == "learning-loop.v1"
    evidence = recommendation["evidence"]
    assert evidence["rule"] == "posting_time_group_views_v1"
    assert evidence["group_stats"]["morning"]["publications"] == 3
    assert evidence["group_stats"]["evening"]["publications"] == 3
    assert evidence["better_group"] == "morning"
    assert len(evidence["publication_ids"]) == 6
    assert evidence["fingerprint"]

    # Generation is idempotent for identical evidence.
    regenerated = await client.post("/api/v1/analytics/recommendations/generate", headers=editor)
    assert regenerated.status_code == 201
    assert regenerated.json()["total"] == 0

    # The learning loop wrote nothing outside its own tables.
    after = await _policy_table_counts(app, tenant_id)
    assert after == before

    # Review requires the reviewer role and only approve/reject decisions.
    editor_review = await client.post(
        f"/api/v1/analytics/recommendations/{recommendation['id']}/review",
        headers=editor,
        json={"decision": "approve"},
    )
    assert editor_review.status_code == 403, editor_review.text
    apply_attempt = await client.post(
        f"/api/v1/analytics/recommendations/{recommendation['id']}/review",
        headers=reviewer,
        json={"decision": "apply"},
    )
    assert apply_attempt.status_code == 422, apply_attempt.text

    approved = await client.post(
        f"/api/v1/analytics/recommendations/{recommendation['id']}/review",
        headers=reviewer,
        json={"decision": "approve", "note": "Worth a one-week scheduling trial."},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["state"] == "approved"
    assert body["reviewed_by_user_id"]
    assert body["reviewed_at"] is not None
    assert body["review_note"] == "Worth a one-week scheduling trial."

    second_review = await client.post(
        f"/api/v1/analytics/recommendations/{recommendation['id']}/review",
        headers=reviewer,
        json={"decision": "reject"},
    )
    assert second_review.status_code == 409, second_review.text

    # Even approval changes nothing outside the recommendations table.
    assert await _policy_table_counts(app, tenant_id) == before

    async with app.state.session_factory() as session:
        actions = list(
            (
                await session.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.tenant_id == tenant_id,
                        AuditEvent.action.like("analytics.%"),
                    )
                )
            ).all()
        )
    assert "analytics.recommendations_generated" in actions
    assert "analytics.recommendation_reviewed" in actions

    # Reject path on a directly seeded proposed recommendation.
    async with app.state.session_factory() as session:
        seeded = Recommendation(
            tenant_id=tenant_id,
            kind="hook",
            title="Seeded for reject-path coverage",
            rationale="Directly seeded test row.",
            evidence={"rule": "seed"},
            algorithm_version="learning-loop.v1",
            created_by="system:learning-loop",
        )
        session.add(seeded)
        await session.commit()
        seeded_id = seeded.id
    rejected = await client.post(
        f"/api/v1/analytics/recommendations/{seeded_id}/review",
        headers=reviewer,
        json={"decision": "reject", "note": "Not enough evidence."},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"


async def test_recommendation_generation_needs_evidence(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    generated = await client.post("/api/v1/analytics/recommendations/generate", headers=editor)
    assert generated.status_code == 201, generated.text
    assert generated.json()["items"] == []
    listing = await client.get("/api/v1/analytics/recommendations", headers=editor)
    assert listing.json()["total"] == 0


def test_metric_result_rejects_invented_values() -> None:
    with pytest.raises(ValueError):
        MetricResult(
            metric="views",
            value=0.0,
            unit="count",
            availability=MetricAvailability.MISSING,
        )
    with pytest.raises(ValueError):
        MetricResult(
            metric="views",
            value=None,
            unit="count",
            availability=MetricAvailability.REPORTED,
        )
