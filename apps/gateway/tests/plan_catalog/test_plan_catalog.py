"""RED suite for plan-catalog (TASK.md §3 CONTRACT FROZEN @ v1).

Covers the plan/tier catalog + superadmin cross-tenant assignment surface:
  - NEW `plans` reference table, seeded via migration with exactly 3 rows (M1)
  - Additive `TenantRow.plan_id`/`seat_cap` columns, universally NULL until a superadmin
    explicitly acts (M2) — no auto-assignment at signup
  - The platform tenant can never hold a plan (M3/R4/R8, app-level AND DB-level)
  - GET /admin/platform/plans                      (M4, catalog list)
  - GET /admin/platform/tenants/{tenant_id}/plan     (M5, view)
  - PUT /admin/platform/tenants/{tenant_id}/plan     (M6-M9, assign/change/unassign)

RED before BUILD: `platform_plans_router.py` does not exist and is not registered in
main.py yet (this task's own Scope registers it there directly — unlike
cross-tenant-config-budget, which deferred that registration to the orchestrator — so,
mirroring platform_tenant_directory's own RED phase, every HTTP-level test below fails
with a bare 404 (route not found) rather than an ImportError). The two migration-scenario
tests (M1, M2-pre-existing) fail because the `plans` table/columns do not exist yet against
the real Alembic chain. The R8 DB-CHECK test fails because `PlanRow`/the FK/CHECK
constraints do not exist in the ORM yet.

Superadmin/owner identities are minted directly via app.state.token_service.issue(...) — no
DB user row needed (mirrors tests/cross_tenant_config_budget and
tests/platform_tenant_directory). Target tenants and plans are seeded directly via ORM/SQL —
no signup flow needed for tenants/plans this task's SUPERADMIN caller does not itself create.

DO NOT change these tests to make them pass — that is the Build phase's job.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Reused real-Alembic scratch-DB fixtures (mirrors tests/platform_tenant_seed's own
# precedent exactly): Base.metadata.create_all() never replays a migration's own DML
# seed INSERT, so "the migration seeds 3 rows" / "a pre-existing tenant survives the
# migration untouched" can only be proven by really running `alembic upgrade`.
from tests.migrations.conftest import (  # noqa: F401 — migration_db is a transitive fixture dep
    MIGRATION_DATABASE_URL,
    MIGRATION_DSN,
    clean_migration_db,
    migration_db,
)

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _plans_url() -> str:
    return "/admin/platform/plans"


def _tenant_plan_url(tenant_id: uuid.UUID) -> str:
    return f"/admin/platform/tenants/{tenant_id}/plan"


# ---------------------------------------------------------------------------
# Alembic scratch-DB helper (mirrors tests/platform_tenant_seed/
# test_platform_tenant_seed.py's own _alembic_config() verbatim)
# ---------------------------------------------------------------------------


def _alembic_config() -> object:  # returns alembic.config.Config at runtime
    """Build an Alembic Config pointed at the dedicated migration test DB."""
    from alembic.config import Config  # noqa: PLC0415 — intentional late import
    from tests.migrations.conftest import ALEMBIC_INI  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


# ---------------------------------------------------------------------------
# Fixtures + helpers (app/client/db_session/settings from the root conftest.py —
# this task's own Scope registers platform_plans_router directly in main.py, so no
# directory-local `app` fixture override is needed, mirroring
# tests/platform_tenant_directory's own precedent)
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant; seed one directly when the
    fast create_all test schema has not run the seed migration (mirrors
    tests/cross_tenant_config_budget/test_cross_tenant_config_budget.py's fixture of the
    same name)."""
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


async def _get_tenant(db_session: AsyncSession, tenant_id: uuid.UUID) -> Any:
    """Fetch a TenantRow via the SAME repository function the router under test uses."""
    from gateway.tenants.infrastructure.repository import get_tenant_by_id

    return await get_tenant_by_id(db_session, tenant_id)


async def _seed_customer_tenant(
    db_session: AsyncSession,
    *,
    name: str,
    plan_id: uuid.UUID | None = None,
    seat_cap: int | None = None,
) -> uuid.UUID:
    """Insert a kind='customer' tenant row, optionally pre-assigned a plan/seat_cap."""
    tid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name, kind, plan_id, seat_cap) "
            "VALUES (:id, :name, 'customer', :plan_id, :seat_cap)"
        ),
        {"id": tid, "name": name, "plan_id": plan_id, "seat_cap": seat_cap},
    )
    await db_session.commit()
    return tid


