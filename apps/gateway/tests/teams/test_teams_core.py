"""Failing-first (RED) suite for teams-core (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.

TRUE-RED rule: every test asserts TARGET behavior and fails NOW for the RIGHT reason.
Expected failure mode for all tests: the /admin/teams router does not exist yet, so
/admin/teams calls return 404/405 routing errors (no route registered). The key
attribution tests (team_id field) fail because the field is absent from
schemas/responses (KeyError or AssertionError on the missing field). Tests that
assert DB state on the teams/team_members tables fail with ProgrammingError
("relation teams does not exist") since the migration has not been run.

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys (PATCH/DELETE included),
  /internal/authz, /v1/chat/completions, /admin/teams (the new surface under test)

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 (db index 9, flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network — same as existing suites)
  - FakeCompletionUpstream injected via app.state.completion_upstream for proxy scenarios
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
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
INTERNAL_AUTHZ = "/internal/authz"
COMPLETIONS = "/v1/chat/completions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert RFC 9457 problem+json shape and return the parsed body."""
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


async def create_team(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
) -> dict[str, Any]:
    """POST /admin/teams and assert 201; return response body."""
    resp = await client.post(ADMIN_TEAMS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_team failed ({resp.status_code}): {resp.text}"
    return resp.json()


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str = "k",
    team_id: str | None = None,
) -> dict[str, Any]:
    """POST /admin/keys; return response body."""
    payload: dict[str, Any] = {"name": name}
    if team_id is not None:
        payload["team_id"] = team_id
    resp = await client.post(ADMIN_KEYS, json=payload, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


def make_member_jwt(owner_token: str, tenant_id: str) -> str:
    """Forge a member-role JWT from the owner token's claims using the test secret."""
    import jwt as pyjwt  # noqa: PLC0415

    from tests.conftest import TEST_JWT_SECRET  # noqa: PLC0415

    owner_claims = pyjwt.decode(
        owner_token,
        TEST_JWT_SECRET,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    member_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "email": f"member-{uuid.uuid4().hex[:8]}@test.io",
        "role": "member",
        "iss": owner_claims.get("iss", "ai-proxy"),
        "iat": owner_claims["iat"],
        "exp": owner_claims["exp"],
    }
    return pyjwt.encode(member_claims, TEST_JWT_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — reused from key-governance / proxy pattern."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-teams-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
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
            yield b'data: {"id":"gen-t1","choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model row for proxy completion tests."""
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


# ===========================================================================
# S1 — create team
# ===========================================================================


async def test_create_team(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/teams → 201 + team object shape + DB row with correct tenant_id.

    Right-reason red: /admin/teams route does not exist → 404 routing error before build.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo1", email="owner1@teams.io"
    )

    resp = await client.post(
        ADMIN_TEAMS, json={"name": "platform"}, headers=auth_jwt(jwt)
    )

    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "id" in body, f"team id missing: {body}"
    assert body["name"] == "platform"
    assert body["tenant_id"] == tenant_id
    assert "created_at" in body
    assert body["member_count"] == 0
    assert body["key_count"] == 0

    # DB row must exist with correct tenant_id
    row = (
        await db_session.execute(
            text("SELECT tenant_id, name FROM teams WHERE id = :id"),
            {"id": body["id"]},
        )
    ).one()
    assert str(row[0]) == tenant_id
    assert row[1] == "platform"


# ===========================================================================
# S2 — duplicate team name within tenant rejected
# ===========================================================================


async def test_create_team_duplicate_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Duplicate name in same tenant → 409 ERR_TEAM_EXISTS; no extra row.

    Right-reason red: /admin/teams route does not exist → 404 routing error before build.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo2", email="owner2@teams.io"
    )
    await create_team(client, jwt, name="platform")

    count_before = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM teams WHERE tenant_id = :tid AND name = 'platform'"),
            {"tid": tenant_id},
        )
    ).scalar()

    resp = await client.post(
        ADMIN_TEAMS, json={"name": "platform"}, headers=auth_jwt(jwt)
    )

    assert_problem(resp, 409, "ERR_TEAM_EXISTS")

    count_after = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM teams WHERE tenant_id = :tid AND name = 'platform'"),
            {"tid": tenant_id},
        )
    ).scalar()
    assert count_after == count_before == 1, "exactly one team row must remain"


