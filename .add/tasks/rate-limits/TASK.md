# TASK: App-level TPM/RPM sliding-window limits

slug: rate-limits · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-key RPM/TPM sliding-window rate limits with Redis Lua atomicity

Framings weighed:
  - Redis Lua sliding-window ZSET per key (chosen) — atomic check-and-record in a single
    Lua script avoids the TOCTOU race between reading the window count and recording the
    new request; uses an ordered set of (score=timestamp_ms, member=uuid7) to evict
    expired entries and count the window in one round-trip. Alternative considered:
    token-bucket in Redis (INCR + TTL) — simpler but does not give a precise "time until
    next slot frees" for Retry-After because the bucket refill is not tied to the oldest
    entry's expiry. Alternative considered: fixed-window INCR with previous-window weighted
    average (LiteLLM ParallelRequestLimiter pattern) — reduces Lua complexity but the
    two-key interaction is less atomic and Retry-After estimation is approximate.
    CHOSEN: ZSET sliding window — precise Retry-After, fully atomic, single Lua round-trip.
    Rationale for Lua over pipeline: ZADD + ZREMRANGEBYSCORE + ZCARD must be atomic; any
    gap between eviction and count allows burst over-admission under concurrency.

  Lua script design (ZSET sliding window, 60-second window):
    KEYS[1] = rate-limit key (e.g. "ratelimit:rpm:{key_id}")
    ARGV[1] = now_ms  (current epoch milliseconds, integer string)
    ARGV[2] = window_ms = 60000
    ARGV[3] = limit  (integer max requests per window)
    ARGV[4] = member  (unique per-call identifier, e.g. uuid4 hex; prevents ZADD NX collision)
    Script:
      1. ZREMRANGEBYSCORE KEYS[1] 0 (now_ms - window_ms)   — evict expired entries
      2. count = ZCARD KEYS[1]
      3. if count >= limit: return [0, oldest_score]       — denied; oldest_score for Retry-After
      4. ZADD KEYS[1] now_ms ARGV[4]                       — record this request
      5. EXPIRE KEYS[1] 60 + 1                             — TTL = window + 1s buffer (auto-GC)
      6. return [1, 0]                                     — admitted
    Retry-After = ceil((oldest_score + window_ms - now_ms) / 1000) seconds, min 1.
    oldest_score is the score of ZRANGE KEYS[1] 0 0 WITHSCORES (lowest = oldest).
    The script returns oldest_score on denial so the caller can compute Retry-After without
    a second round-trip.
    Lua scripts loaded once at startup via redis.asyncio script.register_script() —
    no SCRIPT LOAD/EVALSHA management required; redis-py's register_script() handles caching.

  TPM admission semantics (pre-flight with post-hoc accounting — the LiteLLM pattern):
    RPM: checked and recorded atomically in the Lua ZSET script on EVERY request (pre-flight).
    TPM: the window accumulates ACTUAL tokens (from usage records post-stream). Pre-flight
         admission checks the CURRENT accumulated token count in the window against tpm_limit
         (same ZSET but members carry token counts — see TPM Lua design below).
         Bounded overshoot: a streaming request that passes the pre-flight TPM check may
         push the window total beyond tpm_limit by at most one request's token consumption
         (identical to the v1 budget overage decision in PROJECT.md Key Decisions). The
         maximum overshoot is bounded by the model's max_tokens cap (~4k–128k per request).
         This is a deliberate availability-over-strictness tradeoff: synchronous post-stream
         enforcement would require holding the response until token count is known, breaking
         streaming UX. Document as a Key Decision.

  TPM Lua design (ZSET accumulator, 60-second window):
    KEYS[1] = "ratelimit:tpm:{key_id}"
    ARGV[1] = now_ms
    ARGV[2] = window_ms = 60000
    ARGV[3] = tpm_limit  (integer max tokens per window)
    ARGV[4] = token_count  (tokens to add; 0 on pre-flight check, >0 on post-stream record)
    ARGV[5] = member  (unique per-call identifier)
    Script (dual-mode: check-only when token_count==0, check-and-record when >0):
      1. ZREMRANGEBYSCORE KEYS[1] 0 (now_ms - window_ms)
      2. current_tokens = sum of all scores in window (ZSCORE of each member OR
         use member="{tokens}:{uuid}" and parse; CHOSEN: member encodes token count as
         integer prefix, score = now_ms, member = "{token_count}:{uuid4}")
         NOTE: ZRANGEBYSCORE KEYS[1] -inf +inf returns members; sum is parsed client-side
         after the check; or use a separate Redis key "ratelimit:tpm_sum:{key_id}" as an
         INCRBYFLOAT accumulator alongside the ZSET for O(1) sum access.
         FINAL DESIGN: use a companion INCRBYFLOAT counter "ratelimit:tpm_sum:{key_id}"
         (same TTL). Lua:
           a. ZREMRANGEBYSCORE + re-sum: not efficient for large sets.
           b. Companion sum key: INCRBYFLOAT is O(1); ZREMRANGEBYSCORE evicts and we also
              decrement the sum by the evicted members' values.
         CHOSEN FINAL: hybrid — ZSET stores (score=now_ms, member="{tokens}:{uuid}");
         on eviction, parse evicted members and subtract from sum key. This keeps sum accurate.
         Lua script returns sum after eviction.
      Implementation note: the Lua script handles both the eviction+sum-correction and the
      admission check. Member format: "{token_count_int}:{uuid_hex}" — parseable in Lua with
      string.match.
    Pre-flight (token_count=0): check window sum >= tpm_limit → deny or admit.
    Post-stream record (token_count=N): always records (no deny); TTL refreshed.
    Retry-After for TPM: same formula as RPM — use the oldest entry's score in the ZSET.

  Fail-open on Redis outage (⚠ deliberate deviation from MILESTONE §24 "fail-closed for hard limits"):
    The MILESTONE shared decision states "fail-closed for hard limits (402/429)".
    Rate limits are declared as hard limits in the milestone scope line.
    HOWEVER: the v1 budget precedent (PROJECT.md Key Decisions 2026-06-10, RedisBudgetGuard)
    established fail-open advisory counters for spend enforcement because "availability over
    strictness" is the accepted tradeoff when the outer Envoy 50/s backstop still bounds
    catastrophic abuse. Rate-limit counters are stored exclusively in Redis (no DB fallback),
    making fail-closed infeasible without blocking ALL traffic during Redis outages.
    ORCHESTRATOR DECISION: rate limits fail-OPEN on Redis outage — consistent with budget
    counter precedent; Envoy 50/s edge DDoS backstop remains as the abuse floor.
    RATIONALE recorded here and ⚠-flagged at freeze per ADD rules.
    Cost if wrong (fail-open): during a Redis outage, per-key rate limits are not enforced;
    a misbehaving client can burst beyond their RPM/TPM limit until Redis recovers. The
    Envoy 50/s per-connection backstop still applies.
    Cost if fail-closed: ALL completions blocked during any Redis blip, regardless of whether
    the tenant has rate limits set — severe availability penalty for the common case (Redis up).

  Scope boundary — per KEY only (not per tenant, not per model):
    This task implements per-KEY rpm_limit and tpm_limit only.
    Tenant-level and model-level limits are explicitly DEFERRED (see MILESTONE v3 scope line
    "per key/tenant/model" — amended understanding: per-KEY first, hierarchy later).
    The Redis key namespace and Lua script parameters are designed to be extensible:
    ratelimit:rpm:{key_id} → later: ratelimit:rpm:tenant:{tenant_id}, ratelimit:rpm:model:{model_id}
    The most-specific-wins hierarchy (key < tenant < model-specific per MILESTONE §36) will
    be layered in a follow-on task without changing the Lua script shape.
    NOTE: this amendment is recorded as a living-doc update for the MILESTONE.