def _issue_token(app: Any, *, role: Any, tenant_id: uuid.UUID, email: str) -> str:
    """Mint a Bearer token directly via the live token service — no DB user row required."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


@pytest.fixture
async def seeded_plans(db_session: AsyncSession) -> dict[str, uuid.UUID]:
    """Insert the 3 named plan tiers directly via ORM — create_all() does not replay the
    migration's own seed INSERT. Values mirror the migration's own seed data exactly
    (TASK.md §3 Schema) so scenario text like "the 'starter' plan has seat_cap=3" holds
    for these HTTP-level tests too (the migration's OWN seed values are independently
    verified by test_migration_seeds_exactly_3_named_plan_tiers below via real Alembic)."""
    from gateway.tenants.infrastructure.orm import PlanRow

    rows = {
        "starter": PlanRow(
            id=uuid.uuid4(),
            name="starter",
            display_name="Starter",
            seat_cap=3,
            budget_usd_monthly_default=Decimal("50.00"),
            rpm_limit_default=60,
            tpm_limit_default=40000,
        ),
        "team": PlanRow(
            id=uuid.uuid4(),
            name="team",
            display_name="Team",
            seat_cap=None,
            budget_usd_monthly_default=Decimal("500.00"),
            rpm_limit_default=600,
            tpm_limit_default=400000,
        ),
        "enterprise": PlanRow(
            id=uuid.uuid4(),
            name="enterprise",
            display_name="Enterprise",
            seat_cap=None,
            budget_usd_monthly_default=None,
            rpm_limit_default=None,
            tpm_limit_default=None,
        ),
    }
    for row in rows.values():
        db_session.add(row)
    await db_session.commit()
    return {name: row.id for name, row in rows.items()}


def _plan_json(
    plan_id: uuid.UUID,
    *,
    name: str,
    display_name: str,
    seat_cap: int | None,
    budget: str | None,
    rpm: int | None,
    tpm: int | None,
) -> dict[str, Any]:
    return {
        "id": str(plan_id),
        "name": name,
        "display_name": display_name,
        "seat_cap": seat_cap,
        "budget_usd_monthly_default": budget,
        "rpm_limit_default": rpm,
        "tpm_limit_default": tpm,
    }


async def _audit_count(
    db_session: AsyncSession, *, action: str, target_tenant_id: uuid.UUID | None
) -> int:
    """Let the fire-and-forget audit write complete (admin-console-audit's own
    asyncio.sleep(0.05) drain convention), then count matching audit_events rows."""
    await asyncio.sleep(0.05)
    if target_tenant_id is None:
        result = await db_session.execute(
            text("SELECT COUNT(*) FROM audit_events WHERE tenant_id IS NULL AND action = :action"),
            {"action": action},
        )
    else:
        result = await db_session.execute(
            text("SELECT COUNT(*) FROM audit_events WHERE tenant_id = :tid AND action = :action"),
            {"tid": target_tenant_id, "action": action},
        )
    return result.scalar() or 0


async def _audit_metadata(
    db_session: AsyncSession, *, action: str, target_tenant_id: uuid.UUID
) -> dict[str, Any]:
    """Fetch the audit_events row for this action+tenant — asserts EXACTLY one row was
    written (§1 After M6: "exactly one ... audit row"), attributing the REAL calling
    superadmin as actor (§1 After M6: "attributing the REAL calling superadmin as actor"
    — every superadmin_token in this suite is minted with this same fixed email)."""
    await asyncio.sleep(0.05)
    rows = (
        await db_session.execute(
            text(
                "SELECT actor_email, metadata FROM audit_events"
                " WHERE tenant_id = :tid AND action = :action"
            ),
            {"tid": target_tenant_id, "action": action},
        )
    ).fetchall()
    assert len(rows) == 1, (
        f"expected exactly 1 audit_events row for action={action!r} "
        f"tenant={target_tenant_id}, found {len(rows)}"
    )
    actor_email, metadata = rows[0]
    assert actor_email == "root@platform.internal", (
        f"expected the real superadmin as actor, got {actor_email!r}"
    )
    return dict(metadata)


# ---------------------------------------------------------------------------
# M1 — seed migration creates the tier catalog (real Alembic upgrade)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_migration_seeds_exactly_3_named_plan_tiers() -> None:
    """M1: migration seeds exactly 3 rows (starter/team/enterprise), each carrying its
    own independently-nullable ceilings + a non-null display_name."""
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        rows = await conn.fetch(
            "SELECT name, display_name, seat_cap, budget_usd_monthly_default,"
            " rpm_limit_default, tpm_limit_default FROM plans ORDER BY name"
        )
    finally:
        await conn.close()

    assert len(rows) == 3, f"expected exactly 3 plans, found {len(rows)}"
    assert {r["name"] for r in rows} == {"starter", "team", "enterprise"}
    assert all(r["display_name"] for r in rows), "every plan needs a non-null display_name"

    by_name = {r["name"]: r for r in rows}

    starter = by_name["starter"]
    assert starter["seat_cap"] == 3
    assert starter["budget_usd_monthly_default"] == Decimal("50.00")
    assert starter["rpm_limit_default"] == 60
    assert starter["tpm_limit_default"] == 40000

    team = by_name["team"]
    assert team["seat_cap"] is None
    assert team["budget_usd_monthly_default"] == Decimal("500.00")
    assert team["rpm_limit_default"] == 600
    assert team["tpm_limit_default"] == 400000

    enterprise = by_name["enterprise"]
    assert enterprise["seat_cap"] is None
    assert enterprise["budget_usd_monthly_default"] is None
    assert enterprise["rpm_limit_default"] is None
    assert enterprise["tpm_limit_default"] is None


# ---------------------------------------------------------------------------
# M2 — unplanned is the universal starting state
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_preexisting_tenant_unaffected_by_migration() -> None:
    """M2: a tenant row created BEFORE this migration keeps plan_id=NULL, seat_cap=NULL
    after the migration runs — mirrors platform_tenant_seed's own
    test_existing_rows_backfill_to_customer_kind precedent exactly."""
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "1193bc6178f3")  # prior head, before this task's migration

    pre_id = uuid.uuid4()
    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'PreCo')", pre_id)
    finally:
        await conn.close()

    command.upgrade(cfg, "head")

    conn = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        row = await conn.fetchrow(
            "SELECT plan_id, seat_cap, name FROM tenants WHERE id = $1", pre_id
        )
    finally:
        await conn.close()

    assert row is not None, "pre-existing tenant row vanished across the migration"
    assert row["name"] == "PreCo"
    assert row["plan_id"] is None
    assert row["seat_cap"] is None


async def test_new_signup_tenant_starts_unplanned_no_auto_assignment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2: a brand-new signup's tenant starts with plan_id=NULL, seat_cap=NULL — no plan
    is auto-assigned. Byte-identical to a pre-existing, never-assigned tenant."""
    resp = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "FreshCo",
            "email": "owner@freshco.io",
            "password": "correct horse battery",
        },
    )
    assert resp.status_code == 201, resp.text
    tenant_id = resp.json()["tenant_id"]

    row = await _get_tenant(db_session, uuid.UUID(tenant_id))
    assert row is not None
    assert row.plan_id is None
    assert row.seat_cap is None


