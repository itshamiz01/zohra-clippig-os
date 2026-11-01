from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from clipos.core.enums import (
    EvidenceSource,
    GateOutcome,
    JobState,
    QualityCheckKind,
    QualityCheckStatus,
    QualitySeverity,
    RenderState,
    Role,
)
from clipos.core.errors import AuthorizationError
from clipos.core.security import Principal
from clipos.models import (
    BackgroundJob,
    ClipCandidate,
    GateVerification,
    RenderJob,
    RenderOutput,
    RenderPlan,
    RightsEvidence,
    Transcript,
    TranscriptSegment,
)
from clipos.quality_schemas import QualityReviewRequest
from clipos.services.quality import (
    CheckDraft,
    QualityGateService,
    RightsAssessment,
)
from tests.test_phase4_rendering import (
    create_plan,
    resume_processing,
    seed_approved_candidate,
)

pytestmark = pytest.mark.integration


async def seed_completed_render(
    client: Any,
    app: Any,
    ids: dict[str, Any],
    media_path: Path,
    owner_headers: dict[str, str],
    editor_headers: dict[str, str],
    *,
    key: str,
    tenant: str = "tenant_a",
    submitter: str = "editor",
) -> dict[str, UUID]:
    if tenant == "tenant_a":
        await resume_processing(client, owner_headers)
    candidate_id = await seed_approved_candidate(
        app,
        ids,
        media_path,
        tenant=tenant,
        submitter=submitter,
    )
    created = await create_plan(client, editor_headers, candidate_id, key=key)
    body = created.json()
    render_id = UUID(body["render"]["id"])
    plan_id = UUID(body["plan"]["id"])
    output_key = f"renders/{render_id}/phase5-validated.mp4"
    source_bytes = await asyncio.to_thread(media_path.read_bytes)
    output_bytes = b"phase5-validated-fixture\0" + source_bytes
    output_info = await app.state.storage.put(
        ids[tenant], output_key, output_bytes, content_type="video/mp4"
    )
    sidecar_key = f"renders/{render_id}/phase5-captions.srt"
    sidecar_info = await app.state.storage.put(
        ids[tenant],
        sidecar_key,
        b"1\n00:00:00,000 --> 00:00:01,000\nHello cafe captions stay readable\n",
        content_type="application/x-subrip; charset=utf-8",
    )
    async with app.state.session_factory() as session:
        render = await session.scalar(select(RenderJob).where(RenderJob.id == render_id))
        plan = await session.scalar(select(RenderPlan).where(RenderPlan.id == plan_id))
        assert render is not None and plan is not None
        background = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == render.background_job_id)
        )
        assert background is not None
        render.state = RenderState.SUCCEEDED
        render.stage = "succeeded"
        render.progress_percent = 100
        render.attempt_count = 1
        render.started_at = datetime.now(UTC) - timedelta(seconds=2)
        render.finished_at = datetime.now(UTC)
        background.state = JobState.SUCCEEDED
        background.finished_at = datetime.now(UTC)
        output = RenderOutput(
            tenant_id=ids[tenant],
            render_job_id=render.id,
            render_plan_id=plan.id,
            attempt_number=1,
            is_current=True,
            storage_key=output_key,
            checksum_sha256=output_info.checksum_sha256,
            size_bytes=output_info.size,
            content_type="video/mp4",
            sidecar_storage_key=sidecar_key,
            sidecar_checksum_sha256=sidecar_info.checksum_sha256,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            duration_seconds=plan.duration_ms / 1000,
            video_codec="h264",
            audio_codec="aac",
            width=plan.width,
            height=plan.height,
            frame_rate=plan.frame_rate,
            audio_channels=int(plan.audio_settings["channels"]),
            audio_sample_rate=int(plan.audio_settings["sample_rate"]),
            probe_version="phase5-fixture-probe-v1",
            probe_payload={"fixture": True},
            validation={
                "passed": True,
                "expected_duration_seconds": plan.duration_ms / 1000,
                "actual_duration_seconds": plan.duration_ms / 1000,
                "duration_delta_seconds": 0,
            },
            provenance={
                **plan.provenance,
                "candidate_id": str(candidate_id),
                "render_plan_id": str(plan.id),
                "render_job_id": str(render.id),
            },
            renderer_version=render.renderer_version,
        )
        session.add(output)
        await session.commit()
        return {
            "asset_id": plan.asset_id,
            "candidate_id": candidate_id,
            "render_id": render.id,
            "plan_id": plan.id,
            "output_id": output.id,
        }


