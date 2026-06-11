# TASK: Semantic response caching — similarity layer over the exact-match cache

slug: semantic-cache · created: 2026-06-11 · stage: production · risk: moderate · autonomy: auto
<!-- risk: moderate because the chosen framing (normalization-only) makes false hits
     structurally impossible rather than threshold-tuned; autonomy: auto is defensible
     on that basis. If the framing were embedding/LSH, risk: high + autonomy: conservative
     would be mandatory. See §1 for the full argument. -->
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Normalized near-duplicate semantic cache — a second lookup layer over the v4 exact-match
         cache that matches prompts that differ only in whitespace, casing, and trailing punctuation.

Framings weighed:
- **(a) aggressive normalization only** (chosen): unicode NFKC casefold, whitespace collapse,
  trailing-punctuation strip of the last user message, stable-serialize the rest. Near-duplicate
  matching with structurally-zero false hits: single-character differences like negations ("don't"
  vs "do"), year changes ("in 1300" vs "in 1400"), or any non-whitespace/case/trailing-punct
  difference produce distinct normalized keys. The normalized string is deterministic, reversible
  in inspection, and requires only stdlib (`unicodedata`, `re`, `hashlib`). No threshold to tune.
  FALSE-HIT STORY: false hits are structurally impossible because the normalization is injective
  on the semantic content — two strings that differ in meaning differ in at least one character
  that survives normalization (negation particles, numbers, punctuation mid-sentence all survive
  verbatim). The only collision class is true near-duplicates (e.g. "  What is X?" == "what is x"
  after normalization) which ARE the intended matches.

- **(b) MinHash/LSH over token shingles** (explicitly rejected for v5): high Jaccard threshold
  (≥0.98) reduces but does not eliminate false hits — a single-token negation changes Jaccard
  by only O(1/n) for long prompts, so "what is the capital of France?" and "what is NOT the
  capital of France?" may still collide at short prompt lengths. FALSE-HIT BOUND: unacceptable
  for an LLM proxy where a false hit is a correctness failure strictly worse than a miss. Would
  also require either a new package (datasketch) or a hand-rolled MinHash, adding allowlist
  complexity. Deferred to a future task if precision telemetry from (a) shows a gap.

- **(c) embeddings via upstream API** (rejected): OpenRouter embeddings availability is not
  guaranteed; adds latency on the hot path (~100ms embedding call); introduces a new outbound
  dependency before every cache lookup; and costs money for every cache miss. The cost of an
  embedding call can exceed the cost of the LLM call for short prompts. OUT OF SCOPE per v5
  MILESTONE.md ("embedding-free heuristics acceptable").

NAMING HONESTY: This task is called "semantic cache" in the GLOSSARY and milestone, but the
v5 similarity strategy is NORMALIZATION-ONLY (near-duplicate matching). The GLOSSARY gains
the term `semantic_cache_hit` which is honest: it means "a hit via the normalized-near-duplicate
layer" — not "embedding similarity". Any monitoring or user-facing surface must label this
"normalized near-duplicate cache" if precision of language matters. This TASK.md uses
"semantic cache" as the established GLOSSARY term while internally noting the actual mechanism.

