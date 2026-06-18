# TASK: Prefer the upstream-reported cost when present; stamp cost_basis for audit

slug: provider-cost-reconciliation · created: 2026-06-17 · stage: production
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
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder._record_internal`
  — the billing seam (t1 owner). Today (lines 174-195) the per_token branch computes
  `cost_usd = compute_per_token_cost_usd(...)` from catalog prices ONLY; no provider cost is
  ever read. The `usage: dict[str, object] | None` arg is the VERBATIM upstream `response_body["usage"]`
  (confirmed via proxy/application/use_cases.py:_fire_record_with_raw → _dispatch_record → record).
- `apps/gateway/src/gateway/usage/application/recorder.py:_safe_tier` — the fail-safe tier-extractor
  pattern (malformed/bool/negative → 0) I will MIRROR for a `_safe_provider_cost` (Decimal | None).
- `apps/gateway/src/gateway/usage/application/recorder.py` event_fields dict (lines 267-286) — the
  Redis-stream event the flusher consumes; I append `cost_basis` + `provider_cost` (str-encoded).
- `apps/gateway/src/gateway/usage/application/flusher.py:UsageStreamFlusher._process_entry` (lines 111-194)
  — reads each event field via `_field(...)`, INSERTs into `usage_records`. I extend the `_field`
  reads + the INSERT column list/VALUES/params with the two new columns.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` (lines 35-73) — append-only
  ledger ORM; I add `cost_basis` (Text NOT NULL DEFAULT 'catalog') + `provider_cost` (Numeric(20,10) NULL).
- `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:OpenRouterCompletionUpstream`
  — forwards `payload` VERBATIM (complete() line 112-117, stream() line 141-152). OpenRouter only
  returns `usage.cost` when the request body sets `usage: {"include": true}`; the gateway does NOT
  send it today. The default-off opt-in knob (if approved at freeze) injects it here.

Context (working folder): migration head after t1 is `f3c8d1a6b9e4` (tiered_token_billing). The new
migration revises it. Test DB localhost:5433, Redis localhost:6380 db 9. Suite lives in
`apps/gateway/tests/provider_cost_reconciliation/` (per the milestone exit-criterion verify line).

Honors (patterns / conventions):
- **Byte-identical floor** (v6/v9/v10/v11): a usage payload WITHOUT a provider cost field bills via the
  EXISTING catalog path unchanged (same operand order, same rounding). Provider-cost is an additive branch.
- **All-Decimal arithmetic** (v12): `provider_cost × (1 + markup_pct/100)`, no float in the cost path.
- **Fail-safe extraction** (_safe_tier precedent, t1): malformed/non-numeric/negative provider cost →
  treated as ABSENT → catalog fallback (never trust a bad upstream number; never raise).
- **Accuracy is never an availability gate** (v12): a missing/garbage provider cost DEGRADES to catalog,
  it never fails the request.
- **Config-gated, default-off, reversible knob** (GATEWAY_UPSTREAM_MAX_RETRIES=0 / GATEWAY_AZURE_AD_AUTHORITY
  precedent): any outbound-request change is opt-in so the default path stays byte-identical.
- **Additive Alembic migration** (instant on PG11+: ADD COLUMN with NOT NULL DEFAULT / NULL).
- **Sibling test-double row-shape sync** (t1 lesson): extending `_fetch_latest_pricing`'s tuple again
  would re-break the pricing_units FakeSession — but cost_basis/provider_cost ride the USAGE dict + event,
  NOT the pricing tuple, so `_fetch_latest_pricing` stays a 7-tuple. No sibling-double churn expected.

Anchors the contract cites: `_record_internal`, `_safe_provider_cost` (new), `compute_per_token_cost_usd`
(unchanged catalog fallback), the `cost_basis`/`provider_cost` ledger columns, the `usage["cost"]` field,
the event_fields keys, the migration revising `f3c8d1a6b9e4`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: provider-cost-reconciliation — when the upstream usage payload carries its own cost, bill on
that cost (× markup) instead of catalog math, and stamp every ledger row with which basis produced it.

