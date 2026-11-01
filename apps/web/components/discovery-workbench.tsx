"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { SessionAccess } from "@/components/session-access";
import {
  Candidate,
  CandidateContext,
  CandidateDetail,
  CandidateList,
  CandidateState,
  DiscoveryResult,
  DiscoveryRun,
  DiscoveryRunList,
  DiscoveryStrategy,
  formatPercent,
  formatScore,
  stateLabel
} from "@/lib/discovery-api";
import {
  APIError,
  apiRequest,
  formatTime,
  readSessionToken,
  storeSessionToken
} from "@/lib/media-api";

interface Principal {
  email: string;
  role: string;
  tenant_id: string;
  is_platform_admin?: boolean;
}

interface Filters {
  asset: string;
  state: "" | CandidateState;
  language: string;
  topic: string;
  minScore: string;
}

interface RunForm {
  asset: string;
  strategy: DiscoveryStrategy;
  profile: "general" | "tiktok" | "instagram_reels" | "youtube_shorts" | "linkedin";
  minimum: number;
  target: number;
  maximum: number;
  padding: number;
  maxCandidates: number;
  audience: string;
  topics: string;
}

const ROLE_RANK: Record<string, number> = {
  viewer: 10,
  editor: 20,
  reviewer: 30,
  admin: 40,
  owner: 50
};

const INITIAL_FILTERS: Filters = {
  asset: "",
  state: "",
  language: "",
  topic: "",
  minScore: ""
};

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function candidateQuery(filters: Filters): string {
  const query = new URLSearchParams({ limit: "100" });
  if (filters.asset.trim()) query.set("asset_id", filters.asset.trim());
  if (filters.state) query.set("state", filters.state);
  if (filters.language.trim()) query.set("language", filters.language.trim());
  if (filters.topic.trim()) query.set("topic", filters.topic.trim());
  if (filters.minScore.trim()) query.set("min_score", filters.minScore.trim());
  return query.toString();
}

function runQuery(asset: string): string {
  const query = new URLSearchParams({ limit: "8" });
  if (asset.trim()) query.set("asset_id", asset.trim());
  return query.toString();
}

function canEdit(role: string | undefined): boolean {
  return (ROLE_RANK[role || ""] || 0) >= ROLE_RANK.editor;
}

function canReview(role: string | undefined): boolean {
  return (ROLE_RANK[role || ""] || 0) >= ROLE_RANK.reviewer;
}

