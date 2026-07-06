"""Suite-local fixtures for tenant-activity-tab (TASK.md §3 — FROZEN @ v1).

GET /admin/platform/tenants/{tenant_id}/audit is a superadmin-scoped, any-target-tenant audit
read — a SECOND door onto the same audit_events trail the self-service GET /admin/audit
(tests/audit_read) already exposes. Fixture/helper provenance (this repo's convention is
suite-local fixtures, not cross-suite imports — matched here, not by importing from sibling
test packages):
  - `platform_tenant_id`/`_issue_token`/`superadmin_token`/`superadmin_user_id` mirror
    tests/platform_tenant_directory/test_platform_tenant_directory.py and
    tests/admin_console_audit/test_admin_console_audit.py's own fixtures of the same name.
  - `seed_customer_tenant` mirrors those same suites' `_seed_customer_tenant` helper, trimmed to
    only name/kind — this task's route never reads any config/budget column.
  - `seed_audit_event`/`BASE`/`_naive` mirror tests/audit_read/conftest.py verbatim (the
    naive-TIMESTAMP `create_all` gotcha for audit_events.created_at — asyncpg rejects an aware
    datetime there).
  - `fetch_one_audit_row`/`count_audit_rows`/`drain_fire_and_forget` mirror
    tests/admin_console_audit/test_admin_console_audit.py's own helpers of the same name.

Real Postgres (localhost:5433/gateway_test) + Redis + the root `client`/`db_session`/`app`
fixtures (tests/conftest.py) — no mocking of session or repository, matching every sibling
platform_*_router.py suite this task's own §0 GROUND cites.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

PLATFORM_TENANTS = "/admin/platform/tenants"

# A fixed base UTC instant the tests seed audit rows around (offsets keep ordering deterministic
# — mirrors tests/audit_read/conftest.py's own BASE constant).
BASE = datetime.datetime(2026, 7, 6, 12, 0, 0, tzinfo=datetime.UTC)


def audit_path(tenant_id: object) -> str:
    """The new route's URL for a given tenant_id (accepts a str or uuid.UUID)."""
    return f"{PLATFORM_TENANTS}/{tenant_id}/audit"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def mins(n: int) -> datetime.datetime:
    return BASE + datetime.timedelta(minutes=n)


def _naive(dt: datetime.datetime) -> datetime.datetime:
    """asyncpg rejects an aware datetime into the create_all naive TIMESTAMP column (same
    gotcha as tests/audit_read/conftest.py and tests/alerts_events_viewer/conftest.py)."""
    return dt.astimezone(datetime.UTC).replace(tzinfo=None)


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant; seed one directly when the fast
    create_all test schema has not run the seed migration (mirrors platform_tenant_directory's
    and admin_console_audit's own fixture of the same name)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


def _issue_token(
    app: Any, *, role: Any, tenant_id: uuid.UUID, email: str, user_id: uuid.UUID | None = None
) -> str:
    """Mint a Bearer token directly via the live token service — no DB user row required
    (mirrors platform_tenant_directory's/admin_console_audit's own helper)."""
    token, _ = app.state.token_service.issue(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return str(token)


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


@pytest.fixture
def superadmin_user_id(app: Any, superadmin_token: str) -> uuid.UUID:
    """Decode the superadmin token's own user_id — the REAL actor the M6 audit row must
    attribute (actor_user_id) — never a placeholder (mirrors admin_console_audit's own
    fixture of the same name)."""
    identity = app.state.token_service.decode(superadmin_token)
    return identity.user_id  # type: ignore[no-any-return]


async def seed_customer_tenant(db_session: AsyncSession, *, name: str) -> uuid.UUID:
    """Insert a minimal kind='customer' tenant row — this task's route never reads any
    config/budget column, so only name/kind are needed (simpler than admin_console_audit's
    fuller `_seed_customer_tenant` variant)."""
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
        {"id": tid, "name": name},
    )
    await db_session.commit()
    return tid


async def seed_audit_event(
    session: AsyncSession,
    *,
    tenant_id: object,
    actor_user_id: str | None = None,
    actor_email: str | None = "actor@example.com",
    action: str = "api_key.create",
    target_type: str | None = "api_key",
    target_id: str | None = "seeded-target",
    result: str = "success",
    metadata: dict[str, object] | None = None,
    created_at: datetime.datetime = BASE,
) -> str:
    """Insert one audit_events row directly (adapted from tests/audit_read/conftest.py — same
    nullable-fields shape, same naive-datetime handling). Returns the row's id (str).

    NOTE (deliberate divergence from audit_read/conftest.py's own helper): target_id defaults to
    a fixed literal, NOT an auto-generated-when-None UUID — this task's own edge-case test
    (test_null_actor_and_target_fields_pass_through_gracefully) needs target_id=None to mean a
    genuinely NULL DB column, which an auto-generate-on-None sentinel would silently defeat.
    actor_user_id keeps the auto-generate-on-None behavior: AuditEvent.__post_init__ enforces
    that a tenant-scoped row MUST carry a real actor_user_id, so it can never be genuinely NULL
    here regardless (audit/domain/audit_event.py's own audit_missing_actor invariant)."""
    row_id = str(uuid.uuid4())
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
            "tid": str(tenant_id),
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


async def fetch_one_audit_row(session: AsyncSession, *, action: str) -> Row[Any] | None:
    """Return the single most-recent audit_events row for `action`, or None (mirrors
    tests/admin_console_audit/test_admin_console_audit.py's own helper of the same name)."""
    result = await session.execute(
        text(
            "SELECT tenant_id, actor_user_id, actor_email, action, target_type, "
            "target_id, result, metadata FROM audit_events "
            "WHERE action = :action ORDER BY created_at DESC LIMIT 1"
        ),
        {"action": action},
    )
    return result.fetchone()


async def count_audit_rows(session: AsyncSession, *, action: str) -> int:
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE action = :action"),
        {"action": action},
    )
    return int(result.scalar() or 0)


async def drain_fire_and_forget() -> None:
    """Let a fire-and-forget asyncio.ensure_future(record_audit(...)) task complete before
    querying audit_events (mirrors tests/admin_console_audit's own `asyncio.sleep(0.05)` idiom,
    itself mirroring tests/superadmin_audit_foundation/conftest.py and tests/test_users_role.py)."""
    await asyncio.sleep(0.05)
