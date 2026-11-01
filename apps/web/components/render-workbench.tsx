"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { SessionAccess } from "@/components/session-access";
import { CandidateDetail } from "@/lib/discovery-api";
import {
  APIError,
  apiRequest,
  formatBytes,
  formatTime,
  readSessionToken,
  storeSessionToken
} from "@/lib/media-api";
import {
  ACTIVE_RENDER_STATES,
  RenderCapabilities,
  RenderCreateResponse,
  RenderDetail,
  RenderListItem,
  RenderListResponse,
  RenderPreset,
  RenderState,
  TenantRenderProfile,
  renderStateLabel
} from "@/lib/render-api";

interface Principal {
  email: string;
  role: string;
  tenant_id: string;
  is_platform_admin?: boolean;
}

interface SystemControl {
  paused: boolean;
  reason: string;
  updated_at: string;
}

interface PlanForm {
  candidate: string;
  idempotencyKey: string;
  preset: RenderPreset;
  width: number;
  height: number;
  frameRate: number;
  cropStrategy: "center_crop" | "fit_pad" | "smart_face_fallback";
  captionMode: "none" | "sidecar" | "burn_in" | "both";
  captionStyle: "clean_bottom" | "high_contrast_bottom";
  normalization: "none" | "speech_loudness";
  includeBranding: boolean;
}

const ROLE_RANK: Record<string, number> = {
  viewer: 10,
  editor: 20,
  reviewer: 30,
  admin: 40,
  owner: 50
};

function newIdempotencyKey(): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `render-${random}`;
}

function can(role: string | undefined, minimum: keyof typeof ROLE_RANK): boolean {
  return (ROLE_RANK[role || ""] || 0) >= ROLE_RANK[minimum];
}

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}

function jsonSummary(value: Record<string, unknown>): string {
  return Object.entries(value)
    .map(([key, item]) => `${key.replaceAll("_", " ")}: ${String(item)}`)
    .join(" · ");
}

async function authenticatedBlob(path: string, token: string): Promise<Blob> {
  const response = await fetch(path, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store"
  });
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message || message;
    } catch {
      // A file endpoint may fail without a JSON body.
    }
    throw new APIError(message, response.status, "file_request_failed");
  }
  return response.blob();
}

function stateQuery(state: "" | RenderState): string {
  const query = new URLSearchParams({ limit: "100" });
  if (state) query.set("state", state);
  return query.toString();
}

