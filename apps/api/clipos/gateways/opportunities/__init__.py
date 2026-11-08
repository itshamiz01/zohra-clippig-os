from clipos.gateways.opportunities.contracts import (
    FieldProvenance,
    OpportunityAdapter,
    OpportunityCapabilities,
    OpportunitySourceContext,
    SourcedOpportunity,
)
from clipos.gateways.opportunities.disabled import (
    DisabledOpportunityAdapter,
    DisabledSearchAdapter,
)
from clipos.gateways.opportunities.errors import (
    OpportunityAdapterError,
    UnsupportedOpportunityError,
)
from clipos.gateways.opportunities.factory import (
    OpportunityAdapterRegistry,
    build_opportunity_adapters,
)
from clipos.gateways.opportunities.fake import FakeOpportunityAdapter
from clipos.gateways.opportunities.rss import RssFeedAdapter
from clipos.gateways.opportunities.seed_list import SeedListAdapter

__all__ = [
    "DisabledOpportunityAdapter",
    "DisabledSearchAdapter",
    "FakeOpportunityAdapter",
    "FieldProvenance",
    "OpportunityAdapter",
    "OpportunityAdapterError",
    "OpportunityAdapterRegistry",
    "OpportunityCapabilities",
    "OpportunitySourceContext",
    "RssFeedAdapter",
    "SeedListAdapter",
    "SourcedOpportunity",
    "UnsupportedOpportunityError",
    "build_opportunity_adapters",
]
