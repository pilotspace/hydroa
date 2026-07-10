"""SqlAlchemyPayloadCapture — the PayloadCapturePort adapter.

Owns the two admission gates that sit IN FRONT of the actual scrub/truncate/insert
work (logs/application/capture_writer.py):
  1. ZDR check (LIVE, not cached from opt-in-toggle time) — fail-CLOSED: any internal
     failure from the ZdrOverridePort is treated as "assume ZDR, suppress capture"
     (the ZdrOverridePort implementation itself is contracted to never raise and to
     map its own internal errors to True; this adapter adds a second fail-closed
     layer in case a misbehaving implementation raises anyway — defense in depth,
     never trust a single layer for a data-leak-risk decision).
  2. Bounded-concurrency admission via a non-blocking semaphore try-acquire (folded
     lesson, foundation-version 31: `asyncio.wait_for(sem.acquire(), timeout=0)` is
     NOT a reliable non-blocking acquire in 3.12 — use `if not sem.locked(): await
     sem.acquire()`). A new capture attempt is skipped non-blockingly (no row) when
     the pool is saturated — the practical equivalent of a circuit breaker's open-state
     shed for a fire-and-forget, non-retried write, without a stateful breaker class.

capture() itself NEVER raises (PayloadCapturePort's own contract) — every failure
path here and in persist_request_log ends in "no row" or "metadata-only row", never
an exception escaping to the caller's fire-and-forget task.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.logs.application.capture_writer import persist_request_log
from gateway.logs.domain.ports import ZdrOverridePort

_log = logging.getLogger(__name__)


def _verbatim_token_count(usage: dict[str, Any] | None, key: str) -> int | None:
    """Read one token-count field VERBATIM from a usage dict — never recomputed.

    request-log-metering-fields TASK.md §3 Must: prompt_tokens/completion_tokens/
    total_tokens are read verbatim from the SAME usage dict already passed to the
    call's billing record — never a second extraction, never re-tokenized. Returns
    None for a missing/malformed value (never 0, which would misreport "absent" as
    "confirmed zero") — mirrors the fail-safe posture of recorder.py's own `_safe_tier`.
    bool is an int subclass — excluded explicitly (a flag is not a token count).
    """
    if not isinstance(usage, dict):
        return None
    val = usage.get(key)
    if isinstance(val, bool) or not isinstance(val, int):
        return None
    return val


class SqlAlchemyPayloadCapture:
    """Postgres-backed PayloadCapturePort implementation."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        zdr_port: ZdrOverridePort,
        timeout_seconds: float = 3.0,
        max_field_bytes: int = 8192,
        max_body_bytes: int = 65536,
        max_concurrent_tasks: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._zdr_port = zdr_port
        self._timeout_seconds = timeout_seconds
        self._max_field_bytes = max_field_bytes
        self._max_body_bytes = max_body_bytes
        self._semaphore = asyncio.Semaphore(max_concurrent_tasks)

    async def capture(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        request_body: dict[str, Any],
        response_body: dict[str, Any] | None,
        status: int,
        stream: bool,
        cached: bool,
        guardrail_configs: dict[str, Any],
        usage: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Fire-and-forget capture — NEVER raises (see module docstring).

        request-log-metering-fields TASK.md §3: prompt_tokens/completion_tokens/
        total_tokens are read VERBATIM from `usage` here (the ONE place the usage dict
        is unpacked into individual columns) — never recomputed, never re-tokenized.
        `usage=None` (e.g. a guardrail-BLOCK hook site) yields all three NULL, never 0.
        """
        try:
            try:
                # Bounded — a ZdrOverridePort implementation that never returns (hangs)
                # is a distinct failure mode from one that raises: without this
                # asyncio.wait_for, capture() itself would hang indefinitely (never
                # cancelled, holding its request/response body references forever — an
                # unbounded task/memory leak). Reuses self._timeout_seconds, the same
                # bound already applied to the downstream scrub->truncate->INSERT
                # sequence in persist_request_log.
                is_zdr = await asyncio.wait_for(
                    self._zdr_port.is_zdr(tenant_id), timeout=self._timeout_seconds
                )
            except Exception as exc:
                # Defense-in-depth: the port itself must never raise or hang past its
                # bound, but a misbehaving implementation must still fail-CLOSED here
                # (assume ZDR, suppress write) — the worse of the two failure directions
                # is writing PII for a tenant that might be under Zero-Data-Retention.
                # Covers both a raised exception and a asyncio.wait_for TimeoutError.
                _log.warning(
                    "sqlalchemy_capture: zdr_port raised or timed out "
                    "(fail-CLOSED, suppressing capture)",
                    exc_info=exc,
                )
                is_zdr = True
            if is_zdr:
                return

            # Non-blocking concurrency admission — skip (no row) when saturated, never
            # queue/wait (folded lesson, foundation-version 31).
            if self._semaphore.locked():
                _log.warning(
                    "sqlalchemy_capture: concurrency pool saturated (skipped, no row)",
                    extra={"tenant_id": str(tenant_id)},
                )
                return
            await self._semaphore.acquire()
            try:
                await persist_request_log(
                    session_factory=self._session_factory,
                    tenant_id=tenant_id,
                    key_id=key_id,
                    model=model,
                    request_body=request_body,
                    response_body=response_body,
                    status=status,
                    stream=stream,
                    cached=cached,
                    guardrail_configs=guardrail_configs,
                    timeout_seconds=self._timeout_seconds,
                    max_field_bytes=self._max_field_bytes,
                    max_body_bytes=self._max_body_bytes,
                    request_id=request_id,
                    latency_ms=latency_ms,
                    prompt_tokens=_verbatim_token_count(usage, "prompt_tokens"),
                    completion_tokens=_verbatim_token_count(usage, "completion_tokens"),
                    total_tokens=_verbatim_token_count(usage, "total_tokens"),
                )
            finally:
                self._semaphore.release()
        except Exception as exc:
            _log.warning("sqlalchemy_capture: unexpected error (swallowed, no row)", exc_info=exc)
