"""AudioUseCase — POST /v1/audio/transcriptions (STT) + /v1/audio/speech (TTS).

Contract FROZEN @ audio-endpoints (TASK.md §3); the STT duration resolution
(step 6) is extended by stt-duration-derivation (TASK.md §3).

STT flow:
  1. Validate file + model fields from multipart form.
  2. Run NonChatGovernance.authorize (estimated_tokens=None → TPM skipped).
  3. Query ModelRow.modality + ModelRow.provider for the model_id.
  4. select_provider → UpstreamProvider adapter.
  5. await upstream.post_multipart("/audio/transcriptions", files, data).
  6. Resolve duration_s: positive upstream body["duration"] wins (verbose_json);
     else derive server-side from the uploaded header
     (audio_duration.derive_duration_seconds); else 0.0 + stt_duration_unavailable WARN.
  7. _fire_record_with_raw (single-bill, pricing_unit="per_second").
  8. Return (status, body).

TTS flow:
  1. Validate model + input + voice fields from JSON body.
  2. Run NonChatGovernance.authorize (estimated_tokens=None → TPM skipped).
  3. Query ModelRow.modality + ModelRow.provider for the model_id.
  4. select_provider → UpstreamProvider adapter (503 PRE-stream).
  5. _fire_record_with_raw (single-bill, BEFORE StreamingResponse, pricing_unit="per_character").
  6. gen = provider.stream_bytes("/audio/speech", body)  — NO await (sync returning AsyncIterator).
  7. Return (gen, media_type).
"""

from __future__ import annotations

import logging
import math
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow
from gateway.core.error_catalog import (
    MODEL_UNKNOWN,
    PAYLOAD_FILE_REQUIRED,
    PAYLOAD_INPUT_REQUIRED,
    PAYLOAD_MODEL_REQUIRED,
    PAYLOAD_VOICE_REQUIRED,
    UPSTREAM_UNAVAILABLE,
)
from gateway.proxy.application.audio_duration import derive_duration_seconds
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

_log = logging.getLogger(__name__)


async def _stream_resetting_credential(
    inner: AsyncIterator[bytes], token: object
) -> AsyncIterator[bytes]:
    """Yield from ``inner`` then reset the credential contextvar (credential-resolution-seam §3).

    The TTS adapter fires ``_auth_headers()`` lazily on the FIRST iteration of the byte
    stream — which happens in the router's StreamingResponse AFTER ``execute()`` returns —
    so the credential set in ``execute()`` must stay live across the return boundary and be
    reset only once the stream is consumed (or closed early via GeneratorExit). Tolerate a
    cross-context reset (Starlette may drain the response in a copied context); the original
    request-task context is discarded at task end, so the credential never leaks.
    """
    try:
        async for chunk in inner:
            yield chunk
    finally:
        try:
            reset_provider_credential(token)  # type: ignore[arg-type]
        except ValueError:
            pass


# Media type map for TTS response_format → Content-Type (§3 FROZEN)
_RESPONSE_FORMAT_MEDIA_TYPES: dict[str, str] = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
}

# Passthrough fields forwarded from multipart form to upstream STT endpoint
_STT_PASSTHROUGH_FIELDS = ("language", "prompt", "response_format", "temperature")


