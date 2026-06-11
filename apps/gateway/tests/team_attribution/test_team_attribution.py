"""Failing-first (RED) suite for team-attribution (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason.

Right-reason red targets per test:

  test_teamed_key_ledger_row_has_team_id:
    UsageRecordRow ORM has no team_id column → Base.metadata.create_all does NOT create
    the column → flusher INSERT succeeds (column absent, no FK error) → SELECT mapping has
    no "team_id" key → KeyError or AssertionError. Primary reason: ORM column absent.

  test_unteamed_key_ledger_row_null_team_id:
    Same root: column absent from ORM / table → row mapping has no team_id → KeyError.
    Secondary: even if column existed, event_fields has no team_id → flusher maps "" → NULL
    which would PASS; but column not in DB yet, so the test cannot even select it.

  test_old_format_event_flushes_with_null_team_id:
    Column absent from ORM / table → INSERT does not include team_id column → attempting to
    SELECT team_id from the row fails with KeyError or ProgrammingError. The test asserts
    row["team_id"] is None → KeyError because the key does not exist in the mapping.

  test_ledger_rollup_shows_ledger_cost_usd:
    TeamSpendBreakdownItem has no ledger_cost_usd field → GET /admin/spend returns items
    without this field → body.get("ledger_cost_usd") is None → AssertionError checking
    the field is present and matches the expected sum.

  test_counter_and_ledger_both_visible:
    Same as above: ledger_cost_usd absent from response → AssertionError.

  test_ledger_rollup_tenant_scoped:
    Same: ledger_cost_usd absent from response → AssertionError on field presence.
    (The tenant-scoping assertion on cost_usd would pass via existing group_by=team_id branch,
    but the ledger_cost_usd assertion fails first.)

  test_team_id_column_exists:
    UsageRecordRow ORM has no team_id column → create_all creates table WITHOUT team_id →
    SELECT team_id FROM usage_records LIMIT 0 → sqlalchemy.exc.ProgrammingError:
    column "team_id" does not exist. Test catches any exception except the AssertionError
    "column exists" and marks as failed.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
    (schema rebuilt per test via conftest.py fixture — Base.metadata.drop_all + create_all)
  - Real Redis at redis://localhost:6380/9 (flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network — same as all existing suites)
  - FakeCompletionUpstream injected via app.state.completion_upstream for proxy scenarios
  - asyncio_mode = "auto" (set in pyproject.toml — no @pytest.mark.asyncio needed)
  - flush_once() called explicitly (deterministic; no background flusher timing needed)
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
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
ADMIN_TEAMS = "/admin/teams"
ADMIN_SPEND = "/admin/spend"
COMPLETIONS = "/v1/chat/completions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery staple",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id_str)."""
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


async def create_team(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
) -> dict[str, Any]:
    """POST /admin/teams; assert 201; return body."""
    resp = await client.post(ADMIN_TEAMS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_team failed ({resp.status_code}): {resp.text}"
    return resp.json()


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
    team_id: str | None = None,
) -> dict[str, Any]:
    """POST /admin/keys; assert 201; return body (body["key"] = plaintext key)."""
    payload: dict[str, Any] = {"name": name}
    if team_id is not None:
        payload["team_id"] = team_id
    resp = await client.post(ADMIN_KEYS, json=payload, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — mirrors the pattern from team-governance tests."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-ta-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-ta-s1","choices":[{"delta":{"content":"ok"}}]}\n\n'
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
async def fake_upstream(app: object) -> FakeCompletionUpstream:
    """Inject FakeCompletionUpstream into app.state; return for inspection."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    return upstream


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model + pricing snapshot so recorder computes non-zero cost."""
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    snap_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :mid, :p, :c, now())"
            " ON CONFLICT DO NOTHING"
        ),
        {
            "sid": str(snap_id),
            "mid": model_id,
            "p": "0.000001",
            "c": "0.000002",
        },
    )
    await db_session.commit()
    return model_id


# ===========================================================================
# S1 — Teamed key proxied request → ledger row carries team_id
# TRUE-RED REASON: UsageRecordRow ORM has no team_id column.
#   Base.metadata.create_all creates the table WITHOUT team_id.
#   After flush_once(), the row mapping has no "team_id" key.
#   row["team_id"] raises KeyError → test FAILS for the right reason.
# ===========================================================================


