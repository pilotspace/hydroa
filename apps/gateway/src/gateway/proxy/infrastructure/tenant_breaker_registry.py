"""THE one per-tenant CircuitBreaker registry for the whole proxy (S1/S2/S4).

WHY THIS MODULE EXISTS
----------------------
Every proxy upstream adapter used to hold ONE process-wide ``CircuitBreaker``
built in its ``__init__`` and constructed ONCE in ``create_app()``, while its
per-request BYOK credential arrived from a contextvar. Tenant A's own upstream
5xx/timeouts therefore tripped a breaker that then refused EVERY OTHER tenant on
that provider — a cross-tenant denial of service in a product whose entire value
proposition is per-tenant isolation.

That defect class has been HARD-STOPPED at a verify gate THREE times
(residency-service-tiers, api-surface-parity R4, moderations CR-1). The remedy on
record is always the same: partition the breaker by tenant, never a module-level
singleton and never a dict keyed by provider alone. This module is that remedy,
promoted out of ``ml_moderation_evaluator._TenantBreakerRegistry`` (where CR-1
first shipped it, privately) so there is EXACTLY ONE implementation in the tree.
Two copies is how the two halves drift apart, and a redundant duplicate would
mask the structural guard that keeps the class closed.

ISOLATION IS ACHIEVED BY PARTITIONING THE KEY, NEVER BY WEAKENING PROTECTION.
The threshold, the cooldown and the state machine are untouched: a tenant that
is genuinely failing is still short-circuited — in its OWN partition.
"""

from __future__ import annotations

import sys
import types
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Final

from gateway.proxy.domain.credential_context import current_credential_tenant
from gateway.proxy.domain.guardrail_tenant_context import get_guardrail_tenant_id
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

#: Bounded registry size (M5 / R:UNBOUNDED). Tenant count is unbounded AND
#: attacker-influenceable — Hydroa has self-serve signup and a free tier — so an
#: uncapped dict keyed by tenant would trade a cross-tenant DoS for a
#: memory-exhaustion DoS, which is not a fix. 4096 is comfortably above any
#: realistic CONCURRENTLY-ACTIVE tenant count on a single replica, and each entry
#: is a tiny CircuitBreaker (a handful of ints/enum/None fields), so the whole
#: registry at capacity is a trivial footprint. Not a business limit — purely a
#: "never grow unbounded" safety cap.
MAX_TENANT_BREAKERS: Final[int] = 4096

#: The key segment the sentinel partition renders as in a Redis cooldown key.
#: Contains no ``:`` so it can never split into extra key segments, and no real
#: tenant id (a UUID) can ever equal it.
UNATTRIBUTED_SEGMENT: Final[str] = "_unattributed"


