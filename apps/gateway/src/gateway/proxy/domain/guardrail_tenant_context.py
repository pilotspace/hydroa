"""Request-scoped contextvar for the tenant identity used by pre-call guardrail checks.

ml-moderation-layer TASK.md §3 CONTRACT — FROZEN @ v1 (§0 R6).

DESIGN RATIONALE:
- The frozen ``GuardrailEvaluator.evaluate_pre(messages, guardrail_configs)`` Protocol
  (guardrails-core §3, FROZEN) is a 2-arg call shape that carries NO tenant identity.
  Widening it is out of bounds for this task — a change request against a DIFFERENT
  frozen contract. Instead, tenant identity flows out-of-band via this sibling
  ContextVar, mirroring ``credential_context.py`` (credential-resolution-seam §3,
  FROZEN @ v1) line-for-line.
- Domain module (not application or infrastructure) so BOTH layers can import without a
  layering cycle: the application use-case (``CompletionUseCase``, sets it immediately
  before each ``evaluate_pre`` call) and the infrastructure evaluator
  (``MlModerationGuardrailEvaluator``, reads it to resolve the tenant's BYOK ``openai``
  credential) share this single definition.
- ``reset_guardrail_tenant_id(token)`` MUST be called in a ``finally`` block after every
  ``evaluate_pre`` call so the tenant id does not leak across requests or async tasks.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

# ---------------------------------------------------------------------------
# Request-scoped contextvar — default None (not set)
# ---------------------------------------------------------------------------

current_guardrail_tenant_id: ContextVar[Any | None] = ContextVar(
    "current_guardrail_tenant_id",
    default=None,
)


# ---------------------------------------------------------------------------
# Public helper API — thin wrappers around the contextvar
# ---------------------------------------------------------------------------


def set_guardrail_tenant_id(tenant_id: Any) -> Token[Any | None]:
    """Set the tenant id for the current request/task scope, immediately before

    the ``evaluate_pre`` call. Returns the ``Token`` produced by ``ContextVar.set()``
    — the caller MUST pass this token to ``reset_guardrail_tenant_id()`` in a
    ``finally`` block to restore the previous value and prevent cross-request leaks.
    """
    return current_guardrail_tenant_id.set(tenant_id)


def get_guardrail_tenant_id() -> Any | None:
    """Return the tenant id currently bound to this request scope, or ``None``.

    ``MlModerationGuardrailEvaluator.evaluate_pre`` calls this to obtain the tenant
    id for BYOK credential resolution. A ``None`` return means the contextvar was
    never set for this request — the evaluator treats this the same as "no resolver
    wired" and skips credential resolution (never crashes on a missing tenant id).
    """
    return current_guardrail_tenant_id.get()


def reset_guardrail_tenant_id(token: Token[Any | None]) -> None:
    """Reset the contextvar to its previous value.

    Pass the token returned by ``set_guardrail_tenant_id()``.
    Call this in a ``finally`` block — never omit it, even on exception paths.
    """
    current_guardrail_tenant_id.reset(token)


__all__ = [
    "current_guardrail_tenant_id",
    "get_guardrail_tenant_id",
    "reset_guardrail_tenant_id",
    "set_guardrail_tenant_id",
]