<must>
  ### Lifecycle fields — storage & CRUD (additive to key-governance contract)
  - M1  POST /admin/keys accepts optional fields: rpm_limit (positive integer or null),
        tpm_limit (positive integer or null). These extend the key-governance frozen
        contract ADDITIVELY — all existing fields and codes unchanged.
  - M2  PATCH /admin/keys/{key_id} updates rpm_limit and/or tpm_limit on an active key;
        same semantics as key-governance PATCH (null = unlimited, omit = no change).
  - M3  GET /admin/keys response items carry rpm_limit (integer | null) and
        tpm_limit (integer | null).
  - M4  The api_keys table gains ADDITIVE columns (new Alembic migration, revises b1e3f7c9d2a4):
          rpm_limit  INTEGER  NULL  CHECK (rpm_limit > 0)
          tpm_limit  INTEGER  NULL  CHECK (tpm_limit > 0)
        No backfill; existing rows default to NULL (unlimited).

  ### Enforcement on /v1/chat/completions pre-flight
  - M5  If the authenticated key has a non-null rpm_limit: run the Redis Lua sliding-window
        RPM check BEFORE calling upstream. If the window count >= rpm_limit → 429
        ERR_RATE_LIMITED with Retry-After header (integer seconds, min 1).
  - M6  If the authenticated key has a non-null tpm_limit: run the Redis Lua TPM pre-flight
        check BEFORE calling upstream. If the window token sum >= tpm_limit → 429
        ERR_RATE_LIMITED with Retry-After header.
  - M7  RPM check and TPM pre-flight check run in sequence (RPM first, then TPM). If EITHER
        fires, 429 is returned. The Retry-After value is derived from the triggering limiter's
        window state. If both would trigger, RPM wins (first checked).
  - M8  After a successful (non-rejected) streaming or non-streaming completion, the
        UsageRecorder records the actual token count into the TPM ZSET window for the key.
        This is the "post-hoc accounting" half of the LiteLLM pattern.
  - M9  Null rpm_limit → no RPM check performed. Null tpm_limit → no TPM check or record
        performed. Keys without limits pass through at full speed.
  - M10 The rate-limit check is positioned AFTER authentication and governance enforcement
        (expiry, allowlist, budget) but BEFORE upstream call — same chokepoint as _enforce_governance.
  - M11 Redis Lua scripts are registered once at app startup (register_script() on the
        redis.asyncio client). The scripts handle atomic eviction + count + record.
  - M12 Redis key naming:
          RPM window:  ratelimit:rpm:{key_id}        (ZSET, TTL = 61s)
          TPM window:  ratelimit:tpm:{key_id}        (ZSET, TTL = 61s)
          TPM sum:     ratelimit:tpm_sum:{key_id}    (string/float, TTL = 61s)
        All three keys scoped to the key_id only (no tenant or model in the key — extensible).
  - M13 Retry-After semantics: integer seconds until the oldest entry in the window expires.
        Formula: ceil((oldest_entry_timestamp_ms + 60000 - now_ms) / 1000), minimum 1.
        Sent as the Retry-After HTTP response header (integer, per RFC 7231).
  - M14 On Redis outage (any exception from the Lua script execution): fail-open — the
        request is admitted as if no rate limit exists. Log a warning with the key_id.
        (See fail-open rationale in framings above.)
  - M15 The 429 response body is RFC 9457 problem+json:
          {"code": "ERR_RATE_LIMITED", "status": 429, "title": "Rate limit exceeded",
           "detail": "RPM limit X exceeded for key {key_id}"}  (detail is informational)
        The Retry-After header is set on the HTTP response (not in the body).
  - M16 A key with rpm_limit=null but tpm_limit set (or vice versa) enforces only the
        configured limit. Both null = unlimited (M9).
