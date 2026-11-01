from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.config import Settings
from clipos.core.enums import (
    ActionClass,
    ApprovalStatus,
    AuditOutcome,
    DestinationPlatform,
    GateCheckpoint,
    JobState,
    MembershipStatus,
    OAuthConnectionState,
    ProviderMode,
    PublishEventKind,
    PublishingProvider,
    PublishState,
    PublishVisibility,
    Role,
)
from clipos.core.errors import AuthorizationError, ClipOSError, ConflictError, NotFoundError
from clipos.core.logging import correlation_id_var
from clipos.core.security import Principal
from clipos.gateways.publishing import (
    MissingScopeError,
    PublishingAdapterError,
    PublishingAdapterRegistry,
    TokenCipher,
    TokenEnvelopeError,
    TokenSet,
    UnsupportedCapabilityError,
)
from clipos.models import (
    ApprovalRequest,
    BackgroundJob,
    Publication,
    PublicationEvent,
    PublishingConnection,
    PublishingOAuthState,
    QualityEvaluation,
    RenderOutput,
    TenantMembership,
)
from clipos.publishing_schemas import PublicApprovalCreate, PublicationCreate
from clipos.schemas import ApprovalCreate
from clipos.services.approvals import ApprovalService
from clipos.services.audit import AuditService
from clipos.services.quality import QualityGateService
from clipos.storage.base import ObjectStorage

PUBLISH_JOB_KIND = "publication.upload"

_PROVIDER_NAMES = {
    PublishingProvider.YOUTUBE: "YouTube",
    PublishingProvider.META: "Meta / Instagram",
    PublishingProvider.TIKTOK: "TikTok",
    PublishingProvider.LINKEDIN: "LinkedIn",
}
_PROVIDER_CONFIG = {
    PublishingProvider.YOUTUBE: [
        "CLIPOS_YOUTUBE_PUBLISHING_ENABLED",
        "CLIPOS_YOUTUBE_CLIENT_ID",
        "CLIPOS_YOUTUBE_CLIENT_SECRET",
        "CLIPOS_YOUTUBE_REDIRECT_URI",
        "CLIPOS_PUBLISHING_TOKEN_ENCRYPTION_KEY",
    ],
    PublishingProvider.META: [],
    PublishingProvider.TIKTOK: [],
    PublishingProvider.LINKEDIN: [],
}


