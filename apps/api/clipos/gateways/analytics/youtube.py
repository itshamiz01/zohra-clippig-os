from __future__ import annotations

import httpx

from clipos.config import Settings
from clipos.core.enums import ProviderMode, PublishingProvider
from clipos.gateways.analytics.contracts import (
    CANONICAL_METRIC_NAMES,
    AnalyticsCapabilities,
    AnalyticsConnectionContext,
    MetricResult,
    MetricWindow,
)
from clipos.gateways.analytics.errors import AnalyticsAdapterError

YOUTUBE_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
YOUTUBE_ANALYTICS_API_BASE_URL = "https://youtubeanalytics.googleapis.com/v2"


class YouTubeAnalyticsAdapter:
    """YouTube Analytics contract skeleton — UNVERIFIED, disabled by default.

    The factory only constructs this adapter when
    ``CLIPOS_YOUTUBE_ANALYTICS_ENABLED`` is explicitly true, and even then
    no real provider call is made in Phase 7: ``fetch_publication_metrics``
    fails truthfully with category ``analytics_unverified`` instead of
    returning invented numbers.

    The intended verified request shape (for the future operator test) is
    the YouTube Analytics API reports endpoint::

        GET {YOUTUBE_ANALYTICS_API_BASE_URL}/reports
            ?ids=channel==MINE&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
            &metrics=views,estimatedMinutesWatched,averageViewDuration,
                     likes,comments,shares
            &filters=video==<provider_publication_id>
        Authorization: Bearer <access token with YOUTUBE_ANALYTICS_SCOPE>

    Retention and click/conversion metrics are not part of that basic
    report and would remain MISSING until a verified source exists.
    Nothing about this skeleton may be reported as real-tested.
    """

    provider = PublishingProvider.YOUTUBE
    mode = ProviderMode.UNVERIFIED
    source = "youtube_analytics_api"
    required_scopes = frozenset({YOUTUBE_ANALYTICS_SCOPE})
    capabilities = AnalyticsCapabilities(
        provider=PublishingProvider.YOUTUBE,
        mode=ProviderMode.UNVERIFIED,
        supported_metrics=tuple(sorted(CANONICAL_METRIC_NAMES)),
        limitations=(
            "Skeleton only: no YouTube Analytics API call has been executed or verified.",
            "Requires an operator-completed provider test with the "
            "yt-analytics.readonly scope before any mode promotion.",
        ),
    )

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        # The HTTP client is created lazily for the future verified
        # implementation; while the adapter is unverified no request is
        # ever attempted, so construction must not build one eagerly.
        self._client = client

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.publishing_http_timeout_seconds),
            )
        return self._client

    async def fetch_publication_metrics(
        self,
        *,
        connection: AnalyticsConnectionContext,
        provider_publication_id: str,
        window: MetricWindow,
    ) -> list[MetricResult]:
        del connection, provider_publication_id, window
        raise AnalyticsAdapterError(
            "analytics_unverified",
            "YouTube analytics collection is an unverified skeleton in Phase 7; "
            "no provider call was made and no metrics were collected.",
            retryable=False,
            manual_next_step=(
                "Complete the YouTube Analytics provider test with the "
                "yt-analytics.readonly scope, then promote this adapter."
            ),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
