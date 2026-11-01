from __future__ import annotations


class PublishingAdapterError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        retryable: bool = False,
        uncertain_remote_result: bool = False,
        retry_after_seconds: int | None = None,
        manual_next_step: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message
        self.retryable = retryable
        self.uncertain_remote_result = uncertain_remote_result
        self.retry_after_seconds = retry_after_seconds
        self.manual_next_step = manual_next_step


class OAuthExchangeError(PublishingAdapterError):
    pass


class MissingScopeError(PublishingAdapterError):
    def __init__(self, missing_scopes: set[str] | frozenset[str]) -> None:
        scopes = sorted(missing_scopes)
        super().__init__(
            "missing_scope",
            "the provider grant is missing required publishing permissions",
            manual_next_step="Reconnect the destination and approve the required scopes.",
        )
        self.missing_scopes = scopes


class TokenRefreshError(PublishingAdapterError):
    def __init__(self, message: str = "the provider token could not be refreshed") -> None:
        super().__init__(
            "token_refresh_failed",
            message,
            manual_next_step="Reconnect the destination before retrying.",
        )


class RateLimitedError(PublishingAdapterError):
    def __init__(self, retry_after_seconds: int | None = None) -> None:
        super().__init__(
            "provider_rate_limited",
            "the provider rate limit deferred this upload",
            retryable=True,
            retry_after_seconds=retry_after_seconds,
            manual_next_step="Wait for the provider retry window, then use safe retry.",
        )


class UncertainRemoteResultError(PublishingAdapterError):
    def __init__(self, message: str = "the provider response was uncertain") -> None:
        super().__init__(
            "uncertain_remote_result",
            message,
            retryable=False,
            uncertain_remote_result=True,
            manual_next_step=(
                "Run reconciliation before retrying so a successful remote upload is not "
                "duplicated."
            ),
        )


class UnsupportedCapabilityError(PublishingAdapterError):
    def __init__(self, capability: str) -> None:
        super().__init__(
            "unsupported_capability",
            f"the configured provider does not support {capability}",
        )
        self.capability = capability
