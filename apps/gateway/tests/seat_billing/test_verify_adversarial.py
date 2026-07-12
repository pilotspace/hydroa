"""Independent adversarial VERIFY probes for seat-billing (TASK.md §3 — FROZEN @ v2).

Written by an independent verify agent (NOT the builder) to refute the green — these
probes exercise atomicity-under-failure, calendar edge math, determinism, backfill
correctness on dirtier fixtures, concurrency, and cross-tenant isolation that the
builder's own 38 tests either only partially cover or do not cover at all.

DO NOT weaken or delete any existing test to make these pass; these are ADDITIVE probes
only, run against the real seat-billing implementation. Findings from these probes are
recorded in TASK.md §6, not in this file.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.billing.application.seat_pricer import MembershipEvent, active_days
from gateway.tenants.domain.entities import Role
from tests.invoice_generation.conftest import seed_usage_record

from .conftest import (
    JULY_START,
    assign_plan,
    bearer,
    create_scim_token,
    get_invoice_detail,
    lines_of_type,
    make_generator,
    membership_events_for_user,
    mint_role_token,
    scim_bearer,
    seat_evidence_url,
    seed_event,
    seed_plan_with_seat_price,
    seed_user,
    signup_owner,
    signup_tenant,
)


async def _generate(app: Any, tenant_id: str, period_start: Any = JULY_START) -> str | None:
    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tenant_id), period_start)
    return str(invoice_id) if invoice_id is not None else None


def _ev(event_type: str, occurred_at: datetime.datetime) -> MembershipEvent:
    return MembershipEvent(event_type=event_type, occurred_at=occurred_at)


# ===========================================================================
# A. ATOMICITY — inject a failure AFTER the users-row mutation, BEFORE the
#    shared commit, at each of the 5 write sites. Neither row may survive.
# ===========================================================================


@pytest.fixture
def boom_seat_event_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make constructing a SeatMembershipEventRow raise — this happens INSIDE
    `session.add(SeatMembershipEventRow(...))`'s argument evaluation, i.e. strictly
    AFTER the users-row has already been added/flushed at every one of the 5 write
    sites, and strictly BEFORE the shared `commit()`/`begin()` block exits. A clean
    RuntimeError (not IntegrityError) so it is never accidentally swallowed by any
    `except IntegrityError` clause along the way (e.g. join_verified_tenant_domain's
    disclosed broad catch, §5 Known-problem fixes)."""
    from gateway.tenants.infrastructure import orm as tenants_orm

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("SEAT_LEDGER_INJECTED_FAILURE (verify-phase atomicity probe)")

    monkeypatch.setattr(tenants_orm.SeatMembershipEventRow, "__init__", _boom)


async def test_atomicity_invite_accept_orphans_nothing_on_ledger_failure(
    client: Any, db_session: AsyncSession, boom_seat_event_row: None
) -> None:
    owner = await signup_owner(client, email="owner@atom-invite.example")
    invite_resp = await client.post(
        "/admin/invites",
        json={"email": "newmember@atom-invite.example", "role": "member"},
        headers=bearer(owner["owner_token"]),
    )
    assert invite_resp.status_code == 201, invite_resp.text
    token = invite_resp.json()["token"]

    # httpx's ASGITransport re-raises an unhandled server exception rather than
    # returning it as a response object (Starlette's ServerErrorMiddleware sends the
    # 500 THEN re-raises) — the injected RuntimeError is the observable "it failed"
    # signal here; the DECISIVE assertions are the DB state checks below.
    with pytest.raises(RuntimeError, match="SEAT_LEDGER_INJECTED_FAILURE"):
        await client.post(
            f"/invites/{token}/accept", json={"password": "brand-new-horse-battery-02"}
        )

    # Neither the new users row NOR any ledger row may have survived.
    user_row = (
        await db_session.execute(
            text("SELECT id FROM users WHERE email = 'newmember@atom-invite.example'")
        )
    ).scalar_one_or_none()
    assert user_row is None, "orphaned users row survived a ledger-write failure (atomicity broken)"

    invite_status = (
        await db_session.execute(
            text("SELECT status FROM invites WHERE email = 'newmember@atom-invite.example'")
        )
    ).scalar_one()
    assert invite_status == "pending", (
        "invite must remain unflipped if the accept transaction rolled back"
    )


