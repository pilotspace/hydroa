"""Failing-first (red) suite for usage-metering (contract DRAFT, TASK.md §4).

One test per scenario in .add/tasks/usage-metering/TASK.md §2.
Tests drive UsageLedgerFlusher.flush_once() deterministically — no timing.
Redis is tested against a real redis://localhost:6380/9 (dev compose); the
test fixture flushes db index 9 before each test so state is isolated.

Fakes / monkeypatching:
  - FakeRedis: drops into RecordingUsageRecorder to simulate Redis being down
  - FakeUsageFlusher: drives flush_once() without lifespan timing
  - app.state.usage_recorder injected with RecordingUsageRecorder for integration tests
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# SSE constants reused from proxy tests (same chunks pattern)
# ---------------------------------------------------------------------------
SSE_CHUNKS_WITH_USAGE: list[bytes] = [
    b'data: {"id":"gen-u1","choices":[{"delta":{"content":"h"}}]}\n\n',
    b'data: {"id":"gen-u1","choices":[{"delta":{"content":"i"}}]}\n\n',
    b'data: {"usage":{"prompt_tokens":100,"completion_tokens":50}}\n\n',
    b"data: [DONE]\n\n",
]

COMPLETIONS = "/v1/chat/completions"
ADMIN_USAGE = "/admin/usage"

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal fake upstream — reused without modification from proxy tests."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
        sse_chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-u1",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
        )
        self.sse_chunks = sse_chunks if sse_chunks is not None else SSE_CHUNKS_WITH_USAGE
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in self.sse_chunks:
                yield chunk

        return _gen()


class BrokenRedis:
    """Fake Redis client that raises on every call — simulates Redis being down."""

    async def xadd(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def incrbyfloat(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xack(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key.

    Identical to the proxy fixture pattern — tenant gets default markup_pct=20.
    """
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "Acme",
            "email": "billing@acme.io",
            "password": "correct horse battery",
        },
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "billing@acme.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "billing-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def api_key_b(client: httpx.AsyncClient) -> dict[str, str]:
    """Second tenant (tenant B) for isolation tests."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "Beta Corp",
            "email": "billing@beta.io",
            "password": "another horse battery",
        },
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "billing@beta.io", "password": "another horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "beta-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def active_model_with_pricing(db_session: AsyncSession) -> dict[str, Any]:
    """Insert model + pricing snapshot; return model_id and snapshot details."""
    model_id = "openai/gpt-4o"
    snapshot_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.0000025, 0.00001, now())"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": snapshot_id, "m": model_id},
    )
    await db_session.commit()
    return {
        "model_id": model_id,
        "snapshot_id": snapshot_id,
        "prompt_price": Decimal("0.0000025"),
        "completion_price": Decimal("0.00001"),
    }


def auth_header(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def bearer_header(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status
    assert resp.json()["code"] == code


# ---------------------------------------------------------------------------
# § Scenario 1 — non-streaming completion: correct Decimal cost + ledger row
# ---------------------------------------------------------------------------


async def test_non_streaming_ledger_row_correct_decimal_cost(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """Non-streaming completion → one ledger row, cost = Decimal arithmetic, pricing_snapshot_id set."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    app.state.usage_recorder = recorder
    app.state.completion_upstream = FakeCompletionUpstream()

    resp = await client.post(
        COMPLETIONS,
        json={
            "model": active_model_with_pricing["model_id"],
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers=auth_header(api_key["key"]),
    )
    assert resp.status_code == 200

    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    rows = (
        await db_session.execute(
            text("SELECT * FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).fetchall()

    assert len(rows) == 1
    row = rows[0]._mapping  # type: ignore[union-attr]
    # cost = (100 × 0.0000025 + 50 × 0.00001) × (1 + 20/100)
    #      = (0.00025 + 0.0005) × 1.20
    #      = 0.00075 × 1.20 = 0.00090000
    expected_cost = (Decimal("100") * Decimal("0.0000025") + Decimal("50") * Decimal("0.00001")) * (
        Decimal("1") + Decimal("20") / Decimal("100")
    )
    assert Decimal(str(row["cost_usd"])) == expected_cost
    assert row["pricing_snapshot_id"] is not None
    assert row["tenant_id"] == uuid.UUID(api_key["tenant_id"])
    assert row["model_id"] == active_model_with_pricing["model_id"]
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 50

    # Spend counter check
    import datetime

    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_key = f"usage:spend:{api_key['tenant_id']}:{yyyymm}"
    raw_val = await redis_client.get(spend_key)
    assert raw_val is not None
    assert abs(float(raw_val) - float(expected_cost)) < 1e-7


# ---------------------------------------------------------------------------
# § Scenario 2 — streaming: usage extracted from SSE, bytes byte-identical
# ---------------------------------------------------------------------------


async def test_streaming_usage_extracted_from_sse_and_priced(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """Streaming path: SSE bytes unchanged; usage extracted; one ledger row with correct cost."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    app.state.usage_recorder = recorder
    app.state.completion_upstream = FakeCompletionUpstream()

    resp = await client.post(
        COMPLETIONS,
        json={
            "model": active_model_with_pricing["model_id"],
            "messages": [{"role": "user", "content": "stream me"}],
            "stream": True,
        },
        headers=auth_header(api_key["key"]),
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # Bytes must be byte-identical to what the fake upstream emitted
    assert resp.content == b"".join(SSE_CHUNKS_WITH_USAGE)

    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    rows = (
        await db_session.execute(
            text("SELECT * FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).fetchall()

    assert len(rows) == 1
    row = rows[0]._mapping  # type: ignore[union-attr]
    # SSE_CHUNKS_WITH_USAGE has prompt_tokens=100, completion_tokens=50
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 50
    # cost = same formula as non-streaming (markup_pct=20 default)
    expected_cost = (
        Decimal("100") * Decimal("0.0000025") + Decimal("50") * Decimal("0.00001")
    ) * Decimal("1.20")
    assert Decimal(str(row["cost_usd"])) == expected_cost


# ---------------------------------------------------------------------------
# § Scenario 3 — duplicate flush is idempotent
# ---------------------------------------------------------------------------


async def test_duplicate_flush_idempotent(
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """Flushing the same event twice (at-least-once re-delivery) → exactly one row."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    # Directly call record() to push one event
    await recorder.record(
        tenant_id=uuid.UUID(api_key["tenant_id"]),
        key_id=uuid.UUID(api_key["key_id"]),
        model=active_model_with_pricing["model_id"],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        status=200,
    )

    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)

    # First flush — should insert 1 row
    await flusher.flush_once()

    count_first = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).scalar()
    assert count_first == 1

    # Second flush of the SAME pending event (simulate XACK not yet sent)
    # We re-push the same event manually to simulate re-delivery before ACK
    await flusher.flush_once()

    count_second = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).scalar()
    assert count_second == 1, "Duplicate flush must not insert a second row (idempotent)"


# ---------------------------------------------------------------------------
# § Scenario 4 — Redis unavailable: completion still 200, no exception raised
# ---------------------------------------------------------------------------


async def test_redis_unavailable_completion_still_200(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
) -> None:
    """Redis down must NOT fail the completion — recorder swallows and logs."""
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    broken_recorder = RecordingUsageRecorder(
        redis=BrokenRedis(),  # type: ignore[arg-type]
        session_factory=app.state.sessionmaker,
    )
    app.state.usage_recorder = broken_recorder
    app.state.completion_upstream = FakeCompletionUpstream()

    resp = await client.post(
        COMPLETIONS,
        json={
            "model": active_model_with_pricing["model_id"],
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers=auth_header(api_key["key"]),
    )

    # Completion must succeed even though Redis is broken
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# § Scenario 5 — unknown model pricing: cost=0, raw stored, snapshot NULL
# ---------------------------------------------------------------------------


async def test_unknown_model_pricing_cost_zero_raw_stored(
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    redis_client: Any,
) -> None:
    """Model with no pricing snapshot → tokens=0, cost=0, raw payload stored, snapshot_id NULL."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    # Insert model without a pricing snapshot
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": "ghost/model-x", "n": "Ghost Model"},
    )
    await db_session.commit()

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    raw_usage = {"prompt_tokens": 10, "completion_tokens": 5}
    await recorder.record(
        tenant_id=uuid.UUID(api_key["tenant_id"]),
        key_id=uuid.UUID(api_key["key_id"]),
        model="ghost/model-x",
        usage=raw_usage,
        status=200,
    )

    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    rows = (
        await db_session.execute(
            text(
                "SELECT * FROM usage_records WHERE tenant_id = :tid AND model_id = 'ghost/model-x'"
            ),
            {"tid": api_key["tenant_id"]},
        )
    ).fetchall()

    assert len(rows) == 1
    row = rows[0]._mapping  # type: ignore[union-attr]
    assert row["prompt_tokens"] == 0
    assert row["completion_tokens"] == 0
    assert Decimal(str(row["cost_usd"])) == Decimal("0")
    assert row["pricing_snapshot_id"] is None
    # Raw jsonb must contain the original usage payload
    raw_stored = row["raw"] if isinstance(row["raw"], dict) else json.loads(row["raw"])
    assert raw_stored.get("usage") == raw_usage or "prompt_tokens" in str(raw_stored)


# ---------------------------------------------------------------------------
# § Scenario 6 — spend counter incremented by correct cost
# ---------------------------------------------------------------------------


async def test_spend_counter_incremented(
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """After record(), Redis INCRBYFLOAT key holds the correct cost (float tolerance)."""
    import datetime

    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    await recorder.record(
        tenant_id=uuid.UUID(api_key["tenant_id"]),
        key_id=uuid.UUID(api_key["key_id"]),
        model=active_model_with_pricing["model_id"],
        usage={"prompt_tokens": 200, "completion_tokens": 100},
        status=200,
    )

    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_key = f"usage:spend:{api_key['tenant_id']}:{yyyymm}"
    raw_val = await redis_client.get(spend_key)
    assert raw_val is not None

    # cost = (200×0.0000025 + 100×0.00001) × 1.20
    #      = (0.0005 + 0.001) × 1.20 = 0.0015 × 1.20 = 0.00180000
    expected = float(
        (Decimal("200") * Decimal("0.0000025") + Decimal("100") * Decimal("0.00001"))
        * Decimal("1.20")
    )
    assert abs(float(raw_val) - expected) < 1e-7


# ---------------------------------------------------------------------------
# § Scenario 7 — GET /admin/usage: totals + ≤50 records for authenticated tenant
# ---------------------------------------------------------------------------


async def test_admin_usage_totals_and_records(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """GET /admin/usage returns correct totals and record list from the ledger."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)

    # Push 3 usage events and flush them all
    for _ in range(3):
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=active_model_with_pricing["model_id"],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            status=200,
        )
    await flusher.flush_once()

    resp = await client.get(ADMIN_USAGE, headers=bearer_header(api_key["jwt"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] == 3
    assert body["total_prompt_tokens"] == 30
    assert body["total_completion_tokens"] == 15
    # total_cost_usd is a string (exact Decimal)
    total_cost = Decimal(body["total_cost_usd"])
    assert total_cost > Decimal("0")

    records = body["records"]
    assert isinstance(records, list)
    assert len(records) <= 50
    assert len(records) == 3

    for rec in records:
        assert "id" in rec
        assert "model_id" in rec
        assert "prompt_tokens" in rec
        assert "completion_tokens" in rec
        assert "cost_usd" in rec
        assert "status" in rec
        assert "created_at" in rec


# ---------------------------------------------------------------------------
# § Scenario 8 — GET /admin/usage tenant isolation
# ---------------------------------------------------------------------------


async def test_admin_usage_tenant_isolation(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    api_key_b: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """Tenant B sees only their own rows; tenant A's rows are invisible."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)

    # Tenant A: 2 rows
    for _ in range(2):
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=active_model_with_pricing["model_id"],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            status=200,
        )
    # Tenant B: 1 row
    await recorder.record(
        tenant_id=uuid.UUID(api_key_b["tenant_id"]),
        key_id=uuid.UUID(api_key_b["key_id"]),
        model=active_model_with_pricing["model_id"],
        usage={"prompt_tokens": 20, "completion_tokens": 10},
        status=200,
    )
    await flusher.flush_once()

    # Tenant B queries their usage
    resp = await client.get(ADMIN_USAGE, headers=bearer_header(api_key_b["jwt"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] == 1
    records = body["records"]
    assert len(records) == 1
    # None of tenant A's rows should appear
    for rec in records:
        assert rec["prompt_tokens"] == 20
        assert rec["completion_tokens"] == 10


# ---------------------------------------------------------------------------
# § Scenario 9 — GET /admin/usage rejected without JWT
# ---------------------------------------------------------------------------


async def test_admin_usage_rejected_without_jwt(
    client: httpx.AsyncClient,
) -> None:
    """Missing JWT → 401 ERR_AUTH_INVALID_TOKEN; no usage data in response."""
    resp = await client.get(ADMIN_USAGE)

    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    body = resp.json()
    assert "records" not in body
    assert "total_requests" not in body


# ===========================================================================
# usage-flusher-durability (B4) — recorder durable fallback + Redis timeout
# ===========================================================================
# Fakes exercised only by the B4 durability tests below.


class _XaddFailsRedis:
    """Redis stand-in whose XADD always fails (Redis unavailable for writes).

    Nothing else is reached in the record path before the XADD, so only xadd
    (and, defensively, incrbyfloat) need to raise.
    """

    async def xadd(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("XADD unavailable (_XaddFailsRedis fake)")

    async def incrbyfloat(self, *args: Any, **kwargs: Any) -> float:
        # Must never be reached once XADD fails (advisory counters are gated on a
        # successful XADD); defensive so a regression here fails loudly, not silently.
        raise ConnectionError("incrbyfloat unavailable (_XaddFailsRedis fake)")


class _SlowButLandedRedis:
    """Wrap a real redis client: the XADD write DOES land in the stream, but the
    call then raises TimeoutError (a slow ack the client gave up waiting on).

    Models the no-double-bill race: the recorder's bounded timeout fires (→ direct
    fallback INSERT), yet the event later flushes from the stream. Both must key on
    the SAME deterministic id and collapse to one row via ON CONFLICT (id).
    All other calls delegate to the real client so the flusher can read the event.
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    async def xadd(self, key: str, fields: dict[str, str]) -> bytes:
        landed = await self._real.xadd(key, fields)  # the write DID land
        raise TimeoutError("XADD ack timed out (_SlowButLandedRedis fake)")
        return landed  # noqa: unreachable — documents the real return contract

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _ExplodingSessionFactory:
    """Session factory whose session is unusable — models Postgres also down.

    Records call count so a test can prove the durable fallback was ATTEMPTED
    (not merely that record() stayed quiet — the pre-fix code also stays quiet
    but never reaches the fallback).
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        raise ConnectionError("Postgres unavailable (_ExplodingSessionFactory fake)")


async def test_redis_xadd_failure_falls_back_to_postgres(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
) -> None:
    """B4: a failed XADD persists the usage event DIRECTLY to the ledger (durable
    fallback), and record() does not raise. RED pre-fix: no fallback → 0 rows."""
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=_XaddFailsRedis(),  # type: ignore[arg-type]
        session_factory=app.state.sessionmaker,
    )
    result = await recorder.record(
        tenant_id=uuid.UUID(api_key["tenant_id"]),
        key_id=uuid.UUID(api_key["key_id"]),
        model=active_model_with_pricing["model_id"],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        status=200,
    )
    assert result is None  # never raises into the proxy path

    rows = (
        await db_session.execute(
            text("SELECT * FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).fetchall()
    assert len(rows) == 1, "XADD failure must leave a durable row via the fallback"
    row = rows[0]._mapping  # type: ignore[union-attr]
    assert row["model_id"] == active_model_with_pricing["model_id"]
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5


async def test_slow_redis_no_double_bill(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """B4 invariant: fallback fired AND the same event later flushes → exactly ONE
    row (same deterministic id → ON CONFLICT). RED pre-fix: 0 rows after record()."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=_SlowButLandedRedis(redis_client),  # type: ignore[arg-type]
        session_factory=app.state.sessionmaker,
    )
    await recorder.record(
        tenant_id=uuid.UUID(api_key["tenant_id"]),
        key_id=uuid.UUID(api_key["key_id"]),
        model=active_model_with_pricing["model_id"],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        status=200,
    )

    # The fallback must have persisted exactly one row already (before any flush).
    count_after_record = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).scalar()
    assert count_after_record == 1, "durable fallback row must exist after record()"

    # The XADD landed too — flushing it must NOT create a second row (same id).
    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    count_after_flush = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).scalar()
    assert count_after_flush == 1, "slow-but-recovered Redis must not double-bill"


async def test_both_stores_down_swallows_and_stays_200() -> None:
    """B4 invariant: Redis XADD AND the Postgres fallback both fail → record()
    logs, swallows, returns None (never raises). RED pre-fix: fallback not
    attempted (exploding factory never called)."""
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    factory = _ExplodingSessionFactory()
    recorder = RecordingUsageRecorder(
        redis=_XaddFailsRedis(),  # type: ignore[arg-type]
        session_factory=factory,  # type: ignore[arg-type]
    )
    # cached=True skips the pricing DB read so the exploding factory is only hit at
    # the fallback — isolating the double-store-down path.
    result = await recorder.record(
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        model="openai/gpt-4o",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
        status=200,
        cached=True,
    )
    assert result is None, "record() must never raise, even with both stores down"
    assert factory.calls == 1, "the durable fallback must be attempted on XADD failure"


async def test_normal_record_carries_explicit_id_and_flusher_uses_it(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model_with_pricing: dict[str, Any],
    redis_client: Any,
) -> None:
    """B4-id: a normal record() writes a deterministic explicit "id" into the event,
    the flusher uses it as the row PK, and a double-flush stays idempotent.
    RED pre-fix: event carries no "id" field."""
    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]
    from gateway.usage.infrastructure.redis_stream import STREAM_KEY  # type: ignore[import]

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    await recorder.record(
        tenant_id=uuid.UUID(api_key["tenant_id"]),
        key_id=uuid.UUID(api_key["key_id"]),
        model=active_model_with_pricing["model_id"],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        status=200,
    )

    # The stream event must carry an explicit "id".
    entries = await redis_client.xrange(STREAM_KEY)
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    id_field = fields.get(b"id") if isinstance(next(iter(fields), b""), bytes) else fields.get("id")
    assert id_field is not None, "normal record() must write an explicit event id"
    explicit_id = uuid.UUID(id_field.decode() if isinstance(id_field, bytes) else id_field)

    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    rows = (
        await db_session.execute(
            text("SELECT id FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]._mapping["id"] == explicit_id, "flusher must use the explicit id as the PK"

    # Second flush of the same (still-pending until ACK) event → still one row.
    await flusher.flush_once()
    count_second = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE tenant_id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).scalar()
    assert count_second == 1, "double-flush must stay idempotent on the explicit id"
