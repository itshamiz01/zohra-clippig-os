# Zohra ClipOS

Zohra ClipOS is a safety-first, multi-tenant control plane for AI-assisted short-form media operations. This repository implements the first six gated phases of the 11-phase specification:

1. control-plane foundation;
2. secure media ingestion and transcription;
3. explainable clip-candidate discovery and human editorial review;
4. immutable editing plans, captions, composition and validated private rendering;
5. a Quality Council, structured Rights Guardian, independent human review and mandatory signed safety gates;
6. provider-neutral publishing, official YouTube OAuth/upload contracts and reconciliation-safe platform job control.

Version: `0.7.0` — phase-gated development release, **not a production release**.

## Implemented scope

### Phase 1 — retained foundation

- FastAPI control plane with structured errors, correlation IDs, security headers, request/rate limits, readiness and health checks
- tenant membership and role enforcement, short-lived signed sessions and Argon2 password hashing
- append-only audit evidence, durable jobs, bounded retries, approval contracts and a platform-admin global pause control
- provider, queue and storage boundaries with truthful `real_tested`, `mocked`, `disabled`, `blocked` and `unverified` states
- additive Alembic migrations and a responsive Next.js operator shell

### Phase 2 — retained ingestion and transcription

- streamed, rights-confirmed local audio/video upload with signature, FFprobe, size, duration, codec and dimension validation
- tenant-private checksummed storage and bounded, shell-free FFmpeg/FFprobe processing
- recoverable worker lifecycle with heartbeats, stale recovery, cancellation, retries and explicit failure/review categories
- provider-neutral transcription with bounded responses, strict timestamp validation and disabled-by-default behavior
- original/normalized text, language, confidence, speaker labels, segment/word timestamps, topics and exact long-content chunks
- URL ingestion remains explicitly disabled and performs no fetch

### Phase 3 — retained explainable candidate discovery

- deterministic, versioned planning from transcript evidence, source duration, timestamps, context and configurable duration targets
- 13 explainable weighted factors with evidence, applicability and explicit missing-data behavior; no virality promise
- states `proposed`, `needs_review`, `approved`, `rejected` and `superseded`
- reviewer-only decisions and immutable boundary revisions with reasons, lineage, rescoring and audit evidence
- optional provider-neutral model selection with strict schemas, option-ID allowlisting, cost preflight and deterministic fallback
- tenant-safe discovery APIs and `/discovery` evidence/review workbench

### Phase 4 — validated media rendering

- render-plan creation is accepted only for an existing tenant-scoped **approved** candidate and a ready, rights-confirmed source
- separately persisted immutable plan version containing approved bounds, linear source-time mapping, aspect ratio, dimensions, frame rate, crop behavior, safe area, captions, overlays, tenant-brand snapshot, audio transformation, codecs, output prefix and provenance
- seven bounded presets: TikTok-compatible, Reels-compatible, Shorts-compatible, general vertical 1080p/720p, square 1080p and landscape 1080p, plus bounded even-dimension custom output
- deterministic center crop or fit-with-padding; a smart-face request is transparently recorded as a center-crop fallback because face detection/tracking is not implemented
- Unicode SRT and burned-in captions with word/segment source timing, deterministic line/cue limits, HTML/ASS-override neutralization and bidirectional-control removal
- optional tenant-owner-configured text branding only when FFmpeg reports `drawtext`; logo uploads, third-party music and generative edits remain unavailable
- explicit speech loudness/true-peak handling, channel count and sample rate; normalization can also be disabled without changing the plan invisibly
- shell-free FFmpeg invocation with capability discovery, generated paths, memory/CPU/file limits, bounded diagnostics, timeout/process-group kill, cancellation checks and temporary-directory cleanup
- preflight duration/dimension/pixel/frame-rate/queue/disk limits, including space for the complete source copy, maximum output and configured reserve
- unique attempt output keys; source media is never overwritten; client idempotency keys cannot create duplicate plans
- source storage bytes are checked against the approved asset checksum and size before rendering
- post-render FFprobe checks for readable streams, duration tolerance, dimensions, frame rate, codecs, MP4 family format, audio layout and nonzero bounded size
- output records are created only after validation and include SHA-256, probe/renderer versions, validation evidence and complete provenance; private delivery rechecks storage bytes and immutable database checksums
- states `queued`, `validating`, `rendering`, `validating_output`, `succeeded`, `failed`, `cancelled` and `needs_review`, with progress, typed failures, per-tenant queue/concurrency locks, cancellation, bounded retry and stale recovery
- authenticated list/detail/cancel/retry/defect/preview/download/caption APIs and `/renders` control room with approved-candidate comparison, plan metadata, caption evidence, private browser-blob preview and defect ledger
- meaning- or boundary-changing defects block render retry and require a new candidate revision and approval path

