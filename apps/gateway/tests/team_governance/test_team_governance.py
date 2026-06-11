"""Failing-first (RED) suite for team-governance (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason.

Right-reason red targets per test:

  PATCH /admin/teams/{id} endpoint does not exist → FastAPI returns 404 or 405
  routing error, so asserting 200/403/422 FAILS with AssertionError showing status mismatch.

  team_budget_usd field absent from GET /admin/teams + GET /admin/teams/{id} responses
  → KeyError or AssertionError because the field is not in TeamResponse/TeamDetailResponse yet.

  Enforcement: AuthzResult has no team_id/team_budget_usd fields yet; _check_team_budget
  does not exist in CompletionUseCase; teamed key 402 test fails because the proxy returns
  200 (upstream called successfully — no team budget gate blocks it).

  Counter increment: RecordingUsageRecorder does not INCRBYFLOAT the team counter yet;
  assertion that usage:spend:team:{team_id}:{YYYYMM} > 0 will FAIL (key remains 0 or absent).

  /admin/spend?group_by=team_id: the group_by whitelist only contains "key_id"; the
  team_id branch is not implemented; asserting breakdown with team_id/team_name fields
  FAILS (breakdown is None because group_by=team_id is not handled, or 422 is returned
  for the invalid group_by — both fail the target assertion).

  group_by=user_id: currently NO 422 guard for unknown group_by values; the endpoint
  silently ignores unknown group_by and returns 200 with breakdown=None; asserting 422 FAILS.

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys, /admin/teams, /admin/spend,
  /v1/chat/completions

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
    (schema rebuilt per test via conftest.py fixture — Base.metadata.drop_all + create_all)
  - Real Redis at redis://localhost:6380 (db index 9, flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network — same as all existing suites)
  - FakeCompletionUpstream injected via app.state.completion_upstream for proxy scenarios
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


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert RFC 9457 problem+json shape; return parsed body."""
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


# DISPOSITION (team-governance TASK.md §6, 2026-06-11): signup_and_login_member
# was removed with the S11 arrange correction — its duplicate-tenant-name signup
# produced a NEW tenant's OWNER, not a same-tenant member, and S11 was its only
# consumer. See test_patch_team_member_role_forbidden for the corrected arrange.


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
    monthly_budget_usd: str | None = None,
) -> dict[str, Any]:
    """POST /admin/keys; assert 201; return body (body["key"] = plaintext key)."""
    payload: dict[str, Any] = {"name": name}
    if team_id is not None:
        payload["team_id"] = team_id
    if monthly_budget_usd is not None:
        payload["monthly_budget_usd"] = monthly_budget_usd
    resp = await client.post(ADMIN_KEYS, json=payload, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


def _team_spend_key(team_id: str) -> str:
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:team:{team_id}:{yyyymm}"


def _key_spend_key(key_id: str) -> str:
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:key:{key_id}:{yyyymm}"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — mirrors the pattern from key-governance tests."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
        call_tracker: list[int] | None = None,
    ) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-tg-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        )
        self._tracker = call_tracker if call_tracker is not None else []

    @property
    def calls(self) -> int:
        return len(self._tracker)

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self._tracker.append(1)
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self._tracker.append(1)

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-tg-s1","choices":[{"delta":{"content":"ok"}}]}\n\n'
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
    """Inject a FakeCompletionUpstream into app.state; return it for call-count inspection."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    return upstream


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model + pricing snapshot so the recorder computes non-zero cost."""
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
# S1 — PATCH /admin/teams/{id} sets team_budget_usd
# TRUE-RED REASON: PATCH /admin/teams/{id} endpoint does not exist yet.
#   FastAPI returns 404 or 405. Asserting status == 200 FAILS.
# ===========================================================================


