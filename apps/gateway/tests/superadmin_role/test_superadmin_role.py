"""Red suite for superadmin-role (contract DRAFT — TASK.md §2).

Twelve tests per the §4 plan. Pure tests (role/permission matrix, authorize_tenant_scope,
the use-case guard) need no DB. Schema/DML tests use the standard create_all `db_session`
fixture plus a local `superadmin_guard_session` fixture that manually re-applies the new
trigger SQL — create_all mirrors the CHECK constraint via __table_args__ automatically but
never runs migration-only op.execute() DDL (mirrors tests/audit/test_audit_store.py's
immutable_audit_session fixture). ONE test drives the real Alembic migration end-to-end via
the tests/migrations/conftest.py harness (mirrors platform_tenant_seed's worked example).

DO NOT change these tests to make them pass — that is the Build phase's job. DO NOT modify
apps/gateway/tests/test_users_role.py::test_role_validation — this task's router-level Must
is designed specifically so that existing test keeps passing unmodified (see TASK.md §1/§3).
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.migrations.conftest import (  # noqa: F401 — migration_db is a transitive fixture dep
    MIGRATION_DSN,
    clean_migration_db,
    migration_db,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_config() -> object:  # returns alembic.config.Config at runtime
    from alembic.config import Config  # noqa: PLC0415

    from tests.migrations.conftest import ALEMBIC_INI, MIGRATION_DATABASE_URL  # noqa: PLC0415

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return cfg


class _UnreachableRepo:
    """Stub repo — any call proves the superadmin guard failed to short-circuit."""

    async def get_by_id_and_tenant(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID) -> Any:
        raise AssertionError("repository must not be reached when new_role=SUPERADMIN")

    async def update_role(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, new_role: Any) -> Any:
        raise AssertionError("repository must not be reached when new_role=SUPERADMIN")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Insert a kind='platform' tenant row directly (create_all seeds no data)."""
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


@pytest.fixture
async def customer_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Insert an ordinary kind='customer' tenant row."""
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Acme', 'customer')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


@pytest.fixture
async def superadmin_guard_session(db_session: AsyncSession) -> AsyncSession:
    """Apply the superadmin/platform-tenant guard TRIGGER to the create_all schema.

    Base.metadata.create_all creates the table + the widened CHECK constraint (both are
    declarative SQLAlchemy constructs mirrored in UserRow.__table_args__) but does NOT run
    the migration's trigger creation (op.execute() calls are migration-only). This fixture
    applies the trigger manually, mirroring tests/audit/test_audit_store.py's
    immutable_audit_session fixture exactly, so most scenarios can run against the fast
    per-test schema instead of the full Alembic harness (see
    test_migration_creates_widened_check_and_guard_trigger for the real-migration proof).
    """
    await db_session.execute(
        text("""
        CREATE OR REPLACE FUNCTION users_superadmin_platform_tenant_guard_fn()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.role = 'superadmin' THEN
            IF NOT EXISTS (
              SELECT 1 FROM tenants WHERE id = NEW.tenant_id AND kind = 'platform'
            ) THEN
              RAISE EXCEPTION 'superadmin_requires_platform_tenant: role=superadmin is only '
                              'permitted for the platform tenant';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """)
    )
    await db_session.execute(
        text("""
        CREATE OR REPLACE TRIGGER users_superadmin_platform_tenant_guard
        BEFORE INSERT OR UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION users_superadmin_platform_tenant_guard_fn()
        """)
    )
    await db_session.commit()
    return db_session


@pytest.fixture
async def seeded_owner_and_target(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> dict[str, Any]:
    """Signup an owner + a second member user in the same tenant (target for role changes)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "SuperadminGuardCo",
            "email": "owner@superadminguard.io",
            "password": "guard-horse-battery-01",
        },
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": "owner@superadminguard.io", "password": "guard-horse-battery-01"},
    )
    assert login.status_code == 200, login.text
    tenant_id = uuid.UUID(signup.json()["tenant_id"])

    target_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:id, :tid, 'member@superadminguard.io', '$argon2id$v=dummy', 'member')
            """
        ),
        {"id": target_id, "tid": tenant_id},
    )
    await db_session.commit()

    return {
        "owner_token": login.json()["access_token"],
        "target_id": target_id,
        "tenant_id": tenant_id,
    }


# ---------------------------------------------------------------------------
# TEST 1: Role + ROLE_PERMISSIONS
# ---------------------------------------------------------------------------


def test_superadmin_role_exists_with_full_permissions() -> None:
    from gateway.tenants.domain.authz import ROLE_PERMISSIONS, Permission
    from gateway.tenants.domain.entities import Role

    assert Role.SUPERADMIN == "superadmin"
    assert Role.SUPERADMIN in ROLE_PERMISSIONS
    assert ROLE_PERMISSIONS[Role.SUPERADMIN] == frozenset(Permission), (
        "superadmin must hold ALL permissions (parity with owner within its own tenant)"
    )
    # Role.OPERATOR (per-tenant) is untouched and distinct from this new value.
    assert Role.OPERATOR == "operator"
    assert Role.OPERATOR != Role.SUPERADMIN


# ---------------------------------------------------------------------------
# TEST 2: ORM/CHECK accepts superadmin under the platform tenant (positive)
# ---------------------------------------------------------------------------


async def test_orm_accepts_superadmin_role_under_platform_tenant(
    superadmin_guard_session: AsyncSession,
    platform_tenant_id: uuid.UUID,
) -> None:
    uid = uuid.uuid4()
    await superadmin_guard_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:uid, :tid, 'root@platform.internal', 'dummyhash', 'superadmin')
            """
        ),
        {"uid": uid, "tid": platform_tenant_id},
    )
    await superadmin_guard_session.commit()  # must NOT raise

    role = (
        await superadmin_guard_session.execute(
            text("SELECT role FROM users WHERE id = :uid"), {"uid": uid}
        )
    ).scalar_one()
    assert role == "superadmin"


