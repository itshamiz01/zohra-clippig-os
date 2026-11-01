from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from clipos.config import Settings
from clipos.core.enums import (
    AssetSource,
    AssetState,
    CandidateState,
    DiarizationState,
    DiscoveryRunState,
    DiscoveryStrategy,
    MediaKind,
    ProviderMode,
    TranscriptState,
)
from clipos.media.errors import MediaPipelineError, MediaProcessTimeoutError
from clipos.media.tooling import MediaTooling, ProbeResult
from clipos.models import (
    Asset,
    ClipCandidate,
    DiscoveryRun,
    MediaMetadata,
    RenderJob,
    RenderOutput,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from clipos.queue import QueueMessage
from clipos.rendering import CaptionLayoutError, build_caption_cues, cues_to_srt
from clipos.services.rendering import RenderProcessor

pytestmark = pytest.mark.integration


async def resume_processing(client: Any, headers: dict[str, str]) -> None:
    response = await client.post(
        "/api/v1/diagnostics/control/resume",
        headers=headers,
        json={"reason": "Run explicitly authorized Phase 4 rendering tests."},
    )
    assert response.status_code == 200, response.text


async def seed_approved_candidate(
    app: Any,
    ids: dict[str, Any],
    media_path: Path,
    *,
    tenant: str = "tenant_a",
    submitter: str = "editor",
    state: CandidateState = CandidateState.APPROVED,
    source_present: bool = True,
) -> UUID:
    tenant_id = ids[tenant]
    submitter_id = ids[submitter]
    reviewer_id = ids["reviewer"] if tenant == "tenant_a" else ids["bravo_owner"]
    asset_id = uuid4()
    storage_key = f"assets/{asset_id}/original.mp4"
    data = await asyncio.to_thread(media_path.read_bytes)
    checksum = hashlib.sha256(data).hexdigest()
    if source_present:
        info = await app.state.storage.put_file(
            tenant_id, storage_key, media_path, content_type="video/mp4"
        )
        assert info.checksum_sha256 == checksum
    async with app.state.session_factory() as session:
        asset = Asset(
            id=asset_id,
            tenant_id=tenant_id,
            submitted_by_user_id=submitter_id,
            processing_job_id=None,
            source=AssetSource.LOCAL_UPLOAD,
            state=AssetState.READY,
            media_kind=MediaKind.VIDEO,
            original_filename="authorized-phase4-fixture.mp4",
            safe_extension=".mp4",
            declared_content_type="video/mp4",
            detected_content_type="video/mp4",
            size_bytes=len(data),
            checksum_sha256=checksum,
            storage_key=storage_key,
            derived_audio_storage_key=None,
            derived_audio_checksum_sha256=None,
            permission_confirmed=True,
            rights_note="Synthetic media generated locally and authorized for Phase 4 tests.",
            progress_percent=100,
            processing_stage="ready",
            metadata_verified=True,
            cancellation_requested=False,
        )
        metadata = MediaMetadata(
            tenant_id=tenant_id,
            asset_id=asset_id,
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            duration_seconds=1.2,
            bit_rate=180000,
            has_video=True,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
            width=320,
            height=180,
            frame_rate=25.0,
            audio_channels=1,
            audio_sample_rate=44100,
            probe_version="real-fixture-probe",
            probe_payload={"fixture": True},
        )
        transcript = Transcript(
            tenant_id=tenant_id,
            asset_id=asset_id,
            state=TranscriptState.READY,
            provider="fixture",
            model="fixture-timestamps-v1",
            language="multilingual",
            confidence=0.94,
            diarization_state=DiarizationState.AVAILABLE,
            original_text="Hello café दुनिया مرحبا — captions stay readable!",
            normalized_text="Hello café दुनिया مرحبا — captions stay readable!",
            summary="A multilingual caption fixture.",
            key_topics=["captions", "rendering"],
            raw_provider_output={"fixture": True},
            word_timestamps_available=True,
            segment_count=1,
            word_count=8,
            provider_request_id="phase4-fixture",
            algorithm_version="transcript-normalize-chunk-v1",
        )
        session.add_all([asset, metadata, transcript])
        await session.flush()
        segment = TranscriptSegment(
            tenant_id=tenant_id,
            transcript_id=transcript.id,
            ordinal=0,
            start_ms=0,
            end_ms=1100,
            text=transcript.original_text,
            normalized_text=transcript.normalized_text,
            confidence=0.94,
            speaker_label="speaker_0",
        )
        session.add(segment)
        await session.flush()
        tokens = transcript.original_text.split()
        for ordinal, token in enumerate(tokens):
            start = 50 + ordinal * 120
            session.add(
                TranscriptWord(
                    tenant_id=tenant_id,
                    transcript_id=transcript.id,
                    segment_id=segment.id,
                    ordinal=ordinal,
                    start_ms=start,
                    end_ms=min(990, start + 105),
                    text=token,
                    confidence=0.94,
                    speaker_label="speaker_0",
                )
            )
        run = DiscoveryRun(
            tenant_id=tenant_id,
            asset_id=asset.id,
            transcript_id=transcript.id,
            requested_by_user_id=submitter_id,
            state=DiscoveryRunState.SUCCEEDED,
            strategy=DiscoveryStrategy.DETERMINISTIC,
            input_hash=hashlib.sha256(str(asset.id).encode()).hexdigest(),
            algorithm_version="candidate-planner-v1",
            score_version="explainable-score-v1",
            configuration={"target_profile": "general"},
            provider=None,
            model=None,
            provider_mode=ProviderMode.DISABLED,
            model_verified=False,
            model_request_id=None,
            input_tokens=None,
            output_tokens=None,
            estimated_cost=Decimal("0"),
            latency_ms=1,
            candidate_count=1,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        candidate = ClipCandidate(
            tenant_id=tenant_id,
            asset_id=asset.id,
            transcript_id=transcript.id,
            discovery_run_id=run.id,
            parent_candidate_id=None,
            state=state,
            rank=1,
            start_ms=0,
            end_ms=1000,
            speech_start_ms=0,
            speech_end_ms=1000,
            start_segment_ordinal=0,
            end_segment_ordinal=0,
            transcript_excerpt=transcript.original_text,
            language="multilingual",
            transcript_confidence=0.94,
            recommendation_confidence=0.88,
            overall_score=84.0,
            rationale="Approved synthetic fixture; no performance claim.",
            topics=["captions", "rendering"],
            risk_flags=[],
            model_provider=None,
            model=None,
            model_request_id=None,
            model_verified=False,
            word_timestamps_available=True,
            algorithm_version="candidate-planner-v1",
            score_version="explainable-score-v1",
            boundary_revision=1,
            reviewed_by_user_id=reviewer_id if state == CandidateState.APPROVED else None,
            reviewed_at=datetime.now(UTC) if state == CandidateState.APPROVED else None,
        )
        session.add(candidate)
        await session.commit()
        return candidate.id


def render_payload(key: str = "render-fixture-0001") -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "preset": "custom",
        "width": 240,
        "height": 426,
        "frame_rate": 25,
        "crop_strategy": "smart_face_fallback",
        "captions": {
            "mode": "both",
            "max_characters_per_line": 24,
            "max_lines": 2,
            "style": "high_contrast_bottom",
        },
        "audio": {
            "normalization": "speech_loudness",
            "target_lufs": -16,
            "true_peak_db": -1.5,
        },
        "include_tenant_branding": False,
        "video_codec": "h264",
        "audio_codec": "aac",
        "container": "mp4",
    }


async def create_plan(
    client: Any,
    headers: dict[str, str],
    candidate_id: UUID,
    *,
    key: str = "render-fixture-0001",
) -> Any:
    response = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=headers,
        json=render_payload(key),
    )
    assert response.status_code == 201, response.text
    return response


