# TASK: Embedding-similarity vector cache — near-duplicate prompts hit above a cosine threshold (v19 task 5)

slug: semantic-cache · created: 2026-06-15 · stage: production · risk: high
autonomy: conservative   <!-- HIGH-RISK (true embedding similarity over a CONTINUOUS threshold — a false hit serves a wrong-but-plausible answer, strictly worse than a miss; new hot-path IO dependency on an embedding provider). Lowered from auto so the verify gate REQUIRES Tin's sign-off (unguarded_high_risk_auto guard). Per Tin's v19 pacing: build to verify, then STOP. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Feature: a THIRD response-cache lookup layer for non-streaming chat completions — true
EMBEDDING-SIMILARITY matching. After the exact-match (v8 `resp-cache:`) and the normalized
near-duplicate (v?, "semantic" `resp-cache-sem:`) layers both MISS, embed the prompt and serve a
cached response when the cosine similarity to a previously-cached prompt is at or above a
configurable threshold. Per-tenant + per-model isolated, default-off, billed on miss only.

NAMING HONESTY (critical — a collision the milestone wording hides): the codebase ALREADY ships a
layer it calls "semantic" — but that layer is NORMALIZATION-ONLY (NFKC + casefold + whitespace +
trailing-punct), a BINARY match with NO threshold (`build_semantic_cache_key`, `semantic_cache_enabled`,
`x_cache="semantic_hit"`, the `tenants.semantic_cache_enabled` column + admin toggle — all live,
grep-confirmed). This v19 task is the milestone's "true embedding-similarity (semantic) cache"
(intake amendment, Tin 2026-06-15) — a genuinely DIFFERENT mechanism (continuous cosine threshold,
catches paraphrases the normalization layer cannot). To avoid overloading the taken `semantic_*`
namespace, this layer is named **vector cache** internally (`vector_cache_*`, `x_cache="vector_hit"`,
`RedisVectorCache`). The milestone's "SEMANTIC cache hit" maps to `vector_hit`. The exit criterion's
"semantic similarity above the configured threshold" is satisfiable ONLY by a continuous metric — the
normalization layer (binary) cannot deliver it, which is why a new layer is required, not an extension.

Touches (files · symbols · signatures):
- `proxy/infrastructure/response_cache.py` (REUSE, no change) — `build_cache_key` (exact key
  `resp-cache:{tenant}:{sha256}`) is the body store the vector layer POINTS at; `RedisResponseCache.get/set/get_pointer/set_pointer`
  is the pointer-storage pattern this layer mirrors. The two-GET dangling-pointer→MISS discipline is
  the precedent.
- `proxy/infrastructure/vector_cache.py` (NEW) — `cosine_similarity(a, b) -> float` (pure-Python,
  TOTAL: 0.0 on zero-norm / length mismatch) + `class RedisVectorCache` with
  `async lookup(*, tenant_id, model, body) -> dict | None` and
  `async store(*, tenant_id, model, body, response_body, ttl) -> None`. Owns: embed the query text,
  bounded cosine NN scan over a per-(tenant,model) Redis index, pointer dereference to the exact body.
  ALL failures (embed None, redis error, scan error, dangling pointer) degrade to MISS / no-op.
- `proxy/application/use_cases.py:CompletionUseCase` — ctor gains additive `vector_cache: VectorCache | None = None`
  (default None ⇒ feature OFF ⇒ byte-identical). `complete()` Step 4.5 cache block gains **Step 4.5c**:
  after the exact-miss + normalization-miss branch, before `x_cache="miss"`, when `self._vector_cache`
  is wired → `await self._vector_cache.lookup(...)`; a hit takes the SAME billing/metric/PII/TPM path as
  the existing exact/semantic hit (`_fire_record_cached` $0, `cache_events_total{result="vector_hit"}`,
  `evaluate_post`, TPM post-account) and returns `(200, body, "vector_hit")`. On upstream 200 (not bypass)
  → fire-and-forget `self._vector_cache.store(...)`.
- `proxy/domain/ports.py` — ADD a `VectorCache` Protocol (lookup/store) for the ctor type. (Domain
  port for the EXISTING ResponseCache stays frozen.)
- `core/config.py:Settings` — ADD `vector_cache_enabled: bool=False`, `vector_cache_threshold: float=0.95
  (ge=0,le=1)`, `vector_cache_embed_model: str=""`, `vector_cache_max_candidates: int=100 (ge=1,le=1000)`.
