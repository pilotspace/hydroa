# TASK: Bandwidth bucket observability readout

slug: bandwidth-counter-view · created: 2026-06-24 · stage: production
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
  MIRROR (the frozen v31 sibling — copy its shape almost exactly):
  - `usage/api/router.py:get_ratelimits` (795) + `RatelimitItem`/`RatelimitsResponse` (754/767) +
    `_read_ratelimit_counters` (775) — the EXACT pattern: owner/admin-scoped, tenant-scoped,
    READ-ONLY Redis read, fail-open → null, 200 always. `GET /admin/ratelimits` on `usage_router`.
    `_RATELIMIT_DB_TIMEOUT_SECONDS=30` / `_RATELIMIT_REDIS_TIMEOUT_SECONDS=5` (IO timeout floor).
  - test harness `tests/ratelimit_counter_view/conftest.py` — signup_tenant · create_key (POST
    /admin/keys) · mint_role_token (same-tenant role JWT) · seed_* (direct Redis) · real PG :5433 +
    Redis. MIRROR for bandwidth seeding.

  CHANGES (this task owns):
  - `usage/api/router.py` — ADD `GET /admin/bandwidth` (`get_bandwidth`) + `BandwidthItem` /
    `BandwidthResponse` models + a READ-ONLY `_read_bandwidth_levels(redis, key_ids)` helper.
  - tests in `apps/gateway/tests/bandwidth_counter_view/` (new dir + conftest mirroring the above).

  CONSUMES (task 1 / task 2, already done):
  - bandwidth bucket Redis keyspace `bandwidth:bucket:{key_id}` (persisted FLOAT level, may be
    negative; String GET) + `bandwidth:bucket_ts:{key_id}` (last-refill epoch ms, String GET) — BOTH
    read for the refill-adjusted readout (Tin chose refill-adjusted over the raw floor 2026-06-24).
  - task-1 Lua refill (redis_token_bucket.py 59-62) — the formula the view duplicates, comment-pinned.
  - `core/config.py:Settings.bandwidth_tokens_per_sec` / `.bandwidth_burst_tokens` — global rate/burst
    (per-key override is a deferred delta) → surface as capacity context. `>0` ⇒ enabled.

  READ (mirror — NOT changed):
  - `require_owner_or_admin` dep + `Identity.tenant_id` (the authz seam every /admin view uses).
  - `RedisError`/`asyncio.timeout`/`text` imports already present at the top of usage/api/router.py.

Context (working folder):
  - The bucket's own `RedisTokenBucket.level()` REFILLS-to-now but masks Redis errors as `burst`
    (fail-open=admit). That is RIGHT for admission, WRONG for an honest readout (it would report
    "full" when Redis is down). So the view does NOT call level(); it reads the raw persisted
    `bandwidth:bucket:{key_id}` via GET and reports null on error/absent — mirroring the ratelimit
    view's honest-null. Caveat surfaced in §1: the GET value is the level at LAST activity (refill is
    lazy), so a quiet key reads stale-low; real available ≥ shown. rate/burst give capacity context.
  - No migration. No DB schema change. New tests dir only.

Honors (patterns / conventions):
  - READ-ONLY observability: GET only, never SET/INCR/EXPIRE — same as ratelimit view.
  - Fail-open to NULL (unknown), never 0, never 500 — Redis down still returns 200.
  - Tenant-scoped via the api_keys tenant filter; owner/admin only (member → 403 ERR_AUTH_FORBIDDEN,
    missing bearer → 401) — the standard /admin authz, no new authz surface (no security HARD-STOP).
  - PROJECT.md IO invariant: bounded timeouts on both the DB and the Redis read.

Anchors the contract cites (§3 may name ONLY these):
  - `GET /admin/bandwidth` on usage_router · `get_bandwidth` handler · `BandwidthItem` /
    `BandwidthResponse` · `_read_bandwidth_levels` · Redis key `bandwidth:bucket:{key_id}` (GET,
    read-only) · Settings.bandwidth_tokens_per_sec / .bandwidth_burst_tokens · require_owner_or_admin.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: GET /admin/bandwidth — owner/admin READ-ONLY view of per-key bandwidth bucket levels vs configured capacity
Framings weighed:
  - Refill-adjusted READ-ONLY read (CHOSEN, Tin 2026-06-24): GET both `bandwidth:bucket:{key_id}`
    and `bandwidth:bucket_ts:{key_id}`, apply task-1's EXACT refill formula in the view
    (level = min(burst, stored + rate·(now−ts)/1000)), null on error/absent. Honest CURRENT level
    (a quiet key shows true available, not the stale floor) AND side-effect-free (compute, never write).
    Accepts a small drift risk: the formula is duplicated from task-1's Lua — pinned by a comment
    referencing redis_token_bucket.py lines 59-62 so the two stay in lock-step.
  - Call bucket.level()/try_consume(0) — rejected: level() masks Redis errors as "full" (would
    report a full bucket while Redis is down); try_consume(0) WRITES (refill persist) — a GET must not mutate.
  - Raw stored floor only (no refill) — rejected (Tin): a quiet key reads stale-low, misleading operators.
