from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.config import Settings
from clipos.core.enums import (
    AuditOutcome,
    CandidateState,
    DestinationPlatform,
    EvidenceSource,
    GateCheckpoint,
    GateOutcome,
    GateReviewAction,
    MaterialClearance,
    QualityCheckKind,
    QualityCheckStatus,
    QualitySeverity,
    RenderState,
    RightsEvidenceStatus,
    RightsScope,
    Role,
    TranscriptState,
)
from clipos.core.errors import AuthorizationError, ClipOSError, ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.models import (
    Asset,
    ClipCandidate,
    GateVerification,
    MediaMetadata,
    QualityCheckResult,
    QualityEvaluation,
    QualityReviewDecision,
    RenderCaptionSegment,
    RenderDefect,
    RenderJob,
    RenderOutput,
    RenderPlan,
    RightsEvidence,
    Transcript,
    TranscriptSegment,
)
from clipos.quality_schemas import (
    QualityEvaluationCreate,
    QualityReviewRequest,
    RightsEvidenceCreate,
)
from clipos.services.audit import AuditService
from clipos.storage.base import ObjectInfo, ObjectStorage

EVALUATION_VERSION = "quality-council.v1"
POLICY_VERSION = "mandatory-release-gate.v1"
RIGHTS_EVIDENCE_VERSION = "rights-evidence.v1"
CHECK_VERSION = "quality-checks.v1"
SIGNATURE_CONTEXT = b"zohra-clipos-phase5-gate-v1\0"

_EXPECTED_CHECKS = set(QualityCheckKind)
_REQUIRED_MATERIAL_KEYS = {
    "music",
    "footage",
    "logos",
    "likeness_or_voice",
    "personal_data",
    "sensitive_information",
}

_DESTINATION_PROFILES: dict[DestinationPlatform, dict[str, Any]] = {
    DestinationPlatform.YOUTUBE_SHORTS: {
        "max_duration_seconds": 180.0,
        "aspect_ratios": {"9:16", "1:1"},
        "video_codec": "h264",
        "audio_codec": "aac",
        "profile_version": "internal-youtube-shorts-2026-10",
    },
    DestinationPlatform.INSTAGRAM_REELS: {
        "max_duration_seconds": 180.0,
        "aspect_ratios": {"9:16"},
        "video_codec": "h264",
        "audio_codec": "aac",
        "profile_version": "internal-instagram-reels-2026-10",
    },
    DestinationPlatform.TIKTOK: {
        "max_duration_seconds": 180.0,
        "aspect_ratios": {"9:16"},
        "video_codec": "h264",
        "audio_codec": "aac",
        "profile_version": "internal-tiktok-2026-10",
    },
    DestinationPlatform.LINKEDIN: {
        "max_duration_seconds": 600.0,
        "aspect_ratios": {"9:16", "1:1", "16:9"},
        "video_codec": "h264",
        "audio_codec": "aac",
        "profile_version": "internal-linkedin-2026-10",
    },
}


@dataclass(frozen=True, slots=True)
class CheckDraft:
    kind: QualityCheckKind
    status: QualityCheckStatus
    severity: QualitySeverity
    evidence: dict[str, Any]
    confidence: float
    remediation: str
    hard_block: bool = False
    evidence_source: EvidenceSource = EvidenceSource.DETERMINISTIC
    version: str = CHECK_VERSION


@dataclass(frozen=True, slots=True)
class RightsAssessment:
    state: str
    valid: bool
    hard_blocks: list[str]
    missing_evidence: list[str]
    remediation: list[str]
    explanations: list[str]


@dataclass(frozen=True, slots=True)
class LoadedContext:
    render: RenderJob
    plan: RenderPlan
    output: RenderOutput
    candidate: ClipCandidate
    asset: Asset
    transcript: Transcript
    metadata: MediaMetadata | None
    captions: list[RenderCaptionSegment]
    transcript_segments: list[TranscriptSegment]
    defects: list[RenderDefect]
    rights: RightsEvidence | None
    output_object: ObjectInfo | None
    sidecar_object: ObjectInfo | None


@dataclass(frozen=True, slots=True)
class EvaluationCreation:
    evaluation: QualityEvaluation
    idempotent: bool