def rights_payload(
    ids: dict[str, UUID],
    *,
    status: str = "valid",
    basis: str = "owned",
    platforms: list[str] | None = None,
    territories: list[str] | None = None,
    expires_delta: timedelta = timedelta(days=30),
    campaign_reference: str | None = "campaign-alpha",
    attribution_required: bool = False,
    attribution_text: str | None = None,
    material_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    material = {
        "music": "not_present",
        "footage": "not_present",
        "logos": "not_present",
        "likeness_or_voice": "cleared",
        "personal_data": "not_present",
        "sensitive_information": "not_present",
    }
    material.update(material_overrides or {})
    return {
        "asset_id": str(ids["asset_id"]),
        "scope": "render",
        "render_job_id": str(ids["render_id"]),
        "basis": basis,
        "status": status,
        "source_owner": "Synthetic Fixture Owner",
        "evidence_type": "ownership_record",
        "evidence_reference": "internal-rights-ledger:phase5-fixture",
        "evidence_checksum_sha256": hashlib.sha256(b"phase5-rights").hexdigest(),
        "permission_recorded_at": (now - timedelta(days=2)).isoformat(),
        "effective_from": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + expires_delta).isoformat(),
        "no_expiration_asserted": False,
        "allowed_platforms": platforms or ["youtube_shorts"],
        "allowed_territories": territories or ["IN"],
        "commercial_use_allowed": True,
        "editing_allowed": True,
        "ai_processing_allowed": True,
        "attribution_required": attribution_required,
        "attribution_text": attribution_text,
        "license_terms": "Commercial edited short-form use is explicitly authorized.",
        "restrictions": "Only the recorded render, platforms, territory, and campaign.",
        "third_party_material": material,
        "campaign_reference": campaign_reference,
    }


async def record_rights(
    client: Any,
    owner: dict[str, str],
    render_ids: dict[str, UUID],
    **overrides: Any,
) -> Any:
    response = await client.post(
        "/api/v1/rights-evidence",
        headers=owner,
        json=rights_payload(render_ids, **overrides),
    )
    assert response.status_code == 201, response.text
    return response


async def evaluate(
    client: Any,
    editor: dict[str, str],
    render_id: UUID,
    *,
    key: str,
    rights_id: str | None,
    destination: str = "youtube_shorts",
    territory: str = "IN",
    campaign_reference: str | None = "campaign-alpha",
) -> Any:
    response = await client.post(
        f"/api/v1/renders/{render_id}/quality-evaluations",
        headers=editor,
        json={
            "idempotency_key": key,
            "rights_evidence_id": rights_id,
            "destination": destination,
            "territory": territory,
            "campaign_reference": campaign_reference,
        },
    )
    assert response.status_code in {200, 201}, response.text
    return response


async def setup_valid_gate(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    *,
    key: str,
) -> tuple[
    Any, Any, dict[str, Any], dict[str, str], dict[str, str], dict[str, str], dict[str, UUID], str
]:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key=f"{key}-render",
    )
    rights = await record_rights(client, owner, render_ids)
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key=f"{key}-evaluation",
        rights_id=rights.json()["id"],
    )
    return client, app, ids, owner, editor, reviewer, render_ids, evaluation.json()["id"]


