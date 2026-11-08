from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from clipos.config import Settings
from clipos.core.enums import Environment, Role, SalesAssetKind
from clipos.core.errors import ClipOSError
from clipos.core.security import Principal
from clipos.gateways.comms import (
    DisabledCommunicationsAdapter,
    FakeCommsAdapter,
    OutboundMessage,
    UnsupportedCommsError,
)
from clipos.gateways.comms.contracts import CommsChannel
from clipos.gateways.payments import (
    DisabledPaymentsAdapter,
    FakePaymentsAdapter,
    PaymentRequest,
    UnsupportedPaymentsError,
)
from clipos.models import AuditEvent, CrmAccount, CrmPipelineEvent
from clipos.queue import QueueMessage
from clipos.services.crm import CrmService
from clipos.services.opportunities import OPPORTUNITY_JOB_KIND

pytestmark = pytest.mark.integration

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


async def import_seed_records(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    *,
    key: str = "phase9-seed-import-01",
) -> tuple[Any, Any, dict[str, Any]]:
    client, app, ids = app_client
    editor = await auth_headers("editor")
    created = await client.post(
        "/api/v1/opportunities/sources",
        headers=editor,
        json={"kind": "seed_list", "name": "Operator shortlist", "config": {}},
    )
    assert created.status_code == 201, created.text
    source = created.json()
    queued = await client.post(
        f"/api/v1/opportunities/sources/{source['id']}/syncs",
        headers=editor,
        json={"idempotency_key": key, "records": SEED_RECORDS},
    )
    assert queued.status_code == 202, queued.text
    run = queued.json()
    await app.state.worker.process(
        QueueMessage(
            job_id=UUID(run["background_job_id"]),
            tenant_id=UUID(run["tenant_id"]),
            kind=OPPORTUNITY_JOB_KIND,
        )
    )
    finished = await client.get(
        f"/api/v1/opportunities/sync-runs/{run['id']}", headers=editor
    )
    assert finished.json()["state"] == "succeeded", finished.text
    return client, app, ids


async def ready_handoff(
    client: Any, headers: dict[str, str], *, external_key: str = "yt-deepdives"
) -> dict[str, Any]:
    listing = await client.get("/api/v1/opportunities", headers=headers)
    target = next(
        item for item in listing.json()["items"] if item["external_key"] == external_key
    )
    created = await client.post(
        f"/api/v1/opportunities/{target['id']}/handoff", headers=headers
    )
    assert created.status_code == 201, created.text
    handoff = created.json()
    ready = await client.post(
        f"/api/v1/opportunities/handoffs/{handoff['id']}/transitions",
        headers=headers,
        json={"target_state": "ready"},
    )
    assert ready.status_code == 200, ready.text
    return ready.json()


