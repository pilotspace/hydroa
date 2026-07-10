import time
import uuid

import jwt
import structlog.contextvars

from gateway.core.config import Settings
from gateway.tenants.domain.entities import Identity, ImpersonationContext, Role
from gateway.tenants.domain.errors import InvalidTokenError


class JwtTokenService:
    """Implements domain port TokenService (HS256; contract tenant-identity §3)."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret
        self._ttl = settings.jwt_ttl_seconds
        self._issuer = settings.jwt_issuer

    def issue(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: Role,
        email: str,
        # NEW optional kwargs (impersonation-session-lifecycle TASK.md §3 Part B,
        # FROZEN @ v1). Neither is ever passed by an EXISTING caller (auth/application/
        # use_cases.py, tenants/application/use_cases.py — both keyword-only, confirmed
        # the only 2 call sites), so this stays byte-identical for every ordinary issue().
        impersonation: ImpersonationContext | None = None,
        ttl_seconds: int | None = None,
    ) -> tuple[str, int]:
        now = int(time.time())
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        # Explicit dict[str, object] (not inferred dict[str, str | int]): the optional
        # "impersonation" key below nests a dict value, which the narrower inferred type
        # from the literal alone would reject under strict pyright.
        claims: dict[str, object] = {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "role": str(role),
            "email": email,
            "iat": now,
            "exp": now + ttl,
            "iss": self._issuer,
        }
        if impersonation is not None:
            # Byte-identical claims dict when None (M2) — this key is added ONLY for an
            # impersonation mint, and is never in the required-claims set below, so an
            # ordinary decode() path is completely unaffected either way.
            claims["impersonation"] = {
                "session_id": str(impersonation.session_id),
                "actor_user_id": str(impersonation.actor_user_id),
                "actor_tenant_id": str(impersonation.actor_tenant_id),
                "actor_email": impersonation.actor_email,
            }
        return jwt.encode(claims, self._secret, algorithm="HS256"), ttl

    def decode(self, token: str) -> Identity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                issuer=self._issuer,
                options={"require": ["sub", "tenant_id", "role", "email", "exp", "iat", "iss"]},
            )
            role = Role(claims["role"])
            # Optional claim — never in the required set above, so absent ⇒ impersonation
            # =None, byte-identical to today for every ordinary token (M3).
            imp_claim = claims.get("impersonation")
            impersonation = (
                ImpersonationContext(
                    session_id=uuid.UUID(imp_claim["session_id"]),
                    actor_user_id=uuid.UUID(imp_claim["actor_user_id"]),
                    actor_tenant_id=uuid.UUID(imp_claim["actor_tenant_id"]),
                    actor_email=imp_claim["actor_email"],
                )
                if imp_claim is not None
                else None
            )
            if role == Role.SUPERADMIN and impersonation is not None:
                # Defense-in-depth (contract Part B; security review, FROZEN @ v1): never
                # produced by this task's own mint path (Mint always resolves a
                # non-superadmin target role, M6) — rejected unconditionally here so a
                # future minting bug can never smuggle SUPERADMIN-level ROLE_PERMISSIONS
                # through an impersonation identity.
                raise InvalidTokenError
            identity = Identity(
                user_id=uuid.UUID(claims["sub"]),
                tenant_id=uuid.UUID(claims["tenant_id"]),
                email=claims["email"],
                role=role,
                impersonation=impersonation,
            )
            # Observability contract M2: every successfully authenticated
            # /admin/* request carries tenant_id in its access log line.
            structlog.contextvars.bind_contextvars(tenant_id=str(identity.tenant_id))
            return identity
        except (jwt.InvalidTokenError, ValueError, KeyError, TypeError) as exc:
            # TypeError (N3, security review Build-time note): the nested-claim parsing
            # above indexes `imp_claim` with string keys — a malformed non-dict claim
            # (e.g. a forged token with "impersonation": "not-a-dict") raises TypeError,
            # not KeyError, so it must join this tuple too or a malformed claim 500s
            # instead of 401ing. Not attacker-reachable (requires the HMAC secret to
            # produce), but cheap to get right.
            raise InvalidTokenError from exc
