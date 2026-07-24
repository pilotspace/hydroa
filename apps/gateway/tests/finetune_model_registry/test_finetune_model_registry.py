"""RED/GREEN suite for finetune-model-registry (register a SUCCEEDED fine-tune's model
in the tenant catalog with a pricing snapshot — callable + billed via the shared paths).

Contract under test (finetune-model-registry PLAN.md §3, DRAFT):
  Flow (no new HTTP endpoint — a listener wired at the FROZEN finetune-broker extension
  point ``app.state.finetune_completion_listener``, invoked AFTER the winning CAS to
  "succeeded"):
    job succeeded + fine_tuned_model set
      -> models row  { id=<ft:*>, tenant_id=<owner>, provider=job.provider, modality="chat",
                       active=true, name=<ft:*>, region/context_length copied from base }
      -> pricing_snapshots row { model_id=<ft:*>, prompt/completion copied from the BASE
                                 model's LATEST snapshot, pricing_unit="per_token" }
  Tenant scoping: a tenant-owned models row is visible/callable ONLY to its owner —
    ModelChecker.check_for_tenant returns UNKNOWN (== model_not_found 404 path) for any
    other tenant; /v1/models listing excludes foreign tenant-owned rows.
  Billing: the ft model bills through the ONE shared rate-card resolver
    (resolve_markup_pct) + the recorder's latest-pricing_snapshots lookup — NO new
    billing mechanism; exact Decimal.
  Exactly-once: registration is idempotent on the ft model id (double-fire safe).
  Partial failure (broker D4): a listener crash never rolls back the CAS — the job stays
    "succeeded"; a repair sweep eventually registers the model. A registration with NO
    resolvable base pricing is DEFERRED (no unpriced callable model, no silent $0 row).
  Catalog sync: the provider-scoped deactivation sweep NEVER deactivates tenant-owned rows.

RED until the finetune_registry module + the models.tenant_id migration + wiring exist:
each test targets a symbol/column/behavior that does not exist yet, so the suite fails
for the RIGHT reason (missing implementation), not a broken harness.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text as sa_text

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fixtures (mirror tests/finetune_broker/test_finetune_broker.py)
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "token": token,
    }


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="FtRegA", email="ftreg-a@example.io", password="ftreg-a-battery-1"
    )


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="FtRegB", email="ftreg-b@example.io", password="ftreg-b-battery-1"
    )


_JSONL = b'{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"yo"}]}\n'
_BASE_MODEL = "gpt-4o-mini-2024-07-18"
_FT_MODEL = "ft:gpt-4o-mini-2024-07-18:acme::registry1"
_BASE_PROMPT = Decimal("0.0000001500")
_BASE_COMPLETION = Decimal("0.0000006000")


async def _upload_training_file(client: Any, key: str) -> str:
    resp = await client.post(
        "/v1/files",
        files={"file": ("train.jsonl", _JSONL, "application/jsonl")},
        data={"purpose": "fine-tune"},
        headers=_bearer(key),
    )
    assert resp.status_code == 200, f"training-file upload failed: {resp.status_code}: {resp.text}"
    return resp.json()["id"]


async def _seed_base_catalog(db_session: Any) -> None:
    """Seed the BASE model catalog row + its latest pricing snapshot (the pricing basis)."""
    await db_session.execute(
        sa_text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 128000, true, 'chat', 'openai')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": _BASE_MODEL, "name": _BASE_MODEL},
    )
    await db_session.execute(
        sa_text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, pricing_unit)"
            " VALUES (:id, :m, :p, :c, 'per_token')"
        ),
        {"id": str(uuid.uuid4()), "m": _BASE_MODEL, "p": str(_BASE_PROMPT), "c": str(_BASE_COMPLETION)},
    )
    await db_session.commit()


@dataclass
class _PortCall:
    op: str
    secret: str
    payload: dict[str, Any] = field(default_factory=dict)
    tenant_id: Any = None


class FakeFinetuneProvider:
    """In-memory FinetuneProviderPort (mirrors the broker suite's fake)."""

    def __init__(self) -> None:
        self.calls: list[_PortCall] = []
        self.poll_status: str = "running"
        self.poll_fine_tuned_model: str | None = None

    @staticmethod
    def _secret_of(credential: object) -> str:
        return str(getattr(credential, "secret", credential))

    async def submit(self, tenant_id: object, credential: object, request: dict[str, Any]) -> str:
        self.calls.append(_PortCall("submit", self._secret_of(credential), dict(request), tenant_id))
        return f"ftjob-provider-{uuid.uuid4().hex[:8]}"

    async def poll(
        self, tenant_id: object, credential: object, provider_job_id: str
    ) -> dict[str, Any]:
        self.calls.append(_PortCall("poll", self._secret_of(credential), tenant_id=tenant_id))
        return {"status": self.poll_status, "fine_tuned_model": self.poll_fine_tuned_model}

    async def cancel(self, tenant_id: object, credential: object, provider_job_id: str) -> None:
        self.calls.append(_PortCall("cancel", self._secret_of(credential), tenant_id=tenant_id))


class RecordingPerTenantResolver:
    def __init__(self) -> None:
        self.secrets: dict[str, str] = {}

    async def resolve(self, tenant_id: object, provider: str) -> object:
        from gateway.proxy.domain.provider_credentials import BearerCredential

        tid = str(tenant_id)
        secret = self.secrets.setdefault(tid, f"sk-tenant-{tid}-{uuid.uuid4().hex[:6]}")
        return BearerCredential(secret=secret)


@pytest.fixture
def provider_port(app: Any) -> FakeFinetuneProvider:
    port = FakeFinetuneProvider()
    app.state.finetune_provider = port
    return port


@pytest.fixture
def resolver(app: Any) -> RecordingPerTenantResolver:
    r = RecordingPerTenantResolver()
    app.state.tenant_credential_resolver = r
    return r


async def _drive_job_to_succeeded(
    client: Any,
    key: str,
    provider_port: FakeFinetuneProvider,
    *,
    fine_tuned_model: str = _FT_MODEL,
) -> str:
    """Create a job, then GET it with the provider reporting succeeded — the GET's poll
    commits the terminal CAS and fires the completion listener (broker M7, FROZEN)."""
    file_id = await _upload_training_file(client, key)
    created = await client.post(
        "/v1/fine_tuning/jobs",
        json={"model": _BASE_MODEL, "training_file": file_id},
        headers=_bearer(key),
    )
    assert created.status_code == 200, f"job create failed: {created.text}"
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"

    provider_port.poll_status = "succeeded"
    provider_port.poll_fine_tuned_model = fine_tuned_model
    got = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(key))
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "succeeded", got.json()
    assert got.json()["fine_tuned_model"] == fine_tuned_model
    return job_id


