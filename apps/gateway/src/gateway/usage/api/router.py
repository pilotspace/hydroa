"""GET /admin/usage and GET /admin/spend — authenticated tenant usage and spend windows.

GET /admin/usage contract (FROZEN @ v1 — TASK.md §3):
  - Authorization: Bearer <JWT>
  - 200: UsageTotalsResponse
  - 401: problem+json ERR_AUTH_INVALID_TOKEN
  - Totals and records come from Postgres ledger (NOT Redis counter).
  - Tenant isolation: only the authenticated tenant's rows are returned.

GET /admin/spend contract (FROZEN @ spend-windows — TASK.md §3):
  - Authorization: Bearer <JWT> (owner/admin role)
  - 200: SpendWindowResponse with Decimal-exact aggregates from usage_records
  - 401: ERR_AUTH_INVALID_TOKEN, 422: ERR_PAYLOAD_INVALID, 404: ERR_KEY_NOT_FOUND
  - Aggregation via date_trunc over Postgres NUMERIC — no float arithmetic.
  - Tenant isolation: WHERE tenant_id = :tenant_id always applied.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_TOKEN_INVALID,
    AUTH_TOKEN_MISSING,
    KEY_NOT_FOUND_IN_TENANT,
    PAYLOAD_END_DATE_INVALID,
    PAYLOAD_GROUP_BY_INVALID,
    PAYLOAD_INVALID,
    PAYLOAD_KEY_ID_UUID_INVALID,
    PAYLOAD_START_DATE_INVALID,
    PAYLOAD_WINDOW_INVALID,
)
from gateway.keys.api.deps import require_owner_or_admin
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.domain.ports import TokenService
from gateway.usage.api.schemas import (
    ReconciliationResponse,
    ReconciliationSourceItem,
    SpendBreakdownItem,
    SpendBucket,
    SpendTotals,
    SpendWindowResponse,
    TeamSpendBreakdownItem,
    UsageRecordItem,
    UsageTotalsResponse,
)
from gateway.usage.application.reconciliation import reconcile_window

usage_router = APIRouter(prefix="/admin", tags=["usage"])

_VALID_WINDOWS = {"day", "week", "month"}
_GRANULARITY = {"day": "day", "week": "week", "month": "month"}


def _extract_identity(request: Request) -> Identity:
    """Extract and validate Bearer JWT; raise 401 on any failure."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AUTH_TOKEN_MISSING.exc()
    token_service = cast(TokenService, request.app.state.token_service)
    try:
        return token_service.decode(token)
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None


