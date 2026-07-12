"""Domain entities for the billing/invoice bounded context.

Schema contract (invoice-generation TASK.md §3 — FROZEN @ v1). These are plain,
frozen dataclasses — the translation layer between InvoiceRepository (ORM rows)
and the api/router.py Pydantic response schemas, mirroring audit's own
AuditEvent split (domain dataclass -> repository -> API item).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Invoice:
    id: uuid.UUID
    tenant_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    status: str
    currency: str
    total_usd: Decimal
    raw_total_usd: Decimal
    tax_usd: Decimal
    issued_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    id: uuid.UUID
    invoice_id: uuid.UUID
    model_id: str
    team_id: uuid.UUID | None
    key_id: uuid.UUID
    tags: dict[str, str] = field(default_factory=dict)
    amount_usd: Decimal = Decimal("0")
    raw_amount_usd: Decimal = Decimal("0")
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_count: int = 0
    line_type: str = "usage"


@dataclass(frozen=True, slots=True)
class InvoiceCorrection:
    id: uuid.UUID
    invoice_id: uuid.UUID
    delta_usd: Decimal
    reason: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UsageEvidenceRow:
    usage_record_id: uuid.UUID
    created_at: datetime
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    request_id: str | None