Must:
<must>
  - GET /admin/bandwidth (owner/admin) → 200 { enabled, rate_per_sec, burst, keys:[{key_id, name,
    level:int|null}] } for the CALLER'S TENANT ONLY (api_keys filtered by tenant_id, revoked excluded),
    ordered created_at ASC, id ASC (same as the ratelimit view).
  - `level` = REFILL-ADJUSTED current tokens, READ-ONLY: GET both `bandwidth:bucket:{key_id}` (stored
    float, may be negative) and `bandwidth:bucket_ts:{key_id}` (last-refill epoch ms), then
    `int(min(burst, stored + rate·max(0, now_ms−ts)/1000))` — task-1's exact refill (redis_token_bucket
    Lua 59-62). ts absent → no refill (ts=now). null when the level key is absent (never touched) OR
    Redis is unavailable/erroring OR pacing is disabled. NEVER writes a key.
  - `enabled` = settings.bandwidth_tokens_per_sec > 0; `rate_per_sec` / `burst` = the global knobs
    (capacity context + the refill inputs; per-key override is a deferred delta). Disabled (rate≤0) ⇒
    no refill math, all levels null.
  - Bounded IO: timeouts on the DB query and the Redis read (mirror the ratelimit floor 30s/5s).
  - Fail-open: ANY Redis error (or absent client) → every level null, still 200 (never 0, never 500).
</must>
Reject:
<reject>
  - caller is a member (not owner/admin) -> "ERR_AUTH_FORBIDDEN" (403)
  - missing / malformed bearer -> "ERR_AUTH_INVALID_TOKEN" (401)
</reject>
After:
<after>
  - No state changed (pure read): no Redis key written, no api_keys row touched. A second identical
    call returns the same body (idempotent).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The view's refill formula stays in lock-step with task-1's Lua — lowest confidence because it is
    DUPLICATED math (the only honest way to show current level read-only); if task-1's refill changes,
    the readout drifts. RESOLVED-as-mitigated: pinned by a comment citing redis_token_bucket Lua 59-62
    + a test asserting the exact refilled value for a known (stored, ts, now, rate, burst). COST: a
    readout that lags a future Lua change until the comment-pinned test catches it.
  - [x] Stored value is a FLOAT string, possibly negative — GROUND-VERIFIED (Lua `tostring(level)`,
    line 75) → parse int(float(raw)); int(raw) would 500 on "199.5".
  - [x] enabled/rate/burst are GLOBAL (no per-key column in v36) — confirmed by task-1 (knobs only).
  - [x] member→403 / missing→401 come free from require_owner_or_admin — confirmed by the ratelimit mirror.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: owner sees per-key levels vs capacity
  Given pacing is enabled (rate>0) and the caller's tenant has 2 keys, one with a seeded
        bandwidth:bucket level + a FRESH ts (now) and one untouched
  When the owner GETs /admin/bandwidth
  Then 200 with enabled=true, rate_per_sec/burst from settings, keys ordered created_at ASC,
       the seeded key level≈the seeded stored value (fresh ts ⇒ ~no refill) and the untouched key level=null

Scenario: refill-adjusted level reflects elapsed time
  Given rate=100, burst=200, a key with stored level "50.0" and ts = now − 1000ms (1s ago)
  When the owner GETs /admin/bandwidth
  Then level = 150 (50 + 100·1s), clamped at burst=200 — the refilled CURRENT level, not the stale 50

Scenario: float/negative persisted level parses without a 500
  Given a key whose bandwidth:bucket holds "199.5" (fresh ts) and another holding "-20.0" (fresh ts, debt)
  When the owner GETs /admin/bandwidth
  Then the levels read back as ints (199 and -20-ish), never a 500 from int("199.5")

Scenario: disabled pacing still reports honestly
  Given bandwidth_tokens_per_sec=0 (default)
  When the owner GETs /admin/bandwidth
  Then 200 with enabled=false, rate_per_sec=0, burst=0, and each key level=null (no bucket data)

Scenario: Redis unavailable → null levels, still 200
  Given the app.state.redis_client errors on get
  When the owner GETs /admin/bandwidth
  Then 200 and every key level=null (fail-open, never 0, never 500)

Scenario: tenant isolation
  Given tenant A and tenant B each have keys with seeded levels
  When A's owner GETs /admin/bandwidth
  Then only A's keys appear; B's key_ids are never present

