"""Failing-first (RED) suite for preset-admin-surface (TASK.md §4, contract FROZEN @ v2).

Backend half of the FIRST admin-write path for tenant model presets: an authenticated
tenant OWNER can list/upsert/delete their OWN named (preset_name, alias_key) -> target_model
mappings via ``/admin/presets``, mirroring the v25 BYOK ``provider_keys_admin_router`` auth
and error-mapping shape exactly.

TRUE-RED RULE: every test asserts TARGET behavior against the contracted surface
``presets_admin_router`` @ ``/admin/presets``. That router does not exist yet and is NOT
registered in main.py, so every GET/PUT/DELETE hits FastAPI's default 404 (no route) —
each test fails NOW for the RIGHT reason:

  create/list/upsert/delete happy paths : route missing -> 404 default; expected 200/204.
  reject-selector / reject-target       : route missing -> 404 default; expected 400.
  reject-non-owner                      : route missing -> 404 default; expected 403.
  reject-missing-token                  : route missing -> 404 default; expected 401.
  tenant-isolation                      : route missing -> 404 default.
  ingress-composition                   : the store itself already exists (tenant-preset-store,
                                           done) — this test's PUT leg still 404s until the
                                           router exists.

Infrastructure (mirrors tests/provider_config_admin_api + tests/tenant_model_presets):
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9
  - httpx.ASGITransport (no network for gateway calls)
  - Self-contained per test: create_app + bootstrap_fresh_db
  - signup_tenant() -> owner JWT; app.state.token_service.issue() -> member JWT (non-owner case)
  - target_model validation requires an active row in the ``models`` catalog table
    (seeded via raw SQL, mirroring tests/tenant_model_presets/test_tenant_model_preset_store_db.py)
  - asyncio_mode = "auto" (pyproject.toml)
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.core.errors import ProblemError
from gateway.main import create_app
from gateway.proxy.api.presets_admin_router import (
    PresetUpsertBody,
    delete_preset,
    upsert_preset,
)
from gateway.proxy.domain.model_presets import TenantModelPreset
from gateway.tenants.domain.entities import Role

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
PRESETS = "/admin/presets"

# ---------------------------------------------------------------------------
# Test parameters
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
TEST_REDIS_URL = "redis://localhost:6380/9"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
PASSWORD = "correct horse battery staple"  # noqa: S105


# ---------------------------------------------------------------------------
# Settings + bootstrap helpers (mirror tests/provider_config_admin_api)
# ---------------------------------------------------------------------------


def make_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
    )


def _is_deadlock(exc: DBAPIError) -> bool:
    """True when `exc` wraps Postgres' deadlock_detected (SQLSTATE 40P01).

    A per-test full drop_all/create_all against the shared `gateway_test`
    database can transiently race a not-yet-fully-released connection from
    the previous test (or a concurrent write) for an AccessExclusiveLock —
    a known, pre-existing characteristic of this repo's per-test bootstrap
    pattern (see preset-admin-surface TASK.md), not a defect in the schema
    under test. PostgreSQL's own docs recommend retrying a deadlock.
    """
    if getattr(exc.orig, "sqlstate", None) == "40P01":
        return True
    return "deadlock detected" in str(exc).lower()


async def bootstrap_fresh_db(settings: Settings) -> tuple[Any, Any]:
    """Create fresh schema; return (engine, sessionmaker)."""
    bootstrap_app = create_app(settings)
    engine = bootstrap_app.state.engine
    for attempt in range(3):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            break
        except DBAPIError as exc:
            if not _is_deadlock(exc) or attempt == 2:
                raise
            await asyncio.sleep(random.uniform(0, 0.05 * (2**attempt)))  # noqa: S311 — jitter, not cryptographic
    await bootstrap_app.state.engine.dispose()
    return engine, bootstrap_app.state.sessionmaker


async def make_app(settings: Settings, engine: Any, sessionmaker: Any) -> Any:
    """create_app wired to the bootstrap engine/sessionmaker."""
    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    return app


def client_for(app: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def signup_tenant(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = PASSWORD,
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


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert RFC 9457 problem+json shape; return parsed body."""
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