### Phase 5 — Quality Council, Rights Guardian and mandatory gates

- eight separately persisted checks: technical integrity, transcript/audio consistency, caption quality, context integrity, visual composition, audio quality, duplication/campaign consistency and destination constraints
- every check records status, severity, structured evidence, confidence, validator source, version, remediation and hard-block state; unavailable and low-confidence evidence becomes human review rather than a silent pass
- deterministic failures remain authoritative; model evidence cannot replace a missing deterministic check or overrule a failure, and no quality model is enabled without real provider proof
- structured, scoped permission evidence records owner, human recorder/time, basis/reference/checksum, exact asset/candidate/render scope, platform, territory, duration, commercial/editing/AI permission, attribution, license terms, restrictions, campaign and six third-party-material clearance classes
- public availability is not an accepted permission basis; one render/platform/campaign grant is never inferred as a blanket grant
- expired, revoked, unknown, rejected, future-effective, unsupported-platform/territory and incomplete third-party rights block the gate; quality cannot compensate for rights violations
- server policy combines evidence only into `needs_human_review`, `approved`, `rejected` or `blocked`; rights, authorization, security, provenance and technical-integrity blocks are non-overridable
- independent reviewer role, different requester/reviewer identities, minimum reason, explicit acknowledgement of each uncertain check and append-only decision/audit records
- versioned HMAC-signed approval evidence with bounded expiry and exact destination/territory/output/rights/input hashes
- reusable pre-external-action service re-reads database state and private-object checksums at both `publish_job_acceptance` and `external_upload`; an earlier signature or preflight cannot bypass a fresh check
- generic jobs reject publish/upload/outreach/contract/payment-like kinds; Phase 5 preflight endpoints create no job and contact no platform
- `/quality` review workbench presents checks, deterministic/model distinction, scoped rights, missing evidence, policy explanation, remediation, stale/revoked state, decisions and verification history while avoiding raw identity-document storage

### Phase 6 — Publishing adapters, OAuth and provider job control

- provider-neutral contracts cover destination validation, least-privilege OAuth grants, resumable upload, progress, processing, visibility, metadata, thumbnails, provider identifiers, reconciliation and a sanitized Phase 7 analytics handoff
- YouTube uses the official server-side authorization-code flow with exact redirect URI, one-time hashed state, CSRF rejection, PKCE S256, offline access and the single `youtube.upload` scope
- access and refresh tokens plus resumable session URIs are stored only in context-bound AES-256-GCM envelopes; APIs/UI expose scope, expiry, status and remediation but no credential values
- refresh, revocation and reconnect states are explicit; missing scope, expiry, invalid grants, provider authorization failures and safe manual next steps are normalized
- Meta/Instagram, TikTok and LinkedIn adapters are explicitly disabled and are never described as integrated; the capability matrix distinguishes configuration, mocked contract proof and real OAuth/upload proof
- queue acceptance revalidates tenant/user authorization, active membership, exact connection/destination/scopes, approved render, fresh quality/rights gate, metadata, visibility approval, queue/daily budgets and platform constraints
- immediately before external upload, the worker repeats authorization, token, object integrity and quality/rights checks; a passed acceptance gate is not reusable authority
- pilot visibility defaults to private; public visibility is disabled by default and, if explicitly enabled, requires an independent approved request cryptographically bound to the exact release payload
- states are `queued`, `validating`, `uploading`, `processing`, `succeeded`, `failed`, `needs_reconciliation` and `cancelled`, with immutable sequenced events and sanitized provider metadata
- resumable sessions survive retries and restarts; 429 and typed provider failures retain retry guidance; uncertain mutating responses always enter reconciliation before a possible repeat upload
- generic jobs cannot create, retry or cancel publishing work around the publishing-specific authorization path
- `/publishing` presents truthful capability status, connection scope/expiry/reconnect state, private-first release controls, progress, visibility, failures, immutable events, safe retry/cancel and manual next steps

### Phase 7 — Analytics, experimentation and a bounded learning loop

