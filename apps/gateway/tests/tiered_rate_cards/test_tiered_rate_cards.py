"""Failing-first (red) suite for tiered-rate-cards (contract FROZEN @ v1, TASK.md §4).

One test per §2 scenario + the §4 authz-matrix test. RED before BUILD — they fail
for the right reason:

  * override-billing tests (unit, FakeSession): `_fetch_markup_pct(session, tenant_id)`
    takes no `model_id` today and never queries `tenant_rate_card_entries` at all, so
    a configured override is silently ignored and the cost comes out at the FLAT
    markup instead of the override — a Decimal mismatch, not a harness bug. Every
    such test deliberately sets flat_markup != override_markup so a leaked flat read
    is caught (not a false green).
  * `test_no_entry_falls_back_byte_identical` is the one scenario that CANNOT be RED
    (see its docstring) — it is a regression guard on EXISTING behavior.
  * catalog/recovery equality tests (DB): `tenant_rate_card_entries` does not exist
    yet -> the setup INSERT raises a Postgres UndefinedTable error before any
    catalog/billing assertion runs.
  * admin-router tests (DB): PUT/GET/DELETE /admin/rate-cards are not mounted yet ->
    404, not the contracted 200/204/403/422.
  * `test_owner_holds_rate_cards_manage_matrix`: `Permission.RATE_CARDS_MANAGE` does
    not exist yet -> AttributeError (deferred import keeps this contained to the one
    test, never at module-collection time).

Run only the pure-unit / no-infra subset:
  cd apps/gateway && uv run pytest tests/tiered_rate_cards/ -q --no-cov \
      -k "bills_override or byte_identical or per_unit_basis or matrix" -p no:cacheprovider

Full suite (needs Postgres + Redis — see the task report for the isolated-DB env var):
  cd apps/gateway && uv run pytest tests/tiered_rate_cards/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role
from tests.tiered_rate_cards.conftest import (
    FakeSession,
    FakeSessionFactory,
    StreamCapture,
)

# ---------------------------------------------------------------------------
# Shared helpers (router tests)
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _issue_token(app: Any, *, tenant_id: uuid.UUID, role: Role) -> str:
    """Issue a JWT for an arbitrary role bound to the given tenant (mirrors
    tests/rbac_roles/test_rbac_roles.py's `_issue_token`)."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=role,
        email=f"{role.value}@ratecard-rbac.local",
    )
    return token


# ---------------------------------------------------------------------------
# RC1 — Set a per-model markup and bill at the override  (Must 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_per_model_markup_bills_override(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("20"),  # tenant flat — deliberately != the override below
        rate_card_overrides={"gpt-4o": Decimal("50")},
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    # "gpt-4o" has an override -> must bill at x1.50, NOT the flat x1.20.
    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="gpt-4o",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 1.00},
        status=200,
    )
    expected_override = Decimal("1.00") * (Decimal("1") + Decimal("50") / Decimal("100"))
    evt_override = stream.events[0]
    assert Decimal(evt_override["cost_usd"]) == expected_override, (
        f"override not applied: got {evt_override['cost_usd']!r}, expected {expected_override}"
    )

    # A different model with NO entry must still bill at the flat x1.20.
    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="claude-3-no-override",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 1.00},
        status=200,
    )
    expected_flat = Decimal("1.00") * (Decimal("1") + Decimal("20") / Decimal("100"))
    evt_flat = stream.events[1]
    assert Decimal(evt_flat["cost_usd"]) == expected_flat, (
        f"no-entry model must bill flat: got {evt_flat['cost_usd']!r}, expected {expected_flat}"
    )


# ---------------------------------------------------------------------------
# RC2 — No entry falls back byte-identical to the flat markup  (Must 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_entry_falls_back_byte_identical(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    """Scenario: No entry falls back byte-identical to the flat markup.

    NOT expressible as RED: this scenario pins EXISTING behavior (no override ->
    the flat `tenants` markup, unchanged) — there is no override-consulting logic
    to be missing yet, so this test is expected to be GREEN before AND after BUILD.
    It stays in the suite as the regression guard BUILD must not break (TASK.md §0
    "Regression blast radius" honor) — see the task report for why this is the one
    scenario that cannot be forced RED without asserting on internals (a harness-
    reason red, which would fail the refute-read).
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("20"),
        rate_card_overrides={},  # no entries at all
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="gpt-4o",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 1.00},
        status=200,
    )

    expected = Decimal("1.00") * (Decimal("1") + Decimal("20") / Decimal("100"))
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"byte-identical fallback broken: got {evt['cost_usd']!r}, expected {expected}"
    )
    # The fallback query TEXT must be preserved verbatim (TASK.md §3 resolver contract).
    assert any("SELECT markup_pct FROM tenants" in sql for sql in session.executed), (
        f"fallback query text not preserved; issued: {session.executed}"
    )


