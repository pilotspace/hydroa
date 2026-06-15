# TASK: Cache controls: per-request TTL override + embedding-response cache (per-tenant, billed on miss only)

slug: cache-controls · created: 2026-06-15 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

This task has TWO sub-features: (A) per-request cache TTL override; (B) embedding-response cache.

Touches (files · symbols · signatures):
- `proxy/infrastructure/response_cache.py` — exact-match cache infra. `build_cache_key(tenant_id, payload)` (L39, chat fields: messages/temperature/…) · `RedisResponseCache.get/set(key, body, ttl)` (L150/L165, errors swallowed) · `build_semantic_cache_key` + pointer helpers. **ADD**: `build_embedding_cache_key(tenant_id, payload)` (NEW — exact-match over embedding output-affecting fields: model, input, encoding_format, dimensions, user; distinct prefix `embed-cache:`) + `resolve_cache_ttl(headers, default_ttl, max_ttl)` (NEW pure — parses `Cache-Control: max-age=N` → clamped [1, max_ttl]; absent/invalid → default_ttl).
- `proxy/api/router.py` (chat, L60-73) — already extracts `req_headers` + reads `app.state.cache_ttl_seconds` and passes `cache_ttl_seconds=cache_ttl` to the use case. **CHANGE (A)**: resolve effective TTL via `resolve_cache_ttl(req_headers, cache_ttl, cache_max_ttl)` and pass THAT. → chat `use_cases.py` is UNCHANGED for the TTL override (router-side resolution; the use case already uses its `cache_ttl_seconds` param for both exact + pointer sets at L1019/L1039/L1062).
- `proxy/application/embeddings_use_case.py:EmbeddingsUseCase.execute()` — today NO cache (validate→governance→catalog→provider.post_json("/embeddings")→_fire_record_with_raw→return). FROZEN @ embeddings-endpoint §3 (additive, default-off extension is contract-preserving — same pattern as v8 adding cache to the v1 chat flow). **CHANGE (B)**: accept additive kwargs `response_cache: ResponseCache | None = None`, `cache_ttl_seconds: int = 300`, `request_headers: dict | None = None`; after `authz` (gives `authz.cache_enabled`): if enabled + not `no-cache` → `build_embedding_cache_key` → `cache.get` → HIT: `_fire_record_cached` ($0) + return (200, cached, x_cache="hit"); MISS→ upstream, on 200 `_fire_cache_set` with effective TTL.
- `proxy/api/embeddings_deps.py:get_embeddings_use_case` — inject `response_cache` (RedisResponseCache from app.state) + `cache_ttl_seconds` (app.state.cache_ttl_seconds).
- `proxy/api/embeddings_router.py:embeddings` — pass `request_headers` (+ resolved TTL) into `execute()`; set `X-Cache` response header from the returned marker (parity with chat).
- `core/config.py:Settings` (cache knobs L131-132) — `cache_ttl_seconds: int = 300` exists; **ADD** `cache_max_ttl_seconds: int = Field(default=86400)` (cap for the per-request override).
- `main.py` (L531-532) — `app.state.cache_ttl_seconds = settings.cache_ttl_seconds` exists; **ADD** `app.state.cache_max_ttl_seconds` for the routers to read.
- Billing helpers (REUSE, do NOT modify use_cases.py): `_fire_record_cached` (L206 — extras `{cached:True}` → cost_usd=0, never re-bills) and `_fire_record_with_raw` (already imported by embeddings_use_case via `# pyright: ignore[reportPrivateUsage]`).
- `keys/domain/entities.py:AuthzResult.cache_enabled` (L90, effective = key OR tenant, resolved at auth). NonChatGovernance.authorize returns the same AuthzResult → `authz.cache_enabled` available in embeddings.

Context (working folder):
- `.add/milestones/v19/MILESTONE.md` — shared decisions: opt-in/default-off, BILLING ACCURACY (cache hit bills $0, never re-bills), TENANT ISOLATION (per-tenant key namespace preserved), DESIGN-FOR-FAILURE (cache failure degrades to MISS, never fails the request).
- Tests: `apps/gateway/tests/response_caching/` (chat exact/semantic cache hit/miss/bypass conventions) · the embeddings endpoint suite (provider_seam / embeddings tests). New suite dir: `apps/gateway/tests/cache_controls/`.
- `core/config.py` cache knob conventions; `main.py` app.state cache wiring (L531).

