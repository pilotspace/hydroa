#!/usr/bin/env python3
"""v21 Azure OpenAI stub — INDEPENDENT auth/routing oracle for live verification.

Pure stdlib. Speaks the Azure OpenAI wire surface on 127.0.0.1:9921:
  POST /{tenant}/oauth2/v2.0/token                                    -> AAD client-credentials token
  POST /openai/deployments/{deployment}/chat/completions?api-version  -> OpenAI chat JSON | SSE (stream:true)
  POST /openai/deployments/{deployment}/embeddings?api-version        -> OpenAI embeddings list

EVERY model request is auth-verified AND routing-verified BEFORE any canned body is
returned. The verifier does NOT import gateway code, so accepting the real adapter's
requests is a genuine INDEPENDENT cross-check. Crucially the AAD path is END-TO-END:
the stub MINTS the bearer token at its own token endpoint (only for the correct
client_secret), and the chat/embed routes accept a Bearer ONLY if it equals that minted
token — so a green AAD test proves the real AzureADTokenProvider fetched + presented the
token correctly, not merely that some header was attached.

Auth (verify_auth): the `api-key` header equals EXPECTED_API_KEY, OR `Authorization:
Bearer <t>` equals MINTED_TOKEN. Routing: the deployment is a PATH segment and a non-empty
`api-version` query is present (a malformed URL is rejected, never silently served).

Per-deployment behaviors (keyed on the deployment name in the path):
  *-retry-once       -> 503 on the first verified attempt, 200 thereafter   (retry compose)
  *-content-filter   -> 400 OpenAI content_filter error  (error-aware fallback trigger)
  everything else    -> 200 canned body (chat / SSE / embeddings)

Control endpoints (no auth):
  GET  /__health    -> {"ok": true}
  GET  /__counters  -> {deployment: attempt_count, ...}
  POST /__reset     -> clears counters (double-pass idempotency)

Binding:  127.0.0.1:9921  (NEVER 0.0.0.0 — security requirement, §1)
Security: FAKE public test credentials only; the api-key / client_secret / token are
          NEVER logged (log_message is suppressed); auth compares use hmac.compare_digest.

Usage:
    Started by scripts/live_v21_verify.py (live) and by
    apps/gateway/tests/azure_verify/test_azure_verify.py (no-docker) in a daemon thread.
    Standalone:  python3 scripts/v21_azure_stub.py
"""

from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Constants — binding + FAKE public credentials / minted token
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9921

# FAKE public test values. NOT real Azure credentials.
EXPECTED_API_KEY = "azure-stub-api-key-FAKE"
EXPECTED_CLIENT_SECRET = "azure-stub-client-secret-FAKE"
MINTED_TOKEN = "azure-ad-stub-minted-token-v21"  # the pinned bearer vector

_EMBED_DIM = 8
_EMBED_VECTOR = [0.125] * _EMBED_DIM  # deterministic, non-empty


# ---------------------------------------------------------------------------
# Independent auth oracle (no gateway import)
# ---------------------------------------------------------------------------


def verify_auth(headers: dict[str, str]) -> bool:
    """Accept iff the api-key header matches OR a Bearer equals the minted token.

    Constant-time comparison; never logs the value. PURE, never raises.
    """
    h = {k.lower(): v for k, v in headers.items()}
    api_key = h.get("api-key")
    if api_key is not None:
        return hmac.compare_digest(api_key, EXPECTED_API_KEY)
    authz = h.get("authorization", "")
    if authz.startswith("Bearer "):
        return hmac.compare_digest(authz[len("Bearer ") :], MINTED_TOKEN)
    return False


# ---------------------------------------------------------------------------
# Per-deployment state — attempt counters (thread-safe)
# ---------------------------------------------------------------------------

_counters: dict[str, int] = {}
_lock = threading.Lock()


def _increment(deployment: str) -> int:
    with _lock:
        _counters[deployment] = _counters.get(deployment, 0) + 1
        return _counters[deployment]


def _get_counters() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def _reset() -> None:
    with _lock:
        _counters.clear()


# ---------------------------------------------------------------------------
# Canned response bodies (OpenAI-shaped — Azure speaks OpenAI natively)
# ---------------------------------------------------------------------------


def _chat_body(deployment: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "model": deployment,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello world"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
    }


def _content_filter_error() -> dict[str, Any]:
    # OpenAI error shape with code=content_filter; the message carries the Azure
    # "content management policy" phrase so the gateway's classify_fallback_trigger
    # routes it to error-aware fallover.
    return {
        "error": {
            "message": (
                "The response was filtered due to the prompt triggering Azure OpenAI's "
                "content management policy."
            ),
            "type": "invalid_request_error",
            "code": "content_filter",
        }
    }


