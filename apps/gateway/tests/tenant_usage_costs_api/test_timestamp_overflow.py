"""Regression suite — out-of-range Unix timestamps must yield the contracted 422, never a 500.

These pin R3 (start_time) and R8 (page cursor) against a refute finding: a validly-parsed but
astronomically large integer overflowed ``datetime.fromtimestamp`` uncaught, surfacing as a 500
instead of ``ERR_PAYLOAD_INVALID``. ADDITIVE — no frozen test is modified or weakened.
"""

from __future__ import annotations

from typing import Any

from .conftest import COMPLETIONS_PATH, DAY0, auth_header, unix

# One past the max representable instant (year 10000-01-01 00:00:00 UTC) → ValueError.
_START_TIME_BEYOND_YEAR_9999 = 253402300800
# base64("b:99999999999999999999") — a well-formed cursor whose int overflows time_t.
_CURSOR_OVERFLOW = "Yjo5OTk5OTk5OTk5OTk5OTk5OTk5OQ=="


async def test_start_time_overflow_returns_422_not_500(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """R3: a start_time beyond the representable epoch range → 422 ERR_PAYLOAD_INVALID."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": _START_TIME_BEYOND_YEAR_9999},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "ERR_PAYLOAD_INVALID"


async def test_end_time_overflow_returns_422_not_500(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """R4: an end_time beyond the representable epoch range → 422 ERR_PAYLOAD_INVALID."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "end_time": _START_TIME_BEYOND_YEAR_9999},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "ERR_PAYLOAD_INVALID"


async def test_cursor_overflow_returns_422_not_500(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """R8: a well-formed cursor whose decoded int overflows time_t → 422, same code as malformed
    (no oracle distinguishing a forged far-future cursor from garbage)."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "page": _CURSOR_OVERFLOW},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "ERR_PAYLOAD_INVALID"
