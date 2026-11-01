# Phase 4 handoff — autonomous editing, captions, composition and validated rendering

Date: 2026-10-31 (Asia/Calcutta)

## Gate decision

**PASS for the authorized Phase 4 development scope, with the limitations below.**

This means the repository implements and locally validates immutable editing plans, bounded private FFmpeg rendering, Unicode captions, deterministic composition, output validation/provenance, lifecycle controls and the operator dashboard. It does **not** mean production readiness, platform acceptance, public release authority or commercial success.

Phase 5 and all publishing, outreach, contracting, payment and autonomous commercial actions remain unauthorized and absent.

## Delivered behavior

### Approval and immutable planning

- only a tenant-scoped candidate in the retained Phase 3 `approved` state can create a plan;
- exact source bounds and boundary revision are server-owned and snapshotted;
- plans are separately persisted, versioned (`render-plan.v1`) and have no update API;
- strict schemas reject unknown fields and arbitrary paths/codecs/filters;
- tenant/idempotency uniqueness plus canonical request hash returns same-request replays and rejects conflicts;
- source asset permission, rights note, ready state, verified metadata, audio/video streams and duration are rechecked;
- duration, width/height, even dimensions, pixels, FPS, queue caps and capabilities are validated before queueing;
- a linear 1.0× source-time formula and caption source/output mappings cover the complete rendered interval.

### Presets and composition

- TikTok-compatible, Instagram Reels-compatible and YouTube Shorts-compatible vertical targets;
- general vertical 1080p and resource-reduced 720p targets;
- square 1080p and landscape 1080p targets;
- bounded custom even dimensions/FPS;
- deterministic center crop and fit-with-black-padding;
- smart-face requests truthfully fall back to center crop; no face detection/tracking claim;
- safe-area metadata and explicit platform-acceptance disclaimer.

### Captions and branding

- word-timestamp cues when available, segment timing fallback otherwise;
- source/output timestamp pairs for each cue;
- Unicode-preserving deterministic cue/line/character/duration/count bounds;
- SRT sidecar, burn-in or both;
- HTML escaping, ASS override neutralization and Unicode bidi-control removal for untrusted transcript text;
- tenant-owner text branding policy, immutable per-plan snapshot and `drawtext` capability gate;
- no logo/image upload, arbitrary overlay, music or third-party asset path.

### Audio and FFmpeg containment

- explicit optional speech loudness normalization, LUFS, true peak, output channels and sample rate;
- H.264/AAC MP4 allowlist;
- real FFmpeg/FFprobe capability probing;
- argument-array subprocesses only, no shell interpolation;
- unique generated temporary/output paths and no-overwrite FFmpeg mode;
- virtual-memory, CPU-time, file-size, wall-time, thread and diagnostics bounds;
- cancellation monitor and process-group kill;
- temporary directory and orphan object cleanup;
- preflight free-space requirement includes configured reserve + full approved source + configured maximum output.

### Output validation and provenance

A current output record is created only after:

- source materialization bytes match storage metadata and approved asset SHA-256/size;
- FFmpeg exits successfully;
- output exists and has positive bounded size;
- FFprobe parses video and audio streams;
- dimensions, duration tolerance, frame rate, codecs, MP4-family format and audio layout match the plan;
- storage calculates a SHA-256 and atomically writes the unique attempt object.

Output evidence includes source/plan/candidate/caption/branding/audio provenance, validation report, stream metadata, FFprobe version and renderer version. Private delivery rehashes storage and compares it to both storage metadata and the immutable database checksum/size.

### Lifecycle, recovery and UI

- states: `queued`, `validating`, `rendering`, `validating_output`, `succeeded`, `failed`, `cancelled`, `needs_review`;
- durable background job, progress/stage, cancellation flag, bounded attempts and safe failure categories;
- typed shared worker dispatch for `asset.process` and `render.execute`;
- reserved kinds blocked from generic job submission;
- per-tenant queued/active caps protected by tenant-row locks under PostgreSQL semantics;
- durable queued/stale recovery paths;
- authenticated list/detail/cancel/retry/defect/preview/download/caption APIs;
- `/renders` dashboard with capabilities, global pause, profile, approved-candidate handoff/comparison, queue/progress, private blob preview, plan/caption/output/provenance evidence and defect controls;
- meaning/boundary defects move output to review and permanently block retry against the old plan.

## Final validation evidence

### Backend static and type gates

```text
Ruff format --check: passed (87 files)
Ruff lint: passed
Strict mypy: passed (73 source files)
```

