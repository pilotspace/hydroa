"""Redis-backed per-model cooldown circuit breaker.

Implements the ModelHealthGate protocol from gateway.proxy.domain.ports.
Contract FROZEN @ cooldown-circuit TASK.md §3 (Amendments B1-B3 binding).

State machine (authoritative order in is_available):
  1. threshold == 0 → True (zero Redis commands).
  2. open key present → False (OPEN state).
  3. half marker present → HALF_OPEN:
       SET probe NX EX ttl_s;
       if NX succeeds → True + emit transition="probe";
       else → False (probe claimed by another caller).
  4. otherwise (no open, no half) → CLOSED → True, read-only, zero Redis writes.

Key shapes — TENANT-PARTITIONED (tenant-scoped-breaker-cooldown, R9 P0 #3):
  gateway:cooldown:fails:{tenant}:{model_id}   — INCR counter; EXPIRE NX cooldown_window_s
  gateway:cooldown:open:{tenant}:{model_id}    — SET "1" EX cooldown_ttl_s on trip
  gateway:cooldown:half:{tenant}:{model_id}    — SET "1" EX (2*cooldown_ttl_s) on trip/re-trip
  gateway:cooldown:probe:{tenant}:{model_id}   — SET "1" NX EX cooldown_ttl_s in HALF_OPEN

ALL FOUR kinds carry the tenant, not just `open` (A31). Partitioning only `open`
would leave the shared `fails` COUNTER intact, so tenant A's failures would still
accumulate toward a trip that opens tenant B's circuit — the defect surviving in
the one key that actually causes it. `probe` is equally load-bearing: shared, it
lets one tenant claim the single half-open probe token and force every other
tenant to be refused.

TENANT SEGMENT FIRST, model_id LAST — this ordering is a SECURITY property, not a
style choice (R:KEY_COLLISION / A20). `model_id` arrives straight from the request
body and MAY contain ":". With the tenant segment first, a crafted model id can
only ever address deeper inside its OWN tenant's namespace; it can never climb out
of it into a neighbour's. Any shape placing model_id before the tenant would make a
cross-tenant read/write reachable by a caller-controlled string — validation would
then be load-bearing, whereas this ordering makes the collision unreachable BY
CONSTRUCTION.

Fail-OPEN: any Redis error → log WARNING (no key string, no payload, no credential
material); behave as available / no-op.

B3: model_id is a public catalog id and MAY appear in log fields.
"""

from __future__ import annotations

from typing import Any

import structlog

from gateway.observability.metrics import MetricsRegistry
from gateway.proxy.infrastructure.tenant_breaker_registry import tenant_key_segment

# Redis key prefixes. The `{tenant_key}` slot is filled by `_key()` below; it sits
# BEFORE the caller-controlled model id in every one of the four kinds (see the
# module docstring's R:KEY_COLLISION note).
_PFX_FAILS = "gateway:cooldown:fails:{tenant_key}:"
_PFX_OPEN = "gateway:cooldown:open:{tenant_key}:"
_PFX_HALF = "gateway:cooldown:half:{tenant_key}:"
_PFX_PROBE = "gateway:cooldown:probe:{tenant_key}:"

# Bounded cross-partition SCAN (the superadmin routing board only — never the hot
# path). SCAN is O(keyspace); these two constants cap the work one admin read may
# do, and an INCOMPLETE scan reports "unknown" rather than "closed" so the board
# never claims a circuit is fine over partitions it did not actually inspect.
_SCAN_COUNT = 500
_SCAN_MAX_ITERATIONS = 20


def _key(prefix: str, tenant_id: Any, model_id: str) -> str:
    """Build one tenant-partitioned cooldown key.

    `tenant_id is None` renders the reserved sentinel segment — never the legacy
    unprefixed key (A17). A bare key would BE the pre-fix global key, i.e. the
    defect surviving as the None case.
    """
    return prefix.format(tenant_key=tenant_key_segment(tenant_id)) + model_id


def _fixed_prefix(prefix: str) -> str:
    """The constant head of a key template, e.g. "gateway:cooldown:open:"."""
    return prefix.format(tenant_key="")[:-1]