def test_caption_layout_is_unicode_safe_bounded_and_srt_escaped() -> None:
    words = [
        SimpleNamespace(start_ms=0, end_ms=400, text="Hello"),
        SimpleNamespace(start_ms=420, end_ms=800, text="दुनिया"),
        SimpleNamespace(start_ms=820, end_ms=1200, text="<script>"),
        SimpleNamespace(start_ms=1220, end_ms=1500, text="مرحبا!"),
        SimpleNamespace(start_ms=1520, end_ms=1750, text="{\\an8}\u202e"),
    ]
    cues = build_caption_cues(
        words=words,
        segments=[],
        clip_start_ms=0,
        clip_end_ms=1800,
        max_characters_per_line=14,
        max_lines=2,
        max_cue_ms=1800,
        max_segments=10,
    )
    assert cues
    assert all(cue.line_count <= 2 for cue in cues)
    assert all(len(line) <= 14 for cue in cues for line in cue.text.splitlines())
    srt = cues_to_srt(cues)
    assert "दुनिया" in srt and "مرحبا" in srt
    assert "&lt;script&gt;" in srt and "<script>" not in srt
    assert "{\\an8}" not in srt and "\u202e" not in srt and "｛\\an8｝" in srt

    with pytest.raises(CaptionLayoutError):
        build_caption_cues(
            words=[SimpleNamespace(start_ms=0, end_ms=500, text="x" * 200)],
            segments=[],
            clip_start_ms=0,
            clip_end_ms=1000,
            max_characters_per_line=12,
            max_lines=1,
            max_cue_ms=1000,
            max_segments=5,
        )


