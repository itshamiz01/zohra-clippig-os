from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from clipos.core.enums import ProviderMode


class CommsChannel(StrEnum):
    """Outbound communication channels a future provider could serve.

    Phase 9 defines the boundary only: no provider is integrated, so no
    channel can actually deliver anything in this phase.
    """

    EMAIL = "email"


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """One message a future communications provider would deliver.

    ``recipient_label`` is a public contact-route label (for example
    "business email listed on channel page"), never an address: ClipOS
    stores no private contact data, so no adapter can be handed a real
    destination in this phase.
    """

    channel: CommsChannel
    recipient_label: str
    subject: str
    body: str
    draft_id: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """What a delivery attempt reports back.

    ``delivered`` is always False in Phase 9: the disabled adapter raises
    instead of returning a receipt, and the mocked fake adapter only
    records the message in memory — nothing reaches any recipient.
    """

    provider: str
    mode: ProviderMode
    delivered: bool
    provider_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CommsCapabilities:
    provider: str
    mode: ProviderMode
    channels: tuple[str, ...]
    limitations: tuple[str, ...]


class CommunicationsAdapter(Protocol):
    provider: str
    mode: ProviderMode
    source: str
    capabilities: CommsCapabilities

    async def deliver(self, *, message: OutboundMessage) -> DeliveryReceipt: ...

    async def close(self) -> None: ...
