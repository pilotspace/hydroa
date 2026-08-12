# TASK: Bill cached-input and reasoning tokens at their own rates

slug: tiered-token-billing · created: 2026-06-17 · stage: production
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
- `apps/gateway/src/gateway/usage/application/recorder.py:_record_internal` — the per_token billing path (lines 170-178): `cost = (prompt_tokens·prompt_price + completion_tokens·completion_price)·(1+markup/100)`, ALL Decimal. Reads only `usage.get("prompt_tokens")` / `usage.get("completion_tokens")` (173-174) — IGNORES tier details. This is where tiered math lands.
- `apps/gateway/src/gateway/usage/application/recorder.py:_fetch_latest_pricing` (289-323) — `SELECT id, prompt_usd_per_token, completion_usd_per_token, pricing_unit, unit_usd_per_unit FROM pricing_snapshots … ORDER BY captured_at DESC LIMIT 1`; returns a 5-tuple. Must extend to also select the two new price columns.
- `apps/gateway/src/gateway/usage/application/recorder.py:record` (67-117) — public port; builds the Redis-stream event dict (~250-261: `cost_usd`, `prompt_tokens`, `completion_tokens`, `pricing_unit`, `quantity`). New tier-token fields are added here, mirroring `quantity`.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py:PricingSnapshotRow` (39-61) — append-only pricing ledger: `prompt_usd_per_token`/`completion_usd_per_token` Numeric(20,10); additive `pricing_unit`/`unit_usd_per_unit`. Add `cached_input_usd_per_token` + `reasoning_usd_per_token` Numeric(20,10) NULL here (mirror `unit_usd_per_unit`).
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` (36-71) — append-only ledger; `prompt_tokens`/`completion_tokens` Integer; additive `pricing_unit`/`quantity`. Add `cached_tokens` + `reasoning_tokens` Integer NOT NULL DEFAULT 0 (mirror the int columns) so tiers are visible on the row.
- `apps/gateway/src/gateway/usage/application/flusher.py:_handle_entry` (~100-191) — reads stream event fields (`_field(...)`), INSERTs the ledger row (162-188). Must read + forward the two new tier-token fields (backward-compat: missing → 0, mirroring `pricing_unit`/`quantity`).
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_fire_record_with_raw` (295) + call site ~1245 — passes the upstream response's raw `usage` dict THROUGH to `recorder.record(usage=…)` verbatim; tier details (`prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`) already arrive here for providers that return them — NO new upstream parsing needed for non-stream chat.

Context (working folder):
- Migration head = `e2b7f4c9a1d8` (tenants_updated_at) — the new additive migration revises it. Pattern to mirror: `migrations/versions/c9e2f4a8b1d6_pricing_units_schema.py` (ADD COLUMN … NOT NULL DEFAULT, instant on PG11+, belt-and-suspenders UPDATE, downgrade drops in reverse).
- Seed `catalog/infrastructure/openai_seed.py` + `openrouter_source.py` set only prompt/completion price today — they do NOT source cached/reasoning prices; new price columns are NULL by default (sourcing them is OUT of scope; the fallback covers it). Tests will seed a snapshot with an explicit cached price.
- Tests dir (declare): `apps/gateway/tests/tiered_token_billing/`; sibling exemplars: `tests/pricing_units/`, `tests/usage/`.

Honors (patterns / conventions):
- PROJECT.md v12 fold: "exact token billing is the domain rule; an estimate is a documented last-resort fallback." · v6/v9/v11 BYTE-IDENTICAL invariant: usage WITHOUT tier details + WITHOUT tier prices bills exactly as today (same operand order, no new intermediate rounding).
- MILESTONE.md shared decisions: byte-identical floor; billing keys on the SERVED model id; accuracy is never an availability gate.
- ALL money math stays `Decimal` (recorder convention); append-only ledger (no UPDATE/DELETE); additive migration only.

Anchors the contract cites (§3 names only these):
- `pricing_snapshots.cached_input_usd_per_token`, `pricing_snapshots.reasoning_usd_per_token` (NUMERIC(20,10) NULL)
- `usage_records.cached_tokens`, `usage_records.reasoning_tokens` (INT NOT NULL DEFAULT 0)
- `recorder._record_internal` per_token branch · `recorder._fetch_latest_pricing` 7-tuple · `recorder.record` event dict · `flusher._handle_entry` INSERT
- migration revising `e2b7f4c9a1d8`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tier token billing — price cached-input and reasoning tokens distinctly from fresh input/output.
Framings weighed: priced snapshot columns + base-price fallback (chosen) · hard-coded discount ratio of base price (rejected — ratios are provider/model-specific, belong in the snapshot) · split reasoning only, bill cached at full price (rejected — misses the biggest accuracy win, prompt caching).
Must:
<must>
  - Read tier counts from the usage dict the recorder already receives: `usage["prompt_tokens_details"]["cached_tokens"]` and `usage["completion_tokens_details"]["reasoning_tokens"]` (OpenAI-canonical shape). Both legs apply to the per_token path only; non-token pricing_units (per_image/per_second/per_character) are untouched.
  - When `cached_tokens` (int ≥ 0) is present: bill `cached_tokens × cached_input_usd_per_token` + `(prompt_tokens − cached_tokens) × prompt_usd_per_token`; markup applies to the total. When `cached_input_usd_per_token` is NULL/absent, cached tokens fall back to `prompt_usd_per_token` (→ reduces to today's flat cost).
  - When `reasoning_tokens` (int ≥ 0) is present: bill `reasoning_tokens × reasoning_usd_per_token` + `(completion_tokens − reasoning_tokens) × completion_usd_per_token`. When `reasoning_usd_per_token` is NULL/absent, reasoning tokens fall back to `completion_usd_per_token`.
  - BYTE-IDENTICAL floor: a usage payload with no `*_details` (or details absent/zero) AND base-only prices computes the exact same `cost_usd` as today — same Decimal operand order, no new intermediate rounding.
  - Persist `cached_tokens` and `reasoning_tokens` on the `usage_records` row (INT, default 0) so the tiers are queryable on the ledger; they flow recorder event-dict → flusher → INSERT, backward-compatible (missing field → 0).
  - All money math stays `Decimal`; `_fetch_latest_pricing` returns the two new prices; the snapshot id, spend counters (Redis INCRBYFLOAT on the tiered total), and append-only ledger semantics are preserved.
</must>
Reject:   <!-- internal fire-and-forget billing path: the recorder MUST NOT raise; "reject" = a named defensive clamp + log marker, not an HTTP 4xx -->
<reject>
  - cached_tokens negative OR > prompt_tokens (e.g. an exclusive-reporting provider) -> clamp fresh input to `max(0, prompt − cached)` -> log "tier_token_clamped" (never negative cost).
  - reasoning_tokens negative OR > completion_tokens -> clamp non-reasoning output to `max(0, completion − reasoning)` -> log "tier_token_clamped".
  - malformed `prompt_tokens_details` / `completion_tokens_details` (not a dict, non-int member) -> treat the tier as ABSENT (count 0) -> fall back to flat billing -> never crash the recorder.
</reject>
After:
<after>
  - The `usage_records` row's `cost_usd` reflects per-tier pricing; `cached_tokens`/`reasoning_tokens` columns are populated; `pricing_snapshot_id` still references the snapshot; Redis spend counters reflect the tiered total. Append-only preserved (no UPDATE/DELETE).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Token inclusivity — `prompt_tokens` INCLUDES `cached_tokens` and `completion_tokens` INCLUDES `reasoning_tokens` (OpenAI shape; OpenRouter/Azure normalize to it), so billing SUBTRACTS to find fresh tokens. Lowest confidence because a non-OpenAI adapter could surface them EXCLUSIVE, turning the subtraction into an under-count. If wrong: cached/reasoning split is off by up-to the tier count per request. Mitigation baked in: `max(0, base − tier)` clamp bounds the worst case to a small under-bill, never negative.
  - [ ] Canonical field paths are `prompt_tokens_details.cached_tokens` / `completion_tokens_details.reasoning_tokens` — confirm our adapters surface these names; if an adapter uses different names the tier is simply absent → flat billing (no error, no regression).
  - [ ] Two INT columns on `usage_records` (not raw-only) are worth the additive migration — required by the exit criterion "visible on the ledger row" (queryable).
  - [ ] The recorder does NOT enforce `cached_price ≤ prompt_price`; pricing sanity is the catalog's job. A snapshot with cached > prompt would bill as priced (acceptable — recorder prices, it does not police).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- Musts ---

Scenario: Cached input is billed below fresh input  (Must: cached split; exit-criterion 1)
  Given a pricing snapshot for "gpt-x": prompt=0.00001, completion=0.00003, cached_input=0.0000025 USD/token, markup=0
  And a 200 chat usage {prompt_tokens:1000, prompt_tokens_details:{cached_tokens:800}, completion_tokens:100}
  When the recorder records it
  Then the usage_records row cost_usd = 0.00700000  # (200*0.00001)+(800*0.0000025)+(100*0.00003)
  And the identical usage with cached_tokens:0 bills cost_usd = 0.01300000 — strictly greater

Scenario: Reasoning tokens are billed at the reasoning rate  (Must: reasoning split; exit-criterion 2)
  Given a snapshot: prompt=0.00001, completion=0.00003, reasoning=0.00006 USD/token, markup=0
  And usage {prompt_tokens:100, completion_tokens:500, completion_tokens_details:{reasoning_tokens:400}}
  When the recorder records it
  Then cost_usd = 0.02800000  # (100*0.00001)+((500-400)*0.00003)+(400*0.00006)
  And the row's reasoning_tokens column = 400

Scenario: No tiers + base-only prices is byte-identical to today  (Must: byte-identical floor)
  Given a snapshot: prompt=0.00001, completion=0.00003, cached_input=NULL, reasoning=NULL, markup=10
  And usage {prompt_tokens:1000, completion_tokens:500} with no *_details
  When the recorder records it
  Then cost_usd = 0.02750000  # ((1000*0.00001)+(500*0.00003))*1.10 — same operand order as the v6 path
  And the row's cached_tokens = 0 and reasoning_tokens = 0

Scenario: Absent tier price falls back to the base price  (Must: NULL tier-price fallback)
  Given a snapshot: prompt=0.00001, cached_input=NULL, completion=0.00003, markup=0
  And usage {prompt_tokens:1000, prompt_tokens_details:{cached_tokens:800}, completion_tokens:0}
  When the recorder records it
  Then cost_usd = 0.01000000  # cached tokens priced at prompt rate → 1000*0.00001, identical to flat
  And the row's cached_tokens = 800  (recorded for observability even though priced flat)

Scenario: Tier counts persist on the ledger row through the flusher  (Must: persist tiers)
  Given usage {prompt_tokens:1000, prompt_tokens_details:{cached_tokens:300}, completion_tokens:200, completion_tokens_details:{reasoning_tokens:50}}
  When the recorder records it and the flusher writes the row
  Then the usage_records row has cached_tokens = 300 and reasoning_tokens = 50

# --- Rejects (each asserts what stays unchanged) ---

Scenario: cached_tokens greater than prompt_tokens is clamped, never negative  (Reject: cached clamp)
  Given a snapshot with cached_input set and usage {prompt_tokens:100, prompt_tokens_details:{cached_tokens:150}}
  When the recorder records it
  Then fresh input is clamped to max(0, 100-150)=0 and cost_usd >= 0
  And a "tier_token_clamped" marker is logged
  And exactly one usage record is written and the recorder does not raise (append-only intact)

Scenario: reasoning_tokens greater than completion_tokens is clamped  (Reject: reasoning clamp)
  Given a snapshot with reasoning set and usage {completion_tokens:50, completion_tokens_details:{reasoning_tokens:80}}
  When the recorder records it
  Then non-reasoning output is clamped to max(0, 50-80)=0 and cost_usd >= 0
  And a "tier_token_clamped" marker is logged
  And exactly one usage record is written and the recorder does not raise

Scenario: malformed *_details falls back to flat billing  (Reject: malformed → absent)
  Given a snapshot prompt=0.00001, completion=0.00003, markup=0
  And usage {prompt_tokens:1000, prompt_tokens_details:"oops", completion_tokens:200, completion_tokens_details:{reasoning_tokens:"x"}}
  When the recorder records it
  Then both tiers are treated as absent and cost_usd = 0.01600000  # flat (1000*0.00001)+(200*0.00003)
  And the recorder does not raise and the row has cached_tokens = 0 and reasoning_tokens = 0
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# This task has NO HTTP surface. The contract is the billing function seam + the additive schema.

## DB schema (additive migration revising e2b7f4c9a1d8)
pricing_snapshots:
  ADD cached_input_usd_per_token  NUMERIC(20,10) NULL   -- cached-input price; NULL → fall back to prompt_usd_per_token
  ADD reasoning_usd_per_token     NUMERIC(20,10) NULL   -- reasoning price;    NULL → fall back to completion_usd_per_token
usage_records:
  ADD cached_tokens     INTEGER NOT NULL DEFAULT 0      -- cached-input tier count on this row (observability)
  ADD reasoning_tokens  INTEGER NOT NULL DEFAULT 0      -- reasoning tier count on this row (observability)
# downgrade() drops the four columns in reverse ADD order. (mirror c9e2f4a8b1d6)

## Pricing fetch  — recorder._fetch_latest_pricing(session, model_id)
returns 7-tuple | None:
  (snapshot_id: UUID, prompt_price: Decimal, completion_price: Decimal,
   pricing_unit: str, unit_usd_per_unit: Decimal|None,
   cached_input_price: Decimal|None, reasoning_price: Decimal|None)
  SELECT … , cached_input_usd_per_token, reasoning_usd_per_token  (appended to the existing SELECT)

## Pure cost helper  — recorder.compute_per_token_cost_usd(...)  (new module-level fn)
inputs:  prompt_tokens:int, completion_tokens:int, cached_tokens:int, reasoning_tokens:int,
         prompt_price:Decimal, completion_price:Decimal,
         cached_price:Decimal|None, reasoning_price:Decimal|None, markup_pct:Decimal
output:  cost_usd: Decimal
rule:
  if cached_tokens == 0 and reasoning_tokens == 0:
      # FLAT PATH — the existing v6 expression, verbatim, byte-identical:
      cost = (Dp*Pp + Dc*Pc) * (1 + markup/100)
  else:
      fresh_in  = max(0, prompt_tokens - cached_tokens)        # clamp → "tier_token_clamped" if it fired
      fresh_out = max(0, completion_tokens - reasoning_tokens)  # clamp → "tier_token_clamped" if it fired
      cprice = cached_price    if cached_price    is not None else prompt_price
      rprice = reasoning_price if reasoning_price is not None else completion_price
      cost = (fresh_in*Pp + cached_tokens*cprice + fresh_out*Pc + reasoning_tokens*rprice) * (1 + markup/100)
  # all operands Decimal(str(x)); markup term identical to today.

## Tier extraction  — recorder._record_internal (per_token branch only)
  cached_tokens    = _safe_tier(usage, "prompt_tokens_details", "cached_tokens")        # → int ≥ 0, malformed → 0
  reasoning_tokens = _safe_tier(usage, "completion_tokens_details", "reasoning_tokens")  # → int ≥ 0, malformed → 0
  # non-per_token pricing_units: cached_tokens = reasoning_tokens = 0 (untouched).

## Redis-stream event dict  — recorder.record (additive keys, mirror "quantity")
  + "cached_tokens": str(cached_tokens)        # "0" on every non-tiered / non-chat record
  + "reasoning_tokens": str(reasoning_tokens)

## Ledger write  — flusher._handle_entry (backward-compatible read + INSERT)
  cached_tokens    = int(_field("cached_tokens") or "0")
  reasoning_tokens = int(_field("reasoning_tokens") or "0")
  INSERT … , cached_tokens, reasoning_tokens   (appended to the existing column list + VALUES)

## Contracted response for every §1 Reject (internal — no HTTP code; observable behavior + marker)
  cached_tokens < 0 or > prompt_tokens        -> fresh_in = max(0, …);  log "tier_token_clamped"; cost_usd ≥ 0; one row written
  reasoning_tokens < 0 or > completion_tokens -> fresh_out = max(0, …); log "tier_token_clamped"; cost_usd ≥ 0; one row written
  malformed *_details (not dict / non-int)    -> _safe_tier returns 0 → FLAT path; recorder never raises; row has tier cols = 0

Schema names (GLOSSARY): cached-input token · reasoning token · pricing snapshot · usage record · markup.
```

