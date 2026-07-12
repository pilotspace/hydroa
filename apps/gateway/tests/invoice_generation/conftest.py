"""Suite-local fixtures for invoice-generation (TASK.md §3 — FROZEN @ v1).

Real Postgres (GATEWAY_TEST_DATABASE_URL) + the root `client`/`db_session`/`app`
fixtures. Mirrors tests/logs_explorer_api/conftest.py + tests/cost_attribution_tags's
own `insert_direct_usage_row` idiom (a tenant+owner via the canonical signup flow,
`usage_records` rows seeded DIRECTLY via `text()`, role tokens minted via
`app.state.token_service.issue(...)`).
"""

from __future__ import annotations

import datetime
import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
LIST_INVOICES = "/admin/invoices"
PASSWORD = "correct horse battery staple"

# A fixed base UTC instant the tests seed around.
BASE = datetime.datetime(2026, 7, 1, 0, 0, 0, tzinfo=datetime.UTC)

# The July 2026 calendar-month period used throughout M1-M4/M10/M12/M13/M14 tests.
JULY_START = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
AUGUST_START = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)


def detail_url(invoice_id: str) -> str:
    return f"/admin/invoices/{invoice_id}"


def evidence_url(invoice_id: str, line_id: str) -> str:
    return f"/admin/invoices/{invoice_id}/lines/{line_id}/evidence"


def export_url(invoice_id: str, fmt: str | None) -> str:
    base = f"/admin/invoices/{invoice_id}/export"
    return f"{base}?format={fmt}" if fmt is not None else base


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def _naive(dt: datetime.datetime) -> datetime.datetime:
    """asyncpg rejects an aware datetime into the create_all naive TIMESTAMP column."""
    return dt.astimezone(datetime.UTC).replace(tzinfo=None)


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Sign up a tenant+owner; return (owner_jwt, tenant_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def mint_role_token(app: Any, *, tenant_id: str, role: Role, email: str) -> str:
    """Mint a JWT for `role` in the given tenant (no DB user row needed — the invoices
    endpoints only read usage_records/invoices, never join to users)."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token)


async def seed_usage_record(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str | None = None,
    team_id: str | None = None,
    model_id: str = "openai/gpt-4o-mini",
    cost_usd: str | Decimal = "1.00",
    tags: dict[str, str] | None = None,
    status: int = 200,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    created_at: datetime.datetime = BASE,
    request_id: str | None = None,
) -> uuid.UUID:
    """Insert one usage_records row directly (bypassing the proxy pipeline) — the
    legitimate black-box arrange step for invoice-generation's pure-aggregation read
    path (mirrors tests/cost_attribution_tags's own insert_direct_usage_row)."""
    row_id = uuid.uuid4()
    raw: dict[str, Any] = {}
    if request_id is not None:
        raw["request_id"] = request_id
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, team_id, model_id, status, cost_usd, raw, tags,"
            "  prompt_tokens, completion_tokens, created_at)"
            " VALUES"
            " (:id, :tenant_id, :key_id, :team_id, :model_id, :status, :cost_usd, :raw, :tags,"
            "  :prompt_tokens, :completion_tokens, :created_at)"
        ),
        {
            "id": row_id,
            "tenant_id": str(tenant_id),
            "key_id": str(key_id) if key_id is not None else str(uuid.uuid4()),
            "team_id": str(team_id) if team_id is not None else None,
            "model_id": model_id,
            "status": status,
            "cost_usd": str(cost_usd),
            "raw": json.dumps(raw),
            "tags": json.dumps(tags or {}),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "created_at": _naive(created_at),
        },
    )
    await session.commit()
    return row_id


def make_generator(app: Any) -> Any:
    """Build an InvoiceGenerator wired to the test app's sessionmaker."""
    from gateway.billing.application.invoice_generator import InvoiceGenerator

    return InvoiceGenerator(session_factory=app.state.sessionmaker)
