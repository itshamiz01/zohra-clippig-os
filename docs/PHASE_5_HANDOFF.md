# Phase 5 handoff — Quality Council, Rights Guardian and mandatory safety gates

Date: 2026-10-31  
Version: `0.5.0`  
Alembic head: `43b5bc652f2e`  
Status: **implemented and locally validated; Phase 6 remains locked**

## Gate decision

The authorized Phase 5 scope is feature-complete in this source tree. It reuses the existing tenant, asset, transcript, candidate, render-plan, render-job and private-output contracts. It does not create a parallel media pipeline and does not publish, upload, contact a platform or create an external-action job.

This handoff is evidence of a local engineering gate, not a production-readiness certificate, legal opinion, legal clearance, platform-acceptance guarantee or authorization to continue into Phase 6.

## What was delivered

### Eight-check Quality Council

Every evaluation persists exactly one structured result for each of these check kinds:

1. technical integrity;
2. transcript/audio consistency;
3. caption quality;
4. context integrity;
5. visual composition;
6. audio quality;
7. duplication/campaign consistency;
8. destination-specific constraints.

Each result stores status, severity, structured evidence, confidence, evidence source, validator/check version, remediation and hard-block state. The aggregation policy uses only `approved`, `needs_human_review`, `rejected` and `blocked` outcomes.

Unavailable semantic evidence and confidence below the configured threshold become review requirements. Deterministic failures remain authoritative: model observations cannot replace missing deterministic checks, downgrade their severity, clear a hard block or authorize an action.

### Structured Rights Guardian

Tenant-scoped rights evidence records:

- source owner and the authenticated human recorder;
- permission-recording timestamp, effective date and expiration or an explicit no-expiration assertion;
- evidence type/reference and optional SHA-256 document reference;
- exact asset, candidate or render scope;
- permission basis, state and revocation evidence;
- allowed platforms and territories;
- commercial, editing and AI-processing permission;
- attribution requirements and text;
- license terms, restrictions and campaign reference;
- separate states for music, footage, logos, likeness/voice, personal data and sensitive information.

`publicly_available` is not an accepted permission basis. Evidence for one asset/candidate/render, destination, territory or campaign is not inferred as permission for another. Unknown, expired, revoked, rejected, future-effective, incomplete, wrong-platform, wrong-territory and missing-attribution evidence fails closed.

The application stores operational references and checksums rather than raw contracts, identity documents or unnecessary personal records. Qualified legal/privacy review is still mandatory where ownership, consent, territory, trademark, publicity, music, employment, personal-data or sensitive-information questions are ambiguous.

### Independent human decision

Only authorized reviewer roles can decide an evaluation. Approval additionally requires:

- a reviewer different from the evaluation requester;
- a substantive reason;
- explicit acknowledgement of every uncertain or low-confidence check;
- no non-overridable rights, authorization, security, provenance or technical-integrity block.

The decision and audit records preserve actor, role, time, reason, requested action, effective action and acknowledgement evidence. An AI/model actor cannot approve its own risky action.

### Signed evidence and fresh mandatory gates

Approved evaluations receive canonical, versioned HMAC-SHA-256 server-side evidence bound to the exact tenant, evaluation, render, output, destination, territory, campaign, rights evidence, protected-input hash, policy version and expiration.

`QualityGateService.verify` is the reusable Phase 6 boundary. A future publishing service must call it:

1. when accepting a publish job; and
2. immediately before external upload.

Every call re-reads database state, rights state, approval evidence, defects, provenance and private-object size/checksum. It rejects expired, altered, revoked, stale, bypassed or cross-scope evidence and writes a bounded verification record. An earlier signature or checkpoint is never sufficient by itself.

Phase 5 exposes both checkpoint values to validate this contract, but creates no publisher, scheduler, OAuth flow, upload, outreach or external action. Generic job creation rejects publish/upload/outreach/contract/payment-like kinds.

### Operator workbench

The responsive `/quality` route provides:

- rights-evidence recording and scoped state;
- evaluation creation for an existing successful render;
- eight-check status, severity, confidence, source and remediation;
- deterministic/model distinction;
- policy explanation, missing evidence and legal-clearance warning;
- independent review controls and acknowledgement requirements;
- approval/staleness/signature state;
- both preflight checkpoint controls and audit history;
- a persistent Phase 6 lock notice.

Private media remains authenticated; bearer tokens are not placed in URLs.

## Validation evidence

### Backend regression and coverage

Final non-coverage regression:

```text
134 passed in 48.65s
```

Final branch-aware coverage gate:

```text
134 passed in 56.84s
TOTAL: 4,334 statements, 790 missed; 1,556 branches, 98 partial; 75.37%
required minimum: 70.00%
```

The full regression retains the genuine deterministic Phase 4 FFmpeg render proof. Phase 5’s focused fixtures reuse those validated render contracts for speed; they are not presented as a second genuine encode.

