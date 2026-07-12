"""GET /admin/logs + GET /admin/logs/{log_id} — tenant-admin request-logs query API.

Contract (logs-explorer-api TASK.md §3 — FROZEN @ v1):
  - Authorization: Bearer <JWT>, Permission.LOGS_READ (owner/admin/operator/superadmin;
    billing_admin/viewer/member -> 403). New additive permission, same precedent as
    RATE_CARDS_MANAGE.
  - Tenant-scoped: only the caller's own tenant_id rows, under every filter combination —
    the WHERE clause itself excludes cross-tenant rows (never a post-fetch check). A
    SUPERADMIN identity stays scoped to its own (platform) tenant here — no cross-tenant
    reach is wired in this task (authorize_tenant_scope() stays dormant).
  - GET /admin/logs: keyset-cursor pagination ONLY (no offset) — (created_at, id) DESC,
    limit 1..100 default 50 (an interactive-page ceiling, mirrors get_audit/get_alerts —
    NOT audit-export's archival 1..5000). Filters: since/until (ISO-8601 inclusive),
    model_id (exact), key_id (exact UUID), status (exact 3-digit code OR "success"/"error"
    bucket), cost_min/cost_max (decimal, inclusive; NULL cost_usd never matches).
  - List rows are METADATA-ONLY (never request_body/response_body/guardrail_verdict) — to
    keep every page's payload bounded regardless of individual row size.
  - GET /admin/logs/{log_id}: full row (adds request_body/response_body/guardrail_verdict
    + request_id) for exactly one log owned by the caller's tenant. An unknown id and a
    cross-tenant id return the IDENTICAL 404 ERR_LOG_NOT_FOUND — no distinguishable signal
    (mirrors keys/api/router.py:rotate_key's 404-invisibility idiom verbatim).
  - Both endpoints are READ-ONLY: SELECT only, no write path, no self-audit-of-read entry
    (out of scope for v1, unlike audit/export's own self-audit).
  - The list query is wrapped in an explicit asyncio.timeout (mirrors export_audit's
    pattern) mapped to ERR_LOGS_QUERY_TIMEOUT (504) on expiry — never an unbounded scan,
    never an unhandled 500. The detail query is a single-row tenant+id lookup — no
    dedicated timeout wrapper (mirrors keys/api/router.py's own single-row admin lookups,
    which carry no explicit asyncio.timeout either; the frozen contract's own "Access
    pattern" paragraph scopes the timeout requirement to the list query specifically).

This is a pure read-side addition to the EXISTING `logs/` bounded context
(payload-capture-store already owns logs/domain, logs/infrastructure, logs/application,
logs/api) — no write path, no change to the capture-write path's behavior.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    CURSOR_INVALID,
    LOG_NOT_FOUND,
    LOGS_QUERY_TIMEOUT,
    PAYLOAD_INVALID,
)
from gateway.logs.application.get_request_log import GetRequestLogUseCase
from gateway.logs.application.list_request_logs import ListRequestLogsUseCase
from gateway.logs.infrastructure.logs_repository import LogsRepository
from gateway.tenants.application.entitlements import check_plan_feature
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity

logs_query_router = APIRouter(prefix="/admin/logs", tags=["logs"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
_READ_TIMEOUT_SECONDS = 30.0
_STATUS_BUCKETS = ("success", "error")


# ---------------------------------------------------------------------------
# Response shapes (mirrors AuditEventItem/AlertListResponse — frozen, immutable)
# ---------------------------------------------------------------------------


class LogListItem(BaseModel):
    """One request_logs row, metadata-only — never a payload-bearing field."""

    model_config = ConfigDict(frozen=True)

    id: str
    key_id: str
    team_id: str | None
    model_id: str
    status_code: int
    stream: bool
    cached: bool
    scrub_status: str
    truncated: bool
    cost_usd: str | None
    created_at: str
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class LogDetailItem(LogListItem):
    """Full single-log detail — adds the payload-bearing fields + correlation key."""

    request_id: str | None
    request_body: dict[str, object] | None
    response_body: dict[str, object] | None
    guardrail_verdict: dict[str, object] | None


class LogListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[LogListItem]
    next_cursor: str | None
    has_more: bool


# ---------------------------------------------------------------------------
# Query-param parsing helpers (mirrors audit/api/router.py's local helpers)
# ---------------------------------------------------------------------------


def _parse_limit(limit: str | None) -> int:
    """Parse limit (1..100, default 50); non-integer or out-of-range -> PAYLOAD_INVALID."""
    if limit is None:
        return _DEFAULT_LIMIT
    try:
        parsed = int(limit)
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None
    if parsed < 1 or parsed > _MAX_LIMIT:
        raise PAYLOAD_INVALID.exc()
    return parsed


def _parse_iso_datetime(raw: str) -> datetime:
    """Parse an ISO-8601 string; malformed -> PAYLOAD_INVALID."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None