@pytest.mark.asyncio
async def test_quality_council_returns_eight_structured_checks_and_requires_human(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        _app,
        _ids,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        evaluation_id,
    ) = await setup_valid_gate(app_client, auth_headers, media_files, key="council")
    detail = await client.get(f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["evaluation"]["outcome"] == "needs_human_review", [
        (item["kind"], item["status"], item["evidence"].get("failures")) for item in body["checks"]
    ]
    assert body["rights_state"] == "valid"
    assert body["model_assistance_used"] is False
    assert body["legal_clearance"] is False
    assert len(body["checks"]) == 8
    assert {item["kind"] for item in body["checks"]} == {item.value for item in QualityCheckKind}
    assert all(
        {
            "status",
            "severity",
            "evidence",
            "confidence",
            "check_version",
            "remediation",
            "evidence_source",
        }.issubset(item)
        for item in body["checks"]
    )
    assert all(item["evidence_source"] == "deterministic" for item in body["checks"])


@pytest.mark.asyncio
async def test_independent_reviewer_approval_is_signed_and_rechecked_at_both_checkpoints(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        _app,
        _ids,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        evaluation_id,
    ) = await setup_valid_gate(app_client, auth_headers, media_files, key="approved")
    detail = await client.get(f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer)
    checks = detail.json()["checks"]
    acknowledged = [
        item["id"]
        for item in checks
        if item["status"] in {"needs_review", "unavailable"} or item["confidence"] < 0.7
    ]
    decision = await client.post(
        f"/api/v1/quality-evaluations/{evaluation_id}/decisions",
        headers=reviewer,
        json={
            "action": "approve",
            "reason": "I reviewed source context, audio, visuals, captions, and scoped rights.",
            "acknowledged_check_ids": acknowledged,
        },
    )
    assert decision.status_code == 200, decision.text
    approved = decision.json()
    assert approved["evaluation"]["outcome"] == "approved"
    assert len(approved["approval_signature"]) == 64
    assert approved["approval_evidence"]["policy_version"] == "mandatory-release-gate.v1"
    for checkpoint in ("publish_job_acceptance", "external_upload"):
        verification = await client.post(
            f"/api/v1/quality-evaluations/{evaluation_id}/verify",
            headers=reviewer,
            json={
                "checkpoint": checkpoint,
                "expected_destination": "youtube_shorts",
                "expected_territory": "IN",
            },
        )
        assert verification.status_code == 200, verification.text
        assert verification.json()["accepted"] is True
        assert len(verification.json()["evidence_signature"]) == 64
        assert verification.json()["evidence_payload"]["requires_fresh_database_recheck"] is True


@pytest.mark.asyncio
async def test_unknown_rights_block_and_cannot_be_overridden(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key="unknown-rights-render",
    )
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key="unknown-rights-evaluation",
        rights_id=None,
    )
    assert evaluation.json()["outcome"] == "blocked"
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    assert detail.json()["rights_state"] == "unknown"
    assert detail.json()["missing_evidence"]
    blocked_override = await client.post(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}/decisions",
        headers=reviewer,
        json={
            "action": "approve",
            "reason": "Attempt to approve despite missing permission documentation.",
            "acknowledged_check_ids": [],
        },
    )
    assert blocked_override.status_code == 409
    assert blocked_override.json()["error"]["code"] == "non_overridable_gate"


