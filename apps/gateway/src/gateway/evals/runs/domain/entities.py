"""Value types for eval-run execution (eval-run-executor §3).

Small, immutable carriers the executor and router pass around. The persisted shapes live in
``infrastructure/orm.py``; these are the in-memory outcome of driving one case, kept free of
any SQLAlchemy / provider import so the executor's Protocol port (M8) can be exercised with a
zero-network fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: A run's status is DERIVED from its cases (M7), never written speculatively:
#:   pending   — created, not every snapshot case is terminal yet
#:   completed — every snapshot case has a terminal result row (incl. the vacuous empty run, A4)
#:   blocked   — a ZDR flip refused the run mid-flight; nothing further persisted (M5)
RunStatus = Literal["pending", "completed", "blocked"]

#: A driven case's terminal status:
#:   completed — dialed, upstream 200; carries response_text (the ZDR-gated payload) + a usage row
#:   refused   — governance denied BEFORE any dial (M3); carries a reason, NO usage row, NO payload
#:   errored   — breaker-open / per-call timeout / upstream 5xx (M4); carries a reason, fail-closed
CaseStatus = Literal["completed", "refused", "errored"]


@dataclass(frozen=True)
class CaseOutcome:
    """The result of driving ONE case — what the executor commits as an eval_case_results row."""

    status: CaseStatus
    #: The assistant text (choices[0].message.content). Present only on ``completed``.
    response_text: str | None = None
    #: A short, payload-free reason for a refused/errored case (A6). None on completed.
    reason: str | None = None
