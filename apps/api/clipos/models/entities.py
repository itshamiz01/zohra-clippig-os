from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clipos.core.enums import (
    ActionClass,
    AgentDefinitionStatus,
    AgentRole,
    AgentRunState,
    AnalyticsCollectionState,
    ApprovalStatus,
    AssetSource,
    AssetState,
    AuditOutcome,
    AutonomyClass,
    CampaignAssessmentState,
    CampaignRightsBasis,
    CampaignSourceKind,
    CampaignStatus,
    CandidateReviewAction,
    CandidateState,
    CrmStage,
    CrmTaskState,
    DestinationPlatform,
    DiarizationState,
    DiscoveryRunState,
    DiscoveryStrategy,
    EvidenceSource,
    ExecutiveActionKind,
    ExecutiveActionState,
    ExecutiveControlMode,
    ExecutivePlanState,
    ExecutiveProposalKind,
    ExecutiveProposalState,
    ExperimentState,
    GateCheckpoint,
    GateOutcome,
    GateReviewAction,
    JobState,
    MediaKind,
    MembershipStatus,
    MetricAvailability,
    OAuthConnectionState,
    OnboardingState,
    OpportunityHandoffState,
    OpportunitySourceKind,
    OpportunitySourceStatus,
    OpportunityState,
    OpportunitySyncState,
    ProviderMode,
    PublishEventKind,
    PublishingProvider,
    PublishState,
    PublishVisibility,
    QualityCheckKind,
    QualityCheckStatus,
    QualitySeverity,
    RecommendationKind,
    RecommendationState,
    RenderState,
    RevenueKind,
    RevenueState,
    RightsBasis,
    RightsEvidenceStatus,
    RightsScope,
    Role,
    SalesAssetKind,
    SalesAssetState,
    SuppressionMatchType,
    TenantStatus,
    TranscriptState,
)
from clipos.database import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        Enum(TenantStatus, name="tenant_status"), default=TenantStatus.ACTIVE, nullable=False
    )

    memberships: Mapped[list[TenantMembership]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    memberships: Mapped[list[TenantMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class TenantMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        Index("ix_memberships_tenant_role", "tenant_id", "role"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), nullable=False)
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status"),
        default=MembershipStatus.ACTIVE,
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )

    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    outcome: Mapped[AuditOutcome] = mapped_column(
        Enum(AuditOutcome, name="audit_outcome"), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approvals_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "decided_by_user_id IS NULL OR decided_by_user_id != requested_by_user_id",
            name="different_decider",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    action_class: Mapped[ActionClass] = mapped_column(
        Enum(ActionClass, name="action_class"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status"),
        default=ApprovalStatus.PENDING,
        nullable=False,
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BackgroundJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "kind", "idempotency_key", name="uq_job_tenant_kind_idempotency"
        ),
        Index("ix_jobs_tenant_state_created", "tenant_id", "state", "created_at"),
        Index("ix_jobs_kind_state_heartbeat", "kind", "state", "heartbeat_at"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[JobState] = mapped_column(
        Enum(JobState, name="job_state"), default=JobState.QUEUED, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Asset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "checksum_sha256", name="uq_asset_tenant_checksum"),
        Index("ix_assets_tenant_state_created", "tenant_id", "state", "created_at"),
        Index("ix_assets_tenant_filename", "tenant_id", "original_filename"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="progress_range",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    processing_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[AssetSource] = mapped_column(
        Enum(AssetSource, name="asset_source"), nullable=False
    )
    state: Mapped[AssetState] = mapped_column(
        Enum(AssetState, name="asset_state"), default=AssetState.RECEIVED, nullable=False
    )
    media_kind: Mapped[MediaKind] = mapped_column(
        Enum(MediaKind, name="media_kind"), default=MediaKind.UNKNOWN, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    safe_extension: Mapped[str] = mapped_column(String(16), nullable=False)
    declared_content_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    detected_content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(700), nullable=False)
    derived_audio_storage_key: Mapped[str | None] = mapped_column(String(700), nullable=True)
    derived_audio_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permission_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rights_note: Mapped[str] = mapped_column(Text, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processing_stage: Mapped[str] = mapped_column(String(80), default="received", nullable=False)
    metadata_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class MediaMetadata(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_metadata"
    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_media_metadata_asset"),
        Index("ix_media_metadata_tenant_asset", "tenant_id", "asset_id"),
        CheckConstraint("duration_seconds >= 0", name="duration_nonnegative"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    format_name: Mapped[str] = mapped_column(String(160), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    bit_rate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    has_video: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_audio: Mapped[bool] = mapped_column(Boolean, nullable=False)
    video_codec: Mapped[str | None] = mapped_column(String(80), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(80), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_version: Mapped[str] = mapped_column(String(160), nullable=False)
    probe_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Transcript(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_transcript_asset"),
        Index("ix_transcript_tenant_state", "tenant_id", "state"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[TranscriptState] = mapped_column(
        Enum(TranscriptState, name="transcript_state"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    language: Mapped[str | None] = mapped_column(String(35), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    diarization_state: Mapped[DiarizationState] = mapped_column(
        Enum(DiarizationState, name="diarization_state"), nullable=False
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_topics: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    raw_provider_output: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    word_timestamps_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(80), nullable=False)


class TranscriptSegment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_id", "ordinal", name="uq_segment_transcript_ordinal"),
        Index("ix_segment_tenant_transcript", "tenant_id", "transcript_id", "ordinal"),
        CheckConstraint("start_ms >= 0", name="start_nonnegative"),
        CheckConstraint("end_ms > start_ms", name="time_order"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    transcript_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    speaker_label: Mapped[str | None] = mapped_column(String(100), nullable=True)


class TranscriptWord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "transcript_words"
    __table_args__ = (
        UniqueConstraint("transcript_id", "ordinal", name="uq_word_transcript_ordinal"),
        Index("ix_word_tenant_transcript", "tenant_id", "transcript_id", "ordinal"),
        CheckConstraint("start_ms >= 0", name="start_nonnegative"),
        CheckConstraint("end_ms >= start_ms", name="time_order"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    transcript_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    segment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("transcript_segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    speaker_label: Mapped[str | None] = mapped_column(String(100), nullable=True)


class TranscriptChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "transcript_chunks"
    __table_args__ = (
        UniqueConstraint("transcript_id", "ordinal", name="uq_chunk_transcript_ordinal"),
        Index("ix_chunk_tenant_transcript", "tenant_id", "transcript_id", "ordinal"),
        CheckConstraint("start_ms >= 0", name="start_nonnegative"),
        CheckConstraint("end_ms > start_ms", name="time_order"),
        CheckConstraint("char_start >= 0", name="char_start_nonnegative"),
        CheckConstraint("char_end >= char_start", name="char_order"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    transcript_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_segment_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    end_segment_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    topics: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(80), nullable=False)


class DiscoveryRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "discovery_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "asset_id", "input_hash", name="uq_discovery_tenant_asset_input"
        ),
        Index("ix_discovery_tenant_state_created", "tenant_id", "state", "created_at"),
        CheckConstraint("candidate_count >= 0", name="candidate_count_nonnegative"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[DiscoveryRunState] = mapped_column(
        Enum(DiscoveryRunState, name="discovery_run_state"), nullable=False
    )
    strategy: Mapped[DiscoveryStrategy] = mapped_column(
        Enum(DiscoveryStrategy, name="discovery_strategy"), nullable=False
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(80), nullable=False)
    score_version: Mapped[str] = mapped_column(String(80), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_mode: Mapped[ProviderMode] = mapped_column(
        Enum(ProviderMode, name="provider_mode"), nullable=False
    )
    model_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    model_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=8), default=Decimal("0"), nullable=False
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClipCandidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clip_candidates"
    __table_args__ = (
        UniqueConstraint("discovery_run_id", "rank", name="uq_candidate_run_rank"),
        Index("ix_candidates_tenant_asset_state", "tenant_id", "asset_id", "state"),
        Index("ix_candidates_tenant_score", "tenant_id", "overall_score"),
        CheckConstraint("start_ms >= 0", name="start_nonnegative"),
        CheckConstraint("end_ms > start_ms", name="time_order"),
        CheckConstraint("speech_start_ms >= start_ms", name="speech_start_in_window"),
        CheckConstraint("speech_end_ms <= end_ms", name="speech_end_in_window"),
        CheckConstraint("speech_end_ms > speech_start_ms", name="speech_time_order"),
        CheckConstraint("overall_score >= 0 AND overall_score <= 100", name="score_range"),
        CheckConstraint(
            "recommendation_confidence >= 0 AND recommendation_confidence <= 1",
            name="confidence_range",
        ),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint("boundary_revision >= 1", name="revision_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    discovery_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("discovery_runs.id", ondelete="CASCADE"), nullable=False
    )
    parent_candidate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[CandidateState] = mapped_column(
        Enum(CandidateState, name="candidate_state"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    speech_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    speech_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    start_segment_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    end_segment_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(35), nullable=True)
    transcript_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommendation_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    risk_flags: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    word_timestamps_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(80), nullable=False)
    score_version: Mapped[str] = mapped_column(String(80), nullable=False)
    boundary_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CandidateScoreComponent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_score_components"
    __table_args__ = (
        UniqueConstraint("candidate_id", "key", name="uq_score_candidate_key"),
        Index("ix_score_tenant_candidate", "tenant_id", "candidate_id"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score_range"),
        CheckConstraint("weight > 0", name="weight_positive"),
        CheckConstraint("weighted_score >= 0", name="weighted_nonnegative"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    meaning: Mapped[str] = mapped_column(String(500), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    weighted_score: Mapped[float] = mapped_column(Float, nullable=False)
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    missing_behavior: Mapped[str] = mapped_column(String(160), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(80), nullable=False)


class CandidateTopic(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_topics"
    __table_args__ = (
        UniqueConstraint("candidate_id", "normalized_topic", name="uq_candidate_topic_normalized"),
        Index("ix_candidate_topic_lookup", "tenant_id", "normalized_topic", "candidate_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_topic: Mapped[str] = mapped_column(String(80), nullable=False)


class CandidateReview(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_reviews"
    __table_args__ = (Index("ix_review_tenant_candidate", "tenant_id", "candidate_id"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="CASCADE"), nullable=False
    )
    previous_candidate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="SET NULL"), nullable=True
    )
    reviewer_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[CandidateReviewAction] = mapped_column(
        Enum(CandidateReviewAction, name="candidate_review_action"), nullable=False
    )
    from_state: Mapped[CandidateState] = mapped_column(
        Enum(CandidateState, name="candidate_state"), nullable=False
    )
    to_state: Mapped[CandidateState] = mapped_column(
        Enum(CandidateState, name="candidate_state"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    previous_bounds: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    new_bounds: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TenantRenderProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenant_render_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_render_profile_tenant"),
        CheckConstraint("branding_opacity >= 0.1 AND branding_opacity <= 1", name="opacity_range"),
        CheckConstraint(
            "branding_font_size >= 12 AND branding_font_size <= 144", name="font_size_range"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    updated_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    branding_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    branding_text: Mapped[str | None] = mapped_column(String(80), nullable=True)
    branding_position: Mapped[str] = mapped_column(String(32), default="top_right", nullable=False)
    branding_opacity: Mapped[float] = mapped_column(Float, default=0.85, nullable=False)
    branding_font_size: Mapped[int] = mapped_column(Integer, default=36, nullable=False)


class RenderPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "render_plans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_render_plan_tenant_idempotency"),
        Index(
            "ix_render_plans_tenant_candidate_created", "tenant_id", "candidate_id", "created_at"
        ),
        CheckConstraint("source_start_ms >= 0", name="source_start_nonnegative"),
        CheckConstraint("source_end_ms > source_start_ms", name="source_time_order"),
        CheckConstraint(
            "duration_ms = source_end_ms - source_start_ms", name="duration_matches_bounds"
        ),
        CheckConstraint("duration_ms > 0", name="duration_positive"),
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
        CheckConstraint("frame_rate > 0", name="frame_rate_positive"),
        CheckConstraint("source_boundary_revision >= 1", name="boundary_revision_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    transcript_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    plan_version: Mapped[str] = mapped_column(String(80), nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    preset: Mapped[str] = mapped_column(String(80), nullable=False)
    source_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_boundary_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(20), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_rate: Mapped[float] = mapped_column(Float, nullable=False)
    crop_strategy_requested: Mapped[str] = mapped_column(String(40), nullable=False)
    crop_strategy_applied: Mapped[str] = mapped_column(String(40), nullable=False)
    smart_crop_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    crop_fallback_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    caption_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    caption_style: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    safe_area: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    overlays: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    branding_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    audio_settings: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    video_codec: Mapped[str] = mapped_column(String(40), nullable=False)
    audio_codec: Mapped[str] = mapped_column(String(40), nullable=False)
    container: Mapped[str] = mapped_column(String(20), nullable=False)
    output_storage_prefix: Mapped[str] = mapped_column(String(700), nullable=False)
    source_time_mapping: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)


class RenderCaptionSegment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "render_caption_segments"
    __table_args__ = (
        UniqueConstraint("render_plan_id", "ordinal", name="uq_render_caption_plan_ordinal"),
        Index("ix_render_caption_tenant_plan", "tenant_id", "render_plan_id", "ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("source_start_ms >= 0", name="source_start_nonnegative"),
        CheckConstraint("source_end_ms > source_start_ms", name="source_time_order"),
        CheckConstraint("output_start_ms >= 0", name="output_start_nonnegative"),
        CheckConstraint("output_end_ms > output_start_ms", name="output_time_order"),
        CheckConstraint("line_count >= 1 AND line_count <= 2", name="line_count_range"),
        CheckConstraint("character_count > 0", name="character_count_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    render_plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_plans.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    output_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    output_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    timing_source: Mapped[str] = mapped_column(String(32), nullable=False)


class RenderJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "render_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "render_plan_id", name="uq_render_job_tenant_plan"),
        UniqueConstraint("background_job_id", name="uq_render_job_background_job"),
        Index("ix_render_jobs_tenant_state_created", "tenant_id", "state", "created_at"),
        CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name="progress_range"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("elapsed_ms IS NULL OR elapsed_ms >= 0", name="elapsed_nonnegative"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    render_plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_plans.id", ondelete="RESTRICT"), nullable=False
    )
    background_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("background_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[RenderState] = mapped_column(
        Enum(RenderState, name="render_state"), default=RenderState.QUEUED, nullable=False
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), default="queued", nullable=False)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    failure_details: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RenderOutput(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "render_outputs"
    __table_args__ = (
        UniqueConstraint("render_job_id", "attempt_number", name="uq_render_output_job_attempt"),
        UniqueConstraint("tenant_id", "storage_key", name="uq_render_output_tenant_storage"),
        Index("ix_render_outputs_tenant_job_current", "tenant_id", "render_job_id", "is_current"),
        CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
        CheckConstraint("frame_rate > 0", name="frame_rate_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    render_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_jobs.id", ondelete="CASCADE"), nullable=False
    )
    render_plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_plans.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(700), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    sidecar_storage_key: Mapped[str | None] = mapped_column(String(700), nullable=True)
    sidecar_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    format_name: Mapped[str] = mapped_column(String(160), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    video_codec: Mapped[str] = mapped_column(String(80), nullable=False)
    audio_codec: Mapped[str] = mapped_column(String(80), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_rate: Mapped[float] = mapped_column(Float, nullable=False)
    audio_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_version: Mapped[str] = mapped_column(String(160), nullable=False)
    probe_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    validation: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RenderDefect(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "render_defects"
    __table_args__ = (Index("ix_render_defects_tenant_job", "tenant_id", "render_job_id"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    render_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_jobs.id", ondelete="CASCADE"), nullable=False
    )
    render_output_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_outputs.id", ondelete="RESTRICT"), nullable=False
    )
    reported_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    meaning_or_boundary_changed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requested_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RightsEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rights_evidence"
    __table_args__ = (
        Index("ix_rights_tenant_asset_created", "tenant_id", "asset_id", "created_at"),
        Index("ix_rights_tenant_render", "tenant_id", "render_job_id"),
        CheckConstraint(
            "expires_at IS NOT NULL OR no_expiration_asserted = true",
            name="expiration_explicit",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > effective_from",
            name="expiration_after_effective",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="RESTRICT"), nullable=True
    )
    render_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_jobs.id", ondelete="RESTRICT"), nullable=True
    )
    recorded_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    scope: Mapped[RightsScope] = mapped_column(
        Enum(RightsScope, name="rights_scope"), nullable=False
    )
    basis: Mapped[RightsBasis] = mapped_column(
        Enum(RightsBasis, name="rights_basis"), nullable=False
    )
    status: Mapped[RightsEvidenceStatus] = mapped_column(
        Enum(RightsEvidenceStatus, name="rights_evidence_status"), nullable=False
    )
    source_owner: Mapped[str] = mapped_column(String(300), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permission_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    no_expiration_asserted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_platforms: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    allowed_territories: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    commercial_use_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    editing_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ai_processing_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attribution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    license_terms: Mapped[str] = mapped_column(Text, nullable=False)
    restrictions: Mapped[str] = mapped_column(Text, nullable=False)
    third_party_material: Mapped[dict[str, str]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    campaign_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    evidence_version: Mapped[str] = mapped_column(String(80), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class QualityEvaluation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "quality_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_quality_evaluation_tenant_idempotency"
        ),
        Index("ix_quality_tenant_outcome_created", "tenant_id", "outcome", "created_at"),
        Index("ix_quality_tenant_render_created", "tenant_id", "render_job_id", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    render_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    render_output_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_outputs.id", ondelete="RESTRICT"), nullable=False
    )
    render_plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_plans.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    rights_evidence_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rights_evidence.id", ondelete="RESTRICT"), nullable=True
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    destination: Mapped[DestinationPlatform] = mapped_column(
        Enum(DestinationPlatform, name="destination_platform"), nullable=False
    )
    territory: Mapped[str] = mapped_column(String(32), nullable=False)
    campaign_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    outcome: Mapped[GateOutcome] = mapped_column(
        Enum(GateOutcome, name="gate_outcome"), nullable=False
    )
    automated_outcome: Mapped[GateOutcome] = mapped_column(
        Enum(GateOutcome, name="gate_outcome"), nullable=False
    )
    evaluation_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_assistance_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    policy_explanation: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    missing_evidence: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    remediation: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    hard_block_reasons: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    approval_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    approval_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class QualityCheckResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_check_results"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "kind", name="uq_quality_check_evaluation_kind"),
        Index("ix_quality_check_tenant_evaluation", "tenant_id", "evaluation_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    evaluation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quality_evaluations.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[QualityCheckKind] = mapped_column(
        Enum(QualityCheckKind, name="quality_check_kind"), nullable=False
    )
    status: Mapped[QualityCheckStatus] = mapped_column(
        Enum(QualityCheckStatus, name="quality_check_status"), nullable=False
    )
    severity: Mapped[QualitySeverity] = mapped_column(
        Enum(QualitySeverity, name="quality_severity"), nullable=False
    )
    evidence_source: Mapped[EvidenceSource] = mapped_column(
        Enum(EvidenceSource, name="evidence_source"), nullable=False
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    check_version: Mapped[str] = mapped_column(String(80), nullable=False)
    remediation: Mapped[str] = mapped_column(Text, nullable=False)
    hard_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class QualityReviewDecision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "quality_review_decisions"
    __table_args__ = (Index("ix_quality_decision_tenant_evaluation", "tenant_id", "evaluation_id"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    evaluation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quality_evaluations.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[GateReviewAction] = mapped_column(
        Enum(GateReviewAction, name="gate_review_action"), nullable=False
    )
    decision_source: Mapped[EvidenceSource] = mapped_column(
        Enum(EvidenceSource, name="evidence_source"), nullable=False
    )
    from_outcome: Mapped[GateOutcome] = mapped_column(
        Enum(GateOutcome, name="gate_outcome"), nullable=False
    )
    to_outcome: Mapped[GateOutcome] = mapped_column(
        Enum(GateOutcome, name="gate_outcome"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged_check_ids: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class GateVerification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "gate_verifications"
    __table_args__ = (
        Index("ix_gate_verification_tenant_evaluation", "tenant_id", "evaluation_id"),
        CheckConstraint("valid_until > verified_at", name="validity_order"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    evaluation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quality_evaluations.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    checkpoint: Mapped[GateCheckpoint] = mapped_column(
        Enum(GateCheckpoint, name="gate_checkpoint"), nullable=False
    )
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_reasons: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    evidence_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    evidence_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PublishingOAuthState(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "publishing_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_publishing_oauth_state_hash"),
        Index("ix_publishing_oauth_tenant_user", "tenant_id", "user_id", "created_at"),
        CheckConstraint("expires_at > created_at", name="publishing_oauth_expiry_order"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[PublishingProvider] = mapped_column(
        Enum(PublishingProvider, name="publishing_provider"), nullable=False
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pkce_verifier_envelope: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    requested_scopes: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    denial_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class PublishingConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publishing_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_destination_id",
            name="uq_publishing_connection_destination",
        ),
        Index("ix_publishing_connection_tenant_state", "tenant_id", "state"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[PublishingProvider] = mapped_column(
        Enum(PublishingProvider, name="publishing_provider"), nullable=False
    )
    state: Mapped[OAuthConnectionState] = mapped_column(
        Enum(OAuthConnectionState, name="oauth_connection_state"), nullable=False
    )
    provider_destination_id: Mapped[str] = mapped_column(String(300), nullable=False)
    destination_display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_scopes: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    token_envelope: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    has_refresh_token: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconnect_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Publication(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "idempotency_key", name="uq_publication_idempotency"
        ),
        UniqueConstraint("background_job_id", name="uq_publication_background_job"),
        Index("ix_publications_tenant_state_created", "tenant_id", "state", "created_at"),
        Index("ix_publications_provider_remote", "provider", "provider_publication_id"),
        CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name="progress_range"),
        CheckConstraint("committed_bytes >= 0", name="committed_bytes_nonnegative"),
        CheckConstraint("content_length > 0", name="content_length_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    connection_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("publishing_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    render_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    render_output_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("render_outputs.id", ondelete="RESTRICT"), nullable=False
    )
    quality_evaluation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quality_evaluations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    background_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("background_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    public_approval_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=True
    )
    acceptance_gate_verification_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("gate_verifications.id", ondelete="RESTRICT"), nullable=False
    )
    upload_gate_verification_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("gate_verifications.id", ondelete="RESTRICT"), nullable=True
    )
    provider: Mapped[PublishingProvider] = mapped_column(
        Enum(PublishingProvider, name="publishing_provider"), nullable=False
    )
    provider_destination_id: Mapped[str] = mapped_column(String(300), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[PublishState] = mapped_column(
        Enum(PublishState, name="publish_state"), default=PublishState.QUEUED, nullable=False
    )
    visibility: Mapped[PublishVisibility] = mapped_column(
        Enum(PublishVisibility, name="publish_visibility"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    made_for_kids: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    category_id: Mapped[str] = mapped_column(String(10), default="22", nullable=False)
    territory: Mapped[str] = mapped_column(String(32), nullable=False)
    campaign_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), default="queued", nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    content_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    committed_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    upload_session_envelope: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_publication_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    provider_processing_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sanitized_provider_response: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    analytics_handoff: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uncertain_remote_result: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manual_next_step: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    upload_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    metric_snapshots: Mapped[list[AnalyticsMetricSnapshot]] = relationship(
        back_populates="publication", cascade="all, delete-orphan"
    )


class PublicationEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "publication_events"
    __table_args__ = (
        UniqueConstraint("publication_id", "sequence", name="uq_publication_event_sequence"),
        Index("ix_publication_events_tenant_publication", "tenant_id", "publication_id"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name="progress_range"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    publication_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("publications.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[PublishEventKind] = mapped_column(
        Enum(PublishEventKind, name="publish_event_kind"), nullable=False
    )
    from_state: Mapped[PublishState | None] = mapped_column(
        Enum(PublishState, name="publish_state"), nullable=True
    )
    to_state: Mapped[PublishState] = mapped_column(
        Enum(PublishState, name="publish_state"), nullable=False
    )
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class UsageCostEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_cost_events"
    __table_args__ = (Index("ix_usage_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    input_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=8), default=Decimal("0"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class FeatureFlag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_feature_tenant_name"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)


class ProviderStatus(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "provider_statuses"
    __table_args__ = (UniqueConstraint("provider", "capability", name="uq_provider_capability"),)

    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    capability: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[ProviderMode] = mapped_column(
        Enum(ProviderMode, name="provider_mode"), nullable=False
    )
    required_variable_names: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(100), nullable=True)


class SystemControl(Base):
    __tablename__ = "system_control"

    key: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    paused: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="safe default", nullable=False)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AnalyticsCollectionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One tenant-scoped analytics collection pass.

    The run is the durable record of what collection attempted and what it
    truthfully obtained. ``adapter_mode`` records the mode of the adapter
    that served the run so mocked or disabled evidence can never be
    mistaken for real provider data later.
    """

    __tablename__ = "analytics_collection_runs"
    __table_args__ = (
        Index("ix_analytics_runs_tenant_state", "tenant_id", "state"),
        CheckConstraint(
            "publications_considered >= 0 AND snapshots_written >= 0 "
            "AND metrics_reported >= 0 AND metrics_unavailable >= 0",
            name="ck_analytics_runs_counts_nonnegative",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    background_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_jobs.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    provider: Mapped[PublishingProvider | None] = mapped_column(
        Enum(PublishingProvider, name="publishing_provider"), nullable=True
    )
    state: Mapped[AnalyticsCollectionState] = mapped_column(
        Enum(AnalyticsCollectionState, name="analytics_collection_state"),
        default=AnalyticsCollectionState.QUEUED,
        nullable=False,
    )
    adapter_mode: Mapped[ProviderMode] = mapped_column(
        Enum(ProviderMode, name="provider_mode"), default=ProviderMode.DISABLED, nullable=False
    )
    publications_considered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshots_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_reported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_unavailable: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalyticsMetricSnapshot(UUIDPrimaryKeyMixin, Base):
    """One provider metric value (or truthful non-value) for a publication.

    ``value`` is NULL whenever the metric was not REPORTED or STALE; the
    database check constraint enforces that a missing, unavailable or
    permission-denied metric can never carry an invented number.
    """

    __tablename__ = "analytics_metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "metric",
            "window_start",
            "window_end",
            "source",
            name="uq_analytics_snapshot_dedup",
        ),
        Index("ix_analytics_snapshots_tenant_publication", "tenant_id", "publication_id"),
        Index("ix_analytics_snapshots_metric_window", "metric", "window_end"),
        CheckConstraint(
            "(availability IN ('REPORTED', 'STALE') AND value IS NOT NULL) "
            "OR (availability NOT IN ('REPORTED', 'STALE') AND value IS NULL)",
            name="ck_analytics_snapshot_value_matches_availability",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    publication_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("publications.id", ondelete="CASCADE"),
        nullable=False,
    )
    collection_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("analytics_collection_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[PublishingProvider] = mapped_column(
        Enum(PublishingProvider, name="publishing_provider"), nullable=False
    )
    provider_publication_id: Mapped[str] = mapped_column(String(500), nullable=False)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    availability: Mapped[MetricAvailability] = mapped_column(
        Enum(MetricAvailability, name="metric_availability"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_definition_version: Mapped[str] = mapped_column(String(80), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    publication: Mapped[Publication] = relationship(back_populates="metric_snapshots")
    collection_run: Mapped[AnalyticsCollectionRun | None] = relationship()


class Experiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (Index("ix_experiments_tenant_state", "tenant_id", "state"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[ExperimentState] = mapped_column(
        Enum(ExperimentState, name="experiment_state"),
        default=ExperimentState.DRAFT,
        nullable=False,
    )
    eligibility: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    success_metric: Mapped[str] = mapped_column(String(80), nullable=False)
    success_criteria: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    variants: Mapped[list[ExperimentVariant]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class ExperimentVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "name", name="uq_experiment_variant_name"),
        CheckConstraint(
            "candidate_id IS NOT NULL OR publication_id IS NOT NULL",
            name="ck_experiment_variant_linked",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    experiment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    candidate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("clip_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    publication_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("publications.id", ondelete="SET NULL"),
        nullable=True,
    )
    # What this variant changes (hook text, duration target, caption mode,
    # posting time or format parameters), kept as reviewed configuration.
    config: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)

    experiment: Mapped[Experiment] = relationship(back_populates="variants")


class Recommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A bounded learning-loop suggestion.

    Recommendations are created only in PROPOSED state by the deterministic
    generator, carry the evidence they were derived from, and can change
    production behaviour only after a human review transition. The
    generator itself has no write path to any other domain table.
    """

    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_tenant_state", "tenant_id", "state"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[RecommendationKind] = mapped_column(
        Enum(RecommendationKind, name="recommendation_kind"), nullable=False
    )
    state: Mapped[RecommendationState] = mapped_column(
        Enum(RecommendationState, name="recommendation_state"),
        default=RecommendationState.PROPOSED,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(80), nullable=False)
    # Recommendations are authored by the system generator, never by a
    # user; the reviewer fields below record the human decision instead.
    created_by: Mapped[str] = mapped_column(String(80), default="system", nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class OpportunitySource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant-configured origin for Phase 8 opportunity imports.

    ``adapter_mode`` records the truthful mode of the adapter that most
    recently served this source (seed lists are real-tested local code,
    RSS is unverified until a live fetch is proven, search and public-site
    adapters are disabled); it is refreshed on creation and every sync so
    mocked or disabled evidence can never masquerade as a live source.
    """

    __tablename__ = "opportunity_sources"
    __table_args__ = (Index("ix_opportunity_sources_tenant_kind", "tenant_id", "kind"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[OpportunitySourceKind] = mapped_column(
        Enum(OpportunitySourceKind, name="opportunity_source_kind"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[OpportunitySourceStatus] = mapped_column(
        Enum(OpportunitySourceStatus, name="opportunity_source_status"),
        default=OpportunitySourceStatus.ENABLED,
        nullable=False,
    )
    adapter_mode: Mapped[ProviderMode] = mapped_column(
        Enum(ProviderMode, name="provider_mode"), default=ProviderMode.DISABLED, nullable=False
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    opportunities: Mapped[list[Opportunity]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    sync_runs: Mapped[list[OpportunitySyncRun]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class OpportunitySyncRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One tenant-scoped opportunity import pass over a single source.

    The run is the durable record of what the sync attempted and what it
    truthfully did: records received from the adapter, rows created,
    exact matches merged, possible duplicates flagged for human review
    (never auto-merged) and records skipped because a suppression matched.
    """

    __tablename__ = "opportunity_sync_runs"
    __table_args__ = (
        Index("ix_opportunity_sync_runs_tenant_state", "tenant_id", "state"),
        CheckConstraint(
            "records_received >= 0 AND records_created >= 0 AND records_merged >= 0 "
            "AND duplicates_flagged >= 0 AND suppressed_skipped >= 0",
            name="ck_opportunity_sync_runs_counts_nonnegative",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunity_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    background_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_jobs.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    state: Mapped[OpportunitySyncState] = mapped_column(
        Enum(OpportunitySyncState, name="opportunity_sync_state"),
        default=OpportunitySyncState.QUEUED,
        nullable=False,
    )
    adapter_mode: Mapped[ProviderMode] = mapped_column(
        Enum(ProviderMode, name="provider_mode"), default=ProviderMode.DISABLED, nullable=False
    )
    records_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_merged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates_flagged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    suppressed_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source: Mapped[OpportunitySource] = relationship(back_populates="sync_runs")


class Opportunity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One discovered creator/content opportunity, pre-Phase-9.

    Phase 8 records evidence and an explainable score only. The
    ``contact_route_label`` is a human-readable label of a public contact
    route (for example "business email on channel About page"); ClipOS
    never stores scraped private contact data and has no code path that
    contacts anyone.
    """

    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_id", "external_key", name="uq_opportunity_source_external_key"
        ),
        Index("ix_opportunities_tenant_dedup", "tenant_id", "dedup_key"),
        Index("ix_opportunities_tenant_state", "tenant_id", "state"),
        CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name="ck_opportunities_score_range",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunity_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    sync_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunity_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_key: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    creator_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    creator_handle: Mapped[str | None] = mapped_column(String(200), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(80), nullable=True)
    contact_route_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Provenance per normalised field (adapter, fetched_at, source URL),
    # the scoring signals consumed by opportunity-score-v1, and the raw
    # source record, kept so every field can be traced to its origin.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    niche: Mapped[str | None] = mapped_column(String(120), nullable=True)
    language: Mapped[str | None] = mapped_column(String(35), nullable=True)
    audience_band: Mapped[str | None] = mapped_column(String(40), nullable=True)
    posting_cadence: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(600), nullable=False)
    possible_duplicate_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[OpportunityState] = mapped_column(
        Enum(OpportunityState, name="opportunity_state"),
        default=OpportunityState.NEW,
        nullable=False,
    )
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_version: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    # v2.1 content-debt intake. Recorded library facts an operator or
    # source asserted at import time; all nullable because most sources
    # cannot provide them, and the content-debt score treats every
    # absence as an unscored factor rather than an estimate.
    library_episode_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    library_long_form_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    existing_shorts_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    moments_per_episode_assumption: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[OpportunitySource] = relationship(back_populates="opportunities")
    score_components: Mapped[list[OpportunityScoreComponent]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )


class OpportunityScoreComponent(UUIDPrimaryKeyMixin, Base):
    """One explainable Phase 8 scoring factor, mirroring Phase 3."""

    __tablename__ = "opportunity_score_components"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "key", name="uq_opportunity_score_component_key"),
        Index("ix_opportunity_score_tenant_opportunity", "tenant_id", "opportunity_id"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score_range"),
        CheckConstraint("weight > 0", name="weight_positive"),
        CheckConstraint("weighted_score >= 0", name="weighted_nonnegative"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    meaning: Mapped[str] = mapped_column(String(500), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    weighted_score: Mapped[float] = mapped_column(Float, nullable=False)
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    missing_behavior: Mapped[str] = mapped_column(String(160), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(80), nullable=False)

    opportunity: Mapped[Opportunity] = relationship(back_populates="score_components")


class OpportunitySuppression(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant-scoped do-not-import rule for Phase 8.

    Suppressions are matched at import time (matching records are skipped)
    and when a suppression is created it immediately transitions matching
    existing opportunities to ``suppressed`` so they leave the working
    list. Suppressed records are never contacted: Phase 8 has no outreach.
    """

    __tablename__ = "opportunity_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "match_type", "match_value", name="uq_opportunity_suppression_match"
        ),
        Index("ix_opportunity_suppressions_tenant_type", "tenant_id", "match_type"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    match_type: Mapped[SuppressionMatchType] = mapped_column(
        Enum(SuppressionMatchType, name="suppression_match_type"), nullable=False
    )
    match_value: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class OpportunityHandoff(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The versioned Phase 9 intake package for one opportunity.

    A handoff is data preparation only: it packages the opportunity, its
    evidence provenance and its score components into an
    ``opportunity-handoff.v1`` payload that a future Phase 9 may consume.
    Nothing is sent anywhere when a handoff is created or marked ready.
    """

    __tablename__ = "opportunity_handoffs"
    __table_args__ = (
        UniqueConstraint("opportunity_id", name="uq_opportunity_handoff_opportunity"),
        Index("ix_opportunity_handoffs_tenant_state", "tenant_id", "state"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    payload_version: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[OpportunityHandoffState] = mapped_column(
        Enum(OpportunityHandoffState, name="opportunity_handoff_state"),
        default=OpportunityHandoffState.DRAFT,
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class CrmContact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One person known to the Phase 9 CRM.

    The only contact route stored is ``contact_route_label`` — a text
    label of a public route (for example "business email listed on
    channel page"), carried over from Phase 8. ClipOS never stores
    scraped private email addresses or phone numbers, and Phase 9 has no
    code path that contacts anyone. ``suppressed`` is the opt-out flag:
    a suppressed contact must never receive new sales drafts.
    """

    __tablename__ = "crm_contacts"
    __table_args__ = (
        Index("ix_crm_contacts_tenant_dedup", "tenant_id", "dedup_key"),
        Index("ix_crm_contacts_tenant_suppressed", "tenant_id", "suppressed"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    organisation: Mapped[str | None] = mapped_column(String(300), nullable=True)
    role_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_route_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_opportunity_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True,
    )
    dedup_key: Mapped[str] = mapped_column(String(600), nullable=False)
    possible_duplicate_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True
    )
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    accounts: Mapped[list[CrmAccount]] = relationship(
        back_populates="primary_contact", foreign_keys="CrmAccount.primary_contact_id"
    )


class CrmAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One prospect or client organisation in the Phase 9 pipeline.

    ``source_handoff_id`` is unique: a Phase 8 handoff can be taken into
    the CRM exactly once, which is what makes intake idempotent.
    ``stage`` is the current lifecycle position; every change is recorded
    as an append-only :class:`CrmPipelineEvent`.
    """

    __tablename__ = "crm_accounts"
    __table_args__ = (
        UniqueConstraint("source_handoff_id", name="uq_crm_account_source_handoff"),
        UniqueConstraint(
            "tenant_id", "source_opportunity_id", name="uq_crm_account_source_opportunity"
        ),
        Index("ix_crm_accounts_tenant_stage", "tenant_id", "stage"),
        Index("ix_crm_accounts_tenant_dedup", "tenant_id", "dedup_key"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    stage: Mapped[CrmStage] = mapped_column(
        Enum(CrmStage, name="crm_stage"), default=CrmStage.PROSPECT, nullable=False
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    primary_contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True
    )
    source_opportunity_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_handoff_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("opportunity_handoffs.id", ondelete="SET NULL"),
        nullable=True,
    )
    dedup_key: Mapped[str] = mapped_column(String(600), nullable=False)
    possible_duplicate_of_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_accounts.id", ondelete="SET NULL"), nullable=True
    )
    next_action: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    primary_contact: Mapped[CrmContact | None] = relationship(
        back_populates="accounts", foreign_keys=[primary_contact_id]
    )
    pipeline_events: Mapped[list[CrmPipelineEvent]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    drafts: Mapped[list[SalesAssetDraft]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[CrmTask]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    revenue_records: Mapped[list[RevenueRecord]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    onboarding_records: Mapped[list[OnboardingRecord]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class CrmPipelineEvent(UUIDPrimaryKeyMixin, Base):
    """One append-only entry in an account's (or contact's) history.

    Stage transitions set ``event_kind='stage_transition'`` with from/to
    stages; reviewed dedup merges set ``event_kind='merge'``. Rows are
    never updated or deleted by the service — the table is the history.
    """

    __tablename__ = "crm_pipeline_events"
    __table_args__ = (
        Index("ix_crm_pipeline_events_tenant_account", "tenant_id", "account_id"),
        Index("ix_crm_pipeline_events_tenant_contact", "tenant_id", "contact_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_accounts.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=True
    )
    event_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    from_stage: Mapped[CrmStage | None] = mapped_column(
        Enum(CrmStage, name="crm_stage"), nullable=True
    )
    to_stage: Mapped[CrmStage | None] = mapped_column(
        Enum(CrmStage, name="crm_stage"), nullable=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    account: Mapped[CrmAccount | None] = relationship(back_populates="pipeline_events")


class SalesAssetDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One draft sales asset: outreach copy, a follow-up, a proposal
    outline, a discovery agenda or an onboarding checklist draft.

    Drafts are assembled only from cited stored evidence — ``evidence``
    carries the citation list and a draft with zero citations cannot be
    created. The lifecycle ends at ``approved`` (or ``rejected`` /
    ``archived``): there is no sent state, because Phase 9 has no send
    path and approval grants no sending capability.
    """

    __tablename__ = "sales_asset_drafts"
    __table_args__ = (
        Index("ix_sales_asset_drafts_tenant_account", "tenant_id", "account_id"),
        Index("ix_sales_asset_drafts_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_accounts.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[SalesAssetKind] = mapped_column(
        Enum(SalesAssetKind, name="sales_asset_kind"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[SalesAssetState] = mapped_column(
        Enum(SalesAssetState, name="sales_asset_state"),
        default=SalesAssetState.DRAFT,
        nullable=False,
    )
    template_version: Mapped[str] = mapped_column(String(80), nullable=False)
    # Drafts are authored either by the deterministic template generator
    # ("system") or by a user; the reviewer fields record the human
    # decision, mirroring the Phase 7 recommendation pattern.
    created_by: Mapped[str] = mapped_column(String(80), default="system", nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    account: Mapped[CrmAccount] = relationship(back_populates="drafts")


class CrmTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One follow-up task attached to a CRM account."""

    __tablename__ = "crm_tasks"
    __table_args__ = (
        Index("ix_crm_tasks_tenant_account", "tenant_id", "account_id"),
        Index("ix_crm_tasks_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_accounts.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[CrmTaskState] = mapped_column(
        Enum(CrmTaskState, name="crm_task_state"), default=CrmTaskState.OPEN, nullable=False
    )
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    account: Mapped[CrmAccount] = relationship(back_populates="tasks")


class RevenueRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One money-related record for a CRM account.

    Kinds stay distinct forever: a ``quote`` records what was quoted, a
    ``contract`` what was agreed, an ``invoice`` what was billed, a
    ``payment`` what was collected, a ``refund`` what was returned and a
    ``recurring`` record a recurring commitment. Summaries never add one
    kind into another kind's bucket, and ``external_reference`` is only
    ever an operator-recorded provider reference string — Phase 9 has no
    payments integration and moves no money.
    """

    __tablename__ = "revenue_records"
    __table_args__ = (
        Index("ix_revenue_records_tenant_account", "tenant_id", "account_id"),
        Index("ix_revenue_records_tenant_kind_status", "tenant_id", "kind", "status"),
        Index("ix_revenue_records_tenant_campaign", "tenant_id", "campaign_id"),
        CheckConstraint("amount_minor_units >= 0", name="amount_nonnegative"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_accounts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[RevenueKind] = mapped_column(
        Enum(RevenueKind, name="revenue_kind"), nullable=False
    )
    amount_minor_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[RevenueState] = mapped_column(
        Enum(RevenueState, name="revenue_state"), default=RevenueState.DRAFT, nullable=False
    )
    external_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # v2.1: set only on ``campaign_payout`` records, linking collected
    # campaign money back to the campaign it was collected for.
    campaign_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    account: Mapped[CrmAccount] = relationship(back_populates="revenue_records")


class OnboardingRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The onboarding state for one client account (one record each).

    ``checklist`` is the JSON checklist state (items with key, label and
    done flag) and ``milestones`` the recorded milestone list. Completing
    onboarding requires every checklist item to be done.
    """

    __tablename__ = "onboarding_records"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_onboarding_record_account"),
        Index("ix_onboarding_records_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_accounts.id", ondelete="CASCADE"), nullable=False
    )
    checklist: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    milestones: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    status: Mapped[OnboardingState] = mapped_column(
        Enum(OnboardingState, name="onboarding_state"),
        default=OnboardingState.NOT_STARTED,
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    account: Mapped[CrmAccount] = relationship(back_populates="onboarding_records")


class AgentDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One registered agent contract for the Phase 10 executive layer.

    Definitions are tenant-scoped; a row with ``tenant_id IS NULL`` and
    ``is_system`` set is a platform-provided default that any tenant may
    resolve against. ``contract`` is validated against the executive
    action registry on write: it names the action kinds the agent may
    run, the input/output schema versions it speaks, a per-run cost
    budget (internal budget minor units), a per-dispatch timeout and a
    per-plan step cap. A definition grants no external authority — the
    registry it references contains only internal, reversible actions.
    """

    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_agent_definition_tenant_name"),
        Index("ix_agent_definitions_tenant_role", "tenant_id", "role", "status"),
    )

    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[AgentRole] = mapped_column(Enum(AgentRole, name="agent_role"), nullable=False)
    contract: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    status: Mapped[AgentDefinitionStatus] = mapped_column(
        Enum(AgentDefinitionStatus, name="agent_definition_status"),
        default=AgentDefinitionStatus.DISABLED,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ExecutiveControl(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The per-tenant executive kill switch and cost ceilings.

    One row per tenant. ``mode`` is checked before a plan is queued and
    again between every pair of actions during execution, so flipping
    it to PAUSED stops a running plan at the next action boundary. The
    Phase 1 platform-level SystemControl pause is a second, independent
    layer and is honoured as well. Ceilings are internal budget minor
    units (the same units action cost estimates use); NULL means no
    ceiling of that kind.
    """

    __tablename__ = "executive_controls"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_executive_control_tenant"),
        CheckConstraint(
            "daily_cost_ceiling_minor IS NULL OR daily_cost_ceiling_minor >= 0",
            name="ck_executive_control_daily_ceiling_nonnegative",
        ),
        CheckConstraint(
            "per_action_cost_ceiling_minor IS NULL OR per_action_cost_ceiling_minor >= 0",
            name="ck_executive_control_per_action_ceiling_nonnegative",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[ExecutiveControlMode] = mapped_column(
        Enum(ExecutiveControlMode, name="executive_control_mode"),
        default=ExecutiveControlMode.NORMAL,
        nullable=False,
    )
    daily_cost_ceiling_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    per_action_cost_ceiling_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ExecutivePlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One executive plan: a validated, ordered list of internal actions
    with the trusted-state evidence it was derived from.

    Plans are created whole (by the deterministic planner or manually
    through the API) and are immutable once they leave DRAFT — there is
    no edit path, so an approval can never be silently re-pointed at
    different actions. ``approval_request_id`` links to the Phase 1
    approvals system when the plan needs human approval; execution
    re-reads that approval's status before the first action and refuses
    to run on a missing, rejected or expired one.
    """

    __tablename__ = "executive_plans"
    __table_args__ = (
        Index("ix_executive_plans_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "total_estimated_cost_minor >= 0", name="ck_executive_plan_cost_nonnegative"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ExecutivePlanState] = mapped_column(
        Enum(ExecutivePlanState, name="executive_plan_state"),
        default=ExecutivePlanState.DRAFT,
        nullable=False,
    )
    # ``created_by`` mirrors the Phase 7/9 pattern: "system" for the
    # executive planner loop, otherwise the creating user's id as text;
    # the FK below is the authoritative attribution when a user exists.
    created_by: Mapped[str] = mapped_column(String(80), default="system", nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    total_estimated_cost_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    approval_request_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    execution_requested_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    actions: Mapped[list[ExecutiveAction]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="ExecutiveAction.sequence"
    )


class ExecutiveAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One step of an executive plan.

    ``kind`` can only ever be a member of the closed registry enum, and
    ``payload`` is re-validated against the kind's strict pydantic
    schema both when the plan is created and again immediately before
    dispatch — a tampered row therefore fails closed at execution time
    instead of running. Costs are internal budget minor units.
    """

    __tablename__ = "executive_actions"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence", name="uq_executive_action_plan_sequence"),
        Index("ix_executive_actions_tenant_plan", "tenant_id", "plan_id"),
        CheckConstraint("sequence >= 1", name="ck_executive_action_sequence_positive"),
        CheckConstraint(
            "cost_estimate_minor >= 0 AND cost_incurred_minor >= 0",
            name="ck_executive_action_costs_nonnegative",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executive_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[ExecutiveActionKind] = mapped_column(
        Enum(ExecutiveActionKind, name="executive_action_kind"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    autonomy_class: Mapped[AutonomyClass] = mapped_column(
        Enum(AutonomyClass, name="autonomy_class"), nullable=False
    )
    status: Mapped[ExecutiveActionState] = mapped_column(
        Enum(ExecutiveActionState, name="executive_action_state"),
        default=ExecutiveActionState.PENDING,
        nullable=False,
    )
    cost_estimate_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cost_incurred_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    agent_definition_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped[ExecutivePlan] = relationship(back_populates="actions")


class AgentRun(UUIDPrimaryKeyMixin, Base):
    """One agent's execution of one executive action.

    The run snapshots the agent name/role at dispatch time (definitions
    can be edited later), records steps used against the contract cap
    and the internal budget units actually incurred. Internal
    deterministic actions generate no provider usage, so
    ``usage_cost_event_id`` stays NULL for them; it exists so a future
    model-backed action can link the exact UsageCostEvent it produced.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_tenant_plan", "tenant_id", "plan_id"),
        Index("ix_agent_runs_tenant_agent", "tenant_id", "agent_id"),
        CheckConstraint("steps_used >= 0", name="ck_agent_run_steps_nonnegative"),
        CheckConstraint("cost_incurred_minor >= 0", name="ck_agent_run_cost_nonnegative"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_role: Mapped[AgentRole] = mapped_column(
        Enum(AgentRole, name="agent_role"), nullable=False
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executive_plans.id", ondelete="SET NULL"),
        nullable=True,
    )
    action_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("executive_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[AgentRunState] = mapped_column(
        Enum(AgentRunState, name="agent_run_state"),
        default=AgentRunState.RUNNING,
        nullable=False,
    )
    steps_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_incurred_minor: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    usage_cost_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("usage_cost_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ExecutiveProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A versioned self-improvement proposal: a suggested change to
    scoring weights, a prompt/template, or a policy text.

    Proposals are records for human decision, nothing more. Review can
    approve or reject one, but approval changes only this row — there
    is no apply path in this system, so an approved proposal takes
    effect only when a human makes the corresponding configuration
    change separately and under its own audit trail.
    """

    __tablename__ = "executive_proposals"
    __table_args__ = (
        Index("ix_executive_proposals_tenant_state", "tenant_id", "state"),
        CheckConstraint("version >= 1", name="ck_executive_proposal_version_positive"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[ExecutiveProposalKind] = mapped_column(
        Enum(ExecutiveProposalKind, name="executive_proposal_kind"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    change_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, nullable=False
    )
    state: Mapped[ExecutiveProposalState] = mapped_column(
        Enum(ExecutiveProposalState, name="executive_proposal_state"),
        default=ExecutiveProposalState.PROPOSED,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), default="system", nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One third-party clipping campaign a tenant may produce for (v2.1).

    Campaigns enter only from an operator: a single manual record or an
    owner-supplied seed import. ``source_reference`` keeps the
    provenance of the listing (a URL or label the owner supplied) in
    Phase 8's source-record style — no external campaign source is
    connected and nothing scrapes one. Money is integer minor units
    with an ISO currency; ``cpm_rate_minor`` is the recorded rate per
    thousand views, never a value ClipOS estimates or invents.
    """

    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
        Index("ix_campaigns_tenant_platform", "tenant_id", "platform"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_campaign_tenant_idempotency_key"),
        CheckConstraint("cpm_rate_minor IS NULL OR cpm_rate_minor >= 0", name="cpm_nonnegative"),
        CheckConstraint(
            "max_payout_minor IS NULL OR max_payout_minor >= 0", name="max_payout_nonnegative"
        ),
        CheckConstraint("budget_minor IS NULL OR budget_minor >= 0", name="budget_nonnegative"),
        CheckConstraint(
            "minimum_views IS NULL OR minimum_views >= 0", name="minimum_views_nonnegative"
        ),
        CheckConstraint(
            "expected_views IS NULL OR expected_views >= 0", name="expected_views_nonnegative"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    sponsor_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_kind: Mapped[CampaignSourceKind] = mapped_column(
        Enum(CampaignSourceKind, name="campaign_source_kind"),
        default=CampaignSourceKind.MANUAL,
        nullable=False,
    )
    source_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cpm_rate_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    max_payout_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    budget_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    minimum_views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    rights_basis: Mapped[CampaignRightsBasis] = mapped_column(
        Enum(CampaignRightsBasis, name="campaign_rights_basis"),
        default=CampaignRightsBasis.UNKNOWN,
        nullable=False,
    )
    submission_process: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status"),
        default=CampaignStatus.CANDIDATE,
        nullable=False,
    )
    expected_views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional CRM link: campaign payouts are recorded as Phase 9
    # revenue records, which belong to an account, so a payout needs
    # this link or an account named at payout time.
    crm_account_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("crm_accounts.id", ondelete="SET NULL"), nullable=True
    )
    qualification_override_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    requirements: Mapped[list[CampaignRequirement]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    assessments: Mapped[list[CampaignAssessment]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    publication_links: Mapped[list[CampaignPublication]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignRequirement(UUIDPrimaryKeyMixin, Base):
    """One recorded requirement of a campaign (a minimum, a restriction,
    an account condition). ``kind`` is an operator-supplied label such
    as ``minimum_views`` or ``account_age_days``; the requirement is
    evidence for a human decision, never an automated check against a
    platform."""

    __tablename__ = "campaign_requirements"
    __table_args__ = (Index("ix_campaign_requirements_campaign", "campaign_id"),)

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    campaign_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="requirements")


class CampaignAssessment(UUIDPrimaryKeyMixin, Base):
    """One immutable profitability assessment of a campaign.

    Assessments are append-only: recomputing creates a new row with its
    own inputs, so the number a qualification decision relied on can
    always be re-read exactly. ``unassessable`` rows persist the missing
    input names instead of a result. Money is integer minor units in
    the campaign's currency; the risk adjustment is a recorded
    percentage input, never a hidden fudge factor.
    """

    __tablename__ = "campaign_assessments"
    __table_args__ = (
        Index("ix_campaign_assessments_campaign", "tenant_id", "campaign_id", "created_at"),
        CheckConstraint("risk_percent >= 0 AND risk_percent <= 100", name="risk_percent_range"),
        CheckConstraint(
            "editing_cost_minor >= 0 AND model_cost_minor >= 0 AND distribution_cost_minor >= 0",
            name="assessment_costs_nonnegative",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    campaign_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[CampaignAssessmentState] = mapped_column(
        Enum(CampaignAssessmentState, name="campaign_assessment_state"), nullable=False
    )
    missing_inputs: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, default=list, nullable=False)
    expected_views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cpm_rate_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gross_revenue_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    capped_by_max_payout: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    editing_cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    model_cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    distribution_cost_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    risk_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_adjustment_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_profit_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="assessments")


class CampaignPublication(UUIDPrimaryKeyMixin, Base):
    """The link between a campaign and one publication produced for it.

    The link is attribution only: it records that a publication counts
    toward this campaign, so payouts and analytics can be read per
    campaign. Creating or removing a link changes no publication state.
    """

    __tablename__ = "campaign_publications"
    __table_args__ = (
        UniqueConstraint("campaign_id", "publication_id", name="uq_campaign_publication_pair"),
        Index("ix_campaign_publications_tenant_campaign", "tenant_id", "campaign_id"),
        Index("ix_campaign_publications_publication", "publication_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    campaign_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    publication_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("publications.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    campaign: Mapped[Campaign] = relationship(back_populates="publication_links")
