# Phase 5 integration truth matrix

Status vocabulary is evidence-based:

- **real_tested** — this gate executed the real adapter/tool locally and asserted meaningful output;
- **mocked** — only a fixture/`MockTransport` contract was executed;
- **disabled** — intentionally unavailable by safe default or out of authorized scope;
- **blocked** — runtime prerequisite/capability is missing;
- **unverified** — implementation/configuration exists but no live successful test occurred.

| Integration / capability | Adapter | Status | Phase 5 evidence | Remaining proof before production |
|---|---|---:|---|---|
| SQLite database | SQLAlchemy + aiosqlite | **real_tested** | 134-test suite; fresh base→head; no drift; Phase 5 rollback/reapply; all five new tables and clean foreign-key checks | Development only; not a production database |
| PostgreSQL database | SQLAlchemy + asyncpg | **unverified** | 46,862-byte offline full upgrade; 10,013-byte Phase 5 upgrade and 990-byte downgrade; five tables, JSONB and 11 native enums with matching drops | Live migrate/rollback, concurrent tenant locks/idempotency, backup/restore |
| In-memory typed queue | `InMemoryQueueBroker` + `ClipOSWorker` | **real_tested** | Asset/render kind dispatch, durable queued rows, cancellation/retry/recovery unit/integration paths | Single-process development only |
| Redis queue | `RedisQueueBroker` | **unverified** | Configuration and retained mocked broker contracts only; Redis unavailable | Live enqueue/dequeue, duplicate/loss, failover, stale recovery |
| Local object storage | `LocalObjectStorage` | **real_tested** | Tenant/key confinement, atomic writes, source materialization checksum, validated output/SRT storage, immutable DB checksum delivery check | Hardened shared storage, encryption/retention, malware/media isolation |
| S3 object storage | none | **disabled** | Startup rejects `storage_backend=s3`; credential names are status-only placeholders | Reviewed private adapter and real object-integrity/tenant tests |
| FFmpeg/FFprobe 7.0.2 static | shell-free local tooling | **real_tested** | Genuine generated video+audio, center-crop vertical render, Unicode subtitle burn-in, AAC/H.264 output, progress, probe, checksum | Broader malformed-media corpus, container sandbox, patched binary lifecycle |
| FFmpeg base render capabilities | scale/crop/pad/fps/loudnorm/libx264/AAC | **real_tested** | Runtime capability parser and genuine render | Repeat in exact deployment image |
| FFmpeg subtitle burn-in | subtitles/libass | **real_tested** | Unicode Hindi/Arabic caption fixture burned into genuine output; sidecar verified | Font coverage/visual QA corpus and exact deployment image |
| FFmpeg text branding | drawtext | **blocked** | Tested static binary transparently reports missing `drawtext`; plan rejects requested branding | Real render with tenant policy in exact capable deployment image |
| Face detection / face tracking | none | **disabled** | Capability/API disclose unavailable; smart request stores center-crop fallback | Separate reviewed implementation and accuracy/privacy tests |
| Logo/image overlays | none | **disabled** | Capability/API disclose unavailable; no upload/request schema | Rights-cleared asset policy and rendering implementation |
| Third-party music | none | **disabled** | No request/schema/executor exists | Rights/licensing ledger and separate authorization |
| OpenAI transcription | provider-neutral HTTP adapter | **unverified** | Retained strict mocked response/limit tests; no credential/live call | Real authorized call, timestamp/quality/cost/privacy review |
| Disabled transcription | safe local adapter | **real_tested** | Explicitly moves audio processing to review; no fake transcript | None for disabled behavior |
| Deterministic discovery | local planner/scorer | **real_tested** | Retained genuine transcript evidence tests; no provider needed | Broader editorial validation only |
| OpenAI/OpenAI-compatible selector | bounded model gateway | **mocked** | Retained `MockTransport` schemas, bounds, retries, usage/cost/fallback | Real authorized provider call and current pricing confirmation |
| Anthropic selector | bounded model gateway | **mocked** | Retained `MockTransport` contract/failure tests | Real authorized provider call and current pricing confirmation |
| Model-disabled discovery | deterministic fallback | **real_tested** | Default configuration and deterministic run tests | None for disabled behavior |
| Phase 5 deterministic Quality Council | `QualityGateService` + eight persisted check types | **real_tested** | Focused database/API/storage tests for complete council, corrupted output, fabricated transcript, low confidence, contradictory evidence and destination profiles | Broader audiovisual/editorial corpus and independent calibration |
| Structured Rights Guardian | strict API + relational evidence + live-time policy | **real_tested** | Unknown/expired/revoked/wrong platform/territory/missing attribution/third-party state and tenant-isolation tests | Legal/privacy review, approved external document system, retention policy and jurisdictional validation |
| Human quality decision | reviewer RBAC + requester separation + acknowledgements | **real_tested** | Authorized approval, editor denial, requester self-approval denial, non-overridable block and model-actor denial | Organizational reviewer assignment, training and escalation policy |
| Signed gate evidence | canonical HMAC-SHA-256 server evidence | **real_tested** | Signature generation/verification, bounded expiry contract, protected-input hash and stale/revocation denial | Key rotation/HSM policy and multi-instance production secret management |
| Future publish/upload preflight | fresh server-side `verify` at two checkpoint enums | **real_tested** | Both positive checkpoints and unapproved/revoked/direct-job-bypass negatives; creates no external action | Phase 6 must call internally at job acceptance and immediate upload; full race/transaction test |
| Model-assisted Phase 5 quality | none enabled | **disabled** | UI/API truthfully report `model_assistance_used=false`; deterministic evidence and human review used | Strict bounded provider schema/provenance, real authorized calls and quality/privacy evaluation before enablement |
| Legal clearance automation | none | **disabled** | Every detail response states passing gate is not legal clearance | Qualified human legal/privacy review remains mandatory |
| Next.js dashboard | Next.js 16.4.3 standalone | **real_tested** | Oxlint, strict TypeScript, optimized eleven-route build including `/quality` | Browser E2E/accessibility matrix and deployment TLS/proxy test |
| Authenticated dashboard API | same-origin proxy → FastAPI | **real_tested** | Production-style HTTP smoke for `/quality` and retained operator routes; login; empty tenant quality/rights lists; unauthenticated 401; external-action job bypass denial; security headers | Multi-browser E2E with live worker/output and populated review records |
| Docker Compose | Postgres/Redis/API/worker/web declaration | **unverified** | YAML parsed; Dockerfile reviewed; worker resource declarations present | Docker unavailable: config/build/up/health/runtime all required |
| Public social publishing | none | **disabled** | No OAuth token, publisher, uploader or public media route; generic publish/upload jobs rejected | Phase 6 only after separate authorization and mandatory gate integration |
| Outreach/CRM/contracts/payments | none | **disabled** | No executor exists | Future separately authorized phases only |
| URL media acquisition | none | **disabled** | Endpoint reports disabled and performs no fetch | Redirect/DNS-rebinding/private-address/size controls plus authorization |