def _sse_bytes() -> bytes:
    """OpenAI-shaped SSE: role → text deltas → a final frame carrying usage → [DONE]."""

    def frame(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    base = {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "model": "stub"}
    parts = [
        frame({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}),
        frame({**base, "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}]}),
        frame({**base, "choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}]}),
        frame(
            {
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            }
        ),
        "data: [DONE]\n\n",
    ]
    return "".join(parts).encode("utf-8")


def _embeddings_body(deployment: str, texts: list[str]) -> dict[str, Any]:
    total = sum(len(t) for t in texts)  # exact, summed → verifiable billing
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": list(_EMBED_VECTOR)}
            for i in range(len(texts))
        ],
        "model": deployment,
        "usage": {"prompt_tokens": total, "total_tokens": total},
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    # SECURITY: suppress default logging — it would echo the request line; we NEVER
    # log Authorization / api-key headers or the client_secret / token.
    def log_message(self, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length", 0) or 0)
        return self.rfile.read(length) if length else b""

    @staticmethod
    def _deployment_and_op(path_only: str) -> tuple[str, str] | None:
        prefix = "/openai/deployments/"
        if not path_only.startswith(prefix):
            return None
        rest = path_only[len(prefix) :]
        deployment, sep, op = rest.partition("/")
        if not sep or not deployment or not op:
            return None
        return deployment, op

    def do_GET(self) -> None:
        if self.path == "/__health":
            self._send_json(200, {"ok": True})
        elif self.path == "/__counters":
            self._send_json(200, _get_counters())
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path == "/__reset":
            _reset()
            self._send_json(200, {"reset": True})
            return

        body = self._read_body()
        parsed_url = urlparse(self.path)
        path_only = parsed_url.path

        # AAD token endpoint — client-credentials. Mints the bearer the gateway must echo.
        if path_only.endswith("/oauth2/v2.0/token"):
            self._handle_token(body)
            return

        parsed = self._deployment_and_op(path_only)
        if parsed is None:
            self._send_json(404, {"error": {"message": "not found"}})
            return
        deployment, op = parsed

        # Routing gate: a non-empty api-version query is REQUIRED (never serve a malformed URL).
        qs = parse_qs(parsed_url.query)
        api_version = (qs.get("api-version") or [""])[0]
        if not api_version:
            self._send_json(400, {"error": {"message": "api-version query parameter is required"}})
            return

        # INDEPENDENT auth gate — must pass before ANY canned body is served.
        headers = {k: v for k, v in self.headers.items()}
        if not verify_auth(headers):
            self._send_json(401, {"error": {"message": "auth failed", "code": "unauthorized"}})
            return

        attempt = _increment(deployment)

        # Retry compose: 503 on the first verified attempt, 200 thereafter.
        if deployment.endswith("retry-once") and attempt == 1:
            self._send_json(503, {"error": {"message": "service unavailable"}})
            return

        # Error-aware fallback trigger: a content_filter 400 on the filtered deployment.
        if deployment.endswith("content-filter"):
            self._send_json(400, _content_filter_error())
            return

        if op == "chat/completions":
            try:
                req = json.loads(body.decode("utf-8")) if body else {}
            except (ValueError, UnicodeDecodeError):
                req = {}
            if req.get("stream") is True:
                self._send_bytes(200, _sse_bytes(), "text/event-stream")
            else:
                self._send_json(200, _chat_body(deployment))
        elif op == "embeddings":
            try:
                req = json.loads(body.decode("utf-8")) if body else {}
            except (ValueError, UnicodeDecodeError):
                req = {}
            inp = req.get("input", "")
            texts = [inp] if isinstance(inp, str) else [str(t) for t in inp]
            self._send_json(200, _embeddings_body(deployment, texts))
        else:
            self._send_json(404, {"error": {"message": "unknown operation"}})

    def _handle_token(self, body: bytes) -> None:
        form = parse_qs(body.decode("utf-8")) if body else {}
        client_secret = (form.get("client_secret") or [""])[0]
        grant = (form.get("grant_type") or [""])[0]
        if grant != "client_credentials" or not hmac.compare_digest(
            client_secret, EXPECTED_CLIENT_SECRET
        ):
            self._send_json(401, {"error": "invalid_client"})
            return
        self._send_json(
            200,
            {"access_token": MINTED_TOKEN, "expires_in": 3600, "token_type": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def make_stub_server(port: int = STUB_PORT) -> HTTPServer:
    """Create the stub HTTPServer bound to 127.0.0.1 (NEVER 0.0.0.0).

    ``port=0`` lets the OS assign an ephemeral port (used by the no-docker suite).
    """
    return HTTPServer((STUB_HOST, port), _StubHandler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def main() -> None:
    server = make_stub_server()
    host, port = server.server_address[0], server.server_address[1]
    print(f"v21 Azure OpenAI stub listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