Honors (patterns / conventions):
- OPT-IN / DEFAULT-OFF: embedding cache gated by the EXISTING `cache_enabled` (default false) → byte-identical when off; TTL override only active when `Cache-Control: max-age=N` present → default TTL otherwise.
- BILLING ACCURACY sacrosanct (v12): a cache HIT bills via `_fire_record_cached` (cost $0, no spend increment) and serves WITHOUT an upstream call (no provider tokens billed); a MISS bills the upstream once (existing single-bill).
- TENANT ISOLATION (v8): the embedding cache key is `embed-cache:{tenant_id}:{hash}` — never crosses tenants; mirrors the exact-cache namespace.
- DESIGN-FOR-FAILURE (CLAUDE.md): `RedisResponseCache.get/set` already swallow all errors → a cache failure is a MISS, never a request failure; `resolve_cache_ttl` is pure + total (never raises, bad header → default).
- INVIOLABLE chat `use_cases.py`: avoided for sub-feature A by resolving the effective TTL router-side (the use case already consumes `cache_ttl_seconds`).

Anchors the contract cites:
- `build_embedding_cache_key(tenant_id, payload) -> str` (NEW, prefix `embed-cache:`).
- `resolve_cache_ttl(headers, default_ttl, max_ttl) -> int` (NEW pure fn; parses `Cache-Control: max-age=N`).
- `EmbeddingsUseCase.execute(..., response_cache=None, cache_ttl_seconds=300, request_headers=None)` (additive kwargs).
- `Settings.cache_max_ttl_seconds` (NEW knob, env `GATEWAY_CACHE_MAX_TTL_SECONDS`).
- Reused: `RedisResponseCache.get/set`, `_fire_record_cached` (cached=$0), `AuthzResult.cache_enabled`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Cache controls — two opt-in cache refinements. (A) A caller may set the per-request cache STORE TTL via `Cache-Control: max-age=N` (bounded). (B) An exact-match response cache for `/v1/embeddings`, per-tenant isolated, billed only on miss. Both default-off / byte-identical.

Framings weighed:
- (A) Resolve the effective TTL in the ROUTER (chosen) — the router already reads request headers + `app.state.cache_ttl_seconds` and passes `cache_ttl_seconds` to the use case; resolving the `max-age` override there leaves the high-blast-radius chat `use_cases.py` UNTOUCHED. Alt (rejected): parse inside the chat use case → edits the inviolable core path for no benefit.
- (B) Cache in the embeddings USE CASE (chosen) — billing ($0 on hit via `_fire_record_cached`) and the no-cache bypass already live at the use-case layer; mirrors the chat cache shape. Alt (rejected): cache in the router → splits billing from caching, can't reuse the single-bill helpers.
- (B-key) A DISTINCT key prefix `embed-cache:` over embedding output-affecting fields (model, input, encoding_format, dimensions, user) — chosen so embeddings never collide with the chat `resp-cache:` namespace. Alt (rejected): reuse `build_cache_key` → its `messages` field set is wrong for embeddings (would key on nothing → false cross-request hits).

Must:
<must>
  - (A) When a request carries `Cache-Control: max-age=N` with N a positive integer, the cache STORE for that request uses TTL = min(N, cache_max_ttl_seconds); absent / non-integer / N<1 → the default `cache_ttl_seconds`. `resolve_cache_ttl` is PURE + TOTAL (never raises).
  - (A) `Cache-Control: no-cache` STILL bypasses (existing behavior) and takes precedence over any `max-age` (no-cache wins: bypass, no store of the bypassed key per existing semantics).
  - (A) The TTL override applies to BOTH the chat store path (exact key + semantic pointer share the one resolved TTL) and the embedding store path. It NEVER changes lookup behavior — only the store TTL.
  - (B) When the tenant/key has `cache_enabled` AND the request is not `no-cache`, an exact-match `/v1/embeddings` request returns a prior identical response from cache (HIT): served WITHOUT an upstream call, billed via `_fire_record_cached` (cost $0, no spend increment, never re-billed), `X-Cache: hit`.
  - (B) On a MISS the request forwards upstream exactly as today and, on a 200, stores the response under `build_embedding_cache_key(tenant_id, payload)` with the resolved TTL (fire-and-forget); `X-Cache: miss`. The upstream is billed once (existing single-bill).
  - (B) The embedding cache key is per-tenant (`embed-cache:{tenant_id}:{hash}`) — a HIT NEVER crosses tenants and never collides with the chat `resp-cache:` namespace.
  - (B) A non-200 upstream embeddings response is NEVER cached (only 200 bodies are stored) and is billed/returned as today.
  - DESIGN-FOR-FAILURE: any cache get/set error degrades to a MISS (or a no-op store) — it NEVER fails the embeddings request (RedisResponseCache already swallows; the use case must not add a raising path).
  - DEFAULT-OFF byte-identical: with `cache_enabled=False` (default) the embeddings path is byte-identical to today (no cache get/set, no X-Cache semantics change beyond absent header); with no `max-age` header the store TTL is the default.
