"""Explainable Phase 8 opportunity scoring (commercial/content fit).

Pure and deterministic: the same normalised record always produces the
same components and overall score under ``opportunity-score-v1``, so a
score can be reproduced and audited long after import. Scores are a
ranking aid for human reviewers — not a prediction of views, revenue or
reply likelihood, and never a substitute for rights or legal review.
"""

from __future__ import annotations

from dataclasses import dataclass

SCORE_VERSION = "opportunity-score-v1"

AUDIENCE_BANDS: frozenset[str] = frozenset(
    {"under_10k", "10k_100k", "100k_500k", "500k_1m", "over_1m"}
)
VIEWS_BANDS: frozenset[str] = frozenset({"under_1k", "1k_10k", "10k_100k", "over_100k"})

# Editorial target bands. These are transparent operator preferences for
# the kind of creator ClipOS is built to clip for, not platform facts.
_AUDIENCE_FIT: dict[str, float] = {
    "under_10k": 45.0,
    "10k_100k": 85.0,
    "100k_500k": 95.0,
    "500k_1m": 80.0,
    "over_1m": 55.0,
}
_VIEWS_FIT: dict[str, float] = {
    "under_1k": 40.0,
    "1k_10k": 65.0,
    "10k_100k": 85.0,
    "over_100k": 75.0,
}
_KNOWN_PLATFORMS = {"youtube", "podcast", "vimeo", "twitch"}
_ACTIVE_CADENCES = {"daily", "multiple_weekly", "weekly"}


@dataclass(frozen=True, slots=True)
class OpportunitySignals:
    """The normalised inputs scoring is allowed to see.

    Everything here is either a normalised opportunity field or a signal
    stored in the opportunity's evidence at import time, so rescoring a
    stored row needs nothing beyond the row itself.
    """

    platform: str | None
    niche: str | None
    language: str | None
    audience_band: str | None
    posting_cadence: str | None
    contact_route_label: str | None
    sample_item_count: int
    recent_views_band: str | None
    third_party_compilation: bool


@dataclass(frozen=True, slots=True)
class ScoreDefinition:
    key: str
    label: str
    meaning: str
    weight: float
    missing_behavior: str