export function DiscoveryWorkbench({ initialAssetId = "" }: { initialAssetId?: string }) {
  const [token, setToken] = useState("");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [filters, setFilters] = useState<Filters>({ ...INITIAL_FILTERS, asset: initialAssetId });
  const [runForm, setRunForm] = useState<RunForm>({
    asset: initialAssetId,
    strategy: "deterministic",
    profile: "general",
    minimum: 15,
    target: 35,
    maximum: 60,
    padding: 1,
    maxCandidates: 12,
    audience: "",
    topics: ""
  });
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [context, setContext] = useState<CandidateContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewAction, setReviewAction] = useState<"approve" | "reject">("approve");
  const [reviewReason, setReviewReason] = useState("");
  const [boundaryStart, setBoundaryStart] = useState("0");
  const [boundaryEnd, setBoundaryEnd] = useState("0");
  const [boundaryReason, setBoundaryReason] = useState("");

  async function loadQueue(sessionToken: string, nextFilters: Filters = filters) {
    if (!sessionToken) return;
    setLoading(true);
    setError("");
    try {
      const [candidateResponse, runResponse] = await Promise.all([
        apiRequest<CandidateList>(`/api/v1/candidates?${candidateQuery(nextFilters)}`, sessionToken),
        apiRequest<DiscoveryRunList>(
          `/api/v1/discovery/runs?${runQuery(nextFilters.asset)}`,
          sessionToken
        )
      ]);
      setCandidates(candidateResponse.items);
      setCandidateTotal(candidateResponse.total);
      setRuns(runResponse.items);
      setSelectedId((current) => {
        if (current && candidateResponse.items.some((item) => item.id === current)) return current;
        return candidateResponse.items[0]?.id || "";
      });
      if (candidateResponse.items.length === 0) {
        setDetail(null);
        setContext(null);
      }
    } catch (caught) {
      if (caught instanceof APIError && caught.status === 401) {
        storeSessionToken("");
        setToken("");
        setPrincipal(null);
        setError("Your API session expired.");
      } else {
        setError(errorMessage(caught, "Unable to load the candidate queue"));
      }
    } finally {
      setLoading(false);
    }
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
          void loadQueue(existing, { ...INITIAL_FILTERS, asset: initialAssetId });
        })
        .catch(() => {
          if (cancelled) return;
          storeSessionToken("");
          setToken("");
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // Initial authentication must run once for the server-supplied asset scope.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialAssetId]);

  useEffect(() => {
    if (!token || !selectedId) return;
    let cancelled = false;
    setDetailLoading(true);
    setError("");
    void Promise.all([
      apiRequest<CandidateDetail>(`/api/v1/candidates/${selectedId}`, token),
      apiRequest<CandidateContext>(`/api/v1/candidates/${selectedId}/context`, token)
    ])
      .then(([nextDetail, nextContext]) => {
        if (cancelled) return;
        setDetail(nextDetail);
        setContext(nextContext);
        setBoundaryStart((nextDetail.candidate.start_ms / 1000).toFixed(3).replace(/\.?0+$/, ""));
        setBoundaryEnd((nextDetail.candidate.end_ms / 1000).toFixed(3).replace(/\.?0+$/, ""));
        setBoundaryReason("");
        setReviewReason("");
      })
      .catch((caught) => {
        if (!cancelled) setError(errorMessage(caught, "Unable to load candidate evidence"));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, token]);

  const selectedCandidate = detail?.candidate || null;
  const reviewable =
    selectedCandidate && ["proposed", "needs_review"].includes(selectedCandidate.state);
  const contextSpan = context
    ? Math.max(1, context.context_end_ms - context.context_start_ms)
    : 1;
  const boundaryStyle = context && selectedCandidate
    ? {
        left: `${Math.max(
          0,
          ((selectedCandidate.start_ms - context.context_start_ms) / contextSpan) * 100
        )}%`,
        width: `${Math.max(
          1,
          ((selectedCandidate.end_ms - selectedCandidate.start_ms) / contextSpan) * 100
        )}%`
      }
    : { left: "0%", width: "0%" };

  const sortedScores = useMemo(
    () => [...(detail?.scores || [])].sort((a, b) => b.weighted_score - a.weighted_score),
    [detail]
  );

  function handleToken(next: string) {
    setToken(next);
    if (!next) {
      setPrincipal(null);
      setCandidates([]);
      setRuns([]);
      setSelectedId("");
      setDetail(null);
      setContext(null);
    }
  }

  async function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await loadQueue(token, filters);
  }

  async function runDiscovery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setBusy(true);
    setError("");
    setNotice("");
    const topics = runForm.topics
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 12);
    try {
      const result = await apiRequest<DiscoveryResult>(
        `/api/v1/assets/${runForm.asset.trim()}/discover`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            strategy: runForm.strategy,
            target_profile: runForm.profile,
            min_duration_seconds: runForm.minimum,
            target_duration_seconds: runForm.target,
            max_duration_seconds: runForm.maximum,
            padding_seconds: runForm.padding,
            max_candidates: runForm.maxCandidates,
            audience_hint: runForm.audience.trim() || undefined,
            topic_hints: topics
          })
        }
      );
      const scopedFilters = { ...filters, asset: runForm.asset.trim() };
      setFilters(scopedFilters);
      setNotice(
        result.idempotent
          ? `Existing run ${result.run.id.slice(0, 8)} replayed without duplicate candidates.`
          : `Discovery completed with ${result.candidates.length} evidence-backed candidates.`
      );
      await loadQueue(token, scopedFilters);
      if (result.candidates[0]) setSelectedId(result.candidates[0].id);
    } catch (caught) {
      const message = errorMessage(caught, "Candidate discovery failed");
      const failedFilters = { ...filters, asset: runForm.asset.trim() };
      setFilters(failedFilters);
      await loadQueue(token, failedFilters);
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  async function submitReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !selectedCandidate) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await apiRequest<Candidate>(
        `/api/v1/candidates/${selectedCandidate.id}/review`,
        token,
        {
          method: "POST",
          body: JSON.stringify({ action: reviewAction, reason: reviewReason.trim() })
        }
      );
      setNotice(
        reviewAction === "approve"
          ? "Candidate approved. It may now enter an immutable render plan, but it is not published or externally released."
          : "Candidate rejected with an auditable reason."
      );
      setSelectedId(updated.id);
      await loadQueue(token, filters);
      const refreshed = await apiRequest<CandidateDetail>(
        `/api/v1/candidates/${updated.id}`,
        token
      );
      setDetail(refreshed);
      setReviewReason("");
    } catch (caught) {
      setError(errorMessage(caught, "Unable to record this review"));
    } finally {
      setBusy(false);
    }
  }

  async function submitBoundaryEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !selectedCandidate) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const replacement = await apiRequest<Candidate>(
        `/api/v1/candidates/${selectedCandidate.id}/boundaries`,
        token,
        {
          method: "PATCH",
          body: JSON.stringify({
            start_ms: Math.round(Number(boundaryStart) * 1000),
            end_ms: Math.round(Number(boundaryEnd) * 1000),
            reason: boundaryReason.trim()
          })
        }
      );
      setNotice(
        `Boundary revision ${replacement.boundary_revision} created. The previous candidate is preserved as superseded.`
      );
      setSelectedId(replacement.id);
      await loadQueue(token, filters);
    } catch (caught) {
      setError(errorMessage(caught, "Unable to create a boundary revision"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <header className="discovery-hero">
        <div>
          <p className="eyebrow">VIRAL INTELLIGENCE / HUMAN-GOVERNED DISCOVERY</p>
          <h1>Candidate workbench</h1>
          <p>
            Find transcript-supported moments, inspect every factor, then approve, reject, or
            revise boundaries. Scores prioritize review; they never guarantee reach, views, or
            virality.
          </p>
        </div>
        <div className="discovery-scope">
          <Icon name="scissors" className="h-6 w-6" />
          <div><strong>Review authority</strong><span>Only approved moments can enter rendering · no publishing</span></div>
        </div>
      </header>

      <div className="media-toolbar">
        <SessionAccess
          token={token}
          onToken={handleToken}
          principal={principal}
          onPrincipal={(next) => {
            setPrincipal(next);
            const sessionToken = token || readSessionToken();
            if (next && sessionToken) void loadQueue(sessionToken, filters);
          }}
        />
      </div>

      {!token || !principal ? (
        <section className="media-locked">
          <div className="gate-lock"><Icon name="lock" className="h-6 w-6" /></div>
          <div><h2>Tenant authentication required</h2><p>Candidate evidence and reviews remain private until a tenant-scoped API session is established.</p></div>
        </section>
      ) : (
        <>
          {(error || notice) && (
            <div className={`media-notice ${error ? "notice-error" : "notice-ok"}`} role={error ? "alert" : "status"}>
              {error || notice}
            </div>
          )}

          <section className="discovery-control-grid">
            <form className="discovery-run-card" onSubmit={runDiscovery}>
              <div className="discovery-card-head">
                <div><p className="card-kicker">NEW ANALYSIS</p><h2>Generate candidates</h2></div>
                <span className="bounded-chip">BOUNDED + IDEMPOTENT</span>
              </div>
              <div className="discovery-fields">
                <label className="field-wide">Ready asset UUID<input required value={runForm.asset} onChange={(event) => setRunForm({ ...runForm, asset: event.target.value })} placeholder="00000000-0000-0000-0000-000000000000" /></label>
                <label>Strategy<select value={runForm.strategy} onChange={(event) => setRunForm({ ...runForm, strategy: event.target.value as DiscoveryStrategy })}><option value="deterministic">Deterministic</option><option value="hybrid">Hybrid with safe fallback</option><option value="model_required">Model required</option></select></label>
                <label>Editorial profile<select value={runForm.profile} onChange={(event) => setRunForm({ ...runForm, profile: event.target.value as RunForm["profile"] })}><option value="general">General short-form</option><option value="tiktok">TikTok</option><option value="instagram_reels">Instagram Reels</option><option value="youtube_shorts">YouTube Shorts</option><option value="linkedin">LinkedIn</option></select></label>
                <label>Minimum seconds<input type="number" min="3" max="300" step="1" value={runForm.minimum} onChange={(event) => setRunForm({ ...runForm, minimum: Number(event.target.value) })} /></label>
                <label>Target seconds<input type="number" min="3" max="300" step="1" value={runForm.target} onChange={(event) => setRunForm({ ...runForm, target: Number(event.target.value) })} /></label>
                <label>Maximum seconds<input type="number" min="5" max="600" step="1" value={runForm.maximum} onChange={(event) => setRunForm({ ...runForm, maximum: Number(event.target.value) })} /></label>
                <label>Context padding<input type="number" min="0" max="10" step="0.25" value={runForm.padding} onChange={(event) => setRunForm({ ...runForm, padding: Number(event.target.value) })} /></label>
                <label>Candidate limit<input type="number" min="1" max="50" step="1" value={runForm.maxCandidates} onChange={(event) => setRunForm({ ...runForm, maxCandidates: Number(event.target.value) })} /></label>
                <label className="field-wide">Audience hint<input maxLength={240} value={runForm.audience} onChange={(event) => setRunForm({ ...runForm, audience: event.target.value })} placeholder="Optional evidence-matching hint" /></label>
                <label className="field-wide">Topic hints<input value={runForm.topics} onChange={(event) => setRunForm({ ...runForm, topics: event.target.value })} placeholder="Comma-separated, up to 12" /></label>
              </div>
              <p className="model-strategy-note">
                Transcript text is treated as untrusted data. Hybrid mode may use a configured model
                only to select supplied option IDs; authorization and review state remain server-owned.
              </p>
              <button className="primary-button" type="submit" disabled={busy || !canEdit(principal.role)}>
                {busy ? "Analyzing evidence…" : "Run candidate discovery"}<Icon name="arrow" className="h-4 w-4" />
              </button>
              {!canEdit(principal.role) && <small className="permission-note">Editor role or higher is required to start a run.</small>}
            </form>

            <aside className="run-ledger">
              <div className="discovery-card-head"><div><p className="card-kicker">RUN LEDGER</p><h2>Recent evidence</h2></div><button type="button" onClick={() => void loadQueue(token, filters)}>Refresh</button></div>
              {runs.length === 0 ? <p className="ledger-empty">No run is visible in this asset scope.</p> : <div className="run-list">{runs.map((run) => <article key={run.id}><header><span className={`run-state run-${run.state}`}>{stateLabel(run.state)}</span><code>{run.id.slice(0, 8)}</code></header><strong>{stateLabel(run.strategy)}</strong><p>{run.model_verified ? `${run.provider} / ${run.model}` : run.strategy === "deterministic" ? "Deterministic only" : "Model not verified or fallback used"}</p><footer><span>{run.candidate_count} candidates</span><span>{run.latency_ms === null ? "—" : `${run.latency_ms} ms`}</span><span>${Number(run.estimated_cost).toFixed(4)} est.</span></footer>{run.failure_message && <small>{run.failure_category}: {run.failure_message}</small>}</article>)}</div>}
            </aside>
          </section>

          <section className="candidate-workspace">
            <div className="candidate-queue">
              <div className="queue-heading"><div><p className="card-kicker">REVIEW QUEUE</p><h2>{candidateTotal} candidates</h2></div>{loading && <span>Refreshing…</span>}</div>
              <form className="candidate-filters" onSubmit={applyFilters}>
                <label>Asset UUID<input value={filters.asset} onChange={(event) => setFilters({ ...filters, asset: event.target.value })} placeholder="All tenant assets" /></label>
                <label>Status<select value={filters.state} onChange={(event) => setFilters({ ...filters, state: event.target.value as Filters["state"] })}><option value="">All states</option><option value="proposed">Proposed</option><option value="needs_review">Needs review</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="superseded">Superseded</option></select></label>
                <label>Language<input value={filters.language} onChange={(event) => setFilters({ ...filters, language: event.target.value })} placeholder="en" /></label>
                <label>Exact topic<input value={filters.topic} onChange={(event) => setFilters({ ...filters, topic: event.target.value })} /></label>
                <label>Minimum score<input type="number" min="0" max="100" step="1" value={filters.minScore} onChange={(event) => setFilters({ ...filters, minScore: event.target.value })} /></label>
                <button type="submit">Apply filters</button>
              </form>
              <div className="candidate-list">
                {candidates.map((candidate) => (
                  <button type="button" key={candidate.id} className={candidate.id === selectedId ? "candidate-row candidate-selected" : "candidate-row"} onClick={() => setSelectedId(candidate.id)}>
                    <span className="score-orb">{Math.round(candidate.overall_score)}</span>
                    <span className="candidate-row-main"><span><strong>#{candidate.rank}</strong><em className={`candidate-state candidate-${candidate.state}`}>{stateLabel(candidate.state)}</em></span><q>{candidate.transcript_excerpt}</q><small>{formatTime(candidate.start_ms)}–{formatTime(candidate.end_ms)} · {formatPercent(candidate.recommendation_confidence)} evidence confidence</small></span>
                    <Icon name="arrow" className="h-4 w-4" />
                  </button>
                ))}
                {!loading && candidates.length === 0 && <div className="candidate-empty"><span>00</span><p><strong>No matching candidates</strong><small>Run discovery on a ready, timestamped asset or broaden the current filters.</small></p></div>}
              </div>
            </div>

            <div className="candidate-inspector">
              {!selectedId ? <div className="inspector-empty"><Icon name="transcript" className="h-7 w-7" /><h2>Select a candidate</h2><p>Factor evidence, source context, caveats, revisions, and review controls will appear here.</p></div> : detailLoading || !detail || !context ? <div className="inspector-empty"><span className="loading-mark" /><h2>Loading private evidence…</h2></div> : (
                <>
                  <header className="candidate-detail-head">
                    <div><p className="eyebrow">CANDIDATE / {detail.candidate.id.slice(0, 8).toUpperCase()}</p><h2>{formatScore(detail.candidate.overall_score)}<small>/100 ranking score</small></h2></div>
                    <div><span className={`candidate-state candidate-${detail.candidate.state}`}>{stateLabel(detail.candidate.state)}</span>{detail.candidate.state === "approved" && <Link className="render-handoff" href={`/renders?candidate=${detail.candidate.id}`}>Create validated render <Icon name="render" className="h-3.5 w-3.5" /></Link>}<Link href={`/media/${detail.candidate.asset_id}`}>Open source asset <Icon name="arrow" className="h-3.5 w-3.5" /></Link></div>
                  </header>

                  <div className="honesty-banner"><Icon name="pulse" className="h-5 w-5" /><p><strong>Ranking aid, not a forecast.</strong>{detail.candidate.rationale}</p></div>

                  <section className="candidate-summary-grid">
                    <div><span>Window</span><strong>{formatTime(detail.candidate.start_ms)}–{formatTime(detail.candidate.end_ms)}</strong></div>
                    <div><span>Evidence confidence</span><strong>{formatPercent(detail.candidate.recommendation_confidence)}</strong></div>
                    <div><span>Transcript confidence</span><strong>{formatPercent(detail.candidate.transcript_confidence)}</strong></div>
                    <div><span>Boundary revision</span><strong>v{detail.candidate.boundary_revision}</strong></div>
                  </section>

                  <section className="source-context">
                    <div className="candidate-section-head"><div><p className="card-kicker">SOURCE CONTEXT</p><h3>Transcript around the cut</h3></div><span>{formatTime(context.context_start_ms)}–{formatTime(context.context_end_ms)}</span></div>
                    <div className="boundary-rail" aria-label="Candidate boundary within loaded context"><span style={boundaryStyle} /></div>
                    <div className="context-list">{context.segments.map((segment) => <article key={segment.ordinal} className={segment.selected ? "context-selected" : ""}><time>{formatTime(segment.start_ms)}</time><div><p>{segment.text}</p><small>{segment.speaker_label || "speaker unavailable"} · {formatPercent(segment.confidence)}</small></div></article>)}</div>
                    <blockquote>{detail.candidate.transcript_excerpt}</blockquote>
                  </section>

                  <section className="factor-panel">
                    <div className="candidate-section-head"><div><p className="card-kicker">EXPLAINABLE SCORE</p><h3>{sortedScores.length} weighted factors</h3></div><code>{detail.candidate.score_version}</code></div>
                    <div className="factor-list">{sortedScores.map((factor) => <details key={factor.key}><summary><span><strong>{factor.label}</strong><small>{factor.applicable ? `${Math.round(factor.weight * 100)}% weight` : "excluded from denominator"}</small></span><span className="factor-score">{factor.score === null ? "N/A" : formatScore(factor.score)}</span></summary><div className="factor-body"><p>{factor.meaning}</p><div className="factor-meter"><span style={{ width: `${factor.score || 0}%` }} /></div><ul>{factor.evidence.map((item) => <li key={item}>{item}</li>)}</ul><small><strong>Missing-data rule:</strong> {factor.missing_behavior}</small></div></details>)}</div>
                  </section>

                  <section className="caveat-panel">
                    <div><p className="card-kicker">CAVEATS + PROVENANCE</p><h3>What a reviewer must know</h3></div>
                    <div className="risk-row">{detail.candidate.risk_flags.length ? detail.candidate.risk_flags.map((risk) => <span key={risk}>{stateLabel(risk)}</span>) : <span className="risk-clear">No configured risk flag triggered</span>}</div>
                    <dl><div><dt>Selection</dt><dd>{detail.candidate.model_verified ? `Verified ${detail.candidate.model_provider} / ${detail.candidate.model}` : "Deterministic or safe model fallback"}</dd></div><div><dt>Word alignment</dt><dd>{detail.candidate.word_timestamps_available ? "available" : "segment-level only"}</dd></div><div><dt>Algorithm</dt><dd>{detail.candidate.algorithm_version}</dd></div><div><dt>Parent revision</dt><dd>{detail.candidate.parent_candidate_id || "original candidate"}</dd></div></dl>
                  </section>

                  <section className="review-controls">
                    <div className="candidate-section-head"><div><p className="card-kicker">HUMAN DECISION</p><h3>Review and revise</h3></div><span>{principal.role} authority</span></div>
                    {!canReview(principal.role) ? <p className="permission-box">Reviewer role or higher is required. Your session remains read-only for candidate lifecycle decisions.</p> : !reviewable ? <p className="permission-box">This immutable revision is {stateLabel(detail.candidate.state)} and cannot be reviewed or edited again.</p> : (
                      <div className="review-grid">
                        <form onSubmit={submitReview}>
                          <h4>Editorial decision</h4>
                          <div className="decision-toggle"><button type="button" className={reviewAction === "approve" ? "decision-active" : ""} onClick={() => setReviewAction("approve")}>Approve candidate</button><button type="button" className={reviewAction === "reject" ? "decision-active decision-reject" : ""} onClick={() => setReviewAction("reject")}>Reject candidate</button></div>
                          <label>Reason<textarea required minLength={5} maxLength={2000} value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder="Record evidence and editorial rationale" /></label>
                          <button type="submit" disabled={busy}>{busy ? "Recording…" : "Record decision"}</button>
                          <small>Approval changes candidate state only. It does not render, publish, or authorize an external action.</small>
                        </form>
                        <form onSubmit={submitBoundaryEdit}>
                          <h4>Create boundary revision</h4>
                          <div className="boundary-fields"><label>Start seconds<input required type="number" min="0" max={context.duration_ms / 1000} step="0.001" value={boundaryStart} onChange={(event) => setBoundaryStart(event.target.value)} /></label><label>End seconds<input required type="number" min="0.001" max={context.duration_ms / 1000} step="0.001" value={boundaryEnd} onChange={(event) => setBoundaryEnd(event.target.value)} /></label></div>
                          <label>Reason<textarea required minLength={5} maxLength={2000} value={boundaryReason} onChange={(event) => setBoundaryReason(event.target.value)} placeholder="Why these timestamps improve the candidate" /></label>
                          <button type="submit" disabled={busy}>{busy ? "Validating…" : "Create immutable revision"}</button>
                          <small>Server validation requires source-bounded timestamps, transcript overlap, configured duration limits, and word alignment when available.</small>
                        </form>
                      </div>
                    )}
                  </section>

                  <section className="review-history">
                    <div className="candidate-section-head"><div><p className="card-kicker">AUDIT TRAIL</p><h3>Review history</h3></div><span>{detail.reviews.length} records</span></div>
                    {detail.reviews.length === 0 ? <p>No human decision has been recorded for this revision.</p> : <ol>{detail.reviews.map((review) => <li key={review.id}><span>{review.action.replaceAll("_", " ")}</span><div><strong>{stateLabel(review.from_state)} → {stateLabel(review.to_state)}</strong><p>{review.reason}</p><small>{new Date(review.created_at).toLocaleString()} · reviewer {review.reviewer_user_id.slice(0, 8)}</small></div></li>)}</ol>}
                  </section>
                </>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