async def test_teamed_key_ledger_row_has_team_id(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """Proxied request with a teamed key → usage_records row has team_id set (SQL assert)."""
    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    # Wire recorder to test Redis (db 9)
    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    app.state.usage_recorder = recorder

    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="AttrCo1", email="owner1@attr.io"
    )
    team = await create_team(client, jwt, name="engineering")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="teamed-key", team_id=team_id)
    key = key_body["key"]

    # Make a proxied completion request
    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(key),
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"

    # Flush the stream event to the ledger
    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    # Assert: ledger row carries team_id (RED: column does not exist yet)
    rows = (
        await db_session.execute(
            text(
                "SELECT id, tenant_id, key_id, team_id"
                " FROM usage_records"
                " WHERE key_id = (SELECT id FROM api_keys WHERE name = 'teamed-key' LIMIT 1)"
            ),
        )
    ).fetchall()

    assert len(rows) == 1, f"expected 1 usage_record row, got {len(rows)}"
    row = rows[0]._mapping  # type: ignore[union-attr]

    # THIS IS THE RED ASSERTION: team_id column does not exist in ORM → KeyError
    assert row["team_id"] == uuid.UUID(team_id), (
        f"expected team_id={team_id!r}, got {row.get('team_id')!r}; "
        "team_id column not yet added to UsageRecordRow ORM — this is the RED reason"
    )


# ===========================================================================
# S2 — Un-teamed key → ledger row has team_id IS NULL
# TRUE-RED REASON: team_id column absent from ORM/table → KeyError on row["team_id"].
#   Once column is added, the second assertion (team_id IS NULL) would be the red target;
#   but right now the primary red reason is the missing column.
# ===========================================================================


async def test_unteamed_key_ledger_row_null_team_id(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """Proxied request with an un-teamed key → usage_records row has team_id IS NULL."""
    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    app.state.usage_recorder = recorder

    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="AttrCo2", email="owner2@attr.io"
    )

    # No team — solo key
    key_body = await create_key(client, jwt, name="solo-key")
    key = key_body["key"]

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "solo"}]},
        headers=auth_key(key),
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"

    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    rows = (
        await db_session.execute(
            text(
                "SELECT id, team_id FROM usage_records"
                " WHERE key_id = (SELECT id FROM api_keys WHERE name = 'solo-key' LIMIT 1)"
            ),
        )
    ).fetchall()

    assert len(rows) == 1, f"expected 1 usage_record row, got {len(rows)}"
    row = rows[0]._mapping  # type: ignore[union-attr]

    # RED ASSERTION: column absent → KeyError; or if column exists but is not NULL → AssertionError
    assert row["team_id"] is None, (
        f"expected team_id IS NULL for un-teamed key, got {row.get('team_id')!r}; "
        "team_id column not yet added to ORM — this is the RED reason (KeyError)"
    )


# ===========================================================================
# S3 — Old-format event (no team_id field) flushes with team_id NULL
# TRUE-RED REASON: column absent from table → INSERT from flusher does not include
#   team_id column → SELECT team_id raises ProgrammingError or KeyError on mapping.
#   This test injects a legacy event directly into the Redis Stream (no team_id field)
#   and asserts flush produces a row with team_id IS NULL.
# ===========================================================================


