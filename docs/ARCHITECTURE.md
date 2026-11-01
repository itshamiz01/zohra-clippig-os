# Architecture — Phase 6

## Scope and invariants

Phase 6 retains the control plane, secure ingestion/transcription, explainable candidate review, validated private rendering and mandatory quality/rights gates, then adds provider-neutral OAuth, official YouTube upload contracts and reconciliation-safe platform jobs. The central invariants are:

> No bytes become a current render output unless a tenant-scoped approved candidate produced an immutable plan, bounded FFmpeg completed, FFprobe verified the artifact, storage checksummed it, and the database committed provenance.

> No future external-action checkpoint can pass unless current server state still matches one independently human-approved, versioned quality evaluation with valid scoped rights and a valid server signature. Stored signatures and earlier preflights are evidence, never standalone authorization.

A publishing job can reach only a configured official YouTube adapter after tenant/user authorization and both fresh release gates. Private is the pilot default; uncertain mutations reconcile before retry. The system creates no analytics learning loop, outreach, contract, payment, client-selection or autonomous commercial action. Phase 7 is not implemented.

## Runtime topology

```text
Browser
  │ HTTPS / same-origin API proxy
  ▼
Next.js operator dashboard
  │ Authorization: Bearer <short-lived token>
  ▼
FastAPI control plane ───── PostgreSQL (protected) / SQLite (development)
  │       │                         │
  │       ├── tenant-private object storage (local adapter now; S3 blocked)
  │       ├── FFmpeg/FFprobe capability/status inspection
  │       ├── Quality Council + Rights Guardian + signed gate service
  │       ├── one-time OAuth state + encrypted publishing grants/sessions
  │       └── typed queue enqueue + durable DB job/plan/publication transaction
  ▼
Redis queue (protected) / typed in-memory broker (development)
  ▼
Separate ClipOS worker (protected) / optional embedded worker (development)
  │
  ├── asset processing dispatcher
  ├── render processing dispatcher
  │     ├── materialize/check source
  │     ├── bounded FFmpeg child process
  │     ├── FFprobe output validation
  │     └── atomic private-object write + DB output/provenance commit
  └── publishing dispatcher
        ├── repeat authorization, grant and quality/rights checks
        ├── provider resumable upload + processing polling
        └── reconciliation before any uncertain repeat transfer
              │ HTTPS / OAuth 2.0
              ▼
        YouTube official API adapter (opt-in; live proof not completed)
```

Protected environments enforce PostgreSQL, Redis, migration-managed schema, a non-ephemeral secret and a separate worker. The embedded worker, SQLite and optional Redis readiness are development/test conveniences only.

## Trust boundaries

### Browser and dashboard

The dashboard is untrusted input. It can request presets, caption/audio modes and an idempotency key, but cannot submit storage paths, arbitrary codecs, arbitrary filter graphs, FFmpeg arguments, plan provenance, source bounds or approval state.

Private previews/downloads are fetched with an Authorization header into a browser blob. Bearer tokens never appear in media query parameters.

### API and plan service

The API is the authorization and validation boundary. It:

- reloads the authenticated user, active tenant and authoritative membership role on every request;
- requires editor authority for plan/cancel/retry, reviewer authority for defects and tenant-owner authority for branding policy;
- requires the global processing control to be running before plan creation/retry;
- accepts only an approved candidate belonging to the same tenant;
- reloads ready asset, verified metadata and transcript evidence;
- computes source bounds, output prefix, mapping, provenance and fallback disclosures server-side;
- checks runtime FFmpeg capability status before a plan is persisted;
- locks the tenant row when enforcing queued limits so concurrent PostgreSQL requests cannot bypass the cap;
- commits plan/background/render parents in explicit foreign-key order before caption rows;
- commits durable state before queue publication, allowing recovery if queue publication fails.

Plan records have no update endpoint. A replay with the same tenant/idempotency key and identical request returns the existing plan; a conflicting request returns 409.

### Queue and worker

`ClipOSWorker` dispatches only reserved typed kinds:

- `asset.process`
- `render.execute`

