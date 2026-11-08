from __future__ import annotations

from collections.abc import Iterable

from clipos.config import Settings
from clipos.core.enums import PublishingProvider
from clipos.gateways.analytics.contracts import AnalyticsAdapter
from clipos.gateways.analytics.disabled import DisabledAnalyticsAdapter
from clipos.gateways.analytics.fake import FakeAnalyticsAdapter
from clipos.gateways.analytics.youtube import YouTubeAnalyticsAdapter


class AnalyticsAdapterRegistry:
    def __init__(self, adapters: Iterable[AnalyticsAdapter]) -> None:
        self._adapters: dict[PublishingProvider, AnalyticsAdapter] = {}
        for adapter in adapters:
            if adapter.provider in self._adapters:
                raise ValueError(f"duplicate analytics adapter for {adapter.provider}")
            self._adapters[adapter.provider] = adapter

    def get(self, provider: PublishingProvider) -> AnalyticsAdapter:
        try:
            return self._adapters[provider]
        except KeyError:
            return DisabledAnalyticsAdapter(provider)

    def providers(self) -> tuple[PublishingProvider, ...]:
        return tuple(self._adapters)

    async def close(self) -> None:
        for adapter in self._adapters.values():
            await adapter.close()


def build_analytics_adapters(settings: Settings) -> AnalyticsAdapterRegistry:
    """Build the per-provider analytics adapters from configuration.

    Fail-closed defaults mirror publishing: without an explicit opt-in the
    YouTube adapter is disabled (not merely unverified), and the fake
    adapter is selected only when ``CLIPOS_ANALYTICS_PROVIDER=fake``.
    """
    adapters: list[AnalyticsAdapter] = []
    for provider in PublishingProvider:
        if provider is PublishingProvider.YOUTUBE and settings.youtube_analytics_enabled:
            adapters.append(YouTubeAnalyticsAdapter(settings))
        elif settings.analytics_provider == "fake":
            adapters.append(FakeAnalyticsAdapter(provider))
        else:
            adapters.append(DisabledAnalyticsAdapter(provider))
    return AnalyticsAdapterRegistry(adapters)