</must>
Reject:
<reject>
  - `Cache-Control: max-age=abc` / `max-age=-5` / `max-age=0` / absent -> NOT an override -> default `cache_ttl_seconds` (fail-safe).
  - `Cache-Control: no-cache` on embeddings -> bypass lookup AND (per existing chat semantics) do not serve a hit -> "bypass" (X-Cache: bypass).
  - An embeddings request with `cache_enabled=False` -> no cache get/set at all -> byte-identical to today.
  - A non-200 upstream embeddings response -> NOT stored -> returned + billed verbatim.
  - A Redis get/set failure -> treated as MISS / no-op store -> "degrade (no synthetic error)" (request never fails).
</reject>
After:
<after>
  - A repeated identical embeddings request (same tenant, same model+input+params) serves from cache, costs the tenant $0 for upstream tokens, and makes zero upstream calls.
  - A caller setting `Cache-Control: max-age=60` causes that request's stored entry to expire in 60s (bounded by the configured cap); the default TTL is unchanged for requests without the header.
  - Tenant A's embedding cache entry is never served to tenant B (distinct key namespace).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The embedding cache key field set (model, input, encoding_format, dimensions, user) captures ALL output-affecting embedding params — lowest confidence because a provider-specific param outside this set could change the embedding yet collide on the key (a stale hit). Chosen this set from the OpenAI /v1/embeddings schema; if wrong: a caller using an unlisted output-affecting param gets a wrong cached vector. Cost: medium — additive field to the key set, but a wrong hit returns incorrect data (mitigation: the set covers every documented OpenAI embeddings field; unknown params are RARE and the feature is opt-in). Surfaced at freeze.
  - [ ] Resolving the TTL override router-side keeps chat `use_cases.py` byte-identical — confidence high: the router already passes `cache_ttl_seconds`; the use case uses it verbatim for exact + pointer sets. If wrong (some store path reads the default elsewhere): the override silently no-ops for that path (fail-safe, default TTL). Verified by grep (L1019/L1039/L1062 all use the param).
  - [x] `authz.cache_enabled` is available in the embeddings use case — confirmed: NonChatGovernance.authorize returns the AuthzResult from authenticate(), which carries the effective (key OR tenant) cache_enabled.
  - [x] A 200-only store rule preserves billing accuracy — confirmed: only successful bodies cache; a hit bills $0 via `_fire_record_cached`; a miss bills once. No double-bill path exists (the use case bills exactly one of hit/miss).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- (A) per-request TTL override (resolve_cache_ttl, pure) ---
Scenario: max-age overrides the store TTL (bounded)
  Given default cache_ttl_seconds=300 and cache_max_ttl_seconds=86400
  When resolve_cache_ttl({"cache-control": "max-age=60"}, 300, 86400) is called
  Then it returns 60

Scenario: max-age above the cap clamps to the cap
  When resolve_cache_ttl({"cache-control": "max-age=999999"}, 300, 86400) is called
  Then it returns 86400

Scenario: absent / invalid max-age falls back to default
  When resolve_cache_ttl is called with no cache-control, "max-age=abc", "max-age=-5", "max-age=0"
  Then each returns 300 (the default)
  And it never raises for any header value

