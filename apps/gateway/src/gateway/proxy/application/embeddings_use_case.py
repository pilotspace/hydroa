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
    AUTH_KEY_INVALID,
    MODEL_UNKNOWN,
    PAYLOAD_INPUT_REQUIRED,
    PAYLOAD_MODEL_REQUIRED,
    PRESET_NOT_FOUND,
    UPSTREAM_UNAVAILABLE,
)
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.proxy.application.governance import NonChatGovernance

# use_cases.py is INVIOLABLE (must stay byte-identical), so the fire-and-forget
# recorder/cache helpers cannot be made public there; the frozen contract mandates
# reusing these exact fns — hence the targeted reportPrivateUsage suppressions.
from gateway.proxy.application.use_cases import (
    _fire_cache_set,  # pyright: ignore[reportPrivateUsage]
    _fire_record_cached,  # pyright: ignore[reportPrivateUsage]
    _fire_record_with_raw,  # pyright: ignore[reportPrivateUsage]
    resolve_provider_credential,
)
from gateway.proxy.domain.credential_context import reset_provider_credential
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.model_presets import TenantModelPresetStore, parse_preset_selector
from gateway.proxy.domain.ports import (
    KeyAuthenticator,
    ResponseCache,
    TenantCredentialResolver,
    UsageRecorder,
)
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry, select_provider
from gateway.proxy.infrastructure.response_cache import build_embedding_cache_key


class EmbeddingsUseCase:
    """Orchestrate a single POST /v1/embeddings request."""

    def __init__(
        self,
        *,
        governance: NonChatGovernance,
        session: AsyncSession,
        tenant_credential_resolver: TenantCredentialResolver | None = None,
        authenticator: KeyAuthenticator | None = None,
        tenant_model_preset_store: TenantModelPresetStore | None = None,
    ) -> None:
        self._governance = governance
        self._session = session
        # credential-resolution-seam §3: None ⇒ resolver not wired (legacy/test) ⇒
        # no per-request credential set (env-bound adapters unaffected).
        self._tenant_credential_resolver = tenant_credential_resolver
        # preset-resolution-ingress (v56 §3): both None (defaults) ⇒ feature off ⇒
        # execute() is byte-identical (no resolve() call, no rewrite). `authenticator`
        # is the SAME KeyAuthenticator instance the DI factory already builds and wraps
        # into `governance` — never a second instance.
        self._authenticator = authenticator
        self._tenant_model_preset_store = tenant_model_preset_store

    async def execute(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        registry: ProviderRegistry,
        usage_recorder: UsageRecorder,
        response_cache: ResponseCache | None = None,
        cache_ttl_seconds: int = 300,
        request_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], str | None]:
        """Execute the embeddings request pipeline.

        Steps per FROZEN contract (TASK.md §3 — cache-controls additive layer):
          1. Validate model field → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
          2. Validate input field → PAYLOAD_INPUT_REQUIRED (422) if absent/empty
          3. Governance authorize (9 steps)
          3.5 Cache lookup (additive; before the catalog query so a HIT skips the DB):
              cache_on = response_cache is not None AND authz.cache_enabled
              HIT → _fire_record_cached ($0) → return (200, hit, "hit")
          4. Query ModelRow.modality + ModelRow.provider
          5. select_provider → UpstreamProvider
          6. await upstream.post_json("/embeddings", body) → (status, resp_body)
          7. _fire_record_with_raw (single-bill); store on 200 (fire-and-forget)
          8. return (status, resp_body, x_cache)  — x_cache None when cache disabled
        """
        # preset-resolution-ingress (v56 §3): resolve a <preset>:<alias> selector to the
        # tenant's target model BEFORE validation/governance/cache/catalog/upstream. Mutates
        # body["model"] in place — body is forwarded raw to upstream (Step 6 below).
        # Either collaborator unwired (None) ⇒ guaranteed no-op (byte-identical).
        if self._authenticator is not None and self._tenant_model_preset_store is not None:
            _raw_model = body.get("model", "")
            if isinstance(_raw_model, str):
                _selector = parse_preset_selector(_raw_model)
                if _selector is not None:
                    _preset_name, _alias_key = _selector
                    if not raw_key:
                        raise AUTH_KEY_INVALID.exc()
                    try:
                        _authz_pre = await self._authenticator.authenticate(raw_key)
                    except InvalidApiKeyError:
                        raise AUTH_KEY_INVALID.exc() from None
                    _target = await self._tenant_model_preset_store.resolve(
                        _authz_pre.tenant_id, _preset_name, _alias_key
                    )
                    if _target is None:
                        raise PRESET_NOT_FOUND.exc() from None
                    body["model"] = _target

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

        # Step 3.5: Cache logic (additive — None/disabled ⇒ today's flow, x_cache None).
        cache = response_cache
        x_cache: str | None = None
        cache_key: str | None = None
        no_cache = "no-cache" in (request_headers or {}).get("cache-control", "").lower()
        if cache is not None and authz.cache_enabled:
            cache_key = build_embedding_cache_key(str(authz.tenant_id), body)
            if not no_cache:
                hit = await cache.get(cache_key)  # swallows errors → None (degrade to MISS)
                if hit is not None:
                    # CACHE HIT: bill $0, skip the catalog query + upstream entirely.
                    hit_usage = hit.get("usage")
                    _fire_record_cached(
                        usage_recorder,
                        tenant_id=authz.tenant_id,
                        key_id=authz.key_id,
                        model=model_id,
                        usage=hit_usage if isinstance(hit_usage, dict) else None,
                        team_id=authz.team_id,
                    )
                    return 200, hit, "hit"
                x_cache = "miss"
            else:
                # BYPASS: Cache-Control: no-cache — call upstream, never store.
                x_cache = "bypass"

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
        # (credential-resolution-seam §3) so the Bearer non-chat adapter authenticates with
        # THIS tenant's key. Gated to converted providers; Bedrock/Azure skip (env-bound,
        # task 3). ProviderKeyMissing → ProblemError(402). Reset in finally — never leaks.
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver, authz.tenant_id, row.provider
        )

        # Step 6: Call upstream
        try:
            status, resp_body = await provider_adapter.post_json("/embeddings", body)
        except (UpstreamUnavailableError, CircuitOpenError):
            raise UPSTREAM_UNAVAILABLE.exc() from None
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

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

        # Step 7.5: Store the MISS on a 200 only (never non-200, never on bypass).
        if (
            cache is not None
            and authz.cache_enabled
            and status == 200
            and not no_cache
            and cache_key is not None
        ):
            _fire_cache_set(cache, cache_key, resp_body, cache_ttl_seconds)

        # Step 8: Return upstream response + cache marker
        return status, resp_body, x_cache
