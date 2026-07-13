"""Failing-first (RED) suite for service-tiers M1/M2 (tier resolution) and the
tenant-scoped /admin/service-tiers/* admin router (service-tiers TASK.md §4,
contract FROZEN @ v1).

RED reason before BUILD: `AuthzResult.tier` does not exist -> AttributeError;
`/admin/service-tiers/*` is not mounted -> 404, not the contracted 200/403/422.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role
from tests.service_tiers.conftest import assert_problem, bearer


def _issue_token(app: Any, *, tenant_id: uuid.UUID, role: Role) -> str:
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=role,
        email=f"{role.value}@service-tiers-rbac.local",
    )
    return token


# ---------------------------------------------------------------------------
# Scenario — key-level tier override wins over tenant default (M1)
# ---------------------------------------------------------------------------


async def test_key_level_tier_override_wins_over_tenant_default(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET default_tier = 'standard' WHERE id = :t"),
        {"t": api_key["tenant_id"]},
    )
    await db_session.commit()

    created = await client.post(
        "/admin/keys",
        json={"name": "priority-key", "tier": "priority"},
        headers=bearer(api_key["jwt"]),
    )
    assert created.status_code == 201, created.text
    assert created.json()["tier"] == "priority"

    # A sibling key with no override still resolves the tenant default ("standard").
    sibling = await client.post(
        "/admin/keys", json={"name": "sibling-key"}, headers=bearer(api_key["jwt"])
    )
    assert sibling.status_code == 201, sibling.text
    assert sibling.json()["tier"] is None, "no override was set — tier stays NULL on the row"

    # Assert resolution directly via the repository/use-case (no dedicated HTTP authz
    # endpoint exists to introspect a key's resolved tier without a live chat call).
    from gateway.keys.application.use_cases import AuthzUseCase
    from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
    from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

    repo = SqlAlchemyApiKeyRepository(db_session)
    use_case = AuthzUseCase(repo, Sha256SecretHasher())
    result = await use_case.execute(raw_key=created.json()["key"])
    assert result.tier == "priority"
    assert result.tier_source == "key"

    sibling_result = await use_case.execute(raw_key=sibling.json()["key"])
    assert sibling_result.tier == "standard"
    assert sibling_result.tier_source == "tenant"


# ---------------------------------------------------------------------------
# Scenario — absent key override falls back to the tenant default (M1)
# ---------------------------------------------------------------------------


async def test_absent_key_override_falls_back_to_tenant_default(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET default_tier = 'priority' WHERE id = :t"),
        {"t": api_key["tenant_id"]},
    )
    await db_session.commit()

    created = await client.post(
        "/admin/keys", json={"name": "inherits-default"}, headers=bearer(api_key["jwt"])
    )
    assert created.status_code == 201, created.text

    from gateway.keys.application.use_cases import AuthzUseCase
    from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
    from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

    repo = SqlAlchemyApiKeyRepository(db_session)
    use_case = AuthzUseCase(repo, Sha256SecretHasher())
    result = await use_case.execute(raw_key=created.json()["key"])
    assert result.tier == "priority"
    assert result.tier_source == "tenant"


# ---------------------------------------------------------------------------
# Scenario — tier change mid-flight takes effect on the very next request (M2)
# ---------------------------------------------------------------------------


async def test_tier_patch_takes_effect_on_next_request(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    created = await client.post(
        "/admin/keys", json={"name": "mutable-tier"}, headers=bearer(api_key["jwt"])
    )
    assert created.status_code == 201, created.text
    key_id = created.json()["key_id"]

    patch = await client.patch(
        f"/admin/keys/{key_id}", json={"tier": "priority"}, headers=bearer(api_key["jwt"])
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["tier"] == "priority"

    from gateway.keys.application.use_cases import AuthzUseCase
    from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
    from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

    repo = SqlAlchemyApiKeyRepository(db_session)
    use_case = AuthzUseCase(repo, Sha256SecretHasher())
    result = await use_case.execute(raw_key=created.json()["key"])
    assert result.tier == "priority"


async def test_clearing_key_override_reverts_to_tenant_default(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET default_tier = 'standard' WHERE id = :t"),
        {"t": api_key["tenant_id"]},
    )
    await db_session.commit()

    created = await client.post(
        "/admin/keys",
        json={"name": "clearable-tier", "tier": "priority"},
        headers=bearer(api_key["jwt"]),
    )
    assert created.status_code == 201, created.text
    key_id = created.json()["key_id"]

    patch = await client.patch(
        f"/admin/keys/{key_id}", json={"tier": None}, headers=bearer(api_key["jwt"])
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["tier"] is None

    from gateway.keys.application.use_cases import AuthzUseCase
    from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
    from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

    repo = SqlAlchemyApiKeyRepository(db_session)
    use_case = AuthzUseCase(repo, Sha256SecretHasher())
    result = await use_case.execute(raw_key=created.json()["key"])
    assert result.tier == "standard"
    assert result.tier_source == "tenant"

    tenant_row = (
        await db_session.execute(
            text("SELECT default_tier FROM tenants WHERE id = :t"), {"t": api_key["tenant_id"]}
        )
    ).fetchone()
    assert tenant_row is not None and tenant_row[0] == "standard"


# ---------------------------------------------------------------------------
# Scenario — invalid tier value on key/admin surfaces is rejected (R1)
# ---------------------------------------------------------------------------


async def test_invalid_tier_on_create_key_rejected(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    resp = await client.post(
        "/admin/keys", json={"name": "bad-tier", "tier": "gold"}, headers=bearer(api_key["jwt"])
    )
    assert resp.status_code == 422, resp.text


async def test_invalid_tier_on_patch_key_rejected(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    created = await client.post(
        "/admin/keys", json={"name": "patch-bad-tier"}, headers=bearer(api_key["jwt"])
    )
    assert created.status_code == 201
    resp = await client.patch(
        f"/admin/keys/{created.json()['key_id']}",
        json={"tier": "gold"},
        headers=bearer(api_key["jwt"]),
    )
    assert resp.status_code == 422, resp.text


async def test_invalid_default_tier_on_admin_route_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    resp = await client.put(
        "/admin/service-tiers/default-tier",
        json={"default_tier": "vip"},
        headers=bearer(api_key["jwt"]),
    )
    assert resp.status_code == 422, resp.text

    row = (
        await db_session.execute(
            text("SELECT default_tier FROM tenants WHERE id = :t"), {"t": api_key["tenant_id"]}
        )
    ).fetchone()
    assert row is not None and row[0] == "standard", "no row must be changed on rejection"


# ---------------------------------------------------------------------------
# Scenario — invalid markup_pct is rejected (R2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_markup", [-5, "abc"])
async def test_invalid_markup_pct_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str], bad_markup: Any
) -> None:
    resp = await client.put(
        "/admin/service-tiers/priority-markup",
        json={"markup_pct": bad_markup},
        headers=bearer(api_key["jwt"]),
    )
    assert resp.status_code == 422, resp.text

    row = (
        await db_session.execute(
            text("SELECT count(*) FROM tenant_priority_markup_overrides WHERE tenant_id = :t"),
            {"t": api_key["tenant_id"]},
        )
    ).fetchone()
    assert row is not None and int(row[0]) == 0


# ---------------------------------------------------------------------------
# Scenario — non-owner (MEMBER) cannot manage tenant-scoped service tiers (R3)
# ---------------------------------------------------------------------------


async def test_member_cannot_manage_tenant_service_tiers(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str]
) -> None:
    token = _issue_token(app, tenant_id=uuid.UUID(api_key["tenant_id"]), role=Role.MEMBER)

    resp = await client.put(
        "/admin/service-tiers/priority-markup",
        json={"markup_pct": 30},
        headers=bearer(token),
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    created = await client.post(
        "/admin/keys", json={"name": "member-cant-patch"}, headers=bearer(api_key["jwt"])
    )
    patch = await client.patch(
        f"/admin/keys/{created.json()['key_id']}",
        json={"tier": "priority"},
        headers=bearer(token),
    )
    assert_problem(patch, 403, "ERR_AUTH_FORBIDDEN")


async def test_admin_role_can_manage_tenant_service_tiers(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str]
) -> None:
    """KEYS_MANAGE is owner-OR-admin — an ADMIN-role caller must succeed (not 403)."""
    token = _issue_token(app, tenant_id=uuid.UUID(api_key["tenant_id"]), role=Role.ADMIN)

    resp = await client.put(
        "/admin/service-tiers/priority-markup", json={"markup_pct": 30}, headers=bearer(token)
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Scenario — duplicate priority-markup PUT is an idempotent upsert (R6)
# ---------------------------------------------------------------------------


async def test_duplicate_priority_markup_put_idempotent_upsert(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    first = await client.put(
        "/admin/service-tiers/priority-markup",
        json={"markup_pct": 15},
        headers=bearer(api_key["jwt"]),
    )
    assert first.status_code == 200, first.text
    assert Decimal(first.json()["markup_pct"]) == Decimal("15")

    second = await client.put(
        "/admin/service-tiers/priority-markup",
        json={"markup_pct": 18},
        headers=bearer(api_key["jwt"]),
    )
    assert second.status_code == 200, second.text
    assert Decimal(second.json()["markup_pct"]) == Decimal("18")

    row = (
        await db_session.execute(
            text(
                "SELECT count(*), max(markup_pct) FROM tenant_priority_markup_overrides"
                " WHERE tenant_id = :t"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).fetchone()
    assert row is not None
    assert int(row[0]) == 1, "exactly one row must exist for that tenant afterward"
    assert Decimal(str(row[1])) == Decimal("18")


# ---------------------------------------------------------------------------
# Scenario — GET /admin/service-tiers returns the effective (override-or-seed) config
# ---------------------------------------------------------------------------


async def test_get_service_tiers_returns_effective_seed_when_no_override(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    resp = await client.get("/admin/service-tiers", headers=bearer(api_key["jwt"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["default_tier"] == "standard"
    assert Decimal(body["priority_markup_pct"]) == Decimal("25")


async def test_get_service_tiers_reflects_override(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    await client.put(
        "/admin/service-tiers/default-tier",
        json={"default_tier": "priority"},
        headers=bearer(api_key["jwt"]),
    )
    await client.put(
        "/admin/service-tiers/priority-markup",
        json={"markup_pct": 12},
        headers=bearer(api_key["jwt"]),
    )

    resp = await client.get("/admin/service-tiers", headers=bearer(api_key["jwt"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["default_tier"] == "priority"
    assert Decimal(body["priority_markup_pct"]) == Decimal("12")
