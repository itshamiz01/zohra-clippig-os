from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_runtime_settings, get_session, require_role
from clipos.config import Settings
from clipos.core.enums import RenderState, Role
from clipos.core.errors import ClipOSError, NotFoundError
from clipos.core.security import Principal
from clipos.render_schemas import (
    RenderCapabilitiesResponse,
    RenderCaptionSegmentResponse,
    RenderCreateResponse,
    RenderDefectCreate,
    RenderDefectResponse,
    RenderDetailResponse,
    RenderJobResponse,
    RenderListItem,
    RenderListResponse,
    RenderOutputResponse,
    RenderPlanCreateRequest,
    RenderPlanResponse,
    TenantRenderProfileResponse,
    TenantRenderProfileUpdate,
)
from clipos.services.rendering import RenderingService

router = APIRouter(tags=["rendering"])


@router.get("/rendering/capabilities", response_model=RenderCapabilitiesResponse)
async def rendering_capabilities(
    request: Request,
    _: Principal = Depends(get_principal),
    settings: Settings = Depends(get_runtime_settings),
) -> RenderCapabilitiesResponse:
    return RenderCapabilitiesResponse.model_validate(
        RenderingService.capabilities(settings, request.app.state.media_tools)
    )


@router.get("/rendering/profile", response_model=TenantRenderProfileResponse)
async def get_render_profile(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TenantRenderProfileResponse:
    profile = await RenderingService.get_profile(session, principal.tenant_id)
    if profile is not None:
        return TenantRenderProfileResponse.model_validate(profile)
    return TenantRenderProfileResponse(
        tenant_id=principal.tenant_id,
        branding_enabled=False,
        branding_text=None,
        branding_position="top_right",
        branding_opacity=0.85,
        branding_font_size=36,
    )


@router.put("/rendering/profile", response_model=TenantRenderProfileResponse)
async def update_render_profile(
    payload: TenantRenderProfileUpdate,
    principal: Principal = Depends(require_role(Role.OWNER)),
    session: AsyncSession = Depends(get_session),
) -> TenantRenderProfileResponse:
    profile = await RenderingService.update_profile(session, principal=principal, request=payload)
    return TenantRenderProfileResponse.model_validate(profile)


@router.post(
    "/candidates/{candidate_id}/render-plans",
    response_model=RenderCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_render_plan(
    candidate_id: UUID,
    payload: RenderPlanCreateRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> RenderCreateResponse:
    outcome = await RenderingService.create_plan(
        session,
        principal=principal,
        candidate_id=candidate_id,
        request=payload,
        settings=settings,
        media_tools=request.app.state.media_tools,
        queue=request.app.state.queue,
    )
    if outcome.idempotent:
        response.status_code = status.HTTP_200_OK
        response.headers["X-ClipOS-Idempotent-Replay"] = "true"
    return RenderCreateResponse(
        plan=RenderPlanResponse.model_validate(outcome.plan),
        captions=[RenderCaptionSegmentResponse.model_validate(item) for item in outcome.captions],
        render=RenderJobResponse.model_validate(outcome.render_job),
        idempotent=outcome.idempotent,
    )


@router.get("/renders", response_model=RenderListResponse)
async def list_renders(
    state: RenderState | None = None,
    candidate_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RenderListResponse:
    rows, total = await RenderingService.list_jobs(
        session,
        tenant_id=principal.tenant_id,
        state=state,
        candidate_id=candidate_id,
        offset=offset,
        limit=limit,
    )
    return RenderListResponse(
        items=[
            RenderListItem(
                render=RenderJobResponse.model_validate(row.render_job),
                plan=RenderPlanResponse.model_validate(row.plan),
                current_output=(
                    RenderOutputResponse.model_validate(row.current_output)
                    if row.current_output
                    else None
                ),
            )
            for row in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/renders/{render_job_id}", response_model=RenderDetailResponse)
async def get_render(
    render_job_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> RenderDetailResponse:
    detail = await RenderingService.get_detail(session, principal.tenant_id, render_job_id)
    return RenderDetailResponse(
        plan=RenderPlanResponse.model_validate(detail.plan),
        captions=[RenderCaptionSegmentResponse.model_validate(item) for item in detail.captions],
        render=RenderJobResponse.model_validate(detail.render_job),
        outputs=[RenderOutputResponse.model_validate(item) for item in detail.outputs],
        defects=[RenderDefectResponse.model_validate(item) for item in detail.defects],
    )


@router.post("/renders/{render_job_id}/cancel", response_model=RenderJobResponse)
async def cancel_render(
    render_job_id: UUID,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> RenderJobResponse:
    render_job = await RenderingService.cancel(
        session, principal=principal, render_job_id=render_job_id
    )
    return RenderJobResponse.model_validate(render_job)


@router.post(
    "/renders/{render_job_id}/retry",
    response_model=RenderJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_render(
    render_job_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> RenderJobResponse:
    render_job = await RenderingService.retry(
        session,
        principal=principal,
        render_job_id=render_job_id,
        queue=request.app.state.queue,
    )
    return RenderJobResponse.model_validate(render_job)


@router.post(
    "/renders/{render_job_id}/defects",
    response_model=RenderDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def report_render_defect(
    render_job_id: UUID,
    payload: RenderDefectCreate,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> RenderDetailResponse:
    await RenderingService.report_defect(
        session,
        principal=principal,
        render_job_id=render_job_id,
        request=payload,
    )
    detail = await RenderingService.get_detail(session, principal.tenant_id, render_job_id)
    return RenderDetailResponse(
        plan=RenderPlanResponse.model_validate(detail.plan),
        captions=[RenderCaptionSegmentResponse.model_validate(item) for item in detail.captions],
        render=RenderJobResponse.model_validate(detail.render_job),
        outputs=[RenderOutputResponse.model_validate(item) for item in detail.outputs],
        defects=[RenderDefectResponse.model_validate(item) for item in detail.defects],
    )


async def _render_file_response(
    *,
    render_job_id: UUID,
    kind: Literal["output", "captions"],
    disposition: Literal["inline", "attachment"],
    request: Request,
    principal: Principal,
    session: AsyncSession,
) -> FileResponse:
    output = await RenderingService.current_output(session, principal.tenant_id, render_job_id)
    if kind == "captions":
        if not output.sidecar_storage_key:
            raise NotFoundError("caption sidecar")
        key = output.sidecar_storage_key
        media_type = "application/x-subrip; charset=utf-8"
        filename = f"clipos-{render_job_id}.srt"
    else:
        key = output.storage_key
        media_type = output.content_type
        filename = f"clipos-{render_job_id}.mp4"
    try:
        path = await request.app.state.storage.path_for_read(principal.tenant_id, key)
        stored = await request.app.state.storage.head(principal.tenant_id, key)
        expected_checksum = (
            output.sidecar_checksum_sha256 if kind == "captions" else output.checksum_sha256
        )
        if (
            stored is None
            or stored.checksum_sha256 != expected_checksum
            or (kind == "output" and stored.size != output.size_bytes)
        ):
            raise OSError("stored object no longer matches immutable output provenance")
    except FileNotFoundError as exc:
        raise NotFoundError("render output object") from exc
    except OSError as exc:
        raise ClipOSError(
            "render_output_integrity_failure",
            "stored render output failed integrity verification",
            status_code=503,
        ) from exc
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type=disposition,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/renders/{render_job_id}/preview", response_class=FileResponse)
async def preview_render(
    render_job_id: UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    return await _render_file_response(
        render_job_id=render_job_id,
        kind="output",
        disposition="inline",
        request=request,
        principal=principal,
        session=session,
    )


@router.get("/renders/{render_job_id}/download", response_class=FileResponse)
async def download_render(
    render_job_id: UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    return await _render_file_response(
        render_job_id=render_job_id,
        kind="output",
        disposition="attachment",
        request=request,
        principal=principal,
        session=session,
    )


@router.get("/renders/{render_job_id}/captions", response_class=FileResponse)
async def download_render_captions(
    render_job_id: UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    return await _render_file_response(
        render_job_id=render_job_id,
        kind="captions",
        disposition="attachment",
        request=request,
        principal=principal,
        session=session,
    )
