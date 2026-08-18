"""Bounded-timeout session-revocation check (auth-hardening-login-sessions TASK.md §3
M5/M6, FROZEN @ v1, SECURITY).

One SELECT answers both revocation questions for the presented session JWT:
  - jti denylist: a row in revoked_auth_sessions for this jti (POST /admin/auth/logout)
  - user watermark: users.sessions_not_before > the token's iat (password-reset confirm)

Failure posture is SPLIT deliberately (unlike DbImpersonationSessionGuard, which folds
both into InvalidTokenError): a positive "revoked" answer raises nothing here — the
caller (ensure_session_not_revoked, tenants/domain/authz.py) maps True -> the same 401
as any invalid token; but a store failure/timeout raises
SessionRevocationUnavailableError -> the 503-class ERR_AUTH_UNAVAILABLE, because telling
a caller their live token is invalid when the DB merely blinked would be a lie (M6/A11).
Never a silent allow either way — fail-CLOSED, the api_keys revocation-check precedent.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.errors import SessionRevocationUnavailableError
from gateway.tenants.infrastructure.orm import RevokedAuthSessionRow, UserRow


class DbSessionRevocationGuard:
    """Constructed per-request with the ALREADY-RESOLVED AsyncSession (get_session's
    dependency-cache means this is the same session/connection the route handler uses —
    no second DB connection). Zero writes; both reads hit primary keys."""

    def __init__(self, *, session: AsyncSession, timeout_seconds: float) -> None:
        self._session = session
        self._timeout_seconds = timeout_seconds

    async def is_revoked(self, identity: Identity) -> bool:
        """True iff the presented token must be refused: its jti is denylisted, or its
        iat strictly predates the user's sessions_not_before watermark (A9: strict —
        a token minted in the same second as the reset survives, so the victim's own
        fresh post-reset login never bounces).

        Raises SessionRevocationUnavailableError on timeout or any store failure —
        the caller maps it to ERR_AUTH_UNAVAILABLE (503), never to a silent allow.
        """
        try:
            async with asyncio.timeout(self._timeout_seconds):
                if identity.jti is not None:
                    denied = (
                        await self._session.execute(
                            select(RevokedAuthSessionRow.jti).where(
                                RevokedAuthSessionRow.jti == identity.jti
                            )
                        )
                    ).one_or_none()
                    if denied is not None:
                        return True
                if identity.iat is not None:
                    watermark = (
                        await self._session.execute(
                            select(UserRow.sessions_not_before).where(
                                UserRow.id == identity.user_id
                            )
                        )
                    ).scalar_one_or_none()
                    if (
                        watermark is not None
                        and datetime.fromtimestamp(identity.iat, tz=UTC) < watermark
                    ):
                        return True
                return False
        except Exception as exc:
            # fail-CLOSED via the 503 path (M6): a revocation decision, not an
            # availability gate — but also not a known-bad token, so never a 401.
            structlog.get_logger().warning(
                "session_revocation_check_failed",
                user_id=str(identity.user_id),
                error=type(exc).__name__,
            )
            raise SessionRevocationUnavailableError from exc
