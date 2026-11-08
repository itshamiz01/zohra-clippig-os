"""Explainable v2.1 content-debt scoring for Phase 8 opportunities.

The Content Debt Score answers a different question from the Phase 8
commercial-fit score (``opportunity-score-v1``, unchanged): *how much
unpublished short-form value is sitting in this creator's existing
long-form library?* It implements the blueprint's seven factors with
their maximum points — long-form hours 25, short-form utilisation gap
25, commercial potential 20, audience size 10, posting consistency 10,
contactability 5, niche compatibility 5 — totalling 100.

Pure and deterministic, like the commercial score. Every input comes
from facts recorded on the opportunity row at import time (or, for
commercial potential, from the already-persisted commercial score
components). A factor whose inputs were never recorded is *unscored*:
it contributes nothing, lowers the reported coverage, and marks the
result partial. Nothing is estimated from nothing — in particular a
creator's library size is never inferred from their audience band.

The unused-moments figure is an estimate by construction and is
labelled as one wherever it surfaces: recorded episodes × an explicit
moments-per-episode assumption (stored on the row when the source
supplied one, otherwise ``DEFAULT_MOMENTS_PER_EPISODE``) − recorded
existing shorts.
"""

from __future__ import annotations

from dataclasses import dataclass

DEBT_SCORE_VERSION = "content-debt-v1"
DEFAULT_MOMENTS_PER_EPISODE = 8

# Editorial defaults, in the same spirit as the commercial score's
# target bands: transparent operator preferences about which niches
# ClipOS is built to clip for, not platform facts.
TARGET_NICHES: frozenset[str] = frozenset(
    {
        "business",
        "education",
        "entrepreneurship",
        "finance",
        "fitness",
        "health",
        "marketing",
        "self_improvement",
        "technology",
    }
)

_AUDIENCE_SIZE: dict[str, float] = {
    "under_10k": 20.0,
    "10k_100k": 45.0,
    "100k_500k": 70.0,
    "500k_1m": 90.0,
    "over_1m": 100.0,
}
_CADENCE_CONSISTENCY: dict[str, float] = {
    "daily": 100.0,
    "multiple_weekly": 85.0,
    "weekly": 70.0,
    "monthly": 40.0,
    "irregular": 20.0,
}


@dataclass(frozen=True, slots=True)
class ContentDebtSignals:
    """The recorded inputs content-debt scoring is allowed to see.

    ``commercial_component_scores`` carries the persisted Phase 8
    component scores (audience_fit / performance_fit) so commercial
    potential derives from recorded evidence instead of re-deriving
    band tables here.
    """

    library_episode_count: int | None
    library_long_form_hours: float | None
    existing_shorts_count: int | None
    moments_per_episode: int
    audience_band: str | None
    posting_cadence: str | None
    contact_route_label: str | None
    niche: str | None
    commercial_component_scores: dict[str, float | None]


@dataclass(frozen=True, slots=True)
class DebtDefinition:
    key: str
    label: str
    meaning: str
    max_points: float
    missing_behavior: str


DEBT_DEFINITIONS: tuple[DebtDefinition, ...] = (
    DebtDefinition(
        "long_form_hours",
        "Long-form hours",
        "How much long-form material the recorded library holds.",
        25.0,
        "Exclude when no long-form hours were recorded; library size is never inferred.",
    ),
    DebtDefinition(
        "utilisation_gap",
        "Short-form utilisation gap",
        "The share of the library's estimated clip potential not yet published as shorts.",
        25.0,
        "Exclude unless both an episode count and an existing-shorts count were recorded.",
    ),
    DebtDefinition(
        "commercial_potential",
        "Commercial potential",
        "Recorded commercial evidence (audience and performance fit) for the library's owner.",
        20.0,
        "Exclude when neither commercial-fit component was scored at import.",
    ),
    DebtDefinition(
        "audience_size",
        "Audience size",
        "The publicly shown audience size band, scored as size (not fit).",
        10.0,
        "Exclude when no audience band was publicly shown; never estimate an audience.",
    ),
    DebtDefinition(
        "posting_consistency",
        "Posting consistency",
        "How regularly the creator publishes long-form content.",
        10.0,
        "Exclude when no posting cadence was observed.",
    ),
    DebtDefinition(
        "contactability",
        "Contactability",
        "Whether a labelled public contact route was recorded for later human-led phases.",
        5.0,
        "A missing route scores zero: the import itself is the observation that none was shown.",
    ),
    DebtDefinition(
        "niche_compatibility",
        "Niche compatibility",
        "Whether the recorded niche is one ClipOS is configured to clip for.",
        5.0,
        "Exclude when no niche was recorded.",
    ),
)


