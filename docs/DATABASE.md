# Database and migrations — Phase 6

## Supported modes

| Mode | Database | Schema policy |
|---|---|---|
| Development/test | SQLite + `aiosqlite` | Alembic recommended; controlled tests may use `Base.metadata.create_all` |
| Staging/production configuration | PostgreSQL + `asyncpg` | Alembic only; automatic schema creation rejected at startup |

SQLite is a development adapter. PostgreSQL is required in protected environments but was not available for a live Phase 6 test; PostgreSQL results below are offline SQL contracts only.

## Linear migration chain

```text
88a5dfd612fc  Phase 1 foundation
    ↓
5708ce50be06  Phase 2 ingestion and transcription
    ↓
b71f2e44e1fe  Phase 3 explainable clip discovery
    ↓
8f0da12e33a4  Phase 4 validated media rendering
    ↓
43b5bc652f2e  Phase 5 mandatory quality and rights gates
    ↓
d4a9a9290b9a  Phase 6 gated YouTube publishing
    ↓
c7d40e02a0b4  Phase 7 analytics, experiments and learning (head)
```

All revisions are additive. Phase 7 does not rewrite an accepted historical migration.

## Phase 4 tables

### `tenant_render_profiles`

One mutable branding policy per tenant:

- tenant and last updater
- enable flag and optional text (maximum 80 characters)
- approved position, opacity and font size
- one-row-per-tenant uniqueness
- database checks for opacity `0.1–1.0` and font size `12–144`

A render plan snapshots this policy; changing the profile never changes an existing plan.

### `render_plans`

Immutable editing contract and queue authority:

- tenant, approved candidate, source asset, transcript and requester
- plan/renderer versions
- tenant-unique client idempotency key plus canonical request hash
- preset, approved source bounds, duration and candidate boundary revision
- aspect ratio, dimensions and frame rate
- requested/applied crop, smart-crop truth flag and fallback reason
- caption mode/style, safe area, overlays and branding snapshot
- explicit audio settings, video/audio codecs and container
- server-generated output prefix
- source-time mapping, provenance and limitations (portable JSON/JSONB)

Checks enforce positive ordered bounds, exact duration/bounds equality, positive dimensions/frame rate and positive boundary revision. Foreign keys use `RESTRICT` so accepted source evidence cannot be silently deleted beneath a plan.

Uniqueness/indexes:

- `UNIQUE (tenant_id, idempotency_key)`
- `INDEX (tenant_id, candidate_id, created_at)`

### `render_caption_segments`

Persisted source/output caption mapping:

- plan, tenant and ordinal
- source start/end and output start/end in milliseconds
- sanitized Unicode text
- line/character count and timing source

Checks enforce nonnegative ordered timestamps, one or two lines and positive text size. `(render_plan_id, ordinal)` is unique.

### `render_jobs`

Lifecycle and bounded retry state:

- one-to-one plan and durable `background_jobs` reference
- requester, `render_state`, progress and stage
- cancellation flag, current attempt/max attempts
- renderer version, elapsed time
- safe failure category/message/details
- start/finish/audit timestamps

Checks enforce progress `0–100`, nonnegative attempts/elapsed time and a positive retry budget.

PostgreSQL uses the native `render_state` enum:

```text
QUEUED, VALIDATING, RENDERING, VALIDATING_OUTPUT,
SUCCEEDED, FAILED, CANCELLED, NEEDS_REVIEW
```

SQLite uses SQLAlchemy’s portable enum representation. Other render configuration values are bounded strings and validated by strict API schemas/service allowlists rather than database-native enum types.

Uniqueness/indexes:

- one render job per `(tenant_id, render_plan_id)`
- one render job per background job
- `INDEX (tenant_id, state, created_at)`

### `render_outputs`

Append-only validated attempts:

- tenant, job, plan, attempt number and current flag
- unique private object key, SHA-256, size and content type
- optional SRT key/checksum
- container duration, codecs, dimensions, frame rate and audio layout
- FFprobe version/payload
- validation report, complete provenance and renderer version

No row is inserted before output validation passes. Checks enforce positive attempt, size, duration, dimensions and frame rate. Attempt numbers are unique per job; storage keys are unique per tenant.

A retry marks a previous current output non-current but preserves its evidence.

### `render_defects`

Append-only reviewer reports attached to the exact validated output:

- tenant/job/output/reporter
- bounded category and detailed description
- meaning/boundary-change flag
- optional requested resolution and timestamp

A meaning/boundary defect prevents retry against the old plan.

## Phase 5 tables

### `rights_evidence`

One immutable operational permission assertion plus an explicit revocation state:

- tenant, source asset and optional exact candidate/render scope;
- human recorder and optional revoker;
- scope, basis and review status enums;
- source owner, evidence type/reference and optional evidence checksum;
- permission/effective/expiration timestamps or explicit no-expiration assertion;
- allowed platform and territory arrays;
- commercial/editing/AI-processing booleans;
- attribution, license terms, restrictions, campaign scope and six material clearances;
- evidence version, revocation actor/time/reason and ordinary creation/update timestamps.

