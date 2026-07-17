"""Concrete platform-tenant credential fallback.

platform-credential-fallback TASK.md §3, FROZEN @ v1.

Injected into the credential-resolution seam (``resolve_provider_credential``) as the optional
``PlatformCredentialFallback`` collaborator. It owns exactly three responsibilities:

  1. ``platform_tenant_id()`` — resolve the reserved ``kind='platform'`` tenant's id via
     ``get_platform_tenant``, bounded by a timeout, failing CLOSED (None) on an absent row OR any
     DB error/timeout. It NEVER fabricates an id.
  2. ``audit_served`` — fire-and-forget audit that a request was served by the platform fallback.
  3. ``audit_misconfig`` — fire-and-forget error-level audit that the platform row is missing while
     the fallback is ON (the operator-facing signal for the R3 tenant-facing-402 decision).

Design-for-failure: the DB read is timeout-bounded and swallows every exception (fail-closed →
402, never a fabricated/empty credential). Audits are scheduled via ``asyncio.ensure_future`` on a
SEPARATE session (``record_audit`` is itself fail-open), so a slow/broken audit never blocks or
fails the hot path.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.tenants.infrastructure.repository import get_platform_tenant

# Audit action for the proxy hot-path platform-fallback (distinct from the ops-mTLS
# "ops.platform_credential_resolve" action so the two provenance paths never conflate).
_AUDIT_ACTION = "proxy.platform_credential_fallback"


class PlatformCredentialFallbackService:
    """Session-factory-backed ``PlatformCredentialFallback`` for the proxy credential seam."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        enabled: bool,
        resolve_timeout_s: float = 2.0,
    ) -> None:
        self._session_factory = session_factory
        self.enabled = enabled
        self._timeout = resolve_timeout_s

    async def platform_tenant_id(self) -> uuid.UUID | None:
        """Return the reserved platform tenant id, or None (unprovisioned / DB error / timeout).

        Fail-CLOSED: any exception or a timeout resolves to None so the seam returns a 402 to the
        tenant (never a fabricated id). Resolved fresh per call — the platform tenant row is never
        cached as a constant (it is a real ``uuid7`` row, not a well-known UUID).
        """
        try:
            async with asyncio.timeout(self._timeout):
                async with self._session_factory() as session:
                    row = await get_platform_tenant(session)
                    return row.id if row is not None else None
        except Exception:
            return None

    async def audit_served(self, *, tenant_id: uuid.UUID, provider: str) -> None:
        """Audit (fire-and-forget) that ``tenant_id``'s ``provider`` call was platform-served."""
        self._schedule_audit(tenant_id=tenant_id, provider=provider, result="success", note=None)

    async def audit_misconfig(self, *, tenant_id: uuid.UUID, provider: str) -> None:
        """Audit (fire-and-forget, error-level) that the platform tenant row is missing (R3)."""
        self._schedule_audit(
            tenant_id=tenant_id, provider=provider, result="error", note="platform_tenant_missing"
        )

    def _schedule_audit(
        self, *, tenant_id: uuid.UUID, provider: str, result: str, note: str | None
    ) -> None:
        # tenant_id is carried in metadata (NOT the event's tenant_id) so no user/key actor is
        # required — mirrors ops.platform_credential_resolve, whose events are system-level
        # (tenant_id=None). This keeps the AuditEvent actor invariant satisfied on the hot path.
        metadata: dict[str, object] = {
            "requesting_tenant_id": str(tenant_id),
            "provider": provider,
            "credential_source": "platform_fallback",
        }
        if note is not None:
            metadata["note"] = note
        asyncio.ensure_future(  # noqa: RUF006 — fire-and-forget; record_audit is fail-open
            record_audit(
                self._session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=None,
                    actor_user_id=None,
                    actor_email=None,
                    action=_AUDIT_ACTION,
                    target_type="provider",
                    target_id=provider,
                    result=result,
                    metadata=metadata,
                    created_at=datetime.now(UTC),
                ),
            )
        )
