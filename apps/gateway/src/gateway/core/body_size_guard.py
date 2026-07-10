"""Request body-size cap ASGI middleware (edge-input-hardening TASK.md §3 Part C FROZEN @ v1).

Bounds every request body's size at the application layer (Envoy enforces a coarser outer
ceiling independently — see infra/envoy/*.yaml's ``envoy.filters.http.buffer`` filter).
Mirrors the existing pre-auth pattern (``token_router.py`` / ``device_authorize_router.py`` /
``device_approval_router.py``'s ``_MAX_BODY_BYTES`` dual declared-vs-actual check),
generalized into ONE shared middleware covering the previously-uncapped ``/v1/*`` +
``/admin/*`` traffic:

  1. A truthful oversized ``Content-Length`` header -> reject BEFORE ``receive`` is ever
     called (no body bytes read at all, no downstream handler invoked).
  2. A missing/understated ``Content-Length`` with an actually-oversized streamed body ->
     reject once the running byte count crosses the cap, mid-stream — the partial body is
     discarded and no downstream handler ever observes it (the overage is signalled by
     raising out of the wrapped ``receive`` awaitable, unwinding whatever coroutine inside
     the downstream app was reading the body).

``route_caps`` is a prefix -> max_bytes mapping; the LONGEST matching prefix wins (so a more
specific route, e.g. ``/v1/audio/``, overrides the wider ``/v1/`` default). Anything unmatched
falls back to ``default_cap`` — fail-closed to the smaller cap, never unlimited.

Registered in ``main.py`` AFTER ``GlobalBackPressureMiddleware`` so it becomes the new
OUTERMOST layer (Starlette's ``add_middleware`` inserts at index 0; the LAST call wins as
outermost) — a request too large to even want back-pressure accounting is rejected before
that accounting runs, and well before auth/routing/governance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PROBLEM_CONTENT_TYPE = b"application/problem+json"


class _BodyTooLarge(Exception):
    """Internal control-flow signal: the streamed body crossed the cap mid-read."""


def _cap_for_path(path: str, route_caps: Mapping[str, int], default_cap: int) -> int:
    """Return the byte cap for *path* — longest matching prefix in ``route_caps`` wins."""
    best_prefix = ""
    best_cap = default_cap
    for prefix, cap in route_caps.items():
        if path.startswith(prefix) and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_cap = cap
    return best_cap


class BodySizeLimitMiddleware:
    """Pure-ASGI middleware — rejects a request whose body exceeds its route's byte cap.

    Args:
        app: the downstream ASGI application to wrap.
        route_caps: prefix -> max_bytes mapping; longest-prefix-match wins.
        default_cap: applied to any path with no matching prefix (fail-closed, never
            unlimited).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        route_caps: Mapping[str, int],
        default_cap: int,
    ) -> None:
        self._app = app
        self._route_caps = dict(route_caps)
        self._default_cap = default_cap

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path: str = scope.get("path", "") or ""
        cap = _cap_for_path(path, self._route_caps, self._default_cap)

        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        declared: int | None = None
        for key, value in headers:
            if key.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                break

        # ── 1. Truthful oversized Content-Length -> reject BEFORE reading any body ──
        # `receive` is never called; no downstream handler / governance / billing runs.
        if declared is not None and declared > cap:
            await self._send_413(send, cap)
            return

        # ── 2. Wrap `receive` to catch a missing/lying Content-Length mid-stream ────
        running_total = 0

        async def _guarded_receive() -> Message:
            nonlocal running_total
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                running_total += len(body)
                if running_total > cap:
                    # Raising here (instead of returning the message) unwinds the
                    # downstream app's in-flight body read — no handler ever sees an
                    # oversized body, and the partial body is discarded with it.
                    raise _BodyTooLarge
            return message

        try:
            await self._app(scope, _guarded_receive, send)
        except _BodyTooLarge:
            await self._send_413(send, cap)

    async def _send_413(self, send: Send, cap: int) -> None:
        """Send a 413 problem+json response matching ``ERR_REQUEST_BODY_TOO_LARGE``."""
        body: dict[str, Any] = {
            "type": "about:blank",
            "title": "Request body exceeds the maximum allowed size",
            "status": 413,
            "code": "ERR_REQUEST_BODY_TOO_LARGE",
            "detail": f"Request body exceeds the {cap}-byte limit for this route.",
        }
        body_bytes = json.dumps(body).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", _PROBLEM_CONTENT_TYPE),
                    (b"content-length", str(len(body_bytes)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body_bytes, "more_body": False})


__all__ = ["BodySizeLimitMiddleware"]
