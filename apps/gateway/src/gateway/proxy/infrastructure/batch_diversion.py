"""Infrastructure implementation: BatchDiversionAdapter.

§3 CONTRACT (batch-window-grouping TASK.md) — FROZEN @ v1.

SUPERSEDES batch-auto-grouping's (TASK.md, gate=PASS) size-1-job-per-request
diversion: instead of immediately creating a size-1 batch job and returning a flat
batch_reference JSON envelope, an eligible request is now appended to a per-tenant
BatchWindowBuffer (fixed-tick accumulation window) and the HTTP response is ALWAYS a
200 text/event-stream, returned immediately on acceptance (G8) — "event: queued" right
away, then either "event: submitted" (the window flushed; poll the real result via the
existing GET /v1/batches/{id}, unchanged) or "event: completion" (G10 per-item sync
fallback: flush failed, or the window elapsed with no recorded flush attempt at all —
the verbatim ChatCompletion body, delivered as the stream's terminal event since the
200 status and text/event-stream header are already committed and cannot change).

The M4 safety-gate (batch-auto-grouping TASK.md §1) is restored verbatim, gating
APPEND rather than just flush (batch-window-grouping build note, Tin 2026-07-03): a
missing batch_processor returns None immediately, BEFORE ever touching the buffer —
this is what keeps enabling the tenant policy pre-adapter a genuine zero-added-latency
no-op, not a landmine that would otherwise sit "queued" for up to batch_window_seconds
only to learn nothing could process it.

Two independent failure modes are handled, both by returning None (R5 fail-open,
"proceed synchronously exactly as if the policy were disabled"):
  1. buffer.append() raises (Redis unreachable) — caught here, nothing was ever
     buffered, so falling back to sync is correct and leaves nothing behind.
  2. batch_processor is None — the safety gate above, checked before append.

Once append() succeeds, a caller-visible response (the SSE stream) is ALWAYS
returned — the generator itself resolves via BatchWindowFlusher's later claim (a
"submitted" event) or, failing that, sync_fallback (a "completion" event) — never a
raised exception, never a hung connection (G10's own bounded wait guarantees this).

DOUBLE-PROCESSING FIX (adversarial-review finding, not in the original design): a
None from wait_for_result does NOT reliably mean "never claimed" on its own — its own
deadline check can fire in the exact same instant a concurrent claim_due() writes
"claimed" (see BatchWindowBuffer.wait_for_result). _lifecycle therefore calls
buffer.try_abandon() before ever falling back: True means it genuinely won (safe to
run sync_fallback, exactly as before); False means a claim already exists, so it
re-waits once, bounded, for the real terminal marker instead of double-processing the
item. See BatchWindowBuffer's module docstring for the Redis-side half of this fix.

try_abandon EXCEPTION HANDLING (second adversarial-review finding on the fix above):
an exception out of try_abandon is genuinely ambiguous — the Lua script can complete
server-side even as the client-side call times out (proven empirically, not just
hypothesized), so the caller cannot tell whether nothing happened, its own write
landed, or a claim got there first. Treating that ambiguity as "safe to fall back"
risks the exact double-process this fix exists to prevent (if a claim actually won).
_lifecycle therefore treats a try_abandon exception identically to a False return:
re-wait once, bounded, same as the documented race above. This is deliberately NOT
the same convention as append()'s immediate-fallback (R5) — append failing leaves
nothing buffered, so falling back is unconditionally safe, but try_abandon failing
may leave a live claim behind, so it is not. In the rare case the exception meant the
script genuinely never ran AND the flusher does not claim the item within the
re-wait's bound either, this collapses into the same accepted crashed-flusher
residual _CLAIMED_CEILING_SECONDS already documents — not a new residual.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from gateway.proxy.domain.ports import BatchDivertedStream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from gateway.core.config import Settings
    from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer

_log = logging.getLogger(__name__)

# Fixed slack added on top of 1x batch_window_seconds before concluding "this item's
# window was never claimed at all" (G10's elapsed-with-no-flush case). Generous by
# design: a late arrival only needs to wait out the REMAINDER of an already-open
# window (well under this bound), and over-waiting here only costs latency on an
# already-rare fallback path, never correctness. Exposed as a module attribute (not a
# local constant) specifically so tests can shrink it via monkeypatch alongside a
# small Settings.batch_window_seconds, without touching production defaults.
_SHORT_WAIT_MARGIN_SECONDS = 1.0

# Outer safety ceiling once a "claimed" marker HAS been observed (see
# BatchWindowBuffer.wait_for_result's two-tier design) — covers only the pathological
# case of the flusher process crashing between claim and its first terminal write; a
# healthy claim -> submitted/failed transition is sub-second. An accepted, documented
# residual (matches this codebase's existing crashed-worker-recovers-on-restart
# precedent elsewhere), not a scenario this task's test suite exercises directly.
_CLAIMED_CEILING_SECONDS = 30.0


class BatchDiversionAdapter:
    """Diverts an eligible /v1/chat/completions request into BatchWindowBuffer's
    per-tenant accumulation window, returning an SSE lifecycle stream immediately.

    Constructed once at startup (main.py) — cheap, holds only stable references.
    """

    __slots__ = ("_buffer", "_settings")

    def __init__(self, *, settings: Settings, buffer: BatchWindowBuffer) -> None:
        self._settings = settings
        self._buffer = buffer

    async def try_divert(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        body: dict[str, Any],
        batch_processor: object | None,
        sync_fallback: Callable[[], Awaitable[dict[str, Any]]],
    ) -> BatchDivertedStream | None:
        """Return a BatchDivertedStream when genuinely accumulated, else None.

        NEVER raises — every failure mode is caught and logged; a None result
        signals the caller to proceed synchronously, unchanged.
        """
        if batch_processor is None:
            # Restored M4 safety branch (d), gating APPEND not just flush: see module
            # docstring. Keeps a pre-adapter tenant byte-identical to policy-disabled.
            return None

        custom_id = str(uuid.uuid4())
        try:
            await self._buffer.append(
                tenant_id=tenant_id, custom_id=custom_id, key_id=key_id, body=body
            )
        except Exception:
            _log.warning(
                "batch window buffer append failed; falling back to sync (R5)",
                exc_info=True,
            )
            return None

        window_seconds = self._settings.batch_window_seconds
        short_max_wait = window_seconds + _SHORT_WAIT_MARGIN_SECONDS
        long_max_wait = short_max_wait + _CLAIMED_CEILING_SECONDS
        buffer = self._buffer

        async def _lifecycle() -> AsyncIterator[bytes]:
            yield _sse_event("queued", {"custom_id": custom_id})

            result = await buffer.wait_for_result(
                custom_id, short_max_wait=short_max_wait, long_max_wait=long_max_wait
            )
            if result is None:
                # Never observed a terminal marker. Before concluding "never claimed,
                # safe to fall back", atomically check whether a claim actually won
                # this exact instant (see module docstring: wait_for_result's own
                # deadline check can fire in the same narrow window a concurrent
                # claim_due() writes "claimed").
                try:
                    won_abandon = await buffer.try_abandon(custom_id)
                except Exception:
                    # Ambiguous -- the script may have completed server-side despite
                    # this call raising (see module docstring). Treat exactly like a
                    # refusal below: re-wait rather than risk falling back onto a live
                    # claim.
                    _log.warning(
                        "try_abandon failed; re-waiting before fallback (G6)",
                        exc_info=True,
                    )
                    won_abandon = False
                if not won_abandon:
                    # Refused (or ambiguous) -- a claim (or, rarer, a terminal
                    # result) may already exist. Falling back now would risk
                    # double-processing an item that is, or already was, inside a
                    # real job. Re-wait once, bounded on the claimed ceiling, for the
                    # real terminal marker -- if a flusher genuinely crashed
                    # mid-flush (or try_abandon's own write genuinely never landed)
                    # this also times out and we fall through to sync_fallback below
                    # anyway, the same accepted residual _CLAIMED_CEILING_SECONDS
                    # already documents, not a new one. Deliberately NOT a loop: a
                    # genuinely crashed flush must resolve via this one fallback, not
                    # spin forever.
                    result = await buffer.wait_for_result(
                        custom_id,
                        short_max_wait=_CLAIMED_CEILING_SECONDS,
                        long_max_wait=_CLAIMED_CEILING_SECONDS,
                    )

            if result is not None and result.get("kind") == "submitted":
                yield _sse_event(
                    "submitted",
                    {
                        "batch_job_id": result["batch_job_id"],
                        "custom_id": result["custom_id"],
                        "poll_url": result["poll_url"],
                    },
                )
                return

            # G10: an explicit "failed" marker, a silently-elapsed window with no
            # recorded flush attempt at all, and a claimed-but-crashed flusher's
            # re-wait above ALL collapse to the IDENTICAL fallback path — none of them
            # ever produced a "submitted" marker within their respective bound.
            fallback_body = await sync_fallback()
            yield _sse_event("completion", fallback_body)

        return BatchDivertedStream(body_stream=_lifecycle())


def _sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


__all__ = ["BatchDiversionAdapter"]
