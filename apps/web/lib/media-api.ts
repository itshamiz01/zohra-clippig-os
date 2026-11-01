export const TOKEN_KEY = "clipos.session.token";

export type AssetState =
  | "received"
  | "validating"
  | "queued"
  | "extracting_metadata"
  | "transcribing"
  | "ready"
  | "failed"
  | "cancelled"
  | "needs_review";

export interface Asset {
  id: string;
  submitted_by_user_id: string;
  processing_job_id: string | null;
  source: "local_upload" | "url";
  state: AssetState;
  media_kind: "audio" | "video" | "unknown";
  original_filename: string;
  safe_extension: string;
  declared_content_type: string | null;
  detected_content_type: string;
  size_bytes: number;
  checksum_sha256: string;
  permission_confirmed: boolean;
  rights_note: string;
  acquired_at: string;
  progress_percent: number;
  processing_stage: string;
  metadata_verified: boolean;
  cancellation_requested: boolean;
  error_category: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface MediaMetadata {
  format_name: string;
  duration_seconds: number;
  bit_rate: number | null;
  has_video: boolean;
  has_audio: boolean;
  video_codec: string | null;
  audio_codec: string | null;
  width: number | null;
  height: number | null;
  frame_rate: number | null;
  audio_channels: number | null;
  audio_sample_rate: number | null;
  probe_version: string;
  verified_at: string;
}

export interface TranscriptSummary {
  id: string;
  state: "pending" | "processing" | "ready" | "failed" | "needs_review";
  provider: string;
  model: string;
  language: string | null;
  confidence: number | null;
  diarization_state: "available" | "unavailable" | "not_requested";
  summary: string | null;
  key_topics: string[];
  word_timestamps_available: boolean;
  segment_count: number;
  word_count: number;
  algorithm_version: string;
  created_at: string;
  updated_at: string;
}

export interface AssetDetail {
  asset: Asset;
  metadata: MediaMetadata | null;
  transcript: TranscriptSummary | null;
}

export interface TranscriptSegment {
  id: string;
  ordinal: number;
  start_ms: number;
  end_ms: number;
  text: string;
  normalized_text: string;
  confidence: number | null;
  speaker_label: string | null;
}

export interface TranscriptWord {
  id: string;
  segment_id: string | null;
  ordinal: number;
  start_ms: number;
  end_ms: number;
  text: string;
  confidence: number | null;
  speaker_label: string | null;
}

export interface TranscriptChunk {
  id: string;
  ordinal: number;
  start_segment_ordinal: number;
  end_segment_ordinal: number;
  start_ms: number;
  end_ms: number;
  char_start: number;
  char_end: number;
  text: string;
  summary: string | null;
  topics: string[];
  algorithm_version: string;
}

export interface TranscriptDetail {
  transcript: TranscriptSummary;
  original_text: string;
  normalized_text: string;
  segments: TranscriptSegment[];
  words: TranscriptWord[];
  chunks: TranscriptChunk[];
  segment_offset: number;
  segment_limit: number;
  word_offset: number;
  word_limit: number;
  chunk_offset: number;
  chunk_limit: number;
  chunk_count: number;
}

export interface APIErrorBody {
  error?: { code?: string; message?: string };
  detail?: string;
}

export class APIError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string
  ) {
    super(message);
  }
}

export function readSessionToken(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(TOKEN_KEY) || "";
}

export function storeSessionToken(token: string): void {
  if (typeof window === "undefined") return;
  if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
  else window.sessionStorage.removeItem(TOKEN_KEY);
}

export async function apiRequest<T>(
  path: string,
  token: string,
  init: RequestInit = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers, cache: "no-store" });
  if (!response.ok) {
    let body: APIErrorBody = {};
    try {
      body = (await response.json()) as APIErrorBody;
    } catch {
      // The server may reject a request before producing JSON.
    }
    throw new APIError(
      body.error?.message || body.detail || `Request failed with status ${response.status}`,
      response.status,
      body.error?.code || "request_failed"
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${units[index]}`;
}

export function formatTime(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const prefix = hours ? `${hours}:` : "";
  return `${prefix}${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function isActiveState(state: AssetState): boolean {
  return ["received", "validating", "queued", "extracting_metadata", "transcribing"].includes(state);
}
