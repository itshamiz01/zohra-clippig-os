from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from clipos.core.enums import Environment

_EPHEMERAL_SECRET_PREFIX = "ephemeral-development-secret:"


def _ephemeral_development_secret() -> SecretStr:
    return SecretStr(_EPHEMERAL_SECRET_PREFIX + secrets.token_urlsafe(48))


class Settings(BaseSettings):
    """Runtime configuration loaded only from environment/.env.

    Secret values are represented as SecretStr and must never be returned by status APIs.
    """

    model_config = SettingsConfigDict(
        env_prefix="CLIPOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Zohra ClipOS"
    version: str = "1.1.0"
    environment: Environment = Environment.DEVELOPMENT
    api_prefix: str = "/api/v1"
    debug: bool = False
    log_level: str = "INFO"

    secret_key: SecretStr = Field(default_factory=_ephemeral_development_secret)
    access_token_minutes: int = Field(default=30, ge=5, le=1440)
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "zohra-clipos"
    jwt_audience: str = "clipos-api"

    database_url: str = "sqlite+aiosqlite:///./data/clipos.db"
    auto_create_schema: bool = True
    sql_echo: bool = False

    queue_backend: Literal["memory", "redis"] = "memory"
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    redis_required: bool = False

    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: Path = Path("./data/storage")
    s3_endpoint_url: SecretStr | None = None
    s3_bucket: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    max_upload_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1024, le=20 * 1024**3)
    upload_chunk_bytes: int = Field(default=1024 * 1024, ge=64 * 1024, le=8 * 1024**2)
    upload_temp_root: Path = Path("./data/tmp")
    max_media_duration_seconds: float = Field(default=6 * 60 * 60, ge=1, le=24 * 60 * 60)
    max_media_dimension: int = Field(default=8192, ge=240, le=16384)
    max_derived_audio_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024, ge=1024 * 1024, le=20 * 1024**3
    )
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    media_probe_timeout_seconds: float = Field(default=60, ge=1, le=600)
    media_extract_timeout_seconds: float = Field(default=900, ge=5, le=7200)
    media_subprocess_output_bytes: int = Field(default=1024 * 1024, ge=4096, le=16 * 1024**2)
    media_memory_limit_mb: int = Field(default=2048, ge=256, le=16384)
    media_cpu_limit_seconds: int = Field(default=900, ge=5, le=7200)

    rendering_enabled: bool = True
    render_temp_root: Path = Path("./data/render-tmp")
    render_max_duration_seconds: float = Field(default=180, ge=0.5, le=600)
    render_max_width: int = Field(default=3840, ge=240, le=8192)
    render_max_height: int = Field(default=3840, ge=240, le=8192)
    render_max_pixels: int = Field(default=8_294_400, ge=57_600, le=67_108_864)
    render_max_frame_rate: float = Field(default=60, ge=1, le=120)
    render_timeout_seconds: float = Field(default=900, ge=5, le=7200)
    render_max_output_bytes: int = Field(
        default=1024 * 1024 * 1024, ge=1024 * 1024, le=20 * 1024**3
    )
    render_min_free_disk_bytes: int = Field(
        default=512 * 1024 * 1024, ge=16 * 1024 * 1024, le=100 * 1024**3
    )
    render_duration_tolerance_seconds: float = Field(default=0.5, ge=0.05, le=3)
    render_max_active_per_tenant: int = Field(default=2, ge=1, le=20)
    render_max_queued_per_tenant: int = Field(default=20, ge=1, le=500)
    render_max_attempts: int = Field(default=3, ge=1, le=10)
    render_max_caption_segments: int = Field(default=250, ge=1, le=2000)
    render_caption_max_chars_per_line: int = Field(default=42, ge=12, le=64)
    render_caption_max_lines: Literal[1, 2] = 2
    render_caption_max_cue_seconds: float = Field(default=4.0, ge=0.5, le=10)
    render_output_audio_sample_rate: Literal[44100, 48000] = 48000
    render_output_audio_channels: Literal[1, 2] = 2
    render_target_lufs: float = Field(default=-16.0, ge=-24, le=-10)
    render_true_peak_db: float = Field(default=-1.5, ge=-6, le=-0.1)
    render_x264_preset: Literal[
        "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"
    ] = "medium"
    render_crf: int = Field(default=20, ge=14, le=30)

    quality_min_confidence: float = Field(default=0.7, ge=0, le=1)
    quality_approval_ttl_minutes: int = Field(default=1440, ge=5, le=10080)
    quality_preflight_ttl_seconds: int = Field(default=300, ge=30, le=900)

    # Phase 6 publishing is fail-closed. Enabling YouTube requires an exact OAuth client
    # configuration; unrelated providers remain disabled until their own evidence-gated phase.
    youtube_publishing_enabled: bool = False
    publishing_public_enabled: bool = False
    publishing_token_encryption_key: SecretStr | None = None
    publishing_oauth_state_ttl_seconds: int = Field(default=600, ge=120, le=1800)
    publishing_token_refresh_skew_seconds: int = Field(default=300, ge=30, le=1800)
    publishing_http_timeout_seconds: float = Field(default=60.0, ge=5, le=300)
    publishing_upload_chunk_bytes: int = Field(
        default=8 * 1024 * 1024, ge=256 * 1024, le=64 * 1024 * 1024
    )
    publishing_max_active_per_tenant: int = Field(default=2, ge=1, le=20)
    publishing_max_queued_per_tenant: int = Field(default=20, ge=1, le=500)
    publishing_max_attempts: int = Field(default=3, ge=1, le=10)
    publishing_daily_upload_limit: int = Field(default=20, ge=1, le=10000)
    publishing_processing_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    publishing_processing_max_polls: int = Field(default=5, ge=1, le=100)
    youtube_client_id: str | None = None
    youtube_client_secret: SecretStr | None = None
    youtube_redirect_uri: str = "http://localhost:8000/api/v1/publishing/oauth/youtube/callback"
    youtube_authorization_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    youtube_token_url: str = "https://oauth2.googleapis.com/token"
    youtube_revocation_url: str = "https://oauth2.googleapis.com/revoke"
    youtube_api_base_url: str = "https://www.googleapis.com/youtube/v3"
    youtube_upload_base_url: str = "https://www.googleapis.com/upload/youtube/v3"

    # Phase 7 analytics. Fail-closed like publishing: real YouTube
    # analytics stays disabled unless explicitly enabled, and even then
    # the adapter is an unverified skeleton until a provider test passes.
    # The "fake" provider exists for tests and local demos only and is
    # always reported as mocked.
    analytics_provider: Literal["disabled", "fake"] = "disabled"
    youtube_analytics_enabled: bool = False

    # Phase 8 opportunity discovery. Fail-closed: RSS fetching performs
    # outbound network requests, so it stays disabled unless the operator
    # explicitly enables it, and every fetch still passes the SSRF guard.
    # The "fake" source provider exists for tests and local demos only
    # and is always reported as mocked. Search and public-site sources
    # have no adapter and report disabled.
    opportunity_provider: Literal["disabled", "fake"] = "disabled"
    opportunity_rss_enabled: bool = False
    opportunity_fetch_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    opportunity_fetch_max_bytes: int = Field(
        default=2 * 1024 * 1024, ge=16 * 1024, le=16 * 1024 * 1024
    )
    opportunity_fetch_max_redirects: int = Field(default=3, ge=0, le=5)
    opportunity_max_import_records: int = Field(default=500, ge=1, le=5000)

    # Phase 9 sales/CRM gateways. Fail-closed by design: no email,
    # calendar or payment provider has been chosen yet, so the only
    # valid values are "disabled" and the explicitly mocked "fake"
    # (tests/local only). Naming a real provider is a configuration
    # error until the user makes — and a later phase implements — the
    # provider decisions; the validator below says exactly that.
    comms_provider: str = "disabled"
    payments_provider: str = "disabled"

    # Phase 10 executive layer. The executive dispatches only internal,
    # reversible actions from a closed registry, so there is no provider
    # to enable; this switch exists so an operator can take the whole
    # layer offline (planner, plan creation and execution queueing all
    # refuse) without touching per-tenant kill switches.
    executive_enabled: bool = True

    # v2.1 campaign economics. These are the recorded defaults a
    # profitability assessment falls back to when the operator does not
    # supply per-assessment cost inputs. They are configuration an
    # operator sets deliberately — never observed market prices — and
    # every assessment persists the exact inputs it used, so a default
    # can always be told apart from a supplied value in the audit trail.
    campaign_editing_cost_minor: int = Field(default=0, ge=0)
    campaign_model_cost_minor: int = Field(default=0, ge=0)
    campaign_distribution_cost_minor: int = Field(default=0, ge=0)
    campaign_risk_percent: int = Field(default=0, ge=0, le=100)
    campaign_max_import_records: int = Field(default=200, ge=1, le=2000)

    embedded_worker_enabled: bool = False
    worker_poll_seconds: float = Field(default=1.0, ge=0.1, le=30)
    worker_stale_job_seconds: int = Field(default=1800, ge=60, le=86_400)

    transcription_provider: Literal["disabled", "openai"] = "disabled"
    transcription_model: str | None = None
    transcription_timeout_seconds: float = Field(default=600, ge=10, le=3600)
    transcription_max_retries: int = Field(default=1, ge=0, le=3)
    transcription_response_max_bytes: int = Field(
        default=64 * 1024 * 1024, ge=1024 * 1024, le=512 * 1024 * 1024
    )
    transcript_confidence_review_threshold: float = Field(default=0.55, ge=0, le=1)
    transcript_max_text_chars: int = Field(default=5_000_000, ge=10_000, le=50_000_000)
    transcript_max_segments: int = Field(default=50_000, ge=1, le=250_000)
    transcript_max_words: int = Field(default=150_000, ge=1, le=1_000_000)
    transcript_chunk_max_chars: int = Field(default=1200, ge=200, le=10000)
    transcript_chunk_max_seconds: float = Field(default=120, ge=10, le=900)
    transcript_chunk_pause_seconds: float = Field(default=1.5, ge=0.1, le=30)

    model_provider: Literal["disabled", "openai", "anthropic", "openai_compatible"] = "disabled"
    model_timeout_seconds: float = Field(default=45.0, ge=1, le=300)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None
    model_input_cost_per_million: float = Field(default=0, ge=0)
    model_output_cost_per_million: float = Field(default=0, ge=0)
    model_cost_rates_confirmed: bool = False
    model_request_max_input_chars: int = Field(default=80_000, ge=1_000, le=2_000_000)
    model_request_max_output_tokens: int = Field(default=4_096, ge=128, le=32_768)
    model_response_max_bytes: int = Field(
        default=4 * 1024 * 1024, ge=64 * 1024, le=64 * 1024 * 1024
    )
    model_response_max_chars: int = Field(default=1_000_000, ge=10_000, le=10_000_000)

    discovery_min_duration_seconds: float = Field(default=15, ge=3, le=300)
    discovery_target_duration_seconds: float = Field(default=35, ge=3, le=300)
    discovery_max_duration_seconds: float = Field(default=60, ge=5, le=600)
    discovery_padding_seconds: float = Field(default=1.0, ge=0, le=10)
    discovery_max_candidates: int = Field(default=12, ge=1, le=50)
    discovery_pool_multiplier: int = Field(default=3, ge=1, le=10)
    discovery_overlap_threshold: float = Field(default=0.75, ge=0.1, le=1)
    discovery_similarity_threshold: float = Field(default=0.86, ge=0.1, le=1)
    discovery_max_excerpt_chars: int = Field(default=50_000, ge=1_000, le=500_000)
    discovery_model_max_input_chars: int = Field(default=40_000, ge=1_000, le=500_000)
    discovery_model_max_output_tokens: int = Field(default=2_048, ge=128, le=8_192)
    discovery_model_cost_cap: float = Field(default=0.25, ge=0, le=100)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver", "*.e2b.app"]
    )
    rate_limit_per_minute: int = Field(default=120, ge=10, le=10000)
    max_request_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized

    @field_validator("cors_origins", "trusted_hosts")
    @classmethod
    def no_empty_entries(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("at least one value is required")
        return cleaned

    @model_validator(mode="after")
    def safe_environment_defaults(self) -> Settings:
        if not (
            self.discovery_min_duration_seconds
            <= self.discovery_target_duration_seconds
            <= self.discovery_max_duration_seconds
        ):
            raise ValueError(
                "discovery duration defaults must satisfy minimum <= target <= maximum"
            )
        if self.discovery_model_max_input_chars > self.model_request_max_input_chars:
            raise ValueError(
                "CLIPOS_DISCOVERY_MODEL_MAX_INPUT_CHARS cannot exceed the gateway input limit"
            )
        if self.discovery_model_max_output_tokens > self.model_request_max_output_tokens:
            raise ValueError(
                "CLIPOS_DISCOVERY_MODEL_MAX_OUTPUT_TOKENS cannot exceed the gateway output limit"
            )
        if self.render_max_active_per_tenant > self.render_max_queued_per_tenant:
            raise ValueError("CLIPOS_RENDER_MAX_ACTIVE_PER_TENANT cannot exceed the queued limit")
        if self.publishing_max_active_per_tenant > self.publishing_max_queued_per_tenant:
            raise ValueError(
                "CLIPOS_PUBLISHING_MAX_ACTIVE_PER_TENANT cannot exceed the queued limit"
            )
        if self.publishing_upload_chunk_bytes % (256 * 1024) != 0:
            raise ValueError("CLIPOS_PUBLISHING_UPLOAD_CHUNK_BYTES must be a multiple of 256 KiB")
        publishing_urls = {
            "CLIPOS_YOUTUBE_REDIRECT_URI": self.youtube_redirect_uri,
            "CLIPOS_YOUTUBE_AUTHORIZATION_URL": self.youtube_authorization_url,
            "CLIPOS_YOUTUBE_TOKEN_URL": self.youtube_token_url,
            "CLIPOS_YOUTUBE_REVOCATION_URL": self.youtube_revocation_url,
            "CLIPOS_YOUTUBE_API_BASE_URL": self.youtube_api_base_url,
            "CLIPOS_YOUTUBE_UPLOAD_BASE_URL": self.youtube_upload_base_url,
        }
        for name, value in publishing_urls.items():
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.fragment:
                raise ValueError(f"{name} must be an absolute HTTP(S) URL without a fragment")
        if self.youtube_publishing_enabled:
            if not self.youtube_client_id or not self.youtube_client_id.strip():
                raise ValueError(
                    "CLIPOS_YOUTUBE_CLIENT_ID is required when YouTube publishing is enabled"
                )
            if not self.youtube_client_secret or not self.youtube_client_secret.get_secret_value():
                raise ValueError(
                    "CLIPOS_YOUTUBE_CLIENT_SECRET is required when YouTube publishing is enabled"
                )
        if self.youtube_analytics_enabled:
            if not self.youtube_client_id or not self.youtube_client_id.strip():
                raise ValueError(
                    "CLIPOS_YOUTUBE_CLIENT_ID is required when YouTube analytics is enabled"
                )
            if not self.youtube_client_secret or not self.youtube_client_secret.get_secret_value():
                raise ValueError(
                    "CLIPOS_YOUTUBE_CLIENT_SECRET is required when YouTube analytics is enabled"
                )
            if (
                self.publishing_token_encryption_key is None
                or len(self.publishing_token_encryption_key.get_secret_value()) < 32
            ):
                raise ValueError(
                    "CLIPOS_PUBLISHING_TOKEN_ENCRYPTION_KEY must contain at least 32 "
                    "characters when YouTube analytics is enabled"
                )
        if self.analytics_provider == "fake" and self.environment in {
            Environment.STAGING,
            Environment.PRODUCTION,
        }:
            raise ValueError(
                "CLIPOS_ANALYTICS_PROVIDER=fake is forbidden in staging/production; "
                "mocked metrics must never be served as production evidence"
            )
        if self.opportunity_provider == "fake" and self.environment in {
            Environment.STAGING,
            Environment.PRODUCTION,
        }:
            raise ValueError(
                "CLIPOS_OPPORTUNITY_PROVIDER=fake is forbidden in staging/production; "
                "mocked opportunities must never be served as production evidence"
            )
        if self.comms_provider not in {"disabled", "fake"}:
            raise ValueError(
                f"CLIPOS_COMMS_PROVIDER={self.comms_provider!r} names a real communications "
                "provider, but no email provider decision has been made for Phase 9: the "
                "email provider, calendar provider and payment provider decisions are "
                "still owed by the user, and no provider integration exists in this "
                "phase. Communications stay disabled (or the mocked fake in tests/local) "
                "until those decisions are made and a provider is reviewed and integrated."
            )
        if self.payments_provider not in {"disabled", "fake"}:
            raise ValueError(
                f"CLIPOS_PAYMENTS_PROVIDER={self.payments_provider!r} names a real payment "
                "provider, but no payment provider decision has been made for Phase 9: "
                "the payment provider decision is still owed by the user, and no "
                "provider integration exists in this phase. Payments stay disabled "
                "(or the mocked fake in tests/local) until that decision is made and a "
                "provider is reviewed and integrated in test mode."
            )
        if self.comms_provider == "fake" and self.environment in {
            Environment.STAGING,
            Environment.PRODUCTION,
        }:
            raise ValueError(
                "CLIPOS_COMMS_PROVIDER=fake is forbidden in staging/production; "
                "mocked communications must never be served as production evidence"
            )
        if self.payments_provider == "fake" and self.environment in {
            Environment.STAGING,
            Environment.PRODUCTION,
        }:
            raise ValueError(
                "CLIPOS_PAYMENTS_PROVIDER=fake is forbidden in staging/production; "
                "mocked payments must never be served as production evidence"
            )
        storage_root = self.storage_local_root.resolve()
        render_temp_root = self.render_temp_root.resolve()
        if (
            storage_root == render_temp_root
            or storage_root in render_temp_root.parents
            or render_temp_root in storage_root.parents
        ):
            raise ValueError("CLIPOS_RENDER_TEMP_ROOT and private object storage must not overlap")
        transcription_attempt_budget = (
            self.transcription_timeout_seconds * (self.transcription_max_retries + 1)
            + self.transcription_max_retries * 4.1
        )
        minimum_stale_window = int(
            max(
                self.media_probe_timeout_seconds,
                self.media_extract_timeout_seconds,
                self.render_timeout_seconds,
                transcription_attempt_budget,
            )
            + 60
        )
        if self.worker_stale_job_seconds < minimum_stale_window:
            raise ValueError(
                "CLIPOS_WORKER_STALE_JOB_SECONDS must exceed the longest processing retry budget"
            )
        protected = {Environment.STAGING, Environment.PRODUCTION}
        if self.environment in protected:
            if self.secret_key.get_secret_value().startswith(_EPHEMERAL_SECRET_PREFIX):
                raise ValueError("CLIPOS_SECRET_KEY must be set outside development/test")
            if len(self.secret_key.get_secret_value()) < 32:
                raise ValueError("CLIPOS_SECRET_KEY must contain at least 32 characters")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise ValueError("SQLite/non-PostgreSQL URLs are forbidden; use postgresql+asyncpg")
            if self.auto_create_schema:
                raise ValueError("automatic schema creation is forbidden in staging/production")
            if "*" in self.cors_origins:
                raise ValueError("wildcard CORS is forbidden in staging/production")
            if self.queue_backend != "redis":
                raise ValueError("Redis queue backend is required in staging/production")
            if not self.redis_required:
                raise ValueError("Redis readiness must be required in staging/production")
            if self.embedded_worker_enabled:
                raise ValueError("embedded workers are forbidden in staging/production")
            if self.youtube_publishing_enabled:
                for name, value in publishing_urls.items():
                    if urlsplit(value).scheme != "https":
                        raise ValueError(
                            f"{name} must use HTTPS when YouTube publishing is active in a "
                            "protected environment"
                        )
                if (
                    self.publishing_token_encryption_key is None
                    or len(self.publishing_token_encryption_key.get_secret_value()) < 32
                ):
                    raise ValueError(
                        "CLIPOS_PUBLISHING_TOKEN_ENCRYPTION_KEY must contain at least 32 "
                        "characters when publishing is active in staging/production"
                    )
        if self.queue_backend == "redis" and not self.redis_url.get_secret_value():
            raise ValueError("CLIPOS_REDIS_URL is required for the Redis queue backend")
        if self.storage_backend == "s3":
            raise ValueError("S3 storage is not implemented in the current phase")
        openai_required = (
            self.model_provider
            in {
                "openai",
                "openai_compatible",
            }
            or self.transcription_provider == "openai"
        )
        if openai_required and (
            not self.openai_api_key or not self.openai_api_key.get_secret_value()
        ):
            raise ValueError("CLIPOS_OPENAI_API_KEY is required for selected provider")
        if self.model_provider != "disabled" and not self.model_cost_rates_confirmed:
            raise ValueError(
                "CLIPOS_MODEL_COST_RATES_CONFIRMED must be true after verifying the configured "
                "input/output rates (including deliberately free rates)"
            )
        if self.model_provider in {"openai", "openai_compatible"} and not self.openai_model:
            raise ValueError("CLIPOS_OPENAI_MODEL is required for selected model provider")
        if self.transcription_provider == "openai" and not self.transcription_model:
            raise ValueError(
                "CLIPOS_TRANSCRIPTION_MODEL is required for the OpenAI transcription provider"
            )
        if self.model_provider == "anthropic":
            if not self.anthropic_api_key or not self.anthropic_api_key.get_secret_value():
                raise ValueError("CLIPOS_ANTHROPIC_API_KEY is required for selected model provider")
            if not self.anthropic_model:
                raise ValueError("CLIPOS_ANTHROPIC_MODEL is required for selected model provider")
        return self

    @property
    def is_protected_environment(self) -> bool:
        return self.environment in {Environment.STAGING, Environment.PRODUCTION}

    @property
    def max_upload_request_bytes(self) -> int:
        # Multipart boundaries and rights fields require bounded overhead beyond file bytes.
        return self.max_upload_bytes + 10 * 1024 * 1024

    def configuration_status(self) -> list[dict[str, str | bool]]:
        """Return names/status only. This method deliberately cannot return values."""
        checks: list[tuple[str, bool, bool, bool]] = [
            (
                "CLIPOS_SECRET_KEY",
                not self.secret_key.get_secret_value().startswith(_EPHEMERAL_SECRET_PREFIX),
                True,
                True,
            ),
            ("CLIPOS_DATABASE_URL", bool(self.database_url), True, True),
            (
                "CLIPOS_REDIS_URL",
                bool(self.redis_url.get_secret_value()),
                self.queue_backend == "redis",
                True,
            ),
            (
                "CLIPOS_OPENAI_API_KEY",
                bool(self.openai_api_key and self.openai_api_key.get_secret_value()),
                self.model_provider in {"openai", "openai_compatible"}
                or self.transcription_provider == "openai",
                True,
            ),
            (
                "CLIPOS_ANTHROPIC_API_KEY",
                bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value()),
                self.model_provider == "anthropic",
                True,
            ),
            (
                "CLIPOS_OPENAI_MODEL",
                bool(self.openai_model),
                self.model_provider in {"openai", "openai_compatible"},
                False,
            ),
            (
                "CLIPOS_ANTHROPIC_MODEL",
                bool(self.anthropic_model),
                self.model_provider == "anthropic",
                False,
            ),
            (
                "CLIPOS_MODEL_COST_RATES_CONFIRMED",
                self.model_cost_rates_confirmed,
                self.model_provider != "disabled",
                False,
            ),
            (
                "CLIPOS_TRANSCRIPTION_MODEL",
                bool(self.transcription_model),
                self.transcription_provider == "openai",
                False,
            ),
            ("CLIPOS_FFMPEG_PATH", bool(self.ffmpeg_path), True, False),
            ("CLIPOS_FFPROBE_PATH", bool(self.ffprobe_path), True, False),
            ("CLIPOS_RENDERING_ENABLED", self.rendering_enabled, True, False),
            (
                "CLIPOS_RENDER_TEMP_ROOT",
                bool(str(self.render_temp_root)),
                self.rendering_enabled,
                False,
            ),
            (
                "CLIPOS_S3_ACCESS_KEY_ID",
                bool(self.s3_access_key_id and self.s3_access_key_id.get_secret_value()),
                self.storage_backend == "s3",
                True,
            ),
            (
                "CLIPOS_S3_SECRET_ACCESS_KEY",
                bool(self.s3_secret_access_key and self.s3_secret_access_key.get_secret_value()),
                self.storage_backend == "s3",
                True,
            ),
            (
                "CLIPOS_YOUTUBE_PUBLISHING_ENABLED",
                self.youtube_publishing_enabled,
                False,
                False,
            ),
            (
                "CLIPOS_ANALYTICS_PROVIDER",
                self.analytics_provider != "disabled",
                False,
                False,
            ),
            (
                "CLIPOS_YOUTUBE_ANALYTICS_ENABLED",
                self.youtube_analytics_enabled,
                False,
                False,
            ),
            (
                "CLIPOS_OPPORTUNITY_PROVIDER",
                self.opportunity_provider != "disabled",
                False,
                False,
            ),
            (
                "CLIPOS_OPPORTUNITY_RSS_ENABLED",
                self.opportunity_rss_enabled,
                False,
                False,
            ),
            (
                "CLIPOS_COMMS_PROVIDER",
                self.comms_provider != "disabled",
                False,
                False,
            ),
            (
                "CLIPOS_PAYMENTS_PROVIDER",
                self.payments_provider != "disabled",
                False,
                False,
            ),
            (
                "CLIPOS_EXECUTIVE_ENABLED",
                self.executive_enabled,
                False,
                False,
            ),
            (
                "CLIPOS_YOUTUBE_CLIENT_ID",
                bool(self.youtube_client_id),
                self.youtube_publishing_enabled,
                False,
            ),
            (
                "CLIPOS_YOUTUBE_CLIENT_SECRET",
                bool(self.youtube_client_secret and self.youtube_client_secret.get_secret_value()),
                self.youtube_publishing_enabled,
                True,
            ),
            (
                "CLIPOS_YOUTUBE_REDIRECT_URI",
                bool(self.youtube_redirect_uri),
                self.youtube_publishing_enabled,
                False,
            ),
            (
                "CLIPOS_PUBLISHING_TOKEN_ENCRYPTION_KEY",
                bool(
                    self.publishing_token_encryption_key
                    and self.publishing_token_encryption_key.get_secret_value()
                ),
                self.youtube_publishing_enabled and self.is_protected_environment,
                True,
            ),
            (
                "CLIPOS_PUBLISHING_PUBLIC_ENABLED",
                self.publishing_public_enabled,
                False,
                False,
            ),
        ]
        return [
            {"name": name, "configured": configured, "required": required, "secret": secret}
            for name, configured, required, secret in checks
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
