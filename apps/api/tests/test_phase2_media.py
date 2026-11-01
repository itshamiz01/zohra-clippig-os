from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest

from clipos.config import Settings
from clipos.core.enums import MediaKind
from clipos.gateways.transcription import (
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegmentData,
    TranscriptWordData,
)
from clipos.gateways.transcription.adapters import OpenAITranscriptionAdapter
from clipos.gateways.transcription.errors import (
    TranscriptionAuthenticationError,
    TranscriptionResponseError,
    TranscriptionTimeoutError,
    TranscriptionTransientError,
)
from clipos.gateways.transcription.gateway import TranscriptionGateway
from clipos.media.errors import MediaPipelineError, UploadValidationError
from clipos.media.signatures import detect_media_signature, sanitize_display_filename
from clipos.media.tooling import MediaTooling, ToolStatus, _parse_rate
from clipos.storage.local import LocalObjectStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required for Phase 2 media tests",
)


def test_signature_detection_uses_bytes_not_only_declared_type(
    media_files: dict[str, Path],
) -> None:
    wav = media_files["audio"].read_bytes()
    detected = detect_media_signature(
        wav[:4096], filename="voice.wav", declared_content_type="audio/wav"
    )
    assert detected.kind_hint == MediaKind.AUDIO
    assert detected.safe_extension == ".wav"
    assert detected.content_type == "audio/wav"

    with pytest.raises(UploadValidationError, match="extension"):
        detect_media_signature(wav[:4096], filename="voice.mp4", declared_content_type="video/mp4")
    with pytest.raises(UploadValidationError, match="content type"):
        detect_media_signature(wav[:4096], filename="voice.wav", declared_content_type="video/mp4")
    aac = detect_media_signature(
        b"\xff\xf1" + b"\x00" * 30,
        filename="voice.aac",
        declared_content_type="audio/aac",
    )
    assert aac.family == "aac"
    mp3 = detect_media_signature(
        b"\xff\xfb" + b"\x00" * 30,
        filename="voice.mp3",
        declared_content_type="audio/mpeg",
    )
    assert mp3.family == "mp3"
    with pytest.raises(UploadValidationError, match="signature"):
        detect_media_signature(
            b"not a media file", filename="voice.wav", declared_content_type="audio/wav"
        )


def test_filename_sanitizer_removes_paths_controls_and_bidi() -> None:
    assert sanitize_display_filename("../../private/voice.wav") == "voice.wav"
    value = sanitize_display_filename("\u202e secret\x00 name .wav")
    assert "\u202e" not in value
    assert "\x00" not in value
    assert "/" not in value


def test_unusual_or_missing_frame_rates_are_handled_safely() -> None:
    assert _parse_rate("30000/1001") == pytest.approx(29.97003, rel=1e-5)
    assert _parse_rate("0/0") is None
    assert _parse_rate("24000/0") is None
    assert _parse_rate("N/A") is None
    assert _parse_rate("not-a-rate") is None
    assert _parse_rate(float("inf")) is None


@pytest.mark.asyncio
async def test_real_probe_and_normalized_audio_extraction(
    tmp_path: Path, media_files: dict[str, Path]
) -> None:
    settings = Settings(
        secret_key="t" * 40,
        storage_local_root=tmp_path / "storage",
        upload_temp_root=tmp_path / "tmp",
    )
    tooling = MediaTooling(settings)
    status = await tooling.check()
    assert status.available is True
    assert status.ffmpeg_version
    probe = await tooling.probe(media_files["video"])
    assert probe.has_video is True
    assert probe.has_audio is True
    assert probe.media_kind == MediaKind.VIDEO
    assert 1 <= probe.duration_seconds <= 2
    assert probe.video_codec == "h264"
    assert probe.audio_codec == "aac"
    destination = tmp_path / "derived" / "audio.wav"
    await tooling.extract_audio(media_files["video"], destination, probe.duration_seconds)
    derived = await tooling.probe(destination)
    assert derived.has_audio is True
    assert derived.has_video is False
    assert derived.audio_codec == "pcm_s16le"
    assert derived.audio_channels == 1
    assert derived.audio_sample_rate == 16000


