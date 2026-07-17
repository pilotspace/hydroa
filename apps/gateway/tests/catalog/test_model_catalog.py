"""Red suite for model-catalog (contract DRAFT — spec frozen, awaiting build).

One test per scenario in .add/tasks/model-catalog/TASK.md §2.
Asserts observable behavior only — endpoints, problem+json codes, DB row effects,
and marked-up price arithmetic.

FakeCatalogSource is injected via app.state / FastAPI dependency override so no
real HTTP ever leaves this process (CatalogSource is a typing.Protocol port).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_JWT_SECRET

SYNC = "/internal/catalog/sync"
MODELS = "/v1/models"

# ---------------------------------------------------------------------------
# Fake domain types (mirror what gateway.catalog.domain will define)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeCatalogModel:
    """Mirrors the CatalogModel value object the domain port will return."""

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float
    # openrouter-embeddings-routing TASK.md §3: CatalogModel.modality is now read
    # by _upsert_model; mirror its "chat" default so this suite is unaffected.
    modality: str = "chat"
    # minimax-catalog-seed TASK.md §3: CatalogModel.provider/.input_modalities are now
    # read by _upsert_model too; mirror their real defaults so this suite is unaffected.
    provider: str = "openrouter"
    input_modalities: str = "text"
    # catalog-pricing-fields TASK.md §3: CatalogModel.cached_input_usd_per_token is now
    # read by _insert_snapshot too; mirror its real default so this suite is unaffected.
    cached_input_usd_per_token: float | None = None
    # gpt-realtime-pricing-fields TASK.md §3: CatalogModel.audio_* fields are now read
    # by _insert_snapshot too; mirror their real defaults so this suite is unaffected.
    audio_prompt_usd_per_token: float | None = None
    audio_completion_usd_per_token: float | None = None
    audio_cached_usd_per_token: float | None = None
    # region-catalog-dimension TASK.md §3: CatalogModel.region is now read by
    # _upsert_model too; mirror its real default so this suite is unaffected.
    region: str = "global"
    # catalog-db-seed TASK.md §3 (FROZEN @ v1): CatalogModel.cache_creation_usd_per_token/
    # .pricing_unit/.unit_usd_per_unit are now read by _insert_snapshot too; mirror their
    # real defaults so this suite is unaffected.
    cache_creation_usd_per_token: float | None = None
    pricing_unit: str = "per_token"
    unit_usd_per_unit: float | None = None


class FakeCatalogSource:
    """In-memory stub implementing the CatalogSource Protocol port.

    Configure `models` with the desired return value before each test, or set
    `raise_unavailable=True` to simulate an upstream network failure.
    """

    def __init__(
        self,
        models: list[FakeCatalogModel] | None = None,
        *,
        raise_unavailable: bool = False,
    ) -> None:
        self.models = models or []
        self.raise_unavailable = raise_unavailable

    async def list_models(self) -> AsyncIterator[FakeCatalogModel]:
        if self.raise_unavailable:
            # The production adapter raises CatalogSourceUnavailableError;
            # here we use the same name so the use case's except clause fires.
            from gateway.catalog.domain.errors import CatalogSourceUnavailableError

            raise CatalogSourceUnavailableError("fake upstream unavailable")
        for model in self.models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[FakeCatalogModel]:
        # openrouter-embeddings-routing TASK.md §3: CatalogSource grew this sibling
        # method. This suite never exercises embeddings — always succeeds with zero
        # rows (SyncCatalogUseCase.execute() calls it unconditionally now).
        return
        yield  # pragma: no cover — makes this an async generator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OPUS = FakeCatalogModel(
    id="anthropic/claude-opus-4",
    name="Claude Opus 4",
    context_length=200_000,
    prompt_usd_per_token=15e-6,
    completion_usd_per_token=75e-6,
)
_SONNET = FakeCatalogModel(
    id="anthropic/claude-sonnet-4",
    name="Claude Sonnet 4",
    context_length=200_000,
    prompt_usd_per_token=3e-6,
    completion_usd_per_token=15e-6,
)


def _install_fake_source(app: Any, fake: FakeCatalogSource) -> None:
    """Override the catalog_source stored on app.state (composition root wires it)."""
    app.state.catalog_source = fake


async def _signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str = "Acme",
    email: str = "ada@acme.io",
    password: str = "correct horse battery",
) -> str:
    """Helper: signup + login, return bearer token."""
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


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    body: dict[str, Any] = resp.json()
    assert body["code"] == code
    assert body["status"] == status
    assert "title" in body
    return body


async def _snapshot_count(db: AsyncSession, model_id: str) -> int:
    row = await db.execute(
        text("SELECT count(*) FROM pricing_snapshots WHERE model_id = :mid"),
        {"mid": model_id},
    )
    return int(row.scalar_one())


async def _model_active(db: AsyncSession, model_id: str) -> bool | None:
    row = await db.execute(
        text("SELECT active FROM models WHERE id = :mid"),
        {"mid": model_id},
    )
    result = row.scalar_one_or_none()
    return bool(result) if result is not None else None


# ---------------------------------------------------------------------------
# §2 Scenario: sync populates catalog from source
# ---------------------------------------------------------------------------


async def test_sync_populates_catalog(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """Arrange FakeCatalogSource with 2 models; act POST sync; assert rows written."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS, _SONNET]))

    resp = await client.post(SYNC)

    assert resp.status_code == 200
    body = resp.json()
    assert body["synced"] == 2

    model_rows = (
        await db_session.execute(text("SELECT count(*) FROM models WHERE active = true"))
    ).scalar_one()
    assert int(model_rows) == 2

    snap_rows = (
        await db_session.execute(text("SELECT count(*) FROM pricing_snapshots"))
    ).scalar_one()
    assert int(snap_rows) == 2