- every succeeded publication's Phase 6 `clipos-analytics-handoff.v1` blob is now consumed by tenant-scoped analytics collection; the handoff reports `phase_7_status: implemented`
- metric snapshots persist an explicit availability state — `reported`, `missing`, `unavailable`, `permission_denied` or `stale` — and a database check constraint makes it impossible to store a value for a metric that was not reported: a missing metric is `NULL`, never an invented zero
- collection runs are durable background jobs (`analytics.collect`, creatable only through the analytics domain endpoint) with per-run counts, the serving adapter's truthful mode, and sanitized failure categories; snapshots dedup on (publication, metric, day-grained window, source) and refresh in place
- the analytics gateway mirrors publishing: a disabled-by-default adapter that records every canonical metric as unavailable, a deterministic fake adapter labelled `mocked` on every snapshot it produces, and a YouTube Analytics skeleton that is **unverified** — it makes no provider call and fails collection truthfully with `analytics_unverified` until an operator completes a real provider test with the `yt-analytics.readonly` scope
- performance queries expose per-publication latest metrics with availability counts, campaign rollups over `campaign_reference` (sums for additive metrics, view-weighted average view duration, means for percentages; nothing is summed that should not be), and a usage-cost summary whose attribution limits are stated in the response — cost events are per provider operation, not per publication
- predicted-vs-actual joins discovery score components (including `hook_clarity`) to collected metrics through the publication → render job → render plan → candidate lineage, and reports lineage gaps explicitly instead of fabricating a join
- experiments are tenant-scoped records with a validated canonical success metric, a guarded lifecycle (`draft → running → paused → completed`, `cancelled`) that requires at least one candidate- or publication-linked variant before running, and audited transitions
- the learning loop is deterministic rules v1 over stored, reported views only (duration, captions, posting-time and format group comparisons plus a hook-clarity inversion check), with conservative minimum sample sizes and effect thresholds; every recommendation cites its evidence (group statistics, publication ids, fingerprint) and is created `proposed`
- recommendations are never auto-applied: there is no apply path in Phase 7, approval/rejection requires the reviewer role and is audited, and the generator has no write path to rights, quality, gate, approval, policy/feature-flag or budget tables — a test proves those tables are byte-identical before and after generation, collection and review

## Intentionally not implemented

Phase 8 and later remain locked. Phase 7 does not implement lead discovery (opportunity hunting), outreach, contracting, payments, billing, autonomous client selection or executive action, and it does not apply recommendations automatically. Real YouTube Analytics collection is **not** implemented: the adapter is an unverified skeleton, no real analytics API call has been made, and no provider metric in this delivery is real evidence — disabled collection records `unavailable`, and the fake adapter's numbers are deterministic test fixtures labelled `mocked`. Meta, TikTok and LinkedIn publishing and analytics, S3 and URL ingestion remain unavailable. No code bypasses authentication, paywalls, CAPTCHA, robots policy or platform controls.

The YouTube publishing adapter has mocked provider-contract evidence only in this delivery. No real Google consent, test-channel upload, processing result or public post was performed, so it is **not** marked real-tested. A succeeded render, passing gate or mocked adapter response is not legal advice, production proof, platform-acceptance guarantee, reach claim or revenue claim.

## Quick start (local development)

Prerequisites:

- Python 3.11+
- Node.js 20.19+
- FFmpeg and FFprobe on `PATH`; required render filters/encoders are checked at runtime

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm --prefix apps/web ci

PYTHONPATH=apps/api .venv/bin/alembic upgrade head
make bootstrap ARGS="--slug local --tenant-name 'Local Studio' --email owner@example.com --display-name Owner"
```

Run the API and web application in separate terminals:

```bash
make api
make web
```

Open:

- `http://localhost:3000/media` — private media and transcript evidence
- `http://localhost:3000/discovery` — candidate discovery and review
- `http://localhost:3000/renders` — rendering queue, evidence, preview and defect controls
- `http://localhost:3000/quality` — rights evidence, eight-check council, review and preflight history
- `http://localhost:3000/publishing` — provider capability, OAuth grant and platform job control
- `http://localhost:8000/docs` — development OpenAPI

The global processing control starts paused. Only a platform administrator with tenant-owner authority can resume it, and must provide an audited reason. Protected environments prohibit the embedded worker.

## Rendering workflow

