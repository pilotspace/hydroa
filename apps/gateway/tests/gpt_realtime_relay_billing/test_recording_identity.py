"""Red suite: write-behind reuse + relay-billed row identity (TASK.md §2, scenarios 9-11).

RED before BUILD:
  * scenario 9: passes today (record() already writes via XADD) — kept as a contract-
    conformance pin, not a new red.
  * scenario 10: `usage_source` extra already exists; RED comes from `_make_relay_usage_callback`
    not existing yet (ImportError).
  * scenario 11: `_real_session_factory` does not build/pass an on_usage callback yet
    → the constructed OpenAIRealtimeSession's `_on_usage` is None → AttributeError/assert fails.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from gateway.core.config import Settings

from tests.gpt_realtime_relay_billing.conftest import FakeSession, FakeSessionFactory, SpyRecorder, StreamCapture


# ---------------------------------------------------------------------------
# Scenario 9 — Recording reuses the existing write-behind pipeline only
# ---------------------------------------------------------------------------


async def test_recording_goes_through_redis_stream_xadd_only(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(snapshot_id=snapshot_id, prompt_price=Decimal("0.000004"))
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="gpt-realtime",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        status=200,
        usage_source="realtime_relay",
    )

    assert len(stream.events) == 1, "exactly one Redis Stream XADD — no second write path"


# ---------------------------------------------------------------------------
# Scenario 10 — Relay-billed rows are distinguishable from proxy-billed rows
# ---------------------------------------------------------------------------


async def test_relay_billed_row_has_distinct_usage_source(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.proxy.api.realtime_relay_ws import _make_relay_usage_callback  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(snapshot_id=snapshot_id, prompt_price=Decimal("0.000004"))
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    callback = _make_relay_usage_callback(recorder, tenant_id, key_id, "gpt-realtime")
    await callback({"prompt_tokens": 10, "completion_tokens": 5})

    evt = stream.last_event
    assert evt["usage_source"] == "realtime_relay"
    assert evt["usage_source"] != "frame", "must NOT collide with the default proxy usage_source"


# ---------------------------------------------------------------------------
# Scenario 11 — tenant_id/key_id come from WS auth, never re-derived
# ---------------------------------------------------------------------------


class _FakeCred:
    def __init__(self, key: str) -> None:
        self.api_key = SimpleNamespace(get_secret_value=lambda: key)


class _FakeResolver:
    async def resolve(self, tenant_id: Any, provider: str) -> _FakeCred:
        return _FakeCred("sk-fake")


def _fake_app(*, provider: str, usage_recorder: Any) -> SimpleNamespace:
    settings = Settings(
        realtime_relay_provider=provider,
        realtime_relay_openai_model="gpt-realtime",
    )
    return SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            tenant_credential_resolver=_FakeResolver(),
            usage_recorder=usage_recorder,
        )
    )


async def test_real_session_factory_wires_authz_identity_into_every_turn() -> None:
    from gateway.proxy.api.realtime_relay_ws import _real_session_factory  # type: ignore[import]
    from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession

    recorder = SpyRecorder()
    app = _fake_app(provider="openai", usage_recorder=recorder)
    authz = SimpleNamespace(tenant_id=uuid.uuid4(), key_id=uuid.uuid4())

    session = await _real_session_factory(app, authz)
    assert isinstance(session, OpenAIRealtimeSession)

    # Simulate two turns on the SAME session — both must carry the SAME one-time authz identity.
    await session._on_usage({"prompt_tokens": 1, "completion_tokens": 1})  # noqa: SLF001
    await session._on_usage({"prompt_tokens": 2, "completion_tokens": 2})  # noqa: SLF001

    assert len(recorder.calls) == 2
    for call in recorder.calls:
        assert call["tenant_id"] == authz.tenant_id
        assert call["key_id"] == authz.key_id
        assert call["model"] == "gpt-realtime"
