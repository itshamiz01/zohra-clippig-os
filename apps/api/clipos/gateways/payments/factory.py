from __future__ import annotations

from clipos.config import Settings
from clipos.gateways.payments.contracts import PaymentsAdapter
from clipos.gateways.payments.disabled import DisabledPaymentsAdapter
from clipos.gateways.payments.fake import FakePaymentsAdapter


class PaymentsAdapterRegistry:
    """The single configured payments adapter, fail-closed.

    Settings validation has already rejected any real provider name (no
    payment provider decision exists for Phase 9), so the only selectable
    implementations are the disabled adapter and the explicitly mocked
    fake. Anything unexpected falls back to disabled.
    """

    def __init__(self, adapter: PaymentsAdapter) -> None:
        self._adapter = adapter

    def get(self) -> PaymentsAdapter:
        return self._adapter

    async def close(self) -> None:
        await self._adapter.close()


def build_payments_adapters(settings: Settings) -> PaymentsAdapterRegistry:
    if settings.payments_provider == "fake":
        return PaymentsAdapterRegistry(FakePaymentsAdapter())
    return PaymentsAdapterRegistry(DisabledPaymentsAdapter())
