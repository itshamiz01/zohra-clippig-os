from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from sqlalchemy import select

from clipos.core.enums import PublishingProvider
from clipos.gateways.publishing import DisabledPublishingAdapter, PublishingAdapterRegistry
from clipos.core.errors import ClipOSError
from clipos.models import PublishingConnection
from clipos.queue import QueueMessage
from clipos.services.publishing import PUBLISH_JOB_KIND, PublishingService
from tests.publishing_fakes import FakeYouTubeAdapter
from tests.test_phase5_quality_rights import setup_valid_gate

pytestmark = pytest.mark.integration


def install_fake(app: Any) -> FakeYouTubeAdapter:
    fake = FakeYouTubeAdapter()
    registry = PublishingAdapterRegistry(
        [
            fake,
            DisabledPublishingAdapter(PublishingProvider.META),
            DisabledPublishingAdapter(PublishingProvider.TIKTOK),
            DisabledPublishingAdapter(PublishingProvider.LINKEDIN),
        ]
    )
    app.state.publishing_adapters = registry
    app.state.publish_processor.registry = registry
    app.state.worker.publish_processor.registry = registry
    return fake


async def connect_youtube(
    client: Any,
    app: Any,
    owner: dict[str, str],
    *,
    code: str = "good-code",
) -> Any:
    start = await client.post("/api/v1/publishing/oauth/youtube/start", headers=owner)
    assert start.status_code == 201, start.text
    body = start.json()
    query = parse_qs(urlsplit(body["authorization_url"]).query)
    state = query["state"][0]
    callback = await client.get(
        "/api/v1/publishing/oauth/youtube/callback",
        params={"state": state, "code": code},
    )
    return callback


async def approved_gate(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    *,
    key: str,
) -> tuple[Any, Any, dict[str, Any], dict[str, str], dict[str, str], dict[str, str], dict[str, UUID], str]:
    result = await setup_valid_gate(app_client, auth_headers, media_files, key=key)
    client, app, ids, owner, editor, reviewer, render_ids, evaluation_id = result
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer
    )
    assert detail.status_code == 200, detail.text
    acknowledged = [
        item["id"]
        for item in detail.json()["checks"]
        if item["status"] in {"needs_review", "unavailable"}
        or item["confidence"] < 0.7
    ]
    decision = await client.post(
        f"/api/v1/quality-evaluations/{evaluation_id}/decisions",
        headers=reviewer,
        json={
            "action": "approve",
            "reason": (
                "I reviewed the current render, source context, captions, technical evidence, "
                "and scoped rights for this controlled publication test."
            ),
            "acknowledged_check_ids": acknowledged,
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["evaluation"]["outcome"] == "approved"
    return client, app, ids, owner, editor, reviewer, render_ids, evaluation_id


def release_payload(
    *,
    connection_id: str,
    render_id: UUID,
    evaluation_id: str,
    key: str,
    visibility: str = "private",
    title: str = "A controlled Phase 6 upload",
    public_approval_id: str | None = None,
) -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "render_job_id": str(render_id),
        "quality_evaluation_id": evaluation_id,
        "idempotency_key": key,
        "visibility": visibility,
        "title": title,
        "description": "Evidence-gated fixture upload. No virality or revenue claim.",
        "tags": ["clipos", "phase6"],
        "made_for_kids": False,
        "category_id": "22",
        "public_approval_id": public_approval_id,
    }


async def create_ready_release(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    *,
    key: str,
) -> tuple[Any, Any, dict[str, Any], FakeYouTubeAdapter, dict[str, str], dict[str, str], dict[str, str], dict[str, UUID], str, dict[str, Any]]:
    client, app, ids, owner, editor, reviewer, render_ids, evaluation_id = await approved_gate(
        app_client, auth_headers, media_files, key=key
    )
    fake = install_fake(app)
    connection = await connect_youtube(client, app, owner)
    assert connection.status_code == 200, connection.text
    connection_id = connection.json()["connection"]["id"]
    payload = release_payload(
        connection_id=connection_id,
        render_id=render_ids["render_id"],
        evaluation_id=evaluation_id,
        key=f"{key}-publication",
    )
    created = await client.post(
        "/api/v1/publishing/publications", headers=reviewer, json=payload
    )
    assert created.status_code == 202, created.text
    return (
        client,
        app,
        ids,
        fake,
        owner,
        editor,
        reviewer,
        render_ids,
        evaluation_id,
        created.json(),
    )


async def process_publication(app: Any, publication: dict[str, Any]) -> None:
    await app.state.publish_processor.process_job(
        QueueMessage(
            job_id=UUID(publication["background_job_id"]),
            tenant_id=UUID(publication["tenant_id"]),
            kind=PUBLISH_JOB_KIND,
        )
    )


@pytest.mark.asyncio
async def test_capability_matrix_and_oauth_state_scope_controls(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, app, _ids = app_client
    owner = await auth_headers("owner")
    fake = install_fake(app)
    capabilities = await client.get("/api/v1/publishing/capabilities", headers=owner)
    assert capabilities.status_code == 200
    rows = {item["provider"]: item for item in capabilities.json()}
    assert rows["youtube"]["real_upload_verified"] is False
    assert rows["meta"]["mode"] == "disabled"
    assert rows["tiktok"]["features"]["resumable_upload"] is False

    mismatch = await client.get(
        "/api/v1/publishing/oauth/youtube/callback",
        params={"state": "wrong-state-value-long-enough", "code": "x"},
    )
    assert mismatch.status_code == 400
    missing_scope = await connect_youtube(client, app, owner, code="missing-scope")
    assert missing_scope.status_code == 409
    assert missing_scope.json()["error"]["code"] == "missing_scope"

    denied_start = await client.post(
        "/api/v1/publishing/oauth/youtube/start", headers=owner
    )
    state = parse_qs(urlsplit(denied_start.json()["authorization_url"]).query)["state"][0]
    denied = await client.get(
        "/api/v1/publishing/oauth/youtube/callback",
        params={"state": state, "error": "access_denied"},
    )
    assert denied.status_code == 200
    assert denied.json()["connected"] is False
    assert fake.initiation_count == 0

    unsupported = await client.post(
        "/api/v1/publishing/oauth/meta/start", headers=owner
    )
    assert unsupported.status_code == 409


@pytest.mark.asyncio
async def test_private_first_upload_succeeds_with_progress_and_analytics_handoff(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        _ids,
        fake,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        _evaluation_id,
        publication,
    ) = await create_ready_release(
        app_client, auth_headers, media_files, key="phase6-success"
    )
    assert publication["visibility"] == "private"
    await process_publication(app, publication)
    detail = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=reviewer
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["state"] == "succeeded"
    assert body["progress_percent"] == 100
    assert body["provider_publication_id"] == "video-phase6"
    assert body["provider_url"] == "https://youtu.be/video-phase6"
    assert body["analytics_handoff"]["phase_7_status"] == "implemented"
    assert fake.last_metadata is not None
    assert fake.last_metadata.visibility.value == "private"
    events = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}/events", headers=reviewer
    )
    assert events.status_code == 200
    sequences = [item["sequence"] for item in events.json()]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert {item["kind"] for item in events.json()} >= {
        "created",
        "upload_progress",
        "provider_processing",
        "succeeded",
    }


