#!/usr/bin/env python3
"""vertex-adapter stub — INDEPENDENT RFC 7523 JWT-bearer-verifying stub for live
verification.

Pure stdlib. Speaks two surfaces on 127.0.0.1:
  POST /token                                                     -> OAuth2 token endpoint
  POST /v1/projects/{p}/locations/{l}/publishers/google/models/{m}:generateContent
  POST /v1/projects/{p}/locations/{l}/publishers/google/models/{m}:streamGenerateContent

EVERY /token request is RS256-signature-verified BEFORE any canned access_token is
issued, and every generateContent/streamGenerateContent call requires a Bearer token
this stub itself minted. The verifier RE-IMPLEMENTS RSA PKCS#1v1.5/SHA-256 signature
verification FROM SCRATCH — manual DER/ASN.1 parsing of the RSA public key + manual
modular-exponentiation signature check via ``pow(s, e, n)`` — it does NOT import
``pyjwt`` or ``cryptography`` (gateway's own signing stack), so accepting the gateway
adapter's real PyJWT-signed assertion is a genuine INDEPENDENT cross-check (mirrors
``scripts/v20_bedrock_stub.py``'s "does NOT import gateway.sign_request" independence
property, and is itself pinned to a known-good/known-bad pair by the vertex_verify
suite before it judges any gateway signature).

Control endpoints (no auth):
  POST /__configure -> {public_key_pem, client_email, project_id, expected_aud} — must
                        be called before the first /token request.
  GET  /__health     -> {"ok": true}
  GET  /__counters   -> {"token": n, model_id: attempt_count, ...}
  POST /__reset      -> clears counters + minted-token set + retry state (double-pass
                         idempotency)

Binding:  127.0.0.1 (NEVER 0.0.0.0 — security requirement, mirrors v20_bedrock_stub).
Security: log_message is suppressed — Authorization headers / signed assertions are
          NEVER logged.

Usage:
    The stub is started by scripts/live_vertex_verify.py (live) and by
    apps/gateway/tests/vertex_verify/test_vertex_verify.py (no-docker) in a daemon
    thread. It can also be run standalone: python3 scripts/vertex_stub.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement
STUB_PORT = 9928

# SHA-256 DigestInfo DER prefix (PKCS#1 v1.5) — the well-known constant prepended to
# a raw 32-byte SHA-256 digest inside an RSA signature's padded message.
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


# ---------------------------------------------------------------------------
# Minimal from-scratch ASN.1 DER parser — just enough to read an RSA
# SubjectPublicKeyInfo PEM and extract (modulus n, publicExponent e).
# ---------------------------------------------------------------------------


def _read_der_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """Read one DER TLV (tag, length, value) starting at ``pos``.

    Returns (tag, value_bytes, next_pos). Supports short- and long-form lengths
    (sufficient for the fixed SubjectPublicKeyInfo / RSAPublicKey structures).
    """
    tag = data[pos]
    pos += 1
    length_byte = data[pos]
    pos += 1
    if length_byte & 0x80 == 0:
        length = length_byte
    else:
        num_bytes = length_byte & 0x7F
        length = int.from_bytes(data[pos : pos + num_bytes], "big")
        pos += num_bytes
    value = data[pos : pos + length]
    return tag, value, pos + length


def _der_integer_to_int(value: bytes) -> int:
    return int.from_bytes(value, "big")


def parse_rsa_public_key_pem(pem: str) -> tuple[int, int]:
    """Parse a PEM-encoded RSA SubjectPublicKeyInfo (``-----BEGIN PUBLIC KEY-----``)
    into (modulus n, publicExponent e) via a hand-rolled DER parser.

    Structure (RFC 5280 / RFC 3447):
        SubjectPublicKeyInfo ::= SEQUENCE {
            algorithm         AlgorithmIdentifier,   -- ignored (assumed rsaEncryption)
            subjectPublicKey  BIT STRING              -- wraps RSAPublicKey DER
        }
        RSAPublicKey ::= SEQUENCE { modulus INTEGER, publicExponent INTEGER }
    """
    lines = [
        line.strip()
        for line in pem.strip().splitlines()
        if line.strip() and "BEGIN" not in line and "END" not in line
    ]
    der = base64.b64decode("".join(lines))

    tag, spki_value, _ = _read_der_tlv(der, 0)
    if tag != 0x30:  # SEQUENCE
        raise ValueError("not a DER SEQUENCE (SubjectPublicKeyInfo)")

    # Skip the AlgorithmIdentifier SEQUENCE; read the BIT STRING that follows.
    _alg_tag, _alg_value, pos = _read_der_tlv(spki_value, 0)
    bs_tag, bs_value, _ = _read_der_tlv(spki_value, pos)
    if bs_tag != 0x03:  # BIT STRING
        raise ValueError("expected BIT STRING for subjectPublicKey")
    # First byte of a BIT STRING is the "unused bits" count (0 for a DER-aligned key).
    rsa_pub_der = bs_value[1:]

    rk_tag, rk_value, _ = _read_der_tlv(rsa_pub_der, 0)
    if rk_tag != 0x30:  # SEQUENCE (RSAPublicKey)
        raise ValueError("expected SEQUENCE for RSAPublicKey")
    n_tag, n_value, next_pos = _read_der_tlv(rk_value, 0)
    e_tag, e_value, _ = _read_der_tlv(rk_value, next_pos)
    if n_tag != 0x02 or e_tag != 0x02:  # INTEGER
        raise ValueError("expected INTEGER modulus/exponent")
    return _der_integer_to_int(n_value), _der_integer_to_int(e_value)


# ---------------------------------------------------------------------------
# base64url + PKCS#1 v1.5 / SHA-256 signature verification (independent of
# pyjwt/cryptography) — pure stdlib hashlib + big-int modular exponentiation.
# ---------------------------------------------------------------------------


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def verify_rs256_jwt(token: str, *, n: int, e: int) -> dict[str, Any] | None:
    """Independently verify an RS256-signed compact JWT against RSA public key (n, e).

    Returns the decoded payload dict on success, or None on ANY failure (malformed
    token, wrong alg, bad padding, signature mismatch) — never raises.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(sig_b64)
    except (ValueError, UnicodeDecodeError):
        return None
    if header.get("alg") != "RS256":
        return None

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    digest = hashlib.sha256(signing_input).digest()

    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return None
    s = int.from_bytes(signature, "big")
    if s >= n:
        return None
    m = pow(s, e, n)
    em = m.to_bytes(k, "big")

    digest_info = _SHA256_DIGEST_INFO_PREFIX + digest
    padlen = k - 3 - len(digest_info)
    if padlen < 8:  # PKCS#1 v1.5 requires >= 8 padding bytes
        return None
    expected = b"\x00\x01" + b"\xff" * padlen + b"\x00" + digest_info

    if em != expected:
        return None
    return payload  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Mutable stub state — configured once via /__configure before use.
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_config: dict[str, Any] = {
    "n": None,
    "e": None,
    "client_email": None,
    "project_id": None,
    "expected_aud": None,
}
_counters: dict[str, int] = {}
_issued_tokens: set[str] = set()


