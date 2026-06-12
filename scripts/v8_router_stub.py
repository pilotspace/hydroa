#!/usr/bin/env python3
"""Multi-deployment router stub for v8 live verification.

Speaks the OpenRouter chat-completions surface on :9922 (localhost only).
Exposes a /__faults control endpoint (per-model fault injection) and a
GET /__counters endpoint (per-model call counter readout — v8 new).

Binding:  127.0.0.1:9922  (NEVER 0.0.0.0 — security requirement from §1)
Protocol: HTTP/1.1, stdlib http.server (same idiom as v6_fault_stub.py)

Fault table API (POST /__faults):
  {"model": "<str>", "behavior": <behavior>}

  <behavior> one of:
    "ok"           — normal successful 200 response (default)
    "fail_5xx"     — HTTP 500
    {"fail_n": N}  — HTTP 500 for first N calls, then "ok"

Counter readout API (GET /__counters):
  -> 200 {"<model_id>": <int calls since last fault-config>, ...}

Completions API (POST /api/v1/chat/completions):
  Routes by request body "model" field. Default (model not in table): "ok".
  "ok" 200 body: echoes the served model id in the "model" field and carries
  usage.{prompt_tokens, completion_tokens, total_tokens} all > 0 (§3 contract).

Usage:
    The stub is started by scripts/live_v8_verify.py in a daemon thread.
    It can also be run standalone for manual testing:
        python scripts/v8_router_stub.py
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
STUB_PORT = 9922

# ---------------------------------------------------------------------------
# Fault table and per-model call counters (module-level mutable state)
# ---------------------------------------------------------------------------

# _fault_table: model_id -> behavior value
# behavior value is one of: "ok" | "fail_5xx" | {"fail_n": N}
_fault_table: dict[str, Any] = {}
_fault_table_lock = threading.Lock()

# _call_counters: model_id -> int (counts calls since last fault config / reset)
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


def get_counters() -> dict[str, int]:
    """Return a snapshot of the current call counters dict."""
    with _counters_lock:
        return dict(_call_counters)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _ok_json_body(model_id: str) -> bytes:
    """Build a well-formed non-streaming completions response body (§3 contract).

    The model field ECHOES the served model_id (so the gateway / billing layer
    records the correct deployment id, not the alias).  usage carries
    prompt_tokens>0, completion_tokens>0 so the flusher writes a non-zero cost.
    """
    body = {
        "id": f"stub-v8-{int(time.time())}",
        "object": "chat.completion",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": f"ok from {model_id}"},
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


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    """HTTP handler for the v8 multi-deployment router stub."""

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send_json(self, status: int, body: dict[str, Any] | bytes, *,
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
        if self.path == "/__counters":
            self._handle_counters()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/__faults":
            self._handle_faults()
        elif self.path == "/api/v1/chat/completions":
            self._handle_completions()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_counters(self) -> None:
        """GET /__counters — return per-model call counter snapshot as JSON.

        Response: 200 {"<model_id>": <int>, ...}
        This is the v8-new endpoint; the v6 stub lacks it.
        """
        self._send_json(200, get_counters())

    def _handle_faults(self) -> None:
        """POST /__faults — update the fault table for a model id.

        Request body: {"model": "<str>", "behavior": <behavior>}
        Response: 200 {"ok": true}
        Setting a fault also resets the call counter for that model.
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

        Behaviors:
          "ok"          -> 200 with echoed model id and usage > 0
          "fail_5xx"    -> 500
          {"fail_n": N} -> 500 for first N calls, then "ok"

        Every call (success or fail) increments the model counter.
        """
        raw = self._read_body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"invalid json: {exc}"})
            return

        model_id: str = payload.get("model", "unknown")
        behavior = get_behavior(model_id)
        call_count = increment_and_get_count(model_id)

        # --- Resolve effective behavior for fail_n ---
        effective_behavior: Any
        if isinstance(behavior, dict) and "fail_n" in behavior:
            n = behavior["fail_n"]
            effective_behavior = "fail_5xx" if call_count <= n else "ok"
        else:
            effective_behavior = behavior

        # --- Dispatch ---
        if effective_behavior == "ok":
            self._send_json(200, _ok_json_body(model_id))

        elif effective_behavior == "fail_5xx":
            self._send_json(500, {"error": "stub upstream error"})

        else:
            # Unknown behavior — treat as 500 to surface misconfiguration
            self._send_json(500, {"error": f"unknown stub behavior: {effective_behavior!r}"})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"  [v8_router_stub] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# Server lifecycle helpers (used by live_v8_verify.py)
# ---------------------------------------------------------------------------

def make_stub_server() -> HTTPServer:
    """Create and return a configured stub HTTPServer on 127.0.0.1:9922 (not yet started)."""
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
    print(f"v8 router stub listening on {STUB_HOST}:{STUB_PORT}")
    print("POST /__faults      to configure per-model behavior")
    print("GET  /__counters    to read per-model call counters")
    print("POST /api/v1/chat/completions  to invoke completions")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
