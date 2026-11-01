class MediaPipelineError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        retryable: bool = False,
        needs_review: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.safe_message = message
        self.retryable = retryable
        self.needs_review = needs_review


class UploadValidationError(MediaPipelineError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(category, message, retryable=False, needs_review=False)


class MediaToolUnavailableError(MediaPipelineError):
    def __init__(self, tool: str) -> None:
        super().__init__(
            "media_tool_unavailable",
            f"{tool} is unavailable or could not be verified",
            retryable=False,
            needs_review=True,
        )


class MediaProcessTimeoutError(MediaPipelineError):
    def __init__(self, operation: str) -> None:
        super().__init__(
            "media_process_timeout",
            f"{operation} exceeded its configured timeout",
            retryable=True,
        )


class MediaProcessCancelledError(MediaPipelineError):
    def __init__(self) -> None:
        super().__init__(
            "render_cancelled",
            "render was cancelled by a tenant operator",
            retryable=False,
        )
