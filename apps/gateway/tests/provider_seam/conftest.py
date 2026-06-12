"""Suite-local fixtures for provider_seam tests.

No DB, no live Redis, no live OpenAI — everything is faked.

Key fakes:
  - FakeUpstreamProvider  — implements UpstreamProvider; records calls
  - FakeCompletionUpstream — implements CompletionUpstream for chat path; records calls
  - SequencedMockTransport — httpx transport that replays a pre-built response list
  - FakeProviderRegistry   — records get() calls; used to assert the chat path never
                             consults the registry

Pattern follows tests/retry_policy/conftest.py (httpx.MockTransport for HTTP-level
assertions) and tests/cooldown_circuit/conftest.py (in-memory fakes, no network).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest


# ---------------------------------------------------------------------------
# Response / payload constants
# ---------------------------------------------------------------------------

FAKE_API_KEY = "sk-test-provider-seam"
FAKE_OPENAI_BASE_URL = "https://api.openai.com/v1"
FAKE_OPENROUTER_KEY = "sk-or-test"

CHAT_PAYLOAD = {
    "model": "openai/gpt-4o",
    "messages": [{"role": "user", "content": "hello"}],
}

EMBEDDING_PAYLOAD = {
    "model": "text-embedding-3-small",
    "input": "hello world",
}

CHAT_RESPONSE_BODY = {
    "id": "gen-chat-1",
    "model": "openai/gpt-4o",
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}

EMBEDDING_RESPONSE_BODY = {
    "object": "list",
    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
    "model": "text-embedding-3-small",
    "usage": {"prompt_tokens": 2, "total_tokens": 2},
}


# ---------------------------------------------------------------------------
# FakeUpstreamProvider — implements UpstreamProvider protocol (once it exists)
# ---------------------------------------------------------------------------


class FakeUpstreamProvider:
    """In-memory UpstreamProvider that records calls and returns preset responses.

    Used to verify that select_provider() returns the correct adapter instance
    and that the registry dispatches to the right provider.
    """

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.post_json_calls: list[dict[str, Any]] = []
        self.post_multipart_calls: list[dict[str, Any]] = []
        self.stream_bytes_calls: list[dict[str, Any]] = []
        self._post_json_response: tuple[int, dict[str, Any]] = (200, {"object": "list", "data": []})

    def set_post_json_response(self, status: int, body: dict[str, Any]) -> None:
        self._post_json_response = (status, body)

    async def post_json(
        self, path: str, payload: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        self.post_json_calls.append({"path": path, "payload": payload})
        return self._post_json_response

    async def post_multipart(
        self, path: str, files: dict[str, Any], data: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        self.post_multipart_calls.append({"path": path, "files": files, "data": data})
        return (200, {"text": "transcription"})

    def stream_bytes(
        self, path: str, payload: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        self.stream_bytes_calls.append({"path": path, "payload": payload})

        async def _gen() -> AsyncIterator[bytes]:
            yield b"audio-bytes"

        return _gen()


# ---------------------------------------------------------------------------
# FakeCompletionUpstream — implements CompletionUpstream for chat path
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Records calls to complete() and stream() — injected on app.state.completion_upstream.

    Used to assert that the v6 chat path still calls completion_upstream.complete()
    and that the provider registry is NOT consulted.
    """

    def __init__(self) -> None:
        self.complete_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        self._complete_response: tuple[int, dict[str, Any]] = (200, CHAT_RESPONSE_BODY)

    @property
    def call_count(self) -> int:
        return len(self.complete_calls)

    async def complete(
        self, payload: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        self.complete_calls.append(dict(payload))
        return self._complete_response

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: {}\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# FakeProviderRegistry — records get() calls; used to assert chat path does NOT consult it
# ---------------------------------------------------------------------------


class FakeProviderRegistry:
    """Records all get() calls.

    Mounted on app.state.provider_registry in PS9 to assert that the chat path
    never calls registry.get().
    """

    def __init__(self) -> None:
        self.get_calls: list[str] = []

    def get(self, provider_name: str) -> Any:
        self.get_calls.append(provider_name)
        return None

    @property
    def call_count(self) -> int:
        return len(self.get_calls)


# ---------------------------------------------------------------------------
# SequencedMockTransport — replays a list of httpx.Response or exceptions
# ---------------------------------------------------------------------------


class SequencedMockTransport(httpx.AsyncBaseTransport):
    """Replay a pre-built sequence of responses or exceptions.

    Matches the retry_policy conftest pattern.
    After the sequence is exhausted, raises RuntimeError (test bug guard).
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.requests: list[httpx.Request] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> httpx.Request | None:
        return self.requests[-1] if self.requests else None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._idx >= len(self._responses):
            raise RuntimeError(
                f"SequencedMockTransport: ran out of responses after {self._idx} calls"
            )
        entry = self._responses[self._idx]
        self._idx += 1
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, type) and issubclass(entry, BaseException):
            raise entry("mock transport error")
        return entry


def make_json_response(status: int, body: dict[str, Any] | None = None) -> httpx.Response:
    """Build a minimal httpx.Response with JSON body."""
    content = json.dumps(body or {}).encode()
    return httpx.Response(
        status_code=status,
        headers={"content-type": "application/json"},
        content=content,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_openai_provider() -> FakeUpstreamProvider:
    """A FakeUpstreamProvider named 'openai'."""
    return FakeUpstreamProvider(name="openai")


@pytest.fixture
def fake_openrouter_provider() -> FakeUpstreamProvider:
    """A FakeUpstreamProvider named 'openrouter'."""
    return FakeUpstreamProvider(name="openrouter")


@pytest.fixture
def fake_completion_upstream() -> FakeCompletionUpstream:
    """A FakeCompletionUpstream for chat path injection."""
    return FakeCompletionUpstream()


@pytest.fixture
def fake_provider_registry() -> FakeProviderRegistry:
    """A FakeProviderRegistry that records calls."""
    return FakeProviderRegistry()


@pytest.fixture
def mock_transport_200_embedding() -> SequencedMockTransport:
    """Transport that returns 200 with a minimal embedding response body."""
    return SequencedMockTransport(
        [make_json_response(200, EMBEDDING_RESPONSE_BODY)]
    )
