"""Self-serve checkout — behavior red suite (TASK.md §2 scenarios / §4 plan).

Asserts observable behavior (HTTP status + body shapes from §3, DB side effects), never
internals. RED before Build: the payments module + /admin/checkout/* routes do not exist.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest

from gateway.tenants.domain.entities import Role

from .conftest import (
    CREDIT_TOPUP,
    PLAN_UPGRADE,
    balance_of,
    bearer,
    find_audit,
    ledger_count,
    mint_role_token,
    poll_until,
    session_count,
    session_status,
    signup_owner,
    tenant_plan_id,
)


def _key() -> str:
    return f"idem-{uuid.uuid4()}"


def _billing_admin(app: Any, who: dict[str, str], email: str = "ba@co.io") -> dict[str, str]:
    return bearer(
        mint_role_token(app, tenant_id=who["tenant_id"], role=Role.BILLING_ADMIN, email=email)
    )


# ---------------------------------------------------------------------------
# Happy paths (M8, M9, M2)
# ---------------------------------------------------------------------------


async def test_owner_plan_upgrade_dev(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    """M8/M9 — OWNER upgrades starter->pro via the dev adapter; plan changes, no ledger row."""
    who = personal_starter_owner
    created = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["provider"] == "dev"
    assert body["status"] == "pending"
    preview = body["preview"]
    assert preview["intent"] == "plan_upgrade"
    assert preview["current_base_usd"] == "1.00"
    assert preview["target_base_usd"] == "20.00"
    assert preview["currency"] == "USD"
    assert preview["effective"] == "immediate"

    session_id = body["session_id"]
    confirmed = await client.post(
        f"/admin/checkout/{session_id}/confirm", json={}, headers=bearer(who["jwt"])
    )
    assert confirmed.status_code == 200, confirmed.text
    cbody = confirmed.json()
    assert cbody["status"] == "succeeded"
    assert cbody["applied"]["new_plan_id"] == catalog["pro"]

    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["pro"]
    assert await ledger_count(db_session, who["tenant_id"]) == 0


async def test_billing_admin_credit_topup(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    """M2/M8 — BILLING_ADMIN tops up 25.00 (balance 5 -> 30) through topup_service."""
    who = personal_starter_owner
    # Seed starting balance 5.00 via the superadmin topup? Simpler: assert delta from 0.
    hdr = _billing_admin(app, who)
    created = await client.post(
        CREDIT_TOPUP,
        json={"amount_usd": "25.00", "idempotency_key": _key()},
        headers=hdr,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["provider"] == "dev"
    preview = body["preview"]
    assert preview["intent"] == "credit_topup"
    assert preview["amount_usd"] == "25.00"
    assert preview["currency"] == "USD"

    session_id = body["session_id"]
    confirmed = await client.post(f"/admin/checkout/{session_id}/confirm", json={}, headers=hdr)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "succeeded"

    assert await ledger_count(db_session, who["tenant_id"]) == 1
    assert await balance_of(db_session, who["tenant_id"]) == Decimal("25.00000000")


# ---------------------------------------------------------------------------
# M1 — tenant scope from session, never body
# ---------------------------------------------------------------------------


async def test_tenant_scope_from_session_not_body(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    other_tenant = str(uuid.uuid4())
    created = await client.post(
        PLAN_UPGRADE,
        json={
            "target_plan_id": catalog["pro"],
            "idempotency_key": _key(),
            "tenant_id": other_tenant,  # attacker-supplied — must be ignored
        },
        headers=bearer(who["jwt"]),
    )
    assert created.status_code == 200, created.text
    # The created session belongs to tenant A (the caller), not the body's tenant.
    assert await session_count(db_session, who["tenant_id"]) == 1
    assert await session_count(db_session, other_tenant) == 0


# ---------------------------------------------------------------------------
# M2 — role floor (owner/billing_admin). member + admin refused.
# ---------------------------------------------------------------------------


async def test_member_forbidden(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    hdr = bearer(
        mint_role_token(app, tenant_id=who["tenant_id"], role=Role.MEMBER, email="m@co.io")
    )
    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=hdr,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "ERR_AUTH_FORBIDDEN"
    assert await session_count(db_session, who["tenant_id"]) == 0


async def test_admin_forbidden(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    """M2 — ADMIN holds BUDGETS_MANAGE but NOT BILLING_MANAGE -> refused."""
    who = personal_starter_owner
    hdr = bearer(mint_role_token(app, tenant_id=who["tenant_id"], role=Role.ADMIN, email="a@co.io"))
    resp = await client.post(
        CREDIT_TOPUP,
        json={"amount_usd": "10.00", "idempotency_key": _key()},
        headers=hdr,
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "ERR_AUTH_FORBIDDEN"
    assert await ledger_count(db_session, who["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# M3 — double-confirm never double-applies
# ---------------------------------------------------------------------------


async def test_double_confirm_idempotent(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    hdr = _billing_admin(app, who)
    created = await client.post(
        CREDIT_TOPUP,
        json={"amount_usd": "25.00", "idempotency_key": _key()},
        headers=hdr,
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    r1, r2 = await asyncio.gather(
        client.post(f"/admin/checkout/{session_id}/confirm", json={}, headers=hdr),
        client.post(f"/admin/checkout/{session_id}/confirm", json={}, headers=hdr),
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["status"] == "succeeded"
    assert r2.json()["status"] == "succeeded"
    assert r1.json()["session_id"] == r2.json()["session_id"] == session_id

    assert await ledger_count(db_session, who["tenant_id"]) == 1
    assert await balance_of(db_session, who["tenant_id"]) == Decimal("25.00000000")


# ---------------------------------------------------------------------------
# M4 — repeated create is idempotent
# ---------------------------------------------------------------------------


async def test_repeated_create_idempotent(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    key = _key()
    a = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": key},
        headers=bearer(who["jwt"]),
    )
    b = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": key},
        headers=bearer(who["jwt"]),
    )
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert a.json()["session_id"] == b.json()["session_id"]
    assert await session_count(db_session, who["tenant_id"]) == 1


# ---------------------------------------------------------------------------
# M1 — cross-tenant / unknown session -> 404 checkout_not_found (no oracle)
# ---------------------------------------------------------------------------


async def test_cross_tenant_confirm_not_found(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    created = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    other = await signup_owner(client, tenant_name="OtherCo", email="owner@otherco.io")
    resp = await client.post(
        f"/admin/checkout/{session_id}/confirm", json={}, headers=bearer(other["jwt"])
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "checkout_not_found"
    # tenant A's session stays pending + unapplied.
    assert await session_status(db_session, session_id) == "pending"
    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["starter"]


async def test_unknown_session_not_found(
    client: httpx.AsyncClient,
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    missing = str(uuid.uuid4())
    resp = await client.post(
        f"/admin/checkout/{missing}/confirm", json={}, headers=bearer(who["jwt"])
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "checkout_not_found"

    get_resp = await client.get(f"/admin/checkout/{missing}", headers=bearer(who["jwt"]))
    assert get_resp.status_code == 404
    assert get_resp.json()["error"] == "checkout_not_found"


# ---------------------------------------------------------------------------
# R: checkout_not_confirmable
# ---------------------------------------------------------------------------


async def test_reconfirm_failed_session(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    created = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    # Force the session to a terminal non-pending, non-succeeded state.
    from sqlalchemy import text

    await db_session.execute(
        text("UPDATE checkout_sessions SET status = 'failed' WHERE id = :i"),
        {"i": session_id},
    )
    await db_session.commit()

    resp = await client.post(
        f"/admin/checkout/{session_id}/confirm", json={}, headers=bearer(who["jwt"])
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "checkout_not_confirmable"
    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["starter"]


# ---------------------------------------------------------------------------
# Plan rejects (R: plan_*)
# ---------------------------------------------------------------------------


async def test_enterprise_not_self_serve(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    business_team_owner: dict[str, str],
) -> None:
    who = business_team_owner
    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["enterprise"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "plan_not_self_serve"
    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["team"]


async def test_cross_audience(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    """Personal tenant cannot self-upgrade onto a business plan (team)."""
    who = personal_starter_owner
    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["team"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "plan_account_type_mismatch"
    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["starter"]


async def test_downgrade(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_pro_owner: dict[str, str],
) -> None:
    """pro (20) -> starter (1) is a downgrade — rejected."""
    who = personal_pro_owner
    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["starter"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "plan_downgrade_not_self_serve"
    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["pro"]


async def test_plan_unchanged(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_pro_owner: dict[str, str],
) -> None:
    who = personal_pro_owner
    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "plan_unchanged"


async def test_plan_not_found(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": str(uuid.uuid4()), "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "plan_not_found"
    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["starter"]


# ---------------------------------------------------------------------------
# Amount rejects (R: amount_*)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "NaN", "Infinity"])
async def test_amount_invalid(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
    bad: str,
) -> None:
    who = personal_starter_owner
    resp = await client.post(
        CREDIT_TOPUP,
        json={"amount_usd": bad, "idempotency_key": _key()},
        headers=_billing_admin(app, who),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "amount_invalid"
    assert await ledger_count(db_session, who["tenant_id"]) == 0


async def test_amount_exceeds_max(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    """Default ceiling is $10,000/topup (A7) — above it fires amount_exceeds_max."""
    who = personal_starter_owner
    resp = await client.post(
        CREDIT_TOPUP,
        json={"amount_usd": "10000.01", "idempotency_key": _key()},
        headers=_billing_admin(app, who),
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "amount_exceeds_max"
    assert await ledger_count(db_session, who["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Idempotency-key rejects (R: idempotency_key_*)
# ---------------------------------------------------------------------------


async def test_idempotency_key_required(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    resp = await client.post(
        CREDIT_TOPUP,
        json={"amount_usd": "10.00"},  # no idempotency_key
        headers=_billing_admin(app, who),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "idempotency_key_required"
    assert await session_count(db_session, who["tenant_id"]) == 0


async def test_idempotency_key_conflict(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    hdr = _billing_admin(app, who)
    key = _key()
    first = await client.post(
        CREDIT_TOPUP, json={"amount_usd": "25.00", "idempotency_key": key}, headers=hdr
    )
    assert first.status_code == 200, first.text
    # reuse the key with a DIFFERENT amount.
    second = await client.post(
        CREDIT_TOPUP, json={"amount_usd": "50.00", "idempotency_key": key}, headers=hdr
    )
    assert second.status_code == 409
    assert second.json()["error"] == "idempotency_key_conflict"
    assert await session_count(db_session, who["tenant_id"]) == 1


# ---------------------------------------------------------------------------
# M11 — kill switch
# ---------------------------------------------------------------------------


async def test_kill_switch_disabled(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    app.state.settings.payment_checkout_enabled = False
    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "checkout_disabled"
    assert await session_count(db_session, who["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# M12 — stripe unavailable degrades to 503, no partial mutation
# ---------------------------------------------------------------------------


async def test_stripe_unavailable_degrades(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    # Install a stripe provider whose circuit breaker is already OPEN.
    from gateway.payments.infrastructure.stripe_provider import StripePaymentProvider
    from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()  # trip immediately
    provider = StripePaymentProvider(
        api_key="sk_test_x",
        base_url="https://api.stripe.com",
        breaker=breaker,
    )
    app.state.payment_provider = provider

    resp = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"] == "payment_provider_unavailable"
    # no partial mutation
    assert await tenant_plan_id(db_session, who["tenant_id"]) == catalog["starter"]
    assert await session_count(db_session, who["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# M5 / M7 — dev-adapter mutation marked in audit; audit failure never fails checkout
# ---------------------------------------------------------------------------


async def test_dev_provider_marked_in_audit(
    client: httpx.AsyncClient,
    db_session: Any,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    created = await client.post(
        PLAN_UPGRADE,
        json={"target_plan_id": catalog["pro"], "idempotency_key": _key()},
        headers=bearer(who["jwt"]),
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    confirmed = await client.post(
        f"/admin/checkout/{session_id}/confirm", json={}, headers=bearer(who["jwt"])
    )
    assert confirmed.status_code == 200, confirmed.text

    async def _audit() -> dict[str, Any] | None:
        return await find_audit(db_session, "checkout.plan_upgrade")

    row = await poll_until(_audit)
    assert row["tenant_id"] == who["tenant_id"]
    assert row["actor_email"] == "owner@personalco.io"
    assert row["actor_user_id"] is not None
    assert row["metadata"]["provider"] == "dev"


async def test_audit_failure_does_not_fail_checkout(
    client: httpx.AsyncClient,
    app: Any,
    db_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    catalog: dict[str, str],
    personal_starter_owner: dict[str, str],
) -> None:
    who = personal_starter_owner
    hdr = _billing_admin(app, who)

    import gateway.payments.api.router as checkout_router

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("audit writer exploded")

    monkeypatch.setattr(checkout_router, "record_audit", _boom)

    created = await client.post(
        CREDIT_TOPUP,
        json={"amount_usd": "25.00", "idempotency_key": _key()},
        headers=hdr,
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    confirmed = await client.post(f"/admin/checkout/{session_id}/confirm", json={}, headers=hdr)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "succeeded"
    # the mutation still landed despite the audit blowing up.
    assert await balance_of(db_session, who["tenant_id"]) == Decimal("25.00000000")
    assert await ledger_count(db_session, who["tenant_id"]) == 1
