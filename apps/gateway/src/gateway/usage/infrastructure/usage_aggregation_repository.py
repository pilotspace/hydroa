"""SQLAlchemy read-only bucketed aggregation over usage_records (OpenAI-wire usage/costs).

tenant-usage-costs-api TASK.md §3 (FROZEN @ v1). A SIBLING of usage_repository.py — that
FROZEN file is never touched. Mirrors the proven Decimal-exact, tenant-scoped date_trunc +
NUMERIC SUM pattern of usage/api/router.py::get_spend (different wire, same math), served by
the existing ``usage_records_tenant_created_id_idx`` — NO new index, NO migration.

Injection safety: EVERY user-supplied value is bound as a query parameter. The only string
interpolated into the SQL text is ``unit`` — one of the {minute, hour, day} literals selected
from a whitelist in the use case — exactly as get_spend interpolates its validated
``granularity``. S608 on that literal is a false positive (no user value is interpolated).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.usage.domain.openai_usage import AggregatedBucketRow, AggregationQuery

# date_trunc unit is a hardcoded whitelist literal (never a user value) — parity with
# get_spend's validated granularity interpolation. Bound params carry every user value.
_ALLOWED_UNITS = ("minute", "hour", "day")

# Design-for-failure: bound every DB read so a slow/stuck query can never hang the request
# indefinitely (mirrors get_alerts/get_audit/get_slo's asyncio.timeout in usage/api/router.py).
# Read-only + idempotent — a timeout simply surfaces as an error, no half-write to roll back.
_READ_TIMEOUT_SECONDS = 30.0


def _filter_clause(query: AggregationQuery, params: dict[str, object]) -> str:
    """Append the optional model/api_key filters as bound-param ANY() predicates.

    Both are ALWAYS intersected with the tenant_id AND already present in the caller's SQL,
    so a foreign or unknown id simply matches zero rows (anti-enumeration — never a 404).
    """
    clause = ""
    if query.models:
        clause += " AND model_id = ANY(:models)"
        params["models"] = list(query.models)
    if query.api_key_ids:
        clause += " AND key_id::text = ANY(:api_key_ids)"
        params["api_key_ids"] = list(query.api_key_ids)
    return clause


class SqlAlchemyUsageAggregationRepository:
    """Read-only aggregation repo implementing UsageAggregationPort."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_populated_buckets(self, query: AggregationQuery) -> list[datetime]:
        if query.unit not in _ALLOWED_UNITS:  # defensive — the use case already whitelists
            raise ValueError(f"unsupported bucket unit: {query.unit!r}")

        params: dict[str, object] = {
            "tenant_id": query.tenant_id,
            "start": query.start,
            "end": query.end,
            "limit_plus_one": query.limit + 1,
        }
        cursor_clause = ""
        if query.after_bucket is not None:
            cursor_clause = f" AND date_trunc('{query.unit}', created_at) > :after"
            params["after"] = query.after_bucket

        filter_clause = _filter_clause(query, params)

        sql = text(
            "SELECT date_trunc('" + query.unit + "', created_at) AS bucket"  # noqa: S608 — unit is a whitelist literal; every user value is bound
            " FROM usage_records"
            " WHERE tenant_id = :tenant_id"
            "   AND created_at >= :start"
            "   AND created_at <  :end"
            f"{filter_clause}"
            f"{cursor_clause}"
            " GROUP BY bucket"
            " ORDER BY bucket ASC"
            " LIMIT :limit_plus_one"
        )
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            rows = (await self._session.execute(sql, params)).fetchall()
        return [row[0] for row in rows]

    async def aggregate(
        self, query: AggregationQuery, first_bucket: datetime, last_bucket_end: datetime
    ) -> list[AggregatedBucketRow]:
        if query.unit not in _ALLOWED_UNITS:  # defensive — the use case already whitelists
            raise ValueError(f"unsupported bucket unit: {query.unit!r}")

        params: dict[str, object] = {
            "tenant_id": query.tenant_id,
            "first_bucket": first_bucket,
            "last_bucket_end": last_bucket_end,
        }

        # Optional group dimensions — each a fixed column expression (no user value).
        group_selects = ""
        group_by_cols = ""
        if query.group_model:
            group_selects += " model_id AS g_model,"
            group_by_cols += ", model_id"
        else:
            group_selects += " NULL::text AS g_model,"
        if query.group_api_key_id:
            group_selects += " key_id::text AS g_api_key,"
            group_by_cols += ", key_id"
        else:
            group_selects += " NULL::text AS g_api_key,"
        if query.group_line_item:
            group_selects += " model_id AS g_line_item,"
            group_by_cols += ", model_id"
        else:
            group_selects += " NULL::text AS g_line_item,"

        filter_clause = _filter_clause(query, params)

        sql = text(
            "SELECT date_trunc('" + query.unit + "', created_at) AS bucket,"  # noqa: S608 — unit is a whitelist literal; every user value is bound
            f"{group_selects}"
            "  COALESCE(SUM(prompt_tokens), 0) AS input_tokens,"
            "  COALESCE(SUM(completion_tokens), 0) AS output_tokens,"
            "  COUNT(*) AS num_model_requests,"
            "  COALESCE(SUM(cost_usd), 0) AS cost_usd"
            " FROM usage_records"
            " WHERE tenant_id = :tenant_id"
            "   AND created_at >= :first_bucket"
            "   AND created_at <  :last_bucket_end"
            f"{filter_clause}"
            " GROUP BY bucket" + group_by_cols + ""
            " ORDER BY bucket ASC"
        )
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            rows = (await self._session.execute(sql, params)).fetchall()
        return [
            AggregatedBucketRow(
                bucket_start=row[0],
                group_model=row[1],
                group_api_key_id=row[2],
                group_line_item=row[3],
                input_tokens=int(row[4]),
                output_tokens=int(row[5]),
                num_model_requests=int(row[6]),
                cost_usd=Decimal(str(row[7])),
            )
            for row in rows
        ]
