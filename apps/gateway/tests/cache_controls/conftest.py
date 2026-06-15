"""Suite-local fixtures for cache_controls tests (v19 task 4).

Self-contained (no DB, no Redis, no live server): fakes for the EmbeddingsUseCase
collaborators so the embedding-cache behavior can be unit-tested fast.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from gateway.keys.domain.entities import AuthzResult

TENANT_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TENANT_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
KEY_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

EMBED_MODEL = "text-embedding-3-small"

EMBED_PAYLOAD: dict[str, Any] = {"model": EMBED_MODEL, "input": "hello world"}
EMBED_RESPONSE_200: dict[str, Any] = {
    "object": "list",
    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
    "model": EMBED_MODEL,
    "usage": {"prompt_tokens": 2, "total_tokens": 2},
}
EMBED_ERROR_400: dict[str, Any] = {"error": {"message": "bad input", "code": "invalid_request"}}


def make_payload(input_val: Any = "hello world", model: str = EMBED_MODEL) -> dict[str, Any]:
    return {"model": model, "input": input_val}


class FakeGovernance:
    """Returns a fixed AuthzResult; records the model_id it authorized."""

    def __init__(self, *, tenant_id: uuid.UUID = TENANT_A, cache_enabled: bool = True) -> None:
        self._tenant_id = tenant_id
        self._cache_enabled = cache_enabled
        self.calls: list[str] = []

    async def authorize(
        self, raw_key: str | None, model_id: str, *, estimated_tokens: int | None = None
    ) -> AuthzResult:
        self.calls.append(model_id)
        return AuthzResult(
            tenant_id=self._tenant_id, key_id=KEY_ID, cache_enabled=self._cache_enabled
        )


class FakeSession:
    """Minimal AsyncSession stand-in: execute(stmt).one_or_none() → a model row."""

    def __init__(self, *, modality: str = "embedding", provider: str = "openai") -> None:
        self._row = SimpleNamespace(modality=modality, provider=provider)
        self.execute_calls = 0

    async def execute(self, _stmt: Any) -> Any:
        self.execute_calls += 1
        row = self._row

        class _Result:
            def one_or_none(self) -> Any:
                return row

        return _Result()


class FakeProvider:
    """UpstreamProvider stand-in: post_json returns a configured (status, body)."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self._status = status
        self._body = body if body is not None else EMBED_RESPONSE_200
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append((path, dict(payload)))
        return self._status, self._body


class FakeRegistry:
    """ProviderRegistry stand-in for select_provider (registry.get(provider))."""

    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    def get(self, _provider: str) -> FakeProvider:
        return self._provider


class FakeCache:
    """ResponseCache stand-in with an in-memory store + recorded set calls.

    Honors the ResponseCache port contract: get() never raises (returns None on
    "error" — simulated by always_miss). set() records (key, body, ttl).
    """

    def __init__(self, *, always_miss: bool = False) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self.set_calls: list[tuple[str, dict[str, Any], int]] = []
        self._always_miss = always_miss

    def seed(self, key: str, body: dict[str, Any]) -> None:
        self._store[key] = body

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        if self._always_miss:
            return None  # simulate a swallowed internal error → MISS (port contract)
        return self._store.get(cache_key)

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        self.set_calls.append((cache_key, body, ttl_seconds))
        self._store[cache_key] = body


class SpyRecorder:
    """UsageRecorder stand-in that records every record() call's kwargs.

    Declares supported_extras so the `cached` capability marker is forwarded by
    _dispatch_record (the typed-extras seam filters extras against this set).
    """

    supported_extras = frozenset({"cached", "team_id"})

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))

    @property
    def call_count(self) -> int:
        return len(self.calls)
