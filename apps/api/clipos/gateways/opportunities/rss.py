from __future__ import annotations

import statistics
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from clipos.config import Settings
from clipos.core.enums import OpportunitySourceKind, ProviderMode
from clipos.core.ssrf import PublicFetchError, Resolver, default_resolver, fetch_public_url
from clipos.gateways.opportunities.contracts import (
    FieldProvenance,
    OpportunityCapabilities,
    OpportunitySourceContext,
    SourcedOpportunity,
)
from clipos.gateways.opportunities.errors import OpportunityAdapterError
from clipos.gateways.opportunities.normalize import (
    derive_external_key,
    normalize_cadence,
    normalize_url,
    url_host,
)

_FEED_CONTENT_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
        "application/rdf+xml",
        "text/plain",
    }
)
_ATOM = "{http://www.w3.org/2005/Atom}"


class RssFeedAdapter:
    """Reads one configured RSS/Atom feed as one channel opportunity.

    The feed itself is the opportunity (a podcast or channel publishing
    full episodes); its items are content evidence — titles, dates and
    cadence — never downloaded media. Every fetch goes through the SSRF
    guard in ``clipos.core.ssrf``.

    Truth state: the parsing and normalisation logic is exercised by
    tests through a fake transport, but no live feed fetch has been
    performed from this codebase, so the adapter reports ``unverified``
    and is only built when the operator explicitly enables RSS
    (``CLIPOS_OPPORTUNITY_RSS_ENABLED=true``).
    """

    kind = OpportunitySourceKind.RSS_FEED
    mode = ProviderMode.UNVERIFIED
    source = "rss_feed_adapter"

    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver = default_resolver,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.client = client
        self.capabilities = OpportunityCapabilities(
            kind=self.kind,
            mode=ProviderMode.UNVERIFIED,
            limitations=(
                "Feed parsing is test-proven via a fake transport; no live feed "
                "fetch has been performed, so the network path is unverified.",
                "One feed maps to one opportunity; items are evidence, not media.",
                "All fetches pass the SSRF guard (public addresses only, "
                "guarded redirects, byte and content-type caps).",
            ),
        )

    async def fetch_opportunities(
        self,
        *,
        context: OpportunitySourceContext,
        import_payload: list[dict[str, Any]] | None,
    ) -> list[SourcedOpportunity]:
        del import_payload
        feed_url = context.config.get("feed_url")
        if not isinstance(feed_url, str) or not feed_url.strip():
            raise OpportunityAdapterError(
                "rss_feed_url_missing",
                "RSS source has no feed_url in its configuration",
                manual_next_step="Set the source config feed_url to the public feed address.",
            )
        feed_url = feed_url.strip()
        try:
            fetched = await fetch_public_url(
                feed_url,
                resolver=self.resolver,
                client=self.client,
                timeout_seconds=self.settings.opportunity_fetch_timeout_seconds,
                max_bytes=self.settings.opportunity_fetch_max_bytes,
                max_redirects=self.settings.opportunity_fetch_max_redirects,
                allowed_content_types=_FEED_CONTENT_TYPES,
            )
        except PublicFetchError as exc:
            raise OpportunityAdapterError(
                exc.category,
                exc.safe_message,
                retryable=exc.retryable,
                manual_next_step="Check the feed URL is public and reachable, then retry.",
            ) from exc
        return [self._parse_feed(fetched.body, feed_url=feed_url)]

    def _parse_feed(self, body: bytes, *, feed_url: str) -> SourcedOpportunity:
        try:
            root = ET.fromstring(body)  # noqa: S314 - feed XML is data; DTDs are not resolved
        except ET.ParseError as exc:
            raise OpportunityAdapterError(
                "rss_parse_failed",
                "feed body is not well-formed XML",
                manual_next_step="Confirm the URL serves an RSS or Atom feed.",
            ) from exc
        fetched_at = datetime.now(UTC)
        if root.tag == "rss":
            channel = root.find("channel")
            if channel is None:
                raise OpportunityAdapterError(
                    "rss_parse_failed", "RSS document has no channel element"
                )
            title = _child_text(channel, "title")
            link = _child_text(channel, "link")
            language = _child_text(channel, "language")
            items = channel.findall("item")
            item_count = len(items)
            item_titles = [_child_text(item, "title") for item in items]
            item_dates = [
                parsed
                for item in items
                if (parsed := _parse_rfc822(_child_text(item, "pubDate"))) is not None
            ]
        elif root.tag == f"{_ATOM}feed":
            title = _child_text(root, f"{_ATOM}title")
            link = None
            for link_el in root.findall(f"{_ATOM}link"):
                if link_el.get("rel") in {None, "alternate"}:
                    link = link_el.get("href")
                    break
            language = root.get("{http://www.w3.org/XML/1998/namespace}lang")
            entries = root.findall(f"{_ATOM}entry")
            item_count = len(entries)
            item_titles = [_child_text(entry, f"{_ATOM}title") for entry in entries]
            item_dates = [
                parsed
                for entry in entries
                if (
                    parsed := _parse_iso(
                        _child_text(entry, f"{_ATOM}published")
                        or _child_text(entry, f"{_ATOM}updated")
                    )
                )
                is not None
            ]
        else:
            raise OpportunityAdapterError(
                "rss_parse_failed",
                f"document root {root.tag!r} is neither RSS nor Atom",
            )
        if not title:
            raise OpportunityAdapterError(
                "rss_parse_failed", "feed carries no channel title to identify the opportunity"
            )
        canonical_url = normalize_url(link) or normalize_url(feed_url)
        external_key = derive_external_key(
            canonical_url=normalize_url(feed_url), creator_handle=None, platform="podcast"
        )
        if external_key is None:  # pragma: no cover - feed_url was fetchable, so it parses
            raise OpportunityAdapterError(
                "rss_parse_failed", "could not derive an external key for the feed"
            )
        cadence = _cadence_from_dates(item_dates)
        clean_titles = [item_title for item_title in item_titles if item_title]
        values: dict[str, Any] = {
            "external_key": external_key,
            "title": title,
            "creator_name": title,
            "creator_handle": None,
            "canonical_url": canonical_url,
            "platform": "podcast",
            "contact_route_label": None,
            "niche": None,
            "language": language.strip().casefold() if language else None,
            "audience_band": None,
            "posting_cadence": normalize_cadence(cadence),
            "sample_item_count": item_count,
            "recent_views_band": None,
            "third_party_compilation": False,
        }
        provenance = {
            name: FieldProvenance(adapter=self.source, fetched_at=fetched_at, source_url=feed_url)
            for name, value in values.items()
            if value is not None and name != "external_key"
        }
        host = url_host(feed_url)
        return SourcedOpportunity(
            **values,
            field_provenance=provenance,
            raw={
                "feed_url": feed_url,
                "feed_host": host,
                "item_titles": clean_titles[:20],
                "dated_item_count": len(item_dates),
            },
        )

    async def close(self) -> None:
        return None


def _child_text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    cleaned = child.text.strip()
    return cleaned or None


def _parse_rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _cadence_from_dates(dates: list[datetime]) -> str | None:
    if len(dates) < 2:
        return None
    ordered = sorted(dates)
    gaps = [
        (later - earlier).total_seconds() / 86_400
        for earlier, later in zip(ordered, ordered[1:], strict=False)
    ]
    median_gap = statistics.median(gaps)
    if median_gap <= 2:
        return "daily"
    if median_gap <= 4:
        return "multiple_weekly"
    if median_gap <= 10:
        return "weekly"
    if median_gap <= 45:
        return "monthly"
    return "irregular"
