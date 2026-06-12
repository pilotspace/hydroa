#!/usr/bin/env python3
"""OpenAI-compatible upstream stub for v7 live verification.

Speaks the OpenAI multi-modal surface on :9921 (localhost only).
No fault injection is required for v7 (all happy-path). The /__faults
endpoint is present as a no-op for idiom parity with v6_fault_stub.py.

Binding:  127.0.0.1:9921  (NEVER 0.0.0.0 — security requirement from §1)
Protocol: HTTP/1.1, stdlib http.server

Routes (gateway base_url = http://host.docker.internal:9921/v1, so provider
appends "/embeddings" etc. directly to produce the full path):

  POST /v1/embeddings          -> 200 JSON embeddings body
  POST /v1/images/generations  -> 200 JSON {"created":<int>,"data":[{...},{...}]}
  POST /v1/audio/transcriptions -> 200 JSON {"text":"hello from stub","duration":12.5}
  POST /v1/audio/speech        -> 200, Content-Type audio/mpeg, streaming audio bytes
  POST /__faults               -> 200 {} (no-op; present for idiom parity with v6)

Usage:
    The stub is started by scripts/live_v7_verify.py in a daemon thread.
    It can also be run standalone for manual testing:
        python scripts/v7_openai_stub.py
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_HOST = "127.0.0.1"  # MUST NOT be 0.0.0.0 — security requirement (§1)
STUB_PORT = 9921

# A fixed-ish creation timestamp for image responses (re-evaluated each call)
_CREATED_TS = int(time.time())


# ---------------------------------------------------------------------------
# Response body helpers
# ---------------------------------------------------------------------------


def _embeddings_body(model: str) -> bytes:
    """200 JSON for POST /v1/embeddings (§3 contract exact shape)."""
    body = {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": 0,
                "embedding": [0.01, 0.02, 0.03],
            }
        ],
        "model": model,
        "usage": {
            "prompt_tokens": 8,
            "total_tokens": 8,
        },
    }
    return json.dumps(body).encode()


def _images_body() -> bytes:
    """200 JSON for POST /v1/images/generations — exactly 2 entries (§3 contract)."""
    body = {
        "created": int(time.time()),
        "data": [
            {"url": "https://stub/img0.png"},
            {"url": "https://stub/img1.png"},
        ],
    }
    return json.dumps(body).encode()


def _transcriptions_body() -> bytes:
    """200 JSON for POST /v1/audio/transcriptions — duration 12.5 s (§3 contract)."""
    body = {
        "text": "hello from stub",
        "duration": 12.5,
    }
    return json.dumps(body).encode()


def _speech_chunks() -> list[bytes]:
    """Streaming audio byte chunks for POST /v1/audio/speech (§3 contract).

    Returns a small list of byte chunks.  The Content-Type is audio/mpeg.
    The gateway streams these directly back to the client; per_character
    quantity is derived from len(input) not from byte count.
    """
    # Mimic a tiny MP3: ID3 header tag + padding zeros
    return [
        b"ID3\x03\x00\x00\x00\x00\x00\x00",
        b"\xff\xfb\x90\x00" + b"\x00" * 32,
        b"\xff\xfb\x90\x00" + b"\x00" * 32,
    ]


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """HTTP handler for the OpenAI-compatible multi-modal stub."""

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send_json(
        self,
        status: int,
        raw: bytes,
        *,
        content_type: str = "application/json",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]  # strip query string if any
        if path == "/__faults":
            self._handle_faults()
        elif path == "/v1/embeddings":
            self._handle_embeddings()
        elif path == "/v1/images/generations":
            self._handle_images()
        elif path == "/v1/audio/transcriptions":
            self._handle_transcriptions()
        elif path == "/v1/audio/speech":
            self._handle_speech()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_faults(self) -> None:
        """POST /__faults — no-op; returns 200 {} for idiom parity with v6."""
        self._read_body()  # discard
        self._send_json(200, b"{}")

    def _handle_embeddings(self) -> None:
        """POST /v1/embeddings — return well-formed embeddings response.

        Echoes the model from the request body so the billing recorder
        can match it against the seeded model row.
        """
        raw = self._read_body()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            payload = {}
        model = payload.get("model", "unknown")
        self._send_json(200, _embeddings_body(model))

    def _handle_images(self) -> None:
        """POST /v1/images/generations — return 2 image objects (C2 quantity=2)."""
        self._read_body()  # discard
        self._send_json(200, _images_body())

    def _handle_transcriptions(self) -> None:
        """POST /v1/audio/transcriptions — multipart in; JSON out with duration.

        Per context brief: read+discard the body; do NOT parse multipart.
        duration=12.5 drives per_second quantity=12.5 in the billing recorder.
        """
        self._read_body()  # discard full body (multipart; no parsing required)
        self._send_json(200, _transcriptions_body())

    def _handle_speech(self) -> None:
        """POST /v1/audio/speech — return streaming audio bytes (Content-Type audio/mpeg).

        per_character quantity is derived from len(input) by the billing recorder,
        NOT from the byte count here.  We just need to return non-empty bytes.
        """
        self._read_body()  # discard
        chunks = _speech_chunks()
        body = b"".join(chunks)
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"  [v7_openai_stub] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# Server lifecycle helpers (used by live_v7_verify.py)
# ---------------------------------------------------------------------------


def make_stub_server() -> HTTPServer:
    """Create and return a configured OpenAI stub HTTPServer (not yet started).

    Binding is 127.0.0.1 only — 0.0.0.0 is rejected at this layer.
    """
    assert STUB_HOST == "127.0.0.1", "stub MUST NOT bind to 0.0.0.0"
    return HTTPServer((STUB_HOST, STUB_PORT), _Handler)


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
    print(f"v7 OpenAI stub listening on {STUB_HOST}:{STUB_PORT}")
    print("POST /v1/embeddings           to get embedding response")
    print("POST /v1/images/generations   to get image response")
    print("POST /v1/audio/transcriptions to get transcription response")
    print("POST /v1/audio/speech         to get audio bytes")
    print("POST /__faults                no-op control endpoint")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        srv.shutdown()
