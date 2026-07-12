"""Adversarial VERIFY probes for credits-ledger (payments-adversary refute-read).

NOT part of the frozen §4 suite — these are verify-authored red repros for gaps the
frozen suite does not cover. Left uncommitted per the verify dispatch. Run:

    cd apps/gateway && uv run pytest tests/credits_ledger/test_verify_adversarial.py -q --no-cov
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.credits_ledger.conftest import (
    auth_key,
    get_balance_row,
    ledger_count,
    poll_until,
    seed_balance,
)


# ---------------------------------------------------------------------------
# FINDING 1 (🔴 blocker candidate): settle()/release() read "is this hold still
# open" via find_open_hold() BEFORE taking the tenant balance row lock. Two
# concurrent finalizers for the SAME (tenant_id, request_id) — e.g. the M6
# reconciliation sweep racing a slow-but-legitimate completion, or a duplicate
# settle fire — can BOTH see the hold as "open" and BOTH post a settle/release
# row against it: a double-credit (or double-debit) of one hold. M4/M5's
# "exactly one SETTLE or RELEASE" invariant is enforced only against sequential
# callers, not concurrent ones — the same TOCTOU class M3 closes for admission
# is NOT closed for finalization.
# ---------------------------------------------------------------------------


async def test_verify_concurrent_settle_and_release_double_post_same_hold(
    db_session: AsyncSession,
    app: Any,
) -> None:
    """Fire settle() and release() concurrently against the SAME open hold
    (simulates the sweep racing a late-arriving completion, or a duplicate
    finalize event). Frozen M4/M5 requires EXACTLY one settle-or-release per
    hold; the row-lock discipline from M3 is not reused here, so both should
    NOT be able to post."""
    from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard

    tenant_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="5.00", grace_usd="0")

    guard = PostgresCreditGuard(session_factory=app.state.sessionmaker)
    request_id = uuid.uuid4()

    # Place the hold synchronously first (not part of the race under test).
    await guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))
    balance_after_hold = await get_balance_row(db_session, str(tenant_id))
    assert balance_after_hold is not None
    assert Decimal(balance_after_hold[0]) == Decimal("4.50")

    usage_record_id = uuid.uuid4()

    # Race: settle(actual=0.20) [-> +0.30 refund] concurrently with release()
    # [-> +0.50 full refund] against the SAME open hold, separate guard
    # instances / separate sessions (mirrors the M3 concurrency-test shape).
    guard_a = PostgresCreditGuard(session_factory=app.state.sessionmaker)
    guard_b = PostgresCreditGuard(session_factory=app.state.sessionmaker)
    await asyncio.gather(
        guard_a.settle(tenant_id, request_id, usage_record_id, Decimal("0.20")),
        guard_b.release(tenant_id, request_id),
        return_exceptions=True,
    )

    # Exactly one finalize row should exist for this request_id (settle XOR
    # release) — never both.
    finalize_rows = await _finalize_row_count(db_session, tenant_id, request_id)
    assert finalize_rows == 1, (
        f"expected exactly ONE settle-or-release row for request_id={request_id}, "
        f"found {finalize_rows} — the hold was double-finalized (double-credit/debit)"
    )


async def _finalize_row_count(session: AsyncSession, tenant_id: uuid.UUID, request_id: uuid.UUID) -> int:
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM credit_ledger"
                " WHERE tenant_id = :t AND request_id = :r AND entry_type IN ('settle','release')"
            ),
            {"t": str(tenant_id), "r": str(request_id)},
        )
    ).fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# FINDING 2 (🟡 concern): topup()'s idempotency-key conflict check (R4) is a
# GLOBAL lookup (find_topup_by_idempotency_key is unscoped by tenant_id) but
# is only serialized against the CALLER's own tenant balance row lock. Two
# concurrent topups for the SAME key but DIFFERENT tenants lock DIFFERENT
# balance rows, so neither blocks the other -> both can commit, silently
# violating R4 (which says a cross-tenant key reuse must 409, never double
# insert) under true concurrency. Only superadmin can reach this path, so it
# is a financial/audit-integrity gap, not an attacker-facing exploit -- still
# real money duplicated across two tenants from ONE operator action.
# ---------------------------------------------------------------------------


async def test_verify_concurrent_topup_same_key_different_tenants_bypasses_r4(
    db_session: AsyncSession,
    app: Any,
) -> None:
    """Forces genuine interleaving (an asyncio.Barrier) at the idempotency-key
    read so both transactions reach `find_topup_by_idempotency_key` before
    either commits — a plain `asyncio.gather` alone is NOT sufficient to prove
    this race (observed: without forcing, the two coroutines usually run
    close-to-sequentially on a local test DB and R4 appears to hold by
    accident; the barrier removes that scheduling luck)."""
    from gateway.credits.application import topup_service
    from gateway.credits.infrastructure import ledger_store

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await seed_balance(db_session, str(tenant_a), balance_usd="0", grace_usd="0")
    await seed_balance(db_session, str(tenant_b), balance_usd="0", grace_usd="0")

    key = f"race-{uuid.uuid4()}"

    barrier = asyncio.Barrier(2)
    original = ledger_store.find_topup_by_idempotency_key

    async def _rendezvous_then_lookup(session: AsyncSession, idempotency_key: str) -> Any:
        await barrier.wait()
        return await original(session, idempotency_key)

    topup_service.find_topup_by_idempotency_key = _rendezvous_then_lookup  # type: ignore[attr-defined]
    try:
        results = await asyncio.gather(
            topup_service.topup(
                app.state.sessionmaker,
                tenant_id=tenant_a,
                amount_usd=Decimal("100.00"),
                idempotency_key=key,
            ),
            topup_service.topup(
                app.state.sessionmaker,
                tenant_id=tenant_b,
                amount_usd=Decimal("50.00"),
                idempotency_key=key,
            ),
            return_exceptions=True,
        )
    finally:
        topup_service.find_topup_by_idempotency_key = original  # type: ignore[attr-defined]

    successes = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, BaseException)]

    # R4's intent: a key reused across tenants is a CONFLICT (409) — at most
    # one topup should ever succeed for a shared key. Under forced concurrent
    # interleaving both commit (both balance-row locks are disjoint, so
    # neither transaction blocks the other's idempotency-key read).
    assert len(successes) == 1, (
        f"R4 requires a cross-tenant idempotency-key reuse to conflict — expected "
        f"exactly 1 successful topup and 1 CREDITS_IDEMPOTENCY_KEY_CONFLICT under "
        f"genuine concurrency, got {len(successes)} successes / {len(conflicts)} "
        f"conflicts: {results!r}"
    )


# ---------------------------------------------------------------------------
# FINDING 3 probe (informational, expect PASS): double-spend across BOTH
# pipelines (chat + a direct guard call standing in for the embeddings choke
# point) against one near-zero balance, real Postgres row locks.
# ---------------------------------------------------------------------------


async def test_verify_cross_pipeline_concurrent_admission_closed_by_row_lock(
    db_session: AsyncSession,
    app: Any,
) -> None:
    """Two DIFFERENT guard instances (standing in for the chat and non-chat
    choke points, which both resolve to the SAME app.state.credit_guard in
    production) racing check_and_hold for the SAME tenant at a balance that
    can only cover ONE hold. Confirms M3's row lock closes the race
    regardless of which pipeline the caller is."""
    from gateway.core.errors import ProblemError
    from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard

    tenant_id = uuid.uuid4()
    await seed_balance(db_session, str(tenant_id), balance_usd="0.50", grace_usd="0")

    chat_guard = PostgresCreditGuard(session_factory=app.state.sessionmaker)
    embeddings_guard = PostgresCreditGuard(session_factory=app.state.sessionmaker)

    results = await asyncio.gather(
        chat_guard.check_and_hold(tenant_id, uuid.uuid4(), Decimal("0.50")),
        embeddings_guard.check_and_hold(tenant_id, uuid.uuid4(), Decimal("0.50")),
        return_exceptions=True,
    )
    successes = [r for r in results if r is None]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ProblemError)
    assert failures[0].status == 402

    count = await ledger_count(db_session, str(tenant_id))
    assert count == 1


# ---------------------------------------------------------------------------
# FINDING 4 probe (informational, expect PASS): 10-way concurrent hold burst
# against a balance sized for exactly 2 — worst-case negative-balance bound.
# ---------------------------------------------------------------------------


async def test_verify_ten_way_concurrent_holds_never_exceed_grace_bound(
    db_session: AsyncSession,
    app: Any,
) -> None:
    from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard

    tenant_id = uuid.uuid4()
    # balance covers exactly 2 holds of 0.50 each; grace = 0.
    await seed_balance(db_session, str(tenant_id), balance_usd="1.00", grace_usd="0")

    guards = [PostgresCreditGuard(session_factory=app.state.sessionmaker) for _ in range(10)]
    results = await asyncio.gather(
        *[g.check_and_hold(tenant_id, uuid.uuid4(), Decimal("0.50")) for g in guards],
        return_exceptions=True,
    )
    successes = [r for r in results if r is None]
    assert len(successes) == 2, f"expected exactly 2 admissions to fit balance=1.00, got {len(successes)}"

    balance = await get_balance_row(db_session, str(tenant_id))
    assert balance is not None
    assert Decimal(balance[0]) == Decimal("0.00"), "balance must never go negative when grace=0"


# ---------------------------------------------------------------------------
# FINDING 3 / HEAL FIXED 2026-07-12 (🔴 blocker candidate — quantified disclosure
# #5): the non-chat pipeline (governance.py:NonChatGovernance) NEVER set
# _credit_hold_ctx (that contextvar was set ONLY from
# CompletionUseCase.complete()/stream()) — so a successful non-chat request
# settled NOTHING: the hold sat until the M6 sweep blindly RELEASEd the FULL
# hold_estimate_usd after hold_timeout_s, regardless of the real metered cost.
# FIXED: NonChatGovernance.authorize() now calls `_credit_hold_ctx.set(...)`
# (imported from use_cases.py, the SAME ContextVar) right after check_and_hold
# succeeds — _dispatch_record (fired later in the same asyncio Task from the
# non-chat use case's _fire_record_with_raw/_fire_record_cached) now finds the
# open hold and settles/releases it exactly like chat. See
# gateway/proxy/application/governance.py for the fix.
# ---------------------------------------------------------------------------


async def test_verify_nonchat_success_never_settles_only_sweep_fully_refunds(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    credit_guard: Any,
) -> None:
    """HEAL (2026-07-12): finding 2 (non-chat settle never wired) is FIXED —
    NonChatGovernance.authorize() now publishes _credit_hold_ctx (the same
    ContextVar the chat pipeline uses) right after check_and_hold succeeds, so
    _dispatch_record's settle/release done-callback fires for images/audio/
    embeddings exactly like chat. This test originally proved the GAP (only a
    'hold' row ever posted, the sweep blindly fully refunded it regardless of
    real cost) — its own docstring called that premise out as something that
    would go stale once fixed ("If this now includes 'settle', the non-chat
    settle gap has been fixed and this test's premise is stale"). It now
    proves the FIX instead: (1) a real 'settle' row posts reflecting the
    ACTUAL metered cost (not just a full hold refund); (2) the M6 sweep finds
    NO orphaned hold for this request (it is no longer "open" — a settle
    sibling already exists), so sweep_once() is a no-op and the balance is
    untouched by it."""
    from gateway.credits.application.recovery_sweep import CreditHoldRecoverySweeper
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

    model_id = "text-embedding-3-small-verify"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 8192, true, 'embedding', 'openai')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": model_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.01, 0.01, now()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    await seed_balance(db_session, api_key["tenant_id"], balance_usd="5.00", grace_usd="0")

    class _FakeEmbeddingProvider:
        name = "openai"

        async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            # Deliberately large token usage -> a real, non-trivial metered cost
            # (1000 prompt tokens * 0.01 usd/token = $10 real cost, FAR exceeding
            # the $0.50 hold estimate) that should show up as a settle debit.
            return 200, {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": model_id,
                "usage": {"prompt_tokens": 1000, "total_tokens": 1000},
            }

        async def post_multipart(self, *a: Any, **kw: Any) -> tuple[int, dict[str, Any]]:
            return 200, {}

        def stream_bytes(self, *a: Any, **kw: Any) -> Any:
            async def _gen() -> Any:
                yield b""

            return _gen()

    registry = getattr(app.state, "provider_registry", None)
    if registry is not None and hasattr(registry, "_providers"):
        registry._providers["openai"] = _FakeEmbeddingProvider()  # type: ignore[attr-defined]
    else:
        app.state.provider_registry = ProviderRegistry({"openai": _FakeEmbeddingProvider()})  # type: ignore[arg-type]

    resp = await client.post(
        "/v1/embeddings",
        json={"model": model_id, "input": "hello world"},
        headers=auth_key(api_key["key"]),
    )
    assert resp.status_code == 200, resp.text

    # Give any fire-and-forget usage-recording task a moment to land.
    await asyncio.sleep(0.5)

    rows = (
        await db_session.execute(
            text(
                "SELECT entry_type, amount_usd FROM credit_ledger WHERE tenant_id = :t"
                " ORDER BY created_at"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).all()
    entry_types = [r[0] for r in rows]
    # HEAL: settle now posts for the non-chat pipeline (was ["hold"] before the fix).
    assert entry_types == ["hold", "settle"], (
        f"expected a hold THEN a settle row for a successful non-chat request now that "
        f"finding 2 (non-chat settle never wired) is fixed — got {entry_types!r}"
    )

    # HEAL: 1000 prompt tokens @ $0.01/tok = $10.00 base cost; tenant markup_pct
    # defaults 20% -> $12.00 billed. settle = hold_estimate(0.50) - actual(12.00) =
    # -11.50 (an additional debit FAR beyond the hold, exactly what the real spend
    # was — the bug this test used to prove was that this NEVER happened).
    settle_row = next(r for r in rows if r[0] == "settle")
    assert Decimal(str(settle_row[1])) == Decimal("-11.50000000"), (
        f"settle must reflect the REAL metered cost (hold 0.50 - actual 12.00 = -11.50), "
        f"got {settle_row[1]}"
    )

    balance_after_success = await get_balance_row(db_session, api_key["tenant_id"])
    assert balance_after_success is not None
    assert Decimal(balance_after_success[0]) == Decimal("-7.00"), (
        "balance must reflect the hold AND its settle (5.00 - 0.50 - 11.50 = -7.00) — "
        "the credit balance now genuinely tracks non-chat spend, never just a "
        "transient hold reservation"
    )

    # Force the hold past hold_timeout_s and run the M6 sweep — HEAL: confirm it is
    # now a NO-OP, since this hold already has a settle sibling (find_open_hold's
    # NOT EXISTS check makes it no longer "open") — the sweep must never touch an
    # already-finalized hold, regardless of its age.
    await db_session.execute(
        text(
            "UPDATE credit_ledger SET created_at = now() - interval '700 seconds'"
            " WHERE tenant_id = :t AND entry_type = 'hold'"
        ),
        {"t": api_key["tenant_id"]},
    )
    await db_session.commit()

    sweeper = CreditHoldRecoverySweeper(
        session_factory=app.state.sessionmaker,
        credit_guard=credit_guard,
        hold_timeout_s=600,
    )
    swept = await sweeper.sweep_once()
    assert swept == 0, "the hold already settled — the sweep must not release it a second time"

    balance_after_sweep = await get_balance_row(db_session, api_key["tenant_id"])
    assert balance_after_sweep is not None
    assert Decimal(balance_after_sweep[0]) == Decimal("-7.00"), (
        "the sweep must leave an already-settled hold's balance untouched — it is no "
        "longer an orphan"
    )


# ---------------------------------------------------------------------------
# FINDING 4 probe (informational, expect PASS): _credit_hold_ctx ContextVar
# isolation across two DIFFERENT tenants' concurrent chat requests on one
# event loop — the BYOK-ContextVar attack class named in the dispatch. Proves
# hold A never settles against request B's cost.
# ---------------------------------------------------------------------------


async def test_verify_contextvar_hold_never_crosses_concurrent_requests(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model_with_pricing: dict[str, Any],
    credit_guard: Any,
) -> None:
    """Two DIFFERENT tenants fire /v1/chat/completions CONCURRENTLY on the same
    event loop. Tenant CHEAP's response reports 1 completion token (~$0.01
    cost); tenant EXPENSIVE's reports 1000 (~$10 cost). If _credit_hold_ctx
    ever leaked across the two concurrent request Tasks, one tenant's hold
    could settle against the OTHER tenant's cost. Assert each tenant's settle
    amount matches ONLY its own usage."""
    model_id = active_model_with_pricing["model_id"]

    async def _signup(name: str, email: str) -> dict[str, str]:
        signup = await client.post(
            "/admin/auth/signup",
            json={"tenant_name": name, "email": email, "password": "correct horse battery"},
        )
        assert signup.status_code == 201, signup.text
        token = (
            await client.post(
                "/admin/auth/login", json={"email": email, "password": "correct horse battery"}
            )
        ).json()["access_token"]
        created = await client.post(
            "/admin/keys", json={"name": "ci"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert created.status_code == 201, created.text
        return {"key": created.json()["key"], "tenant_id": signup.json()["tenant_id"]}

    cheap = await _signup("CheapCo", "cheap@ctxvar-verify.io")
    expensive = await _signup("ExpensiveCo", "expensive@ctxvar-verify.io")
    await seed_balance(db_session, cheap["tenant_id"], balance_usd="5.00", grace_usd="0")
    await seed_balance(db_session, expensive["tenant_id"], balance_usd="50.00", grace_usd="0")

    class _MarkerUpstream:
        async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            marker = payload["messages"][0]["content"]
            completion_tokens = 1 if marker == "cheap" else 1000
            return 200, {
                "id": f"gen-{marker}",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": completion_tokens,
                    "total_tokens": completion_tokens + 1,
                },
            }

    app.state.completion_upstream = _MarkerUpstream()

    await asyncio.gather(
        client.post(
            "/v1/chat/completions",
            json={"model": model_id, "messages": [{"role": "user", "content": "cheap"}]},
            headers=auth_key(cheap["key"]),
        ),
        client.post(
            "/v1/chat/completions",
            json={"model": model_id, "messages": [{"role": "user", "content": "expensive"}]},
            headers=auth_key(expensive["key"]),
        ),
    )

    async def _both_settled() -> tuple[Any, Any] | None:
        c = (
            await db_session.execute(
                text(
                    "SELECT amount_usd FROM credit_ledger WHERE tenant_id = :t"
                    " AND entry_type = 'settle'"
                ),
                {"t": cheap["tenant_id"]},
            )
        ).fetchone()
        e = (
            await db_session.execute(
                text(
                    "SELECT amount_usd FROM credit_ledger WHERE tenant_id = :t"
                    " AND entry_type = 'settle'"
                ),
                {"t": expensive["tenant_id"]},
            )
        ).fetchone()
        return (c, e) if c is not None and e is not None else None

    cheap_settle, expensive_settle = await poll_until(_both_settled)

    # cheap: 1 prompt + 1 completion token @ $0.01/tok = $0.02 base cost, tenant
    # markup_pct defaults 20% -> $0.024 billed; hold=$0.50 -> settle = +0.476.
    # expensive: 1 + 1000 tokens @ $0.01/tok = $10.01 base -> $12.012 billed;
    # hold=$0.50 -> settle = 0.50 - 12.012 = -11.512 (extra debit).
    # If the ContextVar ever crossed, these would be swapped or blended.
    assert Decimal(str(cheap_settle[0])) == Decimal("0.47600000"), (
        f"cheap tenant's settle must reflect ONLY its own ~$0.024 cost, got {cheap_settle[0]}"
    )
    assert Decimal(str(expensive_settle[0])) == Decimal("-11.51200000"), (
        f"expensive tenant's settle must reflect ONLY its own ~$12.012 cost, got {expensive_settle[0]}"
    )
