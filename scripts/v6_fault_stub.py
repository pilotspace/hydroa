#!/usr/bin/env python3
"""Fault-injecting upstream stub for v6 live verification.

# SKELETON — BUILD PHASE COMPLETES THIS FILE
# Status: DRAFT as of v6-live-verify spec phase. The structure, port contract,
# and fault-table API shape are final (frozen in TASK.md §3). The streaming
# paths and per-model counters need completion in BUILD.

Speaks the OpenRouter chat-completions surface on :9920 (localhost only).
Exposes a /__faults control endpoint to configure per-model behavior at runtime.

Binding:  127.0.0.1:9920  (NEVER 0.0.0.0 — security requirement from §1)
Protocol: HTTP/1.1, stdlib http.server (same idiom as live_v5_verify.py IdP mocks)

Fault table API (POST /__faults):
  {"model": "<str>", "behavior": <behavior>}

  <behavior> one of:
    "ok"                              — normal successful response
    "fail_5xx"                        — HTTP 500
    {"fail_n": N}                     — HTTP 500 for first N calls, then "ok"
    {"status": 429, "retry_after": s} — HTTP 429 with Retry-After: <s>
    "stream_cut"                      — one SSE chunk then connection close

Completions API (POST /api/v1/chat/completions):
  Routes by request body "model" field. Default (model not in table): "ok".
  See §3 contract for full response shapes.

Usage:
    The stub is started by scripts/live_v6_verify.py in a daemon thread.
    It can also be run standalone for manual testing:
        python scripts/v6_fault_stub.py
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9920

# ---------------------------------------------------------------------------
# Fault table and per-model call counters (module-level mutable state)
# ---------------------------------------------------------------------------

# _fault_table: model_id -> behavior value
# behavior value is one of the shapes defined in §3 FAULT STUB CONTRACT
_fault_table: dict[str, Any] = {}
_fault_table_lock = threading.Lock()

# _call_counters: model_id -> int (counts calls since last fault config)
_call_counters: dict[str, int] = {}
_counters_lock = threading.Lock()


def set_fault(model_id: str, behavior: Any) -> None:
    """Set or update the fault for a model id. Resets the call counter."""
    with _fault_table_lock:
        _fault_table[model_id] = behavior
    with _counters_lock:
        _call_counters[model_id] = 0


def get_behavior(model_id: str) -> Any:
    """Return the current behavior for model_id; default "ok" if not in table."""
    with _fault_table_lock:
        return _fault_table.get(model_id, "ok")


def increment_and_get_count(model_id: str) -> int:
    """Atomically increment call counter for model_id and return the new count."""
    with _counters_lock:
        _call_counters[model_id] = _call_counters.get(model_id, 0) + 1
        return _call_counters[model_id]


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _ok_json_body(model_id: str) -> bytes:
    """Build a well-formed non-streaming completions response body (§3 contract)."""
    body = {
        "id": f"stub-{int(time.time())}",
        "object": "chat.completion",
        "model": model_id,
        "choices": [
            {
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        },
    }
    return json.dumps(body).encode()


def _ok_sse_body(model_id: str) -> bytes:
    """Build a well-formed streaming SSE response body (§3 contract).

    Three frames: role delta, content+usage delta, [DONE].
    """
    # SKELETON: BUILD phase assembles the byte sequence.
    # Shape from §3:
    #   data: <delta chunk with role>
    #   data: <delta chunk with usage>
    #   data: [DONE]
    stub_id = f"stub-{int(time.time())}"
    frames = [
        json.dumps({
            "id": stub_id, "model": model_id,
            "choices": [{"delta": {"role": "assistant", "content": "ok"},
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


def _stream_cut_body(model_id: str) -> bytes:
    """Build a stream_cut SSE response — one chunk then EOF (no [DONE] frame).

    This simulates an upstream that starts streaming then closes the connection
    after the first byte is forwarded. Used to verify C5 (stream hard boundary).
    """
    stub_id = f"stub-{int(time.time())}"
    first_chunk = json.dumps({
        "id": stub_id, "model": model_id,
        "choices": [{"delta": {"role": "assistant", "content": "st"},
                     "finish_reason": None, "index": 0}],
    })
    # Intentionally no [DONE] frame — connection closed after first chunk
    return f"data: {first_chunk}\n\n".encode()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    """HTTP handler for the fault-injecting upstream stub."""

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send_json(self, status: int, body: dict | bytes, *, content_type: str = "application/json") -> None:
        if isinstance(body, dict):
            raw = json.dumps(body).encode()
        else:
            raw = body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/__faults":
            self._handle_faults()
        elif self.path == "/api/v1/chat/completions":
            self._handle_completions()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_faults(self) -> None:
        """POST /__faults — update the fault table for a model id.

        Request body: {"model": "<str>", "behavior": <behavior>}
        Response: 200 {"ok": true}
        """
        raw = self._read_body()
        try:
            payload = json.loads(raw)
            model_id = payload["model"]
            behavior = payload["behavior"]
        except (json.JSONDecodeError, KeyError) as exc:
            self._send_json(400, {"error": f"bad request: {exc}"})
            return

        set_fault(model_id, behavior)
        self._send_json(200, {"ok": True})

    def _handle_completions(self) -> None:
        """POST /api/v1/chat/completions — route by model id and apply fault behavior.

        SKELETON: streaming path needs completion in BUILD.
        Shape per §3 FAULT STUB CONTRACT.
        """
        raw = self._read_body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid json: {exc}"})
            return

        model_id: str = payload.get("model", "unknown")
        is_stream: bool = bool(payload.get("stream", False))
        behavior = get_behavior(model_id)
        call_count = increment_and_get_count(model_id)

        # --- Resolve effective behavior for fail_n ---
        if isinstance(behavior, dict) and "fail_n" in behavior:
            n = behavior["fail_n"]
            if call_count <= n:
                effective_behavior: Any = "fail_5xx"
            else:
                effective_behavior = "ok"
        else:
            effective_behavior = behavior

        # --- Dispatch ---
        if effective_behavior == "ok":
            if is_stream:
                body = _ok_sse_body(model_id)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json(200, _ok_json_body(model_id))

        elif effective_behavior == "fail_5xx":
            self._send_json(500, {"error": "stub upstream error"})

        elif effective_behavior == "stream_cut":
            # Stream-cut: emit one chunk then close — no [DONE] frame (§3)
            body = _stream_cut_body(model_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # Deliberately no Content-Length so the connection drop is the signal
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except BrokenPipeError:
                pass  # client disconnected — expected

        elif isinstance(effective_behavior, dict) and effective_behavior.get("status") == 429:
            retry_after = effective_behavior.get("retry_after", 1)
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", str(retry_after))
            body = json.dumps({"error": "rate limited by stub"}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            # Unknown behavior — treat as 500 to surface misconfiguration
            self._send_json(500, {"error": f"unknown stub behavior: {effective_behavior!r}"})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"  [v6_fault_stub] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# Server lifecycle helpers (used by live_v6_verify.py)
# ---------------------------------------------------------------------------

def make_stub_server() -> HTTPServer:
    """Create and return a configured stub HTTPServer (not yet started)."""
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
    print(f"v6 fault stub listening on {STUB_HOST}:{STUB_PORT}")
    print("POST /__faults  to configure per-model behavior")
    print("POST /api/v1/chat/completions  to invoke completions")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
