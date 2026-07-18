"""Self-serve plans catalog — behavior red suite (self-serve-plans-catalog TASK.md §2/§4).

Asserts observable behavior (HTTP status + body shape from §3), never internals. RED before
Build: `GET /admin/plans` (plural) is not mounted on `plan_router` yet -> 404, and
`gateway.tenants.application.self_serve_plans` does not exist -> ImportError if referenced
directly. This suite only ever calls the HTTP boundary, so the correct RED signature is a 404.
"""

from __future__ import annotations

import httpx

from .conftest import (
    ADMIN_PLANS,
    bearer,
)


def _ids(body: dict[str, object]) -> list[str]:
    return [p["id"] for p in body["plans"]]  # type: ignore[index]


# ---------------------------------------------------------------------------
# Scenario: Personal tenant sees personal self-serve plans, not business or
# enterprise (M1, M3) — and never its own current plan (I3).
# ---------------------------------------------------------------------------


async def test_personal_tenant_sees_personal_self_serve_plans_price_ascending_never_business_current_or_enterprise(
    client: httpx.AsyncClient,
    catalog: dict[str, str],
    personal_free_owner: dict[str, str],
) -> None:
    who = personal_free_owner
    resp = await client.get(ADMIN_PLANS, headers=bearer(who["jwt"]))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = _ids(body)

    # Exactly starter then pro, price-ascending (M1, M3).
    assert ids == [catalog["starter"], catalog["pro"]]

    # Never the current plan (free, I3), never a business-audience plan (team/team_plus,
    # M3), never enterprise (self_serve=false, I2).
    assert catalog["free"] not in ids
    assert catalog["team"] not in ids
    assert catalog["team_plus"] not in ids
    assert catalog["enterprise"] not in ids

    starter_entry = next(p for p in body["plans"] if p["id"] == catalog["starter"])
    assert starter_entry["name"] == "starter"
    assert starter_entry["display_name"] == "Starter"
    assert starter_entry["base_price_usd_monthly"] == "1.00"


# ---------------------------------------------------------------------------
# Scenario: Business tenant sees business self-serve plans, not personal-only
# (M1, M3) — and never its own current plan (I3) or enterprise (I2).
# ---------------------------------------------------------------------------


async def test_business_tenant_sees_business_self_serve_plans_never_personal_current_or_enterprise(
    client: httpx.AsyncClient,
    catalog: dict[str, str],
    business_team_owner: dict[str, str],
) -> None:
    who = business_team_owner
    resp = await client.get(ADMIN_PLANS, headers=bearer(who["jwt"]))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = _ids(body)

    # Only the OTHER business self-serve tier (team_plus) — team is the current plan.
    assert ids == [catalog["team_plus"]]

    assert catalog["team"] not in ids  # current plan (I3)
    assert catalog["enterprise"] not in ids  # self_serve=false (I2)
    assert catalog["free"] not in ids  # personal-only
    assert catalog["starter"] not in ids  # personal-only
    assert catalog["pro"] not in ids  # personal-only


# ---------------------------------------------------------------------------
# Scenario: Endpoint is tenant-scoped and needs auth (M2, R:unauthorized).
# ---------------------------------------------------------------------------


async def test_unauthenticated_request_returns_401_and_no_plan_data(
    client: httpx.AsyncClient,
    catalog: dict[str, str],
) -> None:
    resp = await client.get(ADMIN_PLANS)

    assert resp.status_code == 401
    assert "plans" not in resp.json()


# ---------------------------------------------------------------------------
# Extra coverage (test_locations): ordering is price-ascending with NULL first.
# `free` (NULL base_price_usd_monthly) must sort BEFORE `starter` (1.00) when
# both are visible (tenant currently on `pro`, so `free` is not excluded as
# the current plan).
# ---------------------------------------------------------------------------


async def test_ordering_is_price_ascending_with_null_first(
    client: httpx.AsyncClient,
    catalog: dict[str, str],
    personal_pro_owner: dict[str, str],
) -> None:
    who = personal_pro_owner
    resp = await client.get(ADMIN_PLANS, headers=bearer(who["jwt"]))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = _ids(body)

    assert ids == [catalog["free"], catalog["starter"]]
    free_entry = next(p for p in body["plans"] if p["id"] == catalog["free"])
    assert free_entry["base_price_usd_monthly"] is None
