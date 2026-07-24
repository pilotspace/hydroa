"""Red suite for responses-api-core (PLAN.md §4 — contract DRAFT, freeze pending).

One test per §1 Must/Reject. Fakes are injected via app.state
(completion_upstream, usage_recorder); model rows are inserted directly into
the catalog tables — no network anywhere. Every test MUST fail 404 (route
absent) before build: red for the right reason.
"""

from __future__ import annotations

from typing import Any

import httpx

from .conftest import (
    CHAT_COMPLETIONS,
    DETAILED_USAGE_UPSTREAM_BODY,
    ERROR_SSE_CHUNKS,
    LENGTH_UPSTREAM_BODY,
    RESPONSES,
    TOOL_CALL_UPSTREAM_BODY,
    FakeCompletionUpstream,
    FakeUsageRecorder,
    auth_bearer,
    parse_named_sse,
    responses_payload,
)


def _inject(app: Any, upstream: FakeCompletionUpstream) -> FakeUsageRecorder:
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder
    return recorder


def _assert_problem_code(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected code {code}, got: {body}"


# ===========================================================================
# M1 — non-stream Response object shape
# ===========================================================================


async def test_non_stream_basic_response(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES, json=responses_payload(active_model), headers=auth_bearer(api_key["key"])
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "response"
    assert isinstance(body["id"], str) and body["id"].startswith("resp_")
    assert body["status"] == "completed"
    assert body["model"] == active_model
    assert body["store"] is False  # stateless-core default, echoed honestly
    assert body["previous_response_id"] is None
    output = body["output"]
    assert isinstance(output, list) and len(output) == 1
    msg = output[0]
    assert msg["type"] == "message"
    assert msg["role"] == "assistant"
    assert isinstance(msg["id"], str) and msg["id"].startswith("msg_")
    assert msg["content"] == [{"type": "output_text", "text": "Hello there!", "annotations": []}]
    assert upstream.calls == 1, "exactly ONE CompletionUseCase round-trip"


# ===========================================================================
# M4 — billing through the unchanged recorder (non-stream)
# ===========================================================================


async def test_non_stream_bills_exactly_one_usage_record(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)

    resp = await client.post(
        RESPONSES, json=responses_payload(active_model), headers=auth_bearer(api_key["key"])
    )

    assert resp.status_code == 200, resp.text
    assert len(recorder.records) == 1, "exactly one usage_records write per served request"
    assert recorder.records[0]["model"] == active_model  # billed on the SERVED model
    assert recorder.records[0]["status"] == 200


# ===========================================================================
# M2 — request translation: input items · instructions · max_output_tokens
# ===========================================================================


async def test_input_items_and_instructions_translate(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(
            active_model,
            input_=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "What is 2+2?"}],
                }
            ],
            instructions="You are terse.",
            max_output_tokens=64,
        ),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    payload = upstream.last_payload
    assert payload is not None
    messages = payload["messages"]
    assert messages[0] == {"role": "system", "content": "You are terse."}
    assert messages[1]["role"] == "user"
    # input_text part flattens to plain chat content
    assert messages[1]["content"] == "What is 2+2?" or messages[1]["content"] == [
        {"type": "text", "text": "What is 2+2?"}
    ]
    assert payload["max_tokens"] == 64
    assert "max_output_tokens" not in payload
    assert "input" not in payload and "instructions" not in payload


# ===========================================================================
# M2/M3 — flattened tools in; function_call output items out
# ===========================================================================


