from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.core.errors import AuthorizationError
from clipos.core.security import Principal
from clipos.models import SystemControl
from clipos.services.audit import AuditService


class SystemControlService:
    @staticmethod
    async def get(session: AsyncSession) -> SystemControl:
        control = await session.scalar(select(SystemControl).where(SystemControl.key == "global"))
        if control is None:
            control = SystemControl(key="global", paused=True, reason="safe default")
            session.add(control)
            await session.commit()
            await session.refresh(control)
        return control

    @staticmethod
    async def set_paused(
        session: AsyncSession, *, principal: Principal, paused: bool, reason: str
    ) -> SystemControl:
        if not principal.is_platform_admin:
            raise AuthorizationError("platform administrator authority is required")
        control = await SystemControlService.get(session)
        control.paused = paused
        control.reason = reason
        control.updated_by_user_id = principal.user_id
        AuditService.record(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="system.paused" if paused else "system.resumed",
            resource_type="system_control",
            resource_id="global",
            event_data={"reason": reason},
        )
        await session.commit()
        await session.refresh(control)
        return control
