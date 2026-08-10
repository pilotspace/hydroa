"""Boot-guard (M10) + assign_plan extraction byte-identity (M8/A8) red suite.

- Settings boot validator: selecting stripe with an empty/whitespace key is a boot error;
  the top-up ceiling must be positive.
- The superadmin plan-assign endpoint must behave byte-identically after the assign_plan
  use-case is extracted (both the endpoint and checkout call the SAME shared op).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings

from tests._polling import poll_until

from .conftest import _CATALOG, _seed_catalog, bearer, mint_role_token, signup_owner

TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


def _base_kwargs(**over: Any) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "jwt_secret": TEST_JWT_SECRET,
        "environment": "test",
    }
    kw.update(over)
    return kw


# ---------------------------------------------------------------------------
# M10 — boot guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_key", ["", "   ", "\t\n"])
def test_stripe_empty_key_boot_error(bad_key: str) -> None:
    """Selecting stripe with an empty/whitespace key raises a boot ValueError."""
    with pytest.raises(ValueError, match=r"(?i)stripe"):
        Settings(**_base_kwargs(payment_provider="stripe", payment_stripe_api_key=bad_key))


def test_stripe_with_key_boots() -> None:
    s = Settings(**_base_kwargs(payment_provider="stripe", payment_stripe_api_key="sk_live_xyz"))
    assert s.payment_provider == "stripe"


def test_dev_default_boots_without_stripe_key() -> None:
    s = Settings(**_base_kwargs())
    assert s.payment_provider == "dev"
    assert s.payment_checkout_enabled is True


def test_absent_stripe_key_uses_dev() -> None:
    """An absent stripe key = stripe disabled, dev used (no boot error)."""
    s = Settings(**_base_kwargs(payment_stripe_api_key=""))
    assert s.payment_provider == "dev"


@pytest.mark.parametrize("bad", ["0", "-1", "-10000"])
def test_topup_ceiling_must_be_positive(bad: str) -> None:
    with pytest.raises(ValueError):
        Settings(**_base_kwargs(payment_topup_max_usd=bad))


def test_topup_ceiling_default_is_10000() -> None:
    from decimal import Decimal

    assert Settings(**_base_kwargs()).payment_topup_max_usd == Decimal("10000")


# ---------------------------------------------------------------------------
# M8 / A8 — superadmin plan endpoint unchanged after assign_plan extraction
# ---------------------------------------------------------------------------


async def _platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
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


async def test_superadmin_plan_endpoint_unchanged(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """PUT /admin/platform/tenants/{id}/plan still assigns + audits identically."""
    from gateway.tenants.domain.entities import Role

    catalog = await _seed_catalog(db_session)
    who = await signup_owner(client, tenant_name="SuperTargetCo", email="owner@supertarget.io")

    plat_id = await _platform_tenant_id(db_session)
    super_hdr = bearer(
        mint_role_token(
            app, tenant_id=str(plat_id), role=Role.SUPERADMIN, email="root@platform.internal"
        )
    )

    # team has seat_cap NULL in the seeded catalog; use pro (seat_cap 1) for a concrete cap.
    resp = await client.put(
        f"/admin/platform/tenants/{who['tenant_id']}/plan",
        json={"plan_id": catalog["pro"]},
        headers=super_hdr,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant_id"] == who["tenant_id"]
    assert body["plan"]["id"] == catalog["pro"]
    # seat_cap inherited from the plan (pro seat_cap = 1) per M8.
    assert body["seat_cap"] == _CATALOG["pro"]["seat_cap"]

    # DB reflects it.
    row = (
        await db_session.execute(
            text("SELECT plan_id, seat_cap FROM tenants WHERE id = :t"),
            {"t": who["tenant_id"]},
        )
    ).fetchone()
    assert str(row[0]) == catalog["pro"]
    assert row[1] == _CATALOG["pro"]["seat_cap"]

    # superadmin audit row still written with the assign action. The audit write is
    # fire-and-forget, and this site had NO wait of any kind — it passed because the task
    # usually completes before the next await, and failed under 12-way load. The assertion
    # is `is not None`, so a bounded poll for its arrival is exactly equivalent.
    async def _latest_assign_audit() -> Any:
        return (
            await db_session.execute(
                text(
                    "SELECT action, metadata FROM audit_events"
                    " WHERE action = 'platform.plan.assign' ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).fetchone()

    audit = await poll_until(_latest_assign_audit, lambda r: r is not None)
    assert audit is not None
    assert audit[0] == "platform.plan.assign"
    assert audit[1]["new_plan_id"] == catalog["pro"]
