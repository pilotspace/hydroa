# TASK: B6: cache-hit alias bills served candidate, not alias

slug: cache-alias-billing · created: 2026-07-02 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `proxy/application/use_cases.py:CompletionUseCase.complete` — the ONLY file this fix touches. The non-stream cache path.
  - REACHABILITY (CONFIRMED by code read, not assumed): `build_cache_key` (response_cache.py:39) includes `model` in `_CACHE_KEY_FIELDS`. `FallbackModelRouter.complete` (fallback_router.py:231) routes an alias via a NEW dict `rewritten = {**payload, "model": candidate}` (L~314) and NEVER mutates the caller's `body`. So in `complete()`, `body["model"]` stays the ALIAS through the cache WRITE. Write-key == read-key (both alias) → a repeat identical alias request HITS the exact cache and fires `_fire_record_cached(model=model_id=ALIAS)` → alias has no pricing_snapshots row → $0. LEAK IS REACHABLE.
  - Cache topology (why one fix covers 3 hit paths): semantic (`use_cases.py:1168` `cache.get(exact_key_str)`) and vector (`vector_cache.py:129,155` deref `build_cache_key`) BOTH return the SAME body stored at the exact-cache key. So a single served-id stamp on the exact-cache value is visible on ALL THREE hit paths.
  - WRITE sites to stamp (enumerated — all store the response body): `use_cases.py:1373` `_fire_cache_set(cache, ck, response_body, ttl)` (main miss-store) · `:1408` `_cache.set(_pointed, _body, ttl)` (bypass+semantic refresh) · `:1411` `_cache.set(_own_ck, _body, ttl)` (bypass cold/dangling). Vector store (`:1427`) persists only an embedding+pointer (no body) → no stamp needed.
  - READ/hit sites to bill-on-served (enumerated — all charged status=200, all currently bill `model=model_id`=alias): exact `:1143` · semantic `:1199` · vector `:1257`.
  - Served-id source: `served_model_id` (use_cases.py:1300/1310) — the router's 3rd tuple element; already the billing key on the correct MISS path (`:1463`, frozen F7 invariant).