Focused Phase 5 suite:

```text
19 passed in 12.79s
```

The 19 collected cases cover:

- all eight structured check records and human-review aggregation;
- signed approval and fresh success at both mandatory checkpoints;
- unknown, expired and revoked permission;
- unsupported platform scope and incomplete third-party material;
- public availability and cross-render scope denial;
- missing attribution at schema and gate layers;
- corrupted private output as non-overridable technical failure;
- fabricated transcript lineage despite high model quality;
- low confidence and explicit acknowledgement;
- unauthorized override and requester self-approval denial;
- post-approval revocation/stale evidence;
- direct generic-job bypass and unapproved preflight denial;
- tenant isolation for rights, evaluations and verifications;
- contradictory model evidence unable to overrule deterministic failure;
- a model actor unable to authorize a risky action;
- an unknown destination profile becoming review, not a silent pass.

### Static backend gates

```text
Ruff format check: passed
Ruff lint: passed
mypy --strict: passed for 67 source files
```

OpenAPI generation exposes eight Phase 5 path templates covering rights list/create/detail/revoke, evaluation create/list/detail, decision and verification operations.

### Database and migration gates

Fresh SQLite validation:

```text
base -> head: 43b5bc652f2e
alembic check: No new upgrade operations detected.
Phase 5 tables after upgrade: 5/5
foreign_key_check: clean
downgrade to 8f0da12e33a4: all 5 Phase 5 tables removed
reapply head: all 5 restored
foreign_key_check after reapply: clean
```

The five tables are `rights_evidence`, `quality_evaluations`, `quality_check_results`, `quality_review_decisions` and `gate_verifications`.

Offline PostgreSQL SQL contract:

```text
full base -> Phase 5 head upgrade: 46,862 bytes
Phase 5-only upgrade: 10,013 bytes
Phase 5-only downgrade: 990 bytes
asserted: 5 CREATE TABLE / 5 DROP TABLE
asserted: JSONB emitted
asserted: 11 native enum creates / 11 matching enum drops
asserted: Phase 5 revision marker
```

This is static dialect evidence only. No live PostgreSQL server was available, so native migration execution, locking, concurrency, rollback under load and backup/restore remain unverified.

### Frontend gates

```text
Oxlint 1.90.0: 0 warnings / 0 errors across 27 files and 96 rules
TypeScript strict no-emit: passed
Next.js 16.4.3 optimized production build: passed
routes generated: 11, including /quality
```

### Runtime HTTP smoke

A real FastAPI process and production-style Next.js standalone process were started locally with temporary SQLite/private-storage state, then stopped and cleaned up.

Observed:

- HTTP 200 for `/`, `/quality`, `/renders`, `/approvals` and `/settings`;
- `/quality` server output included `QUALITY COUNCIL / RIGHTS GUARDIAN / PHASE 05`;
- proxied `/healthz` and `/readyz` returned healthy/ready;
- API root returned version `0.5.0`, phase `5`, status `mandatory_quality_rights_gates`;
- authenticated login succeeded through the same-origin web proxy;
- authenticated quality-evaluation and rights-evidence list APIs returned tenant-scoped empty results;
- unauthenticated quality access returned 401;
- a `publish.external_upload` generic job attempt returned 422 `external_action_unavailable`;
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and a deny-by-default API content-security policy were observed.

This is HTTP/runtime smoke, not full browser interaction, populated-workflow E2E, accessibility or visual-regression certification.

### Dependency, configuration and static security gates

```text
npm audit --audit-level=high: 0 vulnerabilities
pip-audit: no known vulnerabilities in auditable dependencies
local unpublished zohra-clipos 0.5.0: skipped because it is not a PyPI project
private-key/common live-token scan: 163 text files, 0 hits
production shell-execution/embedded-bearer scan: 77 text files, 0 hits
Docker Compose YAML parse: 5 expected services
worker bounds: 3g memory, 2 CPUs, 256 PIDs
```

Production media subprocesses use argument-vector execution; no `shell=True`, `create_subprocess_shell` or `os.system` path was found. Protected-environment configuration validators and their retained regression tests reject ephemeral/short secrets, SQLite, automatic schema creation, wildcard CORS, the in-memory queue, optional Redis readiness and embedded workers.

Docker itself was unavailable. No `docker compose config`, image build, image scan or container runtime result is claimed.

## Requirement traceability

