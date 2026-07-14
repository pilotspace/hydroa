"""Red/green regression suite for audit-remediation package C1 (MED proxy global
breaker): `get_completion_upstream` (proxy/api/deps.py) used to wrap EVERY provider
in the SAME `app.state.circuit_breaker` instance. Five consecutive 5xx from one
provider tripped that single breaker and then silently blocked every OTHER
provider too — a success on provider X was never required to unblock provider Y,
but the bug meant provider Y could never even be TRIED once X's breaker opened.

Pure unit-level suite against `get_completion_upstream` directly (duck-typed
Request/app.state stand-ins — the function only reads `request.app.state.*`, so a
real FastAPI Request is unnecessary). No HTTP/DB required; fast and isolates the
defect precisely at its exact fix location.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from gateway.proxy.api.deps import get_completion_upstream
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker


class _MultiProviderFakeDelegate:
    """Fails (500) for "model-a", succeeds (200) for every other model."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        model = str(payload["model"])
        self.calls[model] = self.calls.get(model, 0) + 1
        if model == "model-a":
            return 500, {"error": "boom"}
        return 200, {"ok": True}

    def stream(self, payload: dict[str, Any]) -> Any:
        raise NotImplementedError


class _FakeProviderResolver:
    """Mirrors CatalogProviderResolver's contract: never raises, in-memory only."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    async def provider_for(self, model_id: str) -> str:
        return self._mapping.get(model_id, "openrouter")


def _make_request(
    delegate: Any,
    *,
    resolver: Any = None,
    breakers: dict[str, CircuitBreaker] | None = None,
    legacy_breaker: CircuitBreaker | None = None,
) -> Any:
    """Duck-typed stand-in for FastAPI's Request — only `.app.state.*` is read."""
    state = types.SimpleNamespace(
        completion_upstream=delegate,
        circuit_breaker=legacy_breaker if legacy_breaker is not None else CircuitBreaker(),
    )
    if resolver is not None:
        state.provider_resolver = resolver
    if breakers is not None:
        state.provider_circuit_breakers = breakers
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(app=app)


async def test_provider_a_breaker_trip_does_not_block_provider_b() -> None:
    """The core fix: tripping provider A's breaker must not short-circuit provider B."""
    delegate = _MultiProviderFakeDelegate()
    resolver = _FakeProviderResolver({"model-a": "anthropic", "model-b": "openai"})
    breakers: dict[str, CircuitBreaker] = {}
    request = _make_request(delegate, resolver=resolver, breakers=breakers)

    upstream = get_completion_upstream(request)

    # 5 consecutive failures on provider "anthropic" (model-a) — each reaches the
    # delegate and raises UpstreamUnavailableError (breaker still CLOSED while
    # counting; trips OPEN only once the 5th failure is recorded).
    for _ in range(5):
        with pytest.raises(UpstreamUnavailableError):
            await upstream.complete({"model": "model-a"})
    assert delegate.calls["model-a"] == 5

    # 6th call for model-a: provider "anthropic"'s breaker is now OPEN — short-circuited
    # BEFORE reaching the delegate (no new delegate call).
    with pytest.raises(CircuitOpenError):
        await upstream.complete({"model": "model-a"})
    assert delegate.calls["model-a"] == 5, "6th call must be short-circuited, not reach delegate"

    # Provider "openai" (model-b) has had ZERO failures — it must NOT be blocked by
    # provider "anthropic"'s open breaker. This is the exact defect: under the old
    # single global app.state.circuit_breaker, this call would raise CircuitOpenError
    # and never reach the delegate.
    status, body = await upstream.complete({"model": "model-b"})
    assert status == 200
    assert body == {"ok": True}
    assert delegate.calls.get("model-b") == 1, "model-b must reach the delegate — its own breaker is CLOSED"


async def test_unknown_provider_gets_a_fresh_closed_breaker_never_a_keyerror() -> None:
    """Design-for-failure: an unseen provider key must never KeyError the registry."""
    delegate = _MultiProviderFakeDelegate()
    resolver = _FakeProviderResolver({})  # empty map -> resolver falls back to "openrouter"
    breakers: dict[str, CircuitBreaker] = {}
    request = _make_request(delegate, resolver=resolver, breakers=breakers)

    upstream = get_completion_upstream(request)

    status, _body = await upstream.complete({"model": "never-seen-model"})

    assert status == 200
    assert breakers, "a fresh breaker must have been lazily created for the resolved provider"


async def test_missing_provider_registry_falls_back_to_legacy_single_breaker() -> None:
    """Fail-safe: an app.state without the new per-provider registry (e.g. a stripped
    test double) must not crash — it falls back to the stable legacy
    app.state.circuit_breaker, preserving today's behavior exactly.
    """
    delegate = _MultiProviderFakeDelegate()
    request = _make_request(delegate)  # no resolver, no breakers dict

    upstream = get_completion_upstream(request)
    status, _body = await upstream.complete({"model": "model-b"})

    assert status == 200
