"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { SessionAccess } from "@/components/session-access";
import {
  APIError,
  Asset,
  AssetState,
  apiRequest,
  formatBytes,
  isActiveState,
  readSessionToken,
  storeSessionToken
} from "@/lib/media-api";

interface Principal {
  email: string;
  role: string;
  tenant_id: string;
  is_platform_admin?: boolean;
}

interface UploadResponse {
  asset: Asset;
  duplicate: boolean;
  processing_job_id: string | null;
}

interface SourceCapability {
  source: string;
  mode: string;
  enabled: boolean;
  limitations: string[];
}

interface SystemControl {
  paused: boolean;
  reason: string;
  updated_at: string;
}

const filters: Array<{ label: string; value: "" | AssetState }> = [
  { label: "All", value: "" },
  { label: "In progress", value: "queued" },
  { label: "Ready", value: "ready" },
  { label: "Review", value: "needs_review" },
  { label: "Failed", value: "failed" }
];

export function MediaLibrary() {
  const [token, setToken] = useState("");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [filter, setFilter] = useState<"" | AssetState>("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [capabilities, setCapabilities] = useState<SourceCapability[]>([]);
  const [control, setControl] = useState<SystemControl | null>(null);
  const [controlReason, setControlReason] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [rightsNote, setRightsNote] = useState("");
  const [permissionConfirmed, setPermissionConfirmed] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState(0);

  const loadAssets = useCallback(async (sessionToken: string, state = filter, q = search) => {
    if (!sessionToken) return;
    setLoading(true);
    setError("");
    try {
      const parameters = new URLSearchParams();
      if (state && state !== "queued") parameters.set("state", state);
      if (q.trim()) parameters.set("q", q.trim());
      const result = await apiRequest<Asset[]>(`/api/v1/assets?${parameters.toString()}`, sessionToken);
      const visible = state === "queued" ? result.filter((asset) => isActiveState(asset.state)) : result;
      setAssets(visible);
    } catch (caught) {
      if (caught instanceof APIError && caught.status === 401) {
        storeSessionToken("");
        setToken("");
        setPrincipal(null);
        setError("Your API session expired. Authenticate again.");
      } else {
        setError(caught instanceof Error ? caught.message : "Unable to load media assets");
      }
    } finally {
      setLoading(false);
    }
  }, [filter, search]);

  useEffect(() => {
    const existing = readSessionToken();
    if (!existing) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setToken(existing);
      void apiRequest<Principal>("/api/v1/auth/me", existing)
        .then((current) => {
          if (!cancelled) setPrincipal(current);
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
  }, []);

  useEffect(() => {
    if (!token || !principal) return;
    const timer = window.setTimeout(() => {
      void loadAssets(token);
      void apiRequest<SourceCapability[]>("/api/v1/assets/sources", token)
        .then(setCapabilities)
        .catch(() => undefined);
      void apiRequest<SystemControl>("/api/v1/diagnostics/control", token)
        .then(setControl)
        .catch(() => undefined);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [filter, principal, search, token, loadAssets]);

  const hasActive = useMemo(() => assets.some((asset) => isActiveState(asset.state)), [assets]);
  useEffect(() => {
    if (!token || !hasActive) return;
    const timer = window.setInterval(() => void loadAssets(token), 3000);
    return () => window.clearInterval(timer);
  }, [token, hasActive, loadAssets]);

  function handleToken(nextToken: string) {
    setToken(nextToken);
    if (!nextToken) {
      setAssets([]);
      setCapabilities([]);
      setControl(null);
    }
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !token || !permissionConfirmed) return;
    setUploading(true);
    setUploadPercent(0);
    setError("");
    setNotice("");
    const payload = new FormData();
    payload.append("file", file);
    payload.append("permission_confirmed", String(permissionConfirmed));
    payload.append("rights_note", rightsNote);
    const request = new XMLHttpRequest();
    request.open("POST", "/api/v1/assets/upload");
    request.setRequestHeader("Authorization", `Bearer ${token}`);
    request.upload.onprogress = (progress) => {
      if (progress.lengthComputable) setUploadPercent(Math.round((progress.loaded / progress.total) * 100));
    };
    request.onload = () => {
      setUploading(false);
      try {
        const response = JSON.parse(request.responseText) as UploadResponse & { error?: { message?: string } };
        if (request.status !== 200 && request.status !== 201) {
          throw new Error(response.error?.message || `Upload failed with status ${request.status}`);
        }
        setNotice(response.duplicate
          ? "This checksum already exists in your tenant. The existing asset was returned; no duplicate job was created."
          : "Upload verified and queued for private media processing.");
        setFile(null);
        setRightsNote("");
        setPermissionConfirmed(false);
        const input = document.getElementById("media-file") as HTMLInputElement | null;
        if (input) input.value = "";
        void loadAssets(token);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "The upload failed");
      }
    };
    request.onerror = () => {
      setUploading(false);
      setError("The upload connection failed before the server accepted the media.");
    };
    request.send(payload);
  }

  async function action(asset: Asset, kind: "retry" | "cancel") {
    setError("");
    setNotice("");
    try {
      await apiRequest<Asset>(`/api/v1/assets/${asset.id}/${kind}`, token, { method: "POST" });
      setNotice(kind === "retry" ? "Asset requeued within its retry budget." : "Cancellation requested.");
      await loadAssets(token);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `Unable to ${kind} asset`);
    }
  }

  async function updateControl(paused: boolean) {
    if (!token || controlReason.trim().length < 10) return;
    setError("");
    try {
      const updated = await apiRequest<SystemControl>(
        `/api/v1/diagnostics/control/${paused ? "pause" : "resume"}`,
        token,
        { method: "POST", body: JSON.stringify({ reason: controlReason.trim() }) }
      );
      setControl(updated);
      setControlReason("");
      setNotice(paused ? "The global processing pause is active." : "Global processing was resumed with an audit record.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to update the global control");
    }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearch(query);
  }

  const urlCapability = capabilities.find((item) => item.source === "url");

  return (
    <div>
      <div className="media-toolbar">
        <SessionAccess token={token} onToken={handleToken} principal={principal} onPrincipal={setPrincipal} />
      </div>

      {!token || !principal ? (
        <section className="media-locked">
          <div className="gate-lock"><Icon name="lock" className="h-6 w-6" /></div>
          <div><h2>Tenant authentication required</h2><p>Assets, provenance, and transcripts are private. Start a scoped API session above; this dashboard never offers anonymous media access.</p></div>
        </section>
      ) : (
        <>
          {control && <section className={`worker-control ${control.paused ? "worker-paused" : "worker-running"}`}><div><span><Icon name={control.paused ? "pause" : "pulse"} className="h-4 w-4" /> {control.paused ? "PROCESSING PAUSED" : "PROCESSING ALLOWED"}</span><p>{control.reason} · updated {new Date(control.updated_at).toLocaleString()}</p></div>{principal.role === "owner" && principal.is_platform_admin ? <div className="worker-action"><input minLength={10} maxLength={500} value={controlReason} onChange={(event) => setControlReason(event.target.value)} placeholder={`Reason to ${control.paused ? "resume" : "pause"} (minimum 10 characters)`} /><button type="button" disabled={controlReason.trim().length < 10} onClick={() => void updateControl(!control.paused)}>{control.paused ? "Resume audited processing" : "Pause all workers"}</button></div> : control.paused && <small>A platform administrator with owner authority must provide an audited reason to resume queued processing.</small>}</section>}
          <section className="ingest-grid">
            <form className="upload-card" onSubmit={upload}>
              <div className="upload-card-head"><div><p className="card-kicker">LOCAL INGESTION</p><h2>Bring authorized media in.</h2></div><span className="source-live"><i /> REAL LOCAL</span></div>
              <label className="file-drop" htmlFor="media-file">
                <Icon name="upload" className="h-7 w-7" />
                <strong>{file ? file.name : "Choose audio or video"}</strong>
                <span>{file ? formatBytes(file.size) : "Signature, size, codec, duration, and checksum are verified server-side"}</span>
                <input id="media-file" required type="file" accept="audio/*,video/*,.mkv,.m4a,.opus" onChange={(event) => setFile(event.target.files?.[0] || null)} />
              </label>
              <label className="rights-field">Rights & provenance note<textarea required minLength={10} maxLength={4000} value={rightsNote} onChange={(event) => setRightsNote(event.target.value)} placeholder="Describe ownership, license, consent, or the authorized source…" /></label>
              <label className="rights-check"><input required type="checkbox" checked={permissionConfirmed} onChange={(event) => setPermissionConfirmed(event.target.checked)} /><span><strong>I confirm I have permission to process this media.</strong><small>Do not upload scraped, paywalled, private, or access-controlled material without authorization.</small></span></label>
              {uploading && <div className="upload-progress"><span style={{ width: `${uploadPercent}%` }} /><p>Browser upload {uploadPercent}% · server validation follows</p></div>}
              <button className="primary-button" type="submit" disabled={uploading || !file || !permissionConfirmed}>{uploading ? "Uploading…" : "Upload & verify"}<Icon name="arrow" className="h-4 w-4" /></button>
            </form>

            <div className="source-card disabled-source">
              <div><p className="card-kicker">REMOTE INGESTION</p><h2>URLs stay off by default.</h2></div>
              <span className="source-disabled"><Icon name="lock" className="h-3.5 w-3.5" /> DISABLED</span>
              <p>{urlCapability?.limitations[0] || "URL ingestion is disabled until redirect and DNS-rebinding-safe SSRF controls are proven."}</p>
              <ul><li>No redirects to private networks</li><li>No paywall or access-control bypass</li><li>No unbounded remote responses</li></ul>
              <button type="button" disabled>Remote URL ingestion unavailable</button>
            </div>
          </section>

          {(notice || error) && <div className={`media-notice ${error ? "notice-error" : "notice-ok"}`} role="status">{error || notice}</div>}

          <section className="library-section">
            <div className="library-head"><div><p className="eyebrow">TENANT MEDIA LIBRARY</p><h2>Evidence, not a black box.</h2></div><form className="asset-search" onSubmit={submitSearch}><Icon name="search" className="h-4 w-4" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search filename or transcript" /><button type="submit">Search</button></form></div>
            <div className="filter-row">{filters.map((item) => <button type="button" key={item.label} className={filter === item.value ? "filter-active" : ""} onClick={() => setFilter(item.value)}>{item.label}</button>)}<button type="button" onClick={() => void loadAssets(token)}>Refresh</button></div>

            {loading && assets.length === 0 ? <div className="asset-empty">Loading private media…</div> : assets.length === 0 ? (
              <div className="asset-empty"><span>00</span><div><h3>No matching media</h3><p>Upload an authorized local file. The server will record provenance, checksum, detected type, and every lifecycle transition.</p></div></div>
            ) : (
              <div className="asset-list">{assets.map((asset) => (
                <article className="asset-row" key={asset.id}>
                  <div className={`asset-kind kind-${asset.media_kind}`}><Icon name={asset.media_kind === "audio" ? "audio" : "video"} className="h-5 w-5" /></div>
                  <div className="asset-main"><div className="asset-title-line"><Link href={`/media/${asset.id}`}>{asset.original_filename}</Link><span className={`state-badge state-${asset.state}`}>{asset.state.replaceAll("_", " ")}</span></div><p>{asset.detected_content_type} · {formatBytes(asset.size_bytes)} · SHA-256 <code title={asset.checksum_sha256}>{asset.checksum_sha256.slice(0, 16)}…</code></p><div className="asset-progress"><span style={{ width: `${asset.progress_percent}%` }} /></div><small>{asset.processing_stage.replaceAll("_", " ")} · {asset.progress_percent}%</small>{asset.error_message && <em>{asset.error_category}: {asset.error_message}</em>}</div>
                  <div className="asset-provenance"><strong>RIGHTS CONFIRMED</strong><p>{asset.rights_note}</p><small>{new Date(asset.acquired_at).toLocaleString()}</small></div>
                  <div className="asset-actions"><Link href={`/media/${asset.id}`}>Inspect</Link>{["failed", "needs_review"].includes(asset.state) && <button type="button" onClick={() => void action(asset, "retry")}>Retry</button>}{isActiveState(asset.state) && <button className="danger-link" type="button" onClick={() => void action(asset, "cancel")}>Cancel</button>}</div>
                </article>
              ))}</div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
