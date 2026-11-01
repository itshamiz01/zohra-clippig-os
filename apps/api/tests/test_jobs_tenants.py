from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from clipos.core.enums import JobState
from clipos.core.errors import ConflictError
from clipos.services.jobs import JobService

pytestmark = pytest.mark.integration


async def _create_job(client: AsyncClient, headers: dict[str, str], key: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "kind": "system.diagnostic",
            "idempotency_key": key,
            "payload": {"check": "foundation"},
            "max_attempts": 2,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_job_creation_is_idempotent(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, _ = app_client
    headers = await auth_headers("owner")
    first = await _create_job(client, headers, "diagnostic-0001")
    second = await _create_job(client, headers, "diagnostic-0001")
    assert first["id"] == second["id"]
    assert first["state"] == "queued"


async def test_viewer_cannot_create_internal_job(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, _ = app_client
    response = await client.post(
        "/api/v1/jobs",
        headers=await auth_headers("viewer"),
        json={
            "kind": "system.diagnostic",
            "idempotency_key": "diagnostic-viewer",
            "payload": {},
        },
    )
    assert response.status_code == 403


async def test_tenant_cannot_read_another_tenants_job(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, _ = app_client
    alpha_job = await _create_job(client, await auth_headers("owner"), "diagnostic-cross-tenant")
    response = await client.get(
        f"/api/v1/jobs/{alpha_job['id']}", headers=await auth_headers("bravo_owner")
    )
    # Not-found avoids revealing whether the foreign-tenant identifier exists.
    assert response.status_code == 404


async def test_job_transition_rules_and_retry_budget(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, app, ids = app_client
    job_data = await _create_job(client, await auth_headers("owner"), "diagnostic-transitions")
    job_id = UUID(job_data["id"])
    factory = app.state.session_factory
    async with factory() as session:
        job = await JobService.transition(
            session,
            tenant_id=ids["tenant_a"],
            job_id=job_id,
            target=JobState.RUNNING,
            actor_user_id=ids["owner"],
        )
        assert job.attempts == 1
        job = await JobService.transition(
            session,
            tenant_id=ids["tenant_a"],
            job_id=job.id,
            target=JobState.FAILED,
            actor_user_id=ids["owner"],
            failure_category="fixture_failure",
            failure_message="expected test failure",
        )
        assert job.finished_at is not None
        retried = await JobService.retry(
            session,
            tenant_id=ids["tenant_a"],
            job_id=job.id,
            actor_user_id=ids["owner"],
        )
        assert retried.state == JobState.QUEUED
        running = await JobService.transition(
            session,
            tenant_id=ids["tenant_a"],
            job_id=job.id,
            target=JobState.RUNNING,
            actor_user_id=ids["owner"],
        )
        assert running.attempts == 2
        failed = await JobService.transition(
            session,
            tenant_id=ids["tenant_a"],
            job_id=job.id,
            target=JobState.FAILED,
            actor_user_id=ids["owner"],
        )
        with pytest.raises(ConflictError, match="retry budget exhausted"):
            await JobService.retry(
                session,
                tenant_id=ids["tenant_a"],
                job_id=failed.id,
                actor_user_id=ids["owner"],
            )


async def test_unknown_job_id_is_not_found(
    app_client: tuple[AsyncClient, Any, dict[str, Any]], auth_headers
) -> None:
    client, _, _ = app_client
    response = await client.get(f"/api/v1/jobs/{uuid4()}", headers=await auth_headers("owner"))
    assert response.status_code == 404
