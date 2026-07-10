"""Suite-local fixtures for cache-alias-billing tests (B6 revenue leak — TASK.md §4).

A non-streaming chat request that HITS the response cache (exact / semantic / vector
layer) through a model-group ALIAS currently bills usage keyed on the ALIAS string
(``_fire_record_cached(..., model=model_id, ...)`` at all three hit sites in
``CompletionUseCase.complete``). An alias has no ``pricing_snapshots`` row → cost
resolves to $0, silently. The MISS path already bills the served candidate (frozen
F7); this suite extends that invariant to the three cache-HIT paths.

Fakes only — NO DB/Redis (mirrors ``tests/stream_alias_billing/`` (B1) and
``tests/vector_cache/`` conventions):
  - ``FakeAuthenticator`` / ``FakeModelChecker`` / ``TENANT_A`` / ``FakeCompletionUpstream``
    / ``FakeVectorCache`` are REUSED verbatim from ``tests.vector_cache.conftest`` (already
    support ``cache_enabled`` / ``semantic_cache_enabled`` and the vector-hit shape).
  - ``ALIAS`` / ``CAND_A`` / ``CAND_B`` / ``make_payload`` are REUSED from
    ``tests.streaming_resilience.conftest``.
  - ``MarkerSpyRecorder`` / ``billed_records`` / ``settle`` are REUSED from the B1 suite
    (``tests.stream_alias_billing.conftest`` and ``tests.stream_usage_completeness.conftest``)
    — generic, not streaming-specific.
  - ``FakeResponseCache`` is written HERE (not reused) because the existing
    ``tests.vector_cache.conftest.FakeResponseCache`` stubs ``get_pointer``/``set_pointer``
    to always return ``None`` (those tests never exercise the semantic-pointer path). This
    suite needs REAL in-memory pointer semantics to drive a genuine semantic-cache HIT.

Reserved stamp key under test (TASK.md §3 CONTRACT — not yet implemented in src/, hence
defined here as a plain literal, never imported from gateway.*):
  STAMP = "__hydroa_served_model__"
"""

from __future__ import annotations

import copy
from typing import Any

from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase

from tests.stream_alias_billing.conftest import (  # noqa: F401
    MarkerSpyRecorder,
    billed_records,
    settle,
)
from tests.streaming_resilience.conftest import (  # noqa: F401
    ALIAS,
    CAND_A,
    CAND_B,
    make_payload,
)
from tests.vector_cache.conftest import (  # noqa: F401
    KEY_ID,
    TENANT_A,
    FakeCompletionUpstream,
    FakeModelChecker,
    FakeVectorCache,
)

# --- the reserved stamp key (TASK.md §3 CONTRACT) ---------------------------
STAMP = "__hydroa_served_model__"


# --- authenticator (local: needs cache_enabled=True by default per Ground §0) -----
class FakeAuthenticator:
    """KeyAuthenticator stub with cache/semantic-cache toggles.

    cache_enabled defaults to True — TASK.md §0 Ground: "authz.cache_enabled must
    be true" for this suite (every test exercises the cache path).
    """

    def __init__(
        self,
        *,
        tenant_id: Any = TENANT_A,
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


# --- response cache fake with REAL pointer semantics -------------------------
class FakeResponseCache:
    """Exact + semantic-pointer cache stub with real in-memory dict semantics.

    Unlike ``tests.vector_cache.conftest.FakeResponseCache`` (whose get_pointer/
    set_pointer are permanent stubs), this fake actually resolves a semantic
    pointer to its exact-cache entry — required to drive a genuine SEMANTIC HIT
    (scenario 2) end-to-end through ``CompletionUseCase.complete``.
    """

    def __init__(
        self,
        exact: dict[str, dict[str, Any]] | None = None,
        pointers: dict[str, str] | None = None,
    ) -> None:
        self.store: dict[str, dict[str, Any]] = dict(exact or {})
        self.pointers: dict[str, str] = dict(pointers or {})
        self.set_calls: list[str] = []
        self.pointer_set_calls: list[tuple[str, str]] = []

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        return self.store.get(cache_key)

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        self.store[cache_key] = body
        self.set_calls.append(cache_key)

    async def get_pointer(self, sem_key: str) -> str | None:
        return self.pointers.get(sem_key)

    async def set_pointer(self, sem_key: str, exact_key: str, ttl_seconds: int) -> None:
        self.pointers[sem_key] = exact_key
        self.pointer_set_calls.append((sem_key, exact_key))


def stamped_body(
    *,
    served: str,
    model_field: str | None = None,
    usage: dict[str, Any] | None = None,
    resp_id: str = "resp-cached",
) -> dict[str, Any]:
    """Build a cached response body carrying the reserved STAMP key.

    ``model_field`` deliberately defaults to a value DIFFERENT from ``served``
    (an OpenRouter ":free"-style variant) when not given, so a test asserting
    "billed == served" can only pass if the (future) fix reads the STAMP first —
    never accidentally passes by coincidentally reading cached["model"] instead
    (TASK.md §1 ⚠ assumption: provider-returned model may differ from the
    catalog id).
    """
    return {
        "id": resp_id,
        "model": model_field if model_field is not None else f"{served}:free",
        "choices": [{"message": {"role": "assistant", "content": "cached"}}],
        "usage": usage
        if usage is not None
        else {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        STAMP: served,
    }


def legacy_body(*, served: str, resp_id: str = "resp-legacy") -> dict[str, Any]:
    """Build a PRE-FIX cached body: carries cached["model"] but NO stamp key at all."""
    return {
        "id": resp_id,
        "model": served,
        "choices": [{"message": {"role": "assistant", "content": "legacy cached"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def make_router() -> FallbackModelRouter:
    """Alias 'fast' -> [CAND_A, CAND_B], OrderedStrategy (deterministic: routes to CAND_A)."""
    return FallbackModelRouter(model_groups={ALIAS: [CAND_A, CAND_B]})


def make_use_case(
    *,
    response_cache: Any,
    vector_cache: Any = None,
    cache_enabled: bool = True,
    semantic_cache_enabled: bool = False,
) -> CompletionUseCase:
    return CompletionUseCase(
        FakeAuthenticator(
            cache_enabled=cache_enabled, semantic_cache_enabled=semantic_cache_enabled
        ),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        response_cache=response_cache,
        vector_cache=vector_cache,
    )


async def run_complete(
    uc: CompletionUseCase,
    upstream: Any,
    recorder: Any,
    body: dict[str, Any],
    *,
    router: FallbackModelRouter | None = None,
) -> tuple[int, dict[str, Any], str | None]:
    """Drive a single alias completion request through the use case."""
    return await uc.complete(
        raw_key="sk-test",
        body=body,
        upstream=upstream,
        usage_recorder=recorder,
        model_router=router if router is not None else make_router(),
    )


__all__ = [
    "ALIAS",
    "CAND_A",
    "CAND_B",
    "STAMP",
    "TENANT_A",
    "FakeAuthenticator",
    "FakeCompletionUpstream",
    "FakeModelChecker",
    "FakeResponseCache",
    "FakeVectorCache",
    "MarkerSpyRecorder",
    "billed_records",
    "copy",
    "legacy_body",
    "make_payload",
    "make_router",
    "make_use_case",
    "run_complete",
    "settle",
    "stamped_body",
]
