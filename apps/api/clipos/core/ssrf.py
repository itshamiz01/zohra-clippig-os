"""SSRF guard for the few reviewed outbound fetches ClipOS performs.

Phase 2 deliberately shipped URL ingestion disabled because these controls
did not exist. Phase 8 introduces exactly one network consumer (configured
RSS/Atom feed URLs for opportunity sources) and routes every request
through this module:

- only ``http``/``https`` URLs without embedded credentials are accepted;
- the hostname is resolved through an injectable resolver and *every*
  resulting address must be globally routable — loopback, private,
  link-local, CGNAT, benchmarking, documentation and multicast ranges are
  rejected, including IPv4-mapped IPv6 forms;
- redirects are followed manually (at most ``max_redirects`` hops) and each
  hop is re-validated, so a public URL cannot bounce the fetcher onto a
  private target;
- responses are streamed with a hard byte cap and per-hop timeouts, and an
  optional content-type allowlist is enforced.

Known limitation, stated plainly: DNS is resolved for validation and again
by the HTTP stack at connect time, so a hostile authoritative server could
in theory rebind between the two lookups (TOCTOU). Full connection pinning
via a custom transport remains a hardening item; the guard still removes
the practical attack surface (literal private targets, credential URLs,
redirect escapes, oversized or wrong-type bodies).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

Resolver = Callable[[str], Awaitable[Sequence[str]]]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Ranges that must never be fetch targets, checked explicitly in addition
# to the ``is_global`` test so behaviour does not depend on one flag.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "100::/64",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)


class PublicFetchError(RuntimeError):
    """A guarded fetch failed with a sanitized, stable category."""

    def __init__(self, category: str, safe_message: str, *, retryable: bool = False) -> None:
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message
        self.retryable = retryable


class SSRFBlockedError(PublicFetchError):
    """The URL or one of its redirect hops violated the SSRF policy."""


async def default_resolver(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        addresses.append(str(info[4][0]))
    return addresses


def assert_public_address(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Return the parsed address only if it is safe to connect to."""
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        raise SSRFBlockedError(
            "ssrf_unresolvable_address",
            f"resolved address {raw!r} is not a valid IP address",
        ) from None
    candidate: ipaddress.IPv4Address | ipaddress.IPv6Address = address
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        candidate = address.ipv4_mapped
    if (
        not candidate.is_global
        or candidate.is_multicast
        or candidate.is_unspecified
        or candidate.is_loopback
        or candidate.is_link_local
        or candidate.is_private
        or any(candidate in network for network in _BLOCKED_NETWORKS)
    ):
        raise SSRFBlockedError(
            "ssrf_non_public_address",
            f"address {raw} is not a globally routable public address",
        )
    return candidate


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    url: str
    hostname: str
    addresses: tuple[str, ...]


async def validate_public_url(url: str, *, resolver: Resolver = default_resolver) -> ValidatedUrl:
    """Validate scheme, credentials and every resolved address for ``url``."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFBlockedError(
            "ssrf_scheme_not_allowed",
            f"URL scheme {parsed.scheme or '<none>'!r} is not allowed; use http or https",
        )
    if parsed.username or parsed.password:
        raise SSRFBlockedError(
            "ssrf_credentials_not_allowed",
            "URLs with embedded credentials are not allowed",
        )
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError("ssrf_missing_host", "URL has no hostname")
    try:
        _port = parsed.port
    except ValueError:
        raise SSRFBlockedError("ssrf_invalid_port", "URL port is invalid") from None
    del _port

    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        assert_public_address(hostname)
        return ValidatedUrl(url=url, hostname=hostname, addresses=(hostname,))

    try:
        resolved = await resolver(hostname)
    except (OSError, UnicodeError) as exc:
        raise SSRFBlockedError(
            "ssrf_dns_failure", f"hostname {hostname} could not be resolved"
        ) from exc
    if not resolved:
        raise SSRFBlockedError("ssrf_dns_failure", f"hostname {hostname} resolved to no addresses")
    for raw in resolved:
        # A single private address in the answer poisons the whole name.
        assert_public_address(raw)
    return ValidatedUrl(url=url, hostname=hostname, addresses=tuple(resolved))


@dataclass(frozen=True, slots=True)
class FetchedContent:
    url: str
    status_code: int
    content_type: str
    body: bytes


async def fetch_public_url(
    url: str,
    *,
    resolver: Resolver = default_resolver,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 10.0,
    max_bytes: int = 2 * 1024 * 1024,
    max_redirects: int = 3,
    allowed_content_types: Collection[str] | None = None,
) -> FetchedContent:
    """Fetch ``url`` under the SSRF policy, following guarded redirects."""
    own_client = client is None
    active = client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_seconds), follow_redirects=False
    )
    try:
        current = url
        hops = 0
        while True:
            await validate_public_url(current, resolver=resolver)
            try:
                async with active.stream(
                    "GET",
                    current,
                    headers={"Accept": "*/*", "User-Agent": "ZohraClipOS/0.8 feed-reader"},
                    timeout=httpx.Timeout(timeout_seconds),
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise PublicFetchError(
                                "fetch_redirect_without_location",
                                "redirect response carried no Location header",
                            )
                        if hops >= max_redirects:
                            raise SSRFBlockedError(
                                "ssrf_redirect_limit",
                                f"more than {max_redirects} redirects are not followed",
                            )
                        hops += 1
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise PublicFetchError(
                            "fetch_http_status",
                            f"feed host answered HTTP {response.status_code}",
                            retryable=response.status_code >= 500,
                        )
                    content_type = (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                    )
                    if allowed_content_types is not None and content_type not in {
                        item.lower() for item in allowed_content_types
                    }:
                        raise SSRFBlockedError(
                            "ssrf_content_type_not_allowed",
                            f"response content type {content_type or '<none>'!r} is not allowed",
                        )
                    declared = response.headers.get("content-length")
                    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                        raise SSRFBlockedError(
                            "ssrf_response_too_large",
                            f"response declares {declared} bytes, above the {max_bytes} cap",
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise SSRFBlockedError(
                                "ssrf_response_too_large",
                                f"response exceeded the {max_bytes} byte cap",
                            )
                    return FetchedContent(
                        url=current,
                        status_code=200,
                        content_type=content_type,
                        body=bytes(body),
                    )
            except httpx.TimeoutException as exc:
                raise PublicFetchError(
                    "fetch_timeout", "feed host did not answer in time", retryable=True
                ) from exc
            except httpx.HTTPError as exc:
                raise PublicFetchError(
                    "fetch_failed",
                    f"feed request failed ({type(exc).__name__})",
                    retryable=True,
                ) from exc
    finally:
        if own_client:
            await active.aclose()
