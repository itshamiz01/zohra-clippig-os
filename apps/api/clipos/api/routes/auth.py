from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from clipos.api.deps import get_principal, get_runtime_settings, get_session
from clipos.config import Settings
from clipos.core.security import Principal
from clipos.schemas import LoginRequest, PrincipalResponse, TokenResponse
from clipos.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> TokenResponse:
    token, _ = await AuthService.authenticate(session, request, settings)
    return token


@router.get("/me", response_model=PrincipalResponse)
async def me(principal: Principal = Depends(get_principal)) -> PrincipalResponse:
    return PrincipalResponse(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        role=principal.role,
        email=principal.email,
        is_platform_admin=principal.is_platform_admin,
    )