## Current capability disclosure

The real-tested static FFmpeg status is:

```text
available: true
render_ready: true
capabilities: aac, crop, fps, libx264, loudnorm, pad, scale, subtitles
limitations:
  - tenant text branding unavailable
  - face detection/tracking unavailable; smart requests use center crop
```

Capabilities are runtime evidence, not assumed universal FFmpeg features.

## Mock-versus-real rule

Mocked provider/process-failure tests establish validation and failure behavior only. They do not establish service availability, output quality, pricing accuracy, deployment security or production readiness. The genuine deterministic FFmpeg fixture is specifically separated from timeout/crash/probe mocks.

## Stop condition

Phase 5 may create private validated media, structured rights/check evidence, independent approval and non-distributing fresh preflight records. Public publishing and all external commercial integrations remain disabled until a separately authorized Phase 6 implementation calls the mandatory gate at job acceptance and immediately before upload.

---

# Phase 7 addendum — analytics truth rows

The matrix above is the Phase 5 record and is retained unchanged. Phase 7 adds the following rows under the same status vocabulary; Phase 7 test counts are recorded in `docs/PHASE_7_HANDOFF.md`.

| Integration / capability | Adapter | Status | Phase 7 evidence | Remaining proof before production |
|---|---|---:|---|---|
| Analytics collection (all providers, default) | `DisabledAnalyticsAdapter` | **disabled** | Integration test: collection over a succeeded publication persists all 9 canonical metrics as `unavailable` with `NULL` values; run records `adapter_mode=disabled` | None for disabled behaviour |
| Analytics collection (test/local) | `FakeAnalyticsAdapter` | **mocked** | End-to-end integration test: queued job → worker → 9 snapshots (8 `reported`, `conversions` `missing` with `NULL` value, never zero); dedup refresh and stale/permission-denied states persisted truthfully | Never production evidence; the factory refuses `CLIPOS_ANALYTICS_PROVIDER=fake` in staging/production |
| YouTube Analytics API | `YouTubeAnalyticsAdapter` skeleton | **unverified** | Contract implemented; adapter disabled by default (`CLIPOS_YOUTUBE_ANALYTICS_ENABLED=false`); when explicitly enabled it still makes no provider call and collection fails truthfully with `analytics_unverified`; fail-closed settings validation requires the OAuth client and token key | Operator-completed real provider test with the `yt-analytics.readonly` scope, response-shape verification, then mode promotion |
| Meta / TikTok / LinkedIn analytics | none | **disabled** | Factory returns the disabled adapter; capabilities endpoint reports `disabled` | Separate reviewed adapters and real provider tests |
| Metric truth model | `analytics_metric_snapshots` | **real_tested** | DB check constraint + `MetricResult` invariant tests prove unreported metrics cannot carry values; missing-vs-zero distinction asserted end to end | PostgreSQL live migration check remains part of the general unverified PostgreSQL row |
| Predicted-vs-actual lineage join | publication → render job → render plan → candidate score components | **real_tested** | Integration test joins seeded score components (`hook_clarity`, `narrative_payoff`) to collected metrics and asserts lineage completeness and gap reporting | Broader candidate corpus from real discovery runs |
| Learning loop (recommendations v1) | deterministic rules over stored snapshots | **real_tested** | Integration test: evidence-cited posting-time recommendation from 6 publications, generation dedup, reviewer-only approve/reject with audit rows, and byte-identical rights/quality/gate/approval/policy/cost/publication tables before and after | Editorial validation of rule thresholds on real provider data (none exists yet) |
| Recommendation application | none | **disabled** | No apply path exists in Phase 7 by design; the review schema accepts only `approve`/`reject` (an `apply` decision is rejected with 422) | A separately reviewed change process in a later phase |