### Backend tests and coverage

```text
115 passed in 44.62s
Branch-aware coverage: 76.10%
Configured minimum: 70%
Focused Phase 4 module: 14 passed
```

The focused module includes one genuine deterministic FFmpeg render, not a mocked production claim. It generates synthetic rights-safe source media locally, builds an approved candidate/plan, burns Unicode captions, renders H.264/AAC MP4, probes the output, checks the checksum/SRT/private route and exercises a defect gate.

Negative/edge evidence covers:

- missing FFmpeg;
- unapproved and cross-tenant candidates;
- unknown/unsafe plan fields and invalid source timestamps;
- idempotency replay and conflict;
- Unicode/HTML/ASS/bidi handling and measurable caption overflow;
- queued cancellation and bounded retry;
- timeout and process crash with no output record;
- failed output probe;
- wrong duration and missing audio;
- missing and provenance-changed source;
- insufficient disk;
- output object+metadata replacement rejected against immutable DB checksum;
- tenant-owner branding and truthful missing `drawtext`;
- tenant-scoped list/database records;
- meaning/boundary defect retry denial.

During development, the first focused run exposed SQLite inserting manually keyed caption children before their plan parent. Explicit plan/background/render parent flushes were added; the final focused and full suites above pass with foreign keys enabled. No schema weakening or FK suppression was used.

### Genuine media tooling

The evidence runtime used static FFmpeg/FFprobe **7.0.2** from `/home/user/tools/ffmpeg`.

Detected render capabilities:

```text
aac, crop, fps, libx264, loudnorm, pad, scale, subtitles
```

`render_ready=true`. The build lacks `drawtext`, so tenant text branding remains visibly unavailable in this runtime. Subtitle burn-in is genuine-tested. No face tracking is present.

### Migration gates

SQLite fresh/round-trip:

```text
base → 88a5dfd612fc → 5708ce50be06 → b71f2e44e1fe → 8f0da12e33a4
alembic check: No new upgrade operations detected.
Phase 4 downgrade: all 6 Phase 4 tables absent
Phase 4 re-upgrade: all 6 tables present; head 8f0da12e33a4
```

Offline PostgreSQL:

```text
full upgrade SQL: 36,866 bytes
Phase 4-only downgrade SQL: 618 bytes
asserted: 6 create/drop tables, JSONB, native render_state, config fields, revision marker
```

This is static dialect evidence. PostgreSQL was not live-tested.

### Frontend gates

```text
Oxlint: 0 warnings / 0 errors across 25 files and 96 rules
TypeScript strict no-emit: passed
Next.js 16.4.3 optimized build: passed
Routes generated: 10 (including framework not-found)
```

Built routes include dynamic `/renders`, `/discovery`, `/media/[id]` and the retained static operator pages.

### Runtime smoke

A production-style Next.js standalone server and real FastAPI process were started locally. Evidence:

- HTTP 200: `/`, `/discovery`, `/renders`, candidate-query `/renders`, `/settings`;
- web-proxied `/healthz` and `/readyz` returned healthy/ready;
- API root returned version `0.4.0`, phase `4`, status `validated_media_rendering`;
- authenticated login → real render capability status → default tenant profile → empty tenant render queue passed;
- unauthenticated capability request returned 401;
- `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY` observed.

This is HTTP/runtime smoke, not a full browser interaction, accessibility or visual-regression certification.

### Dependency and static security gates

```text
npm audit --audit-level=high: 0 vulnerabilities
pip-audit: no known vulnerabilities in auditable dependencies
local unpublished zohra-clipos 0.4.0: skipped by pip-audit as not on PyPI
private-key/common live-token pattern scan: passed
production subprocess scan: create_subprocess_exec only; no shell=True/create_subprocess_shell
Docker Compose YAML parse: expected 5 services; worker 3g/2 CPU/256 PID bounds parsed
```

Docker itself was unavailable. No `docker compose config`, image build, image scan or container runtime result is claimed.

## Requirement traceability

