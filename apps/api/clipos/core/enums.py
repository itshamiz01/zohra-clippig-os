from enum import StrEnum


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    DISABLED = "disabled"


class Role(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    ADMIN = "admin"
    OWNER = "owner"


ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 10,
    Role.EDITOR: 20,
    Role.REVIEWER: 30,
    Role.ADMIN: 40,
    Role.OWNER: 50,
}


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ActionClass(StrEnum):
    PUBLIC_PUBLISH = "public_publish"
    EXTERNAL_OUTREACH = "external_outreach"
    CONTRACT = "contract"
    PAYMENT = "payment"
    DESTRUCTIVE_CHANGE = "destructive_change"
    PERMISSION_CHANGE = "permission_change"
    AUTONOMY_CHANGE = "autonomy_change"
    INTERNAL_REVIEW = "internal_review"


class ProviderMode(StrEnum):
    REAL_TESTED = "real_tested"
    MOCKED = "mocked"
    DISABLED = "disabled"
    BLOCKED = "blocked"
    UNVERIFIED = "unverified"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


class SystemScope(StrEnum):
    GLOBAL = "global"
    TENANT = "tenant"


class AssetState(StrEnum):
    RECEIVED = "received"
    VALIDATING = "validating"
    QUEUED = "queued"
    EXTRACTING_METADATA = "extracting_metadata"
    TRANSCRIBING = "transcribing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class AssetSource(StrEnum):
    LOCAL_UPLOAD = "local_upload"
    URL = "url"


class MediaKind(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"
    UNKNOWN = "unknown"


class TranscriptState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class DiarizationState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_REQUESTED = "not_requested"


class CandidateState(StrEnum):
    PROPOSED = "proposed"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class DiscoveryRunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class DiscoveryStrategy(StrEnum):
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"
    MODEL_REQUIRED = "model_required"


class CandidateReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    BOUNDARY_EDIT = "boundary_edit"


class RenderState(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    RENDERING = "rendering"
    VALIDATING_OUTPUT = "validating_output"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class RenderCropStrategy(StrEnum):
    CENTER_CROP = "center_crop"
    FIT_PAD = "fit_pad"
    SMART_FACE_FALLBACK = "smart_face_fallback"


class CaptionMode(StrEnum):
    NONE = "none"
    SIDECAR = "sidecar"
    BURN_IN = "burn_in"
    BOTH = "both"


class AudioNormalizationMode(StrEnum):
    NONE = "none"
    SPEECH_LOUDNESS = "speech_loudness"


class RenderDefectCategory(StrEnum):
    CAPTIONS = "captions"
    CROP = "crop"
    AUDIO = "audio"
    VISUAL = "visual"
    TIMING = "timing"
    BRANDING = "branding"
    TECHNICAL = "technical"
    MEANING_OR_BOUNDARY = "meaning_or_boundary"
    OTHER = "other"


class DestinationPlatform(StrEnum):
    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"
    OTHER = "other"


class RightsScope(StrEnum):
    ASSET = "asset"
    CANDIDATE = "candidate"
    RENDER = "render"


class RightsBasis(StrEnum):
    OWNED = "owned"
    LICENSED = "licensed"
    WRITTEN_PERMISSION = "written_permission"
    CONTRACT = "contract"
    PUBLIC_DOMAIN = "public_domain"
    UNKNOWN = "unknown"


class RightsEvidenceStatus(StrEnum):
    VALID = "valid"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class MaterialClearance(StrEnum):
    NOT_PRESENT = "not_present"
    CLEARED = "cleared"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"


class QualityCheckKind(StrEnum):
    TECHNICAL_INTEGRITY = "technical_integrity"
    TRANSCRIPT_AUDIO_CONSISTENCY = "transcript_audio_consistency"
    CAPTION_QUALITY = "caption_quality"
    CONTEXT_INTEGRITY = "context_integrity"
    VISUAL_COMPOSITION = "visual_composition"
    AUDIO_QUALITY = "audio_quality"
    DUPLICATION_CAMPAIGN_CONSISTENCY = "duplication_campaign_consistency"
    DESTINATION_CONSTRAINTS = "destination_constraints"


class QualityCheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    UNAVAILABLE = "unavailable"


class QualitySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class EvidenceSource(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL = "model"
    HUMAN = "human"


class GateOutcome(StrEnum):
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class GateReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_REVISION = "request_revision"


class GateCheckpoint(StrEnum):
    PUBLISH_JOB_ACCEPTANCE = "publish_job_acceptance"
    EXTERNAL_UPLOAD = "external_upload"


class PublishingProvider(StrEnum):
    YOUTUBE = "youtube"
    META = "meta"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"


class OAuthConnectionState(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    EXPIRED = "expired"
    RECONNECT_REQUIRED = "reconnect_required"
    REVOKED = "revoked"
    ERROR = "error"


class PublishVisibility(StrEnum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class PublishState(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    CANCELLED = "cancelled"


class PublishEventKind(StrEnum):
    CREATED = "created"
    VALIDATION_STARTED = "validation_started"
    GATE_VERIFIED = "gate_verified"
    TOKEN_REFRESHED = "token_refreshed"  # noqa: S105 - event name, not a credential
    UPLOAD_INITIATED = "upload_initiated"
    UPLOAD_PROGRESS = "upload_progress"
    PROVIDER_PROCESSING = "provider_processing"
    RECONCILIATION = "reconciliation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    RETRY_QUEUED = "retry_queued"


class MetricAvailability(StrEnum):
    """Truth state of one collected metric.

    ``value`` is only meaningful for REPORTED and STALE. MISSING means the
    provider does not expose the metric for this publication, UNAVAILABLE
    means collection could not obtain it, and PERMISSION_DENIED means the
    connected account lacks the analytics scope. A metric that was not
    reported must never be persisted as an invented zero.
    """

    REPORTED = "reported"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    PERMISSION_DENIED = "permission_denied"
    STALE = "stale"


class AnalyticsCollectionState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


class ExperimentState(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RecommendationKind(StrEnum):
    HOOK = "hook"
    DURATION = "duration"
    CAPTIONS = "captions"
    POSTING_TIME = "posting_time"
    FORMAT = "format"


class RecommendationState(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


class OpportunitySourceKind(StrEnum):
    SEED_LIST = "seed_list"
    RSS_FEED = "rss_feed"
    PUBLIC_SITE = "public_site"
    SEARCH_PROVIDER = "search_provider"


class OpportunitySourceStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class OpportunitySyncState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"


class OpportunityState(StrEnum):
    NEW = "new"
    REVIEWING = "reviewing"
    SHORTLISTED = "shortlisted"
    REJECTED = "rejected"
    SUPPRESSED = "suppressed"


class SuppressionMatchType(StrEnum):
    CREATOR_HANDLE = "creator_handle"
    DOMAIN = "domain"
    KEYWORD = "keyword"


class OpportunityHandoffState(StrEnum):
    DRAFT = "draft"
    READY = "ready"
