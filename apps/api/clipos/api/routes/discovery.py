from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_runtime_settings, get_session, require_role
from clipos.config import Settings
from clipos.core.enums import CandidateState, DiscoveryRunState, Role
from clipos.core.security import Principal
from clipos.discovery_schemas import (
    CandidateBoundaryUpdate,
    CandidateContextResponse,
    CandidateDetailResponse,
    CandidateListResponse,
    CandidateResponse,
    CandidateReviewRequest,
    CandidateReviewResponse,
    ContextSegmentResponse,
    DiscoveryRequest,
    DiscoveryResultResponse,
    DiscoveryRunListResponse,
    DiscoveryRunResponse,
    ScoreComponentResponse,
)
from clipos.services.discovery import DiscoveryService

router = APIRouter(tags=["clip discovery"])


@router.post(
    "/assets/{asset_id}/discover",
    response_model=DiscoveryResultResponse,
)
async def discover_candidates(
    asset_id: UUID,
    payload: DiscoveryRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> DiscoveryResultResponse:
    outcome = await DiscoveryService.discover(
        session,
        principal=principal,
        asset_id=asset_id,
        request=payload,
        settings=settings,
        model_gateway=request.app.state.model_gateway,
    )
    response.status_code = status.HTTP_200_OK if outcome.idempotent else status.HTTP_201_CREATED
    return DiscoveryResultResponse(
        run=DiscoveryRunResponse.model_validate(outcome.run),
        candidates=[CandidateResponse.model_validate(item) for item in outcome.candidates],
        idempotent=outcome.idempotent,
    )


@router.get("/discovery/runs", response_model=DiscoveryRunListResponse)
async def list_discovery_runs(
    asset_id: UUID | None = None,
    state: DiscoveryRunState | None = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DiscoveryRunListResponse:
    rows, total = await DiscoveryService.list_runs(
        session,
        tenant_id=principal.tenant_id,
        asset_id=asset_id,
        state=state,
        offset=offset,
        limit=limit,
    )
    return DiscoveryRunListResponse(
        items=[DiscoveryRunResponse.model_validate(item) for item in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/discovery/runs/{run_id}",
    response_model=DiscoveryRunResponse,
)
async def get_discovery_run(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DiscoveryRunResponse:
    run = await DiscoveryService.get_run(session, principal.tenant_id, run_id)
    return DiscoveryRunResponse.model_validate(run)


@router.get("/discovery/scoring", response_model=dict[str, Any])
async def scoring_definition(
    _: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return DiscoveryService.score_definitions()


@router.get("/candidates", response_model=CandidateListResponse)
async def list_candidates(
    asset_id: UUID | None = None,
    state: CandidateState | None = None,
    language: Annotated[str | None, Query(min_length=2, max_length=35)] = None,
    topic: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CandidateListResponse:
    rows, total = await DiscoveryService.list_candidates(
        session,
        tenant_id=principal.tenant_id,
        asset_id=asset_id,
        state=state,
        language=language,
        topic=topic,
        min_score=min_score,
        offset=offset,
        limit=limit,
    )
    return CandidateListResponse(
        items=[CandidateResponse.model_validate(item) for item in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateDetailResponse)
async def candidate_detail(
    candidate_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CandidateDetailResponse:
    candidate, scores, reviews = await DiscoveryService.candidate_detail(
        session, principal.tenant_id, candidate_id
    )
    return CandidateDetailResponse(
        candidate=CandidateResponse.model_validate(candidate),
        scores=[ScoreComponentResponse.model_validate(item) for item in scores],
        reviews=[CandidateReviewResponse.model_validate(item) for item in reviews],
    )


@router.get(
    "/candidates/{candidate_id}/context",
    response_model=CandidateContextResponse,
)
async def candidate_context(
    candidate_id: UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> CandidateContextResponse:
    candidate, metadata, segments = await DiscoveryService.context_segments(
        session, principal.tenant_id, candidate_id
    )
    return CandidateContextResponse(
        candidate_id=candidate.id,
        asset_id=candidate.asset_id,
        duration_ms=round(metadata.duration_seconds * 1000),
        context_start_ms=segments[0].start_ms if segments else candidate.start_ms,
        context_end_ms=segments[-1].end_ms if segments else candidate.end_ms,
        segments=[
            ContextSegmentResponse(
                ordinal=item.ordinal,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                text=item.text,
                normalized_text=item.normalized_text,
                confidence=item.confidence,
                speaker_label=item.speaker_label,
                selected=(
                    candidate.start_segment_ordinal <= item.ordinal <= candidate.end_segment_ordinal
                ),
            )
            for item in segments
        ],
    )


@router.post(
    "/candidates/{candidate_id}/review",
    response_model=CandidateResponse,
)
async def review_candidate(
    candidate_id: UUID,
    payload: CandidateReviewRequest,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> CandidateResponse:
    candidate = await DiscoveryService.review(
        session,
        principal=principal,
        candidate_id=candidate_id,
        request=payload,
    )
    return CandidateResponse.model_validate(candidate)


@router.patch(
    "/candidates/{candidate_id}/boundaries",
    response_model=CandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def edit_candidate_boundaries(
    candidate_id: UUID,
    payload: CandidateBoundaryUpdate,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> CandidateResponse:
    candidate = await DiscoveryService.edit_boundaries(
        session,
        principal=principal,
        candidate_id=candidate_id,
        request=payload,
        settings=settings,
    )
    return CandidateResponse.model_validate(candidate)
