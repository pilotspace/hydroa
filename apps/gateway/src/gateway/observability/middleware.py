"""RequestIdMiddleware — generates request_id, emits access log, records metrics.

Contract (TASK.md §3):
  - Generates a UUID4 request_id per request via contextvars.
  - Binds request_id to structlog context at request start.
  - Clears structlog contextvars before each request to prevent cross-request leakage.
  - After response: emits one structlog JSON line with event="http_request" and
    fields: method, path, status_code, duration_ms, request_id.
  - tenant_id is NOT bound here; it is bound by the use case layer after auth
    via structlog.contextvars.bind_contextvars(tenant_id=...) so it only appears
    on authenticated paths.
  - Increments gateway_http_requests_total counter with method + exact status_code.
  - Observes gateway_request_duration_seconds histogram with method + status_class.
  - NEVER reads, logs, or passes the Authorization header value.

Implementation note — pure ASGI (not BaseHTTPMiddleware):
  BaseHTTPMiddleware uses anyio.create_task_group internally, which creates a
  child copy of the contextvars context.  Mutations made by the handler (e.g.
  bind_contextvars(tenant_id=...)) are lost when the task exits and are NOT
  visible in the middleware after call_next returns.  A raw ASGI middleware
  class runs in the SAME context as the handler, so tenant_id bound during auth
  is visible when we emit the access log at the end of the request.

Middleware ordering note: this middleware wraps the ASGI app at the outermost
level so it captures the FINAL status code including 401/402/4xx raised by
FastAPI dependency injection (exception handlers resolve before response is sent
back up through middleware layers).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
import structlog.contextvars
from starlette.types import ASGIApp, Receive, Scope, Send

_log = structlog.get_logger(__name__)


def _status_class(status_code: int) -> str:
    """Convert an integer status code to a coarse class string (2xx/4xx/5xx/...)."""
    prefix = status_code // 100
    return f"{prefix}xx"


class RequestIdMiddleware:
    """Pure-ASGI middleware that adds request_id and emits access logs + metrics.

    Implemented as a raw ASGI callable (not BaseHTTPMiddleware) so that
    contextvars mutations made inside the request handler (e.g. binding
    tenant_id after successful auth) are visible when we emit the access log
    after the response is sent.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        # --- request setup ---
        structlog.contextvars.clear_contextvars()
        request_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)

        method: str = scope.get("method", "")
        path: str = scope.get("path", "")
        start = time.perf_counter()

        # Intercept the response status code via a wrapping send callable
        status_code: list[int] = [200]

        async def _send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_code[0] = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, _send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            code = status_code[0]

            # Emit access log — tenant_id present if bound by use case layer during auth
            _log.info(
                "http_request",
                method=method,
                path=path,
                status_code=code,
                duration_ms=round(duration_ms, 3),
            )

            # Update Prometheus metrics — failure must never affect the response
            # scope["app"] is set by Starlette's app.__call__ before routing; safe here
            _app_ref: Any = scope.get("app")
            if _app_ref is not None:
                try:
                    metrics = _app_ref.state.metrics_registry
                    metrics.http_requests_total.labels(
                        method=method,
                        status_code=str(code),
                    ).inc()
                    metrics.request_duration_seconds.labels(
                        method=method,
                        status_class=_status_class(code),
                    ).observe(duration_ms / 1000.0)
                except Exception:  # noqa: S110
                    pass  # Metrics failure must never affect the response
