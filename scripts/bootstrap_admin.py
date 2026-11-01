"""Interactive one-time bootstrap for the first tenant owner.

Run only after migrations. The password is read from a hidden terminal prompt and is never logged.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import re

from sqlalchemy import select

from clipos.config import get_settings
from clipos.core.enums import MembershipStatus, Role, TenantStatus
from clipos.core.security import hash_password
from clipos.database import build_engine, build_session_factory
from clipos.models import Tenant, TenantMembership, User


async def bootstrap(*, slug: str, tenant_name: str, email: str, display_name: str) -> None:
    settings = get_settings()
    password = getpass.getpass("Owner password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    password_hash = hash_password(password)
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    async with factory() as session:
        if await session.scalar(select(User).where(User.is_platform_admin.is_(True))):
            raise SystemExit(
                "A platform administrator already exists; use a reviewed admin workflow"
            )
        if await session.scalar(select(Tenant).where(Tenant.slug == slug)):
            raise SystemExit("Tenant slug already exists")
        normalized_email = email.strip().lower()
        if await session.scalar(select(User).where(User.email == normalized_email)):
            raise SystemExit("User email already exists; add membership through an admin workflow")
        tenant = Tenant(slug=slug, name=tenant_name, status=TenantStatus.ACTIVE)
        user = User(
            email=normalized_email,
            display_name=display_name,
            password_hash=password_hash,
            is_active=True,
            is_platform_admin=True,
        )
        session.add_all([tenant, user])
        await session.flush()
        session.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                role=Role.OWNER,
                status=MembershipStatus.ACTIVE,
            )
        )
        await session.commit()
    await engine.dispose()
    print(f"Created owner {normalized_email} for tenant {slug}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,79}", args.slug):
        raise SystemExit("Slug must be lowercase letters, numbers, and hyphens")
    asyncio.run(
        bootstrap(
            slug=args.slug,
            tenant_name=args.tenant_name,
            email=args.email,
            display_name=args.display_name,
        )
    )


if __name__ == "__main__":
    main()
