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

from gateway.catalog.domain.entities import parse_input_modalities
from gateway.catalog.infrastructure.orm import ModelRow
from gateway.core.error_catalog import (
    AUTH_KEY_INVALID,
    IMAGE_EDIT_MODEL_UNKNOWN,
    MODEL_MODALITY_MISMATCH,
    MODEL_UNKNOWN,
    PAYLOAD_IMAGE_REQUIRED,
    PAYLOAD_IMAGE_TOO_LARGE,
    PAYLOAD_MODEL_REQUIRED,
    PAYLOAD_PROMPT_REQUIRED,
    PRESET_NOT_FOUND,
    UPSTREAM_UNAVAILABLE,
)
from gateway.core.errors import ProblemError
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.proxy.application.governance import NonChatGovernance
from gateway.proxy.application.nonchat_guardrails import evaluate_nonchat_request_guardrails

# use_cases.py is INVIOLABLE (must stay byte-identical), so _fire_record_with_raw
# cannot be made public there; the frozen contract mandates reusing this exact
# recorder fn — hence the targeted reportPrivateUsage suppression.
from gateway.proxy.application.use_cases import (
    _fire_record_with_raw,  # pyright: ignore[reportPrivateUsage]
    resolve_provider_credential,
)
from gateway.proxy.domain.credential_context import reset_provider_credential
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.model_presets import TenantModelPresetStore, parse_preset_selector
from gateway.proxy.domain.ports import (
    KeyAuthenticator,
    PayloadCapturePort,
    PlatformCredentialFallback,
    TenantCredentialResolver,
    UsageRecorder,
)
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry, select_provider


class ImagesUseCase:
    """Orchestrate a single POST /v1/images/generations request."""

    def __init__(
        self,
        *,
        governance: NonChatGovernance,
        session: AsyncSession,
        tenant_credential_resolver: TenantCredentialResolver | None = None,
        platform_credential_fallback: PlatformCredentialFallback | None = None,
        authenticator: KeyAuthenticator | None = None,
        tenant_model_preset_store: TenantModelPresetStore | None = None,
        guardrail_evaluator: Any = None,
        metrics_registry: Any = None,
        guardrail_verdict_session_factory: Any = None,
        payload_capture: PayloadCapturePort | None = None,
    ) -> None:
        self._governance = governance
        self._session = session
        # guardrails-nonchat-parity (audit Issue 1): all four optional — None ⇒ feature
        # off ⇒ execute() is byte-identical (shared helper fast-path). Wired by images_deps.
        self._guardrail_evaluator = guardrail_evaluator
        self._metrics_registry = metrics_registry
        self._guardrail_verdict_session_factory = guardrail_verdict_session_factory
        self._payload_capture = payload_capture
        # credential-resolution-seam §3: None ⇒ resolver not wired (legacy/test).
        self._tenant_credential_resolver = tenant_credential_resolver
        self._platform_credential_fallback = platform_credential_fallback
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
        # preset-resolution-ingress (v56 §3): resolve a <preset>:<alias> selector to the
        # tenant's target model BEFORE validation/governance/catalog/upstream. Mutates
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

        # Step 2: Validate prompt field
        prompt = body.get("prompt")
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            raise PAYLOAD_PROMPT_REQUIRED.exc()

        # Step 3: Governance (auth → expiry → allowlist → catalog → budget → rpm)
        # estimated_tokens=None → Step 9 (TPM) is skipped — images have no token dimension
        authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=None)

        # Step 3.5: Request-leg guardrails (guardrails-nonchat-parity, audit Issue 1).
        # Runs BEFORE catalog/upstream so a block rejects here (never billed) and a
        # pii_mask rewrites body["prompt"] before it is sent upstream. No-op fast path
        # when unwired / no tenant configs (byte-identical).
        _masked, _pii_masked = await evaluate_nonchat_request_guardrails(
            guardrail_evaluator=self._guardrail_evaluator,
            authz=authz,
            texts=[prompt],
            model_id=model_id,
            usage_recorder=usage_recorder,
            request_body=body,
            metrics_registry=self._metrics_registry,
            verdict_session_factory=self._guardrail_verdict_session_factory,
            payload_capture=self._payload_capture,
        )
        if _pii_masked:
            body = {**body, "prompt": _masked[0]}

        # Step 4: Query modality + provider from catalog
        stmt = select(ModelRow.modality, ModelRow.provider).where(
            ModelRow.id == model_id,
            ModelRow.active.is_(True),
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise MODEL_UNKNOWN.exc(model_id=model_id)

        # preset-capability-validation §3: coarse operation-type guard — unconditional (no
        # feature flag). Reuses row.modality already fetched above (zero new I/O). A model
        # whose modality isn't "image" would otherwise reach select_provider/the upstream
        # facade and silently misroute to a chat completion (still billed) — reject here,
        # BEFORE select_provider/upstream/billing (single-bill invariant preserved).
        if row.modality != "image":
            raise MODEL_MODALITY_MISMATCH.exc(
                model_id=model_id,
                detail=(
                    f"model '{model_id}' has modality '{row.modality}', endpoint requires 'image'"
                ),
            )

        # Step 5: Resolve provider adapter
        provider_adapter = select_provider(row.modality, row.provider, registry)

        # Step 5.5: Resolve the per-tenant provider credential into the request contextvar
        # (credential-resolution-seam §3). Gated to converted providers; Bedrock/Azure skip
        # (env-bound, task 3). ProviderKeyMissing → ProblemError(402). Reset in finally.
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver,
            authz.tenant_id,
            row.provider,
            platform_fallback=self._platform_credential_fallback,
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
            agent_principal_id=authz.agent_principal_id,
            pricing_unit="per_image",
            quantity=Decimal(n_images),
            pii_masked=_pii_masked,
        )

        # Step 9: Return upstream response
        return status, resp_body