Status: FROZEN @ v1 — approved by Tin Dang, 2026-06-17 (inclusivity assumption accepted with the max(0,…) clamp mitigation; risk: medium, autonomy: auto)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the touched recorder/flusher lines (matches the usage suite).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_tt1_cached_input_billed_below_fresh_input — tiered cost 0.007 < flat 0.013; event cached_tokens="800"
  - test_tt2_reasoning_tokens_billed_at_reasoning_rate — cost 0.028; event reasoning_tokens="400"
  - test_tt3_no_tiers_byte_identical_to_today — cost 0.0275 (markup 10); event cached/reasoning="0"  (byte-identical floor)
  - test_tt4_absent_cached_price_falls_back_to_prompt_price — cost 0.010; event cached_tokens="800"
  - test_tt5_db_tier_counts_persist_through_flusher — DB: row cached_tokens=300, reasoning_tokens=50 (Postgres+Redis)
  - test_tt6_cached_exceeds_prompt_is_clamped — fresh_in clamps to 0 → cost 0.000375 ≥ 0; exactly one record (unchanged)
  - test_tt7_reasoning_exceeds_completion_is_clamped — fresh_out clamps to 0 → cost 0.0048 ≥ 0; exactly one record (unchanged)
  - test_tt8_malformed_details_falls_back_to_flat — flat cost 0.016; event tiers="0"; recorder did not raise (unchanged)
  - test_tt_db_pricing_snapshots_has_tier_price_columns — DB: the two NUMERIC(20,10) NULL columns present
