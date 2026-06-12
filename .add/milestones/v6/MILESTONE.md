# MILESTONE: LiteLLM parity slice 4 — routing & resilience

goal: a tenant's completion survives upstream failure — bounded retries with backoff, ordered model fallbacks, per-model cooldown circuit breaking, and admin-visible upstream health — without ever double-billing or corrupting the ledger
rationale: new-major — LiteLLM's Router is the largest remaining parity surface (standing goal "production grade to full main features of litellm"); v1–v5 built a single-shot proxy path; this slice makes it resilient. Scoped per Tin Dang's "Routing & resilience" selection (2026-06-12).
stage: production · status: active · created: 2026-06-12

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  upstream retry policy (bounded attempts + exponential backoff + jitter on
     connect errors / 5xx / 429, request timeout policy; NEVER retry once response
     streaming has begun or after request body bytes reached a non-idempotent
     upstream state — define the retryable set precisely at task spec);
     ordered model fallbacks (tenant-visible model-group alias → ordered candidate
     list; fall through on retryable exhaustion; ledger + usage record the ACTUAL
     model served and its pricing snapshot — billing follows reality, not the alias);
     per-model cooldown circuit breaker (consecutive-failure threshold → cooldown
     TTL in Redis, half-open probe; cooled-down candidates skipped by fallback);
     admin surface (GET upstream health/cooldown states; routing config read path;
     write path only if a task spec proves it safe — env/config-file first is fine);
     observability (gateway_* metrics for retries/fallbacks/cooldowns + span events).
Out: multiple upstream PROVIDERS (Hydroa speaks OpenRouter only — OpenRouter is
     itself a multi-provider router; direct OpenAI/Anthropic adapters are their own
     future intake); latency-/cost-based routing strategies (priority order first;
     a strategy seam may be left, unproven strategies are not built); passthrough
     endpoints (still carried); client-visible retry semantics changes (the caller
     sees ONE request; retries/fallbacks are server-side and invisible except via
     headers/metrics explicitly contracted).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Resilience NEVER weakens billing correctness: a retried/fallback request bills
  exactly once, for the model that actually answered, with that model's pricing
  snapshot; budget/rate-limit checks run against the SERVED model. The recorder/
  flusher path (typed-extras seam) is the only ledger write path.
- Streaming is the hard boundary: once the first upstream byte is forwarded to the
  client, no retry and no fallback — fail as v5 does today (502 passthrough
  semantics preserved). Pre-stream failures are the only resilience window.
- Cooldown state lives in Redis (tenant-agnostic, per upstream model id), TTL-based;
  circuit state transitions are observable (metric + span event). Fail-OPEN on
  Redis unavailability: no cooldown data ⇒ route normally (resilience must not
  become an outage amplifier).
- Model-group aliases are catalog-layer config; a request for a plain model id
  behaves exactly as v5 (aliases are additive; no existing wire behavior changes).
- Frozen-suite compatibility: the proxy use-case seams (checker, recorder,
  guardrails, cache) keep their contracts; resilience wraps the upstream call site.
- Every new env knob uses the GATEWAY_ prefix; defaults preserve v5 behavior
  (retries=0 / fallbacks-off until configured — additive rollout).
- Every new app.state/test seam ships with its paired production-wiring regression
  test (foundation v6 rule — folded from the v5 live defects).
- GLOSSARY gains: model_group, fallback_chain, retry_policy, cooldown (circuit
  half-open), upstream_health.

## Shared / risky contracts (freeze these first)
- retryable-failure classification + retry/timeout policy shape -> owning task retry-policy
- model-group alias config shape + served-model ledger semantics -> owning task model-fallbacks
- cooldown Redis key/TTL/threshold shape + half-open probe rule -> owning task cooldown-circuit
- admin health/routing read surface -> owning task routing-admin

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] retry-policy      depends-on: none            — bounded upstream retries + backoff/jitter + timeout policy; precise retryable set; single-bill invariant
- [ ] model-fallbacks   depends-on: retry-policy    — model-group aliases with ordered candidates; fall-through on retryable exhaustion; served-model billing
- [ ] cooldown-circuit  depends-on: retry-policy    — per-model consecutive-failure circuit breaker in Redis; half-open probe; fallback skips cooled candidates
- [ ] routing-admin     depends-on: model-fallbacks, cooldown-circuit — GET /admin/routing health+config surface; metrics/span events for retry/fallback/cooldown
- [ ] v6-live-verify    depends-on: all-of-above    — live close harness: fault-injecting upstream stub overlay + scripts/live_v6_verify.py (double-pass rule)

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A pre-stream upstream failure (connect error / 5xx / 429) is retried within the configured budget and the client receives a normal 200 with exactly ONE ledger row for the served model (← retry-policy)
- [ ] A request to a model-group alias whose first candidate is failing is served by the next candidate; the ledger row and usage raw carry the SERVED model id and its pricing snapshot (← model-fallbacks)
- [ ] A candidate exceeding the consecutive-failure threshold is cooled down (skipped by routing) and recovers via half-open probe after TTL; transitions visible in metrics (← cooldown-circuit)
- [ ] GET /admin/routing returns per-candidate health/cooldown state, tenant-authenticated, secrets-free (← routing-admin)
- [ ] Mid-stream failures keep v5 semantics exactly (no retry/fallback after first forwarded byte) — frozen streaming suites stay green (← retry-policy)
- [ ] All of the above proven LIVE through the TLS edge with the fault-injecting overlay, two consecutive clean passes (← v6-live-verify)
