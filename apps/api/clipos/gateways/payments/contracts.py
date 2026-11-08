from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from clipos.core.enums import ProviderMode


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    """One payment a future payments provider would process.

    Phase 9 only defines the boundary: revenue records inside ClipOS are
    operator-recorded facts, and no adapter in this phase can move money,
    so a request like this can never be charged here.
    """

    amount_minor_units: int
    currency: str
    external_reference: str | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class PaymentReceipt:
    """What a payment attempt reports back.

    ``processed`` is always False in Phase 9: the disabled adapter raises
    instead of returning a receipt, and the mocked fake adapter only
    records the request in memory — no money ever moves.
    """

    provider: str
    mode: ProviderMode
    processed: bool
    provider_reference: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentsCapabilities:
    provider: str
    mode: ProviderMode
    limitations: tuple[str, ...]


class PaymentsAdapter(Protocol):
    provider: str
    mode: ProviderMode
    source: str
    capabilities: PaymentsCapabilities

    async def process_payment(self, *, request: PaymentRequest) -> PaymentReceipt: ...

    async def close(self) -> None: ...
