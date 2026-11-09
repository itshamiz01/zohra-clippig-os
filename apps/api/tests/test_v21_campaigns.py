from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from clipos.models import AuditEvent
from clipos.opportunities.content_debt import ContentDebtSignals, score_content_debt
from tests.test_phase6_publishing import create_ready_release
from tests.test_phase8_opportunities import create_source, queue_and_process

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def create_campaign(
    client: Any,
    headers: dict[str, str],
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Spring Fitness Clipping Drive",
        "platform": "youtube",
        "sponsor_label": "FitBrand Co",
        "cpm_rate_minor": 2500,
        "currency": "USD",
        "rights_basis": "licensed",
        "expected_views": 100_000,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/campaigns", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_account(client: Any, headers: dict[str, str], name: str) -> dict[str, Any]:
    response = await client.post("/api/v1/crm/accounts", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["account"]


async def assess(
    client: Any, headers: dict[str, str], campaign_id: str, **overrides: Any
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/campaigns/{campaign_id}/assessments", headers=headers, json=overrides
    )
    assert response.status_code == 201, response.text
    return response.json()


async def transition(
    client: Any,
    headers: dict[str, str],
    campaign_id: str,
    target: str,
    *,
    expect: int = 200,
    **extra: Any,
) -> Any:
    response = await client.post(
        f"/api/v1/campaigns/{campaign_id}/transitions",
        headers=headers,
        json={"target_status": target, **extra},
    )
    assert response.status_code == expect, response.text
    return response


# ---------------------------------------------------------------------------
# Campaign CRUD, provenance and tenancy
# ---------------------------------------------------------------------------
async def test_create_and_get_campaign_with_requirements(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(
        client,
        editor,
        requirements=[
            {
                "kind": "minimum_views",
                "description": "Clips must clear 10k views",
                "required": True,
            },
            {
                "kind": "account_age_days",
                "description": "Channel older than 90 days",
                "required": False,
            },
        ],
    )
    assert campaign["source_kind"] == "manual"
    assert campaign["status"] == "candidate"
    assert campaign["expired"] is False

    detail = await client.get(f"/api/v1/campaigns/{campaign['id']}", headers=editor)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert [item["kind"] for item in body["requirements"]] == [
        "minimum_views",
        "account_age_days",
    ]
    assert body["publication_ids"] == []
    assert body["latest_assessment"] is None


async def test_campaign_create_idempotency_replay_and_conflict(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    first = await create_campaign(client, editor, idempotency_key="campaign-create-key-0001")
    replay = await create_campaign(client, editor, idempotency_key="campaign-create-key-0001")
    assert replay["id"] == first["id"]

    conflict = await client.post(
        "/api/v1/campaigns",
        headers=editor,
        json={
            "name": "A Different Campaign",
            "platform": "youtube",
            "idempotency_key": "campaign-create-key-0001",
        },
    )
    assert conflict.status_code == 409, conflict.text


async def test_campaign_tenant_isolation(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    bravo = await auth_headers("bravo_owner")
    campaign = await create_campaign(client, editor)

    assert (
        await client.get(f"/api/v1/campaigns/{campaign['id']}", headers=bravo)
    ).status_code == 404
    listing = await client.get("/api/v1/campaigns", headers=bravo)
    assert listing.status_code == 200
    assert listing.json()["total"] == 0
    summary = await client.get("/api/v1/campaigns/summary", headers=bravo)
    assert summary.json()["campaigns"] == []


async def test_campaign_viewer_is_read_only(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    viewer = await auth_headers("viewer")
    campaign = await create_campaign(client, editor)

    assert (await client.get("/api/v1/campaigns", headers=viewer)).status_code == 200
    assert (
        await client.get(f"/api/v1/campaigns/{campaign['id']}", headers=viewer)
    ).status_code == 200
    forbidden = await client.post(
        "/api/v1/campaigns",
        headers=viewer,
        json={"name": "Nope", "platform": "youtube"},
    )
    assert forbidden.status_code == 403, forbidden.text


# ---------------------------------------------------------------------------
# Seed import
# ---------------------------------------------------------------------------
async def test_import_campaigns_dedup_and_replay(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    records = [
        {
            "name": "Whop-style CPM push A",
            "platform": "tiktok",
            "source_reference": "https://campaigns.example.com/a",
            "cpm_rate_minor": 3000,
            "currency": "USD",
            "rights_basis": "authorized",
        },
        {
            "name": "Whop-style CPM push B",
            "platform": "youtube",
            "source_reference": "https://campaigns.example.com/b",
        },
        {"name": "Unreferenced listing", "platform": "youtube"},
    ]
    first = await client.post(
        "/api/v1/campaigns/import",
        headers=editor,
        json={"idempotency_key": "campaign-import-key-0001", "records": records},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["created"]) == 3
    assert body["skipped"] == []

    replay = await client.post(
        "/api/v1/campaigns/import",
        headers=editor,
        json={"idempotency_key": "campaign-import-key-0001", "records": records},
    )
    assert replay.status_code == 200, replay.text
    replayed = replay.json()
    # Referenced listings dedup on source_reference; the unreferenced
    # record has no dedup key and imports again (documented behaviour).
    assert len(replayed["created"]) == 1
    assert {item["index"] for item in replayed["skipped"]} == {0, 1}
    assert all("duplicate" in item["reason"] for item in replayed["skipped"])

    listing = await client.get("/api/v1/campaigns", headers=editor)
    imported = [item for item in listing.json()["items"] if item["source_kind"] == "seed_import"]
    assert len(imported) == 4


# ---------------------------------------------------------------------------
# Profitability
# ---------------------------------------------------------------------------
async def test_profitability_math_exact(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor)
    result = await assess(
        client,
        editor,
        campaign["id"],
        editing_cost_minor=10_000,
        model_cost_minor=2_000,
        distribution_cost_minor=3_000,
        risk_percent=10,
    )
    assert result["state"] == "assessable"
    assert result["missing_inputs"] == []
    # gross = 100_000 views x 2500 minor / 1000 = 250_000; risk 10% = 25_000.
    assert result["gross_revenue_minor"] == 250_000
    assert result["risk_adjustment_minor"] == 25_000
    assert result["expected_profit_minor"] == 250_000 - 15_000 - 25_000
    assert result["capped_by_max_payout"] is False


async def test_profitability_capped_by_max_payout(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor, max_payout_minor=100_000)
    result = await assess(client, editor, campaign["id"])
    assert result["gross_revenue_minor"] == 100_000
    assert result["capped_by_max_payout"] is True
    assert result["expected_profit_minor"] == 100_000


async def test_profitability_defaults_come_from_settings(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor)
    result = await assess(client, editor, campaign["id"])
    # Test settings carry the recorded zero defaults: no cost is invented.
    assert result["editing_cost_minor"] == 0
    assert result["model_cost_minor"] == 0
    assert result["distribution_cost_minor"] == 0
    assert result["risk_percent"] == 0
    assert result["expected_profit_minor"] == result["gross_revenue_minor"]


async def test_profitability_unassessable_without_cpm(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor, cpm_rate_minor=None)
    result = await assess(client, editor, campaign["id"])
    assert result["state"] == "unassessable"
    assert result["missing_inputs"] == ["cpm_rate_minor"]
    assert result["expected_profit_minor"] is None
    assert result["gross_revenue_minor"] is None


async def test_profitability_unassessable_without_expected_views(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor, expected_views=None)
    result = await assess(client, editor, campaign["id"])
    assert result["state"] == "unassessable"
    assert result["missing_inputs"] == ["expected_views"]
    # An assessment-time views figure makes the same campaign assessable.
    fixed = await assess(client, editor, campaign["id"], expected_views=40_000)
    assert fixed["state"] == "assessable"
    assert fixed["gross_revenue_minor"] == 100_000


async def test_assessments_are_append_only_history(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor)
    first = await assess(client, editor, campaign["id"], risk_percent=0)
    second = await assess(client, editor, campaign["id"], risk_percent=50)
    assert first["expected_profit_minor"] == 250_000
    assert second["expected_profit_minor"] == 125_000

    history = await client.get(f"/api/v1/campaigns/{campaign['id']}/assessments", headers=editor)
    assert history.status_code == 200
    items = history.json()["items"]
    assert [item["id"] for item in items] == [second["id"], first["id"]]
    assert items[1]["risk_percent"] == 0  # the first assessment is unchanged

    detail = await client.get(f"/api/v1/campaigns/{campaign['id']}", headers=editor)
    assert detail.json()["latest_assessment"]["id"] == second["id"]


# ---------------------------------------------------------------------------
# Qualification gates
# ---------------------------------------------------------------------------
async def test_qualify_blocked_when_rights_unknown(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor, rights_basis="unknown")
    await assess(client, editor, campaign["id"])
    response = await transition(client, editor, campaign["id"], "qualified", expect=422)
    assert "campaign_rights_unknown" in response.text


async def test_qualify_unassessable_needs_recorded_override(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, app, ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor, cpm_rate_minor=None)
    await assess(client, editor, campaign["id"])

    blocked = await transition(client, editor, campaign["id"], "qualified", expect=422)
    assert "campaign_unassessable" in blocked.text

    allowed = await transition(
        client,
        editor,
        campaign["id"],
        "qualified",
        override_reason="Sponsor confirmed rates verbally; CPM sheet pending.",
    )
    body = allowed.json()
    assert body["status"] == "qualified"
    assert body["qualification_override_reason"] is not None

    async with app.state.session_factory() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == ids["tenant_a"],
                    AuditEvent.action == "campaign.status_changed",
                    AuditEvent.resource_id == campaign["id"],
                )
            )
        ).all()
    assert len(events) == 1
    assert events[0].event_data["assessment_override"] is True


async def test_qualify_with_assessment_and_invalid_transition(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor)
    await assess(client, editor, campaign["id"])
    ok = await transition(client, editor, campaign["id"], "qualified")
    assert ok.json()["status"] == "qualified"
    assert ok.json()["qualification_override_reason"] is None

    other = await create_campaign(client, editor, name="Second campaign")
    # candidate -> active skips qualification and is not a legal transition.
    await transition(client, editor, other["id"], "active", expect=409)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------
async def test_expired_campaign_flagged_on_read_and_summary(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    campaign = await create_campaign(client, editor, deadline_at=past)
    assert campaign["expired"] is True
    # Stored status is untouched: expiry is a read-time flag.
    assert campaign["status"] == "candidate"

    summary = await client.get("/api/v1/campaigns/summary", headers=editor)
    body = summary.json()
    assert body["counts_by_status"]["expired"] == 1
    assert body["expired_open"] == 1


# ---------------------------------------------------------------------------
# Publication links
# ---------------------------------------------------------------------------
async def test_link_publication_and_duplicate_conflict(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor)
    *_, publication = await create_ready_release(
        app_client, auth_headers, media_files, key="v21-link-0001"
    )

    linked = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/publications",
        headers=editor,
        json={"publication_id": publication["id"]},
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["publication_id"] == publication["id"]

    duplicate = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/publications",
        headers=editor,
        json={"publication_id": publication["id"]},
    )
    assert duplicate.status_code == 409, duplicate.text

    missing = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/publications",
        headers=editor,
        json={"publication_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert missing.status_code == 404, missing.text

    detail = await client.get(f"/api/v1/campaigns/{campaign['id']}", headers=editor)
    assert detail.json()["publication_ids"] == [publication["id"]]


# ---------------------------------------------------------------------------
# Payouts -> Phase 9 revenue records
# ---------------------------------------------------------------------------
async def test_payout_creates_campaign_payout_revenue_record(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    account = await create_account(client, editor, "FitBrand Co")
    campaign = await create_campaign(client, editor, crm_account_id=account["id"])

    payout = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/payouts",
        headers=editor,
        json={"amount_minor_units": 75_000, "currency": "USD", "external_reference": "PO-1"},
    )
    assert payout.status_code == 201, payout.text
    record = payout.json()
    assert record["kind"] == "campaign_payout"
    assert record["status"] == "paid"
    assert record["campaign_id"] == campaign["id"]
    assert record["account_id"] == account["id"]

    revenue = await client.get(f"/api/v1/crm/accounts/{account['id']}/revenue", headers=editor)
    assert revenue.status_code == 200
    assert [item["id"] for item in revenue.json()["items"]] == [record["id"]]

    summary = await client.get(
        f"/api/v1/crm/accounts/{account['id']}/revenue-summary", headers=editor
    )
    usd = summary.json()["currencies"][0]
    # Collected campaign money is its own bucket, never "collected".
    assert usd["campaign_payout"] == 75_000
    assert usd["collected"] == 0


async def test_payout_currency_must_match_campaign(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    account = await create_account(client, editor, "FitBrand Co")
    campaign = await create_campaign(client, editor, crm_account_id=account["id"])
    response = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/payouts",
        headers=editor,
        json={"amount_minor_units": 100, "currency": "EUR"},
    )
    assert response.status_code == 422, response.text
    assert "campaign_payout_currency_mismatch" in response.text


async def test_payout_requires_an_account(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    campaign = await create_campaign(client, editor)
    response = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/payouts",
        headers=editor,
        json={"amount_minor_units": 100, "currency": "USD"},
    )
    assert response.status_code == 422, response.text
    assert "campaign_payout_account_required" in response.text

    account = await create_account(client, editor, "Named At Payout")
    ok = await client.post(
        f"/api/v1/campaigns/{campaign['id']}/payouts",
        headers=editor,
        json={
            "amount_minor_units": 100,
            "currency": "USD",
            "account_id": account["id"],
        },
    )
    assert ok.status_code == 201, ok.text


async def test_summary_reports_expected_and_collected_separately(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    account = await create_account(client, editor, "FitBrand Co")
    campaign = await create_campaign(client, editor, crm_account_id=account["id"])
    await assess(client, editor, campaign["id"], risk_percent=10)
    await client.post(
        f"/api/v1/campaigns/{campaign['id']}/payouts",
        headers=editor,
        json={"amount_minor_units": 40_000, "currency": "USD"},
    )
    await client.post(
        f"/api/v1/campaigns/{campaign['id']}/payouts",
        headers=editor,
        json={"amount_minor_units": 35_000, "currency": "USD"},
    )

    summary = await client.get("/api/v1/campaigns/summary", headers=editor)
    body = summary.json()
    assert body["counts_by_status"] == {"candidate": 1}
    entry = body["campaigns"][0]
    assert entry["expected_profit_minor"] == 225_000
    assert entry["collected_minor"] == 75_000
    assert entry["payout_count"] == 2
    assert entry["linked_publications"] == 0


# ---------------------------------------------------------------------------
# Content debt (Phase 8 extension)
# ---------------------------------------------------------------------------
async def import_one(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    record: dict[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    client, app, ids = app_client
    editor = await auth_headers("editor")
    source = await create_source(client, editor, kind="seed_list", name="Debt shortlist")
    await queue_and_process(client, app, editor, source_id=source["id"], key=key, records=[record])
    listing = await client.get("/api/v1/opportunities", headers=editor)
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert len(items) == 1
    return items[0]


DEBT_FULL_RECORD: dict[str, Any] = {
    "external_key": "yt-fitlibrary",
    "title": "Fit Library Podcast",
    "creator_name": "Fit Library",
    "creator_handle": "@fitlibrary",
    "url": "https://www.youtube.com/@fitlibrary",
    "platform": "youtube",
    "contact_route_label": "business email on channel About page",
    "niche": "fitness",
    "language": "en",
    "audience_band": "100k-500k",
    "posting_cadence": "weekly",
    "sample_item_count": 6,
    "recent_views_band": "10k-100k",
    "library_episode_count": 175,
    "library_long_form_hours": 320,
    "existing_shorts_count": 60,
}


async def test_content_debt_full_coverage_and_estimate(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    item = await import_one(app_client, auth_headers, DEBT_FULL_RECORD, key="v21-debt-full-01")
    debt = item["content_debt"]
    assert debt["scoring_version"] == "content-debt-v1"
    assert debt["coverage_percent"] == 100.0
    assert debt["partial"] is False
    # 25 (hours) + 23.929 (gap) + 18 (commercial) + 7 + 7 + 5 + 5 = 90.929
    assert debt["score"] == pytest.approx(90.929, abs=0.01)
    # 175 episodes x 8 assumed moments - 60 recorded shorts.
    assert debt["estimated_content_opportunities"] == 1340
    assert debt["moments_per_episode"] == 8
    assert "8 assumed moments per episode" in debt["estimate_basis"]
    components = {entry["key"]: entry for entry in debt["components"]}
    assert components["long_form_hours"]["points"] == 25.0
    assert components["utilisation_gap"]["applicable"] is True
    # The commercial score is untouched by the debt computation.
    assert item["overall_score"] is not None
    assert item["score_version"] == "opportunity-score-v1"


async def test_content_debt_partial_coverage(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    record = {
        "external_key": "yt-quietlibrary",
        "title": "Quiet Library",
        "library_episode_count": 100,
        "existing_shorts_count": 100,
    }
    item = await import_one(app_client, auth_headers, record, key="v21-debt-partial-01")
    debt = item["content_debt"]
    # Only the utilisation gap (25) and contactability (5) can be scored.
    assert debt["coverage_percent"] == 30.0
    assert debt["partial"] is True
    assert debt["score"] == pytest.approx(72.917, abs=0.01)
    assert debt["estimated_content_opportunities"] == 700
    components = {entry["key"]: entry for entry in debt["components"]}
    assert components["long_form_hours"]["applicable"] is False
    assert components["commercial_potential"]["applicable"] is False
    assert components["audience_size"]["applicable"] is False


async def test_content_debt_without_library_data_stays_unestimated(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    record = {
        "external_key": "yt-plain",
        "title": "Plain Channel",
        "creator_handle": "@plain",
        "url": "https://www.youtube.com/@plain",
        "platform": "youtube",
    }
    item = await import_one(app_client, auth_headers, record, key="v21-debt-none-01")
    debt = item["content_debt"]
    assert debt["estimated_content_opportunities"] is None
    assert debt["estimate_basis"] is None
    components = {entry["key"]: entry for entry in debt["components"]}
    assert components["utilisation_gap"]["applicable"] is False
    assert components["long_form_hours"]["applicable"] is False


async def test_content_debt_custom_moments_assumption(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    record = {
        "external_key": "yt-custom",
        "title": "Custom Assumption",
        "library_episode_count": 100,
        "existing_shorts_count": 50,
        "moments_per_episode": 5,
    }
    item = await import_one(app_client, auth_headers, record, key="v21-debt-custom-01")
    debt = item["content_debt"]
    assert debt["moments_per_episode"] == 5
    assert debt["estimated_content_opportunities"] == 450
    assert "5 assumed moments per episode" in debt["estimate_basis"]


def test_content_debt_scoring_with_no_inputs() -> None:
    result = score_content_debt(
        ContentDebtSignals(
            library_episode_count=None,
            library_long_form_hours=None,
            existing_shorts_count=None,
            moments_per_episode=8,
            audience_band=None,
            posting_cadence=None,
            contact_route_label=None,
            niche=None,
            commercial_component_scores={},
        )
    )
    assert result.score is None
    assert result.estimated_content_opportunities is None
    # Contactability is the only always-applicable factor (5 of 100).
    assert result.coverage_percent == 5.0
    assert result.partial is True
