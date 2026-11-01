# Phase 2 handoff — secure media ingestion and transcription

Date: 2026-10-30 (Asia/Kolkata)  
Version: `0.2.0`  
Migration head: `5708ce50be06`  
Gate status: **Phase 2 development gate passed with the limitations below; this is not a production-readiness claim.**  
Next-phase status: **Phase 3 is locked and was not started.**

## 1. Authorized scope delivered

This phase extends the Phase 1 control plane with local, authorized audio/video ingestion, deterministic preparation, provider-neutral transcription, semantic transcript segmentation and an operator transcript inspector.

Delivered behavior:

1. **Local upload first**
   - authenticated editor-or-owner multipart upload;
   - independent streamed byte ceiling, including requests without trustworthy `Content-Length`;
   - mandatory rights confirmation and bounded provenance note;
   - safe generated filename/key rather than trusting the supplied filename;
   - per-tenant SHA-256 duplicate detection.

2. **Media validation and quarantine**
   - byte-signature detection rather than extension/MIME trust;
   - signature/extension and declared/detected MIME consistency checks;
   - finite size, duration, dimensions, stream and codec policy;
   - real FFprobe structural validation;
   - explicit safe categories for malformed, corrupt, silent, oversized, unsupported and timed-out media;
   - unsupported or policy-ambiguous media ends in `needs_review`; corrupt media fails safely.

3. **Private tenant-scoped storage**
   - opaque tenant-prefixed object keys;
   - path traversal, absolute path, NUL, backslash and symlink escape defenses;
   - mode `0700` directories and `0600` local files;
   - atomic writes and metadata replacement;
   - checksum verification on storage and materialization;
   - best-effort object rollback when database persistence fails.

4. **Bounded media preparation**
   - shell-free FFmpeg/FFprobe process invocation;
   - wall-clock, stdout/stderr, CPU, address-space and output-file bounds;
   - process-group termination on timeout/cancellation/output overflow;
   - deterministic first-audio-stream extraction to mono 16 kHz PCM `s16le` WAV;
   - generated-audio size estimation and post-write enforcement;
   - per-job temporary directories removed at context exit.

5. **Durable lifecycle and recovery**
   - explicit asset and job state machines;
   - persisted stage/progress/error fields;
   - bounded attempts, retry and cooperative cancellation;
   - durable database recovery when queue notification fails;
   - persisted running-job heartbeats;
   - stale-worker lease recovery with row locking, retry-budget enforcement and audit evidence;
   - job correlation ID propagated into worker audit events and reset after each task.

6. **Provider-neutral transcription**
   - typed timestamped transcript contract;
   - safe disabled adapter, which produces `needs_review` rather than fake content;
   - OpenAI adapter with asynchronously streamed multipart audio and verbose segment/word timestamps;
   - bounded response bytes/text/row counts and timeline validation;
   - transient-only bounded retry;
   - sanitized authentication, rate, timeout, transport, server and malformed-response categories.

7. **Transcript evidence and segmentation**
   - original provider text and deterministic NFKC/whitespace-normalized text;
   - private bounded raw provider response;
   - provider/model/request identity, language, confidence and optional diarization labels;
   - ordered segment and word timestamps;
   - deterministic summary/topics;
   - semantic chunks with segment, time and exact normalized-character offsets;
   - paginated segment, word and chunk reads with total counts.

8. **Tenant-safe API and dashboard**
   - source capability, upload, disabled URL, list/search, detail, transcript, retry and cancel routes;
   - tenant identity comes only from the authenticated principal;
   - cross-tenant resources return `404`;
   - media dashboard shows upload progress, provenance, checksums, lifecycle, verified metadata, errors, language/confidence, speaker-aware timeline, original/normalized text and incrementally loaded chunks;
   - global pause/resume remains owner-only and audited.

9. **Audit and diagnostics**
   - upload/duplicate, lifecycle, retry, cancellation, recovery and completion events;
   - request and worker correlation identifiers;
   - truthful source/provider capability status;
   - configuration diagnostics expose names/status, never secret values;
   - no asset-deletion or asset-permission mutation endpoint exists in this phase, so there is no unaudited deletion/permission action surface.

## 2. Explicitly disabled or not implemented

The following are deliberately absent rather than represented by mocks:

- URL ingestion and remote fetching;
- S3-compatible storage;
- viral scoring, clip selection, editing or rendering;
- publishing, social OAuth, lead discovery, outreach, contracting, payments or revenue automation;
- any Phase 3-or-later route, worker or executor.

`POST /api/v1/assets/from-url` always returns `501` with category `url_ingestion_disabled`. Adversarial tests submit localhost, private IPv4, IPv6 loopback, link-local metadata and credential-bearing URL candidates while replacing DNS and HTTP connection functions with fail-on-call guards; no lookup or connection is made.

## 3. Acceptance evidence

