#!/usr/bin/env python3
"""v39 agent-oauth upstream stub — deterministic OpenRouter-shaped provider.

Serves ONE endpoint that accepts ANY Bearer token and returns a canned
OpenAI-compatible chat.completion. Used by the v39 live double-pass instead of
a real provider so the stack needs NO real API key / external network.

Routes served (OpenRouter wire shape):
  POST /api/v1/chat/completions   — accepts any Bearer; returns a canned chat.completion

Control:
  GET  /__health   — 200 {"status": "ok"}
  GET  /__counters — 200 {"chat": <accept_count>}
  POST /__reset    — 200 {"reset": true}; zero the counters (double-pass idempotency)

Binding: 0.0.0.0 (all interfaces) — required so Docker containers can reach it via
  host.docker.internal (OrbStack/Docker Desktop route to host). This differs from the
  v25 credential-oracle stubs (which bind 127.0.0.1 only) because:
  (a) this stub does NOT verify credentials — it accepts any Bearer value;
  (b) it serves only canned responses with NO real secrets;
  (c) it runs on an ephemeral local dev port for a bounded test session only.
  The stub NEVER logs the Bearer value.

Started by scripts/live_v39_verify.py in a daemon thread.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "0.0.0.0"  # All interfaces — required for Docker container → host reachability
STUB_PORT = 9939  # v39-specific port (no clash with v25 stubs)

# Per-verb ACCEPT counters (guarded by a lock for thread-safety)
_COUNTERS: dict[str, int] = {"chat": 0}
_COUNTERS_LOCK = threading.Lock()


def _bump(verb: str) -> None:
    with _COUNTERS_LOCK:
        _COUNTERS[verb] = _COUNTERS.get(verb, 0) + 1


def _snapshot() -> dict[str, int]:
    with _COUNTERS_LOCK:
        return dict(_COUNTERS)


def _reset() -> None:
    with _COUNTERS_LOCK:
        for k in _COUNTERS:
            _COUNTERS[k] = 0


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


def _chat_completion(model_id: str) -> bytes:
    return json.dumps(
        {
            "id": f"stub-v39-{int(time.time())}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok-agent"},
                    "finish_reason": "stop",
                }
            ],
            "usage": _USAGE,
        }
    ).encode()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send_json(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/__health":
            self._send_json(200, json.dumps({"status": "ok"}).encode())
        elif self.path == "/__counters":
            self._send_json(200, json.dumps(_snapshot()).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]

        if path == "/__reset":
            self._read_body()
            _reset()
            self._send_json(200, json.dumps({"reset": True}).encode())
            return

        raw = self._read_body()

        if path == "/api/v1/chat/completions":
            # Accept any Bearer token — this is a stub, not a credential oracle.
            # Bump counter to let the verify script cross-check at least one call landed.
            _bump("chat")
            try:
                model_id = str(json.loads(raw).get("model", "stub-model"))
            except (json.JSONDecodeError, AttributeError):
                model_id = "stub-model"
            self._send_json(200, _chat_completion(model_id))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: object) -> None:
        # Suppress access log — headers carry Bearer; must NEVER be logged.
        _ = args


# ---------------------------------------------------------------------------
# Server lifecycle helpers
# ---------------------------------------------------------------------------


def make_stub_server(port: int = STUB_PORT) -> HTTPServer:
    """Create the stub HTTPServer on 0.0.0.0:port. Not started.

    Binds all interfaces so Docker containers can reach it via host.docker.internal.
    This is a canned-response-only stub (no real secrets, accepts any Bearer).
    """
    return HTTPServer((STUB_HOST, port), _Handler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    """Start server.serve_forever() in a daemon thread. Returns the thread."""
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    srv = make_stub_server()
    print(f"v39 upstream stub listening on {STUB_HOST}:{STUB_PORT}")
    print("  POST /api/v1/chat/completions  (any Bearer → 200 chat.completion)")
    print("  GET  /__health  ·  GET /__counters  ·  POST /__reset")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
