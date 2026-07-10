# TASK: Monetization: per-model/per-tier rate cards replace flat markup_pct

slug: tiered-rate-cards · created: 2026-07-02 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
- `tenants/infrastructure/orm.py:TenantRow.markup_pct` — `Numeric(7,4)` NOT NULL `server_default="20.0"`; the flat per-tenant markup this task EXTENDS. Sibling additive cols (`budget_usd_monthly` nullable, `cache_enabled`, `guardrail_configs` JSONB, `semantic_cache_enabled`) model the additive-migration pattern to mirror.
- `usage/application/recorder.py:_fetch_markup_pct(session, tenant_id) -> Decimal` (L705) — `SELECT markup_pct FROM tenants WHERE id=:tid`; 0 if tenant absent. **Billing read #1.**
- `usage/application/recorder.py` markup application — `_compute_cost(...markup_pct...)` (L584, per-tier token cost; FLAT PATH byte-identical to pre-tier at L599); applied at L223 (provider_cost basis), L276 (per-unit quantity), L306 (disconnect strip-markup → recover provider_cost).
- `usage/application/cost_recovery.py:L219` — inline `SELECT markup_pct FROM tenants WHERE id=:t`. **Billing read #2** (disconnect / OpenRouter recovery).
- `catalog/infrastructure/repository.py:list_active_models_with_markup` (L115 joins `TenantRow.markup_pct`; L129 `multiplier = 1 + markup_pct/100`) → `MarkedUpModel`. **Read #3** (catalog display). Port at `catalog/domain/ports.py:56`.
- `usage/application/recorder.py:_fetch_latest_pricing(session, model)` (L178) — resolves per-MODEL provider prices from `pricing_snapshots`; markup applies ON TOP of this per-model cost basis (so per-model markup composes naturally).
- Migration home: `apps/gateway/migrations/versions/` — `markup_pct` born in `ad14442336db_baseline.py:51`. New rate-card table/column = ONE additive Alembic migration here.

Context (working folder):
- **No admin write-API for markup exists yet** — `tests/catalog/test_model_catalog.py:329` sets markup "directly in DB (bypasses the not-yet-built API)". The MILESTONE.md "admin API" for rate cards is NET-NEW.
- **Regression blast radius (must stay byte-identical when NO rate card is set):** FakeSession conftests mock the markup read in `tests/{pricing_units,provider_cost_reconciliation,tiered_token_billing}/conftest.py` (return `FakeRow((str(markup_pct),))` by matching the literal `SELECT markup_pct FROM tenants` text), plus `tests/{openrouter_cost_recovery,catalog,usage,prompt_cache_passthrough,team_attribution,disconnect_provider_cost,kind_e2e}`. ⇒ the effective-rate resolver MUST preserve that exact fallback query on the no-override path or ~8 suites break.
- v56 (unmerged branch) `tenant_model_presets` — a per-(tenant,model) precedent + a naming collision to avoid; the new table MUST live in THIS branch's `Base.metadata`. Test DB: use the `gateway_test_gwN` per-worktree convention.

Honors (patterns / conventions):
- MILESTONE.md shared decisions: billing truth = append-only `usage_records`; **change-request re-opens Specify** (this REPLACES the documented flat-`markup_pct` behavior → the freeze ratifies it); design-for-failure on every IO seam.
- Decimal arithmetic end-to-end; **byte-identical fallback when the feature is unconfigured** — mirror `tiered-token-billing` (recorder.py:598-600 "FLAT PATH — byte-identical to pre-tier code"; a NULL tier price falls back to base).
- Additive migration whose `server_default` covers pre-existing rows (backward compatible; no data backfill).
- **Single effective-rate resolver, THREE callers** (advisor): recorder billing, cost_recovery disconnect, catalog display MUST resolve the SAME per-(tenant,model) rate — else catalog shows one price, billing charges another, recovery a third (the B1 "missed 3rd charged site" failure mode, reincarnated).

Anchors the contract cites:
- `TenantRow` (schema: `markup_pct` fallback + the new rate-card structure/table)
- `_fetch_markup_pct` → generalized into the new effective-rate resolver (per-tenant, per-model)
- `_compute_cost` (markup application; FLAT + tier paths)
- `cost_recovery.py` markup read
- `list_active_models_with_markup` / `MarkedUpModel`
- the new admin endpoint · the new rate-card table · the new Alembic migration

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-model rate cards — an admin sets a per-(tenant, model) markup that overrides the tenant's flat `markup_pct`; billing, disconnect-recovery, and catalog display all charge/show that model's effective rate through ONE shared resolver. Flat `markup_pct` stays the fallback when a model has no entry (byte-identical to today).

