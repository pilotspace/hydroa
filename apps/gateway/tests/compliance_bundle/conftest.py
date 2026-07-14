"""Suite-local fixtures for the Art. 12 record-keeping bundle tests
(art12-record-keeping-preset TASK.md §3 — FROZEN @ v1).

Real Postgres (localhost:5433/gateway_test_<suffix>) + Redis + the root `client`/
`db_session`/`app` fixtures. Mirrors tests/audit_export/conftest.py (audit_events seeding),
tests/logs_explorer_api/conftest.py (request_logs seeding), and tests/plan_enforcement/
conftest.py (plans/feature_flags seeding) — this suite composes over all 3 stores.

NOTE: every seeded table's `created_at` is TIMESTAMPTZ in production (Alembic) but the test
schema is bootstrapped via Base.metadata.create_all, where `Mapped[datetime]` maps to a NAIVE
TIMESTAMP column — asyncpg rejects an aware datetime there, so every seed strips tz to naive
UTC (same gotcha as tests/audit_export/conftest.py's own `_naive` helper).
"""

from __future__ import annotations

import datetime
import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
BUNDLE = "/admin/compliance/art12-bundle"
PASSWORD = "correct horse battery art12"

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
    """Mint a JWT for `role` in the given tenant (no DB user row needed)."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token)


# ---------------------------------------------------------------------------
# audit_events seeding (mirrors tests/audit_export/conftest.py)
# ---------------------------------------------------------------------------


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


async def count_audit_rows(
    session: AsyncSession, *, action: str, tenant_id: str | None = None
) -> int:
    where = "WHERE action = :action" + (" AND tenant_id = :tid" if tenant_id else "")
    params: dict[str, object] = {"action": action}
    if tenant_id:
        params["tid"] = tenant_id
    result = await session.execute(
        text(f"SELECT COUNT(*) FROM audit_events {where}"),  # noqa: S608 — no user input
        params,
    )
    return int(result.scalar() or 0)


async def fetch_audit_rows(
    session: AsyncSession, *, action: str, tenant_id: str | None = None
) -> list[Row[Any]]:
    where = "WHERE action = :action" + (" AND tenant_id = :tid" if tenant_id else "")
    params: dict[str, object] = {"action": action}
    if tenant_id:
        params["tid"] = tenant_id
    result = await session.execute(
        text(
            "SELECT tenant_id, actor_user_id, action, result, metadata, created_at "  # noqa: S608
            f"FROM audit_events {where} ORDER BY created_at ASC"
        ),
        params,
    )
    return list(result.fetchall())


# ---------------------------------------------------------------------------
# request_logs seeding (mirrors tests/logs_explorer_api/conftest.py)
# ---------------------------------------------------------------------------


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


async def seed_many_request_logs(
    session: AsyncSession,
    *,
    tenant_id: str,
    count: int,
    start: datetime.datetime = BASE,
    step: datetime.timedelta = datetime.timedelta(seconds=1),
) -> list[str]:
    rows: list[dict[str, object]] = []
    ids: list[str] = []
    for i in range(count):
        row_id = str(uuid.uuid4())
        ids.append(row_id)
        rows.append(
            {
                "id": row_id,
                "tid": tenant_id,
                "kid": str(uuid.uuid4()),
                "teamid": None,
                "model_id": "openai/gpt-4o-mini",
                "status_code": 200,
                "stream": False,
                "cached": False,
                "request_body": None,
                "response_body": None,
                "guardrail_verdict": None,
                "scrub_status": "scrubbed",
                "truncated": False,
                "cost_usd": None,
                "ts": _naive(start + i * step),
                "request_id": None,
                "latency_ms": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }
        )
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
        rows,
    )
    await session.commit()
    return ids


# ---------------------------------------------------------------------------
# usage_records seeding (NEW — no existing suite seeds this table for keyset paging)
# ---------------------------------------------------------------------------


async def seed_usage_record(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str | None = None,
    model_id: str = "openai/gpt-4o-mini",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    cost_usd: str | Decimal = "0.001",
    status: int = 200,
    cost_basis: str = "catalog",
    usage_source: str = "frame",
    tier_served: str = "standard",
    created_at: datetime.datetime = BASE,
) -> str:
    row_id = str(uuid.uuid4())
    if key_id is None:
        key_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens, cost_usd,"
            "  status, raw, created_at, cost_basis, usage_source, tier_served)"
            " VALUES"
            " (:id, :tid, :kid, :model_id, :pt, :ct, :cost,"
            "  :status, CAST(:raw AS JSONB), :ts, :cost_basis, :usage_source, :tier_served)"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "kid": key_id,
            "model_id": model_id,
            "pt": prompt_tokens,
            "ct": completion_tokens,
            "cost": str(cost_usd),
            "status": status,
            "raw": "{}",
            "ts": _naive(created_at),
            "cost_basis": cost_basis,
            "usage_source": usage_source,
            "tier_served": tier_served,
        },
    )
    await session.commit()
    return row_id


async def seed_many_usage_records(
    session: AsyncSession,
    *,
    tenant_id: str,
    count: int,
    start: datetime.datetime = BASE,
    step: datetime.timedelta = datetime.timedelta(seconds=1),
) -> list[str]:
    rows: list[dict[str, object]] = []
    ids: list[str] = []
    for i in range(count):
        row_id = str(uuid.uuid4())
        ids.append(row_id)
        rows.append(
            {
                "id": row_id,
                "tid": tenant_id,
                "kid": str(uuid.uuid4()),
                "model_id": "openai/gpt-4o-mini",
                "pt": 10,
                "ct": 20,
                "cost": "0.001",
                "status": 200,
                "raw": "{}",
                "ts": _naive(start + i * step),
                "cost_basis": "catalog",
                "usage_source": "frame",
                "tier_served": "standard",
            }
        )
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens, cost_usd,"
            "  status, raw, created_at, cost_basis, usage_source, tier_served)"
            " VALUES"
            " (:id, :tid, :kid, :model_id, :pt, :ct, :cost,"
            "  :status, CAST(:raw AS JSONB), :ts, :cost_basis, :usage_source, :tier_served)"
        ),
        rows,
    )
    await session.commit()
    return ids


# ---------------------------------------------------------------------------
# Tenant state helpers (zdr / residency / guardrail_configs / plan feature)
# ---------------------------------------------------------------------------


async def set_tenant_zdr(
    session: AsyncSession,
    tenant_id: str,
    *,
    enabled: bool,
    enabled_at: datetime.datetime | None = BASE,
) -> None:
    await session.execute(
        text("UPDATE tenants SET zdr_enabled = :en, zdr_enabled_at = :at WHERE id = :tid"),
        {
            "en": enabled,
            "at": _naive(enabled_at) if enabled_at is not None else None,
            "tid": tenant_id,
        },
    )
    await session.commit()


async def seed_plan(
    session: AsyncSession,
    *,
    name: str,
    feature_flags: list[str] | None = None,
) -> str:
    """Insert a `plans` row directly via ORM (create_all doesn't replay the migration's own
    seed INSERT — mirrors tests/plan_enforcement/conftest.py's own `seed_plan` verbatim)."""
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name=name,
        display_name=name.title(),
        seat_cap=None,
        budget_usd_monthly_default=None,
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=None,
        feature_flags=feature_flags if feature_flags is not None else [],
    )
    session.add(row)
    await session.commit()
    return str(row.id)


async def assign_plan(session: AsyncSession, *, tenant_id: str, plan_id: str) -> None:
    await session.execute(
        text("UPDATE tenants SET plan_id = :pid WHERE id = :tid"),
        {"pid": plan_id, "tid": tenant_id},
    )
    await session.commit()


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"