async def test_atomicity_scim_create_user_orphans_nothing_on_ledger_failure(
    client: Any, db_session: AsyncSession, boom_seat_event_row: None
) -> None:
    owner = await signup_owner(client, email="owner@atom-scim.example")
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])

    with pytest.raises(RuntimeError, match="SEAT_LEDGER_INJECTED_FAILURE"):
        await client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "atomscim@atom-scim.example",
                "active": True,
            },
            headers=scim_bearer(scim_token),
        )

    user_row = (
        await db_session.execute(
            text("SELECT id FROM users WHERE email = 'atomscim@atom-scim.example'")
        )
    ).scalar_one_or_none()
    assert user_row is None, "orphaned SCIM users row survived a ledger-write failure"


async def test_atomicity_scim_set_active_leaves_state_unflipped_on_ledger_failure(
    client: Any, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create the user FIRST (ledger write succeeds), THEN inject the failure only for
    the set_active flip — proves the deactivation flip + ledger append are atomic too,
    not just user-creation + ledger append."""
    owner = await signup_owner(client, email="owner@atom-flip.example")
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])
    create = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "flipvictim@atom-flip.example",
        },
        headers=scim_bearer(scim_token),
    )
    assert create.status_code == 201, create.text
    user_id = create.json()["id"]

    from gateway.tenants.infrastructure import orm as tenants_orm

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("SEAT_LEDGER_INJECTED_FAILURE (verify-phase atomicity probe)")

    monkeypatch.setattr(tenants_orm.SeatMembershipEventRow, "__init__", _boom)

    with pytest.raises(RuntimeError, match="SEAT_LEDGER_INJECTED_FAILURE"):
        await client.patch(
            f"/scim/v2/Users/{user_id}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "active", "value": False}],
            },
            headers=scim_bearer(scim_token),
        )

    deactivated_at = (
        await db_session.execute(
            text("SELECT deactivated_at FROM users WHERE id = :uid"), {"uid": user_id}
        )
    ).scalar_one()
    assert deactivated_at is None, (
        "user was left deactivated even though the ledger-append failed and the "
        "transaction should have rolled back both writes together"
    )
    events = await membership_events_for_user(db_session, user_id=user_id)
    assert len(events) == 1 and events[0]["event_type"] == "joined", (
        "no 'deactivated' event may have landed if the flip itself rolled back"
    )


async def test_atomicity_sso_new_user_orphans_nothing_on_ledger_failure(
    app: Any, client: Any, db_session: AsyncSession, boom_seat_event_row: None
) -> None:
    from .conftest import build_oidc_app, oidc_callback

    owner = await signup_owner(client, email="owner@atom-sso.example")
    engine = app.state.engine
    _oidc_app, oidc_client = build_oidc_app(
        engine=engine,
        tenant_id=owner["tenant_id"],
        domain="atom-sso.example",
        id_token_email="atomsso@atom-sso.example",
    )
    async with oidc_client:
        with pytest.raises(RuntimeError, match="SEAT_LEDGER_INJECTED_FAILURE"):
            await oidc_callback(oidc_client)

    user_row = (
        await db_session.execute(
            text("SELECT id FROM users WHERE email = 'atomsso@atom-sso.example'")
        )
    ).scalar_one_or_none()
    assert user_row is None, "orphaned SSO-provisioned users row survived a ledger-write failure"


async def test_atomicity_domain_capture_orphans_nothing_on_ledger_failure(
    client: Any, db_session: AsyncSession, boom_seat_event_row: None
) -> None:
    from .conftest import FakeDnsResolverForSeatBilling, claim_and_verify_domain

    owner = await signup_owner(client, email="owner@atom-domain.example")

    # app fixture not directly available here; reuse the client's bound app via state.
    fake_dns = FakeDnsResolverForSeatBilling()
    client._transport.app.state.dns_resolver = fake_dns  # type: ignore[attr-defined]
    await claim_and_verify_domain(
        client, owner_token=owner["owner_token"], domain="atom-domain.example", fake_dns=fake_dns
    )

    with pytest.raises(RuntimeError, match="SEAT_LEDGER_INJECTED_FAILURE"):
        await client.post(
            "/admin/auth/signup",
            json={
                "tenant_name": "ignored on the auto-join branch",
                "email": "atomjoiner@atom-domain.example",
                "password": "atom-joiner-horse-battery-01",
            },
        )

    user_row = (
        await db_session.execute(
            text("SELECT id FROM users WHERE email = 'atomjoiner@atom-domain.example'")
        )
    ).scalar_one_or_none()
    assert user_row is None, "orphaned domain-capture users row survived a ledger-write failure"


# ===========================================================================
# B. CALENDAR / MONTH-BOUNDARY EDGES (pure math, hand-computed Decimal)
# ===========================================================================


def test_join_on_last_day_of_31_day_month_prorates_one_day() -> None:
    period_start = datetime.datetime(2026, 7, 1)
    period_end = datetime.datetime(2026, 8, 1)
    events = (_ev("joined", datetime.datetime(2026, 7, 31, 23, 0, 0)),)
    assert active_days(events, period_start, period_end) == 1, "only July 31 itself"


def test_february_28_day_month_full_period() -> None:
    period_start = datetime.datetime(2026, 2, 1)
    period_end = datetime.datetime(2026, 3, 1)  # 2026 is not a leap year: Feb has 28 days
    events = (_ev("joined", datetime.datetime(2026, 1, 1)),)
    assert (period_end - period_start).days == 28
    assert active_days(events, period_start, period_end) == 28


def test_february_29_day_leap_month_full_period() -> None:
    period_start = datetime.datetime(2028, 2, 1)
    period_end = datetime.datetime(2028, 3, 1)  # 2028 IS a leap year
    events = (_ev("joined", datetime.datetime(2028, 1, 1)),)
    assert (period_end - period_start).days == 29
    assert active_days(events, period_start, period_end) == 29


def test_join_and_leave_same_calendar_day_inside_period_counts_one_day() -> None:
    """Distinct from the frozen 'zero active days' scenario, which is a join+leave
    OUTSIDE the period entirely. A same-day join-then-leave INSIDE the period is a real,
    billable seat-day — not a $0 no-line case — and the M7 edge scenario text does not
    explicitly cover it."""
    period_start = datetime.datetime(2026, 7, 1)
    period_end = datetime.datetime(2026, 8, 1)
    events = (
        _ev("joined", datetime.datetime(2026, 7, 15, 8, 0, 0)),
        _ev("deactivated", datetime.datetime(2026, 7, 15, 20, 0, 0)),
    )
    assert active_days(events, period_start, period_end) == 1, (
        "a same-day join-then-leave INSIDE the period must bill for that one day, not zero"
    )


def test_deactivated_exactly_at_period_end_midnight_excludes_period_end_date() -> None:
    """M6's half-open interval doctrine: `deactivated` at exactly period_end (midnight)
    must NOT count period_end's own calendar date (the user was active zero instants on
    that date within [period_start, period_end))."""
    period_start = datetime.datetime(2026, 7, 1)
    period_end = datetime.datetime(2026, 8, 1)
    events = (
        _ev("joined", datetime.datetime(2026, 7, 1)),
        _ev("deactivated", datetime.datetime(2026, 8, 1, 0, 0, 0)),
    )
    assert active_days(events, period_start, period_end) == 31, "all of July, never Aug 1"


def test_joined_exactly_at_period_start_midnight_counts_period_start_date() -> None:
    period_start = datetime.datetime(2026, 7, 1)
    period_end = datetime.datetime(2026, 8, 1)
    events = (_ev("joined", datetime.datetime(2026, 7, 1, 0, 0, 0)),)
    assert active_days(events, period_start, period_end) == 31


# ===========================================================================
# C. DETERMINISM — regenerate the same period twice; an event appended AFTER
#    generation must never retroactively change an already-issued invoice.
# ===========================================================================


async def test_regenerating_same_period_is_idempotent_no_duplicate_lines(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name="Idempotent Seat Co", email="idem@seat.io"
    )
    plan_id = await seed_plan_with_seat_price(db_session, name="idem-plan", seat_price="17.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )

    first_id = await _generate(app, tid)
    assert first_id is not None
    second_id = await _generate(app, tid)
    assert second_id is None, "a re-run must be a silent no-op (ON CONFLICT DO NOTHING, M13)"

    lines = (
        (
            await db_session.execute(
                text(
                    "SELECT line_type, amount_usd FROM invoice_lines"
                    " WHERE invoice_id = :id ORDER BY line_type"
                ),
                {"id": first_id},
            )
        )
        .mappings()
        .all()
    )
    seat_lines = [line for line in lines if line["line_type"] == "seat"]
    assert len(seat_lines) == 1, "no duplicate seat line from the re-run"


async def test_event_appended_after_invoice_issued_never_mutates_that_invoice(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name="Post-Issue Immutable Co", email="pi@seat.io"
    )
    plan_id = await seed_plan_with_seat_price(
        db_session, name="post-issue-plan", seat_price="10.00"
    )
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )
    new_member = await seed_user(db_session, tenant_id=tid, created_at=JULY_START.replace(month=6))
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=new_member,
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )

    invoice_id = await _generate(app, tid)
    assert invoice_id is not None
    before = (
        (
            await db_session.execute(
                text(
                    "SELECT amount_usd, request_count FROM invoice_lines"
                    " WHERE invoice_id = :id AND line_type = 'seat'"
                ),
                {"id": invoice_id},
            )
        )
        .mappings()
        .one()
    )
    assert before["request_count"] == 2

    # A LATE-arriving deactivation for the new member, backdated to mid-July — if this
    # retroactively mutated the ALREADY-ISSUED invoice's total or seat line, that would
    # be a severe immutability violation (M14/M5 "money is immutable once issued").
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=new_member,
        event_type="deactivated",
        occurred_at=JULY_START.replace(day=10),
    )

    after = (
        (
            await db_session.execute(
                text(
                    "SELECT amount_usd, request_count FROM invoice_lines"
                    " WHERE invoice_id = :id AND line_type = 'seat'"
                ),
                {"id": invoice_id},
            )
        )
        .mappings()
        .one()
    )
    assert after == before, "an already-issued invoice's persisted line must never change"

    # But seat-evidence RE-RUNS the bucket predicate live (M11 doctrine) — confirm this
    # is a KNOWN, documented characteristic, not a silent surprise: the evidence for this
    # exact line NOW reflects the late-arriving event even though the persisted dollar
    # amount does not. This is the exact "evidence vs frozen total can drift" residue
    # named in the verify report, demonstrated concretely here.
    line_id = (
        await db_session.execute(
            text("SELECT id FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"),
            {"id": invoice_id},
        )
    ).scalar_one()
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="pi-sub@seat.io")
    resp = await client.get(seat_evidence_url(invoice_id, str(line_id)), headers=bearer(token))
    assert resp.status_code == 200, resp.text
    returned_user_ids = {item["user_id"] for item in resp.json()["items"]}
    assert new_member not in returned_user_ids, (
        "DRIFT CONFIRMED: seat-evidence for the FROZEN 'seat' line no longer includes "
        "new_member after their late-arriving deactivation, even though the persisted "
        "amount_usd/request_count still bills for them — evidence and the frozen total "
        "can silently diverge because evidence is a LIVE re-derivation, not a snapshot"
    )


# ===========================================================================
# D. BACKFILL ON A DIRTY FIXTURE — see tests/migrations/test_seat_billing_backfill_adversarial.py
#    (moved there: needs the `clean_migration_db` fixture, which is local to that
#    directory's own conftest.py and not visible cross-directory).
# ===========================================================================


# ===========================================================================
# E. CONCURRENCY — two concurrent joins at the same tenant; composition with
#    the plan-seat-cap admission lock.
# ===========================================================================


async def test_two_concurrent_scim_creates_each_land_exactly_one_event(
    client: Any, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@concurrent-scim.example")
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])

    async def _create(username: str) -> Any:
        return await client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": username,
                "active": True,
            },
            headers=scim_bearer(scim_token),
        )

    resp_a, resp_b = await asyncio.gather(
        _create("concurrent-a@concurrent-scim.example"),
        _create("concurrent-b@concurrent-scim.example"),
    )
    assert resp_a.status_code == 201, resp_a.text
    assert resp_b.status_code == 201, resp_b.text

    events_a = await membership_events_for_user(db_session, user_id=resp_a.json()["id"])
    events_b = await membership_events_for_user(db_session, user_id=resp_b.json()["id"])
    assert len(events_a) == 1, f"user A must have exactly 1 event, got {events_a}"
    assert len(events_b) == 1, f"user B must have exactly 1 event, got {events_b}"

    total = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM seat_membership_events se JOIN users u ON u.id = se.user_id"
                " WHERE u.tenant_id = :tid AND se.event_type = 'joined'"
                " AND u.email IN ('concurrent-a@concurrent-scim.example',"
                " 'concurrent-b@concurrent-scim.example')"
            ),
            {"tid": owner["tenant_id"]},
        )
    ).scalar_one()
    assert total == 2, "no lost update, no duplicate — exactly one event per concurrent join"


async def test_concurrent_joins_against_a_tight_seat_cap_admit_exactly_the_allowed_count(
    client: Any, db_session: AsyncSession
) -> None:
    """Composes with plan-seat-cap's assert_seat_available row lock: with a cap that
    allows exactly ONE more seat, two concurrent SCIM creates must never BOTH succeed —
    and the loser must leave behind NEITHER a users row NOR a ledger event (a rejected
    admission must be as atomic as a rejected ledger write, probe A's same doctrine)."""
    from tests.plan_seat_cap.conftest import set_tenant_seat_cap

    owner = await signup_owner(client, email="owner@concurrent-cap.example")
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])
    # owner is seat #1; cap=2 allows exactly ONE more admission.
    await set_tenant_seat_cap(db_session, tenant_id=owner["tenant_id"], seat_cap=2)

    async def _create(username: str) -> Any:
        return await client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": username,
                "active": True,
            },
            headers=scim_bearer(scim_token),
        )

    resp_a, resp_b = await asyncio.gather(
        _create("cap-race-a@concurrent-cap.example"),
        _create("cap-race-b@concurrent-cap.example"),
    )
    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [201, 403], f"expected exactly one admit + one cap-reject, got {statuses}"

    winner = resp_a if resp_a.status_code == 201 else resp_b
    loser_email = (
        "cap-race-b@concurrent-cap.example"
        if winner is resp_a
        else "cap-race-a@concurrent-cap.example"
    )

    events = await membership_events_for_user(db_session, user_id=winner.json()["id"])
    assert len(events) == 1

    loser_user = (
        await db_session.execute(text("SELECT id FROM users WHERE email = :e"), {"e": loser_email})
    ).scalar_one_or_none()
    assert loser_user is None, "the cap-rejected loser must leave no orphaned users row"

    total_events_for_tenant = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM seat_membership_events se JOIN users u ON u.id = se.user_id"
                " WHERE u.tenant_id = :tid"
            ),
            {"tid": owner["tenant_id"]},
        )
    ).scalar_one()
    # owner (signup, ledger-less by construction) contributes 0 ledger rows + exactly 1
    # for the admitted concurrent winner.
    assert total_events_for_tenant == 1


