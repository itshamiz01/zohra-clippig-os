from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from clipos.core.enums import AuditOutcome, Role
from clipos.core.errors import AuthorizationError
from clipos.core.logging import correlation_id_var
from clipos.core.security import Principal
from clipos.models import AuditEvent
from clipos.services.audit import AuditService
from clipos.services.system_control import SystemControlService

pytestmark = pytest.mark.integration


async def _request_approval(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/approvals",
        headers=headers,
        json={
            "action_class": "public_publish",
            "action_type": "publish.youtube.public",
            "resource_type": "rendered_clip",
            "resource_id": "clip-fixture-1",
            "rationale": "A reviewer must verify this consequential public action.",
            "action_payload": {"visibility": "public", "token": "must-not-persist"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_requester_cannot_self_approve(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, _ = app_client
    owner_headers = await auth_headers("owner")
    approval = await _request_approval(client, owner_headers)
    response = await client.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        headers=owner_headers,
        json={"reason": "I should not be allowed to decide my own request."},
    )
    assert response.status_code == 403


async def test_independent_reviewer_can_approve(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, _ = app_client
    approval = await _request_approval(client, await auth_headers("editor"))
    response = await client.post(
        f"/api/v1/approvals/{approval['id']}/approve",
        headers=await auth_headers("reviewer"),
        json={"reason": "Evidence and action scope were independently reviewed."},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"


async def test_approval_is_hidden_from_other_tenant(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, _ = app_client
    await _request_approval(client, await auth_headers("editor"))
    response = await client.get("/api/v1/approvals", headers=await auth_headers("bravo_owner"))
    assert response.status_code == 200
    assert response.json() == []


async def test_audit_redacts_secret_shaped_fields(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> None:
    _, app, ids = app_client
    factory = app.state.session_factory
    token = correlation_id_var.set("audit-test")
    try:
        async with factory() as session:
            AuditService.record(
                session,
                tenant_id=ids["tenant_a"],
                actor_user_id=ids["owner"],
                action="fixture.audit",
                resource_type="fixture",
                resource_id="one",
                outcome=AuditOutcome.SUCCESS,
                event_data={"api_key": "secret-value", "safe": "visible"},
            )
            await session.commit()
            event = await session.scalar(
                select(AuditEvent).where(AuditEvent.action == "fixture.audit")
            )
            assert event is not None
            assert event.event_data["api_key"] == "[REDACTED]"
            assert event.event_data["safe"] == "visible"
    finally:
        correlation_id_var.reset(token)


async def test_only_platform_admin_can_change_global_control(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> None:
    _, app, ids = app_client
    factory = app.state.session_factory
    normal_owner = Principal(
        user_id=ids["bravo_owner"],
        tenant_id=ids["tenant_b"],
        role=Role.OWNER,
        email="owner.bravo@example.com",
        is_platform_admin=False,
    )
    platform_owner = Principal(
        user_id=ids["owner"],
        tenant_id=ids["tenant_a"],
        role=Role.OWNER,
        email="owner.alpha@example.com",
        is_platform_admin=True,
    )
    async with factory() as session:
        with pytest.raises(AuthorizationError):
            await SystemControlService.set_paused(
                session,
                principal=normal_owner,
                paused=False,
                reason="An ordinary tenant owner cannot resume the platform.",
            )
        control = await SystemControlService.set_paused(
            session,
            principal=platform_owner,
            paused=False,
            reason="Platform administrator authorized a controlled local test.",
        )
        assert control.paused is False
