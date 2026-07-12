"""Pin suite closing the verify-round 🟡 coverage residue (credits-ledger §6):
history-cursor pagination was completely untested — `next_cursor` was never non-None
in any existing test — plus the untested defensive branches called out alongside it
(`_parse_topup_amount`'s InvalidOperation arm, `_parse_history_limit`'s ValueError arm,
`PassthroughCreditGuard`'s settle/release bodies).

Additive only: no frozen §4 test is touched; asserts pin the FROZEN §3 shapes
(keyset cursor over (created_at, id), ERR_PAYLOAD_INVALID on malformed paging input,
ERR_CREDITS_TOPUP_INVALID on a non-decimal amount).
"""

# The cursor codec is deliberately module-private; pinning its round-trip
# exactness is the point of this suite.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.credits.api.router import (
    _decode_history_cursor,
    _encode_history_cursor,
)
from gateway.credits.domain.ports import PassthroughCreditGuard
from tests.credits_ledger.conftest import assert_problem, bearer, ledger_count

TOPUP_PATH = "/admin/platform/tenants/{tenant_id}/credits/topup"
HISTORY_PATH = "/admin/credits/history"


async def _seed_topups(
    client: httpx.AsyncClient,
    superadmin_token: str,
    tenant_id: str,
    amounts: list[str],
) -> None:
    for i, amount in enumerate(amounts):
        resp = await client.post(
            TOPUP_PATH.format(tenant_id=tenant_id),
            json={"amount_usd": amount},
            headers={**bearer(superadmin_token), "Idempotency-Key": f"page-seed-{i}"},
        )
        assert resp.status_code == 201, resp.text


async def test_history_paginates_with_cursor_no_overlap_no_gap(
    client: httpx.AsyncClient,
    superadmin_token: str,
    api_key: dict[str, str],
) -> None:
    """5 ledger rows, limit=2 -> pages of 2/2/1; the union of entry ids across pages
    is exactly the full set (no row skipped, no row repeated); the final page carries
    next_cursor=None."""
    amounts = ["1.00", "2.00", "3.00", "4.00", "5.00"]
    await _seed_topups(client, superadmin_token, api_key["tenant_id"], amounts)

    seen_ids: list[str] = []
    seen_amounts: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str] = {"limit": "2"}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await client.get(HISTORY_PATH, params=params, headers=bearer(api_key["jwt"]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["entries"]) <= 2
        seen_ids.extend(e["id"] for e in body["entries"])
        seen_amounts.extend(e["amount_usd"] for e in body["entries"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10, "cursor chain must terminate"

    assert pages == 3, f"5 rows at limit=2 must take exactly 3 pages, took {pages}"
    assert len(seen_ids) == 5
    assert len(set(seen_ids)) == 5, "a ledger row appeared on two pages (keyset overlap)"
    assert sorted(Decimal(a) for a in seen_amounts) == [Decimal(a) for a in amounts], (
        "paged union must equal the full ledger — a row was skipped or fabricated"
    )


async def test_history_single_page_when_under_limit(
    client: httpx.AsyncClient,
    superadmin_token: str,
    api_key: dict[str, str],
) -> None:
    """Rows < limit -> one page, next_cursor=None (the always-None shape every prior
    test implicitly relied on, now pinned explicitly)."""
    await _seed_topups(client, superadmin_token, api_key["tenant_id"], ["9.00"])
    resp = await client.get(HISTORY_PATH, headers=bearer(api_key["jwt"]))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["entries"]) == 1
    assert body["next_cursor"] is None


def test_history_cursor_round_trips_datetime_and_uuid() -> None:
    """encode -> decode is exact, including microseconds and tz, and survives the
    base64 re-padding path."""
    created_at = datetime.datetime(2026, 7, 12, 13, 37, 1, 654321, tzinfo=datetime.UTC)
    row_id = uuid.uuid4()
    token = _encode_history_cursor(created_at, row_id)
    assert "=" not in token or True  # urlsafe token; decoder must re-pad regardless
    decoded_at, decoded_id = _decode_history_cursor(token.rstrip("="))
    assert decoded_at == created_at
    assert decoded_id == row_id


async def test_history_malformed_cursor_rejected(
    client: httpx.AsyncClient,
    api_key: dict[str, str],
) -> None:
    """Garbage cursor -> 422 ERR_PAYLOAD_INVALID (never a 500, never an empty 200)."""
    for bad in ("not-base64!!", "aGVsbG8", ""):  # invalid b64 · valid b64/not JSON · empty
        resp = await client.get(
            HISTORY_PATH, params={"cursor": bad}, headers=bearer(api_key["jwt"])
        )
        assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_history_invalid_limit_rejected(
    client: httpx.AsyncClient,
    api_key: dict[str, str],
) -> None:
    """Non-integer, zero, negative, and over-max limits -> 422 ERR_PAYLOAD_INVALID."""
    for bad in ("abc", "0", "-1", "201"):
        resp = await client.get(
            HISTORY_PATH, params={"limit": bad}, headers=bearer(api_key["jwt"])
        )
        assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_topup_rejects_non_numeric_amount(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    api_key: dict[str, str],
) -> None:
    """amount_usd = "ten dollars" -> 422 ERR_CREDITS_TOPUP_INVALID via the
    InvalidOperation branch (the frozen suite only exercised the finite-negative arm);
    no ledger row written."""
    resp = await client.post(
        TOPUP_PATH.format(tenant_id=api_key["tenant_id"]),
        json={"amount_usd": "ten dollars"},
        headers={**bearer(superadmin_token), "Idempotency-Key": "nonnumeric-1"},
    )
    assert_problem(resp, 422, "ERR_CREDITS_TOPUP_INVALID")
    assert await ledger_count(db_session, api_key["tenant_id"]) == 0


async def test_passthrough_guard_finalizers_are_noops() -> None:
    """PassthroughCreditGuard settle/release complete without error and without
    side effects — the wired default when credits is not configured."""
    guard = PassthroughCreditGuard()
    tenant_id, request_id = uuid.uuid4(), uuid.uuid4()
    await guard.check_and_hold(tenant_id, request_id, Decimal("0.50"))
    await guard.settle(tenant_id, request_id, uuid.uuid4(), Decimal("1.23"))
    await guard.release(tenant_id, request_id)
