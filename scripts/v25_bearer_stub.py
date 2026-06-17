#!/usr/bin/env python3
"""v25 BYOK bearer-provider stub — independent per-tenant credential oracle.

Serves the 4 Bearer-flavoured providers behind ONE port and INDEPENDENTLY verifies the
credential the gateway presents on every model request BEFORE serving a canned body. The
gateway resolves that credential per request from the tenant's Fernet-encrypted store
(v25 BYOK), so an ACCEPT proves the resolved secret reached the adapter and was put on the
wire with the correct header — a genuine cross-check (this stub does NOT import gateway code).

Surfaces + the header each must carry (re-implemented from each provider's spec):
  POST /api/v1/chat/completions                       OpenRouter  Authorization: Bearer == EXPECTED_OPENROUTER
  POST /v1/chat/completions                           OpenAI      Authorization: Bearer == EXPECTED_OPENAI
  POST /v1/embeddings                                 OpenAI      Authorization: Bearer == EXPECTED_OPENAI
  POST /v1/messages                                   Anthropic   x-api-key      == EXPECTED_ANTHROPIC
  POST /v1beta/models/{m}:generateContent             Gemini      x-goog-api-key == EXPECTED_GOOGLE
       (+ :streamGenerateContent / :embedContent / :batchEmbedContents)
wrong / missing credential -> 401 (the oracle is not a rubber stamp).

Control: GET /__health · GET /__counters (per-provider ACCEPT counts) · POST /__reset.

Binding:  127.0.0.1 ONLY (NEVER 0.0.0.0 — security requirement §1).
Secrets:  FAKE public test values only; auth compared with hmac.compare_digest; headers NEVER logged.

Started by scripts/live_v25_verify.py in a daemon thread; also loaded by the no-docker earned-green
suite (apps/gateway/tests/byok_verify) via importlib on an ephemeral port (make_stub_server(port=0)).
"""

from __future__ import annotations

import hmac
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9928

# Fake public test secrets (NEVER real provider keys). The earned-green suite + the live
# script PUT exactly these into the BYOK store, so the stub ACCEPTS only the resolved value.
EXPECTED_OPENROUTER = "or-byok-secret-FAKE"
EXPECTED_OPENAI = "oai-byok-secret-FAKE"
EXPECTED_ANTHROPIC = "ant-byok-secret-FAKE"
EXPECTED_GOOGLE = "goog-byok-secret-FAKE"

_GEMINI_PATH_RE = re.compile(r"^/v1beta/models/([^:]+):([^?]+)")

# Per-provider ACCEPT counters (guarded by a lock for thread-safety).
_COUNTERS: dict[str, int] = {"openrouter": 0, "openai": 0, "anthropic": 0, "google": 0}
_COUNTERS_LOCK = threading.Lock()


def _bump(provider: str) -> None:
    with _COUNTERS_LOCK:
        _COUNTERS[provider] = _COUNTERS.get(provider, 0) + 1


def _snapshot() -> dict[str, int]:
    with _COUNTERS_LOCK:
        return dict(_COUNTERS)


def _reset() -> None:
    with _COUNTERS_LOCK:
        for k in _COUNTERS:
            _COUNTERS[k] = 0


# ---------------------------------------------------------------------------
# Auth verification (constant-time; never logs the value)
# ---------------------------------------------------------------------------


def _ct_eq(got: str | None, want: str) -> bool:
    """Constant-time string compare; False when the header is absent."""
    if got is None:
        return False
    return hmac.compare_digest(got, want)


def verify_bearer(headers: object, expected: str) -> bool:
    """True iff the Authorization header equals 'Bearer {expected}'."""
    got = headers.get("Authorization") if hasattr(headers, "get") else None  # type: ignore[union-attr]
    return _ct_eq(got, f"Bearer {expected}")


def verify_header(headers: object, name: str, expected: str) -> bool:
    """True iff header `name` equals `expected` (used for x-api-key / x-goog-api-key)."""
    got = headers.get(name) if hasattr(headers, "get") else None  # type: ignore[union-attr]
    return _ct_eq(got, expected)


# ---------------------------------------------------------------------------
# Response builders (provider-NATIVE wire shapes the real adapters translate)
# ---------------------------------------------------------------------------

_OPENAI_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
_EMBED_VALUES = [0.1, 0.2, 0.3]


def _openai_chat(model_id: str) -> bytes:
    return json.dumps(
        {
            "id": f"stub-v25-{int(time.time())}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "ok-byok"}, "finish_reason": "stop"}
            ],
            "usage": _OPENAI_USAGE,
        }
    ).encode()


def _openai_embeddings(model_id: str, n: int) -> bytes:
    return json.dumps(
        {
            "object": "list",
            "model": model_id,
            "data": [
                {"object": "embedding", "index": i, "embedding": list(_EMBED_VALUES)} for i in range(max(n, 1))
            ],
            "usage": {"prompt_tokens": 3, "total_tokens": 3},
        }
    ).encode()


