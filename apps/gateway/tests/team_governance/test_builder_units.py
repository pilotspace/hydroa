"""BUILDER-ADDED unit tests (team-governance §5) — NOT part of the frozen red suite.

Direct-repository and unit coverage for branches the behavior suite exercises only
through the API surface (coverage-floor headroom per §5 build constraint). These
tests assert the same §3-contracted semantics at the repository/use-case layer.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.teams.domain.errors import (
    MemberExistsError,
    TeamExistsError,
    TeamNotFoundError,
    UserNotFoundError,
)
from gateway.teams.infrastructure.repository import SqlAlchemyTeamRepository


@pytest.fixture
async def tenant_id(db_session: AsyncSession) -> uuid.UUID:
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tid), "name": f"unit-tenant-{tid.hex[:8]}"},
    )
    await db_session.commit()
    return tid


@pytest.fixture
async def user_id(db_session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    uid = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'x', 'member')"
        ),
        {"id": str(uid), "tid": str(tenant_id), "email": f"u-{uid.hex[:8]}@unit.io"},
    )
    await db_session.commit()
    return uid


@pytest.fixture
async def repo(app: Any) -> Any:
    # Repo methods manage their own transactions (session.begin()) — give the
    # repository a dedicated session so the db_session fixture's open transaction
    # (from arrange inserts) cannot conflict.
    async with app.state.sessionmaker() as session:
        yield SqlAlchemyTeamRepository(session)


async def _settle(repo: SqlAlchemyTeamRepository) -> None:
    """Close the session's autobegun (post-commit attribute refresh) transaction.

    Repo methods open their own session.begin(); in production each request uses
    one repo call per session, so the leaked autobegin never bites. Back-to-back
    calls on one session (these unit tests) must settle in between.
    """
    await repo._session.rollback()  # noqa: SLF001 — test-only session settle


async def _mk_team(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID, name: str = "unit-team"
) -> uuid.UUID:
    await _settle(repo)
    team = await repo.create(team_id=uuid.uuid4(), tenant_id=tenant_id, name=name)
    return team.id


async def test_repo_create_duplicate_raises(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID
) -> None:
    await _mk_team(repo, tenant_id, name="dup")
    with pytest.raises(TeamExistsError):
        await _mk_team(repo, tenant_id, name="dup")


async def test_repo_get_by_id_detail_and_cross_tenant_none(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    team_id = await _mk_team(repo, tenant_id)
    await _settle(repo)
    await repo.add_member(team_id=team_id, tenant_id=tenant_id, user_id=user_id, role="member")
    await _settle(repo)

    detail = await repo.get_by_id(team_id, tenant_id)
    assert detail is not None
    assert detail.member_count == 1
    assert detail.key_count == 0
    assert detail.members[0].user_id == user_id
    assert detail.team_budget_usd is None

    assert await repo.get_by_id(team_id, uuid.uuid4()) is None  # cross-tenant invisible


async def test_repo_delete_true_then_false(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID
) -> None:
    team_id = await _mk_team(repo, tenant_id)
    assert await repo.delete(team_id, tenant_id) is True
    assert await repo.delete(team_id, tenant_id) is False


async def test_repo_add_member_error_branches(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    team_id = await _mk_team(repo, tenant_id)

    await _settle(repo)
    with pytest.raises(TeamNotFoundError):
        await repo.add_member(
            team_id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, role="member"
        )
    await _settle(repo)
    with pytest.raises(UserNotFoundError):
        await repo.add_member(
            team_id=team_id, tenant_id=tenant_id, user_id=uuid.uuid4(), role="member"
        )

    await _settle(repo)
    await repo.add_member(team_id=team_id, tenant_id=tenant_id, user_id=user_id, role="lead")
    await _settle(repo)
    with pytest.raises(MemberExistsError):
        await repo.add_member(team_id=team_id, tenant_id=tenant_id, user_id=user_id, role="lead")


async def test_repo_remove_member_true_then_false(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    team_id = await _mk_team(repo, tenant_id)
    await _settle(repo)
    await repo.add_member(team_id=team_id, tenant_id=tenant_id, user_id=user_id, role="member")
    await _settle(repo)
    assert await repo.remove_member(team_id=team_id, tenant_id=tenant_id, user_id=user_id) is True
    assert await repo.remove_member(team_id=team_id, tenant_id=tenant_id, user_id=user_id) is False


async def test_repo_get_team_for_tenant(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID
) -> None:
    team_id = await _mk_team(repo, tenant_id)
    assert await repo.get_team_for_tenant(team_id, tenant_id) is True
    assert await repo.get_team_for_tenant(team_id, uuid.uuid4()) is False
    assert await repo.get_team_for_tenant(uuid.uuid4(), tenant_id) is False


async def test_repo_update_budget_set_clear_and_missing(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID
) -> None:
    team_id = await _mk_team(repo, tenant_id)

    updated = await repo.update_budget(team_id, tenant_id, Decimal("25.00"))
    assert updated is not None
    assert updated.team_budget_usd == Decimal("25.00")

    cleared = await repo.update_budget(team_id, tenant_id, None)
    assert cleared is not None
    assert cleared.team_budget_usd is None

    assert await repo.update_budget(uuid.uuid4(), tenant_id, Decimal("1")) is None
    assert await repo.update_budget(team_id, uuid.uuid4(), Decimal("1")) is None  # cross-tenant


async def test_repo_list_includes_budget(
    repo: SqlAlchemyTeamRepository, tenant_id: uuid.UUID
) -> None:
    team_id = await _mk_team(repo, tenant_id, name="listed")
    await repo.update_budget(team_id, tenant_id, Decimal("7.50"))
    teams = await repo.list_by_tenant(tenant_id)
    assert len(teams) == 1
    assert teams[0].team_budget_usd == Decimal("7.50")
    assert teams[0].name == "listed"


def _bare_use_case(budget_guard: Any) -> Any:
    """CompletionUseCase with stub collaborators — only budget paths exercised."""
    from gateway.proxy.application.use_cases import CompletionUseCase

    return CompletionUseCase(None, None, budget_guard, None)  # type: ignore[arg-type]


async def test_team_budget_check_fail_open_on_redis_error() -> None:
    """_check_team_budget swallows Redis errors (advisory fail-OPEN, §3)."""
    from gateway.keys.domain.entities import AuthzResult

    class _BoomRedis:
        async def get(self, key: str) -> bytes:
            raise ConnectionError("redis down")

    class _GuardWithBoomRedis:
        _redis = _BoomRedis()

    use_case = _bare_use_case(_GuardWithBoomRedis())
    authz = AuthzResult(
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        team_budget_usd=Decimal("1.00"),
    )
    # Must NOT raise — fail-open on Redis failure
    await use_case._check_team_budget(authz)


async def test_team_budget_check_rejects_at_cap() -> None:
    """Counter >= team_budget_usd → 402 ERR_BUDGET_EXCEEDED with team-scoped detail."""
    from gateway.core.errors import ProblemError
    from gateway.keys.domain.entities import AuthzResult

    class _Redis:
        async def get(self, key: str) -> bytes:
            return b"5.00"

    class _Guard:
        _redis = _Redis()

    use_case = _bare_use_case(_Guard())
    authz = AuthzResult(
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        team_budget_usd=Decimal("5.00"),
    )
    with pytest.raises(ProblemError) as exc_info:
        await use_case._check_team_budget(authz)
    assert exc_info.value.status == 402
    assert exc_info.value.code == "ERR_BUDGET_EXCEEDED"


async def test_team_budget_check_skips_when_unset() -> None:
    """No team_id or no team budget → no Redis read, no rejection."""
    from gateway.keys.domain.entities import AuthzResult

    class _NeverRedis:
        async def get(self, key: str) -> bytes:
            raise AssertionError("Redis must not be read when team budget unset")

    class _Guard:
        _redis = _NeverRedis()

    use_case = _bare_use_case(_Guard())
    await use_case._check_team_budget(AuthzResult(tenant_id=uuid.uuid4(), key_id=uuid.uuid4()))
    await use_case._check_team_budget(
        AuthzResult(tenant_id=uuid.uuid4(), key_id=uuid.uuid4(), team_id=uuid.uuid4())
    )