## Phase 7 stop condition

Phase 7 may collect and report analytics truthfully, run experiments as records, and propose evidence-cited recommendations for human review. It may not apply recommendations, select leads, contact anyone, or take any external or commercial action; those remain locked until a separately authorized Phase 8 implementation.

---

# Phase 8 addendum — opportunity discovery truth rows

The matrices above are the Phase 5 and Phase 7 records and are retained unchanged. Phase 8 adds the following rows under the same status vocabulary; Phase 8 test counts are recorded in `docs/PHASE_8_HANDOFF.md`.

| Integration / capability | Adapter | Status | Phase 8 evidence | Remaining proof before production |
|---|---|---:|---|---|
| Opportunity import (operator seed lists) | `SeedListAdapter` | **real_tested** | Integration tests: end-to-end import through the durable job, strict record validation, per-field provenance, exact-match merge on re-import with stable IDs | None for local behaviour; record contents remain operator assertions, not verified live data |
| Opportunity import (RSS/Atom feeds) | `RssFeedAdapter` behind `clipos.core.ssrf` | **unverified** | Parsing/normalisation proven via `httpx.MockTransport` (RSS 2.0 + Atom, cadence derivation, malformed feed fails the run with `rss_parse_failed`); SSRF guard unit-tested for private literals, DNS answers containing private addresses, credential URLs, redirect-to-private, redirect loops, oversized bodies and wrong content types; adapter disabled by default (`CLIPOS_OPPORTUNITY_RSS_ENABLED=false`) | One operator-run live fetch of a real public feed, robots/terms review per feed, then mode promotion |
| Opportunity discovery (search providers) | `DisabledSearchAdapter` | **disabled** | Capabilities endpoint reports `disabled`; a sync fails its run truthfully with `opportunity_unsupported` and records nothing | A reviewed provider adapter, credentials and a real provider test under a separate authorization |
| Opportunity discovery (public sites) | `DisabledOpportunityAdapter` | **disabled** | Same truthful failure as search; no scraping code path exists | Per-site robots/terms/authorisation review before any adapter is designed |
| Opportunity discovery (test/local) | `FakeOpportunityAdapter` | **mocked** | Deterministic records labelled `fake_opportunity_adapter` in every provenance entry; run records `adapter_mode=mocked`; settings validation forbids selecting it in staging/production | Never production evidence |
| SSRF guard | `clipos.core.ssrf.fetch_public_url` | **real_tested** | Unit tests listed above cover scheme/credential rejection, literal and DNS-resolved private ranges (incl. cloud metadata 169.254.169.254), per-hop redirect revalidation (max 3), byte caps and content-type allowlist | Connection pinning to remove the DNS validation/connect TOCTOU window; live-network behaviour remains unverified |
| Dedup, suppression and scoring | `OpportunityService` + `opportunity-score-v1` | **real_tested** | Integration tests: exact-match merge, fuzzy match flagged via `possible_duplicate_of_id` and never merged, suppression blocks re-import and hides existing rows, six persisted components with a not-applicable factor excluded (never zero), identical rescore after merge, reviewer-gated lifecycle with audit rows | Editorial calibration of factor weights/thresholds on real discovered corpora (none exist yet) |
| Phase 9 handoff packages | `opportunity-handoff.v1` payload | **real_tested** | Integration test: draft contents (schema, provenance, six score components, `outreach_status: not_sent`, `phase_9_status: not_implemented`), `draft → ready` transition, ready package cannot be silently rewritten | Phase 9 itself is locked; no consumer exists |
| Outreach (email, DM, contact forms) | none | **disabled** | No route, adapter method or code path exists; a test asserts the opportunities route table and adapter surface contain no send/outreach/email surface | A separately authorized Phase 9 design with per-action human approval |

