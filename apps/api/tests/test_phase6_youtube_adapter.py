from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from clipos.config import Settings
from clipos.core.enums import PublishVisibility
from clipos.core.logging import sanitize
from clipos.gateways.publishing import (
    MissingScopeError,
    PublishMetadata,
    RateLimitedError,
    ReconciliationStatus,
    TokenCipher,
    TokenEnvelopeError,
    UncertainRemoteResultError,
    YOUTUBE_UPLOAD_SCOPE,
    YouTubePublishingAdapter,
)

pytestmark = pytest.mark.unit


def settings() -> Settings:
    return Settings(
        _env_file=None,
        youtube_client_id="phase6-client",
        youtube_client_secret="phase6-secret",
        publishing_token_encryption_key="a" * 48,
    )


def metadata() -> PublishMetadata:
    return PublishMetadata(
        title="A controlled upload",
        description="Provider-contract fixture",
        tags=("clipos", "phase6"),
        visibility=PublishVisibility.PRIVATE,
        made_for_kids=False,
        category_id="22",
    )


@pytest.mark.asyncio
async def test_token_cipher_round_trip_context_binding_and_tamper_rejection() -> None:
    cipher = TokenCipher("phase6-encryption-material-must-be-long-enough")
    envelope = cipher.encrypt(
        {"access_token": "secret-access", "refresh_token": "secret-refresh"},
        context="tenant-a:connection-a",
    )
    assert "secret-access" not in envelope
    assert cipher.decrypt(envelope, context="tenant-a:connection-a") == {
        "access_token": "secret-access",
        "refresh_token": "secret-refresh",
    }
    with pytest.raises(TokenEnvelopeError):
        cipher.decrypt(envelope, context="tenant-b:connection-a")
    replacement = "A" if envelope[-1] != "A" else "B"
    with pytest.raises(TokenEnvelopeError):
        cipher.decrypt(envelope[:-1] + replacement, context="tenant-a:connection-a")
    sanitized = sanitize(
        {
            "access_token": "secret-access",
            "message": (
                "Bearer bearer-secret https://callback.test?code=oauth-code&state=oauth-state"
            ),
        }
    )
    assert sanitized["access_token"] == "[REDACTED]"
    assert "bearer-secret" not in sanitized["message"]
    assert "oauth-code" not in sanitized["message"]
    assert "oauth-state" not in sanitized["message"]


@pytest.mark.asyncio
async def test_authorization_url_has_exact_redirect_pkce_state_and_least_scope() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        adapter = YouTubePublishingAdapter(settings(), client=client)
        url = adapter.build_authorization_url(
            state="state-value",
            code_challenge="challenge-value",
            redirect_uri="https://clipos.example/api/v1/publishing/oauth/youtube/callback",
        )
    query = parse_qs(urlsplit(url).query)
    assert query["state"] == ["state-value"]
    assert query["redirect_uri"] == [
        "https://clipos.example/api/v1/publishing/oauth/youtube/callback"
    ]
    assert query["code_challenge"] == ["challenge-value"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [YOUTUBE_UPLOAD_SCOPE]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]


@pytest.mark.asyncio
async def test_token_exchange_refresh_and_missing_scope_are_normalized() -> None:
    responses = [
        httpx.Response(
            200,
            json={
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "scope": YOUTUBE_UPLOAD_SCOPE,
                "token_type": "Bearer",
            },
        ),
        httpx.Response(
            200,
            json={"access_token": "access-2", "expires_in": 1800},
        ),
        httpx.Response(
            200,
            json={
                "access_token": "access-3",
                "expires_in": 3600,
                "scope": "openid email",
            },
        ),
    ]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = YouTubePublishingAdapter(settings(), client=client)
        exchanged = await adapter.exchange_code(
            code="authorization-code",
            code_verifier="pkce-verifier",
            redirect_uri="https://clipos.example/callback",
        )
        assert exchanged.refresh_token == "refresh-1"
        assert exchanged.scopes == frozenset({YOUTUBE_UPLOAD_SCOPE})
        refreshed = await adapter.refresh_tokens(refresh_token="refresh-1")
        assert refreshed.access_token == "access-2"
        assert refreshed.refresh_token is None
        assert refreshed.scopes == frozenset({YOUTUBE_UPLOAD_SCOPE})
        with pytest.raises(MissingScopeError):
            await adapter.exchange_code(
                code="second-code",
                code_verifier="second-verifier",
                redirect_uri="https://clipos.example/callback",
            )
    assert b"code_verifier=pkce-verifier" in seen[0].content
    assert b"grant_type=refresh_token" in seen[1].content
    assert b"phase6-secret" in seen[0].content