class TranscriptionUseCase:
    """Orchestrate a single POST /v1/audio/transcriptions (STT) request."""

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
        form: Any,  # starlette.datastructures.FormData
        registry: ProviderRegistry,
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Execute the STT transcription request pipeline.

        Steps per FROZEN contract (TASK.md §3 TRANSCRIPTION USE CASE FLOW):
          1. Validate file field → PAYLOAD_FILE_REQUIRED (422) if absent
          2. Validate model field → PAYLOAD_MODEL_REQUIRED (422) if absent/empty
          3. Governance authorize (estimated_tokens=None → TPM skipped)
          4. Query ModelRow.modality + ModelRow.provider
          5. select_provider → UpstreamProvider
          6. Read file bytes; build files + data dicts; call post_multipart
          7. Resolve duration_s: positive upstream body["duration"] wins; else
             derive_duration_seconds(file_bytes); else 0.0 + stt_duration_unavailable WARN
          8. _fire_record_with_raw (single-bill, pricing_unit="per_second")
          9. return (status, resp_body)
        """
        # Step 1: Validate file field
        file_field = form.get("file")
        if not file_field:
            raise PAYLOAD_FILE_REQUIRED.exc()

        # Step 2: Validate model field
        model_id = form.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        # Step 3: Governance (auth → expiry → allowlist → catalog → budget → rpm)
        # estimated_tokens=None → Step 9 (TPM) is skipped — audio has no token dimension
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

        # Step 6: Read file bytes and build multipart payload
        file_bytes = await file_field.read()
        files: dict[str, Any] = {
            "file": (
                file_field.filename or "audio",
                file_bytes,
                file_field.content_type or "application/octet-stream",
            )
        }
        data: dict[str, Any] = {"model": model_id}
        for field_name in _STT_PASSTHROUGH_FIELDS:
            value = form.get(field_name)
            if value is not None:
                data[field_name] = value

        # Resolve per-tenant provider credential into the contextvar (credential-resolution-seam
        # §3). Gated to converted providers; Bedrock/Azure skip. ProviderKeyMissing → 402.
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver, authz.tenant_id, row.provider
        )

        # Call upstream STT endpoint
        try:
            status, resp_body = await provider_adapter.post_multipart(
                "/audio/transcriptions", files=files, data=data
            )
        except (UpstreamUnavailableError, CircuitOpenError):
            raise UPSTREAM_UNAVAILABLE.exc() from None
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

        # Step 7: Resolve billable duration (prefer-upstream-fallback-derive).
        # Upstream's own positive duration (verbose_json) is authoritative and wins
        # byte-identically (AU2). Absent it, derive server-side from the uploaded
        # header so a no-verbose_json call no longer silently bills $0. An
        # undecodable header degrades to $0 + WARN — accuracy is never an
        # availability gate; the transcription still returns 200.
        raw_duration = resp_body.get("duration")
        if (
            isinstance(raw_duration, (int, float))
            and not isinstance(raw_duration, bool)
            and math.isfinite(raw_duration)
            and raw_duration > 0
        ):
            duration_s = float(raw_duration)
        else:
            derived = derive_duration_seconds(file_bytes)
            if derived is not None:
                duration_s = derived
            else:
                duration_s = 0.0
                _log.warning(
                    "stt_duration_unavailable",
                    extra={"model": model_id, "provider": row.provider},
                )

        # Step 8: Fire-and-forget usage record (single-bill invariant)
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=None,
            status=status,
            team_id=authz.team_id,
            pricing_unit="per_second",
            quantity=Decimal(str(duration_s)),
        )

        # Step 9: Return upstream response
        return status, resp_body


class SpeechUseCase:
    """Orchestrate a single POST /v1/audio/speech (TTS) request.

    CRITICAL: governance MUST run and be able to raise BEFORE the StreamingResponse
    is constructed. The usage record fires exactly once AFTER governance+provider-select
    succeed and BEFORE the generator is returned (bill-at-start invariant).
    """

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
    ) -> tuple[AsyncIterator[bytes], str]:
        """Execute the TTS speech request pipeline.

        Returns (audio_generator, media_type); billing fires inside BEFORE return.

        Steps per FROZEN contract (TASK.md §3 SPEECH USE CASE FLOW):
          1. Validate model field → PAYLOAD_MODEL_REQUIRED (422)
          2. Validate input field → PAYLOAD_INPUT_REQUIRED (422)
          3. Validate voice field → PAYLOAD_VOICE_REQUIRED (422)
          4. Governance authorize (all errors PRE-stream)
          5. Query ModelRow.modality + ModelRow.provider
          6. select_provider → UpstreamProvider (503 PRE-stream)
          7. _fire_record_with_raw (single-bill BEFORE StreamingResponse)
          8. Resolve media_type from response_format
          9. gen = provider.stream_bytes(...) — sync, NO await
          10. return (gen, media_type)
        """
        # Step 1: Validate model field
        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        # Step 2: Validate input field
        input_text = body.get("input")
        if not input_text or not isinstance(input_text, str) or not input_text.strip():
            raise PAYLOAD_INPUT_REQUIRED.exc()

        # Step 3: Validate voice field
        voice = body.get("voice")
        if not voice or not isinstance(voice, str) or not voice.strip():
            raise PAYLOAD_VOICE_REQUIRED.exc()

        # Step 4: Governance (all checks run PRE-stream; any error prevents streaming)
        # estimated_tokens=None → TPM check skipped for audio
        authz = await self._governance.authorize(raw_key, model_id, estimated_tokens=None)

        # Step 5: Query modality + provider from catalog
        stmt = select(ModelRow.modality, ModelRow.provider).where(
            ModelRow.id == model_id,
            ModelRow.active.is_(True),
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise MODEL_UNKNOWN.exc(model_id=model_id)

        # Step 6: Resolve provider adapter (503 PRE-stream if absent)
        provider_adapter = select_provider(row.modality, row.provider, registry)

        # Step 6.5: Resolve the per-tenant credential into the contextvar PRE-stream
        # (credential-resolution-seam §3) — BEFORE billing so a ProviderKeyMissing → 402
        # prevents a bill (single-bill invariant). Gated to converted providers; Bedrock/
        # Azure skip. The contextvar stays set across the return boundary and is reset by
        # the generator wrapper after the byte stream is consumed.
        _cred_token = await resolve_provider_credential(
            self._tenant_credential_resolver, authz.tenant_id, row.provider
        )

        # Step 7: Fire-and-forget usage record BEFORE constructing the stream.
        # quantity=Decimal(len(input_text)): char count known from request body pre-stream.
        # status=200: committed immediately after governance+provider-select succeed.
        # SINGLE-BILL: fires exactly once; no recording on any error path above.
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=None,
            status=200,
            team_id=authz.team_id,
            pricing_unit="per_character",
            quantity=Decimal(len(input_text)),
        )

        # Step 8: Resolve media_type from response_format; default "audio/mpeg" (mp3)
        media_type = _RESPONSE_FORMAT_MEDIA_TYPES.get(
            body.get("response_format", "mp3"), "audio/mpeg"
        )

        # Step 9: stream_bytes is a SYNC method returning AsyncIterator[bytes] (NO await).
        # Verified: OpenAIDirectProvider.stream_bytes(path, payload) -> AsyncIterator[bytes]
        gen = provider_adapter.stream_bytes("/audio/speech", body)

        # Step 9.5: when a credential was resolved, wrap the stream so the contextvar is
        # reset after the bytes are consumed (the adapter reads it lazily on first iteration).
        if _cred_token is not None:
            gen = _stream_resetting_credential(gen, _cred_token)

        # Step 10: Return generator + media_type (StreamingResponse constructed by the router)
        return gen, media_type