Scenario: no-cache takes precedence over max-age
  When resolve_cache_ttl is asked about "no-cache, max-age=60"
  Then bypass semantics win (no store) — max-age is irrelevant on a bypass

# --- (B) embedding-response cache ---
Scenario: embedding cache MISS forwards upstream, stores 200, bills once
  Given cache_enabled=True, an empty cache, a fake embeddings provider returning 200
  When the embeddings request runs
  Then the upstream is called once, the 200 body is returned with X-Cache: miss
  And the response is stored under embed-cache:{tenant}:{hash}
  And exactly one usage record is written (the upstream bill)

Scenario: embedding cache HIT serves from cache, $0, no upstream call
  Given cache_enabled=True and a prior identical request already cached
  When the same embeddings request runs again
  Then it returns the cached body with X-Cache: hit
  And the upstream provider is NOT called
  And the usage record is written with cached=True (cost $0)

Scenario: embedding cache is per-tenant isolated
  Given tenant A cached an embeddings response
  When tenant B sends the identical embeddings request
  Then tenant B MISSES (distinct embed-cache:{B} key) and calls upstream
  And tenant A's entry is never served to B

Scenario: embedding cache key excludes non-output fields and keys on output fields
  Given two embeddings payloads identical in model+input+dimensions+encoding_format
  When build_embedding_cache_key is computed for each (same tenant)
  Then the keys are EQUAL (and differ from any chat resp-cache: key)
  And changing input OR model OR dimensions yields a DIFFERENT key

Scenario: embedding non-200 upstream is not cached
  Given cache_enabled=True and a fake provider returning 400
  When the embeddings request runs
  Then the 400 is returned and NOTHING is stored in the cache

Scenario: embedding no-cache bypass
  Given cache_enabled=True and a prior cached entry
  When the request carries Cache-Control: no-cache
  Then the upstream is called (bypass), X-Cache: bypass

Scenario: embedding cache disabled is byte-identical (default-off)
  Given cache_enabled=False (default)
  When two identical embeddings requests run
  Then BOTH call upstream (no cache get/set), no X-Cache hit/miss semantics
  And behavior is byte-identical to today

Scenario: embedding cache get failure degrades to MISS (never fails request)
  Given cache_enabled=True and a cache whose get() raises internally (swallowed → None)
  When the embeddings request runs
  Then it forwards upstream and returns the 200 (the request never fails)

Scenario: embedding store honors the per-request max-age TTL
  Given cache_enabled=True and Cache-Control: max-age=60
  When an embeddings MISS stores the 200 body
  Then the store TTL passed to cache.set is 60 (resolved override)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── (A) Per-request TTL override — pure resolver (response_cache.py) ──────
resolve_cache_ttl(headers: dict[str, str] | None, default_ttl: int, max_ttl: int) -> int
  PURE · TOTAL · never raises. Reads ONLY the "cache-control" header (case-insensitive key).
  - parse a `max-age=<int>` directive (comma/space-separated, case-insensitive) from the value
  - if the int N is >= 1            -> min(N, max_ttl)
  - else (absent / non-int / N < 1) -> default_ttl
  Does NOT decide bypass (no-cache) — that stays where it is (the use case / existing path).

# Router wiring (chat: proxy/api/router.py · embeddings: embeddings_router.py):
#   cache_ttl     = app.state.cache_ttl_seconds        (default 300)
#   cache_max_ttl = app.state.cache_max_ttl_seconds    (default 86400)
#   effective_ttl = resolve_cache_ttl(req_headers, cache_ttl, cache_max_ttl)
#   -> passed as cache_ttl_seconds into the use case. NO chat use_cases.py change.

# ── (B) Embedding cache key — pure (response_cache.py) ────────────────────
build_embedding_cache_key(tenant_id: str, payload: dict[str, Any]) -> str
  key format: embed-cache:{tenant_id}:{sha256(canonical_json)}
  canonical_json: sorted-keys compact JSON over ONLY the present embedding output-affecting
  fields = {"model","input","encoding_format","dimensions","user"}. Absent fields EXCLUDED.
  DISTINCT prefix from chat (resp-cache:) and semantic (resp-cache-sem:) — never collides.