@dataclass(frozen=True, slots=True)
class EvaluationInspection:
    evaluation: QualityEvaluation
    checks: list[QualityCheckResult]
    rights: RightsEvidence | None
    rights_state: str
    decisions: list[QualityReviewDecision]
    verifications: list[GateVerification]
    stale: bool
    stale_reasons: list[str]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_document(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sign_document(value: dict[str, Any], secret: str) -> str:
    return hmac.new(
        SIGNATURE_CONTEXT + secret.encode("utf-8"),
        _canonical_json(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right, autojunk=False).ratio()


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class QualityGateService:
    """Mandatory Phase 5 quality, rights, and pre-external-action gate.

    The service is deliberately reusable: Phase 6 must call ``verify`` at publish-job
    acceptance and again immediately before an external upload. A stored signature or an
    earlier verification is never sufficient by itself because this method re-reads rights,
    provenance, defects, and private-object integrity on every call.
    """

    @staticmethod
    async def record_rights_evidence(
        session: AsyncSession,
        *,
        principal: Principal,
        request: RightsEvidenceCreate,
    ) -> RightsEvidence:
        principal.require(Role.ADMIN)
        now = datetime.now(UTC)
        if request.permission_recorded_at > now + timedelta(minutes=5):
            raise ClipOSError(
                "rights_timestamp_invalid",
                "permission evidence cannot be recorded with a future permission timestamp",
                status_code=422,
            )
        asset = await session.scalar(
            select(Asset).where(
                Asset.id == request.asset_id,
                Asset.tenant_id == principal.tenant_id,
            )
        )
        if asset is None:
            raise NotFoundError("asset")

        candidate: ClipCandidate | None = None
        render: RenderJob | None = None
        if request.candidate_id is not None:
            candidate = await session.scalar(
                select(ClipCandidate).where(
                    ClipCandidate.id == request.candidate_id,
                    ClipCandidate.tenant_id == principal.tenant_id,
                    ClipCandidate.asset_id == asset.id,
                )
            )
            if candidate is None:
                raise NotFoundError("candidate")
        if request.render_job_id is not None:
            row = (
                await session.execute(
                    select(RenderJob, RenderPlan)
                    .join(RenderPlan, RenderPlan.id == RenderJob.render_plan_id)
                    .where(
                        RenderJob.id == request.render_job_id,
                        RenderJob.tenant_id == principal.tenant_id,
                        RenderPlan.asset_id == asset.id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise NotFoundError("render")
            render, plan = row
            if candidate is not None and plan.candidate_id != candidate.id:
                raise ClipOSError(
                    "rights_scope_mismatch",
                    "candidate and render rights scope do not describe the same source clip",
                    status_code=422,
                )
            candidate = await session.scalar(
                select(ClipCandidate).where(
                    ClipCandidate.id == plan.candidate_id,
                    ClipCandidate.tenant_id == principal.tenant_id,
                )
            )
            if candidate is None:
                raise NotFoundError("candidate")

        payload = request.model_dump(mode="json")
        evidence = RightsEvidence(
            tenant_id=principal.tenant_id,
            asset_id=asset.id,
            candidate_id=(
                candidate.id if request.scope != RightsScope.ASSET and candidate else None
            ),
            render_job_id=render.id if request.scope == RightsScope.RENDER and render else None,
            recorded_by_user_id=principal.user_id,
            revoked_by_user_id=None,
            scope=request.scope,
            basis=request.basis,
            status=request.status,
            source_owner=request.source_owner,
            evidence_type=request.evidence_type,
            evidence_reference=request.evidence_reference,
            evidence_checksum_sha256=request.evidence_checksum_sha256,
            permission_recorded_at=request.permission_recorded_at,
            effective_from=request.effective_from,
            expires_at=request.expires_at,
            no_expiration_asserted=request.no_expiration_asserted,
            allowed_platforms=[item.value for item in request.allowed_platforms],
            allowed_territories=request.allowed_territories,
            commercial_use_allowed=request.commercial_use_allowed,
            editing_allowed=request.editing_allowed,
            ai_processing_allowed=request.ai_processing_allowed,
            attribution_required=request.attribution_required,
            attribution_text=request.attribution_text,
            license_terms=request.license_terms,
            restrictions=request.restrictions,
            third_party_material=payload["third_party_material"],
            campaign_reference=request.campaign_reference,
            evidence_version=RIGHTS_EVIDENCE_VERSION,
            revoked_at=None,
            revocation_reason=None,
        )
        session.add(evidence)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="rights_evidence.recorded",
            resource_type="rights_evidence",
            resource_id=str(evidence.id),
            event_data={
                "asset_id": str(asset.id),
                "candidate_id": str(evidence.candidate_id) if evidence.candidate_id else None,
                "render_job_id": str(evidence.render_job_id) if evidence.render_job_id else None,
                "scope": evidence.scope.value,
                "basis": evidence.basis.value,
                "status": evidence.status.value,
                "allowed_platforms": evidence.allowed_platforms,
                "allowed_territories": evidence.allowed_territories,
                "expires_at": _datetime_text(evidence.expires_at),
                "has_evidence_checksum": bool(evidence.evidence_checksum_sha256),
            },
        )
        await session.commit()
        return evidence

    @staticmethod
    async def list_rights_evidence(
        session: AsyncSession,
        *,
        principal: Principal,
        asset_id: UUID | None = None,
        render_job_id: UUID | None = None,
        limit: int = 100,
    ) -> list[RightsEvidence]:
        principal.require(Role.REVIEWER)
        statement = select(RightsEvidence).where(RightsEvidence.tenant_id == principal.tenant_id)
        if asset_id is not None:
            statement = statement.where(RightsEvidence.asset_id == asset_id)
        if render_job_id is not None:
            statement = statement.where(RightsEvidence.render_job_id == render_job_id)
        rows = await session.scalars(
            statement.order_by(RightsEvidence.created_at.desc()).limit(min(limit, 200))
        )
        return list(rows.all())

    @staticmethod
    async def get_rights_evidence(
        session: AsyncSession,
        *,
        principal: Principal,
        evidence_id: UUID,
    ) -> RightsEvidence:
        principal.require(Role.REVIEWER)
        evidence = await session.scalar(
            select(RightsEvidence).where(
                RightsEvidence.id == evidence_id,
                RightsEvidence.tenant_id == principal.tenant_id,
            )
        )
        if evidence is None:
            raise NotFoundError("rights evidence")
        return evidence

    @staticmethod
    async def revoke_rights_evidence(
        session: AsyncSession,
        *,
        principal: Principal,
        evidence_id: UUID,
        reason: str,
    ) -> RightsEvidence:
        principal.require(Role.ADMIN)
        evidence = await session.scalar(
            select(RightsEvidence)
            .where(
                RightsEvidence.id == evidence_id,
                RightsEvidence.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if evidence is None:
            raise NotFoundError("rights evidence")
        if evidence.revoked_at is not None:
            raise ConflictError("rights evidence has already been revoked")
        evidence.revoked_at = datetime.now(UTC)
        evidence.revoked_by_user_id = principal.user_id
        evidence.revocation_reason = reason
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="rights_evidence.revoked",
            resource_type="rights_evidence",
            resource_id=str(evidence.id),
            event_data={
                "asset_id": str(evidence.asset_id),
                "render_job_id": str(evidence.render_job_id) if evidence.render_job_id else None,
                "reason": reason,
            },
        )
        await session.commit()
        return evidence

    @classmethod
    async def create_evaluation(
        cls,
        session: AsyncSession,
        *,
        principal: Principal,
        render_job_id: UUID,
        request: QualityEvaluationCreate,
        settings: Settings,
        storage: ObjectStorage,
    ) -> EvaluationCreation:
        principal.require(Role.EDITOR)
        existing = await session.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.tenant_id == principal.tenant_id,
                QualityEvaluation.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.render_job_id != render_job_id
                or existing.rights_evidence_id != request.rights_evidence_id
                or existing.destination != request.destination
                or existing.territory != request.territory
                or existing.campaign_reference != request.campaign_reference
            ):
                raise ConflictError("quality evaluation idempotency key was reused with new input")
            return EvaluationCreation(evaluation=existing, idempotent=True)

        context = await cls._load_context(
            session,
            tenant_id=principal.tenant_id,
            render_job_id=render_job_id,
            rights_evidence_id=request.rights_evidence_id,
            storage=storage,
            require_current_output=True,
        )
        rights = cls._assess_rights(
            context.rights,
            context=context,
            destination=request.destination,
            territory=request.territory,
            campaign_reference=request.campaign_reference,
        )
        checks = await cls._build_checks(
            session,
            context=context,
            destination=request.destination,
            campaign_reference=request.campaign_reference,
            settings=settings,
        )
        outcome, aggregation_reasons = cls.aggregate_outcome(checks, rights)
        input_hash = cls._input_hash(
            context,
            destination=request.destination,
            territory=request.territory,
            campaign_reference=request.campaign_reference,
        )
        evidence_hash = _hash_document(
            {
                "checks": [cls._draft_document(item) for item in checks],
                "rights": cls._rights_assessment_document(rights),
                "evaluation_version": EVALUATION_VERSION,
                "policy_version": POLICY_VERSION,
            }
        )
        policy_explanation = _deduplicate(
            [
                "Rights, authorization, security, and technical-integrity failures are "
                "non-overridable and quality cannot compensate for them.",
                "Deterministic failures cannot be replaced by model judgment.",
                "Publication remains unavailable; this evaluation creates no external action.",
                *rights.explanations,
                *aggregation_reasons,
            ]
        )
        remediation = _deduplicate(
            [
                *rights.remediation,
                *(item.remediation for item in checks if item.status != QualityCheckStatus.PASSED),
            ]
        )
        hard_blocks = _deduplicate(
            [
                *rights.hard_blocks,
                *(f"{item.kind.value}: {item.remediation}" for item in checks if item.hard_block),
            ]
        )
        evaluation = QualityEvaluation(
            tenant_id=principal.tenant_id,
            render_job_id=context.render.id,
            render_output_id=context.output.id,
            render_plan_id=context.plan.id,
            candidate_id=context.candidate.id,
            asset_id=context.asset.id,
            rights_evidence_id=context.rights.id if context.rights else None,
            requested_by_user_id=principal.user_id,
            idempotency_key=request.idempotency_key,
            destination=request.destination,
            territory=request.territory,
            campaign_reference=request.campaign_reference,
            outcome=outcome,
            automated_outcome=outcome,
            evaluation_version=EVALUATION_VERSION,
            policy_version=POLICY_VERSION,
            input_hash=input_hash,
            evidence_hash=evidence_hash,
            model_assistance_used=False,
            policy_explanation=policy_explanation,
            missing_evidence=_deduplicate(rights.missing_evidence),
            remediation=remediation,
            hard_block_reasons=hard_blocks,
            approval_evidence=None,
            approval_signature=None,
            approval_expires_at=None,
            evaluated_at=datetime.now(UTC),
        )
        session.add(evaluation)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            replay = await session.scalar(
                select(QualityEvaluation).where(
                    QualityEvaluation.tenant_id == principal.tenant_id,
                    QualityEvaluation.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                return EvaluationCreation(evaluation=replay, idempotent=True)
            raise ConflictError("quality evaluation could not be created atomically") from exc

        for draft in checks:
            session.add(
                QualityCheckResult(
                    tenant_id=principal.tenant_id,
                    evaluation_id=evaluation.id,
                    kind=draft.kind,
                    status=draft.status,
                    severity=draft.severity,
                    evidence_source=draft.evidence_source,
                    evidence=draft.evidence,
                    confidence=draft.confidence,
                    check_version=draft.version,
                    remediation=draft.remediation,
                    hard_block=draft.hard_block,
                )
            )
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="quality_evaluation.created",
            resource_type="quality_evaluation",
            resource_id=str(evaluation.id),
            event_data={
                "render_job_id": str(render_job_id),
                "render_output_id": str(context.output.id),
                "rights_evidence_id": (
                    str(context.rights.id) if context.rights is not None else None
                ),
                "destination": request.destination.value,
                "territory": request.territory,
                "outcome": outcome.value,
                "input_hash": input_hash,
                "hard_block_count": len(hard_blocks),
                "model_assistance_used": False,
            },
        )
        await session.commit()
        return EvaluationCreation(evaluation=evaluation, idempotent=False)

    @staticmethod
    def aggregate_outcome(
        checks: list[CheckDraft], rights: RightsAssessment
    ) -> tuple[GateOutcome, list[str]]:
        """Combine independent evidence without allowing model evidence to erase facts."""

        explanations: list[str] = []
        deterministic_by_kind = {
            item.kind: item
            for item in checks
            if item.evidence_source == EvidenceSource.DETERMINISTIC
        }
        missing = sorted(item.value for item in _EXPECTED_CHECKS - deterministic_by_kind.keys())
        if missing:
            explanations.append(
                "Blocked because mandatory deterministic checks are missing: " + ", ".join(missing)
            )
            return GateOutcome.BLOCKED, explanations
        if not rights.valid or rights.hard_blocks:
            explanations.append("Blocked by unresolved or invalid rights evidence.")
            return GateOutcome.BLOCKED, explanations
        hard_failures = [
            item
            for item in deterministic_by_kind.values()
            if item.hard_block
            or (
                item.status == QualityCheckStatus.FAILED
                and item.severity == QualitySeverity.CRITICAL
            )
        ]
        if hard_failures:
            explanations.append(
                "Blocked by non-overridable deterministic evidence: "
                + ", ".join(item.kind.value for item in hard_failures)
            )
            return GateOutcome.BLOCKED, explanations
        failures = [
            item
            for item in deterministic_by_kind.values()
            if item.status == QualityCheckStatus.FAILED
        ]
        if failures:
            explanations.append(
                "Rejected until deterministic quality failures are remediated: "
                + ", ".join(item.kind.value for item in failures)
            )
            return GateOutcome.REJECTED, explanations
        uncertain = [
            item
            for item in deterministic_by_kind.values()
            if item.status in {QualityCheckStatus.NEEDS_REVIEW, QualityCheckStatus.UNAVAILABLE}
            or item.confidence < 0.7
        ]
        if uncertain:
            explanations.append(
                "Human review is mandatory for unavailable, low-confidence, or semantic checks: "
                + ", ".join(item.kind.value for item in uncertain)
            )
        else:
            explanations.append(
                "All deterministic checks passed; independent human release review remains "
                "mandatory."
            )
        return GateOutcome.NEEDS_HUMAN_REVIEW, explanations

    @classmethod
    async def review(
        cls,
        session: AsyncSession,
        *,
        principal: Principal,
        evaluation_id: UUID,
        request: QualityReviewRequest,
        settings: Settings,
        storage: ObjectStorage,
        decision_source: EvidenceSource = EvidenceSource.HUMAN,
    ) -> QualityReviewDecision:
        principal.require(Role.REVIEWER)
        if decision_source != EvidenceSource.HUMAN:
            raise AuthorizationError("AI or model actors cannot make gate approval decisions")
        evaluation = await session.scalar(
            select(QualityEvaluation)
            .where(
                QualityEvaluation.id == evaluation_id,
                QualityEvaluation.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if evaluation is None:
            raise NotFoundError("quality evaluation")
        if evaluation.requested_by_user_id == principal.user_id:
            raise AuthorizationError(
                "an independent reviewer must decide an evaluation they did not request"
            )
        if evaluation.outcome in {GateOutcome.APPROVED, GateOutcome.REJECTED}:
            raise ConflictError("quality evaluation already has a terminal human decision")

        checks = list(
            (
                await session.scalars(
                    select(QualityCheckResult)
                    .where(
                        QualityCheckResult.evaluation_id == evaluation.id,
                        QualityCheckResult.tenant_id == principal.tenant_id,
                    )
                    .order_by(QualityCheckResult.created_at, QualityCheckResult.kind)
                )
            ).all()
        )
        from_outcome = evaluation.outcome
        if request.action == GateReviewAction.APPROVE:
            if evaluation.automated_outcome != GateOutcome.NEEDS_HUMAN_REVIEW:
                raise ClipOSError(
                    "non_overridable_gate",
                    "blocked or rejected gate evidence cannot be approved by a human override",
                    status_code=409,
                    details={"automated_outcome": evaluation.automated_outcome.value},
                )
            if evaluation.hard_block_reasons or any(
                item.hard_block or item.status == QualityCheckStatus.FAILED for item in checks
            ):
                raise ClipOSError(
                    "non_overridable_gate",
                    "deterministic or critical gate failures must be remediated and re-evaluated",
                    status_code=409,
                )
            required_acknowledgements = {
                item.id
                for item in checks
                if item.status in {QualityCheckStatus.NEEDS_REVIEW, QualityCheckStatus.UNAVAILABLE}
                or item.confidence < settings.quality_min_confidence
            }
            supplied_acknowledgements = set(request.acknowledged_check_ids)
            if not required_acknowledgements.issubset(supplied_acknowledgements):
                raise ClipOSError(
                    "review_acknowledgement_incomplete",
                    "every uncertain or low-confidence check must be explicitly acknowledged",
                    status_code=422,
                    details={
                        "missing_check_ids": sorted(
                            str(item)
                            for item in required_acknowledgements - supplied_acknowledgements
                        )
                    },
                )
            context = await cls._load_context(
                session,
                tenant_id=principal.tenant_id,
                render_job_id=evaluation.render_job_id,
                rights_evidence_id=evaluation.rights_evidence_id,
                storage=storage,
                output_id=evaluation.render_output_id,
                require_current_output=False,
            )
            current_hash = cls._input_hash(
                context,
                destination=evaluation.destination,
                territory=evaluation.territory,
                campaign_reference=evaluation.campaign_reference,
            )
            rights = cls._assess_rights(
                context.rights,
                context=context,
                destination=evaluation.destination,
                territory=evaluation.territory,
                campaign_reference=evaluation.campaign_reference,
            )
            if current_hash != evaluation.input_hash:
                raise ClipOSError(
                    "stale_gate_evidence",
                    "quality or rights evidence changed; run a new evaluation",
                    status_code=409,
                )
            if not rights.valid:
                raise ClipOSError(
                    "non_overridable_gate",
                    "rights evidence is not currently valid and cannot be overridden",
                    status_code=409,
                    details={"rights_state": rights.state},
                )
            to_outcome = GateOutcome.APPROVED
        else:
            to_outcome = GateOutcome.REJECTED

        decision = QualityReviewDecision(
            tenant_id=principal.tenant_id,
            evaluation_id=evaluation.id,
            reviewer_user_id=principal.user_id,
            action=request.action,
            decision_source=EvidenceSource.HUMAN,
            from_outcome=from_outcome,
            to_outcome=to_outcome,
            reason=request.reason,
            acknowledged_check_ids=[str(item) for item in request.acknowledged_check_ids],
        )
        session.add(decision)
        await session.flush()
        evaluation.outcome = to_outcome
        if to_outcome == GateOutcome.APPROVED:
            now = datetime.now(UTC)
            rights_expiry = context.rights.expires_at if context.rights else None
            expires = now + timedelta(minutes=settings.quality_approval_ttl_minutes)
            if rights_expiry is not None:
                expires = min(expires, _aware(rights_expiry))
            approval_evidence: dict[str, Any] = {
                "schema": "clipos-gate-approval.v1",
                "evaluation_id": str(evaluation.id),
                "decision_id": str(decision.id),
                "reviewer_user_id": str(principal.user_id),
                "render_job_id": str(evaluation.render_job_id),
                "render_output_id": str(evaluation.render_output_id),
                "rights_evidence_id": (
                    str(evaluation.rights_evidence_id)
                    if evaluation.rights_evidence_id is not None
                    else None
                ),
                "destination": evaluation.destination.value,
                "territory": evaluation.territory,
                "input_hash": evaluation.input_hash,
                "evidence_hash": evaluation.evidence_hash,
                "evaluation_version": evaluation.evaluation_version,
                "policy_version": evaluation.policy_version,
                "approved_at": _datetime_text(now),
                "expires_at": _datetime_text(expires),
                "attribution_required": bool(
                    context.rights and context.rights.attribution_required
                ),
                "attribution_text": (
                    context.rights.attribution_text
                    if context.rights and context.rights.attribution_required
                    else None
                ),
            }
            evaluation.approval_evidence = approval_evidence
            evaluation.approval_signature = _sign_document(
                approval_evidence, settings.secret_key.get_secret_value()
            )
            evaluation.approval_expires_at = expires
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action=f"quality_evaluation.{request.action.value}",
            resource_type="quality_evaluation",
            resource_id=str(evaluation.id),
            event_data={
                "from_outcome": from_outcome.value,
                "to_outcome": to_outcome.value,
                "reason": request.reason,
                "acknowledged_check_ids": decision.acknowledged_check_ids,
                "approval_expires_at": _datetime_text(evaluation.approval_expires_at),
            },
        )
        await session.commit()
        return decision

    @classmethod
    async def verify(
        cls,
        session: AsyncSession,
        *,
        principal: Principal,
        evaluation_id: UUID,
        checkpoint: GateCheckpoint,
        expected_destination: DestinationPlatform,
        expected_territory: str,
        settings: Settings,
        storage: ObjectStorage,
    ) -> GateVerification:
        """Re-check a gate for one exact future external-action checkpoint.

        The method records both accepted and denied attempts. Callers MUST check ``accepted``;
        API routes additionally translate a denial into HTTP 409. This method never creates a
        publish job, uploads media, or contacts a third party.
        """

        principal.require(Role.REVIEWER)
        evaluation = await session.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.id == evaluation_id,
                QualityEvaluation.tenant_id == principal.tenant_id,
            )
        )
        if evaluation is None:
            raise NotFoundError("quality evaluation")
        now = datetime.now(UTC)
        failures: list[str] = []
        if evaluation.destination != expected_destination:
            failures.append("destination does not match the approved destination")
        if evaluation.territory != expected_territory:
            failures.append("territory does not match the approved territory")
        if evaluation.outcome != GateOutcome.APPROVED:
            failures.append("quality evaluation is not approved")

        context = await cls._load_context(
            session,
            tenant_id=principal.tenant_id,
            render_job_id=evaluation.render_job_id,
            rights_evidence_id=evaluation.rights_evidence_id,
            storage=storage,
            output_id=evaluation.render_output_id,
            require_current_output=False,
        )
        observed_hash = cls._input_hash(
            context,
            destination=evaluation.destination,
            territory=evaluation.territory,
            campaign_reference=evaluation.campaign_reference,
        )
        if observed_hash != evaluation.input_hash:
            failures.append("approval evidence is stale because protected input changed")
        if not context.output.is_current:
            failures.append("approved render output is no longer the current output")
        if context.render.state != RenderState.SUCCEEDED:
            failures.append("render is no longer in the succeeded state")
        rights = cls._assess_rights(
            context.rights,
            context=context,
            destination=evaluation.destination,
            territory=evaluation.territory,
            campaign_reference=evaluation.campaign_reference,
            now=now,
        )
        if not rights.valid:
            failures.extend(rights.hard_blocks or [f"rights state is {rights.state}"])
        technical = cls._technical_check(context)
        if technical.status != QualityCheckStatus.PASSED:
            failures.append("fresh technical-integrity verification did not pass")
        if evaluation.approval_expires_at is None or _aware(evaluation.approval_expires_at) <= now:
            failures.append("approval evidence is missing or expired")
        if evaluation.approval_evidence is None or evaluation.approval_signature is None:
            failures.append("signed approval evidence is missing")
        elif not hmac.compare_digest(
            evaluation.approval_signature,
            _sign_document(evaluation.approval_evidence, settings.secret_key.get_secret_value()),
        ):
            failures.append("approval evidence signature is invalid")
        failures = _deduplicate(failures)
        accepted = not failures
        verification_id = uuid4()
        valid_until = now + timedelta(seconds=settings.quality_preflight_ttl_seconds)
        payload: dict[str, Any] = {
            "schema": "clipos-gate-verification.v1",
            "verification_id": str(verification_id),
            "evaluation_id": str(evaluation.id),
            "checkpoint": checkpoint.value,
            "accepted": accepted,
            "destination": expected_destination.value,
            "territory": expected_territory,
            "render_job_id": str(evaluation.render_job_id),
            "render_output_id": str(evaluation.render_output_id),
            "observed_input_hash": observed_hash,
            "verified_at": _datetime_text(now),
            "valid_until": _datetime_text(valid_until),
            "failure_reasons": failures,
            "requires_fresh_database_recheck": True,
        }
        verification = GateVerification(
            id=verification_id,
            tenant_id=principal.tenant_id,
            evaluation_id=evaluation.id,
            requested_by_user_id=principal.user_id,
            checkpoint=checkpoint,
            accepted=accepted,
            observed_input_hash=observed_hash,
            failure_reasons=failures,
            evidence_payload=payload,
            evidence_signature=_sign_document(payload, settings.secret_key.get_secret_value()),
            verified_at=now,
            valid_until=valid_until,
        )
        session.add(verification)
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="quality_gate.verified" if accepted else "quality_gate.denied",
            resource_type="quality_evaluation",
            resource_id=str(evaluation.id),
            outcome=AuditOutcome.SUCCESS if accepted else AuditOutcome.DENIED,
            event_data={
                "verification_id": str(verification.id),
                "checkpoint": checkpoint.value,
                "accepted": accepted,
                "failure_reasons": failures,
                "observed_input_hash": observed_hash,
            },
        )
        await session.commit()
        return verification

    @staticmethod
    async def list_evaluations(
        session: AsyncSession,
        *,
        principal: Principal,
        outcome: GateOutcome | None = None,
        render_job_id: UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[QualityEvaluation], int]:
        principal.require(Role.REVIEWER)
        conditions = [QualityEvaluation.tenant_id == principal.tenant_id]
        if outcome is not None:
            conditions.append(QualityEvaluation.outcome == outcome)
        if render_job_id is not None:
            conditions.append(QualityEvaluation.render_job_id == render_job_id)
        total = await session.scalar(
            select(func.count()).select_from(QualityEvaluation).where(*conditions)
        )
        rows = await session.scalars(
            select(QualityEvaluation)
            .where(*conditions)
            .order_by(QualityEvaluation.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows.all()), int(total or 0)

    @classmethod
    async def inspect_evaluation(
        cls,
        session: AsyncSession,
        *,
        principal: Principal,
        evaluation_id: UUID,
        settings: Settings,
        storage: ObjectStorage,
    ) -> EvaluationInspection:
        principal.require(Role.REVIEWER)
        evaluation = await session.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.id == evaluation_id,
                QualityEvaluation.tenant_id == principal.tenant_id,
            )
        )
        if evaluation is None:
            raise NotFoundError("quality evaluation")
        checks = list(
            (
                await session.scalars(
                    select(QualityCheckResult)
                    .where(
                        QualityCheckResult.evaluation_id == evaluation.id,
                        QualityCheckResult.tenant_id == principal.tenant_id,
                    )
                    .order_by(QualityCheckResult.kind)
                )
            ).all()
        )
        decisions = list(
            (
                await session.scalars(
                    select(QualityReviewDecision)
                    .where(
                        QualityReviewDecision.evaluation_id == evaluation.id,
                        QualityReviewDecision.tenant_id == principal.tenant_id,
                    )
                    .order_by(QualityReviewDecision.created_at)
                )
            ).all()
        )
        verifications = list(
            (
                await session.scalars(
                    select(GateVerification)
                    .where(
                        GateVerification.evaluation_id == evaluation.id,
                        GateVerification.tenant_id == principal.tenant_id,
                    )
                    .order_by(GateVerification.verified_at.desc())
                    .limit(100)
                )
            ).all()
        )
        context = await cls._load_context(
            session,
            tenant_id=principal.tenant_id,
            render_job_id=evaluation.render_job_id,
            rights_evidence_id=evaluation.rights_evidence_id,
            storage=storage,
            output_id=evaluation.render_output_id,
            require_current_output=False,
        )
        current_hash = cls._input_hash(
            context,
            destination=evaluation.destination,
            territory=evaluation.territory,
            campaign_reference=evaluation.campaign_reference,
        )
        rights = cls._assess_rights(
            context.rights,
            context=context,
            destination=evaluation.destination,
            territory=evaluation.territory,
            campaign_reference=evaluation.campaign_reference,
        )
        stale_reasons: list[str] = []
        if current_hash != evaluation.input_hash:
            stale_reasons.append("protected quality, render, or rights input changed")
        if not context.output.is_current:
            stale_reasons.append("evaluated render output is no longer current")
        if evaluation.outcome == GateOutcome.APPROVED:
            now = datetime.now(UTC)
            if (
                evaluation.approval_expires_at is None
                or _aware(evaluation.approval_expires_at) <= now
            ):
                stale_reasons.append("approval expired")
            if evaluation.approval_evidence is None or evaluation.approval_signature is None:
                stale_reasons.append("signed approval evidence is missing")
            elif not hmac.compare_digest(
                evaluation.approval_signature,
                _sign_document(
                    evaluation.approval_evidence,
                    settings.secret_key.get_secret_value(),
                ),
            ):
                stale_reasons.append("approval signature is invalid")
            if not rights.valid:
                stale_reasons.append(f"rights state changed to {rights.state}")
        return EvaluationInspection(
            evaluation=evaluation,
            checks=checks,
            rights=context.rights,
            rights_state=rights.state,
            decisions=decisions,
            verifications=verifications,
            stale=bool(stale_reasons),
            stale_reasons=_deduplicate(stale_reasons),
        )

    @classmethod
    async def _load_context(
        cls,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        render_job_id: UUID,
        rights_evidence_id: UUID | None,
        storage: ObjectStorage,
        require_current_output: bool,
        output_id: UUID | None = None,
    ) -> LoadedContext:
        row = (
            await session.execute(
                select(RenderJob, RenderPlan, ClipCandidate, Asset, Transcript)
                .join(RenderPlan, RenderPlan.id == RenderJob.render_plan_id)
                .join(ClipCandidate, ClipCandidate.id == RenderPlan.candidate_id)
                .join(Asset, Asset.id == RenderPlan.asset_id)
                .join(Transcript, Transcript.id == RenderPlan.transcript_id)
                .where(
                    RenderJob.id == render_job_id,
                    RenderJob.tenant_id == tenant_id,
                    RenderPlan.tenant_id == tenant_id,
                    ClipCandidate.tenant_id == tenant_id,
                    Asset.tenant_id == tenant_id,
                    Transcript.tenant_id == tenant_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("render")
        render, plan, candidate, asset, transcript = row
        output_conditions = [
            RenderOutput.render_job_id == render.id,
            RenderOutput.tenant_id == tenant_id,
        ]
        if output_id is not None:
            output_conditions.append(RenderOutput.id == output_id)
        elif require_current_output:
            output_conditions.append(RenderOutput.is_current.is_(True))
        output = await session.scalar(
            select(RenderOutput).where(*output_conditions).order_by(RenderOutput.created_at.desc())
        )
        if output is None:
            raise ConflictError("render has no persisted output available for quality evaluation")
        if require_current_output and not output.is_current:
            raise ConflictError("quality evaluation requires the current render output")
        metadata = await session.scalar(
            select(MediaMetadata).where(
                MediaMetadata.asset_id == asset.id,
                MediaMetadata.tenant_id == tenant_id,
            )
        )
        captions = list(
            (
                await session.scalars(
                    select(RenderCaptionSegment)
                    .where(
                        RenderCaptionSegment.render_plan_id == plan.id,
                        RenderCaptionSegment.tenant_id == tenant_id,
                    )
                    .order_by(RenderCaptionSegment.ordinal)
                )
            ).all()
        )
        transcript_segments = list(
            (
                await session.scalars(
                    select(TranscriptSegment)
                    .where(
                        TranscriptSegment.transcript_id == transcript.id,
                        TranscriptSegment.tenant_id == tenant_id,
                        TranscriptSegment.ordinal >= candidate.start_segment_ordinal,
                        TranscriptSegment.ordinal <= candidate.end_segment_ordinal,
                    )
                    .order_by(TranscriptSegment.ordinal)
                )
            ).all()
        )
        defects = list(
            (
                await session.scalars(
                    select(RenderDefect)
                    .where(
                        RenderDefect.render_job_id == render.id,
                        RenderDefect.tenant_id == tenant_id,
                    )
                    .order_by(RenderDefect.created_at, RenderDefect.id)
                )
            ).all()
        )
        rights: RightsEvidence | None = None
        if rights_evidence_id is not None:
            rights = await session.scalar(
                select(RightsEvidence).where(
                    RightsEvidence.id == rights_evidence_id,
                    RightsEvidence.tenant_id == tenant_id,
                )
            )
            if rights is None:
                raise NotFoundError("rights evidence")
        try:
            output_object = await storage.head(tenant_id, output.storage_key)
            sidecar_object = (
                await storage.head(tenant_id, output.sidecar_storage_key)
                if output.sidecar_storage_key
                else None
            )
        except OSError:
            output_object = None
            sidecar_object = None
        return LoadedContext(
            render=render,
            plan=plan,
            output=output,
            candidate=candidate,
            asset=asset,
            transcript=transcript,
            metadata=metadata,
            captions=captions,
            transcript_segments=transcript_segments,
            defects=defects,
            rights=rights,
            output_object=output_object,
            sidecar_object=sidecar_object,
        )

    @classmethod
    async def _build_checks(
        cls,
        session: AsyncSession,
        *,
        context: LoadedContext,
        destination: DestinationPlatform,
        campaign_reference: str | None,
        settings: Settings,
    ) -> list[CheckDraft]:
        return [
            cls._technical_check(context),
            cls._transcript_audio_check(context, settings),
            cls._caption_check(context),
            cls._context_check(context),
            cls._visual_check(context),
            cls._audio_check(context),
            await cls._duplication_check(
                session, context=context, campaign_reference=campaign_reference
            ),
            cls._destination_check(context, destination),
        ]

    @staticmethod
    def _technical_check(context: LoadedContext) -> CheckDraft:
        failures: list[str] = []
        if context.render.state != RenderState.SUCCEEDED:
            failures.append(f"render state is {context.render.state.value}, not succeeded")
        if not context.output.is_current:
            failures.append("output is not the current immutable render attempt")
        if context.output_object is None:
            failures.append("private output object is missing or unreadable")
        else:
            if context.output_object.checksum_sha256 != context.output.checksum_sha256:
                failures.append("stored output checksum differs from immutable provenance")
            if context.output_object.size != context.output.size_bytes:
                failures.append("stored output size differs from immutable provenance")
        if context.output.validation.get("passed") is not True:
            failures.append("persisted independent output validation did not pass")
        if context.output.video_codec != context.plan.video_codec:
            failures.append("video codec differs from the immutable render plan")
        if context.output.audio_codec != context.plan.audio_codec:
            failures.append("audio codec differs from the immutable render plan")
        if (context.output.width, context.output.height) != (
            context.plan.width,
            context.plan.height,
        ):
            failures.append("output dimensions differ from the immutable render plan")
        duration_delta = abs(context.output.duration_seconds - context.plan.duration_ms / 1000)
        if duration_delta > 0.5:
            failures.append("output duration differs from the approved source window")
        status = QualityCheckStatus.FAILED if failures else QualityCheckStatus.PASSED
        return CheckDraft(
            kind=QualityCheckKind.TECHNICAL_INTEGRITY,
            status=status,
            severity=QualitySeverity.CRITICAL if failures else QualitySeverity.INFO,
            evidence={
                "validator": "deterministic",
                "checks": {
                    "render_state": context.render.state.value,
                    "output_is_current": context.output.is_current,
                    "database_checksum": context.output.checksum_sha256,
                    "stored_checksum": (
                        context.output_object.checksum_sha256
                        if context.output_object is not None
                        else None
                    ),
                    "database_size_bytes": context.output.size_bytes,
                    "stored_size_bytes": (
                        context.output_object.size if context.output_object is not None else None
                    ),
                    "validation_passed": context.output.validation.get("passed"),
                    "duration_delta_seconds": duration_delta,
                    "dimensions": [context.output.width, context.output.height],
                    "codecs": [context.output.video_codec, context.output.audio_codec],
                },
                "failures": failures,
            },
            confidence=1.0,
            remediation=(
                "Re-render from the approved source and preserve a matching private object, "
                "checksum, probe, and validation record."
                if failures
                else "No technical remediation is required."
            ),
            hard_block=bool(failures),
        )

    @staticmethod
    def _transcript_audio_check(context: LoadedContext, settings: Settings) -> CheckDraft:
        failures: list[str] = []
        review_reasons: list[str] = []
        segment_text = " ".join(item.text for item in context.transcript_segments)
        excerpt_similarity = _similarity(context.candidate.transcript_excerpt, segment_text)
        if context.transcript.state != TranscriptState.READY:
            failures.append(f"transcript state is {context.transcript.state.value}")
        if not context.transcript_segments:
            failures.append("approved source segment records are missing")
        elif excerpt_similarity < 0.85:
            failures.append("candidate excerpt does not match its source transcript segments")
        confidence_values = [
            value
            for value in (
                context.transcript.confidence,
                context.candidate.transcript_confidence,
                *[item.confidence for item in context.transcript_segments],
            )
            if value is not None
        ]
        confidence = min(confidence_values) if confidence_values else 0.0
        if not confidence_values:
            review_reasons.append("transcript confidence evidence is unavailable")
        elif confidence < settings.quality_min_confidence:
            review_reasons.append(
                f"minimum transcript confidence {confidence:.3f} is below the quality threshold"
            )
        review_reasons.append(
            "no independent acoustic speech-to-text comparison was run in Phase 5"
        )
        if failures:
            status = QualityCheckStatus.FAILED
            severity = QualitySeverity.CRITICAL
        else:
            status = QualityCheckStatus.NEEDS_REVIEW
            severity = QualitySeverity.WARNING
        return CheckDraft(
            kind=QualityCheckKind.TRANSCRIPT_AUDIO_CONSISTENCY,
            status=status,
            severity=severity,
            evidence={
                "validator": "deterministic_lineage_with_human_acoustic_review",
                "transcript_state": context.transcript.state.value,
                "segment_count": len(context.transcript_segments),
                "excerpt_segment_similarity": round(excerpt_similarity, 6),
                "minimum_reported_confidence": confidence if confidence_values else None,
                "quality_confidence_threshold": settings.quality_min_confidence,
                "failures": failures,
                "review_reasons": review_reasons,
                "model_judgment_used": False,
            },
            confidence=max(0.0, min(1.0, confidence)),
            remediation=(
                "Restore transcript lineage from the source audio and obtain human confirmation "
                "of words, speakers, names, and claims."
                if failures
                else "A human must compare clip audio with the displayed transcript and resolve "
                "low-confidence words before approval."
            ),
            hard_block=bool(failures),
        )

    @staticmethod
    def _caption_check(context: LoadedContext) -> CheckDraft:
        failures: list[str] = []
        review_reasons: list[str] = []
        expects_captions = context.plan.caption_mode != "none"
        if expects_captions and not context.captions:
            failures.append("the render plan requires captions but no caption cues are persisted")
        previous_end = -1
        for cue in context.captions:
            if cue.output_start_ms < previous_end:
                failures.append(f"caption cue {cue.ordinal} overlaps the preceding cue")
            if cue.output_start_ms < 0 or cue.output_end_ms > context.plan.duration_ms:
                failures.append(f"caption cue {cue.ordinal} is outside the output timeline")
            if cue.line_count > 2:
                failures.append(f"caption cue {cue.ordinal} exceeds two lines")
            if not cue.text.strip():
                failures.append(f"caption cue {cue.ordinal} is blank")
            previous_end = cue.output_end_ms
        caption_text = " ".join(item.text for item in context.captions)
        caption_similarity = (
            _similarity(caption_text, context.candidate.transcript_excerpt)
            if context.captions
            else 0.0
        )
        if context.captions and caption_similarity < 0.75:
            failures.append(
                "caption text does not sufficiently match the approved transcript excerpt"
            )
        expects_sidecar = context.plan.caption_mode in {"sidecar", "both"}
        if expects_sidecar:
            if not context.output.sidecar_storage_key or context.sidecar_object is None:
                failures.append("required caption sidecar is missing")
            elif context.sidecar_object.checksum_sha256 != context.output.sidecar_checksum_sha256:
                failures.append("caption sidecar checksum differs from immutable provenance")
        if not expects_captions:
            review_reasons.append("captions are disabled and accessibility must be reviewed")
        if failures:
            status = QualityCheckStatus.FAILED
            severity = QualitySeverity.ERROR
        elif review_reasons:
            status = QualityCheckStatus.NEEDS_REVIEW
            severity = QualitySeverity.WARNING
        else:
            status = QualityCheckStatus.PASSED
            severity = QualitySeverity.INFO
        return CheckDraft(
            kind=QualityCheckKind.CAPTION_QUALITY,
            status=status,
            severity=severity,
            evidence={
                "validator": "deterministic",
                "caption_mode": context.plan.caption_mode,
                "cue_count": len(context.captions),
                "caption_excerpt_similarity": round(caption_similarity, 6),
                "sidecar_expected": expects_sidecar,
                "sidecar_present": context.sidecar_object is not None,
                "failures": _deduplicate(failures),
                "review_reasons": review_reasons,
            },
            confidence=1.0 if not review_reasons else 0.65,
            remediation=(
                "Regenerate captions from the approved transcript with non-overlapping timing, "
                "readable line bounds, and a matching sidecar."
                if failures
                else (
                    "Confirm that a caption-free release is intentional and accessible."
                    if review_reasons
                    else "No caption remediation is required."
                )
            ),
            hard_block=bool(
                context.sidecar_object is not None
                and context.output.sidecar_checksum_sha256
                and context.sidecar_object.checksum_sha256 != context.output.sidecar_checksum_sha256
            ),
        )

    @staticmethod
    def _context_check(context: LoadedContext) -> CheckDraft:
        failures: list[str] = []
        if context.candidate.state != CandidateState.APPROVED:
            failures.append("source candidate is no longer human-approved")
        if (
            context.plan.source_start_ms != context.candidate.start_ms
            or context.plan.source_end_ms != context.candidate.end_ms
            or context.plan.source_boundary_revision != context.candidate.boundary_revision
        ):
            failures.append("render boundaries no longer match the approved candidate revision")
        meaning_defects = [item for item in context.defects if item.meaning_or_boundary_changed]
        if meaning_defects:
            failures.append("a reviewer reported a meaning or source-boundary defect")
        if failures:
            return CheckDraft(
                kind=QualityCheckKind.CONTEXT_INTEGRITY,
                status=QualityCheckStatus.FAILED,
                severity=QualitySeverity.CRITICAL,
                evidence={
                    "validator": "deterministic_provenance",
                    "candidate_state": context.candidate.state.value,
                    "candidate_bounds": [
                        context.candidate.start_ms,
                        context.candidate.end_ms,
                        context.candidate.boundary_revision,
                    ],
                    "render_bounds": [
                        context.plan.source_start_ms,
                        context.plan.source_end_ms,
                        context.plan.source_boundary_revision,
                    ],
                    "meaning_defect_ids": [str(item.id) for item in meaning_defects],
                    "failures": failures,
                },
                confidence=1.0,
                remediation=(
                    "Return to source-candidate review, resolve meaning defects, and create a new "
                    "immutable render and quality evaluation."
                ),
                hard_block=True,
            )
        return CheckDraft(
            kind=QualityCheckKind.CONTEXT_INTEGRITY,
            status=QualityCheckStatus.NEEDS_REVIEW,
            severity=QualitySeverity.WARNING,
            evidence={
                "validator": "deterministic_provenance_plus_human_semantic_review",
                "candidate_state": context.candidate.state.value,
                "bounds_match": True,
                "risk_flags": context.candidate.risk_flags,
                "reported_defect_count": len(context.defects),
                "semantic_claim_verification_available": False,
                "model_judgment_used": False,
            },
            confidence=0.6,
            remediation=(
                "A human must watch surrounding source context and verify that the clip preserves "
                "speaker intent, qualifications, uncertainty, and attribution."
            ),
        )

    @staticmethod
    def _visual_check(context: LoadedContext) -> CheckDraft:
        failures: list[str] = []
        if (
            context.output.width != context.plan.width
            or context.output.height != context.plan.height
        ):
            failures.append("output dimensions do not match the composition plan")
        safe_area = context.plan.safe_area
        required_safe_area = {"top", "right", "bottom", "left"}
        if not required_safe_area.issubset(safe_area):
            failures.append("composition plan has incomplete safe-area evidence")
        if failures:
            status = QualityCheckStatus.FAILED
            severity = QualitySeverity.ERROR
            confidence = 1.0
        else:
            status = QualityCheckStatus.NEEDS_REVIEW
            severity = QualitySeverity.WARNING
            confidence = 0.55
        return CheckDraft(
            kind=QualityCheckKind.VISUAL_COMPOSITION,
            status=status,
            severity=severity,
            evidence={
                "validator": "deterministic_geometry_plus_human_visual_review",
                "dimensions": [context.output.width, context.output.height],
                "aspect_ratio": context.plan.aspect_ratio,
                "crop_requested": context.plan.crop_strategy_requested,
                "crop_applied": context.plan.crop_strategy_applied,
                "crop_fallback_reason": context.plan.crop_fallback_reason,
                "safe_area": safe_area,
                "content_aware_visual_analyzer_available": False,
                "failures": failures,
                "model_judgment_used": False,
            },
            confidence=confidence,
            remediation=(
                "Re-render with a valid safe area and composition geometry."
                if failures
                else "A human must inspect subject framing, occlusion, overlays, cropping, and "
                "platform UI-safe areas."
            ),
        )

    @staticmethod
    def _audio_check(context: LoadedContext) -> CheckDraft:
        failures: list[str] = []
        expected_channels = int(context.plan.audio_settings.get("channels", 2))
        expected_rate = int(context.plan.audio_settings.get("sample_rate", 48000))
        if context.output.audio_codec != "aac":
            failures.append("output audio codec is not AAC")
        if context.output.audio_channels != expected_channels:
            failures.append("output channel count differs from the immutable audio plan")
        if context.output.audio_sample_rate != expected_rate:
            failures.append("output sample rate differs from the immutable audio plan")
        if failures:
            status = QualityCheckStatus.FAILED
            severity = QualitySeverity.ERROR
            confidence = 1.0
        else:
            status = QualityCheckStatus.NEEDS_REVIEW
            severity = QualitySeverity.WARNING
            confidence = 0.6
        return CheckDraft(
            kind=QualityCheckKind.AUDIO_QUALITY,
            status=status,
            severity=severity,
            evidence={
                "validator": "deterministic_stream_layout_plus_human_listening_review",
                "codec": context.output.audio_codec,
                "channels": context.output.audio_channels,
                "sample_rate": context.output.audio_sample_rate,
                "normalization_plan": context.plan.audio_settings,
                "measured_loudness_scan_available": False,
                "failures": failures,
                "model_judgment_used": False,
            },
            confidence=confidence,
            remediation=(
                "Re-render with the approved audio codec, channel layout, and sample rate."
                if failures
                else "A human must listen for clipping, silence, sync, intelligibility, cuts, "
                "and background-material concerns."
            ),
        )

    @staticmethod
    async def _duplication_check(
        session: AsyncSession,
        *,
        context: LoadedContext,
        campaign_reference: str | None,
    ) -> CheckDraft:
        duplicate_ids = list(
            (
                await session.scalars(
                    select(QualityEvaluation.id)
                    .join(RenderOutput, RenderOutput.id == QualityEvaluation.render_output_id)
                    .where(
                        QualityEvaluation.tenant_id == context.render.tenant_id,
                        QualityEvaluation.render_output_id != context.output.id,
                        RenderOutput.checksum_sha256 == context.output.checksum_sha256,
                    )
                    .limit(20)
                )
            ).all()
        )
        review_reasons: list[str] = []
        if duplicate_ids:
            review_reasons.append("an identical output checksum exists in another evaluation")
        if not campaign_reference:
            review_reasons.append("no campaign reference was supplied for consistency comparison")
        status = QualityCheckStatus.NEEDS_REVIEW if review_reasons else QualityCheckStatus.PASSED
        return CheckDraft(
            kind=QualityCheckKind.DUPLICATION_CAMPAIGN_CONSISTENCY,
            status=status,
            severity=(QualitySeverity.WARNING if review_reasons else QualitySeverity.INFO),
            evidence={
                "validator": "deterministic_exact_checksum",
                "campaign_reference": campaign_reference,
                "duplicate_evaluation_ids": [str(item) for item in duplicate_ids],
                "semantic_near_duplicate_model_used": False,
                "review_reasons": review_reasons,
            },
            confidence=1.0 if not review_reasons else 0.65,
            remediation=(
                "Review the identified duplicate and campaign history before release."
                if duplicate_ids
                else (
                    "Assign a campaign reference and manually compare campaign claims, cadence, "
                    "and repeated footage."
                    if not campaign_reference
                    else "No exact duplicate remediation is required."
                )
            ),
        )

    @staticmethod
    def _destination_check(context: LoadedContext, destination: DestinationPlatform) -> CheckDraft:
        profile = _DESTINATION_PROFILES.get(destination)
        if profile is None:
            return CheckDraft(
                kind=QualityCheckKind.DESTINATION_CONSTRAINTS,
                status=QualityCheckStatus.UNAVAILABLE,
                severity=QualitySeverity.WARNING,
                evidence={
                    "validator": "deterministic",
                    "destination": destination.value,
                    "profile_available": False,
                    "platform_acceptance_guaranteed": False,
                },
                confidence=0.0,
                remediation=(
                    "A reviewer must obtain current official destination requirements and record a "
                    "supported platform-specific profile before external use."
                ),
            )
        failures: list[str] = []
        if context.output.duration_seconds > float(profile["max_duration_seconds"]):
            failures.append("clip duration exceeds the internal destination profile")
        observed_ratio = context.output.width / context.output.height
        canonical_ratios = {"9:16": 9 / 16, "1:1": 1.0, "16:9": 16 / 9}
        aspect_matches = any(
            abs(observed_ratio - canonical_ratios[item]) <= 0.02
            for item in profile["aspect_ratios"]
        )
        if not aspect_matches:
            failures.append("aspect ratio is outside the internal destination profile")
        if context.output.video_codec != profile["video_codec"]:
            failures.append("video codec is outside the internal destination profile")
        if context.output.audio_codec != profile["audio_codec"]:
            failures.append("audio codec is outside the internal destination profile")
        return CheckDraft(
            kind=QualityCheckKind.DESTINATION_CONSTRAINTS,
            status=(QualityCheckStatus.FAILED if failures else QualityCheckStatus.PASSED),
            severity=QualitySeverity.ERROR if failures else QualitySeverity.INFO,
            evidence={
                "validator": "deterministic",
                "destination": destination.value,
                "profile_version": profile["profile_version"],
                "max_duration_seconds": profile["max_duration_seconds"],
                "allowed_aspect_ratios": sorted(profile["aspect_ratios"]),
                "observed_duration_seconds": context.output.duration_seconds,
                "observed_aspect_ratio": context.plan.aspect_ratio,
                "observed_codecs": [
                    context.output.video_codec,
                    context.output.audio_codec,
                ],
                "failures": failures,
                "platform_acceptance_guaranteed": False,
            },
            confidence=0.9,
            remediation=(
                "Create a new destination-specific render that satisfies the versioned internal "
                "profile, then confirm current official platform requirements."
                if failures
                else "Confirm official platform rules at release time; this internal profile does "
                "not guarantee destination acceptance."
            ),
        )

    @staticmethod
    def _assess_rights(
        evidence: RightsEvidence | None,
        *,
        context: LoadedContext,
        destination: DestinationPlatform,
        territory: str,
        campaign_reference: str | None,
        now: datetime | None = None,
    ) -> RightsAssessment:
        checked_at = now or datetime.now(UTC)
        hard_blocks: list[str] = []
        missing: list[str] = []
        remediation: list[str] = []
        explanations: list[str] = []
        if evidence is None:
            return RightsAssessment(
                state="unknown",
                valid=False,
                hard_blocks=["no structured permission evidence is linked to this evaluation"],
                missing_evidence=[
                    "source owner",
                    "permission basis and reference",
                    "platform and territory scope",
                    "expiration",
                    "third-party-material clearances",
                ],
                remediation=[
                    "An administrator must record scoped, traceable permission evidence; public "
                    "availability and a free-text upload note are not permission."
                ],
                explanations=["Unknown rights block downstream use."],
            )
        if evidence.asset_id != context.asset.id:
            hard_blocks.append("rights evidence belongs to a different source asset")
        if (
            evidence.scope == RightsScope.CANDIDATE
            and evidence.candidate_id != context.candidate.id
        ):
            hard_blocks.append("candidate-scoped permission does not cover this clip")
        if evidence.scope == RightsScope.RENDER and evidence.render_job_id != context.render.id:
            hard_blocks.append("render-scoped permission does not cover this render")
        if evidence.scope == RightsScope.ASSET and (
            evidence.candidate_id is not None or evidence.render_job_id is not None
        ):
            hard_blocks.append("asset-scoped permission has contradictory clip identifiers")
        if evidence.status == RightsEvidenceStatus.REJECTED:
            hard_blocks.append("rights evidence was rejected")
        elif evidence.status == RightsEvidenceStatus.NEEDS_REVIEW:
            hard_blocks.append("rights evidence still requires specialist human review")
        if evidence.revoked_at is not None:
            hard_blocks.append("permission was revoked")
        if _aware(evidence.permission_recorded_at) > checked_at + timedelta(minutes=5):
            hard_blocks.append("permission record timestamp is in the future")
        if _aware(evidence.effective_from) > checked_at:
            hard_blocks.append("permission is not yet effective")
        state = "valid"
        if evidence.revoked_at is not None:
            state = "revoked"
        elif evidence.expires_at is not None and _aware(evidence.expires_at) <= checked_at:
            state = "expired"
            hard_blocks.append("permission expired")
        elif evidence.status == RightsEvidenceStatus.NEEDS_REVIEW:
            state = "needs_review"
        elif evidence.status == RightsEvidenceStatus.REJECTED:
            state = "rejected"
        elif _aware(evidence.effective_from) > checked_at:
            state = "not_yet_effective"
        if evidence.expires_at is None and not evidence.no_expiration_asserted:
            missing.append("expiration or explicit no-expiration assertion")
            hard_blocks.append("permission duration is not explicit")
        if destination.value not in evidence.allowed_platforms:
            hard_blocks.append(
                f"permission does not include destination platform {destination.value}"
            )
        if (
            "WORLDWIDE" not in evidence.allowed_territories
            and territory not in evidence.allowed_territories
        ):
            hard_blocks.append(f"permission does not include territory {territory}")
        if evidence.campaign_reference and evidence.campaign_reference != campaign_reference:
            hard_blocks.append("campaign-scoped permission does not match this campaign")
        if not evidence.commercial_use_allowed:
            hard_blocks.append("commercial use is not authorized")
        if not evidence.editing_allowed:
            hard_blocks.append("editing or derivative clipping is not authorized")
        if not evidence.ai_processing_allowed:
            hard_blocks.append("AI-assisted processing is not authorized")
        if evidence.attribution_required and not (evidence.attribution_text or "").strip():
            missing.append("required attribution text")
            hard_blocks.append("required attribution is missing")
        material = evidence.third_party_material
        missing_material = sorted(_REQUIRED_MATERIAL_KEYS - material.keys())
        if missing_material:
            missing.extend(f"{item} clearance" for item in missing_material)
            hard_blocks.append("third-party-material declarations are incomplete")
        for key in sorted(_REQUIRED_MATERIAL_KEYS):
            value = material.get(key, MaterialClearance.UNKNOWN.value)
            if value == MaterialClearance.UNKNOWN.value:
                hard_blocks.append(f"{key} rights or consent are unknown")
            elif value == MaterialClearance.RESTRICTED.value:
                hard_blocks.append(f"{key} is marked restricted")
            elif value not in {
                MaterialClearance.NOT_PRESENT.value,
                MaterialClearance.CLEARED.value,
            }:
                hard_blocks.append(f"{key} has an unsupported clearance state")
        if not evidence.source_owner.strip():
            missing.append("source owner")
            hard_blocks.append("source owner is missing")
        if not evidence.evidence_reference.strip():
            missing.append("permission evidence reference")
            hard_blocks.append("permission evidence reference is missing")
        if hard_blocks:
            remediation.append(
                "Do not publish or upload. Obtain corrected, platform- and territory-scoped rights "
                "evidence and create a new quality evaluation."
            )
        elif evidence.attribution_required:
            explanations.append(
                "Any future destination payload must include the exact recorded attribution text."
            )
        explanations.append(
            "Public availability was not treated as permission, and this grant applies only to its "
            "explicit asset/candidate/render, platform, territory, duration, and campaign scope."
        )
        return RightsAssessment(
            state=state if not hard_blocks or state != "valid" else "invalid_scope",
            valid=not hard_blocks,
            hard_blocks=_deduplicate(hard_blocks),
            missing_evidence=_deduplicate(missing),
            remediation=_deduplicate(remediation),
            explanations=explanations,
        )

    @staticmethod
    def _input_hash(
        context: LoadedContext,
        *,
        destination: DestinationPlatform,
        territory: str,
        campaign_reference: str | None,
    ) -> str:
        rights = context.rights
        document: dict[str, Any] = {
            "schema": "quality-protected-input.v1",
            "policy_version": POLICY_VERSION,
            "destination": destination.value,
            "territory": territory,
            "campaign_reference": campaign_reference,
            "render": {
                "id": str(context.render.id),
                "state": context.render.state.value,
                "plan_id": str(context.plan.id),
                "plan_request_hash": context.plan.request_hash,
                "source_bounds": [
                    context.plan.source_start_ms,
                    context.plan.source_end_ms,
                    context.plan.source_boundary_revision,
                ],
            },
            "output": {
                "id": str(context.output.id),
                "is_current": context.output.is_current,
                "checksum": context.output.checksum_sha256,
                "size": context.output.size_bytes,
                "stored_checksum": (
                    context.output_object.checksum_sha256
                    if context.output_object is not None
                    else None
                ),
                "stored_size": (
                    context.output_object.size if context.output_object is not None else None
                ),
                "sidecar_checksum": context.output.sidecar_checksum_sha256,
                "stored_sidecar_checksum": (
                    context.sidecar_object.checksum_sha256
                    if context.sidecar_object is not None
                    else None
                ),
                "validation": context.output.validation,
                "probe_version": context.output.probe_version,
                "renderer_version": context.output.renderer_version,
            },
            "candidate": {
                "id": str(context.candidate.id),
                "state": context.candidate.state.value,
                "boundary_revision": context.candidate.boundary_revision,
                "excerpt_hash": hashlib.sha256(
                    context.candidate.transcript_excerpt.encode("utf-8")
                ).hexdigest(),
                "risk_flags": context.candidate.risk_flags,
            },
            "transcript": {
                "id": str(context.transcript.id),
                "state": context.transcript.state.value,
                "confidence": context.transcript.confidence,
                "algorithm_version": context.transcript.algorithm_version,
                "normalized_text_hash": hashlib.sha256(
                    context.transcript.normalized_text.encode("utf-8")
                ).hexdigest(),
                "segments": [
                    {
                        "id": str(item.id),
                        "ordinal": item.ordinal,
                        "start_ms": item.start_ms,
                        "end_ms": item.end_ms,
                        "text_hash": hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                        "confidence": item.confidence,
                    }
                    for item in context.transcript_segments
                ],
            },
            "captions": [
                {
                    "id": str(item.id),
                    "ordinal": item.ordinal,
                    "source_start_ms": item.source_start_ms,
                    "source_end_ms": item.source_end_ms,
                    "output_start_ms": item.output_start_ms,
                    "output_end_ms": item.output_end_ms,
                    "text_hash": hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                }
                for item in context.captions
            ],
            "defects": [
                {
                    "id": str(item.id),
                    "category": item.category,
                    "meaning_or_boundary_changed": item.meaning_or_boundary_changed,
                    "description_hash": hashlib.sha256(
                        item.description.encode("utf-8")
                    ).hexdigest(),
                }
                for item in context.defects
            ],
            "rights": (
                {
                    "id": str(rights.id),
                    "scope": rights.scope.value,
                    "asset_id": str(rights.asset_id),
                    "candidate_id": str(rights.candidate_id) if rights.candidate_id else None,
                    "render_job_id": str(rights.render_job_id) if rights.render_job_id else None,
                    "basis": rights.basis.value,
                    "status": rights.status.value,
                    "source_owner_hash": hashlib.sha256(
                        rights.source_owner.encode("utf-8")
                    ).hexdigest(),
                    "evidence_reference_hash": hashlib.sha256(
                        rights.evidence_reference.encode("utf-8")
                    ).hexdigest(),
                    "evidence_checksum": rights.evidence_checksum_sha256,
                    "permission_recorded_at": _datetime_text(rights.permission_recorded_at),
                    "effective_from": _datetime_text(rights.effective_from),
                    "expires_at": _datetime_text(rights.expires_at),
                    "no_expiration_asserted": rights.no_expiration_asserted,
                    "allowed_platforms": sorted(rights.allowed_platforms),
                    "allowed_territories": sorted(rights.allowed_territories),
                    "commercial_use_allowed": rights.commercial_use_allowed,
                    "editing_allowed": rights.editing_allowed,
                    "ai_processing_allowed": rights.ai_processing_allowed,
                    "attribution_required": rights.attribution_required,
                    "attribution_text_hash": (
                        hashlib.sha256(rights.attribution_text.encode("utf-8")).hexdigest()
                        if rights.attribution_text
                        else None
                    ),
                    "license_terms_hash": hashlib.sha256(
                        rights.license_terms.encode("utf-8")
                    ).hexdigest(),
                    "restrictions_hash": hashlib.sha256(
                        rights.restrictions.encode("utf-8")
                    ).hexdigest(),
                    "third_party_material": rights.third_party_material,
                    "campaign_reference": rights.campaign_reference,
                    "revoked_at": _datetime_text(rights.revoked_at),
                    "revoked_by_user_id": (
                        str(rights.revoked_by_user_id) if rights.revoked_by_user_id else None
                    ),
                    "revocation_reason_hash": (
                        hashlib.sha256(rights.revocation_reason.encode("utf-8")).hexdigest()
                        if rights.revocation_reason
                        else None
                    ),
                    "updated_at": _datetime_text(rights.updated_at),
                }
                if rights is not None
                else None
            ),
        }
        return _hash_document(document)

    @staticmethod
    def _draft_document(item: CheckDraft) -> dict[str, Any]:
        return {
            "kind": item.kind.value,
            "status": item.status.value,
            "severity": item.severity.value,
            "evidence_source": item.evidence_source.value,
            "evidence": item.evidence,
            "confidence": item.confidence,
            "version": item.version,
            "remediation": item.remediation,
            "hard_block": item.hard_block,
        }

    @staticmethod
    def _rights_assessment_document(item: RightsAssessment) -> dict[str, Any]:
        return {
            "state": item.state,
            "valid": item.valid,
            "hard_blocks": item.hard_blocks,
            "missing_evidence": item.missing_evidence,
            "remediation": item.remediation,
            "explanations": item.explanations,
        }
