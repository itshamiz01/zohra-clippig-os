# Zohra ClipOS — Phase 8 Handoff

Date: 2026-11-08
Version: 0.8.0
Alembic head: `0b728736cdd4`
Status: Phase 8 implemented in the working tree for gate review.

## Gate decision

**ACCEPT PHASE 8 as the opportunity-hunting and commercial-fit backend, with the explicit limitations below.**

This is not a production, outreach, revenue or client-acquisition certificate. In particular, **no creator has been discovered from a live source in this delivery**: seed-list records are operator assertions, the RSS adapter's network path has never been exercised against a real feed (it is disabled by default), search and public-site sources are disabled, and the fake adapter's records are deterministic fixtures. Phase 8 contacts nobody — it has no outreach code path at all — and nothing here promotes mocked or operator-supplied evidence to verified discovery.

## What was delivered

### Sources and adapters (`clipos/gateways/opportunities/`)

- The gateway mirrors publishing/analytics: an `OpportunityAdapter` protocol, per-kind capabilities with limitations, a registry/factory, and truthful provider states on every source and sync run.
- `SeedListAdapter` (**real_tested**) imports an operator-supplied record list from the sync request body — fully local, strict per-record validation (one malformed record fails the sync with the record index), full normalisation.
- `RssFeedAdapter` (**unverified**) reads one configured RSS 2.0 or Atom feed as one channel opportunity: items become evidence (titles, dated-item count) and posting cadence is derived from item-date intervals. It is built only when `CLIPOS_OPPORTUNITY_RSS_ENABLED=true`; parsing and normalisation are proven through a fake transport, never against a live feed.
- `DisabledSearchAdapter` / `DisabledOpportunityAdapter` (**disabled**) serve search-provider and public-site sources — and RSS while it is not enabled — failing every sync truthfully with `opportunity_unsupported` and recording nothing.
- `FakeOpportunityAdapter` (**mocked**) emits deterministic records labelled `fake_opportunity_adapter` in every provenance entry; settings validation forbids selecting it in staging/production.

### SSRF guard (`clipos/core/ssrf.py`)

- The control set Phase 2 required before any URL fetch: http/https only, credential-bearing URLs rejected, every DNS answer validated (one private address poisons the name), literal and resolved loopback/private/link-local/CGNAT/benchmarking/documentation/multicast ranges rejected including IPv4-mapped IPv6, redirects followed manually with per-hop revalidation (max 3), streamed byte caps and a content-type allowlist, per-hop timeouts. Cloud metadata (`169.254.169.254`) is covered by the link-local rejection and an explicit test.
- All feed fetches go through `fetch_public_url` with an injectable resolver and HTTP client, which is what makes the guard unit-testable without network access.

### Sync (import)

- Syncs are durable background jobs (`opportunity.sync`) created only through `POST /api/v1/opportunities/sources/{id}/syncs` (editor+); the generic jobs endpoint rejects the kind as reserved, and generic cancel/retry direct callers back to the domain endpoint, mirroring Phases 6 and 7.
- `opportunity_sync_runs` records each pass: lifecycle state, the serving adapter's truthful mode, records received/created/merged, possible duplicates flagged, suppressed records skipped, and a sanitized failure category. The worker dispatches the kind to `OpportunityProcessor`; restart recovery requeues safely because sync is idempotent (exact-match merge, suppression re-check, deterministic rescoring — no uncertain remote side effect exists).
- Normalisation is shared by adapters and service: handles (lowercase, no `@`), canonical URLs (tracking parameters stripped, default ports dropped), platform names, audience/view bands and cadences mapped onto the scoring vocabulary — unrecognised bands become "not observed", never a guess.

### Evidence and provenance

- Every stored opportunity carries per-field provenance (adapter, fetch timestamp, source URL), the scoring signals consumed by `opportunity-score-v1`, and the raw source record. The pre-existing `OpportunityContract` vocabulary in `contracts/future.py` maps directly: `source` → source row, `external_key` → `external_key`, `review_state` → lifecycle `state`, `evidence` → `evidence`, `score_version` → `score_version`.

### Dedup and suppression

- Exact identity (normalised handle, else canonical URL, else external key — tenant-scoped) merges into the earliest record and refreshes it: newly observed fields win, absent fields keep stored values, and the record is rescored. Identity is deliberately tenant-wide: the same creator imported from two sources is one opportunity, attributed to the source that first imported it.
- Fuzzy matches (creator/title text similarity ≥ 0.86 over the 500 most recent tenant records) are **flagged only**, via `possible_duplicate_of_id`; nothing is ever auto-merged on a fuzzy match.
- Suppressions (`creator_handle`, `domain`, `keyword`; reviewer-created with a reason) are matched at import (record skipped, existing row suppressed) and applied immediately to existing rows on creation; suppressed rows leave the default working list and remain visible under an explicit `state=suppressed` filter. Suppression create/delete is audited.

### Scoring (`clipos/opportunities/scoring.py`)

