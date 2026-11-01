"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { SessionAccess } from "@/components/session-access";
import {
  apiRequest,
  formatBytes,
  readSessionToken,
  storeSessionToken
} from "@/lib/media-api";
import {
  freshPublicationKey,
  humanize,
  OAuthStart,
  PublicApproval,
  Publication,
  PublicationEvent,
  PublicationList,
  PublicationState,
  PublishingCapability,
  PublishingConnection,
  Visibility
} from "@/lib/publishing-api";

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

const activeStates: PublicationState[] = ["queued", "validating", "uploading", "processing", "needs_reconciliation"];
const cancellableStates: PublicationState[] = ["queued", "validating", "uploading", "processing"];
const filters: Array<"" | PublicationState> = ["", "queued", "uploading", "processing", "needs_reconciliation", "failed", "succeeded", "cancelled"];

function can(role: string | undefined, minimum: keyof typeof ROLE_RANK): boolean {
  return (ROLE_RANK[role || ""] || 0) >= ROLE_RANK[minimum];
}

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function localDate(hours: number): string {
  const date = new Date(Date.now() + hours * 3_600_000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function releaseStateClass(state: string): string {
  return `publication-${state}`;
}

export function PublishingWorkbench() {
  const [token, setToken] = useState("");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [capabilities, setCapabilities] = useState<PublishingCapability[]>([]);
  const [connections, setConnections] = useState<PublishingConnection[]>([]);
  const [publications, setPublications] = useState<Publication[]>([]);
  const [total, setTotal] = useState(0);
  const [stateFilter, setStateFilter] = useState<"" | PublicationState>("");
  const [selectedId, setSelectedId] = useState("");
  const [events, setEvents] = useState<PublicationEvent[]>([]);
  const [oauth, setOauth] = useState<OAuthStart | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [approvalRationale, setApprovalRationale] = useState("");
  const [approvalExpiresAt, setApprovalExpiresAt] = useState(localDate(24));
  const [form, setForm] = useState({
    connectionId: "",
    renderJobId: "",
    qualityEvaluationId: "",
    idempotencyKey: freshPublicationKey(),
    visibility: "private" as Visibility,
    title: "",
    description: "",
    tags: "",
    madeForKids: false,
    categoryId: "22",
    publicApprovalId: ""
  });

  const selected = useMemo(
    () => publications.find((item) => item.id === selectedId) || null,
    [publications, selectedId]
  );
  const youtube = useMemo(
    () => capabilities.find((item) => item.provider === "youtube") || null,
    [capabilities]
  );
  const connected = useMemo(
    () => connections.filter((item) => item.state === "connected"),
    [connections]
  );

  async function loadCapabilities(sessionToken: string) {
    const response = await apiRequest<PublishingCapability[]>(
      "/api/v1/publishing/capabilities",
      sessionToken
    );
    setCapabilities(response);
  }

  async function loadConnections(sessionToken: string) {
    const response = await apiRequest<PublishingConnection[]>(
      "/api/v1/publishing/connections",
      sessionToken
    );
    setConnections(response);
    setForm((current) => ({
      ...current,
      connectionId: current.connectionId || response.find((item) => item.state === "connected")?.id || ""
    }));
  }

  async function loadPublications(sessionToken: string, preferred = "") {
    const query = new URLSearchParams({ limit: "100" });
    if (stateFilter) query.set("state", stateFilter);
    const response = await apiRequest<PublicationList>(
      `/api/v1/publishing/publications?${query.toString()}`,
      sessionToken
    );
    setPublications(response.items);
    setTotal(response.total);
    setSelectedId((current) => {
      const target = preferred || current;
      if (target && response.items.some((item) => item.id === target)) return target;
      return response.items[0]?.id || preferred;
    });
  }

  async function loadEvents(sessionToken: string, publicationId: string) {
    if (!publicationId) {
      setEvents([]);
      return;
    }
    const response = await apiRequest<PublicationEvent[]>(
      `/api/v1/publishing/publications/${publicationId}/events`,
      sessionToken
    );
    setEvents(response);
  }

  async function refreshAll(sessionToken: string, preferred = "") {
    setLoading(true);
    try {
      await Promise.all([
        loadCapabilities(sessionToken),
        loadConnections(sessionToken),
        loadPublications(sessionToken, preferred)
      ]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const existing = readSessionToken();
    if (!existing) return;
    let cancelled = false;
    void apiRequest<Principal>("/api/v1/auth/me", existing)
      .then((current) => {
        if (cancelled) return;
        setToken(existing);
        setPrincipal(current);
      })
      .catch(() => {
        storeSessionToken("");
        setToken("");
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!token || !principal) return;
    setError("");
    void refreshAll(token).catch((caught) => {
      setError(messageOf(caught, "Unable to load publishing controls"));
    });
    // Queries are tenant-scoped by the authenticated server principal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, principal?.tenant_id, stateFilter]);

  useEffect(() => {
    if (!token || !selectedId) {
      setEvents([]);
      return;
    }
    void loadEvents(token, selectedId).catch((caught) => {
      setError(messageOf(caught, "Unable to load immutable publication events"));
    });
  }, [token, selectedId]);

  useEffect(() => {
    if (!token || !publications.some((item) => activeStates.includes(item.state))) return;
    const timer = window.setInterval(() => {
      void loadPublications(token, selectedId).then(() => {
        if (selectedId) return loadEvents(token, selectedId);
        return undefined;
      }).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [token, selectedId, publications]);

  function handleToken(next: string) {
    setToken(next);
    if (!next) {
      setPrincipal(null);
      setCapabilities([]);
      setConnections([]);
      setPublications([]);
      setSelectedId("");
      setEvents([]);
    }
  }

  function releaseBody(publicApprovalId: string | null = form.publicApprovalId.trim() || null) {
    return {
      connection_id: form.connectionId,
      render_job_id: form.renderJobId.trim(),
      quality_evaluation_id: form.qualityEvaluationId.trim(),
      idempotency_key: form.idempotencyKey.trim(),
      visibility: form.visibility,
      title: form.title.trim(),
      description: form.description.trim(),
      tags: form.tags.split(",").map((item) => item.trim()).filter(Boolean),
      made_for_kids: form.madeForKids,
      category_id: form.categoryId.trim(),
      public_approval_id: publicApprovalId
    };
  }

  async function startOAuth() {
    if (!token) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiRequest<OAuthStart>(
        "/api/v1/publishing/oauth/youtube/start",
        token,
        { method: "POST" }
      );
      setOauth(response);
      setNotice("One-time PKCE authorization prepared. Verify the exact redirect and scope, then continue to Google.");
    } catch (caught) {
      setError(messageOf(caught, "Unable to start YouTube authorization"));
    } finally {
      setBusy(false);
    }
  }

  async function revokeConnection(connectionId: string) {
    if (!token || !window.confirm("Revoke this provider grant and disable its stored connection?")) return;
    setBusy(true);
    setError("");
    try {
      await apiRequest(
        `/api/v1/publishing/connections/${connectionId}/revoke`,
        token,
        { method: "POST" }
      );
      setNotice("Provider revocation requested and the local connection was disabled.");
      await loadConnections(token);
    } catch (caught) {
      setError(messageOf(caught, "Unable to revoke this connection"));
    } finally {
      setBusy(false);
    }
  }

  async function requestPublicApproval() {
    if (!token) return;
    if (form.visibility !== "public") {
      setError("Select public visibility before requesting an exact public-release approval.");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiRequest<PublicApproval>(
        "/api/v1/publishing/public-approvals",
        token,
        {
          method: "POST",
          body: JSON.stringify({
            release: releaseBody(null),
            rationale: approvalRationale.trim(),
            expires_at: approvalExpiresAt ? new Date(approvalExpiresAt).toISOString() : null
          })
        }
      );
      setForm((current) => ({ ...current, publicApprovalId: response.approval.id }));
      setNotice("Exact public-release approval requested. An independent reviewer must approve it before queueing; any metadata change invalidates the binding.");
    } catch (caught) {
      setError(messageOf(caught, "Unable to request public approval"));
    } finally {
      setBusy(false);
    }
  }

  async function queuePublication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiRequest<Publication>(
        "/api/v1/publishing/publications",
        token,
        { method: "POST", body: JSON.stringify(releaseBody()) }
      );
      setNotice("Publication accepted only after a fresh server-side quality and rights gate. The upload worker will re-check immediately before external transfer.");
      setForm((current) => ({
        ...current,
        idempotencyKey: freshPublicationKey(),
        publicApprovalId: ""
      }));
      await loadPublications(token, response.id);
      setSelectedId(response.id);
    } catch (caught) {
      setError(messageOf(caught, "Publication queue acceptance failed"));
    } finally {
      setBusy(false);
    }
  }

  async function safeAction(action: "cancel" | "retry") {
    if (!token || !selected) return;
    const warning = action === "retry"
      ? "Safe retry will reconcile an uncertain remote result before any repeat upload. Continue?"
      : "Cancel stops work when safe. A provider upload already in flight may require reconciliation. Continue?";
    if (!window.confirm(warning)) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiRequest<Publication>(
        `/api/v1/publishing/publications/${selected.id}/${action}`,
        token,
        { method: "POST" }
      );
      setNotice(action === "retry" ? "Safe retry queued with reconciliation protection." : "Cancellation request recorded.");
      await loadPublications(token, response.id);
      await loadEvents(token, response.id);
    } catch (caught) {
      setError(messageOf(caught, `Unable to ${action} this publication`));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="publishing-page">
      <section className="publishing-hero">
        <div>
          <p className="eyebrow">PLATFORM JOB CONTROL / PHASE 06</p>
          <h1>Private first.<br/><em>Reconcile before retry.</em></h1>
          <p>One real YouTube adapter path, one-time OAuth state, encrypted grants, two mandatory release gates, resumable progress, and immutable provider events. Public release requires an exact independent approval.</p>
        </div>
        <div className="pilot-rule">
          <Icon name="lock" className="h-6 w-6"/>
          <div><strong>Pilot visibility: private</strong><span>Mocked contract evidence is not a live-provider verification. No public post is implied.</span></div>
        </div>
      </section>

      <SessionAccess token={token} onToken={handleToken} principal={principal} onPrincipal={setPrincipal}/>
      {error && <div className="media-notice notice-error" role="alert">{error}</div>}
      {notice && <div className="media-notice notice-ok" role="status">{notice}</div>}

      {!token || !principal ? (
        <div className="media-locked publishing-locked"><Icon name="lock" className="h-7 w-7"/><div><h2>Publishing evidence is tenant private</h2><p>Authenticate to inspect destination grants, submit an approved private pilot, or reconcile a provider result.</p></div></div>
      ) : (
        <>
          <section className="capability-board">
            <header><div><p className="card-kicker">HONEST CAPABILITY MATRIX</p><h2>Official paths, exact limits.</h2></div><span>{loading ? "REFRESHING" : `${capabilities.length} PROVIDERS`}</span></header>
            <div className="capability-grid">
              {capabilities.map((item) => <article key={item.provider} className={item.provider === "youtube" ? "capability-primary" : ""}>
                <div className="capability-title"><span className={`provider-mark provider-${item.provider}`}>{item.display_name.slice(0, 1)}</span><div><strong>{item.display_name}</strong><small>{item.provider}</small></div><em className={`mode-${item.mode}`}>{humanize(item.mode)}</em></div>
                <dl><div><dt>Configured</dt><dd>{item.configured ? "yes" : "no"}</dd></div><div><dt>Real OAuth verified</dt><dd>{item.real_oauth_verified ? "yes" : "no"}</dd></div><div><dt>Real upload verified</dt><dd>{item.real_upload_verified ? "yes" : "no"}</dd></div><div><dt>Resumable</dt><dd>{item.features.resumable_upload ? "supported" : "unavailable"}</dd></div></dl>
                <p>{item.evidence_note}</p>
                <details><summary>Scopes & limitations</summary><strong>Required scopes</strong>{item.required_scopes.length ? item.required_scopes.map((scope) => <code key={scope}>{scope}</code>) : <small>None while disabled.</small>}<strong>Boundaries</strong>{item.limitations.map((limit) => <small key={limit}>{limit}</small>)}</details>
              </article>)}
            </div>
          </section>

          <section className="publishing-control-grid">
            <article className="connection-card">
              <header><div><p className="card-kicker">DESTINATION GRANTS</p><h2>Connection vault</h2></div><button type="button" onClick={() => void loadConnections(token)} disabled={busy}>Refresh</button></header>
              <div className="oauth-boundary"><Icon name="lock" className="h-5 w-5"/><p><strong>Tokens stay server-side</strong><span>The UI receives status, scope, expiry, and remediation only—never access or refresh tokens.</span></p></div>
              {!connections.length && <div className="connection-empty"><strong>No destinations connected</strong><p>YouTube is the only Phase 6 adapter that can be configured. Meta, TikTok, and LinkedIn remain disabled.</p></div>}
              <div className="connection-list">{connections.map((item) => <div key={item.id}>
                <span className={`connection-light connection-${item.state}`}/><p><strong>{item.destination_display_name}</strong><small>{humanize(item.provider)} · expires {new Date(item.access_token_expires_at).toLocaleString()}</small></p><em>{humanize(item.state)}</em>{item.state !== "revoked" && can(principal.role, "admin") && <button type="button" onClick={() => void revokeConnection(item.id)} disabled={busy}>Revoke</button>}
                {item.last_error_message && <small className="connection-error">{item.last_error_message}</small>}
              </div>)}</div>
              <button className="primary-button connect-action" type="button" onClick={() => void startOAuth()} disabled={busy || !can(principal.role, "admin") || !youtube?.features.oauth}>Prepare YouTube OAuth</button>
              {!can(principal.role, "admin") && <small>Admin role or higher is required to authorize a destination.</small>}
              {oauth && <div className="oauth-ready"><strong>Verify before leaving ClipOS</strong><dl><div><dt>Redirect</dt><dd><code>{oauth.redirect_uri}</code></dd></div><div><dt>Scope</dt><dd>{oauth.required_scopes.map((scope) => <code key={scope}>{scope}</code>)}</dd></div><div><dt>State</dt><dd>one-time · expires {new Date(oauth.expires_at).toLocaleTimeString()}</dd></div><div><dt>PKCE</dt><dd>{oauth.pkce_enabled ? "S256 enabled" : "missing"}</dd></div></dl><a className="primary-button" href={oauth.authorization_url}>Continue to Google <Icon name="arrow" className="h-4 w-4"/></a><p>Google returns to the exact API callback. Return here and refresh connections after consent.</p></div>}
            </article>

            <form className="release-card" onSubmit={queuePublication}>
              <header><div><p className="card-kicker">GATED RELEASE</p><h2>Queue one upload</h2></div><span className={`visibility-chip visibility-${form.visibility}`}>{form.visibility}</span></header>
              <div className="release-rule"><Icon name="check" className="h-5 w-5"/><p>Queue acceptance and pre-upload execution both re-read tenant authorization, exact render bytes, quality approval, rights scope, destination grant, visibility, and budgets.</p></div>
              <div className="release-fields">
                <label className="field-wide">Connection<select required value={form.connectionId} onChange={(event) => setForm({...form, connectionId: event.target.value})}><option value="">Select a connected destination</option>{connected.map((item) => <option value={item.id} key={item.id}>{item.destination_display_name} · {humanize(item.provider)}</option>)}</select></label>
                <label>Render job ID<input required value={form.renderJobId} onChange={(event) => setForm({...form, renderJobId: event.target.value})}/></label>
                <label>Quality evaluation ID<input required value={form.qualityEvaluationId} onChange={(event) => setForm({...form, qualityEvaluationId: event.target.value})}/></label>
                <label>Visibility<select value={form.visibility} onChange={(event) => setForm({...form, visibility: event.target.value as Visibility, publicApprovalId: event.target.value === "public" ? form.publicApprovalId : ""})}><option value="private">Private · pilot default</option><option value="unlisted">Unlisted</option><option value="public">Public · exact approval required</option></select></label>
                <label>Category ID<input required pattern="[0-9]{1,3}" value={form.categoryId} onChange={(event) => setForm({...form, categoryId: event.target.value})}/></label>
                <label className="field-wide">Title<input required maxLength={100} value={form.title} onChange={(event) => setForm({...form, title: event.target.value})}/></label>
                <label className="field-wide">Description<textarea maxLength={5000} value={form.description} onChange={(event) => setForm({...form, description: event.target.value})}/></label>
                <label className="field-wide">Tags, comma separated<input value={form.tags} onChange={(event) => setForm({...form, tags: event.target.value})}/></label>
                <label className="field-wide">Idempotency key<input required minLength={8} value={form.idempotencyKey} onChange={(event) => setForm({...form, idempotencyKey: event.target.value})}/></label>
                <label className="audience-check"><input type="checkbox" checked={form.madeForKids} onChange={(event) => setForm({...form, madeForKids: event.target.checked})}/><span>Self-declare made for kids</span></label>
              </div>

              {form.visibility === "public" && <div className="public-approval-box"><div><Icon name="lock" className="h-5 w-5"/><p><strong>Public is a separately approved action</strong><span>Approval binds this exact metadata, render, destination, audience flag, and idempotency key. Editing any field requires a new request.</span></p></div><label>Independent-review rationale<textarea required minLength={20} value={approvalRationale} onChange={(event) => setApprovalRationale(event.target.value)}/></label><label>Approval expires<input type="datetime-local" value={approvalExpiresAt} onChange={(event) => setApprovalExpiresAt(event.target.value)}/></label><button type="button" onClick={() => void requestPublicApproval()} disabled={busy || !can(principal.role, "editor")}>Request exact public approval</button><label>Approval ID<input value={form.publicApprovalId} onChange={(event) => setForm({...form, publicApprovalId: event.target.value})} placeholder="Approve in the independent review queue first"/></label><Link href="/approvals">Open independent approvals <Icon name="arrow" className="h-3 w-3"/></Link></div>}

              <button className="primary-button queue-release" type="submit" disabled={busy || !can(principal.role, "reviewer") || !form.connectionId || (form.visibility === "public" && !form.publicApprovalId.trim())}>Run gates &amp; queue upload</button>
              {!can(principal.role, "reviewer") && <small>Reviewer role or higher is required to accept an external publication job.</small>}
            </form>
          </section>

          <section className="publication-workspace">
            <aside className="publication-queue">
              <header><div><p className="card-kicker">PROVIDER JOBS</p><h2>{total.toString().padStart(2, "0")}</h2></div><button type="button" onClick={() => void loadPublications(token)}>{loading ? "Loading…" : "Refresh"}</button></header>
              <div className="publication-filters">{filters.map((value) => <button type="button" key={value || "all"} className={stateFilter === value ? "active" : ""} onClick={() => setStateFilter(value)}>{value ? humanize(value) : "all"}</button>)}</div>
              <div className="publication-list">{publications.map((item) => <button type="button" key={item.id} className={selectedId === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}><div><span className={`publication-state ${releaseStateClass(item.state)}`}>{humanize(item.state)}</span><em>{item.visibility}</em></div><strong>{item.title}</strong><small>{humanize(item.provider)} · {new Date(item.created_at).toLocaleString()}</small><div className="mini-progress"><span style={{width: `${item.progress_percent}%`}}/></div><code>{item.id.slice(0, 12)}…</code></button>)}{!publications.length && <p className="queue-empty">No provider jobs match this filter.</p>}</div>
            </aside>

            <div className="publication-inspector">
              {!selected ? <div className="inspector-empty"><Icon name="upload" className="h-8 w-8"/><h2>Select a provider job</h2><p>Progress, safe actions, sanitized provider evidence, and immutable state transitions appear here.</p></div> : <>
                <header className="publication-head"><div><p className="card-kicker">{humanize(selected.provider)} / {selected.visibility}</p><h2>{selected.title}</h2><p><code>{selected.id}</code></p></div><span className={`publication-state ${releaseStateClass(selected.state)}`}>{humanize(selected.state)}</span></header>
                <div className="publication-progress"><span style={{width: `${selected.progress_percent}%`}}/><p><strong>{selected.progress_percent}%</strong><em>{humanize(selected.stage)}</em><small>{formatBytes(selected.committed_bytes)} / {formatBytes(selected.content_length)}</small></p></div>
                {selected.uncertain_remote_result && <div className="reconcile-warning"><Icon name="pulse" className="h-5 w-5"/><div><strong>Remote outcome is uncertain</strong><p>Do not create another release. Safe retry runs provider reconciliation first and uploads again only if absence can be established.</p></div></div>}
                {selected.failure_message && <div className="failure-panel"><strong>{humanize(selected.failure_category || "provider failure")}</strong><p>{selected.failure_message}</p>{selected.manual_next_step && <small>Manual next step: {selected.manual_next_step}</small>}{selected.retry_after_seconds && <small>Provider retry window: {selected.retry_after_seconds} seconds</small>}</div>}
                <div className="publication-actions"><button type="button" onClick={() => void safeAction("retry")} disabled={busy || !selected.retryable && selected.state !== "needs_reconciliation"}>Safe retry / reconcile</button><button type="button" onClick={() => void safeAction("cancel")} disabled={busy || !cancellableStates.includes(selected.state)}>Cancel safely</button>{selected.provider_url && <a href={selected.provider_url} target="_blank" rel="noreferrer">Open provider record <Icon name="arrow" className="h-3 w-3"/></a>}</div>
                <dl className="publication-facts"><div><dt>Destination</dt><dd>{selected.provider_destination_id}</dd></div><div><dt>Provider ID</dt><dd>{selected.provider_publication_id || "Not assigned"}</dd></div><div><dt>Processing</dt><dd>{selected.provider_processing_status || "Not reported"}</dd></div><div><dt>Attempts</dt><dd>{selected.attempt_count} / {selected.max_attempts}</dd></div><div><dt>Acceptance gate</dt><dd><code>{selected.acceptance_gate_verification_id}</code></dd></div><div><dt>Pre-upload gate</dt><dd>{selected.upload_gate_verification_id ? <code>{selected.upload_gate_verification_id}</code> : "Pending"}</dd></div><div><dt>Quality evaluation</dt><dd><code>{selected.quality_evaluation_id}</code></dd></div><div><dt>Public approval</dt><dd>{selected.public_approval_id ? <code>{selected.public_approval_id}</code> : "Not required"}</dd></div></dl>
                <section className="event-timeline"><div className="panel-title"><div><p className="card-kicker">IMMUTABLE EVENTS</p><h3>Provider job history</h3></div><span>{events.length} records</span></div><ol>{events.map((item) => <li key={item.id}><span>{String(item.sequence).padStart(2, "0")}</span><div><strong>{humanize(item.kind)}</strong><p>{item.message}</p><small>{humanize(item.from_state || "created")} → {humanize(item.to_state)} · {item.progress_percent}% · {new Date(item.created_at).toLocaleString()}</small></div></li>)}</ol>{!events.length && <p className="event-empty">No events recorded for this job.</p>}</section>
              </>}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
