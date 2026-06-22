"""Pure-stdlib AWS EventStream binary decoder for Bedrock ConverseStream.

No boto3 / botocore dependency — stdlib only (struct, binascii).

Public surface (frozen contract, v20 task 3):
  EventStreamError            Exception raised on CRC mismatch or truncation.
  decode_event_stream(data)   Iterator[tuple[dict[str,str], bytes]]
                              Single-pass decoder over the full wire buffer.

AWS EventStream frame layout
-----------------------------
  [0:4]   total_len       uint32 BE
  [4:8]   headers_len     uint32 BE
  [8:12]  prelude_crc     uint32 BE  (CRC of bytes [0:8])
  [12:12+headers_len]  headers bytes
  [12+headers_len:-4]  payload bytes
  [-4:]   message_crc     uint32 BE  (CRC of all preceding bytes in frame)

Header wire encoding (repeated until headers_len bytes consumed)
  1 byte      name_len
  name_len    name (UTF-8)
  1 byte      header_type
  type-specific value bytes (see _HEADER_WIDTHS / string handling below)
"""

from __future__ import annotations

import binascii
import struct
from collections.abc import AsyncIterator, Iterator

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class EventStreamError(Exception):
    """Raised on CRC mismatch, truncated frame, or unknown header type."""


# ---------------------------------------------------------------------------
# Header type widths (fixed-width types only; variable-width handled inline)
# ---------------------------------------------------------------------------
#
#  0  true        → 0 bytes
#  1  false       → 0 bytes
#  2  byte        → 1 byte
#  3  short       → 2 bytes
#  4  int         → 4 bytes
#  5  long        → 8 bytes
#  6  bytearray   → 2-byte BE length prefix + that many bytes
#  7  string      → 2-byte BE length prefix + that many bytes (UTF-8, stored)
#  8  timestamp   → 8 bytes
#  9  uuid        → 16 bytes

_FIXED_WIDTHS: dict[int, int] = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4, 5: 8, 8: 8, 9: 16}


# ---------------------------------------------------------------------------
# decode_event_stream
# ---------------------------------------------------------------------------


def _read_prelude(buf: bytes | bytearray, off: int) -> tuple[int, int]:
    """Parse + CRC-validate the 12-byte prelude at ``off`` (caller ensures ≥12 bytes).

    Returns (total_len, headers_len). Raises EventStreamError on prelude CRC mismatch.
    """
    total_len, headers_len = struct.unpack(">II", buf[off : off + 8])
    prelude_crc = struct.unpack(">I", buf[off + 8 : off + 12])[0]
    computed_prelude_crc = binascii.crc32(buf[off : off + 8]) & 0xFFFFFFFF
    if computed_prelude_crc != prelude_crc:
        raise EventStreamError(
            f"Prelude CRC mismatch at offset {off}: "
            f"expected {prelude_crc:#010x}, got {computed_prelude_crc:#010x}"
        )
    return total_len, headers_len


