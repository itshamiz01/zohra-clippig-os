from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_runtime_settings, get_session, require_role
from clipos.config import Settings
from clipos.core.enums import GateOutcome, Role
from clipos.core.errors import ClipOSError
from clipos.core.security import Principal
from clipos.quality_schemas import (
    GateVerificationRequest,
    GateVerificationResponse,
    QualityCheckResponse,
    QualityEvaluationCreate,
    QualityEvaluationDetail,
    QualityEvaluationList,
    QualityEvaluationSummary,
    QualityReviewDecisionResponse,
    QualityReviewRequest,
    RightsEvidenceCreate,
    RightsEvidenceResponse,
    RightsRevocationRequest,
)
from clipos.services.quality import EvaluationInspection, QualityGateService
from clipos.storage.base import ObjectStorage

router = APIRouter(tags=["quality-and-rights"])


def _storage(request: Request) -> ObjectStorage:
    storage = request.app.state.storage
    if storage is None:
        raise ClipOSError(
            "quality_storage_unavailable",
            "quality gates require a configured private object-storage implementation",
            status_code=503,
        )
    return cast(ObjectStorage, storage)


def _summary(evaluation: object) -> QualityEvaluationSummary:
    return QualityEvaluationSummary.model_validate(evaluation)


def _detail(inspection: EvaluationInspection) -> QualityEvaluationDetail:
    evaluation = inspection.evaluation
    return QualityEvaluationDetail(
        evaluation=_summary(evaluation),
        input_hash=evaluation.input_hash,
        evidence_hash=evaluation.evidence_hash,
        model_assistance_used=evaluation.model_assistance_used,
        policy_explanation=evaluation.policy_explanation,
        missing_evidence=evaluation.missing_evidence,
        remediation=evaluation.remediation,
        hard_block_reasons=evaluation.hard_block_reasons,
        approval_evidence=evaluation.approval_evidence,
        approval_signature=evaluation.approval_signature,
        approval_expires_at=evaluation.approval_expires_at,
        checks=[QualityCheckResponse.model_validate(item) for item in inspection.checks],
        rights=(
            RightsEvidenceResponse.model_validate(inspection.rights)
            if inspection.rights is not None
            else None
        ),
        rights_state=inspection.rights_state,
        decisions=[
            QualityReviewDecisionResponse.model_validate(item) for item in inspection.decisions
        ],
        verifications=[
            GateVerificationResponse.model_validate(item) for item in inspection.verifications
        ],
        stale=inspection.stale,
        stale_reasons=inspection.stale_reasons,
    )


@router.post(
    "/rights-evidence",
    response_model=RightsEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_rights_evidence(
    payload: RightsEvidenceCreate,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> RightsEvidenceResponse:
    evidence = await QualityGateService.record_rights_evidence(
        session, principal=principal, request=payload
    )
    return RightsEvidenceResponse.model_validate(evidence)


@router.get("/rights-evidence", response_model=list[RightsEvidenceResponse])
async def list_rights_evidence(
    asset_id: UUID | None = None,
    render_job_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> list[RightsEvidenceResponse]:
    items = await QualityGateService.list_rights_evidence(
        session,
        principal=principal,
        asset_id=asset_id,
        render_job_id=render_job_id,
        limit=limit,
    )
    return [RightsEvidenceResponse.model_validate(item) for item in items]


@router.get("/rights-evidence/{evidence_id}", response_model=RightsEvidenceResponse)
async def get_rights_evidence(
    evidence_id: UUID,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> RightsEvidenceResponse:
    evidence = await QualityGateService.get_rights_evidence(
        session, principal=principal, evidence_id=evidence_id
    )
    return RightsEvidenceResponse.model_validate(evidence)


@router.post(
    "/rights-evidence/{evidence_id}/revoke",
    response_model=RightsEvidenceResponse,
)
async def revoke_rights_evidence(
    evidence_id: UUID,
    payload: RightsRevocationRequest,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> RightsEvidenceResponse:
    evidence = await QualityGateService.revoke_rights_evidence(
        session,
        principal=principal,
        evidence_id=evidence_id,
        reason=payload.reason,
    )
    return RightsEvidenceResponse.model_validate(evidence)


@router.post(
    "/renders/{render_job_id}/quality-evaluations",
    response_model=QualityEvaluationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_quality_evaluation(
    render_job_id: UUID,
    payload: QualityEvaluationCreate,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> QualityEvaluationSummary:
    result = await QualityGateService.create_evaluation(
        session,
        principal=principal,
        render_job_id=render_job_id,
        request=payload,
        settings=settings,
        storage=_storage(request),
    )
    if result.idempotent:
        response.status_code = status.HTTP_200_OK
        response.headers["X-ClipOS-Idempotent-Replay"] = "true"
    return _summary(result.evaluation)


@router.get("/quality-evaluations", response_model=QualityEvaluationList)
async def list_quality_evaluations(
    outcome: GateOutcome | None = None,
    render_job_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> QualityEvaluationList:
    rows, total = await QualityGateService.list_evaluations(
        session,
        principal=principal,
        outcome=outcome,
        render_job_id=render_job_id,
        offset=offset,
        limit=limit,
    )
    return QualityEvaluationList(
        items=[_summary(item) for item in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/quality-evaluations/{evaluation_id}", response_model=QualityEvaluationDetail)
async def get_quality_evaluation(
    evaluation_id: UUID,
    request: Request,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> QualityEvaluationDetail:
    inspection = await QualityGateService.inspect_evaluation(
        session,
        principal=principal,
        evaluation_id=evaluation_id,
        settings=settings,
        storage=_storage(request),
    )
    return _detail(inspection)


@router.post(
    "/quality-evaluations/{evaluation_id}/decisions",
    response_model=QualityEvaluationDetail,
)
async def decide_quality_evaluation(
    evaluation_id: UUID,
    payload: QualityReviewRequest,
    request: Request,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> QualityEvaluationDetail:
    storage = _storage(request)
    await QualityGateService.review(
        session,
        principal=principal,
        evaluation_id=evaluation_id,
        request=payload,
        settings=settings,
        storage=storage,
    )
    inspection = await QualityGateService.inspect_evaluation(
        session,
        principal=principal,
        evaluation_id=evaluation_id,
        settings=settings,
        storage=storage,
    )
    return _detail(inspection)


@router.post(
    "/quality-evaluations/{evaluation_id}/verify",
    response_model=GateVerificationResponse,
)
async def verify_quality_gate(
    evaluation_id: UUID,
    payload: GateVerificationRequest,
    request: Request,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> GateVerificationResponse:
    verification = await QualityGateService.verify(
        session,
        principal=principal,
        evaluation_id=evaluation_id,
        checkpoint=payload.checkpoint,
        expected_destination=payload.expected_destination,
        expected_territory=payload.expected_territory,
        settings=settings,
        storage=_storage(request),
    )
    if not verification.accepted:
        raise ClipOSError(
            "mandatory_gate_denied",
            "the mandatory quality and rights gate rejected this external-action checkpoint",
            status_code=409,
            details={
                "verification_id": str(verification.id),
                "failure_reasons": verification.failure_reasons,
            },
        )
    return GateVerificationResponse.model_validate(verification)
