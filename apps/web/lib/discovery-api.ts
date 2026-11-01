export type CandidateState =
  | "proposed"
  | "needs_review"
  | "approved"
  | "rejected"
  | "superseded";

export type DiscoveryStrategy = "deterministic" | "hybrid" | "model_required";
export type DiscoveryRunState = "running" | "succeeded" | "failed" | "needs_review";

export interface DiscoveryRun {
  id: string;
  tenant_id: string;
  asset_id: string;
  transcript_id: string;
  requested_by_user_id: string;
  state: DiscoveryRunState;
  strategy: DiscoveryStrategy;
  input_hash: string;
  algorithm_version: string;
  score_version: string;
  configuration: Record<string, unknown>;
  provider: string | null;
  model: string | null;
  provider_mode: "real_tested" | "mocked" | "disabled" | "blocked" | "unverified";
  model_verified: boolean;
  model_request_id: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost: string;
  latency_ms: number | null;
  candidate_count: number;
  failure_category: string | null;
  failure_message: string | null;
  started_at: string;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Candidate {
  id: string;
  tenant_id: string;
  asset_id: string;
  transcript_id: string;
  discovery_run_id: string;
  parent_candidate_id: string | null;
  state: CandidateState;
  rank: number;
  start_ms: number;
  end_ms: number;
  speech_start_ms: number;
  speech_end_ms: number;
  start_segment_ordinal: number;
  end_segment_ordinal: number;
  transcript_excerpt: string;
  language: string | null;
  transcript_confidence: number | null;
  recommendation_confidence: number;
  overall_score: number;
  rationale: string;
  topics: string[];
  risk_flags: string[];
  model_provider: string | null;
  model: string | null;
  model_request_id: string | null;
  model_verified: boolean;
  word_timestamps_available: boolean;
  algorithm_version: string;
  score_version: string;
  boundary_revision: number;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScoreComponent {
  key: string;
  label: string;
  meaning: string;
  score: number | null;
  weight: number;
  weighted_score: number;
  applicable: boolean;
  evidence: string[];
  missing_behavior: string;
  algorithm_version: string;
}

export interface CandidateReview {
  id: string;
  candidate_id: string;
  previous_candidate_id: string | null;
  reviewer_user_id: string;
  action: "approve" | "reject" | "boundary_edit";
  from_state: CandidateState;
  to_state: CandidateState;
  reason: string;
  previous_bounds: Record<string, unknown> | null;
  new_bounds: Record<string, unknown> | null;
  created_at: string;
}

export interface CandidateDetail {
  candidate: Candidate;
  scores: ScoreComponent[];
  reviews: CandidateReview[];
}

export interface ContextSegment {
  ordinal: number;
  start_ms: number;
  end_ms: number;
  text: string;
  normalized_text: string;
  confidence: number | null;
  speaker_label: string | null;
  selected: boolean;
}

export interface CandidateContext {
  candidate_id: string;
  asset_id: string;
  duration_ms: number;
  context_start_ms: number;
  context_end_ms: number;
  segments: ContextSegment[];
}

export interface DiscoveryRunList {
  items: DiscoveryRun[];
  total: number;
  offset: number;
  limit: number;
}

export interface CandidateList {
  items: Candidate[];
  total: number;
  offset: number;
  limit: number;
}

export interface DiscoveryResult {
  run: DiscoveryRun;
  candidates: Candidate[];
  idempotent: boolean;
}

export interface DiscoveryRequestBody {
  strategy: DiscoveryStrategy;
  target_profile: string;
  min_duration_seconds: number;
  target_duration_seconds: number;
  max_duration_seconds: number;
  padding_seconds: number;
  max_candidates: number;
  audience_hint?: string;
  topic_hints: string[];
}

export function formatScore(value: number): string {
  return Number.isFinite(value) ? value.toFixed(1) : "—";
}

export function formatPercent(value: number | null): string {
  return value === null ? "Unavailable" : `${Math.round(value * 100)}%`;
}

export function stateLabel(value: string): string {
  return value.replaceAll("_", " ");
}
