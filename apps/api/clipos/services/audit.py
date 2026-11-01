from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.core.enums import AuditOutcome
from clipos.core.logging import correlation_id_var, sanitize
from clipos.models import AuditEvent


class AuditService:
    @staticmethod
    def record(
        session: AsyncSession,
        *,
        tenant_id: UUID | None,
        actor_user_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        event_data: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            correlation_id=correlation_id_var.get(),
            event_data=sanitize(event_data or {}),
        )
        session.add(event)
        return event

    @staticmethod
    async def list_for_tenant(
        session: AsyncSession, tenant_id: UUID, *, limit: int = 100
    ) -> list[AuditEvent]:
        rows = await session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(desc(AuditEvent.created_at))
            .limit(min(limit, 200))
        )
        return list(rows.all())