Framings weighed:
  - **Per-model override + flat fallback** (chosen) — new additive `tenant_rate_card_entries` table keyed (tenant_id, model_id); a missing entry falls back to `tenants.markup_pct`. No new shared entity, no data backfill, byte-identical when unconfigured. Delivers the exit criterion ("charge that model's rate, not one flat percentage") with the smallest surface.
  - Shared named RateCard plan (a `rate_cards` entity multiple tenants attach to via FK) — richer "tier" story, but a net-new shared entity + assignment API; deferred to a spec-delta (this is the ⚠ flag below).
  - Full replacement of `markup_pct` by a rate_cards table (no scalar fallback) — cleanest data model but breaks ~8 markup-mocking test suites + needs a backfill migration; rejected (violates byte-identical honor).
Must:
<must>
  - An admin can SET a per-model markup for a tenant (create/update a rate-card entry); a subsequent billed request for that model charges the override rate, not the flat `markup_pct`.
  - An admin can LIST a tenant's rate-card entries and DELETE one (delete → that model reverts to the flat fallback).
  - When a model has NO entry for the tenant, billing/recovery/catalog all fall back to `tenants.markup_pct` — byte-identical to the pre-rate-card behavior (the exact `SELECT markup_pct FROM tenants` fallback query is preserved).
  - The effective per-(tenant, model) rate resolves IDENTICALLY across all three read sites — recorder billing (`_compute_cost`), disconnect/OpenRouter recovery (`cost_recovery.py`), and catalog display (`list_active_models_with_markup`) — via one shared resolver (no third-site drift).
  - The override applies uniformly across every cost basis the flat markup covers today: per-token (incl. cached/reasoning/cache-creation tiers) and per-unit quantity (per_image/second/character).
  - All markup arithmetic stays Decimal; the stored/applied override is `Numeric(7,4)` like `markup_pct`.
</must>
Reject:
<reject>
  - a caller WITHOUT `Permission.RATE_CARDS_MANAGE` (i.e. not OWNER) tries to set/list/delete a rate-card entry -> "forbidden" (403)
  - markup_pct value that is negative, non-numeric, or exceeds the column range (Numeric(7,4)) -> "invalid_markup" (422)
  - a duplicate (tenant_id, model_id) SET -> idempotent UPSERT (update the existing entry), NOT an error (documented non-error)
  - a DELETE of a model with no entry -> idempotent 204 (already-absent is success), NOT 404 (documented non-error)
