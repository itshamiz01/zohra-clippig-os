from __future__ import annotations

import hashlib

from clipos.core.enums import MetricAvailability, ProviderMode, PublishingProvider
from clipos.gateways.analytics.contracts import (
    CANONICAL_METRIC_NAMES,
    METRIC_UNITS,
    AnalyticsCapabilities,
    AnalyticsConnectionContext,
    MetricResult,
    MetricWindow,
)

# Metrics the fake provider contract never exposes. They are reported as
# MISSING (value None), which is exactly the missing-vs-zero distinction
# the analytics store exists to preserve.
FAKE_MISSING_METRICS = frozenset({"conversions"})


class FakeAnalyticsAdapter:
    """Deterministic in-memory analytics double for tests and local demos.

    This adapter is MOCKED evidence only. Values are derived from a hash
    of the provider publication id so runs are reproducible; they are not
    provider data and are labelled with source ``fake_analytics_adapter``
    on every persisted snapshot.
    """

    mode = ProviderMode.MOCKED
    source = "fake_analytics_adapter"

    def __init__(
        self,
        provider: PublishingProvider,
        *,
        permission_denied_ids: frozenset[str] = frozenset(),
        stale_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.provider = provider
        self.permission_denied_ids = permission_denied_ids
        self.stale_ids = stale_ids
        self.capabilities = AnalyticsCapabilities(
            provider=provider,
            mode=ProviderMode.MOCKED,
            supported_metrics=tuple(sorted(CANONICAL_METRIC_NAMES - FAKE_MISSING_METRICS)),
            limitations=(
                "Deterministic fake metrics for tests and local development only; "
                "never provider evidence.",
                "The fake provider contract does not expose conversions.",
            ),
        )
        self.closed = False

    async def fetch_publication_metrics(
        self,
        *,
        connection: AnalyticsConnectionContext,
        provider_publication_id: str,
        window: MetricWindow,
    ) -> list[MetricResult]:
        del connection, window
        if provider_publication_id in self.permission_denied_ids:
            return [
                MetricResult(
                    metric=name,
                    value=None,
                    unit=METRIC_UNITS[name],
                    availability=MetricAvailability.PERMISSION_DENIED,
                )
                for name in sorted(CANONICAL_METRIC_NAMES)
            ]
        digest = hashlib.sha256(provider_publication_id.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        views = 250 + seed % 24_750
        average_duration = 12.0 + (seed % 460) / 10.0
        values: dict[str, float] = {
            "views": float(views),
            "watch_time_seconds": round(views * average_duration, 1),
            "average_view_duration_seconds": average_duration,
            "retention_percent": round(25.0 + (seed % 600) / 10.0, 1),
            "likes": float(int(views * 0.04)),
            "comments": float(int(views * 0.006)),
            "shares": float(int(views * 0.008)),
            "clicks": float(int(views * 0.015)),
        }
        availability = (
            MetricAvailability.STALE
            if provider_publication_id in self.stale_ids
            else MetricAvailability.REPORTED
        )
        results: list[MetricResult] = []
        for name in sorted(CANONICAL_METRIC_NAMES):
            if name in FAKE_MISSING_METRICS:
                results.append(
                    MetricResult(
                        metric=name,
                        value=None,
                        unit=METRIC_UNITS[name],
                        availability=MetricAvailability.MISSING,
                    )
                )
            else:
                results.append(
                    MetricResult(
                        metric=name,
                        value=values[name],
                        unit=METRIC_UNITS[name],
                        availability=availability,
                    )
                )
        return results

    async def close(self) -> None:
        self.closed = True
