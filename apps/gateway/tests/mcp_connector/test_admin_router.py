"""HTTP+DB suite for /admin/mcp-servers and /admin/keys/{key_id}/mcp-servers CRUD
(TASK.md §2, FROZEN @ v2): M1, M2, M3, R2, R3, R7, R8 + boundary/duplicate/concurrency
edge cases.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.mcp_connector.conftest import (
    MCP_SERVERS,
    assert_problem,
    bearer,
    create_second_key,
    key_mcp_servers_path,
    revoke_key,
    set_key_mcp_override,
    set_tenant_mcp_servers,
    issue_role_token,
)

pytestmark = pytest.mark.asyncio


# ===========================================================================
# M1: tenant-wide allow-list CRUD
# ===========================================================================


async def test_owner_sets_tenant_allowlist_and_audit_recorded(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    resp = await client.put(
        MCP_SERVERS,
        json={"servers": [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]},
        headers=bearer(owner["jwt"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["servers"] == [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]
    assert body["updated_at"] is not None

    await asyncio.sleep(0.1)
    audit = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM audit_events WHERE tenant_id = :tid"
                " AND action = 'mcp_server_policy.put'"
            ),
            {"tid": owner["tenant_id"]},
        )
    ).scalar()
    assert (audit or 0) >= 1, "a fire-and-forget audit event must be recorded"


async def test_get_default_tenant_allowlist_is_empty(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    resp = await client.get(MCP_SERVERS, headers=bearer(owner["jwt"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["servers"] == []
    assert body["updated_at"] is None


async def test_member_can_read_tenant_allowlist(
    client: httpx.AsyncClient, owner: dict[str, str], app: object, db_session: AsyncSession
) -> None:
    await set_tenant_mcp_servers(
        db_session, owner["tenant_id"], [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]
    )
    token = issue_role_token(
        app, tenant_id=owner["tenant_id"], role_str="member", email="member@mcpconn.test"
    )
    resp = await client.get(MCP_SERVERS, headers=bearer(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["servers"] == [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]


# ===========================================================================
# M3, R2: write-time literal-IP / scheme validation
# ===========================================================================


async def test_put_metadata_ip_literal_rejected(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    resp = await client.put(
        MCP_SERVERS,
        json={"servers": [{"url": "https://169.254.169.254/mcp", "label": "evil"}]},
        headers=bearer(owner["jwt"]),
    )
    assert_problem(resp, 422, "ERR_MCP_SERVER_URL_INVALID")

    row = (
        await db_session.execute(
            text("SELECT mcp_allowed_servers FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).first()
    assert row is not None
    assert row[0] == [], "a rejected PUT must leave the tenant's stored allow-list unchanged"


async def test_put_non_https_scheme_rejected(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    resp = await client.put(
        MCP_SERVERS,
        json={"servers": [{"url": "http://mcp.acme.example/v1", "label": "insecure"}]},
        headers=bearer(owner["jwt"]),
    )
    assert_problem(resp, 422, "ERR_MCP_SERVER_URL_INVALID")


async def test_put_hostname_always_passes_write_time_check(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    """A hostname (not a literal IP) always passes the write-time check — DNS
    resolution is deliberately deferred to dial time."""
    resp = await client.put(
        MCP_SERVERS,
        json={"servers": [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]},
        headers=bearer(owner["jwt"]),
    )
    assert resp.status_code == 200, resp.text


# ===========================================================================
# R3: 50-entry boundary
# ===========================================================================


async def test_put_exactly_50_entries_succeeds(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    servers = [{"url": f"https://mcp{i}.acme.example/v1", "label": f"s{i}"} for i in range(50)]
    resp = await client.put(MCP_SERVERS, json={"servers": servers}, headers=bearer(owner["jwt"]))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["servers"]) == 50


async def test_put_51_entries_rejected(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    servers = [{"url": f"https://mcp{i}.acme.example/v1", "label": f"s{i}"} for i in range(51)]
    resp = await client.put(MCP_SERVERS, json={"servers": servers}, headers=bearer(owner["jwt"]))
    assert_problem(resp, 422, "ERR_MCP_SERVER_LIST_TOO_LONG")


async def test_put_duplicate_urls_stored_without_erroring(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    resp = await client.put(
        MCP_SERVERS,
        json={
            "servers": [
                {"url": "https://mcp.acme.example/v1", "label": "first"},
                {"url": "https://mcp.acme.example/v1", "label": "second"},
            ]
        },
        headers=bearer(owner["jwt"]),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["servers"]) == 2


# ===========================================================================
# R7: non-owner cannot PUT the tenant allow-list
# ===========================================================================


@pytest.mark.parametrize("role", ["admin", "operator", "billing_admin", "viewer", "member"])
async def test_non_owner_cannot_put_tenant_allowlist(
    client: httpx.AsyncClient, owner: dict[str, str], app: object, role: str
) -> None:
    domain_role = role.replace("_", "-")
    token = issue_role_token(
        app, tenant_id=owner["tenant_id"], role_str=role, email=f"{domain_role}@nonowner.test"
    )
    resp = await client.put(
        MCP_SERVERS,
        json={"servers": [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]},
        headers=bearer(token),
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ===========================================================================
# M2: per-key override CRUD
# ===========================================================================


async def test_owner_sets_key_override_tenant_unchanged(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    await set_tenant_mcp_servers(
        db_session, owner["tenant_id"], [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]
    )
    resp = await client.put(
        key_mcp_servers_path(owner["key_id"]),
        json={"servers": [{"url": "https://mcp.narrow.example", "label": "narrow"}]},
        headers=bearer(owner["jwt"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "key"
    assert body["servers"] == [{"url": "https://mcp.narrow.example", "label": "narrow"}]

    tenant_row = (
        await db_session.execute(
            text("SELECT mcp_allowed_servers FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).first()
    assert tenant_row is not None
    assert tenant_row[0] == [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]


async def test_explicit_empty_key_override_denies_everything(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    resp = await client.put(
        key_mcp_servers_path(owner["key_id"]), json={"servers": []}, headers=bearer(owner["jwt"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["servers"] == []
    assert resp.json()["source"] == "key"

    row = (
        await db_session.execute(
            text("SELECT mcp_allowed_servers_override FROM api_keys WHERE id = :kid"),
            {"kid": owner["key_id"]},
        )
    ).first()
    assert row is not None
    assert row[0] == [], "an explicit [] override must be stored as [], never NULL"


async def test_key_with_no_override_reports_source_tenant(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    await set_tenant_mcp_servers(
        db_session, owner["tenant_id"], [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]
    )
    resp = await client.get(key_mcp_servers_path(owner["key_id"]), headers=bearer(owner["jwt"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "tenant"
    assert body["servers"] == [{"url": "https://mcp.acme.example/v1", "label": "Acme"}]


async def test_delete_key_override_reverts_to_tenant_inheritance(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    await set_key_mcp_override(
        db_session, owner["key_id"], [{"url": "https://mcp.narrow.example", "label": "narrow"}]
    )
    resp = await client.delete(key_mcp_servers_path(owner["key_id"]), headers=bearer(owner["jwt"]))
    assert resp.status_code == 204, resp.text

    get_resp = await client.get(key_mcp_servers_path(owner["key_id"]), headers=bearer(owner["jwt"]))
    assert get_resp.json()["source"] == "tenant"


# ===========================================================================
# R7: member cannot set or clear a key override
# ===========================================================================


async def test_member_cannot_put_key_override(
    client: httpx.AsyncClient, owner: dict[str, str], app: object
) -> None:
    token = issue_role_token(
        app, tenant_id=owner["tenant_id"], role_str="member", email="member2@mcpconn.test"
    )
    resp = await client.put(
        key_mcp_servers_path(owner["key_id"]), json={"servers": []}, headers=bearer(token)
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


async def test_member_cannot_delete_key_override(
    client: httpx.AsyncClient, owner: dict[str, str], app: object
) -> None:
    token = issue_role_token(
        app, tenant_id=owner["tenant_id"], role_str="member", email="member3@mcpconn.test"
    )
    resp = await client.delete(key_mcp_servers_path(owner["key_id"]), headers=bearer(token))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ===========================================================================
# R8: unknown / cross-tenant / revoked key_id all collapse to one 404
# ===========================================================================


async def test_unknown_key_id_404(client: httpx.AsyncClient, owner: dict[str, str]) -> None:
    resp = await client.get(key_mcp_servers_path(str(uuid.uuid4())), headers=bearer(owner["jwt"]))
    assert_problem(resp, 404, "ERR_KEY_NOT_FOUND")

    put_resp = await client.put(
        key_mcp_servers_path(str(uuid.uuid4())), json={"servers": []}, headers=bearer(owner["jwt"])
    )
    assert_problem(put_resp, 404, "ERR_KEY_NOT_FOUND")


async def test_cross_tenant_key_id_404(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    n = uuid.uuid4().hex[:8]
    signup2 = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": f"OtherCo-{n}",
            "email": f"other-{n}@mcpconn.test",
            "password": "correct horse battery staple",
        },
    )
    assert signup2.status_code == 201, signup2.text
    login2 = await client.post(
        "/admin/auth/login",
        json={"email": f"other-{n}@mcpconn.test", "password": "correct horse battery staple"},
    )
    other_jwt = login2.json()["access_token"]
    other_key = await create_second_key(client, other_jwt, name="other-tenant-key")

    resp = await client.get(
        key_mcp_servers_path(other_key["key_id"]), headers=bearer(owner["jwt"])
    )
    assert_problem(resp, 404, "ERR_KEY_NOT_FOUND")


async def test_revoked_key_id_404(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    second = await create_second_key(client, owner["jwt"], name="to-revoke")
    await revoke_key(client, owner["jwt"], second["key_id"])

    resp = await client.get(key_mcp_servers_path(second["key_id"]), headers=bearer(owner["jwt"]))
    assert_problem(resp, 404, "ERR_KEY_NOT_FOUND")


# ===========================================================================
# Concurrency edge cases
# ===========================================================================


async def test_concurrent_key_put_never_corrupts_state(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    body_a = {"servers": [{"url": "https://a.example/mcp", "label": "a"}]}
    body_b = {"servers": [{"url": "https://b.example/mcp", "label": "b"}]}

    resp_a, resp_b = await asyncio.gather(
        client.put(
            key_mcp_servers_path(owner["key_id"]), json=body_a, headers=bearer(owner["jwt"])
        ),
        client.put(
            key_mcp_servers_path(owner["key_id"]), json=body_b, headers=bearer(owner["jwt"])
        ),
    )
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text
    assert resp_a.json()["servers"] == body_a["servers"]
    assert resp_b.json()["servers"] == body_b["servers"]

    get_resp = await client.get(key_mcp_servers_path(owner["key_id"]), headers=bearer(owner["jwt"]))
    final = get_resp.json()["servers"]
    assert final in (body_a["servers"], body_b["servers"]), (
        "the final state must equal exactly one submitted list, never a merged/corrupted mix"
    )