Must:
<must>
  - The semantic cache is a SECOND LOOKUP LAYER that runs only after the v4 exact-match cache
    MISSES. Lookup order: exact → semantic. Both layers share the same opt-in gate
    (semantic layer only active when the tenant has semantic_cache_enabled=true; see below).

  - Normalization algorithm (PINNED — this IS the contract; changing it is a change request):
    Input: the full request body (model, messages, temperature, top_p, max_tokens, stop, n,
           presence_penalty, frequency_penalty, seed — same field set as the exact cache).
    Step 1 — per-message normalize: for each message in messages:
      a. content string: apply unicode.normalize("NFKC", content) → casefold()
         (casefold is the unicode-aware lowercasing per Python docs — handles ligatures, etc.)
      b. whitespace collapse: re.sub(r'\s+', ' ', content).strip()
      c. trailing-punctuation strip ON THE LAST USER MESSAGE ONLY:
         re.sub(r'[.!?,;:]+$', '', content).rstrip() for the final message where role=="user".
         All other messages: only NFKC + casefold + whitespace collapse (no punct strip).
      d. role field: lowercased verbatim (casefold). name field: if present, NFKC + casefold.
    Step 2 — model + tenant binding: model is included verbatim (case-sensitive; model names
      must NOT be normalized — "openai/GPT-4o" and "openai/gpt-4o" are different models in
      OpenRouter and produce different outputs). model is NOT subjected to normalization.
    Step 3 — parameter fields (temperature, top_p, max_tokens, stop, n, presence_penalty,
      frequency_penalty, seed): included verbatim, absent fields excluded (same as exact cache).
    Step 4 — canonical JSON: json.dumps(normalized_subset, sort_keys=True, separators=(',',':'))
      where normalized_subset = {"model": model_verbatim, "messages": normalized_messages,
      **{k: v for k, v in body.items() if k in exact-cache field set and k not in {"model","messages"}}}
    Step 5 — key derivation: sha256(canonical_json.encode("utf-8")).hexdigest()
      Redis key: resp-cache-sem:{tenant_id}:{sha256_digest}
      Key prefix is DISTINCT from v4 exact key (resp-cache:) so both layers coexist
      and neither accidentally reads the other's entries.

  - Exact-match lookup ALWAYS runs first. On exact HIT, the semantic layer is not consulted.
    On exact MISS: if semantic_cache_enabled for the tenant, run semantic lookup.
    On semantic HIT: return cached body; record usage with cached=true cost-0 (same as exact hit);
    emit metric with label result="semantic_hit" (distinct from "hit" / "miss" / "bypass").
    On semantic MISS: call upstream; store BOTH keys (exact key and semantic key) pointing at
    the same payload. See storage shape below.

  - Storage shape for semantic key: the semantic key stores a POINTER to the exact cache key
    (a plain UTF-8 string that is the exact-cache Redis key) rather than the full body.
    This avoids doubling the storage per cached response.
    Read path on semantic HIT: GET semantic_key → exact_key string → GET exact_key → body.
    If the exact key has expired (TTL expired between the two GETs), treat as a SEMANTIC MISS
    (call upstream, re-store both keys). This is a single acceptable race window.
    Write path on miss (upstream returns 200): fire-and-forget store exact key with body (as v4);
    then fire-and-forget store semantic key with value = exact_key_string, same TTL.

  - TTL: same as v4 cache_ttl_seconds (GATEWAY_CACHE_TTL_SECONDS setting). No separate TTL.

  - Opt-in surface (per-tenant only, NOT per-key):
    A new column tenants.semantic_cache_enabled BOOLEAN NOT NULL DEFAULT false is added
    via an additive migration after e1a3f5b9c7d2 (chain head as of 2026-06-11).
    The semantic layer is active for a request iff BOTH:
      (a) the effective exact-cache is enabled (authz.cache_enabled is True — existing gate), AND
      (b) tenant.semantic_cache_enabled is True (new field on AuthzResult + TenantRow).
    Rationale for per-tenant only (not per-key): semantic normalization is a tenant policy
    (decides acceptable accuracy / recall tradeoff); individual key owners should not be able
    to opt into a "looser" cache without tenant owner consent. Per-key was weighed and rejected.
    A new field AuthzResult.semantic_cache_enabled: bool = False is added additively.
    The tenant's semantic_cache_enabled value is read at authentication time via the existing
    LEFT JOIN tenants in get_by_id() (same pattern as guardrail_configs, cache_enabled) —
    zero extra DB reads.

  - Admin surface: extend the existing GET/PUT /admin/cache endpoint (defined in
    cache_router.py) to also carry semantic_enabled: bool. New contract shape:
    GET  /admin/cache → {"enabled": bool, "semantic_enabled": bool}
    PUT  /admin/cache body: {"enabled"?: bool, "semantic_enabled"?: bool}
                    → {"enabled": bool, "semantic_enabled": bool}
    Both fields are optional in PUT body; absent = no change to that field (sentinel pattern,
    consistent with PATCH on api_keys). Requires owner or admin role (403 for member).
    ADDITIVE: the v4 "enabled" field stays with identical semantics; semantic_enabled is new.
    v4 frozen tests that only assert {"enabled": bool} continue to pass (body has more fields;
    they don't assert absence of additional fields).

  - Governance enforcement order is unchanged: governance → exact lookup → semantic lookup
    → upstream → store (exact + semantic) → record. The semantic lookup is at step 4.6
    (after exact lookup at 4.5, before upstream at step 5).

  - Cache bypass (Cache-Control: no-cache): bypasses BOTH exact and semantic lookup, calls
    upstream, re-stores BOTH keys (exact and semantic) on 200 response. Metric: "bypass".

  - Non-streaming completions only. Streaming (stream=true) bypasses both layers (same as v4).

  - Tenant isolation is absolute: semantic key includes tenant_id prefix. A semantic hit MUST
    NEVER cross tenants.

  - Model isolation: model is included verbatim (not normalized). Different model → different
    semantic key → never collide. A semantic hit MUST NEVER cross models.

  - PII: the semantic layer stores and reads using the same UNMASKED body convention as the
    exact layer. PII re-masking via evaluate_post is applied on semantic HIT read, same as exact.

  - Ledger semantics on semantic hit: usage row with cached=true cost_usd=0, same as exact hit.
    The existing _fire_record_cached helper and cached=true UsageRecordExtras path are reused
    unchanged. No new extras field needed.

  - Metrics: reuse gateway_cache_events_total counter (same MetricsRegistry attribute) with a
    new label value result="semantic_hit". Label cardinality: 5 total values
    (miss, hit, bypass, semantic_hit — "hit" stays for exact hits to avoid breaking v4 tests).
    The v4 tests assert result="hit" for exact hits — that label is preserved.
</must>

Reject:
<reject>
  - Proxy completion (non-streaming) with semantic_cache_enabled=false and a normalized-variant
    prompt → semantic lookup NOT performed; upstream called normally (MISS on exact, upstream call).

  - Proxy completion (non-streaming) with semantic_cache_enabled=true but cache_enabled=false
    → semantic layer NOT active (semantic requires both gates). Same behavior as full-cache-off.

  - Proxy completion (streaming, stream=true) with semantic_cache_enabled=true and cache_enabled=true
    → neither layer consulted; no X-Cache header; upstream called normally.

  - Normalized variant prompt crossing tenant boundary → semantic MISS (tenant_id scopes the key).

  - Normalized variant prompt crossing model boundary (e.g. gpt-4o vs gpt-4o-mini) → semantic MISS
    (model is included verbatim in the normalized key; different model = different key).

  - Negation-injected prompt ("don't delete" vs "delete") → semantic MISS (negation particle "n't"
    and space survive normalization — produces a distinct normalized string). This is the precision
    contract: negation must never cause a false hit.

  - Number-changed prompt ("in 1300" vs "in 1400") → semantic MISS (digits survive normalization).
    This is the precision contract: year/number changes must never cause a false hit.

  - PUT /admin/cache with member-role JWT → "ERR_AUTH_FORBIDDEN" (403). (Inherited from v4.)

  - PUT /admin/cache with {"semantic_enabled": true} but malformed (non-bool) → 422 ERR_PAYLOAD_INVALID.

  - Semantic HIT when exact key has already expired (race): treat as MISS; call upstream,
    re-store both keys (atomic recovery — no stale pointer returned).
</reject>

After:
<after>
  - tenants.semantic_cache_enabled BOOLEAN NOT NULL DEFAULT false column exists.
  - AuthzResult.semantic_cache_enabled bool field exists (default False).
  - GET /admin/cache returns {"enabled": bool, "semantic_enabled": bool}.
  - PUT /admin/cache with {"semantic_enabled": true} persists and returns the new value.
  - A normalized-variant prompt (different whitespace/casing/trailing-punct on last user message,
    identical semantic content) hits the semantic cache: upstream called ONCE across both requests;
    second response has X-Cache: semantic_hit; second usage row has cached=true, cost_usd=0.
  - A negation-injected or number-changed prompt MISSES semantic cache: upstream called twice.
  - A same normalized prompt from a different tenant MISSES (tenant isolation).
  - A same normalized prompt for a different model MISSES (model isolation).
  - The exact-match layer (v4) continues to work unchanged; a byte-identical prompt still gets
    X-Cache: hit (exact), not X-Cache: semantic_hit.
  - Prometheus counter gateway_cache_events_total gains result="semantic_hit" events.
  - PII masking applied on semantic-hit reads (evaluate_post on the retrieved body).
  - Advisory spend counters NOT incremented on semantic hits (cost_usd=0 path, inherited).
  - Streaming requests bypass semantic layer entirely (no X-Cache header, upstream called always).
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ POINTER STORAGE RACE WINDOW [contract]: the semantic key stores a pointer (exact key string);
    if the exact key expires between the two GETs (semantic lookup → exact body fetch), the
    semantic HIT collapses to a MISS. Under the 300s TTL, both keys are written at the same
    timestamp so they expire together — the race window is only if Redis evicts the exact key
    under memory pressure before the semantic key. Chosen resolution: treat expired-pointer as
    MISS and re-store. The alternative (store body in both keys, ~2x Redis memory) is avoided
    to keep storage O(1) per response. If wrong (pointer expiry causes noticeable miss-rate
    spike under memory pressure): store body redundantly in the semantic key as a fallback —
    a mechanical change to one helper function. Confidence: 0.85; lowest because the two-GET
    sequence has no atomicity guarantee in the absence of Lua scripts. [contract]

  ⚠ PUT /admin/cache ADDITIVE EXTENSION [contract]: the v4 frozen contract defines PUT body as
    {"enabled": bool}. The v5 extension adds {"semantic_enabled"?: bool}. Both fields are
    optional in PUT. The v4 frozen tests POST {"enabled": bool} and assert {"enabled": bool} in
    response — additive fields do not break those assertions. The risk is that a test explicitly
    asserts the response has EXACTLY one field — inspection of the frozen test shows it asserts
    put_resp.json().get("enabled") is True (no exact-key-set assertion), so additive is safe.
    Confidence: 0.90. [contract]

  - The normalization algorithm produces no false hits because negation, numbers, punctuation
    mid-sentence, and word boundaries all survive normalization verbatim. Confidence: 0.95.
    Structural argument: NFKC + casefold + whitespace-collapse + trailing-punct-of-last-user-message
    is injective on semantic content — two messages with different tokens produce different
    normalized strings. The only exception would be if two different Unicode sequences NFKC-fold
    to the same result (e.g. "ﬁ" → "fi") which is the intended behavior (ligature variants ARE
    near-duplicates). Cost if wrong: add an explicit test catching the failure mode, tighten spec.

  - AuthzResult gains semantic_cache_enabled: bool = False as an additive dataclass field
    (default-False, frozen=True allows adding fields without breaking existing construction by
    keyword). All existing frozen test suites construct AuthzResult by keyword or use factories
    that do not specify this field → default-False is safe. Confidence: 0.97.

  - No new Python packages required (unicodedata, re, hashlib, json are stdlib; all already
    imported in the gateway). Allowlist unchanged. Confidence: 1.0.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SC1 — normalized variant hits semantic cache (precision contract, variant side)
  Given a key+tenant with cache_enabled=true AND semantic_cache_enabled=true
  When request R1 is sent with prompt "  What is the capital of France?  "
  And request R2 is sent with prompt "what is the capital of france" (different whitespace/case/punct)
  Then upstream is called exactly once across R1 and R2 (semantic hit on R2)
  And R1 has X-Cache: miss (exact miss; semantic key stored on first request)
  And R2 has X-Cache: semantic_hit
  And R2 usage row has cached=true, cost_usd=0

Scenario: SC2 — negation-changed prompt MISSES (precision contract, negation side)
  Given a key+tenant with cache_enabled=true AND semantic_cache_enabled=true
  And cache is warm for "delete my account"
  When a request with "don't delete my account" is sent
  Then upstream is called (semantic MISS — negation particle survives normalization)
  And X-Cache: miss (not semantic_hit) on the response

Scenario: SC3 — number-changed prompt MISSES (precision contract, number side)
  Given a key+tenant with cache_enabled=true AND semantic_cache_enabled=true
  And cache is warm for "what is the capital of France in 1400?"
  When a request with "what is the capital of France in 1300?" is sent
  Then upstream is called (semantic MISS — digits survive normalization)
  And X-Cache: miss on the response

Scenario: SC4 — semantic layer inactive when semantic_cache_enabled=false
  Given a key+tenant with cache_enabled=true AND semantic_cache_enabled=false (default)
  When two requests are sent with prompts differing only in whitespace/case
  Then upstream is called twice (semantic layer not active)
  And neither response has X-Cache: semantic_hit

Scenario: SC5 — semantic layer inactive when cache_enabled=false
  Given a key+tenant with cache_enabled=false AND semantic_cache_enabled=true
  When two requests with normalized-variant prompts are sent
  Then upstream is called twice (overall cache gate is off)
  And neither response has any X-Cache header

Scenario: SC6 — tenant isolation: same normalized prompt, second tenant → miss
  Given tenant A with cache_enabled=true AND semantic_cache_enabled=true, cache warm for prompt P
  And tenant B with cache_enabled=true AND semantic_cache_enabled=true
  When tenant B sends normalized variant of prompt P
  Then upstream is called for tenant B (semantic MISS — different tenant_id in key)
  And tenant B response has X-Cache: miss

Scenario: SC7 — model isolation: same normalized prompt, different model → miss
  Given a tenant with cache_enabled=true AND semantic_cache_enabled=true
  And cache is warm for (prompt P, model M1)
  When a request for (normalized variant of P, model M2 != M1) is sent
  Then upstream is called (semantic MISS — model included verbatim in key)
  And X-Cache: miss on response

Scenario: SC8 — exact-match layer regression: byte-identical prompt still hits exact cache
  Given a tenant with cache_enabled=true AND semantic_cache_enabled=true
  When two byte-identical requests are sent
  Then exact-match cache serves the second request (X-Cache: hit, NOT semantic_hit)
  And upstream is called exactly once

Scenario: SC9 — ledger row for semantic hit carries cached=true cost 0
  Given a tenant with cache_enabled=true AND semantic_cache_enabled=true
  When prompt P is sent (miss), then normalized variant P' is sent (semantic hit)
  Then the usage row for P' has cached=true in raw, cost_usd=0, real token counts from P's body

Scenario: SC10 — semantic_hit metric counter increments
  Given a tenant with both cache flags true
  When a semantic hit occurs
  Then gateway_cache_events_total{result="semantic_hit"} increments by 1
  And gateway_cache_events_total{result="miss"} increments by 1 for the first request

Scenario: SC11 — PII re-masked on semantic hit body
  Given a tenant with guardrail pii_mask enabled AND both cache flags true
  And cache is warm with an UNMASKED body (stored before masking)
  When a normalized variant prompt is sent (semantic hit)
  Then the returned body has PII masked (evaluate_post called on the cached body)
  And the Redis-stored body remains UNMASKED (cache stores unmasked body)

Scenario: SC12 — admin toggle: PUT /admin/cache with semantic_enabled=true round-trip
  Given an owner JWT for a tenant
  When PUT /admin/cache {"semantic_enabled": true} is sent
  Then response is 200 {"enabled": bool, "semantic_enabled": true}
  And GET /admin/cache returns {"semantic_enabled": true}
  And the DB row has semantic_cache_enabled=true

Scenario: SC13 — member role 403 on PUT /admin/cache with semantic_enabled
  Given a member-role JWT for a tenant
  When PUT /admin/cache {"semantic_enabled": true} is sent
  Then response is 403 ERR_AUTH_FORBIDDEN

Scenario: SC14 — streaming bypasses semantic layer
  Given a tenant with cache_enabled=true AND semantic_cache_enabled=true
  When two streaming requests (stream=true) with normalized-variant prompts are sent
  Then upstream is called twice (streaming never touches semantic layer)
  And neither response has X-Cache: semantic_hit

Scenario: SC15 — Cache-Control: no-cache bypasses semantic layer and re-stores both keys
  Given a tenant with both cache flags true, warm cache for prompt P
  When a normalized variant P' is sent with Cache-Control: no-cache
  Then upstream is called (bypass; X-Cache: bypass)
  And subsequent P' request without bypass returns X-Cache: semantic_hit (semantic key re-stored)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/cache
  200 -> { enabled: bool, semantic_enabled: bool }  # ADDITIVE: v4 returns {enabled}; v5 adds semantic_enabled
  401 -> { code: "ERR_AUTH_INVALID_KEY" }

PUT /admin/cache   body: { enabled?: bool, semantic_enabled?: bool }
  200 -> { enabled: bool, semantic_enabled: bool }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }   # member role
  422 -> { code: "ERR_PAYLOAD_INVALID" }  # either field wrong type