@dataclass(frozen=True, slots=True)
class DebtComponent:
    key: str
    label: str
    meaning: str
    max_points: float
    score: float | None
    points: float
    applicable: bool
    evidence: list[str]
    missing_behavior: str


@dataclass(frozen=True, slots=True)
class ContentDebt:
    score: float | None
    coverage_percent: float
    partial: bool
    components: list[DebtComponent]
    estimated_content_opportunities: int | None
    moments_per_episode: int
    estimate_basis: str | None
    rationale: str
    scoring_version: str = DEBT_SCORE_VERSION


def score_content_debt(signals: ContentDebtSignals) -> ContentDebt:
    component_values: dict[str, tuple[float | None, list[str]]] = {
        "long_form_hours": _hours_score(signals),
        "utilisation_gap": _gap_score(signals),
        "commercial_potential": _commercial_score(signals),
        "audience_size": _audience_score(signals),
        "posting_consistency": _cadence_score(signals),
        "contactability": _contactability_score(signals),
        "niche_compatibility": _niche_score(signals),
    }

    components: list[DebtComponent] = []
    earned = 0.0
    applicable_points = 0.0
    total_points = 0.0
    for definition in DEBT_DEFINITIONS:
        score, evidence = component_values[definition.key]
        applicable = score is not None
        points = (score or 0.0) / 100.0 * definition.max_points if applicable else 0.0
        if applicable:
            earned += points
            applicable_points += definition.max_points
        total_points += definition.max_points
        components.append(
            DebtComponent(
                key=definition.key,
                label=definition.label,
                meaning=definition.meaning,
                max_points=definition.max_points,
                score=round(score, 3) if score is not None else None,
                points=round(points, 3),
                applicable=applicable,
                evidence=evidence[:4],
                missing_behavior=definition.missing_behavior,
            )
        )
    coverage = applicable_points / total_points * 100.0 if total_points else 0.0
    # A headline score needs at least one content factor: contactability
    # is always applicable, and on its own it says nothing about debt —
    # reporting 0.0 there would claim "no debt" from no evidence.
    substantive = any(
        component.applicable and component.key != "contactability" for component in components
    )
    overall = earned / applicable_points * 100.0 if substantive else None
    estimate, estimate_basis = _unused_moments(signals)
    if overall is None:
        rationale = (
            "Content debt cannot be scored: no content factor has recorded inputs. "
            "Record library facts at import to score this opportunity."
        )
    else:
        rationale = (
            f"Content-debt score {overall:.1f}/100 over {coverage:.0f}% of factors with "
            "recorded inputs; it ranks unpublished-library potential only and is not a "
            "revenue or reply forecast."
        )
        if estimate is not None:
            rationale += (
                f" Estimated unused short-form opportunities: {estimate} ({estimate_basis})."
            )
    return ContentDebt(
        score=round(overall, 3) if overall is not None else None,
        coverage_percent=round(coverage, 3),
        partial=coverage < 100.0,
        components=components,
        estimated_content_opportunities=estimate,
        moments_per_episode=signals.moments_per_episode,
        estimate_basis=estimate_basis,
        rationale=rationale,
    )