</must>

<reject>
  - R1  POST /admin/keys with rpm_limit = 0 -> "ERR_PAYLOAD_INVALID" (422)
        (zero is not a valid limit; use null for unlimited)
  - R2  POST /admin/keys with rpm_limit < 0 -> "ERR_PAYLOAD_INVALID" (422)
  - R3  POST /admin/keys with tpm_limit = 0 -> "ERR_PAYLOAD_INVALID" (422)
  - R4  POST /admin/keys with tpm_limit < 0 -> "ERR_PAYLOAD_INVALID" (422)
  - R5  POST /v1/chat/completions with a key whose RPM window is full ->
        "ERR_RATE_LIMITED" (429) + Retry-After header
  - R6  POST /v1/chat/completions with a key whose TPM window token sum >= tpm_limit ->
        "ERR_RATE_LIMITED" (429) + Retry-After header
  - R7  POST /v1/chat/completions with a rate-limited key does NOT expose rate-limit
        state of other keys (no cross-key leak in the 429 response or headers)
</reject>

<after>
  - After M1/M5: api_keys row carries rpm_limit and tpm_limit; GET /admin/keys echoes them.
  - After R5: the RPM-limited key receives 429 with Retry-After; a sibling key with its own
    (unfilled) window is unaffected and receives 200 on its own completions request.
  - After R6: TPM-limited key receives 429 with Retry-After; no upstream call, no usage_record.
  - After M14: Redis outage → request admitted (fail-open); limit not enforced; warning logged.
  - After M9: keys with null rpm_limit and null tpm_limit pass through at full speed.
  - After M8: post-completion token recording does not block the response (fire-and-forget
    or background; must not fail the request on Redis error).
  - After M15: 429 response carries Retry-After header as integer seconds ≥ 1.
  - After M10: expiry/allowlist/budget rejections (401/402/403) are unaffected — rate limiting
    only fires AFTER those checks pass.
