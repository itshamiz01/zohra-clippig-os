from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_runtime_settings, get_session, require_role
from clipos.config import Settings
from clipos.core.enums import PublishingProvider, PublishState, Role
from clipos.core.errors import ClipOSError
from clipos.core.security import Principal
from clipos.gateways.publishing import PublishingAdapterError, PublishingAdapterRegistry
from clipos.publishing_schemas import (
    OAuthCallbackResponse,
    OAuthStartResponse,
    PublicApprovalCreate,
    PublicApprovalResponse,
    PublicationCreate,
    PublicationEventResponse,
    PublicationList,
    PublicationResponse,
    PublishingCapabilityResponse,
    PublishingConnectionResponse,
)
from clipos.queue import QueueMessage
from clipos.schemas import ApprovalResponse
from clipos.services.approvals import ApprovalService
from clipos.services.publishing import (
    PUBLISH_JOB_KIND,
    PublishingService,
    public_approval_request,
)
from clipos.storage.base import ObjectStorage

router = APIRouter(prefix="/publishing", tags=["publishing"])


def _registry(request: Request) -> PublishingAdapterRegistry:
    return cast(PublishingAdapterRegistry, request.app.state.publishing_adapters)


def _storage(request: Request) -> ObjectStorage:
    storage = request.app.state.storage
    if storage is None:
        raise ClipOSError(
            "publishing_storage_unavailable",
            "publishing requires configured private object storage",
            status_code=503,
        )
    return cast(ObjectStorage, storage)


@router.get("/capabilities", response_model=list[PublishingCapabilityResponse])
async def capabilities(
    request: Request,
    _principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_runtime_settings),
) -> list[PublishingCapabilityResponse]:
    return [
        PublishingCapabilityResponse.model_validate(item)
        for item in PublishingService.capabilities(
            settings=settings,
            registry=_registry(request),
        )
    ]


@router.post(
    "/oauth/{provider}/start",
    response_model=OAuthStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_oauth(
    provider: PublishingProvider,
    request: Request,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> OAuthStartResponse:
    try:
        result = await PublishingService.begin_oauth(
            session,
            principal=principal,
            provider=provider,
            settings=settings,
            registry=_registry(request),
        )
    except PublishingAdapterError as exc:
        raise ClipOSError(exc.category, exc.safe_message, status_code=409) from exc
    return OAuthStartResponse(
        provider=provider,
        authorization_url=result.authorization_url,
        redirect_uri=result.redirect_uri,
        required_scopes=result.required_scopes,
        expires_at=result.expires_at,
    )


@router.get("/oauth/{provider}/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    provider: PublishingProvider,
    request: Request,
    state: Annotated[str, Query(min_length=16, max_length=500)],
    code: Annotated[str | None, Query(min_length=1, max_length=4000)] = None,
    error: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> OAuthCallbackResponse:
    connection, message = await PublishingService.complete_oauth(
        session,
        provider=provider,
        state_value=state,
        code=code,
        error=error,
        settings=settings,
        registry=_registry(request),
    )
    return OAuthCallbackResponse(
        connected=connection is not None,
        provider=provider,
        message=message,
        connection=(
            PublishingConnectionResponse.model_validate(connection)
            if connection is not None
            else None
        ),
    )


@router.get("/connections", response_model=list[PublishingConnectionResponse])
async def list_connections(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[PublishingConnectionResponse]:
    rows = await PublishingService.list_connections(session, principal=principal)
    return [PublishingConnectionResponse.model_validate(row) for row in rows]


@router.get("/connections/{connection_id}", response_model=PublishingConnectionResponse)
async def get_connection(
    connection_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PublishingConnectionResponse:
    row = await PublishingService.get_connection(
        session,
        principal=principal,
        connection_id=connection_id,
    )
    return PublishingConnectionResponse.model_validate(row)


@router.post(
    "/connections/{connection_id}/revoke",
    response_model=PublishingConnectionResponse,
)
async def revoke_connection(
    connection_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> PublishingConnectionResponse:
    row = await PublishingService.revoke_connection(
        session,
        principal=principal,
        connection_id=connection_id,
        settings=settings,
        registry=_registry(request),
    )
    return PublishingConnectionResponse.model_validate(row)


@router.post(
    "/public-approvals",
    response_model=PublicApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_public_approval(
    payload: PublicApprovalCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PublicApprovalResponse:
    approval = await PublishingService.request_public_approval(
        session,
        principal=principal,
        request=payload,
    )
    expected = public_approval_request(
        payload.release,
        rationale=payload.rationale,
        expires_at=payload.expires_at,
    )
    return PublicApprovalResponse(
        approval=ApprovalResponse.model_validate(approval),
        payload_hash=ApprovalService.payload_hash(expected),
        binding_note=(
            "Approval binds the exact connection, render, quality evaluation, public visibility, "
            "metadata, audience flag, category, and idempotency key. Any change requires a new "
            "approval."
        ),
    )


@router.post(
    "/publications",
    response_model=PublicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_publication(
    payload: PublicationCreate,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> PublicationResponse:
    try:
        result = await PublishingService.create_publication(
            session,
            principal=principal,
            request=payload,
            settings=settings,
            storage=_storage(request),
            registry=_registry(request),
        )
    except PublishingAdapterError as exc:
        raise ClipOSError(exc.category, exc.safe_message, status_code=409) from exc
    if result.created:
        await request.app.state.queue.enqueue(
            QueueMessage(
                job_id=result.publication.background_job_id,
                tenant_id=result.publication.tenant_id,
                kind=PUBLISH_JOB_KIND,
            )
        )
    else:
        response.status_code = status.HTTP_200_OK
        response.headers["X-ClipOS-Idempotent-Replay"] = "true"
    return PublicationResponse.model_validate(result.publication)


@router.get("/publications", response_model=PublicationList)
async def list_publications(
    state: PublishState | None = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PublicationList:
    rows, total = await PublishingService.list_publications(
        session,
        principal=principal,
        state=state,
        offset=offset,
        limit=limit,
    )
    return PublicationList(
        items=[PublicationResponse.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/publications/{publication_id}", response_model=PublicationResponse)
async def get_publication(
    publication_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> PublicationResponse:
    row = await PublishingService.get_publication(
        session,
        principal=principal,
        publication_id=publication_id,
    )
    return PublicationResponse.model_validate(row)


@router.get(
    "/publications/{publication_id}/events",
    response_model=list[PublicationEventResponse],
)
async def list_publication_events(
    publication_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[PublicationEventResponse]:
    rows = await PublishingService.list_events(
        session,
        principal=principal,
        publication_id=publication_id,
    )
    return [PublicationEventResponse.model_validate(row) for row in rows]


@router.post(
    "/publications/{publication_id}/cancel",
    response_model=PublicationResponse,
)
async def cancel_publication(
    publication_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PublicationResponse:
    row = await PublishingService.cancel(
        session,
        principal=principal,
        publication_id=publication_id,
    )
    return PublicationResponse.model_validate(row)


@router.post(
    "/publications/{publication_id}/retry",
    response_model=PublicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_publication(
    publication_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> PublicationResponse:
    row = await PublishingService.retry(
        session,
        principal=principal,
        publication_id=publication_id,
    )
    await request.app.state.queue.enqueue(
        QueueMessage(
            job_id=row.background_job_id,
            tenant_id=row.tenant_id,
            kind=PUBLISH_JOB_KIND,
        )
    )
    return PublicationResponse.model_validate(row)
