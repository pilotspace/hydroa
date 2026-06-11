# TASK: Opt-in exact-match Redis response cache

slug: response-caching · created: 2026-06-11 · stage: production
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Opt-in exact-match Redis response cache for non-streaming completions

Framings weighed:
- **per-key-or-tenant opt-in exact-match cache** (chosen): caching is an explicit opt-in flag
  per api_keys row OR per tenants row (effective = key.cache_enabled OR tenant.cache_enabled,
  default both false). Exact-match key = sha256 hash of canonicalized payload. All governance
  checks run before cache lookup. Streaming bypassed entirely. Cost-0 usage rows written on hits
  so request counts stay honest. Prometheus counter surfaces hit/miss/bypass. This matches the
  v4 MILESTONE.md shared decision exactly and keeps billing accuracy first.
- always-on global cache (rejected): would make tenants subject to each other's cached payloads
  across different API keys — violated tenant isolation; "billing accuracy first" dictates opt-in.
- separate cache service / embedding-based semantic cache (rejected): out of scope for v4 per
  MILESTONE.md ("semantic caching: v5; needs embeddings decision"); exact-match is simpler,
  auditable, and already has the Redis infra.
- per-request opt-in via payload field (rejected): exposes caching semantics into the AI
  protocol surface; per-key/tenant toggle is the right governance level — callers should not
  control caching behavior per request (only bypass via Cache-Control: no-cache for emergencies).

Must:
<must>
  - api_keys gains a `cache_enabled BOOLEAN NOT NULL DEFAULT false` column (additive migration
    after a4f8c2e1b9d3). POST /admin/keys and PATCH /admin/keys/{key_id} accept `cache_enabled`
    (bool); response body echoes it. Default false.
  - tenants gains a `cache_enabled BOOLEAN NOT NULL DEFAULT false` column (same migration).
    A new endpoint PUT /admin/cache {"enabled": bool} sets tenant-level cache toggle; returns
    200 {"enabled": bool}; requires owner or admin role (403 for member). GET /admin/cache
    returns {"enabled": bool}; any authenticated role.
  - Effective cache enable = api_keys.cache_enabled OR tenants.cache_enabled. This is evaluated
    at AuthzResult population time (zero extra DB reads — rides the existing authz path). The
    `cache_enabled` bool rides AuthzResult as an additive field (default False).
  - AuthzResult gains `cache_enabled: bool = False` (additive). AuthzUseCase.execute() populates
    it from the api_keys row. Tenant-level override is resolved separately; see contract for the
    join strategy.
  - Cache applies to NON-STREAMING completions only. When stream=true (or stream omitted and
    effectively false is fine, but the key decision: stream=true path), the cache layer is
    completely bypassed — no read, no write. This is explicit in the stream() path.
  - Cache key: `resp-cache:{tenant_id}:{sha256(canonical_json)}` where canonical_json =
    `json.dumps(obj, sort_keys=True, separators=(',', ':'))` over the dict of ONLY the
    forwardable fields that affect output:
    {model, messages, temperature, top_p, max_tokens, stop, n, presence_penalty,
     frequency_penalty, seed}. Fields absent from the request body are EXCLUDED from the
    canonical dict (not inserted as null). `stream`, `user`, and any other metadata fields are
    excluded. Tenant isolation is enforced by the `{tenant_id}` prefix in the Redis key.
  - TTL for stored cache entries: GATEWAY_CACHE_TTL_SECONDS setting (int, default 300).
    The Setting is additive in gateway.core.config.Settings.
  - Cache lookup behavior (non-streaming, effective cache enabled):
      MISS: call upstream; on 200 response, store body in Redis with TTL (fire-and-forget SET;
            storage failure is logged + swallowed — never fails the request); add response
            header X-Cache: miss; record usage row normally (cost computed from real tokens).
      HIT:  return cached body verbatim; add response header X-Cache: hit; record usage row
            with cached=true in raw field, cost_usd=0, token counts from cached body; do NOT
            increment advisory spend counters on hits (cost_usd=0 INCRBYFLOAT is a no-op in
            the recorder — the recorder's spend-counter logic only runs when cost_usd > 0, so
            this is automatically correct; no special casing needed).
      4xx/5xx responses from upstream are NEVER stored in cache.
  - Bypass: request header `Cache-Control: no-cache` (canonical) forces the upstream call even
    when effective cache is enabled. The fresh 200 response IS stored after a bypass (re-warm).
    Bypass adds response header X-Cache: bypass.
  - When effective cache is NOT enabled, the X-Cache response header is ABSENT.
  - Governance runs BEFORE cache lookup: expiry, allowlist, catalog, per-key budget, team budget,
    tenant budget, rate limits all enforce normally. A cache hit does NOT bypass governance.
    Rate-limit RPM counter is incremented for cache hits (the request reached the gateway).
    TPM accounting uses the cached token counts on a hit.
  - Metrics: Prometheus counter `gateway_cache_events_total{result="hit"|"miss"|"bypass"}`
    added to MetricsRegistry, exposed at /internal/metrics.
  - PATCH /admin/keys/{key_id} with {"cache_enabled": true/false} toggles the flag; echoed in
    response. Absent from body = no change (consistent with the existing PATCH sentinel pattern).
  - Only 200 responses from upstream are cached. Upstream 4xx/5xx pass through verbatim and are
    NOT stored.
