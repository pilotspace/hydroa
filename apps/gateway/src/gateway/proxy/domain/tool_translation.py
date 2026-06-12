"""Canonical OpenAI<->native tool-translation seam — tool-use-contract TASK.md §3 (FROZEN @ v1).

The frozen vocabulary + pure helpers that EVERY provider chat translator
(OpenRouter passthrough, Anthropic, Gemini) imports to speak the OpenAI
tool-calling shape. No IO; no provider logic here — only the canonical types and
the cross-cutting helpers (id synthesis, streaming-fragment builder, fail-safe
arguments (de)serialization).

Design (v10 milestone): provider is a first-class chat dimension (v9); tools add a
richer request/response shape ON TOP of that seam. The canonical shape is OpenAI's;
each provider maps its native form (Anthropic tool_use/tool_result content blocks;
Gemini functionDeclarations/functionCall/functionResponse) to/from these types.

Security: a synthesized tool-call id is derived from the function NAME + index only
(a non-crypto digest) — it never encodes a key, secret, or tenant identifier.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, NotRequired, TypedDict

# ── Canonical OpenAI tool vocabulary (the frozen shapes) ──────────────────────


class ToolFunction(TypedDict):
    """The `function` block of a request-side tool declaration."""

    name: str
    description: NotRequired[str]
    parameters: NotRequired[dict[str, object]]


class Tool(TypedDict):
    """A request-side tool: `{type:"function", function:{...}}`."""

    type: Literal["function"]
    function: ToolFunction


class _ToolChoiceFunctionName(TypedDict):
    name: str


class ToolChoiceNamed(TypedDict):
    """A request forcing one specific function: `{type:"function", function:{name}}`."""

    type: Literal["function"]
    function: _ToolChoiceFunctionName


# "auto" | "none" | "required" | {type:"function", function:{name}}
ToolChoice = Literal["auto", "none", "required"] | ToolChoiceNamed


class ToolCallFunction(TypedDict):
    """The `function` block of a response-side tool call. `arguments` is a JSON STRING."""

    name: str
    arguments: str


class ToolCall(TypedDict):
    """A response-side tool call: `{id, type:"function", function:{name, arguments}}`."""

    id: str
    type: Literal["function"]
    function: ToolCallFunction


class ToolMessage(TypedDict):
    """A follow-up turn tool result: `{role:"tool", tool_call_id, content}`."""

    role: Literal["tool"]
    tool_call_id: str
    content: str


# ── Helpers (the seam both provider tasks call) ───────────────────────────────


def synthesize_tool_call_id(name: str, index: int) -> str:
    """Return a deterministic, secret-free tool-call id for an id-less native call.

    Providers whose native tool call carries no id (Gemini's ``functionCall``) get a
    stable gateway-owned id so the OpenAI response can carry one and the follow-up
    ``role:"tool"`` message can be correlated back. Same ``(name, index)`` -> same id.

    The id is ``call_{8 hex of blake2b(name)}_{index}`` — derived from the function
    name + index ONLY; it never encodes a key, secret, or tenant id.

    Raises ValueError("tool_name_required") when ``name`` is empty/whitespace — a
    nameless tool call cannot be correlated on the return leg.
    """
    if not name or not name.strip():
        raise ValueError("tool_name_required")
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=4).hexdigest()
    return f"call_{digest}_{index}"


def build_tool_call_delta(
    index: int,
    *,
    id: str | None = None,
    name: str | None = None,
    arguments_fragment: str | None = None,
) -> dict[str, object]:
    """Build one OpenAI ``choices[0].delta.tool_calls`` streaming fragment.

    The FIRST fragment of a tool call carries ``id`` + ``name`` (pass both); later
    fragments carry only an ``arguments`` string chunk (pass ``arguments_fragment``).
    The ``index`` keys the fragment to its tool call across the stream.

    Shape: ``{index, id?, type:"function", function:{name?, arguments?}}`` — keys are
    present only when their value is supplied, matching the OpenAI delta schema.

    Raises ValueError("tool_call_index_invalid") when ``index`` < 0.
    """
    if index < 0:
        raise ValueError("tool_call_index_invalid")
    function: dict[str, object] = {}
    if name is not None:
        function["name"] = name
    if arguments_fragment is not None:
        function["arguments"] = arguments_fragment
    fragment: dict[str, object] = {"index": index, "type": "function", "function": function}
    if id is not None:
        # insert id right after index for a stable, OpenAI-like key order
        fragment = {"index": index, "id": id, "type": "function", "function": function}
    return fragment


def dump_tool_arguments(value: object) -> str:
    """Serialize native tool-call arguments to the OpenAI wire JSON STRING. FAIL-SAFE.

    - dict/list -> ``json.dumps`` (canonical OpenAI form).
    - str -> returned unchanged (already wire-shaped).
    - anything else, or a value json.dumps cannot encode -> ``str(value)`` verbatim.

    Never raises and never drops — a malformed arguments blob must not crash the proxy
    or lose the request.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def load_tool_arguments(value: object) -> object:
    """Parse an OpenAI wire arguments JSON STRING to a native object. FAIL-SAFE.

    - a JSON-parseable str -> the parsed object.
    - a non-JSON str -> returned verbatim (never raises).
    - a non-str value -> returned unchanged (already native).
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


__all__ = [
    "Tool",
    "ToolCall",
    "ToolCallFunction",
    "ToolChoice",
    "ToolChoiceNamed",
    "ToolFunction",
    "ToolMessage",
    "build_tool_call_delta",
    "dump_tool_arguments",
    "load_tool_arguments",
    "synthesize_tool_call_id",
]