@pytest.mark.asyncio
async def test_probe_rejects_silent_video_and_corrupt_media(
    tmp_path: Path, media_files: dict[str, Path]
) -> None:
    settings = Settings(
        secret_key="t" * 40,
        storage_local_root=tmp_path / "storage",
        upload_temp_root=tmp_path / "tmp",
    )
    tooling = MediaTooling(settings)
    await tooling.check()
    silent = await tooling.probe(media_files["silent_video"])
    assert silent.has_video is True
    assert silent.has_audio is False
    with pytest.raises(MediaPipelineError) as unsupported:
        await tooling.probe(media_files["unsupported_audio"])
    assert unsupported.value.category == "unsupported_audio_codec"
    assert unsupported.value.needs_review is True

    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"broken" * 20)
    with pytest.raises(MediaPipelineError) as caught:
        await tooling.probe(corrupt)
    assert caught.value.category == "ffprobe_failed"
    assert str(corrupt) not in caught.value.safe_message


@pytest.mark.asyncio
async def test_missing_media_binary_is_reported_without_crashing(tmp_path: Path) -> None:
    settings = Settings(
        secret_key="t" * 40,
        storage_local_root=tmp_path / "storage",
        upload_temp_root=tmp_path / "tmp",
        ffmpeg_path="/definitely/missing/ffmpeg",
        ffprobe_path="/definitely/missing/ffprobe",
    )
    status = await MediaTooling(settings).check()
    assert status.available is False
    assert status.error_category == "media_tool_unavailable"


@pytest.mark.asyncio
async def test_probe_enforces_duration_and_dimension_limits(
    tmp_path: Path, media_files: dict[str, Path]
) -> None:
    duration_settings = Settings(
        secret_key="t" * 40,
        storage_local_root=tmp_path / "duration-storage",
        upload_temp_root=tmp_path / "duration-tmp",
        max_media_duration_seconds=1,
    )
    duration_tooling = MediaTooling(duration_settings)
    await duration_tooling.check()
    with pytest.raises(MediaPipelineError) as duration_error:
        await duration_tooling.probe(media_files["video"])
    assert duration_error.value.category == "duration_limit"

    dimension_settings = Settings(
        secret_key="t" * 40,
        storage_local_root=tmp_path / "dimension-storage",
        upload_temp_root=tmp_path / "dimension-tmp",
        max_media_dimension=240,
    )
    dimension_tooling = MediaTooling(dimension_settings)
    await dimension_tooling.check()
    with pytest.raises(MediaPipelineError) as dimension_error:
        await dimension_tooling.probe(media_files["video"])
    assert dimension_error.value.category == "dimension_limit"


@pytest.mark.asyncio
async def test_ffprobe_timeout_is_bounded(tmp_path: Path) -> None:
    sleeping_probe = tmp_path / "sleeping-ffprobe"
    sleeping_probe.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    sleeping_probe.chmod(0o700)
    media = tmp_path / "input.wav"
    media.write_bytes(b"RIFF" + b"0" * 100)
    settings = Settings(
        secret_key="t" * 40,
        storage_local_root=tmp_path / "storage",
        upload_temp_root=tmp_path / "tmp",
        ffprobe_path=str(sleeping_probe),
        media_probe_timeout_seconds=1,
    )
    tooling = MediaTooling(settings)
    tooling.status = ToolStatus(
        available=True,
        ffmpeg_version="test",
        ffprobe_version="test",
    )
    with pytest.raises(MediaPipelineError) as caught:
        await tooling.probe(media)
    assert caught.value.category == "media_process_timeout"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_ffmpeg_extraction_timeout_is_bounded_and_cleans_output(tmp_path: Path) -> None:
    sleeping_ffmpeg = tmp_path / "sleeping-ffmpeg"
    sleeping_ffmpeg.write_text("#!/bin/sh\nsleep 10\n", encoding="utf-8")
    sleeping_ffmpeg.chmod(0o700)
    source = tmp_path / "input.wav"
    source.write_bytes(b"RIFF" + b"0" * 100)
    destination = tmp_path / "derived" / "audio.wav"
    settings = Settings(
        secret_key="t" * 40,
        storage_local_root=tmp_path / "storage",
        upload_temp_root=tmp_path / "tmp",
        ffmpeg_path=str(sleeping_ffmpeg),
        media_extract_timeout_seconds=5,
    )
    tooling = MediaTooling(settings)
    tooling.status = ToolStatus(
        available=True,
        ffmpeg_version="test",
        ffprobe_version="test",
    )
    with pytest.raises(MediaPipelineError) as caught:
        await tooling.extract_audio(source, destination, 1.0)
    assert caught.value.category == "media_process_timeout"
    assert caught.value.retryable is True
    assert destination.exists() is False


