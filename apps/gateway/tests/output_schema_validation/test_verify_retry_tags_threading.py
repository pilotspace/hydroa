"""Pin test (verify heal, cost-attribution-tags 🟡 finding #1): the output-validation
retry path must carry the request's X-Gateway-Tags onto EVERY row it bills — the
attempt-1 "validation_retry" row and the caller's final row — instead of dropping
them to {} as the pre-heal _run_output_validation_retry fire sites did.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .conftest import (
    MISMATCHED_CONTENT,
    VALID_CONTENT,
    FakeUsageRecorder,
    SequencedFakeUpstream,
    make_body,
    make_upstream_body,
)
from .test_output_schema_validation import _make_use_case

_TAGS_HEADER = '{"project": "alpha", "env": "prod"}'
_TAGS = {"project": "alpha", "env": "prod"}


class TagsFakeUsageRecorder(FakeUsageRecorder):
    """The directory fake predates cost-attribution-tags; declare the extra here."""

    supported_extras = FakeUsageRecorder.supported_extras | {"tags"}


async def _settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_validation_retry_rows_carry_request_tags() -> None:
    up = SequencedFakeUpstream(
        [
            (200, make_upstream_body(MISMATCHED_CONTENT)),
            (200, make_upstream_body(VALID_CONTENT)),
        ]
    )
    rec = TagsFakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    result: Any = await uc.complete(
        raw_key="sk-test",
        body=make_body(validate_output=True),
        upstream=up,  # type: ignore[arg-type]
        usage_recorder=rec,  # type: ignore[arg-type]
        request_headers={"x-gateway-tags": _TAGS_HEADER},
    )
    await _settle()

    status = result[0]
    assert status == 200
    assert rec.call_count == 2
    # Attempt 1 (validation_retry row) and the final row BOTH carry the tags.
    assert rec.calls[0]["usage_source"] == "validation_retry"
    assert rec.calls[0]["tags"] == _TAGS
    assert rec.calls[1]["tags"] == _TAGS


async def test_terminal_double_failure_rows_carry_request_tags() -> None:
    up = SequencedFakeUpstream(
        [
            (200, make_upstream_body(MISMATCHED_CONTENT)),
            (200, make_upstream_body(MISMATCHED_CONTENT)),
        ]
    )
    rec = TagsFakeUsageRecorder()
    uc = _make_use_case(output_validation_enabled=True)

    raised = False
    try:
        await uc.complete(
            raw_key="sk-test",
            body=make_body(validate_output=True),
            upstream=up,  # type: ignore[arg-type]
            usage_recorder=rec,  # type: ignore[arg-type]
            request_headers={"x-gateway-tags": _TAGS_HEADER},
        )
    except Exception:
        raised = True
    await _settle()

    assert raised
    # Both paid attempts billed, both tagged (M8 pairing + tags threading).
    assert rec.call_count == 2
    assert all(call["tags"] == _TAGS for call in rec.calls)
