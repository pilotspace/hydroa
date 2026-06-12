"""EmbeddingsUseCase — POST /v1/embeddings application layer.

Contract FROZEN @ embeddings-endpoint (TASK.md §3).

Flow:
  1. Validate model + input fields from request body.
  2. Run NonChatGovernance.authorize (9 ordered checks).
  3. Query ModelRow.modality + ModelRow.provider for the model_id.
  4. select_provider → UpstreamProvider adapter.
  5. await upstream.post_json("/embeddings", body).
  6. _fire_record_with_raw (single-bill invariant).
  7. Return (status, body) to the router.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow
from gateway.core.error_catalog import (
    MODEL_UNKNOWN,
    PAYLOAD_INPUT_REQUIRED,
    PAYLOAD_MODEL_REQUIRED,
    UPSTREAM_UNAVAILABLE,
)
from gateway.proxy.application.governance import NonChatGovernance

# use_cases.py is INVIOLABLE (must stay byte-identical), so _fire_record_with_raw
# cannot be made public there; the frozen contract mandates reusing this exact
# recorder fn — hence the targeted reportPrivateUsage suppression.
from gateway.proxy.application.use_cases import (
    _fire_record_with_raw,  # pyright: ignore[reportPrivateUsage]
)
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.ports import UsageRecorder
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry, select_provider


class EmbeddingsUseCase:
    """Orchestrate a single POST /v1/embeddings request."""

    def __init__(
        self,
        *,
        governance: NonChatGovernance,
        session: AsyncSession,
    ) -> None:
        self._governance = governance
        self._session = session

    async def execute(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        registry: ProviderRegistry,
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Execute the embeddings request pipeline.

        Steps per FROZEN contract (TASK.md §3 EMBEDDINGS USE CASE FLOW):
          1. Validate model field → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
          2. Validate input field → PAYLOAD_INPUT_REQUIRED (422) if absent/empty
          3. Governance authorize (9 steps)
          4. Query ModelRow.modality + ModelRow.provider
          5. select_provider → UpstreamProvider
          6. await upstream.post_json("/embeddings", body) → (status, resp_body)
          7. _fire_record_with_raw (single-bill)
          8. return (status, resp_body)
        """
        # Step 1: Validate model field
        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        # Step 2: Validate input field
        input_val = body.get("input")
        if input_val is None or input_val == "" or input_val == []:
            raise PAYLOAD_INPUT_REQUIRED.exc()

        # Step 3: Governance (auth → expiry → allowlist → catalog → budget → rpm → tpm)
        authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=1)

        # Step 4: Query modality + provider from catalog
        stmt = select(ModelRow.modality, ModelRow.provider).where(
            ModelRow.id == model_id,
            ModelRow.active.is_(True),
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise MODEL_UNKNOWN.exc(model_id=model_id)

        # Step 5: Resolve provider adapter
        provider_adapter = select_provider(row.modality, row.provider, registry)

        # Step 6: Call upstream
        try:
            status, resp_body = await provider_adapter.post_json("/embeddings", body)
        except (UpstreamUnavailableError, CircuitOpenError):
            raise UPSTREAM_UNAVAILABLE.exc() from None

        # Step 7: Fire-and-forget usage record (single-bill invariant)
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=resp_body.get("usage"),
            status=status,
            team_id=authz.team_id,
        )

        # Step 8: Return upstream response
        return status, resp_body
