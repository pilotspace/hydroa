import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """UUIDv7 (RFC 9562): 48-bit unix-ms timestamp + random — time-ordered PKs.

    Python 3.12 stdlib has no uuid7; drop this once we run on 3.14+.
    """
    unix_ms = time.time_ns() // 1_000_000
    raw = bytearray(unix_ms.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(raw))