async def intake_account(
    client: Any, headers: dict[str, str], handoff_id: str
) -> dict[str, Any]:
    response = await client.post(f"/api/v1/crm/intake/{handoff_id}", headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def create_manual_account(
    client: Any, headers: dict[str, str], name: str = "Manual Media Co"
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/crm/accounts", headers=headers, json={"name": name}
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["created"] is True
    return payload["account"]


async def test_intake_creates_prospect_and_is_idempotent(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, ids = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    handoff = await ready_handoff(client, editor)

    result = await intake_account(client, editor, handoff["id"])
    assert result["created"] is True
    account = result["account"]
    assert account["stage"] == "prospect"
    assert account["source_handoff_id"] == handoff["id"]
    assert account["source_opportunity_id"] == handoff["opportunity_id"]
    assert account["name"] == "Deep Dive Documentaries"

    detail = await client.get(f"/api/v1/crm/accounts/{account['id']}", headers=editor)
    assert detail.status_code == 200, detail.text
    contact = detail.json()["primary_contact"]
    assert contact["display_name"] == "Deep Dive Documentaries"
    assert contact["contact_route_label"] == "business email on channel About page"
    assert contact["dedup_key"] == "handle:deepdives"
    assert contact["source_opportunity_id"] == handoff["opportunity_id"]
    events = detail.json()["pipeline_events"]
    assert [event["event_kind"] for event in events] == ["intake"]
    assert events[0]["to_stage"] == "prospect"

    # Repeating intake for the same handoff returns the same account and
    # creates nothing new.
    again = await intake_account(client, editor, handoff["id"])
    assert again["created"] is False
    assert again["account"]["id"] == account["id"]
    async with app.state.session_factory() as session:
        accounts = list(
            (
                await session.scalars(
                    select(CrmAccount).where(CrmAccount.tenant_id == ids["tenant_a"])
                )
            ).all()
        )
    assert len(accounts) == 1


async def test_intake_requires_ready_handoff(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    listing = await client.get("/api/v1/opportunities", headers=editor)
    target = next(
        item for item in listing.json()["items"] if item["external_key"] == "yt-buildlab"
    )
    created = await client.post(
        f"/api/v1/opportunities/{target['id']}/handoff", headers=editor
    )
    draft_handoff = created.json()
    assert draft_handoff["state"] == "draft"
    response = await client.post(
        f"/api/v1/crm/intake/{draft_handoff['id']}", headers=editor
    )
    assert response.status_code == 409, response.text


async def test_stage_lifecycle_validation_and_append_only_history(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    account = await create_manual_account(client, editor)
    url = f"/api/v1/crm/accounts/{account['id']}/transitions"

    forward = await client.post(
        url, headers=editor, json={"target_stage": "qualified_lead"}
    )
    assert forward.status_code == 200, forward.text
    assert forward.json()["stage"] == "qualified_lead"

    # Same-stage and backwards-without-reason are rejected.
    same = await client.post(url, headers=editor, json={"target_stage": "qualified_lead"})
    assert same.status_code == 409, same.text
    backwards = await client.post(url, headers=editor, json={"target_stage": "prospect"})
    assert backwards.status_code == 422, backwards.text
    assert backwards.json()["error"]["code"] == "stage_reason_required"
    backwards_ok = await client.post(
        url,
        headers=editor,
        json={"target_stage": "prospect", "reason": "qualification was premature"},
    )
    assert backwards_ok.status_code == 200, backwards_ok.text

    # Terminal side-states require a reason and end the pipeline.
    lost_no_reason = await client.post(url, headers=editor, json={"target_stage": "lost"})
    assert lost_no_reason.status_code == 422, lost_no_reason.text
    lost = await client.post(
        url, headers=editor, json={"target_stage": "lost", "reason": "chose another vendor"}
    )
    assert lost.status_code == 200, lost.text
    after_terminal = await client.post(
        url, headers=editor, json={"target_stage": "conversation"}
    )
    assert after_terminal.status_code == 409, after_terminal.text

    detail = await client.get(f"/api/v1/crm/accounts/{account['id']}", headers=editor)
    events = detail.json()["pipeline_events"]
    kinds = [event["event_kind"] for event in events]
    assert kinds == ["created", "stage_transition", "stage_transition", "stage_transition"]
    transitions = [
        (event["from_stage"], event["to_stage"])
        for event in events
        if event["event_kind"] == "stage_transition"
    ]
    assert transitions == [
        ("prospect", "qualified_lead"),
        ("qualified_lead", "prospect"),
        ("prospect", "lost"),
    ]


async def test_contact_dedup_exact_merge_and_fuzzy_flag(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids = app_client
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")

    first = await client.post(
        "/api/v1/crm/contacts",
        headers=editor,
        json={"display_name": "Jordan Lee", "organisation": "Lee Media"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["created"] is True
    primary = first.json()["contact"]

    # Exact identity merges into the existing record and refreshes fields.
    again = await client.post(
        "/api/v1/crm/contacts",
        headers=editor,
        json={
            "display_name": "Jordan Lee",
            "organisation": "Lee Media",
            "role_title": "Founder",
        },
    )
    assert again.status_code == 201, again.text
    assert again.json()["created"] is False
    assert again.json()["contact"]["id"] == primary["id"]
    assert again.json()["contact"]["role_title"] == "Founder"

    # A near-identical name is flagged, never auto-merged.
    fuzzy = await client.post(
        "/api/v1/crm/contacts",
        headers=editor,
        json={"display_name": "Jordan Lea", "organisation": "Lee Media"},
    )
    assert fuzzy.status_code == 201, fuzzy.text
    assert fuzzy.json()["created"] is True
    duplicate = fuzzy.json()["contact"]
    assert duplicate["possible_duplicate_of_id"] == primary["id"]

    # Editors cannot merge; a reviewer can, and the merge is recorded.
    forbidden = await client.post(
        f"/api/v1/crm/contacts/{primary['id']}/merge",
        headers=editor,
        json={"duplicate_id": duplicate["id"]},
    )
    assert forbidden.status_code == 403, forbidden.text
    merged = await client.post(
        f"/api/v1/crm/contacts/{primary['id']}/merge",
        headers=reviewer,
        json={"duplicate_id": duplicate["id"]},
    )
    assert merged.status_code == 200, merged.text
    gone = await client.get(f"/api/v1/crm/contacts/{duplicate['id']}", headers=editor)
    assert gone.status_code == 404, gone.text
    async with app.state.session_factory() as session:
        events = list((await session.scalars(select(CrmPipelineEvent))).all())
    merge_events = [event for event in events if event.event_kind == "merge"]
    assert len(merge_events) == 1
    assert merge_events[0].contact_id == UUID(primary["id"])


async def test_account_fuzzy_flag_and_reviewed_merge(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")

    primary = await create_manual_account(client, editor, "Acme Clips")
    second = await client.post(
        "/api/v1/crm/accounts", headers=editor, json={"name": "Acme Clip"}
    )
    assert second.status_code == 201, second.text
    assert second.json()["created"] is True
    duplicate = second.json()["account"]
    assert duplicate["possible_duplicate_of_id"] == primary["id"]

    task = await client.post(
        f"/api/v1/crm/accounts/{duplicate['id']}/tasks",
        headers=editor,
        json={"title": "Send the pilot brief"},
    )
    assert task.status_code == 201, task.text

    merged = await client.post(
        f"/api/v1/crm/accounts/{primary['id']}/merge",
        headers=reviewer,
        json={"duplicate_id": duplicate["id"]},
    )
    assert merged.status_code == 200, merged.text
    gone = await client.get(f"/api/v1/crm/accounts/{duplicate['id']}", headers=editor)
    assert gone.status_code == 404, gone.text
    tasks = await client.get(f"/api/v1/crm/accounts/{primary['id']}/tasks", headers=editor)
    assert [item["title"] for item in tasks.json()["items"]] == ["Send the pilot brief"]
    detail = await client.get(f"/api/v1/crm/accounts/{primary['id']}", headers=editor)
    assert "merge" in [event["event_kind"] for event in detail.json()["pipeline_events"]]


async def test_suppression_blocks_draft_creation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    handoff = await ready_handoff(client, editor)
    account = (await intake_account(client, editor, handoff["id"]))["account"]
    contact_id = account["primary_contact_id"]

    forbidden = await client.post(
        f"/api/v1/crm/contacts/{contact_id}/suppress",
        headers=editor,
        json={"reason": "opted out"},
    )
    assert forbidden.status_code == 403, forbidden.text
    suppressed = await client.post(
        f"/api/v1/crm/contacts/{contact_id}/suppress",
        headers=reviewer,
        json={"reason": "opted out by email on 2026-11-01"},
    )
    assert suppressed.status_code == 200, suppressed.text
    assert suppressed.json()["suppressed"] is True

    blocked = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": account["id"], "kind": "outreach_message"},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "contact_suppressed"

    async with app.state.session_factory() as session:
        events = list(
            (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == "crm.draft_blocked_suppressed"
                    )
                )
            ).all()
        )
    assert len(events) == 1
    assert events[0].outcome.value == "denied"

    lifted = await client.post(
        f"/api/v1/crm/contacts/{contact_id}/unsuppress", headers=reviewer, json={}
    )
    assert lifted.status_code == 200, lifted.text
    draft = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": account["id"], "kind": "outreach_message"},
    )
    assert draft.status_code == 201, draft.text


async def test_draft_requires_evidence_citations(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, ids = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    handoff = await ready_handoff(client, editor)
    account = (await intake_account(client, editor, handoff["id"]))["account"]

    created = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": account["id"], "kind": "outreach_message"},
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["status"] == "draft"
    assert draft["created_by"] == "system"
    assert draft["template_version"] == "sales-draft-v1"
    citation_types = [item["type"] for item in draft["evidence"]["citations"]]
    assert "opportunity" in citation_types
    assert "opportunity_handoff" in citation_types
    assert "documentary" in draft["body"]
    assert "Evidence cited" in draft["body"]

    # A manual account has no opportunity evidence: outreach is refused,
    # while a follow-up may cite its pipeline history.
    manual = await create_manual_account(client, editor)
    refused = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": manual["id"], "kind": "outreach_message"},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "insufficient_evidence"
    follow_up = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": manual["id"], "kind": "follow_up"},
    )
    assert follow_up.status_code == 201, follow_up.text
    follow_types = [
        item["type"] for item in follow_up.json()["evidence"]["citations"]
    ]
    assert follow_types == ["pipeline_event"]

    # An account with no stored records at all has zero citations: the
    # service refuses even a follow-up.
    async with app.state.session_factory() as session:
        bare = CrmAccount(
            tenant_id=ids["tenant_a"],
            name="Bare Records Ltd",
            dedup_key="name:bare records ltd",
            created_by_user_id=ids["editor"],
        )
        session.add(bare)
        await session.commit()
        principal = Principal(
            user_id=ids["editor"],
            tenant_id=ids["tenant_a"],
            role=Role.EDITOR,
            email="editor.alpha@example.com",
        )
        with pytest.raises(ClipOSError) as excinfo:
            await CrmService.create_draft(
                session,
                principal=principal,
                account_id=bare.id,
                kind=SalesAssetKind.FOLLOW_UP,
                contact_id=None,
                title=None,
                body=None,
            )
    assert excinfo.value.code == "insufficient_evidence"


async def test_draft_review_workflow_and_approval_grants_no_sending(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ids = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    handoff = await ready_handoff(client, editor)
    account = (await intake_account(client, editor, handoff["id"]))["account"]
    created = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": account["id"], "kind": "outreach_message"},
    )
    draft = created.json()
    review_url = f"/api/v1/crm/drafts/{draft['id']}/review"

    # Approval straight from draft skips review and is rejected; a
    # "sent" state does not exist at all.
    direct = await client.post(
        review_url, headers=reviewer, json={"target_state": "approved"}
    )
    assert direct.status_code == 409, direct.text
    sent = await client.post(review_url, headers=reviewer, json={"target_state": "sent"})
    assert sent.status_code == 422, sent.text

    submitted = await client.post(
        review_url, headers=editor, json={"target_state": "in_review"}
    )
    assert submitted.status_code == 200, submitted.text
    editor_approve = await client.post(
        review_url, headers=editor, json={"target_state": "approved"}
    )
    assert editor_approve.status_code == 403, editor_approve.text
    approved = await client.post(
        review_url,
        headers=reviewer,
        json={"target_state": "approved", "note": "Accurate and cited"},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["reviewed_by_user_id"] is not None
    assert body["review_note"] == "Accurate and cited"

    # Approval granted nothing external: the communications adapter in
    # this app is disabled and has no deliveries, and capabilities still
    # report both Phase 9 gateways as disabled.
    adapter = app.state.comms_adapters.get()
    assert adapter.mode.value == "disabled"
    capabilities = await client.get("/api/v1/crm/capabilities", headers=editor)
    modes = {item["domain"]: item["mode"] for item in capabilities.json()}
    assert modes == {"communications": "disabled", "payments": "disabled"}


async def test_revenue_summary_keeps_kinds_separate(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    account = await create_manual_account(client, editor)
    base = f"/api/v1/crm/accounts/{account['id']}/revenue"
    records = [
        ("quote", 100_000, "issued"),
        ("quote", 50_000, "draft"),
        ("quote", 70_000, "void"),
        ("contract", 80_000, "issued"),
        ("invoice", 80_000, "issued"),
        ("invoice", 20_000, "overdue"),
        ("payment", 60_000, "paid"),
        ("payment", 99_900, "issued"),
        ("refund", 5_000, "paid"),
        ("recurring", 10_000, "issued"),
    ]
    for kind, amount, record_status in records:
        response = await client.post(
            base,
            headers=editor,
            json={
                "kind": kind,
                "amount_minor_units": amount,
                "currency": "usd",
                "status": record_status,
                "occurred_at": "2026-11-01T10:00:00Z",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["currency"] == "USD"
    eur = await client.post(
        base,
        headers=editor,
        json={
            "kind": "payment",
            "amount_minor_units": 700,
            "currency": "EUR",
            "status": "paid",
            "occurred_at": "2026-11-02T10:00:00Z",
        },
    )
    assert eur.status_code == 201, eur.text

    summary = await client.get(
        f"/api/v1/crm/accounts/{account['id']}/revenue-summary", headers=editor
    )
    assert summary.status_code == 200, summary.text
    payload = summary.json()
    by_currency = {item["currency"]: item for item in payload["currencies"]}
    usd = by_currency["USD"]
    assert usd["quoted"] == 100_000
    assert usd["contracted"] == 80_000
    assert usd["invoiced"] == 100_000
    # Only paid payments are collected: the 100k quote and the issued
    # 99_900 payment never leak into collected.
    assert usd["collected"] == 60_000
    assert usd["refunded"] == 5_000
    assert usd["recurring"] == 10_000
    assert by_currency["EUR"]["collected"] == 700
    assert "quote" in payload["definitions"]["collected"]


async def test_tenant_isolation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids = await import_seed_records(app_client, auth_headers)
    editor = await auth_headers("editor")
    bravo = await auth_headers("bravo_owner")
    handoff = await ready_handoff(client, editor)
    account = (await intake_account(client, editor, handoff["id"]))["account"]
    draft = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": account["id"], "kind": "follow_up"},
    )
    assert draft.status_code == 201, draft.text

    assert (
        await client.get(f"/api/v1/crm/accounts/{account['id']}", headers=bravo)
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/crm/contacts/{account['primary_contact_id']}", headers=bravo
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/crm/drafts/{draft.json()['id']}", headers=bravo)
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/crm/intake/{handoff['id']}", headers=bravo)
    ).status_code == 404
    listing = await client.get("/api/v1/crm/accounts", headers=bravo)
    assert listing.json()["total"] == 0


async def test_onboarding_flow(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, _ids = app_client
    editor = await auth_headers("editor")
    account = await create_manual_account(client, editor)

    too_early = await client.post(
        f"/api/v1/crm/accounts/{account['id']}/onboarding", headers=editor
    )
    assert too_early.status_code == 409, too_early.text

    transitions = f"/api/v1/crm/accounts/{account['id']}/transitions"
    for stage in ("conversation", "discovery_call", "proposal", "contract_review", "client"):
        moved = await client.post(
            transitions, headers=editor, json={"target_stage": stage}
        )
        assert moved.status_code == 200, moved.text

    created = await client.post(
        f"/api/v1/crm/accounts/{account['id']}/onboarding", headers=editor
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["status"] == "not_started"
    items = record["checklist"]["items"]
    assert len(items) == 5
    assert all(item["done"] is False for item in items)
    duplicate = await client.post(
        f"/api/v1/crm/accounts/{account['id']}/onboarding", headers=editor
    )
    assert duplicate.status_code == 409, duplicate.text

    patch_url = f"/api/v1/crm/onboarding/{record['id']}"
    unknown = await client.patch(
        patch_url, headers=editor, json={"item_states": {"nope": True}}
    )
    assert unknown.status_code == 422, unknown.text
    premature = await client.patch(
        patch_url, headers=editor, json={"status": "completed"}
    )
    assert premature.status_code == 409, premature.text

    all_keys = {item["key"]: True for item in items}
    updated = await client.patch(
        patch_url,
        headers=editor,
        json={"item_states": all_keys, "milestone_label": "Kickoff held"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["milestones"][0]["label"] == "Kickoff held"
    started = await client.patch(
        patch_url, headers=editor, json={"status": "in_progress"}
    )
    assert started.status_code == 200, started.text
    completed = await client.patch(
        patch_url, headers=editor, json={"status": "completed"}
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"

    checklist_draft = await client.post(
        "/api/v1/crm/drafts",
        headers=editor,
        json={"account_id": account["id"], "kind": "onboarding_checklist"},
    )
    assert checklist_draft.status_code == 201, checklist_draft.text
    assert "Onboarding checklist" in checklist_draft.json()["body"]


async def test_tasks_crud_and_assignee_validation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, _app, ids = app_client
    editor = await auth_headers("editor")
    account = await create_manual_account(client, editor)

    created = await client.post(
        f"/api/v1/crm/accounts/{account['id']}/tasks",
        headers=editor,
        json={"title": "Prepare discovery brief", "assignee_user_id": str(ids["editor"])},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "open"

    outsider = await client.post(
        f"/api/v1/crm/accounts/{account['id']}/tasks",
        headers=editor,
        json={"title": "Bad assignee", "assignee_user_id": str(ids["bravo_owner"])},
    )
    assert outsider.status_code == 422, outsider.text
    assert outsider.json()["error"]["code"] == "not_a_tenant_member"

    done = await client.patch(
        f"/api/v1/crm/tasks/{task['id']}", headers=editor, json={"status": "done"}
    )
    assert done.status_code == 200, done.text
    cancelled = await client.patch(
        f"/api/v1/crm/tasks/{task['id']}", headers=editor, json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200, cancelled.text
    reopen = await client.patch(
        f"/api/v1/crm/tasks/{task['id']}", headers=editor, json={"status": "open"}
    )
    assert reopen.status_code == 409, reopen.text


async def test_gateways_disabled_mocked_and_config_refusal() -> None:
    disabled_comms = DisabledCommunicationsAdapter()
    assert disabled_comms.mode.value == "disabled"
    with pytest.raises(UnsupportedCommsError) as comms_exc:
        await disabled_comms.deliver(
            message=OutboundMessage(
                channel=CommsChannel.EMAIL,
                recipient_label="business email on channel About page",
                subject="Hello",
                body="Draft only",
            )
        )
    assert comms_exc.value.category == "comms_unsupported"

    fake_comms = FakeCommsAdapter()
    receipt = await fake_comms.deliver(
        message=OutboundMessage(
            channel=CommsChannel.EMAIL,
            recipient_label="business email on channel About page",
            subject="Hello",
            body="Draft only",
        )
    )
    assert fake_comms.mode.value == "mocked"
    assert len(fake_comms.delivered) == 1
    # The fake records in memory and delivers nothing.
    assert receipt.delivered is False
    assert receipt.provider_reference == "fake-comms-1"

    disabled_payments = DisabledPaymentsAdapter()
    with pytest.raises(UnsupportedPaymentsError) as payments_exc:
        await disabled_payments.process_payment(
            request=PaymentRequest(amount_minor_units=100, currency="USD")
        )
    assert payments_exc.value.category == "payments_unsupported"

    fake_payments = FakePaymentsAdapter()
    payment_receipt = await fake_payments.process_payment(
        request=PaymentRequest(amount_minor_units=100, currency="USD")
    )
    assert fake_payments.mode.value == "mocked"
    assert len(fake_payments.requests) == 1
    assert payment_receipt.processed is False

    # Naming a real provider is a configuration error that names the
    # provider decision the user still owes.
    with pytest.raises(ValidationError) as comms_config:
        Settings(_env_file=None, comms_provider="resend")
    assert "email provider decision" in str(comms_config.value)
    with pytest.raises(ValidationError) as payments_config:
        Settings(_env_file=None, payments_provider="stripe")
    assert "payment provider decision" in str(payments_config.value)
    with pytest.raises(ValidationError) as fake_staging:
        Settings(
            _env_file=None,
            environment=Environment.STAGING,
            comms_provider="fake",
        )
    assert "forbidden in staging/production" in str(fake_staging.value)


async def test_no_send_surface_in_crm(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    _client, app, _ids = app_client
    paths = [path for path in app.openapi()["paths"] if "/crm" in path]
    assert paths, "expected CRM routes to exist"
    for path in paths:
        for term in ("send", "deliver", "transmit", "charge", "checkout"):
            assert term not in path, f"send-like surface in CRM route {path}"
    service_surfaces = [
        name for name in dir(CrmService) if not name.startswith("_")
    ]
    for name in service_surfaces:
        for term in ("send", "deliver", "transmit", "charge"):
            assert term not in name.lower(), f"send-like CRM service method {name}"
