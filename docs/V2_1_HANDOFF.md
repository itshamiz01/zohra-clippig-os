# Zohra ClipOS — v2.1 Handoff (Campaign Economics)

Date: 2026-11-09
Version: 1.1.0
Alembic head: `ed6bc3ed6804`
Base: remote `main` at `4eed56b` (v1.0.0, all ten build phases merged)
Status: v2.1 implemented on `feat/v2-1-campaign-economics` for gate review. Not merged.

## Gate decision

**ACCEPT v2.1 as the campaign-economics backend — Campaign Hunter (manual/seed intake, qualification gating, append-only profitability assessments), the 7-factor Content Debt Score on Phase 8 opportunities, and payout attribution into Phase 9 revenue records — with the explicit limitations below.**

v2.1 adds a new money-adjacent domain, so its honesty rules are the release: a CPM or an expected-view count is never invented (a campaign that lacks either is `unassessable`, with the missing inputs named on the persisted assessment); expected money and collected money are never mixed (a payout exists only as a Phase 9 revenue record of kind `campaign_payout`, written through the existing revenue service — there is no parallel money table); and no external campaign source is connected. Nothing in this release scrapes Whop or any other site. Intake is an operator creating records by hand or importing owner-supplied JSON listings, with source kind and reference stored on every record, in the Phase 8 provenance style.

The Phase 10 executive action registry is untouched. Campaigns surface through a summary endpoint in the existing analytics style; the closed registry stays frozen per the Architecture Lock.

## Design decisions

### Profitability is persisted, append-only and reproducible

`CampaignService.assess_profitability` writes one immutable `campaign_assessments` row per computation, holding every input (expected views, CPM, currency, the three cost figures, the risk percentage), the derived figures (gross, risk adjustment, expected profit), a `capped_by_max_payout` flag, and — for the unassessable case — the list of missing input names. Recomputing creates a new row; history is never edited, so any past qualification decision can be re-derived from the assessment that justified it.

The math is integer-only, in minor units: gross = round-half-up(expected_views × CPM / 1000), capped at the campaign's recorded `max_payout_minor` when one is set; risk adjustment = round-half-up(gross × risk_percent / 100); expected profit = gross − editing − model − distribution − risk adjustment. There is no hidden fudge factor: the risk percentage is a recorded input on every assessment.

### Cost rates are configuration, not constants in code

Per-assessment cost and risk inputs may be given explicitly; otherwise they fall back to deployment settings `CLIPOS_CAMPAIGN_EDITING_COST_MINOR`, `CLIPOS_CAMPAIGN_MODEL_COST_MINOR`, `CLIPOS_CAMPAIGN_DISTRIBUTION_COST_MINOR` and `CLIPOS_CAMPAIGN_RISK_PERCENT` (all default 0), following the codebase's existing `Settings` pattern. Whichever value was used is copied onto the assessment row. These are deployment-level defaults, not per-tenant records — see Limitations.

### Qualification is gated, and the override is part of the record

A campaign may move `candidate → qualified` only when `rights_basis != unknown` (otherwise 422 `campaign_rights_unknown`) and a latest assessment that is assessable exists — or the caller supplies a non-empty override reason, which is stored on the campaign (`qualification_override_reason`) and flagged in the transition's audit event (`assessment_override: true`). An override never fabricates an assessment; it records that a human accepted the risk explicitly. All other transitions follow a fixed map; anything outside it is a 409.

### Expiry is a read-time flag, never a silent rewrite

A non-terminal campaign whose `deadline_at` has passed reads as expired (`expired: true` on responses, an `expired` effective status and an `expired_open` count in the summary), but its stored status is never mutated by a read. Lifecycle truth stays in the transition history; expiry is a derived view of it.

### Payouts reuse Phase 9 money discipline

Recording a payout validates currency against the campaign (422 `campaign_payout_currency_mismatch`) and resolves the CRM account (the request's `account_id`, else the campaign's linked `crm_account_id`, else 422 `campaign_payout_account_required`), then delegates to `CrmService.create_revenue_record` with kind `campaign_payout`, status `paid`, and the campaign id. Phase 9's revenue summary gains a `campaign_payout` bucket alongside — never inside — quoted/contracted/invoiced figures, and `collected` still counts only paid payment records. The campaigns summary reports expected (latest assessable gross) and collected (paid campaign payouts) per campaign, side by side, never netted.

### Content debt never estimates a library from nothing

