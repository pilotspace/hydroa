#!/usr/bin/env python3
"""v20 AWS Bedrock stub — INDEPENDENT SigV4-verifying stub for live verification.

Pure stdlib. Speaks the AWS Bedrock wire surface on 127.0.0.1:9927:
  POST /model/{modelId}/converse         -> Converse JSON (chat / tools)
  POST /model/{modelId}/converse-stream  -> AWS binary EventStream (streaming)
  POST /model/{modelId}/invoke           -> Titan embeddings JSON

EVERY model request is SigV4-verified BEFORE any canned body is returned. The
verifier RE-IMPLEMENTS AWS Signature Version 4 from the spec — it does NOT import
gateway.sign_request — so accepting the gateway adapter's signed request is a
genuine INDEPENDENT cross-check (the same independent-oracle philosophy as the
botocore checks that caught the v20 task-2 double-encode bug). The verifier is
itself pinned to the AWS-published "get-vanilla" vector by the bedrock_verify
suite (BV1), so it is ground-truth-correct before it judges any signature.

Per-model behaviors (keyed on the modelId in the path):
  *-retry-once -> 503 on attempt 1 (per model), 200 thereafter   (retry compose)
  *-fb-fail    -> 400 context-window error  (error-aware fallback trigger)
  *-fb-ok / everything else -> 200 canned body
  *-tool       -> 200 Converse response carrying a toolUse block

Control endpoints (no SigV4):
  GET  /__health    -> {"ok": true}
  GET  /__counters  -> {modelId: attempt_count, ...}
  POST /__reset     -> clears counters + retry state (double-pass idempotency)

Binding:  127.0.0.1:9927  (NEVER 0.0.0.0 — security requirement, §1)
Security: FAKE public test credentials only; the Authorization MAC / secret is
          NEVER logged (log_message is suppressed).

Usage:
    The stub is started by scripts/live_v20_verify.py (live) and by
    apps/gateway/tests/bedrock_verify/test_bedrock_verify.py (no-docker) in a
    daemon thread. It can also be run standalone:
        python3 scripts/v20_bedrock_stub.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
import threading
from binascii import crc32
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import quote, unquote, urlparse

# ---------------------------------------------------------------------------
# Constants — binding + FAKE public credentials
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9927

# AWS-published test-suite credentials (PUBLIC, fake). The secret pins BV1 to the
# get-vanilla vector and is what we recompute every adapter signature against.
FAKE_ACCESS_KEY_ID = "AKIDEXAMPLE"
FAKE_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

# ---------------------------------------------------------------------------
# Independent SigV4 v4 implementation (pure stdlib — NOT gateway.sign_request)
# ---------------------------------------------------------------------------


def _hmac_sha256(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def sigv4_signature(
    *,
    method: str,
    canonical_uri: str,
    canonical_querystring: str,
    signed_headers: dict[str, str],
    payload_hash: str,
    service: str,
    region: str,
    secret_access_key: str,
    amz_date: str,
) -> str:
    """Compute the AWS SigV4 signature (lowercase hex) — independent re-impl.

    ``amz_date`` is the ``x-amz-date`` value (``YYYYMMDDTHHMMSSZ``); the date
    scope is its first 8 chars. ``signed_headers`` is ``{lowercase-name: value}``;
    they are sorted internally to build the canonical form.

    Algorithm: https://docs.aws.amazon.com/general/latest/gr/signature-v4-create-canonical-request.html
    """
    datestamp = amz_date[:8]

    sorted_keys = sorted(signed_headers)
    canonical_headers = "".join(f"{k}:{signed_headers[k].strip()}\n" for k in sorted_keys)
    signed_headers_list = ";".join(sorted_keys)

    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers_list,
            payload_hash,
        ]
    )

    scope = f"{datestamp}/{region}/{service}/aws4_request"
    cr_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{cr_hash}"

    k_date = _hmac_sha256(("AWS4" + secret_access_key).encode("utf-8"), datestamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "aws4_request")

    return hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def _parse_authorization(auth: str) -> dict[str, str] | None:
    """Parse an ``AWS4-HMAC-SHA256`` Authorization header into its parts, or None."""
    if not auth.startswith("AWS4-HMAC-SHA256 "):
        return None
    parts: dict[str, str] = {}
    for segment in auth[len("AWS4-HMAC-SHA256 ") :].split(","):
        segment = segment.strip()
        if "=" not in segment:
            return None
        key, _, value = segment.partition("=")
        parts[key.strip()] = value.strip()
    if not {"Credential", "SignedHeaders", "Signature"} <= parts.keys():
        return None
    return parts


def verify_request(method: str, path: str, headers: dict[str, str], body: bytes) -> bool:
    """Independently verify the request's SigV4 signature. PURE, never raises.

    Recomputes the signature from the wire (canonical URI re-derived via
    quote(unquote(path)) so it is robust to whether the client sent a literal
    ':' or '%3A' in a versioned model path) and constant-time-compares it to the
    Authorization ``Signature=``. Also checks x-amz-content-sha256 == sha256(body).
    """
    # Case-insensitive header lookup.
    h = {k.lower(): v for k, v in headers.items()}
    auth = h.get("authorization")
    if not auth:
        return False
    parsed = _parse_authorization(auth)
    if parsed is None:
        return False

    # Credential = AKID/datestamp/region/service/aws4_request
    cred_parts = parsed["Credential"].split("/")
    if len(cred_parts) != 5 or cred_parts[4] != "aws4_request":
        return False
    akid, _datestamp, region, service, _ = cred_parts
    if akid != FAKE_ACCESS_KEY_ID:
        return False

    amz_date = h.get("x-amz-date", "")
    payload_hash = h.get("x-amz-content-sha256", "")
    if not amz_date or not payload_hash:
        return False
    # The signed payload hash MUST match the bytes actually received.
    if payload_hash != hashlib.sha256(body).hexdigest():
        return False

    signed_header_names = parsed["SignedHeaders"].split(";")
    signed_headers: dict[str, str] = {}
    for name in signed_header_names:
        if name == "host":
            host = h.get("host")
            if host is None:
                return False
            signed_headers["host"] = host
        else:
            val = h.get(name)
            if val is None:
                return False
            signed_headers[name] = val

    parsed_url = urlparse(path)
    canonical_uri = quote(unquote(parsed_url.path), safe="/~")
    canonical_querystring = parsed_url.query or ""

    expected = sigv4_signature(
        method=method,
        canonical_uri=canonical_uri,
        canonical_querystring=canonical_querystring,
        signed_headers=signed_headers,
        payload_hash=payload_hash,
        service=service,
        region=region,
        secret_access_key=FAKE_SECRET_ACCESS_KEY,
        amz_date=amz_date,
    )
    return hmac.compare_digest(expected, parsed["Signature"])


# ---------------------------------------------------------------------------
# Per-model state — attempt counters + retry tracking (thread-safe)
# ---------------------------------------------------------------------------

_counters: dict[str, int] = {}
_lock = threading.Lock()


def _increment(model_id: str) -> int:
    with _lock:
        _counters[model_id] = _counters.get(model_id, 0) + 1
        return _counters[model_id]


def _get_counters() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def _reset() -> None:
    with _lock:
        _counters.clear()


# ---------------------------------------------------------------------------
# Canned response bodies
# ---------------------------------------------------------------------------

_EMBED_DIM = 8
_EMBED_VECTOR = [0.125] * _EMBED_DIM  # deterministic, non-empty


def _converse_chat_body(model_id: str) -> dict[str, Any]:
    return {
        "output": {
            "message": {"role": "assistant", "content": [{"text": "Hello"}, {"text": " world"}]}
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 11, "outputTokens": 5, "totalTokens": 16},
    }


def _converse_tool_body(model_id: str) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "tu_stub_1",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        }
                    }
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 12, "outputTokens": 7, "totalTokens": 19},
    }


def _context_window_error() -> dict[str, Any]:
    # Bedrock error shape {"message", "__type"}; the message carries a context-window
    # phrase so the gateway's classify_fallback_trigger routes it to fallover.
    return {
        "message": "Input is too long for requested model: the context window (context_length) is exceeded.",
        "__type": "ValidationException",
    }


def _es_header(name: str, value: str) -> bytes:
    n = name.encode()
    v = value.encode()
    return bytes([len(n)]) + n + bytes([7]) + struct.pack(">H", len(v)) + v


def _es_message(event_type: str, payload: dict[str, Any]) -> bytes:
    """Build one authentic AWS EventStream message (prelude + 2 CRCs via crc32)."""
    headers = (
        _es_header(":event-type", event_type)
        + _es_header(":content-type", "application/json")
        + _es_header(":message-type", "event")
    )
    body = json.dumps(payload).encode()
    headers_len = len(headers)
    total_len = 12 + headers_len + len(body) + 4
    prelude = struct.pack(">II", total_len, headers_len)
    prelude_full = prelude + struct.pack(">I", crc32(prelude) & 0xFFFFFFFF)
    msg_wo_crc = prelude_full + headers + body
    return msg_wo_crc + struct.pack(">I", crc32(msg_wo_crc) & 0xFFFFFFFF)


def _converse_stream_bytes() -> bytes:
    return b"".join(
        [
            _es_message("messageStart", {"role": "assistant"}),
            _es_message("contentBlockDelta", {"delta": {"text": "Hello"}, "contentBlockIndex": 0}),
            _es_message("contentBlockDelta", {"delta": {"text": " world"}, "contentBlockIndex": 0}),
            _es_message("contentBlockStop", {"contentBlockIndex": 0}),
            _es_message("messageStop", {"stopReason": "end_turn"}),
            _es_message(
                "metadata", {"usage": {"inputTokens": 11, "outputTokens": 5, "totalTokens": 16}}
            ),
        ]
    )


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    # SECURITY: suppress default logging entirely — it would otherwise echo the
    # request line; we NEVER log Authorization headers or any MAC/secret.
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
    def _model_and_action(path: str) -> tuple[str, str] | None:
        path_only = path.split("?", 1)[0]
        if not path_only.startswith("/model/"):
            return None
        rest = path_only[len("/model/") :]
        model_id, sep, action = rest.rpartition("/")
        if not sep:
            return None
        return model_id, action

    def do_GET(self) -> None:
        if self.path == "/__health":
            self._send_json(200, {"ok": True})
        elif self.path == "/__counters":
            self._send_json(200, _get_counters())
        else:
            self._send_json(404, {"message": "not found"})

    def do_POST(self) -> None:
        if self.path == "/__reset":
            _reset()
            self._send_json(200, {"reset": True})
            return

        body = self._read_body()
        parsed = self._model_and_action(self.path)
        if parsed is None:
            self._send_json(404, {"message": "not found"})
            return
        model_id, action = parsed

        # INDEPENDENT SigV4 gate — must pass before ANY canned body is served.
        headers = {k: v for k, v in self.headers.items()}
        if not verify_request("POST", self.path, headers, body):
            self._send_json(403, {"message": "SigV4 signature mismatch"})
            return

        attempt = _increment(model_id)

        # Retry compose: 503 on the first verified attempt, 200 thereafter.
        if model_id.endswith("retry-once") and attempt == 1:
            self._send_json(503, {"message": "service unavailable", "__type": "ServiceUnavailable"})
            return

        # Error-aware fallback trigger: a context-window 400 on the fail candidate.
        if model_id.endswith("fb-fail"):
            self._send_json(400, _context_window_error())
            return

        if action == "converse":
            if model_id.endswith("tool"):
                self._send_json(200, _converse_tool_body(model_id))
            else:
                self._send_json(200, _converse_chat_body(model_id))
        elif action == "converse-stream":
            self._send_bytes(200, _converse_stream_bytes(), "application/vnd.amazon.eventstream")
        elif action == "invoke":
            try:
                req = json.loads(body.decode("utf-8"))
                input_text = str(req.get("inputText", ""))
            except (ValueError, UnicodeDecodeError):
                input_text = ""
            self._send_json(
                200,
                {"embedding": list(_EMBED_VECTOR), "inputTextTokenCount": len(input_text)},
            )
        else:
            self._send_json(404, {"message": "unknown action"})


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
    print(f"v20 Bedrock stub listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
