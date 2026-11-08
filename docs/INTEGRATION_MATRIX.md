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
