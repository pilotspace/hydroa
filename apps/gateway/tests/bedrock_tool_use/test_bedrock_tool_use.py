"""Red suite for bedrock-tool-use (v20 task 4/4) — frozen contract.

v25 task-3 amendment: _make_adapter drops credentials=/region= ctor args.
Credentials travel via BedrockCredential in the contextvar. BT7 (the async
end-to-end test) wraps the complete() call with set/reset_provider_credential.

Asserts the Bedrock Converse tool-use translation seam: OpenAI tools/tool_choice/
tool_calls/role:"tool" map to/from the Bedrock Converse toolConfig / toolUse /
toolResult shapes (request, response, end-to-end) — using the FROZEN contract
from the execution context.

Contract shapes (botocore bedrock-runtime service model, FROZEN @ v20 task 4):
  Request toolConfig:
    {"tools":[{"toolSpec":{"name":..., "description":...,
                            "inputSchema":{"json":<OpenAI parameters schema>}}}],
     "toolChoice": <choice>}
  toolChoice mapping:
    "auto"    -> {"auto":{}}
    "required"-> {"any":{}}
    {"type":"function","function":{"name":"x"}} -> {"tool":{"name":"x"}}
    "none"    -> toolConfig has NO toolChoice key (omitted)
  toolConfig OMITTED entirely when payload has no "tools".
  Assistant tool_calls -> content block:
    {"toolUse":{"toolUseId":<id>, "name":<name>, "input":<DICT>}}
  role:"tool" messages -> user message with toolResult blocks;
    consecutive tool messages COLLAPSE into ONE user message.
  Response toolUse block -> OpenAI tool_calls with arguments as JSON STRING;
    message.content is None when only tool_calls.
  Mixed text+toolUse -> message.content == the text, plus tool_calls.
  No-tools path BYTE-IDENTICAL to task 2 (no toolConfig key in body).

These helper imports are RED until BUILD adds them to bedrock_upstream.py:
  _tools_to_converse, _tool_choice_to_converse, _assistant_tool_calls_to_content

Async tests (BT7) are marked via the module-level pytestmark; pure helper tests
(BT1-BT6, BT8) are synchronous def — no asyncio marks applied to them.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

# ── Stable imports (already implemented) ─────────────────────────────────────
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BedrockCredential
from gateway.proxy.domain.tool_translation import dump_tool_arguments, load_tool_arguments
from gateway.proxy.infrastructure.bedrock_sigv4 import AwsCredentials

# RED until BUILD creates the new helper symbols in bedrock_upstream.
from gateway.proxy.infrastructure.bedrock_upstream import (
    BedrockCompletionUpstream,
    _assistant_tool_calls_to_content,  # NEW — RED
    _converse_to_openai,
    _map_finish_reason,
    _openai_to_converse_request,
    _tool_choice_to_converse,  # NEW — RED
    _tools_to_converse,  # NEW — RED
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures / constants — mirroring tool_translation and bedrock_provider suites
# ---------------------------------------------------------------------------

_DUMMY_CREDS = AwsCredentials(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)

# v25 task-3: BedrockCredential travels via contextvar, not ctor args.
_DUMMY_CRED = BedrockCredential(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)

_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"

# Canonical OpenAI-shaped tool list (mirrors tool_translation fixture _TOOLS).
_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]

# Two-tool list for collapse tests (BT4).
_TOOLS_TWO: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get current time",
            "parameters": {"type": "object", "properties": {"timezone": {"type": "string"}}},
        },
    },
]

# Canonical tool-conversation messages (mirrors tool_translation _TOOL_MESSAGES style).
_TOOL_MESSAGES_SINGLE: list[dict[str, Any]] = [
    {"role": "user", "content": "weather in Paris?"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "tu_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "tu_1", "content": "sunny, 21C"},
]

_TOOL_MESSAGES_TWO: list[dict[str, Any]] = [
    {"role": "user", "content": "weather and time?"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "tu_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
            },
            {
                "id": "tu_2",
                "type": "function",
                "function": {"name": "get_time", "arguments": '{"timezone": "UTC"}'},
            },
        ],
    },
    {"role": "tool", "tool_call_id": "tu_1", "content": "sunny, 21C"},
    {"role": "tool", "tool_call_id": "tu_2", "content": "14:32 UTC"},
]


def _make_adapter(
    handler: object,
    *,
    endpoint_url: str = "https://bedrock-runtime.us-east-1.amazonaws.com",
    max_retries: int = 0,
) -> BedrockCompletionUpstream:
    """Construct the adapter (no ctor creds/region — task-3), swap _client.

    v25 task-3: credentials travel via BedrockCredential in the contextvar,
    NOT as ctor arguments. The caller must set/reset the contextvar around
    adapter.complete() / adapter.stream() calls.

    RIGHT-REASON RED: existing ctor still requires credentials= and region=
    → TypeError until BUILD removes those args.

    Mirrors the helper from tests/bedrock_provider/test_bedrock_provider.py exactly
    so the BT7 end-to-end test uses the same pattern.
    """
    adapter = BedrockCompletionUpstream(  # type: ignore[call-arg]
        endpoint_url=endpoint_url,
        default_max_tokens=4096,
        max_retries=max_retries,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


# ---------------------------------------------------------------------------
# BT1 — tools list -> toolConfig.tools (toolSpec shape)
# ---------------------------------------------------------------------------


def test_tools_to_toolconfig() -> None:
    """_openai_to_converse_request with tools present emits body["toolConfig"]["tools"]
    as a list of {"toolSpec":{"name":...,"description":...,"inputSchema":{"json":<params>}}}.

    The "inputSchema" wraps the OpenAI "parameters" dict verbatim under a "json" key —
    asserted EXACTLY per the botocore bedrock-runtime service model.
    """
    payload: dict[str, Any] = {
        "model": _MODEL_ID,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": _TOOLS,
    }
    _model_id, body = _openai_to_converse_request(payload, default_max_tokens=4096)

    assert "toolConfig" in body, "toolConfig must be present when tools are supplied"
    tool_specs = body["toolConfig"]["tools"]
    assert isinstance(tool_specs, list), "toolConfig.tools must be a list"
    assert len(tool_specs) == 1

    spec = tool_specs[0]
    assert "toolSpec" in spec, "each entry must be wrapped in a toolSpec key"
    ts = spec["toolSpec"]
    assert ts["name"] == "get_weather"
    assert ts["description"] == "Get weather for a city"
    assert ts["inputSchema"] == {
        "json": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        }
    }, "inputSchema must wrap the OpenAI parameters schema verbatim under a 'json' key"


# ---------------------------------------------------------------------------
# BT2 — tool_choice variants (four mappings + no-tools omission)
# ---------------------------------------------------------------------------


def test_tool_choice_variants() -> None:
    """toolChoice mapping per the frozen contract:
      "auto"    -> {"auto":{}}
      "required"-> {"any":{}}
      {"type":"function","function":{"name":"get_weather"}} -> {"tool":{"name":"get_weather"}}
      "none"    -> toolChoice key OMITTED from toolConfig (but toolConfig still present)
    AND: a no-tools request must have NO "toolConfig" key in the body at all.
    """
    base_payload: dict[str, Any] = {
        "model": _MODEL_ID,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": _TOOLS,
    }

    # "auto" -> {"auto":{}}
    _, body_auto = _openai_to_converse_request(
        {**base_payload, "tool_choice": "auto"}, default_max_tokens=4096
    )
    assert body_auto["toolConfig"]["toolChoice"] == {"auto": {}}, '"auto" must map to {"auto":{}}'

    # "required" -> {"any":{}}
    _, body_required = _openai_to_converse_request(
        {**base_payload, "tool_choice": "required"}, default_max_tokens=4096
    )
    assert body_required["toolConfig"]["toolChoice"] == {"any": {}}, (
        '"required" must map to {"any":{}}'
    )

    # named function -> {"tool":{"name":"get_weather"}}
    named_choice: dict[str, Any] = {
        "type": "function",
        "function": {"name": "get_weather"},
    }
    _, body_named = _openai_to_converse_request(
        {**base_payload, "tool_choice": named_choice}, default_max_tokens=4096
    )
    assert body_named["toolConfig"]["toolChoice"] == {"tool": {"name": "get_weather"}}, (
        'named tool_choice must map to {"tool":{"name":"..."}}'
    )

    # "none" -> toolChoice key OMITTED (toolConfig still present due to tools list)
    _, body_none = _openai_to_converse_request(
        {**base_payload, "tool_choice": "none"}, default_max_tokens=4096
    )
    assert "toolConfig" in body_none, (
        "toolConfig must still be present when tools is non-empty and tool_choice='none'"
    )
    assert "toolChoice" not in body_none["toolConfig"], (
        '"none" tool_choice must OMIT the toolChoice key from toolConfig'
    )

    # no tools at all -> NO "toolConfig" key in body
    _, body_no_tools = _openai_to_converse_request(
        {"model": _MODEL_ID, "messages": [{"role": "user", "content": "hi"}]},
        default_max_tokens=4096,
    )
    assert "toolConfig" not in body_no_tools, (
        "toolConfig must be OMITTED entirely when no tools are supplied"
    )


def test_tool_choice_to_converse_direct() -> None:
    """Unit-test _tool_choice_to_converse directly for the four mappings + None."""
    assert _tool_choice_to_converse("auto") == {"auto": {}}
    assert _tool_choice_to_converse("required") == {"any": {}}
    assert _tool_choice_to_converse({"type": "function", "function": {"name": "get_weather"}}) == {
        "tool": {"name": "get_weather"}
    }
    # "none" -> returns None (caller omits the key)
    assert _tool_choice_to_converse("none") is None
    # explicit None sentinel -> also returns None
    assert _tool_choice_to_converse(None) is None


# ---------------------------------------------------------------------------
# BT3 — assistant tool_calls -> toolUse content block (input is a DICT)
# ---------------------------------------------------------------------------


def test_assistant_tool_calls_to_touse() -> None:
    """An assistant message with tool_calls translates to a Converse messages entry
    whose content is a list of toolUse blocks. The 'input' field is a DICT (not a
    JSON string) — load_tool_arguments("...") is called on the arguments string.
    """
    payload: dict[str, Any] = {
        "model": _MODEL_ID,
        "messages": [
            {"role": "user", "content": "weather in Paris?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "tu_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            },
        ],
    }
    _model_id, body = _openai_to_converse_request(payload, default_max_tokens=4096)

    converse_msgs = body["messages"]
    # Should be: [user_msg, assistant_msg]
    assert len(converse_msgs) == 2

    asst_msg = converse_msgs[1]
    assert asst_msg["role"] == "assistant"
    assert asst_msg["content"] == [
        {
            "toolUse": {
                "toolUseId": "tu_1",
                "name": "get_weather",
                "input": {"city": "Paris"},  # DICT, not a JSON string
            }
        }
    ], (
        "assistant tool_calls must translate to toolUse blocks; "
        "input must be a parsed dict, not a JSON string"
    )


# ---------------------------------------------------------------------------
# BT4 — consecutive role:"tool" messages collapse into ONE user message
# ---------------------------------------------------------------------------


def test_tool_results_collapse() -> None:
    """Two consecutive role:'tool' messages (for tu_1 and tu_2) must collapse into
    a SINGLE user message whose content list has two toolResult blocks, in order.
    Each block shape: {"toolResult":{"toolUseId":<id>,"content":[{"text":<content>}],"status":"success"}}
    """
    payload: dict[str, Any] = {
        "model": _MODEL_ID,
        "messages": _TOOL_MESSAGES_TWO,
        "tools": _TOOLS_TWO,
    }
    _model_id, body = _openai_to_converse_request(payload, default_max_tokens=4096)

    converse_msgs = body["messages"]
    # Expected structure: [user, assistant(toolUse blocks), user(toolResult blocks)]
    # The two tool messages must collapse into the final user message.
    assert len(converse_msgs) == 3, (
        f"Expected 3 Converse messages (user, assistant, collapsed-tool-user), "
        f"got {len(converse_msgs)}"
    )

    collapsed_user = converse_msgs[2]
    assert collapsed_user["role"] == "user", "Collapsed tool-result message must have role 'user'"
    content = collapsed_user["content"]
    assert len(content) == 2, (
        f"Expected 2 toolResult blocks in collapsed user message, got {len(content)}"
    )

    # First toolResult: tu_1 / "sunny, 21C"
    tr1 = content[0]
    assert "toolResult" in tr1
    assert tr1["toolResult"]["toolUseId"] == "tu_1"
    assert tr1["toolResult"]["content"] == [{"text": "sunny, 21C"}]
    assert tr1["toolResult"]["status"] == "success"

    # Second toolResult: tu_2 / "14:32 UTC"
    tr2 = content[1]
    assert "toolResult" in tr2
    assert tr2["toolResult"]["toolUseId"] == "tu_2"
    assert tr2["toolResult"]["content"] == [{"text": "14:32 UTC"}]
    assert tr2["toolResult"]["status"] == "success"


# ---------------------------------------------------------------------------
# BT5 — toolUse response block -> OpenAI tool_calls (arguments is JSON STRING)
# ---------------------------------------------------------------------------


def test_touse_response_to_tool_calls() -> None:
    """_converse_to_openai on a Bedrock Converse 200 response whose output.message.content
    is a single toolUse block and stopReason is "tool_use" must:
      - set choices[0].message.tool_calls == [{"id":"tu_9","type":"function","function":{"name":"get_weather","arguments":'{"city": "Paris"}'}}]
      - set choices[0].message.content to None (no text content)
      - set choices[0].finish_reason to "tool_calls"
      - map usage correctly: inputTokens->prompt_tokens, outputTokens->completion_tokens
    arguments must be a JSON STRING produced by dump_tool_arguments().
    """
    converse_200_tool: dict[str, Any] = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "tu_9",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        }
                    }
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {
            "inputTokens": 5,
            "outputTokens": 7,
            "totalTokens": 12,
        },
    }
    result = _converse_to_openai(converse_200_tool, model_id=_MODEL_ID)

    choice = result["choices"][0]
    msg = choice["message"]

    # tool_calls must be present and correctly shaped
    assert "tool_calls" in msg, "tool_calls key must be present in message"
    tool_calls = msg["tool_calls"]
    assert len(tool_calls) == 1

    tc = tool_calls[0]
    assert tc["id"] == "tu_9"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    # arguments must be a JSON STRING (not a dict)
    args = tc["function"]["arguments"]
    assert isinstance(args, str), f"arguments must be a JSON string, got {type(args)}"
    assert json.loads(args) == {"city": "Paris"}, (
        f"arguments must round-trip to the input dict; got {args!r}"
    )

    # content must be None when only tool_calls
    assert msg["content"] is None, (
        f"message.content must be None when response only contains toolUse blocks; got {msg['content']!r}"
    )

    # finish_reason must be "tool_calls"
    assert choice["finish_reason"] == "tool_calls"

    # usage mapping
    usage = result["usage"]
    assert usage["prompt_tokens"] == 5
    assert usage["completion_tokens"] == 7
    assert usage["total_tokens"] == 12


# ---------------------------------------------------------------------------
# BT6 — mixed text + toolUse response -> content is text AND tool_calls populated
# ---------------------------------------------------------------------------


def test_mixed_text_and_touse() -> None:
    """When output.message.content has both a text block and a toolUse block:
    - message.content must be the concatenated text string
    - message.tool_calls must be populated with the toolUse block
    - finish_reason must be "tool_calls"
    """
    converse_mixed: dict[str, Any] = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Let me check"},
                    {
                        "toolUse": {
                            "toolUseId": "tu_m",
                            "name": "get_weather",
                            "input": {"city": "Rome"},
                        }
                    },
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {
            "inputTokens": 8,
            "outputTokens": 6,
            "totalTokens": 14,
        },
    }
    result = _converse_to_openai(converse_mixed, model_id=_MODEL_ID)

    choice = result["choices"][0]
    msg = choice["message"]

    # Text portion preserved
    assert msg["content"] == "Let me check", (
        f"message.content must be the text string; got {msg['content']!r}"
    )

    # tool_calls portion populated
    assert "tool_calls" in msg, "tool_calls must be present in mixed response"
    assert len(msg["tool_calls"]) == 1
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"

    # finish_reason from tool_use
    assert choice["finish_reason"] == "tool_calls"


# ---------------------------------------------------------------------------
# BT7 — end-to-end: complete() over MockTransport returns tool_calls in body
# ---------------------------------------------------------------------------


async def test_complete_tool_roundtrip() -> None:
    """complete() with a payload that includes tools, end-to-end through MockTransport:
    the adapter calls _openai_to_converse_request (which emits toolConfig),
    sends the request, then maps the toolUse response to OpenAI tool_calls.
    Asserts: (200, body) with body["choices"][0]["message"]["tool_calls"] populated
    and finish_reason == "tool_calls".
    """
    # The Bedrock Converse 200 response body the mock returns
    _converse_tool_200: dict[str, Any] = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "tu_7",
                            "name": "get_weather",
                            "input": {"city": "Berlin"},
                        }
                    }
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {
            "inputTokens": 10,
            "outputTokens": 8,
            "totalTokens": 18,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_converse_tool_200)

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        status, body = await adapter.complete(
            {
                "model": _MODEL_ID,
                "messages": [{"role": "user", "content": "weather in Berlin?"}],
                "tools": _TOOLS,
                "tool_choice": "auto",
            }
        )
    finally:
        reset_provider_credential(tok)

    assert status == 200, f"Expected 200, got {status}: {body}"

    choices = body.get("choices", [])
    assert len(choices) == 1
    choice = choices[0]

    # tool_calls must be present and populated
    msg = choice["message"]
    assert "tool_calls" in msg, (
        "complete() must return tool_calls in the message when the Converse response "
        "contains a toolUse block"
    )
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "tu_7"
    assert tc["function"]["name"] == "get_weather"
    assert isinstance(tc["function"]["arguments"], str), (
        "arguments must be a JSON string in the complete() response"
    )
    assert json.loads(tc["function"]["arguments"]) == {"city": "Berlin"}

    # finish_reason
    assert choice["finish_reason"] == "tool_calls"


# ---------------------------------------------------------------------------
# BT8 — no-tools path unchanged (regression guard)
# ---------------------------------------------------------------------------


def test_plain_text_unchanged() -> None:
    """A no-tools request body must have NO 'toolConfig' key — byte-identical to task 2.
    _converse_to_openai on a plain text Converse 200 (content=[{text:"hi"}], stopReason
    end_turn) must yield message.content=="hi" and NO "tool_calls" key in message.
    This guards against regression from the tool-use additions.
    """
    # Request path: no toolConfig key
    _, body = _openai_to_converse_request(
        {"model": _MODEL_ID, "messages": [{"role": "user", "content": "hello"}]},
        default_max_tokens=4096,
    )
    assert "toolConfig" not in body, (
        "toolConfig must NOT appear in the request body when no tools are supplied"
    )

    # Response path: plain text -> message.content is the text string, no tool_calls
    converse_plain: dict[str, Any] = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "hi"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": 3,
            "outputTokens": 1,
            "totalTokens": 4,
        },
    }
    result = _converse_to_openai(converse_plain, model_id=_MODEL_ID)
    msg = result["choices"][0]["message"]

    assert msg["content"] == "hi", (
        f"Plain-text response must produce message.content=='hi'; got {msg['content']!r}"
    )
    assert "tool_calls" not in msg, (
        "Plain-text response must NOT have a 'tool_calls' key in message — "
        "tool-use additions must not regress the no-tools path"
    )
    assert result["choices"][0]["finish_reason"] == "stop"