- `main.py` (~app.state) — ADD `app.state.vector_cache_enabled` + threshold/model/max from settings.
- `proxy/api/deps.py:get_completion_use_case` — when `settings.vector_cache_enabled` AND a redis_client AND
  a non-empty embed_model: construct `RedisVectorCache(redis, embedder=<registry-backed adapter>,
  threshold=..., max_candidates=...)` and pass it; else None. The embedder is a closure over the
  per-request ProviderRegistry that calls `select_provider(<embed model modality/provider>, registry)`
  then `post_json("/embeddings", {model, input})`, returns `data[0].embedding` (list[float]) or None on
  any error (timeout/5xx/shape). The internal embedding cost is NEVER billed to the served request.
- REUSE (no change): `_fire_record_cached` / `_fire_record_with_raw` (use_cases, billing helpers);
  `select_provider` + `ProviderRegistry` (embedder); `MetricsRegistry.cache_events_total` (new label
  value `vector_hit`); `evaluate_post` (PII on hit).

Context (working folder):
- `.add/milestones/v19/MILESTONE.md` — shared contract "The SEMANTIC-CACHE KEY+THRESHOLD contract — how
  a prompt maps to an embedding, the similarity metric + configurable hit threshold, and the per-tenant
  namespace." BILLING ACCURACY: a cache hit bills $0 and never re-bills; the embedding lookup done FOR the
  cache is an internal cost, never billed. TENANT ISOLATION absolute. DESIGN-FOR-FAILURE: embedding/lookup
  failure degrades to a MISS, never fails the request. OPT-IN/DEFAULT-OFF byte-identical.
- depends-on: cache-controls (DONE) — reuses the exact-key body store + TTL resolution.

Honors (patterns / conventions):
- ALLOWLIST / STDLIB-FIRST: cosine is pure Python (`math`), no numpy/scipy (grep-confirmed: numpy is
  neither a dep nor importable). Consistent with the v5 normalization layer (stdlib only).
- POINTER STORAGE (precedent): the vector entry stores a POINTER to the exact-cache key (not a body copy);
  dangling pointer (exact body expired) → MISS, identical to the normalization layer's two-GET discipline.
- FAIL-SAFE / DESIGN-FOR-FAILURE (CLAUDE.md): every IO (embed call, redis ops) is wrapped; failure →
  MISS / no-op; the served request never fails because of the vector layer. The embed call carries a bounded
  timeout (provider adapter default) and is fired only after both cheaper layers miss.
- SINGLE-BILL (v12): only the served attempt bills; a vector hit bills $0 (cached); the internal embed call
  is unbilled. Store is fire-and-forget (never blocks the response).
- TENANT + MODEL ISOLATION: the Redis index namespace is `vec-cache:{tenant}:{model_hash}:…` — a hit can
  NEVER cross tenants or models (different model → different output → must not share a vector index).

Anchors the contract cites:
- `cosine_similarity(a: list[float], b: list[float]) -> float` (NEW pure fn).
- `RedisVectorCache.lookup(*, tenant_id, model, body) -> dict | None` / `.store(*, tenant_id, model, body, response_body, ttl) -> None` (NEW).
- `VectorCache` Protocol (NEW domain port) + `CompletionUseCase(..., vector_cache: VectorCache | None = None)`.
- `Settings.vector_cache_{enabled,threshold,embed_model,max_candidates}` (NEW knobs).
- `x_cache="vector_hit"` + `cache_events_total{result="vector_hit"}` (NEW marker/label value).
- REUSE: `build_cache_key`, `_fire_record_cached`, `select_provider`, `evaluate_post`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Embedding-similarity ("vector") response cache — a third lookup layer that serves a cached
chat-completion when the prompt's embedding is within a configurable cosine threshold of a cached prompt.

Framings weighed:
- **(a) real embeddings via the gateway's existing embedding upstream + per-(tenant,model) Redis vector
  index + pure-Python bounded cosine NN (chosen)** — delivers TRUE semantic similarity (the milestone's
  explicit ask + the only framing that satisfies "similarity above the configured threshold"), reuses the
  embedding provider the gateway already proxies, needs NO new package (stdlib cosine), NO new Redis module
  (linear scan over a capped candidate list). Cost: an embedding call on the hot path AFTER both cheaper
  layers miss (bounded, internal/unbilled, fail-safe→miss); O(candidates·dim) scan (capped, opt-in).
