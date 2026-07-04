# TASK: Batch observability scaffolding: window-latency + placeholder completion metrics

slug: batch-observability-scaffolding · created: 2026-07-04 · stage: production
milestone: (none)
autonomy: auto
phase: done
fast: true

> Fast lane — one small task, minimal sections, filled top-to-bottom. The trust floor still
> holds: a FROZEN §3 contract · ≥1 red test before build · a recorded §6 gate (security = HARD-STOP).
> The acceptance scenario collapses into §1 `Accept:`; OBSERVE is one optional line at the gate.

---

## 0 · GROUND — the real codebase

Touches (files · symbols):
  - `apps/gateway/src/gateway/proxy/infrastructure/batch_window_buffer.py:_CLAIM_DUE_LUA` — Lua
    script already computes `local elapsed = now - tonumber(started)` (used today only for the
    `due` check). Add two ADDITIVE Redis writes using that already-computed value — zero change
    to the script's `claimed_items` return shape (frozen contract from batch-window-grouping,
    extensively asserted on by `tests/batches/test_batch_window_grouping.py`, ~13 call sites).
  - `apps/gateway/src/gateway/batches/api/stats_router.py:BatchStatsResponse,get_batch_stats` —
    add 3 new optional fields; add a Redis read for the new window-wait aggregate, mirroring the
    fail-open pattern below.
  - `apps/gateway/src/gateway/usage/api/router.py:_read_ratelimit_counters,get_ratelimits` —
    REFERENCE pattern only, not modified: fail-open Redis read (catches `RedisError, OSError,
    ValueError, TimeoutError` → empty/null, never 0/500), bounded by
    `asyncio.timeout(_RATELIMIT_REDIS_TIMEOUT_SECONDS)`, client obtained via
    `getattr(request.app.state, "redis_client", None)`.
  - `apps/dashboard/lib/batches.ts:BatchStatsData,getBatchStats` — add the 3 new optional fields
    to the TS type.
  - `apps/dashboard/components/batches/BatchesStatsPage.tsx:BatchesStatsPage` — add 3 new
    `StatCard`s (reusing `apps/dashboard/components/ui/stat-card.tsx:StatCard`, which already
    supports a `footer` caption — no new UI primitive needed).
  - `apps/gateway/tests/batches/test_batch_window_grouping.py` — existing ~13 `claim_due` call
    sites assert only on the returned item list; unaffected by an additive Lua-side-effect. New
    test(s) added here for the new Redis aggregate keys.
  - `apps/dashboard/tests/batches-stats-page.test.tsx` — existing mocked responses
    (`STATS_ZERO`/`STATS_ACTIVE`) omit the 3 new fields entirely; passes unmodified iff the new
    fields are optional and the new StatCards render a defined placeholder for `undefined`.

Context (working folder): none beyond code — no new env var/config knob (this task adds no
  operator-facing toggle, only additive response fields + an internal Redis aggregate).

