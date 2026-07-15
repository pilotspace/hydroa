"""Request-leg guardrail evaluation for the non-chat modalities (embeddings, images, TTS).

guardrails-nonchat-parity (audit Issue 1): chat's ``complete()``/``stream()`` run every
tenant guardrail (prompt-injection block, ml-moderation block, ``pii_mask``) over the
request BEFORE upstream; the embeddings / images / audio pipelines never did — so a
tenant's block/mask policy was SILENTLY BYPASSED on those endpoints. This module factors
chat's request-leg block (``use_cases.py`` ~2631-2773) into ONE reusable coroutine the
non-chat use-cases call right after ``governance.authorize()``:

  * builds a synthetic chat ``messages`` list from the modality's request text(s),
  * runs ``evaluate_pre`` under the guardrail tenant context (BYOK ml-moderation parity),
  * fires guardrail metrics + verdict-analytics dispatch (same fire-and-forget seams),
  * on a BLOCK-mode hit records the blocked usage + capture then raises GUARDRAIL_BLOCKED
    (refused ⇒ never billed for upstream, but the block IS recorded, exactly like chat),
  * on a MASK-mode hit returns the masked text(s) + ``pii_masked=True``,
  * fail-CLOSED on an evaluator exception when any block-mode guardrail is configured,
    otherwise fail-OPEN (proceed unmasked) — identical to chat's error handling.

Fast path: returns ``(texts, False)`` unchanged with ZERO evaluator work when the
evaluator is unwired or the tenant has no guardrail configs, so every non-guardrail
request on these endpoints stays byte-identical.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from gateway.core.error_catalog import GUARDRAIL_BLOCKED
from gateway.keys.domain.entities import AuthzResult
from gateway.proxy.domain.guardrail_tenant_context import (
    reset_guardrail_tenant_id,
    set_guardrail_tenant_id,
)
from gateway.proxy.domain.ports import PayloadCapturePort, UsageRecorder

# use_cases.py is INVIOLABLE (must stay byte-identical), so these guardrail telemetry
# helpers cannot be made public there; reusing the EXACT same fns is what keeps this
# request-leg block behaviourally identical to chat's — hence the reportPrivateUsage
# suppressions (the same pattern the modality use-cases already use for the recorder).
from gateway.proxy.application.use_cases import (  # isort: skip
    _dispatch_capture,  # pyright: ignore[reportPrivateUsage]
    _dispatch_guardrail_verdicts,  # pyright: ignore[reportPrivateUsage]
    _fire_guardrail_metrics,  # pyright: ignore[reportPrivateUsage]
    _fire_record_with_raw,  # pyright: ignore[reportPrivateUsage]
    _make_error_event,  # pyright: ignore[reportPrivateUsage]
)

_log = logging.getLogger(__name__)


def _has_block_mode(guardrail_configs: dict[str, Any]) -> bool:
    """True when any configured guardrail is in block mode (drives fail-CLOSED)."""
    return any(
        isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("mode") == "block"
        for cfg in guardrail_configs.values()
    )


def _record_and_capture_block(
    *,
    authz: AuthzResult,
    model_id: str,
    blocked_by: str | None,
    usage_recorder: UsageRecorder,
    payload_capture: PayloadCapturePort | None,
    request_body: dict[str, Any],
    guardrail_configs: dict[str, Any],
    tags: dict[str, str] | None,
    request_id: uuid.UUID | None,
) -> None:
    """Record the blocked request + fire the capture hook (status=400, no upstream call).

    Mirrors chat's pre-call guardrail-BLOCK bookkeeping: usage is recorded with
    guardrail_blocked=True/blocked_by and NO usage dict (nothing was consumed upstream);
    capture stores request_body with response_body=None. Both are fire-and-forget.
    """
    _fire_record_with_raw(
        usage_recorder,
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        model=model_id,
        usage=None,
        status=400,
        team_id=getattr(authz, "team_id", None),
        agent_principal_id=getattr(authz, "agent_principal_id", None),
        guardrail_blocked=True,
        blocked_by=blocked_by,
        request_id=request_id,
        tags=tags,
    )
    _dispatch_capture(
        payload_capture,
        enabled=getattr(authz, "payload_capture_enabled", False),
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        model=model_id,
        request_body=request_body,
        response_body=None,
        status=400,
        stream=False,
        cached=False,
        guardrail_configs=guardrail_configs,
        usage=None,
        latency_ms=None,
        request_id=request_id,
    )


async def evaluate_nonchat_request_guardrails(
    *,
    guardrail_evaluator: Any,
    authz: AuthzResult,
    texts: list[str],
    model_id: str,
    usage_recorder: UsageRecorder,
    request_body: dict[str, Any],
    metrics_registry: Any = None,
    verdict_session_factory: Any = None,
    payload_capture: PayloadCapturePort | None = None,
    tags: dict[str, str] | None = None,
    request_id: uuid.UUID | None = None,
) -> tuple[list[str], bool]:
    """Run the tenant's request-leg guardrails over ``texts``.

    Returns ``(masked_texts, pii_masked)``: the (possibly masked) texts in the SAME order
    and length as the input, and whether any masking fired. Raises
    ``ProblemError`` (400 ERR_GUARDRAIL_BLOCKED) on a block-mode hit, AFTER recording the
    blocked usage + capture. No-op fast path (``(texts, False)``) when the evaluator is
    unwired, the tenant has no guardrail configs, or there is no text to evaluate.
    """
    guardrail_configs = getattr(authz, "guardrail_configs", None) or {}
    if (
        guardrail_evaluator is None
        or not guardrail_configs
        or not texts
        or not hasattr(guardrail_evaluator, "evaluate_pre")
    ):
        return texts, False

    policy_source = getattr(authz, "guardrail_policy_source", None) or "none"
    messages = [{"role": "user", "content": t} for t in texts]

    # Set the guardrail tenant context so a BYOK ml-moderation provider resolves THIS
    # tenant's credential (parity with chat); reset in finally so it never leaks.
    _gtid_token = set_guardrail_tenant_id(authz.tenant_id)
    try:
        result = await guardrail_evaluator.evaluate_pre(messages, guardrail_configs)
    except Exception as _exc:
        # Evaluator itself raised: apply fail-CLOSED/fail-OPEN at this layer (chat parity).
        _log.warning("nonchat guardrail evaluate_pre raised unexpectedly", exc_info=_exc)
        err_events = [_make_error_event(guardrail_configs)]
        _fire_guardrail_metrics(metrics_registry, err_events, guardrail_configs)
        _dispatch_guardrail_verdicts(
            verdict_session_factory,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            team_id=getattr(authz, "team_id", None),
            policy_source=policy_source,
            events=err_events,
        )
        if _has_block_mode(guardrail_configs):
            _record_and_capture_block(
                authz=authz,
                model_id=model_id,
                blocked_by="error",
                usage_recorder=usage_recorder,
                payload_capture=payload_capture,
                request_body=request_body,
                guardrail_configs=guardrail_configs,
                tags=tags,
                request_id=request_id,
            )
            raise GUARDRAIL_BLOCKED.exc() from None
        # Fail-OPEN: mask/audit-only policy — proceed with the original text.
        return texts, False
    finally:
        reset_guardrail_tenant_id(_gtid_token)

    _fire_guardrail_metrics(metrics_registry, result.events, guardrail_configs)
    _dispatch_guardrail_verdicts(
        verdict_session_factory,
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        team_id=getattr(authz, "team_id", None),
        policy_source=policy_source,
        events=result.events,
    )
    if result.blocked:
        _record_and_capture_block(
            authz=authz,
            model_id=model_id,
            blocked_by=result.blocked_by,
            usage_recorder=usage_recorder,
            payload_capture=payload_capture,
            request_body=request_body,
            guardrail_configs=guardrail_configs,
            tags=tags,
            request_id=request_id,
        )
        raise GUARDRAIL_BLOCKED.exc()

    if result.masked_messages is not None:
        masked = result.masked_messages
        # The masker preserves order+length; guard defensively — a shape disagreement
        # means we cannot map masked text back, so proceed with the original (mask is
        # audit-only, never blocks) rather than mis-pairing content across inputs.
        if len(masked) == len(texts):
            masked_texts = [
                m["content"]
                if isinstance(m, dict) and isinstance(m.get("content"), str)
                else texts[i]
                for i, m in enumerate(masked)
            ]
            return masked_texts, True
        _log.warning(
            "nonchat guardrail mask shape mismatch (%d masked vs %d inputs); proceeding unmasked",
            len(masked),
            len(texts),
        )
    return texts, False