POST /v1/chat/completions (non-streaming, both cache flags enabled)
  EXACT MISS + SEMANTIC HIT:
    200 -> cached upstream body verbatim
    X-Cache: semantic_hit
  EXACT MISS + SEMANTIC MISS:
    200 -> upstream body verbatim
    X-Cache: miss
  EXACT HIT (semantic layer not consulted):
    200 -> cached upstream body verbatim
    X-Cache: hit     (unchanged from v4)
  BYPASS (Cache-Control: no-cache):
    200 -> fresh upstream body verbatim
    X-Cache: bypass  (unchanged from v4)

POST /v1/chat/completions (streaming, stream=true, any cache settings)
  X-Cache header: ABSENT (semantic layer completely bypassed)

Schema touched (ADDITIVE):
  tenants: ADD COLUMN semantic_cache_enabled BOOLEAN NOT NULL DEFAULT false
  Migration ID: <new_id>_semantic_caching (after e1a3f5b9c7d2)
  Rollback: DROP COLUMN tenants.semantic_cache_enabled
  (safe — additive column with server default; no existing code references it pre-migration)

AuthzResult (additive field):
  semantic_cache_enabled: bool = False
  Populated at auth time from tenants.semantic_cache_enabled via the existing LEFT JOIN tenants
  in get_by_id(). Zero extra DB reads (tenant row already joined for cache_enabled and
  guardrail_configs).

