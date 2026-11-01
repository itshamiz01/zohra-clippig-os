from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from clipos.discovery.planner import DiscoveryPlan, SegmentEvidence

ALGORITHM_VERSION = "clip-discovery-v1"
SCORE_VERSION = "explainable-score-v1"
_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_HOOK_WORDS = {
    "attention",
    "here",
    "how",
    "imagine",
    "listen",
    "secret",
    "stop",
    "why",
    "क्या",
    "कैसे",
    "لماذا",
    "كيف",
}
_INFORMATION_MARKERS = {
    "because",
    "example",
    "first",
    "means",
    "reason",
    "result",
    "second",
    "therefore",
    "क्योंकि",
    "مثال",
}
_PAYOFF_MARKERS = {
    "but",
    "finally",
    "however",
    "instead",
    "so",
    "therefore",
    "लेकिन",
    "इसलिए",
    "لكن",
    "لذلك",
}
_EMOTION_WORDS = {
    "amazing",
    "angry",
    "excited",
    "fear",
    "incredible",
    "love",
    "powerful",
    "shocking",
    "surprising",
    "अद्भुत",
    "खुश",
    "مذهل",
}
_AUDIENCE_WORDS = {
    "you",
    "your",
    "try",
    "learn",
    "avoid",
    "आप",
    "आपका",
    "أنت",
}
_PLATFORM_PROFILES: dict[str, tuple[float, float, float]] = {
    # These are transparent editorial targets, not platform upload limits.
    "general": (15.0, 35.0, 60.0),
    "tiktok": (12.0, 28.0, 45.0),
    "instagram_reels": (15.0, 35.0, 60.0),
    "youtube_shorts": (20.0, 40.0, 60.0),
    "linkedin": (30.0, 55.0, 90.0),
}
_LEADING_CONTEXT_WORDS = {
    "also",
    "and",
    "because",
    "but",
    "he",
    "it",
    "she",
    "so",
    "that",
    "they",
    "this",
}


@dataclass(frozen=True, slots=True)
class ScoreDefinition:
    key: str
    label: str
    meaning: str
    weight: float
    missing_behavior: str


SCORE_DEFINITIONS: tuple[ScoreDefinition, ...] = (
    ScoreDefinition(
        "hook_clarity",
        "Hook clarity",
        "Whether the opening quickly establishes a question, claim, or attention cue.",
        0.10,
        "Score from transcript text; no audio inference.",
    ),
    ScoreDefinition(
        "self_contained_context",
        "Self-contained context",
        "Whether the excerpt begins and ends as a coherent standalone thought.",
        0.12,
        "Penalize incomplete sentence boundaries or context-dependent openings.",
    ),
    ScoreDefinition(
        "informational_value",
        "Informational value",
        "Density of concrete facts, explanations, examples, and distinct terms.",
        0.10,
        "Use lexical evidence only; no unsupported factuality claim.",
    ),
    ScoreDefinition(
        "narrative_payoff",
        "Narrative payoff",
        "Presence of contrast, resolution, or a concluding takeaway within the window.",
        0.08,
        "Use explicit transcript markers; otherwise retain a conservative score.",
    ),
    ScoreDefinition(
        "novelty",
        "Novelty within source",
        "How strongly the excerpt uses terms that are uncommon elsewhere in this transcript.",
        0.07,
        "Compare only with the same transcript; this is not global novelty.",
    ),
    ScoreDefinition(
        "emotional_intensity",
        "Emotional intensity",
        "Explicit emotional wording and punctuation in the transcript.",
        0.06,
        "No facial-expression or vocal-emotion inference is made.",
    ),
    ScoreDefinition(
        "question_answer_structure",
        "Question and answer structure",
        "Whether a question and its answer or explanation are preserved together.",
        0.08,
        "Use punctuation and explanation markers; missing punctuation lowers certainty.",
    ),
    ScoreDefinition(
        "platform_fit",
        "Configured platform fit",
        "How duration and transcript pacing fit the selected editorial profile.",
        0.08,
        "Use configured editorial ranges only; this is not a platform-performance forecast.",
    ),
    ScoreDefinition(
        "topic_relevance",
        "Topic relevance",
        "Overlap with transcript topics and optional operator topic hints.",
        0.10,
        "Use only supplied or transcript-derived topics; exclude when none exist.",
    ),
    ScoreDefinition(
        "speaker_emphasis",
        "Speaker-turn emphasis",
        "Whether available speaker labels indicate a coherent focused turn.",
        0.05,
        "Exclude when diarization labels are unavailable; do not invent audio emphasis.",
    ),
    ScoreDefinition(
        "quotability",
        "Quotability",
        "Conciseness and completeness of a potentially reusable excerpt.",
        0.08,
        "Score textual shape only; it is not a popularity forecast.",
    ),
    ScoreDefinition(
        "audience_relevance",
        "Audience relevance",
        "Explicit practical or second-person relevance, including an operator audience hint.",
        0.05,
        "Use textual overlap only; exclude audience-hint matching when no hint is supplied.",
    ),
    ScoreDefinition(
        "transcript_confidence",
        "Transcript confidence",
        "Reliability of the underlying transcript evidence for this window.",
        0.03,
        "Use provider confidence when present; otherwise mark unavailable and add a risk.",
    ),
)


