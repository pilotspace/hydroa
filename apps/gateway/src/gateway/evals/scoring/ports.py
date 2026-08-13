"""Scorer port + result (deterministic-scorers PLAN.md §3, FROZEN @ v1, M2/M3).

The seam the run/verdict layers depend on: a pure ``score`` that never touches a DB, a
network, the clock, or randomness. ``ScoreResult`` is the shape [[eval-run-executor]] persists
per case and [[baseline-and-verdict]] aggregates (pass-count / total).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

#: The four scorer kinds that ship in R7 — a compile-time exhaustive alias, never a DB enum
#: (M2). A stored assertion may carry a kind OUTSIDE this set (eval-set-store A2 does not
#: validate the kind); the scorer maps that to a fail-closed ScoreResult (M4), it is not a type.
ScorerKind = Literal["exact", "contains", "regex", "json_schema"]


@dataclass(frozen=True)
class ScoreResult:
    """A single case's deterministic verdict.

    ``passed``  — the boolean the verdict layer counts.
    ``kind``    — the assertion.kind AS GIVEN (may be an unsupported string, for M4).
    ``detail``  — a short, human-readable, PAYLOAD-FREE reason on a fail/unscoreable; ``None``
                  on a pass (M3/A6). Never the raw output echoed back verbatim.
    """

    passed: bool
    kind: str
    detail: str | None = None


class Scorer(Protocol):
    """Maps an assertion + a model output string to a ScoreResult. Pure and total (M1)."""

    def score(self, *, assertion: Mapping[str, object], output_text: str) -> ScoreResult:
        """Return a ScoreResult for (assertion, output_text). NEVER raises (R:UNSCOREABLE_CRASH)."""
        ...