</test_plan>

RED CONFIRMED 2026-06-17: 7 unit tests red for the right reason (`cost_usd='0.01600'` flat + no `cached_tokens` key → missing tiered logic & event fields); 2 DB tests deselected (need Postgres, red on missing columns at verify).
Tests live in: `apps/gateway/tests/tiered_token_billing/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/src/gateway/usage/infrastructure/orm.py` `apps/gateway/src/gateway/catalog/infrastructure/orm.py` `apps/gateway/migrations/versions/` `apps/gateway/tests/tiered_token_billing/`
Strategy (ordered batches): 1. additive migration (4 cols, revises e2b7f4c9a1d8) + ORM column adds. 2. `_fetch_latest_pricing` 7-tuple + the pure `compute_per_token_cost_usd` helper (flat-path-verbatim branch). 3. `_record_internal` tier extraction (`_safe_tier`) + clamps + event-dict keys. 4. flusher read + INSERT. 5. wire helper into `_record_internal`.
Safety rule (feature-specific): the no-tier path must execute the EXISTING flat Decimal expression verbatim (byte-identical cost_usd string); tiered terms are only added when a tier count > 0. Recorder MUST NOT raise (fire-and-forget). Ledger stays append-only.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.
Scope addendum (build-time, honest): also touched `apps/gateway/tests/pricing_units/conftest.py` — a SIBLING test DOUBLE of the shared `_fetch_latest_pricing` seam. Its `FakeSession` hard-coded a 5-value pricing row; the frozen §3 grows that to a 7-tuple, so the double was synced to return 7 (two trailing `None` tier prices). NO pricing_units assertion changed; the full suite (1134) confirms it. This is the v26 sibling-double-sync precedent, not a test weakening. No package added (stdlib `logging` only, in tests).

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite **1134 passed, 19 deselected** (2m11s); the 9-test tiered suite green incl. 2 DB tests (Postgres 5433 + Redis 6380).
- [x] coverage did not decrease — recorder.py/flusher.py exercised by tiered+usage+pricing_units (28 passed under --cov, all new branches hit: flat, tiered, cached-only, reasoning-only, both clamps, malformed, NULL-price fallback).
- [x] no test or contract was altered during build — frozen §3 untouched; this task's own tests unchanged except a hardening ADD (caplog `tier_token_clamped` pin on TT6/TT7, from the refute-read). The only non-own-test edit is the pricing_units sibling-double ROW-SHAPE sync (§5 addendum) — no assertion weakened.
- [x] the green was EARNED — adversarial refute-read (sonnet, 7 attack points) returned **EARNED-GREEN**: arithmetic general not overfit; persistence chain complete; recorder cannot raise; sibling double safe. Its one finding (clamp-log marker unpinned) was CLOSED by adding the caplog assertions.
- [x] concurrency / timing — recorder is fire-and-forget; `compute_per_token_cost_usd` + `_safe_tier` are PURE (no shared state, no new locks/await); no new race surface.
- [x] no exposed secrets, injection openings, or unexpected dependencies — SELECT/INSERT use bound params (`:cached_tokens` etc.), no string interpolation; no secrets; no new package (stdlib `logging` only).
- [x] layering & dependencies follow CONVENTIONS.md — pure helpers in the application module; ORM in infrastructure; additive Alembic migration; `Decimal` money math throughout; ledger append-only preserved.
- [x] a person reviewed and approved the change — human (Tin) approved the bundle at the §3 freeze; under `autonomy: auto` with NO residue (security/concurrency/architecture all clear) the verify gate auto-resolves with this run accountable.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_safe_tier` + `compute_per_token_cost_usd` called in `_record_internal` (per_token branch + cached-hit branch); the 4 new columns read by `_fetch_latest_pricing` SELECT (snapshot prices) + the recorder event dict + flusher INSERT + ORM models; migration `f3c8d1a6b9e4` applies them. `migrations::test_autogenerate_empty_diff` PASS = ORM↔DDL match, no drift.
- [x] DEAD-CODE (code) — no orphans: every new symbol/column has a reader and a writer; no unused import (ruff check clean); `model=` arg on the helper feeds the clamp log.
- [x] SEMANTIC — n/a (code task); byte-identical floor confirmed empirically: full pre-existing billing suites (usage/pricing_units/embeddings/audio/images) green unchanged.

### GATE RECORD
Outcome: PASS
Tamper-tripwire note: the refute-read prompted a test-HARDENING edit (caplog `tier_token_clamped` pins on TT6/TT7) during verify, which tripped `build_tampered` (the mechanical tripwire cannot tell strengthen from weaken). Resolved the method-sanctioned way — re-crossed tests→build to re-baseline the snapshot (the added assertions were themselves red-before-build: pre-impl there is no clamp log). NO assertion weakened; full suite re-confirmed green.
Reviewed by: auto-resolved under autonomy:auto (run accountable) — bundle human-approved by Tin Dang at §3 freeze; adversarial refute-read EARNED-GREEN · date: 2026-06-17

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
