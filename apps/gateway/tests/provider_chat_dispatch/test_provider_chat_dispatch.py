"""Red suite for provider-chat-dispatch (v9 task 1/4) — TASK.md §4.

Tests the DISPATCH seam only (provider resolution + CompletionUpstream adapter
selection + the byte-identical openrouter default). NO real Anthropic/Gemini call:
the per-provider translations are the later provider tasks. Fakes are used for the
resolver (scripted model->provider) and the adapters (spies).

Contract: TASK.md §3 (FROZEN @ v1).
  - ProviderResolver Protocol: async provider_for(model_id) -> str (default "openrouter").
  - ProviderAwareCompletionUpstream(adapters, resolver, default_provider="openrouter")
    implements CompletionUpstream (complete + stream); selection only, no extra behavior.
  - CatalogProviderResolver(loader): cached model->provider map; fail-safe -> "openrouter".
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

# These imports are RED until BUILD creates the modules/symbols.
from gateway.proxy.infrastructure.catalog_provider_resolver import CatalogProviderResolver
from gateway.proxy.infrastructure.provider_aware_upstream import (
    ProviderAwareCompletionUpstream,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """A CompletionUpstream spy. Records calls; returns an OpenAI-shaped body."""

    def __init__(self, name: str, status: int = 200) -> None:
        self.name = name
        self._status = status
        self.complete_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        self.complete_calls.append(dict(payload))
        return (
            self._status,
            {
                "id": f"{self.name}-resp",
                "object": "chat.completion",
                "model": payload.get("model"),
                "_served_by": self.name,
            },
        )

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield f'data: {{"_served_by":"{self.name}"}}\n\n'.encode()
            yield b"data: [DONE]\n\n"

        return _gen()


class _FakeResolver:
    """A ProviderResolver returning a scripted provider per model id."""

    def __init__(self, mapping: dict[str, str], default: str = "openrouter") -> None:
        self._mapping = mapping
        self._default = default

    async def provider_for(self, model_id: str) -> str:
        return self._mapping.get(model_id, self._default)


def _make(adapters: dict[str, _FakeAdapter], resolver: _FakeResolver) -> ProviderAwareCompletionUpstream:
    return ProviderAwareCompletionUpstream(adapters=adapters, resolver=resolver)


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# Dispatch — complete()
# ---------------------------------------------------------------------------


async def test_openrouter_default_byte_identical() -> None:
    """A model resolving to 'openrouter' (and an unset/default model) hits the openrouter adapter only."""
    orouter, fake = _FakeAdapter("openrouter"), _FakeAdapter("fake")
    disp = _make({"openrouter": orouter, "fake": fake}, _FakeResolver({"or/m": "openrouter"}))

    status, body = await disp.complete({"model": "or/m", "messages": []})

    assert status == 200
    assert body["_served_by"] == "openrouter"
    assert len(orouter.complete_calls) == 1
    assert fake.complete_calls == []  # the other adapter never touched


async def test_dispatch_routes_by_provider() -> None:
    """complete() delegates to the adapter for the resolved provider; served model id intact."""
    orouter, fake = _FakeAdapter("openrouter"), _FakeAdapter("fake")
    disp = _make({"openrouter": orouter, "fake": fake}, _FakeResolver({"x/m": "fake"}))

    status, body = await disp.complete({"model": "x/m", "messages": []})

    assert status == 200
    assert body["_served_by"] == "fake"
    assert body["model"] == "x/m"  # served id passed through unchanged (billing keys on it)
    assert fake.complete_calls[0]["model"] == "x/m"
    assert orouter.complete_calls == []


async def test_per_candidate_resolution() -> None:
    """Resolution is per-call: the router rewrites payload['model'] per fallback candidate."""
    orouter, fake = _FakeAdapter("openrouter"), _FakeAdapter("fake")
    disp = _make({"openrouter": orouter, "fake": fake}, _FakeResolver({"or/a": "openrouter", "fake/b": "fake"}))

    await disp.complete({"model": "or/a"})   # first candidate
    await disp.complete({"model": "fake/b"})  # rewritten to the second candidate

    assert [c["model"] for c in orouter.complete_calls] == ["or/a"]
    assert [c["model"] for c in fake.complete_calls] == ["fake/b"]


async def test_unknown_provider_falls_back_to_openrouter() -> None:
    """A provider absent from the adapter map (e.g. empty-key → not registered) → openrouter, never 500."""
    orouter = _FakeAdapter("openrouter")
    # resolver says 'anthropic' but the map has no 'anthropic' adapter (key was empty)
    disp = _make({"openrouter": orouter}, _FakeResolver({"a/m": "anthropic"}))

    status, body = await disp.complete({"model": "a/m"})

    assert status == 200
    assert body["_served_by"] == "openrouter"  # fail-safe fallback
    assert len(orouter.complete_calls) == 1


async def test_dispatch_adds_no_extra_behavior() -> None:
    """Dispatch delegates exactly once — no retry/duplication of its own."""
    fake = _FakeAdapter("fake", status=503)
    disp = _make({"openrouter": _FakeAdapter("openrouter"), "fake": fake}, _FakeResolver({"x/m": "fake"}))

    status, _ = await disp.complete({"model": "x/m"})

    assert status == 503  # passed through, not retried/swallowed
    assert len(fake.complete_calls) == 1


# ---------------------------------------------------------------------------
# Dispatch — stream()
# ---------------------------------------------------------------------------


async def test_stream_dispatch() -> None:
    """stream() resolves the provider (inside the generator) and yields the adapter's chunks."""
    orouter, fake = _FakeAdapter("openrouter"), _FakeAdapter("fake")
    disp = _make({"openrouter": orouter, "fake": fake}, _FakeResolver({"x/m": "fake"}))

    chunks = await _drain(disp.stream({"model": "x/m"}))

    assert b'"_served_by":"fake"' in chunks[0]
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert len(fake.stream_calls) == 1
    assert orouter.stream_calls == []