async def insert_model_row(session: AsyncSession, model_id: str, *, active: bool = True) -> None:
    """Seed a catalog models row so target_model validation can pass.

    Mirrors tests/tenant_model_presets/test_tenant_model_preset_store_db.py's
    insert_model_row helper exactly.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, active) VALUES (:id, :name, :active) "
            "ON CONFLICT (id) DO UPDATE SET active = EXCLUDED.active"
        ),
        {"id": model_id, "name": model_id, "active": active},
    )
    await session.commit()


async def seed_model(app: Any, model_id: str, *, active: bool = True) -> None:
    async with app.state.sessionmaker() as session:
        await insert_model_row(session, model_id, active=active)


class _FakeRequest:
    """Minimal Request stand-in exposing only what ``_require_owner_tenant_id`` touches.

    Used to call the router's path-operation functions directly (bypassing HTTP/ASGI
    routing entirely) for the slash-guard tests below — see their docstrings for why.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.headers: dict[str, str] = {"Authorization": f"Bearer {token}"}


class _SpyPresetStore:
    """Records every call; used to prove the "/" guard fires BEFORE any store call."""

    def __init__(self) -> None:
        self.upsert_calls: list[tuple[Any, ...]] = []
        self.delete_calls: list[tuple[Any, ...]] = []
        self.list_calls: list[Any] = []

    async def upsert(
        self, tenant_id: uuid.UUID, preset_name: str, alias_key: str, target_model: str
    ) -> None:
        self.upsert_calls.append((tenant_id, preset_name, alias_key, target_model))

    async def list(self, tenant_id: uuid.UUID) -> list[TenantModelPreset]:
        self.list_calls.append(tenant_id)
        return []

    async def delete(self, tenant_id: uuid.UUID, preset_name: str, alias_key: str) -> bool:
        self.delete_calls.append((tenant_id, preset_name, alias_key))
        return False

    async def resolve(self, tenant_id: uuid.UUID, preset_name: str, alias_key: str) -> str | None:
        return None


# ===========================================================================
# test_create_then_list_round_trip
# ===========================================================================


@pytest.mark.asyncio
async def test_create_then_list_round_trip() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5-mini")

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T1", email="owner-t1@presets.io"
        )
        put_resp = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token),
            json={"target_model": "gpt5-5-mini"},
        )
        assert put_resp.status_code == 200, (
            f"expected 200, got {put_resp.status_code}: {put_resp.text}"
        )
        body = put_resp.json()
        assert body["preset_name"] == "cheap"
        assert body["alias_key"] == "opus"
        assert body["target_model"] == "gpt5-5-mini"
        assert "updated_at" in body

        list_resp = await client.get(PRESETS, headers=auth(token))
        assert list_resp.status_code == 200
        presets = list_resp.json()["presets"]
        assert len(presets) == 1
        assert presets[0]["preset_name"] == "cheap"
        assert presets[0]["alias_key"] == "opus"
        assert presets[0]["target_model"] == "gpt5-5-mini"

    await engine.dispose()


# ===========================================================================
# test_upsert_updates_existing_preset_in_place
# ===========================================================================


@pytest.mark.asyncio
async def test_upsert_updates_existing_preset_in_place() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5-mini")
    await seed_model(app, "gpt5-5")

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T2", email="owner-t2@presets.io"
        )
        first = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token),
            json={"target_model": "gpt5-5-mini"},
        )
        assert first.status_code == 200

        second = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token),
            json={"target_model": "gpt5-5"},
        )
        assert second.status_code == 200, f"expected 200, got {second.status_code}: {second.text}"
        assert second.json()["target_model"] == "gpt5-5"

        list_resp = await client.get(PRESETS, headers=auth(token))
        presets = list_resp.json()["presets"]
        matching = [p for p in presets if p["preset_name"] == "cheap" and p["alias_key"] == "opus"]
        assert len(matching) == 1, "upsert must update in place, not duplicate"
        assert matching[0]["target_model"] == "gpt5-5"

    await engine.dispose()


# ===========================================================================
# test_delete_removes_a_preset
# ===========================================================================