@pytest.mark.asyncio
async def test_local_storage_is_tenant_scoped_and_checksum_verified(tmp_path: Path) -> None:
    from uuid import uuid4

    storage = LocalObjectStorage(tmp_path / "storage")
    tenant_a = uuid4()
    tenant_b = uuid4()
    tenant_c = uuid4()
    source = tmp_path / "source.bin"
    source.write_bytes(b"private media")
    info = await storage.put_file(
        tenant_a,
        "assets/one/original.bin",
        source,
        content_type="application/octet-stream",
    )
    assert info.size == len(b"private media")
    assert await storage.head(tenant_a, info.key) is not None
    assert await storage.head(tenant_b, info.key) is None
    with pytest.raises(FileNotFoundError):
        await storage.copy_to(tenant_b, info.key, tmp_path / "leak.bin")
    with pytest.raises(ValueError, match="normalized relative path"):
        await storage.put_file(tenant_a, "../escape", source)
    outside = tmp_path / "outside-storage"
    outside.mkdir()
    (tmp_path / "storage" / str(tenant_c)).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="tenant storage root escaped"):
        await storage.put_file(tenant_c, "asset.bin", source)

    destination = tmp_path / "materialized.bin"
    copied = await storage.copy_to(tenant_a, info.key, destination)
    assert copied.checksum_sha256 == info.checksum_sha256
    assert destination.read_bytes() == b"private media"

    stored_path = tmp_path / "storage" / str(tenant_a) / "assets/one/original.bin"
    stored_path.write_bytes(b"tampered media")
    tampered_destination = tmp_path / "tampered.bin"
    with pytest.raises(OSError, match="checksum verification"):
        await storage.copy_to(tenant_a, info.key, tampered_destination)
    assert not tampered_destination.exists()


class _ValidAdapter:
    provider_name = "fixture"
    model_name = "fixture-timestamps-v1"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        assert request.audio_path.exists()
        return TranscriptionResult(
            text="Hello Delhi. Secure media works.",
            language="en",
            confidence=0.93,
            segments=[
                TranscriptSegmentData(
                    start_seconds=0,
                    end_seconds=min(0.55, request.duration_seconds),
                    text="Hello Delhi.",
                    confidence=0.95,
                    speaker_label="speaker_0",
                ),
                TranscriptSegmentData(
                    start_seconds=min(0.55, request.duration_seconds - 0.1),
                    end_seconds=request.duration_seconds,
                    text="Secure media works.",
                    confidence=0.91,
                    speaker_label="speaker_0",
                ),
            ],
            words=[
                TranscriptWordData(
                    start_seconds=0,
                    end_seconds=0.2,
                    text="Hello",
                    confidence=0.95,
                    speaker_label="speaker_0",
                ),
                TranscriptWordData(
                    start_seconds=0.21,
                    end_seconds=min(0.5, request.duration_seconds),
                    text="Delhi",
                    confidence=0.94,
                    speaker_label="speaker_0",
                ),
            ],
            diarization_available=True,
            raw_provider_output={"fixture": True, "version": 1},
        )

    async def close(self) -> None:
        return None


class _MalformedAdapter:
    provider_name = "fixture"
    model_name = "malformed"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(
            text="bad",
            language="en",
            confidence=0.8,
            segments=[
                TranscriptSegmentData(
                    start_seconds=0,
                    end_seconds=request.duration_seconds + 10,
                    text="outside media",
                )
            ],
            words=[],
            raw_provider_output={"secret": "not exposed in the error"},
        )

    async def close(self) -> None:
        return None


class _FlakyAdapter:
    provider_name = "fixture"
    model_name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        self.calls += 1
        if self.calls == 1:
            raise TranscriptionTransientError("temporary fixture outage")
        return TranscriptionResult(
            text="Recovered safely.",
            language="en",
            confidence=0.9,
            segments=[
                TranscriptSegmentData(
                    start_seconds=0,
                    end_seconds=request.duration_seconds,
                    text="Recovered safely.",
                )
            ],
        )

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_transcription_gateway_retries_only_transient_failure() -> None:
    adapter = _FlakyAdapter()
    gateway = TranscriptionGateway(adapter, max_retries=1)
    result = await gateway.transcribe(
        TranscriptionRequest(audio_path=Path("audio.wav"), duration_seconds=1)
    )
    assert result.text == "Recovered safely."
    assert adapter.calls == 2


