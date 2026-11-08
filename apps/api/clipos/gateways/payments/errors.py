from __future__ import annotations


class PaymentsAdapterError(RuntimeError):
    """A payments failure with a sanitized, stable category."""

    def __init__(
        self,
        category: str,
        safe_message: str,
        *,
        retryable: bool = False,
        manual_next_step: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message
        self.retryable = retryable
        self.manual_next_step = manual_next_step


class UnsupportedPaymentsError(PaymentsAdapterError):
    def __init__(self, safe_message: str) -> None:
        super().__init__(
            "payments_unsupported",
            safe_message,
            retryable=False,
            manual_next_step=(
                "A payment provider decision is still owed for Phase 9; until a "
                "provider is chosen, reviewed and integrated in test mode, payments "
                "stay disabled and revenue records remain operator-recorded facts."
            ),
        )
