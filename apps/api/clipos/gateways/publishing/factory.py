from __future__ import annotations

from collections.abc import Iterable

from clipos.config import Settings
from clipos.core.enums import PublishingProvider
from clipos.gateways.publishing.contracts import PublishingAdapter
from clipos.gateways.publishing.disabled import DisabledPublishingAdapter
from clipos.gateways.publishing.youtube import YouTubePublishingAdapter


class PublishingAdapterRegistry:
    def __init__(self, adapters: Iterable[PublishingAdapter]) -> None:
        self._adapters = {adapter.provider: adapter for adapter in adapters}

    def get(self, provider: PublishingProvider) -> PublishingAdapter:
        return self._adapters[provider]

    def values(self) -> tuple[PublishingAdapter, ...]:
        return tuple(self._adapters.values())

    async def close(self) -> None:
        for adapter in self._adapters.values():
            await adapter.close()


def build_publishing_adapters(settings: Settings) -> PublishingAdapterRegistry:
    adapters: list[PublishingAdapter] = []
    if settings.youtube_publishing_enabled:
        adapters.append(YouTubePublishingAdapter(settings))
    else:
        adapters.append(DisabledPublishingAdapter(PublishingProvider.YOUTUBE))
    adapters.extend(
        DisabledPublishingAdapter(provider)
        for provider in (
            PublishingProvider.META,
            PublishingProvider.TIKTOK,
            PublishingProvider.LINKEDIN,
        )
    )
    return PublishingAdapterRegistry(adapters)