# ---------------------------------------------------------------------------
# §2 Scenario: sync is idempotent when prices unchanged
# ---------------------------------------------------------------------------


async def test_sync_idempotent_when_prices_unchanged(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """Second sync with identical prices must not append a new snapshot row."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    first = await client.post(SYNC)
    assert first.status_code == 200

    # Same source data — re-install to be explicit
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    second = await client.post(SYNC)
    assert second.status_code == 200

    count = await _snapshot_count(db_session, _OPUS.id)
    assert count == 1, "idempotent sync must not duplicate snapshot rows"


# ---------------------------------------------------------------------------
# §2 Scenario: sync appends a new snapshot when price changes
# ---------------------------------------------------------------------------


async def test_sync_appends_snapshot_on_price_change(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """Re-sync with a changed price must append a second snapshot row."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    assert (await client.post(SYNC)).status_code == 200

    opus_v2 = FakeCatalogModel(
        id=_OPUS.id,
        name=_OPUS.name,
        context_length=_OPUS.context_length,
        prompt_usd_per_token=20e-6,  # price went up
        completion_usd_per_token=_OPUS.completion_usd_per_token,
    )
    _install_fake_source(app, FakeCatalogSource(models=[opus_v2]))
    assert (await client.post(SYNC)).status_code == 200

    count = await _snapshot_count(db_session, _OPUS.id)
    assert count == 2, "price change must produce a new snapshot row"

    latest_prompt = (
        await db_session.execute(
            text(
                "SELECT prompt_usd_per_token FROM pricing_snapshots "
                "WHERE model_id = :mid ORDER BY captured_at DESC LIMIT 1"
            ),
            {"mid": _OPUS.id},
        )
    ).scalar_one()
    assert float(latest_prompt) == pytest.approx(20e-6)


# ---------------------------------------------------------------------------
# §2 Scenario: sync marks absent models inactive
# ---------------------------------------------------------------------------


async def test_sync_marks_absent_models_inactive(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """Models absent from the upstream response must be set active=false."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS, _SONNET]))
    assert (await client.post(SYNC)).status_code == 200

    # Second sync omits _SONNET
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    assert (await client.post(SYNC)).status_code == 200

    assert await _model_active(db_session, _OPUS.id) is True
    assert await _model_active(db_session, _SONNET.id) is False


# ---------------------------------------------------------------------------
# §2 Scenario: sync fails when source is unreachable
# ---------------------------------------------------------------------------


async def test_sync_upstream_unavailable(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """A source network error must return 502 ERR_UPSTREAM_UNAVAILABLE and write no rows."""
    _install_fake_source(app, FakeCatalogSource(raise_unavailable=True))

    resp = await client.post(SYNC)

    assert_problem(resp, 502, "ERR_UPSTREAM_UNAVAILABLE")

    model_rows = (await db_session.execute(text("SELECT count(*) FROM models"))).scalar_one()
    snap_rows = (
        await db_session.execute(text("SELECT count(*) FROM pricing_snapshots"))
    ).scalar_one()
    assert int(model_rows) == 0
    assert int(snap_rows) == 0


# ---------------------------------------------------------------------------
# §2 Scenario: GET /v1/models returns marked-up prices for authenticated tenant
# ---------------------------------------------------------------------------


async def test_get_models_returns_markedup_prices(client: httpx.AsyncClient, app: Any) -> None:
    """Prices served to a 20%-markup tenant must be upstream × 1.20."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    assert (await client.post(SYNC)).status_code == 200

    # Default markup_pct is 20.0 for all tenants
    token = await _signup_and_login(client)
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    data = body["data"]
    assert len(data) == 1
    model = data[0]
    assert model["id"] == _OPUS.id
    assert model["object"] == "model"
    assert model["prompt_per_token"] == pytest.approx(_OPUS.prompt_usd_per_token * 1.20)
    assert model["completion_per_token"] == pytest.approx(_OPUS.completion_usd_per_token * 1.20)


# ---------------------------------------------------------------------------
# §2 Scenario: GET /v1/models applies the tenant's own markup_pct
# ---------------------------------------------------------------------------


async def test_get_models_respects_per_tenant_markup(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """Two tenants with different markup_pct see different prices for the same model."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    assert (await client.post(SYNC)).status_code == 200

    token_a = await _signup_and_login(
        client, tenant_name="TenantA", email="a@tenanta.io", password="passwordA-long"
    )
    token_b = await _signup_and_login(
        client, tenant_name="TenantB", email="b@tenantb.io", password="passwordB-long"
    )

    # Set markup_pct for each tenant directly in DB (bypasses the not-yet-built API)
    await db_session.execute(
        text(
            "UPDATE tenants SET markup_pct = 10.0 "
            "WHERE id = (SELECT tenant_id FROM users WHERE email = 'a@tenanta.io')"
        )
    )
    await db_session.execute(
        text(
            "UPDATE tenants SET markup_pct = 50.0 "
            "WHERE id = (SELECT tenant_id FROM users WHERE email = 'b@tenantb.io')"
        )
    )
    await db_session.commit()

    resp_a = await client.get(MODELS, headers={"Authorization": f"Bearer {token_a}"})
    resp_b = await client.get(MODELS, headers={"Authorization": f"Bearer {token_b}"})

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    price_a = resp_a.json()["data"][0]["prompt_per_token"]
    price_b = resp_b.json()["data"][0]["prompt_per_token"]

    assert price_a == pytest.approx(_OPUS.prompt_usd_per_token * 1.10)
    assert price_b == pytest.approx(_OPUS.prompt_usd_per_token * 1.50)


# ---------------------------------------------------------------------------
# §2 Scenario: GET /v1/models with invalid token is rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Bearer " + jwt.encode({"sub": "x"}, "wrong-secret", algorithm="HS256")},
        {
            "Authorization": "Bearer "
            + jwt.encode(
                {"sub": "x", "iss": "ai-proxy", "iat": 0, "exp": 1},  # expired in 1970
                TEST_JWT_SECRET,
                algorithm="HS256",
            )
        },
    ],
    ids=["missing", "malformed", "wrong-signature", "expired"],
)
async def test_get_models_invalid_token_rejected(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    """Any invalid/missing bearer token must return 401 ERR_AUTH_INVALID_TOKEN."""
    resp = await client.get(MODELS, headers=headers)

    body = assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    # No model data leaked
    assert "data" not in body
    assert "id" not in body


# ---------------------------------------------------------------------------
# §2 Scenario: GET /v1/models before any sync returns catalog-empty error
# ---------------------------------------------------------------------------


async def test_get_models_before_sync_returns_catalog_empty(
    client: httpx.AsyncClient,
) -> None:
    """With zero active models, GET /v1/models must return 409 ERR_CATALOG_EMPTY."""
    token = await _signup_and_login(client)
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})

    assert_problem(resp, 409, "ERR_CATALOG_EMPTY")
    assert "data" not in resp.json()


# ---------------------------------------------------------------------------
# Additional guard: GET /v1/models omits inactive models from the response
# ---------------------------------------------------------------------------


async def test_get_models_excludes_inactive_models(client: httpx.AsyncClient, app: Any) -> None:
    """After a re-sync that deactivates a model it must not appear in GET /v1/models."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS, _SONNET]))
    assert (await client.post(SYNC)).status_code == 200

    # Re-sync with only _OPUS — _SONNET becomes inactive
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert _OPUS.id in ids
    assert _SONNET.id not in ids


# ---------------------------------------------------------------------------
# Additional guard: sync response count includes deactivated models
# ---------------------------------------------------------------------------


async def test_sync_count_reflects_all_processed_models(
    client: httpx.AsyncClient, app: Any
) -> None:
    """{"synced": N} counts all models the sync processed, not just new inserts."""
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS, _SONNET]))
    first = await client.post(SYNC)
    assert first.json()["synced"] == 2

    # Second run: one model remains active, one is deactivated — still 1 processed
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    second = await client.post(SYNC)
    # The sync saw 1 active model from source; _SONNET deactivation counts as processed
    assert second.json()["synced"] >= 1


