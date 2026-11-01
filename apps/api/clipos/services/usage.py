from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from clipos.core.logging import correlation_id_var
from clipos.gateways.models.contracts import GenerationMetadata
from clipos.models import UsageCostEvent


class UsageService:
    @staticmethod
    async def record_generation(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        metadata: GenerationMetadata,
        success: bool = True,
        failure_category: str | None = None,
        commit: bool = True,
    ) -> UsageCostEvent:
        event = UsageCostEvent(
            tenant_id=tenant_id,
            provider=metadata.provider,
            model=metadata.model,
            operation=metadata.operation,
            input_units=metadata.input_tokens,
            output_units=metadata.output_tokens,
            estimated_cost=metadata.estimated_cost,
            latency_ms=metadata.latency_ms,
            success=success,
            failure_category=failure_category,
            correlation_id=correlation_id_var.get(),
        )
        session.add(event)
        if commit:
            await session.commit()
            await session.refresh(event)
        else:
            await session.flush()
        return event
