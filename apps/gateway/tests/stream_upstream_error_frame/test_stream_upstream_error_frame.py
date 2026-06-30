"""RED suite — terminal SSE error frame + [DONE] on mid-stream upstream failure (TASK.md §2 / §4).

6 tests, one per §2 scenario.  ALL of the new-behavior tests MUST run RED until BUILD adds:
  - In _wrapped()'s mid-stream `except (UpstreamUnavailableError, CircuitOpenError) as exc:`
    (use_cases.py ~L1608): after the 502 record, yield ONE OpenAI error chunk then a guarded
    `data: [DONE]\\n\\n`, then return.
  - code == ERR_UPSTREAM_RATE_LIMITED for UpstreamRateLimitedError instances;
    else ERR_UPSTREAM_UNAVAILABLE.

The regression-guard tests (disconnect / success) PASS now — they assert UNCHANGED behavior.
Construction mirrors tests/streaming_resilience/test_stream_resilience_use_case.py:
  CompletionUseCase.stream() → async-drain the returned generator → assert raw bytes.

asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

# ---------------------------------------------------------------------------
# Shared helpers from the streaming_resilience test harness (confirmed working)
# ---------------------------------------------------------------------------
from tests.streaming_resilience.conftest import (
    ALIAS,
    CAND_A,
    CAND_B,
    FakeAuthenticator,
    FakeModelChecker,
    FakeUsageRecorder,
    PlanStreamUpstream,
    make_payload,
)

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.errors import (
    CircuitOpenError,
    UpstreamUnavailableError,
)

# ---------------------------------------------------------------------------
# SSE chunk constants for this suite
# ---------------------------------------------------------------------------

# A plain content chunk — no [DONE], no error
_CHUNK_A = b'data: {"id":"A","choices":[{"delta":{"content":"hello"}}]}\n\n'
_CHUNK_B = b'data: {"id":"A","choices":[{"delta":{"content":" world"}}]}\n\n'

# A terminal [DONE] emitted by the upstream (clean-success or "upstream already done" case)
_UPSTREAM_DONE = b"data: [DONE]\n\n"

# Expected error code strings from the catalog (defined in error_catalog.py)
_CODE_UNAVAILABLE = "ERR_UPSTREAM_UNAVAILABLE"
_CODE_RATE_LIMITED = "ERR_UPSTREAM_RATE_LIMITED"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_uc() -> CompletionUseCase:
    """Construct a use-case with fake auth/model-check and resilience ON."""
    return CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        stream_resilience_enabled=True,
    )


def _make_router(upstream: PlanStreamUpstream) -> FallbackModelRouter:
    """Wrap upstream in a router with a single-alias group (resilience ON)."""
    return FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={ALIAS: [CAND_A, CAND_B]},
        stream_resilience_enabled=True,
    )


async def _drain(
    upstream: PlanStreamUpstream,
    recorder: FakeUsageRecorder,
) -> list[bytes]:
    """Run a stream end-to-end and collect every yielded chunk.

    Returns list of bytes chunks from the StreamingResponse generator.
    """
    uc = _make_uc()
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=_make_router(upstream),
    )
    chunks: list[bytes] = []
    async for chunk in gen:
        chunks.append(chunk)
    # Let fire-and-forget usage records complete.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return chunks


def _parse_error_frame(raw: bytes) -> dict[str, Any] | None:
    """If ``raw`` is a ``data: {...}`` SSE line whose JSON has an ``error`` key, return
    the parsed payload; otherwise return None."""
    if not raw.startswith(b"data: "):
        return None
    payload_bytes = raw[len(b"data: ") :].strip()
    try:
        obj = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return None
    if "error" in obj:
        return obj  # type: ignore[return-value]
    return None


def _error_code(chunks: list[bytes]) -> str | None:
    """Return the error.code from the FIRST error frame in chunks, or None."""
    for chunk in chunks:
        parsed = _parse_error_frame(chunk)
        if parsed is not None:
            return parsed.get("error", {}).get("code")
    return None


def _count_done(chunks: list[bytes]) -> int:
    """Count how many chunks equal ``data: [DONE]\\n\\n``."""
    return sum(1 for c in chunks if c == _UPSTREAM_DONE)


def _has_error_frame(chunks: list[bytes]) -> bool:
    return any(_parse_error_frame(c) is not None for c in chunks)


# ===========================================================================
# Scenario 1 — mid-stream UpstreamUnavailableError → error frame + [DONE]
# "Given a stream that yields 2 chunks then raises UpstreamUnavailableError"
# ===========================================================================


async def test_midstream_unavailable_emits_error_frame_and_done() -> None:
    """SEF-1: stream yields 2 chunks then raises UpstreamUnavailableError.

    The client must receive BOTH prefix chunks, then exactly ONE error frame
    whose code is ERR_UPSTREAM_UNAVAILABLE, then ``data: [DONE]``.
    A single usage row with status=502 must be fired.

    RED until: _wrapped() mid-stream catch yields the error chunk + [DONE].
    """
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream(
        {CAND_A: [_CHUNK_A, _CHUNK_B, UpstreamUnavailableError("upstream 5xx mid-stream")]}
    )

    chunks = await _drain(upstream, recorder)

    # The two prefix chunks must arrive before the error frame.
    assert _CHUNK_A in chunks, f"Expected prefix chunk A in stream; got {chunks!r}"
    assert _CHUNK_B in chunks, f"Expected prefix chunk B in stream; got {chunks!r}"

    # An error frame with the right code must be present.
    code = _error_code(chunks)
    assert code == _CODE_UNAVAILABLE, (
        f"Expected error frame code={_CODE_UNAVAILABLE!r}; got code={code!r}. "
        f"Full stream: {chunks!r}"
    )

    # A terminal [DONE] must follow the error frame.
    assert _count_done(chunks) >= 1, (
        f"Expected at least one data: [DONE] after error frame; got none. Full stream: {chunks!r}"
    )

    # The error frame must precede [DONE] in the stream.
    error_idx = next(i for i, c in enumerate(chunks) if _parse_error_frame(c) is not None)
    done_idx = next(i for i, c in enumerate(chunks) if c == _UPSTREAM_DONE)
    assert error_idx < done_idx, (
        f"Error frame (idx={error_idx}) must precede [DONE] (idx={done_idx})"
    )

    # 502 usage record must fire (unchanged behavior).
    status_codes = [r.get("status") for r in recorder.calls]
    assert 502 in status_codes, (
        f"Expected a usage record with status=502; got statuses={status_codes!r}"
    )


# ===========================================================================
# Scenario 2 — mid-stream UpstreamRateLimitedError → ERR_UPSTREAM_RATE_LIMITED code
# "Given a stream that yields 1 chunk then raises UpstreamRateLimitedError"
# ===========================================================================


async def test_midstream_ratelimit_frame_code() -> None:
    """SEF-2: UpstreamRateLimitedError mid-stream → error frame code ERR_UPSTREAM_RATE_LIMITED.

    The error frame must appear after the prefix chunk; a terminal [DONE] must follow.

    RED until: isinstance check in _wrapped() emits the rate-limit code.
    """
    from gateway.proxy.domain.errors import UpstreamRateLimitedError  # noqa: PLC0415

    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream(
        {CAND_A: [_CHUNK_A, UpstreamRateLimitedError("upstream 429 mid-stream", retry_after=5.0)]}
    )

    chunks = await _drain(upstream, recorder)

    # Prefix chunk must arrive.
    assert _CHUNK_A in chunks, f"Expected prefix chunk A; got {chunks!r}"

    # Error frame must carry the rate-limit code.
    code = _error_code(chunks)
    assert code == _CODE_RATE_LIMITED, (
        f"Expected error frame code={_CODE_RATE_LIMITED!r} for UpstreamRateLimitedError; "
        f"got code={code!r}. Full stream: {chunks!r}"
    )

    # Terminal [DONE] must be present after the error frame.
    assert _count_done(chunks) >= 1, (
        f"Expected data: [DONE] after rate-limit error frame; got none. Full stream: {chunks!r}"
    )

    error_idx = next(i for i, c in enumerate(chunks) if _parse_error_frame(c) is not None)
    done_idx = next(i for i, c in enumerate(chunks) if c == _UPSTREAM_DONE)
    assert error_idx < done_idx, (
        f"Error frame (idx={error_idx}) must precede [DONE] (idx={done_idx})"
    )


# ===========================================================================
# Scenario 3 — CircuitOpenError mid-stream → ERR_UPSTREAM_UNAVAILABLE frame
# "Given a stream that raises CircuitOpenError mid-flight"
# ===========================================================================


async def test_midstream_circuit_open_frame() -> None:
    """SEF-3: CircuitOpenError raised mid-stream → error frame code ERR_UPSTREAM_UNAVAILABLE.

    CircuitOpenError is caught by the same handler as UpstreamUnavailableError
    (L1608 of use_cases.py), so it must carry the generic unavailable code.

    RED until: _wrapped() yields the error frame for CircuitOpenError.
    """
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream(
        {CAND_A: [_CHUNK_A, CircuitOpenError("circuit tripped mid-stream")]}
    )

    chunks = await _drain(upstream, recorder)

    # Prefix chunk must arrive.
    assert _CHUNK_A in chunks, f"Expected prefix chunk A; got {chunks!r}"

    # Error frame with unavailable code.
    code = _error_code(chunks)
    assert code == _CODE_UNAVAILABLE, (
        f"Expected code={_CODE_UNAVAILABLE!r} for CircuitOpenError; "
        f"got code={code!r}. Full stream: {chunks!r}"
    )

    # Terminal [DONE] must be present.
    assert _count_done(chunks) >= 1, (
        f"Expected data: [DONE] after CircuitOpenError frame; got none. Full stream: {chunks!r}"
    )


# ===========================================================================
# Scenario 4 — client disconnect (GeneratorExit) → NO error frame emitted
# "Given a stream that raises GeneratorExit mid-flight"
# (REGRESSION GUARD — must PASS now; disconnect path is UNCHANGED)
# ===========================================================================


async def test_client_disconnect_emits_no_frame() -> None:
    """SEF-4 (regression guard): GeneratorExit (client disconnect) → no error frame.

    Drive the disconnect by calling gen.aclose() after the first chunk, mirroring
    tests/disconnect_provider_cost/test_disconnect_provider_cost.py.

    The v34 disconnect usage-record path still fires (usage_source=client_disconnect).
    This test PASSES now (unchanged behavior); it guards against regressions where the
    new error-frame emission accidentally fires on disconnect.
    """
    upstream = PlanStreamUpstream(
        # Plan: yield a chunk then more chunks — client disconnects before draining.
        {CAND_A: [_CHUNK_A, _CHUNK_B]}
    )

    # Use a _SpyRecorder that captures all fields (mirrors disconnect_provider_cost suite).
    class _SpyRecorder:
        supported_extras: frozenset[str] = frozenset(
            {
                "team_id",
                "pii_masked",
                "usage_source",
                "provider_generation_id",
                "disconnect_estimate",
            }
        )

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def record(self, **kwargs: Any) -> None:
            self.calls.append(dict(kwargs))

    spy = _SpyRecorder()
    uc = _make_uc()
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=spy,  # type: ignore[arg-type]
        model_router=_make_router(upstream),
    )

    # Consume the first chunk (commits the stream), then close (simulates client disconnect).
    received: list[bytes] = []
    received.append(await gen.__anext__())
    await gen.aclose()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # No error frame must appear in the received chunks.
    assert not _has_error_frame(received), (
        f"disconnect path must NOT emit an error frame; got {received!r}"
    )

    # No injected [DONE] in the received chunks (the upstream hadn't sent one yet).
    assert _count_done(received) == 0, f"disconnect path must NOT inject [DONE]; got {received!r}"

    # The v34 disconnect usage record must still fire.
    sources = [c.get("usage_source") for c in spy.calls]
    assert "client_disconnect" in sources, (
        f"disconnect usage-record path must fire with usage_source=client_disconnect; "
        f"got sources={sources!r}"
    )


# ===========================================================================
# Scenario 5 — clean success → byte-identical (exactly one [DONE], no error frame)
# "Given an upstream stream that completes with its own data: [DONE]"
# (REGRESSION GUARD — must PASS now; success path is UNCHANGED)
# ===========================================================================


async def test_success_stream_byte_identical() -> None:
    """SEF-5 (regression guard): clean upstream success → no error frame injected.

    Exactly one [DONE] must appear (the upstream's own) and no error chunk.
    This guards against the build accidentally injecting an error frame on success.
    """
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: [_CHUNK_A, _CHUNK_B, _UPSTREAM_DONE]})

    chunks = await _drain(upstream, recorder)

    # No error frame must appear.
    assert not _has_error_frame(chunks), (
        f"success path must not emit any error frame; got {chunks!r}"
    )

    # Exactly one [DONE] (the upstream's).
    done_count = _count_done(chunks)
    assert done_count == 1, (
        f"success path must have exactly 1 [DONE]; got {done_count}. Full stream: {chunks!r}"
    )

    # The two content chunks must be present.
    assert _CHUNK_A in chunks, f"Expected prefix chunk A in success stream; got {chunks!r}"
    assert _CHUNK_B in chunks, f"Expected prefix chunk B in success stream; got {chunks!r}"


# ===========================================================================
# Scenario 6 — upstream already emitted [DONE] before raising → NO second [DONE]
# "Given a stream that yields data: [DONE] then raises UpstreamUnavailableError"
# ===========================================================================


async def test_no_double_done_when_upstream_already_done() -> None:
    """SEF-6: upstream emits [DONE] then raises UpstreamUnavailableError → error chunk present,
    but NO second [DONE] appended.

    The guard condition: ``b"[DONE]" not in (collected[-1] if collected else b"")``.
    The error chunk is still emitted so the failure is visible; the terminal sentinel is skipped.

    RED until: _wrapped() guards the second [DONE] injection.
    """
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream(
        {
            CAND_A: [
                _CHUNK_A,
                _UPSTREAM_DONE,  # upstream sends its own [DONE] …
                UpstreamUnavailableError("raised after [DONE]"),  # … then raises
            ]
        }
    )

    chunks = await _drain(upstream, recorder)

    # Error frame MUST be present (failure is always visible).
    assert _has_error_frame(chunks), (
        f"Error frame must appear even when upstream already sent [DONE]; got {chunks!r}"
    )

    code = _error_code(chunks)
    assert code == _CODE_UNAVAILABLE, f"Expected code={_CODE_UNAVAILABLE!r}; got code={code!r}"

    # At most ONE [DONE] total — no double terminal.
    done_count = _count_done(chunks)
    assert done_count <= 1, (
        f"Must NOT emit a second [DONE] when upstream already sent one; "
        f"got {done_count} [DONE] frames. Full stream: {chunks!r}"
    )
