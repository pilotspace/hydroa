"""RED suite for agent-identity-governance (TASK.md §3 CONTRACT FROZEN @ v1).

One test per §2 scenario (M1-M12 + rejects + edge cases). Asserts observable
behavior (HTTP status/body shape, DB row state) — never internals.

M4 (principal aggregate budget) is unit-tested directly against NonChatGovernance
with fakes — mirrors the established pattern in
tests/nonchat_soft_budget_alert/test_nonchat_soft_budget_alert.py (a real
Postgres/HTTP round trip through the full data plane is unnecessary to prove the
governance CHECK fires; the write-side Redis INCR is a disclosed, out-of-scope
follow-up — see TASK.md §7 OBSERVE, mirrors team_budget_usd's own precedent, whose
recorder-side increment is ALSO not yet wired: "recorder does not yet INCRBYFLOAT
usage:spend:team:..." per tests/team_governance/test_team_governance.py).

M6 (last_seen_at) note: TASK.md §0 Ground note #6 establishes that a principal can
ONLY be attached to an ALREADY-MINTED agent_tokens row (never a pending/unminted
device authorization) — and each device_code mints at most once. So the scenario's
literal "mint tied to an attached token" cannot occur via two sequential public
/oauth/token polls (the token is unattached at the instant it is minted, by
construction). This suite instead: (a) proves the production hook function
(bump_principal_last_seen) fires and updates last_seen_at when invoked for an
attached token — the SAME function token_router.py schedules fire-and-forget after
every mint; and (b) proves a data-plane call through /internal/authz for that same
attached token does NOT update last_seen_at (the never-a-hot-path-write half of M6).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from gateway.agent_oauth.application.principal_use_cases import bump_principal_last_seen
from gateway.agent_oauth.infrastructure.orm import AgentPrincipalRow, AgentTokenRow
from gateway.core.error_catalog import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.domain.ports import ModelAccess
from gateway.tenants.domain.entities import Role
from tests.agent_identity_governance.conftest import mint_agent_token, mint_role_jwt

_AGENTS = "/admin/agents"


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# --------------------------------------------------------------------------- M1


async def test_create_agent_principal_owner(
    client: httpx.AsyncClient, owner: dict[str, Any]
) -> None:
    r = await client.post(
        _AGENTS,
        json={"name": "billing-bot", "monthly_budget_usd": "50.00"},
        headers=_auth(owner["owner_jwt"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "billing-bot"
    assert body["monthly_budget_usd"] == "50.00"
    assert body["created_at"] is not None
    assert body["last_seen_at"] is None
    assert body["killed_at"] is None
    assert body["attached_token_count"] == 0


async def test_create_agent_principal_missing_bearer(client: httpx.AsyncClient) -> None:
    r = await client.post(_AGENTS, json={"name": "no-auth-bot"})
    assert r.status_code == 401


async def test_create_agent_principal_forbidden_for_viewer(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    viewer_jwt = mint_role_jwt(
        app, tenant_id=owner["tenant_id"], role=Role.VIEWER, email="viewer@agentco.io"
    )
    r = await client.post(_AGENTS, json={"name": "viewer-bot"}, headers=_auth(viewer_jwt))
    assert r.status_code == 403


async def test_create_agent_principal_duplicate_name_conflict(
    client: httpx.AsyncClient, owner: dict[str, Any]
) -> None:
    ok = await client.post(_AGENTS, json={"name": "dup-bot"}, headers=_auth(owner["owner_jwt"]))
    assert ok.status_code == 200
    dup = await client.post(_AGENTS, json={"name": "dup-bot"}, headers=_auth(owner["owner_jwt"]))
    assert dup.status_code == 409
    assert dup.json()["code"] == "ERR_AGENT_PRINCIPAL_NAME_CONFLICT"


async def test_create_agent_principal_invalid_budget(
    client: httpx.AsyncClient, owner: dict[str, Any]
) -> None:
    r = await client.post(
        _AGENTS,
        json={"name": "bad-budget-bot", "monthly_budget_usd": "-5.00"},
        headers=_auth(owner["owner_jwt"]),
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- M2


async def test_attach_agent_token_admin(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    admin_jwt = mint_role_jwt(
        app, tenant_id=owner["tenant_id"], role=Role.ADMIN, email="admin@agentco.io"
    )
    principal = (
        await client.post(_AGENTS, json={"name": "attach-bot"}, headers=_auth(admin_jwt))
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])

    r = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(admin_jwt),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["principal_id"] == principal["id"]
    assert body["token_id"] == str(token["token_id"])
    assert body["attached_at"] is not None

    listing = (await client.get(_AGENTS, headers=_auth(admin_jwt))).json()
    got = next(a for a in listing["agents"] if a["id"] == principal["id"])
    assert got["attached_token_count"] == 1


async def test_attach_unknown_or_cross_tenant_token_not_found(
    client: httpx.AsyncClient, owner: dict[str, Any], other_tenant: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "attach-404-bot"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()

    # Case 1: fully unknown token id.
    unknown = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{uuid.uuid4()}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "ERR_AGENT_TOKEN_NOT_FOUND"

    # Case 2: a REAL token, but belonging to a different tenant — byte-identical 404.
    other_token = await mint_agent_token(
        app, tenant_id=other_tenant["tenant_id"], user_id=other_tenant["user_id"]
    )
    cross = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{other_token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert cross.status_code == 404
    assert cross.json()["code"] == "ERR_AGENT_TOKEN_NOT_FOUND"
    assert cross.json() == unknown.json()


async def test_attach_token_already_attached_elsewhere(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal_a = (
        await client.post(_AGENTS, json={"name": "principal-a"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    principal_b = (
        await client.post(_AGENTS, json={"name": "principal-b"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])

    first = await client.post(
        f"{_AGENTS}/{principal_a['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert first.status_code == 200

    second = await client.post(
        f"{_AGENTS}/{principal_b['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert second.status_code == 409
    assert second.json()["code"] == "ERR_AGENT_TOKEN_ALREADY_ATTACHED"


async def test_attach_fresh_token_to_killed_principal_rejected(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "kill-then-attach"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    kill = await client.post(f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"]))
    assert kill.status_code == 200

    fresh_token = await mint_agent_token(
        app, tenant_id=owner["tenant_id"], user_id=owner["user_id"]
    )
    r = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{fresh_token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert r.status_code == 409
    assert r.json()["code"] == "ERR_AGENT_PRINCIPAL_KILLED"


# --------------------------------------------------------------------------- M3


async def test_detach_token_reverts_to_default(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS,
            json={"name": "detach-bot", "monthly_budget_usd": "5.00"},
            headers=_auth(owner["owner_jwt"]),
        )
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    attach = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert attach.status_code == 200

    detach = await client.delete(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}",
        headers=_auth(owner["owner_jwt"]),
    )
    assert detach.status_code == 200
    body = detach.json()
    assert body["detached_at"] is not None

    async with app.state.sessionmaker() as session:
        row = await session.scalar(
            select(AgentTokenRow).where(AgentTokenRow.id == token["token_id"])
        )
        assert row is not None
        assert row.principal_id is None
        assert row.revoked_at is None  # untouched — still authenticates as a plain v39 token


# --------------------------------------------------------------------------- M4


class _FakeAuthenticator:
    def __init__(self, authz: AuthzResult) -> None:
        self._authz = authz

    async def authenticate(self, raw_key: str) -> AuthzResult:
        return self._authz


class _FakeModelChecker:
    async def is_active(self, model_id: str) -> bool:
        return True

    async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
        return ModelAccess.ACTIVE


class _FakeRedis:
    def __init__(self, value: str | None) -> None:
        self._value = value

    async def get(self, key: str) -> str | None:
        return self._value


class _FakeBudgetGuard:
    async def check(self, tenant_id: uuid.UUID) -> None:
        return None


async def test_principal_aggregate_budget_blocks_request_neither_token_alone_exceeds() -> None:
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    principal_id = uuid.uuid4()
    authz = AuthzResult(
        tenant_id=tenant_id,
        key_id=key_id,
        monthly_budget_usd=Decimal("1000.00"),  # generous per-token budget — never trips
        agent_principal_id=principal_id,
        agent_principal_budget_usd=Decimal("10.00"),  # combined spend >= this trips
    )
    governance = NonChatGovernance(
        authenticator=_FakeAuthenticator(authz),
        model_checker=_FakeModelChecker(),
        budget_guard=_FakeBudgetGuard(),
        rate_limiter=None,
        redis_client=_FakeRedis("10.00"),  # combined spend across both attached tokens
    )
    with pytest.raises(ProblemError) as exc_info:
        await governance.authorize("tok", "openai/gpt-4o-mini")
    assert exc_info.value.status == 402
    assert exc_info.value.code == "ERR_BUDGET_EXCEEDED"


async def test_principal_budget_untouched_when_unattached() -> None:
    """Control case: an unattached token (agent_principal_id=None) never consults
    the principal-budget check — byte-identical to pre-task behavior."""
    authz = AuthzResult(tenant_id=uuid.uuid4(), key_id=uuid.uuid4(), monthly_budget_usd=None)
    governance = NonChatGovernance(
        authenticator=_FakeAuthenticator(authz),
        model_checker=_FakeModelChecker(),
        budget_guard=_FakeBudgetGuard(),
        rate_limiter=None,
        redis_client=_FakeRedis("999999.00"),  # would trip ANY budget if consulted
    )
    result = await governance.authorize("tok", "openai/gpt-4o-mini")
    assert result is authz


# --------------------------------------------------------------------------- M5


async def test_list_agent_principals_any_role(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    p1 = (
        await client.post(_AGENTS, json={"name": "live-one"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    p2 = (
        await client.post(_AGENTS, json={"name": "dead-one"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    kill = await client.post(f"{_AGENTS}/{p2['id']}/kill", headers=_auth(owner["owner_jwt"]))
    assert kill.status_code == 200

    viewer_jwt = mint_role_jwt(
        app, tenant_id=owner["tenant_id"], role=Role.VIEWER, email="viewer2@agentco.io"
    )
    r = await client.get(_AGENTS, headers=_auth(viewer_jwt))
    assert r.status_code == 200
    agents_by_id = {a["id"]: a for a in r.json()["agents"]}
    assert agents_by_id[p1["id"]]["killed_at"] is None
    assert agents_by_id[p2["id"]]["killed_at"] is not None


# --------------------------------------------------------------------------- M6


async def test_last_seen_at_updates_on_attached_mint_not_on_dataplane_call(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "last-seen-bot"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    attach = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert attach.status_code == 200

    async with app.state.sessionmaker() as session:
        before = await session.scalar(
            select(AgentPrincipalRow).where(AgentPrincipalRow.id == uuid.UUID(principal["id"]))
        )
        assert before is not None
        assert before.last_seen_at is None

    # Exercise the SAME production function token_router.py schedules fire-and-forget
    # after every successful mint (see module docstring: a brand-new mint's token is
    # unattached by construction, so this directly invokes the hook for an
    # already-attached token to prove the mint-tied-to-an-attached-token wiring).
    mint_time = datetime.now(UTC)
    await bump_principal_last_seen(
        app.state.sessionmaker, token_id=token["token_id"], now=mint_time
    )

    async with app.state.sessionmaker() as session:
        after_mint = await session.scalar(
            select(AgentPrincipalRow).where(AgentPrincipalRow.id == uuid.UUID(principal["id"]))
        )
        assert after_mint is not None
        assert after_mint.last_seen_at is not None

    # A data-plane authn call (ext_authz) using the SAME attached token must NOT
    # update last_seen_at again (M6: never a synchronous write on the hot path).
    authz_call = await client.post(
        "/internal/authz", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert authz_call.status_code == 200

    async with app.state.sessionmaker() as session:
        after_dataplane = await session.scalar(
            select(AgentPrincipalRow).where(AgentPrincipalRow.id == uuid.UUID(principal["id"]))
        )
        assert after_dataplane is not None
        assert after_dataplane.last_seen_at == after_mint.last_seen_at


# --------------------------------------------------------------------------- M7/M8


async def test_kill_principal_revokes_all_attached_tokens(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "kill-3-tokens"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    tokens = [
        await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
        for _ in range(3)
    ]
    for t in tokens:
        attach = await client.post(
            f"{_AGENTS}/{principal['id']}/tokens/{t['token_id']}/attach",
            headers=_auth(owner["owner_jwt"]),
        )
        assert attach.status_code == 200

    kill = await client.post(f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"]))
    assert kill.status_code == 200
    body = kill.json()
    assert body["id"] == principal["id"]
    assert body["killed_at"] is not None

    async with app.state.sessionmaker() as session:
        for t in tokens:
            row = await session.scalar(
                select(AgentTokenRow).where(AgentTokenRow.id == t["token_id"])
            )
            assert row is not None
            assert row.revoked_at is not None
        principal_row = await session.scalar(
            select(AgentPrincipalRow).where(AgentPrincipalRow.id == uuid.UUID(principal["id"]))
        )
        assert principal_row is not None
        assert principal_row.killed_at is not None


async def test_re_killing_already_killed_principal_is_idempotent(
    client: httpx.AsyncClient, owner: dict[str, Any]
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "idempotent-kill"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    first = await client.post(
        f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"])
    )
    assert first.status_code == 200
    first_killed_at = first.json()["killed_at"]

    second = await client.post(
        f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"])
    )
    assert second.status_code == 200
    assert second.json()["killed_at"] == first_killed_at


# --------------------------------------------------------------------------- M9


async def test_killed_principal_token_rejected_identically_at_both_seams(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "both-seams-bot"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    attach = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert attach.status_code == 200

    # Both seams accept it BEFORE the kill.
    pre_kill = await client.post(
        "/internal/authz", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert pre_kill.status_code == 200

    kill = await client.post(f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"]))
    assert kill.status_code == 200

    # In-process /v1 seam (CompositeKeyAuthenticator, via /internal/authz — the SAME
    # authenticate() call both the /v1 in-process path and Envoy's ext_authz use).
    seam_1 = await client.post(
        "/internal/authz", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    # Envoy ext_authz entry point — /internal/authz/{subpath} delegates to the exact
    # same authz semantics (agent_principal_router main.py registration confirmed).
    seam_2 = await client.post(
        "/internal/authz/v1/chat/completions",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert seam_1.status_code == 401
    assert seam_2.status_code == 401
    assert seam_1.json() == seam_2.json()
    assert seam_1.json()["code"] == "ERR_AUTH_INVALID_KEY"


# --------------------------------------------------------------------------- M10


async def test_kill_does_not_retroactively_change_already_returned_admission(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    """M10: kill affects only NEW admissions, never a session already past the gate.

    No mid-stream re-authorization check exists anywhere in this codebase (TASK.md §0
    Ground note #4) — there is nothing to "abort" at the code level once a request is
    admitted, so the observable, testable half of this contract is: an admission
    already granted before the kill is never retroactively invalidated (no code path
    reaches back into an in-flight response), while the SAME token's very next NEW
    admission attempt after the kill is rejected (proven by test_killed_principal_
    token_rejected_identically_at_both_seams above, run in the SAME before/after
    sequence). This test isolates the "successful pre-kill admission is untouched"
    half explicitly.
    """
    principal = (
        await client.post(_AGENTS, json={"name": "inflight-bot"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )

    pre_kill = await client.post(
        "/internal/authz", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert pre_kill.status_code == 200
    admitted_tenant_id = pre_kill.json()["tenant_id"]

    await client.post(f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"]))

    # The EARLIER admission's response body is immutable Python data already returned
    # to this test — proving the kill cannot reach back and change it (no code path
    # exists that would even attempt to: kill only writes revoked_at columns).
    assert admitted_tenant_id == str(owner["tenant_id"])


# --------------------------------------------------------------------------- M11


async def test_kill_audited_independently_fail_open(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        calls.append("attempted")
        raise RuntimeError("audit sink unreachable")

    monkeypatch.setattr("gateway.agent_oauth.api.agent_principal_router.record_audit", _boom)

    principal = (
        await client.post(_AGENTS, json={"name": "audit-bot"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    kill = await client.post(f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"]))
    # Fail-open: the kill's own 200 response is UNAFFECTED by an audit-write failure.
    assert kill.status_code == 200
    assert kill.json()["killed_at"] is not None

    # Let the fire-and-forget task actually run and raise/swallow.
    await asyncio.sleep(0.05)
    assert calls == ["attempted"]


# --------------------------------------------------------------------------- M12


async def test_cross_tenant_kill_indistinguishable_from_unknown(
    client: httpx.AsyncClient, owner: dict[str, Any], other_tenant: dict[str, Any]
) -> None:
    principal = (
        await client.post(_AGENTS, json={"name": "tenant-a-bot"}, headers=_auth(owner["owner_jwt"]))
    ).json()

    cross = await client.post(
        f"{_AGENTS}/{principal['id']}/kill", headers=_auth(other_tenant["owner_jwt"])
    )
    unknown = await client.post(
        f"{_AGENTS}/{uuid.uuid4()}/kill", headers=_auth(other_tenant["owner_jwt"])
    )
    assert cross.status_code == 404
    assert unknown.status_code == 404
    assert cross.json() == unknown.json()
    assert cross.json()["code"] == "ERR_AGENT_PRINCIPAL_NOT_FOUND"


# --------------------------------------------------------------------------- Rejects (generic)


async def test_missing_bearer_on_admin_agents(client: httpx.AsyncClient) -> None:
    r = await client.post(_AGENTS, json={"name": "x"})
    assert r.status_code == 401


async def test_insufficient_role_create_and_kill(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    viewer_jwt = mint_role_jwt(
        app, tenant_id=owner["tenant_id"], role=Role.VIEWER, email="viewer3@agentco.io"
    )
    create = await client.post(
        _AGENTS, json={"name": "viewer-cant-create"}, headers=_auth(viewer_jwt)
    )
    assert create.status_code == 403

    principal = (
        await client.post(
            _AGENTS, json={"name": "owner-made-this"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    kill = await client.post(f"{_AGENTS}/{principal['id']}/kill", headers=_auth(viewer_jwt))
    assert kill.status_code == 403

    async with app.state.sessionmaker() as session:
        row = await session.scalar(
            select(AgentPrincipalRow).where(AgentPrincipalRow.id == uuid.UUID(principal["id"]))
        )
        assert row is not None
        assert row.killed_at is None  # no row modified by the forbidden attempt


# --------------------------------------------------------------------------- Edge: concurrency


async def test_concurrent_kill_race_exactly_one_winner(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(_AGENTS, json={"name": "race-bot"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    tokens = [
        await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
        for _ in range(2)
    ]
    for t in tokens:
        attach = await client.post(
            f"{_AGENTS}/{principal['id']}/tokens/{t['token_id']}/attach",
            headers=_auth(owner["owner_jwt"]),
        )
        assert attach.status_code == 200

    async def _kill() -> httpx.Response:
        return await client.post(
            f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"])
        )

    r1, r2 = await asyncio.gather(_kill(), _kill())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["killed_at"] == r2.json()["killed_at"]  # same winning timestamp

    # Let any fire-and-forget audit tasks settle, then confirm exactly ONE audit
    # event was recorded for this kill (not zero, not two).
    await asyncio.sleep(0.1)
    async with app.state.sessionmaker() as session:
        count = await session.scalar(
            text(
                "SELECT COUNT(*) FROM audit_events WHERE action = 'agent_principal.killed' "
                "AND target_id = :pid"
            ),
            {"pid": principal["id"]},
        )
        assert count == 1

        for t in tokens:
            row = await session.scalar(
                select(AgentTokenRow).where(AgentTokenRow.id == t["token_id"])
            )
            assert row is not None
            assert row.revoked_at is not None


async def test_kill_racing_attach_never_leaves_token_attached_to_killed_principal(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "race-attach-bot"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    fresh_token = await mint_agent_token(
        app, tenant_id=owner["tenant_id"], user_id=owner["user_id"]
    )

    async def _kill() -> httpx.Response:
        return await client.post(
            f"{_AGENTS}/{principal['id']}/kill", headers=_auth(owner["owner_jwt"])
        )

    async def _attach() -> httpx.Response:
        return await client.post(
            f"{_AGENTS}/{principal['id']}/tokens/{fresh_token['token_id']}/attach",
            headers=_auth(owner["owner_jwt"]),
        )

    kill_result, attach_result = await asyncio.gather(_kill(), _attach())
    assert kill_result.status_code == 200

    async with app.state.sessionmaker() as session:
        token_row = await session.scalar(
            select(AgentTokenRow).where(AgentTokenRow.id == fresh_token["token_id"])
        )
        assert token_row is not None
        if attach_result.status_code == 200:
            # Attach committed strictly before the kill — M9's next-request check
            # (resolve_access_token) must reject it: revoked_at is now set.
            assert token_row.principal_id == uuid.UUID(principal["id"])
            assert token_row.revoked_at is not None
        else:
            # Kill committed first — attach must have been refused, never silently
            # partially applied.
            assert attach_result.status_code == 409
            assert attach_result.json()["code"] == "ERR_AGENT_PRINCIPAL_KILLED"
            assert token_row.principal_id is None