# ---------------------------------------------------------------------------
# TEST 3: CHECK still rejects a wholly unknown role value
# ---------------------------------------------------------------------------


async def test_orm_rejects_unknown_role_value(
    db_session: AsyncSession,
    customer_tenant_id: uuid.UUID,
) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            text(
                """
                INSERT INTO users (id, tenant_id, email, password_hash, role)
                VALUES (:uid, :tid, 'bogus@acme.io', 'dummyhash', 'bogus_role')
                """
            ),
            {"uid": uuid.uuid4(), "tid": customer_tenant_id},
        )
        await db_session.commit()
    assert getattr(exc_info.value.orig, "sqlstate", None) == "23514"
    await db_session.rollback()


# ---------------------------------------------------------------------------
# TEST 4: superadmin INSERT rejected outside the platform tenant (negative)
# ---------------------------------------------------------------------------


async def test_superadmin_insert_rejected_outside_platform_tenant(
    superadmin_guard_session: AsyncSession,
    customer_tenant_id: uuid.UUID,
) -> None:
    uid = uuid.uuid4()
    with pytest.raises(Exception, match="superadmin_requires_platform_tenant"):
        await superadmin_guard_session.execute(
            text(
                """
                INSERT INTO users (id, tenant_id, email, password_hash, role)
                VALUES (:uid, :tid, 'fake-root@acme.io', 'dummyhash', 'superadmin')
                """
            ),
            {"uid": uid, "tid": customer_tenant_id},
        )
        await superadmin_guard_session.commit()
    await superadmin_guard_session.rollback()

    count = (
        await superadmin_guard_session.execute(
            text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": uid}
        )
    ).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# TEST 5: existing user cannot be promoted to superadmin outside the platform tenant
# ---------------------------------------------------------------------------


