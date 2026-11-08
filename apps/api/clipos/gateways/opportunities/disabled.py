from __future__ import annotations

from typing import Any

from clipos.core.enums import OpportunitySourceKind, ProviderMode
from clipos.gateways.opportunities.contracts import (
    OpportunityCapabilities,
    OpportunitySourceContext,
    SourcedOpportunity,
)
from clipos.gateways.opportunities.errors import UnsupportedOpportunityError

_LIMITATIONS: dict[OpportunitySourceKind, tuple[str, ...]] = {
    OpportunitySourceKind.SEARCH_PROVIDER: (
        "No search provider is integrated; discovery APIs require their own "
        "reviewed adapter, credentials and provider test.",
        "Search-based discovery stays disabled by default and reports "
        "unavailable truthfully instead of fabricating results.",
    ),
    OpportunitySourceKind.PUBLIC_SITE: (
        "Public-site scraping is not implemented: robots policy, site terms "
        "and per-site authorisation must be reviewed before any adapter exists.",
    ),
    OpportunitySourceKind.RSS_FEED: (
        "RSS fetching is disabled until the operator enables it "
        "(CLIPOS_OPPORTUNITY_RSS_ENABLED=true).",
    ),
    OpportunitySourceKind.SEED_LIST: ("Seed list imports are handled by the seed adapter.",),
}


class DisabledOpportunityAdapter:
    """Truthful default for source kinds without a verified adapter.

    A sync against this adapter fails the run with
    ``opportunity_unsupported`` and records nothing, rather than
    pretending to discover opportunities.
    """

    mode = ProviderMode.DISABLED
    source = "disabled_opportunity_adapter"

    def __init__(self, kind: OpportunitySourceKind) -> None:
        self.kind = kind
        self.capabilities = OpportunityCapabilities(
            kind=kind,
            mode=ProviderMode.DISABLED,
            limitations=_LIMITATIONS[kind],
        )

    async def fetch_opportunities(
        self,
        *,
        context: OpportunitySourceContext,
        import_payload: list[dict[str, Any]] | None,
    ) -> list[SourcedOpportunity]:
        del context, import_payload
        raise UnsupportedOpportunityError(
            f"opportunity discovery is disabled for {self.kind.value} sources in this build"
        )

    async def close(self) -> None:
        return None


class DisabledSearchAdapter(DisabledOpportunityAdapter):
    """Search-provider sources are disabled by default (Phase 8 boundary)."""

    source = "disabled_search_adapter"

    def __init__(self) -> None:
        super().__init__(OpportunitySourceKind.SEARCH_PROVIDER)
