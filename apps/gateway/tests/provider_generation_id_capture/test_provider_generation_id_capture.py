"""Red suite for provider-generation-id-capture (v30 t6.2) — TASK.md §4.

Capture the provider's SSE generation id onto the client-disconnect ledger row —
the rail inline recovery (t6.2b) + the sweep (t6.3) ride to look up authoritative
cost later. Additive nullable column, threaded via the typed extras seam exactly
like v27 usage_source. No billing change; append-only preserved.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

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
from gateway.usage.domain.extractor import extract_generation_id_from_sse
from tests import _redis_env

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# extract_generation_id_from_sse — pure unit
# ---------------------------------------------------------------------------

_ID_CHUNK = b'data: {"id":"gen-abc","choices":[{"delta":{"content":"hi"}}]}\n\n'


def test_extract_generation_id_present() -> None:
    assert extract_generation_id_from_sse([_ID_CHUNK]) == "gen-abc"


def test_extract_generation_id_absent() -> None:
    chunks = [b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n', b"data: [DONE]\n\n", b"garbage"]
    assert extract_generation_id_from_sse(chunks) is None


def test_extract_generation_id_split_across_chunks() -> None:
    mid = len(_ID_CHUNK) // 2
    assert extract_generation_id_from_sse([_ID_CHUNK[:mid], _ID_CHUNK[mid:]]) == "gen-abc"


# ---------------------------------------------------------------------------
# Disconnect record carries the generation id (no DB)
# ---------------------------------------------------------------------------


class _SpyRecorder:
    supported_extras: frozenset[str] = frozenset(
        {"team_id", "pii_masked", "usage_source", "provider_generation_id"}
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "no record() calls captured"
        return self.calls[-1]

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


async def _settle() -> None:
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_disconnect_record_carries_generation_id() -> None:
    upstream = PlanStreamUpstream(
        {
            CAND_A: [
                b'data: {"id":"gen-xyz","choices":[{"delta":{"content":"hi"}}]}\n\n',
                b"more\n\n",
            ]
        }
    )
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
    await gen.__anext__()  # consume the id chunk, leave the stream suspended
    await gen.aclose()  # client disconnect
    await _settle()

    assert recorder.last_call.get("usage_source") == "client_disconnect"
    assert recorder.last_call.get("provider_generation_id") == "gen-xyz"


# ---------------------------------------------------------------------------
# The id round-trips recorder -> flusher -> ledger column (DB)
# ---------------------------------------------------------------------------


async def test_flusher_persists_generation_id(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]
    from sqlalchemy import text

    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    redis_client = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openrouter/gen-id-persist"
        snap_id = str(uuid.uuid4())
        await db_session.execute(
            text(
                "INSERT INTO models (id, name, context_length, active)"
                " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": model_id, "n": "Gen id persist"},
        )
        await db_session.execute(
            text(
                "INSERT INTO pricing_snapshots"
                " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
                " VALUES (:id, :m, 0.00001, 0.00003, now())"
            ),
            {"id": snap_id, "m": model_id},
        )
        await db_session.commit()

        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        # A disconnect row carrying the generation id.
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage=None,
            status=200,
            usage_source="client_disconnect",
            provider_generation_id="gen-1",
        )
        # A normal row with NO generation id → NULL column.
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            status=200,
        )

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        rows = (
            await db_session.execute(
                text(
                    "SELECT provider_generation_id, cost_usd FROM usage_records"
                    " WHERE tenant_id = :tid ORDER BY provider_generation_id NULLS LAST"
                ),
                {"tid": api_key["tenant_id"]},
            )
        ).fetchall()
        gen_ids = [r[0] for r in rows]
        assert "gen-1" in gen_ids, f"captured id missing; rows={rows}"
        assert None in gen_ids, f"normal row must leave the column NULL; rows={rows}"
        # No billing change: the normal row still bills its tokens.
        normal = next(r for r in rows if r[0] is None)
        assert Decimal(str(normal[1])) > Decimal("0")
    finally:
        await redis_client.aclose()
