from __future__ import annotations

from clipos.core.enums import ProviderMode
from clipos.gateways.comms.contracts import (
    CommsCapabilities,
    CommsChannel,
    DeliveryReceipt,
    OutboundMessage,
)


class FakeCommsAdapter:
    """In-memory communications double for tests and local demos.

    This adapter is MOCKED evidence only. ``deliver`` records the message
    in this process's memory and returns a mocked receipt; it opens no
    connection, sends no email and contacts nobody. Settings validation
    forbids selecting it in staging/production, and the CRM service never
    calls it — drafts are stored, never delivered.
    """

    provider = "fake"
    mode = ProviderMode.MOCKED
    source = "fake_comms_adapter"

    def __init__(self) -> None:
        self.capabilities = CommsCapabilities(
            provider=self.provider,
            mode=ProviderMode.MOCKED,
            channels=(CommsChannel.EMAIL.value,),
            limitations=(
                "Deterministic in-memory double for tests and local development "
                "only; it records messages in memory and delivers nothing.",
                "Mocked delivery receipts are never evidence that a message reached anyone.",
            ),
        )
        self.delivered: list[OutboundMessage] = []
        self.closed = False

    async def deliver(self, *, message: OutboundMessage) -> DeliveryReceipt:
        self.delivered.append(message)
        return DeliveryReceipt(
            provider=self.provider,
            mode=ProviderMode.MOCKED,
            delivered=False,
            provider_reference=f"fake-comms-{len(self.delivered)}",
        )

    async def close(self) -> None:
        self.closed = True
