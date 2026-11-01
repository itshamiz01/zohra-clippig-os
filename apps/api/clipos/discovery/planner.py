from __future__ import annotations

import math
import re
from dataclasses import dataclass

_SENTENCE_ENDINGS = (".", "?", "!", "。", "！", "？", "।", "؟")
_ANSWER_LEADS = (
    "because ",
    "but ",
    "however ",
    "it ",
    "so ",
    "that ",
    "therefore ",
    "this ",
    "yes ",
)
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SegmentEvidence:
    ordinal: int
    start_ms: int
    end_ms: int
    text: str
    normalized_text: str
    confidence: float | None
    speaker_label: str | None


@dataclass(frozen=True, slots=True)
class WordEvidence:
    ordinal: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    min_duration_ms: int
    target_duration_ms: int
    max_duration_ms: int
    padding_ms: int
    max_candidates: int
    pool_multiplier: int
    overlap_threshold: float
    similarity_threshold: float


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    start_segment_ordinal: int
    end_segment_ordinal: int
    speech_start_ms: int
    speech_end_ms: int
    start_ms: int
    end_ms: int
    excerpt: str
    sentence_aligned_start: bool
    sentence_aligned_end: bool
    source_is_short: bool

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def fingerprint(self) -> str:
        return f"{self.start_segment_ordinal}:{self.end_segment_ordinal}"


def build_candidate_pool(
    segments: list[SegmentEvidence],
    *,
    source_duration_ms: int,
    config: PlannerConfig,
) -> list[DiscoveryPlan]:
    if not segments or source_duration_ms <= 0:
        return []
    _validate_segments(segments, source_duration_ms)
    pool_limit = max(config.max_candidates, config.max_candidates * config.pool_multiplier)
    source_is_short = segments[-1].end_ms - segments[0].start_ms < config.min_duration_ms
    if source_is_short:
        plan = _make_plan(segments, 0, len(segments) - 1, source_duration_ms, config)
        return [plan] if plan.duration_ms <= config.max_duration_ms else []

    starts = [0]
    starts.extend(
        index
        for index in range(1, len(segments))
        if _is_sentence_end(segments[index - 1].normalized_text)
    )
    if len(starts) < min(pool_limit, len(segments)):
        stride = max(1, math.ceil(len(segments) / pool_limit))
        starts.extend(range(0, len(segments), stride))
    starts = sorted(set(starts))
    starts = _evenly_limit(starts, pool_limit)

    plans: list[DiscoveryPlan] = []
    seen: set[tuple[int, int]] = set()
    for proposed_start in starts:
        start = _include_question_context(segments, proposed_start)
        end = _choose_end(segments, start, config)
        key = (start, end)
        if key in seen:
            continue
        plan = _make_plan(segments, start, end, source_duration_ms, config)
        if plan.duration_ms > config.max_duration_ms:
            continue
        if not source_is_short and plan.duration_ms < config.min_duration_ms:
            continue
        seen.add(key)
        plans.append(plan)
    return plans


def _choose_end(segments: list[SegmentEvidence], start: int, config: PlannerConfig) -> int:
    speech_start = segments[start].start_ms
    candidates: list[int] = []
    last_within_limit = start
    for index in range(start, len(segments)):
        duration = segments[index].end_ms - speech_start
        if duration > config.max_duration_ms:
            break
        last_within_limit = index
        if duration >= config.min_duration_ms and _is_sentence_end(segments[index].normalized_text):
            candidates.append(index)
    if candidates:
        return min(
            candidates,
            key=lambda index: abs(
                (segments[index].end_ms - speech_start) - config.target_duration_ms
            ),
        )
    return last_within_limit


def _make_plan(
    segments: list[SegmentEvidence],
    start: int,
    end: int,
    source_duration_ms: int,
    config: PlannerConfig,
) -> DiscoveryPlan:
    first = segments[start]
    last = segments[end]
    speech_start = first.start_ms
    speech_end = last.end_ms
    available_padding = max(0, config.max_duration_ms - (speech_end - speech_start))
    desired_before = min(config.padding_ms, speech_start)
    desired_after = min(config.padding_ms, source_duration_ms - speech_end)
    before = min(desired_before, available_padding // 2)
    after = min(desired_after, available_padding - before)
    before += min(desired_before - before, available_padding - before - after)
    after += min(desired_after - after, available_padding - before - after)
    start_ms = speech_start - before
    end_ms = speech_end + after
    excerpt = " ".join(item.normalized_text for item in segments[start : end + 1]).strip()
    return DiscoveryPlan(
        start_segment_ordinal=first.ordinal,
        end_segment_ordinal=last.ordinal,
        speech_start_ms=speech_start,
        speech_end_ms=speech_end,
        start_ms=start_ms,
        end_ms=end_ms,
        excerpt=excerpt,
        sentence_aligned_start=start == 0 or _is_sentence_end(segments[start - 1].normalized_text),
        sentence_aligned_end=_is_sentence_end(last.normalized_text),
        source_is_short=speech_end - speech_start < config.min_duration_ms,
    )


def _include_question_context(segments: list[SegmentEvidence], start: int) -> int:
    if start <= 0:
        return start
    text = segments[start].normalized_text.casefold()
    previous = segments[start - 1].normalized_text.rstrip()
    if previous.endswith(("?", "؟", "？")) and text.startswith(_ANSWER_LEADS):
        return start - 1
    return start


def _is_sentence_end(text: str) -> bool:
    return text.rstrip().endswith(_SENTENCE_ENDINGS)


def _evenly_limit(values: list[int], limit: int) -> list[int]:
    if len(values) <= limit:
        return values
    if limit == 1:
        return [values[0]]
    selected = {values[round(index * (len(values) - 1) / (limit - 1))] for index in range(limit)}
    return sorted(selected)


def _validate_segments(segments: list[SegmentEvidence], source_duration_ms: int) -> None:
    previous_start = -1
    for expected, segment in enumerate(segments):
        if segment.ordinal != expected:
            raise ValueError("transcript segment ordinals must be contiguous from zero")
        if segment.start_ms < previous_start or segment.end_ms <= segment.start_ms:
            raise ValueError("transcript segment timeline is invalid")
        if segment.end_ms > source_duration_ms:
            raise ValueError("transcript segment exceeds verified source duration")
        previous_start = segment.start_ms


def temporal_overlap_ratio(left: DiscoveryPlan, right: DiscoveryPlan) -> float:
    overlap = max(0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))
    denominator = min(left.duration_ms, right.duration_ms)
    return overlap / denominator if denominator > 0 else 0.0


def text_similarity(left: str, right: str) -> float:
    left_tokens = {item.casefold() for item in _TOKEN_PATTERN.findall(left)}
    right_tokens = {item.casefold() for item in _TOKEN_PATTERN.findall(right)}
    union = left_tokens | right_tokens
    if not union:
        return 1.0 if left.strip() == right.strip() else 0.0
    return len(left_tokens & right_tokens) / len(union)