@pytest.mark.asyncio
async def test_destination_and_resumable_initiation_contract() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"items": [{"id": "channel-1", "snippet": {"title": "Test"}}]},
            )
        return httpx.Response(
            200,
            headers={"Location": "https://upload.youtube.test/resumable/session"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = YouTubePublishingAdapter(settings(), client=client)
        destination = await adapter.validate_destination(access_token="access")
        assert destination.provider_destination_id == "channel-1"
        upload = await adapter.initiate_upload(
            access_token="access",
            metadata=metadata(),
            content_type="video/mp4",
            content_length=1024,
        )
    assert upload.session_uri == "https://upload.youtube.test/resumable/session"
    assert seen[0].headers["Authorization"] == "Bearer access"
    assert seen[1].headers["X-Upload-Content-Length"] == "1024"
    request_body: dict[str, Any] = json.loads(seen[1].content)
    assert request_body["status"]["privacyStatus"] == "private"
    assert request_body["snippet"]["title"] == "A controlled upload"


@pytest.mark.asyncio
async def test_resumable_chunks_parse_308_range_and_final_provider_id() -> None:
    responses = [
        httpx.Response(308, headers={"Range": "bytes=0-2"}),
        httpx.Response(
            201,
            json={
                "id": "video-1",
                "kind": "youtube#video",
                "status": {"privacyStatus": "private", "uploadStatus": "uploaded"},
            },
        ),
    ]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = YouTubePublishingAdapter(settings(), client=client)
        first = await adapter.upload_chunk(
            access_token="access",
            session_uri="https://upload.youtube.test/session",
            chunk=b"abc",
            offset=0,
            total_bytes=6,
        )
        assert first.complete is False
        assert first.committed_bytes == 3
        final = await adapter.upload_chunk(
            access_token="access",
            session_uri="https://upload.youtube.test/session",
            chunk=b"def",
            offset=3,
            total_bytes=6,
        )
    assert final.complete is True
    assert final.provider_publication_id == "video-1"
    assert final.provider_url == "https://youtu.be/video-1"
    assert seen[0].headers["Content-Range"] == "bytes 0-2/6"
    assert seen[1].headers["Content-Range"] == "bytes 3-5/6"


@pytest.mark.asyncio
async def test_rate_limit_and_mutating_server_failure_are_normalized() -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "17"}),
        httpx.Response(503, json={"error": {"message": "unavailable"}}),
    ]

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: responses.pop(0))
    ) as client:
        adapter = YouTubePublishingAdapter(settings(), client=client)
        with pytest.raises(RateLimitedError) as limited:
            await adapter.upload_chunk(
                access_token="access",
                session_uri="https://upload.youtube.test/session",
                chunk=b"abc",
                offset=0,
                total_bytes=3,
            )
        assert limited.value.retry_after_seconds == 17
        with pytest.raises(UncertainRemoteResultError):
            await adapter.upload_chunk(
                access_token="access",
                session_uri="https://upload.youtube.test/session",
                chunk=b"abc",
                offset=0,
                total_bytes=3,
            )


@pytest.mark.asyncio
async def test_reconciliation_processing_and_thumbnail_contracts() -> None:
    responses = [
        httpx.Response(308, headers={"Range": "bytes=0-4"}),
        httpx.Response(
            200,
            json={
                "items": [
                    {
                        "status": {
                            "uploadStatus": "processed",
                            "privacyStatus": "unlisted",
                        },
                        "processingDetails": {"processingStatus": "succeeded"},
                    }
                ]
            },
        ),
        httpx.Response(200, json={"items": [{"default": True}]}),
    ]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = YouTubePublishingAdapter(settings(), client=client)
        progress = await adapter.reconcile(
            access_token="access",
            provider_publication_id=None,
            session_uri="https://upload.youtube.test/session",
            total_bytes=10,
        )
        assert progress.status == ReconciliationStatus.IN_PROGRESS
        assert progress.committed_bytes == 5
        status = await adapter.get_processing_status(
            access_token="access", provider_publication_id="video-1"
        )
        assert status.status == ReconciliationStatus.FOUND
        assert status.processing_status == "processed"
        assert status.visibility == PublishVisibility.UNLISTED
        thumbnail = await adapter.set_thumbnail(
            access_token="access",
            provider_publication_id="video-1",
            content_type="image/jpeg",
            content=b"jpeg-bytes",
        )
    assert thumbnail == {"accepted": True, "http_status": 200}
    assert seen[0].headers["Content-Range"] == "bytes */10"
    assert seen[2].headers["Content-Type"] == "image/jpeg"
