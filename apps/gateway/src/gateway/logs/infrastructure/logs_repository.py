"""SQLAlchemy read-side repository for request_logs (logs-explorer-api TASK.md §3 — FROZEN @ v1).

Read-only over the FROZEN request_logs table (payload-capture-store TASK.md §3, extended
by request-log-metering-fields TASK.md §3 — both FROZEN @ v1). Exposes exactly two methods:
  - list_for_tenant_keyset(...) — keyset page over (created_at, id) DESC, mirrors
    AuditRepository.list_for_tenant_keyset VERBATIM (same decomposed OR/AND keyset
    predicate — tuple_()'s stub typing rejects plain literal operands under strict
    pyright, so this form is used instead; same order-by).
  - get_for_tenant(tenant_id, log_id) — single-row lookup, tenant-scoped in the WHERE
    clause itself (never a post-fetch check) so an unknown id and a cross-tenant id are
    indistinguishable by construction (zero rows either way -> caller maps to
    ERR_LOG_NOT_FOUND).

Deliberately exposes NO write/update/delete method — the capture-write path
(logs/application/capture_writer.py) is untouched by this task.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.logs.domain.entities import RequestLog
from gateway.logs.infrastructure.orm import RequestLogRow


class LogsRepository:
    """Read-only repository over request_logs. Implements no write path by design."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant_keyset(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        model_id: str | None = None,
        key_id: uuid.UUID | None = None,
        status_exact: int | None = None,
        status_bucket: str | None = None,
        cost_min: Decimal | None = None,
        cost_max: Decimal | None = None,
    ) -> list[RequestLog]:
        """Keyset page over (created_at, id) DESC; caller fetches limit+1 to derive has_more.

        Every filter is composed with AND. `status_exact` (a literal 3-digit code) and
        `status_bucket` ("success"/"error") are mutually exclusive by construction — the
        caller (application layer) resolves the dual-mode `status` query param to exactly
        one of the two before calling this method.

        cost_min/cost_max use plain `>=`/`<=` — a row with cost_usd IS NULL never matches
        either bound under ordinary SQL NULL-comparison semantics (no special-casing
        needed, matches the frozen contract's own note).
        """
        stmt = (
            select(RequestLogRow)
            .where(RequestLogRow.tenant_id == tenant_id)
            .order_by(desc(RequestLogRow.created_at), desc(RequestLogRow.id))
            .limit(limit)
        )
        if since is not None:
            stmt = stmt.where(RequestLogRow.created_at >= since)
        if until is not None:
            stmt = stmt.where(RequestLogRow.created_at <= until)
        if model_id is not None:
            stmt = stmt.where(RequestLogRow.model_id == model_id)
        if key_id is not None:
            stmt = stmt.where(RequestLogRow.key_id == key_id)
        if status_exact is not None:
            stmt = stmt.where(RequestLogRow.status_code == status_exact)
        elif status_bucket == "success":
            stmt = stmt.where(RequestLogRow.status_code < 400)
        elif status_bucket == "error":
            stmt = stmt.where(RequestLogRow.status_code >= 400)
        if cost_min is not None:
            stmt = stmt.where(RequestLogRow.cost_usd >= cost_min)
        if cost_max is not None:
            stmt = stmt.where(RequestLogRow.cost_usd <= cost_max)
        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            # Decomposed OR/AND form of `(created_at, id) < (cursor_created_at, cursor_id)`
            # — mirrors AuditRepository.list_for_tenant_keyset verbatim (tuple_()'s stub
            # typing rejects plain literal operands under strict pyright).
            stmt = stmt.where(
                or_(
                    RequestLogRow.created_at < cursor_created_at,
                    and_(
                        RequestLogRow.created_at == cursor_created_at,
                        RequestLogRow.id < cursor_id,
                    ),
                )
            )

        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [_row_to_entity(r) for r in rows]

    async def get_for_tenant(self, tenant_id: uuid.UUID, log_id: uuid.UUID) -> RequestLog | None:
        """Single-row lookup, tenant-scoped IN THE WHERE CLAUSE ITSELF.

        Returns None for both an unknown id and a cross-tenant id — the caller cannot
        distinguish the two from this method's return value alone (no leak by
        construction, mirrors keys/api/router.py:rotate_key's 404-invisibility idiom).
        """
        stmt = select(RequestLogRow).where(
            RequestLogRow.tenant_id == tenant_id, RequestLogRow.id == log_id
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _row_to_entity(row) if row is not None else None


def _row_to_entity(row: RequestLogRow) -> RequestLog:
    """Convert an ORM row to the domain RequestLog entity."""
    return RequestLog(
        id=row.id,
        tenant_id=row.tenant_id,
        key_id=row.key_id,
        team_id=row.team_id,
        model_id=row.model_id,
        status_code=row.status_code,
        stream=row.stream,
        cached=row.cached,
        request_body=row.request_body,
        response_body=row.response_body,
        guardrail_verdict=row.guardrail_verdict,
        scrub_status=row.scrub_status,
        truncated=row.truncated,
        cost_usd=str(row.cost_usd) if row.cost_usd is not None else None,
        created_at=row.created_at,
        request_id=row.request_id,
        latency_ms=row.latency_ms,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
    )
