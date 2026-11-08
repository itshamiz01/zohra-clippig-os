from __future__ import annotations


class CommsAdapterError(RuntimeError):
    """A communications failure with a sanitized, stable category."""

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


class UnsupportedCommsError(CommsAdapterError):
    def __init__(self, safe_message: str) -> None:
        super().__init__(
            "comms_unsupported",
            safe_message,
            retryable=False,
            manual_next_step=(
                "An email provider decision is still owed for Phase 9; until a provider "
                "is chosen, reviewed and integrated, communications stay disabled and "
                "sales assets remain drafts inside ClipOS."
            ),
        )
