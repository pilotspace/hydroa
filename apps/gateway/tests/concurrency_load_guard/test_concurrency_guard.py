"""Failing-first (RED) suite for global back-pressure guard (concurrency-load-guard TASK.md §4).

One test per scenario in TASK.md §2 SCENARIOS.

Right-reason red targets (before BUILD):
  - GlobalBackPressureMiddleware does not exist yet → ImportError
  - Settings.max_concurrent_requests / back_pressure_retry_after_seconds missing → AttributeError
  - 503 + Retry-After + ERR_OVERLOADED body not returned on saturation → AssertionError

Infrastructure:
  - Pure-ASGI / no DB — all tests run without Postgres or Redis.
  - httpx.ASGITransport pointing at a minimal FastAPI app with GlobalBackPressureMiddleware.
  - asyncio.gather for concurrent burst tests (mirrors rate_limits pattern).

Implementation notes (after BUILD):
  - Middleware stack is built lazily on first request — call app.middleware_stack AFTER
    a request has been made to trigger the build, OR access the middleware instance directly
    when instantiating it for unit tests.
  - Stack traversal: Starlette wraps in ServerErrorMiddleware(app=...) → our middleware;
    uses .app attribute (public), not ._app.

Coverage target: ≥90% of new middleware branches (§4 plan).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# ---------------------------------------------------------------------------
# Import the two new symbols — these will fail until BUILD is done (right-reason red)
# ---------------------------------------------------------------------------

from gateway.proxy.api.concurrency_guard import GlobalBackPressureMiddleware
from gateway.core.config import Settings

# ---------------------------------------------------------------------------
# Helpers — minimal ASGI apps for testing
# ---------------------------------------------------------------------------

_COMPLETIONS_PATH = "/v1/chat/completions"
_EMBEDDINGS_PATH = "/v1/embeddings"

# How long a "slow stream" holds a slot open (seconds).
_SLOW_STREAM_DELAY_S = 0.1


def _make_test_app(max_concurrent: int, retry_after_s: int = 1) -> FastAPI:
    """Build a minimal FastAPI app with GlobalBackPressureMiddleware wired."""
    app = FastAPI()

    @app.get(_COMPLETIONS_PATH)
    async def chat() -> dict[str, str]:
        return {"ok": "true"}

    @app.get(_EMBEDDINGS_PATH)
    async def embeddings() -> dict[str, str]:
        return {"ok": "true"}

    app.add_middleware(
        GlobalBackPressureMiddleware,
        max_concurrent=max_concurrent,
        retry_after_s=retry_after_s,
    )
    return app


def _assert_503_overloaded(resp: httpx.Response, retry_after_s: int = 1) -> None:
    """Assert the shed response has the expected shape."""
    assert resp.status_code == 503, f"expected 503, got {resp.status_code}: {resp.text}"
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None, "Retry-After header missing on 503"
    assert int(retry_after) == retry_after_s, (
        f"expected Retry-After={retry_after_s}, got {retry_after!r}"
    )
    body = resp.json()
    assert body.get("code") == "ERR_OVERLOADED", (
        f"expected code=ERR_OVERLOADED, got {body.get('code')!r}; full body: {body}"
    )
    assert body.get("status") == 503, f"body.status must be 503, got {body.get('status')!r}"


def _find_bp_middleware(app: FastAPI) -> GlobalBackPressureMiddleware | None:
    """Walk the ASGI middleware stack to find the GlobalBackPressureMiddleware instance.

    The stack is built lazily by Starlette on first request. Call this AFTER at least
    one request has been made (or call app.middleware_stack explicitly first).

    Stack order after build: ServerErrorMiddleware.app → GlobalBackPressureMiddleware
    (the last-added middleware is outermost, ServerErrorMiddleware wraps it).
    """
    cur: Any = app.middleware_stack
    for _ in range(20):
        if cur is None:
            break
        if isinstance(cur, GlobalBackPressureMiddleware):
            return cur
        # Starlette ServerErrorMiddleware / ExceptionMiddleware use .app (public)
        cur = getattr(cur, "app", None)
    return None


async def _fire_one(client: httpx.AsyncClient, path: str = _COMPLETIONS_PATH) -> httpx.Response:
    return await client.get(path)


# ---------------------------------------------------------------------------
# Minimal ASGI scope / receive / send for direct middleware unit tests
# ---------------------------------------------------------------------------


def _http_scope(path: str = _COMPLETIONS_PATH) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
    }


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


# ---------------------------------------------------------------------------
# Test 1 — burst beyond the cap sheds the excess (app NOT called)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_burst_beyond_cap_sheds_excess() -> None:
    """cap=1, 1 slow stream in-flight → request #2 gets 503 + Retry-After, app NOT invoked.

    Uses a spy ASGI app to confirm the downstream is never reached on shed.
    """
    downstream_calls: list[str] = []

    class _SpyApp:
        """Downstream app spy — records paths called; sends a minimal 200 for admitted requests."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                downstream_calls.append(scope["path"])
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"application/json"]],
                }
            )
            await send({"type": "http.response.body", "body": b'{"ok":"true"}', "more_body": False})

    mw = GlobalBackPressureMiddleware(_SpyApp(), max_concurrent=1, retry_after_s=2)  # type: ignore[arg-type]

    # Saturate: acquire the single slot manually
    assert mw._sem is not None
    await mw._sem.acquire()
    mw._in_flight += 1

    # Drive a request through the saturated middleware
    shed_parts: list[dict[str, Any]] = []

    async def _send(message: dict[str, Any]) -> None:
        shed_parts.append(message)

    await mw(_http_scope(), _noop_receive, _send)  # type: ignore[arg-type]

    # The downstream spy must NOT have been called
    assert downstream_calls == [], (
        f"downstream was called on a saturated cap — must NOT be invoked; calls={downstream_calls}"
    )

    # Response must be 503 with Retry-After
    start_msg = next(m for m in shed_parts if m["type"] == "http.response.start")
    assert start_msg["status"] == 503, f"expected 503 shed status, got {start_msg['status']}"

    headers_dict = dict(start_msg.get("headers", []))
    retry_after_val = headers_dict.get(b"retry-after")
    assert retry_after_val is not None, "Retry-After header missing on shed"
    assert int(retry_after_val) == 2, f"expected Retry-After=2, got {retry_after_val!r}"

    # Full ASGI message sequence: a body message must follow the start message
    body_msg = next((m for m in shed_parts if m["type"] == "http.response.body"), None)
    assert body_msg is not None, (
        "shed response is missing http.response.body message after http.response.start"
    )
    assert body_msg.get("body"), "shed http.response.body must contain a non-empty body"

    # Release the slot we took
    mw._in_flight -= 1
    mw._sem.release()