def _unused_moments(signals: ContentDebtSignals) -> tuple[int | None, str | None]:
    if signals.library_episode_count is None or signals.existing_shorts_count is None:
        return None, None
    potential = signals.library_episode_count * signals.moments_per_episode
    estimate = max(potential - signals.existing_shorts_count, 0)
    basis = (
        f"{signals.library_episode_count} recorded episodes × "
        f"{signals.moments_per_episode} assumed moments per episode − "
        f"{signals.existing_shorts_count} recorded existing shorts"
    )
    return estimate, basis


def _hours_score(signals: ContentDebtSignals) -> tuple[float | None, list[str]]:
    hours = signals.library_long_form_hours
    if hours is None:
        return None, ["No long-form hours were recorded for this library."]
    if hours < 0:
        return None, ["Recorded long-form hours are negative and cannot be scored."]
    # 200 recorded hours saturate the factor; the scale is linear below it.
    score = min(hours * 0.5, 100.0)
    return score, [f"Library holds {hours:g} recorded long-form hours."]


def _gap_score(signals: ContentDebtSignals) -> tuple[float | None, list[str]]:
    episodes = signals.library_episode_count
    shorts = signals.existing_shorts_count
    if episodes is None or shorts is None:
        return None, [
            "Utilisation gap needs both a recorded episode count and a recorded "
            "existing-shorts count."
        ]
    potential = episodes * signals.moments_per_episode
    if potential <= 0:
        return None, ["Recorded episode count is zero; there is no library to under-use."]
    utilisation = min(shorts / potential, 1.0)
    score = (1.0 - utilisation) * 100.0
    return score, [
        f"{shorts} recorded shorts against an estimated {potential} moments "
        f"({episodes} episodes × {signals.moments_per_episode} assumed per episode): "
        f"{utilisation * 100:.0f}% utilised."
    ]


def _commercial_score(signals: ContentDebtSignals) -> tuple[float | None, list[str]]:
    parts: list[tuple[str, float]] = []
    for key, label in (("audience_fit", "audience fit"), ("performance_fit", "performance fit")):
        value = signals.commercial_component_scores.get(key)
        if value is not None:
            parts.append((label, value))
    if not parts:
        return None, ["No commercial-fit component was scored at import."]
    score = sum(value for _label, value in parts) / len(parts)
    evidence = ", ".join(f"{label} {value:.0f}" for label, value in parts)
    return score, [f"Derived from recorded commercial-fit evidence: {evidence}."]


def _audience_score(signals: ContentDebtSignals) -> tuple[float | None, list[str]]:
    band = signals.audience_band
    if band is None:
        return None, ["No audience band was publicly shown for this opportunity."]
    score = _AUDIENCE_SIZE.get(band)
    if score is None:
        return None, [f"Audience band {band!r} is not a recognised size band; factor excluded."]
    return score, [f"Publicly shown audience band {band} scores {score:.0f} as size."]


def _cadence_score(signals: ContentDebtSignals) -> tuple[float | None, list[str]]:
    cadence = signals.posting_cadence
    if cadence is None:
        return None, ["Posting cadence was not observed."]
    score = _CADENCE_CONSISTENCY.get(cadence, 20.0)
    return score, [f"Posting cadence is {cadence}."]


def _contactability_score(signals: ContentDebtSignals) -> tuple[float, list[str]]:
    if signals.contact_route_label:
        return 100.0, [
            f"A public contact route is labelled: {signals.contact_route_label}. "
            "The label is recorded for later human-led phases; nothing contacts anyone."
        ]
    return 0.0, ["No public contact route was recorded on the source record."]


def _niche_score(signals: ContentDebtSignals) -> tuple[float | None, list[str]]:
    niche = signals.niche
    if niche is None:
        return None, ["No niche was recorded for this opportunity."]
    if niche.strip().casefold() in TARGET_NICHES:
        return 100.0, [f"Niche {niche} is one of the configured target niches."]
    return 60.0, [f"Niche {niche} is recorded but outside the configured target niches."]
