# Database and migrations — Phase 10

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
c7d40e02a0b4  Phase 7 analytics, experiments and learning
    ↓
0b728736cdd4  Phase 8 opportunity hunter and commercial fit
    ↓
e37c610043af  Phase 9 sales assistant, CRM, onboarding and revenue
    ↓
a162cab389d6  Phase 10 executive orchestration (head)
```

All revisions are additive. Phase 10 does not rewrite an accepted historical migration.

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

## Phase 8 tables

### `opportunity_sources`

One tenant-configured discovery origin:

- tenant, creating user, kind (`seed_list`, `rss_feed`, `public_site`, `search_provider`) and operator name;
- free-form `config` JSON (an RSS source carries its `feed_url` here; the URL is validated again by the SSRF guard at fetch time);
- `status` (`enabled`/`disabled`) and the truthful `adapter_mode` of the adapter that most recently served the source;
- `last_synced_at` for the most recent completed sync.

### `opportunity_sync_runs`

One tenant-scoped import pass over a single source:

- tenant, source (cascade), requesting user (nullable) and optional durable background job (unique);
- lifecycle state and the serving adapter's truthful `provider_mode`;
- records received/created/merged, possible duplicates flagged and suppressed records skipped — all check-constrained non-negative;
- sanitized failure category/message and start/finish timestamps.

### `opportunities`

One discovered creator/content opportunity:

- tenant, source (cascade), optional sync run (set null) and the source's `external_key` — `UNIQUE (tenant_id, source_id, external_key)`;
- normalised identity and research fields: title, creator name/handle, canonical URL, platform, a *labelled* public contact route (never scraped private contact data), niche, language, audience band and posting cadence;
- `evidence` JSON carrying per-field provenance (adapter, fetch time, source URL), the scoring signals and the raw source record;
- `dedup_key` (indexed per tenant) driving conservative exact-match merging, and `possible_duplicate_of_id` (set null) recording a fuzzy match flagged for human review — fuzzy matches are never merged automatically;
- lifecycle state (`new`, `reviewing`, `shortlisted`, `rejected`, `suppressed`) and the explainable `overall_score` (check-constrained 0–100 when present) with its `score_version`.

### `opportunity_score_components`

One explainable scoring factor per opportunity, mirroring `candidate_score_components`: key, label, meaning, nullable score (NULL when the factor is not applicable), weight, weighted score, applicability flag, evidence list, missing-data behaviour and scoring version. `UNIQUE (opportunity_id, key)`; components are replaced wholesale on every rescore.

### `opportunity_suppressions`

Tenant do-not-import rules: match type (`creator_handle`, `domain`, `keyword`), the normalised match value, a human reason and the creating reviewer. `UNIQUE (tenant_id, match_type, match_value)`. Matches are enforced at import and applied to existing rows when a suppression is created.

### `opportunity_handoffs`

The versioned Phase 9 intake package for one opportunity (`UNIQUE (opportunity_id)`): the `opportunity-handoff.v1` payload JSON (opportunity, evidence provenance, score components, `outreach_status: not_sent`), payload version, state (`draft`, `ready`) and creating user. Creating or marking a handoff ready sends nothing anywhere.

## Phase 8 native enums

```text
opportunity_source_kind, opportunity_source_status, opportunity_sync_state,
opportunity_state, suppression_match_type, opportunity_handoff_state
```

The Phase 8 migration creates exactly these six types on PostgreSQL and drops them on downgrade; `provider_mode` is referenced from Phase 1 without recreation.

## Phase 9 tables

### `crm_contacts`

One person known to the CRM:

- tenant, display name, optional organisation and role/title, and a *labelled* public contact route (never a scraped private address or number);
- optional source opportunity (set null) linking back to the Phase 8 record the contact came from;
- `dedup_key` (indexed per tenant) driving exact-match merging, and `possible_duplicate_of_id` (set null) recording a fuzzy match flagged for human review — fuzzy matches are never merged automatically;
- the opt-out pair `suppressed` + `suppression_reason`: a suppressed contact is refused new sales drafts, and suppression survives merges.

### `crm_accounts`

One prospect or client organisation:

- tenant, name, lifecycle `stage` (the `crm_stage` enum), optional owner user (set null) and primary contact (set null);
- optional source opportunity and source handoff — `UNIQUE (source_handoff_id)` and `UNIQUE (tenant_id, source_opportunity_id)` are what make Phase 9 intake idempotent;
- `dedup_key` (indexed per tenant) and `possible_duplicate_of_id` for the same conservative dedup as contacts;
- `next_action`/`next_action_at` and a free-text `notes_summary`.

### `crm_pipeline_events`

The append-only history for accounts and contacts: tenant, optional account (cascade) and contact (cascade), `event_kind` (`created`, `intake`, `stage_transition`, `merge`), nullable from/to stages, actor and reason, and `created_at`. Rows are never updated or deleted by the service; stage history and reviewed merges are both recorded here.

### `sales_asset_drafts`

One draft sales asset (outreach message, follow-up, proposal outline, discovery agenda, onboarding checklist):

- tenant, account (cascade), optional contact (set null) and `kind`;
- title and body, plus the `evidence` JSON citation list the asset was assembled from — a draft with zero citations cannot be created;
- `status` (`draft`, `in_review`, `approved`, `rejected`, `archived`) — there is deliberately no sent state, because Phase 9 has no send path;
- `template_version`, `created_by` (`system` for the deterministic generator, otherwise the authoring user's id) and the reviewer fields.

### `crm_tasks`

Follow-up tasks per account: tenant, account (cascade), title, optional due date, `status` (`open`, `done`, `cancelled`), optional assignee and creating user. Assignees are validated as tenant members by the service.

### `revenue_records`

One money-related record per row, with kinds kept strictly distinct: `kind` (`quote`, `contract`, `invoice`, `payment`, `refund`, `recurring`), `amount_minor_units` (bigint, check-constrained non-negative), 3-letter `currency`, `status` (`draft`, `issued`, `paid`, `void`, `overdue`), an optional operator-recorded `external_reference`, `occurred_at` and notes. Summaries count commitment kinds only in issued/paid/overdue states and money kinds only when `paid`, so a quote is never reported as collected revenue.

### `onboarding_records`

One onboarding record per client account (`UNIQUE (account_id)`): tenant, account (cascade), the `checklist` JSON state (versioned items with key/label/done), the `milestones` JSON list, `status` (`not_started`, `in_progress`, `blocked`, `completed`) and creating user. Completion requires every checklist item done.

## Phase 9 native enums

```text
crm_stage, crm_task_state, sales_asset_kind, sales_asset_state,
revenue_kind, revenue_state, onboarding_state
```

The Phase 9 migration creates exactly these seven types on PostgreSQL and drops them on downgrade.

## Phase 10 tables

### `agent_definitions`

One named agent contract: tenant (nullable — a null tenant with `is_system` marks a platform default definition), `name`, `role` (`researcher`, `planner`, `analyst`, `reviewer`, `executor`), the `contract` JSON (allowed action kinds — validated against the closed registry, so a contract can narrow but never widen it — input/output schema versions, per-run cost budget, timeout seconds, max steps), `status` (`enabled`, `disabled`) and a `version` counter the service increments on every update. There are no seeded system definitions; with none configured, execution uses a built-in default contract recorded as `agent_definition_id = NULL` on runs.

### `executive_controls`

The tenant kill switch and budget ceilings — exactly one row per tenant (`UNIQUE (tenant_id)`), created lazily by the service: `mode` (`normal`, `human_approval_all`, `paused`), nullable `daily_cost_ceiling_minor` and `per_action_cost_ceiling_minor` (null means uncapped), and `updated_by_user_id`. This is the tenant layer only; the Phase 1 platform `SystemControl` pause applies independently on top of it.

### `executive_plans`

One executive plan: tenant, title, objective, the `status` lifecycle (`draft`, `pending_approval`, `approved`, `executing`, `completed`, `failed`, `paused`, `cancelled`), `created_by` (`system` for planner output, otherwise the creating user's id) plus the creating user FK, `rationale`, the `evidence` JSON snapshot the planner consumed (per-section `as_of` timestamps), `total_estimated_cost_minor`, the optional Phase 1 `approval_request_id` (set null) with approver and timestamp, the execution job and requesting user recorded when execution is queued, start/finish timestamps, and the failure category/message pair.

### `executive_actions`

One planned action: tenant, plan (cascade), `sequence` (`UNIQUE (plan_id, sequence)`), `kind` — the `executive_action_kind` enum is the closed registry expressed at the database level — the strict-schema-validated `payload` JSON, the registry-stamped `autonomy_class` (`auto_internal`, `approval_required`), the `status` lifecycle (`pending`, `running`, `succeeded`, `failed`, `skipped`), cost estimate and incurred minor units, the `result` JSON, failure category/message, the agent definition used (set null) and `executed_at`.

### `agent_runs`

One execution record per dispatched action: tenant, the agent definition (set null; null means the built-in default ran) with the snapshot `agent_name` and `agent_role` (so history survives later definition edits), optional plan, action and Phase 1 `usage_cost_event` links (all set null), `status` (`queued`, `running`, `succeeded`, `failed`, `timed_out`), steps used, cost incurred, start/finish timestamps and failure category/message. Executive costs are internal orchestration accounting in minor units; no run in v1 creates a `UsageCostEvent`, because no provider call is made.

### `executive_proposals`

One self-improvement decision record: tenant, `kind` (`scoring_weights`, `prompt`, `policy`, `planner_rule`, `other`), title, summary, the `change_payload` JSON, `state` (`proposed`, `approved`, `rejected`) and a `version` counter. There are deliberately **no** applied-state or applied-at columns: an approved proposal records a human decision and nothing more — applying it is a separate, manual, audited configuration change outside this system, and no code path in Phase 10 mutates configuration from a proposal.

## Phase 10 native enums

```text
agent_role, agent_definition_status, agent_run_state,
executive_plan_state, executive_action_kind, autonomy_class,
executive_action_state, executive_control_mode,
executive_proposal_kind, executive_proposal_state
```

The Phase 10 migration creates exactly these ten types on PostgreSQL and drops them on downgrade.

## Tenant isolation

Every Phase 2–10 domain table carries `tenant_id`. Services never authorize by a bare object UUID: tenant predicates are included in candidate, plan, job, output, defect, rights, evaluation, decision, verification, OAuth connection, publication, publication-event, collection-run, snapshot, experiment, variant, recommendation, opportunity-source, sync-run, opportunity, score-component, suppression, handoff, CRM contact, CRM account, pipeline-event, draft, task, revenue-record, onboarding, agent-definition, executive-control, executive-plan, executive-action, agent-run and executive-proposal queries. Storage is also tenant-prefixed. (`agent_definitions` additionally carries platform rows with a null tenant; those are readable as system defaults but never writable through tenant endpoints, and a tenant predicate never matches another tenant's rows.)

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

# Roll back Phase 10 only
.venv/bin/alembic downgrade e37c610043af

# Restore Phase 10
.venv/bin/alembic upgrade a162cab389d6
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
