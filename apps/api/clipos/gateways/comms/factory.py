from __future__ import annotations

from clipos.config import Settings
from clipos.gateways.comms.contracts import CommunicationsAdapter
from clipos.gateways.comms.disabled import DisabledCommunicationsAdapter
from clipos.gateways.comms.fake import FakeCommsAdapter


class CommsAdapterRegistry:
    """The single configured communications adapter, fail-closed.

    Settings validation has already rejected any real provider name (no
    email provider decision exists for Phase 9), so the only selectable
    implementations are the disabled adapter and the explicitly mocked
    fake. Anything unexpected falls back to disabled.
    """

    def __init__(self, adapter: CommunicationsAdapter) -> None:
        self._adapter = adapter

    def get(self) -> CommunicationsAdapter:
        return self._adapter

    async def close(self) -> None:
        await self._adapter.close()


def build_comms_adapters(settings: Settings) -> CommsAdapterRegistry:
    if settings.comms_provider == "fake":
        return CommsAdapterRegistry(FakeCommsAdapter())
    return CommsAdapterRegistry(DisabledCommunicationsAdapter())
