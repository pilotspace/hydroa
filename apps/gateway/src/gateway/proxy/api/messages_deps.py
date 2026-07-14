"""Dependency helpers for the /v1/messages (+count_tokens) Anthropic-wire ingress.

Mirrors the pattern from proxy/api/embeddings_deps.py without modifying it.
messages_router.py reuses get_completion_use_case / get_completion_upstream /
get_usage_recorder / get_raw_key_ingress from deps.py VERBATIM for governance —
this module holds only the ONE ingress-specific extra: a lightweight catalog
lookup so the translator can gate thinking/cache_control forwarding on which
provider the model actually resolves to (TASK.md §5 Strategy item 5 — "mirrors
the existing chat_modality_lookup/provider_resolver getattr pattern in
deps.py — do not invent a new resolution mechanism").

Contract FROZEN @ v1 (anthropic-messages-ingress TASK.md §3).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow

# The ONLY provider whose egress adapter forwards `thinking`/`cache_control`
# today (M7/M8) — see anthropic_ingress.py's docstring for the disclosed
# thinking/budget_tokens residual gap.
ANTHROPIC_PROVIDER = "anthropic"


async def resolve_model_provider(session: AsyncSession, model_id: str) -> str | None:
    """Look up the catalog `provider` column for `model_id`.

    Returns None when the model is absent from the catalog (governance's own
    ERR_MODEL_UNKNOWN check — inside CompletionUseCase — is the authoritative
    rejection; this lookup is advisory-only and never raises). A fail-open
    None simply means thinking/cache_control default to "forward" (harmless:
    every non-Anthropic adapter already ignores both fields, so a wrong guess
    never corrupts the outgoing request — see M7/M8's own "silently dropped,
    never an error" invariant).
    """
    stmt = select(ModelRow.provider).where(ModelRow.id == model_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row
