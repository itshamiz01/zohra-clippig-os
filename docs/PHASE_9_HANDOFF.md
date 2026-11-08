# Zohra ClipOS — Phase 9 Handoff

Date: 2026-11-08
Version: 0.9.0
Alembic head: `e37c610043af`
Status: Phase 9 implemented in the working tree for gate review.

## Gate decision

**ACCEPT PHASE 9 as the sales-assistant, CRM, onboarding and revenue-operations backend, with the explicit limitations below.**

This is not a production, outreach, revenue or client-acquisition certificate. In particular, **nothing has been sent to anyone and no money has moved**: no email, calendar or payment provider has been chosen by the user, both gateways are disabled (real provider names are configuration errors), drafts exist only inside ClipOS, and every revenue record is an operator-recorded fact rather than a provider transaction. Phase 9 also inherits Phase 8's boundary: the prospects in the CRM came from operator-supplied or feed-supplied evidence whose truth Phase 9 does not re-verify.

## Provider decisions the user still owes

Phase 9 deliberately leaves three decisions open; the system is built so each can be implemented later as a reviewed adapter behind the existing gateway contracts, without changing the CRM:

1. **Email provider** — who sends outreach/follow-up mail (for example a transactional email API or Google Workspace). Until chosen, `CLIPOS_COMMS_PROVIDER` accepts only `disabled`/`fake`, and naming a real provider fails configuration with an error that names this missing decision.
2. **Calendar provider** — who issues discovery-call invites (for example Google Calendar). The comms gateway contract is where invites would live; no calendar surface exists in this phase.
3. **Payment provider** — who collects money (for example Stripe), to be integrated in test mode first when chosen. Until then `CLIPOS_PAYMENTS_PROVIDER` behaves exactly like the comms setting, and revenue `external_reference` values are operator-recorded strings only.

## What was delivered

### CRM core (`clipos/services/crm.py`, routes under `/api/v1/crm`)

- **Intake**: `POST /crm/intake/{handoff_id}` consumes a `ready` `opportunity-handoff.v1` package (draft handoffs are refused with 409) and creates a prospect `CrmAccount` plus its primary `CrmContact`. Unique constraints on `source_handoff_id` and `(tenant_id, source_opportunity_id)` make intake idempotent — repeats return the existing account with `created: false` and write nothing. Contacts created by intake dedup on the Phase 8 identity (normalised handle, else canonical URL, else name), reusing an existing contact when the identity matches. Intake is synchronous database work over stored records with no external call, so Phase 9 deliberately adds **no background job kind** and no worker change.
- **Lifecycle**: stages `prospect → qualified_lead → conversation → discovery_call → proposal → contract_review → client → campaign → deliverable → invoiced → retained` with `rejected`/`lost` as terminal side-states. Forward moves are free; backwards moves and side-state moves require a recorded reason (422 `stage_reason_required` without one); terminal stages cannot be left (409). Every accepted change appends a `CrmPipelineEvent` (`created`/`intake`/`stage_transition`/`merge`) — the table is append-only and is the account's history of record.
- **Dedup and merges**: contacts and accounts follow the Phase 8 conservatism. Exact `dedup_key` matches merge on create and refresh supplied fields; fuzzy text matches (similarity ≥ 0.86 over the 500 most recent tenant records) are only flagged via `possible_duplicate_of_id`. A reviewer-only merge endpoint folds a flagged duplicate into the survivor: child rows (drafts, tasks, revenue, onboarding, pipeline history, contact references) are re-pointed, the merge is recorded as a pipeline event plus an audit event, and a contact's suppression is sticky — if either side is suppressed, the survivor stays suppressed.
- **Suppression / opt-out**: contacts carry `suppressed` + `suppression_reason` (reviewer-managed). Creating any draft for a suppressed contact is refused with 409 `contact_suppressed`, and the refusal is audited with outcome `denied` before the error is raised.

### Sales asset drafts (draft-only, evidence-cited)

- Five kinds: `outreach_message`, `follow_up`, `proposal_outline`, `discovery_agenda`, `onboarding_checklist`. Bodies are rendered by deterministic templates (`sales-draft-v1`) from cited stored evidence only: the source opportunity with its six score components, the intake handoff, the account's five most recent revenue records, its latest pipeline event, and — only when the account came from an opportunity and the tenant has reported Phase 7 views snapshots — an analytics summary explicitly labelled `scope: tenant_publications` so tenant numbers can never be presented as the account's own.
- **Evidence gate**: a draft with zero citations cannot be created (422 `insufficient_evidence`); outreach drafts additionally require an opportunity or handoff citation and proposal outlines an opportunity, handoff or revenue citation, so a manually created account with no stored evidence cannot receive outreach copy. User-authored bodies are allowed but pass the same evidence gate and are recorded with `created_by` set to the author's user id instead of `system`.
- **Review**: `draft → in_review → approved/rejected`, with archive reachable from any non-archived state; approve/reject require the reviewer role (the service enforces it even though the route admits editors) and stamp reviewer, time and note. Approval changes only the draft row: there is no send path, no `sent` state exists in `SalesAssetState`, and a test asserts the CRM route table and the `CrmService` surface contain no send/deliver/transmit/charge surface.

### Gateways (`clipos/gateways/comms/`, `clipos/gateways/payments/`)

