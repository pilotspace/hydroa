"""Failing-first (RED) suite for key-governance (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.

Right-reason red targets:
  - New endpoints (PATCH /admin/keys/{id}, POST /admin/keys/{id}/rotate) → 404/405 routing error
    before build (no route registered yet).
  - New governance fields on POST /admin/keys → missing from response (KeyError or AssertionError)
    because the schema/use-case does not accept/return them yet.
  - Enforcement tests (expiry, allowlist, per-key budget) → wrong status code (upstream called,
    returns 200) because CompletionUseCase has no enforcement yet.

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys, /internal/authz, /v1/chat/completions

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 (db index 9, flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network — same as existing suites)
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
ADMIN_KEYS = "/admin/keys"
INTERNAL_AUTHZ = "/internal/authz"
COMPLETIONS = "/v1/chat/completions"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
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


def _spend_key_tenant(tenant_id: str) -> str:
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:{tenant_id}:{yyyymm}"


def _spend_key_key(key_id: str) -> str:
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:key:{key_id}:{yyyymm}"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — reused from proxy/budgets test pattern."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-gov-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-g1","choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model for proxy tests."""
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


@pytest.fixture
async def active_model_b(db_session: AsyncSession) -> str:
    """Second active model for allowlist boundary tests."""
    model_id = "openai/gpt-3.5-turbo"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 16384, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-3.5 Turbo"},
    )
    await db_session.commit()
    return model_id


async def _create_governed_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str = "governed-key",
    monthly_budget_usd: str | None = None,
    soft_budget_usd: str | None = None,
    expires_at: str | None = None,
    model_allowlist: list[str] | None = None,
    _allowlist_sentinel: bool = False,
) -> dict[str, Any]:
    """Create a key with specified governance fields; return the response body."""
    payload: dict[str, Any] = {"name": name}
    if monthly_budget_usd is not None:
        payload["monthly_budget_usd"] = monthly_budget_usd
    if soft_budget_usd is not None:
        payload["soft_budget_usd"] = soft_budget_usd
    if expires_at is not None:
        payload["expires_at"] = expires_at
    if model_allowlist is not None or _allowlist_sentinel:
        payload["model_allowlist"] = model_allowlist
    resp = await client.post(ADMIN_KEYS, json=payload, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create key failed: {resp.text}"
    return resp.json()


async def _set_tenant_budget(db_session: AsyncSession, tenant_id: str, budget: str | None) -> None:
    await db_session.execute(
        text("UPDATE tenants SET budget_usd_monthly = :b WHERE id = :tid"),
        {"b": budget, "tid": tenant_id},
    )
    await db_session.commit()


# ===========================================================================
# §2 Scenario 1: Create key with all governance fields persisted and echoed
# ===========================================================================


async def test_create_key_with_all_governance_fields(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/keys with all governance fields → 201 + fields in response and DB row."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo1", email="owner1@gov.io")

    resp = await client.post(
        ADMIN_KEYS,
        json={
            "name": "governed-key",
            "monthly_budget_usd": "50.00",
            "soft_budget_usd": "40.00",
            "expires_at": "2099-01-01T00:00:00Z",
            "model_allowlist": ["openai/gpt-4o", "openai/gpt-3.5-turbo"],
        },
        headers=auth_jwt(jwt),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Governance fields must be present in the creation response
    assert body["monthly_budget_usd"] == "50.00", f"got: {body.get('monthly_budget_usd')}"
    assert body["soft_budget_usd"] == "40.00", f"got: {body.get('soft_budget_usd')}"
    assert "expires_at" in body, "expires_at missing from response"
    assert body["model_allowlist"] == ["openai/gpt-4o", "openai/gpt-3.5-turbo"]

    # GET /admin/keys must echo the same governance fields
    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    assert list_resp.status_code == 200
    items: list[dict[str, Any]] = list_resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["key_id"] == body["key_id"]
    assert item["monthly_budget_usd"] == "50.00"
    assert item["soft_budget_usd"] == "40.00"
    assert item["model_allowlist"] == ["openai/gpt-4o", "openai/gpt-3.5-turbo"]

    # DB row must carry the values
    key_id_hex = str(uuid.UUID(body["key_id"])).replace("-", "")
    row = (
        await db_session.execute(
            text(
                "SELECT monthly_budget_usd, soft_budget_usd, expires_at, model_allowlist"
                " FROM api_keys WHERE id = :id"
            ),
            {"id": body["key_id"]},
        )
    ).one()
    assert Decimal(str(row[0])) == Decimal("50.00")
    assert Decimal(str(row[1])) == Decimal("40.00")
    assert row[2] is not None  # expires_at set
    # JSONB array comparison
    assert row[3] == ["openai/gpt-4o", "openai/gpt-3.5-turbo"]


# ===========================================================================
# §2 Scenario 2: Create key with no governance fields defaults to nulls
# ===========================================================================


async def test_create_key_defaults_to_unlimited(
    client: httpx.AsyncClient,
) -> None:
    """POST /admin/keys bare → 201 with null governance fields."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo2", email="owner2@gov.io")

    resp = await client.post(ADMIN_KEYS, json={"name": "bare-key"}, headers=auth_jwt(jwt))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body.get("monthly_budget_usd") is None, f"expected null, got {body.get('monthly_budget_usd')}"
    assert body.get("soft_budget_usd") is None
    assert body.get("expires_at") is None
    assert body.get("model_allowlist") is None

    # GET /admin/keys echoes null fields
    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    items = list_resp.json()
    assert items[0]["monthly_budget_usd"] is None
    assert items[0]["model_allowlist"] is None


# ===========================================================================
# §2 Scenario 3: PATCH updates budget and allowlist on an active key
# ===========================================================================


async def test_patch_key_updates_budget_and_allowlist(
    client: httpx.AsyncClient,
) -> None:
    """PATCH /admin/keys/{key_id} → 200 with updated fields; GET echoes them."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo3", email="owner3@gov.io")
    created = (await client.post(
        ADMIN_KEYS, json={"name": "patchable"}, headers=auth_jwt(jwt)
    )).json()
    key_id = created["key_id"]

    resp = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"monthly_budget_usd": "25.00", "model_allowlist": ["openai/gpt-4o"]},
        headers=auth_jwt(jwt),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["monthly_budget_usd"] == "25.00"
    assert body["model_allowlist"] == ["openai/gpt-4o"]

    # GET echoes the updated values
    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    items = list_resp.json()
    item = next(i for i in items if i["key_id"] == key_id)
    assert item["monthly_budget_usd"] == "25.00"
    assert item["model_allowlist"] == ["openai/gpt-4o"]