Generic job APIs cannot submit either reserved kind. Unknown messages are not interpreted as another workload. If an in-memory/Redis message is lost, recovery scans durable queued rows. PostgreSQL `FOR UPDATE SKIP LOCKED` protects recovery between workers.

At render start, the worker locks the tenant row and counts active `validating`, `rendering` and `validating_output` jobs. This serializes per-tenant concurrency admission under PostgreSQL.

### FFmpeg subprocess

FFmpeg and FFprobe are never invoked through a shell. All paths and arguments are discrete `create_subprocess_exec` entries. Source/output/caption/branding paths are server-generated under a mode-0700 temporary root. Filter paths are escaped separately for FFmpeg’s filter parser; tenant branding text is passed through a generated text file rather than interpolated into the filter expression.

Child processes receive:

- no stdin;
- a new process group;
- virtual-memory, CPU-time and output-file limits;
- two FFmpeg threads;
- bounded stdout/stderr readers;
- wall-clock timeout;
- cancellation monitoring;
- process-group kill on timeout, cancellation or task cancellation.

A `TemporaryDirectory` removes the source copy, SRT, branding text and output working file after every success/failure path.

### Private object storage

Keys are normalized relative POSIX paths, tenant-prefixed and resolved beneath the configured root. Absolute paths, empty/dot segments, `..`, NUL and backslashes are rejected. Writes use mode-0600 temporary files, `fsync` and atomic replace.

Materialization verifies stored metadata against copied bytes. Rendering additionally compares source size/SHA-256 with the immutable approved asset record. Delivery rehashes the object, verifies storage metadata, then compares checksum/size with the immutable render-output database record.

## Phase 4 render flow

### 1. Capability and policy discovery

`GET /api/v1/rendering/capabilities` reports actual FFmpeg/FFprobe versions, discovered encoders/filters, presets, real features, conditional features, unavailable features and limitations. No feature is inferred only from configuration.

`GET/PUT /api/v1/rendering/profile` reads or changes tenant text-branding policy. Only the policy is mutable; each plan snapshots the exact approved text/position/opacity/font size and profile identity/time.

### 2. Plan creation

`POST /api/v1/candidates/{candidate_id}/render-plans` performs, in order:

1. idempotency lookup and request-hash comparison;
2. global pause and rendering-enabled checks;
3. required FFmpeg capability check;
4. approved-candidate/tenant check;
5. source asset, verified metadata, transcript, permission and rights-evidence checks;
6. duration/source-bound checks;
7. preset/custom dimension, even-dimension, pixel and frame-rate checks;
8. caption capability/layout/timing/density checks;
9. branding capability and tenant-policy check;
10. tenant queue lock/cap check;
11. immutable plan, caption rows, background job, render job and audit record commit;
12. typed queue publication.

The plan stores a linear mapping:

```text
source_ms = output_ms + source_start_ms
playback_rate = 1.0
```

No speed change, reordering or generative insertion exists.

### 3. Validation and materialization

The worker moves `queued → validating`, checks available disk for:

```text
configured reserve + approved source size + configured maximum output size
```

It copies the private source to a unique temporary directory, verifies copy checksum/size against storage metadata and approved asset provenance, then probes the source again. Missing video/audio or a changed duration is explicit failure/review evidence.

### 4. Composition

The renderer applies one deterministic video path:

- center crop: scale-to-fill, centered crop, square pixels, configured FPS;
- fit pad: scale-to-fit, black pad, square pixels, configured FPS;
- smart-face request: immutable disclosure plus center-crop fallback; no tracking claim.

Captions use bounded word timestamps when available and segment timestamps otherwise. Text is normalized, control and bidi-control characters are removed, ASS braces are neutralized, HTML is escaped in SRT, and line/cue/count limits are enforced. Burn-in uses the FFmpeg `subtitles` filter and safe-bottom margin; sidecar SRT can be delivered independently.

Audio maps the first required source audio stream to AAC with explicit sample rate/channels. The default speech mode applies bounded one-pass `loudnorm` with plan-stored LUFS/true-peak targets; `none` preserves source loudness while still applying the explicit output codec/layout.