async def _model_row(db_session: Any, model_id: str) -> Any:
    return (
        await db_session.execute(
            sa_text("SELECT id, tenant_id, provider, modality, active, name FROM models WHERE id = :m"),
            {"m": model_id},
        )
    ).mappings().one_or_none()


# ---------------------------------------------------------------------------
# M1/M2 — the listener registers the model + pricing snapshot
# ---------------------------------------------------------------------------


class TestRegistration:
    async def test_listener_wired_and_registers_model_on_success(
        self,
        client: Any,
        app: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M1: create_app wires a real registrar at the FROZEN extension point, and a
        succeeded job produces a tenant-owned, active catalog models row for ft:*."""
        assert app.state.finetune_completion_listener is not None, (
            "app.state.finetune_completion_listener is None — the finetune-model-registry"
            " registrar is not wired at the broker's FROZEN extension point"
        )
        await _seed_base_catalog(db_session)
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)

        row = await _model_row(db_session, _FT_MODEL)
        assert row is not None, "succeeded fine-tune did not register a models row"
        assert str(row["tenant_id"]) == tenant_a["tenant_id"]
        assert row["provider"] == "openai"
        assert row["modality"] == "chat"
        assert row["active"] is True
        assert row["name"] == _FT_MODEL

    async def test_registration_creates_pricing_snapshot_from_base(
        self,
        client: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M2: the ft model's snapshot copies the BASE model's LATEST snapshot prices
        (provider-passthrough basis), pricing_unit per_token — exact Decimal equality."""
        await _seed_base_catalog(db_session)
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)

        snap = (
            await db_session.execute(
                sa_text(
                    "SELECT prompt_usd_per_token, completion_usd_per_token, pricing_unit"
                    " FROM pricing_snapshots WHERE model_id = :m"
                    " ORDER BY captured_at DESC LIMIT 1"
                ),
                {"m": _FT_MODEL},
            )
        ).mappings().one_or_none()
        assert snap is not None, "no pricing snapshot registered for the ft model"
        assert Decimal(str(snap["prompt_usd_per_token"])) == _BASE_PROMPT
        assert Decimal(str(snap["completion_usd_per_token"])) == _BASE_COMPLETION
        assert snap["pricing_unit"] == "per_token"