| Authorized Phase 4 requirement | Evidence | Status |
|---|---|---:|
| Only reviewed/approved candidates render | state/tenant/source checks + denial tests | Complete |
| Separate versioned strict immutable plan | table/schema/service/no update API/idempotency tests | Complete |
| Duration/dimension/resource/path validation | schema/service/storage/process bounds + negative tests | Complete |
| Vertical/platform/landscape/square variants | seven presets + custom + capability API/UI | Complete |
| Preserve source/transcript time mapping | plan linear formula + per-caption source/output pairs + provenance assertions | Complete |
| Reuse/harden FFmpeg/FFprobe | capability probe, shell-free process, RLIMIT/log/timeout/kill/cleanup + genuine render | Complete locally |
| Never overwrite source; unique/idempotent output | generated temp/attempt keys, `-n`, source checksum, unique DB constraints/tests | Complete |
| Honest crop fallback/no tracking claim | plan fields, limitations, capabilities and UI | Complete |
| Unicode aligned captions/safe areas/density | caption engine, SRT/burn-in, persisted cues and tests | Complete |
| Tenant-only branding; music disabled | owner profile/snapshot/capability gate/no asset schema | Complete; branding runtime blocked honestly |
| Explicit audio normalization/layout | immutable audio settings + FFmpeg args + output validation | Complete |
| Probe and validate output before success | validator/output transaction/genuine and negative tests | Complete |
| Lifecycle/progress/cancel/retry/failures/caps | enums/job/worker/locks/routes/tests/UI | Complete locally |
| Dashboard queue/status/files/metadata/captions/plan/retry/defect/comparison | `/renders` build/type/runtime smoke | Complete at build/HTTP level |
| Meaning/source changes return to review | defect flag, state transition and retry block | Complete |
| Required negative and genuine tests | 14 focused + retained full suite | Complete locally |
| No Phase 5/publishing/outreach/commercial action | no route/executor/UI action; docs and capability disclosure | Complete |

## Honest limitations and remaining gates

1. **No live PostgreSQL.** Offline SQL and SQLite do not prove native lock behavior, JSONB/enum execution, concurrent limits/idempotency or rollback under load.
2. **No live Redis.** Separate-worker duplicate/loss/outage/failover/recovery behavior is unverified.
3. **No Docker runtime.** Compose and Dockerfiles are configuration-reviewed only; the packaged FFmpeg capabilities are unknown until built/run.
4. **Text branding unavailable in the tested binary.** The policy and capability-gated implementation exist, but no genuine branding render is claimed.
5. **No browser E2E/accessibility/visual QA.** The dashboard passed lint, type, build and HTTP smoke only.
6. **No production media sandbox/malware scanner.** RLIMITs/container declarations reduce risk but FFmpeg remains a high-risk parser.
7. **Local storage is not production object storage.** Encryption/KMS, retention/deletion, legal hold, versioning, backup/restore and multi-host consistency are absent.
8. **No large malformed-media/fuzz corpus.** Genuine deterministic fixture and negative process/probe tests are bounded evidence only.
9. **One-pass loudness handling is not mastering.** It is explicit and validated for stream layout, but perceptual/audio QA belongs to a later human quality gate.
10. **Character bounds are not full visual QA.** Caption density/safe area are measurable deterministic controls; typography across languages/devices still needs Phase 5 review.
11. **Preset names are technical targets only.** They do not guarantee TikTok/Instagram/YouTube acceptance.
12. **No external provider live calls.** Retained AI/transcription provider status remains mocked/unverified/disabled as documented.
13. **No production claim.** Passing this gate demonstrates authorized local implementation and evidence, not customer deployment safety or revenue.

## Operator safety checklist

- verify source rights and tenant before ingestion;
- inspect actual runtime capabilities, not documentation assumptions;
- keep global processing paused until a platform-admin owner records a reason;
- approve exact source boundaries before plan creation;
- use stable idempotency keys;
- do not request smart tracking or branding when disclosed unavailable;
- inspect source comparison, caption mapping, checksum and probe evidence;
- report defects rather than replacing source/output bytes;
- route meaning/boundary changes through a new candidate review;
- do not expose local storage directly or put bearer tokens in URLs;
- do not publish or continue into Phase 5 without separate authorization.

## Delivery files

- source tree: `/home/user/zohra-clipos`
- clean archive: `/home/user/Zohra_ClipOS_Phase_4.zip`
- archive checksum sidecar: `/home/user/Zohra_ClipOS_Phase_4.zip.sha256`
- internal source manifest: `docs/PHASE_4_SOURCE_HASHES.sha256`

The source manifest intentionally excludes itself. The archive intentionally excludes VCS metadata, secrets, runtime databases/media/output, virtual environments, dependencies, build output, caches, coverage files and prior archives.

## Authorization boundary

Phase 4 implementation stops here. Phase 5 remains locked. Continue only after human review of this handoff and separate explicit authorization.