# ---------------------------------------------------------------------------
# M3 / R4 / R8 — platform tenant permanently exempt
# ---------------------------------------------------------------------------


async def test_assigning_plan_to_platform_tenant_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    platform_tenant_id: uuid.UUID,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    """M3, R4: PUT targeting the platform tenant's own tenant_id is rejected 403, no write,
    no audit fired."""
    resp = await client.put(
        _tenant_plan_url(platform_tenant_id),
        json={"plan_id": str(seeded_plans["starter"])},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_PLAN_TENANT_INELIGIBLE"

    row = await _get_tenant(db_session, platform_tenant_id)
    assert row is not None
    assert row.plan_id is None

    count = await _audit_count(
        db_session, action="platform.plan.assign", target_tenant_id=platform_tenant_id
    )
    assert count == 0


async def test_db_rejects_direct_plan_id_update_on_platform_tenant(
    db_session: AsyncSession,
    platform_tenant_id: uuid.UUID,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    """R8: a direct SQL UPDATE bypassing application code is rejected by the
    ck_tenants_platform_no_plan CHECK constraint itself."""
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            text("UPDATE tenants SET plan_id = :pid WHERE id = :tid"),
            {"pid": seeded_plans["starter"], "tid": platform_tenant_id},
        )
        await db_session.commit()
    assert getattr(exc_info.value.orig, "sqlstate", None) == "23514"

    await db_session.rollback()
    row = await _get_tenant(db_session, platform_tenant_id)
    assert row is not None
    assert row.plan_id is None


# ---------------------------------------------------------------------------
# M4 — catalog list
# ---------------------------------------------------------------------------


async def test_superadmin_lists_full_plan_catalog(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    resp = await client.get(_plans_url(), headers={"Authorization": f"Bearer {superadmin_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["plans"]) == 3
    by_name = {p["name"]: p for p in body["plans"]}
    assert set(by_name) == {"starter", "team", "enterprise"}

    assert by_name["starter"] == _plan_json(
        seeded_plans["starter"],
        name="starter",
        display_name="Starter",
        seat_cap=3,
        budget="50.00",
        rpm=60,
        tpm=40000,
    )
    assert by_name["team"] == _plan_json(
        seeded_plans["team"],
        name="team",
        display_name="Team",
        seat_cap=None,
        budget="500.00",
        rpm=600,
        tpm=400000,
    )
    assert by_name["enterprise"] == _plan_json(
        seeded_plans["enterprise"],
        name="enterprise",
        display_name="Enterprise",
        seat_cap=None,
        budget=None,
        rpm=None,
        tpm=None,
    )

    count = await _audit_count(db_session, action="platform.plan.list", target_tenant_id=None)
    assert count == 1


# ---------------------------------------------------------------------------
# M5 — view a tenant's plan
# ---------------------------------------------------------------------------


async def test_view_unplanned_tenant_plan_shows_null(
    client: httpx.AsyncClient, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await _seed_customer_tenant(db_session, name="UnplannedCo")

    resp = await client.get(
        _tenant_plan_url(tid), headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tenant_id": str(tid), "plan": None, "seat_cap": None}

    count = await _audit_count(db_session, action="platform.plan.view", target_tenant_id=tid)
    assert count == 1


async def test_view_assigned_tenant_plan_shows_full_plan_and_seat_cap(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="TeamCo", plan_id=seeded_plans["team"], seat_cap=12
    )

    resp = await client.get(
        _tenant_plan_url(tid), headers={"Authorization": f"Bearer {superadmin_token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == str(tid)
    assert body["plan"] == _plan_json(
        seeded_plans["team"],
        name="team",
        display_name="Team",
        seat_cap=None,
        budget="500.00",
        rpm=600,
        tpm=400000,
    )
    assert body["seat_cap"] == 12


# ---------------------------------------------------------------------------
# M6 — assign / change a plan
# ---------------------------------------------------------------------------


async def test_assign_plan_to_unplanned_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(db_session, name="AssignCo")

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": str(seeded_plans["team"])},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"]["name"] == "team"

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.plan_id == seeded_plans["team"]

    meta = await _audit_metadata(db_session, action="platform.plan.assign", target_tenant_id=tid)
    assert meta["old_plan_id"] is None
    assert meta["new_plan_id"] == str(seeded_plans["team"])


async def test_change_tenant_to_different_plan(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="ChangeCo", plan_id=seeded_plans["starter"], seat_cap=3
    )

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": str(seeded_plans["enterprise"])},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"]["name"] == "enterprise"

    meta = await _audit_metadata(db_session, action="platform.plan.assign", target_tenant_id=tid)
    assert meta["old_plan_id"] == str(seeded_plans["starter"])
    assert meta["new_plan_id"] == str(seeded_plans["enterprise"])


# ---------------------------------------------------------------------------
# M7 / R7 — unassign clears both fields atomically
# ---------------------------------------------------------------------------


async def test_unassign_clears_both_fields_atomically(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="UnassignCo", plan_id=seeded_plans["team"], seat_cap=25
    )

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": None},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"tenant_id": str(tid), "plan": None, "seat_cap": None}

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.plan_id is None
    assert row.seat_cap is None


async def test_seat_cap_with_null_plan_id_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="RejectUnassignCo", plan_id=seeded_plans["team"], seat_cap=25
    )

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": None, "seat_cap": 10},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.plan_id == seeded_plans["team"]
    assert row.seat_cap == 25


# ---------------------------------------------------------------------------
# M8 — omitted seat_cap copies down from the plan's own default
# ---------------------------------------------------------------------------


async def test_omitted_seat_cap_copies_plan_default(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(db_session, name="CopyDownCo")

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": str(seeded_plans["starter"])},  # seat_cap key omitted entirely
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["seat_cap"] == 3

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.seat_cap == 3


async def test_changing_tiers_recopies_seat_cap_not_stale(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="RecopyCo", plan_id=seeded_plans["starter"], seat_cap=3
    )

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": str(seeded_plans["team"])},  # seat_cap key omitted
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["seat_cap"] is None  # NOT the stale 3 from the prior assignment

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.seat_cap is None


# ---------------------------------------------------------------------------
# M9 / R6 — explicit seat_cap override
# ---------------------------------------------------------------------------


async def test_explicit_seat_cap_overrides_plan_default(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(db_session, name="NegotiatedCo")

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": str(seeded_plans["enterprise"]), "seat_cap": 47},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["seat_cap"] == 47  # NOT null, despite enterprise's own default

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.seat_cap == 47


async def test_zero_or_negative_seat_cap_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    tid = await _seed_customer_tenant(db_session, name="BadSeatCapCo")

    for bad_seat_cap in (0, -5):
        resp = await client.put(
            _tenant_plan_url(tid),
            json={"plan_id": str(seeded_plans["starter"]), "seat_cap": bad_seat_cap},
            headers={"Authorization": f"Bearer {superadmin_token}"},
        )
        assert resp.status_code == 422, f"seat_cap={bad_seat_cap}: {resp.text}"
        assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

        row = await _get_tenant(db_session, tid)
        assert row is not None
        assert row.plan_id is None
        assert row.seat_cap is None


# ---------------------------------------------------------------------------
# R1 — missing or invalid bearer token is rejected on every plan endpoint
# ---------------------------------------------------------------------------


async def test_missing_or_invalid_bearer_token_rejected_on_every_plan_endpoint(
    client: httpx.AsyncClient,
) -> None:
    tid = uuid.uuid4()
    cases: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", _plans_url(), None),
        ("GET", _tenant_plan_url(tid), None),
        ("PUT", _tenant_plan_url(tid), {"plan_id": None}),
    ]

    # Missing Authorization header entirely.
    for method, url, json_body in cases:
        resp = await client.request(method, url, json=json_body)
        assert resp.status_code == 401, f"{method} {url}: {resp.text}"
        body = resp.json()
        assert body.get("code") == "ERR_AUTH_INVALID_TOKEN"
        assert "plans" not in body
        assert "plan" not in body
        assert "seat_cap" not in body

    # Present but invalid/garbled Bearer token.
    for method, url, json_body in cases:
        resp = await client.request(
            method, url, json=json_body, headers={"Authorization": "Bearer not-a-real-jwt"}
        )
        assert resp.status_code == 401, f"{method} {url}: {resp.text}"
        assert resp.json().get("code") == "ERR_AUTH_INVALID_TOKEN"


# ---------------------------------------------------------------------------
# R2 — non-superadmin rejected on every plan endpoint, regardless of target tenant_id
# ---------------------------------------------------------------------------


async def test_non_superadmin_rejected_on_every_plan_endpoint(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = await _seed_customer_tenant(db_session, name="OwnerCo")
    other_tenant_id = await _seed_customer_tenant(db_session, name="OtherCo")
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@planreject.io"
    )
    headers = {"Authorization": f"Bearer {owner_token}"}

    # Catalog list has no target tenant_id at all.
    resp = await client.get(_plans_url(), headers=headers)
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    for target in (owner_tenant_id, other_tenant_id):
        get_resp = await client.get(_tenant_plan_url(target), headers=headers)
        assert get_resp.status_code == 403, get_resp.text
        assert get_resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

        put_resp = await client.put(
            _tenant_plan_url(target), json={"plan_id": None}, headers=headers
        )
        assert put_resp.status_code == 403, put_resp.text
        assert put_resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

        row = await _get_tenant(db_session, target)
        assert row is not None
        assert row.plan_id is None  # unchanged — no write occurred


# ---------------------------------------------------------------------------
# R3 — unknown tenant_id
# ---------------------------------------------------------------------------


async def test_unknown_tenant_id_rejected_on_get_and_put(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    seeded_plans: dict[str, uuid.UUID],
) -> None:
    missing_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {superadmin_token}"}

    get_resp = await client.get(_tenant_plan_url(missing_id), headers=headers)
    assert get_resp.status_code == 404, get_resp.text
    assert get_resp.json().get("code") == "ERR_TENANT_NOT_FOUND"

    put_resp = await client.put(
        _tenant_plan_url(missing_id),
        json={"plan_id": str(seeded_plans["starter"])},
        headers=headers,
    )
    assert put_resp.status_code == 404, put_resp.text
    assert put_resp.json().get("code") == "ERR_TENANT_NOT_FOUND"

    row = await _get_tenant(db_session, missing_id)
    assert row is None  # the UPDATE never ran — no row materialized


# ---------------------------------------------------------------------------
# R5 — unknown plan_id
# ---------------------------------------------------------------------------


async def test_unknown_plan_id_rejected_on_assign(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="UnknownPlanCo")
    unknown_plan_id = uuid.uuid4()

    resp = await client.put(
        _tenant_plan_url(tid),
        json={"plan_id": str(unknown_plan_id)},
        headers={"Authorization": f"Bearer {superadmin_token}"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_PLAN_NOT_FOUND"

    row = await _get_tenant(db_session, tid)
    assert row is not None
    assert row.plan_id is None
    assert row.seat_cap is None