# ===========================================================================
# §2 Scenario 4: Rotate key — new secret works, old rejected immediately
# ===========================================================================


async def test_rotate_key_new_works_old_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/keys/{key_id}/rotate → 201; old key rejected; new key authenticated."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo4", email="owner4@gov.io")
    created = (await client.post(
        ADMIN_KEYS, json={"name": "to-rotate"}, headers=auth_jwt(jwt)
    )).json()
    old_key_id = created["key_id"]
    old_plaintext = created["key"]

    # Verify old key is active before rotation
    pre_authz = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": old_plaintext})
    assert pre_authz.status_code == 200

    # Rotate
    rotate_resp = await client.post(
        f"{ADMIN_KEYS}/{old_key_id}/rotate",
        json={},
        headers=auth_jwt(jwt),
    )
    assert rotate_resp.status_code == 201, f"expected 201, got {rotate_resp.status_code}: {rotate_resp.text}"
    rot_body = rotate_resp.json()

    # Response shape
    assert "new_key_id" in rot_body, f"new_key_id missing: {rot_body}"
    assert "superseded_key_id" in rot_body
    assert "key" in rot_body, "plaintext new key missing"
    assert rot_body["superseded_key_id"] == old_key_id
    assert rot_body["new_key_id"] != old_key_id

    new_plaintext: str = rot_body["key"]
    new_key_id: str = rot_body["new_key_id"]

    # Old key rejected immediately
    authz_old = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": old_plaintext})
    assert_problem(authz_old, 401, "ERR_AUTH_INVALID_KEY")

    # New key works
    authz_new = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": new_plaintext})
    assert authz_new.status_code == 200, f"new key authz failed: {authz_new.text}"

    # DB: old row has revoked_at; new row has rotated_from_key_id
    old_row = (
        await db_session.execute(
            text("SELECT revoked_at FROM api_keys WHERE id = :id"),
            {"id": old_key_id},
        )
    ).one()
    assert old_row[0] is not None, "old key revoked_at must be set after rotation"

    new_row = (
        await db_session.execute(
            text("SELECT rotated_from_key_id FROM api_keys WHERE id = :id"),
            {"id": new_key_id},
        )
    ).one()
    assert str(new_row[0]) == old_key_id, (
        f"rotated_from_key_id must equal old key_id; got {new_row[0]}"
    )


