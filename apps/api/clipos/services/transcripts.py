from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.config import Settings
from clipos.core.enums import DiarizationState, TranscriptState
from clipos.gateways.transcription import TranscriptionResult
from clipos.models import (
    Transcript,
    TranscriptChunk,
    TranscriptSegment,
    TranscriptWord,
)

_ALGORITHM_VERSION = "transcript-normalize-chunk-v1"
_WORD_PATTERN = re.compile(r"[^\W\d_]{3,}", flags=re.UNICODE)
_STOPWORDS = {
    "and",
    "are",
    "but",
    "for",
    "from",
    "have",
    "into",
    "not",
    "that",
    "the",
    "their",
    "this",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
    "you",
    "your",
}


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split())


def extract_topics(text: str, *, limit: int = 8) -> list[str]:
    words = [
        item.casefold() for item in _WORD_PATTERN.findall(text) if item.casefold() not in _STOPWORDS
    ]
    return [word for word, _ in Counter(words).most_common(limit)]


def extractive_summary(text: str, *, limit: int = 280) -> str | None:
    clean = normalize_text(text)
    if not clean:
        return None
    sentence_end = min(
        (
            position
            for position in (clean.find(". "), clean.find("? "), clean.find("! "))
            if position >= 0
        ),
        default=-1,
    )
    summary = clean[: sentence_end + 1] if sentence_end >= 0 else clean
    if len(summary) > limit:
        summary = summary[: limit - 1].rstrip() + "…"
    return summary


@dataclass(frozen=True, slots=True)
class ChunkPlan:
    ordinal: int
    start_segment_ordinal: int
    end_segment_ordinal: int
    start_ms: int
    end_ms: int
    char_start: int
    char_end: int
    text: str


def build_chunks(result: TranscriptionResult, settings: Settings) -> tuple[str, list[ChunkPlan]]:
    normalized_segments = [normalize_text(item.text) for item in result.segments]
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for index, text in enumerate(normalized_segments):
        start = cursor
        cursor += len(text)
        offsets.append((start, cursor))
        if index < len(normalized_segments) - 1:
            cursor += 1
    full_text = " ".join(normalized_segments)

    chunks: list[ChunkPlan] = []
    current_start = 0
    for index in range(len(result.segments)):
        segment = result.segments[index]
        start_segment = result.segments[current_start]
        char_start = offsets[current_start][0]
        char_end = offsets[index][1]
        duration = segment.end_seconds - start_segment.start_seconds
        char_count = char_end - char_start
        pause_after = (
            result.segments[index + 1].start_seconds - segment.end_seconds
            if index + 1 < len(result.segments)
            else 0
        )
        sentence_end = normalized_segments[index].endswith(
            (".", "?", "!", "。", "！", "？", "।", "؟")
        )
        at_limit = (
            duration >= settings.transcript_chunk_max_seconds
            or char_count >= settings.transcript_chunk_max_chars
        )
        semantic_pause = pause_after >= settings.transcript_chunk_pause_seconds and sentence_end
        is_last = index == len(result.segments) - 1
        if not (at_limit or semantic_pause or is_last):
            continue
        chunk_text = full_text[char_start:char_end]
        chunks.append(
            ChunkPlan(
                ordinal=len(chunks),
                start_segment_ordinal=current_start,
                end_segment_ordinal=index,
                start_ms=round(start_segment.start_seconds * 1000),
                end_ms=round(segment.end_seconds * 1000),
                char_start=char_start,
                char_end=char_end,
                text=chunk_text,
            )
        )
        current_start = index + 1
    return full_text, chunks


class TranscriptService:
    @staticmethod
    async def persist(
        session: AsyncSession,
        *,
        tenant_id: UUID,
        asset_id: UUID,
        result: TranscriptionResult,
        provider: str,
        model: str,
        settings: Settings,
    ) -> Transcript:
        existing_id = await session.scalar(
            select(Transcript.id).where(
                Transcript.tenant_id == tenant_id,
                Transcript.asset_id == asset_id,
            )
        )
        if existing_id:
            await session.execute(
                delete(Transcript).where(
                    Transcript.id == existing_id,
                    Transcript.tenant_id == tenant_id,
                )
            )
            await session.flush()

        normalized_text, chunks = build_chunks(result, settings)
        confidence = result.confidence
        state = (
            TranscriptState.NEEDS_REVIEW
            if confidence is None or confidence < settings.transcript_confidence_review_threshold
            else TranscriptState.READY
        )
        transcript = Transcript(
            tenant_id=tenant_id,
            asset_id=asset_id,
            state=state,
            provider=provider,
            model=model,
            language=result.language,
            confidence=confidence,
            diarization_state=(
                DiarizationState.AVAILABLE
                if result.diarization_available
                else DiarizationState.UNAVAILABLE
            ),
            original_text=result.text,
            normalized_text=normalized_text,
            summary=extractive_summary(normalized_text, limit=500),
            key_topics=extract_topics(normalized_text),
            raw_provider_output=result.raw_provider_output,
            word_timestamps_available=bool(result.words),
            segment_count=len(result.segments),
            word_count=len(result.words),
            provider_request_id=result.provider_request_id,
            algorithm_version=_ALGORITHM_VERSION,
        )
        session.add(transcript)
        await session.flush()

        segment_rows: list[TranscriptSegment] = []
        for ordinal, segment in enumerate(result.segments):
            row = TranscriptSegment(
                tenant_id=tenant_id,
                transcript_id=transcript.id,
                ordinal=ordinal,
                start_ms=round(segment.start_seconds * 1000),
                end_ms=round(segment.end_seconds * 1000),
                text=segment.text,
                normalized_text=normalize_text(segment.text),
                confidence=segment.confidence,
                speaker_label=segment.speaker_label,
            )
            session.add(row)
            segment_rows.append(row)
        await session.flush()

        segment_index = 0
        for ordinal, word in enumerate(result.words):
            midpoint = (word.start_seconds + word.end_seconds) / 2
            while (
                segment_index + 1 < len(result.segments)
                and midpoint > result.segments[segment_index].end_seconds
            ):
                segment_index += 1
            segment_id = segment_rows[segment_index].id if segment_rows else None
            session.add(
                TranscriptWord(
                    tenant_id=tenant_id,
                    transcript_id=transcript.id,
                    segment_id=segment_id,
                    ordinal=ordinal,
                    start_ms=round(word.start_seconds * 1000),
                    end_ms=round(word.end_seconds * 1000),
                    text=word.text,
                    confidence=word.confidence,
                    speaker_label=word.speaker_label,
                )
            )

        for chunk in chunks:
            session.add(
                TranscriptChunk(
                    tenant_id=tenant_id,
                    transcript_id=transcript.id,
                    ordinal=chunk.ordinal,
                    start_segment_ordinal=chunk.start_segment_ordinal,
                    end_segment_ordinal=chunk.end_segment_ordinal,
                    start_ms=chunk.start_ms,
                    end_ms=chunk.end_ms,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    text=chunk.text,
                    summary=extractive_summary(chunk.text),
                    topics=extract_topics(chunk.text, limit=5),
                    algorithm_version=_ALGORITHM_VERSION,
                )
            )
        await session.flush()
        return transcript