@pytest.mark.asyncio
async def test_duplicate_prevention_and_tenant_isolation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        _ids,
        fake,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        _evaluation_id,
        publication,
    ) = await create_ready_release(
        app_client, auth_headers, media_files, key="phase6-idempotency"
    )
    payload = release_payload(
        connection_id=publication["connection_id"],
        render_id=UUID(publication["render_job_id"]),
        evaluation_id=publication["quality_evaluation_id"],
        key=publication["idempotency_key"],
    )
    replay = await client.post(
        "/api/v1/publishing/publications", headers=reviewer, json=payload
    )
    assert replay.status_code == 200, replay.text
    assert replay.headers["X-ClipOS-Idempotent-Replay"] == "true"
    assert replay.json()["id"] == publication["id"]
    changed = {**payload, "title": "Changed title must conflict"}
    conflict = await client.post(
        "/api/v1/publishing/publications", headers=reviewer, json=changed
    )
    assert conflict.status_code == 409
    bravo = await auth_headers("bravo_owner")
    hidden = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=bravo
    )
    assert hidden.status_code == 404
    await process_publication(app, publication)
    assert fake.initiation_count == 1


@pytest.mark.asyncio
async def test_expired_token_refresh_and_refresh_failure_require_reconnect(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        _ids,
        fake,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        _evaluation_id,
        publication,
    ) = await create_ready_release(
        app_client, auth_headers, media_files, key="phase6-refresh"
    )
    async with app.state.session_factory() as session:
        connection = await session.get(
            PublishingConnection, UUID(publication["connection_id"])
        )
        assert connection is not None
        connection.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    await process_publication(app, publication)
    assert fake.refresh_count == 1
    succeeded = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=reviewer
    )
    assert succeeded.json()["state"] == "succeeded"

    fake.fail_refresh = True
    async with app.state.session_factory() as session:
        connection = await session.get(
            PublishingConnection, UUID(publication["connection_id"])
        )
        assert connection is not None
        connection.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
    async with app.state.session_factory() as session:
        with pytest.raises(ClipOSError) as exc_info:
            await PublishingService.usable_access_grant(
                session,
                tenant_id=UUID(publication["tenant_id"]),
                connection_id=UUID(publication["connection_id"]),
                settings=app.state.settings,
                registry=app.state.publishing_adapters,
            )
        assert exc_info.value.code == "token_refresh_failed"
    connection_detail = await client.get(
        f"/api/v1/publishing/connections/{publication['connection_id']}",
        headers=reviewer,
    )
    assert connection_detail.json()["state"] == "reconnect_required"