# ---------------------------------------------------------------------------
# M3 — billed via the ONE shared rate-card resolver (no new billing mechanism)
# ---------------------------------------------------------------------------


class TestBilling:
    async def test_billing_resolves_through_shared_rate_card_resolver(
        self,
        client: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M3: the registered ft model prices through resolve_markup_pct (the SAME
        shared resolver every billed request uses) + the recorder's latest-snapshot
        lookup — exact Decimal: effective = base_price * (1 + markup/100)."""
        from gateway.usage.application.rate_card_resolver import resolve_markup_pct

        await _seed_base_catalog(db_session)
        await db_session.execute(
            sa_text("UPDATE tenants SET markup_pct = 25 WHERE id = :t"),
            {"t": tenant_a["tenant_id"]},
        )
        await db_session.commit()
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)

        # The recorder's EXACT lookup shape: latest snapshot for the served model id.
        snap = (
            await db_session.execute(
                sa_text(
                    "SELECT prompt_usd_per_token FROM pricing_snapshots"
                    " WHERE model_id = :m ORDER BY captured_at DESC LIMIT 1"
                ),
                {"m": _FT_MODEL},
            )
        ).fetchone()
        assert snap is not None, "shared billing path cannot price the ft model (no snapshot)"
        markup = await resolve_markup_pct(
            db_session, uuid.UUID(tenant_a["tenant_id"]), _FT_MODEL
        )
        assert markup == Decimal("25")
        effective = Decimal(str(snap[0])) * (Decimal("1") + markup / Decimal("100"))
        assert effective == _BASE_PROMPT * Decimal("1.25")


# ---------------------------------------------------------------------------
# M4 — tenant scoping: 404-never-leak + listing exclusion
# ---------------------------------------------------------------------------


class TestTenantScoping:
    async def test_cross_tenant_model_access_is_unknown(
        self,
        client: Any,
        app: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M4: the proxy's tenant gate (ModelChecker.check_for_tenant — the symbol the
        completions hot path consults) returns ACTIVE for the owner and UNKNOWN (the
        model_not_found 404 path) for any other tenant — never a distinguishable state."""
        from gateway.proxy.domain.ports import ModelAccess
        from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker

        await _seed_base_catalog(db_session)
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)

        checker = SqlAlchemyModelChecker(db_session)
        owner = await checker.check_for_tenant(_FT_MODEL, uuid.UUID(tenant_a["tenant_id"]))
        foreign = await checker.check_for_tenant(_FT_MODEL, uuid.UUID(tenant_b["tenant_id"]))
        assert owner is ModelAccess.ACTIVE
        assert foreign is ModelAccess.UNKNOWN, (
            f"cross-tenant ft model leaked: expected UNKNOWN, got {foreign}"
        )

    async def test_listing_excludes_foreign_tenant_models(
        self,
        client: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M4: /v1/models (dashboard JWT branch) lists the ft model for its OWNER only;
        another tenant's listing never contains it (no existence leak)."""
        await _seed_base_catalog(db_session)
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)

        a_list = await client.get(
            "/v1/models", headers={"Authorization": f"Bearer {tenant_a['token']}"}
        )
        b_list = await client.get(
            "/v1/models", headers={"Authorization": f"Bearer {tenant_b['token']}"}
        )
        assert a_list.status_code == 200, a_list.text
        assert b_list.status_code == 200, b_list.text
        a_ids = [m["id"] for m in a_list.json()["data"]]
        b_ids = [m["id"] for m in b_list.json()["data"]]
        assert _FT_MODEL in a_ids, "owner tenant cannot see its own registered ft model"
        assert _FT_MODEL not in b_ids, "foreign tenant's listing leaks a tenant-owned ft model"


