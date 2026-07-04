"""BatchWindowBuffer — Redis-backed fixed-tick accumulation window per tenant.

§3 CONTRACT (batch-window-grouping TASK.md) — FROZEN @ v1.

Redis key scheme (per tenant_id):
  batch:window:items:{tenant_id}      — LIST of JSON-encoded {"custom_id", "key_id",
                                          "body"} dicts, one per accumulated request.
  batch:window:started:{tenant_id}    — STRING holding the epoch-seconds float the
                                          window opened at (the FIRST arrival's time —
                                          set NX so a later arrival never resets it).
  batch:window:active_tenants          — SET of tenant_id strings with an open window,
                                          so BatchWindowFlusher can enumerate candidates
                                          without SCANning the whole keyspace.
  batch:window:result:{custom_id}     — per-item result marker written by
                                          claim_due (kind=claimed, atomically, as part
                                          of the SAME claim) and later overwritten by
                                          BatchWindowFlusher.publish_result
                                          (kind=submitted | failed). TTL'd so completed
                                          markers do not accumulate forever.
  batch:window:wait_sum:{tenant_id}    — STRING, INCRBYFLOAT'd by the already-computed
                                          `elapsed` on every successful claim (batch-
                                          observability-scaffolding TASK.md §3) — an
                                          all-time running sum, never reset, no TTL.
  batch:window:wait_count:{tenant_id}  — STRING, INCR'd by 1 alongside wait_sum, same
                                          claim, same task. Together they back
                                          GET /admin/batches/stats' avg_window_wait_ms
                                          (stats_router.py) — read-only from there,
                                          written only by _CLAIM_DUE_LUA below.

ATOMICITY (G6 — the hardest requirement): append and claim_due are each a single Lua
EVAL script. Redis executes scripts to completion with no other command interleaving
(single-threaded command execution) — this, not a GET-then-DELETE (explicitly
forbidden, non-atomic under N concurrent gateway replicas) or a RENAME trick, is what
guarantees exactly one replica's claim_due() call ever claims a given due window, and
that append can never race a concurrent claim into a "phantom" started_at-with-no-items
or a lost item (R6).

CLAIMED-MARKER RACE FIX (build-time finding, not in the original design): a claim and
its owning BatchWindowFlusher's DB write + dispatch are NOT atomic with each other (the
Lua script only owns the Redis-side hand-off). Without a signal in between, a racing
SSE generator's OWN bounded wait could time out in the gap between "claimed" and
"submitted", incorrectly concluding the window was never claimed and running the G10
sync fallback for an item that is ALSO now inside a real job — a latent double-submit
once a live provider adapter exists (none does yet). Fixed by having claim_due write a
"claimed" marker for every item it claims, atomically, in the SAME script — see
BatchDiversionAdapter._lifecycle's two-tier wait for the consuming side.

DOUBLE-PROCESSING FIX (adversarial-review finding, not in the original design): the
CLAIMED-MARKER RACE FIX above still left a gap on the OTHER side of the same race — a
merely-SLOW (not crashed) flusher recovering after a caller's own short-wait deadline
would still claim_due() and dispatch that item as a real batch job, even though the
caller already gave up and ran G10's sync fallback for it, double-processing the same
logical request. try_abandon() closes this: before falling back, the caller atomically
marks the item abandoned IFF claim_due has not already claimed (or resolved) it — a
single Lua GET-then-maybe-SET, same atomicity convention as append/claim_due. claim_due
in turn skips — never claims, never returns — any item whose result key already reads
non-nil at the moment it goes to claim it. Whichever of {try_abandon, claim_due} runs
its script first wins; Redis's serial script execution is what guarantees exactly one
of them does, never both and never neither. See BatchDiversionAdapter._lifecycle for
the consuming side.

Settings (batch_window_seconds, batch_max_items_per_job) are read FRESH from the live
Settings object on every call, never snapshotted — this lets tests shrink the window
after app construction (app.state.settings.batch_window_seconds = ...) and lets an
operator tune it live.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    import redis.asyncio as aioredis

    from gateway.core.config import Settings

# Bounds every Redis op below — mirrors RedisBatchJobQueue's _ENQUEUE_TIMEOUT_SECONDS
# convention: a hung connection degrades to a raised TimeoutError rather than blocking
# the request/flush path indefinitely. The CALLER (BatchDiversionAdapter/
# BatchWindowFlusher) decides fail-open on the exception — this module never swallows
# on append/claim_due/active_tenant_ids/publish_result (only wait_for_result does,
# documented on that method specifically, since its caller is an SSE generator that
# cannot propagate an exception without corrupting the stream).
_DEFAULT_TIMEOUT_SECONDS = 2.0

_RESULT_KEY_PREFIX = "batch:window:result:"
_ACTIVE_TENANTS_KEY = "batch:window:active_tenants"
# TTL for a "claimed" marker and for a terminal (submitted/failed) marker. Generous
# relative to any realistic poll window — this is cleanup, not a correctness bound:
# each is read at most once, by its single owner, well inside this window.
_RESULT_TTL_SECONDS = 300

# TTL for an "abandoned" marker specifically (adversarial-review finding, not in the
# original design) — deliberately NOT _RESULT_TTL_SECONDS. Unlike a claimed/terminal
# marker, nothing ever reads this one on purpose; its only job is to keep refusing
# claim_due until the window it belongs to is next drained (see _CLAIM_DUE_LUA). That
# drain can happen arbitrarily later than an ordinary poll (flusher stall, deploy,
# incident) — so THIS is a real correctness bound, not cleanup: if it expires before
# the window is drained, a later claim_due sees a nil key and silently re-claims an
# item that was already sync_fallback'd (G6 "never two"). Hours, not minutes — wide
# enough to cover any realistic operational stall. A stall longer than this is an
# accepted, documented residual (same shape as the crashed-flusher residual already
# in this module), not a live code path any test exercises.
_ABANDONED_TTL_SECONDS = 14400  # 4 hours

# KEYS[1]=items list key, KEYS[2]=started_at key, KEYS[3]=active_tenants set key
# ARGV[1]=tenant_id str, ARGV[2]=now (epoch seconds str), ARGV[3]=item JSON str
_APPEND_LUA = """
redis.call('SET', KEYS[2], ARGV[2], 'NX')
redis.call('RPUSH', KEYS[1], ARGV[3])
redis.call('SADD', KEYS[3], ARGV[1])
return 1
"""

# KEYS[1]=items list key, KEYS[2]=started_at key, KEYS[3]=active_tenants set key,
# KEYS[4]=wait_sum key, KEYS[5]=wait_count key
# ARGV[1]=tenant_id str, ARGV[2]=now, ARGV[3]=window_seconds, ARGV[4]=max_items,
# ARGV[5]=result_key_prefix, ARGV[6]=claimed_marker_ttl_seconds
# Returns the Redis nil (Python None) when nothing is due yet, else the claimed
# item list (and DELs the items/started_at keys + SREMs the tenant, all atomically).
# Per item, skips (never claims, never returns) any custom_id whose result key is
# already non-nil -- a try_abandon() call won the race for that one item; the window's
# housekeeping (DEL/SREM) still happens unconditionally so it never lingers. The
# abandoned marker itself is DELeted right here too (batch-claim-drain-del TASK.md)
# -- this is the one moment its owning window drains, ever, so its job is done;
# cleanup no longer depends on _ABANDONED_TTL_SECONDS in the common case.
# batch-observability-scaffolding TASK.md §3: once `due` is confirmed true (never on
# the early `return false` path above), additively records this claim's already-
# computed `elapsed` into KEYS[4]/KEYS[5] -- a pure side effect purely for
# GET /admin/batches/stats' avg_window_wait_ms; the claimed_items return value below
# is untouched, byte-identical to before this addition.
_CLAIM_DUE_LUA = """
local started = redis.call('GET', KEYS[2])
if not started then
  return false
