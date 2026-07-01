"""Red suite for chat-modality-guard (v56 task 5/5) — TASK.md §4.

Contract FROZEN @ v1 (chat-modality-guard TASK.md §3).
  - CatalogProviderResolver gains an OPTIONAL `modality_loader` param + `modality_for()` method —
    additive, backward compatible (the frozen provider_chat_dispatch suite's `loader`-only
    construction keeps working, unaffected).
  - CompletionUseCase gains an OPTIONAL `chat_modality_lookup` ctor param; a KNOWN, non-"chat"
    cached modality -> ERR_MODEL_MODALITY_MISMATCH (400), fired before credential
    resolution/upstream/usage. Unknown/uncached modality -> fail-open (byte-identical).
  - realtime-WS chat (`_real_chat`) wires the SAME `app.state.provider_resolver` instance in as
    `chat_modality_lookup` — no new app.state attribute, no new object.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.proxy.infrastructure.catalog_provider_resolver import CatalogProviderResolver

pytestmark = pytest.mark.asyncio

COMPLETIONS_PATH = "/v1/chat/completions"


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _assert_problem(resp: Any, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; body={body}"
    )
    assert body.get("status") == status
    return body


# ---------------------------------------------------------------------------
# CatalogProviderResolver — modality_loader / modality_for (new, additive)
# ---------------------------------------------------------------------------


async def test_resolver_modality_for_returns_cached_value_else_none() -> None:
    """modality_for returns the cached modality for a known model, None for unknown."""

    async def loader() -> dict[str, str]:
        return {"anthropic/claude": "anthropic"}

    async def modality_loader() -> dict[str, str]:
        return {"anthropic/claude": "chat", "openai/embed": "embedding"}

    resolver = CatalogProviderResolver(loader=loader, modality_loader=modality_loader)
    await resolver.refresh()

    assert await resolver.modality_for("anthropic/claude") == "chat"
    assert await resolver.modality_for("openai/embed") == "embedding"
    assert await resolver.modality_for("nobody/knows") is None  # cache miss -> fail-open signal
    # provider_for is unaffected by wiring a modality_loader alongside it.
    assert await resolver.provider_for("anthropic/claude") == "anthropic"


async def test_refresh_serializes_concurrent_calls() -> None:
    """Two concurrent refresh() calls never interleave their paired loader/modality_loader
    reads (refute-read finding, chat-modality-guard VERIFY): without a lock, main.py's SHARED
    closure-scoped modality cache could be torn between two overlapping refresh() cycles (e.g.
    overlapping /internal/catalog/sync calls), leaving _map and _modality_map populated from
    two DIFFERENT cycles. Repro: a loader that yields control mid-call (await asyncio.sleep(0))
    must never see a second refresh()'s loader/modality_loader run at the same time.
    """
    in_flight = 0
    max_concurrent = 0

    async def loader() -> dict[str, str]:
        nonlocal in_flight, max_concurrent
        in_flight += 1
        max_concurrent = max(max_concurrent, in_flight)
        await asyncio.sleep(0)  # yield — lets an unguarded concurrent refresh() interleave here
        in_flight -= 1
        return {"m": "p"}

    async def modality_loader() -> dict[str, str]:
        nonlocal in_flight, max_concurrent
        in_flight += 1
        max_concurrent = max(max_concurrent, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return {"m": "mod"}

    resolver = CatalogProviderResolver(loader=loader, modality_loader=modality_loader)

    await asyncio.gather(resolver.refresh(), resolver.refresh(), resolver.refresh())

    assert max_concurrent == 1, (
        f"expected refresh() to serialize loader calls, but {max_concurrent} ran concurrently"
    )


async def test_resolver_modality_for_none_when_no_modality_loader_wired() -> None:
    """OLD-style construction (loader only, mirrors the frozen provider_chat_dispatch suite)
    keeps working unchanged: modality_for always returns None (fail-open), provider_for intact."""

    async def loader() -> dict[str, str]:
        return {"anthropic/claude": "anthropic", "google/gemini": "google"}

    resolver = CatalogProviderResolver(loader=loader)  # no modality_loader — byte-identical ctor
    await resolver.refresh()

    assert await resolver.modality_for("anthropic/claude") is None
    assert await resolver.provider_for("anthropic/claude") == "anthropic"
    assert await resolver.provider_for("google/gemini") == "google"
    assert await resolver.provider_for("nobody/knows") == "openrouter"


# ---------------------------------------------------------------------------
# HTTP chat — coarse modality guard
# ---------------------------------------------------------------------------


async def test_chat_to_compatible_model_byte_identical(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """A chat model, cached with modality="chat" -> proceeds exactly as today."""
    from tests.chat_modality_guard.conftest import inject_fake_completion_upstream, seed_chat_model

    model_id = "chat-compatible-model"
    await seed_chat_model(db_session, model_id=model_id, modality="chat")
    await app.state.provider_resolver.refresh()
    fake_upstream = inject_fake_completion_upstream(app)

    body = {"model": model_id, "messages": [{"role": "user", "content": "hello"}]}
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert fake_upstream.calls == 1


async def test_chat_to_known_incompatible_model_rejected(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """A model whose CACHED modality is known and != "chat" -> 400, before upstream/billing."""
    from tests.chat_modality_guard.conftest import (
        SpyRecorder,
        inject_fake_completion_upstream,
        seed_chat_model,
    )

    model_id = "embedding-not-chat-model"
    await seed_chat_model(db_session, model_id=model_id, modality="embedding")
    await app.state.provider_resolver.refresh()
    fake_upstream = inject_fake_completion_upstream(app)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    body = {"model": model_id, "messages": [{"role": "user", "content": "hello"}]}
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    _assert_problem(resp, 400, "ERR_MODEL_MODALITY_MISMATCH")
    assert fake_upstream.calls == 0, "upstream MUST NOT be called on rejected request"
    assert spy.call_count == 0, "usage row MUST NOT be recorded on rejected request"


async def test_chat_to_unknown_modality_model_is_fail_open(
    app: Any,
    client: Any,
    db_session: Any,
    api_key_info: dict[str, str],
) -> None:
    """A model seeded AFTER the resolver's last refresh (cache miss) -> fail-open, proceeds.

    Confirms the guard is safe by construction for every other suite's tests: they never
    refresh the resolver after seeding a model, so modality stays uncached there too.
    """
    from tests.chat_modality_guard.conftest import inject_fake_completion_upstream, seed_chat_model

    model_id = "not-yet-synced-model"
    await seed_chat_model(db_session, model_id=model_id, modality="chat")
    # Deliberately do NOT call app.state.provider_resolver.refresh() — simulates the
    # stale-cache window right after a new model is added but before the next sync.
    fake_upstream = inject_fake_completion_upstream(app)

    body = {"model": model_id, "messages": [{"role": "user", "content": "hello"}]}
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    assert resp.status_code == 200, f"expected 200 (fail-open), got {resp.status_code}: {resp.text}"
    assert fake_upstream.calls == 1


# ---------------------------------------------------------------------------
# Realtime-WS chat (_real_chat wiring)
# ---------------------------------------------------------------------------


async def test_realtime_ws_real_chat_wires_chat_modality_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_real_chat must thread the SAME app.state.provider_resolver instance into
    CompletionUseCase as chat_modality_lookup= — proving realtime-WS inherits the guard with
    no new app.state attribute and no new object (mirrors preset-resolution-ingress's WIRING
    test for _real_chat, which proved the same shape for provider_resolver/tenant_model_preset_store).
    """
    from tests.preset_resolution_ingress.test_preset_resolution_ingress import (
        FakeUsageRecorder,
    )

    from gateway.proxy.api import realtime_ws

    _resolver_sentinel = object()
    captured_kwargs: dict[str, Any] = {}

    class _RecordingCompletionUseCase:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        async def complete(self, **kwargs: Any) -> tuple[int, dict[str, Any], str | None]:
            return 200, {"choices": [{"message": {"content": "resolved-ok"}}]}, None

    monkeypatch.setattr(
        "gateway.proxy.application.use_cases.CompletionUseCase",
        _RecordingCompletionUseCase,
    )

    class _FakeAppState:
        def __init__(self) -> None:
            self.budget_guard = object()
            self.rate_limiter = None
            self.span_emitter = None
            self.stream_resilience_enabled = False
            self.tenant_credential_resolver = None
            self.provider_resolver = _resolver_sentinel
            self.cost_recovery_service = None
            self.bandwidth_bucket = None

            class _Settings:
                bandwidth_max_wait_seconds = 0.0
                input_modality_guard_enabled = False

            self.settings = _Settings()
            self.circuit_breaker = object()
            self.completion_upstream = object()
            self.usage_recorder = FakeUsageRecorder()
            self.model_router = None
            self.tenant_model_preset_store = None

            class _SessionCtx:
                async def __aenter__(self) -> object:
                    return object()

                async def __aexit__(self, *exc: object) -> None:
                    return None

            self.sessionmaker = _SessionCtx

    class _FakeApp:
        def __init__(self) -> None:
            self.state = _FakeAppState()

    class _FakeWebSocket:
        def __init__(self) -> None:
            self.app = _FakeApp()

    websocket = _FakeWebSocket()

    reply = await realtime_ws._real_chat(  # pyright: ignore[reportPrivateUsage]
        "hello there",
        "some-model",
        "sk-test",
        websocket=websocket,  # type: ignore[arg-type]
    )

    assert reply == "resolved-ok"
    assert captured_kwargs.get("chat_modality_lookup") is _resolver_sentinel, (
        "the SAME app.state.provider_resolver instance must reach CompletionUseCase as "
        "chat_modality_lookup — no new app.state attribute, no new object"
    )
    assert captured_kwargs.get("provider_resolver") is _resolver_sentinel
