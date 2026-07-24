"""API router for POST /v1/moderations proxy endpoint.

Contract FROZEN @ moderations-endpoint (PLAN.md §3).

Mirrors the style of proxy/api/embeddings_router.py without modifying it.
Calls app.state.ml_moderation_provider.moderate_raw() directly — never
select_provider()/ProviderRegistry (that path is the SHARED chat/embeddings
breaker; routing moderation traffic through it would defeat the isolation
ml-moderation-layer froze).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from gateway.proxy.api.deps import get_raw_api_key, get_usage_recorder
from gateway.proxy.api.moderations_deps import get_moderations_use_case
from gateway.proxy.application.moderations_use_case import ModerationsUseCase
from gateway.proxy.domain.ports import UsageRecorder

moderations_router = APIRouter(tags=["proxy"])


@moderations_router.post("/v1/moderations")
async def moderations(
    request: Request,
    use_case: Annotated[ModerationsUseCase, Depends(get_moderations_use_case)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/moderations — OpenAI-wire text-classification, metered per_token.

    Reads raw JSON body (dict), validates model + input fields, runs governance,
    resolves the tenant's BYOK openai credential, scrubs PII, forwards to the
    isolated ml-moderation provider, fires a usage record, and returns the full
    upstream moderation response verbatim.
    """
    body: dict[str, Any] = await request.json()

    _status, response_body = await use_case.execute(
        raw_key=raw_key,
        body=body,
        usage_recorder=usage_recorder,
    )

    return response_body
