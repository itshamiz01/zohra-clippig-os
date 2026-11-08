from __future__ import annotations

from clipos.core.enums import ProviderMode
from clipos.gateways.payments.contracts import (
    PaymentReceipt,
    PaymentRequest,
    PaymentsCapabilities,
)
from clipos.gateways.payments.errors import UnsupportedPaymentsError


class DisabledPaymentsAdapter:
    """Default adapter: payments are disabled and fail truthfully.

    No payment provider has been chosen for Phase 9, so there is nothing
    to charge through. Revenue records in the CRM are operator-recorded
    facts only; every processing attempt raises instead of pretending
    money moved.
    """

    provider = "disabled"
    mode = ProviderMode.DISABLED
    source = "disabled_payments_adapter"

    def __init__(self) -> None:
        self.capabilities = PaymentsCapabilities(
            provider=self.provider,
            mode=ProviderMode.DISABLED,
            limitations=(
                "No payment provider has been chosen for Phase 9, so payments are "
                "disabled; ClipOS records revenue facts but moves no money.",
                "Processing attempts fail truthfully with payments_unsupported.",
            ),
        )

    async def process_payment(self, *, request: PaymentRequest) -> PaymentReceipt:
        del request
        raise UnsupportedPaymentsError(
            "payments are disabled in this build: no payment provider is "
            "integrated, so no payment can be processed"
        )

    async def close(self) -> None:
        return None
