from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clipos.config import Settings
from clipos.core.enums import MembershipStatus, Role, TenantStatus
from clipos.core.errors import AuthenticationError, AuthorizationError
from clipos.core.security import Principal, decode_access_token
from clipos.models import Tenant, TenantMembership, User

bearer = HTTPBearer(auto_error=False)


def get_runtime_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_runtime_settings),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    claims = decode_access_token(credentials.credentials, settings)
    row = (
        await session.execute(
            select(User, Tenant, TenantMembership)
            .join(TenantMembership, TenantMembership.user_id == User.id)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                User.id == claims.sub,
                Tenant.id == claims.tid,
                User.is_active.is_(True),
                Tenant.status == TenantStatus.ACTIVE,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
        )
    ).one_or_none()
    if row is None:
        raise AuthenticationError("account or tenant membership is inactive")
    user, tenant, membership = row
    # Database role is authoritative. A token with a stale or altered role is rejected.
    if membership.role != claims.role:
        raise AuthenticationError("token role is stale; sign in again")
    return Principal(
        user_id=user.id,
        tenant_id=tenant.id,
        role=membership.role,
        email=user.email,
        is_platform_admin=user.is_platform_admin,
    )


def require_role(minimum: Role) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        try:
            principal.require(minimum)
        except AuthorizationError:
            raise
        return principal

    return dependency
