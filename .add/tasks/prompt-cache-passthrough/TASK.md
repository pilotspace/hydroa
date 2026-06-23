# TASK: Prompt-cache passthrough: forward OpenAI-wire cache hints to Anthropic/Gemini + surface cache tokens for billing

slug: prompt-cache-passthrough · created: 2026-06-23 · stage: production
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

All paths under `apps/gateway/`. KEY FACT: Helios's OpenAI-wire path DELIBERATELY sends NO cache_control (../helios-mono openai_completions/convert.rs:9-11) → pass-through alone is a no-op for Helios → Anthropic needs AUTO-INJECT (Tin-chosen, default-ON knob). OpenAI/Azure/OpenRouter cache automatically server-side (only need response surfacing). Gemini 2.5 implicit-caches automatically. This task changes the Anthropic + Gemini translators AND adds a cache-write billing tier (recorder + flusher + two ORM columns + one additive migration — see CACHE-WRITE TIER below; Tin approved at freeze).

Touches (files · symbols · signatures):
- `src/gateway/proxy/infrastructure/anthropic_upstream.py:125` `_openai_to_anthropic_request(payload, *, default_max_tokens)` — REQUEST: (a) PASS-THROUGH any client cache_control on OpenAI content parts → Anthropic blocks; (b) AUTO-INJECT `cache_control:{type:"ephemeral"}` on the stable prefix (system block + last tool) when the knob is on AND the client sent none. Anthropic allows ≤4 breakpoints; sub-min-length prompts simply won't cache (no error).
- `:260` `_anthropic_to_openai(body)` — RESPONSE usage (lines 292–331): read `usage.cache_creation_input_tokens` + `usage.cache_read_input_tokens`; emit `prompt_tokens = input_tokens + cache_read + cache_creation` (total) and `prompt_tokens_details.cached_tokens = cache_read`.
- `:359` `_AnthropicSSEStepper` (`message_start` usage / `_emit_terminal` ~412) — STREAM: same cache-token surfacing on the terminal usage chunk (Anthropic sends cache_* in message_start.usage).
- `src/gateway/proxy/infrastructure/gemini_upstream.py:126` `_openai_to_gemini_request` — Gemini 2.5 implicit cache = automatic; NO request change needed (note in spec). `_gemini_to_openai` + `_GeminiSSEStepper.finish` — RESPONSE: map `usageMetadata.cachedContentTokenCount` → `prompt_tokens_details.cached_tokens` (promptTokenCount already includes it).
CACHE-WRITE TIER (Tin chose to ADD it now — expands scope beyond translators):
- `src/gateway/usage/application/recorder.py` `compute_per_token_cost_usd` (@512): add `cache_creation_tokens:int` + `cache_creation_price:Decimal|None` params; flat-path guard becomes `cached==0 AND reasoning==0 AND cache_creation==0` (preserve byte-identical); tiered `fresh_in = prompt_tokens - cached_tokens - cache_creation_tokens`; add `cache_creation_tokens * (cache_creation_price or prompt_price)`. `_fetch_latest_pricing` SELECT (@588) adds `cache_creation_usd_per_token`. `record()` reads `cache_creation_tokens = _safe_tier(usage,"prompt_tokens_details","cache_creation_tokens")` (@~203) + passes it + persists it.
- `src/gateway/catalog/infrastructure/orm.py:65` add `cache_creation_usd_per_token: Mapped[Decimal|None]` (Numeric(20,10), nullable).
- `src/gateway/usage/infrastructure/orm.py:84` add `cache_creation_tokens: Mapped[int]` (Integer, server_default="0").
- `src/gateway/usage/application/flusher.py:147` add `cache_creation_tokens = int(_field("cache_creation_tokens") or "0")` + SQL insert column/param (@188/195/214).
- MIGRATION (new, under `migrations/versions/`): add both columns. Derive down_revision from `alembic heads` (current head — run it; v33's heads include a2c4e6f8b0d1 routing_config & d1e2f3a4b5c6). Mirror an existing additive-column migration.
- Translators emit `prompt_tokens_details.cache_creation_tokens` (internal convention) so the recorder reads it; Anthropic cache_creation_input_tokens → that field. (prompt_tokens still = i+cr+cc total.)

Context (working folder):
- New knob: `GATEWAY_ANTHROPIC_AUTO_CACHE` (bool, default True) on Settings. Threaded to the Anthropic adapter like other adapter knobs.
- Helios reads cache tokens from responses (convert.rs:594-633: cached_tokens/cache_write) → our response surfacing makes Helios's cost display correct.
- ⚠ NOT the proxy's own response cache (`tests/{cache_controls,semantic_cache,vector_cache,response_caching}` — unrelated; do not touch).
- Tests use the v34 harness (SEAM A `_anthropic_to_openai`/`_openai_to_anthropic_request` + SEAM C real adapter via MockTransport with native cache_* usage fields).

Honors (patterns / conventions):
- Translate OpenAI-wire → native; byte-identical when caching not engaged (knob off AND no client cache_control → zero new keys).
- Estimate honesty / no silent $0: cached_tokens surfaced from AUTHORITATIVE native fields (no estimate). Cache-write under-bill is documented, not hidden (seed a SPEC delta for a cache_creation price tier).
- Default-ON knob is design-for-failure-safe: a model that can't cache simply ignores the breakpoint; malformed client cache_control → ignore + WARN, never crash (cf. tool_translation fail-safe).

Anchors the contract cites: `_openai_to_anthropic_request` (:125) · `_anthropic_to_openai` (:260) · `_AnthropicSSEStepper` · `_openai_to_gemini_request` (:126) · `_gemini_to_openai` · `_GeminiSSEStepper.finish` · Anthropic `cache_control:{type:"ephemeral"}` · `usage.cache_creation_input_tokens` / `usage.cache_read_input_tokens` · Gemini `usageMetadata.cachedContentTokenCount` · OpenAI `prompt_tokens_details.cached_tokens` · `cached_input_usd_per_token` (catalog/orm.py:65) · Settings knob `GATEWAY_ANTHROPIC_AUTO_CACHE`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: prompt-cache passthrough — make Anthropic prompt caching actually engage for Helios (auto-inject cache_control on the stable prefix), forward client-supplied cache_control when present, and surface Anthropic/Gemini cache tokens into OpenAI usage so the existing recorder bills cache reads at the cached rate.

Framings weighed: passthrough + auto-inject (chosen, Tin) · passthrough-only (rejected — no-op for Helios which sends no cache_control) · auto-inject-only (rejected — misses OpenRouter-style clients that DO send hints).

Must:
<must>
  - ANTHROPIC request PASS-THROUGH: if the OpenAI-wire body already carries `cache_control` markers on message/content parts, forward them to the Anthropic native blocks and DO NOT auto-inject (respect client intent).
  - ANTHROPIC request AUTO-INJECT: when `GATEWAY_ANTHROPIC_AUTO_CACHE` is True (default) AND the client supplied NO cache_control, add `cache_control:{type:"ephemeral"}` on the stable prefix — the system block and the last tool definition (≤4 Anthropic breakpoints). A prompt below the model's min cacheable length simply won't cache (no error).
  - ANTHROPIC response (non-stream + stream): read `usage.cache_read_input_tokens` + `usage.cache_creation_input_tokens`; emit `prompt_tokens = input_tokens + cache_read + cache_creation`, `prompt_tokens_details.cached_tokens = cache_read`, AND `prompt_tokens_details.cache_creation_tokens = cache_creation`.
  - CACHE-WRITE BILLING (Tin chose to add the tier now): the recorder bills `cache_creation_tokens` at a NEW `cache_creation_usd_per_token` pricing tier (Anthropic's ~1.25× write premium); `fresh_in = prompt_tokens - cached_tokens - cache_creation_tokens` billed at prompt rate. When the price column is NULL, fall back to the prompt rate (today's behavior — no regression). New pricing column + new usage column persisted (one additive migration).
  - GEMINI response (non-stream + stream): map `usageMetadata.cachedContentTokenCount` → `prompt_tokens_details.cached_tokens` (promptTokenCount already includes it). Gemini request: NO change (2.5 implicit caching is automatic).
  - PASSTHROUGH providers (OpenAI/Azure/OpenRouter): unchanged — they cache server-side and already surface cached_tokens.
  - BYTE-IDENTICAL: knob OFF AND no client cache_control → zero new request keys; a response with no cache fields (cached==0 AND cache_creation==0 AND reasoning==0) → recorder flat-path + usage identical to today.
</must>
Reject:
<reject>
  - client `cache_control` present but malformed (not the documented `{type:"ephemeral"...}` shape) -> "cache_control_malformed" — fail-SAFE: drop that marker, continue (auto-inject still suppressed since the client intended to control it), log WARN (never 400)
  - auto-inject requested but there is no system block AND no tools (nothing stable to cache) -> "cache_nothing_to_anchor" — no-op (no breakpoint added), DEBUG log; not an error
</reject>
After:
<after>
  - A Helios request to an Anthropic model auto-caches its stable prefix; subsequent turns report cache_read tokens billed at the cached rate; Gemini cache reads are surfaced + billed; non-cache paths byte-identical; OpenAI/Azure/OpenRouter untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] cache-WRITE tier price SOURCE: the new `cache_creation_usd_per_token` column is populated by the catalog sync from the provider price feed; until a feed supplies it the column is NULL and we fall back to the prompt rate (so existing rows/tests are unaffected). Lowest confidence because no catalog-sync change ships in THIS task — the column exists + is billed when present, but stays NULL (= today's 1× behavior) until a follow-up populates it. If the feed never carries it: keep a manual price or a fixed 1.25× multiplier helper — isolated to `compute_per_token_cost_usd`.
  ⚠ [contract] AUTO-INJECT breakpoint placement = system block + last tool. If a deployment's stable prefix is actually the first large user turn (not system/tools), those breakpoints cache the wrong thing (wasted cache-write, no hit). If wrong: make placement configurable or add a last-N-messages breakpoint — isolated to the inject helper.
  - [ ] [contract] OpenAI-wire client cache_control shape to pass through = OpenRouter convention (`cache_control` on a content-array part). Grounded as the de-facto standard; Helios sends none today so this path is for OpenRouter-style clients.
  - [ ] knob default-ON is desired (Tin chose default-ON) — a tenant wanting to disable sets GATEWAY_ANTHROPIC_AUTO_CACHE=false.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Anthropic auto-inject caches the stable prefix
  Given GATEWAY_ANTHROPIC_AUTO_CACHE=True, a body with a system prompt + tools and NO client cache_control
  When _openai_to_anthropic_request translates it
  Then the system block (and the last tool) carry cache_control:{type:"ephemeral"}

Scenario: client cache_control is passed through, auto-inject suppressed
  Given a body whose content already carries a cache_control marker
  When translated (knob on)
  Then the client's cache_control is forwarded to the Anthropic block
  And no ADDITIONAL auto breakpoint is injected (client intent respected)

Scenario: knob OFF is byte-identical
  Given GATEWAY_ANTHROPIC_AUTO_CACHE=False and no client cache_control
  When translated
  Then the result has no cache_control anywhere (byte-identical to today)

Scenario: Anthropic response surfaces cache read tokens
  Given an Anthropic response usage with input_tokens=100, cache_read_input_tokens=900, cache_creation_input_tokens=50
  When _anthropic_to_openai maps it
  Then prompt_tokens==1050 and prompt_tokens_details.cached_tokens==900

Scenario: Anthropic streaming surfaces cache tokens (real adapter)
  Given a real AnthropicCompletionUpstream streaming message_start.usage with cache_read_input_tokens=900
  When consumed via MockTransport (SEAM C)
  Then the terminal usage chunk carries prompt_tokens_details.cached_tokens==900

Scenario: Gemini response surfaces cached content tokens
  Given a Gemini response usageMetadata with promptTokenCount=1000, cachedContentTokenCount=800
  When _gemini_to_openai maps it
  Then prompt_tokens==1000 and prompt_tokens_details.cached_tokens==800

Scenario: cache reads bill at the cached rate via the recorder
  Given a SEAM-B request whose response usage has cached_tokens
  When recorded
  Then the usage row bills fresh_in at prompt rate + cached_tokens at cached_input rate

Scenario: cache WRITES bill at the cache-creation rate
  Given pricing with cache_creation_usd_per_token set AND a response usage with cache_creation_tokens=50
  When compute_per_token_cost_usd runs
  Then cost == fresh_in*prompt + cached*cached_input + 50*cache_creation_price
  And fresh_in == prompt_tokens - cached_tokens - cache_creation_tokens

Scenario: cache-write tier is byte-identical when price is NULL
  Given cache_creation_usd_per_token is NULL and cache_creation_tokens=50
  When compute_per_token_cost_usd runs
  Then the 50 cache_creation tokens are billed at the prompt rate (no regression vs today)

Scenario: all tiers zero takes the flat path
  Given cached==0 AND reasoning==0 AND cache_creation==0
  When compute_per_token_cost_usd runs
  Then it returns the flat byte-identical cost (unchanged)

Scenario: REJECT a malformed client cache_control
  Given a content part with cache_control of the wrong shape
  When translated
  Then that marker is dropped + WARN "cache_control_malformed"
  And every other translated field is unchanged from the no-cache baseline

Scenario: REJECT auto-inject with nothing to anchor
  Given knob on, a body with no system block and no tools
  When translated
  Then no cache_control is added (no-op, DEBUG "cache_nothing_to_anchor")
  And the request is otherwise unchanged from the no-cache baseline
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP surface — internal translator changes + one Settings knob.

NEW KNOB: Settings.anthropic_auto_cache: bool = Field(default=True)  # env GATEWAY_ANTHROPIC_AUTO_CACHE
  threaded into AnthropicCompletionUpstream(__init__ kwarg auto_cache: bool = True) at main.py:557.

ANTHROPIC request (_openai_to_anthropic_request):
  pass-through: client cache_control on OpenAI content parts → Anthropic block cache_control (forward verbatim shape {type:"ephemeral"[, ttl]})
  auto-inject (knob on AND client sent none): set cache_control:{type:"ephemeral"} on the system block + the last tool (≤4 breakpoints)

ANTHROPIC response (_anthropic_to_openai + _AnthropicSSEStepper):
  read usage.cache_read_input_tokens (cr), usage.cache_creation_input_tokens (cc), input_tokens (i)
  emit usage.prompt_tokens = i + cr + cc
       usage.prompt_tokens_details.cached_tokens = cr
       usage.prompt_tokens_details.cache_creation_tokens = cc

GEMINI request: unchanged (implicit caching). 
GEMINI response (_gemini_to_openai + _GeminiSSEStepper.finish):
  emit usage.prompt_tokens_details.cached_tokens = usageMetadata.cachedContentTokenCount   # promptTokenCount already total
  (Gemini exposes no cache-write count → cache_creation_tokens omitted/0 for Gemini)

BILLING (recorder.py — cache-write tier ADDED):
  compute_per_token_cost_usd(... , cached_tokens, reasoning_tokens, cache_creation_tokens,
                             prompt_price, cached_price, reasoning_price, cache_creation_price):
    flat path (byte-identical) iff cached==0 AND reasoning==0 AND cache_creation==0
    else fresh_in = prompt_tokens - cached_tokens - cache_creation_tokens
         cost = fresh_in*prompt_price + cached*cached_price + reasoning*reasoning_price
              + cache_creation * (cache_creation_price or prompt_price)   # NULL price -> prompt rate (no regression)
  record(): cache_creation_tokens = _safe_tier(usage,"prompt_tokens_details","cache_creation_tokens"); pass + persist it
  _fetch_latest_pricing SELECT adds cache_creation_usd_per_token

Fail-safe (no client error):
  malformed client cache_control -> drop marker, WARN "cache_control_malformed"
  auto-inject with no system+no tools -> no-op, DEBUG "cache_nothing_to_anchor"

Schema (ONE additive migration, down_revision = a2c4e6f8b0d1 current head):
  ALTER pricing_snapshots ADD cache_creation_usd_per_token NUMERIC(20,10) NULL   (catalog/orm.py)
  ALTER usage_records     ADD cache_creation_tokens INTEGER NOT NULL DEFAULT 0   (usage/orm.py); flusher persists it
Constants/helpers live in anthropic_upstream.py / gemini_upstream.py; knob in core/config.py.
```

Least-sure flag surfaced at freeze: [contract] the cache-write tier ships the COLUMN + billing path, but `cache_creation_usd_per_token` stays NULL (→ prompt-rate fallback = today's behavior) until a follow-up catalog-sync populates the feed value — so no live billing change until then. Runner-up [contract]: AUTO-INJECT breakpoint placement = system block + last tool; wrong if a deployment's stable prefix is the first big user turn (isolated to the inject helper).

Status: FROZEN @ v1 — approved by Tin (2026-06-23)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new branches.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_anthropic_auto_inject_caches_prefix: _openai_to_anthropic_request(system+tools, no cc, auto_cache=True) → system block + last tool have cache_control ephemeral
  - test_client_cache_control_passthrough_suppresses_autoinject: body w/ client cache_control → forwarded AND no extra auto breakpoint
  - test_knob_off_byte_identical: auto_cache=False, no cc → no cache_control anywhere (== baseline)
  - test_anthropic_response_surfaces_cache_read: _anthropic_to_openai(usage i=100,cr=900,cc=50) → prompt_tokens==1050, cached_tokens==900, cache_creation_tokens==50
  - test_anthropic_stream_surfaces_cache_read: SEAM C real adapter, message_start.usage cr=900 → terminal cached_tokens==900
  - test_gemini_response_surfaces_cached_content: _gemini_to_openai(promptTokenCount=1000,cachedContentTokenCount=800) → prompt_tokens==1000, cached_tokens==800
  - test_cache_billing_via_recorder: SEAM B request w/ cached_tokens → recorded row bills cached at cached_input rate (recorded_usage helper)
  - test_cache_write_billed_at_creation_rate: compute_per_token_cost_usd with cache_creation_tokens=50 + cache_creation_price set → cost includes 50*creation_price; fresh_in == prompt - cached - cache_creation
  - test_cache_write_null_price_falls_back_to_prompt_rate: cache_creation_price=None, cache_creation_tokens=50 → 50 billed at prompt rate (no regression)
  - test_all_tiers_zero_flat_path: cached==reasoning==cache_creation==0 → flat byte-identical cost
  - test_recorder_persists_cache_creation_tokens: record() reads prompt_tokens_details.cache_creation_tokens → flusher writes usage_records.cache_creation_tokens
  - test_reject_malformed_cache_control: wrong-shape client cc → dropped, WARN, other fields == baseline
  - test_reject_autoinject_nothing_to_anchor: knob on, no system+no tools → no cache_control added, request == baseline
</test_plan>

Tests live in: `apps/gateway/tests/prompt_cache_passthrough/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/src/gateway/usage/infrastructure/orm.py` `apps/gateway/src/gateway/catalog/infrastructure/orm.py` `apps/gateway/src/gateway/migrations/versions/`
  — Anthropic request pass-through+auto-inject + response cache-token surfacing (cached + cache_creation); Gemini response cache-token surfacing; GATEWAY_ANTHROPIC_AUTO_CACHE knob (config.py) threaded into the adapter (main.py:557); recorder cache-write tier + flusher persist + two ORM columns + ONE additive migration (down_revision a2c4e6f8b0d1).
Strategy (ordered batches): 1. config knob + thread to adapter ctor. 2. Anthropic request: pass-through detect + auto-inject helper (system+last tool) + fail-safe. 3. Anthropic response + stepper: cache_read/cache_creation → prompt_tokens + cached_tokens + cache_creation_tokens. 4. Gemini response + stepper: cachedContentTokenCount → cached_tokens. 5. migration + ORM columns (pricing_snapshots.cache_creation_usd_per_token, usage_records.cache_creation_tokens). 6. recorder compute_per_token_cost_usd cache-write tier + read prompt_tokens_details.cache_creation_tokens + pass/persist; flusher _field + SQL. 7. green the §4 suite.
Safety rule (feature-specific): byte-identical when knob off + no client cc AND all tiers zero (flat path); malformed cc never raises (drop+WARN); cached/cache_creation only from authoritative native fields (no estimate); prompt_tokens must equal total input (i+cr+cc) so fresh_in is correct; NULL cache_creation price → prompt-rate fallback (no billing regression); migration additive + reversible.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/` (+ core/config.py, main.py wiring)
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

- [x] all tests pass — full gate `uv run pytest -m 'not e2e' --cov-fail-under=80` → 1466 passed, 19 deselected
- [x] coverage did not decrease — 87.36% (≥80% floor)
- [x] no test or contract was altered during build — only NEW tests added; frozen §3 untouched
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) = EARNED-GREEN @ 0.91, zero code defects; its 3 test-assertion gaps closed by STRENGTHENING (PC12 baseline-equality, PC5 stream cache_creation+prompt_tokens, NEW PC14 Gemini-stream)
- [x] concurrency / timing — translators are pure/per-request; no shared mutable state; SSE steppers per-stream (unchanged pattern)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; no secrets; cache_control validated before forward (malformed dropped)
- [x] layering & dependencies follow CONVENTIONS.md — translator→adapter→recorder layering preserved; additive migration mirrors f3c8d1a6b9e4
- [x] a person reviewed and approved the change — Tin approved the frozen contract (2026-06-23); auto-gate on complete evidence under autonomy:auto (no security/concurrency residue)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] Anthropic auto-inject puts cache_control:{type:"ephemeral"} on BOTH system block AND last tool when knob on + no client cc — PC1 asserts both
- [x] knob OFF + no client cc → zero new request keys (byte-identical) — PC3 asserts no "cache_control" anywhere
- [x] Anthropic response: prompt_tokens=i+cr+cc, cached_tokens=cr, cache_creation_tokens=cc (non-stream PC4 1050/900/50; stream PC5 1050/900/50)
- [x] Gemini: cachedContentTokenCount→cached_tokens, prompt_tokens unchanged, NO bogus cache_creation (PC6 non-stream, PC14 stream)
- [x] cache-write billed at creation rate; NULL price→prompt rate; all-tiers-zero→flat path — PC8/PC9/PC7 hand-verified Decimals (0.009125 / 0.013 / 0.009)
- [x] cache_creation_tokens persisted via recorder→flusher; migration c3e5b7a9f1d2 down_revision a2c4e6f8b0d1, additive+reversible — PC11 asserts persisted "50"

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — knob `anthropic_auto_cache` → AnthropicCompletionUpstream ctor → complete()/stream() (main.py passes settings value, default True confirmed in prod path); `_fetch_latest_pricing` 8-tuple unpacked at single call site; cache_creation_tokens flows recorder→flusher→SQL→ORM column; refute-read traced each
- [x] DEAD-CODE (code) — new helpers `_has_client_cache_control`/`_auto_inject_cache_control`/`_is_valid_cache_control` all referenced; no orphans
- [x] SEMANTIC (prose / non-code) — frozen §3 contract re-read against build: prompt_tokens=i+cr+cc, cached=cr, cache_creation=cc, NULL-price→prompt fallback, Gemini no cache_creation — all honored

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract approval 2026-06-23) + adversarial refute-read (sonnet, EARNED-GREEN 0.91, defects→strengthened) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): cache_control_malformed WARN rate · cache_nothing_to_anchor DEBUG rate · cache-hit ratio (cached_tokens/prompt_tokens) on Anthropic · cache_creation_usd_per_token NULL-fallback rate (= unpriced cache-writes)

