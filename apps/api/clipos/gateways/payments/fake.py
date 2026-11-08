from __future__ import annotations

from clipos.core.enums import ProviderMode
from clipos.gateways.payments.contracts import (
    PaymentReceipt,
    PaymentRequest,
    PaymentsCapabilities,
)


class FakePaymentsAdapter:
    """In-memory payments double for tests and local demos.

    This adapter is MOCKED evidence only. ``process_payment`` records the
    request in this process's memory and returns a mocked receipt with
    ``processed=False``; it opens no connection and moves no money.
    Settings validation forbids selecting it in staging/production, and
    the CRM service never calls it — revenue records are stored facts,
    not provider transactions.
    """

    provider = "fake"
    mode = ProviderMode.MOCKED
    source = "fake_payments_adapter"

    def __init__(self) -> None:
        self.capabilities = PaymentsCapabilities(
            provider=self.provider,
            mode=ProviderMode.MOCKED,
            limitations=(
                "Deterministic in-memory double for tests and local development "
                "only; it records requests in memory and processes nothing.",
                "Mocked receipts are never evidence that money moved.",
            ),
        )
        self.requests: list[PaymentRequest] = []
        self.closed = False

    async def process_payment(self, *, request: PaymentRequest) -> PaymentReceipt:
        self.requests.append(request)
        return PaymentReceipt(
            provider=self.provider,
            mode=ProviderMode.MOCKED,
            processed=False,
            provider_reference=f"fake-payment-{len(self.requests)}",
        )

    async def close(self) -> None:
        self.closed = True
