"""mcp-budget-governance CR suite (mcp-connector-passthrough TASK.md §3 -> v3).

Before this CR, POST /v1/mcp/call ran NO budget governance — every MCP tool call
silently bypassed the agent-principal/per-key/tenant/team budget caps chat and
non-chat calls already enforce. One test per breached dimension (the confirmed
budget-integrity hole) + one regression proving an under-cap call still proceeds
and meters normally.

Uses the real app/client/db_session fixtures (tests/conftest.py) — app.state.budget_guard
is already a real RedisBudgetGuard wired against the SAME Redis db this suite's own
redis_client fixture points at (tests/conftest.py's `settings` fixture), so no per-test
budget_guard override is needed (unlike some older sibling suites that predate that
alignment — see tests/conftest.py's `settings` fixture docstring).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env
from tests.agent_token_authn_seam.conftest import mint_agent_token
from tests.mcp_connector.conftest import (
    ADMIN_KEYS,
    MCP_CALL,
    StubMcpDialer,
    StubToolCallObserver,
    ZeroDialAssertingDialer,
    assert_problem,
    bearer,
    jsonrpc_text_result,
    jsonrpc_tool_call,
    set_tenant_mcp_servers,
)

pytestmark = pytest.mark.asyncio

ALLOWED_URL = "https://mcp.acme.example/v1"
ADMIN_TEAMS = "/admin/teams"
ADMIN_AGENTS = "/admin/agents"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> Any:
    """Real redis.asyncio client on the suite's test db; flushed before/after."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def auth_jwt(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _yyyymm() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m")


def _key_spend_key(key_id: str) -> str:
    return f"usage:spend:key:{key_id}:{_yyyymm()}"


def _team_spend_key(team_id: str) -> str:
    return f"usage:spend:team:{team_id}:{_yyyymm()}"


def _tenant_spend_key(tenant_id: str) -> str:
    return f"usage:spend:{tenant_id}:{_yyyymm()}"


def _principal_spend_key(principal_id: str) -> str:
    return f"usage:spend:agent_principal:{principal_id}:{_yyyymm()}"


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
    team_id: str | None = None,
    monthly_budget_usd: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name}
    if team_id is not None:
        payload["team_id"] = team_id
    if monthly_budget_usd is not None:
        payload["monthly_budget_usd"] = monthly_budget_usd
    resp = await client.post(ADMIN_KEYS, json=payload, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed: {resp.text}"
    return resp.json()


async def create_team(client: httpx.AsyncClient, jwt: str, *, name: str) -> dict[str, Any]:
    resp = await client.post(ADMIN_TEAMS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_team failed: {resp.text}"
    return resp.json()


async def set_team_budget(
    client: httpx.AsyncClient, jwt: str, team_id: str, budget: str
) -> None:
    resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}", json={"team_budget_usd": budget}, headers=auth_jwt(jwt)
    )
    assert resp.status_code == 200, f"PATCH team budget failed: {resp.text}"


async def set_tenant_budget(db_session: AsyncSession, tenant_id: str, budget: str) -> None:
    await db_session.execute(
        text("UPDATE tenants SET budget_usd_monthly = :b WHERE id = :tid"),
        {"b": budget, "tid": tenant_id},
    )
    await db_session.commit()


async def create_agent_principal(
    client: httpx.AsyncClient, jwt: str, *, name: str, monthly_budget_usd: str
) -> dict[str, Any]:
    resp = await client.post(
        ADMIN_AGENTS,
        json={"name": name, "monthly_budget_usd": monthly_budget_usd},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, f"create_agent_principal failed: {resp.text}"
    return resp.json()


async def attach_token(
    client: httpx.AsyncClient, jwt: str, *, principal_id: str, token_id: str
) -> None:
    resp = await client.post(
        f"{ADMIN_AGENTS}/{principal_id}/tokens/{token_id}/attach", headers=auth_jwt(jwt)
    )
    assert resp.status_code == 200, f"attach_token failed: {resp.text}"


async def real_user_id(db_session: AsyncSession, tenant_id: str) -> uuid.UUID:
    row = (
        await db_session.execute(
            text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tenant_id}
        )
    ).first()
    assert row is not None
    return row[0]


async def usage_records_count(db_session: AsyncSession) -> int:
    return (
        await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))
    ).scalar()  # type: ignore[return-value]


# ===========================================================================
# Over-cap: per-key budget
# ===========================================================================


async def test_per_key_budget_exceeded_refused_zero_dials_zero_meter(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    app: object,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """Key monthly_budget_usd=1.00, per-key counter=1.00 -> 402, zero dials,
    zero usage_records row, zero tool-call meter. RED-before-GREEN: on current
    (pre-fix) code the budget gate does not exist at all, so this call would
    reach the allow-list check with no server configured -> 403
    ERR_MCP_SERVER_NOT_ALLOWED, never the expected 402 ERR_BUDGET_EXCEEDED —
    proving the suite was failing for the RIGHT reason (missing governance
    gate), not a broken harness."""
    await set_tenant_mcp_servers(db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}])
    key_body = await create_key(
        client, owner["jwt"], name="tight-key", monthly_budget_usd="1.00"
    )
    plaintext_key = key_body["key"]
    key_id = key_body["key_id"]
    await redis_client.set(_key_spend_key(key_id), b"1.00")

    app.state.mcp_dialer = ZeroDialAssertingDialer()  # type: ignore[attr-defined]
    observer = StubToolCallObserver()
    app.state.mcp_tool_call_observer = observer  # type: ignore[attr-defined]
    count_before = await usage_records_count(db_session)

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(plaintext_key),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert observer.records == [], "a budget-refused call must NOT be metered"
    count_after = await usage_records_count(db_session)
    assert count_after == count_before, "a budget-refused call must write zero usage_records rows"