Optional branding is tenant-configured text only and requires `drawtext`. Logo/image overlays, third-party music and arbitrary assets are not accepted.

### 5. Output validation and commit

After FFmpeg exits successfully, the job enters `validating_output`. FFprobe and file checks require:

- nonzero size at or below the configured bound;
- readable video and audio streams;
- exact plan dimensions;
- duration within configured tolerance;
- H.264 video and AAC audio;
- MP4-family container;
- frame rate within 0.5 FPS;
- exact configured audio channel count and sample rate.

Only then does storage create a unique attempt object and optional SRT object. The output record persists attempt number, checksums, size, stream metadata, validation report, FFprobe/renderer versions and source/plan/caption/branding/audio provenance. Cancellation is checked again before success commits; orphan objects are removed if commit/finalization fails.

### 6. Lifecycle and recovery

```text
queued
  ├─ cancel ───────────────────────────────────────► cancelled
  ▼
validating
  ├─ cancel/process cancellation ─────────────────► cancelled
  ├─ safe failure ─────────────────────────────────► failed
  ├─ review-class source failure ─────────────────► needs_review
  ▼
rendering
  ├─ progress 20–88%
  ├─ timeout/process/output-limit/failure ─────────► failed
  └─ cancel ───────────────────────────────────────► cancelled
  ▼
validating_output
  ├─ output mismatch ──────────────────────────────► needs_review
  ▼
succeeded
  └─ human defect ─────────────────────────────────► needs_review
```

Retries are manual, auditable and bounded by the immutable job budget. Old current outputs are retired before retry. A meaning/boundary defect permanently blocks retry against that plan and requires a new reviewed candidate path.

Stale worker recovery categorizes an interrupted active render, fails it safely, and requeues only when retryable/budgeted according to the recovery contract. Durable records remain the source of truth.

## Data ownership and immutability

- Every Phase 4 table carries `tenant_id` and every service query applies it.
- A render plan references one candidate, asset and transcript and stores the candidate boundary revision.
- Caption rows belong to exactly one plan and preserve source/output timestamp pairs.
- A render job belongs to one plan and one durable background job.
- Outputs are append-only attempts; one may be `is_current`.
- Defects are append-only human reports attached to the exact output.
- The source asset and approved candidate are never overwritten by rendering.

## API surface added in Phase 4

| Method | Route | Minimum authority | Purpose |
|---|---|---:|---|
| GET | `/api/v1/rendering/capabilities` | viewer | Actual renderer capability/limitation disclosure |
| GET | `/api/v1/rendering/profile` | viewer | Tenant branding policy |
| PUT | `/api/v1/rendering/profile` | owner | Change tenant text-branding policy |
| POST | `/api/v1/candidates/{id}/render-plans` | editor | Validate/store immutable plan and queue job |
| GET | `/api/v1/renders` | viewer | Tenant list/filter/status |
| GET | `/api/v1/renders/{id}` | viewer | Plan, captions, job, attempts and defects |
| POST | `/api/v1/renders/{id}/cancel` | editor | Cancel queued/active work |
| POST | `/api/v1/renders/{id}/retry` | editor | Queue a bounded retry |
| POST | `/api/v1/renders/{id}/defects` | reviewer | Move succeeded output to needs review |
| GET | `/api/v1/renders/{id}/preview` | viewer | Verified private inline media |
| GET | `/api/v1/renders/{id}/download` | viewer | Verified private attachment |
| GET | `/api/v1/renders/{id}/captions` | viewer | Verified private SRT attachment |

## Dashboard

`/renders` includes:

- authenticated tooling/capability truth;
- audited global pause/resume for platform-admin owners;
- approved candidate handoff from `/discovery`;
- preset/crop/caption/audio/branding plan controls;
- queue filter, progress, state and failure evidence;
- source candidate versus plan-boundary comparison;
- private blob preview and download;
- plan version, source map, captions, limitations, output checksum/probe metadata and JSON provenance;
- cancel, bounded retry and reviewer defect controls.

## Phase 5 quality and release-gate flow

