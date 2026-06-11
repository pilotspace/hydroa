"""Domain entities for the proxy guardrail framework — zero framework imports.

New entities for guardrails-core (TASK.md §3 CONTRACT):
  GuardrailResult — the result of a pre-call or post-call guardrail evaluation.
  GuardrailEvent  — a single guardrail event (one per guardrail evaluated).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GuardrailEvent:
    """A single guardrail event emitted during evaluation.

    guardrail : "prompt_injection" | "pii_mask"
    action    : "blocked" | "masked" | "audited" | "passed" | "error"
    detail    : human-readable detail string; empty string if no detail
    """

    guardrail: str
    action: str
    detail: str


@dataclass(frozen=True)
class GuardrailResult:
    """Result returned by GuardrailEvaluator.evaluate_pre().

    blocked         : True = request must be rejected (fail-CLOSED on BLOCK mode error).
    blocked_by      : name of the guardrail that triggered the block, or None.
    masked_messages : non-None when PII masking was applied; the new messages list.
    events          : zero or more events, one per guardrail evaluated.
    """

    blocked: bool
    blocked_by: str | None
    masked_messages: list[dict[str, Any]] | None
    events: list[GuardrailEvent] = field(default_factory=list)