def _as_naive_utc(value: datetime) -> datetime:
    """Normalize a bound to naive UTC before binding.

    asyncpg expects a NAIVE UTC datetime against the test schema's `Mapped[datetime]`
    column (create_all maps it to a naive TIMESTAMP; prod's Alembic column is
    TIMESTAMPTZ). Mirrors audit/api/router.py:_as_naive_utc verbatim (documented gotcha).
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _parse_time_range(
    since: str | None, until: str | None
) -> tuple[datetime | None, datetime | None]:
    """Parse since/until; reject malformed values or an inverted range."""
    parsed_since = _parse_iso_datetime(since) if since is not None else None
    parsed_until = _parse_iso_datetime(until) if until is not None else None
    if parsed_since is not None and parsed_until is not None and parsed_since > parsed_until:
        raise PAYLOAD_INVALID.exc()
    return parsed_since, parsed_until


def _parse_key_id(key_id: str | None) -> uuid.UUID | None:
    if key_id is None:
        return None
    try:
        return uuid.UUID(key_id)
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None


def _parse_status(status: str | None) -> tuple[int | None, str | None]:
    """Dual-mode: exact 3-digit code ("429") OR bucket keyword ("success"/"error").

    Returns (status_exact, status_bucket) — mutually exclusive, at most one non-None.
    """
    if status is None:
        return None, None
    if status in _STATUS_BUCKETS:
        return None, status
    if len(status) == 3 and status.isdigit():
        code = int(status)
        if 100 <= code <= 599:
            return code, None
    raise PAYLOAD_INVALID.exc()


def _parse_decimal_bound(value: str | None, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        raise PAYLOAD_INVALID.exc(detail=f"{field_name} must be a valid decimal string") from None


def _encode_cursor(created_at: datetime, log_id: uuid.UUID) -> str:
    """Opaque base64url token carrying {created_at, id} of the last row of a page."""
    payload = json.dumps({"created_at": created_at.isoformat(), "id": str(log_id)}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    """Decode an opaque logs-list cursor. Any failure -> ERR_CURSOR_INVALID — a code
    distinct from ERR_PAYLOAD_INVALID, so a malformed cursor is never confused with a
    malformed limit/since/until/status/cost in a client's error handling."""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(payload)
        created_at = datetime.fromisoformat(obj["created_at"])
        log_id = uuid.UUID(obj["id"])
    except Exception:
        raise CURSOR_INVALID.exc() from None
    return created_at, log_id