### 1. Structured rights assertion

`POST /api/v1/rights-evidence` is admin-restricted and accepts no free-form blanket approval. The strict schema requires an existing tenant asset and an explicit `asset`, `candidate` or `render` scope. Render scope is resolved back to its exact candidate and asset. Evidence records:

- source owner, recorder UUID and permission timestamp;
- basis and operational evidence reference plus optional SHA-256;
- effective date and expiration or explicit no-expiration assertion;
- exact destination platforms and ISO territory codes or `WORLDWIDE`;
- commercial, editing and AI-processing permission;
- attribution requirement/text, license terms and restrictions;
- optional campaign scope;
- explicit states for music, footage, logos, likeness/voice, personal data and sensitive information.

There is no `publicly_available` basis. Evidence cannot be edited through an API: corrections create a new record. Revocation is an explicit admin action with actor, time, reason and audit event. Expiration is evaluated against current time rather than cached as a historical success.

### 2. Council evaluation

`POST /api/v1/renders/{id}/quality-evaluations` reloads the exact tenant render, immutable plan, current output, candidate, asset, transcript, source segments, caption rows, defect ledger, selected rights row and private storage metadata. It computes a protected-input hash over these records and persists eight independent check rows:

1. technical integrity;
2. transcript/audio consistency;
3. caption quality;
4. context integrity;
5. visual composition;
6. audio quality;
7. duplication/campaign consistency;
8. destination constraints.

Each row contains `status`, `severity`, structured `evidence`, `confidence`, `check_version`, `remediation`, `hard_block` and `evidence_source`. Technical checksum/provenance, boundary and fabricated-transcript-lineage failures are deterministic. Acoustic word accuracy, speaker intent, visual framing and perceptual audio quality are honestly marked for human review because Phase 5 has no independently validated audiovisual model for them.

The aggregator requires all eight deterministic rows. Supplemental model evidence cannot replace one, erase a failure or authorize an outcome. No Phase 5 model-assisted checker is enabled in the validated environment; this avoids converting mocked provider output into production proof.

### 3. Rights Guardian and policy combination

The Rights Guardian never derives permission from an upload checkbox, a public URL, another clip or another platform. It requires exact source/scope match and checks current status, effective/expiration/revocation time, platform, territory, campaign, three use permissions, attribution and every material clearance.

The policy combines evidence into only:

```text
blocked             rights/auth/security/technical/provenance hard failure
rejected            remediable deterministic quality failure
needs_human_review  semantic, unavailable or low-confidence evidence; also the all-pass pre-release state
approved            independent bounded human decision only
```

Rights, authorization, security, technical integrity and source/context provenance hard blocks cannot be approved. Quality scores never compensate for rights violations.

### 4. Independent human decision

The decision route requires reviewer authority and a different user from the evaluation requester. Approval requires:

- automated outcome exactly `needs_human_review`;
- no failed or hard-blocked check;
- currently valid rights;
- unchanged protected-input hash;
- explicit acknowledgement of every `needs_review`, `unavailable` or low-confidence check;
- a bounded nonblank reason.

Reject and request-revision decisions become `rejected`; remediation requires a new evaluation. Decision records are append-only and audit events retain reason and transition. A service call marked as a model actor is rejected before policy evaluation.

### 5. Signed evidence and stale-input defense

Approval stores canonical `clipos-gate-approval.v1` evidence containing evaluation/decision/reviewer, exact render/output/rights IDs, destination, territory, input/evidence hashes, policy versions, attribution and bounded expiry. The server signs it with HMAC-SHA-256 using a context-separated runtime secret.

The signature is not a bearer token. `verify` re-reads current rows and private-object metadata, recomputes the protected-input hash, confirms the output is current and the render succeeded, reruns rights timing/scope and technical-integrity checks, verifies signature and expiry, and records a signed short-lived verification result. It accepts an exact checkpoint only:

- `publish_job_acceptance`; or
- `external_upload`.

Phase 6 must call the service immediately at both points. It must not trust a client-supplied signature or a prior successful verification. Revocation, expiration, new render attempt, candidate/transcript/caption/defect change, object replacement or signature mutation denies the call.

