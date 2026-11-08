# Zohra ClipOS — Phase 7 Handoff

Date: 2026-11-08
Version: 0.7.0
Alembic head: `c7d40e02a0b4`
Status: Phase 7 implemented in the working tree for gate review.

## Gate decision

**ACCEPT PHASE 7 as the analytics, experimentation and bounded learning-loop backend, with the explicit limitations below.**

This is not a production, platform, revenue or reach-readiness certificate. In particular, **no real analytics data exists in this delivery**: the default adapter is disabled, the test adapter is a deterministic fake, and the YouTube Analytics adapter is an unverified skeleton that makes no provider call. Nothing in Phase 7 changes what Phase 6 proved, and nothing here promotes any mocked evidence to real.

## What was delivered

### Analytics truth model

- `analytics_metric_snapshots` stores one provider metric outcome per publication and day-grained window with an explicit `availability` — `reported`, `missing`, `unavailable`, `permission_denied` or `stale`. A database check constraint (and a matching gateway-level `MetricResult` invariant) guarantees a value exists exactly when the metric was reported or is stale: a missing metric is `NULL`, never an invented zero.
- Snapshots dedup on `(publication_id, metric, window_start, window_end, source)`; re-collection refreshes the stored truth in place instead of duplicating rows.
- `analytics_collection_runs` records each pass: provider filter, lifecycle state, the serving adapter's truthful mode, publications considered, snapshots written, reported/unavailable counts and a sanitized failure category.

### Collection

- Collection is a durable background job (`analytics.collect`) created only through `POST /api/v1/analytics/collections` (editor+); the generic jobs endpoint rejects the kind as reserved, and generic cancel/retry direct callers back to the domain endpoint, mirroring Phase 6 publishing.
- The worker dispatches the kind to `AnalyticsProcessor`; restart recovery requeues queued jobs and safely requeues stale running jobs because collection is read-only and idempotent (unlike publishing, there is no uncertain remote side effect).
- The service reads only succeeded publications carrying a `clipos-analytics-handoff.v1` blob (whose `phase_7_status` now reads `implemented`), builds a provider connection context per publication, and persists per-metric results. Whole-call adapter failures are counted per publication and produce `partially_succeeded`/`failed` runs with sanitized categories — never fabricated snapshots.

### Gateway (`clipos/gateways/analytics/`)

- Contracts mirror publishing: an `AnalyticsAdapter` protocol, canonical metric definitions (`clipos-metrics.v1`: views, watch time, average view duration, retention, likes, comments, shares, clicks, conversions) and an additive/non-additive distinction used by rollups.
- `DisabledAnalyticsAdapter` is the default for every provider and reports every metric `unavailable`.
- `FakeAnalyticsAdapter` is deterministic, labelled `mocked` in its mode and `fake_analytics_adapter` in every snapshot source; it models `conversions` as `missing` and can simulate stale and permission-denied publications for tests. Settings validation forbids selecting it in staging/production.
- `YouTubeAnalyticsAdapter` is a skeleton: disabled by default (`CLIPOS_YOUTUBE_ANALYTICS_ENABLED=false`), fail-closed configuration validation when enabled, and — even when enabled — it performs **no** provider call and raises `analytics_unverified`. The intended YouTube Analytics API request shape and the `yt-analytics.readonly` scope are documented in the module for the future operator test.

### Queries

- Per-publication performance (latest snapshot per metric with availability counts) and a tenant performance list.
- Campaign rollups over `Publication.campaign_reference`: sums for additive metrics, view-weighted average view duration, means for percentages, with reported/unavailable publication counts per metric. Missing campaigns return an explicit gap note.
- Cost summary over `UsageCostEvent` with exact Decimal totals, success/failure counts and per-provider/operation breakdown. The response states its attribution limit: usage events are not attributed to individual publications, so `cost_per_publication` is an indicative tenant average only.
- Predicted-vs-actual joins `CandidateScoreComponent` rows (including `hook_clarity`) through publication → render job → render plan → candidate, returning predicted components beside actual metrics and listing lineage gaps explicitly.

