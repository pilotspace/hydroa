import time
import uuid

import jwt

from gateway.core.config import Settings
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import InvalidTokenError


class JwtTokenService:
    """Implements domain port TokenService (HS256; contract tenant-identity §3)."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret
        self._ttl = settings.jwt_ttl_seconds
        self._issuer = settings.jwt_issuer

    def issue(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: Role, email: str
    ) -> tuple[str, int]:
        now = int(time.time())
        claims = {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "role": str(role),
            "email": email,
            "iat": now,
            "exp": now + self._ttl,
            "iss": self._issuer,
        }
        return jwt.encode(claims, self._secret, algorithm="HS256"), self._ttl

    def decode(self, token: str) -> Identity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["sub", "tenant_id", "role", "email", "exp", "iat", "iss"]},
            )
            return Identity(
                user_id=uuid.UUID(claims["sub"]),
                tenant_id=uuid.UUID(claims["tenant_id"]),
                email=claims["email"],
                role=Role(claims["role"]),
            )
        except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
            raise InvalidTokenError from exc
