"""Behavioral tests for capabilities-admin-surface (v55 task 2).

Three tests — written RED first (fields/schemas absent before implementation).

SC1 — test_v1_models_stays_lean:
    GET /v1/models MUST NOT expose input_modalities; public OpenAI shape unchanged.
SC2 — test_admin_catalog_models_includes_input_modalities:
    GET /admin/catalog/models returns sorted list[str] for input_modalities, and
    id + pricing are byte-identical to /v1/models for the same model (no drift).
SC3 — test_admin_models_includes_input_modalities:
    GET /admin/models returns sorted list[str] for input_modalities alongside the
    enabled bool (owner JWT required).

Seeding strategy
----------------
Both SC1 and SC2 use /v1/models (backed by ListModelsForTenantUseCase), which
JOINs models x pricing_snapshots. The seed_model() helper inserts both rows
directly via SQL so specific input_modalities values can be controlled (the
repository _upsert_model does not include input_modalities in the values dict).

SC3 uses /admin/models which does a plain SELECT from models; no pricing_snapshots
needed, so the model-only helper is used.

Run from apps/gateway/:
    uv run pytest tests/catalog_input_capabilities --no-cov -p no:randomly -q
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Route constants
# ---------------------------------------------------------------------------

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
V1_MODELS = "/v1/models"
ADMIN_CATALOG_MODELS = "/admin/catalog/models"
ADMIN_MODELS = "/admin/models"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def seed_model_with_pricing(
    db: AsyncSession,
    model_id: str,
    *,
    name: str,
    input_modalities: str,
    prompt_usd_per_token: float = 1e-6,
    completion_usd_per_token: float = 2e-6,
) -> None:
    """Seed a model row (with input_modalities) plus a pricing_snapshot.

    The pricing_snapshot is required for ListModelsForTenantUseCase to return
    the model (it JOINs latest snapshot). input_modalities is set directly in
    the INSERT so we bypass the repository upsert which omits it.
    """
    await db.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, input_modalities) "
            "VALUES (:id, :name, 128000, true, :im) "
            "ON CONFLICT (id) DO UPDATE "
            "SET active = true, name = :name, input_modalities = :im"
        ),
        {"id": model_id, "name": name, "im": input_modalities},
    )
    await db.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "    (id, model_id, prompt_usd_per_token, completion_usd_per_token) "
            "VALUES (:sid, :mid, :prompt, :comp)"
        ),
        {
            "sid": str(uuid.uuid4()),
            "mid": model_id,
            "prompt": prompt_usd_per_token,
            "comp": completion_usd_per_token,
        },
    )
    await db.commit()


async def seed_model_only(
    db: AsyncSession,
    model_id: str,
    *,
    name: str,
    input_modalities: str,
) -> None:
    """Seed just a model row (no pricing snapshot).

    Sufficient for /admin/models which does a plain SELECT from models;
    does NOT work for /v1/models or /admin/catalog/models because those
    endpoints JOIN pricing_snapshots via ListModelsForTenantUseCase.
    """
    await db.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, input_modalities) "
            "VALUES (:id, :name, 128000, true, :im) "
            "ON CONFLICT (id) DO UPDATE "
            "SET active = true, name = :name, input_modalities = :im"
        ),
        {"id": model_id, "name": name, "im": input_modalities},
    )
    await db.commit()


# ---------------------------------------------------------------------------
# SC1 — GET /v1/models stays lean (no input_modalities)
# ---------------------------------------------------------------------------


async def test_v1_models_stays_lean(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """GET /v1/models MUST NOT expose input_modalities — public OpenAI shape unchanged.

    Even though gpt-4o has input_modalities='text,image' in the DB, the public
    endpoint must never surface it.  /v1/models is the byte-identical shape
    Anthropic/OpenRouter clients expect; adding a field is a breaking change.

    RED reason: this test would become red if a future refactor accidentally adds
    input_modalities to ModelItem — it guards that regression.  Before implementation
    it may be green (field is absent from the current lean schema), but it must stay
    green throughout and after the build.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="LeanTenant", email="lean@cap-test.io"
    )
    await seed_model_with_pricing(
        db_session,
        "openai/gpt-4o",
        name="GPT-4o",
        input_modalities="text,image",
    )
    await seed_model_with_pricing(
        db_session,
        "openai/whisper-1",
        name="Whisper 1",
        input_modalities="audio",
    )

    resp = await client.get(V1_MODELS, headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200, f"GET /v1/models failed: {resp.text}"

    data = resp.json()["data"]
    assert len(data) == 2, f"expected 2 models; got {len(data)}"

    for entry in data:
        assert "input_modalities" not in entry, (
            f"input_modalities MUST NOT appear in GET /v1/models; "
            f"found in entry id={entry.get('id')!r}"
        )


# ---------------------------------------------------------------------------
# SC2 — GET /admin/catalog/models exposes input_modalities (sorted)
# ---------------------------------------------------------------------------


async def test_admin_catalog_models_includes_input_modalities(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """GET /admin/catalog/models returns sorted list[str] for each model's input_modalities.

    Asserts:
    - Each entry contains an 'input_modalities' key as a sorted list.
    - gpt-4o (stored 'text,image') -> ['image', 'text'] (canonical sort order).
    - whisper-1 (stored 'audio') -> ['audio'].
    - id + prompt_per_token + completion_per_token are byte-identical to /v1/models
      for the same model (pricing must not drift between the two endpoints).

    RED reason: before build, 'input_modalities' is absent from the AdminCatalogModelItem
    schema (and the AdminCatalogModelsListResponse envelope doesn't exist yet), so
    'input_modalities' will be missing from the admin catalog response.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="AdminCatTenant", email="admcat@cap-test.io"
    )
    await seed_model_with_pricing(
        db_session,
        "openai/gpt-4o",
        name="GPT-4o",
        input_modalities="text,image",
        prompt_usd_per_token=5e-6,
        completion_usd_per_token=15e-6,
    )
    await seed_model_with_pricing(
        db_session,
        "openai/whisper-1",
        name="Whisper 1",
        input_modalities="audio",
        prompt_usd_per_token=0.1e-6,
        completion_usd_per_token=0.0,
    )

    headers = {"Authorization": f"Bearer {jwt}"}
    v1_resp = await client.get(V1_MODELS, headers=headers)
    admin_resp = await client.get(ADMIN_CATALOG_MODELS, headers=headers)

    assert v1_resp.status_code == 200, f"GET /v1/models failed: {v1_resp.text}"
    assert admin_resp.status_code == 200, f"GET /admin/catalog/models failed: {admin_resp.text}"

    v1_by_id: dict[str, Any] = {m["id"]: m for m in v1_resp.json()["data"]}
    admin_by_id: dict[str, Any] = {m["id"]: m for m in admin_resp.json()["data"]}

    assert "openai/gpt-4o" in admin_by_id, "gpt-4o missing from /admin/catalog/models"
    assert "openai/whisper-1" in admin_by_id, "whisper-1 missing from /admin/catalog/models"

    # input_modalities must be a sorted list (canonical sort: text < image < audio by position)
    gpt4o_modalities = admin_by_id["openai/gpt-4o"].get("input_modalities")
    assert gpt4o_modalities == ["image", "text"], (
        f"gpt-4o: expected ['image', 'text'] (sorted), got {gpt4o_modalities!r}"
    )

    whisper_modalities = admin_by_id["openai/whisper-1"].get("input_modalities")
    assert whisper_modalities == ["audio"], (
        f"whisper-1: expected ['audio'], got {whisper_modalities!r}"
    )

    # Pricing and id must match /v1/models exactly — no drift between endpoints
    for model_id in ("openai/gpt-4o", "openai/whisper-1"):
        assert admin_by_id[model_id]["id"] == v1_by_id[model_id]["id"]
        assert admin_by_id[model_id]["prompt_per_token"] == pytest.approx(
            v1_by_id[model_id]["prompt_per_token"]
        )
        assert admin_by_id[model_id]["completion_per_token"] == pytest.approx(
            v1_by_id[model_id]["completion_per_token"]
        )


# ---------------------------------------------------------------------------
# SC3 — GET /admin/models exposes input_modalities alongside enabled bool
# ---------------------------------------------------------------------------


async def test_admin_models_includes_input_modalities(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """GET /admin/models returns sorted list[str] for input_modalities alongside enabled.

    /admin/models is owner/admin-gated and does NOT include pricing (omitted by design).
    After this task, each AdminModelItem must also carry input_modalities.

    Asserts:
    - gpt-4o (stored 'text,image') -> ['image', 'text'] (sorted).
    - whisper-1 (stored 'audio') -> ['audio'].
    - Both entries still carry an 'enabled' boolean (no override -> default True).

    RED reason: before build, AdminModelItem lacks the input_modalities field and the
    SELECT in get_admin_models doesn't retrieve ModelRow.input_modalities.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="AdminModTenant", email="admmod@cap-test.io"
    )
    # /admin/models does a plain SELECT from models — no pricing_snapshots needed.
    await seed_model_only(
        db_session, "openai/gpt-4o", name="GPT-4o", input_modalities="text,image"
    )
    await seed_model_only(
        db_session, "openai/whisper-1", name="Whisper 1", input_modalities="audio"
    )

    resp = await client.get(ADMIN_MODELS, headers={"Authorization": f"Bearer {jwt}"})
    assert resp.status_code == 200, f"GET /admin/models failed: {resp.text}"

    data_by_id: dict[str, Any] = {m["id"]: m for m in resp.json()["data"]}

    assert "openai/gpt-4o" in data_by_id, "gpt-4o missing from /admin/models"
    assert "openai/whisper-1" in data_by_id, "whisper-1 missing from /admin/models"

    # input_modalities: sorted list
    gpt4o_modalities = data_by_id["openai/gpt-4o"].get("input_modalities")
    assert gpt4o_modalities == ["image", "text"], (
        f"gpt-4o: expected ['image', 'text'] (sorted), got {gpt4o_modalities!r}"
    )
    whisper_modalities = data_by_id["openai/whisper-1"].get("input_modalities")
    assert whisper_modalities == ["audio"], (
        f"whisper-1: expected ['audio'], got {whisper_modalities!r}"
    )

    # enabled bool must still be present (feature not regressed)
    for mid in ("openai/gpt-4o", "openai/whisper-1"):
        assert "enabled" in data_by_id[mid], f"'enabled' missing from {mid!r} entry"
        assert data_by_id[mid]["enabled"] is True, (
            f"{mid!r}: no override row -> default enabled=True, got {data_by_id[mid]['enabled']!r}"
        )
