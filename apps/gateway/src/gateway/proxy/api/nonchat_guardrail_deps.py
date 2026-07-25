"""Shared DI helpers wiring request-leg guardrails into the non-chat modality deps.

guardrails-nonchat-parity (audit Issue 1): embeddings / images / audio each need the SAME
guardrail evaluator + telemetry collaborators chat's get_completion_use_case resolves from
app.state. Factoring that here keeps the three deps files in lock-step and byte-identical
to chat's construction (same app.state override → default RegexGuardrailEvaluator → optional
ml-moderation composite pattern).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from gateway.proxy.infrastructure.guardrail_evaluator import RegexGuardrailEvaluator


def resolve_guardrail_evaluator(request: Request, tenant_credential_resolver: Any = None) -> Any:
    """Return the guardrail evaluator for a non-chat use-case (chat-parity construction).

    Reads app.state.guardrail_evaluator first (tests inject failing evaluators this way),
    else builds the default RegexGuardrailEvaluator, wrapping it in the ml-moderation
    composite ONLY when app.state.ml_moderation_provider is boot-wired — identical to
    deps.py::get_completion_use_case.
    """
    guardrail_evaluator = getattr(request.app.state, "guardrail_evaluator", None)
    if guardrail_evaluator is not None:
        return guardrail_evaluator
    regex_evaluator = RegexGuardrailEvaluator()
    ml_provider = getattr(request.app.state, "ml_moderation_provider", None)
    if ml_provider is None:
        return regex_evaluator
    from gateway.proxy.infrastructure.ml_moderation_evaluator import (
        CompositeGuardrailEvaluator,
        MlModerationGuardrailEvaluator,
    )

    return CompositeGuardrailEvaluator(
        regex_evaluator,
        MlModerationGuardrailEvaluator(ml_provider, tenant_credential_resolver),
    )


def resolve_guardrail_telemetry(request: Request) -> tuple[Any, Any, Any]:
    """Return (metrics_registry, guardrail_verdict_session_factory, payload_capture).

    Same app.state singletons chat uses — each None ⇒ that telemetry seam no-ops.
    """
    metrics_registry = getattr(request.app.state, "metrics_registry", None)
    verdict_session_factory = getattr(request.app.state, "sessionmaker", None)
    payload_capture = getattr(request.app.state, "payload_capture", None)
    return metrics_registry, verdict_session_factory, payload_capture
