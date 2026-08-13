"""The PURE scoring + verdict core — baseline-and-verdict §3 (M1, M2).

Two pure total functions, no IO / clock / randomness / DB:

  ``score_run`` — a run's exact ``(pass_count, total)`` re-derived from its launch snapshot and
  per-case results (M1). ``total`` is the snapshot size (the fail-closed denominator, A2); a
  case is a pass ONLY if its result ``status == "completed"`` AND the pure scorer passes it, so
  a ``refused``/``errored``/``pending``/absent/unscoreable case is counted but never a pass.

  ``decide`` — the verdict, by EXACT integer cross-multiplication (M2, R:FLOAT_TIE): a candidate
  PASSES iff ``pass_c * total_b >= pass_b * total_c``. ``>=`` decides an equal-as-rationals
  boundary in the candidate's favor (A3); the comparison never divides, so IEEE rounding cannot
  flip it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Literal

from gateway.evals.scoring.ports import Scorer
from gateway.evals.verdict.domain.entities import CaseResultView, RunScore, ScorableCase


def score_run(
    snapshot_cases: Sequence[ScorableCase],
    results_by_case_id: Mapping[uuid.UUID, CaseResultView],
    scorer: Scorer,
) -> RunScore:
    """Re-derive a run's exact ``(pass_count, total)`` — pure, fail-closed (M1, A2, A4)."""
    total = len(snapshot_cases)
    passed = 0
    for case in snapshot_cases:
        result = results_by_case_id.get(case.id)
        # Fail-closed: a case that did not complete (refused/errored/pending/absent) is in the
        # denominator but is NEVER a pass — a run cannot score as if it answered a case it didn't.
        if result is None or result.status != "completed":
            continue
        output_text = result.response_text or ""
        if scorer.score(assertion=case.assertion, output_text=output_text).passed:
            passed += 1
    return passed, total


def decide(candidate: RunScore, baseline: RunScore) -> Literal["pass", "fail"]:
    """PASS iff the candidate rate is >= the baseline rate, by EXACT integer cross-multiply.

    ``pass_c / total_c >= pass_b / total_b`` is evaluated as ``pass_c * total_b >= pass_b *
    total_c`` — integers only, so 3/5 and 6/10 compare exactly equal (30 >= 30 → PASS) and no
    float last-bit can decide the boundary (M2, A3, R:FLOAT_TIE). A degenerate empty baseline
    (``total_b == 0``) yields ``pass_c * 0 >= 0`` → ``0 >= 0`` → PASS for any candidate (A4).
    """
    pass_c, total_c = candidate
    pass_b, total_b = baseline
    return "pass" if pass_c * total_b >= pass_b * total_c else "fail"