def configure(
    *, public_key_pem: str, client_email: str, project_id: str, expected_aud: str
) -> None:
    n, e = parse_rsa_public_key_pem(public_key_pem)
    with _state_lock:
        _config["n"] = n
        _config["e"] = e
        _config["client_email"] = client_email
        _config["project_id"] = project_id
        _config["expected_aud"] = expected_aud


def _increment(key: str) -> int:
    with _state_lock:
        _counters[key] = _counters.get(key, 0) + 1
        return _counters[key]


def _get_counters() -> dict[str, int]:
    with _state_lock:
        return dict(_counters)


def _reset() -> None:
    with _state_lock:
        _counters.clear()
        _issued_tokens.clear()


# ---------------------------------------------------------------------------
# Canned Gemini-shaped response bodies (Vertex's generateContent wire shape is
# byte-identical to the public Gemini API's — TASK.md §1 Framings).
# ---------------------------------------------------------------------------


def _generate_content_body() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": "Hello"}, {"text": " world"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 11,
            "candidatesTokenCount": 5,
            "totalTokenCount": 16,
        },
    }


def _region_not_found_body() -> dict[str, Any]:
    return {
        "error": {
            "code": 404,
            "message": "The requested model is not available in this location.",
            "status": "NOT_FOUND",
        }
    }