# ===========================================================================
# S3 — list teams with aggregates
# ===========================================================================


async def test_list_teams_with_aggregates(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /admin/teams → list with member_count and key_count aggregates.

    Right-reason red: /admin/teams route does not exist → 404 routing error before build.
    Arrange: team + 2 members + 1 attributed key via canonical routes.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo3", email="owner3@teams.io"
    )
    team = await create_team(client, jwt, name="alpha")
    team_id = team["id"]

    # Add owner as member (owner is a user in the tenant)
    owner_user_id = (
        await db_session.execute(
            text("SELECT id FROM users WHERE email = 'owner3@teams.io'")
        )
    ).scalar_one()

    # Arrange a second user in the same tenant directly in DB (no signup route for adding users)
    second_user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', 'member')"
        ),
        {"id": second_user_id, "tid": tenant_id, "email": "user2-tco3@teams.io"},
    )
    await db_session.commit()

    # Add 2 members via the teams API
    resp1 = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": str(owner_user_id), "role": "lead"},
        headers=auth_jwt(jwt),
    )
    assert resp1.status_code == 201, f"member add 1 failed: {resp1.text}"
    resp2 = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": second_user_id, "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert resp2.status_code == 201, f"member add 2 failed: {resp2.text}"

    # Attribute 1 key to the team
    await create_key(client, jwt, name="attributed-key", team_id=team_id)

    # Act
    list_resp = await client.get(ADMIN_TEAMS, headers=auth_jwt(jwt))
    assert list_resp.status_code == 200, f"list teams failed: {list_resp.text}"
    items: list[dict[str, Any]] = list_resp.json()

    target = next((i for i in items if i["id"] == team_id), None)
    assert target is not None, f"team {team_id} missing from list: {items}"
    assert target["member_count"] == 2, (
        f"expected member_count=2, got {target['member_count']}"
    )
    assert target["key_count"] == 1, (
        f"expected key_count=1, got {target['key_count']}"
    )


# ===========================================================================
# S4 — get team with members
# ===========================================================================


async def test_get_team_with_members(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /admin/teams/{team_id} → team object + members list.

    Right-reason red: /admin/teams/{id} route does not exist → 404 routing error.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo4", email="owner4@teams.io"
    )
    team = await create_team(client, jwt, name="alpha")
    team_id = team["id"]

    owner_user_id = str(
        (
            await db_session.execute(
                text("SELECT id FROM users WHERE email = 'owner4@teams.io'")
            )
        ).scalar_one()
    )

    add_resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": owner_user_id, "role": "lead"},
        headers=auth_jwt(jwt),
    )
    assert add_resp.status_code == 201, add_resp.text

    get_resp = await client.get(f"{ADMIN_TEAMS}/{team_id}", headers=auth_jwt(jwt))
    assert get_resp.status_code == 200, f"get team failed: {get_resp.text}"
    body = get_resp.json()

    assert body["id"] == team_id
    assert body["name"] == "alpha"
    assert "members" in body, f"members list missing from response: {body}"
    assert isinstance(body["members"], list)
    assert len(body["members"]) == 1, f"expected 1 member, got {len(body['members'])}"
    m = body["members"][0]
    assert m["user_id"] == owner_user_id
    assert m["role"] == "lead"
    assert "added_at" in m


# ===========================================================================
# S5 — delete team cascades members, nulls key attribution
# ===========================================================================


