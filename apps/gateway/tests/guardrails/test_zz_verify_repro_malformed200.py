"""VERIFY-phase repro (ml-moderation-layer, add-verify finding #1): a malformed
200 response body from /v1/moderations (missing the expected "results" key —
e.g. schema drift, or a misconfigured base_url pointing at a JSON-emitting but
wrong endpoint) is silently read as a CLEAN verdict (flagged=False) instead of
being treated as a failed moderation call.

M6 requires EVERY failure to obtain a verdict to surface as action="unchecked",
never "passed". `OpenAiModerationClient.moderate()` only raises on status >= 400;
a 200 with an unexpected/empty body sails through `.get("results") or [{}]` and
`.get("categories") or {}` defaults, producing ModerationVerdict(flagged=False,
categories=[]) — MlModerationGuardrailEvaluator then emits action="passed" for
content that was never actually classified by anything.

This test asserts the CORRECT behavior (a malformed 200 must not be readable as
a clean verdict) and is expected to FAIL against the current
ml_moderation_evaluator.py implementation — a red repro, not a fix.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.ml_moderation_evaluator import OpenAiModerationClient
from gateway.proxy.infrastructure.openai_provider import OpenAIDirectProvider


async def test_moderate_on_200_with_missing_results_must_not_read_as_clean() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Valid JSON, 200 status, but NOT shaped like OpenAI's moderation response
        # (no "results" key at all).
        return httpx.Response(200, json={})

    provider = OpenAIDirectProvider(base_url="https://api.openai.com/v1")
    provider._client = httpx.AsyncClient(  # noqa: SLF001 — test double injection
        base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler)
    )
    ml_client = OpenAiModerationClient(provider)

    token = set_provider_credential(BearerCredential(secret="sk-test"))  # noqa: S106
    try:
        # EXPECTED (correct) behavior: a 200 body shaped nothing like the wire
        # contract must be treated as a FAILED call (so
        # MlModerationGuardrailEvaluator's except-Exception path fires
        # action="unchecked"), never surfaced as a verdict at all. Today
        # moderate() raises nothing and returns ModerationVerdict(flagged=False,
        # categories=[]) instead — so this call must raise, and currently doesn't.
        with pytest.raises(Exception, match=".*"):
            await ml_client.moderate("some genuinely flagged content")
    finally:
        reset_provider_credential(token)
