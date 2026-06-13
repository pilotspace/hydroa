#!/usr/bin/env python3
"""Gemini embed + :countTokens stub for v12 live verification.

Serves the Gemini embed wire format on :9926 (localhost only).
Exposes a GET /__health endpoint for readiness polling.

Binding:  127.0.0.1:9926  (NEVER 0.0.0.0 — security requirement from §1)
Protocol: HTTP/1.1, stdlib http.server (same idiom as v9_provider_stub.py)

Provider surfaces:
  POST /v1beta/models/{m}:countTokens      — Gemini token count (EXACT billing)
  POST /v1beta/models/{m}:embedContent     — Gemini single embedding
  POST /v1beta/models/{m}:batchEmbedContents — Gemini batch embeddings
  GET  /__health                           — readiness probe

Auth:  x-goog-api-key header (NEVER ?key= — keeps the secret out of URLs/logs)

Usage:
    The stub is started by scripts/live_v12_verify.py in a daemon thread.
    It can also be run standalone for manual testing:
        python scripts/v12_embed_stub.py
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9926

# EXACT token count returned by :countTokens (contract FROZEN @ v1).
# C1 asserts usage_records.total_tokens == 42, NOT ceil(chars/4).
_EXACT_TOKEN_COUNT = 42

# Embedding values returned for any embed request.
_EMBED_VALUES = [0.1, 0.2, 0.3]

# Regex to parse /v1beta/models/<model>:<verb>
_GEMINI_PATH_RE = re.compile(r"^/v1beta/models/([^:]+):([^?]+)")


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _count_tokens_response() -> bytes:
    """Build a :countTokens 200 response → {"totalTokens": 42} (EXACT count, FROZEN)."""
    return json.dumps({"totalTokens": _EXACT_TOKEN_COUNT}).encode()


def _gemini_embed_response() -> bytes:
    """Build a Gemini embedContent 200 response per §3 contract."""
    body = {"embedding": {"values": _EMBED_VALUES}}
    return json.dumps(body).encode()


def _gemini_batch_embed_response(n_requests: int) -> bytes:
    """Build a Gemini batchEmbedContents 200 response — one embedding per request."""
    body = {"embeddings": [{"values": _EMBED_VALUES} for _ in range(max(n_requests, 1))]}
    return json.dumps(body).encode()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    """HTTP handler for the v12 Gemini embed stub."""

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
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]  # strip query string for routing
        m = _GEMINI_PATH_RE.match(path)
        if m:
            self._handle_gemini(m.group(1), m.group(2))
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_gemini(self, model_id: str, verb: str) -> None:
        """POST /v1beta/models/{model}:<verb> — route by verb."""
        # model_id is unused in responses (stub is stateless) but captured for future use
        _ = model_id
        if verb == "countTokens":
            self._read_body()  # consume body; do not log (may carry API key context)
            self._send_json(200, _count_tokens_response())
        elif verb == "embedContent":
            self._read_body()  # consume body; do not log
            self._send_json(200, _gemini_embed_response())
        elif verb == "batchEmbedContents":
            raw = self._read_body()
            try:
                payload = json.loads(raw)
                n_requests = len(payload.get("requests", []))
            except (json.JSONDecodeError, TypeError):
                n_requests = 0
            self._send_json(200, _gemini_batch_embed_response(n_requests))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        # Log path only — NEVER headers (which may carry x-goog-api-key)
        _ = fmt, args
        print(f"  [v12_embed_stub] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# Server lifecycle helpers (used by live_v12_verify.py)
# ---------------------------------------------------------------------------


def make_stub_server() -> HTTPServer:
    """Create and return a configured stub HTTPServer on 127.0.0.1:9926 (not yet started)."""
    return HTTPServer((STUB_HOST, STUB_PORT), _StubHandler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    """Start server.serve_forever() in a daemon thread. Returns the thread."""
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Standalone entry point (manual testing only)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    srv = make_stub_server()
    print(f"v12 embed stub listening on {STUB_HOST}:{STUB_PORT}")
    print("GET  /__health                                      readiness check")
    print("POST /v1beta/models/{m}:countTokens                → {totalTokens: 42}")
    print("POST /v1beta/models/{m}:embedContent               Gemini single embedding")
    print("POST /v1beta/models/{m}:batchEmbedContents         Gemini batch embeddings")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