### Experiments

- Tenant-scoped experiments with a canonical-metric-validated success metric, eligibility/success-criteria JSON, and a guarded lifecycle: `draft → running → paused → completed`, with `cancelled` from any non-terminal state. Running requires at least one variant; variants must link to a tenant-owned clip candidate or publication and are unique by name per experiment. All transitions and variant additions are audited.

### Bounded learning loop

- Deterministic rules v1 (`learning-loop.v1`) over stored, reported views only: duration, captions, posting-time and format group comparisons plus a hook-clarity inversion check. Rules require at least 3 publications per group and a ≥1.2× mean-views effect before firing.
- Every recommendation is created `proposed` by `system:learning-loop`, cites its evidence (rule, group statistics, publication ids, evidence fingerprint), and generation is fingerprint-deduped.
- There is **no apply path**: review accepts only `approve`/`reject` (reviewer role), is audited, and approval changes nothing by itself. A test proves rights, quality, gate, approval, feature-flag, usage-cost and publication tables are identical before and after generation, collection and review — the learning loop has no write path to them by construction.

## Gate evidence

- Backend gate: **160 passed in 124.65s** (149 pre-Phase-7 baseline retained green + 11 new Phase 7 tests) with **75.02% branch-aware coverage** (70% enforced minimum; coverage run: 160 passed in 187.10s). Ruff check and format check pass on `apps/api/clipos`; mypy reports no issues in 98 source files. Baseline note: `ruff check apps/api` still shows the 7 pre-existing Phase 6 test-file errors and 2 unformatted Phase 6 test files that predate this phase; no new lint debt was added.
- Migration: clean SQLite base→head through all seven revisions; Phase 7 revision re-run is a no-op at head; `downgrade -1` returns to `d4a9a9290b9a` and re-upgrade restores head; `alembic check` reports no drift. Offline PostgreSQL DDL for the Phase 7 step: 7,020-byte upgrade (5 tables, 5 new native enum types, check/unique/index constraints) and a 741-byte downgrade that drops the tables and the five new types.
- Focused Phase 7 evidence (11 tests): disabled-adapter truthfulness (all metrics `unavailable`/`NULL`), fake-adapter end-to-end collection through the durable job (8 reported + `conversions` missing-as-`NULL`), snapshot dedup and in-place refresh across stale → permission-denied changes, YouTube skeleton unverified failure (`analytics_unverified`, zero snapshots) and fail-closed settings, tenant isolation across performance/runs/recommendations, exact cost maths, campaign rollup maths, experiment lifecycle validation, recommendation evidence + dedup + reviewer-gated approve/reject + audit rows, learning-loop table isolation, and the `MetricResult` value/availability invariant.

## Limitations and unverified boundaries

- **No real analytics provider is verified.** YouTube Analytics requires an operator-completed provider test (OAuth grant with `yt-analytics.readonly`, live report fetch, response-shape verification) before the skeleton can be promoted; until then collection for YouTube is either disabled or truthfully failed.
- The Phase 7 service does not yet decrypt connection tokens for adapters (`access_token` is intentionally unpopulated in the connection context); token wiring lands with the verified YouTube adapter so no credential path exists before it is needed.
- Collection windows are day-grained lifetime windows; intraday refresh creates a new snapshot only on the next UTC day per publication/source.
- Recommendation rules v1 use views only, fixed thresholds and UTC posting-hour buckets; they are decision support for humans, not an optimization system, and their thresholds have never been validated against real provider data.
- Experiments are records and lifecycle only: Phase 7 does not assign traffic, split audiences, or compute experiment outcomes automatically.
- The web console gained only static home-page status updates for Phase 7; there is no `/analytics` UI in this phase.
- PostgreSQL, Redis and Docker Compose remain **unverified** live, exactly as in earlier phases; all Phase 7 evidence is SQLite + in-memory queue.

## Stop condition

Phase 8 remains locked. A later phase may add opportunity discovery only under its own authorized design; Phase 7 grants no authority to select leads, contact anyone, apply recommendations automatically, or take any external or commercial action.
