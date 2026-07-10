"""RED-first integration suite — output-schema-validation (TASK.md §2 scenarios).

Exercises CompletionUseCase.complete() end-to-end with fakes only (no DB/Redis/live
server) — mirrors tests/vector_cache/test_use_case_wiring.py conventions. One test
per §2 scenario; each maps back to the Must/Reject it covers in its docstring.

MUST run red (missing implementation: no `output_validation_enabled` ctor kwarg, no
`validate_output` handling) before BUILD.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.core.errors import ProblemError
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.infrastructure.response_cache import build_cache_key

from .conftest import (
    MALFORMED_SCHEMA,
    MISMATCHED_CONTENT,
    UNPARSEABLE_CONTENT,
    VALID_CONTENT,
    FakeAuthenticator,
    FakeGuardrailEvaluator,
    FakeModelChecker,
    FakeResponseCache,
    FakeUsageRecorder,
    SequencedFakeUpstream,
    make_body,
    make_multi_choice_body,
    make_upstream_body,
    response_format_json_schema,
)


def _make_use_case(
    *,
    output_validation_enabled: bool = True,
    response_cache: Any = None,
    guardrail_evaluator: Any = None,
    authenticator: Any = None,
) -> CompletionUseCase:
    try:
        return CompletionUseCase(
            authenticator or FakeAuthenticator(cache_enabled=response_cache is not None),
            FakeModelChecker(),  # type: ignore[arg-type]
            response_cache=response_cache,
            guardrail_evaluator=guardrail_evaluator,
            output_validation_enabled=output_validation_enabled,
        )
    except TypeError:
        pytest.fail(
            "RED: CompletionUseCase has no output_validation_enabled kwarg — build pending"
        )


async def _settle() -> None:
    """Let fire-and-forget usage-record / cache-set tasks run before asserting."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _complete(
    uc: CompletionUseCase,
    up: SequencedFakeUpstream,
    rec: FakeUsageRecorder,
    body: dict[str, Any],
) -> Any:
    result = await uc.complete(
        raw_key="sk-test",
        body=body,
        upstream=up,  # type: ignore[arg-type]
        usage_recorder=rec,  # type: ignore[arg-type]
    )
    await _settle()
    return result


# ===========================================================================
# Scenario: opt-in off by default — byte-identical path (M1)
# ===========================================================================


async def test_opt_in_off_by_default_byte_identical() -> None:
    """Operator flag False: validate_output stripped, single call, single usage row.

    Feeds SCHEMA-MISMATCHING content on purpose — if validation ran at all, this
    would trigger a retry/422. Proves zero validation logic executes when off.
    """
    up = SequencedFakeUpstream([(200, make_upstream_body(MISMATCHED_CONTENT))])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=False)

    status, body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=True))

    assert status == 200
    assert body["choices"][0]["message"]["content"] == MISMATCHED_CONTENT
    assert up.call_count == 1
    assert "validate_output" not in up.calls[0]
    assert rec.call_count == 1
    assert "usage_source" not in rec.calls[0]


# ===========================================================================
# Scenario: operator flag on, caller does not opt in — byte-identical path (M1)
# ===========================================================================


async def test_operator_on_caller_not_opted_in_byte_identical() -> None:
    up = SequencedFakeUpstream([(200, make_upstream_body(MISMATCHED_CONTENT))])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    status, body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=None))

    assert status == 200
    assert body["choices"][0]["message"]["content"] == MISMATCHED_CONTENT
    assert up.call_count == 1
    assert rec.call_count == 1
    assert "usage_source" not in rec.calls[0]


# ===========================================================================
# Scenario: gateway-only field never reaches upstream (M2)
# ===========================================================================


async def test_validate_output_never_forwarded_upstream_across_retry() -> None:
    up = SequencedFakeUpstream(
        [(200, make_upstream_body(MISMATCHED_CONTENT)), (200, make_upstream_body(VALID_CONTENT))]
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    status, _body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=True))

    assert status == 200
    assert up.call_count == 2
    assert "validate_output" not in up.calls[0]
    assert "validate_output" not in up.calls[1]


# ===========================================================================
# Scenario: malformed schema rejected pre-flight, zero upstream calls (M3, Reject)
# ===========================================================================


async def test_malformed_schema_rejected_preflight() -> None:
    up = SequencedFakeUpstream([])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    with pytest.raises(ProblemError) as exc_info:
        await _complete(
            uc,
            up,
            rec,
            make_body(response_format=response_format_json_schema(MALFORMED_SCHEMA)),
        )

    err = exc_info.value
    assert err.status == 400
    assert err.code == "ERR_INVALID_JSON_SCHEMA"
    assert up.call_count == 0
    assert rec.call_count == 0


# ===========================================================================
# Scenario: first attempt already valid — no retry, single bill (M4, M5, After)
# ===========================================================================