</reject>
<!-- scope note: every endpoint operates on the CALLER'S OWN tenant (identity.tenant_id) — so a
     "tenant_not_found" reject cannot arise (the JWT's tenant always exists). If the freeze chooses the
     platform-operator model instead (§1 assumption flag #2), an explicit {tenant_id} path segment +
     a 404 tenant_not_found reject come back. -->

After:
<after>
  - the rate-card entry is persisted in `tenant_rate_card_entries` (UNIQUE(tenant_id, model_id)); GET reflects it.
  - a billed status=200 request for that (tenant, model) writes a `usage_records` row whose cost = provider_cost × (1 + override/100); a model with no entry is byte-identical to before.
  - `GET /v1/models` for that tenant shows the model priced at the override; other models unchanged.
  - deleting the entry reverts that model to the flat `markup_pct` on the next request.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **"per-tier" semantics** — lowest confidence because the MILESTONE.md phrase "per-model + per-tier markup" is undesigned and this repo has NO tenant pricing-tier/plan concept ("tier" today means token-type tiers: cached/reasoning). I read a "rate card" as THIS tenant's per-model overrides + its flat default (MVP), NOT a shared named plan multiple tenants attach to. If wrong: need a `rate_cards` entity + tenant→card FK + card-assignment admin API — a schema + API reshape. Surfaced as the bundle's lead freeze flag; the alternative is logged as a §7 spec-delta.
  - [x] **model identity key — RESOLVED (grounded):** all three sites key on the model_id STRING. `_fetch_latest_pricing(session, model_id: str)` queries `pricing_snapshots WHERE model_id = :model_id` (recorder.py:680); the catalog joins `snap_sub.c.model_id == ModelRow.id` (repository.py:117) — so `ModelRow.id == pricing_snapshots.model_id == the recorder's model arg` (e.g. "gpt-4o"). No UUID/string mismatch. ⇒ `tenant_rate_card_entries.model_id` is a String; resolution rule = `COALESCE(entry.markup_pct, tenants.markup_pct)`; the trap is avoided by making that rule the ONE source (shared scalar resolver for recorder+recovery; identical LEFT-JOIN+COALESCE for the catalog bulk list) + a billing==catalog equality test.
  ⚠ **who may set markup — the revenue-model flag (2nd freeze decision)** — markup is the PLATFORM's margin over provider cost; letting a tenant lower its own markup is a revenue hole in a monetization-integrity milestone. But the existing authz is tenant-scoped (a JWT is bound to ONE tenant), and a cross-tenant platform-operator surface would entangle S1's ops-permission — while this task deps ONLY usage-flusher-durability. Drafted the least-entangled revenue-honest MVP: writes are gated by a NEW OWNER-only `Permission.RATE_CARDS_MANAGE` and act on the caller's OWN tenant. If wrong (Tin wants platform-operator-only / cross-tenant): re-scope to an explicit {tenant_id} + bind to the S1 ops-permission (adds a cross-task dep) — a freeze decision, logged as a §7 spec-delta.
  - [ ] **setting a markup for a model absent from the catalog** — allowed (the override simply resolves once the model is billed/listed); NOT rejected as unknown_model. If wrong: add a catalog-existence check to the SET path.
  - [ ] **new `RATE_CARDS_MANAGE` permission vs reuse** — adding an enum value touches the frozen authz matrix (`ROLE_PERMISSIONS` + the import-time completeness guard + its test). Chose a dedicated OWNER-only permission (self-documenting; additive, well-patterned) over overloading `SECURITY_CONFIG` (semantic lie). Confirm at freeze.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Set a per-model markup and bill at the override   # Must 1
  Given tenant T has flat markup_pct=20 and NO rate-card entry for "gpt-4o"
  When an admin sets a rate-card entry (T, "gpt-4o", markup_pct=50)
  And a status=200 request for T on "gpt-4o" is billed with provider_cost=$1.00
  Then the usage_records row cost = 1.00 × 1.50 = $1.50 (override), not $1.20
  And a request for a different model with no entry still bills at the flat ×1.20

Scenario: No entry falls back byte-identical to the flat markup   # Must 3
  Given tenant T has flat markup_pct=20 and NO rate-card entries at all
  When a status=200 request for T on any model with provider_cost=$1.00 is billed
  Then the cost = $1.20 — exactly the pre-rate-card value
  And the markup read still issues `SELECT markup_pct FROM tenants` (fallback query preserved)

Scenario: Catalog display equals billing for the same override — no third-site drift   # Must 4
  Given tenant T has a rate-card entry (T, "gpt-4o", 50)
  When GET /v1/models is fetched for T and a "gpt-4o" request for T is billed
  Then the catalog prompt/completion price for "gpt-4o" = upstream × 1.50
  And the billed cost uses the SAME ×1.50 multiplier (catalog price == billing basis, no drift)

Scenario: Disconnect/OpenRouter recovery uses the override too   # Must 4 (recovery site)
  Given tenant T has a rate-card entry (T, "gpt-4o", 50)
  When a cost-recovery correction is computed for a "gpt-4o" request with authoritative provider_cost=$2.00
  Then the recovered total reaches 2.00 × 1.50 = $3.00 (override), not ×1.20

Scenario: Override applies across a per-unit (non-token) cost basis   # Must 5
  Given tenant T has a rate-card entry (T, "dall-e-3", 30) and a per_image snapshot (unit=$0.04, quantity=2)
  When a status=200 per_image request for T on "dall-e-3" is billed
  Then the cost = 2 × 0.04 × 1.30 = $0.104 (override applied on the per-unit basis)

Scenario: List then delete reverts to the flat fallback   # Must 2
  Given tenant T has a rate-card entry (T, "gpt-4o", 50)
  When an admin GETs T's rate card (sees the entry), then DELETEs entry (T, "gpt-4o")
  Then GET after delete shows no entry for "gpt-4o"
  And a subsequent billed "gpt-4o" request for T (provider_cost=$1.00) costs $1.20 (reverted to flat markup_pct=20)

Scenario: A non-OWNER cannot set a rate-card entry   # Reject 1
  Given a caller WITHOUT Permission.RATE_CARDS_MANAGE (e.g. ADMIN or MEMBER)
  When they PUT a rate-card entry for their tenant
  Then the response is 403 "forbidden"
  And no rate-card entry is persisted (state unchanged)

Scenario: Invalid markup value is rejected   # Reject 2
  Given an authorized OWNER
  When they PUT a rate-card entry with markup_pct = -5 (or non-numeric / exceeding Numeric(7,4) range)
  Then the response is 422 "invalid_markup"
  And no entry is persisted (state unchanged)

Scenario: Deleting a model with no entry is idempotent   # Reject 3 (documented non-error)
  Given tenant T has NO rate-card entry for "gpt-4o"
  When an admin DELETEs entry (T, "gpt-4o")
  Then the response is 204 (already-absent is success), not 404
  And T still has no entry for "gpt-4o" (state unchanged) and billing stays on the flat fallback

Scenario: Duplicate (tenant, model) create is an idempotent upsert   # Reject 4 (documented non-error)
  Given tenant T already has a rate-card entry (T, "gpt-4o", 50)
  When an OWNER PUTs (T, "gpt-4o", 30) again
  Then the entry is UPDATED to 30 (idempotent success), not rejected
  And exactly ONE entry remains for (T, "gpt-4o") (UNIQUE(tenant_id, model_id) preserved)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── Admin API — acts on the CALLER'S OWN tenant (identity.tenant_id);
#    every route: require_permission(Permission.RATE_CARDS_MANAGE)  (OWNER-only) ──
PUT /admin/rate-cards/{model_id}      body: { markup_pct: number }      # idempotent upsert
  200 -> { model_id: string, markup_pct: string }        # Decimal serialized as string, like existing money fields
  403 -> { error: "forbidden" }                          # caller lacks RATE_CARDS_MANAGE
  422 -> { error: "invalid_markup" }                     # negative | non-numeric | exceeds Numeric(7,4)

GET /admin/rate-cards
  200 -> { entries: [ { model_id: string, markup_pct: string } ] }      # [] when the tenant has none
  403 -> { error: "forbidden" }

DELETE /admin/rate-cards/{model_id}
  204 -> (no body)                                       # idempotent — already-absent is success (never 404)
  403 -> { error: "forbidden" }

# ── Schema — ONE additive Alembic migration in apps/gateway/migrations/versions/ ──
Table tenant_rate_card_entries:               # name deliberately AVOIDS the substrings "tenants" & "pricing_snapshots"
  id          UUID          PK  default uuid7
  tenant_id   UUID          NOT NULL  FK -> tenants.id  ON DELETE CASCADE
  model_id    String        NOT NULL          # == pricing_snapshots.model_id == ModelRow.id (e.g. "gpt-4o")
  markup_pct  Numeric(7,4)  NOT NULL           # same type/semantics as tenants.markup_pct; CHECK >= 0
  created_at  timestamptz   default now()
  updated_at  timestamptz   default now()
  UNIQUE (tenant_id, model_id)                 # one entry per (tenant, model) — the UPSERT conflict target
  INDEX  (tenant_id)                           # LIST + per-model resolve
  No server_default / NO backfill: an ABSENT row means "fall back to tenants.markup_pct".
  Declared in THIS branch's Base.metadata (new orm module gateway/tenants/infrastructure/rate_card_orm.py or fold into orm.py).

# ── Effective-rate resolver — THE single source of truth (one rule, three callers) ──
resolve_markup_pct(session, tenant_id: UUID, model_id: str) -> Decimal:
  1. SELECT markup_pct FROM tenant_rate_card_entries WHERE tenant_id=:t AND model_id=:m   # override, if any
  2. ELSE the EXACT existing fallback:  SELECT markup_pct FROM tenants WHERE id = :tid     # 0 if tenant absent
  Callers (all resolve the identical value):
    - recorder.py: `_fetch_markup_pct(session, tenant_id)` gains a `model_id` param and delegates here;
      `_compute_cost` is UNCHANGED (still receives one Decimal markup_pct). model_id is already in scope at the call site (L179).
    - cost_recovery.py:219: the inline `SELECT markup_pct FROM tenants` becomes a resolver call with (tenant_id, model_id).
    - catalog/infrastructure/repository.py:list_active_models_with_markup: add a LEFT JOIN tenant_rate_card_entries
      ON (tenant_id, ModelRow.id) and use COALESCE(entry.markup_pct, TenantRow.markup_pct) as the per-row multiplier — SAME rule, bulk form.
  INVARIANT (no third-site drift): for any (tenant, model), catalog multiplier == billing multiplier == recovery multiplier.

# ── Authz matrix delta (tenants/domain/authz.py) ──
Permission.RATE_CARDS_MANAGE added to the enum; ROLE_PERMISSIONS[OWNER] auto-holds it (frozenset(Permission));
ADMIN / MEMBER / lower roles do NOT. The import-time completeness guard (OWNER holds ALL) + its test still pass.

Schema touched: NEW tenant_rate_card_entries (RW via admin API; R via resolver on the hot billing + catalog paths).
  tenants.markup_pct UNCHANGED (still the fallback). usage_records UNCHANGED (cost still a computed Decimal).
```

Status: FROZEN @ v1 — approved by Tin (2026-07-02)
Least-sure flag surfaced at freeze: [spec] "per-tier" semantics — drafted as per-(tenant,model) overrides + flat `markup_pct` fallback, NOT a shared named RateCard "plan"; if wrong the cost is a schema+API reshape (`rate_cards` entity + assignment). [contract] who-may-set-markup — drafted OWNER-only (`RATE_CARDS_MANAGE`) on the caller's own tenant, NOT platform-operator cross-tenant. Both RESOLVED as-drafted at Tin's freeze; alternatives deferred to §7 spec-deltas. Neither is a security HARD-STOP.
Bundle lowest-confidence flags — surfaced at the freeze; RESOLVED as drafted:
  1. [spec] **"per-tier" semantics** — FROZEN as per-model overrides + flat fallback (no shared entity). The shared named RateCard "plan" alternative is deferred → §7 spec-delta.
  2. [contract] **who may set markup** — FROZEN as OWNER-only (`Permission.RATE_CARDS_MANAGE`) on the caller's OWN tenant. The platform-operator-only / cross-tenant alternative (binds S1's ops-permission) is deferred → §7 spec-delta.
  Both were DESIGN/authz — NOT security-gap HARD-STOPs; the build is behavior-additive + byte-identical when unconfigured.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on new code (billing math + resolver + router)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_set_per_model_markup_bills_override: set (T,"gpt-4o",50) / bill provider_cost=$1.00 / assert cost==$1.50 AND a no-entry model still bills ×1.20
  - test_no_entry_falls_back_byte_identical: no entries / bill / assert cost==$1.20 AND the markup read still hits `SELECT markup_pct FROM tenants` (fallback preserved)
  - test_catalog_price_equals_billing_for_override: set (T,"gpt-4o",50) / GET /v1/models + bill / assert catalog price == upstream×1.50 == billing multiplier (no third-site drift)
  - test_recovery_uses_override: set (T,"gpt-4o",50) / run cost-recovery correction (authoritative $2.00) / assert recovered total==$3.00 (×1.50)
  - test_override_applies_to_per_unit_basis: set (T,"dall-e-3",30) / per_image snapshot unit=$0.04 quantity=2 / assert cost==$0.104 (2×0.04×1.30)
  - test_list_then_delete_reverts_to_flat: set (T,"gpt-4o",50) / GET shows it / DELETE / GET empty / bill / assert cost==$1.20 (reverted)
  - test_non_owner_cannot_set_403: ADMIN/MEMBER PUT / assert 403 "forbidden" AND no row persisted
  - test_invalid_markup_422: PUT markup_pct=-5 (and a non-numeric case) / assert 422 "invalid_markup" AND no row persisted
  - test_delete_absent_entry_idempotent_204: DELETE a model with no entry / assert 204 AND state unchanged
  - test_duplicate_set_is_idempotent_upsert: PUT (T,"gpt-4o",50) then (T,"gpt-4o",30) / assert entry==30 AND exactly one row for (T,"gpt-4o")
  - test_owner_holds_rate_cards_manage_matrix: assert ROLE_PERMISSIONS[OWNER] holds RATE_CARDS_MANAGE and ADMIN/MEMBER do not (authz-matrix delta pinned; completeness guard still green)
  - REGRESSION (not new tests — run at verify): tiered_token_billing · pricing_units · provider_cost_reconciliation · catalog · usage · prompt_cache_passthrough · openrouter_cost_recovery stay GREEN unchanged (byte-identical proof).
</test_plan>

Tests live in: `apps/gateway/tests/tiered_rate_cards/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/` · `apps/gateway/src/gateway/usage/application/` · `apps/gateway/src/gateway/catalog/` · `apps/gateway/src/gateway/main.py` · `apps/gateway/migrations/versions/` · `apps/gateway/tests/tiered_rate_cards/`
Strategy (ordered batches):
  1. Schema + migration — new `tenant_rate_card_entries` ORM model in THIS branch's Base.metadata + additive Alembic migration (UNIQUE(tenant_id,model_id), CHECK markup_pct>=0, ON DELETE CASCADE; no backfill).
  2. Shared resolver — `resolve_markup_pct(session, tenant_id, model_id) -> Decimal`: override row → ELSE the EXACT `SELECT markup_pct FROM tenants WHERE id = :tid` fallback (text preserved verbatim).
  3. Wire the three callers — recorder `_fetch_markup_pct` gains `model_id` + delegates (`_compute_cost` untouched); cost_recovery.py:219 → resolver; catalog `list_active_models_with_markup` → LEFT JOIN + COALESCE(entry, tenant).
  4. Authz — add `Permission.RATE_CARDS_MANAGE`; OWNER auto-holds via `frozenset(Permission)`; ADMIN/MEMBER do not.
  5. Admin router — `tenants/api/rate_card_router.py` (PUT/GET/DELETE /admin/rate-cards), mount in main.py; 422 validation, 403 authz, idempotent upsert + idempotent delete.
Known-problem fixes:
  - third-site drift (B1 reincarnation) → ONE resolver rule; pin with the billing==catalog==recovery equality test.
  - byte-identical break under FakeSession mocks → table name avoids the substrings "tenants"/"pricing_snapshots" (verified: `tenant_rate_card_entries` matches neither); fallback query text immutable; run all 3 FakeSession suites + DB suites at verify.
  - cross-worktree schema drift on shared :5433 → new table in this branch's Base.metadata; verify on an isolated `gateway_test_gwN` DB.
  - authz completeness guard (`incomplete_matrix`) → OWNER holds ALL via frozenset(Permission); run the guard + its test.
  - scope-snapshot poison at gate → pristine `.pytest_cache`/`.ruff_cache`, re-cross tests→build→verify, do NOT run pytest/ruff between the re-cross and the gate ([[add-scope-snapshot-poisoning]]).
Strategy actually used: As planned (all 5 batches). New files: `rate_card_orm.py` (TenantRateCardEntry), `rate_card_resolver.py` (resolve_markup_pct), `rate_card_router.py` (admin API), migration `f70104c27b41` (chained onto head `c2e4a6f8b0d3`). Wirings: recorder `_fetch_markup_pct` + cost_recovery `_fetch_markup` both grew a `model` param and delegate to the resolver; catalog uses `func.coalesce(TenantRateCardEntry.markup_pct, TenantRow.markup_pct)` via LEFT JOIN. Upsert via `pg_insert(...).on_conflict_do_update`. ONE deviation-note (not a scope/contract deviation): `migrations/env.py`'s autogenerate allowlist omits the new ORM module — a PRE-EXISTING systemic gap (env.py already omits conversations/memories/artifacts/video_generation_jobs; env.py is OUTSIDE §5 scope). The hand-written migration round-trips cleanly (verified upgrade→downgrade→upgrade); only `alembic revision --autogenerate` is affected → §7 spec-delta chore.
Safety rule (feature-specific): billing math stays Decimal end-to-end; the NO-ENTRY path is byte-identical to pre-feature (the resolver's `SELECT markup_pct FROM tenants WHERE id = :tid` fallback text is immutable); a rate-card override is READ-only on the hot path (never a write during billing).
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

- [x] all tests pass — tiered_rate_cards 11/11 + the 9 markup-regression suites 104/104 + rbac_roles 8/8 (re-run first-hand against isolated `gateway_test_gw_ratecard`)
- [x] coverage did not decrease — new code (resolver · router · orm · 3 wirings · authz) exercised by the 11 scenario tests; no covered lines removed
- [x] no test or contract was altered during build — build touched only src + one migration; `tests/tiered_rate_cards/` frozen; §3 FROZEN @ v1 unchanged (verified via `git diff`)
- [x] the green was EARNED, not gamed — my own refute-read CLEAN + an independent adversarial reviewer (revenue-drift focus, agent a0edd2988499e9d37) probed all 6 attack surfaces (incl. a live Pydantic NaN/Infinity/overflow probe) → VERDICT CLEAN, no BLOCK; 2 non-blocking spec-deltas (C1 settlement-time markup, C2 env.py autogen gap) carried to §7
- [x] concurrency / timing of the risky operation is safe — resolver is a bounded 2-SELECT read (no lock, no loop); PUT upsert is atomic (`on_conflict_do_update`); no new shared mutable state
- [x] no exposed secrets, injection openings, or unexpected dependencies — all SQL is parameterized `text()` / ORM; no secrets; NO new third-party dependency (uses existing sqlalchemy/pydantic/fastapi)
- [x] layering & dependencies follow CONVENTIONS.md — resolver in usage/application, router in tenants/api, orm in tenants/infrastructure; catalog importing `TenantRateCardEntry` from tenants/infrastructure mirrors its EXISTING import of `TenantRow` (consistent cross-context infra join)
- [x] a person reviewed and approved the change — Tin froze §3 @ v1 (contract approval); code-level review = my full refute-read + the independent adversarial pass (both CLEAN). Under autonomy: auto, verify auto-gates on this evidence (NOT a security task → no HARD-STOP).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A billed status=200 request for a (tenant, model) WITH an override records `cost_usd = provider_cost × (1 + override/100)` — CONFIRMED: RC1 cost=$1.50 (×1.50), RC5 per-unit=$0.104 (×1.30), NOT the flat $1.20/$0.096.
- [x] A model with NO entry bills byte-identical to the flat markup AND the resolver still issues `SELECT markup_pct FROM tenants` — CONFIRMED: RC2 green (cost=$1.20) + all 9 markup-regression suites 104/104 green UNCHANGED; resolver preserves the fallback text verbatim (read in rate_card_resolver.py:60).
- [x] `GET /v1/models` price for an override model equals the billing multiplier — CONFIRMED: RC3 asserts catalog `prompt_per_token` == upstream×1.50 == billed multiplier (catalog==billing, no third-site drift).
- [x] Disconnect/OpenRouter recovery reaches `provider_cost × override` — CONFIRMED: RC4 summed `usage_records.cost_usd` == $3.00 (×1.50).
- [x] Admin API observable: PUT upserts (idempotent, 200 + markup_pct); GET lists; DELETE 204 even when absent — CONFIRMED: RC6/RC9/RC10 green.
- [x] A non-OWNER PUT is 403 `problem+json` `code=="ERR_AUTH_FORBIDDEN"`; an invalid markup is 422 `problem+json` (a `code` field present) — CONFIRMED: RC7/RC8 green; validation is real Pydantic `Field(ge=0, max_digits=7, decimal_places=4)`; NO row persisted.
- [x] `ROLE_PERMISSIONS[OWNER]` holds `RATE_CARDS_MANAGE`, ADMIN/MEMBER do not, completeness guard still passes — CONFIRMED: RC11 green + rbac_roles 8/8.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `resolve_markup_pct` imported+called by recorder.py (L177) AND cost_recovery.py (L158); `TenantRateCardEntry` used by catalog/repository.py (LEFT JOIN) + rate_card_router.py (upsert) + the migration; `RATE_CARDS_MANAGE` gates all 3 router routes; `rate_card_router` `include_router`'d in main.py. No orphan.
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; the old inline markup SELECTs in recorder+cost_recovery were REPLACED (not left dangling); `_fetch_markup_pct`/`_fetch_markup` kept as thin delegators (still called by their sites).
- [~] SEMANTIC — n/a (code task); the frozen §3 contract was read in full and the build matches it (no deviation).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (AI, autonomy: auto) + independent adversarial review by agent a0edd2988499e9d37 (backend-expert, revenue-drift focus) · adversarially checked: (1) cross-site drift catalog-COALESCE vs resolver two-step (incl. override-only model with no pricing_snapshot, model_id case/whitespace) — consistent, no $ split; (2) byte-identical fallback text + no failed-transaction/session-state hazard from the extra SELECT; (3) disconnect strip-markup uses the same local resolved markup; recovery re-resolves fresh (pre-existing temporal behavior, preserved → C1 spec-delta); (4) authz OWNER-only + tenant_id from JWT only (no cross-tenant param) + live Pydantic probe (NaN/Infinity/1e10/precision all → 422, no 500 bypass); (5) on_conflict target == UNIQUE constraint (atomic upsert); (6) env.py autogen metadata gap (→ C2). VERDICT CLEAN — no unconditional or trivially-triggerable misbilling path.

### GATE RECORD
Outcome: PASS
Deferred (non-blocking, NOT security — carried to §7 spec-deltas): C1 recovery settlement-time markup (pre-existing temporal semantics faithfully preserved; contract invariant is spatial; no adversarial incentive under OWNER-only-own-tenant) · C2 migrations/env.py autogenerate allowlist omits rate_card_orm (pre-existing systemic pattern gap; env.py OUTSIDE §5 scope + milestone rule = don't fold out-of-file defects; hand-written migration round-trips cleanly; runtime unaffected).
Reviewed by: self (AI, autonomy: auto) + independent adversarial review (agent a0edd2988499e9d37) · Tin froze §3 @ v1 · date: 2026-07-02

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin (2026-07-02))
- [AI] build — strategy used: As planned (all 5 batches). New files: `rate_card_orm.py` (TenantRateCardEntry), `rate_card_resolver.py` (resolve_markup_pct), `rate_card_router.py` (admin API), migration `f70104c27b41` (chained onto head `c2e4a6f8b0d3`). Wirings: recorder `_fetch_markup_pct` + cost_recovery `_fetch_markup` both grew a `model` param and delegate to the resolver; catalog uses `func.coalesce(TenantRateCardEntry.markup_pct, TenantRow.markup_pct)` via LEFT JOIN. Upsert via `pg_insert(...).on_conflict_do_update`. ONE deviation-note (not a scope/contract deviation): `migrations/env.py`'s autogenerate allowlist omits the new ORM module — a PRE-EXISTING systemic gap (env.py already omits conversations/memories/artifacts/video_generation_jobs; env.py is OUTSIDE §5 scope). The hand-written migration round-trips cleanly (verified upgrade→downgrade→upgrade); only `alembic revision --autogenerate` is affected → §7 spec-delta chore.
- [AI] verify — gate PASS (reviewed by self (AI, autonomy: auto) + independent adversarial review (agent a0edd2988499e9d37))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] **C1 — recovery settlement-time markup**: `cost_recovery._fetch_markup` re-resolves the CURRENT rate at settlement, so a rate-card edit between the disconnect anchor and `recover()` (inline ~5–35s; wider if the periodic sweeper is on) settles at the new rate, not request-time. Pre-existing temporal behavior, now first mutable via this task's self-service API. Decide: pin the resolved markup on the anchor row (request-time guarantee) vs document settlement-time as intended. Needs `test_recovery_after_override_changed_mid_flight` (evidence: adversarial review agent a0edd2988499e9d37; green suite holds override static so cannot see it).
- [SPEC · open] **C2 — migrations/env.py autogenerate gap**: `env.py` explicitly imports each feature ORM module into `target_metadata` but omits `gateway.tenants.infrastructure.rate_card_orm` (and pre-existing: conversations/memories/artifacts/video). Next `alembic revision --autogenerate` on this branch would propose `DROP TABLE tenant_rate_card_entries` → mass rate-card loss if applied unreviewed. One-line fix: add `import gateway.tenants.infrastructure.rate_card_orm  # noqa: F401` (env.py is OUTSIDE this task's §5 scope; runtime + hand-written migration unaffected) (evidence: adversarial review + grep zero `rate_card` matches in env.py).
- [SPEC · seeded] **the shared named RateCard "plan" tier** (the frozen "per-tier" alternative) — a `rate_cards` entity multiple tenants attach to via FK + assignment admin API; and **platform-operator cross-tenant markup authz** (bind S1's ops-permission) — both deferred at Tin's freeze (evidence: §1 assumptions ⚠, §3 freeze flags).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · open] a green suite that holds a mutable input STATIC across the whole test cannot see time-of-check/time-of-settlement drift; for any value now made mutable by a new write API, add a "changed mid-flight" test (evidence: C1 was invisible to 11 scenario + 104 regression tests).
- [SDD · open] a new self-service WRITE API silently widens the blast radius of PRE-EXISTING read-time semantics elsewhere (recovery re-resolve; env.py autogen); a build's grounding should scan "what does making X mutable newly expose?" not just "does X compute right" (evidence: C1 + C2 both pre-existing, both newly reachable via this task).
