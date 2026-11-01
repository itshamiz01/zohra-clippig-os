from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_runtime_settings, get_session, require_role
from clipos.asset_schemas import (
    AssetDetailResponse,
    AssetResponse,
    MediaMetadataResponse,
    SourceCapability,
    TranscriptChunkResponse,
    TranscriptDetailResponse,
    TranscriptSegmentResponse,
    TranscriptSummaryResponse,
    TranscriptWordResponse,
    UploadAssetResponse,
    URLIngestionRequest,
)
from clipos.config import Settings
from clipos.core.enums import AssetSource, AssetState, Role
from clipos.core.errors import ClipOSError, NotFoundError
from clipos.core.security import Principal
from clipos.media.errors import MediaPipelineError, UploadValidationError
from clipos.models import (
    MediaMetadata,
    Transcript,
    TranscriptChunk,
    TranscriptSegment,
    TranscriptWord,
)
from clipos.services.assets import AssetService

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/sources", response_model=list[SourceCapability])
async def source_capabilities(
    _: Principal = Depends(get_principal),
) -> list[SourceCapability]:
    return [
        SourceCapability(
            source=AssetSource.LOCAL_UPLOAD,
            mode="real_tested",
            enabled=True,
            limitations=[
                "authorized audio/video files only",
                "private local storage in development",
                "S3-compatible storage is not implemented",
            ],
            required_configuration_names=[
                "CLIPOS_MAX_UPLOAD_BYTES",
                "CLIPOS_STORAGE_LOCAL_ROOT",
            ],
        ),
        SourceCapability(
            source=AssetSource.URL,
            mode="disabled",
            enabled=False,
            limitations=[
                "disabled until redirect and DNS-rebinding-safe SSRF controls are implemented",
                "does not bypass authentication, CAPTCHA, paywalls, or access controls",
            ],
            required_configuration_names=[],
        ),
    ]