async def test_first_attempt_valid_no_retry_single_bill() -> None:
    up = SequencedFakeUpstream([(200, make_upstream_body(VALID_CONTENT))])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    status, body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=True))

    assert status == 200
    assert body["choices"][0]["message"]["content"] == VALID_CONTENT
    assert up.call_count == 1
    assert rec.call_count == 1
    assert "usage_source" not in rec.calls[0]


# ===========================================================================
# Scenario: unparseable JSON content counts as a validation failure (M4)
# ===========================================================================


async def test_unparseable_content_triggers_retry() -> None:
    up = SequencedFakeUpstream(
        [(200, make_upstream_body(UNPARSEABLE_CONTENT)), (200, make_upstream_body(VALID_CONTENT))]
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    status, body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=True))

    assert status == 200
    assert up.call_count == 2
    assert body["choices"][0]["message"]["content"] == VALID_CONTENT


# ===========================================================================
# Scenario: schema mismatch triggers exactly one retry that then succeeds
# (M5, M6, M7, M8, After)
# ===========================================================================


async def test_mismatch_then_retry_succeeds_bills_two_rows() -> None:
    up = SequencedFakeUpstream(
        [(200, make_upstream_body(MISMATCHED_CONTENT)), (200, make_upstream_body(VALID_CONTENT))]
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    status, body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=True))

    assert status == 200
    assert body["choices"][0]["message"]["content"] == VALID_CONTENT
    assert up.call_count == 2
    # M8: TWO usage records — attempt 1 tagged validation_retry, attempt 2 default "frame".
    assert rec.call_count == 2
    assert rec.calls[0]["usage_source"] == "validation_retry"
    assert "usage_source" not in rec.calls[1]


# ===========================================================================
# Scenario: both attempts fail validation — terminal structured error
# (Reject, M8, M12, M13)
# ===========================================================================


async def test_both_attempts_fail_terminal_422() -> None:
    up = SequencedFakeUpstream(
        [
            (200, make_upstream_body(MISMATCHED_CONTENT)),
            (200, make_upstream_body(UNPARSEABLE_CONTENT)),
        ]
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="sk-test",
            body=make_body(validate_output=True),
            upstream=up,  # type: ignore[arg-type]
            usage_recorder=rec,  # type: ignore[arg-type]
        )
    await _settle()

    err = exc_info.value
    assert err.status == 422
    assert err.code == "ERR_OUTPUT_SCHEMA_VALIDATION_FAILED"
    assert err.extra is not None
    assert err.extra["raw_output"] == UNPARSEABLE_CONTENT
    assert err.extra["validation_errors"]
    assert up.call_count == 2
    # M8: both real, paid calls are billed — two rows, both validation_retry.
    assert rec.call_count == 2
    assert rec.calls[0]["usage_source"] == "validation_retry"
    assert rec.calls[1]["usage_source"] == "validation_retry"


# ===========================================================================
# Scenario: circuit open during the retry — fails like any other blocked call (M7)
# ===========================================================================


async def test_circuit_open_during_retry_fails_like_any_blocked_call() -> None:
    up = SequencedFakeUpstream(
        [(200, make_upstream_body(MISMATCHED_CONTENT)), CircuitOpenError("open")]
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="sk-test",
            body=make_body(validate_output=True),
            upstream=up,  # type: ignore[arg-type]
            usage_recorder=rec,  # type: ignore[arg-type]
        )
    await _settle()

    err = exc_info.value
    assert err.status == 502
    assert err.code == "ERR_UPSTREAM_UNAVAILABLE"
    assert up.call_count == 2
    # attempt 1's usage (validation_retry) is still recorded; no second row (never billed).
    assert rec.call_count == 1
    assert rec.calls[0]["usage_source"] == "validation_retry"