# ---------------------------------------------------------------------------
# ImageEditUseCase / ImageVariationUseCase — image-edits-variations TASK.md §3
# (FROZEN @ v1)
#
# Sibling classes mirroring TranscriptionUseCase/SpeechUseCase's split in
# audio_use_case.py (chosen framing, PLAN.md §1): edits (prompt required, mask
# optional) and variations (no prompt at all) have different required-field
# shapes, so two classes beat one polymorphic execute(op=...) method. Both share
# the same multipart-in/JSON-out shape as ImagesUseCase.execute but read a
# `starlette.datastructures.FormData` (multipart) instead of a JSON dict body,
# and forward via UpstreamProvider.post_multipart (already shipped for audio
# STT, unused by images until now) instead of post_json.
# ---------------------------------------------------------------------------

# Passthrough fields forwarded verbatim from the multipart form to the upstream
# /images/edits endpoint (besides image/mask/model/prompt, handled explicitly).
_IMAGE_EDIT_PASSTHROUGH_FIELDS = (
    "n",
    "size",
    "response_format",
    "background",
    "quality",
    "output_format",
    "user",
)

# /images/variations has NO prompt/mask/background/quality/output_format params.
_IMAGE_VARIATION_PASSTHROUGH_FIELDS = (
    "n",
    "size",
    "response_format",
    "user",
)


async def _authorize_translating_model_unknown(
    governance: NonChatGovernance, raw_key: str | None, model_id: str
) -> Any:
    """Run governance.authorize(), translating its shared 400 ERR_MODEL_UNKNOWN into this
    task's frozen-contract 404 (PLAN.md §3 / §4 test_ie7 + the IV-analog).

    NonChatGovernance._check_model_catalog (Step 4 of the nine ordered governance checks,
    shared by chat/embeddings/generations/audio) raises the catalog-wide MODEL_UNKNOWN
    ErrorSpec at 400 -- correct for every OTHER call site and left untouched there. This
    task's own §4 red suite asserts 404 specifically for /v1/images/edits and
    /v1/images/variations, so the translation happens HERE (a task-local seam) rather than
    by changing the shared governance module or its ErrorSpec (which would flip status for
    every other endpoint's unknown-model case). Every other ProblemError from governance
    (auth, budget, rpm, allowlist, ...) propagates unchanged.
    """
    try:
        return await governance.authorize(raw_key, model_id, estimated_tokens=None)
    except ProblemError as exc:
        if exc.code == MODEL_UNKNOWN.code and exc.status == MODEL_UNKNOWN.status:
            raise IMAGE_EDIT_MODEL_UNKNOWN.exc(model_id=model_id) from None
        raise


async def _read_capped_upload(field: Any, max_bytes: int) -> bytes:
    """Read an UploadFile's bytes, rejecting an oversized file BEFORE upstream/bill.

    0 (or negative, defensively) ⇒ cap disabled (mirrors the tts_max_input_characters
    escape-hatch convention) — the read still happens, just unchecked.
    """
    data: bytes = await field.read()
    if max_bytes > 0 and len(data) > max_bytes:
        raise PAYLOAD_IMAGE_TOO_LARGE.exc()
    return data


def _build_upload_tuple(field: Any, data: bytes, default_filename: str) -> tuple[str, bytes, str]:
    return (
        field.filename or default_filename,
        data,
        field.content_type or "application/octet-stream",
    )


