#!/usr/bin/env python3
"""v19 Reliability stub — stdlib HTTP stub for live verification.

Speaks the OpenRouter chat-completions surface on :9930 (localhost only).
Handles all v19 reliability checks:
  C1 RETRY:     model v19/retry-a → 503 on first attempt, 200 on subsequent
  C2 FALLBACK:  model v19/fb-a (candidate A) → 400 context_length_exceeded
                model v19/fb-b (candidate B) → 200 OK
  C3 STREAM:    model v19/stream-a → 500 before first SSE byte (pre-first-byte fail)
                model v19/stream-b → normal SSE stream
  C4 CACHE:     model v19/cache-main → normal 200 (exact repeat = cache hit)
                model v19/embed → embeddings response for vector cache internals
                NOTE: embed model receives {"model": "v19/embed", "input": text}
                      via the OpenRouter facade's complete() call; we detect "input"
                      and return {"data": [{"embedding": [...]}]} (deterministic vector)

Control endpoints:
  GET  /__health                        — readiness probe
  GET  /__counters                      — per-model call counter snapshot
  POST /__reset                         — reset retry counters (for double-pass)

Binding:  127.0.0.1:9930  (NEVER 0.0.0.0 — security requirement from §1)
Protocol: HTTP/1.1, stdlib http.server

Usage:
    The stub is started by scripts/live_v19_verify.py in a daemon thread.
    It can also be run standalone:
        python scripts/v19_reliability_stub.py
"""

from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9930

# v19 model ids used across checks (mirrored in live_v19_verify.py and overlay)
V19_RETRY_A = "v19/retry-a"           # C1: 503 first attempt → 200 retry
V19_FB_A = "v19/fb-a"                 # C2: context-window 400 → triggers fallback
V19_FB_B = "v19/fb-b"                 # C2: 200 OK (fallback candidate)
V19_STREAM_A = "v19/stream-a"         # C3: 500 pre-first-byte → streaming resilience fallover
V19_STREAM_B = "v19/stream-b"         # C3: 200 normal SSE stream (fallback candidate)
V19_CACHE = "v19/cache-main"          # C4: normal 200 (gateway-side cache hits on repeat)
V19_EMBED = "v19/embed"               # C4: embed model for vector cache internal calls

# Deterministic embedding vector — all dimensions equal so cosine(a, a) = 1.0.
# For "near-duplicate" testing we return the SAME vector regardless of input,
# which means any second call (even different text) will have cosine=1.0 >= 0.95.
# The gateway's vector cache deduplicates per (tenant, model, first-user-msg),
# so a second DISTINCT message key is stored separately but the vector similarity
# equals 1.0 → vector_hit for near-duplicate inputs.
_EMBED_DIM = 16
_EMBED_VECTOR: list[float] = [1.0 / math.sqrt(_EMBED_DIM)] * _EMBED_DIM

# ---------------------------------------------------------------------------
# Per-model call counters and retry state (module-level mutable state)
# ---------------------------------------------------------------------------

_call_counters: dict[str, int] = {}
_counters_lock = threading.Lock()

# Per-request-key retry state for C1 (tracks call count per unique run_id marker)
# Key: (model_id, request_marker) → call count
# The request_marker is extracted from the first user message content.
_retry_state: dict[tuple[str, str], int] = {}
_retry_lock = threading.Lock()


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
    with _retry_lock:
        _retry_state.clear()


def _get_retry_count(model_id: str, marker: str) -> int:
    with _retry_lock:
        return _retry_state.get((model_id, marker), 0)


def _increment_retry(model_id: str, marker: str) -> int:
    with _retry_lock:
        key = (model_id, marker)
        _retry_state[key] = _retry_state.get(key, 0) + 1
        return _retry_state[key]


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _chat_ok(model_id: str, content: str = "ok") -> bytes:
    """Build a well-formed non-streaming chat completion response."""
    body = {
        "id": f"stub-v19-{int(time.time())}",
        "object": "chat.completion",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        },
    }
    return json.dumps(body).encode()


def _chat_sse_ok(model_id: str) -> bytes:
    """Build a complete SSE stream for a streaming chat completion."""
    stub_id = f"stub-v19-{int(time.time())}"
    frames = [
        json.dumps({
            "id": stub_id, "model": model_id,
            "choices": [{"delta": {"role": "assistant", "content": "ok from "},
                         "finish_reason": None, "index": 0}],
        }),
        json.dumps({
            "id": stub_id, "model": model_id,
            "choices": [{"delta": {"content": model_id},
                         "finish_reason": None, "index": 0}],
        }),
        json.dumps({
            "id": stub_id, "model": model_id,
            "choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }),
        "[DONE]",
    ]
    return b"".join(f"data: {f}\n\n".encode() for f in frames)


def _context_window_error(model_id: str) -> bytes:
    """Build a context_length_exceeded 400 response (triggers C2 error-aware fallback)."""
    body = {
        "error": {
            "message": "This model's maximum context length is 8192 tokens. "
                       "Please reduce the length of the messages.",
            "type": "invalid_request_error",
            "code": "context_length_exceeded",
        }
    }
    return json.dumps(body).encode()


def _embed_response() -> bytes:
    """Build an embeddings response (for vector cache internal calls via OpenRouter facade).

    The OpenRouter facade calls complete({"model": "v19/embed", "input": text}).
    deps.py reads resp.get("data")[0].get("embedding") from the response.
    We return the deterministic _EMBED_VECTOR regardless of input — this ensures
    cosine(vec1, vec2) = 1.0 >= 0.95 threshold for any two prompts.
    """
    body = {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": 0,
                "embedding": _EMBED_VECTOR,
            }
        ],
        "model": V19_EMBED,
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }
    return json.dumps(body).encode()