async def test_delete_team_cascades_and_nulls_keys(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """DELETE /admin/teams/{id} → 204; member row gone; key.team_id NULL; key still authzs.

    Right-reason red: /admin/teams/{id} DELETE route does not exist → 404/405 routing error.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo5", email="owner5@teams.io"
    )
    team = await create_team(client, jwt, name="doomed")
    team_id = team["id"]

    owner_user_id = str(
        (
            await db_session.execute(
                text("SELECT id FROM users WHERE email = 'owner5@teams.io'")
            )
        ).scalar_one()
    )
    add_resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": owner_user_id, "role": "lead"},
        headers=auth_jwt(jwt),
    )
    assert add_resp.status_code == 201, add_resp.text

    key_body = await create_key(client, jwt, name="to-null", team_id=team_id)
    key_id = key_body["key_id"]
    plaintext_key = key_body["key"]

    # Act
    del_resp = await client.delete(f"{ADMIN_TEAMS}/{team_id}", headers=auth_jwt(jwt))
    assert del_resp.status_code == 204, f"expected 204, got {del_resp.status_code}: {del_resp.text}"

    # team_members row must be gone
    member_count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_id},
        )
    ).scalar()
    assert member_count == 0, f"team_members must be empty after team delete; got {member_count}"

    # api_keys.team_id must be NULL
    key_team_id = (
        await db_session.execute(
            text("SELECT team_id FROM api_keys WHERE id = :id"),
            {"id": key_id},
        )
    ).scalar()
    assert key_team_id is None, f"key team_id must be NULL after team delete; got {key_team_id}"

    # Key must still be active (authz passes)
    authz_resp = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": plaintext_key})
    assert authz_resp.status_code == 200, (
        f"key must remain active after team deletion: {authz_resp.text}"
    )


# ===========================================================================
# S6 — key completes successfully after team deletion
# ===========================================================================


async def test_key_completion_after_team_deletion(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
) -> None:
    """Delete team then proxy completion with attributed key → 200.

    Right-reason red: /admin/teams DELETE route missing → arrange step fails first;
    if route existed, team_id column on api_keys does not exist → DB error.
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="TCo6", email="owner6@teams.io"
    )
    team = await create_team(client, jwt, name="ephemeral")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="survivor", team_id=team_id)
    plaintext_key = key_body["key"]

    # Delete the team
    del_resp = await client.delete(f"{ADMIN_TEAMS}/{team_id}", headers=auth_jwt(jwt))
    assert del_resp.status_code == 204, del_resp.text

    # Wire fake upstream + bypass budget guard
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    from gateway.budgets.domain.ports import PassthroughBudgetGuard  # noqa: PLC0415

    app.state.budget_guard = PassthroughBudgetGuard()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(plaintext_key),
    )

    assert resp.status_code == 200, (
        f"completion must succeed after team deletion; got {resp.status_code}: {resp.text}"
    )
    assert upstream.calls == 1


# ===========================================================================
# S7 — member role forbidden on team create
# ===========================================================================


async def test_member_role_forbidden(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Member JWT → 403 ERR_AUTH_FORBIDDEN on POST /admin/teams; no row created.

    Right-reason red: /admin/teams route does not exist → 404 routing error. Once the
    route exists the member-role dep will enforce 403.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo7", email="owner7@teams.io"
    )
    member_jwt = make_member_jwt(jwt, tenant_id)

    count_before = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM teams WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
    ).scalar()

    resp = await client.post(
        ADMIN_TEAMS, json={"name": "x"}, headers=auth_jwt(member_jwt)
    )

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    count_after = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM teams WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
    ).scalar()
    assert count_after == count_before, "no teams row must be created for a member"


# ===========================================================================
# S8 — cross-tenant team GET returns 404, not 403
# ===========================================================================


async def test_cross_tenant_team_returns_404(
    client: httpx.AsyncClient,
) -> None:
    """Tenant A's JWT calling GET on tenant B's team → 404 (no existence leak).

    Right-reason red: /admin/teams/{id} route does not exist → 404 routing error
    (same status code, but from FastAPI "not found" not from the tenant-isolation check).
    After build the right 404 must come from the repository tenant-scope filter.
    """
    jwt_a, _ = await signup_and_login(
        client, tenant_name="TCoA8", email="ownerA8@teams.io"
    )
    jwt_b, _ = await signup_and_login(
        client, tenant_name="TCoB8", email="ownerB8@teams.io"
    )
    # Tenant B creates a team
    team_b = await create_team(client, jwt_b, name="b-team")
    team_b_id = team_b["id"]

    # Tenant A tries to GET it — must be 404
    resp = await client.get(f"{ADMIN_TEAMS}/{team_b_id}", headers=auth_jwt(jwt_a))
    assert resp.status_code == 404, (
        f"cross-tenant team must return 404; got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# S9 — add member to team
# ===========================================================================


async def test_add_member_to_team(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/teams/{id}/members → 201 + shape + DB row.

    Right-reason red: /admin/teams/{id}/members route does not exist → 404/405.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo9", email="owner9@teams.io"
    )
    team = await create_team(client, jwt, name="crew")
    team_id = team["id"]

    # Arrange a second user in the tenant
    user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', 'member')"
        ),
        {"id": user_id, "tid": tenant_id, "email": "user-tco9@teams.io"},
    )
    await db_session.commit()

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": user_id, "role": "lead"},
        headers=auth_jwt(jwt),
    )

    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["team_id"] == team_id
    assert body["user_id"] == user_id
    assert body["role"] == "lead"
    assert "added_at" in body

    # DB row must exist
    row = (
        await db_session.execute(
            text(
                "SELECT role FROM team_members WHERE team_id = :tid AND user_id = :uid"
            ),
            {"tid": team_id, "uid": user_id},
        )
    ).one()
    assert row[0] == "lead"