- **(b) MinHash/LSH or normalization-only** (rejected) — the normalization layer ALREADY exists and is
  binary (no threshold); it cannot satisfy "similarity above the configured threshold". Re-implementing it
  is redundant.
- **(c) Redis Stack VSS / RediSearch / a vector DB** (rejected for v19) — introduces a new infra
  dependency/module not in the locked stack; the opt-in bounded linear scan is sufficient for v19 and
  keeps the stack unchanged. Deferred if scan latency telemetry shows a gap.
- **(d) per-tenant DB toggle + admin API (like the v5 normalization layer)** (deferred) — v19 milestone
  scopes reliability policy as "env/config-driven in v19; per-tenant admin overrides are OUT". So the
  vector layer is GLOBAL config-gated (one operator knob), per-tenant ISOLATED by key namespace. No
  migration → smaller blast radius. A per-tenant admin toggle is a clean future task.

Must:
<must>
  - The vector layer is the THIRD lookup, consulted ONLY after BOTH the exact (`resp-cache:`) and the
    normalization ("semantic" `resp-cache-sem:`) layers MISS, AND only when the layer is active.
    Lookup order: exact → normalization → vector → upstream. An exact OR normalization hit returns
    immediately; the vector layer is never consulted then.
  - ACTIVE iff: `vector_cache` collaborator is wired (settings.vector_cache_enabled AND redis present AND
    a non-empty embed model) AND the request's effective exact-cache gate is on (`authz.cache_enabled`).
    Default-off: with vector_cache absent the complete() path is byte-identical to today.
  - QUERY TEXT (PINNED — this is the contract; changing it is a change request): the text embedded is the
    content of the LAST message whose role casefolds to "user" (stringified). No user message ⇒ no lookup,
    no store (the layer is a no-op). Rationale: the last user turn is the discriminating prompt; embedding
    the whole transcript would make every multi-turn conversation a near-unique vector (no recall).
  - LOOKUP: embed the query text; if the embedder returns None → MISS (fail-safe). Else scan up to
    `max_candidates` stored vectors for this (tenant, model); compute cosine similarity to each; take the
    BEST. If best ≥ threshold → dereference its pointer (GET exact-key body); if the body exists →
    VECTOR HIT (return it); if the pointer dangles (body expired) → MISS. If best < threshold or no
    candidates → MISS.
  - On VECTOR HIT: x_cache="vector_hit"; `_fire_record_cached` ($0, cached=true, token counts from the
    cached body); `cache_events_total{result="vector_hit"}.inc()`; apply `evaluate_post` PII mask on the
    returned body (same as exact/semantic hit, fail-OPEN); TPM post-account from cached tokens. Upstream is
    NOT called.
  - On VECTOR MISS (after the upstream 200, not on bypass, not on non-200): fire-and-forget `store(...)`:
    embed the query text (None ⇒ skip), write the entry `{v: vector, k: exact_key}` and register it in the
    per-(tenant,model) index, capped at `max_candidates` (oldest trimmed). The exact_key is the SAME
    `build_cache_key` value already stored in this block, so the vector entry points at the live body.
  - TENANT + MODEL ISOLATION: the index + entry keys are namespaced `vec-cache:{tenant_id}:{sha256(model)}:…`.
    A hit can never cross tenants or models.
  - BILLING ACCURACY: vector hit bills $0 (cached); the internal embed call (lookup AND store) is NEVER
    billed to the served request; single-bill preserved (only a MISS that reaches upstream bills, once).
  - FAIL-SAFE: any exception in embed / redis / scan / json is swallowed → MISS (lookup) or no-op (store).
    The vector layer can never fail, slow-fail, or error a user request beyond the bounded embed timeout.
  - SECRET DISCIPLINE: provider keys never touch vector code paths' logs, keys, or labels. Redis keys are
    `tenant:sha256(model):id` — no raw prompt text in keys. The embedding VECTOR is stored (floats), not the
    prompt; the body lives only in the existing exact key (same trust boundary as today).
  - STREAMING: streaming requests never consult or populate the vector layer (it lives in the
    non-streaming complete() cache block only — same boundary as exact/semantic).
  - METRICS: reuse `cache_events_total`; ADD label value `result="vector_hit"`. Existing values
    (hit, semantic_hit, miss, bypass) unchanged — v8/v? frozen tests assert those and stay green.