async def test_flattened_tools_translate_and_tool_call_returns(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream(body=dict(TOOL_CALL_UPSTREAM_BODY))
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(
            active_model,
            input_="weather in SF?",
            tools=[
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "Get weather for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                }
            ],
            tool_choice={"type": "function", "name": "get_weather"},
        ),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    payload = upstream.last_payload
    assert payload is not None
    # Responses flattened tool -> chat-nested function tool
    assert payload["tools"] == [
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
    assert payload["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}

    body = resp.json()
    (item,) = body["output"]
    assert item["type"] == "function_call"
    assert item["call_id"] == "call_abc"  # upstream call id preserved verbatim
    assert item["name"] == "get_weather"
    assert item["arguments"] == '{"city": "SF"}'
    assert isinstance(item["id"], str) and item["id"].startswith("fc_")


# ===========================================================================
# M2 — function_call / function_call_output input items round-trip
# ===========================================================================


async def test_function_call_round_trip_input_items(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(
            active_model,
            input_=[
                {"role": "user", "content": "weather in SF?"},
                {
                    "type": "function_call",
                    "call_id": "call_abc",
                    "name": "get_weather",
                    "arguments": '{"city": "SF"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_abc",
                    "output": '{"temp_f": 61}',
                },
            ],
        ),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    payload = upstream.last_payload
    assert payload is not None
    messages = payload["messages"]
    assistant_turn = messages[-2]
    tool_turn = messages[-1]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["tool_calls"] == [
        {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "SF"}'},
        }
    ]
    assert tool_turn == {
        "role": "tool",
        "tool_call_id": "call_abc",
        "content": '{"temp_f": 61}',
    }


# ===========================================================================
# M2 — text.format -> response_format
# ===========================================================================


async def test_text_format_json_schema_maps_to_response_format(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    resp = await client.post(
        RESPONSES,
        json=responses_payload(
            active_model,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "answer_shape",
                    "schema": schema,
                    "strict": True,
                }
            },
        ),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    payload = upstream.last_payload
    assert payload is not None
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer_shape", "schema": schema, "strict": True},
    }
    assert "text" not in payload


# ===========================================================================
# M4 — usage name mapping + details defaults
# ===========================================================================


async def test_usage_mapping_names_and_details(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream(body=dict(DETAILED_USAGE_UPSTREAM_BODY))
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES, json=responses_payload(active_model), headers=auth_bearer(api_key["key"])
    )

    assert resp.status_code == 200, resp.text
    usage = resp.json()["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 40
    assert usage["total_tokens"] == 140
    assert usage["input_tokens_details"] == {"cached_tokens": 60}
    assert usage["output_tokens_details"] == {"reasoning_tokens": 8}
    assert "prompt_tokens" not in usage and "completion_tokens" not in usage


# ===========================================================================
# M3 — finish_reason length -> incomplete
# ===========================================================================


async def test_length_finish_reason_maps_to_incomplete(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream(body=dict(LENGTH_UPSTREAM_BODY))
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, max_output_tokens=16),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "max_output_tokens"}
    assert body["output"][0]["content"][0]["text"] == "truncated outp"


# ===========================================================================
# M5 — streaming event sequence
# ===========================================================================


async def test_stream_event_sequence_and_terminal_completed(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, stream=True),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = parse_named_sse(resp.content)
    assert frames, "no SSE frames emitted"

    names = [name for name, _ in frames]
    assert all(name is not None for name in names), "every frame must be event-named"
    assert names[0] == "response.created"
    assert names[-1] == "response.completed"
    for required in (
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
    ):
        assert required in names, f"missing {required}: {names}"

    # No chat-style [DONE] sentinel anywhere.
    assert not any(d.get("__done_sentinel__") for _, d in frames)

    # sequence_number strictly monotonic from 0.
    seqs = [d["sequence_number"] for _, d in frames]
    assert seqs == list(range(len(frames))), f"non-monotonic sequence_numbers: {seqs}"

    # Deltas reassemble the text; terminal frame carries the full Response + usage.
    deltas = [d["delta"] for name, d in frames if name == "response.output_text.delta"]
    assert "".join(deltas) == "Hello"
    final = frames[-1][1]["response"]
    assert final["object"] == "response"
    assert final["status"] == "completed"
    assert final["usage"]["input_tokens"] == 7
    assert final["usage"]["output_tokens"] == 2
    assert final["output"][0]["content"][0]["text"] == "Hello"


async def test_stream_bills_exactly_one_usage_record(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, stream=True),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    _ = resp.content  # drain
    assert len(recorder.records) == 1, "stream must bill exactly once via the unchanged recorder"
    assert recorder.records[0]["model"] == active_model


# ===========================================================================
# M6 — mid-stream upstream error -> terminal response.failed
# ===========================================================================


async def test_mid_stream_error_emits_response_failed(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream(stream_chunks=list(ERROR_SSE_CHUNKS))
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, stream=True),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    frames = parse_named_sse(resp.content)
    names = [name for name, _ in frames]
    assert names[-1] == "response.failed", f"terminal event must be response.failed: {names}"
    final = frames[-1][1]["response"]
    assert final["status"] == "failed"
    assert final["error"]["message"] == "upstream boom"
    assert "response.completed" not in names, "a failed stream must never fabricate completion"


# ===========================================================================
# Rejects — named codes, upstream never dialed, nothing billed
# ===========================================================================


async def test_reject_background_mode(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, background=True),
        headers=auth_bearer(api_key["key"]),
    )

    _assert_problem_code(resp, 400, "ERR_RESPONSES_BACKGROUND_UNSUPPORTED")
    assert upstream.calls == 0, "rejected before any upstream dial"
    assert len(recorder.records) == 0, "nothing billed on rejection"


async def test_reject_hosted_tool_types(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)

    for hosted in ({"type": "web_search"}, {"type": "file_search"}):
        resp = await client.post(
            RESPONSES,
            json=responses_payload(active_model, tools=[hosted]),
            headers=auth_bearer(api_key["key"]),
        )
        _assert_problem_code(resp, 400, "ERR_RESPONSES_TOOL_UNSUPPORTED")

    assert upstream.calls == 0
    assert len(recorder.records) == 0


# RETIRED (responses-state-store PLAN.md §3 "Ownership-transfer note", 2026-07-24):
# test_reject_store_true_and_previous_response_id asserted that store:true /
# previous_response_id terminate 400 ERR_RESPONSES_STORE_UNSUPPORTED in the stateless
# core. The responses-api-core frozen §3 "State extension point" pre-authorizes the
# wave-2 responses-state-store contract to take ownership of their ACCEPTANCE; that
# contract now serves them (persist / chain / GET / DELETE, tests/responses_state_store/).
# This is the ONE contracted frozen-suite retirement (transfer clause) — not a tamper.


async def test_reject_malformed_and_empty_input(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)
    headers = auth_bearer(api_key["key"])

    # Non-JSON body.
    resp = await client.post(
        RESPONSES, content=b"not json{", headers={**headers, "content-type": "application/json"}
    )
    _assert_problem_code(resp, 400, "ERR_PAYLOAD_INVALID")

    # Empty input list.
    resp = await client.post(
        RESPONSES, json=responses_payload(active_model, input_=[]), headers=headers
    )
    _assert_problem_code(resp, 400, "ERR_PAYLOAD_INVALID")

    # Unknown input item type.
    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, input_=[{"type": "telepathy", "text": "hi"}]),
        headers=headers,
    )
    _assert_problem_code(resp, 400, "ERR_PAYLOAD_INVALID")

    assert upstream.calls == 0, "malformed body never reaches governance or the upstream"
    assert len(recorder.records) == 0