### Backend quality and tests

Final commands:

```bash
PYTHONPATH=apps/api .venv/bin/ruff format apps/api
PYTHONPATH=apps/api .venv/bin/ruff check apps/api
PYTHONPATH=apps/api .venv/bin/mypy apps/api/clipos
PYTHONPATH=apps/api .venv/bin/pytest \
  --cov=apps/api/clipos --cov-branch \
  --cov-report=term-missing --cov-report=json:coverage-phase2.json -q
```

Results:

- Ruff formatting/check: **passed**;
- mypy: **passed across 61 source files**;
- pytest: **80 passed, 0 failed, 0 skipped**;
- branch-aware aggregate coverage: **79.11%**;
- configured minimum: **70%**.

Collected tests by module:

| Module | Collected |
|---|---:|
| `test_approvals_audit_control.py` | 5 |
| `test_config_security.py` | 8 |
| `test_error_provider_adapter.py` | 3 |
| `test_health_auth_api.py` | 6 |
| `test_jobs_tenants.py` | 5 |
| `test_model_gateway.py` | 6 |
| `test_phase2_media.py` | 18 |
| `test_phase2_processing.py` | 19 |
| `test_rate_limit.py` | 1 |
| `test_storage_contracts.py` | 8 |
| **Total** | **80** |

The Phase 2 tests exercise genuine generated WAV, audio/video MP4, silent MP4 and unsupported PCM-U8 media through installed FFmpeg/FFprobe 7.1.5. They also cover:

- signature spoofing, MIME/extension mismatch and corrupt containers;
- unsafe Unicode/bidirectional/path filenames;
- missing binaries, timeout, duration/dimension bounds and output limits;
- tenant storage isolation, symlink defenses and checksum tampering;
- missing rights, duplicates and cross-tenant reads;
- chunked/unknown-length oversize bodies, negative `Content-Length`, interrupted uploads and partial-file cleanup;
- queue, simulated disk-exhaustion and other storage failures with sanitized responses;
- successful audio and video processing, disabled transcription, low confidence, silent media, unsupported codec and transcription timeout;
- retry/cancel behavior, durable queue fallback, worker correlation and simulated stale-worker recovery;
- provider timeline/row/response bounds and transient-only retry;
- multilingual semantic chunks with exact character slices;
- transcript segment/word/chunk pagination.

### Database migrations

Migration chain:

```text
base
  -> 88a5dfd612fc  phase 1 foundation
  -> 5708ce50be06  phase 2 ingestion and transcription
```

Fresh SQLite evidence completed successfully:

1. `base -> head`;
2. `alembic check` with no new upgrade operations;
3. `head -> 88a5dfd612fc -> head`;
4. second no-drift check;
5. `head -> base -> head`;
6. current revision confirmed as `5708ce50be06`.

Offline PostgreSQL DDL compilation succeeded. Twelve assertions confirmed the heartbeat column/index, Phase 2 tables, native enum creation, JSONB and final revision marker in a 16,858-byte SQL stream. This is static compilation evidence only, not a live PostgreSQL claim.

### Frontend

Final commands:

```bash
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
npm --prefix apps/web audit --audit-level=high
```

Results:

- Oxlint 1.90.0: **0 warnings, 0 errors across 20 files / 96 rules**;
- TypeScript: **passed**;
- Next.js 16.4.3 optimized build: **passed**;
- generated routes: **8** (`/`, `/_not-found`, `/approvals`, `/diagnostics`, `/jobs`, `/media`, `/media/[id]`, `/settings`);
- npm full dependency audit (runtime and development dependencies): **0 vulnerabilities found**.

This proves compilation, not browser end-to-end behavior or accessibility certification.

### Dependency and artifact checks

- `pip-audit`: **no known vulnerabilities found**; the local non-PyPI package `zohra-clipos` was explicitly skipped as an unpublished project;
- Compose YAML: parsed as **5 services** and **3 volumes**, with protected-environment schema/queue safety markers asserted;
- secret-pattern scan: no obvious live API key, AWS access key or private-key material found;
- sensitive artifact scan: no committed `.env`, credential file or private media candidate found;
- forbidden-scope scan: no Phase 3 viral-score, clip-selection, render-pipeline or social-publish implementation found;
- `docs/PHASE_2_SOURCE_HASHES.sha256` records SHA-256 checksums for every other clean delivery file.

### Validation environment

- Python 3.13.16
- Node.js 20.20.2 / npm 10.8.2
- FastAPI 0.162.0
- SQLAlchemy 2.1.6
- Pydantic 2.14.2
- HTTPX 0.28.1
- Alembic 1.20.1
- Next.js 16.4.3 / React 19.3.1 / Oxlint 1.90.0
- FFmpeg and FFprobe 7.1.5

## 4. Requirement-to-evidence map

