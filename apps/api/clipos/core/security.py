from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel, ConfigDict

from clipos.config import Settings
from clipos.core.enums import ROLE_RANK, Role
from clipos.core.errors import AuthenticationError, AuthorizationError

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
_dummy_password_hash = _password_hasher.hash("not-a-real-user-password")


class TokenClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub: UUID
    tid: UUID
    iss: str
    aud: str
    role: Role
    type: str
    iat: int
    exp: int
    jti: UUID


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    role: Role
    email: str
    is_platform_admin: bool = False

    def require(self, minimum: Role) -> None:
        if ROLE_RANK[self.role] < ROLE_RANK[minimum]:
            raise AuthorizationError(f"role {minimum.value} or higher is required")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    return _password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _password_hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_password_or_dummy(password: str, encoded: str | None) -> bool:
    """Spend password-verification work even when no account row exists."""
    return verify_password(password, encoded or _dummy_password_hash)


def create_access_token(
    *,
    user_id: UUID,
    tenant_id: UUID,
    role: Role,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(minutes=settings.access_token_minutes)
    claims = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "role": role.value,
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid4()),
    }
    token = jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, expires


def decode_access_token(token: str, settings: Settings) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": [
                    "sub",
                    "tid",
                    "role",
                    "type",
                    "iss",
                    "aud",
                    "iat",
                    "exp",
                    "jti",
                ]
            },
        )
        claims = TokenClaims.model_validate(payload)
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationError("invalid or expired access token") from exc
    if claims.type != "access":
        raise AuthenticationError("invalid token type")
    return claims