Cache key derivation for semantic layer:
  Redis key:       resp-cache-sem:{tenant_id}:{sha256(canonical_sem_json)}
  Exact-layer key: resp-cache:{tenant_id}:{sha256(canonical_json)}   (UNCHANGED, v4)

  Normalization function build_semantic_cache_key(tenant_id: str, payload: dict) -> str:
    1. Extract messages list from payload.
    2. For each message dict in messages:
       a. Copy dict; normalize content field:
          content = unicodedata.normalize("NFKC", str(message.get("content", "")))
          content = content.casefold()
          content = re.sub(r'\s+', ' ', content).strip()
          If this is the last message and message["role"].casefold() == "user":
            content = re.sub(r'[.!?,;:]+$', '', content).rstrip()
       b. Normalize role: message["role"].casefold()
       c. If "name" in message: message["name"] = unicodedata.normalize("NFKC", name).casefold()
    3. Build normalized_subset dict:
       { "model": payload["model"],   # verbatim — NOT normalized
         "messages": normalized_messages,
         **{k: v for k, v in payload.items()
            if k in {"temperature","top_p","max_tokens","stop","n",
                     "presence_penalty","frequency_penalty","seed"} }
       }  # absent fields excluded (exact-cache convention)
    4. canonical = json.dumps(normalized_subset, sort_keys=True, separators=(',',':'))
    5. digest = hashlib.sha256(canonical.encode()).hexdigest()
    6. return f"resp-cache-sem:{tenant_id}:{digest}"