# ---------------------------------------------------------------------------
# Test 2 — exactly cap admitted under concurrent burst (mirror rate_limits pattern)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_cap_admitted_concurrent() -> None:
    """N=10 concurrent requests, cap=3 → at most 3 admitted (200), rest shed (503).

    Uses the middleware directly with a slow downstream (asyncio.sleep) so all N
    coroutines are genuinely in-flight simultaneously at the locked() check.
    httpx.ASGITransport over fast synchronous handlers serialises requests (no yield
    points), so this test drives the middleware layer directly.
    """
    cap = 3
    total = 10
    hold_s = 0.05  # each admitted request holds its slot for 50 ms

    # Barrier: all tasks arrive before any of them are processed
    barrier = asyncio.Barrier(total)
    results: list[int] = []  # 200 = admitted, 503 = shed

    class _SlowDownstream:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await asyncio.sleep(hold_s)
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"application/json"]],
                }
            )
            await send({"type": "http.response.body", "body": b'{"ok":"true"}', "more_body": False})

    mw = GlobalBackPressureMiddleware(
        _SlowDownstream(),
        max_concurrent=cap,
        retry_after_s=1,  # type: ignore[arg-type]
    )

    async def _drive_one() -> None:
        captured_status: list[int] = []

        async def _send(msg: dict[str, Any]) -> None:
            if msg["type"] == "http.response.start":
                captured_status.append(msg["status"])

        # Wait at the barrier so all N coroutines reach the middleware simultaneously
        await barrier.wait()
        await mw(_http_scope(), _noop_receive, _send)  # type: ignore[arg-type]
        results.append(captured_status[0] if captured_status else -1)

    tasks = [asyncio.create_task(_drive_one()) for _ in range(total)]
    await asyncio.gather(*tasks)

    admitted = results.count(200)
    shed = results.count(503)

    assert 1 <= admitted <= cap, f"admission out of range: {admitted} admitted with cap={cap}; results={results}"
    assert shed >= total - cap, (
        f"expected at least {total - cap} shed, got {shed}; results={results}"
    )
    assert admitted + shed == total, f"all responses must be 200 or 503; got results={results}"
    # Semaphore must be fully released after all tasks complete
    assert not mw._sem.locked(), "semaphore still locked after all tasks completed — slot leaked"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Test 3 — PIVOTAL: slot held for the whole stream (the least-sure-flag canary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_held_for_whole_stream() -> None:
    """A slow SSE stream keeps in_flight elevated until the generator drains, then returns to 0.

    This is the canary for the critical assumption: does `await app(scope, receive, send)`
    return only AFTER the StreamingResponse body is fully sent?

    If in_flight drops to 0 BEFORE the stream completes → early-return confirmed → contract
    violated → STOP and report (the fallback is a send-wrapper that releases on final body msg).
    """
    delay_s = _SLOW_STREAM_DELAY_S
    stream_started: asyncio.Event = asyncio.Event()
    stream_done: asyncio.Event = asyncio.Event()
    in_flight_snapshots: list[int] = []

    # Build a middleware instance wrapping an async generator app directly
    # so we can inspect _in_flight without needing stack traversal.

    class _SlowStreamApp:
        """ASGI app that yields one SSE chunk after a delay."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"text/event-stream"]],
                }
            )
            stream_started.set()
            await asyncio.sleep(delay_s)
            await send(
                {
                    "type": "http.response.body",
                    "body": b"data: chunk\n\n",
                    "more_body": False,
                }
            )
            stream_done.set()

    mw = GlobalBackPressureMiddleware(_SlowStreamApp(), max_concurrent=5, retry_after_s=1)  # type: ignore[arg-type]

    sent_parts: list[dict[str, Any]] = []

    async def _send(msg: dict[str, Any]) -> None:
        sent_parts.append(msg)

    async def _monitor() -> None:
        """Sample in_flight once the stream has started, while it is still in-flight."""
        await stream_started.wait()
        # Stream has started but NOT finished (we're still in asyncio.sleep(delay_s))
        in_flight_snapshots.append(mw._in_flight)

    async def _drive() -> None:
        await mw(_http_scope(), _noop_receive, _send)  # type: ignore[arg-type]

    await asyncio.gather(_drive(), _monitor())

    # Post-drain: in_flight must be back to 0
    assert mw._in_flight == 0, (
        f"in_flight={mw._in_flight} after stream drained — expected 0 (slot must be released). "
        "If this is non-zero, the slot was leaked (finally block missing or wrong path)."
    )

    # During stream: in_flight must have been 1
    assert in_flight_snapshots, (
        "Monitor did not capture in_flight during stream — check timing/event ordering"
    )
    assert all(v == 1 for v in in_flight_snapshots), (
        f"Expected in_flight=1 while slow stream was running; got {in_flight_snapshots}. "
        "If in_flight=0 during stream → `await self._app(...)` returned EARLY before the stream "
        "body was fully sent. STOP — use the send-wrapper fallback (release on more_body=False)."
    )


# ---------------------------------------------------------------------------
# Test 4 — slot released on client disconnect (no leak)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_released_on_disconnect() -> None:
    """Client disconnects mid-stream → slot released (in_flight returns to 0), no leak."""

    class _DisconnectApp:
        """Downstream that raises GeneratorExit to simulate client disconnect mid-stream."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"text/event-stream"]],
                }
            )
            raise GeneratorExit()

    mw = GlobalBackPressureMiddleware(
        _DisconnectApp(),
        max_concurrent=2,
        retry_after_s=1,  # type: ignore[arg-type]
    )

    sent_parts: list[dict[str, Any]] = []

    async def _send(msg: dict[str, Any]) -> None:
        sent_parts.append(msg)

    with pytest.raises(GeneratorExit):
        await mw(_http_scope(), _noop_receive, _send)  # type: ignore[arg-type]

    # After GeneratorExit propagates, the slot MUST be released (try/finally)
    assert mw._in_flight == 0, (
        f"in_flight={mw._in_flight} after GeneratorExit — slot LEAKED (must be 0). "
        "The try/finally in __call__ must cover GeneratorExit."
    )
    # Semaphore must have a free slot (value > 0 = not fully locked)
    assert mw._sem is not None
    assert not mw._sem.locked(), (
        "Semaphore is fully locked after disconnect — slot leaked, semaphore.release() not called."
    )


