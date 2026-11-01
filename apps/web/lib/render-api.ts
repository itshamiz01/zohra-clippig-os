export type RenderState =
  | "queued"
  | "validating"
  | "rendering"
  | "validating_output"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "needs_review";

export type RenderPreset =
  | "tiktok_vertical"
  | "instagram_reels"
  | "youtube_shorts"
  | "vertical_1080p"
  | "vertical_720p"
  | "square_1080p"
  | "landscape_1080p"
  | "custom";

export interface RenderJob {
  id: string;
  tenant_id: string;
  render_plan_id: string;
  background_job_id: string;
  requested_by_user_id: string;
  state: RenderState;
  progress_percent: number;
  stage: string;
  cancellation_requested: boolean;
  attempt_count: number;
  max_attempts: number;
  renderer_version: string;
  elapsed_ms: number | null;
  failure_category: string | null;
  failure_message: string | null;
  failure_details: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RenderPlan {
  id: string;
  tenant_id: string;
  candidate_id: string;
  asset_id: string;
  transcript_id: string;
  requested_by_user_id: string;
  plan_version: string;
  renderer_version: string;
  idempotency_key: string;
  request_hash: string;
  preset: string;
  source_start_ms: number;
  source_end_ms: number;
  duration_ms: number;
  source_boundary_revision: number;
  aspect_ratio: string;
  width: number;
  height: number;
  frame_rate: number;
  crop_strategy_requested: string;
  crop_strategy_applied: string;
  smart_crop_used: boolean;
  crop_fallback_reason: string | null;
  caption_mode: string;
  caption_style: Record<string, unknown>;
  safe_area: Record<string, unknown>;
  overlays: Array<Record<string, unknown>>;
  branding_snapshot: Record<string, unknown>;
  audio_settings: Record<string, unknown>;
  video_codec: string;
  audio_codec: string;
  container: string;
  output_storage_prefix: string;
  source_time_mapping: Record<string, unknown>;
  provenance: Record<string, unknown>;
  limitations: string[];
  created_at: string;
  updated_at: string;
}

export interface RenderCaption {
  id: string;
  ordinal: number;
  source_start_ms: number;
  source_end_ms: number;
  output_start_ms: number;
  output_end_ms: number;
  text: string;
  line_count: number;
  character_count: number;
  timing_source: string;
}

export interface RenderOutput {
  id: string;
  render_job_id: string;
  render_plan_id: string;
  attempt_number: number;
  is_current: boolean;
  storage_key: string;
  checksum_sha256: string;
  size_bytes: number;
  content_type: string;
  sidecar_storage_key: string | null;
  sidecar_checksum_sha256: string | null;
  format_name: string;
  duration_seconds: number;
  video_codec: string;
  audio_codec: string;
  width: number;
  height: number;
  frame_rate: number;
  audio_channels: number | null;
  audio_sample_rate: number | null;
  probe_version: string;
  validation: Record<string, unknown>;
  provenance: Record<string, unknown>;
  renderer_version: string;
  created_at: string;
}

export interface RenderDefect {
  id: string;
  render_job_id: string;
  render_output_id: string;
  reported_by_user_id: string;
  category: string;
  description: string;
  meaning_or_boundary_changed: boolean;
  requested_resolution: string | null;
  created_at: string;
}

export interface RenderDetail {
  plan: RenderPlan;
  captions: RenderCaption[];
  render: RenderJob;
  outputs: RenderOutput[];
  defects: RenderDefect[];
}

export interface RenderListItem {
  render: RenderJob;
  plan: RenderPlan;
  current_output: RenderOutput | null;
}

export interface RenderListResponse {
  items: RenderListItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface RenderCapabilities {
  enabled: boolean;
  ffmpeg_available: boolean;
  render_ready: boolean;
  ffmpeg_version: string | null;
  ffprobe_version: string | null;
  capabilities: string[];
  presets: Array<Record<string, unknown>>;
  real_features: string[];
  conditional_features: string[];
  unavailable_features: string[];
  limitations: string[];
}

export interface RenderCreateResponse {
  plan: RenderPlan;
  captions: RenderCaption[];
  render: RenderJob;
  idempotent: boolean;
}

export interface TenantRenderProfile {
  id: string | null;
  tenant_id: string;
  updated_by_user_id: string | null;
  branding_enabled: boolean;
  branding_text: string | null;
  branding_position: string;
  branding_opacity: number;
  branding_font_size: number;
  created_at: string | null;
  updated_at: string | null;
  logo_overlay_supported: boolean;
}

export const ACTIVE_RENDER_STATES: RenderState[] = [
  "queued",
  "validating",
  "rendering",
  "validating_output"
];

export function renderStateLabel(state: string): string {
  return state.replaceAll("_", " ");
}