# ---------------------------------------------------------------------------
# M5 — exactly-once: idempotent on the ft model id (double-fire safe)
# ---------------------------------------------------------------------------


class TestExactlyOnce:
    async def test_double_fire_listener_registers_exactly_once(
        self,
        client: Any,
        app: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M5: invoking the registrar twice for the same succeeded job (defensive
        double-fire) leaves exactly ONE models row and ONE pricing snapshot."""
        # Symbol under test — does not exist yet (red for missing implementation).
        from gateway.finetune_registry.application.registrar import (  # noqa: PLC0415
            FinetuneModelRegistrar,
        )

        await _seed_base_catalog(db_session)
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)
        job_row = (
            await db_session.execute(
                sa_text("SELECT * FROM finetune_jobs WHERE tenant_id = :t"),
                {"t": tenant_a["tenant_id"]},
            )
        ).mappings().one()

        registrar = FinetuneModelRegistrar(session_factory=app.state.sessionmaker)
        await registrar.on_succeeded(job_row)  # fire #2 (the HTTP drive was #1)
        await registrar.on_succeeded(job_row)  # fire #3

        n_models = (
            await db_session.execute(
                sa_text("SELECT count(*) FROM models WHERE id = :m"), {"m": _FT_MODEL}
            )
        ).scalar_one()
        n_snaps = (
            await db_session.execute(
                sa_text("SELECT count(*) FROM pricing_snapshots WHERE model_id = :m"),
                {"m": _FT_MODEL},
            )
        ).scalar_one()
        assert n_models == 1, f"expected exactly one models row, got {n_models}"
        assert n_snaps == 1, f"expected exactly one pricing snapshot, got {n_snaps}"


# ---------------------------------------------------------------------------
# M6/R1 — partial failure: CAS survives, repair registers; unpriced never registers
# ---------------------------------------------------------------------------


class TestPartialFailure:
    async def test_missing_base_pricing_defers_registration(
        self,
        client: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """R1: NO base pricing snapshot resolvable -> the model is NOT registered (an
        unpriced callable model would produce silent-$0 usage rows) AND the job stays
        'succeeded' (broker D4: a listener failure never rolls back the CAS)."""
        # NOTE: deliberately no _seed_base_catalog — the pricing basis is unresolvable.
        job_id = await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)

        row = await _model_row(db_session, _FT_MODEL)
        assert row is None, "registered a CALLABLE model with no pricing basis (silent-$0 risk)"
        got = await client.get(f"/v1/fine_tuning/jobs/{job_id}", headers=_bearer(tenant_a["key"]))
        assert got.status_code == 200
        assert got.json()["status"] == "succeeded", "listener failure must never touch the CAS"

    async def test_repair_sweep_registers_missed_model(
        self,
        client: Any,
        app: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M6: a registration missed at listener time (here: pricing basis appeared
        AFTER the job succeeded) is eventually registered by the idempotent repair
        sweep scanning succeeded-but-unregistered jobs."""
        from gateway.finetune_registry.application.registrar import (  # noqa: PLC0415
            FinetuneModelRegistrar,
        )

        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)
        assert await _model_row(db_session, _FT_MODEL) is None  # missed (no pricing basis yet)

        await _seed_base_catalog(db_session)  # the basis becomes resolvable
        registrar = FinetuneModelRegistrar(session_factory=app.state.sessionmaker)
        repaired = await registrar.repair_missed()
        assert repaired >= 1, "repair sweep did not register the missed model"

        row = await _model_row(db_session, _FT_MODEL)
        assert row is not None
        assert str(row["tenant_id"]) == tenant_a["tenant_id"]


