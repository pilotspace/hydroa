#!/usr/bin/env python3
"""Multi-provider stub for v9 live verification.

Serves OpenRouter, Anthropic, and Google Gemini wire formats on :9923 (localhost only).
Exposes a GET /__health endpoint for readiness polling.

Binding:  127.0.0.1:9923  (NEVER 0.0.0.0 — security requirement from §1)
Protocol: HTTP/1.1, stdlib http.server (same idiom as v8_router_stub.py)

Provider surfaces:
  POST /api/v1/chat/completions        — OpenRouter (OpenAI chat.completion shape)
  POST /v1/messages                    — Anthropic Messages API (+ SSE when body.stream==true)
  POST /v1beta/models/{m}:generateContent         — Gemini non-stream chat
  POST /v1beta/models/{m}:streamGenerateContent   — Gemini SSE chat
  POST /v1beta/models/{m}:embedContent            — Gemini single embedding
  POST /v1beta/models/{m}:batchEmbedContents      — Gemini batch embeddings

Usage:
    The stub is started by scripts/live_v9_verify.py in a daemon thread.
    It can also be run standalone for manual testing:
        python scripts/v9_provider_stub.py
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9923

# Fixed usage values per §3 contract (FROZEN)
_OPENROUTER_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
_ANTHROPIC_INPUT_TOKENS = 7
_ANTHROPIC_OUTPUT_TOKENS = 4
_GEMINI_PROMPT_TOKEN_COUNT = 9
_GEMINI_CANDIDATES_TOKEN_COUNT = 6
_GEMINI_TOTAL_TOKEN_COUNT = 15
_EMBED_VALUES = [0.1, 0.2, 0.3]

# Regex to parse /v1beta/models/<model>:<verb>
_GEMINI_PATH_RE = re.compile(r"^/v1beta/models/([^:]+):([^?]+)")


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def _openrouter_response(model_id: str) -> bytes:
    """Build an OpenAI chat.completion response (OpenRouter surface).

    Echoes the model id; carries usage {5, 3, 8} per §3 contract.
    content = "ok-openrouter" (distinguishes this provider in assertions).
    """
    body = {
        "id": f"stub-v9-or-{int(time.time())}",
        "object": "chat.completion",
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok-openrouter"},
                "finish_reason": "stop",
            }
        ],
        "usage": _OPENROUTER_USAGE,
    }
    return json.dumps(body).encode()


def _anthropic_non_stream_response(model_id: str) -> bytes:
    """Build an Anthropic Messages API non-streaming 200 response per §3."""
    body = {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "content": [{"type": "text", "text": "Hello world"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": _ANTHROPIC_INPUT_TOKENS,
            "output_tokens": _ANTHROPIC_OUTPUT_TOKENS,
        },
    }
    return json.dumps(body).encode()


def _anthropic_sse_response(model_id: str) -> bytes:
    """Build the Anthropic SSE event sequence per §3 contract.

    Sequence matches the _ANTHROPIC_SSE fixture used by the unit suite
    (test_anthropic_provider.py) so the adapter already parses this shape.

    Events emitted:
      message_start (usage.input_tokens=7)
      content_block_start
      content_block_delta x2 (text_delta "Hello" / " world")
      content_block_stop
      message_delta (stop_reason:end_turn, usage.output_tokens=4)
      message_stop
    """
    model_json = json.dumps(model_id)
    events = (
        "event: message_start\n"
        f'data: {{"type":"message_start","message":{{"id":"msg_stub","model":{model_json},'
        f'"usage":{{"input_tokens":{_ANTHROPIC_INPUT_TOKENS},'
        f'"output_tokens":1}}}}}}\n\n'
        "event: content_block_start\n"
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"text","text":""}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":"Hello"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"text_delta","text":" world"}}\n\n'
        "event: content_block_stop\n"
        'data: {"type":"content_block_stop","index":0}\n\n'
        "event: message_delta\n"
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},'
        f'"usage":{{"output_tokens":{_ANTHROPIC_OUTPUT_TOKENS}}}}}\n\n'
        "event: message_stop\n"
        'data: {"type":"message_stop"}\n\n'
    )
    return events.encode()


def _gemini_non_stream_response() -> bytes:
    """Build a Gemini generateContent 200 response per §3 contract."""
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Hello gemini"}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": _GEMINI_PROMPT_TOKEN_COUNT,
            "candidatesTokenCount": _GEMINI_CANDIDATES_TOKEN_COUNT,
            "totalTokenCount": _GEMINI_TOTAL_TOKEN_COUNT,
        },
    }
    return json.dumps(body).encode()


def _gemini_sse_response() -> bytes:
    """Build the Gemini SSE data: sequence per §3 contract.

    Matches the _GEMINI_SSE fixture used by the unit suite so the adapter
    already parses this shape. Three data: frames:
      1. candidates[0] text "Hello"
      2. candidates[0] text " gemini"
      3. candidates[0] finishReason "STOP" + usageMetadata
    """
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "Hello"}], "role": "model"}}]},
        {"candidates": [{"content": {"parts": [{"text": " gemini"}], "role": "model"}}]},
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": ""}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": _GEMINI_PROMPT_TOKEN_COUNT,
                "candidatesTokenCount": _GEMINI_CANDIDATES_TOKEN_COUNT,
                "totalTokenCount": _GEMINI_TOTAL_TOKEN_COUNT,
            },
        },
    ]
    frames = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return frames.encode()


def _gemini_embed_response() -> bytes:
    """Build a Gemini embedContent 200 response per §3 contract."""
    body = {"embedding": {"values": _EMBED_VALUES}}
    return json.dumps(body).encode()


def _gemini_batch_embed_response(n_requests: int) -> bytes:
    """Build a Gemini batchEmbedContents 200 response — one embedding per request."""
    body = {"embeddings": [{"values": _EMBED_VALUES} for _ in range(n_requests)]}
    return json.dumps(body).encode()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    """HTTP handler for the v9 multi-provider stub."""

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send_json(
        self, status: int, body: bytes, *, content_type: str = "application/json"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, status: int, body: bytes) -> None:
        """Send a text/event-stream response."""
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
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

        if path == "/api/v1/chat/completions":
            self._handle_openrouter()
        elif path == "/v1/messages":
            self._handle_anthropic()
        else:
            m = _GEMINI_PATH_RE.match(path)
            if m:
                self._handle_gemini(m.group(1), m.group(2))
            else:
                self.send_response(404)
                self.end_headers()

    def _handle_openrouter(self) -> None:
        """POST /api/v1/chat/completions → OpenAI chat.completion echo with fixed usage."""
        raw = self._read_body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, json.dumps({"error": f"invalid json: {exc}"}).encode())
            return
        model_id: str = payload.get("model", "unknown")
        self._send_json(200, _openrouter_response(model_id))

    def _handle_anthropic(self) -> None:
        """POST /v1/messages → Anthropic message response or SSE stream."""
        raw = self._read_body()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, json.dumps({"error": f"invalid json: {exc}"}).encode())
            return
        model_id: str = payload.get("model", "unknown")
        if payload.get("stream"):
            self._send_sse(200, _anthropic_sse_response(model_id))
        else:
            self._send_json(200, _anthropic_non_stream_response(model_id))

    def _handle_gemini(self, model_id: str, verb: str) -> None:
        """POST /v1beta/models/{model}:<verb> — route by verb."""
        if verb == "generateContent":
            self._read_body()
            self._send_json(200, _gemini_non_stream_response())
        elif verb == "streamGenerateContent":
            self._read_body()
            self._send_sse(200, _gemini_sse_response())
        elif verb == "embedContent":
            self._read_body()  # consume body; do not log (may carry API key context)
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
        # Log path only — NEVER headers (which may carry API keys)
        # fmt/args unused intentionally; suppress via _ prefix pattern
        _ = fmt, args
        print(f"  [v9_provider_stub] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# Server lifecycle helpers (used by live_v9_verify.py)
# ---------------------------------------------------------------------------


def make_stub_server() -> HTTPServer:
    """Create and return a configured stub HTTPServer on 127.0.0.1:9923 (not yet started)."""
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
    print(f"v9 provider stub listening on {STUB_HOST}:{STUB_PORT}")
    print("GET  /__health                                   readiness check")
    print("POST /api/v1/chat/completions                   OpenRouter surface")
    print("POST /v1/messages                               Anthropic (stream or non-stream)")
    print("POST /v1beta/models/{m}:generateContent         Gemini non-stream chat")
    print("POST /v1beta/models/{m}:streamGenerateContent   Gemini SSE chat")
    print("POST /v1beta/models/{m}:embedContent            Gemini single embedding")
    print("POST /v1beta/models/{m}:batchEmbedContents      Gemini batch embeddings")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