class _UnattributedTenant:
    """The ONE reserved partition for calls with no resolvable tenant (A16/A17).

    A call that reaches a breaker with no tenant in context must not share a
    bucket with a real tenant — that is the same cross-tenant coupling wearing a
    different key (R:SHARED_BUCKET). It must equally not get a FRESH breaker per
    call, which would remove upstream protection entirely and hammer a failing
    provider without bound (R:WEAKEN). So: one reserved sentinel bucket, shared
    only among unattributed calls.

    This bucket is REACHABLE, not defensive. ``_auth_headers`` raises
    ProviderKeyMissing only INSIDE the request — i.e. AFTER ``guard()`` has
    already run — so the tempting invariant "no tenant in context ⇒ no credential
    ⇒ never dials" is FALSE.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<unattributed-tenant>"

    def __str__(self) -> str:
        return UNATTRIBUTED_SEGMENT


#: Module-level singleton so identity comparison is stable across every call site.
UNATTRIBUTED_TENANT: Final[_UnattributedTenant] = _UnattributedTenant()


def breaker_tenant_key() -> object:
    """Resolve the tenant key a breaker lookup is scoped by (S2).

    Precedence — credential tenant, THEN guardrail tenant, THEN the sentinel (M13):

    1. ``current_credential_tenant`` — set at the single credential-resolution
       seam by ``set_provider_credential(cred, tenant_id)``. This is the same
       channel that already supplies these adapters their per-request credential,
       and the same channel a prior cross-tenant SECURITY fix used (the vertex
       BYOK token-cache confused-deputy).
    2. ``guardrail_tenant_context`` — the moderation seam is ALREADY per-tenant
       but keys off the guardrail tenant. Reading only the credential contextvar
       would collapse every tenant's moderation traffic into the sentinel bucket
       and silently undo the CR-1 HARD-STOP fix.
    3. The sentinel — and only then.

    Read EAGERLY, at the instant the breaker is resolved for the guard, never
    lazily inside a generator body (A10): ``stream()`` guards eagerly but yields
    lazily, and the credential contextvar is reset by its own ``finally`` before
    deferred work runs, so a lazily-read tenant would be correct at guard time
    and wrong at record time — splitting ONE request across two failure domains.
    """
    tenant = current_credential_tenant.get()
    if tenant is not None:
        return tenant
    guardrail_tenant = get_guardrail_tenant_id()
    if guardrail_tenant is not None:
        return guardrail_tenant
    return UNATTRIBUTED_TENANT


def tenant_key_segment(tenant_key: object) -> str:
    """Render a tenant key as ONE safe Redis key segment.

    ``:`` is the key separator, so a segment containing one would let a crafted
    value address a neighbouring partition. Tenant keys are UUIDs or the
    sentinel and cannot contain ``:`` today; this makes that a property of the
    code rather than of the current call sites.

    REJECT, never rewrite (D4). The earlier form returned
    ``str(tenant_key).replace(":", "_")``, which is NOT injective: ``"a:b"`` and
    ``"a_b"`` both render to ``"a_b"`` and would share one partition — the exact
    cross-tenant coupling this module exists to close, reintroduced by the
    sanitiser itself. A tenant key containing ``:`` cannot arise from any current
    caller (UUIDs, or a guardrail tenant id which is also a tenant id), so it is a
    programming error, not user input: fail loudly on the ONE request rather than
    silently route it into a neighbour's bucket.

    The sentinel is matched by IDENTITY, not by its rendering, so a real tenant
    that merely *stringifies* to the sentinel segment can never join the
    unattributed partition (R:SHARED_BUCKET).
    """
    if tenant_key is None or tenant_key is UNATTRIBUTED_TENANT:
        return UNATTRIBUTED_SEGMENT
    segment = str(tenant_key)
    if ":" in segment:
        raise ValueError(
            "tenant key renders to a segment containing the Redis key separator ':' "
            f"({segment!r}); rewriting it would collide two distinct tenants on one "
            "partition, so the key is refused instead."
        )
    if segment == UNATTRIBUTED_SEGMENT:
        raise ValueError(
            f"tenant key renders to the reserved unattributed segment {segment!r}; "
            "a real tenant must never share the sentinel partition (R:SHARED_BUCKET)."
        )
    return segment


class TenantScopedBreakerRegistry:
    """Bounded LRU registry of per-tenant CircuitBreaker instances (S1).

    Eviction policy — LRU (move-to-MRU on EVERY access, hit or miss) rather than
    a size-capped dict with FIFO/arbitrary eviction. This is load-bearing, not a
    micro-optimisation: LRU's eviction victim is ALWAYS the least-recently-used
    entry, so a HOT tenant — one that keeps driving traffic through this seam —
    is by construction touched on every call and can never become the victim.
    Its OPEN breaker therefore survives an insertion burst far past the cap.
    A FIFO or random-eviction cap could not make that guarantee: a hot tenant
    could be evicted by insertion-order bad luck, resetting its OPEN breaker to
    CLOSED and removing exactly the upstream protection the breaker exists to
    provide (R:HOT_EVICT — trading a cross-tenant DoS for a lost circuit).

    Trade-off accepted: a COLD tenant whose breaker happened to be OPEN loses
    that state on eviction — its next call starts a fresh CLOSED breaker. That is
    an honest, bounded degrade (a stale circuit re-probing after being idle long
    enough to fall out of a 4096-entry LRU is reasonable), never a fabricated
    result, and never observable for the tenant the isolation protects.

    Backing store: an ``OrderedDict`` by default, but any ``MutableMapping`` may
    be supplied so a caller can keep an EXISTING stable dict as the store (the
    ``deps.py`` per-(tenant, provider) registry keeps ``app.state.provider_
    circuit_breakers`` as its store, so breaker state survives across requests
    while the cap still binds). The move-to-MRU idiom below is delete-then-
    reinsert, which is O(1) and correct on a plain ``dict`` as well as an
    ``OrderedDict`` — never ``move_to_end``, which a plain dict does not have.

    Concurrency: every method here is SYNCHRONOUS (no ``await`` anywhere), so
    under asyncio's cooperative scheduling a get-or-create is atomic within one
    event-loop tick. Two concurrent coroutines resolving the SAME new tenant's
    first call can never race into creating two different breaker objects, and
    no lock is needed on the hot path.
    """

    def __init__(
        self,
        max_size: int = MAX_TENANT_BREAKERS,
        *,
        store: MutableMapping[Any, CircuitBreaker] | None = None,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._breakers: MutableMapping[Any, CircuitBreaker] = (
            OrderedDict() if store is None else store
        )

    def get_or_create(self, tenant_key: Any) -> CircuitBreaker:
        """Return this tenant's breaker, creating one lazily on first use (A11).

        Lazily, never pre-seeded per tenant: pre-seeding would make the registry
        grow with the TENANT TABLE rather than with active traffic.

        Every call — hit or miss — moves the entry to the MRU end, so a tenant
        making back-to-back calls never ages toward eviction.
        """
        existing = self._breakers.get(tenant_key)
        if existing is not None:
            # Move to MRU. delete-then-reinsert works on dict AND OrderedDict.
            del self._breakers[tenant_key]
            self._breakers[tenant_key] = existing
            return existing

        breaker = CircuitBreaker()
        self._breakers[tenant_key] = breaker
        while len(self._breakers) > self._max_size:
            # Evict strictly the least-recently-used entry — never the newcomer,
            # never a hot tenant (see the class docstring's R:HOT_EVICT note).
            del self._breakers[next(iter(self._breakers))]
        return breaker

    def __len__(self) -> int:
        return len(self._breakers)

    def live_breakers(self) -> list[tuple[Any, CircuitBreaker]]:
        """Snapshot of the ``(tenant_key, breaker)`` pairs held right now.

        Read-only and non-mutating — in particular it does NOT touch LRU order,
        so inspecting the registry (the boot census, the Prometheus gauge) can
        never change which entry is next to be evicted. An observer that
        reordered the LRU would make a hot tenant's survival depend on whether
        anyone happened to be scraping.

        Returns a list, not a view: the caller may iterate it across arbitrary
        code without risking "dict changed size during iteration".
        """
        return list(self._breakers.items())


class TenantScopedBreakerMixin:
    """The single breaker accessor every proxy upstream adapter inherits (S4).

    Replaces each adapter's own ``self._breaker = CircuitBreaker()``. One
    registry PER ADAPTER INSTANCE (A6), so anthropic's failures never touch
    openrouter's — this preserves the per-provider isolation ``deps.py`` already
    won (audit-remediation C1) and adds the tenant dimension UNDERNEATH it.

    Call ``_breaker_for()`` ONCE per call, at guard time, and close over the
    returned object for anything that runs later (streaming generator bodies,
    long-lived pumps). Re-reading it inside a deferred body would resolve a
    DIFFERENT tenant — see ``breaker_tenant_key``'s A10 note.
    """

    #: Class-level default so an instance built via ``__new__`` — a construction
    #: shape this codebase's test doubles use deliberately, and which therefore
    #: SKIPS ``__init__`` — still resolves a registry instead of raising
    #: AttributeError deep inside a guard. Mirrors the ``_usage_accounting``
    #: class-level-default convention already used by these adapters. The lazy
    #: creation in ``_breaker_for`` below keeps this per-INSTANCE: it must never
    #: become a shared class-level registry, which would silently re-couple every
    #: adapter instance into one failure domain.
    _tenant_breakers: TenantScopedBreakerRegistry | None = None

    def _init_tenant_breakers(
        self,
        registry: TenantScopedBreakerRegistry | None = None,
        *,
        max_size: int = MAX_TENANT_BREAKERS,
    ) -> None:
        """Install this instance's registry. Call from ``__init__``.

        Pass ``registry`` to SHARE an existing one (the realtime path and the
        ``deps.py`` fail-safe both share the per-app registry); omit it for the
        usual one-registry-per-adapter-instance shape.
        """
        self._tenant_breakers = (
            registry if registry is not None else TenantScopedBreakerRegistry(max_size)
        )

    def _breaker_for(self) -> CircuitBreaker:
        """Resolve THIS call's per-tenant breaker.

        Creates this instance's registry on first use if ``__init__`` never ran
        (see the class-level default above). Assigning to ``self`` — never to the
        class — keeps one registry per adapter INSTANCE (A6), so anthropic's
        failures still never touch openrouter's.
        """
        return self._breaker_registry().get_or_create(breaker_tenant_key())

    def _breaker_registry(self) -> TenantScopedBreakerRegistry:
        """This instance's registry, created on first use if __init__ never ran."""
        registry = self._tenant_breakers
        if registry is None:
            registry = TenantScopedBreakerRegistry()
            self._tenant_breakers = registry
        return registry