def _stream_chunks() -> list[bytes]:
    return [
        b'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hello"}]}}]}\n\n',
        b'data: {"candidates":[{"content":{"role":"model","parts":[{"text":" world"}]},'
        b'"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":11,'
        b'"candidatesTokenCount":5,"totalTokenCount":16}}\n\n',
    ]


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _StubHandler(BaseHTTPRequestHandler):
    # SECURITY: suppress default logging entirely — never echo Authorization headers
    # or signed assertions.
    def log_message(self, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, status: int, chunks: list[bytes]) -> None:
        body = b"".join(chunks)
        self.send_response(status)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self) -> None:
        if self.path == "/__health":
            self._send_json(200, {"ok": True})
        elif self.path == "/__counters":
            self._send_json(200, _get_counters())
        else:
            self._send_json(404, {"message": "not found"})

    def _handle_token(self, body: bytes) -> None:
        with _state_lock:
            n, e = _config["n"], _config["e"]
            client_email = _config["client_email"]
            expected_aud = _config["expected_aud"]
        if n is None:
            self._send_json(500, {"error": "stub_not_configured"})
            return

        form = parse_qs(body.decode("utf-8"))
        grant_type = form.get("grant_type", [""])[0]
        assertion = form.get("assertion", [""])[0]
        if grant_type != "urn:ietf:params:oauth:grant-type:jwt-bearer" or not assertion:
            self._send_json(400, {"error": "unsupported_grant_type"})
            return

        payload = verify_rs256_jwt(assertion, n=n, e=e)
        if payload is None:
            self._send_json(400, {"error": "invalid_grant", "error_description": "bad signature"})
            return

        # RFC 7523 claim checks — independent of the signature check above.
        if payload.get("iss") != client_email or payload.get("sub") != client_email:
            self._send_json(400, {"error": "invalid_grant", "error_description": "bad iss/sub"})
            return
        if expected_aud is not None and payload.get("aud") != expected_aud:
            self._send_json(400, {"error": "invalid_grant", "error_description": "bad aud"})
            return
        iat, exp = payload.get("iat"), payload.get("exp")
        if not isinstance(iat, int) or not isinstance(exp, int) or exp <= iat:
            self._send_json(400, {"error": "invalid_grant", "error_description": "bad iat/exp"})
            return
        if exp - iat > 3600:
            self._send_json(400, {"error": "invalid_grant", "error_description": "assertion too long-lived"})
            return

        n_hits = _increment("token")
        token = f"stub-access-token-{n_hits}"
        with _state_lock:
            _issued_tokens.add(token)
        self._send_json(200, {"access_token": token, "token_type": "Bearer", "expires_in": 3600})

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        token = auth[len("Bearer ") :]
        with _state_lock:
            return token in _issued_tokens

    def do_POST(self) -> None:
        if self.path == "/__reset":
            _reset()
            self._send_json(200, {"reset": True})
            return
        if self.path == "/__configure":
            body = self._read_body()
            try:
                cfg = json.loads(body.decode("utf-8"))
                configure(
                    public_key_pem=cfg["public_key_pem"],
                    client_email=cfg["client_email"],
                    project_id=cfg["project_id"],
                    expected_aud=cfg["expected_aud"],
                )
            except (ValueError, KeyError, TypeError) as exc:
                self._send_json(400, {"error": f"bad configure payload: {exc}"})
                return
            self._send_json(200, {"configured": True})
            return

        body = self._read_body()
        if self.path == "/token":
            self._handle_token(body)
            return

        parsed = urlparse(self.path)
        path_only = parsed.path
        if not self._authorized():
            self._send_json(401, {"error": {"code": 401, "message": "missing/invalid Bearer token"}})
            return

        if path_only.startswith("/v1/projects/") and ":" in path_only:
            rest, _, action = path_only.rpartition(":")
            model_id = rest.rsplit("/", 1)[-1]

            attempt = _increment(model_id)

            if model_id.endswith("-retry") and attempt == 1:
                self._send_json(503, {"error": {"code": 503, "message": "unavailable", "status": "UNAVAILABLE"}})
                return
            if model_id.endswith("-notfound-region"):
                self._send_json(404, _region_not_found_body())
                return

            if action == "generateContent":
                self._send_json(200, _generate_content_body())
                return
            if action == "streamGenerateContent":
                self._send_sse(200, _stream_chunks())
                return

        self._send_json(404, {"message": "not found"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def make_stub_server(port: int = STUB_PORT) -> HTTPServer:
    """Create the stub HTTPServer bound to 127.0.0.1 (NEVER 0.0.0.0)."""
    return HTTPServer((STUB_HOST, port), _StubHandler)


def start_stub_in_thread(server: HTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def main() -> None:
    server = make_stub_server()
    host, port = server.server_address[0], server.server_address[1]
    print(f"vertex-adapter stub listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
