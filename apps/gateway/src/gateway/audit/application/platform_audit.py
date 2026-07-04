"""Shared fire-and-forget audit-emission helper for superadmin cross-tenant admin actions
(admin-console-audit TASK.md §3 — FROZEN @ v1).

Every platform_*_router.py call site (15 total, across platform_tenants_router.py,
platform_tenant_config_router.py, platform_keys_router.py, platform_users_router.py) calls
emit_platform_audit(...) exactly once, on its own success path, instead of duplicating the
existing self-service convention's own ~15-line AuditEvent(...) + asyncio.ensure_future(...)
inline block 15 more times.

target_tenant_id is the AFFECTED (target) tenant_id — NEVER the superadmin caller's own
identity.tenant_id — so the target tenant's existing GET /admin/audit (tenant-scoped by
identity.tenant_id, AUDIT_READ) surfaces the row with zero new endpoint. target_tenant_id=None
denotes a system-level event (only platform_tenants_router.py's bulk list today), mirroring
ops.platform_credential_resolve's own tenant_id=None precedent (AuditEvent.__post_init__ only
requires actor_user_id when tenant_id is not None; actor_user_id is always populated here
regardless, since the REAL superadmin actor is always known).

Fail-open (M12): inherited verbatim from record_audit's own FROZEN contract — this helper adds
no additional try/except, timeout, retry, or circuit-breaker of its own. An audit-write failure
never raises out of this function and never blocks/changes the caller's HTTP response.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.tenants.domain.entities import Identity


async def emit_platform_audit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity: Identity,
    target_tenant_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str | None,
    metadata: dict[str, object],
) -> None:
    """Schedule ONE fire-and-forget audit_events row for a superadmin cross-tenant action.

    target_tenant_id: the PATH tenant_id being acted upon (the AFFECTED tenant) — None ONLY
    for the bulk, targetless tenant-directory list (a system-level event). NEVER pass
    identity.tenant_id here (the superadmin's own reserved platform tenant).
    identity: the SUPERADMIN caller — always supplies the REAL actor_user_id/actor_email,
    regardless of target_tenant_id.

    Callers never construct AuditEvent or call record_audit/asyncio.ensure_future themselves
    (M1) — this is the ONLY place either happens for all 15 cross-tenant call sites.
    """
    event = AuditEvent(
        id=uuid.uuid4(),
        tenant_id=target_tenant_id,
        actor_user_id=identity.user_id,
        actor_email=identity.email,
        action=action,
        target_type=target_type,
        target_id=target_id,
        result="success",
        metadata=metadata,
        created_at=datetime.now(UTC),
    )
    asyncio.ensure_future(record_audit(session_factory, event))  # noqa: RUF006
