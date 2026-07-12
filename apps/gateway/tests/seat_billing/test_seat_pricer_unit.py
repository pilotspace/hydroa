"""RED suite — seat_pricer pure math (seat-billing TASK.md §3 — FROZEN @ v2, M6/M7/M9).

Pure, DB-free unit tests over `gateway.billing.application.seat_pricer` — zero infra,
zero pytest-asyncio. RED before BUILD: the module does not exist yet, so every import
fails — the honest missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.

NOTE (build-time scenario-arithmetic correction, not a silent contract edit — mirrors
the precedent `invite_repository.py::InviteRepository.accept`'s own docstring already
sets for a TASK.md prose gap): §2's "Reactivation same month bills for actual days on
both sides of the gap" scenario states "active_days is 12 (July 1-4) + 12 (July 20-31) =
24" — but July 1-4 is 4 calendar days, not 12 (the label and the number contradict each
other), and 4 + 12 = 16, not 24. M6's own normative rule (replay + count distinct UTC
calendar dates touched, clipped to the period) is unambiguous and is what
`test_reactivation_same_month_bills_both_sides_of_gap` below asserts (16, correctly
derived from "July 1-4" = 4 real days + "July 20-31" = 12 real days). Flagged here as an
open question for Specify to fix in the scenario prose; NOT a change to any Must/Reject/
Schema in the frozen §3 CONTRACT itself.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from gateway.billing.application.seat_pricer import (
    NIL_SEAT_KEY_ID,
    MembershipEvent,
    UserMembership,
    active_days,
    compute_seat_lines,
    resolve_line_contributors,
)

JULY_START = datetime.datetime(2026, 7, 1)
AUGUST_START = datetime.datetime(2026, 8, 1)
JUNE_START = datetime.datetime(2026, 6, 1)


def _ev(event_type: str, occurred_at: datetime.datetime) -> MembershipEvent:
    return MembershipEvent(event_type=event_type, occurred_at=occurred_at)


# ---------------------------------------------------------------------------
# M6 — active_days replay
# ---------------------------------------------------------------------------


def test_full_period_seat_touches_every_calendar_day() -> None:
    events = (_ev("joined", JUNE_START),)
    assert active_days(events, JULY_START, AUGUST_START) == 31


def test_mid_month_join_prorates_by_calendar_days_touched() -> None:
    events = (_ev("joined", datetime.datetime(2026, 7, 15, 14, 0, 0)),)
    assert active_days(events, JULY_START, AUGUST_START) == 17, "July 15 through 31 inclusive"


def test_mid_month_leave_prorates_by_calendar_days_touched() -> None:
    events = (
        _ev("joined", JUNE_START),
        _ev("deactivated", datetime.datetime(2026, 7, 10, 9, 0, 0)),
    )
    assert active_days(events, JULY_START, AUGUST_START) == 10, "July 1 through 10 inclusive"


def test_reactivation_same_month_bills_both_sides_of_gap() -> None:
    events = (
        _ev("joined", JUNE_START),
        _ev("deactivated", datetime.datetime(2026, 7, 5, 0, 0, 0)),
        _ev("reactivated", datetime.datetime(2026, 7, 20, 0, 0, 0)),
    )
    # July 1-4 (4 days, deactivated exactly at July-5-midnight touches zero of July 5) +
    # July 20-31 (12 days, still active at period_end) = 16 — see the module docstring's
    # NOTE on the §2 scenario-prose arithmetic typo this corrects.
    assert active_days(events, JULY_START, AUGUST_START) == 16
    assert active_days(events, JULY_START, AUGUST_START) not in (31, 4, 12), (
        "neither side alone, nor the whole period, may stand in for the summed gap-aware total"
    )


def test_orphan_deactivated_with_no_open_interval_is_ignored_never_negative() -> None:
    events = (_ev("deactivated", datetime.datetime(2026, 7, 10)),)
    assert active_days(events, JULY_START, AUGUST_START) == 0


def test_seat_active_zero_days_entirely_outside_period() -> None:
    events = (
        _ev("joined", datetime.datetime(2026, 6, 10, 8, 0, 0)),
        _ev("deactivated", datetime.datetime(2026, 6, 10, 20, 0, 0)),
    )
    assert active_days(events, JULY_START, AUGUST_START) == 0


def test_second_open_while_already_open_is_defensively_ignored() -> None:
    events = (
        _ev("joined", JUNE_START),
        _ev("joined", datetime.datetime(2026, 6, 15)),  # defensive: two opens, no close between
    )
    assert active_days(events, JULY_START, AUGUST_START) == 31, "still clips once at period_end"


# ---------------------------------------------------------------------------
# M7/M9 — compute_seat_lines bucketing + line-shape
# ---------------------------------------------------------------------------


def test_full_period_seats_aggregate_mid_period_seat_gets_own_proration_line() -> None:
    full_ids = [uuid.uuid4() for _ in range(3)]
    partial_id = uuid.uuid4()
    users = [UserMembership(user_id=uid, events=(_ev("joined", JUNE_START),)) for uid in full_ids]
    users.append(
        UserMembership(
            user_id=partial_id,
            events=(
                _ev("joined", JULY_START),
                _ev("deactivated", datetime.datetime(2026, 7, 11)),  # 10 days
            ),
        )
    )

    specs = compute_seat_lines(
        seat_price_usd_monthly=Decimal("31.00"),
        users=users,
        period_start=JULY_START,
        period_end=AUGUST_START,
    )

    seat_specs = [s for s in specs if s.line_type == "seat"]
    proration_specs = [s for s in specs if s.line_type == "proration"]
    assert len(seat_specs) == 1
    assert seat_specs[0].request_count == 3
    assert seat_specs[0].key_id == NIL_SEAT_KEY_ID
    assert set(seat_specs[0].contributing_user_ids) == set(full_ids)
    assert seat_specs[0].raw_amount_usd == Decimal("93.00")  # 31.00 * 3
    assert seat_specs[0].amount_usd == Decimal("93.00")

    assert len(proration_specs) == 1
    assert proration_specs[0].key_id == partial_id
    assert proration_specs[0].contributing_user_ids == (partial_id,)
    assert proration_specs[0].request_count == 1
    expected_raw = Decimal("31.00") * 10 / Decimal(31)
    assert proration_specs[0].raw_amount_usd == expected_raw
    assert proration_specs[0].amount_usd == expected_raw.quantize(Decimal("0.01"))


def test_zero_active_days_seat_produces_no_line() -> None:
    zero_day_user = uuid.uuid4()
    users = [
        UserMembership(
            user_id=zero_day_user,
            events=(
                _ev("joined", datetime.datetime(2026, 6, 10, 8, 0, 0)),
                _ev("deactivated", datetime.datetime(2026, 6, 10, 20, 0, 0)),
            ),
        )
    ]
    specs = compute_seat_lines(
        seat_price_usd_monthly=Decimal("15.00"),
        users=users,
        period_start=JULY_START,
        period_end=AUGUST_START,
    )
    assert specs == []


def test_no_seats_at_all_produces_empty_specs() -> None:
    specs = compute_seat_lines(
        seat_price_usd_monthly=Decimal("15.00"),
        users=[],
        period_start=JULY_START,
        period_end=AUGUST_START,
    )
    assert specs == []


def test_ledger_less_user_falls_back_to_current_state_columns() -> None:
    """M5 — a user with an EMPTY events tuple is never silently dropped; it falls back to
    the implicit joined/deactivated pair from fallback_created_at/fallback_deactivated_at."""
    uid = uuid.uuid4()
    users = [
        UserMembership(
            user_id=uid,
            events=(),
            fallback_created_at=JUNE_START,
            fallback_deactivated_at=None,
        )
    ]
    specs = compute_seat_lines(
        seat_price_usd_monthly=Decimal("10.00"),
        users=users,
        period_start=JULY_START,
        period_end=AUGUST_START,
    )
    assert len(specs) == 1
    assert specs[0].line_type == "seat"
    assert specs[0].contributing_user_ids == (uid,)


def test_ledger_less_already_deactivated_user_falls_back_to_both_columns() -> None:
    """M5's other half — a ledger-less user that is ALSO already deactivated falls back to
    the IMPLICIT joined+deactivated pair (both fallback columns set), producing a
    'proration' line for the days it was active before deactivation, never a full seat and
    never silently dropped."""
    uid = uuid.uuid4()
    users = [
        UserMembership(
            user_id=uid,
            events=(),
            fallback_created_at=JULY_START,
            fallback_deactivated_at=datetime.datetime(2026, 7, 11),
        )
    ]
    specs = compute_seat_lines(
        seat_price_usd_monthly=Decimal("10.00"),
        users=users,
        period_start=JULY_START,
        period_end=AUGUST_START,
    )
    assert len(specs) == 1
    assert specs[0].line_type == "proration"
    assert specs[0].contributing_user_ids == (uid,)


# ---------------------------------------------------------------------------
# M11 — resolve_line_contributors (evidence bucket re-derivation)
# ---------------------------------------------------------------------------


def test_resolve_line_contributors_proration() -> None:
    partial_id = uuid.uuid4()
    users = [
        UserMembership(
            user_id=partial_id,
            events=(_ev("joined", datetime.datetime(2026, 7, 15, 14, 0, 0)),),
        )
    ]
    result = resolve_line_contributors(
        users=users,
        period_start=JULY_START,
        period_end=AUGUST_START,
        line_type="proration",
        key_id=partial_id,
    )
    assert result == (partial_id,)


def test_resolve_line_contributors_seat_aggregate() -> None:
    full_ids = [uuid.uuid4() for _ in range(2)]
    partial_id = uuid.uuid4()
    users = [UserMembership(user_id=uid, events=(_ev("joined", JUNE_START),)) for uid in full_ids]
    users.append(
        UserMembership(
            user_id=partial_id,
            events=(_ev("joined", datetime.datetime(2026, 7, 20)),),
        )
    )
    result = resolve_line_contributors(
        users=users,
        period_start=JULY_START,
        period_end=AUGUST_START,
        line_type="seat",
        key_id=NIL_SEAT_KEY_ID,
    )
    assert result is not None
    assert set(result) == set(full_ids)


def test_resolve_line_contributors_no_match_returns_none() -> None:
    result = resolve_line_contributors(
        users=[],
        period_start=JULY_START,
        period_end=AUGUST_START,
        line_type="proration",
        key_id=uuid.uuid4(),
    )
    assert result is None