# ===========================================================================
# M7 — governance posture byte-identical to /v1/chat/completions
# ===========================================================================


async def test_governance_parity_unauthenticated_matches_chat(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    chat = await client.post(
        CHAT_COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
    )
    responses = await client.post(RESPONSES, json=responses_payload(active_model))

    assert chat.status_code == responses.status_code == 401
    chat_body, resp_body = chat.json(), responses.json()
    # Compared live against the chat seam, never hardcoded — same code, same opacity.
    assert resp_body.get("code") == chat_body.get("code")
    assert resp_body.get("title") == chat_body.get("title")
    assert upstream.calls == 0


# ===========================================================================
# M8 — byte-identical default path: chat still serves through the same app
# ===========================================================================


async def test_default_path_untouched_chat_still_serves(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)

    resp = await client.post(
        CHAT_COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "chat.completion"  # untranslated — zero new plumbing engaged
    assert body["choices"][0]["message"]["content"] == "Hello there!"
    assert len(recorder.records) == 1


# ===========================================================================
# M5 regression (heal 1) — usage split across / arriving AFTER the
# finish_reason frame must still bill real tokens into response.completed
# ===========================================================================

# Mirrors the live OpenRouter shape the frozen chat seam already handles: the
# finish_reason frame carries NO usage, the usage-only frame arrives LATER, and
# that usage frame is split across two network chunks. The earlier fixture
# (TEXT_SSE_CHUNKS) put finish_reason + usage in ONE frame, masking a translator
# that closed on finish_reason and reported zeroed usage. The terminal event
# must derive usage the same way billing does (extract_usage_from_sse: join +
# reverse-scan for the LAST usage frame).
SPLIT_USAGE_SSE_CHUNKS: list[bytes] = [
    b'data: {"id":"gen-x","model":"openai/gpt-4o",'
    b'"choices":[{"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"id":"gen-x","choices":[{"delta":{"content":"Hel"}}]}\n\n',
    b'data: {"id":"gen-x","choices":[{"delta":{"content":"lo"}}]}\n\n',
    # finish_reason arrives FIRST, with no usage (the falsifying ordering).
    b'data: {"id":"gen-x","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    # usage-only frame, SPLIT across two network chunks.
    b'data: {"id":"gen-x","choices":[],"usage":{"prompt_tokens":11,',
    b'"completion_tokens":5,"total_tokens":16}}\n\n',
    b"data: [DONE]\n\n",
]


async def test_stream_usage_split_after_finish_reason_bills_real_tokens(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream(stream_chunks=list(SPLIT_USAGE_SSE_CHUNKS))
    recorder = _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, stream=True),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    frames = parse_named_sse(resp.content)
    names = [name for name, _ in frames]
    assert names[-1] == "response.completed", names

    # Deltas still reassemble the text (TTFB path unchanged).
    deltas = [d["delta"] for name, d in frames if name == "response.output_text.delta"]
    assert "".join(deltas) == "Hello"

    # Terminal event carries the REAL split usage — never zeros.
    final = frames[-1][1]["response"]
    assert final["usage"]["input_tokens"] == 11
    assert final["usage"]["output_tokens"] == 5
    assert final["usage"]["total_tokens"] == 16

    # Billing wrote exactly one NON-ZERO usage_record on the served model.
    assert len(recorder.records) == 1
    assert recorder.records[0]["model"] == active_model
    recorded_usage = recorder.records[0]["usage"]
    assert isinstance(recorded_usage, dict)
    assert recorded_usage.get("prompt_tokens") == 11
    assert recorded_usage.get("completion_tokens") == 5
