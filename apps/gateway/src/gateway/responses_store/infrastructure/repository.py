"""SQLAlchemy repository for the responses-state-store domain.

INVARIANT (appsec floor, responses-state-store PLAN.md §3): every method takes
``tenant_id`` and every statement filters on it IN THE SAME query that checks
existence. A cross-tenant / unknown / deleted id always returns None / False —
NEVER a 403/404 alternation; the caller maps it to the SINGLE byte-identical 404
ERR_RESPONSE_NOT_FOUND (anti-enumeration).

The ``previous_response_id`` column is a bare pointer (no FK). DELETE cascades to
every tenant-scoped descendant in ONE transaction via a WITH RECURSIVE walk over
``ix_stored_responses_tenant_prev``, tenant_id filtered at the anchor AND every hop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.responses_store.infrastructure.orm import StoredResponseRow


@dataclass(frozen=True, slots=True)
class ResolvedPrev:
    """The minimal previous-row shape the chain path needs (all tenant-scoped)."""

    chain_depth: int
    context_messages: Any
    response_body: Any


# Cascade: delete the root AND every descendant reachable via previous_response_id,
# tenant_id filtered at the anchor and at every recursive hop (a concurrent chain-child
# committed after this transaction's snapshot survives as a dangling-pointer orphan — a
# contracted honest bound, PLAN.md §3).
_CASCADE_DELETE = text(
    """
    WITH RECURSIVE descendants(id) AS (
        SELECT id FROM stored_responses
         WHERE id = :root AND tenant_id = :tid
        UNION
        SELECT sr.id FROM stored_responses sr
          JOIN descendants d ON sr.previous_response_id = d.id
         WHERE sr.tenant_id = :tid
    )
    DELETE FROM stored_responses
     WHERE id IN (SELECT id FROM descendants) AND tenant_id = :tid
    RETURNING id
    """
)


class StoredResponseRepository:
    """All stored-response persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        response_id: str,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        status: str,
        previous_response_id: str | None,
        chain_depth: int,
        context_messages: Any,
        response_body: Any,
        usage: Any,
    ) -> None:
        """Insert one stored_responses row (atomic in the caller's transaction).

        The ZDR fail-closed gate is enforced by the caller BEFORE the governance dial
        (raise_if_zdr as the first line of the store path) — a ZDR tenant never reaches
        this write, so no metadata-only branch exists here.
        """
        row = StoredResponseRow(
            id=response_id,
            tenant_id=tenant_id,
            key_id=key_id,
            model=model,
            status=status,
            previous_response_id=previous_response_id,
            chain_depth=chain_depth,
            context_messages=context_messages,
            response_body=response_body,
            usage=usage,
        )
        self._session.add(row)
        await self._session.flush()

    async def resolve_prev(self, *, tenant_id: uuid.UUID, response_id: str) -> ResolvedPrev | None:
        """Fetch the previous row for chaining — tenant_id in the SAME query as the id.

        Returns None for an unknown, cross-tenant, deleted, or malformed id (the SELECT
        simply matches nothing) — the caller maps None to the single 404.
        """
        row = (
            await self._session.execute(
                text(
                    "SELECT chain_depth, context_messages, response_body"
                    " FROM stored_responses WHERE id = :rid AND tenant_id = :tid"
                ),
                {"rid": response_id, "tid": str(tenant_id)},
            )
        ).first()
        if row is None:
            return None
        return ResolvedPrev(
            chain_depth=int(row.chain_depth),
            context_messages=row.context_messages,
            response_body=row.response_body,
        )

    async def get_response_body(self, *, tenant_id: uuid.UUID, response_id: str) -> Any | None:
        """Return the persisted Response object verbatim, or None (→ 404)."""
        row = (
            await self._session.execute(
                text(
                    "SELECT response_body FROM stored_responses"
                    " WHERE id = :rid AND tenant_id = :tid"
                ),
                {"rid": response_id, "tid": str(tenant_id)},
            )
        ).first()
        if row is None:
            return None
        return row.response_body

    async def cascade_delete(self, *, tenant_id: uuid.UUID, response_id: str) -> bool:
        """Delete the row + every tenant-scoped descendant in ONE statement.

        Returns True iff the root existed for this tenant (>= 1 row deleted), False for an
        unknown / cross-tenant / already-deleted id (→ single 404). The recursive anchor is
        tenant-scoped, so a foreign root deletes nothing and is indistinguishable from unknown.
        """
        deleted = (
            await self._session.execute(
                _CASCADE_DELETE,
                {"root": response_id, "tid": str(tenant_id)},
            )
        ).fetchall()
        await self._session.flush()
        return any(str(r[0]) == response_id for r in deleted)
