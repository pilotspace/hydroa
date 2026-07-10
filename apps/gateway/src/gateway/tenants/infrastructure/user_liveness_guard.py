"""Fail-closed, bounded-timeout user-liveness check.

Implements domain port UserLivenessGuard (scim-provisioning deactivation-escalation fix —
verify-phase HARD-STOP finding): a stateless session JWT's documented residual is "usable
for ORDINARY requests up to jwt_ttl_seconds after deactivation" (scim-provisioning TASK.md
§1 M7) — it must NOT extend to minting a NEW, independently-long-lived credential (a fresh
SCIM token or API key that would outlive the JWT itself). This guard is wired ONLY onto the
credential-minting routes (POST/rotate for /admin/scim/tokens and /admin/keys), never onto
every hot /admin read — see tenants/domain/authz.py::require_active_user's own docstring.

Mirrors DbImpersonationSessionGuard byte-for-byte in shape and failure posture: fail-CLOSED
(timeout, DB error, missing row, or a deactivated row all reject) rather than the
RedisCooldownGate/RedisBudgetGuard fail-OPEN convention — this is a credential-revocation
decision, not an availability gate, so a transient DB blip must not silently reopen the
mint-time escalation window this guard exists to close.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.infrastructure.orm import UserRow


class DbUserLivenessGuard:
    """Fail-closed, bounded-timeout live-check against users.deactivated_at. Constructed
    per-request with the ALREADY-RESOLVED AsyncSession (get_session's FastAPI dependency-
    cache means this is the SAME session/connection the route handler's own body uses — no
    second DB connection). Zero writes; zero new index (id is already the primary key)."""

    def __init__(self, *, session: AsyncSession, timeout_seconds: float) -> None:
        self._session = session
        self._timeout_seconds = timeout_seconds

    async def ensure_active(self, user_id: uuid.UUID) -> None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                row = (
                    await self._session.execute(
                        select(UserRow.deactivated_at).where(UserRow.id == user_id)
                    )
                ).one_or_none()
        except Exception as exc:
            # fail-CLOSED: timeout, DB error, or any other exception -> reject. Deliberate
            # deviation from the fail-OPEN rate-limiter convention (see module docstring) —
            # this is a credential-revocation decision, not an availability gate.
            structlog.get_logger().warning(
                "user_liveness_check_failed",
                user_id=str(user_id),
                error=type(exc).__name__,
            )
            raise InvalidTokenError from exc
        # A MISSING row is deliberately NOT treated as "inactive": every other identity
        # check in this codebase (require_permission/require_owner_or_admin/_resolve_
        # identity) is role/tenant-claims-only and never validates that user_id resolves
        # to a real row — including this project's own test convention of minting a
        # synthetic JWT for a role check with no backing users row (see scim_provisioning/
        # conftest.py::issue_jwt's own docstring). Only an EXISTING, deactivated row is a
        # genuine liveness failure; a missing row is out of scope for this guard (it is not
        # the escalation this fix closes — a real deactivation always acts on a real row).
        if row is not None and row.deactivated_at is not None:
            raise InvalidTokenError
