from __future__ import annotations

import asyncio
import contextlib
import errno
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from starlette.datastructures import Headers, UploadFile

from clipos.config import Settings
from clipos.core.enums import AssetState, JobState
from clipos.gateways.transcription import (
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegmentData,
    TranscriptWordData,
)
from clipos.gateways.transcription.errors import TranscriptionTimeoutError
from clipos.gateways.transcription.gateway import TranscriptionGateway
from clipos.media.errors import UploadValidationError
from clipos.models import Asset, BackgroundJob, Transcript
from clipos.queue import QueueMessage
from clipos.services.asset_processing import AssetProcessingService, AssetWorker
from clipos.services.assets import stage_upload
from clipos.services.transcripts import build_chunks, normalize_text


class ValidTranscriptionAdapter:
    provider_name = "fixture"
    model_name = "fixture-timestamps-v1"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        end = request.duration_seconds
        boundary = min(0.55, max(0.1, end / 2))
        return TranscriptionResult(
            text="Hello   Delhi. Secure media works.",
            language="en",
            confidence=0.93,
            segments=[
                TranscriptSegmentData(
                    start_seconds=0,
                    end_seconds=boundary,
                    text="Hello Delhi.",
                    confidence=0.95,
                    speaker_label="speaker_0",
                ),
                TranscriptSegmentData(
                    start_seconds=boundary,
                    end_seconds=end,
                    text="Secure media works.",
                    confidence=0.91,
                    speaker_label="speaker_0",
                ),
            ],
            words=[
                TranscriptWordData(
                    start_seconds=0,
                    end_seconds=min(0.2, boundary),
                    text="Hello",
                    confidence=0.95,
                    speaker_label="speaker_0",
                ),
                TranscriptWordData(
                    start_seconds=min(0.21, boundary),
                    end_seconds=boundary,
                    text="Delhi",
                    confidence=0.94,
                    speaker_label="speaker_0",
                ),
            ],
            diarization_available=True,
            raw_provider_output={"fixture": True, "version": 1},
            provider_request_id="fixture-request-1",
        )

    async def close(self) -> None:
        return None


class LowConfidenceAdapter:
    provider_name = "fixture"
    model_name = "fixture-low-confidence"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(
            text="Uncertain words.",
            language="en",
            confidence=0.2,
            segments=[
                TranscriptSegmentData(
                    start_seconds=0,
                    end_seconds=request.duration_seconds,
                    text="Uncertain words.",
                    confidence=0.2,
                )
            ],
            raw_provider_output={"raw_evidence_marker": "preserved"},
        )

    async def close(self) -> None:
        return None


class TimeoutTranscriptionAdapter:
    provider_name = "fixture"
    model_name = "fixture-timeout"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        raise TranscriptionTimeoutError("private provider timeout detail")

    async def close(self) -> None:
        return None


def upload_payload(path: Path) -> dict[str, Any]:
    return {
        "files": {
            "file": (
                path.name,
                path.read_bytes(),
                "audio/wav" if path.suffix == ".wav" else "video/mp4",
            )
        },
        "data": {
            "permission_confirmed": "true",
            "rights_note": "I own this synthetic fixture and authorize its processing.",
        },
    }


async def process_with_gateway(
    app: Any, body: dict[str, Any], gateway: TranscriptionGateway
) -> None:
    processor = AssetProcessingService(
        session_factory=app.state.session_factory,
        storage=app.state.storage,
        media_tools=app.state.media_tools,
        transcription=gateway,
        settings=app.state.settings,
    )
    await processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )


@pytest.mark.asyncio
async def test_end_to_end_real_media_processing_persists_timeline_and_search(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    owner_headers = await auth_headers("owner")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=owner_headers,
        **upload_payload(media_files["video"]),
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    gateway = TranscriptionGateway(ValidTranscriptionAdapter(), max_retries=0)
    await process_with_gateway(app, body, gateway)

    detail = await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=owner_headers)
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["asset"]["state"] == "ready"
    assert payload["asset"]["progress_percent"] == 100
    assert payload["asset"]["metadata_verified"] is True
    assert payload["metadata"]["has_audio"] is True
    assert payload["metadata"]["has_video"] is True
    assert payload["metadata"]["duration_seconds"] > 1
    assert payload["transcript"]["state"] == "ready"
    assert payload["transcript"]["language"] == "en"
    assert payload["transcript"]["confidence"] == pytest.approx(0.93)
    assert payload["transcript"]["diarization_state"] == "available"

    transcript = await client.get(
        f"/api/v1/assets/{body['asset']['id']}/transcript", headers=owner_headers
    )
    assert transcript.status_code == 200, transcript.text
    evidence = transcript.json()
    assert evidence["original_text"] == "Hello   Delhi. Secure media works."
    assert evidence["normalized_text"] == "Hello Delhi. Secure media works."
    assert len(evidence["segments"]) == 2
    assert evidence["segments"][0]["start_ms"] == 0
    assert evidence["segments"][0]["speaker_label"] == "speaker_0"
    assert len(evidence["words"]) == 2
    assert evidence["chunk_count"] == len(evidence["chunks"])
    assert evidence["chunk_offset"] == 0
    assert evidence["word_offset"] == 0
    assert evidence["chunks"][0]["char_start"] == 0
    assert evidence["chunks"][-1]["char_end"] == len(evidence["normalized_text"])
    assert "secure" in evidence["transcript"]["key_topics"]

    search = await client.get("/api/v1/assets?q=secure", headers=owner_headers)
    assert search.status_code == 200
    assert [item["id"] for item in search.json()] == [body["asset"]["id"]]


@pytest.mark.asyncio
async def test_valid_video_upload_processes_to_ready_with_verified_metadata(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["video"]),
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    processor = AssetProcessingService(
        session_factory=app.state.session_factory,
        storage=app.state.storage,
        media_tools=app.state.media_tools,
        transcription=TranscriptionGateway(ValidTranscriptionAdapter(), max_retries=0),
        settings=app.state.settings,
    )
    await processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "ready"
    assert detail["asset"]["media_kind"] == "video"
    stored = await app.state.storage.list(UUID(body["asset"]["tenant_id"]))
    derived = next(item for item in stored if item.key.endswith("/derived/audio-16k.wav"))
    assert len(derived.checksum_sha256) == 64
    assert detail["metadata"]["has_video"] is True
    assert detail["metadata"]["has_audio"] is True
    assert detail["metadata"]["width"] == 320
    assert detail["metadata"]["height"] == 180
    assert detail["transcript"]["state"] == "ready"


