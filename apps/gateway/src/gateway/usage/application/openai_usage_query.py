"""Use case for the OpenAI-wire tenant usage/costs read API.

tenant-usage-costs-api TASK.md §3 (FROZEN @ v1). Authenticates the caller's ``sk-`` key via
the existing AuthzUseCase (same seam /v1/chat uses), validates + parses every query param
(R3-R9), enforces the per-bucket_width span cap (R7), walks buckets by an opaque keyset
cursor (M7), and shapes the OpenAI page/bucket envelope. READ-ONLY over usage_records — no
write, no counter movement (After).

Hard tenant scoping (M3) is delegated to the port: every SQL query ANDs
``tenant_id = <caller tenant>``; the filters (models/api_key_ids) are intersected with it, so
a foreign/unknown id yields zero rows and never a 404 (R-FILTER, anti-enumeration).
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    USAGE_BUCKET_WIDTH_INVALID,
    USAGE_END_TIME_INVALID,
    USAGE_GROUP_BY_INVALID,
    USAGE_LIMIT_INVALID,
    USAGE_PAGE_INVALID,
    USAGE_RANGE_TOO_LARGE,
    USAGE_START_TIME_INVALID,
    ErrorSpec,
)
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.usage.domain.openai_usage import (
    AggregatedBucketRow,
    AggregationQuery,
    UsageAggregationPort,
    UsageQueryError,
)

Endpoint = Literal["completions", "costs"]

# bucket_width token → (date_trunc unit, width seconds, span cap seconds).
_COMPLETIONS_WIDTHS: dict[str, tuple[str, int, int]] = {
    "1m": ("minute", 60, 7 * 86400),
    "1h": ("hour", 3600, 92 * 86400),
    "1d": ("day", 86400, 366 * 86400),
}
_COSTS_WIDTHS: dict[str, tuple[str, int, int]] = {
    "1d": ("day", 86400, 366 * 86400),
}

_COMPLETIONS_GROUP_FIELDS = frozenset({"model", "api_key_id"})
_COSTS_GROUP_FIELDS = frozenset({"line_item"})

_DEFAULT_LIMIT = 7
_MIN_LIMIT = 1
_MAX_LIMIT = 180

_CURSOR_PREFIX = "b:"
# urlsafe base64 alphabet (RFC 4648 §5) + optional '=' padding — urlsafe_b64decode has no
# `validate` kwarg, so we reject any non-alphabet cursor up front (R8) rather than let it
# silently decode garbage.
_CURSOR_ALPHABET = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


@dataclass(frozen=True, slots=True)
class RawUsageParams:
    """The raw (still-string) query values, exactly as they arrive on the wire."""

    start_time: str | None
    end_time: str | None
    bucket_width: str | None
    group_by: str | None
    models: str | None
    api_key_ids: str | None
    limit: str | None
    page: str | None


def _parse_int(value: str) -> int:
    """int() but rejecting the leading-zero / whitespace / sign quirks int() tolerates only
    where they would be surprising; we accept a plain optional-signed decimal integer."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("empty")
    return int(stripped)


def _encode_cursor(bucket_unix: int) -> str:
    token = f"{_CURSOR_PREFIX}{bucket_unix}".encode()
    return base64.urlsafe_b64encode(token).decode("ascii")


def _decode_cursor(cursor: str) -> int:
    if not _CURSOR_ALPHABET.match(cursor):
        raise UsageQueryError(USAGE_PAGE_INVALID)
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise UsageQueryError(USAGE_PAGE_INVALID) from exc
    if not raw.startswith(_CURSOR_PREFIX):
        raise UsageQueryError(USAGE_PAGE_INVALID)
    try:
        return int(raw[len(_CURSOR_PREFIX) :])
    except ValueError as exc:
        raise UsageQueryError(USAGE_PAGE_INVALID) from exc


def _unix_to_naive_utc(unix_seconds: int, spec: ErrorSpec) -> datetime:
    """Convert Unix seconds → naive-UTC datetime, mapping any out-of-range value to ``spec``.

    ``datetime.fromtimestamp`` raises for timestamps outside the supported epoch range
    (OverflowError past platform ``time_t``; ValueError past year 9999; OSError on some
    platforms). A validly-parsed but astronomically large integer must therefore be a 422
    (R3/R4/R8), NOT an uncaught 500 — so the conversion is guarded at every call site.
    """
    try:
        return datetime.fromtimestamp(unix_seconds, tz=UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError) as exc:
        raise UsageQueryError(spec) from exc


