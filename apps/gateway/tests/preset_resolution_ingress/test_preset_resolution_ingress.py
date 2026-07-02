"""Failing-first (RED) suite for preset-resolution-ingress (v56 §4, TASK.md).

Resolves a ``<preset>:<alias>`` selector in the ``model`` field of all 5 entry points
(chat complete/stream, images, embeddings, audio STT, audio TTS — realtime-WS voice
chat is covered for free via ``_real_chat``'s delegation to ``CompletionUseCase.complete``)
to a tenant-specific target model, BEFORE any authorization/catalog/budget/billing
logic or upstream provider call.

Pure unit tests: every collaborator (TenantModelPresetStore, KeyAuthenticator,
NonChatGovernance, provider adapter, session, model router) is a hand-written fake
defined in this module — no real DB, no real network, no HTTP client. Assertions
target OBSERVABLE behavior (what reaches the fake router/provider/governance),
never internals.

TRUE-RED RULE (at write time): imports `parse_preset_selector` from
`gateway.proxy.domain.model_presets` (does not exist yet) and `PRESET_NOT_FOUND` from
`gateway.core.error_catalog` (does not exist yet) → collection fails with ImportError.
Once those + the new constructor params exist, each test asserts the scenario's
contracted behavior (TASK.md §2 SCENARIOS).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest

from gateway.core.errors import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.domain.ports import ModelAccess

# ---------------------------------------------------------------------------
# Modules under test — parse_preset_selector / PRESET_NOT_FOUND do not exist yet;
# their absence IS the red target (ImportError at collection).
# ---------------------------------------------------------------------------
from gateway.core.error_catalog import PRESET_NOT_FOUND  # type: ignore[import]
from gateway.proxy.application.audio_use_case import SpeechUseCase, TranscriptionUseCase
from gateway.proxy.application.embeddings_use_case import EmbeddingsUseCase
from gateway.proxy.application.images_use_case import ImagesUseCase
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.model_presets import parse_preset_selector  # type: ignore[import]
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Fakes — no real DB / network (TASK.md §4 constraint)
# ---------------------------------------------------------------------------


class FakePresetStore:
    """In-memory TenantModelPresetStore fake. Records every resolve() call."""

    def __init__(self) -> None:
        self._data: dict[tuple[uuid.UUID, str, str], str] = {}
        self.resolve_calls: list[tuple[uuid.UUID, str, str]] = []

    def seed(
        self, tenant_id: uuid.UUID, preset_name: str, alias_key: str, target_model: str
    ) -> None:
        self._data[(tenant_id, preset_name, alias_key)] = target_model

    async def resolve(self, tenant_id: uuid.UUID, preset_name: str, alias_key: str) -> str | None:
        self.resolve_calls.append((tenant_id, preset_name, alias_key))
        return self._data.get((tenant_id, preset_name, alias_key))

    # Full TenantModelPresetStore Protocol conformance (upsert/list/delete are unused by
    # these ingress tests — the store layer itself is covered by tenant_model_presets/).
    async def upsert(
        self, tenant_id: uuid.UUID, preset_name: str, alias_key: str, target_model: str
    ) -> None:
        raise NotImplementedError("not exercised by preset-resolution-ingress tests")

    async def list(self, tenant_id: uuid.UUID) -> list[Any]:
        raise NotImplementedError("not exercised by preset-resolution-ingress tests")

    async def delete(self, tenant_id: uuid.UUID, preset_name: str, alias_key: str) -> bool:
        raise NotImplementedError("not exercised by preset-resolution-ingress tests")


class FakeAuthenticator:
    """Fake KeyAuthenticator — returns a fixed AuthzResult for any non-empty raw_key."""

    def __init__(self, tenant_id: uuid.UUID, key_id: uuid.UUID | None = None) -> None:
        self.tenant_id = tenant_id
        self.key_id = key_id or uuid.uuid4()
        self.calls: list[str] = []

    async def authenticate(self, raw_key: str) -> AuthzResult:
        self.calls.append(raw_key)
        return AuthzResult(tenant_id=self.tenant_id, key_id=self.key_id)


class FakeModelChecker:
    """Fake ModelChecker — every model is active (catalog check is not this task's concern)."""

    async def is_active(self, model_id: str) -> bool:
        return True

    async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
        return ModelAccess.ACTIVE


class FakeGovernance:
    """Fake NonChatGovernance — records authorize() calls, returns a fixed AuthzResult."""

    def __init__(self, tenant_id: uuid.UUID, key_id: uuid.UUID | None = None) -> None:
        self.tenant_id = tenant_id
        self.key_id = key_id or uuid.uuid4()
        self.authorize_calls: list[tuple[str | None, str, int | None]] = []

    async def authorize(
        self, raw_key: str | None, model_id: str, *, estimated_tokens: int | None = None
    ) -> AuthzResult:
        self.authorize_calls.append((raw_key, model_id, estimated_tokens))
        return AuthzResult(tenant_id=self.tenant_id, key_id=self.key_id)


class _FakeCatalogRow:
    def __init__(self, modality: str, provider: str) -> None:
        self.modality = modality
        self.provider = provider


class _FakeResult:
    def __init__(self, row: _FakeCatalogRow | None) -> None:
        self._row = row

    def one_or_none(self) -> _FakeCatalogRow | None:
        return self._row


class FakeSession:
    """Fake AsyncSession — ignores the statement, always returns the seeded catalog row.

    The non-chat use cases only use the session for one query (modality + provider);
    this task never touches that query, so a canned row over any stmt is sufficient.
    """

    def __init__(self, modality: str = "text", provider: str = "fake-provider") -> None:
        self._row: _FakeCatalogRow | None = _FakeCatalogRow(modality, provider)

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._row)


