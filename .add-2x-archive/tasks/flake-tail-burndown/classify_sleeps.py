"""Classify every `asyncio.sleep(...)` site in apps/gateway/tests.

Three buckets, per todo #79's rule:

  POSITIVE   — the sleep waits for something to APPEAR. Convertible to
               tests._polling.poll_until without changing what is asserted.
  NEGATIVE   — the sleep proves something NEVER happens. Polling would return
               the instant the first row exists and never give the unwanted
               write a chance, turning a real assertion vacuous. KEEP AS-IS.
  STRUCTURAL — not an assert-wait at all: a fake/stub simulating latency, a
               TTL/expiry advance, a `sleep(0)` yield, a worker-loop tick.
  UNKNOWN    — needs a human/per-site read.

Emits JSONL so each site can be judged and tracked individually.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TESTS = Path("/Users/tindang/workspaces/tind-repo/ai-proxy/apps/gateway/tests")

SLEEP_RE = re.compile(r"asyncio\.sleep\(")

# --- signals -----------------------------------------------------------------

# "prove it never happened" — the sleep IS the test
NEGATIVE_SIGNALS = (
    r"==\s*0\b",
    r"len\([^)]*\)\s*==\s*0",
    r"\bis\s+None\b",
    r"\bnot\s+called\b",
    r"assert\s+not\b",
    r"\bmust\s+not\b",
    r"\bnever\b",
    r"\bno\s+(second|additional|further|extra|new|other)\b",
    r"\bunchanged\b",
    r"\bstill\s+(0|zero|empty|none)\b",
    r"\bdid\s+not\b",
    r"\bdoes\s+not\s+(fire|write|emit|record|run)\b",
    r"\bshould\s+not\b",
    r"\bempty\b",
    r"\bsuppress",
    r"\bdedup",
    r"\bidempotent\b",
)

# "wait for it to show up"
POSITIVE_SIGNALS = (
    r">=\s*1\b",
    r"len\([^)]*\)\s*>=",
    r"len\([^)]*\)\s*==\s*[1-9]",
    r"\bis\s+not\s+None\b",
    r"\bfire-and-forget\b",
    r"\ballow\b.*\b(write|record|flush|land|persist)",
    r"\blet\b.*\b(land|flush|complete|finish|drain|age)",
    r"\bwait\s+for\b",
    r"\buntil\b",
    r"\beventually\b",
    r"\bexpected\s+at\s+least\b",
    r"\bassert\s+len\(",
    r"\bfetchall\(\)",
    r"\bfetchone\(\)",
    r"\bcalls\s*==\s*[1-9]",
)

# not a race at all
STRUCTURAL_SIGNALS = (
    r"async\s+def\s+_?(fake|stub|slow|delayed|hang)",
    r"\bsimulat",
    r"\blatency\b",
    r"\bslow\b",
    r"\bttl\b",
    r"\bexpir",
    r"\bsleep\(0\)",
    r"\bsleep\(0\.0\)",
    r"\byield\s+to\s+the\s+loop\b",
    r"\blet\s+the\s+loop\b",
    r"\btick\b",
    r"\bcancel",
    r"\bshutdown\b",
    r"\btimeout\b",
    r"\bmonotonic\b",
)


def _any(patterns: tuple[str, ...], text: str) -> list[str]:
    low = text.lower()
    return [p for p in patterns if re.search(p, low)]


def classify(before: str, after: str) -> tuple[str, list[str]]:
    """Weigh the 8 lines before (intent/comment) and 12 after (the assertion)."""
    window = before + "\n" + after

    neg = _any(NEGATIVE_SIGNALS, after) or _any(NEGATIVE_SIGNALS, before)
    pos = _any(POSITIVE_SIGNALS, after) or _any(POSITIVE_SIGNALS, before)
    struct = _any(STRUCTURAL_SIGNALS, window)

    # A structural site is not an assert-wait; it outranks both, EXCEPT when the
    # lines after it clearly fetch-and-assert (a TTL advance can still be followed
    # by a positive wait).
    if struct and not (pos or neg):
        return "STRUCTURAL", struct
    # NEGATIVE outranks POSITIVE: converting a negative site is the one move that
    # silently weakens a test, so any negative signal forces a human read.
    if neg and pos:
        return "UNKNOWN", ["MIXED"] + neg + pos
    if neg:
        return "NEGATIVE", neg
    if pos:
        return "POSITIVE", pos
    return "UNKNOWN", []


def main() -> int:
    sites = []
    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if not SLEEP_RE.search(line):
                continue
            before = "\n".join(lines[max(0, i - 8) : i])
            after = "\n".join(lines[i + 1 : i + 13])
            bucket, why = classify(before, after)
            sites.append(
                {
                    "file": str(path.relative_to(TESTS.parent)),
                    "line": i + 1,
                    "code": line.strip(),
                    "bucket": bucket,
                    "signals": why[:4],
                }
            )

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sleep-sites.jsonl")
    out.write_text("\n".join(json.dumps(s) for s in sites) + "\n")

    counts: dict[str, int] = {}
    for s in sites:
        counts[s["bucket"]] = counts.get(s["bucket"], 0) + 1
    print(f"{len(sites)} sites across {len({s['file'] for s in sites})} files -> {out}")
    for bucket in ("POSITIVE", "NEGATIVE", "STRUCTURAL", "UNKNOWN"):
        print(f"  {bucket:11s} {counts.get(bucket, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