Storage shape for semantic key (pointer, NOT copy):
  Redis value at semantic key: the EXACT-LAYER Redis key string (UTF-8, plain text).
  E.g. "resp-cache:abc123:deadbeef..."
  On semantic HIT read:
    sem_key → exact_key_string (GET sem_key)
    body → GET exact_key_string
    If exact_key_string is None OR body is None → treat as MISS (race: exact key expired)
  On MISS (upstream 200 returned):
    fire-and-forget: SET exact_key body TTL=ttl_seconds    (v4, unchanged)
    fire-and-forget: SET sem_key exact_key_string TTL=ttl_seconds  (v5, new)

Lookup order within complete() method (non-streaming, cache_enabled=True):
  Step 4.5a: build exact_key; attempt exact GET (existing v4 logic, unchanged)
  Step 4.5b: on exact MISS only: if authz.semantic_cache_enabled:
               build sem_key; GET sem_key → exact_key_str
               if exact_key_str: GET exact_key_str → cached_body
               if cached_body: SEMANTIC HIT path (same as exact HIT but metric="semantic_hit")
               else: SEMANTIC MISS (pointer expired → continue to upstream)
             else: skip semantic lookup
  Step 5:   upstream call (on all misses and bypasses)
  Step 6:   on upstream 200: fire-and-forget store exact key (v4) + fire-and-forget store sem key (v5)

