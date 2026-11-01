from __future__ import annotations

from typing import Any

from clipos.core.enums import PublishingProvider
from clipos.gateways.publishing.contracts import (
    Destination,
    ProviderCapabilities,
    PublishMetadata,
    RemotePublication,
    TokenSet,
    UploadChunkResult,
    UploadSession,
)
from clipos.gateways.publishing.errors import UnsupportedCapabilityError


class DisabledPublishingAdapter:
    required_scopes: frozenset[str] = frozenset()
    capabilities = ProviderCapabilities(
        oauth=False,
        resumable_upload=False,
        processing_status=False,
        privacy_status=False,
        metadata=False,
        thumbnails=False,
        cancellation=False,
        reconciliation=False,
        analytics_handoff=False,
        supported_visibilities=(),
        limitations=("The adapter is intentionally disabled and cannot contact the provider.",),
    )

    def __init__(self, provider: PublishingProvider) -> None:
        self.provider = provider

    @staticmethod
    def _disabled() -> UnsupportedCapabilityError:
        return UnsupportedCapabilityError("publishing while the provider adapter is disabled")

    def build_authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        raise self._disabled()

    async def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenSet:
        raise self._disabled()

    async def refresh_tokens(self, *, refresh_token: str) -> TokenSet:
        raise self._disabled()

    async def revoke_token(self, *, token: str) -> None:
        raise self._disabled()

    async def validate_destination(self, *, access_token: str) -> Destination:
        raise self._disabled()

    async def initiate_upload(
        self,
        *,
        access_token: str,
        metadata: PublishMetadata,
        content_type: str,
        content_length: int,
    ) -> UploadSession:
        raise self._disabled()

    async def upload_chunk(
        self,
        *,
        access_token: str,
        session_uri: str,
        chunk: bytes,
        offset: int,
        total_bytes: int,
    ) -> UploadChunkResult:
        raise self._disabled()

    async def reconcile(
        self,
        *,
        access_token: str,
        provider_publication_id: str | None,
        session_uri: str | None,
        total_bytes: int,
    ) -> RemotePublication:
        raise self._disabled()

    async def get_processing_status(
        self, *, access_token: str, provider_publication_id: str
    ) -> RemotePublication:
        raise self._disabled()

    async def set_thumbnail(
        self,
        *,
        access_token: str,
        provider_publication_id: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        raise self._disabled()

    async def close(self) -> None:
        return None