</must>

Reject:
<reject>
  - Non-streaming completion, vector layer OFF (default), two near-duplicate prompts → vector lookup NOT
    performed; upstream called for both (today's behavior, byte-identical). No "vector_hit".
  - Non-streaming completion, vector ON, cache_enabled=false → vector layer NOT active (requires the exact
    gate on). Upstream called normally; no X-Cache.
  - Below-threshold prompt (cosine < threshold to every candidate) → MISS; upstream called; x_cache="miss".
  - Same prompt, DIFFERENT tenant → MISS (tenant namespace in the index key).
  - Same prompt, DIFFERENT model → MISS (model hash in the index key).
  - Embedder returns None (provider down / timeout / bad shape) → MISS (lookup) and no-op (store) — request
    proceeds to upstream and is served normally (fail-safe).
  - Redis error during scan/get → MISS (swallowed).
  - Dangling pointer (best candidate ≥ threshold but its exact body expired) → MISS; re-store on the
    subsequent upstream 200.
  - Exact HIT or normalization HIT present → vector layer NOT consulted (short-circuit).
  - Streaming request (stream=true), vector ON → vector layer not consulted/populated; no X-Cache:vector_hit.
  - A message list with no user message → no lookup, no store (no-op).
</reject>

After:
<after>
  - A second prompt whose last-user-message embedding is ≥ threshold to a cached prompt's embedding (and
    both cheaper layers miss) returns the cached body with X-Cache: vector_hit; upstream called ONCE across
    the two requests; the second usage row is cached=true cost_usd=0.
  - A below-threshold prompt MISSES; upstream called for both; both x_cache="miss".
  - Cross-tenant and cross-model near-duplicates MISS.
  - With the layer off (default) the complete() path is byte-identical; all frozen suites green.
  - `cache_events_total{result="vector_hit"}` increments on a vector hit; `{result="miss"}` on a miss.
  - Embedder/redis failure degrades to MISS; the request is served by upstream; no error surfaces.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ EMBED-ON-HOT-PATH ARCHITECTURE [contract]: the chosen framing calls a real embedding provider on the
    request hot path (after exact+normalization miss) for both lookup and store. This adds latency + an
    outbound dependency + internal cost the v5 normalization task explicitly avoided. It is in BECAUSE the
    v19 milestone explicitly pulled in "true embedding-similarity" (Tin 2026-06-15) and a continuous
    threshold is unachievable without embeddings. Mitigations: default-off; only fires after both cheaper
    layers miss; bounded provider timeout; fail-safe→miss; store is fire-and-forget. If wrong (latency/cost
    unacceptable in practice): the layer is one config flag away from fully off, and an in-process
    lightweight embedder or async-precompute is a contained follow-up. Confidence: 0.80 — lowest because it
    is the one judgment a reviewer is most likely to challenge at the verify gate. [contract]
  ⚠ FALSE-HIT TOLERANCE AT THRESHOLD [contract]: unlike normalization (structurally zero false hits), a
    cosine threshold admits false hits — two semantically different prompts can sit above the threshold.
    Default 0.95 is conservative (near-identical). A false hit serves a wrong-but-plausible cached answer —
    strictly worse than a miss. Mitigation: high default threshold + opt-in + the threshold is operator-
    tunable. If wrong (false hits observed): raise the default. Confidence: 0.82. [contract]
  - Pure-Python cosine over ≤ max_candidates (default 100) vectors of provider dim (~1.5k floats) is fast
    enough for an opt-in feature (~150k mults/lookup). Confidence: 0.9. If wrong: lower max_candidates or
    add numpy (allowlist change). [contract]
  - The additive `vector_cache` ctor kwarg (default None) + the new VectorCache Protocol do not perturb any
    frozen suite (all construct CompletionUseCase positionally/by-keyword without this arg). Confidence: 0.96.
  - No new package (math/json/hashlib stdlib; redis + the embedding upstream already present). Confidence: 1.0.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: VC1 — above-threshold near-duplicate hits the vector cache
  Given the vector layer is active and exact+normalization both miss
  And a prior prompt P was cached with embedding E_P
  When a prompt P' whose last-user embedding has cosine(E_P', E_P) >= threshold is sent
  Then the cached body is returned with X-Cache: vector_hit
  And upstream is called exactly once across P and P'
  And the P' usage row is cached=true, cost_usd=0
  And the internal embedding lookup is NOT billed to P'