The 7-factor Content Debt Score (`content-debt-v1`) mirrors the commercial scorer's applicability semantics: long-form hours 25, short-form utilisation gap 25, commercial potential 20, audience size 10, posting consistency 10, contactability 5, niche compatibility 5. A factor whose inputs were never recorded is unscored, contributes no points, and reduces the coverage percentage; the result is marked `partial`. Commercial potential reuses the persisted commercial-fit component scores (`audience_fit`, `performance_fit`) rather than re-deriving them.

One refinement the tests forced: contactability is always applicable (a missing route is itself an observation), but on its own it cannot produce a headline score — with no content factor applicable, the score is `null` rather than 0.0, because reporting "no debt" from no library evidence would be a false claim.

The unused-moments estimate is computed only when an episode count and an existing-shorts count are both recorded: `episodes × moments_per_episode − existing_shorts`, floored at zero, exposed with the moments-per-episode assumption actually used (explicit intake input, default 8) and a basis string naming it. It is labelled an estimate everywhere it appears.

## What was delivered

### Models and migration (revision `ed6bc3ed6804`)

- `campaigns` — tenant-owned: name, platform, sponsor label, source kind (`manual` / `seed_import`) + source reference, `cpm_rate_minor` + ISO currency, nullable `max_payout_minor` / `budget_minor` / `minimum_views` / `deadline_at` / `expected_views`, content restrictions, account requirements, `rights_basis` (`authorized` / `licensed` / `public_domain` / `unknown`), submission process, status (7 states), nullable CRM account link, qualification override reason, idempotency key + request hash (unique per tenant).
- `campaign_requirements` — campaign child rows: kind, description, required flag.
- `campaign_assessments` — append-only assessment ledger as described above.
- `campaign_publications` — campaign↔publication join (unique pair, both tenant-validated at link time).
- `opportunities` — four nullable intake columns: `library_episode_count`, `library_long_form_hours`, `existing_shorts_count`, `moments_per_episode_assumption`.
- `revenue_records` — nullable `campaign_id` FK (SET NULL) + tenant/campaign index; `revenue_kind` gains `CAMPAIGN_PAYOUT` (PostgreSQL: `ALTER TYPE ... ADD VALUE IF NOT EXISTS`; the downgrade leaves the value in place because PostgreSQL cannot remove enum values — documented in the migration; SQLite: the column is widened in batch mode so the migrated schema matches the models with zero drift).

### Campaign service and routes (`clipos/services/campaigns.py`, `clipos/api/routes/campaigns.py`, `clipos/campaign_schemas.py`)

- CRUD with idempotent creation (same key + same payload replays; same key + different payload is a 409), PATCH, requirements replacement on update.
- `POST /api/v1/campaigns/import` — owner-supplied JSON listings (bounded by `CLIPOS_CAMPAIGN_MAX_IMPORT_RECORDS`, default 200); deduplicates within the tenant on `source_reference` and reports created/skipped counts. Records without a reference always import — replay safety exists only for referenced records, and the import response makes the counts visible rather than hiding that.
- `POST /api/v1/campaigns/{id}/transitions`, `POST|GET /api/v1/campaigns/{id}/assessments`, `POST /api/v1/campaigns/{id}/publications`, `POST /api/v1/campaigns/{id}/payouts` (201, returns the created revenue record), `GET /api/v1/campaigns/summary`.
- RBAC follows the house pattern (editor writes, viewer reads, tenant isolation on every query). Audit actions: `campaign.created`, `campaign.updated`, `campaign.status_changed`, `campaign.assessed`, `campaign.publication_linked`, `campaign.payout_recorded`, `campaign.imported`.

### Content debt (Phase 8 extension)

- `clipos/opportunities/content_debt.py` — the pure scorer described above (96% measured coverage).
- The seed-list source contract and intake parsing accept the four library fields; the opportunity service persists them, mirrors them into evidence signals, and the opportunity list/detail responses carry a computed `content_debt` object (score, coverage, partial flag, per-factor components, the labelled unused-moments estimate). The existing 6-factor commercial score and all Phase 8 behaviour are unchanged.

## Real / mocked / disabled labelling

- **Real:** campaign CRUD, import of owner-supplied listings, qualification gating, profitability assessment, expiry flagging, publication linking, payout recording into Phase 9 revenue, campaigns summary, content-debt scoring and the unused-moments estimate. All of it is covered by the 25 new tests below.
- **Not connected:** any external campaign source. No Whop integration exists, no marketplace is polled, and nothing scrapes any website. Campaign records enter only through the API by an operator or an owner-supplied import file.
- **Disabled, unchanged:** Phase 9's communication and payment gateways remain disabled — a "payout" here is an operator recording money already received, never a payment being made. No money moves.
- **Web:** no campaign UI in this release (backend-first, per the Phase 7 precedent); the web home page copy was not changed.

