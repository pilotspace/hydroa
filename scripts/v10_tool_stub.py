#!/usr/bin/env python3
"""Tool-aware multi-provider stub for v10 live verification.

Serves the Anthropic, Gemini, and OpenRouter wire formats on :9924 (localhost only)
with FUNCTION-CALLING round-trip support. The stub is STATELESS: it decides what to
return by inspecting the incoming request —
  - if the request already carries a TOOL RESULT (an Anthropic tool_result block /
    a Gemini functionResponse part) → return the FINAL TEXT answer (the 2nd turn);
  - else if the request carries `tools` → return a TOOL CALL (the 1st turn);
  - else → a plain text answer (the no-tools / byte-identical path).

Binding:  127.0.0.1:9924  (NEVER 0.0.0.0 — security requirement)
Protocol: HTTP/1.1, stdlib http.server (same idiom as v9_provider_stub.py)

Surfaces:
  POST /api/v1/chat/completions                   — OpenRouter (plain, no tools)
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
STUB_PORT = 9924

# Fixed usage values (match the v9 contract so billing assertions are stable)
_OPENROUTER_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
_ANTHROPIC_INPUT_TOKENS = 7
_ANTHROPIC_OUTPUT_TOKENS = 4
_GEMINI_PROMPT = 9
_GEMINI_CANDIDATES = 6
_GEMINI_TOTAL = 15

# The single tool the stub "calls" on turn 1.
_TOOL_NAME = "get_weather"
_TOOL_ARGS = {"city": "Paris"}
_FINAL_TEXT = "It is sunny in Paris."

_GEMINI_PATH_RE = re.compile(r"^/v1beta/models/([^:]+):([^?]+)")


# ---------------------------------------------------------------------------
# Request inspection — has the client already sent a tool result?
# ---------------------------------------------------------------------------


def _anthropic_has_tool_result(payload: dict[str, Any]) -> bool:
    for msg in payload.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    return True
    return False


def _gemini_has_function_response(payload: dict[str, Any]) -> bool:
    for content in payload.get("contents", []):
        for part in content.get("parts", []):
            if isinstance(part, dict) and "functionResponse" in part:
                return True
    return False


# ---------------------------------------------------------------------------
# Anthropic responses
# ---------------------------------------------------------------------------


def _anthropic_tool_use_response(model_id: str) -> bytes:
    body = {
        "id": "msg_tool_stub",
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "content": [
            {"type": "tool_use", "id": "toolu_stub_1", "name": _TOOL_NAME, "input": _TOOL_ARGS}
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": _ANTHROPIC_INPUT_TOKENS, "output_tokens": _ANTHROPIC_OUTPUT_TOKENS},
    }
    return json.dumps(body).encode()


def _anthropic_final_text_response(model_id: str) -> bytes:
    body = {
        "id": "msg_final_stub",
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "content": [{"type": "text", "text": _FINAL_TEXT}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": _ANTHROPIC_INPUT_TOKENS, "output_tokens": _ANTHROPIC_OUTPUT_TOKENS},
    }
    return json.dumps(body).encode()


def _anthropic_tool_sse_response(model_id: str) -> bytes:
    """SSE stream with a streamed tool_use block (input_json_delta fragments)."""
    model_json = json.dumps(model_id)
    events = (
        "event: message_start\n"
        f'data: {{"type":"message_start","message":{{"id":"msg_tool_stub","model":{model_json},'
        f'"usage":{{"input_tokens":{_ANTHROPIC_INPUT_TOKENS},"output_tokens":1}}}}}}\n\n'
        "event: content_block_start\n"
        'data: {"type":"content_block_start","index":0,'
        '"content_block":{"type":"tool_use","id":"toolu_stub_1","name":"get_weather"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"{\\"city\\":"}}\n\n'
        "event: content_block_delta\n"
        'data: {"type":"content_block_delta","index":0,'
        '"delta":{"type":"input_json_delta","partial_json":"\\"Paris\\"}"}}\n\n'
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


def _gemini_function_call_response() -> bytes:
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [{"functionCall": {"name": _TOOL_NAME, "args": _TOOL_ARGS}}],
                    "role": "model",
                },
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": _gemini_usage(),
    }
    return json.dumps(body).encode()


def _gemini_final_text_response() -> bytes:
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": _FINAL_TEXT}], "role": "model"},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "usageMetadata": _gemini_usage(),
    }
    return json.dumps(body).encode()


def _gemini_function_call_sse() -> bytes:
    chunk = {
        "candidates": [
            {
                "content": {
                    "parts": [{"functionCall": {"name": _TOOL_NAME, "args": _TOOL_ARGS}}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": _gemini_usage(),
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


def _openrouter_response(model_id: str) -> bytes:
    body = {
        "id": f"stub-v10-or-{int(time.time())}",
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
        has_result = _anthropic_has_tool_result(payload)
        has_tools = bool(payload.get("tools"))
        if payload.get("stream"):
            # streaming verify exercises turn 1 only → always emit the tool_use stream
            self._send_sse(200, _anthropic_tool_sse_response(model_id))
            return
        if has_result or not has_tools:
            self._send_json(200, _anthropic_final_text_response(model_id))
        else:
            self._send_json(200, _anthropic_tool_use_response(model_id))

    def _handle_gemini(self, verb: str) -> None:
        payload = self._read_json()
        has_result = _gemini_has_function_response(payload)
        has_tools = bool(payload.get("tools"))
        if verb == "generateContent":
            if has_result or not has_tools:
                self._send_json(200, _gemini_final_text_response())
            else:
                self._send_json(200, _gemini_function_call_response())
        elif verb == "streamGenerateContent":
            self._send_sse(200, _gemini_function_call_sse())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        _ = fmt, args
        print(f"  [v10_tool_stub] {self.command} {self.path}")


def make_stub_server() -> HTTPServer:
    return HTTPServer((STUB_HOST, STUB_PORT), _StubHandler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    srv = make_stub_server()
    print(f"v10 tool stub listening on {STUB_HOST}:{STUB_PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