async def test_patch_team_sets_budget(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH /admin/teams/{id} with team_budget_usd → 200 + field echoed + persisted in DB."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo1", email="owner1@budg.io")
    team = await create_team(client, jwt, name="alpha")
    team_id = team["id"]

    resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "100.00"},
        headers=auth_jwt(jwt),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    # Response must include all team fields + new team_budget_usd
    assert body["id"] == team_id
    assert body["name"] == "alpha"
    assert "tenant_id" in body
    assert "created_at" in body
    assert "member_count" in body
    assert "key_count" in body
    assert body.get("team_budget_usd") == "100.00", (
        f"expected team_budget_usd='100.00', got {body.get('team_budget_usd')!r}"
    )

    # Verify DB persistence
    row = (
        await db_session.execute(
            text("SELECT team_budget_usd FROM teams WHERE id = :id"),
            {"id": team_id},
        )
    ).fetchone()
    assert row is not None, "teams row not found"
    assert Decimal(str(row[0])) == Decimal("100.00"), (
        f"DB team_budget_usd expected 100.00, got {row[0]}"
    )


# ===========================================================================
# S2 — PATCH /admin/teams/{id} clears team_budget_usd (null)
# TRUE-RED REASON: same as S1 — PATCH endpoint does not exist yet → 404/405.
# ===========================================================================


async def test_patch_team_clears_budget(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH team_budget_usd=null clears the budget → 200 + null + DB NULL."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo2", email="owner2@budg.io")
    team = await create_team(client, jwt, name="beta")
    team_id = team["id"]

    # First set a budget
    set_resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "50.00"},
        headers=auth_jwt(jwt),
    )
    assert set_resp.status_code == 200, f"set budget failed: {set_resp.text}"

    # Now clear it
    clear_resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": None},
        headers=auth_jwt(jwt),
    )
    assert clear_resp.status_code == 200, (
        f"expected 200 on clear, got {clear_resp.status_code}: {clear_resp.text}"
    )
    body = clear_resp.json()
    assert body.get("team_budget_usd") is None, (
        f"expected null, got {body.get('team_budget_usd')!r}"
    )

    # Verify DB IS NULL
    row = (
        await db_session.execute(
            text("SELECT team_budget_usd FROM teams WHERE id = :id"),
            {"id": team_id},
        )
    ).fetchone()
    assert row is not None
    assert row[0] is None, f"expected DB NULL, got {row[0]}"


# ===========================================================================
# S3 — GET /admin/teams includes team_budget_usd
# TRUE-RED REASON: TeamResponse schema does not have team_budget_usd field yet.
#   The field is absent from the JSON response → assertion fails with KeyError or
#   body.get("team_budget_usd") returns None even after PATCH sets it (field never serialized).
# ===========================================================================


async def test_get_teams_includes_team_budget_usd(
    client: httpx.AsyncClient,
) -> None:
    """GET /admin/teams — list items include team_budget_usd field."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo3", email="owner3@budg.io")
    team = await create_team(client, jwt, name="gamma")
    team_id = team["id"]

    # Set budget via PATCH (this itself may fail red; but the GET assertion is what we track)
    patch_resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "75.00"},
        headers=auth_jwt(jwt),
    )
    # We proceed even if patch fails (PATCH is separately red in S1) — the GET assertion
    # is the target behavior for THIS test.
    assert patch_resp.status_code == 200, (
        f"pre-condition PATCH failed: {patch_resp.status_code} {patch_resp.text}"
    )

    list_resp = await client.get(ADMIN_TEAMS, headers=auth_jwt(jwt))
    assert list_resp.status_code == 200, f"GET /admin/teams failed: {list_resp.text}"
    items: list[dict[str, Any]] = list_resp.json()
    assert len(items) >= 1
    match = next((i for i in items if i["id"] == team_id), None)
    assert match is not None, f"team {team_id} not found in list"
    # team_budget_usd must be present in the response item (not absent from schema)
    assert "team_budget_usd" in match, (
        f"'team_budget_usd' absent from GET /admin/teams item; keys: {list(match.keys())}"
    )
    assert match["team_budget_usd"] == "75.00", (
        f"expected '75.00', got {match['team_budget_usd']!r}"
    )


# ===========================================================================
# S4 — GET /admin/teams/{id} includes team_budget_usd
# TRUE-RED REASON: TeamDetailResponse schema does not have team_budget_usd yet.
# ===========================================================================


async def test_get_team_detail_includes_team_budget_usd(
    client: httpx.AsyncClient,
) -> None:
    """GET /admin/teams/{id} — detail response includes team_budget_usd."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo4", email="owner4@budg.io")
    team = await create_team(client, jwt, name="delta")
    team_id = team["id"]

    patch_resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "200.00"},
        headers=auth_jwt(jwt),
    )
    assert patch_resp.status_code == 200, (
        f"pre-condition PATCH failed: {patch_resp.status_code} {patch_resp.text}"
    )

    detail_resp = await client.get(f"{ADMIN_TEAMS}/{team_id}", headers=auth_jwt(jwt))
    assert detail_resp.status_code == 200, f"GET detail failed: {detail_resp.text}"
    body = detail_resp.json()
    assert "team_budget_usd" in body, (
        f"'team_budget_usd' absent from GET detail; keys: {list(body.keys())}"
    )
    assert body["team_budget_usd"] == "200.00", (
        f"expected '200.00', got {body['team_budget_usd']!r}"
    )