@pytest.mark.asyncio
async def test_transcription_gateway_caps_timestamp_row_counts(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixture")
    request = TranscriptionRequest(audio_path=audio, duration_seconds=1.2)
    gateway = TranscriptionGateway(_ValidAdapter(), max_retries=0, max_segments=1)
    with pytest.raises(TranscriptionResponseError, match="too many timestamp rows"):
        await gateway.transcribe(request)
    gateway = TranscriptionGateway(_ValidAdapter(), max_retries=0, max_text_chars=10)
    with pytest.raises(TranscriptionResponseError, match="excessive transcript text"):
        await gateway.transcribe(request)


@pytest.mark.asyncio
async def test_transcription_gateway_rejects_out_of_bounds_timestamps() -> None:
    gateway = TranscriptionGateway(_MalformedAdapter(), max_retries=0)
    request = TranscriptionRequest(audio_path=Path("audio.wav"), duration_seconds=1)
    with pytest.raises(TranscriptionResponseError) as caught:
        await gateway.transcribe(request)
    assert caught.value.category == "transcription_response_invalid"
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_openai_adapter_parses_verbose_timestamps_without_live_claim(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF" + b"fixture")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/audio/transcriptions")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            headers={"x-request-id": "provider-request"},
            json={
                "text": "Hello world.",
                "language": "en",
                "segments": [
                    {
                        "start": 0,
                        "end": 1,
                        "text": "Hello world.",
                        "avg_logprob": -0.1,
                    }
                ],
                "words": [
                    {"start": 0, "end": 0.4, "word": "Hello"},
                    {"start": 0.5, "end": 1, "word": "world"},
                ],
            },
        )

    adapter = OpenAITranscriptionAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )
    result = await adapter.transcribe(TranscriptionRequest(audio_path=audio, duration_seconds=1))
    await adapter.close()
    assert result.text == "Hello world."
    assert result.language == "en"
    assert result.provider_request_id == "provider-request"
    assert len(result.segments) == 1
    assert len(result.words) == 2
    assert result.confidence == pytest.approx(0.9048, rel=1e-3)