class FakeProviderAdapter:
    """Fake UpstreamProvider — records exactly what reaches the 'upstream' call."""

    def __init__(self) -> None:
        self.post_json_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_multipart_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.stream_bytes_calls: list[tuple[str, dict[str, Any]]] = []
        self.json_response: tuple[int, dict[str, Any]] = (200, {"data": [{"url": "x"}]})
        self.multipart_response: tuple[int, dict[str, Any]] = (200, {"text": "hello"})

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.post_json_calls.append((path, dict(payload)))
        return self.json_response

    async def post_multipart(
        self, path: str, files: dict[str, Any], data: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        self.post_multipart_calls.append((path, dict(files), dict(data)))
        return self.multipart_response

    def stream_bytes(self, path: str, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_bytes_calls.append((path, dict(payload)))

        async def _gen() -> AsyncIterator[bytes]:
            yield b"audio-bytes"

        return _gen()


class FakeCompletionUpstream:
    """Fake CompletionUpstream — records the body reaching the completion call."""

    def __init__(self) -> None:
        self.complete_calls: list[dict[str, Any]] = []
        self.response: tuple[int, dict[str, Any]] = (
            200,
            {"choices": [{"message": {"content": "hi"}}]},
        )

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.complete_calls.append(dict(payload))
        return self.response

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        raise NotImplementedError("not exercised by these tests")


class FakeModelRouter:
    """Minimal FallbackModelRouter stand-in.

    Mirrors the REAL router's ground-truth behavior (TASK.md §0 GROUND): it
    independently re-reads ``payload.get("model", "")`` off the SAME body dict rather
    than accepting a model_id parameter — proving a single ingress rewrite of
    ``body["model"]`` is sufficient with no second wiring point.
    """

    def __init__(self) -> None:
        self.model_groups: dict[str, list[str]] = {}
        self.complete_calls: list[dict[str, Any]] = []

    def candidates_for(self, model_id: str) -> list[str] | None:
        return self.model_groups.get(model_id)

    async def complete(
        self, body: dict[str, Any], *, upstream: FakeCompletionUpstream
    ) -> tuple[int, dict[str, Any], str]:
        served_model_id = body.get("model", "")
        self.complete_calls.append(dict(body))
        status, resp_body = await upstream.complete(body)
        return status, resp_body, served_model_id


class FakeUsageRecorder:
    """Fire-and-forget usage recorder fake — swallows everything, records nothing needed."""

    async def record(self, **kwargs: Any) -> None:
        return None


class FakeUploadFile:
    """Fake starlette UploadFile — filename/content_type attrs + async .read()."""

    def __init__(
        self,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        content: bytes = b"fake-audio-bytes",
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_tenant_id() -> uuid.UUID:
    return uuid.uuid4()


def make_registry(adapter: FakeProviderAdapter, name: str = "fake-provider") -> ProviderRegistry:
    return ProviderRegistry({name: adapter})


# ---------------------------------------------------------------------------
# parse_preset_selector — pure function unit tests
# ---------------------------------------------------------------------------


def test_parse_preset_selector_bare_id_returns_none() -> None:
    """No colon → None (bare model id, existing behavior untouched)."""
    assert parse_preset_selector("gpt5-5") is None


def test_parse_preset_selector_empty_string_returns_none() -> None:
    assert parse_preset_selector("") is None


def test_parse_preset_selector_single_colon_splits_on_first() -> None:
    assert parse_preset_selector("cheap:opus") == ("cheap", "opus")


def test_parse_preset_selector_multiple_colons_alias_key_keeps_rest() -> None:
    """Splits on the FIRST colon only — alias_key absorbs any remaining colons."""
    assert parse_preset_selector("cheap:opus:extra") == ("cheap", "opus:extra")


# ---------------------------------------------------------------------------
# Chat (CompletionUseCase.complete) — scenarios
# ---------------------------------------------------------------------------


async def test_chat_bare_model_id_unaffected(tenant_id: uuid.UUID) -> None:
    """Bare model id, any/no presets configured -> unchanged, resolve() never called."""
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "opus", "gpt5-5-mini")  # unrelated row present
    authenticator = FakeAuthenticator(tenant_id)
    uc = CompletionUseCase(
        authenticator=authenticator,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=store,
    )
    upstream = FakeCompletionUpstream()
    router = FakeModelRouter()
    body: dict[str, Any] = {
        "model": "gpt5-5",
        "messages": [{"role": "user", "content": "hi"}],
    }

    status, _resp, _x_cache = await uc.complete(
        raw_key="sk-test",
        body=body,
        upstream=upstream,
        usage_recorder=FakeUsageRecorder(),
        model_router=cast(Any, router),
    )

    assert status == 200
    assert router.complete_calls[0]["model"] == "gpt5-5"
    assert upstream.complete_calls[0]["model"] == "gpt5-5"
    assert store.resolve_calls == [], "no colon in model -> resolve() must never be called"


async def test_chat_colon_selector_resolves_before_router(tenant_id: uuid.UUID) -> None:
    """cheap:opus -> gpt5-5-mini; router AND provider both see the resolved target."""
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "opus", "gpt5-5-mini")
    authenticator = FakeAuthenticator(tenant_id)
    uc = CompletionUseCase(
        authenticator=authenticator,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=store,
    )
    upstream = FakeCompletionUpstream()
    router = FakeModelRouter()
    body: dict[str, Any] = {
        "model": "cheap:opus",
        "messages": [{"role": "user", "content": "hi"}],
    }

    status, _resp, _x_cache = await uc.complete(
        raw_key="sk-test",
        body=body,
        upstream=upstream,
        usage_recorder=FakeUsageRecorder(),
        model_router=cast(Any, router),
    )

    assert status == 200
    assert router.complete_calls[0]["model"] == "gpt5-5-mini"
    assert upstream.complete_calls[0]["model"] == "gpt5-5-mini"
    # The literal selector string must never reach the provider.
    assert "cheap:opus" not in upstream.complete_calls[0].values()
    assert store.resolve_calls == [(tenant_id, "cheap", "opus")]


async def test_chat_stream_colon_selector_resolves_before_router(tenant_id: uuid.UUID) -> None:
    """Same resolution, exercised through .stream() — covers the SECOND insertion point."""
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "opus", "gpt5-5-mini")
    authenticator = FakeAuthenticator(tenant_id)
    uc = CompletionUseCase(
        authenticator=authenticator,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=store,
    )

    class _FakeStreamUpstream(FakeCompletionUpstream):
        def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
            self.complete_calls.append(dict(payload))

            async def _gen() -> AsyncIterator[bytes]:
                yield b"data: {}\n\n"

            return _gen()

    upstream = _FakeStreamUpstream()

    class _FakeStreamRouter(FakeModelRouter):
        def stream(
            self,
            body: dict[str, Any],
            *,
            upstream: Any,
            on_served: Any = None,
        ) -> AsyncIterator[bytes]:
            self.complete_calls.append(dict(body))
            if on_served is not None:
                on_served(body.get("model", ""))
            return upstream.stream(body)

    stream_router = _FakeStreamRouter()
    body: dict[str, Any] = {
        "model": "cheap:opus",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }

    gen = await uc.stream(
        raw_key="sk-test",
        body=body,
        upstream=upstream,
        usage_recorder=FakeUsageRecorder(),
        model_router=cast(Any, stream_router),
    )
    chunks = [chunk async for chunk in gen]

    assert chunks, "stream must yield at least one chunk"
    assert stream_router.complete_calls[0]["model"] == "gpt5-5-mini"
    assert store.resolve_calls == [(tenant_id, "cheap", "opus")]


