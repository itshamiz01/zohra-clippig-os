from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration


async def test_liveness_and_readiness(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> None:
    client, _, _ = app_client
    live = await client.get("/healthz", headers={"X-Correlation-ID": "test-correlation"})
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert live.headers["x-correlation-id"] == "test-correlation"
    assert live.headers["x-content-type-options"] == "nosniff"

    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["components"]["database"]["status"] == "ok"


async def test_login_and_me(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, ids = app_client
    headers = await auth_headers("owner")
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(ids["tenant_a"])
    assert response.json()["role"] == "owner"
    assert response.json()["is_platform_admin"] is True


async def test_invalid_login_has_consistent_safe_error(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> None:
    client, _, _ = app_client
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "owner.alpha@example.com",
            "password": "wrong",
            "tenant_slug": "alpha",
        },
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert "correlation_id" in response.json()["error"]


async def test_login_does_not_cross_tenant(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> None:
    client, _, _ = app_client
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "owner.alpha@example.com",
            "password": TEST_PASSWORD,
            "tenant_slug": "bravo",
        },
    )
    assert response.status_code == 401


async def test_protected_route_requires_bearer_token(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> None:
    client, _, _ = app_client
    response = await client.get("/api/v1/jobs")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_framework_errors_use_consistent_envelope(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> None:
    client, _, _ = app_client
    response = await client.get("/route-that-does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"
    assert response.json()["error"]["correlation_id"]
