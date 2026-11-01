export type Destination =
  | "youtube_shorts"
  | "instagram_reels"
  | "tiktok"
  | "linkedin"
  | "other";

export type GateOutcome = "needs_human_review" | "approved" | "rejected" | "blocked";
export type CheckStatus = "passed" | "failed" | "needs_review" | "unavailable";

export interface QualityEvaluationSummary {
  id: string;
  render_job_id: string;
  render_output_id: string;
  candidate_id: string;
  asset_id: string;
  rights_evidence_id: string | null;
  requested_by_user_id: string;
  destination: Destination;
  territory: string;
  campaign_reference: string | null;
  outcome: GateOutcome;
  automated_outcome: GateOutcome;
  evaluation_version: string;
  policy_version: string;
  evaluated_at: string;
  created_at: string;
  updated_at: string;
}

export interface QualityCheck {
  id: string;
  evaluation_id: string;
  kind: string;
  status: CheckStatus;
  severity: "info" | "warning" | "error" | "critical";
  evidence_source: "deterministic" | "model" | "human";
  evidence: Record<string, unknown>;
  confidence: number;
  check_version: string;
  remediation: string;
  hard_block: boolean;
  created_at: string;
}

export interface RightsEvidence {
  id: string;
  asset_id: string;
  candidate_id: string | null;
  render_job_id: string | null;
  recorded_by_user_id: string;
  revoked_by_user_id: string | null;
  scope: "asset" | "candidate" | "render";
  basis: string;
  status: "valid" | "needs_review" | "rejected";
  source_owner: string;
  evidence_type: string;
  evidence_reference: string;
  evidence_checksum_sha256: string | null;
  permission_recorded_at: string;
  effective_from: string;
  expires_at: string | null;
  no_expiration_asserted: boolean;
  allowed_platforms: string[];
  allowed_territories: string[];
  commercial_use_allowed: boolean;
  editing_allowed: boolean;
  ai_processing_allowed: boolean;
  attribution_required: boolean;
  attribution_text: string | null;
  license_terms: string;
  restrictions: string;
  third_party_material: Record<string, string>;
  campaign_reference: string | null;
  evidence_version: string;
  revoked_at: string | null;
  revocation_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewDecision {
  id: string;
  evaluation_id: string;
  reviewer_user_id: string;
  action: "approve" | "reject" | "request_revision";
  decision_source: "human" | "model" | "deterministic";
  from_outcome: GateOutcome;
  to_outcome: GateOutcome;
  reason: string;
  acknowledged_check_ids: string[];
  created_at: string;
}

export interface GateVerification {
  id: string;
  evaluation_id: string;
  checkpoint: "publish_job_acceptance" | "external_upload";
  accepted: boolean;
  observed_input_hash: string;
  failure_reasons: string[];
  evidence_payload: Record<string, unknown>;
  evidence_signature: string;
  verified_at: string;
  valid_until: string;
}

export interface QualityEvaluationDetail {
  evaluation: QualityEvaluationSummary;
  input_hash: string;
  evidence_hash: string;
  model_assistance_used: boolean;
  policy_explanation: string[];
  missing_evidence: string[];
  remediation: string[];
  hard_block_reasons: string[];
  approval_evidence: Record<string, unknown> | null;
  approval_signature: string | null;
  approval_expires_at: string | null;
  checks: QualityCheck[];
  rights: RightsEvidence | null;
  rights_state: string;
  decisions: ReviewDecision[];
  verifications: GateVerification[];
  stale: boolean;
  stale_reasons: string[];
  legal_clearance: false;
  privacy_note: string;
  legal_note: string;
}

export interface QualityEvaluationList {
  items: QualityEvaluationSummary[];
  total: number;
  offset: number;
  limit: number;
}

export const DESTINATIONS: Array<{ value: Destination; label: string }> = [
  { value: "youtube_shorts", label: "YouTube Shorts" },
  { value: "instagram_reels", label: "Instagram Reels" },
  { value: "tiktok", label: "TikTok" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "other", label: "Other / manual profile" }
];

export function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