# ===========================================================================
# F. NIL-UUID SENTINEL / TENANT ISOLATION
# ===========================================================================


async def test_nil_uuid_sentinel_never_leaks_across_tenants_sharing_the_same_key_id(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """Two DIFFERENT tenants each generate an aggregate 'seat' line with the BYTE-
    IDENTICAL nil-UUID key_id (00000000-0000-0000-0000-000000000000, M9) — the
    seat-evidence predicate must be derived from (tenant_id, period, bucket), never from
    key_id value equality alone, or tenant B's seat-evidence could leak tenant A's users
    (or vice versa)."""
    _owner_a, tid_a = await signup_tenant(client, tenant_name="Nil Sentinel A", email="nsa@seat.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="Nil Sentinel B", email="nsb@seat.io")
    plan_id = await seed_plan_with_seat_price(
        db_session, name="nil-sentinel-plan", seat_price="9.00"
    )
    await assign_plan(db_session, tenant_id=tid_a, plan_id=plan_id)
    await assign_plan(db_session, tenant_id=tid_b, plan_id=plan_id)

    owner_a_id = (
        await db_session.execute(
            text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid_a}
        )
    ).scalar_one()
    owner_b_id = (
        await db_session.execute(
            text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid_b}
        )
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid_a,
        user_id=str(owner_a_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )
    await seed_event(
        db_session,
        tenant_id=tid_b,
        user_id=str(owner_b_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )

    invoice_a = await _generate(app, tid_a)
    invoice_b = await _generate(app, tid_b)
    assert invoice_a is not None and invoice_b is not None

    line_a = (
        (
            await db_session.execute(
                text(
                    "SELECT id, key_id FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"
                ),
                {"id": invoice_a},
            )
        )
        .mappings()
        .one()
    )
    line_b = (
        (
            await db_session.execute(
                text(
                    "SELECT id, key_id FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"
                ),
                {"id": invoice_b},
            )
        )
        .mappings()
        .one()
    )
    assert str(line_a["key_id"]) == str(line_b["key_id"]) == "00000000-0000-0000-0000-000000000000"

    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="nsa-sub@seat.io")
    resp = await client.get(
        seat_evidence_url(invoice_a, str(line_a["id"])), headers=bearer(token_a)
    )
    assert resp.status_code == 200, resp.text
    returned_user_ids = {item["user_id"] for item in resp.json()["items"]}
    assert returned_user_ids == {str(owner_a_id)}, (
        f"tenant A's seat-evidence must NEVER include tenant B's owner despite the "
        f"identical nil-UUID key_id, got {returned_user_ids}"
    )
    assert str(owner_b_id) not in returned_user_ids


async def test_usage_evidence_route_against_a_seat_line_is_honestly_empty_never_cross_data(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """M12's documented, ACCEPTED gap: the EXISTING /evidence route (usage_records-
    based) called against a 'seat' line is not hardened into a reject — confirm it stays
    an honest empty page and never accidentally returns unrelated usage_records rows
    that happen to share the nil-UUID key_id by coincidence."""
    from .conftest import usage_evidence_url

    _owner, tid = await signup_tenant(client, tenant_name="Cross Evidence Co", email="ce@seat.io")
    plan_id = await seed_plan_with_seat_price(
        db_session, name="cross-evidence-plan", seat_price="10.00"
    )
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    # A usage record whose key_id happens to equal the nil-UUID sentinel would be the
    # worst-case coincidental collision — seed one deliberately to stress the predicate.
    await seed_usage_record(
        db_session,
        tenant_id=tid,
        cost_usd="2.50",
        created_at=JULY_START,
        key_id="00000000-0000-0000-0000-000000000000",
    )
    owner_id = (
        await db_session.execute(text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid})
    ).scalar_one()
    await seed_event(
        db_session,
        tenant_id=tid,
        user_id=str(owner_id),
        event_type="joined",
        occurred_at=JULY_START.replace(month=6),
    )

    invoice_id = await _generate(app, tid)
    assert invoice_id is not None
    seat_line_id = (
        await db_session.execute(
            text("SELECT id FROM invoice_lines WHERE invoice_id = :id AND line_type = 'seat'"),
            {"id": invoice_id},
        )
    ).scalar_one()

    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ce-sub@seat.io")
    resp = await client.get(
        usage_evidence_url(invoice_id, str(seat_line_id)), headers=bearer(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == [], (
        "a coincidental key_id collision between a seat line and a REAL usage_records "
        "row must never leak that usage row into the seat line's (wrong-route) evidence"
    )


# ===========================================================================
# G. LEDGER APPEND-ONLY (static) + PRICE-RESOLUTION precedence gap
# ===========================================================================


def test_no_code_path_ever_updates_or_deletes_seat_membership_events() -> None:
    """Static confirmation (not DB-trigger-enforced, unlike invoice_lines/invoices/
    invoice_corrections which DO get a real production trigger in migration
    0b5527920450 — seat_membership_events gets none): every reference to
    SeatMembershipEventRow in src/ is either a construction (INSERT via session.add) or
    a read (SELECT column reference) — never an UPDATE/assignment to an existing row's
    attribute, never a delete() call."""
    import ast
    import pathlib

    src_root = pathlib.Path(__file__).parents[2] / "src" / "gateway"
    offending: list[str] = []
    for path in src_root.rglob("*.py"):
        text_content = path.read_text()
        if "SeatMembershipEventRow" not in text_content:
            continue
        if (
            "delete(SeatMembershipEventRow" in text_content
            or "delete(\n    SeatMembershipEventRow" in text_content
        ):
            offending.append(f"{path}: delete() call")
        tree = ast.parse(text_content, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        # crude heuristic: a bare `row.<field> = ...` where `row` came
                        # from a SeatMembershipEventRow query is not statically provable
                        # without type inference; this at least catches the common
                        # `event_row.<field> = ...` / `event.<field> = ...` naming idiom
                        # this codebase actually uses for its OWN mutable rows (e.g.
                        # `row.deactivated_at = ...` in scim/infrastructure/repository.py).
                        and target.attr in ("event_type", "occurred_at", "user_id", "tenant_id")
                        and target.value.id in ("event", "event_row", "seat_event", "ledger_row")
                    ):
                        offending.append(
                            f"{path}:{node.lineno}: possible mutation of {target.attr}"
                        )
    assert offending == [], f"found possible mutation(s) of an append-only ledger row: {offending}"


async def test_seat_price_has_no_tenant_level_override_despite_frozen_contract_prose(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """§3's DECIDED freeze note claims: 'Prices live in the plans catalog and are
    tenant-overridable via the shared rate-card resolver like every other price.' This
    probe demonstrates that claim is NOT implemented: `_load_seat_price` reads ONLY
    `plans.seat_price_usd_monthly` via a plain tenant-plan JOIN — `tenant_rate_card_entries`
    (the actual 'shared rate-card resolver' table, keyed on tenant_id+model_id for
    per-MODEL markup) has no seat-domain row shape and is never consulted here. Two
    tenants on the IDENTICAL plan get the IDENTICAL seat price; there is no override
    slot. This is a prose-vs-implementation discrepancy in the frozen contract, not a
    wrong-money bug (every tenant is still billed correctly per the plan's uniform
    price) — flagged for §6/§7, not a HARD-STOP."""
    _owner_a, tid_a = await signup_tenant(
        client, tenant_name="Price Override A", email="poa@seat.io"
    )
    _owner_b, tid_b = await signup_tenant(
        client, tenant_name="Price Override B", email="pob@seat.io"
    )
    plan_id = await seed_plan_with_seat_price(db_session, name="override-plan", seat_price="25.00")
    await assign_plan(db_session, tenant_id=tid_a, plan_id=plan_id)
    await assign_plan(db_session, tenant_id=tid_b, plan_id=plan_id)

    # If a "tenant-overridable... like every other price" mechanism existed, it would
    # be `tenant_rate_card_entries` (the ONLY tenant-level price-override table in this
    # codebase) — attempt the natural analogous override for tenant A and confirm it is
    # silently ignored by seat pricing (no seat-domain column/row shape consumes it).
    has_seat_override_column = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'tenant_rate_card_entries'"
                " AND column_name LIKE '%seat%'"
            )
        )
    ).scalar_one_or_none()
    assert has_seat_override_column is None, (
        "found a seat-domain column on tenant_rate_card_entries — if a tenant-override "
        "mechanism was added, this probe (and the finding it backs) is stale, re-verify"
    )

    for tid in (tid_a, tid_b):
        owner_id = (
            await db_session.execute(
                text("SELECT id FROM users WHERE tenant_id = :tid"), {"tid": tid}
            )
        ).scalar_one()
        await seed_event(
            db_session,
            tenant_id=tid,
            user_id=str(owner_id),
            event_type="joined",
            occurred_at=JULY_START.replace(month=6),
        )

    invoice_a = await _generate(app, tid_a)
    invoice_b = await _generate(app, tid_b)
    assert invoice_a is not None and invoice_b is not None
    detail_a = await get_invoice_detail(
        client,
        token=mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="poa-sub2@seat.io"),
        invoice_id=invoice_a,
    )
    detail_b = await get_invoice_detail(
        client,
        token=mint_role_token(app, tenant_id=tid_b, role=Role.OWNER, email="pob-sub2@seat.io"),
        invoice_id=invoice_b,
    )
    amount_a = Decimal(lines_of_type(detail_a, "seat")[0]["amount_usd"])
    amount_b = Decimal(lines_of_type(detail_b, "seat")[0]["amount_usd"])
    assert amount_a == amount_b == Decimal("25.00"), (
        "same plan -> IDENTICAL seat price for both tenants, confirming no tenant-level "
        "override is actually wired despite the frozen contract's DECIDED prose"
    )
