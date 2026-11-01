"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { SessionAccess } from "@/components/session-access";
import {
  apiRequest,
  readSessionToken,
  storeSessionToken
} from "@/lib/media-api";
import {
  DESTINATIONS,
  Destination,
  GateOutcome,
  QualityEvaluationDetail,
  QualityEvaluationList,
  QualityEvaluationSummary,
  RightsEvidence,
  humanize
} from "@/lib/quality-api";

interface Principal {
  user_id?: string;
  email: string;
  role: string;
  tenant_id: string;
  is_platform_admin?: boolean;
}

const ROLE_RANK: Record<string, number> = {
  viewer: 10,
  editor: 20,
  reviewer: 30,
  admin: 40,
  owner: 50
};

const clearanceFields = [
  ["music", "Music"],
  ["footage", "Third-party footage"],
  ["logos", "Logos / marks"],
  ["likeness_or_voice", "Likeness or voice"],
  ["personal_data", "Personal data"],
  ["sensitive_information", "Sensitive information"]
] as const;

function can(role: string | undefined, minimum: keyof typeof ROLE_RANK): boolean {
  return (ROLE_RANK[role || ""] || 0) >= ROLE_RANK[minimum];
}

function freshKey(prefix: string): string {
  const value = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${value}`;
}

function localDate(days: number): string {
  const date = new Date(Date.now() + days * 86_400_000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function outcomeLabel(outcome: string): string {
  return humanize(outcome);
}

function evidenceLines(value: Record<string, unknown>): string[] {
  return Object.entries(value).map(([key, item]) => {
    const rendered = typeof item === "object" && item !== null
      ? JSON.stringify(item)
      : String(item);
    return `${humanize(key)}: ${rendered}`;
  });
}

export function QualityWorkbench() {
  const [token, setToken] = useState("");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [items, setItems] = useState<QualityEvaluationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [outcomeFilter, setOutcomeFilter] = useState<"" | GateOutcome>("");
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<QualityEvaluationDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewReason, setReviewReason] = useState("");
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const [evaluationForm, setEvaluationForm] = useState({
    renderId: "",
    rightsId: "",
    destination: "youtube_shorts" as Destination,
    territory: "IN",
    campaign: "",
    idempotencyKey: freshKey("quality")
  });
  const [rightsForm, setRightsForm] = useState({
    assetId: "",
    renderId: "",
    sourceOwner: "",
    evidenceReference: "",
    basis: "owned",
    platform: "youtube_shorts" as Destination,
    territory: "IN",
    campaign: "",
    expiresAt: localDate(365),
    attributionRequired: false,
    attributionText: "",
    licenseTerms: "Commercial edited short-form use is authorized.",
    restrictions: "Only the recorded render, platform, territory, duration, and campaign.",
    commercialUse: true,
    editing: true,
    aiProcessing: true,
    material: Object.fromEntries(
      clearanceFields.map(([key]) => [
        key,
        key === "likeness_or_voice" ? "cleared" : "not_present"
      ])
    ) as Record<string, string>
  });

  async function loadQueue(sessionToken: string, preferred = "") {
    if (!sessionToken || !can(principal?.role, "reviewer")) return;
    setLoading(true);
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (outcomeFilter) query.set("outcome", outcomeFilter);
      const response = await apiRequest<QualityEvaluationList>(
        `/api/v1/quality-evaluations?${query.toString()}`,
        sessionToken
      );
      setItems(response.items);
      setTotal(response.total);
      setSelectedId((current) => {
        const candidate = preferred || current;
        if (candidate && response.items.some((item) => item.id === candidate)) return candidate;
        return response.items[0]?.id || preferred;
      });
      if (!response.items.length && !preferred) setDetail(null);
    } catch (caught) {
      setError(messageOf(caught, "Unable to load the quality queue"));
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(sessionToken: string, evaluationId: string) {
    if (!sessionToken || !evaluationId) return;
    const response = await apiRequest<QualityEvaluationDetail>(
      `/api/v1/quality-evaluations/${evaluationId}`,
      sessionToken
    );
    setDetail(response);
    setAcknowledged((current) => current.filter((id) => response.checks.some((item) => item.id === id)));
  }

  useEffect(() => {
    const existing = readSessionToken();
    if (!existing) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setToken(existing);
      void apiRequest<Principal>("/api/v1/auth/me", existing)
        .then((current) => {
          if (cancelled) return;
          setPrincipal(current);
        })
        .catch(() => {
          storeSessionToken("");
          setToken("");
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!token || !principal || !can(principal.role, "reviewer")) return;
    void loadQueue(token);
    // Queue queries are deliberately limited to authorized review roles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, principal?.role, outcomeFilter]);

  useEffect(() => {
    if (!token || !selectedId || !can(principal?.role, "reviewer")) return;
    setError("");
    void loadDetail(token, selectedId).catch((caught) => {
      setError(messageOf(caught, "Unable to load gate evidence"));
    });
    // Selected evidence is immutable except for auditable review, revocation, and verification events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, token, principal?.role]);

  const uncertainCheckIds = useMemo(
    () => detail?.checks
      .filter((item) => item.status === "needs_review" || item.status === "unavailable" || item.confidence < 0.7)
      .map((item) => item.id) || [],
    [detail]
  );
  const allUncertainAcknowledged = uncertainCheckIds.every((id) => acknowledged.includes(id));
  const approveLocked = !detail ||
    detail.evaluation.outcome !== "needs_human_review" ||
    detail.stale ||
    detail.hard_block_reasons.length > 0 ||
    detail.checks.some((item) => item.status === "failed" || item.hard_block) ||
    !allUncertainAcknowledged;

  function handleToken(next: string) {
    setToken(next);
    if (!next) {
      setPrincipal(null);
      setItems([]);
      setSelectedId("");
      setDetail(null);
    }
  }

  async function createEvaluation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await apiRequest<QualityEvaluationSummary>(
        `/api/v1/renders/${evaluationForm.renderId.trim()}/quality-evaluations`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            idempotency_key: evaluationForm.idempotencyKey,
            rights_evidence_id: evaluationForm.rightsId.trim() || null,
            destination: evaluationForm.destination,
            territory: evaluationForm.territory.trim().toUpperCase(),
            campaign_reference: evaluationForm.campaign.trim() || null
          })
        }
      );
      setNotice(
        created.outcome === "blocked"
          ? "Evaluation recorded and blocked. Review missing rights or hard-failure remediation."
          : "Eight independent checks were recorded. No publication action was created."
      );
      setEvaluationForm((current) => ({
        ...current,
        idempotencyKey: freshKey("quality")
      }));
      if (can(principal?.role, "reviewer")) {
        await loadQueue(token, created.id);
        setSelectedId(created.id);
      }
    } catch (caught) {
      setError(messageOf(caught, "Unable to create the quality evaluation"));
    } finally {
      setBusy(false);
    }
  }

  async function createRights(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const now = new Date();
      const created = await apiRequest<RightsEvidence>("/api/v1/rights-evidence", token, {
        method: "POST",
        body: JSON.stringify({
          asset_id: rightsForm.assetId.trim(),
          scope: "render",
          render_job_id: rightsForm.renderId.trim(),
          basis: rightsForm.basis,
          status: rightsForm.basis === "unknown" ? "needs_review" : "valid",
          source_owner: rightsForm.sourceOwner.trim(),
          evidence_type: rightsForm.basis === "owned" ? "ownership_record" : "license",
          evidence_reference: rightsForm.evidenceReference.trim(),
          evidence_checksum_sha256: null,
          permission_recorded_at: now.toISOString(),
          effective_from: now.toISOString(),
          expires_at: new Date(rightsForm.expiresAt).toISOString(),
          no_expiration_asserted: false,
          allowed_platforms: [rightsForm.platform],
          allowed_territories: [rightsForm.territory.trim().toUpperCase()],
          commercial_use_allowed: rightsForm.commercialUse,
          editing_allowed: rightsForm.editing,
          ai_processing_allowed: rightsForm.aiProcessing,
          attribution_required: rightsForm.attributionRequired,
          attribution_text: rightsForm.attributionRequired
            ? rightsForm.attributionText.trim()
            : null,
          license_terms: rightsForm.licenseTerms.trim(),
          restrictions: rightsForm.restrictions.trim(),
          third_party_material: rightsForm.material,
          campaign_reference: rightsForm.campaign.trim() || null
        })
      });
      setNotice("Scoped rights evidence recorded with recorder, timestamp, duration, and audit trail.");
      setEvaluationForm((current) => ({
        ...current,
        renderId: created.render_job_id || current.renderId,
        rightsId: created.id,
        destination: created.allowed_platforms[0] as Destination,
        territory: created.allowed_territories[0] || current.territory,
        campaign: created.campaign_reference || ""
      }));
    } catch (caught) {
      setError(messageOf(caught, "Unable to record rights evidence"));
    } finally {
      setBusy(false);
    }
  }

  async function decide(action: "approve" | "reject" | "request_revision") {
    if (!token || !detail) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await apiRequest<QualityEvaluationDetail>(
        `/api/v1/quality-evaluations/${detail.evaluation.id}/decisions`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            action,
            reason: reviewReason.trim(),
            acknowledged_check_ids: action === "approve" ? acknowledged : []
          })
        }
      );
      setDetail(next);
      setReviewReason("");
      setNotice(
        action === "approve"
          ? "Independent human approval signed. A fresh gate check is still mandatory at each future external checkpoint."
          : "Decision recorded. Remediation and a new evaluation are required before release."
      );
      await loadQueue(token, next.evaluation.id);
    } catch (caught) {
      setError(messageOf(caught, "Unable to record the review decision"));
    } finally {
      setBusy(false);
    }
  }

  async function verify(checkpoint: "publish_job_acceptance" | "external_upload") {
    if (!token || !detail) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiRequest(
        `/api/v1/quality-evaluations/${detail.evaluation.id}/verify`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            checkpoint,
            expected_destination: detail.evaluation.destination,
            expected_territory: detail.evaluation.territory
          })
        }
      );
      setNotice(
        `${humanize(checkpoint)} preflight passed and was signed. This did not create a job or upload anything.`
      );
      await loadDetail(token, detail.evaluation.id);
    } catch (caught) {
      setError(messageOf(caught, "Mandatory gate denied this checkpoint"));
      await loadDetail(token, detail.evaluation.id).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="quality-page">
      <section className="quality-hero">
        <div>
          <p className="eyebrow">QUALITY COUNCIL / RIGHTS GUARDIAN / PHASE 05</p>
          <h1>Proof before permission.</h1>
          <p>Eight separate checks, scoped rights evidence, independent human review, and a fresh signed server gate stand between a private render and any future external action.</p>
        </div>
        <div className="quality-scope">
          <Icon name="lock" className="h-6 w-6" />
          <div><strong>Publishing authority stays separate</strong><span>A Phase 5 preflight is evidence—not an upload or reusable authorization token. Phase 6 must call it fresh.</span></div>
        </div>
      </section>

      <div className="quality-session">
        <SessionAccess
          token={token}
          onToken={handleToken}
          principal={principal}
          onPrincipal={setPrincipal}
        />
      </div>

      {error && <div className="media-notice notice-error" role="alert">{error}</div>}
      {notice && <div className="media-notice notice-ok" role="status">{notice}</div>}

      {!token || !principal ? (
        <div className="media-locked"><Icon name="lock" className="h-7 w-7"/><div><h2>Tenant evidence is private</h2><p>Authenticate to record scoped rights, run the council, or review gate history.</p></div></div>
      ) : (
        <>
          <section className="quality-create-grid">
            <form className="quality-form" onSubmit={createEvaluation}>
              <div className="quality-card-head"><div><p className="card-kicker">RUN THE COUNCIL</p><h2>Evaluate one render</h2></div><span className="source-tag source-deterministic">8 CHECKS</span></div>
              <div className="quality-fields two-up">
                <label>Render ID<input required value={evaluationForm.renderId} onChange={(event) => setEvaluationForm({...evaluationForm, renderId: event.target.value})}/></label>
                <label>Rights evidence ID<input value={evaluationForm.rightsId} onChange={(event) => setEvaluationForm({...evaluationForm, rightsId: event.target.value})} placeholder="Blank deliberately blocks downstream use"/></label>
                <label>Destination<select value={evaluationForm.destination} onChange={(event) => setEvaluationForm({...evaluationForm, destination: event.target.value as Destination})}>{DESTINATIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                <label>Territory<input required maxLength={32} value={evaluationForm.territory} onChange={(event) => setEvaluationForm({...evaluationForm, territory: event.target.value})}/></label>
                <label>Campaign reference<input value={evaluationForm.campaign} onChange={(event) => setEvaluationForm({...evaluationForm, campaign: event.target.value})} placeholder="Recommended for duplicate checks"/></label>
                <label>Idempotency key<input required value={evaluationForm.idempotencyKey} onChange={(event) => setEvaluationForm({...evaluationForm, idempotencyKey: event.target.value})}/></label>
              </div>
              <button className="primary-button" type="submit" disabled={busy || !can(principal.role, "editor")}>Run deterministic checks</button>
              {!can(principal.role, "editor") && <small>Editor role or higher is required.</small>}
            </form>

            <details className="quality-form rights-recorder">
              <summary><span><p className="card-kicker">RIGHTS GUARDIAN</p><strong>Record scoped permission evidence</strong></span><Icon name="arrow" className="h-4 w-4"/></summary>
              <form onSubmit={createRights}>
                <p className="quality-warning">A public page, an upload-time checkbox, or approval for another clip or platform is not permission. Store references—not raw identity documents.</p>
                <div className="quality-fields two-up">
                  <label>Source asset ID<input required value={rightsForm.assetId} onChange={(event) => setRightsForm({...rightsForm, assetId: event.target.value})}/></label>
                  <label>Exact render ID<input required value={rightsForm.renderId} onChange={(event) => setRightsForm({...rightsForm, renderId: event.target.value})}/></label>
                  <label>Source owner<input required minLength={2} value={rightsForm.sourceOwner} onChange={(event) => setRightsForm({...rightsForm, sourceOwner: event.target.value})}/></label>
                  <label>Evidence reference<input required minLength={3} value={rightsForm.evidenceReference} onChange={(event) => setRightsForm({...rightsForm, evidenceReference: event.target.value})}/></label>
                  <label>Permission basis<select value={rightsForm.basis} onChange={(event) => setRightsForm({...rightsForm, basis: event.target.value})}><option value="owned">Owned</option><option value="licensed">Licensed</option><option value="written_permission">Written permission</option><option value="contract">Contract</option><option value="public_domain">Public-domain assessment</option><option value="unknown">Unknown — review required</option></select></label>
                  <label>Expires at<input required type="datetime-local" value={rightsForm.expiresAt} onChange={(event) => setRightsForm({...rightsForm, expiresAt: event.target.value})}/></label>
                  <label>Allowed platform<select value={rightsForm.platform} onChange={(event) => setRightsForm({...rightsForm, platform: event.target.value as Destination})}>{DESTINATIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                  <label>Allowed territory<input required value={rightsForm.territory} onChange={(event) => setRightsForm({...rightsForm, territory: event.target.value})}/></label>
                  <label>Campaign scope<input value={rightsForm.campaign} onChange={(event) => setRightsForm({...rightsForm, campaign: event.target.value})}/></label>
                  <label>Attribution text<input disabled={!rightsForm.attributionRequired} required={rightsForm.attributionRequired} value={rightsForm.attributionText} onChange={(event) => setRightsForm({...rightsForm, attributionText: event.target.value})}/></label>
                </div>
                <div className="rights-assertions">
                  <label><input type="checkbox" checked={rightsForm.commercialUse} onChange={(event) => setRightsForm({...rightsForm, commercialUse: event.target.checked})}/> Commercial use allowed</label>
                  <label><input type="checkbox" checked={rightsForm.editing} onChange={(event) => setRightsForm({...rightsForm, editing: event.target.checked})}/> Editing allowed</label>
                  <label><input type="checkbox" checked={rightsForm.aiProcessing} onChange={(event) => setRightsForm({...rightsForm, aiProcessing: event.target.checked})}/> AI processing allowed</label>
                  <label><input type="checkbox" checked={rightsForm.attributionRequired} onChange={(event) => setRightsForm({...rightsForm, attributionRequired: event.target.checked})}/> Attribution required</label>
                </div>
                <div className="material-grid">
                  {clearanceFields.map(([key, label]) => <label key={key}>{label}<select value={rightsForm.material[key]} onChange={(event) => setRightsForm({...rightsForm, material: {...rightsForm.material, [key]: event.target.value}})}><option value="not_present">Not present</option><option value="cleared">Cleared with evidence</option><option value="unknown">Unknown</option><option value="restricted">Restricted</option></select></label>)}
                </div>
                <label className="wide-field">License terms<textarea required value={rightsForm.licenseTerms} onChange={(event) => setRightsForm({...rightsForm, licenseTerms: event.target.value})}/></label>
                <label className="wide-field">Restrictions<textarea required value={rightsForm.restrictions} onChange={(event) => setRightsForm({...rightsForm, restrictions: event.target.value})}/></label>
                <button className="primary-button" type="submit" disabled={busy || !can(principal.role, "admin")}>Record evidence</button>
                {!can(principal.role, "admin") && <small>Admin role or higher is required to assert permission evidence.</small>}
              </form>
            </details>
          </section>

          {!can(principal.role, "reviewer") ? (
            <div className="quality-review-locked"><Icon name="lock" className="h-5 w-5"/><div><strong>Independent review panel restricted</strong><p>Your role can request an evaluation, but only reviewer role or higher may inspect sensitive rights references or make a decision.</p></div></div>
          ) : (
            <section className="quality-workspace">
              <aside className="quality-queue">
                <header><div><p className="card-kicker">REVIEW QUEUE</p><h2>{total.toString().padStart(2, "0")}</h2></div><button type="button" onClick={() => void loadQueue(token)}>{loading ? "Loading…" : "Refresh"}</button></header>
                <div className="quality-filters">
                  {["", "needs_human_review", "approved", "rejected", "blocked"].map((value) => <button type="button" key={value || "all"} className={outcomeFilter === value ? "active" : ""} onClick={() => setOutcomeFilter(value as "" | GateOutcome)}>{value ? outcomeLabel(value) : "all"}</button>)}
                </div>
                <div className="quality-list">
                  {items.map((item) => <button type="button" key={item.id} className={selectedId === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span className={`gate-outcome outcome-${item.outcome}`}>{outcomeLabel(item.outcome)}</span><strong>{humanize(item.destination)}</strong><small>{item.territory} · {new Date(item.evaluated_at).toLocaleString()}</small><code>{item.render_job_id.slice(0, 12)}…</code></button>)}
                  {!items.length && <p className="queue-empty">No evaluations match this filter.</p>}
                </div>
              </aside>

              <div className="quality-inspector">
                {!detail ? (
                  <div className="inspector-empty"><Icon name="check" className="h-8 w-8"/><h2>Select an evaluation</h2><p>Every decision stays linked to exact render, rights, policy, and check evidence.</p></div>
                ) : (
                  <>
                    <header className="gate-head">
                      <div><p className="card-kicker">MANDATORY GATE / {detail.evaluation.policy_version}</p><h2>{humanize(detail.evaluation.destination)}</h2><p>{detail.evaluation.territory} · render <code>{detail.evaluation.render_job_id}</code></p></div>
                      <div><span className={`gate-outcome outcome-${detail.evaluation.outcome}`}>{outcomeLabel(detail.evaluation.outcome)}</span>{detail.stale && <span className="stale-chip">STALE</span>}</div>
                    </header>

                    <div className="legal-banner"><Icon name="lock" className="h-5 w-5"/><div><strong>Operational evidence—not legal clearance</strong><p>{detail.legal_note} {detail.privacy_note}</p></div></div>
                    {detail.stale && <div className="hard-block-panel"><strong>Approval evidence is stale</strong><ul>{detail.stale_reasons.map((item) => <li key={item}>{item}</li>)}</ul></div>}
                    {detail.hard_block_reasons.length > 0 && <div className="hard-block-panel"><strong>Non-overridable blocks</strong><ul>{detail.hard_block_reasons.map((item) => <li key={item}>{item}</li>)}</ul></div>}

                    <section className="rights-panel">
                      <div className="panel-title"><div><p className="card-kicker">RIGHTS STATE</p><h3>{humanize(detail.rights_state)}</h3></div><span className={`rights-state rights-${detail.rights_state}`}>{detail.rights ? detail.rights.scope : "no evidence"}</span></div>
                      {detail.rights ? <div className="rights-summary"><dl><div><dt>Owner</dt><dd>{detail.rights.source_owner}</dd></div><div><dt>Basis</dt><dd>{humanize(detail.rights.basis)}</dd></div><div><dt>Evidence reference</dt><dd>{detail.rights.evidence_reference}</dd></div><div><dt>Recorder / timestamp</dt><dd><code>{detail.rights.recorded_by_user_id}</code><br/>{new Date(detail.rights.permission_recorded_at).toLocaleString()}</dd></div><div><dt>Platforms</dt><dd>{detail.rights.allowed_platforms.map(humanize).join(", ")}</dd></div><div><dt>Territory</dt><dd>{detail.rights.allowed_territories.join(", ")}</dd></div><div><dt>Expiration</dt><dd>{detail.rights.expires_at ? new Date(detail.rights.expires_at).toLocaleString() : "Explicitly asserted no expiration"}</dd></div><div><dt>Attribution</dt><dd>{detail.rights.attribution_required ? detail.rights.attribution_text : "Not required"}</dd></div></dl><div className="material-state"><strong>Third-party material</strong>{Object.entries(detail.rights.third_party_material).map(([key, value]) => <span key={key}><em>{humanize(key)}</em><b className={`material-${value}`}>{humanize(value)}</b></span>)}</div></div> : <p className="missing-rights">No structured evidence is linked. Public availability and upload notes are never inferred as permission.</p>}
                      {detail.missing_evidence.length > 0 && <div className="missing-docs"><strong>Missing evidence</strong>{detail.missing_evidence.map((item) => <span key={item}>{item}</span>)}</div>}
                    </section>

                    <section className="council-panel">
                      <div className="panel-title"><div><p className="card-kicker">QUALITY COUNCIL</p><h3>Eight independent checks</h3></div><span>{detail.model_assistance_used ? "MODEL ASSISTED" : "NO MODEL JUDGMENT"}</span></div>
                      <div className="check-grid">
                        {detail.checks.map((check) => {
                          const needsAck = uncertainCheckIds.includes(check.id);
                          return <article key={check.id} className={`check-card check-${check.status} ${check.hard_block ? "check-hard" : ""}`}><header><span className={`check-status status-${check.status}`}>{humanize(check.status)}</span><span className={`source-tag source-${check.evidence_source}`}>{check.evidence_source}</span></header><h4>{humanize(check.kind)}</h4><div className="confidence-line"><span>Confidence</span><strong>{Math.round(check.confidence * 100)}%</strong><i><b style={{width: `${Math.round(check.confidence * 100)}%`}}/></i></div><details><summary>Evidence</summary><ul>{evidenceLines(check.evidence).map((line) => <li key={line}>{line}</li>)}</ul></details><p>{check.remediation}</p>{needsAck && <label className="acknowledge"><input type="checkbox" checked={acknowledged.includes(check.id)} onChange={(event) => setAcknowledged((current) => event.target.checked ? (current.includes(check.id) ? current : [...current, check.id]) : current.filter((id) => id !== check.id))}/> I reviewed this uncertain evidence</label>}</article>;
                        })}
                      </div>
                    </section>

                    <section className="policy-panel"><div><p className="card-kicker">POLICY EXPLANATION</p><h3>Why this outcome</h3></div><ol>{detail.policy_explanation.map((item) => <li key={item}>{item}</li>)}</ol>{detail.remediation.length > 0 && <div className="remediation-list"><strong>Required remediation</strong>{detail.remediation.map((item) => <p key={item}>{item}</p>)}</div>}</section>

                    <section className="review-panel">
                      <div className="panel-title"><div><p className="card-kicker">INDEPENDENT HUMAN AUTHORITY</p><h3>Decision with reason</h3></div><span>{acknowledged.length}/{uncertainCheckIds.length} uncertain checks acknowledged</span></div>
                      <textarea value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} minLength={20} maxLength={4000} placeholder="Explain the evidence reviewed, unresolved limitations, and decision…"/>
                      <div className="review-buttons"><button type="button" disabled={busy || approveLocked || reviewReason.trim().length < 20} onClick={() => void decide("approve")}>Approve gate</button><button type="button" disabled={busy || detail.evaluation.outcome === "approved" || reviewReason.trim().length < 20} onClick={() => void decide("request_revision")}>Request revision</button><button type="button" disabled={busy || detail.evaluation.outcome === "approved" || reviewReason.trim().length < 20} onClick={() => void decide("reject")}>Reject</button></div>
                      {approveLocked && detail.evaluation.outcome === "needs_human_review" && <small>Approval unlocks only after every uncertain check is acknowledged and no deterministic failure, stale input, or hard block remains.</small>}
                    </section>

                    <section className="preflight-panel"><div><p className="card-kicker">FUTURE PHASE CONTRACT</p><h3>Fresh server-side preflight</h3><p>These controls only test the reusable gate service. They cannot create a publishing job or contact a platform.</p></div><div><button type="button" disabled={busy || detail.evaluation.outcome !== "approved" || detail.stale} onClick={() => void verify("publish_job_acceptance")}>Verify job acceptance</button><button type="button" disabled={busy || detail.evaluation.outcome !== "approved" || detail.stale} onClick={() => void verify("external_upload")}>Verify external upload</button></div></section>

                    <section className="audit-panel"><div className="panel-title"><div><p className="card-kicker">AUDIT HISTORY</p><h3>Human decisions & gate attempts</h3></div><span>{detail.decisions.length + detail.verifications.length} events</span></div>{detail.decisions.map((item) => <article key={item.id}><time>{new Date(item.created_at).toLocaleString()}</time><div><strong>{humanize(item.action)} · {item.decision_source}</strong><p>{item.reason}</p><code>{item.reviewer_user_id}</code></div></article>)}{detail.verifications.map((item) => <article key={item.id}><time>{new Date(item.verified_at).toLocaleString()}</time><div><strong>{humanize(item.checkpoint)} · {item.accepted ? "accepted" : "denied"}</strong><p>{item.failure_reasons.length ? item.failure_reasons.join(" · ") : "Fresh rights, provenance, object integrity, expiry, and signature checks passed."}</p><code>{item.evidence_signature}</code></div></article>)}{detail.decisions.length + detail.verifications.length === 0 && <p className="audit-empty">No human decision or downstream preflight has been recorded.</p>}</section>
                  </>
                )}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
