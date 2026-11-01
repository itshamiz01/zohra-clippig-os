from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.config import Settings
from clipos.core.enums import ProviderMode
from clipos.models import ProviderStatus


class ProviderStatusService:
    @staticmethod
    async def seed(session: AsyncSession, settings: Settings) -> None:
        selected_mode = (
            ProviderMode.UNVERIFIED
            if settings.model_provider != "disabled"
            else ProviderMode.DISABLED
        )
        model_variables: dict[str, list[str]] = {
            "openai": [
                "CLIPOS_OPENAI_API_KEY",
                "CLIPOS_OPENAI_MODEL",
                "CLIPOS_MODEL_INPUT_COST_PER_MILLION",
                "CLIPOS_MODEL_OUTPUT_COST_PER_MILLION",
                "CLIPOS_MODEL_COST_RATES_CONFIRMED",
            ],
            "openai_compatible": [
                "CLIPOS_OPENAI_API_KEY",
                "CLIPOS_OPENAI_MODEL",
                "CLIPOS_OPENAI_BASE_URL",
                "CLIPOS_MODEL_INPUT_COST_PER_MILLION",
                "CLIPOS_MODEL_OUTPUT_COST_PER_MILLION",
                "CLIPOS_MODEL_COST_RATES_CONFIRMED",
            ],
            "anthropic": [
                "CLIPOS_ANTHROPIC_API_KEY",
                "CLIPOS_ANTHROPIC_MODEL",
                "CLIPOS_MODEL_INPUT_COST_PER_MILLION",
                "CLIPOS_MODEL_OUTPUT_COST_PER_MILLION",
                "CLIPOS_MODEL_COST_RATES_CONFIRMED",
            ],
            "disabled": [],
        }
        database_provider = (
            "postgresql" if settings.database_url.startswith("postgresql") else "sqlite"
        )
        definitions = [
            (
                database_provider,
                "database",
                ProviderMode.UNVERIFIED
                if database_provider == "postgresql"
                else ProviderMode.REAL_TESTED,
                ["CLIPOS_DATABASE_URL"],
            ),
            (
                settings.model_provider,
                "text_and_structured_generation",
                selected_mode,
                model_variables[settings.model_provider],
            ),
            (
                settings.transcription_provider,
                "audio_transcription",
                ProviderMode.UNVERIFIED
                if settings.transcription_provider != "disabled"
                else ProviderMode.DISABLED,
                ["CLIPOS_OPENAI_API_KEY", "CLIPOS_TRANSCRIPTION_MODEL"]
                if settings.transcription_provider == "openai"
                else [],
            ),
            (
                "ffmpeg",
                "media_probe_and_audio_extraction",
                ProviderMode.UNVERIFIED,
                ["CLIPOS_FFMPEG_PATH", "CLIPOS_FFPROBE_PATH"],
            ),
            (
                "ffmpeg",
                "media_rendering",
                ProviderMode.UNVERIFIED if settings.rendering_enabled else ProviderMode.DISABLED,
                [
                    "CLIPOS_FFMPEG_PATH",
                    "CLIPOS_FFPROBE_PATH",
                    "CLIPOS_RENDER_TEMP_ROOT",
                ],
            ),
            (
                "local_upload",
                "ingestion_source",
                ProviderMode.REAL_TESTED,
                ["CLIPOS_MAX_UPLOAD_BYTES"],
            ),
            (
                "url",
                "ingestion_source",
                ProviderMode.DISABLED,
                [],
            ),
            (
                settings.storage_backend,
                "object_storage",
                ProviderMode.UNVERIFIED
                if settings.storage_backend == "s3"
                else ProviderMode.REAL_TESTED,
                [
                    "CLIPOS_S3_ENDPOINT_URL",
                    "CLIPOS_S3_BUCKET",
                    "CLIPOS_S3_ACCESS_KEY_ID",
                    "CLIPOS_S3_SECRET_ACCESS_KEY",
                ]
                if settings.storage_backend == "s3"
                else ["CLIPOS_STORAGE_LOCAL_ROOT"],
            ),
            (
                settings.queue_backend,
                "job_queue",
                ProviderMode.UNVERIFIED
                if settings.queue_backend == "redis"
                else ProviderMode.MOCKED,
                ["CLIPOS_REDIS_URL"] if settings.queue_backend == "redis" else [],
            ),
            (
                settings.queue_backend,
                "rate_limiter",
                ProviderMode.UNVERIFIED
                if settings.queue_backend == "redis"
                else ProviderMode.REAL_TESTED,
                ["CLIPOS_REDIS_URL"] if settings.queue_backend == "redis" else [],
            ),
            (
                "youtube",
                "oauth_and_resumable_publishing",
                ProviderMode.UNVERIFIED
                if settings.youtube_publishing_enabled
                else ProviderMode.DISABLED,
                [
                    "CLIPOS_YOUTUBE_CLIENT_ID",
                    "CLIPOS_YOUTUBE_CLIENT_SECRET",
                    "CLIPOS_YOUTUBE_REDIRECT_URI",
                    "CLIPOS_PUBLISHING_TOKEN_ENCRYPTION_KEY",
                ],
            ),
            ("meta", "oauth_and_publishing", ProviderMode.DISABLED, []),
            ("tiktok", "oauth_and_publishing", ProviderMode.DISABLED, []),
            ("linkedin", "oauth_and_publishing", ProviderMode.DISABLED, []),
        ]
        for provider, capability, mode, variable_names in definitions:
            existing = await session.scalar(
                select(ProviderStatus).where(
                    ProviderStatus.provider == provider,
                    ProviderStatus.capability == capability,
                )
            )
            if existing is None:
                session.add(
                    ProviderStatus(
                        provider=provider,
                        capability=capability,
                        mode=mode,
                        required_variable_names=variable_names,
                    )
                )
            else:
                if mode in {ProviderMode.DISABLED, ProviderMode.REAL_TESTED, ProviderMode.MOCKED}:
                    existing.mode = mode
                elif existing.mode not in {ProviderMode.REAL_TESTED, ProviderMode.BLOCKED}:
                    existing.mode = mode
                existing.required_variable_names = variable_names
        await session.commit()

    @staticmethod
    async def update_runtime_status(
        session: AsyncSession,
        *,
        provider: str,
        capability: str,
        mode: ProviderMode,
        error_category: str | None = None,
    ) -> None:
        status = await session.scalar(
            select(ProviderStatus).where(
                ProviderStatus.provider == provider,
                ProviderStatus.capability == capability,
            )
        )
        if status is None:
            status = ProviderStatus(
                provider=provider,
                capability=capability,
                mode=mode,
                required_variable_names=[],
            )
            session.add(status)
        status.mode = mode
        status.last_checked_at = datetime.now(UTC)
        status.last_error_category = error_category
        await session.commit()

    @staticmethod
    async def list(session: AsyncSession) -> list[ProviderStatus]:
        return list(
            (
                await session.scalars(
                    select(ProviderStatus).order_by(
                        ProviderStatus.capability, ProviderStatus.provider
                    )
                )
            ).all()
        )
