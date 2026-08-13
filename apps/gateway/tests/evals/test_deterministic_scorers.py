"""RED suite for deterministic-scorers (gateway/evals/scoring) — the R7 L1 scorer library.

Contract under test (deterministic-scorers PLAN.md §3, FROZEN @ sha256:7986be6c):
  score(assertion={kind, expected}, output_text) -> ScoreResult{passed, kind, detail}
  Four kinds: exact · contains · regex · json_schema. PURE — no IO, no session, no network.

INVARIANTS asserted here (M1-M6):
  - determinism: same (assertion, output_text) -> byte-identical ScoreResult every call (M1).
  - fail-closed: unsupported kind, malformed expected, or a bounded-out regex/schema ->
    passed=False with a reason, NEVER a raise, NEVER a silent passed=True (M4/M5/M6).
  - bounded: a catastrophic-backtracking regex / deeply-nested JSON resolves fast, never hangs (M6).
  - purity: driven with NO session, NO app, NO network (M1/A1).

RED until gateway/evals/scoring exists. DO NOT edit to make pass — that is Build's job.
"""

from __future__ import annotations

from gateway.evals.scoring.ports import ScoreResult
from gateway.evals.scoring.scorers import DeterministicScorer


def _score(kind: str, expected: object, output_text: str) -> ScoreResult:
    return DeterministicScorer().score(
        assertion={"kind": kind, "expected": expected}, output_text=output_text
    )


# ---------------------------------------------------------------------------
# M2 — the four kinds each pass their own case and fail their own case
# ---------------------------------------------------------------------------


def test_four_scorers_pass_and_fail_their_own_case() -> None:
    """covers: M2, A2, A3 — each kind passes a case it must and fails one it must (pure inputs)."""
    # exact — byte-level, case + whitespace sensitive (A3)
    assert _score("exact", "hello", "hello").passed is True
    assert _score("exact", "hello", "Hello").passed is False
    assert _score("exact", "hello", "hello\n").passed is False

    # contains — case-sensitive substring
    assert _score("contains", "ell", "hello").passed is True
    assert _score("contains", "ELL", "hello").passed is False

    # regex — search semantics
    assert _score("regex", r"\d{3}", "abc 456 def").passed is True
    assert _score("regex", r"^\d+$", "abc 456").passed is False

    # json_schema — output_text must parse as JSON and validate
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    assert _score("json_schema", schema, '{"ok": true}').passed is True
    assert _score("json_schema", schema, '{"ok": "yes"}').passed is False


# ---------------------------------------------------------------------------
# M1 — determinism: identical inputs -> byte-identical ScoreResult, every call
# ---------------------------------------------------------------------------


def test_scoring_is_deterministic_across_calls() -> None:
    """covers: M1, E4, R:NONDETERMINISM — the gate's core invariant."""
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "number"}}}
    cases = [
        ("exact", "x", "x"),
        ("contains", "z", "abc"),
        ("regex", r"[a-z]+", "hello"),
        ("json_schema", schema, '{"n": 1}'),
    ]
    for kind, expected, output in cases:
        first = _score(kind, expected, output)
        second = _score(kind, expected, output)
        assert first == second, f"{kind} not deterministic: {first} != {second}"


# ---------------------------------------------------------------------------
# M4 — an unsupported kind fails CLOSED, never crashes, never silently passes
# ---------------------------------------------------------------------------


def test_unsupported_kind_fails_closed_not_crash() -> None:
    """covers: M4, E1, R:SILENT_PASS, R:UNSCOREABLE_CRASH — a stored-but-unscoreable kind."""
    result = _score("totally-unknown-kind", "x", "x")
    assert result.passed is False
    assert result.kind == "totally-unknown-kind"
    assert result.detail and "unsupported" in result.detail.lower()


# ---------------------------------------------------------------------------
# M5 — a malformed `expected` for its kind fails closed, never raises
# ---------------------------------------------------------------------------


def test_malformed_expected_fails_closed() -> None:
    """covers: M5, E3, R:UNSCOREABLE_CRASH — a stored case may carry a bad expected."""
    # un-compilable regex
    bad_regex = _score("regex", "(unclosed", "anything")
    assert bad_regex.passed is False and bad_regex.detail

    # non-string contains needle
    bad_contains = _score("contains", 123, "hello")
    assert bad_contains.passed is False and bad_contains.detail

    # expected is not a valid JSON Schema
    bad_schema = _score("json_schema", {"type": "not-a-real-type"}, '{"a": 1}')
    assert bad_schema.passed is False and bad_schema.detail