@usage_router.get("/usage", response_model=UsageTotalsResponse)
async def get_usage(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsageTotalsResponse:
    """Return usage totals and 50 newest records for the authenticated tenant."""
    identity = _extract_identity(request)
    tenant_id: uuid.UUID = identity.tenant_id

    # Totals from the ledger (NOT the Redis advisory counter)
    totals_row = (
        await session.execute(
            text(
                "SELECT"
                "  COALESCE(SUM(cost_usd), 0) AS total_cost_usd,"
                "  COUNT(*) AS total_requests,"
                "  COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,"
                "  COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens"
                " FROM usage_records"
                " WHERE tenant_id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    total_cost_usd = Decimal(str(totals_row[0])) if totals_row else Decimal("0")
    total_requests = int(totals_row[1]) if totals_row else 0
    total_prompt_tokens = int(totals_row[2]) if totals_row else 0
    total_completion_tokens = int(totals_row[3]) if totals_row else 0

    # ≤50 newest records ordered by created_at DESC
    rows = (
        await session.execute(
            text(
                "SELECT id, model_id, prompt_tokens, completion_tokens,"
                "  cost_usd, status, created_at"
                " FROM usage_records"
                " WHERE tenant_id = :tid"
                " ORDER BY created_at DESC"
                " LIMIT 50"
            ),
            {"tid": str(tenant_id)},
        )
    ).fetchall()

    records = [
        UsageRecordItem(
            id=row[0],
            model_id=str(row[1]),
            prompt_tokens=int(row[2]),
            completion_tokens=int(row[3]),
            cost_usd=str(Decimal(str(row[4]))),
            status=int(row[5]),
            created_at=row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
        )
        for row in rows
    ]

    return UsageTotalsResponse(
        total_cost_usd=str(total_cost_usd),
        total_requests=total_requests,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        records=records,
    )


def _compute_window_bounds(
    window: str,
    start: str | None,
    end: str | None,
) -> tuple[datetime.datetime, datetime.datetime, str]:
    """Compute (window_start, window_end, granularity) from query params.

    window_end is exclusive — callers pass (end_date + 1 day) for inclusive end.
    Raises ProblemError 422 on invalid window value or invalid ISO date string.
    """
    # Validate window
    if window not in _VALID_WINDOWS:
        raise PAYLOAD_WINDOW_INVALID.exc(window=window)

    granularity = _GRANULARITY[window]
    now_utc = datetime.datetime.now(datetime.UTC)

    # Parse optional start/end overrides
    start_dt: datetime.datetime | None = None
    end_dt: datetime.datetime | None = None

    if start is not None:
        try:
            d = datetime.date.fromisoformat(start)
            start_dt = datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.UTC)
        except ValueError:
            raise PAYLOAD_START_DATE_INVALID.exc(start=start) from None

    if end is not None:
        try:
            d = datetime.date.fromisoformat(end)
            # Inclusive end: add 1 day so WHERE created_at < window_end covers the full end day
            end_dt = datetime.datetime(
                d.year, d.month, d.day, tzinfo=datetime.UTC
            ) + datetime.timedelta(days=1)
        except ValueError:
            raise PAYLOAD_END_DATE_INVALID.exc(end=end) from None

    # Determine window_start based on window type (if not overridden by start param)
    if start_dt is not None:
        window_start = start_dt
    else:
        if window == "month":
            window_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif window == "week":
            # ISO week: Monday-aligned date_trunc('week')
            # date_trunc('week') in Postgres gives Monday 00:00 UTC
            days_since_monday = now_utc.weekday()  # Monday=0
            window_start = (now_utc - datetime.timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:  # day
            window_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    # Determine window_end (exclusive upper bound).
    # When no explicit end is supplied, use the end of the current period so that
    # all records within the period are included (tests may seed records at any
    # hour within the day/week/month).
    if end_dt is not None:
        window_end = end_dt
    elif window == "month":
        # End of current month = start of next month
        if now_utc.month == 12:
            window_end = now_utc.replace(
                year=now_utc.year + 1,
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        else:
            window_end = now_utc.replace(
                month=now_utc.month + 1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
    elif window == "week":
        # End of current ISO week = start of next Monday
        days_until_next_monday = 7 - now_utc.weekday()
        window_end = (now_utc + datetime.timedelta(days=days_until_next_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:  # day
        # End of current UTC day = start of tomorrow
        window_end = (now_utc + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    return window_start, window_end, granularity


@usage_router.get("/spend", response_model=SpendWindowResponse)
async def get_spend(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query()] = "month",
    key_id: Annotated[str | None, Query()] = None,
    group_by: Annotated[str | None, Query()] = None,
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> SpendWindowResponse:
    """Return windowed spend aggregates from the usage_records ledger.

    Contract (FROZEN @ spend-windows — TASK.md §3):
    - JWT admin/owner auth required (same Bearer token as /admin/usage).
    - Aggregation uses date_trunc + NUMERIC SUM — no float arithmetic.
    - Tenant isolation: every query includes WHERE tenant_id = :tenant_id.
    - Empty window → 200 with zero totals + empty buckets (never 404).
    - Invalid window → 422 ERR_PAYLOAD_INVALID.
    """
    identity = _extract_identity(request)
    tenant_id: uuid.UUID = identity.tenant_id

    # Validate window param (422 on invalid)
    if window not in _VALID_WINDOWS:
        raise PAYLOAD_WINDOW_INVALID.exc(window=window)

    # Validate optional key_id filter — must belong to caller's tenant
    key_uuid: uuid.UUID | None = None
    if key_id is not None:
        try:
            key_uuid = uuid.UUID(key_id)
        except ValueError:
            raise PAYLOAD_KEY_ID_UUID_INVALID.exc(key_id=key_id) from None
        # Tenant-scope check: verify key belongs to this tenant
        key_row = (
            await session.execute(
                text("SELECT id FROM api_keys WHERE id = :kid AND tenant_id = :tid"),
                {"kid": str(key_uuid), "tid": str(tenant_id)},
            )
        ).fetchone()
        if key_row is None:
            raise KEY_NOT_FOUND_IN_TENANT.exc()

    # Compute window boundaries
    window_start, window_end, granularity = _compute_window_bounds(window, start, end)

    # Build query params dict
    params: dict[str, object] = {
        "tenant_id": str(tenant_id),
        "window_start": window_start.replace(tzinfo=None),  # asyncpg expects naive UTC
        "window_end": window_end.replace(tzinfo=None),
    }

    key_filter = ""
    if key_uuid is not None:
        key_filter = " AND key_id = :key_id"
        params["key_id"] = str(key_uuid)

    # ── Bucket aggregation query (per §3 contract SQL) ──────────────────────────
    # Uses date_trunc with NUMERIC arithmetic — exact, no float drift.
    # granularity is validated against _VALID_WINDOWS whitelist above; key_filter
    # is a hardcoded literal string " AND key_id = :key_id" or "". S608 is a
    # false positive — no user-supplied values are interpolated into the SQL text.
    _bucket_sql_str = (
        "SELECT"
        f"  date_trunc('{granularity}', created_at AT TIME ZONE 'UTC') AS bucket_start,"
        "  COUNT(*) AS requests,"
        "  COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
        "  COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
        "  COALESCE(SUM(cost_usd), 0) AS cost_usd"
        " FROM usage_records"
        " WHERE tenant_id = :tenant_id"
        "   AND created_at >= :window_start"
        "   AND created_at <  :window_end"
        f"{key_filter}"
        " GROUP BY bucket_start"
        " ORDER BY bucket_start ASC"
    )
    bucket_sql = text(_bucket_sql_str)

    bucket_rows = (await session.execute(bucket_sql, params)).fetchall()

    # Build buckets list
    buckets: list[SpendBucket] = []
    total_requests = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost = Decimal("0")

    for row in bucket_rows:
        bs = row[0]
        bucket_start_str = bs.isoformat() if hasattr(bs, "isoformat") else str(bs)
        b_cost = Decimal(str(row[4]))
        buckets.append(
            SpendBucket(
                bucket_start=bucket_start_str,
                requests=int(row[1]),
                prompt_tokens=int(row[2]),
                completion_tokens=int(row[3]),
                cost_usd=str(b_cost),
            )
        )
        total_requests += int(row[1])
        total_prompt_tokens += int(row[2])
        total_completion_tokens += int(row[3])
        total_cost += b_cost

    # ── Totals ────────────────────────────────────────────────────────────────
    # Computed as sum of bucket rows (same WHERE clause → exact reconciliation).
    if buckets:
        totals_bucket_start = buckets[0].bucket_start
    else:
        # Empty window: use the window_start as bucket_start
        totals_bucket_start = window_start.replace(tzinfo=None).isoformat()

    # bucket_end = last bucket end = window_end (exclusive) - 1 microsecond, or now()
    effective_end = window_end - datetime.timedelta(microseconds=1)
    totals_bucket_end = effective_end.replace(tzinfo=None).isoformat()

    totals = SpendTotals(
        bucket_start=totals_bucket_start,
        bucket_end=totals_bucket_end,
        requests=total_requests,
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
        cost_usd=str(total_cost),
    )

    # ── group_by whitelist validation (422 for unknown values) ───────────────
    _VALID_GROUP_BY = {"key_id", "team_id"}
    if group_by is not None and group_by not in _VALID_GROUP_BY:
        raise PAYLOAD_GROUP_BY_INVALID.exc(
            valid=", ".join(sorted(_VALID_GROUP_BY)), group_by=group_by
        )

    # ── Breakdown (group_by=key_id or group_by=team_id) ──────────────────────
    breakdown: list[SpendBreakdownItem] | list[TeamSpendBreakdownItem] | None = None
    if group_by == "key_id":
        _breakdown_sql_str = (
            "SELECT"
            "  key_id,"
            "  COUNT(*) AS requests,"
            "  COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
            "  COALESCE(SUM(completion_tokens), 0) AS completion_tokens,"
            "  COALESCE(SUM(cost_usd), 0) AS cost_usd"
            " FROM usage_records"
            " WHERE tenant_id = :tenant_id"
            "   AND created_at >= :window_start"
            "   AND created_at <  :window_end"
            f"{key_filter}"
            " GROUP BY key_id"
            " ORDER BY cost_usd DESC"
        )
        breakdown_sql = text(_breakdown_sql_str)
        breakdown_rows = (await session.execute(breakdown_sql, params)).fetchall()
        breakdown = [
            SpendBreakdownItem(
                key_id=uuid.UUID(str(row[0])),
                requests=int(row[1]),
                prompt_tokens=int(row[2]),
                completion_tokens=int(row[3]),
                cost_usd=str(Decimal(str(row[4]))),
            )
            for row in breakdown_rows
        ]
    elif group_by == "team_id":
        # Group by current team attribution (JOIN api_keys to get team_id at query time).
        # Un-teamed keys (api_keys.team_id IS NULL) roll into a single NULL-team bucket.
        # Results ordered cost_usd DESC, NULL-team bucket last (NULLS LAST on team_id).
        # granularity and key_filter are safe literals (validated above); S608 false positive.
        _team_breakdown_sql_str = (
            "SELECT"
            "  ak.team_id,"
            "  t.name AS team_name,"
            "  COUNT(*) AS requests,"
            "  COALESCE(SUM(ur.prompt_tokens), 0) AS prompt_tokens,"
            "  COALESCE(SUM(ur.completion_tokens), 0) AS completion_tokens,"
            "  COALESCE(SUM(ur.cost_usd), 0) AS cost_usd"
            " FROM usage_records ur"
            " JOIN api_keys ak ON ur.key_id = ak.id"
            " LEFT JOIN teams t ON ak.team_id = t.id"
            " WHERE ur.tenant_id = :tenant_id"
            "   AND ur.created_at >= :window_start"
            "   AND ur.created_at <  :window_end"
            f"{key_filter.replace('key_id', 'ur.key_id') if key_filter else ''}"
            " GROUP BY ak.team_id, t.name"
            " ORDER BY COALESCE(SUM(ur.cost_usd), 0) DESC NULLS LAST,"
            "          ak.team_id NULLS LAST"
        )
        team_breakdown_sql = text(_team_breakdown_sql_str)
        team_breakdown_rows = (await session.execute(team_breakdown_sql, params)).fetchall()

        # Second aggregation: ledger_cost_usd — SUM(cost_usd) grouped by usage_records.team_id
        # (historical, at-request-time attribution). NULL-safe: NULL team_id gets its own bucket.
        # Strictly tenant-scoped (WHERE tenant_id = :tenant_id).
        _ledger_sql_str = (
            "SELECT"
            "  ur.team_id,"
            "  COALESCE(SUM(ur.cost_usd), 0) AS ledger_cost_usd"
            " FROM usage_records ur"
            " WHERE ur.tenant_id = :tenant_id"
            "   AND ur.created_at >= :window_start"
            "   AND ur.created_at <  :window_end"
            " GROUP BY ur.team_id"
        )
        ledger_rows = (await session.execute(text(_ledger_sql_str), params)).fetchall()
        # Build lookup: team_id (UUID or None) → ledger_cost_usd (Decimal)
        # NULL team_id in SQL comes back as Python None — handled explicitly.
        _ledger_map: dict[uuid.UUID | None, Decimal] = {}
        for lr in ledger_rows:
            _lid = uuid.UUID(str(lr[0])) if lr[0] is not None else None
            _ledger_map[_lid] = Decimal(str(lr[1]))

        breakdown = [
            TeamSpendBreakdownItem(
                team_id=uuid.UUID(str(row[0])) if row[0] is not None else None,
                team_name=str(row[1]) if row[1] is not None else None,
                requests=int(row[2]),
                prompt_tokens=int(row[3]),
                completion_tokens=int(row[4]),
                cost_usd=str(Decimal(str(row[5]))),
                ledger_cost_usd=str(
                    _ledger_map.get(
                        uuid.UUID(str(row[0])) if row[0] is not None else None,
                        Decimal("0"),
                    )
                ),
            )
            for row in team_breakdown_rows
        ]

    return SpendWindowResponse(
        window=window,
        bucket_size=granularity,
        totals=totals,
        buckets=buckets,
        breakdown=breakdown,
    )


@usage_router.get("/reconciliation", response_model=ReconciliationResponse)
async def get_reconciliation(
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query()] = "month",
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> ReconciliationResponse:
    """Return the caller's tenant reconciliation drift for a window (FROZEN @ v1 — TASK.md §3).

    Owner/admin-scoped (member → 403) and TENANT-SCOPED: tenant_id is always the caller's own
    tenant — the aggregate is never invoked operator-wide here. A thin read over the v29
    reconcile_window aggregate; reuses /admin/spend's window params and _compute_window_bounds
    (422 on a bad window/date). Empty window → 200 with explicit zeros (never 404). READ-ONLY.
    """
    window_start, window_end, _granularity = _compute_window_bounds(window, start, end)

    summary = await reconcile_window(
        session, window_start, window_end, tenant_id=identity.tenant_id
    )

    return ReconciliationResponse(
        window_from=summary.window_from.isoformat(),
        window_to=summary.window_to.isoformat(),
        provider_cost_total=str(summary.provider_cost_total),
        billed_total=str(summary.billed_total),
        drift=str(summary.drift),
        unbilled_upstream_cost=str(summary.unbilled_upstream_cost),
        unbilled_rows=summary.unbilled_rows,
        catalog_billed_total=str(summary.catalog_billed_total),
        by_source=[
            ReconciliationSourceItem(
                usage_source=item.usage_source,
                rows=item.rows,
                provider_cost=str(item.provider_cost),
            )
            for item in summary.by_source
        ],
    )


# ---------------------------------------------------------------------------
# GET /admin/alerts — paginated alert/event history (FROZEN @ v1 — TASK.md §3)
# ---------------------------------------------------------------------------
_ALERTS_DEFAULT_LIMIT = 50
_ALERTS_MAX_LIMIT = 100
_ALERTS_READ_TIMEOUT_SECONDS = 30.0


class AlertEventItem(BaseModel):
    """One alert_events row as returned by GET /admin/alerts."""

    model_config = ConfigDict(frozen=True)

    id: str
    event_type: str
    payload: dict[str, object]
    created_at: str
    delivered: bool
    delivered_at: str | None


class AlertListResponse(BaseModel):
    """Paginated alert history envelope: { items, total }."""

    model_config = ConfigDict(frozen=True)

    items: list[AlertEventItem]
    total: int


def _parse_pagination(limit: str | None, offset: str | None) -> tuple[int, int]:
    """Parse limit (1..100, default 50) + offset (>=0, default 0).

    Parsed manually (not via FastAPI int coercion) so a bad value maps to the catalog
    ERR_PAYLOAD_INVALID code rather than FastAPI's own 422 shape.
    """
    parsed_limit = _ALERTS_DEFAULT_LIMIT
    if limit is not None:
        try:
            parsed_limit = int(limit)
        except ValueError:
            raise PAYLOAD_INVALID.exc() from None
        if parsed_limit < 1 or parsed_limit > _ALERTS_MAX_LIMIT:
            raise PAYLOAD_INVALID.exc()

    parsed_offset = 0
    if offset is not None:
        try:
            parsed_offset = int(offset)
        except ValueError:
            raise PAYLOAD_INVALID.exc() from None
        if parsed_offset < 0:
            raise PAYLOAD_INVALID.exc()

    return parsed_limit, parsed_offset


def _coerce_payload(raw: object) -> dict[str, object]:
    """alert_events.payload is JSONB; asyncpg may surface it as a JSON string or a dict."""
    if isinstance(raw, dict):
        return cast("dict[str, object]", raw)
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


@usage_router.get("/alerts", response_model=AlertListResponse)
async def get_alerts(
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[str | None, Query()] = None,
    offset: Annotated[str | None, Query()] = None,
) -> AlertListResponse:
    """Return the caller's tenant-visible alert history (FROZEN @ v1 — TASK.md §3).

    Owner/admin-scoped (member → 403). Visibility is the caller's own tenant rows PLUS
    platform system events (tenant_id IS NULL: circuit-open, upstream-health, drift) —
    Tin-approved at the freeze; system rows belong to no tenant (operational-info, not
    cross-tenant tenant-data). Newest-first, paginated. READ-ONLY — never writes
    delivered_at or any row.
    """
    parsed_limit, parsed_offset = _parse_pagination(limit, offset)
    tenant_id: uuid.UUID = identity.tenant_id
    where = "WHERE tenant_id = :tid OR tenant_id IS NULL"

    async with asyncio.timeout(_ALERTS_READ_TIMEOUT_SECONDS):
        total_row = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM alert_events {where}"),
                {"tid": str(tenant_id)},
            )
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

        rows = (
            await session.execute(
                text(
                    "SELECT id, event_type, payload, created_at, delivered_at"
                    f" FROM alert_events {where}"
                    " ORDER BY created_at DESC, id DESC"
                    " LIMIT :limit OFFSET :offset"
                ),
                {"tid": str(tenant_id), "limit": parsed_limit, "offset": parsed_offset},
            )
        ).fetchall()

    items = [
        AlertEventItem(
            id=str(row[0]),
            event_type=str(row[1]),
            payload=_coerce_payload(row[2]),
            created_at=row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
            delivered=row[4] is not None,
            delivered_at=(
                row[4].isoformat() if row[4] is not None and hasattr(row[4], "isoformat") else None
            ),
        )
        for row in rows
    ]

    return AlertListResponse(items=items, total=total)


# ---------------------------------------------------------------------------
# GET /admin/health/upstreams — per-upstream up/down (FROZEN @ v1 — TASK.md §3)
# ---------------------------------------------------------------------------
# MONITORED today is exactly the upstream the health checker actually pings (OpenRouter).
# We deliberately do NOT fabricate up/down rows for providers with no health ping
# (anthropic/gemini/bedrock/azure/openai) — reporting fake-green would be dishonest. The
# response is a LIST so it extends for free when per-provider pingers land (filed spec delta).
_MONITORED_UPSTREAMS: tuple[str, ...] = ("openrouter",)
_HEALTH_FAIL_EVENT = "upstream_health_fail"
_HEALTH_RECOVERED_EVENT = "upstream_health_recovered"
_HEALTH_READ_TIMEOUT_SECONDS = 30.0


class UpstreamHealthItem(BaseModel):
    """The current health status of one monitored upstream."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: str  # "up" | "down"
    last_event_at: str | None
    last_event_type: str | None


class UpstreamHealthResponse(BaseModel):
    """Envelope: { checked_at, upstreams } — status as of the query time."""

    model_config = ConfigDict(frozen=True)

    checked_at: str
    upstreams: list[UpstreamHealthItem]


@usage_router.get("/health/upstreams", response_model=UpstreamHealthResponse)
async def get_upstream_health(
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UpstreamHealthResponse:
    """Return per-upstream up/down derived from durable health events (FROZEN @ v1 — TASK.md §3).

    Owner/admin-scoped (member → 403). Status is GLOBAL platform state, derived from the
    NULL-tenant system rows in alert_events (the same operational rows GET /admin/alerts reads —
    Tin-approved at the alerts freeze; no new tenant-scope relaxation). For each monitored
    upstream: the latest of {upstream_health_fail, upstream_health_recovered} decides up/down; no
    health event ever → up with null last_event_*. READ-ONLY — never writes a row.
    """
    checked_at = datetime.datetime.now(datetime.UTC).isoformat()
    upstreams: list[UpstreamHealthItem] = []

    async with asyncio.timeout(_HEALTH_READ_TIMEOUT_SECONDS):
        for name in _MONITORED_UPSTREAMS:
            # OpenRouter is the only emitter today, so all health events belong to it. When
            # per-provider pingers land, this gains a payload/provider filter per upstream.
            row = (
                await session.execute(
                    text(
                        "SELECT event_type, created_at FROM alert_events"
                        " WHERE tenant_id IS NULL"
                        " AND event_type IN (:fail, :recovered)"
                        " ORDER BY created_at DESC, id DESC"
                        " LIMIT 1"
                    ),
                    {"fail": _HEALTH_FAIL_EVENT, "recovered": _HEALTH_RECOVERED_EVENT},
                )
            ).fetchone()

            if row is None:
                upstreams.append(
                    UpstreamHealthItem(
                        name=name, status="up", last_event_at=None, last_event_type=None
                    )
                )
                continue

            last_event_type = str(row[0])
            last_event_at = (
                row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])
            )
            status = "down" if last_event_type == _HEALTH_FAIL_EVENT else "up"
            upstreams.append(
                UpstreamHealthItem(
                    name=name,
                    status=status,
                    last_event_at=last_event_at,
                    last_event_type=last_event_type,
                )
            )

    return UpstreamHealthResponse(checked_at=checked_at, upstreams=upstreams)


# ---------------------------------------------------------------------------
# GET /admin/ratelimits — live per-key rpm/tpm consumption (FROZEN @ v1 — TASK.md §3)
# ---------------------------------------------------------------------------
# The live counters are the SAME Redis keys RedisLuaRateLimiter writes (the single source of
# truth — no drift). READ-ONLY: ZCARD + GET only, never ZADD/INCR/EXPIRE. Design-for-failure:
# Redis unavailable/erroring → counters reported as null (unknown, never 0 or 500), mirroring
# the limiter's own fail-open. Tenant-scoped: only the caller's tenant's keys.
_RATELIMIT_DB_TIMEOUT_SECONDS = 30.0
_RATELIMIT_REDIS_TIMEOUT_SECONDS = 5.0


class RatelimitItem(BaseModel):
    """One API key's live rpm/tpm consumption vs its configured limit."""

    model_config = ConfigDict(frozen=True)

    key_id: str
    name: str
    rpm_limit: int | None
    tpm_limit: int | None
    rpm_current: int | None  # null when Redis is unavailable
    tpm_current: float | None  # null when Redis is unavailable


class RatelimitsResponse(BaseModel):
    """Envelope: { keys } — per-key live counters for the caller's tenant."""

    model_config = ConfigDict(frozen=True)

    keys: list[RatelimitItem]


async def _read_ratelimit_counters(
    redis_client: object, key_ids: list[str]
) -> dict[str, tuple[int | None, float | None]]:
    """Read live (rpm, tpm) per key from Redis, READ-ONLY. Fail-open: ANY Redis error (or an
    absent client) → an empty map, so every key reports null (unknown) — never 0, never 500."""
    if redis_client is None:
        return {}
    counters: dict[str, tuple[int | None, float | None]] = {}
    try:
        async with asyncio.timeout(_RATELIMIT_REDIS_TIMEOUT_SECONDS):
            for key_id in key_ids:
                rpm = int(await redis_client.zcard(f"ratelimit:rpm:{key_id}"))  # type: ignore[attr-defined]
                raw = await redis_client.get(f"ratelimit:tpm_sum:{key_id}")  # type: ignore[attr-defined]
                tpm = float(raw) if raw is not None else 0.0
                counters[key_id] = (rpm, tpm)
    except (RedisError, OSError, ValueError, TimeoutError):
        return {}  # fail-open — all counters unknown
    return counters


@usage_router.get("/ratelimits", response_model=RatelimitsResponse)
async def get_ratelimits(
    request: Request,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RatelimitsResponse:
    """Return the caller's tenant's per-key live rpm/tpm counters (FROZEN @ v1 — TASK.md §3).

    Owner/admin-scoped (member → 403). Tenant-scoped: only this tenant's keys are listed, so the
    Redis counters read (keyed by key_id) are implicitly tenant-isolated — another tenant's
    counters are never reached. READ-ONLY; Redis-down → null counters, still 200.
    """
    tenant_id: uuid.UUID = identity.tenant_id

    async with asyncio.timeout(_RATELIMIT_DB_TIMEOUT_SECONDS):
        rows = (
            await session.execute(
                text(
                    "SELECT id, name, rpm_limit, tpm_limit FROM api_keys"
                    " WHERE tenant_id = :tid AND revoked_at IS NULL"
                    " ORDER BY created_at ASC, id ASC"
                ),
                {"tid": str(tenant_id)},
            )
        ).fetchall()

    key_ids = [str(row[0]) for row in rows]
    counters = await _read_ratelimit_counters(
        getattr(request.app.state, "redis_client", None), key_ids
    )

    items = [
        RatelimitItem(
            key_id=str(row[0]),
            name=str(row[1]),
            rpm_limit=int(row[2]) if row[2] is not None else None,
            tpm_limit=int(row[3]) if row[3] is not None else None,
            rpm_current=counters.get(str(row[0]), (None, None))[0],
            tpm_current=counters.get(str(row[0]), (None, None))[1],
        )
        for row in rows
    ]

    return RatelimitsResponse(keys=items)
