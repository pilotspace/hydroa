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
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog

from gateway.proxy.application.fallback_triggers import classify_fallback_trigger
from gateway.proxy.application.routing_strategy import (
    AsyncRoutingStrategy,
    OrderedStrategy,
    RoutingStrategy,
)
from gateway.proxy.application.streaming_resilience import open_resilient_stream
from gateway.proxy.domain.errors import AllDeploymentsSaturatedError, UpstreamUnavailableError
from gateway.proxy.domain.ports import (
    CompletionUpstream,
    DeploymentLimitGate,
    DeploymentLoadGate,
    ModelHealthGate,
)

if TYPE_CHECKING:
    from gateway.core.config import Deployment

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
        upstream: CompletionUpstream | None = None,
        model_groups: dict[str, list[str]] | None = None,
        health_gate: ModelHealthGate | None = None,
        metrics_registry: Any = None,
        deployments: dict[str, list[Deployment]] | None = None,
        strategy: RoutingStrategy | None = None,
        load_gate: DeploymentLoadGate | None = None,
        limit_gate: DeploymentLimitGate | None = None,
        fallback_on_error: bool = False,
        stream_resilience_enabled: bool = False,
    ) -> None:
        # upstream and model_groups kept as positional-compatible; None only for
        # test convenience (tests always pass them explicitly).
        self._upstream: CompletionUpstream = upstream  # type: ignore[assignment]
        self._model_groups: dict[str, list[str]] = model_groups or {}
        self._health_gate = health_gate
        self._metrics_registry = metrics_registry
        # Normalized deployment view (deployment-model v8). The router's own
        # fallback logic still iterates the bare-string `model_groups` (byte-identical
        # to v6); `deployments` is exposed for the v8 routing-strategy layer.
        self._deployments: dict[str, list[Deployment]] = deployments or {}
        # Routing strategy (routing-strategy v8). None ⇒ OrderedStrategy → v6 byte-identical.
        self._strategy: RoutingStrategy = strategy if strategy is not None else OrderedStrategy()
        # Load gate (balance-strategies v8). None ⇒ no acquire/release/record_latency;
        # byte-identical to v6 when None.
        self._load_gate: DeploymentLoadGate | None = load_gate
        # Limit gate (deployment-limits v8). None ⇒ no saturation filter/recording;
        # byte-identical to v6/balance-strategies when None.
        self._limit_gate: DeploymentLimitGate | None = limit_gate
        # Error-aware fallback (error-aware-fallback v19). False (default) ⇒ a 4xx is
        # returned to the client after the first candidate (v6 byte-identical). True ⇒ a
        # context-window / content-policy 4xx falls over to the next candidate.
        self._fallback_on_error: bool = fallback_on_error
        # Pre-first-byte streaming resilience (streaming-resilience v19). False (default) ⇒
        # the sync stream() path (first candidate only) is used. True ⇒ the use case calls
        # stream_resilient() to fall over across candidates before the first SSE byte.
        self._stream_resilience_enabled: bool = stream_resilience_enabled

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

    @property
    def deployments(self) -> dict[str, list[Deployment]]:
        """Read-only normalized deployment view (alias -> [Deployment, ...], order-preserved).

        Exposed for the v8 routing-strategy layer; the router's own fallback path
        still iterates the bare-string `model_groups` view (v6 byte-identical).
        """
        return self._deployments

    def candidates_for(self, model_id: str) -> list[str] | None:
        """Return the ordered candidate list when model_id is an alias, else None.

        Used by the use case to detect that a fallback occurred (served model
        differs from the group's first candidate) for span attribution.
        """
        return self._model_groups.get(model_id)

    def _strategy_order(self, alias: str, candidates: list[str]) -> list[str]:
        """Ask the routing strategy for the attempt order (sync); guard it is a permutation.

        For the default OrderedStrategy this returns `candidates` unchanged
        (v6 byte-identical). A strategy that adds/drops/duplicates a candidate is a
        programming bug — abort rather than serve a wrong/partial set.
        Used by stream() (sync path) and as fallback when no async capability.
        """
        deployments = self._deployments.get(alias, [])
        order = self._strategy.order(alias, candidates, deployments)
        if len(order) != len(candidates) or set(order) != set(candidates):
            raise ValueError(
                f"INVALID_ROUTING_ORDER: strategy returned a non-permutation for alias '{alias}'"
            )
        return order

    async def _strategy_order_async(self, alias: str, candidates: list[str]) -> list[str]:
        """Ask the routing strategy for the attempt order (async-aware).

        Prefers aorder() when the strategy implements AsyncRoutingStrategy; falls back
        to sync order(). Permutation guard covers both paths.
        """
        deployments = self._deployments.get(alias, [])
        if isinstance(self._strategy, AsyncRoutingStrategy):
            order = await self._strategy.aorder(alias, candidates, deployments)
        else:
            order = self._strategy.order(alias, candidates, deployments)
        if len(order) != len(candidates) or set(order) != set(candidates):
            raise ValueError(
                f"INVALID_ROUTING_ORDER: strategy returned a non-permutation for alias '{alias}'"
            )
        return order

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

        # Alias path — iterate candidates with fallback, in the strategy's order.
        gate = self._health_gate
        load_gate = self._load_gate
        limit_gate = self._limit_gate
        alias = model_id

        # Deployment-limits filter (v8): skip saturated candidates BEFORE the strategy.
        # When limit_gate is None this block is completely skipped — byte-identical to v6.
        if limit_gate is not None:
            deps = {d.model_id: d for d in self._deployments.get(alias, [])}
            survivors: list[str] = []
            for c in candidates:
                dep = deps.get(c)
                rpm = dep.rpm_limit if dep is not None else None
                tpm = dep.tpm_limit if dep is not None else None
                try:
                    saturated = await limit_gate.is_saturated(c, rpm, tpm)
                except Exception:
                    # Fail-OPEN: any gate error admits the candidate.
                    saturated = False
                if not saturated:
                    survivors.append(c)
            if not survivors:
                raise AllDeploymentsSaturatedError(alias)
            candidates = survivors

        order = await self._strategy_order_async(alias, candidates)
        last_fallen: str | None = None
        # Last classifiable trigger-4xx (error-aware-fallback v19): if the loop ends with no
        # served response but an earlier candidate produced a context-window/content-policy
        # 4xx, this carries it so the client receives the REAL error, not a synthetic 502.
        last_error: tuple[int, dict[str, Any], str] | None = None

        for i, candidate in enumerate(order):
            next_model = order[i + 1] if i + 1 < len(order) else "_exhausted"

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
            rewritten: dict[str, object] = {**payload, "model": candidate}

            # Step 2b (load-gate): acquire in-flight slot before the upstream call.
            if load_gate is not None:
                await load_gate.acquire(candidate)

            # Step 3: call upstream inside try/finally so release fires on ALL exit edges.
            _t_start = time.monotonic()
            try:
                # CircuitOpenError and any non-fallback exception propagate naturally
                # (abort loop, no health gate call). The finally still releases.
                status, body = await _upstream.complete(rewritten)
            except UpstreamUnavailableError:
                # Step 5: fall-through to the next candidate (release fires in finally).
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
            finally:
                # Release on ALL four exit edges: served-return (before return below),
                # UpstreamUnavailable fallthrough-continue, exhausted raise, other re-raise.
                if load_gate is not None:
                    await load_gate.release(candidate)

            # Step 3.5 (error-aware-fallback v19): an enabled, classifiable trigger-4xx with
            # a NEXT candidate falls over instead of returning the 4xx. The candidate
            # ANSWERED → the model is alive (record_success, never record_failure — a
            # context-window/content-policy rejection is request-specific, not a cooldown
            # signal). Billing-safe: the discarded 4xx is never returned, so the use case
            # never bills it. Latency is NOT recorded here (a fast rejection must not make a
            # model look attractive to load-aware strategies).
            if self._fallback_on_error and i + 1 < len(order):
                trigger = classify_fallback_trigger(status, body)
                if trigger is not None:
                    if gate is not None:
                        await gate.record_success(candidate)
                    self._inc_counter(
                        alias=alias, from_model=candidate, to_model=next_model, outcome=trigger
                    )
                    last_error = (status, body, candidate)
                    last_fallen = candidate
                    continue

            # Step 4: candidate answered (any status including 4xx passthrough).
            # record_latency AFTER a candidate answers (not on UpstreamUnavailableError).
            if load_gate is not None:
                elapsed_ms = (time.monotonic() - _t_start) * 1000.0
                await load_gate.record_latency(candidate, elapsed_ms)

            if gate is not None:
                await gate.record_success(candidate)

            if last_fallen is not None:
                self._inc_counter(
                    alias=alias, from_model=last_fallen, to_model=candidate, outcome="served"
                )

            # Deployment-limits recording: record RPM hit and response tokens for the
            # SERVED candidate only. When limit_gate is None this block is skipped —
            # byte-identical to v6/balance-strategies.
            if limit_gate is not None:
                await limit_gate.record_request(candidate)
                _usage = body.get("usage")
                _tokens: Any = _usage.get("total_tokens") if isinstance(_usage, dict) else None
                if isinstance(_tokens, int) and _tokens > 0:
                    await limit_gate.record_tokens(candidate, _tokens)

            return status, body, candidate

        # Loop ended without a served return. If an earlier candidate produced a
        # classifiable trigger-4xx (error-aware-fallback v19), return that real error so the
        # client sees the actual upstream 4xx (e.g. context-window) rather than a 502.
        if last_error is not None:
            return last_error

        # All candidates exhausted or gated (loop ended without return).
        self._inc_counter(
            alias=alias,
            from_model=order[-1] if order else "_exhausted",
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

        # Alias: resolve to the strategy PRIMARY only (§3 STREAMING BOUNDARY — no
        # fallback on stream). For OrderedStrategy this is candidates[0] (v6 byte-identical).
        primary = self._strategy_order(model_id, candidates)[0]
        rewritten: dict[str, object] = {**payload, "model": primary}
        return _upstream.stream(rewritten)

    async def stream_resilient(
        self,
        payload: dict[str, Any],
        upstream: CompletionUpstream | None = None,
    ) -> tuple[bytes | None, AsyncIterator[bytes]]:
        """Pre-first-byte resilient streaming (streaming-resilience v19).

        Returns (first_chunk, rest): the use case yields first_chunk (when not None) then
        drains rest. Fallover happens ONLY before the first chunk reaches the client:

          - Alias -> strategy_order(candidates): on a pre-first-byte transport failure
            (UpstreamUnavailableError / CircuitOpenError) fall over to the next candidate.
          - Plain model id -> a single attempt (no same-target streaming retry — the
            documented boundary; the retry seam stays complete()-only).

        The first candidate whose first chunk is obtained COMMITS the stream; a later
        (mid-stream) error surfaces to the caller's drain loop and is never replayed. If
        EVERY candidate fails pre-first-byte, UpstreamUnavailableError propagates (the use
        case maps it to 502 BEFORE the response commits).

        Only invoked by the use case when stream resilience is enabled; the sync stream()
        above remains the byte-identical default path.
        """
        _upstream = self._resolve_upstream(upstream)
        alias: str = payload.get("model", "")

        candidates = self._model_groups.get(alias)
        if candidates is None:
            attempts = [alias]  # plain model id — single attempt, no retry
        else:
            attempts = self._strategy_order(alias, candidates)

        def _open(model_id: str) -> AsyncIterator[bytes]:
            return _upstream.stream({**payload, "model": model_id})

        def _on_fallover(from_model: str, to_model: str) -> None:
            self._inc_counter(
                alias=alias, from_model=from_model, to_model=to_model, outcome="stream_fallover"
            )

        return await open_resilient_stream(
            attempts=attempts, open_stream=_open, on_fallover=_on_fallover
        )