Framings weighed:
- **recorder consumes `usage["cost"]`, additive branch, basis stamped per row** (chosen) — the cost rides
  the SAME usage dict the recorder already receives (verbatim `response_body["usage"]`); a fail-safe
  extractor decides provider-vs-catalog; ledger gains `cost_basis` + `provider_cost`. Lowest blast radius,
  byte-identical when absent, mirrors the t1 tier seam exactly.
- new dedicated provider-cost service/port (rejected) — over-engineered; the recorder already owns cost.
- store only `cost_basis`, drop the raw number (rejected) — loses the audit trail the milestone demands
  ("auditable… records which basis produced it"); the raw provider number is needed to reconcile bills.

Must:
<must>
  - When `usage["cost"]` is present and a valid NON-NEGATIVE number (int or float, incl. 0), bill
    `cost_usd = Decimal(provider_cost) × (1 + markup_pct/100)`, set `cost_basis = "provider"`, and persist
    the raw reported number in `provider_cost`. This applies ONLY to non-cached records on the per_token unit.
  - A provider cost of exactly 0 (a free model that genuinely cost the upstream nothing) is AUTHORITATIVE:
    `cost_basis = "provider"`, `cost_usd = 0`, `provider_cost = 0` — NOT treated as absent (treating 0 as
    absent would wrongly fall back to catalog and over-bill a free model).
  - When `usage["cost"]` is absent/malformed, bill via the EXISTING catalog path unchanged (the t1
    `compute_per_token_cost_usd`), set `cost_basis = "catalog"`, leave `provider_cost` NULL — BYTE-IDENTICAL
    cost_usd to today.
  - Markup is applied identically to both bases (`× (1 + markup_pct/100)`), all-Decimal, no float.
  - `cost_basis` and `provider_cost` flow recorder event-dict → flusher → the new ledger columns; every row
    is queryable for which basis billed it.
  - (config-gated, default-off) GATEWAY_OPENROUTER_USAGE_ACCOUNTING — when true, the OpenRouter upstream
    injects `usage: {"include": true}` into the outbound payload so OpenRouter returns its cost; default
    false keeps the outbound request byte-identical to today. [DECISION FLAGGED — see Assumptions]
</must>
Reject:
<reject>
  - `usage["cost"]` is a bool / string / None / non-numeric -> treat as ABSENT -> "catalog" basis (never raise).
  - `usage["cost"]` is negative -> treat as ABSENT -> "catalog" basis + WARNING log `provider_cost_rejected`
    (a negative upstream cost is nonsense; never bill a negative, never trust it).
  - provider cost present on a CACHED hit -> ignored: a cache hit made no upstream call, so cost stays 0,
    basis "catalog" (no provider cost was used).
  - provider cost present on a NON-per_token unit (per_image/per_second/per_character) -> NOT honored in
    this task: those units bill by quantity; provider-cost reconciliation is scoped to the per_token chat/
    embeddings path (out-of-scope units keep their existing math, basis "catalog").
