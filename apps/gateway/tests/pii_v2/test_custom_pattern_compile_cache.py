"""Compiled tenant custom-PII patterns are cached (perf: no per-request recompile).

hydroa-envoy-top3 #2 — `_compile_custom_patterns` was called on every chat request with
a custom PII guardrail and ran `re.compile()` afresh each time, even though the config
only changes on an admin PUT. The compile is now cached keyed by the pattern CONTENT, so
an unchanged config reuses the compiled Pattern objects and a config edit naturally keys
to a fresh entry (no manual invalidation). This suite pins that contract; the redaction
behaviour itself is covered by test_pii_v2.py.
"""

from __future__ import annotations

from gateway.proxy.infrastructure import guardrail_evaluator as ge


def _cfg(name: str = "EMP", pattern: str = r"EMP-\d{4}") -> dict:
    return {"pii_custom_patterns": [{"name": name, "pattern": pattern}]}


def test_equal_configs_reuse_the_whole_compiled_result() -> None:
    a = ge._compile_custom_patterns(_cfg())
    b = ge._compile_custom_patterns(_cfg())
    assert a and b
    # The (pattern, literal) pair is the SAME object across calls — proving the whole
    # result is memoised, not just re's built-in per-pattern compile cache. (Rebuilding
    # the list every call, as the un-cached code did, yields distinct tuple objects.)
    assert a[0] is b[0]
    assert a[0][1] == "[EMP_REDACTED]"


def test_distinct_configs_compile_distinctly() -> None:
    a = ge._compile_custom_patterns(_cfg("A", r"a\d"))
    b = ge._compile_custom_patterns(_cfg("B", r"b\d"))
    assert a[0][0].pattern == r"a\d"
    assert b[0][0].pattern == r"b\d"
    assert a[0][0] is not b[0][0]


def test_invalid_pattern_is_skipped() -> None:
    assert ge._compile_custom_patterns({"pii_custom_patterns": [{"name": "BAD", "pattern": "("}]}) == []


def test_empty_or_missing_returns_empty() -> None:
    assert ge._compile_custom_patterns({}) == []
    assert ge._compile_custom_patterns({"pii_custom_patterns": []}) == []
    assert ge._compile_custom_patterns({"pii_custom_patterns": "nonsense"}) == []