Scenario: VC2 — below-threshold prompt misses
  Given the vector layer is active and a prior prompt P was cached
  When a prompt whose embedding has cosine < threshold to every candidate is sent
  Then upstream is called (MISS); X-Cache: miss
  And no vector_hit metric is emitted for it

Scenario: VC3 — tenant isolation
  Given tenant A cached prompt P (vector layer active)
  When tenant B sends a prompt with an identical embedding
  Then upstream is called for B (MISS — different tenant namespace); X-Cache: miss

Scenario: VC4 — model isolation
  Given (prompt P, model M1) cached (vector layer active)
  When (identical embedding, model M2 != M1) is sent
  Then upstream is called (MISS — model hash differs); X-Cache: miss

Scenario: VC5 — embedder failure degrades to miss (fail-safe)
  Given the vector layer is active but the embedder returns None
  When any prompt is sent
  Then upstream is called normally (MISS); the request is served; no error surfaces

Scenario: VC6 — dangling pointer treated as miss
  Given a candidate embedding is >= threshold but its exact body has expired
  When that prompt is sent
  Then upstream is called (MISS); the entry is re-stored on the 200

Scenario: VC7 — exact hit short-circuits the vector layer
  Given a byte-identical prompt that hits the exact cache
  When it is sent (vector layer active)
  Then X-Cache: hit (exact); the vector layer (embedder) is NOT consulted

Scenario: VC8 — default-off is byte-identical
  Given the vector layer is NOT wired (default)
  When two near-duplicate prompts are sent
  Then upstream is called twice; no X-Cache: vector_hit; behavior is today's exactly

Scenario: VC9 — vector_hit metric increments
  Given the vector layer is active
  When a vector hit occurs
  Then cache_events_total{result="vector_hit"} increments by 1
  And the first (miss) request increments {result="miss"}

Scenario: VC10 — store caps the per-(tenant,model) index at max_candidates
  Given max_candidates = N and N entries already stored for (tenant, model)
  When an (N+1)th distinct prompt is cached
  Then the index holds at most N entries (oldest trimmed)

Scenario: VC11 — cosine is total
  Given two vectors of different lengths OR a zero vector
  When cosine_similarity is computed
  Then it returns 0.0 (never raises, never NaN)

Scenario: VC12 — no user message is a no-op
  Given a messages list with only a system/assistant message (no user)
  When sent with the vector layer active
  Then no embed call is made for lookup or store; upstream serves normally
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /v1/chat/completions (non-streaming; vector layer active = settings.vector_cache_enabled
                           AND redis AND non-empty embed model AND authz.cache_enabled)
  EXACT MISS + NORMALIZATION MISS + VECTOR HIT (best cosine >= threshold, pointer live):
    200 -> cached upstream body verbatim ;  X-Cache: vector_hit
  EXACT MISS + NORMALIZATION MISS + VECTOR MISS (below threshold / no candidates / dangling / embed None):
    200 -> upstream body verbatim ;  X-Cache: miss
  EXACT HIT or NORMALIZATION HIT:
    vector layer NOT consulted ;  X-Cache: hit | semantic_hit  (unchanged)
  STREAMING (stream=true): vector layer never consulted/populated ; X-Cache absent (unchanged)

New pure function (proxy/infrastructure/vector_cache.py):
  cosine_similarity(a: list[float], b: list[float]) -> float
    TOTAL: returns 0.0 when len(a) != len(b), either is empty, or either has zero L2 norm.
    Else sum(ai*bi) / (||a|| * ||b||). Never raises, never NaN/inf.