end
local count = redis.call('LLEN', KEYS[1])
local now = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local max_items = tonumber(ARGV[4])
local elapsed = now - tonumber(started)
local due = false
if elapsed >= window then
  due = true
end
if count >= max_items then
  due = true
end
if not due then
  return false
end
redis.call('INCRBYFLOAT', KEYS[4], elapsed)
redis.call('INCR', KEYS[5])
local items = redis.call('LRANGE', KEYS[1], 0, -1)
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
redis.call('SREM', KEYS[3], ARGV[1])
local ttl = tonumber(ARGV[6])
local claimed_items = {}
for _, raw in ipairs(items) do
  local decoded = cjson.decode(raw)
  local result_key = ARGV[5] .. decoded['custom_id']
  local existing = redis.call('GET', result_key)
  if not existing then
    redis.call('SET', result_key, '{"kind":"claimed"}', 'EX', ttl)
    table.insert(claimed_items, raw)
  else
    redis.call('DEL', result_key)
  end
end
return claimed_items
"""

# KEYS[1]=result key for one custom_id, ARGV[1]=abandoned_marker_ttl_seconds
# Atomic GET-then-maybe-SET: refuses (returns false) when ANYTHING already occupies
# the result key -- claimed, or a terminal submitted/failed that raced in ahead of
# this call -- so it can never clobber a real, live claim. Only ever writes
# "abandoned" onto a genuinely-untouched key.
_TRY_ABANDON_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
  return false
end
redis.call('SET', KEYS[1], '{"kind":"abandoned"}', 'EX', ARGV[1])
return true
"""