async def test_superadmin_update_rejected_outside_platform_tenant(
    superadmin_guard_session: AsyncSession,
    customer_tenant_id: uuid.UUID,
) -> None:
    uid = uuid.uuid4()
    await superadmin_guard_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:uid, :tid, 'member@acme.io', 'dummyhash', 'member')
            """
        ),
        {"uid": uid, "tid": customer_tenant_id},
    )
    await superadmin_guard_session.commit()

    with pytest.raises(Exception, match="superadmin_requires_platform_tenant"):
        await superadmin_guard_session.execute(
            text("UPDATE users SET role = 'superadmin' WHERE id = :uid"), {"uid": uid}
        )
        await superadmin_guard_session.commit()
    await superadmin_guard_session.rollback()

    role = (
        await superadmin_guard_session.execute(
            text("SELECT role FROM users WHERE id = :uid"), {"uid": uid}
        )
    ).scalar_one()
    assert role == "member"


# ---------------------------------------------------------------------------
# TEST 6: the real migration, end-to-end (positive + negative)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_migration_db")
async def test_migration_creates_widened_check_and_guard_trigger() -> None:
    from alembic import command  # noqa: PLC0415

    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn: asyncpg.Connection = await asyncpg.connect(dsn=MIGRATION_DSN)
    try:
        platform_id = await conn.fetchval("SELECT id FROM tenants WHERE kind = 'platform'")
        customer_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, kind) VALUES ($1, 'Acme', 'customer')", customer_id
        )

        # Positive: superadmin under the platform tenant succeeds.
        good_uid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES ($1, $2, 'root@platform.internal', 'dummyhash', 'superadmin')",
            good_uid,
            platform_id,
        )

        # Negative: superadmin under a customer tenant is rejected by the trigger.
        bad_uid = uuid.uuid4()
        raised = False
        try:
            await conn.execute(
                "INSERT INTO users (id, tenant_id, email, password_hash, role) "
                "VALUES ($1, $2, 'fake-root@acme.io', 'dummyhash', 'superadmin')",
                bad_uid,
                customer_id,
            )
        except asyncpg.PostgresError as exc:
            raised = True
            assert "superadmin_requires_platform_tenant" in str(exc)
        assert raised, "expected the guard trigger to reject a non-platform superadmin insert"

        count = await conn.fetchval("SELECT count(*) FROM users WHERE role = 'superadmin'")
    finally:
        await conn.close()

    assert count == 1  # only the platform-tenant row persisted


# ---------------------------------------------------------------------------
# TEST 7: the generic role-assignment endpoint never accepts superadmin
# ---------------------------------------------------------------------------


async def test_assign_role_endpoint_rejects_superadmin_payload(
    client: httpx.AsyncClient,
    seeded_owner_and_target: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """422 ERR_PAYLOAD_INVALID — byte-identical to the pre-existing 'unknown role literal'
    rejection shape (test_users_role.py::test_role_validation), never a 403 and never a 200."""
    target_id = seeded_owner_and_target["target_id"]
    r = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "superadmin"},
        headers={"Authorization": f"Bearer {seeded_owner_and_target['owner_token']}"},
    )
    assert r.status_code == 422, r.text
    assert r.json().get("code") == "ERR_PAYLOAD_INVALID", r.text

    role = (
        await db_session.execute(text("SELECT role FROM users WHERE id = :id"), {"id": target_id})
    ).scalar_one()
    assert role == "member"  # unchanged


# ---------------------------------------------------------------------------
# TEST 8: AssignUserRoleUseCase rejects superadmin before touching the repository
# ---------------------------------------------------------------------------


async def test_assign_user_role_use_case_rejects_superadmin_directly() -> None:
    from gateway.tenants.application.users_use_cases import AssignUserRoleUseCase
    from gateway.tenants.domain.entities import Role
    from gateway.tenants.domain.errors import EscalationForbiddenError

    use_case = AssignUserRoleUseCase(_UnreachableRepo())  # type: ignore[arg-type]

    with pytest.raises(EscalationForbiddenError):
        await use_case.execute(
            caller_user_id=uuid.uuid4(),
            caller_role=Role.OWNER,
            tenant_id=uuid.uuid4(),
            target_user_id=uuid.uuid4(),
            new_role=Role.SUPERADMIN,
        )


# ---------------------------------------------------------------------------
# TEST 9-11: authorize_tenant_scope
# ---------------------------------------------------------------------------


def test_authorize_tenant_scope_allows_superadmin_cross_tenant() -> None:
    from gateway.tenants.domain.authz import authorize_tenant_scope
    from gateway.tenants.domain.entities import Identity, Role

    platform_tenant_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()
    identity = Identity(
        user_id=uuid.uuid4(),
        tenant_id=platform_tenant_id,
        email="root@platform.internal",
        role=Role.SUPERADMIN,
    )
    authorize_tenant_scope(identity, other_tenant_id)  # must not raise
    authorize_tenant_scope(identity, platform_tenant_id)  # must not raise


def test_authorize_tenant_scope_rejects_non_superadmin_cross_tenant() -> None:
    from gateway.core.errors import ProblemError
    from gateway.tenants.domain.authz import authorize_tenant_scope
    from gateway.tenants.domain.entities import Identity, Role

    own_tenant_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()
    non_superadmin_roles = [
        Role.OWNER,
        Role.ADMIN,
        Role.OPERATOR,
        Role.BILLING_ADMIN,
        Role.VIEWER,
        Role.MEMBER,
    ]
    for role in non_superadmin_roles:
        identity = Identity(
            user_id=uuid.uuid4(), tenant_id=own_tenant_id, email=f"{role.value}@acme.io", role=role
        )
        with pytest.raises(ProblemError) as exc_info:
            authorize_tenant_scope(identity, other_tenant_id)
        assert exc_info.value.code == "ERR_AUTH_FORBIDDEN", f"role={role.value}"


def test_authorize_tenant_scope_allows_same_tenant_for_any_role() -> None:
    from gateway.tenants.domain.authz import authorize_tenant_scope
    from gateway.tenants.domain.entities import Identity, Role

    tenant_id = uuid.uuid4()
    for role in Role:
        identity = Identity(
            user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"{role.value}@acme.io", role=role
        )
        authorize_tenant_scope(identity, tenant_id)  # must not raise for ANY role


# ---------------------------------------------------------------------------
# TEST 12: signup regression guard
# ---------------------------------------------------------------------------


async def test_signup_creates_owner_never_superadmin(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Regression guard: create_tenant_with_owner is untouched by this task — it must keep
    hardcoding role='owner'. get_or_provision_oidc_user (hardcoded role='member') is confirmed
    unchanged by code inspection; its own SSO/OIDC suites cover it and are unmodified here."""
    resp = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RegressionCo",
            "email": "owner@regressionguard.io",
            "password": "regress-horse-battery-01",
        },
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["user_id"]

    role = (
        await db_session.execute(text("SELECT role FROM users WHERE id = :id"), {"id": user_id})
    ).scalar_one()
    assert role == "owner"
