from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from clipos.core.enums import MetricAvailability, ProviderMode, PublishingProvider

METRIC_DEFINITION_VERSION = "clipos-metrics.v1"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    unit: str
    # Additive metrics can be summed across publications in rollups;
    # averages and percentages must be recombined, never summed.
    additive: bool


CANONICAL_METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition("views", "count", True),
    MetricDefinition("watch_time_seconds", "seconds", True),
    MetricDefinition("average_view_duration_seconds", "seconds", False),
    MetricDefinition("retention_percent", "percent", False),
    MetricDefinition("likes", "count", True),
    MetricDefinition("comments", "count", True),
    MetricDefinition("shares", "count", True),
    MetricDefinition("clicks", "count", True),
    MetricDefinition("conversions", "count", True),
)

CANONICAL_METRIC_NAMES: frozenset[str] = frozenset(item.name for item in CANONICAL_METRICS)
METRIC_UNITS: dict[str, str] = {item.name: item.unit for item in CANONICAL_METRICS}
ADDITIVE_METRICS: frozenset[str] = frozenset(
    item.name for item in CANONICAL_METRICS if item.additive
)


@dataclass(frozen=True, slots=True)
class MetricWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class MetricResult:
    """One metric outcome for one publication.

    ``value`` is set only when ``availability`` is REPORTED or STALE. Every
    other availability state carries ``value=None`` so a metric the provider
    did not report can never be persisted as an invented zero.
    """

    metric: str
    value: float | None
    unit: str
    availability: MetricAvailability
    definition_version: str = METRIC_DEFINITION_VERSION

    def __post_init__(self) -> None:
        has_value = self.value is not None
        expects_value = self.availability in {
            MetricAvailability.REPORTED,
            MetricAvailability.STALE,
        }
        if has_value != expects_value:
            raise ValueError(
                "metric value presence must match its availability state: "
                f"{self.metric} is {self.availability} with value={self.value!r}"
            )


@dataclass(frozen=True, slots=True)
class AnalyticsConnectionContext:
    """What an adapter needs to address a provider destination.

    ``access_token`` is intentionally not populated by the Phase 7 service:
    no real analytics adapter is verified yet, and the disabled/fake
    adapters never use credentials. It exists so the verified YouTube
    adapter can receive a decrypted token through the same contract the
    publishing gateway uses, without a contract change.
    """

    provider: PublishingProvider
    connection_id: UUID | None
    provider_destination_id: str
    granted_scopes: frozenset[str] = frozenset()
    access_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AnalyticsCapabilities:
    provider: PublishingProvider
    mode: ProviderMode
    supported_metrics: tuple[str, ...]
    limitations: tuple[str, ...]


class AnalyticsAdapter(Protocol):
    provider: PublishingProvider
    mode: ProviderMode
    source: str
    capabilities: AnalyticsCapabilities

    async def fetch_publication_metrics(
        self,
        *,
        connection: AnalyticsConnectionContext,
        provider_publication_id: str,
        window: MetricWindow,
    ) -> list[MetricResult]: ...

    async def close(self) -> None: ...