@router.post(
    "/upload",
    response_model=UploadAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    permission_confirmed: Annotated[bool, Form()],
    rights_note: Annotated[str, Form(min_length=10, max_length=4000)],
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> UploadAssetResponse:
    try:
        outcome = await AssetService.ingest_local_upload(
            session,
            storage=request.app.state.storage,
            principal=principal,
            upload=file,
            permission_confirmed=permission_confirmed,
            rights_note=rights_note,
            settings=settings,
        )
        if not outcome.duplicate:
            await AssetService.create_processing_job(
                session,
                asset=outcome.asset,
                actor_user_id=principal.user_id,
                queue=request.app.state.queue,
            )
            await session.refresh(outcome.asset)
        else:
            response.status_code = status.HTTP_200_OK
            response.headers["X-ClipOS-Duplicate"] = "true"
        return UploadAssetResponse(
            asset=AssetResponse.model_validate(outcome.asset),
            duplicate=outcome.duplicate,
            processing_job_id=outcome.asset.processing_job_id,
        )
    except UploadValidationError as exc:
        raise ClipOSError(
            exc.category,
            exc.safe_message,
            status_code=413 if exc.category == "upload_too_large" else 422,
        ) from exc
    except MediaPipelineError as exc:
        raise ClipOSError(
            exc.category,
            exc.safe_message,
            status_code=503 if exc.retryable else 422,
        ) from exc


@router.post("/from-url", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def ingest_from_url(
    _: URLIngestionRequest,
    __: Principal = Depends(require_role(Role.EDITOR)),
) -> None:
    raise ClipOSError(
        "url_ingestion_disabled",
        (
            "URL ingestion is disabled until DNS resolution, redirect revalidation, "
            "rebinding resistance, response limits, and source-policy controls are implemented"
        ),
        status_code=501,
    )


@router.get("", response_model=list[AssetResponse])
async def list_assets(
    state: AssetState | None = None,
    q: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[AssetResponse]:
    assets = await AssetService.list(
        session,
        principal.tenant_id,
        state=state,
        query_text=q,
        limit=limit,
    )
    return [AssetResponse.model_validate(asset) for asset in assets]


@router.get("/{asset_id}", response_model=AssetDetailResponse)
async def get_asset(
    asset_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AssetDetailResponse:
    asset = await AssetService.get(session, principal.tenant_id, asset_id)
    metadata = await session.scalar(
        select(MediaMetadata).where(
            MediaMetadata.asset_id == asset.id,
            MediaMetadata.tenant_id == principal.tenant_id,
        )
    )
    transcript = await session.scalar(
        select(Transcript).where(
            Transcript.asset_id == asset.id,
            Transcript.tenant_id == principal.tenant_id,
        )
    )
    return AssetDetailResponse(
        asset=AssetResponse.model_validate(asset),
        metadata=MediaMetadataResponse.model_validate(metadata) if metadata else None,
        transcript=TranscriptSummaryResponse.model_validate(transcript) if transcript else None,
    )


@router.get("/{asset_id}/transcript", response_model=TranscriptDetailResponse)
async def get_transcript(
    asset_id: UUID,
    segment_offset: int = Query(default=0, ge=0),
    segment_limit: int = Query(default=200, ge=1, le=1000),
    word_offset: int = Query(default=0, ge=0),
    word_limit: int = Query(default=2000, ge=0, le=5000),
    chunk_offset: int = Query(default=0, ge=0),
    chunk_limit: int = Query(default=500, ge=1, le=1000),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TranscriptDetailResponse:
    await AssetService.get(session, principal.tenant_id, asset_id)
    transcript = await session.scalar(
        select(Transcript).where(
            Transcript.asset_id == asset_id,
            Transcript.tenant_id == principal.tenant_id,
        )
    )
    if transcript is None:
        raise NotFoundError("transcript")
    segments = list(
        (
            await session.scalars(
                select(TranscriptSegment)
                .where(
                    TranscriptSegment.transcript_id == transcript.id,
                    TranscriptSegment.tenant_id == principal.tenant_id,
                )
                .order_by(TranscriptSegment.ordinal)
                .offset(segment_offset)
                .limit(segment_limit)
            )
        ).all()
    )
    words = (
        list(
            (
                await session.scalars(
                    select(TranscriptWord)
                    .where(
                        TranscriptWord.transcript_id == transcript.id,
                        TranscriptWord.tenant_id == principal.tenant_id,
                    )
                    .order_by(TranscriptWord.ordinal)
                    .offset(word_offset)
                    .limit(word_limit)
                )
            ).all()
        )
        if word_limit
        else []
    )
    chunks = list(
        (
            await session.scalars(
                select(TranscriptChunk)
                .where(
                    TranscriptChunk.transcript_id == transcript.id,
                    TranscriptChunk.tenant_id == principal.tenant_id,
                )
                .order_by(TranscriptChunk.ordinal)
                .offset(chunk_offset)
                .limit(chunk_limit)
            )
        ).all()
    )
    chunk_count = int(
        await session.scalar(
            select(func.count(TranscriptChunk.id)).where(
                TranscriptChunk.transcript_id == transcript.id,
                TranscriptChunk.tenant_id == principal.tenant_id,
            )
        )
        or 0
    )
    return TranscriptDetailResponse(
        transcript=TranscriptSummaryResponse.model_validate(transcript),
        original_text=transcript.original_text,
        normalized_text=transcript.normalized_text,
        segments=[TranscriptSegmentResponse.model_validate(item) for item in segments],
        words=[TranscriptWordResponse.model_validate(item) for item in words],
        chunks=[TranscriptChunkResponse.model_validate(item) for item in chunks],
        segment_offset=segment_offset,
        segment_limit=segment_limit,
        word_offset=word_offset,
        word_limit=word_limit,
        chunk_offset=chunk_offset,
        chunk_limit=chunk_limit,
        chunk_count=chunk_count,
    )


@router.post("/{asset_id}/retry", response_model=AssetResponse, status_code=202)
async def retry_asset(
    asset_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    try:
        asset = await AssetService.retry(
            session,
            principal=principal,
            asset_id=asset_id,
            queue=request.app.state.queue,
        )
        return AssetResponse.model_validate(asset)
    except MediaPipelineError as exc:
        raise ClipOSError(
            exc.category,
            exc.safe_message,
            status_code=503 if exc.retryable else 422,
        ) from exc


@router.post("/{asset_id}/cancel", response_model=AssetResponse)
async def cancel_asset(
    asset_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> AssetResponse:
    return AssetResponse.model_validate(
        await AssetService.cancel(session, principal=principal, asset_id=asset_id)
    )