def _items_key(tenant_id: uuid.UUID) -> str:
    return f"batch:window:items:{tenant_id}"


def _started_key(tenant_id: uuid.UUID) -> str:
    return f"batch:window:started:{tenant_id}"


def _wait_sum_key(tenant_id: uuid.UUID) -> str:
    return f"batch:window:wait_sum:{tenant_id}"


def _wait_count_key(tenant_id: uuid.UUID) -> str:
    return f"batch:window:wait_count:{tenant_id}"


class BatchWindowBuffer:
    """Redis-backed accumulation buffer: one fixed-tick window per tenant.

    Constructed cheaply (no I/O) — safe to build in create_app()'s synchronous body,
    same as BatchDiversionAdapter/RedisBatchJobQueue.
    """

    __slots__ = (
        "_abandon_script",
        "_append_script",
        "_claim_script",
        "_now",
        "_redis",
        "_settings",
        "_timeout",
    )

    def __init__(
        self,
        *,
        redis: aioredis.Redis,  # type: ignore[type-arg]
        settings: Settings,
        _now: Callable[[], float] = time.time,
        _timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._redis = redis
        self._settings = settings
        self._now = _now
        self._timeout = _timeout
        self._append_script = redis.register_script(_APPEND_LUA)
        self._claim_script = redis.register_script(_CLAIM_DUE_LUA)
        self._abandon_script = redis.register_script(_TRY_ABANDON_LUA)

    async def append(
        self,
        *,
        tenant_id: uuid.UUID,
        custom_id: str,
        key_id: uuid.UUID,
        body: dict[str, Any],
    ) -> None:
        """Atomically open-if-first + push this item onto tenant_id's window.

        Raises (TimeoutError included) on failure — the caller (BatchDiversionAdapter)
        treats any exception as R5's fail-open path (proceed synchronously, unchanged).
        """
        item = json.dumps({"custom_id": custom_id, "key_id": str(key_id), "body": body})
        await asyncio.wait_for(
            self._append_script(
                keys=[_items_key(tenant_id), _started_key(tenant_id), _ACTIVE_TENANTS_KEY],
                args=[str(tenant_id), str(self._now()), item],
            ),
            timeout=self._timeout,
        )

    async def claim_due(self, tenant_id: uuid.UUID) -> list[dict[str, Any]] | None:
        """Atomically claim tenant_id's window IFF it is due (G6/R6).

        Due = elapsed-since-first-arrival >= batch_window_seconds OR item count >=
        batch_max_items_per_job (read fresh from the live Settings object). Returns
        None when nothing is due (including when there is no open window at all).
        When due: atomically drains the items list, clears started_at, removes the
        tenant from the active set, and marks every returned item "claimed" — all in
        ONE Lua script, so a second concurrent caller (another replica) racing the
        same tenant_id is guaranteed to see the post-claim state and get None back.

        Raises on a genuine Redis failure — the caller (BatchWindowFlusher) treats
        that as "nothing claimed this cycle", never as a false empty claim.

        Additive side effect (batch-observability-scaffolding TASK.md §3): a
        successful claim also records this window's elapsed wait time into a
        per-tenant Redis sum+count pair (batch:window:wait_sum/wait_count) — read by
        GET /admin/batches/stats' avg_window_wait_ms. Never affects the value
        returned here.
        """
        raw = await asyncio.wait_for(
            self._claim_script(
                keys=[
                    _items_key(tenant_id),
                    _started_key(tenant_id),
                    _ACTIVE_TENANTS_KEY,
                    _wait_sum_key(tenant_id),
                    _wait_count_key(tenant_id),
                ],
                args=[
                    str(tenant_id),
                    str(self._now()),
                    str(self._settings.batch_window_seconds),
                    str(self._settings.batch_max_items_per_job),
                    _RESULT_KEY_PREFIX,
                    str(_RESULT_TTL_SECONDS),
                ],
            ),
            timeout=self._timeout,
        )
        if not raw:
            return None
        claimed: list[dict[str, Any]] = []
        for entry in raw:
            text = entry if isinstance(entry, str) else entry.decode()
            claimed.append(json.loads(text))
        return claimed

    async def try_abandon(self, custom_id: str) -> bool:
        """Atomically mark custom_id abandoned IFF nothing has claimed/resolved it yet.

        Returns True when this call wins: the result key was genuinely empty, so it is
        now "abandoned" and the caller (BatchDiversionAdapter) may safely run
        sync_fallback. A later claim_due for this item's window will see the
        "abandoned" marker and skip claiming it (see _CLAIM_DUE_LUA), so it is never
        double-processed.

        Returns False when the result key was already occupied — a claim_due call (or,
        rarer, a terminal submitted/failed racing in) got there first. The caller must
        NOT fall back in that case; it must re-wait for the real terminal outcome.

        A single atomic GET-then-maybe-SET Lua script, not a plain SET — this is the
        same atomicity convention as append/claim_due, and it is what makes this call
        safe against the exact inverse of the race claim_due already guards against:
        whichever of {this call, a concurrent claim_due} runs its script first wins;
        Redis's serial script execution guarantees no interleaving between them.

        The marker this writes uses _ABANDONED_TTL_SECONDS, NOT _RESULT_TTL_SECONDS —
        see that constant's comment for why the two must not be conflated.

        Raises on a genuine Redis failure — the caller decides the fail-open behavior,
        same convention as every other method here except wait_for_result. A raise
        here is genuinely ambiguous (the Lua script may have completed server-side
        despite a client-side timeout) — the caller does not treat a raise as
        EVIDENCE of either outcome. It takes the same branch as a False return
        (re-wait) purely because that is the safe default under ambiguity, not
        because a raise means the same thing as False; see
        BatchDiversionAdapter._lifecycle.
        """
        result = await asyncio.wait_for(
            self._abandon_script(
                keys=[f"{_RESULT_KEY_PREFIX}{custom_id}"],
                args=[str(_ABANDONED_TTL_SECONDS)],
            ),
            timeout=self._timeout,
        )
        return bool(result)

    async def active_tenant_ids(self) -> list[uuid.UUID]:
        """All tenant_ids with a currently-open window — BatchWindowFlusher's
        per-cycle candidate list (avoids SCANning the whole Redis keyspace)."""
        raw = await asyncio.wait_for(
            self._redis.smembers(_ACTIVE_TENANTS_KEY), timeout=self._timeout
        )
        return [uuid.UUID(r if isinstance(r, str) else r.decode()) for r in raw]

    async def publish_result(self, custom_id: str, result: dict[str, Any]) -> None:
        """Overwrite custom_id's result marker with a terminal outcome (submitted or
        failed) — called by BatchWindowFlusher once a claimed group resolves.

        A plain SET is sufficient here (no extra atomicity needed): claim_due already
        guarantees exactly one flusher instance ever owns a given claimed custom_id, so
        there is never a concurrent writer to race against.
        """
        await asyncio.wait_for(
            self._redis.set(
                f"{_RESULT_KEY_PREFIX}{custom_id}",
                json.dumps(result),
                ex=_RESULT_TTL_SECONDS,
            ),
            timeout=self._timeout,
        )

    async def wait_for_result(
        self,
        custom_id: str,
        *,
        short_max_wait: float,
        long_max_wait: float,
        poll_interval: float = 0.05,
    ) -> dict[str, Any] | None:
        """Two-tier bounded poll for custom_id's result marker (G10).

        Tier 1 (short_max_wait, from call-time): before ever observing a "claimed"
        marker, bounded by short_max_wait — expiring here means the window was
        genuinely never claimed (buffer/sweeper unreachable, or this call simply
        raced ahead of a slow sweeper tick); safe to fall back immediately.
        Tier 2 (long_max_wait, from call-time): once "claimed" IS observed, a real
        flush has begun — the caller must NOT fall back on the short bound (that item
        is, or is about to be, inside a real job row; falling back now risks a
        double-submit once a live processor exists). long_max_wait is a generous outer
        safety ceiling covering only the pathological case of the flusher process
        crashing between claim and its first terminal write (a healthy claim ->
        submitted/failed transition is sub-second) — an accepted, documented residual,
        matching how this codebase already treats a crashed worker elsewhere (orphan-
        recovery on restart, not a live-connection concern).

        Deliberately swallows per-poll Redis exceptions (unlike every other method on
        this class) — its caller is an SSE generator that cannot propagate an
        exception without corrupting/hanging the already-committed 200 stream; a
        transient hiccup here degrades to "keep polling", bounded by the same
        deadlines either way.
        """
        key = f"{_RESULT_KEY_PREFIX}{custom_id}"
        deadline_short = self._now() + short_max_wait
        deadline_long = self._now() + long_max_wait
        saw_claimed = False
        while True:
            now = self._now()
            if saw_claimed:
                if now >= deadline_long:
                    return None
            elif now >= deadline_short:
                return None
            try:
                raw = await asyncio.wait_for(self._redis.get(key), timeout=self._timeout)
            except Exception:
                raw = None
            if raw is not None:
                text = raw if isinstance(raw, str) else raw.decode()
                try:
                    payload = json.loads(text)
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict):
                    kind = payload.get("kind")
                    if kind == "claimed":
                        saw_claimed = True
                    elif kind in ("submitted", "failed"):
                        return payload
            await asyncio.sleep(poll_interval)


__all__ = ["BatchWindowBuffer"]
