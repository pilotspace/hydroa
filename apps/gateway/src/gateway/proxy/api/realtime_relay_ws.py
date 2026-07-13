"""WebSocket endpoint /v1/realtime/relay — provider-agnostic full-duplex relay (v52).

Flow:
  accept
   -> first frame {type:"auth", token}  (bounded by realtime_auth_timeout_seconds)
        | close 4401  bad/missing/expired token, or first frame not auth
        | close 4408  no auth frame within the timeout
   -> CONNECT-TIME GOVERNANCE (realtime-relay-governance TASK.md §3, M1/M2): additive,
      after auth-over-WS succeeds, before the provider session is built.
        | close 4000 + exc.status  allowlist/catalog/budget/RPM rejection (4400/4402/4403/4429)
   -> select the configured provider session
        | close 4404  no realtime provider configured (honest-degrade)
   -> run the t1 RelayPump (it owns connect-timeout / breaker / bandwidth-pacing /
      teardown + the relay outcome close code: 4503 / 1011 / 4408-idle / 4429 / 1000)
   -> fire-and-forget AuditEvents at session_opened / session_closed / session_rejected
      (realtime-relay-governance TASK.md §3, M6) — never gates the relay's own outcome.

AUTH-OVER-WS (security HARD, inherited from v47): the token is read ONLY from the
first frame, NEVER from the URL/query, and NO provider session is opened before a
successful authenticate. Reuses v47 `_authenticate_token` verbatim.

app.state STUB seams (present → used instead of the real path; DB/key-free WS tests):
  realtime_relay_authenticate(token, session) -> AuthzResult | None
  realtime_relay_session_factory(authz, websocket) -> RealtimeRelaySession | None  (None → 4404)
  realtime_relay_governance_authorize(token, model_id) -> AuthzResult  (raises ProblemError)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.config import Settings
from gateway.core.errors import ProblemError

# Reuse v47's auth-over-WS helper verbatim (the approved security surface).
from gateway.proxy.api.realtime_ws import (
    _authenticate_token,  # pyright: ignore[reportPrivateUsage]
)
from gateway.proxy.application.realtime_relay_pump import RelayPump
from gateway.proxy.domain.realtime_relay import RealtimeRelayClosed, RealtimeRelaySession

_log = logging.getLogger(__name__)

realtime_relay_router = APIRouter(tags=["proxy"])

_CODE_AUTH_INVALID = 4401
_CODE_AUTH_TIMEOUT = 4408
_CODE_NO_PROVIDER = 4404
# realtime-relay-governance (B2 TASK.md §3, M2): mechanical mapping, never a
# hand-maintained parallel table — 4000 + the SAME status error_catalog.py already names.
_GOVERNANCE_CODE_BASE = 4000


class _StarletteRelayTransport:
    """Adapts a Starlette WebSocket to the RealtimeClientTransport seam."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._closed = False

    async def receive(self) -> dict[str, Any] | bytes:
        msg = await self._ws.receive()
        if msg.get("type") == "websocket.disconnect":
            raise RealtimeRelayClosed("client disconnected")
        data = msg.get("bytes")
        if data is not None:
            return data
        text = msg.get("text")
        if text is not None:
            try:
                return json.loads(text)
            except (ValueError, TypeError) as exc:
                raise RealtimeRelayClosed("client sent a non-JSON text frame") from exc
        raise RealtimeRelayClosed("client frame had neither bytes nor text")

    async def send_event(self, frame: dict[str, Any]) -> None:
        await self._ws.send_json(frame)

    async def send_audio(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def close(self, code: int) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close(code)
        except Exception:
            _log.debug("relay transport close failed", exc_info=True)


async def _authenticate(app: Any, token: str) -> Any:
    """Authenticate the token via the stub seam, else v47 _authenticate_token."""
    stub = getattr(app.state, "realtime_relay_authenticate", None)
    if stub is not None:
        return await stub(token, None)
    async with app.state.sessionmaker() as session:
        return await _authenticate_token(token, session)


async def _build_session(app: Any, authz: Any, websocket: WebSocket) -> RealtimeRelaySession | None:
    """Build the provider session via the stub seam, else the real provider factory."""
    stub = getattr(app.state, "realtime_relay_session_factory", None)
    if stub is not None:
        result = stub(authz, websocket)
        if inspect.isawaitable(result):
            result = await result
        return result
    return await _real_session_factory(app, authz)


def _extract_secret(cred: Any) -> str | None:
    for attr in ("secret", "api_key"):
        value = getattr(cred, attr, None)
        if value is not None and hasattr(value, "get_secret_value"):
            return value.get_secret_value()
    return None


def _make_relay_usage_callback(
    usage_recorder: Any,
    tenant_id: Any,
    key_id: Any,
    model: str,
    *,
    team_id: Any = None,
) -> Any:
    """Build the per-turn usage capture callback for one relay session (TASK.md §3).

    Captures the ONE-TIME WS-auth identity (tenant_id/key_id/team_id) — never re-derived
    per turn. Reuses RecordingUsageRecorder.record()'s existing signature verbatim; its own
    Must #5 (never raise into the caller) already covers the swallow-on-failure guarantee.

    team_id (realtime-relay-governance TASK.md §3, M4): threaded through to
    usage_recorder.record() so a team-budgeted key's relay usage counts against its team's
    spend counter — was silently omitted before this fix. Applies to BOTH providers (this
    is the ONE shared callback builder both OpenAI and Gemini sessions receive).
    """

    async def _callback(usage: dict[str, Any]) -> None:
        await usage_recorder.record(
            tenant_id=tenant_id,
            key_id=key_id,
            model=model,
            usage=usage,
            status=200,
            usage_source="realtime_relay",
            team_id=team_id,
        )

    return _callback


def _dialed_model(settings: Settings) -> str | None:
    """Resolve the model governance/billing should key on (TASK.md §3).

    The SAME selector `_real_session_factory` already uses — an unconfigured provider
    (neither "openai" nor "gemini") resolves to None, so governance never runs and the
    existing 4404 honest-degrade is UNCHANGED.
    """
    if settings.realtime_relay_provider == "openai":
        return settings.realtime_relay_openai_model
    if settings.realtime_relay_provider == "gemini":
        return settings.realtime_relay_gemini_model
    return None


async def _authorize_governance(app: Any, token: str, model_id: str) -> Any:
    """Run connect-time governance via the stub seam, else the real NonChatGovernance gate.

    Mirrors realtime_ws.py:_real_stt's EXACT construction shape (TASK.md §3): a fresh
    NonChatGovernance built from app.state.budget_guard / app.state.sessionmaker / the
    SAME getattr(budget_guard, "_redis", None) pattern. Raises ProblemError on rejection —
    the caller maps it to a WS close code (M2, mechanical: 4000 + exc.status).
    """
    stub = getattr(app.state, "realtime_relay_governance_authorize", None)
    if stub is not None:
        return await stub(token, model_id)

    async with app.state.sessionmaker() as session:
        from gateway.keys.application.use_cases import AuthzUseCase as _AuthzUseCase
        from gateway.keys.infrastructure.repository import (
            SqlAlchemyApiKeyRepository as _KeyRepo,
        )
        from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher as _Hasher
        from gateway.proxy.application.governance import NonChatGovernance
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
        _redis = getattr(_budget_guard, "_redis", None)

        from gateway.proxy.infrastructure.tier_capacity_guard import (
            PassthroughTierCapacityGuard,
        )

        # service-tiers TASK.md §3 (FROZEN @ v1): same app.state-boot singleton pattern
        # as residency_lookup above. Absent ⇒ PassthroughTierCapacityGuard ⇒ byte-identical.
        _tier_capacity_guard = (
            getattr(app.state, "tier_capacity_guard", None) or PassthroughTierCapacityGuard()
        )

        governance = NonChatGovernance(
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
        authz = await governance.authorize(raw_key=token, model_id=model_id, estimated_tokens=None)

        # plan-enforcement (TASK.md §3, M6/R6): ONE-TIME WS-connect feature gate, reusing
        # the SAME session already open in this block — never per-message. Raised
        # ProblemError propagates to the caller's existing ProblemError->WS-close-code
        # translation (_GOVERNANCE_CODE_BASE + exc.status), never a new close-code scheme.
        # Inert for an unplanned tenant (M7).
        from gateway.tenants.application.entitlements import check_plan_feature

        await check_plan_feature(session, authz.tenant_id, "realtime")

        return authz


def _schedule_audit(app: Any, event: AuditEvent) -> None:
    """Fire-and-forget an AuditEvent via the existing record_audit() (TASK.md §3, M6).

    Never awaited on the hot path — record_audit's own fail-open contract means a DB/Redis
    failure here can never disrupt the relay session's own outcome.
    """
    _task = asyncio.ensure_future(record_audit(app.state.sessionmaker, event))
    _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _relay_audit_event(
    *,
    authz: Any,
    action: str,
    result: str,
    provider: str,
    model: str,
    extra: dict[str, Any] | None = None,
) -> AuditEvent:
    """Build one realtime_relay.* audit event (TASK.md §3, Option B — actor_key_id scoped).

    metadata NEVER carries token/key material — only provider/model + event-specific fields
    (close_code / rejection reason), per M6.
    """
    metadata: dict[str, Any] = {"provider": provider, "model": model}
    if extra:
        metadata.update(extra)
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=authz.tenant_id,
        actor_user_id=None,
        actor_key_id=authz.key_id,
        actor_email=None,
        action=action,
        target_type="realtime_relay",
        target_id=str(authz.key_id),
        result=result,
        metadata=metadata,
    )


async def _real_session_factory(app: Any, authz: Any) -> RealtimeRelaySession | None:
    """Production provider build — honest-degrade (None) when nothing is configured."""
    settings: Settings = app.state.settings
    provider = settings.realtime_relay_provider
    if provider not in ("openai", "gemini"):
        return None
    resolver = getattr(app.state, "tenant_credential_resolver", None)
    if resolver is None:
        return None
    try:
        cred = await resolver.resolve(authz.tenant_id, provider)
    except Exception:
        return None
    api_key = _extract_secret(cred)
    if not api_key:
        return None
    # M4: authz.team_id via getattr — tolerates a duck-typed authz double lacking the
    # attribute entirely (pre-M4 test fixtures), degrading to team_id=None rather than
    # AttributeError; a real AuthzResult always carries the field (defaults to None).
    team_id = getattr(authz, "team_id", None)
    if provider == "openai":
        from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession

        return OpenAIRealtimeSession(
            model=settings.realtime_relay_openai_model,
            api_key=api_key,
            connect_timeout=settings.realtime_relay_connect_timeout_seconds,
            on_usage=_make_relay_usage_callback(
                app.state.usage_recorder,
                authz.tenant_id,
                authz.key_id,
                settings.realtime_relay_openai_model,
                team_id=team_id,
            ),
        )
    from gateway.proxy.infrastructure.gemini_live import GeminiLiveSession

    # M3: Gemini now receives the SAME callback OpenAI already gets (was: none at all —
    # Gemini relay spend was categorically unmetered before this fix).
    return GeminiLiveSession(
        model=settings.realtime_relay_gemini_model,
        api_key=api_key,
        connect_timeout=settings.realtime_relay_connect_timeout_seconds,
        on_usage=_make_relay_usage_callback(
            app.state.usage_recorder,
            authz.tenant_id,
            authz.key_id,
            settings.realtime_relay_gemini_model,
            team_id=team_id,
        ),
    )


@realtime_relay_router.websocket("/v1/realtime/relay")
async def realtime_relay(websocket: WebSocket) -> None:
    app = websocket.app
    settings: Settings = app.state.settings
    await websocket.accept()

    # 1) AUTH-OVER-WS — first frame must be {type:"auth", token}, bounded by the timeout.
    try:
        first = await asyncio.wait_for(
            websocket.receive(), timeout=settings.realtime_auth_timeout_seconds
        )
    except TimeoutError:
        await websocket.close(_CODE_AUTH_TIMEOUT)
        return
    except WebSocketDisconnect:
        return

    if first.get("type") == "websocket.disconnect":
        return
    text = first.get("text")
    if text is None:
        await websocket.close(_CODE_AUTH_INVALID)  # binary / non-text first frame
        return
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        await websocket.close(_CODE_AUTH_INVALID)
        return
    token = payload.get("token") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "auth"
        or not isinstance(token, str)
        or not token
    ):
        # A non-string/empty token would crash the authenticator (500) — close cleanly.
        await websocket.close(_CODE_AUTH_INVALID)
        return

    authz = await _authenticate(app, token)
    if authz is None:
        await websocket.close(_CODE_AUTH_INVALID)
        return

    # 1.5) CONNECT-TIME GOVERNANCE (realtime-relay-governance TASK.md §3, M1/M2) — additive,
    # after auth-over-WS succeeds, before the provider session is built. Reuses the ONE
    # shared NonChatGovernance gate (never a second hand-rolled allowlist/budget/RPM copy).
    # An unconfigured provider (dialed_model is None) is UNCHANGED — governance never runs
    # for a provider that doesn't exist; falls through to the existing 4404 honest-degrade.
    dialed_model = _dialed_model(settings)
    if dialed_model is not None:
        try:
            authz = await _authorize_governance(app, token, dialed_model)
        except ProblemError as exc:
            await websocket.close(_GOVERNANCE_CODE_BASE + exc.status)
            # Identity is already known at this point (auth-over-WS succeeded) — a
            # post-identity rejection IS audited, unlike the pre-identity 4401/4408 paths.
            _schedule_audit(
                app,
                _relay_audit_event(
                    authz=authz,
                    action="realtime_relay.session_rejected",
                    result="rejected",
                    provider=settings.realtime_relay_provider,
                    model=dialed_model,
                    extra={
                        "close_code": _GOVERNANCE_CODE_BASE + exc.status,
                        "reason": exc.code,
                    },
                ),
            )
            return
        _schedule_audit(
            app,
            _relay_audit_event(
                authz=authz,
                action="realtime_relay.session_opened",
                result="success",
                provider=settings.realtime_relay_provider,
                model=dialed_model,
            ),
        )

    # 2) SELECT provider — honest-degrade when none configured.
    session = await _build_session(app, authz, websocket)
    if session is None:
        await websocket.close(_CODE_NO_PROVIDER)
        return

    # 3) RELAY — the pump owns connect/timeout/bandwidth-pacing/teardown + the outcome
    # close code. bandwidth_bucket reuses the SAME app.state singleton the chat streaming
    # path already reads (deps.py) — this task threads it into the relay endpoint too, it
    # does not invent a second one.
    transport = _StarletteRelayTransport(websocket)
    breaker = getattr(app.state, "realtime_relay_breaker", None)
    bandwidth_bucket = getattr(app.state, "bandwidth_bucket", None)
    pump = RelayPump(
        transport,
        session,
        settings,
        breaker=breaker,
        bandwidth_bucket=bandwidth_bucket,
        key_id=authz.key_id,
    )
    await pump.run()

    # session_closed (M6) — scheduled after pump.run() returns; carries the close code.
    # By this point dialed_model is guaranteed non-None (session-build required it).
    _schedule_audit(
        app,
        _relay_audit_event(
            authz=authz,
            action="realtime_relay.session_closed",
            result="success",
            provider=settings.realtime_relay_provider,
            model=dialed_model,  # type: ignore[arg-type]
            extra={"close_code": pump.close_code},
        ),
    )
