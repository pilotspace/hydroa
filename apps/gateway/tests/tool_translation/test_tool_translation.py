"""Red suite for tool-use-contract (v10 task 1/4) — TASK.md §4.

Freezes the canonical OpenAI<->native tool-translation seam every provider chat
translator imports: the TypedDict vocabulary, a deterministic tool-call id
synthesizer, an OpenAI delta.tool_calls fragment builder, and fail-safe
arguments (de)serialization. Plus regression pins that tools/tool_choice/tool
messages flow through the dispatch seam UNSTRIPPED and a no-tools request stays
byte-identical to v9.

Contract: TASK.md §3 (FROZEN @ v1).
  - synthesize_tool_call_id(name, index) -> "call_{stable8(name)}_{index}";
    raises ValueError("tool_name_required") on empty/whitespace name.
  - build_tool_call_delta(index, *, id, name, arguments_fragment) -> OpenAI
    delta.tool_calls fragment; raises ValueError("tool_call_index_invalid") on index<0.
  - dump_tool_arguments / load_tool_arguments: JSON string <-> object, FAIL-SAFE
    (never raise, never drop).

These imports are RED until BUILD creates gateway/proxy/domain/tool_translation.py.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from gateway.proxy.domain.tool_translation import (
    Tool,
    ToolCall,
    ToolChoice,
    build_tool_call_delta,
    dump_tool_arguments,
    load_tool_arguments,
    synthesize_tool_call_id,
)


# ---------------------------------------------------------------------------
# synthesize_tool_call_id
# ---------------------------------------------------------------------------


def test_synthesize_id_deterministic() -> None:
    a = synthesize_tool_call_id("get_weather", 0)
    b = synthesize_tool_call_id("get_weather", 0)
    assert a == b
    assert a.startswith("call_")
    # the id is built from the name + index only — no secret material
    assert "key" not in a.lower()
    assert "secret" not in a.lower()


def test_synthesize_id_distinct_index() -> None:
    a = synthesize_tool_call_id("get_weather", 0)
    b = synthesize_tool_call_id("get_weather", 1)
    assert a != b


@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_synthesize_id_empty_name_rejected(bad: str) -> None:
    with pytest.raises(ValueError) as exc:
        synthesize_tool_call_id(bad, 0)
    assert "tool_name_required" in str(exc.value)


# ---------------------------------------------------------------------------
# build_tool_call_delta
# ---------------------------------------------------------------------------


def test_first_fragment_has_id_and_name() -> None:
    frag = build_tool_call_delta(0, id="call_x", name="get_weather")
    assert frag == {
        "index": 0,
        "id": "call_x",
        "type": "function",
        "function": {"name": "get_weather"},
    }
    assert "arguments" not in frag["function"]


def test_later_fragment_args_only() -> None:
    frag = build_tool_call_delta(0, arguments_fragment='{"city":')
    assert frag == {
        "index": 0,
        "type": "function",
        "function": {"arguments": '{"city":'},
    }
    assert "id" not in frag
    assert "name" not in frag["function"]


def test_fragment_negative_index_rejected() -> None:
    with pytest.raises(ValueError) as exc:
        build_tool_call_delta(-1, name="x")
    assert "tool_call_index_invalid" in str(exc.value)


# ---------------------------------------------------------------------------
# dump_tool_arguments / load_tool_arguments — fail-safe boundary
# ---------------------------------------------------------------------------


def test_dump_arguments_object_to_json_string() -> None:
    out = dump_tool_arguments({"city": "Paris"})
    assert isinstance(out, str)
    assert json.loads(out) == {"city": "Paris"}
    # round-trips back to the original object
    assert load_tool_arguments(out) == {"city": "Paris"}


def test_dump_arguments_already_string_passthrough() -> None:
    assert dump_tool_arguments('{"x": 1}') == '{"x": 1}'


def test_load_arguments_non_json_string_failsafe() -> None:
    # a non-JSON string is returned verbatim, never raised
    assert load_tool_arguments("not json") == "not json"


def test_dump_arguments_failsafe_never_raises() -> None:
    class _Weird:
        def __str__(self) -> str:
            return "weird"

    out = dump_tool_arguments(_Weird())
    assert isinstance(out, str)  # forwarded as str, no exception


# ---------------------------------------------------------------------------
# Canonical typed shapes exist and validate structurally
# ---------------------------------------------------------------------------


def test_canonical_shapes_typed() -> None:
    tool: Tool = {
        "type": "function",
        "function": {"name": "get_weather", "parameters": {"type": "object"}},
    }
    call: ToolCall = {
        "id": "call_x",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
    }
    choice: ToolChoice = {"type": "function", "function": {"name": "get_weather"}}
    assert tool["type"] == "function"
    assert call["function"]["arguments"] == '{"city": "Paris"}'
    assert choice["function"]["name"] == "get_weather"
    # the literal string choices are also valid ToolChoice values
    for literal in ("auto", "none", "required"):
        c: ToolChoice = literal
        assert c == literal


# ---------------------------------------------------------------------------
# Request passthrough invariant — tools/tool_choice/tool messages UNSTRIPPED
# (regression-pinned through the v9 dispatch seam with a spy adapter)
# ---------------------------------------------------------------------------


class _SpyAdapter:
    """CompletionUpstream spy: records the exact payload it receives."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.complete_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        self.complete_calls.append(payload)
        return 200, {"_served_by": self.name, "model": payload.get("model")}

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        self.stream_calls.append(payload)

        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: [DONE]\n\n"

        return _gen()


