"""Failing-first (red) suite for agent-token-authn-seam (TASK.md §4).

One test per §2 scenario + unit dispatch test.
Coverage target: 90% of CompositeKeyAuthenticator.

Tests assert OBSERVABLE behavior:
  - HTTP status + problem+json code
  - usage_events rows (tenant_id + key_id=token_id)
  - "no upstream call / no usage row" on rejection
  - regression: existing sk- API keys are byte-identical

All rejections are fail-closed → 401 ERR_AUTH_INVALID_KEY (anti-enumeration).
"""

from __future__ import annotations

import datetime
import secrets
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

COMPLETIONS = "/v1/chat/completions"
EMBEDDINGS = "/v1/embeddings"

# ---------------------------------------------------------------------------
# Minimal upstream fakes
# ---------------------------------------------------------------------------

UPSTREAM_BODY = {
    "id": "gen-authn-1",
    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

EMBED_BODY = {
    "object": "list",
    "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}],
    "model": "openai/text-embedding-3-small",
    "usage": {"prompt_tokens": 5, "total_tokens": 5},
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


class FakeEmbeddingProvider:
    """A minimal UpstreamProvider fake for embeddings."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else EMBED_BODY
        self.calls = 0

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    async def post_multipart(
        self, path: str, files: dict[str, Any], data: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:  # pragma: no cover
        return self.status, self.body

    def stream_bytes(
        self, path: str, payload: dict[str, Any]
    ) -> AsyncIterator[bytes]:  # pragma: no cover
        async def _gen() -> AsyncIterator[bytes]:
            yield b""

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
    assert resp.json()["code"] == code, f"Expected code={code}, got {resp.json()}"


def chat_payload(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": "hello"}]}


def embed_payload(model: str) -> dict[str, Any]:
    return {"model": model, "input": "hello world"}


# ---------------------------------------------------------------------------
# § Scenario 1 — live agent token authenticates /v1/chat and bills the tenant
# ---------------------------------------------------------------------------


async def test_agent_token_authenticates_v1_chat_and_bills(
    client: httpx.AsyncClient,
    app: Any,
    agent_token: dict[str, Any],
    active_model: str,
) -> None:
    """Approved+minted agent token → 200; usage_events row with tenant_id + key_id=token_id."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers=auth(agent_token["access_token"]),
    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1

    # Billing: exactly one record with correct tenant_id + key_id=token_id
    assert len(recorder.records) == 1
    rec = recorder.records[0]
    assert rec["tenant_id"] == str(agent_token["tenant_id"])
    assert rec["key_id"] == str(agent_token["token_id"])
    assert rec["status"] == 200


# ---------------------------------------------------------------------------
# § Scenario 2 — existing API key is unaffected (regression guard)
# ---------------------------------------------------------------------------


async def test_existing_api_key_unaffected(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """sk- key still authenticates byte-identical; agent path never consulted."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers=auth(api_key["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1

    # key_id must be the API key's id, NOT any agent token id
    assert len(recorder.records) == 1
    rec = recorder.records[0]
    assert rec["tenant_id"] == api_key["tenant_id"]
    assert rec["key_id"] == api_key["key_id"]
    # Confirm the key starts with sk- (verifies grammar dispatch path)
    assert api_key["key"].startswith("sk-")


# ---------------------------------------------------------------------------
# § Scenario 3 — unknown agent token is rejected fail-closed (no upstream, no row)
# ---------------------------------------------------------------------------


async def test_unknown_agent_token_401(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
) -> None:
    """Random non-sk- bearer → 401 ERR_AUTH_INVALID_KEY; no upstream; no usage row."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    # A random token that matches no agent_tokens row
    unknown_token = secrets.token_urlsafe(32)
    assert not unknown_token.startswith("sk-")

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers=auth(unknown_token),
    )

    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert upstream.calls == 0
    assert len(recorder.records) == 0


# ---------------------------------------------------------------------------
# § Scenario 4 — expired agent token is rejected fail-closed
# ---------------------------------------------------------------------------


async def test_expired_agent_token_401(
    client: httpx.AsyncClient,
    app: Any,
    tenant_user: dict[str, uuid.UUID],
    active_model: str,
) -> None:
    """Token with access_expires_at in the past → resolve returns None → 401."""
    from tests.agent_token_authn_seam.conftest import mint_agent_token

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    # Mint token that expired 1 second ago
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
    access_token, _ = await mint_agent_token(
        app,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
        now=past,
        access_ttl_seconds=1,  # expires 1 second after `past` → well in the past
    )

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers=auth(access_token),
    )

    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert upstream.calls == 0
    assert len(recorder.records) == 0


# ---------------------------------------------------------------------------
# § Scenario 5 — revoked agent token is rejected fail-closed
# ---------------------------------------------------------------------------


async def test_revoked_agent_token_401(
    client: httpx.AsyncClient,
    app: Any,
    agent_token: dict[str, Any],
    active_model: str,
) -> None:
    """Token with revoked_at set → resolve returns None → 401."""
    from sqlalchemy import text as sqla_text

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    # Directly revoke the token in the DB
    async with app.state.sessionmaker() as session:
        await session.execute(
            sqla_text("UPDATE agent_tokens SET revoked_at = now() WHERE id = :tid"),
            {"tid": agent_token["token_id"]},
        )
        await session.commit()

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers=auth(agent_token["access_token"]),
    )

    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert upstream.calls == 0
    assert len(recorder.records) == 0