</reject>
After:
<after>
  - Every new usage_records row carries a non-NULL `cost_basis` ∈ {provider, catalog}; provider rows also
    carry `provider_cost` (the raw upstream number); catalog rows carry `provider_cost` NULL.
  - A payload with no provider cost bills exactly as it did before this task (byte-identical floor holds).
  - The billed `cost_usd` on a provider-basis row equals `provider_cost × (1 + markup)` to the ledger's
    numeric(14,8) precision.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The default-off OpenRouter opt-in knob (GATEWAY_OPENROUTER_USAGE_ACCOUNTING) is the right shape —
    lowest confidence because it's the ONE part that mutates the outbound proxy request (everything else is
    billing-only). Without SOME opt-in, no provider ever returns cost today, so the consume path is correct
    but DORMANT in production. Default-off preserves the byte-identical floor and matches the repo's knob
    idiom (retries=0, azure authority). If wrong (Tin wants consume-only, or wants it default-ON, or wants
    it omitted entirely): the recorder/ledger consume path is unaffected — only the OpenRouter adapter +
    one Settings field change. THIS is the freeze decision to confirm.
  - [ ] Provider cost is read from the single canonical field `usage["cost"]` (OpenRouter's field name,
    USD) — confirm. Other providers (Bedrock/Anthropic/Gemini/Azure) don't report cost → always catalog.
    If a provider used a different field, it'd silently stay catalog (safe, not wrong) until a follow-up.
  - [ ] `provider_cost` column is NUMERIC(20,10) (matches t1's tier-price columns), storing the RAW
    pre-markup reported number; billed cost_usd stays NUMERIC(14,8) — confirm the precision split.
  - [ ] A provider cost of 0 is authoritative (provider basis, $0), not "absent" — confirmed in Must;
    flagging because it's the one semantic that could surprise (free-model billing).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: provider cost present -> billed on provider basis
  Given a non-cached per_token chat record whose usage carries cost = 0.0042
  And the tenant markup is 10%
  When the recorder records it
  Then the ledger row has cost_basis = "provider", provider_cost = 0.0042
  And cost_usd = 0.0042 * 1.10 = 0.00462 (to numeric(14,8))

Scenario: no provider cost -> catalog basis, byte-identical
  Given a non-cached per_token chat record whose usage has NO cost field
  When the recorder records it
  Then cost_usd equals the exact catalog math compute_per_token_cost_usd would have produced today
  And cost_basis = "catalog"
  And provider_cost is NULL

Scenario: free model reports zero cost -> authoritative zero, not catalog
  Given a non-cached per_token record whose usage carries cost = 0
  When the recorder records it
  Then cost_basis = "provider", provider_cost = 0, cost_usd = 0
  And the catalog price is NOT consulted for the billed amount

Scenario: malformed provider cost -> catalog fallback, never raises
  Given a non-cached per_token record whose usage carries cost = "free" (a string)
  When the recorder records it
  Then cost_basis = "catalog" and the catalog math bills the row
  And no exception propagates and provider_cost is NULL

Scenario: negative provider cost -> rejected to catalog with warning
  Given a non-cached per_token record whose usage carries cost = -1.5
  When the recorder records it
  Then cost_basis = "catalog", provider_cost is NULL
  And a provider_cost_rejected warning is logged
  And the billed cost_usd is never negative

Scenario: provider cost on a cache hit is ignored
  Given a CACHED record whose usage carries cost = 0.99
  When the recorder records it
  Then cost_usd = 0 and cost_basis = "catalog"
  And provider_cost is NULL   # cache hit made no upstream call

Scenario: provider cost stamped end-to-end onto the ledger
  Given a non-cached per_token record with usage cost = 0.0042 recorded to Redis
  When the flusher drains the stream into Postgres
  Then the usage_records row persists cost_basis = "provider" and provider_cost = 0.0042

Scenario: opt-in knob injects usage accounting into the OpenRouter request
  Given GATEWAY_OPENROUTER_USAGE_ACCOUNTING is true
  When the OpenRouter upstream forwards a chat payload
  Then the outbound body carries usage = {"include": true}
  And with the knob false (default) the outbound body is byte-identical to today (no usage key added)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# This is an internal billing seam, not an HTTP endpoint. The frozen shapes:

## Recorder cost-basis selection (usage/application/recorder.py)
_safe_provider_cost(usage: dict[str, Any] | None) -> Decimal | None
  - usage["cost"] present AND int/float (NOT bool) AND >= 0  -> Decimal(str(cost))   # 0 is valid
  - missing / non-dict usage / bool / str / None / non-numeric -> None  (caller -> catalog)
  - negative -> None + _log.warning("provider_cost_rejected", extra={model, cost})   (caller -> catalog)

_record_internal, non-cached per_token branch:
  provider_cost = _safe_provider_cost(usage)
  if provider_cost is not None:
      cost_usd     = provider_cost * (Decimal("1") + Decimal(str(markup_pct)) / Decimal("100"))
      cost_basis   = "provider"
  else:
      cost_usd     = compute_per_token_cost_usd(...)   # UNCHANGED catalog path (t1)
      cost_basis   = "catalog"   ;   provider_cost stays None
  # cached hits, non-per_token units: cost_basis = "catalog", provider_cost = None (provider cost ignored)

## Event dict (recorder -> Redis stream) — two new string fields (additive)
  "cost_basis":   "provider" | "catalog"           # always present
  "provider_cost": str(Decimal) | ""               # "" encodes NULL

## Flusher (usage/application/flusher.py) — read + INSERT the two columns
  cost_basis    = _field("cost_basis") or "catalog"          # old events -> catalog
  provider_cost = Decimal(_field("provider_cost")) if _field("provider_cost") else None

## Schema (additive migration, revises f3c8d1a6b9e4)
  usage_records += cost_basis     TEXT           NOT NULL DEFAULT 'catalog'
  usage_records += provider_cost  NUMERIC(20,10) NULL          # raw upstream number, pre-markup
  # downgrade drops both, reverse order. ORM UsageRecordRow grows the two mapped columns.

## Config (gateway settings) — default-off opt-in [FROZEN: default-off, Tin 2026-06-17]
  GATEWAY_OPENROUTER_USAGE_ACCOUNTING: bool = False
  # when True, OpenRouterCompletionUpstream merges usage={"include": true} into the outbound
  # complete()/stream() payload (non-destructive: never overwrites a caller-supplied usage key).
  # default False -> outbound body byte-identical to today.

## Invariants (frozen)
  - No provider cost in usage  -> cost_usd BYTE-IDENTICAL to pre-task catalog math; basis "catalog".
  - provider_cost column stores the RAW reported number; billed cost_usd is provider_cost*(1+markup)
    rounded to numeric(14,8) (same column/rounding as catalog cost).
  - _fetch_latest_pricing stays a 7-tuple (cost-basis rides the usage dict + event, not the pricing row).
  - Never raises on a malformed cost; accuracy degrades to catalog, request always ships.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-17 (knob decision: GATEWAY_OPENROUTER_USAGE_ACCOUNTING default-OFF)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the new recorder/extractor/flusher branches.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - PC1 test_provider_cost_billed_on_provider_basis: usage cost=0.0042, markup 10% / record / cost_usd==0.00462, cost_basis=="provider", provider_cost==0.0042
  - PC2 test_no_provider_cost_is_catalog_byte_identical: usage w/o cost / record / cost_usd == compute_per_token_cost_usd(...) exactly, cost_basis=="catalog", provider_cost is None
  - PC3 test_zero_provider_cost_is_authoritative: usage cost=0 / record / cost_basis=="provider", cost_usd==0, provider_cost==0 (catalog NOT consulted — assert via a non-zero catalog price that would have billed > 0)
  - PC4 test_malformed_provider_cost_falls_back_to_catalog: usage cost="free" / record / cost_basis=="catalog", catalog cost billed, no raise, provider_cost None
  - PC5 test_negative_provider_cost_rejected_with_warning: usage cost=-1.5 / record / cost_basis=="catalog", provider_cost None, caplog has "provider_cost_rejected", cost_usd >= 0
  - PC6 test_provider_cost_on_cache_hit_ignored: cached=True usage cost=0.99 / record / cost_usd==0, cost_basis=="catalog", provider_cost None
  - PC7 test_bool_cost_not_treated_as_number: usage cost=True / record / cost_basis=="catalog" (bool is not a valid number)
  - PC8 test_safe_provider_cost_unit: direct unit-table over _safe_provider_cost (None/missing/str/bool/0/neg/float/int)
  - PC9 (DB) test_provider_basis_row_persisted: record provider-cost event -> flush -> usage_records row has cost_basis="provider", provider_cost==0.0042
  - PC10 (DB) test_catalog_basis_row_persisted: record no-cost event -> flush -> row cost_basis="catalog", provider_cost IS NULL
  - PC11 test_openrouter_knob_off_payload_unchanged: knob false / complete() outbound body has NO usage key added (byte-identical)
  - PC12 test_openrouter_knob_on_injects_usage_accounting: knob true / outbound body carries usage=={"include": true}; a caller-supplied usage key is NOT overwritten
</test_plan>

Tests live in: `apps/gateway/tests/provider_cost_reconciliation/` · MUST run red (missing implementation) before Build.
Red confirmed: 9 unit FAIL for the right reason (no cost_basis event field / _safe_provider_cost absent /
knob-on payload uninjected), PC11 (knob-OFF floor) GREEN by design, PC9/PC10 (DB) red on missing columns.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/src/gateway/usage/infrastructure/orm.py` `apps/gateway/migrations/versions/` `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` `apps/gateway/src/gateway/config/settings.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/provider_cost_reconciliation/`
Strategy (ordered batches):
  1. Migration revising f3c8d1a6b9e4: usage_records += cost_basis (TEXT NOT NULL DEFAULT 'catalog'), provider_cost (NUMERIC(20,10) NULL). ORM UsageRecordRow grows both mapped columns.
  2. recorder.py: add _safe_provider_cost; init cost_basis/provider_cost in _record_internal; provider branch in non-cached per_token; event_fields += cost_basis/provider_cost.
  3. flusher.py: read cost_basis/provider_cost via _field; extend INSERT column-list/VALUES/params.
  4. settings.py: GATEWAY_OPENROUTER_USAGE_ACCOUNTING bool=False; main.py threads it to the OpenRouter upstream ctor.
  5. openrouter_upstream.py: ctor takes usage_accounting flag; complete()/stream() merge usage={"include": true} non-destructively when on.
Safety rule (feature-specific): byte-identical floor — when _safe_provider_cost returns None the catalog
  path and its operand order are untouched; the provider branch only ever ADDS, never reorders existing math.
Code lives in: `apps/gateway/src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

BUILD-TIME SCOPE ADDENDA (declared in-task, the v25/v26 precedent):
- `apps/gateway/migrations/env.py` — ROOT-CAUSE harness fix. The migrations test-suite invokes
  Alembic autogenerate IN-PROCESS; `fileConfig(...)` defaulted to `disable_existing_loggers=True`,
  which set `disabled=True` on every already-imported `gateway.*` logger → suppressed the recorder's
  WARNINGs for the rest of the process → the full-suite caplog assertions in THIS task (PC5) AND the
  pre-existing t1 tests (TT6/TT7) saw empty `caplog.text`. Fix: `disable_existing_loggers=False`.
  A latent pre-existing bug surfaced by adding a 3rd caplog test; NO test weakened (harness strengthened).
- `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` class-level `_usage_accounting
  = False` default — so the retry_policy `make_upstream` __new__-double (which doesn't set the new
  attr) still resolves the knob OFF (byte-identical). Same class-level-default-extends-ctor precedent as v26.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — FULL gateway suite GREEN (1146 passed after the env.py fix; +PC13/PC14 → final
      run re-confirmed). 14-test provider_cost_reconciliation suite green incl. 2 DB persistence tests.
- [x] coverage did not decrease — additive branches + new tests; no code path removed.
- [x] no test or contract was altered during build — §3 contract FROZEN @ v1 unchanged. Test files were
      formatted (ruff) and HARDENED (PC13/PC14 + a type-annotation fix) during build then RE-CROSSED
      (tests→build re-snapshot) — no assertion weakened; the tamper-tripwire re-baselined the right way.
- [x] the green was EARNED — adversarial refute-read (sonnet, 11 attack points) = EARNED-GREEN @ 0.95,
      ZERO production bugs. Byte-identical floor PASS, zero-cost-authoritative PASS (`is not None`, not
      truthy), bool-exclusion PASS, Decimal(str(x)) PASS, negative-reject PASS, cache/non-token-ignore
      PASS, event→flusher→DB round-trip PASS, knob non-destructive PASS, migration PASS. The 2 gaps it
      raised (stream() injection untested; no Settings→upstream wiring test) were CLOSED before gate
      (PC13 + PC14); the type smell it raised was fixed (AsyncIterator→Iterator).
- [x] concurrency / timing — recorder is fire-and-forget already; provider-cost is a pure in-line branch,
      no new shared state, no new I/O. Flusher INSERT is the existing single-statement append.
- [x] no exposed secrets / injection — `provider_cost` is a numeric the recorder produces; the SQL INSERT
      is fully parameterized (named binds), no string interpolation. The knob adds a constant dict to the
      payload — no user data. No new dependency.
- [x] layering & dependencies follow CONVENTIONS.md — change stays in usage/application + usage/infra +
      proxy/infra + core/config + main wiring; no cross-layer leak. Decimal-only cost path preserved.
- [x] a person reviewed — Tin approved the frozen contract (the §7 decision point) + chose the knob shape.

### Deep checks
- [x] WIRING (code) — `_safe_provider_cost` referenced in `_record_internal:190`; `cost_basis`/`provider_cost`
      flow event_fields → flusher `_field` reads → INSERT params/columns → ORM mapped columns → migration
      DDL; `_maybe_inject_usage_accounting` called in complete() + stream(); `openrouter_usage_accounting`
      Settings field → main.py:373 ctor kwarg → `_usage_accounting` (PC14 asserts the live thread). No orphan.
- [x] DEAD-CODE — no unused symbol; `_usage_accounting` class default IS read by `_maybe_inject_usage_accounting`.
- [x] SEMANTIC — read the migration + env.py fix in full: env.py `disable_existing_loggers=False` is the
      canonical in-process-Alembic fix; downgrade reverses ADDs; types/nullability/default match the contract.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze + knob decision) · ADD auto-gate on complete evidence · date: 2026-06-17
