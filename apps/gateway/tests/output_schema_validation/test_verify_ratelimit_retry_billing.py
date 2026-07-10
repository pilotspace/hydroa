"""THROWAWAY verify-team repro — DELETE after triage.

Adversarial attack on M8 (billing honesty): the frozen §3 schema note says the
retry's usage_source gains "validation_retry" for "attempt 1 of a mismatching
pair, and the terminal attempt of a fully-failed pair". When the retry leg's
OWN upstream call raises UpstreamRateLimitedError (a genuine terminal failure
of the bounded-retry loop, distinct from a schema mismatch), is that call
still correctly tagged validation_retry, or does it silently default to
"frame" because `_run_output_validation_retry`'s UpstreamRateLimitedError
except-branch (use_cases.py:512-521) calls `_fire_record` (no usage_source
param) instead of `_fire_record_with_raw` (which has one)?
"""

from __future__ import annotations

import asyncio

import pytest

from gateway.core.errors import ProblemError
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.errors import UpstreamRateLimitedError

from .conftest import (
    MISMATCHED_CONTENT,
    FakeAuthenticator,
    FakeModelChecker,
    FakeUsageRecorder,
    SequencedFakeUpstream,
    make_body,
    make_upstream_body,
)


async def test_upstream_rate_limited_during_retry_leg_billed_with_validation_retry_tag() -> None:
    """The retry leg's own 429 should still be tagged usage_source=validation_retry.

    Per the schema note in TASK.md §3: "validation_retry" (attempt 1 of a
    mismatching pair, and the TERMINAL attempt of a fully-failed pair)" — a
    retry leg that terminates in UpstreamRateLimitedError IS the terminal
    attempt of a fully-failed pair (attempt 1 mismatched; attempt 2 never even
    produced a body to validate). It must not silently default to "frame".
    """
    up = SequencedFakeUpstream(
        [
            (200, make_upstream_body(MISMATCHED_CONTENT)),
            UpstreamRateLimitedError("429 from upstream", retry_after=12.0),
        ]
    )
    rec = FakeUsageRecorder()
    uc = CompletionUseCase(
        FakeAuthenticator(),
        FakeModelChecker(),  # type: ignore[arg-type]
        response_cache=None,
        guardrail_evaluator=None,
        output_validation_enabled=True,
    )

    with pytest.raises(ProblemError) as exc_info:
        await uc.complete(
            raw_key="sk-test",
            body=make_body(validate_output=True),
            upstream=up,  # type: ignore[arg-type]
            usage_recorder=rec,  # type: ignore[arg-type]
        )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert exc_info.value.code == "ERR_UPSTREAM_RATE_LIMITED"
    assert up.call_count == 2
    # M8: both real, paid legs (attempt 1's mismatch + attempt 2's rate-limited
    # terminal failure) must be billed and BOTH tagged validation_retry.
    assert rec.call_count == 2, f"expected 2 usage rows, got {rec.call_count}: {rec.calls}"
    assert rec.calls[0]["usage_source"] == "validation_retry"
    assert rec.calls[1].get("usage_source") == "validation_retry", (
        f"attempt 2's rate-limited terminal failure was NOT tagged validation_retry "
        f"(got {rec.calls[1].get('usage_source', '<ABSENT — defaults to frame>')!r}) — "
        f"use_cases.py's _run_output_validation_retry UpstreamRateLimitedError branch "
        f"calls _fire_record (no usage_source param) instead of _fire_record_with_raw"
    )
