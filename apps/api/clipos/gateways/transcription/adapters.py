from __future__ import annotations

import asyncio
import json
import math
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from clipos.gateways.transcription.contracts import (
    TranscriptionRequest,
    TranscriptionResult,
    TranscriptSegmentData,
    TranscriptWordData,
)
from clipos.gateways.transcription.errors import (
    TranscriptionAuthenticationError,
    TranscriptionDisabledError,
    TranscriptionRateLimitError,
    TranscriptionResponseError,
    TranscriptionTimeoutError,
    TranscriptionTransientError,
)


def _multipart_prefix(fields: list[tuple[str, str]], boundary: str) -> bytes:
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode("ascii")
    )
    return b"".join(parts)


class _AsyncMultipartFileStream(httpx.AsyncByteStream):
    def __init__(self, path: Path, prefix: bytes, suffix: bytes) -> None:
        self.path = path
        self.prefix = prefix
        self.suffix = suffix

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.prefix
        handle = await asyncio.to_thread(self.path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(handle.read, 1024 * 1024):
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)
        yield self.suffix


class DisabledTranscriptionAdapter:
    provider_name = "disabled"
    model_name = "none"

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        del request
        raise TranscriptionDisabledError("transcription provider is disabled")

    async def close(self) -> None:
        return None


class OpenAITranscriptionAdapter:
    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        response_max_bytes: int = 64 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model_name = model
        self.response_max_bytes = response_max_bytes
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        data: list[tuple[str, str]] = [
            ("model", self.model_name),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "segment"),
            ("timestamp_granularities[]", "word"),
        ]
        if request.language_hint:
            data.append(("language", request.language_hint))
        boundary = f"clipos-{secrets.token_hex(16)}"
        prefix = _multipart_prefix(data, boundary)
        suffix = f"\r\n--{boundary}--\r\n".encode()
        try:
            audio_size = (await asyncio.to_thread(request.audio_path.stat)).st_size
            stream = _AsyncMultipartFileStream(request.audio_path, prefix, suffix)
            async with self._client.stream(
                "POST",
                "audio/transcriptions",
                content=stream,
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "Content-Length": str(len(prefix) + audio_size + len(suffix)),
                },
            ) as response:
                self._raise_for_status(response)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > self.response_max_bytes:
                        raise TranscriptionResponseError(
                            "transcription provider response exceeded the configured limit"
                        )
                    content.extend(chunk)
        except OSError as exc:
            raise TranscriptionResponseError("normalized audio is unavailable") from exc
        except httpx.TimeoutException as exc:
            raise TranscriptionTimeoutError("transcription provider timed out") from exc
        except httpx.TransportError as exc:
            raise TranscriptionTransientError("transcription provider transport failed") from exc
        try:
            body = json.loads(content)
            if not isinstance(body, dict):
                raise TypeError
            segments = [self._segment(item) for item in body.get("segments", [])]
            words = [self._word(item) for item in body.get("words", [])]
            text = str(body.get("text") or "").strip()
            confidence_values = [
                item.confidence for item in segments if item.confidence is not None
            ]
            confidence = (
                sum(confidence_values) / len(confidence_values) if confidence_values else None
            )
            return TranscriptionResult(
                text=text,
                language=_optional_string(body.get("language"), 35),
                confidence=confidence,
                segments=segments,
                words=words,
                diarization_available=any(item.speaker_label for item in segments),
                raw_provider_output=body,
                provider_request_id=response.headers.get("x-request-id"),
            )
        except (TypeError, ValueError, KeyError, ValidationError) as exc:
            raise TranscriptionResponseError(
                "transcription provider returned an invalid response shape"
            ) from exc

    @staticmethod
    def _segment(item: Any) -> TranscriptSegmentData:
        if not isinstance(item, dict):
            raise TypeError
        confidence = None
        if item.get("confidence") is not None:
            confidence = float(item["confidence"])
        elif item.get("avg_logprob") is not None:
            confidence = max(0.0, min(1.0, math.exp(float(item["avg_logprob"]))))
        return TranscriptSegmentData(
            start_seconds=float(item["start"]),
            end_seconds=float(item["end"]),
            text=str(item["text"]).strip(),
            confidence=confidence,
            speaker_label=_optional_string(item.get("speaker"), 100),
        )

    @staticmethod
    def _word(item: Any) -> TranscriptWordData:
        if not isinstance(item, dict):
            raise TypeError
        return TranscriptWordData(
            start_seconds=float(item["start"]),
            end_seconds=float(item["end"]),
            text=str(item.get("word") or item.get("text") or "").strip(),
            confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
            speaker_label=_optional_string(item.get("speaker"), 100),
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise TranscriptionAuthenticationError(
                "transcription provider rejected credentials or scope"
            )
        if response.status_code == 429:
            raise TranscriptionRateLimitError("transcription provider rate limit reached")
        if response.status_code >= 500:
            raise TranscriptionTransientError("transcription provider is temporarily unavailable")
        if response.status_code >= 400:
            raise TranscriptionResponseError(
                f"transcription provider rejected request ({response.status_code})"
            )

    async def close(self) -> None:
        await self._client.aclose()


def _optional_string(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered[:limit] or None
