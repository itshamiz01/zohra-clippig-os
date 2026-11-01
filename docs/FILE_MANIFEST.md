# File manifest — Phase 5

The authoritative byte-level clean-source manifest is `docs/PHASE_5_SOURCE_HASHES.sha256`. This document explains ownership and purpose.

## Repository root

| Path | Purpose |
|---|---|
| `README.md` | Phase 5 scope, quick start, rights/quality workflow, validation and truthful limitations |
| `pyproject.toml` | Python package `0.5.0`, dependencies and Ruff/mypy/pytest/coverage policy |
| `.env.example` | Non-secret development settings including bounded Phase 4 render and Phase 5 gate controls |
| `.env.compose.example` | Non-secret Compose variable names/placeholders |
| `.gitignore` | Excludes secrets, runtime data, media, builds, caches and dependencies |
| `.dockerignore` | Excludes VCS, secrets, runtime objects/databases, dependencies, builds, caches and archives from image context |
| `Makefile` | Install, API, worker, web, migration, test and quality commands |
| `docker-compose.yml` | Unverified PostgreSQL/Redis/API/resource-bounded worker/web topology |
| `alembic.ini` | Alembic runtime configuration |

## Migrations

| Path | Purpose |
|---|---|
| `alembic/env.py` | Async/offline migration environment and metadata binding |
| `alembic/versions/88a5dfd612fc_phase_1_foundation.py` | Historical Phase 1 schema |
| `alembic/versions/5708ce50be06_phase_2_ingestion_and_transcription.py` | Historical Phase 2 schema |
| `alembic/versions/b71f2e44e1fe_phase_3_explainable_clip_discovery.py` | Historical Phase 3 discovery/review schema |
| `alembic/versions/8f0da12e33a4_phase_4_validated_media_rendering.py` | Reversible Phase 4 profile/plan/caption/job/output/defect schema |
| `alembic/versions/43b5bc652f2e_phase_5_mandatory_quality_rights_gates.py` | Reversible Phase 5 rights/check/evaluation/decision/verification schema |

## API foundation and configuration

| Path | Purpose |
|---|---|
| `apps/api/Dockerfile` | Non-root Python runtime image with FFmpeg installation |
| `apps/api/clipos/__init__.py` | Version `0.5.0` marker |
| `apps/api/clipos/config.py` | Strict environment validation, render/resource settings and gate evidence TTLs |
| `apps/api/clipos/database.py` | Async engine/session/base conventions |
| `apps/api/clipos/main.py` | Application lifespan, providers, tooling, shared worker and route wiring |
| `apps/api/clipos/worker.py` | Standalone queue/recovery worker entrypoint |
| `apps/api/clipos/core/enums.py` | Retained enums plus rights, quality, policy outcome and checkpoint states |
| `apps/api/clipos/core/errors.py` | Safe structured application errors |
| `apps/api/clipos/core/logging.py` | Structured correlation-aware logging |
| `apps/api/clipos/core/middleware.py` | Correlation, request/rate, security-header and Redis limiter controls |
| `apps/api/clipos/core/security.py` | Password/JWT/principal/role logic |
| `apps/api/clipos/schemas.py` | Foundation/auth/job/diagnostic API contracts |
| `apps/api/clipos/media_schemas.py` | Retained media/transcript contracts |
| `apps/api/clipos/discovery_schemas.py` | Retained strict discovery/review contracts |
| `apps/api/clipos/render_schemas.py` | Strict Phase 4 request/response/capability/profile/defect contracts |
| `apps/api/clipos/quality_schemas.py` | Strict Phase 5 rights, council, decision, verification and review-panel contracts |

## Persistence

| Path | Purpose |
|---|---|
| `apps/api/clipos/models/base.py` | UUID and timestamp mixins/naming conventions |
| `apps/api/clipos/models/entities.py` | All tenant/auth/job/media/discovery/render ORM entities and database checks |
| `apps/api/clipos/models/__init__.py` | Model exports/metadata registration |

Phase 4 entities are `TenantRenderProfile`, `RenderPlan`, `RenderCaptionSegment`, `RenderJob`, `RenderOutput` and `RenderDefect`. Phase 5 adds `RightsEvidence`, `QualityEvaluation`, `QualityCheckResult`, `QualityReviewDecision` and `GateVerification`.

## Media, captions and storage

