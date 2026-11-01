from __future__ import annotations

import asyncio
import secrets

from clipos.gateways.transcription.contracts import (
    TranscriptionAdapter,
    TranscriptionRequest,
    TranscriptionResult,
)
from clipos.gateways.transcription.errors import TranscriptionError, TranscriptionResponseError


class TranscriptionGateway:
    def __init__(
        self,
        adapter: TranscriptionAdapter,
        *,
        max_retries: int = 1,
        max_text_chars: int = 5_000_000,
        max_segments: int = 50_000,
        max_words: int = 150_000,
    ) -> None:
        self.adapter = adapter
        self.max_retries = max_retries
        self.max_text_chars = max_text_chars
        self.max_segments = max_segments
        self.max_words = max_words

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        attempts = 0
        while True:
            attempts += 1
            try:
                result = await self.adapter.transcribe(request)
                if len(result.segments) > self.max_segments or len(result.words) > self.max_words:
                    raise TranscriptionResponseError(
                        "transcription provider returned too many timestamp rows"
                    )
                segment_chars = sum(len(item.text) for item in result.segments)
                if len(result.text) > self.max_text_chars or segment_chars > self.max_text_chars:
                    raise TranscriptionResponseError(
                        "transcription provider returned excessive transcript text"
                    )
                self._validate_timeline(result, request.duration_seconds)
                return result
            except TranscriptionError as exc:
                if not exc.retryable or attempts > self.max_retries:
                    raise
                delay = min(4.0, 0.5 * (2 ** (attempts - 1))) + secrets.randbelow(100) / 1000
                await asyncio.sleep(delay)

    @staticmethod
    def _validate_timeline(result: TranscriptionResult, duration_seconds: float) -> None:
        tolerance = max(1.0, min(5.0, duration_seconds * 0.01))
        prior_start = -1.0
        for segment in result.segments:
            if segment.start_seconds < prior_start:
                raise TranscriptionResponseError("transcript segments are not time ordered")
            if segment.end_seconds > duration_seconds + tolerance:
                raise TranscriptionResponseError("transcript segment exceeds media duration")
            prior_start = segment.start_seconds
        prior_start = -1.0
        for word in result.words:
            if word.start_seconds < prior_start:
                raise TranscriptionResponseError("transcript words are not time ordered")
            if word.end_seconds > duration_seconds + tolerance:
                raise TranscriptionResponseError("transcript word exceeds media duration")
            prior_start = word.start_seconds

    async def close(self) -> None:
        await self.adapter.close()
