from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.config import Settings
from clipos.core.enums import MembershipStatus, TenantStatus
from clipos.core.errors import AuthenticationError
from clipos.core.security import Principal, create_access_token, verify_password_or_dummy
from clipos.models import Tenant, TenantMembership, User
from clipos.schemas import LoginRequest, TokenResponse
from clipos.services.audit import AuditService


class AuthService:
    @staticmethod
    async def authenticate(
        session: AsyncSession, request: LoginRequest, settings: Settings
    ) -> tuple[TokenResponse, Principal]:
        query = (
            select(User, Tenant, TenantMembership)
            .join(TenantMembership, TenantMembership.user_id == User.id)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                User.email == str(request.email).lower(),
                Tenant.slug == request.tenant_slug,
                User.is_active.is_(True),
                Tenant.status == TenantStatus.ACTIVE,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
        )
        row = (await session.execute(query)).one_or_none()
        password_hash = row[0].password_hash if row is not None else None
        password_valid = verify_password_or_dummy(request.password, password_hash)
        if row is None or not password_valid:
            raise AuthenticationError("invalid email, password, or tenant")
        user, tenant, membership = row
        token, expires_at = create_access_token(
            user_id=user.id,
            tenant_id=tenant.id,
            role=membership.role,
            settings=settings,
        )
        principal = Principal(
            user_id=user.id,
            tenant_id=tenant.id,
            role=membership.role,
            email=user.email,
            is_platform_admin=user.is_platform_admin,
        )
        AuditService.record(
            session,
            tenant_id=tenant.id,
            actor_user_id=user.id,
            action="auth.login",
            resource_type="user",
            resource_id=str(user.id),
        )
        await session.commit()
        return TokenResponse(access_token=token, expires_at=expires_at), principal