def _parse_frame(msg: bytes, off: int) -> tuple[dict[str, str], bytes]:
    """Validate the message CRC and parse ONE complete frame (``msg`` = exactly total_len bytes).

    ``off`` is the buffer offset, used only in error messages. Returns (headers, payload)
    where headers contains ONLY the string-typed (type code 7) header values.

    Raises:
        EventStreamError: On message CRC mismatch or unknown header type byte.
    """
    headers_len = struct.unpack(">I", msg[4:8])[0]

    # ── Message CRC ───────────────────────────────────────────────────────
    message_crc = struct.unpack(">I", msg[-4:])[0]
    computed_msg_crc = binascii.crc32(msg[:-4]) & 0xFFFFFFFF
    if computed_msg_crc != message_crc:
        raise EventStreamError(
            f"Message CRC mismatch at offset {off}: "
            f"expected {message_crc:#010x}, got {computed_msg_crc:#010x}"
        )

    # ── Slice headers + payload ────────────────────────────────────────────
    headers_bytes = msg[12 : 12 + headers_len]
    payload = msg[12 + headers_len : -4]

    # ── Parse headers ──────────────────────────────────────────────────────
    headers: dict[str, str] = {}
    h = 0
    hlen = len(headers_bytes)

    while h < hlen:
        # Name
        name_len = headers_bytes[h]
        h += 1
        name = headers_bytes[h : h + name_len].decode("utf-8")
        h += name_len

        # Type byte
        htype = headers_bytes[h]
        h += 1

        if htype in _FIXED_WIDTHS:
            width = _FIXED_WIDTHS[htype]
            h += width
            # Fixed-width types are NOT stored (none are string-valued)
        elif htype == 6:
            # bytearray: 2-byte length prefix + data (walk past, not stored)
            vlen = struct.unpack(">H", headers_bytes[h : h + 2])[0]
            h += 2 + vlen
        elif htype == 7:
            # string: 2-byte length prefix + UTF-8 data (STORED)
            vlen = struct.unpack(">H", headers_bytes[h : h + 2])[0]
            h += 2
            value = headers_bytes[h : h + vlen].decode("utf-8")
            h += vlen
            headers[name] = value
        else:
            raise EventStreamError(
                f"Unknown header type {htype!r} for header {name!r} at offset {off}"
            )

    return headers, payload


def decode_event_stream(data: bytes) -> Iterator[tuple[dict[str, str], bytes]]:
    """Decode a full AWS EventStream buffer into (headers_dict, payload) tuples.

    Single-pass decoder.  Iterates all frames in wire order.  Only string-typed
    (type code 7) header values are retained in the returned dict; all other
    types are walked past without storing.

    Args:
        data: Complete wire-format EventStream buffer (may contain multiple frames).

    Yields:
        (headers, payload) where headers is dict[str, str] containing ONLY the
        string-typed (:event-type, :content-type, :message-type, …) entries,
        and payload is the raw bytes of the frame body.

    Raises:
        EventStreamError: On prelude CRC mismatch, message CRC mismatch,
                          truncated buffer, or unknown header type byte.
    """
    off = 0
    total = len(data)

    while off < total:
        # ── Prelude ────────────────────────────────────────────────────────
        if total - off < 12:
            raise EventStreamError(
                f"Truncated EventStream: only {total - off} bytes remain at offset {off}; "
                "need at least 12 for the prelude"
            )

        total_len, _headers_len = _read_prelude(data, off)

        # Validate that the full frame fits in the remaining buffer
        if off + total_len > total:
            raise EventStreamError(
                f"Truncated EventStream at offset {off}: "
                f"frame declares total_len={total_len} but only {total - off} bytes remain"
            )

        headers, payload = _parse_frame(data[off : off + total_len], off)
        yield headers, payload
        off += total_len


async def aiter_event_stream(
    chunks: AsyncIterator[bytes],
) -> AsyncIterator[tuple[dict[str, str], bytes]]:
    """Incrementally decode an AWS EventStream from a byte-chunk async iterator.

    Yields each (headers, payload) the instant its length-prefixed frame is fully
    buffered — correctly reassembling a frame split across chunks AND splitting
    multiple frames packed into one chunk. Only the partial trailing frame is held.

    "Not enough bytes yet" is NOT an error — the decoder waits for the next chunk.

    Raises:
        EventStreamError: On a prelude/message CRC mismatch (corrupt frame), or when
            the stream ends with a partial trailing frame (truncation).
    """
    buf = bytearray()
    async for chunk in chunks:
        buf += chunk
        while True:
            if len(buf) < 12:
                break  # need more bytes for the prelude
            total_len, _headers_len = _read_prelude(buf, 0)
            if len(buf) < total_len:
                break  # need more bytes for the full frame
            headers, payload = _parse_frame(bytes(buf[:total_len]), 0)
            yield headers, payload
            del buf[:total_len]
    if buf:
        raise EventStreamError(
            f"Truncated EventStream: {len(buf)} trailing bytes do not form a complete frame"
        )
