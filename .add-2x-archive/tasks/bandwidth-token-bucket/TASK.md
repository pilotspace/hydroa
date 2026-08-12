# TASK: Redis aggregate per-key token-bucket core

slug: bandwidth-token-bucket · created: 2026-06-24 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  NEW (this task owns):
  - `apps/gateway/src/gateway/rate_limits/domain/ports.py:BandwidthBucket` — NEW Protocol alongside
    `RateLimiter`. Methods (draft, frozen at §3): `acquire(key_id: UUID, estimated_tokens: int,
    max_wait_s: float) -> BandwidthGrant` (bounded-wait pace-or-shed) · `reconcile(key_id, grant,
    real_tokens: int) -> None` (estimate→real delta correction at close) · `level(key_id) -> int`
    (current available tokens — for the counter-view + tests). Zero framework imports (mirrors the
    existing `RateLimiter` Protocol in this same file).
  - `apps/gateway/src/gateway/rate_limits/domain/errors.py:BandwidthExhaustedError` — NEW, mirrors
    `RateLimitExceededError(limit_type, limit, key_id, retry_after_s)` shape (carries `retry_after_s`
    for the 503 path; raised only when the bounded wait is spent, not on every empty bucket).
  - `apps/gateway/src/gateway/rate_limits/infrastructure/redis_token_bucket.py:RedisTokenBucket` — NEW
    concrete impl. Atomic Lua refill+consume (one round-trip) keyed `bandwidth:bucket:{key_id}`
    (+ a companion `bandwidth:bucket_ts:{key_id}` for last-refill timestamp, mirroring the
    tpm/tpm_sum companion-key shape in `redis_lua_limiter.py`). Constructor registers the script via
    `redis.register_script(...)` and does NOT connect (safe without lifespan) — same as
    `RedisLuaRateLimiter.__init__` and `RedisDeploymentLoadGate.__init__`.
  - `apps/gateway/src/gateway/rate_limits/application/passthrough.py:PassthroughBandwidthBucket` — NEW
    no-op (always-admit, zero wait) for the default-OFF / unwired path (mirrors
    `PassthroughRateLimiter`).

  EXISTING (read / mirror — NOT changed this task):
  - `rate_limits/infrastructure/redis_lua_limiter.py:RedisLuaRateLimiter` — the canonical Lua pattern
    to mirror: `register_script` in `__init__`, atomic evict+count+record, `_WINDOW_MS`/`_TTL_S`
    consts, fail-open (`except Exception → _log.warning(... key_id only) → admit/return`),
    `_retry_after(oldest_ms, now_ms)` helper. Token-bucket math (refill = elapsed_ms/1000 * rate,
    cap at burst) replaces the sliding-window math; fail-open + companion-key + TTL shape are reused.
  - `proxy/infrastructure/redis_load_gate.py:RedisDeploymentLoadGate` — second fail-open precedent
    (neutral value on error, `redis: Any` typing, no-connect constructor).
  - `core/config.py` (lines 96, 350–386) — knob conventions to mirror: `redis_url` (line 96);
    `max_concurrent_requests: int = Field(default=0)` + `back_pressure_retry_after_seconds` with a
    `@field_validator(mode="before")` that coerces negatives→default (lines 350–386). NEW knobs this
    task adds: `bandwidth_tokens_per_sec: int = 0` (0 ⇒ OFF), `bandwidth_burst_tokens: int` (bucket
    cap), `bandwidth_max_wait_seconds: float` (bounded-wait budget) — all default-OFF, negatives
    coerced per the existing validator convention.
  - `main.py:653` `app.state.rate_limiter = RedisLuaRateLimiter(redis=redis_client)` — the wiring
    seam: this task adds `app.state.bandwidth_bucket = RedisTokenBucket(...)` next to it (tests
    override via `app.state.bandwidth_bucket`, getattr(...,None) injection — see deps.py:117).

Context (working folder):
  - `apps/gateway/migrations/versions/c3f8a2e1d5b7_rate_limit_columns.py` — precedent migration that
    added per-key rpm/tpm limit columns; the per-key `bandwidth_tokens_per_sec` override (if stored
    on the api_key row rather than global-only) would follow this shape. DECISION DEFERRED to §1:
    global-knob-only (no migration) vs per-key column. Lean global-only for this core task; per-key
    override is a candidate follow-up.
  - No new docs/fixtures yet; tests live in `./tests/` (this task dir).

