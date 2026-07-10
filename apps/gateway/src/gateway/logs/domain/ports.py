"""Domain ports for the logs (payload-capture-store) module — zero framework imports.

Contract FROZEN @ v1 (payload-capture-store TASK.md §3).
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class ZdrOverridePort(Protocol):
    """Zero-Data-Retention override check — the FROZEN hook the sibling
    tenant-retention-zdr task implements against.

    Checked LIVE before every capture persist (never cached from opt-in-toggle time,
    per §1 Must) — a tenant that goes under ZDR after opting in must stop producing
    rows on its very next proxied call, not just at the next toggle write.
    """

    async def is_zdr(self, tenant_id: uuid.UUID) -> bool:
        """Return True if this tenant is under Zero-Data-Retention (capture must be
        suppressed). NEVER raises to the caller — any internal failure (DB error,
        timeout) is caught and mapped to True (fail-closed: assume ZDR on doubt).
        Default wiring (AlwaysAllowCapture) always returns False until the
        tenant-retention-zdr task adds the real ZDR column/check.
        """
        ...


__all__ = ["ZdrOverridePort"]
