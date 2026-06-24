#!/usr/bin/env python3
"""v35 error-fidelity stub — stdlib HTTP fault injector for live verification.

Speaks the OpenRouter chat-completions surface on 127.0.0.1:9935 (loopback ONLY).
Handles the three v35 error-fidelity models:

  v35/ratelimit-a   -> HTTP 429 + Retry-After: 7  (Finding A)
  v35/stream-fail-a -> HTTP 200 SSE; one valid chunk then graceful FIN close
                       mid-stream (no [DONE]), triggering httpx.RemoteProtocolError
                       in the gateway's stream reader (Finding B + Finding C).
                       Env V35_STUB_DROP_MODE=rst: abruptly reset instead (SO_LINGER=0).
  v35/ok-a          -> HTTP 200 SSE; one chunk then data: [DONE]  (control)

Control endpoints:
  GET  /__health     — 200 {"status":"ok"}
  GET  /__counters   — per-model call counts
  POST /__reset      — zero the counters

Security:
  Binds 127.0.0.1 ONLY. Refuses to start on 0.0.0.0 (assert+sys.exit(1)).
  Never logs request headers or bodies (may carry API keys).

Usage (standalone — normally started by live_v35_verify.py):
    python3 scripts/v35_error_fidelity_stub.py
    curl http://127.0.0.1:9935/__health
    curl -i -X POST http://127.0.0.1:9935/api/v1/chat/completions \\
         -H 'Content-Type: application/json' \\
         -d '{"model":"v35/ratelimit-a","messages":[]}'
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9935

# v35 model ids (mirrored in live_v35_verify.py and the compose overlay)
V35_RATELIMIT_A = "v35/ratelimit-a"   # Finding A: 429 + Retry-After: 7
V35_STREAM_FAIL_A = "v35/stream-fail-a"  # Finding B+C: partial SSE then FIN/RST
V35_OK_A = "v35/ok-a"                 # Control: clean SSE stream with [DONE]

# Retry-After seconds injected on every 429 response — matches §3 CONTRACT freeze
_RETRY_AFTER_S = 7

# ---------------------------------------------------------------------------
# Per-model call counters (module-level mutable state, thread-safe)
# ---------------------------------------------------------------------------

_call_counters: dict[str, int] = {}
_counters_lock = threading.Lock()


def _increment_counter(model_id: str) -> int:
    with _counters_lock:
        _call_counters[model_id] = _call_counters.get(model_id, 0) + 1
        return _call_counters[model_id]


def _get_counters() -> dict[str, int]:
    with _counters_lock:
        return dict(_call_counters)


def _reset_counters() -> None:
    with _counters_lock:
        _call_counters.clear()


# ---------------------------------------------------------------------------
# Response body builders
# ---------------------------------------------------------------------------

def _ratelimit_body() -> bytes:
    """429 error body matching OpenRouter's rate-limit shape."""
    body = {
        "error": {
            "message": "rate-limited",
            "type": "rate_limit_error",
            "code": "rate_limited",
        }
    }
    return json.dumps(body).encode()