@pytest.mark.asyncio
async def test_delete_removes_a_preset() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5-mini")

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T3", email="owner-t3@presets.io"
        )
        await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token),
            json={"target_model": "gpt5-5-mini"},
        )

        del_resp = await client.delete(f"{PRESETS}/cheap/opus", headers=auth(token))
        assert del_resp.status_code == 204, (
            f"expected 204, got {del_resp.status_code}: {del_resp.text}"
        )
        assert del_resp.content == b""

        list_resp = await client.get(PRESETS, headers=auth(token))
        assert list_resp.json()["presets"] == []

    await engine.dispose()


# ===========================================================================
# test_delete_is_idempotent
# ===========================================================================


@pytest.mark.asyncio
async def test_delete_is_idempotent() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T4", email="owner-t4@presets.io"
        )
        first = await client.delete(f"{PRESETS}/cheap/opus", headers=auth(token))
        assert first.status_code == 204, (
            f"expected 204 (idempotent, not 404), got {first.status_code}: {first.text}"
        )
        second = await client.delete(f"{PRESETS}/cheap/opus", headers=auth(token))
        assert second.status_code == 204

        list_resp = await client.get(PRESETS, headers=auth(token))
        assert list_resp.json()["presets"] == []

    await engine.dispose()


# ===========================================================================
# test_tenant_isolation_on_list
# ===========================================================================


@pytest.mark.asyncio
async def test_tenant_isolation_on_list() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "model-a")

    async with client_for(app) as client:
        token_a, _tenant_a = await signup_tenant(
            client, tenant_name="T5A", email="owner-t5a@presets.io"
        )
        token_b, _tenant_b = await signup_tenant(
            client, tenant_name="T5B", email="owner-t5b@presets.io"
        )
        put_resp = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token_a),
            json={"target_model": "model-a"},
        )
        assert put_resp.status_code == 200

        list_b = await client.get(PRESETS, headers=auth(token_b))
        assert list_b.status_code == 200
        assert list_b.json()["presets"] == [], "tenant B must never see tenant A's preset"

        list_a = await client.get(PRESETS, headers=auth(token_a))
        assert len(list_a.json()["presets"]) == 1

    await engine.dispose()


# ===========================================================================
# test_tenant_isolation_on_delete
# ===========================================================================


@pytest.mark.asyncio
async def test_tenant_isolation_on_delete() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "model-a")

    async with client_for(app) as client:
        token_a, _tenant_a = await signup_tenant(
            client, tenant_name="T6A", email="owner-t6a@presets.io"
        )
        token_b, _tenant_b = await signup_tenant(
            client, tenant_name="T6B", email="owner-t6b@presets.io"
        )
        put_resp = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token_a),
            json={"target_model": "model-a"},
        )
        assert put_resp.status_code == 200

        del_b = await client.delete(f"{PRESETS}/cheap/opus", headers=auth(token_b))
        assert del_b.status_code == 204, (
            "tenant B's delete on a non-existent (for B) preset is idempotent 204"
        )

        list_a = await client.get(PRESETS, headers=auth(token_a))
        presets_a = list_a.json()["presets"]
        assert len(presets_a) == 1, "tenant A's preset must be untouched by tenant B's delete"
        assert presets_a[0]["target_model"] == "model-a"

    await engine.dispose()


# ===========================================================================
# test_reject_invalid_selector_colon
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_invalid_selector_colon() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5")

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T7", email="owner-t7@presets.io"
        )
        # "cheap%3A" decodes to "cheap:" — a colon-bearing preset_name segment.
        resp = await client.put(
            f"{PRESETS}/cheap%3A/opus",
            headers=auth(token),
            json={"target_model": "gpt5-5"},
        )
        assert_problem(resp, 400, "ERR_PRESET_SELECTOR_INVALID")

        list_resp = await client.get(PRESETS, headers=auth(token))
        assert list_resp.json()["presets"] == [], "no row must be created on rejection"

    await engine.dispose()


# ===========================================================================
# test_reject_slash_in_preset_name
# ===========================================================================