Scenario: read-only, no mutation
  Given a seeded bucket level for a key
  When the owner GETs /admin/bandwidth twice
  Then the bandwidth:bucket Redis value is byte-identical before and after (no write from the GET)
  And the api_keys rows are unchanged

Scenario: member is forbidden   # REJECTION
  Given a member (non owner/admin) JWT for the tenant
  When they GET /admin/bandwidth
  Then 403 ERR_AUTH_FORBIDDEN
  And no tenant data is returned

Scenario: missing bearer is unauthorized   # REJECTION
  Given no Authorization header
  When the request hits GET /admin/bandwidth
  Then 401 ERR_AUTH_INVALID_TOKEN
  And no tenant data is returned
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/bandwidth        (no body; owner/admin via require_owner_or_admin; tenant-scoped)
  200 -> {
    enabled: bool,            # settings.bandwidth_tokens_per_sec > 0
    rate_per_sec: int,        # settings.bandwidth_tokens_per_sec  (global; per-key override deferred)
    burst: int,               # settings.bandwidth_burst_tokens
    keys: [ { key_id: str, name: str, level: int | null } ]   # level = int(float(GET bandwidth:bucket:{key_id}))
  }                           #   level null = key absent (never touched) OR Redis unavailable
  403 -> { error: "ERR_AUTH_FORBIDDEN" }        # member
  401 -> { error: "ERR_AUTH_INVALID_TOKEN" }    # missing/malformed bearer

New symbols (usage/api/router.py):
  - BandwidthItem(BaseModel, frozen): key_id:str, name:str, level:int|None
  - BandwidthResponse(BaseModel, frozen): enabled:bool, rate_per_sec:int, burst:int, keys:list[BandwidthItem]
  - _read_bandwidth_levels(redis_client, key_ids, *, rate, burst, now_ms) -> dict[str,int|None]
      (READ-ONLY: GET bandwidth:bucket:{kid} + bandwidth:bucket_ts:{kid} per key; refill-adjust
       int(min(burst, float(stored) + rate·max(0, now_ms−float(ts))/1000)); ts absent ⇒ ts=now_ms;
       level key absent ⇒ null; fail-open {} → all null on ANY error; rate≤0 ⇒ {} (all null);
       bounded by asyncio.timeout(_BANDWIDTH_REDIS_TIMEOUT_SECONDS=5.0))
  - get_bandwidth(request, identity=Depends(require_owner_or_admin), session) -> BandwidthResponse
      (api_keys SELECT id,name WHERE tenant_id AND revoked_at IS NULL ORDER BY created_at ASC, id ASC,
       bounded by asyncio.timeout(_BANDWIDTH_DB_TIMEOUT_SECONDS=30.0); reads settings from app.state;
       now_ms via time.time()*1000)

Schema: READS api_keys (id, name, tenant_id, revoked_at, created_at) + Redis Strings
        bandwidth:bucket:{key_id} + bandwidth:bucket_ts:{key_id} (GET only). WRITES: none. No migration.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-24), with refill-adjusted level (Tin chose over raw floor)
Least-sure flag surfaced at freeze: [contract] the view DUPLICATES task-1's Lua refill math
(level = min(burst, stored + rate·elapsed)) — the only honest way to show CURRENT tokens read-only,
but it drifts if task-1's refill ever changes. WHY least-sure: it is the one place this read-only view
re-implements writer logic. COST if wrong: the readout silently lags a future Lua change. MITIGATED:
the formula is comment-pinned to redis_token_bucket.py 59-62 + a deterministic clamp test
(stored 0, ts −100s, rate 100 → exactly burst) locks the behavior. Ground-verified resolutions:
[spec] stored value is a FLOAT string possibly negative → int(float(raw)), not int(raw) (would 500);
[spec] member→403 / missing→401 ride require_owner_or_admin for free (ratelimit-view precedent).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (gateway floor). Real PG :5433 + Redis, mirroring tests/ratelimit_counter_view/
conftest.py (signup_tenant · create_key · mint_role_token · auth) + a bandwidth seeder
(SET bandwidth:bucket:{key_id}). One test per §2 scenario.
<test_plan>
  - test_owner_sees_levels_vs_capacity: seed level + fresh ts, leave one untouched → 200, enabled, levels [≈seeded, null], order
  - test_refill_adjusted_level: rate=100,burst=200, stored "50.0", ts=now−1000ms → level==150 (deterministic refill)
  - test_float_negative_level_parses: seed "199.5" + "-20.0" (fresh ts) → int levels, no 500
  - test_disabled_reports_honestly: settings rate=0 → enabled=false, rate/burst 0, all levels null
  - test_redis_unavailable_levels_null: erroring redis client → all levels null, 200
  - test_tenant_isolation: A's owner sees only A's keys; B's key_id absent
  - test_read_only_no_mutation: GET twice → bandwidth:bucket value byte-identical, api_keys unchanged
  - test_member_forbidden: member JWT → 403 ERR_AUTH_FORBIDDEN
  - test_missing_bearer_unauthorized: no header → 401 ERR_AUTH_INVALID_TOKEN
