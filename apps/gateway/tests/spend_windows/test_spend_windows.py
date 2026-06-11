"""Failing-first (RED) suite for spend-windows (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.

Right-reason red targets:
  - GET /admin/spend endpoint does not exist → FastAPI returns 404, so asserting 200 FAILS.
  - alert_events table does not exist → query raises ProgrammingError, so asserting COUNT==1
    raises and FAILS (not caught/swallowed).
  - Windowed aggregation SQL over usage_records is not implemented → 404 response, so
    asserting 200 with exact bucket aggregates FAILS.

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys, /admin/spend, /v1/chat/completions

Source of truth for aggregates: usage_records Postgres ledger (NOT Redis counters).
Soft-budget crossing: persists ONE alert_events row per key+window (idempotent via
  UNIQUE dedupe_key constraint); never blocks the hot path.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 (db index 9, flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network — same as existing suites)
  - asyncio_mode = "auto" (set in pyproject.toml — no @pytest.mark.asyncio needed)
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
ADMIN_SPEND = "/admin/spend"
ADMIN_KEYS = "/admin/keys"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
COMPLETIONS = "/v1/chat/completions"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; full body: {body}"
    )
    assert body.get("status") == status
    assert "title" in body
    return body


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _yyyymm() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m")


def _spend_key_key(key_id: str) -> str:
    return f"usage:spend:key:{key_id}:{_yyyymm()}"


def _naive_utc(dt: datetime.datetime) -> datetime.datetime:
    """Strip timezone info for asyncpg TIMESTAMPTZ parameters (it interprets as UTC)."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — consistent with key_governance pattern."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-sw-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-sw-1","choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model for proxy/budget tests."""
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.commit()
    return model_id


# ---------------------------------------------------------------------------
# Shared ledger seeder — bypasses recorder for deterministic reconciliation tests
# ---------------------------------------------------------------------------


async def _seed_usage_records(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    rows: list[dict[str, Any]],
) -> None:
    """Insert ledger rows directly for reconciliation tests.

    Each row dict: {prompt_tokens, completion_tokens, cost_usd, status?, bucket_ts}
    bucket_ts: datetime (tz-aware OK; stripped to naive for asyncpg TIMESTAMPTZ).
    """
    for row in rows:
        rid = str(uuid.uuid4())
        ts = _naive_utc(row["bucket_ts"])
        await session.execute(
            text(
                "INSERT INTO usage_records"
                " (id, tenant_id, key_id, model_id, prompt_tokens,"
                "  completion_tokens, cost_usd, status, raw, created_at)"
                " VALUES (:id, :tid, :kid, :mid, :pt, :ct, :cost, :status, :raw, :ts)"
            ),
            {
                "id": rid,
                "tid": tenant_id,
                "kid": key_id,
                "mid": "openai/gpt-4o",
                "pt": row["prompt_tokens"],
                "ct": row["completion_tokens"],
                "cost": str(row["cost_usd"]),
                "status": row.get("status", 200),
                "raw": "{}",
                "ts": ts,
            },
        )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests — one per SCENARIO
# ---------------------------------------------------------------------------


# ── S1: Windowed aggregates reconcile exactly with ledger rows ───────────────


async def test_windowed_aggregates_reconcile_with_ledger(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """S1: GET /admin/spend?window=month returns aggregates equal to SUM(usage_records).

    EXIT CRITERION: "aggregates must equal SUM over usage_records for the same window"
    (MILESTONE.md exit criteria, spend-windows task).

    RED reason: route /admin/spend not registered → FastAPI returns 404; asserting 200 FAILS.
    GREEN contract: 200 with totals.cost_usd == sum of seeded values.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Reconcile Corp", email="recon@acme.io"
    )
    kr = await client.post(ADMIN_KEYS, json={"name": "recon-key"}, headers=auth_jwt(jwt))
    assert kr.status_code == 201
    key_id = kr.json()["key_id"]

    now_utc = datetime.datetime.now(datetime.UTC)
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    seeded = [
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cost_usd": Decimal("0.00250000"),
            "bucket_ts": month_start + datetime.timedelta(hours=1),
        },
        {
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "cost_usd": Decimal("0.00500000"),
            "bucket_ts": month_start + datetime.timedelta(hours=2),
        },
        {
            "prompt_tokens": 50,
            "completion_tokens": 25,
            "cost_usd": Decimal("0.00125000"),
            "bucket_ts": month_start + datetime.timedelta(hours=3),
        },
    ]
    await _seed_usage_records(db_session, tenant_id=tenant_id, key_id=key_id, rows=seeded)

    expected_cost = sum(r["cost_usd"] for r in seeded)
    expected_requests = len(seeded)
    expected_prompt = sum(r["prompt_tokens"] for r in seeded)
    expected_completion = sum(r["completion_tokens"] for r in seeded)

    # Act — RED: endpoint not built yet → 404; asserting 200 will FAIL
    resp = await client.get(ADMIN_SPEND, params={"window": "month"}, headers=auth_jwt(jwt))

    # TARGET behavior: 200 with exact aggregates matching seeded sums
    assert resp.status_code == 200, (
        f"Expected 200 with windowed aggregates, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["window"] == "month"
    totals = body["totals"]
    assert totals["requests"] == expected_requests, (
        f"requests: expected {expected_requests}, got {totals['requests']}"
    )
    assert totals["prompt_tokens"] == expected_prompt, (
        f"prompt_tokens: expected {expected_prompt}, got {totals['prompt_tokens']}"
    )
    assert totals["completion_tokens"] == expected_completion, (
        f"completion_tokens: expected {expected_completion}, got {totals['completion_tokens']}"
    )
    # cost_usd is a Decimal string — compare as Decimal for exactness
    assert Decimal(totals["cost_usd"]) == expected_cost, (
        f"cost_usd: expected {expected_cost}, got {totals['cost_usd']}"
    )


# ── S2: Bucket boundaries — UTC date_trunc alignment ────────────────────────


async def test_daily_bucket_boundaries_align_to_utc_date_trunc(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """S2: window=day returns buckets aligned to UTC midnight (date_trunc('day', created_at)).

    Arrangement: seed rows on two distinct UTC days.
    RED reason: route missing → 404; asserting 200 with 2 buckets FAILS.
    GREEN contract: buckets list has 2 items; each item sum matches the seeded day only.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Bucket Corp", email="bucket@acme.io"
    )
    kr = await client.post(ADMIN_KEYS, json={"name": "bucket-key"}, headers=auth_jwt(jwt))
    assert kr.status_code == 201
    key_id = kr.json()["key_id"]

    now_utc = datetime.datetime.now(datetime.UTC)
    day_today = now_utc.replace(hour=10, minute=0, second=0, microsecond=0)
    day_yesterday = (now_utc - datetime.timedelta(days=1)).replace(
        hour=5, minute=0, second=0, microsecond=0
    )

    await _seed_usage_records(
        db_session,
        tenant_id=tenant_id,
        key_id=key_id,
        rows=[
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "cost_usd": Decimal("0.00100000"),
                "bucket_ts": day_today,
            },
            {
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "cost_usd": Decimal("0.00200000"),
                "bucket_ts": day_yesterday,
            },
        ],
    )

    resp = await client.get(
        ADMIN_SPEND,
        params={
            "window": "day",
            "start": day_yesterday.date().isoformat(),
            "end": day_today.date().isoformat(),
        },
        headers=auth_jwt(jwt),
    )
    # TARGET behavior: 200 with exactly 2 day-aligned buckets
    assert resp.status_code == 200, (
        f"Expected 200 with day buckets, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    buckets = body["buckets"]
    assert len(buckets) == 2, (
        f"Expected 2 day buckets (one per seeded day), got {len(buckets)}: {buckets}"
    )
    # Each bucket's cost must match only that day's seeded record
    costs = {Decimal(b["cost_usd"]) for b in buckets}
    assert Decimal("0.00100000") in costs, "Today's bucket cost missing"
    assert Decimal("0.00200000") in costs, "Yesterday's bucket cost missing"
    # bucket_start fields must be distinct UTC midnights
    starts = {b["bucket_start"] for b in buckets}
    assert len(starts) == 2, f"Expected 2 distinct bucket_start values, got {starts}"


# ── S3: key_id filter scopes aggregates to a single key ─────────────────────


async def test_key_id_filter_scopes_aggregates(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """S3: GET /admin/spend?window=month&key_id={id} returns only that key's rows.

    RED reason: route missing → 404; asserting 200 with key-A-only cost FAILS.
    GREEN contract: totals reflect only key-A cost; key-B cost absent.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Filter Corp", email="filter@acme.io"
    )
    kr_a = await client.post(ADMIN_KEYS, json={"name": "key-a"}, headers=auth_jwt(jwt))
    kr_b = await client.post(ADMIN_KEYS, json={"name": "key-b"}, headers=auth_jwt(jwt))
    assert kr_a.status_code == 201 and kr_b.status_code == 201
    key_a_id = kr_a.json()["key_id"]
    key_b_id = kr_b.json()["key_id"]

    now_utc = datetime.datetime.now(datetime.UTC)
    ts = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)

    await _seed_usage_records(
        db_session,
        tenant_id=tenant_id,
        key_id=key_a_id,
        rows=[
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "cost_usd": Decimal("0.00100000"),
                "bucket_ts": ts,
            }
        ],
    )
    await _seed_usage_records(
        db_session,
        tenant_id=tenant_id,
        key_id=key_b_id,
        rows=[
            {
                "prompt_tokens": 999,
                "completion_tokens": 999,
                "cost_usd": Decimal("0.99000000"),
                "bucket_ts": ts,
            }
        ],
    )

    resp = await client.get(
        ADMIN_SPEND,
        params={"window": "month", "key_id": key_a_id},
        headers=auth_jwt(jwt),
    )
    # TARGET behavior: 200 with only key-A's cost
    assert resp.status_code == 200, (
        f"Expected 200 with key-A-filtered aggregates, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    totals = body["totals"]
    assert Decimal(totals["cost_usd"]) == Decimal("0.00100000"), (
        f"Expected only key-A cost 0.00100000, got {totals['cost_usd']} "
        f"(key-B cost 0.99000000 must not appear)"
    )
    assert totals["requests"] == 1, f"Expected 1 request (key-A only), got {totals['requests']}"


# ── S4: group_by=key_id returns per-key breakdown ───────────────────────────


async def test_group_by_key_id_returns_per_key_breakdown(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """S4: GET /admin/spend?window=month&group_by=key_id returns separate rows per key.

    RED reason: route missing → 404; asserting 200 with breakdown of 2 items FAILS.
    GREEN contract: body["breakdown"] has 2 items, each with key_id + aggregates.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Group Corp", email="group@acme.io"
    )
    kr_a = await client.post(ADMIN_KEYS, json={"name": "gkey-a"}, headers=auth_jwt(jwt))
    kr_b = await client.post(ADMIN_KEYS, json={"name": "gkey-b"}, headers=auth_jwt(jwt))
    assert kr_a.status_code == 201 and kr_b.status_code == 201
    key_a_id = kr_a.json()["key_id"]
    key_b_id = kr_b.json()["key_id"]

    now_utc = datetime.datetime.now(datetime.UTC)
    ts = now_utc.replace(hour=6, minute=0, second=0, microsecond=0)

    await _seed_usage_records(
        db_session,
        tenant_id=tenant_id,
        key_id=key_a_id,
        rows=[
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": Decimal("0.00010000"),
                "bucket_ts": ts,
            }
        ],
    )
    await _seed_usage_records(
        db_session,
        tenant_id=tenant_id,
        key_id=key_b_id,
        rows=[
            {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "cost_usd": Decimal("0.00020000"),
                "bucket_ts": ts,
            }
        ],
    )

    resp = await client.get(
        ADMIN_SPEND,
        params={"window": "month", "group_by": "key_id"},
        headers=auth_jwt(jwt),
    )
    # TARGET behavior: 200 with breakdown containing both keys
    assert resp.status_code == 200, (
        f"Expected 200 with per-key breakdown, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    breakdown = body.get("breakdown")
    assert breakdown is not None, "Expected 'breakdown' key in response when group_by=key_id"
    assert len(breakdown) == 2, (
        f"Expected 2 breakdown items (one per key), got {len(breakdown)}: {breakdown}"
    )
    # Each item must have required fields
    for item in breakdown:
        assert "key_id" in item, f"breakdown item missing key_id: {item}"
        assert "requests" in item, f"breakdown item missing requests: {item}"
        assert "prompt_tokens" in item, f"breakdown item missing prompt_tokens: {item}"
        assert "completion_tokens" in item, f"breakdown item missing completion_tokens: {item}"
        assert "cost_usd" in item, f"breakdown item missing cost_usd: {item}"
    # Verify per-key cost values are correct
    breakdown_by_key = {item["key_id"]: item for item in breakdown}
    assert key_a_id in breakdown_by_key, f"key-A ({key_a_id}) not in breakdown"
    assert key_b_id in breakdown_by_key, f"key-B ({key_b_id}) not in breakdown"
    assert Decimal(breakdown_by_key[key_a_id]["cost_usd"]) == Decimal("0.00010000")
    assert Decimal(breakdown_by_key[key_b_id]["cost_usd"]) == Decimal("0.00020000")


# ── S5: Empty window returns zeros, not 404 ──────────────────────────────────


async def test_empty_window_returns_zeros_not_404(
    client: httpx.AsyncClient,
) -> None:
    """S5: GET /admin/spend?window=month on tenant with no usage_records returns 200 zeros.

    IMPORTANT: must NOT return 404 on empty data — empty is a valid state.
    RED reason: route missing → 404; asserting 200 with zeros FAILS.
    GREEN contract: 200 with cost_usd="0", requests=0 for all numeric fields.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Empty Corp", email="empty@acme.io"
    )

    resp = await client.get(ADMIN_SPEND, params={"window": "month"}, headers=auth_jwt(jwt))
    # TARGET behavior: 200 with all-zero totals (empty is valid, not 404)
    assert resp.status_code == 200, (
        f"Expected 200 with zero totals (empty window is valid), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    totals = body["totals"]
    assert totals["requests"] == 0, f"Expected requests=0 for empty window, got {totals['requests']}"
    assert totals["prompt_tokens"] == 0, f"Expected prompt_tokens=0, got {totals['prompt_tokens']}"
    assert totals["completion_tokens"] == 0, (
        f"Expected completion_tokens=0, got {totals['completion_tokens']}"
    )
    assert Decimal(totals["cost_usd"]) == Decimal("0"), (
        f"Expected cost_usd='0', got {totals['cost_usd']}"
    )
    assert body["buckets"] == [], (
        f"Expected empty buckets list for empty window, got {body['buckets']}"
    )


# ── S6: Invalid window parameter → 422 ──────────────────────────────────────


async def test_invalid_window_param_returns_422(
    client: httpx.AsyncClient,
) -> None:
    """S6: GET /admin/spend?window=fortnight (invalid) → 422 ERR_PAYLOAD_INVALID.

    window must be one of: day, week, month.
    RED reason: route missing → 404; asserting 422 ERR_PAYLOAD_INVALID FAILS.
    GREEN contract: 422 ERR_PAYLOAD_INVALID.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Invalid Corp", email="invalid@acme.io"
    )

    resp = await client.get(
        ADMIN_SPEND, params={"window": "fortnight"}, headers=auth_jwt(jwt)
    )
    # TARGET behavior: 422 ERR_PAYLOAD_INVALID (not 404 — route exists, param invalid)
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ── S7: Unauthenticated request → 401 ───────────────────────────────────────


async def test_unauthenticated_returns_401(
    client: httpx.AsyncClient,
) -> None:
    """S7: GET /admin/spend without Authorization header → 401 ERR_AUTH_INVALID_TOKEN.

    RED reason: route missing → 404; asserting 401 ERR_AUTH_INVALID_TOKEN FAILS.
    GREEN contract: 401 ERR_AUTH_INVALID_TOKEN.
    """
    resp = await client.get(ADMIN_SPEND, params={"window": "month"})
    # TARGET behavior: 401 (auth check fires before aggregation)
    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")


# ── S8: Tenant isolation — cross-tenant rows not visible ─────────────────────


async def test_spend_tenant_isolation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """S8: Spend response for tenant A must not include usage_records from tenant B.

    RED reason: route missing → 404; asserting 200 with tenant-A-only cost FAILS.
    GREEN contract: totals.cost_usd for tenant-A excludes tenant-B rows.
    """
    jwt_a, tenant_a_id = await signup_and_login(
        client, tenant_name="TenantA Corp", email="a@iso.io"
    )
    jwt_b, tenant_b_id = await signup_and_login(
        client, tenant_name="TenantB Corp", email="b@iso.io"
    )

    kr_a = await client.post(ADMIN_KEYS, json={"name": "a-key"}, headers=auth_jwt(jwt_a))
    kr_b = await client.post(ADMIN_KEYS, json={"name": "b-key"}, headers=auth_jwt(jwt_b))
    assert kr_a.status_code == 201 and kr_b.status_code == 201
    key_a_id = kr_a.json()["key_id"]
    key_b_id = kr_b.json()["key_id"]

    now_utc = datetime.datetime.now(datetime.UTC)
    ts = now_utc.replace(hour=7, minute=0, second=0, microsecond=0)

    await _seed_usage_records(
        db_session,
        tenant_id=tenant_a_id,
        key_id=key_a_id,
        rows=[
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": Decimal("0.00010000"),
                "bucket_ts": ts,
            }
        ],
    )
    await _seed_usage_records(
        db_session,
        tenant_id=tenant_b_id,
        key_id=key_b_id,
        rows=[
            {
                "prompt_tokens": 999,
                "completion_tokens": 999,
                "cost_usd": Decimal("9.99000000"),
                "bucket_ts": ts,
            }
        ],
    )

    resp = await client.get(ADMIN_SPEND, params={"window": "month"}, headers=auth_jwt(jwt_a))
    # TARGET behavior: 200 showing only tenant-A's cost (tenant-B's 9.99 must not appear)
    assert resp.status_code == 200, (
        f"Expected 200 with tenant-A only cost, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    totals = body["totals"]
    assert Decimal(totals["cost_usd"]) == Decimal("0.00010000"), (
        f"Expected tenant-A cost 0.00010000 only; tenant-B 9.99 must not appear. "
        f"Got cost_usd={totals['cost_usd']}"
    )
    assert totals["requests"] == 1, f"Expected 1 request (tenant-A only), got {totals['requests']}"


# ── S9: week and month window params accepted ────────────────────────────────


async def test_window_week_accepted(
    client: httpx.AsyncClient,
) -> None:
    """S9: window=week is valid; returns 200 with bucket_start aligned to ISO week Monday.

    RED reason: route missing → 404; asserting 200 FAILS.
    GREEN contract: 200 with window="week" in response body.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="Week Corp", email="week@acme.io"
    )
    resp = await client.get(ADMIN_SPEND, params={"window": "week"}, headers=auth_jwt(jwt))
    # TARGET behavior: 200 (window=week is valid)
    assert resp.status_code == 200, (
        f"Expected 200 for window=week (valid param), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("window") == "week", f"Expected window='week' in body, got {body.get('window')}"


async def test_window_month_accepted(
    client: httpx.AsyncClient,
) -> None:
    """S9b: window=month returns 200 with calendar-month bucket_start.

    RED reason: route missing → 404; asserting 200 FAILS.
    GREEN contract: 200 with window="month" in response body.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="Month Corp", email="month@acme.io"
    )
    resp = await client.get(ADMIN_SPEND, params={"window": "month"}, headers=auth_jwt(jwt))
    # TARGET behavior: 200 (window=month is valid)
    assert resp.status_code == 200, (
        f"Expected 200 for window=month (valid param), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("window") == "month", (
        f"Expected window='month' in body, got {body.get('window')}"
    )


# ── S10: start/end ISO overrides filter the window ───────────────────────────


async def test_start_end_iso_overrides_filter_window(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """S10: GET /admin/spend?start=YYYY-MM-DD&end=YYYY-MM-DD overrides window boundary.

    Arrangement: seed rows on 3 distinct days; start/end spans only 2 days.
    RED reason: route missing → 404; asserting 200 with only 2-day cost FAILS.
    GREEN contract: totals.cost_usd covers only the 2 in-range days.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Range Corp", email="range@acme.io"
    )
    kr = await client.post(ADMIN_KEYS, json={"name": "range-key"}, headers=auth_jwt(jwt))
    assert kr.status_code == 201
    key_id = kr.json()["key_id"]

    now_utc = datetime.datetime.now(datetime.UTC)
    day_in_1 = (now_utc - datetime.timedelta(days=3)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    day_in_2 = (now_utc - datetime.timedelta(days=2)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )
    day_out = (now_utc - datetime.timedelta(days=10)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )

    await _seed_usage_records(
        db_session,
        tenant_id=tenant_id,
        key_id=key_id,
        rows=[
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": Decimal("0.00010000"),
                "bucket_ts": day_in_1,
            },
            {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "cost_usd": Decimal("0.00020000"),
                "bucket_ts": day_in_2,
            },
            {
                "prompt_tokens": 999,
                "completion_tokens": 999,
                "cost_usd": Decimal("9.99000000"),
                "bucket_ts": day_out,
            },
        ],
    )

    resp = await client.get(
        ADMIN_SPEND,
        params={
            "window": "day",
            "start": day_in_1.date().isoformat(),
            "end": day_in_2.date().isoformat(),
        },
        headers=auth_jwt(jwt),
    )
    # TARGET behavior: 200 with only in-range cost (day_out record excluded)
    assert resp.status_code == 200, (
        f"Expected 200 with start/end filtered cost, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    totals = body["totals"]
    expected_cost = Decimal("0.00010000") + Decimal("0.00020000")
    assert Decimal(totals["cost_usd"]) == expected_cost, (
        f"Expected cost {expected_cost} (2 in-range days only); day_out (9.99) must be excluded. "
        f"Got {totals['cost_usd']}"
    )
    assert totals["requests"] == 2, (
        f"Expected 2 requests (in-range only), got {totals['requests']}"
    )


# ── S11: Soft-budget crossing persists exactly ONE alert_events row ──────────


async def test_soft_budget_crossing_persists_one_alert_event(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    active_model: str,
) -> None:
    """S11: When per-key spend crosses soft_budget_usd, exactly ONE alert_events row
    is persisted (idempotent via UNIQUE dedupe_key constraint).

    RIGHT-REASON RED: alert_events table does not exist → querying it raises
    ProgrammingError, so the COUNT assertion cannot pass. The test FAILS on the
    SELECT COUNT(*) FROM alert_events call with a ProgrammingError (not caught).

    GREEN contract:
      - All 3 completions return 200 (soft budget never blocks)
      - Exactly 1 row in alert_events with dedupe_key="soft_budget:{key_id}:{YYYYMM}"
      - Row has event_type="soft_budget_exceeded"
      - Row payload contains soft_budget_usd and key_spend_usd fields
      - delivered_at IS NULL
    """
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # noqa: PLC0415

    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Alert Corp", email="alert@acme.io"
    )

    kr = await client.post(
        ADMIN_KEYS,
        json={"name": "soft-key", "soft_budget_usd": "0.00050000"},
        headers=auth_jwt(jwt),
    )
    assert kr.status_code == 201, f"key create failed: {kr.text}"
    key_id = kr.json()["key_id"]
    plaintext_key = kr.json()["key"]

    # Seed Redis counter far above soft budget so crossing is detected on every call
    await redis_client.set(_spend_key_key(key_id), "1.00000000")
    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client, session_factory=app.state.sessionmaker
    )

    fake_upstream = FakeCompletionUpstream()
    app.state.completion_upstream = fake_upstream

    # Act: make 3 completions — all must return 200 (soft budget never blocks)
    for i in range(3):
        resp = await client.post(
            COMPLETIONS,
            json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
            headers=auth_key(plaintext_key),
        )
        assert resp.status_code == 200, (
            f"Completion {i+1}/3 blocked by soft budget — must never block: "
            f"{resp.status_code} {resp.text}"
        )

    assert fake_upstream.calls == 3, f"Expected 3 upstream calls, got {fake_upstream.calls}"

    # Allow fire-and-forget task to complete
    import asyncio
    await asyncio.sleep(0.05)

    # Assert: exactly 1 row in alert_events with correct shape
    # RED: this SELECT raises ProgrammingError (table absent) → test FAILS for right reason
    row = (
        await db_session.execute(
            text(
                "SELECT event_type, dedupe_key, payload, delivered_at"
                " FROM alert_events"
                " WHERE key_id = :kid"
                " ORDER BY created_at DESC"
                " LIMIT 1"
            ),
            {"kid": key_id},
        )
    ).fetchone()

    assert row is not None, (
        f"Expected 1 alert_events row for key {key_id}, got none"
    )

    count_row = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM alert_events WHERE key_id = :kid"),
            {"kid": key_id},
        )
    ).fetchone()
    assert count_row is not None and int(count_row[0]) == 1, (
        f"Expected exactly 1 alert_events row (idempotent), got {count_row[0] if count_row else 'none'}"
    )

    yyyymm = _yyyymm()
    expected_dedupe_key = f"soft_budget:{key_id}:{yyyymm}"
    assert row[0] == "soft_budget_exceeded", (
        f"event_type: expected 'soft_budget_exceeded', got {row[0]!r}"
    )
    assert row[1] == expected_dedupe_key, (
        f"dedupe_key: expected {expected_dedupe_key!r}, got {row[1]!r}"
    )
    # payload must contain soft_budget_usd and key_spend_usd
    import json as _json
    payload = row[2] if isinstance(row[2], dict) else _json.loads(row[2])
    assert "soft_budget_usd" in payload, f"payload missing soft_budget_usd: {payload}"
    assert "key_spend_usd" in payload, f"payload missing key_spend_usd: {payload}"
    # delivered_at must be NULL at creation
    assert row[3] is None, f"delivered_at must be NULL at creation, got {row[3]}"
    _ = tenant_id  # used in arrange above


