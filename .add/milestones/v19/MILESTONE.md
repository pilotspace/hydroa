# MILESTONE: Reliability — uniform retries, error-aware fallback, response & semantic caching

goal: a tenant's LLM request survives transient upstream failures and model outages — uniform error-aware retries across every provider, smarter model fallback, pre-first-byte streaming resilience, and response + semantic cache reuse — with billing accuracy preserved and byte-identical behavior at default settings
rationale: Intake → `new-major` (confirmed by Tin 2026-06-15). Reliability is a new product theme no active milestone's goal covered — the parity arc's resilience pillar. The gateway already grew reliability PRIMITIVES incrementally (v5 retry+jitter, v6 model-group fallback, v6 cooldown circuit breaker, v8 response cache) but they are UNEVENLY wired: retries are OpenRouter-only (Anthropic/Gemini have zero retry code — grep-confirmed), fallback triggers only on retry-exhaustion (not on context-window-overflow or content-policy blocks), and the cache is exact-match Redis only. v19 makes the primitives UNIFORM and ERROR-AWARE across every provider. AMENDMENTS at intake confirm (Tin 2026-06-15): (a) streaming resilience pulled IN as a flagged HIGH-RISK pre-first-byte task (NOT mid-stream — SSE can't replay); (b) a true embedding-similarity ("semantic") cache pulled IN as a flagged HIGH-RISK task. DEPTH on the existing gateway reliability seams; it adds NO new provider and NO dashboard surface.
stage: production · status: active · created: 2026-06-15

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  RETRIES — extract OpenRouter's retry/full-jitter/backoff into a shared helper; wire Anthropic +
     Gemini onto it; per-error-type RetryPolicy (retry 408/429/500/502/503/504 + connect/read timeouts;
     NEVER 4xx auth/validation/context-window); a cumulative retry deadline (bounded worst-case latency).
     FALLBACK — extend the existing non-streaming FallbackModelRouter so a fallback fires not only on
     exhausted retries but on specific NON-retryable, model-specific conditions another deployment can
     satisfy: context-window-exceeded and content-policy/safety blocks.
     STREAMING RESILIENCE (HIGH-RISK) — retry/fallback for streaming requests ONLY before the first SSE
     byte is emitted to the client (a pre-first-byte failure is replayable; once a chunk is sent the
     stream is committed — today's behavior holds).
     CACHING — per-request cache TTL override; an embedding-response cache (extends exact-match Redis to
     /v1/embeddings); and a true embedding-similarity SEMANTIC cache (HIGH-RISK — near-duplicate prompts
     hit above a configurable threshold), per-tenant isolated, billed on miss only.
     VERIFICATION — a cross-cutting live double-pass + retry/fallback/cache metric assertions + the full
     behavioral floor stays green.
Out: MID-stream retry/fallback (a failure AFTER the first SSE byte — SSE can't replay; permanently out,
     not just deferred); NEW providers or NEW gateway endpoints beyond what these features need; any
     dashboard surface for reliability config (a later UI milestone if wanted); an in-memory L1 / DualCache
     (Tin deferred at intake — coherence across replicas is its own milestone); image-response caching;
     per-tenant retry/fallback POLICY overrides via admin API (env/config-driven in v19); changing the
     4 routing strategies or the cooldown circuit-breaker mechanics (reused as-is); any billing-formula
     change (billing accuracy is a v12 invariant this milestone must PRESERVE, not alter).

## Shared decisions & glossary deltas   (living — every task must honor these)
- OPT-IN / DEFAULT-OFF is non-negotiable: at default settings every v19 feature is byte-identical to
  current behavior (the established foundation rule — GATEWAY_UPSTREAM_MAX_RETRIES default 0, cache off).
  Each new knob ships default-off with a documented byte-identical baseline.
- BILLING ACCURACY is sacrosanct (foundation v12): a retry or fallback bills ONLY the upstream attempt
  that actually produced the served response — never the discarded attempts, never double. A cache hit
  (exact OR semantic) bills $0 for upstream tokens and never re-bills; an embedding lookup done FOR the
  semantic cache is an internal cost, never billed to the tenant's served request.
- SECRET DISCIPLINE: provider API keys NEVER appear in retry/fallback/cache code paths' logs, metric
  labels, span attributes, exception messages, cache keys, or URLs. Cache keys are per-tenant-salted
  hashes of request content — never raw prompts in plaintext where a key could co-locate.
- STREAMING IS THE HARD BOUNDARY: resilience applies ONLY pre-first-byte. The instant the first SSE
  chunk reaches the client the stream is committed — no retry, no fallback, no replay. This line is the
  riskiest contract (owned by streaming-resilience) and every reviewer checks it.
- TENANT ISOLATION: a cache hit (exact or semantic) NEVER crosses tenants; the per-tenant namespace that
  v8 established is preserved verbatim and re-verified for the new embedding + semantic paths.
- DESIGN-FOR-FAILURE (CLAUDE.md): every new IO path declares timeout, bounded retry, and a fail-safe
  default (cache/embedding-lookup failure degrades to a cache MISS — it never fails the user's request).

## Shared / risky contracts (freeze these first)
- The RETRY SEAM — the shared helper signature + the RetryPolicy error-classification (retryable vs.
  terminal) + the cumulative-deadline semantics. Every provider upstream consumes it. -> owning task retry-seam-unify
- The FALLBACK-TRIGGER TAXONOMY — the closed set of conditions that trigger a model fallback
  (retry-exhausted | context-window-exceeded | content-policy-blocked) vs. hard-fail. -> owning task error-aware-fallback
- The PRE-FIRST-BYTE COMMIT POINT — the precise boundary in the streaming path before which a retry/
  fallback is permitted and after which the stream is irrevocably committed. -> owning task streaming-resilience
- The SEMANTIC-CACHE KEY+THRESHOLD contract — how a prompt maps to an embedding, the similarity metric +
  configurable hit threshold, and the per-tenant namespace. -> owning task semantic-cache

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] retry-seam-unify       depends-on: none                          — extract retry/jitter helper, wire Anthropic+Gemini, per-error RetryPolicy + cumulative deadline
- [x] error-aware-fallback   depends-on: retry-seam-unify              — FallbackModelRouter fails over on context-window-exceeded & content-policy block (non-streaming)
- [ ] streaming-resilience   depends-on: retry-seam-unify, error-aware-fallback  — HIGH-RISK: pre-first-byte streaming retry/fallback (no mid-stream replay)
- [ ] cache-controls         depends-on: none                          — per-request cache TTL override + embedding-response cache (per-tenant, billed on miss only)
- [ ] semantic-cache         depends-on: cache-controls                — HIGH-RISK: embedding-similarity cache (near-duplicate prompts hit above configurable threshold)
- [ ] reliability-verify     depends-on: retry-seam-unify, error-aware-fallback, streaming-resilience, cache-controls, semantic-cache  — live double-pass ×2 + metric assertions + zero-regression floor

## Exit criteria (observable; map each to the task that delivers it)
- [x] A transient 503/429/timeout from Anthropic OR Gemini (not just OpenRouter) is transparently retried, while a 400/401/422 is NOT retried, and retries stop at a cumulative deadline; at default settings behavior is byte-identical to today (← retry-seam-unify) (verify: per-provider retry tests + default-off byte-identical test) ✓ DONE 2026-06-15 — 62 retry tests green, 97% module cov, refute-read EARNED
- [x] A request that exceeds a model's context window OR is content-policy-blocked fails over to the next deployment in its model-group instead of hard-erroring (← error-aware-fallback) (verify: context-window + content-policy fallback tests) ✓ DONE 2026-06-15 — 32 tests green (classifier 100% cov), refute-read EARNED-WITH-GAPS 0.87 → 2 gaps fixed in-loop (retry-domain 408/429 excluded, patterns narrowed); opt-in default-off byte-identical
- [ ] A streaming request that fails BEFORE the first byte retries/falls-over transparently; a failure AFTER the first byte keeps today's behavior (no replay) (← streaming-resilience) (verify: pre-first-byte success test + post-first-byte commit test)
- [ ] A repeated identical embeddings request serves from cache (per-tenant isolated, billed only on miss), and a caller can override the cache TTL per request (← cache-controls) (verify: embedding-cache hit/miss + per-request-TTL tests)
- [ ] A near-duplicate prompt (semantic similarity above the configured threshold) serves a SEMANTIC cache hit — per-tenant isolated, default-off, threshold-configurable — and a below-threshold prompt misses (← semantic-cache) (verify: above/below-threshold tests + tenant-isolation test)
- [ ] Live double-pass green ×2, retry/fallback/cache Prometheus counters increment as expected, and zero behavioral regression on the committed floor (← reliability-verify) (verify: the §6 evidence block + live double-pass log)