# ---------------------------------------------------------------------------
# Test 5 — slot released on downstream error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_released_on_downstream_error() -> None:
    """Handler raises RuntimeError → slot released in finally, error re-raised."""

    class _ErrorApp:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            raise RuntimeError("simulated downstream error")

    mw = GlobalBackPressureMiddleware(
        _ErrorApp(),
        max_concurrent=2,
        retry_after_s=1,  # type: ignore[arg-type]
    )

    async def _send(msg: dict[str, Any]) -> None:
        pass

    with pytest.raises(RuntimeError, match="simulated downstream error"):
        await mw(_http_scope(), _noop_receive, _send)  # type: ignore[arg-type]

    assert mw._in_flight == 0, (
        f"in_flight={mw._in_flight} after RuntimeError — slot LEAKED (must be 0)."
    )
    assert mw._sem is not None
    assert not mw._sem.locked(), (
        "Semaphore fully locked after downstream error — release() not called in finally."
    )


# ---------------------------------------------------------------------------
# Test 6 — disabled knob is byte-identical (no accounting, no 503)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_knob_passthrough() -> None:
    """max_concurrent=0 → pure pass-through: no 503 ever, _sem is None, in_flight stays 0."""
    app = _make_test_app(max_concurrent=0)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        tasks = [_fire_one(c) for _ in range(20)]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    statuses = [r.status_code if isinstance(r, httpx.Response) else -1 for r in responses]
    assert all(s == 200 for s in statuses), (
        f"Expected all 200 with disabled guard (max_concurrent=0), got statuses={statuses}"
    )

    # Find the middleware instance — stack is now built after requests
    bp_mw = _find_bp_middleware(app)
    assert bp_mw is not None, "GlobalBackPressureMiddleware not found in stack"
    assert bp_mw._sem is None, (
        "Expected _sem=None when max_concurrent=0 (disabled mode — zero overhead)"
    )
    assert bp_mw._in_flight == 0, (
        f"in_flight={bp_mw._in_flight} with disabled guard — must stay 0 (no accounting)"
    )