- `opportunity-score-v1` mirrors Phase 3's explainable pattern: six factors — `content_opportunity` (0.24), `audience_fit` (0.20), `performance_fit` (0.16), `contactability` (0.14), `rights_feasibility` (0.14), `effort_inverse` (0.12) — each persisted in `opportunity_score_components` with label, meaning, weight, weighted score, evidence and missing-data behaviour.
- A factor with no observed data (for example no publicly shown views band) is **excluded** from the weighted mean — never scored as zero. Scoring is a pure function of the stored record, so a rescore after a merge reproduces the identical overall score; a test proves it.

### Lifecycle and Phase 9 handoff

- Lifecycle `new → reviewing → shortlisted/rejected/suppressed` with audited transitions; moving into review is editor+, while shortlist/reject/suppress require the reviewer role (the service enforces it even though the route admits editors).
- Handoffs are `opportunity-handoff.v1` packages (opportunity fields, evidence provenance, all six score components) with `draft → ready` states, one per opportunity; a ready package cannot be silently rewritten. The payload records `phase_9_status: not_implemented` and `outreach_status: not_sent`.
- **No outreach exists**: no email, DM or contact-form code path, route or adapter method exists in Phase 8; the only contact data stored is a text label of a public contact route. A test asserts the opportunities route table and the adapter surface contain no send/outreach/email surface.

## Gate evidence

- Backend gate: **184 passed in 142.82s** (160 pre-Phase-8 baseline retained green + 24 new Phase 8 cases) with **73.71% branch-aware coverage** (70% enforced minimum; coverage run: 184 passed in 190.80s). Ruff check and format check pass on `apps/api/clipos`; mypy reports no issues in 114 source files. Baseline note: `ruff check apps/api` still shows the 7 pre-existing Phase 6 test-file errors and 2 unformatted Phase 6 test files that predate this phase; no new lint debt was added. Environment note: the Phase 4 rendering tests are timing- and disk-sensitive on this VM — two intermediate runs with a nearly full `/tmp` failed them environmentally; with space freed, the plain and coverage runs both passed 184/184.
- Migration: clean SQLite base→head through all eight revisions; Phase 8 revision re-run is a no-op at head; `downgrade -1` returns to `c7d40e02a0b4` and re-upgrade restores head; `alembic check` reports no drift. Offline PostgreSQL DDL for the Phase 8 step: 8,429-byte upgrade (6 tables, 6 new native enum types, check/unique/index constraints) and a 937-byte downgrade that drops the tables and the six new types.
- Focused Phase 8 evidence (16 test functions, 24 cases): seed-list import end-to-end through the durable job (provenance, score ordering, components), exact-match merge with stable IDs, fuzzy match flagged and not merged, suppression blocking re-import + hiding existing rows + reviewer gating, scoring reproducibility/component persistence/version + a not-applicable factor excluded, SSRF guard cases (private literals incl. `[::1]` and `169.254.169.254`, `0.0.0.0`, credential URLs, non-http scheme, DNS answers containing a private address, redirect-to-private, redirect loop, oversized body, wrong content type), RSS + Atom import via fake transport with cadence derivation and malformed-feed run failure (`rss_parse_failed`), tenant isolation, lifecycle transition permissions with audit rows, handoff draft contents/version + ready transition + no silent rewrite, disabled search/public-site truthfulness (`opportunity_unsupported`, zero records), reserved job kind rejection, fake adapter mocked labelling, and the no-outreach surface assertion.

## Limitations and unverified boundaries

- **No real discovery source is verified.** RSS requires an operator to enable it and complete a live fetch of a real public feed (plus a per-feed robots/terms review) before its mode can be promoted from `unverified`; until then it is disabled by default. Search and public-site discovery do not exist as adapters.
- The SSRF guard validates DNS answers immediately before fetching, but the HTTP stack resolves again at connect time; full connection pinning (custom transport) remains a hardening item against DNS-rebinding TOCTOU.
- Seed-list contents are operator assertions. ClipOS normalises and scores them but does not verify that a channel exists, that an audience band is accurate, or that a labelled contact route is real.
- Scoring weights, target bands, the fuzzy threshold (0.86) and the fuzzy scan bound (500 most recent records) are transparent editorial constants that have never been calibrated against a real discovered corpus.
- Exact-match identity is tenant-wide across sources (see above): a creator first imported from a seed list keeps that source attribution when an RSS import later merges into the same record.
- RSS feeds are modelled as podcast/channel sources (`platform: podcast`); feed items are evidence only — no media is downloaded, and no audience or performance data is inferred from a feed.
- The web console gained only static home-page status updates for Phase 8; there is no `/opportunities` UI in this phase.
- PostgreSQL, Redis and Docker Compose remain **unverified** live, exactly as in earlier phases; all Phase 8 evidence is SQLite + in-memory queue.

## Stop condition

Phase 9 remains locked. A later phase may add a CRM and human-approved outreach only under its own authorized design; Phase 8 grants no authority to contact any creator, send anything, promise terms, or take any external or commercial action.
