from __future__ import annotations

import html
import re
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BIDI_CONTROL_CHARACTERS = re.compile(r"[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_SENTENCE_END = re.compile(r"[.!?…。！？][\"'’”)]*$")


class TimedText(Protocol):
    start_ms: int
    end_ms: int
    text: str


class CaptionLayoutError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CaptionCue:
    ordinal: int
    source_start_ms: int
    source_end_ms: int
    output_start_ms: int
    output_end_ms: int
    text: str
    line_count: int
    character_count: int
    timing_source: str


def _clean_text(value: str) -> str:
    without_controls = _CONTROL_CHARACTERS.sub("", value)
    without_bidi_controls = _BIDI_CONTROL_CHARACTERS.sub("", without_controls)
    # Curly braces can become ASS override delimiters inside FFmpeg/libass even when the source
    # format is SRT. Keep punctuation visible while making untrusted transcript text inert.
    without_overrides = without_bidi_controls.replace("{", "｛").replace("}", "｝")
    return " ".join(without_overrides.replace("\r", " ").replace("\n", " ").split())


def _split_long_tokens(text: str, width: int) -> list[str]:
    tokens: list[str] = []
    for token in text.split():
        if len(token) <= width:
            tokens.append(token)
            continue
        tokens.extend(token[index : index + width] for index in range(0, len(token), width))
    return tokens


def _layout(text: str, max_characters_per_line: int, max_lines: int) -> list[str]:
    cleaned = _clean_text(text)
    if not cleaned:
        return []
    tokens = _split_long_tokens(cleaned, max_characters_per_line)
    wrapped = textwrap.wrap(
        " ".join(tokens),
        width=max_characters_per_line,
        break_long_words=False,
        break_on_hyphens=False,
        replace_whitespace=True,
        drop_whitespace=True,
    )
    if len(wrapped) > max_lines:
        raise CaptionLayoutError("caption text exceeds the configured line budget")
    if any(len(line) > max_characters_per_line for line in wrapped):
        raise CaptionLayoutError("caption text exceeds the configured line width")
    return wrapped


def _make_cue(
    *,
    ordinal: int,
    source_start_ms: int,
    source_end_ms: int,
    clip_start_ms: int,
    clip_end_ms: int,
    text: str,
    timing_source: str,
    max_characters_per_line: int,
    max_lines: int,
) -> CaptionCue:
    source_start = max(clip_start_ms, source_start_ms)
    source_end = min(clip_end_ms, source_end_ms)
    if source_end <= source_start:
        source_end = min(clip_end_ms, source_start + 200)
    if source_end <= source_start:
        raise CaptionLayoutError("caption timing falls outside the approved source window")
    lines = _layout(text, max_characters_per_line, max_lines)
    if not lines:
        raise CaptionLayoutError("caption cue contains no visible text")
    rendered = "\n".join(lines)
    return CaptionCue(
        ordinal=ordinal,
        source_start_ms=source_start,
        source_end_ms=source_end,
        output_start_ms=source_start - clip_start_ms,
        output_end_ms=source_end - clip_start_ms,
        text=rendered,
        line_count=len(lines),
        character_count=sum(len(line) for line in lines),
        timing_source=timing_source,
    )


def _word_groups(
    words: Sequence[TimedText],
    *,
    max_characters_per_line: int,
    max_lines: int,
    max_cue_ms: int,
) -> list[tuple[int, int, str]]:
    groups: list[tuple[int, int, str]] = []
    current: list[TimedText] = []
    capacity = max_characters_per_line * max_lines
    for word in words:
        cleaned = _clean_text(word.text)
        if not cleaned:
            continue
        if not current:
            current = [word]
            continue
        tentative = " ".join(_clean_text(item.text) for item in [*current, word])
        starts_at = current[0].start_ms
        duration = max(word.end_ms, word.start_ms + 1) - starts_at
        exceeds_capacity = len(tentative) > capacity
        try:
            _layout(tentative, max_characters_per_line, max_lines)
        except CaptionLayoutError:
            exceeds_capacity = True
        if duration > max_cue_ms or exceeds_capacity:
            text = " ".join(_clean_text(item.text) for item in current)
            groups.append((current[0].start_ms, max(item.end_ms for item in current), text))
            current = [word]
        else:
            current.append(word)
        current_text = " ".join(_clean_text(item.text) for item in current)
        current_duration = max(item.end_ms for item in current) - current[0].start_ms
        if _SENTENCE_END.search(current_text) and current_duration >= 700:
            groups.append((current[0].start_ms, max(item.end_ms for item in current), current_text))
            current = []
    if current:
        groups.append(
            (
                current[0].start_ms,
                max(item.end_ms for item in current),
                " ".join(_clean_text(item.text) for item in current),
            )
        )
    return groups


def _segment_groups(
    segments: Sequence[TimedText],
    *,
    max_characters_per_line: int,
    max_lines: int,
) -> list[tuple[int, int, str]]:
    capacity = max_characters_per_line * max_lines
    groups: list[tuple[int, int, str]] = []
    for segment in segments:
        cleaned = _clean_text(segment.text)
        if not cleaned:
            continue
        tokens = _split_long_tokens(cleaned, max_characters_per_line)
        chunks: list[str] = []
        current: list[str] = []
        for token in tokens:
            tentative = " ".join([*current, token])
            if current and len(tentative) > capacity:
                chunks.append(" ".join(current))
                current = [token]
            else:
                current.append(token)
        if current:
            chunks.append(" ".join(current))
        duration = max(1, segment.end_ms - segment.start_ms)
        for index, chunk in enumerate(chunks):
            start = segment.start_ms + round(duration * index / len(chunks))
            end = segment.start_ms + round(duration * (index + 1) / len(chunks))
            groups.append((start, max(start + 1, end), chunk))
    return groups


def build_caption_cues(
    *,
    words: Sequence[TimedText],
    segments: Sequence[TimedText],
    clip_start_ms: int,
    clip_end_ms: int,
    max_characters_per_line: int,
    max_lines: int,
    max_cue_ms: int,
    max_segments: int,
) -> list[CaptionCue]:
    if clip_end_ms <= clip_start_ms:
        raise CaptionLayoutError("caption window is invalid")
    selected_words = [
        item for item in words if item.end_ms > clip_start_ms and item.start_ms < clip_end_ms
    ]
    if selected_words:
        raw_groups = _word_groups(
            selected_words,
            max_characters_per_line=max_characters_per_line,
            max_lines=max_lines,
            max_cue_ms=max_cue_ms,
        )
        timing_source = "word_timestamps"
    else:
        selected_segments = [
            item for item in segments if item.end_ms > clip_start_ms and item.start_ms < clip_end_ms
        ]
        raw_groups = _segment_groups(
            selected_segments,
            max_characters_per_line=max_characters_per_line,
            max_lines=max_lines,
        )
        timing_source = "segment_timestamps"
    if not raw_groups:
        raise CaptionLayoutError("no timestamped transcript text overlaps the approved window")
    if len(raw_groups) > max_segments:
        raise CaptionLayoutError("caption segment count exceeds the configured resource limit")
    cues = [
        _make_cue(
            ordinal=index,
            source_start_ms=start,
            source_end_ms=end,
            clip_start_ms=clip_start_ms,
            clip_end_ms=clip_end_ms,
            text=text,
            timing_source=timing_source,
            max_characters_per_line=max_characters_per_line,
            max_lines=max_lines,
        )
        for index, (start, end, text) in enumerate(raw_groups)
    ]
    for previous, current in zip(cues, cues[1:], strict=False):
        if current.output_start_ms < previous.output_start_ms:
            raise CaptionLayoutError("caption cues are not monotonic")
    return cues


def _srt_timestamp(milliseconds: int) -> str:
    bounded = max(0, milliseconds)
    hours, remainder = divmod(bounded, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def cues_to_srt(cues: Sequence[CaptionCue]) -> str:
    blocks: list[str] = []
    for cue in cues:
        safe_text = html.escape(cue.text, quote=False)
        blocks.append(
            f"{cue.ordinal + 1}\n"
            f"{_srt_timestamp(cue.output_start_ms)} --> {_srt_timestamp(cue.output_end_ms)}\n"
            f"{safe_text}\n"
        )
    return "\n".join(blocks) + "\n"