Honors (patterns / conventions):
  - Fail-open Redis reads → `null`/`None`, never a fabricated `0` or a 500
    (`usage/api/router.py:_read_ratelimit_counters` docstring: "never 0, never 500").
  - Additive-only, honestly-labeled placeholder fields for data that cannot exist yet — the exact
    precedent is `stats_router.py`'s own `_SAVINGS_USD_PENDING_BILLING_ACCURACY` constant.
  - Never edit a frozen contract's observable shape — extend via a new, additive side-effect only
    (`_CLAIM_DUE_LUA`'s return value is untouched; only new Redis keys are written).
  - Bounded IO — every new Redis op wrapped in `asyncio.timeout` (global CLAUDE.md: design for
    failure — timeouts/retries/circuit breakers on every IO path).
  - Dashboard StatCard composition (`Loading/ErrorState/Empty/StatCard` from `@/components/ui`) —
    reuse, no new component.

Anchors the contract cites:
  - `BatchStatsResponse` (new fields: `avg_window_wait_ms`, `avg_completion_latency_ms`,
    `completion_tpm` — all `float | None`)
  - `get_batch_stats()`
  - `_CLAIM_DUE_LUA` (additive Redis writes only)
  - `_items_key`/`_started_key` naming convention (new keys follow the same `batch:window:*` scheme)
  - `BatchesStatsPage` / `BatchStatsData`

Issues/Risks (→ feed §1):
  - `avg_completion_latency_ms` and `completion_tpm` CANNOT hold real data today: no live
    `BatchProcessor` exists (`batch_processor` is hardcoded `None` in `main.py`;
    `batch_diversion.py:121`'s `if batch_processor is None: return None` means diversion never
    actually fires in production). These ship as honest, documented `None` placeholders — same
    shape as `savings_usd` — pending v58's `openai-batch-adapter`/`anthropic-batch-adapter` +
    `batch-billing-accuracy`.
  - `avg_window_wait_ms` IS real code, live-testable today via the existing stub-processor test
    harness — but will ALSO read `None` in production today, for a DIFFERENT reason (diversion
    never fires at all while `batch_processor` is `None`, so no window ever flushes for real
    traffic). Both fields read `None` today but for different reasons — must be worded distinctly
    in the dashboard so this isn't mistaken for one bug.
  - The window-wait aggregate is a simple, never-reset all-time running average (sum+count) —
    consistent with `tenant_status_counts`'s own "across EVERY batch job the tenant has ever
    submitted" convention, but will drift/become less responsive over a long-lived deployment.
    Accepted MVP scope, not solved here.
  - TTFT is explicitly OUT of scope: no token streaming occurs on the batch path (confirmed
    against both providers' docs this session) — not a coherent metric for batch mode at all.

Related intent: this session's own conversation — Tin asked how admins monitor
  latency/TPM/TTFT once batch mode is enabled; live investigation found the stats page exposes
  none of the three, TTFT doesn't apply structurally, and the other two can't be real until v58's
  provider adapters exist. Tin approved "add and implement it," scoped down (per AskUserQuestion
  timeout → safe-default judgment call) to inert/scaffolding-now rather than activating v58.
  PROJECT.md: batch-discounted chat completions feature area (v57 closed/archived, v58 queued).

Ground SHA: f23fc5c

---

## 1 · SPECIFY — the rules

Feature: Batch observability scaffolding — real window-wait latency + honest placeholder
  completion metrics on the existing batch stats surface.

Framings weighed: extend the existing `GET /admin/batches/stats` + `BatchesStatsPage` (chosen —
  reuses the exact `savings_usd`-placeholder precedent, zero new endpoint/auth surface) · a
  separate `/admin/batches/observability` endpoint (rejected — splits one tenant-wide stats view
  into two calls for no benefit) · a Prometheus-only metric with no dashboard field (rejected —
  the user's actual question was "can an admin see this on the dashboard," which a
  Prometheus-only metric does not answer).

Must:
  - `BatchStatsResponse` gains 3 new fields, all `float | None`: `avg_window_wait_ms`,
    `avg_completion_latency_ms`, `completion_tpm`.
  - `avg_window_wait_ms` is REAL, computed from data: `_CLAIM_DUE_LUA` additively records its
    already-computed `elapsed` (the local var already used for the `due` check) into a new
    per-tenant Redis sum+count pair at the moment of a successful claim. `get_batch_stats` reads
    it fail-open (Redis error/absent client/no samples yet -> `None`, never a fabricated `0`),
    same convention as `_read_ratelimit_counters`.
  - `avg_completion_latency_ms` and `completion_tpm` are ALWAYS `None` today, each backed by a
    named constant documented inline (mirrors `_SAVINGS_USD_PENDING_BILLING_ACCURACY`) — pending
    v58's `openai-batch-adapter`/`anthropic-batch-adapter` + `batch-billing-accuracy`.
  - Non-regression: `_CLAIM_DUE_LUA`'s existing `claimed_items` return value and due/not-due
    decision are byte-identical before and after — verified by the existing
    `test_batch_window_grouping.py` suite passing UNMODIFIED.
  - `BatchesStatsPage` renders 3 new `StatCard`s (existing component, no new UI primitive). The
    two placeholder cards' `footer` caption reads differently from the window-wait card's own
    empty-state caption — both can show `None` today, for two DIFFERENT reasons (§0 Issues/Risks),
    and must not read as the same bug.
  - Existing tests (`test_batch_window_grouping.py`, `batches-stats-page.test.tsx`) pass with NO
    modification — their mocked responses simply omit the 3 new optional fields.

Reject: none new. This task adds no new input surface (same `GET` endpoint, same
  `require_owner_or_admin` dependency, unchanged) — the one guarantee that matters is the
  non-regression Must above, not a new rejection.

Accept: Given a tenant with `batch_grouping_enabled=true` whose stub `BatchProcessor` test
  harness has flushed 2 windows, When an admin calls `GET /admin/batches/stats`, Then the response
  has `avg_window_wait_ms` as a real positive number reflecting those 2 flushes' actual elapsed
  times, while `avg_completion_latency_ms` and `completion_tpm` are both `None`.

Assumptions — lowest-confidence first:
  ⚠ Units for the two latency fields (`avg_window_wait_ms`, `avg_completion_latency_ms`) —
    choosing **milliseconds**, matching this codebase's one existing precedent
    (`observability/middleware.py`'s `duration_ms`), over seconds (which would match
    `batch_window_seconds`'s own config unit). Lowest confidence because there's no batch-specific
    precedent to anchor it either way — a pure judgment call. If wrong: cosmetic, but a frozen
    contract field name/unit is a change-request to fix, not a hotfix.
  ⚠ The two placeholder fields are ALWAYS PRESENT in the response as JSON `null` (Pydantic
    `Optional[float] = None` default), never omitted — matching `savings_usd`'s always-present
    precedent, so the frontend can rely on the field existing rather than doing an `in` check. If
    wrong: a small frontend refactor (nullish-check -> presence-check), low cost.

---

## 3 · CONTRACT — freeze the shape

```
BatchStatsResponse (apps/gateway/src/gateway/batches/api/stats_router.py) — 3 new fields, all
optional, appended after the existing 3:
  savings_usd: str                        # unchanged
  total_requests: int                     # unchanged
  status_counts: dict[str, int]           # unchanged
  avg_window_wait_ms: float | None        # NEW — real, computed; None = no samples yet
  avg_completion_latency_ms: float | None # NEW — ALWAYS None today (placeholder, see below)
  completion_tpm: float | None            # NEW — ALWAYS None today (placeholder, see below)

Two new named placeholder constants (mirrors _SAVINGS_USD_PENDING_BILLING_ACCURACY):
  _COMPLETION_LATENCY_PENDING_ADAPTERS: float | None = None
  _COMPLETION_TPM_PENDING_ADAPTERS: float | None = None
  (each with an inline comment naming the blocking v58 task, exactly like the existing constant)

Redis key scheme (new, additive — apps/gateway/src/gateway/proxy/infrastructure/batch_window_buffer.py):
  batch:window:wait_sum:{tenant_id}    STRING — INCRBYFLOAT'd by `elapsed` on every claim
  batch:window:wait_count:{tenant_id}  STRING — INCR'd by 1 on every claim
  No TTL (2 keys/tenant, not per-item — bounded growth). Never read/written anywhere except
  _CLAIM_DUE_LUA (write) and get_batch_stats (read).

_CLAIM_DUE_LUA — ADDITIVE ONLY, inserted right after the existing `due = true` / before the
  existing `items = redis.call('LRANGE', ...)` line (i.e. only on the already-guaranteed-due
  path, never on the early `return false`):
  redis.call('INCRBYFLOAT', KEYS[4], elapsed)
  redis.call('INCR', KEYS[5])
  -- KEYS[4]/KEYS[5] appended to the script's existing KEYS[1..3]; ARGV unchanged.
  Existing `claimed_items` return value: BYTE-IDENTICAL, unchanged.

New Python helper (apps/gateway/src/gateway/proxy/infrastructure/batch_window_buffer.py):
  def _wait_sum_key(tenant_id: uuid.UUID) -> str: f"batch:window:wait_sum:{tenant_id}"
  def _wait_count_key(tenant_id: uuid.UUID) -> str: f"batch:window:wait_count:{tenant_id}"
  (same naming convention as _items_key/_started_key)

get_batch_stats() (stats_router.py) — new fail-open Redis read, mirrors
  _read_ratelimit_counters's exact convention:
  - redis_client obtained via getattr(request.app.state, "redis_client", None); handler gains a
    `request: Request` param (currently has none)
  - bounded by asyncio.timeout(5.0) (matches _RATELIMIT_REDIS_TIMEOUT_SECONDS)
  - catches (RedisError, OSError, ValueError, TimeoutError) -> avg_window_wait_ms = None
  - count == 0 (no samples, including "key absent") -> None, never a fabricated 0.0
  - else -> (sum / count) * 1000.0 if the stored elapsed is in seconds (it is — `elapsed` is
    epoch-seconds arithmetic) rounded to 3dp, matching duration_ms's own `round(x, 3)` convention

BatchStatsData (apps/dashboard/lib/batches.ts) — TS mirror of the 3 new fields, each
  `number | null`.

BatchesStatsPage.tsx — 3 new StatCard entries in the existing grid:
  - "Avg. queue wait" — value: `data.avg_window_wait_ms != null ? `${Math.round(data.avg_window_wait_ms)} ms` : "—"`,
    footer (only when null): "No windows have flushed yet."
  - "Avg. completion latency" — value always "—" today, footer: "Lands once real batch
    processing exists (v58)."
  - "Token throughput (TPM)" — value always "—" today, same footer as above.

Success response: 200, same shape as today + the 3 new fields (backward-compatible for any
  existing client parsing only the original 3). No new error case — auth/403 unchanged.
```

`Least-sure flag surfaced at freeze:` [contract] the ms-vs-seconds unit choice for the two
  latency field names — see §1's ⚠ ranking; this is the single most likely thing to need a
  change-request if reviewed differently. If wrong: rename + reconvert at the storage or
  read boundary — contained, but touches a frozen name.
Status: FROZEN @ v1 — approved by Tin Dang (2026-07-04)

---

## 4 · TESTS — failing-first (red)

Plan: test_<accept> — assert the §1 Accept line's Then (behavior, not internals).
Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code

Scope (may touch):
  - `apps/gateway/src/gateway/proxy/infrastructure/batch_window_buffer.py` (additive Lua +
    2 new key-name helpers only)
  - `apps/gateway/src/gateway/batches/api/stats_router.py`
  - `apps/gateway/tests/batches/test_batch_window_grouping.py` (new tests only, no edits to
    existing ones)
  - a new gateway test file for `get_batch_stats`'s new fields (fail-open + real-average paths)
  - `apps/dashboard/lib/batches.ts`
  - `apps/dashboard/components/batches/BatchesStatsPage.tsx`
  - `apps/dashboard/tests/batches-stats-page.test.tsx` (new tests only)

Strategy & known-problem fixes (ordered):
  1. Gateway: add `_wait_sum_key`/`_wait_count_key` + the 2 additive Lua lines to
     `_CLAIM_DUE_LUA`. Known-problem: must insert AFTER `due` is confirmed true and BEFORE the
     early-return `false` path — an accidental insert above the `due` check would record a wait
     sample for windows that aren't actually due yet.
  2. Gateway: write the RED test first — assert the two new Redis keys hold the right sum/count
     after a `claim_due` call in the existing test harness (raw-Redis read, same convention as
     `test_abandoned_marker_deleted_on_drain_sibling_claim_unaffected`) — confirm existing
     `test_batch_window_grouping.py` suite is untouched/still green throughout.
  3. Gateway: extend `BatchStatsResponse` + `get_batch_stats` (add `request: Request` param,
     fail-open Redis read, the 2 placeholder constants). Known-problem: must NOT let a Redis
     error propagate as a 500 — wrap in the same try/except shape as `_read_ratelimit_counters`.
  4. Dashboard: extend `BatchStatsData` + add the 3 `StatCard`s to `BatchesStatsPage.tsx`,
     wording the two placeholder-vs-real-empty captions distinctly per §1's Must.
  5. Run full existing suites (gateway + dashboard) unmodified to confirm zero regression, then
     the new tests green.

Strategy actually used: Reordered step 1 and step 2 to keep red-before-green honest: added
  ONLY the two name-only `_wait_sum_key`/`_wait_count_key` helpers first (pure naming
  scaffolding — makes zero tests pass by itself), THEN wrote the RED buffer-level test
  (asserting the new Redis keys after a claim), confirmed it failed for the right reason
  (keys never written — the Lua script was still untouched), THEN added the two additive
  Lua lines + wired them into claim_due()'s `keys=[...]` call site. This avoided the
  declared order's risk of an ImportError taking down the whole pre-existing 13-test file
  before red could even be observed. Step 2 also split into TWO new test surfaces, not one:
  (a) `test_batch_window_grouping.py` gained a new `TestWindowWaitAggregateRecorded` class
  (buffer-level Lua-side-effect assertions, 3 tests) — as declared; (b) a NEW file
  `apps/gateway/tests/batches/test_batch_stats_observability.py` (6 tests) was created for
  `get_batch_stats`'s endpoint-level fail-open + real-average behavior, deliberately
  separate from the PRE-EXISTING `test_batch_stats.py` (which already covers this same
  endpoint's original 3 fields and is untouched by this task) — §0's Ground map omitted
  that file's existence; §5's own "a new gateway test file" wording is honored literally
  rather than silently appending to the pre-existing suite. Dashboard: 4 new tests appended
  to `batches-stats-page.test.tsx` (not a new file), matching that bullet's literal wording.
  One deviation found and fixed mid-build, not in the original plan: the first
  `_read_window_wait_average` draft placed its `int()`/`float()` casts OUTSIDE the
  `try/except (RedisError, OSError, ValueError, TimeoutError)` block, so a corrupted/non-
  numeric stored value would have raised an uncaught ValueError -> 500, violating the
  "never a 500" fail-open Must. Caught via close comparison against
  `_read_ratelimit_counters`'s exact structural placement (casts sit INSIDE its try block);
  fixed by moving the casts inside, and locked in with a new regression test
  (`test_corrupted_wait_count_value_avg_window_wait_null`) empirically verified to fail
  against the un-fixed code and pass against the fix. Also applied one precedented, non-
  contract static-analysis fix: `_wait_sum_key`/`_wait_count_key` (leading-underscore by
  the frozen naming convention) trip pyright's strict-mode `reportPrivateUsage` when
  imported cross-module into `stats_router.py` — resolved with the SAME
  `# pyright: ignore[reportPrivateUsage]` idiom already used elsewhere in this codebase for
  an identical frozen-contract-mandates-cross-module-reuse situation
  (`proxy/application/images_use_case.py`'s `_fire_record_with_raw` import). Otherwise as
  planned: strategy steps 3-5 (BatchStatsResponse/get_batch_stats extension, dashboard
  StatCards, full-suite regression run) proceeded in the declared order.

Post-build fixes (found at VERIFY, applied before gate — contract shape untouched by either):
  1. An independently-spawned adversarial verify pass found a reproducible torn-read race in
     `_read_window_wait_average`: it issued `wait_sum` and `wait_count` as TWO SEQUENTIAL
     Redis `GET`s, but `_CLAIM_DUE_LUA` writes both keys together inside ONE atomic EVAL — a
     concurrent claim landing between the two GETs could be observed as sum-absent +
     count-already-incremented, fabricating a `0.0` average and violating this task's own
     "never a fabricated 0.0" Must. Reachability traced and confirmed latent-until-v58 (today
     `batch_processor` is hardcoded `None`, so `batch_diversion.py` never calls
     `buffer.append()` and `wait_count` is never written for any tenant) — not exploitable in
     production yet, but would arm silently the moment v58 wires a real processor. Fixed by
     replacing the two GETs with a single `MGET` (one Redis command = one atomic read of both
     keys, matching the write side's own atomicity). Locked in with a new regression test,
     `TestWindowWaitAtomicRead`, that pins the call shape itself (asserts exactly one `mget`
     call, zero `get` calls) so a future edit can't quietly reintroduce two sequential reads.
     One pre-existing test's fake Redis stub (`test_redis_error_avg_window_wait_null`'s
     `_BoomRedis`) implemented `.get` and had to be updated to implement `.mget` to match the
     new call shape — same behavioral assertion (Redis error -> fail open -> null), not a
     weakened test.
  2. Tin asked, mid-flight, for a visible text hint so admins know this feature is
     experimental. Added a page-level notice to `BatchesStatsPage.tsx` (an `Experimental`
     `Badge` + one sentence, always visible regardless of loading/error/data state) — additive
     UI-only, does not touch any frozen §3 field/type/shape. Red-then-green verified by hand
     (temporarily reverted the JSX, confirmed the new test failed for the right reason,
     restored it, confirmed green) since it landed after the main build cycle.
  3. Reverted an unrelated drift in `apps/dashboard/next-env.d.ts` (Next.js dev-server
     auto-regenerates this file's import path; not a deliberate edit, same recurring artifact
     flagged earlier this session).

Code lives in: `./src/`   ·   Constraints: change no test, no contract; allow-list packages only.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build — gateway
      `tests/batches/` 85/85 (`uv run pytest tests/batches/ -p no:randomly`, isolated DB
      `gateway_test_obsscaffold`), dashboard `batches-stats-page.test.tsx` 11/11 (real
      `./node_modules/.bin/vitest` binary, not the `npx` shim). `ruff check` + `uv run
      pyright` clean on both touched gateway files; real `./node_modules/.bin/tsc --noEmit`
      + `./node_modules/.bin/eslint` clean on both touched dashboard files. §3 CONTRACT text
      unchanged throughout (field names/types/response shape identical to the frozen v1).
      One pre-existing test's fake-Redis stub method name updated (`.get`->`.mget`) to track
      the post-build atomicity fix — same behavioral assertion, not a weakened test (see §5
      Post-build fixes #1).
- [x] green was EARNED — no overfit / vacuous asserts / stubbed-away logic — an
      independently-spawned adversarial verify agent (not the build agent) found a real,
      reproducible torn-read race via a working repro against real Redis (§5 Post-build
      fixes #1), which neither the build agent's own green suite nor my own spot-checks had
      surfaced. Fixed, then locked in with a regression test that pins the call shape itself
      (single `mget`, never two sequential `get`s) rather than a timing-dependent repro —
      deterministic and fails loudly on regression. The new experimental-notice test was
      independently confirmed red-then-green by hand (JSX temporarily reverted, test
      confirmed failing for the right reason, JSX restored, test confirmed passing).
- [x] no exposed secrets, injection openings, or unexpected dependencies (security =
      HARD-STOP) — no new dependency, no secret, no new input surface (same route, same
      `require_owner_or_admin` gate). The verify agent's reachability trace additionally
      confirmed the torn-read defect was not exploitable in production today (diversion
      never fires while `batch_processor` is `None`) — informed urgency, not a security
      finding either way. Nothing here triggers HARD-STOP.

Build expectations (from §1 Accept + §3 CONTRACT): a tenant whose stub `BatchProcessor` test
  harness has flushed 2 windows sees `avg_window_wait_ms` as a real positive number reflecting
  those flushes' actual elapsed times, while the two placeholder fields stay `None` — confirmed
  by `TestWindowWaitRealAverage::test_avg_window_wait_reflects_two_flushed_windows`
  (`(3.5 + 4.0) / 2 windows = 3750.0ms`, exact). Fail-open (never a fabricated 0.0, never a
  500) confirmed by `TestWindowWaitNoSamplesYet` + `TestWindowWaitFailOpen` (3 cases: absent
  client, Redis error, corrupted value) + the new `TestWindowWaitAtomicRead` (single-MGET call
  shape). Dashboard rendering (real value, distinct empty-vs-placeholder captions, the new
  experimental notice) confirmed by `batches-stats-page.test.tsx`'s 11 passing cases.

### GATE RECORD
Outcome: PASS
Reviewed by: Claude (orchestrator, adversarial verify by an independent add-verify subagent) · date: 2026-07-04
OBSERVE `[TDD · confirmed]`: when a write is atomic across multiple keys (one Lua EVAL touching
  N keys), the matching read must ALSO be atomic (one MGET/Lua), never N sequential GETs — a
  torn read across "wrote together, read apart" is a distinct, generalizable defect class from
  the single-key fail-open pattern this codebase already knows well. Worth carrying into
  PROJECT.md at the next fold as a reusable review question: "does every multi-key write have
  a matching single-command read?"