# ---------------------------------------------------------------------------
# M6 — regex / json scoring is bounded: a pathological input resolves fast, never hangs
# ---------------------------------------------------------------------------


def test_regex_and_json_scoring_is_bounded() -> None:
    """covers: M6, E2 — a catastrophic-backtracking regex + deeply nested JSON are bounded.

    Proven CAUSALLY, not by a wall-clock threshold: M6 bounds the runaway by REJECTING the
    dangerous pattern BEFORE it reaches the matching engine (the guardrail nested-quantifier
    heuristic), so the proof is that the evil pattern comes back UNSCOREABLE with a rejection
    reason — it never ran the matcher, so there is no backtracking to time. (An elapsed-time
    assertion would be a bet on host speed; the structural rejection is the real guarantee, and
    if the code DID enter an unbounded match the test would hang and pytest-timeout would fail
    it — no threshold of ours required.)
    """
    # classic catastrophic-backtracking pattern: rejected by the nested-quantifier heuristic,
    # so it is UNSCOREABLE (never matched) rather than a timing gamble.
    evil = _score("regex", "(a+)+$", "a" * 5000 + "!")
    assert evil.passed is False
    assert evil.detail and ("nested quantifier" in evil.detail or "redos" in evil.detail.lower())

    # deeply nested JSON: the length cap + RecursionError catch contain it — a RETURNED
    # ScoreResult (whatever its verdict) IS the proof it was bounded, not a stack blow-up.
    # A depth past CPython's parser limit is caught and fails closed; a shallow one parses —
    # either way it must be a verdict, never a crash. Use a depth that forces the guard.
    nested = _score("json_schema", {"type": "array"}, "[" * 200_000 + "]" * 200_000)
    assert isinstance(nested, ScoreResult)  # contained: a verdict, not a crash/hang
    assert nested.passed is False  # 200k-deep exceeds the parse bound -> fail closed


# ---------------------------------------------------------------------------
# A4 — empty output scores by the normal rule, never errors
# ---------------------------------------------------------------------------


def test_empty_output_scores_not_errors() -> None:
    """covers: A4, E5 — a model that returned nothing / a timed-out case scores, never raises."""
    assert _score("exact", "", "").passed is True
    assert _score("exact", "x", "").passed is False
    assert _score("contains", "x", "").passed is False
    assert _score("regex", r"\w+", "").passed is False
    assert _score("json_schema", {"type": "object"}, "").passed is False  # "" is not valid JSON


# ---------------------------------------------------------------------------
# A6 / M3 — a fail's detail is actionable and payload-free; a pass's detail is None
# ---------------------------------------------------------------------------


def test_fail_detail_is_actionable_and_payload_free() -> None:
    """covers: A6, M3 — the operator's per-case drill-down reads `detail`."""
    big_secret = "SENSITIVE-PAYLOAD-" + "x" * 500
    fail = _score("contains", "needle-not-present", big_secret)
    assert fail.passed is False
    assert fail.detail and len(fail.detail) > 0
    # detail must NOT echo the full output payload verbatim
    assert big_secret not in fail.detail

    ok = _score("exact", "match", "match")
    assert ok.passed is True
    assert ok.detail is None


# ---------------------------------------------------------------------------
# A1 — a scorer is a pure library function: NO session, NO app, NO network
# ---------------------------------------------------------------------------


def test_scorers_are_pure_no_io() -> None:
    """covers: A1, M1 — every kind is driven with pure inputs and no async/DB/app fixture.

    This test function is SYNC and takes NO fixtures (no `client`, no `app`, no `db_session`):
    if scoring needed a session/app/network it could not run here at all. That the four kinds
    each return a ScoreResult from bare inputs IS the proof of purity + re-scorability.
    """
    scorer = DeterministicScorer()
    for kind, expected, output in [
        ("exact", "a", "a"),
        ("contains", "a", "ba"),
        ("regex", r"a", "a"),
        ("json_schema", {"type": "string"}, '"a"'),
    ]:
        result = scorer.score(assertion={"kind": kind, "expected": expected}, output_text=output)
        assert isinstance(result, ScoreResult)


# ---------------------------------------------------------------------------
# A5 — an assertion carries EXACTLY ONE kind; ScoreResult has no combinator field
# ---------------------------------------------------------------------------


def test_score_result_is_single_kind_no_combinator() -> None:
    """covers: A5 — R7 assertions are single-kind (no AND/OR); the result reflects one kind."""
    field_names = {f.name for f in __import__("dataclasses").fields(ScoreResult)}
    assert field_names == {"passed", "kind", "detail"}, field_names
    result = _score("contains", "x", "axb")
    assert result.kind == "contains"  # a single kind, not a composed/combinator verdict