Effective semantic layer activation:
  active = authz.cache_enabled AND authz.semantic_cache_enabled
  authz.cache_enabled:          resolved from api_keys.cache_enabled OR tenants.cache_enabled (v4)
  authz.semantic_cache_enabled: resolved from tenants.semantic_cache_enabled (new column)
  Both resolved at auth time via LEFT JOIN tenants in ApiKeyRepository.get_by_id()
  (that query already reads tenants.cache_enabled and tenants.guardrail_configs — additive)

Usage recording on semantic hit:
  Identical to exact hit: _fire_record_cached(usage_recorder, ...) (cached=True extras)
  cost_usd=0 (same recorder path), token counts from cached body, cached=true in raw.
  No new UsageRecordExtras field needed — cached=True already declares semantic intent.
  ⚠ [spec] If a future task needs to distinguish "exact hit" vs "semantic hit" in the ledger,
    a cache_layer: str field can be added to UsageRecordExtras. Not needed for v5.

Metrics:
  MetricsRegistry.cache_events_total counter (existing gateway_cache_events_total)
  New label value: result="semantic_hit"
  Existing label values unchanged: "hit" (exact), "miss", "bypass"
  Label cardinality: 4 values — within acceptable bounds.
  v4 frozen tests assert result="hit" for exact hits — UNAFFECTED (label preserved).