1. Ingest rights-confirmed local media and obtain a ready asset with verified timestamps.
2. Run discovery and have a reviewer approve the exact candidate boundaries.
3. Use **Create validated render** in Discovery, or paste the candidate UUID into `/renders`.
4. Select a preset, deterministic crop behavior, caption mode and explicit audio handling.
5. If allowed, enable only the tenant branding policy already configured by a tenant owner.
6. Submit a stable idempotency key. The server validates and stores the plan before queueing.
7. Inspect lifecycle progress and the approved-source/plan comparison.
8. Load the private preview only after `succeeded`; the browser fetches with an Authorization header and uses a temporary blob URL, never a bearer-token query string.
9. Report defects. Meaning or boundary changes cannot be retried against the old approval.

Example:

```bash
curl -X POST "http://localhost:8000/api/v1/candidates/$CANDIDATE_ID/render-plans" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "idempotency_key":"editorial-export-2026-10-31-001",
    "preset":"vertical_720p",
    "crop_strategy":"center_crop",
    "captions":{"mode":"both","style":"high_contrast_bottom"},
    "audio":{"normalization":"speech_loudness"},
    "include_tenant_branding":false,
    "video_codec":"h264",
    "audio_codec":"aac",
    "container":"mp4"
  }'
```

Use `GET /api/v1/renders`, `GET /api/v1/renders/{id}`, and the authenticated `/preview`, `/download` and `/captions` endpoints. Capability and tenant-brand policy endpoints are under `/api/v1/rendering`.

## Quality and rights workflow

1. An administrator records structured evidence through `/quality` or `POST /api/v1/rights-evidence`. An expiration or explicit no-expiration assertion and every material-clearance field are mandatory.
2. An editor runs `POST /api/v1/renders/{id}/quality-evaluations` with one exact evidence ID, destination, territory, campaign and idempotency key.
3. The service rechecks the current private object and persists all eight check records. Missing or invalid rights block; semantic and unmeasured audiovisual questions require human review.
4. A different reviewer examines source context, rights scope, every uncertain check, policy reasons and remediation. Approval requires a reason and explicit check acknowledgements.
5. Approval produces versioned signed evidence with a bounded expiry. It still publishes nothing.
6. Phase 6 invokes the server-side gate at publishing job acceptance and again immediately before external upload; both calls re-read current rights, provenance, defects, signature, expiry and storage integrity.
7. Revocation, expiration, output replacement, a new render attempt, transcript/candidate change, defect or signature alteration makes previous approval stale or denied.

The application stores operational permission references and optional evidence checksums, not raw contracts, identity documents or unnecessary personal records. Legal/privacy specialists must review ambiguous ownership, consent, territorial, employment, trademark, publicity, music, personal-data and sensitive-information questions. A passing result is not legal clearance.

## Publishing and OAuth workflow

Publishing is disabled by default. Before enabling YouTube, create an official Google OAuth **web application** client, register `CLIPOS_YOUTUBE_REDIRECT_URI` exactly, inject a distinct 32+ character `CLIPOS_PUBLISHING_TOKEN_ENCRYPTION_KEY`, client ID and client secret through an external secret manager, and keep `CLIPOS_PUBLISHING_PUBLIC_ENABLED=false` for private/unlisted verification.

```dotenv
CLIPOS_YOUTUBE_PUBLISHING_ENABLED=true
CLIPOS_PUBLISHING_PUBLIC_ENABLED=false
CLIPOS_PUBLISHING_TOKEN_ENCRYPTION_KEY=<external-secret-at-least-32-characters>
CLIPOS_YOUTUBE_CLIENT_ID=<google-oauth-client-id>
CLIPOS_YOUTUBE_CLIENT_SECRET=<external-secret>
CLIPOS_YOUTUBE_REDIRECT_URI=https://your-api.example/api/v1/publishing/oauth/youtube/callback
```

1. An administrator opens `/publishing`, verifies the exact callback and sole `youtube.upload` scope, and completes the one-time PKCE flow.
2. ClipOS validates the authorized channel and stores only encrypted token envelopes. The UI shows destination, scopes, expiry, refresh and reconnect status.
3. An editor prepares exact metadata and keeps visibility `private` for the pilot. Public release additionally requires the public feature flag and an independent approval bound to the unchanged payload.
4. A reviewer submits one approved render and Quality Council evaluation with a stable idempotency key. Queue acceptance performs all fresh gates and limits.
5. The worker repeats authorization, token and gate checks immediately before resumable transfer, records progress and polls provider processing.
6. If the final response may have been lost, the job enters `needs_reconciliation`. Use the publication-specific **safe retry** only; it probes the provider/session before any repeat transfer.
7. Treat the first private OAuth/upload/processing run as operator verification evidence. Do not mark the adapter real-tested or enable public release until the result and provider configuration have been independently reviewed.

