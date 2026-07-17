"""Red suite for gpt-realtime-pricing-fields (TASK.md §2, GRPF1-GRPF6; contract FROZEN @ v1).

Seeds GPT-Realtime's real dual-stream (text + audio) pricing into the catalog and switches the
realtime relay's default model to "gpt-realtime". Mirrors tests/catalog_pricing_fields's
FakeCatalogSource-injection pattern (CatalogSource is a typing.Protocol port).

GRPF7 (full-suite regression) is verified at VERIFY time, not by a test here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env

SYNC = "/internal/catalog/sync"
MODELS = "/v1/models"
ADMIN_CATALOG_MODELS = "/admin/catalog/models"

# Real gpt-realtime prices (TASK.md §0, confirmed 2026-07-02 via
# developers.openai.com/api/docs/models/gpt-realtime).
GPT_REALTIME_PROMPT_PRICE = 0.000004
GPT_REALTIME_COMPLETION_PRICE = 0.000016
GPT_REALTIME_CACHED_PRICE = 0.0000004
GPT_REALTIME_AUDIO_PROMPT_PRICE = 0.000032
GPT_REALTIME_AUDIO_COMPLETION_PRICE = 0.000064
GPT_REALTIME_AUDIO_CACHED_PRICE = 0.0000004


# ---------------------------------------------------------------------------
# Fake domain types (local copy — same convention as catalog_pricing_fields)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeCatalogModel:
    """Mirrors CatalogModel, including the 3 new audio_* fields."""

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float
    modality: str = "chat"
    provider: str = "openrouter"
    input_modalities: str = "text"
    cached_input_usd_per_token: float | None = None
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
    """In-memory stub implementing the CatalogSource Protocol port."""

    def __init__(self, models: list[FakeCatalogModel] | None = None) -> None:
        self.models = models or []

    async def list_models(self) -> AsyncIterator[FakeCatalogModel]:
        for model in self.models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[FakeCatalogModel]:
        return
        yield  # pragma: no cover — makes this an async generator


_GPT_REALTIME = FakeCatalogModel(
    id="gpt-realtime",
    name="gpt-realtime",
    context_length=None,
    prompt_usd_per_token=GPT_REALTIME_PROMPT_PRICE,
    completion_usd_per_token=GPT_REALTIME_COMPLETION_PRICE,
    provider="openai",
    cached_input_usd_per_token=GPT_REALTIME_CACHED_PRICE,
    audio_prompt_usd_per_token=GPT_REALTIME_AUDIO_PROMPT_PRICE,
    audio_completion_usd_per_token=GPT_REALTIME_AUDIO_COMPLETION_PRICE,
    audio_cached_usd_per_token=GPT_REALTIME_AUDIO_CACHED_PRICE,
)
_OPENROUTER_MODEL = FakeCatalogModel(
    id="anthropic/claude-opus-4",
    name="Claude Opus 4",
    context_length=200_000,
    prompt_usd_per_token=15e-6,
    completion_usd_per_token=75e-6,
    # no cache/audio prices — single-stream model, every new field stays None.
)


def _install_fake_source(app: Any, fake: FakeCatalogSource) -> None:
    app.state.catalog_source = fake


async def _signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str = "GptRealtimePricingFieldsTenant",
    email: str = "grpf@acme.io",
    password: str = "correct horse battery",
) -> str:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, signup.text
    login = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token: str = login.json()["access_token"]
    return token


async def _latest_prices(db: AsyncSession, model_id: str) -> tuple[Decimal | None, ...] | None:
    from sqlalchemy import text

    row = (
        await db.execute(
            text(
                "SELECT prompt_usd_per_token, completion_usd_per_token, cached_input_usd_per_token,"
                " audio_prompt_usd_per_token, audio_completion_usd_per_token,"
                " audio_cached_usd_per_token"
                " FROM pricing_snapshots WHERE model_id = :mid ORDER BY captured_at DESC LIMIT 1"
            ),
            {"mid": model_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return tuple(Decimal(str(v)) if v is not None else None for v in row)


async def _snapshot_count(db: AsyncSession, model_id: str) -> int:
    from sqlalchemy import text

    row = await db.execute(
        text("SELECT count(*) FROM pricing_snapshots WHERE model_id = :mid"),
        {"mid": model_id},
    )
    return int(row.scalar_one())


# ---------------------------------------------------------------------------
# GRPF1 — a catalog sync persists gpt-realtime with all 6 real prices
# ---------------------------------------------------------------------------


async def test_grpf1_sync_persists_gpt_realtime_with_all_6_prices(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_GPT_REALTIME, _OPENROUTER_MODEL]))
    assert (await client.post(SYNC)).status_code == 200

    prices = await _latest_prices(db_session, _GPT_REALTIME.id)
    assert prices is not None
    assert prices == (
        Decimal(str(GPT_REALTIME_PROMPT_PRICE)),
        Decimal(str(GPT_REALTIME_COMPLETION_PRICE)),
        Decimal(str(GPT_REALTIME_CACHED_PRICE)),
        Decimal(str(GPT_REALTIME_AUDIO_PROMPT_PRICE)),
        Decimal(str(GPT_REALTIME_AUDIO_COMPLETION_PRICE)),
        Decimal(str(GPT_REALTIME_AUDIO_CACHED_PRICE)),
    )

    openrouter_prices = await _latest_prices(db_session, _OPENROUTER_MODEL.id)
    assert openrouter_prices is not None
    assert openrouter_prices[3:] == (None, None, None)


# ---------------------------------------------------------------------------
# GRPF2 — GET /v1/models lists gpt-realtime with all 6 tenant-marked-up prices
# ---------------------------------------------------------------------------


async def test_grpf2_v1_models_lists_gpt_realtime_6_prices(
    client: httpx.AsyncClient, app: Any
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_GPT_REALTIME, _OPENROUTER_MODEL]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = {m["id"]: m for m in resp.json()["data"]}

    gr = data[_GPT_REALTIME.id]
    markup = 1.20  # default 20% markup on a freshly-signed-up tenant
    assert gr["audio_prompt_usd_per_1m"] == pytest.approx(
        GPT_REALTIME_AUDIO_PROMPT_PRICE * markup * 1_000_000
    )
    assert gr["audio_completion_usd_per_1m"] == pytest.approx(
        GPT_REALTIME_AUDIO_COMPLETION_PRICE * markup * 1_000_000
    )
    assert gr["audio_cached_usd_per_1m"] == pytest.approx(
        GPT_REALTIME_AUDIO_CACHED_PRICE * markup * 1_000_000
    )
    # existing fields stay correct too (byte-identical arithmetic)
    assert gr["prompt_usd_per_1m"] == pytest.approx(GPT_REALTIME_PROMPT_PRICE * markup * 1_000_000)

    openrouter = data[_OPENROUTER_MODEL.id]
    assert openrouter["audio_prompt_usd_per_1m"] is None
    assert openrouter["audio_completion_usd_per_1m"] is None
    assert openrouter["audio_cached_usd_per_1m"] is None


# ---------------------------------------------------------------------------
# GRPF3 — GET /admin/catalog/models mirrors GRPF2
# ---------------------------------------------------------------------------


async def test_grpf3_admin_catalog_models_mirrors_grpf2(
    client: httpx.AsyncClient, app: Any
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_GPT_REALTIME, _OPENROUTER_MODEL]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    v1 = await client.get(MODELS, headers=headers)
    admin = await client.get(ADMIN_CATALOG_MODELS, headers=headers)
    assert v1.status_code == 200 and admin.status_code == 200

    v1_data = {m["id"]: m for m in v1.json()["data"]}
    admin_data = {m["id"]: m for m in admin.json()["data"]}

    for model_id, v1_entry in v1_data.items():
        admin_entry = admin_data[model_id]
        for field in (
            "audio_prompt_usd_per_1m",
            "audio_completion_usd_per_1m",
            "audio_cached_usd_per_1m",
        ):
            if v1_entry[field] is None:
                assert admin_entry[field] is None
            else:
                assert admin_entry[field] == pytest.approx(v1_entry[field])
        assert "input_modalities" in admin_entry
        assert "input_modalities" not in v1_entry


# ---------------------------------------------------------------------------
# GRPF4 — markup is applied identically to audio prices as to existing prices
# ---------------------------------------------------------------------------


async def test_grpf4_markup_applied_to_audio_prices(client: httpx.AsyncClient, app: Any) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_GPT_REALTIME]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client, email="grpf4@acme.io")
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})
    gr = resp.json()["data"][0]

    markup = 1.20  # default markup_pct on a freshly-signed-up tenant
    assert gr["completion_usd_per_1m"] / gr["audio_completion_usd_per_1m"] == pytest.approx(
        GPT_REALTIME_COMPLETION_PRICE / GPT_REALTIME_AUDIO_COMPLETION_PRICE
    )
    assert gr["audio_prompt_usd_per_1m"] == pytest.approx(
        GPT_REALTIME_AUDIO_PROMPT_PRICE * markup * 1_000_000
    )


# ---------------------------------------------------------------------------
# GRPF5 — an audio-price-only change still appends a new append-only snapshot
# ---------------------------------------------------------------------------


async def test_grpf5_audio_price_only_change_appends_snapshot(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_GPT_REALTIME]))
    assert (await client.post(SYNC)).status_code == 200
    assert await _snapshot_count(db_session, _GPT_REALTIME.id) == 1

    changed = FakeCatalogModel(
        id=_GPT_REALTIME.id,
        name=_GPT_REALTIME.name,
        context_length=_GPT_REALTIME.context_length,
        prompt_usd_per_token=_GPT_REALTIME.prompt_usd_per_token,
        completion_usd_per_token=_GPT_REALTIME.completion_usd_per_token,
        provider="openai",
        cached_input_usd_per_token=_GPT_REALTIME.cached_input_usd_per_token,
        audio_prompt_usd_per_token=_GPT_REALTIME.audio_prompt_usd_per_token,
        audio_completion_usd_per_token=0.00007,  # only this changed
        audio_cached_usd_per_token=_GPT_REALTIME.audio_cached_usd_per_token,
    )
    _install_fake_source(app, FakeCatalogSource(models=[changed]))
    assert (await client.post(SYNC)).status_code == 200

    assert await _snapshot_count(db_session, _GPT_REALTIME.id) == 2
    latest = await _latest_prices(db_session, _GPT_REALTIME.id)
    assert latest is not None
    assert latest[4] == Decimal("0.00007")


# ---------------------------------------------------------------------------
# GRPF6 — the realtime relay's default model is "gpt-realtime"
# ---------------------------------------------------------------------------


def test_grpf6_realtime_relay_default_model_is_gpt_realtime(settings: Any) -> None:
    assert settings.realtime_relay_openai_model == "gpt-realtime"


def test_grpf6_env_override_still_wins() -> None:
    from gateway.core.config import Settings

    overridden = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret="test-secret-not-the-dev-default-xyzzy",
        realtime_relay_openai_model="gpt-4o-realtime-preview",
    )
    assert overridden.realtime_relay_openai_model == "gpt-4o-realtime-preview"