## Phase 8 stop condition

Phase 8 may discover, normalise, deduplicate, suppress, score and package opportunities for human review. It may not contact any creator, send anything, or take any external or commercial action; CRM, outreach, contracting and payments remain locked until a separately authorized Phase 9 implementation.

---

# Phase 9 addendum — sales, CRM and revenue truth rows

The matrices above are the Phase 5, Phase 7 and Phase 8 records and are retained unchanged. Phase 9 adds the following rows under the same status vocabulary; Phase 9 test counts are recorded in `docs/PHASE_9_HANDOFF.md`.

| Integration / capability | Adapter | Status | Phase 9 evidence | Remaining proof before production |
|---|---|---:|---|---|
| Handoff intake into CRM | `CrmService.intake_handoff` over `opportunity-handoff.v1` | **real_tested** | Integration tests: ready handoff → prospect account + contact with handle-based dedup identity, intake pipeline event, repeat intake returns the same account and creates nothing (unique constraints on handoff and opportunity); draft handoffs are refused with 409 | None for local behaviour; intake consumes operator/Phase 8 evidence, it does not verify it |
| Pipeline lifecycle and history | `CrmService.transition_stage` + `crm_pipeline_events` | **real_tested** | Integration tests: forward moves, backwards/side-state moves refused without a reason (422 `stage_reason_required`) and accepted with one, terminal stages locked (409), and the append-only event sequence matches every accepted transition | Editorial calibration of stage definitions against a real sales process (none exists yet) |
| Contact/account dedup and merges | `CrmService` (Phase 8 pattern) | **real_tested** | Integration tests: exact-match merge on create with field refresh, fuzzy match flagged via `possible_duplicate_of_id` and never merged, reviewer-only merge re-points tasks/drafts/history and records a `merge` pipeline event; suppression is sticky across merges | Threshold calibration (0.86) on a real corpus, as in Phase 8 |
| Opt-out suppression at draft creation | `CrmService.create_draft` guard | **real_tested** | Integration test: suppressed contact → draft refused with 409 `contact_suppressed`, refusal audited with outcome `denied`, drafts work again after a reviewer lifts suppression | None for local behaviour |
| Draft generation from cited evidence | deterministic templates `sales-draft-v1` | **real_tested** | Integration tests: outreach draft cites opportunity + handoff evidence and states only cited facts; outreach on an evidence-free manual account refused (422 `insufficient_evidence`); follow-up cites pipeline history; zero-citation account refused at service level | Editorial review of template copy against real prospects (none exist yet) |
| Draft review | `CrmService.review_draft` | **real_tested** | Integration tests: `draft → in_review → approved` with reviewer-only approve/reject (editor 403), direct approval from draft refused (409), a `sent` target state rejected as invalid (422) — approval grants no sending capability | None; there is nothing to prove delivery with, by design |
| Outbound communications (email) | `DisabledCommunicationsAdapter` | **disabled** | Capabilities endpoint reports `disabled`; the adapter raises `comms_unsupported` naming the missing email provider decision; settings reject any real provider name with an error stating the email/calendar/payment provider decisions are still owed | The user must choose an email provider (and a calendar provider for invites); then a reviewed adapter, credentials and a real provider test under a separate authorization |
| Communications (test/local) | `FakeCommsAdapter` | **mocked** | Unit tests: records messages in memory only, receipts report `delivered=False` with `fake-comms-*` references; settings forbid it in staging/production; the CRM service never calls it | Never production evidence |
| Payments | `DisabledPaymentsAdapter` | **disabled** | Same pattern as communications: `payments_unsupported`, capabilities report `disabled`, real provider names are configuration errors naming the missing payment provider decision | The user must choose a payment provider; then a reviewed adapter integrated in test mode first, with a real test-mode transaction under a separate authorization |
| Payments (test/local) | `FakePaymentsAdapter` | **mocked** | Unit tests: records requests in memory only, receipts report `processed=False`; forbidden in staging/production; never called by the CRM service | Never production evidence |
| Revenue records and summaries | `revenue_records` + `CrmService.revenue_summary` | **real_tested** | Integration test: per-currency buckets keep kinds separate — an issued 100k quote, an issued 99_900 payment and draft/void records never inflate `collected`, which counts only paid payments; definitions are returned in the response | Reconciliation against a real payment provider once one is chosen and integrated |
| Onboarding | `onboarding_records` + checklist `onboarding-checklist-v1` | **real_tested** | Integration tests: creation refused before client stage (409) and on duplicates (409), unknown checklist items refused (422), completion refused until every item is done (409), milestone recording | None for local behaviour |
| Sending of any sales asset | none | **disabled** | No route, service method or adapter call can transmit a draft; a test asserts the CRM route table and `CrmService` surface contain no send/deliver/transmit/charge surface, and `SalesAssetState` has no sent state | A separately authorized design with per-send human approval, only after a communications provider is chosen |

## Phase 9 stop condition

Phase 9 may intake opportunities into a CRM, run a human-driven pipeline, generate and review evidence-cited drafts, track tasks and onboarding, and record revenue facts with honest separation of kinds. It may not send anything, charge anything, sign anything, or take any external or commercial action; the autonomous CEO layer and any live outreach or payments remain locked until Phase 10's separately authorized design — and outreach/payments additionally wait on the user's provider decisions (email, calendar, payment).