### 6. Gate service remains non-distributing

The Phase 5 verification API still records gate evidence only; it never uploads. Phase 6 calls that service from a separate publishing-specific path. Generic job creation continues to reject publish, posting, external-upload/delivery, social, outreach, contract and payment-like kinds, so callers cannot bypass publication authorization, idempotency or reconciliation.

## Phase 5 API surface

| Method | Route | Minimum authority | Purpose |
|---|---|---:|---|
| POST | `/api/v1/rights-evidence` | admin | Record one scoped permission assertion |
| GET | `/api/v1/rights-evidence` | reviewer | Review tenant evidence, optionally filtered |
| GET | `/api/v1/rights-evidence/{id}` | reviewer | Inspect one evidence record |
| POST | `/api/v1/rights-evidence/{id}/revoke` | admin | Revoke with reason and audit evidence |
| POST | `/api/v1/renders/{id}/quality-evaluations` | editor | Persist eight checks and policy outcome |
| GET | `/api/v1/quality-evaluations` | reviewer | Filter review queue |
| GET | `/api/v1/quality-evaluations/{id}` | reviewer | Rights/check/policy/decision/verification panel |
| POST | `/api/v1/quality-evaluations/{id}/decisions` | reviewer | Independent approve/reject/revision decision |
| POST | `/api/v1/quality-evaluations/{id}/verify` | reviewer | Exercise a fresh future-checkpoint contract; no external action |

## Phase 5 dashboard

`/quality` provides role-aware forms and a review panel for:

- scoped rights references without raw document upload;
- exact render/destination/territory/campaign evaluation;
- eight status/severity/confidence cards with deterministic/model/human source badges;
- hard blocks, missing documents, policy explanation and remediation;
- material clearances, attribution, expiration and revocation state;
- explicit uncertain-check acknowledgement and reasoned human decisions;
- signature/expiry/stale state and decision/preflight audit history;
- safe preflight demonstrations that explicitly create no job or upload.

## Phase 6 publishing flow

### 1. Provider boundary and capability truth

`PublishingAdapter` defines OAuth URL/exchange/refresh/revoke, destination validation, upload initiation/chunks, session probing, processing status, thumbnail handling and reconciliation. The registry contains exactly one adapter per known provider. YouTube uses the official implementation only when explicitly enabled with complete configuration; otherwise it is disabled. Meta, TikTok and LinkedIn always use `DisabledPublishingAdapter` in Phase 6.

The API capability response separates configured state, feature support, required configuration/scopes, mocked contract evidence, real OAuth verification and real upload verification. Provider-contract tests never set the real-verification fields.

### 2. One-time OAuth and encrypted grants

An administrator starts OAuth. The server generates a high-entropy state and PKCE verifier, stores only the state digest and encrypts the verifier, returns Google’s official authorization URL with exact callback, offline access, prompt/consent and the sole `youtube.upload` scope, then accepts the state exactly once. The callback rejects unknown, expired, consumed or provider-mismatched state before code exchange.

After exchange, granted scopes are checked rather than assumed, the authorized channel is validated, and tokens are stored in an AES-256-GCM envelope bound to tenant and connection IDs. Refresh, revocation and reconnect states are explicit. Browser/API responses never expose token or upload-session values.

### 3. Acceptance gate and durable publication

A reviewer submits one exact connection, render job, quality evaluation, idempotency key, visibility and metadata payload. In one service boundary ClipOS verifies:

- active tenant membership and permitted human role;
- connected destination, required scope and supported capabilities;
- succeeded current render, current output and private-object checksum/size;
- fresh `publish_job_acceptance` quality/rights verification;
- private/unlisted default or an exact independent public approval;
- metadata/platform bounds, active/queued concurrency and daily budgets;
- tenant-unique canonical idempotency hash.

Only then are the publication, background job and initial immutable event committed and queued. Generic job routes reserve the publishing kind and cannot cancel/retry around this service.

### 4. Pre-upload gate, resumable execution and processing

