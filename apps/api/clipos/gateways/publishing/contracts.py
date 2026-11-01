from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from clipos.core.enums import PublishingProvider, PublishVisibility


class ReconciliationStatus(StrEnum):
    FOUND = "found"
    IN_PROGRESS = "in_progress"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    oauth: bool
    resumable_upload: bool
    processing_status: bool
    privacy_status: bool
    metadata: bool
    thumbnails: bool
    cancellation: bool
    reconciliation: bool
    analytics_handoff: bool
    supported_visibilities: tuple[PublishVisibility, ...]
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: frozenset[str]
    token_type: str = "Bearer"  # noqa: S105 - OAuth scheme label, not a credential


@dataclass(frozen=True, slots=True)
class Destination:
    provider_destination_id: str
    display_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublishMetadata:
    title: str
    description: str
    tags: tuple[str, ...]
    visibility: PublishVisibility
    made_for_kids: bool = False
    category_id: str = "22"


@dataclass(frozen=True, slots=True)
class UploadSession:
    session_uri: str
    expires_at: datetime | None = None
    sanitized_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UploadChunkResult:
    committed_bytes: int
    complete: bool
    provider_publication_id: str | None = None
    provider_url: str | None = None
    sanitized_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RemotePublication:
    status: ReconciliationStatus
    provider_publication_id: str | None = None
    provider_url: str | None = None
    processing_status: str | None = None
    visibility: PublishVisibility | None = None
    committed_bytes: int | None = None
    sanitized_response: dict[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None


class PublishingAdapter(Protocol):
    provider: PublishingProvider
    required_scopes: frozenset[str]
    capabilities: ProviderCapabilities

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str: ...

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> TokenSet: ...

    async def refresh_tokens(self, *, refresh_token: str) -> TokenSet: ...

    async def revoke_token(self, *, token: str) -> None: ...

    async def validate_destination(self, *, access_token: str) -> Destination: ...

    async def initiate_upload(
        self,
        *,
        access_token: str,
        metadata: PublishMetadata,
        content_type: str,
        content_length: int,
    ) -> UploadSession: ...

    async def upload_chunk(
        self,
        *,
        access_token: str,
        session_uri: str,
        chunk: bytes,
        offset: int,
        total_bytes: int,
    ) -> UploadChunkResult: ...

    async def reconcile(
        self,
        *,
        access_token: str,
        provider_publication_id: str | None,
        session_uri: str | None,
        total_bytes: int,
    ) -> RemotePublication: ...

    async def get_processing_status(
        self,
        *,
        access_token: str,
        provider_publication_id: str,
    ) -> RemotePublication: ...

    async def set_thumbnail(
        self,
        *,
        access_token: str,
        provider_publication_id: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...