def registry_for_state(state: Any, attr: str = "tenant_breakers") -> TenantScopedBreakerRegistry:
    """Return the per-app shared registry, creating and caching it on first use.

    Used by the seams that have no adapter instance of their own to hang a
    registry off — the ``deps.py`` fail-safe (A29: a stripped ``app.state`` must
    NOT silently restore the process-wide breaker this task removes) and the
    realtime websocket chat turn (M8).

    Lazily attached rather than boot-wired so a stripped-down test double or any
    app built without the full ``create_app()`` wiring still gets a tenant-scoped
    registry instead of falling back to a shared breaker. getattr→setattr with no
    ``await`` between them is atomic under asyncio, so concurrent requests cannot
    race into two registries.
    """
    registry = getattr(state, attr, None)
    if not isinstance(registry, TenantScopedBreakerRegistry):
        registry = TenantScopedBreakerRegistry()
        setattr(state, attr, registry)
    return registry


# ---------------------------------------------------------------------------
# Live-object-graph census (S13/S14) — ONE population, two consumers.
# ---------------------------------------------------------------------------

#: Types the walk treats as leaves. Recursing into these either cannot reach a
#: breaker or would wander out of the app graph and into the interpreter.
_OPAQUE_LEAVES: Final[tuple[type, ...]] = (
    str,
    bytes,
    bytearray,
    int,
    float,
    bool,
    complex,
    type(None),
    type,
    types.ModuleType,
    types.BuiltinFunctionType,
)

