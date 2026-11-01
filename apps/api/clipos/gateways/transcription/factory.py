from clipos.config import Settings
from clipos.gateways.transcription.adapters import (
    DisabledTranscriptionAdapter,
    OpenAITranscriptionAdapter,
)
from clipos.gateways.transcription.contracts import TranscriptionAdapter
from clipos.gateways.transcription.gateway import TranscriptionGateway


def build_transcription_gateway(settings: Settings) -> TranscriptionGateway:
    adapter: TranscriptionAdapter
    if settings.transcription_provider == "disabled":
        adapter = DisabledTranscriptionAdapter()
    elif settings.transcription_provider == "openai":
        assert settings.openai_api_key is not None
        assert settings.transcription_model is not None
        adapter = OpenAITranscriptionAdapter(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.transcription_model,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.transcription_timeout_seconds,
            response_max_bytes=settings.transcription_response_max_bytes,
        )
    else:
        raise ValueError("unsupported transcription provider")
    return TranscriptionGateway(
        adapter,
        max_retries=settings.transcription_max_retries,
        max_text_chars=settings.transcript_max_text_chars,
        max_segments=settings.transcript_max_segments,
        max_words=settings.transcript_max_words,
    )