</after>

<assumptions>
  ⚠ A1 [LOWEST CONFIDENCE — cost: architecture rework] The rate-limiter port and Lua-script
     infrastructure will be injected via app.state (same pattern as budget_guard). The
     CompletionUseCase will grow a rate_limiter: RateLimiter optional constructor arg.
     Risk: if the existing CompletionUseCase.__init__ signature is pinned by frozen proxy-
     completions tests (proxy tests that assert on constructor args directly), adding an
     optional arg could break them. Mitigation: the proxy-completions contract pins behavior
     (HTTP response shape), not constructor signature; Python optional args are backward-
     compatible. MUST confirm by reading frozen proxy test files before build.
     If wrong: need a separate factory wrapper rather than extending CompletionUseCase.

  ⚠ A2 [HIGH CONCERN — cost: TPM post-stream record never fires]
     The post-stream TPM token recording (M8) requires that after a streaming completion
     the token count (from the SSE usage frame) is written to the TPM ZSET. The current
     _fire_record() call in CompletionUseCase already passes usage data to UsageRecorder.
     The rate-limiter's record_tpm() call must be triggered from the same post-stream path,
     AFTER the SSE is fully consumed. If wired incorrectly, the TPM window never fills and
     TPM limits are never enforced (same class of risk as key-governance A2 for per-key
     spend counter). MUST be explicitly in the Build safety rule.
     If wrong: TPM enforcement passes pre-flight but never accumulates; limit is silent no-op.

  - A3 [Lua script member uniqueness] Each ZADD entry uses a UUID4 hex as the member suffix
     to prevent NX collisions when two requests arrive at the same millisecond timestamp.
     Alternative: use "{now_ms}:{uuid}" as the FULL key (timestamp as member, not score).
     CHOSEN: score=timestamp_ms, member=uuid4 — allows ZREMRANGEBYSCORE by score efficiently.
     Cost if wrong: at sub-millisecond concurrency, two ZADDs with the same score but same
     member would deduplicate (last-write wins) — one request would not be counted.
     The UUID4 member prevents this. Cost: low (UUID4 generation is cheap).

  - A4 [TPM sum key race during eviction] When the Lua script evicts old ZSET entries it
     must also decrement the tpm_sum key by the token counts of evicted members. If Redis
     crashes between the ZREMRANGEBYSCORE and the sum DECRBY, the sum diverges.
     Mitigation: the Lua script runs atomically; there is no inter-command gap. The only
     failure is a full Redis crash mid-Lua (which means Redis is down — fail-open anyway).
     Cost if wrong: tpm_sum drifts low (overestimates available capacity = slightly liberal).
     Acceptable: same class of overage as the budget pattern.

  - A5 [Window granularity fixed at 60s] The 60-second window is the only granularity in
     this task. Finer-grained windows (per-second burst) are NOT in scope. The Lua script
     accepts window_ms as a parameter so the window can be changed without a code change,
     but the API contracts (Retry-After formula, Redis key TTL) all assume 60s.
     Cost if wrong: customers may expect per-second burst limits — deferred to a follow-on task.

  - A6 [Concurrency admitted ≤ limit + in-flight] Under concurrent burst (N asyncio tasks
     simultaneously), the atomic Lua script ensures at most `limit` entries are admitted to
     the ZSET per 60-second window. In-flight requests that passed the RPM check BEFORE the
     window filled are completing concurrently — they are already admitted and not counted
     back. The promise is: admitted requests ≤ limit + min(concurrency, server connection
     limit). For the test, with N > limit concurrent requests and a fresh window, exactly
     `limit` should be admitted. The remaining N - limit receive 429.
     Cost if wrong: the test bound must be exact; any over-admission beyond `limit` is a bug.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first,
     the top two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1/M3: CRUD — RPM/TPM fields on create and list ─────────────────────────

Scenario: Create key with rpm_limit and tpm_limit persisted and echoed
  Given an owner-role JWT for tenant Acme
  When  POST /admin/keys with name="rate-key", rpm_limit=60, tpm_limit=100000
  Then  201 with rpm_limit=60, tpm_limit=100000 in response
  And   GET /admin/keys returns rpm_limit=60, tpm_limit=100000 on that key_id