# ===========================================================================
# S5 — Teamed key 402 (team cap full); un-teamed sibling 200
# TRUE-RED REASON: AuthzResult has no team_id/team_budget_usd fields; _check_team_budget
#   does not exist in _enforce_governance; teamed key receives 200 (proxy passes through
#   without team enforcement) → asserting 402 FAILS.
# ===========================================================================


async def test_teamed_key_402_unteamed_sibling_200(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """Teamed key 402 ERR_BUDGET_EXCEEDED when team counter >= cap; un-teamed sibling 200."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo5", email="owner5@budg.io")

    # Create team with budget cap
    team = await create_team(client, jwt, name="capped-team")
    team_id = team["id"]
    patch_resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "10.00"},
        headers=auth_jwt(jwt),
    )
    assert patch_resp.status_code == 200, f"PATCH budget failed: {patch_resp.text}"

    # Create teamed key (no per-key budget)
    teamed_key_body = await create_key(client, jwt, name="teamed-key", team_id=team_id)
    teamed_key = teamed_key_body["key"]

    # Create un-teamed key
    solo_key_body = await create_key(client, jwt, name="solo-key")
    solo_key = solo_key_body["key"]

    # Seed the team Redis counter to cap (>= budget)
    team_counter_key = _team_spend_key(team_id)
    await redis_client.set(team_counter_key, "10.00")

    # Act: teamed key must 402
    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}

    teamed_resp = await client.post(
        COMPLETIONS, json=payload, headers=auth_key(teamed_key)
    )
    assert_problem(teamed_resp, 402, "ERR_BUDGET_EXCEEDED")
    body = teamed_resp.json()
    detail = body.get("detail", "") or body.get("title", "")
    assert "team" in detail.lower() or "Team" in detail, (
        f"Expected detail to mention team scope; got: {detail!r}"
    )

    # Act: un-teamed key must 200
    solo_resp = await client.post(
        COMPLETIONS, json=payload, headers=auth_key(solo_key)
    )
    assert solo_resp.status_code == 200, (
        f"un-teamed sibling expected 200, got {solo_resp.status_code}: {solo_resp.text}"
    )


# ===========================================================================
# S6 — Per-key hard budget fires BEFORE team budget
# TRUE-RED REASON: same as S5 — _check_team_budget not implemented; but this test
#   would also fail EVEN IF team check were added because the per-key check must fire
#   first. Currently the key-budget check IS implemented (v3), so the 402 fires for the
#   right reason — BUT the detail must mention "key" scope, not "team" scope. Since
#   team_budget_usd is not in AuthzResult yet, the per-key path fires correctly but
#   the test is still red because it depends on the PATCH pre-condition (S1 still red).
#   Primary failure: PATCH team endpoint 404/405 → pre-condition assert fails → test RED.
# ===========================================================================


async def test_per_key_hard_budget_fires_before_team(
    client: httpx.AsyncClient,
    redis_client: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """Per-key hard budget fires BEFORE team budget (key is tighter cap)."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo6", email="owner6@budg.io")

    team = await create_team(client, jwt, name="wide-team")
    team_id = team["id"]
    patch_resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "100.00"},
        headers=auth_jwt(jwt),
    )
    assert patch_resp.status_code == 200, f"PATCH team budget failed: {patch_resp.text}"

    # Key with tighter per-key budget
    key_body = await create_key(
        client, jwt, name="tight-key", team_id=team_id, monthly_budget_usd="5.00"
    )
    key = key_body["key"]
    key_id = key_body["key_id"]

    # Seed key counter at cap; team counter empty
    await redis_client.set(_key_spend_key(key_id), "5.00")
    await redis_client.set(_team_spend_key(team_id), "0.00")

    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}
    resp = await client.post(COMPLETIONS, json=payload, headers=auth_key(key))

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    body = resp.json()
    detail = body.get("detail", "") or ""
    # Detail must mention per-key scope (key id or "key" word), not team
    assert "key" in detail.lower() or str(key_id) in detail, (
        f"Expected key-scope detail, got: {detail!r}"
    )
    # Upstream must NOT have been called
    assert fake_upstream.calls == 0, (
        f"upstream should NOT be called; got {fake_upstream.calls} calls"
    )


