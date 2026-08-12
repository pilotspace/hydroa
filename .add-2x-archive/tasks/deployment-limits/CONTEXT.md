# Shared context — deployment-limits (v8 task 4/5)

Frozen task spec: `.add/tasks/deployment-limits/TASK.md` (§1–§4; §3 CONTRACT FROZEN @ v1).
Read it FIRST and in full — single source of truth. Do NOT edit it.

## What this task adds (frozen)
Usage-based routing: a deployment over its per-deployment RPM/TPM limit is SKIPPED at
selection; when ALL are saturated → clean 429 ERR_RATE_LIMITED (never 500). Builds on the v8
router (routing-strategy + balance-strategies, both DONE) and the frozen Deployment.rpm_limit /
tpm_limit fields. See §3 CONTRACT for exact shapes.

## Hard rules (NON-NEGOTIABLE — violation = ERR_FROZEN_VIOLATION / HARD-STOP)
- v6 chat path BYTE-IDENTICAL when limit_gate is None (no deployment declares a limit): no
  candidate filtering, no recording, zero new Redis IO.
- Do NOT make the frozen sync order()/stream() async; do NOT edit any frozen test
  (routing_strategy, balance_strategies, model_fallbacks, proxy, deployment_model, cooldown).
- The limit filter runs UPSTREAM of the strategy in complete() ONLY (stream() unchanged — no
  filter on the stream path).
- is_saturated is FAIL-OPEN (Redis error ⇒ False ⇒ admit; never a false-429, never a 500).
  record_* are fail-soft (swallow errors). Log deployment_id ONLY — never key strings/secrets.
- Redis keys use deployment_id only (gateway:deplimit:{rpm,tpm}:{id}:{bucket}).
- 429 reuses the EXISTING `error_catalog.RATE_LIMITED` spec (ErrorSpec(429, "ERR_RATE_LIMITED")).
  Look at how use_cases.py / governance.py already raise `RATE_LIMITED.exc(detail=..., ...)` and
  MATCH that signature exactly (check the real ErrorSpec.exc kwargs — e.g. retry_after_s).

## Files (all under apps/gateway/)
- MODIFY `src/gateway/proxy/domain/ports.py` — ADD DeploymentLimitGate @runtime_checkable Protocol.
- MODIFY `src/gateway/proxy/domain/errors.py` — ADD AllDeploymentsSaturatedError(alias).
- NEW `src/gateway/proxy/infrastructure/redis_limit_gate.py` — RedisDeploymentLimitGate; mirror
  `redis_load_gate.py` / `redis_cooldown_gate.py` (redis: Any, no connect in ctor, fail-OPEN).
  Per-minute fixed windows (bucket = floor(now/window_s), window_s=60).
- MODIFY `src/gateway/proxy/application/fallback_router.py` — additive `limit_gate` kwarg;
  filter candidates BEFORE _strategy_order_async; raise AllDeploymentsSaturatedError when no
  survivors; record_request + record_tokens on the SERVED candidate. limit_gate=None ⇒ no-op.
- MODIFY `src/gateway/proxy/application/use_cases.py` — ADD an except clause around the existing
  `await model_router.complete(...)` (~L961) mapping AllDeploymentsSaturatedError → RATE_LIMITED.exc.
  Touch ONLY that — the chat success path stays byte-identical.
- MODIFY `src/gateway/main.py` — wire RedisDeploymentLimitGate(redis=redis_client) into the
  router ONLY when some configured deployment has rpm_limit or tpm_limit set; else limit_gate=None.

## Reuse / idioms
- `src/gateway/rate_limits/infrastructure/redis_lua_limiter.py` — the v1 per-minute window idiom
  (do NOT reuse its RAISE semantics; we need skip-not-raise booleans).
- `src/gateway/core/error_catalog.py` — RATE_LIMITED spec + `.exc(...)`.
- Test fakes: `tests/balance_strategies/test_balance_strategies.py` (FakeLoadGate, _Upstream,
  _Gate, _deployment) and `tests/routing_strategy/test_routing_strategy.py`. Settings test kwargs:
  database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
  jwt_secret="test-secret-not-for-production-0123456789", redis_url="redis://localhost:6380/9",
  environment="test". For the 429 integration test, look at `tests/rate_limits/` or `tests/proxy/`
  for the endpoint harness that asserts an HTTP status from a completion.

## Tests location
`apps/gateway/tests/deployment_limits/test_deployment_limits.py` (+ `__init__.py`).

## Verification commands
- Red check: `cd apps/gateway && uv run pytest tests/deployment_limits -o addopts="" -q`
- Regression: `cd apps/gateway && uv run pytest tests/routing_strategy tests/balance_strategies
  tests/model_fallbacks tests/routing_admin tests/proxy tests/deployment_model tests/rate_limits
  -o addopts="" -q`
- Lint (MATCH the gate scope): `cd apps/gateway && uv run ruff check . && uv run ruff format --check .`
- Typecheck: `make typecheck` (from repo root). Full gate: `make ci` (orchestrator runs authoritative).
- NOTE: `make ci` runs `ruff check .` over the WHOLE tree incl. tests/ — avoid ambiguous unicode
  (RUF001) like Greek α / ≈ inside string literals; use ASCII in assert messages.