</must>

Reject:
<reject>
  - Proxy completion with stream=true and cache-enabled key → no X-Cache header, upstream called
    normally, no cache read/write (streaming never cached in v4).
  - PUT /admin/cache with member-role JWT → "ERR_AUTH_FORBIDDEN" (403).
  - A key from a different tenant using an identical payload → cache MISS (tenant isolation via
    tenant_id prefix in cache key).
  - A non-200 upstream response (e.g. 400 or 500) → response NOT stored; next identical request
    calls upstream again.
  - A request with header Cache-Control: no-cache when cache is enabled → upstream called (bypass);
    fresh 200 stored; X-Cache: bypass on response.
  - A governance-blocked request (e.g. disabled model, expired key) even when cache is warm →
    governance error returned (403 ERR_MODEL_DISABLED, 401, etc.), NOT the cached body.
</reject>

After:
<after>
  - api_keys.cache_enabled and tenants.cache_enabled columns exist with NOT NULL DEFAULT false.
  - POST /admin/keys with {"cache_enabled": true} creates a key with caching on; PATCH toggles it.
  - GET /admin/cache returns {"enabled": bool} reflecting tenant-level toggle.
  - An identical non-streaming completion with a cache-enabled key: second request returns
    upstream body verbatim, X-Cache: hit, usage row with cached=true in raw + cost_usd=0 +
    real token counts; upstream called exactly once across both requests.
  - Advisory spend counters unchanged after a cache hit (cost_usd=0 path in recorder).
  - Stream request with cache-enabled key: behaves identically to no-cache; no X-Cache header.
  - Prometheus counter gateway_cache_events_total increments per hit/miss/bypass event.
  - Bypass header forces upstream call; response stored; next identical request (no bypass) hits
    the freshly stored cache.
  - Different payload → miss (upstream called again).
  - Different tenant, identical payload → miss (tenant isolation).
  - Model disabled after cache warm → 403 on next request, NOT cached-200.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ CACHE STORAGE IN CompletionUseCase vs. a dedicated CacheLayer class [spec]: The v4
    persona says "inject cache as a collaborator into CompletionUseCase via constructor" vs.
    "weave cache logic inline". Chosen: introduce a `ResponseCache` protocol (domain port) and
    inject it into CompletionUseCase. This keeps the use case testable without Redis and avoids
    a God-class. If wrong (inline is mandated by a future review): refactor is mechanical
    (extract port), tests need no changes since they assert behavior, not structure. Confidence:
    0.88; lowest because the persona guidance is silent on exact layering. Cost if wrong:
    medium refactor contained within CompletionUseCase + main.py wiring. [spec]

  ⚠ TENANT-LEVEL cache_enabled FETCH STRATEGY [contract]: AuthzResult already carries per-key
    fields via the existing get_by_id() LEFT JOIN chain. Adding tenant.cache_enabled to the same
    query requires extending the LEFT JOIN to include tenants, OR doing a second SELECT on the
    hot path (violates zero-extra-DB-reads). The contract mandates a second LEFT JOIN tenant
    row to be included in the get_by_id() query result — this extends the existing LEFT JOIN
    teams pattern. The effective value = row.cache_enabled OR tenant_row.cache_enabled is
    computed at repository level and stored as a single bool in AuthzResult.cache_enabled.
    Risk: the existing get_by_id() already does a LEFT JOIN teams; adding LEFT JOIN tenants is
    additive. If wrong (e.g. tenants row unavailable = treat as false, which is the safe
    default anyway): fail-open, cache simply disabled. Confidence: 0.90. [contract]

  - Cache TTL of 300s is fixed by config default; no per-key TTL override in v4. Deferred.
    Cost if wrong: additive PATCH /admin/keys field in a future task.

  - Storing only the upstream 200 JSON body verbatim (no envelope wrapping) is correct because
    the proxy returns the upstream body as-is on 200 (confirmed by proxy-completions contract).
    Confidence: 0.98.

  - Fire-and-forget Redis SET on cache store (never awaited after kickoff, swallows errors) is
    consistent with the advisory-counter and fire-and-forget recording pattern used throughout
    this codebase. Confidence: 0.98.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — default off — identical requests call upstream twice
  Given a key with cache_enabled=false and tenant cache_enabled=false
  When two identical non-streaming completion requests are made
  Then upstream is called twice, no X-Cache header is present on either response
  And both usage rows have normal (non-zero) cost

