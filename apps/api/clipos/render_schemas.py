from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from clipos.core.enums import (
    AudioNormalizationMode,
    CaptionMode,
    RenderCropStrategy,
    RenderDefectCategory,
    RenderState,
)

RenderPreset = Literal[
    "tiktok_vertical",
    "instagram_reels",
    "youtube_shorts",
    "vertical_1080p",
    "vertical_720p",
    "square_1080p",
    "landscape_1080p",
    "custom",
]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaptionConfiguration(StrictSchema):
    mode: CaptionMode = CaptionMode.BOTH
    max_characters_per_line: int | None = Field(default=None, ge=12, le=64)
    max_lines: Literal[1, 2] | None = None
    style: Literal["clean_bottom", "high_contrast_bottom"] = "high_contrast_bottom"


class AudioConfiguration(StrictSchema):
    normalization: AudioNormalizationMode = AudioNormalizationMode.SPEECH_LOUDNESS
    target_lufs: float | None = Field(default=None, ge=-24, le=-10)
    true_peak_db: float | None = Field(default=None, ge=-6, le=-0.1)


class RenderPlanCreateRequest(StrictSchema):
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    preset: RenderPreset = "vertical_1080p"
    width: int | None = Field(default=None, ge=240, le=8192)
    height: int | None = Field(default=None, ge=240, le=8192)
    frame_rate: float | None = Field(default=None, ge=1, le=120)
    crop_strategy: RenderCropStrategy = RenderCropStrategy.CENTER_CROP
    captions: CaptionConfiguration = Field(default_factory=CaptionConfiguration)
    audio: AudioConfiguration = Field(default_factory=AudioConfiguration)
    include_tenant_branding: bool = False
    video_codec: Literal["h264"] = "h264"
    audio_codec: Literal["aac"] = "aac"
    container: Literal["mp4"] = "mp4"

    @model_validator(mode="after")
    def custom_dimensions(self) -> RenderPlanCreateRequest:
        supplied = self.width is not None or self.height is not None
        if self.preset == "custom" and (self.width is None or self.height is None):
            raise ValueError("custom preset requires both width and height")
        if self.preset != "custom" and supplied:
            raise ValueError("width and height may be supplied only with the custom preset")
        if self.width is not None and self.width % 2:
            raise ValueError("width must be even for H.264/YUV420 output")
        if self.height is not None and self.height % 2:
            raise ValueError("height must be even for H.264/YUV420 output")
        return self


class TenantRenderProfileUpdate(StrictSchema):
    branding_enabled: bool
    branding_text: str | None = Field(default=None, max_length=80)
    branding_position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_right"
    branding_opacity: float = Field(default=0.85, ge=0.1, le=1)
    branding_font_size: int = Field(default=36, ge=12, le=144)

    @field_validator("branding_text")
    @classmethod
    def normalize_branding_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def enabled_requires_text(self) -> TenantRenderProfileUpdate:
        if self.branding_enabled and not self.branding_text:
            raise ValueError("enabled branding requires non-blank tenant branding text")
        return self


class TenantRenderProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    tenant_id: UUID
    updated_by_user_id: UUID | None = None
    branding_enabled: bool
    branding_text: str | None
    branding_position: str
    branding_opacity: float
    branding_font_size: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    logo_overlay_supported: bool = False


class RenderCaptionSegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ordinal: int
    source_start_ms: int
    source_end_ms: int
    output_start_ms: int
    output_end_ms: int
    text: str
    line_count: int
    character_count: int
    timing_source: str


class RenderPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    candidate_id: UUID
    asset_id: UUID
    transcript_id: UUID
    requested_by_user_id: UUID
    plan_version: str
    renderer_version: str
    idempotency_key: str
    request_hash: str
    preset: str
    source_start_ms: int
    source_end_ms: int
    duration_ms: int
    source_boundary_revision: int
    aspect_ratio: str
    width: int
    height: int
    frame_rate: float
    crop_strategy_requested: str
    crop_strategy_applied: str
    smart_crop_used: bool
    crop_fallback_reason: str | None
    caption_mode: str
    caption_style: dict[str, object]
    safe_area: dict[str, object]
    overlays: list[dict[str, object]]
    branding_snapshot: dict[str, object]
    audio_settings: dict[str, object]
    video_codec: str
    audio_codec: str
    container: str
    output_storage_prefix: str
    source_time_mapping: dict[str, object]
    provenance: dict[str, object]
    limitations: list[str]
    created_at: datetime
    updated_at: datetime


class RenderJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    render_plan_id: UUID
    background_job_id: UUID
    requested_by_user_id: UUID
    state: RenderState
    progress_percent: int
    stage: str
    cancellation_requested: bool
    attempt_count: int
    max_attempts: int
    renderer_version: str
    elapsed_ms: int | None
    failure_category: str | None
    failure_message: str | None
    failure_details: dict[str, object]
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RenderOutputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    render_job_id: UUID
    render_plan_id: UUID
    attempt_number: int
    is_current: bool
    storage_key: str
    checksum_sha256: str
    size_bytes: int
    content_type: str
    sidecar_storage_key: str | None
    sidecar_checksum_sha256: str | None
    format_name: str
    duration_seconds: float
    video_codec: str
    audio_codec: str
    width: int
    height: int
    frame_rate: float
    audio_channels: int | None
    audio_sample_rate: int | None
    probe_version: str
    validation: dict[str, object]
    provenance: dict[str, object]
    renderer_version: str
    created_at: datetime


class RenderDefectCreate(StrictSchema):
    category: RenderDefectCategory
    description: str = Field(min_length=10, max_length=4000)
    meaning_or_boundary_changed: bool = False
    requested_resolution: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def category_matches_boundary_flag(self) -> RenderDefectCreate:
        if self.category == RenderDefectCategory.MEANING_OR_BOUNDARY:
            self.meaning_or_boundary_changed = True
        return self


class RenderDefectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    render_job_id: UUID
    render_output_id: UUID
    reported_by_user_id: UUID
    category: str
    description: str
    meaning_or_boundary_changed: bool
    requested_resolution: str | None
    created_at: datetime


class RenderCreateResponse(StrictSchema):
    plan: RenderPlanResponse
    captions: list[RenderCaptionSegmentResponse]
    render: RenderJobResponse
    idempotent: bool


class RenderDetailResponse(StrictSchema):
    plan: RenderPlanResponse
    captions: list[RenderCaptionSegmentResponse]
    render: RenderJobResponse
    outputs: list[RenderOutputResponse]
    defects: list[RenderDefectResponse]


class RenderListItem(StrictSchema):
    render: RenderJobResponse
    plan: RenderPlanResponse
    current_output: RenderOutputResponse | None


class RenderListResponse(StrictSchema):
    items: list[RenderListItem]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class RenderCapabilitiesResponse(StrictSchema):
    enabled: bool
    ffmpeg_available: bool
    render_ready: bool
    ffmpeg_version: str | None
    ffprobe_version: str | None
    capabilities: list[str]
    presets: list[dict[str, object]]
    real_features: list[str]
    conditional_features: list[str]
    unavailable_features: list[str]
    limitations: list[str]


RenderStateFilter = Annotated[RenderState | None, Field(default=None)]