def _anthropic_message(model_id: str) -> bytes:
    return json.dumps(
        {
            "id": "msg_stub",
            "type": "message",
            "role": "assistant",
            "model": model_id,
            "content": [{"type": "text", "text": "Hello world"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 4},
        }
    ).encode()


def _gemini_generate() -> bytes:
    return json.dumps(
        {
            "candidates": [
                {"content": {"parts": [{"text": "Hello gemini"}], "role": "model"}, "finishReason": "STOP", "index": 0}
            ],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 6, "totalTokenCount": 15},
        }
    ).encode()


def _gemini_stream() -> bytes:
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "Hello"}], "role": "model"}}]},
        {"candidates": [{"content": {"parts": [{"text": " gemini"}], "role": "model"}}]},
        {
            "candidates": [{"content": {"parts": [{"text": ""}], "role": "model"}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 6, "totalTokenCount": 15},
        },
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode()


def _gemini_embed() -> bytes:
    return json.dumps({"embedding": {"values": list(_EMBED_VALUES)}}).encode()


def _gemini_batch_embed(n: int) -> bytes:
    return json.dumps({"embeddings": [{"values": list(_EMBED_VALUES)} for _ in range(n)]}).encode()


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

    def _send_sse(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self) -> None:
        self._send_json(401, json.dumps({"error": {"message": "invalid credential", "type": "auth"}}).encode())

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

        raw = self._read_body()  # always consume the body (even on a 401)

        if path == "/api/v1/chat/completions":
            self._bearer_chat(raw, EXPECTED_OPENROUTER, "openrouter")
        elif path == "/v1/chat/completions":
            self._bearer_chat(raw, EXPECTED_OPENAI, "openai")
        elif path == "/v1/embeddings":
            self._openai_embeddings(raw)
        elif path == "/v1/messages":
            self._anthropic(raw)
        else:
            m = _GEMINI_PATH_RE.match(path)
            if m:
                self._gemini(m.group(1), m.group(2), raw)
            else:
                self.send_response(404)
                self.end_headers()

    # -- provider handlers (verify credential BEFORE serving) ----------------

    def _model_of(self, raw: bytes) -> str:
        try:
            return str(json.loads(raw).get("model", "unknown"))
        except (json.JSONDecodeError, AttributeError):
            return "unknown"

    def _bearer_chat(self, raw: bytes, expected: str, provider: str) -> None:
        if not verify_bearer(self.headers, expected):
            self._unauthorized()
            return
        _bump(provider)
        self._send_json(200, _openai_chat(self._model_of(raw)))

    def _openai_embeddings(self, raw: bytes) -> None:
        if not verify_bearer(self.headers, EXPECTED_OPENAI):
            self._unauthorized()
            return
        _bump("openai")
        try:
            inp = json.loads(raw).get("input", [])
            n = len(inp) if isinstance(inp, list) else 1
        except (json.JSONDecodeError, AttributeError):
            n = 1
        self._send_json(200, _openai_embeddings(self._model_of(raw), n))

    def _anthropic(self, raw: bytes) -> None:
        if not verify_header(self.headers, "x-api-key", EXPECTED_ANTHROPIC):
            self._unauthorized()
            return
        _bump("anthropic")
        self._send_json(200, _anthropic_message(self._model_of(raw)))

    def _gemini(self, _model: str, verb: str, raw: bytes) -> None:
        if not verify_header(self.headers, "x-goog-api-key", EXPECTED_GOOGLE):
            self._unauthorized()
            return
        _bump("google")
        if verb == "generateContent":
            self._send_json(200, _gemini_generate())
        elif verb == "streamGenerateContent":
            self._send_sse(200, _gemini_stream())
        elif verb == "embedContent":
            self._send_json(200, _gemini_embed())
        elif verb == "batchEmbedContents":
            try:
                n = len(json.loads(raw).get("requests", []))
            except (json.JSONDecodeError, AttributeError, TypeError):
                n = 0
            self._send_json(200, _gemini_batch_embed(n))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: object) -> None:
        # Fully suppressed — request headers carry the credential and must NEVER be logged.
        _ = args


# ---------------------------------------------------------------------------
# Server lifecycle helpers
# ---------------------------------------------------------------------------


def make_stub_server(port: int = STUB_PORT) -> HTTPServer:
    """Create the stub HTTPServer on 127.0.0.1:port (port=0 → ephemeral). Not started."""
    return HTTPServer((STUB_HOST, port), _Handler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    """Start server.serve_forever() in a daemon thread. Returns the thread."""
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    srv = make_stub_server()
    print(f"v25 bearer stub listening on {STUB_HOST}:{STUB_PORT}")
    print("  /api/v1/chat/completions (openrouter) · /v1/chat/completions + /v1/embeddings (openai)")
    print("  /v1/messages (anthropic) · /v1beta/models/{m}:<verb> (gemini)")
    print("  GET /__health · GET /__counters · POST /__reset")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
