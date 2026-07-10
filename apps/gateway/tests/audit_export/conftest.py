"""Suite-local fixtures for the compliance-export tests (TASK.md §3 — FROZEN @ v1).

Real Postgres (localhost:5433/gateway_test_<suffix>) + Redis + the root `client`/
`db_session`/`app` fixtures. Mirrors tests/audit_read/conftest.py's own pattern (a tenant+
owner via the canonical signup flow, `audit_events` rows seeded DIRECTLY via `text()`, role
tokens minted via `app.state.token_service.issue(...)`), plus a bulk-seed helper for the
1000+/1200-row page-size scenarios where a one-INSERT-per-row loop would be needlessly slow.

NOTE: audit_events.created_at is TIMESTAMPTZ in production (Alembic) but the test schema is
bootstrapped via Base.metadata.create_all, where `Mapped[datetime]` maps to a NAIVE TIMESTAMP
column — asyncpg rejects an aware datetime there, so every seed strips tz to naive UTC (same
gotcha as tests/audit_read/conftest.py and tests/alerts_events_viewer/conftest.py).
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
EXPORT = "/admin/audit/export"
PASSWORD = "correct horse battery"

# A fixed base UTC instant the tests seed around (offsets keep ordering deterministic).
BASE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)


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
    """Mint a JWT for `role` in the given tenant (no DB user row needed — the export
    endpoint only reads audit_events, never joins to users; mirrors tests/audit_read's own
    role-200/403 fixtures, which mint directly via token_service for the same reason)."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token)


async def seed_audit_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: str | None = None,
    actor_email: str | None = "actor@example.com",
    action: str = "api_key.create",
    target_type: str | None = "api_key",
    target_id: str | None = None,
    result: str = "success",
    metadata: dict[str, object] | None = None,
    created_at: datetime.datetime = BASE,
) -> str:
    """Insert one audit_events row (controlled fields). Returns id."""
    row_id = str(uuid.uuid4())
    if target_id is None:
        target_id = str(uuid.uuid4())
    if actor_user_id is None:
        actor_user_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO audit_events"
            " (id, tenant_id, actor_user_id, actor_email, action, target_type,"
            "  target_id, result, metadata, created_at)"
            " VALUES (:id, :tid, :auid, :aemail, :action, :ttype,"
            "         :tid2, :result, CAST(:metadata AS JSONB), :ts)"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "auid": actor_user_id,
            "aemail": actor_email,
            "action": action,
            "ttype": target_type,
            "tid2": target_id,
            "result": result,
            "metadata": json.dumps(metadata if metadata is not None else {}),
            "ts": _naive(created_at),
        },
    )
    await session.commit()
    return row_id


async def seed_many_audit_events(
    session: AsyncSession,
    *,
    tenant_id: str,
    count: int,
    start: datetime.datetime = BASE,
    step: datetime.timedelta = datetime.timedelta(seconds=1),
    action: str = "bulk.seed",
) -> list[str]:
    """Bulk-insert `count` audit_events rows via one executemany-style call — a one-row-at-
    a-time loop is needlessly slow for the 1000+/1200-row page-size scenarios (M5)."""
    rows: list[dict[str, object]] = []
    ids: list[str] = []
    for i in range(count):
        row_id = str(uuid.uuid4())
        ids.append(row_id)
        rows.append(
            {
                "id": row_id,
                "tid": tenant_id,
                "auid": str(uuid.uuid4()),
                "aemail": "bulk@example.com",
                "action": action,
                "ttype": "api_key",
                "tid2": str(uuid.uuid4()),
                "result": "success",
                "metadata": "{}",
                "ts": _naive(start + i * step),
            }
        )
    await session.execute(
        text(
            "INSERT INTO audit_events"
            " (id, tenant_id, actor_user_id, actor_email, action, target_type,"
            "  target_id, result, metadata, created_at)"
            " VALUES (:id, :tid, :auid, :aemail, :action, :ttype,"
            "         :tid2, :result, CAST(:metadata AS JSONB), :ts)"
        ),
        rows,
    )
    await session.commit()
    return ids


async def fetch_one_audit_row(
    session: AsyncSession, *, action: str, tenant_id: str | None = None
) -> Row[Any] | None:
    """Return the single most-recent audit_events row for `action`, optionally tenant-scoped
    (mirrors tests/admin_console_audit/test_admin_console_audit.py's own helper)."""
    # `where` is built only from two fixed literal clauses (never request/user input) and
    # every value is bound via `params` — not a real SQL-injection vector.
    where = "WHERE action = :action" + (" AND tenant_id = :tid" if tenant_id else "")
    params: dict[str, object] = {"action": action}
    if tenant_id:
        params["tid"] = tenant_id
    query = (
        "SELECT tenant_id, actor_user_id, actor_email, action, target_type, "  # noqa: S608
        "target_id, result, metadata FROM audit_events "
        f"{where} ORDER BY created_at DESC LIMIT 1"
    )
    result = await session.execute(text(query), params)
    return result.fetchone()


async def count_audit_rows(
    session: AsyncSession, *, action: str, tenant_id: str | None = None
) -> int:
    where = "WHERE action = :action" + (" AND tenant_id = :tid" if tenant_id else "")
    params: dict[str, object] = {"action": action}
    if tenant_id:
        params["tid"] = tenant_id
    result = await session.execute(
        text(f"SELECT COUNT(*) FROM audit_events {where}"),  # noqa: S608 — no user input, see above
        params,
    )
    return int(result.scalar() or 0)
