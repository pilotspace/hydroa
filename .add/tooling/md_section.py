"""md_section — fence-aware markdown section slicer.

Exposes a single pure-stdlib function:
    section(text: str, heading: str) -> str

The function slices a markdown document from the first occurrence of `heading`
(INCLUSIVE) up to — but NOT including — the next line that starts with "## "
OUTSIDE a ``` fence.  Lines that start with ``` toggle fence state; a "## " line
inside an open fence never terminates the slice.  An unclosed fence runs to
end-of-text with no exception.  If `heading` is absent, "" is returned.

No IO.  No regex.  Stdlib only.
"""
from __future__ import annotations


def section(text: str, heading: str) -> str:
    """Return the markdown section starting at *heading* (inclusive).

    Parameters
    ----------
    text:    The full markdown document.
    heading: The exact heading string to locate (e.g. ``"## Wave ledger"``).

    Returns
    -------
    The text from the first character of *heading* up to (not including) the
    first line that starts with ``"## "`` found OUTSIDE a fence.  Returns ``""``
    if *heading* is not present in *text*.
    """
    start = text.find(heading)
    if start == -1:
        return ""

    # Slice from the heading start to end-of-text, then scan line by line.
    tail = text[start:]
    lines = tail.split("\n")

    in_fence = False
    collected: list[str] = []

    for i, line in enumerate(lines):
        # Check terminator FIRST (against current fence state), before any toggle.
        # The heading line itself (i == 0) is always collected.
        if i > 0 and not in_fence and line.startswith("## "):
            # This is an unfenced H2 — it terminates the slice.
            break

        collected.append(line)

        # Toggle fence state AFTER deciding to include the line.
        if line.startswith("```"):
            in_fence = not in_fence

    # Re-join the collected lines.  The original text used "\n" as the delimiter.
    return "\n".join(collected)