async def test_old_format_event_flushes_with_null_team_id(
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """Old-format stream event without team_id field → row lands with team_id IS NULL."""
    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.infrastructure.redis_stream import STREAM_KEY

    # Arrange: create a tenant so we have valid UUIDs for FK
    tenant_id = str(uuid.uuid4())
    key_id = str(uuid.uuid4())

    # We need a real tenant row for FK; insert directly
    # (Using a raw INSERT; the tenant must exist for the FK on usage_records.tenant_id)
    # We use a fresh UUID that won't collide — insert directly into tenants table
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name, markup_pct)"
            " VALUES (:id, :name, 0)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": tenant_id, "name": f"legacy-tenant-{tenant_id[:8]}"},
    )
    await db_session.commit()

    # Inject a legacy-shaped stream event — NO team_id field at all
    legacy_event_fields = {
        "tenant_id": tenant_id,
        "key_id": key_id,
        "model_id": "openai/gpt-4o-mini",
        "prompt_tokens": "5",
        "completion_tokens": "3",
        "cost_usd": "0.00001000",
        "pricing_snapshot_id": "",
        "status": "200",
        "raw": json.dumps({"tenant_id": tenant_id, "key_id": key_id, "model": "openai/gpt-4o-mini"}),
        "created_at": "2026-06-11T00:00:00+00:00",
        # DELIBERATELY NO "team_id" field — simulates a pre-deploy event
    }
    await redis_client.xadd(STREAM_KEY, legacy_event_fields)

    # Flush
    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    # Assert: row was inserted with team_id IS NULL; no crash
    rows = (
        await db_session.execute(
            text(
                "SELECT id, tenant_id, team_id FROM usage_records"
                " WHERE tenant_id = :tid"
            ),
            {"tid": tenant_id},
        )
    ).fetchall()

    assert len(rows) == 1, (
        f"expected 1 row after flushing legacy event; got {len(rows)} — "
        "flusher may have crashed or column not in INSERT list"
    )
    row = rows[0]._mapping  # type: ignore[union-attr]

    # RED ASSERTION: team_id column absent from ORM → KeyError or ProgrammingError
    assert row["team_id"] is None, (
        f"expected team_id IS NULL for legacy event without team_id field, "
        f"got {row.get('team_id')!r}; mixed-deploy safety failed — this is the RED reason"
    )


# ===========================================================================
# S4 — Ledger rollup shows ledger_cost_usd in GET /admin/spend breakdown
# TRUE-RED REASON: TeamSpendBreakdownItem has no ledger_cost_usd field.
#   GET /admin/spend?group_by=team_id returns breakdown items without this field.
#   Assertion that ledger_cost_usd is present and correct → AssertionError.
# ===========================================================================


