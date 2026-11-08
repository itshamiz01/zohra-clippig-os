"""Shared normalisation for opportunity records.

These helpers decide identity: the dedup key is derived from the
normalised handle or canonical URL, so normalisation must be stable and
shared by every adapter and by the service that merges records.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "si",
        "utm_campaign",
        "utm_content",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_handle(value: str | None) -> str | None:
    """Normalise a creator handle: lowercase, no leading ``@``, no URL parts."""
    if value is None:
        return None
    cleaned = value.strip().casefold()
    if "://" in cleaned:
        parts = [part for part in cleaned.split("/") if part]
        cleaned = parts[-1] if parts else ""
    cleaned = cleaned.lstrip("@").strip().rstrip("/")
    return cleaned or None


def normalize_url(value: str | None) -> str | None:
    """Canonicalise a URL for identity comparison.

    Lowercases scheme/host, drops default ports, fragments and tracking
    parameters, sorts remaining query parameters and strips a trailing
    path slash. Returns None for values that are not absolute HTTP(S)
    URLs — callers treat those as "no canonical URL", never as identity.
    """
    if value is None:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{port}"
    path = parsed.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query_pairs = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def url_host(value: str | None) -> str | None:
    if value is None:
        return None
    host = urlsplit(value).hostname
    return host.lower() if host else None


def derive_external_key(
    *,
    canonical_url: str | None,
    creator_handle: str | None,
    platform: str | None,
) -> str | None:
    """Derive a stable external key when a source did not supply one."""
    if canonical_url:
        return "url:" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:32]
    if creator_handle:
        return f"handle:{(platform or 'unknown').casefold()}:{creator_handle}"
    return None


def compute_dedup_key(
    *,
    platform: str | None,
    creator_handle: str | None,
    canonical_url: str | None,
    external_key: str,
) -> str:
    """Tenant-scoped identity used for conservative exact-match merging."""
    if creator_handle:
        return f"handle:{(platform or 'unknown').strip().casefold()}:{creator_handle}"
    if canonical_url:
        return f"url:{canonical_url}"
    return f"key:{external_key}"


def normalize_platform(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().casefold().replace(" ", "_")
    return cleaned or None


def normalize_cadence(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().casefold().replace("-", "_").replace(" ", "_")
    known = {"daily", "multiple_weekly", "weekly", "monthly", "irregular"}
    return cleaned if cleaned in known else None


def normalize_band(value: str | None, allowed: frozenset[str]) -> str | None:
    """Map a free-form band label onto the canonical scoring vocabulary.

    Accepts spellings like ``100k-500k``, ``under 10k``, ``<10k`` and
    ``1m+``. Anything unrecognised returns None, which excludes the
    scoring factor instead of guessing a band.
    """
    if value is None:
        return None
    cleaned = value.strip().casefold()
    if cleaned in allowed:
        return cleaned
    over = cleaned.endswith("+")
    cleaned = cleaned.rstrip("+")
    cleaned = cleaned.replace("<", "under_").replace(">", "over_")
    cleaned = cleaned.replace("-", "_").replace(" ", "_")
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    candidates = [cleaned]
    for prefix in ("under", "over"):
        if cleaned.startswith(prefix) and not cleaned.startswith(prefix + "_"):
            candidates.append(prefix + "_" + cleaned[len(prefix) :].lstrip("_"))
    if over:
        candidates.append("over_" + cleaned)
    for candidate in candidates:
        if candidate in allowed:
            return candidate
    return None
