# TASK: Rate-limit counter view — GET /admin/ratelimits + read-only counter panel

slug: ratelimit-counter-view · created: 2026-06-23 · stage: production
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
COUNTER SOURCE (the deciding finding): rate limits are enforced by `RedisLuaRateLimiter` (rate_limits/infrastructure/redis_lua_limiter.py) over a 60s SLIDING window. The live counters are Redis keys, keyed by `key_id` (UUID):
- RPM: ZSET `f"ratelimit:rpm:{key_id}"` (member=uuid hex, score=now_ms, TTL 61s) → current rpm = ZCARD (count of members; ~1s eviction-lag over-count, acceptable for a dashboard snapshot).
- TPM: String `f"ratelimit:tpm_sum:{key_id}"` (INCRBYFLOAT, eviction-corrected, TTL 61s) → current tpm = GET (float). (Companion ZSET `ratelimit:tpm:{key_id}` holds per-record members; the _sum string is the canonical live value the limiter reads.)
- redis_client decode_responses=False → bytes; ZCARD returns int, GET returns bytes|None → parse float, None→0.0.
BACKEND (add to the EXISTING usage_router next to get_upstream_health — same prefix /admin, same auth, read-only):
- `apps/gateway/src/gateway/usage/api/router.py:get_upstream_health` / `:get_alerts` — CLOSEST analogs; copy: `Depends(require_owner_or_admin)`, `asyncio.timeout(...)` around IO, frozen Pydantic response. Add `get_ratelimits` here. NEW: this handler also needs `request: Request` for `request.app.state.redis_client` and `Depends(get_session)` for the api_keys query.
- `apps/gateway/src/gateway/keys/infrastructure/orm.py:ApiKeyRow` — table `api_keys`; columns `id`(key_id), `tenant_id` NOT NULL, `name`, `key_prefix`/`prefix`, `rpm_limit:int|None`, `tpm_limit:int|None`. The per-key configured limits.
- `apps/gateway/src/gateway/keys/infrastructure/repository.py:list_by_tenant(tenant_id)` — `SELECT ... WHERE tenant_id == :tid` — the tenant-scoping seam (read only the caller's keys → Redis keys embed key_id → implicitly tenant-scoped; NO cross-tenant access possible).
- `apps/gateway/src/gateway/keys/api/deps.py:require_owner_or_admin` — owner/admin; member→403 ERR_AUTH_FORBIDDEN.
- `apps/gateway/src/gateway/proxy/api/deps.py` (line ~118) — canonical redis access `getattr(request.app.state, "redis_client", None)`.
- `apps/gateway/src/gateway/rate_limits/infrastructure/redis_lua_limiter.py:RedisLuaRateLimiter.check_rpm/check_tpm` — the WRITERS; their key strings (`ratelimit:rpm:{key_id}`, `ratelimit:tpm_sum:{key_id}`) are the format I must read in. All limiter ops FAIL-OPEN (catch Exception→admit) — my read path mirrors: design-for-failure, Redis error → counter reported as null (unknown), never crash the page.
- NO new error spec (read-only, 401/403 inherited). NO migration, NO new table, NO Redis write.

FRONTEND (read-only counter panel on the existing /keys page):
- `apps/dashboard/components/keys/KeysPage.tsx` — existing keys page (bffGet "/admin/keys", ApiKey type already has rpm_limit/tpm_limit). Add a read-only "Rate-limit usage" panel: a useQuery(["admin-ratelimits"], bffGet "/admin/ratelimits") rendered as a table (Key / rpm current÷limit / tpm current÷limit). Mirror the existing useQuery + DataTable shape.
- `apps/dashboard/lib/bff-client.ts:bffGet` — the keys page's read helper (cookie auth via /api/gw catch-all; NO new BFF route).
- NO nav change (lives inside the existing /keys page).

Context (working folder):
- `.add/milestones/v31/MILESTONE.md` criterion "An owner sees current rpm/tpm consumption per key." (task: ratelimit-counter-view; depends-on: none).
- Tests to mirror: backend `apps/gateway/tests/upstream_health_view/` (signup tenant + owner/member tokens + Redis fixture); frontend `apps/dashboard/tests/*.test.tsx` (msw + within(region)).

Honors (patterns / conventions):
- CONVENTIONS.md: Clean-Arch (api reads ORM + Redis, no logic in api) · owner/admin gate · frozen Pydantic · asyncio.timeout on IO · design-for-failure (Redis fail → null counter, never 500) · frontend within(<section>) · bffGet for keys-page reads.
- PROJECT.md: rate-limit counters are PER-KEY operational state in Redis; tenant-scoped via the api_keys list (NEVER read another tenant's key counters).

Anchors the contract cites: `ratelimit:rpm:{key_id}` (ZCARD) · `ratelimit:tpm_sum:{key_id}` (GET) · `api_keys`(rpm_limit/tpm_limit/tenant_id) · `list_by_tenant` · `usage_router` · `require_owner_or_admin` · `request.app.state.redis_client` · `bffGet`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Rate-limit counter view — an owner/admin sees the CURRENT rpm/tpm consumption per API key (live Redis counters vs the per-key configured limit) via `GET /admin/ratelimits`, on a read-only panel within the existing /keys page.
Framings weighed: read the live Redis counters the limiter writes (chosen — the single source of truth, no drift) · re-derive from usage_records SQL (rejected — that's billed-history, not the live sliding-window counter, and would double-count) · expose the limiter object's in-memory state (rejected — there is none; the limiter is stateless over Redis).
Must:
<must>
  - For each API key of the CALLER'S tenant, return: key_id, name, rpm_limit (int|null), tpm_limit (int|null), rpm_current (int|null), tpm_current (float|null).
  - rpm_current := ZCARD `ratelimit:rpm:{key_id}` (count of requests in the live ~60s window). tpm_current := float of GET `ratelimit:tpm_sum:{key_id}` (0.0 if the key is absent/unset).
  - Tenant-scoped: only the caller's tenant's keys appear (list_by_tenant(identity.tenant_id)); another tenant's key counters are NEVER read or returned.
  - Owner/admin only (member → 403); READ-ONLY — no Redis write (no ZADD/INCR/EXPIRE), no DB write.
  - Design-for-failure: all Redis reads under asyncio.timeout; if Redis is unavailable/errors, the counter is reported as null (unknown) — the endpoint still returns 200 with the key list + limits (never 500, mirroring the limiter's fail-open).
  - Frontend: a read-only "Rate-limit usage" panel on /keys showing each key's rpm_current/rpm_limit and tpm_current/tpm_limit, with loading/empty/error states.
Reject:
  - missing/invalid Bearer -> existing auth rejection (401); member role -> "ERR_AUTH_FORBIDDEN" (403)
After:
  - A read-only GET returned the caller's tenant's per-key live rpm/tpm counters vs limits; no Redis key and no DB row was created/updated/deleted.
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **rpm_current via ZCARD is an APPROXIMATE sliding-window count (eviction-lagged), not the exact instantaneous value** — lowest confidence because the limiter prunes the ZSET (ZREMRANGEBYSCORE) only on the next check_rpm, so a read-only ZCARD can include up to ~1s of just-expired members; a precise read would need ZCOUNT with a now_ms floor matching the limiter's clock. For a dashboard "current consumption" snapshot this over-count is negligible and never under-reports (safe direction). If wrong (operators need exact): switch to ZCOUNT(now_ms-60000, +inf) — a one-line change, same contract. Cost if wrong: a follow-up tweak, no contract rework.
  - [ ] tpm_current from the `_sum` string (not summing the tpm ZSET) — the _sum is the canonical value the limiter itself reads for enforcement, so the view matches enforcement exactly. Low risk.
  - [ ] Redis-down → null counter (not 0, not 500) — null is honest ("unknown"); 0 would falsely read "no usage". Low risk; matches design-for-failure.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner sees live counters vs limits for each tenant key
  Given the caller's tenant has an API key with rpm_limit=60, tpm_limit=1000
  And the Redis ZSET ratelimit:rpm:{key_id} holds 3 members and ratelimit:tpm_sum:{key_id} = "150.0"
  And an owner is authenticated
  When the owner GETs /admin/ratelimits
  Then the response includes that key with rpm_current=3, rpm_limit=60, tpm_current=150.0, tpm_limit=1000

Scenario: A key with no traffic reads zero
  Given the caller's tenant has an API key with NO ratelimit:* Redis keys set
  And an owner is authenticated
  When the owner GETs /admin/ratelimits
  Then that key reports rpm_current=0 and tpm_current=0.0 (not null — Redis is up, the key is simply unused)

Scenario: Only the caller's tenant keys appear
  Given tenant T and tenant U each have an API key with Redis counters set
  And an owner of tenant T is authenticated
  When the owner GETs /admin/ratelimits
  Then only tenant T's key is in the response; tenant U's key_id is absent

Scenario: Read-only — no Redis or DB mutation
  Given a key with counters set to rpm=3
  When the owner GETs /admin/ratelimits
  Then the ZCARD of ratelimit:rpm:{key_id} is still 3 afterward and no api_keys row changed

Scenario: Redis unavailable -> counters null, still 200
  Given the Redis client is unavailable (absent/raises)
  And an owner is authenticated
  When the owner GETs /admin/ratelimits
  Then the response is 200 with each key's rpm_current=null and tpm_current=null (limits still shown)

Scenario: Admin role is allowed
  Given an admin (non-owner) is authenticated
  When the admin GETs /admin/ratelimits
  Then the request succeeds (200) and returns the per-key counter list

Scenario: Member is forbidden
  Given a member is authenticated
  When the member GETs /admin/ratelimits
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And no Redis key and no api_keys row was created, updated, or deleted

Scenario: Missing bearer is unauthorized
  Given no Authorization header is sent
  When a client GETs /admin/ratelimits
  Then the response is 401
  And no Redis key and no api_keys row was created, updated, or deleted

Scenario: Dashboard panel renders current/limit per key
  Given the API returns a key with rpm_current=3, rpm_limit=60
  When an owner opens /keys
  Then the rate-limit usage panel shows that key's rpm consumption against its limit
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/ratelimits   body: none
  auth: Bearer (owner or admin) via require_owner_or_admin
  200 -> {
    keys: [
      {
        key_id: str,
        name: str,
        rpm_limit: int | null,      # per-key configured limit (api_keys.rpm_limit)
        tpm_limit: int | null,      # per-key configured limit (api_keys.tpm_limit)
        rpm_current: int | null,    # ZCARD ratelimit:rpm:{key_id}; null if Redis unavailable
        tpm_current: float | null   # float(GET ratelimit:tpm_sum:{key_id}); null if Redis unavailable
      }
    ]
  }
  401 -> handled by auth dependency (missing/invalid bearer)
  403 -> { detail: { error: "ERR_AUTH_FORBIDDEN", ... } }  # member role

Read derivation (per key of the caller's tenant only):
  keys := list_by_tenant(identity.tenant_id)            # tenant scoping — NEVER another tenant
  rpm_current := int(ZCARD f"ratelimit:rpm:{key_id}")   # 0 if key absent (Redis up); null if Redis errors
  tpm_current := float(GET f"ratelimit:tpm_sum:{key_id}" or 0.0)  # null if Redis errors

Schema/Access: SELECT over api_keys (via list_by_tenant) + Redis ZCARD/GET (READ-ONLY) on usage_router
with Depends(require_owner_or_admin) + Depends(get_session) + request.app.state.redis_client. All IO under
asyncio.timeout; Redis error → counters null, 200 still returned (fail-open). NO write, NO migration.
Frontend: bffGet("/admin/ratelimits") on the existing /keys page via the BFF catch-all (no new BFF route).
```

Status: FROZEN @ v1 — approved by Tin (autonomy:auto; NOT a security freeze — read-only, strictly
tenant-scoped via list_by_tenant, no new scope relaxation; Redis reads are non-mutating).

Least-sure flag surfaced at freeze: [contract] **rpm_current is an APPROXIMATE sliding-window count via ZCARD**
— the limiter evicts expired members only on the next write, so a read-only ZCARD can over-count by up to ~1s
of just-expired requests (it never UNDER-reports — the safe direction for a consumption gauge). Exact-instant
reads would need ZCOUNT(now_ms-60000, +inf); that's a one-line swap with the SAME contract. Cost if wrong:
a follow-up tweak, no contract rework. (tpm_current uses the limiter's own `_sum` value, so it matches
enforcement exactly.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_owner_sees_counters_vs_limits: create key (rpm_limit=60,tpm_limit=1000), seed ZSET 3 members + tpm_sum "150.0" / owner GET / entry rpm_current=3, rpm_limit=60, tpm_current=150.0, tpm_limit=1000
  - test_unused_key_reads_zero: create key, NO redis keys / owner GET / rpm_current=0, tpm_current=0.0 (not null)
  - test_only_callers_tenant_keys: tenant T + tenant U each a key w/ counters / owner-of-T GET / only T's key_id present, U's absent
  - test_read_only_no_mutation: seed rpm ZSET 3 / owner GET / ZCARD still 3 after + api_keys unchanged
  - test_redis_unavailable_counters_null: app.state.redis_client=None (or raises) / owner GET / 200, rpm_current=None, tpm_current=None, limits still shown
  - test_admin_allowed: admin GET / 200 + keys list present
  - test_member_forbidden: member GET / 403 ERR_AUTH_FORBIDDEN + no redis/db mutation
  - test_missing_bearer_unauthorized: no auth / 401 + no mutation
  - (frontend) tests/ratelimits.test.tsx: panel renders current/limit per key + loading/empty/error states (msw + within(region))
</test_plan>

Tests live in: `apps/gateway/tests/ratelimit_counter_view` · `apps/dashboard/tests/ratelimits.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/api/router.py` · `apps/dashboard/components/keys/KeysPage.tsx` · `apps/dashboard/components/keys`
Strategy (ordered batches): 1. backend get_ratelimits + schemas on usage_router (read api_keys via list_by_tenant + Redis ZCARD/GET, fail-open to null) (RED→green) · 2. frontend read-only RatelimitsPanel on KeysPage (useQuery + bffGet) · 3. run both suites green.
Safety rule (feature-specific): READ-ONLY — NEVER ZADD/INCR/EXPIRE/DEL any ratelimit:* key or write api_keys; all Redis IO under asyncio.timeout with a fail-open (Redis error → counter null, return 200).
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

- [x] all tests pass — backend tests/ratelimit_counter_view 9 green; full gateway suite 1346 green (--ignore=tests/edge, single process); dashboard 378 green (+5).
- [x] coverage did not decrease — 9 backend + 5 frontend tests ADDED; none removed.
- [x] no test or contract was altered during build — §3 FROZEN unchanged; tests only ADDED/STRENGTHENED (refute EG-2 revoked-key), re-crossed tests→build.
- [x] the green was EARNED — adversarial refute-read (sonnet) UPHELD 0.91, ZERO blockers. Confirmed the critical key-format match (limiter-writes == handler-reads == conftest-seeds, all dashed-UUID f-string — NOT a closed loop). 5 earned-gaps low-risk; closed the meaningful one (revoked_at IS NULL clause now guarded by test_revoked_key_excluded).
- [x] concurrency / timing — Redis reads (ZCARD/GET only, non-mutating) under asyncio.timeout(5s); DB SELECT under asyncio.timeout(30s); fail-open on Redis error/timeout → null counters, never 500.
- [x] no exposed secrets, injection openings, or unexpected dependencies — parameterized SQL bind; Redis key strings built from DB-sourced key_ids only; redis.exceptions.RedisError already a transitive dep (limiter uses it).
- [x] layering & dependencies follow CONVENTIONS.md — api reads ORM table (raw text) + Redis, owner/admin gate, frozen Pydantic; frontend bffGet + within(section) + DataTable.
- [x] reviewed — autonomy:auto auto-resolved (NOT security: read-only, strictly tenant-scoped by the api_keys WHERE tenant_id filter, no new scope relaxation; Redis reads non-mutating); refute-read stands in for the adversarial human check.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Owner sees rpm_current (ZCARD)=3 vs rpm_limit=60 and tpm_current (GET float)=150.0 vs tpm_limit=1000 — confirmed by test_owner_sees_counters_vs_limits.
- [x] Unused key (Redis up) reads 0 / 0.0, NOT null — confirmed by test_unused_key_reads_zero (distinguished from Redis-down null).
- [x] Redis unavailable → counters null, still 200 with limits — confirmed by test_redis_unavailable_counters_null (app.state.redis_client=None).
- [x] Cross-tenant isolation: only the caller's tenant keys; another tenant's key_id absent — confirmed by test_only_callers_tenant_keys (asserts U absent, not just T present).
- [x] READ-ONLY: ZCARD unchanged after the GET; no api_keys mutation — confirmed by test_read_only_no_mutation.
- [x] member→403 ERR_AUTH_FORBIDDEN, missing bearer→401 — confirmed by test_member_forbidden / test_missing_bearer_unauthorized.
- [x] Revoked keys excluded from the live view — confirmed by test_revoked_key_excluded.
- [x] Dashboard /keys panel renders current/limit per key; null→"—", null limit→"∞" — confirmed by tests/ratelimits.test.tsx (5 states incl. null-counter-renders-unknown).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — get_ratelimits on usage_router (already registered); _read_ratelimit_counters called by it; RatelimitsPanel imported + rendered unconditionally in KeysPage.tsx. All referenced (tests + refute confirm).
- [x] DEAD-CODE (code) — no orphaned symbol; the timeout constants / RatelimitItem / RatelimitsResponse all used.
- [x] SEMANTIC — n/a (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a
Reviewed by: autonomy:auto (auto-resolved; refute-read sonnet UPHELD 0.91, 0 blockers) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
