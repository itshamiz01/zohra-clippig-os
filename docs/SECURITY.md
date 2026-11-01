# Security model — Phase 6

## Security posture

Zohra ClipOS is a phase-gated development system, not a production release. Phase 6 permits a configured, evidence-gated YouTube upload through an official adapter. It defaults pilots to private, requires two fresh server-side release gates and treats uncertain remote mutation as reconciliation work—not ordinary retry. Live provider behavior remains unverified in this delivery.

The design assumes:

- browser/client input is hostile;
- uploaded media and transcript text are hostile data;
- FFmpeg/FFprobe process untrusted binary formats and require containment;
- tenant identifiers, object UUIDs and storage keys can be guessed;
- worker messages can be delayed, duplicated, reordered or lost;
- provider/configuration presence and mocked HTTP responses are not evidence that an integration works;
- OAuth redirects, authorization codes, tokens, provider errors and resumable session URIs are sensitive/untrusted inputs;
- provider requests can rate-limit, fail after remote mutation, return malformed metadata or lose a final success response;
- workers can restart after a remote mutation but before local commit;
- a validated output can later be corrupted in storage;
- permission can be unknown, over-broad, expired, revoked or valid for a different clip/platform/territory;
- model confidence and client-supplied approval claims are untrusted;
- approval signatures and earlier successful preflights can be replayed after protected state changes;
- human approval is required but can still contain mistakes, so provenance, reasons, expiry, fresh checks and remediation remain mandatory.

## Authorization

### Authentication

- short-lived signed JWTs with issuer/audience validation;
- bearer token required for every media/discovery/render route;
- token subject, tenant and role are reloaded against active database records per request;
- a stale/altered token role is rejected;
- Argon2 password hashes;
- bearer tokens are retained only in browser session storage by the local dashboard.

### Role boundaries

| Action | Minimum role / extra condition |
|---|---|
| Inspect capabilities, render list/detail, preview/download/captions | Viewer |
| Create plan, cancel, retry | Editor |
| Request an exact render quality evaluation | Editor |
| Report a render defect; inspect rights/gates; make an independent gate decision; invoke non-distributing preflight | Reviewer |
| Inspect publishing capabilities, connections, queue, progress and events | Authenticated tenant member |
| Request exact public approval; safe cancel/retry a publication | Editor |
| Accept an external publication after fresh gates | Reviewer |
| Start/revoke an official provider OAuth connection | Admin |
| Record or revoke structured permission evidence | Admin |
| Change tenant branding policy | Owner |
| Pause global processing | Admin route + platform-admin flag |
| Resume global processing | Owner route + platform-admin flag |

The service layer rechecks platform-admin authority for the global switch. The dashboard only exposes the switch to a principal that is both owner and platform admin.

### Tenant isolation

- every Phase 2–6 domain query includes authenticated `tenant_id`;
- plan, gate and publication creation resolve candidate/asset/transcript/render/output/rights/connection inside that tenant;
- render, rights, evaluation, decision, verification, connection, publication and event APIs cannot authorize by UUID alone;
- the OAuth callback resolves tenant/user only from a valid one-time state record, never caller query data;
- each storage key is rooted beneath an authenticated tenant directory;
- tests use two tenants to prove list/detail/idempotency and rights/evaluation/verification/publication isolation.

## Approval boundary

A client cannot set candidate state or source bounds during plan creation. The service accepts only `CandidateState.APPROVED` and copies exact approved boundaries/revision into an immutable plan.

A succeeded render is not a release approval. Defects that alter meaning or boundaries:

- move the render to `needs_review`;
- set an immutable defect flag;
- block retry against that plan;
- require a new candidate/review path.

Phase 5 adds an independent operational release gate; Phase 6 is the only component allowed to consume it for publishing and must call it fresh at acceptance and pre-upload. The evaluator and reviewer must be different users. Approval is impossible for a blocked/rejected automated outcome, failed deterministic check, hard block, stale input or invalid rights. Every uncertain check requires explicit acknowledgement and every decision requires a reason. AI/model actors are rejected as decision-makers.

Public visibility adds a second approval class. It is disabled by default and, when enabled, binds the exact destination connection, render, quality evaluation, metadata, audience flag, category, visibility and idempotency key. Any change invalidates the payload hash; the publication requester cannot substitute a generic or stale approval.

## OAuth, token and provider boundary

### OAuth CSRF and redirect controls

- server-generated high-entropy state is stored only as a SHA-256 digest;
- the PKCE verifier is encrypted and only its S256 challenge reaches Google;
- state is provider-bound, expires quickly and is consumed exactly once, including denial callbacks;
- the callback redirect URI is configuration-derived and sent unchanged in authorization and exchange requests;
- staging/production rejects an active publishing configuration with non-HTTPS provider/callback URLs;
- only the `youtube.upload` scope is requested and the returned granted scope is validated;
- the authorized channel is validated before a connection becomes usable.