Note: no security finding (parameterized SQL, no secret in the new fields, opt-in knob default-off). The
full-suite RED uncovered + fixed a PRE-EXISTING latent flake (Alembic env.py disabling app loggers in-process
→ empty caplog for t1's TT6/TT7 AND this task's PC5) — fixed at root WITHOUT weakening any test.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ratio of cost_basis=provider vs catalog rows per tenant (a sudden
drop to all-catalog when the knob is on signals OpenRouter stopped returning cost); rate of
`provider_cost_rejected` warnings (negative upstream costs = an upstream bug); any provider_cost that
diverges wildly from catalog math for the same model (mis-priced catalog or upstream anomaly).
Spec delta for the next loop: provider-cost is OpenRouter-canonical (`usage["cost"]`) only; t4
(stream-usage-completeness) must ensure the tiered+cost usage survives the SSE extractor; a future task
could broaden to other providers that report cost natively (none today).

### Competency deltas
- [TDD · folded] A latent test-isolation bug can hide until a 2nd/3rd test of the same kind is added —
  Alembic's `env.py fileConfig(...)` defaulted to `disable_existing_loggers=True`, silently disabling
  every `gateway.*` logger in-process once the migrations suite ran, so caplog saw nothing downstream.
  t1's TT6/TT7 only "passed" by collection-order luck. Lesson: when adding a caplog-on-app-logger test,
  treat full-suite ordering as part of the contract; the canonical fix is `disable_existing_loggers=False`.
  (evidence: 3 caplog tests RED in full suite, green after the env.py one-liner; bisected to tests/migrations).
- [TDD · folded] Extending a shared adapter ctor with a new attribute breaks sibling `__new__`-built test
  doubles (retry_policy `make_upstream`) unless a CLASS-LEVEL default is provided — same v26 lesson, now
  re-confirmed for `OpenRouterCompletionUpstream._usage_accounting=False`. Default on the class body, not
  only in __init__. (evidence: 9 retry_policy tests AttributeError → green after class-level default).
- [ADD · folded] The verify-gate adversarial refute-read again paid for itself: confirmed EARNED-GREEN AND
  surfaced 2 real coverage gaps (stream() injection + Settings→upstream wiring) that the scenario set
  under-pinned; both closed before gate (PC13/PC14). Extends the t1 refute-read delta.
- [SDD · folded] "Capture upstream-reported cost" had a hidden dormancy trap: consuming `usage["cost"]` is
  correct but NEVER fires unless the gateway opts into OpenRouter usage accounting. Surfacing that at the
  freeze (the default-off knob) turned a would-be no-op feature into a real, operator-flippable one.
