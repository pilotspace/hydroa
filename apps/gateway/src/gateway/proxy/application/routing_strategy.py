"""RoutingStrategy seam — selects the attempt order of a model group's deployments.

routing-strategy TASK.md §3 (FROZEN @ v1). The strategy is consulted by
FallbackModelRouter at the top of complete()/stream(); it returns a PERMUTATION of
the group's candidate model_ids (the attempt order). The v6 fallback loop then runs
over that order unchanged.

Strategies shipped here:
  - OrderedStrategy (DEFAULT): declared order → v6 byte-identical.
  - SimpleShuffleStrategy: weighted-random PRIMARY pick (P ∝ Deployment.weight), with
    the remaining candidates appended in declared order as the fallback tail.

`order()` is SYNC this task. SUPERSESSION NOTE: balance-strategies (least-busy /
latency, Redis) will supersede this to an async variant per the v6 "frozen behavioral
pin → supersession" pattern; order() is sync now because stream() is a sync method
returning an AsyncIterator and runs synchronously at a frozen call site.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from gateway.core.config import Deployment


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


def build_strategy(name: str) -> RoutingStrategy:
    """Construct the configured RoutingStrategy from its name (validated at Settings).

    `name` is one of the values Settings.routing_strategy already validated
    ("ordered" | "simple-shuffle"); an unexpected value falls back to OrderedStrategy
    defensively (the startup validator is the real gate).
    """
    if name == "simple-shuffle":
        return SimpleShuffleStrategy()
    return OrderedStrategy()
