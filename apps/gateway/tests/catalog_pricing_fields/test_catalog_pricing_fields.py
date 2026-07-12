"""Red suite for catalog-pricing-fields (TASK.md §2, CPF1-CPF7; contract FROZEN @ v1).

Additive per-1M cost fields on GET /v1/models + GET /admin/catalog/models, PLUS wiring the
pre-existing (previously-unused) cached_input_usd_per_token column end-to-end from the catalog
seed through to a real billed cost_usd — closing the gap minimax-live-verify's real evidence
exposed (MiniMax's genuine cache-hit discount was never applied).

FakeCatalogSource is injected via app.state (CatalogSource is a typing.Protocol port), same
pattern as tests/catalog/test_model_catalog.py. CPF6 syncs a real MiniMax-shaped model (exercising
this task's _insert_snapshot wiring) then drives the real RecordingUsageRecorder + UsageLedgerFlusher
against the real dev DB/Redis to prove a real usage_records.cost_usd reflects the cache discount —
mirroring the established tests/tiered_token_billing/test_tt5_db_tier_counts_persist_through_flusher
pattern, extended to assert on cost_usd rather than the raw tier-count columns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SYNC = "/internal/catalog/sync"
MODELS = "/v1/models"
ADMIN_CATALOG_MODELS = "/admin/catalog/models"

# Real MiniMax pay-as-you-go prices (minimax-live-verify + catalog-pricing-fields TASK.md §0,
# confirmed 2026-07-01 via platform.minimax.io/docs/guides/pricing-paygo).
MINIMAX_PROMPT_PRICE = 0.0000003
MINIMAX_COMPLETION_PRICE = 0.0000012
MINIMAX_CACHED_PRICE = 0.00000006


# ---------------------------------------------------------------------------
# Fake domain types (local copy — same convention as catalog_sync_trigger/conftest.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeCatalogModel:
    """Mirrors the CatalogModel value object, including the new cache-price field."""

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float
    modality: str = "chat"
    provider: str = "openrouter"
    input_modalities: str = "text"
    cached_input_usd_per_token: float | None = None
    # gpt-realtime-pricing-fields TASK.md §3: CatalogModel.audio_* fields are now read
    # by _insert_snapshot too; mirror their real defaults so this suite is unaffected.
    audio_prompt_usd_per_token: float | None = None
    audio_completion_usd_per_token: float | None = None
    audio_cached_usd_per_token: float | None = None
    # region-catalog-dimension TASK.md §3: CatalogModel.region is now read by
    # _upsert_model too; mirror its real default so this suite is unaffected.
    region: str = "global"


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


_MINIMAX_M3 = FakeCatalogModel(
    id="MiniMax-M3",
    name="MiniMax M3",
    context_length=1_000_000,
    prompt_usd_per_token=MINIMAX_PROMPT_PRICE,
    completion_usd_per_token=MINIMAX_COMPLETION_PRICE,
    provider="minimax",
    cached_input_usd_per_token=MINIMAX_CACHED_PRICE,
)
_OPENROUTER_MODEL = FakeCatalogModel(
    id="anthropic/claude-opus-4",
    name="Claude Opus 4",
    context_length=200_000,
    prompt_usd_per_token=15e-6,
    completion_usd_per_token=75e-6,
    # cached_input_usd_per_token intentionally absent (None) — no cache price today.
)


def _install_fake_source(app: Any, fake: FakeCatalogSource) -> None:
    app.state.catalog_source = fake


async def _signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str = "CatalogPricingFieldsTenant",
    email: str = "cpf@acme.io",
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


async def _snapshot_count(db: AsyncSession, model_id: str) -> int:
    row = await db.execute(
        text("SELECT count(*) FROM pricing_snapshots WHERE model_id = :mid"),
        {"mid": model_id},
    )
    return int(row.scalar_one())


async def _latest_cached_price(db: AsyncSession, model_id: str) -> Decimal | None:
    row = (
        await db.execute(
            text(
                "SELECT cached_input_usd_per_token FROM pricing_snapshots "
                "WHERE model_id = :mid ORDER BY captured_at DESC LIMIT 1"
            ),
            {"mid": model_id},
        )
    ).scalar_one_or_none()
    return Decimal(str(row)) if row is not None else None


# ---------------------------------------------------------------------------
# CPF1 — GET /v1/models shows per-1M prices additively, existing fields untouched
# ---------------------------------------------------------------------------


async def test_v1_models_shows_per_1m_fields_additively(
    client: httpx.AsyncClient, app: Any
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_MINIMAX_M3, _OPENROUTER_MODEL]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    resp = await client.get(MODELS, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = {m["id"]: m for m in resp.json()["data"]}

    minimax = data[_MINIMAX_M3.id]
    # Existing fields byte-identical (20% default markup)
    assert minimax["prompt_per_token"] == pytest.approx(MINIMAX_PROMPT_PRICE * 1.20)
    assert minimax["completion_per_token"] == pytest.approx(MINIMAX_COMPLETION_PRICE * 1.20)
    assert minimax["object"] == "model"
    # New additive per-1M fields
    assert minimax["prompt_usd_per_1m"] == pytest.approx(MINIMAX_PROMPT_PRICE * 1.20 * 1_000_000)
    assert minimax["completion_usd_per_1m"] == pytest.approx(
        MINIMAX_COMPLETION_PRICE * 1.20 * 1_000_000
    )
    assert minimax["cached_input_usd_per_1m"] == pytest.approx(
        MINIMAX_CACHED_PRICE * 1.20 * 1_000_000
    )

    openrouter = data[_OPENROUTER_MODEL.id]
    assert openrouter["cached_input_usd_per_1m"] is None
    assert openrouter["prompt_usd_per_1m"] == pytest.approx(
        _OPENROUTER_MODEL.prompt_usd_per_token * 1.20 * 1_000_000
    )


# ---------------------------------------------------------------------------
# CPF2 — GET /admin/catalog/models mirrors CPF1 plus input_modalities
# ---------------------------------------------------------------------------


async def test_admin_catalog_models_mirrors_pricing_fields(
    client: httpx.AsyncClient, app: Any
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_MINIMAX_M3, _OPENROUTER_MODEL]))
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
        assert admin_entry["prompt_usd_per_1m"] == pytest.approx(v1_entry["prompt_usd_per_1m"])
        assert admin_entry["completion_usd_per_1m"] == pytest.approx(
            v1_entry["completion_usd_per_1m"]
        )
        assert admin_entry["cached_input_usd_per_1m"] == v1_entry["cached_input_usd_per_1m"] or (
            admin_entry["cached_input_usd_per_1m"]
            == pytest.approx(v1_entry["cached_input_usd_per_1m"])
        )
        assert "input_modalities" in admin_entry
        assert "input_modalities" not in v1_entry


# ---------------------------------------------------------------------------
# CPF3 — catalog sync persists MiniMax's real cache-hit price
# ---------------------------------------------------------------------------


async def test_sync_persists_minimax_cache_price(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_MINIMAX_M3, _OPENROUTER_MODEL]))
    assert (await client.post(SYNC)).status_code == 200

    minimax_price = await _latest_cached_price(db_session, _MINIMAX_M3.id)
    assert minimax_price == Decimal(str(MINIMAX_CACHED_PRICE))

    openrouter_price = await _latest_cached_price(db_session, _OPENROUTER_MODEL.id)
    assert openrouter_price is None


# ---------------------------------------------------------------------------
# CPF4 — a cache-price-only change still appends a new append-only snapshot
# ---------------------------------------------------------------------------


async def test_sync_appends_snapshot_on_cache_price_only_change(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_MINIMAX_M3]))
    assert (await client.post(SYNC)).status_code == 200
    assert await _snapshot_count(db_session, _MINIMAX_M3.id) == 1

    minimax_v2 = FakeCatalogModel(
        id=_MINIMAX_M3.id,
        name=_MINIMAX_M3.name,
        context_length=_MINIMAX_M3.context_length,
        prompt_usd_per_token=_MINIMAX_M3.prompt_usd_per_token,
        completion_usd_per_token=_MINIMAX_M3.completion_usd_per_token,
        provider="minimax",
        cached_input_usd_per_token=0.00000012,  # cache price changed; prompt/completion did not
    )
    _install_fake_source(app, FakeCatalogSource(models=[minimax_v2]))
    assert (await client.post(SYNC)).status_code == 200

    assert await _snapshot_count(db_session, _MINIMAX_M3.id) == 2
    latest = await _latest_cached_price(db_session, _MINIMAX_M3.id)
    assert latest == Decimal("0.00000012")


# ---------------------------------------------------------------------------
# CPF5 — fully unchanged model (incl. cache price) does not append a spurious snapshot
# ---------------------------------------------------------------------------


async def test_sync_idempotent_including_cache_price(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_MINIMAX_M3]))
    assert (await client.post(SYNC)).status_code == 200

    _install_fake_source(app, FakeCatalogSource(models=[_MINIMAX_M3]))
    assert (await client.post(SYNC)).status_code == 200

    assert await _snapshot_count(db_session, _MINIMAX_M3.id) == 1, (
        "identical prices (incl. cache price) must not duplicate a snapshot row"
    )


# ---------------------------------------------------------------------------
# CPF6 — a REAL sync-written MiniMax cache price flows through to a REAL billed
# usage_records.cost_usd, reflecting the discount (recorder.py's tiered math itself
# already existed; this proves catalog-pricing-fields' sync-side wiring is what was
# missing — RED today because _insert_snapshot doesn't persist the field yet, so the
# recorder falls back to flat pricing and cost_usd comes out HIGHER than expected).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cached_tokens_billed_at_cache_rate(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    import uuid

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    # Real sync — exercises this task's _insert_snapshot wiring, not a raw INSERT.
    _install_fake_source(app, FakeCatalogSource(models=[_MINIMAX_M3]))
    assert (await client.post(SYNC)).status_code == 200

    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "CPF6Tenant",
            "email": "cpf6@acme.io",
            "password": "cpf6 test password",
        },
    )
    assert signup.status_code == 201, signup.text
    tenant_uuid = uuid.UUID(signup.json()["tenant_id"])
    login = await client.post(
        "/admin/auth/login",
        json={"email": "cpf6@acme.io", "password": "cpf6 test password"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "cpf6-key"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    key_id = uuid.UUID(created.json()["key_id"])
    # 20% default markup_pct on a freshly-signed-up tenant (established project default).

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        # Exact numbers from minimax-live-verify's real, live-verified call.
        await recorder.record(
            tenant_id=tenant_uuid,
            key_id=key_id,
            model=_MINIMAX_M3.id,
            usage={
                "prompt_tokens": 198,
                "prompt_tokens_details": {"cached_tokens": 128},
                "completion_tokens": 49,
            },
            status=200,
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE key_id = :kid"),
            {"kid": str(key_id)},
        )
    ).scalar_one()
    got = Decimal(str(row))

    # usage_records.cost_usd is Numeric(14,8) — the exact 9-decimal-digit computed value
    # (0.000104976) is stored rounded to 8 decimal places (0.00010498).
    exact = (
        Decimal("70") * Decimal(str(MINIMAX_PROMPT_PRICE))
        + Decimal("128") * Decimal(str(MINIMAX_CACHED_PRICE))
        + Decimal("49") * Decimal(str(MINIMAX_COMPLETION_PRICE))
    ) * Decimal("1.20")
    assert exact == Decimal("0.000104976")
    expected_persisted = Decimal("0.00010498")
    flat_rate = (
        Decimal("198") * Decimal(str(MINIMAX_PROMPT_PRICE))
        + Decimal("49") * Decimal(str(MINIMAX_COMPLETION_PRICE))
    ) * Decimal("1.20")

    assert got == expected_persisted, f"tiered cache billing mismatch: got {got}"
    assert got < flat_rate, "a real cache hit must bill strictly less than the flat (pre-fix) rate"


# ---------------------------------------------------------------------------
# CPF7 (reject) — a model with no cache price never masks null as 0 or the prompt price
# ---------------------------------------------------------------------------


async def test_null_cache_price_stays_null_never_masked(
    client: httpx.AsyncClient, app: Any
) -> None:
    _install_fake_source(app, FakeCatalogSource(models=[_OPENROUTER_MODEL]))
    assert (await client.post(SYNC)).status_code == 200

    token = await _signup_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    v1 = await client.get(MODELS, headers=headers)
    admin = await client.get(ADMIN_CATALOG_MODELS, headers=headers)

    v1_model = v1.json()["data"][0]
    admin_model = admin.json()["data"][0]

    assert v1_model["cached_input_usd_per_1m"] is None
    assert admin_model["cached_input_usd_per_1m"] is None
    assert v1_model["cached_input_usd_per_1m"] != 0
    assert v1_model["cached_input_usd_per_1m"] != v1_model["prompt_usd_per_1m"]