Scenario: Create key with no rate-limit fields defaults to unlimited
  Given an owner-role JWT for tenant Acme
  When  POST /admin/keys with name="bare-key" (no rpm_limit, no tpm_limit)
  Then  201 and rpm_limit=null, tpm_limit=null in response
  And   GET /admin/keys shows null for both fields

# ── M2: PATCH updates rate-limit fields ──────────────────────────────────────

Scenario: PATCH updates rpm_limit and tpm_limit on an active key
  Given an owner-role JWT and an active key with rpm_limit=null, tpm_limit=null
  When  PATCH /admin/keys/{key_id} with rpm_limit=30, tpm_limit=50000
  Then  200 with rpm_limit=30, tpm_limit=50000
  And   GET /admin/keys echoes the updated values

# ── M5 / R5: RPM window full → 429 + Retry-After; sibling key unaffected ────

Scenario: Burst over RPM limit gets 429 + Retry-After; sibling key unaffected
  Given key_A has rpm_limit=2 and a fresh Redis window
  And   key_B has rpm_limit=null (unlimited) for the same tenant
  When  key_A sends 3 sequential POST /v1/chat/completions requests
  Then  first 2 requests return 200 (admitted, upstream called each time)
  And   3rd request returns 429 ERR_RATE_LIMITED
  And   3rd response has Retry-After header >= 1 (integer seconds)
  And   POST /v1/chat/completions with key_B returns 200 (sibling unaffected)

# ── M6 / R6: TPM window full → 429 + Retry-After ────────────────────────────

Scenario: TPM limit exceeded by accumulated token records → 429
  Given key_C has tpm_limit=100 and the Redis TPM window already records 95 tokens
  When  key_C sends POST /v1/chat/completions (pre-flight check sees sum >= tpm_limit)
  Then  429 ERR_RATE_LIMITED
  And   Retry-After header present (>= 1 second)
  And   upstream is never called (upstream.calls == 0)

# ── M14: Redis outage → fail-open ────────────────────────────────────────────

Scenario: Redis down — rate-limited key passes through (fail-open)
  Given key_D has rpm_limit=1 and Redis is simulated as unavailable
  When  POST /v1/chat/completions with key_D
  Then  200 (request admitted — fail-open; upstream called)
  And   no ERR_RATE_LIMITED returned

# ── M9: Null limits → unlimited ─────────────────────────────────────────────

Scenario: Key with null rpm_limit and null tpm_limit is never rate-limited
  Given key_E has rpm_limit=null, tpm_limit=null
  When  100 sequential POST /v1/chat/completions requests with key_E
  Then  all 100 return 200 (no 429)

# ── R7: No cross-key state leak in 429 ───────────────────────────────────────

Scenario: 429 response for rate-limited key does not expose other keys' state
  Given key_F has rpm_limit=1 (window full)
  And   key_G has rpm_limit=10 (window with 5 entries)
  When  POST /v1/chat/completions with key_F → 429
  Then  the 429 response body and headers contain no reference to key_G's state

# ── R1-R4: Input validation rejections ───────────────────────────────────────

Scenario: Create key with rpm_limit=0 rejected
  Given an owner JWT
  When  POST /admin/keys with rpm_limit=0
  Then  422 ERR_PAYLOAD_INVALID

Scenario: Create key with negative rpm_limit rejected
  Given an owner JWT
  When  POST /admin/keys with rpm_limit=-5
  Then  422 ERR_PAYLOAD_INVALID

Scenario: Create key with tpm_limit=0 rejected
  Given an owner JWT
  When  POST /admin/keys with tpm_limit=0
  Then  422 ERR_PAYLOAD_INVALID

Scenario: Create key with negative tpm_limit rejected
  Given an owner JWT
  When  POST /admin/keys with tpm_limit=-100
  Then  422 ERR_PAYLOAD_INVALID

# ── M10: Governance checks fire before rate limits ───────────────────────────

Scenario: Expired key gets 401 even if under rpm_limit (governance before rate limits)
  Given an expired key with rpm_limit=100 (window empty)
  When  POST /v1/chat/completions with that expired key
  Then  401 ERR_AUTH_KEY_EXPIRED (not 429 ERR_RATE_LIMITED)

# ── A6: Concurrency atomicity — admitted ≤ limit under burst ─────────────────