def _naive_utc_to_unix(dt: datetime) -> int:
    return int(dt.replace(tzinfo=UTC).timestamp())


@dataclass(frozen=True, slots=True)
class _ValidatedParams:
    unit: str
    width_seconds: int
    start: datetime
    end: datetime
    models: tuple[str, ...]
    api_key_ids: tuple[str, ...]
    group_model: bool
    group_api_key_id: bool
    group_line_item: bool
    limit: int
    after_bucket: datetime | None


class OpenAiUsageQueryUseCase:
    """Auth + validate + aggregate + paginate for both /v1/organization usage endpoints."""

    def __init__(self, authz: AuthzUseCase, repo: UsageAggregationPort) -> None:
        self._authz = authz
        self._repo = repo

    async def execute(
        self, *, endpoint: Endpoint, raw_key: str, params: RawUsageParams
    ) -> dict[str, object]:
        tenant_id = await self._authenticate(raw_key)
        validated = self._validate(endpoint, params)
        return await self._run(endpoint, tenant_id, validated)

    # ── auth (R1/R2) ────────────────────────────────────────────────────────
    async def _authenticate(self, raw_key: str) -> str:
        try:
            authz = await self._authz.execute(raw_key)
        except InvalidApiKeyError as exc:
            raise UsageQueryError(AUTH_KEY_INVALID) from exc
        if authz.expires_at is not None:
            exp = authz.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if exp <= datetime.now(tz=UTC):
                raise UsageQueryError(AUTH_KEY_EXPIRED)
        return str(authz.tenant_id)

    # ── param validation (R3-R9) ─────────────────────────────────────────────
    def _validate(self, endpoint: Endpoint, params: RawUsageParams) -> _ValidatedParams:
        widths = _COMPLETIONS_WIDTHS if endpoint == "completions" else _COSTS_WIDTHS
        group_fields = (
            _COMPLETIONS_GROUP_FIELDS if endpoint == "completions" else _COSTS_GROUP_FIELDS
        )

        # start_time (R3): required, integer, non-negative.
        if params.start_time is None:
            raise UsageQueryError(USAGE_START_TIME_INVALID)
        try:
            start_unix = _parse_int(params.start_time)
        except ValueError as exc:
            raise UsageQueryError(USAGE_START_TIME_INVALID) from exc
        if start_unix < 0:
            raise UsageQueryError(USAGE_START_TIME_INVALID)

        # end_time (R4): optional (default now), integer; if provided must be > start_time.
        if params.end_time is None:
            end_unix = int(datetime.now(tz=UTC).timestamp())
        else:
            try:
                end_unix = _parse_int(params.end_time)
            except ValueError as exc:
                raise UsageQueryError(USAGE_END_TIME_INVALID) from exc
            if end_unix <= start_unix:
                raise UsageQueryError(USAGE_END_TIME_INVALID)

        # bucket_width (R5/M6): default 1d, must be allowed for the endpoint.
        width_token = params.bucket_width if params.bucket_width is not None else "1d"
        if width_token not in widths:
            raise UsageQueryError(USAGE_BUCKET_WIDTH_INVALID)
        unit, width_seconds, span_cap = widths[width_token]

        # span cap (R7) — only meaningful for a forward window.
        if end_unix > start_unix and (end_unix - start_unix) > span_cap:
            raise UsageQueryError(USAGE_RANGE_TOO_LARGE)

        # group_by (R6): csv ⊆ endpoint's allowed set.
        group_model = group_api_key_id = group_line_item = False
        if params.group_by:
            for field in (t.strip() for t in params.group_by.split(",") if t.strip()):
                if field not in group_fields:
                    raise UsageQueryError(USAGE_GROUP_BY_INVALID)
                if field == "model":
                    group_model = True
                elif field == "api_key_id":
                    group_api_key_id = True
                elif field == "line_item":
                    group_line_item = True

        # limit (R9): integer 1..180, default 7.
        if params.limit is None:
            limit = _DEFAULT_LIMIT
        else:
            try:
                limit = _parse_int(params.limit)
            except ValueError as exc:
                raise UsageQueryError(USAGE_LIMIT_INVALID) from exc
            if limit < _MIN_LIMIT or limit > _MAX_LIMIT:
                raise UsageQueryError(USAGE_LIMIT_INVALID)

        # page (R8): opaque cursor over the last returned bucket start_time.
        after_bucket: datetime | None = None
        if params.page:
            # A validly-base64-decoded but out-of-range cursor int maps to the SAME
            # USAGE_PAGE_INVALID as a malformed cursor — no oracle distinguishing a forged
            # far-future cursor from garbage (R8, anti-enumeration).
            after_bucket = _unix_to_naive_utc(_decode_cursor(params.page), USAGE_PAGE_INVALID)

        models = _split_csv(params.models)
        api_key_ids = _split_csv(params.api_key_ids)

        return _ValidatedParams(
            unit=unit,
            width_seconds=width_seconds,
            start=_unix_to_naive_utc(start_unix, USAGE_START_TIME_INVALID),
            end=_unix_to_naive_utc(end_unix, USAGE_END_TIME_INVALID),
            models=models,
            api_key_ids=api_key_ids,
            group_model=group_model,
            group_api_key_id=group_api_key_id,
            group_line_item=group_line_item,
            limit=limit,
            after_bucket=after_bucket,
        )

    # ── aggregate + paginate + shape ─────────────────────────────────────────
    async def _run(
        self, endpoint: Endpoint, tenant_id: str, v: _ValidatedParams
    ) -> dict[str, object]:
        query = AggregationQuery(
            tenant_id=tenant_id,
            unit=v.unit,
            start=v.start,
            end=v.end,
            models=v.models,
            api_key_ids=v.api_key_ids,
            group_model=v.group_model,
            group_api_key_id=v.group_api_key_id,
            group_line_item=v.group_line_item,
            after_bucket=v.after_bucket,
            limit=v.limit,
        )

        populated = await self._repo.list_populated_buckets(query)
        if not populated:
            return {"object": "page", "data": [], "has_more": False, "next_page": None}

        has_more = len(populated) > v.limit
        page_buckets = populated[: v.limit]
        first_bucket = page_buckets[0]
        last_bucket_end = page_buckets[-1] + timedelta(seconds=v.width_seconds)

        rows = await self._repo.aggregate(query, first_bucket, last_bucket_end)

        buckets = self._build_buckets(endpoint, page_buckets, rows, v.width_seconds)
        next_page = _encode_cursor(_naive_utc_to_unix(page_buckets[-1])) if has_more else None
        return {
            "object": "page",
            "data": buckets,
            "has_more": has_more,
            "next_page": next_page,
        }

    def _build_buckets(
        self,
        endpoint: Endpoint,
        page_buckets: list[datetime],
        rows: list[AggregatedBucketRow],
        width_seconds: int,
    ) -> list[dict[str, object]]:
        by_bucket: dict[datetime, list[AggregatedBucketRow]] = {b: [] for b in page_buckets}
        for row in rows:
            # aggregate() is range-bounded to the page window; every row lands on a page bucket.
            by_bucket.setdefault(row.bucket_start, []).append(row)

        buckets: list[dict[str, object]] = []
        for bucket_start in page_buckets:
            start_unix = _naive_utc_to_unix(bucket_start)
            results = [self._result(endpoint, row) for row in by_bucket.get(bucket_start, [])]
            buckets.append(
                {
                    "object": "bucket",
                    "start_time": start_unix,
                    "end_time": start_unix + width_seconds,
                    "results": results,
                }
            )
        return buckets

    def _result(self, endpoint: Endpoint, row: AggregatedBucketRow) -> dict[str, object]:
        if endpoint == "completions":
            return {
                "object": "organization.usage.completions.result",
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "num_model_requests": row.num_model_requests,
                "model": row.group_model,
                "api_key_id": row.group_api_key_id,
            }
        # costs — billed cost_usd ONLY (never provider_cost/cost_basis/markup). The value is
        # a JSON number rendered from the exact Postgres NUMERIC SUM (no float in the SUM).
        return {
            "object": "organization.costs.result",
            "amount": {"value": _decimal_to_wire(row.cost_usd), "currency": "usd"},
            "line_item": row.group_line_item,
            "project_id": None,
        }


def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(token.strip() for token in raw.split(",") if token.strip())


def _decimal_to_wire(value: Decimal) -> float:
    """Render the exact NUMERIC SUM as a JSON number at the edge only.

    The SUM itself is computed in Postgres over NUMERIC (Decimal-exact, no float drift); this
    final scalar cast is the single float conversion, at serialization time, mirroring the
    OpenAI ``amount.value`` JSON-number wire shape.
    """
    return float(value)