@pytest.mark.asyncio
async def test_public_availability_and_another_clips_permission_are_never_inferred(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    tmp_path: Path,
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    first = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key="scope-first-render",
    )
    invalid_basis = rights_payload(first)
    invalid_basis["basis"] = "publicly_available"
    public_page = await client.post("/api/v1/rights-evidence", headers=owner, json=invalid_basis)
    assert public_page.status_code == 422

    rights = await record_rights(client, owner, first)
    second_media = tmp_path / "distinct-source.mp4"
    source_bytes = await asyncio.to_thread(media_files["video"].read_bytes)
    await asyncio.to_thread(second_media.write_bytes, source_bytes + b"distinct-phase5-source")
    second = await seed_completed_render(
        client,
        app,
        ids,
        second_media,
        owner,
        editor,
        key="scope-second-render",
    )
    evaluation = await evaluate(
        client,
        editor,
        second["render_id"],
        key="wrong-clip-rights-evaluation",
        rights_id=rights.json()["id"],
    )
    assert evaluation.json()["outcome"] == "blocked"
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    assert "different source asset" in " ".join(detail.json()["hard_block_reasons"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "rights_overrides", "destination", "expected_fragment"),
    [
        ("expired", {"expires_delta": timedelta(hours=-1)}, "youtube_shorts", "expired"),
        (
            "platform",
            {"platforms": ["instagram_reels"]},
            "youtube_shorts",
            "destination platform",
        ),
        (
            "territory",
            {"territories": ["US"]},
            "youtube_shorts",
            "territory IN",
        ),
        (
            "unknown_music",
            {"material_overrides": {"music": "unknown"}},
            "youtube_shorts",
            "music rights",
        ),
    ],
)
async def test_expired_unsupported_scope_and_unknown_third_party_material_block(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    case: str,
    rights_overrides: dict[str, Any],
    destination: str,
    expected_fragment: str,
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key=f"{case}-render",
    )
    rights = await record_rights(client, owner, render_ids, **rights_overrides)
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key=f"{case}-evaluation",
        rights_id=rights.json()["id"],
        destination=destination,
    )
    assert evaluation.json()["outcome"] == "blocked"
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    assert expected_fragment in " ".join(detail.json()["hard_block_reasons"])


@pytest.mark.asyncio
async def test_missing_attribution_is_rejected_at_schema_and_gate_layers(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key="attribution-render",
    )
    invalid = rights_payload(render_ids, attribution_required=True, attribution_text=None)
    rejected = await client.post("/api/v1/rights-evidence", headers=owner, json=invalid)
    assert rejected.status_code == 422

    valid = await record_rights(
        client,
        owner,
        render_ids,
        attribution_required=True,
        attribution_text="Creator: Synthetic Fixture Owner",
    )
    async with app.state.session_factory() as session:
        evidence = await session.scalar(
            select(RightsEvidence).where(RightsEvidence.id == UUID(valid.json()["id"]))
        )
        assert evidence is not None
        evidence.attribution_text = None
        await session.commit()
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key="attribution-evaluation",
        rights_id=valid.json()["id"],
    )
    assert evaluation.json()["outcome"] == "blocked"
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    assert "required attribution is missing" in detail.json()["hard_block_reasons"]


@pytest.mark.asyncio
async def test_corrupted_private_output_is_a_non_overridable_technical_failure(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key="corrupt-render",
    )
    rights = await record_rights(client, owner, render_ids)
    async with app.state.session_factory() as session:
        output = await session.scalar(
            select(RenderOutput).where(RenderOutput.id == render_ids["output_id"])
        )
        assert output is not None
        await app.state.storage.put(
            ids["tenant_a"], output.storage_key, b"corrupted", content_type="video/mp4"
        )
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key="corrupt-evaluation",
        rights_id=rights.json()["id"],
    )
    assert evaluation.json()["outcome"] == "blocked"
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    technical = next(
        item for item in detail.json()["checks"] if item["kind"] == "technical_integrity"
    )
    assert technical["status"] == "failed"
    assert technical["hard_block"] is True
    assert "checksum" in " ".join(technical["evidence"]["failures"])


