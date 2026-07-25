"""Characterization of the MCP tool-result relay decision — pinned BEFORE a refactor.

`lint-type-debt-sweep` must clear a pyright `reportPossiblyUnboundVariable` on
`candidate_body` in `mcp_connector/application/use_cases.py`. That variable is
bound only inside `if not block_relay:`, and read only where `block_relay` is
False — provably safe by hand, but pyright cannot narrow it, and the code sits
on the PII-mask fail-closed path whose own comments record a prior confirmed
security HARD-STOP.

The risk of the fix is not that it fails to compile. It is that a restructure
(a default value, an early initialisation, a `# type: ignore`) silently turns a
BLOCK into a RELAY. These tests pin both arms of that decision so the narrowing
has to preserve them:

* blocked  -> the caller gets ERR_MCP_TOOL_RESULT_BLOCKED and NO upstream body;
* allowed  -> the caller gets the upstream body, masked where the guardrail
              asked for masking.

They are deliberately about the OBSERVABLE relay decision, not about the shape
of the code, so they stay valid whichever way the narrowing is written.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.mcp_connector.test_call_flow import (
    ALLOWED_URL,
    MCP_CALL,
    McpDialResult,
    StubMcpDialer,
    bearer,
    jsonrpc_tool_call,
    set_tenant_guardrails,
    set_tenant_mcp_servers,
)

async def test_blocked_relay_returns_error_and_no_upstream_body(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    app: object,
    db_session: AsyncSession,
) -> None:
    """When the guardrail blocks, NOTHING from upstream may reach the caller.

    This is the arm the `candidate_body` narrowing must not weaken: the blocked
    path is exactly the path on which `candidate_body` is never bound, so a
    "fix" that initialises it to a default and relays it would turn this test
    from green to a leak.
    """
    await set_tenant_mcp_servers(
        db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}]
    )
    await set_tenant_guardrails(
        db_session, owner["tenant_id"], {"pii_mask": {"enabled": True, "mode": "mask"}}
    )
    secret = "jane.doe@example.com"
    app.state.mcp_dialer = StubMcpDialer(  # type: ignore[attr-defined]
        result=McpDialResult(
            status=200,
            body={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [
                        {
                            "type": "resource",
                            "resource": {
                                "uri": f"file:///exports/{secret}/report.pdf",
                                "mimeType": "text/plain",
                            },
                        }
                    ]
                },
            },
        )
    )

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(owner["key"]),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert secret not in resp.text, "upstream PII reached the caller on the BLOCKED arm"
    assert "error" in body, "a blocked relay must not return a result"
    assert body["error"]["message"] == "ERR_MCP_TOOL_RESULT_BLOCKED"
    assert "result" not in body, "blocked relay leaked an upstream result field"


async def test_allowed_relay_returns_the_upstream_body(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    app: object,
    db_session: AsyncSession,
) -> None:
    """When nothing is blocked, the upstream body IS relayed.

    The complement of the test above: a narrowing that fails closed too
    eagerly — raising, or blocking whenever `candidate_body` looks unset —
    would break the normal path instead. Both arms have to survive.
    """
    await set_tenant_mcp_servers(
        db_session, owner["tenant_id"], [{"url": ALLOWED_URL, "label": "x"}]
    )
    await set_tenant_guardrails(
        db_session, owner["tenant_id"], {"pii_mask": {"enabled": True, "mode": "mask"}}
    )
    marker = "Quarterly summary, nothing sensitive."
    app.state.mcp_dialer = StubMcpDialer(  # type: ignore[attr-defined]
        result=McpDialResult(
            status=200,
            body={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": marker}]},
            },
        )
    )

    resp = await client.post(
        MCP_CALL,
        json={"server_url": ALLOWED_URL, "message": jsonrpc_tool_call()},
        headers=bearer(owner["key"]),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "error" not in body, f"clean payload was blocked: {body}"
    assert marker in resp.text, "the clean upstream body was not relayed"
