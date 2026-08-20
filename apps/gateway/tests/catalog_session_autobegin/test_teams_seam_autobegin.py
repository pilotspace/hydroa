"""The SECOND live seam of the revocation-guard autobegin class (.add/tasks/
catalog-sync-session-autobegin.md §CHECKS — M1b).

`keys/api/deps.py::get_identity` runs the same session-revocation SELECT as the catalog
seam, on the same request-scoped session, and `teams/api/deps.py` builds
`SqlAlchemyTeamRepository` on that session. The repository owns its own transaction
(teams/infrastructure/repository.py:42,:209), so the autobegun read-only transaction makes
`async with self._session.begin()` raise InvalidRequestError and EVERY /admin/teams
mutation 500s.

Red at 42224201 (tests/teams: 29 failed / 2 passed). This module drives the seam directly
so the failure is attributed to the dependency rather than to any one teams route.

DO NOT weaken this test to pass. Removing the revocation guard from keys/api/deps.py would
turn phase B green while trading away the auth-hardening P0 gate — that is R:GATE_REMOVED.
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.catalog_session_autobegin.conftest import (
    LOGOUT,
    auth,
    owner_user_id,
    signup_and_login,
)

ADMIN_TEAMS = "/admin/teams"


async def _team_count(db_session: AsyncSession, tenant_id: str) -> int:
    return int(
        (
            await db_session.execute(
                text("SELECT count(*) FROM teams WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
        ).scalar_one()
    )


async def test_teams_mutations_succeed_and_stay_revocation_guarded(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """covers: M1b, M2, A1, A6, E4 — an authenticated console identity can create a team and
    add a member (rows actually persisted through SqlAlchemyTeamRepository's OWN
    transaction), and a denylisted jti is still refused at that seam.

    Right-reason red before the fix: InvalidRequestError ("A transaction is already begun on
    this Session") escaping the FIRST mutating call — at the repository's `session.begin()`,
    not at a route, an import or a fixture.
    """
    # --- A. the mutations work again, and actually persist (M1b, E4) ---
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Autobegin Teams", email="owner@autobegin-teams.io"
    )

    create = await client.post(ADMIN_TEAMS, json={"name": "platform"}, headers=auth(jwt))
    assert create.status_code == 201, (
        f"the keys/teams seam still 500s on create: {create.status_code}: {create.text}"
    )
    body: dict[str, Any] = create.json()
    team_id = body["id"]
    assert body["tenant_id"] == tenant_id

    # E4: persisted through the repository's own transaction, not merely echoed back.
    assert await _team_count(db_session, tenant_id) == 1

    # A second mutation on a DIFFERENT repository method (:209, the member path) — the
    # autobegin clash is per-`begin()`, so one green call does not vouch for the others.
    uid = await owner_user_id(db_session, email="owner@autobegin-teams.io")
    member = await client.post(
        f"{ADMIN_TEAMS}/{team_id}/members",
        json={"user_id": str(uid), "role": "lead"},
        headers=auth(jwt),
    )
    assert member.status_code == 201, (
        f"the keys/teams seam still 500s on member add: {member.status_code}: {member.text}"
    )

    # --- B. revocation is STILL enforced at this seam (M2, A6, R:GATE_REMOVED) ---
    assert (await client.post(LOGOUT, headers=auth(jwt))).status_code == 204

    revoked = await client.post(ADMIN_TEAMS, json={"name": "after-logout"}, headers=auth(jwt))
    assert revoked.status_code == 401, (
        f"gate removed: a revoked console token mutated teams ({revoked.status_code}) "
        f"— R:GATE_REMOVED"
    )
    # A6: authz refuses BEFORE any transaction bookkeeping, so nothing was written.
    assert await _team_count(db_session, tenant_id) == 1
