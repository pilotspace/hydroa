"""RED suite: M4/R2/M9 — plan model-allowlist intersection (TASK.md §3, FROZEN @ v1).

Covers BOTH independently-maintained governance copies (§0 Issues/Risks — dual-copy drift
risk, never staggered across two PRs):
  - chat path: gateway.proxy.application.use_cases._check_plan_model_allowlist, exercised
    via a real HTTP /v1/chat/completions request (real Postgres).
  - non-chat path: gateway.proxy.application.governance.NonChatGovernance
    ._check_plan_model_allowlist, exercised directly against fakes (mirrors
    tests/nonchat_soft_budget_alert's own no-DB fake pattern) — the dual-copy risk this
    suite exists to catch.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.errors import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.domain.ports import ModelAccess

from .conftest import assert_problem, assign_plan, auth, seed_active_model, seed_plan, signup_owner

COMPLETIONS = "/v1/chat/completions"


class FakeCompletionUpstream:
    def __init__(self) -> None:
        self.calls = 0
        self.body = {
            "id": "gen-plan-allow-1",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return 200, self.body


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    return await signup_owner(client, tenant_name="PlanAllowCo", email="owner@planallow.io")


def _install(app: Any, upstream: FakeCompletionUpstream) -> None:
    from gateway.budgets.domain.ports import PassthroughBudgetGuard

    app.state.completion_upstream = upstream
    app.state.budget_guard = PassthroughBudgetGuard()


# ---------------------------------------------------------------------------
# M4/R2/M9 — chat path (use_cases.py), HTTP-level, real DB
# ---------------------------------------------------------------------------


async def test_chat_model_excluded_by_plan_but_allowed_by_key_is_rejected(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    await seed_active_model(db_session, model_id="gpt-4o-mini")
    await seed_active_model(db_session, model_id="claude-opus-4")
    plan_id = await seed_plan(db_session, name="starter", model_allowlist=["gpt-4o-mini"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    upstream = FakeCompletionUpstream()
    _install(app, upstream)

    from sqlalchemy import text

    count_before = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()

    resp = await client.post(
        COMPLETIONS,
        json={"model": "claude-opus-4", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert_problem(resp, 403, "ERR_PLAN_MODEL_NOT_ALLOWED")
    hint = resp.json().get("upgrade_hint")
    assert hint is not None, f"expected extra.upgrade_hint in body, got {resp.json()}"
    assert hint["plan_id"] == plan_id
    assert hint["model"] == "claude-opus-4"
    assert upstream.calls == 0

    count_after = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()
    assert count_after == count_before, "no usage_record for a plan-rejected request"


async def test_chat_model_allowed_by_both_key_and_plan_succeeds(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    await seed_active_model(db_session, model_id="gpt-4o-mini")
    plan_id = await seed_plan(db_session, name="starter", model_allowlist=["gpt-4o-mini"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    upstream = FakeCompletionUpstream()
    _install(app, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


async def test_chat_key_only_allowlist_rejection_unchanged_when_no_plan(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    """M4 (unchanged) — an unplanned tenant's key-level allowlist rejection still fires the
    ORIGINAL ERR_MODEL_NOT_ALLOWED, never the new plan code."""
    await seed_active_model(db_session, model_id="gpt-4o-mini")
    await seed_active_model(db_session, model_id="claude-opus-4")

    key_created = await client.post(
        "/admin/keys",
        json={"name": "allowlisted-key", "model_allowlist": ["gpt-4o-mini"]},
        headers=auth(owner["jwt"]),
    )
    assert key_created.status_code == 201, key_created.text
    scoped_key = key_created.json()["key"]

    upstream = FakeCompletionUpstream()
    _install(app, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": "claude-opus-4", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(scoped_key),
    )

    assert_problem(resp, 403, "ERR_MODEL_NOT_ALLOWED")


async def test_chat_null_plan_model_allowlist_is_a_noop(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    await seed_active_model(db_session, model_id="gpt-4o-mini")
    plan_id = await seed_plan(db_session, name="enterprise", model_allowlist=None)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    upstream = FakeCompletionUpstream()
    _install(app, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


# ---------------------------------------------------------------------------
# M4/R2/M9 — non-chat path (governance.py), fake-based, no DB (dual-copy coverage)
# ---------------------------------------------------------------------------

_TENANT = uuid.uuid4()
_KEY = uuid.uuid4()
_PLAN = uuid.uuid4()


class _FakeAuthenticator:
    def __init__(self, authz: AuthzResult) -> None:
        self._authz = authz

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return self._authz


class _FakeModelChecker:
    async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
        return ModelAccess.ACTIVE


class _FakeBudgetGuard:
    async def check(self, tenant_id: uuid.UUID) -> None:
        return None


def _authz(
    *,
    model_allowlist: list[str] | None = None,
    plan_id: uuid.UUID | None = None,
    plan_model_allowlist: list[str] | None = None,
    plan_name: str | None = None,
) -> AuthzResult:
    return AuthzResult(
        tenant_id=_TENANT,
        key_id=_KEY,
        model_allowlist=model_allowlist,
        plan_id=plan_id,
        plan_model_allowlist=plan_model_allowlist,
        plan_name=plan_name,
    )


def _governance(authz: AuthzResult) -> NonChatGovernance:
    return NonChatGovernance(
        authenticator=_FakeAuthenticator(authz),
        model_checker=_FakeModelChecker(),
        budget_guard=_FakeBudgetGuard(),
        rate_limiter=None,
        redis_client=None,
    )


async def test_nonchat_model_excluded_by_plan_but_allowed_by_key_is_rejected() -> None:
    authz = _authz(
        model_allowlist=["gpt-4o-mini", "claude-opus-4"],
        plan_id=_PLAN,
        plan_model_allowlist=["gpt-4o-mini"],
        plan_name="starter",
    )
    gov = _governance(authz)

    with pytest.raises(ProblemError) as exc:
        await gov.authorize("sk-good", "claude-opus-4")

    assert exc.value.status == 403
    assert exc.value.code == "ERR_PLAN_MODEL_NOT_ALLOWED"
    assert exc.value.extra is not None
    hint = exc.value.extra["upgrade_hint"]
    assert hint["plan_id"] == str(_PLAN)
    assert hint["plan_name"] == "starter"
    assert hint["model"] == "claude-opus-4"


async def test_nonchat_model_allowed_by_both_key_and_plan_succeeds() -> None:
    authz = _authz(
        model_allowlist=["gpt-4o-mini", "claude-opus-4"],
        plan_id=_PLAN,
        plan_model_allowlist=["gpt-4o-mini"],
    )
    gov = _governance(authz)

    result = await gov.authorize("sk-good", "gpt-4o-mini")

    assert result.key_id == _KEY


async def test_nonchat_unplanned_tenant_grandfathered_regardless_of_plan_allowlist_field() -> None:
    """M7 — plan_id None short-circuits the plan-allowlist check entirely, even if a stray
    plan_model_allowlist value were somehow present."""
    authz = _authz(model_allowlist=None, plan_id=None, plan_model_allowlist=["gpt-4o-mini"])
    gov = _governance(authz)

    result = await gov.authorize("sk-good", "claude-opus-4")

    assert result.key_id == _KEY


async def test_nonchat_null_plan_allowlist_imposes_no_restriction() -> None:
    authz = _authz(model_allowlist=None, plan_id=_PLAN, plan_model_allowlist=None)
    gov = _governance(authz)

    result = await gov.authorize("sk-good", "any-model-at-all")

    assert result.key_id == _KEY