@pytest.mark.asyncio
async def test_missing_ffmpeg_is_reported_without_mock_success(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        secret_key="x" * 40,
        storage_local_root=tmp_path / "storage",
        render_temp_root=tmp_path / "render-tmp",
        ffmpeg_path="/definitely/missing/ffmpeg",
        ffprobe_path="/definitely/missing/ffprobe",
    )
    status = await MediaTooling(settings).check()
    assert status.available is False
    assert status.render_ready is False
    assert status.error_category == "media_tool_unavailable"


@pytest.mark.asyncio
async def test_plan_requires_approved_candidate_and_rejects_unsafe_fields(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(
        app, ids, media_files["video"], state=CandidateState.NEEDS_REVIEW
    )
    denied = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=editor,
        json=render_payload(),
    )
    assert denied.status_code == 409
    assert "approved" in denied.json()["error"]["message"]

    invalid = render_payload("unsafe-path-0001")
    invalid["output_path"] = "../../source.mp4"
    traversal = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=editor,
        json=invalid,
    )
    assert traversal.status_code == 422
    invalid_custom = render_payload("invalid-custom-0001")
    invalid_custom.pop("height")
    missing_dimension = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=editor,
        json=invalid_custom,
    )
    assert missing_dimension.status_code == 422


@pytest.mark.asyncio
async def test_plan_rejects_approved_timestamps_outside_verified_source(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(app, ids, media_files["video"])
    async with app.state.session_factory() as session:
        candidate = await session.scalar(
            select(ClipCandidate).where(ClipCandidate.id == candidate_id)
        )
        assert candidate is not None
        candidate.end_ms = 1300
        candidate.speech_end_ms = 1300
        await session.commit()
    response = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=editor,
        json=render_payload("invalid-timestamps-0001"),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "render_bounds_invalid"


@pytest.mark.asyncio
async def test_plan_creation_is_tenant_safe_immutable_and_idempotent(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    bravo = await auth_headers("bravo_owner")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(app, ids, media_files["video"])
    first = await create_plan(client, editor, candidate_id)
    body = first.json()
    assert body["plan"]["source_start_ms"] == 0
    assert body["plan"]["source_end_ms"] == 1000
    assert body["plan"]["crop_strategy_requested"] == "smart_face_fallback"
    assert body["plan"]["crop_strategy_applied"] == "center_crop"
    assert body["plan"]["smart_crop_used"] is False
    assert "face detection" in body["plan"]["crop_fallback_reason"]
    assert body["plan"]["output_storage_prefix"].startswith("renders/")
    assert ".." not in body["plan"]["output_storage_prefix"]
    assert body["captions"] and "दुनिया" in " ".join(item["text"] for item in body["captions"])

    replay = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=editor,
        json=render_payload(),
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["plan"]["id"] == body["plan"]["id"]
    assert replay.json()["render"]["id"] == body["render"]["id"]

    conflict_payload = render_payload()
    conflict_payload["crop_strategy"] = "fit_pad"
    conflict = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=editor,
        json=conflict_payload,
    )
    assert conflict.status_code == 409

    hidden = await client.get(f"/api/v1/renders/{body['render']['id']}", headers=bravo)
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_real_fixture_render_captions_probe_checksum_preview_and_defect_gate(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    reviewer = await auth_headers("reviewer")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(app, ids, media_files["video"])
    created = await create_plan(client, editor, candidate_id, key="real-render-0001")
    render = created.json()["render"]
    await app.state.render_processor.process_job(
        QueueMessage(
            job_id=UUID(render["background_job_id"]),
            tenant_id=ids["tenant_a"],
            kind="render.execute",
        )
    )
    detail = await client.get(f"/api/v1/renders/{render['id']}", headers=editor)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["render"]["state"] == "succeeded", (
        body["render"]["failure_category"],
        body["render"]["failure_message"],
        body["render"]["failure_details"],
    )
    assert body["render"]["progress_percent"] == 100
    assert body["render"]["attempt_count"] == 1
    output = body["outputs"][0]
    assert output["validation"]["passed"] is True
    assert output["width"] == 240 and output["height"] == 426
    assert output["video_codec"] == "h264" and output["audio_codec"] == "aac"
    assert output["checksum_sha256"] and output["size_bytes"] > 0
    assert output["sidecar_checksum_sha256"]
    assert output["provenance"]["candidate_id"] == str(candidate_id)
    assert output["provenance"]["source_time_mapping"]["source_end_ms"] == 1000
    assert output["provenance"]["source_time_mapping"]["playback_rate"] == 1.0

    preview = await client.get(f"/api/v1/renders/{render['id']}/preview", headers=editor)
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("video/mp4")
    assert preview.headers["cache-control"] == "private, no-store"
    assert len(preview.content) == output["size_bytes"]
    captions = await client.get(f"/api/v1/renders/{render['id']}/captions", headers=editor)
    assert captions.status_code == 200
    assert "दुनिया" in captions.content.decode("utf-8")

    await app.state.storage.put(
        ids["tenant_a"],
        output["storage_key"],
        b"tampered-output-and-metadata",
        content_type="video/mp4",
    )
    integrity_failure = await client.get(f"/api/v1/renders/{render['id']}/preview", headers=editor)
    assert integrity_failure.status_code == 503
    assert integrity_failure.json()["error"]["code"] == "render_output_integrity_failure"

    defect = await client.post(
        f"/api/v1/renders/{render['id']}/defects",
        headers=reviewer,
        json={
            "category": "meaning_or_boundary",
            "description": "The requested change would alter the approved source boundary.",
            "meaning_or_boundary_changed": False,
            "requested_resolution": "Return to candidate review for a new immutable revision.",
        },
    )
    assert defect.status_code == 201
    assert defect.json()["render"]["state"] == "needs_review"
    assert defect.json()["defects"][0]["meaning_or_boundary_changed"] is True
    retry = await client.post(f"/api/v1/renders/{render['id']}/retry", headers=editor)
    assert retry.status_code == 409
    assert "new candidate revision" in retry.json()["error"]["message"]


@pytest.mark.asyncio
async def test_queued_cancellation_and_bounded_retry_are_consistent(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(app, ids, media_files["video"])
    created = await create_plan(client, editor, candidate_id, key="cancel-render-0001")
    render = created.json()["render"]
    cancelled = await client.post(f"/api/v1/renders/{render['id']}/cancel", headers=editor)
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    await app.state.render_processor.process_job(
        QueueMessage(
            job_id=UUID(render["background_job_id"]),
            tenant_id=ids["tenant_a"],
            kind="render.execute",
        )
    )
    detail = await client.get(f"/api/v1/renders/{render['id']}", headers=editor)
    assert detail.json()["render"]["state"] == "cancelled"
    assert detail.json()["outputs"] == []

    retried = await client.post(f"/api/v1/renders/{render['id']}/retry", headers=editor)
    assert retried.status_code == 202
    assert retried.json()["state"] == "queued"
    replay = await client.post(
        f"/api/v1/candidates/{candidate_id}/render-plans",
        headers=editor,
        json=render_payload("cancel-render-0001"),
    )
    assert replay.status_code == 200
    assert replay.json()["render"]["id"] == render["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_category"),
    [
        (MediaProcessTimeoutError("render"), "media_process_timeout"),
        (
            MediaPipelineError(
                "render_process_failed", "FFmpeg fixture process crashed", retryable=False
            ),
            "render_process_failed",
        ),
    ],
)
async def test_timeout_and_process_crash_leave_no_success_output(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure: MediaPipelineError,
    expected_category: str,
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(app, ids, media_files["video"])
    created = await create_plan(
        client, editor, candidate_id, key=f"failure-{expected_category}-0001"
    )
    render = created.json()["render"]

    async def fail_render(*_args: Any, **_kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(app.state.media_tools, "render_clip", fail_render)
    await app.state.render_processor.process_job(
        QueueMessage(
            job_id=UUID(render["background_job_id"]),
            tenant_id=ids["tenant_a"],
            kind="render.execute",
        )
    )
    detail = await client.get(f"/api/v1/renders/{render['id']}", headers=editor)
    body = detail.json()
    assert body["render"]["state"] == "failed"
    assert body["render"]["failure_category"] == expected_category
    assert body["outputs"] == []


@pytest.mark.asyncio
async def test_failed_output_probe_cannot_create_a_success_record(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(app, ids, media_files["video"])
    created = await create_plan(client, editor, candidate_id, key="probe-failure-render-0001")
    render = created.json()["render"]
    original_probe = app.state.media_tools.probe

    async def selective_probe(path: Path) -> ProbeResult:
        if path.name == "render.mp4":
            raise MediaPipelineError(
                "ffprobe_invalid_output", "ffprobe returned invalid output metadata"
            )
        return await original_probe(path)

    async def fake_render(spec: Any, **kwargs: Any) -> None:
        fixture_bytes = await asyncio.to_thread(media_files["video"].read_bytes)
        await asyncio.to_thread(spec.destination.write_bytes, fixture_bytes)
        callback = kwargs.get("progress_callback")
        if callback is not None:
            await callback(1.0)

    monkeypatch.setattr(app.state.media_tools, "probe", selective_probe)
    monkeypatch.setattr(app.state.media_tools, "render_clip", fake_render)
    await app.state.render_processor.process_job(
        QueueMessage(
            job_id=UUID(render["background_job_id"]),
            tenant_id=ids["tenant_a"],
            kind="render.execute",
        )
    )
    detail = await client.get(f"/api/v1/renders/{render['id']}", headers=editor)
    assert detail.json()["render"]["failure_category"] == "ffprobe_invalid_output"
    assert detail.json()["outputs"] == []


@pytest.mark.asyncio
async def test_missing_or_changed_source_and_insufficient_disk_are_explicit_failures(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    await resume_processing(client, owner)
    missing_candidate = await seed_approved_candidate(
        app, ids, media_files["video"], source_present=False
    )
    missing = await create_plan(client, editor, missing_candidate, key="missing-source-render-0001")
    missing_render = missing.json()["render"]
    await app.state.render_processor.process_job(
        QueueMessage(
            job_id=UUID(missing_render["background_job_id"]),
            tenant_id=ids["tenant_a"],
            kind="render.execute",
        )
    )
    detail = await client.get(f"/api/v1/renders/{missing_render['id']}", headers=editor)
    assert detail.json()["render"]["failure_category"] == "render_source_missing"
    assert detail.json()["outputs"] == []

    async with app.state.session_factory() as session:
        candidate = await session.scalar(
            select(ClipCandidate).where(ClipCandidate.id == missing_candidate)
        )
        assert candidate is not None
        asset = await session.scalar(select(Asset).where(Asset.id == candidate.asset_id))
        assert asset is not None
        storage_key = asset.storage_key
    await app.state.storage.put_file(
        ids["tenant_a"],
        storage_key,
        media_files["silent_video"],
        content_type="video/mp4",
    )
    integrity_retry = await client.post(
        f"/api/v1/renders/{missing_render['id']}/retry", headers=editor
    )
    assert integrity_retry.status_code == 202
    await app.state.render_processor.process_job(
        QueueMessage(
            job_id=UUID(missing_render["background_job_id"]),
            tenant_id=ids["tenant_a"],
            kind="render.execute",
        )
    )
    integrity_detail = await client.get(f"/api/v1/renders/{missing_render['id']}", headers=editor)
    assert (
        integrity_detail.json()["render"]["failure_category"] == "render_source_integrity_changed"
    )
    assert integrity_detail.json()["render"]["state"] == "needs_review"

    await app.state.storage.put_file(
        ids["tenant_a"],
        storage_key,
        media_files["video"],
        content_type="video/mp4",
    )
    retry = await client.post(f"/api/v1/renders/{missing_render['id']}/retry", headers=editor)
    assert retry.status_code == 202
    monkeypatch.setattr(
        "clipos.services.rendering.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=99, free=1),
    )
    await app.state.render_processor.process_job(
        QueueMessage(
            job_id=UUID(missing_render["background_job_id"]),
            tenant_id=ids["tenant_a"],
            kind="render.execute",
        )
    )
    disk_detail = await client.get(f"/api/v1/renders/{missing_render['id']}", headers=editor)
    assert disk_detail.json()["render"]["failure_category"] == "insufficient_render_disk"
    assert disk_detail.json()["outputs"] == []


@pytest.mark.asyncio
async def test_output_validation_rejects_wrong_duration_and_missing_audio(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        secret_key="x" * 40,
        storage_local_root=tmp_path / "storage",
        render_temp_root=tmp_path / "render-tmp",
    )
    processor = RenderProcessor(
        session_factory=SimpleNamespace(),  # type: ignore[arg-type]
        storage=SimpleNamespace(),  # type: ignore[arg-type]
        media_tools=MediaTooling(settings),
        settings=settings,
    )
    plan = SimpleNamespace(
        duration_ms=1000,
        width=240,
        height=426,
        frame_rate=25.0,
        audio_settings={"channels": 2, "sample_rate": 48000},
    )
    output = tmp_path / "output.mp4"
    output.write_bytes(b"not-empty")

    def probe(*, duration: float, has_audio: bool = True) -> ProbeResult:
        return ProbeResult(
            format_name="mov,mp4",
            duration_seconds=duration,
            bit_rate=1000,
            has_video=True,
            has_audio=has_audio,
            video_codec="h264",
            audio_codec="aac" if has_audio else None,
            width=240,
            height=426,
            frame_rate=25.0,
            audio_channels=2 if has_audio else None,
            audio_sample_rate=48000 if has_audio else None,
            media_kind=MediaKind.VIDEO,
            probe_version="fixture",
            raw={"fixture": True},
        )

    with pytest.raises(MediaPipelineError) as wrong_duration:
        processor._validate_output(plan, probe(duration=2.0), output)
    assert wrong_duration.value.category == "output_duration_mismatch"
    with pytest.raises(MediaPipelineError) as missing_audio:
        processor._validate_output(plan, probe(duration=1.0, has_audio=False), output)
    assert missing_audio.value.category == "output_audio_missing"


@pytest.mark.asyncio
async def test_branding_profile_is_owner_controlled_and_honest_about_capability(
    app_client: tuple[Any, Any, dict[str, Any]], auth_headers: Any
) -> None:
    client, _, _ = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    denied = await client.put(
        "/api/v1/rendering/profile",
        headers=editor,
        json={
            "branding_enabled": True,
            "branding_text": "ALPHA",
            "branding_position": "top_right",
            "branding_opacity": 0.8,
            "branding_font_size": 30,
        },
    )
    assert denied.status_code == 403
    configured = await client.put(
        "/api/v1/rendering/profile",
        headers=owner,
        json={
            "branding_enabled": True,
            "branding_text": "ALPHA ©",
            "branding_position": "bottom_right",
            "branding_opacity": 0.8,
            "branding_font_size": 30,
        },
    )
    assert configured.status_code == 200
    assert configured.json()["branding_text"] == "ALPHA ©"
    assert configured.json()["logo_overlay_supported"] is False
    capabilities = await client.get("/api/v1/rendering/capabilities", headers=editor)
    assert capabilities.status_code == 200
    body = capabilities.json()
    assert body["render_ready"] is True
    assert "face detection or face tracking" in body["unavailable_features"]
    assert "logo/image uploads and third-party music" in body["unavailable_features"]


@pytest.mark.asyncio
async def test_render_list_and_database_records_remain_tenant_scoped(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, ids = app_client
    owner = await auth_headers("owner")
    editor = await auth_headers("editor")
    bravo = await auth_headers("bravo_owner")
    await resume_processing(client, owner)
    candidate_id = await seed_approved_candidate(app, ids, media_files["video"])
    created = await create_plan(client, editor, candidate_id, key="tenant-list-render-0001")
    render_id = UUID(created.json()["render"]["id"])
    alpha_list = await client.get("/api/v1/renders", headers=editor)
    bravo_list = await client.get("/api/v1/renders", headers=bravo)
    assert alpha_list.status_code == 200 and alpha_list.json()["total"] == 1
    assert bravo_list.status_code == 200 and bravo_list.json()["total"] == 0
    async with app.state.session_factory() as session:
        row = await session.scalar(select(RenderJob).where(RenderJob.id == render_id))
        assert row is not None and row.tenant_id == ids["tenant_a"]
        assert (
            await session.scalar(select(RenderOutput).where(RenderOutput.render_job_id == row.id))
            is None
        )
