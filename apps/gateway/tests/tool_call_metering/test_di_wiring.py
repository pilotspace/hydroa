"""End-to-end RED suite — real DI wiring (TASK.md §5 Strategy step 5, M1).

Exercises POST /v1/mcp/call WITHOUT overriding `app.state.mcp_tool_call_observer` —
proves main.py's real wiring (MeteringToolCallObserver replacing NoopToolCallObserver)
is live, not just unit-tested in isolation. Reuses the sibling mcp-connector-passthrough
suite's own fixtures (owner, StubMcpDialer, jsonrpc_tool_call, set_tenant_mcp_servers)
per this project's established cross-suite fixture-reuse convention (test_call_flow.py
itself imports tests.agent_token_authn_seam.conftest the same way).

RED before BUILD: `gateway.tool_call_metering.infrastructure.observer` does not exist
yet / main.py has no `app.state.mcp_tool_call_observer` wiring yet, so
app.state.mcp_tool_call_observer stays the sibling's NoopToolCallObserver default and
no usage_records row is ever produced — the honest missing-implementation red.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.agent_token_authn_seam.conftest import mint_agent_token
from tests.mcp_connector.conftest import (
    MCP_CALL,
    StubMcpDialer,
    jsonrpc_text_result,
    jsonrpc_tool_call,
    set_tenant_mcp_servers,
)
from tests.tool_call_metering.conftest import (
    MCP_TOOL_CALL_MODEL_ID,
    bearer,
    seed_mcp_tool_call_pricing,
)

# `owner` is intentionally NOT imported — this suite's own conftest.py (same
# directory) already defines it (".io" email domains, no dependency on
# mcp_connector/conftest.py's autouse email-domain fixture, which only applies
# within that conftest's own directory scope); pytest auto-discovers it as a
# fixture without an import.

pytestmark = pytest.mark.asyncio

ALLOWED_URL = "https://mcp.acme.example/v1"


async def test_successful_call_produces_one_usage_records_row_via_real_di_wiring(
    client: httpx.AsyncClient,
    owner: dict[str, str],  # noqa: F811 - fixture shadowing is the pytest convention
    app: Any,
    db_session: AsyncSession,
) -> None:
    await seed_mcp_tool_call_pricing(db_session)
    await set_tenant_mcp_servers(db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}])
    app.state.mcp_dialer = StubMcpDialer(result=jsonrpc_text_result("ok"))  # type: ignore[attr-defined]
    # app.state.mcp_tool_call_observer intentionally LEFT UNTOUCHED — this test proves
    # main.py's real wiring, not a test-injected stub.

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call(name="search")},
        headers=bearer(owner["key"]),
    )
    assert resp.status_code == 200, resp.text

    await asyncio.sleep(0.3)  # fire-and-forget observer call settles
    from gateway.usage.application.flusher import UsageLedgerFlusher

    flusher = UsageLedgerFlusher(redis=app.state.redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    rows = (
        await db_session.execute(
            text(
                "SELECT * FROM usage_records WHERE tenant_id = :tid AND model_id = :m"
            ),
            {"tid": owner["tenant_id"], "m": MCP_TOOL_CALL_MODEL_ID},
        )
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]._mapping  # type: ignore[union-attr]
    assert row["pricing_unit"] == "per_tool_call"
    assert Decimal(str(row["quantity"])) == Decimal("1")
    assert row["tags"] == {"mcp_server": "mcp.acme.example", "mcp_tool": "search"}
    assert row["key_id"] == uuid.UUID(owner["key_id"])


async def test_refused_call_never_produces_a_usage_records_row(
    client: httpx.AsyncClient, owner: dict[str, str], app: Any, db_session: AsyncSession  # noqa: F811
) -> None:
    """Boundary scenario: a refused (unlisted-server) call never reaches
    MeteringToolCallObserver at all (mcp-connector-passthrough's own M9/M11 —
    exercised here against the REAL DI-wired observer to prove no usage_records
    row leaks through from this task's own wiring)."""
    await seed_mcp_tool_call_pricing(db_session)
    # No app.state.mcp_dialer override needed — a refused call never dials.

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(owner["key"]),
    )
    assert resp.status_code == 403

    await asyncio.sleep(0.3)
    from gateway.usage.application.flusher import UsageLedgerFlusher

    flusher = UsageLedgerFlusher(redis=app.state.redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    rows = (
        await db_session.execute(
            text(
                "SELECT * FROM usage_records WHERE tenant_id = :tid AND model_id = :m"
            ),
            {"tid": owner["tenant_id"], "m": MCP_TOOL_CALL_MODEL_ID},
        )
    ).fetchall()
    assert rows == []


# ===========================================================================
# Defect fix (audit-remediation, HIGH): agent-principal MCP metering unfunded.
#
# ToolCallObserver.record() never carried agent_principal_id, so an MCP tool
# call made by an agent-token-authenticated caller attached to an agent
# principal never incremented that principal's `usage:spend:agent_principal:
# {id}:{yyyymm}` Redis counter — the SAME counter
# GovernanceService._check_agent_principal_budget() enforces against and
# GET /admin/agents reads for display (see mcp_connector/test_budget_governance.py's
# `test_agent_principal_budget_exceeded_refused_zero_dials_zero_meter`, which
# seeds that exact key to simulate an at-cap spend). A successful call under
# cap silently never funded it.
#
# This drives POST /v1/mcp/call end to end through the REAL DI-wired
# MeteringToolCallObserver + RecordingUsageRecorder (no test-injected stub,
# same discipline as the two scenarios above), authenticated via a real agent
# OAuth access token attached to an agent principal, and asserts the
# principal's spend counter goes from absent/zero to nonzero.
# ===========================================================================

ADMIN_AGENTS = "/admin/agents"


def _yyyymm() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m")


def _principal_spend_key(principal_id: str) -> str:
    return f"usage:spend:agent_principal:{principal_id}:{_yyyymm()}"


async def _create_agent_principal(
    client: httpx.AsyncClient, jwt: str, *, name: str, monthly_budget_usd: str
) -> dict[str, Any]:
    resp = await client.post(
        ADMIN_AGENTS,
        json={"name": name, "monthly_budget_usd": monthly_budget_usd},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, f"create_agent_principal failed: {resp.text}"
    return resp.json()


async def _attach_token(
    client: httpx.AsyncClient, jwt: str, *, principal_id: str, token_id: str
) -> None:
    resp = await client.post(
        f"{ADMIN_AGENTS}/{principal_id}/tokens/{token_id}/attach",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 200, f"attach_token failed: {resp.text}"


async def _real_user_id(db_session: AsyncSession, tenant_id: str) -> uuid.UUID:
    row = (
        await db_session.execute(
            text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tenant_id}
        )
    ).first()
    assert row is not None
    return row[0]


async def test_successful_agent_principal_call_funds_agent_spend_counter(
    client: httpx.AsyncClient,
    owner: dict[str, str],  # noqa: F811 - fixture shadowing is the pytest convention
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """RED-before-fix: ToolCallObserver.record() has no agent_principal_id parameter,
    the mcp_connector execute() call site never passes authz.agent_principal_id, so
    this counter stays absent forever even though the call succeeds and bills the
    tenant/key counters. Asserts it goes from absent (None) to a positive Decimal."""
    await seed_mcp_tool_call_pricing(db_session)
    await set_tenant_mcp_servers(db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}])
    app.state.mcp_dialer = StubMcpDialer(result=jsonrpc_text_result("ok"))  # type: ignore[attr-defined]
    # app.state.mcp_tool_call_observer intentionally LEFT UNTOUCHED — real DI wiring.

    principal = await _create_agent_principal(
        client, owner["jwt"], name="mcp-spend-bot", monthly_budget_usd="1000.00"
    )
    user_id = await _real_user_id(db_session, owner["tenant_id"])
    access_token, token_row = await mint_agent_token(
        app, tenant_id=uuid.UUID(owner["tenant_id"]), user_id=user_id
    )
    await _attach_token(
        client, owner["jwt"], principal_id=principal["id"], token_id=str(token_row.id)
    )

    spend_key = _principal_spend_key(principal["id"])
    assert await redis_client.get(spend_key) is None, "must start absent (never funded)"

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call(name="search")},
        headers=bearer(access_token),
    )
    assert resp.status_code == 200, resp.text

    await asyncio.sleep(0.3)  # fire-and-forget observer call settles
    from gateway.usage.application.flusher import UsageLedgerFlusher

    flusher = UsageLedgerFlusher(redis=app.state.redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    raw = await redis_client.get(spend_key)
    assert raw is not None, (
        "agent-principal spend counter must be funded after a successful MCP tool "
        "call made under that principal's agent token — it was never incremented "
        "because ToolCallObserver.record() dropped agent_principal_id on the floor"
    )
    raw_str = raw.decode() if isinstance(raw, bytes) else raw
    assert Decimal(raw_str) > Decimal("0")
