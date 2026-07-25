"""WebSocket endpoint /v1/realtime — authenticated, turn-based voice conversation.

Protocol (FROZEN CONTRACT):

Handshake:
  client → {"type":"auth","token":"sk-..."}
  server → {"type":"ready"}                    on success
         | close(code=4401)                     bad/missing/expired token, or first frame not auth
         | close(code=4408)                     no auth frame within
         |                                         settings.realtime_auth_timeout_seconds

Turn (repeatable):
  client → <binary audio frames>*  (accumulated into per-turn buffer)
  client → {"type":"commit","model_stt":..,"model_chat":..,"model_tts":..,"voice":..?}
  server → {"type":"transcript","text":<STT result>}
         → {"type":"reply","text":<chat reply>}
         → <binary audio frame(s)>  (TTS stream)
         → {"type":"turn_done"}

Non-fatal errors (socket stays open):
  {"type":"error","code":"utterance_too_large","message":...}
  {"type":"error","code":"no_audio","message":...}
  {"type":"error","code":"bad_frame","message":...}
  {"type":"error","code":"stt_failed","message":...}
  {"type":"error","code":"chat_failed","message":...}
  {"type":"error","code":"tts_failed","message":...}

WebSocketDisconnect at any point → break, cancel in-flight work, return cleanly.

Auth INVARIANTS:
  - Token comes ONLY from the auth frame — URL/query params are NEVER read.
  - No turn frame is processed before successful auth.
  - First frame that is binary, or JSON with type != "auth" → close(4401).
  - Auth wait is bounded by realtime_auth_timeout_seconds (asyncio.wait_for).

Test seam:
  app.state.realtime_stt(audio_bytes, model, raw_key) -> str
  app.state.realtime_chat(transcript, model, raw_key) -> str
  app.state.realtime_tts(text, model, voice, raw_key) -> AsyncIterator[bytes]
  When present on app.state, stubs are used instead of the real use-cases.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect

from gateway.core.config import Settings
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

_log = logging.getLogger(__name__)

realtime_router = APIRouter(tags=["proxy"])

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()

# WebSocket close codes (per frozen contract)
_CODE_AUTH_INVALID = 4401
_CODE_AUTH_TIMEOUT = 4408


# ---------------------------------------------------------------------------
# Internal auth helper
# ---------------------------------------------------------------------------


async def _authenticate_token(
    token: str,
    session: AsyncSession,
) -> AuthzResult | None:
    """Authenticate an sk- token via SqlAlchemyKeyAuthenticator.

    Returns AuthzResult on success, None on any auth failure (invalid/expired).
    Applies the same tz-aware expiry gate as the memory router's _authenticate.
    """
    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    authenticator = SqlAlchemyKeyAuthenticator(authz_use_case)
    try:
        authz = await authenticator.authenticate(token)
    except InvalidApiKeyError:
        return None

    # Expiry gate — tz-aware compare (mirrors memory/api/router.py _authenticate)
    if authz.expires_at is not None:
        exp = authz.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.UTC)
        if exp <= datetime.datetime.now(tz=datetime.UTC):
            return None

    return authz


# ---------------------------------------------------------------------------
# Error frame helper
# ---------------------------------------------------------------------------


def _error_frame(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


# ---------------------------------------------------------------------------
# Real use-case wrappers (used when stubs are absent)
# ---------------------------------------------------------------------------


async def _real_stt(
    audio_bytes: bytes,
    model: str,
    raw_key: str,
    *,
    websocket: WebSocket,
) -> str:
    """Call TranscriptionUseCase to transcribe audio_bytes; return transcript text."""
    from tempfile import SpooledTemporaryFile

    from starlette.datastructures import UploadFile

    from gateway.proxy.domain.ports import UsageRecorder

    app = websocket.app

    # Build a minimal UploadFile wrapping the buffered bytes (mirrors multipart form shape)
    spooled = SpooledTemporaryFile(max_size=0)
    spooled.write(audio_bytes)
    spooled.seek(0)
    upload_file = UploadFile(
        filename="audio.bin",
        file=spooled,  # type: ignore[arg-type]
        size=len(audio_bytes),
    )

    # Build a FormData-like dict (TranscriptionUseCase.execute reads form.get("file") and
    # form.get("model") — a plain dict satisfies both calls)
    form: dict[str, Any] = {"file": upload_file, "model": model}

    # Build the use-case the same way audio_deps.get_transcription_use_case does
    async with app.state.sessionmaker() as session:
        from gateway.keys.application.use_cases import AuthzUseCase as _AuthzUseCase
        from gateway.keys.infrastructure.repository import (
            SqlAlchemyApiKeyRepository as _KeyRepo,
        )
        from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher as _Hasher
        from gateway.proxy.application.audio_use_case import TranscriptionUseCase
        from gateway.proxy.application.governance import NonChatGovernance
        from gateway.proxy.infrastructure.key_authenticator import (
            SqlAlchemyKeyAuthenticator as _Auth,
        )
        from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
        from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

        _repo = _KeyRepo(session)
        _authz_uc = _AuthzUseCase(_repo, _Hasher())
        _authenticator = _Auth(_authz_uc)
        _model_checker = SqlAlchemyModelChecker(session)
        _budget_guard = app.state.budget_guard
        _rate_limiter = getattr(app.state, "rate_limiter", None)
        _redis = getattr(_budget_guard, "_redis", None)
        from gateway.proxy.infrastructure.tier_capacity_guard import (
            PassthroughTierCapacityGuard,
        )

        # service-tiers TASK.md §3 (FROZEN @ v1): same app.state-boot singleton pattern
        # as residency_lookup below. Absent ⇒ PassthroughTierCapacityGuard ⇒ byte-identical.
        _tier_capacity_guard = (
            getattr(app.state, "tier_capacity_guard", None) or PassthroughTierCapacityGuard()
        )

        _governance = NonChatGovernance(
            authenticator=_authenticator,
            model_checker=_model_checker,
            budget_guard=_budget_guard,
            rate_limiter=_rate_limiter,
            redis_client=_redis,
            session_factory=app.state.sessionmaker,
            # residency-policy TASK.md §3 (FROZEN @ v2): app.state-boot singleton, same
            # getattr pattern used across every other NonChatGovernance construction.
            residency_lookup=getattr(app.state, "residency_lookup", None),
            tier_capacity_guard=_tier_capacity_guard,
        )
        _cred_resolver = getattr(app.state, "tenant_credential_resolver", None)
        _settings: Settings = app.state.settings
        # preset-resolution-ingress (v56 §3): full coverage was the chosen scope for the
        # realtime-WS voice protocol — thread the SAME `_authenticator` already built above
        # (no second KeyAuthenticator) plus the real app.state singleton, mirroring _real_chat.
        _tenant_model_preset_store = getattr(app.state, "tenant_model_preset_store", None)
        # preset-capability-validation (v56 §3): mirror audio_deps.py:106 exactly — read the
        # SAME settings flag the HTTP STT path reads (never hardcode a boolean), so
        # realtime-WS STT behaves identically to HTTP under the same config.
        _uc = TranscriptionUseCase(
            governance=_governance,
            session=session,
            tenant_credential_resolver=_cred_resolver,
            max_duration_seconds=_settings.stt_max_duration_seconds,
            input_modality_guard_enabled=_settings.input_modality_guard_enabled,
            authenticator=_authenticator,
            tenant_model_preset_store=_tenant_model_preset_store,
        )
        _registry: ProviderRegistry = app.state.provider_registry
        _recorder: UsageRecorder = app.state.usage_recorder

        _status, _body = await _uc.execute(
            raw_key=raw_key,
            form=form,
            registry=_registry,
            usage_recorder=_recorder,
        )

    text: str = _body.get("text", "")
    return text


async def _real_chat(
    transcript: str,
    model: str,
    raw_key: str,
    *,
    websocket: WebSocket,
) -> str:
    """Call CompletionUseCase.complete (non-streaming) for a single-turn chat reply."""
    app = websocket.app

    async with app.state.sessionmaker() as session:
        # Build a minimal CompletionUseCase (same approach as deps.get_completion_use_case)
        from gateway.keys.application.use_cases import AuthzUseCase as _AuthzUseCase
        from gateway.keys.infrastructure.repository import (
            SqlAlchemyApiKeyRepository as _KeyRepo,
        )
        from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher as _Hasher
        from gateway.proxy.application.use_cases import CompletionUseCase
        from gateway.proxy.infrastructure.circuit_breaker_proxy import (
            BoundCircuitBreakerUpstream,
        )
        from gateway.proxy.infrastructure.input_modality_lookup import (
            SqlAlchemyInputModalityLookup,
        )
        from gateway.proxy.infrastructure.key_authenticator import (
            SqlAlchemyKeyAuthenticator as _Auth,
        )
        from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker

        _repo = _KeyRepo(session)
        _authz_uc = _AuthzUseCase(_repo, _Hasher())
        _authenticator = _Auth(_authz_uc)
        _model_checker = SqlAlchemyModelChecker(session)
        _budget_guard = app.state.budget_guard
        _rate_limiter = getattr(app.state, "rate_limiter", None)
        _span_emitter = getattr(app.state, "span_emitter", None)
        _stream_resilience = getattr(app.state, "stream_resilience_enabled", False)
        _cred_resolver = getattr(app.state, "tenant_credential_resolver", None)
        _provider_resolver = getattr(app.state, "provider_resolver", None)
        _cost_recovery = getattr(app.state, "cost_recovery_service", None)
        _bw_bucket = getattr(app.state, "bandwidth_bucket", None)
        _settings: Settings = app.state.settings
        # preset-resolution-ingress (v56 §3): full coverage was the chosen scope for
        # realtime-WS voice chat — thread the REAL app.state singleton (not defaulted
        # to None here); CompletionUseCase.complete() resolves via the SAME insertion
        # point as the HTTP chat path.
        _tenant_model_preset_store = getattr(app.state, "tenant_model_preset_store", None)
        # preset-capability-validation (v56 §3): mirror deps.py:208-211/228-229 exactly —
        # build the SAME SqlAlchemyInputModalityLookup over this request's session and read
        # the SAME settings flag the HTTP chat path reads (never hardcode a boolean), so
        # realtime-WS chat behaves identically to HTTP under the same config.
        _input_modality_lookup = SqlAlchemyInputModalityLookup(session)

        # file-search-tool (CR v2, F1): wire the shared FileSearchGrounder here too, so a
        # file_search tool on the realtime-WS chat path is serviced end-to-end AND stripped
        # (M5) before the upstream dial — never forwarded RAW. Mirrors deps.py exactly: the
        # SAME default-ON per-tenant-breaker embedder singleton (app.state.vector_store_
        # embedder, fail-safe-created if absent) over the SAME app.state.sessionmaker.
        from gateway.vector_stores.application.file_search import FileSearchGrounder
        from gateway.vector_stores.infrastructure.embedding_client import (
            VectorStoreEmbeddingClient,
        )

        _vs_embedder = getattr(app.state, "vector_store_embedder", None)
        if _vs_embedder is None:
            _vs_embedder = VectorStoreEmbeddingClient()
            app.state.vector_store_embedder = _vs_embedder
        _file_search_grounder = FileSearchGrounder(
            embedding_client=_vs_embedder,
            session_factory=app.state.sessionmaker,
        )

        _use_case = CompletionUseCase(
            authenticator=_authenticator,
            model_checker=_model_checker,
            budget_guard=_budget_guard,
            rate_limiter=_rate_limiter,
            span_emitter=_span_emitter,
            stream_resilience_enabled=_stream_resilience,
            tenant_credential_resolver=_cred_resolver,
            provider_resolver=_provider_resolver,
            cost_recovery=_cost_recovery,
            bandwidth_bucket=_bw_bucket,
            bandwidth_max_wait_s=_settings.bandwidth_max_wait_seconds,
            input_modality_lookup=_input_modality_lookup,
            input_modality_guard_enabled=_settings.input_modality_guard_enabled,
            tenant_model_preset_store=_tenant_model_preset_store,
            # chat-modality-guard (v56 §3): reuses the SAME app.state.provider_resolver
            # instance fetched above — zero new app.state attribute, zero new instance.
            chat_modality_lookup=_provider_resolver,
            # residency-policy TASK.md §3 (FROZEN @ v2): SAME app.state singleton the
            # HTTP chat path (deps.py) reads — never a second instance.
            residency_lookup=getattr(app.state, "residency_lookup", None),
            file_search_grounder=_file_search_grounder,
        )

        _circuit_breaker = app.state.circuit_breaker
        _base_upstream = app.state.completion_upstream
        _upstream = BoundCircuitBreakerUpstream(
            delegate=_base_upstream,
            breaker=_circuit_breaker,
        )
        _recorder = app.state.usage_recorder
        _model_router = getattr(app.state, "model_router", None)

        _body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": transcript}],
            "stream": False,
        }

        _status, _resp_body, _x_cache = await _use_case.complete(
            raw_key=raw_key,
            body=_body,
            upstream=_upstream,
            usage_recorder=_recorder,
            model_router=_model_router,
        )

    # batch-window-grouping (§3, G8): complete()'s return type is now a union
    # (dict[str, Any] | BatchDivertedStream). This call site never passes a
    # batch_processor (realtime voice-chat is a synchronous single turn, never
    # diverted), so a BatchDivertedStream is not reachable here today — guarded
    # anyway (never assume a union member away): degrade to "no reply text",
    # exactly like the other malformed-shape cases below, rather than an
    # AttributeError on a plain dict method.
    from gateway.proxy.domain.ports import BatchDivertedStream

    if isinstance(_resp_body, BatchDivertedStream):
        _log.warning("realtime chat completion unexpectedly diverted; returning empty reply")
        return ""

    # Extract reply text from choices[0].message.content
    choices = _resp_body.get("choices", [])
    if choices and isinstance(choices, list):
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message", {})
            if isinstance(msg, dict):
                content: str = msg.get("content", "")
                return content
    return ""


async def _real_tts(
    text_: str,
    model: str,
    voice: str,
    raw_key: str,
    *,
    websocket: WebSocket,
) -> AsyncIterator[bytes]:
    """Call SpeechUseCase.execute; return the audio byte-stream iterator."""
    app = websocket.app

    async with app.state.sessionmaker() as session:
        from gateway.keys.application.use_cases import AuthzUseCase as _AuthzUseCase
        from gateway.keys.infrastructure.repository import (
            SqlAlchemyApiKeyRepository as _KeyRepo,
        )
        from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher as _Hasher
        from gateway.proxy.application.audio_use_case import SpeechUseCase
        from gateway.proxy.application.governance import NonChatGovernance
        from gateway.proxy.infrastructure.key_authenticator import (
            SqlAlchemyKeyAuthenticator as _Auth,
        )
        from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker
        from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

        _repo = _KeyRepo(session)
        _authz_uc = _AuthzUseCase(_repo, _Hasher())
        _authenticator = _Auth(_authz_uc)
        _model_checker = SqlAlchemyModelChecker(session)
        _budget_guard = app.state.budget_guard
        _rate_limiter = getattr(app.state, "rate_limiter", None)
        _redis = getattr(_budget_guard, "_redis", None)
        _settings: Settings = app.state.settings
        from gateway.proxy.infrastructure.tier_capacity_guard import (
            PassthroughTierCapacityGuard,
        )

        # service-tiers TASK.md §3 (FROZEN @ v1): same app.state-boot singleton pattern
        # as residency_lookup below. Absent ⇒ PassthroughTierCapacityGuard ⇒ byte-identical.
        _tier_capacity_guard = (
            getattr(app.state, "tier_capacity_guard", None) or PassthroughTierCapacityGuard()
        )

        _governance = NonChatGovernance(
            authenticator=_authenticator,
            model_checker=_model_checker,
            budget_guard=_budget_guard,
            rate_limiter=_rate_limiter,
            redis_client=_redis,
            session_factory=app.state.sessionmaker,
            # residency-policy TASK.md §3 (FROZEN @ v2): app.state-boot singleton, same
            # getattr pattern used across every other NonChatGovernance construction.
            residency_lookup=getattr(app.state, "residency_lookup", None),
            tier_capacity_guard=_tier_capacity_guard,
        )
        _cred_resolver = getattr(app.state, "tenant_credential_resolver", None)
        # preset-resolution-ingress (v56 §3): full coverage was the chosen scope for the
        # realtime-WS voice protocol — thread the SAME `_authenticator` already built above
        # (no second KeyAuthenticator) plus the real app.state singleton, mirroring _real_chat.
        _tenant_model_preset_store = getattr(app.state, "tenant_model_preset_store", None)
        _uc = SpeechUseCase(
            governance=_governance,
            session=session,
            tenant_credential_resolver=_cred_resolver,
            max_input_characters=_settings.tts_max_input_characters,
            authenticator=_authenticator,
            tenant_model_preset_store=_tenant_model_preset_store,
        )
        _registry: ProviderRegistry = app.state.provider_registry
        _recorder = app.state.usage_recorder

        _body: dict[str, Any] = {"model": model, "input": text_, "voice": voice}
        gen, _media_type = await _uc.execute(
            raw_key=raw_key,
            body=_body,
            registry=_registry,
            usage_recorder=_recorder,
        )

    return gen


# ---------------------------------------------------------------------------
# The WebSocket endpoint
# ---------------------------------------------------------------------------


@realtime_router.websocket("/v1/realtime")
async def realtime_ws(websocket: WebSocket) -> None:
    """WebSocket /v1/realtime — authenticated, turn-based voice conversation.

    See module docstring for the full protocol specification.
    """
    await websocket.accept()

    app = websocket.app
    settings: Settings = app.state.settings

    # ------------------------------------------------------------------
    # Phase 1: Auth handshake (bounded by realtime_auth_timeout_seconds)
    # ------------------------------------------------------------------
    raw_key: str | None = None

    try:
        async with asyncio.timeout(settings.realtime_auth_timeout_seconds):
            first_msg = await websocket.receive()
    except TimeoutError:
        await websocket.close(code=_CODE_AUTH_TIMEOUT)
        return
    except WebSocketDisconnect:
        return

    # Decode the first frame
    if first_msg.get("type") == "websocket.receive":
        _bytes = first_msg.get("bytes")
        _text = first_msg.get("text")
    else:
        # Unexpected ASGI message
        await websocket.close(code=_CODE_AUTH_INVALID)
        return

    if _bytes is not None:
        # Binary frame as first message — close 4401 (no turn before auth)
        await websocket.close(code=_CODE_AUTH_INVALID)
        return

    # Parse JSON auth frame
    if _text is None:
        await websocket.close(code=_CODE_AUTH_INVALID)
        return

    import json as _json

    try:
        auth_frame = _json.loads(_text)
    except Exception:
        await websocket.close(code=_CODE_AUTH_INVALID)
        return

    if not isinstance(auth_frame, dict) or auth_frame.get("type") != "auth":
        # First frame is valid JSON but not an auth frame
        await websocket.close(code=_CODE_AUTH_INVALID)
        return

    token: str = auth_frame.get("token", "")
    if not token:
        await websocket.close(code=_CODE_AUTH_INVALID)
        return

    # Authenticate — token comes ONLY from the auth frame (never URL/query)
    async with app.state.sessionmaker() as auth_session:
        authz = await _authenticate_token(token, auth_session)

    if authz is None:
        await websocket.close(code=_CODE_AUTH_INVALID)
        return

    raw_key = token  # the authed plaintext key used for downstream use-case calls

    # Auth succeeded
    await websocket.send_json({"type": "ready"})

    # ------------------------------------------------------------------
    # Phase 2: Turn loop
    # ------------------------------------------------------------------
    audio_buffer = bytearray()

    while True:
        try:
            msg = await websocket.receive()
        except WebSocketDisconnect:
            # Client disconnected — clean return, no re-raise
            return

        if msg.get("type") == "websocket.disconnect":
            return

        _msg_bytes = msg.get("bytes")
        _msg_text = msg.get("text")

        if _msg_bytes is not None:
            # Binary audio frame — accumulate, but BOUND the in-memory buffer.
            # Design-for-failure: a remote-fed buffer must be bounded, else a client
            # that streams audio and never commits can exhaust server memory (DoS).
            # Once we reach the cap we stop growing (keeping cap+1 bytes — enough to
            # trip the `> cap` check at commit, which still returns utterance_too_large
            # per the frozen contract). 0 = unlimited (operator opt-out).
            _acc_cap = settings.realtime_max_utterance_bytes
            if _acc_cap > 0 and len(audio_buffer) + len(_msg_bytes) > _acc_cap:
                if len(audio_buffer) <= _acc_cap:
                    audio_buffer.extend(_msg_bytes[: _acc_cap + 1 - len(audio_buffer)])
                # else already at cap+1 — drop the excess (buffer stays bounded)
            else:
                audio_buffer.extend(_msg_bytes)
            continue

        if _msg_text is None:
            # Unexpected empty message; skip
            continue

        # Parse JSON control frame
        try:
            frame = _json.loads(_msg_text)
        except Exception:
            await websocket.send_json(_error_frame("bad_frame", "Malformed JSON control frame"))
            continue

        if not isinstance(frame, dict):
            await websocket.send_json(_error_frame("bad_frame", "Expected JSON object"))
            continue

        frame_type = frame.get("type")

        if frame_type == "commit":
            # ── Process a turn ──────────────────────────────────────────
            model_stt: str = frame.get("model_stt") or ""
            model_chat: str = frame.get("model_chat") or ""
            model_tts: str = frame.get("model_tts") or ""
            voice: str = frame.get("voice") or "alloy"

            # Re-read stubs per turn so tests can swap them between turns
            # (and so that mid-session stub replacement takes effect immediately).
            _stt_stub = getattr(app.state, "realtime_stt", None)
            _chat_stub = getattr(app.state, "realtime_chat", None)
            _tts_stub = getattr(app.state, "realtime_tts", None)

            # Check for empty buffer
            if not audio_buffer:
                await websocket.send_json(
                    _error_frame("no_audio", "No audio received before commit")
                )
                continue

            # Check utterance size cap (0 = unlimited)
            cap = settings.realtime_max_utterance_bytes
            if cap > 0 and len(audio_buffer) > cap:
                audio_buffer = bytearray()
                await websocket.send_json(
                    _error_frame(
                        "utterance_too_large",
                        f"Audio buffer exceeds {cap} byte limit",
                    )
                )
                continue

            audio_bytes = bytes(audio_buffer)
            audio_buffer = bytearray()  # reset for next turn

            # STT
            try:
                if _stt_stub is not None:
                    transcript: str = await _stt_stub(audio_bytes, model_stt, raw_key)
                else:
                    transcript = await _real_stt(
                        audio_bytes, model_stt, raw_key, websocket=websocket
                    )
            except Exception as exc:
                _log.warning("realtime stt_failed", exc_info=exc)
                await websocket.send_json(_error_frame("stt_failed", str(exc)))
                continue

            await websocket.send_json({"type": "transcript", "text": transcript})

            # Chat
            try:
                if _chat_stub is not None:
                    reply: str = await _chat_stub(transcript, model_chat, raw_key)
                else:
                    reply = await _real_chat(transcript, model_chat, raw_key, websocket=websocket)
            except Exception as exc:
                _log.warning("realtime chat_failed", exc_info=exc)
                await websocket.send_json(_error_frame("chat_failed", str(exc)))
                continue

            await websocket.send_json({"type": "reply", "text": reply})

            # TTS — stream binary audio frames
            try:
                if _tts_stub is not None:
                    audio_iter: AsyncIterator[bytes] = await _tts_stub(
                        reply, model_tts, voice, raw_key
                    )
                else:
                    audio_iter = await _real_tts(
                        reply, model_tts, voice, raw_key, websocket=websocket
                    )
                async for chunk in audio_iter:
                    await websocket.send_bytes(chunk)
            except WebSocketDisconnect:
                return
            except Exception as exc:
                _log.warning("realtime tts_failed", exc_info=exc)
                await websocket.send_json(_error_frame("tts_failed", str(exc)))
                continue

            await websocket.send_json({"type": "turn_done"})

        else:
            # Unknown control frame type
            await websocket.send_json(
                _error_frame(
                    "bad_frame",
                    f"Unknown frame type: {frame_type!r}; expected 'commit'",
                )
            )
