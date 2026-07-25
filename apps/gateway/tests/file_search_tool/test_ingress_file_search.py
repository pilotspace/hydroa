"""RED — file_search must stop being rejected and ride into the chat body.

Contract under test (file-search-tool PLAN.md §3, DRAFT):
  - `file_search` is REMOVED from the responses ingress hosted-tool blocklist, so a
    /v1/responses request carrying {"type":"file_search","vector_store_ids":[...]}
    is ACCEPTED (no ERR_RESPONSES_TOOL_UNSUPPORTED).
  - `responses_request_to_chat` PRESERVES the file_search tool (with its
    vector_store_ids) into the internal chat body's `tools` array, so the ONE shared
    grounding seam inside CompletionUseCase sees the identical tool for BOTH ingresses.
  - Default path unchanged: a request with NO file_search tool translates byte-identically.

RED reason: `file_search` is currently in `_HOSTED_TOOL_TYPES` (validate raises), and
`_translate_tools` drops every non-"function" tool. Both are missing-implementation reds
against the live, imported ingress module — not import/harness errors.
"""

from __future__ import annotations

import pytest

from gateway.proxy.infrastructure.openai_responses_ingress import (
    ResponsesIngressError,
    responses_request_to_chat,
    validate_responses_request,
)

_VS = "vs_" + "a" * 32


def _file_search_body() -> dict[str, object]:
    return {
        "model": "gpt-4o",
        "input": "What does the onboarding doc say about SSO?",
        "tools": [{"type": "file_search", "vector_store_ids": [_VS]}],
    }


def test_file_search_tool_is_accepted_not_rejected() -> None:
    """validate_responses_request must NOT reject a file_search tool.

    RED today: raises ResponsesIngressError(ERR_RESPONSES_TOOL_UNSUPPORTED) because
    'file_search' is in _HOSTED_TOOL_TYPES.
    """
    try:
        validate_responses_request(_file_search_body())
    except ResponsesIngressError as exc:  # pragma: no cover - asserted red today
        pytest.fail(
            "file_search must be accepted, not rejected as a hosted tool; "
            f"got {exc.code}: {exc.message}"
        )


def test_file_search_tool_preserved_into_chat_body() -> None:
    """The translated chat body must carry the file_search tool + its vector_store_ids.

    RED today: _translate_tools drops non-function tools, so internal['tools'] omits it.
    """
    internal = responses_request_to_chat(_file_search_body())

    tools = internal.get("tools")
    assert isinstance(tools, list) and tools, (
        f"chat body dropped the tools array entirely: {internal!r}"
    )
    fs = [t for t in tools if isinstance(t, dict) and t.get("type") == "file_search"]
    assert fs, f"file_search tool not preserved into chat body tools: {tools!r}"
    assert fs[0].get("vector_store_ids") == [_VS], (
        f"vector_store_ids not carried through translation: {fs[0]!r}"
    )


def test_default_path_no_file_search_translation_unchanged() -> None:
    """Byte-identical default path: a request with NO file_search translates as today.

    Guards the invariant that the file_search change engages ZERO new plumbing when the
    tool is absent. Stays green through build (a regression pin, not a gated red).
    """
    body = {"model": "gpt-4o", "input": "hello"}
    internal = responses_request_to_chat(body)
    assert internal == {"model": "gpt-4o", "messages": [{"role": "user", "content": "hello"}]}
