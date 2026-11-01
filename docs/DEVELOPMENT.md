# Development and operator guide — Phase 5

## Prerequisites

- Python 3.11 or newer
- Node.js 20.19 or newer
- FFmpeg and FFprobe on `PATH`
- SQLite for local development
- PostgreSQL 16 and Redis 7 for protected-environment validation
- Docker/Compose only if exercising the described multi-service topology

The Phase 5 gate used Python 3.13.16, Next.js 16.4.3 and a static FFmpeg/FFprobe 7.0.2 at `/home/user/tools/ffmpeg`. That local binary directory is an evidence-environment path, not a repository dependency.

## Install

```bash
cd /path/to/zohra-clipos
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
npm --prefix apps/web ci
cp .env.example .env
```

Keep `.env`, databases, source media, render output and provider credentials out of source control.

## Configure local rendering

At minimum:

```dotenv
CLIPOS_ENVIRONMENT=development
CLIPOS_SECRET_KEY=<at-least-32-random-characters>
CLIPOS_DATABASE_URL=sqlite+aiosqlite:///./data/clipos.db
CLIPOS_AUTO_CREATE_SCHEMA=false
CLIPOS_QUEUE_BACKEND=memory
CLIPOS_STORAGE_BACKEND=local
CLIPOS_STORAGE_LOCAL_ROOT=./data/storage
CLIPOS_UPLOAD_TEMP_ROOT=./data/tmp
CLIPOS_RENDER_TEMP_ROOT=./data/render-tmp
CLIPOS_FFMPEG_PATH=ffmpeg
CLIPOS_FFPROBE_PATH=ffprobe
CLIPOS_EMBEDDED_WORKER_ENABLED=true
CLIPOS_RENDERING_ENABLED=true
```

`CLIPOS_RENDER_TEMP_ROOT` must be outside the private object-storage root. Startup rejects an equal/nested temp root.

Default Phase 4 rendering bounds and Phase 5 confidence/approval/preflight TTL bounds are listed in `.env.example`. Gate evidence expires by default after 24 hours and a successful preflight after five minutes, but Phase 6 must re-call the server service rather than trust either duration as reusable authority.

## Database and first owner

```bash
export PYTHONPATH=apps/api
.venv/bin/alembic upgrade head
make bootstrap ARGS="--slug local --tenant-name 'Local Studio' --email owner@example.com --display-name Owner"
```

The bootstrap password is read from a hidden terminal prompt and is never logged. It creates the first platform administrator and tenant owner only when none exists.

## Run locally

Separate terminals:

```bash
make api
make web
```

With `CLIPOS_EMBEDDED_WORKER_ENABLED=false`, also run:

```bash
make worker
```

Endpoints:

- dashboard: `http://localhost:3000`
- API: `http://localhost:8000`
- development OpenAPI: `http://localhost:8000/docs`
- health: `http://localhost:8000/healthz`
- readiness: `http://localhost:8000/readyz`

## Capability check

After authenticating:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/rendering/capabilities
```

Read `render_ready`, versions, `capabilities` and `limitations`. Configuration is not proof of capability. The service interrogates the actual binary.

The tested static 7.0.2 build reports:

```text
aac, crop, fps, libx264, loudnorm, pad, scale, subtitles
```

It lacks `drawtext`, so tenant text branding is unavailable in that environment. Do not disable the capability gate or claim branding success.

## Safe operator workflow

1. Sign in through the dashboard.
2. A platform-admin owner provides an audited reason to resume global processing.
3. Upload only media you are authorized to process; confirm permission and write a rights note.
4. Wait for verified metadata/transcript evidence. Disabled transcription intentionally stops audio assets in review.
5. Run deterministic discovery (or a separately configured, truthful provider mode).
6. A reviewer inspects context/factors and approves exact boundaries.
7. Use **Create validated render** or open `/renders?candidate=<UUID>`.
8. Review actual renderer limitations.
9. Choose a preset/crop/caption/audio policy; use a stable idempotency key.
10. Follow queue/progress and inspect the approved-candidate comparison.
11. Preview/download only after `succeeded`.
12. Report defects. Any meaning/boundary change must take a new candidate through review.
13. In `/quality`, an administrator records one exact rights assertion with platform, territory, duration, use permissions, attribution and six material states. Do not treat the upload checkbox as release evidence.
14. An editor runs the eight-check council for one render/output/destination/territory/campaign.
15. A different reviewer inspects source context, check evidence, rights scope, missing documents and remediation; uncertain checks require explicit acknowledgement and every decision requires a reason.
16. If approved, optionally exercise both preflight contracts to verify current signed evidence. These calls publish and upload nothing.
17. Revoke evidence immediately if permission is withdrawn; old approval becomes stale and preflight is denied.
18. Stop. Do not publish or execute Phase 6+ actions.

## Tenant branding policy

A tenant owner can configure text policy from `/renders` or:

```bash
curl -X PUT http://localhost:8000/api/v1/rendering/profile \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "branding_enabled":true,
    "branding_text":"TENANT APPROVED MARK",
    "branding_position":"top_right",
    "branding_opacity":0.85,
    "branding_font_size":36
  }'
