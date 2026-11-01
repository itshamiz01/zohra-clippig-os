from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_session, require_role
from clipos.core.enums import ApprovalStatus, Role
from clipos.core.security import Principal
from clipos.schemas import ApprovalCreate, ApprovalDecision, ApprovalResponse
from clipos.services.approvals import ApprovalService

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    state: ApprovalStatus | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[ApprovalResponse]:
    items = await ApprovalService.list(session, principal.tenant_id, status=state, limit=limit)
    return [ApprovalResponse.model_validate(item) for item in items]


@router.post("", response_model=ApprovalResponse, status_code=status.HTTP_201_CREATED)
async def request_approval(
    request: ApprovalCreate,
    principal: Principal = Depends(require_role(Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
) -> ApprovalResponse:
    approval = await ApprovalService.create(session, principal=principal, request=request)
    return ApprovalResponse.model_validate(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: UUID,
    body: ApprovalDecision,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> ApprovalResponse:
    item = await ApprovalService.decide(
        session,
        principal=principal,
        approval_id=approval_id,
        decision=ApprovalStatus.APPROVED,
        reason=body.reason,
    )
    return ApprovalResponse.model_validate(item)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: UUID,
    body: ApprovalDecision,
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> ApprovalResponse:
    item = await ApprovalService.decide(
        session,
        principal=principal,
        approval_id=approval_id,
        decision=ApprovalStatus.REJECTED,
        reason=body.reason,
    )
    return ApprovalResponse.model_validate(item)
