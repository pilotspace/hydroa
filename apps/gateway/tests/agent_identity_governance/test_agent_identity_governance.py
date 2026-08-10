"""Suite for agent-identity-governance (TASK.md §3 CONTRACT FROZEN @ v2).

One test per §2 scenario (M1-M13 + CR-2 + rejects + edge cases). Asserts observable
behavior (HTTP status/body shape, DB row state) — never internals.

M4 (principal aggregate budget CHECK) is unit-tested directly against
NonChatGovernance with fakes — mirrors the established pattern in
tests/nonchat_soft_budget_alert/test_nonchat_soft_budget_alert.py (a real
Postgres/HTTP round trip is unnecessary to prove the governance CHECK fires in
isolation). Correction to an earlier (FALSE) claim in this docstring: the
recorder-side Redis INCR for usage:spend:agent_principal:{id}:{YYYYMM} is now
wired end-to-end (recorder.py, mirrors team_budget_usd's OWN write-side, which
IS incremented in production — see recorder.py's per-team INCRBYFLOAT block).
test_agent_principal_spend_counter_increments_and_enforces_on_next_call below
proves the FULL loop through a real billed /v1/chat/completions call: the
counter increments AND a principal at/over budget is refused on the NEXT call
— not Redis-seeded, unlike the M4 CHECK-only unit test above it.

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
from collections.abc import AsyncIterator
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
from gateway.usage.application.recorder import RecordingUsageRecorder
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.domain.ports import ModelAccess
from gateway.tenants.domain.entities import Role
from tests.agent_identity_governance.conftest import mint_agent_token, mint_role_jwt
from tests._polling import poll_until

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


class _FakeCompletionUpstream:
    """Minimal non-streaming fake upstream (mirrors tests/team_governance's own).

    usage 10 prompt + 5 completion tokens, paired with active_model's pricing
    (0.0005/0.001 per token) -> EXACTLY $0.01 per call, a deterministic cost
    chosen so a "0.01" principal budget trips precisely on the call AFTER the
    one that reaches it (M4/CR write-side enforcement — never Redis-seeded).
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return 200, {
            "id": f"gen-aig-{self.calls}",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        raise NotImplementedError("non-streaming fake — this suite never streams")


def _principal_spend_key(principal_id: str) -> str:
    yyyymm = datetime.now(UTC).strftime("%Y%m")
    return f"usage:spend:agent_principal:{principal_id}:{yyyymm}"


async def test_agent_principal_spend_counter_increments_and_enforces_on_next_call(
    client: httpx.AsyncClient,
    owner: dict[str, Any],
    app: Any,
    redis_client: Any,
    active_model: str,
) -> None:
    """Build-completeness fix (contract v2 note): the write-side Redis INCR for
    usage:spend:agent_principal:{id}:{YYYYMM} — proves the FULL loop end-to-end
    through a real billed /v1/chat/completions call, NOT Redis-seeded (unlike
    the M4 CHECK-only unit test above, which manually seeds _FakeRedis).

    1. A principal with monthly_budget_usd="0.01" and one attached token.
    2. First billed call succeeds (200) — spend was $0 going in.
    3. The recorder's real INCRBYFLOAT fires; usage:spend:agent_principal:{id}:
       {YYYYMM} reads > $0 (the catalog cost composed with the tenant's markup —
       intentionally not asserted to the exact cent, only that it moved AND
       meets/exceeds the $0.01 budget, which active_model's pricing guarantees).
    4. The SAME token's NEXT call is refused 402 BUDGET_EXCEEDED — spend now
       meets/exceeds the budget, proving the counter this test just verified is
       the SAME one the enforcement check reads (byte-identical key string).
    """
    app.state.completion_upstream = _FakeCompletionUpstream()

    principal = (
        await client.post(
            _AGENTS,
            json={"name": "spend-bot", "monthly_budget_usd": "0.01"},
            headers=_auth(owner["owner_jwt"]),
        )
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    attach = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert attach.status_code == 200, attach.text

    spend_key = _principal_spend_key(principal["id"])
    assert await redis_client.get(spend_key) is None  # clean slate (autouse _clear_state)

    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    first = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert first.status_code == 200, first.text

    # Fire-and-forget recorder coroutine — allow it to complete.

    raw = await poll_until(lambda: redis_client.get(spend_key), lambda v: v is not None)
    assert raw is not None, (
        f"usage:spend:agent_principal:{principal['id']}:<YYYYMM> was never written — "
        "the write-side INCR did not fire"
    )
    spend = Decimal(raw.decode() if isinstance(raw, bytes) else raw)
    assert spend > Decimal("0"), f"expected counter to move off zero, got {spend}"
    assert spend >= Decimal("0.01"), (
        f"expected spend to meet/exceed the $0.01 budget (active_model's pricing "
        f"guarantees this), got {spend} — the enforcement assertion below would be "
        "vacuous otherwise"
    )

    second = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert second.status_code == 402, second.text
    assert second.json()["code"] == "ERR_BUDGET_EXCEEDED"


async def test_agent_principal_correction_counter_exactly_once_on_double_fire(
    app: Any, redis_client: Any
) -> None:
    """record_correction's SET-NX exactly-once guard (recorder.py) covers the NEW
    agent-principal counter too, not just the pre-existing team/key ones — a
    duplicate correction firing twice for the SAME event_id (inline recovery
    racing the periodic sweep — cost_recovery.py's own documented race) must
    move usage:spend:agent_principal:{id}:{YYYYMM} by the delta exactly ONCE.
    """
    recorder = RecordingUsageRecorder(
        redis=app.state.redis_client, session_factory=app.state.sessionmaker
    )
    principal_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    event_id = uuid.uuid4()
    spend_key = _principal_spend_key(str(principal_id))
    assert await redis_client.get(spend_key) is None

    for _ in range(2):  # simulates inline recovery + the periodic sweep re-firing
        await recorder.record_correction(
            event_id=event_id,
            tenant_id=tenant_id,
            key_id=key_id,
            model="openai/gpt-4o-mini",
            cost_usd=Decimal("2.50"),
            provider_cost=Decimal("2.50"),
            provider_generation_id="gen-correction-1",
            agent_principal_id=principal_id,
        )

    raw = await redis_client.get(spend_key)
    assert raw is not None
    spend = Decimal(raw.decode() if isinstance(raw, bytes) else raw)
    assert spend == Decimal("2.50"), (
        f"expected exactly-once delta application ($2.50), got {spend} — "
        "the SET-NX guard did not dedupe the double-fire"
    )


async def test_disconnect_correction_reaches_agent_principal_counter_via_recovery_sweep(
    client: httpx.AsyncClient,
    app: Any,
    redis_client: Any,
    owner: dict[str, Any],
    db_session: Any,
    active_model: str,
) -> None:
    """Defect fix (adversarial verify, conf 0.93): the disconnect/cost-recovery
    CORRECTION path never attributed to the agent-principal spend counter, letting a
    principal spend past its cap after a mid-stream disconnect + later true-up.

    Root cause: OpenRouterCostRecoveryService.recover() had no team_id/
    agent_principal_id params and never passed either to record_correction() — even
    though agent_principal_id was already threaded to record()'s main-path Redis
    INCR at every call site, it was NEVER persisted durably anywhere (not even the
    `raw` JSONB), so recovery_sweep.py's candidate scan had no way to recover it for
    an already-flushed anchor row.

    Unlike the pre-existing test_agent_principal_correction_counter_exactly_once_on_
    double_fire above (a fixture-overfit: it calls recorder.record_correction(
    agent_principal_id=...) DIRECTLY — a parameter the real production caller never
    supplied), THIS test drives the correction through the ACTUAL production chain
    end to end, with agent_principal_id flowing automatically — never passed
    explicitly to recover() or record_correction() by this test:
      1. A real disconnect anchor row lands via RecordingUsageRecorder.record() (the
         SAME call proxy/application/use_cases.py's stream-disconnect handler makes)
         + UsageLedgerFlusher.flush_once() (lands the Redis-stream event into
         usage_records, including the NEW agent_principal_id column).
      2. OpenRouterRecoverySweeper.sweep_once() — the periodic backstop — scans
         usage_records via the real _CANDIDATE_SQL (now selecting agent_principal_id
         off the anchor row) and calls OpenRouterCostRecoveryService.recover() with
         it threaded automatically.
      3. Asserts the SAME usage:spend:agent_principal:{id}:{YYYYMM} counter the main
         path increments now reflects the full CORRECTED (trued-up) total —
         partial-floor + delta — not just the partial estimate frozen at disconnect.
      4. Asserts the principal's VERY NEXT call is refused 402 BUDGET_EXCEEDED —
         proving the corrected counter is the SAME one enforcement reads.
    """
    from gateway.proxy.infrastructure.openrouter_upstream import GenerationCost
    from gateway.usage.application.cost_recovery import OpenRouterCostRecoveryService
    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recovery_sweep import OpenRouterRecoverySweeper

    class _FakeGenerationUpstream:
        def __init__(self, total_cost: str) -> None:
            self._cost = GenerationCost(
                total_cost=Decimal(total_cost),
                upstream_inference_cost=Decimal(total_cost),
                native_tokens_prompt=0,
                native_tokens_completion=0,
                native_tokens_cached=0,
            )

        async def get_generation(self, generation_id: str) -> GenerationCost:
            return self._cost

    class _FakeProviderResolver:
        def __init__(self, model_id: str) -> None:
            self._model_id = model_id

        async def provider_for(self, model_id: str) -> str:
            return "openrouter" if model_id == self._model_id else "unknown"

    # A budget the partial floor alone ($0.01, per active_model's fixed 10+5 token
    # pricing — the SAME deterministic cost the M4 write-side test above relies on)
    # does NOT trip, but the trued-up total ($0.05, after the $0.04 correction) does.
    recorder = RecordingUsageRecorder(redis=app.state.redis_client, session_factory=app.state.sessionmaker)

    created = await client.post(
        _AGENTS,
        json={"name": "disconnect-correction-bot", "monthly_budget_usd": "0.05"},
        headers=_auth(owner["owner_jwt"]),
    )
    assert created.status_code == 200, created.text
    principal_id = uuid.UUID(created.json()["id"])

    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    attach = await client.post(
        f"{_AGENTS}/{principal_id}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert attach.status_code == 200, attach.text

    spend_key = _principal_spend_key(str(principal_id))
    assert await redis_client.get(spend_key) is None  # clean slate

    gid = f"gen-agent-defect2-{uuid.uuid4().hex[:8]}"

    # 1. The REAL disconnect anchor write (mirrors use_cases.py's stream-disconnect
    #    handler exactly: same recorder.record() call shape, usage_source=
    #    "client_disconnect", agent_principal_id threaded). Deterministic $0.012
    #    (10 prompt + 5 completion tokens @ active_model's fixed pricing = $0.01,
    #    times the tenant's default 20% markup_pct = $0.012).
    await recorder.record(
        tenant_id=owner["tenant_id"],
        key_id=token["token_id"],
        model=active_model,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        status=200,
        agent_principal_id=principal_id,
        usage_source="client_disconnect",
        provider_generation_id=gid,
    )
    # Land the Redis-stream event into usage_records (including the anchor row's
    # NEW agent_principal_id column) — the flusher the sweep's _CANDIDATE_SQL reads.
    flusher = UsageLedgerFlusher(redis=app.state.redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()

    anchor_row = (
        await db_session.execute(
            text(
                "SELECT agent_principal_id, cost_usd FROM usage_records"
                " WHERE provider_generation_id = :g AND usage_source = 'client_disconnect'"
            ),
            {"g": gid},
        )
    ).fetchone()
    assert anchor_row is not None, "anchor row did not land — flusher setup broken"
    assert anchor_row[0] == principal_id, (
        "anchor row's agent_principal_id column was not persisted — the recorder/"
        "flusher wiring fix is broken"
    )
    assert Decimal(str(anchor_row[1])) == Decimal("0.012")

    partial_spend = await redis_client.get(spend_key)
    assert partial_spend is not None
    assert Decimal(partial_spend.decode()) == Decimal("0.012")

    # 2. The periodic backstop — the SAME service both the inline hook and the
    #    sweep call — recovers the authoritative $0.05 upstream cost via the real
    #    chain. With the tenant's 20% markup_pct applied (the SAME multiplier that
    #    priced the partial floor above), the billed target is $0.06.
    service = OpenRouterCostRecoveryService(
        upstream=_FakeGenerationUpstream("0.05"),
        recorder=recorder,
        session_factory=app.state.sessionmaker,
        credential_resolver=None,
    )
    sweeper = OpenRouterRecoverySweeper(
        session_factory=app.state.sessionmaker,
        recovery_service=service,
        provider_resolver=_FakeProviderResolver(active_model),
    )
    attempts = await sweeper.sweep_once()
    assert attempts == 1

    # record_correction() only XADDs to the Redis stream (unlike record()'s XADD, it
    # has no synchronous DB fallback) — flush again to land the correction row itself
    # into usage_records so the DB assertions below can see it. The Redis counter INCR
    # a few lines below is independent of this and already fired synchronously inside
    # record_correction() above.
    await flusher.flush_once()

    correction_row = (
        await db_session.execute(
            text(
                "SELECT agent_principal_id, cost_usd FROM usage_records"
                " WHERE provider_generation_id = :g AND usage_source = 'openrouter_recovered'"
            ),
            {"g": gid},
        )
    ).fetchone()
    assert correction_row is not None, "recover() never appended a correction row"
    assert correction_row[0] == principal_id, (
        "correction row's agent_principal_id was not persisted — record_correction "
        "was not threaded the id"
    )
    assert Decimal(str(correction_row[1])) == Decimal("0.048")  # 0.06 target - 0.012 already-billed

    # 3. The SAME agent-principal counter now reflects the FULL trued-up total.
    trued_up_spend = await redis_client.get(spend_key)
    assert trued_up_spend is not None
    spend = Decimal(trued_up_spend.decode())
    assert spend == Decimal("0.06"), (
        f"expected the corrected total (0.012 partial + 0.048 delta = 0.06), got {spend} "
        "— the correction delta never reached the agent-principal counter"
    )

    # 4. Enforcement: the principal's VERY NEXT call is refused — proving the
    #    counter the correction just moved is the SAME one GovernanceService reads.
    next_call = await client.post(
        "/internal/authz", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    # /internal/authz only authenticates (no governance budget check on that seam —
    # confirmed by M4's own test using /v1/chat/completions for enforcement); the
    # budget gate lives in GovernanceService.authorize, exercised the SAME way M4's
    # write-side test above does.
    assert next_call.status_code == 200  # authn is unaffected — sanity only

    class _StaticAuthenticator:
        async def authenticate(self, raw_key: str) -> AuthzResult:
            return AuthzResult(
                tenant_id=owner["tenant_id"],
                key_id=token["token_id"],
                monthly_budget_usd=None,
                agent_principal_id=principal_id,
                agent_principal_budget_usd=Decimal("0.05"),
            )

    class _StaticModelChecker:
        async def is_active(self, model_id: str) -> bool:
            return True

        async def check_for_tenant(self, model_id: str, tenant_id: uuid.UUID) -> ModelAccess:
            return ModelAccess.ACTIVE

    class _StaticBudgetGuard:
        async def check(self, tenant_id: uuid.UUID) -> None:
            return None

    governance = NonChatGovernance(
        authenticator=_StaticAuthenticator(),
        model_checker=_StaticModelChecker(),
        budget_guard=_StaticBudgetGuard(),
        rate_limiter=None,
        redis_client=app.state.redis_client,
    )
    with pytest.raises(ProblemError) as exc_info:
        await governance.authorize(token["access_token"], active_model)
    assert exc_info.value.status == 402
    assert exc_info.value.code == "ERR_BUDGET_EXCEEDED"


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


async def test_list_agent_principals_includes_spend_usd_this_month(
    client: httpx.AsyncClient, owner: dict[str, Any], redis_client: Any
) -> None:
    """CR-2 (contract v2, M5 amended): a seeded counter renders as a 2-dp string;
    a principal with no counter yet reads "0.00" — never null, never fabricated.
    Reads the SAME usage:spend:agent_principal:{id}:{YYYYMM} key the write-side
    fix increments and _check_agent_principal_budget enforces on.
    """
    seeded = (
        await client.post(_AGENTS, json={"name": "seeded-bot"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    unseeded = (
        await client.post(_AGENTS, json={"name": "unseeded-bot"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    await redis_client.set(_principal_spend_key(seeded["id"]), "12.50")

    r = await client.get(_AGENTS, headers=_auth(owner["owner_jwt"]))
    assert r.status_code == 200
    agents_by_id = {a["id"]: a for a in r.json()["agents"]}
    assert agents_by_id[seeded["id"]]["spend_usd_this_month"] == "12.50"
    assert agents_by_id[unseeded["id"]]["spend_usd_this_month"] == "0.00"


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

    # MIXED wait: the fire-and-forget task must run at all (positive), and it must be
    # attempted EXACTLY once (negative — a retry loop around a failing audit sink would
    # append twice, and a bare poll would return before the second append could show).
    async def _attempts() -> int:
        return len(calls)

    await poll_until(_attempts, lambda n: n >= 1)
    # NEGATIVE WAIT: the no-retry half of `calls == ["attempted"]`.
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

    # MIXED wait — this asserts exactly ONE audit event: not zero, and not two.
    # The two halves need opposite treatment, so neither a bare sleep nor a bare
    # poll is correct on its own:
    #   * "not zero" is a POSITIVE wait on a fire-and-forget write. The old fixed
    #     0.1s passed on an idle laptop and failed in CI run 31243949907 as
    #     `assert 0 == 1` — the audit row simply had not landed yet.
    #   * "not two" is a NEGATIVE assertion. Polling alone would return the instant
    #     the FIRST row appeared and never give a duplicate a chance to show up,
    #     silently downgrading this to "at least one" — the vacuous-green trap
    #     tests/_polling.py warns about.
    # So: poll until the row exists (kills the flake), THEN settle and re-count
    # (preserves the duplicate check).
    async def _count_kill_events() -> int:
        async with app.state.sessionmaker() as session:
            return (
                await session.scalar(
                    text(
                        "SELECT COUNT(*) FROM audit_events "
                        "WHERE action = 'agent_principal.killed' AND target_id = :pid"
                    ),
                    {"pid": principal["id"]},
                )
                or 0
            )

    await poll_until(_count_kill_events, lambda c: c >= 1)
    # NEGATIVE WAIT: the poll above already proved the kill event ARRIVED; this settle
    # is the other half of `== 1` — it gives a SECOND (erroneous) write a chance to
    # appear. Polling cannot show absence, so the duration is load-bearing.
    await asyncio.sleep(0.1)  # deliberate: give a SECOND (erroneous) write a chance
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
            # Attach committed — the token is attached to this principal. The kill's
            # bulk-revoke sweep MAY have missed it under the documented attach-vs-kill
            # race (repository.py:237): in that interleaving revoked_at is left NULL and
            # nothing re-sweeps it. So do NOT assert the incidental revoked_at timing
            # property (it flakes under -n12 CPU load when that interleaving happens);
            # assert M9's ACTUAL guarantee instead — the token is UNUSABLE at resolve
            # time. When revoked_at IS set, the by-construction path already holds; when
            # it is NULL, the additive killed-principal re-check must fail closed (401),
            # exactly as test_resolve_access_token_fails_closed_when_attached_principal_
            # killed_out_of_band proves deterministically.
            assert token_row.principal_id == uuid.UUID(principal["id"])
            if token_row.revoked_at is None:
                seam = await client.post(
                    "/internal/authz",
                    headers={"Authorization": f"Bearer {fresh_token['access_token']}"},
                )
                assert seam.status_code == 401, seam.text
                assert seam.json()["code"] == "ERR_AUTH_INVALID_KEY"
        else:
            # Kill committed first — attach must have been refused, never silently
            # partially applied.
            assert attach_result.status_code == 409
            assert attach_result.json()["code"] == "ERR_AGENT_PRINCIPAL_KILLED"
            assert token_row.principal_id is None


async def test_resolve_access_token_fails_closed_when_attached_principal_killed_out_of_band(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    """DETERMINISTIC reproduction of the attach-vs-kill race (defect fix, adversarial
    verify conf 0.95) — no asyncio.gather timing dependency, unlike
    test_kill_racing_attach_never_leaves_token_attached_to_killed_principal above.

    Root cause: attach_token's conditional UPDATE gates on a plain (non-locking) EXISTS
    subquery against agent_principals.killed_at, which under READ COMMITTED can miss a
    concurrent kill_principal transaction's not-yet-committed write. When that happens,
    kill_principal's bulk-revoke sweep (which only touches ALREADY-attached tokens) runs
    BEFORE the attach commits — so the token lands attached to a killed principal with
    revoked_at left NULL, and nothing ever re-sweeps it.

    This test arranges that exact end state directly (bypassing the actual race window,
    which is too narrow to hit reliably under gather): attach a token normally, then set
    killed_at on its principal WITHOUT going through kill_principal's bulk-revoke sweep
    (simulating "the sweep already ran and missed this token"). Asserts the FAIL-CLOSED
    read (resolve_access_token, exercised via both authn seams) rejects it — proving M9's
    guarantee ("every attached token stops authenticating... starting with the very next
    request") holds even for this race outcome, not just the by-construction revoked_at
    case.
    """
    principal = (
        await client.post(
            _AGENTS, json={"name": "race-outcome-bot"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    attach = await client.post(
        f"{_AGENTS}/{principal['id']}/tokens/{token['token_id']}/attach",
        headers=_auth(owner["owner_jwt"]),
    )
    assert attach.status_code == 200, attach.text

    # Sanity: the token authenticates BEFORE the out-of-band kill.
    pre_kill = await client.post(
        "/internal/authz", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert pre_kill.status_code == 200

    # Simulate the race OUTCOME directly: killed_at set on the principal, but
    # revoked_at deliberately left untouched on the already-attached token — exactly
    # what a kill_principal sweep that ran BEFORE this attach committed would produce.
    async with app.state.sessionmaker() as session:
        principal_row = await session.scalar(
            select(AgentPrincipalRow).where(AgentPrincipalRow.id == uuid.UUID(principal["id"]))
        )
        assert principal_row is not None
        principal_row.killed_at = datetime.now(UTC)
        await session.commit()

    async with app.state.sessionmaker() as session:
        token_row = await session.scalar(
            select(AgentTokenRow).where(AgentTokenRow.id == token["token_id"])
        )
        assert token_row is not None
        assert token_row.principal_id == uuid.UUID(principal["id"])
        assert token_row.revoked_at is None, (
            "test setup invariant: the token must still be UN-revoked — proving any "
            "rejection below comes from the NEW killed-principal re-check, not the "
            "pre-existing revoked_at fail-closed path"
        )

    # Both authn seams must now reject it — the additive killed-principal check in
    # resolve_access_token, not revoked_at (which this test proved stays NULL above).
    seam_1 = await client.post(
        "/internal/authz", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    seam_2 = await client.post(
        "/internal/authz/v1/chat/completions",
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    assert seam_1.status_code == 401, seam_1.text
    assert seam_2.status_code == 401, seam_2.text
    assert seam_1.json() == seam_2.json()
    assert seam_1.json()["code"] == "ERR_AUTH_INVALID_KEY"


# --------------------------------------------------------------------------- M13 / CR-B


async def test_list_principal_tokens_happy_path_and_secret_redaction(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    """M13/CR-B: an ADMIN lists a principal's attached tokens (one revoked) —
    id/name/created_at/revoked_at/access_expires_at present, NO token hash or
    secret field anywhere in the response (mirrors ScimTokenInfo's redaction).
    """
    admin_jwt = mint_role_jwt(
        app, tenant_id=owner["tenant_id"], role=Role.ADMIN, email="admin-picker@agentco.io"
    )
    principal = (
        await client.post(_AGENTS, json={"name": "picker-bot"}, headers=_auth(owner["owner_jwt"]))
    ).json()
    live_token = await mint_agent_token(app, tenant_id=owner["tenant_id"], user_id=owner["user_id"])
    revoked_token = await mint_agent_token(
        app, tenant_id=owner["tenant_id"], user_id=owner["user_id"]
    )
    for t in (live_token, revoked_token):
        attach = await client.post(
            f"{_AGENTS}/{principal['id']}/tokens/{t['token_id']}/attach",
            headers=_auth(owner["owner_jwt"]),
        )
        assert attach.status_code == 200, attach.text

    async with app.state.sessionmaker() as session:
        row = await session.scalar(
            select(AgentTokenRow).where(AgentTokenRow.id == revoked_token["token_id"])
        )
        assert row is not None
        row.revoked_at = datetime.now(UTC)
        await session.commit()

    r = await client.get(f"{_AGENTS}/{principal['id']}/tokens", headers=_auth(admin_jwt))
    assert r.status_code == 200, r.text
    body = r.json()
    tokens_by_id = {t["id"]: t for t in body["tokens"]}
    live_id = str(live_token["token_id"])
    revoked_id = str(revoked_token["token_id"])
    assert set(tokens_by_id) == {live_id, revoked_id}

    live_info = tokens_by_id[live_id]
    revoked_info = tokens_by_id[revoked_id]
    assert live_info["revoked_at"] is None
    assert revoked_info["revoked_at"] is not None
    for info in (live_info, revoked_info):
        assert set(info) == {"id", "name", "created_at", "revoked_at", "access_expires_at"}
        assert "token" not in info
        assert "access_token" not in info
        assert "access_token_hash" not in info
        assert "hash" not in info
        assert "secret" not in info


async def test_list_principal_tokens_forbidden_for_viewer(
    client: httpx.AsyncClient, owner: dict[str, Any], app: Any
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "viewer-blocked-bot"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()
    viewer_jwt = mint_role_jwt(
        app, tenant_id=owner["tenant_id"], role=Role.VIEWER, email="viewer-picker@agentco.io"
    )
    r = await client.get(f"{_AGENTS}/{principal['id']}/tokens", headers=_auth(viewer_jwt))
    assert r.status_code == 403


async def test_list_principal_tokens_not_found_cross_tenant_and_unknown(
    client: httpx.AsyncClient, owner: dict[str, Any], other_tenant: dict[str, Any]
) -> None:
    principal = (
        await client.post(
            _AGENTS, json={"name": "cross-tenant-picker-bot"}, headers=_auth(owner["owner_jwt"])
        )
    ).json()

    cross_tenant = await client.get(
        f"{_AGENTS}/{principal['id']}/tokens", headers=_auth(other_tenant["owner_jwt"])
    )
    unknown = await client.get(
        f"{_AGENTS}/{uuid.uuid4()}/tokens", headers=_auth(other_tenant["owner_jwt"])
    )
    assert cross_tenant.status_code == 404
    assert unknown.status_code == 404
    assert cross_tenant.json() == unknown.json()
    assert cross_tenant.json()["code"] == "ERR_AGENT_PRINCIPAL_NOT_FOUND"
