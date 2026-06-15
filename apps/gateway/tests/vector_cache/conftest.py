"""Suite-local fixtures for the vector_cache tests (v19 task 5 — semantic-cache).

Self-contained (no DB, no Redis, no live server): a faithful in-memory FakeRedis
(bytes values + list ops, mirroring redis.asyncio with decode_responses=False), a
FakeEmbedder (text -> known vector, with a fail toggle), and the CompletionUseCase
integration fakes (mirroring tests/streaming_resilience/conftest) so the third
embedding-similarity lookup layer and its use-case wiring can be unit-tested fast.
"""

from __future__ import annotations

import uuid
from typing import Any

from gateway.keys.domain.entities import AuthzResult
from gateway.observability.metrics import MetricsRegistry
from prometheus_client import CollectorRegistry

# --- identities + models ----------------------------------------------------
TENANT_A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TENANT_B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
KEY_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

MODEL_1 = "openai/gpt-4o"
MODEL_2 = "openai/gpt-4o-mini"

# --- known vectors (3-dim is plenty for cosine-threshold assertions) --------
V_A = [1.0, 0.0, 0.0]  # base prompt
V_A_NEAR = [0.98, 0.1990, 0.0]  # cosine(V_A, V_A_NEAR) ≈ 0.980 (>= 0.95 default)
V_B = [0.0, 1.0, 0.0]  # orthogonal to V_A → cosine 0.0 (< threshold)


def make_body(text: str, *, model: str = MODEL_1) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": text}]}


def _b(v: Any) -> bytes:
    if isinstance(v, bytes):
        return v
    return str(v).encode("utf-8")


def _s(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8")
    return str(v)


class FakeRedis:
    """Minimal async Redis faithful to redis.asyncio (bytes values, decode_responses=False).

    Supports the ops RedisVectorCache uses: get / set(ex) / lpush / lrange / ltrim / expire.
    `fail=True` makes every op raise (to prove the layer's fail-safe → MISS / no-op).
    """

    def __init__(self) -> None:
        self.kv: dict[bytes, bytes] = {}
        self.lists: dict[bytes, list[bytes]] = {}
        self.fail: bool = False
        self.set_calls: list[tuple[str, int | None]] = []

    def _guard(self) -> None:
        if self.fail:
            raise RuntimeError("fake redis down")

    async def get(self, k: Any) -> bytes | None:
        self._guard()
        return self.kv.get(_b(k))

    async def set(self, k: Any, v: Any, ex: int | None = None) -> None:
        self._guard()
        self.kv[_b(k)] = _b(v)
        self.set_calls.append((_s(k), ex))

    async def lpush(self, k: Any, *vals: Any) -> int:
        self._guard()
        lst = self.lists.setdefault(_b(k), [])
        for v in vals:
            lst.insert(0, _b(v))  # newest-first, like real LPUSH
        return len(lst)

    async def lrange(self, k: Any, start: int, stop: int) -> list[bytes]:
        self._guard()
        lst = self.lists.get(_b(k), [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]  # redis LRANGE stop is INCLUSIVE

    async def ltrim(self, k: Any, start: int, stop: int) -> None:
        self._guard()
        lst = self.lists.get(_b(k), [])
        self.lists[_b(k)] = lst[start : (None if stop == -1 else stop + 1)]

    async def expire(self, k: Any, ttl: int) -> None:
        self._guard()


class FakeEmbedder:
    """Maps the embedded text to a known vector. Unknown text -> None. `fail` -> None always."""

    def __init__(self, mapping: dict[str, list[float]], *, fail: bool = False) -> None:
        self.mapping = mapping
        self.fail = fail
        self.calls: list[str] = []

    async def __call__(self, text: str) -> list[float] | None:
        self.calls.append(text)
        if self.fail:
            return None
        return self.mapping.get(text)


# --- CompletionUseCase integration fakes ------------------------------------
class FakeAuthenticator:
    def __init__(
        self,
        *,
        tenant_id: uuid.UUID = TENANT_A,
        cache_enabled: bool = True,
        semantic_cache_enabled: bool = False,
    ) -> None:
        self._tenant_id = tenant_id
        self._cache_enabled = cache_enabled
        self._semantic = semantic_cache_enabled

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return AuthzResult(
            tenant_id=self._tenant_id,
            key_id=KEY_ID,
            cache_enabled=self._cache_enabled,
            semantic_cache_enabled=self._semantic,
        )


class FakeModelChecker:
    async def is_active(self, model_id: str) -> bool:
        return True


class FakeUsageRecorder:
    # Declare the cached/team_id extras so _dispatch_record forwards them (the typed-extras
    # seam: extras absent from supported_extras are dropped). Lets tests assert cached=True.
    supported_extras = frozenset({"cached", "team_id"})

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FakeResponseCache:
    """Exact-layer cache stub. `exact` maps a cache_key->body for exact hits; default MISS.

    Also satisfies the normalization-layer hasattr(get_pointer/set_pointer) probes, but with
    semantic_cache_enabled=False on the authz those branches are skipped anyway.
    """

    def __init__(self, exact: dict[str, dict[str, Any]] | None = None) -> None:
        self.store: dict[str, dict[str, Any]] = dict(exact or {})
        self.set_calls: list[str] = []

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        return self.store.get(cache_key)

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        self.store[cache_key] = body
        self.set_calls.append(cache_key)

    async def get_pointer(self, sem_key: str) -> str | None:
        return None

    async def set_pointer(self, sem_key: str, exact_key: str, ttl_seconds: int) -> None:
        return None


class FakeCompletionUpstream:
    """Returns a canned (200, body) and counts calls. No streaming used in these tests."""

    def __init__(self, body: dict[str, Any] | None = None, *, status: int = 200) -> None:
        self._body = body or {
            "id": "up",
            "choices": [{"message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        self._status = status
        self.complete_calls: list[str] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.complete_calls.append(str(payload.get("model", "")))
        return self._status, self._body


class FakeVectorCache:
    """Records lookup/store calls; `hit_body` (or None) is what lookup returns."""

    def __init__(self, hit_body: dict[str, Any] | None = None) -> None:
        self.hit_body = hit_body
        self.lookup_calls: list[dict[str, Any]] = []
        self.store_calls: list[dict[str, Any]] = []

    async def lookup(
        self, *, tenant_id: str, model: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.lookup_calls.append({"tenant_id": tenant_id, "model": model, "body": body})
        return self.hit_body

    async def store(
        self,
        *,
        tenant_id: str,
        model: str,
        body: dict[str, Any],
        response_body: dict[str, Any],
        ttl: int,
    ) -> None:
        self.store_calls.append({"tenant_id": tenant_id, "model": model, "body": body, "ttl": ttl})


def make_metrics() -> MetricsRegistry:
    return MetricsRegistry(registry=CollectorRegistry())


def cache_counter(reg: MetricsRegistry, *, result: str) -> float:
    return reg.cache_events_total.labels(result=result)._value.get()  # type: ignore[no-any-return]