</test_plan>

Tests live in: `apps/gateway/tests/bandwidth_counter_view/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/api/router.py`
Strategy (ordered batches):
  1. Add BandwidthItem / BandwidthResponse models + _BANDWIDTH_DB/REDIS_TIMEOUT consts next to the ratelimit ones.
  2. Add _read_bandwidth_levels (READ-ONLY GET, int(float(raw)), fail-open {}).
  3. Add get_bandwidth handler (owner/admin dep, tenant api_keys SELECT, settings from app.state, assemble).
Safety rule (feature-specific): READ-ONLY — GET only, never SET/INCR/EXPIRE; fail-open to null on any
  Redis error; bounded timeouts on both IO reads. No new authz surface (reuse require_owner_or_admin).
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

- [x] all tests pass — bandwidth suite 10/10; ratelimit mirror 9/9 (no regression); full suite 1568 passed
- [x] coverage did not decrease — gateway floor held (full-suite run, no --cov-fail-under trip)
- [x] no test or contract was altered during build — §3 frozen untouched; one test STRENGTHENED (redis-down arm now raises redis.exceptions.ConnectionError, the real prod arm) per refute-read, then re-crossed
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) = UPHOLD, 0 blockers/majors. Verified refill formula matches the writer Lua EXACTLY; READ-ONLY proven by byte-identity; the clamp test would catch a dropped refill term. 2 MINORs: (1) test exception class → FIXED; (2) int() vs floor for negative-fractional debt → spec-compliant (§3 frozen int()), logged as observe delta.
- [x] concurrency / timing safe — pure read; bounded asyncio.timeout on both DB (30s) and Redis (5s); refill uses a single now snapshot; no writes ⇒ no races
- [x] no exposed secrets, injection openings, or unexpected dependencies — parametrized SQL (tenant_id bind); no new deps; no secret in logs; tenant-scoped
- [x] layering & dependencies follow CONVENTIONS.md — same module + pattern as the frozen ratelimit view; owner/admin via require_owner_or_admin (no new authz surface)
- [ ] a person reviewed and approved the change — PENDING Tin (commit/PR held)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Refill-adjusted level: a key untouched for 100s with stored 0 reads burst (200), a fresh key reads ~its stored value — confirmed by test_refill_adjusted_level (clamped==200 exact, fresh∈[149,151])
- [x] Honest null: untouched key, Redis-down (real RedisError arm), and disabled pacing all → level null, still 200 — confirmed by 3 tests; never 0, never 500
- [x] Float/negative parse: "199.5"/"-20.0" never 500 (int(float(raw))) — confirmed by test_float_negative_level_parses
- [x] READ-ONLY: bandwidth:bucket value byte-identical after 2 GETs; api_keys unchanged — confirmed by test_read_only_no_mutation
- [x] Authz/tenant: member→403, missing→401, tenant B's keys never appear — confirmed by 3 tests

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — get_bandwidth registered on usage_router (@usage_router.get("/bandwidth")); BandwidthItem/Response/_read_bandwidth_levels all referenced by the handler; route reached live in 10 tests (404→200 after build)
- [x] DEAD-CODE (code) — no orphaned symbol; every new name is on the live request path
- [x] SEMANTIC — n/a (code task)

### GATE RECORD
Outcome: PASS
Evidence: bandwidth suite 10/10 · ratelimit mirror 9/9 (no regression) · full gateway suite **1568
passed**, 19 deselected (exit 0, ~4m11s) · ruff + pyright clean · refute-read (sonnet) UPHOLD, 0
blockers; 1 MINOR fixed (real RedisError arm), 1 MINOR logged as observe delta (int vs floor).
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a
Reviewed by: AI auto-gate (autonomy:auto) · human approval (Tin) PENDING for commit/PR · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] level uses int() (truncates toward zero) → a negative-fractional debt over-reports by ≤1 token vs math.floor (evidence: refute-read MINOR; spec-compliant since §3 froze int(); revisit only if debt precision matters for an operator)
- [SPEC · open] dashboard /bandwidth UI to render this readout (evidence: BE-only here; mirrors the v31 ratelimit-counter-view → ratelimits UI pairing — a frontend task)
- [SPEC · open] per-key rate/burst override would make rate_per_sec/burst per-row rather than the global knob (evidence: carried task-1 delta; today single global ceiling)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
