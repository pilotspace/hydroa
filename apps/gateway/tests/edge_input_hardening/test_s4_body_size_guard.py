"""Red suite for S4 — request body-size caps (edge-input-hardening TASK.md §2/§3 FROZEN @ v1).

One test per §2 S4 scenario. Uses the suite-local `settings` fixture (conftest.py) with
SMALL caps (2 KiB JSON / 4 KiB audio) so declared/streamed-oversize scenarios are fast to
construct — the exact same comparison logic is exercised regardless of the numeric
threshold. The mid-stream (missing/lying Content-Length) scenario is exercised directly at
the ASGI level against `BodySizeLimitMiddleware` (decoupled from auth/routing, which would
otherwise short-circuit before the body is ever streamed).

RED reason (pre-BUILD): `gateway.core.body_size_guard` does not exist -> ImportError; no
edge cap exists on /v1/* today, so an oversized body reaches the router/handler instead of
being rejected with 413.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.body_size_guard import BodySizeLimitMiddleware
from tests.edge_input_hardening.conftest import (
    SMALL_MAX_AUDIO_UPLOAD_BYTES,
    SMALL_MAX_JSON_BODY_BYTES,
    FakeAudioProvider,
    inject_fake_openai_audio_provider,
    seed_stt_model,
)
from tests import _redis_env

COMPLETIONS_PATH = "/v1/chat/completions"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"


def _assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code {code!r}, got {body.get('code')!r}: {body}"
    return body


# ---------------------------------------------------------------------------
# S4.1 — oversized JSON body rejected BEFORE parsing, truthful Content-Length
# ---------------------------------------------------------------------------


async def test_s4_1_oversized_json_declared_content_length_rejected_pre_routing(
    client: httpx.AsyncClient,
) -> None:
    """A truthful oversized Content-Length is rejected 413 before request.json() is ever
    called — no auth/routing/governance runs (proven here by NO Authorization header being
    needed at all: the 413 fires before the auth dependency would ever run)."""
    oversized = SMALL_MAX_JSON_BODY_BYTES + 1
    resp = await client.post(
        COMPLETIONS_PATH,
        content=b"x" * oversized,
        headers={
            "content-type": "application/json",
            "content-length": str(oversized),
        },
        # Deliberately NO Authorization header — if the 413 fires pre-routing (as required),
        # the response must be 413, never a 401 (which would mean auth ran first).
    )
    _assert_problem(resp, 413, "ERR_REQUEST_BODY_TOO_LARGE")


async def test_s4_1b_under_cap_json_is_not_rejected_by_body_guard(
    client: httpx.AsyncClient,
) -> None:
    """A body under the cap is NOT rejected by the size guard — it reaches downstream
    (proven by getting a 401, the auth layer's response, rather than a 413)."""
    under = SMALL_MAX_JSON_BODY_BYTES - 200
    resp = await client.post(
        COMPLETIONS_PATH,
        content=json.dumps({"model": "m", "messages": [], "pad": "x" * under}).encode(),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code != 413, resp.text


# ---------------------------------------------------------------------------
# S4.2 — oversized JSON body with a missing/lying Content-Length caught mid-stream
# ---------------------------------------------------------------------------


async def test_s4_2_missing_content_length_streamed_oversize_aborted_mid_stream() -> None:
    """ASGI-level unit test against BodySizeLimitMiddleware directly (decoupled from
    auth/routing) — proves the mid-stream abort behavior: once the running byte count
    crosses the cap, the downstream app never completes its body read and 413 is sent."""
    cap = 1_000
    downstream_saw_full_body = False

    async def _downstream_app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal downstream_saw_full_body
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        downstream_saw_full_body = True  # pragma: no cover — must never be reached
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    app = BodySizeLimitMiddleware(_downstream_app, route_caps={}, default_cap=cap)

    async def _client_for(total_bytes: int) -> httpx.Response:
        chunk = b"x" * 100
        n_chunks = total_bytes // len(chunk)

        async def _receive_gen() -> Any:
            for i in range(n_chunks):
                yield {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": i < n_chunks - 1,
                }

        gen = _receive_gen()

        async def _receive() -> Any:
            return await gen.__anext__()

        sent: list[dict[str, Any]] = []

        async def _send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "path": "/v1/chat/completions",
            "headers": [],  # no content-length — forces the mid-stream path
            "method": "POST",
        }
        await app(scope, _receive, _send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        body_msg = next(m for m in sent if m["type"] == "http.response.body")
        return httpx.Response(
            start["status"], content=body_msg["body"], headers=start.get("headers", [])
        )

    resp = await _client_for(2_200)  # > cap, no Content-Length header
    assert resp.status_code == 413, resp.text
    body = json.loads(resp.content)
    assert body.get("code") == "ERR_REQUEST_BODY_TOO_LARGE"
    assert not downstream_saw_full_body, (
        "the downstream app must never observe the full oversized body — the partial body "
        "is discarded once the running count crosses the cap"
    )


# ---------------------------------------------------------------------------
# S4.3 — legitimate large audio upload just under the cap succeeds
# ---------------------------------------------------------------------------


async def test_s4_3_under_cap_audio_upload_succeeds(
    app: Any,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    await seed_stt_model(db_session)
    fake_provider: FakeAudioProvider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(
        200, {"task": "transcribe", "language": "english", "duration": 1.0, "text": "hi"}
    )

    under_cap_audio = b"R" * (SMALL_MAX_AUDIO_UPLOAD_BYTES - 500)
    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files={"file": ("audio.mp3", under_cap_audio, "audio/mpeg")},
        data={"model": "whisper-1"},
        headers={"Authorization": f"Bearer {api_key_info['key']}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "hi"
    assert len(fake_provider.post_multipart_calls) == 1


# ---------------------------------------------------------------------------
# S4.4 — audio upload just over its cap rejected with the AUDIO cap, not the JSON cap
# ---------------------------------------------------------------------------


async def test_s4_4_audio_over_audio_cap_rejected_with_audio_specific_limit(
    client: httpx.AsyncClient,
) -> None:
    """The audio route gets the wider audio cap (route_caps longest-prefix-match), not the
    tighter JSON cap — proven via the declared-Content-Length fast path.

    SANCTIONED EDIT (upload-bounds-audio TASK.md §3 M3, 2026-08-18): the /v1/audio/ route
    cap now carries 1 MiB multipart headroom over max_audio_upload_bytes (main.
    _audio_route_cap — the _files_route_cap precedent), so TranscriptionUseCase owns the
    precise per-file boundary. This check's INTENT is unchanged — the audio route's edge
    cap is the wider audio-derived one and refuses with ERR_REQUEST_BODY_TOO_LARGE — the
    probe just moved one byte past the NEW edge boundary (cap+headroom+1)."""
    from gateway.main import _audio_route_cap

    over_audio_cap = _audio_route_cap(SMALL_MAX_AUDIO_UPLOAD_BYTES) + 1
    assert over_audio_cap > SMALL_MAX_JSON_BODY_BYTES, "audio cap must exceed the JSON cap"
    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        content=b"x" * over_audio_cap,
        headers={
            "content-type": "multipart/form-data; boundary=x",
            "content-length": str(over_audio_cap),
        },
    )
    _assert_problem(resp, 413, "ERR_REQUEST_BODY_TOO_LARGE")


async def test_s4_4b_audio_between_json_and_audio_cap_not_rejected_by_size_guard(
    client: httpx.AsyncClient,
) -> None:
    """A body BETWEEN the JSON cap and the audio cap on the audio route is allowed through
    by the size guard (would have 413'd on a /v1/ JSON route) — proving the audio cap, not
    the tighter JSON cap, governs this route. Uses a well-formed multipart body (httpx-built,
    real boundary) so the request actually reaches the handler instead of erroring on
    malformed multipart syntax — the size guard's decision is what's under test, not the
    audio handler's own parsing."""
    file_len = SMALL_MAX_JSON_BODY_BYTES + 300
    assert file_len < SMALL_MAX_AUDIO_UPLOAD_BYTES
    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files={"file": ("audio.mp3", b"R" * file_len, "audio/mpeg")},
        data={"model": "whisper-1"},
        # No Authorization header — downstream will 401, but that PROVES the body-size
        # guard let a >JSON-cap body through to the auth layer instead of 413ing it.
    )
    assert resp.status_code != 413, resp.text


# ---------------------------------------------------------------------------
# S4.6 — pre-existing pre-auth caps and domain caps are untouched
# ---------------------------------------------------------------------------


async def test_s4_6_pre_auth_4096_byte_cap_reachable_under_default_json_cap() -> None:
    """With the REAL default GATEWAY_MAX_JSON_BODY_BYTES (20 MiB, not this suite's small
    override), a body between 4096 bytes and the outer JSON cap still reaches
    /oauth/device/authorize's OWN pre-existing 4096-byte check and gets its OWN unchanged
    422 invalid_request — the new outer guard sits ABOVE it, never inside/replacing it."""
    from gateway.core.config import Settings
    from gateway.core.db import Base
    from gateway.main import create_app
    from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET

    settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
    )
    assert settings.max_json_body_bytes == 20_971_520  # unchanged real default — outer cap
    app = create_app(settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    oversized = 4096 + 1  # over the pre-auth router's OWN cap, well under the 20 MiB outer cap
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/oauth/device/authorize",
            content=json.dumps({"scope": "x" * oversized}).encode(),
            headers={"content-type": "application/json"},
        )
    await engine.dispose()
    assert resp.status_code == 422, resp.text
    assert resp.json().get("error") == "invalid_request", resp.text


def test_s4_6b_pre_auth_and_domain_cap_constants_unchanged() -> None:
    """Source-level regression floor: the 3 pre-auth routers' own byte cap and the 2
    pre-existing domain caps (artifact/realtime) are untouched by this task."""
    from gateway.agent_oauth.api import (
        device_approval_router,
        device_authorize_router,
        token_router,
    )
    from gateway.core.config import Settings

    assert token_router._MAX_BODY_BYTES == 4096  # noqa: SLF001
    assert device_authorize_router._MAX_BODY_BYTES == 4096  # noqa: SLF001
    assert device_approval_router._MAX_BODY_BYTES == 4096  # noqa: SLF001

    defaults = Settings(database_url="postgresql+asyncpg://x/y", jwt_secret="s")
    assert defaults.artifact_max_bytes == 10_485_760
    assert defaults.realtime_max_utterance_bytes == 26_214_400
