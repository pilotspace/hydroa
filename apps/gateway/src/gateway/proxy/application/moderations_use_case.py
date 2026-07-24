"""ModerationsUseCase — POST /v1/moderations application layer.

Contract FROZEN @ moderations-endpoint (PLAN.md §3).

Flow:
  1. Validate model + input fields from request body.
  2. Run NonChatGovernance.authorize (9 ordered checks, estimated_tokens=None — no
     TPM dimension for this endpoint).
  3. Query ModelRow.modality + ModelRow.provider for the model_id — catalog-gated
     ONLY (M2): the tenant's `ml_moderation` guardrail config block is NEVER consulted.
  4. Resolve the per-tenant BYOK `openai` credential (resolve_provider_credential) —
     a missing/disabled key surfaces as ERR_PROVIDER_KEY_MISSING (402), never the
     internal guardrail's silent fail_open/fail_closed degrade (M5).
  5. Unconditional PII scrub (mask_pii_in_messages) over the input text(s) before
     they ever leave the process (M4).
  6. await app.state.ml_moderation_provider.moderate_raw(...) — the SAME isolated
     breaker instance the internal guardrail uses (M3); NEVER select_provider/
     ProviderRegistry, NEVER evaluate_nonchat_request_guardrails over its own input
     (M9 — circular).
  7. _fire_record_with_raw (single-bill invariant); billed prompt_tokens = a
     deterministic word-count estimate over the ACTUAL (PII-scrubbed) input, tagged
     usage_source="estimated_word_count" (M7/M8).
  8. Return (status, body) to the router.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow
from gateway.core.error_catalog import (
    MODEL_MODALITY_MISMATCH,
    MODEL_UNKNOWN,
    MODERATION_INPUT_REQUIRED,
    MODERATION_INPUT_UNSUPPORTED,
    MODERATION_MODEL_REQUIRED,
    PROVIDER_UNAVAILABLE,
    UPSTREAM_UNAVAILABLE,
)
from gateway.proxy.application.governance import NonChatGovernance

# use_cases.py is INVIOLABLE (must stay byte-identical), so the fire-and-forget
# recorder helper cannot be made public there; the frozen contract mandates reusing
# this exact recorder fn — hence the targeted reportPrivateUsage suppression (same
# precedent as embeddings_use_case.py / images_use_case.py / audio_use_case.py).
from gateway.proxy.application.use_cases import (
    _fire_record_with_raw,  # pyright: ignore[reportPrivateUsage]
    resolve_provider_credential,
)
from gateway.proxy.domain.credential_context import reset_provider_credential
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.guardrail_tenant_context import (
    reset_guardrail_tenant_id,
    set_guardrail_tenant_id,
)
from gateway.proxy.domain.ports import (
    PlatformCredentialFallback,
    TenantCredentialResolver,
    UsageRecorder,
)
from gateway.proxy.infrastructure.guardrail_evaluator import mask_pii_in_messages

# ~4 chars/token is the same industry-standard proxy the M8 fallback rule anchors on.
_CHARS_PER_TOKEN_FALLBACK = 4
# A naive whitespace split "looks implausibly low" once the average token it implies
# is far longer than a plausible Latin-script word (English averages ~4.7 chars/word).
# Above this ratio (chars / whitespace_word_count) the char-based proxy takes over —
# this is exactly the CJK/Thai/non-whitespace-delimited-script case M8 names.
_IMPLAUSIBLE_CHARS_PER_WORD = 10


class ModerationRawProvider(Protocol):
    """Structural port: the additive `moderate_raw` seam on OpenAiModerationClient."""

    async def moderate_raw(
        self, input_value: str | list[str], *, model: str = ...
    ) -> dict[str, Any]: ...


def estimate_word_count(text: str) -> int:
    """Deterministic billed-quantity estimate over one (already-scrubbed) input text.

    M8: a real, reproducible measurement of real input — never an invented number.
    Naive whitespace split by default; falls back to `ceil(len(text) / 4)` (the same
    ~4-chars/token heuristic industry tokenizers approximate) whenever the whitespace
    count looks implausibly low relative to text length, so non-whitespace-delimited
    scripts (CJK/Thai/...) are never near-zero-billed.
    """
    if not text:
        return 0
    word_count = len(text.split())
    chars = len(text)
    if word_count == 0:
        return math.ceil(chars / _CHARS_PER_TOKEN_FALLBACK)
    avg_chars_per_word = chars / word_count
    if avg_chars_per_word > _IMPLAUSIBLE_CHARS_PER_WORD:
        char_estimate = math.ceil(chars / _CHARS_PER_TOKEN_FALLBACK)
        return max(word_count, char_estimate)
    return word_count


def _estimate_total_word_count(texts: list[str]) -> int:
    """Sum the per-item estimate across every input text (array input, M1/M2)."""
    return sum(estimate_word_count(t) for t in texts)


def _validate_input(input_val: Any) -> tuple[list[str], bool]:
    """Validate + normalize the `input` field.

    Returns (texts, is_list). Raises MODERATION_INPUT_REQUIRED (422) for None/""/[],
    MODERATION_INPUT_UNSUPPORTED (422) for a list containing a non-string item.
    """
    if input_val is None or input_val == "" or input_val == []:
        raise MODERATION_INPUT_REQUIRED.exc()
    if isinstance(input_val, str):
        return [input_val], False
    if isinstance(input_val, list):
        if not all(isinstance(x, str) for x in input_val):
            raise MODERATION_INPUT_UNSUPPORTED.exc()
        return list(input_val), True
    raise MODERATION_INPUT_UNSUPPORTED.exc()


class ModerationsUseCase:
    """Orchestrate a single POST /v1/moderations request."""

    def __init__(
        self,
        *,
        governance: NonChatGovernance,
        session: AsyncSession,
        ml_moderation_provider: ModerationRawProvider | None,
        tenant_credential_resolver: TenantCredentialResolver | None = None,
        platform_credential_fallback: PlatformCredentialFallback | None = None,
    ) -> None:
        self._governance = governance
        self._session = session
        self._ml_moderation_provider = ml_moderation_provider
        self._tenant_credential_resolver = tenant_credential_resolver
        self._platform_credential_fallback = platform_credential_fallback

    async def execute(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Execute the moderations request pipeline. Returns (status, response_body)."""
        # Step 1: Validate model field
        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise MODERATION_MODEL_REQUIRED.exc()

        # Step 2: Validate input field
        texts, is_list = _validate_input(body.get("input"))

        # Step 3: Governance (auth -> expiry -> allowlist -> catalog -> budget -> rpm).
        # estimated_tokens=None -> TPM dimension skipped (moderation has no TPM concept,
        # mirrors images_use_case.py/audio_use_case.py).
        authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=None)

        # Step 4: Query modality + provider from catalog (M2: catalog-gated ONLY — the
        # tenant's ml_moderation guardrail config block is never consulted here).
        stmt = select(ModelRow.modality, ModelRow.provider).where(
            ModelRow.id == model_id,
            ModelRow.active.is_(True),
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise MODEL_UNKNOWN.exc(model_id=model_id)
        if row.modality != "moderation":
            raise MODEL_MODALITY_MISMATCH.exc(
                model_id=model_id,
                detail=(
                    f"model '{model_id}' has modality '{row.modality}',"
                    " endpoint requires 'moderation'"
                ),
            )

        # Step 5: provider entirely unwired -> 503 ERR_PROVIDER_UNAVAILABLE (M6),
        # distinct from a configured-but-down provider (502, below).
        if self._ml_moderation_provider is None:
            raise PROVIDER_UNAVAILABLE.exc(provider="ml_moderation")

        # Step 6: Resolve the per-tenant BYOK `openai` credential — a missing/disabled
        # key raises ERR_PROVIDER_KEY_MISSING (402) from inside resolve_provider_credential
        # itself (M5), never the internal guardrail's silent fail_open/fail_closed degrade.
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver,
            authz.tenant_id,
            row.provider,
            platform_fallback=self._platform_credential_fallback,
        )

        try:
            # Step 7: Unconditional PII scrub (M4) — parity with the internal guardrail's
            # own protection, applied before the input ever leaves the process. Reuses
            # the message-shaped helper so both callers share one regex table.
            messages = [{"content": t} for t in texts]
            scrubbed = mask_pii_in_messages(messages, {})
            scrubbed_texts = [m.get("content", "") for m in scrubbed]

            # Step 8: Call the moderation provider (M3, M9 — no evaluate_nonchat_
            # request_guardrails wrap over this endpoint's own input).
            #
            # CR-1 (moderations-endpoint PLAN.md §6 GATE RECORD): thread tenant
            # identity to the per-tenant CircuitBreaker registry OpenAiModerationClient
            # owns, via the SAME guardrail_tenant_context ContextVar the internal
            # guardrail call sites already set — this endpoint never routes through
            # evaluate_nonchat_request_guardrails (M9), so that existing set-call never
            # runs on this path; set it here instead so both call sites reach the SAME
            # breaker for a given tenant. Reset in `finally`, mirroring
            # nonchat_guardrails.py's own set/reset pattern exactly.
            send_value: str | list[str] = scrubbed_texts if is_list else scrubbed_texts[0]
            _gtid_token = set_guardrail_tenant_id(authz.tenant_id)
            try:
                raw_body = await self._ml_moderation_provider.moderate_raw(
                    send_value, model=model_id
                )
            except (UpstreamUnavailableError, CircuitOpenError):
                raise UPSTREAM_UNAVAILABLE.exc() from None
            finally:
                reset_guardrail_tenant_id(_gtid_token)
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

        # Step 9: Fire-and-forget usage record (single-bill invariant, M7). Billed
        # quantity = a deterministic word-count estimate over the ACTUAL (PII-scrubbed)
        # input actually sent, tagged usage_source so it is never confused with a
        # provider-reported count (the real endpoint never returns one) (M8).
        quantity = _estimate_total_word_count(scrubbed_texts)
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage={"prompt_tokens": quantity},
            status=200,
            team_id=authz.team_id,
            agent_principal_id=authz.agent_principal_id,
            usage_source="estimated_word_count",
        )

        return 200, raw_body
