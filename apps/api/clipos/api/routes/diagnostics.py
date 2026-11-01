from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_runtime_settings, get_session, require_role
from clipos.config import Settings
from clipos.core.enums import Role
from clipos.core.security import Principal
from clipos.schemas import (
    AuditEventResponse,
    ConfigurationItem,
    ProviderStatusResponse,
    SystemControlResponse,
    SystemControlUpdate,
)
from clipos.services.audit import AuditService
from clipos.services.providers import ProviderStatusService
from clipos.services.system_control import SystemControlService

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("/configuration", response_model=list[ConfigurationItem])
async def configuration_status(
    _: Principal = Depends(require_role(Role.ADMIN)),
    settings: Settings = Depends(get_runtime_settings),
) -> list[ConfigurationItem]:
    return [ConfigurationItem.model_validate(item) for item in settings.configuration_status()]


@router.get("/providers", response_model=list[ProviderStatusResponse])
async def provider_statuses(
    _: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderStatusResponse]:
    rows = await ProviderStatusService.list(session)
    return [ProviderStatusResponse.model_validate(row) for row in rows]


@router.get("/audit", response_model=list[AuditEventResponse])
async def audit_events(
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(require_role(Role.REVIEWER)),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEventResponse]:
    events = await AuditService.list_for_tenant(session, principal.tenant_id, limit=limit)
    return [AuditEventResponse.model_validate(event) for event in events]


@router.get("/control", response_model=SystemControlResponse)
async def get_control(
    _: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> SystemControlResponse:
    return SystemControlResponse.model_validate(await SystemControlService.get(session))


@router.post("/control/pause", response_model=SystemControlResponse)
async def pause(
    update: SystemControlUpdate,
    principal: Principal = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> SystemControlResponse:
    control = await SystemControlService.set_paused(
        session, principal=principal, paused=True, reason=update.reason
    )
    return SystemControlResponse.model_validate(control)


@router.post("/control/resume", response_model=SystemControlResponse)
async def resume(
    update: SystemControlUpdate,
    principal: Principal = Depends(require_role(Role.OWNER)),
    session: AsyncSession = Depends(get_session),
) -> SystemControlResponse:
    control = await SystemControlService.set_paused(
        session, principal=principal, paused=False, reason=update.reason
    )
    return SystemControlResponse.model_validate(control)
