"""RED suite for routing-config-write (TASK.md §4, contract §3 FROZEN @ v1).

PUT /admin/routing (owner/admin, always-on — Tin-approved authz) validates an incoming
routing config through the SAME Settings/Deployment validators (via merge_routing_config),
persists it to the routing_config singleton, and returns the extended GET shape. GET is
additively extended with routing_strategy + a per-deployment `deployments` block.

RED before BUILD: there is no PUT handler on routing_admin_router yet, and GET does not
emit routing_strategy/deployments → these assertions fail (405/KeyError) until Build.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from gateway.tenants.domain.entities import Role

from .conftest import ROUTING, auth, mint_role_token, routing_row_count, signup_tenant


async def _owner(client: httpx.AsyncClient, n: str = "acme") -> str:
    token, _ = await signup_tenant(client, tenant_name=n, email=f"owner@{n}.example")
    return token


# ── valid write round-trips ─────────────────────────────────────────────────
async def test_put_valid_persists_and_returns(
    app: Any, client: httpx.AsyncClient
) -> None:
    token = await _owner(client)
    body = {"model_groups": {"gpt": ["a", "b"]}, "routing_strategy": "simple-shuffle"}

    r = await client.put(ROUTING, json=body, headers=auth(token))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["model_groups"] == {"gpt": ["a", "b"]}
    assert out["routing_strategy"] == "simple-shuffle"
    assert await routing_row_count(app) == 1

    # read-after-write: GET reflects the persisted config
    g = await client.get(ROUTING, headers=auth(token))
    assert g.status_code == 200
    assert g.json()["model_groups"] == {"gpt": ["a", "b"]}
    assert g.json()["routing_strategy"] == "simple-shuffle"


async def test_put_roundtrips_deployment_detail(
    app: Any, client: httpx.AsyncClient
) -> None:
    token = await _owner(client)
    body = {
        "model_groups": {"gpt": [{"model_id": "a", "weight": 3, "rpm_limit": 60, "tpm_limit": 1000}]}
    }

    r = await client.put(ROUTING, json=body, headers=auth(token))
    assert r.status_code == 200, r.text
    dep = r.json()["deployments"]["gpt"]
    assert dep == [{"model_id": "a", "weight": 3, "rpm_limit": 60, "tpm_limit": 1000}]

    g = await client.get(ROUTING, headers=auth(token))
    assert g.json()["deployments"]["gpt"] == [
        {"model_id": "a", "weight": 3, "rpm_limit": 60, "tpm_limit": 1000}
    ]


async def test_get_extended_additive(client: httpx.AsyncClient) -> None:
    token = await _owner(client)
    g = await client.get(ROUTING, headers=auth(token))
    assert g.status_code == 200
    body = g.json()
    # the four FROZEN blocks remain present with their shape
    assert set(body["retry_policy"]) == {"max_retries", "backoff_base_s"}
    assert set(body["cooldown"]) == {"enabled", "threshold", "ttl_s", "window_s"}
    assert "model_groups" in body
    assert "candidates" in body
    # additive extensions
    assert "routing_strategy" in body
    assert "deployments" in body


# ── rejections — nothing persists ───────────────────────────────────────────
async def test_put_unknown_strategy_rejected_nothing_persists(
    app: Any, client: httpx.AsyncClient
) -> None:
    token = await _owner(client)
    # store a valid config C first
    c = {"model_groups": {"gpt": ["a", "b"]}, "routing_strategy": "simple-shuffle"}
    assert (await client.put(ROUTING, json=c, headers=auth(token))).status_code == 200

    bad = await client.put(ROUTING, json={"routing_strategy": "bogus"}, headers=auth(token))
    assert bad.status_code == 422, bad.text
    assert "UNKNOWN_ROUTING_STRATEGY" in bad.text

    # C is unchanged (nothing persisted by the bad PUT)
    g = await client.get(ROUTING, headers=auth(token))
    assert g.json()["routing_strategy"] == "simple-shuffle"
    assert g.json()["model_groups"] == {"gpt": ["a", "b"]}
    assert await routing_row_count(app) == 1


async def test_put_duplicate_deployment_rejected_no_row(
    app: Any, client: httpx.AsyncClient
) -> None:
    token = await _owner(client)
    bad = await client.put(
        ROUTING, json={"model_groups": {"gpt": ["a", "a"]}}, headers=auth(token)
    )
    assert bad.status_code == 422, bad.text
    assert "DUPLICATE_DEPLOYMENT" in bad.text
    assert await routing_row_count(app) == 0


# ── authz ───────────────────────────────────────────────────────────────────
async def test_put_member_forbidden(
    app: Any, client: httpx.AsyncClient, db_session: Any
) -> None:
    _, tenant_id = await signup_tenant(client, tenant_name="acme", email="owner@acme.example")
    member = await mint_role_token(
        app, db_session, tenant_id=tenant_id, role=Role.MEMBER, email="member@acme.example"
    )
    r = await client.put(
        ROUTING, json={"model_groups": {"gpt": ["a"]}}, headers=auth(member)
    )
    assert r.status_code == 403
    assert await routing_row_count(app) == 0


async def test_put_unauthenticated(app: Any, client: httpx.AsyncClient) -> None:
    r = await client.put(ROUTING, json={"model_groups": {"gpt": ["a"]}})
    assert r.status_code == 401
    assert await routing_row_count(app) == 0


async def test_put_admin_role_allowed(
    app: Any, client: httpx.AsyncClient, db_session: Any
) -> None:
    """ADMIN role (not only OWNER) may write (require_owner_or_admin)."""
    _, tenant_id = await signup_tenant(client, tenant_name="acme", email="owner@acme.example")
    admin = await mint_role_token(
        app, db_session, tenant_id=tenant_id, role=Role.ADMIN, email="admin@acme.example"
    )
    r = await client.put(
        ROUTING, json={"model_groups": {"gpt": ["a"]}}, headers=auth(admin)
    )
    assert r.status_code == 200, r.text
    assert await routing_row_count(app) == 1


async def test_put_strips_unrecognized_keys(
    app: Any, client: httpx.AsyncClient
) -> None:
    """A stray client-supplied key (e.g. a secret) is NOT persisted into the JSONB row."""
    token = await _owner(client)
    body = {"model_groups": {"gpt": ["a"]}, "jwt_secret": "should-not-persist", "junk": 1}
    r = await client.put(ROUTING, json=body, headers=auth(token))
    assert r.status_code == 200, r.text

    from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository

    stored = await RoutingConfigRepository(app.state.sessionmaker).get()
    assert stored is not None
    assert "jwt_secret" not in stored
    assert "junk" not in stored
    assert stored["model_groups"] == {"gpt": ["a"]}