### Credential envelopes

Access tokens, refresh tokens and resumable upload session URIs use versioned AES-256-GCM. The key is derived from at least 32 characters of runtime secret material; protected environments require a distinct publishing encryption key when publishing is active. Associated data binds each envelope to its state, tenant/connection or tenant/publication context. Copying ciphertext between rows fails authentication.

Credential fields are excluded from response schemas, audit payloads and sanitized provider metadata. Structured log redaction covers token/secret/authorization keys, bearer values and OAuth secrets embedded in URLs/messages. Provider errors are normalized to safe categories rather than persisted raw.

### Outbound provider safety

The adapter uses fixed configured official base URLs, bounded HTTP timeouts, strict JSON-object parsing and an HTTPS resumable-session downgrade check. It never accepts a client-provided provider URL or arbitrary HTTP headers. Content is read from tenant-private checksummed storage; the browser never supplies upload bytes to the provider adapter directly.

### Duplicate and uncertainty defense

Tenant idempotency is backed by a canonical request hash and a database uniqueness constraint. A 429 can be retried only after provider guidance. A network loss or provider 5xx during mutation becomes `needs_reconciliation`; safe retry probes provider ID/session before another transfer. Worker restart recovery makes the same distinction based on whether remote success is possible. Generic job routes reserve the publishing kind and reject bypass cancel/retry.

## Strict plan input

Pydantic schemas use `extra="forbid"`. The request can select only allowlisted:

- presets or bounded even custom dimensions;
- crop strategy;
- caption mode/style and bounded density overrides;
- explicit audio normalization/targets;
- tenant-brand inclusion flag;
- H.264/AAC/MP4 output.

Clients cannot provide:

- source/output paths;
- FFmpeg arguments or filter graphs;
- arbitrary codec/container names;
- source timestamps;
- caption timestamps/text;
- branding text/files;
- music or third-party assets;
- output/provenance metadata.

Unknown path-like fields are rejected. Idempotency keys are pattern/length bounded and are never used in filesystem paths.

## Storage and path safety

Local storage:

- rejects absolute, empty, `.`, `..`, NUL and backslash key forms;
- resolves every target beneath `<root>/<tenant UUID>`;
- uses mode-0700 directories and mode-0600 temporary files;
- writes object bytes and metadata through `fsync` + atomic replace;
- validates metadata structure, nonnegative size and lowercase 64-character SHA-256;
- verifies object bytes on copy/get/path delivery.

Render output prefixes and attempt filenames are server-generated from UUIDs. Temporary source/output names are fixed server names inside a unique mode-0700 directory outside object storage.

Source materialization must match three pieces of evidence:

1. copied bytes match storage metadata;
2. copied size matches the approved asset record;
3. copied SHA-256 matches the approved asset record.

Private delivery must match storage bytes/metadata and the immutable render-output checksum/size. This catches a file-only modification and a file-plus-local-metadata replacement.

Local storage is not hardened multi-host object storage and remains a documented deployment limitation.

## FFmpeg/FFprobe containment

### No shell execution

Both generic media operations and rendering use `asyncio.create_subprocess_exec`. No shell string or `shell=True` exists. The final security grep found only `create_subprocess_exec` in production media tooling; fixed trusted test fixture binaries use `subprocess.run` arrays.

### Capability checks

Startup/tool checks inspect actual:

- FFmpeg and FFprobe versions;
- filters: scale, crop, pad, FPS, loudnorm, subtitles and drawtext;
- encoders: libx264 and AAC.

A plan cannot queue if base rendering capabilities are absent. Caption burn-in and branding have additional feature checks. Missing capabilities are explicit 4xx/5xx errors and dashboard limitations, never mocked success.

### Process bounds

Each child receives:

- `stdin=DEVNULL`;
- `start_new_session=True`;
- address-space (`RLIMIT_AS`) cap;
- CPU-time (`RLIMIT_CPU`) cap;
- output-file (`RLIMIT_FSIZE`) cap;
- bounded stdout/stderr readers;
- configured wall-clock timeout;
- process-group SIGKILL on timeout/cancellation;
- no overwrite (`-n`);
- two FFmpeg threads.

Compose adds worker PID, memory and CPU limits, but Docker was unavailable to runtime-test those declarations.

### Logs

FFmpeg diagnostics are length-bounded, NUL-stripped, whitespace-normalized and source paths are redacted before warning logs. API failures expose safe category/messages rather than raw media/tool output.

### Temporary cleanup

