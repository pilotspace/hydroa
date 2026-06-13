"""Canonical OpenAI<->native response_format seam — response-format-contract §3 (FROZEN @ v1).

The frozen vocabulary + pure helpers that EVERY provider chat translator
(OpenRouter passthrough, Anthropic, Gemini) imports to speak the OpenAI
structured-output shape. No IO; no provider logic here — only the canonical types,
the no-op-aware request extractor, and the Anthropic JSON-schema tool-COERCION
strategy (a synthetic forced tool whose parameters ARE the requested schema, plus
the tool_use->content UNWRAP for the return leg).

Design (v11 milestone): response_format is DEPTH on the v9 ChatTranslator seam and
COMPOSES with v10 tools. The canonical shape is OpenAI's; each provider maps it to
native:
  - openrouter/openai : passthrough (already speaks response_format) — nothing here.
  - google (gemini)   : generationConfig.responseMimeType (+responseSchema).
  - anthropic         : NO native field -> json_schema COERCED to one forced tool
                        (build_json_coercion_tool), unwrapped back to message.content.

The MODEL OUTPUT is ALWAYS returned as OpenAI ``message.content`` (a JSON string) —
response_format never introduces a new response field.

Security: no secrets here. The coercion tool name is a fixed gateway-owned constant
and the schema is the caller's own; nothing derived from a key, secret, or tenant id.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal, NotRequired, TypedDict

from gateway.proxy.domain.tool_translation import (
    Tool,
    ToolChoiceNamed,
    ToolFunction,
    dump_tool_arguments,
)

# ── Canonical OpenAI response_format vocabulary (the frozen shapes) ───────────


class JsonSchemaSpec(TypedDict):
    """The `json_schema` block: `{name, schema, strict?}`."""

    name: str
    schema: dict[str, object]
    strict: NotRequired[bool]


class ResponseFormat(TypedDict):
    """`{type:"text"|"json_object"|"json_schema", json_schema?}` — the OpenAI directive."""

    type: Literal["text", "json_object", "json_schema"]
    json_schema: NotRequired[JsonSchemaSpec]


# A single, stable, gateway-owned reserved tool name used for the Anthropic
# json_schema coercion. A caller-supplied tool may not claim it.
JSON_COERCION_TOOL_NAME: Final[str] = "json_output"

_COERCION_TOOL_DESCRIPTION: Final[str] = (
    "Return the response strictly as a JSON object conforming to the provided schema."
)

_SUPPORTED_TYPES: Final[frozenset[str]] = frozenset({"text", "json_object", "json_schema"})


# ── Helpers (the seam both provider tasks call) ───────────────────────────────


def extract_response_format(payload: dict[str, object]) -> ResponseFormat | None:
    """Return the request's ``response_format`` directive, or None for the no-op path.

    Returns None when ``response_format`` is absent OR is ``{type:"text"}`` (the
    default) — the no-op fast path that guarantees a byte-identical v10 request: no
    json plumbing runs.

    Raises:
        ValueError("ERR_UNSUPPORTED_RESPONSE_FORMAT"): ``type`` not in
            {text, json_object, json_schema} — the gateway never silently drops an
            unknown directive.
        ValueError("ERR_INVALID_JSON_SCHEMA"): ``type`` is "json_schema" but no
            ``json_schema.schema`` object is present — the schema is required to
            build the native mapping / coercion tool.
    """
    rf = payload.get("response_format")
    if rf is None:
        return None
    if not isinstance(rf, dict):
        raise ValueError("ERR_UNSUPPORTED_RESPONSE_FORMAT")
    rf_type = rf.get("type")
    if rf_type not in _SUPPORTED_TYPES:
        raise ValueError("ERR_UNSUPPORTED_RESPONSE_FORMAT")
    if rf_type == "text":
        return None  # no-op fast path
    if rf_type == "json_schema":
        json_schema = rf.get("json_schema")
        if not isinstance(json_schema, dict) or not isinstance(json_schema.get("schema"), dict):
            raise ValueError("ERR_INVALID_JSON_SCHEMA")
    return rf  # type: ignore[return-value]  # validated against the canonical shape


def build_json_coercion_tool(
    rf: ResponseFormat,
    *,
    existing_tool_names: Iterable[str] = (),
) -> tuple[Tool, ToolChoiceNamed]:
    """Build the synthetic forced tool that coerces a provider with no native field.

    Only valid for a ``json_schema`` directive. Returns a canonical OpenAI ``Tool``
    named :data:`JSON_COERCION_TOOL_NAME` whose ``parameters`` ARE the requested
    schema, plus a ``ToolChoiceNamed`` forcing that tool. The provider adapter then
    maps this OpenAI tool to its native form via the v10 tool helpers and UNWRAPS the
    returned tool call back into ``message.content`` (see unwrap_coerced_tool_input).

    The coercion tool is ADDED alongside any caller-supplied tools, never replacing
    them (the composition rule).

    Raises:
        ValueError("ERR_RESERVED_TOOL_NAME"): a caller-supplied tool already claims
            the reserved coercion name — the name is gateway-owned.
        ValueError("ERR_INVALID_JSON_SCHEMA"): called for a non-json_schema directive
            (defensive — extract_response_format normally guards this).
    """
    if JSON_COERCION_TOOL_NAME in set(existing_tool_names):
        raise ValueError("ERR_RESERVED_TOOL_NAME")
    json_schema = rf.get("json_schema")
    if not json_schema or "schema" not in json_schema:
        raise ValueError("ERR_INVALID_JSON_SCHEMA")
    function: ToolFunction = {
        "name": JSON_COERCION_TOOL_NAME,
        "description": _COERCION_TOOL_DESCRIPTION,
        "parameters": json_schema["schema"],
    }
    tool: Tool = {"type": "function", "function": function}
    choice: ToolChoiceNamed = {
        "type": "function",
        "function": {"name": JSON_COERCION_TOOL_NAME},
    }
    return tool, choice


def is_coercion_tool_call(name: str) -> bool:
    """True when a returned tool call is the gateway's synthetic coercion tool.

    The provider adapter uses this to decide whether a native tool call should be
    UNWRAPPED into ``message.content`` (the coercion tool) or surfaced as a normal
    ``tool_calls`` entry (a caller-supplied tool).
    """
    return name == JSON_COERCION_TOOL_NAME


def unwrap_coerced_tool_input(value: object) -> str:
    """Serialize the coercion tool's ``input`` object to the JSON STRING for content.

    FAIL-SAFE: reuses the v10 :func:`dump_tool_arguments` semantics — a dict/list is
    json-dumped, a str passes through, anything else becomes ``str(value)``; never
    raises and never drops.
    """
    return dump_tool_arguments(value)


__all__ = [
    "JSON_COERCION_TOOL_NAME",
    "JsonSchemaSpec",
    "ResponseFormat",
    "build_json_coercion_tool",
    "extract_response_format",
    "is_coercion_tool_call",
    "unwrap_coerced_tool_input",
]
