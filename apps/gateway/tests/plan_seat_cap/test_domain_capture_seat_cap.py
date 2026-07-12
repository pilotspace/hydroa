"""RED suite: seat-cap enforcement at the domain-capture auto-join signup seam
(TASK.md §3 CONTRACT R3/M4, FROZEN @ v1) — `join_verified_tenant_domain`, reached from
`POST /admin/auth/signup`'s verified-domain branch.

RED until `assert_seat_available` is wired as the FIRST statement inside
`join_verified_tenant_domain`'s existing `async with self._session.begin():` block, and
`tenants/api/router.py`'s domain-capture branch translates SeatCapExceededError ->
ERR_PLAN_SEAT_CAP_EXCEEDED (403).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    active_user_count,
    assert_problem,
    assign_plan,
    bearer,
    seed_extra_active_users,
    seed_plan,
    signup_owner,
    user_exists,
)

pytestmark = pytest.mark.asyncio


class _FakeDnsResolver:
    """Minimal local double — mirrors tests/domain_capture/conftest.py's own
    FakeDnsResolver (not imported cross-suite to keep this suite self-contained)."""

    def __init__(self) -> None:
        self._records: dict[str, list[str]] = {}

    def set_record(self, name: str, token: str) -> None:
        self._records[name] = [f"ai-proxy-domain-verification={token}"]

    async def lookup_txt(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109
        return list(self._records.get(name, []))


def _record_name(domain: str) -> str:
    return f"_ai-proxy-challenge.{domain}"


@pytest.fixture
def fake_dns(app: Any) -> _FakeDnsResolver:
    resolver = _FakeDnsResolver()
    app.state.dns_resolver = resolver
    return resolver


async def _claim_and_verify_domain(
    client: httpx.AsyncClient, *, owner_token: str, domain: str, fake_dns: _FakeDnsResolver
) -> None:
    create = await client.post(
        "/admin/domain-claims", json={"domain": domain}, headers=bearer(owner_token)
    )
    assert create.status_code == 201, f"setup failed creating domain claim: {create.text}"
    claim_id = create.json()["claim_id"]
    claim_token = create.json()["dns_record_value"].split("=", 1)[1]
    fake_dns.set_record(_record_name(domain), claim_token)

    verify = await client.post(
        f"/admin/domain-claims/{claim_id}/verify", headers=bearer(owner_token)
    )
    assert verify.status_code == 200, f"setup failed verifying domain claim: {verify.text}"


async def test_verified_domain_signup_at_seat_cap_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: _FakeDnsResolver
) -> None:
    """Scenario: 'A verified-domain signup at the tenant's seat cap is rejected' (R3) —
    tenant with a verified domain claim for 'corp.example', plan 'starter' (seat_cap=5),
    already at 5 active users -> a new user signing up with 'new@corp.example' -> 403
    ERR_PLAN_SEAT_CAP_EXCEEDED; no users row inserted; plan_id/seat_cap unchanged."""
    owner = await signup_owner(client, email="owner@corp-seatcap-atcap.example")
    plan_id = await seed_plan(db_session, name="starter-domaincapture-atcap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=4)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5

    await _claim_and_verify_domain(
        client,
        owner_token=owner["owner_token"],
        domain="corp-seatcap-atcap.example",
        fake_dns=fake_dns,
    )

    resp = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ignored on the auto-join branch",
            "email": "new@corp-seatcap-atcap.example",
            "password": "new-hire-horse-battery-01",
        },
    )

    assert_problem(resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")
    assert not await user_exists(db_session, email="new@corp-seatcap-atcap.example")
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5


async def test_brand_new_tenant_signup_never_capped(client: httpx.AsyncClient) -> None:
    """Scenario: 'Brand-new tenant signup (no verified domain match) is never capped'
    (ruled out, M2) — a signup email with NO verified domain claim anywhere creates a
    BRAND-NEW tenant + owner -> 201; the new tenant's plan_id is NULL (plan-catalog M2),
    uncapped by construction; assert_seat_available is never even consulted on this
    branch."""
    resp = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "Never Capped Co",
            "email": "founder@unclaimed-seatcap-domain.example",
            "password": "founder-horse-battery-01",
        },
    )

    assert resp.status_code == 201, resp.text
