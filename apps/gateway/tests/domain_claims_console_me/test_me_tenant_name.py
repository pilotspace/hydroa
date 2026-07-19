"""RED test for the domain-claims-console CR (2026-07-19): GET /admin/auth/me must
carry the caller's own tenant display name so the dashboard joined-workspace callout
can name the workspace from session context (Tin chose "add tenant_name to /me" over
a dashboard-only generic-copy fallback).

Asserts OBSERVABLE behavior only: the /me response body. RED now because MeResponse
has no tenant_name field yet (the handler returns user_id/tenant_id/email/role).
Real Postgres; httpx.ASGITransport — see conftest.py.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import ME, bearer, signup_and_login

pytestmark = pytest.mark.asyncio


async def test_me_returns_tenant_name(client: httpx.AsyncClient) -> None:
    # covers: M6 (gateway half) — /me exposes the caller's OWN tenant display name.
    tenant_id, token = await signup_and_login(
        client, tenant_name="Acme, Inc.", email="owner@acme-me.com"
    )

    resp = await client.get(ME, headers=bearer(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # the new additive field — the whole point of the CR
    assert body["tenant_name"] == "Acme, Inc.", body
    # existing frozen fields remain intact (additive-only change)
    assert body["tenant_id"] == str(tenant_id)
    assert body["email"] == "owner@acme-me.com"
    assert body["role"] in {"owner", "OWNER", "Role.OWNER"}
    assert "user_id" in body