# ── S12: Soft-budget idempotency — UNIQUE constraint prevents duplicate rows ──


async def test_soft_budget_alert_idempotent_unique_constraint(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """S12: ON CONFLICT (dedupe_key) DO NOTHING makes repeated crossings produce 1 row.

    Direct DDL test: attempt two INSERTs with the same dedupe_key via raw SQL.
    RIGHT-REASON RED: alert_events table does not exist → first INSERT raises
    ProgrammingError, so the test FAILS for the right reason (table absent).
    GREEN contract: second INSERT silently ignored; COUNT(*) == 1.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Idem Corp", email="idem@acme.io"
    )
    kr = await client.post(ADMIN_KEYS, json={"name": "idem-key"}, headers=auth_jwt(jwt))
    assert kr.status_code == 201
    key_id = kr.json()["key_id"]

    yyyymm = _yyyymm()
    dedupe_key = f"soft_budget:{key_id}:{yyyymm}"
    row_id_1 = str(uuid.uuid4())
    row_id_2 = str(uuid.uuid4())

    # First INSERT — RED: table absent → ProgrammingError → test FAILS for right reason
    await db_session.execute(
        text(
            "INSERT INTO alert_events"
            " (id, tenant_id, key_id, event_type, payload, dedupe_key, created_at)"
            " VALUES (:id, :tid, :kid, :etype, :payload::jsonb, :dk, now())"
            " ON CONFLICT (dedupe_key) DO NOTHING"
        ),
        {
            "id": row_id_1,
            "tid": tenant_id,
            "kid": key_id,
            "etype": "soft_budget_exceeded",
            "payload": '{"soft_budget_usd": "0.01", "key_spend_usd": "0.02"}',
            "dk": dedupe_key,
        },
    )
    await db_session.commit()

    # Second INSERT with same dedupe_key — ON CONFLICT DO NOTHING silently discards it
    await db_session.execute(
        text(
            "INSERT INTO alert_events"
            " (id, tenant_id, key_id, event_type, payload, dedupe_key, created_at)"
            " VALUES (:id, :tid, :kid, :etype, :payload::jsonb, :dk, now())"
            " ON CONFLICT (dedupe_key) DO NOTHING"
        ),
        {
            "id": row_id_2,
            "tid": tenant_id,
            "kid": key_id,
            "etype": "soft_budget_exceeded",
            "payload": '{"soft_budget_usd": "0.01", "key_spend_usd": "0.03"}',
            "dk": dedupe_key,
        },
    )
    await db_session.commit()

    # Assert: exactly 1 row despite 2 INSERTs (UNIQUE constraint is the idempotency gate)
    count_row = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM alert_events WHERE dedupe_key = :dk"),
            {"dk": dedupe_key},
        )
    ).fetchone()
    assert count_row is not None and int(count_row[0]) == 1, (
        f"Expected exactly 1 alert_events row after 2 INSERTs with same dedupe_key, "
        f"got {count_row[0] if count_row else 'none'} "
        f"(ON CONFLICT DO NOTHING must silently ignore the second)"
    )


# ── S13: Soft-budget crossing never blocks or raises into the response ────────


async def test_soft_budget_crossing_never_blocks_response(
    client: httpx.AsyncClient,
    app: Any,
    redis_client: Any,
    active_model: str,
) -> None:
    """S13: When soft budget is crossed, the completion MUST succeed (200).

    Soft budget is advisory only — it NEVER returns 402.
    This is a CONTRACT LOCK test (invariant already implemented from key-governance build).
    It asserts the invariant that must hold BOTH before AND after the spend-windows build.
    It passes now and must continue to pass after build (legitimately green).

    INVARIANT LOCK JUSTIFICATION: The non-blocking soft-budget behavior was implemented
    and verified in the key-governance phase. This test locks that invariant so the
    spend-windows build cannot accidentally regress it. It correctly passes now because
    the implementation is already present.
    """
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # noqa: PLC0415

    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="NoBlock Corp", email="noblock@acme.io"
    )
    kr = await client.post(
        ADMIN_KEYS,
        json={"name": "noblock-key", "soft_budget_usd": "0.00001000"},
        headers=auth_jwt(jwt),
    )
    assert kr.status_code == 201
    plaintext_key = kr.json()["key"]
    key_id = kr.json()["key_id"]

    # Seed Redis counter far above soft budget
    await redis_client.set(_spend_key_key(key_id), "1.00000000")
    # Wire real redis into app so the per-key counter GET reads it
    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client, session_factory=app.state.sessionmaker
    )

    fake_upstream = FakeCompletionUpstream()
    app.state.completion_upstream = fake_upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(plaintext_key),
    )
    # Soft budget must NEVER block — 200 always
    assert resp.status_code == 200, (
        f"Soft budget crossing blocked — must never block: {resp.status_code} {resp.text}"
    )
    assert fake_upstream.calls == 1, "upstream must be called exactly once"


# ── S14: Redis counter unavailable → no alert_events, no failure ─────────────


async def test_redis_unavailable_no_alert_event_no_failure(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
) -> None:
    """S14: Redis unavailable → soft-budget detection skipped, completion succeeds.

    Fail-open contract: alert event creation is fire-and-forget; Redis failure
    must not propagate to the HTTP response.

    Two assertions:
    1. Completion returns 200 (fail-open — ALREADY correct from key-governance build;
       this part legitimately passes now and after build).
    2. alert_events COUNT == 0 (no INSERT attempted when Redis is down).
       RED reason: alert_events table absent → COUNT query raises ProgrammingError → FAILS.
       GREEN: table exists, count == 0 (no row inserted because Redis was unavailable).
    """
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # noqa: PLC0415

    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="FailOpen Corp", email="failopen@acme.io"
    )
    kr = await client.post(
        ADMIN_KEYS,
        json={"name": "failopen-key", "soft_budget_usd": "0.00001000"},
        headers=auth_jwt(jwt),
    )
    assert kr.status_code == 201
    plaintext_key = kr.json()["key"]

    class BrokenRedis:
        async def get(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("Redis unavailable")

        async def set(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("Redis unavailable")

        async def incrbyfloat(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("Redis unavailable")

        async def xadd(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("Redis unavailable")

    broken = BrokenRedis()
    app.state.budget_guard = RedisBudgetGuard(
        redis=broken, session_factory=app.state.sessionmaker
    )

    fake_upstream = FakeCompletionUpstream()
    app.state.completion_upstream = fake_upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(plaintext_key),
    )
    # Fail-open: Redis down → 200 (not 5xx)
    assert resp.status_code == 200, (
        f"Redis failure should fail-open (200), got {resp.status_code}: {resp.text}"
    )

    # Allow any fire-and-forget task to attempt (should not — no Redis crossing detected)
    import asyncio
    await asyncio.sleep(0.05)

    # Assert: NO alert_events row (Redis was unavailable → no crossing detected → no INSERT)
    # RED: table absent → ProgrammingError → test FAILS for right reason
    count_row = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM alert_events")
        )
    ).fetchone()
    assert count_row is not None and int(count_row[0]) == 0, (
        f"Expected 0 alert_events rows when Redis is unavailable (no crossing detected), "
        f"got {count_row[0] if count_row else 'none'}"
    )


# ── S15: member role (owner acting as member-role check note) ────────────────


async def test_admin_spend_requires_owner_or_admin_jwt(
    client: httpx.AsyncClient,
) -> None:
    """S15: GET /admin/spend requires a valid admin-tier JWT (owner role).

    Note: full member-role rejection requires the member-invite flow (not yet built).
    This test verifies owner JWT is accepted (200) and unauthenticated request is
    rejected (401).

    RED reason: route missing → 404 for both cases; asserting 200 for owner and
    401 for unauthenticated both FAIL.
    GREEN contract: owner JWT → 200; no JWT → 401 ERR_AUTH_INVALID_TOKEN.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="Auth Corp", email="auth@acme.io"
    )
    # Owner JWT → TARGET: 200
    resp_owner = await client.get(
        ADMIN_SPEND, params={"window": "month"}, headers=auth_jwt(jwt)
    )
    assert resp_owner.status_code == 200, (
        f"Expected 200 for owner JWT, got {resp_owner.status_code}: {resp_owner.text}"
    )

    # No JWT → TARGET: 401 ERR_AUTH_INVALID_TOKEN
    resp_unauth = await client.get(ADMIN_SPEND, params={"window": "month"})
    assert_problem(resp_unauth, 401, "ERR_AUTH_INVALID_TOKEN")