Scenario: Concurrent burst of N > rpm_limit requests — exactly limit admitted
  Given key_H has rpm_limit=5 and a fresh Redis window
  When  10 concurrent POST /v1/chat/completions requests fire simultaneously (asyncio.gather)
  Then  exactly 5 return 200 (admitted = limit)
  And   exactly 5 return 429 ERR_RATE_LIMITED
  And   admitted ≤ rpm_limit (no over-admission beyond the atomic limit)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/keys  (EXTENDED — additive to key-governance FROZEN contract)
  body additions (all fields optional; existing fields unchanged):
    "rpm_limit": integer | null   (optional; positive integer or null; null = unlimited)
    "tpm_limit": integer | null   (optional; positive integer or null; null = unlimited)
  201 additions:
    "rpm_limit": integer | null
    "tpm_limit": integer | null
  422 -> { "code": "ERR_PAYLOAD_INVALID" }  — zero or negative rpm_limit/tpm_limit
  NOTE: all existing key-governance fields (monthly_budget_usd, soft_budget_usd,
        expires_at, model_allowlist, rotated_from_key_id) and their error codes are
        UNCHANGED. This is a strictly additive extension.

PATCH /admin/keys/{key_id}  (EXTENDED — additive to key-governance FROZEN contract)
  body additions (all optional; omit = no change):
    "rpm_limit": integer | null
    "tpm_limit": integer | null
  200 -> KeyInfoResponse (with rpm_limit and tpm_limit fields added)
  All existing PATCH error codes unchanged (ERR_AUTH_FORBIDDEN, ERR_KEY_NOT_FOUND,
  ERR_PAYLOAD_INVALID).

GET /admin/keys  (EXTENDED — additive to key-governance FROZEN contract)
  200 item additions:
    "rpm_limit": integer | null   -- NEW
    "tpm_limit": integer | null   -- NEW

POST /v1/chat/completions  (RATE-LIMIT ENFORCEMENT — hot path, no new endpoint)
  auth: Bearer sk-<hex>.<secret>
  NEW:
  429 -> { "code": "ERR_RATE_LIMITED", "status": 429, "title": "Rate limit exceeded",
           "detail": "RPM limit {N} exceeded for key {key_id}" | "TPM limit {N} exceeded for key {key_id}" }
       + Retry-After: <integer seconds >= 1>   (HTTP response header)
  Enforcement order (M10): expiry → allowlist → budget → RPM check → TPM check → upstream.
  EXISTING 401/402/403 codes unchanged.

Schema DDL (additive migration — revises: b1e3f7c9d2a4):
  ALTER TABLE api_keys
    ADD COLUMN rpm_limit  INTEGER  NULL  CHECK (rpm_limit > 0),
    ADD COLUMN tpm_limit  INTEGER  NULL  CHECK (tpm_limit > 0);
  Downgrade: DROP COLUMN rpm_limit, DROP COLUMN tpm_limit; DROP CONSTRAINTs.

Migration revision: <next Alembic hash — generated at build time, revises b1e3f7c9d2a4>

Redis keys and semantics:
  RPM window ZSET:   ratelimit:rpm:{key_id}       score=epoch_ms  member=uuid4_hex  TTL=61s
  TPM window ZSET:   ratelimit:tpm:{key_id}       score=epoch_ms  member="{token_count}:{uuid4_hex}"  TTL=61s
  TPM sum counter:   ratelimit:tpm_sum:{key_id}   string (float)  TTL=61s
  Window duration:   60 000 ms (60 seconds)
  Lua atomicity:     ZREMRANGEBYSCORE + ZCARD (RPM) or sum-correction (TPM) + conditional ZADD
                     in a single Lua script; registered once at startup via register_script().

Retry-After formula:
  oldest_ms = score of ZRANGE {key} 0 0 WITHSCORES (lowest timestamp in window)
  retry_after_s = max(1, ceil((oldest_ms + 60000 - now_ms) / 1000))

Fail-open deviation from MILESTONE §24:
  ⚠ The MILESTONE states "fail-closed for hard limits (402/429)".
     Rate limits fail-OPEN on Redis outage (any exception from Lua execution).
     Rationale: availability-over-strictness; consistent with budget counter precedent
     (PROJECT.md Key Decisions 2026-06-10); Envoy 50/s edge backstop remains.
     This is a deliberate policy deviation, not an oversight.

