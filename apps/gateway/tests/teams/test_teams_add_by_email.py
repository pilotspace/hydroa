"""Failing-first (RED) suite for teams-add-by-email (TASK.md §4).

The Tin-approved gateway CR: POST /admin/teams/{id}/members accepts an `email`
(resolved server-side to a user_id, tenant-scoped) in addition to `user_id`.

TRUE-RED rule: every test asserts TARGET behavior and fails NOW for the right
reason. Today AddMemberRequest has no `email` field and `user_id` is required, so:
  - the email happy-path + unknown + cross-tenant tests get 422 (user_id missing),
    not the expected 201/404;
  - the "both" test currently 404s (email ignored, phantom user_id), not 422.
The "neither" test is already 422 — included as a regression guard (noted in §4).

Infrastructure: live Postgres (5433/gateway_test) + Redis (6380) via
infra/docker-compose.dev.yml; reuses the test_teams_core helpers.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.teams.test_teams_core import (
    ADMIN_TEAMS,
    assert_problem,
    auth_jwt,
    create_team,
    signup_and_login,
)


async def _seed_user(
    db_session: AsyncSession, *, tenant_id: str, email: str, role: str = "member"
) -> str:
    """Insert a user into the given tenant; return its user_id.

    Emails are stored lowercase to match the tenant-scoped resolution path
    (repository resolves on ``email.lower()``); the guard keeps the fixture and
    the code under test agreeing on normalization rather than silently diverging.
    """
    normalized = email.lower()
    assert normalized == email, "seed emails must be lowercase to match resolution"
    user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', :role)"
        ),
        {"id": user_id, "tid": tenant_id, "email": normalized, "role": role},
    )
    await db_session.commit()
    return user_id


async def test_add_member_by_email(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """POST {email, role} → 201 + resolved user_id + a team_members row."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCoEmail1", email="owner-email1@teams.io"
    )
    team = await create_team(client, jwt, name="crew-email1")
    team_id = team["id"]
    user_id = await _seed_user(db_session, tenant_id=tenant_id, email="member-email1@teams.io")

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"email": "member-email1@teams.io", "role": "lead"},
        headers=auth_jwt(jwt),
    )

    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["user_id"] == user_id
    assert body["role"] == "lead"
    assert body["team_id"] == team_id

    row = (
        await db_session.execute(
            text("SELECT role FROM team_members WHERE team_id = :tid AND user_id = :uid"),
            {"tid": team_id, "uid": user_id},
        )
    ).one()
    assert row[0] == "lead"


async def test_add_member_by_user_id_still_works(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Backward-compat guard: the user_id path is unchanged → 201."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCoEmail2", email="owner-email2@teams.io"
    )
    team = await create_team(client, jwt, name="crew-email2")
    user_id = await _seed_user(db_session, tenant_id=tenant_id, email="member-email2@teams.io")

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team['id']}/members",
        json={"user_id": user_id, "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 201, f"expected 201, got {resp.status_code}: {resp.text}"
    assert resp.json()["user_id"] == user_id


async def test_add_member_unknown_email_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """An email with no matching user → 404 ERR_USER_NOT_FOUND, no row created."""
    jwt, _ = await signup_and_login(client, tenant_name="TCoEmail3", email="owner-email3@teams.io")
    team = await create_team(client, jwt, name="crew-email3")
    team_id = team["id"]

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"email": "ghost@nowhere.io", "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 404, "ERR_USER_NOT_FOUND")

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_id},
        )
    ).scalar()
    assert count == 0, "no team_members row must be created"


async def test_add_member_cross_tenant_email_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Tenant isolation: an email belonging to a DIFFERENT tenant → 404, not added."""
    jwt_a, _tenant_a = await signup_and_login(
        client, tenant_name="TCoEmailA", email="owner-emailA@teams.io"
    )
    _jwt_b, tenant_b = await signup_and_login(
        client, tenant_name="TCoEmailB", email="owner-emailB@teams.io"
    )
    team_a = await create_team(client, jwt_a, name="crew-emailA")
    team_a_id = team_a["id"]
    # user b lives in tenant B
    await _seed_user(db_session, tenant_id=tenant_b, email="user-b@teams.io")

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_a_id}/members",
        json={"email": "user-b@teams.io", "role": "member"},
        headers=auth_jwt(jwt_a),
    )
    assert_problem(resp, 404, "ERR_USER_NOT_FOUND")

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_a_id},
        )
    ).scalar()
    assert count == 0, "cross-tenant user must NOT be added"


async def test_add_member_both_user_id_and_email_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Both user_id and email → 422 ERR_PAYLOAD_INVALID (exactly-one-of), no row."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="TCoEmail4", email="owner-email4@teams.io"
    )
    team = await create_team(client, jwt, name="crew-email4")
    team_id = team["id"]
    user_id = await _seed_user(db_session, tenant_id=tenant_id, email="member-email4@teams.io")

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": user_id, "email": "member-email4@teams.io", "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM team_members WHERE team_id = :tid"),
            {"tid": team_id},
        )
    ).scalar()
    assert count == 0, "no row on a both-fields request"


async def test_add_member_neither_422(client: httpx.AsyncClient) -> None:
    """Neither user_id nor email → 422 ERR_PAYLOAD_INVALID (regression guard)."""
    jwt, _ = await signup_and_login(client, tenant_name="TCoEmail5", email="owner-email5@teams.io")
    team = await create_team(client, jwt, name="crew-email5")

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team['id']}/members",
        json={"role": "member"},
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_add_member_whitespace_email_422(client: httpx.AsyncClient) -> None:
    """A whitespace-only email collapses to empty (str_strip_whitespace) → 422.

    Boundary guard: `str_strip_whitespace=True` strips `"   "` to `""`, which the
    exactly-one-of validator treats as "no email present" — so neither identifier
    is supplied and the request is rejected, never reaching tenant resolution.
    """
    jwt, _ = await signup_and_login(client, tenant_name="TCoEmail6", email="owner-email6@teams.io")
    team = await create_team(client, jwt, name="crew-email6")

    resp = await client.post(
        f"{ADMIN_TEAMS}/{team['id']}/members",
        json={"email": "   ", "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