| Path | Purpose |
|---|---|
| `apps/api/clipos/media/errors.py` | Safe tool/pipeline timeout/cancellation/failure classes |
| `apps/api/clipos/media/signatures.py` | Retained streamed signature/type validation |
| `apps/api/clipos/media/tooling.py` | Bounded shell-free FFprobe/audio extraction and FFmpeg render pipeline |
| `apps/api/clipos/rendering/captions.py` | Unicode-safe bounded caption grouping, source mapping and SRT generation |
| `apps/api/clipos/storage/base.py` | Tenant object-storage protocol and immutable object info |
| `apps/api/clipos/storage/local.py` | Safe keys, atomic writes, validated metadata and checksum-verifying private storage |
| `apps/api/clipos/storage/__init__.py` | Storage exports |

## Services and workers

| Path | Purpose |
|---|---|
| `apps/api/clipos/services/rendering.py` | Capability/profile service, immutable plan construction, lifecycle, FFmpeg processing, validation, retries, cancellation, defects and recovery |
| `apps/api/clipos/services/quality.py` | Rights Guardian, eight-check council, aggregation, human decision, protected hashes, signatures, staleness and reusable preflight gate |
| `apps/api/clipos/services/workers.py` | Shared typed `asset.process` / `render.execute` dispatcher and recovery ordering |
| `apps/api/clipos/services/asset_processing.py` | Retained bounded asset worker, exposed recovery contract |
| `apps/api/clipos/services/assets.py` | Retained upload/list/retry/cancel service |
| `apps/api/clipos/services/discovery.py` | Retained candidate discovery/review service |
| `apps/api/clipos/services/transcripts.py` | Retained transcript inspection service |
| `apps/api/clipos/services/system_control.py` | Global platform-admin pause/resume service |
| `apps/api/clipos/services/providers.py` | Truthful configured/runtime capability status |
| `apps/api/clipos/services/audit.py` | Append-only audit recording/listing |
| `apps/api/clipos/services/auth.py`, `jobs.py`, `approvals.py`, `health.py`, `usage.py` | Retained foundation services |
| `apps/api/clipos/queue/base.py`, `brokers.py` | Typed in-memory/Redis broker contracts |

## API routes

| Path | Purpose |
|---|---|
| `apps/api/clipos/api/deps.py` | Session, principal and role dependencies |
| `apps/api/clipos/api/routes/rendering.py` | Phase 4 capabilities/profile/plan/list/detail/actions/private-file routes |
| `apps/api/clipos/api/routes/quality.py` | Phase 5 rights/evaluation/review/verification routes |
| `apps/api/clipos/api/routes/discovery.py` | Retained candidate discovery/context/review routes |
| `apps/api/clipos/api/routes/assets.py` | Retained media/source/transcript routes |
| `apps/api/clipos/api/routes/auth.py` | Login and authoritative current principal |
| `apps/api/clipos/api/routes/diagnostics.py` | Configuration/provider/audit/global control |
| `apps/api/clipos/api/routes/health.py` | Health/readiness |
| `apps/api/clipos/api/routes/jobs.py`, `approvals.py` | Retained guarded generic lifecycle/approval routes |

## Provider boundaries

| Path | Purpose |
|---|---|
| `apps/api/clipos/gateways/transcription/*` | Retained disabled/OpenAI transcription contracts, adapters and retry bounds |
| `apps/api/clipos/gateways/models/*` | Retained disabled/OpenAI-compatible/Anthropic selection gateways |
| `apps/api/clipos/contracts/future.py` | Inert future-phase boundary contracts only; no executor |

## Tests

| Path | Purpose |
|---|---|
| `apps/api/tests/conftest.py` | Isolated app/tenants/auth plus genuine generated audio/video fixtures and bounded test render disk settings |
| `apps/api/tests/test_phase4_rendering.py` | 14 retained Phase 4 cases including genuine FFmpeg output |
| `apps/api/tests/test_phase5_quality_rights.py` | 19 focused Phase 5 cases for rights, all checks, human authority, signatures, staleness, scope denial, bypass and tenant isolation |
| `apps/api/tests/test_phase3_discovery.py` | Retained discovery/review tests |
| `apps/api/tests/test_phase2_media.py` | Retained ingestion/storage/transcript tests |
| `apps/api/tests/test_security_and_providers.py` | Retained middleware/config/provider boundary tests |
| `apps/api/tests/test_api_foundation.py` | Retained foundation/auth/job/approval tests |
| `apps/api/tests/test_migrations.py` | Retained migration metadata checks |