A context-managed unique temporary directory holds source copy, generated SRT, tenant text file and output. It is removed on success, expected failure, timeout, cancellation and unexpected exception. Objects stored before final database success are tracked and deleted on failure.

## Caption and overlay safety

Transcript text is untrusted data, not instructions.

Caption construction:

- removes ASCII control characters;
- removes Unicode bidirectional controls that could reorder/spoof visible text;
- neutralizes `{`/`}` into visible full-width punctuation so FFmpeg/libass cannot interpret ASS overrides;
- replaces source line breaks with bounded layout-controlled breaks;
- HTML-escapes SRT output;
- enforces cue count, one/two-line limit, character width and cue duration;
- clips all cue timestamps to approved source bounds.

Caption paths are server-generated and escaped for the FFmpeg filter parser.

Branding text is owner-configured, bounded to 80 normalized characters and passed through a text file. A client can only request inclusion of the current tenant policy. Arbitrary logo/image upload is not implemented.

## Resource exhaustion controls

Before queueing:

- maximum render duration;
- width/height and even H.264 dimensions;
- pixel count;
- frame rate;
- caption cue/line/character limits;
- per-tenant queued cap under tenant-row lock.

Before source copy:

- configured free-space reserve;
- complete approved source size;
- complete configured maximum output size.

During processing:

- per-tenant active cap under tenant-row lock;
- process CPU/address-space/file limits;
- wall timeout and output/log bounds;
- bounded frame dimensions/FPS and two encoding threads;
- optional Compose worker limits.

The development in-memory rate limiter is process-local. Protected environments require Redis-backed rate limiting/queue readiness.

## Queue, replay and race controls

- `asset.process` and `render.execute` are reserved internal kinds;
- generic job creation rejects reserved kinds;
- one dispatcher routes by exact kind;
- durable background/render rows are committed before enqueue;
- duplicate messages observe non-queued state and no-op;
- tenant/idempotency uniqueness plus request hash prevents conflicting replays;
- explicit parent flushes avoid cross-dialect foreign-key-order assumptions;
- queued and active tenant caps are serialized using a tenant-row lock under PostgreSQL;
- retry is bounded, audited and cannot bypass a meaning/boundary defect;
- cancellation is checked before rendering, during FFmpeg, before output validation and before success commit;
- stale active work has explicit recovery/failure handling.

Live PostgreSQL/Redis concurrency behavior remains unverified because those services were unavailable.

## Output validation and integrity

FFmpeg exit code alone never means success. The worker requires:

- output exists and has a positive bounded size;
- FFprobe parses it;
- video and audio streams exist;
- exact dimensions;
- duration within tolerance;
- H.264/AAC codecs;
- MP4-family format;
- frame rate within tolerance;
- expected audio channels/sample rate.

Only then is the private object stored and a `render_outputs` row committed. The row records checksum, stream metadata, FFprobe/renderer versions, validation details and complete source/plan provenance.

Preview/download/caption routes authorize tenant access, require the current validated output, recheck object integrity and send `Cache-Control: private, no-store` plus `X-Content-Type-Options: nosniff`.

## HTTP/browser controls

Retained controls include:

- trusted host validation;
- explicit CORS allowlist and no credentialed wildcard;
- correlation ID and bounded request body;
- rate limit;
- CSP/security headers, `X-Frame-Options: DENY`, `nosniff`, referrer/permissions policies;
- production API docs disabled;
- no bearer tokens in URLs;
- no anonymous media route;
- same-origin Next.js API proxy.

## Secrets and supply chain

- secrets use environment injection and `SecretStr`;
- status APIs expose names/configured state only, never values;
- `.dockerignore` excludes `.env`, VCS data, private runtime media/databases, caches, dependencies, builds and archives;
- `.gitignore` excludes runtime secrets/media/build output;
- no provider key is required for deterministic discovery or rendering;
- `pip-audit` reported no known third-party Python vulnerabilities (the local unpublished project is skipped by design);
- `npm audit --audit-level=high` reported zero vulnerabilities;
- secret-pattern scans found no private keys or common live-token forms.

These scans are point-in-time evidence, not a substitute for continuous dependency monitoring or signed container provenance.

## Rights Guardian

The upload-time permission checkbox and note remain provenance inputs but never satisfy the release gate. Phase 5 requires a separately recorded, administrator-attributed rights row with exact scope, platform, territory and duration.

Hard blocks include:

- no structured evidence, unknown/rejected status or future-effective permission;
- expiration or revocation;
- wrong asset/candidate/render, platform, territory or campaign;
- commercial, editing or AI-processing permission denied;
- required attribution without text;
- any missing, unknown, restricted or malformed music, footage, logo, likeness/voice, personal-data or sensitive-information state.

