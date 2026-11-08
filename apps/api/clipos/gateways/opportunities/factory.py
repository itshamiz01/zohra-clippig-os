from __future__ import annotations

from collections.abc import Iterable

from clipos.config import Settings
from clipos.core.enums import OpportunitySourceKind
from clipos.gateways.opportunities.contracts import OpportunityAdapter
from clipos.gateways.opportunities.disabled import (
    DisabledOpportunityAdapter,
    DisabledSearchAdapter,
)
from clipos.gateways.opportunities.fake import FakeOpportunityAdapter
from clipos.gateways.opportunities.rss import RssFeedAdapter
from clipos.gateways.opportunities.seed_list import SeedListAdapter


class OpportunityAdapterRegistry:
    def __init__(self, adapters: Iterable[OpportunityAdapter]) -> None:
        self._adapters: dict[OpportunitySourceKind, OpportunityAdapter] = {}
        for adapter in adapters:
            if adapter.kind in self._adapters:
                raise ValueError(f"duplicate opportunity adapter for {adapter.kind}")
            self._adapters[adapter.kind] = adapter

    def get(self, kind: OpportunitySourceKind) -> OpportunityAdapter:
        try:
            return self._adapters[kind]
        except KeyError:
            return DisabledOpportunityAdapter(kind)

    def kinds(self) -> tuple[OpportunitySourceKind, ...]:
        return tuple(self._adapters)

    async def close(self) -> None:
        for adapter in self._adapters.values():
            await adapter.close()


def build_opportunity_adapters(settings: Settings) -> OpportunityAdapterRegistry:
    """Build the per-kind opportunity adapters from configuration.

    Fail-closed defaults: seed lists are local and always available; RSS
    requires an explicit operator opt-in because it performs outbound
    fetches; public-site scraping does not exist; search providers are
    disabled unless the explicitly-mocked fake is selected for tests.
    """
    adapters: list[OpportunityAdapter] = [SeedListAdapter()]
    if settings.opportunity_rss_enabled:
        adapters.append(RssFeedAdapter(settings))
    else:
        adapters.append(DisabledOpportunityAdapter(OpportunitySourceKind.RSS_FEED))
    adapters.append(DisabledOpportunityAdapter(OpportunitySourceKind.PUBLIC_SITE))
    if settings.opportunity_provider == "fake":
        adapters.append(FakeOpportunityAdapter(OpportunitySourceKind.SEARCH_PROVIDER))
    else:
        adapters.append(DisabledSearchAdapter())
    return OpportunityAdapterRegistry(adapters)