#: Hard bound on the traversal. The real booted app graph is ~800 nodes plus one
#: node per live breaker, so this is orders of magnitude of headroom — it exists
#: only so a pathological cycle-free-but-huge graph terminates rather than
#: stalling a metrics scrape. `truncated` is reported, never swallowed.
MAX_CENSUS_NODES: Final[int] = 200_000


@dataclass(frozen=True)
class LiveBreaker:
    """One live ``CircuitBreaker`` found in the object graph.

    ``owner`` is the ``TenantScopedBreakerRegistry`` that holds it, or ``None``
    when the breaker is loose in the graph — i.e. some object constructed one
    directly instead of resolving it per tenant, which is a shared failure
    domain (R:GLOBAL_BREAKER).
    """

    path: str
    breaker: CircuitBreaker
    owner: TenantScopedBreakerRegistry | None


@dataclass(frozen=True)
class LiveBreakerScan:
    """Result of one census walk: what was found, and whether the walk was honest."""

    breakers: tuple[LiveBreaker, ...]
    nodes_visited: int
    truncated: bool
    #: How many distinct registries the traversal reached. This is the walker's
    #: health signal that SURVIVES a correct fix: every adapter owns one, so the
    #: count grows with the tree and never empties — unlike "how many unowned
    #: breakers did you find", which a correct fix drives to zero.
    registries_visited: int = 0

    @property
    def unowned(self) -> tuple[LiveBreaker, ...]:
        return tuple(b for b in self.breakers if b.owner is None)


#: Memo of top-level package name -> "is this an installed dependency?".
_THIRD_PARTY_ROOT: dict[str, bool] = {}