| Authorized Phase 5 requirement | Evidence | Status |
|---|---|---:|
| Reuse existing asset/transcript/candidate/render/tenant contracts | Foreign keys, loaded-context service and focused integration fixtures | Complete |
| Eight separate structured checks | Enum, table, service builders, detail API/UI and exact-set tests | Complete locally |
| Status/severity/evidence/confidence/version/remediation | Strict schemas and persisted result fields | Complete |
| Missing/low-confidence evidence becomes review | Aggregator policy, UI explanation and negative tests | Complete |
| Deterministic failures cannot be overruled by models | Source-aware aggregation and contradictory-model test | Complete |
| Scoped rights with owner, recorder, time, terms, restrictions and third-party material | Rights schema/table/service/API/UI | Complete locally |
| Public availability and one-clip blanket inference forbidden | Schema denial plus public/cross-render focused case | Complete |
| Approved/review/rejected/blocked outcomes | Versioned server aggregation policy | Complete |
| Critical rights/auth/security/technical failures non-overridable | Hard-block policy and override negatives | Complete |
| Role-restricted, reasoned and audited human overrides | Reviewer RBAC, requester separation, decisions and audit rows | Complete locally |
| AI cannot approve its own risky action | Human-principal/actor policy and model-actor test | Complete |
| Versioned signed approval evidence | Canonical HMAC evidence, expiry and comparison tests | Complete locally |
| Reject stale/bypassed gates | Fresh state/object revalidation, signature checks and negatives | Complete locally |
| Reusable gate at job acceptance and before upload | Two checkpoint enum values and `verify` service | Complete contract; Phase 6 integration intentionally absent |
| Review panel with minimized personal data | `/quality` detail/workbench and operational references only | Complete at build/HTTP-smoke level |
| Required negative scenarios and tenant isolation | 19 focused cases plus retained full suite | Complete locally |
| No publishing or outreach | No executor/OAuth/uploader; generic job bypass denied | Complete |
| Limitations/manual/legal review documented | README, security/development/integration docs and this handoff | Complete |

## Honest limitations and remaining production gates

1. **No live PostgreSQL.** SQLite and offline SQL do not prove native enums/JSONB, locking, isolation, concurrent idempotency or rollback behavior.
2. **No live Redis or separate worker fleet.** Queue outage, duplicate/loss, multi-worker races, failover and recovery remain unverified.
3. **No Docker runtime.** Compose and Dockerfiles are configuration-reviewed only; exact image capabilities and sandboxing are unverified.
4. **No external provider proof.** Transcription/model adapters remain disabled, mocked or unverified exactly as disclosed. No model-assisted Phase 5 checker was enabled.
5. **No public platform integration.** Destination profiles are internal versioned constraints, not current platform acceptance guarantees. No OAuth, upload or publish call exists.
6. **No legal-clearance automation.** A passing rights/gate result cannot resolve ambiguous legal or privacy facts.
7. **No approved document vault.** Evidence references/checksums are not a contract-management, identity-verification, retention or legal-hold system.
8. **Local HMAC key only.** Production needs key rotation, multi-instance secret management and preferably HSM/KMS-backed signing policy.
9. **Local storage is not production object storage.** Encryption/KMS, retention/deletion, legal hold, versioning, backup/restore and multi-host consistency are absent.
10. **No full audiovisual ML quality model.** Semantic checks intentionally require humans where deterministic evidence is insufficient.
11. **No broad media/adversarial corpus.** Corruption and lineage negatives are bounded; fuzzing, malware isolation and diverse language/device QA remain required.
12. **No full browser E2E/accessibility/visual QA.** The dashboard passed lint, type, build and HTTP smoke only.
13. **No production claim.** Load, observability, disaster recovery, incident response, penetration testing and independent security/legal review are still required.

## Operator safety checklist

- record permission only from evidence a qualified human has actually reviewed;
- scope evidence to the exact asset/candidate/render, platform, territory, campaign and time window;
- never use public availability as republishing permission;
- mark revocation or expiry immediately and do not reuse prior approval;
- inspect every check’s source, confidence, evidence and remediation;
- require a reviewer independent from the requester and preserve a substantive reason;
- treat model observations as supplemental only;
- do not interpret `approved` as legal clearance or platform acceptance;
- keep media private and never place bearer tokens in URLs;
- in Phase 6, call the same server-side gate at job acceptance and immediately before upload;
- do not implement, enable or simulate publishing, outreach, contracts or payments without separate authorization.

## Delivery files

- source tree: `/home/user/zohra-clipos`
- clean archive: `/home/user/Zohra_ClipOS_Phase_5.zip`
- archive checksum sidecar: `/home/user/Zohra_ClipOS_Phase_5.zip.sha256`
- internal source manifest: `docs/PHASE_5_SOURCE_HASHES.sha256`

The source manifest intentionally excludes itself. The archive intentionally excludes VCS metadata, secrets, runtime databases/media/output, virtual environments, dependencies, build output, caches, coverage files and prior archives.

## Authorization boundary

Phase 5 implementation stops here. Phase 6 and all public publishing, scheduling, outreach, contracts, payments and autonomous commercial action remain locked. Continue only after human review of this handoff and separate explicit authorization.