Scenario: S2 — key-enabled cache hit flow
  Given a key with cache_enabled=true
  When two identical non-streaming completion requests are made
  Then upstream is called exactly once across both requests
  And the second response has X-Cache: hit and body identical to the first
  And the second usage row has cached=true in raw, cost_usd=0, token counts from cached body
  And advisory spend counters are NOT incremented after the second request

Scenario: S3 — tenant-enabled cache hit flow
  Given a key with cache_enabled=false and tenant cache_enabled=true
  When two identical non-streaming completion requests are made
  Then upstream is called exactly once across both requests
  And the second response has X-Cache: hit
  And the second usage row has cached=true in raw, cost_usd=0

Scenario: S4 — bypass header forces upstream; re-stores response
  Given a key with cache_enabled=true and a warm cache for payload P
  When a request for payload P with header Cache-Control: no-cache is made
  Then upstream is called and X-Cache: bypass is on the response
  And the response is stored in cache (a subsequent identical request without bypass gets X-Cache: hit)

Scenario: S5 — different payload → cache miss
  Given a key with cache_enabled=true
  When payload A is sent twice and then payload B (different messages) is sent
  Then payload A hits cache on second request
  And payload B results in a cache miss (upstream called; X-Cache: miss)

Scenario: S6 — different tenant, identical payload → cache miss
  Given two tenants each with cache-enabled keys and an identical payload P
  When tenant A warms the cache with P
  And tenant B sends payload P
  Then tenant B receives a cache miss (upstream called for tenant B's request)

Scenario: S7 — upstream 4xx is NOT cached
  Given a key with cache_enabled=true and upstream returns 400
  When the same request is made twice
  Then upstream is called both times (4xx never stored)
  And neither response has X-Cache: hit

Scenario: S8 — governance enforced before cache lookup; disabled model blocks even when cache warm
  Given a key with cache_enabled=true, cache warmed for model M
  When model M is disabled for the tenant after warming
  And the same request is made again
  Then the response is 403 ERR_MODEL_DISABLED (governance blocks before cache read)
  And X-Cache header is absent (governance fired first, cache never read)

Scenario: S9 — streaming request bypasses cache entirely
  Given a key with cache_enabled=true
  When a streaming completion (stream=true) is made twice with identical payload
  Then upstream is called both times (no cache read or write)
  And neither response has an X-Cache header

Scenario: S10 — Prometheus counter increments
  Given a key with cache_enabled=true
  When a request results in a cache miss (first request)
  Then gateway_cache_events_total{result="miss"} is incremented by 1
  When a request results in a cache hit (second identical request)
  Then gateway_cache_events_total{result="hit"} is incremented by 1
  When a request with Cache-Control: no-cache is made
  Then gateway_cache_events_total{result="bypass"} is incremented by 1

Scenario: S11 — PATCH key cache_enabled toggle
  Given an owner JWT and an existing key with cache_enabled=false
  When PATCH /admin/keys/{key_id} with {"cache_enabled": true} is sent
  Then the response is 200 with cache_enabled=true in the body
  And the DB row has cache_enabled=true

Scenario: S12 — member role 403 on PUT /admin/cache
  Given a member-role JWT for a tenant
  When PUT /admin/cache {"enabled": true} is sent
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And tenant cache_enabled is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/keys           body: { name, cache_enabled?: bool, ...existing fields }
  201 -> { key_id, key, name, cache_enabled: bool, ...existing fields }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }         # member role