# ---------------------------------------------------------------------------
# RC3 — Catalog display equals billing for the same override  (Must 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_price_equals_billing_for_override(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """Scenario: Catalog display equals billing for the same override — no third-site drift.

    RED reason: `tenant_rate_card_entries` does not exist yet -> the setup INSERT
    below raises a Postgres UndefinedTable error before any catalog/billing
    assertion runs.
    """
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    model_id = "ratecard/gpt-4o-catalog-eq"
    prompt_price = Decimal("0.00001")
    completion_price = Decimal("0.00003")
    tenant_id = api_key["tenant_id"]

    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o catalog-eq test"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, :pp, :cp, now())"
        ),
        {
            "id": str(uuid.uuid4()),
            "m": model_id,
            "pp": str(prompt_price),
            "cp": str(completion_price),
        },
    )
    await db_session.execute(
        text("UPDATE tenants SET markup_pct = :p WHERE id = :t"),
        {"p": "20", "t": tenant_id},
    )
    # The override (50%) deliberately differs from the flat 20% set above, so a
    # leaked flat read is caught. RED pre-build: this table does not exist yet.
    await db_session.execute(
        text(
            "INSERT INTO tenant_rate_card_entries (id, tenant_id, model_id, markup_pct)"
            " VALUES (:id, :t, :m, :p)"
        ),
        {"id": str(uuid.uuid4()), "t": tenant_id, "m": model_id, "p": "50"},
    )
    await db_session.commit()

    # --- catalog side ---
    resp = await client.get("/v1/models", headers=_bearer(api_key["jwt"]))
    assert resp.status_code == 200, resp.text
    catalog_model = next(m for m in resp.json()["data"] if m["id"] == model_id)
    expected_catalog_prompt = float(prompt_price) * 1.50
    assert catalog_model["prompt_per_token"] == pytest.approx(expected_catalog_prompt), (
        f"catalog price must reflect the {model_id} override (x1.50): {catalog_model}"
    )

    # --- billing side ---
    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(tenant_id),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": tenant_id, "m": model_id},
        )
    ).fetchone()
    assert row is not None, "no usage row flushed"
    billed_cost = Decimal(str(row[0]))
    expected_billed = Decimal("1.00") * Decimal("1.50")
    assert billed_cost == expected_billed, (
        f"billing must use the same x1.50 override: {billed_cost}"
    )

    # No third-site drift: catalog multiplier == billing multiplier.
    catalog_multiplier = catalog_model["prompt_per_token"] / float(prompt_price)
    billing_multiplier = float(billed_cost) / 1.00
    assert catalog_multiplier == pytest.approx(billing_multiplier), (
        "catalog price and billed cost must resolve the SAME multiplier (no drift)"
    )


# ---------------------------------------------------------------------------
# RC4 — Disconnect/OpenRouter recovery uses the override too  (Must 4, recovery site)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_uses_override(
    app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """Scenario: Disconnect/OpenRouter recovery uses the override too.

    Mirrors tests/openrouter_cost_recovery/test_openrouter_cost_recovery.py's
    DB-backed idiom with local copies of its small test doubles (no cross-suite
    import — that suite has no `__init__.py`).

    RED reason: `tenant_rate_card_entries` does not exist yet -> the setup INSERT
    below raises a Postgres UndefinedTable error before `recover()` is even called.
    """
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.proxy.infrastructure.openrouter_upstream import GenerationCost
    from gateway.usage.application.cost_recovery import OpenRouterCostRecoveryService
    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    class _FakeUpstream:
        def __init__(self, cost: GenerationCost) -> None:
            self._cost = cost

        async def get_generation(self, generation_id: str) -> GenerationCost | None:
            return self._cost

    class _FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def now(self) -> float:
            return self.t

        async def sleep(self, seconds: float) -> None:
            self.t += seconds

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openrouter/ratecard-recover-test"
        gid = "gen-ratecard-recover-1"
        tenant_id = api_key["tenant_id"]

        # Flat markup (20%) deliberately differs from the override (50%) below, so a
        # leaked flat read is caught.
        await db_session.execute(
            text("UPDATE tenants SET markup_pct = :p WHERE id = :t"),
            {"p": "20", "t": tenant_id},
        )
        # RED pre-build: this table does not exist yet.
        await db_session.execute(
            text(
                "INSERT INTO tenant_rate_card_entries (id, tenant_id, model_id, markup_pct)"
                " VALUES (:id, :t, :m, :p)"
            ),
            {"id": str(uuid.uuid4()), "t": tenant_id, "m": model_id, "p": "50"},
        )
        # Seed the flushed client-disconnect anchor row (a partial $0 floor) —
        # mirrors _seed_anchor in the sibling suite.
        await db_session.execute(
            text(
                "INSERT INTO usage_records"
                " (id, tenant_id, key_id, model_id, status, raw, cost_usd,"
                "  provider_generation_id, usage_source)"
                " VALUES (:id, :t, :k, :m, 200, '{}', 0.00, :gid, 'client_disconnect')"
            ),
            {
                "id": str(uuid.uuid4()),
                "t": tenant_id,
                "k": api_key["key_id"],
                "m": model_id,
                "gid": gid,
            },
        )
        await db_session.commit()

        clock = _FakeClock()
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        svc = OpenRouterCostRecoveryService(
            upstream=_FakeUpstream(  # type: ignore[arg-type]
                GenerationCost(
                    total_cost=Decimal("2.00"),
                    upstream_inference_cost=Decimal("2.00"),
                    native_tokens_prompt=0,
                    native_tokens_completion=0,
                    native_tokens_cached=0,
                )
            ),
            recorder=recorder,
            session_factory=app.state.sessionmaker,
            credential_resolver=None,
            sleep=clock.sleep,
            monotonic=clock.now,
        )
        outcome = await svc.recover(
            tenant_id=uuid.UUID(tenant_id),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            provider_generation_id=gid,
        )
        assert outcome.status == "recovered", outcome

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        total_row = (
            await db_session.execute(
                text(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_records"
                    " WHERE tenant_id = :t AND provider_generation_id = :g"
                ),
                {"t": tenant_id, "g": gid},
            )
        ).fetchone()
        assert total_row is not None
        assert Decimal(str(total_row[0])) == Decimal("3.00"), (
            f"recovery must use the {model_id} override (x1.50 -> $3.00): {total_row}"
        )
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# RC5 — Override applies across a per-unit (non-token) cost basis  (Must 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_applies_to_per_unit_basis(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        pricing_unit="per_image",
        unit_usd_per_unit=Decimal("0.04"),
        markup_pct=Decimal("20"),  # tenant flat — deliberately != the override below
        rate_card_overrides={"dall-e-3": Decimal("30")},
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="dall-e-3",
        usage=None,
        status=200,
        pricing_unit="per_image",
        quantity=Decimal("2"),
    )

    # 2 x 0.04 x 1.30 = 0.104 (override) — pre-build the recorder still resolves the
    # flat 20% -> 0.096, so this mismatches: RED for the right reason.
    expected = Decimal("2") * Decimal("0.04") * (Decimal("1") + Decimal("30") / Decimal("100"))
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"per-unit override mismatch: got {evt['cost_usd']!r}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# RC6 — List then delete reverts to the flat fallback  (Must 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_then_delete_reverts_to_flat(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """Scenario: List then delete reverts to the flat fallback.

    RED reason: PUT/GET/DELETE /admin/rate-cards are not mounted yet -> 404, not
    the contracted 200/200/204.
    """
    model_id = "gpt-4o"
    owner_token = api_key["jwt"]

    put_resp = await client.put(
        f"/admin/rate-cards/{model_id}",
        json={"markup_pct": 50},
        headers=_bearer(owner_token),
    )
    assert put_resp.status_code == 200, put_resp.text
    assert Decimal(put_resp.json()["markup_pct"]) == Decimal("50")

    get_resp = await client.get("/admin/rate-cards", headers=_bearer(owner_token))
    assert get_resp.status_code == 200, get_resp.text
    entries = get_resp.json()["entries"]
    assert any(
        e["model_id"] == model_id and Decimal(e["markup_pct"]) == Decimal("50") for e in entries
    ), entries

    del_resp = await client.delete(f"/admin/rate-cards/{model_id}", headers=_bearer(owner_token))
    assert del_resp.status_code == 204, del_resp.text

    get_after = await client.get("/admin/rate-cards", headers=_bearer(owner_token))
    assert get_after.status_code == 200, get_after.text
    assert all(e["model_id"] != model_id for e in get_after.json()["entries"]), get_after.json()

    # Reverted to the flat fallback (tenant default markup_pct=20).
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o revert test"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.00001, 0.00003, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": api_key["tenant_id"], "m": model_id},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("1.20"), f"must revert to flat x1.20: {row}"


# ---------------------------------------------------------------------------
# RC7 — A non-OWNER cannot set a rate-card entry  (Reject 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_owner_cannot_set_403(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """Scenario: A non-OWNER cannot set a rate-card entry.

    RED reason: /admin/rate-cards is not mounted yet -> 404, not the contracted
    403 ERR_AUTH_FORBIDDEN (require_permission(Permission.RATE_CARDS_MANAGE) does
    not exist to even raise it).
    """
    tenant_id = uuid.UUID(api_key["tenant_id"])
    for role in (Role.ADMIN, Role.MEMBER):
        token = _issue_token(app, tenant_id=tenant_id, role=role)
        resp = await client.put(
            "/admin/rate-cards/gpt-4o", json={"markup_pct": 50}, headers=_bearer(token)
        )
        assert resp.status_code == 403, (
            f"role={role.value}: expected 403 got {resp.status_code}: {resp.text}"
        )
        assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN", resp.text

        # State unchanged — no row persisted. Only reachable post-build; pre-build
        # the assertions above already fail first (404 != 403), so this SELECT
        # against the not-yet-existing table never runs.
        row = (
            await db_session.execute(
                text(
                    "SELECT count(*) FROM tenant_rate_card_entries"
                    " WHERE tenant_id = :t AND model_id = 'gpt-4o'"
                ),
                {"t": api_key["tenant_id"]},
            )
        ).fetchone()
        assert row is not None and int(row[0]) == 0


# ---------------------------------------------------------------------------
# RC8 — Invalid markup value is rejected  (Reject 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_markup_422(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """Scenario: Invalid markup value is rejected.

    RED reason: /admin/rate-cards is not mounted yet -> 404, not the contracted 422.
    """
    owner_token = api_key["jwt"]

    for bad_markup in (-5, "not-a-number"):
        resp = await client.put(
            "/admin/rate-cards/gpt-4o",
            json={"markup_pct": bad_markup},
            headers=_bearer(owner_token),
        )
        assert resp.status_code == 422, (
            f"markup_pct={bad_markup!r}: expected 422 got {resp.status_code}: {resp.text}"
        )
        assert "code" in resp.json(), resp.text

    # No row persisted for either rejected attempt. Only reachable post-build;
    # pre-build the loop's assertions already fail first (404 != 422).
    row = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM tenant_rate_card_entries"
                " WHERE tenant_id = :t AND model_id = 'gpt-4o'"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).fetchone()
    assert row is not None and int(row[0]) == 0


# ---------------------------------------------------------------------------
# RC9 — Deleting a model with no entry is idempotent  (Reject 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_absent_entry_idempotent_204(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    """Scenario: Deleting a model with no entry is idempotent.

    RED reason: DELETE /admin/rate-cards/{model_id} is not mounted yet -> 404, not
    the contracted 204.
    """
    resp = await client.delete("/admin/rate-cards/gpt-4o", headers=_bearer(api_key["jwt"]))
    assert resp.status_code == 204, f"expected 204 got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# RC10 — Duplicate (tenant, model) create is an idempotent upsert  (Reject 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_set_is_idempotent_upsert(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """Scenario: Duplicate (tenant, model) create is an idempotent upsert.

    RED reason: PUT /admin/rate-cards/{model_id} is not mounted yet -> 404 on the
    FIRST call, not the contracted 200.
    """
    owner_token = api_key["jwt"]

    first = await client.put(
        "/admin/rate-cards/gpt-4o", json={"markup_pct": 50}, headers=_bearer(owner_token)
    )
    assert first.status_code == 200, first.text

    second = await client.put(
        "/admin/rate-cards/gpt-4o", json={"markup_pct": 30}, headers=_bearer(owner_token)
    )
    assert second.status_code == 200, second.text
    assert Decimal(second.json()["markup_pct"]) == Decimal("30")

    rows = (
        await db_session.execute(
            text(
                "SELECT markup_pct FROM tenant_rate_card_entries"
                " WHERE tenant_id = :t AND model_id = 'gpt-4o'"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).fetchall()
    assert len(rows) == 1, f"exactly one entry must remain for (tenant, gpt-4o); got {rows}"
    assert Decimal(str(rows[0][0])) == Decimal("30")


# ---------------------------------------------------------------------------
# RC11 — Authz matrix delta: RATE_CARDS_MANAGE is OWNER-only
# ---------------------------------------------------------------------------


def test_owner_holds_rate_cards_manage_matrix() -> None:
    """Scenario: authz matrix delta — RATE_CARDS_MANAGE is OWNER-only.

    RED reason: `Permission.RATE_CARDS_MANAGE` does not exist yet -> AttributeError.
    Deferred (function-local) import keeps this AttributeError CONTAINED to this one
    test — a module-level reference would AttributeError at collection and mask
    every other test's RED reason in this file.
    """
    from gateway.tenants.domain.authz import ROLE_PERMISSIONS, Permission

    assert Permission.RATE_CARDS_MANAGE in ROLE_PERMISSIONS[Role.OWNER], (
        "OWNER must hold RATE_CARDS_MANAGE (frozenset(Permission) superuser invariant)"
    )
    assert Permission.RATE_CARDS_MANAGE not in ROLE_PERMISSIONS[Role.ADMIN], (
        "ADMIN must NOT hold RATE_CARDS_MANAGE"
    )
    assert Permission.RATE_CARDS_MANAGE not in ROLE_PERMISSIONS[Role.MEMBER], (
        "MEMBER must NOT hold RATE_CARDS_MANAGE"
    )