@dataclass(frozen=True, slots=True)
class OAuthStart:
    authorization_url: str
    redirect_uri: str
    required_scopes: list[str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PublicationCreation:
    publication: Publication
    created: bool


@dataclass(frozen=True, slots=True)
class AccessGrant:
    connection: PublishingConnection
    access_token: str
    refresh_token: str | None


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _cipher(settings: Settings) -> TokenCipher:
    configured = settings.publishing_token_encryption_key
    material = (
        configured.get_secret_value()
        if configured is not None
        else settings.secret_key.get_secret_value()
    )
    return TokenCipher(material)


def _oauth_context(state_id: UUID) -> str:
    return f"oauth-state:{state_id}"


def _connection_context(connection_id: UUID, tenant_id: UUID) -> str:
    return f"tenant:{tenant_id}:connection:{connection_id}"


def _session_context(publication_id: UUID, tenant_id: UUID) -> str:
    return f"tenant:{tenant_id}:publication:{publication_id}:session"


def publication_payload(request: PublicationCreate) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    payload["public_approval_id"] = None
    return payload


def publication_request_hash(request: PublicationCreate) -> str:
    return _hash(publication_payload(request))


def public_approval_request(
    request: PublicationCreate,
    *,
    rationale: str,
    expires_at: datetime | None,
) -> ApprovalCreate:
    return ApprovalCreate(
        action_class=ActionClass.PUBLIC_PUBLISH,
        action_type="publishing.youtube.make_public",
        resource_type="render_job",
        resource_id=str(request.render_job_id),
        rationale=rationale,
        action_payload=publication_payload(request),
        expires_at=expires_at,
    )


class PublishingService:
    @staticmethod
    def capabilities(
        *, settings: Settings, registry: PublishingAdapterRegistry
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for provider in PublishingProvider:
            adapter = registry.get(provider)
            configured = (
                provider == PublishingProvider.YOUTUBE and settings.youtube_publishing_enabled
            )
            mode = ProviderMode.UNVERIFIED if configured else ProviderMode.DISABLED
            evidence_note = (
                "Official adapter is configured, but real OAuth, upload, and publication have not "
                "been externally verified. Mocked tests are reported separately."
                if configured
                else "Provider publishing is intentionally disabled; no placeholder is integrated."
            )
            features = adapter.capabilities
            rows.append(
                {
                    "provider": provider,
                    "display_name": _PROVIDER_NAMES[provider],
                    "mode": mode,
                    "configured": configured,
                    "real_oauth_verified": False,
                    "real_upload_verified": False,
                    "required_scopes": sorted(adapter.required_scopes),
                    "required_configuration": _PROVIDER_CONFIG[provider],
                    "features": {
                        "oauth": features.oauth,
                        "resumable_upload": features.resumable_upload,
                        "processing_status": features.processing_status,
                        "privacy_status": features.privacy_status,
                        "metadata": features.metadata,
                        "thumbnails": features.thumbnails,
                        "cancellation": features.cancellation,
                        "reconciliation": features.reconciliation,
                        "analytics_handoff": features.analytics_handoff,
                        "supported_visibilities": list(features.supported_visibilities),
                    },
                    "limitations": list(features.limitations),
                    "evidence_note": evidence_note,
                }
            )
        return rows

    @staticmethod
    async def begin_oauth(
        session: AsyncSession,
        *,
        principal: Principal,
        provider: PublishingProvider,
        settings: Settings,
        registry: PublishingAdapterRegistry,
    ) -> OAuthStart:
        principal.require(Role.ADMIN)
        adapter = registry.get(provider)
        if not adapter.capabilities.oauth:
            raise UnsupportedCapabilityError("OAuth connection")
        state_value = secrets.token_urlsafe(48)
        verifier = secrets.token_urlsafe(64)
        now = datetime.now(UTC)
        state = PublishingOAuthState(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            provider=provider,
            state_hash=hashlib.sha256(state_value.encode("utf-8")).hexdigest(),
            pkce_verifier_envelope="pending",
            redirect_uri=settings.youtube_redirect_uri,
            requested_scopes=sorted(adapter.required_scopes),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.publishing_oauth_state_ttl_seconds),
            consumed_at=None,
            denial_reason=None,
        )
        state.pkce_verifier_envelope = _cipher(settings).encrypt(
            {"code_verifier": verifier}, context=_oauth_context(state.id)
        )
        session.add(state)
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="publishing.oauth_started",
            resource_type="publishing_oauth_state",
            resource_id=str(state.id),
            event_data={
                "provider": provider.value,
                "redirect_uri": state.redirect_uri,
                "required_scopes": state.requested_scopes,
                "expires_at": state.expires_at.isoformat(),
                "pkce": True,
            },
        )
        await session.commit()
        return OAuthStart(
            authorization_url=adapter.build_authorization_url(
                state=state_value,
                code_challenge=_pkce_challenge(verifier),
                redirect_uri=state.redirect_uri,
            ),
            redirect_uri=state.redirect_uri,
            required_scopes=state.requested_scopes,
            expires_at=state.expires_at,
        )

    @staticmethod
    async def complete_oauth(
        session: AsyncSession,
        *,
        provider: PublishingProvider,
        state_value: str,
        code: str | None,
        error: str | None,
        settings: Settings,
        registry: PublishingAdapterRegistry,
    ) -> tuple[PublishingConnection | None, str]:
        state_hash = hashlib.sha256(state_value.encode("utf-8")).hexdigest()
        state = await session.scalar(
            select(PublishingOAuthState)
            .where(
                PublishingOAuthState.provider == provider,
                PublishingOAuthState.state_hash == state_hash,
            )
            .with_for_update()
        )
        if state is None or state.consumed_at is not None:
            raise ClipOSError(
                "oauth_state_invalid",
                "the OAuth state is invalid or has already been used",
                status_code=400,
            )
        now = datetime.now(UTC)
        if aware(state.expires_at) <= now:
            state.consumed_at = now
            state.denial_reason = "expired"
            await session.commit()
            raise ClipOSError(
                "oauth_state_expired",
                "the OAuth state expired; start a new connection attempt",
                status_code=400,
            )
        state.consumed_at = now
        if error:
            state.denial_reason = error[:200]
            AuditService.record(
                session,
                tenant_id=state.tenant_id,
                actor_user_id=state.user_id,
                action="publishing.oauth_denied",
                resource_type="publishing_oauth_state",
                resource_id=str(state.id),
                outcome=AuditOutcome.DENIED,
                event_data={"provider": provider.value, "error_category": error[:100]},
            )
            await session.commit()
            return None, "The provider authorization request was denied; no connection was created."
        if not code:
            state.denial_reason = "missing_code"
            await session.commit()
            raise ClipOSError(
                "oauth_code_missing",
                "the OAuth callback contained no authorization code",
                status_code=400,
            )
        try:
            verifier_payload = _cipher(settings).decrypt(
                state.pkce_verifier_envelope, context=_oauth_context(state.id)
            )
            verifier = verifier_payload.get("code_verifier")
            if not isinstance(verifier, str) or not verifier:
                raise TokenEnvelopeError("PKCE verifier is unavailable")
            adapter = registry.get(provider)
            token_set = await adapter.exchange_code(
                code=code,
                code_verifier=verifier,
                redirect_uri=state.redirect_uri,
            )
            missing = adapter.required_scopes - token_set.scopes
            if missing:
                raise MissingScopeError(missing)
            destination = await adapter.validate_destination(access_token=token_set.access_token)
        except (PublishingAdapterError, TokenEnvelopeError) as exc:
            category = getattr(exc, "category", "oauth_state_unreadable")
            state.denial_reason = str(category)[:200]
            AuditService.record(
                session,
                tenant_id=state.tenant_id,
                actor_user_id=state.user_id,
                action="publishing.oauth_failed",
                resource_type="publishing_oauth_state",
                resource_id=str(state.id),
                outcome=AuditOutcome.FAILURE,
                event_data={"provider": provider.value, "error_category": category},
            )
            await session.commit()
            raise ClipOSError(
                str(category),
                getattr(exc, "safe_message", "the OAuth connection could not be completed"),
                status_code=409,
            ) from exc

        connection = await session.scalar(
            select(PublishingConnection)
            .where(
                PublishingConnection.tenant_id == state.tenant_id,
                PublishingConnection.provider == provider,
                PublishingConnection.provider_destination_id == destination.provider_destination_id,
            )
            .with_for_update()
        )
        if connection is None:
            connection = PublishingConnection(
                id=uuid4(),
                tenant_id=state.tenant_id,
                user_id=state.user_id,
                provider=provider,
                state=OAuthConnectionState.PENDING,
                provider_destination_id=destination.provider_destination_id,
                destination_display_name=destination.display_name,
                granted_scopes=[],
                token_envelope="pending",  # noqa: S106 - transient sentinel, not a token
                access_token_expires_at=token_set.expires_at,
                has_refresh_token=False,
            )
            session.add(connection)
            await session.flush()
        existing_refresh: str | None = None
        if connection.token_envelope != "pending":  # noqa: S105 - transient sentinel
            try:
                current = _cipher(settings).decrypt(
                    connection.token_envelope,
                    context=_connection_context(connection.id, connection.tenant_id),
                )
                value = current.get("refresh_token")
                existing_refresh = value if isinstance(value, str) and value else None
            except TokenEnvelopeError:
                existing_refresh = None
        refresh_token = token_set.refresh_token or existing_refresh
        connection.user_id = state.user_id
        connection.state = OAuthConnectionState.CONNECTED
        connection.destination_display_name = destination.display_name
        connection.granted_scopes = sorted(token_set.scopes)
        connection.token_envelope = PublishingService._encrypt_token_set(
            settings,
            connection=connection,
            token_set=token_set,
            refresh_token=refresh_token,
        )
        connection.access_token_expires_at = token_set.expires_at
        connection.has_refresh_token = bool(refresh_token)
        connection.last_validated_at = now
        connection.last_refreshed_at = None
        connection.revoked_at = None
        connection.reconnect_required_at = None
        connection.last_error_category = None
        connection.last_error_message = None
        AuditService.record(
            session,
            tenant_id=state.tenant_id,
            actor_user_id=state.user_id,
            action="publishing.connection_connected",
            resource_type="publishing_connection",
            resource_id=str(connection.id),
            event_data={
                "provider": provider.value,
                "provider_destination_id": connection.provider_destination_id,
                "granted_scopes": connection.granted_scopes,
                "has_refresh_token": connection.has_refresh_token,
            },
        )
        await session.commit()
        await session.refresh(connection)
        return (
            connection,
            "Destination connected. Tokens are encrypted and never returned by the API.",
        )

    @staticmethod
    def _encrypt_token_set(
        settings: Settings,
        *,
        connection: PublishingConnection,
        token_set: TokenSet,
        refresh_token: str | None,
    ) -> str:
        return _cipher(settings).encrypt(
            {
                "access_token": token_set.access_token,
                "refresh_token": refresh_token,
                "token_type": token_set.token_type,
            },
            context=_connection_context(connection.id, connection.tenant_id),
        )

    @staticmethod
    async def list_connections(
        session: AsyncSession, *, principal: Principal
    ) -> list[PublishingConnection]:
        principal.require(Role.VIEWER)
        return list(
            (
                await session.scalars(
                    select(PublishingConnection)
                    .where(PublishingConnection.tenant_id == principal.tenant_id)
                    .order_by(PublishingConnection.created_at.desc())
                )
            ).all()
        )

    @staticmethod
    async def get_connection(
        session: AsyncSession, *, principal: Principal, connection_id: UUID
    ) -> PublishingConnection:
        principal.require(Role.VIEWER)
        connection = await session.scalar(
            select(PublishingConnection).where(
                PublishingConnection.id == connection_id,
                PublishingConnection.tenant_id == principal.tenant_id,
            )
        )
        if connection is None:
            raise NotFoundError("publishing connection")
        return connection

    @staticmethod
    async def revoke_connection(
        session: AsyncSession,
        *,
        principal: Principal,
        connection_id: UUID,
        settings: Settings,
        registry: PublishingAdapterRegistry,
    ) -> PublishingConnection:
        principal.require(Role.ADMIN)
        connection = await session.scalar(
            select(PublishingConnection)
            .where(
                PublishingConnection.id == connection_id,
                PublishingConnection.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise NotFoundError("publishing connection")
        remote_error: str | None = None
        try:
            token_payload = _cipher(settings).decrypt(
                connection.token_envelope,
                context=_connection_context(connection.id, connection.tenant_id),
            )
            token = token_payload.get("refresh_token") or token_payload.get("access_token")
            if isinstance(token, str) and token:
                await registry.get(connection.provider).revoke_token(token=token)
        except (PublishingAdapterError, TokenEnvelopeError) as exc:
            remote_error = getattr(exc, "category", "token_envelope_invalid")
        connection.state = OAuthConnectionState.REVOKED
        connection.revoked_at = datetime.now(UTC)
        connection.token_envelope = _cipher(settings).encrypt(
            {"access_token": None, "refresh_token": None, "token_type": "revoked"},
            context=_connection_context(connection.id, connection.tenant_id),
        )
        connection.has_refresh_token = False
        connection.last_error_category = remote_error
        connection.last_error_message = (
            "Local credentials were removed; remote revocation could not be confirmed."
            if remote_error
            else None
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="publishing.connection_revoked",
            resource_type="publishing_connection",
            resource_id=str(connection.id),
            event_data={
                "provider": connection.provider.value,
                "remote_revocation_confirmed": remote_error is None,
                "error_category": remote_error,
            },
        )
        await session.commit()
        return connection

    @staticmethod
    async def usable_access_grant(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        settings: Settings,
        registry: PublishingAdapterRegistry,
    ) -> AccessGrant:
        connection = await session.scalar(
            select(PublishingConnection)
            .where(
                PublishingConnection.id == connection_id,
                PublishingConnection.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if connection is None:
            raise NotFoundError("publishing connection")
        membership = await session.scalar(
            select(TenantMembership.id).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == connection.user_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
        )
        if membership is None:
            raise AuthorizationError("the user who authorized this destination is no longer active")
        if connection.state != OAuthConnectionState.CONNECTED:
            raise ClipOSError(
                "publishing_reconnect_required",
                "the destination connection is not active; reconnect before publishing",
                status_code=409,
            )
        adapter = registry.get(connection.provider)
        missing = adapter.required_scopes - set(connection.granted_scopes)
        if missing:
            await PublishingService._mark_reconnect(
                session,
                connection=connection,
                category="missing_scope",
                message="the stored grant is missing required publishing permissions",
            )
            raise MissingScopeError(missing)
        try:
            payload = _cipher(settings).decrypt(
                connection.token_envelope,
                context=_connection_context(connection.id, connection.tenant_id),
            )
        except TokenEnvelopeError as exc:
            await PublishingService._mark_reconnect(
                session,
                connection=connection,
                category="token_envelope_invalid",
                message="encrypted provider credentials could not be authenticated",
            )
            raise ClipOSError(
                "token_envelope_invalid",
                "encrypted provider credentials are unavailable; reconnect the destination",
                status_code=409,
            ) from exc
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            await PublishingService._mark_reconnect(
                session,
                connection=connection,
                category="access_token_missing",
                message="the stored provider access token is unavailable",
            )
            raise ClipOSError(
                "access_token_missing",
                "provider credentials are unavailable; reconnect the destination",
                status_code=409,
            )
        expires_at = aware(connection.access_token_expires_at)
        refresh_before = datetime.now(UTC) + timedelta(
            seconds=settings.publishing_token_refresh_skew_seconds
        )
        if expires_at <= refresh_before:
            if not isinstance(refresh_token, str) or not refresh_token:
                await PublishingService._mark_reconnect(
                    session,
                    connection=connection,
                    category="refresh_token_missing",
                    message="the provider grant has no refresh token",
                )
                raise ClipOSError(
                    "refresh_token_missing",
                    "the provider grant expired and must be reconnected",
                    status_code=409,
                )
            try:
                refreshed = await adapter.refresh_tokens(refresh_token=refresh_token)
                missing = adapter.required_scopes - refreshed.scopes
                if missing:
                    raise MissingScopeError(missing)
            except PublishingAdapterError as exc:
                await PublishingService._mark_reconnect(
                    session,
                    connection=connection,
                    category=exc.category,
                    message=exc.safe_message,
                )
                raise ClipOSError(
                    exc.category,
                    exc.safe_message,
                    status_code=409,
                ) from exc
            refresh_token = refreshed.refresh_token or refresh_token
            connection.token_envelope = PublishingService._encrypt_token_set(
                settings,
                connection=connection,
                token_set=refreshed,
                refresh_token=refresh_token,
            )
            connection.access_token_expires_at = refreshed.expires_at
            connection.granted_scopes = sorted(refreshed.scopes)
            connection.has_refresh_token = bool(refresh_token)
            connection.last_refreshed_at = datetime.now(UTC)
            connection.last_error_category = None
            connection.last_error_message = None
            access_token = refreshed.access_token
            await session.commit()
        return AccessGrant(
            connection=connection,
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        )

    @staticmethod
    async def _mark_reconnect(
        session: AsyncSession,
        *,
        connection: PublishingConnection,
        category: str,
        message: str,
    ) -> None:
        connection.state = OAuthConnectionState.RECONNECT_REQUIRED
        connection.reconnect_required_at = datetime.now(UTC)
        connection.last_error_category = category[:100]
        connection.last_error_message = message[:500]
        await session.commit()

    @staticmethod
    async def request_public_approval(
        session: AsyncSession,
        *,
        principal: Principal,
        request: PublicApprovalCreate,
    ) -> ApprovalRequest:
        principal.require(Role.EDITOR)
        if request.release.visibility != PublishVisibility.PUBLIC:
            raise ClipOSError(
                "public_approval_visibility_mismatch",
                "public approval can be requested only for public visibility",
                status_code=422,
            )
        if request.release.public_approval_id is not None:
            raise ClipOSError(
                "public_approval_nested",
                "a new public approval request cannot already reference an approval",
                status_code=422,
            )
        approval_request = public_approval_request(
            request.release,
            rationale=request.rationale,
            expires_at=request.expires_at,
        )
        return await ApprovalService.create(
            session,
            principal=principal,
            request=approval_request,
        )

    @classmethod
    async def create_publication(
        cls,
        session: AsyncSession,
        *,
        principal: Principal,
        request: PublicationCreate,
        settings: Settings,
        storage: ObjectStorage,
        registry: PublishingAdapterRegistry,
    ) -> PublicationCreation:
        principal.require(Role.REVIEWER)
        request_hash = publication_request_hash(request)
        connection = await session.scalar(
            select(PublishingConnection).where(
                PublishingConnection.id == request.connection_id,
                PublishingConnection.tenant_id == principal.tenant_id,
            )
        )
        if connection is None:
            raise NotFoundError("publishing connection")
        existing = await session.scalar(
            select(Publication).where(
                Publication.tenant_id == principal.tenant_id,
                Publication.provider == connection.provider,
                Publication.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ConflictError("publication idempotency key was reused with different input")
            return PublicationCreation(publication=existing, created=False)
        adapter = registry.get(connection.provider)
        if not adapter.capabilities.resumable_upload:
            raise UnsupportedCapabilityError("resumable upload")
        if request.visibility not in adapter.capabilities.supported_visibilities:
            raise ClipOSError(
                "visibility_unsupported",
                "the selected provider does not support the requested visibility",
                status_code=422,
            )
        evaluation = await session.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.id == request.quality_evaluation_id,
                QualityEvaluation.tenant_id == principal.tenant_id,
                QualityEvaluation.render_job_id == request.render_job_id,
            )
        )
        if evaluation is None:
            raise NotFoundError("quality evaluation")
        if evaluation.destination != DestinationPlatform.YOUTUBE_SHORTS:
            raise ClipOSError(
                "destination_gate_mismatch",
                "the approved quality destination does not match the YouTube adapter",
                status_code=409,
            )
        if request.visibility == PublishVisibility.PUBLIC:
            await cls._validate_public_approval(
                session,
                principal=principal,
                request=request,
                settings=settings,
            )
        elif request.public_approval_id is not None:
            raise ClipOSError(
                "public_approval_not_applicable",
                "a public approval cannot be attached to private or unlisted visibility",
                status_code=422,
            )
        grant = await cls.usable_access_grant(
            session,
            tenant_id=principal.tenant_id,
            connection_id=connection.id,
            settings=settings,
            registry=registry,
        )
        destination = await adapter.validate_destination(access_token=grant.access_token)
        if destination.provider_destination_id != connection.provider_destination_id:
            raise ClipOSError(
                "destination_changed",
                "the provider grant now resolves to a different destination; reconnect it",
                status_code=409,
            )
        verification = await QualityGateService.verify(
            session,
            principal=principal,
            evaluation_id=evaluation.id,
            checkpoint=GateCheckpoint.PUBLISH_JOB_ACCEPTANCE,
            expected_destination=evaluation.destination,
            expected_territory=evaluation.territory,
            settings=settings,
            storage=storage,
        )
        if not verification.accepted:
            raise ClipOSError(
                "mandatory_gate_denied",
                "the mandatory quality and rights gate denied publish-job acceptance",
                status_code=409,
                details={"failure_reasons": verification.failure_reasons},
            )
        output = await session.scalar(
            select(RenderOutput).where(
                RenderOutput.id == evaluation.render_output_id,
                RenderOutput.tenant_id == principal.tenant_id,
                RenderOutput.render_job_id == request.render_job_id,
                RenderOutput.is_current.is_(True),
            )
        )
        if output is None:
            raise ConflictError("the approved render output is no longer current")
        await cls._check_budget(session, principal.tenant_id, settings)
        background = BackgroundJob(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            kind=PUBLISH_JOB_KIND,
            idempotency_key=request.idempotency_key,
            state=JobState.QUEUED,
            payload={
                "schema": "clipos-publication-job.v1",
                "quality_evaluation_id": str(evaluation.id),
                "render_job_id": str(request.render_job_id),
            },
            result=None,
            attempts=0,
            max_attempts=settings.publishing_max_attempts,
            correlation_id=correlation_id_var.get(),
        )
        publication = Publication(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            requested_by_user_id=principal.user_id,
            connection_id=connection.id,
            render_job_id=request.render_job_id,
            render_output_id=output.id,
            quality_evaluation_id=evaluation.id,
            background_job_id=background.id,
            public_approval_id=request.public_approval_id,
            acceptance_gate_verification_id=verification.id,
            upload_gate_verification_id=None,
            provider=connection.provider,
            provider_destination_id=connection.provider_destination_id,
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
            state=PublishState.QUEUED,
            visibility=request.visibility,
            title=request.title,
            description=request.description,
            tags=request.tags,
            made_for_kids=request.made_for_kids,
            category_id=request.category_id,
            territory=evaluation.territory,
            campaign_reference=evaluation.campaign_reference,
            progress_percent=0,
            stage="queued",
            content_type=output.content_type,
            content_length=output.size_bytes,
            committed_bytes=0,
            upload_session_envelope=None,
            provider_publication_id=None,
            provider_url=None,
            provider_processing_status=None,
            sanitized_provider_response={},
            analytics_handoff=None,
            cancellation_requested=False,
            attempt_count=0,
            max_attempts=settings.publishing_max_attempts,
            retryable=False,
            uncertain_remote_result=False,
        )
        session.add_all([background, publication])
        # Flush both parents before the immutable event to preserve FK ordering on SQLite and PG.
        await session.flush()
        session.add(
            PublicationEvent(
                tenant_id=publication.tenant_id,
                publication_id=publication.id,
                sequence=1,
                kind=PublishEventKind.CREATED,
                from_state=None,
                to_state=publication.state,
                progress_percent=0,
                stage="queued",
                message="Publication accepted after the mandatory quality and rights gate.",
                event_data={
                    "visibility": publication.visibility.value,
                    "private_first": publication.visibility != PublishVisibility.PUBLIC,
                    "acceptance_gate_verification_id": str(verification.id),
                },
            )
        )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="publication.created",
            resource_type="publication",
            resource_id=str(publication.id),
            event_data={
                "provider": publication.provider.value,
                "visibility": publication.visibility.value,
                "render_job_id": str(publication.render_job_id),
                "quality_evaluation_id": str(publication.quality_evaluation_id),
                "public_approval_id": (
                    str(publication.public_approval_id) if publication.public_approval_id else None
                ),
            },
        )
        await session.commit()
        await session.refresh(publication)
        return PublicationCreation(publication=publication, created=True)

    @staticmethod
    async def _validate_public_approval(
        session: AsyncSession,
        *,
        principal: Principal,
        request: PublicationCreate,
        settings: Settings,
    ) -> None:
        if not settings.publishing_public_enabled:
            raise ClipOSError(
                "public_publishing_disabled",
                "public publication is disabled by configuration; use private or unlisted",
                status_code=409,
            )
        if request.public_approval_id is None:
            raise ClipOSError(
                "public_approval_required",
                "public visibility requires an explicit approved human request",
                status_code=409,
            )
        approval = await session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.id == request.public_approval_id,
                ApprovalRequest.tenant_id == principal.tenant_id,
            )
        )
        if approval is None:
            raise NotFoundError("public publication approval")
        now = datetime.now(UTC)
        if approval.status != ApprovalStatus.APPROVED:
            raise ClipOSError(
                "public_approval_not_approved",
                "the public publication approval is not approved",
                status_code=409,
            )
        if approval.decided_by_user_id is None or approval.decided_at is None:
            raise ClipOSError(
                "public_approval_incomplete",
                "the public publication approval has no human decision evidence",
                status_code=409,
            )
        if approval.expires_at is not None and aware(approval.expires_at) <= now:
            raise ClipOSError(
                "public_approval_expired",
                "the public publication approval has expired",
                status_code=409,
            )
        expected = public_approval_request(
            request.model_copy(update={"public_approval_id": None}),
            rationale=approval.rationale,
            expires_at=(aware(approval.expires_at) if approval.expires_at else None),
        )
        if approval.payload_hash != ApprovalService.payload_hash(expected):
            raise ClipOSError(
                "public_approval_payload_mismatch",
                "the approval does not bind this exact visibility, metadata, render, and "
                "destination",
                status_code=409,
            )
        if approval.action_class != ActionClass.PUBLIC_PUBLISH:
            raise ClipOSError(
                "public_approval_class_mismatch",
                "the approval is not for public publication",
                status_code=409,
            )

    @staticmethod
    async def _check_budget(session: AsyncSession, tenant_id: UUID, settings: Settings) -> None:
        active_states = {
            PublishState.QUEUED,
            PublishState.VALIDATING,
            PublishState.UPLOADING,
            PublishState.PROCESSING,
            PublishState.NEEDS_RECONCILIATION,
        }
        active = await session.scalar(
            select(func.count())
            .select_from(Publication)
            .where(
                Publication.tenant_id == tenant_id,
                Publication.state.in_(active_states),
            )
        )
        if int(active or 0) >= settings.publishing_max_queued_per_tenant:
            raise ClipOSError(
                "publishing_queue_limit",
                "the tenant publishing queue is full",
                status_code=429,
            )
        since = datetime.now(UTC) - timedelta(hours=24)
        daily = await session.scalar(
            select(func.count())
            .select_from(Publication)
            .where(
                Publication.tenant_id == tenant_id,
                Publication.created_at >= since,
                Publication.state != PublishState.CANCELLED,
            )
        )
        if int(daily or 0) >= settings.publishing_daily_upload_limit:
            raise ClipOSError(
                "publishing_daily_limit",
                "the configured rolling 24-hour publication budget is exhausted",
                status_code=429,
            )

    @staticmethod
    async def get_publication(
        session: AsyncSession, *, principal: Principal, publication_id: UUID
    ) -> Publication:
        principal.require(Role.VIEWER)
        publication = await session.scalar(
            select(Publication).where(
                Publication.id == publication_id,
                Publication.tenant_id == principal.tenant_id,
            )
        )
        if publication is None:
            raise NotFoundError("publication")
        return publication

    @staticmethod
    async def list_publications(
        session: AsyncSession,
        *,
        principal: Principal,
        state: PublishState | None,
        offset: int,
        limit: int,
    ) -> tuple[list[Publication], int]:
        principal.require(Role.VIEWER)
        conditions = [Publication.tenant_id == principal.tenant_id]
        if state is not None:
            conditions.append(Publication.state == state)
        total = await session.scalar(
            select(func.count()).select_from(Publication).where(*conditions)
        )
        rows = await session.scalars(
            select(Publication)
            .where(*conditions)
            .order_by(Publication.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows.all()), int(total or 0)

    @staticmethod
    async def list_events(
        session: AsyncSession, *, principal: Principal, publication_id: UUID
    ) -> list[PublicationEvent]:
        await PublishingService.get_publication(
            session, principal=principal, publication_id=publication_id
        )
        return list(
            (
                await session.scalars(
                    select(PublicationEvent)
                    .where(
                        PublicationEvent.publication_id == publication_id,
                        PublicationEvent.tenant_id == principal.tenant_id,
                    )
                    .order_by(PublicationEvent.sequence)
                )
            ).all()
        )

    @staticmethod
    async def cancel(
        session: AsyncSession, *, principal: Principal, publication_id: UUID
    ) -> Publication:
        principal.require(Role.EDITOR)
        publication = await session.scalar(
            select(Publication)
            .where(
                Publication.id == publication_id,
                Publication.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if publication is None:
            raise NotFoundError("publication")
        if publication.state in {
            PublishState.SUCCEEDED,
            PublishState.CANCELLED,
        }:
            raise ConflictError("completed or cancelled publications cannot be cancelled")
        if (
            publication.state
            in {
                PublishState.QUEUED,
                PublishState.VALIDATING,
                PublishState.FAILED,
            }
            and not publication.provider_publication_id
        ):
            source = publication.state
            publication.state = PublishState.CANCELLED
            publication.stage = "cancelled"
            publication.finished_at = datetime.now(UTC)
            publication.cancellation_requested = True
            background = await session.get(BackgroundJob, publication.background_job_id)
            if background is not None:
                background.state = JobState.CANCELLED
                background.finished_at = publication.finished_at
            await PublishingService.append_event(
                session,
                publication=publication,
                kind=PublishEventKind.CANCELLED,
                from_state=source,
                message="Publication cancelled before a confirmed remote publication existed.",
            )
        else:
            publication.cancellation_requested = True
            publication.manual_next_step = (
                "A remote upload may already exist. Reconcile provider state before any deletion "
                "or new submission."
            )
            await PublishingService.append_event(
                session,
                publication=publication,
                kind=PublishEventKind.CANCELLATION_REQUESTED,
                from_state=publication.state,
                message="Cancellation requested; remote state must be reconciled safely.",
            )
        await session.commit()
        return publication

    @staticmethod
    async def retry(
        session: AsyncSession, *, principal: Principal, publication_id: UUID
    ) -> Publication:
        principal.require(Role.EDITOR)
        publication = await session.scalar(
            select(Publication)
            .where(
                Publication.id == publication_id,
                Publication.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if publication is None:
            raise NotFoundError("publication")
        if publication.attempt_count >= publication.max_attempts:
            raise ConflictError("publication retry budget is exhausted")
        source = publication.state
        if source == PublishState.NEEDS_RECONCILIATION:
            publication.stage = "reconciliation_queued"
        elif source == PublishState.FAILED and publication.retryable:
            publication.state = PublishState.QUEUED
            publication.stage = "retry_queued"
            publication.finished_at = None
            publication.failure_category = None
            publication.failure_message = None
            publication.manual_next_step = None
            publication.retry_after_seconds = None
            background = await session.get(BackgroundJob, publication.background_job_id)
            if background is not None:
                background.state = JobState.QUEUED
                background.finished_at = None
                background.failure_category = None
                background.failure_message = None
        else:
            raise ConflictError(
                "only retryable failures or reconciliation-required publications can be retried"
            )
        await PublishingService.append_event(
            session,
            publication=publication,
            kind=PublishEventKind.RETRY_QUEUED,
            from_state=source,
            message=(
                "Reconciliation queued before any possible repeat upload."
                if source == PublishState.NEEDS_RECONCILIATION
                else "Safe publication retry queued."
            ),
        )
        await session.commit()
        return publication

    @staticmethod
    async def append_event(
        session: AsyncSession,
        *,
        publication: Publication,
        kind: PublishEventKind,
        from_state: PublishState | None,
        message: str,
        event_data: dict[str, Any] | None = None,
    ) -> PublicationEvent:
        # Flush the parent and any prior event before allocating the next immutable
        # sequence. This is required when one transaction appends multiple events;
        # otherwise both SELECT max() calls can observe the same database state.
        await session.flush()
        current = await session.scalar(
            select(func.max(PublicationEvent.sequence)).where(
                PublicationEvent.publication_id == publication.id
            )
        )
        event = PublicationEvent(
            tenant_id=publication.tenant_id,
            publication_id=publication.id,
            sequence=int(current or 0) + 1,
            kind=kind,
            from_state=from_state,
            to_state=publication.state,
            progress_percent=publication.progress_percent,
            stage=publication.stage,
            message=message[:500],
            event_data=event_data or {},
        )
        session.add(event)
        await session.flush()
        return event

    @staticmethod
    def encrypt_upload_session(
        settings: Settings, *, publication: Publication, session_uri: str
    ) -> str:
        return _cipher(settings).encrypt(
            {"session_uri": session_uri},
            context=_session_context(publication.id, publication.tenant_id),
        )

    @staticmethod
    def decrypt_upload_session(settings: Settings, *, publication: Publication) -> str | None:
        if not publication.upload_session_envelope:
            return None
        payload = _cipher(settings).decrypt(
            publication.upload_session_envelope,
            context=_session_context(publication.id, publication.tenant_id),
        )
        value = payload.get("session_uri")
        if not isinstance(value, str) or not value:
            raise TokenEnvelopeError("upload session envelope is invalid")
        return value