async def test_ledger_rollup_shows_ledger_cost_usd(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /admin/spend?group_by=team_id breakdown items include ledger_cost_usd field."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="AttrCo4", email="owner4@attr.io"
    )

    team = await create_team(client, jwt, name="ledger-team")
    team_id = team["id"]

    teamed_key_body = await create_key(client, jwt, name="k-attr-team", team_id=team_id)
    teamed_key_id = teamed_key_body["key_id"]
    solo_key_body = await create_key(client, jwt, name="k-attr-solo")
    solo_key_id = solo_key_body["key_id"]

    # Insert model for FK validity
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )

    # Seed usage_records directly with team_id set (teamed row: cost 7.00)
    # RED: once team_id column is added, this INSERT will work; until then it fails silently
    # or raises. The test asserts ledger_cost_usd in the response — that's the RED anchor.
    teamed_row_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, team_id, created_at)"
            " VALUES (:id, :tid, :kid, :mid, 100, 50, 7.00, 200, :raw, :team_id, now())"
        ),
        {
            "id": teamed_row_id,
            "tid": tenant_id,
            "kid": teamed_key_id,
            "mid": model_id,
            "raw": "{}",
            "team_id": team_id,
        },
    )
    # Solo row with team_id IS NULL (cost 2.00)
    solo_row_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at)"
            " VALUES (:id, :tid, :kid, :mid, 20, 10, 2.00, 200, :raw, now())"
        ),
        {
            "id": solo_row_id,
            "tid": tenant_id,
            "kid": solo_key_id,
            "mid": model_id,
            "raw": "{}",
        },
    )
    await db_session.commit()

    resp = await client.get(
        f"{ADMIN_SPEND}?group_by=team_id&window=month",
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    breakdown: list[dict[str, Any]] | None = body.get("breakdown")
    assert breakdown is not None, (
        "breakdown must not be None when group_by=team_id; unexpected error"
    )

    # Find the team bucket
    team_bucket = next((b for b in breakdown if b.get("team_id") == team_id), None)
    assert team_bucket is not None, (
        f"No breakdown bucket for team_id={team_id}; buckets: {breakdown}"
    )

    # RED ASSERTION: ledger_cost_usd field absent from TeamSpendBreakdownItem schema
    assert "ledger_cost_usd" in team_bucket, (
        f"'ledger_cost_usd' absent from team breakdown item; keys: {list(team_bucket.keys())}; "
        "TeamSpendBreakdownItem does not have this field yet — this is the RED reason"
    )
    assert Decimal(str(team_bucket["ledger_cost_usd"])) == Decimal("7.00"), (
        f"expected ledger_cost_usd=7.00 for team bucket, got {team_bucket.get('ledger_cost_usd')!r}"
    )

    # NULL-team bucket
    null_bucket = next((b for b in breakdown if b.get("team_id") is None), None)
    assert null_bucket is not None, (
        f"No NULL-team bucket in breakdown; buckets: {breakdown}"
    )
    assert "ledger_cost_usd" in null_bucket, (
        f"'ledger_cost_usd' absent from NULL-team bucket; keys: {list(null_bucket.keys())}"
    )
    assert Decimal(str(null_bucket["ledger_cost_usd"])) == Decimal("2.00"), (
        f"expected ledger_cost_usd=2.00 for NULL-team bucket, got {null_bucket.get('ledger_cost_usd')!r}"
    )


# ===========================================================================
# S5 — Counter and ledger both visible; reconciliation observable
# TRUE-RED REASON: ledger_cost_usd absent from breakdown item → AssertionError.
#   The cost_usd field (counter/JOIN-derived) already exists and would be populated
#   by the existing group_by=team_id branch. The new field is the red anchor.
# ===========================================================================


async def test_counter_and_ledger_both_visible(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """After a completion + flush: breakdown item has both cost_usd and ledger_cost_usd."""
    import asyncio

    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,
    )
    app.state.usage_recorder = recorder

    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="AttrCo5", email="owner5@attr.io"
    )
    team = await create_team(client, jwt, name="reconcile-team")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="recon-key", team_id=team_id)
    key = key_body["key"]

    # Complete a request
    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "reconcile"}]},
        headers=auth_key(key),
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"

    # Allow recorder fire-and-forget to finish
    await asyncio.sleep(0.1)

    # Flush to ledger
    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    resp2 = await client.get(
        f"{ADMIN_SPEND}?group_by=team_id&window=month",
        headers=auth_jwt(jwt),
    )
    assert resp2.status_code == 200, f"spend query failed: {resp2.text}"
    body = resp2.json()

    breakdown: list[dict[str, Any]] | None = body.get("breakdown")
    assert breakdown is not None, "breakdown must not be None"

    team_bucket = next((b for b in breakdown if b.get("team_id") == team_id), None)
    assert team_bucket is not None, (
        f"No breakdown bucket for team_id={team_id}; buckets: {breakdown}"
    )

    # cost_usd (counter/JOIN-derived) must be present (already exists)
    assert "cost_usd" in team_bucket, "'cost_usd' absent from team breakdown item"
    assert Decimal(str(team_bucket["cost_usd"])) > Decimal("0"), (
        "cost_usd should be > 0 after completion"
    )

    # RED ASSERTION: ledger_cost_usd absent → AssertionError
    assert "ledger_cost_usd" in team_bucket, (
        f"'ledger_cost_usd' absent from team breakdown item; keys: {list(team_bucket.keys())}; "
        "ledger rollup field not yet implemented — this is the RED reason"
    )
    assert Decimal(str(team_bucket["ledger_cost_usd"])) > Decimal("0"), (
        "ledger_cost_usd should be > 0 after flush"
    )


# ===========================================================================
# S6 — Tenant-scoped: team in tenant B not visible to tenant A
# TRUE-RED REASON: ledger_cost_usd absent from breakdown item → AssertionError.
#   The tenant-scoping assertion on cost_usd would pass (existing group_by=team_id
#   already scopes by tenant_id), but the ledger_cost_usd assertion fires first.
# ===========================================================================