#
# NOTE on how these three are exercised — an empirically-confirmed deviation from the
# TASK.md §2 scenario's literal HTTP transcript (documented in full in the accompanying
# build report; TASK.md itself flagged this exact point as its lowest-confidence flag):
#
# Probed directly (httpx.ASGITransport + FastAPI, matching this repo's stack): a
# percent-encoded "/" (e.g. "cheap%2Fteam") is decoded into ``scope["path"]`` by the ASGI
# layer BEFORE Starlette's router ever matches it (mandated by the ASGI spec itself, not
# an httpx-only quirk — a real uvicorn server decodes identically). Against the frozen
# route shape ``PUT /{preset_name}/{alias_key}`` (a fixed 2-segment pattern), a decoded
# "/" turns one segment into two, so the request 404s at the ROUTING layer — it never
# reaches this router's handler, and therefore never reaches ``_reject_slash`` at all.
#
# The safety invariant TASK.md actually cares about — "no preset can ever be created
# with '/' in it" — holds regardless: a 404 never calls the store either. See
# ``test_percent_encoded_slash_via_http_never_reaches_router`` below for that HTTP-level
# proof. The three tests here instead call the router's path-operation functions
# DIRECTLY (bypassing ASGI routing) to prove the ``_reject_slash`` guard the contract
# mandates actually exists, is wired into both verbs, and fires before any store call —
# reachable today by any direct/non-HTTP caller (e.g. a future internal tool), and by any
# ASGI stack that does NOT pre-decode "/" the way this one does.
#


@pytest.mark.asyncio
async def test_reject_slash_in_preset_name() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5")

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T8", email="owner-t8@presets.io"
        )

    spy = _SpyPresetStore()
    app.state.tenant_model_preset_store = spy
    fake_request = _FakeRequest(app, token)

    async with sessionmaker() as session:
        with pytest.raises(ProblemError) as exc_info:
            await upsert_preset(
                "cheap/team",
                "opus",
                PresetUpsertBody(target_model="gpt5-5"),
                fake_request,  # type: ignore[arg-type]
                session,
            )
    assert exc_info.value.status == 400
    assert exc_info.value.code == "ERR_PRESET_SELECTOR_INVALID"
    assert spy.upsert_calls == [], "the guard must fire BEFORE any store call"

    await engine.dispose()


# ===========================================================================
# test_reject_slash_in_alias_key
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_slash_in_alias_key() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5")

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T9", email="owner-t9@presets.io"
        )

    spy = _SpyPresetStore()
    app.state.tenant_model_preset_store = spy
    fake_request = _FakeRequest(app, token)

    async with sessionmaker() as session:
        with pytest.raises(ProblemError) as exc_info:
            await upsert_preset(
                "cheap",
                "opus/team",
                PresetUpsertBody(target_model="gpt5-5"),
                fake_request,  # type: ignore[arg-type]
                session,
            )
    assert exc_info.value.status == 400
    assert exc_info.value.code == "ERR_PRESET_SELECTOR_INVALID"
    assert spy.upsert_calls == [], "the guard must fire BEFORE any store call"

    await engine.dispose()


# ===========================================================================
# test_reject_slash_on_delete
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_slash_on_delete() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T10", email="owner-t10@presets.io"
        )

    spy = _SpyPresetStore()
    app.state.tenant_model_preset_store = spy
    fake_request = _FakeRequest(app, token)

    async with sessionmaker() as session:
        with pytest.raises(ProblemError) as exc_info:
            await delete_preset(
                "cheap/team",
                "opus",
                fake_request,
                session,  # type: ignore[arg-type]
            )
    assert exc_info.value.status == 400
    assert exc_info.value.code == "ERR_PRESET_SELECTOR_INVALID"
    assert spy.delete_calls == [], "the guard must apply identically on DELETE"

    await engine.dispose()


# ===========================================================================
# test_percent_encoded_slash_via_http_never_reaches_router
# ===========================================================================


