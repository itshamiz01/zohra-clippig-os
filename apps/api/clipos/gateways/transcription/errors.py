class TranscriptionError(RuntimeError):
    category = "transcription_error"
    retryable = False


class TranscriptionDisabledError(TranscriptionError):
    category = "transcription_disabled"


class TranscriptionAuthenticationError(TranscriptionError):
    category = "transcription_authentication"


class TranscriptionRateLimitError(TranscriptionError):
    category = "transcription_rate_limit"
    retryable = True


class TranscriptionTimeoutError(TranscriptionError):
    category = "transcription_timeout"
    retryable = True


class TranscriptionTransientError(TranscriptionError):
    category = "transcription_transient"
    retryable = True


class TranscriptionResponseError(TranscriptionError):
    category = "transcription_response_invalid"
