"""RED behavioral suite for `zdr-retention-inventory-extension` (§CHECKS rows 1, 3, 4).

RED at the current tree, and WHY — three independent absences, one per test:

  1. `_sweep_zdr_purge_pass` (usage/application/retention_sweep.py L992+) enumerates
     nine table names by hand and never touches vector_store_chunks, eval_cases,
     eval_case_results, finetune_jobs or finetune_job_events. A ZDR tenant's chunk
     text + embeddings, eval request bodies, model response text and finetune
     hyperparameters/error text therefore survive every sweep tick (M1).

  2. The finetune write path has ZERO ZDR gating: finetune/api/router.py:170 ->
     application/use_cases.py:97 `create_job` -> infrastructure/repository.py:38
     `create` are all unguarded, unlike vector_stores (ingest_worker.py:374) and evals
     (repository.py:83 / runs/repository.py:131) which are already locked-rechecked. A
     ZDR tenant's create returns 200 and persists rows (M4 / E3).

  3. Nothing re-reads `tenants.zdr_enabled` under lock anywhere on that path, so a flip
     landing in flight persists payload at rest — the exact TOCTOU shape this codebase
     was HARD-STOPPED on twice ([[zdr-toctou-async-write-paths]], M4 / E4 /
     R:TOCTOU_WRITE).

Infrastructure mirrors the shipped precedents rather than inventing fixtures:
  - `_make_sweeper` / `_set_tenant_zdr` from tests/retention_zdr/test_retention_zdr.py
  - the finetune provider/resolver doubles from tests/finetune_broker/test_finetune_broker.py
  - the held-uncommitted-flip TOCTOU harness from
    tests/vector_store_files/test_vector_store_files.py::
    test_worker_zdr_flip_inflight_at_finalize_fails_closed

DO NOT weaken these tests to pass — that is Build's job.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import text

from gateway.usage.application.retention_sweep import RetentionSweeper
from tests.retention_zdr_inventory._inventory import (
    EXPECTED_PRE_FIX_VICTIMS,
    INVENTORY_TUPLE_POINTER,
    consumed_inventory,
)

pytestmark = pytest.mark.asyncio

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
FINETUNE_JOBS = "/v1/fine_tuning/jobs"
PASSWORD = "correct horse battery staple"

# finetune-broker's supported model + the JSONL body its file validation accepts.
_MODEL = "gpt-4o-mini-2024-07-18"
_JSONL = b'{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"yo"}]}\n'

# The five payload tables this task adds to the ZDR purge inventory.
VICTIM_TABLES: tuple[str, ...] = (
    "vector_store_chunks",
    "eval_cases",
    "eval_case_results",
    "finetune_jobs",
    "finetune_job_events",
)

# A2: metadata containers that must SURVIVE the purge byte-for-byte. Deleting these
# would be over-purging — and relying on their FK cascade to remove the payload
# children is R:CASCADE_RELIANCE.
CONTAINER_TABLES: tuple[str, ...] = (
    "vector_stores",
    "vector_store_files",
    "eval_sets",
    "eval_runs",
    "eval_baselines",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _problem_code(resp: Any) -> str | None:
    """The error code out of either problem envelope this gateway emits.

    /v1 routers nest it (`{"error": {"code": ...}}`), /admin routers flatten it
    (`{"code": ...}`), and both shapes appear in shipped ZDR assertions
    (tests/evals/test_eval_set_store.py:182 vs tests/files_uploads_api:287). The
    contract pins the CODE and the STATUS, not the nesting — so read both rather than
    guess and produce a wrong-reason red.
    """
    try:
        body = resp.json()
    except Exception:  # pragma: no cover - a non-JSON body is itself a failure
        return None
    if not isinstance(body, dict):
        return None
    inner = body.get("error")
    if isinstance(inner, dict) and inner.get("code"):
        return str(inner["code"])
    code = body.get("code")
    return str(code) if code else None


async def _signup_and_key(client: Any, *, tenant_name: str, email: str) -> dict[str, str]:
    signup = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    login = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, f"login failed: {login.text}"
    created = await client.post(
        ADMIN_KEYS,
        json={"name": f"key-{tenant_name}"},
        headers=_bearer(login.json()["access_token"]),
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "tenant_id": tenant_id,
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
    }


async def _set_zdr(app: Any, tenant_id: str, *, enabled: bool = True) -> None:
    """Raw-SQL flip of tenants.zdr_enabled (the retention_zdr suite's idiom)."""
    async with app.state.sessionmaker() as session:
        await session.execute(
            text("UPDATE tenants SET zdr_enabled = :v WHERE id = :tid"),
            {"v": enabled, "tid": tenant_id},
        )
        await session.commit()


async def _count(app: Any, table: str, tenant_id: str) -> int:
    async with app.state.sessionmaker() as session:
        result = await session.execute(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :tid"),  # noqa: S608 — fixed table set, test-only
            {"tid": tenant_id},
        )
        return int(result.scalar_one())


def _make_sweeper(app: Any, *, object_store: Any = None) -> RetentionSweeper:
    """Mirrors tests/retention_zdr/test_retention_zdr.py::_make_sweeper.

    Windows are set wide (365d) on purpose: this check must isolate the UNCONDITIONAL
    ZDR purge pass, so no row may be removed by the age-cutoff passes instead — a
    cutoff-driven delete would be a green for the wrong reason.
    """
    from unittest.mock import MagicMock

    s = MagicMock()
    s.retention_check_interval_seconds = 86400
    s.retention_usage_records_days = 365
    s.retention_alert_events_days = 365
    s.retention_audit_events_days = 0
    s.retention_audit_floor_days = 0
    s.retention_batch_size = 1000
    s.retention_request_logs_days = 0
    s.retention_tenant_window_ceiling_days = 365
    return RetentionSweeper(
        session_factory=app.state.sessionmaker, settings=s, redis=None, object_store=object_store
    )


async def _seed_all_stores(app: Any, tenant_id: str) -> None:
    """Seed one row in each of the five payload tables AND each of their containers.

    Direct ORM inserts (the task's own grounding sanctions this) — the point of this
    check is the SWEEP, not the write paths, and two of those write paths are exactly
    what the sibling checks prove are ungated today.
    """
    from gateway.evals.infrastructure.orm import EvalCaseRow, EvalSetRow
    from gateway.evals.runs.infrastructure.orm import EvalCaseResultRow, EvalRunRow
    from gateway.evals.verdict.infrastructure.orm import EvalBaselineRow
    from gateway.finetune.infrastructure.orm import FinetuneJobEventRow, FinetuneJobRow
    from gateway.vector_stores.infrastructure.orm import (
        VectorStoreChunkRow,
        VectorStoreFileRow,
        VectorStoreRow,
    )

    tid = uuid.UUID(tenant_id)
    key_id = uuid.uuid4()

    async with app.state.sessionmaker() as session:
        # --- vector stores: container -> attach record -> payload chunk -------------
        store = VectorStoreRow(tenant_id=tid, key_id=key_id, name="vs-seed")
        session.add(store)
        await session.flush()
        vs_file = VectorStoreFileRow(
            vector_store_id=store.id, tenant_id=tid, file_id=uuid.uuid4(), status="completed"
        )
        session.add(vs_file)
        await session.flush()
        session.add(
            VectorStoreChunkRow(
                vector_store_file_id=vs_file.id,
                vector_store_id=store.id,
                tenant_id=tid,
                chunk_index=0,
                content="secret chunk text that must not survive a ZDR sweep",
                embedding=[0.0] * 1536,
            )
        )

        # --- evals: set -> case (payload); run -> case result (payload); baseline ----
        eval_set = EvalSetRow(tenant_id=tid, name=f"set-{uuid.uuid4().hex[:8]}")
        session.add(eval_set)
        await session.flush()
        session.add(
            EvalCaseRow(
                tenant_id=tid,
                eval_set_id=eval_set.id,
                request_body={"model": _MODEL, "messages": [{"role": "user", "content": "pii"}]},
                assertion={"kind": "contains", "expected": "4"},
            )
        )
        eval_run = EvalRunRow(
            tenant_id=tid, eval_set_id=eval_set.id, key_id=key_id, model=_MODEL, status="completed"
        )
        session.add(eval_run)
        await session.flush()
        session.add(
            EvalCaseResultRow(
                tenant_id=tid,
                eval_run_id=eval_run.id,
                eval_case_id=uuid.uuid4(),
                status="completed",
                response_text="model output payload at rest",
            )
        )
        session.add(EvalBaselineRow(tenant_id=tid, eval_set_id=eval_set.id, run_id=eval_run.id))

        # --- finetune: job (payload) -> event (payload) ------------------------------
        job = FinetuneJobRow(
            tenant_id=tid,
            key_id=key_id,
            provider="openai",
            model=_MODEL,
            training_file_id=uuid.uuid4(),
            validation_file_id=None,
            suffix="seed",
            hyperparameters={"n_epochs": 3},
            status="queued",
            error="provider unreachable at submit: payload-bearing error text",
        )
        session.add(job)
        await session.flush()
        session.add(
            FinetuneJobEventRow(
                job_id=job.id, tenant_id=tid, level="info", message="created", data={"k": "v"}
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# CHECK 1 — the sweep
# ---------------------------------------------------------------------------


async def test_zdr_sweep_purges_all_three_stores(client: Any, app: Any) -> None:
    """covers: M1, M2, M5, A1, A2, A3, A4, A6, E1, R:CASCADE_RELIANCE

    Seed all five payload tables (plus every metadata container) for a ZDR tenant AND a
    non-ZDR tenant, run ONE sweep_once(), then assert:
      - the ZDR tenant has ZERO rows in all five payload tables (M1, A3 wholesale
        delete, A4 next-tick cadence);
      - its containers survive untouched (M5, A2) — which is also what makes the child
        deletes provably explicit rather than FK cascade (R:CASCADE_RELIANCE);
      - the non-ZDR tenant's rows in all ten tables survive byte-for-byte (M5, E1, A1);
      - the pass reads ONE declared tuple the sweeper itself imports (M2).

    RED today: the ZDR pass never names these tables, so all five counts stay 1.
    """
    zdr = await _signup_and_key(client, tenant_name="ZdrCo", email="zdr-inv@example.io")
    keep = await _signup_and_key(client, tenant_name="KeepCo", email="keep-inv@example.io")
    await _seed_all_stores(app, zdr["tenant_id"])
    await _seed_all_stores(app, keep["tenant_id"])

    for table in VICTIM_TABLES + CONTAINER_TABLES:
        assert await _count(app, table, zdr["tenant_id"]) == 1, f"seed failed for {table}"
        assert await _count(app, table, keep["tenant_id"]) == 1, f"seed failed for {table}"

    await _set_zdr(app, zdr["tenant_id"], enabled=True)
    await _make_sweeper(app).sweep_once()

    # --- M1: every payload table zeroed for the ZDR tenant --------------------------
    survivors = {t: await _count(app, t, zdr["tenant_id"]) for t in VICTIM_TABLES}
    leaked = {t: n for t, n in survivors.items() if n != 0}
    assert not leaked, (
        "M1 — a ZDR tenant's payload survived a full sweep_once() in "
        f"{sorted(leaked)}: {leaked}. `_sweep_zdr_purge_pass` must issue an explicit "
        f"tenant_id-scoped DELETE for every table in {INVENTORY_TUPLE_POINTER}"
    )

    # --- M5 / A2 / R:CASCADE_RELIANCE: containers survive ---------------------------
    for table in CONTAINER_TABLES:
        assert await _count(app, table, zdr["tenant_id"]) == 1, (
            f"M5/A2 — metadata container {table} was purged for the ZDR tenant; only the "
            "five payload tables are in scope, containers carry ids/names/status and stay"
        )

    # --- M5 / E1 / A1: a non-ZDR tenant is untouched --------------------------------
    for table in VICTIM_TABLES + CONTAINER_TABLES:
        assert await _count(app, table, keep["tenant_id"]) == 1, (
            f"E1 — a NON-ZDR tenant lost its {table} row; tenant selection must stay "
            "tenants.zdr_enabled = true exactly as the existing pass (retention_sweep.py:414)"
        )

    # --- M2 / R:SECOND_INVENTORY: one tuple, consumed by the sweeper ----------------
    inventory, label = consumed_inventory()
    assert label == "retention_policy.ZDR_PURGE_TABLES", (
        "M2 — the ZDR purge pass must build its per-table deletes from ONE declared "
        f"tuple the guard and the sweeper both read: {INVENTORY_TUPLE_POINTER} "
        f"(currently: {label})"
    )
    absent = sorted(EXPECTED_PRE_FIX_VICTIMS - set(inventory))
    assert not absent, (
        f"M2/A6 — {absent} are purged but not declared in ZDR_PURGE_TABLES, so the "
        "guard and the sweeper have drifted apart again"
    )
    assert "finetune_job_events" in inventory, (
        "R:CASCADE_RELIANCE — finetune_job_events must have its OWN explicit DELETE "
        "statement in the inventory, never be left to the ON DELETE CASCADE from "
        "finetune_jobs; a re-parented or denormalized event row escapes a cascade"
    )


# ---------------------------------------------------------------------------
# Finetune doubles (mirror tests/finetune_broker/test_finetune_broker.py)
# ---------------------------------------------------------------------------


@dataclass
class _PortCall:
    op: str
    tenant_id: Any = None
    payload: dict[str, Any] = field(default_factory=dict)


class RecordingFinetuneProvider:
    """FinetuneProviderPort double that records every outbound call.

    `submit_delay` widens the provider await so a ZDR flip can be pinned inside it.
    """

    def __init__(self, submit_delay: float = 0.0) -> None:
        self.calls: list[_PortCall] = []
        self.submit_delay = submit_delay
        self.submit_entered = asyncio.Event()

    async def submit(self, tenant_id: Any, credential: Any, request: dict[str, Any]) -> str:
        self.calls.append(_PortCall("submit", tenant_id, dict(request)))
        self.submit_entered.set()
        if self.submit_delay:
            await asyncio.sleep(self.submit_delay)
        return f"ftjob-provider-{uuid.uuid4().hex[:8]}"

    async def poll(self, tenant_id: Any, credential: Any, provider_job_id: str) -> dict[str, Any]:
        self.calls.append(_PortCall("poll", tenant_id))
        return {"status": "running", "fine_tuned_model": None}

    async def cancel(self, tenant_id: Any, credential: Any, provider_job_id: str) -> None:
        self.calls.append(_PortCall("cancel", tenant_id))


class PerTenantResolver:
    """TenantCredentialResolver double — a distinct BYOK secret per tenant."""

    async def resolve(self, tenant_id: Any, provider: str) -> Any:
        from pydantic import SecretStr

        from gateway.proxy.domain.provider_credentials import BearerCredential

        return BearerCredential(secret=SecretStr(f"sk-tenant-{tenant_id}"))


async def _upload_training_file(client: Any, key: str) -> str:
    resp = await client.post(
        "/v1/files",
        files={"file": ("train.jsonl", _JSONL, "application/jsonl")},
        data={"purpose": "fine-tune"},
        headers=_bearer(key),
    )
    assert resp.status_code == 200, f"training-file upload failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def _create_job(client: Any, key: str, training_file: str) -> Any:
    return await client.post(
        FINETUNE_JOBS,
        json={"model": _MODEL, "training_file": training_file},
        headers=_bearer(key),
    )


# ---------------------------------------------------------------------------
# CHECK 3 — the entry gate
# ---------------------------------------------------------------------------


async def test_finetune_create_blocked_for_zdr_tenant(client: Any, app: Any) -> None:
    """covers: M4, A7, A8, A10, A14, A15, E3

    A ZDR tenant's POST /v1/fine_tuning/jobs must be refused 403
    ERR_ZDR_PAYLOAD_BLOCKED — the EXISTING problem shape, no new error surface (A15) —
    with zero finetune_jobs/finetune_job_events rows and ZERO provider calls (A10: the
    entry `raise_if_zdr` fires before any provider or persist work). Authz on the route
    is untouched (A14): a NON-ZDR tenant on the same app still creates successfully,
    which is what keeps this a data-policy gate rather than a broken route.

    RED today: finetune has NO ZDR gating anywhere (router.py:170 -> use_cases.py:97 ->
    repository.py:38), so the create returns 200 and persists payload at rest.
    """
    provider = RecordingFinetuneProvider()
    app.state.finetune_provider = provider
    app.state.tenant_credential_resolver = PerTenantResolver()

    zdr = await _signup_and_key(client, tenant_name="ZdrFt", email="zdr-ft@example.io")
    keep = await _signup_and_key(client, tenant_name="KeepFt", email="keep-ft@example.io")

    # Upload BEFORE the flip: POST /v1/files is itself ZDR-gated, and this check is
    # about the finetune seam, not the files seam.
    zdr_file = await _upload_training_file(client, zdr["key"])
    keep_file = await _upload_training_file(client, keep["key"])
    await _set_zdr(app, zdr["tenant_id"], enabled=True)

    calls_before = len(provider.calls)
    resp = await _create_job(client, zdr["key"], zdr_file)

    assert resp.status_code == 403, (
        "M4/E3 — a ZDR tenant's finetune create must be refused 403 "
        f"ERR_ZDR_PAYLOAD_BLOCKED; got {resp.status_code}: {resp.text}"
    )
    assert _problem_code(resp) == "ERR_ZDR_PAYLOAD_BLOCKED", (
        "A15 — the refusal must reuse the EXISTING ZDR problem shape (same as "
        f"files/evals), no new error surface; got {_problem_code(resp)!r}: {resp.text}"
    )
    assert await _count(app, "finetune_jobs", zdr["tenant_id"]) == 0, (
        "E3 — a refused create must persist ZERO finetune_jobs rows"
    )
    assert await _count(app, "finetune_job_events", zdr["tenant_id"]) == 0, (
        "A8/E3 — a refused create must persist ZERO finetune_job_events rows either"
    )
    assert len(provider.calls) == calls_before, (
        "A10 — the entry raise_if_zdr must fire BEFORE any provider call; a refused "
        f"tenant still cost a provider round-trip: {provider.calls[calls_before:]!r}"
    )

    # A14: the gate is data policy, not authz — a non-ZDR tenant is unaffected.
    ok = await _create_job(client, keep["key"], keep_file)
    assert ok.status_code == 200, (
        f"A14 — a NON-ZDR tenant must still create a finetune job: {ok.status_code} {ok.text}"
    )
    assert await _count(app, "finetune_jobs", keep["tenant_id"]) == 1


# ---------------------------------------------------------------------------
# CHECK 4 — the atomic (locked) re-check
# ---------------------------------------------------------------------------


async def test_finetune_zdr_flip_mid_await_refused(client: Any, app: Any) -> None:
    """covers: M4, A9, E4, R:TOCTOU_WRITE

    [[zdr-toctou-async-write-paths]] — HARD-STOPPED twice on this codebase. The entry
    check and the finetune persist are separated by the provider await, so `zdr_enabled`
    AT COMMIT TIME must decide: re-read under lock as the LAST statement before the
    create transaction commits, AFTER the provider await returns (A9/M4 — never before
    it, or the tenants row lock is held across an outbound round-trip).

    THE FLIP STARTS ONLY AFTER THE REQUEST IS ALREADY INSIDE `submit`, and that ordering
    is the whole point of this check. It is what makes the check DISCRIMINATE:

      entry gate only (plain `raise_if_zdr`)   -> entry read sees zdr=false (the flip has
        not happened yet), submit runs, the persist commits -> rows at rest -> RED.
      entry gate only (LOCKED `raise_if_zdr_locked` at entry) -> same outcome: the entry
        read completes and releases before the flip is ever issued, so the lock buys
        nothing -> rows at rest -> RED. **This is the case an earlier revision of this
        check could not distinguish** — it started the flip BEFORE driving the request,
        so an entry-only locked read blocked on it and passed, letting M4's second
        clause ship unimplemented.
      contracted fix (post-await locked re-check) -> the re-check runs after submit
        returns, BLOCKS on the row lock the holder still holds, reads zdr_enabled=true
        once the holder commits, and rolls the whole transaction back -> zero rows.

    Timing is structural, not racy (the shipped
    tests/vector_store_files::test_worker_zdr_flip_inflight_at_finalize_fails_closed
    harness, re-phased): the holder waits for `submit_entered`, then takes the tenants
    row lock with an UNCOMMITTED UPDATE and holds it 0.8s — strictly longer than the
    0.2s remaining submit window — so the lock is still held when the contracted
    re-check runs. A plain re-read cannot see an uncommitted UPDATE and is never
    blocked by it; only `SELECT ... FOR UPDATE` blocks.
    """
    provider = RecordingFinetuneProvider(submit_delay=0.2)
    app.state.finetune_provider = provider
    app.state.tenant_credential_resolver = PerTenantResolver()

    tenant = await _signup_and_key(client, tenant_name="FlipCo", email="flip-ft@example.io")
    training_file = await _upload_training_file(client, tenant["key"])

    async def _hold_uncommitted_flip() -> None:
        # Wait until the request is provably PAST its entry check and inside the
        # provider await before the flip exists at all.
        await provider.submit_entered.wait()
        async with app.state.sessionmaker() as session:
            await session.execute(
                text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
                {"tid": tenant["tenant_id"]},
            )
            await asyncio.sleep(0.8)  # > the 0.2s remaining submit: spans the commit window
            await session.commit()

    async def _drive() -> Any:
        return await _create_job(client, tenant["key"], training_file)

    resp, _ = await asyncio.gather(_drive(), _hold_uncommitted_flip())

    # CONFOUND GUARD 1: the entry check must have PASSED on zdr=false, i.e. the request
    # really did reach the provider await before the flip existed. This holds both
    # pre-fix (no gate) and post-fix (entry gate reads false and lets it through), so it
    # never masks the defect — it only proves the window under test was exercised. If
    # submit never ran, the 403 below would be an entry-gate refusal wearing this
    # check's name, and M4's second clause would go unproven.
    submits = [c for c in provider.calls if c.op == "submit"]
    assert len(submits) == 1, (
        "TEST CONFOUND, not a security failure: expected EXACTLY ONE provider submit "
        f"(the entry check passing on zdr=false, then the await this check measures), "
        f"saw {len(submits)}. A zero here means the request was refused at ENTRY and "
        "this run never exercised the post-await window at all."
    )

    # CONFOUND GUARD 2: if the flip never actually landed, a surviving row would be a
    # benign result read as a security failure. Fail here first, diagnosably.
    async with app.state.sessionmaker() as session:
        flipped = (
            await session.execute(
                text("SELECT zdr_enabled FROM tenants WHERE id = :tid"),
                {"tid": tenant["tenant_id"]},
            )
        ).scalar_one()
    assert flipped is True, (
        "TEST CONFOUND, not a security failure: the holder's flip never committed, so "
        "this run never exercised the in-flight window"
    )

    jobs = await _count(app, "finetune_jobs", tenant["tenant_id"])
    events = await _count(app, "finetune_job_events", tenant["tenant_id"])
    assert (jobs, events) == (0, 0), (
        "R:TOCTOU_WRITE — a ZDR flip that landed AFTER the entry check passed left "
        f"{jobs} finetune_jobs / {events} finetune_job_events rows at rest. The ZDR "
        "decision must be ATOMIC with the commit: raise_if_zdr_locked (SELECT ... FOR "
        "UPDATE) as the LAST statement before the create transaction commits — after "
        "the provider await returns, never before it (M4). Mirrors "
        "vector_stores/application/ingest_worker.py:374 and "
        "evals/infrastructure/repository.py:83. Neither a plain re-read NOR a locked "
        "check at ENTRY is enough: both complete before this flip is issued. It was "
        "HARD-STOPPED for exactly this twice ([[zdr-toctou-async-write-paths]])."
    )
    assert resp.status_code == 403, (
        "M4/E4 — the refused persist must surface as the contracted 403 "
        f"ERR_ZDR_PAYLOAD_BLOCKED, never a 200 over an empty write: {resp.status_code} {resp.text}"
    )
    assert _problem_code(resp) == "ERR_ZDR_PAYLOAD_BLOCKED", (
        f"A15 — expected the existing ZDR problem shape, got {_problem_code(resp)!r}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# CHECK 5 — blob-backed purge semantics (a GREEN-BY-DESIGN regression pin)
# ---------------------------------------------------------------------------


class RecordingObjectStore:
    """ObjectStore double that records every delete and can simulate an outage.

    Mirrors tests/retention_zdr/test_retention_zdr.py::FakeObjectStore (same put/get/
    delete/health surface, same ObjectStoreUnavailableError on a failing key); the only
    addition is `attempted`, which records keys the sweeper TRIED to delete even when
    the call raised — that is what proves the blob arm ran at all during an outage.
    """

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.attempted: list[str] = []
        self.deleted: list[str] = []
        self.fail_all = False

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.store[key] = data

    async def get(self, key: str) -> bytes:
        return self.store[key]

    async def delete(self, key: str) -> None:
        from gateway.objectstore.errors import ObjectStoreUnavailableError

        self.attempted.append(key)
        if self.fail_all:
            raise ObjectStoreUnavailableError(f"simulated outage for {key}")
        self.store.pop(key, None)
        self.deleted.append(key)

    async def health(self) -> bool:
        return not self.fail_all


async def _seed_blob_rows(app: Any, tenant_id: str, store: RecordingObjectStore) -> list[str]:
    """Seed one s3-backed artifacts row and one s3-backed files row. Returns object keys."""
    from gateway.artifacts.infrastructure.orm import ArtifactRow
    from gateway.files.infrastructure.orm import FileRow

    tid = uuid.UUID(tenant_id)
    key_id = uuid.uuid4()
    artifact_key = f"artifacts/{tenant_id}/{uuid.uuid4().hex}.bin"
    file_key = f"files/{tenant_id}/{uuid.uuid4().hex}.jsonl"
    store.store[artifact_key] = b"artifact payload bytes"
    store.store[file_key] = b"training payload bytes"

    async with app.state.sessionmaker() as session:
        session.add(
            ArtifactRow(
                tenant_id=tid,
                key_id=key_id,
                name="seed.bin",
                content_type="application/octet-stream",
                size_bytes=22,
                storage_backend="s3",
                object_key=artifact_key,
                content=None,
            )
        )
        session.add(
            FileRow(
                tenant_id=tid,
                key_id=key_id,
                filename="train.jsonl",
                purpose="fine-tune",
                bytes=22,
                content_type="application/jsonl",
                status="processed",
                storage_backend="s3",
                object_key=file_key,
                content=None,
            )
        )
        await session.commit()
    return [artifact_key, file_key]


async def test_blob_backed_purge_keeps_object_store_semantics(client: Any, app: Any) -> None:
    """covers: M5, A18, E5, R:BLOB_ORPHAN

    GREEN-BY-DESIGN, disclosed (the upload-bounds M4 two-phase precedent): this check
    pins SHIPPED behavior rather than driving new behavior, so it passes on the pre-fix
    tree. It is not a red-first check and must not be counted as one. It exists because
    M2 tells Build to drive every purge from ONE tuple, and the obvious way to do that —
    collapsing all fifteen names into a generic `DELETE FROM {t} WHERE tenant_id = :tid`
    — would silently destroy the object-store-aware arm that three of the existing nine
    tables depend on (retention_sweep.py:763-767). The rows would vanish, the S3 objects
    would not, and the object_key that was the ONLY way to ever find those bytes again
    would go with the row: a permanent orphan, unreachable by any later tick. A18 draws
    the line the tuple must respect — it names WHICH tables are purged, never HOW.

    Two phases, both pinning behavior Build must not weaken:
      Phase 1 (outage)  — the store raises for every key: the sweeper must ATTEMPT each
        blob delete and then DEFER both rows (still present, retryable next tick),
        never delete a row whose bytes may still be there (E5).
      Phase 2 (healed)  — the store recovers: the same sweeper deletes each object AND
        then its row. This is the half that fails if a generic row-DELETE replaced the
        blob-aware purger, because the rows would go without `delete` ever being called.
    """
    store = RecordingObjectStore()
    tenant = await _signup_and_key(client, tenant_name="BlobCo", email="blob-zdr@example.io")
    keys = await _seed_blob_rows(app, tenant["tenant_id"], store)
    await _set_zdr(app, tenant["tenant_id"], enabled=True)

    assert await _count(app, "artifacts", tenant["tenant_id"]) == 1, "artifacts seed failed"
    assert await _count(app, "files", tenant["tenant_id"]) == 1, "files seed failed"

    # --- Phase 1: object store unreachable -> attempt the blob, DEFER the row --------
    store.fail_all = True
    await _make_sweeper(app, object_store=store).sweep_once()

    assert sorted(store.attempted) == sorted(keys), (
        "E5/R:BLOB_ORPHAN — the ZDR purge of a blob-backed table must go through the "
        "object-store-aware purger and attempt each row's object delete; attempted="
        f"{store.attempted!r}, expected {keys!r}. A bare DELETE built from the new "
        "tuple would attempt nothing."
    )
    assert store.deleted == [], "the outage must mean no object was actually removed"
    deferred_artifacts = await _count(app, "artifacts", tenant["tenant_id"])
    deferred_files = await _count(app, "files", tenant["tenant_id"])
    assert (deferred_artifacts, deferred_files) == (1, 1), (
        "E5/R:BLOB_ORPHAN — with the object store unreachable, a blob-backed row must "
        f"be DEFERRED (left in place, retried next tick), not deleted: artifacts="
        f"{deferred_artifacts}, files={deferred_files}. Deleting the row strands its "
        "bytes where no later tick can ever find them — the object_key lived ON the row."
    )

    # --- Phase 2: store healed -> blob deleted first, THEN the row ------------------
    store.fail_all = False
    store.attempted.clear()
    await _make_sweeper(app, object_store=store).sweep_once()

    assert sorted(store.deleted) == sorted(keys), (
        "M5/A18 — once the store is healthy the ZDR purge must delete each row's object "
        f"before dropping the row; deleted={store.deleted!r}, expected {keys!r}"
    )
    assert store.store == {}, "every seeded object must be gone from the store"
    assert await _count(app, "artifacts", tenant["tenant_id"]) == 0, (
        "M5 — the deferred artifacts row must be purged on the retry tick"
    )
    assert await _count(app, "files", tenant["tenant_id"]) == 0, (
        "M5 — the deferred files row must be purged on the retry tick"
    )
