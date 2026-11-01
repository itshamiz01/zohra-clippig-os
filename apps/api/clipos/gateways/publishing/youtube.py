from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from clipos.config import Settings
from clipos.core.enums import PublishingProvider, PublishVisibility
from clipos.gateways.publishing.contracts import (
    Destination,
    ProviderCapabilities,
    PublishMetadata,
    ReconciliationStatus,
    RemotePublication,
    TokenSet,
    UploadChunkResult,
    UploadSession,
)
from clipos.gateways.publishing.errors import (
    MissingScopeError,
    OAuthExchangeError,
    PublishingAdapterError,
    RateLimitedError,
    TokenRefreshError,
    UncertainRemoteResultError,
)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if value and value.isdigit():
        return min(int(value), 86_400)
    return None


def _scope_set(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(item for item in value.split() if item)
    if isinstance(value, list):
        return frozenset(str(item) for item in value if item)
    return frozenset()


class YouTubePublishingAdapter:
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
        limitations=(
            "Real OAuth and uploads remain unverified until an operator completes the "
            "provider test.",
            "YouTube resumable sessions cannot be cancelled through the Data API.",
            "Public visibility is additionally gated by exact ClipOS human approval.",
        ),
    )

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not settings.youtube_client_id or not settings.youtube_client_secret:
            raise ValueError("YouTube OAuth client configuration is incomplete")
        self.settings = settings
        self._client_id = settings.youtube_client_id
        self._client_secret = settings.youtube_client_secret.get_secret_value()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.publishing_http_timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "Zohra-ClipOS/0.6"},
        )

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(sorted(self.required_scopes)),
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self.settings.youtube_authorization_url}?{query}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> TokenSet:
        try:
            response = await self._client.post(
                self.settings.youtube_token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "code_verifier": code_verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OAuthExchangeError(
                "oauth_exchange_unavailable",
                "the provider token endpoint could not be reached",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise OAuthExchangeError(
                "oauth_exchange_rejected",
                "the provider rejected the authorization code exchange",
                manual_next_step="Start a new connection attempt and approve the requested scope.",
            )
        payload = _json_object(response)
        return self._parse_token_payload(payload, refresh=False)

    async def refresh_tokens(self, *, refresh_token: str) -> TokenSet:
        try:
            response = await self._client.post(
                self.settings.youtube_token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise TokenRefreshError("the provider token endpoint could not be reached") from exc
        if response.status_code >= 400:
            raise TokenRefreshError()
        return self._parse_token_payload(_json_object(response), refresh=True)

    def _parse_token_payload(self, payload: dict[str, Any], *, refresh: bool) -> TokenSet:
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(access_token, str) or not access_token:
            if refresh:
                raise TokenRefreshError("the provider refresh response contained no access token")
            raise OAuthExchangeError(
                "oauth_exchange_invalid_response",
                "the provider token response contained no access token",
            )
        try:
            if not isinstance(expires_in, (str, int, float)):
                raise TypeError
            lifetime = max(60, min(int(expires_in), 86_400))
        except (TypeError, ValueError) as exc:
            if refresh:
                raise TokenRefreshError(
                    "the provider refresh response had an invalid expiry"
                ) from exc
            raise OAuthExchangeError(
                "oauth_exchange_invalid_response",
                "the provider token response had an invalid expiry",
            ) from exc
        scopes = _scope_set(payload.get("scope"))
        if refresh and not scopes:
            scopes = self.required_scopes
        missing = self.required_scopes - scopes
        if missing:
            raise MissingScopeError(missing)
        refresh_value = payload.get("refresh_token")
        return TokenSet(
            access_token=access_token,
            refresh_token=refresh_value if isinstance(refresh_value, str) else None,
            expires_at=datetime.now(UTC) + timedelta(seconds=lifetime),
            scopes=scopes,
            token_type=str(payload.get("token_type") or "Bearer"),
        )

    async def revoke_token(self, *, token: str) -> None:
        try:
            response = await self._client.post(
                self.settings.youtube_revocation_url,
                data={"token": token},
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise PublishingAdapterError(
                "provider_revocation_unavailable",
                "the provider revocation endpoint could not be reached",
                retryable=True,
            ) from exc
        if response.status_code not in {200, 400}:
            self._raise_response_error(response, operation="token revocation")

    async def validate_destination(self, *, access_token: str) -> Destination:
        try:
            response = await self._client.get(
                f"{self.settings.youtube_api_base_url}/channels",
                params={"part": "id,snippet", "mine": "true"},
                headers=self._authorization(access_token),
            )
        except httpx.HTTPError as exc:
            raise PublishingAdapterError(
                "destination_validation_unavailable",
                "the provider destination could not be validated",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            self._raise_response_error(response, operation="destination validation")
        payload = _json_object(response)
        items = payload.get("items")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise PublishingAdapterError(
                "destination_not_found",
                "the authorized Google account has no accessible YouTube channel",
                manual_next_step="Use an account with an eligible YouTube channel.",
            )
        item: dict[str, Any] = items[0]
        destination_id = item.get("id")
        snippet_value = item.get("snippet")
        snippet: dict[str, Any] = snippet_value if isinstance(snippet_value, dict) else {}
        if not isinstance(destination_id, str) or not destination_id:
            raise PublishingAdapterError(
                "destination_invalid_response",
                "the provider returned an invalid destination identifier",
            )
        title = snippet.get("title")
        return Destination(
            provider_destination_id=destination_id,
            display_name=title if isinstance(title, str) and title else destination_id,
            metadata={"kind": "youtube_channel"},
        )

    async def initiate_upload(
        self,
        *,
        access_token: str,
        metadata: PublishMetadata,
        content_type: str,
        content_length: int,
    ) -> UploadSession:
        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": list(metadata.tags),
                "categoryId": metadata.category_id,
            },
            "status": {
                "privacyStatus": metadata.visibility.value,
                "selfDeclaredMadeForKids": metadata.made_for_kids,
            },
        }
        try:
            response = await self._client.post(
                f"{self.settings.youtube_upload_base_url}/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                json=body,
                headers={
                    **self._authorization(access_token),
                    "Accept": "application/json",
                    "X-Upload-Content-Length": str(content_length),
                    "X-Upload-Content-Type": content_type,
                },
            )
        except httpx.HTTPError as exc:
            raise PublishingAdapterError(
                "upload_initiation_unavailable",
                "the provider upload session could not be initiated",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            self._raise_response_error(response, operation="upload initiation")
        location = response.headers.get("Location")
        if not location:
            raise PublishingAdapterError(
                "upload_session_missing",
                "the provider did not return a resumable upload session",
            )
        parsed = urlsplit(location)
        configured = urlsplit(self.settings.youtube_upload_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise PublishingAdapterError(
                "upload_session_invalid",
                "the provider returned an invalid upload session location",
            )
        if configured.scheme == "https" and parsed.scheme != "https":
            raise PublishingAdapterError(
                "upload_session_insecure",
                "the provider returned an insecure upload session location",
            )
        return UploadSession(
            session_uri=location,
            sanitized_metadata={"resumable": True, "content_length": content_length},
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
        if not chunk:
            raise ValueError("an upload chunk must not be empty")
        end = offset + len(chunk) - 1
        try:
            response = await self._client.put(
                session_uri,
                content=chunk,
                headers={
                    **self._authorization(access_token),
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{total_bytes}",
                },
            )
        except httpx.HTTPError as exc:
            raise UncertainRemoteResultError(
                "the upload connection ended before the provider confirmed the committed range"
            ) from exc
        if response.status_code == 308:
            committed = self._committed_bytes(response, fallback=offset)
            return UploadChunkResult(
                committed_bytes=committed,
                complete=False,
                sanitized_response={"http_status": 308, "committed_bytes": committed},
            )
        if response.status_code in {200, 201}:
            payload = _json_object(response)
            provider_id = payload.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                raise UncertainRemoteResultError(
                    "the provider accepted the final chunk but returned no video identifier"
                )
            return UploadChunkResult(
                committed_bytes=total_bytes,
                complete=True,
                provider_publication_id=provider_id,
                provider_url=f"https://youtu.be/{provider_id}",
                sanitized_response=self._video_response_metadata(payload),
            )
        self._raise_response_error(response, operation="upload chunk", mutating=True)
        raise AssertionError("unreachable")

    async def reconcile(
        self,
        *,
        access_token: str,
        provider_publication_id: str | None,
        session_uri: str | None,
        total_bytes: int,
    ) -> RemotePublication:
        if provider_publication_id:
            return await self.get_processing_status(
                access_token=access_token,
                provider_publication_id=provider_publication_id,
            )
        if not session_uri:
            return RemotePublication(status=ReconciliationStatus.ABSENT)
        try:
            response = await self._client.put(
                session_uri,
                content=b"",
                headers={
                    **self._authorization(access_token),
                    "Content-Length": "0",
                    "Content-Range": f"bytes */{total_bytes}",
                },
            )
        except httpx.HTTPError:
            return RemotePublication(
                status=ReconciliationStatus.UNKNOWN,
                failure_reason="the resumable session could not be probed",
            )
        if response.status_code == 308:
            committed = self._committed_bytes(response, fallback=0)
            return RemotePublication(
                status=ReconciliationStatus.IN_PROGRESS,
                committed_bytes=committed,
                processing_status="uploading",
                sanitized_response={"http_status": 308, "committed_bytes": committed},
            )
        if response.status_code in {200, 201}:
            payload = _json_object(response)
            provider_id = payload.get("id")
            return RemotePublication(
                status=(
                    ReconciliationStatus.FOUND
                    if isinstance(provider_id, str) and provider_id
                    else ReconciliationStatus.UNKNOWN
                ),
                provider_publication_id=(provider_id if isinstance(provider_id, str) else None),
                provider_url=(
                    f"https://youtu.be/{provider_id}"
                    if isinstance(provider_id, str) and provider_id
                    else None
                ),
                processing_status="uploaded",
                committed_bytes=total_bytes,
                sanitized_response=self._video_response_metadata(payload),
            )
        if response.status_code in {404, 410}:
            return RemotePublication(status=ReconciliationStatus.ABSENT)
        if response.status_code == 429:
            raise RateLimitedError(_retry_after(response))
        if response.status_code >= 500:
            return RemotePublication(
                status=ReconciliationStatus.UNKNOWN,
                failure_reason="the provider could not confirm the resumable session",
            )
        self._raise_response_error(response, operation="upload reconciliation")
        raise AssertionError("unreachable")

    async def get_processing_status(
        self,
        *,
        access_token: str,
        provider_publication_id: str,
    ) -> RemotePublication:
        try:
            response = await self._client.get(
                f"{self.settings.youtube_api_base_url}/videos",
                params={"part": "status,processingDetails", "id": provider_publication_id},
                headers=self._authorization(access_token),
            )
        except httpx.HTTPError:
            return RemotePublication(
                status=ReconciliationStatus.UNKNOWN,
                provider_publication_id=provider_publication_id,
                provider_url=f"https://youtu.be/{provider_publication_id}",
                failure_reason="the provider processing status could not be read",
            )
        if response.status_code == 404:
            return RemotePublication(status=ReconciliationStatus.ABSENT)
        if response.status_code >= 400:
            self._raise_response_error(response, operation="processing status")
        payload = _json_object(response)
        items = payload.get("items")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return RemotePublication(status=ReconciliationStatus.ABSENT)
        item: dict[str, Any] = items[0]
        status_value = item.get("status")
        status: dict[str, Any] = status_value if isinstance(status_value, dict) else {}
        processing_value = item.get("processingDetails")
        processing: dict[str, Any] = processing_value if isinstance(processing_value, dict) else {}
        upload_status = status.get("uploadStatus")
        privacy = status.get("privacyStatus")
        visibility: PublishVisibility | None = None
        if privacy in {item.value for item in PublishVisibility}:
            visibility = PublishVisibility(str(privacy))
        if upload_status in {"failed", "rejected", "deleted"}:
            return RemotePublication(
                status=ReconciliationStatus.FOUND,
                provider_publication_id=provider_publication_id,
                provider_url=f"https://youtu.be/{provider_publication_id}",
                processing_status=str(upload_status),
                visibility=visibility,
                sanitized_response={
                    "upload_status": upload_status,
                    "processing_status": processing.get("processingStatus"),
                    "failure_reason": processing.get("processingFailureReason"),
                },
                failure_reason="the provider reported that video processing failed",
            )
        return RemotePublication(
            status=ReconciliationStatus.FOUND,
            provider_publication_id=provider_publication_id,
            provider_url=f"https://youtu.be/{provider_publication_id}",
            processing_status=str(upload_status or processing.get("processingStatus") or "unknown"),
            visibility=visibility,
            sanitized_response={
                "upload_status": upload_status,
                "processing_status": processing.get("processingStatus"),
            },
        )

    async def set_thumbnail(
        self,
        *,
        access_token: str,
        provider_publication_id: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        if not content:
            raise ValueError("thumbnail content must not be empty")
        try:
            response = await self._client.post(
                f"{self.settings.youtube_upload_base_url}/thumbnails/set",
                params={"videoId": provider_publication_id},
                content=content,
                headers={
                    **self._authorization(access_token),
                    "Content-Type": content_type,
                    "Content-Length": str(len(content)),
                },
            )
        except httpx.HTTPError as exc:
            raise PublishingAdapterError(
                "thumbnail_upload_unavailable",
                "the provider thumbnail endpoint could not be reached",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            self._raise_response_error(response, operation="thumbnail upload", mutating=True)
        return {"accepted": True, "http_status": response.status_code}

    @staticmethod
    def _authorization(access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    @staticmethod
    def _committed_bytes(response: httpx.Response, *, fallback: int) -> int:
        value = response.headers.get("Range", "")
        if value.startswith("bytes=0-"):
            try:
                return int(value.removeprefix("bytes=0-")) + 1
            except ValueError:
                return fallback
        return fallback

    @staticmethod
    def _video_response_metadata(payload: dict[str, Any]) -> dict[str, Any]:
        status_value = payload.get("status")
        status: dict[str, Any] = status_value if isinstance(status_value, dict) else {}
        return {
            "kind": payload.get("kind"),
            "privacy_status": status.get("privacyStatus"),
            "upload_status": status.get("uploadStatus"),
        }

    @staticmethod
    def _raise_response_error(
        response: httpx.Response,
        *,
        operation: str,
        mutating: bool = False,
    ) -> None:
        if response.status_code == 429:
            raise RateLimitedError(_retry_after(response))
        if response.status_code in {401, 403}:
            raise PublishingAdapterError(
                "provider_authorization_failed",
                f"the provider rejected authorization during {operation}",
                manual_next_step="Reconnect the destination and verify the granted scope.",
            )
        if response.status_code >= 500:
            if mutating:
                raise UncertainRemoteResultError(
                    f"the provider returned a server error during {operation}"
                )
            raise PublishingAdapterError(
                "provider_server_error",
                f"the provider returned a server error during {operation}",
                retryable=True,
            )
        raise PublishingAdapterError(
            "provider_request_rejected",
            f"the provider rejected {operation}",
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
