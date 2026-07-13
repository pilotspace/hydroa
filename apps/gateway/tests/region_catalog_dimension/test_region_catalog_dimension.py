"""Red suite for region-catalog-dimension — TASK.md §4 (contract FROZEN @ v1).

13 tests, one per scenario in TASK.md §2. All tests MUST fail (ImportError,
AttributeError, or assertion failure) before the Build phase because the
Region/normalize_region/InvalidRegionError/ModelRow.region/BEDROCK_SEED_MODELS
symbols do not exist yet.

SC1  — pre-existing catalog row defaults to global region                      (M1, M2)
SC2  — dynamic OpenRouter sync leaves region global                            (M1, M2)
SC3  — normalize_region accepts a valid token                                  (M3)
SC4  — normalize_region rejects an unknown token                               (M3, R1)
SC5  — Bedrock EU seed row syncs with region=eu                                (M4)
SC6  — Bedrock US seed row syncs with region=us and a distinct id              (M4, R3)
SC7  — admin catalog surface exposes region                                    (M5)
SC8  — admin models surface exposes region                                     (M5)
SC9  — public models list stays byte-identical                                 (M5)
SC10 — region is not admin-editable                                            (M6)
SC11 — Bedrock seed rows survive the deactivation sweep                        (M7)
SC12 — no Vertex row is ever seeded by this task                               (R2)
SC13 — static seed omitting region defaults global, not rejected               (R4)

Run only this suite (from apps/gateway/):
  uv run pytest tests/region_catalog_dimension/ -q --no-cov -p no:randomly
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

SYNC = "/internal/catalog/sync"
MODELS = "/v1/models"
ADMIN_CATALOG_MODELS = "/admin/catalog/models"
ADMIN_MODELS = "/admin/models"

_SONNET_EU = "eu.anthropic.claude-3-5-sonnet-20241022-v2:0"
_SONNET_US = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

# The exact public-shape key set /v1/models must keep byte-identical (task 1 + every
# additive task since — catalog-pricing-fields, gpt-realtime-pricing-fields — none of
# them ever added a key here; this task must not be the first).
_MODEL_ITEM_KEYS = frozenset(
    {
        "id",
        "name",
        "context_length",
        "prompt_per_token",
        "completion_per_token",
        "object",
        "prompt_usd_per_1m",
        "completion_usd_per_1m",
        "cached_input_usd_per_1m",
        "audio_prompt_usd_per_1m",
        "audio_completion_usd_per_1m",
        "audio_cached_usd_per_1m",
    }
)


class _FakeSource:
    """Minimal CatalogSource-shaped double — chat + embedding model lists are configurable."""

    def __init__(
        self,
        chat_models: list[CatalogModel] | None = None,
        embedding_models: list[CatalogModel] | None = None,
    ) -> None:
        self.chat_models = chat_models or []
        self.embedding_models = embedding_models or []

    async def list_models(self) -> AsyncIterator[CatalogModel]:
        for model in self.chat_models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[CatalogModel]:
        for model in self.embedding_models:
            yield model


def _install_composite(app: Any, fake_primary: _FakeSource) -> None:
    """Install a CompositeCatalogSource(fake_primary, BEDROCK_SEED_MODELS) on app.state."""
    from gateway.catalog.infrastructure.bedrock_seed import BEDROCK_SEED_MODELS
    from gateway.catalog.infrastructure.composite_source import CompositeCatalogSource

    app.state.catalog_source = CompositeCatalogSource(
        primary=fake_primary, static_models=BEDROCK_SEED_MODELS
    )


async def _signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str = "RegionCatalogTest",
    email: str = "rcd@example.io",
    password: str = "region catalog dimension test",
) -> str:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    token: str = login.json()["access_token"]
    return token


# ===========================================================================
# SC1 — pre-existing catalog row defaults to global region
# ===========================================================================


async def test_sc1_preexisting_row_defaults_to_global_region(db_session: AsyncSession) -> None:
    """A row inserted without region gets region='global' via the server_default.

    RED reason: ModelRow ORM class lacks the 'region' column — either the column
    is absent from the mapper (AttributeError) or the INSERT/refresh path fails.
    """
    from sqlalchemy import inspect as sa_inspect

    from gateway.catalog.infrastructure.orm import ModelRow

    mapper = sa_inspect(ModelRow)
    column_names = {c.key for c in mapper.columns}
    assert "region" in column_names, f"ModelRow ORM must have 'region' column; got: {column_names}"

    row = ModelRow(
        id="test-preexisting-sc1",
        name="Pre-existing Model",
        context_length=4096,
        active=True,
        modality="chat",
        provider="openrouter",
        # region intentionally omitted — must default to 'global'
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.region == "global", f"region must default to 'global'; got {row.region!r}"
    # No other column changed.
    assert row.id == "test-preexisting-sc1"
    assert row.modality == "chat"
    assert row.provider == "openrouter"
    assert row.context_length == 4096


# ===========================================================================
# SC2 — dynamic OpenRouter sync leaves region global
# ===========================================================================


async def test_sc2_dynamic_openrouter_sync_leaves_region_global(db_session: AsyncSession) -> None:
    """A CatalogModel with no region set persists as region='global' via sync_catalog.

    RED reason: ModelRow ORM lacks 'region' → the upsert either omits the column
    (server_default applies, masking a real red) or raises; CatalogModel itself
    lacks a 'region' attribute → AttributeError constructing the model below is
    the expected first failure once the domain layer already has the field but
    infra doesn't wire it through — either way this fails before Build.
    """
    repo = SqlAlchemyCatalogRepository(db_session)
    model = CatalogModel(
        id="anthropic/claude-opus-4-sc2",
        name="Claude Opus 4",
        context_length=200_000,
        prompt_usd_per_token=15e-6,
        completion_usd_per_token=75e-6,
        provider="openrouter",
        # region NOT provided — must default to 'global'
    )

    await repo.sync_catalog([model])

    row = (
        await db_session.execute(
            text("SELECT region FROM models WHERE id = :mid"),
            {"mid": "anthropic/claude-opus-4-sc2"},
        )
    ).one()
    assert row.region == "global", f"expected region='global'; got {row.region!r}"


# ===========================================================================
# SC3 — normalize_region accepts a valid token
# ===========================================================================


def test_sc3_normalize_region_accepts_valid_token() -> None:
    """normalize_region('eu') returns 'eu'.

    RED reason: normalize_region does not exist → ImportError.
    """
    from gateway.catalog.domain.entities import normalize_region  # type: ignore[attr-defined]

    assert normalize_region("eu") == "eu"
    assert normalize_region("us") == "us"
    assert normalize_region("ap") == "ap"
    assert normalize_region("global") == "global"


# ===========================================================================
# SC4 — normalize_region rejects an unknown token
# ===========================================================================


async def test_sc4_normalize_region_rejects_unknown_token(db_session: AsyncSession) -> None:
    """normalize_region('apac') raises InvalidRegionError(code='invalid_region').

    RED reason: normalize_region / InvalidRegionError do not exist → ImportError.

    Also confirms no catalog row is written as a side effect of the rejected token
    (the raise happens before any DB interaction — no model row for the probe id
    exists before or after).
    """
    from gateway.catalog.domain.entities import normalize_region  # type: ignore[attr-defined]
    from gateway.catalog.domain.errors import InvalidRegionError  # type: ignore[attr-defined]

    probe_id = "region-sc4-should-never-exist"

    with pytest.raises(InvalidRegionError) as exc_info:
        normalize_region("apac")

    error = exc_info.value
    assert error.code == "invalid_region", (  # type: ignore[attr-defined]
        f"Expected error.code='invalid_region'; got {error.code!r}"  # type: ignore[attr-defined]
    )

    row = (
        await db_session.execute(text("SELECT id FROM models WHERE id = :mid"), {"mid": probe_id})
    ).one_or_none()
    assert row is None, "no catalog row must be written when normalize_region rejects a token"


# ===========================================================================
# SC5 — Bedrock EU seed row syncs with region=eu
# ===========================================================================


async def test_sc5_bedrock_eu_seed_row_syncs_with_region_eu(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """BEDROCK_SEED_MODELS' eu. row persists with region='eu', provider='bedrock'.

    RED reason: bedrock_seed.py does not exist → ImportError in _install_composite.
    """
    _install_composite(app, _FakeSource())

    resp = await client.post(SYNC)
    assert resp.status_code == 200, resp.text

    row = (
        await db_session.execute(
            text("SELECT region, provider, active FROM models WHERE id = :mid"),
            {"mid": _SONNET_EU},
        )
    ).one_or_none()
    assert row is not None, f"expected a ModelRow for {_SONNET_EU!r} after sync"
    assert row.region == "eu", f"expected region='eu'; got {row.region!r}"
    assert row.provider == "bedrock", f"expected provider='bedrock'; got {row.provider!r}"
    assert row.active is True


# ===========================================================================
# SC6 — Bedrock US seed row syncs with region=us and a distinct id
# ===========================================================================


async def test_sc6_bedrock_us_seed_row_distinct_id_no_integrity_error(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """BEDROCK_SEED_MODELS' us. row persists alongside the eu. row with a DIFFERENT id.

    RED reason: bedrock_seed.py does not exist → ImportError.
    """
    _install_composite(app, _FakeSource())

    resp = await client.post(SYNC)
    assert resp.status_code == 200, resp.text  # no IntegrityError surfaced as a 500

    rows = (
        await db_session.execute(
            text("SELECT id, region FROM models WHERE id = ANY(:ids)"),
            {"ids": [_SONNET_US, _SONNET_EU]},
        )
    ).all()
    by_id = {r.id: r.region for r in rows}
    assert set(by_id) == {_SONNET_US, _SONNET_EU}, (
        f"expected both {_SONNET_US!r} and {_SONNET_EU!r} as DISTINCT rows; got {set(by_id)}"
    )
    assert by_id[_SONNET_US] == "us"
    assert by_id[_SONNET_EU] == "eu"


# ===========================================================================
# SC7 — admin catalog surface exposes region
# ===========================================================================


async def test_sc7_admin_catalog_surface_exposes_region(
    client: httpx.AsyncClient, app: Any
) -> None:
    """GET /admin/catalog/models exposes region='eu' for the seeded Bedrock EU row.

    RED reason: AdminCatalogModelItem lacks a 'region' field → KeyError on
    m["region"] below (pydantic silently drops unknown response data).
    """
    _install_composite(app, _FakeSource())
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client, email="rcd-sc7@example.io")
    resp = await client.get(ADMIN_CATALOG_MODELS, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text

    data = {m["id"]: m for m in resp.json()["data"]}
    assert _SONNET_EU in data, f"{_SONNET_EU} missing from /admin/catalog/models"
    assert data[_SONNET_EU]["region"] == "eu", (
        f"expected region='eu'; got {data[_SONNET_EU].get('region')!r}"
    )


# ===========================================================================
# SC8 — admin models surface exposes region
# ===========================================================================


async def test_sc8_admin_models_surface_exposes_region(client: httpx.AsyncClient, app: Any) -> None:
    """GET /admin/models exposes region='eu' for the seeded Bedrock EU row.

    RED reason: AdminModelItem lacks a 'region' field.
    """
    _install_composite(app, _FakeSource())
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client, email="rcd-sc8@example.io")
    resp = await client.get(ADMIN_MODELS, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text

    data = {m["id"]: m for m in resp.json()["data"]}
    assert _SONNET_EU in data, f"{_SONNET_EU} missing from /admin/models"
    assert data[_SONNET_EU]["region"] == "eu", (
        f"expected region='eu'; got {data[_SONNET_EU].get('region')!r}"
    )


# ===========================================================================
# SC9 — public models list stays byte-identical
# ===========================================================================


async def test_sc9_public_models_list_stays_byte_identical(
    client: httpx.AsyncClient, app: Any
) -> None:
    """GET /v1/models never carries a 'region' key; every other key is unchanged.

    RED reason (this is a POSITIVE assertion the current codebase should already
    pass) — the test suite fails RED overall because _install_composite requires
    bedrock_seed.py, which doesn't exist yet; once Build lands, this scenario
    guards against region ever leaking onto the lean public shape.
    """
    _install_composite(app, _FakeSource())
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client, email="rcd-sc9@example.io")
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text

    data = resp.json()["data"]
    assert data, "expected at least one model in /v1/models"
    for item in data:
        assert "region" not in item, f"/v1/models must not expose 'region'; got keys {item.keys()}"
        assert set(item.keys()) == _MODEL_ITEM_KEYS, (
            f"ModelItem shape drifted: {set(item.keys()) - _MODEL_ITEM_KEYS} extra, "
            f"{_MODEL_ITEM_KEYS - set(item.keys())} missing"
        )


# ===========================================================================
# SC10 — region is not admin-editable
# ===========================================================================


async def test_sc10_region_is_not_admin_editable(client: httpx.AsyncClient, app: Any) -> None:
    """PUT /admin/models/{model_id} never changes region — only 'enabled' toggles.

    RED reason: AdminModelItem lacks 'region'; PutModelRequest has no region field
    to attempt writing through in the first place (confirms it can't even be
    ASKED to change region), and the response body has no region to assert on.
    """
    _install_composite(app, _FakeSource())
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client, email="rcd-sc10@example.io")
    headers = {"Authorization": f"Bearer {token}"}

    before = await client.get(ADMIN_MODELS, headers=headers)
    assert before.status_code == 200
    before_region = {m["id"]: m["region"] for m in before.json()["data"]}[_SONNET_US]
    assert before_region == "us"

    # Attempt to smuggle a region change through the PUT body — the endpoint has
    # no region field on PutModelRequest, so an extra key is silently ignored by
    # pydantic (or rejected if strict); either way it must never reach the row.
    put_resp = await client.put(
        f"{ADMIN_MODELS}/{_SONNET_US}",
        json={"enabled": False, "region": "ap"},
        headers=headers,
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json()["enabled"] is False
    assert put_resp.json()["region"] == "us", (
        "PUT response must echo the row's existing region, never the smuggled value"
    )

    after = await client.get(ADMIN_MODELS, headers=headers)
    after_region = {m["id"]: m["region"] for m in after.json()["data"]}[_SONNET_US]
    assert after_region == "us", f"region must be unchanged by PUT; got {after_region!r}"


# ===========================================================================
# SC11 — Bedrock seed rows survive the deactivation sweep
# ===========================================================================


async def test_sc11_bedrock_seed_rows_survive_deactivation_sweep(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """A second sync with a wholly different OpenRouter feed leaves Bedrock rows active.

    RED reason: bedrock_seed.py does not exist → ImportError in _install_composite.
    """
    _install_composite(
        app,
        _FakeSource(
            chat_models=[
                CatalogModel(
                    id="anthropic/claude-opus-4-sc11",
                    name="Claude Opus 4",
                    context_length=200_000,
                    prompt_usd_per_token=15e-6,
                    completion_usd_per_token=75e-6,
                    provider="openrouter",
                )
            ]
        ),
    )
    assert (await client.post(SYNC)).status_code == 200

    # Second sync: the dynamic feed now returns a COMPLETELY different model.
    _install_composite(
        app,
        _FakeSource(
            chat_models=[
                CatalogModel(
                    id="anthropic/claude-sonnet-4-sc11",
                    name="Claude Sonnet 4",
                    context_length=200_000,
                    prompt_usd_per_token=3e-6,
                    completion_usd_per_token=15e-6,
                    provider="openrouter",
                )
            ]
        ),
    )
    assert (await client.post(SYNC)).status_code == 200

    from gateway.catalog.infrastructure.bedrock_seed import BEDROCK_SEED_MODELS

    ids = [m.id for m in BEDROCK_SEED_MODELS]
    rows = (
        await db_session.execute(
            text("SELECT id, active FROM models WHERE id = ANY(:ids)"), {"ids": ids}
        )
    ).all()
    by_id = {r.id: r.active for r in rows}
    assert set(by_id) == set(ids), (
        f"expected all Bedrock seed rows present: {set(ids) - set(by_id)}"
    )
    assert all(active is True for active in by_id.values()), (
        f"Bedrock seed rows must survive a second sync with a different dynamic feed: {by_id}"
    )


# ===========================================================================
# SC12 — no Vertex row is ever seeded by this task
# ===========================================================================


async def test_sc12_no_vertex_row_ever_seeded(app: Any) -> None:
    """No CatalogModel in THIS task's static seed lists carries provider='vertex'.

    RED reason: bedrock_seed.py does not exist → ImportError.

    Cross-task amendment (2026-07-12, integration of the sibling vertex-adapter task,
    FROZEN @ v1): the original second assertion — `"vertex" not in
    app.state.chat_adapters`, rationale "no adapter exists" — was a point-in-time
    guard that this task's own §3 scope-cut anticipated superseding ("Vertex ships in
    the sibling vertex-adapter task"). That sibling contract now registers the
    adapter and seeds provider='vertex' rows via its OWN vertex_seed.py (pinned by
    tests/vertex_catalog_seed). This task's enduring invariant — its three seed
    lists never smuggle a vertex row — is unchanged below.
    """
    from gateway.catalog.infrastructure.bedrock_seed import BEDROCK_SEED_MODELS
    from gateway.catalog.infrastructure.gpt_realtime_seed import GPT_REALTIME_SEED_MODELS
    from gateway.catalog.infrastructure.minimax_seed import MINIMAX_SEED_MODELS

    for seed_list in (MINIMAX_SEED_MODELS, GPT_REALTIME_SEED_MODELS, BEDROCK_SEED_MODELS):
        for model in seed_list:
            assert model.provider != "vertex", (
                f"{model.id!r} must not carry provider='vertex' (scope-cut, §3)"
            )


# ===========================================================================
# SC13 — static seed omitting region defaults global, not rejected
# ===========================================================================


async def test_sc13_static_seed_omitting_region_defaults_global_not_rejected(
    db_session: AsyncSession,
) -> None:
    """A CatalogModel constructed WITHOUT a region kwarg syncs as region='global'.

    RED reason: CatalogModel has no 'region' field → TypeError is NOT raised
    (region is additive/optional everywhere), but the persisted row would lack a
    'region' column to read back — AttributeError/ORM failure is the expected
    first red.
    """
    repo = SqlAlchemyCatalogRepository(db_session)
    model = CatalogModel(
        id="omitted-region-sc13",
        name="Omitted Region Model",
        context_length=8192,
        prompt_usd_per_token=1e-6,
        completion_usd_per_token=2e-6,
        provider="openrouter",
        # region NOT provided
    )

    await repo.sync_catalog([model])

    row = (
        await db_session.execute(
            text("SELECT region FROM models WHERE id = :mid"),
            {"mid": "omitted-region-sc13"},
        )
    ).one()
    assert row.region == "global", f"expected region='global'; got {row.region!r}"


# ===========================================================================
# Post-verify pin (2026-07-12) — conflict-update rewrites region on a genuine
# value change for the SAME id. Closes the verify-round 🟡 residue: SC11's two
# sync cycles varied the dynamic FEED, never the region of one id, so nothing
# in the frozen suite proved the `_upsert_model` conflict-update `set_` clause
# actually carries region (the verify's mutation test removed it and all 13
# tests stayed green). Additive only — no frozen §4 test touched.
# ===========================================================================


async def test_pin_resync_rewrites_region_on_value_change(db_session: AsyncSession) -> None:
    """Same catalog id synced with region='us' then region='eu' → row flips to 'eu'.

    Pins the builder's disclosed judgment call (region written on BOTH insert and
    conflict-update, unlike input_modalities' frozen no-clobber): a region change
    upstream must be re-affirmed on the next sync cycle, never silently stale.
    """
    repo = SqlAlchemyCatalogRepository(db_session)
    base = {
        "id": "us.anthropic.claude-pin-resync-v1:0",
        "name": "Pin Resync",
        "context_length": 200_000,
        "prompt_usd_per_token": 3e-6,
        "completion_usd_per_token": 15e-6,
        "provider": "bedrock",
    }

    await repo.sync_catalog([CatalogModel(**base, region="us")])
    await repo.sync_catalog([CatalogModel(**base, region="eu")])

    row = (
        await db_session.execute(
            text("SELECT region FROM models WHERE id = :mid"), {"mid": base["id"]}
        )
    ).one()
    assert row.region == "eu", (
        f"conflict-update must rewrite region on re-sync; got {row.region!r} (stale 'us' means"
        " the set_ clause silently dropped region)"
    )
