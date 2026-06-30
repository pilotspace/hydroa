"""Helios Harness — programmable stub + fixture library for gateway integration tests.

This module is test-support only (non-e2e). It exports:
  - StubCompletionUpstream  (SEAM B)
  - HarnessError
  - helios_request / provider_fixture / ProviderFixture / HeliosCase / Provider
  - assert_fixtures_have_provenance
  - wire_mock_transport / sse_handler / fake_provider_credential  (SEAM C)

Contract: agent-coding-stub-harness TASK.md §3 — FROZEN @ v1.
Zero live network calls from this module itself.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any, Literal, TypedDict
from collections.abc import Callable

import httpx

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BearerCredential

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

HeliosCase = Literal[
    "chat",
    "chat_stream",
    "tool_call",
    "parallel_tool_calls",
    "tool_result_followup",
    "reasoning_effort",
]

Provider = Literal["anthropic", "gemini", "bedrock", "openrouter"]

TransportHandler = Callable[[httpx.Request], httpx.Response]


# ---------------------------------------------------------------------------
# HarnessError
# ---------------------------------------------------------------------------


class HarnessError(AssertionError):
    """Test-infrastructure error.  Subclasses AssertionError so pytest surfaces it cleanly."""

    code: Literal["invalid_sse_fixture", "stub_unscripted", "unfaithful_fixture"]

    def __init__(
        self,
        code: Literal["invalid_sse_fixture", "stub_unscripted", "unfaithful_fixture"],
    ) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# ProviderFixture TypedDict
# ---------------------------------------------------------------------------


class ProviderFixture(TypedDict):
    """One canonical provider response fixture."""

    native: dict[str, object] | list[bytes]
    provenance: str  # non-empty source tag — REQUIRED


# ---------------------------------------------------------------------------
# StubCompletionUpstream (SEAM B)
# ---------------------------------------------------------------------------


def _validate_sse_frame(frame: bytes) -> None:
    """Raise HarnessError if *frame* is not a well-formed SSE data frame.

    Well-formed means:
      - Starts with b"data: "
      - Ends with b"\\n\\n"
    """
    if not frame or not frame.startswith(b"data: ") or not frame.endswith(b"\n\n"):
        raise HarnessError("invalid_sse_fixture")


class StubCompletionUpstream:
    """Programmable stub implementing the CompletionUpstream Protocol (SEAM B).

    Construction is ATOMIC — if any stream frame is malformed, a HarnessError is raised
    before the instance is returned, so callers can never observe a partially-valid stub.
    """

    def __init__(
        self,
        *,
        complete: tuple[int, dict[str, object]] | None = None,
        stream: list[bytes] | None = None,
    ) -> None:
        # Validate all stream frames BEFORE storing any state (atomic)
        if stream is not None:
            for frame in stream:
                _validate_sse_frame(frame)

        self._complete_script = complete
        self._stream_script = stream
        self.forwarded: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        """Record payload; return scripted response or raise HarnessError."""
        self.forwarded.append(payload)
        if self._complete_script is None:
            raise HarnessError("stub_unscripted")
        return self._complete_script

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        """Record payload; yield scripted frames or raise HarnessError."""
        self.forwarded.append(payload)

        if self._stream_script is None:

            async def _unscripted() -> AsyncIterator[bytes]:
                raise HarnessError("stub_unscripted")
                yield  # make it an async generator

            return _unscripted()

        frames = self._stream_script

        async def _gen() -> AsyncIterator[bytes]:
            for frame in frames:
                yield frame

        return _gen()


# ---------------------------------------------------------------------------
# Fixture library: canonical Helios OpenAI-wire requests
# ---------------------------------------------------------------------------

# Shared tool definitions used by multiple cases
_GET_WEATHER_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
            },
            "required": ["city"],
        },
    },
}

_GET_TIME_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the current time in a timezone",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "IANA timezone"},
            },
            "required": ["timezone"],
        },
    },
}

# Canonical Helios OpenAI-wire request bodies (faithful to convert.rs)
_HELIOS_REQUESTS: dict[HeliosCase, dict[str, object]] = {
    "chat": {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "Hello, world!"}],
    },
    "chat_stream": {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "Hello, world!"}],
        "stream": True,
    },
    "tool_call": {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "What's the weather in Paris?"}],
        "tools": [_GET_WEATHER_TOOL],
        "tool_choice": "auto",
    },
    "parallel_tool_calls": {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "What's the weather in Paris and the UTC time?"}],
        "tools": [_GET_WEATHER_TOOL, _GET_TIME_TOOL],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    },
    "tool_result_followup": {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "user", "content": "What's the weather in Paris?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "content": '{"temperature": 18, "unit": "celsius"}',
            },
        ],
        "tools": [_GET_WEATHER_TOOL],
    },
    "reasoning_effort": {
        "model": "openai/o1-mini",
        "messages": [{"role": "user", "content": "Solve: what is 2+2?"}],
        "reasoning_effort": "high",
    },
}


def helios_request(case: HeliosCase) -> dict[str, object]:
    """Return the canonical Helios OpenAI-wire request body for *case*."""
    return dict(_HELIOS_REQUESTS[case])


# ---------------------------------------------------------------------------
# Fixture library: provider native responses
# ---------------------------------------------------------------------------

# Anthropic non-stream chat response (faithful to Anthropic Messages API)
_ANTHROPIC_CHAT_NATIVE: dict[str, object] = {
    "id": "msg_01abcdef",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [{"type": "text", "text": "Hello, world!"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 8, "output_tokens": 5},
}

# Anthropic SSE frames for chat_stream (faithful to Anthropic streaming Messages API)
_ANTHROPIC_CHAT_STREAM_NATIVE: list[bytes] = [
    b"event: message_start\ndata: "
    + json.dumps(
        {
            "type": "message_start",
            "message": {
                "id": "msg_stream_01",
                "type": "message",
                "role": "assistant",
                "model": "claude-3-5-sonnet-20241022",
                "usage": {"input_tokens": 8, "output_tokens": 0},
            },
        }
    ).encode()
    + b"\n\n",
    b"event: content_block_start\ndata: "
    + json.dumps(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
    ).encode()
    + b"\n\n",
    b"event: content_block_delta\ndata: "
    + json.dumps(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello, world!"},
        }
    ).encode()
    + b"\n\n",
    b"event: content_block_stop\ndata: "
    + json.dumps({"type": "content_block_stop", "index": 0}).encode()
    + b"\n\n",
    b"event: message_delta\ndata: "
    + json.dumps(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5},
        }
    ).encode()
    + b"\n\n",
    b"event: message_stop\ndata: " + json.dumps({"type": "message_stop"}).encode() + b"\n\n",
]

# Anthropic non-stream tool_call response (single tool_use block)
_ANTHROPIC_TOOL_CALL_NATIVE: dict[str, object] = {
    "id": "msg_tool_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_01abcdef",
            "name": "get_weather",
            "input": {"city": "Paris"},
        }
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 20, "output_tokens": 8},
}

# Anthropic non-stream parallel tool calls response (2 tool_use blocks, for SEAM A)
# convert.rs ref: Messages API response with parallel tool calls in content array
_ANTHROPIC_PARALLEL_TOOL_CALLS_NATIVE: dict[str, object] = {
    "id": "msg_para_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [
        {
            "type": "tool_use",
            "id": "toolu_01",
            "name": "get_weather",
            "input": {"city": "Paris"},
        },
        {
            "type": "tool_use",
            "id": "toolu_02",
            "name": "get_time",
            "input": {"timezone": "UTC"},
        },
    ],
    "stop_reason": "tool_use",
    "stop_sequence": None,
    "usage": {"input_tokens": 25, "output_tokens": 12},
}

# Anthropic non-stream reasoning response
# convert.rs ref: Anthropic thinking block as part of content
_ANTHROPIC_REASONING_NATIVE: dict[str, object] = {
    "id": "msg_reason_01",
    "type": "message",
    "role": "assistant",
    "model": "claude-3-5-sonnet-20241022",
    "content": [
        {
            "type": "thinking",
            "thinking": "2+2=4 because...",
        },
        {
            "type": "text",
            "text": "2+2 equals 4.",
        },
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 12, "output_tokens": 30},
}

# Gemini (OpenRouter wire) non-stream chat response
_GEMINI_CHAT_NATIVE: dict[str, object] = {
    "id": "chatcmpl-gemini-01",
    "object": "chat.completion",
    "created": 1750000000,
    "model": "google/gemini-2.0-flash",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello, world!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
}

# Bedrock (OpenRouter wire) non-stream chat response
_BEDROCK_CHAT_NATIVE: dict[str, object] = {
    "id": "chatcmpl-bedrock-01",
    "object": "chat.completion",
    "created": 1750000000,
    "model": "anthropic/claude-3-5-sonnet",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello, world!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
}

# OpenRouter non-stream chat response
_OPENROUTER_CHAT_NATIVE: dict[str, object] = {
    "id": "chatcmpl-or-01",
    "object": "chat.completion",
    "created": 1750000000,
    "model": "openai/gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello, world!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
    "provider": "OpenAI",
}

# Fixture library: maps (case, provider) → ProviderFixture
# Mutable so provenance-guard tests can monkey-patch it
_FIXTURE_LIBRARY: dict[tuple[HeliosCase, Provider], ProviderFixture] = {
    # ── chat ──────────────────────────────────────────────────────────────────
    ("chat", "anthropic"): ProviderFixture(
        native=_ANTHROPIC_CHAT_NATIVE,
        provenance="Anthropic Messages API docs / POST /v1/messages response shape",
    ),
    ("chat", "gemini"): ProviderFixture(
        native=_GEMINI_CHAT_NATIVE,
        provenance="OpenRouter OpenAI-wire compat / gemini-2.0-flash response shape",
    ),
    ("chat", "bedrock"): ProviderFixture(
        native=_BEDROCK_CHAT_NATIVE,
        provenance="OpenRouter OpenAI-wire compat / Bedrock Anthropic response shape",
    ),
    ("chat", "openrouter"): ProviderFixture(
        native=_OPENROUTER_CHAT_NATIVE,
        provenance="OpenRouter OpenAI-wire compat / gpt-4o response shape",
    ),
    # ── chat_stream ───────────────────────────────────────────────────────────
    ("chat_stream", "anthropic"): ProviderFixture(
        native=_ANTHROPIC_CHAT_STREAM_NATIVE,
        provenance="Anthropic Messages Streaming API docs / message_start→content_block_delta→message_stop",
    ),
    # ── tool_call ─────────────────────────────────────────────────────────────
    ("tool_call", "anthropic"): ProviderFixture(
        native=_ANTHROPIC_TOOL_CALL_NATIVE,
        provenance="Anthropic Messages API docs / tool_use content block shape",
    ),
    # ── parallel_tool_calls ───────────────────────────────────────────────────
    ("parallel_tool_calls", "anthropic"): ProviderFixture(
        native=_ANTHROPIC_PARALLEL_TOOL_CALLS_NATIVE,
        provenance="Anthropic Messages API docs / parallel tool_use blocks in content array",
    ),
    # ── reasoning_effort ──────────────────────────────────────────────────────
    ("reasoning_effort", "anthropic"): ProviderFixture(
        native=_ANTHROPIC_REASONING_NATIVE,
        provenance="Anthropic extended thinking docs / thinking+text content blocks",
    ),
}


def provider_fixture(case: HeliosCase, provider: Provider) -> ProviderFixture:
    """Return the canonical ProviderFixture for (case, provider)."""
    key = (case, provider)
    if key not in _FIXTURE_LIBRARY:
        raise KeyError(f"No provider fixture for case={case!r}, provider={provider!r}")
    return _FIXTURE_LIBRARY[key]


# ---------------------------------------------------------------------------
# Provenance guard
# ---------------------------------------------------------------------------


def assert_fixtures_have_provenance() -> None:
    """Enumerate the library; any entry with empty/missing provenance → HarnessError."""
    for (_case, _provider), pf in _FIXTURE_LIBRARY.items():
        prov = pf.get("provenance", "")
        if not isinstance(prov, str) or not prov.strip():
            raise HarnessError("unfaithful_fixture")


# ---------------------------------------------------------------------------
# SEAM C helpers
# ---------------------------------------------------------------------------


def sse_handler(frames: list[bytes], *, status: int = 200) -> TransportHandler:
    """Return an httpx mock transport handler that streams the given native SSE frames.

    The handler concatenates all frames into a single response body with
    content-type: text/event-stream.  The adapter reads via aiter_lines() which
    works on the in-memory content bytes.
    """
    body = b"".join(frames)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    return _handler


def wire_mock_transport(adapter: object, handler: TransportHandler) -> None:
    """Swap adapter._client for a new AsyncClient backed by httpx.MockTransport(handler).

    Exercises the REAL adapter (request build · auth · SSE parse · circuit breaker ·
    error map) with zero sockets.  The base_url is preserved from the existing client.
    """
    existing_client: httpx.AsyncClient = adapter._client  # type: ignore[attr-defined]
    base_url = str(existing_client.base_url)

    new_client = httpx.AsyncClient(
        base_url=base_url,
        transport=httpx.MockTransport(handler),
    )
    adapter._client = new_client  # type: ignore[attr-defined]


@contextmanager
def fake_provider_credential(secret: str = "test-key") -> Iterator[None]:
    """Context manager: install a BearerCredential in the request-scoped contextvar.

    Resets the contextvar in finally to avoid cross-test leaks.
    """
    cred = BearerCredential(secret=secret)
    token = set_provider_credential(cred)
    try:
        yield
    finally:
        reset_provider_credential(token)