async def test_chat_cross_tenant_isolation(
    tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """Tenant A's mapping must never leak to tenant B's request."""
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "opus", "model-a")  # tenant A only
    authenticator_b = FakeAuthenticator(other_tenant_id)
    uc = CompletionUseCase(
        authenticator=authenticator_b,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=store,
    )
    upstream = FakeCompletionUpstream()
    body: dict[str, Any] = {
        "model": "cheap:opus",
        "messages": [{"role": "user", "content": "hi"}],
    }

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="sk-test-b",
            body=body,
            upstream=upstream,
            usage_recorder=FakeUsageRecorder(),
            model_router=cast(Any, FakeModelRouter()),
        )

    assert exc_info.value.code == PRESET_NOT_FOUND.code
    assert exc_info.value.status == 400
    assert store.resolve_calls == [(other_tenant_id, "cheap", "opus")]
    assert tenant_id not in [call[0] for call in store.resolve_calls]
    assert upstream.complete_calls == [], "no upstream call on a rejected preset"


async def test_chat_unmatched_colon_selector_rejected(tenant_id: uuid.UUID) -> None:
    """missing:opus, no such preset -> 400 ERR_PRESET_NOT_FOUND; zero side effects."""
    store = FakePresetStore()  # empty — no preset named "missing"
    authenticator = FakeAuthenticator(tenant_id)
    uc = CompletionUseCase(
        authenticator=authenticator,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=store,
    )
    upstream = FakeCompletionUpstream()
    body: dict[str, Any] = {
        "model": "missing:opus",
        "messages": [{"role": "user", "content": "hi"}],
    }

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="sk-test",
            body=body,
            upstream=upstream,
            usage_recorder=FakeUsageRecorder(),
            model_router=cast(Any, FakeModelRouter()),
        )

    assert exc_info.value.status == 400
    assert exc_info.value.code == PRESET_NOT_FOUND.code
    assert upstream.complete_calls == [], "no upstream call on rejection"


