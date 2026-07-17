"""RED suite for fallback-usage-marker (§4 TESTS plan) — credential_source usage marker.

Covers §2 scenarios / §1 Must+Reject:
  - publish side  : _resolve_platform_fallback sets _credential_source_ctx "platform"; own-key leaves None
  - consume side  : _dispatch_record threads it as the credential_source extra, capability-filtered
  - stream trap   : the marker survives reset_provider_credential (does NOT read served_via flag at dispatch)
  - concurrency   : no cross-request leak under interleave
  - recorder      : credential_source lands in the raw JSONB extras only-when-present; tenant_id unchanged

At Ground SHA 3c27af5 the symbol `_credential_source_ctx` does not exist, so this module fails RED with
ImportError until BUILD adds it (and the dispatch/recorder assertions then fail until the wiring lands).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, cast

from gateway.proxy.application.use_cases import (
    _credential_source_ctx,  # pyright: ignore[reportPrivateUsage]  # NEW — added by BUILD
    _dispatch_record,  # pyright: ignore[reportPrivateUsage]
    resolve_provider_credential,
)
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    served_via_platform_fallback,
)
from gateway.usage.application.recorder import RecordingUsageRecorder

from .conftest import (
    CapturingRecorder,
    CapturingRedis,
    ExplodingSessionFactory,
    FakePlatformFallback,
    FakeResolver,
    bearer,
)

PROVIDER = "openrouter"


# --------------------------------------------------------------------------- publish side


async def test_resolve_publishes_credential_source_platform(
    requester_id: uuid.UUID, platform_id: uuid.UUID
) -> None:
    """M1/M4 — a keyless requester served by the platform credential publishes ctx 'platform'."""
    resolver = FakeResolver({(platform_id, PROVIDER): bearer("sk-platform-999")})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)
    _credential_source_ctx.set(None)  # simulate a fresh request context

    token = await resolve_provider_credential(
        resolver, requester_id, PROVIDER, platform_fallback=fb
    )
    try:
        assert _credential_source_ctx.get() == "platform"
    finally:
        reset_provider_credential(cast(Any, token))


async def test_own_key_resolve_leaves_ctx_none(
    requester_id: uuid.UUID, platform_id: uuid.UUID
) -> None:
    """M2/R2 — a tenant with its own key never triggers fallback; ctx stays default None."""
    resolver = FakeResolver(
        {
            (requester_id, PROVIDER): bearer("sk-own-000"),
            (platform_id, PROVIDER): bearer("sk-platform-999"),
        }
    )
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)
    _credential_source_ctx.set(None)

    token = await resolve_provider_credential(
        resolver, requester_id, PROVIDER, platform_fallback=fb
    )
    try:
        assert _credential_source_ctx.get() is None
    finally:
        reset_provider_credential(cast(Any, token))


# --------------------------------------------------------------------------- consume side (dispatch)


async def _dispatch_and_capture(
    recorder: CapturingRecorder, *, tenant_id: uuid.UUID
) -> dict[str, Any]:
    _dispatch_record(
        cast(Any, recorder),
        tenant_id=tenant_id,
        key_id=uuid.uuid4(),
        model="gpt-4o",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        status=200,
    )
    await recorder.wait()
    assert len(recorder.calls) == 1
    return recorder.calls[0]


async def test_dispatch_stamps_credential_source_when_ctx_platform(
    requester_id: uuid.UUID,
) -> None:
    """M1/M3 — ctx 'platform' → the extra reaches the recorder; tenant_id is the requester."""
    _credential_source_ctx.set("platform")
    try:
        recorder = CapturingRecorder(supports_credential_source=True)
        call = await _dispatch_and_capture(recorder, tenant_id=requester_id)
        assert call["credential_source"] == "platform"
        assert call["tenant_id"] == requester_id
    finally:
        _credential_source_ctx.set(None)


async def test_dispatch_omits_credential_source_when_ctx_none(
    requester_id: uuid.UUID,
) -> None:
    """M2/R2 — ctx default None → no credential_source extra (byte-identical to today)."""
    _credential_source_ctx.set(None)
    recorder = CapturingRecorder(supports_credential_source=True)
    call = await _dispatch_and_capture(recorder, tenant_id=requester_id)
    assert "credential_source" not in call


async def test_dispatch_survives_credential_reset(
    requester_id: uuid.UUID, platform_id: uuid.UUID
) -> None:
    """M4 — the marker is stamped even after reset_provider_credential cleared the credential-scoped
    served_via flag (the streaming-path ordering trap): dispatch must read _credential_source_ctx,
    NOT served_via_platform_fallback()."""
    resolver = FakeResolver({(platform_id, PROVIDER): bearer("sk-platform-999")})
    fb = FakePlatformFallback(enabled=True, platform_id=platform_id)
    _credential_source_ctx.set(None)

    token = await resolve_provider_credential(
        resolver, requester_id, PROVIDER, platform_fallback=fb
    )
    # Simulate the stream path: credential scope reset BEFORE the terminal usage record dispatch.
    reset_provider_credential(cast(Any, token))
    assert served_via_platform_fallback() is False  # credential-scoped flag cleared
    try:
        recorder = CapturingRecorder(supports_credential_source=True)
        call = await _dispatch_and_capture(recorder, tenant_id=requester_id)
        assert call["credential_source"] == "platform"  # still marked
    finally:
        _credential_source_ctx.set(None)


async def test_dispatch_drops_extra_when_recorder_lacks_capability(
    requester_id: uuid.UUID,
) -> None:
    """M5/R1 — a recorder without 'credential_source' in supported_extras receives only base kwargs;
    no error, base record byte-identical."""
    _credential_source_ctx.set("platform")
    try:
        recorder = CapturingRecorder(supports_credential_source=False)
        call = await _dispatch_and_capture(recorder, tenant_id=requester_id)
        assert "credential_source" not in call
    finally:
        _credential_source_ctx.set(None)


async def test_no_cross_request_leak_interleave(
    requester_id: uuid.UUID,
) -> None:
    """M4 (concurrency) — two concurrent 'requests' (separate asyncio Tasks) do not leak the marker:
    the fallback task's 'platform' set stays inside its own context, the own-key task sees None."""
    own_tenant = uuid.uuid4()

    async def fallback_request() -> dict[str, Any]:
        _credential_source_ctx.set("platform")  # this task's own copied context
        recorder = CapturingRecorder(supports_credential_source=True)
        return await _dispatch_and_capture(recorder, tenant_id=requester_id)

    async def own_key_request() -> dict[str, Any]:
        # deliberately does NOT set the ctx — must observe the default None
        await asyncio.sleep(0)  # force interleave with the fallback task
        recorder = CapturingRecorder(supports_credential_source=True)
        return await _dispatch_and_capture(recorder, tenant_id=own_tenant)

    fb_call, own_call = await asyncio.gather(
        asyncio.create_task(fallback_request()),
        asyncio.create_task(own_key_request()),
    )
    assert fb_call["credential_source"] == "platform"
    assert "credential_source" not in own_call