### Spec delta
- [SPEC · seeded] populate `cache_creation_usd_per_token` from the catalog price feed — column + billing path ship this task but stay NULL → prompt-rate fallback until a catalog-sync writes Anthropic's ~1.25× write premium (evidence: §3 least-sure flag; v33 reconciliation drift will show the residual under-bill while NULL)
- [SPEC · open] make Anthropic auto-inject breakpoint placement configurable (today: system block + last tool) — wrong if a deployment's stable prefix is the first large user turn (evidence: §1 runner-up assumption)
- [SPEC · open] no SEAM-B/full-HTTP test exercises auto_cache=True through complete()/stream() (helper covered directly + prod threading verified by inspection) — add when disconnect/load tasks build HTTP fixtures (evidence: refute-read item 5)

### Competency deltas
- [TDD · folded] adversarial refute-read found 3 test-assertion gaps on an otherwise-correct build (PC12 baseline-equality, PC5 stream cache_creation, missing Gemini-stream) — closed by STRENGTHENING not weakening; refute-read pays off even at 0.91 (evidence: gaps were real coverage holes for contract-cited behavior) [folded foundation-version 31]
- [ADD · folded] expanding a frozen-DRAFT bundle at the freeze decision (Tin chose to add the cache-write tier) cleanly widened scope from translator-only → +migration/recorder/flusher/2 ORMs without re-running earlier phases — the freeze IS the right place to absorb a scope decision (evidence: one approval, one coherent bundle) [folded foundation-version 31]
