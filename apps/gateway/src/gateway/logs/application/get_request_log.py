"""GetRequestLogUseCase (logs-explorer-api TASK.md §3 — FROZEN @ v1).

Thin orchestration over LogsRepository.get_for_tenant. Returns None for BOTH an unknown
id and a cross-tenant id (indistinguishable by construction — the repository's WHERE
clause itself excludes cross-tenant rows) — the router maps None to ERR_LOG_NOT_FOUND.
"""

from __future__ import annotations

import uuid

from gateway.logs.domain.entities import RequestLog
from gateway.logs.infrastructure.logs_repository import LogsRepository


class GetRequestLogUseCase:
    """execute(tenant_id, log_id) -> RequestLog | None."""

    def __init__(self, repository: LogsRepository) -> None:
        self._repository = repository

    async def execute(self, tenant_id: uuid.UUID, log_id: uuid.UUID) -> RequestLog | None:
        return await self._repository.get_for_tenant(tenant_id, log_id)