# --------------------------------------------------------------------------- recorder (raw JSONB)


async def test_recorder_stamps_credential_source_in_raw(requester_id: uuid.UUID) -> None:
    """M1/M3 — RecordingUsageRecorder stamps credential_source into the raw JSONB extras and keeps
    tenant_id = the requesting tenant. cached=True → cost 0 → no DB session, no spend counter."""
    redis = CapturingRedis()
    recorder = RecordingUsageRecorder(
        redis=cast(Any, redis), session_factory=cast(Any, ExplodingSessionFactory())
    )
    await recorder.record_with_outcome(
        tenant_id=requester_id,
        key_id=uuid.uuid4(),
        model="gpt-4o",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        status=200,
        cached=True,
        credential_source="platform",
    )
    assert len(redis.xadds) == 1
    _key, fields = redis.xadds[0]
    raw = json.loads(fields["raw"])
    assert raw["credential_source"] == "platform"
    assert raw["tenant_id"] == str(requester_id)


async def test_recorder_omits_credential_source_when_none(requester_id: uuid.UUID) -> None:
    """M2 — credential_source=None (own key) → no credential_source key in the raw payload."""
    redis = CapturingRedis()
    recorder = RecordingUsageRecorder(
        redis=cast(Any, redis), session_factory=cast(Any, ExplodingSessionFactory())
    )
    await recorder.record_with_outcome(
        tenant_id=requester_id,
        key_id=uuid.uuid4(),
        model="gpt-4o",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        status=200,
        cached=True,
        credential_source=None,
    )
    assert len(redis.xadds) == 1
    _key, fields = redis.xadds[0]
    raw = json.loads(fields["raw"])
    assert "credential_source" not in raw