@pytest.mark.asyncio
async def test_recoverable_worker_consumes_queue_after_audited_resume(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("owner")
    resumed = await client.post(
        "/api/v1/diagnostics/control/resume",
        headers=headers,
        json={"reason": "Run the authorized Phase 2 worker fixture."},
    )
    assert resumed.status_code == 200
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    processor = AssetProcessingService(
        session_factory=app.state.session_factory,
        storage=app.state.storage,
        media_tools=app.state.media_tools,
        transcription=TranscriptionGateway(ValidTranscriptionAdapter(), max_retries=0),
        settings=app.state.settings,
    )
    worker = AssetWorker(
        processor=processor,
        session_factory=app.state.session_factory,
        queue=app.state.queue,
        settings=app.state.settings,
    )
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(80):
            detail = await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)
            if detail.json()["asset"]["state"] == "ready":
                break
            await asyncio.sleep(0.05)
        assert detail.json()["asset"]["state"] == "ready"
        job_response = await client.get(
            f"/api/v1/jobs/{body['processing_job_id']}", headers=headers
        )
        audit = await client.get("/api/v1/diagnostics/audit", headers=headers)
        completed = next(
            row for row in audit.json() if row["action"] == "asset.processing_completed"
        )
        assert completed["correlation_id"] == job_response.json()["correlation_id"]
    finally:
        worker.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_stale_running_job_recovers_after_simulated_worker_crash(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("owner")
    resumed = await client.post(
        "/api/v1/diagnostics/control/resume",
        headers=headers,
        json={"reason": "Recover a simulated stale media worker job."},
    )
    assert resumed.status_code == 200
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    stale_time = datetime.now(UTC) - timedelta(hours=2)
    async with app.state.session_factory() as session:
        job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == UUID(body["processing_job_id"]))
        )
        asset = await session.scalar(select(Asset).where(Asset.id == UUID(body["asset"]["id"])))
        assert job is not None and asset is not None
        job.state = JobState.RUNNING
        job.attempts = 1
        job.started_at = stale_time
        job.heartbeat_at = stale_time
        asset.state = AssetState.TRANSCRIBING
        asset.processing_stage = "transcribing"
        asset.progress_percent = 65
        await session.commit()

    processor = AssetProcessingService(
        session_factory=app.state.session_factory,
        storage=app.state.storage,
        media_tools=app.state.media_tools,
        transcription=TranscriptionGateway(ValidTranscriptionAdapter(), max_retries=0),
        settings=app.state.settings,
    )
    worker = AssetWorker(
        processor=processor,
        session_factory=app.state.session_factory,
        queue=app.state.queue,
        settings=app.state.settings,
    )
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(100):
            detail = await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)
            if detail.json()["asset"]["state"] == "ready":
                break
            await asyncio.sleep(0.05)
        assert detail.json()["asset"]["state"] == "ready"
        job_response = await client.get(
            f"/api/v1/jobs/{body['processing_job_id']}", headers=headers
        )
        assert job_response.json()["state"] == "succeeded"
        assert job_response.json()["attempts"] == 2
        audit = await client.get("/api/v1/diagnostics/audit", headers=headers)
        assert "asset.processing_recovered" in [row["action"] for row in audit.json()]
    finally:
        worker.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_low_confidence_transcript_requires_review_and_preserves_raw_evidence(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    await process_with_gateway(
        app,
        body,
        TranscriptionGateway(LowConfidenceAdapter(), max_retries=0),
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "needs_review"
    assert detail["asset"]["error_category"] == "low_confidence"
    assert detail["transcript"]["state"] == "needs_review"
    assert detail["transcript"]["confidence"] == pytest.approx(0.2)

    async with app.state.session_factory() as session:
        transcript = await session.scalar(
            select(Transcript).where(Transcript.asset_id == UUID(body["asset"]["id"]))
        )
        assert transcript is not None
        assert transcript.raw_provider_output == {"raw_evidence_marker": "preserved"}


@pytest.mark.asyncio
async def test_disabled_transcription_is_needs_review_not_fake_success(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    await app.state.asset_processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )
    detail = await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["asset"]["state"] == "needs_review"
    assert detail.json()["asset"]["error_category"] == "transcription_disabled"
    assert detail.json()["transcript"] is None
    transcript = await client.get(
        f"/api/v1/assets/{body['asset']['id']}/transcript", headers=headers
    )
    assert transcript.status_code == 404


@pytest.mark.asyncio
async def test_transcription_timeout_fails_with_sanitized_category(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    await process_with_gateway(
        app,
        body,
        TranscriptionGateway(TimeoutTranscriptionAdapter(), max_retries=0),
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "failed"
    assert detail["asset"]["error_category"] == "transcription_timeout"
    assert detail["asset"]["error_message"] == "transcription provider failed"
    assert "private provider" not in str(detail)


@pytest.mark.asyncio
async def test_silent_video_reaches_needs_review_with_verified_metadata(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("owner")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["silent_video"]),
    )
    body = upload.json()
    await app.state.asset_processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "needs_review"
    assert detail["asset"]["error_category"] == "no_audio_stream"
    assert detail["metadata"]["has_audio"] is False
    assert detail["metadata"]["has_video"] is True


@pytest.mark.asyncio
async def test_unsupported_codec_is_quarantined_for_review(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["unsupported_audio"]),
    )
    assert upload.status_code == 201
    body = upload.json()
    await app.state.asset_processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "needs_review"
    assert detail["asset"]["error_category"] == "unsupported_audio_codec"
    assert detail["asset"]["metadata_verified"] is False


@pytest.mark.asyncio
async def test_corrupt_container_fails_safely_after_signature_acceptance(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    corrupt = b"\x00\x00\x00\x18ftypisom" + b"malformed-container" * 20
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        files={"file": ("corrupt.mp4", corrupt, "video/mp4")},
        data={
            "permission_confirmed": "true",
            "rights_note": "I own this synthetic corrupt fixture and authorize its processing.",
        },
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    await app.state.asset_processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "failed"
    assert detail["asset"]["error_category"] == "ffprobe_failed"
    assert "pytest" not in detail["asset"]["error_message"]
    assert detail["metadata"] is None


@pytest.mark.asyncio
async def test_cancelled_queued_asset_never_processes(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    cancelled = await client.post(f"/api/v1/assets/{body['asset']['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    await app.state.asset_processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "cancelled"
    assert detail["metadata"] is None


@pytest.mark.asyncio
async def test_retry_requeues_same_job_with_bounded_attempts(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    message = QueueMessage(
        job_id=UUID(body["processing_job_id"]),
        tenant_id=UUID(body["asset"]["tenant_id"]),
        kind="asset.process",
    )
    await app.state.asset_processor.process_job(message)
    retry = await client.post(f"/api/v1/assets/{body['asset']['id']}/retry", headers=headers)
    assert retry.status_code == 202, retry.text
    assert retry.json()["state"] == "queued"
    assert retry.json()["processing_job_id"] == body["processing_job_id"]
    await app.state.asset_processor.process_job(message)
    job = await client.get(f"/api/v1/jobs/{body['processing_job_id']}", headers=headers)
    assert job.status_code == 200
    assert job.json()["attempts"] == 2
    assert job.json()["state"] == "needs_review"
    stored = await app.state.storage.list(UUID(body["asset"]["tenant_id"]))
    assert len(stored) == 2
    assert len({item.key for item in stored}) == 2
    assets = await client.get("/api/v1/assets", headers=headers)
    assert [item["id"] for item in assets.json()] == [body["asset"]["id"]]


@pytest.mark.asyncio
async def test_derivative_storage_failure_marks_processing_failed_safely(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    upload = await client.post(
        "/api/v1/assets/upload",
        headers=headers,
        **upload_payload(media_files["audio"]),
    )
    body = upload.json()
    real_storage = app.state.storage

    class FailingDerivativeStorage:
        async def copy_to(self, *args: Any, **kwargs: Any) -> Any:
            return await real_storage.copy_to(*args, **kwargs)

        async def put_file(self, *_: Any, **__: Any) -> Any:
            raise OSError(errno.ENOSPC, "No space left on /private/tenant-volume")

    processor = AssetProcessingService(
        session_factory=app.state.session_factory,
        storage=FailingDerivativeStorage(),  # type: ignore[arg-type]
        media_tools=app.state.media_tools,
        transcription=TranscriptionGateway(ValidTranscriptionAdapter(), max_retries=0),
        settings=app.state.settings,
    )
    await processor.process_job(
        QueueMessage(
            job_id=UUID(body["processing_job_id"]),
            tenant_id=UUID(body["asset"]["tenant_id"]),
            kind="asset.process",
        )
    )
    detail = (await client.get(f"/api/v1/assets/{body['asset']['id']}", headers=headers)).json()
    assert detail["asset"]["state"] == "failed"
    assert detail["asset"]["error_category"] == "storage_processing_failure"
    assert detail["asset"]["error_message"] == "private media storage failed during processing"
    assert "/private/tenant-volume" not in str(detail)


@pytest.mark.asyncio
async def test_queue_failure_returns_safe_503_but_keeps_durable_job(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    original_queue = app.state.queue

    class FailingQueue:
        async def enqueue(self, *_: Any, **__: Any) -> None:
            raise ConnectionError("redis://credential@private-host")

    app.state.queue = FailingQueue()
    try:
        response = await client.post(
            "/api/v1/assets/upload",
            headers=headers,
            **upload_payload(media_files["audio"]),
        )
    finally:
        app.state.queue = original_queue
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "queue_unavailable"
    assert "credential" not in response.text
    listing = await client.get("/api/v1/assets", headers=headers)
    assert len(listing.json()) == 1
    assert listing.json()[0]["state"] == "queued"
    assert listing.json()[0]["processing_job_id"] is not None


@pytest.mark.asyncio
async def test_upload_storage_failure_is_sanitized_and_does_not_create_asset(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, app, _ = app_client
    headers = await auth_headers("editor")
    original_storage = app.state.storage

    class FailingStorage:
        async def put_file(self, *_: Any, **__: Any) -> Any:
            raise OSError("/secret/backend/path: credential-like-debug-value")

    app.state.storage = FailingStorage()
    try:
        response = await client.post(
            "/api/v1/assets/upload",
            headers=headers,
            **upload_payload(media_files["audio"]),
        )
    finally:
        app.state.storage = original_storage
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "object_storage_failure"
    assert "secret" not in response.text
    listing = await client.get("/api/v1/assets", headers=headers)
    assert listing.json() == []


@pytest.mark.asyncio
async def test_chunked_request_body_limit_cannot_bypass_content_length_check(
    app_client: tuple[Any, Any, dict[str, Any]],
) -> None:
    client, _, _ = app_client

    async def oversized_chunks():
        for _ in range(33):
            yield b"x" * (64 * 1024)

    response = await client.post(
        "/api/v1/auth/login",
        content=oversized_chunks(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


@pytest.mark.asyncio
async def test_streaming_stage_enforces_size_and_removes_partial_file(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="t" * 40,
        max_upload_bytes=1024,
        upload_chunk_bytes=64 * 1024,
        upload_temp_root=tmp_path,
        storage_local_root=tmp_path / "storage",
    )
    upload = UploadFile(
        file=BytesIO(b"R" * 1025),
        filename="large.wav",
        headers=Headers({"content-type": "audio/wav"}),
    )
    with pytest.raises(UploadValidationError) as caught:
        await stage_upload(upload, settings)
    assert caught.value.category == "upload_too_large"
    assert await asyncio.to_thread(lambda: list(tmp_path.glob("clipos-upload-*"))) == []


@pytest.mark.asyncio
async def test_stream_interruption_removes_partial_file(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="t" * 40,
        max_upload_bytes=4096,
        upload_chunk_bytes=64 * 1024,
        upload_temp_root=tmp_path,
        storage_local_root=tmp_path / "storage",
    )

    class InterruptedUpload:
        filename = "interrupted.wav"
        content_type = "audio/wav"

        def __init__(self) -> None:
            self.calls = 0

        async def read(self, _: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"RIFF" + b"x" * 100
            raise ConnectionError("client disconnected")

        async def close(self) -> None:
            return None

    with pytest.raises(UploadValidationError) as caught:
        await stage_upload(InterruptedUpload(), settings)  # type: ignore[arg-type]
    assert caught.value.category == "upload_interrupted"
    assert await asyncio.to_thread(lambda: list(tmp_path.glob("clipos-upload-*"))) == []


def test_semantic_chunks_preserve_exact_offsets_and_unicode(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="t" * 40,
        transcript_chunk_max_chars=200,
        transcript_chunk_max_seconds=20,
        transcript_chunk_pause_seconds=1,
        upload_temp_root=tmp_path,
        storage_local_root=tmp_path / "storage",
    )
    first = "नमस्ते दिल्ली। " + "सुरक्षित मीडिया " * 10 + "।"
    second = "مرحبا بالعالم. " + "تحقق زمني " * 10 + "."
    result = TranscriptionResult(
        text=f"{first} {second}",
        language="hi",
        confidence=0.9,
        segments=[
            TranscriptSegmentData(start_seconds=0, end_seconds=10, text=first),
            TranscriptSegmentData(start_seconds=12, end_seconds=22, text=second),
        ],
        words=[],
    )
    normalized, chunks = build_chunks(result, settings)
    assert normalized == f"{normalize_text(first)} {normalize_text(second)}"
    assert len(chunks) == 2
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(normalized)
    for chunk in chunks:
        assert normalized[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.start_ms < chunk.end_ms
