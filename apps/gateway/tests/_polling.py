"""Wait for a fire-and-forget write instead of sleeping a fixed interval.

suite-stability M3. Several suites did:

    await asyncio.sleep(0.1)
    rows = await _fetch(...)
    assert len(rows) == 2

which passes on an idle laptop and fails under `-n 6` on a loaded host — the
write simply had not landed yet. It produced 11 failures across 8 full runs,
in 7 different suites, and was the last thing keeping the suite from three
consecutive green runs.

USE THIS ONLY FOR A POSITIVE WAIT — "wait until the expected rows appear".

Do NOT use it to replace a sleep whose purpose is to prove something NEVER
happens ("a second record_failure() while OPEN must not produce a second row").
There, the sleep IS the test: polling returns the instant the first row exists
and never gives the unwanted second write a chance to appear, which turns a real
assertion into a vacuous one. Those sites keep their fixed sleep deliberately.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_INTERVAL_SECONDS = 0.05


async def poll_until[T](
    fetch: Callable[[], Awaitable[T]],
    ready: Callable[[T], bool],
    *,
    # noqa: ASYNC109 — `timeout` here bounds a POLLING LOOP of independent fetches, not a
    # single cancellable await, so the rule's advice to take a cancel scope instead does
    # not apply. Same justified exception as
    # gateway.vector_stores.application.ingest_worker.claim.
    timeout: float = DEFAULT_TIMEOUT_SECONDS,  # noqa: ASYNC109
    interval: float = DEFAULT_INTERVAL_SECONDS,
) -> T:
    """Re-run `fetch` until `ready` holds, or the timeout expires.

    Returns the LAST fetched value either way — the caller still makes the real
    assertion, so a genuinely missing write fails exactly as before, just later.
    Costs nothing when the write has already landed.
    """
    deadline = time.monotonic() + timeout
    result = await fetch()
    while not ready(result) and time.monotonic() < deadline:
        await asyncio.sleep(interval)
        result = await fetch()
    return result


async def poll_for_count[T](
    fetch: Callable[[], Awaitable[list[T]]],
    expected: int,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,  # noqa: ASYNC109 — see poll_until above
) -> list[T]:
    """`poll_until` for the common "wait for N rows to appear" case."""
    return await poll_until(fetch, lambda rows: len(rows) >= expected, timeout=timeout)
