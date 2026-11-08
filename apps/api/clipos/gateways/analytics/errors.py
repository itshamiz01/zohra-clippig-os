from __future__ import annotations


class AnalyticsAdapterError(RuntimeError):
    """A whole-call analytics failure with a sanitized, stable category.

    Per-metric problems are not errors: adapters report them as
    MetricResult rows with an explicit availability state. This error is
    reserved for failures that prevent any truthful per-metric answer,
    such as an unverified adapter or a missing provider configuration.
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


class UnsupportedAnalyticsError(AnalyticsAdapterError):
    def __init__(self, safe_message: str) -> None:
        super().__init__(
            "analytics_unsupported",
            safe_message,
            retryable=False,
            manual_next_step="Use a provider adapter that has been implemented and verified.",
        )