def _extract_request_marker(payload: dict[str, Any]) -> str:
    """Extract a stable identifier from the request payload for per-request retry tracking.

    For chat requests, uses the first user message content.
    For embedding requests (has "input"), uses the input text.
    Falls back to a counter-based marker if content is not extractable.
    """
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    return content[:128]  # cap length
    inp = payload.get("input")
    if isinstance(inp, str):
        return inp[:128]
    # Fallback: monotonic timestamp bucket (1-second buckets = one retry chance per second)
    return str(int(time.time()))


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    """HTTP handler for the v19 reliability stub."""

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send_json(self, status: int, body: bytes | dict[str, Any], *,
                   content_type: str = "application/json") -> None:
        if isinstance(body, dict):
            raw = json.dumps(body).encode()
        else:
            raw = body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/__health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/__counters":
            self._send_json(200, _get_counters())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/__reset":
            _reset_counters()
            self._send_json(200, {"ok": True})
        elif self.path in ("/api/v1/chat/completions", "/v1/chat/completions"):
            self._handle_completions()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_completions(self) -> None:
        """POST /api/v1/chat/completions — route by model id and apply v19 behavior."""
        raw = self._read_body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid json: {exc}"})
            return

        model_id: str = payload.get("model", "unknown")
        is_stream: bool = bool(payload.get("stream", False))
        has_input: bool = "input" in payload  # embedding call via OpenRouter facade

        # Always increment global call counter
        _increment_counter(model_id)

        # ── C4/EMBED: embedding call (has "input" key, not "messages") ──────────
        # The OpenRouter facade ignores the path and calls complete(payload).
        # deps.py sends {"model": V19_EMBED, "input": text_to_embed}.
        if has_input or model_id == V19_EMBED:
            self._send_json(200, _embed_response())
            return

        # ── C1 RETRY: first attempt 503, then 200 ────────────────────────────────
        if model_id == V19_RETRY_A:
            marker = _extract_request_marker(payload)
            call_count = _increment_retry(model_id, marker)
            if call_count <= 1:
                # First attempt → 503 (retryable upstream error)
                self._send_json(503, {"error": {"message": "service unavailable", "code": "service_unavailable"}})
                return
            else:
                # Subsequent attempt → 200 (retry succeeds)
                self._send_json(200, _chat_ok(model_id, f"ok on retry {call_count}"))
                return

        # ── C2 FALLBACK CANDIDATE A: context-window 400 ────────────────────────
        if model_id == V19_FB_A:
            self._send_json(400, _context_window_error(model_id))
            return

        # ── C2 FALLBACK CANDIDATE B: 200 OK ────────────────────────────────────
        if model_id == V19_FB_B:
            self._send_json(200, _chat_ok(model_id))
            return

        # ── C3 STREAMING CANDIDATE A: 500 (pre-first-byte failure) ─────────────
        # Must fail before emitting ANY SSE byte. Return 500 immediately.
        if model_id == V19_STREAM_A:
            if is_stream:
                # Streaming request: return 500 status code before any SSE chunk.
                # The gateway treats 5xx response on streaming as transport failure
                # (UpstreamUnavailableError raised when reading the response body).
                self._send_json(500, {"error": {"message": "upstream unavailable (stub injected pre-byte failure)", "code": "service_unavailable"}})
            else:
                # Non-streaming variant: just 500
                self._send_json(500, {"error": "stub injected failure for v19/stream-a"})
            return

        # ── C3 STREAMING CANDIDATE B: normal SSE stream ─────────────────────────
        if model_id == V19_STREAM_B:
            if is_stream:
                body = _chat_sse_ok(model_id)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json(200, _chat_ok(model_id))
            return

        # ── C4 CACHE: normal 200 (gateway caches on repeat) ────────────────────
        if model_id == V19_CACHE:
            self._send_json(200, _chat_ok(model_id))
            return

        # ── Default: 200 OK for any unknown model ───────────────────────────────
        self._send_json(200, _chat_ok(model_id))

    def log_message(self, fmt: str, *args: object) -> None:
        # Log method + path only; never log headers or body (may carry API keys)
        print(f"  [v19_reliability_stub] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# Server lifecycle helpers (used by live_v19_verify.py)
# ---------------------------------------------------------------------------

def make_stub_server() -> HTTPServer:
    """Create and return a configured stub HTTPServer on 127.0.0.1:9930 (not yet started)."""
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
    print(f"v19 reliability stub listening on {STUB_HOST}:{STUB_PORT}")
    print("GET  /__health                                      readiness")
    print("GET  /__counters                                    per-model call counts")
    print("POST /__reset                                       reset retry/counters")
    print("POST /api/v1/chat/completions                       v19 behaviors:")
    print(f"  model={V19_RETRY_A}     → 503 (attempt 1), 200 (attempt 2+)    [C1 RETRY]")
    print(f"  model={V19_FB_A}        → 400 context_length_exceeded           [C2 FALLBACK-A]")
    print(f"  model={V19_FB_B}        → 200 OK                                [C2 FALLBACK-B]")
    print(f"  model={V19_STREAM_A}   → 500 pre-first-byte                     [C3 STREAM-A]")
    print(f"  model={V19_STREAM_B}   → 200 SSE stream                         [C3 STREAM-B]")
    print(f"  model={V19_CACHE}      → 200 OK (gateway caches repeat)         [C4 CACHE]")
    print(f"  model={V19_EMBED}      → embed vector response                  [C4 VECTOR]")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