@dataclass(frozen=True, slots=True)
class ComponentScore:
    key: str
    label: str
    meaning: str
    score: float | None
    weight: float
    weighted_score: float
    applicable: bool
    evidence: list[str]
    missing_behavior: str


@dataclass(frozen=True, slots=True)
class ScoredPlan:
    plan: DiscoveryPlan
    overall_score: float
    recommendation_confidence: float
    transcript_confidence: float | None
    components: list[ComponentScore]
    rationale: str
    topics: list[str]
    risk_flags: list[str]


def score_plan(
    plan: DiscoveryPlan,
    *,
    selected_segments: list[SegmentEvidence],
    corpus_text: str,
    transcript_topics: list[str],
    topic_hints: list[str],
    audience_hint: str | None,
    target_profile: str,
    word_timestamps_available: bool,
    confidence_review_threshold: float,
) -> ScoredPlan:
    text = plan.excerpt
    tokens = _tokens(text)
    folded = text.casefold()
    duration_seconds = plan.duration_ms / 1000
    confidences = [item.confidence for item in selected_segments if item.confidence is not None]
    transcript_confidence = sum(confidences) / len(confidences) if confidences else None
    corpus_counts = Counter(_tokens(corpus_text))
    topic_values = _unique_normalized([*transcript_topics, *topic_hints])
    topic_overlap = [topic for topic in topic_values if topic in folded]
    leading = tokens[0] if tokens else ""

    component_values: dict[str, tuple[float | None, list[str]]] = {
        "hook_clarity": _hook_score(text, tokens),
        "self_contained_context": _context_score(plan, leading, len(tokens)),
        "informational_value": _information_score(tokens, text),
        "narrative_payoff": _payoff_score(folded, plan.sentence_aligned_end),
        "novelty": _novelty_score(tokens, corpus_counts),
        "emotional_intensity": _emotion_score(folded, text),
        "question_answer_structure": _question_answer_score(text, folded),
        "platform_fit": _platform_fit_score(
            len(tokens), duration_seconds, target_profile=target_profile
        ),
        "topic_relevance": _topic_score(topic_values, topic_overlap),
        "speaker_emphasis": _speaker_score(selected_segments),
        "quotability": _quotability_score(plan, len(tokens)),
        "audience_relevance": _audience_score(folded, audience_hint),
        "transcript_confidence": _confidence_score(transcript_confidence),
    }

    components: list[ComponentScore] = []
    weighted_total = 0.0
    applicable_weight = 0.0
    for definition in SCORE_DEFINITIONS:
        score, evidence = component_values[definition.key]
        applicable = score is not None
        weighted = (score or 0.0) * definition.weight
        if applicable:
            weighted_total += weighted
            applicable_weight += definition.weight
        components.append(
            ComponentScore(
                key=definition.key,
                label=definition.label,
                meaning=definition.meaning,
                score=round(score, 3) if score is not None else None,
                weight=definition.weight,
                weighted_score=round(weighted, 3),
                applicable=applicable,
                evidence=evidence[:4],
                missing_behavior=definition.missing_behavior,
            )
        )
    overall = weighted_total / applicable_weight if applicable_weight else 0.0
    alignment_confidence = (
        (1.0 if plan.sentence_aligned_start else 0.65)
        + (1.0 if plan.sentence_aligned_end else 0.65)
    ) / 2
    evidence_confidence = transcript_confidence if transcript_confidence is not None else 0.45
    timestamp_confidence = 1.0 if word_timestamps_available else 0.72
    recommendation_confidence = _clamp(
        evidence_confidence * 0.7 + timestamp_confidence * 0.2 + alignment_confidence * 0.1,
        0,
        1,
    )

    risks: list[str] = []
    if plan.source_is_short:
        risks.append("source_shorter_than_minimum")
    if not word_timestamps_available:
        risks.append("coarse_segment_timestamps")
    if transcript_confidence is None:
        risks.append("transcript_confidence_unavailable")
    elif transcript_confidence < confidence_review_threshold:
        risks.append("low_transcript_confidence")
    if not plan.sentence_aligned_start:
        risks.append("incomplete_sentence_start")
    if not plan.sentence_aligned_end:
        risks.append("incomplete_sentence_end")
    if leading in _LEADING_CONTEXT_WORDS:
        risks.append("context_may_be_missing")
    if target_profile not in _PLATFORM_PROFILES:
        risks.append("unknown_platform_profile_general_fallback")

    top = sorted(
        (item for item in components if item.score is not None),
        key=lambda item: item.weighted_score,
        reverse=True,
    )[:3]
    strengths = ", ".join(f"{item.label.lower()} {item.score:.0f}" for item in top)
    rationale = (
        f"Recommendation score {overall:.1f}/100 is an explainable ranking aid, not a view "
        f"or virality guarantee. Strongest weighted evidence: {strengths or 'none available'}."
    )
    return ScoredPlan(
        plan=plan,
        overall_score=round(overall, 3),
        recommendation_confidence=round(recommendation_confidence, 4),
        transcript_confidence=(
            round(transcript_confidence, 4) if transcript_confidence is not None else None
        ),
        components=components,
        rationale=rationale,
        topics=topic_overlap[:8],
        risk_flags=risks,
    )