# ---------------------------------------------------------------------------
# Test 7 — guard covers non-chat route (embeddings)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_covers_nonchat_route() -> None:
    """When cap is reached, non-chat routes (/v1/embeddings) are also shed with 503."""
    app = FastAPI()

    @app.get(_EMBEDDINGS_PATH)
    async def embeddings() -> dict[str, str]:
        return {"ok": "true"}

    app.add_middleware(
        GlobalBackPressureMiddleware,
        max_concurrent=1,
        retry_after_s=1,
    )

    # Make one request to trigger the lazy middleware stack build
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        warm = await c.get(_EMBEDDINGS_PATH)
        assert warm.status_code == 200, f"warmup request failed: {warm.text}"

    # Now find the middleware and saturate it
    bp_mw = _find_bp_middleware(app)
    assert bp_mw is not None, (
        "GlobalBackPressureMiddleware not found in middleware stack. "
        "Check that add_middleware registered it and _find_bp_middleware traversal is correct."
    )
    assert bp_mw._sem is not None

    # Take the one available slot
    assert not bp_mw._sem.locked(), "Semaphore should be free after warmup completed"
    await bp_mw._sem.acquire()
    bp_mw._in_flight += 1

    try:
        # The embeddings route should now be shed
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(_EMBEDDINGS_PATH)
        _assert_503_overloaded(resp)
    finally:
        bp_mw._in_flight -= 1
        bp_mw._sem.release()


