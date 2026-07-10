"""Suite-local fixtures for the logs-explorer-api tests (TASK.md §3 — FROZEN @ v1).

Real Postgres (localhost:5433/gateway_test_lexa) + Redis + the root `client`/`db_session`/
`app` fixtures. Mirrors tests/audit_export/conftest.py's own pattern (a tenant+owner via the
canonical signup flow, `request_logs` rows seeded DIRECTLY via `text()`, role tokens minted
via `app.state.token_service.issue(...)`).

NOTE: request_logs.created_at is TIMESTAMPTZ in production (Alembic) but the test schema is
bootstrapped via Base.metadata.create_all, where `Mapped[datetime]` maps to a NAIVE TIMESTAMP
column — asyncpg rejects an aware datetime there, so every seed strips tz to naive UTC (same
gotcha as tests/audit_export/conftest.py's own `_naive` helper).
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
LIST_LOGS = "/admin/logs"
PASSWORD = "correct horse battery"

# A fixed base UTC instant the tests seed around (offsets keep ordering deterministic).
BASE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)


def detail_url(log_id: str) -> str:
    return f"/admin/logs/{log_id}"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
    """Mint a JWT for `role` in the given tenant (no DB user row needed — the logs endpoints
    only read request_logs, never join to users; mirrors tests/audit_export's own
    role-200/403 fixtures)."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token)


async def seed_request_log(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str | None = None,
    team_id: str | None = None,
    model_id: str = "openai/gpt-4o-mini",
    status_code: int = 200,
    stream: bool = False,
    cached: bool = False,
    request_body: dict[str, Any] | None = None,
    response_body: dict[str, Any] | None = None,
    guardrail_verdict: dict[str, Any] | None = None,
    scrub_status: str = "scrubbed",
    truncated: bool = False,
    cost_usd: str | Decimal | None = None,
    created_at: datetime.datetime = BASE,
    request_id: str | None = None,
    latency_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> str:
    """Insert one request_logs row (controlled fields). Returns id."""
    row_id = str(uuid.uuid4())
    if key_id is None:
        key_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO request_logs"
            " (id, tenant_id, key_id, team_id, model_id, status_code, stream, cached,"
            "  request_body, response_body, guardrail_verdict, scrub_status, truncated,"
            "  cost_usd, created_at, request_id, latency_ms, prompt_tokens,"
            "  completion_tokens, total_tokens)"
            " VALUES"
            " (:id, :tid, :kid, :teamid, :model_id, :status_code, :stream, :cached,"
            "  CAST(:request_body AS JSONB), CAST(:response_body AS JSONB),"
            "  CAST(:guardrail_verdict AS JSONB), :scrub_status, :truncated,"
            "  :cost_usd, :ts, :request_id, :latency_ms, :prompt_tokens,"
            "  :completion_tokens, :total_tokens)"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "kid": key_id,
            "teamid": team_id,
            "model_id": model_id,
            "status_code": status_code,
            "stream": stream,
            "cached": cached,
            "request_body": json.dumps(request_body) if request_body is not None else None,
            "response_body": json.dumps(response_body) if response_body is not None else None,
            "guardrail_verdict": (
                json.dumps(guardrail_verdict) if guardrail_verdict is not None else None
            ),
            "scrub_status": scrub_status,
            "truncated": truncated,
            "cost_usd": str(cost_usd) if cost_usd is not None else None,
            "ts": _naive(created_at),
            "request_id": request_id,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    )
    await session.commit()
    return row_id
