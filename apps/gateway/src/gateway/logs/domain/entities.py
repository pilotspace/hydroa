"""Domain entities for the logs (payload-capture-store) module — zero framework imports.

Contract (payload-capture-store TASK.md §3, FROZEN @ v1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestLog:
    """A single captured, PII-scrubbed request/response row (read-side projection).

    Mirrors the AuthzResult-style frozen-dataclass shape. Not written directly by the
    capture write path (which uses a raw INSERT, mirroring alert_writer.py) — this
    entity exists for future read-side consumers (the sibling logs-explorer-api task).
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    key_id: uuid.UUID
    team_id: uuid.UUID | None
    model_id: str
    status_code: int
    stream: bool
    cached: bool
    request_body: dict[str, Any] | None
    response_body: dict[str, Any] | None
    guardrail_verdict: dict[str, Any] | None
    scrub_status: str
    truncated: bool
    cost_usd: str | None
    created_at: datetime
    # request-log-metering-fields TASK.md §3 (FROZEN @ v1) — 5 additive fields, DISPLAY-ONLY
    # metadata (never billing truth; usage_records remains sole source of truth).
    request_id: uuid.UUID | None
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
