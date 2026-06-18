"""Pure, total non-finite-float sanitizer for JSON-passthrough response bodies.

Starlette's `JSONResponse.render` serializes with `allow_nan=False`, so any non-finite
float (inf / -inf / nan) anywhere in an echoed upstream body raises
`ValueError: Out of range float values are not JSON compliant` → the request 500s on
RESPONSE serialization. `sanitize_non_finite` rebuilds the structure, replacing every
non-finite float with `None` (the standard "not representable" sentinel that keeps the
response shape), and returns `(sanitized_copy, count_of_substitutions)`.

stt-nonfinite-passthrough TASK.md §3 (FROZEN): null replacement · full-tree recursion ·
TOTAL + PURE (never raises, never mutates the input).
"""

from __future__ import annotations

import math
from typing import Any


def sanitize_non_finite(obj: Any) -> tuple[Any, int]:
    """Return ``(obj with every non-finite float replaced by None, count_replaced)``.

    Recurses the two JSON container types — ``dict`` and ``list`` — which is exhaustive
    for a ``json.loads``-decoded body (the only caller: an echoed upstream response).
    Every other value is returned as-is, including any non-JSON container (``tuple`` /
    ``set``): its contents are NOT inspected, since a decoded JSON body never holds one.
    A ``bool`` is NOT a float case (``True``/``False`` pass through, since ``bool`` is a
    subclass of ``int``, not ``float``). PURE: new ``dict``/``list`` containers are built,
    the input is never mutated. TOTAL: never raises on any input.
    """
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        count = 0
        for key, value in obj.items():
            sanitized, sub = sanitize_non_finite(value)
            out[key] = sanitized
            count += sub
        return out, count
    if isinstance(obj, list):
        items: list[Any] = []
        count = 0
        for value in obj:
            sanitized, sub = sanitize_non_finite(value)
            items.append(sanitized)
            count += sub
        return items, count
    if isinstance(obj, float) and not math.isfinite(obj):
        return None, 1
    return obj, 0