New collaborator (proxy/infrastructure/vector_cache.py):
  class RedisVectorCache:
    __init__(self, redis, *, embedder: Callable[[str], Awaitable[list[float] | None]],
             threshold: float, max_candidates: int)
    async lookup(self, *, tenant_id: str, model: str, body: dict) -> dict | None
      query = last-user-message content (str); no user msg -> return None (no embed call)
      vec = await embedder(query); vec is None -> return None
      idx = vec-cache:{tenant_id}:{sha256(model)}:idx           (Redis LIST of entry ids, newest-first)
      ids = LRANGE idx 0 (max_candidates-1)                     (bounded scan)
      best_id, best_sim = argmax over GET vec-cache:{...}:{id} -> {v,k}, cosine(vec, v)
      best_sim >= threshold:
        body = GET (pointed exact key k) -> dict | None
        body is not None -> return body                         (VECTOR HIT)
        else -> return None                                     (dangling -> MISS)
      else -> return None                                       (below threshold -> MISS)
      ANY exception -> return None                              (fail-safe)
    async store(self, *, tenant_id, model, body, response_body, ttl) -> None
      query = last-user-message content; none -> return (no-op)
      vec = await embedder(query); None -> return (no-op)
      exact_key = build_cache_key(tenant_id, body)              (the SAME key stored this request)
      id = sha256(canonical(query-vector-independent: reuse exact_key digest)) -> entry id
      SET vec-cache:{tenant}:{sha256(model)}:{id}  json({v:vec,k:exact_key})  EX=ttl
      LPUSH idx id ; LTRIM idx 0 (max_candidates-1) ; EXPIRE idx ttl
      ANY exception -> swallow (no-op)

New domain port (proxy/domain/ports.py):
  class VectorCache(Protocol):
    async def lookup(self, *, tenant_id: str, model: str, body: dict[str, Any]) -> dict[str, Any] | None: ...
    async def store(self, *, tenant_id: str, model: str, body: dict[str, Any],
                    response_body: dict[str, Any], ttl: int) -> None: ...

CompletionUseCase (additive):
  __init__(..., vector_cache: VectorCache | None = None)   # default None => feature OFF, byte-identical
  complete() Step 4.5c (inside the exact-miss/normalization-miss branch, before x_cache="miss"):
    if self._vector_cache is not None:
       vb = await self._vector_cache.lookup(tenant_id=str(authz.tenant_id), model=model_id, body=body)
       if vb is not None: x_cache="vector_hit"; metric vector_hit; evaluate_post; _fire_record_cached($0);
                          TPM post-account; return 200, vb, "vector_hit"
  complete() store (after upstream 200, NOT bypass, status==200, alongside the exact+pointer store):
    if self._vector_cache is not None:
       fire-and-forget self._vector_cache.store(tenant_id=..., model=model_id, body=body,
                                                response_body=response_body, ttl=cache_ttl_seconds)

Settings (core/config.py, additive; env prefix GATEWAY_):
  vector_cache_enabled: bool = False                         # GATEWAY_VECTOR_CACHE_ENABLED
  vector_cache_threshold: float = Field(0.95, ge=0.0, le=1.0)# GATEWAY_VECTOR_CACHE_THRESHOLD
  vector_cache_embed_model: str = ""                         # GATEWAY_VECTOR_CACHE_EMBED_MODEL (req'd when enabled)
  vector_cache_max_candidates: int = Field(100, ge=1, le=1000)# GATEWAY_VECTOR_CACHE_MAX_CANDIDATES

Metrics: cache_events_total gains label value result="vector_hit". Existing values unchanged.

Modules touched (hard boundary — BUILD adds no module outside this list):
  - proxy/infrastructure/vector_cache.py            (NEW)
  - proxy/domain/ports.py                           (ADD VectorCache Protocol)
  - proxy/application/use_cases.py                  (ctor kwarg + Step 4.5c + store hook)
  - core/config.py                                  (4 settings)
  - main.py                                         (app.state knobs)
  - proxy/api/deps.py                               (construct RedisVectorCache + embedder adapter)
  - tests/vector_cache/                             (NEW suite)

No new package. No new table/migration (global config-gated). EXPECTED_TABLES unchanged.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, v19 pacing 2026-06-15).
  Orchestrator decisions at freeze (project-lead, AUTO MODE):
  (1) NAMING: the live "semantic" layer is normalization-only (binary); this layer is the milestone's
      "true embedding-similarity" cache, named `vector_*`/`vector_hit` to avoid namespace collision. The
      milestone's "SEMANTIC cache hit" ≡ `vector_hit`. Recorded as the binding interpretation.
  (2) EMBED-ON-HOT-PATH ACCEPTED: real embeddings via the existing embedding upstream — the only framing
      that delivers a continuous threshold; mitigated by default-off + after-both-misses + bounded timeout
      + fail-safe→miss. This is the headline judgment for Tin's verify-gate review.
  (3) GLOBAL CONFIG GATE (no per-tenant DB toggle / migration) — per v19 "policy is env/config-driven;
      per-tenant admin overrides OUT". Per-tenant isolation preserved via key namespace.
  (4) PURE-PYTHON COSINE (no numpy) — allowlist/stdlib discipline; bounded candidate scan.