```

This does not prove the runtime supports `drawtext`. Plan creation rejects requested branding when that capability is missing. Existing plans retain their snapshot after policy changes.

## Render-plan API example

```bash
curl -X POST "http://localhost:8000/api/v1/candidates/$CANDIDATE_ID/render-plans" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "idempotency_key":"campaign-a-cut-001",
    "preset":"vertical_720p",
    "crop_strategy":"fit_pad",
    "captions":{"mode":"both","style":"high_contrast_bottom"},
    "audio":{"normalization":"speech_loudness","target_lufs":-16,"true_peak_db":-1.5},
    "include_tenant_branding":false,
    "video_codec":"h264",
    "audio_codec":"aac",
    "container":"mp4"
  }'
```

Reuse of that key with the same request returns the original plan with an idempotent replay header. Reuse with different parameters returns 409.

## Status, cancellation, retry and defects

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/renders
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/renders/$RENDER_ID
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/renders/$RENDER_ID/cancel
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/renders/$RENDER_ID/retry
```

Defect example:

```bash
curl -X POST "http://localhost:8000/api/v1/renders/$RENDER_ID/defects" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "category":"captions",
    "description":"The final cue overlaps a critical visual element.",
    "meaning_or_boundary_changed":false,
    "requested_resolution":"Use a reviewed safe-area/style correction without changing source bounds."
  }'
```

Preview/download/captions require an Authorization header. Do not append the token to a URL. The dashboard uses authenticated fetch + browser blob URLs.

## Phase 5 rights and quality API

The `/quality` dashboard is the preferred review surface. API callers must use strict JSON. Example render-scoped rights evidence (timestamps abbreviated here; use timezone-aware ISO 8601 values):

```bash
curl -X POST http://localhost:8000/api/v1/rights-evidence \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d "{
    \"asset_id\":\"$ASSET_ID\", \"scope\":\"render\", \"render_job_id\":\"$RENDER_ID\",
    \"basis\":\"written_permission\", \"status\":\"valid\",
    \"source_owner\":\"Referenced source owner\", \"evidence_type\":\"written_consent\",
    \"evidence_reference\":\"legal-ledger:record-123\", \"evidence_checksum_sha256\":null,
    \"permission_recorded_at\":\"2026-10-31T10:00:00+05:30\",
    \"effective_from\":\"2026-10-31T10:00:00+05:30\",
    \"expires_at\":\"2027-10-31T10:00:00+05:30\", \"no_expiration_asserted\":false,
    \"allowed_platforms\":[\"youtube_shorts\"], \"allowed_territories\":[\"IN\"],
    \"commercial_use_allowed\":true, \"editing_allowed\":true,
    \"ai_processing_allowed\":true, \"attribution_required\":false,
    \"attribution_text\":null, \"license_terms\":\"Exact documented terms.\",
    \"restrictions\":\"Exact render, platform, territory and campaign only.\",
    \"third_party_material\":{
      \"music\":\"not_present\", \"footage\":\"not_present\", \"logos\":\"not_present\",
      \"likeness_or_voice\":\"cleared\", \"personal_data\":\"not_present\",
      \"sensitive_information\":\"not_present\"
    }, \"campaign_reference\":\"campaign-a\"
  }"
```

Create an evaluation:

```bash
curl -X POST "http://localhost:8000/api/v1/renders/$RENDER_ID/quality-evaluations" \
  -H "Authorization: Bearer $EDITOR_TOKEN" -H 'Content-Type: application/json' \
  -d "{\"idempotency_key\":\"release-check-001\",\"rights_evidence_id\":\"$RIGHTS_ID\",\"destination\":\"youtube_shorts\",\"territory\":\"IN\",\"campaign_reference\":\"campaign-a\"}"
```

A reviewer retrieves detail, acknowledges every check ID with `needs_review`, `unavailable` or low confidence, and submits a reason. Approval is rejected if that reviewer requested the evaluation, any protected input changed, rights are not currently valid, or a deterministic failure exists.

```bash
curl -X POST "http://localhost:8000/api/v1/quality-evaluations/$EVALUATION_ID/decisions" \
  -H "Authorization: Bearer $REVIEWER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"approve","reason":"Independent source, audiovisual and rights review completed.","acknowledged_check_ids":["CHECK_UUID"]}'
```

Preflight contract example (no publish job or upload is created):

```bash
curl -X POST "http://localhost:8000/api/v1/quality-evaluations/$EVALUATION_ID/verify" \
  -H "Authorization: Bearer $REVIEWER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"checkpoint":"publish_job_acceptance","expected_destination":"youtube_shorts","expected_territory":"IN"}'
```

Repeat with `external_upload` only at the future immediate upload boundary. Never treat the returned signature as a bearer token or skip the service recheck.

## Quality gates