# ---------------------------------------------------------------------------
# Test 8 — negative knob coerced to 0, boot succeeds, WARN emitted
# ---------------------------------------------------------------------------


def test_reject_invalid_knob_coerced_to_zero(caplog: pytest.LogCaptureFixture) -> None:
    """Negative GATEWAY_MAX_CONCURRENT_REQUESTS → coerced to 0, boot succeeds, WARN logged."""
    with caplog.at_level(logging.WARNING):
        s = Settings(max_concurrent_requests=-5)  # type: ignore[call-arg]

    assert s.max_concurrent_requests == 0, (  # type: ignore[attr-defined]
        f"Expected max_concurrent_requests=0 after coercion of -5, got {s.max_concurrent_requests}"  # type: ignore[attr-defined]
    )
    warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "max_concurrent_requests" in m.lower()
        or "back_pressure" in m.lower()
        or "negative" in m.lower()
        or "invalid" in m.lower()
        for m in warn_msgs
    ), f"Expected a WARNING about negative max_concurrent_requests, got: {warn_msgs}"


# ---------------------------------------------------------------------------
# Test 9 — Settings knobs exist with correct defaults
# ---------------------------------------------------------------------------


def test_settings_knobs_defaults() -> None:
    """Settings.max_concurrent_requests=0 (default) and back_pressure_retry_after_seconds=1 (default)."""
    s = Settings()
    assert s.max_concurrent_requests == 0, (  # type: ignore[attr-defined]
        f"Expected default max_concurrent_requests=0, got {s.max_concurrent_requests}"  # type: ignore[attr-defined]
    )
    assert s.back_pressure_retry_after_seconds == 1, (  # type: ignore[attr-defined]
        f"Expected default back_pressure_retry_after_seconds=1, got {s.back_pressure_retry_after_seconds}"  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# Test 10 — negative retry_after coerced to 0, boot succeeds, WARN emitted
# ---------------------------------------------------------------------------


def test_negative_retry_after_coerced_to_zero(caplog: pytest.LogCaptureFixture) -> None:
    """Negative GATEWAY_BACK_PRESSURE_RETRY_AFTER_SECONDS → coerced to 0, boot succeeds, WARN logged.

    A negative Retry-After value is RFC-invalid; the validator coerces it to 0
    (no delay hint) rather than crashing startup, consistent with the
    max_concurrent_requests negative→0 convention.
    """
    with caplog.at_level(logging.WARNING):
        s = Settings(back_pressure_retry_after_seconds=-3)  # type: ignore[call-arg]

    assert s.back_pressure_retry_after_seconds == 0, (  # type: ignore[attr-defined]
        f"Expected back_pressure_retry_after_seconds=0 after coercion of -3, "
        f"got {s.back_pressure_retry_after_seconds}"  # type: ignore[attr-defined]
    )
    warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "retry_after" in m.lower()
        or "back_pressure" in m.lower()
        or "negative" in m.lower()
        or "invalid" in m.lower()
        for m in warn_msgs
    ), f"Expected a WARNING about negative back_pressure_retry_after_seconds, got: {warn_msgs}"