Least-sure flag surfaced at freeze:
  ⚠ [contract] EMBED-ON-HOT-PATH — a real embedding call on the request path (after two cheaper misses)
    is the architectural judgment most likely to draw a reviewer challenge. It is in because the milestone
    explicitly asked for "true embedding-similarity" (continuous threshold ⇒ embeddings mandatory).
    Mitigated default-off + fail-safe + bounded. Cost if wrong: flip one flag off; an in-process embedder
    is a contained follow-up.
  ⚠ [contract] FALSE-HIT AT THRESHOLD — cosine admits false hits (unlike normalization). Default 0.95 is
    conservative + operator-tunable. Cost if wrong: raise the default.
  ⚠ [test] VC1 (above-threshold hit) and VC2 (below-threshold miss) are the threshold-correctness guard;
    proven only by red→green with a fake embedder mapping known prompts to known-cosine vectors.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new vector_cache.py module)
Plan (one test per scenario; behavior not internals; self-contained — fake redis + fake embedder, no DB/live):
<test_plan>
  cosine (pure):
  - test_cosine_identical_is_one / _orthogonal_is_zero / _opposite_is_negative
  - test_cosine_total_on_length_mismatch_returns_zero / _zero_vector_returns_zero  (VC11)
  RedisVectorCache (FakeRedis + FakeEmbedder):
  - test_above_threshold_returns_pointed_body                 (VC1 unit)
  - test_below_threshold_returns_none                         (VC2 unit)
  - test_tenant_isolation_misses                              (VC3 unit)
  - test_model_isolation_misses                               (VC4 unit)
  - test_embedder_none_returns_none                           (VC5 unit)
  - test_redis_error_returns_none                             (fail-safe)
  - test_dangling_pointer_returns_none                        (VC6 unit)
  - test_no_user_message_is_noop_no_embed_call               (VC12 unit)
  - test_store_registers_and_caps_index_at_max_candidates     (VC10 unit)
  - test_store_embedder_none_is_noop                          (VC5 store side)
  CompletionUseCase integration (fakes mirroring tests/streaming_resilience/conftest):
  - test_vector_layer_off_is_byte_identical                   (VC8)
  - test_exact_miss_then_vector_hit_bills_cached_zero         (VC1 e2e: x_cache, $0, upstream once)
  - test_vector_miss_calls_upstream_and_stores                (VC2 e2e)
  - test_exact_hit_short_circuits_vector_layer                (VC7: embedder never called)
  - test_vector_hit_metric_increments                         (VC9)
</test_plan>

Tests live in: `apps/gateway/tests/vector_cache/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/vector_cache.py`
  `apps/gateway/src/gateway/proxy/domain/ports.py`
  `apps/gateway/src/gateway/proxy/application/use_cases.py`
  `apps/gateway/src/gateway/core/config.py`
  `apps/gateway/src/gateway/main.py`
  `apps/gateway/src/gateway/proxy/api/deps.py`
  `apps/gateway/tests/vector_cache/`
Strategy (ordered batches): 1. vector_cache.py (cosine + RedisVectorCache) + ports.VectorCache → unit green.
  2. config.py knobs. 3. use_cases.py ctor kwarg + Step 4.5c + store hook → integration green.
  4. main.py + deps.py wiring (embedder adapter). 5. full regression floor + typecheck + lint.
Safety rule (feature-specific): the vector lookup MUST run only AFTER exact AND normalization miss; a false
  hit is strictly worse than a miss → the threshold compare is `>=` and tenant+model are in the key namespace
  (cross-tenant/model hit = security violation, HARD-STOP). Every embed/redis op is fail-safe → MISS/no-op.
Code lives in: `apps/gateway/src/`
Constraints: do NOT change any test or the contract; allow-list packages only (stdlib cosine, no numpy); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — tests/vector_cache 27/27 (7 cosine + 11 RedisVectorCache + 5 embedding-adapter
      + 5 use-case wiring [includes default-off byte-identical]). Regression floor green: no-DB gate +
      streaming_resilience 177/177; DB cache/proxy/fallback/retry 128/128; v5 normalization semantic_cache
      15/15; response_caching+proxy+semantic_cache+vector_cache re-run after the deps refactor 67/67.
