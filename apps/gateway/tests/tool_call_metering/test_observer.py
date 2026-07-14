"""Unit-level RED suite — MeteringToolCallObserver (TASK.md §4, FROZEN @ v1).

No DB, no real Redis — drives MeteringToolCallObserver directly against
FakeUsageRecorder/FakeRedis so the forwarding contract (M2/M3), the never-raises
discipline (M10), and the call_id dedupe gate (R2/CR-1) are each pinned in isolation
from the shared rate-card resolver / Decimal cost math (covered separately in
test_billing_integration.py, which drives the REAL RecordingUsageRecorder).

RED before BUILD: `gateway.tool_call_metering.infrastructure.observer` does not exist
yet, so every import below fails — the honest missing-implementation red.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from tests.tool_call_metering.conftest import FakeRedis, FakeUsageRecorder

pytestmark = pytest.mark.asyncio


def _observer(*, usage_recorder: FakeUsageRecorder, redis: FakeRedis) -> object:
    from gateway.tool_call_metering.infrastructure.observer import MeteringToolCallObserver

    return MeteringToolCallObserver(usage_recorder=usage_recorder, redis=redis)


# ===========================================================================
# Scenario: A successful MCP tool call is metered exactly once (M1, M2, M3)
# ===========================================================================


async def test_successful_call_forwards_exact_kwargs_to_usage_recorder() -> None:
    recorder = FakeUsageRecorder()
    observer = _observer(usage_recorder=recorder, redis=FakeRedis())
    tenant_id, key_id, call_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await observer.record(  # type: ignore[attr-defined]
        call_id=call_id,
        tenant_id=tenant_id,
        key_id=key_id,
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=120,
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["tenant_id"] == tenant_id
    assert call["key_id"] == key_id
    assert call["model"] == "mcp_tool_call"
    assert call["usage"] is None
    assert call["status"] == 200
    assert call["pricing_unit"] == "per_tool_call"
    assert call["quantity"] == Decimal("1")
    assert call["tags"] == {"mcp_server": "mcp.acme.example", "mcp_tool": "search"}


# ===========================================================================
# Scenario: Metering never raises or blocks on a recorder outage (M10)
# ===========================================================================


async def test_never_raises_when_usage_recorder_raises() -> None:
    recorder = FakeUsageRecorder(raises=ConnectionError("Redis/DB unavailable"))
    observer = _observer(usage_recorder=recorder, redis=FakeRedis())

    # Must return normally — no exception propagates to the caller.
    await observer.record(  # type: ignore[attr-defined]
        call_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=50,
    )


# ===========================================================================
# Scenario: A hypothetical double-invocation is billed exactly once (R2, CR-1)
# ===========================================================================


async def test_duplicate_call_id_billed_exactly_once() -> None:
    recorder = FakeUsageRecorder()
    observer = _observer(usage_recorder=recorder, redis=FakeRedis())
    call_id = uuid.uuid4()
    tenant_id, key_id = uuid.uuid4(), uuid.uuid4()

    for _ in range(2):
        await observer.record(  # type: ignore[attr-defined]
            call_id=call_id,
            tenant_id=tenant_id,
            key_id=key_id,
            server_host="mcp.acme.example",
            tool_name="search",
            status="success",
            latency_ms=100,
        )

    assert len(recorder.calls) == 1, (
        "a defect at the upstream fire-and-forget call site invoking record() twice "
        "with the SAME call_id must still bill exactly once (CR-1 closes the "
        "double-bill residual structurally)"
    )


async def test_distinct_call_ids_both_billed() -> None:
    """Sanity counterpart: the dedupe gate must never collapse two DIFFERENT logical
    tool calls — only a genuine repeat of the SAME call_id."""
    recorder = FakeUsageRecorder()
    observer = _observer(usage_recorder=recorder, redis=FakeRedis())
    tenant_id, key_id = uuid.uuid4(), uuid.uuid4()

    for _ in range(2):
        await observer.record(  # type: ignore[attr-defined]
            call_id=uuid.uuid4(),
            tenant_id=tenant_id,
            key_id=key_id,
            server_host="mcp.acme.example",
            tool_name="search",
            status="success",
            latency_ms=100,
        )

    assert len(recorder.calls) == 2


# ===========================================================================
# audit-remediation (HIGH): agent_principal_id must be forwarded when present,
# and omitted (never a stray None kwarg) when absent — so a v1-Protocol fake
# recorder that never declared the kwarg (e.g. this file's own FakeUsageRecorder)
# is unaffected by an unattached-principal call, exactly as before this fix.
# ===========================================================================


async def test_agent_principal_id_forwarded_to_usage_recorder_when_present() -> None:
    recorder = FakeUsageRecorder()
    observer = _observer(usage_recorder=recorder, redis=FakeRedis())
    tenant_id, key_id, call_id, principal_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )

    await observer.record(  # type: ignore[attr-defined]
        call_id=call_id,
        tenant_id=tenant_id,
        key_id=key_id,
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=120,
        agent_principal_id=principal_id,
    )

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["agent_principal_id"] == principal_id


async def test_agent_principal_id_omitted_when_absent() -> None:
    """An sk- key call (no attached principal) must NOT add a stray
    agent_principal_id kwarg — a narrow v1 UsageRecorder fake that never declared
    the keyword would TypeError on an unconditional forward."""
    recorder = FakeUsageRecorder()
    observer = _observer(usage_recorder=recorder, redis=FakeRedis())

    await observer.record(  # type: ignore[attr-defined]
        call_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=120,
    )

    assert len(recorder.calls) == 1
    assert "agent_principal_id" not in recorder.calls[0]


async def test_dedupe_check_failure_fails_open_and_still_bills() -> None:
    """If the Redis dedupe gate itself raises (Redis unreachable), the observer must
    still forward to usage_recorder.record() — never a silently dropped bill (R1)."""
    recorder = FakeUsageRecorder()
    observer = _observer(
        usage_recorder=recorder, redis=FakeRedis(raises=ConnectionError("redis down"))
    )

    await observer.record(  # type: ignore[attr-defined]
        call_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        server_host="mcp.acme.example",
        tool_name="search",
        status="success",
        latency_ms=100,
    )

    assert len(recorder.calls) == 1


# ===========================================================================
# Scenario: Agent-token-authenticated calls bill identically to sk-key ones (M12)
# ===========================================================================


async def test_identity_shape_is_indistinguishable_by_credential_class() -> None:
    """The observer never branches on HOW tenant_id/key_id were authenticated — an
    agent-token-resolved identity produces the exact same forwarded shape as any
    other identity."""
    recorder = FakeUsageRecorder()
    observer = _observer(usage_recorder=recorder, redis=FakeRedis())
    # Simulates an agent-token-authenticated caller's resolved identity — the
    # observer has no way to know (and must not care) how it was authenticated.
    tenant_id, key_id = uuid.uuid4(), uuid.uuid4()

    await observer.record(  # type: ignore[attr-defined]
        call_id=uuid.uuid4(),
        tenant_id=tenant_id,
        key_id=key_id,
        server_host="mcp.acme.example",
        tool_name="fetch",
        status="success",
        latency_ms=75,
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert set(call.keys()) == {
        "tenant_id",
        "key_id",
        "model",
        "usage",
        "status",
        "pricing_unit",
        "quantity",
        "tags",
    }
