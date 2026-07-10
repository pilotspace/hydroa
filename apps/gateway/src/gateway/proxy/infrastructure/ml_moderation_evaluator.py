"""ML moderation guardrail check — provider-backed, default-off, pre-call only.

ml-moderation-layer TASK.md §3 CONTRACT — FROZEN @ v1.

Live-verify evidence (mandatory pre-build gate, §3 least-sure flag): OpenAI's
``POST /v1/moderations`` endpoint was confirmed on 2026-07-10 to be FREE — it does
not count toward usage limits and is not billed — so this design's choice to never
emit a ``usage_record`` for a moderation call carries no silent-spend risk. Request
shape: ``{"model": "omni-moderation-latest", "input": <str | content-parts>}``.
Response shape: ``results[0].flagged: bool`` + ``results[0].categories: dict[str,
bool]`` (a category->hit map, NOT the ``list[str]`` this module's internal
``ModerationVerdict.categories`` uses — that list is an adapter-only convenience
mapping built by ``OpenAiModerationClient.moderate()``, not a wire contract).

Composition (Framing A, §1 chosen):
  ``MlModerationGuardrailEvaluator`` implements the frozen ``GuardrailEvaluator``
  Protocol (guardrails-core §3) standalone, alongside the untouched
  ``RegexGuardrailEvaluator`` — never inside it (Framing B rejected: mixing real
  network IO into a class whose frozen tests assert pure-regex/CPU-only behavior).
  ``CompositeGuardrailEvaluator`` chains the two: regex runs FIRST (cheap, already
  fail-safe) and its ``masked_messages`` feed ``ml_moderation`` so a third-party
  moderation call never sees raw PII (M7).

Tenant identity: the frozen 2-arg ``evaluate_pre(messages, guardrail_configs)`` call
  shape is NEVER widened (§0 R6) — tenant id for BYOK credential resolution flows via
  the sibling ``guardrail_tenant_context.ContextVar``, set by ``CompletionUseCase``
  immediately before each call site.

Failure handling (M6): EVERY moderation-call failure mode — missing/disabled BYOK key
  (``ProviderKeyMissing``), breaker OPEN (``CircuitOpenError``), timeout/network error
  (``UpstreamUnavailableError`` / raw ``httpx`` exceptions) — is caught INSIDE
  ``MlModerationGuardrailEvaluator.evaluate_pre`` (never raised to the use-case) and
  fires ``GuardrailEvent(action="unchecked")``. Naming failure subclasses separately
  would add branches with no behavioral difference — every one degrades identically,
  fail-open or fail-closed per the tenant's ``failure_mode`` config.

Isolation (M8, §0 R3): ``OpenAiModerationClient`` wraps a DEDICATED
  ``OpenAIDirectProvider`` instance — its own ``CircuitBreaker``, its own
  ``httpx.AsyncClient`` with a timeout tighter than the chat-completion default
  (connect=1.5s / read=2.5s vs. 10s/120s) — constructed separately from the singleton
  used for real OpenAI chat/embeddings traffic, so a moderation-provider outage can
  never trip real completions and vice versa.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.entities import GuardrailEvent, GuardrailResult
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.guardrail_tenant_context import get_guardrail_tenant_id
from gateway.proxy.domain.ports import GuardrailEvaluator, TenantCredentialResolver
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider

# ---------------------------------------------------------------------------
# OpenAI /v1/moderations wire constants (live-verified 2026-07-10 — free endpoint)
# ---------------------------------------------------------------------------
_MODERATION_MODEL = "omni-moderation-latest"
_MODERATION_PATH = "/moderations"

# ---------------------------------------------------------------------------
# Latency budget (§1 FREEZE-QUESTION 2 — decided at freeze as the starting proposal)
# ---------------------------------------------------------------------------
MODERATION_CONNECT_TIMEOUT_S = 1.5
MODERATION_READ_TIMEOUT_S = 2.5
MODERATION_MAX_RETRIES = 1
MODERATION_BACKOFF_BASE_S = 0.5
MODERATION_RETRY_DEADLINE_S = 4.0


class ModerationVerdict(TypedDict):
    """Internal, adapter-normalized moderation result — not a wire shape."""

    flagged: bool
    categories: list[str]


class ModerationProvider(Protocol):
    """Structural port: one dedicated adapter instance, one dedicated breaker."""

    async def moderate(self, text: str) -> ModerationVerdict: ...


def _concat_user_content(messages: list[dict[str, Any]]) -> str:
    """Concatenate all user-role message content into one string for classification.

    Matches RegexGuardrailEvaluator's convention (guardrail_evaluator.py) of trusting
    the frozen GuardrailEvaluator.evaluate_pre `list[dict[str, Any]]` type — no
    per-message isinstance re-check.
    """
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")


class OpenAiModerationClient:
    """``ModerationProvider`` backed by OpenAI's ``/v1/moderations`` endpoint.

    Wraps a DEDICATED ``OpenAIDirectProvider`` instance (own ``CircuitBreaker``, own
    ``httpx.AsyncClient`` — never the singleton used for real chat/embeddings) and
    drives its ``post_json_with_retry`` — the same provider-agnostic
    ``execute_with_retry`` seam every other adapter's ``complete()`` uses, exposed as
    a public method rather than reaching into ``OpenAIDirectProvider``'s private
    ``_client``/``_breaker``/``_auth_headers()`` from this separate module.

    Credential handling: the caller (``MlModerationGuardrailEvaluator``) resolves the
    tenant's ``openai`` BYOK credential and places it in the shared
    ``credential_context`` ContextVar (the SAME one ``OpenAIDirectProvider._auth_
    headers()`` reads internally) immediately before calling ``moderate()`` — this
    class never resolves credentials itself, it only sends the authenticated request.
    """

    def __init__(self, provider: OpenAIDirectProvider) -> None:
        self._provider = provider

    async def moderate(self, text: str) -> ModerationVerdict:
        payload: dict[str, Any] = {"model": _MODERATION_MODEL, "input": text}

        status, body = await self._provider.post_json_with_retry(
            _MODERATION_PATH,
            payload,
            max_retries=MODERATION_MAX_RETRIES,
            backoff_base=MODERATION_BACKOFF_BASE_S,
            deadline_s=MODERATION_RETRY_DEADLINE_S,
            provider_label="openai_moderation",
        )
        if status >= 400:
            # Any non-2xx that execute_with_retry passed through terminally (e.g. a
            # non-retryable 4xx) is a failed moderation call — never silently "passed".
            raise UpstreamUnavailableError(f"Moderation upstream returned {status}")

        results = body.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            # A 200 whose body doesn't match the wire contract (missing/malformed
            # "results") is NOT a clean verdict — it is an unparseable response and
            # must degrade exactly like a >=400 status, so MlModerationGuardrailEvaluator's
            # except-Exception path fires action="unchecked" instead of silently
            # defaulting to flagged=False (never let "unchecked" read as "passed").
            raise UpstreamUnavailableError(
                "Moderation upstream returned 200 with a malformed body (missing results)"
            )
        result = results[0]
        raw_categories = result.get("categories") or {}
        categories = [name for name, hit in raw_categories.items() if hit]
        return ModerationVerdict(flagged=bool(result.get("flagged", False)), categories=categories)


class MlModerationGuardrailEvaluator:
    """Implements ``GuardrailEvaluator`` structurally (evaluate_pre only).

    ``evaluate_post`` is a pass-through no-op — post-call moderation is explicitly
    OUT of v1 scope (§1 FREEZE-QUESTION 3, seeded as a spec delta) — so the Protocol
    is still structurally satisfied.
    """

    def __init__(
        self,
        provider: ModerationProvider,
        credential_resolver: TenantCredentialResolver | None,
    ) -> None:
        self._provider = provider
        self._resolver = credential_resolver

    async def evaluate_pre(
        self,
        messages: list[dict[str, Any]],
        guardrail_configs: dict[str, Any],
    ) -> GuardrailResult:
        """Evaluate the ml_moderation guardrail.

        Signature stays the frozen 2-arg ``GuardrailEvaluator`` shape (§0 R6) — tenant
        identity is read from the request-scoped ContextVar ``CompletionUseCase`` sets
        immediately before this call, NOT a widened parameter.

        A malformed ``ml_moderation`` block (R4: not a dict, missing keys) degrades
        the same defensively-permissive way ``prompt_injection``/``pii_mask`` already
        do — absent/non-dict/disabled all take the same "off" branch below.
        """
        cfg = guardrail_configs.get("ml_moderation")
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            # M2: zero credential resolution, zero network call, zero new event.
            return GuardrailResult(blocked=False, blocked_by=None, masked_messages=None, events=[])

        mode = cfg.get("mode", "audit")
        failure_mode = cfg.get("failure_mode", "fail_open")
        tenant_id = get_guardrail_tenant_id()

        _cred_token: Any = None
        try:
            if self._resolver is not None and tenant_id is not None:
                # Direct call — NEVER the resolve_provider_credential wrapper (which
                # converts ProviderKeyMissing to an HTTP 402 for the ROUTED model's own
                # provider). §0 R25/M6: a missing moderation key must degrade honestly,
                # not fail the whole request. The resolved credential is placed in the
                # SAME request-scoped contextvar OpenAIDirectProvider._auth_headers()
                # reads, exactly mirroring resolve_provider_credential's own set step —
                # and reset in `finally` below so it never leaks to the routed call.
                cred = await self._resolver.resolve(tenant_id, "openai")
                _cred_token = set_provider_credential(cred)
            verdict = await self._provider.moderate(_concat_user_content(messages))
        except Exception as exc:
            reason = type(exc).__name__
            event = GuardrailEvent(guardrail="ml_moderation", action="unchecked", detail=reason)
            if failure_mode == "fail_closed":
                return GuardrailResult(
                    blocked=True, blocked_by="ml_moderation", masked_messages=None, events=[event]
                )
            return GuardrailResult(
                blocked=False, blocked_by=None, masked_messages=None, events=[event]
            )
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)

        if verdict["flagged"]:
            action = "blocked" if mode == "block" else "audited"
            event = GuardrailEvent(
                guardrail="ml_moderation", action=action, detail=",".join(verdict["categories"])
            )
            return GuardrailResult(
                blocked=(mode == "block"),
                blocked_by="ml_moderation" if mode == "block" else None,
                masked_messages=None,
                events=[event],
            )
        return GuardrailResult(
            blocked=False,
            blocked_by=None,
            masked_messages=None,
            events=[GuardrailEvent(guardrail="ml_moderation", action="passed", detail="")],
        )

    async def evaluate_post(
        self, response_body: dict[str, Any], guardrail_configs: dict[str, Any]
    ) -> dict[str, Any]:
        return response_body


class CompositeGuardrailEvaluator:
    """Chains the existing ``RegexGuardrailEvaluator`` with ``MlModerationGuardrailEvaluator``.

    Regex runs FIRST — cheap, already fail-safe, and its ``masked_messages`` feed
    ``ml_moderation`` so third-party moderation calls never see raw PII (M7). Both
    checks always run (no short-circuit on a regex block) so an ml_moderation outage
    is always observable even when regex already blocked — "recorded, not silently
    dropped" per the composite-ordering scenario.
    """

    def __init__(
        self, primary: GuardrailEvaluator, ml_moderation: MlModerationGuardrailEvaluator
    ) -> None:
        self._primary = primary
        self._ml = ml_moderation

    async def evaluate_pre(
        self, messages: list[dict[str, Any]], guardrail_configs: dict[str, Any]
    ) -> GuardrailResult:
        r1 = await self._primary.evaluate_pre(messages, guardrail_configs)
        content = r1.masked_messages if r1.masked_messages is not None else messages
        r2 = await self._ml.evaluate_pre(content, guardrail_configs)
        return GuardrailResult(
            blocked=r1.blocked or r2.blocked,
            blocked_by=r1.blocked_by or r2.blocked_by,
            masked_messages=r1.masked_messages,
            events=[*r1.events, *r2.events],
        )

    async def evaluate_post(
        self, response_body: dict[str, Any], guardrail_configs: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._primary.evaluate_post(response_body, guardrail_configs)


__all__ = [
    "CompositeGuardrailEvaluator",
    "MlModerationGuardrailEvaluator",
    "ModerationProvider",
    "ModerationVerdict",
    "OpenAiModerationClient",
]