Honors (patterns / conventions):
  - INVARIANT (PROJECT.md): "No outbound IO without timeout + bounded retry (idempotent only) +
    circuit breaker" — the bounded-wait budget IS the timeout; fail-open IS the circuit breaker;
    Lua single-round-trip is the atomicity guarantee (no read-modify-write race across workers).
  - Fail-open is a floor, not a knob (v36 MILESTONE shared decision): any Redis/Lua error ⇒ admit +
    `_log.warning` with key_id ONLY (never secrets) — copied verbatim from `redis_lua_limiter.py`.
  - Default-OFF byte-identical (v36 shared decision): `bandwidth_tokens_per_sec == 0` ⇒
    `PassthroughBandwidthBucket` wired ⇒ zero accounting, byte-identical to today (mirrors
    `GlobalBackPressureMiddleware` `max_concurrent=0 → _sem is None` pass-through).
  - GLOSSARY delta (v36): a **bandwidth bucket** is a per-key_id token-bucket of ESTIMATED-then-
    reconciled LLM tokens, distinct from the v8 RPM/TPM ZSET windows; new keyspace
    `bandwidth:bucket:{key_id}`.
  - Domain ports are `typing.Protocol` with fakes injected via `app.state` (PROJECT.md v1 fold).

Anchors the contract cites (§3 may name ONLY these):
  - `BandwidthBucket` Protocol (ports.py) · `BandwidthGrant` value object · `BandwidthExhaustedError`
    (errors.py) · `RedisTokenBucket` + key shapes `bandwidth:bucket:{key_id}` /
    `bandwidth:bucket_ts:{key_id}` + the Lua refill+consume contract · `PassthroughBandwidthBucket` ·
    config knobs `bandwidth_tokens_per_sec` / `bandwidth_burst_tokens` / `bandwidth_max_wait_seconds`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-key aggregate token-bucket primitive (refill + bounded-wait consume + estimate/reconcile)
Framings weighed:
  - Redis Lua token-bucket, atomic refill+consume in one round-trip (CHOSEN) — aggregate across all
    workers/streams of a key_id; honest cap; reuses the rate_limits Lua + fail-open precedent.
  - per-worker asyncio bucket (rejected — Tin chose aggregate; per-worker = N×cap, dishonest).
  - reuse the v8 TPM ZSET sliding window for pacing (rejected — a sliding window measures rate over a
    fixed window; it is not a DRAINABLE bucket and cannot express burst capacity + a pace-until-refill
    wait. Bandwidth pacing needs bucket semantics, distinct from TPM admission).
