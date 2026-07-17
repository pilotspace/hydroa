"""Pydantic request schemas for the self-serve checkout API (TASK.md §3).

`idempotency_key` is OPTIONAL at the schema level so a MISSING key is a checkout-specific
400 `idempotency_key_required` (raised in the route), not a generic 422 field error. Any
extra body field (e.g. an attacker-supplied `tenant_id`) is ignored — tenant scope is ALWAYS
derived from the JWT Identity (M1), never a body field.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class PlanUpgradeRequest(BaseModel):
    target_plan_id: uuid.UUID
    idempotency_key: str | None = None


class CreditTopupRequest(BaseModel):
    amount_usd: str
    idempotency_key: str | None = None
    note: str | None = None


class ConfirmRequest(BaseModel):
    """Empty confirm body (POST .../confirm)."""
