"""Failing-first (red) suite for agent-oauth-harness-e2e (TASK.md §4).

One test per §2 scenario. Coverage target: 90% on the edited authz wiring in
keys/api/router.py (authz + authz_subpath) and keys/api/deps.py (get_authz_authenticator).

Tests assert OBSERVABLE behavior only:
  - HTTP status codes + response bodies
  - Response headers x-tenant-id / x-key-id set for Envoy upstream forwarding
  - usage_events billing via FakeUsageRecorder (same pattern as agent-token-authn-seam)
  - Budget cap enforcement at /v1

LOAD-BEARING TEST: test_edge_authz_accepts_agent_token — this is the edge gap
(task-5 wired composite into /v1 in-process but NOT /internal/authz, so agent
tokens were rejected at ext_authz before /v1 was reached).

Rejection policy: all failures at /internal/authz → 401 AUTH_KEY_INVALID_AUTHZ
(byte-identical code="ERR_AUTH_INVALID_KEY") — anti-enumeration preserved.
No x-tenant-id / x-key-id headers on rejection.
"""

from __future__ import annotations

import datetime
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

COMPLETIONS = "/v1/chat/completions"
AUTHZ_PATH = "/internal/authz/v1/chat/completions"
AUTHZ_ROOT = "/internal/authz"

# ---------------------------------------------------------------------------
# Minimal upstream fakes (mirror agent-token-authn-seam pattern)
# ---------------------------------------------------------------------------