# ---------------------------------------------------------------------------
# M7 — catalog sync never deactivates tenant-owned models
# ---------------------------------------------------------------------------


class TestSyncSurvival:
    async def test_catalog_sync_never_deactivates_tenant_models(
        self,
        client: Any,
        app: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M7: the provider-scoped deactivation sweep in sync_catalog treats tenant-owned
        rows as OUT of the sweep — an upstream sync of provider 'openai' models that does
        not mention the ft model must leave it active."""
        from gateway.catalog.domain.entities import CatalogModel
        from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

        await _seed_base_catalog(db_session)
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)
        assert (await _model_row(db_session, _FT_MODEL)) is not None

        async with app.state.sessionmaker() as session:
            repo = SqlAlchemyCatalogRepository(session)
            await repo.sync_catalog(
                [
                    CatalogModel(
                        id=_BASE_MODEL,
                        name=_BASE_MODEL,
                        context_length=128000,
                        prompt_usd_per_token=_BASE_PROMPT,
                        completion_usd_per_token=_BASE_COMPLETION,
                        modality="chat",
                        provider="openai",
                    )
                ],
                embedding_models=[],
            )

        row = await _model_row(db_session, _FT_MODEL)
        assert row is not None
        assert row["active"] is True, (
            "catalog sync deactivated a tenant-owned fine-tuned model — the sweep must"
            " exclude rows with tenant_id IS NOT NULL"
        )


# ---------------------------------------------------------------------------
# M8 — preset-target validation must not be a cross-tenant existence oracle
# ---------------------------------------------------------------------------


class TestPresetOracle:
    async def test_preset_target_validation_is_tenant_scoped(
        self,
        client: Any,
        app: Any,
        db_session: Any,
        tenant_a: dict[str, str],
        tenant_b: dict[str, str],
        provider_port: FakeFinetuneProvider,
        resolver: RecordingPerTenantResolver,
    ) -> None:
        """M8 (advisor finding): TenantModelPresetStore.upsert validates targets via the
        tenant-blind is_active — once tenant-owned rows exist that is a cross-tenant
        existence oracle. A foreign tenant naming another tenant's ft model as a preset
        target must get the SAME ERR_PRESET_TARGET_UNKNOWN as a nonexistent model,
        while the OWNER may target its own ft model."""
        from gateway.proxy.domain.model_presets import ModelPresetError
        from gateway.proxy.infrastructure.tenant_model_preset_store import (
            DbTenantModelPresetStore,
        )

        await _seed_base_catalog(db_session)
        await _drive_job_to_succeeded(client, tenant_a["key"], provider_port)

        store = DbTenantModelPresetStore(sessionmaker=app.state.sessionmaker)

        # Owner: its own ft model is a valid preset target.
        await store.upsert(
            uuid.UUID(tenant_a["tenant_id"]), "prod", "chat", _FT_MODEL
        )

        # Foreign tenant: byte-identical rejection to a nonexistent model (no oracle).
        with pytest.raises(ModelPresetError) as leaked:
            await store.upsert(uuid.UUID(tenant_b["tenant_id"]), "prod", "chat", _FT_MODEL)
        with pytest.raises(ModelPresetError) as absent:
            await store.upsert(
                uuid.UUID(tenant_b["tenant_id"]), "prod", "chat", f"ft:nope::{uuid.uuid4().hex[:6]}"
            )
        assert str(leaked.value) == str(absent.value), (
            "preset upsert distinguishes a foreign tenant-owned model from a nonexistent"
            f" one — existence oracle: {leaked.value!r} vs {absent.value!r}"
        )
