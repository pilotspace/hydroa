"""OpenTelemetry span export for the completion path.

Hand-rolled OTLP/HTTP JSON exporter — zero new packages, uses existing httpx.
All errors are swallowed; collector outages have zero request impact.

§3 CONTRACT pinned classes:
  OtelSpan         — frozen dataclass (domain entity)
  OtelSpanEmitter  — Protocol (capability seam)
  QueueOtelSpanEmitter — bounded asyncio.Queue emitter with DROP-OLDEST overflow
  OtelFlusher      — batches queued spans into a single OTLP POST
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain entity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OtelSpan:
    """Immutable domain entity representing one proxy completion span.

    All fields are set at use-case exit; the dataclass is frozen so callers
    cannot mutate it after creation.
    """

    trace_id: str  # 32 lowercase hex chars (random 16 bytes)
    span_id: str  # 16 lowercase hex chars (random 8 bytes)
    name: str  # always "proxy.completion"
    start_time_ns: int  # time.time_ns() at use-case entry
    end_time_ns: int  # time.time_ns() at use-case exit
    tenant_id: str  # always set — span only emitted post-authz
    key_id: str  # always set
    team_id: str | None  # set when authz.team_id is not None
    model: str  # always set — after _validate_payload
    status_code: int  # HTTP status (200/400/402/429/502 etc.)
    stream: bool  # True for streaming path
    cached: bool = field(default=False)  # True on cache HIT (x_cache == "hit")
    guardrail_blocked: bool = field(default=False)  # True when ERR_GUARDRAIL_BLOCKED raised
    # True when the fallback router served a different candidate than the
    # group's first choice (model-fallbacks §3 OBSERVABILITY — additive field,
    # default False keeps all prior span shapes byte-identical).
    fallback: bool = field(default=False)
    # ProblemError code for error spans ("ERR_BUDGET_EXCEEDED" etc.); None for
    # success or non-Problem errors — used as status.message in OTLP export.
    error_code: str | None = field(default=None)

    @property
    def attributes(self) -> dict[str, Any]:
        """Return span attributes as a flat dict (for test assertions via _get_span_attr)."""
        attrs: dict[str, Any] = {
            "ai_proxy.tenant_id": self.tenant_id,
            "ai_proxy.key_id": self.key_id,
            "ai_proxy.model": self.model,
            "ai_proxy.status_code": self.status_code,
            "ai_proxy.stream": "true" if self.stream else "false",
        }
        if self.team_id is not None:
            attrs["ai_proxy.team_id"] = self.team_id
        if self.cached:
            attrs["ai_proxy.cached"] = "true"
        if self.guardrail_blocked:
            attrs["ai_proxy.guardrail_blocked"] = "true"
        if self.fallback:
            attrs["ai_proxy.fallback"] = "true"
        return attrs


# ---------------------------------------------------------------------------
# Port / capability seam
# ---------------------------------------------------------------------------


@runtime_checkable
class OtelSpanEmitter(Protocol):
    """Domain-layer port: capability seam for span emission.

    CompletionUseCase accepts `OtelSpanEmitter | None`; when None (default,
    otel_enabled=False), no spans are emitted and behavior is unchanged.
    """

    async def emit(self, span: OtelSpan) -> None:
        """Enqueue span for async export. Must never raise."""
        ...


# ---------------------------------------------------------------------------
# OTLP JSON serialisation helpers
# ---------------------------------------------------------------------------


def _span_to_otlp(span: OtelSpan) -> dict[str, Any]:
    """Serialize OtelSpan to OTLP/HTTP JSON span object.

    Per §3 CONTRACT:
      - startTimeUnixNano / endTimeUnixNano are JSON STRINGS (int64 > JS MAX_SAFE_INT)
      - ai_proxy.status_code is intValue (JSON NUMBER, not string)
      - other attributes use stringValue
      - status {"code": 1} for 2xx, {"code": 2, "message": "<code>"} for errors
    """
    attributes: list[dict[str, Any]] = [
        {"key": "ai_proxy.tenant_id", "value": {"stringValue": span.tenant_id}},
        {"key": "ai_proxy.key_id", "value": {"stringValue": span.key_id}},
    ]
    if span.team_id is not None:
        attributes.append({"key": "ai_proxy.team_id", "value": {"stringValue": span.team_id}})
    attributes.append({"key": "ai_proxy.model", "value": {"stringValue": span.model}})
    attributes.append({"key": "ai_proxy.status_code", "value": {"intValue": span.status_code}})
    attributes.append(
        {"key": "ai_proxy.stream", "value": {"stringValue": "true" if span.stream else "false"}}
    )
    if span.cached:
        attributes.append({"key": "ai_proxy.cached", "value": {"stringValue": "true"}})
    if span.guardrail_blocked:
        attributes.append({"key": "ai_proxy.guardrail_blocked", "value": {"stringValue": "true"}})
    if span.fallback:
        attributes.append({"key": "ai_proxy.fallback", "value": {"stringValue": "true"}})

    # Status: code=1 (OK) for <400, code=2 (ERROR) otherwise
    if span.status_code < 400:
        status: dict[str, Any] = {"code": 1}
    else:
        # message carries the ProblemError code when available (§3); generic
        # non-Problem errors fall back to the numeric status string.
        status = {"code": 2, "message": span.error_code or str(span.status_code)}

    return {
        "traceId": span.trace_id,
        "spanId": span.span_id,
        "name": span.name,
        "startTimeUnixNano": str(span.start_time_ns),
        "endTimeUnixNano": str(span.end_time_ns),
        "attributes": attributes,
        "status": status,
    }


def _build_otlp_body(
    spans: list[OtelSpan],
    service_name: str,
) -> dict[str, Any]:
    """Build the OTLP ResourceSpans JSON body for a batch of spans."""
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "hydroa-gateway"},
                        "spans": [_span_to_otlp(s) for s in spans],
                    }
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# QueueOtelSpanEmitter — bounded queue with DROP-OLDEST overflow
# ---------------------------------------------------------------------------


class QueueOtelSpanEmitter:
    """Protocol-compatible emitter backed by a bounded asyncio.Queue.

    On overflow: discards the oldest span (ring-buffer semantics) and
    increments gateway_otel_spans_total{result="dropped"}.

    Thread-safety: asyncio.Queue is safe for multiple concurrent coroutine
    producers on the same event loop (all ASGI requests share one loop).
    """

    def __init__(
        self,
        queue: asyncio.Queue[OtelSpan],
        metrics_registry: Any,
    ) -> None:
        self._queue = queue
        self._metrics_registry = metrics_registry

    async def emit(self, span: OtelSpan) -> None:
        """Enqueue span; drop oldest on overflow. Never raises."""
        try:
            if self._queue.full():
                # DROP-OLDEST: evict the stale front item, then enqueue the new span.
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                # Increment dropped counter
                try:
                    counter = getattr(self._metrics_registry, "otel_spans_total", None)
                    if counter is not None:
                        counter.labels(result="dropped").inc()
                except Exception:  # noqa: S110
                    pass
            self._queue.put_nowait(span)
        except Exception:  # noqa: S110
            # Safety net: emit must never propagate into the request path.
            pass


# ---------------------------------------------------------------------------
# OtelFlusher — background OTLP POST task
# ---------------------------------------------------------------------------


class OtelFlusher:
    """Drains the span queue and POSTs batches to the OTLP collector.

    All network errors are swallowed and logged — a collector outage has zero
    request impact (§3 INVIOLABLE).

    Args:
        queue: Shared asyncio.Queue populated by QueueOtelSpanEmitter.
        export_url: Base URL for the OTLP collector (e.g. "http://otel:4318").
        service_name: Identifies this service in OTLP resource attributes.
        httpx_client: Injected httpx.AsyncClient (allows FakeHttpClient in tests).
        metrics_registry: MetricsRegistry instance for counter updates.
    """

    def __init__(
        self,
        queue: asyncio.Queue[OtelSpan],
        export_url: str,
        service_name: str,
        httpx_client: Any,
        metrics_registry: Any,
    ) -> None:
        self._queue = queue
        self._export_url = export_url.rstrip("/")
        self._service_name = service_name
        self._client = httpx_client
        self._metrics_registry = metrics_registry

    async def flush_once(self) -> None:
        """Drain all currently pending spans and POST as one batch.

        Steps:
          1. Non-blocking drain of all items currently in the queue.
          2. If queue was empty, return immediately (no POST).
          3. Build OTLP JSON body.
          4. POST to {export_url}/v1/traces.
          5. On success (2xx): increment {result="exported"} per span.
          6. On any error (network/timeout/non-2xx): log warning,
             increment {result="error"} per span, swallow exception.

        NEVER raises — all errors are caught and logged.
        """
        spans: list[OtelSpan] = []
        while True:
            try:
                span = self._queue.get_nowait()
                spans.append(span)
            except asyncio.QueueEmpty:
                break

        if not spans:
            return

        body = _build_otlp_body(spans, self._service_name)
        url = f"{self._export_url}/v1/traces"

        try:
            import httpx as _httpx

            timeout = _httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
            resp = await self._client.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            if resp.status_code >= 200 and resp.status_code < 300:
                self._inc_counter("exported", len(spans))
            else:
                _log.warning(
                    "otel_flush: collector returned non-2xx",
                    extra={"status_code": resp.status_code, "url": url, "span_count": len(spans)},
                )
                self._inc_counter("error", len(spans))
        except Exception as exc:
            _log.warning(
                "otel_flush: POST to collector failed (collector down?)",
                exc_info=exc,
                extra={"url": url, "span_count": len(spans)},
            )
            self._inc_counter("error", len(spans))

    def _inc_counter(self, result: str, count: int) -> None:
        """Safely increment the otel_spans_total counter; swallows all errors."""
        try:
            counter = getattr(self._metrics_registry, "otel_spans_total", None)
            if counter is not None:
                for _ in range(count):
                    counter.labels(result=result).inc()
        except Exception:  # noqa: S110
            pass

    async def run_forever(self, interval_seconds: float = 5.0) -> None:
        """Loop calling flush_once() then sleeping interval_seconds.

        Started as a background asyncio.Task by main.py lifespan.
        Cancellation (CancelledError) is not caught here — the task
        cancels cleanly when the lifespan shutdown cancels it.
        """
        while True:
            await self.flush_once()
            await asyncio.sleep(interval_seconds)