async def test_stream_default_openrouter() -> None:
    """An unset/openrouter model streams through the openrouter adapter (byte-identical path)."""
    orouter, fake = _FakeAdapter("openrouter"), _FakeAdapter("fake")
    disp = _make({"openrouter": orouter, "fake": fake}, _FakeResolver({}))  # default → openrouter

    chunks = await _drain(disp.stream({"model": "unknown/m"}))

    assert b'"_served_by":"openrouter"' in chunks[0]
    assert len(orouter.stream_calls) == 1


# ---------------------------------------------------------------------------
# CatalogProviderResolver
# ---------------------------------------------------------------------------


async def test_resolver_returns_mapped_provider_else_openrouter() -> None:
    """provider_for returns the cached provider for a known model, 'openrouter' for unknown."""
    async def loader() -> dict[str, str]:
        return {"anthropic/claude": "anthropic", "google/gemini": "google"}

    resolver = CatalogProviderResolver(loader=loader)
    await resolver.refresh()

    assert await resolver.provider_for("anthropic/claude") == "anthropic"
    assert await resolver.provider_for("google/gemini") == "google"
    assert await resolver.provider_for("nobody/knows") == "openrouter"  # default


async def test_resolver_failsafe_on_loader_error() -> None:
    """A raising loader never propagates — provider_for degrades to 'openrouter'."""
    async def boom() -> dict[str, str]:
        raise RuntimeError("catalog unavailable")

    resolver = CatalogProviderResolver(loader=boom)
    await resolver.refresh()  # must not raise

    assert await resolver.provider_for("anything") == "openrouter"


async def test_resolver_refresh_updates_map() -> None:
    """A refresh swaps in the new map (catalog sync hook)."""
    state = {"map": {"m": "anthropic"}}

    async def loader() -> dict[str, str]:
        return state["map"]

    resolver = CatalogProviderResolver(loader=loader)
    await resolver.refresh()
    assert await resolver.provider_for("m") == "anthropic"

    state["map"] = {"m": "google"}
    await resolver.refresh()
    assert await resolver.provider_for("m") == "google"
