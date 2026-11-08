# Zohra ClipOS — Phase 10 Handoff

Date: 2026-11-08
Version: 1.0.0
Alembic head: `a162cab389d6`
Status: Phase 10 implemented in the working tree for gate review.

## Gate decision

**ACCEPT PHASE 10 as the executive orchestration backend — a deterministic planner, a closed internal action registry, human approvals, kill switches, cost ceilings and agent contracts — with the explicit limitations below.**

Version 1.0.0 means exactly one thing here: all build phases (1–10) are implemented. It is **not** a production-readiness certificate, and it must not be read as one — production readiness is the verdict of the Phase 11 audit and controlled launch, which has not happened. Earlier programme documents claimed `v1.0.0-rc2` prematurely; this delivery does not repeat that claim as fact.

This is also not an autonomy certificate. The executive can dispatch exactly six internal, reversible action kinds. It cannot publish, send, charge, delete, change permissions, change budgets or ceilings, change its own autonomy, apply its own improvement proposals, or follow any instruction found in content. None of those capabilities exist in its registry, routes, services or dispatch code — they are absent, not merely switched off.

## Design decisions

### The closed action registry is the safety boundary

`clipos/services/executive_actions.py` holds `ACTION_REGISTRY`: the complete, code-level list of what the executive may ever dispatch in v1 — `generate_recommendations`, `run_analytics_collection` (Phase 7), `sync_opportunity_source` (Phase 8), `create_crm_draft`, `create_internal_task` (Phase 9) and `noop_report`. Each entry fixes the kind's autonomy class (`auto_internal` / `approval_required`), the agent role that may run it, a fixed internal cost estimate, and a strict pydantic payload schema (`extra="forbid"`). Kinds are an enum in the database, a literal in the registry and a branch in exactly one dispatch function; there is no generic "call a service" or "run a tool" action.

Forbidden kinds (publishing, sending, payments, deletion, permission, budget and autonomy changes, approving) are named in `FORBIDDEN_ACTION_KINDS` only so that refusals are precise: asking for one fails plan validation with `executive_action_forbidden`, an unrecognised kind fails with `executive_action_unknown`, and neither can be created, submitted, queued or executed. Agent contracts validate their `allowed_action_kinds` against the same enum, so configuration can narrow the registry per agent but can never widen it. Adding a seventh kind is a code change with its own review — never a runtime, configuration or model decision.

### ExecutiveProposal, not a reused Recommendation

Self-improvement uses a dedicated `ExecutiveProposal` model rather than Phase 7's `Recommendation`. The two are different kinds of object: a Recommendation is an analytics-derived *content* suggestion (which clip format, hook or posting pattern to try), produced by the learning loop from metric evidence and decided by a reviewer. An executive proposal is a *governance* record — a versioned proposed change to scoring weights, prompts, policies or planner rules — with different producers (operators or a future model-backed planner), different reviewers, and a hard requirement that approval must not change system behaviour. Reusing `Recommendation` would have blurred that line and risked a future "apply recommendation" path silently becoming a configuration-change path. `ExecutiveProposal` therefore has create/list/get/review only, carries `version` for future amendment chains, deliberately has no applied state or applied-at columns, and no code path in Phase 10 mutates any configuration from a proposal. Applying an approved proposal is a manual, separately audited change made outside this system.

### Deterministic planner first, model never required

The planner (`executive-planner-v1`) is five deterministic rules over trusted stored state. The Phase 3 Model Gateway exists and remains available for a future, separately authorised planner that may only *propose* registry-shaped draft actions through the same validation — but Phase 10 neither requires nor consults a model, and works with `CLIPOS_MODEL_PROVIDER=disabled`. A plan is therefore fully reproducible from the evidence snapshot it cites.

### Costs are internal accounting, not provider spend

Action estimates and incurred amounts are internal orchestration units (minor units) that exist so ceilings and agent budgets have something deterministic to bind to. They are not provider invoices: provider spend evidence remains the Phase 7 usage-cost summary over `UsageCostEvent`, and no Phase 10 run creates a `UsageCostEvent`, because dispatch makes no provider call.

## What was delivered

### Models and migration (`clipos/models/entities.py`, revision `a162cab389d6`)

- `agent_definitions` — tenant-scoped agent contracts (nullable tenant + `is_system` for platform defaults; none are seeded), role, contract JSON, status, version.
- `executive_controls` — one row per tenant: mode (`normal` / `human_approval_all` / `paused`), daily and per-action cost ceilings (null = uncapped), last updater.
- `executive_plans` — lifecycle `draft → pending_approval → approved → executing → completed / failed / paused / cancelled`, creator (`system` or a user id), rationale, evidence JSON with per-section `as_of` timestamps, total estimated cost, Phase 1 approval link, execution job/requester, failure category/message.
- `executive_actions` — plan child rows with sequence, registry kind, strict payload, registry-stamped autonomy class, lifecycle, estimates/incurred, result JSON, failure fields, executing agent link.
- `agent_runs` — one row per dispatched action with the agent name/role snapshot, steps used, cost incurred, timing and failure fields.
- `executive_proposals` — decision records as described above.

