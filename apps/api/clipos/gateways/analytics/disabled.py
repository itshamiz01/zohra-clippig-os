from __future__ import annotations

from clipos.core.enums import MetricAvailability, ProviderMode, PublishingProvider
from clipos.gateways.analytics.contracts import (
    CANONICAL_METRICS,
    AnalyticsCapabilities,
    AnalyticsConnectionContext,
    MetricResult,
    MetricWindow,
)


class DisabledAnalyticsAdapter:
    """Default adapter: reports every canonical metric as unavailable.

    Collection still runs through this adapter so the system records, per
    publication and metric, that analytics were not obtainable instead of
    silently showing nothing or inventing zeros.
    """

    mode = ProviderMode.DISABLED
    source = "disabled_adapter"

    def __init__(self, provider: PublishingProvider) -> None:
        self.provider = provider
        self.capabilities = AnalyticsCapabilities(
            provider=provider,
            mode=ProviderMode.DISABLED,
            supported_metrics=(),
            limitations=(
                "Analytics collection is disabled for this provider; every metric is "
                "recorded as unavailable, never as zero.",
            ),
        )

    async def fetch_publication_metrics(
        self,
        *,
        connection: AnalyticsConnectionContext,
        provider_publication_id: str,
        window: MetricWindow,
    ) -> list[MetricResult]:
        del connection, provider_publication_id, window
        return [
            MetricResult(
                metric=definition.name,
                value=None,
                unit=definition.unit,
                availability=MetricAvailability.UNAVAILABLE,
            )
            for definition in CANONICAL_METRICS
        ]

    async def close(self) -> None:
        return None