@pytest.mark.asyncio
async def test_percent_encoded_slash_via_http_never_reaches_router() -> None:
    """HTTP-level companion to the three direct-call guard tests above: a real request
    bearing a percent-encoded "/" 404s at the ASGI routing layer (route-segment-count
    mismatch, per the ASGI spec's mandated path-decoding — see the note above), on both
    PUT and DELETE. The safety invariant holds either way: no row is ever created."""
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5")

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T10B", email="owner-t10b@presets.io"
        )

        put_resp = await client.put(
            f"{PRESETS}/cheap%2Fteam/opus",
            headers=auth(token),
            json={"target_model": "gpt5-5"},
        )
        assert put_resp.status_code == 404

        del_resp = await client.delete(f"{PRESETS}/cheap%2Fteam/opus", headers=auth(token))
        assert del_resp.status_code == 404

        list_resp = await client.get(PRESETS, headers=auth(token))
        assert list_resp.json()["presets"] == [], "no row must be created either way"

    await engine.dispose()


# ===========================================================================
# test_reject_unknown_target_model
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_unknown_target_model() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    # no models seeded -> "not-a-real-model" is absent from the catalog

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="T11", email="owner-t11@presets.io"
        )
        resp = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token),
            json={"target_model": "not-a-real-model"},
        )
        assert_problem(resp, 400, "ERR_PRESET_TARGET_UNKNOWN")

        list_resp = await client.get(PRESETS, headers=auth(token))
        assert list_resp.json()["presets"] == [], "no row must be created on rejection"

    await engine.dispose()


# ===========================================================================
# test_reject_non_owner_role
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_non_owner_role() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5")

    async with client_for(app) as client:
        _owner_token, tenant_id = await signup_tenant(
            client, tenant_name="T12", email="owner-t12@presets.io"
        )
        member_jwt, _ = app.state.token_service.issue(
            user_id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            role=Role.MEMBER,
            email="member-t12@presets.io",
        )

        put_resp = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(member_jwt),
            json={"target_model": "gpt5-5"},
        )
        assert_problem(put_resp, 403, "ERR_AUTH_FORBIDDEN")

        del_resp = await client.delete(f"{PRESETS}/cheap/opus", headers=auth(member_jwt))
        assert_problem(del_resp, 403, "ERR_AUTH_FORBIDDEN")

        get_resp = await client.get(PRESETS, headers=auth(member_jwt))
        assert_problem(get_resp, 403, "ERR_AUTH_FORBIDDEN")

        store = app.state.tenant_model_preset_store
        assert await store.list(uuid.UUID(tenant_id)) == [], (
            "a forbidden write must not create a row"
        )

    await engine.dispose()


# ===========================================================================
# test_reject_missing_or_invalid_session_token
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_missing_or_invalid_session_token() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        # Need a tenant to exist so the only failing dimension is the missing/invalid token.
        await signup_tenant(client, tenant_name="T13", email="owner-t13@presets.io")

        no_token_resp = await client.get(PRESETS)
        assert_problem(no_token_resp, 401, "ERR_AUTH_INVALID_TOKEN")

        garbage_resp = await client.get(PRESETS, headers=auth("not-a-real-jwt"))
        assert_problem(garbage_resp, 401, "ERR_AUTH_INVALID_TOKEN")

    await engine.dispose()


# ===========================================================================
# test_ingress_resolves_newly_created_preset_immediately
# ===========================================================================


@pytest.mark.asyncio
async def test_ingress_resolves_newly_created_preset_immediately() -> None:
    """A preset created via the admin router is immediately visible through
    ``TenantModelPresetStore.resolve`` — the exact method preset-resolution-ingress's
    ``CompletionUseCase`` (and the 4 non-chat use cases) call at request time. Proves the
    two already-shipped/this-task layers compose with no cache/restart step."""
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)
    await seed_model(app, "gpt5-5-mini")

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="T14", email="owner-t14@presets.io"
        )
        put_resp = await client.put(
            f"{PRESETS}/cheap/opus",
            headers=auth(token),
            json={"target_model": "gpt5-5-mini"},
        )
        assert put_resp.status_code == 200

        store = app.state.tenant_model_preset_store
        resolved = await store.resolve(uuid.UUID(tenant_id), "cheap", "opus")
        assert resolved == "gpt5-5-mini", (
            "the real ingress-facing resolve() must see the row immediately, "
            "no restart/cache-invalidation step"
        )

    await engine.dispose()
