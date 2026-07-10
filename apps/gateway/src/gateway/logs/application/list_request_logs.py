"""ListRequestLogsUseCase (logs-explorer-api TASK.md §3 — FROZEN @ v1).

Thin orchestration over LogsRepository.list_for_tenant_keyset: fetches limit+1 rows to
derive has_more (mirrors audit/api/router.py:export_audit's own pattern), then trims the
page back to `limit`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from gateway.logs.domain.entities import RequestLog
from gateway.logs.infrastructure.logs_repository import LogsRepository


class ListRequestLogsUseCase:
    """execute(...) -> (page, has_more) — the caller derives next_cursor from page[-1]."""

    def __init__(self, repository: LogsRepository) -> None:
        self._repository = repository

    async def execute(
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
    ) -> tuple[list[RequestLog], bool]:
        rows = await self._repository.list_for_tenant_keyset(
            tenant_id,
            limit=limit + 1,  # fetch one extra row to derive has_more
            cursor=cursor,
            since=since,
            until=until,
            model_id=model_id,
            key_id=key_id,
            status_exact=status_exact,
            status_bucket=status_bucket,
            cost_min=cost_min,
            cost_max=cost_max,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        return page, has_more
