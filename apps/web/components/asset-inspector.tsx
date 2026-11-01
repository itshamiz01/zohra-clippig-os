"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { SessionAccess } from "@/components/session-access";
import {
  APIError,
  Asset,
  AssetDetail,
  TranscriptDetail,
  apiRequest,
  formatBytes,
  formatTime,
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

export function AssetInspector({ assetId }: { assetId: string }) {
  const [token, setToken] = useState("");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const [transcript, setTranscript] = useState<TranscriptDetail | null>(null);
  const [mode, setMode] = useState<"timeline" | "normalized" | "original" | "chunks">("timeline");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async (sessionToken: string) => {
    if (!sessionToken) return;
    setLoading(true);
    setError("");
    try {
      const asset = await apiRequest<AssetDetail>(`/api/v1/assets/${assetId}`, sessionToken);
      setDetail(asset);
      if (asset.transcript) {
        const evidence = await apiRequest<TranscriptDetail>(`/api/v1/assets/${assetId}/transcript?segment_limit=200&word_limit=2000`, sessionToken);
        setTranscript(evidence);
      } else {
        setTranscript(null);
      }
    } catch (caught) {
      if (caught instanceof APIError && caught.status === 401) {
        storeSessionToken("");
        setToken("");
        setPrincipal(null);
        setError("Your API session expired.");
      } else {
        setError(caught instanceof Error ? caught.message : "Unable to load this asset");
      }
    } finally {
      setLoading(false);
    }
  }, [assetId]);

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
    if (!token || !principal || detail) return;
    const timer = window.setTimeout(() => void load(token), 0);
    return () => window.clearTimeout(timer);
  }, [detail, load, principal, token]);

  useEffect(() => {
    if (!token || !detail || !isActiveState(detail.asset.state)) return;
    const timer = window.setInterval(() => void load(token), 3000);
    return () => window.clearInterval(timer);
  }, [detail, load, token]);

  async function loadMore() {
    if (!token || !transcript) return;
    try {
      const next = await apiRequest<TranscriptDetail>(
        `/api/v1/assets/${assetId}/transcript?segment_offset=${transcript.segments.length}&segment_limit=200&word_limit=0`,
        token
      );
      setTranscript({ ...transcript, segments: [...transcript.segments, ...next.segments] });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load more transcript segments");
    }
  }

  async function loadMoreChunks() {
    if (!token || !transcript) return;
    try {
      const next = await apiRequest<TranscriptDetail>(
        `/api/v1/assets/${assetId}/transcript?segment_limit=1&word_limit=0&chunk_offset=${transcript.chunks.length}&chunk_limit=500`,
        token
      );
      setTranscript({ ...transcript, chunks: [...transcript.chunks, ...next.chunks] });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load more semantic chunks");
    }
  }

  async function action(kind: "retry" | "cancel") {
    if (!token) return;
    setError("");
    try {
      await apiRequest<Asset>(`/api/v1/assets/${assetId}/${kind}`, token, { method: "POST" });
      setNotice(kind === "retry" ? "Asset requeued within its retry budget." : "Cancellation requested.");
      await load(token);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : `Unable to ${kind} asset`);
    }
  }

  const visibleSegments = useMemo(() => {
    if (!transcript || !query.trim()) return transcript?.segments || [];
    const needle = query.trim().toLocaleLowerCase();
    return transcript.segments.filter((segment) => segment.normalized_text.toLocaleLowerCase().includes(needle));
  }, [query, transcript]);

  function handleToken(next: string) {
    setToken(next);
    if (!next) {
      setDetail(null);
      setTranscript(null);
    }
  }

  return (
    <div>
      <div className="inspector-nav"><Link href="/media"><Icon name="arrowBack" className="h-4 w-4" /> Media library</Link><SessionAccess token={token} onToken={handleToken} principal={principal} onPrincipal={setPrincipal} /></div>
      {!token || !principal ? (
        <section className="media-locked"><div className="gate-lock"><Icon name="lock" className="h-6 w-6" /></div><div><h2>Tenant authentication required</h2><p>This route does not reveal whether an asset exists until you establish a scoped session.</p></div></section>
      ) : loading && !detail ? <div className="asset-empty">Loading private evidence…</div> : error && !detail ? <div className="media-notice notice-error">{error}</div> : detail ? (
        <>
          <header className="asset-hero">
            <div className={`asset-kind kind-${detail.asset.media_kind}`}><Icon name={detail.asset.media_kind === "audio" ? "audio" : "video"} className="h-7 w-7" /></div>
            <div><p className="eyebrow">ASSET / {detail.asset.id.slice(0, 8).toUpperCase()}</p><h1>{detail.asset.original_filename}</h1><p>{detail.asset.detected_content_type} · {formatBytes(detail.asset.size_bytes)} · acquired {new Date(detail.asset.acquired_at).toLocaleString()}</p></div>
            <div className="asset-hero-state"><span className={`state-badge state-${detail.asset.state}`}>{detail.asset.state.replaceAll("_", " ")}</span><strong>{detail.asset.progress_percent}%</strong><small>{detail.asset.processing_stage.replaceAll("_", " ")}</small></div>
          </header>

          <div className="asset-progress large"><span style={{ width: `${detail.asset.progress_percent}%` }} /></div>
          {(error || notice || detail.asset.error_message) && <div className={`media-notice ${error || detail.asset.error_message ? "notice-error" : "notice-ok"}`}>{error || notice || `${detail.asset.error_category}: ${detail.asset.error_message}`}</div>}

          <section className="evidence-grid">
            <article className="evidence-card"><p className="card-kicker">PROVENANCE</p><h3>Authorization record</h3><dl><div><dt>Source</dt><dd>{detail.asset.source.replaceAll("_", " ")}</dd></div><div><dt>Permission</dt><dd>{detail.asset.permission_confirmed ? "confirmed" : "not confirmed"}</dd></div><div><dt>SHA-256</dt><dd><code title={detail.asset.checksum_sha256}>{detail.asset.checksum_sha256}</code></dd></div><div><dt>Declared type</dt><dd>{detail.asset.declared_content_type || "not supplied"}</dd></div><div><dt>Detected type</dt><dd>{detail.asset.detected_content_type}</dd></div></dl><blockquote>{detail.asset.rights_note}</blockquote></article>
            <article className="evidence-card"><p className="card-kicker">VERIFIED MEDIA</p><h3>Probe evidence</h3>{detail.metadata ? <dl><div><dt>Duration</dt><dd>{formatTime(detail.metadata.duration_seconds * 1000)}</dd></div><div><dt>Container</dt><dd>{detail.metadata.format_name}</dd></div><div><dt>Video</dt><dd>{detail.metadata.has_video ? `${detail.metadata.video_codec} · ${detail.metadata.width}×${detail.metadata.height}` : "none"}</dd></div><div><dt>Audio</dt><dd>{detail.metadata.has_audio ? `${detail.metadata.audio_codec} · ${detail.metadata.audio_sample_rate || "?"} Hz · ${detail.metadata.audio_channels || "?"} ch` : "none"}</dd></div><div><dt>Verified</dt><dd>{new Date(detail.metadata.verified_at).toLocaleString()}</dd></div></dl> : <p className="evidence-pending">Metadata has not been verified yet.</p>}</article>
            <article className="evidence-card"><p className="card-kicker">CONTROLS</p><h3>Bounded lifecycle</h3><p className="control-copy">Processing runs in a recoverable job with server-side state, timeouts, resource ceilings, and tenant-scoped storage.</p><div className="control-actions">{["failed", "needs_review"].includes(detail.asset.state) && <button type="button" onClick={() => void action("retry")}>Retry within budget</button>}{isActiveState(detail.asset.state) && <button className="danger-link" type="button" onClick={() => void action("cancel")}>Cancel processing</button>}<button type="button" onClick={() => void load(token)}>Refresh evidence</button>{detail.asset.state === "ready" && transcript && <Link href={`/discovery?asset=${detail.asset.id}`}>Open candidate discovery</Link>}</div></article>
          </section>

          <section className="transcript-panel">
            <div className="transcript-head"><div><p className="eyebrow">TRANSCRIPT INSPECTION</p><h2>{transcript ? "Timestamped provider evidence" : "No transcript available"}</h2></div>{transcript && <div className="transcript-metrics"><span><strong>{transcript.transcript.segment_count}</strong> segments</span><span><strong>{transcript.transcript.word_count}</strong> words</span><span><strong>{transcript.transcript.confidence === null ? "—" : `${Math.round(transcript.transcript.confidence * 100)}%`}</strong> confidence</span></div>}</div>
            {!transcript ? <div className="transcript-empty"><Icon name="transcript" className="h-7 w-7" /><div><strong>{detail.asset.state === "needs_review" ? "Human review required" : "Transcript not produced yet"}</strong><p>{detail.asset.error_message || "The provider-neutral transcription stage will preserve raw output, normalized text, language, confidence, and timestamp mappings when configured."}</p></div></div> : (
              <>
                <div className="transcript-summary"><div><span>Provider</span><strong>{transcript.transcript.provider} / {transcript.transcript.model}</strong></div><div><span>Language</span><strong>{transcript.transcript.language || "undetermined"}</strong></div><div><span>Diarization</span><strong>{transcript.transcript.diarization_state}</strong></div><div><span>Timestamp detail</span><strong>{transcript.transcript.word_timestamps_available ? "segment + word" : "segment"}</strong></div></div>
                {transcript.transcript.summary && <blockquote className="summary-quote">{transcript.transcript.summary}</blockquote>}
                <div className="topic-row">{transcript.transcript.key_topics.map((topic) => <span key={topic}>{topic}</span>)}</div>
                <div className="transcript-tools"><div className="filter-row">{(["timeline", "normalized", "original", "chunks"] as const).map((item) => <button type="button" key={item} className={mode === item ? "filter-active" : ""} onClick={() => setMode(item)}>{item}</button>)}</div>{mode === "timeline" && <label><Icon name="search" className="h-4 w-4" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter loaded segments" /></label>}</div>
                {mode === "timeline" && <div className="timeline-list">{visibleSegments.map((segment) => <article key={segment.id}><time>{formatTime(segment.start_ms)} — {formatTime(segment.end_ms)}</time><div><p>{segment.text}</p><small>{segment.speaker_label || "speaker unknown"}{segment.confidence === null ? "" : ` · ${Math.round(segment.confidence * 100)}% confidence`}</small></div></article>)}{visibleSegments.length === 0 && <p className="evidence-pending">No loaded segment matches this filter.</p>}{transcript.segments.length < transcript.transcript.segment_count && <button className="load-more" type="button" onClick={() => void loadMore()}>Load next 200 segments</button>}</div>}
                {mode === "normalized" && <pre className="transcript-text">{transcript.normalized_text}</pre>}
                {mode === "original" && <div><p className="raw-note">Original provider text is preserved verbatim below. Raw provider JSON remains private server-side and is not exposed by this endpoint.</p><pre className="transcript-text">{transcript.original_text}</pre></div>}
                {mode === "chunks" && <div className="chunk-list">{transcript.chunks.map((chunk) => <article key={chunk.id}><header><span>CHUNK {String(chunk.ordinal + 1).padStart(2, "0")}</span><time>{formatTime(chunk.start_ms)} — {formatTime(chunk.end_ms)}</time></header><p>{chunk.text}</p><footer>segments {chunk.start_segment_ordinal}–{chunk.end_segment_ordinal} · chars {chunk.char_start}–{chunk.char_end} · {chunk.algorithm_version}</footer></article>)}{transcript.chunks.length < transcript.chunk_count && <button className="load-more" type="button" onClick={() => void loadMoreChunks()}>Load next 500 chunks</button>}</div>}
              </>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
