"""Fixtures for the mcp-connector-passthrough red/green suite (TASK.md §4, FROZEN @ v2).

Reuses the project-wide `app`/`client`/`db_session` fixtures (tests/conftest.py) plus
the signup->login->create-key pattern used across sibling suites (residency_policy,
per_key_guardrail_policies).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import email_validator
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.mcp_connector.domain.entities import McpDialResult

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True, scope="session")
def _allow_test_email_domains() -> None:  # type: ignore[return]
    original = email_validator.TEST_ENVIRONMENT
    email_validator.TEST_ENVIRONMENT = True
    yield
    email_validator.TEST_ENVIRONMENT = original


SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
MCP_SERVERS = "/admin/mcp-servers"
MCP_CALL = "/v1/mcp/call"


def key_mcp_servers_path(key_id: str) -> str:
    return f"/admin/keys/{key_id}/mcp-servers"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code {code!r}, got {body.get('code')!r}: {body}"
    return body


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup -> login -> create API key (OWNER role); returns ids + jwt + plaintext key."""
    n = uuid.uuid4().hex[:8]
    email = f"owner-{n}@mcpconn.test"
    signup = await client.post(
        SIGNUP,
        json={"tenant_name": f"McpConnCo-{n}", "email": email, "password": PASSWORD},
    )
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenant_id"]
    login = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    jwt = login.json()["access_token"]
    created = await client.post(ADMIN_KEYS, json={"name": "mcp-ci"}, headers=bearer(jwt))
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": jwt,
        "email": email,
    }


def issue_role_token(app: object, *, tenant_id: str, role_str: str, email: str) -> str:
    from gateway.tenants.domain.entities import Role

    token, _ = app.state.token_service.issue(  # type: ignore[attr-defined]
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=Role(role_str),
        email=email,
    )
    return token


async def create_second_key(client: httpx.AsyncClient, jwt: str, *, name: str = "second") -> dict[str, Any]:
    resp = await client.post(ADMIN_KEYS, json={"name": name}, headers=bearer(jwt))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def revoke_key(client: httpx.AsyncClient, jwt: str, key_id: str) -> None:
    resp = await client.delete(f"/admin/keys/{key_id}", headers=bearer(jwt))
    assert resp.status_code in (200, 204), resp.text


async def set_tenant_mcp_servers(
    db_session: AsyncSession, tenant_id: str, servers: list[dict[str, str]]
) -> None:
    """Raw-SQL patch of a tenant's mcp_allowed_servers (bypasses the PUT router — used
    to arrange pre-conditions the router itself is not under test for in a given case)."""
    await db_session.execute(
        text(
            "UPDATE tenants SET mcp_allowed_servers = :val::jsonb,"
            " mcp_allowed_servers_updated_at = now() WHERE id = :tid"
        ),
        {"val": json.dumps(servers), "tid": tenant_id},
    )
    await db_session.commit()


async def set_key_mcp_override(
    db_session: AsyncSession, key_id: str, servers: list[dict[str, str]] | None
) -> None:
    await db_session.execute(
        text("UPDATE api_keys SET mcp_allowed_servers_override = :val WHERE id = :kid"),
        {"val": json.dumps(servers) if servers is not None else None, "kid": key_id},
    )
    await db_session.commit()


async def set_tenant_guardrails(db_session: AsyncSession, tenant_id: str, configs: dict[str, Any]) -> None:
    await db_session.execute(
        text("UPDATE tenants SET guardrail_configs = :val::jsonb WHERE id = :tid"),
        {"val": json.dumps(configs), "tid": tenant_id},
    )
    await db_session.commit()


async def set_zdr(db_session: AsyncSession, tenant_id: str, enabled: bool) -> None:
    await db_session.execute(
        text("UPDATE tenants SET zdr_enabled = :z WHERE id = :tid"),
        {"z": enabled, "tid": tenant_id},
    )
    await db_session.commit()


class StubMcpDialer:
    """Fake McpDialer — records every dial() call; replays a fixed result or raises
    a fixed exception. Zero real network/DNS/egress-check involvement."""

    def __init__(
        self,
        *,
        result: McpDialResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.result = result or McpDialResult(
            status=200, body={"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
        )
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def dial(
        self,
        *,
        server_url: str,
        message: dict[str, Any],
        upstream_headers: dict[str, str],
        egress_policy: Any,
    ) -> McpDialResult:
        self.calls.append(
            {
                "server_url": server_url,
                "message": message,
                "upstream_headers": upstream_headers,
            }
        )
        if self.exc is not None:
            raise self.exc
        return self.result


class ZeroDialAssertingDialer:
    """A dialer whose `dial()` fails the test immediately if ever invoked — used to
    prove a refusal costs ZERO egress dials (never a mocked-away assertion)."""

    def __init__(self) -> None:
        self.called = False

    async def dial(self, **_kwargs: Any) -> McpDialResult:
        self.called = True
        raise AssertionError("McpDialer.dial() must NEVER be invoked for a refused call")


class StubToolCallObserver:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        call_id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        server_host: str,
        tool_name: str,
        status: str,
        latency_ms: int,
        agent_principal_id: uuid.UUID | None = None,
    ) -> None:
        self.records.append(
            {
                "call_id": call_id,
                "tenant_id": tenant_id,
                "key_id": key_id,
                "server_host": server_host,
                "tool_name": tool_name,
                "status": status,
                "latency_ms": latency_ms,
                "agent_principal_id": agent_principal_id,
            }
        )


def jsonrpc_tool_call(name: str = "search", arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def jsonrpc_text_result(text_value: str, *, msg_id: int = 1) -> McpDialResult:
    return McpDialResult(
        status=200,
        body={
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text_value}]},
        },
    )
