from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from clipos.core.enums import OpportunitySourceKind
from clipos.core.ssrf import SSRFBlockedError, fetch_public_url, validate_public_url
from clipos.gateways.opportunities import (
    DisabledOpportunityAdapter,
    DisabledSearchAdapter,
    FakeOpportunityAdapter,
    OpportunityAdapterRegistry,
    RssFeedAdapter,
    SeedListAdapter,
)
from clipos.models import AuditEvent, Opportunity
from clipos.opportunities.scoring import OpportunitySignals, score_opportunity
from clipos.queue import QueueMessage
from clipos.services.opportunities import OPPORTUNITY_JOB_KIND

pytestmark = pytest.mark.integration

PUBLIC_IP = "93.184.216.34"

SEED_RECORDS: list[dict[str, Any]] = [
    {
        "external_key": "yt-deepdives",
        "title": "Deep Dive Documentaries",
        "creator_name": "Deep Dive Documentaries",
        "creator_handle": "@DeepDives",
        "url": "https://www.youtube.com/@DeepDives?utm_source=share",
        "platform": "youtube",
        "contact_route_label": "business email on channel About page",
        "niche": "documentary",
        "language": "en",
        "audience_band": "100k-500k",
        "posting_cadence": "weekly",
        "sample_item_count": 6,
        "recent_views_band": "10k-100k",
    },
    {
        "external_key": "yt-buildlab",
        "title": "Build Lab",
        "creator_name": "Build Lab",
        "creator_handle": "buildlab",
        "url": "https://www.youtube.com/@buildlab",
        "platform": "youtube",
        "niche": "diy",
        "language": "en",
        "audience_band": "10k-100k",
        "posting_cadence": "weekly",
        "sample_item_count": 3,
    },
]

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Systems Deep Dive</title>
<link>https://systems.example.com/</link>
<language>en-us</language>
<item><title>Episode 3</title><pubDate>Mon, 03 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Episode 2</title><pubDate>Mon, 27 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Episode 1</title><pubDate>Mon, 20 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>
"""

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Cast</title>
<link rel="alternate" href="https://atomcast.example.com/"/>
<entry><title>Entry 1</title><published>2026-11-01T10:00:00Z</published></entry>
<entry><title>Entry 2</title><published>2026-11-03T10:00:00Z</published></entry>
</feed>
"""


async def public_resolver(hostname: str) -> list[str]:
    del hostname
    return [PUBLIC_IP]