Modules touched (hard boundary for the builder — no other modules):
  gateway/keys/domain/entities.py            -- add rpm_limit: int | None, tpm_limit: int | None to ApiKey, ApiKeyInfo, AuthzResult
  gateway/keys/infrastructure/orm.py         -- add rpm_limit, tpm_limit mapped_columns to ApiKeyRow
  gateway/keys/infrastructure/repository.py  -- extend create(), update() for new fields
  gateway/keys/api/schemas.py                -- extend CreateKeyRequest/Response, KeyInfoResponse, PatchKeyRequest
  gateway/proxy/application/use_cases.py     -- extend _enforce_governance() and CompletionUseCase.__init__ with rate_limiter
  gateway/proxy/api/deps.py                  -- wire rate_limiter from app.state.rate_limiter
  gateway/proxy/domain/ports.py             -- add RateLimiter Protocol
  gateway/rate_limits/                       -- NEW module (domain/ + application/ + infrastructure/)
    domain/ports.py                          -- RateLimiter Protocol
    domain/errors.py                         -- RateLimitExceededError (domain error)
    infrastructure/redis_lua_limiter.py      -- RedisLuaRateLimiter: Lua scripts, check_rpm(), check_tpm(), record_tpm()
  gateway/main.py                            -- wire RedisLuaRateLimiter to app.state.rate_limiter at startup
  apps/gateway/migrations/versions/<hash>_rate_limit_columns.py  -- new additive migration

TPM post-stream seam:
  After a completion or stream, the CompletionUseCase calls rate_limiter.record_tpm(key_id, tokens)
  (non-blocking; swallows Redis errors). This is the "post-hoc accounting" half.
  The existing _fire_record() + UsageRecorder path is unchanged; record_tpm() is a parallel call.