### Executive service and planner (`clipos/services/executive.py`)

- Evidence gathering reads only server-side state: latest analytics run and snapshot freshness, proposed recommendation count, succeeded publications; opportunity counts, top score, stale non-seed sources; CRM stage counts and overdue open tasks; provider cost totals (all-time and today) from the usage ledger plus executive-incurred-today and the tenant ceilings; background-job counts by state; the tenant control mode. Free text from records is copied into evidence as data and is never parsed.
- Planner rules v1: stale or missing analytics → `run_analytics_collection`; succeeded publications with no proposed recommendations → `generate_recommendations`; up to three stale enabled non-seed sources → `sync_opportunity_source`; up to three distinct accounts with overdue tasks → `create_crm_draft` (follow-up kind); every plan ends with `noop_report`. The plan rationale cites the rule IDs.
- Plan creation is one validated path shared by the API and the planner. Submission sends approval-required plans (or all plans under `human_approval_all`) through the existing Phase 1 `ApprovalService` as `executive_plan.execute` requests; all-auto plans within ceilings are auto-approved with an audit event. Validation problems on submit are audited as denied and refused with 422.

### Execution engine (`clipos/services/executive_processing.py`)

- Execution is a durable background job with the reserved kind `executive.execute`, created only through the executive domain endpoint; the generic jobs API refuses to create it (422 `reserved_job_kind`) and refuses generic cancel/retry for it (409), following the Phase 6/7/8 pattern. Stale running jobs are recovered by requeueing, and an interrupted plan returns to `approved` — completed actions are never re-run.
- The engine re-validates at execution time: tenant control not paused, platform `SystemControl` not paused (both are also re-checked between actions — a pause lands as plan state `paused` with remaining actions untouched, never as silent success), `CLIPOS_EXECUTIVE_ENABLED`, the linked approval still `APPROVED` and unexpired, full plan validation (registry, schemas, autonomy stamps, per-action and projected daily ceilings, agent contracts), and a live active membership for the executing principal (reconstructed from the execution requester, falling back to the plan's creator, mirroring the Phase 6 publish processor).
- Dispatch is a single function mapping the six registry kinds onto the existing domain services (`AnalyticsService`, `OpportunityService`, `CrmService`, and the evidence compiler for reports). Each action runs under its resolved agent contract: allowed kinds, a per-plan step cap per agent role, a literal per-run budget for defined agents, and a timeout around the dispatch awaitable (`asyncio.timeout`; 0 means no timeout, for tests). The first failure halts the plan: remaining pending actions are marked `skipped`, the plan is `failed` with the action's sanitised category, and every step is audited (`executive.plan_created`, `plan_submitted`, `plan_auto_approved`, `plan_execution_queued`, `plan_execution_started`, `action_succeeded`, `action_failed`, `plan_completed`, `plan_failed`, `plan_paused_mid_execution`, plus the approvals system's own events).

### Routes (`clipos/api/routes/executive.py`, schemas in `clipos/executive_schemas.py`)

`GET /executive/actions` (the registry, readable by any member); agent definition create/list/get/update (owner/admin; created disabled by default); controls `GET` (any member) and `PUT` / `pause` / `resume` (owner only, audited); `POST /executive/planner/run` (editor); plans create/list/get/submit/execute/cancel; `GET /executive/plans/{id}/runs`; proposals create/list/get and reviewer-only review. When `CLIPOS_EXECUTIVE_ENABLED=false`, planner runs, plan creation, submission and execution queueing are refused with `executive_disabled`.

### Web

Static home-page status copy only (phase orbit 10 of 11, version stamp, current-handoff section); no executive UI was built, as in Phases 7–9.

## Gate evidence