def _is_first_party_callable(func: Any) -> bool:
    """Is this callable OUR code, rather than an installed dependency's?

    The census follows a callable's captures (closure cells, a bound method's
    ``__self__``) because a breaker can live there and be named by no attribute
    anywhere. Following EVERY callable, though, walks into SQLAlchemy/pydantic
    internals and turns a ~1.8k-node app graph into ~90k — a cost this same walk
    would pay on every Prometheus scrape, blocking the event loop.

    First-party is decided by where the top-level package lives (site-packages or
    not), memoised per package: a rule about provenance, not a hardcoded list of
    names, so it holds for first-party test doubles and future packages alike
    without this module knowing anything about them.
    """
    module_name = getattr(func, "__module__", None)
    if not isinstance(module_name, str) or not module_name:
        return False
    root = module_name.split(".", 1)[0]

    cached = _THIRD_PARTY_ROOT.get(root)
    if cached is None:
        origin = getattr(getattr(sys.modules.get(root), "__spec__", None), "origin", None)
        cached = isinstance(origin, str) and (
            "site-packages" in origin or "dist-packages" in origin
        )
        _THIRD_PARTY_ROOT[root] = cached
    return not cached


def _instance_dict(obj: Any) -> dict[str, Any] | None:
    """This object's own ``__dict__``, or None — WITHOUT running its code.

    ``object.__getattribute__`` bypasses any ``__getattribute__`` / ``__getattr__``
    the class defines, so reading the instance dict cannot dispatch into
    application or third-party code. That matters: this walk runs on every
    metrics scrape, over a graph full of SQLAlchemy/httpx/redis objects, and a
    census must INSPECT the program, never make it do work.
    """
    try:
        namespace = object.__getattribute__(obj, "__dict__")
    except Exception:
        return None
    return namespace if isinstance(namespace, dict) else None


def _slot_values(obj: Any) -> list[tuple[str, Any]]:
    """Every ``__slots__`` value this object holds — again, running no code.

    A slot is a ``member_descriptor``: reading it through ``__get__`` is a raw
    C-level storage read with no user code in the path. Iterating the MRO for
    member descriptors (rather than parsing ``__slots__`` and calling ``getattr``)
    also means properties, cached_properties and memoised descriptors are SKIPPED
    by construction — which is the point. One SQLAlchemy memoised slot raises
    ``TypeError`` from a weakref when read with plain ``getattr``; that is the
    class of surprise a census must not be able to trigger at all.

    An unset slot raises AttributeError and is simply absent from the result.
    """
    values: list[tuple[str, Any]] = []
    for klass in type(obj).__mro__:
        for name, descriptor in klass.__dict__.items():
            if isinstance(descriptor, types.MemberDescriptorType):
                try:
                    values.append((name, descriptor.__get__(obj)))
                except AttributeError:
                    continue
    return values