The publishing worker re-reads the publication/connection/requester, confirms active authorization, obtains or refreshes a usable grant, verifies object integrity and invokes the fresh `external_upload` gate. It starts or resumes a provider session, encrypts the session URI, streams bounded chunks, records committed offsets and sanitized progress, then polls provider processing. Provider IDs, URL, status and selected safe metadata are stored; raw provider payloads and credentials are not.

### 5. Uncertainty and restart recovery

Timeout or provider 5xx during a mutating request is not treated as ordinary failure. The job enters `needs_reconciliation`; safe retry probes the known provider ID or resumable session first. `FOUND` continues processing/succeeds, `IN_PROGRESS` preserves the committed offset, `ABSENT` can safely requeue under the same attempt/session rules, and `UNKNOWN` remains reconciliation-required.

Stale `validating`, `uploading` or `processing` jobs with a provider ID/session are recovered to reconciliation after worker restart. Jobs with no possible remote mutation can return to the normal queue. Immutable event sequence allocation flushes each prior event so reconciliation and success in one transaction cannot collide.

## Phase 6 API surface

| Method | Route | Minimum authority | Purpose |
|---|---|---:|---|
| GET | `/api/v1/publishing/capabilities` | authenticated | Truthful provider capability/evidence matrix |
| POST | `/api/v1/publishing/oauth/{provider}/start` | admin | One-time PKCE authorization start |
| GET | `/api/v1/publishing/oauth/{provider}/callback` | one-time state | Exchange/validate and store encrypted grant |
| GET | `/api/v1/publishing/connections` | authenticated | Tenant connection status/scope/expiry |
| POST | `/api/v1/publishing/connections/{id}/revoke` | admin | Provider/local revocation and audit |
| POST | `/api/v1/publishing/public-approvals` | editor | Request approval bound to exact public payload |
| POST | `/api/v1/publishing/publications` | reviewer | Run acceptance gates and queue one job |
| GET | `/api/v1/publishing/publications` | authenticated | Tenant platform job queue |
| GET | `/api/v1/publishing/publications/{id}` | authenticated | Exact job, progress and remediation |
| GET | `/api/v1/publishing/publications/{id}/events` | authenticated | Immutable sequenced event history |
| POST | `/api/v1/publishing/publications/{id}/cancel` | editor | Safe bounded cancellation request |
| POST | `/api/v1/publishing/publications/{id}/retry` | editor | Reconciliation-aware retry only |

## Phase 6 dashboard

`/publishing` provides a role-aware capability matrix, server-side token boundary explanation, connection scope/expiry/reconnect/revoke controls, private-first release form, exact public-approval request, queue filters, byte/progress display, provider IDs/URLs, failure/manual guidance, immutable events and safe retry/cancel controls. It never renders token values or claims that mocked tests are live provider proof.

## Honest limitations

- Local SQLite/storage/in-memory queue and static FFmpeg were real-tested; they are not a production topology.
- PostgreSQL DDL is offline contract-tested only; no live PostgreSQL lock/concurrency run was possible.
- Redis/Compose are configuration-reviewed only; Docker was unavailable.
- YouTube HTTP/OAuth/upload contracts are mocked and the implementation is opt-in. No real Google consent, channel validation, upload, processing, quota or public release was run in this evidence environment.
- Meta, TikTok and LinkedIn publishing remain disabled, not placeholders presented as integrations.
- The tested static FFmpeg 7.0.2 lacks `drawtext`, so text branding is unavailable in that runtime. Subtitle burn-in is real-tested.
- No face detection/tracking, smart reframing, image/logo overlay, third-party music ingestion or generative edits exists. Phase 6 can publish only through the gated YouTube path; it does not acquire or legally adjudicate material.
- Context, visual and audio perceptual checks require human review because no real-tested audiovisual quality model is enabled.
- Destination profiles are conservative, versioned internal checks; platforms change rules and acceptance is never guaranteed.
- A passing gate is not legal advice or legal clearance. Legal/privacy review remains mandatory for ambiguous rights, consent, trademark, publicity, employment, territorial and personal/sensitive-data questions.