SCORE_DEFINITIONS: tuple[ScoreDefinition, ...] = (
    ScoreDefinition(
        "content_opportunity",
        "Content opportunity",
        "Whether the source shows an active body of clip-able content to work from.",
        0.24,
        "Score from observed sample items and cadence only; no content is fetched or judged.",
    ),
    ScoreDefinition(
        "audience_fit",
        "Audience fit (commercial)",
        "How the publicly shown audience size band fits the configured target bands.",
        0.20,
        "Exclude when no audience band was publicly shown; never estimate an audience.",
    ),
    ScoreDefinition(
        "performance_fit",
        "Performance fit",
        "How publicly shown recent-view bands compare with the target engagement range.",
        0.16,
        "Exclude when no recent-view band was observed; no analytics are inferred.",
    ),
    ScoreDefinition(
        "contactability",
        "Contactability",
        "Whether a labelled public contact route exists for later human-led phases.",
        0.14,
        "A missing route scores low; the route is a label only and is never used by Phase 8.",
    ),
    ScoreDefinition(
        "rights_feasibility",
        "Rights feasibility",
        "A conservative heuristic for how plausible a future rights conversation is.",
        0.14,
        "Heuristic only; it is not a rights determination or legal advice.",
    ),
    ScoreDefinition(
        "effort_inverse",
        "Effort (inverse)",
        "How complete the normalised record is; completeness means less manual research.",
        0.12,
        "Score from field completeness only; missing fields lower the score, never block.",
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
class ScoredOpportunity:
    overall_score: float
    components: list[ComponentScore]
    rationale: str


def score_opportunity(signals: OpportunitySignals) -> ScoredOpportunity:
    component_values: dict[str, tuple[float | None, list[str]]] = {
        "content_opportunity": _content_score(signals),
        "audience_fit": _band_score(
            signals.audience_band,
            _AUDIENCE_FIT,
            "Publicly shown audience band {band} scores {score:.0f} against target bands.",
            "No audience band was publicly shown for this opportunity.",
        ),
        "performance_fit": _band_score(
            signals.recent_views_band,
            _VIEWS_FIT,
            "Publicly shown recent views band {band} scores {score:.0f} against target range.",
            "No recent-view band was observed for this opportunity.",
        ),
        "contactability": _contactability_score(signals),
        "rights_feasibility": _rights_score(signals),
        "effort_inverse": _effort_score(signals),
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
    top = sorted(
        (item for item in components if item.score is not None),
        key=lambda item: item.weighted_score,
        reverse=True,
    )[:3]
    strengths = ", ".join(f"{item.label.lower()} {item.score:.0f}" for item in top)
    rationale = (
        f"Commercial-fit score {overall:.1f}/100 is an explainable ranking aid, not a "
        f"revenue, reach or reply forecast. Strongest weighted evidence: "
        f"{strengths or 'none available'}."
    )
    return ScoredOpportunity(
        overall_score=round(overall, 3), components=components, rationale=rationale
    )


def _content_score(signals: OpportunitySignals) -> tuple[float, list[str]]:
    evidence: list[str] = []
    score = 30.0 + min(max(signals.sample_item_count, 0), 5) * 8.0
    if signals.sample_item_count > 0:
        evidence.append(f"{signals.sample_item_count} sample item(s) observed at import.")
    else:
        evidence.append("No sample items were observed at import.")
    if signals.posting_cadence in _ACTIVE_CADENCES:
        score += 20.0
        evidence.append(f"Posting cadence is {signals.posting_cadence}.")
    elif signals.posting_cadence == "monthly":
        score += 8.0
        evidence.append("Posting cadence is monthly.")
    elif signals.posting_cadence:
        evidence.append(f"Posting cadence is {signals.posting_cadence}.")
    else:
        evidence.append("Posting cadence was not observed.")
    return min(score, 100.0), evidence


def _band_score(
    band: str | None,
    table: dict[str, float],
    present_template: str,
    missing_evidence: str,
) -> tuple[float | None, list[str]]:
    if band is None:
        return None, [missing_evidence]
    score = table.get(band)
    if score is None:
        return None, [f"Band {band!r} is not a recognised target band; factor excluded."]
    return score, [present_template.format(band=band, score=score)]


def _contactability_score(signals: OpportunitySignals) -> tuple[float, list[str]]:
    if signals.contact_route_label:
        return 85.0, [
            f"A public contact route is labelled: {signals.contact_route_label}. "
            "The label is recorded for later human-led phases; Phase 8 never uses it."
        ]
    return 15.0, ["No public contact route was recorded; contact would need manual research."]


def _rights_score(signals: OpportunitySignals) -> tuple[float, list[str]]:
    if signals.third_party_compilation:
        return 20.0, [
            "Source material is flagged as third-party compilation; rights feasibility is low "
            "and a human rights review would be required before any use."
        ]
    platform = (signals.platform or "").strip().lower()
    if platform in _KNOWN_PLATFORMS:
        return 70.0, [
            f"Platform {platform} hosts the creator's own channel content; a rights "
            "conversation is plausible but has not happened and is not assessed here."
        ]
    if not platform:
        return 45.0, ["Platform is unknown; rights feasibility cannot be assessed further."]
    return 55.0, [f"Platform {platform} is outside the reviewed set; feasibility is uncertain."]


def _effort_score(signals: OpportunitySignals) -> tuple[float, list[str]]:
    fields = {
        "platform": signals.platform,
        "niche": signals.niche,
        "language": signals.language,
        "audience_band": signals.audience_band,
        "posting_cadence": signals.posting_cadence,
        "contact_route_label": signals.contact_route_label,
    }
    present = [name for name, value in fields.items() if value]
    missing = [name for name, value in fields.items() if not value]
    score = round(len(present) / len(fields) * 100.0, 3)
    evidence = [f"Normalised record carries {len(present)} of {len(fields)} research fields."]
    if missing:
        evidence.append(f"Still missing: {', '.join(missing)}.")
    return score, evidence
