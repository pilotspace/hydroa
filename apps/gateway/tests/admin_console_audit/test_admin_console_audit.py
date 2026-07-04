"""RED->GREEN suite for admin-console-audit (TASK.md §2/§3 — FROZEN @ v1).

Covers all 15 `emit_platform_audit()` call sites across the 4 already-registered cross-tenant
routers (platform_tenants_router.py, platform_tenant_config_router.py, platform_keys_router.py,
platform_users_router.py) plus the shared helper itself
(gateway.audit.application.platform_audit.emit_platform_audit), independently unit-tested.

RED before BUILD: `gateway.audit.application.platform_audit` does not exist yet ->
  - the 3 direct unit tests of emit_platform_audit() fail with ModuleNotFoundError.
  - every HTTP-driven test fails on `assert row is not None` — the route itself still succeeds
    (main.py already registers all 4 routers, wired by the 3 prior DONE sibling tasks) but emits
    no audit row, since none of the 4 router files call emit_platform_audit() yet.
Both are the missing-implementation RED reason, never a fixture/import bug.

main.py already registers all 4 routers (platform_tenants_router, platform_tenant_config_router,
platform_keys_router, platform_users_router — confirmed via direct read of main.py:979-987, each
wired by its own owning sibling task). No local `app` fixture override is needed here, unlike the
2 sibling suites that predated that registration — this file uses the root tests/conftest.py's
own `app`/`client`/`db_session`/`settings` fixtures directly.

Fixture/helper provenance (this repo's convention is suite-local fixtures, not cross-suite
imports — matched here, not by importing from sibling test packages):
  - `platform_tenant_id`/`superadmin_token`/`_issue_token` mirror
    tests/platform_tenant_directory/test_platform_tenant_directory.py.
  - `_seed_customer_tenant` mirrors tests/cross_tenant_config_budget's own fuller variant
    (cache/guardrails/budget columns, minus markup_pct which this task's routes never touch).
  - `_seed_key`/`_seed_user` mirror tests/cross_tenant_keys_members/test_cross_tenant_keys_members.py.
  - `fetch_one_audit_row`/`count_audit_rows` + the `asyncio.sleep(0.05)` fire-and-forget-drain
    idiom mirror tests/superadmin_audit_foundation/conftest.py and tests/test_users_role.py.

Scenario 14 ("the audit subsystem's own infrastructure is untouched — pure consumer, zero
regressions", M11) is NOT a pytest assertion in this file — it is a `git diff` check performed at
§6 VERIFY (confirming zero changed lines in audit/domain/audit_event.py, audit/application/
audit_writer.py, audit/infrastructure/audit_repository.py, audit/infrastructure/
audit_events_orm.py, usage/api/router.py), mirroring cross_tenant_config_budget's own precedent
for its byte-identical-invariant scenario (file-diff confirmed separately at VERIFY, not by a
test in this file).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

PLATFORM_TENANTS = "/admin/platform/tenants"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant; seed one directly when the
    fast create_all test schema has not run the seed migration (mirrors the 3 sibling tasks'
    own fixture of the same name)."""
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
    """Mint a Bearer token directly via the live token service — no DB user row required."""
    token, _ = app.state.token_service.issue(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


@pytest.fixture
def superadmin_user_id(app: Any, superadmin_token: str) -> uuid.UUID:
    """Decode the superadmin token's own user_id — the REAL actor every audit row this task
    adds must attribute (actor_user_id) — never a placeholder."""
    identity = app.state.token_service.decode(superadmin_token)
    return identity.user_id  # type: ignore[no-any-return]


async def _seed_customer_tenant(
    db_session: AsyncSession,
    *,
    name: str,
    cache_enabled: bool = False,
    semantic_cache_enabled: bool = False,
    guardrail_configs: dict[str, Any] | None = None,
    budget_usd_monthly: float | None = None,
) -> uuid.UUID:
    """Insert a kind='customer' tenant row with explicit config/budget columns — covers every
    field the 4 routers' 15 handlers read or write (mirrors cross_tenant_config_budget's own
    fuller _seed_customer_tenant, minus markup_pct which this task's routes never touch)."""
    tid = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO tenants
                (id, name, kind, cache_enabled, semantic_cache_enabled,
                 guardrail_configs, budget_usd_monthly)
            VALUES
                (:id, :name, 'customer', :cache_enabled, :semantic_cache_enabled,
                 :guardrail_configs::jsonb, :budget_usd_monthly)
            """
        ),
        {
            "id": tid,
            "name": name,
            "cache_enabled": cache_enabled,
            "semantic_cache_enabled": semantic_cache_enabled,
            "guardrail_configs": json.dumps(guardrail_configs or {}),
            "budget_usd_monthly": budget_usd_monthly,
        },
    )
    await db_session.commit()
    return tid


async def _seed_key(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str = "seeded-key",
    revoked: bool = False,
) -> uuid.UUID:
    """Insert a minimal api_keys row directly (mirrors cross_tenant_keys_members's own helper).
    key_hash is a dummy placeholder — these tests only manage keys via the admin API, never
    authenticate WITH them. Two literal SQL strings (not an f-string interpolating the
    revoked-vs-not clause) — matches the sibling suite's own S608-avoidance convention."""
    kid = uuid.uuid4()
    params = {"id": kid, "tid": tenant_id, "name": name}
    if revoked:
        await db_session.execute(
            text(
                """
                INSERT INTO api_keys (id, tenant_id, name, key_hash, revoked_at)
                VALUES (:id, :tid, :name, 'dummy-hash-not-a-real-secret', now())
                """
            ),
            params,
        )
    else:
        await db_session.execute(
            text(
                """
                INSERT INTO api_keys (id, tenant_id, name, key_hash, revoked_at)
                VALUES (:id, :tid, :name, 'dummy-hash-not-a-real-secret', NULL)
                """
            ),
            params,
        )
    await db_session.commit()
    return kid


async def _seed_user(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str = "member",
) -> uuid.UUID:
    """Insert a users row directly (mirrors cross_tenant_keys_members's own helper)."""
    uid = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:id, :tid, :email, '$argon2id$v=dummy', :role)
            """
        ),
        {"id": uid, "tid": tenant_id, "email": email, "role": role},
    )
    await db_session.commit()
    return uid


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def fetch_one_audit_row(session: AsyncSession, *, action: str) -> Row[Any] | None:
    """Return the single most-recent audit_events row for `action`, or None (mirrors
    tests/superadmin_audit_foundation/conftest.py's own helper)."""
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


async def _drain_fire_and_forget() -> None:
    """Let a fire-and-forget asyncio.ensure_future(record_audit(...)) task complete before
    querying audit_events (mirrors tests/test_users_role.py's own `asyncio.sleep(0.05)` idiom)."""
    await asyncio.sleep(0.05)


# ===========================================================================
# 1/15 — platform_tenants_router.py: list_platform_tenants (M3 — system-level event)
# ===========================================================================


async def test_bulk_tenant_list_audited_as_system_level_event(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): the bulk tenant directory list is audited as a system-level event (no
    single target) — M3. tenant_id IS NULL, still attributes the real superadmin actor,
    mirroring ops.platform_credential_resolve's own tenant_id=None precedent."""
    await _seed_customer_tenant(db_session, name="ListAuditCo1")
    await _seed_customer_tenant(db_session, name="ListAuditCo2")
    await _seed_customer_tenant(db_session, name="ListAuditCo3")

    resp = await client.get(PLATFORM_TENANTS, headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.tenant.list")
    assert row is not None, "Expected exactly one platform.tenant.list audit event"
    tenant_id, actor_user_id, _actor_email, action, target_type, target_id, result, metadata = row
    assert tenant_id is None, "bulk list is a system-level event — no single target tenant"
    assert actor_user_id == superadmin_user_id
    assert action == "platform.tenant.list"
    assert result == "success"
    assert target_type == "tenant"
    assert target_id is None
    assert metadata == {}


# ===========================================================================
# 2/15 — platform_tenants_router.py: get_platform_tenant_by_id (M2)
# ===========================================================================


async def test_tenant_view_get_one_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """M2: the get-one tenant-directory read is audited — closes the retrofit
    platform-tenant-directory's own Spec delta explicitly seeded for this task."""
    tid = await _seed_customer_tenant(db_session, name="ViewAuditCo")

    resp = await client.get(f"{PLATFORM_TENANTS}/{tid}", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.tenant.view")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid), "tenant_id must be the TARGET tenant"
    assert actor_user_id == superadmin_user_id
    assert target_type == "tenant"
    assert target_id == str(tid)
    assert metadata == {}


# ===========================================================================
# 3/15 — platform_tenant_config_router.py: get_platform_tenant_cache (M2, M4, M8)
# ===========================================================================


async def test_cross_tenant_cache_read_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): a cross-tenant read (GET cache) is now audited — zero self-service
    precedent, by design (M2, M4, M8)."""
    tid = await _seed_customer_tenant(db_session, name="CacheViewAuditCo", cache_enabled=True)

    resp = await client.get(f"{PLATFORM_TENANTS}/{tid}/cache", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": True, "semantic_enabled": False}

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.cache.view")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "cache"
    assert target_id == "config"
    assert metadata == {}


# ===========================================================================
# 4/15 — platform_tenant_config_router.py: put_platform_tenant_cache
# ===========================================================================


async def test_cross_tenant_cache_update_is_audited_with_post_update_state(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="CacheUpdateAuditCo", cache_enabled=False, semantic_cache_enabled=False
    )

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{tid}/cache",
        json={"enabled": True},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.cache.update")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "cache"
    assert target_id == "config"
    assert metadata == {"enabled": True, "semantic_enabled": False}


# ===========================================================================
# 5/15 — platform_tenant_config_router.py: get_platform_tenant_guardrails
# ===========================================================================


async def test_cross_tenant_guardrails_read_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(
        db_session,
        name="GuardrailsViewAuditCo",
        guardrail_configs={"prompt_injection": {"enabled": True, "mode": "block"}},
    )

    resp = await client.get(
        f"{PLATFORM_TENANTS}/{tid}/guardrails", headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.guardrails.view")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "guardrails"
    assert target_id == "config"
    assert metadata == {}


# ===========================================================================
# 6/15 — platform_tenant_config_router.py: put_platform_tenant_guardrails (M2, M8)
# ===========================================================================


async def test_guardrails_update_audited_with_changed_field_names(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): a guardrails update is audited with the changed field names, never the
    full config (M2, M8)."""
    tid = await _seed_customer_tenant(
        db_session,
        name="GuardrailsUpdateAuditCo",
        guardrail_configs={"prompt_injection": {"enabled": True, "mode": "block"}},
    )

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{tid}/guardrails",
        json={"pii_mask": {"enabled": True, "mode": "mask"}},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.guardrails.update")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "guardrails"
    assert target_id == "config"
    assert metadata == {"fields_changed": ["pii_mask"]}
    assert "prompt_injection" not in json.dumps(metadata), (
        "metadata must carry only the changed field NAME, never the full guardrail_configs blob"
    )


# ===========================================================================
# 7/15 — platform_tenant_config_router.py: get_platform_tenant_budget
# ===========================================================================


async def test_cross_tenant_budget_read_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(
        db_session, name="BudgetViewAuditCo", budget_usd_monthly=500.00
    )

    resp = await client.get(f"{PLATFORM_TENANTS}/{tid}/budget", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.budget.view")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "budget"
    assert target_id == "monthly"
    assert metadata == {}


# ===========================================================================
# 8/15 — platform_tenant_config_router.py: put_platform_tenant_budget (M1, M2, M5, M7)
# ===========================================================================


async def test_cross_tenant_budget_put_is_audited_closing_regression(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): cross-tenant budget PUT is now audited — closing the flagged regression
    (M1, M2, M5, M7): self-service PUT /admin/budget has audited this same kind of change since
    before this milestone; the cross-tenant equivalent now does too."""
    tid = await _seed_customer_tenant(db_session, name="BudgetPutAuditCo")

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{tid}/budget",
        json={"budget_usd_monthly": "250.00"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"budget_usd_monthly": "250.00"}

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.budget.update")
    assert row is not None
    tenant_id, actor_user_id, actor_email, _action, target_type, target_id, result, metadata = row
    assert str(tenant_id) == str(tid), (
        "tenant_id must be the TARGET tenant, not the superadmin's own"
    )
    assert actor_user_id == superadmin_user_id
    assert actor_email == "root@platform.internal"
    assert target_type == "budget"
    assert target_id == "monthly"
    assert result == "success"
    assert metadata == {"budget_usd_monthly": "250.00"}


# ===========================================================================
# 9/15 — platform_keys_router.py: list_platform_tenant_keys
# ===========================================================================


async def test_key_list_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="KeyListAuditCo")
    await _seed_key(db_session, tenant_id=tid, name="k1")

    resp = await client.get(f"{PLATFORM_TENANTS}/{tid}/keys", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.key.list")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "api_key"
    assert target_id is None
    assert metadata == {}


# ===========================================================================
# 10/15 — platform_keys_router.py: create_platform_tenant_key (M2, M6, M7)
# ===========================================================================


async def test_key_creation_audited_without_plaintext_secret(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): key creation is audited without ever recording the plaintext secret
    (M2, M6, M7)."""
    tid = await _seed_customer_tenant(db_session, name="KeyCreateAuditCo")

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{tid}/keys",
        json={"name": "cross-tenant-audited-key"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 201, resp.text
    plaintext_key = resp.json()["key"]
    key_id = resp.json()["key_id"]

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.key.create")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "api_key"
    assert target_id == str(key_id)
    assert metadata == {"key_name": "cross-tenant-audited-key"}
    assert plaintext_key not in json.dumps(metadata)
    assert "key" not in metadata


# ===========================================================================
# 11/15 — platform_keys_router.py: patch_platform_tenant_key (M2, M8)
# ===========================================================================


async def test_key_patch_audited_despite_no_self_service_precedent(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): a key patch is audited even though self-service PATCH has no audit
    precedent at all — NEW coverage, not a regression fix (M2, M8)."""
    tid = await _seed_customer_tenant(db_session, name="KeyPatchAuditCo")
    key_id = await _seed_key(db_session, tenant_id=tid, name="patch-target")

    resp = await client.patch(
        f"{PLATFORM_TENANTS}/{tid}/keys/{key_id}",
        json={"monthly_budget_usd": "42.50"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.key.patch")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "api_key"
    assert target_id == str(key_id)
    assert metadata == {"fields_changed": ["monthly_budget_usd"]}


# ===========================================================================
# 12/15 — platform_keys_router.py: rotate_platform_tenant_key (M2, M6, M7)
# ===========================================================================


async def test_key_rotation_audited_without_plaintext_secret(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): key rotation is audited without recording either the old or the new
    plaintext secret (M2, M6, M7)."""
    tid = await _seed_customer_tenant(db_session, name="KeyRotateAuditCo")
    key_id = await _seed_key(db_session, tenant_id=tid, name="rotate-target")

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{tid}/keys/{key_id}/rotate",
        json={},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 201, resp.text
    new_plaintext_key = resp.json()["key"]
    new_key_id = resp.json()["new_key_id"]

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.key.rotate")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "api_key"
    assert target_id == str(new_key_id)
    assert metadata == {"superseded_key_id": str(key_id), "key_name": "rotate-target"}
    assert new_plaintext_key not in json.dumps(metadata)


# ===========================================================================
# 13/15 — platform_keys_router.py: revoke_platform_tenant_key
# ===========================================================================


async def test_key_revocation_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="KeyRevokeAuditCo")
    key_id = await _seed_key(db_session, tenant_id=tid, name="revoke-target")

    resp = await client.delete(
        f"{PLATFORM_TENANTS}/{tid}/keys/{key_id}", headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 204, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.key.revoke")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "api_key"
    assert target_id == str(key_id)
    assert metadata == {}


# ===========================================================================
# 14/15 — platform_users_router.py: list_platform_tenant_users
# ===========================================================================


async def test_user_list_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    tid = await _seed_customer_tenant(db_session, name="UserListAuditCo")
    await _seed_user(db_session, tenant_id=tid, email="member1@userlistaudit.io")

    resp = await client.get(f"{PLATFORM_TENANTS}/{tid}/users", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.user.list")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "user"
    assert target_id is None
    assert metadata == {}


# ===========================================================================
# 15/15 — platform_users_router.py: assign_platform_tenant_user_role (M2, M7, M10)
# ===========================================================================


async def test_role_reassignment_audited_with_old_and_new_role(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """Scenario (§2): a role reassignment is audited with both old and new role, mirroring
    self-service (M2, M7, M10) — requires the new old-role fetch this task adds (M10b), with
    tenant_id = the PATH tenant_id (never identity.tenant_id, the superadmin's own tenant)."""
    tid = await _seed_customer_tenant(db_session, name="RoleAssignAuditCo")
    user_id = await _seed_user(
        db_session, tenant_id=tid, email="member2@roleassignaudit.io", role="member"
    )

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{tid}/users/{user_id}/role",
        json={"role": "admin"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.user.role_assign")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, _result, metadata = row
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert target_type == "user"
    assert target_id == str(user_id)
    assert metadata == {
        "target_user_id": str(user_id),
        "old_role": "member",
        "new_role": "admin",
    }


# ===========================================================================
# Rejections produce ZERO audit rows (M9, R2/R3/R4) — explicit, named decisions
# ===========================================================================


async def test_403_rejection_produces_zero_audit_rows(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    """Scenario (§2): a REJECTED (403) cross-tenant attempt produces zero audit rows —
    explicit, named decision (M9, R2): auditing a rejection would require modifying the
    shared, FROZEN require_superadmin predicate, out of this task's declared scope."""
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = uuid.uuid4()
    tid = await _seed_customer_tenant(db_session, name="Owner403AuditCo")
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@403audit.io"
    )

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{tid}/budget",
        json={"budget_usd_monthly": "999.00"},
        headers=_bearer(owner_token),
    )
    assert resp.status_code == 403, resp.text

    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="platform.budget.update") == 0


async def test_404_nonexistent_target_tenant_produces_zero_audit_rows(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Scenario (§2): a REJECTED (404, nonexistent target tenant) attempt produces zero
    audit rows (M9, R3)."""
    missing_tid = uuid.uuid4()

    resp = await client.get(
        f"{PLATFORM_TENANTS}/{missing_tid}/keys", headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"

    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="platform.key.list") == 0


async def test_422_payload_validation_produces_zero_audit_rows(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Scenario (§2): a REJECTED (422 payload validation) attempt produces zero audit rows
    (M9, R4)."""
    tid = await _seed_customer_tenant(db_session, name="Payload422AuditCo")

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{tid}/budget",
        json={"budget_usd_monthly": "-10.00"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 422, resp.text

    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="platform.budget.update") == 0


# ===========================================================================
# Non-superadmin behavior is completely unchanged (M10, byte-identical invariant)
# ===========================================================================


async def test_non_superadmin_existing_self_service_behavior_unchanged(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    """Scenario (§2): non-superadmin callers' existing behavior on every OTHER route is
    completely unchanged — this task's changes are additive (new Request param + one new
    call) on routes already gated superadmin-only; no non-superadmin request reaches any of
    this task's changed code at all. Also proves self-service's OWN existing audit
    (budget.update) still fires, unaffected by this task's NEW platform.* action family."""
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = await _seed_customer_tenant(
        db_session, name="SelfServiceUnchangedCo", budget_usd_monthly=100.0
    )
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@selfservice.io"
    )
    headers = _bearer(owner_token)

    assert (await client.get("/admin/cache", headers=headers)).status_code == 200
    assert (await client.get("/admin/guardrails", headers=headers)).status_code == 200
    assert (await client.get("/admin/keys", headers=headers)).status_code == 200
    assert (await client.get("/admin/users", headers=headers)).status_code == 200

    budget_resp = await client.get("/admin/budget", headers=headers)
    assert budget_resp.status_code == 200
    assert budget_resp.json()["budget_usd_monthly"] == "100.00"

    put_resp = await client.put(
        "/admin/budget", json={"budget_usd_monthly": "150.00"}, headers=headers
    )
    assert put_resp.status_code == 200, put_resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="budget.update")
    assert row is not None, "self-service budget.update audit must still fire, unchanged"
    tenant_id = row[0]
    assert str(tenant_id) == str(owner_tenant_id)


# ===========================================================================
# Audit-write failure never blocks the caller's HTTP response (M12)
# ===========================================================================


async def test_audit_write_failure_never_blocks_cross_tenant_http_response(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    superadmin_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scenario (§2): an audit-write failure never blocks or changes a cross-tenant route's
    HTTP response (M12) — simulates an audit DB outage for the fire-and-forget audit write
    ONLY, leaving the route's own primary request session healthy.

    core/db.py:get_session (the route's OWN `Depends(get_session)`) and
    emit_platform_audit/record_audit (the fire-and-forget audit write) both read
    `request.app.state.sessionmaker` — the SAME attribute (confirmed by direct read of
    core/db.py:73-75) — so unconditionally replacing it would ALSO break the route's own
    primary DB session before it ever reaches the audit call, which is a test-design bug, not
    the scenario under test ("an audit-write failure", not "a request-session failure").
    get_session's own `async with sessionmaker() as session` calls the sessionmaker exactly
    ONCE per request (FastAPI caches each unique dependency's resolution per request); this
    wrapper lets that FIRST call through to the REAL sessionmaker, then fails every
    subsequent call — the fire-and-forget record_audit()'s own, separate
    `session_factory()` call inside emit_platform_audit."""
    tid = await _seed_customer_tenant(
        db_session, name="AuditOutageHttpCo", cache_enabled=False, semantic_cache_enabled=False
    )
    real_sessionmaker = app.state.sessionmaker
    call_count = {"n": 0}

    def _fail_after_first_call(*args: object, **kwargs: object) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_sessionmaker(*args, **kwargs)
        raise RuntimeError("audit db unreachable (simulated outage)")

    app.state.sessionmaker = _fail_after_first_call
    try:
        with caplog.at_level(logging.WARNING):
            resp = await client.put(
                f"{PLATFORM_TENANTS}/{tid}/cache",
                json={"enabled": True},
                headers=_bearer(superadmin_token),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"enabled": True, "semantic_enabled": False}
            await _drain_fire_and_forget()
    finally:
        app.state.sessionmaker = real_sessionmaker

    assert call_count["n"] >= 2, (
        "expected at least 2 sessionmaker calls (the request's own get_session + the "
        "fire-and-forget audit write) — got fewer, meaning emit_platform_audit was never "
        "reached at all (this would be a false pass, not a proof of fail-open)"
    )

    assert "failed to persist audit event" in caplog.text


# ===========================================================================
# emit_platform_audit() — independently unit-testable (per §3's own framing)
# ===========================================================================


async def test_emit_platform_audit_writes_target_tenant_event(
    db_session: AsyncSession,
    app: Any,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """emit_platform_audit() is independently unit-testable — no HTTP/router involvement
    needed. Proves the helper alone builds a correct AuditEvent and schedules record_audit()."""
    from gateway.audit.application.platform_audit import emit_platform_audit

    identity = app.state.token_service.decode(superadmin_token)
    tid = uuid.uuid4()

    await emit_platform_audit(
        app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tid,
        action="platform.tenant.view",
        target_type="tenant",
        target_id=str(tid),
        metadata={},
    )

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.tenant.view")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, _action, _target_type, _target_id, result, metadata = (
        row
    )
    assert str(tenant_id) == str(tid)
    assert actor_user_id == superadmin_user_id
    assert result == "success"
    assert metadata == {}


async def test_emit_platform_audit_system_level_event_tenant_id_none(
    db_session: AsyncSession,
    app: Any,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """emit_platform_audit() permits target_tenant_id=None for a system-level event —
    AuditEvent's own __post_init__ invariant only requires actor_user_id when tenant_id is
    not None; actor_user_id is populated here regardless (M5)."""
    from gateway.audit.application.platform_audit import emit_platform_audit

    identity = app.state.token_service.decode(superadmin_token)

    await emit_platform_audit(
        app.state.sessionmaker,
        identity=identity,
        target_tenant_id=None,
        action="platform.tenant.list",
        target_type="tenant",
        target_id=None,
        metadata={},
    )

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="platform.tenant.list")
    assert row is not None
    (
        tenant_id,
        actor_user_id,
        _actor_email,
        _action,
        _target_type,
        _target_id,
        _result,
        _metadata,
    ) = row
    assert tenant_id is None
    assert actor_user_id == superadmin_user_id


async def test_emit_platform_audit_fails_open_on_audit_db_outage(
    app: Any,
    superadmin_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M12: an audit-write failure never raises out of emit_platform_audit — record_audit's
    own FROZEN fail-open contract, inherited verbatim (mirrors
    tests/superadmin_audit_foundation/conftest.py's own failing_session_factory pattern)."""
    from gateway.audit.application.platform_audit import emit_platform_audit

    identity = app.state.token_service.decode(superadmin_token)

    def _raise(*_args: object, **_kwargs: object) -> AsyncSession:
        raise RuntimeError("audit db unreachable (simulated outage)")

    with caplog.at_level(logging.WARNING):
        await emit_platform_audit(
            _raise,  # type: ignore[arg-type]
            identity=identity,
            target_tenant_id=uuid.uuid4(),
            action="platform.tenant.view",
            target_type="tenant",
            target_id="whatever",
            metadata={},
        )
        await _drain_fire_and_forget()

    assert "failed to persist audit event" in caplog.text