export function RenderWorkbench({ initialCandidateId = "" }: { initialCandidateId?: string }) {
  const [token, setToken] = useState("");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [capabilities, setCapabilities] = useState<RenderCapabilities | null>(null);
  const [profile, setProfile] = useState<TenantRenderProfile | null>(null);
  const [profileEnabled, setProfileEnabled] = useState(false);
  const [profileText, setProfileText] = useState("");
  const [control, setControl] = useState<SystemControl | null>(null);
  const [controlReason, setControlReason] = useState("");
  const [stateFilter, setStateFilter] = useState<"" | RenderState>("");
  const [renders, setRenders] = useState<RenderListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<RenderDetail | null>(null);
  const [candidate, setCandidate] = useState<CandidateDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [captionText, setCaptionText] = useState("");
  const [form, setForm] = useState<PlanForm>({
    candidate: initialCandidateId,
    idempotencyKey: newIdempotencyKey(),
    preset: "vertical_720p",
    width: 720,
    height: 1280,
    frameRate: 30,
    cropStrategy: "center_crop",
    captionMode: "both",
    captionStyle: "high_contrast_bottom",
    normalization: "speech_loudness",
    includeBranding: false
  });
  const [defectCategory, setDefectCategory] = useState("technical");
  const [defectDescription, setDefectDescription] = useState("");
  const [defectResolution, setDefectResolution] = useState("");
  const [defectChangesMeaning, setDefectChangesMeaning] = useState(false);

  async function loadRenders(sessionToken: string, filter = stateFilter, preferredId = "") {
    if (!sessionToken) return;
    setLoading(true);
    try {
      const response = await apiRequest<RenderListResponse>(
        `/api/v1/renders?${stateQuery(filter)}`,
        sessionToken
      );
      setRenders(response.items);
      setTotal(response.total);
      setSelectedId((current) => {
        const preferred = preferredId || current;
        if (preferred && response.items.some((item) => item.render.id === preferred)) {
          return preferred;
        }
        return response.items[0]?.render.id || preferredId;
      });
      if (response.items.length === 0 && !preferredId) {
        setDetail(null);
        setCandidate(null);
      }
    } catch (caught) {
      if (caught instanceof APIError && caught.status === 401) {
        storeSessionToken("");
        setToken("");
        setPrincipal(null);
      }
      setError(messageOf(caught, "Unable to load the render queue"));
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(sessionToken: string, renderId: string) {
    if (!sessionToken || !renderId) return;
    const next = await apiRequest<RenderDetail>(`/api/v1/renders/${renderId}`, sessionToken);
    setDetail(next);
    try {
      const sourceCandidate = await apiRequest<CandidateDetail>(
        `/api/v1/candidates/${next.plan.candidate_id}`,
        sessionToken
      );
      setCandidate(sourceCandidate);
    } catch {
      setCandidate(null);
    }
  }

  async function loadControlData(sessionToken: string) {
    const [nextCapabilities, nextProfile, nextControl] = await Promise.all([
      apiRequest<RenderCapabilities>("/api/v1/rendering/capabilities", sessionToken),
      apiRequest<TenantRenderProfile>("/api/v1/rendering/profile", sessionToken),
      apiRequest<SystemControl>("/api/v1/diagnostics/control", sessionToken)
    ]);
    setCapabilities(nextCapabilities);
    setProfile(nextProfile);
    setControl(nextControl);
    setProfileEnabled(nextProfile.branding_enabled);
    setProfileText(nextProfile.branding_text || "");
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
          return Promise.all([loadControlData(existing), loadRenders(existing)]);
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
    // Initial authentication is scoped to the server-supplied candidate handoff.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCandidateId]);

  useEffect(() => {
    if (!token || !selectedId) return;
    let cancelled = false;
    setCaptionText("");
    setPreviewUrl("");
    void loadDetail(token, selectedId).catch((caught) => {
      if (!cancelled) setError(messageOf(caught, "Unable to load render evidence"));
    });
    return () => {
      cancelled = true;
    };
    // loadDetail updates both immutable plan evidence and the approved source comparison.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, token]);

  useEffect(() => {
    if (!token || !selectedId || !detail || !ACTIVE_RENDER_STATES.includes(detail.render.state)) {
      return;
    }
    const interval = window.setInterval(() => {
      void Promise.all([
        loadDetail(token, selectedId),
        loadRenders(token, stateFilter, selectedId)
      ]).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(interval);
    // Poll only while this render is active; terminal evidence is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.render.state, selectedId, stateFilter, token]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const currentOutput = useMemo(
    () => detail?.outputs.find((output) => output.is_current) || null,
    [detail]
  );
  const drawtextAvailable = capabilities?.capabilities.includes("drawtext") || false;
  const sourceMatches = candidate
    ? candidate.candidate.start_ms === detail?.plan.source_start_ms &&
      candidate.candidate.end_ms === detail?.plan.source_end_ms &&
      candidate.candidate.boundary_revision === detail?.plan.source_boundary_revision
    : false;

  function handleToken(next: string) {
    setToken(next);
    if (!next) {
      setPrincipal(null);
      setRenders([]);
      setSelectedId("");
      setDetail(null);
      setCandidate(null);
      setCapabilities(null);
      setProfile(null);
      setControl(null);
    }
  }

  async function createRender(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setBusy(true);
    setError("");
    setNotice("");
    const custom = form.preset === "custom";
    try {
      const created = await apiRequest<RenderCreateResponse>(
        `/api/v1/candidates/${form.candidate.trim()}/render-plans`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            idempotency_key: form.idempotencyKey,
            preset: form.preset,
            ...(custom
              ? { width: form.width, height: form.height, frame_rate: form.frameRate }
              : {}),
            crop_strategy: form.cropStrategy,
            captions: { mode: form.captionMode, style: form.captionStyle },
            audio: { normalization: form.normalization },
            include_tenant_branding: form.includeBranding,
            video_codec: "h264",
            audio_codec: "aac",
            container: "mp4"
          })
        }
      );
      setNotice(
        created.idempotent
          ? "The existing immutable plan was returned; no duplicate render was queued."
          : "Immutable render plan validated and queued. No publishing action was created."
      );
      setForm((current) => ({ ...current, idempotencyKey: newIdempotencyKey() }));
      setSelectedId(created.render.id);
      setDetail({
        plan: created.plan,
        captions: created.captions,
        render: created.render,
        outputs: [],
        defects: []
      });
      await loadRenders(token, stateFilter, created.render.id);
    } catch (caught) {
      setError(messageOf(caught, "Unable to create the render plan"));
    } finally {
      setBusy(false);
    }
  }

  async function action(kind: "cancel" | "retry") {
    if (!token || !detail) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiRequest(`/api/v1/renders/${detail.render.id}/${kind}`, token, { method: "POST" });
      setNotice(kind === "cancel" ? "Cancellation requested." : "Bounded retry queued.");
      await Promise.all([
        loadDetail(token, detail.render.id),
        loadRenders(token, stateFilter, detail.render.id)
      ]);
    } catch (caught) {
      setError(messageOf(caught, `Unable to ${kind} this render`));
    } finally {
      setBusy(false);
    }
  }

  async function loadPreview() {
    if (!token || !detail) return;
    setBusy(true);
    setError("");
    try {
      const blob = await authenticatedBlob(`/api/v1/renders/${detail.render.id}/preview`, token);
      setPreviewUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return URL.createObjectURL(blob);
      });
    } catch (caught) {
      setError(messageOf(caught, "Unable to verify and load the private preview"));
    } finally {
      setBusy(false);
    }
  }

  async function downloadOutput() {
    if (!token || !detail || !currentOutput) return;
    setBusy(true);
    setError("");
    try {
      const blob = await authenticatedBlob(`/api/v1/renders/${detail.render.id}/download`, token);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `clip-${detail.render.id}.${detail.plan.container}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(messageOf(caught, "Unable to download the validated output"));
    } finally {
      setBusy(false);
    }
  }

  async function loadCaptions() {
    if (!token || !detail) return;
    setBusy(true);
    setError("");
    try {
      const blob = await authenticatedBlob(`/api/v1/renders/${detail.render.id}/captions`, token);
      setCaptionText(await blob.text());
    } catch (caught) {
      setError(messageOf(caught, "No verified sidecar caption file is available"));
    } finally {
      setBusy(false);
    }
  }

  async function reportDefect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !detail) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await apiRequest(`/api/v1/renders/${detail.render.id}/defects`, token, {
        method: "POST",
        body: JSON.stringify({
          category: defectCategory,
          description: defectDescription.trim(),
          meaning_or_boundary_changed:
            defectChangesMeaning || defectCategory === "meaning_or_boundary",
          requested_resolution: defectResolution.trim() || null
        })
      });
      setNotice(
        defectChangesMeaning || defectCategory === "meaning_or_boundary"
          ? "Defect recorded. Meaning or boundary changes must return to candidate review."
          : "Defect recorded; this output now needs human review."
      );
      setDefectDescription("");
      setDefectResolution("");
      await Promise.all([
        loadDetail(token, detail.render.id),
        loadRenders(token, stateFilter, detail.render.id)
      ]);
    } catch (caught) {
      setError(messageOf(caught, "Unable to record this defect"));
    } finally {
      setBusy(false);
    }
  }

  async function updateControl(paused: boolean) {
    if (!token || controlReason.trim().length < 10) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await apiRequest<SystemControl>(
        `/api/v1/diagnostics/control/${paused ? "pause" : "resume"}`,
        token,
        { method: "POST", body: JSON.stringify({ reason: controlReason.trim() }) }
      );
      setControl(updated);
      setControlReason("");
      setNotice(
        paused
          ? "The global processing pause is active."
          : "Global processing resumed with an auditable owner reason."
      );
    } catch (caught) {
      setError(messageOf(caught, "Unable to update the global processing control"));
    } finally {
      setBusy(false);
    }
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setBusy(true);
    setError("");
    try {
      const updated = await apiRequest<TenantRenderProfile>("/api/v1/rendering/profile", token, {
        method: "PUT",
        body: JSON.stringify({
          branding_enabled: profileEnabled,
          branding_text: profileText.trim() || null,
          branding_position: "top_right",
          branding_opacity: 0.85,
          branding_font_size: 36
        })
      });
      setProfile(updated);
      setNotice("Tenant branding policy saved. Existing render plans remain immutable.");
    } catch (caught) {
      setError(messageOf(caught, "Unable to save tenant branding policy"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <header className="render-hero">
        <div>
          <p className="eyebrow">PRODUCTION / VALIDATED MEDIA RENDERING</p>
          <h1>Render control room</h1>
          <p>
            Turn only approved candidates into immutable, source-mapped render plans. Every output
            is probed, checksummed, private, retry-bounded, and held outside any publishing flow.
          </p>
        </div>
        <div className={`render-readiness ${capabilities?.render_ready ? "render-ready" : "render-blocked"}`}>
          <span />
          <div><strong>{capabilities?.render_ready ? "Renderer ready" : "Renderer unavailable"}</strong><small>{capabilities?.ffmpeg_version || "Authenticate to inspect tooling"}</small></div>
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
            if (next && sessionToken) {
              void Promise.all([loadControlData(sessionToken), loadRenders(sessionToken)]);
            }
          }}
        />
      </div>

      {!token || !principal ? (
        <section className="media-locked">
          <div className="gate-lock"><Icon name="lock" className="h-6 w-6" /></div>
          <div><h2>Tenant authentication required</h2><p>Plans, source comparisons, previews, captions, and output files remain private to a tenant session.</p></div>
        </section>
      ) : (
        <>
          {(error || notice) && (
            <div className={`media-notice ${error ? "notice-error" : "notice-ok"}`} role={error ? "alert" : "status"}>
              {error || notice}
            </div>
          )}

          {control && <section className={`worker-control ${control.paused ? "worker-paused" : "worker-running"}`}><div><span><Icon name={control.paused ? "pause" : "pulse"} className="h-4 w-4" /> {control.paused ? "RENDERING PAUSED" : "RENDERING ALLOWED"}</span><p>{control.reason} · updated {new Date(control.updated_at).toLocaleString()}</p></div>{principal.role === "owner" && principal.is_platform_admin ? <div className="worker-action"><input minLength={10} maxLength={500} value={controlReason} onChange={(event) => setControlReason(event.target.value)} placeholder={`Audited reason to ${control.paused ? "resume" : "pause"} processing`} /><button type="button" disabled={busy || controlReason.trim().length < 10} onClick={() => void updateControl(!control.paused)}>{control.paused ? "Resume audited rendering" : "Pause all workers"}</button></div> : control.paused && <small>A platform administrator with owner authority must provide an audited reason before queued work can run.</small>}</section>}

          <section className="render-tooling-strip">
            <div><span>FFmpeg</span><strong>{capabilities?.ffmpeg_available ? "Detected" : "Unavailable"}</strong></div>
            <div><span>Validated presets</span><strong>{capabilities?.presets.length || 0}</strong></div>
            <div><span>Text branding</span><strong>{drawtextAvailable ? "Available" : "Capability-gated"}</strong></div>
            <div><span>Publishing</span><strong>Not present</strong></div>
          </section>

          {capabilities && capabilities.limitations.length > 0 && (
            <div className="render-limitations"><Icon name="pulse" className="h-5 w-5" /><p><strong>Transparent limitations</strong>{capabilities.limitations.join(" · ")}</p></div>
          )}

          <section className="render-setup-grid">
            <form className="render-plan-card" onSubmit={createRender}>
              <div className="render-card-head"><div><p className="card-kicker">IMMUTABLE PLAN</p><h2>Queue an approved moment</h2></div><span>EDITOR+</span></div>
              <div className="render-fields">
                <label className="field-full">Approved candidate UUID<input required value={form.candidate} onChange={(event) => setForm({ ...form, candidate: event.target.value })} placeholder="Paste or hand off from Discovery" /></label>
                <label>Preset<select value={form.preset} onChange={(event) => setForm({ ...form, preset: event.target.value as RenderPreset })}><option value="vertical_720p">Vertical 720p · 9:16</option><option value="vertical_1080p">Vertical 1080p · 9:16</option><option value="tiktok_vertical">TikTok-compatible vertical</option><option value="instagram_reels">Reels-compatible vertical</option><option value="youtube_shorts">Shorts-compatible vertical</option><option value="square_1080p">Square 1080p · 1:1</option><option value="landscape_1080p">Landscape 1080p · 16:9</option><option value="custom">Custom dimensions</option></select></label>
                <label>Reframing<select value={form.cropStrategy} onChange={(event) => setForm({ ...form, cropStrategy: event.target.value as PlanForm["cropStrategy"] })}><option value="center_crop">Center crop</option><option value="fit_pad">Fit with padding</option><option value="smart_face_fallback">Smart-face request + disclosed fallback</option></select></label>
                <label>Caption delivery<select value={form.captionMode} onChange={(event) => setForm({ ...form, captionMode: event.target.value as PlanForm["captionMode"] })}><option value="both">Burned in + SRT</option><option value="burn_in">Burned in</option><option value="sidecar">SRT sidecar</option><option value="none">No captions</option></select></label>
                <label>Caption style<select value={form.captionStyle} onChange={(event) => setForm({ ...form, captionStyle: event.target.value as PlanForm["captionStyle"] })}><option value="high_contrast_bottom">High contrast bottom</option><option value="clean_bottom">Clean bottom</option></select></label>
                <label>Audio<select value={form.normalization} onChange={(event) => setForm({ ...form, normalization: event.target.value as PlanForm["normalization"] })}><option value="speech_loudness">Speech loudness normalization</option><option value="none">Preserve source levels</option></select></label>
                <label>Tenant branding<select value={form.includeBranding ? "on" : "off"} disabled={!profile?.branding_enabled || !drawtextAvailable} onChange={(event) => setForm({ ...form, includeBranding: event.target.value === "on" })}><option value="off">Do not include</option><option value="on">Include configured text</option></select></label>
                {form.preset === "custom" && <><label>Width<input type="number" min="240" max="8192" step="2" value={form.width} onChange={(event) => setForm({ ...form, width: Number(event.target.value) })} /></label><label>Height<input type="number" min="240" max="8192" step="2" value={form.height} onChange={(event) => setForm({ ...form, height: Number(event.target.value) })} /></label><label>Frame rate<input type="number" min="1" max="120" step="1" value={form.frameRate} onChange={(event) => setForm({ ...form, frameRate: Number(event.target.value) })} /></label></>}
                <label className="field-full">Idempotency key<input required minLength={8} value={form.idempotencyKey} onChange={(event) => setForm({ ...form, idempotencyKey: event.target.value })} /></label>
              </div>
              <p className="render-plan-note">Smart/face tracking is not claimed. When unavailable, the plan records a center-crop fallback. Music and third-party media remain disabled.</p>
              <button className="primary-button" type="submit" disabled={busy || control?.paused || !can(principal.role, "editor") || !capabilities?.render_ready}>Validate plan and queue render <Icon name="arrow" className="h-4 w-4" /></button>
            </form>

            <aside className="branding-card">
              <p className="card-kicker">TENANT BRAND POLICY</p>
              <h2>Configured, never improvised</h2>
              <p>Branding can enter a plan only from this tenant policy. The current renderer reports text overlay as <strong>{drawtextAvailable ? "available" : "unavailable"}</strong>.</p>
              <form onSubmit={saveProfile}>
                <label><input type="checkbox" checked={profileEnabled} onChange={(event) => setProfileEnabled(event.target.checked)} /> Enable tenant text branding</label>
                <label>Approved text<input maxLength={80} value={profileText} onChange={(event) => setProfileText(event.target.value)} placeholder="Tenant-approved mark" /></label>
                <button type="submit" disabled={busy || !can(principal.role, "owner")}>Save immutable-plan source</button>
              </form>
              {!can(principal.role, "owner") && <small>Tenant-owner authority is required to change policy.</small>}
            </aside>
          </section>

          <section className="render-workspace">
            <div className="render-queue">
              <header><div><p className="card-kicker">RENDER QUEUE</p><h2>{total} jobs</h2></div><button type="button" onClick={() => void loadRenders(token, stateFilter, selectedId)}>{loading ? "Refreshing…" : "Refresh"}</button></header>
              <div className="render-filters">
                <label>State<select value={stateFilter} onChange={(event) => { const next = event.target.value as "" | RenderState; setStateFilter(next); void loadRenders(token, next); }}><option value="">All states</option><option value="queued">Queued</option><option value="validating">Validating</option><option value="rendering">Rendering</option><option value="validating_output">Validating output</option><option value="succeeded">Succeeded</option><option value="needs_review">Needs review</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option></select></label>
              </div>
              <div className="render-list">
                {renders.map((item) => <button type="button" key={item.render.id} className={item.render.id === selectedId ? "render-row render-row-selected" : "render-row"} onClick={() => setSelectedId(item.render.id)}><div className="render-row-top"><span className={`render-state render-${item.render.state}`}>{renderStateLabel(item.render.state)}</span><code>{item.render.id.slice(0, 8)}</code></div><strong>{item.plan.preset.replaceAll("_", " ")}</strong><p>{item.plan.width}×{item.plan.height} · {formatTime(item.plan.duration_ms)} · plan {item.plan.plan_version}</p><div className="render-progress"><span style={{ width: `${item.render.progress_percent}%` }} /></div><footer><span>{item.render.stage.replaceAll("_", " ")}</span><b>{item.render.progress_percent}%</b></footer></button>)}
                {!loading && renders.length === 0 && <div className="render-empty"><span>00</span><p><strong>No render jobs</strong><small>Approve a candidate in Discovery, then validate an immutable plan here.</small></p></div>}
              </div>
            </div>

            <div className="render-inspector">
              {!detail ? <div className="inspector-empty"><Icon name="render" className="h-8 w-8" /><h2>Select a render</h2><p>Lifecycle, source comparison, plan version, captions, output validation, provenance, and defect controls appear here.</p></div> : <>
                <header className="render-detail-head"><div><p className="eyebrow">RENDER / {detail.render.id.slice(0, 8).toUpperCase()}</p><h2>{detail.plan.preset.replaceAll("_", " ")}</h2><span>{detail.plan.width}×{detail.plan.height} · {detail.plan.frame_rate} fps · {detail.plan.aspect_ratio}</span></div><div><span className={`render-state render-${detail.render.state}`}>{renderStateLabel(detail.render.state)}</span><code>{detail.render.progress_percent}% · {detail.render.stage.replaceAll("_", " ")}</code></div></header>

                <div className="render-action-bar">
                  {ACTIVE_RENDER_STATES.includes(detail.render.state) && <button type="button" disabled={busy || !can(principal.role, "editor")} onClick={() => void action("cancel")}>Cancel safely</button>}
                  {["failed", "cancelled", "needs_review"].includes(detail.render.state) && <button type="button" disabled={busy || !can(principal.role, "editor") || detail.render.attempt_count >= detail.render.max_attempts} onClick={() => void action("retry")}>Queue bounded retry</button>}
                  {detail.render.state === "succeeded" && <button type="button" disabled={busy} onClick={() => void loadPreview()}>Load private preview</button>}
                  {currentOutput && <button type="button" disabled={busy} onClick={() => void downloadOutput()}>Download verified MP4</button>}
                  {currentOutput?.sidecar_storage_key && <button type="button" disabled={busy} onClick={() => void loadCaptions()}>Inspect SRT</button>}
                </div>

                {detail.render.failure_message && <div className="render-failure"><strong>{detail.render.failure_category}</strong><p>{detail.render.failure_message}</p><small>Attempt {detail.render.attempt_count} of {detail.render.max_attempts}</small></div>}

                {previewUrl && <div className="render-preview"><video controls playsInline src={previewUrl}><track kind="captions" /></video><p>Authenticated browser blob · no bearer token appears in the URL</p></div>}

                <section className="source-comparison">
                  <div className="render-section-head"><div><p className="card-kicker">APPROVED-CANDIDATE COMPARISON</p><h3>Source authority versus plan snapshot</h3></div><span className={sourceMatches ? "comparison-match" : "comparison-warning"}>{candidate ? (sourceMatches ? "MATCH" : "REVIEW") : "UNAVAILABLE"}</span></div>
                  <div className="comparison-grid"><article><span>Approved source candidate</span>{candidate ? <><strong>{formatTime(candidate.candidate.start_ms)}–{formatTime(candidate.candidate.end_ms)}</strong><q>{candidate.candidate.transcript_excerpt}</q><small>State: {candidate.candidate.state} · boundary v{candidate.candidate.boundary_revision}</small></> : <p>Candidate evidence could not be loaded.</p>}</article><article><span>Immutable render plan</span><strong>{formatTime(detail.plan.source_start_ms)}–{formatTime(detail.plan.source_end_ms)}</strong><p>Output 00:00–{formatTime(detail.plan.duration_ms)} maps linearly to source. No moment is synthesized or reordered.</p><small>Boundary v{detail.plan.source_boundary_revision} · plan {detail.plan.plan_version}</small></article></div>
                  <Link href={`/discovery?asset=${detail.plan.asset_id}`}>Open source review evidence <Icon name="arrow" className="h-3.5 w-3.5" /></Link>
                </section>

                <section className="render-evidence-grid">
                  <article><p className="card-kicker">PLAN CONTRACT</p><dl><div><dt>Plan / renderer</dt><dd>{detail.plan.plan_version} / {detail.plan.renderer_version}</dd></div><div><dt>Crop requested</dt><dd>{detail.plan.crop_strategy_requested.replaceAll("_", " ")}</dd></div><div><dt>Crop applied</dt><dd>{detail.plan.crop_strategy_applied.replaceAll("_", " ")}</dd></div><div><dt>Smart crop used</dt><dd>{detail.plan.smart_crop_used ? "Yes" : "No"}</dd></div><div><dt>Captions</dt><dd>{detail.plan.caption_mode.replaceAll("_", " ")}</dd></div><div><dt>Audio</dt><dd>{jsonSummary(detail.plan.audio_settings)}</dd></div><div><dt>Safe area</dt><dd>{jsonSummary(detail.plan.safe_area)}</dd></div></dl>{detail.plan.crop_fallback_reason && <p className="evidence-warning">{detail.plan.crop_fallback_reason}</p>}</article>
                  <article><p className="card-kicker">OUTPUT VALIDATION</p>{currentOutput ? <dl><div><dt>Container / codecs</dt><dd>{currentOutput.format_name} · {currentOutput.video_codec}/{currentOutput.audio_codec}</dd></div><div><dt>Probe</dt><dd>{currentOutput.probe_version}</dd></div><div><dt>Duration</dt><dd>{currentOutput.duration_seconds.toFixed(3)} s</dd></div><div><dt>Dimensions</dt><dd>{currentOutput.width}×{currentOutput.height}</dd></div><div><dt>Audio stream</dt><dd>{currentOutput.audio_channels || "—"} ch · {currentOutput.audio_sample_rate || "—"} Hz</dd></div><div><dt>Size</dt><dd>{formatBytes(currentOutput.size_bytes)}</dd></div><div><dt>SHA-256</dt><dd><code>{currentOutput.checksum_sha256}</code></dd></div></dl> : <p className="evidence-pending">No output record exists until rendering and independent output validation both succeed.</p>}</article>
                </section>

                <section className="caption-evidence">
                  <div className="render-section-head"><div><p className="card-kicker">TIMESTAMP-ALIGNED CAPTIONS</p><h3>{detail.captions.length} bounded cues</h3></div><span>{detail.plan.caption_mode.replaceAll("_", " ")}</span></div>
                  <div className="caption-cue-list">{detail.captions.slice(0, 12).map((cue) => <article key={cue.id}><time>{formatTime(cue.output_start_ms)}–{formatTime(cue.output_end_ms)}</time><p>{cue.text}</p><small>source {formatTime(cue.source_start_ms)}–{formatTime(cue.source_end_ms)} · {cue.line_count} line{cue.line_count === 1 ? "" : "s"}</small></article>)}</div>
                  {captionText && <pre className="caption-text">{captionText}</pre>}
                </section>

                <section className="provenance-panel">
                  <div><p className="card-kicker">PROVENANCE + LIMITATIONS</p><h3>No hidden intelligence claims</h3></div>
                  <ul>{detail.plan.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
                  <details><summary>Plan provenance</summary><pre>{JSON.stringify(detail.plan.provenance, null, 2)}</pre></details>
                  {currentOutput && <details><summary>Output provenance and validation</summary><pre>{JSON.stringify({ provenance: currentOutput.provenance, validation: currentOutput.validation }, null, 2)}</pre></details>}
                </section>

                {detail.render.state === "succeeded" && <form className="defect-form" onSubmit={reportDefect}><div className="render-section-head"><div><p className="card-kicker">HUMAN QUALITY GATE</p><h3>Report a render defect</h3></div><span>REVIEWER+</span></div><div className="defect-fields"><label>Category<select value={defectCategory} onChange={(event) => setDefectCategory(event.target.value)}><option value="captions">Captions</option><option value="crop">Crop</option><option value="audio">Audio</option><option value="visual">Visual</option><option value="timing">Timing</option><option value="branding">Branding</option><option value="technical">Technical</option><option value="meaning_or_boundary">Meaning or boundary</option><option value="other">Other</option></select></label><label className="field-full">Description<textarea required minLength={10} value={defectDescription} onChange={(event) => setDefectDescription(event.target.value)} /></label><label className="field-full">Requested resolution<textarea value={defectResolution} onChange={(event) => setDefectResolution(event.target.value)} /></label><label className="defect-check field-full"><input type="checkbox" checked={defectChangesMeaning} onChange={(event) => setDefectChangesMeaning(event.target.checked)} /> This fix would alter meaning or source boundaries and must return to candidate review.</label></div><button type="submit" disabled={busy || !can(principal.role, "reviewer")}>Move output to needs review</button></form>}

                {detail.defects.length > 0 && <section className="defect-history"><p className="card-kicker">DEFECT LEDGER</p>{detail.defects.map((defect) => <article key={defect.id}><strong>{defect.category.replaceAll("_", " ")}</strong><p>{defect.description}</p><small>{new Date(defect.created_at).toLocaleString()} · {defect.meaning_or_boundary_changed ? "returns to candidate review" : "render-only correction"}</small></article>)}</section>}
              </>}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
