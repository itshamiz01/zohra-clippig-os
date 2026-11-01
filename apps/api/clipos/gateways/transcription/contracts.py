from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictTranscriptionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptSegmentData(StrictTranscriptionModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    speaker_label: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def ordered(self) -> TranscriptSegmentData:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("segment end must be after start")
        return self


class TranscriptWordData(StrictTranscriptionModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)
    speaker_label: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def ordered(self) -> TranscriptWordData:
        if self.end_seconds < self.start_seconds:
            raise ValueError("word end must not precede start")
        return self


class TranscriptionResult(StrictTranscriptionModel):
    text: str = Field(min_length=1)
    language: str | None = Field(default=None, max_length=35)
    confidence: float | None = Field(default=None, ge=0, le=1)
    segments: list[TranscriptSegmentData] = Field(min_length=1)
    words: list[TranscriptWordData] = Field(default_factory=list)
    diarization_available: bool = False
    raw_provider_output: dict[str, Any] = Field(default_factory=dict)
    provider_request_id: str | None = Field(default=None, max_length=200)


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio_path: Path
    duration_seconds: float
    language_hint: str | None = None


class TranscriptionAdapter(Protocol):
    provider_name: str
    model_name: str

    async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult: ...

    async def close(self) -> None: ...