- [x] coverage did not decrease — new vector_cache.py exercised by 11 RedisVectorCache + 5 cosine tests;
      build_embedding_adapter by 5 deps tests (was the one uncovered block; now covered).
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched; no frozen suite modified
      (git diff outside tests/vector_cache/ is empty); only NEW tests added/strengthened in-suite.
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet subagent) verdict EARNED-WITH-GAPS:
      claims B (threshold/false-hit), C (tenant/model isolation), D (fail-safe), E (billing), F (default-off),
      G (secrets) all REFUTED (i.e. invariants HOLD). Two MED gaps it found were FIXED in-loop (see GATE RECORD).
- [x] concurrency / timing of the risky operation is safe — store is fire-and-forget (asyncio.ensure_future
      + add_done_callback swallows exceptions); the FIX moved the embedder onto its OWN short-lived session
      (build_embedding_adapter) so the after-response store path no longer races the request-session teardown;
      lookup awaits inline (session open). Bounded candidate scan (max_candidates) bounds lookup latency.
- [x] no exposed secrets, injection openings, or unexpected dependencies — keys are
      vec-cache:{tenant}:{sha256(model)}:{sha256(exact_key)} (no raw prompts/keys); entries store the float
      vector + the exact-key pointer; logs are generic; stdlib-only cosine (no numpy / no new package).
- [x] layering & dependencies follow CONVENTIONS.md — cosine+store in proxy/infrastructure, VectorCache
      Protocol in proxy/domain/ports, orchestration in application (Step 4.5c), composition (embedder adapter
      + DI) in proxy/api/deps; additive ctor kwarg (default None) — zero perturbation when off.
- [ ] a person reviewed and approved the change — HIGH-RISK human gate (Tin's sign-off REQUIRED) ← PENDING

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — vector_cache flows settings → main.app.state → deps.get_completion_use_case →
      CompletionUseCase ctor → Step 4.5c lookup + store hook; embedder adapter resolves the embed model via
      its own session + select_provider + post_json("/embeddings"); asserted end-to-end by the 5 wiring tests
      (hit/miss/exact-short-circuit/metric/default-off) + 5 adapter tests.
- [x] DEAD-CODE (code) — every new symbol referenced on a live path (cosine_similarity ← RedisVectorCache;
      RedisVectorCache ← deps; VectorCache ← ctor type + __all__; build_embedding_adapter ← deps); no orphan.
- [x] SEMANTIC (prose / non-code) — §3 normalization/lookup-order/threshold/pointer/cap algorithm
      byte-compared against vector_cache.py + use_cases.py Step 4.5c; false-hit invariants proven by VC1/VC2;
      isolation by VC3/VC4.

### GATE RECORD
Outcome: PASS   ← HIGH-RISK human gate SIGNED OFF by Tin (2026-06-15): "Sign off PASS + commit"
Dispositions (refute-read findings, all addressed before presenting the gate):
  1. MED — `_embed` captured the REQUEST session, but the store path runs fire-and-forget AFTER the
     response (session likely closed) → store would silently fail to populate the index in production.
     FIXED: extracted `build_embedding_adapter(session_factory, registry, embed_model)` (deps.py, module-
     level, testable); the embedder now opens its OWN short-lived session via app.state.sessionmaker. The
     store path no longer depends on the request-session lifecycle.
  2. MED — `_embed` was entirely untested (every test mocked the embedder). FIXED: added
     tests/vector_cache/test_embedding_adapter.py (5 tests: returns-vector + own-session enter/exit,
     unknown-model→None-no-provider-call, non-200→None, bad-shape(dict)→None, empty-data→None — the exact
     shape regression the refute-read flagged).
  3. LOW — `VectorCache` missing from ports `__all__` → ADDED. LOW — wiring store test now asserts
     tenant_id + body forwarded. LOW — normalization-hit short-circuit is structurally unreachable (return
     at the semantic_hit path precedes Step 4.5c) — left as documented (no test, no code path to reach).
Reviewed by: Tin (HIGH-RISK human gate, explicit sign-off "Sign off PASS + commit") · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): vector_hit rate per tenant/model; embed-call latency added on the
miss path; false-hit reports (would force a threshold raise); scan size vs max_candidates.
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency (`DDD · SDD · UDD · TDD · ADD`), status `open`.
