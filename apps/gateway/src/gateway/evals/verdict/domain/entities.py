"""Domain types for baseline-and-verdict — baseline-and-verdict PLAN.md §3."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

#: A run's exact score: ``(pass_count, total)``. Kept as two integers — NEVER a float rate at
#: rest — so the verdict comparison is an exact integer cross-multiply (M2, R:FLOAT_TIE).
RunScore = tuple[int, int]


@dataclass(frozen=True)
class ScorableCase:
    """A snapshot case reduced to what scoring needs: its id (result key) + the assertion.

    A pure domain value object — the router adapts an ``EvalCaseRow`` into it, so the pure
    ``score_run`` never depends on the ORM (clean-architecture inward dependency).
    """

    id: uuid.UUID
    assertion: Mapping[str, object]


@dataclass(frozen=True)
class CaseResultView:
    """A per-case result reduced to what scoring needs: its terminal status + the payload text."""

    status: str
    response_text: str | None


#: The three verdict states. ``pass``/``fail`` come from the pure ``decide``; ``no_baseline`` is
#: the I/O-layer state when a run's set has no pinned baseline (M4, R:SILENT_PASS_NO_BASELINE).
Verdict = Literal["pass", "fail", "no_baseline"]
