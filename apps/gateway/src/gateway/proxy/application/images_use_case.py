"""ImagesUseCase — POST /v1/images/generations application layer.

Contract FROZEN @ images-endpoint (TASK.md §3).

Flow:
  1. Validate model field → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
  2. Validate prompt field → PAYLOAD_PROMPT_REQUIRED (422) if absent/empty
  3. Run NonChatGovernance.authorize (estimated_tokens=None → TPM skipped)
  4. Query ModelRow.modality + ModelRow.provider for the model_id.
  5. select_provider → UpstreamProvider adapter.
  6. await upstream.post_json("/images/generations", body) → (status, resp_body)
  7. n_images = len(resp_body.get("data", [])) — bill exactly what upstream returned
  8. _fire_record_with_raw (single-bill invariant, pricing_unit="per_image")
  9. Return (status, resp_body) to the router.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow
from gateway.core.error_catalog import (
    MODEL_UNKNOWN,
    PAYLOAD_MODEL_REQUIRED,
    PAYLOAD_PROMPT_REQUIRED,
    UPSTREAM_UNAVAILABLE,
)
from gateway.proxy.application.governance import NonChatGovernance

# use_cases.py is INVIOLABLE (must stay byte-identical), so _fire_record_with_raw
# cannot be made public there; the frozen contract mandates reusing this exact
# recorder fn — hence the targeted reportPrivateUsage suppression.
from gateway.proxy.application.use_cases import (
    _fire_record_with_raw,  # pyright: ignore[reportPrivateUsage]
    resolve_provider_credential,
)
from gateway.proxy.domain.credential_context import reset_provider_credential
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.ports import TenantCredentialResolver, UsageRecorder
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry, select_provider


class ImagesUseCase:
    """Orchestrate a single POST /v1/images/generations request."""

    def __init__(
        self,
        *,
        governance: NonChatGovernance,
        session: AsyncSession,
        tenant_credential_resolver: TenantCredentialResolver | None = None,
    ) -> None:
        self._governance = governance
        self._session = session
        # credential-resolution-seam §3: None ⇒ resolver not wired (legacy/test).
        self._tenant_credential_resolver = tenant_credential_resolver

    async def execute(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        registry: ProviderRegistry,
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Execute the images request pipeline.

        Steps per FROZEN contract (TASK.md §3 IMAGES USE CASE FLOW):
          1. Validate model field → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
          2. Validate prompt field → PAYLOAD_PROMPT_REQUIRED (422) if absent/empty
          3. Governance authorize (estimated_tokens=None → TPM skipped)
          4. Query ModelRow.modality + ModelRow.provider
          5. select_provider → UpstreamProvider
          6. await upstream.post_json("/images/generations", body) → (status, resp_body)
          7. n_images = len(resp_body.get("data", [])) — no requested-n fallback
          8. _fire_record_with_raw (single-bill, pricing_unit="per_image")
          9. return (status, resp_body)
        """
        # Step 1: Validate model field
        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        # Step 2: Validate prompt field
        prompt = body.get("prompt")
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            raise PAYLOAD_PROMPT_REQUIRED.exc()

        # Step 3: Governance (auth → expiry → allowlist → catalog → budget → rpm)
        # estimated_tokens=None → Step 9 (TPM) is skipped — images have no token dimension
        authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=None)

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

        # Step 5.5: Resolve the per-tenant provider credential into the request contextvar
        # (credential-resolution-seam §3). Gated to converted providers; Bedrock/Azure skip
        # (env-bound, task 3). ProviderKeyMissing → ProblemError(402). Reset in finally.
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver, authz.tenant_id, row.provider
        )

        # Step 6: Call upstream
        try:
            status, resp_body = await provider_adapter.post_json("/images/generations", body)
        except (UpstreamUnavailableError, CircuitOpenError):
            raise UPSTREAM_UNAVAILABLE.exc() from None
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

        # Step 7: Compute billed quantity — bill exactly the images the upstream returned.
        # NO fallback to requested n: absent/empty data → bill 0 (never over-bill on failure).
        # Resolved at freeze: consistent with chat's "bill what was consumed".
        n_images = len(resp_body.get("data", []))

        # Step 8: Fire-and-forget usage record (single-bill invariant)
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=None,
            status=status,
            team_id=authz.team_id,
            pricing_unit="per_image",
            quantity=Decimal(n_images),
        )

        # Step 9: Return upstream response
        return status, resp_body
