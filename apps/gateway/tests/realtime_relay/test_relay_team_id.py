"""Red suite: team_id attribution fix for relay usage capture (B2 TASK.md §2/§3, M4).

RED until _make_relay_usage_callback threads team_id=authz.team_id into
usage_recorder.record(), for BOTH providers (fix lives in the one shared callback
builder), and _real_session_factory wires it through for both OpenAI and Gemini.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from gateway.core.config import Settings


class _FakeCred:
    def __init__(self, key: str) -> None:
        self.api_key = SimpleNamespace(get_secret_value=lambda: key)


class _FakeResolver:
    async def resolve(self, tenant_id: Any, provider: str) -> _FakeCred:
        return _FakeCred("sk-fake")


class SpyRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _fake_app(*, provider: str, usage_recorder: Any) -> SimpleNamespace:
    settings = Settings(
        realtime_relay_provider=provider,
        realtime_relay_openai_model="gpt-realtime",
        realtime_relay_gemini_model="gemini-2.0-flash-exp",
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            tenant_credential_resolver=_FakeResolver(),
            usage_recorder=usage_recorder,
        )
    )


# ---------------------------------------------------------------------------
# The shared callback builder carries team_id through
# ---------------------------------------------------------------------------


async def test_make_relay_usage_callback_passes_team_id() -> None:
    from gateway.proxy.api.realtime_relay_ws import _make_relay_usage_callback

    recorder = SpyRecorder()
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    team_id = uuid.uuid4()

    callback = _make_relay_usage_callback(
        recorder, tenant_id, key_id, "gpt-realtime", team_id=team_id
    )
    await callback({"prompt_tokens": 10, "completion_tokens": 5})

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["team_id"] == team_id
    assert call["tenant_id"] == tenant_id
    assert call["key_id"] == key_id


async def test_make_relay_usage_callback_team_id_defaults_none() -> None:
    """A key with no team (authz.team_id is None) records team_id=None — unchanged shape."""
    from gateway.proxy.api.realtime_relay_ws import _make_relay_usage_callback

    recorder = SpyRecorder()
    callback = _make_relay_usage_callback(recorder, uuid.uuid4(), uuid.uuid4(), "gpt-realtime")
    await callback({"prompt_tokens": 1, "completion_tokens": 1})

    assert recorder.calls[0]["team_id"] is None


# ---------------------------------------------------------------------------
# _real_session_factory wires team_id through for BOTH providers
# ---------------------------------------------------------------------------


async def test_real_session_factory_wires_team_id_for_openai() -> None:
    from gateway.proxy.api.realtime_relay_ws import _real_session_factory
    from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession

    recorder = SpyRecorder()
    app = _fake_app(provider="openai", usage_recorder=recorder)
    authz = SimpleNamespace(tenant_id=uuid.uuid4(), key_id=uuid.uuid4(), team_id=uuid.uuid4())

    session = await _real_session_factory(app, authz)
    assert isinstance(session, OpenAIRealtimeSession)

    await session._on_usage({"prompt_tokens": 1, "completion_tokens": 1})  # noqa: SLF001
    assert recorder.calls[0]["team_id"] == authz.team_id


async def test_real_session_factory_wires_on_usage_for_gemini() -> None:
    """M3: Gemini now receives the SAME callback OpenAI already gets (was: none at all)."""
    from gateway.proxy.api.realtime_relay_ws import _real_session_factory
    from gateway.proxy.infrastructure.gemini_live import GeminiLiveSession

    recorder = SpyRecorder()
    app = _fake_app(provider="gemini", usage_recorder=recorder)
    authz = SimpleNamespace(tenant_id=uuid.uuid4(), key_id=uuid.uuid4(), team_id=uuid.uuid4())

    session = await _real_session_factory(app, authz)
    assert isinstance(session, GeminiLiveSession)
    assert session._on_usage is not None  # noqa: SLF001

    await session._on_usage({"prompt_tokens": 2, "completion_tokens": 2})  # noqa: SLF001
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["team_id"] == authz.team_id
    assert recorder.calls[0]["usage_source"] == "realtime_relay"
    assert recorder.calls[0]["model"] == "gemini-2.0-flash-exp"


async def test_real_session_factory_wires_authz_without_team_id_attr_defensively() -> None:
    """Backward-compat: an authz-like object with no team_id attribute at all (the shape the
    pre-M4 frozen identity test in gpt_realtime_relay_billing still constructs) must not
    AttributeError — degrades to team_id=None via getattr, never crashes the relay."""
    from gateway.proxy.api.realtime_relay_ws import _real_session_factory

    recorder = SpyRecorder()
    app = _fake_app(provider="openai", usage_recorder=recorder)
    authz = SimpleNamespace(tenant_id=uuid.uuid4(), key_id=uuid.uuid4())  # no team_id attr

    session = await _real_session_factory(app, authz)
    await session._on_usage({"prompt_tokens": 1, "completion_tokens": 1})  # noqa: SLF001
    assert recorder.calls[0]["team_id"] is None