# ===========================================================================
# §2 Scenario 5: Rotate inherits governance fields from old key
# ===========================================================================


async def test_rotate_inherits_governance_fields(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST rotate with no body overrides → new row inherits all governance fields."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo5", email="owner5@gov.io")
    created = (await client.post(
        ADMIN_KEYS,
        json={
            "name": "inheritor",
            "monthly_budget_usd": "20.00",
            "model_allowlist": ["openai/gpt-4o"],
        },
        headers=auth_jwt(jwt),
    )).json()
    old_key_id = created["key_id"]

    rotate_resp = await client.post(
        f"{ADMIN_KEYS}/{old_key_id}/rotate", json={}, headers=auth_jwt(jwt)
    )
    assert rotate_resp.status_code == 201, rotate_resp.text
    rot_body = rotate_resp.json()

    # Governance fields must be inherited
    assert rot_body.get("monthly_budget_usd") == "20.00", (
        f"expected '20.00', got {rot_body.get('monthly_budget_usd')}"
    )
    assert rot_body.get("model_allowlist") == ["openai/gpt-4o"], (
        f"expected inherited allowlist, got {rot_body.get('model_allowlist')}"
    )

    # Confirm DB row
    new_key_id = rot_body["new_key_id"]
    row = (
        await db_session.execute(
            text("SELECT monthly_budget_usd, model_allowlist FROM api_keys WHERE id = :id"),
            {"id": new_key_id},
        )
    ).one()
    assert Decimal(str(row[0])) == Decimal("20.00")
    assert row[1] == ["openai/gpt-4o"]


# ===========================================================================
# §2 Scenario 6: Rotate with explicit override replaces only supplied fields
# ===========================================================================


async def test_rotate_with_override_replaces_only_supplied(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Rotate with monthly_budget_usd override → only that field changes; others inherited."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo6", email="owner6@gov.io")
    created = (await client.post(
        ADMIN_KEYS,
        json={
            "name": "partial-override",
            "monthly_budget_usd": "20.00",
            "model_allowlist": ["openai/gpt-4o"],
            "expires_at": "2099-01-01T00:00:00Z",
        },
        headers=auth_jwt(jwt),
    )).json()
    old_key_id = created["key_id"]

    rotate_resp = await client.post(
        f"{ADMIN_KEYS}/{old_key_id}/rotate",
        json={"monthly_budget_usd": "30.00"},
        headers=auth_jwt(jwt),
    )
    assert rotate_resp.status_code == 201, rotate_resp.text
    rot_body = rotate_resp.json()

    # Only monthly_budget_usd should change; allowlist and expires_at inherit
    assert rot_body.get("monthly_budget_usd") == "30.00"
    assert rot_body.get("model_allowlist") == ["openai/gpt-4o"], (
        f"model_allowlist must be inherited; got {rot_body.get('model_allowlist')}"
    )
    assert rot_body.get("expires_at") is not None, "expires_at must be inherited"


# ===========================================================================
# §2 Scenario 7: Expired key rejected at proxy (R9)
# ===========================================================================


async def test_expired_key_rejected_at_proxy(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
) -> None:
    """Expired key → 401 ERR_AUTH_KEY_EXPIRED; upstream.calls==0; no usage_record written."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo7", email="owner7@gov.io")

    # Create key with expiry 2 seconds in the past
    past_ts = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = (await client.post(
        ADMIN_KEYS,
        json={"name": "expired-key", "expires_at": past_ts},
        headers=auth_jwt(jwt),
    )).json()
    plaintext_key: str = created["key"]

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    count_before = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert_problem(resp, 401, "ERR_AUTH_KEY_EXPIRED")
    assert upstream.calls == 0, f"upstream must not be called; calls={upstream.calls}"

    count_after = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()
    assert count_after == count_before, "no usage_record must be written for expired key"


# ===========================================================================
# §2 Scenario 8: Model not in allowlist rejected (R10)
# ===========================================================================


async def test_model_not_in_allowlist_rejected(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    active_model_b: str,
) -> None:
    """Key allowlist=["openai/gpt-4o"] + request model=gpt-3.5-turbo → 403 ERR_MODEL_NOT_ALLOWED."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo8", email="owner8@gov.io")
    created = (await client.post(
        ADMIN_KEYS,
        json={"name": "restricted", "model_allowlist": [active_model]},
        headers=auth_jwt(jwt),
    )).json()
    plaintext_key: str = created["key"]

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    count_before = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model_b, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert_problem(resp, 403, "ERR_MODEL_NOT_ALLOWED")
    assert upstream.calls == 0
    count_after = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()
    assert count_after == count_before