# ===========================================================================
# S7 — Team budget does not affect keys of OTHER teams
# TRUE-RED REASON: _check_team_budget not implemented → T1-capped key K2 in T2
#   returns 200 (correct outcome) but the PATCH pre-condition for T1 is red (404/405).
#   → pre-condition assert fails → test RED.
# ===========================================================================


async def test_team_budget_does_not_affect_other_teams(
    client: httpx.AsyncClient,
    redis_client: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """T1 cap full does not block K2 (key in T2 with empty cap)."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo7", email="owner7@budg.io")

    team1 = await create_team(client, jwt, name="team-one")
    team2 = await create_team(client, jwt, name="team-two")
    team1_id = team1["id"]
    team2_id = team2["id"]

    # Set budgets on both teams
    for tid in (team1_id, team2_id):
        pr = await client.patch(
            f"{ADMIN_TEAMS}/{tid}",
            json={"team_budget_usd": "10.00"},
            headers=auth_jwt(jwt),
        )
        assert pr.status_code == 200, f"PATCH team {tid} failed: {pr.text}"

    key1_body = await create_key(client, jwt, name="k1", team_id=team1_id)
    key2_body = await create_key(client, jwt, name="k2", team_id=team2_id)
    key2 = key2_body["key"]

    # Seed T1 at cap; T2 empty
    await redis_client.set(_team_spend_key(team1_id), "10.00")
    await redis_client.set(_team_spend_key(team2_id), "0.00")

    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}
    resp = await client.post(COMPLETIONS, json=payload, headers=auth_key(key2))
    assert resp.status_code == 200, (
        f"K2 (team2) should proceed; got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# S8 — Team budget cross-tenant isolation
# TRUE-RED REASON: PATCH pre-condition is red (endpoint absent); assert would fail there.
#   If PATCH were somehow available, the actual enforcement is also red (team_id not in
#   AuthzResult); K_B returns 200 for the right reason in either case — but PATCH red
#   causes the test to fail before the completion assertion.
# ===========================================================================


async def test_team_budget_cross_tenant_isolation(
    client: httpx.AsyncClient,
    redis_client: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """Tenant B un-teamed key proceeds even when tenant A team cap is full."""
    jwt_a, _ = await signup_and_login(
        client, tenant_name="TenantA-Iso", email="owner-a@iso.io"
    )
    jwt_b, _ = await signup_and_login(
        client, tenant_name="TenantB-Iso", email="owner-b@iso.io"
    )

    # Tenant A: create team with cap, seed counter
    team_a = await create_team(client, jwt_a, name="a-team")
    team_a_id = team_a["id"]
    pr = await client.patch(
        f"{ADMIN_TEAMS}/{team_a_id}",
        json={"team_budget_usd": "10.00"},
        headers=auth_jwt(jwt_a),
    )
    assert pr.status_code == 200, f"PATCH tenant A team failed: {pr.text}"
    await redis_client.set(_team_spend_key(team_a_id), "10.00")

    # Tenant B: un-teamed key
    key_b_body = await create_key(client, jwt_b, name="b-key")
    key_b = key_b_body["key"]

    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}
    resp = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_b))
    assert resp.status_code == 200, (
        f"Tenant B key should proceed; got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# S9 — Successful completion increments BOTH key and team counters
# TRUE-RED REASON: RecordingUsageRecorder does not INCRBYFLOAT the team counter yet.
#   After a successful completion: usage:spend:key:{key_id}:{YYYYMM} > 0 (this already
#   works — v3), BUT usage:spend:team:{team_id}:{YYYYMM} == 0 or absent → assertion
#   that team counter > 0 FAILS for the right reason.
# ===========================================================================


async def test_completion_increments_both_counters(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    app: object,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    """Successful completion: both key and team Redis counters are incremented.

    Arrange: wire app.state.usage_recorder to the test Redis (db 9) so we can
    observe the counters it writes. The recorder writes to the Redis it is
    constructed with; we swap it to redis_client (db 9) for this test.
    """
    import asyncio

    from gateway.usage.application.recorder import RecordingUsageRecorder

    # Wire recorder to test Redis (db 9) so counter reads are on the same instance
    test_recorder = RecordingUsageRecorder(
        redis=redis_client,
        session_factory=app.state.sessionmaker,  # type: ignore[attr-defined]
    )
    app.state.usage_recorder = test_recorder  # type: ignore[attr-defined]

    jwt, _ = await signup_and_login(client, tenant_name="BudgCo9", email="owner9@budg.io")

    team = await create_team(client, jwt, name="counted-team")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="counted-key", team_id=team_id)
    key = key_body["key"]
    key_id = key_body["key_id"]

    # Flush counters to ensure clean state
    await redis_client.delete(_key_spend_key(key_id))
    await redis_client.delete(_team_spend_key(team_id))

    payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": "count me"}],
    }
    resp = await client.post(COMPLETIONS, json=payload, headers=auth_key(key))
    assert resp.status_code == 200, f"completion failed: {resp.text}"

    # Allow fire-and-forget recorder coroutine to complete
    await asyncio.sleep(0.2)

    # Per-key counter must be > 0 (already works in v3 — baseline check)
    raw_key = await redis_client.get(_key_spend_key(key_id))
    key_spend = Decimal(raw_key.decode() if raw_key else "0")
    assert key_spend > Decimal("0"), (
        f"key counter expected > 0, got {key_spend!r} (key={key_id}); "
        "this is the baseline v3 behavior — recorder writes key counter"
    )

    # Team counter MUST also be > 0 — THIS IS THE RED ASSERTION
    # recorder does not yet INCRBYFLOAT usage:spend:team:{team_id}:{YYYYMM}
    raw_team = await redis_client.get(_team_spend_key(team_id))
    team_spend = Decimal(raw_team.decode() if raw_team else "0")
    assert team_spend > Decimal("0"), (
        f"team counter expected > 0, got {team_spend!r} (team={team_id}); "
        "recorder does not increment team counter yet — this is the RED reason"
    )

    # Both counters must equal the same cost_usd
    assert key_spend == team_spend, (
        f"key counter ({key_spend}) != team counter ({team_spend}); "
        "they should both equal the same cost_usd"
    )


# ===========================================================================
# S10 — GET /admin/spend?group_by=team_id rollup + NULL bucket
# TRUE-RED REASON: group_by=team_id is not in the current whitelist (only "key_id" is
#   whitelisted). The current code silently ignores unknown group_by and returns
#   breakdown=null, OR may return 422 for the unrecognised value. In either case,
#   asserting breakdown contains team-shaped items FAILS — breakdown is None or absent.
# ===========================================================================


async def test_spend_group_by_team_id_rollup(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /admin/spend?group_by=team_id returns team breakdown + NULL bucket."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="SpendCo10", email="owner10@spend.io"
    )

    # Arrange: team + teamed key + solo key
    team = await create_team(client, jwt, name="platform")
    team_id = team["id"]

    key_team_body = await create_key(client, jwt, name="k-team", team_id=team_id)
    key_team_id = key_team_body["key_id"]
    key_solo_body = await create_key(client, jwt, name="k-solo")
    key_solo_id = key_solo_body["key_id"]

    # Seed usage_records directly (bypasses the full completion path for speed)
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    # 1 row for teamed key: cost 5.00
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at)"
            " VALUES (:id, :tid, :kid, :mid, 100, 50, 5.00, 200, :raw, now())"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id,
            "kid": key_team_id,
            "mid": model_id,
            "raw": "{}",
        },
    )
    # 1 row for solo key: cost 3.00
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at)"
            " VALUES (:id, :tid, :kid, :mid, 60, 30, 3.00, 200, :raw, now())"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id,
            "kid": key_solo_id,
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
        "breakdown must not be None when group_by=team_id; "
        "group_by=team_id is not yet implemented — this is the RED reason"
    )

    # Breakdown must have team-shaped items with team_id and team_name
    for item in breakdown:
        assert "team_id" in item, (
            f"breakdown item missing 'team_id'; keys: {list(item.keys())}"
        )
        assert "team_name" in item, (
            f"breakdown item missing 'team_name'; keys: {list(item.keys())}"
        )

    # Find the team bucket
    team_bucket = next(
        (b for b in breakdown if b.get("team_id") == team_id), None
    )
    assert team_bucket is not None, (
        f"No breakdown bucket for team_id={team_id}; buckets: {breakdown}"
    )
    assert team_bucket["team_name"] == "platform", (
        f"expected team_name='platform', got {team_bucket.get('team_name')!r}"
    )
    assert int(team_bucket["requests"]) == 1
    assert Decimal(str(team_bucket["cost_usd"])) == Decimal("5.00")

    # Find the NULL-team bucket
    null_bucket = next(
        (b for b in breakdown if b.get("team_id") is None), None
    )
    assert null_bucket is not None, (
        f"No NULL-team bucket in breakdown; buckets: {breakdown}"
    )
    assert null_bucket.get("team_name") is None
    assert int(null_bucket["requests"]) == 1
    assert Decimal(str(null_bucket["cost_usd"])) == Decimal("3.00")

    # Totals must reconcile
    totals = body.get("totals", {})
    total_cost = Decimal(str(totals.get("cost_usd", "0")))
    assert total_cost == Decimal("8.00"), (
        f"totals.cost_usd expected 8.00, got {total_cost}"
    )


# ===========================================================================
# S11 — Member role forbidden on PATCH /admin/teams/{id}
# TRUE-RED REASON: PATCH endpoint does not exist → FastAPI 404/405, not 403.
#   Asserting 403 FAILS. (Even if endpoint existed, member-role logic would need
#   implementation — but the primary failure is the missing route.)
# ===========================================================================


async def test_patch_team_member_role_forbidden(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    """PATCH /admin/teams/{id} with member JWT → 403 ERR_AUTH_FORBIDDEN."""
    # DISPOSITION (team-governance TASK.md §6, 2026-06-11): the original arrange
    # used signup_and_login_member, whose second signup creates a NEW tenant whose
    # user is OWNER — making the asserted same-tenant 403 unreachable and
    # contradicting the frozen teams-core cross-tenant-404 contract. Corrected to
    # a true same-tenant member via direct insert + token_service.issue (the
    # model-mgmt suite pattern). Assertion target (403 ERR_AUTH_FORBIDDEN for a
    # same-tenant member) is unchanged.
    owner_jwt, tenant_id = await signup_and_login(
        client, tenant_name="BudgCo11", email="owner11@budg.io"
    )

    team = await create_team(client, owner_jwt, name="restricted-team")
    team_id = team["id"]

    # Create a same-tenant member user directly; mint a member JWT
    member_user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', 'member')"
        ),
        {"id": member_user_id, "tid": tenant_id, "email": "member11@budg.io"},
    )
    await db_session.commit()

    from gateway.tenants.domain.entities import Role

    member_jwt, _ = app.state.token_service.issue(
        user_id=uuid.UUID(member_user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.MEMBER,
        email="member11@budg.io",
    )

    resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "50.00"},
        headers=auth_jwt(member_jwt),
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    # Verify DB unchanged
    row = (
        await db_session.execute(
            text("SELECT team_budget_usd FROM teams WHERE id = :id"),
            {"id": team_id},
        )
    ).fetchone()
    assert row is not None
    assert row[0] is None, f"team_budget_usd should remain NULL; got {row[0]}"


# ===========================================================================
# S12 — Invalid group_by returns 422
# TRUE-RED REASON: the current get_spend implementation does NOT validate group_by
#   against a whitelist. group_by="user_id" is silently ignored and 200 is returned
#   with breakdown=null. Asserting 422 ERR_PAYLOAD_INVALID FAILS.
# ===========================================================================


async def test_spend_invalid_group_by(
    client: httpx.AsyncClient,
) -> None:
    """GET /admin/spend?group_by=user_id → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(client, tenant_name="BudgCo12", email="owner12@budg.io")

    resp = await client.get(
        f"{ADMIN_SPEND}?group_by=user_id",
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# S13 — Cross-tenant team PATCH returns 404
# TRUE-RED REASON: PATCH endpoint does not exist → FastAPI 404/405.
#   This test would naturally pass the 404 assertion (FastAPI 404 for missing route
#   matches the expected 404), BUT the route-not-found error body is not a problem+json
#   with code "ERR_TEAM_NOT_FOUND" — it's FastAPI's default {"detail": "Not Found"}.
#   The assert_problem helper checks body.get("code") == "ERR_TEAM_NOT_FOUND" → FAILS.
# ===========================================================================


async def test_patch_team_cross_tenant_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tenant A cannot PATCH tenant B's team → 404 ERR_TEAM_NOT_FOUND (no leak)."""
    jwt_a, _ = await signup_and_login(
        client, tenant_name="CrossA-13", email="a13@cross.io"
    )
    jwt_b, _ = await signup_and_login(
        client, tenant_name="CrossB-13", email="b13@cross.io"
    )

    team_b = await create_team(client, jwt_b, name="b-private")
    team_b_id = team_b["id"]

    resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_b_id}",
        json={"team_budget_usd": "50.00"},
        headers=auth_jwt(jwt_a),
    )
    assert_problem(resp, 404, "ERR_TEAM_NOT_FOUND")

    # Verify team B's budget unchanged
    row = (
        await db_session.execute(
            text("SELECT team_budget_usd FROM teams WHERE id = :id"),
            {"id": team_b_id},
        )
    ).fetchone()
    assert row is not None
    assert row[0] is None, f"team_budget_usd should remain NULL; got {row[0]}"


# ===========================================================================
# S14 — Negative team_budget_usd rejected (422)
# TRUE-RED REASON: PATCH endpoint does not exist → FastAPI 404/405.
#   Asserting 422 FAILS (got 404 or 405, not 422).
# ===========================================================================


async def test_patch_team_negative_budget_422(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH with negative team_budget_usd → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(
        client, tenant_name="BudgCo14", email="owner14@budg.io"
    )
    team = await create_team(client, jwt, name="neg-team")
    team_id = team["id"]

    resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "-5.00"},
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")

    # DB must be unchanged
    row = (
        await db_session.execute(
            text("SELECT team_budget_usd FROM teams WHERE id = :id"),
            {"id": team_id},
        )
    ).fetchone()
    assert row is not None
    assert row[0] is None, f"team_budget_usd should remain NULL; got {row[0]}"