# ---------------------------------------------------------------------------
# § Scenario 6 — missing Bearer token is rejected
# ---------------------------------------------------------------------------


async def test_missing_bearer_401(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
) -> None:
    """No Authorization header → 401 ERR_AUTH_INVALID_KEY (unchanged behavior)."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(COMPLETIONS, json=chat_payload(active_model))

    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert upstream.calls == 0


# ---------------------------------------------------------------------------
# § Scenario 7 — non-chat path (/v1/embeddings) accepts agent token
# ---------------------------------------------------------------------------


async def test_embeddings_accepts_agent_token(
    client: httpx.AsyncClient,
    app: Any,
    agent_token: dict[str, Any],
    active_embed_model: str,
) -> None:
    """Live agent token on /v1/embeddings → authenticates past the auth gate (not 401).
    A bad/unknown token on /v1/embeddings → 401 fail-closed.

    The test patches select_provider in the embeddings_use_case module (where it is
    imported-as a local name) to return a FakeEmbeddingProvider, allowing the full
    request to complete as 200 without a real upstream.
    """
    import gateway.proxy.application.embeddings_use_case as _emu

    fake_provider = FakeEmbeddingProvider()
    original_select = _emu.select_provider

    def _patched_select(modality: str, provider: str, registry: Any) -> Any:
        return fake_provider

    _emu.select_provider = _patched_select  # type: ignore[assignment]

    try:
        recorder = FakeUsageRecorder()
        app.state.usage_recorder = recorder

        resp = await client.post(
            EMBEDDINGS,
            json=embed_payload(active_embed_model),
            headers=auth(agent_token["access_token"]),
        )
        # Auth passed → not 401; provider returns 200 → 200 from endpoint
        assert resp.status_code != 401, f"Embeddings path rejected valid agent token: {resp.text}"
        assert resp.status_code == 200, f"Unexpected status: {resp.status_code} {resp.text}"

        # Usage recorded with correct tenant + key_id=token_id
        assert len(recorder.records) >= 1
        rec = recorder.records[-1]
        assert rec["tenant_id"] == str(agent_token["tenant_id"])
        assert rec["key_id"] == str(agent_token["token_id"])

        # Unknown token on embeddings path → 401 fail-closed
        unknown_token = secrets.token_urlsafe(32)
        assert not unknown_token.startswith("sk-")
        bad_resp = await client.post(
            EMBEDDINGS,
            json=embed_payload(active_embed_model),
            headers=auth(unknown_token),
        )
        assert_problem(bad_resp, 401, "ERR_AUTH_INVALID_KEY")
    finally:
        _emu.select_provider = original_select  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# § Scenario 8 — over-budget agent token gets 402 ERR_BUDGET_EXCEEDED
# ---------------------------------------------------------------------------


async def test_agent_token_over_budget_402(
    client: httpx.AsyncClient,
    app: Any,
    agent_token: dict[str, Any],
    active_model: str,
) -> None:
    """seed spend >= cap → 402 ERR_BUDGET_EXCEEDED; no upstream call."""
    import redis.asyncio as aioredis

    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    redis_client = aioredis.from_url("redis://localhost:6380/9")
    try:
        # The per-key guard keys on usage:spend:key:{key_id}:{YYYYMM}
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
        assert upstream.calls == 0
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# § Unit test — composite dispatch logic (test_composite_unit_dispatch)
# ---------------------------------------------------------------------------


async def test_composite_unit_dispatch() -> None:
    """Unit test: verifies CompositeKeyAuthenticator dispatch logic.

    - sk- prefix → delegates to wrapped api_key_authenticator
    - non-sk- prefix → calls resolve_access_token with hashed token
    - None binding → raises InvalidApiKeyError (fail-closed)
    - non-None binding → returns AuthzResult(key_id=token_id, monthly_budget_usd=cap)
    """
    from gateway.keys.domain.errors import InvalidApiKeyError
    from gateway.proxy.infrastructure.composite_key_authenticator import (
        CompositeKeyAuthenticator,
    )

    cap = Decimal("100.00")
    token_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    # Stub settings with agent_oauth_default_budget_usd
    mock_settings = MagicMock()
    mock_settings.agent_oauth_default_budget_usd = cap

    # Stub api_key_authenticator
    from gateway.keys.domain.entities import AuthzResult

    sk_result = AuthzResult(tenant_id=tenant_id, key_id=token_id)
    mock_api_key_auth = AsyncMock()
    mock_api_key_auth.authenticate.return_value = sk_result

    # Stub agent_token_repo — returns None for resolve_access_token (binding absent)
    mock_repo_none = AsyncMock()
    mock_repo_none.resolve_access_token.return_value = None

    # Stub hasher
    mock_hasher = MagicMock()
    mock_hasher.hash.return_value = "deadbeef"

    composite = CompositeKeyAuthenticator(
        api_key_authenticator=mock_api_key_auth,
        agent_token_repo=mock_repo_none,
        hasher=mock_hasher,
        settings=mock_settings,
    )

    # Branch 1: sk- prefix → delegates to wrapped authenticator
    result = await composite.authenticate("sk-abc123.secret")
    mock_api_key_auth.authenticate.assert_called_once_with("sk-abc123.secret")
    assert result is sk_result
    mock_repo_none.resolve_access_token.assert_not_called()

    # Branch 2: non-sk- with None binding → InvalidApiKeyError
    non_sk_token = "AAABBBCCC"
    with pytest.raises(InvalidApiKeyError):
        await composite.authenticate(non_sk_token)
    mock_hasher.hash.assert_called_with(non_sk_token)
    mock_repo_none.resolve_access_token.assert_called_once()

    # Branch 3: non-sk- with valid binding → AuthzResult(key_id=token_id, budget=cap)
    from gateway.agent_oauth.domain.entities import AgentTokenBinding

    binding = AgentTokenBinding(
        token_id=token_id,
        tenant_id=tenant_id,
        user_id=uuid.uuid4(),
        scope="proxy",
    )
    mock_repo_bound = AsyncMock()
    mock_repo_bound.resolve_access_token.return_value = binding

    composite2 = CompositeKeyAuthenticator(
        api_key_authenticator=mock_api_key_auth,
        agent_token_repo=mock_repo_bound,
        hasher=mock_hasher,
        settings=mock_settings,
    )
    authz = await composite2.authenticate("some-agent-token")
    assert authz.tenant_id == tenant_id
    assert authz.key_id == token_id
    assert authz.monthly_budget_usd == cap
    # No expiry (resolve already gates)
    assert authz.expires_at is None
    assert authz.model_allowlist is None
    assert authz.rpm_limit is None
    assert authz.tpm_limit is None


# ---------------------------------------------------------------------------
# § Config knob validation test
# ---------------------------------------------------------------------------


def test_settings_agent_oauth_default_budget_usd_knob() -> None:
    """Settings.agent_oauth_default_budget_usd defaults to 100.00 and is configurable."""
    from gateway.core.config import Settings

    # Default value
    s = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
    )
    assert s.agent_oauth_default_budget_usd == Decimal("100.00")

    # Configurable via env-prefixed field
    s2 = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        agent_oauth_default_budget_usd=Decimal("50.00"),
    )
    assert s2.agent_oauth_default_budget_usd == Decimal("50.00")