def _one_sse_chunk(model_id: str) -> bytes:
    """A single valid OpenAI SSE data frame with a partial content delta."""
    chunk = {
        "id": f"stub-v35-{int(time.time())}",
        "object": "chat.completion.chunk",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "delta": {"content": "partial"},
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


def _done_frame() -> bytes:
    return b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    """HTTP handler for the v35 error-fidelity stub."""

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send_json(
        self,
        status: int,
        body: bytes | dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
        content_type: str = "application/json",
    ) -> None:
        raw = json.dumps(body).encode() if isinstance(body, dict) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/__health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/__counters":
            self._send_json(200, _get_counters())
        elif self.path in ("/api/v1/models", "/api/v1/auth/key"):
            # Handle any probe calls the OpenRouter facade might make at init.
            # In practice OpenRouterCompletionUpstream only calls /chat/completions
            # and /generation, so these are just a safety net.
            self._send_json(200, {"data": [], "status": "ok"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/__reset":
            _reset_counters()
            self._send_json(200, {"ok": True})
        elif self.path in ("/api/v1/chat/completions", "/v1/chat/completions",
                           "/chat/completions"):
            self._handle_completions()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_completions(self) -> None:
        """POST /api/v1/chat/completions — dispatch by model id."""
        raw = self._read_body()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid json: {exc}"})
            return

        model_id: str = str(payload.get("model", "unknown"))
        _increment_counter(model_id)

        # ── Finding A: upstream 429 + Retry-After ────────────────────────────
        if model_id == V35_RATELIMIT_A:
            self._send_json(
                429,
                _ratelimit_body(),
                extra_headers={"Retry-After": str(_RETRY_AFTER_S)},
            )
            return

        # ── Finding B+C: partial SSE then connection drop ────────────────────
        if model_id == V35_STREAM_FAIL_A:
            self._serve_partial_stream_then_drop()
            return

        # ── Control: clean SSE stream ────────────────────────────────────────
        if model_id == V35_OK_A:
            self._serve_clean_stream(model_id)
            return

        # ── Default: echo back a clean 200 for unknown models ────────────────
        self._serve_clean_stream(model_id)

    def _serve_partial_stream_then_drop(self) -> None:
        """Emit one valid SSE chunk then close the connection without [DONE].

        Default mode: graceful FIN close (the kernel sends FIN after we close
        the socket while the response is still "streaming"). From the httpx client's
        perspective this is an httpx.RemoteProtocolError (peer closed while
        chunked-transfer was in progress).

        Env V35_STUB_DROP_MODE=rst: set SO_LINGER(0) before close → TCP RST.
        The gateway catches both as UpstreamUnavailableError.
        """
        chunk = _one_sse_chunk(V35_STREAM_FAIL_A)

        # Write the HTTP/1.1 response head + one SSE data frame.
        # Use chunked transfer-encoding so the peer knows the response is streaming
        # and will detect the premature close (no final 0-length chunk).
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Write the one chunk in chunked-TE format: <hex-len>\r\n<data>\r\n
        hex_len = format(len(chunk), "x")
        self.wfile.write(f"{hex_len}\r\n".encode())
        self.wfile.write(chunk)
        self.wfile.write(b"\r\n")
        try:
            self.wfile.flush()
        except OSError:
            return  # client already gone — that's fine

        # Abrupt drop: close the socket NOW without the final 0-length chunk.
        drop_mode = os.environ.get("V35_STUB_DROP_MODE", "fin").lower()
        try:
            raw_sock: socket.socket = self.connection  # type: ignore[attr-defined]
            if drop_mode == "rst":
                # SO_LINGER(l_onoff=1, l_linger=0) → TCP RST on close
                linger = struct.pack("ii", 1, 0)
                raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            raw_sock.close()
        except OSError:
            pass  # already closed

    def _serve_clean_stream(self, model_id: str) -> None:
        """Emit one SSE content chunk then a clean data: [DONE] frame."""
        chunk = _one_sse_chunk(model_id)
        body = chunk + _done_frame()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        # Log method + path only; never log headers or body (may carry API keys).
        print(f"  [v35_error_fidelity_stub] {self.command} {self.path}", flush=True)


# ---------------------------------------------------------------------------
# Server lifecycle helpers (used by live_v35_verify.py)
# ---------------------------------------------------------------------------

def make_stub_server() -> HTTPServer:
    """Create and return a configured stub HTTPServer on 127.0.0.1:9935 (not started).

    Security: asserts STUB_HOST == '127.0.0.1' and sys.exit(1) if not.
    """
    if STUB_HOST != "127.0.0.1":
        print(
            f"HARD-STOP: stub host {STUB_HOST!r} is not loopback — refusing to start.",
            file=sys.stderr,
        )
        sys.exit(1)
    return HTTPServer((STUB_HOST, STUB_PORT), _StubHandler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    """Start server.serve_forever() in a daemon thread. Returns the thread."""
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Standalone entry point (manual testing / self-test only)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    srv = make_stub_server()
    print(f"v35 error-fidelity stub listening on {STUB_HOST}:{STUB_PORT}")
    print("GET  /__health                 — readiness probe")
    print("GET  /__counters               — per-model call counts")
    print("POST /__reset                  — zero counters")
    print("POST /api/v1/chat/completions  — v35 behaviors:")
    print(f"  model={V35_RATELIMIT_A}    -> 429 + Retry-After: {_RETRY_AFTER_S}  [Finding A]")
    print(f"  model={V35_STREAM_FAIL_A}  -> 200 SSE 1 chunk then FIN/RST drop   [Finding B+C]")
    print(f"  model={V35_OK_A}           -> 200 SSE 1 chunk + [DONE]             [control]")
    print("Env V35_STUB_DROP_MODE=rst     — use TCP RST instead of graceful FIN")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
