"""open_resilient_stream — the pre-first-byte streaming fallover primitive (v19 task 3).

Try each attempt in order, awaiting its FIRST chunk — the pre-first-byte boundary, the
ONLY point at which a fallover is safe because nothing has reached the client yet. A
pre-first-byte transport failure (UpstreamUnavailableError / CircuitOpenError) falls over
to the next attempt. The first chunk obtained COMMITS the stream: the caller yields it and
drains the rest; any later (mid-stream) error surfaces to the caller's drain loop and is
NEVER replayed. This module owns no billing and no metrics (metrics only via on_fallover).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError


async def _empty_aiter() -> AsyncIterator[bytes]:
    """An async iterator that yields nothing (for a committed-empty upstream stream)."""
    for chunk in ():  # never iterates — an empty async generator
        yield chunk


async def open_resilient_stream(
    *,
    attempts: list[str],
    open_stream: Callable[[str], AsyncIterator[bytes]],
    on_fallover: Callable[[str, str], None] | None = None,
    on_committed: Callable[[str], None] | None = None,
) -> tuple[bytes | None, AsyncIterator[bytes]]:
    """Acquire the first SSE chunk with pre-first-byte fallover.

    Args:
      attempts:    ordered model ids to try (≥1). For an alias this is the strategy order;
                   for a plain model id it is a single-element list (no same-target retry).
      open_stream: model_id -> a FRESH upstream stream (AsyncIterator[bytes]).
      on_fallover: optional (from_model, to_model) callback fired on each pre-first-byte skip.
      on_committed: optional (model_id) callback fired ONCE with the candidate that COMMITS
                   the stream (its first chunk obtained, or a clean committed-empty) — for
                   billing attribution so usage keys on the served candidate, not the alias
                   (stream-alias-billing B1).

    Returns (first_chunk, rest): the caller yields first_chunk (when not None) then drains
    rest. first_chunk is None only for a committed-empty upstream (zero chunks, clean end).

    Raises UpstreamUnavailableError if EVERY attempt fails pre-first-byte (the last attempt's
    error propagates). Mid-stream errors after commit are NOT caught here — they live in the
    returned `rest` iterator and surface to the caller's drain loop (no replay).
    """
    last = len(attempts) - 1
    for i, model_id in enumerate(attempts):
        try:
            # open_stream() is INSIDE the try: the circuit breaker (BoundCircuitBreakerUpstream
            # + each provider's stream()) raises CircuitOpenError SYNCHRONOUSLY at the stream()
            # call, not on __anext__. Both the sync open and the async first chunk are
            # pre-first-byte, so both must trigger fallover.
            ait = open_stream(model_id).__aiter__()
            first = await ait.__anext__()
        except (UpstreamUnavailableError, CircuitOpenError):
            if i < last:
                if on_fallover is not None:
                    on_fallover(model_id, attempts[i + 1])
                continue
            raise  # last attempt failed pre-first-byte → propagate to the caller (→ 502)
        except StopAsyncIteration:
            # Committed-empty: this candidate served (zero chunks, clean end) — it is the
            # committed candidate for billing attribution (stream-alias-billing B1).
            if on_committed is not None:
                on_committed(model_id)
            return None, _empty_aiter()  # upstream yielded nothing — committed-empty
        else:
            if on_committed is not None:
                on_committed(model_id)  # the candidate whose first chunk COMMITTED the stream
            return first, ait  # COMMITTED — caller yields `first` then drains `ait`
    # Defensive: attempts must be non-empty; an empty list reaches here.
    raise UpstreamUnavailableError("open_resilient_stream: no attempts provided")
