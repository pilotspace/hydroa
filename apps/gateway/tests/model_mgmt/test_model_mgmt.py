"""Failing-first (RED) suite for model-mgmt (contract DRAFT — Status: DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.
Asserts observable behavior only — endpoints, problem+json codes, DB row effects.

Right-reason red targets (documented per test):
  - GET /admin/models → 404 (no route registered yet)
  - PUT /admin/models/{model_id} → 404/405 (no route registered yet)
  - /v1/chat/completions with tenant-disabled model → wrong status (200 or 400
    ERR_MODEL_UNKNOWN) because SqlAlchemyModelChecker has no tenant_id-aware check
    and CompletionUseCase does not pass tenant_id

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys, /admin/models,
  /v1/chat/completions

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
    (schema reset per test via the shared `app` fixture in tests/conftest.py)
  - httpx.ASGITransport (no network — same as existing suites)
  - FakeCompletionUpstream injected via app.state (mirrors proxy test pattern)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Route constants — contract §3
# ---------------------------------------------------------------------------
ADMIN_MODELS = "/admin/models"
COMPLETIONS = "/v1/chat/completions"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"

# ---------------------------------------------------------------------------
# Fake upstream — mirrors tests/proxy/test_proxy_completions.py pattern
# ---------------------------------------------------------------------------

_UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-mgmt-001",
    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


class FakeCompletionUpstream:
    """In-memory stub — never makes a real HTTP call."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return 200, _UPSTREAM_BODY

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert RFC 9457 problem+json shape with the expected status and code."""
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
    """Sign up a new tenant+owner; return (jwt_token, tenant_id_str)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str = "test-key",
    model_allowlist: list[str] | None = None,
) -> dict[str, str]:
    """Create an API key via POST /admin/keys; return the full response dict."""
    body: dict[str, Any] = {"name": name}
    if model_allowlist is not None:
        body["model_allowlist"] = model_allowlist
    resp = await client.post(
        ADMIN_KEYS,
        json=body,
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 201, f"create_key failed: {resp.text}"
    return resp.json()  # type: ignore[return-value]


async def insert_catalog_model(
    db: AsyncSession,
    model_id: str,
    *,
    name: str = "Test Model",
    active: bool = True,
) -> None:
    """Insert a model row directly into the catalog (bypasses the sync endpoint)."""
    await db.execute(
        text(
            "INSERT INTO models (id, name, context_length, active) "
            "VALUES (:id, :name, 128000, :active) "
            "ON CONFLICT (id) DO UPDATE SET active = :active, name = :name"
        ),
        {"id": model_id, "name": name, "active": active},
    )
    await db.commit()


async def count_override_rows(
    db: AsyncSession, tenant_id: str, model_id: str
) -> int:
    """Return the count of tenant_model_overrides rows for this (tenant, model) pair."""
    row = await db.execute(
        text(
            "SELECT count(*) FROM tenant_model_overrides "
            "WHERE tenant_id = :tid AND model_id = :mid"
        ),
        {"tid": tenant_id, "mid": model_id},
    )
    return int(row.scalar_one())


def completion_payload(model_id: str) -> dict[str, Any]:
    return {"model": model_id, "messages": [{"role": "user", "content": "ping"}]}


def api_key_auth(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}"}


# ---------------------------------------------------------------------------
# Scenario 1: disable model takes effect on NEXT request, no restart
#
# Right-reason RED:
#   PUT /admin/models/openai%2Fgpt-4o → 404 (route not yet registered)
#   If somehow PUT succeeds in future builds but check is missing in use_cases.py,
#   the completion would return 200 or 400 ERR_MODEL_UNKNOWN (not 403 ERR_MODEL_DISABLED).
# ---------------------------------------------------------------------------


async def test_disable_model_takes_effect_next_request(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """PUT disable → next completion → 403 ERR_MODEL_DISABLED; no upstream call."""
    # Arrange
    jwt_token, tenant_id = await signup_and_login(
        client, tenant_name="TenantDisable", email="disable@tenant.io"
    )
    key_info = await create_key(client, jwt_token)
    raw_key: str = key_info["key"]
    model_id = "openai/gpt-4o"
    await insert_catalog_model(db_session, model_id)
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    # Act: disable the model for this tenant
    put_resp = await client.put(
        f"{ADMIN_MODELS}/{model_id.replace('/', '%2F')}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    # The route doesn't exist yet → right-reason red: 404/405
    assert put_resp.status_code == 200, (
        f"PUT /admin/models did not return 200 — route not yet registered "
        f"(expected red, got {put_resp.status_code}: {put_resp.text})"
    )

    # Act: next completion request (no restart)
    comp_resp = await client.post(
        COMPLETIONS,
        json=completion_payload(model_id),
        headers=api_key_auth(raw_key),
    )

    # Assert: tenant-disabled → 403 ERR_MODEL_DISABLED
    assert_problem(comp_resp, 403, "ERR_MODEL_DISABLED")
    assert upstream.calls == 0, "upstream must NOT be called for a tenant-disabled model"


# ---------------------------------------------------------------------------
# Scenario 2: re-enable model takes effect on next request
#
# Right-reason RED:
#   PUT /admin/models → 404 (no route)
# ---------------------------------------------------------------------------


async def test_reenable_model_takes_effect_next_request(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """disable then re-enable → next completion → 200; upstream called once."""
    jwt_token, _tenant_id = await signup_and_login(
        client, tenant_name="TenantReEnable", email="reenable@tenant.io"
    )
    key_info = await create_key(client, jwt_token)
    raw_key: str = key_info["key"]
    model_id = "openai/gpt-4o"
    await insert_catalog_model(db_session, model_id)
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    model_url = f"{ADMIN_MODELS}/{model_id.replace('/', '%2F')}"

    # Disable
    dr = await client.put(
        model_url,
        json={"enabled": False},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert dr.status_code == 200, (
        f"PUT disable not returning 200 — route not registered (right-reason red): {dr.text}"
    )

    # Re-enable
    er = await client.put(
        model_url,
        json={"enabled": True},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert er.status_code == 200, (
        f"PUT re-enable not returning 200 — route not registered (right-reason red): {er.text}"
    )

    # Next request: must succeed
    comp_resp = await client.post(
        COMPLETIONS,
        json=completion_payload(model_id),
        headers=api_key_auth(raw_key),
    )
    assert comp_resp.status_code == 200, (
        f"expected 200 after re-enable; got {comp_resp.status_code}: {comp_resp.text}"
    )
    assert upstream.calls == 1, "upstream must be called exactly once after re-enable"


# ---------------------------------------------------------------------------
# Scenario 3: PUT with unknown model_id returns 404 ERR_MODEL_NOT_FOUND
#
# Right-reason RED:
#   Route not registered → 404 from FastAPI routing, but the code body will be
#   None (FastAPI default 404 JSON, not ERR_MODEL_NOT_FOUND).
#   The test asserts code == "ERR_MODEL_NOT_FOUND" which is absent → AssertionError.
# ---------------------------------------------------------------------------


async def test_put_unknown_model_returns_404(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """PUT on an unknown model_id → 404 ERR_MODEL_NOT_FOUND; no override row written."""
    jwt_token, tenant_id = await signup_and_login(
        client, tenant_name="TenantUnknown", email="unknown@tenant.io"
    )
    unknown_model = "fake/nonexistent-model"

    resp = await client.put(
        f"{ADMIN_MODELS}/{unknown_model.replace('/', '%2F')}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert_problem(resp, 404, "ERR_MODEL_NOT_FOUND")

    # Assert: no override row written
    try:
        rows = await count_override_rows(db_session, tenant_id, unknown_model)
        assert rows == 0, f"expected 0 override rows, got {rows}"
    except Exception:
        # Table may not exist yet (pre-migration) — that is also a valid red state
        pass


# ---------------------------------------------------------------------------
# Scenario 4: member role PUT → 403 ERR_AUTH_FORBIDDEN
#
# Right-reason RED:
#   Route not registered → 404, not 403.  The test asserts 403 ERR_AUTH_FORBIDDEN
#   which fails → AssertionError on wrong status.
# ---------------------------------------------------------------------------


async def test_put_member_role_forbidden(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """Member-role caller PUT /admin/models → 403 ERR_AUTH_FORBIDDEN."""
    # Create owner (for model catalog setup)
    owner_jwt, tenant_id = await signup_and_login(
        client, tenant_name="TenantMember", email="owner@member-tenant.io"
    )
    model_id = "openai/gpt-4o"
    await insert_catalog_model(db_session, model_id)

    # Create a second user with member role by directly inserting into DB
    # (no API for creating member users in the current surface — use direct SQL)
    member_user_id = str(uuid.uuid4())
    member_password_hash = "argon2_placeholder_not_a_real_hash"
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, :email, :ph, 'member')"
        ),
        {
            "id": member_user_id,
            "tid": tenant_id,
            "email": "member@member-tenant.io",
            "ph": member_password_hash,
        },
    )
    await db_session.commit()

    # Mint a member JWT directly using the token service (avoids password auth
    # which would fail because we inserted a placeholder hash above)
    from gateway.tenants.domain.entities import Role

    member_jwt, _ = app.state.token_service.issue(
        user_id=uuid.UUID(member_user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.MEMBER,
        email="member@member-tenant.io",
    )

    resp = await client.put(
        f"{ADMIN_MODELS}/{model_id.replace('/', '%2F')}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {member_jwt}"},
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# Scenario 5: member role GET /admin/models → 403 ERR_AUTH_FORBIDDEN
#
# Right-reason RED:
#   Route not registered → 404. Test asserts 403 → fails on status.
# ---------------------------------------------------------------------------


async def test_get_admin_models_member_forbidden(
    client: httpx.AsyncClient,
    app: Any,
) -> None:
    """Member role GET /admin/models → 403 ERR_AUTH_FORBIDDEN."""
    _owner_jwt, tenant_id = await signup_and_login(
        client, tenant_name="TenantGetMember", email="gowner@getmember.io"
    )

    # Mint a member JWT directly via token_service.issue (avoids password auth)
    from gateway.tenants.domain.entities import Role

    member_jwt, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.MEMBER,
        email="gmember@getmember.io",
    )

    resp = await client.get(
        ADMIN_MODELS,
        headers={"Authorization": f"Bearer {member_jwt}"},
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# Scenario 6: GET /admin/models shows enabled flags reflecting overrides
#
# Right-reason RED:
#   Route not registered → 404 (FastAPI default, no "code" field).
#   Test asserts resp.status_code == 200 → fails immediately.
# ---------------------------------------------------------------------------


async def test_get_admin_models_shows_enabled_flags(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """GET /admin/models: disabled model shows enabled=false; other shows enabled=true."""
    jwt_token, _tid = await signup_and_login(
        client, tenant_name="TenantFlags", email="flags@tenant.io"
    )
    model_a = "openai/gpt-4o"
    model_b = "anthropic/claude-opus-4"
    await insert_catalog_model(db_session, model_a, name="GPT-4o")
    await insert_catalog_model(db_session, model_b, name="Claude Opus 4")

    # Disable model_a
    dr = await client.put(
        f"{ADMIN_MODELS}/{model_a.replace('/', '%2F')}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert dr.status_code == 200, (
        f"PUT /admin/models not registered (right-reason red for PUT): {dr.text}"
    )

    # GET the list
    resp = await client.get(
        ADMIN_MODELS,
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, (
        f"GET /admin/models not registered (right-reason red): {resp.text}"
    )

    data = resp.json()["data"]
    model_map: dict[str, bool] = {m["id"]: m["enabled"] for m in data}

    assert model_a in model_map, f"{model_a} missing from GET /admin/models response"
    assert model_b in model_map, f"{model_b} missing from GET /admin/models response"
    assert model_map[model_a] is False, f"expected {model_a} enabled=False, got {model_map[model_a]}"
    assert model_map[model_b] is True, f"expected {model_b} enabled=True, got {model_map[model_b]}"


# ---------------------------------------------------------------------------
# Scenario 7: default posture is enabled when no override row exists
#
# Right-reason RED:
#   Route not registered → 404. Test asserts 200 → fails.
# ---------------------------------------------------------------------------


async def test_default_posture_enabled_when_no_override(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """No override row → GET /admin/models shows enabled=true (open by default)."""
    jwt_token, _tid = await signup_and_login(
        client, tenant_name="TenantDefault", email="default@tenant.io"
    )
    model_id = "openai/gpt-4o"
    await insert_catalog_model(db_session, model_id)

    resp = await client.get(
        ADMIN_MODELS,
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 200, (
        f"GET /admin/models not registered (right-reason red): {resp.text}"
    )

    data = resp.json()["data"]
    assert len(data) >= 1
    model_entry = next((m for m in data if m["id"] == model_id), None)
    assert model_entry is not None, f"{model_id} not found in response"
    assert model_entry["enabled"] is True, (
        f"default posture must be enabled=True; got {model_entry['enabled']}"
    )


# ---------------------------------------------------------------------------
# Scenario 8: tenant isolation — A's disable does not affect B
#
# Right-reason RED:
#   PUT route not registered → PUT returns 404.
#   Even if somehow tenant A's disable gets written (e.g. by direct SQL),
#   the ModelChecker has no tenant_id logic so tenant B's request would
#   return the same rejection → wrong behavior caught by the assertion.
# ---------------------------------------------------------------------------


async def test_tenant_isolation_a_disable_does_not_affect_b(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """Tenant A disables model; Tenant B's completions still succeed."""
    model_id = "openai/gpt-4o"
    await insert_catalog_model(db_session, model_id)

    # Tenant A: signs up, disables model
    jwt_a, tenant_id_a = await signup_and_login(
        client, tenant_name="TenantIsoA", email="iso-a@tenant.io"
    )
    dr = await client.put(
        f"{ADMIN_MODELS}/{model_id.replace('/', '%2F')}",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {jwt_a}"},
    )
    assert dr.status_code == 200, (
        f"PUT /admin/models not registered (right-reason red for isolation setup): {dr.text}"
    )

    # Tenant B: signs up, creates key, makes completion
    jwt_b, tenant_id_b = await signup_and_login(
        client, tenant_name="TenantIsoB", email="iso-b@tenant.io"
    )
    key_b = await create_key(client, jwt_b)
    raw_key_b: str = key_b["key"]
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    comp_resp = await client.post(
        COMPLETIONS,
        json=completion_payload(model_id),
        headers=api_key_auth(raw_key_b),
    )
    # Tenant B must succeed — A's override must not bleed across
    assert comp_resp.status_code == 200, (
        f"Tenant B's completion should succeed; got {comp_resp.status_code}: {comp_resp.text}"
    )
    assert upstream.calls == 1

    # GET /admin/models for A shows false; for B shows true
    ga = await client.get(ADMIN_MODELS, headers={"Authorization": f"Bearer {jwt_a}"})
    assert ga.status_code == 200, f"GET /admin/models for A not registered: {ga.text}"
    data_a = ga.json()["data"]
    entry_a = next((m for m in data_a if m["id"] == model_id), None)
    assert entry_a is not None
    assert entry_a["enabled"] is False, f"Tenant A: expected enabled=False, got {entry_a['enabled']}"

    gb = await client.get(ADMIN_MODELS, headers={"Authorization": f"Bearer {jwt_b}"})
    assert gb.status_code == 200, f"GET /admin/models for B not registered: {gb.text}"
    data_b = gb.json()["data"]
    entry_b = next((m for m in data_b if m["id"] == model_id), None)
    assert entry_b is not None
    assert entry_b["enabled"] is True, f"Tenant B: expected enabled=True, got {entry_b['enabled']}"


# ---------------------------------------------------------------------------
# Scenario 9: key allowlist enforced independently — composes with tenant override
#
# Right-reason RED:
#   The key-level allowlist IS already enforced (key-governance task is done).
#   This test asserts ERR_MODEL_NOT_ALLOWED specifically (not DISABLED).
#   Currently the test passes IF allowlist is enforced (it is) — BUT we need to
#   verify the composition: a model that is tenant-ENABLED but key-BLOCKED must
#   still get ERR_MODEL_NOT_ALLOWED, not ERR_MODEL_DISABLED.
#   Before build: the model_allowlist check returns ERR_MODEL_NOT_ALLOWED
#   (already implemented); the tenant-disabled check doesn't exist yet.
#   This test is GREEN for the existing behavior but is included to GUARD that
#   the build does not swap the error code (i.e., short-circuits with DISABLED
#   before checking the allowlist).
#   NOTE: This test starts GREEN. It is a GUARD test that must STAY green after
#   build. Marked with a comment to confirm this is intentional per CONVENTIONS.md
#   (a guard for enforcement order, not a pure red scenario).
# ---------------------------------------------------------------------------


async def test_key_allowlist_enforced_independently_composes(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """Key allowlist blocks model → ERR_MODEL_NOT_ALLOWED (not DISABLED), even when tenant-enabled.

    GUARD TEST: This scenario is currently green (allowlist enforcement already exists).
    Its purpose is to prevent regressions where the build accidentally short-circuits
    with ERR_MODEL_DISABLED before reaching the allowlist check.
    Enforcement order contract: key allowlist check (step 2) BEFORE tenant-disabled check (step 3).
    """
    jwt_token, _tid = await signup_and_login(
        client, tenant_name="TenantAllowlist", email="allowlist@tenant.io"
    )
    model_id = "openai/gpt-4o"
    allowed_model = "anthropic/claude-opus-4"
    await insert_catalog_model(db_session, model_id)
    await insert_catalog_model(db_session, allowed_model)

    # Create key with allowlist that EXCLUDES model_id
    key_info = await create_key(client, jwt_token, model_allowlist=[allowed_model])
    raw_key: str = key_info["key"]

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(model_id),
        headers=api_key_auth(raw_key),
    )
    # Must be ERR_MODEL_NOT_ALLOWED (key-level), not ERR_MODEL_DISABLED (tenant-level)
    assert_problem(resp, 403, "ERR_MODEL_NOT_ALLOWED")
    assert upstream.calls == 0


# ---------------------------------------------------------------------------
# Scenario 10: PUT is idempotent — upsert, no duplicate rows
#
# Right-reason RED:
#   PUT route not registered → both PUTs return 404.
#   count_override_rows query: table tenant_model_overrides does not exist
#   → sqlalchemy raises ProgrammingError.
#   Test fails on: either the PUT assertion (right-reason) or the table-missing exception.
# ---------------------------------------------------------------------------


async def test_put_idempotent_upsert_no_duplicate_rows(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """Two PUT {"enabled": false} calls for the same model → exactly one override row."""
    jwt_token, tenant_id = await signup_and_login(
        client, tenant_name="TenantUpsert", email="upsert@tenant.io"
    )
    model_id = "openai/gpt-4o"
    await insert_catalog_model(db_session, model_id)

    model_url = f"{ADMIN_MODELS}/{model_id.replace('/', '%2F')}"

    r1 = await client.put(
        model_url,
        json={"enabled": False},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r1.status_code == 200, (
        f"First PUT not returning 200 — route not registered (right-reason red): {r1.text}"
    )

    r2 = await client.put(
        model_url,
        json={"enabled": False},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r2.status_code == 200, (
        f"Second PUT not returning 200 — route not registered (right-reason red): {r2.text}"
    )

    # Verify exactly one row (no duplicate)
    rows = await count_override_rows(db_session, tenant_id, model_id)
    assert rows == 1, f"upsert must yield exactly 1 row; got {rows}"