- Backend gate: **216 passed in 192.75s** (198 pre-Phase-10 baseline retained green + 18 new Phase 10 cases) with **73% branch-aware coverage** (70% enforced minimum; coverage run: 216 passed in 261.38s), including the retained genuine deterministic FFmpeg proof. Ruff check and format check pass on `apps/api/clipos`; mypy reports no issues in 134 source files. Baseline note: `ruff check apps/api` still shows the 7 pre-existing Phase 6 test-file errors that predate this phase; the new Phase 10 test file is lint-clean and no new debt was added.
- Migration: clean SQLite base→head through all ten revisions; the Phase 10 revision re-run is a no-op at head; `downgrade -1` returns to `e37c610043af` and re-upgrade restores head; `alembic check` reports no drift. Offline PostgreSQL DDL for the Phase 10 step: 9,404-byte upgrade (6 tables, 10 new native enum types) and a 945-byte downgrade that drops the tables and the ten new types.
- Focused Phase 10 evidence (18 test functions): the registry endpoint lists exactly the six kinds and forbidden/unknown/smuggled-payload plans are refused with the three stable error codes; the planner produces an evidence-cited plan from seeded state (stale collection, never-synced RSS source, overdue task, a proposed recommendation and a stored cost event all visible in the snapshot with `as_of` stamps, rule IDs in the rationale); a seed record titled with an instruction to publish, send and delete yields a plan containing only registry actions with schema-shaped payloads; an all-auto plan executes end to end (task created, report compiled from live state, two succeeded agent runs, durable job succeeded) and is auto-approved at queueing; an approval-required plan cannot execute before approval (409), the requester cannot decide their own approval (403), a reviewer approval enables execution, and the resulting CRM draft cites its opportunity evidence; a seed-list sync action executes through the Phase 8 service; tenant pause refuses queueing (409) and a platform pause parks a queued plan as `paused` with actions pending, then resumes to completion; a zero per-action ceiling refuses submission (422) and a daily ceiling lowered after queueing fails the plan at the execution gate; pausing the tenant between actions via a monkeypatched dispatch boundary leaves the remaining actions pending and the plan resumable to completion; a suppressed contact fails the draft action (`contact_suppressed`), halts the plan and skips the rest, creating neither draft nor task; agent contracts enforce step caps (`agent_step_cap_exceeded`), literal budgets (`agent_budget_exceeded`, with no run recorded for the blocked action) and kind restrictions applied at execution-time validation (`agent_kind_not_allowed`); the dispatch timeout helper raises on a real elapsed deadline and passes fast calls through; controls and agent administration are role-gated (editor 403s, invalid contracts 422, contracts naming `publish_video` 422) and `human_approval_all` forces even a report-only plan through approval; tenant isolation holds across plans, agent definitions, proposals, controls and planner evidence (a second tenant sees none of the first tenant's state); proposals complete their create/review lifecycle with no apply surface anywhere (route table and service surface asserted); the audit trail contains the full plan lifecycle plus the approvals system's `approval.requested`; and the generic jobs API refuses to create or cancel `executive.execute` jobs.

## Limitations and unverified boundaries

- **The planner is rules, not intelligence.** Five deterministic rules with fixed thresholds (24-hour analytics staleness, 7-day source staleness, caps of three) are a starting policy, uncalibrated against any real operating history — none exists yet. It has no model input, no learning from outcomes beyond the Phase 7 recommendation loop it can trigger, and no prioritisation beyond its rule order.
- **Timeouts are cooperative.** Contract timeouts bound the dispatch awaitable via `asyncio.timeout`; all six v1 dispatches are in-process service calls, so this is effective containment for v1, but it is not hard pre-emption of blocked native work and must not be described as such.
- **Step caps count agent runs per plan per role.** Two different definitions sharing a role share the plan's step budget for that role; budgets bind per action, not cumulatively across a plan (the plan-level bound is the daily ceiling). Both semantics are deliberate and recorded here so a future phase can tighten them knowingly.
- **Costs are estimates.** Registry estimates are fixed constants; incurred cost equals the estimate on success. They exist for ceilings and accounting shape, and reconcile against nothing external.
- **Phase 10's own service coverage is thinner than the package average** (`services/executive.py` at 47% line coverage against a 73% package total): the exercised paths are the ones the gate depends on — registry refusal, planning, approval, execution, pauses, ceilings, contracts, failure, isolation, proposals, audit — while some secondary branches (plan cancellation, definition update paths, approval-expiry edges, several evidence sub-branches) rest on design review rather than tests. Recorded here so Phase 11 can target them.
- **No scheduler.** Nothing runs the planner on a timer in this phase; plans are created by an operator call (or the API) and executed when queued. A periodic executive loop would be a deployment decision for Phase 11, with its own approval posture.
- **No system agent definitions are seeded.** Until an operator creates and enables definitions, execution uses the built-in default contracts and runs record `agent_definition_id = NULL` with `builtin-*` names — visible in run history, never hidden.
- The web console gained only static home-page status copy; there is no `/executive` UI.
- PostgreSQL, Redis and Docker Compose remain **unverified** live, exactly as in earlier phases; all Phase 10 evidence is SQLite + in-memory queue. Real YouTube analytics, real RSS fetching, and the user's three Phase 9 provider decisions (email, calendar, payment) are exactly as unverified/owed as Phase 9 left them — Phase 10 changes none of those states.

## Stop condition

Phase 11 remains locked. It is a production readiness audit and controlled live launch — repository and test evidence review, the full migration chain against disposable PostgreSQL, a running staging stack, a secrets inventory, observability and backup/restore checks, one authorised YouTube smoke test, and an explicit GO / CONDITIONAL GO / NO-GO decision — not another feature phase. Phase 10 grants no authority to publish, send, charge, delete, change permissions, budgets or autonomy, apply proposals, or contact anyone; and no Phase 11 activity may widen the executive's registry without its own separately authorised design.