Modules touched (hard boundary — BUILD must not add new modules outside this list):
  - apps/gateway/src/gateway/proxy/infrastructure/response_cache.py
    (add build_semantic_cache_key + semantic lookup helpers)
  - apps/gateway/src/gateway/proxy/application/use_cases.py
    (extend complete() step 4.5b: semantic lookup after exact miss)
  - apps/gateway/src/gateway/proxy/domain/ports.py
    (ADDITIVE: ResponseCache gains async get_pointer + set_pointer OR reuse get/set with
     type awareness — recommend: extend ResponseCache with pointer-aware get/set overloads
     OR add helper functions at infrastructure level only; domain port is unchanged if
     helpers live in response_cache.py. DECISION: keep domain port unchanged; pointer
     operations are infrastructure helpers — domain port stays frozen.)
  - apps/gateway/src/gateway/keys/domain/entities.py
    (add semantic_cache_enabled: bool = False to AuthzResult)
  - apps/gateway/src/gateway/keys/infrastructure/repository.py
    (extend get_by_id() SELECT to also read tenants.semantic_cache_enabled;
     already reads tenants.cache_enabled + guardrail_configs — additive column)
  - apps/gateway/src/gateway/tenants/api/cache_router.py
    (extend GET/PUT to include semantic_enabled field)
  - apps/gateway/src/gateway/tenants/infrastructure/orm.py
    (add semantic_cache_enabled column to TenantRow)
  - apps/gateway/migrations/versions/<new_id>_semantic_caching.py
    (additive migration for tenants.semantic_cache_enabled)
  - apps/gateway/pyproject.toml
    (add tests/semantic_cache/test_semantic_cache.py to ruff format exclude list)

No new Python packages. No new tables. EXPECTED_TABLES manifest unchanged.

ResponseCache domain port: NOT modified (pointer operations are infrastructure concerns;
  build_semantic_cache_key and the two-step GET live in response_cache.py infrastructure
  module, never in the domain port).

Flags for freeze (lowest-confidence points across the bundle):

  ⚠ [contract] POINTER STORAGE TWO-GET RACE: semantic key stores the exact key string;
    on HIT, two sequential Redis GETs with no atomicity. Under normal TTL parity (both
    keys written at same time, same TTL), expiry is simultaneous. Under Redis LRU eviction
    or key deletion, the exact key may expire first, making the pointer dangle. Chosen
    resolution: treat dangling pointer as MISS (re-store both keys). Cost if wrong: rare
    spurious misses under memory pressure — observable in metrics. If unacceptable: use a
    Lua EVALSHA for atomic get-dereference OR store body redundantly. Both are mechanical
    changes contained to response_cache.py + use_cases.py. [contract]

  ⚠ [contract] PUT /admin/cache additive extension: v4 frozen tests assert
    put_resp.json().get("enabled") is True — this uses .get() not key-set equality, so
    additional fields in the v5 response do NOT break v4 frozen tests. Confirmed by reading
    the frozen test: test_member_role_forbidden_on_put_cache only asserts 403 code (no body
    check), test_get_cache_returns_current_setting asserts body["enabled"] is False/True.
    No assertion of exact field set. Risk: zero. [contract]

  ⚠ [test] SC2/SC3 precision tests (negation + number change → MISS) are the contract
    tests for the FALSE-HIT bound. If the normalization spec is too aggressive (accidentally
    normalizes digits or negation particles), these tests reveal it at BUILD stage. Cost:
    normalization spec must be corrected (which is always a change-request on a frozen contract).
    These are the two tests most likely to expose a spec error at build time. [test]
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-11).
  Orchestrator decisions at freeze: (1) pointer two-GET race RESOLVED as
  dangling-pointer-treated-as-MISS + re-store both keys — a rare benign miss during
  the TTL-expiry window is strictly cheaper than Lua/EVALSHA atomicity on the hot
  path; no Lua. (2) normalization-only framing APPROVED — false hits structurally
  impossible (negations/digits survive normalization verbatim), naming-honesty
  paragraph carries the overclaim risk; risk: moderate · autonomy: auto accepted on
  that basis. Red re-run by orchestrator: 14 failed (right reasons — admin-surface
  gate fires first) + 1 passed (SC13 role-gate anchor, green-by-design); frozen
  response_caching 14/14 — authoritative.

