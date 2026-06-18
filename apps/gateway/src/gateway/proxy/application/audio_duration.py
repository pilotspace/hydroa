"""Server-side audio-duration derivation for STT billing.

Closes the silent-$0 leak: when a transcription caller omits `verbose_json`, the
upstream body carries no `duration`, so flat per-second billing falls back to $0.
This module decodes the uploaded audio header in-memory (tinytag, pure-Python, no
native deps) to recover the true length so the call bills accurately.

Contract FROZEN @ stt-duration-derivation (TASK.md §3):
  derive_duration_seconds(data: bytes) -> float | None
    - decode via TinyTag.get(file_obj=BytesIO(data), tags=False, image=False)
    - any Exception -> None (total over bad input — NEVER raises)
    - valid duration iff a finite, strictly-positive int/float (not bool) -> float
    - else -> None

Design-for-failure: this is a billing-accuracy helper, never an availability gate.
A bad/short/unknown header degrades to None (the caller logs + bills $0) instead of
failing the transcription the user already paid an upstream for.
"""

from __future__ import annotations

import math
from io import BytesIO

from tinytag import TinyTag


def derive_duration_seconds(data: bytes) -> float | None:
    """Return the audio's duration in seconds, or None if it cannot be derived.

    Content-sniffed from the bytes themselves; the upload filename / content-type
    are intentionally ignored (a caller may lie). Total and pure: every malformed,
    truncated, or unsupported input yields None rather than raising.
    """
    try:
        tag = TinyTag.get(file_obj=BytesIO(data), tags=False, image=False)
        duration = tag.duration
    except Exception:
        # UnsupportedFormatError, parse errors, empty/short headers — all non-fatal.
        return None

    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return float(duration)
