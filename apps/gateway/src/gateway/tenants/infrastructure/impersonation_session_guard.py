"""Fail-closed, bounded-timeout impersonation-session liveness check.

Implements domain port ImpersonationSessionGuard (impersonation-live-session-guard
TASK.md §3 Part C, FROZEN @ v1) — closes the bounded window between an impersonation
session being explicitly Ended and its own (<=900s) JWT `exp` naturally elapsing.

Mirrors this codebase's OWN api_keys revocation-check precedent
(keys/application/use_cases.py::AuthzUseCase.execute — a per-request DB read checking
`revoked_at IS NULL`, fails closed) rather than the RedisCooldownGate/RedisBudgetGuard
fail-OPEN convention: this is a credential-revocation decision, not an availability
gate, so a transient DB blip must not silently reopen the exact gap this task exists
to close.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import ImpersonationContext
from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.infrastructure.orm import ImpersonationSessionRow


class DbImpersonationSessionGuard:
    """Fail-closed, bounded-timeout live-check against impersonation_sessions — the SAME
    compound predicate impersonation-session-lifecycle TASK.md §3 already declares as
    session validity's sole source of truth (revoked_at IS NULL AND expires_at > now()).
    Constructed per-request with the ALREADY-RESOLVED AsyncSession (get_session's
    FastAPI dependency-cache means this is the SAME session/connection the route
    handler's own body uses — no second DB connection). Zero writes; zero new index
    (id is already the primary key)."""

    def __init__(self, *, session: AsyncSession, timeout_seconds: float) -> None:
        self._session = session
        self._timeout_seconds = timeout_seconds

    async def ensure_live(self, impersonation: ImpersonationContext) -> None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                row = (
                    await self._session.execute(
                        select(
                            ImpersonationSessionRow.revoked_at, ImpersonationSessionRow.expires_at
                        ).where(ImpersonationSessionRow.id == impersonation.session_id)
                    )
                ).one_or_none()
        except Exception as exc:
            # fail-CLOSED: timeout, DB error, or any other exception -> reject. Deliberate
            # deviation from RedisCooldownGate/RedisBudgetGuard's fail-OPEN convention (§1
            # Framings) — this is a credential-revocation decision, not an availability gate.
            structlog.get_logger().warning(
                "impersonation_live_check_failed",
                session_id=str(impersonation.session_id),
                error=type(exc).__name__,
            )
            raise InvalidTokenError from exc
        if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
            raise InvalidTokenError
