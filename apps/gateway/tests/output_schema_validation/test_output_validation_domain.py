"""Unit suite — gateway.proxy.domain.output_validation (pure, no IO).

Covers check_schema_well_formed (M3), validate_model_output (M4 incl. the n>1
edge case), and truncate_raw_output (M12) in isolation from CompletionUseCase —
per the BUILD strategy's "red/green in isolation" sub-loop (TASK.md §5 step 2).
"""

from __future__ import annotations

from gateway.proxy.domain.output_validation import (
    check_schema_well_formed,
    truncate_raw_output,
    validate_model_output,
)

from .conftest import MALFORMED_SCHEMA, SCHEMA, make_multi_choice_body, make_upstream_body

# ---------------------------------------------------------------------------
# check_schema_well_formed (M3)
# ---------------------------------------------------------------------------


def test_well_formed_schema_returns_none() -> None:
    assert check_schema_well_formed(SCHEMA) is None


def test_malformed_schema_returns_reason_string() -> None:
    reason = check_schema_well_formed(MALFORMED_SCHEMA)
    assert reason is not None
    assert isinstance(reason, str) and reason  # non-empty diagnostic


def test_malformed_schema_never_raises() -> None:
    # Docstring contract: "Never raises" — a wildly malformed input must still
    # return a string, not propagate an exception.
    reason = check_schema_well_formed({"type": "object", "properties": "not-a-dict"})
    assert isinstance(reason, str)


# ---------------------------------------------------------------------------
# validate_model_output (M4)
# ---------------------------------------------------------------------------


def test_valid_content_passes() -> None:
    body = make_upstream_body('{"answer": "yes"}')
    outcome = validate_model_output(SCHEMA, body)
    assert outcome["valid"] is True
    assert outcome["errors"] == []
    assert outcome["parsed"] == {"answer": "yes"}


def test_schema_mismatch_fails() -> None:
    body = make_upstream_body('{"wrong_field": "nope"}')
    outcome = validate_model_output(SCHEMA, body)
    assert outcome["valid"] is False
    assert outcome["errors"]


def test_unparseable_json_content_fails_like_a_mismatch() -> None:
    body = make_upstream_body("not json at all {")
    outcome = validate_model_output(SCHEMA, body)
    assert outcome["valid"] is False
    assert any("not valid JSON" in e for e in outcome["errors"])


def test_missing_content_fails() -> None:
    body = {"id": "x", "choices": [{"message": {"role": "assistant"}}]}
    outcome = validate_model_output(SCHEMA, body)
    assert outcome["valid"] is False
    assert any("missing or not a string" in e for e in outcome["errors"])


def test_no_choices_fails() -> None:
    outcome = validate_model_output(SCHEMA, {"id": "x", "choices": []})
    assert outcome["valid"] is False
    assert outcome["errors"] == ["ERR_NO_CHOICES"]


def test_n_greater_than_1_every_choice_must_validate() -> None:
    """M4 edge case: choice[0] valid, choice[1] invalid -> whole response fails."""
    body = make_multi_choice_body(['{"answer": "a"}', '{"wrong_field": "b"}'])
    outcome = validate_model_output(SCHEMA, body)
    assert outcome["valid"] is False
    assert any("choice[1]" in e for e in outcome["errors"])


def test_n_greater_than_1_all_valid_passes() -> None:
    body = make_multi_choice_body(['{"answer": "a"}', '{"answer": "b"}'])
    outcome = validate_model_output(SCHEMA, body)
    assert outcome["valid"] is True
    assert outcome["errors"] == []


def test_errors_bounded_to_max() -> None:
    # 25 mismatching choices -> capped at 20 (_MAX_ERRORS)
    body = make_multi_choice_body(['{"wrong_field": "x"}'] * 25)
    outcome = validate_model_output(SCHEMA, body)
    assert outcome["valid"] is False
    assert len(outcome["errors"]) == 20


# ---------------------------------------------------------------------------
# truncate_raw_output (M12)
# ---------------------------------------------------------------------------


def test_truncate_raw_output_short_content_passthrough() -> None:
    body = make_upstream_body('{"answer": "yes"}')
    assert truncate_raw_output(body) == '{"answer": "yes"}'


def test_truncate_raw_output_caps_long_content() -> None:
    long_content = "x" * 40_000
    body = make_upstream_body(long_content)
    out = truncate_raw_output(body)
    assert out.endswith("...[truncated]")
    assert len(out) < len(long_content)


def test_truncate_raw_output_never_raises_on_malformed_body() -> None:
    out = truncate_raw_output({"choices": [{"message": {}}]})
    assert isinstance(out, str)