UPSTREAM_BODY = {
    "id": "gen-e2e-1",
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


class FakeCompletionUpstream:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else UPSTREAM_BODY
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: [DONE]\n\n"

        return _gen()


class FakeUsageRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, Any] | None,
        status: int,
        **_kwargs: Any,
    ) -> None:
        self.records.append(
            {
                "tenant_id": str(tenant_id),
                "key_id": str(key_id),
                "model": model,
                "usage": usage,
                "status": status,
            }
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, f"Expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"Expected code={code!r}, got {body}"


def chat_payload(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


# ---------------------------------------------------------------------------
# § Scenario 1 — full headless-agent journey bills the tenant (stubbed)
# ---------------------------------------------------------------------------


async def test_full_journey_bills_tenant(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
) -> None:
    """signup → device/authorize → device/approve (JWT) → /oauth/token → /v1 chat → billed.

    Asserts:
      - Chat returns 200.
      - A usage_events record exists with tenant_id=T and key_id=token_id.
    """
    # Inject fakes so no real provider key is needed
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    # 1. Signup
    signup_r = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "JourneyTenant",
            "email": "j@j.io",
            "password": "correct horse battery",
        },
    )
    assert signup_r.status_code == 201, f"signup failed: {signup_r.text}"
    tenant_id = signup_r.json()["tenant_id"]

    # 2. Login to get JWT for approve step
    login_r = await client.post(
        "/admin/auth/login",
        json={"email": "j@j.io", "password": "correct horse battery"},
    )
    assert login_r.status_code == 200, f"login failed: {login_r.text}"
    owner_jwt = login_r.json()["access_token"]

    # 3. Agent: POST /oauth/device/authorize
    auth_r = await client.post(
        "/oauth/device/authorize",
        json={"scope": "proxy"},
    )
    assert auth_r.status_code == 200, f"authorize failed: {auth_r.text}"
    auth_body = auth_r.json()
    device_code = auth_body["device_code"]
    user_code = auth_body["user_code"]

    # 4. Human: POST /oauth/device/approve (requires JWT)
    approve_r = await client.post(
        "/oauth/device/approve",
        json={"user_code": user_code, "approved": True},
        headers=auth(owner_jwt),
    )
    assert approve_r.status_code == 200, f"approve failed: {approve_r.text}"

    # 5. Agent: poll /oauth/token with device_code
    token_r = await client.post(
        "/oauth/token",
        json={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        },
    )
    assert token_r.status_code == 200, f"token failed: {token_r.text}"
    token_body = token_r.json()
    access_token = token_body["access_token"]
    # /oauth/token does NOT return the internal token_id (by design); look it up from the DB
    # so the key_id=token_id billing invariant below is a REAL assertion, not a skipped no-op.
    from sqlalchemy import text as _sql_text

    async with app.state.sessionmaker() as _session:
        _row = await _session.execute(
            _sql_text("SELECT id FROM agent_tokens WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
        token_id = str(_row.scalar_one())

    # 6. Agent: /v1/chat/completions with the minted token
    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers=auth(access_token),
    )
    assert resp.status_code == 200, f"chat failed: {resp.status_code}: {resp.text}"
    assert upstream.calls == 1

    # 7. Assert billing: usage_events row with correct tenant_id + key_id=token_id
    assert len(recorder.records) == 1, f"Expected 1 usage record, got {len(recorder.records)}"
    rec = recorder.records[0]
    assert rec["tenant_id"] == tenant_id, f"tenant_id mismatch: {rec['tenant_id']} != {tenant_id}"
    assert rec["status"] == 200
    # key_id must be the agent token's id, not the tenant/user id (now a REAL, unconditional assert)
    assert str(rec["key_id"]) == token_id, f"key_id should be token_id {token_id}, got {rec['key_id']}"


# ---------------------------------------------------------------------------
# § Scenario 2 — edge authz accepts an agent token (LOAD-BEARING TEST)
# ---------------------------------------------------------------------------


async def test_edge_authz_accepts_agent_token(
    client: httpx.AsyncClient,
    app: Any,
    agent_token: dict[str, Any],
) -> None:
    """POST /internal/authz/v1/chat/completions with agent token → 200 + x-tenant-id/x-key-id.

    This is the LOAD-BEARING test: task-5 wired CompositeKeyAuthenticator into /v1
    in-process but NOT into /internal/authz (the ext_authz gate used by Envoy for /v1).
    Without this task's fix, an agent token is rejected at the edge before /v1 is reached.
    """
    resp = await client.post(
        AUTHZ_PATH,
        headers=auth(agent_token["access_token"]),
    )

    assert resp.status_code == 200, (
        f"Expected 200 for agent token at /internal/authz, got {resp.status_code}: {resp.text}"
    )

    # x-tenant-id and x-key-id are set for Envoy ext_authz allowed_upstream_headers forwarding
    assert resp.headers.get("x-tenant-id") == str(agent_token["tenant_id"]), (
        f"x-tenant-id should be {agent_token['tenant_id']}, got {resp.headers.get('x-tenant-id')}"
    )
    assert resp.headers.get("x-key-id") == str(agent_token["token_id"]), (
        f"x-key-id should be {agent_token['token_id']}, got {resp.headers.get('x-key-id')}"
    )


# ---------------------------------------------------------------------------
# § Scenario 3 — edge authz still accepts an existing API key (regression)
# ---------------------------------------------------------------------------


async def test_edge_authz_api_key_regression(
    client: httpx.AsyncClient,
    api_key: dict[str, str],
) -> None:
    """sk- key at /internal/authz → 200 + x-key-id=api-key-id (byte-identical to before).

    Verifies the sk- path through the composite is UNCHANGED — zero regression.
    """
    resp = await client.post(
        AUTHZ_PATH,
        headers=auth(api_key["key"]),
    )

    assert resp.status_code == 200, (
        f"Expected 200 for sk- key at /internal/authz, got {resp.status_code}: {resp.text}"
    )

    # x-key-id must be the API key's id
    assert resp.headers.get("x-key-id") == api_key["key_id"], (
        f"x-key-id should be {api_key['key_id']}, got {resp.headers.get('x-key-id')}"
    )
    # x-tenant-id must match the key's tenant
    assert resp.headers.get("x-tenant-id") == api_key["tenant_id"], (
        f"x-tenant-id should be {api_key['tenant_id']}, got {resp.headers.get('x-tenant-id')}"
    )
    # The key starts with sk- (grammar dispatch confirmed)
    assert api_key["key"].startswith("sk-")


# ---------------------------------------------------------------------------
# § Scenario 4 — edge authz rejects bad/expired/revoked credential (fail-closed)
# ---------------------------------------------------------------------------


async def test_edge_authz_rejects_bad_credential(
    client: httpx.AsyncClient,
    app: Any,
) -> None:
    """Unknown/expired/revoked token at /internal/authz → 401 AUTH_KEY_INVALID_AUTHZ.

    Asserts:
      - 401 with code="ERR_AUTH_INVALID_KEY" (byte-identical for all failure modes).
      - No x-tenant-id / x-key-id header on rejection (anti-enumeration preserved).
    """
    # A random non-sk- bearer that matches no agent_tokens row
    unknown_token = secrets.token_urlsafe(32)
    assert not unknown_token.startswith("sk-")

    resp = await client.post(
        AUTHZ_PATH,
        headers=auth(unknown_token),
    )
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")

    # No identifying headers on rejection (anti-enumeration)
    assert "x-tenant-id" not in resp.headers, "x-tenant-id must NOT be set on 401"
    assert "x-key-id" not in resp.headers, "x-key-id must NOT be set on 401"


async def test_edge_authz_rejects_unknown_sk_key(
    client: httpx.AsyncClient,
) -> None:
    """An unknown sk- key at /internal/authz → 401 AUTH_KEY_INVALID_AUTHZ.

    Byte-identical 401 for both sk- and non-sk- failure modes.
    """
    fake_sk = "sk-00000000000000000000000000000000.ffffffffffffffffffffffffffffffff"
    resp = await client.post(
        AUTHZ_PATH,
        headers=auth(fake_sk),
    )
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert "x-tenant-id" not in resp.headers
    assert "x-key-id" not in resp.headers


async def test_edge_authz_rejects_revoked_agent_token(
    client: httpx.AsyncClient,
    app: Any,
    agent_token: dict[str, Any],
) -> None:
    """Revoked agent token at /internal/authz → 401 AUTH_KEY_INVALID_AUTHZ (fail-closed)."""
    from sqlalchemy import text as sqla_text

    # Directly revoke the token in the DB
    async with app.state.sessionmaker() as session:
        await session.execute(
            sqla_text("UPDATE agent_tokens SET revoked_at = now() WHERE id = :tid"),
            {"tid": agent_token["token_id"]},
        )
        await session.commit()

    resp = await client.post(
        AUTHZ_PATH,
        headers=auth(agent_token["access_token"]),
    )
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert "x-tenant-id" not in resp.headers
    assert "x-key-id" not in resp.headers


async def test_edge_authz_rejects_missing_header(
    client: httpx.AsyncClient,
) -> None:
    """No Authorization header at /internal/authz → 401 (fail-closed)."""
    resp = await client.post(AUTHZ_PATH)
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert "x-tenant-id" not in resp.headers
    assert "x-key-id" not in resp.headers


# ---------------------------------------------------------------------------
# § Scenario 5 — over-budget agent token blocked at /v1 (402)
# ---------------------------------------------------------------------------


async def test_over_budget_blocked_402(
    client: httpx.AsyncClient,
    app: Any,
    agent_token: dict[str, Any],
    active_model: str,
) -> None:
    """Agent token over monthly cap on /v1 → 402 ERR_BUDGET_EXCEEDED; no upstream call.

    Mirrors the budget test from agent-token-authn-seam §8: seeds the spend counter
    >= the default cap ($100.00) then asserts 402.
    """
    import redis.asyncio as aioredis

    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    redis_client = aioredis.from_url("redis://localhost:6380/9")
    try:
        # Per-key guard keys on usage:spend:key:{key_id}:{YYYYMM}
        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        spend_key = f"usage:spend:key:{agent_token['token_id']}:{yyyymm}"
        # Seed spend >= default cap ($100.00)
        await redis_client.set(spend_key, b"100.00")

        # Wire the real budget guard so the per-key check fires
        guard = RedisBudgetGuard(
            redis=redis_client,
            session_factory=app.state.sessionmaker,
        )
        app.state.budget_guard = guard

        upstream = FakeCompletionUpstream()
        app.state.completion_upstream = upstream

        resp = await client.post(
            COMPLETIONS,
            json=chat_payload(active_model),
            headers=auth(agent_token["access_token"]),
        )

        assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
        assert upstream.calls == 0, "No upstream call should be made when budget exceeded"
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# § Edge authz root path (no subpath)
# ---------------------------------------------------------------------------


async def test_edge_authz_root_accepts_agent_token(
    client: httpx.AsyncClient,
    agent_token: dict[str, Any],
) -> None:
    """POST /internal/authz (no subpath) also accepts agent token → 200 + headers."""
    resp = await client.post(
        AUTHZ_ROOT,
        headers=auth(agent_token["access_token"]),
    )
    assert resp.status_code == 200, (
        f"Expected 200 for agent token at /internal/authz, got {resp.status_code}: {resp.text}"
    )
    assert resp.headers.get("x-tenant-id") == str(agent_token["tenant_id"])
    assert resp.headers.get("x-key-id") == str(agent_token["token_id"])