## Web application

| Path | Purpose |
|---|---|
| `apps/web/package.json`, `package-lock.json` | Version `0.5.0` Next.js/React graph, Oxlint/type/build scripts |
| `apps/web/next.config.mjs` | Standalone output and server-side API/health proxy |
| `apps/web/Dockerfile` | Multi-stage standalone non-root web image |
| `apps/web/app/layout.tsx` | Shared shell and Phase 4/5 CSS registration |
| `apps/web/app/globals.css` | Retained responsive Phase 1–3 design system |
| `apps/web/app/phase4.css` | Responsive render control-room, queue, evidence, caption/provenance/defect styles |
| `apps/web/app/phase5.css` | Responsive council, rights, policy, decision and audit-panel styles |
| `apps/web/app/page.tsx` | Phase 5 overview and Phase 6 lock |
| `apps/web/app/renders/page.tsx` | Candidate-query handoff into render workbench |
| `apps/web/app/quality/page.tsx` | Phase 5 Quality Council workbench route |
| `apps/web/app/discovery/page.tsx` | Retained candidate workbench page |
| `apps/web/app/media/*` | Retained media library/inspector pages |
| `apps/web/app/jobs/page.tsx`, `approvals/page.tsx`, `settings/page.tsx`, `diagnostics/page.tsx` | Updated phase-aware operator pages |
| `apps/web/components/render-workbench.tsx` | Authenticated plan/profile/control/queue/detail/preview/download/caption/retry/defect UI |
| `apps/web/components/quality-workbench.tsx` | Rights recording, council queue, evidence, decision and preflight UI |
| `apps/web/components/discovery-workbench.tsx` | Retained evidence/review UI plus approved-candidate render handoff |
| `apps/web/components/app-shell.tsx` | Phase 5 navigation/header |
| `apps/web/components/session-access.tsx` | Tenant login/session UI including platform-admin principal field |
| `apps/web/components/media-library.tsx` | Retained media UI with platform-admin-aware global control |
| `apps/web/components/asset-inspector.tsx`, `live-health.tsx`, `section-header.tsx`, `icons.tsx` | Shared/retained UI components |
| `apps/web/lib/render-api.ts` | Phase 4 TypeScript lifecycle/plan/output/capability contracts |
| `apps/web/lib/quality-api.ts` | Phase 5 TypeScript rights/check/evaluation/decision/verification contracts |
| `apps/web/lib/discovery-api.ts`, `media-api.ts` | Retained API contracts/helpers and authenticated JSON client |

## Scripts and infrastructure

| Path | Purpose |
|---|---|
| `scripts/bootstrap_admin.py` | One-time hidden-password platform-admin/owner bootstrap |
| `infra/README.md` | Infrastructure boundary note |

## Documentation and evidence

| Path | Purpose |
|---|---|
| `docs/PHASE_5_HANDOFF.md` | Current Phase 5 acceptance/evidence/limitations/stop record |
| `docs/PHASE_4_HANDOFF.md` | Historical accepted Phase 4 gate |
| `docs/PHASE_5_SOURCE_HASHES.sha256` | Current clean-source hash manifest, generated last |
| `docs/ARCHITECTURE.md` | Phase 5 trust boundaries, retained render flow and mandatory gate flow |
| `docs/SECURITY.md` | Phase 5 threat model, rights and stale/bypass controls |
| `docs/DATABASE.md` | Migration/schema/SQL-contract evidence |
| `docs/INTEGRATION_MATRIX.md` | Evidence-based real/mock/disabled/unverified status |
| `docs/DEVELOPMENT.md` | Developer/operator workflow and quality commands |
| `docs/PHASE_1_HANDOFF.md`, `PHASE_2_HANDOFF.md`, `PHASE_3_HANDOFF.md` | Historical accepted gate evidence; not current validators |
| `docs/PHASE_2_SOURCE_HASHES.sha256`, `PHASE_3_SOURCE_HASHES.sha256` | Historical manifests only; not Phase 5 validators |

## Excluded generated/runtime material

Clean archive and source hashes exclude at least:

- `.git`, `.env*` secrets (examples retained), `.venv`;
- `node_modules`, `.next`, Python/tool caches;
- `.coverage`, `coverage.xml`, `htmlcov`;
- `data`, databases, source media, temporary render files and private output;
- prior/current ZIPs and checksum sidecars.
