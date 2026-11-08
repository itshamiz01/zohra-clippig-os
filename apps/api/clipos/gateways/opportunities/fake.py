from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from clipos.core.enums import OpportunitySourceKind, ProviderMode
from clipos.gateways.opportunities.contracts import (
    FieldProvenance,
    OpportunityCapabilities,
    OpportunitySourceContext,
    SourcedOpportunity,
)


class FakeOpportunityAdapter:
    """Deterministic in-memory opportunity double for tests and demos.

    This adapter is MOCKED evidence only. Records are derived from a hash
    of the source name so runs are reproducible; they are labelled with
    source ``fake_opportunity_adapter`` in every provenance entry and are
    never provider or web evidence.
    """

    mode = ProviderMode.MOCKED
    source = "fake_opportunity_adapter"

    def __init__(self, kind: OpportunitySourceKind) -> None:
        self.kind = kind
        self.capabilities = OpportunityCapabilities(
            kind=kind,
            mode=ProviderMode.MOCKED,
            limitations=(
                "Deterministic fake records for tests and local development only; "
                "never discovery evidence.",
            ),
        )
        self.closed = False

    async def fetch_opportunities(
        self,
        *,
        context: OpportunitySourceContext,
        import_payload: list[dict[str, Any]] | None,
    ) -> list[SourcedOpportunity]:
        del import_payload
        fetched_at = datetime.now(UTC)
        digest = hashlib.sha256(f"{self.kind.value}:{context.name}".encode()).digest()
        count = 2 + digest[0] % 2
        records: list[SourcedOpportunity] = []
        for index in range(count):
            handle = f"fake-creator-{index + 1}"
            canonical_url = f"https://example.com/@{handle}"
            values: dict[str, Any] = {
                "external_key": f"fake:{self.kind.value}:{handle}",
                "title": f"Fake Creator {index + 1}",
                "creator_name": f"Fake Creator {index + 1}",
                "creator_handle": handle,
                "canonical_url": canonical_url,
                "platform": "youtube",
                "contact_route_label": "business email on channel About page",
                "niche": "education",
                "language": "en",
                "audience_band": "100k_500k",
                "posting_cadence": "weekly",
                "sample_item_count": 4,
                "recent_views_band": "10k_100k",
                "third_party_compilation": False,
            }
            provenance = {
                name: FieldProvenance(
                    adapter=self.source, fetched_at=fetched_at, source_url=canonical_url
                )
                for name, value in values.items()
                if value is not None and name != "external_key"
            }
            records.append(
                SourcedOpportunity(**values, field_provenance=provenance, raw=dict(values))
            )
        return records

    async def close(self) -> None:
        self.closed = True