@pytest.mark.asyncio
async def test_fabricated_transcript_lineage_blocks_even_with_high_model_quality(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key="fabricated-render",
    )
    rights = await record_rights(client, owner, render_ids)
    async with app.state.session_factory() as session:
        candidate = await session.scalar(
            select(ClipCandidate).where(ClipCandidate.id == render_ids["candidate_id"])
        )
        assert candidate is not None
        candidate.transcript_excerpt = (
            "Fabricated claim that does not occur in any approved source transcript segment."
        )
        candidate.recommendation_confidence = 0.99
        await session.commit()
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key="fabricated-evaluation",
        rights_id=rights.json()["id"],
    )
    assert evaluation.json()["outcome"] == "blocked"
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    transcript_check = next(
        item for item in detail.json()["checks"] if item["kind"] == "transcript_audio_consistency"
    )
    assert transcript_check["status"] == "failed"
    assert transcript_check["hard_block"] is True


@pytest.mark.asyncio
async def test_low_confidence_becomes_needs_review_and_requires_explicit_acknowledgement(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key="confidence-render",
    )
    rights = await record_rights(client, owner, render_ids)
    async with app.state.session_factory() as session:
        candidate = await session.scalar(
            select(ClipCandidate).where(ClipCandidate.id == render_ids["candidate_id"])
        )
        transcript = (
            await session.scalar(select(Transcript).where(Transcript.id == candidate.transcript_id))
            if candidate is not None
            else None
        )
        segments = (
            list(
                (
                    await session.scalars(
                        select(TranscriptSegment).where(
                            TranscriptSegment.transcript_id == candidate.transcript_id
                        )
                    )
                ).all()
            )
            if candidate is not None
            else []
        )
        assert candidate is not None and transcript is not None and segments
        candidate.transcript_confidence = 0.25
        transcript.confidence = 0.25
        for segment in segments:
            segment.confidence = 0.25
        await session.commit()
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key="confidence-evaluation",
        rights_id=rights.json()["id"],
    )
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    transcript_check = next(
        item for item in detail.json()["checks"] if item["kind"] == "transcript_audio_consistency"
    )
    assert transcript_check["status"] == "needs_review"
    assert transcript_check["confidence"] == 0.25
    incomplete = await client.post(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}/decisions",
        headers=reviewer,
        json={
            "action": "approve",
            "reason": (
                "Reviewed manually but omitted the required explicit evidence acknowledgements."
            ),
            "acknowledged_check_ids": [],
        },
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "review_acknowledgement_incomplete"


@pytest.mark.asyncio
async def test_unauthorized_override_and_self_approval_are_denied(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        _app,
        _ids,
        owner,
        editor,
        reviewer,
        _render_ids,
        evaluation_id,
    ) = await setup_valid_gate(app_client, auth_headers, media_files, key="authority")
    detail = await client.get(f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer)
    acknowledgements = [item["id"] for item in detail.json()["checks"]]
    editor_attempt = await client.post(
        f"/api/v1/quality-evaluations/{evaluation_id}/decisions",
        headers=editor,
        json={
            "action": "approve",
            "reason": "An editor must not be able to override the independent review gate.",
            "acknowledged_check_ids": acknowledgements,
        },
    )
    assert editor_attempt.status_code == 403

    owner_evaluation = await client.post(
        f"/api/v1/renders/{detail.json()['evaluation']['render_job_id']}/quality-evaluations",
        headers=owner,
        json={
            "idempotency_key": "owner-self-approval-evaluation",
            "rights_evidence_id": detail.json()["evaluation"]["rights_evidence_id"],
            "destination": "youtube_shorts",
            "territory": "IN",
            "campaign_reference": "campaign-alpha",
        },
    )
    assert owner_evaluation.status_code == 201
    self_attempt = await client.post(
        f"/api/v1/quality-evaluations/{owner_evaluation.json()['id']}/decisions",
        headers=owner,
        json={
            "action": "approve",
            "reason": "The requesting operator must not approve their own consequential gate.",
            "acknowledged_check_ids": acknowledgements,
        },
    )
    assert self_attempt.status_code == 403


