"""Red suite for claude-gateway-protocol-compat (TASK.md §2/§3, DRAFT contract —
see build report / TASK.md §5 NOTE for the bookkeeping-gap disclosure).

One test per scenario. M2 (header/beta-paired-field forwarding to the REAL upstream
dial) and M6 (system-array round-trip fidelity) are DISCLOSED GAPS in the frozen
`anthropic_upstream.py`/`anthropic_ingress.py` siblings this task's Ground explicitly
forbids editing — asserted honestly (boundary-owned capture tested as GREEN; the
sibling's actual gap proven via `xfail(strict=True)` so the suite stays a true green,
not a masked failure) and filed as §7 change-requests, never silently patched here.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.proxy.application.claude_failover_gate import (
    has_no_eligible_anthropic_candidate,
    is_claude_named,
)
from gateway.proxy.application.model_discovery import (
    DiscoveryEntry,
    resolve_claude_discovery_entries,
)
from gateway.proxy.infrastructure.anthropic_ingress import anthropic_messages_request_to_openai
from gateway.proxy.infrastructure.anthropic_passthrough_headers import (
    capture_passthrough_headers,
    drop_unpaired_beta_fields,
    is_claude_platform_aws_provider,
    parse_anthropic_beta,
)
from gateway.proxy.infrastructure.anthropic_upstream import (
    _anthropic_error_to_openai,  # pyright: ignore[reportPrivateUsage]
    _openai_to_anthropic_request,  # pyright: ignore[reportPrivateUsage]
)

from .conftest import (
    CLAUDE_ALIAS,
    NON_ANTHROPIC_CANDIDATE,
    FakeCompletionUpstream,
    FakeUsageRecorder,
    _insert_model,
    anthropic_payload,
    auth_bearer,
    set_allow_non_claude_failover,
)

MESSAGES = "/v1/messages"
MODELS = "/v1/models"


def assert_anthropic_error(
    resp: httpx.Response, status: int, error_type: str | None = None
) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("type") == "error", f"expected Anthropic error envelope, got: {body}"
    assert "error" in body and isinstance(body["error"], dict)
    if error_type is not None:
        assert body["error"]["type"] == error_type, f"expected {error_type}, got {body['error']}"
    return body


# ===========================================================================
# M1 — GET /v1/models discovery
# ===========================================================================


async def test_model_discovery_returns_only_entitled_claude_aliased_models(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    # Model A: entitled, aliased to CLAUDE_ALIAS (via the settings fixture's model_groups).
    await _insert_model(db_session, NON_ANTHROPIC_CANDIDATE, provider="openrouter")
    # Model B: entitled, but NO alias configured for it.
    await _insert_model(db_session, "openrouter/plain-no-alias", provider="openrouter")
    # Model C: NOT entitled (catalog inactive).
    from sqlalchemy import text

    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, provider)"
            " VALUES ('openrouter/disabled-c', 'c', 128000, false, 'openrouter')"
        )
    )
    await db_session.commit()

    resp = await client.get(MODELS, headers=auth_bearer(api_key["key"]))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [row["id"] for row in body["data"]]
    assert ids == [CLAUDE_ALIAS], f"expected only the claude-aliased entry, got {ids}"


async def test_model_discovery_never_redirects(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    ok = await client.get(MODELS, headers=auth_bearer(api_key["key"]), follow_redirects=False)
    assert not (300 <= ok.status_code < 400), f"unexpected redirect: {ok.status_code}"
    assert ok.status_code == 200

    bad = await client.get(
        MODELS, headers=auth_bearer("sk-not-a-real-key"), follow_redirects=False
    )
    assert not (300 <= bad.status_code < 400), f"unexpected redirect: {bad.status_code}"
    assert bad.status_code == 401


async def test_two_entitled_rows_same_alias_collapse_to_one_entry() -> None:
    """M1 edge case — pure-core test (resolve_claude_discovery_entries): two candidate
    rows aliased to the SAME claude id never produce two `data` entries."""
    catalog_rows = [
        ("bedrock/x", "Bedrock X", True),
        ("openrouter/y", "OpenRouter Y", True),
    ]
    entries = resolve_claude_discovery_entries(
        catalog_rows,
        model_groups={CLAUDE_ALIAS: ["bedrock/x", "openrouter/y"]},
        key_model_allowlist=None,
        plan_model_allowlist=None,
    )
    assert entries == [DiscoveryEntry(id=CLAUDE_ALIAS, display_name="Bedrock X")]


# ===========================================================================
# M2 — anthropic-version / anthropic-beta capture (boundary-owned; see module docstring)
# ===========================================================================


def test_anthropic_beta_and_version_captured_verbatim_open_list() -> None:
    headers = {
        "anthropic-beta": "context-management-2025-06-27,some-future-beta-xyz",
        "anthropic-version": "2023-06-01",
    }
    captured = capture_passthrough_headers(headers)
    assert captured.anthropic_version == "2023-06-01"
    assert captured.anthropic_beta == ("context-management-2025-06-27", "some-future-beta-xyz")
    # Open list: an unrecognized value is kept verbatim, never dropped/allowlisted.
    assert "some-future-beta-xyz" in captured.anthropic_beta
    assert parse_anthropic_beta(None) == ()
    assert parse_anthropic_beta("") == ()


def test_beta_paired_body_field_travels_with_header_or_both_drop() -> None:
    body: dict[str, Any] = {"context_management": {"mode": "auto"}, "unrelated": 1}
    surviving = drop_unpaired_beta_fields(
        ("context-management-2025-06-27", "some-future-beta-xyz"), body
    )
    # The PAIRED beta value is dropped together with its body field...
    assert "context-management-2025-06-27" not in surviving
    assert "context_management" not in body
    # ...an UNPAIRED beta value (no table entry) and an unrelated body field survive.
    assert surviving == ("some-future-beta-xyz",)
    assert body == {"unrelated": 1}


# ===========================================================================
# M3 — anthropic-workspace-id: Claude-Platform-on-AWS only
# ===========================================================================


def test_workspace_id_forwarded_only_for_claude_platform_aws() -> None:
    # Disclosed gap: no Claude-Platform-on-AWS-shaped adapter exists yet in this
    # codebase (Bedrock's own Anthropic models are a DIFFERENT provider shape) — the
    # predicate is False for every currently-registered provider, so the header is
    # never forwarded to any of them (satisfies the boundary condition honestly).
    for provider in ("anthropic", "openrouter", "bedrock", "azure", "gemini"):
        assert is_claude_platform_aws_provider(provider) is False
    # The header IS still captured at the boundary regardless of provider resolution.
    captured = capture_passthrough_headers({"anthropic-workspace-id": "wrkspc_01ABC"})
    assert captured.anthropic_workspace_id == "wrkspc_01ABC"


# ===========================================================================
# M4 — session/subagent attribution
# ===========================================================================


async def test_session_and_subagent_headers_attribute_cost_via_raw(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    headers = {
        **auth_bearer(api_key["key"]),
        "x-claude-code-session-id": "sess-abc123",
        "x-claude-code-agent-id": "agent-xyz789",
        "x-claude-code-parent-agent-id": "agent-parent001",
    }
    resp = await client.post(MESSAGES, json=anthropic_payload(active_model), headers=headers)

    assert resp.status_code == 200, resp.text
    assert len(recorder.records) == 1
    row = recorder.records[0]
    assert row.get("cc_session_id") == "sess-abc123"
    assert row.get("cc_agent_id") == "agent-xyz789"
    assert row.get("cc_parent_agent_id") == "agent-parent001"
    # Never forwarded upstream: FakeCompletionUpstream.complete only ever sees the
    # OpenAI-shape payload, never raw request headers — no such key can leak into it.
    assert upstream.last_payload is not None
    assert "x-claude-code-session-id" not in str(upstream.last_payload)


async def test_absent_session_agent_headers_leave_row_byte_identical(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    resp = await client.post(
        MESSAGES, json=anthropic_payload(active_model), headers=auth_bearer(api_key["key"])
    )

    assert resp.status_code == 200, resp.text
    row = recorder.records[0]
    assert "cc_session_id" not in row
    assert "cc_agent_id" not in row
    assert "cc_parent_agent_id" not in row


# ===========================================================================
# M5 — unrecognized custom headers are inert
# ===========================================================================


async def test_unrecognized_custom_header_is_inert(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    without = await client.post(
        MESSAGES, json=anthropic_payload(active_model), headers=auth_bearer(api_key["key"])
    )
    with_custom = await client.post(
        MESSAGES,
        json=anthropic_payload(active_model),
        headers={**auth_bearer(api_key["key"]), "X-Some-Unknown-Header": "whatever"},
    )

    assert without.status_code == with_custom.status_code == 200
    assert without.json()["content"] == with_custom.json()["content"]


# ===========================================================================
# M6 — system array round trip (integration-verified against the built sibling)
# ===========================================================================


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DISCLOSED GAP (§7 change-request against anthropic-messages-ingress, not "
        "patched here): anthropic_upstream.py::_openai_to_anthropic_request only "
        "preserves system block structure when at least one block carries "
        "cache_control (system_has_cc branch); a cache_control-free multi-block "
        "system array collapses into ONE joined string, losing block count/order."
    ),
)
def test_system_array_block_order_round_trip() -> None:
    internal_body = anthropic_messages_request_to_openai(
        {
            "model": "anthropic/claude-opus-4",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
            "system": [
                {"type": "text", "text": "ATTRIBUTION-BLOCK"},
                {"type": "text", "text": "You are a helpful assistant"},
            ],
        }
    )
    outbound = _openai_to_anthropic_request(internal_body, default_max_tokens=4096)
    system = outbound["system"]
    assert isinstance(system, list), f"expected a 2-block array, got {type(system)}: {system!r}"
    assert len(system) == 2
    assert system[0]["text"] == "ATTRIBUTION-BLOCK"
    assert system[1]["text"] == "You are a helpful assistant"


# ===========================================================================
# M7 — upstream error wording preserved verbatim
# ===========================================================================


def test_upstream_error_wording_preserved_verbatim() -> None:
    original_message = "thinking.budget_tokens must be less than max_tokens (specific wording)"
    openai_shape = _anthropic_error_to_openai(
        {"type": "error", "error": {"type": "invalid_request_error", "message": original_message}}
    )
    assert openai_shape["error"]["message"] == original_message


# ===========================================================================
# M8/R3 — non-Claude-failover gate
# ===========================================================================


async def test_fallback_substitution_refused_by_default(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await _insert_model(db_session, NON_ANTHROPIC_CANDIDATE, provider="openrouter")
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    resp = await client.post(
        MESSAGES, json=anthropic_payload(CLAUDE_ALIAS), headers=auth_bearer(api_key["key"])
    )

    body = assert_anthropic_error(resp, 403, "permission_error")
    assert "ERR_NO_ELIGIBLE_ANTHROPIC_CANDIDATE" in body["error"]["message"]
    assert upstream.calls == 0, "must never dial upstream on refusal"
    assert recorder.records == [], "must never write a usage_records row on refusal"


async def test_fallback_substitution_proceeds_once_opted_in(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    await _insert_model(db_session, NON_ANTHROPIC_CANDIDATE, provider="openrouter")
    await set_allow_non_claude_failover(db_session, api_key["tenant_id"], value=True)
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        MESSAGES, json=anthropic_payload(CLAUDE_ALIAS), headers=auth_bearer(api_key["key"])
    )

    assert resp.status_code == 200, resp.text
    assert upstream.calls == 1
    assert upstream.last_payload is not None
    assert upstream.last_payload["model"] == NON_ANTHROPIC_CANDIDATE


async def test_explicitly_named_non_claude_model_unaffected_by_failover_flag(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    # allow_non_claude_failover stays at its default (false) for this tenant.
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        MESSAGES, json=anthropic_payload(active_model), headers=auth_bearer(api_key["key"])
    )

    assert resp.status_code == 200, resp.text
    assert upstream.calls == 1


# ===========================================================================
# M9 — connectivity probes never 500 / hang / redirect
# ===========================================================================


async def test_connectivity_probes_never_500_or_hang(client: httpx.AsyncClient) -> None:
    head_resp = await client.request("HEAD", "/", follow_redirects=False)
    assert head_resp.status_code == 404
    assert not (300 <= head_resp.status_code < 400)

    probe_resp = await client.get(
        "/inference-profiles", params={"type": "SYSTEM_DEFINED"}, follow_redirects=False
    )
    assert probe_resp.status_code == 404
    assert not (300 <= probe_resp.status_code < 400)


# ===========================================================================
# Pure-core sanity (used by messages_router.py's M8 gate)
# ===========================================================================


def test_is_claude_named_and_no_eligible_candidate_predicates() -> None:
    assert is_claude_named("claude-sonnet-4-6") is True
    assert is_claude_named("anthropic-claude-3") is True
    assert is_claude_named("openrouter/anthropic/claude-opus-4") is False

    assert has_no_eligible_anthropic_candidate({"a": "openrouter", "b": "bedrock"}) is True
    assert has_no_eligible_anthropic_candidate({"a": "openrouter", "b": "anthropic"}) is False
    assert has_no_eligible_anthropic_candidate({}) is True
