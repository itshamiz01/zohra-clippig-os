from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from clipos.config import Settings
from clipos.core.enums import Environment, Role
from clipos.core.errors import AuthenticationError
from clipos.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

pytestmark = pytest.mark.unit


def test_production_rejects_development_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            database_url="postgresql+asyncpg://example/clipos",
            auto_create_schema=False,
            queue_backend="redis",
            redis_required=True,
        )


def test_protected_environment_rejects_sqlite() -> None:
    with pytest.raises(ValidationError, match="SQLite"):
        Settings(
            _env_file=None,
            environment=Environment.STAGING,
            secret_key="a-secure-staging-secret-that-is-long-enough",
            database_url="sqlite+aiosqlite:///unsafe.db",
            auto_create_schema=False,
            queue_backend="redis",
            redis_required=True,
        )


def test_unimplemented_s3_backend_fails_closed() -> None:
    with pytest.raises(ValidationError, match="not implemented"):
        Settings(_env_file=None, storage_backend="s3")


def test_worker_stale_lease_covers_full_transcription_retry_budget() -> None:
    with pytest.raises(ValidationError, match="retry budget"):
        Settings(
            _env_file=None,
            transcription_timeout_seconds=600,
            transcription_max_retries=3,
            worker_stale_job_seconds=1800,
        )
    settings = Settings(
        _env_file=None,
        transcription_timeout_seconds=600,
        transcription_max_retries=3,
        worker_stale_job_seconds=2500,
    )
    assert settings.worker_stale_job_seconds == 2500


def test_render_temp_and_private_storage_roots_cannot_overlap(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(
            _env_file=None,
            storage_local_root=tmp_path / "private" / "storage",
            render_temp_root=tmp_path / "private",
        )
    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(
            _env_file=None,
            storage_local_root=tmp_path / "private",
            render_temp_root=tmp_path / "private" / "render-tmp",
        )


def test_development_secret_is_ephemeral_when_not_configured() -> None:
    first = Settings(_env_file=None)
    second = Settings(_env_file=None)
    assert first.secret_key.get_secret_value() != second.secret_key.get_secret_value()
    status = {item["name"]: item for item in first.configuration_status()}
    assert status["CLIPOS_SECRET_KEY"]["configured"] is False


def test_configuration_status_never_returns_values() -> None:
    marker = "super-private-marker-value"
    settings = Settings(_env_file=None, secret_key=marker, openai_api_key=marker)
    rendered = repr(settings.configuration_status())
    assert marker not in rendered
    assert "CLIPOS_SECRET_KEY" in rendered


def test_enabled_model_provider_requires_confirmed_cost_rates() -> None:
    common = {
        "_env_file": None,
        "model_provider": "openai",
        "openai_api_key": "fixture-key",
        "openai_model": "fixture-model",
    }
    with pytest.raises(ValidationError, match="COST_RATES_CONFIRMED"):
        Settings(**common)
    settings = Settings(**common, model_cost_rates_confirmed=True)
    assert settings.model_cost_rates_confirmed is True
    status = {item["name"]: item for item in settings.configuration_status()}
    assert status["CLIPOS_MODEL_COST_RATES_CONFIRMED"]["required"] is True
    assert status["CLIPOS_MODEL_COST_RATES_CONFIRMED"]["configured"] is True


def test_password_hash_and_verification() -> None:
    encoded = hash_password("A-long-and-acceptable-password")
    assert "A-long-and-acceptable-password" not in encoded
    assert verify_password("A-long-and-acceptable-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_password_rejects_short_values() -> None:
    with pytest.raises(ValueError, match="12"):
        hash_password("too-short")


def test_access_token_round_trip_and_expiry() -> None:
    settings = Settings(
        _env_file=None,
        secret_key="a-test-secret-that-is-longer-than-thirty-two",
        access_token_minutes=30,
    )
    user_id, tenant_id = uuid4(), uuid4()
    token, _ = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=Role.OWNER,
        settings=settings,
    )
    claims = decode_access_token(token, settings)
    assert claims.sub == user_id
    assert claims.tid == tenant_id
    assert claims.role == Role.OWNER

    expired_payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": Role.OWNER.value,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
        "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        "jti": str(uuid4()),
    }
    expired = jwt.encode(
        expired_payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(expired, settings)
