# MILESTONE: Per-key bandwidth pacing

goal: Each API key's concurrent requests are paced to a configured tokens/sec ceiling via an aggregate Redis token-bucket with bounded-wait backpressure (pace, then 503 + Retry-After), default-OFF and fail-open.
rationale: new-major (intake-confirmed 2026-06-24). Net-new mechanism — a distributed
  token-bucket pacing layer on the stream path — not an extension of an existing frozen
  contract; too large for one task, has its own coherent goal. Closes the per-identity
  fairness gap v34's GLOBAL back-pressure guard explicitly left open (one noisy key can
  drain every global slot). Decisions confirmed via intake interview: throttle UNIT =
  tokens/sec (estimated, reconciled at close) · GRAIN = per API key (key_id) · SCOPE =
  aggregate across workers (Redis bucket) · OVER-LIMIT = queue with bounded wait → 503.

stage: production · status: active · created: 2026-06-24

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Distributed per-`key_id` token-bucket (Redis, atomic Lua refill+consume) — reuses the
    `rate_limits/` infra shape + the fail-open pattern (RedisLuaRateLimiter precedent).
  - Stream-path pacing: meter an ESTIMATED token count (chars/4-style) as SSE frames flow
    back; bucket empty → `await` refill up to a bounded wait budget → terminate with
    503 + Retry-After / terminal SSE error frame (composes with v35 error-fidelity + [DONE]).
  - Reconcile estimate → REAL `usage` frame at stream close, so estimation drift self-corrects
    in the bucket (no permanent over/under-charge).
  - Non-stream path charges the same bucket, so the per-key cap is honest across both paths.
  - Config: per-key `bandwidth_tokens_per_sec` (+ burst), bounded-wait budget; default-OFF
    globally AND per-key.
  - Observability readout (bucket level / paced-wait / shed counts), mirroring
    `ratelimit-counter-view`.
Out:
  - Per-tenant / per-end-user aggregation (key-level only this milestone).
  - Concurrency-COUNT caps (max simultaneous in-flight) — a distinct mechanism, not chosen.
  - Byte/sec pacing (tokens-estimated chosen over raw bytes).

## Shared decisions & glossary deltas   (living — every task must honor these)
- GLOSSARY delta: a **bandwidth bucket** is a per-`key_id` token-bucket whose tokens are
  ESTIMATED-then-reconciled LLM tokens (not RPM/TPM window entries) — distinct from the v8
  rate-limit ZSETs; new Redis keyspace `bandwidth:bucket:{key_id}`.
- Default-OFF + fail-open are INVARIANTS, not knobs: no limit configured ⇒ byte-identical to
  today; any Redis error ⇒ admit + warn (availability never gated on the bucket).
- Token ESTIMATE is the pacing currency mid-stream; the REAL usage frame is authoritative at
  close. Billing is UNTOUCHED — the bucket meters throughput, the ledger still bills exact.
- Bounded-wait is a hard timeout: pacing NEVER becomes an unbounded hang (designed-for-failure).

## Shared / risky contracts (freeze these first)
- BandwidthBucket port (acquire(estimate)/reconcile(real)/level) + Redis Lua refill+consume
  -> owning task `bandwidth-token-bucket`  (tasks 2–4 build against this frozen seam)
- Stream-pacing seam (where pacing wraps the outbound SSE generator; bounded-wait → terminal
  503/error-frame) -> owning task `stream-bandwidth-pacing`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] bandwidth-token-bucket    depends-on: none                    — Redis aggregate per-key token-bucket core (port + Lua refill/consume + fail-open) + config knobs
- [x] stream-bandwidth-pacing   depends-on: bandwidth-token-bucket  — apply the bucket on the outbound SSE path with bounded-wait + terminal 503/error frame; default-OFF byte-identical pin
- [x] bandwidth-usage-reconcile depends-on: stream-bandwidth-pacing — estimate→real-usage correction at close + non-stream admission charge
- [x] bandwidth-counter-view    depends-on: bandwidth-token-bucket  — observability readout (bucket level / paced / shed), mirroring ratelimit-counter-view

