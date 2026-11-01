from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.core.enums import ApprovalStatus, Role
from clipos.core.errors import AuthorizationError, ConflictError, NotFoundError
from clipos.core.security import Principal
from clipos.models import ApprovalRequest
from clipos.schemas import ApprovalCreate
from clipos.services.audit import AuditService


class ApprovalService:
    @staticmethod
    def payload_hash(request: ApprovalCreate) -> str:
        approval_envelope = {
            "action_class": request.action_class.value,
            "action_type": request.action_type,
            "resource_type": request.resource_type,
            "resource_id": request.resource_id,
            "payload": request.action_payload,
        }
        canonical = json.dumps(
            approval_envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    async def create(
        session: AsyncSession, *, principal: Principal, request: ApprovalCreate
    ) -> ApprovalRequest:
        if request.expires_at and request.expires_at <= datetime.now(UTC):
            raise ConflictError("approval expiry must be in the future")
        approval = ApprovalRequest(
            tenant_id=principal.tenant_id,
            requested_by_user_id=principal.user_id,
            action_class=request.action_class,
            action_type=request.action_type,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            payload_hash=ApprovalService.payload_hash(request),
            rationale=request.rationale,
            expires_at=request.expires_at,
        )
        session.add(approval)
        await session.flush()
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="approval.requested",
            resource_type="approval_request",
            resource_id=str(approval.id),
            event_data={
                "action_class": approval.action_class.value,
                "resource_type": approval.resource_type,
                "resource_id": approval.resource_id,
                "payload_hash": approval.payload_hash,
            },
        )
        await session.commit()
        await session.refresh(approval)
        return approval

    @staticmethod
    async def get(session: AsyncSession, tenant_id: UUID, approval_id: UUID) -> ApprovalRequest:
        approval = await session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.tenant_id == tenant_id,
            )
        )
        if approval is None:
            raise NotFoundError("approval request")
        return approval

    @staticmethod
    async def list(
        session: AsyncSession,
        tenant_id: UUID,
        *,
        status: ApprovalStatus | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        query = select(ApprovalRequest).where(ApprovalRequest.tenant_id == tenant_id)
        if status:
            query = query.where(ApprovalRequest.status == status)
        query = query.order_by(desc(ApprovalRequest.created_at)).limit(min(limit, 200))
        return list((await session.scalars(query)).all())

    @staticmethod
    async def decide(
        session: AsyncSession,
        *,
        principal: Principal,
        approval_id: UUID,
        decision: ApprovalStatus,
        reason: str,
    ) -> ApprovalRequest:
        principal.require(Role.REVIEWER)
        approval = await session.scalar(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.tenant_id == principal.tenant_id,
            )
            .with_for_update()
        )
        if approval is None:
            raise NotFoundError("approval request")
        if approval.requested_by_user_id == principal.user_id:
            raise AuthorizationError("requesters cannot decide their own approval")
        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError("approval request is no longer pending")
        now = datetime.now(UTC)
        expires_at = approval.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                approval.status = ApprovalStatus.EXPIRED
                AuditService.record(
                    session,
                    tenant_id=principal.tenant_id,
                    actor_user_id=principal.user_id,
                    action="approval.expired",
                    resource_type="approval_request",
                    resource_id=str(approval.id),
                )
                await session.commit()
                raise ConflictError("approval request has expired")
        if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ConflictError("unsupported approval decision")
        approval.status = decision
        approval.decided_by_user_id = principal.user_id
        approval.decision_reason = reason
        approval.decided_at = now
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action=f"approval.{decision.value}",
            resource_type="approval_request",
            resource_id=str(approval.id),
            event_data={"action_class": approval.action_class.value},
        )
        await session.commit()
        await session.refresh(approval)
        return approval