# ===========================================================================
# §2 Scenario 9: Model in allowlist passes enforcement
# ===========================================================================


async def test_model_in_allowlist_allowed(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
) -> None:
    """Key allowlist=["openai/gpt-4o"] + request model=gpt-4o → 200."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo9", email="owner9@gov.io")
    created = (await client.post(
        ADMIN_KEYS,
        json={"name": "allowed", "model_allowlist": [active_model]},
        headers=auth_jwt(jwt),
    )).json()
    plaintext_key: str = created["key"]

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    # Bypass tenant/key budget checks by using PassthroughBudgetGuard default
    from gateway.budgets.domain.ports import PassthroughBudgetGuard  # noqa: PLC0415

    app.state.budget_guard = PassthroughBudgetGuard()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert upstream.calls == 1


# ===========================================================================
# §2 Scenario 10: Null model_allowlist allows all models
# ===========================================================================


async def test_null_allowlist_allows_all_models(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
) -> None:
    """Key model_allowlist=null (default) → model passes enforcement."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo10", email="owner10@gov.io")
    created = (await client.post(
        ADMIN_KEYS, json={"name": "open-key"}, headers=auth_jwt(jwt)
    )).json()
    plaintext_key: str = created["key"]

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    from gateway.budgets.domain.ports import PassthroughBudgetGuard  # noqa: PLC0415

    app.state.budget_guard = PassthroughBudgetGuard()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


# ===========================================================================
# §2 Scenario 11: Empty model_allowlist blocks all models
# ===========================================================================