# ===========================================================================
# Over-cap: team budget
# ===========================================================================


async def test_team_budget_exceeded_refused_zero_dials_zero_meter(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    app: object,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """Team budget=10.00 at cap; key has NO per-key budget of its own (so the
    team dimension is the one that trips) -> 402, zero dials, zero meter."""
    await set_tenant_mcp_servers(db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}])
    team = await create_team(client, owner["jwt"], name="wide-team")
    team_id = team["id"]
    await set_team_budget(client, owner["jwt"], team_id, "10.00")
    key_body = await create_key(client, owner["jwt"], name="teamed-key", team_id=team_id)
    plaintext_key = key_body["key"]
    await redis_client.set(_team_spend_key(team_id), b"10.00")

    app.state.mcp_dialer = ZeroDialAssertingDialer()  # type: ignore[attr-defined]
    observer = StubToolCallObserver()
    app.state.mcp_tool_call_observer = observer  # type: ignore[attr-defined]
    count_before = await usage_records_count(db_session)

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(plaintext_key),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert observer.records == []
    count_after = await usage_records_count(db_session)
    assert count_after == count_before


# ===========================================================================
# Over-cap: tenant budget
# ===========================================================================


async def test_tenant_budget_exceeded_refused_zero_dials_zero_meter(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    app: object,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """No per-key/team budget on the key -> falls through to the tenant budget
    (RedisBudgetGuard), at cap -> 402, zero dials, zero meter."""
    await set_tenant_mcp_servers(db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}])
    await set_tenant_budget(db_session, owner["tenant_id"], "5.00")
    await redis_client.set(_tenant_spend_key(owner["tenant_id"]), b"5.00")

    app.state.mcp_dialer = ZeroDialAssertingDialer()  # type: ignore[attr-defined]
    observer = StubToolCallObserver()
    app.state.mcp_tool_call_observer = observer  # type: ignore[attr-defined]
    count_before = await usage_records_count(db_session)

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(owner["key"]),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert observer.records == []
    count_after = await usage_records_count(db_session)
    assert count_after == count_before


# ===========================================================================
# Over-cap: agent-principal aggregate budget
# ===========================================================================


async def test_agent_principal_budget_exceeded_refused_zero_dials_zero_meter(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    app: object,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """An agent-OAuth-token caller (never an sk- key) attached to a principal
    whose aggregate monthly_budget_usd=0.01 is at/over cap -> 402, zero dials,
    zero meter. Proves the gate covers the agent-token auth path too (M14 —
    CompositeKeyAuthenticator's agent-token branch), not just sk- keys."""
    await set_tenant_mcp_servers(db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}])
    principal = await create_agent_principal(
        client, owner["jwt"], name="mcp-spend-bot", monthly_budget_usd="0.01"
    )
    user_id = await real_user_id(db_session, owner["tenant_id"])
    access_token, token_row = await mint_agent_token(
        app, tenant_id=uuid.UUID(owner["tenant_id"]), user_id=user_id
    )
    await attach_token(
        client, owner["jwt"], principal_id=principal["id"], token_id=str(token_row.id)
    )
    await redis_client.set(_principal_spend_key(principal["id"]), b"0.01")

    app.state.mcp_dialer = ZeroDialAssertingDialer()  # type: ignore[attr-defined]
    observer = StubToolCallObserver()
    app.state.mcp_tool_call_observer = observer  # type: ignore[attr-defined]
    count_before = await usage_records_count(db_session)

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(access_token),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert observer.records == []
    count_after = await usage_records_count(db_session)
    assert count_after == count_before


# ===========================================================================
# Under-cap regression: the call proceeds and meters normally
# ===========================================================================


async def test_under_cap_all_dimensions_call_proceeds_and_meters(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    app: object,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """Key/team/tenant all carry a budget but current spend is well UNDER cap
    -> the gate passes, the call reaches the dialer, and the SAME observer
    still records the successful tool call (proves the new gate does not
    regress the existing happy path)."""
    await set_tenant_mcp_servers(db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}])
    team = await create_team(client, owner["jwt"], name="roomy-team")
    team_id = team["id"]
    await set_team_budget(client, owner["jwt"], team_id, "100.00")
    key_body = await create_key(
        client, owner["jwt"], name="roomy-key", team_id=team_id, monthly_budget_usd="50.00"
    )
    plaintext_key = key_body["key"]
    key_id = key_body["key_id"]
    await set_tenant_budget(db_session, owner["tenant_id"], "1000.00")
    await redis_client.set(_key_spend_key(key_id), b"1.00")
    await redis_client.set(_team_spend_key(team_id), b"1.00")
    await redis_client.set(_tenant_spend_key(owner["tenant_id"]), b"1.00")

    dialer = StubMcpDialer(result=jsonrpc_text_result("ok"))
    app.state.mcp_dialer = dialer  # type: ignore[attr-defined]
    observer = StubToolCallObserver()
    app.state.mcp_tool_call_observer = observer  # type: ignore[attr-defined]

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(plaintext_key),
    )

    assert resp.status_code == 200, resp.text
    assert len(dialer.calls) == 1

    import asyncio

    await asyncio.sleep(0.3)
    assert len(observer.records) == 1
    assert observer.records[0]["status"] == "success"