async def create_source(
    client: Any,
    headers: dict[str, str],
    *,
    kind: str,
    name: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/opportunities/sources",
        headers=headers,
        json={"kind": kind, "name": name, "config": config or {}},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def queue_and_process(
    client: Any,
    app: Any,
    headers: dict[str, str],
    *,
    source_id: str,
    key: str,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"idempotency_key": key}
    if records is not None:
        payload["records"] = records
    created = await client.post(
        f"/api/v1/opportunities/sources/{source_id}/syncs",
        headers=headers,
        json=payload,
    )
    assert created.status_code == 202, created.text
    run = created.json()
    assert run["state"] == "queued"
    assert run["background_job_id"]
    await app.state.worker.process(
        QueueMessage(
            job_id=UUID(run["background_job_id"]),
            tenant_id=UUID(run["tenant_id"]),
            kind=OPPORTUNITY_JOB_KIND,
        )
    )
    finished = await client.get(f"/api/v1/opportunities/sync-runs/{run['id']}", headers=headers)
    assert finished.status_code == 200, finished.text
    return finished.json()


async def import_seed_records(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    *,
    key: str = "phase8-seed-import-01",
    records: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    client, app, ids = app_client
    editor = await auth_headers("editor")
    source = await create_source(client, editor, kind="seed_list", name="Operator shortlist")
    run = await queue_and_process(
        client,
        app,
        editor,
        source_id=source["id"],
        key=key,
        records=SEED_RECORDS if records is None else records,
    )
    return client, app, ids, source, run


def install_rss(
    app: Any,
    *,
    handler: Any,
) -> OpportunityAdapterRegistry:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    registry = OpportunityAdapterRegistry(
        [
            SeedListAdapter(),
            RssFeedAdapter(app.state.settings, resolver=public_resolver, client=client),
            DisabledOpportunityAdapter(OpportunitySourceKind.PUBLIC_SITE),
            DisabledSearchAdapter(),
        ]
    )
    app.state.opportunity_adapters = registry
    app.state.opportunity_processor.registry = registry
    app.state.worker.opportunity_processor.registry = registry
    return registry


async def test_seed_list_import_end_to_end(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids, source, run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    viewer = await auth_headers("viewer")

    assert source["adapter_mode"] == "real_tested"
    assert source["kind"] == "seed_list"
    assert run["state"] == "succeeded"
    assert run["adapter_mode"] == "real_tested"
    assert run["records_received"] == 2
    assert run["records_created"] == 2
    assert run["records_merged"] == 0
    assert run["duplicates_flagged"] == 0

    # A viewer cannot create sources.
    forbidden = await client.post(
        "/api/v1/opportunities/sources",
        headers=viewer,
        json={"kind": "seed_list", "name": "Nope", "config": {}},
    )
    assert forbidden.status_code == 403, forbidden.text

    listing = await client.get("/api/v1/opportunities", headers=editor)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["total"] == 2
    # Default ordering is by score: the fully-evidenced record leads.
    first, second = body["items"]
    assert first["external_key"] == "yt-deepdives"
    assert first["creator_handle"] == "deepdives"
    assert first["canonical_url"] == "https://www.youtube.com/@DeepDives"
    assert first["overall_score"] > second["overall_score"]
    assert first["score_version"] == "opportunity-score-v1"
    assert first["state"] == "new"

    detail = await client.get(f"/api/v1/opportunities/{first['id']}", headers=editor)
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    provenance = detail_body["evidence"]["provenance"]
    assert provenance["creator_handle"]["adapter"] == "seed_list_adapter"
    assert provenance["creator_handle"]["source_url"] == "https://www.youtube.com/@DeepDives"
    assert detail_body["evidence"]["signals"]["sample_item_count"] == 6
    assert detail_body["evidence"]["source_record"]["external_key"] == "yt-deepdives"
    component_keys = {item["key"] for item in detail_body["score_components"]}
    assert component_keys == {
        "content_opportunity",
        "audience_fit",
        "performance_fit",
        "contactability",
        "rights_feasibility",
        "effort_inverse",
    }

    refreshed = await client.get(f"/api/v1/opportunities/sources/{source['id']}", headers=editor)
    assert refreshed.json()["last_synced_at"] is not None

    job = await client.get(f"/api/v1/jobs/{run['background_job_id']}", headers=editor)
    assert job.json()["state"] == "succeeded"
    assert job.json()["result"]["records_created"] == 2


async def test_seed_import_dedup_exact_match_merges(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids, source, first_run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    assert first_run["records_created"] == 2

    before = await client.get("/api/v1/opportunities", headers=editor)
    before_ids = {item["id"] for item in before.json()["items"]}

    second_run = await queue_and_process(
        client,
        app,
        editor,
        source_id=source["id"],
        key="phase8-seed-import-02",
        records=SEED_RECORDS,
    )
    assert second_run["state"] == "succeeded"
    assert second_run["records_created"] == 0
    assert second_run["records_merged"] == 2

    after = await client.get("/api/v1/opportunities", headers=editor)
    assert after.json()["total"] == 2
    assert {item["id"] for item in after.json()["items"]} == before_ids

    async with app.state.session_factory() as session:
        rows = list((await session.scalars(select(Opportunity))).all())
    assert len(rows) == 2


async def test_fuzzy_match_flagged_not_merged(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids, source, _run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    listing = await client.get("/api/v1/opportunities", headers=editor)
    original = next(
        item for item in listing.json()["items"] if item["external_key"] == "yt-deepdives"
    )

    fuzzy_record = {
        "external_key": "yt-deepdives-daily",
        "title": "Deep Dive Documentaries",
        "creator_name": "Deep Dive Documentaries",
        "creator_handle": "@deepdivesdaily",
        "url": "https://www.youtube.com/@deepdivesdaily",
        "platform": "youtube",
        "niche": "documentary",
        "language": "en",
        "audience_band": "100k-500k",
        "posting_cadence": "daily",
        "sample_item_count": 9,
    }
    run = await queue_and_process(
        client,
        app,
        editor,
        source_id=source["id"],
        key="phase8-seed-import-fuzzy",
        records=[fuzzy_record],
    )
    assert run["records_created"] == 1
    assert run["records_merged"] == 0
    assert run["duplicates_flagged"] == 1

    listing = await client.get("/api/v1/opportunities", headers=editor)
    assert listing.json()["total"] == 3
    flagged = next(
        item for item in listing.json()["items"] if item["external_key"] == "yt-deepdives-daily"
    )
    # Flagged for human review, pointed at the likely original, and NOT
    # merged into it: both records still exist independently.
    assert flagged["possible_duplicate_of_id"] == original["id"]
    assert flagged["id"] != original["id"]


async def test_suppression_blocks_reimport_and_hides_existing(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids, source, _run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")

    # Editors cannot create suppressions; suppression is a review act.
    forbidden = await client.post(
        "/api/v1/opportunities/suppressions",
        headers=editor,
        json={"match_type": "creator_handle", "match_value": "deepdives", "reason": "x"},
    )
    assert forbidden.status_code == 403, forbidden.text

    created = await client.post(
        "/api/v1/opportunities/suppressions",
        headers=reviewer,
        json={
            "match_type": "creator_handle",
            "match_value": "@DeepDives",
            "reason": "Creator asked not to be contacted",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["match_value"] == "deepdives"

    listing = await client.get("/api/v1/opportunities", headers=editor)
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["external_key"] == "yt-buildlab"

    suppressed = await client.get("/api/v1/opportunities?state=suppressed", headers=editor)
    assert suppressed.json()["total"] == 1
    assert suppressed.json()["items"][0]["external_key"] == "yt-deepdives"
    suppressed_id = suppressed.json()["items"][0]["id"]
    detail = await client.get(f"/api/v1/opportunities/{suppressed_id}", headers=editor)
    assert detail.json()["state"] == "suppressed"

    run = await queue_and_process(
        client,
        app,
        editor,
        source_id=source["id"],
        key="phase8-seed-import-suppressed",
        records=SEED_RECORDS,
    )
    assert run["suppressed_skipped"] == 1
    assert run["records_created"] == 0
    assert run["records_merged"] == 1

    async with app.state.session_factory() as session:
        rows = list((await session.scalars(select(Opportunity))).all())
    assert len(rows) == 2


async def test_scoring_reproducible_components_and_missing_factor(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids, source, _run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    listing = await client.get("/api/v1/opportunities", headers=editor)
    by_key = {item["external_key"]: item for item in listing.json()["items"]}

    detail = await client.get(
        f"/api/v1/opportunities/{by_key['yt-buildlab']['id']}", headers=editor
    )
    components = {item["key"]: item for item in detail.json()["score_components"]}
    # Build Lab supplied no recent-views band: the factor is excluded,
    # never scored as zero.
    assert components["performance_fit"]["applicable"] is False
    assert components["performance_fit"]["score"] is None
    assert components["performance_fit"]["weighted_score"] == 0
    assert components["audience_fit"]["score"] == 85.0
    assert all(item["scoring_version"] == "opportunity-score-v1" for item in components.values())

    # Rescoring through a merge reproduces the identical overall score.
    run = await queue_and_process(
        client,
        app,
        editor,
        source_id=source["id"],
        key="phase8-seed-import-rescore",
        records=SEED_RECORDS,
    )
    assert run["records_merged"] == 2
    rescored = await client.get(
        f"/api/v1/opportunities/{by_key['yt-deepdives']['id']}", headers=editor
    )
    assert rescored.json()["overall_score"] == by_key["yt-deepdives"]["overall_score"]

    # The pure scoring function is deterministic for identical signals.
    signals = OpportunitySignals(
        platform="youtube",
        niche="documentary",
        language="en",
        audience_band="100k_500k",
        posting_cadence="weekly",
        contact_route_label="business email on channel About page",
        sample_item_count=6,
        recent_views_band="10k_100k",
        third_party_compilation=False,
    )
    first = score_opportunity(signals)
    second = score_opportunity(signals)
    assert first.overall_score == second.overall_score
    assert first.overall_score == pytest.approx(by_key["yt-deepdives"]["overall_score"])
    assert [item.score for item in first.components] == [item.score for item in second.components]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/feed.xml",
        "http://0.0.0.0/feed.xml",
        "http://[::1]/feed.xml",
        "http://10.1.2.3/feed.xml",
        "http://172.16.0.9/feed.xml",
        "http://192.168.1.10/feed.xml",
        "http://169.254.169.254/latest/meta-data",
        "ftp://93.184.216.34/feed.xml",
        "http://user:pass@93.184.216.34/feed.xml",
    ],
)
async def test_ssrf_guard_blocks_unsafe_targets(url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        await validate_public_url(url, resolver=public_resolver)


async def test_ssrf_guard_blocks_dns_names_resolving_private() -> None:
    async def mixed_resolver(hostname: str) -> list[str]:
        del hostname
        return [PUBLIC_IP, "10.0.0.5"]

    async def private_resolver(hostname: str) -> list[str]:
        del hostname
        return ["192.168.1.10"]

    with pytest.raises(SSRFBlockedError) as raised:
        await validate_public_url("http://internal.example.com/feed", resolver=private_resolver)
    assert raised.value.category == "ssrf_non_public_address"
    # One private address in the answer poisons the whole name.
    with pytest.raises(SSRFBlockedError):
        await validate_public_url("http://mixed.example.com/feed", resolver=mixed_resolver)
    ok = await validate_public_url("http://feeds.example.com/feed", resolver=public_resolver)
    assert ok.addresses == (PUBLIC_IP,)


async def test_ssrf_guard_redirect_size_and_content_type_controls() -> None:
    def redirect_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, content=RSS_XML, headers={"content-type": "application/rss+xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect_handler)) as client:
        with pytest.raises(SSRFBlockedError) as raised:
            await fetch_public_url(
                "http://feeds.example.com/start",
                resolver=public_resolver,
                client=client,
                allowed_content_types={"application/rss+xml"},
            )
    assert raised.value.category == "ssrf_non_public_address"

    def loop_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(302, headers={"location": "/loop"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(loop_handler)) as client:
        with pytest.raises(SSRFBlockedError) as raised:
            await fetch_public_url(
                "http://feeds.example.com/loop",
                resolver=public_resolver,
                client=client,
                max_redirects=3,
            )
    assert raised.value.category == "ssrf_redirect_limit"

    def big_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200, content=b"x" * 500, headers={"content-type": "application/rss+xml"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(big_handler)) as client:
        with pytest.raises(SSRFBlockedError) as raised:
            await fetch_public_url(
                "http://feeds.example.com/big",
                resolver=public_resolver,
                client=client,
                max_bytes=64,
            )
    assert raised.value.category == "ssrf_response_too_large"

    def html_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(html_handler)) as client:
        with pytest.raises(SSRFBlockedError) as raised:
            await fetch_public_url(
                "http://feeds.example.com/page",
                resolver=public_resolver,
                client=client,
                allowed_content_types={"application/rss+xml", "application/xml"},
            )
    assert raised.value.category == "ssrf_content_type_not_allowed"


async def test_rss_import_via_fake_transport_and_malformed_feed(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids = app_client
    editor = await auth_headers("editor")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/systems.xml":
            return httpx.Response(
                200, content=RSS_XML, headers={"content-type": "application/rss+xml"}
            )
        if request.url.path == "/atom.xml":
            return httpx.Response(
                200, content=ATOM_XML, headers={"content-type": "application/atom+xml"}
            )
        return httpx.Response(
            200, content=b"this is not xml", headers={"content-type": "application/rss+xml"}
        )

    install_rss(app, handler=handler)
    source = await create_source(
        client,
        editor,
        kind="rss_feed",
        name="Systems feed",
        config={"feed_url": "http://feeds.example.com/systems.xml"},
    )
    assert source["adapter_mode"] == "unverified"
    run = await queue_and_process(
        client, app, editor, source_id=source["id"], key="phase8-rss-import-01"
    )
    assert run["state"] == "succeeded"
    assert run["adapter_mode"] == "unverified"
    assert run["records_created"] == 1

    listing = await client.get("/api/v1/opportunities", headers=editor)
    assert listing.json()["total"] == 1
    item = listing.json()["items"][0]
    assert item["title"] == "Systems Deep Dive"
    assert item["platform"] == "podcast"
    assert item["posting_cadence"] == "weekly"
    detail = await client.get(f"/api/v1/opportunities/{item['id']}", headers=editor)
    assert detail.json()["evidence"]["signals"]["sample_item_count"] == 3
    assert detail.json()["evidence"]["provenance"]["title"]["adapter"] == "rss_feed_adapter"
    assert detail.json()["evidence"]["source_record"]["item_titles"][0] == "Episode 3"

    atom_source = await create_source(
        client,
        editor,
        kind="rss_feed",
        name="Atom feed",
        config={"feed_url": "http://feeds.example.com/atom.xml"},
    )
    atom_run = await queue_and_process(
        client, app, editor, source_id=atom_source["id"], key="phase8-rss-import-02"
    )
    assert atom_run["records_created"] == 1

    broken = await create_source(
        client,
        editor,
        kind="rss_feed",
        name="Broken feed",
        config={"feed_url": "http://feeds.example.com/broken.xml"},
    )
    broken_run = await queue_and_process(
        client, app, editor, source_id=broken["id"], key="phase8-rss-import-03"
    )
    assert broken_run["state"] == "failed"
    assert broken_run["failure_category"] == "rss_parse_failed"
    assert broken_run["records_created"] == 0


async def test_opportunity_tenant_isolation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids, source, _run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    bravo = await auth_headers("bravo_owner")

    listing = await client.get("/api/v1/opportunities", headers=bravo)
    assert listing.json()["total"] == 0
    sources = await client.get("/api/v1/opportunities/sources", headers=bravo)
    assert sources.json()["total"] == 0

    alpha_listing = await client.get("/api/v1/opportunities", headers=editor)
    opportunity_id = alpha_listing.json()["items"][0]["id"]
    for url in (
        f"/api/v1/opportunities/{opportunity_id}",
        f"/api/v1/opportunities/sources/{source['id']}",
    ):
        response = await client.get(url, headers=bravo)
        assert response.status_code == 404, (url, response.text)

    runs = await client.get("/api/v1/opportunities/sync-runs", headers=bravo)
    assert runs.json()["total"] == 0


async def test_lifecycle_transition_permissions(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids, _source, _run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    viewer = await auth_headers("viewer")
    listing = await client.get("/api/v1/opportunities", headers=editor)
    target = next(item for item in listing.json()["items"] if item["external_key"] == "yt-buildlab")

    forbidden = await client.post(
        f"/api/v1/opportunities/{target['id']}/transitions",
        headers=viewer,
        json={"target_state": "reviewing"},
    )
    assert forbidden.status_code == 403, forbidden.text

    moved = await client.post(
        f"/api/v1/opportunities/{target['id']}/transitions",
        headers=editor,
        json={"target_state": "reviewing"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["state"] == "reviewing"

    # Shortlisting is a reviewer decision, not an editor one.
    denied = await client.post(
        f"/api/v1/opportunities/{target['id']}/transitions",
        headers=editor,
        json={"target_state": "shortlisted"},
    )
    assert denied.status_code == 403, denied.text

    shortlisted = await client.post(
        f"/api/v1/opportunities/{target['id']}/transitions",
        headers=reviewer,
        json={"target_state": "shortlisted"},
    )
    assert shortlisted.status_code == 200, shortlisted.text
    assert shortlisted.json()["state"] == "shortlisted"

    invalid = await client.post(
        f"/api/v1/opportunities/{target['id']}/transitions",
        headers=reviewer,
        json={"target_state": "new"},
    )
    assert invalid.status_code == 409, invalid.text

    async with app.state.session_factory() as session:
        events = list(
            (
                await session.scalars(
                    select(AuditEvent).where(AuditEvent.action == "opportunity.transitioned")
                )
            ).all()
        )
    assert len(events) == 2
    assert {event.event_data["to"] for event in events} == {"reviewing", "shortlisted"}


async def test_handoff_draft_contents_and_ready_transition(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids, _source, _run = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    listing = await client.get("/api/v1/opportunities", headers=editor)
    target = next(
        item for item in listing.json()["items"] if item["external_key"] == "yt-deepdives"
    )

    created = await client.post(f"/api/v1/opportunities/{target['id']}/handoff", headers=editor)
    assert created.status_code == 201, created.text
    handoff = created.json()
    assert handoff["state"] == "draft"
    assert handoff["payload_version"] == "opportunity-handoff.v1"
    payload = handoff["payload"]
    assert payload["schema"] == "opportunity-handoff.v1"
    # The package prepares Phase 9 intake only: nothing is sent, ever.
    assert payload["phase_9_status"] == "not_implemented"
    assert payload["outreach_status"] == "not_sent"
    assert payload["opportunity"]["external_key"] == "yt-deepdives"
    assert payload["opportunity"]["contact_route_label"] == ("business email on channel About page")
    assert payload["score"]["version"] == "opportunity-score-v1"
    assert len(payload["score"]["components"]) == 6
    assert payload["evidence"]["provenance"]["creator_handle"]["adapter"] == ("seed_list_adapter")

    fetched = await client.get(f"/api/v1/opportunities/{target['id']}/handoff", headers=editor)
    assert fetched.json()["id"] == handoff["id"]

    ready = await client.post(
        f"/api/v1/opportunities/handoffs/{handoff['id']}/transitions",
        headers=editor,
        json={"target_state": "ready"},
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["state"] == "ready"

    # A ready handoff is not silently rewritten by rebuilding.
    conflict = await client.post(f"/api/v1/opportunities/{target['id']}/handoff", headers=editor)
    assert conflict.status_code == 409, conflict.text


async def test_disabled_search_and_public_site_are_truthful(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids = app_client
    editor = await auth_headers("editor")

    capabilities = await client.get("/api/v1/opportunities/capabilities", headers=editor)
    assert capabilities.status_code == 200, capabilities.text
    modes = {item["kind"]: item["mode"] for item in capabilities.json()}
    assert modes == {
        "seed_list": "real_tested",
        "rss_feed": "disabled",
        "public_site": "disabled",
        "search_provider": "disabled",
    }

    for kind, key in (
        ("search_provider", "phase8-search-src-01"),
        ("public_site", "phase8-public-site-01"),
    ):
        source = await create_source(client, editor, kind=kind, name=f"{kind} source")
        assert source["adapter_mode"] == "disabled"
        run = await queue_and_process(client, app, editor, source_id=source["id"], key=key)
        assert run["state"] == "failed"
        assert run["failure_category"] == "opportunity_unsupported"
        assert run["records_received"] == 0
        assert run["records_created"] == 0

    listing = await client.get("/api/v1/opportunities", headers=editor)
    assert listing.json()["total"] == 0


async def test_generic_jobs_rejects_opportunity_kind(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids = app_client
    owner = await auth_headers("owner")
    response = await client.post(
        "/api/v1/jobs",
        headers=owner,
        json={
            "kind": OPPORTUNITY_JOB_KIND,
            "idempotency_key": "phase8-generic-bypass-01",
            "payload": {},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "reserved_job_kind"


async def test_fake_search_adapter_is_labelled_mocked(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids = app_client
    editor = await auth_headers("editor")
    registry = OpportunityAdapterRegistry(
        [
            SeedListAdapter(),
            DisabledOpportunityAdapter(OpportunitySourceKind.RSS_FEED),
            DisabledOpportunityAdapter(OpportunitySourceKind.PUBLIC_SITE),
            FakeOpportunityAdapter(OpportunitySourceKind.SEARCH_PROVIDER),
        ]
    )
    app.state.opportunity_adapters = registry
    app.state.opportunity_processor.registry = registry
    app.state.worker.opportunity_processor.registry = registry

    source = await create_source(client, editor, kind="search_provider", name="Fake search")
    assert source["adapter_mode"] == "mocked"
    run = await queue_and_process(
        client, app, editor, source_id=source["id"], key="phase8-fake-search-01"
    )
    assert run["state"] == "succeeded"
    assert run["adapter_mode"] == "mocked"
    assert run["records_created"] >= 2
    detail_listing = await client.get("/api/v1/opportunities", headers=editor)
    item = detail_listing.json()["items"][0]
    detail = await client.get(f"/api/v1/opportunities/{item['id']}", headers=editor)
    assert detail.json()["evidence"]["provenance"]["title"]["adapter"] == (
        "fake_opportunity_adapter"
    )


async def test_no_outreach_surface_exists(
    app_client: tuple[Any, Any, dict[str, Any]],
) -> None:
    _client, app, _ids = app_client
    paths = [path for path in app.openapi()["paths"] if "/opportunities" in path]
    assert paths  # the domain exists; the assertion below is about its shape
    forbidden_segments = {"send", "outreach", "email", "dm", "contact", "message"}
    for path in paths:
        segments = {part for part in path.replace("{", "").replace("}", "").split("/") if part}
        assert not (segments & forbidden_segments), path
    for adapter_type in (
        SeedListAdapter,
        RssFeedAdapter,
        DisabledOpportunityAdapter,
        DisabledSearchAdapter,
        FakeOpportunityAdapter,
    ):
        public_methods = {
            name for name in dir(adapter_type) if not name.startswith("_") and name.islower()
        }
        callable_names = {
            name for name in public_methods if callable(getattr(adapter_type, name, None))
        }
        assert callable_names <= {"fetch_opportunities", "close"}, (
            adapter_type.__name__,
            callable_names,
        )
