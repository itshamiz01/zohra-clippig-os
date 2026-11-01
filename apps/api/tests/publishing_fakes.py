from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from clipos.core.enums import PublishingProvider, PublishVisibility
from clipos.gateways.publishing import (
    Destination,
    MissingScopeError,
    ProviderCapabilities,
    PublishMetadata,
    RateLimitedError,
    ReconciliationStatus,
    RemotePublication,
    TokenRefreshError,
    TokenSet,
    UncertainRemoteResultError,
    UploadChunkResult,
    UploadSession,
    YOUTUBE_UPLOAD_SCOPE,
)


class FakeYouTubeAdapter:
    provider = PublishingProvider.YOUTUBE
    required_scopes = frozenset({YOUTUBE_UPLOAD_SCOPE})
    capabilities = ProviderCapabilities(
        oauth=True,
        resumable_upload=True,
        processing_status=True,
        privacy_status=True,
        metadata=True,
        thumbnails=True,
        cancellation=False,
        reconciliation=True,
        analytics_handoff=True,
        supported_visibilities=(
            PublishVisibility.PRIVATE,
            PublishVisibility.UNLISTED,
            PublishVisibility.PUBLIC,
        ),
        limitations=("Deterministic fake; never contacts YouTube.",),
    )

    def __init__(self) -> None:
        self.exchange_scopes = self.required_scopes
        self.destination_id = "UC_PHASE6_FIXTURE"
        self.destination_name = "Phase 6 Test Channel"
        self.access_lifetime_seconds = 3600
        self.fail_refresh = False
        self.rate_limit_once = False
        self.uncertain_once = False
        self.processing_status = "processed"
        self.reconcile_status = ReconciliationStatus.FOUND
        self.initiation_count = 0
        self.upload_call_count = 0
        self.refresh_count = 0
        self.revoked = False
        self.remote_exists = False
        self.last_metadata: PublishMetadata | None = None

    def build_authorization_url(
        self, *, state: str, code_challenge: str, redirect_uri: str
    ) -> str:
        return "https://fake.example/authorize?" + urlencode(
            {
                "state": state,
                "code_challenge": code_challenge,
                "redirect_uri": redirect_uri,
                "scope": " ".join(sorted(self.required_scopes)),
            }
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        scopes = frozenset() if code == "missing-scope" else self.exchange_scopes
        missing = self.required_scopes - scopes
        if missing:
            raise MissingScopeError(missing)
        return TokenSet(
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            expires_at=datetime.now(UTC) + timedelta(seconds=self.access_lifetime_seconds),
            scopes=scopes,
        )

    async def refresh_tokens(self, *, refresh_token: str) -> TokenSet:
        self.refresh_count += 1
        if self.fail_refresh:
            raise TokenRefreshError()
        return TokenSet(
            access_token=f"fake-refreshed-{self.refresh_count}",
            refresh_token=None,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=self.required_scopes,
        )

    async def revoke_token(self, *, token: str) -> None:
        self.revoked = True

    async def validate_destination(self, *, access_token: str) -> Destination:
        return Destination(
            provider_destination_id=self.destination_id,
            display_name=self.destination_name,
            metadata={"fake": True},
        )

    async def initiate_upload(
        self,
        *,
        access_token: str,
        metadata: PublishMetadata,
        content_type: str,
        content_length: int,
    ) -> UploadSession:
        self.initiation_count += 1
        self.last_metadata = metadata
        return UploadSession(
            session_uri=f"https://fake.example/upload/session-{self.initiation_count}",
            sanitized_metadata={"fake": True, "content_length": content_length},
        )

    async def upload_chunk(
        self,
        *,
        access_token: str,
        session_uri: str,
        chunk: bytes,
        offset: int,
        total_bytes: int,
    ) -> UploadChunkResult:
        self.upload_call_count += 1
        if self.rate_limit_once:
            self.rate_limit_once = False
            raise RateLimitedError(30)
        if self.uncertain_once:
            self.uncertain_once = False
            self.remote_exists = True
            raise UncertainRemoteResultError("fixture lost the final response")
        committed = offset + len(chunk)
        complete = committed == total_bytes
        if complete:
            self.remote_exists = True
        return UploadChunkResult(
            committed_bytes=committed,
            complete=complete,
            provider_publication_id="video-phase6" if complete else None,
            provider_url="https://youtu.be/video-phase6" if complete else None,
            sanitized_response={"fake": True, "privacy_status": self.last_metadata.visibility.value},
        )

    async def reconcile(
        self,
        *,
        access_token: str,
        provider_publication_id: str | None,
        session_uri: str | None,
        total_bytes: int,
    ) -> RemotePublication:
        if self.remote_exists and self.reconcile_status == ReconciliationStatus.FOUND:
            return RemotePublication(
                status=ReconciliationStatus.FOUND,
                provider_publication_id=provider_publication_id or "video-phase6",
                provider_url="https://youtu.be/video-phase6",
                processing_status=self.processing_status,
                committed_bytes=total_bytes,
                visibility=self.last_metadata.visibility if self.last_metadata else None,
                sanitized_response={"fake": True, "reconciled": True},
            )
        return RemotePublication(status=self.reconcile_status)

    async def get_processing_status(
        self, *, access_token: str, provider_publication_id: str
    ) -> RemotePublication:
        return RemotePublication(
            status=ReconciliationStatus.FOUND,
            provider_publication_id=provider_publication_id,
            provider_url=f"https://youtu.be/{provider_publication_id}",
            processing_status=self.processing_status,
            visibility=self.last_metadata.visibility if self.last_metadata else None,
            sanitized_response={"fake": True, "processing_status": self.processing_status},
            failure_reason=(
                "fixture processing rejected" if self.processing_status == "failed" else None
            ),
        )

    async def set_thumbnail(
        self,
        *,
        access_token: str,
        provider_publication_id: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        return {"accepted": True, "fake": True}

    async def close(self) -> None:
        return None