Least-sure flag surfaced at freeze:
  ⚠ [contract] Pointer-storage two-GET race — semantic key stores a pointer to the exact
    key string; two sequential GETs have no atomicity guarantee (Redis does not have a native
    GETDEREF). Under normal TTL parity both keys expire together so the race window is only
    LRU eviction. Chosen resolution: dangling pointer → MISS + re-store. Cost if wrong:
    spurious misses under memory pressure. Alternative: Lua script or redundant body storage.
    Requires human judgment at freeze: accept the MISS fallback, or mandate the Lua path?
  ⚠ [contract] Additive PUT /admin/cache extension verified safe for v4 frozen tests
    (all v4 cache-router assertions use .get(field) not exact key-set equality). Confirmed
    by reading frozen suite. Cost if wrong: a frozen test breaks → never-edit means change
    request, never test edit.
  ⚠ [test] SC2 (negation) and SC3 (number) precision tests are the false-hit guard;
    normalization algorithm correctness is only proven by running them red-then-green.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_semantic_cache_normalized_variant_hits: SC1 — arrange both cache flags true; act
    request with padded/upper-case prompt variant; assert upstream.calls==1 on second request,
    X-Cache: semantic_hit, usage row cached=true cost_usd=0.
  - test_negation_change_misses_semantic_cache: SC2 — arrange warm cache for "delete my account";
    act with "don't delete my account"; assert upstream.calls==2, X-Cache: miss (NOT semantic_hit).
  - test_number_change_misses_semantic_cache: SC3 — arrange warm cache for prompt with year;
    act with year changed; assert upstream.calls==2, X-Cache: miss.
  - test_semantic_cache_inactive_when_disabled: SC4 — arrange semantic_cache_enabled=false (default),
    cache_enabled=true; act two normalized-variant requests; assert upstream.calls==2, no semantic_hit.
  - test_semantic_cache_inactive_when_cache_disabled: SC5 — arrange cache_enabled=false, even if
    semantic flag were true; act two requests; assert upstream.calls==2, no X-Cache header.
  - test_semantic_tenant_isolation: SC6 — arrange two tenants both with flags true; warm tenant A;
    tenant B sends normalized variant; assert upstream called for B; X-Cache: miss on B response.
  - test_semantic_model_isolation: SC7 — arrange warm for model M1; act with model M2 (same normalized
    prompt); assert upstream.calls==2, X-Cache: miss.
  - test_exact_cache_regression_unchanged: SC8 — arrange both flags true; send byte-identical request
    twice; assert X-Cache: hit (exact, NOT semantic_hit), upstream.calls==1.
  - test_semantic_hit_ledger_cached_true_cost_zero: SC9 — arrange both flags true; warm with P; act
    with normalized variant P'; assert usage row: cached=true, cost_usd=0, token counts from P.
  - test_semantic_hit_metric_increments: SC10 — arrange both flags true; warm + send variant; assert
    cache_events_total{result="semantic_hit"} increments by 1.
  - test_pii_remasked_on_semantic_hit: SC11 — arrange guardrail pii_mask + both cache flags; warm;
    act with normalized variant; assert returned body has PII placeholder; Redis body stays unmasked.
  - test_admin_toggle_semantic_enabled_roundtrip: SC12 — act PUT /admin/cache {"semantic_enabled":true};
    assert 200, body has semantic_enabled=true; GET returns same; DB row has it.
  - test_member_role_forbidden_on_semantic_toggle: SC13 — member JWT; PUT /admin/cache
    {"semantic_enabled": true}; assert 403 ERR_AUTH_FORBIDDEN.
  - test_streaming_bypasses_semantic_layer: SC14 — arrange both flags true; two streaming requests
    with normalized-variant prompts; assert upstream.calls==2, no X-Cache: semantic_hit.
  - test_bypass_restores_semantic_key: SC15 — arrange warm; send normalized variant with
    Cache-Control: no-cache; assert X-Cache: bypass; subsequent variant without bypass → semantic_hit.
</test_plan>

Tests live in: `apps/gateway/tests/semantic_cache/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): The semantic lookup MUST run AFTER the exact lookup (order:
exact → semantic → upstream). A false hit is strictly worse than a miss — the normalization
algorithm must NEVER merge semantically distinct content. Semantic key MUST include tenant_id
prefix — cross-tenant hits are a security violation, always HARD-STOP. Pointer dereference
failure (dangling pointer) MUST result in a MISS, never a stale body return.
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

Watch (reuse scenarios as monitors): semantic_hit rate per tenant per model; pointer-miss rate
(semantic key exists but exact key expired — observable if we add a log line on dangling pointer);
semantic vs exact hit ratio (should be << 1 unless clients send many near-duplicate prompts)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
</content>
