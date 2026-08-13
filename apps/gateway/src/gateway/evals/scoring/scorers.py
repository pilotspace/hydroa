"""The four deterministic scorers + the composed dispatcher (deterministic-scorers §3, FROZEN @ v1).

PURE and TOTAL: ``DeterministicScorer.score`` maps EVERY (assertion, output_text) to a
``ScoreResult`` — a supported kind scored, an unsupported kind or malformed ``expected`` failed
CLOSED with a reason, a pathological regex/JSON bounded — and NEVER raises (R:UNSCOREABLE_CRASH),
never silently passes an unscoreable case (R:SILENT_PASS), and never varies for identical inputs
(M1 / R:NONDETERMINISM: no clock, no randomness, no IO, no locale-dependent operation).

ReDoS containment (M6) reuses the house heuristic from
``gateway.tenants.api.guardrail_router`` — a tenant-authored pattern is rejected as unscoreable
if it exceeds a byte cap, nests a quantifier, or uses a backreference (the catastrophic-
backtracking vectors) — a STATIC check, not a runtime timeout, so a runaway can never start.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from gateway.evals.scoring.ports import ScoreResult

# Bounds (M6). Output is length-capped before regex/JSON work so a single case cannot pin CPU.
_MAX_OUTPUT_LEN = 256 * 1024
# Regex ReDoS heuristic — copied deliberately from guardrail_router's V-checks (one source of
# truth for "is this tenant pattern safe to run"); a match here means REJECT (unscoreable).
_MAX_PATTERN_BYTES = 256
_NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*[+*?][^)]*\)[+*?{]")
_BACKREFERENCE_RE = re.compile(r"\\[1-9]")
_NAMED_BACKREFERENCE_TOKEN = "(?P="  # noqa: S105 — regex syntax fragment, not a secret


def _fail(kind: str, detail: str) -> ScoreResult:
    return ScoreResult(passed=False, kind=kind, detail=detail)


def _score_exact(kind: str, expected: object, output_text: str) -> ScoreResult:
    if not isinstance(expected, str):
        return _fail(kind, "exact assertion requires a string `expected`")
    if output_text == expected:
        return ScoreResult(passed=True, kind=kind, detail=None)
    return _fail(kind, "output did not exactly equal the expected string")


def _score_contains(kind: str, expected: object, output_text: str) -> ScoreResult:
    if not isinstance(expected, str):
        return _fail(kind, "contains assertion requires a string `expected`")
    if expected in output_text:
        return ScoreResult(passed=True, kind=kind, detail=None)
    return _fail(kind, "expected substring not found in output")


def _score_regex(kind: str, expected: object, output_text: str) -> ScoreResult:
    if not isinstance(expected, str):
        return _fail(kind, "regex assertion requires a string pattern")
    if len(expected.encode()) > _MAX_PATTERN_BYTES:
        return _fail(kind, "regex pattern exceeds the maximum length")
    # M6: reject catastrophic-backtracking shapes BEFORE compiling/matching — a runaway that
    # never starts cannot pin CPU. (Same heuristic guardrail_router applies at PUT time.)
    if _NESTED_QUANTIFIER_RE.search(expected):
        return _fail(kind, "regex pattern rejected: nested quantifier (ReDoS risk)")
    if _BACKREFERENCE_RE.search(expected) or _NAMED_BACKREFERENCE_TOKEN in expected:
        return _fail(kind, "regex pattern rejected: backreference (ReDoS risk)")
    try:
        compiled = re.compile(expected)
    except re.error:
        return _fail(kind, "invalid regex syntax")
    capped = output_text[:_MAX_OUTPUT_LEN]
    if compiled.search(capped) is not None:
        return ScoreResult(passed=True, kind=kind, detail=None)
    return _fail(kind, "regex did not match the output")


def _score_json_schema(kind: str, expected: object, output_text: str) -> ScoreResult:
    # The schema itself must be valid (a stored case can carry a bad `expected`, M5).
    try:
        Draft202012Validator.check_schema(expected)  # type: ignore[arg-type]
    except SchemaError:
        return _fail(kind, "`expected` is not a valid JSON Schema")
    except Exception:
        return _fail(kind, "`expected` is not a usable JSON Schema")
    # The output must parse as JSON — a length cap + catching RecursionError bounds a deeply
    # nested payload (M6); empty/invalid output is a fail, never an error (A4).
    try:
        parsed = json.loads(output_text[:_MAX_OUTPUT_LEN])
    except (json.JSONDecodeError, ValueError, RecursionError):
        return _fail(kind, "output is not valid JSON")
    if Draft202012Validator(expected).is_valid(parsed):  # type: ignore[arg-type]
        return ScoreResult(passed=True, kind=kind, detail=None)
    return _fail(kind, "output did not validate against the schema")


class DeterministicScorer:
    """The composed scorer — dispatches on ``assertion['kind']``. Implements the ``Scorer`` port."""

    def score(self, *, assertion: Mapping[str, object], output_text: str) -> ScoreResult:
        raw_kind = assertion.get("kind")
        kind = raw_kind if isinstance(raw_kind, str) else str(raw_kind)
        expected = assertion.get("expected")
        try:
            if kind == "exact":
                return _score_exact(kind, expected, output_text)
            if kind == "contains":
                return _score_contains(kind, expected, output_text)
            if kind == "regex":
                return _score_regex(kind, expected, output_text)
            if kind == "json_schema":
                return _score_json_schema(kind, expected, output_text)
            # M4: a kind outside the four supported kinds — fail closed, name it, never pass.
            return _fail(kind, "unsupported scorer kind")
        except Exception:
            # R:UNSCOREABLE_CRASH backstop: a scorer must total-map every input to a
            # ScoreResult. Payload-free detail — never echo the output into the reason.
            return _fail(kind, "scorer could not evaluate this case")
