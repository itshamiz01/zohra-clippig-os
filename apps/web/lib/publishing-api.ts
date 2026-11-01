export type PublishingProvider = "youtube" | "meta" | "tiktok" | "linkedin";
export type ProviderMode = "real_tested" | "mocked" | "disabled" | "blocked" | "unavailable" | "config_dependent" | "runtime";
export type Visibility = "private" | "unlisted" | "public";
export type PublicationState =
  | "queued"
  | "validating"
  | "uploading"
  | "processing"
  | "succeeded"
  | "failed"
  | "needs_reconciliation"
  | "cancelled";

export interface CapabilityFeatures {
  oauth: boolean;
  resumable_upload: boolean;
  processing_status: boolean;
  privacy_status: boolean;
  metadata: boolean;
  thumbnails: boolean;
  cancellation: boolean;
  reconciliation: boolean;
  analytics_handoff: boolean;
  supported_visibilities: Visibility[];
}

export interface PublishingCapability {
  provider: PublishingProvider;
  display_name: string;
  mode: ProviderMode;
  configured: boolean;
  real_oauth_verified: boolean;
  real_upload_verified: boolean;
  required_scopes: string[];
  required_configuration: string[];
  features: CapabilityFeatures;
  limitations: string[];
  evidence_note: string;
}

export interface OAuthStart {
  provider: PublishingProvider;
  authorization_url: string;
  redirect_uri: string;
  required_scopes: string[];
  expires_at: string;
  state_is_one_time: boolean;
  pkce_enabled: boolean;
}

export interface PublishingConnection {
  id: string;
  tenant_id: string;
  user_id: string;
  provider: PublishingProvider;
  state: "connected" | "expired" | "reconnect_required" | "revoked" | "error";
  provider_destination_id: string;
  destination_display_name: string;
  granted_scopes: string[];
  access_token_expires_at: string;
  has_refresh_token: boolean;
  last_refreshed_at: string | null;
  last_validated_at: string | null;
  revoked_at: string | null;
  reconnect_required_at: string | null;
  last_error_category: string | null;
  last_error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface Publication {
  id: string;
  tenant_id: string;
  requested_by_user_id: string;
  connection_id: string;
  render_job_id: string;
  render_output_id: string;
  quality_evaluation_id: string;
  background_job_id: string;
  public_approval_id: string | null;
  acceptance_gate_verification_id: string;
  upload_gate_verification_id: string | null;
  provider: PublishingProvider;
  provider_destination_id: string;
  idempotency_key: string;
  state: PublicationState;
  visibility: Visibility;
  title: string;
  description: string;
  tags: string[];
  made_for_kids: boolean;
  category_id: string;
  territory: string;
  campaign_reference: string | null;
  progress_percent: number;
  stage: string;
  content_type: string;
  content_length: number;
  committed_bytes: number;
  provider_publication_id: string | null;
  provider_url: string | null;
  provider_processing_status: string | null;
  sanitized_provider_response: Record<string, unknown>;
  analytics_handoff: Record<string, unknown> | null;
  cancellation_requested: boolean;
  attempt_count: number;
  max_attempts: number;
  retryable: boolean;
  uncertain_remote_result: boolean;
  failure_category: string | null;
  failure_message: string | null;
  manual_next_step: string | null;
  retry_after_seconds: number | null;
  started_at: string | null;
  upload_started_at: string | null;
  provider_created_at: string | null;
  finished_at: string | null;
  last_reconciled_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PublicationList {
  items: Publication[];
  total: number;
  offset: number;
  limit: number;
}

export interface PublicationEvent {
  id: string;
  publication_id: string;
  sequence: number;
  kind: string;
  from_state: PublicationState | null;
  to_state: PublicationState;
  progress_percent: number;
  stage: string;
  message: string;
  event_data: Record<string, unknown>;
  created_at: string;
}

export interface PublicApproval {
  approval: {
    id: string;
    status: "pending" | "approved" | "rejected" | "expired";
    expires_at: string | null;
  };
  payload_hash: string;
  binding_note: string;
}

export function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

export function freshPublicationKey(): string {
  const value = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `publish-${value}`;
}