def _cost_str(cost_usd: str | None) -> str | None:
    return cost_usd


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@logs_query_router.get("", response_model=LogListResponse)
async def list_logs(
    identity: Annotated[Identity, require_permission(Permission.LOGS_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    since: Annotated[str | None, Query()] = None,
    until: Annotated[str | None, Query()] = None,
    model_id: Annotated[str | None, Query()] = None,
    key_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    cost_min: Annotated[str | None, Query()] = None,
    cost_max: Annotated[str | None, Query()] = None,
) -> LogListResponse:
    """Tenant-admin request-logs list (FROZEN @ v1 — TASK.md §3).

    LOGS_READ gated: owner/admin/operator/superadmin pass; billing_admin/viewer/member ->
    403. Tenant-scoped (WHERE clause itself, never a post-fetch check). Keyset-paginated,
    newest-first. Rows are metadata-only. READ-ONLY.

    plan-enforcement (TASK.md §3, M6/R5): the query ITSELF is refused with 403
    ERR_PLAN_FEATURE_NOT_ENABLED when the tenant has an assigned plan whose feature_flags
    lacks "logs_explorer" (no persistent toggle exists to gate at write time instead).
    Inert for an unplanned tenant (M7).
    """
    tenant_id: uuid.UUID = identity.tenant_id
    await check_plan_feature(session, tenant_id, "logs_explorer")
    parsed_limit = _parse_limit(limit)
    parsed_since, parsed_until = _parse_time_range(since, until)
    parsed_key_id = _parse_key_id(key_id)
    status_exact, status_bucket = _parse_status(status)
    parsed_cost_min = _parse_decimal_bound(cost_min, "cost_min")
    parsed_cost_max = _parse_decimal_bound(cost_max, "cost_max")

    cursor_tuple: tuple[datetime, uuid.UUID] | None = None
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        cursor_tuple = (_as_naive_utc(cursor_created_at), cursor_id)

    repo = LogsRepository(session)
    use_case = ListRequestLogsUseCase(repo)

    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            page, has_more = await use_case.execute(
                tenant_id,
                limit=parsed_limit,
                cursor=cursor_tuple,
                since=_as_naive_utc(parsed_since) if parsed_since is not None else None,
                until=_as_naive_utc(parsed_until) if parsed_until is not None else None,
                model_id=model_id,
                key_id=parsed_key_id,
                status_exact=status_exact,
                status_bucket=status_bucket,
                cost_min=parsed_cost_min,
                cost_max=parsed_cost_max,
            )
    except TimeoutError:
        raise LOGS_QUERY_TIMEOUT.exc() from None

    items = [
        LogListItem(
            id=str(log.id),
            key_id=str(log.key_id),
            team_id=str(log.team_id) if log.team_id is not None else None,
            model_id=log.model_id,
            status_code=log.status_code,
            stream=log.stream,
            cached=log.cached,
            scrub_status=log.scrub_status,
            truncated=log.truncated,
            cost_usd=_cost_str(log.cost_usd),
            created_at=log.created_at.isoformat(),
            latency_ms=log.latency_ms,
            prompt_tokens=log.prompt_tokens,
            completion_tokens=log.completion_tokens,
            total_tokens=log.total_tokens,
        )
        for log in page
    ]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
    return LogListResponse(items=items, next_cursor=next_cursor, has_more=has_more)


@logs_query_router.get("/{log_id}", response_model=LogDetailItem)
async def get_log(
    log_id: uuid.UUID,
    identity: Annotated[Identity, require_permission(Permission.LOGS_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogDetailItem:
    """Single request_logs row detail (FROZEN @ v1 — TASK.md §3).

    LOGS_READ gated: owner/admin/operator/superadmin pass; billing_admin/viewer/member ->
    403. Returns 404 ERR_LOG_NOT_FOUND for BOTH an unknown id and a cross-tenant id —
    byte-identical shape, no leak (mirrors keys/api/router.py:rotate_key). READ-ONLY.

    plan-enforcement (TASK.md §3, M6/R5): the query ITSELF is refused with 403
    ERR_PLAN_FEATURE_NOT_ENABLED when the tenant has an assigned plan whose feature_flags
    lacks "logs_explorer". Inert for an unplanned tenant (M7).
    """
    tenant_id: uuid.UUID = identity.tenant_id
    await check_plan_feature(session, tenant_id, "logs_explorer")
    repo = LogsRepository(session)
    use_case = GetRequestLogUseCase(repo)

    log = await use_case.execute(tenant_id, log_id)
    if log is None:
        raise LOG_NOT_FOUND.exc()

    return LogDetailItem(
        id=str(log.id),
        key_id=str(log.key_id),
        team_id=str(log.team_id) if log.team_id is not None else None,
        model_id=log.model_id,
        status_code=log.status_code,
        stream=log.stream,
        cached=log.cached,
        scrub_status=log.scrub_status,
        truncated=log.truncated,
        cost_usd=_cost_str(log.cost_usd),
        created_at=log.created_at.isoformat(),
        latency_ms=log.latency_ms,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        total_tokens=log.total_tokens,
        request_id=str(log.request_id) if log.request_id is not None else None,
        request_body=log.request_body,
        response_body=log.response_body,
        guardrail_verdict=log.guardrail_verdict,
    )
