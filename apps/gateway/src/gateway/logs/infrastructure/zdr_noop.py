"""AlwaysAllowCapture — default (permissive) ZdrOverridePort implementation.

is_zdr always returns False — no tenant can be under Zero-Data-Retention before the
sibling tenant-retention-zdr task adds the real column/check. This is the default
wiring in main.py; that task will replace it with a real DB-backed implementation
without changing PayloadCapturePort's or SqlAlchemyPayloadCapture's shape.
"""

from __future__ import annotations

import uuid


class AlwaysAllowCapture:
    """Permissive default ZdrOverridePort — no tenant is ever considered ZDR."""

    async def is_zdr(self, tenant_id: uuid.UUID) -> bool:
        """Always return False (never ZDR) — the frozen default until real ZDR lands."""
        return False
