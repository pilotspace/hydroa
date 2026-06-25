"""Domain entity and port for the audit event subsystem.

Contract (audit-log-store TASK.md §3 — FROZEN @ v1):
  - AuditEvent: frozen dataclass representing one immutable audit row.
  - AuditLog: Protocol exposing ONLY record() and list_for_tenant() — NO mutate path.

Rejections enforced here:
  - audit_missing_actor: a tenant-scoped event (tenant_id is not None) MUST carry actor_user_id.
    System events (tenant_id=None) may carry actor_user_id=None.
  - audit_secret_leak: metadata is an opaque dict — callers are responsible for not writing secrets.
    This layer trusts callers (enforcement is at the emit sites and tested by the test suite).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable audit record.

    Invariant: if tenant_id is not None (i.e. a tenant-scoped action),
    actor_user_id MUST be provided (audit_missing_actor).
    System-level events (tenant_id=None) may omit actor_user_id.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    result: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(__import__("datetime").UTC))

    def __post_init__(self) -> None:
        """Enforce audit_missing_actor invariant."""
        if self.tenant_id is not None and self.actor_user_id is None:
            raise ValueError(
                "audit_missing_actor: a tenant-scoped audit event must carry actor_user_id"
            )


@runtime_checkable
class AuditLog(Protocol):
    """Port for the append-only audit log.

    Deliberately exposes ONLY record() and list_for_tenant().
    No update, delete, patch, or remove methods exist — audit_immutable_violation
    is enforced by the ABSENCE of any such method on this Protocol.
    """

    async def record(self, event: AuditEvent) -> None:
        """Append one audit event row (INSERT only — never UPDATE/DELETE)."""
        ...

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[AuditEvent]:
        """Return audit events for a tenant, newest-first, bounded by limit.

        before: if provided, only return events created before this timestamp.
        """
        ...
