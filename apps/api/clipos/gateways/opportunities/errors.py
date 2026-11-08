from __future__ import annotations


class OpportunityAdapterError(RuntimeError):
    """A whole-call opportunity source failure with a sanitized category.

    A failed fetch is recorded on the sync run with this category; the
    service never fabricates partial records to hide an adapter failure.
    """

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


class UnsupportedOpportunityError(OpportunityAdapterError):
    def __init__(self, safe_message: str) -> None:
        super().__init__(
            "opportunity_unsupported",
            safe_message,
            retryable=False,
            manual_next_step=("Use a source kind whose adapter has been implemented and verified."),
        )