def _split_partitioned_key(prefix: str, key: str) -> tuple[str, str] | None:
    """Split `<prefix>{tenant}:{model_id}` into (tenant_segment, model_id).

    The tenant segment never contains ":" (`tenant_key_segment` normalises it), so
    the FIRST colon after the fixed prefix is unambiguously the tenant/model
    boundary — even when the caller-controlled model id contains colons of its own.
    Returns None for a key that does not belong to this prefix.
    """
    fixed = _fixed_prefix(prefix)
    if not key.startswith(fixed):
        return None
    tenant_segment, sep, model_id = key[len(fixed) :].partition(":")
    if not sep:
        return None
    return tenant_segment, model_id


class RedisCooldownGate:
    """Redis-backed per-model cooldown gate implementing ModelHealthGate.

    Constructed once at create_app() when cooldown_failure_threshold > 0.
    When threshold == 0, is_available always returns True and all record_*
    calls are no-ops with zero Redis commands (CC3 guarantee).

    Constructor does NOT connect to Redis — safe to call without lifespan.

    The redis parameter is typed Any because redis.asyncio does not ship
    bundled stubs compatible with mypy strict; using Any avoids attr-defined
    noise while keeping all runtime behaviour correct.
    """

    def __init__(
        self,
        *,
        redis: Any,
        metrics_registry: MetricsRegistry,
        threshold: int,
        ttl_s: int,
        window_s: int,
    ) -> None:
        self._redis: Any = redis
        self._metrics = metrics_registry
        self._threshold = threshold
        self._ttl_s = ttl_s
        self._window_s = window_s

    # ------------------------------------------------------------------
    # Public protocol methods
    # ------------------------------------------------------------------

    async def is_available(self, model_id: str, *, tenant_id: Any) -> bool:
        """Return True iff the model should be attempted.

        Authoritative order (per §3 B1 amendment):
          1. threshold == 0 → True (no Redis).
          2. open key present → False (OPEN).
          3. half marker present → HALF_OPEN probe machinery.
          4. otherwise → CLOSED → True (read-only, zero writes).
        """
        # Step 1: fast-path; zero Redis commands.
        if self._threshold == 0:
            return True

        try:
            redis: Any = self._redis

            # Step 2: check OPEN key.
            open_key = _key(_PFX_OPEN, tenant_id, model_id)
            open_val = await redis.get(open_key)
            if open_val is not None:
                return False

            # Step 3: check half marker (HALF_OPEN detection — B1).
            half_key = _key(_PFX_HALF, tenant_id, model_id)
            half_val = await redis.get(half_key)
            if half_val is not None:
                # HALF_OPEN: attempt to claim probe token with SET NX.
                probe_key = _key(_PFX_PROBE, tenant_id, model_id)
                claimed = await redis.set(probe_key, "1", ex=self._ttl_s, nx=True)
                if claimed is True:
                    # This caller owns the probe.
                    self._metrics.cooldown_transitions_total.labels(
                        model=model_id, transition="probe"
                    ).inc()
                    return True
                # Probe already claimed by another caller.
                return False

            # Step 4: CLOSED state — no open key, no half marker.
            # Read-only True; zero Redis writes (B1 regression CC10).
            return True

        except Exception as exc:
            structlog.get_logger().warning(
                "cooldown_gate_redis_error",
                model_id=model_id,
                error=type(exc).__name__,
            )
            return True  # fail-OPEN

    async def record_failure(self, model_id: str, *, tenant_id: Any) -> None:
        """Record a retry-exhausted failure for model_id.

        CLOSED → INCR fails; EXPIRE NX window_s; if count >= threshold → trip.
        HALF_OPEN (probe token present) → immediate re-trip (no threshold accumulation).
        """
        if self._threshold == 0:
            return

        try:
            redis: Any = self._redis
            probe_key = _key(_PFX_PROBE, tenant_id, model_id)
            half_key = _key(_PFX_HALF, tenant_id, model_id)

            # Detect HALF_OPEN by checking whether the probe key or half marker exists.
            # The probe caller set the probe key in is_available; record_failure is
            # called by FallbackModelRouter right after the upstream attempt fails.
            probe_val = await redis.get(probe_key)
            half_val = await redis.get(half_key)

            if probe_val is not None or half_val is not None:
                # HALF_OPEN re-trip: immediate SET open + REFRESH half + DEL probe.
                await self._trip(redis, model_id, tenant_id, transition="reopened")
                return

            # CLOSED → accumulate failure counter.
            fails_key = _key(_PFX_FAILS, tenant_id, model_id)
            count = await redis.incr(fails_key)
            # EXPIRE NX: set expiry only if not already set (preserves running window).
            await redis.expire(fails_key, self._window_s, nx=True)

            if count >= self._threshold:
                # Trip: SET open + SET half + DEL fails.
                open_key = _key(_PFX_OPEN, tenant_id, model_id)
                await redis.set(open_key, "1", ex=self._ttl_s)
                await redis.set(half_key, "1", ex=2 * self._ttl_s)
                await redis.delete(fails_key)
                self._metrics.cooldown_transitions_total.labels(
                    model=model_id, transition="tripped"
                ).inc()

        except Exception as exc:
            structlog.get_logger().warning(
                "cooldown_gate_redis_error",
                model_id=model_id,
                error=type(exc).__name__,
            )
            # fail-OPEN: no-op

    async def record_success(self, model_id: str, *, tenant_id: Any) -> None:
        """Record a successful completion for model_id.

        DEL fails, probe, half (idempotent).
        Emit transition="closed" ONLY when the half marker was actually present
        (i.e., a probe/half-open state was being cleared). Plain successes on a
        CLOSED model (no half marker) emit no transition.
        """
        if self._threshold == 0:
            return

        try:
            redis: Any = self._redis
            fails_key = _key(_PFX_FAILS, tenant_id, model_id)
            probe_key = _key(_PFX_PROBE, tenant_id, model_id)
            half_key = _key(_PFX_HALF, tenant_id, model_id)

            # Check half marker before deleting (determines whether to emit "closed").
            half_val = await redis.get(half_key)

            await redis.delete(fails_key, probe_key, half_key)

            if half_val is not None:
                self._metrics.cooldown_transitions_total.labels(
                    model=model_id, transition="closed"
                ).inc()

        except Exception as exc:
            structlog.get_logger().warning(
                "cooldown_gate_redis_error",
                model_id=model_id,
                error=type(exc).__name__,
            )
            # fail-OPEN: no-op

    # ------------------------------------------------------------------
    # Routing-admin read surface (additive; NOT part of ModelHealthGate protocol)
    # ------------------------------------------------------------------

    async def snapshot_state(self, model_id: str, *, tenant_id: Any) -> str:
        """Return the current circuit state for model_id as a string.

        `tenant_id` is KEYWORD-ONLY and REQUIRED — no default. A23 applies here
        exactly as it does to the `ModelHealthGate` methods: a caller that has not
        thought about WHICH partition it is reading must fail loudly at the call
        site, never inherit a silent one. That matters more here than anywhere
        else, because the two legal answers mean opposite things (see below) and a
        defaulted ``None`` would hand a careless caller the expensive
        cross-partition SCAN while looking like a plain read.

        Returns one of: "open" | "half_open" | "closed" | "unknown".

        `tenant_id` semantics — READ ONE PARTITION vs READ ACROSS ALL OF THEM:
          - a tenant id  → exactly that tenant's partition (two GETs, as before).
          - ``None``     → the honest CROSS-PARTITION aggregate (M7/A18). This is
            the superadmin routing board's case: `GET /admin/routing` is a
            `require_superadmin` route with NO tenant in scope, so after
            partitioning "the state of model X" no longer has a single answer.

        NOTE the deliberate asymmetry with `is_available`/`record_*`, where
        ``tenant_id=None`` means the reserved SENTINEL partition (A17). There, the
        call has a definite — if unattributed — owner and must write somewhere. Here
        the reader has no owner at all and must not silently pick one tenant's
        partition and present it as the fleet's.

        Aggregate rule (A18/E7): a model that is open in ANY partition must NEVER be
        reported as plainly "closed". "closed" is returned only when a COMPLETE scan
        found no open and no half marker anywhere. If the bounded scan does not
        complete, the answer is "unknown" — a silent "closed" over an uninspected
        partition is the masked-gate failure mode, and this board exists precisely
        for the incident during which it would be misleading.

        Side-effects: NONE — read-only; zero Redis writes; zero metric increments.
        The scan is bounded by `_SCAN_MAX_ITERATIONS` x `_SCAN_COUNT`; SCAN is a
        cold-path admin cost and never runs on the completion hot path.

        NOTE: This method is concrete-class only — it is NOT part of the frozen
        ModelHealthGate protocol. If a second ModelHealthGate implementation is
        introduced in the future, it must either implement snapshot_state or the
        routing-admin router must add an isinstance guard. See routing-admin §3.
        """
        try:
            redis: Any = self._redis
            if tenant_id is not None:
                open_val = await redis.get(_key(_PFX_OPEN, tenant_id, model_id))
                if open_val is not None:
                    return "open"
                half_val = await redis.get(_key(_PFX_HALF, tenant_id, model_id))
                if half_val is not None:
                    return "half_open"
                return "closed"

            open_hit, open_complete = await self._any_partition(redis, _PFX_OPEN, model_id)
            if open_hit:
                return "open"
            half_hit, half_complete = await self._any_partition(redis, _PFX_HALF, model_id)
            if half_hit:
                return "half_open"
            if not (open_complete and half_complete):
                # Inspected only part of the keyspace — say so rather than claiming
                # a clean board over partitions never looked at.
                return "unknown"
            return "closed"
        except Exception as exc:
            structlog.get_logger().warning(
                "cooldown_gate_redis_error",
                model_id=model_id,
                error=type(exc).__name__,
            )
            return "unknown"

    async def _any_partition(self, redis: Any, prefix: str, model_id: str) -> tuple[bool, bool]:
        """Is `model_id` present under `prefix` in ANY tenant partition?

        Returns (found, scan_ran_to_completion).

        The MATCH pattern contains NO caller-supplied data — it is the fixed key
        prefix plus `*`, and the model id is compared EXACTLY in Python afterwards.
        That is deliberate: `model_id` comes from the catalog/request and embedding
        it in a glob would let `*` widen the match to other models' keys, and glob
        escaping is subtly dialect-specific. Exact comparison has neither problem.

        Bounded by `_SCAN_MAX_ITERATIONS` cursor round-trips; stops at the FIRST
        match, since the aggregate only needs existence. An incomplete scan is
        reported as such so the caller can answer "unknown" rather than "closed".
        """
        # NB: the glob is the fixed prefix + "*" — NOT `format(tenant_key="*")`, which
        # would append a trailing ":" that no live key ends with, so the scan would
        # silently match nothing and every model would read "closed".
        pattern = _fixed_prefix(prefix) + "*"
        cursor: int = 0
        for _ in range(_SCAN_MAX_ITERATIONS):
            cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=_SCAN_COUNT)
            for raw in keys:
                key = raw.decode() if isinstance(raw, bytes | bytearray) else str(raw)
                parsed = _split_partitioned_key(prefix, key)
                if parsed is not None and parsed[1] == model_id:
                    return True, True
            if int(cursor) == 0:
                return False, True
        return False, False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _trip(self, redis: Any, model_id: str, tenant_id: Any, transition: str) -> None:
        """SET open key + REFRESH half marker + DEL probe; emit transition metric.

        Used for the HALF_OPEN re-trip path only (transition="reopened");
        the initial trip is inlined in record_failure (it also DELs the
        fails counter, which does not exist in HALF_OPEN).
        """
        open_key = _key(_PFX_OPEN, tenant_id, model_id)
        half_key = _key(_PFX_HALF, tenant_id, model_id)
        probe_key = _key(_PFX_PROBE, tenant_id, model_id)

        await redis.set(open_key, "1", ex=self._ttl_s)
        await redis.set(half_key, "1", ex=2 * self._ttl_s)
        await redis.delete(probe_key)

        self._metrics.cooldown_transitions_total.labels(model=model_id, transition=transition).inc()
