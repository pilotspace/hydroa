# Shared context — balance-strategies (v8 task 3/5)

Frozen task spec: `.add/tasks/balance-strategies/TASK.md` (§1 SPECIFY · §2 SCENARIOS ·
§3 CONTRACT FROZEN @ v1 · §4 TEST PLAN). Read it FIRST and in full — it is the single
source of truth. Do NOT edit it.

## What this task adds (frozen)
Two LOAD-AWARE routing strategies (`least-busy`, `latency`) that pick a model group's
PRIMARY deployment from live per-deployment metrics in Redis, plus the async-supersession
plumbing. Builds on routing-strategy (v8 task 2, DONE). See §3 CONTRACT for exact shapes.

## Hard rules (NON-NEGOTIABLE — a violation is ERR_FROZEN_VIOLATION / HARD-STOP)
- The frozen sync `RoutingStrategy.order()` signature is IMMUTABLE — routing-strategy's
  frozen tests (`tests/routing_strategy/test_routing_strategy.py` test_rs3/rs4/rs5) call
  `order()` SYNCHRONOUSLY (`strat.order(...)[0]`). NEVER make `order()` async.
- `stream()` stays SYNC and UNCHANGED (sync call site `use_cases.py:1237`). Load-aware
  strategies implement sync `order()` as declared-order → ordered primary on stream.
- v6 chat path BYTE-IDENTICAL when `routing_strategy ∈ {ordered, simple-shuffle}` and
  `load_gate is None`: zero new Redis IO, no acquire/release/record_latency.
- The in-flight acquire/release LIFECYCLE (when load_gate wired) must `release()` on ALL
  FOUR exit edges of complete()'s candidate loop via per-attempt `try/finally`: served-return,
  UpstreamUnavailable fallthrough-continue, all-exhausted raise, CircuitOpenError/other re-raise.
  This is the bundle's #1 risk (see §3 freeze flag). Do NOT alter v6 control flow or the
  fallback-counter `to_model` labels.
- DeploymentLoadGate is FAIL-OPEN: any Redis read error → neutral value (in_flight=0 /
  ewma=0.0); acquire/release errors swallowed. NEVER a 500. Log deployment_id ONLY — never
  key strings, never secrets.
- Secrets: no api keys / secrets in any log field, metric label, span attr, or Redis key.
  Redis keys use deployment_id only.

## Files (all under apps/gateway/)
- MODIFY `src/gateway/proxy/application/routing_strategy.py` — ADD `AsyncRoutingStrategy`
  Protocol, `LeastBusyStrategy`, `LatencyStrategy`; extend `build_strategy(name, load_gate=None)`.
  Keep existing OrderedStrategy / SimpleShuffleStrategy / order() UNCHANGED.
- MODIFY `src/gateway/proxy/domain/ports.py` — ADD `DeploymentLoadGate` @runtime_checkable Protocol.
- NEW `src/gateway/proxy/infrastructure/redis_load_gate.py` — `RedisDeploymentLoadGate`,
  mirror `redis_cooldown_gate.py` exactly (constructor does NOT connect; fail-OPEN; `redis: Any`).
- MODIFY `src/gateway/core/config.py` — extend routing_strategy valid set; add
  `loadbal_ewma_alpha: float = 0.3` (0<α≤1 else INVALID_LOADBAL_ALPHA) and
  `loadbal_inflight_ttl_s: int = 60` (>0 else INVALID_LOADBAL_TTL) with validators.
- MODIFY `src/gateway/proxy/application/fallback_router.py` — additive `load_gate` kwarg;
  async-aware selection (isinstance AsyncRoutingStrategy → await aorder, else sync order);
  in-flight lifecycle when load_gate wired. stream() UNCHANGED.
- MODIFY `src/gateway/main.py` — wire load_gate (RedisDeploymentLoadGate) only when
  routing_strategy ∈ {least-busy, latency}; pass build_strategy(name, load_gate) + load_gate
  to FallbackModelRouter.

## Test idioms to reuse
- `tests/routing_strategy/test_routing_strategy.py` — fakes `_Upstream` (replay outcomes,
  records `.calls`/`.stream_calls`), `_Gate` (always-available health gate), `_deployment(id, w)`.
  Copy these idioms into the new suite (self-contained local fakes, like routing_strategy did).
- Settings test kwargs (from test_rs7): database_url="postgresql+asyncpg://gateway:gateway@
  localhost:5433/gateway_test", jwt_secret="test-secret-not-for-production-0123456789",
  redis_url="redis://localhost:6380/9", environment="test".
- FakeRedis pattern for the RedisDeploymentLoadGate unit test: see
  `tests/cooldown_circuit/conftest.py` (in-memory async fake: GET/SET/INCR/EXPIRE/DEL).
  You will need DECR too — extend a local minimal fake in the new suite if needed.
- The PRIMARY fake for BS1–BS12 is a `FakeLoadGate` (implements DeploymentLoadGate) with
  scripted in_flight/ewma dicts + a call-recording spy list — NOT real Redis.

## Tests location
`apps/gateway/tests/balance_strategies/test_balance_strategies.py` (+ `__init__.py` if the
sibling suites have one — check `tests/routing_strategy/`).

## Verification commands (repo root)
- Red check (tests phase): `cd apps/gateway && uv run pytest tests/balance_strategies -o addopts="" -q`
- Full gate (build/verify): `make ci` (from repo root, via `rtk`).
- Targeted regression: `cd apps/gateway && uv run pytest tests/routing_strategy tests/model_fallbacks
  tests/routing_admin tests/proxy tests/deployment_model -o addopts="" -q`