async def test_empty_allowlist_blocks_all_models(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
) -> None:
    """Key model_allowlist=[] (empty array) → every model rejected → 403."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo11", email="owner11@gov.io")

    # Pass model_allowlist=[] explicitly (security-strict: empty = all blocked)
    resp_create = await client.post(
        ADMIN_KEYS,
        json={"name": "locked-key", "model_allowlist": []},
        headers=auth_jwt(jwt),
    )
    assert resp_create.status_code == 201, resp_create.text
    plaintext_key: str = resp_create.json()["key"]

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert_problem(resp, 403, "ERR_MODEL_NOT_ALLOWED")
    assert upstream.calls == 0


# ===========================================================================
# §2 Scenario 12: Per-key hard budget exceeded blocks completion (R11)
# ===========================================================================


async def test_per_key_budget_exceeded_blocks_completion(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """Key monthly_budget_usd=10.00, per-key counter=10.00 → 402 ERR_BUDGET_EXCEEDED."""
    jwt, tenant_id = await signup_and_login(client, tenant_name="GovCo12", email="owner12@gov.io")
    created = (await client.post(
        ADMIN_KEYS,
        json={"name": "capped", "monthly_budget_usd": "10.00"},
        headers=auth_jwt(jwt),
    )).json()
    plaintext_key: str = created["key"]
    key_id: str = created["key_id"]

    # Simulate per-key spend at limit
    await redis_client.set(_spend_key_key(key_id), b"10.00")

    # Wire the guard that reads per-key counters
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # noqa: PLC0415

    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client, session_factory=app.state.sessionmaker
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    count_before = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert upstream.calls == 0
    count_after = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()
    assert count_after == count_before


# ===========================================================================
# §2 Scenario 13: Per-key budget null falls back to tenant budget
# ===========================================================================


async def test_per_key_budget_null_falls_back_to_tenant(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """Key monthly_budget_usd=null + tenant at limit → 402 (tenant budget enforced)."""
    jwt, tenant_id = await signup_and_login(client, tenant_name="GovCo13", email="owner13@gov.io")
    created = (await client.post(
        ADMIN_KEYS, json={"name": "fallback"}, headers=auth_jwt(jwt)
    )).json()
    plaintext_key: str = created["key"]

    # Set tenant budget at limit
    await _set_tenant_budget(db_session, tenant_id, "5.00")
    await redis_client.set(_spend_key_tenant(tenant_id), b"5.00")

    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # noqa: PLC0415

    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client, session_factory=app.state.sessionmaker
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert upstream.calls == 0


# ===========================================================================
# §2 Scenario 14: Per-key budget wins over tenant budget (most-specific-wins)
# ===========================================================================


async def test_per_key_budget_wins_over_tenant_budget(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """Key budget=3.00 at limit, tenant budget=10.00 not at limit → 402 (key wins)."""
    jwt, tenant_id = await signup_and_login(client, tenant_name="GovCo14", email="owner14@gov.io")
    created = (await client.post(
        ADMIN_KEYS,
        json={"name": "keywin", "monthly_budget_usd": "3.00"},
        headers=auth_jwt(jwt),
    )).json()
    plaintext_key: str = created["key"]
    key_id: str = created["key_id"]

    # Per-key counter at key budget; tenant counter well below tenant budget
    await redis_client.set(_spend_key_key(key_id), b"3.00")
    await _set_tenant_budget(db_session, tenant_id, "10.00")
    await redis_client.set(_spend_key_tenant(tenant_id), b"1.00")

    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # noqa: PLC0415

    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client, session_factory=app.state.sessionmaker
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert upstream.calls == 0


# ===========================================================================
# §2 Scenario 15: Soft budget crossing does NOT block completion (seam-only)
# ===========================================================================


async def test_soft_budget_crossing_does_not_block(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """spend >= soft_budget_usd but < monthly_budget_usd → 200, NOT blocked."""
    jwt, tenant_id = await signup_and_login(client, tenant_name="GovCo15", email="owner15@gov.io")
    created = (await client.post(
        ADMIN_KEYS,
        json={"name": "soft-key", "monthly_budget_usd": "10.00", "soft_budget_usd": "5.00"},
        headers=auth_jwt(jwt),
    )).json()
    plaintext_key: str = created["key"]
    key_id: str = created["key_id"]

    # Per-key spend at soft limit (5.00) but below hard limit (10.00)
    await redis_client.set(_spend_key_key(key_id), b"5.00")
    # No tenant budget to interfere
    await _set_tenant_budget(db_session, tenant_id, None)

    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # noqa: PLC0415

    app.state.budget_guard = RedisBudgetGuard(
        redis=redis_client, session_factory=app.state.sessionmaker
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(plaintext_key),
    )

    assert resp.status_code == 200, (
        f"soft budget crossing must NOT block; got {resp.status_code}: {resp.text}"
    )
    assert upstream.calls == 1


# ===========================================================================
# §2 Scenario 16: soft_budget > monthly_budget rejected (R1)
# ===========================================================================


async def test_create_key_soft_greater_than_hard_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/keys with soft_budget > monthly_budget → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo16", email="owner16@gov.io")
    count_before = (await db_session.execute(text("SELECT COUNT(*) FROM api_keys"))).scalar()

    resp = await client.post(
        ADMIN_KEYS,
        json={
            "name": "bad-budget",
            "monthly_budget_usd": "10.00",
            "soft_budget_usd": "15.00",
        },
        headers=auth_jwt(jwt),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    count_after = (await db_session.execute(text("SELECT COUNT(*) FROM api_keys"))).scalar()
    assert count_after == count_before, "no api_keys row must be created on validation failure"


# ===========================================================================
# §2 Scenario 17: Negative monthly_budget rejected (R2)
# ===========================================================================


async def test_create_key_negative_budget_rejected(
    client: httpx.AsyncClient,
) -> None:
    """POST /admin/keys with negative monthly_budget_usd → 422."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo17", email="owner17@gov.io")

    resp = await client.post(
        ADMIN_KEYS,
        json={"name": "negative", "monthly_budget_usd": "-1.00"},
        headers=auth_jwt(jwt),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# §2 Scenario 18: model_allowlist with empty-string element rejected (R4)
# ===========================================================================


async def test_create_key_empty_allowlist_element_rejected(
    client: httpx.AsyncClient,
) -> None:
    """POST /admin/keys with model_allowlist containing "" → 422."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo18", email="owner18@gov.io")

    resp = await client.post(
        ADMIN_KEYS,
        json={"name": "bad-allowlist", "model_allowlist": ["openai/gpt-4o", ""]},
        headers=auth_jwt(jwt),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# §2 Scenario 19: PATCH revoked key returns 404 (R5)
# ===========================================================================


async def test_patch_revoked_key_returns_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH on a revoked key → 404 ERR_KEY_NOT_FOUND; DB row unchanged."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo19", email="owner19@gov.io")
    created = (await client.post(
        ADMIN_KEYS, json={"name": "to-revoke"}, headers=auth_jwt(jwt)
    )).json()
    key_id = created["key_id"]

    # Revoke via the existing route
    del_resp = await client.delete(f"{ADMIN_KEYS}/{key_id}", headers=auth_jwt(jwt))
    assert del_resp.status_code == 204

    patch_resp = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"monthly_budget_usd": "5.00"},
        headers=auth_jwt(jwt),
    )

    assert_problem(patch_resp, 404, "ERR_KEY_NOT_FOUND")

    # DB row is unchanged: revoked_at still set; budget still null
    row = (
        await db_session.execute(
            text("SELECT revoked_at, monthly_budget_usd FROM api_keys WHERE id = :id"),
            {"id": key_id},
        )
    ).one()
    assert row[0] is not None, "revoked_at must still be set"
    assert row[1] is None, "monthly_budget_usd must not have changed"


# ===========================================================================
# §2 Scenario 20: PATCH cross-tenant key returns 404 (R6)
# ===========================================================================


async def test_patch_cross_tenant_key_returns_404(
    client: httpx.AsyncClient,
) -> None:
    """PATCH on another tenant's key → 404 (no cross-tenant leak)."""
    jwt_a, _ = await signup_and_login(client, tenant_name="GovCoA20", email="ownerA20@gov.io")
    jwt_b, _ = await signup_and_login(client, tenant_name="GovCoB20", email="ownerB20@gov.io")

    # Tenant B creates a key
    k_b = (await client.post(
        ADMIN_KEYS, json={"name": "b-key"}, headers=auth_jwt(jwt_b)
    )).json()

    # Tenant A tries to PATCH it
    resp = await client.patch(
        f"{ADMIN_KEYS}/{k_b['key_id']}",
        json={"monthly_budget_usd": "1.00"},
        headers=auth_jwt(jwt_a),
    )

    assert_problem(resp, 404, "ERR_KEY_NOT_FOUND")


# ===========================================================================
# §2 Scenario 21: Member cannot rotate a key (R7)
# ===========================================================================


async def test_member_cannot_rotate(
    client: httpx.AsyncClient,
) -> None:
    """Member-role JWT → 403 ERR_AUTH_FORBIDDEN on rotate; old key still active."""
    import jwt as pyjwt  # noqa: PLC0415

    from tests.conftest import TEST_JWT_SECRET  # noqa: PLC0415

    owner_token, _ = await signup_and_login(client, tenant_name="GovCo21", email="owner21@gov.io")
    created = (await client.post(
        ADMIN_KEYS, json={"name": "protected"}, headers=auth_jwt(owner_token)
    )).json()
    key_id = created["key_id"]
    plaintext_key: str = created["key"]

    owner_claims = pyjwt.decode(
        owner_token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    member_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": owner_claims["tenant_id"],
        "email": "member21@gov.io",
        "role": "member",
        "iss": "ai-proxy",
        "iat": owner_claims["iat"],
        "exp": owner_claims["exp"],
    }
    member_token = pyjwt.encode(member_claims, TEST_JWT_SECRET, algorithm="HS256")

    resp = await client.post(
        f"{ADMIN_KEYS}/{key_id}/rotate", json={}, headers=auth_jwt(member_token)
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    # Old key must still be active
    authz = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": plaintext_key})
    assert authz.status_code == 200, f"old key must still work: {authz.text}"


# ===========================================================================
# §2 Scenario 22: Rotate already-revoked key returns 404 (R8)
# ===========================================================================


async def test_rotate_revoked_key_returns_404(
    client: httpx.AsyncClient,
) -> None:
    """POST rotate on a revoked key → 404 ERR_KEY_NOT_FOUND."""
    jwt, _ = await signup_and_login(client, tenant_name="GovCo22", email="owner22@gov.io")
    created = (await client.post(
        ADMIN_KEYS, json={"name": "pre-revoked"}, headers=auth_jwt(jwt)
    )).json()
    key_id = created["key_id"]

    # Revoke it
    del_resp = await client.delete(f"{ADMIN_KEYS}/{key_id}", headers=auth_jwt(jwt))
    assert del_resp.status_code == 204

    resp = await client.post(
        f"{ADMIN_KEYS}/{key_id}/rotate", json={}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 404, "ERR_KEY_NOT_FOUND")