# ===========================================================================
# S10 — add unknown user returns 404
# ===========================================================================


async def test_add_unknown_user_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST members with unknown user_id → 404 ERR_USER_NOT_FOUND; no row created.

    Right-reason red: route does not exist → 404 routing error.
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="TCo10", email="owner10@teams.io"
    )
    team = await create_team(client, jwt, name="crew10")
    team_id = team["id"]
    phantom_user = str(uuid.uuid4())

    count_before = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_id},
        )
    ).scalar()

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": phantom_user, "role": "member"},
        headers=auth_jwt(jwt),
    )

    assert_problem(resp, 404, "ERR_USER_NOT_FOUND")

    count_after = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_id},
        )
    ).scalar()
    assert count_after == count_before, "no team_members row must be created"


# ===========================================================================
# S11 — duplicate member returns 409
# ===========================================================================


async def test_duplicate_member_returns_409(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Adding same user twice → 409 ERR_MEMBER_EXISTS; single row remains.

    Right-reason red: route does not exist → 404 routing error.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo11", email="owner11@teams.io"
    )
    team = await create_team(client, jwt, name="crew11")
    team_id = team["id"]

    user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', 'member')"
        ),
        {"id": user_id, "tid": tenant_id, "email": "user-tco11@teams.io"},
    )
    await db_session.commit()

    # First add succeeds
    r1 = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": user_id, "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert r1.status_code == 201, r1.text

    # Second add must be 409
    r2 = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": user_id, "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert_problem(r2, 409, "ERR_MEMBER_EXISTS")

    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM team_members WHERE team_id = :tid AND user_id = :uid"
            ),
            {"tid": team_id, "uid": user_id},
        )
    ).scalar()
    assert count == 1, f"exactly one team_members row must exist; got {count}"


# ===========================================================================
# S12 — remove member
# ===========================================================================