Context (working folder): fake-based unit tests only — NO DB. Mirrors `tests/stream_alias_billing/` (B1): FakeResponseCache + MarkerSpyRecorder spy + FallbackModelRouter with fakes. `authz.cache_enabled` must be true.
Honors (patterns / conventions): F7 "bill the served catalog candidate, never the alias" (this extends F7 from complete()'s live-route path to its cache-hit path) · fire-and-forget billing (`_fire_record_cached`) · client body must NOT leak internal fields.
Anchors the contract cites: `CompletionUseCase.complete`, `_fire_record_cached`, `_fire_cache_set`, `build_cache_key`, `served_model_id`, the reserved stamp key.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Cache-hit billing keys on the served catalog candidate, never the alias (extends F7 to complete()'s cache path).
Framings weighed: PERSIST served id at write, bill on it at read (chosen — the hit path has no live route to capture from) · bill on `cached_body["model"]` at read (rejected — the provider-returned model may differ from the catalog id for OpenRouter ":free" variants → may still lack a pricing snapshot) · resolve the alias→candidate at read time (rejected — 3/4 routing strategies are non-deterministic; the served id must reflect the candidate that actually PRODUCED the cached body).
Must:
<must>
  - On a status=200 cache MISS store, the value written to the exact-cache key carries the SERVED catalog candidate id under a reserved stamp key `__hydroa_served_model__` (a shallow copy `{**response_body, STAMP: served_model_id}`; the body returned to the client is NOT mutated). Applied at EVERY body-write site: main store (:1373), bypass+semantic refresh (:1408), bypass cold/dangling (:1411).
  - On a status=200 cache HIT (exact :1143 | semantic :1199 | vector :1257), the charged `_fire_record_cached` bills `model = served candidate` resolved as `cached.pop(STAMP) or cached.get("model") or model_id`, reading+popping the stamp from the fetched dict BEFORE any post-call guardrail masking runs.
  - Miss-path billing (F7, :1463), x_cache values, TPM accounting, and the HTTP request/response shape are UNCHANGED.
</must>
Reject:
<reject>
  - (no new HTTP rejection — this is a billing-attribution correction, not a new failure mode)
  - INVARIANT (fail case if violated): the response body RETURNED to the client MUST NOT contain `__hydroa_served_model__` — it is popped on every hit path and never mutated onto the client's miss-response.
</reject>
After:
<after>
  - A second identical request through a model-group alias that HITS any cache layer records usage keyed on the candidate that produced the cached body (which HAS a pricing snapshot) → non-zero cost. The alias is never the billed model on any charged cache record.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] The stamp must be read+popped IMMEDIATELY after `cache.get`/`lookup`, BEFORE `evaluate_post` masking (:1128 etc.) — lowest confidence because a masking transform that returns a fresh dict would drop an unknown key; if wrong: the masking path loses the stamp → falls back to `cached["model"]` (mis-bill for OpenRouter variants, but NEVER $0-on-alias). Mitigation pinned in the Must.
  - [ ] Legacy entries (stored pre-fix, still within TTL) lack the stamp → fall back to `cached.get("model")` (the provider model, not the alias) → no $0-on-alias, bounded transient residue until TTL cycles. Confirmed acceptable.
  - [ ] Storing one extra top-level key in the cached dict breaks no internal consumer — the cached body is only ever returned to the client (after pop) and never schema-validated internally. Confirmed by grounding.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Exact cache HIT through an alias bills the served candidate
  Given an alias "fast" routing to candidate CAND_A, cache enabled
  And a prior identical request warmed the exact cache (stored CAND_A's body + stamp)
  When an identical alias request is made and hits the exact cache
  Then the charged usage record keys on CAND_A, not the alias "fast"
  And the response body returned to the client contains no "__hydroa_served_model__" key

Scenario: Semantic cache HIT through an alias bills the served candidate
  Given semantic cache enabled and a warmed semantic pointer → exact entry for CAND_A
  When a normalization-equivalent alias request hits the semantic layer
  Then the charged usage record keys on CAND_A, not the alias
  And the returned body contains no stamp key

Scenario: Vector cache HIT through an alias bills the served candidate
  Given the vector cache is wired and holds a near-duplicate pointing at CAND_A's exact entry
  When a near-duplicate alias request hits the vector layer
  Then the charged usage record keys on CAND_A, not the alias
  And the returned body contains no stamp key

Scenario: Cache MISS is unchanged and leaks no stamp
  Given cache enabled and a cold cache
  When an alias request misses and routes to CAND_A
  Then the charged record keys on CAND_A (F7 unchanged)
  And the body returned to the client contains no "__hydroa_served_model__" key
  And the value stored in the cache DOES carry the stamp = CAND_A

Scenario: Legacy cached entry without a stamp falls back safely
  Given a warmed exact entry whose body has no stamp (pre-fix) but has model="CAND_A"
  When an identical alias request hits it
  Then the charged record keys on CAND_A (from cached["model"]), never the alias
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
CompletionUseCase.complete(...)  — internal billing-attribution invariant (NO HTTP shape change)

Reserved stamp key:  STAMP = "__hydroa_served_model__"

WRITE (status==200 store; all body-write sites — enumerated):
  stored_value = {**response_body, STAMP: served_model_id}
  sites: use_cases.py :1373 (_fire_cache_set) · :1408 (refresh _pointed) · :1411 (cold _own_ck)
  response_body returned to the client is left UNMODIFIED.

READ (status==200 hit; all three layers — enumerated):
  served = cached.pop(STAMP, None) or cached.get("model") or model_id      # pop BEFORE evaluate_post
  _fire_record_cached(usage_recorder, ..., model=served, usage=cached_usage, team_id=...)
  sites: exact :1143 · semantic :1199 · vector :1257

INVARIANT: body returned to client never contains STAMP (popped on every hit path).
UNCHANGED: HTTP req/resp shape · x_cache values · TPM accounting · miss-path F7 billing (:1463).
Schema: none — no DB/table change. usage_records rows now carry a catalog model id (→ pricing snapshot) instead of an alias.
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-02). Changing this contract now = change request back to SPECIFY.
Bundle lowest-confidence flags (surfaced at freeze — Tin froze as-is, stamp approach):
  ⚠ [contract] pop the stamp BEFORE post-call guardrail masking (evaluate_post) — else a masking
     transform dropping unknown keys loses it → falls back to cached["model"] (mis-bill OpenRouter
     variants, never $0-on-alias). Pinned in §1 Must; tested by S1.
  · [test] the legacy no-stamp fallback (S5) must bill cached["model"], never the alias.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: the 3 hit sites + the write-stamp path (mirror of B1's stream_alias_billing rigor).
Plan (one test per scenario, asserting behavior not internals — fakes only, NO DB):
<test_plan>
  - test_exact_hit_bills_served_not_alias: arrange warmed exact cache for alias→CAND_A / act repeat alias request → exact hit / assert billed[-1]["model"]==CAND_A, != alias AND returned body has no STAMP key
  - test_semantic_hit_bills_served_not_alias: arrange semantic pointer→CAND_A exact entry / act equivalent request → semantic hit / assert billed on CAND_A, != alias AND no STAMP in body
  - test_vector_hit_bills_served_not_alias: arrange vector index → CAND_A exact entry / act near-dup → vector hit / assert billed on CAND_A, != alias AND no STAMP in body
  - test_miss_unchanged_and_no_stamp_leak: arrange cold cache / act alias miss→CAND_A / assert billed CAND_A (F7) AND client body has no STAMP AND the FakeResponseCache stored value DOES carry STAMP=CAND_A
  - test_legacy_entry_without_stamp_falls_back_to_cached_model: arrange warmed entry body={"model":"CAND_A",...} no STAMP / act hit / assert billed CAND_A, never alias
</test_plan>
Shared fakes (reuse/mirror tests/stream_alias_billing/conftest.py): MarkerSpyRecorder (billed_records filter status==200), FallbackModelRouter with fakes, and a FakeResponseCache (get/set + get_pointer/set_pointer) + minimal FakeVectorCache (lookup/store) — assert both the billed model AND stamp-absence in the returned body.

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py` · `apps/gateway/tests/cache_alias_billing/`
Strategy (ordered batches): 1. add module-level STAMP constant + a tiny `_stamp_served(body, served)` shallow-copy helper + `_read_served(cached, model_id)` (pop→cached["model"]→model_id). 2. wrap the 3 write sites (:1373/:1408/:1411) to store the stamped value. 3. at the 3 hit sites (:1143/:1199/:1257) read+pop served BEFORE evaluate_post and bill on it.
Known-problem fixes: masking-drops-stamp → read+pop the served id immediately after cache.get/lookup, before evaluate_post · stamp-leaks-to-client → store a shallow COPY, never mutate response_body; pop on every hit path · under-enumeration (the B1 trap) → all 3 write sites AND all 3 read sites listed in §0, each gets a test.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