def _tokens(text: str) -> list[str]:
    return [item.casefold() for item in _TOKEN_PATTERN.findall(text)]


def _unique_normalized(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(value.casefold().split())[:80].strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _hook_score(text: str, tokens: list[str]) -> tuple[float, list[str]]:
    score = 35.0
    evidence: list[str] = []
    opening = set(tokens[:8])
    if text.lstrip().startswith(("?", "¿")) or "?" in text[:180] or "؟" in text[:180]:
        score += 35
        evidence.append("Opening contains a clear question.")
    overlap = sorted(opening & _HOOK_WORDS)
    if overlap:
        score += 25
        evidence.append(f"Opening cue words: {', '.join(overlap[:3])}.")
    if 8 <= len(tokens) <= 80:
        score += 5
        evidence.append("Opening is concise enough to establish quickly.")
    return _clamp(score, 0, 100), evidence or ["No explicit hook marker was found."]


def _context_score(plan: DiscoveryPlan, leading: str, token_count: int) -> tuple[float, list[str]]:
    score = 40.0
    evidence: list[str] = []
    if plan.sentence_aligned_start:
        score += 25
        evidence.append("Window starts on a sentence boundary.")
    if plan.sentence_aligned_end:
        score += 25
        evidence.append("Window ends on a sentence boundary.")
    if leading in _LEADING_CONTEXT_WORDS:
        score -= 25
        evidence.append("Opening may depend on prior context.")
    if token_count >= 12:
        score += 10
        evidence.append("Window contains enough words for local context.")
    return _clamp(score, 0, 100), evidence


def _information_score(tokens: list[str], text: str) -> tuple[float, list[str]]:
    if not tokens:
        return 0.0, ["No transcript tokens are available."]
    unique_ratio = len(set(tokens)) / len(tokens)
    marker_count = sum(1 for item in tokens if item in _INFORMATION_MARKERS)
    number_count = len(_NUMBER_PATTERN.findall(text))
    score = 25 + unique_ratio * 45 + min(20, marker_count * 7) + min(10, number_count * 5)
    return _clamp(score, 0, 100), [
        f"Lexical diversity is {unique_ratio:.2f}.",
        f"Found {marker_count} explanation markers and {number_count} numeric references.",
    ]


def _payoff_score(folded: str, aligned_end: bool) -> tuple[float, list[str]]:
    markers = sorted(item for item in _PAYOFF_MARKERS if item in folded)
    score = 30 + min(45, len(markers) * 15) + (25 if aligned_end else 0)
    evidence = [f"Resolution/contrast markers: {', '.join(markers[:4])}."] if markers else []
    if aligned_end:
        evidence.append("The excerpt reaches a sentence ending.")
    return _clamp(score, 0, 100), evidence or ["No explicit payoff marker was found."]


def _novelty_score(tokens: list[str], corpus_counts: Counter[str]) -> tuple[float, list[str]]:
    content = [item for item in tokens if len(item) >= 4]
    if not content:
        return 25.0, ["Too few content words for within-source novelty."]
    rare = [item for item in content if corpus_counts[item] <= 1]
    ratio = len(rare) / len(content)
    return _clamp(25 + ratio * 75, 0, 100), [
        f"{len(rare)} of {len(content)} content words are uncommon within this transcript."
    ]


def _emotion_score(folded: str, text: str) -> tuple[float, list[str]]:
    markers = sorted(item for item in _EMOTION_WORDS if item in folded)
    punctuation = text.count("!") + text.count("！")
    score = 25 + min(55, len(markers) * 18) + min(20, punctuation * 7)
    return _clamp(score, 0, 100), [
        f"Found {len(markers)} explicit emotion terms and {punctuation} exclamation marks."
    ]


def _question_answer_score(text: str, folded: str) -> tuple[float, list[str]]:
    question = "?" in text or "؟" in text or "？" in text
    explanation = any(item in folded for item in _INFORMATION_MARKERS | _PAYOFF_MARKERS)
    if question and explanation:
        return 95.0, ["A question and an explanatory or resolving marker occur together."]
    if question:
        return 65.0, ["A question is present, but an explicit answer marker is uncertain."]
    if explanation:
        return 50.0, ["An explanation marker is present without an explicit question."]
    return 25.0, ["No explicit question/answer structure was found."]


def _platform_fit_score(
    token_count: int, duration_seconds: float, *, target_profile: str
) -> tuple[float | None, list[str]]:
    if duration_seconds <= 0:
        return None, ["Duration is unavailable."]
    profile = _PLATFORM_PROFILES.get(target_profile, _PLATFORM_PROFILES["general"])
    minimum, target, maximum = profile
    if duration_seconds < minimum:
        duration_score = 75 - ((minimum - duration_seconds) / minimum) * 55
    elif duration_seconds > maximum:
        duration_score = 75 - ((duration_seconds - maximum) / maximum) * 55
    else:
        span = target - minimum if duration_seconds <= target else maximum - target
        distance = abs(duration_seconds - target) / max(span, 1)
        duration_score = 100 - distance * 25
    words_per_second = token_count / duration_seconds
    pacing_score = _clamp(100 - abs(words_per_second - 2.6) * 35, 20, 100)
    score = _clamp(duration_score * 0.65 + pacing_score * 0.35, 0, 100)
    profile_known = target_profile in _PLATFORM_PROFILES
    profile_label = target_profile if profile_known else "general"
    evidence = [
        f"The {profile_label} editorial profile targets {minimum:.0f}–{maximum:.0f} seconds "
        f"with {target:.0f} seconds preferred; this window is {duration_seconds:.1f} seconds.",
        f"Transcript proxy pacing is {words_per_second:.2f} words/second.",
        "Editorial fit does not predict platform distribution or audience response.",
    ]
    if not profile_known:
        evidence.insert(
            0,
            f"Unknown profile '{target_profile}' used the versioned general-profile fallback.",
        )
    return score, evidence


def _topic_score(topics: list[str], overlap: list[str]) -> tuple[float | None, list[str]]:
    if not topics:
        return None, ["No transcript or operator topic evidence was supplied."]
    ratio = len(overlap) / len(topics)
    matched = ", ".join(overlap[:4]) or "none"
    return _clamp(30 + ratio * 70, 0, 100), [
        f"Matched {len(overlap)} of {len(topics)} topic signals: {matched}."
    ]


def _speaker_score(segments: list[SegmentEvidence]) -> tuple[float | None, list[str]]:
    labels = [item.speaker_label for item in segments if item.speaker_label]
    if not labels:
        return None, ["Speaker labels are unavailable."]
    dominant_count = Counter(labels).most_common(1)[0][1]
    ratio = dominant_count / len(labels)
    return _clamp(40 + ratio * 60, 0, 100), [
        f"The dominant labelled speaker covers {ratio:.0%} of selected segments."
    ]


def _quotability_score(plan: DiscoveryPlan, token_count: int) -> tuple[float, list[str]]:
    target = 55
    length_score = _clamp(100 - abs(token_count - target) * 1.2, 20, 100)
    boundary_bonus = 10 if plan.sentence_aligned_start and plan.sentence_aligned_end else 0
    return _clamp(length_score + boundary_bonus, 0, 100), [
        f"Excerpt contains {token_count} words and boundary completeness was considered."
    ]


def _audience_score(folded: str, audience_hint: str | None) -> tuple[float, list[str]]:
    direct = sorted(item for item in _AUDIENCE_WORDS if item in folded)
    score = 30 + min(35, len(direct) * 12)
    evidence = [f"Direct audience terms: {', '.join(direct[:4])}."] if direct else []
    if audience_hint:
        hints = set(_tokens(audience_hint))
        overlap = hints & set(_tokens(folded))
        score += min(35, len(overlap) * 10)
        evidence.append(f"Audience-hint token overlap: {len(overlap)}.")
    return _clamp(score, 0, 100), evidence or ["No explicit audience cue was found."]


def _confidence_score(value: float | None) -> tuple[float | None, list[str]]:
    if value is None:
        return None, ["Provider confidence is unavailable."]
    return _clamp(value * 100, 0, 100), [f"Mean selected-segment confidence is {value:.3f}."]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    if not math.isfinite(value):
        return minimum
    return max(minimum, min(maximum, value))