```

Status: FROZEN @ v3 — approved by Tin Dang (delegated auto mode, 2026-06-11; v3 roadmap confirmed "Proceed as drafted").
Least-sure flag surfaced at freeze:
⚠ [spec] fail-OPEN on Redis outage deliberately deviates from the original MILESTONE
  "fail-closed for hard limits" line — amended in the v3 living doc at this freeze with
  rationale (availability over strictness; Envoy 50/s edge backstop; v1 budget-counter
  precedent); cost if wrong: a Redis outage window admits over-limit traffic bounded only
  by the edge limit.
⚠ [contract] TPM accounting is pre-flight admission + post-stream accounting — a burst of
  concurrent streams can overshoot tpm_limit by the in-flight tokens (same bounded-overage
  shape as the budget decision in PROJECT.md); cost if wrong: tighter SLAs need reservation
  semantics (a future task), not this window design.
⚠ [test] (A1, RESOLVED at freeze) CompletionUseCase is constructed positionally ONLY in
  src deps.py (orchestrator grep, 2026-06-11) — frozen tests exercise HTTP, so an optional
  rate_limiter kwarg is additive-safe.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

⚠ FREEZE FLAG CANDIDATES (lowest-confidence first — block approval until resolved):

1. [spec/contract] A1 — CompletionUseCase extension: adding an optional rate_limiter
   constructor arg must not break any frozen proxy-completions tests that assert on
   constructor behavior. MUST confirm (by reading tests/proxy/) that no frozen test
   constructs CompletionUseCase directly with exhaustive positional args. If it does,
   a keyword-only default=None arg is still safe in Python, but the test would fail
   if it used positional ordering. RISK level: medium.

2. [spec] Fail-open deviation from MILESTONE §24 "fail-closed for hard limits":
   Rate limits failing OPEN on Redis outage explicitly contradicts the milestone's
   shared decision. This is the most significant policy deviation in the bundle.
   The rationale (availability-over-strictness + budget precedent + Envoy backstop)
   is sound, but the orchestrator must confirm before freezing that this deviation
   is acceptable and should be recorded in the MILESTONE living doc as an amendment.
   Cost if the deviation is not accepted: rate limits must be fail-closed, which
   requires a DB-backed fallback counter (out of scope for this task) or a hard
   "Redis required" infrastructure constraint.

3. [contract] TPM Lua sum-correction complexity: the hybrid ZSET + companion sum key
   requires that Lua correctly parses member strings ("{token_count}:{uuid}") to
   subtract evicted tokens from the sum key. This is the most complex part of the
   Lua script. If the member parsing is wrong, the sum diverges and TPM enforcement
   is either too strict (over-subtraction) or too lenient (under-subtraction).
   Alternative: drop the companion sum key and compute the sum by SCANNING the ZSET
   members client-side after the Lua call — simpler but an extra round-trip.
   MUST decide before build: hybrid Lua sum-correction OR client-side sum scan.
   Recommendation: client-side sum scan for correctness simplicity; the extra GET
   on the ZSET for TPM is acceptable (one extra round-trip per request, only when
   tpm_limit is set).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new code paths (Lua limiter, enforcement branches, CRUD)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_create_key_with_rpm_and_tpm_limits:
      arrange signup+login / act POST /admin/keys with rpm_limit=60, tpm_limit=100000 /
      assert 201 + fields in response + GET echoes them
  - test_create_key_defaults_rpm_tpm_to_null:
      arrange signup+login / act POST /admin/keys bare /
      assert rpm_limit=null, tpm_limit=null
  - test_patch_key_updates_rpm_and_tpm:
      arrange active key (null limits) / act PATCH with rpm_limit=30, tpm_limit=50000 /
      assert 200 + fields updated + GET echoes
  - test_rpm_limit_exceeded_returns_429_with_retry_after:
      arrange key rpm_limit=2, fresh Redis / act 3 sequential completions /
      assert first 2 → 200, third → 429 ERR_RATE_LIMITED + Retry-After >= 1
  - test_sibling_key_unaffected_by_rpm_limit:
      arrange key_A rpm_limit=2 (window full), key_B rpm_limit=null /
      act completion with key_B / assert 200 (key_B unaffected)
  - test_tpm_limit_exceeded_returns_429_with_retry_after:
      arrange key tpm_limit=100, pre-seed TPM sum at 95 tokens /
      act completion (pre-flight sees >= limit) / assert 429 + Retry-After + upstream.calls==0
  - test_redis_down_fails_open:
      arrange key rpm_limit=1, BrokenRedis injected /
      act completion / assert 200 (fail-open)
  - test_null_limits_never_rate_limited:
      arrange key rpm_limit=null, tpm_limit=null /
      act 5 sequential completions / assert all 200
  - test_429_does_not_expose_sibling_key_state:
      arrange key_F rpm_limit=1 (window full), key_G rpm_limit=10 (partial) /
      act completion with key_F / assert 429 body and headers contain no reference to key_G
  - test_rpm_limit_zero_rejected:
      act POST /admin/keys with rpm_limit=0 / assert 422 ERR_PAYLOAD_INVALID
  - test_rpm_limit_negative_rejected:
      act POST /admin/keys with rpm_limit=-5 / assert 422 ERR_PAYLOAD_INVALID
  - test_tpm_limit_zero_rejected:
      act POST /admin/keys with tpm_limit=0 / assert 422 ERR_PAYLOAD_INVALID
  - test_tpm_limit_negative_rejected:
      act POST /admin/keys with tpm_limit=-100 / assert 422 ERR_PAYLOAD_INVALID
  - test_expired_key_gets_401_not_429:
      arrange expired key (expires_at in past) with rpm_limit=100 (fresh window) /
      act completion / assert 401 ERR_AUTH_KEY_EXPIRED (governance fires before rate limit)
  - test_concurrent_burst_admits_exactly_limit:
      arrange key rpm_limit=5, fresh Redis /
      act asyncio.gather of 10 concurrent completions /
      assert admitted == 5 (status 200 count), rejected == 5 (status 429 count),
      admitted <= rpm_limit (no over-admission)
</test_plan>

Tests live in: `apps/gateway/tests/rate_limits/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `apps/gateway/tests/rate_limits/` -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  - Lua scripts: ZREMRANGEBYSCORE + ZCARD + conditional ZADD must execute atomically; never
    split into pipeline steps.
  - TPM record_tpm() must fire post-stream (after SSE is consumed), not pre-stream; wiring
    it pre-stream would record tokens for rejected or incomplete requests.
  - Fail-open is non-negotiable: any Exception from Redis/Lua → log + admit (never propagate).
  - Cross-key isolation: the Lua script uses KEYS[1] scoped to the single key_id; no scan,
    no KEYS pattern, no cross-key references in the script.
  - TPM post-stream seam (A2): record_tpm() MUST be called from the _wrapped() generator
    in CompletionUseCase.stream() AND from complete() after upstream.complete() returns —
    if omitted, TPM window never fills and tpm_limit is a silent no-op.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ERR_RATE_LIMITED rate per key · Retry-After distribution ·
  Redis Lua script latency (p99) · fail-open event rate (warns logged) · RPM window utilization
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