### Backend static/tests/coverage

```bash
PATH=/home/user/tools/ffmpeg:$PATH PYTHONPATH=apps/api \
  .venv/bin/ruff format --check apps/api
PYTHONPATH=apps/api .venv/bin/ruff check apps/api
PYTHONPATH=apps/api .venv/bin/mypy apps/api/clipos
PATH=/home/user/tools/ffmpeg:$PATH PYTHONPATH=apps/api \
  .venv/bin/pytest --cov=clipos --cov-branch --cov-report=term
```

### Dedicated Phase 4 tests

```bash
PATH=/home/user/tools/ffmpeg:$PATH PYTHONPATH=apps/api \
  .venv/bin/pytest apps/api/tests/test_phase4_rendering.py -q
```

These are not mock-only: one test creates genuine synthetic audio/video with FFmpeg, burns Unicode captions, probes the output and verifies checksum/private delivery.

### Dedicated Phase 5 tests

```bash
PATH=/home/user/tools/ffmpeg:$PATH PYTHONPATH=apps/api \
  .venv/bin/pytest apps/api/tests/test_phase5_quality_rights.py -q
```

The 19 focused cases exercise real tenant/database/local-storage paths and negative policy behavior, including denial of public availability as a permission basis and denial of cross-render evidence reuse. The test render rows reuse Phase 4-validated contracts for speed; they do not replace the retained genuine FFmpeg proof.

### Frontend

```bash
npm --prefix apps/web run lint
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

Next.js 16 removed `next lint`; the pinned `oxlint` script is the lint gate. With `output: "standalone"`, production-style smoke starts `.next/standalone/server.js` after copying `.next/static` and `public` into the standalone directory.

### Dependencies and security

```bash
.venv/bin/pip-audit --progress-spinner off
npm --prefix apps/web audit --audit-level=high
```

Also run secret scanning and review all subprocess/storage path changes. Dependency results are point-in-time.

### Migrations

See `docs/DATABASE.md` for fresh upgrade, no-drift, rollback/reapply and offline PostgreSQL commands.

## Docker Compose topology

Create an uncommitted `.env` from `.env.compose.example`, then (where Docker is available):

```bash
docker compose config
docker compose build --pull

docker compose up -d postgres redis
docker compose run --rm api alembic upgrade head
docker compose up -d api worker web
```

The API image installs distribution FFmpeg. Verify `/api/v1/rendering/capabilities` inside the built image; do not infer feature parity from the local static binary. The worker has Compose PID/memory/CPU limits in addition to child-process limits.

Docker was unavailable in the Phase 5 evidence environment, so these commands are required future validation, not completed proof.

## Protected-environment rules

`Settings` rejects a staging/production configuration unless:

- secret key is explicit and at least 32 characters;
- database URL is `postgresql+asyncpg`;
- automatic schema creation is off;
- Redis queue is selected and readiness-required;
- embedded worker is off;
- CORS contains no wildcard;
- provider credentials/models/rate confirmation match enabled providers;
- render temp storage is not within object storage;
- stale-worker window exceeds the longest probe/extract/render/provider retry budget.

This validates configuration shape only. It is not a deployment certification.

## Troubleshooting

### `renderer_unavailable`

Call the capability endpoint and inspect missing base filters/encoders. Confirm configured binary paths and execute permissions.

### `subtitle_filter_unavailable`

Use sidecar-only captions or install a rights/security-reviewed FFmpeg build with libass/subtitles. Do not silently claim burn-in.

### `branding_filter_unavailable`

The runtime lacks `drawtext`. Disable branding for this plan or deploy a verified binary with the capability.

### `insufficient_render_disk`

The temp mount lacks reserve + full source + maximum output capacity. Free space or deliberately change bounded settings; do not bypass the check.

### `render_source_integrity_changed`

Stored source bytes no longer match the approved asset size/checksum. Treat as review/integrity evidence; do not replace the database checksum.

### `system_paused`

A platform administrator with owner authority must provide an audited resume reason.

### `output_*_mismatch`

The generated artifact failed independent validation and is not a success output. Inspect safe category/details and preserve the failure evidence.

### Retry rejected after a meaning/boundary defect

Create/review a new candidate path. The old approved plan must not be repurposed.

### `non_overridable_gate`

Rights, authorization, provenance, security or deterministic failures cannot be acknowledged away. Correct the evidence/artifact and run a new evaluation.

### `review_acknowledgement_incomplete`

Retrieve evaluation detail and explicitly acknowledge every current uncertain/low-confidence check after human review. Do not fabricate IDs or blanket-ack without inspection.

### `stale_gate_evidence` / `mandatory_gate_denied`

Protected input, rights, expiry, signature or object integrity changed. Inspect stale/failure reasons, remediate and create a new evaluation if required. Never retry by bypassing the service.

### Revocation

Use `POST /api/v1/rights-evidence/{id}/revoke` with an audited reason. Do not delete or overwrite the evidence row; retained approvals must be visibly invalidated.