Database checks require explicit duration and expiration after effective time. Strict request schemas enforce mutually consistent scope, unique platforms/territories, attribution and timezone rules. There is no update endpoint; revocation is the only intentional mutation and is audited.

### `quality_evaluations`

One versioned aggregate for an exact render output/destination/territory:

- tenant, render job/output/plan, candidate, asset and selected rights evidence;
- human requester and tenant-unique idempotency key;
- destination, territory and optional campaign;
- automated/current outcome;
- evaluation/policy versions;
- protected-input and evidence hashes;
- model-assistance truth flag;
- policy explanation, missing evidence, remediation and hard-block arrays;
- canonical signed approval payload/signature/expiration when independently approved.

An evaluation is never silently reused for a new render, rights row, destination, territory or campaign.

### `quality_check_results`

Exactly one row per evaluation and required check kind. It stores status, severity, deterministic/model/human source, structured evidence, confidence `0–1`, check version, remediation and hard-block flag. The unique `(evaluation_id, kind)` constraint prevents duplicate check kinds from being mistaken for a complete council.

### `quality_review_decisions`

Append-only human decision evidence:

- reviewer and source (`HUMAN` in the implemented service);
- approve/reject/request-revision action;
- from/to outcomes;
- mandatory reason and acknowledged uncertain-check IDs;
- immutable creation timestamp.

The service enforces reviewer role, different requester/reviewer identities and non-overridable hard failures.

### `gate_verifications`

Append-only accepted **and denied** preflight attempts:

- exact evaluation, human caller and checkpoint;
- accepted flag, observed protected-input hash and failure reasons;
- canonical verification payload and HMAC signature;
- verification and short validity timestamps.

A row is audit evidence, not a reusable publishing token. Every call re-evaluates current server state.

## Phase 5 native enums

The migration creates each PostgreSQL type exactly once and drops it only after dependent tables:

```text
rights_scope, rights_basis, rights_evidence_status,
destination_platform, gate_outcome, gate_checkpoint,
quality_check_kind, quality_check_status, quality_severity,
evidence_source, gate_review_action
```

SQLite uses portable SQLAlchemy enum storage. Structured Phase 5 arrays/objects use JSON on SQLite and JSONB on PostgreSQL.

## Phase 6 tables

### `publishing_oauth_states`

Short-lived, one-time OAuth/PKCE authorization evidence:

- tenant and initiating user, provider and redirect URI;
- SHA-256 state digest rather than the browser state value;
- context-bound AES-256-GCM envelope containing the PKCE verifier;
- issue, expiry and one-time consumption timestamps;
- provider denial/error category without token material.

The state digest is unique, expired/consumed rows cannot be reused, and the callback resolves tenant/user context from the stored state rather than from unauthenticated query parameters.

### `publishing_connections`

One encrypted grant for an exact user/provider/destination:

- tenant/user/provider and provider destination ID/display name;
- explicit connection state, granted scopes and token expiry;
- context-bound AES-256-GCM access/refresh envelope;
- refresh, validation, revocation and reconnect timestamps;
- sanitized failure category/message and ordinary audit timestamps.

`UNIQUE (tenant_id, user_id, provider, provider_destination_id)` prevents duplicate logical grants. Tokens are not stored in plaintext columns and are never returned through response schemas.

### `publications`

One immutable release request with mutable, bounded execution state:

- tenant, requesting user, connection, exact render/output/evaluation and durable background job;
- optional exact public-approval request plus mandatory acceptance and optional pre-upload gate-verification IDs;
- provider/destination, tenant-unique idempotency key and canonical request hash;
- visibility, normalized metadata, territory/campaign and validated content length/type;
- lifecycle state, progress/stage, committed byte offset and retry budget;
- encrypted resumable-session envelope;
- provider publication ID/URL/processing status, sanitized response and analytics handoff;
- cancellation, uncertain-result, failure/remediation and provider timing fields.

The service reserves public visibility for an exact independent approval. A publication never references a different render or stale gate silently, and an idempotency-key replay must match the canonical request hash.

### `publication_events`

Append-only state/progress evidence:

- tenant/publication and unique monotonic sequence;
- event kind, from/to states, progress and stage;
- bounded safe message, sanitized structured event data and timestamp.

The service flushes the publication parent and each event before allocating another sequence in the same transaction. This prevents duplicate `(publication_id, sequence)` values during reconciliation-to-success transitions.

## Phase 6 native enums

```text
publishing_provider, oauth_connection_state, publish_visibility,
publish_state, publish_event_kind
```

The Phase 6 migration creates and tears down these PostgreSQL types after/before their dependent tables as appropriate. SQLite uses portable enum values; provider metadata and event evidence use JSON/JSONB.

## Phase 7 tables

### `analytics_collection_runs`

One tenant-scoped collection pass:

- tenant, requesting user (nullable for system triggers) and optional durable background job (unique);
- optional provider filter, lifecycle state and the serving adapter's truthful `provider_mode`;
- publications considered, snapshots written, metrics reported/unavailable counts (check-constrained non-negative);
- sanitized failure category/message and start/finish timestamps.

### `analytics_metric_snapshots`

One provider metric outcome for one publication and day-grained window:

- tenant, publication (cascade), optional collection run (set null), provider and provider publication ID;
- metric name, unit, metric definition version, source label and fetch timestamp;
- `availability` (`reported`, `missing`, `unavailable`, `permission_denied`, `stale`) with a nullable float value;
- a check constraint enforces that value is present exactly when availability is `reported` or `stale` — an unreported metric can never carry an invented zero;
- `UNIQUE (publication_id, metric, window_start, window_end, source)` is the collection dedup key; re-collection refreshes the row in place.

### `experiments` and `experiment_variants`

Tenant-scoped experiment records: hypothesis, lifecycle state, eligibility and success-criteria JSON, canonical success metric, optional start/end and notes. Variants belong to an experiment (cascade), are unique by name within it, and must link to a clip candidate or a publication (check constraint); their `config` JSON records what the variant changes.

### `recommendations`

Bounded learning-loop output:

- tenant, kind (`hook`, `duration`, `captions`, `posting_time`, `format`) and state (`proposed`, `approved`, `rejected`, `applied`);
- title, rationale and the evidence JSON the recommendation was derived from (group statistics, publication IDs, fingerprint);
- algorithm version and a system `created_by` identity — recommendations are never user-authored;
- reviewer user/timestamp/note for the human decision. No service path sets `applied` in Phase 7.

## Phase 7 native enums

```text
analytics_collection_state, metric_availability, experiment_state,
recommendation_kind, recommendation_state
```

The Phase 7 migration creates exactly these five types on PostgreSQL and drops them on downgrade; `publishing_provider` and `provider_mode` are referenced from earlier migrations without recreation.

## Tenant isolation

Every Phase 2–7 domain table carries `tenant_id`. Services never authorize by a bare object UUID: tenant predicates are included in candidate, plan, job, output, defect, rights, evaluation, decision, verification, OAuth connection, publication, publication-event, collection-run, snapshot, experiment, variant and recommendation queries. Storage is also tenant-prefixed.

The schema uses ordinary foreign keys plus service-level tenant predicates rather than PostgreSQL row-level security. Live PostgreSQL RLS was not claimed.

## JSON portability

Phase 4–6 structured columns use:

```python
sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
```

This preserves SQLite development compatibility and emits JSONB in PostgreSQL offline DDL. Structured objects are generated server-side or validated by strict Pydantic schemas before persistence.

## Parent/child flush ordering

Plan, background-job, render-job and publication UUIDs are generated in the application. Because mutable ORM relationships are intentionally not used, parents are explicitly flushed before render jobs/caption children and before publication events. Each publication event is flushed before allocating the next immutable sequence in the same transaction. This provides deterministic foreign-key and event ordering in both SQLite and PostgreSQL.

## Migration commands

```bash
export PYTHONPATH=apps/api

# Apply current head
.venv/bin/alembic upgrade head

# Verify ORM/migration agreement
.venv/bin/alembic check

# Roll back Phase 7 only
.venv/bin/alembic downgrade d4a9a9290b9a

# Restore Phase 7
.venv/bin/alembic upgrade c7d40e02a0b4
```

Offline PostgreSQL contracts:

```bash
CLIPOS_DATABASE_URL='postgresql+asyncpg://clipos:offline@example/clipos' \
  .venv/bin/alembic upgrade head --sql > /tmp/phase6-pg-up.sql

CLIPOS_DATABASE_URL='postgresql+asyncpg://clipos:offline@example/clipos' \
  .venv/bin/alembic downgrade d4a9a9290b9a:43b5bc652f2e --sql \
  > /tmp/phase6-pg-down.sql
```

Protected-environment settings (secret, PostgreSQL, Redis-required and external worker) must also be supplied when loading settings for offline generation.

## Phase 6 validation evidence

A clean SQLite contract passed:

1. base-to-head upgrade through all six revisions;
2. `alembic check` with **no new operations detected**;
3. Phase 6 downgrade to `43b5bc652f2e`;
4. re-upgrade with all four Phase 6 tables restored;
5. head marker exactly `d4a9a9290b9a` and zero foreign-key violations.

Full-chain offline PostgreSQL SQL generation produced 1,125 lines and passed static assertions for all four Phase 6 table names and the `d4a9a9290b9a` revision marker. Phase 6-only upgrade/downgrade enum, index and ordering assertions are recorded in `PHASE_6_HANDOFF.md`.

This remains static dialect evidence only. A live PostgreSQL migration/rollback, concurrent idempotency/event sequencing test, Redis worker-recovery test and backup/restore exercise are mandatory before any production claim.
