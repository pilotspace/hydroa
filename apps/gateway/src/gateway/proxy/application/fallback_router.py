"""FallbackModelRouter — alias-aware fallback orchestrator.

Application-layer seam introduced by model-fallbacks TASK.md §3.

This class sits ABOVE upstream.complete() in the use-case call chain.
It is NOT a CompletionUpstream drop-in — it returns a 3-tuple
(status_code, json_body, served_model_id) instead of the 2-tuple the
CompletionUpstream protocol returns.  The use case unpacks 3 elements
at its single router.complete() call site.

SEAM INJECTION CONTRACT (critical for frozen suites):
  Frozen suites (tests/proxy/, tests/edge/, tests/response_caching/, etc.)
  replace app.state.completion_upstream with fake upstreams at test time.
  The router MUST NOT capture app.state.completion_upstream at create_app()
  time — doing so would bake the production OpenRouterCompletionUpstream into
  the router at construction time, causing frozen suites to call the real
  upstream instead of their injected fakes.

  Solution: the router accepts `upstream` at construction time, and the
  use case reads `request.app.state.completion_upstream` per-request and
  passes it to `router.complete(payload, upstream=upstream)` — OR (simpler
  and equivalent): the router is reconstructed per-request by the dep
  provider, OR the use case reads the current upstream from app.state and
  passes it into router.complete() as an override kwarg.

  Chosen approach (cleanest): the use case calls router.complete(payload)
  where the router was constructed with the upstream at create_app() time,
  BUT the use_cases.py call site instead calls the router with the upstream
  that was injected per-request via the existing `upstream` parameter.
  The FallbackModelRouter.complete() therefore accepts an optional
  `upstream` override kwarg so the use case can supply the request-time
  upstream (which may be a fake injected by a frozen test suite).
  When upstream=None (not passed), the router uses its construction-time
  upstream — this path is used by the wiring tests (which do not swap the
  upstream).

  See use_cases.py for the call site: the router receives the per-request
  upstream (from app.state.completion_upstream, circuit-breaker-wrapped)
  via the override parameter, so frozen suites that inject fakes into
  app.state.completion_upstream work unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import structlog

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream, ModelHealthGate

_log = logging.getLogger(__name__)


class FallbackModelRouter:
    """Alias-aware completion router with ordered-candidate fallback.

    Plain model ids (no alias match) are passed through transparently — zero
    new code-path side effects, byte-identical to v5 behavior.

    Alias resolution:
      For each candidate in the declared order:
        1. If health_gate and not await gate.is_available(candidate): skip.
        2. Rewrite payload["model"] = candidate.
        3. await upstream.complete(rewritten_payload).
        4. On (status, body): record_success (if gate wired); return 3-tuple.
        5. On UpstreamUnavailableError: record_failure (if gate wired); continue.
        6. On CircuitOpenError / other: re-raise immediately (no gate call).
      If all candidates exhausted or gated: raise UpstreamUnavailableError.

    The gate is fail-open by its own contract (§3 A2): it never raises for
    operational failures. An exception escaping the gate is a programming bug
    and propagates (abort loop, re-raise) — the router adds no try/except.
    """

    def __init__(
        self,
        upstream: CompletionUpstream,
        model_groups: dict[str, list[str]],
        health_gate: ModelHealthGate | None = None,
        metrics_registry: Any = None,
    ) -> None:
        self._upstream = upstream
        self._model_groups = model_groups
        self._health_gate = health_gate
        self._metrics_registry = metrics_registry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_counter(self) -> Any:
        """Return gateway_model_fallbacks_total counter or None (fail-open)."""
        if self._metrics_registry is None:
            return None
        return getattr(self._metrics_registry, "model_fallbacks_total", None)

    def _inc_counter(
        self,
        *,
        alias: str,
        from_model: str,
        to_model: str,
        outcome: str,
    ) -> None:
        """Increment the fallback counter; swallows all errors (metrics fail-open)."""
        try:
            counter = self._get_counter()
            if counter is not None:
                counter.labels(
                    alias=alias,
                    from_model=from_model,
                    to_model=to_model,
                    outcome=outcome,
                ).inc()
        except Exception:  # noqa: S110
            pass

    def _resolve_upstream(self, upstream_override: CompletionUpstream | None) -> CompletionUpstream:
        """Return the request-time upstream (override) or the construction-time upstream."""
        return upstream_override if upstream_override is not None else self._upstream

    @property
    def model_groups(self) -> dict[str, list[str]]:
        """Read-only view of the configured alias groups (for alias-aware checks)."""
        return self._model_groups

    def candidates_for(self, model_id: str) -> list[str] | None:
        """Return the ordered candidate list when model_id is an alias, else None.

        Used by the use case to detect that a fallback occurred (served model
        differs from the group's first candidate) for span attribution.
        """
        return self._model_groups.get(model_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        payload: dict[str, Any],
        upstream: CompletionUpstream | None = None,
    ) -> tuple[int, dict[str, Any], str]:
        """Route a non-streaming completion request.

        Returns (status_code, json_body, served_model_id) — a 3-tuple.

        served_model_id:
          - Plain model id: original payload["model"], unchanged.
          - Alias: the candidate id whose complete() returned (not body["model"],
            which may differ due to OpenRouter ":free" suffix variants).

        The `upstream` kwarg allows the use case to pass the request-time
        upstream (potentially a fake injected by a frozen test suite) so that
        app.state.completion_upstream injection semantics are preserved.
        """
        _upstream = self._resolve_upstream(upstream)
        model_id: str = payload.get("model", "")

        candidates = self._model_groups.get(model_id)
        if candidates is None:
            # Plain model id path — transparent pass-through, zero new code paths.
            status, body = await _upstream.complete(payload)
            return status, body, model_id

        # Alias path — iterate candidates with fallback.
        gate = self._health_gate
        alias = model_id
        last_fallen: str | None = None

        for i, candidate in enumerate(candidates):
            next_model = candidates[i + 1] if i + 1 < len(candidates) else "_exhausted"

            # Step 1: health gate check — skip = fell_through without an upstream call.
            if gate is not None and not await gate.is_available(candidate):
                structlog.get_logger().warning(
                    "model_fallback: candidate gated unavailable, skipping",
                    alias=alias,
                    from_model=candidate,
                    attempt=i + 1,
                )
                self._inc_counter(
                    alias=alias, from_model=candidate, to_model=next_model, outcome="fell_through"
                )
                last_fallen = candidate
                continue

            # Step 2: rewrite payload["model"] = candidate.
            rewritten = {**payload, "model": candidate}

            try:
                # Step 3: call upstream. CircuitOpenError and any non-fallback
                # exception propagate naturally (abort loop, no gate call).
                status, body = await _upstream.complete(rewritten)
            except UpstreamUnavailableError:
                # Step 5: fall-through to the next candidate.
                if gate is not None:
                    await gate.record_failure(candidate)
                structlog.get_logger().warning(
                    "model_fallback: candidate exhausted, falling through",
                    alias=alias,
                    from_model=candidate,
                    attempt=i + 1,
                )
                self._inc_counter(
                    alias=alias, from_model=candidate, to_model=next_model, outcome="fell_through"
                )
                last_fallen = candidate
                continue

            # Step 4: candidate answered (any status including 4xx passthrough).
            if gate is not None:
                await gate.record_success(candidate)

            if last_fallen is not None:
                self._inc_counter(
                    alias=alias, from_model=last_fallen, to_model=candidate, outcome="served"
                )

            return status, body, candidate

        # All candidates exhausted or gated (loop ended without return).
        self._inc_counter(
            alias=alias,
            from_model=candidates[-1] if candidates else "_exhausted",
            to_model="_exhausted",
            outcome="exhausted",
        )
        raise UpstreamUnavailableError(f"All candidates for alias '{alias}' are unavailable")

    def stream(
        self,
        payload: dict[str, Any],
        upstream: CompletionUpstream | None = None,
    ) -> AsyncIterator[bytes]:
        """Route a streaming completion request.

        Alias: rewrite payload["model"] = candidates[0]; delegate to upstream.stream().
        Plain model id: delegate directly.
        No health gate check on stream path (v6 scope boundary).
        No fallback on stream failure (deferred beyond v6).

        The `upstream` kwarg allows the use case to pass the request-time upstream.
        """
        _upstream = self._resolve_upstream(upstream)
        model_id: str = payload.get("model", "")

        candidates = self._model_groups.get(model_id)
        if candidates is None:
            # Plain model id — delegate directly.
            return _upstream.stream(payload)

        # Alias: resolve to first candidate only (§3 STREAMING BOUNDARY).
        rewritten = {**payload, "model": candidates[0]}
        return _upstream.stream(rewritten)