PATCH /admin/keys/{key_id} body: { cache_enabled?: bool, ...existing fields }
  200 -> { key_id, name, cache_enabled: bool, ...existing fields }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }         # member role
  404 -> { code: "ERR_KEY_NOT_FOUND" }          # key not found / cross-tenant

GET /admin/cache
  200 -> { enabled: bool }                       # tenant-level cache toggle
  401 -> { code: "ERR_AUTH_INVALID_KEY" }        # not authenticated

PUT /admin/cache           body: { enabled: bool }
  200 -> { enabled: bool }                       # echoes persisted value
  403 -> { code: "ERR_AUTH_FORBIDDEN" }          # member role
  422 -> { code: "ERR_PAYLOAD_INVALID" }         # enabled field missing or wrong type

POST /v1/chat/completions (non-streaming, effective cache enabled)
  MISS:
    200 -> upstream body verbatim
    X-Cache: miss
  HIT:
    200 -> cached upstream body verbatim (byte-identical)
    X-Cache: hit
  BYPASS (Cache-Control: no-cache):
    200 -> fresh upstream body verbatim
    X-Cache: bypass
  WHEN cache NOT enabled:
    X-Cache header: ABSENT

POST /v1/chat/completions (streaming, stream=true, any cache setting)
  X-Cache header: ABSENT (cache layer completely bypassed — no read, no write)

Schema touched (ADDITIVE — no new tables; EXPECTED_TABLES manifest unchanged):
  api_keys: ADD COLUMN cache_enabled BOOLEAN NOT NULL DEFAULT false
  tenants:  ADD COLUMN cache_enabled BOOLEAN NOT NULL DEFAULT false

Migration ID: <new_id>_response_caching (after a4f8c2e1b9d3)
  Rollback: DROP COLUMN api_keys.cache_enabled; DROP COLUMN tenants.cache_enabled
  (safe — additive columns with server default; no existing code references them pre-migration)

Cache key derivation:
  Redis key: resp-cache:{tenant_id}:{sha256(canonical_json)}
  canonical_json: json.dumps(payload_subset, sort_keys=True, separators=(',', ':'))
  payload_subset: dict of ONLY these keys present in the request body:
    model, messages, temperature, top_p, max_tokens, stop, n,
    presence_penalty, frequency_penalty, seed
  Absent fields are EXCLUDED (not set to null). This ensures a request
  without `temperature` and one with `temperature=null` do NOT produce the same
  cache key — strict exact-match only.

Effective cache enable logic:
  effective = authz.cache_enabled (bool, pre-computed in AuthzResult)
  authz.cache_enabled = api_keys.cache_enabled OR tenants.cache_enabled
  Both columns are read at authentication time via the extended get_by_id() JOIN.
  The ApiKeyRepository.get_by_id() LEFT JOIN is extended to also include tenants row.

Usage recording on cache hit:
  - record() called with same signature as a normal completion
  - raw field: {tenant_id, key_id, model, usage, status, cached: true}
    (cached=true marker injected into the raw dict, NOT a new column in usage_records)
  - cost_usd: 0 (pricing lookup returns 0 for cached hits OR recorder short-circuits
    when cached=True — BUILD determines the clean approach; contract mandates cost_usd=0)
  - prompt_tokens / completion_tokens: from cached body's usage field
  - Advisory spend counters (INCRBYFLOAT): NOT incremented for hits because cost_usd=0
    and the recorder only increments when cost_usd > 0 (existing invariant)