| Phase 2 requirement | Implementation/evidence | Status |
|---|---|---:|
| Authorized local upload | streamed upload + mandatory permission/note + negative tests | **real-tested locally** |
| Strict media validation | signatures + real FFprobe + codec/duration/dimension policy | **real-tested locally** |
| Checksum and duplicates | SHA-256 + tenant-local unique constraint + duplicate audit | **real-tested locally** |
| Private tenant storage | confined atomic local adapter + tamper/symlink tests | **real-tested locally; development only** |
| Explicit lifecycle | persisted asset/job states, progress, errors, cancel/retry | **real-tested locally** |
| Bounded FFmpeg/FFprobe | process/output/resource/file limits and fault tests | **real-tested locally** |
| Durable worker recovery | queue fallback, heartbeats, stale lease recovery | **real-tested with SQLite/in-memory worker** |
| Provider-neutral transcription | contracts, gateway, disabled/OpenAI adapters | **contract-tested** |
| Live external transcription | no approved credential or live request | **unverified; no claim** |
| Raw + normalized transcript | persisted raw provider evidence and normalized text | **real-tested with fixture adapter** |
| Language/confidence/diarization | typed/persisted/exposed metadata | **real-tested with fixture adapter** |
| Long-content chunks/offsets | deterministic Unicode-aware chunking and exact-slice tests | **real-tested locally** |
| Asset/status/search/retry/cancel APIs | authenticated tenant-scoped routes and tests | **real-tested locally** |
| Transcript pagination | independent segment/word/chunk offsets, limits and totals | **real-tested locally** |
| Operator inspection UI | media library/detail/upload/transcript views | **build-tested; no browser E2E** |
| Audit and tenant isolation | authoritative principal, tenant predicates, `404`, audit tests | **real-tested locally** |
| URL ingestion | explicit 501, fail-on-network adversarial tests | **disabled by design** |
| PostgreSQL/Redis topology | adapters/config/migrations/Compose | **static only; unverified live** |

## 5. Honest limitations and production blockers

1. **No live PostgreSQL.** SQLite migrations and behavior passed; PostgreSQL DDL only compiled offline. Row locking, enum migration, rollback, concurrency and backup/restore need a real isolated PostgreSQL exercise.
2. **No live Redis.** The memory broker and embedded recovery worker passed. The Redis broker and separate worker need multi-process failure, redelivery, restart and saturation tests.
3. **No live transcription provider.** OpenAI behavior uses `httpx.MockTransport`; successful fixture transcripts are not provider proof. An authorized call using non-sensitive media plus privacy/data-processing review is still required.
4. **Local storage is not production object storage.** There is no S3 adapter, KMS/encryption-at-rest evidence, bucket policy, versioning, lifecycle, multipart, replication or disaster-recovery proof.
5. **No malware scanner.** Files are treated as untrusted and never executed or publicly served, but an antivirus/content-disarm gate is still required before production.
6. **Media tooling is not container-sandbox proven.** Bounds and Unix resource limits exist, but production needs a patched, unprivileged, no-network media sandbox with cgroup/PID/seccomp/AppArmor controls.
7. **No automatic original-media deletion.** This is deliberate: originals and normalized derivatives are retained rather than silently destroyed. Temporary work directories are cleaned. A reviewed, audited database/object retention and deletion workflow remains required.
8. **No URL ingestion.** Redirect-by-redirect DNS/IP checks, rebinding protection, private-address denial, content bounds, robots/source policy and allowlists have not been proven, so remote fetching stays disabled.
9. **No browser E2E, accessibility, load, chaos or penetration test.** Frontend evidence is type/build only; API evidence is local automated testing.
10. **Docker was unavailable.** Compose was parsed but images, health checks, persistence and container networking were not run.
11. **No production observability proof.** Structured logs, audit rows, health/readiness and correlation IDs exist; centralized log/metric/trace retention and alert exercises do not.
12. **No production claim.** Passing this gate means the authorized Phase 2 implementation and local evidence are complete, not that it is safe to deploy to paying customers or process regulated media.

## 6. Operator review checklist

Before accepting the phase, review:

- `README.md` for setup and scope;
- `docs/ARCHITECTURE.md` for trust boundaries and data flow;
- `docs/SECURITY.md` for threat controls and missing production controls;
- `docs/DATABASE.md` for schema/migration evidence;
- `docs/INTEGRATION_MATRIX.md` for real/mock/disabled/unverified status;
- `docs/DEVELOPMENT.md` for local operator procedures;
- this handoff for final gate evidence and limitations.

Before any protected deployment, complete every applicable blocker above, inject secrets outside source control, run migrations as a release step, keep processing paused by default, and obtain human approval for provider/privacy/retention policy.

## 7. Stop condition

Phase 2 implementation stops here. No Phase 3 scoring, candidate ranking or clip selection work is authorized or included. Proceed only after explicit human review and authorization of the next phase.