The OAuth callback must be HTTPS in staging/production. API and worker processes must receive the same encryption key and provider configuration. Rotating the encryption key without an envelope migration makes existing grants unreadable; revoke/reconnect them under a documented rotation plan.

## Model selection configuration

Deterministic discovery is the safe default and needs no model credential:

```dotenv
CLIPOS_MODEL_PROVIDER=disabled
```

To enable an approved provider, inject the matching key/model at runtime, configure current input/output rates, and explicitly confirm those rates. Configuration is not proof of a live successful call. Test fixture adapters remain `mocked`; runtime status becomes `real_tested` only after a successful configured-adapter request.

Transcripts and operator hints are untrusted data. A discovery model may select only supplied deterministic option IDs. No model-assisted Phase 5 checker is enabled in this evidence environment. Any future model observation must remain schema-validated supplemental evidence: it cannot approve candidates or gates, overrule deterministic facts, invoke rendering, change authorization, publish or create an external action.

## Deployment topology and limitations

Protected environments require PostgreSQL, Redis, Alembic migrations and a separate worker. `docker-compose.yml` describes `postgres`, `redis`, `api`, resource-bounded `worker` and `web`; the API/worker image installs FFmpeg. Docker, a live PostgreSQL server and a live Redis server were unavailable for this gate, so Compose and live PostgreSQL/Redis behavior are **unverified**, not production-proven.

The in-memory development broker uses typed dispatch plus durable database recovery. Redis is required outside development/test. Local storage is real-tested but remains a single-host development/staging adapter; S3 is deliberately rejected until a reviewed adapter exists.

The locally tested static FFmpeg/FFprobe 7.0.2 build supports H.264/AAC rendering, crop/pad/fps/loudness and subtitle burn-in, but lacks `drawtext`. Text branding is therefore truthfully capability-gated in this environment. Docker’s distribution FFmpeg package was not runtime-tested here.

Never place bearer tokens, provider keys, private media, render outputs or transcripts in source control.

## Validation

```bash
PATH=/path/to/ffmpeg:$PATH PYTHONPATH=apps/api .venv/bin/ruff format --check apps/api
PYTHONPATH=apps/api .venv/bin/ruff check apps/api
PYTHONPATH=apps/api .venv/bin/mypy apps/api/clipos
PATH=/path/to/ffmpeg:$PATH PYTHONPATH=apps/api .venv/bin/pytest --cov=clipos --cov-branch
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web audit --audit-level=high
.venv/bin/pip-audit --progress-spinner off
```

Latest backend gate: **160 passed in 124.65s** with **75.02% branch-aware coverage** (70% enforced minimum), including 11 focused Phase 7 analytics/experiment/learning-loop cases and the retained genuine deterministic FFmpeg proof. Ruff and mypy pass on the `clipos` package. Fresh SQLite migration round trip/no-drift and offline PostgreSQL DDL contracts for the Phase 7 revision are recorded in [`docs/PHASE_7_HANDOFF.md`](docs/PHASE_7_HANDOFF.md). The web home page received static Phase 7 status copy only in this phase; frontend lint/typecheck/build were not re-run (dependencies are not installed in this checkout).

## Documentation

- [`docs/PHASE_7_HANDOFF.md`](docs/PHASE_7_HANDOFF.md) — Phase 7 gate evidence, limitations and Phase 8 stop condition
- [`docs/PHASE_5_HANDOFF.md`](docs/PHASE_5_HANDOFF.md) — Phase 5 gate evidence, limitations and Phase 6 stop condition
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — trust boundaries, worker flow and data model
- [`docs/SECURITY.md`](docs/SECURITY.md) — rendering threat model and controls
- [`docs/DATABASE.md`](docs/DATABASE.md) — schema and migration chain
- [`docs/INTEGRATION_MATRIX.md`](docs/INTEGRATION_MATRIX.md) — real/mock/disabled/unverified truth table
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — development and operator workflows
- [`docs/PHASE_4_HANDOFF.md`](docs/PHASE_4_HANDOFF.md), [`docs/PHASE_3_HANDOFF.md`](docs/PHASE_3_HANDOFF.md), [`docs/PHASE_2_HANDOFF.md`](docs/PHASE_2_HANDOFF.md) and [`docs/PHASE_1_HANDOFF.md`](docs/PHASE_1_HANDOFF.md) — historical accepted gates
