from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from clipos.core.enums import OpportunitySourceKind, ProviderMode


@dataclass(frozen=True, slots=True)
class FieldProvenance:
    """Where one normalised field came from.

    Every field an adapter emits carries the adapter name, the fetch time
    and the source URL it was read from, so a stored opportunity can be
    traced back to its origin field by field.
    """

    adapter: str
    fetched_at: datetime
    source_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "fetched_at": self.fetched_at.isoformat(),
            "source_url": self.source_url,
        }


@dataclass(frozen=True, slots=True)
class SourcedOpportunity:
    """One normalised opportunity record emitted by a source adapter.

    ``contact_route_label`` is a human-readable label of a *public*
    contact route (for example "business email on channel About page").
    Adapters must never place scraped private contact data — addresses,
    phone numbers, handles for direct messaging — in any field.
    """

    external_key: str
    title: str
    creator_name: str | None = None
    creator_handle: str | None = None
    canonical_url: str | None = None
    platform: str | None = None
    contact_route_label: str | None = None
    niche: str | None = None
    language: str | None = None
    audience_band: str | None = None
    posting_cadence: str | None = None
    sample_item_count: int = 0
    recent_views_band: str | None = None
    third_party_compilation: bool = False
    field_provenance: dict[str, FieldProvenance] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OpportunitySourceContext:
    source_id: UUID
    tenant_id: UUID
    kind: OpportunitySourceKind
    name: str
    config: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OpportunityCapabilities:
    kind: OpportunitySourceKind
    mode: ProviderMode
    limitations: tuple[str, ...]


class OpportunityAdapter(Protocol):
    kind: OpportunitySourceKind
    mode: ProviderMode
    source: str
    capabilities: OpportunityCapabilities

    async def fetch_opportunities(
        self,
        *,
        context: OpportunitySourceContext,
        import_payload: list[dict[str, Any]] | None,
    ) -> list[SourcedOpportunity]: ...

    async def close(self) -> None: ...