@pytest.mark.asyncio
async def test_rate_limit_is_retryable_without_duplicate_submission(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        _ids,
        fake,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        _evaluation_id,
        publication,
    ) = await create_ready_release(
        app_client, auth_headers, media_files, key="phase6-rate-limit"
    )
    fake.rate_limit_once = True
    await process_publication(app, publication)
    failed = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=reviewer
    )
    assert failed.json()["state"] == "failed"
    assert failed.json()["retryable"] is True
    assert failed.json()["retry_after_seconds"] == 30
    retried = await client.post(
        f"/api/v1/publishing/publications/{publication['id']}/retry", headers=reviewer
    )
    assert retried.status_code == 202
    await process_publication(app, retried.json())
    done = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=reviewer
    )
    assert done.json()["state"] == "succeeded"
    assert fake.initiation_count == 1


@pytest.mark.asyncio
async def test_uncertain_timeout_reconciles_before_retry(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        _ids,
        fake,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        _evaluation_id,
        publication,
    ) = await create_ready_release(
        app_client, auth_headers, media_files, key="phase6-uncertain"
    )
    fake.uncertain_once = True
    await process_publication(app, publication)
    uncertain = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=reviewer
    )
    assert uncertain.json()["state"] == "needs_reconciliation"
    assert uncertain.json()["uncertain_remote_result"] is True
    assert fake.initiation_count == 1
    queued = await client.post(
        f"/api/v1/publishing/publications/{publication['id']}/retry", headers=reviewer
    )
    assert queued.status_code == 202
    await process_publication(app, queued.json())
    reconciled = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=reviewer
    )
    assert reconciled.json()["state"] == "succeeded"
    assert reconciled.json()["provider_publication_id"] == "video-phase6"
    assert fake.initiation_count == 1


@pytest.mark.asyncio
async def test_fresh_gate_revocation_and_generic_job_bypass_are_blocked(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        _ids,
        _fake,
        owner,
        _editor,
        reviewer,
        _render_ids,
        evaluation_id,
        publication,
    ) = await create_ready_release(
        app_client, auth_headers, media_files, key="phase6-gate-revocation"
    )
    evaluation = await client.get(
        f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer
    )
    rights_id = evaluation.json()["rights"]["id"]
    revoked = await client.post(
        f"/api/v1/rights-evidence/{rights_id}/revoke",
        headers=owner,
        json={"reason": "Permission was withdrawn before the external upload checkpoint."},
    )
    assert revoked.status_code == 200
    await process_publication(app, publication)
    denied = await client.get(
        f"/api/v1/publishing/publications/{publication['id']}", headers=reviewer
    )
    assert denied.json()["state"] == "failed"
    assert denied.json()["failure_category"] == "mandatory_gate_denied"

    bypass = await client.post(
        "/api/v1/jobs",
        headers=owner,
        json={
            "kind": PUBLISH_JOB_KIND,
            "idempotency_key": "phase6-bypass",
            "payload": {},
            "max_attempts": 1,
        },
    )
    assert bypass.status_code == 422


@pytest.mark.asyncio
async def test_public_visibility_requires_exact_independent_approval(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ids, owner, editor, reviewer, render_ids, evaluation_id = await approved_gate(
        app_client, auth_headers, media_files, key="phase6-public"
    )
    install_fake(app)
    app.state.settings.publishing_public_enabled = True
    connection = await connect_youtube(client, app, owner)
    assert connection.status_code == 200
    payload = release_payload(
        connection_id=connection.json()["connection"]["id"],
        render_id=render_ids["render_id"],
        evaluation_id=evaluation_id,
        key="phase6-public-release",
        visibility="public",
    )
    no_approval = await client.post(
        "/api/v1/publishing/publications", headers=reviewer, json=payload
    )
    assert no_approval.status_code == 409
    requested = await client.post(
        "/api/v1/publishing/public-approvals",
        headers=editor,
        json={
            "release": payload,
            "rationale": (
                "Public visibility is intentionally requested after reviewing this exact release."
            ),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert requested.status_code == 201, requested.text
    approval_id = requested.json()["approval"]["id"]
    approved = await client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        headers=reviewer,
        json={"reason": "I independently approve this exact public release payload."},
    )
    assert approved.status_code == 200, approved.text
    changed = {
        **payload,
        "title": "Changed after approval",
        "public_approval_id": approval_id,
    }
    mismatch = await client.post(
        "/api/v1/publishing/publications", headers=reviewer, json=changed
    )
    assert mismatch.status_code == 409
    exact = {**payload, "public_approval_id": approval_id}
    created = await client.post(
        "/api/v1/publishing/publications", headers=reviewer, json=exact
    )
    assert created.status_code == 202, created.text
    assert created.json()["visibility"] == "public"
    assert created.json()["public_approval_id"] == approval_id
