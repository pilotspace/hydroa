"""RED->GREEN suite: teams audit-coverage + cross-tenant remove_member guard
(fix/audit-remediation).

Two findings closed here:
  1. HIGH audit-coverage gap — teams/api/router.py's `delete_team`, `patch_team_budget`, and
     `remove_member` write NO audit_events row today; only `add_member` audits (action
     "member.role_assign"). This suite adds the missing record_audit call sites, using the
     EXACT same fire-and-forget idiom `add_member` already uses (asyncio.ensure_future +
     record_audit(request.app.state.sessionmaker, AuditEvent(...)) scheduled AFTER the
     mutation's own commit — a SEPARATE session/transaction, never the action's own).
  2. LOW cross-tenant guard — teams/infrastructure/repository.py's `remove_member` DELETE
     matches only on (team_id, user_id), with NO tenant_id in the WHERE clause; today it is
     guarded ONLY by RemoveMemberUseCase's own pre-check (`get_by_id(team_id, tenant_id)`
     raising TeamNotFoundError before the repository call). That pre-check happens to make the
     HTTP-level (use-case-mediated) path safe today, but the repository method itself — called
     directly, or by any FUTURE caller that skips the use-case's pre-check — would silently
     delete a cross-tenant member row. This suite calls the repository method DIRECTLY
     (bypassing the use case) to prove the defect and the fix, per the task's explicit
     instruction to add tenant_id to the WHERE clause with a repository-level negative test.

Fixture/helper provenance (suite-local convention, mirrors tests/teams/test_teams_core.py):
  - `signup_and_login`, `auth_jwt`, `create_team` mirror test_teams_core.py's own helpers.
  - `fetch_one_audit_row` / `count_audit_rows` / `_drain_fire_and_forget` mirror
    tests/admin_console_audit/test_admin_console_audit.py's own helpers.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

ADMIN_TEAMS = "/admin/teams"


# ---------------------------------------------------------------------------
# Helpers (mirrors test_teams_core.py)
# ---------------------------------------------------------------------------


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> tuple[str, str]:
    sr = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_team(client: httpx.AsyncClient, jwt: str, *, name: str) -> dict[str, Any]:
    resp = await client.post(ADMIN_TEAMS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_team failed ({resp.status_code}): {resp.text}"
    return resp.json()


async def _insert_user(
    db_session: AsyncSession, *, tenant_id: str, email: str, role: str = "member"
) -> str:
    uid = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'hash', :role)"
        ),
        {"id": uid, "tid": tenant_id, "email": email, "role": role},
    )
    await db_session.commit()
    return uid


async def fetch_one_audit_row(session: AsyncSession, *, action: str) -> Row[Any] | None:
    result = await session.execute(
        text(
            "SELECT tenant_id, actor_user_id, actor_email, action, target_type, "
            "target_id, result, metadata FROM audit_events "
            "WHERE action = :action ORDER BY created_at DESC LIMIT 1"
        ),
        {"action": action},
    )
    return result.fetchone()


async def count_audit_rows(session: AsyncSession, *, action: str) -> int:
    result = await session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE action = :action"),
        {"action": action},
    )
    return int(result.scalar() or 0)


async def _drain_fire_and_forget() -> None:
    await asyncio.sleep(0.05)


# ===========================================================================
# delete_team is audited
# ===========================================================================


async def test_delete_team_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="DeleteTeamAuditCo", email="owner@deleteteamaudit.io"
    )
    team = await create_team(client, jwt, name="doomed")
    team_id = team["id"]

    resp = await client.delete(f"{ADMIN_TEAMS}/{team_id}", headers=auth_jwt(jwt))
    assert resp.status_code == 204, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="team.delete")
    assert row is not None, "expected exactly one team.delete audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, _metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "team"
    assert target_id == str(team_id)
    assert result == "success"


# ===========================================================================
# patch_team_budget is audited
# ===========================================================================


async def test_patch_team_budget_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="BudgetTeamAuditCo", email="owner@budgetteamaudit.io"
    )
    team = await create_team(client, jwt, name="budgeted")
    team_id = team["id"]

    resp = await client.patch(
        f"{ADMIN_TEAMS}/{team_id}",
        json={"team_budget_usd": "500.00"},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="team.budget_update")
    assert row is not None, "expected exactly one team.budget_update audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "team"
    assert target_id == str(team_id)
    assert result == "success"
    assert metadata == {"team_budget_usd": "500.00"}


# ===========================================================================
# remove_member is audited
# ===========================================================================


async def test_remove_member_is_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="RemoveMemberAuditCo", email="owner@removememberaudit.io"
    )
    team = await create_team(client, jwt, name="crew")
    team_id = team["id"]
    user_id = await _insert_user(db_session, tenant_id=tenant_id, email="member@removememberaudit.io")

    add_resp = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": user_id, "role": "member"},
        headers=auth_jwt(jwt),
    )
    assert add_resp.status_code == 201, add_resp.text
    await _drain_fire_and_forget()

    del_resp = await client.delete(
        f"{ADMIN_TEAMS}/{team_id}/members/{user_id}", headers=auth_jwt(jwt)
    )
    assert del_resp.status_code == 204, del_resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="member.remove")
    assert row is not None, "expected exactly one member.remove audit event"
    row_tenant_id, actor_user_id, _actor_email, _action, target_type, target_id, result, metadata = (
        row
    )
    assert str(row_tenant_id) == str(tenant_id)
    assert actor_user_id is not None
    assert target_type == "user"
    assert target_id == str(user_id)
    assert result == "success"
    assert metadata == {"team_id": str(team_id)}


# ===========================================================================
# Repository-level: remove_member must be scoped by tenant_id (cross-tenant guard)
# ===========================================================================


async def test_repository_remove_member_is_scoped_to_tenant_id(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """SqlAlchemyTeamRepository.remove_member(team_id=<tenant A's team>,
    tenant_id=<tenant B>, user_id=<tenant A's member>) must be a NO-OP (return False,
    row untouched) — called DIRECTLY, bypassing RemoveMemberUseCase's own pre-check, which
    is the exact defense-in-depth gap the task flags (repository DELETE has no tenant_id in
    its WHERE clause today; only the use-case's own pre-check happens to prevent the
    HTTP-level path from exploiting it).

    RED reason: today's `delete(TeamMemberRow).where(team_id=.., user_id=..)` has no
    tenant scoping at all, so this call actually deletes the row and returns True —
    the opposite of what this test asserts.
    """
    from gateway.teams.infrastructure.repository import SqlAlchemyTeamRepository

    jwt_a, tenant_a_id = await signup_and_login(
        client, tenant_name="CrossTenantRemoveA", email="ownerA@crosstenantremove.io"
    )
    _jwt_b, tenant_b_id = await signup_and_login(
        client, tenant_name="CrossTenantRemoveB", email="ownerB@crosstenantremove.io"
    )

    team_a = await create_team(client, jwt_a, name="team-a-private")
    team_a_id = team_a["id"]
    member_user_id = await _insert_user(
        db_session, tenant_id=tenant_a_id, email="member@crosstenantremove.io"
    )
    add_resp = await client.post(
        f"{ADMIN_TEAMS}/{team_a_id}/members",
        json={"user_id": member_user_id, "role": "member"},
        headers=auth_jwt(jwt_a),
    )
    assert add_resp.status_code == 201, add_resp.text

    repo = SqlAlchemyTeamRepository(db_session)
    removed = await repo.remove_member(
        team_id=uuid.UUID(team_a_id),
        tenant_id=uuid.UUID(tenant_b_id),
        user_id=uuid.UUID(member_user_id),
    )
    assert removed is False, (
        "remove_member must be a no-op cross-tenant — it deleted tenant A's member "
        "row using tenant B's tenant_id, proving the WHERE clause has no tenant scope"
    )

    row = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM team_members WHERE team_id = :tid AND user_id = :uid"
            ),
            {"tid": team_a_id, "uid": member_user_id},
        )
    ).scalar()
    assert row == 1, "tenant A's member row must be UNTOUCHED by tenant B's cross-tenant call"

    # Sanity: the SAME-tenant call still works (not over-scoped into a no-op always).
    removed_same_tenant = await repo.remove_member(
        team_id=uuid.UUID(team_a_id),
        tenant_id=uuid.UUID(tenant_a_id),
        user_id=uuid.UUID(member_user_id),
    )
    assert removed_same_tenant is True

    row_after = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM team_members WHERE team_id = :tid AND user_id = :uid"
            ),
            {"tid": team_a_id, "uid": member_user_id},
        )
    ).scalar()
    assert row_after == 0