There is no public-availability permission basis. Public-domain claims require an explicit assessment reference and remain subject to legal review. A clip/platform grant does not flow to another clip/platform. Revocation changes the protected-input hash and is separately checked by current time/state before every preflight.

The system stores an operational owner label, evidence reference and optional checksum—not raw contracts, government IDs, biometric templates or unnecessary personal documents. Reviewer routes are role-restricted. Operators must use external approved legal/privacy document systems where required and reference them minimally.

## Deterministic/model boundary

All eight persisted categories require a deterministic result row. Model evidence, if added later, is supplemental only and cannot:

- substitute for a missing check;
- change a deterministic failure;
- assert permission or consent;
- approve its own risky action;
- invoke the gate or an external executor.

No Phase 5 model-assisted quality checker is enabled or claimed as real-tested. Semantic context, acoustic transcript accuracy, visual framing and perceptual audio quality intentionally enter human review where local deterministic evidence is insufficient.

## Signed approval and replay defense

Approval evidence is canonical, versioned, destination/territory/output/rights bound, time-bounded and HMAC-SHA-256 signed using the runtime secret with a separate context. It contains no secret and is not accepted from the client as authority.

Every `verify` call reloads database rows and object metadata, recomputes the protected-input hash, validates current output/render state, reruns rights timing/scope and technical integrity, and checks approval signature/expiry. Both accepted and denied attempts are persisted and audited. Short verification validity is informative only; Phase 6 must call the service again immediately before upload.

## External-action controls

- music and arbitrary third-party asset ingestion remain disabled even though clearance state can be recorded;
- URL acquisition remains disabled;
- only the publishing-specific YouTube adapter path may hold an encrypted social grant or upload; no browser/public media URL is introduced;
- Meta, TikTok and LinkedIn remain disabled and no general scheduler exists;
- generic job names containing publish/post/upload/delivery/social/external/outreach/contract/payment terms are rejected;
- the Phase 5 preflight endpoint only records evidence and cannot enqueue or call a destination;
- no analytics learner, outreach, contract, payment or revenue executor exists.

## Tests relevant to this threat model

Phase 5 focused tests cover:

- all eight structured check records and mandatory human-review aggregation;
- signed independent approval and fresh verification at both future checkpoints;
- unknown, expired, unsupported-platform/territory and unknown third-party rights;
- missing attribution at schema and gate layers;
- corrupted output and fabricated transcript lineage as non-overridable failures;
- low confidence and explicit uncertain-check acknowledgement;
- editor override, requester self-approval and model-actor denial;
- post-approval revocation, stale evidence and denied preflight;
- generic publish/upload job bypass denial;
- cross-tenant rights/evaluation/verification isolation;
- contradictory model-pass versus deterministic-failure aggregation;
- unprofiled destination becoming `unavailable`/human review, not silent success.

Retained Phase 4 tests cover:

- missing FFmpeg and capability disclosure;
- unapproved/cross-tenant candidate denial;
- extra/unsafe plan fields and invalid timestamps;
- idempotency replay/conflict;
- Unicode/HTML/ASS/bidi caption handling and measurable overflow;
- genuine FFmpeg subtitle render, FFprobe validation, checksum/private delivery;
- output replacement with matching storage metadata rejected against immutable DB checksum;
- queued cancellation and bounded retry;
- timeout and process crash without success output;
- output probe failure, wrong duration and missing audio;
- missing/changed source provenance;
- insufficient temporary disk;
- tenant-owner branding control and truthful missing capability;
- tenant-scoped render list/database records;
- meaning/boundary defect retry block.

Retained Phase 1–4 tests continue covering authentication, roles, rate/request limits, path traversal, ingestion signatures, provider response bounds, transcript validation, prompt-injection boundaries, candidate review and genuine bounded FFmpeg rendering.

## Residual risk / required pre-production work

Before any production claim:

1. run live PostgreSQL migration, rollback, lock/contention and concurrent idempotency/cap tests;
2. run live Redis duplicate/loss/recovery/failover tests;
3. build/scan the container images and verify the exact packaged FFmpeg feature/license/security posture;
4. deploy hardened object storage with private encryption, retention, malware/media sandboxing and immutable-object/version policy;
5. run larger malformed-media fuzz/corpus tests and OS/container seccomp isolation;
6. add observability/alerting for repeated render failures, disk pressure and integrity incidents;
7. run backup/restore and disaster-recovery exercises;
8. obtain independent legal/privacy review of rights workflows and retention/minimization policy;
9. conduct independent application/infrastructure security review;
10. add Phase 6 publisher credentials/executors only after explicit authorization and require the server gate at both checkpoints.
