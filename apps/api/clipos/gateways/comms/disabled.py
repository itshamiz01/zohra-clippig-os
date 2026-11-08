from __future__ import annotations

from clipos.core.enums import ProviderMode
from clipos.gateways.comms.contracts import (
    CommsCapabilities,
    DeliveryReceipt,
    OutboundMessage,
)
from clipos.gateways.comms.errors import UnsupportedCommsError


class DisabledCommunicationsAdapter:
    """Default adapter: delivery is disabled and fails truthfully.

    Phase 9 has no email provider: none has been chosen by the user, so
    there is nothing to configure and nothing to send through. Every
    delivery attempt raises instead of pretending a message went out.
    """

    provider = "disabled"
    mode = ProviderMode.DISABLED
    source = "disabled_communications_adapter"

    def __init__(self) -> None:
        self.capabilities = CommsCapabilities(
            provider=self.provider,
            mode=ProviderMode.DISABLED,
            channels=(),
            limitations=(
                "No email provider has been chosen for Phase 9, so outbound "
                "communications are disabled; sales assets stay as drafts in ClipOS.",
                "Delivery attempts fail truthfully with comms_unsupported and "
                "nothing is sent, queued or simulated.",
            ),
        )

    async def deliver(self, *, message: OutboundMessage) -> DeliveryReceipt:
        del message
        raise UnsupportedCommsError(
            "outbound communications are disabled in this build: no email provider "
            "is integrated, so no message can be delivered"
        )

    async def close(self) -> None:
        return None