Enforcement order (governance before cache — immutable):
  1. Authentication (_authenticate)
  2. Payload validation (_validate_payload)
  3. Governance (_enforce_governance): expiry → allowlist → catalog → per-key budget
     → team budget → tenant budget → rate limits (RPM + TPM pre-flight)
  4. Cache lookup (MISS/HIT/BYPASS decision) — AFTER governance, BEFORE upstream
  5. Upstream call (on MISS or BYPASS)
  6. Cache store (fire-and-forget on 200, after upstream responds)
  7. Usage recording (_fire_record)
  8. TPM post-accounting (_fire_record_tpm)
  Rate-limit RPM: counted at step 3 (before cache, so hits consume RPM quota)
  Rate-limit TPM: uses cached token counts on hits (step 8)

ResponseCache protocol (new domain port in proxy/domain/ports.py):
  class ResponseCache(Protocol):
    async def get(self, cache_key: str) -> dict[str, Any] | None: ...
    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None: ...

  RedisResponseCache (infrastructure adapter in proxy/infrastructure/):
    Implements ResponseCache using app.state.redis_client.
    get(): Redis GET, deserialize JSON; return None on miss or error.
    set(): Redis SET with EX=ttl_seconds, fire-and-forget (errors logged, swallowed).

  PassthroughResponseCache: no-op implementation for non-cached paths (or when
    cache_enabled=False on the CompletionUseCase call).

Modules touched (hard boundary — BUILD must not add new modules outside this list):
  - apps/gateway/src/gateway/proxy/domain/ports.py        (add ResponseCache protocol)
  - apps/gateway/src/gateway/proxy/application/use_cases.py  (complete() cache logic)
  - apps/gateway/src/gateway/proxy/infrastructure/         (new RedisResponseCache)
  - apps/gateway/src/gateway/proxy/api/deps.py             (wire RedisResponseCache)
  - apps/gateway/src/gateway/observability/metrics.py      (add gateway_cache_events_total)
  - apps/gateway/src/gateway/keys/domain/entities.py       (add cache_enabled to ApiKey + AuthzResult)
  - apps/gateway/src/gateway/keys/infrastructure/orm.py    (add cache_enabled column)
  - apps/gateway/src/gateway/keys/infrastructure/repository.py  (extend get_by_id() JOIN)
  - apps/gateway/src/gateway/keys/api/schemas.py           (add cache_enabled to Create/Patch/Info)
  - apps/gateway/src/gateway/keys/api/router.py            (pass cache_enabled through create/patch)
  - apps/gateway/src/gateway/keys/application/use_cases.py (pass cache_enabled in AuthzResult)
  - apps/gateway/src/gateway/tenants/infrastructure/orm.py (add cache_enabled to TenantRow)
  - apps/gateway/src/gateway/core/config.py                (add GATEWAY_CACHE_TTL_SECONDS)
  - apps/gateway/src/gateway/main.py                       (wire ResponseCache; include cache router)
  - apps/gateway/migrations/versions/<new>_response_caching.py
  - New: apps/gateway/src/gateway/proxy/infrastructure/response_cache.py
  - New: apps/gateway/src/gateway/tenants/api/cache_router.py  (GET/PUT /admin/cache)

EXPECTED_TABLES: no new table; manifest unchanged.
No new Python packages; all implementation uses existing allowlist (redis, json, hashlib).

Flags for freeze (lowest-confidence points across the bundle):
  ⚠ [contract] ResponseCache port injection into CompletionUseCase via constructor:
    CompletionUseCase.__init__ gains a `response_cache: ResponseCache | None = None`
    parameter. When None, cache is a no-op (backward-compatible with all frozen fakes
    that construct CompletionUseCase without the new parameter). This is the established
    hasattr/default-None seam pattern. If a future reviewer requires a dedicated CacheUseCase
    wrapping CompletionUseCase instead, the refactor is contained. [spec/contract]

  ⚠ [contract] Tenant cache_enabled fetch in get_by_id() vs. a separate method:
    The repository get_by_id() already LEFT JOINs teams for team_budget_usd. Extending to
    also LEFT JOIN tenants for tenant.cache_enabled keeps it at zero extra DB reads.
    If the JOIN fan-out becomes a maintenance concern later (3 tables: api_keys + teams +
    tenants), a dedicated auth-fields view or a separate SELECT at acceptable extra cost
    can replace it. For v4, zero-extra-reads is the contract. [contract]