@pytest.mark.asyncio
async def test_openai_adapter_bounds_response_and_hides_provider_body(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF" + b"fixture")

    async def oversized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b"x" * 200 + b"}")

    adapter = OpenAITranscriptionAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=10,
        response_max_bytes=32,
        transport=httpx.MockTransport(oversized),
    )
    with pytest.raises(TranscriptionResponseError, match="configured limit"):
        await adapter.transcribe(TranscriptionRequest(audio_path=audio, duration_seconds=1))
    await adapter.close()

    async def unauthorized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"secret": "provider-debug-body"})

    adapter = OpenAITranscriptionAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=10,
        transport=httpx.MockTransport(unauthorized),
    )
    with pytest.raises(TranscriptionAuthenticationError) as caught:
        await adapter.transcribe(TranscriptionRequest(audio_path=audio, duration_seconds=1))
    await adapter.close()
    assert "provider-debug-body" not in str(caught.value)

    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider private timeout detail", request=request)

    adapter = OpenAITranscriptionAdapter(
        api_key="test-key",
        model="fixture-model",
        base_url="https://provider.invalid/v1",
        timeout_seconds=10,
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(TranscriptionTimeoutError) as timeout_error:
        await adapter.transcribe(TranscriptionRequest(audio_path=audio, duration_seconds=1))
    await adapter.close()
    assert "private timeout detail" not in str(timeout_error.value)


def upload_payload(path: Path, filename: str | None = None) -> dict[str, Any]:
    return {
        "files": {
            "file": (
                filename or path.name,
                path.read_bytes(),
                "audio/wav" if path.suffix == ".wav" else "video/mp4",
            )
        },
        "data": {
            "permission_confirmed": "true",
            "rights_note": "I own this synthetic fixture and authorize processing.",
        },
    }


@pytest.mark.asyncio
async def test_upload_duplicate_search_permissions_and_tenant_isolation(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, _, _ = app_client
    owner_headers = await auth_headers("owner")
    viewer_headers = await auth_headers("viewer")
    bravo_headers = await auth_headers("bravo_owner")
    first = await client.post(
        "/api/v1/assets/upload",
        headers=owner_headers,
        **upload_payload(media_files["audio"]),
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["duplicate"] is False
    assert body["asset"]["state"] == "queued"
    assert body["asset"]["permission_confirmed"] is True
    assert body["processing_job_id"]

    duplicate = await client.post(
        "/api/v1/assets/upload",
        headers=owner_headers,
        **upload_payload(media_files["audio"]),
    )
    assert duplicate.status_code == 200
    assert duplicate.headers["x-clipos-duplicate"] == "true"
    assert duplicate.json()["asset"]["id"] == body["asset"]["id"]

    listing = await client.get("/api/v1/assets?q=tone", headers=viewer_headers)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [body["asset"]["id"]]

    forbidden = await client.post(
        "/api/v1/assets/upload",
        headers=viewer_headers,
        **upload_payload(media_files["video"]),
    )
    assert forbidden.status_code == 403
    cross_tenant = await client.get(
        f"/api/v1/assets/{body['asset']['id']}",
        headers=bravo_headers,
    )
    assert cross_tenant.status_code == 404
    other_tenant_upload = await client.post(
        "/api/v1/assets/upload",
        headers=bravo_headers,
        **upload_payload(media_files["audio"]),
    )
    assert other_tenant_upload.status_code == 201
    assert other_tenant_upload.json()["asset"]["id"] != body["asset"]["id"]

    audit = await client.get("/api/v1/diagnostics/audit", headers=owner_headers)
    assert audit.status_code == 200
    actions = [event["action"] for event in audit.json()]
    assert "asset.uploaded" in actions
    assert "asset.duplicate_detected" in actions
    assert all("rights_note" not in event["event_data"] for event in audit.json())


@pytest.mark.asyncio
async def test_upload_rejects_missing_rights_and_malformed_bytes(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    media_files: dict[str, Path],
) -> None:
    client, _, _ = app_client
    editor_headers = await auth_headers("editor")
    no_rights = upload_payload(media_files["audio"])
    no_rights["data"]["permission_confirmed"] = "false"
    response = await client.post(
        "/api/v1/assets/upload",
        headers=editor_headers,
        **no_rights,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "permission_not_confirmed"

    malformed = await client.post(
        "/api/v1/assets/upload",
        headers=editor_headers,
        files={"file": ("malware.mp4", b"not-media", "video/mp4")},
        data={
            "permission_confirmed": "true",
            "rights_note": "I am authorized to test this malformed fixture.",
        },
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "unsupported_signature"


@pytest.mark.asyncio
async def test_url_ingestion_is_explicitly_disabled_without_network_access(
    app_client: tuple[Any, Any, dict[str, Any]],
    auth_headers: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = app_client
    editor_headers = await auth_headers("editor")
    sources = await client.get("/api/v1/assets/sources", headers=editor_headers)
    assert sources.status_code == 200
    capabilities = {item["source"]: item for item in sources.json()}
    assert capabilities["local_upload"]["enabled"] is True
    assert capabilities["local_upload"]["mode"] == "real_tested"
    assert capabilities["url"]["enabled"] is False
    assert capabilities["url"]["mode"] == "disabled"

    def no_network(*_: Any, **__: Any) -> None:
        raise AssertionError("URL ingestion must not make a network request")

    monkeypatch.setattr("socket.create_connection", no_network)
    monkeypatch.setattr("socket.getaddrinfo", no_network)
    candidate_urls = [
        "https://example.com/redirecting-media.mp4",
        "http://rebind.test/media.mp4",
        "http://user:password@example.com/private.mp4",
        "http://127.0.0.1/private.mp4",
        "http://10.0.0.1/private.mp4",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/private.mp4",
    ]
    for url in candidate_urls:
        response = await client.post(
            "/api/v1/assets/from-url",
            headers=editor_headers,
            json={
                "url": url,
                "permission_confirmed": True,
                "rights_note": "I own this source and authorize processing it.",
            },
        )
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "url_ingestion_disabled"