async def test_remove_member(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """DELETE /admin/teams/{id}/members/{user_id} → 204; row gone.

    Right-reason red: route does not exist → 404/405 routing error.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo12", email="owner12@teams.io"
    )
    team = await create_team(client, jwt, name="crew12")
    team_id = team["id"]

    user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', 'member')"
        ),
        {"id": user_id, "tid": tenant_id, "email": "user-tco12@teams.io"},
    )
    await db_session.commit()

    add_resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": user_id, "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert add_resp.status_code == 201, add_resp.text

    del_resp = await client.delete(
        f"{ADMIN_TEAMS}/{team_id}/members/{user_id}",
        headers=auth_jwt(jwt),
    )
    assert del_resp.status_code == 204, (
        f"expected 204, got {del_resp.status_code}: {del_resp.text}"
    )

    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM team_members WHERE team_id = :tid AND user_id = :uid"
            ),
            {"tid": team_id, "uid": user_id},
        )
    ).scalar()
    assert count == 0, f"team_members row must be gone after delete; got {count}"


# ===========================================================================
# S13 — create key with team attribution
# ===========================================================================


async def test_create_key_with_team_attribution(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/keys with team_id → 201; team_id in response + DB.

    Right-reason red: team_id field absent from CreateKeyRequest schema and
    CreateKeyResponse → AssertionError on missing field in response JSON.
    Additionally, the teams table does not exist → ProgrammingError on any DB
    validation of the FK before the migration is run.
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="TCo13", email="owner13@teams.io"
    )
    team = await create_team(client, jwt, name="ops-team")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="attributed", team_id=team_id)

    # Response must carry team_id
    assert "team_id" in key_body, f"team_id missing from create key response: {key_body}"
    assert key_body["team_id"] == team_id, (
        f"expected team_id={team_id!r}, got {key_body.get('team_id')!r}"
    )

    # DB row must have team_id set
    row = (
        await db_session.execute(
            text("SELECT team_id FROM api_keys WHERE id = :id"),
            {"id": key_body["key_id"]},
        )
    ).one()
    assert str(row[0]) == team_id, f"DB team_id mismatch: {row[0]}"


# ===========================================================================
# S14 — PATCH key sets team attribution
# ===========================================================================


async def test_patch_key_sets_team_attribution(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH /admin/keys/{id} with team_id → 200; team_id in response + DB.

    Right-reason red: team_id field absent from PatchKeyRequest/KeyInfoResponse schemas
    → AssertionError on missing field.
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="TCo14", email="owner14@teams.io"
    )
    team = await create_team(client, jwt, name="dev-team")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="patchable")
    key_id = key_body["key_id"]

    patch_resp = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"team_id": team_id},
        headers=auth_jwt(jwt),
    )
    assert patch_resp.status_code == 200, (
        f"expected 200, got {patch_resp.status_code}: {patch_resp.text}"
    )
    body = patch_resp.json()
    assert "team_id" in body, f"team_id missing from PATCH response: {body}"
    assert body["team_id"] == team_id

    # DB must reflect the attribution
    row = (
        await db_session.execute(
            text("SELECT team_id FROM api_keys WHERE id = :id"),
            {"id": key_id},
        )
    ).one()
    assert str(row[0]) == team_id


# ===========================================================================
# S15 — PATCH key clears team attribution
# ===========================================================================


async def test_patch_key_clears_team_attribution(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH /admin/keys/{id} with team_id=null → 200; team_id=null in response + DB.

    Right-reason red: team_id field absent from schemas → field missing in response.
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="TCo15", email="owner15@teams.io"
    )
    team = await create_team(client, jwt, name="tmp-team")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="clearable", team_id=team_id)
    key_id = key_body["key_id"]

    # Clear the attribution
    patch_resp = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"team_id": None},
        headers=auth_jwt(jwt),
    )
    assert patch_resp.status_code == 200, (
        f"expected 200, got {patch_resp.status_code}: {patch_resp.text}"
    )
    body = patch_resp.json()
    # team_id must be present (not absent) and null
    assert "team_id" in body, f"team_id key must be present in response even when null: {body}"
    assert body["team_id"] is None, f"team_id must be null after clear; got {body['team_id']}"

    # DB must be NULL
    row = (
        await db_session.execute(
            text("SELECT team_id FROM api_keys WHERE id = :id"),
            {"id": key_id},
        )
    ).one()
    assert row[0] is None, f"DB team_id must be NULL after clear; got {row[0]}"


# ===========================================================================
# S16 — key attribution with foreign tenant team returns 404
# ===========================================================================


async def test_key_attribution_foreign_tenant_team_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/keys with cross-tenant team_id → 404 ERR_TEAM_NOT_FOUND; no key row.

    Right-reason red: team_id field not accepted by CreateKeyRequest schema →
    422 validation error (wrong code, wrong status) before build.
    """
    jwt_a, _ = await signup_and_login(
        client, tenant_name="TCoA16", email="ownerA16@teams.io"
    )
    jwt_b, _ = await signup_and_login(
        client, tenant_name="TCoB16", email="ownerB16@teams.io"
    )
    # Tenant B creates a team
    team_b = await create_team(client, jwt_b, name="b-exclusive")
    team_b_id = team_b["id"]

    count_before = (
        await db_session.execute(text("SELECT COUNT(*) FROM api_keys"))
    ).scalar()

    # Tenant A tries to attribute a key to tenant B's team
    resp = await client.post(
        ADMIN_KEYS,
        json={"name": "foreign-attr", "team_id": team_b_id},
        headers=auth_jwt(jwt_a),
    )

    assert_problem(resp, 404, "ERR_TEAM_NOT_FOUND")

    count_after = (
        await db_session.execute(text("SELECT COUNT(*) FROM api_keys"))
    ).scalar()
    assert count_after == count_before, "no api_keys row must be created"


# ===========================================================================
# S17 — PATCH key attribution with nonexistent team returns 404
# ===========================================================================


async def test_patch_key_nonexistent_team_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH /admin/keys/{id} with random team UUID → 404 ERR_TEAM_NOT_FOUND; unchanged.

    Right-reason red: team_id field absent from PatchKeyRequest → field ignored or 422
    before the route-level team validation is wired.
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="TCo17", email="owner17@teams.io"
    )
    key_body = await create_key(client, jwt, name="stable-key")
    key_id = key_body["key_id"]
    phantom_team = str(uuid.uuid4())

    resp = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"team_id": phantom_team},
        headers=auth_jwt(jwt),
    )

    assert_problem(resp, 404, "ERR_TEAM_NOT_FOUND")

    # team_id must remain unchanged (NULL)
    row = (
        await db_session.execute(
            text("SELECT team_id FROM api_keys WHERE id = :id"),
            {"id": key_id},
        )
    ).one()
    assert row[0] is None, f"team_id must remain NULL; got {row[0]}"


