"""Red suite for the dashboard playground-token exchange (playground-token-exchange task).

POST /admin/keys/playground-token mints a SHORT-LIVED, spend-capped sk- key that the
dashboard BFF uses SERVER-SIDE to reach the /v1 data plane on behalf of a browser
session (the browser never sees a key). It is the JWT-plane mint that closes the
"playground 401s because the cookie BFF only has a JWT, not an sk- key" gap.

Contract pinned here:
  - Authenticated (any tenant role) → 201 with a one-time sk- secret + expires_at.
  - The minted key is real: it resolves on /internal/authz to the caller's tenant.
  - It is short-lived (expires_at set ~ttl ahead) and spend-capped (monthly_budget_usd).
  - No JWT → 401 (it is a /admin/* JWT-plane route).
"""

import datetime as dt
import re
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PLAYGROUND_TOKEN = "/admin/keys/playground-token"
INTERNAL_AUTHZ = "/internal/authz"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
_KEY_RE = re.compile(r"^sk-([0-9a-f]+)\.([A-Za-z0-9_\-]+)$")


async def _signup_and_login(client: httpx.AsyncClient, *, tenant: str, email: str) -> str:
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant, "email": email, "password": "correct horse batt"}
    )
    assert sr.status_code == 201, sr.text
    lr = await client.post(LOGIN, json={"email": email, "password": "correct horse batt"})
    assert lr.status_code == 200, lr.text
    return lr.json()["access_token"]


async def test_owner_mints_playground_token(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """An authenticated owner gets a one-time sk- secret + expires_at, and a real DB row."""
    token = await _signup_and_login(client, tenant="Pg", email="owner@pg.io")

    resp = await client.post(
        PLAYGROUND_TOKEN, headers={"Authorization": f"Bearer {token}"}, json={}
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert _KEY_RE.match(body["key"]), f"not an sk- key: {body['key'][:12]}…"
    assert body["expires_at"], "playground token must carry an expiry"

    # The row exists, is short-lived (expires_at set), capped, and named for the user.
    row = (
        await db_session.execute(
            text(
                "SELECT name, expires_at, monthly_budget_usd FROM api_keys "
                "WHERE name LIKE 'playground:%' ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).one()
    assert row.name.startswith("playground:")
    assert row.expires_at is not None
    assert row.monthly_budget_usd is not None and float(row.monthly_budget_usd) > 0


async def test_playground_token_resolves_on_data_plane(client: httpx.AsyncClient) -> None:
    """The minted key authenticates on /internal/authz (i.e. it works on the /v1 plane)."""
    token = await _signup_and_login(client, tenant="Pg2", email="owner@pg2.io")
    minted = await client.post(
        PLAYGROUND_TOKEN, headers={"Authorization": f"Bearer {token}"}, json={}
    )
    key = minted.json()["key"]

    authz = await client.post(INTERNAL_AUTHZ, headers={"Authorization": f"Bearer {key}"})
    assert authz.status_code == 200, authz.text
    assert authz.json().get("tenant_id"), "authz must resolve the minted key to a tenant"


async def test_any_role_can_mint_playground_token(client: httpx.AsyncClient, app: Any) -> None:
    """A viewer (non owner/admin) may mint — the playground is a core member feature.

    Unlike POST /admin/keys (owner/admin only), the playground mint is gated on
    authentication alone; risk is bounded by the short TTL + per-key cap.
    """
    from gateway.tenants.domain.entities import Role

    signup = await client.post(
        SIGNUP,
        json={"tenant_name": "PgV", "email": "viewer@pgv.io", "password": "correct horse batt"},
    )
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenant_id"]
    viewer_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.VIEWER,
        email="viewer@pgv.io",
    )

    resp = await client.post(
        PLAYGROUND_TOKEN, headers={"Authorization": f"Bearer {viewer_token}"}, json={}
    )
    assert resp.status_code == 201, resp.text
    assert _KEY_RE.match(resp.json()["key"])


async def test_playground_token_requires_jwt(client: httpx.AsyncClient) -> None:
    """No JWT → 401 (this is a /admin/* JWT-plane route; never mint anonymously)."""
    resp = await client.post(PLAYGROUND_TOKEN, json={})
    assert resp.status_code == 401, resp.text


async def test_playground_token_is_short_lived(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """expires_at is in the near future (a short TTL), not a long-lived key."""
    token = await _signup_and_login(client, tenant="Pg3", email="owner@pg3.io")
    await client.post(PLAYGROUND_TOKEN, headers={"Authorization": f"Bearer {token}"}, json={})

    expires_at = (
        await db_session.execute(
            text(
                "SELECT expires_at FROM api_keys WHERE name LIKE 'playground:%' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).scalar_one()
    now = dt.datetime.now(dt.UTC)
    delta = (
        expires_at.replace(tzinfo=dt.UTC) - now if expires_at.tzinfo is None else expires_at - now
    )
    # default TTL is 1800s; allow generous slack but reject a multi-day lifetime.
    assert dt.timedelta(seconds=0) < delta <= dt.timedelta(hours=2), f"TTL out of range: {delta}"