- Both mirror the publishing/analytics pattern: contracts, errors with sanitized categories, a registry/factory, truthful provider states, provider-status rows and configuration-status entries.
- `DisabledCommunicationsAdapter` / `DisabledPaymentsAdapter` are the defaults: every delivery/processing attempt raises `comms_unsupported` / `payments_unsupported` with a manual next step naming the missing provider decision. Settings validation rejects any real provider value for `CLIPOS_COMMS_PROVIDER` / `CLIPOS_PAYMENTS_PROVIDER` with an error that spells out the decisions still owed (email, calendar, payment).
- `FakeCommsAdapter` / `FakePaymentsAdapter` are **mocked**: they record requests in process memory and return receipts with `delivered=False` / `processed=False`. Settings forbid selecting them in staging/production, and the CRM service never calls either gateway — drafts are stored, never delivered; revenue records are stored, never charged.

### Tasks, revenue and onboarding

- Tasks are per-account with validated tenant-member assignees (422 `not_a_tenant_member` otherwise) and guarded status transitions (cancelled is terminal).
- Revenue records (`revenue_records`) keep kinds strictly distinct. The summary counts commitment kinds (`quote`, `contract`, `invoice`, `recurring`) only in `issued`/`paid`/`overdue` states and money kinds (`payment`, `refund`) only when `paid`, grouped per currency, and returns the bucket definitions in the response — an issued quote can never inflate `collected`.
- Onboarding is one record per account (`UNIQUE (account_id)`), creatable only at client stage or later, with the versioned `onboarding-checklist-v1` checklist and a milestones list; completion is refused until every checklist item is done.

## Gate evidence

- Backend gate: **198 passed in 162.76s** (184 pre-Phase-9 baseline retained green + 14 new Phase 9 cases) with **73% branch-aware coverage** (70% enforced minimum; coverage run: 198 passed in 222.60s), including the retained genuine deterministic FFmpeg proof. Ruff check and format check pass on `apps/api/clipos`; mypy reports no issues in 129 source files. Baseline note: `ruff check apps/api` still shows the 7 pre-existing Phase 6 test-file errors that predate this phase; no new lint debt was added. Environment note: one intermediate coverage run failed 5 Phase 4 rendering tests when `/tmp` (512MB tmpfs) reached 97% full from accumulated test temp dirs; with space freed, the Phase 4 file passed in isolation (14 passed) and the final coverage run passed 198/198 clean — the failure was environmental, exactly as recorded for Phase 8.
- Migration: clean SQLite base→head through all nine revisions; the Phase 9 revision re-run is a no-op at head; `downgrade -1` returns to `0b728736cdd4` and re-upgrade restores head; `alembic check` reports no drift. Offline PostgreSQL DDL for the Phase 9 step: 10,000-byte upgrade (7 tables, 7 new native enum types, check/unique/index constraints) and a 1,166-byte downgrade that drops the tables and the seven new types.
- Focused Phase 9 evidence (14 test functions): intake creates a prospect account + handle-deduped contact and is idempotent end-to-end (repeat intake creates nothing); draft handoffs refused at intake; stage lifecycle validation (backwards/side-state without reason → 422, terminal lock → 409) with the append-only event sequence asserted; contact exact-merge on create with field refresh, fuzzy flag without merge, reviewer-only merge recorded as a pipeline event; account fuzzy flag + reviewed merge re-pointing tasks; suppression refusing draft creation (409) with a denied audit row, and drafts working after unsuppression; evidence gate (intake outreach cites opportunity + handoff, manual-account outreach refused 422, follow-up cites pipeline history, zero-citation account refused at service level); draft review workflow (direct approval 409, `sent` state 422, editor approval 403, reviewer approval audited) with the communications adapter still disabled afterwards; revenue summary separation (issued quote/draft quote/void quote, issued payment and paid refund kept out of or in the right buckets per currency); tenant isolation across accounts, contacts, drafts and intake; the full onboarding flow (stage gate, duplicate refusal, unknown item 422, premature completion 409, milestones, completion, onboarding checklist draft); task assignee membership validation and status guards; gateway truthfulness (disabled adapters raise with stable categories, fakes record in memory with undelivered/unprocessed receipts, real provider names and staging fake selection refused by settings validation with the owed decisions named); and the no-send surface assertion over CRM routes and the service class.

## Limitations and unverified boundaries

- **No provider decisions, no integrations.** Email, calendar and payments are exactly as described above: contracts and disabled/fake adapters only. Any real sending or charging requires the user's provider choices plus a separately reviewed adapter, credentials and — for payments — a test-mode integration first.
- CRM content is operator- and Phase 8-supplied evidence. Intake does not verify that a channel exists, that an audience band is accurate, or that a labelled contact route is real; draft templates assert only cited facts, but the citations are only as true as their sources.
- The analytics citation in drafts is tenant-scoped by construction (Phase 7 metrics have no account link); it is labelled as such in the citation and must not be quoted to a prospect as their own numbers.
- Revenue records are bookkeeping, not accounting: no tax, invoicing-document, payment-reconciliation or multi-entity semantics exist, and `external_reference` values are unverified operator strings.
- Draft template copy (`sales-draft-v1`) is a deterministic starting point for human editing, never reviewed against a real prospect; the review gate exists precisely because generated copy is not approved copy until a reviewer approves it — and even then it can only be used outside ClipOS by a human, since no send path exists.
- Dedup thresholds (0.86 similarity, 500-record scan) are inherited editorial constants, uncalibrated against a real CRM corpus.
- The web console gained only static home-page status updates for Phase 9; there is no `/crm` UI in this phase.
- PostgreSQL, Redis and Docker Compose remain **unverified** live, exactly as in earlier phases; all Phase 9 evidence is SQLite + in-memory queue.

## Stop condition

Phase 10 remains locked. A later phase may add autonomous orchestration only under its own authorized design; Phase 9 grants no authority to contact any prospect, send any draft, promise terms, charge any payment, or take any external or commercial action. Outreach and payments additionally remain blocked on the user's three provider decisions (email, calendar, payment), which no later phase may make on the user's behalf.