# ---------------------------------------------------------------------------
# Additional guard: context_length may be null
# ---------------------------------------------------------------------------


async def test_sync_handles_null_context_length(client: httpx.AsyncClient, app: Any) -> None:
    """A model with no context_length must sync and appear in /v1/models without error."""
    no_ctx = FakeCatalogModel(
        id="openai/gpt-unknown",
        name="GPT Unknown",
        context_length=None,
        prompt_usd_per_token=1e-6,
        completion_usd_per_token=2e-6,
    )
    _install_fake_source(app, FakeCatalogSource(models=[no_ctx]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    model = resp.json()["data"][0]
    assert model["id"] == no_ctx.id
    assert model["context_length"] is None


# ---------------------------------------------------------------------------
# Regression — GET /admin/catalog/models is the JWT/control-plane twin of
# GET /v1/models (dashboard "/v1/* logs you out" bug).
#
# The dashboard BFF reads the model list with a SESSION JWT. /v1/models sits
# behind the edge ext_authz (sk-/agent only), so a browser session 401s through
# the edge and the BFF clears the cookie -> hard logout. This route serves the
# IDENTICAL marked-up list on the /admin/* (JWT) plane, readable by ANY tenant
# role — unlike GET /admin/models (owner/admin-only, no pricing).
# ---------------------------------------------------------------------------

ADMIN_CATALOG_MODELS = "/admin/catalog/models"


async def test_admin_catalog_models_matches_v1_models(client: httpx.AsyncClient, app: Any) -> None:
    """GET /admin/catalog/models returns the same pricing/id as GET /v1/models (no drift).

    After capabilities-admin-surface task 2, /admin/catalog/models returns
    AdminCatalogModelsListResponse which includes an extra 'input_modalities' field
    on each model entry.  The two endpoints can no longer be byte-identical.

    Contract preserved:
    - Same model set (same ids).
    - Same tenant-marked-up pricing (prompt_per_token, completion_per_token).
    - Same context_length and name.
    - /v1/models MUST NOT contain 'input_modalities' (public lean shape unchanged).
    - /admin/catalog/models MUST contain 'input_modalities' (new admin surface).
    """
    _install_fake_source(app, FakeCatalogSource(models=[_OPUS, _SONNET]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    v1 = await client.get(MODELS, headers=headers)
    twin = await client.get(ADMIN_CATALOG_MODELS, headers=headers)

    assert v1.status_code == 200, v1.text
    assert twin.status_code == 200, twin.text

    v1_data = {m["id"]: m for m in v1.json()["data"]}
    twin_data = {m["id"]: m for m in twin.json()["data"]}

    # Same model set
    assert set(v1_data) == set(twin_data), (
        f"model id sets differ: v1={set(v1_data)} twin={set(twin_data)}"
    )

    for model_id, v1_entry in v1_data.items():
        twin_entry = twin_data[model_id]
        # Pricing must be identical (same markup arithmetic, no drift)
        assert twin_entry["prompt_per_token"] == pytest.approx(v1_entry["prompt_per_token"]), (
            f"{model_id}: prompt_per_token drift"
        )
        assert twin_entry["completion_per_token"] == pytest.approx(
            v1_entry["completion_per_token"]
        ), f"{model_id}: completion_per_token drift"
        assert twin_entry["name"] == v1_entry["name"]
        assert twin_entry["context_length"] == v1_entry["context_length"]
        # /v1/models must stay lean (no input_modalities)
        assert "input_modalities" not in v1_entry, (
            f"/v1/models entry {model_id!r} must NOT contain input_modalities"
        )
        # /admin/catalog/models must expose input_modalities
        assert "input_modalities" in twin_entry, (
            f"/admin/catalog/models entry {model_id!r} must contain input_modalities"
        )
        assert isinstance(twin_entry["input_modalities"], list)


async def test_admin_catalog_models_readable_by_non_admin_role(
    client: httpx.AsyncClient, app: Any
) -> None:
    """A viewer (non owner/admin) role can read it — parity with /v1/models.

    GET /admin/models is owner/admin-gated; this twin must NOT inherit that gate,
    or the all-roles dashboard surfaces (Usage / Chat picker / Vision) would 403
    for viewer/member callers. JWT carries the role claim, so we mint a viewer
    token bound to the seeded tenant (the live token_service, same pattern as the
    rbac-roles suite).
    """
    import uuid

    from gateway.tenants.domain.entities import Role

    _install_fake_source(app, FakeCatalogSource(models=[_OPUS]))
    assert (await client.post(SYNC)).status_code == 200

    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "Viewers", "email": "viewer@vco.io", "password": "viewer-horse-batt"},
    )
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenant_id"]

    viewer_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.VIEWER,
        email="viewer@vco.io",
    )

    resp = await client.get(
        ADMIN_CATALOG_MODELS, headers={"Authorization": f"Bearer {viewer_token}"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"][0]["id"] == _OPUS.id


async def test_admin_catalog_models_before_sync_returns_catalog_empty(
    client: httpx.AsyncClient,
) -> None:
    """With zero active models it must mirror /v1/models: 409 ERR_CATALOG_EMPTY."""
    token = await _signup_and_login(client)
    resp = await client.get(ADMIN_CATALOG_MODELS, headers={"Authorization": f"Bearer {token}"})

    assert_problem(resp, 409, "ERR_CATALOG_EMPTY")
    assert "data" not in resp.json()


async def test_admin_catalog_models_invalid_token_rejected(client: httpx.AsyncClient) -> None:
    """No/!bearer token must be rejected (JWT plane) — never serve the list anonymously."""
    resp = await client.get(ADMIN_CATALOG_MODELS)
    assert resp.status_code == 401, resp.text
    assert "data" not in resp.json()
