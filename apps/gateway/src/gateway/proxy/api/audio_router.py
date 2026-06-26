"""API router for audio endpoints: transcriptions, translations (STT) and speech (TTS).

Contract FROZEN @ audio-endpoints (TASK.md §3 AUDIO ROUTER FLOW).

Mirrors the style of proxy/api/embeddings_router.py without modifying it.
Routes exclusively via app.state.provider_registry — never calls
get_completion_upstream(), get_completion_use_case(), or get_embeddings_use_case().

CRITICAL invariants:
  - STT: governance runs before provider call; record fires once after upstream returns.
  - TTS: governance runs before StreamingResponse; record fires once BEFORE streaming.
    Status 200 is committed only when StreamingResponse is returned by the router;
    any exception before that point yields a problem+json error (RFC 9457).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.proxy.api.audio_deps import (
    get_provider_registry,
    get_speech_use_case,
    get_transcription_use_case,
)
from gateway.proxy.api.deps import get_raw_api_key, get_usage_recorder
from gateway.proxy.application.audio_use_case import SpeechUseCase, TranscriptionUseCase
from gateway.proxy.domain.ports import UsageRecorder
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

audio_router = APIRouter(tags=["proxy"])


@audio_router.post("/v1/audio/transcriptions")
async def audio_transcriptions(
    request: Request,
    use_case: Annotated[TranscriptionUseCase, Depends(get_transcription_use_case)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/audio/transcriptions — STT via multipart/form-data.

    Reads multipart form (requires python-multipart; already installed),
    validates file + model fields, runs governance, forwards to upstream,
    fires a per_second usage record, and returns the upstream JSON verbatim.
    """
    form = await request.form()
    status, response_body = await use_case.execute(
        raw_key=raw_key,
        form=form,
        registry=registry,
        usage_recorder=usage_recorder,
    )
    return JSONResponse(content=response_body, status_code=status)


@audio_router.post("/v1/audio/translations")
async def audio_translations(
    request: Request,
    use_case: Annotated[TranscriptionUseCase, Depends(get_transcription_use_case)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/audio/translations — STT translate-to-English via multipart/form-data.

    Identical to /v1/audio/transcriptions except the upstream path is
    /audio/translations and the `language` form field is not forwarded
    (the Whisper translate endpoint always outputs English).
    """
    form = await request.form()
    status, response_body = await use_case.execute(
        raw_key=raw_key,
        form=form,
        registry=registry,
        usage_recorder=usage_recorder,
        upstream_path="/audio/translations",
    )
    return JSONResponse(content=response_body, status_code=status)


@audio_router.post("/v1/audio/speech")
async def audio_speech(
    request: Request,
    use_case: Annotated[SpeechUseCase, Depends(get_speech_use_case)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/audio/speech — TTS as a streaming audio response.

    Reads JSON body, validates model + input + voice fields, runs governance
    (all errors PRE-stream), fires a per_character usage record (BEFORE streaming),
    and returns a StreamingResponse with the resolved audio media_type.

    Governance errors and provider-absent errors propagate as problem+json (RFC 9457)
    BEFORE the StreamingResponse is constructed — status 200 is never committed on
    pre-stream errors.
    """
    body: dict[str, Any] = await request.json()
    gen, media_type = await use_case.execute(
        raw_key=raw_key,
        body=body,
        registry=registry,
        usage_recorder=usage_recorder,
    )
    return StreamingResponse(gen, media_type=media_type)
