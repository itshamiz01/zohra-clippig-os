# Phase 3 handoff — explainable clip discovery and human review

Date: 2026-10-30  
Version: `0.3.0`  
Migration head: `b71f2e44e1fe`  
Gate status: **authorized Phase 3 development scope complete with the limitations below; this is not a production-readiness claim.**

## Stop condition

This handoff stops before Phase 4. No final clip rendering, caption generation/burn-in, quality council, public publishing, platform OAuth, analytics attribution, outreach, contracting, payment, billing or autonomous executive action is implemented.

Candidate approval is internal editorial state only. It has no renderer or external-action executor attached.

## Delivered capability

### Deterministic planning

- configurable minimum/target/maximum duration, context padding and candidate cap;
- verified source-duration bounds;
- ordered timestamp/sentence planning with multilingual sentence endings;
- question/answer context preservation;
- evenly distributed bounded pools for long media;
- explicit short-source behavior and rejection of undersized long-source tail windows;
- temporal overlap and token-similarity duplicate suppression;
- deterministic output and idempotent run hashes.

### Explainable scoring

Thirteen versioned weighted factors total 1.0:

1. hook clarity;
2. self-contained context;
3. informational value;
4. narrative payoff;
5. within-source novelty;
6. explicit emotional intensity;
7. question/answer structure;
8. configured platform/editorial fit;
9. topic relevance;
10. speaker-turn emphasis;
11. quotability;
12. audience relevance;
13. transcript confidence.

Every candidate stores the factor meaning, score or unavailable state, weight, weighted contribution, evidence, applicability and missing-data rule. Scores exclude unavailable factors from the denominator. Platform profiles are editorial targets, not claims about platform algorithms; unknown profiles use and disclose the versioned general fallback.

Recommendation confidence and risk flags expose transcript certainty, timestamp detail, boundary alignment, missing context, short source, model fallback and human-edit alignment. Rationale explicitly denies any view or virality guarantee.

### Optional model selection

- strategies: `deterministic`, `hybrid`, `model_required`;
- disabled/OpenAI-compatible/Anthropic provider-neutral adapters;
- transcript and operator hints marked/serialized as untrusted data;
- strict schema allows only bounded deterministic option IDs and reasons;
- no tools, lifecycle mutation or authorization path for model output;
- unknown/duplicate evidence references, malformed output and empty required selections fail closed;
- hybrid typed failures fall back to deterministic results with visible caveats;
- request characters include schema size, output tokens, response bytes and parsed text are bounded;
- timeouts and transient retries are bounded;
- conservative cost preflight includes schema plus safety overhead;
- provider prices require explicit confirmation before a provider can be enabled;
- provider/model/request ID, units, latency, estimated cost, status and failure category are persisted.

### Lifecycle and human review

- states: `proposed`, `needs_review`, `approved`, `rejected`, `superseded`;
- editor-or-higher can initiate discovery;
- reviewer-or-higher can approve/reject or edit boundaries;
- terminal revisions cannot be silently reversed;
- boundary edits validate source/duration/transcript evidence, rescore locally, create a new row and supersede the old row;
- parent lineage, revision number, structured previous/new bounds, reason, reviewer/time and audit events are retained;
- source context endpoint and review history are tenant-safe.

### APIs and dashboard

Added APIs for run creation/list/detail, scoring definitions, filtered candidate queue, candidate detail, source context, decisions and boundary revisions.

The `/discovery` dashboard provides:

- run configuration and deterministic/hybrid/model-required choices;
- recent run state, failure, model, latency, candidate count and estimated cost;
- asset/state/language/topic/score filters;
- source transcript context and selected boundary rail;
- all factor explanations/evidence/missing-data rules;
- caveats, confidence, algorithm/model provenance and lineage;
- reviewer approval/rejection with rationale;
- immutable source-bounded boundary revision;
- review history and source asset link.

## Persistence and migration evidence

New tables:

- `discovery_runs`;
- `clip_candidates`;
- `candidate_score_components`;
- `candidate_topics`;
- `candidate_reviews`.

Migration chain:

```text
88a5dfd612fc -> 5708ce50be06 -> b71f2e44e1fe
```

SQLite evidence:

- fresh `base → head` succeeded;
- all five tables and normalized-topic index were inspected;
- Phase 3-only downgrade to `5708ce50be06` removed every Phase 3 table;
- Phase 3 reapply succeeded;
- full `head → base → head` succeeded;
- final revision was `b71f2e44e1fe`;
- `alembic check` reported: `No new upgrade operations detected.`

Offline PostgreSQL evidence:

- upgrade stream: 25,668 bytes;
- 13 assertions covered all Phase 3 tables, JSONB, topic index, revision marker and exactly-once native enum creation/reuse;
- Phase 3-only downgrade stream: 711 bytes;
- all Phase 3 tables/new enums were dropped;
- pre-existing `provider_mode` was neither recreated by Phase 3 nor dropped by its downgrade.

This is static PostgreSQL compilation, not live PostgreSQL execution.

## Automated validation

### Backend

Final gate:

```text
Ruff format/check: passed
mypy: Success — 67 source files
pytest: 100 passed
branch-aware coverage: 80.07% (required minimum 70%)
```

The suite includes retained Phase 1/2 regressions and Phase 3 positive/negative cases for:

- deterministic/reproducible planning and scoring;
- short, long and multilingual sources;
- invalid/missing timestamps and source-boundary violations;
- duplicate/overlap thresholds;
- factor weights/evidence and platform fallback;
- low/missing confidence and missing word timestamps;
- run idempotency, failed-run retry and pagination/filtering;
- editor/reviewer role boundaries and cross-tenant `404`;
- approval, rejection, terminal state and immutable revisions;
- transcript prompt injection;
- malformed JSON, out-of-range evidence references and provider timeout;
- disabled provider and hybrid fallback;
- input/output/response and estimated-cost caps;
- sanitized provider body errors and request-ID provenance;
- real generated media through FFmpeg/FFprobe 7.1.5.

No provider fixture is counted as a live external-provider test.

### Frontend

```text
Oxlint 1.90.0: 0 warnings / 0 errors across 23 files and 96 rules
TypeScript strict no-emit: passed
Next.js 16.4.3 optimized build: passed
Routes built: 9
```

The build includes dynamic `/discovery` and `/media/[id]` routes. A local runtime smoke returned HTTP 200 for API root, liveness, readiness, overview, discovery and media pages; the web `/readyz` proxy also returned `ready`. This is not a browser end-to-end, visual-regression or accessibility certification.

### Dependency and static security gates

- `npm audit --audit-level=high`: 0 vulnerabilities;
- `pip-audit`: no known vulnerabilities in auditable dependencies; the local `zohra-clipos 0.3.0` package is not on PyPI and is reported as skipped;
- secret-shaped material scan: passed;
- no runtime `.env`, private database or media included;
- executable route scan: 36 routes, no Phase 4+ endpoint;
- forbidden publishing/payment/social SDK scan: passed;
- static Compose parse: five expected services, three volumes and non-root API/worker markers passed;
- Docker runtime was unavailable, so `docker compose config/build/up` is not claimed.

## Requirement traceability

| Phase 3 requirement | Evidence | Status |
|---|---|---:|
| Multiple timestamped duration-target windows | planner, schemas, service, long/short tests | Complete |
| Sentence boundaries, padding, word timestamps/confidence | planner + boundary excerpt/alignment + tests | Complete |
| Duplicate/high-overlap detection | metrics, dedupe service, pairwise persisted assertion | Complete |
| Weighted/versioned/explainable factors | scorer + component table/API/UI | Complete |
| Hook/clarity/novelty/emotion/usefulness/story/platform fit | 13 documented factors | Complete |
| Evidence, caveats and uncertainty | component/risk/confidence persistence and UI | Complete |
| No virality promise | API metadata, rationale, UI/docs/tests | Complete |
| Five lifecycle states | enum/service/database/UI | Complete |
| Human context, rationale, feedback and boundary edit | context/review/revision APIs and workbench | Complete |
| Optional provider-neutral model | gateway/factory/three strategies | Complete |
| Budget/rate/resource controls | global rate limit, model limits, cost preflight/confirmation | Complete |
| Strict model schema and prompt-injection defense | fixed prompt, strict Pydantic, option allowlist, tests | Complete |
| Usage/latency/cost/failure/version tracking | run, usage ledger, factors and UI | Complete |
| Tenant isolation/audit/idempotency | service scoping, DB keys, audit, tests | Complete |
| Rollback-safe migration | SQLite round trips + offline PostgreSQL DDL | Complete with live-PostgreSQL limitation |
| Candidate review dashboard | `/discovery` | Complete at build/static level |
| Rendering/publishing/outreach/payment excluded | route/dependency scan and no executor | Complete |

## Honest limitations and remaining gates

1. **No live PostgreSQL.** Offline SQL and SQLite execution do not prove native enum/JSONB behavior, row locks, rollback under load or concurrent reviews.
2. **No live Redis.** Separate-worker delivery, outage and recovery are unverified.
3. **No Docker runtime.** Compose remains configuration-reviewed only.
4. **No live external AI provider.** OpenAI-compatible/Anthropic/transcription behavior is contract/mock evidence. No provider quality, retention, pricing or availability claim is made.
5. **Cost is estimated.** Rates can change and token accounting differs by provider. Operators must reconfirm prices and caps.
6. **Heuristics are not performance predictions.** There is no labeled editorial/engagement benchmark, calibration study or fairness evaluation.
7. **No browser E2E/accessibility certification.** The dashboard passed lint/type/build only.
8. **No production media sandbox/malware scanner.** FFmpeg remains a high-risk parser.
9. **Local storage is development-only.** S3, retention/deletion automation, legal hold and backup/restore proof are missing.
10. **Discovery is synchronous.** This is bounded and intentional until typed queue routing/outbox semantics exist.
11. **No raw media preview route.** Review uses timestamped transcript source context and source asset evidence; final media production belongs to Phase 4.
12. **No production claim.** Passing this gate means the authorized Phase 3 implementation and local evidence are complete, not that deployment to customers or regulated data is safe.

## Operator checklist

Before any real provider is enabled:

- confirm media rights and provider data-processing terms;
- inject secrets outside source control;
- select an approved model;
- set current input/output rates and `CLIPOS_MODEL_COST_RATES_CONFIRMED=true`;
- set a conservative discovery cost cap;
- run an approved non-sensitive fixture and inspect provider status/usage;
- keep deterministic mode available;
- require reviewer rationale for every decision;
- do not treat an approved candidate as fact-checked, legally cleared or likely to perform.

## Delivery files

- source tree: `/home/user/zohra-clipos`
- clean archive: `/home/user/Zohra_ClipOS_Phase_3.zip`
- archive checksum: `/home/user/Zohra_ClipOS_Phase_3.zip.sha256`
- internal source manifest: `docs/PHASE_3_SOURCE_HASHES.sha256`

## Authorization boundary

Phase 3 implementation stops here. Phase 4 remains locked. Continue only after human review of this handoff and separate explicit authorization.