class ImageEditUseCase:
    """Orchestrate a single POST /v1/images/edits request (image-edits-variations §3)."""

    def __init__(
        self,
        *,
        governance: NonChatGovernance,
        session: AsyncSession,
        tenant_credential_resolver: TenantCredentialResolver | None = None,
        platform_credential_fallback: PlatformCredentialFallback | None = None,
        max_image_bytes: int = 0,
    ) -> None:
        self._governance = governance
        self._session = session
        self._tenant_credential_resolver = tenant_credential_resolver
        self._platform_credential_fallback = platform_credential_fallback
        # image-edits-variations §3: 0 ⇒ cap disabled (legacy/test-double parity);
        # prod DI injects Settings.image_edit_max_bytes (default 4 MiB).
        self._max_image_bytes = max_image_bytes

    async def execute(
        self,
        *,
        raw_key: str | None,
        form: Any,  # starlette.datastructures.FormData
        registry: ProviderRegistry,
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Execute the /v1/images/edits request pipeline.

        Steps per FROZEN contract (PLAN.md §3):
          1. Validate image field -> PAYLOAD_IMAGE_REQUIRED (422) if absent
          2. Validate prompt field -> PAYLOAD_PROMPT_REQUIRED (422) if absent/empty
          3. Validate model field -> PAYLOAD_MODEL_REQUIRED (422) if absent/empty
          4. Read image (+ optional mask) bytes, size-capped BEFORE governance/
             upstream/bill -> PAYLOAD_IMAGE_TOO_LARGE (413) if oversized
          5. Governance authorize (estimated_tokens=None -> TPM skipped)
          6. Query ModelRow.modality + ModelRow.provider + ModelRow.input_modalities
          7. modality != "image" -> MODEL_MODALITY_MISMATCH (400)
          8. capability gate: "image" not in input_modalities -> UNSUPPORTED_INPUT_MODALITY (400)
          9. select_provider -> UpstreamProvider
          10. await upstream.post_multipart("/images/edits", files, data)
          11. n_images = len(resp_body.get("data", [])) -- no requested-n fallback
          12. _fire_record_with_raw (single-bill, pricing_unit="per_image")
          13. return (status, resp_body)
        """
        # Step 1: Validate image field
        image_field = form.get("image")
        if not image_field:
            raise PAYLOAD_IMAGE_REQUIRED.exc()

        # Step 2: Validate prompt field (edits: REQUIRED, unlike variations)
        prompt = form.get("prompt")
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            raise PAYLOAD_PROMPT_REQUIRED.exc()

        # Step 3: Validate model field
        model_id = form.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        # Step 4: Read + size-cap the image (and optional mask) BEFORE governance/
        # upstream/bill (Must: no partial charge on an oversized upload).
        image_bytes = await _read_capped_upload(image_field, self._max_image_bytes)
        mask_field = form.get("mask")
        mask_bytes: bytes | None = None
        if mask_field:
            mask_bytes = await _read_capped_upload(mask_field, self._max_image_bytes)

        # Step 5: Governance (auth -> expiry -> allowlist -> catalog -> budget -> rpm)
        # estimated_tokens=None -> TPM check skipped -- images have no token dimension
        # (translates governance's shared 400 ERR_MODEL_UNKNOWN to this task's 404)
        authz = await _authorize_translating_model_unknown(self._governance, raw_key, model_id)

        # Step 6: Query modality + provider + input_modalities from catalog
        stmt = select(ModelRow.modality, ModelRow.provider, ModelRow.input_modalities).where(
            ModelRow.id == model_id,
            ModelRow.active.is_(True),
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise IMAGE_EDIT_MODEL_UNKNOWN.exc(model_id=model_id)

        # Step 7: Operation-type guard -- reject BEFORE select_provider/upstream/billing
        if row.modality != "image":
            raise MODEL_MODALITY_MISMATCH.exc(
                model_id=model_id,
                detail=(
                    f"model '{model_id}' has modality '{row.modality}', endpoint requires 'image'"
                ),
            )

        # Step 8: Capability gate -- model must accept an uploaded source image (edit/
        # variation-CAPABLE, e.g. dall-e-2), rejecting a generations-only model (e.g.
        # dall-e-3) BEFORE select_provider/upstream/billing (single-bill invariant).
        from gateway.proxy.application.modality_guard import enforce

        enforce(
            frozenset({"image"}),
            parse_input_modalities(row.input_modalities) or None,
            model_id=model_id,
        )

        # Step 9: Resolve provider adapter
        provider_adapter = select_provider(row.modality, row.provider, registry)

        # Step 9.5: Resolve the per-tenant provider credential (credential-resolution-
        # seam §3). Reset in finally.
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver,
            authz.tenant_id,
            row.provider,
            platform_fallback=self._platform_credential_fallback,
        )

        # Step 10: Build multipart payload + call upstream
        files: dict[str, Any] = {
            "image": _build_upload_tuple(image_field, image_bytes, "image"),
        }
        if mask_bytes is not None:
            files["mask"] = _build_upload_tuple(mask_field, mask_bytes, "mask")
        data: dict[str, Any] = {"model": model_id, "prompt": prompt}
        for field_name in _IMAGE_EDIT_PASSTHROUGH_FIELDS:
            value = form.get(field_name)
            if value is not None:
                data[field_name] = value

        try:
            status, resp_body = await provider_adapter.post_multipart(
                "/images/edits", files=files, data=data
            )
        except (UpstreamUnavailableError, CircuitOpenError):
            raise UPSTREAM_UNAVAILABLE.exc() from None
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

        # Step 11: Compute billed quantity -- bill exactly what upstream returned.
        # NO fallback to requested n: absent/empty data -> bill 0 (never over-bill).
        n_images = len(resp_body.get("data", []))

        # Step 12: Fire-and-forget usage record (single-bill invariant)
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=None,
            status=status,
            team_id=authz.team_id,
            agent_principal_id=authz.agent_principal_id,
            pricing_unit="per_image",
            quantity=Decimal(n_images),
        )

        # Step 13: Return upstream response
        return status, resp_body


class ImageVariationUseCase:
    """Orchestrate a single POST /v1/images/variations request (image-edits-variations §3).

    Identical shape to ImageEditUseCase except: no `prompt` field (never required,
    never forwarded), no `mask` field.
    """

    def __init__(
        self,
        *,
        governance: NonChatGovernance,
        session: AsyncSession,
        tenant_credential_resolver: TenantCredentialResolver | None = None,
        platform_credential_fallback: PlatformCredentialFallback | None = None,
        max_image_bytes: int = 0,
    ) -> None:
        self._governance = governance
        self._session = session
        self._tenant_credential_resolver = tenant_credential_resolver
        self._platform_credential_fallback = platform_credential_fallback
        self._max_image_bytes = max_image_bytes

    async def execute(
        self,
        *,
        raw_key: str | None,
        form: Any,  # starlette.datastructures.FormData
        registry: ProviderRegistry,
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Execute the /v1/images/variations request pipeline (see ImageEditUseCase.execute)."""
        # Step 1: Validate image field
        image_field = form.get("image")
        if not image_field:
            raise PAYLOAD_IMAGE_REQUIRED.exc()

        # Step 2: Validate model field
        model_id = form.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        # Step 3: Read + size-cap the image BEFORE governance/upstream/bill.
        image_bytes = await _read_capped_upload(image_field, self._max_image_bytes)

        # Step 4: Governance (translates the shared 400 ERR_MODEL_UNKNOWN to this task's 404)
        authz = await _authorize_translating_model_unknown(self._governance, raw_key, model_id)

        # Step 5: Query modality + provider + input_modalities from catalog
        stmt = select(ModelRow.modality, ModelRow.provider, ModelRow.input_modalities).where(
            ModelRow.id == model_id,
            ModelRow.active.is_(True),
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise IMAGE_EDIT_MODEL_UNKNOWN.exc(model_id=model_id)

        # Step 6: Operation-type guard
        if row.modality != "image":
            raise MODEL_MODALITY_MISMATCH.exc(
                model_id=model_id,
                detail=(
                    f"model '{model_id}' has modality '{row.modality}', endpoint requires 'image'"
                ),
            )

        # Step 7: Capability gate (same as edits -- variation is also an
        # edit/variation-CAPABLE-only operation, e.g. dall-e-3 is rejected here too).
        from gateway.proxy.application.modality_guard import enforce

        enforce(
            frozenset({"image"}),
            parse_input_modalities(row.input_modalities) or None,
            model_id=model_id,
        )

        # Step 8: Resolve provider adapter
        provider_adapter = select_provider(row.modality, row.provider, registry)

        # Step 8.5: Resolve the per-tenant provider credential
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver,
            authz.tenant_id,
            row.provider,
            platform_fallback=self._platform_credential_fallback,
        )

        # Step 9: Build multipart payload + call upstream (NO prompt/mask -- never
        # forwarded for variations).
        files: dict[str, Any] = {
            "image": _build_upload_tuple(image_field, image_bytes, "image"),
        }
        data: dict[str, Any] = {"model": model_id}
        for field_name in _IMAGE_VARIATION_PASSTHROUGH_FIELDS:
            value = form.get(field_name)
            if value is not None:
                data[field_name] = value

        try:
            status, resp_body = await provider_adapter.post_multipart(
                "/images/variations", files=files, data=data
            )
        except (UpstreamUnavailableError, CircuitOpenError):
            raise UPSTREAM_UNAVAILABLE.exc() from None
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

        # Step 10: Compute billed quantity -- bill exactly what upstream returned.
        n_images = len(resp_body.get("data", []))

        # Step 11: Fire-and-forget usage record (single-bill invariant)
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=None,
            status=status,
            team_id=authz.team_id,
            agent_principal_id=authz.agent_principal_id,
            pricing_unit="per_image",
            quantity=Decimal(n_images),
        )

        # Step 12: Return upstream response
        return status, resp_body