async def test_chat_multi_colon_selector_never_matches(tenant_id: uuid.UUID) -> None:
    """cheap:opus:extra with (cheap,opus) upserted -> alias_key='opus:extra' -> None -> 400."""
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "opus", "model-a")
    authenticator = FakeAuthenticator(tenant_id)
    uc = CompletionUseCase(
        authenticator=authenticator,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=store,
    )
    upstream = FakeCompletionUpstream()
    body: dict[str, Any] = {
        "model": "cheap:opus:extra",
        "messages": [{"role": "user", "content": "hi"}],
    }

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="sk-test",
            body=body,
            upstream=upstream,
            usage_recorder=FakeUsageRecorder(),
            model_router=cast(Any, FakeModelRouter()),
        )

    assert exc_info.value.code == PRESET_NOT_FOUND.code
    assert store.resolve_calls == [(tenant_id, "cheap", "opus:extra")]
    assert upstream.complete_calls == []


# ---------------------------------------------------------------------------
# Images (ImagesUseCase.execute)
# ---------------------------------------------------------------------------


async def test_images_colon_selector_resolves_before_authorize_and_upstream(
    tenant_id: uuid.UUID,
) -> None:
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "draw", "image-gen-mini")
    governance = FakeGovernance(tenant_id)
    authenticator = FakeAuthenticator(tenant_id)
    session = FakeSession(modality="image", provider="fake-provider")
    adapter = FakeProviderAdapter()
    uc = ImagesUseCase(
        governance=cast(Any, governance),
        session=cast(Any, session),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    body: dict[str, Any] = {"model": "cheap:draw", "prompt": "a cat"}

    status, _resp = await uc.execute(
        raw_key="sk-test",
        body=body,
        registry=make_registry(adapter),
        usage_recorder=FakeUsageRecorder(),
    )

    assert status == 200
    assert governance.authorize_calls[0][1] == "image-gen-mini"
    assert adapter.post_json_calls[0][1]["model"] == "image-gen-mini"
    assert "cheap:draw" not in adapter.post_json_calls[0][1].values()


async def test_images_unmatched_selector_rejected_before_upstream(tenant_id: uuid.UUID) -> None:
    store = FakePresetStore()  # empty
    governance = FakeGovernance(tenant_id)
    authenticator = FakeAuthenticator(tenant_id)
    session = FakeSession(modality="image", provider="fake-provider")
    adapter = FakeProviderAdapter()
    uc = ImagesUseCase(
        governance=cast(Any, governance),
        session=cast(Any, session),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    body: dict[str, Any] = {"model": "missing:draw", "prompt": "a cat"}

    with pytest.raises(ProblemError) as exc_info:
        await uc.execute(
            raw_key="sk-test",
            body=body,
            registry=make_registry(adapter),
            usage_recorder=FakeUsageRecorder(),
        )

    assert exc_info.value.code == PRESET_NOT_FOUND.code
    assert governance.authorize_calls == [], "governance must never run on a rejected preset"
    assert adapter.post_json_calls == [], "no upstream call on rejection"


# ---------------------------------------------------------------------------
# Embeddings (EmbeddingsUseCase.execute)
# ---------------------------------------------------------------------------


async def test_embeddings_colon_selector_resolves(tenant_id: uuid.UUID) -> None:
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "embed", "embed-mini")
    governance = FakeGovernance(tenant_id)
    authenticator = FakeAuthenticator(tenant_id)
    session = FakeSession(modality="embedding", provider="fake-provider")
    adapter = FakeProviderAdapter()
    adapter.json_response = (200, {"data": [], "usage": {"total_tokens": 3}})
    uc = EmbeddingsUseCase(
        governance=cast(Any, governance),
        session=cast(Any, session),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    body: dict[str, Any] = {"model": "cheap:embed", "input": "hello world"}

    status, _resp, _x_cache = await uc.execute(
        raw_key="sk-test",
        body=body,
        registry=make_registry(adapter),
        usage_recorder=FakeUsageRecorder(),
    )

    assert status == 200
    assert governance.authorize_calls[0][1] == "embed-mini"
    assert adapter.post_json_calls[0][1]["model"] == "embed-mini"
    assert "cheap:embed" not in adapter.post_json_calls[0][1].values()


# ---------------------------------------------------------------------------
# Audio STT (TranscriptionUseCase.execute)
# ---------------------------------------------------------------------------


async def test_stt_colon_selector_resolves_outgoing_multipart_uses_target(
    tenant_id: uuid.UUID,
) -> None:
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "stt", "whisper-mini")
    governance = FakeGovernance(tenant_id)
    authenticator = FakeAuthenticator(tenant_id)
    session = FakeSession(modality="audio", provider="fake-provider")
    adapter = FakeProviderAdapter()
    adapter.multipart_response = (200, {"text": "hello", "duration": 1.5})
    uc = TranscriptionUseCase(
        governance=cast(Any, governance),
        session=cast(Any, session),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    form: dict[str, Any] = {"file": FakeUploadFile(), "model": "cheap:stt"}

    status, _resp = await uc.execute(
        raw_key="sk-test",
        form=form,
        registry=make_registry(adapter),
        usage_recorder=FakeUsageRecorder(),
    )

    assert status == 200
    assert governance.authorize_calls[0][1] == "whisper-mini"
    outgoing_data = adapter.post_multipart_calls[0][2]
    assert outgoing_data["model"] == "whisper-mini"
    assert "cheap:stt" not in outgoing_data.values()


# ---------------------------------------------------------------------------
# Audio TTS (SpeechUseCase.execute)
# ---------------------------------------------------------------------------


async def test_tts_colon_selector_resolves_outgoing_body_uses_target(
    tenant_id: uuid.UUID,
) -> None:
    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "tts", "tts-mini")
    governance = FakeGovernance(tenant_id)
    authenticator = FakeAuthenticator(tenant_id)
    # preset-capability-validation (v56 §3): SpeechUseCase now rejects a resolved model
    # whose catalog modality isn't "audio_tts" — the fake target must reflect a real TTS
    # catalog row's modality, not the placeholder "audio" used before that guard existed.
    session = FakeSession(modality="audio_tts", provider="fake-provider")
    adapter = FakeProviderAdapter()
    uc = SpeechUseCase(
        governance=cast(Any, governance),
        session=cast(Any, session),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    body: dict[str, Any] = {"model": "cheap:tts", "input": "hello", "voice": "alloy"}

    gen, _media_type = await uc.execute(
        raw_key="sk-test",
        body=body,
        registry=make_registry(adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    _ = [chunk async for chunk in gen]  # drain the stream

    assert governance.authorize_calls[0][1] == "tts-mini"
    outgoing_body = adapter.stream_bytes_calls[0][1]
    assert outgoing_body["model"] == "tts-mini"
    assert "cheap:tts" not in outgoing_body.values()


# ---------------------------------------------------------------------------
# Realtime-WS voice chat (_real_chat delegation)
# ---------------------------------------------------------------------------


async def test_realtime_ws_real_chat_resolves_via_complete_delegation(
    tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_real_chat must thread the real app.state.tenant_model_preset_store singleton into
    the CompletionUseCase it constructs — full coverage was the chosen scope (TASK.md §0),
    not a None-default. The resolution MECHANICS are already proven at the CompletionUseCase
    level (see the chat tests above); this test proves the WIRING — the only new code in
    realtime_ws.py — by patching CompletionUseCase with a recording fake and asserting the
    exact store instance set on app.state reaches the constructor.
    """
    from gateway.proxy.api import realtime_ws

    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "opus", "gpt5-5-mini")

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
            self.provider_resolver = None
            self.cost_recovery_service = None
            self.bandwidth_bucket = None

            class _Settings:
                bandwidth_max_wait_seconds = 0.0
                # preset-capability-validation (v56 §3): _real_chat now reads this flag
                # to wire CompletionUseCase's input-modality guard, mirroring deps.py.
                input_modality_guard_enabled = False

            self.settings = _Settings()
            self.circuit_breaker = object()
            self.completion_upstream = object()
            self.usage_recorder = FakeUsageRecorder()
            self.model_router = None
            self.tenant_model_preset_store = store

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
        "cheap:opus",
        "sk-test",
        websocket=websocket,  # type: ignore[arg-type]
    )

    assert reply == "resolved-ok"
    assert captured_kwargs.get("tenant_model_preset_store") is store, (
        "the real app.state.tenant_model_preset_store singleton must reach the "
        "CompletionUseCase _real_chat constructs — not None, not a copy"
    )


async def test_realtime_ws_real_stt_resolves_via_transcription_delegation(
    tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_real_stt must thread the real app.state.tenant_model_preset_store singleton (plus the
    SAME authenticator it already builds) into the TranscriptionUseCase it constructs — full
    coverage was the chosen scope (TASK.md §0), not a None-default. Regression test: a prior
    build omitted authenticator=/tenant_model_preset_store= on this call site entirely (caught
    by adversarial refute-read), leaving realtime-WS STT silently unresolved while chat resolved.
    """
    from gateway.proxy.api import realtime_ws

    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "stt", "whisper-mini")

    captured_kwargs: dict[str, Any] = {}

    class _RecordingTranscriptionUseCase:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        async def execute(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
            return 200, {"text": "resolved-ok"}

    monkeypatch.setattr(
        "gateway.proxy.application.audio_use_case.TranscriptionUseCase",
        _RecordingTranscriptionUseCase,
    )

    class _FakeSettings:
        stt_max_duration_seconds = 120.0
        # preset-capability-validation (v56 §3): _real_stt now reads this flag to wire
        # TranscriptionUseCase's input-modality guard, mirroring audio_deps.py.
        input_modality_guard_enabled = False

    class _FakeAppState:
        def __init__(self) -> None:
            self.budget_guard = object()
            self.rate_limiter = None
            self.tenant_credential_resolver = None
            self.settings = _FakeSettings()
            self.provider_registry = object()
            self.usage_recorder = FakeUsageRecorder()
            self.tenant_model_preset_store = store

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

    text = await realtime_ws._real_stt(  # pyright: ignore[reportPrivateUsage]
        b"fake-audio-bytes",
        "cheap:stt",
        "sk-test",
        websocket=websocket,  # type: ignore[arg-type]
    )

    assert text == "resolved-ok"
    assert captured_kwargs.get("tenant_model_preset_store") is store, (
        "the real app.state.tenant_model_preset_store singleton must reach the "
        "TranscriptionUseCase _real_stt constructs — not None, not a copy"
    )
    assert captured_kwargs.get("authenticator") is not None, (
        "_real_stt must thread its own authenticator into TranscriptionUseCase — "
        "None here means preset resolution is silently inert on this call site"
    )


async def test_realtime_ws_real_tts_resolves_via_speech_delegation(
    tenant_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_real_tts must thread the real app.state.tenant_model_preset_store singleton (plus the
    SAME authenticator it already builds) into the SpeechUseCase it constructs — full coverage
    was the chosen scope (TASK.md §0), not a None-default. Regression test: a prior build
    omitted authenticator=/tenant_model_preset_store= on this call site entirely (caught by
    adversarial refute-read), leaving realtime-WS TTS silently unresolved while chat resolved.
    """
    from gateway.proxy.api import realtime_ws

    store = FakePresetStore()
    store.seed(tenant_id, "cheap", "tts", "tts-mini")

    captured_kwargs: dict[str, Any] = {}

    class _RecordingSpeechUseCase:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        async def execute(self, **kwargs: Any) -> tuple[AsyncIterator[bytes], str]:
            async def _gen() -> AsyncIterator[bytes]:
                yield b"resolved-ok-bytes"

            return _gen(), "audio/mpeg"

    monkeypatch.setattr(
        "gateway.proxy.application.audio_use_case.SpeechUseCase",
        _RecordingSpeechUseCase,
    )

    class _FakeSettings:
        tts_max_input_characters = 4096

    class _FakeAppState:
        def __init__(self) -> None:
            self.budget_guard = object()
            self.rate_limiter = None
            self.tenant_credential_resolver = None
            self.settings = _FakeSettings()
            self.provider_registry = object()
            self.usage_recorder = FakeUsageRecorder()
            self.tenant_model_preset_store = store

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

    gen = await realtime_ws._real_tts(  # pyright: ignore[reportPrivateUsage]
        "hello there",
        "cheap:tts",
        "alloy",
        "sk-test",
        websocket=websocket,  # type: ignore[arg-type]
    )
    chunks = [chunk async for chunk in gen]

    assert chunks == [b"resolved-ok-bytes"]
    assert captured_kwargs.get("tenant_model_preset_store") is store, (
        "the real app.state.tenant_model_preset_store singleton must reach the "
        "SpeechUseCase _real_tts constructs — not None, not a copy"
    )
    assert captured_kwargs.get("authenticator") is not None, (
        "_real_tts must thread its own authenticator into SpeechUseCase — "
        "None here means preset resolution is silently inert on this call site"
    )


# ---------------------------------------------------------------------------
# Regression safety net — zero presets configured, bare ids across all 5 entry points
# ---------------------------------------------------------------------------


async def test_tenant_with_zero_presets_fully_unaffected(tenant_id: uuid.UUID) -> None:
    """A wired-but-empty preset store + bare model ids -> byte-identical on all 5 entry
    points; resolve() is never called (no colon in any of the bare ids)."""
    store = FakePresetStore()  # zero rows

    # --- chat ---
    authenticator = FakeAuthenticator(tenant_id)
    chat_uc = CompletionUseCase(
        authenticator=authenticator,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=store,
    )
    chat_upstream = FakeCompletionUpstream()
    chat_router = FakeModelRouter()
    chat_body: dict[str, Any] = {
        "model": "gpt5-5",
        "messages": [{"role": "user", "content": "hi"}],
    }
    status, _resp, _xc = await chat_uc.complete(
        raw_key="sk-test",
        body=chat_body,
        upstream=chat_upstream,
        usage_recorder=FakeUsageRecorder(),
        model_router=cast(Any, chat_router),
    )
    assert status == 200
    assert chat_upstream.complete_calls[0]["model"] == "gpt5-5"

    # --- images ---
    governance = FakeGovernance(tenant_id)
    images_adapter = FakeProviderAdapter()
    images_uc = ImagesUseCase(
        governance=cast(Any, governance),
        session=cast(Any, FakeSession(modality="image", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    images_body: dict[str, Any] = {"model": "image-model", "prompt": "a cat"}
    await images_uc.execute(
        raw_key="sk-test",
        body=images_body,
        registry=make_registry(images_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    assert images_adapter.post_json_calls[0][1]["model"] == "image-model"

    # --- embeddings ---
    embeddings_governance = FakeGovernance(tenant_id)
    embeddings_adapter = FakeProviderAdapter()
    embeddings_adapter.json_response = (200, {"data": [], "usage": {}})
    embeddings_uc = EmbeddingsUseCase(
        governance=cast(Any, embeddings_governance),
        session=cast(Any, FakeSession(modality="embedding", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    embeddings_body: dict[str, Any] = {"model": "embed-model", "input": "hi"}
    await embeddings_uc.execute(
        raw_key="sk-test",
        body=embeddings_body,
        registry=make_registry(embeddings_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    assert embeddings_adapter.post_json_calls[0][1]["model"] == "embed-model"

    # --- STT ---
    stt_governance = FakeGovernance(tenant_id)
    stt_adapter = FakeProviderAdapter()
    stt_adapter.multipart_response = (200, {"text": "hi", "duration": 1.0})
    stt_uc = TranscriptionUseCase(
        governance=cast(Any, stt_governance),
        session=cast(Any, FakeSession(modality="audio", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    stt_form: dict[str, Any] = {"file": FakeUploadFile(), "model": "stt-model"}
    await stt_uc.execute(
        raw_key="sk-test",
        form=stt_form,
        registry=make_registry(stt_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    assert stt_adapter.post_multipart_calls[0][2]["model"] == "stt-model"

    # --- TTS ---
    tts_governance = FakeGovernance(tenant_id)
    tts_adapter = FakeProviderAdapter()
    # preset-capability-validation (v56 §3): SpeechUseCase now rejects a resolved model
    # whose catalog modality isn't "audio_tts" — reflect a real TTS row, not "audio".
    tts_uc = SpeechUseCase(
        governance=cast(Any, tts_governance),
        session=cast(Any, FakeSession(modality="audio_tts", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=store,
    )
    tts_body: dict[str, Any] = {"model": "tts-model", "input": "hi", "voice": "alloy"}
    gen, _media_type = await tts_uc.execute(
        raw_key="sk-test",
        body=tts_body,
        registry=make_registry(tts_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    _ = [chunk async for chunk in gen]
    assert tts_adapter.stream_bytes_calls[0][1]["model"] == "tts-model"

    # No colon anywhere above -> resolve() must never have been consulted.
    assert store.resolve_calls == []


# ---------------------------------------------------------------------------
# Unwired store (None) — safe no-op across all 5 entry points
# ---------------------------------------------------------------------------


async def test_unwired_store_is_safe_noop(tenant_id: uuid.UUID) -> None:
    """tenant_model_preset_store=None (DI not wired) -> a colon-bearing model field passes
    through UNRESOLVED to governance/provider, no AttributeError/crash — proves the
    additive `is not None` guard fails safe, not just untested."""
    authenticator = FakeAuthenticator(tenant_id)

    # --- chat ---
    chat_uc = CompletionUseCase(
        authenticator=authenticator,
        model_checker=FakeModelChecker(),
        tenant_model_preset_store=None,
    )
    chat_upstream = FakeCompletionUpstream()
    chat_body: dict[str, Any] = {
        "model": "cheap:opus",
        "messages": [{"role": "user", "content": "hi"}],
    }
    status, _resp, _xc = await chat_uc.complete(
        raw_key="sk-test",
        body=chat_body,
        upstream=chat_upstream,
        usage_recorder=FakeUsageRecorder(),
        model_router=cast(Any, FakeModelRouter()),
    )
    assert status == 200
    assert chat_upstream.complete_calls[0]["model"] == "cheap:opus"

    # --- images ---
    governance = FakeGovernance(tenant_id)
    images_adapter = FakeProviderAdapter()
    images_uc = ImagesUseCase(
        governance=cast(Any, governance),
        session=cast(Any, FakeSession(modality="image", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=None,
    )
    images_body: dict[str, Any] = {"model": "cheap:draw", "prompt": "a cat"}
    await images_uc.execute(
        raw_key="sk-test",
        body=images_body,
        registry=make_registry(images_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    assert images_adapter.post_json_calls[0][1]["model"] == "cheap:draw"
    assert governance.authorize_calls[0][1] == "cheap:draw"

    # --- embeddings ---
    embeddings_governance = FakeGovernance(tenant_id)
    embeddings_adapter = FakeProviderAdapter()
    embeddings_adapter.json_response = (200, {"data": [], "usage": {}})
    embeddings_uc = EmbeddingsUseCase(
        governance=cast(Any, embeddings_governance),
        session=cast(Any, FakeSession(modality="embedding", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=None,
    )
    embeddings_body: dict[str, Any] = {"model": "cheap:embed", "input": "hi"}
    await embeddings_uc.execute(
        raw_key="sk-test",
        body=embeddings_body,
        registry=make_registry(embeddings_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    assert embeddings_adapter.post_json_calls[0][1]["model"] == "cheap:embed"

    # --- STT ---
    stt_governance = FakeGovernance(tenant_id)
    stt_adapter = FakeProviderAdapter()
    stt_adapter.multipart_response = (200, {"text": "hi", "duration": 1.0})
    stt_uc = TranscriptionUseCase(
        governance=cast(Any, stt_governance),
        session=cast(Any, FakeSession(modality="audio", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=None,
    )
    stt_form: dict[str, Any] = {"file": FakeUploadFile(), "model": "cheap:stt"}
    await stt_uc.execute(
        raw_key="sk-test",
        form=stt_form,
        registry=make_registry(stt_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    assert stt_adapter.post_multipart_calls[0][2]["model"] == "cheap:stt"

    # --- TTS ---
    tts_governance = FakeGovernance(tenant_id)
    tts_adapter = FakeProviderAdapter()
    # preset-capability-validation (v56 §3): SpeechUseCase now rejects a resolved model
    # whose catalog modality isn't "audio_tts" — reflect a real TTS row, not "audio".
    tts_uc = SpeechUseCase(
        governance=cast(Any, tts_governance),
        session=cast(Any, FakeSession(modality="audio_tts", provider="fake-provider")),
        authenticator=authenticator,
        tenant_model_preset_store=None,
    )
    tts_body: dict[str, Any] = {"model": "cheap:tts", "input": "hi", "voice": "alloy"}
    gen, _media_type = await tts_uc.execute(
        raw_key="sk-test",
        body=tts_body,
        registry=make_registry(tts_adapter),
        usage_recorder=FakeUsageRecorder(),
    )
    _ = [chunk async for chunk in gen]
    assert tts_adapter.stream_bytes_calls[0][1]["model"] == "cheap:tts"
