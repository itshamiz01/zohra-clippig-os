from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from clipos.config import Settings
from clipos.core.enums import Environment, MembershipStatus, Role, TenantStatus
from clipos.core.security import hash_password
from clipos.main import create_app
from clipos.models import Tenant, TenantMembership, User

TEST_PASSWORD = "Correct-Horse-ClipOS-47"


@pytest.fixture(scope="session")
def encoded_password() -> str:
    return hash_password(TEST_PASSWORD)


@pytest.fixture(scope="session")
def media_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None or shutil.which("ffprobe") is None:
        pytest.skip("real FFmpeg/FFprobe are required for Phase 2 media tests")
    root = tmp_path_factory.mktemp("real-media-shared")
    audio = root / "tone.wav"
    video = root / "tone.mp4"
    silent_video = root / "silent.mp4"
    unsupported_audio = root / "unsupported.wav"
    subprocess.run(  # noqa: S603 - fixed trusted test binary
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1.2",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(audio),
        ],
        check=True,
        timeout=20,
    )
    subprocess.run(  # noqa: S603 - fixed trusted test binary
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=1.0",
            "-c:a",
            "pcm_u8",
            "-y",
            str(unsupported_audio),
        ],
        check=True,
        timeout=20,
    )
    subprocess.run(  # noqa: S603 - fixed trusted test binary
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=1.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(video),
        ],
        check=True,
        timeout=20,
    )
    subprocess.run(  # noqa: S603 - fixed trusted test binary
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:d=1.0",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(silent_video),
        ],
        check=True,
        timeout=20,
    )
    return {
        "audio": audio,
        "video": video,
        "silent_video": silent_video,
        "unsupported_audio": unsupported_audio,
    }


@pytest.fixture
async def app_client(
    tmp_path: Path, encoded_password: str
) -> AsyncIterator[tuple[AsyncClient, Any, dict[str, Any]]]:
    settings = Settings(
        _env_file=None,
        environment=Environment.TEST,
        secret_key="test-secret-that-is-at-least-thirty-two-characters",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        auto_create_schema=True,
        queue_backend="memory",
        storage_backend="local",
        storage_local_root=tmp_path / "storage",
        upload_temp_root=tmp_path / "upload-tmp",
        render_temp_root=tmp_path / "render-tmp",
        render_max_output_bytes=64 * 1024 * 1024,
        render_min_free_disk_bytes=64 * 1024 * 1024,
        model_provider="disabled",
        trusted_hosts=["testserver"],
        cors_origins=["http://testserver"],
        rate_limit_per_minute=1000,
    )
    app = create_app(settings)
    async with LifespanManager(app):
        factory = app.state.session_factory
        async with factory() as session:
            tenant_a = Tenant(slug="alpha", name="Alpha", status=TenantStatus.ACTIVE)
            tenant_b = Tenant(slug="bravo", name="Bravo", status=TenantStatus.ACTIVE)
            users = {
                "owner": User(
                    email="owner.alpha@example.com",
                    display_name="Alpha Owner",
                    password_hash=encoded_password,
                    is_active=True,
                    is_platform_admin=True,
                ),
                "reviewer": User(
                    email="reviewer.alpha@example.com",
                    display_name="Alpha Reviewer",
                    password_hash=encoded_password,
                    is_active=True,
                ),
                "editor": User(
                    email="editor.alpha@example.com",
                    display_name="Alpha Editor",
                    password_hash=encoded_password,
                    is_active=True,
                ),
                "viewer": User(
                    email="viewer.alpha@example.com",
                    display_name="Alpha Viewer",
                    password_hash=encoded_password,
                    is_active=True,
                ),
                "bravo_owner": User(
                    email="owner.bravo@example.com",
                    display_name="Bravo Owner",
                    password_hash=encoded_password,
                    is_active=True,
                ),
            }
            session.add_all([tenant_a, tenant_b, *users.values()])
            await session.flush()
            session.add_all(
                [
                    TenantMembership(
                        tenant_id=tenant_a.id,
                        user_id=users["owner"].id,
                        role=Role.OWNER,
                        status=MembershipStatus.ACTIVE,
                    ),
                    TenantMembership(
                        tenant_id=tenant_a.id,
                        user_id=users["reviewer"].id,
                        role=Role.REVIEWER,
                        status=MembershipStatus.ACTIVE,
                    ),
                    TenantMembership(
                        tenant_id=tenant_a.id,
                        user_id=users["editor"].id,
                        role=Role.EDITOR,
                        status=MembershipStatus.ACTIVE,
                    ),
                    TenantMembership(
                        tenant_id=tenant_a.id,
                        user_id=users["viewer"].id,
                        role=Role.VIEWER,
                        status=MembershipStatus.ACTIVE,
                    ),
                    TenantMembership(
                        tenant_id=tenant_b.id,
                        user_id=users["bravo_owner"].id,
                        role=Role.OWNER,
                        status=MembershipStatus.ACTIVE,
                    ),
                ]
            )
            await session.commit()
            ids = {
                "tenant_a": tenant_a.id,
                "tenant_b": tenant_b.id,
                **{name: user.id for name, user in users.items()},
            }
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client, app, ids


@pytest.fixture
async def auth_headers(
    app_client: tuple[AsyncClient, Any, dict[str, Any]],
) -> Callable[[str], Coroutine[Any, Any, dict[str, str]]]:
    client, _, _ = app_client
    identities = {
        "owner": ("owner.alpha@example.com", "alpha"),
        "reviewer": ("reviewer.alpha@example.com", "alpha"),
        "editor": ("editor.alpha@example.com", "alpha"),
        "viewer": ("viewer.alpha@example.com", "alpha"),
        "bravo_owner": ("owner.bravo@example.com", "bravo"),
    }

    async def get(identity: str) -> dict[str, str]:
        email, tenant_slug = identities[identity]
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": TEST_PASSWORD, "tenant_slug": tenant_slug},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return get
