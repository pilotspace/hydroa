"""seat_pricer — pure per-seat proration math (seat-billing TASK.md §3 — FROZEN @ v2).

Zero infra imports (mirrors resolve_entitlements's own pure-function/adapter split,
TASK.md §0 Honors) — every function here takes already-loaded, plain-dataclass inputs
and returns plain-dataclass outputs; the infra loader that reads `users` +
`seat_membership_events` rows into `UserMembership` lives in
`billing/infrastructure/invoice_repository.py` (M7's own "one extra SELECT" idiom).

CONTRACT (M6/M7/M9):
  - active_days(): replays a user's ordered event stream over [period_start, period_end)
    and counts DISTINCT UTC calendar dates the user was active on for at least one
    instant. `'joined'`/`'reactivated'` opens an active interval; `'deactivated'` closes
    it; an interval still open at period_end is clipped there. An orphan `'deactivated'`
    with no preceding open interval is ignored (never produces negative days).
  - compute_seat_lines(): for every seat with active_days == days_in_period, aggregates
    into ONE `'seat'` line (key_id = NIL_SEAT_KEY_ID, M9); for every seat with
    0 < active_days < days_in_period, ONE `'proration'` line (key_id = the seat's own
    user_id, M9, exactly 1:1); a seat with active_days == 0 produces NO line (M7 edge).
    Each returned SeatLineSpec also carries `contributing_user_ids` — the SAME users
    the amount was computed from — so the seat-evidence route (M11) can re-run this
    IDENTICAL bucket computation and resolve a line back to its membership events,
    never a materialized id list stored separately from the pricing math itself.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")

#: Aggregate `'seat'` line sentinel key_id (M9) — no single user fits an aggregate.
NIL_SEAT_KEY_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@dataclass(frozen=True, slots=True)
class MembershipEvent:
    """One replayable transition — mirrors a `seat_membership_events` row (or the M5
    fallback's implicit joined/deactivated pair)."""

    event_type: str  # "joined" | "deactivated" | "reactivated"
    occurred_at: datetime.datetime


@dataclass(frozen=True, slots=True)
class UserMembership:
    """One seat's full replayable history, plus the M5 fallback columns (used ONLY when
    `events` is empty — a data-integrity gap the backfill/write-sites should make
    impossible, but never silently trusted blind)."""

    user_id: uuid.UUID
    events: tuple[MembershipEvent, ...] = field(default_factory=tuple)
    fallback_created_at: datetime.datetime | None = None
    fallback_deactivated_at: datetime.datetime | None = None


@dataclass(frozen=True, slots=True)
class SeatLineSpec:
    """One invoice_lines row this task writes (M9) — 'seat' (aggregate) or 'proration'
    (per-seat)."""

    line_type: str  # "seat" | "proration"
    key_id: uuid.UUID
    amount_usd: Decimal
    raw_amount_usd: Decimal
    request_count: int
    contributing_user_ids: tuple[uuid.UUID, ...]


def _round_half_up(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _effective_events(user: UserMembership) -> tuple[MembershipEvent, ...]:
    """Resolve the ledger's own events, or the M5 current-state fallback pair — NEVER an
    empty stream for a user that genuinely exists (a seat is never silently dropped)."""
    if user.events:
        return user.events
    if user.fallback_created_at is None:
        return ()
    fallback = [MembershipEvent(event_type="joined", occurred_at=user.fallback_created_at)]
    if user.fallback_deactivated_at is not None:
        fallback.append(
            MembershipEvent(event_type="deactivated", occurred_at=user.fallback_deactivated_at)
        )
    return tuple(fallback)


def active_days(
    events: tuple[MembershipEvent, ...],
    period_start: datetime.datetime,
    period_end: datetime.datetime,
) -> int:
    """Distinct UTC calendar dates touched by the replayed event stream, clipped to
    [period_start, period_end) — the atomic "seat-day" unit (M6, Glossary)."""
    ordered = sorted(events, key=lambda e: e.occurred_at)
    intervals: list[tuple[datetime.datetime, datetime.datetime]] = []
    open_start: datetime.datetime | None = None
    for ev in ordered:
        if ev.event_type in ("joined", "reactivated"):
            if open_start is None:
                open_start = ev.occurred_at
            # A second open while already open is defensively ignored — the contract's
            # 5 write sites never emit two consecutive opens for one user.
        elif ev.event_type == "deactivated":
            if open_start is not None:
                intervals.append((open_start, ev.occurred_at))
                open_start = None
            # else: an orphan 'deactivated' with no preceding open interval — ignored,
            # never produces negative days (M6).
    if open_start is not None:
        # Still active at replay time — clip the open interval at period_end (M6).
        intervals.append((open_start, period_end))

    touched: set[datetime.date] = set()
    for start, end in intervals:
        clipped_start = max(start, period_start)
        clipped_end = min(end, period_end)
        if clipped_start >= clipped_end:
            continue
        # Half-open [clipped_start, clipped_end) — the last touched calendar date is the
        # date of the instant just before clipped_end, never clipped_end's own date
        # (which the seat was NOT active on, since the interval excludes it).
        last_date = (clipped_end - datetime.timedelta(microseconds=1)).date()
        current = clipped_start.date()
        while current <= last_date:
            touched.add(current)
            current += datetime.timedelta(days=1)
    return len(touched)


def compute_seat_lines(
    *,
    seat_price_usd_monthly: Decimal,
    users: list[UserMembership],
    period_start: datetime.datetime,
    period_end: datetime.datetime,
) -> list[SeatLineSpec]:
    """Returns 0, 1 ('seat' only), or 1+1..N ('seat' + 'proration'*) SeatLineSpecs
    (M7). `seat_price_usd_monthly` must already be resolved non-NULL/non-zero by the
    caller (M2's inert gate) — this function is never called for an inert tenant."""
    days_in_period = (period_end - period_start).days

    full_price_user_ids: list[uuid.UUID] = []
    proration_specs: list[SeatLineSpec] = []
    for user in users:
        events = _effective_events(user)
        days = active_days(events, period_start, period_end)
        if days == 0:
            continue
        if days == days_in_period:
            full_price_user_ids.append(user.user_id)
            continue
        raw = seat_price_usd_monthly * days / Decimal(days_in_period)
        proration_specs.append(
            SeatLineSpec(
                line_type="proration",
                key_id=user.user_id,
                amount_usd=_round_half_up(raw),
                raw_amount_usd=raw,
                request_count=1,
                contributing_user_ids=(user.user_id,),
            )
        )

    specs: list[SeatLineSpec] = []
    if full_price_user_ids:
        raw = seat_price_usd_monthly * len(full_price_user_ids)
        specs.append(
            SeatLineSpec(
                line_type="seat",
                key_id=NIL_SEAT_KEY_ID,
                amount_usd=_round_half_up(raw),
                raw_amount_usd=raw,
                request_count=len(full_price_user_ids),
                contributing_user_ids=tuple(full_price_user_ids),
            )
        )
    specs.extend(proration_specs)
    return specs


def resolve_line_contributors(
    *,
    users: list[UserMembership],
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    line_type: str,
    key_id: uuid.UUID,
) -> tuple[uuid.UUID, ...] | None:
    """Re-run the M6/M7 bucket CLASSIFICATION (never $ amounts — price-independent) for
    one already-persisted invoice line, returning the contributing user_id(s) (M11's own
    "re-runs the same bucket computation... never a materialized id list" doctrine).

    Returns None if no seat in the CURRENT replay matches — a data-integrity drift since
    generation time that should be structurally impossible (the ledger is append-only,
    generation and evidence read the identical predicate), but never silently trusted
    blind (mirrors M5's own defense-in-depth posture); the caller degrades to an honest
    empty evidence page rather than raising.
    """
    days_in_period = (period_end - period_start).days
    if line_type == "proration":
        for user in users:
            if user.user_id != key_id:
                continue
            days = active_days(_effective_events(user), period_start, period_end)
            if 0 < days < days_in_period:
                return (user.user_id,)
        return None
    if line_type == "seat":
        full_price = tuple(
            user.user_id
            for user in users
            if active_days(_effective_events(user), period_start, period_end) == days_in_period
        )
        return full_price or None
    return None
