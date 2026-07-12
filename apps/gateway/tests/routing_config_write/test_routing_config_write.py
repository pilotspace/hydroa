"""RED suite for routing-config-write (TASK.md §4, contract §3 FROZEN @ v1).

PUT /admin/routing (SUPERADMIN-only after signup-and-routing-authz S1 M7/M9 — supersedes the
2026-06-23 owner/admin always-on authz) validates an incoming routing config through the SAME
Settings/Deployment validators (via merge_routing_config), persists it to the routing_config
singleton, and returns the extended GET shape. GET is additively extended with routing_strategy
+ a per-deployment `deployments` block.

Authz note: the positive write/read tests below mint a SUPERADMIN token (`_superadmin`); the
member/admin/unauthenticated tests assert the deny path. This suite's original owner/admin-allowed
assertions were reconciled to the S1 supersession (owner/admin → 403).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from gateway.tenants.domain.entities import Role

from .conftest import ROUTING, auth, mint_role_token, routing_row_count, signup_tenant


async def _superadmin(app: Any, n: str = "acme") -> str:
    """Mint a SUPERADMIN token — the ONLY role authorized to read/write the operator-wide
    routing config after signup-and-routing-authz S1 (M7/M9 supersession of the 2026-06-23
    'any owner/admin, always-on' decision). routing_config has no tenant_id, and
    require_superadmin only checks the JWT role claim, so no signup/user row is needed."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role=Role.SUPERADMIN,
        email=f"super@{n}.example",
    )
    return str(token)


# ── valid write round-trips ─────────────────────────────────────────────────
async def test_put_valid_persists_and_returns(app: Any, client: httpx.AsyncClient) -> None:
    token = await _superadmin(app)
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


async def test_put_roundtrips_deployment_detail(app: Any, client: httpx.AsyncClient) -> None:
    token = await _superadmin(app)
    body = {
        "model_groups": {
            "gpt": [{"model_id": "a", "weight": 3, "rpm_limit": 60, "tpm_limit": 1000}]
        }
    }

    r = await client.put(ROUTING, json=body, headers=auth(token))
    assert r.status_code == 200, r.text
    dep = r.json()["deployments"]["gpt"]
    assert dep == [{"model_id": "a", "weight": 3, "rpm_limit": 60, "tpm_limit": 1000}]

    g = await client.get(ROUTING, headers=auth(token))
    assert g.json()["deployments"]["gpt"] == [
        {"model_id": "a", "weight": 3, "rpm_limit": 60, "tpm_limit": 1000}
    ]


async def test_get_extended_additive(app: Any, client: httpx.AsyncClient) -> None:
    token = await _superadmin(app)
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
    token = await _superadmin(app)
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
    token = await _superadmin(app)
    bad = await client.put(ROUTING, json={"model_groups": {"gpt": ["a", "a"]}}, headers=auth(token))
    assert bad.status_code == 422, bad.text
    assert "DUPLICATE_DEPLOYMENT" in bad.text
    assert await routing_row_count(app) == 0


# ── authz ───────────────────────────────────────────────────────────────────
async def test_put_member_forbidden(app: Any, client: httpx.AsyncClient, db_session: Any) -> None:
    _, tenant_id = await signup_tenant(client, tenant_name="acme", email="owner@acme.example")
    member = await mint_role_token(
        app, db_session, tenant_id=tenant_id, role=Role.MEMBER, email="member@acme.example"
    )
    r = await client.put(ROUTING, json={"model_groups": {"gpt": ["a"]}}, headers=auth(member))
    assert r.status_code == 403
    assert await routing_row_count(app) == 0


async def test_put_unauthenticated(app: Any, client: httpx.AsyncClient) -> None:
    r = await client.put(ROUTING, json={"model_groups": {"gpt": ["a"]}})
    assert r.status_code == 401
    assert await routing_row_count(app) == 0


async def test_put_admin_role_forbidden_superadmin_only(
    app: Any, client: httpx.AsyncClient, db_session: Any
) -> None:
    """ADMIN can NO LONGER write the operator-wide routing config. signup-and-routing-authz
    S1 (M7/M9) supersedes the 2026-06-23 'any owner/admin, always-on' decision — /admin/routing
    is now SUPERADMIN-only. (The full role matrix is covered by tests/signup_routing_authz/.)"""
    _, tenant_id = await signup_tenant(client, tenant_name="acme", email="owner@acme.example")
    admin = await mint_role_token(
        app, db_session, tenant_id=tenant_id, role=Role.ADMIN, email="admin@acme.example"
    )
    r = await client.put(ROUTING, json={"model_groups": {"gpt": ["a"]}}, headers=auth(admin))
    assert r.status_code == 403, r.text
    assert await routing_row_count(app) == 0


async def test_put_strips_unrecognized_keys(app: Any, client: httpx.AsyncClient) -> None:
    """A stray client-supplied key (e.g. a secret) is NOT persisted into the JSONB row."""
    token = await _superadmin(app)
    body = {"model_groups": {"gpt": ["a"]}, "jwt_secret": "should-not-persist", "junk": 1}
    r = await client.put(ROUTING, json=body, headers=auth(token))
    assert r.status_code == 200, r.text

    from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository

    stored = await RoutingConfigRepository(app.state.sessionmaker).get()
    assert stored is not None
    assert "jwt_secret" not in stored
    assert "junk" not in stored
    assert stored["model_groups"] == {"gpt": ["a"]}