# ===========================================================================
# S18 — invalid member role rejected
# ===========================================================================


async def test_invalid_member_role_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST members with role="manager" → 422 ERR_PAYLOAD_INVALID; no row created.

    Right-reason red: route does not exist → 404/405 routing error.
    """
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCo18", email="owner18@teams.io"
    )
    team = await create_team(client, jwt, name="strict-team")
    team_id = team["id"]

    user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', 'member')"
        ),
        {"id": user_id, "tid": tenant_id, "email": "user-tco18@teams.io"},
    )
    await db_session.commit()

    count_before = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_id},
        )
    ).scalar()

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": user_id, "role": "manager"},
        headers=auth_jwt(jwt),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")

    count_after = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_id},
        )
    ).scalar()
    assert count_after == count_before, "no team_members row must be created for invalid role"


# ===========================================================================
# S19 — GET /admin/keys list carries team_id
# ===========================================================================


async def test_list_keys_carries_team_id(
    client: httpx.AsyncClient,
) -> None:
    """GET /admin/keys → list item has team_id field set to the attributed team.

    Right-reason red: team_id field absent from KeyInfoResponse schema →
    field missing from list item (AssertionError).
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="TCo19", email="owner19@teams.io"
    )
    team = await create_team(client, jwt, name="list-team")
    team_id = team["id"]

    key_body = await create_key(client, jwt, name="listed-key", team_id=team_id)
    key_id = key_body["key_id"]

    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    assert list_resp.status_code == 200, list_resp.text
    items: list[dict[str, Any]] = list_resp.json()

    target = next((i for i in items if str(i.get("key_id")) == str(key_id)), None)
    assert target is not None, f"key {key_id} missing from list"
    assert "team_id" in target, f"team_id missing from key list item: {target}"
    assert target["team_id"] == team_id, (
        f"expected team_id={team_id!r}, got {target.get('team_id')!r}"
    )


# ===========================================================================
# S20 — cross-tenant add member returns 404
# ===========================================================================


async def test_cross_tenant_add_member_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Tenant B's JWT calling add-member on tenant A's team → 404 (no leak).

    Right-reason red: route does not exist → 404 routing error (same status but from
    FastAPI routing, not from tenant-isolation logic). After build the 404 must come
    from the team-scoped repository query returning empty.
    """
    jwt_a, tenant_a_id = await signup_and_login(
        client, tenant_name="TCoA20", email="ownerA20@teams.io"
    )
    jwt_b, tenant_b_id = await signup_and_login(
        client, tenant_name="TCoB20", email="ownerB20@teams.io"
    )
    # Tenant A creates a team
    team_a = await create_team(client, jwt_a, name="private-a")
    team_a_id = team_a["id"]

    # Arrange a user in tenant B to add (valid UUID in DB, just wrong tenant for the team)
    user_b_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', 'member')"
        ),
        {"id": user_b_id, "tid": tenant_b_id, "email": "user-tcob20@teams.io"},
    )
    await db_session.commit()

    # Tenant B calls add-member on tenant A's team
    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_a_id}/members",
        json={"user_id": user_b_id, "role": "member"},
        headers=auth_jwt(jwt_b),
    )

    assert resp.status_code == 404, (
        f"cross-tenant add-member must return 404; got {resp.status_code}: {resp.text}"
    )
