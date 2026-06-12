"""RoutingStrategy seam — selects the attempt order of a model group's deployments.

routing-strategy TASK.md §3 (FROZEN @ v1). The strategy is consulted by
FallbackModelRouter at the top of complete()/stream(); it returns a PERMUTATION of
the group's candidate model_ids (the attempt order). The v6 fallback loop then runs
over that order unchanged.

Strategies shipped here:
  - OrderedStrategy (DEFAULT): declared order → v6 byte-identical.
  - SimpleShuffleStrategy: weighted-random PRIMARY pick (P ∝ Deployment.weight), with
    the remaining candidates appended in declared order as the fallback tail.
  - LeastBusyStrategy: async aorder() picks the fewest-in-flight candidate as primary;
    sync order() returns declared order (stream path fallback).
  - LatencyStrategy: async aorder() picks the lowest-EWMA-latency candidate as primary;
    unseen deployment ⇒ 0.0 (preferred / cold-start probe); sync order() = declared order.

`order()` is SYNC (frozen). SUPERSESSION: balance-strategies adds the optional async
`aorder()` capability via the AsyncRoutingStrategy Protocol; complete() prefers aorder
when the strategy exposes it, else uses sync order(). stream() (sync call site) always
uses sync order().
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gateway.core.config import Deployment
    from gateway.proxy.domain.ports import DeploymentLoadGate


@runtime_checkable
class RoutingStrategy(Protocol):
    """Selects the attempt order for a model group's candidates."""

    def order(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        """Return a permutation of `candidates` — the order to attempt."""
        ...


class OrderedStrategy:
    """Declared-order strategy (the default) — byte-identical to v6 fallback order."""

    def order(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        return list(candidates)


class SimpleShuffleStrategy:
    """Weighted-random primary selection, remaining candidates as the fallback tail.

    The PRIMARY is drawn with probability proportional to Deployment.weight (missing
    or empty deployments ⇒ uniform weight 1). The remaining candidates keep their
    declared order. The result is a full permutation, so the fallback loop still
    covers every candidate.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()  # noqa: S311 — routing, not crypto

    def order(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        if len(candidates) <= 1:
            return list(candidates)
        weight_by_id = {d.model_id: max(1, d.weight) for d in deployments}
        weights = [weight_by_id.get(c, 1) for c in candidates]
        primary = self._rng.choices(candidates, weights=weights, k=1)[0]
        rest = [c for c in candidates if c != primary]
        return [primary, *rest]


@runtime_checkable
class AsyncRoutingStrategy(Protocol):
    """Optional async capability for load-aware routing strategies.

    balance-strategies TASK.md §3 (FROZEN @ v1).
    A strategy may implement BOTH order() (sync, stream path) and aorder()
    (async, complete path). complete() prefers aorder when isinstance check passes.
    The frozen sync RoutingStrategy.order() signature is IMMUTABLE.
    """

    async def aorder(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        """Return a PERMUTATION of `candidates` using live load metrics.

        Primary is the candidate with the best metric; ties break by declared order.
        Remaining candidates follow in declared order as the fallback tail.
        Fail-OPEN: any gate read error degrades to neutral value (0 / 0.0).
        """
        ...


class LeastBusyStrategy:
    """Load-aware strategy: primary = candidate with fewest in-flight requests.

    Implements both RoutingStrategy (sync order) and AsyncRoutingStrategy (async aorder).
    Ties broken by declared order (stable, no RNG).
    Sync order() returns declared order — stream path fallback (pre-approved freeze boundary).
    """

    def __init__(self, load_gate: DeploymentLoadGate) -> None:
        self._gate = load_gate

    def order(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        """Return declared order — sync stream path fallback."""
        return list(candidates)

    async def aorder(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        """Primary = candidate with MIN in_flight; ties → declared order (stable).

        Fail-OPEN: any gate read error treats that candidate's in_flight as 0.
        Returns full permutation: [primary, *rest-in-declared-order].
        """
        counts: list[int] = []
        for c in candidates:
            try:
                counts.append(await self._gate.in_flight(c))
            except Exception:
                counts.append(0)

        # Stable argmin: first minimum by declared order wins ties.
        min_count = min(counts)
        primary_idx = counts.index(min_count)
        primary = candidates[primary_idx]
        rest = [c for c in candidates if c != primary]
        return [primary, *rest]


class LatencyStrategy:
    """Load-aware strategy: primary = candidate with lowest EWMA latency.

    Implements both RoutingStrategy (sync order) and AsyncRoutingStrategy (async aorder).
    Unseen deployment ⇒ ewma 0.0 (preferred — cold deployments get probe traffic).
    Ties broken by declared order (stable, no RNG).
    Sync order() returns declared order — stream path fallback.
    """

    def __init__(self, load_gate: DeploymentLoadGate) -> None:
        self._gate = load_gate

    def order(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        """Return declared order — sync stream path fallback."""
        return list(candidates)

    async def aorder(
        self,
        alias: str,
        candidates: list[str],
        deployments: list[Deployment],
    ) -> list[str]:
        """Primary = candidate with MIN latency_ewma; unseen ⇒ 0.0 (preferred); ties → declared.

        Fail-OPEN: any gate read error treats that candidate's ewma as 0.0.
        Returns full permutation: [primary, *rest-in-declared-order].
        """
        ewmas: list[float] = []
        for c in candidates:
            try:
                ewmas.append(await self._gate.latency_ewma(c))
            except Exception:
                ewmas.append(0.0)

        # Stable argmin: first minimum by declared order wins ties.
        min_ewma = min(ewmas)
        primary_idx = ewmas.index(min_ewma)
        primary = candidates[primary_idx]
        rest = [c for c in candidates if c != primary]
        return [primary, *rest]


def build_strategy(
    name: str,
    load_gate: DeploymentLoadGate | None = None,
) -> RoutingStrategy:
    """Construct the configured RoutingStrategy from its name (validated at Settings).

    Additive second parameter: load_gate is required (non-None) for load-aware strategies.
    The existing 1-arg call build_strategy("ordered") still works (load_gate defaults None).

    `name` is one of the values Settings.routing_strategy already validated
    ("ordered" | "simple-shuffle" | "least-busy" | "latency"); an unexpected value falls
    back to OrderedStrategy defensively (the startup validator is the real gate).
    """
    if name == "simple-shuffle":
        return SimpleShuffleStrategy()
    if name == "least-busy":
        # load_gate is required; caller (create_app) guarantees non-None for this branch.
        assert load_gate is not None, "load_gate required for least-busy strategy"
        return LeastBusyStrategy(load_gate=load_gate)
    if name == "latency":
        assert load_gate is not None, "load_gate required for latency strategy"
        return LatencyStrategy(load_gate=load_gate)
    return OrderedStrategy()
