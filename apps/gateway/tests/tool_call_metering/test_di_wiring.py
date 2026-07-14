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
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