# ── (B) Embeddings use case (proxy/application/embeddings_use_case.py) ─────
EmbeddingsUseCase.execute(
    *, raw_key, body, registry, usage_recorder,
    response_cache: ResponseCache | None = None,   # NEW (additive; None ⇒ today's flow)
    cache_ttl_seconds: int = 300,                   # NEW (resolved TTL from the router)
    request_headers: dict[str, str] | None = None,  # NEW (for no-cache bypass)
) -> tuple[int, dict[str, Any], str | None]          # 3-tuple NOW: (+ x_cache marker)
  Flow (additive — steps 1-5 + 7 UNCHANGED when cache disabled/None):
    after authz (step 3): cache_on = response_cache is not None AND authz.cache_enabled
      no_cache = request_headers.get("cache-control","").lower().contains "no-cache"
      if cache_on and not no_cache:
        ck = build_embedding_cache_key(str(authz.tenant_id), body)
        hit = await response_cache.get(ck)        # swallows errors → None
        if hit is not None:
          _fire_record_cached(...) ; return (200, hit, "hit")
      <upstream post_json call — UNCHANGED>
      _fire_record_with_raw(...)                  # MISS bill (UNCHANGED single-bill)
      if cache_on and status == 200 and not no_cache:
        _fire_cache_set(response_cache, ck, resp_body, cache_ttl_seconds)   # fire-and-forget
      x_cache = "hit"|"miss"|"bypass"|None        # None when cache_on is False (default-off)
      return (status, resp_body, x_cache)
  Non-200 is NEVER stored. Cache get/set failure ⇒ MISS / no-op (request never fails).

# embeddings_router.py: unpack the 3-tuple; set resp.headers["x-cache"]=x_cache when not None.
# embeddings_deps.py: inject response_cache=RedisResponseCache(redis) + cache_ttl_seconds
#                     + cache_max_ttl from app.state; resolve effective TTL in the router.

# ── Config (core/config.py:Settings) ─────────────────────────────────────
cache_max_ttl_seconds: int = Field(default=86400)   # env GATEWAY_CACHE_MAX_TTL_SECONDS; cap for max-age

# ── Wiring (main.py) ──────────────────────────────────────────────────────
app.state.cache_max_ttl_seconds = settings.cache_max_ttl_seconds

Schema: NONE — no DB tables/columns. Reads: config + per-request headers + cache_enabled
        (resolved at auth). Cache: Redis GET/SET on the new embed-cache: namespace, per-tenant.
        Billing: a HIT bills $0 via _fire_record_cached (no spend increment); a MISS bills the
        upstream once via _fire_record_with_raw. Chat use_cases.py UNCHANGED.