def iter_live_breakers(
    root: Any,
    *,
    root_path: str = "app.state",
    max_nodes: int = MAX_CENSUS_NODES,
) -> LiveBreakerScan:
    """Walk the LIVE OBJECT GRAPH from ``root`` and return every CircuitBreaker.

    This is the population the boot census (S13) asserts over and the population
    the Prometheus gauge (S14) aggregates — deliberately the SAME function, so
    the two can never disagree about which breakers exist (A33).

    The predicate is ``isinstance``, NOT an AST rule. That is the whole point of
    M12: the static census matches ``CircuitBreaker(`` in source, so an import
    alias, ``_CLS = CircuitBreaker``, a module-level default argument,
    ``functools.partial(CircuitBreaker)`` or a subclass all produce a tenant-blind
    breaker the source-level rule cannot see. ``isinstance`` is invisible to every
    one of those shapes, which is exactly why the two guards complement each other
    and neither alone is sufficient.

    Ownership: a breaker reached THROUGH a ``TenantScopedBreakerRegistry`` is
    owned by it. A breaker reached any other way is loose — one shared failure
    domain — and is reported with ``owner=None``.

    REACH — what this walk does and does not see. It follows mappings, sequences,
    instance ``__dict__``, ``__slots__`` storage, CLOSURE CELLS and a bound
    method's ``__self__``. The last two are not decoration: ``main.py`` wires the
    eval executor through closures over ``app.state``, so a breaker captured in a
    cell is a shape that exists in this codebase, and a walk that skipped
    functions would report a clean fleet over it. Deliberately NOT followed:
    ``__globals__`` (that is the whole interpreter, not the app) and anything
    reachable only through a C extension's opaque state.

    DEDUPE — ``seen`` is keyed by ``id()``, so ONE object reached by N references
    is reported ONCE, at whichever path is popped first. Callers must not read
    "one entry" as "one reference": to prove a particular object has no OTHER
    live reference, detach the known one and re-walk.

    Concurrency: the walk contains NO ``await``, so under asyncio's cooperative
    scheduling it is atomic with respect to the request paths that create
    breakers. It can therefore never observe a half-mutated registry, and the
    ``list()`` snapshots below can never raise "dict changed size".

    Bounded and total: it terminates at ``max_nodes`` and reports ``truncated``
    rather than raising or silently returning a short answer — a census that
    quietly inspects part of the graph and reports a clean result is the
    masked-gate failure mode this task exists to close.
    """
    breakers: list[LiveBreaker] = []
    seen: set[int] = set()
    registries: set[int] = set()
    stack: list[tuple[Any, str]] = [(root, root_path)]
    nodes = 0
    truncated = False

    while stack:
        obj, path = stack.pop()
        obj_id = id(obj)
        if obj_id in seen:
            continue
        seen.add(obj_id)

        nodes += 1
        if nodes > max_nodes:
            truncated = True
            break

        if isinstance(obj, CircuitBreaker):
            breakers.append(LiveBreaker(path, obj, None))
            continue

        if isinstance(obj, TenantScopedBreakerRegistry):
            registries.add(obj_id)
            for key, breaker in obj.live_breakers():
                if id(breaker) in seen:
                    continue
                seen.add(id(breaker))
                breakers.append(LiveBreaker(f"{path}[{key!r}]", breaker, obj))
            continue

        if isinstance(obj, _OPAQUE_LEAVES):
            continue

        # A bound method carries its instance; a closure carries its cells. Both
        # can hold a breaker that no attribute anywhere names — main.py wires the
        # eval executor exactly that way — so a walk that skipped callables would
        # report a clean fleet over a captured process-wide breaker.
        #
        # Only OUR callables are followed (`_is_first_party_callable`). Following every
        # third-party bound method turns an ~1.8k-node app graph into a ~90k-node
        # walk through SQLAlchemy/pydantic internals: measured 120ms per pass,
        # which this same walk would then spend BLOCKING THE EVENT LOOP on every
        # Prometheus scrape. The bound is honest and narrow: a breaker this
        # codebase creates is captured by a closure this codebase wrote. A breaker
        # reachable ONLY through a third-party library's internal callable is
        # outside this census's reach — declared, not silently assumed away.
        # __globals__ is never walked either: that is the interpreter, not the app.
        if isinstance(obj, types.MethodType):
            if _is_first_party_callable(obj.__func__):
                stack.append((obj.__self__, f"{path}.__self__"))
            continue

        if isinstance(obj, types.FunctionType):
            if _is_first_party_callable(obj):
                for index, cell in enumerate(obj.__closure__ or ()):
                    try:
                        stack.append((cell.cell_contents, f"{path}.__closure__[{index}]"))
                    except ValueError:
                        continue  # an empty cell (recursive closure not yet bound)
            continue

        if isinstance(obj, Mapping):
            for key, value in list(obj.items()):
                stack.append((value, f"{path}[{key!r}]"))
            continue

        if isinstance(obj, list | tuple | set | frozenset):
            for index, value in enumerate(list(obj)):
                stack.append((value, f"{path}[{index}]"))
            continue

        instance_dict = _instance_dict(obj)
        if instance_dict is not None:
            for key, value in list(instance_dict.items()):
                stack.append((value, f"{path}.{key}"))
        for slot, slot_value in _slot_values(obj):
            stack.append((slot_value, f"{path}.{slot}"))

    return LiveBreakerScan(tuple(breakers), nodes, truncated, len(registries))


__all__ = [
    "MAX_CENSUS_NODES",
    "MAX_TENANT_BREAKERS",
    "UNATTRIBUTED_SEGMENT",
    "UNATTRIBUTED_TENANT",
    "LiveBreaker",
    "LiveBreakerScan",
    "TenantScopedBreakerMixin",
    "TenantScopedBreakerRegistry",
    "breaker_tenant_key",
    "iter_live_breakers",
    "registry_for_state",
    "tenant_key_segment",
]