async def test_upstream_unavailable_during_retry_same_502_path() -> None:
    up = SequencedFakeUpstream(
        [(200, make_upstream_body(MISMATCHED_CONTENT)), UpstreamUnavailableError("down")]
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    with pytest.raises(ProblemError) as exc_info:
        await _complete(uc, up, rec, make_body(validate_output=True))

    assert exc_info.value.status == 502
    assert exc_info.value.code == "ERR_UPSTREAM_UNAVAILABLE"


# ===========================================================================
# Scenario: streaming + opt-in is rejected, never silently ignored (M11, Reject)
# ===========================================================================


async def test_streaming_plus_opt_in_rejected() -> None:
    up = SequencedFakeUpstream([])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    with pytest.raises(ProblemError) as exc_info:
        await _complete(uc, up, rec, make_body(validate_output=True, stream=True))

    err = exc_info.value
    assert err.status == 400
    assert err.code == "ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM"
    assert up.call_count == 0
    assert rec.call_count == 0


async def test_plain_stream_request_unaffected() -> None:
    """A plain stream:true request (validate_output absent) must not be touched by
    the M11 pre-flight check at all — the shared pre-flight gate (_validate_payload,
    used by both complete() and stream()) returns normally, no ProblemError."""
    uc = _make_use_case(output_validation_enabled=True)
    model_id, _messages, engaged = await uc._validate_payload(
        make_body(validate_output=None, stream=True)
    )
    assert model_id == "gpt-test"
    assert engaged is False


# ===========================================================================
# Scenario: opted-in request without a json_schema response_format is rejected
# (Reject ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA)
# ===========================================================================


@pytest.mark.parametrize(
    "response_format",
    [
        {},  # absent entirely
        {"type": "text"},
        {"type": "json_object"},
    ],
)
async def test_requires_json_schema_rejected(response_format: dict[str, Any]) -> None:
    up = SequencedFakeUpstream([])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    with pytest.raises(ProblemError) as exc_info:
        await _complete(
            uc, up, rec, make_body(validate_output=True, response_format=response_format)
        )

    err = exc_info.value
    assert err.status == 400
    assert err.code == "ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA"
    assert up.call_count == 0


async def test_same_response_format_without_opt_in_proceeds_unchanged() -> None:
    """The identical response_format (text) with validate_output absent must NOT
    be affected by the M1 gate at all — proceeds through the existing path."""
    up = SequencedFakeUpstream([(200, make_upstream_body(VALID_CONTENT))])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    status, _body, _x_cache = await _complete(
        uc, up, rec, make_body(validate_output=None, response_format={"type": "text"})
    )

    assert status == 200
    assert up.call_count == 1


# ===========================================================================
# Scenario: opted-in request bypasses the response cache on both read and write
# (M9)
# ===========================================================================


async def test_opted_in_bypasses_cache_read_and_write() -> None:
    from .conftest import TENANT_A

    body = make_body(validate_output=True)
    cache_key = build_cache_key(str(TENANT_A), body)
    stale_cached_body = make_upstream_body("STALE-CACHED-CONTENT")
    cache = FakeResponseCache(exact={cache_key: stale_cached_body})
    up = SequencedFakeUpstream([(200, make_upstream_body(VALID_CONTENT))])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True, response_cache=cache)

    status, resp_body, x_cache = await _complete(uc, up, rec, body)

    assert status == 200
    # NOT served from the stale cache entry — upstream was actually called.
    assert up.call_count == 1
    assert resp_body["choices"][0]["message"]["content"] == VALID_CONTENT
    assert x_cache is None  # cache tier skipped entirely (M9), not merely "missed"
    # Write bypass: the pre-existing entry at this key must be untouched (no
    # overwrite either) — cache.set was never called for the validating response.
    assert cache.store[cache_key] == stale_cached_body


async def test_non_opted_in_request_cache_unaffected() -> None:
    """A later plain (non-opted-in) request with the SAME body still reads/writes
    the cache exactly as today — proves the bypass is scoped to opted-in requests."""
    from .conftest import TENANT_A

    body = make_body(validate_output=None, response_format={"type": "text"})
    cache_key = build_cache_key(str(TENANT_A), body)
    cache = FakeResponseCache()
    up = SequencedFakeUpstream([(200, make_upstream_body(VALID_CONTENT))])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True, response_cache=cache)

    status, _body, x_cache = await _complete(uc, up, rec, body)

    assert status == 200
    assert x_cache == "miss"
    assert cache_key in cache.store  # normal miss-store still happens


# ===========================================================================
# Scenario: validation runs before PII masking (M10)
# ===========================================================================


async def test_validation_runs_before_masking() -> None:
    up = SequencedFakeUpstream([(200, make_upstream_body(VALID_CONTENT))])
    rec = FakeUsageRecorder()
    guardrail = FakeGuardrailEvaluator()
    authenticator = FakeAuthenticator(guardrail_configs={"pii_mask": {"enabled": True}})
    uc = _make_use_case(
        output_validation_enabled=True, guardrail_evaluator=guardrail, authenticator=authenticator
    )

    status, body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=True))

    assert status == 200
    # Validated against the RAW (unmasked) content -> no retry fired.
    assert up.call_count == 1
    # Masking applied AFTER validation -> caller sees the masked body.
    assert body["choices"][0]["message"]["content"] == "MASKED:" + VALID_CONTENT
    assert guardrail.evaluate_post_calls == 1


# ===========================================================================
# Scenario: n>1 requires every choice to validate (M4 edge case)
# ===========================================================================


async def test_n_greater_than_1_partial_mismatch_triggers_retry_then_succeeds() -> None:
    attempt1 = make_multi_choice_body(['{"answer": "a"}', '{"wrong_field": "b"}'])
    attempt2 = make_multi_choice_body(['{"answer": "a2"}', '{"answer": "b2"}'])
    up = SequencedFakeUpstream([(200, attempt1), (200, attempt2)])
    rec = FakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    status, body, _x_cache = await _complete(uc, up, rec, make_body(validate_output=True))

    assert status == 200
    assert up.call_count == 2
    assert body == attempt2
    assert len(body["choices"]) == 2
