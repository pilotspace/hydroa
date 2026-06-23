"""Global back-pressure ASGI middleware (concurrency-load-guard TASK.md §3 FROZEN @ v1).

Caps simultaneous in-flight HTTP requests per worker and sheds excess load with
503 + Retry-After so sustained concurrent agent-coding streams degrade gracefully
instead of exhausting file-descriptors / connections / memory.

Design:
  - Backed by an ``asyncio.BoundedSemaphore`` (per-worker; no external dependency on
    the hot path). Total cluster cap = workers x per-worker cap; documented in config.
  - Non-blocking acquire: if no slot is free RIGHT NOW, shed immediately — do NOT queue
    / wait. This bounds latency on the refusal path and prevents an unbounded waiter queue
    from building up under sustained overload.
  - Slot held for the FULL request: ``await self._app(scope, receive, send)`` returns
    only after a ``StreamingResponse`` body is fully sent (Starlette/ASGI guarantee), so
    releasing in ``finally`` after that await correctly bounds CONCURRENT streams, not just
    request-starts.
  - Symmetric acquire/release in try/finally across ALL exit edges (success, error,
    client disconnect / GeneratorExit / CancelledError) — a slot is NEVER leaked.
  - Disabled default: ``max_concurrent=0`` → ``_sem is None`` → pure pass-through with
    zero accounting overhead; behavior is byte-identical to today.

Middleware ordering in create_app():
  Starlette's ``add_middleware`` inserts at index 0 of ``user_middleware``; the stack is
  built by iterating ``reversed(user_middleware)``, so the LAST ``add_middleware`` call
  becomes the OUTERMOST layer.  ``GlobalBackPressureMiddleware`` is registered AFTER
  ``RequestIdMiddleware`` so it wraps everything (guard runs first on ingress).

Non-blocking acquire mechanism:
  ``asyncio.BoundedSemaphore.locked()`` returns True iff ``_value == 0`` (all slots taken).
  When ``not sem.locked()``, there is at least one slot available and ``sem.acquire()``
  completes SYNCHRONOUSLY on the current event-loop turn (it only suspends when
  ``_value == 0``).  Because asyncio is single-threaded, no other coroutine can steal the
  slot between the ``locked()`` check and the ``acquire()`` call — both happen in the same
  event-loop turn with no ``await`` between them.  This gives true shed-now semantics with
  no reliance on private APIs and no unbounded waiter queue.

  Rejected alternative — ``asyncio.wait_for(sem.acquire(), timeout=0)``: despite the zero
  timeout, asyncio schedules the coroutine and then immediately raises TimeoutError on the
  NEXT iteration, so it always sheds even when slots are free.  Empirically verified in
  Python 3.12.

Contract cites: asyncio.BoundedSemaphore · non-blocking-acquire via locked() guard ·
  503 + Retry-After + ERR_OVERLOADED body · app NOT invoked on shed ·
  in_flight observable · pass-through when disabled/non-http.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

_log = structlog.get_logger(__name__)

_PROBLEM_CONTENT_TYPE = b"application/problem+json"


class GlobalBackPressureMiddleware:
    """Pure-ASGI back-pressure middleware — caps concurrent in-flight HTTP requests.

    Args:
        app: The downstream ASGI application to wrap.
        max_concurrent: Per-worker slot cap. 0 (default) disables the guard (pass-through).
        retry_after_s: Value of the ``Retry-After`` header on 503 shed responses.

    Attributes:
        _sem: ``asyncio.BoundedSemaphore(max_concurrent)`` when enabled; ``None`` when disabled.
        _in_flight: Current number of admitted in-flight requests (observable by tests/metrics).
        _cap: The configured cap (0 when disabled).
    """

    def __init__(self, app: ASGIApp, *, max_concurrent: int = 0, retry_after_s: int = 1) -> None:
        self._app = app
        self._cap = max_concurrent
        self._retry_after_s = retry_after_s
        self._in_flight: int = 0
        # Semaphore is None when disabled (max_concurrent=0) — zero overhead on the hot path.
        self._sem: asyncio.BoundedSemaphore | None = (
            asyncio.BoundedSemaphore(max_concurrent) if max_concurrent > 0 else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry-point.

        Non-HTTP scopes (lifespan, websocket) and the disabled knob (``_sem is None``)
        are forwarded immediately with zero overhead.
        """
        if scope["type"] != "http" or self._sem is None:
            await self._app(scope, receive, send)
            return

        # ── Non-blocking try-acquire ──────────────────────────────────────────────
        # shed-now semantics: if no slot is available RIGHT NOW, return 503 immediately.
        #
        # Mechanism: ``sem.locked()`` is True iff all slots are taken (``_value == 0``).
        # When not locked, ``sem.acquire()`` completes synchronously on THIS event-loop
        # turn — it only suspends when ``_value == 0``.  Because asyncio is single-
        # threaded, no coroutine can interleave between ``locked()`` and ``acquire()``
        # (there is no ``await`` between them), so the slot is guaranteed to be ours.
        # This avoids building an unbounded waiter queue under sustained overload.
        if self._sem.locked():
            await self._send_503(send)
            return

        # Slot is available — acquire it synchronously (no suspension point).
        await self._sem.acquire()

        # ── Slot acquired — hold it for the WHOLE request (incl. stream drain) ───
        self._in_flight += 1
        try:
            await self._app(scope, receive, send)
        finally:
            self._in_flight -= 1
            self._sem.release()

    async def _send_503(self, send: Send) -> None:
        """Send a 503 Service Unavailable shed response with Retry-After.

        Error body matches the project's RFC 9457 problem+json shape (CONVENTIONS.md):
        ``{"type": "about:blank", "title": "...", "status": 503, "code": "ERR_OVERLOADED"}``
        so callers can pattern-match on ``code`` == ``ERR_OVERLOADED``.
        """
        body: dict[str, Any] = {
            "type": "about:blank",
            "title": "Too many concurrent requests",
            "status": 503,
            "code": "ERR_OVERLOADED",
            "detail": (
                f"Gateway is at capacity ({self._cap} concurrent requests per worker). "
                "Retry after the indicated delay."
            ),
        }
        body_bytes = json.dumps(body).encode()
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", _PROBLEM_CONTENT_TYPE),
            (b"retry-after", str(self._retry_after_s).encode()),
            (b"content-length", str(len(body_bytes)).encode()),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body_bytes,
                "more_body": False,
            }
        )
        _log.warning(
            "back_pressure_shed",
            in_flight=self._in_flight,
            cap=self._cap,
            retry_after_s=self._retry_after_s,
        )
