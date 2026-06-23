"""v33 disconnect-provider-cost — stamp residual disconnects as unbilled-upstream + audit
the zero-estimate residue.

A non-recoverable (non-OpenRouter / no-generation-id) client_disconnect would otherwise
record cost_basis='catalog', provider_cost=NULL, cost_usd≈$0 — a silent $0 upstream charge
invisible to the drift monitor. The recorder now stamps a markup-stripped estimated
provider_cost (cost_usd=0, cost_basis='provider') when disconnect_estimate=True and a positive
estimate exists, so reconcile_window surfaces it; zero-estimate residue is surfaced by
audit_unrecovered_disconnects. Recoverable/frame rows are untouched.

Real Postgres:5433 + a dedicated Redis index for the recorder/flusher round-trip. RED before
build: audit_unrecovered_disconnects/UnrecoveredDisconnect do not exist and record() rejects the
disconnect_estimate kwarg until the seam is extended.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from decimal import Decimal
from typing import Any

import pytest
import redis.asyncio as aioredis  # type: ignore[import-untyped]
from sqlalchemy import text

from gateway.usage.application.flusher import UsageLedgerFlusher
from gateway.usage.application.recorder import RecordingUsageRecorder
from gateway.usage.application.reconciliation import (
    audit_unrecovered_disconnects,
    reconcile_window,
)

from .conftest import seed_disconnect_row, seed_priced_model

pytestmark = pytest.mark.asyncio

_REDIS_URL = "redis://localhost:6380/9"


async def _record_and_flush(app: Any, recorder_kwargs: dict[str, Any]) -> None:
    """Record one row then flush it to the ledger via a fresh recorder/flusher on /9."""
    redis_client = aioredis.from_url(_REDIS_URL, decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(**recorder_kwargs)
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()


async def _latest_row(db_session: Any, tenant_id: str) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT cost_basis, provider_cost, cost_usd FROM usage_records"
                " WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT 1"
            ),
            {"tid": tenant_id},
        )
    ).fetchone()


async def test_partial_disconnect_stamped_as_unbilled(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    model_id = "anthropic/disconnect-partial"
    await seed_priced_model(db_session, model_id)
    await _record_and_flush(
        app,
        {
            "tenant_id": uuid.UUID(api_key["tenant_id"]),
            "key_id": uuid.UUID(api_key["key_id"]),
            "model": model_id,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "status": 200,
            "usage_source": "client_disconnect",
            "disconnect_estimate": True,
        },
    )

    cost_basis, provider_cost, cost_usd = await _latest_row(db_session, api_key["tenant_id"])
    assert cost_basis == "provider"
    assert Decimal(str(provider_cost)) > Decimal("0")
    assert Decimal(str(cost_usd)) == Decimal("0")

    # The flushed row's created_at is the DB now() (timestamptz). reconcile_window binds a
    # naive-UTC window that Postgres reinterprets at the session TimeZone, so use a window wide
    # enough (±48h) to swallow any session-TZ offset — what we assert is that the recorder-
    # stamped row matches the unbilled-upstream filter (provider_cost>0, cost_usd=0, provider).
    now = datetime.datetime.now(datetime.UTC)
    summary = await reconcile_window(
        db_session,
        now - datetime.timedelta(hours=48),
        now + datetime.timedelta(hours=48),
        tenant_id=uuid.UUID(api_key["tenant_id"]),
    )
    assert summary.unbilled_upstream_cost > Decimal("0")


async def test_recoverable_disconnect_not_stamped(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    model_id = "openrouter/disconnect-recoverable"
    await seed_priced_model(db_session, model_id)
    await _record_and_flush(
        app,
        {
            "tenant_id": uuid.UUID(api_key["tenant_id"]),
            "key_id": uuid.UUID(api_key["key_id"]),
            "model": model_id,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "status": 200,
            "usage_source": "client_disconnect",
            "provider_generation_id": "gen-recoverable",
            "disconnect_estimate": False,  # v30 chain owns the recovery
        },
    )

    cost_basis, provider_cost, cost_usd = await _latest_row(db_session, api_key["tenant_id"])
    assert cost_basis == "catalog"
    assert provider_cost is None
    assert Decimal(str(cost_usd)) > Decimal("0")  # billed normally, no forced $0


async def test_complete_frame_disconnect_not_stamped(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    model_id = "anthropic/disconnect-frame"
    await seed_priced_model(db_session, model_id)
    await _record_and_flush(
        app,
        {
            "tenant_id": uuid.UUID(api_key["tenant_id"]),
            "key_id": uuid.UUID(api_key["key_id"]),
            "model": model_id,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "status": 200,
            "usage_source": "frame",  # a complete frame arrived
            "disconnect_estimate": False,
        },
    )

    cost_basis, _provider_cost, cost_usd = await _latest_row(db_session, api_key["tenant_id"])
    assert cost_basis == "catalog"
    assert Decimal(str(cost_usd)) > Decimal("0")


async def test_audit_surfaces_zero_estimate_residue(
    db_session: Any, api_key: dict[str, str]
) -> None:
    tid = api_key["tenant_id"]
    kid = api_key["key_id"]
    # residue: a zero-estimate non-recoverable disconnect (provider_cost NULL, cost_usd 0)
    await seed_disconnect_row(
        db_session, tenant_id=tid, key_id=kid,
        provider_generation_id=None, usage_source="client_disconnect",
    )
    # a recovered disconnect: gen id + an openrouter_recovered sibling → must NOT surface
    await seed_disconnect_row(
        db_session, tenant_id=tid, key_id=kid,
        provider_generation_id="gen-done", usage_source="client_disconnect",
    )
    await seed_disconnect_row(
        db_session, tenant_id=tid, key_id=kid,
        provider_generation_id="gen-done", usage_source="openrouter_recovered",
        provider_cost="1.00", cost_usd="1.10", cost_basis="provider",
    )

    rows = await audit_unrecovered_disconnects(db_session)

    mine = [r for r in rows if str(r.tenant_id) == tid]
    assert len(mine) == 1
    assert mine[0].model_id == "openai/gpt-4o"


async def test_audit_inverted_window_raises(db_session: Any) -> None:
    later = datetime.datetime(2026, 6, 4, tzinfo=datetime.UTC)
    earlier = datetime.datetime(2026, 6, 3, tzinfo=datetime.UTC)
    with pytest.raises(ValueError):
        await audit_unrecovered_disconnects(db_session, later, earlier)


# ---------------------------------------------------------------------------
# use_cases disconnect-handler predicate (no DB) — the recoverability decision.
# A row with a generation id is a recovery-chain candidate (inline OR sweep, which
# key ONLY on provider_generation_id), so it must NEVER be stamped — else a sweep
# enabled later would double-count it. Only a NO-generation-id disconnect is stamped.
# ---------------------------------------------------------------------------

_NO_ID_CHUNK = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'


class _SpyRecorder:
    supported_extras: frozenset[str] = frozenset(
        {"team_id", "pii_masked", "usage_source", "provider_generation_id", "disconnect_estimate"}
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "no record() calls captured"
        return self.calls[-1]

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


async def _disconnect_after_first_chunk(first_chunk: bytes) -> _SpyRecorder:
    """Drive a stream that yields one chunk then is closed by the client (disconnect)."""
    from tests.streaming_resilience.conftest import (
        ALIAS,
        CAND_A,
        CAND_B,
        FakeAuthenticator,
        FakeModelChecker,
        PlanStreamUpstream,
        make_payload,
    )

    from gateway.proxy.application.fallback_router import FallbackModelRouter
    from gateway.proxy.application.use_cases import CompletionUseCase

    upstream = PlanStreamUpstream({CAND_A: [first_chunk, b"more\n\n"]})
    recorder = _SpyRecorder()
    uc = CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        stream_resilience_enabled=True,
    )
    router = FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={ALIAS: [CAND_A, CAND_B]},
        stream_resilience_enabled=True,
    )
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=router,
    )
    await gen.__anext__()  # consume the first chunk, leave the stream suspended
    await gen.aclose()  # client disconnect
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return recorder


async def test_no_gen_id_disconnect_is_stamped() -> None:
    recorder = await _disconnect_after_first_chunk(_NO_ID_CHUNK)
    assert recorder.last_call.get("usage_source") == "client_disconnect"
    assert recorder.last_call.get("disconnect_estimate") is True


async def test_gen_id_disconnect_is_not_stamped() -> None:
    # A chunk carrying a generation id → a recovery-chain candidate → must NOT be stamped,
    # even though the recovery knob is off here (_stream_provider unresolved).
    id_chunk = b'data: {"id":"gen-xyz","choices":[{"delta":{"content":"x"}}]}\n\n'
    recorder = await _disconnect_after_first_chunk(id_chunk)
    assert recorder.last_call.get("usage_source") == "client_disconnect"
    assert recorder.last_call.get("provider_generation_id") == "gen-xyz"
    assert recorder.last_call.get("disconnect_estimate") is not True