@pytest.mark.asyncio
async def test_revocation_after_approval_makes_evidence_stale_and_blocks_preflight(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        _app,
        _ids,
        owner,
        _editor,
        reviewer,
        _render_ids,
        evaluation_id,
    ) = await setup_valid_gate(app_client, auth_headers, media_files, key="revocation")
    detail = await client.get(f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer)
    body = detail.json()
    acknowledgements = [
        item["id"]
        for item in body["checks"]
        if item["status"] in {"needs_review", "unavailable"} or item["confidence"] < 0.7
    ]
    approved = await client.post(
        f"/api/v1/quality-evaluations/{evaluation_id}/decisions",
        headers=reviewer,
        json={
            "action": "approve",
            "reason": "Independent review completed before the later permission revocation event.",
            "acknowledged_check_ids": acknowledgements,
        },
    )
    assert approved.status_code == 200
    rights_id = approved.json()["evaluation"]["rights_evidence_id"]
    revoked = await client.post(
        f"/api/v1/rights-evidence/{rights_id}/revoke",
        headers=owner,
        json={
            "reason": "Source owner withdrew permission after the quality approval was recorded."
        },
    )
    assert revoked.status_code == 200
    preflight = await client.post(
        f"/api/v1/quality-evaluations/{evaluation_id}/verify",
        headers=reviewer,
        json={
            "checkpoint": "publish_job_acceptance",
            "expected_destination": "youtube_shorts",
            "expected_territory": "IN",
        },
    )
    assert preflight.status_code == 409
    assert preflight.json()["error"]["code"] == "mandatory_gate_denied"
    assert any(
        "revoked" in item or "stale" in item
        for item in preflight.json()["error"]["details"]["failure_reasons"]
    )
    stale = await client.get(f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer)
    assert stale.json()["stale"] is True
    assert stale.json()["rights_state"] == "revoked"


@pytest.mark.asyncio
async def test_direct_job_bypass_and_unapproved_gate_verification_are_rejected(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        _app,
        _ids,
        owner,
        _editor,
        reviewer,
        _render_ids,
        evaluation_id,
    ) = await setup_valid_gate(app_client, auth_headers, media_files, key="bypass")
    bypass = await client.post(
        "/api/v1/jobs",
        headers=owner,
        json={
            "kind": "publish.external_upload",
            "idempotency_key": "bypass-publish-job-0001",
            "payload": {"quality_evaluation_id": evaluation_id},
            "max_attempts": 1,
        },
    )
    assert bypass.status_code == 422
    assert bypass.json()["error"]["code"] == "external_action_unavailable"
    preflight = await client.post(
        f"/api/v1/quality-evaluations/{evaluation_id}/verify",
        headers=reviewer,
        json={
            "checkpoint": "external_upload",
            "expected_destination": "youtube_shorts",
            "expected_territory": "IN",
        },
    )
    assert preflight.status_code == 409
    assert "not approved" in " ".join(preflight.json()["error"]["details"]["failure_reasons"])


@pytest.mark.asyncio
async def test_tenant_isolation_hides_rights_evaluations_and_verifications(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        ids,
        _owner,
        _editor,
        reviewer,
        _render_ids,
        evaluation_id,
    ) = await setup_valid_gate(app_client, auth_headers, media_files, key="isolation")
    bravo = await auth_headers("bravo_owner")
    detail = await client.get(f"/api/v1/quality-evaluations/{evaluation_id}", headers=reviewer)
    rights_id = detail.json()["evaluation"]["rights_evidence_id"]
    hidden_evaluation = await client.get(
        f"/api/v1/quality-evaluations/{evaluation_id}", headers=bravo
    )
    hidden_rights = await client.get(f"/api/v1/rights-evidence/{rights_id}", headers=bravo)
    hidden_verify = await client.post(
        f"/api/v1/quality-evaluations/{evaluation_id}/verify",
        headers=bravo,
        json={
            "checkpoint": "external_upload",
            "expected_destination": "youtube_shorts",
            "expected_territory": "IN",
        },
    )
    assert hidden_evaluation.status_code == 404
    assert hidden_rights.status_code == 404
    assert hidden_verify.status_code == 404
    async with app.state.session_factory() as session:
        assert (
            await session.scalar(
                select(GateVerification).where(
                    GateVerification.tenant_id == ids["tenant_b"],
                    GateVerification.evaluation_id == UUID(evaluation_id),
                )
            )
            is None
        )


