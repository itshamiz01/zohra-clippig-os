from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clipos.core.enums import OpportunitySourceKind, ProviderMode
from clipos.gateways.opportunities.contracts import (
    FieldProvenance,
    OpportunityCapabilities,
    OpportunitySourceContext,
    SourcedOpportunity,
)
from clipos.gateways.opportunities.errors import OpportunityAdapterError
from clipos.gateways.opportunities.normalize import (
    derive_external_key,
    normalize_band,
    normalize_cadence,
    normalize_handle,
    normalize_platform,
    normalize_url,
)
from clipos.opportunities.scoring import AUDIENCE_BANDS, VIEWS_BANDS


class SeedListAdapter:
    """Imports an operator-supplied list of candidate creators/channels.

    Fully local and deterministic: no network, no inference. This is the
    one Phase 8 source whose adapter logic is real-tested end to end.
    The operator is asserting these records; provenance marks every field
    as operator-supplied so nobody mistakes the list for a live scrape.
    """

    kind = OpportunitySourceKind.SEED_LIST
    mode = ProviderMode.REAL_TESTED
    source = "seed_list_adapter"

    def __init__(self) -> None:
        self.capabilities = OpportunityCapabilities(
            kind=self.kind,
            mode=ProviderMode.REAL_TESTED,
            limitations=(
                "Records are operator-supplied assertions, not verified live data.",
                "Imports are strict: one malformed record fails the whole sync.",
            ),
        )

    async def fetch_opportunities(
        self,
        *,
        context: OpportunitySourceContext,
        import_payload: list[dict[str, Any]] | None,
    ) -> list[SourcedOpportunity]:
        del context
        if not import_payload:
            raise OpportunityAdapterError(
                "seed_list_payload_missing",
                "seed list sync requires a non-empty records payload",
                manual_next_step="Send the records to import in the sync request body.",
            )
        fetched_at = datetime.now(UTC)
        return [
            self._record_to_opportunity(record, index=index, fetched_at=fetched_at)
            for index, record in enumerate(import_payload)
        ]

    def _record_to_opportunity(
        self, record: dict[str, Any], *, index: int, fetched_at: datetime
    ) -> SourcedOpportunity:
        if not isinstance(record, dict):
            raise OpportunityAdapterError(
                "seed_list_record_invalid", f"record {index} is not an object"
            )

        def text(name: str, *aliases: str) -> str | None:
            for key in (name, *aliases):
                value = record.get(key)
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise OpportunityAdapterError(
                        "seed_list_record_invalid",
                        f"record {index} field {key!r} must be a string",
                    )
                cleaned = value.strip()
                if cleaned:
                    return cleaned
            return None

        canonical_url = normalize_url(text("canonical_url", "url", "link"))
        creator_handle = normalize_handle(text("creator_handle", "handle"))
        platform = normalize_platform(text("platform"))
        external_key = text("external_key") or derive_external_key(
            canonical_url=canonical_url,
            creator_handle=creator_handle,
            platform=platform,
        )
        title = text("title") or text("creator_name")
        if external_key is None or title is None:
            raise OpportunityAdapterError(
                "seed_list_record_invalid",
                f"record {index} needs a title plus a URL, handle or external_key",
            )
        sample_raw = record.get("sample_item_count", 0)
        if not isinstance(sample_raw, int) or isinstance(sample_raw, bool) or sample_raw < 0:
            raise OpportunityAdapterError(
                "seed_list_record_invalid",
                f"record {index} field 'sample_item_count' must be a non-negative integer",
            )
        compilation_raw = record.get("third_party_compilation", False)
        if isinstance(compilation_raw, str):
            compilation = compilation_raw.strip().casefold() == "true"
        elif isinstance(compilation_raw, bool):
            compilation = compilation_raw
        else:
            raise OpportunityAdapterError(
                "seed_list_record_invalid",
                f"record {index} field 'third_party_compilation' must be a boolean",
            )

        def optional_count(name: str) -> int | None:
            raw_value = record.get(name)
            if raw_value is None:
                return None
            if not isinstance(raw_value, int) or isinstance(raw_value, bool) or raw_value < 0:
                raise OpportunityAdapterError(
                    "seed_list_record_invalid",
                    f"record {index} field {name!r} must be a non-negative integer",
                )
            return raw_value

        hours_raw = record.get("library_long_form_hours")
        if hours_raw is None:
            long_form_hours: float | None = None
        elif (
            isinstance(hours_raw, bool) or not isinstance(hours_raw, (int, float)) or hours_raw < 0
        ):
            raise OpportunityAdapterError(
                "seed_list_record_invalid",
                f"record {index} field 'library_long_form_hours' must be a non-negative number",
            )
        else:
            long_form_hours = float(hours_raw)
        moments_raw = record.get("moments_per_episode")
        if moments_raw is not None and (
            not isinstance(moments_raw, int)
            or isinstance(moments_raw, bool)
            or moments_raw < 1
            or moments_raw > 100
        ):
            raise OpportunityAdapterError(
                "seed_list_record_invalid",
                f"record {index} field 'moments_per_episode' must be an integer in 1..100",
            )

        values: dict[str, Any] = {
            "external_key": external_key,
            "title": title,
            "creator_name": text("creator_name"),
            "creator_handle": creator_handle,
            "canonical_url": canonical_url,
            "platform": platform,
            "contact_route_label": text("contact_route_label", "contact_route"),
            "niche": text("niche"),
            "language": text("language"),
            "audience_band": normalize_band(text("audience_band"), AUDIENCE_BANDS),
            "posting_cadence": normalize_cadence(text("posting_cadence")),
            "sample_item_count": sample_raw,
            "recent_views_band": normalize_band(text("recent_views_band"), VIEWS_BANDS),
            "third_party_compilation": compilation,
            "library_episode_count": optional_count("library_episode_count"),
            "library_long_form_hours": long_form_hours,
            "existing_shorts_count": optional_count("existing_shorts_count"),
            "moments_per_episode": moments_raw,
        }
        provenance = {
            name: FieldProvenance(
                adapter=self.source, fetched_at=fetched_at, source_url=canonical_url
            )
            for name, value in values.items()
            if value is not None and name != "external_key"
        }
        return SourcedOpportunity(
            **values,
            field_provenance=provenance,
            raw=dict(record),
        )

    async def close(self) -> None:
        return None
