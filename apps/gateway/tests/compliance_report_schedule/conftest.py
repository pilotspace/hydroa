"""Suite-local fixtures/helpers for compliance-report-center (TASK.md §3 — FROZEN @ v1).

Self-contained (this codebase's own convention — no cross-suite conftest import
precedent exists; every suite duplicates the small seed helpers it needs). Mirrors:
  - tests/compliance_bundle/conftest.py: signup_tenant/mint_role_token/seed_audit_event/
    seed_request_log/seed_usage_record/set_tenant_zdr/assert_problem/_naive (copied
    verbatim — same seed shapes since the generator reads the SAME 3 repositories).
  - tests/retention_zdr/test_retention_zdr.py: FakeObjectStore (copied verbatim).
  - tests/test_retention_sweep.py: _make_sweeper idiom (mirrored as _make_generator /
    _make_sweeper_with_store below).

Real Postgres (schema rebuilt per test via the root `app` fixture) + httpx.ASGITransport.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.compliance.application.report_schedule_generator import ReportScheduleGenerator
from gateway.objectstore.errors import ObjectNotFoundError, ObjectStoreUnavailableError
from gateway.tenants.domain.entities import Role
from gateway.usage.application.retention_sweep import RetentionSweeper

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
SCHEDULE = "/admin/compliance/report-schedule"
REPORTS = "/admin/compliance/reports"
PASSWORD = "correct horse battery compliance"

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


async def seed_audit_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_email: str | None = "actor@example.com",
    action: str = "api_key.create",
    created_at: datetime.datetime = BASE,
) -> str:
    row_id = str(uuid.uuid4())
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
            "auid": str(uuid.uuid4()),
            "aemail": actor_email,
            "action": action,
            "ttype": "api_key",
            "tid2": str(uuid.uuid4()),
            "result": "success",
            "metadata": "{}",
            "ts": _naive(created_at),
        },
    )
    await session.commit()
    return row_id


async def seed_request_log(
    session: AsyncSession,
    *,
    tenant_id: str,
    model_id: str = "openai/gpt-4o-mini",
    created_at: datetime.datetime = BASE,
) -> str:
    row_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO request_logs"
            " (id, tenant_id, key_id, team_id, model_id, status_code, stream, cached,"
            "  request_body, response_body, guardrail_verdict, scrub_status, truncated,"
            "  cost_usd, created_at, request_id, latency_ms, prompt_tokens,"
            "  completion_tokens, total_tokens)"
            " VALUES"
            " (:id, :tid, :kid, NULL, :model_id, 200, false, false,"
            "  NULL, NULL, NULL, 'scrubbed', false,"
            "  NULL, :ts, NULL, NULL, NULL, NULL, NULL)"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "kid": str(uuid.uuid4()),
            "model_id": model_id,
            "ts": _naive(created_at),
        },
    )
    await session.commit()
    return row_id


async def seed_usage_record(
    session: AsyncSession,
    *,
    tenant_id: str,
    model_id: str = "openai/gpt-4o-mini",
    cost_usd: str | Decimal = "0.001",
    created_at: datetime.datetime = BASE,
) -> str:
    row_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens, cost_usd,"
            "  status, raw, created_at, cost_basis, usage_source, tier_served)"
            " VALUES"
            " (:id, :tid, :kid, :model_id, 10, 20, :cost,"
            "  200, CAST('{}' AS JSONB), :ts, 'catalog', 'frame', 'standard')"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "kid": str(uuid.uuid4()),
            "model_id": model_id,
            "cost": str(cost_usd),
            "ts": _naive(created_at),
        },
    )
    await session.commit()
    return row_id


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


async def set_retention_window(session: AsyncSession, tenant_id: str, *, days: int | None) -> None:
    await session.execute(
        text("UPDATE tenants SET retention_window_days = :d WHERE id = :tid"),
        {"d": days, "tid": tenant_id},
    )
    await session.commit()


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


async def set_schedule_due(
    session: AsyncSession,
    tenant_id: str,
    *,
    day_of_month: int = 1,
    next_run_at: datetime.datetime | None = None,
    enabled: bool = True,
) -> None:
    """Raw-SQL upsert of a due (or not-yet-due) schedule row — bypasses the router so
    generator tests control next_run_at directly (mirrors test_retention_sweep.py's own
    raw-insert-then-sweep idiom)."""
    due_at = next_run_at if next_run_at is not None else BASE
    await session.execute(
        text(
            "INSERT INTO tenant_report_schedules"
            " (tenant_id, enabled, cadence, day_of_month, window_policy, delivery_target,"
            "  next_run_at)"
            " VALUES (:tid, :en, 'monthly', :dom, 'previous_calendar_month', 'in_app', :next)"
            " ON CONFLICT (tenant_id) DO UPDATE SET"
            "  enabled = EXCLUDED.enabled, day_of_month = EXCLUDED.day_of_month,"
            "  next_run_at = EXCLUDED.next_run_at"
        ),
        {"tid": tenant_id, "en": enabled, "dom": day_of_month, "next": _naive(due_at)},
    )
    await session.commit()


async def fetch_schedule_row(session: AsyncSession, tenant_id: str) -> Any:
    result = await session.execute(
        text(
            "SELECT enabled, last_run_at, last_run_status, next_run_at"
            " FROM tenant_report_schedules WHERE tenant_id = :tid"
        ),
        {"tid": tenant_id},
    )
    return result.fetchone()


async def count_report_runs(session: AsyncSession, tenant_id: str) -> int:
    result = await session.execute(
        text("SELECT COUNT(*) FROM compliance_report_runs WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    )
    return int(result.scalar() or 0)


class FakeObjectStore:
    """In-memory ObjectStore double (mirrors tests/retention_zdr/test_retention_zdr.py's
    own FakeObjectStore verbatim)."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_keys: set[str] = set()
        self.put_fail: bool = False

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if self.put_fail:
            raise ObjectStoreUnavailableError("simulated put outage")
        self.store[key] = data

    async def get(self, key: str) -> bytes:
        if key not in self.store:
            raise ObjectNotFoundError(key)
        return self.store[key]

    async def delete(self, key: str) -> None:
        if key in self.fail_keys:
            raise ObjectStoreUnavailableError("simulated delete outage")
        self.store.pop(key, None)
        self.deleted.append(key)

    async def health(self) -> bool:
        return True


def make_generator(
    app: Any, *, object_store: Any = None, settings: Any = None
) -> ReportScheduleGenerator:
    """Build a ReportScheduleGenerator using the app's sessionmaker (mirrors
    test_retention_sweep.py's own _make_sweeper idiom)."""
    return ReportScheduleGenerator(
        session_factory=app.state.sessionmaker,
        object_store=object_store,
        settings=settings if settings is not None else app.state.settings,
    )


def make_sweeper_with_store(
    app: Any, settings: Any, *, object_store: Any = None
) -> RetentionSweeper:
    return RetentionSweeper(
        session_factory=app.state.sessionmaker,
        settings=settings,
        object_store=object_store,
    )