```

Status: FROZEN @ v1 — approved by Tin (auto mode, 2026-06-15)
Least-sure flag surfaced at freeze: [contract] the embedding cache-key field set
  {model, input, encoding_format, dimensions, user} must capture EVERY output-affecting
  embeddings param — if a provider-specific param outside this set changes the vector, two
  requests collide on the key and a STALE/WRONG vector is served. Chosen from the OpenAI
  /v1/embeddings schema; cost MEDIUM if wrong (a wrong hit returns incorrect data) but the
  feature is opt-in and unknown params are rare. Mitigation: the set covers every documented
  field; adding a field later is an additive key change. Secondary [spec] no-cache precedence
  over max-age (bypass wins) matches existing chat semantics — low risk.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% on the new `resolve_cache_ttl` + `build_embedding_cache_key`; ≥90% on the new embeddings-cache branch.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  RESOLVER (test_resolve_cache_ttl.py — pure):
  - test_max_age_overrides: resolve_cache_ttl({"cache-control":"max-age=60"}, 300, 86400) == 60
  - test_max_age_clamps_to_cap: resolve_cache_ttl({"cache-control":"max-age=999999"}, 300, 86400) == 86400
  - test_absent_or_invalid_defaults: no header / "max-age=abc" / "max-age=-5" / "max-age=0" → 300 each
  - test_max_age_among_directives: "no-store, max-age=120" → 120; case-insensitive "MAX-AGE=45" → 45
  - test_never_raises: a fuzz set of odd header dicts/values never raises (returns default)

  KEY (test_embedding_cache_key.py — pure):
  - test_same_inputs_same_key: identical model+input+dimensions+encoding_format → equal keys
  - test_prefix_is_embed_cache: key startswith "embed-cache:{tenant}:"
  - test_differs_from_chat_key: build_embedding_cache_key != build_cache_key for the same dict
  - test_input_change_changes_key / test_model_change / test_dimensions_change → different keys
  - test_absent_fields_excluded: adding an absent optional field (no dimensions) ≠ dimensions:null key
  - test_tenant_isolation_in_key: tenant A key != tenant B key for identical payload

  EMBEDDINGS CACHE (test_embedding_cache.py — async; FakeGovernance/Session/Registry/Provider/Cache/SpyRecorder):
  - test_miss_forwards_stores_bills_once: enabled, empty cache → upstream called once, X-Cache miss, set() called with the 200 body, ONE usage record (not cached)
  - test_hit_serves_zero_no_upstream: enabled, pre-seeded cache → X-Cache hit, provider NOT called, usage record has cached=True
  - test_tenant_isolation: tenant A seeded; tenant B identical request → MISS (calls upstream); A's body never returned to B
  - test_non_200_not_cached: enabled, provider→400 → 400 returned, cache.set NEVER called
  - test_no_cache_bypass: enabled + pre-seeded + Cache-Control no-cache → provider called, X-Cache bypass
  - test_disabled_byte_identical: cache_enabled=False → no get/set, provider called, x_cache None
  - test_get_failure_degrades_to_miss: cache.get returns None (swallowed-error contract) → upstream called, 200 returned
  - test_store_honors_max_age_ttl: enabled + resolved cache_ttl_seconds=60 → cache.set called with ttl=60

  CONFIG (any file):
  - test_settings_cache_max_ttl_default: Settings().cache_max_ttl_seconds == 86400
</test_plan>

Tests live in: `apps/gateway/tests/cache_controls/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/response_cache.py` `apps/gateway/src/gateway/proxy/application/embeddings_use_case.py` `apps/gateway/src/gateway/proxy/api/embeddings_deps.py` `apps/gateway/src/gateway/proxy/api/embeddings_router.py` `apps/gateway/src/gateway/proxy/api/router.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/cache_controls/`
Strategy (ordered batches):
  1. `response_cache.py` — add `resolve_cache_ttl` (pure) + `build_embedding_cache_key` (pure). Make the two pure suites green first.
  2. `config.py` — add `cache_max_ttl_seconds: int = Field(default=86400)`; `main.py` — `app.state.cache_max_ttl_seconds = settings.cache_max_ttl_seconds`.
  3. `embeddings_use_case.py` — additive kwargs (`response_cache`, `cache_ttl_seconds`, `request_headers`); insert cache get (HIT→$0 return) before upstream + cache set (200-only, fire-and-forget) after; return 3-tuple `(status, body, x_cache)`. Reuse `_fire_record_cached` + `_fire_cache_set` from use_cases.
  4. `embeddings_deps.py` — inject `response_cache` (RedisResponseCache(redis)) + `cache_ttl_seconds`/`cache_max_ttl` from app.state.
  5. `embeddings_router.py` — resolve effective TTL via `resolve_cache_ttl(req_headers, ...)`, pass headers + TTL into execute(), set `X-Cache` header from the 3-tuple.
  6. `router.py` (chat) — resolve effective TTL the same way; pass as `cache_ttl_seconds`. NO use_cases.py change.
Safety rule (feature-specific): a cache HIT bills $0 (`_fire_record_cached`) and makes ZERO upstream calls; a MISS bills the upstream ONCE; only a 200 is stored; cache get/set failure degrades to MISS/no-op (RedisResponseCache swallows — the use case trusts the port contract, no raising path). Per-tenant key namespace (`embed-cache:{tenant}`) — never cross-tenant. Default-off ⇒ byte-identical.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; do NOT modify chat `use_cases.py`; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — cache_controls 23/23; embeddings_endpoint 34/34 (combined); response_caching+semantic_cache+obs_callbacks 43/43; proxy+model_fallbacks+wiring+error_aware_fallback 63/63; `make test-fast` no-DB gate exit 0. `make typecheck` 0 errors; `make lint` clean.
- [x] coverage did not decrease — new pure fns (resolve_cache_ttl, build_embedding_cache_key) fully exercised; cache flow on EmbeddingsUseCase covered by 9 use-case tests (hit/miss/bypass/disabled/non-200/get-failure/ttl/isolation).
- [x] no test or contract was altered during build — only src/ changed; tamper baseline intact (red suite frozen at tests→build crossing).
- [x] the green was EARNED, not gamed — refute-read (20 probes, below) found no overfit/vacuous asserts/stubbed-away logic. Tests assert real billing call-counts (rec.call_count==1 + cached marker), real upstream call_count, real set_calls with key/body/ttl, distinct keys per tenant.
- [x] concurrency / timing safe — _fire_record_cached/_fire_record_with_raw/_fire_cache_set are fire-and-forget (asyncio.ensure_future, same pattern as chat use_cases.py); a HIT returns before any DB/upstream I/O; cache stampede (two identical MISS) bills each its own real upstream call — no double-bill within a request.
- [x] no exposed secrets, injection openings, or unexpected dependencies — cache key = sha256 over body subset, prefixed with tenant_id (UUID, not a secret); raw input never in plaintext key; per-tenant key namespace ⇒ no cross-tenant read; RedisResponseCache logs the hashed key only; no new deps (reuses re/hashlib/json already imported).
- [x] layering & dependencies follow CONVENTIONS.md — pure fns in infrastructure/response_cache.py; cache orchestration in application/embeddings_use_case.py; wiring in api/ (deps+routers) + main.py; use_cases.py UNCHANGED (byte-identical chat path preserved).
- [x] a person reviewed and approved the change — auto mode (autonomy: auto, non-risk-high): auto-resolved PASS on complete evidence; no security/concurrency/architecture residue (Tin's standing mandate).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — resolve_cache_ttl referenced by chat router.py + embeddings_router.py; build_embedding_cache_key by embeddings_use_case.py; cache_max_ttl_seconds by both routers + main.py (app.state) + config Settings; get_response_cache by embeddings_router; EmbeddingsUseCase.execute 3-tuple consumed by embeddings_router (only non-test caller — confirmed by grep, images/audio use their own use cases).
- [x] DEAD-CODE (code) — no orphaned symbols; every new fn/kwarg/setting has a live caller (verified by grep). cache_key local is referenced in both lookup + store branches.
- [x] SEMANTIC — frozen §3 contract re-read in full; build matches: 3.5 cache lookup before catalog query (HIT skips DB), single-bill on MISS, $0 on HIT, non-200 never stored, no-cache bypass wins over max-age, x_cache None when disabled (router omits header), chat use_cases.py untouched.

### Refute-read (adversarial — 20 probes, no confirmed cheat)
1. Chat router TTL change byte-identical w/o max-age (resolve→default 300) ✓  2. no-cache still separate from TTL ✓  3. embeddings_endpoint tests see no x-cache header (no redis/cache_enabled) ✓  4. HIT skips catalog query (execute_calls==0) ✓  5. cache_key reused lookup↔store ✓  6. batch list input keyed correctly (order-sensitive) ✓  7. JSON-serializable assumption matches chat build_cache_key ✓  8. cache stampede bills per real call, no intra-request double-bill ✓  9. Redis outage → get None / set swallowed → degrade to MISS, request never fails ✓  10. resolver empty/blank → default ✓  11. leading-zero max-age parses ✓  12. multiple cache-control headers: last-wins via dict comprehension ✓  13. no-cache precedence over max-age (bypass, no store) ✓  14. "no-cache" substring per frozen contract (.contains), exact test still passes ✓  15. header values are str ✓  16. lowercased keys match resolver ✓  17. security: per-tenant keys, no secrets, hashed input, no cross-tenant leak ✓  18. least-sure flag (stale hit) — key covers ALL documented OpenAI embed params {model,input,encoding_format,dimensions,user}; `user` included conservatively (only ⇒ extra miss, never stale); residual = undocumented provider param, opt-in, additive key change later (pre-approved at freeze) ✓  19. encoding_format base64≠float keyed distinctly ✓  20. no other EmbeddingsUseCase.execute caller left on old 2-tuple (grep) ✓

### GATE RECORD
Outcome: PASS
Reviewed by: auto mode (Tin standing mandate — autonomy: auto, non-risk-high) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
