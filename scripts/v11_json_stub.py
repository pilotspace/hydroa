#!/usr/bin/env python3
"""JSON-mode-aware multi-provider stub for v11 live verification.

Serves the Anthropic, Gemini, and OpenRouter wire formats on :9925 (localhost only)
to exercise response_format / structured-output translation. The stub is STATELESS: it
decides what to return by INSPECTING the translated request —
  - Anthropic: if the request carries a tool named "json_output" (the gateway's coercion
    tool for response_format json_schema) → return a tool_use block for that tool whose
    `input` IS the JSON object (the gateway then UNWRAPS it into message.content);
  - Gemini: if generationConfig.responseMimeType is set (response_format) → return a text
    part that IS a JSON string;
  - OpenRouter / no directive → a plain text answer.

Binding:  127.0.0.1:9925  (NEVER 0.0.0.0 — security requirement)
Protocol: HTTP/1.1, stdlib http.server (same idiom as v10_tool_stub.py)

Surfaces:
  POST /api/v1/chat/completions                   — OpenRouter (plain, no rf)
  POST /v1/messages                               — Anthropic Messages (+ SSE)
  POST /v1beta/models/{m}:generateContent         — Gemini chat
  POST /v1beta/models/{m}:streamGenerateContent   — Gemini SSE chat
  GET  /__health                                  — readiness
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement
STUB_PORT = 9925

# Fixed usage values (match the v10 contract so billing assertions are stable)
_OPENROUTER_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
_ANTHROPIC_INPUT_TOKENS = 7
_ANTHROPIC_OUTPUT_TOKENS = 4
_GEMINI_PROMPT = 9
_GEMINI_CANDIDATES = 6
_GEMINI_TOTAL = 15

# The gateway's reserved coercion tool name (see response_format_translation.py).
_COERCION_TOOL_NAME = "json_output"
# The JSON object the stub "produces" for a structured-output request.
_JSON_OBJECT = {"city": "Paris", "temp": 18}
_JSON_STRING = json.dumps(_JSON_OBJECT)

_GEMINI_PATH_RE = re.compile(r"^/v1beta/models/([^:]+):([^?]+)")


# ---------------------------------------------------------------------------
# Request inspection
# ---------------------------------------------------------------------------


def _anthropic_has_coercion_tool(payload: dict[str, Any]) -> bool:
    for tool in payload.get("tools", []):
        if isinstance(tool, dict) and tool.get("name") == _COERCION_TOOL_NAME:
            return True
    return False


def _gemini_has_response_mime_type(payload: dict[str, Any]) -> bool:
    gen = payload.get("generationConfig", {})
    return isinstance(gen, dict) and bool(gen.get("responseMimeType"))


# ---------------------------------------------------------------------------
# Anthropic responses
# ---------------------------------------------------------------------------


def _anthropic_coercion_response(model_id: str) -> bytes:
    body = {
        "id": "msg_json_stub",
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "content": [
            {"type": "tool_use", "id": "toolu_json_1", "name": _COERCION_TOOL_NAME, "input": _JSON_OBJECT}
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": _ANTHROPIC_INPUT_TOKENS, "output_tokens": _ANTHROPIC_OUTPUT_TOKENS},
    }
    return json.dumps(body).encode()


def _anthropic_text_response(model_id: str) -> bytes:
    body = {
        "id": "msg_text_stub",
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "content": [{"type": "text", "text": "plain answer"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": _ANTHROPIC_INPUT_TOKENS, "output_tokens": _ANTHROPIC_OUTPUT_TOKENS},
    }
    return json.dumps(body).encode()


def _anthropic_coercion_sse(model_id: str) -> bytes:
    """SSE stream with a streamed json_output tool_use block (input_json_delta frags).

    The gateway unwraps this into delta.content; finish_reason becomes "stop".
    """
    model_json = json.dumps(model_id)
    # split the JSON string into two fragments for a realistic stream
    frag_a = '{"city":'
    frag_b = '"Paris","temp":18}'
    events = (
        "event: message_start\n"
        f'data: {{"type":"message_start","message":{{"id":"msg_json_stub","model":{model_json},'
        f'"usage":{{"input_tokens":{_ANTHROPIC_INPUT_TOKENS},"output_tokens":1}}}}}}\n\n'
        "event: content_block_start\n"
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"toolu_json_1","name":"json_output"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        f'"delta":{{"type":"input_json_delta","partial_json":{json.dumps(frag_a)}}}}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        f'"delta":{{"type":"input_json_delta","partial_json":{json.dumps(frag_b)}}}}}\n\n'
        "event: content_block_stop\n"
        'data: {"type":"content_block_stop","index":0}\n\n'
        "event: message_delta\n"
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},'
        f'"usage":{{"output_tokens":{_ANTHROPIC_OUTPUT_TOKENS}}}}}\n\n'
        "event: message_stop\n"
        'data: {"type":"message_stop"}\n\n'
    )
    return events.encode()


# ---------------------------------------------------------------------------
# Gemini responses
# ---------------------------------------------------------------------------


def _gemini_usage() -> dict[str, int]:
    return {
        "promptTokenCount": _GEMINI_PROMPT,
        "candidatesTokenCount": _GEMINI_CANDIDATES,
        "totalTokenCount": _GEMINI_TOTAL,
    }


def _gemini_json_response() -> bytes:
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": _JSON_STRING}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": _gemini_usage(),
    }
    return json.dumps(body).encode()


def _gemini_text_response() -> bytes:
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "plain answer"}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": _gemini_usage(),
    }
    return json.dumps(body).encode()


def _gemini_json_sse() -> bytes:
    chunk = {
        "candidates": [
            {
                "content": {"parts": [{"text": _JSON_STRING}], "role": "model"},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": _gemini_usage(),
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


def _openrouter_response(model_id: str) -> bytes:
    body = {
        "id": f"stub-v11-or-{int(time.time())}",
        "object": "chat.completion",
        "model": model_id,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok-openrouter"}, "finish_reason": "stop"}
        ],
        "usage": _OPENROUTER_USAGE,
    }
    return json.dumps(body).encode()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        try:
            return json.loads(self._read_body() or b"{}")
        except json.JSONDecodeError:
            return {}

    def _send_json(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, status: int, body: bytes) -> None:
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
        path = self.path.split("?")[0]
        if path == "/api/v1/chat/completions":
            payload = self._read_json()
            self._send_json(200, _openrouter_response(payload.get("model", "unknown")))
        elif path == "/v1/messages":
            self._handle_anthropic()
        else:
            m = _GEMINI_PATH_RE.match(path)
            if m:
                self._handle_gemini(m.group(2))
            else:
                self.send_response(404)
                self.end_headers()

    def _handle_anthropic(self) -> None:
        payload = self._read_json()
        model_id = payload.get("model", "unknown")
        has_coercion = _anthropic_has_coercion_tool(payload)
        if payload.get("stream"):
            # streaming verify exercises json_schema → always emit the coercion stream
            self._send_sse(200, _anthropic_coercion_sse(model_id))
            return
        if has_coercion:
            self._send_json(200, _anthropic_coercion_response(model_id))
        else:
            self._send_json(200, _anthropic_text_response(model_id))

    def _handle_gemini(self, verb: str) -> None:
        payload = self._read_json()
        has_json = _gemini_has_response_mime_type(payload)
        if verb == "generateContent":
            if has_json:
                self._send_json(200, _gemini_json_response())
            else:
                self._send_json(200, _gemini_text_response())
        elif verb == "streamGenerateContent":
            self._send_sse(200, _gemini_json_sse())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        _ = fmt, args
        print(f"  [v11_json_stub] {self.command} {self.path}")


def make_stub_server() -> HTTPServer:
    return HTTPServer((STUB_HOST, STUB_PORT), _StubHandler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    srv = make_stub_server()
    print(f"v11 json stub listening on {STUB_HOST}:{STUB_PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