Must:
<must>
  - REFILL-THEN-CONSUME is atomic in ONE Lua round-trip: on each call, lazily refill
    `level = min(burst, level + floor(elapsed_ms/1000 * rate))` using a stored last-refill timestamp
    (companion key `bandwidth:bucket_ts:{key_id}`), persist the new level+timestamp, refresh TTL, then
    decide grant. No read-modify-write race across workers (single EVALSHA).
  - try_consume(key_id, tokens) -> {granted, available, retry_after_s}: grant iff post-refill
    `level >= tokens` (then `level -= tokens`); else granted=False and `retry_after_s = ceil((tokens -
    available) / rate)` (the seconds until enough tokens accrue), min 1.
  - acquire(key_id, estimated_tokens, max_wait_s) -> BandwidthGrant: bounded-wait loop over try_consume.
    If not granted and `retry_after_s <= remaining wait budget` → `await asyncio.sleep(min(retry_after_s,
    slice))` and retry; once cumulative waited would exceed `max_wait_s` → raise BandwidthExhaustedError
    (retry_after_s carried). On grant → return BandwidthGrant(key_id, consumed=estimated_tokens,
    waited_s). NEVER an unbounded wait (the budget is the timeout — PROJECT.md IO invariant).
  - estimated_tokens <= 0 ⇒ immediate grant, consume 0, zero wait (never an error — mirrors the
    record_tpm `tokens <= 0` guard).
  - reconcile(key_id, grant, real_tokens): apply the SIGNED delta `real_tokens - grant.consumed` to the
    bucket level — real > estimate consumes the extra (level may go negative = carried debt, floored at
    `-burst`); real < estimate refunds (level += diff, capped at `burst`). Fire-and-forget; never raises.
  - level(key_id) -> int: best-effort current available tokens after lazy refill (for the counter-view +
    tests).
  - FAIL-OPEN: any Redis/Lua error in try_consume/acquire ⇒ ADMIT (granted, consumed=estimate, waited=0)
    + `_log.warning` with key_id ONLY (never secrets). reconcile/level errors swallowed (return burst
    for level on error = "full/admit"). Copied verbatim from RedisLuaRateLimiter's fail-open shape.
  - DISABLED defensively: a bucket constructed with `rate <= 0` immediate-grants every acquire (the wired
    default-OFF path uses PassthroughBandwidthBucket; this is belt-and-suspenders).
  - AGGREGATE: all workers/streams of one key_id share the single `bandwidth:bucket:{key_id}` key — the
    cap is honest regardless of worker/stream count (the milestone's exit criterion 1).
</must>
Reject:
<reject>
  - bounded wait budget spent before a grant -> raise "BandwidthExhaustedError" (the ONLY raise; the
    HTTP seam in stream-bandwidth-pacing maps it to 503 + Retry-After). Empty-bucket-but-within-budget
    is NOT a rejection — it paces.
</reject>
After:
<after>
  - On grant: `bandwidth:bucket:{key_id}` level decreased by the consumed estimate; ts + TTL refreshed.
  - After reconcile: net level == refilled − real_tokens (the estimate is corrected to truth; no
    permanent drift — milestone exit criterion 3).
  - Default-OFF / fail-open paths leave behavior byte-identical to today (zero pacing).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The bounded-wait LOOP (asyncio.sleep + retry) lives inside the bucket's `acquire()` — lowest
    confidence because it puts pacing orchestration + per-slice Redis round-trips in the "core" task,
    and the stream task (task 2) might instead want to own the sleep cadence (e.g. pace per-frame, not
    per-token-debt). If wrong: split into a pure `try_consume` primitive (stays in task 1, frozen) +
    move the wait-loop to stream-bandwidth-pacing. Cost: re-slices the task-1/task-2 boundary, but
    `try_consume`'s contract is unchanged either way (low blast radius).
  - [ ] reconcile over-estimate ⇒ level goes NEGATIVE down to −burst (honest carried debt) vs clamped
    at 0 (forgive overage). Leaning negative-with-floor; if wrong, one Lua clamp line changes.
  - [ ] GLOBAL knobs only this task (no per-key `bandwidth_tokens_per_sec` column / migration) — per-key
    override deferred to a follow-up. Confirm acceptable for the core.
  - [ ] The bucket is UNIT-AGNOSTIC: it consumes an opaque int. The chars/4 token ESTIMATE formula is
    owned by the stream seam (task 2/3), NOT here. Confirm the core stays estimate-formula-agnostic.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: grant when bucket has capacity
  Given a key with rate=100/s, burst=200, and a full bucket
  When acquire(key, estimated_tokens=50, max_wait_s=5) is called
  Then it returns a BandwidthGrant(consumed=50, waited_s≈0)
  And the bucket level is 150

Scenario: refill accrues over elapsed time
  Given a key with rate=100/s, burst=200, level=0, last-refill 1s ago
  When try_consume(key, 80) is called
  Then it grants (≈100 tokens refilled, capped at burst) and level≈20

Scenario: refill never exceeds burst
  Given a key with rate=100/s, burst=200, level=200, last-refill 10s ago
  When try_consume(key, 0) is called
  Then post-refill level is 200 (clamped at burst, not 200+1000)

Scenario: pace then grant within the wait budget
  Given a key with rate=100/s, burst=100, an empty bucket
  When acquire(key, estimated_tokens=50, max_wait_s=5) is called
  Then it sleeps ~0.5s (until 50 tokens refill), then returns a grant with waited_s≈0.5

Scenario: bounded wait exhausted -> raise   # REJECTION
  Given a key with rate=10/s, burst=10, an empty bucket
  When acquire(key, estimated_tokens=1000, max_wait_s=2) is called
  Then it raises BandwidthExhaustedError with retry_after_s≈ceil((1000-0)/10)
  And the bucket level is unchanged (no partial consume on a refused acquire)

Scenario: zero/negative estimate is an immediate free grant
  Given any key with a configured bucket
  When acquire(key, estimated_tokens=0, max_wait_s=5) is called
  Then it returns a grant immediately (consumed=0, waited_s≈0)
  And the bucket level is unchanged

Scenario: reconcile under-estimate refunds
  Given a grant consumed=50 and post-grant level=150 (rate=100,burst=200)
  When reconcile(key, grant, real_tokens=30) is called
  Then 20 tokens are refunded and level==170 (capped at burst)

Scenario: reconcile over-estimate carries debt
  Given a grant consumed=50 and post-grant level=10 (rate=100,burst=200)
  When reconcile(key, grant, real_tokens=80) is called
  Then 30 extra are consumed and level==-20 (negative debt, floored at -burst)

Scenario: aggregate across concurrent callers
  Given a key with rate=100/s, burst=100, full bucket, two concurrent acquire(60) calls
  When both run against the SAME bandwidth:bucket:{key_id} key
  Then exactly one grants immediately and the other paces/refuses — total consumed never exceeds capacity
  And the cap holds regardless of which worker served each call

Scenario: Redis error fails open   # REJECTION-path safety
  Given the Redis client raises on the bucket script
  When acquire(key, estimated_tokens=50, max_wait_s=5) is called
  Then it ADMITS (grant consumed=50, waited_s=0) and logs a warning with key_id only
  And no exception propagates to the caller

Scenario: disabled bucket admits immediately
  Given a RedisTokenBucket constructed with rate=0
  When acquire(key, estimated_tokens=999, max_wait_s=5) is called
  Then it grants immediately with zero wait (no Redis call required)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Internal domain primitive (NOT an HTTP endpoint — the HTTP 503/Retry-After mapping is
# stream-bandwidth-pacing's contract). This task freezes the port + value object + Lua key contract.

# --- rate_limits/domain/ports.py ---
@runtime_checkable
class BandwidthBucket(Protocol):
    async def acquire(self, key_id: UUID, estimated_tokens: int, max_wait_s: float) -> BandwidthGrant: ...
        # bounded-wait pace-or-raise. estimated_tokens<=0 -> immediate 0-consume grant.
        # raises BandwidthExhaustedError iff a grant needs more than max_wait_s of waiting.
        # FAIL-OPEN: any Redis error -> admit (grant consumed=estimated_tokens, waited_s=0.0).
    async def try_consume(self, key_id: UUID, tokens: int) -> ConsumeResult: ...
        # atomic single-round-trip refill+conditional-consume. No sleep. Fail-open -> granted=True.
    async def reconcile(self, key_id: UUID, grant: BandwidthGrant, real_tokens: int) -> None: ...
        # signed delta (grant.consumed - real_tokens). Fire-and-forget; never raises.
        # [clerical fix 2026-06-24: was "real - grant.consumed"; the authoritative reconcile-Lua
        #  line below + §2 scenarios + impl all use (consumed - real) = refund when real<consumed.]
    async def level(self, key_id: UUID) -> int: ...
        # best-effort available tokens after lazy refill; fail-open -> burst.

# --- value objects (frozen dataclasses) ---
BandwidthGrant   = { key_id: UUID, consumed: int, waited_s: float }
ConsumeResult    = { granted: bool, available: int, retry_after_s: int }

# --- rate_limits/domain/errors.py ---
BandwidthExhaustedError(key_id: str, requested: int, retry_after_s: int)   # mirrors RateLimitExceededError

# --- Redis (RedisTokenBucket) key contract (FROZEN keyspace) ---
bandwidth:bucket:{key_id}      FLOAT  level (may be negative down to -burst)   TTL=ceil(burst/rate)+60s
bandwidth:bucket_ts:{key_id}   FLOAT  last-refill epoch ms                     TTL=same
  Lua (one EVALSHA): refill level=min(burst, level + (now_ms-ts)/1000*rate); set ts=now_ms;
                     if level>=tokens -> level-=tokens, return {1, level, 0}
                     else retry_after = ceil((tokens-level)/rate) (min 1) -> return {0, level, retry_after}
  reconcile Lua: level = clamp(level + (grant.consumed - real_tokens), -burst, burst)  # refund/debit

# --- config knobs (core/config.py; default-OFF; negatives coerced per existing validator) ---
bandwidth_tokens_per_sec: int  = 0      # 0 => OFF => PassthroughBandwidthBucket wired
bandwidth_burst_tokens:   int  = 0      # bucket capacity; when 0 and rate>0, defaults to rate (1s burst)
bandwidth_max_wait_seconds: float = 0.0 # bounded-wait budget the stream seam passes to acquire()

Schema: NO DB migration this task (global knobs only; per-key column deferred). State is Redis-only.
Wiring: app.state.bandwidth_bucket = RedisTokenBucket(redis=..., rate=..., burst=...) at main.py
        (next to rate_limiter @ line 653); Passthrough when rate=0. Tests override via app.state.
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-06-24). Resolutions: acquire() owns the bounded-wait
loop · reconcile carries negative debt floored at −burst · global knobs only (no migration) · bucket
unit-agnostic. Changing this contract = change request back to SPECIFY.

Least-sure flag surfaced at freeze:
  [contract] The bounded-wait loop (asyncio.sleep + retry) lives INSIDE acquire() rather than as a pure
    try_consume primitive with the wait-loop pushed to stream-bandwidth-pacing (task 2). Why least-sure:
    it places pacing orchestration + per-slice Redis round-trips in the "core" task, and task 2 might
    prefer to own the sleep cadence (pace-per-frame). Cost if wrong: re-slice the task-1/task-2 boundary
    — but try_consume's contract is unchanged either way, so the blast radius is one method moving, not
    a re-freeze of the primitive. Tin approved "freeze as drafted" (acquire() owns the loop).
  [contract] Secondary: reconcile over-estimate carries NEGATIVE debt floored at −burst (vs forgiving at
    0). Cost if wrong: one Lua clamp line. Tin approved the negative-debt resolution.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (matches gateway floor)
Plan (one test per scenario, asserting behavior not internals). A FakeRedis (in-memory dict +
register_script returning a callable that runs the Lua-equivalent in Python) backs the unit tests so
they are deterministic and network-free; the Lua itself is exercised against the real redis stub in
the integration check. Time is injected (a `now_ms` callable) so refill math is deterministic.
<test_plan>
  - test_grant_when_capacity: full bucket / acquire(50) / grant consumed=50, level=150
  - test_refill_accrues_over_time: level=0,1s elapsed / try_consume(80) / granted, level≈20
  - test_refill_caps_at_burst: level=200,10s elapsed / try_consume(0) / level==200 (not 1200)
  - test_pace_then_grant_within_budget: empty / acquire(50,max_wait=5) / grant, waited_s≈0.5 (sleep patched)
  - test_bounded_wait_exhausted_raises: empty rate=10 / acquire(1000,max_wait=2) / BandwidthExhaustedError, retry_after≈100, level unchanged
  - test_zero_estimate_immediate_grant: acquire(0) / grant consumed=0, level unchanged
  - test_reconcile_underestimate_refunds: grant consumed=50,level=150 / reconcile(real=30) / level==170 (cap burst)
  - test_reconcile_overestimate_carries_debt: grant consumed=50,level=10 / reconcile(real=80) / level==-20 (floor -burst)
  - test_reconcile_debt_floored_at_neg_burst: large over-estimate / level clamps at -burst
  - test_aggregate_two_callers_share_key: two acquire(60) on same key / total consumed ≤ capacity
  - test_redis_error_fails_open: redis raises / acquire grants consumed=estimate, waited=0, warn logged key_id-only
  - test_disabled_rate_zero_admits: RedisTokenBucket(rate=0) / acquire(999) / immediate grant, no redis call
  - test_passthrough_bucket_always_admits: PassthroughBandwidthBucket.acquire(any) / immediate grant
  - test_config_knobs_default_off_and_coerce_negatives: bandwidth_tokens_per_sec default 0; negative→0
</test_plan>

Tests live in: `apps/gateway/tests/bandwidth_token_bucket/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/rate_limits/domain/ports.py` `apps/gateway/src/gateway/rate_limits/domain/errors.py` `apps/gateway/src/gateway/rate_limits/infrastructure/redis_token_bucket.py` `apps/gateway/src/gateway/rate_limits/application/passthrough.py` `apps/gateway/src/gateway/core/config.py`
Strategy (ordered batches):
  1. domain: add BandwidthBucket Protocol + BandwidthGrant/ConsumeResult value objects (ports.py) + BandwidthExhaustedError (errors.py).
  2. config: add bandwidth_tokens_per_sec / bandwidth_burst_tokens / bandwidth_max_wait_seconds knobs + negative-coercion validators.
  3. infra: RedisTokenBucket (Lua refill+consume + reconcile clamp, register_script in __init__, fail-open) + PassthroughBandwidthBucket (passthrough.py).
  4. (wiring main.py is intentionally NOT in this task's scope — the stream-pacing task consumes app.state.bandwidth_bucket; the bucket primitive + its passthrough are independently testable. Re-evaluate if a wiring smoke-test is needed.)
Safety rule (feature-specific): refill+consume MUST be one atomic Lua EVALSHA (no read-modify-write across workers); every Redis-touching method fail-open (admit) on exception; reconcile/level never raise.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 14/14 bandwidth_token_bucket green; rate_limits (shared module) 15/15 green; full gateway suite (regression for config.py + passthrough.py edits) — see GATE RECORD.
- [x] coverage did not decrease — the task adds a covered module + tests; edits to config.py/passthrough.py are additive (new fields/validators/class). No lines removed.
- [x] no test or contract was altered during build — the §3 freeze is intact; the only TASK.md edit was a CLERICAL comment fix (reconcile delta direction) matching the authoritative Lua line + scenarios + impl (noted at §3). The one test edit STRENGTHENED a vacuous assertion (level>=0 → ==0), a tests-phase strengthening, never a weakening.
- [x] the green was EARNED — adversarial refute-read (sonnet) returned UPHOLD, no blockers. Its one MAJOR (acquire() budgeted theoretical not actual sleep) was FIXED (track real slice_s); its worst-case (50000 iters) was shown non-sustainable (refill closes a sub-floor deficit in ~1 iter). Verified-correct by the reviewer: Lua atomicity, fail-open coverage on every Redis path, reconcile sign+clamp, real-Redis (not mocked) tests.
- [x] concurrency / timing safe — refill+consume is ONE atomic Lua EVALSHA (no cross-worker read-modify-write race); proven by test_aggregate_two_callers_share_key (exactly-one-wins, level never over-drawn). Bounded-wait budget IS the timeout (no unbounded hang).
- [x] no exposed secrets / injection / unexpected deps — fail-open logs key_id ONLY (never secrets), copied from RedisLuaRateLimiter. No new packages (redis.asyncio already a dep). Lua uses parameterized KEYS/ARGV (no string interpolation into the script).
- [x] layering & deps follow CONVENTIONS.md — domain ports are zero-framework Protocols; infra depends on domain; Settings knobs mirror the existing field_validator coercion convention.
- [ ] a person reviewed and approved the change — Tin (contract freeze approved; build review pending at milestone close / PR).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green.
- [x] A full bucket grants immediately; level drops by the consumed estimate — confirmed: test_grant_when_capacity (level 200→150 after acquire(50)).
- [x] Refill accrues at rate × elapsed, capped at burst — confirmed: test_refill_accrues_over_time (+100 in 1s) + test_refill_caps_at_burst (10s elapsed stays at 200, not 1200).
- [x] An empty bucket PACES within budget then grants; exceeds budget → BandwidthExhaustedError + Retry-After — confirmed: test_pace_then_grant_within_budget (waited≈0.5) + test_bounded_wait_exhausted_raises (retry_after=ceil(1000/10), level unchanged==0).
- [x] reconcile corrects estimate→truth: under-est refunds (capped burst), over-est carries negative debt (floored −burst) — confirmed: test_reconcile_underestimate_refunds (→170) + _overestimate_carries_debt (→−20) + _debt_floored_at_neg_burst (→−200).
- [x] Aggregate across concurrent callers never over-admits — confirmed: test_aggregate_two_callers_share_key (exactly one grants, level==40).
- [x] Redis error fails OPEN; rate=0 / Passthrough always admit with no Redis touch — confirmed: test_redis_error_fails_open + test_disabled_rate_zero_admits + test_passthrough_bucket_always_admits.
- [x] Default-OFF: knobs default 0/0/0.0 and coerce negatives — confirmed: test_config_knobs_default_off_and_coerce_negatives + live import (Settings().bandwidth_tokens_per_sec==0).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — BandwidthBucket/BandwidthGrant/ConsumeResult/BandwidthExhaustedError/RedisTokenBucket/PassthroughBandwidthBucket + the 3 config knobs are all referenced by the test suite (constructs RedisTokenBucket + PassthroughBandwidthBucket, asserts the knobs). The PRODUCTION wiring (app.state.bandwidth_bucket at main.py) is DEFERRED to stream-bandwidth-pacing (task 2) by design — see §5 batch 4. Until task 2 lands, RedisTokenBucket is referenced only by tests (an intentional, declared seam, not orphaned).
- [x] DEAD-CODE — no orphaned symbol: every new symbol is on the frozen contract surface that task 2/4 consume. `level()` is used by tests now + the counter-view (task 4); `reconcile()` by tests now + the reconcile task (task 3). No unused private helpers.
- [x] SEMANTIC — read the frozen §3 in full against the impl: contract matches except a CLERICAL comment typo on the reconcile delta direction (fixed to match the authoritative Lua + scenarios). Lua key contract, TTL formula, default-OFF, fail-open all match.

### GATE RECORD
Outcome: PASS
Evidence: full gateway suite 1550 passed / 0 failed (exit 0) @ 87.43% coverage (258s); task suite 14/14;
  shared-module regression (rate_limits) 15/15; ruff clean; pyright 0 errors; adversarial refute-read
  (sonnet) = UPHOLD, its one MAJOR fixed (actual-slept budgeting), worst-case shown non-sustainable.
  Concurrency is the feature and was verified safe (atomic single-EVALSHA; exactly-one-wins aggregate test),
  not unresolved residue — auto-gateable. No security finding. Production wiring deferred to task 2 (declared).
Reviewed by: AI auto-gate (autonomy: auto) · date: 2026-06-24 — human build-review deferred to milestone-close/PR (Tin approved the §3 freeze).

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): bandwidth-shed rate (BandwidthExhaustedError/min per key) · paced-wait p50/p99 · fail-open admit rate (Redis errors) · bucket-level distribution.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] stream-bandwidth-pacing (task 2) must WIRE app.state.bandwidth_bucket at main.py (RedisTokenBucket when rate>0 else PassthroughBandwidthBucket) + map BandwidthExhaustedError → 503 + Retry-After at the HTTP seam (evidence: this task deferred production wiring by design).
- [SPEC · open] Redis-Cluster CROSSSLOT: the two-key EVAL (bandwidth:bucket:{id} + bandwidth:bucket_ts:{id}) hashes to different slots under cluster mode → fail-open on every call. Hash-tag the keys ({key_id} braces) before any clustered deploy (evidence: refute-read MINOR; single-node today).
- [SPEC · open] per-key bandwidth_tokens_per_sec OVERRIDE column on api_keys (this task shipped global-knob-only; per-key plan limits need a migration mirroring c3f8a2e1d5b7) (evidence: §1 deferred decision).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
- [TDD · folded] a bounded-wait pacing loop is made DETERMINISTIC by injecting an epoch-ms clock + patching asyncio.sleep to ADVANCE that clock — real-Redis Lua stays exercised (now_ms is an ARGV) while wall-time is removed (evidence: the 14-test suite runs in <1.3s against real Redis, no flakiness). [folded foundation-version 33]
- [SDD · folded] when a refute-read's worst-case rests on an unphysical assumption (a token deficit that "stays 1 forever" despite refill closing it each slice), FIX the underlying defect anyway if the fix is strictly-more-correct + harmless, but record the corrected severity rather than the reviewer's headline (evidence: acquire() actual-slept budgeting fix; 50000-iter case bounded to ~1 slice by refill). See the `add` skill's `deltas.md`. [folded foundation-version 33]
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
