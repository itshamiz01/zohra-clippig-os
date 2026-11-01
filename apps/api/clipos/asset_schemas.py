from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from clipos.core.enums import (
    AssetSource,
    AssetState,
    DiarizationState,
    MediaKind,
    ProviderMode,
    TranscriptState,
)


class ORMAssetModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class AssetResponse(ORMAssetModel):
    id: UUID
    tenant_id: UUID
    submitted_by_user_id: UUID
    processing_job_id: UUID | None
    source: AssetSource
    state: AssetState
    media_kind: MediaKind
    original_filename: str
    safe_extension: str
    declared_content_type: str | None
    detected_content_type: str
    size_bytes: int
    checksum_sha256: str
    permission_confirmed: bool
    rights_note: str
    acquired_at: datetime
    progress_percent: int
    processing_stage: str
    metadata_verified: bool
    cancellation_requested: bool
    error_category: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class MediaMetadataResponse(ORMAssetModel):
    format_name: str
    duration_seconds: float
    bit_rate: int | None
    has_video: bool
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    frame_rate: float | None
    audio_channels: int | None
    audio_sample_rate: int | None
    probe_version: str
    verified_at: datetime


class TranscriptSummaryResponse(ORMAssetModel):
    id: UUID
    state: TranscriptState
    provider: str
    model: str
    language: str | None
    confidence: float | None
    diarization_state: DiarizationState
    summary: str | None
    key_topics: list[str]
    word_timestamps_available: bool
    segment_count: int
    word_count: int
    algorithm_version: str
    created_at: datetime
    updated_at: datetime


class AssetDetailResponse(BaseModel):
    asset: AssetResponse
    metadata: MediaMetadataResponse | None
    transcript: TranscriptSummaryResponse | None


class UploadAssetResponse(BaseModel):
    asset: AssetResponse
    duplicate: bool
    processing_job_id: UUID | None


class TranscriptSegmentResponse(ORMAssetModel):
    id: UUID
    ordinal: int
    start_ms: int
    end_ms: int
    text: str
    normalized_text: str
    confidence: float | None
    speaker_label: str | None


class TranscriptWordResponse(ORMAssetModel):
    id: UUID
    segment_id: UUID | None
    ordinal: int
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None
    speaker_label: str | None


class TranscriptChunkResponse(ORMAssetModel):
    id: UUID
    ordinal: int
    start_segment_ordinal: int
    end_segment_ordinal: int
    start_ms: int
    end_ms: int
    char_start: int
    char_end: int
    text: str
    summary: str | None
    topics: list[str]
    algorithm_version: str


class TranscriptDetailResponse(BaseModel):
    transcript: TranscriptSummaryResponse
    original_text: str
    normalized_text: str
    segments: list[TranscriptSegmentResponse]
    words: list[TranscriptWordResponse]
    chunks: list[TranscriptChunkResponse]
    segment_offset: int
    segment_limit: int
    word_offset: int
    word_limit: int
    chunk_offset: int
    chunk_limit: int
    chunk_count: int


class URLIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    permission_confirmed: bool
    rights_note: str = Field(min_length=10, max_length=4000)


class SourceCapability(BaseModel):
    source: AssetSource
    mode: ProviderMode
    enabled: bool
    limitations: list[str]
    required_configuration_names: list[str]