async def test_ledger_rollup_tenant_scoped(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Ledger rollup is tenant-scoped: tenant A cannot see tenant B's team ledger data."""
    jwt_a, tenant_id_a = await signup_and_login(
        client, tenant_name="AttrCoA6", email="owner-a6@attr.io"
    )
    jwt_b, tenant_id_b = await signup_and_login(
        client, tenant_name="AttrCoB6", email="owner-b6@attr.io"
    )

    # Tenant A: create team + key + usage row
    team_a = await create_team(client, jwt_a, name="team-alpha")
    team_a_id = team_a["id"]
    key_a_body = await create_key(client, jwt_a, name="k-a6", team_id=team_a_id)
    key_a_id = key_a_body["key_id"]

    # Tenant B: create team + key + usage row
    team_b = await create_team(client, jwt_b, name="team-beta")
    team_b_id = team_b["id"]
    key_b_body = await create_key(client, jwt_b, name="k-b6", team_id=team_b_id)
    key_b_id = key_b_body["key_id"]

    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )

    # Tenant A usage row with team_id set
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, team_id, created_at)"
            " VALUES (:id, :tid, :kid, :mid, 10, 5, 4.00, 200, :raw, :team_id, now())"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id_a,
            "kid": key_a_id,
            "mid": model_id,
            "raw": "{}",
            "team_id": team_a_id,
        },
    )
    # Tenant B usage row with team_id set
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, team_id, created_at)"
            " VALUES (:id, :tid, :kid, :mid, 10, 5, 9.00, 200, :raw, :team_id, now())"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id_b,
            "kid": key_b_id,
            "mid": model_id,
            "raw": "{}",
            "team_id": team_b_id,
        },
    )
    await db_session.commit()

    # Tenant A queries their rollup
    resp = await client.get(
        f"{ADMIN_SPEND}?group_by=team_id&window=month",
        headers=auth_jwt(jwt_a),
    )
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    breakdown: list[dict[str, Any]] | None = body.get("breakdown")
    assert breakdown is not None, "breakdown must not be None"

    # Tenant B's team must NOT appear in tenant A's breakdown
    tenant_b_leak = next((b for b in breakdown if b.get("team_id") == team_b_id), None)
    assert tenant_b_leak is None, (
        f"TENANT ISOLATION BREACH: tenant B team {team_b_id} appeared in tenant A's breakdown; "
        f"this is a data leak: {tenant_b_leak}"
    )

    # Tenant A's team must appear
    team_a_bucket = next((b for b in breakdown if b.get("team_id") == team_a_id), None)
    assert team_a_bucket is not None, (
        f"Tenant A team {team_a_id} not found in breakdown; buckets: {breakdown}"
    )

    # RED ASSERTION: ledger_cost_usd absent → AssertionError
    assert "ledger_cost_usd" in team_a_bucket, (
        f"'ledger_cost_usd' absent from team_a bucket; keys: {list(team_a_bucket.keys())}; "
        "ledger rollup field not yet implemented — this is the RED reason"
    )
    assert Decimal(str(team_a_bucket["ledger_cost_usd"])) == Decimal("4.00"), (
        f"expected tenant A ledger_cost_usd=4.00, got {team_a_bucket.get('ledger_cost_usd')!r}"
    )

    # Tenant B's data (9.00) must NOT appear in tenant A's ledger sum
    all_ledger_costs = [
        Decimal(str(b.get("ledger_cost_usd", "0")))
        for b in breakdown
        if b.get("ledger_cost_usd") is not None
    ]
    total_ledger = sum(all_ledger_costs, Decimal("0"))
    assert total_ledger < Decimal("9.00"), (
        f"tenant B's cost (9.00) appears to be leaking into tenant A's rollup; "
        f"total ledger cost visible to A = {total_ledger}"
    )


# ===========================================================================
# S7 — Migration column exists: usage_records has team_id column after create_all
# TRUE-RED REASON: UsageRecordRow ORM has no team_id column → create_all creates
#   table WITHOUT team_id → SELECT team_id FROM usage_records LIMIT 0 raises
#   sqlalchemy.exc.ProgrammingError: column "team_id" does not exist.
# ===========================================================================


async def test_team_id_column_exists(
    db_session: AsyncSession,
) -> None:
    """usage_records table has team_id column after ORM create_all (schema sanity check)."""
    # If the ORM column is missing, this SELECT raises ProgrammingError.
    # The test will fail with an exception rather than AssertionError — both are
    # right-reason failures (missing implementation: ORM column not added yet).
    try:
        result = await db_session.execute(
            text("SELECT team_id FROM usage_records LIMIT 0")
        )
        # If we get here, the column exists — this should only happen after Build
        columns = [col for col in (result.keys() if hasattr(result, "keys") else [])]
        # Just assert we didn't crash — the column exists
        assert True, f"column exists; result columns: {columns}"
    except Exception as exc:
        # Expected red failure: column "team_id" does not exist
        raise AssertionError(
            f"usage_records.team_id column does not exist: {exc!r}; "
            "UsageRecordRow ORM needs a team_id column — this is the RED reason"
        ) from exc