## Gate evidence

- **Full suite: 241 passed in one run** (216 v1.0.0 baseline + 25 new v2.1 tests), 219.28s; the coverage run independently repeated it: 241 passed with **73% total branch-aware coverage** (70% enforced minimum; per-file note below).
- New tests (`apps/api/tests/test_v21_campaigns.py`, 25): campaign CRUD + requirements, create idempotency replay/conflict, tenant isolation, viewer read-only, import dedup/replay, exact profitability math (gross 250_000 on CPM 2500 × 100k views; 10% risk → 25_000; profit net of 15_000 recorded costs), max-payout capping, settings-default fallbacks, both unassessable cases, append-only assessment history, both qualification gates + recorded override (including its audit flag), invalid transition 409, expiry flag + summary counts, publication link + duplicate 409, payout → `campaign_payout` revenue record with the Phase 9 summary bucket keeping `collected` at 0, payout currency mismatch, payout account-required, summary expected-vs-collected separation, content debt at full coverage (≈90.9, estimate 1340 = 175×8 − 60) and partial coverage (30%, estimate 700), no-library-data stays unestimated, custom moments assumption (estimate 450), and the no-inputs unit case.
- Ruff: clean on the `clipos` package and the new test file; the 7 pre-existing Phase 6 test-file lint errors are unchanged from base (verified identical on the untouched base), and `ruff format` was applied to every file this release touched.
- Mypy (strict house config): **clean, 138 source files.**
- Migration: SQLite upgrade → downgrade to `a162cab389d6` → re-upgrade clean, with **zero drift** against the model metadata (autogenerate comparison). PostgreSQL (disposable `pgserver` PostgreSQL 16.2): full chain upgrade/downgrade/re-upgrade clean, **65 public tables** at head, `revenue_kind` contains `CAMPAIGN_PAYOUT`, the `fk_revenue_records_campaign_id_campaigns` foreign key and all four opportunity columns verified present.
- Per-file coverage note, stated plainly: `services/campaigns.py` measures 44% under this repository's ASGI test harness. That number is a measurement artifact of the harness, not untested code — the identical measurement on the untouched Phase 9 services, run against their own passing test files in this same environment, reads 42% (`services/crm.py`, `services/opportunities.py`), and the profitability figures the tests assert can only be produced by the lines the report calls missed. The comparable whole-suite figure is 73%, unchanged from Phases 9 and 10. `content_debt.py`, which the unit tests call directly, measures 96%.
- Environment note: test runs in this VM are sensitive to where the test temp directory lives — on the shared workspace mount, application startup measured 9–12s and exceeded the fixtures' 5-second lifespan timeout en masse; on tmpfs the same startup is ~0.6s. The gate runs above used `TMPDIR=/tmp` with disk space monitored (431 MB free at start; the Phase 8 `/tmp`-fill flake did not recur). Two genuine bugs were caught by the new tests during the build and fixed before this gate: the content-debt headline-score rule above, a missing schema default on the detail response, and naive/aware datetime normalisation in the expiry check (SQLite does not preserve tzinfo on read).

## Limitations and unverified boundaries

- No campaign web UI; operators use the API. A cockpit view belongs with the v2.3 business-cockpit slice.
- Cost and risk defaults are deployment-level `Settings`, not per-tenant records. A tenant that needs its own editing/model/distribution rates today shares the deployment default or passes explicit per-assessment inputs (which are recorded either way).
- Import deduplication keys on `source_reference` within a tenant. Unreferenced listings are not replay-safe by design, and no fuzzy matching against existing campaigns is attempted — a human reconciles near-duplicates.
- The campaigns summary aggregates expected-vs-collected from the latest assessable assessment and paid payout records; it is a reporting view, not an accounting ledger, and it does not replace Phase 9's revenue reporting.
- Expected views remain an operator-recorded input. Nothing in v2.1 forecasts views, and the summary never presents an assessment as a promise of payout.
- No new dependencies were added.

## Stop condition

v2.1 stops here: backend complete and gated, PR open for review, **not merged**. Real campaign data enters only when the owner supplies listings or works the API; connecting any external campaign source, adding the campaign UI, or making cost rates per-tenant each require their own authorised slice.
