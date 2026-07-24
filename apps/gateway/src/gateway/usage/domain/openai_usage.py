"""Domain projections + port for the OpenAI-wire tenant usage/costs read API.

tenant-usage-costs-api TASK.md §3 (FROZEN @ v1). Zero framework / infrastructure
imports — pure dataclasses, a typing.Protocol port, and a typed query error that the
api layer maps to the OpenAI-style ``{"error": <code>}`` wire envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from gateway.core.error_catalog import ErrorSpec


class UsageQueryError(Exception):
    """A validation / auth failure carrying the catalog spec to render on the wire.

    The api layer converts this to ``{"error": spec.code}`` with HTTP ``spec.status`` —
    the OpenAI-SDK error envelope (a bare code string), NOT the RFC-9457 problem+json
    body the global ProblemError handler emits.
    """

    def __init__(self, spec: ErrorSpec) -> None:
        super().__init__(spec.code)
        self.status = spec.status
        self.code = spec.code


@dataclass(frozen=True, slots=True)
class AggregatedBucketRow:
    """One aggregated group within one time bucket, as returned by the port.

    ``bucket_start`` is a naive-UTC datetime (the ``date_trunc`` result). ``group_model``
    / ``group_api_key_id`` / ``group_line_item`` are populated only for the corresponding
    ``group_by`` dimension; all None on an ungrouped aggregate.
    """

    bucket_start: datetime
    group_model: str | None
    group_api_key_id: str | None
    group_line_item: str | None
    input_tokens: int
    output_tokens: int
    num_model_requests: int
    cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class AggregationQuery:
    """Fully-validated aggregation request handed to the port (all values bound as params).

    ``unit`` is one of the whitelisted date_trunc units ``minute|hour|day``. ``models`` /
    ``api_key_ids`` are the tenant-AND-intersected filters (an empty tuple means no filter;
    the tenant_id AND makes any foreign value a non-leak). ``after_bucket`` is the keyset
    cursor (exclusive lower bound on the truncated bucket), None on the first page.
    """

    tenant_id: str
    unit: str
    start: datetime
    end: datetime
    models: tuple[str, ...]
    api_key_ids: tuple[str, ...]
    group_model: bool
    group_api_key_id: bool
    group_line_item: bool
    after_bucket: datetime | None
    limit: int


class UsageAggregationPort(Protocol):
    """Read-only, tenant-scoped bucketed aggregation over the usage_records ledger."""

    async def list_populated_buckets(self, query: AggregationQuery) -> list[datetime]:
        """Return up to ``limit + 1`` populated bucket starts (ASC) after the cursor.

        The extra probe row (``limit + 1``) lets the caller decide ``has_more`` without a
        second COUNT. Every query ANDs ``tenant_id = query.tenant_id``.
        """
        ...

    async def aggregate(
        self, query: AggregationQuery, first_bucket: datetime, last_bucket_end: datetime
    ) -> list[AggregatedBucketRow]:
        """Aggregate rows whose bucket falls in ``[first_bucket, last_bucket_end)``.

        GROUP BY the truncated bucket plus any requested group dimension. Every query ANDs
        ``tenant_id = query.tenant_id`` (the load-bearing isolation property).
        """
        ...