```

Status: FROZEN @ v4 — approved by Tin Dang (delegated auto mode, 2026-06-11)

Least-sure flag surfaced at freeze:
  ⚠ [contract] ResponseCache rides CompletionUseCase as a default-None constructor param —
    backward-compatible with every frozen fake that constructs the use case without it
    (established default-None/hasattr seam, third use). RESOLVED at freeze: approved. Cost if
    wrong (a frozen suite constructs positionally and breaks): change request, never a frozen
    test edit.
  ⚠ [contract] get_by_id() auth query grows to a 3-table LEFT JOIN (api_keys+teams+tenants)
    to keep the zero-extra-DB-reads contract for tenant cache_enabled. RESOLVED at freeze:
    approved — all PK-indexed; the alternative (second SELECT per request) violates the v3
    zero-extra-reads contract. Cost if wrong: auth-path p99 regression — watch at §7.
  ⚠ [test] The spend-counter-unchanged-on-hit assertion requires the recorder to skip the
    INCRBYFLOAT for cost-0 rows (an INCR of 0 would still create the Redis key). If the
    existing recorder lacks that guard, the build adds it — a behavior-preserving guard for
    all cost-0 records, not just cached ones. Cost if wrong: phantom zero-value counter keys.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_cache_default_off_upstream_called_twice: S1 — arrange key cache_enabled=false,
    tenant cache off; act two identical requests; assert upstream.calls==2, no X-Cache header
  - test_key_enabled_cache_hit: S2 — arrange key cache_enabled=true; act two identical requests;
    assert upstream.calls==1, second resp X-Cache==hit, usage row 2 cached=true cost_usd=0
  - test_tenant_enabled_cache_hit: S3 — arrange key cache_enabled=false, tenant cache on via
    PUT /admin/cache; act two identical requests; assert upstream.calls==1, X-Cache: hit
  - test_bypass_header_forces_upstream_and_restores: S4 — arrange warm cache; act with
    Cache-Control: no-cache; assert upstream called (X-Cache: bypass); act again without bypass;
    assert X-Cache: hit (stored during bypass)
  - test_different_payload_is_cache_miss: S5 — arrange key cache_enabled=true; warm with A;
    act with B; assert upstream called for B; X-Cache: miss
  - test_different_tenant_same_payload_is_cache_miss: S6 — two tenants, cache on, tenant A
    warms; tenant B request → upstream called; no X-Cache: hit on tenant B
  - test_upstream_4xx_not_cached: S7 — upstream returns 400; two requests; upstream.calls==2;
    no X-Cache: hit
  - test_governance_blocks_before_cache: S8 — warm cache; disable model; assert 403
    ERR_MODEL_DISABLED returned (not cached body), no X-Cache header
  - test_stream_bypasses_cache_entirely: S9 — cache-enabled key; two identical stream=true
    requests; upstream.calls==2; no X-Cache header on either
  - test_prometheus_counter_increments: S10 — assert miss/hit/bypass counters increment
  - test_patch_key_cache_enabled_toggle: S11 — PATCH /admin/keys/{key_id}
    {"cache_enabled": true}; assert 200, body has cache_enabled=true, DB has it
  - test_member_role_forbidden_on_put_cache: S12 — member JWT, PUT /admin/cache; assert 403
</test_plan>

Tests live in: `apps/gateway/tests/response_caching/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): Cache store is ALWAYS fire-and-forget; a Redis SET failure
MUST NOT fail the completion response. Governance MUST run before cache lookup —
never short-circuit governance based on cache state. Streaming path MUST never touch cache.
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

Watch (reuse scenarios as monitors): cache hit rate per tenant per model; miss rate spike
detection (upstream cost regression); bypass rate (unusual volume = client misconfiguration)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