def test_contradictory_model_check_cannot_overrule_deterministic_failure() -> None:
    checks = [
        CheckDraft(
            kind=kind,
            status=QualityCheckStatus.PASSED,
            severity=QualitySeverity.INFO,
            evidence={"fixture": True},
            confidence=1.0,
            remediation="none",
        )
        for kind in QualityCheckKind
    ]
    checks[0] = CheckDraft(
        kind=QualityCheckKind.TECHNICAL_INTEGRITY,
        status=QualityCheckStatus.FAILED,
        severity=QualitySeverity.CRITICAL,
        evidence={"corrupted": True},
        confidence=1.0,
        remediation="replace corrupted output",
        hard_block=True,
    )
    checks.append(
        CheckDraft(
            kind=QualityCheckKind.TECHNICAL_INTEGRITY,
            status=QualityCheckStatus.PASSED,
            severity=QualitySeverity.INFO,
            evidence={"model_claim": "looks good"},
            confidence=0.99,
            remediation="none",
            evidence_source=EvidenceSource.MODEL,
        )
    )
    rights = RightsAssessment(
        state="valid",
        valid=True,
        hard_blocks=[],
        missing_evidence=[],
        remediation=[],
        explanations=[],
    )
    outcome, reasons = QualityGateService.aggregate_outcome(checks, rights)
    assert outcome == GateOutcome.BLOCKED
    assert "technical_integrity" in " ".join(reasons)


@pytest.mark.asyncio
async def test_model_actor_cannot_authorize_a_risky_action(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    (
        client,
        app,
        ids,
        _owner,
        _editor,
        _reviewer,
        _render_ids,
        evaluation_id,
    ) = await setup_valid_gate(app_client, auth_headers, media_files, key="model-actor")
    principal = Principal(
        user_id=ids["reviewer"],
        tenant_id=ids["tenant_a"],
        role=Role.REVIEWER,
        email="model@invalid.local",
    )
    with pytest.raises(AuthorizationError, match="AI or model"):
        async with app.state.session_factory() as session:
            await QualityGateService.review(
                session,
                principal=principal,
                evaluation_id=UUID(evaluation_id),
                request=QualityReviewRequest(
                    action="approve",
                    reason="A model attempts to authorize its own risky downstream action.",
                    acknowledged_check_ids=[],
                ),
                settings=app.state.settings,
                storage=app.state.storage,
                decision_source=EvidenceSource.MODEL,
            )


@pytest.mark.asyncio
async def test_destination_without_versioned_profile_is_needs_review_not_silent_pass(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    render_ids = await seed_completed_render(
        client,
        app,
        ids,
        media_files["video"],
        owner,
        editor,
        key="other-destination-render",
    )
    rights = await record_rights(client, owner, render_ids, platforms=["other"])
    evaluation = await evaluate(
        client,
        editor,
        render_ids["render_id"],
        key="other-destination-evaluation",
        rights_id=rights.json()["id"],
        destination="other",
    )
    detail = await client.get(
        f"/api/v1/quality-evaluations/{evaluation.json()['id']}", headers=reviewer
    )
    destination_check = next(
        item for item in detail.json()["checks"] if item["kind"] == "destination_constraints"
    )
    assert destination_check["status"] == "unavailable"
    assert destination_check["confidence"] == 0
    assert detail.json()["evaluation"]["outcome"] == "needs_human_review"
