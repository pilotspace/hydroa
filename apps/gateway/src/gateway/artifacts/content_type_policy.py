"""Pure helpers for the artifact upload content-type allow-policy.

All three functions are stateless and have no IO; they are safe to import
anywhere and straightforward to unit-test in isolation.

Contract (frozen v1, Tin 2026-06-30):
  normalize(s) = s.split(";", 1)[0].strip().lower()
  Empty allow-list CSV "" → allow ANY content_type (byte-identical default).
  Non-empty → normalized request value must be in the normalized allow-list.
"""

from __future__ import annotations


def normalize_content_type(value: str) -> str:
    """Return the bare media-type of *value*, lowercased and stripped.

    Splits on the first semicolon to drop parameters (e.g. ``; charset=utf-8``),
    then strips surrounding whitespace and lowercases.

    Examples::

        normalize_content_type("IMAGE/PNG; charset=utf-8")  # "image/png"
        normalize_content_type("  text/Plain  ")            # "text/plain"
    """
    return value.split(";", 1)[0].strip().lower()


def parse_allowed_content_types(csv: str) -> frozenset[str]:
    """Parse a comma-separated list of media-types into a normalized frozen set.

    Blank entries (empty strings after splitting) are silently dropped.
    Each entry is normalized via :func:`normalize_content_type`.

    An empty *csv* returns an empty ``frozenset``.

    Examples::

        parse_allowed_content_types("")                         # frozenset()
        parse_allowed_content_types("image/png, IMAGE/JPEG ,,")
        # frozenset({"image/png", "image/jpeg"})
    """
    if not csv:
        return frozenset()
    return frozenset(
        normalize_content_type(entry)
        for entry in csv.split(",")
        if entry.strip()
    )


def is_content_type_allowed(content_type: str, allowed_csv: str) -> bool:
    """Return ``True`` when *content_type* is permitted by *allowed_csv*.

    An empty *allowed_csv* (the default) allows every content-type (early
    return — no set is built, behaviour is byte-identical to pre-policy).

    When *allowed_csv* is non-empty the normalized request value must be a
    member of the normalized allow-list; otherwise ``False`` is returned.

    Examples::

        is_content_type_allowed("anything/here", "")              # True
        is_content_type_allowed("IMAGE/PNG; q=1", "image/png")   # True
        is_content_type_allowed("text/html", "image/png")        # False
    """
    if not allowed_csv:
        return True
    allowed = parse_allowed_content_types(allowed_csv)
    return normalize_content_type(content_type) in allowed