class _ScriptedResolver:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    async def provider_for(self, model_id: str) -> str:
        return self._mapping.get(model_id, "openrouter")


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }
]
_TOOL_MESSAGES = [
    {"role": "user", "content": "weather in Paris?"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_x",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_x", "content": "sunny, 21C"},
]

pytestmark = pytest.mark.asyncio


async def test_request_passthrough_tools_unstripped() -> None:
    from gateway.proxy.infrastructure.provider_aware_upstream import (
        ProviderAwareCompletionUpstream,
    )

    spy = _SpyAdapter("anthropic")
    wrapper = ProviderAwareCompletionUpstream(
        adapters={"openrouter": _SpyAdapter("openrouter"), "anthropic": spy},
        resolver=_ScriptedResolver({"claude-x": "anthropic"}),
    )
    body = {
        "model": "claude-x",
        "messages": _TOOL_MESSAGES,
        "tools": _TOOLS,
        "tool_choice": "auto",
    }
    await wrapper.complete(body)
    received = spy.complete_calls[0]
    assert received["tools"] == _TOOLS
    assert received["tool_choice"] == "auto"
    assert received["messages"] == _TOOL_MESSAGES  # tool messages survive unchanged


async def test_openrouter_tool_request_byte_identical() -> None:
    from gateway.proxy.infrastructure.provider_aware_upstream import (
        ProviderAwareCompletionUpstream,
    )

    spy = _SpyAdapter("openrouter")
    wrapper = ProviderAwareCompletionUpstream(
        adapters={"openrouter": spy},
        resolver=_ScriptedResolver({}),  # unknown -> openrouter (default)
    )
    body = {"model": "or-model", "messages": _TOOL_MESSAGES, "tools": _TOOLS}
    await wrapper.complete(body)
    # openrouter adapter receives the body verbatim — no tool translation applied
    assert spy.complete_calls[0] == body


async def test_no_tools_request_byte_identical_v9() -> None:
    from gateway.proxy.infrastructure.provider_aware_upstream import (
        ProviderAwareCompletionUpstream,
    )

    spy = _SpyAdapter("openrouter")
    wrapper = ProviderAwareCompletionUpstream(
        adapters={"openrouter": spy},
        resolver=_ScriptedResolver({}),
    )
    body = {"model": "or-model", "messages": [{"role": "user", "content": "hi"}]}
    await wrapper.complete(body)
    received = spy.complete_calls[0]
    assert "tools" not in received  # zero tool plumbing engaged
    assert received == body