## Exit criteria (observable; map each to the task that delivers it)
- [x] A single key's aggregate stream throughput across N concurrent streams AND M workers stays within the ceiling (±burst)   (← stream-bandwidth-pacing, bandwidth-token-bucket) — bucket is a SHARED Redis keyspace `bandwidth:bucket:{key_id}` with atomic Lua refill+consume; every stream/worker debits the same key, so the aggregate is the ceiling. Proven by bandwidth-token-bucket's 14 real-Redis tests (refill clamp to burst, concurrent debit) + stream-bandwidth-pacing's per-chunk acquire.
- [x] Over-budget requests pace then get 503 + Retry-After after the bounded wait — never an unbounded hang   (← stream-bandwidth-pacing) — acquire() bounded-wait loop raises BandwidthExhaustedError at budget; non-stream → BANDWIDTH_EXHAUSTED.exc(503, Retry-After); mid-stream → terminal SSE error frame + [DONE]. Covered by stream-bandwidth-pacing shed tests.
- [x] Estimated pacing reconciles to the real usage frame at close — no permanent bucket drift   (← bandwidth-usage-reconcile) — _fire_bandwidth_reconcile applies signed delta (Σ estimate − real total_tokens) at clean close, disconnect (toward partial), and non-stream; net bucket consumption == real. 8 tests, refute-read UPHOLD.
- [x] Default-OFF ⇒ stream path byte-identical to today (zero pacing overhead)   (← stream-bandwidth-pacing) — PassthroughBandwidthBucket (no-op acquire/reconcile) when rate≤0; reconcile gated on `_bw_active`. Pinned by test_disabled_passthrough_no_reconcile + stream-pacing disabled byte-identical test.
- [x] Redis failure ⇒ fail-open (admit + warn), matching RedisLuaRateLimiter   (← bandwidth-token-bucket) — RedisTokenBucket.acquire admits + logs (key_id only) on any Redis error; reconcile/level swallow errors. Covered by bandwidth-token-bucket fail-open tests.
- [x] Bucket level + pacing/shed counters observable via an admin readout   (← bandwidth-counter-view) — GET /admin/bandwidth (owner/admin, tenant-scoped) returns refill-adjusted per-key level vs capacity; null on absent/Redis-down/disabled. 10 tests.

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/rate_limits : NEW BandwidthBucket port + BandwidthGrant/ConsumeResult (domain/ports.py) · BandwidthExhaustedError (domain/errors.py) · RedisTokenBucket (infrastructure/redis_token_bucket.py — atomic Lua refill+consume+reconcile, fail-open) · PassthroughBandwidthBucket (infrastructure/passthrough.py).
- gateway/proxy : CompletionUseCase (application/use_cases.py) — stream + non-stream pacing (acquire per chunk / pre-flight), terminal 503/SSE-error-frame on shed, `_fire_bandwidth_reconcile` estimate→real at clean close + disconnect + non-stream. api/deps.py wires bucket + max-wait. BANDWIDTH_EXHAUSTED ErrorSpec(503) in core/error_catalog.py.
- gateway/usage : NEW GET /admin/bandwidth (api/router.py) — owner/admin refill-adjusted per-key readout.
- gateway/core : 3 config knobs (config.py) bandwidth_tokens_per_sec / bandwidth_burst_tokens / bandwidth_max_wait_seconds (default-OFF, negatives coerced). main.py boots RedisTokenBucket(rate>0) / Passthrough onto app.state.
- tooling / skill / book : untouched. No DB migration (global knobs only; per-key column deferred).

### Cross-task evidence   (one row per task)
- bandwidth-token-bucket   : gate=PASS · tests=14 green (real Redis db7) · residue=none (refute-read UPHOLD, 1 MAJOR fixed)
- stream-bandwidth-pacing  : gate=PASS · tests=8 green · residue=none (refute-read caught + fixed a disconnect-during-shed double-bill via `_bw_shed_handled`)
- bandwidth-usage-reconcile: gate=PASS · tests=8 green · residue=none (refute-read UPHOLD, 1 MAJOR test-coverage gap fixed by pinning absolute estimates)
- bandwidth-counter-view   : gate=PASS · tests=10 green (real PG:5433+Redis) · residue=none (refute-read UPHOLD, 1 MINOR fixed)
- full gateway suite: **1576 passed**, 19 deselected (exit 0); ruff + pyright clean (1 pre-existing unrelated pyright error).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cited inline per criterion)
- goal: Each API key's concurrent requests are paced to a configured tokens/sec ceiling via an aggregate Redis token-bucket with bounded-wait backpressure, default-OFF and fail-open. PROOF: the shared `bandwidth:bucket:{key_id}` Redis keyspace + atomic Lua means every concurrent stream/worker debits one bucket (aggregate ceiling); bounded-wait → 503+Retry-After / SSE error frame; estimate reconciles to real at close; Passthrough ⇒ byte-identical; Redis error ⇒ admit+warn. 40 task tests + 1576-green full suite.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] BRANCH off main (`feat/v36-bandwidth-pacing`) — nothing committed yet; per CLAUDE.md commit/PR is HELD for Tin's go-ahead.
- [ ] Commit the 4 tasks (one commit each, per CLAUDE.md format) + the `chore(add)` fold/close bookkeeping.
- [ ] Open a PR from this Close ship-review; Tin reviews + merges (HTTPS gh push per [[git-push-https-gotcha]]).
- [ ] OPTIONAL before release: v36 live-verify pass (real Envoy + real key, pacing enabled) mirroring the v35 live-verify triad — currently a SPEC delta, not done.
- [ ] Bundle into the next release cut (status shows 3 milestones releasable since 0.2.0) — human-run per release.md.
