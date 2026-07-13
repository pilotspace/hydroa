# TASK: Region multiplier on rate cards via the shared resolver

slug: region-pricing · created: 2026-07-12 · stage: production
milestone: residency-service-tiers
autonomy: auto
phase: done
sensitivity: data

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/usage/application/rate_card_resolver.py:resolve_markup_pct(session, tenant_id, model_id) -> Decimal` — the FROZEN v1 (tiered-rate-cards) single resolver; layered rule "per-(tenant,model) override in `tenant_rate_card_entries` wins, ELSE `tenants.markup_pct`". UNTOUCHED by this task — this task adds a SIBLING function in the SAME module, never edits this one (its SQL text is byte-preserved on purpose; ~8 regression suites match on it verbatim).
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder.record` — the ONE place `markup_pct` is resolved (line ~282, `markup_pct = await _fetch_markup_pct(session, tenant_id, model)`) and applied across 3 sub-branches: provider_cost branch (~340-342), `compute_per_token_cost_usd(...)` (per-token branch, ~345-364 — markup math happens INSIDE this frozen helper, signature untouched), non-token branch (~396-401). A 4th site, the disconnect-provider-cost back-derivation (~424-432), DIVIDES markup back OUT of `cost_usd` to recover `provider_cost` for a non-recoverable disconnect.
- `apps/gateway/src/gateway/usage/application/cost_recovery.py:OpenRouterCostRecovery._fetch_markup(tenant_id, model)` (~216-223, delegates to `resolve_markup_pct`) and its caller (~161-162, `target = cost.total_cost * (1 + markup/100)`) — the recovery-path re-application of the SAME rate.
- `apps/gateway/src/gateway/catalog/infrastructure/repository.py:CatalogRepository.list_active_models_with_markup` (~87-186) — the bulk SQL join+COALESCE form of the identical rule; single `multiplier` scalar computed once (~153: `float(Decimal("1") + row.markup_pct / Decimal("100"))`) then reused for every priced field (prompt/completion/cached/audio_* — lines 159-186).
- `apps/gateway/src/gateway/billing/application/invoice_generator.py:InvoiceGenerator.generate_for_tenant` — FROZEN v1 (invoice-generation), NEVER calls `resolve_markup_pct` or any price function; sums already-billed `usage_records.cost_usd` verbatim (docstring is explicit: "NEVER calls resolve_markup_pct ... M3"). This is why the region multiplier needs ZERO invoice-side code — it flows through for free once baked into `cost_usd`.
- `apps/gateway/src/gateway/usage/api/margin_router.py` — same "never calls resolve_markup_pct" guarantee (tested: `test_m2_never_calls_resolve_markup_pct`), same free-inheritance reasoning applies to region.
- `apps/gateway/src/gateway/tenants/infrastructure/rate_card_orm.py:TenantRateCardEntry` + `apps/gateway/src/gateway/tenants/api/rate_card_router.py` — the exact per-(tenant,X) override table + admin CRUD precedent this task's storage/API mirrors (PUT idempotent upsert, GET list, DELETE always-204, `Permission.RATE_CARDS_MANAGE` OWNER-only via `tenants/domain/authz.py:Permission`).
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow.markup_pct` (`Numeric(7,4)`, server_default "20.0") — the flat-fallback precedent's column style.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py:ModelRow` (`__tablename__ = "models"`, `id` = text PK) — where `region-catalog-dimension` will add its `region` column (NOT YET PRESENT in this tree — confirmed absent via `search_for_pattern "region"` over `apps/gateway/src/gateway/catalog`, zero hits).

Context (working folder): sibling task `.add/tasks/region-catalog-dimension/TASK.md` is still template-empty (phase: ground) as of this ground — its §0-§3 have not been drafted in this tree yet. `.add/milestones/residency-service-tiers/MILESTONE.md` binding rule #1 says region lives on "the deployment/catalog row"; given `models.id` is already the catalog's per-provider-entry primary key (provider-seam TASK.md added a `provider` column distinguishing e.g. openrouter/bedrock/vertex rows), the natural additive home is `models.region` (a new column, NOT a new table) — but this is an inference, not a frozen fact (§1 ⚠).

Honors (patterns / conventions): CONVENTIONS.md "additive migrations, rollback via Alembic downgrade" (line ~22); `Mapped[str]` → `sa.String()` for new plain-str columns (folded lesson, NOT `sa.Text()`, which silently breaks migration-parity tests); a new table trips the `EXPECTED_TABLES` manifest in `apps/gateway/tests/migrations/test_migrations.py` (SANCTIONED-EDIT, add with a disposition comment, same as every prior additive table).

Anchors the contract cites: `rate_card_resolver.resolve_markup_pct` (untouched, cited) · new `rate_card_resolver.resolve_region_multiplier` · `recorder.py::RecordingUsageRecorder.record` (extension) · `cost_recovery.py::OpenRouterCostRecovery` (extension) · `catalog/infrastructure/repository.py::CatalogRepository.list_active_models_with_markup` (extension) · `tenants/domain/authz.py:Permission.RATE_CARDS_MANAGE` (reused) · `tenants/infrastructure/rate_card_orm.py:TenantRateCardEntry` (storage-shape precedent) · `billing/application/invoice_generator.py` (cited as proof-of-zero-touch, not edited).

Issues/Risks (→ feed §1):
1. **Disconnect-provider-cost drift trap** (recorder.py ~424-432): today it back-derives `provider_cost = cost_usd / (1 + markup_pct/100)`. If this task multiplies `region_multiplier` into `cost_usd` BEFORE that block runs (the natural placement, right after the pricing if/else), the back-derivation must also divide by `region_multiplier` — otherwise a disconnect-estimate on an EU-region model silently inflates the recorded `provider_cost` by the region factor, corrupting the drift-monitor's unbilled-upstream-cost signal (GLOSSARY `unbilled_upstream_cost`). Found by tracing the FULL recorder.py flow, not just the markup-application branches — a real found-in-grounding trap, not a hypothetical.
2. **Forward dependency on region-catalog-dimension's unfrozen shape** — `models.region`'s exact column name/type/default is not yet frozen in this tree; this task's resolver reads it but cannot itself decide its shape (milestone rule #1: that task owns it).
3. Region multiplier and tenant markup are DIFFERENT units in this codebase's existing convention: `markup_pct` is a percentage (20 = +20%), but the milestone frames region as a MULTIPLIER directly ("1.1×"). Storing both the same way would be simpler to reason about but the milestone's own DECIDED language is multiplicative for region and percentage-additive for the coming tier markup ("+25%") — an intentional asymmetry, not an oversight, but worth flagging so service-tiers doesn't have to guess (see §1 assumption #3).
4. `PricingSnapshotRow`/`ModelRow` carry no per-region PRICE variance today — the region multiplier is a MARGIN premium on top of the (region-independent) catalog price, not a per-region catalog price table; confirmed by the milestone's own framing ("region multiplier on rate cards", not "region-specific pricing snapshots").

Related intent: PROJECT.md/GLOSSARY.md `Cost`/`Markup` definitions (GLOSSARY.md:11-12) — this task adds a GLOSSARY delta layering "region multiplier" onto the existing `Cost = upstream × (1 + markup)` formula. Originating rationale: MILESTONE.md residency-service-tiers — "selling what Anthropic verifiably lacks... US-pin monetized at 1.1x"; M1 monetization-core's binding rule (carried forward, MILESTONE.md shared decision #3) "ONE rate-card resolver ... no second pricing path."

Ground SHA: c3f972d

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Region multiplier on rate cards via the shared resolver
Framings weighed:
  (chosen) Additive SIBLING function `resolve_region_multiplier` in the SAME `rate_card_resolver.py` module, composed at each of the 3 existing call sites (recorder, cost_recovery, catalog repository) as a second, independent multiplication — `resolve_markup_pct` stays byte-untouched.
  · Fold region into `resolve_markup_pct` itself (return one combined "effective pct") — rejected: would force editing a FROZEN v1 contract's return semantics and risk the ~8 markup-mocking regression suites that assert its exact SQL/shape.
  · A wholly separate "region pricing service" outside `rate_card_resolver.py` — rejected: violates MILESTONE.md binding rule #3 ("ONE rate-card resolver... no second pricing path"); the whole point is ONE module owning every rate-card factor.

Must:
<must>
  - M1: `resolve_region_multiplier(session, tenant_id, model_id) -> Decimal` is added to `rate_card_resolver.py` as the SINGLE resolution point for the region factor: a tenant override in `tenant_region_multiplier_overrides` (tenant_id, region) wins; ELSE the DECIDED seed keyed by the model's region (`models.region`, cited from region-catalog-dimension, not redefined here): eu→1.1, us|global→1.0, any NULL/unrecognized region→1.0 (pricing never blocks; only residency-policy fail-closes).
  - M2: The region multiplier is applied to a chargeable request EXACTLY ONCE, at `recorder.py::RecordingUsageRecorder.record` — resolved once alongside `markup_pct` (same session, same batch, no N+1), then ONE new statement `cost_usd = cost_usd * region_multiplier` immediately after the existing pricing/markup if-else block and BEFORE the disconnect-provider-cost block — covers the provider_cost branch, the per-token branch, the non-token branch, and is a harmless no-op on the cached-hit $0 branch (region_multiplier is not even fetched when `cached=True`, mirroring the existing `if not cached:` guard around markup_pct's own fetch).
  - M3: The disconnect-provider-cost back-derivation (recorder.py ~424-432) is corrected to divide `region_multiplier` back out too — `provider_cost = cost_usd / ((1+markup_pct/100) * region_multiplier)` — else a disconnect estimate on an EU-region model silently inflates the recorded `provider_cost` by the region factor (the drift trap found in §0 grounding).
  - M4: `cost_recovery.py`'s recovery-target computation (~161-162) is extended to multiply by the SAME `region_multiplier` (resolved via `resolve_region_multiplier`, same pattern as its existing `_fetch_markup` delegation) — a recovered/corrected row bills at the identical effective rate a fresh `record()` call against the same (tenant, model) would produce.
  - M5: `catalog/infrastructure/repository.py::list_active_models_with_markup` extends its single bulk `multiplier` expression (~153) to also multiply in the region factor via a LEFT JOIN + COALESCE bulk-equivalent of `resolve_region_multiplier` — every priced catalog field (prompt/completion/cached/audio_*) picks up the SAME multiplier with zero per-field changes, exactly mirroring how the existing markup multiplier already fans out to every field.
  - M6: Invoice generation (`invoice_generator.py`) and the margin dashboard (`margin_router.py`) require ZERO code changes — both already sum/aggregate `usage_records.cost_usd` without recomputing price, so the region multiplier flows through for free the instant M2 lands; this is the "provably identical through the one resolver" guarantee the milestone's exit criteria requires.
  - M7: `PUT /admin/region-pricing/{region}` (idempotent upsert), `GET /admin/region-pricing` (list), `DELETE /admin/region-pricing/{region}` (idempotent, always 204) — OWNER-only via `Permission.RATE_CARDS_MANAGE` (reused, §1 assumption #2), scoped to the caller's own tenant only (no cross-tenant surface, mirrors `rate_card_router.py` exactly).
  - M8: All money math stays Decimal end-to-end; `region_multiplier` is composed in Decimal before any float cast — the ONE existing float cast in the catalog bulk path (`float(Decimal(...))`, repository.py:153) is an accepted PRE-EXISTING precedent this task extends, not a new float-math site.
  - M9: A named, additive extension point is reserved (NOT implemented by this task): `resolve_tier_multiplier(session, tenant_id, model_id, tier) -> Decimal` in the SAME `rate_card_resolver.py` module. When service-tiers lands, each of the 3 extension sites' single multiplication line becomes `cost_usd = cost_usd * region_multiplier * tier_multiplier` — same batch-resolution pattern, same 3 sites, no 4th call site invented.
</must>
Reject:
<reject>
  - R1: negative or non-numeric `multiplier` in the PUT body -> "422 problem+json" (mirrors `rate_card_router.py`'s `markup_pct` validation: `Field(gt=0, max_digits=6, decimal_places=4)`)
  - R2: non-OWNER caller on any `/admin/region-pricing/*` route -> "ERR_AUTH_FORBIDDEN" (403)
  - R3: duplicate (tenant, region) PUT -> NOT an error — idempotent UPSERT (same row updated, mirrors RC "Duplicate (tenant, model) create is an idempotent upsert")
  - R4: DELETE on an already-absent (tenant, region) override -> NOT 404 — 204 always (mirrors `rate_card_router.py` precedent)
</reject>
After:
<after>
  - Every (tenant, model) priced quantity reflects tenant markup × region multiplier (× a future tier multiplier), composed by exactly ONE function, extended in place across its lifetime rather than re-derived per feature.
  - Catalog display price, `usage_records.cost_usd`, and every derived invoice line for an EU-region model show the SAME region-inflated figure — provably, because invoice generation and the margin dashboard sum `cost_usd` without ever recomputing it.
  - A tenant can override its own per-region multiplier via the admin API; an ABSENT override falls back to the DECIDED seed (eu=1.1×, us/global=1.0×).
  - The disconnect-provider-cost back-derivation no longer double-counts nor omits the region factor when reconstructing `provider_cost`.
  - `resolve_markup_pct` (frozen v1) is byte-untouched; every pre-existing markup-only regression suite (the ~8 that mock/assert on it) stays green unmodified, because every model defaults to region multiplier 1.0 until region-catalog-dimension actually tags a row 'eu'.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 `models.region`'s exact column name/type/enum/default is OWNED by region-catalog-dimension and NOT YET FROZEN in this tree (its own TASK.md is still template-empty as of this ground). This task's resolver assumes a `region: str` column on `models` with values in {us, eu, global} and a safe default for every pre-existing row. Lowest confidence because it is a genuine sibling dependency this task cannot itself resolve. If wrong (different column name, different table, different enum): `resolve_region_multiplier`'s single query needs a one-line adjustment — cheap if caught before THIS task's own contract freezes (recommend freezing region-catalog-dimension's §3 first, or freezing this contract with an explicit "pending region-catalog-dimension" caveat carried into Build); a change-request back to SPECIFY if caught after this task ships.
  - [ ] #2 Reusing `Permission.RATE_CARDS_MANAGE` (rather than minting a new `REGION_PRICING_MANAGE`) for the region-pricing admin routes — same OWNER-only margin-control domain as rate cards. Confirm at freeze; cheap to rename before any caller depends on it.
  - [ ] #3 Storing the tenant override as a raw multiplier (`Numeric(6,4)`, e.g. 1.1000) rather than a "_pct premium" like `markup_pct` — chosen because the milestone's own DECIDED language for region IS multiplicative ("1.1×"), while the milestone's tier-markup language is additive-percentage ("+25%"); the two units are DELIBERATELY different, composing multiplicatively at the top: `(1+markup_pct/100) × region_multiplier × (1+tier_pct/100)`. Confirm this doesn't read as inconsistent going into service-tiers' freeze.
  - [ ] #4 No format/enum validation on the `region` path segment at PUT time (any string accepted, mirrors `tenant_rate_card_entries`' no-FK-on-model_id precedent: an unrecognized region silently resolves to the safe 1.0 default rather than erroring). A mistyped region override is a SILENT no-op, not a loud rejection — medium cost if wrong (confusing support case), tracked as a SPEC delta for validation once region-catalog-dimension's enum is frozen, not blocking this freeze.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: EU-region request bills the seeded 1.1x premium on top of tenant markup   # M1, M2
  Given a catalog model with region='eu', tenant markup_pct=20, and no tenant region override
  When the tenant makes a chargeable proxy request against that model
  Then usage_records.cost_usd == upstream_cost × 1.20 × 1.10 (Decimal-exact, no float rounding)
  And region_multiplier was resolved through resolve_region_multiplier exactly once for the request

Scenario: US/global-region request stays byte-identical to before this task   # M1, M2 (regression)
  Given a catalog model with region='us' (or 'global'), tenant markup_pct=20
  When the tenant makes a chargeable proxy request
  Then usage_records.cost_usd == upstream_cost × 1.20 (region_multiplier resolves to 1.0)
  And every pre-existing markup-only regression suite's asserted cost_usd is unchanged

Scenario: Tenant overrides the EU multiplier   # M1, M7
  Given a tenant PUTs {multiplier: 1.05} to /admin/region-pricing/eu
  When that tenant's key is billed against an eu-region model
  Then cost_usd reflects the tenant's 1.05x override, not the 1.10x seed
  And every OTHER tenant with no override still resolves eu at the 1.10x seed, unchanged

Scenario: Catalog display matches billed price for an EU model — the drift scenario   # M5, milestone exit criterion
  Given a tenant with no region override and an eu-region model in the catalog
  When the tenant calls GET /v1/models
  Then the displayed prompt_per_token / completion_per_token already reflect the 1.10x region premium
  And a chargeable request against that SAME model bills the IDENTICAL effective multiplier — zero drift between catalog display and usage_records.cost_usd

Scenario: Invoice line for an EU tenant carries the region premium with zero drift   # M6, milestone exit criterion
  Given an EU-pinned tenant accrued usage_records over a billing period, cost_usd already region-inflated
  When invoice generation sums those records into invoice lines
  Then invoice_lines.amount_usd reflects the SAME region-inflated total
  And invoice_generator.py's code is unchanged — it never calls resolve_region_multiplier or resolve_markup_pct

Scenario: Disconnect-estimate on an EU-region model does not double- or under-count the region factor   # M3, the drift trap found in grounding
  Given an eu-region model and a non-recoverable client disconnect with a positive partial cost estimate
  When recorder.py's disconnect-provider-cost block backs provider_cost out of cost_usd
  Then provider_cost divides out BOTH markup_pct AND region_multiplier, landing at the true upstream estimate
  And cost_usd is zeroed exactly as it was before this task (unchanged disconnect behavior otherwise)

Scenario: OpenRouter cost-recovery on an EU-region model bills the same effective rate as the original record   # M4
  Given an EU-pinned tenant's request was under-billed pending upstream settlement
  When cost_recovery.py computes the recovery delta
  Then target == settled_upstream_cost × (1+markup_pct/100) × region_multiplier — the identical multiplier record() would have applied
  And the recorded correction row's implied rate matches a fresh record() call against the same (tenant, model)

Scenario: Invalid multiplier value is rejected   # R1
  Given a PUT /admin/region-pricing/eu with body {multiplier: -1} or {multiplier: "abc"}, called by an OWNER
  When the request is made
  Then 422 problem+json is returned
  And no override row is written for that (tenant, region)

Scenario: Non-owner cannot manage region pricing   # R2
  Given a MEMBER-role caller
  When they PUT /admin/region-pricing/eu
  Then 403 ERR_AUTH_FORBIDDEN is returned
  And no override row is written

Scenario: Duplicate region override PUT is an idempotent upsert   # R3
  Given an existing override for (tenant, eu) at 1.05
  When the same tenant PUTs {multiplier: 1.08} to /admin/region-pricing/eu again
  Then the SAME row is UPDATEd to 1.08, not duplicated
  And exactly one row exists for (tenant, eu) afterward

Scenario: Deleting an absent region override is a no-op success   # R4
  Given no override exists for (tenant, us)
  When the tenant DELETEs /admin/region-pricing/us
  Then 204 is returned
  And no row is created or changed, no error surfaced

Scenario: Unrecognized or NULL region resolves to the safe default, never blocks a request   # edge case (pricing is fail-open, not fail-closed)
  Given a model with a NULL or unrecognized region value (a transitional row before region-catalog-dimension backfills, or a genuine typo)
  When a request against that model is billed
  Then region_multiplier resolves to 1.0 — NEVER a 4xx, NEVER a refusal (residency-policy fail-closes; pricing never does)
  And the request completes exactly as it would have before this task shipped

Scenario: Cached hit is unaffected by the region multiplier and costs zero extra queries   # edge case
  Given a cache_hit=true response for an eu-region model
  When the request is recorded
  Then cost_usd stays 0 (region_multiplier × 0 == 0, or the fetch is skipped entirely on the cached branch)
  And no additional region-pricing DB round trip is made on the cached=True path, mirroring the existing markup_pct skip

Scenario: Concurrent PUTs to the same (tenant, region) override resolve without a race   # edge case, mirrors RC7
  Given two concurrent PUT /admin/region-pricing/eu requests from the same tenant with different multiplier values
  When both requests execute against the ON CONFLICT (tenant_id, region) DO UPDATE upsert
  Then exactly one row exists for (tenant, eu) afterward, holding whichever value's transaction committed last
  And neither request errors or produces a duplicate row
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PUT    /admin/region-pricing/{region}   body: { multiplier: number }
  200 -> { region: string, multiplier: string }
  403 -> problem+json "ERR_AUTH_FORBIDDEN"        (caller lacks RATE_CARDS_MANAGE)
  422 -> problem+json                              (negative | non-numeric | exceeds Numeric(6,4))

GET    /admin/region-pricing            -> { entries: [{ region: string, multiplier: string }] }   ([] when none)

DELETE /admin/region-pricing/{region}   -> 204 always (idempotent — already-absent is success, NEVER 404)

Every route requires Permission.RATE_CARDS_MANAGE (OWNER-only, reused — §1 assumption #2) and acts
on the CALLER'S OWN tenant only — no cross-tenant surface (mirrors rate_card_router.py exactly).
```

Schema (additive only):
```
NEW TABLE tenant_region_multiplier_overrides   (mirrors tenant_rate_card_entries exactly)
  id          uuid7 PK
  tenant_id   FK tenants.id ON DELETE CASCADE, NOT NULL
  region      Text, NOT NULL            (no FK/enum check — §1 assumption #4; forward-cites region-catalog-dimension's eventual enum)
  multiplier  Numeric(6,4), NOT NULL, CHECK (multiplier > 0)
  created_at, updated_at
  UNIQUE (tenant_id, region)            — the ON CONFLICT upsert target
  INDEX  (tenant_id)

Reads only, never writes (forward-cited, owned by region-catalog-dimension, NOT redefined here):
  models.region   TEXT, values {us, eu, global}   — §1 ⚠ assumption #1, not yet frozen
```

New/extended symbols:
```
gateway.usage.application.rate_card_resolver
  resolve_markup_pct(...)                          — FROZEN v1, UNTOUCHED (cited, not edited)
  + resolve_region_multiplier(session, tenant_id, model_id) -> Decimal      [NEW]
      1. SELECT multiplier FROM tenant_region_multiplier_overrides WHERE tenant_id=:t AND region=(SELECT region FROM models WHERE id=:m)
      2. ELSE the DECIDED seed: {"eu": Decimal("1.1")}.get(region, Decimal("1.0"))  — us/global/NULL/unrecognized all -> 1.0
  + resolve_tier_multiplier(session, tenant_id, model_id, tier) -> Decimal  [RESERVED — signature only, NOT implemented by this task; service-tiers' own contract fills the body]

apps/gateway/src/gateway/usage/application/recorder.py :: RecordingUsageRecorder.record
  [EXTEND] resolve region_multiplier once, alongside markup_pct (~line 282)
  [EXTEND] ONE new line: cost_usd = cost_usd * region_multiplier   — immediately after the pricing if/else block, BEFORE the disconnect-provider-cost block (~line 415)
  [EXTEND] disconnect-provider-cost back-derivation (~line 430): divide by region_multiplier too —
           provider_cost = cost_usd / ((Decimal("1") + markup_pct/100) * region_multiplier)

apps/gateway/src/gateway/usage/application/cost_recovery.py :: OpenRouterCostRecovery
  [EXTEND] resolve region_multiplier via resolve_region_multiplier (same pattern as _fetch_markup)
  [EXTEND] target = cost.total_cost * (Decimal("1") + markup / Decimal("100")) * region_multiplier   (~line 162)

apps/gateway/src/gateway/catalog/infrastructure/repository.py :: CatalogRepository.list_active_models_with_markup
  [EXTEND] LEFT JOIN tenant_region_multiplier_overrides + ModelRow.region; bulk-COALESCE equivalent of resolve_region_multiplier
  [EXTEND] multiplier = float(((Decimal("1") + row.markup_pct/100)) * effective_region_multiplier)   (~line 153) — every downstream field (159-186) unchanged, inherits automatically

apps/gateway/src/gateway/tenants/api/region_pricing_router.py   [NEW — mirrors rate_card_router.py]
apps/gateway/src/gateway/tenants/infrastructure/region_pricing_orm.py   [NEW — mirrors rate_card_orm.py]

apps/gateway/src/gateway/billing/application/invoice_generator.py       [ZERO CHANGES — cited as proof]
apps/gateway/src/gateway/usage/api/margin_router.py                     [ZERO CHANGES — cited as proof]
```

Glossary deltas:
- **Region multiplier**: a per-region price multiplier composed with tenant markup at the ONE shared rate-card resolver (`resolve_region_multiplier`) — DECIDED seed eu=1.1×, us/global=1.0×, tenant-overridable via `tenant_region_multiplier_overrides`; applied exactly once, at usage-record time (`recorder.py`), never recomputed downstream — invoice lines and the margin dashboard inherit it for free via `usage_records.cost_usd`.
- **tenant_region_multiplier_overrides**: the per-(tenant, region) override table; an ABSENT row means "fall back to the DECIDED seed" — the same override-wins-else-fallback shape as `tenant_rate_card_entries` (GLOSSARY `Markup`), keyed by region instead of model. [folded foundation-version 52]

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

Least-sure flag surfaced at freeze: ⚠ [spec] §1 assumption #1 — `models.region`'s column name/type/enum is owned by the sibling task region-catalog-dimension and is NOT YET FROZEN anywhere in this tree (its TASK.md is still template-empty). This contract's `resolve_region_multiplier` and the catalog-repository JOIN both read `models.region` as a forward citation. Low cost if region-catalog-dimension freezes the assumed shape (str column, {us,eu,global} values, safe default) before this task reaches Build — a non-event. Moderate cost if it freezes a materially different shape (e.g. a separate `deployments` table, or region living per-PricingSnapshot rather than per-model) — this contract would need a change-request back to SPECIFY for the one query that reads it. Recommend: freeze region-catalog-dimension's §3 before (or in the same freeze session as) this task's own freeze, or carry this caveat explicitly into the human's freeze decision.

DECIDED at freeze review (2026-07-12, Tin): (1) admin routes gated by existing
`Permission.RATE_CARDS_MANAGE` (region multipliers ARE rate-card entries). (2) Storage = raw
multiplier `Numeric(6,4)` (matches the 1.1x DECIDED anchor; deliberate asymmetry with tier's
_pct framing accepted). (3) PUT VALIDATES the region string against region-catalog-dimension's
frozen us|eu|global Literal — 422 on unknown region (deviates from the no-FK precedent
deliberately: a silent no-op on a money knob is a foot-gun).
(4) [Tin directive 2026-07-12] Region set is us|eu|ap|global (Asia added; Vietnam served via ap —
no vn hyperscaler region exists). Multiplier seeds: eu=1.1x (DECIDED), us/ap/global=1.0x,
all tenant-overridable. Validation set for (3) is the four-value Literal.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (matches project convention for money-path modules)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_eu_region_bills_seeded_premium: arrange eu-region model + markup_pct=20, no override / act chargeable request / assert cost_usd == upstream×1.20×1.10 (Decimal) · covers: M1,M2
  - test_us_global_region_byte_identical: arrange us/global-region model / act chargeable request / assert cost_usd == upstream×1.20 (unchanged) · covers: M1,M2
  - test_tenant_region_override_wins: arrange tenant PUT override 1.05 for eu / act billed request / assert 1.05x applied, other tenants still see 1.10x · covers: M1,M7
  - test_catalog_display_matches_billed_price: arrange eu-region model, no override / act GET /v1/models then a billed request / assert identical effective multiplier in both · covers: M5
  - test_invoice_line_zero_drift: arrange region-inflated usage_records over a period / act invoice generation / assert invoice_lines.amount_usd sums the region-inflated cost_usd, monkeypatch resolve_region_multiplier to raise and assert never called by invoice_generator · covers: M6
  - test_disconnect_estimate_divides_out_region: arrange eu-region model + non-recoverable disconnect with positive estimate / act record() / assert provider_cost excludes BOTH markup and region, cost_usd zeroed · covers: M3
  - test_cost_recovery_matches_original_rate: arrange eu-pinned tenant, under-billed pending settlement / act cost_recovery target computation / assert target uses markup × region identical to record() · covers: M4
  - test_invalid_multiplier_422: arrange OWNER caller / act PUT {multiplier:-1} and {multiplier:"abc"} / assert 422, no row written · covers: R1
  - test_non_owner_403: arrange MEMBER caller / act PUT /admin/region-pricing/eu / assert 403 ERR_AUTH_FORBIDDEN, no row written · covers: R2
  - test_duplicate_put_idempotent_upsert: arrange existing override 1.05 / act PUT 1.08 again / assert single row updated to 1.08 · covers: R3
  - test_delete_absent_override_204: arrange no override exists / act DELETE /admin/region-pricing/us / assert 204, no row change · covers: R4
  - test_unrecognized_region_defaults_safe: arrange NULL/unknown model region / act billed request / assert region_multiplier==1.0, request completes (no 4xx) · covers: edge case
  - test_cached_hit_unaffected_no_extra_query: arrange cache_hit=true on eu-region model / act record() / assert cost_usd==0, no region-pricing query issued on cached branch · covers: edge case
  - test_concurrent_put_no_race: arrange two concurrent PUTs same (tenant, eu) / act both execute / assert exactly one row, last-committed value wins, no error · covers: edge case (mirrors RC7)
</test_plan>

Tests live in: `apps/gateway/tests/region_pricing/` (15 tests incl. one 2-way
parametrization, 1 file + suite-local conftest.py) · ran RED for the right
reason before Build (committed as its own commit, `87bc6e5`, before any
implementation edit — no `git stash`): 10/15 failed with `UndefinedTable`/404
(missing `tenant_region_multiplier_overrides` + unmounted `/admin/
region-pricing`) or a Decimal cost mismatch / `AttributeError` on the
not-yet-existing `resolve_region_multiplier` — never a harness error. The
remaining 5 (us/global byte-identical x2, disconnect back-derivation,
cached-hit no-extra-query, unrecognized-region fail-open) are honest
pre-build passes — each docstring states why the scenario cannot be expressed
as RED (mirrors `tests/tiered_rate_cards`'s existing `test_no_entry_falls_
back_byte_identical` precedent) — MUST run red (missing implementation)
before Build — CONFIRMED.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/src/gateway/usage/application/rate_card_resolver.py`
`apps/gateway/src/gateway/usage/application/recorder.py`
`apps/gateway/src/gateway/usage/application/cost_recovery.py`
`apps/gateway/src/gateway/catalog/infrastructure/repository.py`
`apps/gateway/src/gateway/tenants/infrastructure/region_pricing_orm.py`
`apps/gateway/src/gateway/tenants/api/region_pricing_router.py`
`apps/gateway/src/gateway/tenants/domain/authz.py`
`apps/gateway/migrations/versions/`
`apps/gateway/tests/migrations/test_migrations.py`
`apps/gateway/src/gateway/main.py`
`./tests/`

Strategy (ordered batches):
1. Migration: new `tenant_region_multiplier_overrides` table (mirrors `tenant_rate_card_entries`'s Alembic migration file structure) chained to the ACTUAL current `alembic heads` (verify at Build time, not assumed from this draft — folded lesson: a stale down_revision creates a second head). Add the table to `EXPECTED_TABLES` with a disposition comment (SANCTIONED EDIT).
2. `rate_card_resolver.py`: add `resolve_region_multiplier` + the reserved `resolve_tier_multiplier` signature stub (raises `NotImplementedError` with a comment pointing at service-tiers, so an accidental early call fails loud, not silently returns 1.0). Do NOT touch `resolve_markup_pct`.
3. `recorder.py`: extend the Resolve-pricing-and-markup block to also resolve `region_multiplier`; add the single `cost_usd = cost_usd * region_multiplier` line; fix the disconnect-provider-cost back-derivation to divide by it too. Red tests for M2/M3 first.
4. `cost_recovery.py`: extend `_fetch_markup`-adjacent call site to also resolve+multiply `region_multiplier` into `target`.
5. `catalog/infrastructure/repository.py`: extend the bulk join with the region LEFT JOIN + COALESCE; fold into the single `multiplier` scalar.
6. `region_pricing_orm.py` + `region_pricing_router.py`: mirror `rate_card_orm.py`/`rate_card_router.py` file-for-file (PUT/GET/DELETE), mount in `main.py` alongside `rate_card_router`.
7. Regression pass: run the existing markup-only suites (pricing_units, tiered_token_billing, tiered_rate_cards, catalog, usage_metering, provider_cost_reconciliation, openrouter_cost_recovery) unmodified — every one must stay green byte-identical (region defaults to 1.0 for every model with no 'eu' tag).

Persona (required): billing-precision-engineer (`.add/personas/billing-precision-engineer.md`) — closest domain-content match (Decimal-only cost math, provenance-stamped rows, "no second pricing path," reconciliation-as-detective-work); note its frontmatter declares `flow: build, advisor`, not `design` — no project persona currently declares `flow: design` for the billing domain, so this design draft borrows its Critical Rules as the governing lens rather than a flow-matched persona. Flag this gap as a candidate follow-up for add-persona.
Spawn isolation (default): worktree — this task's migration + shared-module edits (`rate_card_resolver.py`, `recorder.py`) overlap the file surface region-catalog-dimension and service-tiers will also touch; a non-worktree shared-tree build risks the documented scope-snapshot poisoning gotcha across concurrent sibling builds.
Known-problem fixes:
  - trap: disconnect-provider-cost back-derivation double-counting/omitting region → fix: single formula divides out both factors (M3, see §3).
  - trap: `Mapped[str]` migrated as `sa.Text()` instead of `sa.String()` silently breaks migration-parity tests (folded lesson) → fix: use `sa.String()`/`Text` consistent with `TenantRateCardEntry.region`... actually `region` column mirrors `TenantRateCardEntry.model_id` which uses `Text` deliberately (unbounded); keep `Text` for `region` too, only the folded `sa.String()` lesson applies to bounded/enum-like short strings elsewhere — confirm against `rate_card_orm.py`'s own `model_id: Mapped[str] = mapped_column(Text, ...)` precedent at Build time.
  - trap: stale `down_revision` creates a second alembic head → fix: `alembic heads` checked fresh at Build time, not copied from this draft's migration list.
  - trap: shared test Postgres cross-worktree table drops → fix: unique `GATEWAY_TEST_DATABASE_URL` per build session (existing project convention).
Strategy actually used: as planned, with one deviation from the draft's illustrative
§3 SQL: `resolve_region_multiplier` runs the CONTRACT's literal override query first
(`... WHERE tenant_id=:t AND region=(SELECT region FROM models WHERE id=:m)`) and
only issues a SECOND `SELECT region FROM models` query on a miss (to key the
DECIDED seed) — cheaper than a LEFT JOIN in the common no-override case, still
O(1) per request (no N+1), and closer to the frozen SQL text than a hand-rolled
join would have been. All 8 batches (migration -> resolver -> recorder -> cost_recovery
-> catalog repository -> ORM+router -> main.py wiring -> regression sweep) executed
in order; no contract friction — region-catalog-dimension's `models.region` (§1 ⚠
assumption #1) was already integrated in this worktree exactly as assumed (str
column, us|eu|ap|global, default 'global'), so the forward dependency resolved
cleanly with zero adjustment.
Safety rule (feature-specific): the region-multiplier resolution + its multiplication into `cost_usd` happen inside the SAME `record()` call/transaction boundary as the existing markup resolution — no separate async task, no eventual-consistency window between "markup applied" and "region applied" (mirrors the existing single-pass cost computation, extends it rather than adding a second pass).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; do NOT edit `resolve_markup_pct`, `compute_per_token_cost_usd`'s frozen signature, or `invoice_generator.py`/`margin_router.py` (their zero-touch is the M6 proof); allow-list packages only; ask if unclear.

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
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] a chargeable request against an eu-region model with no override bills
  upstream x 1.20 (markup) x 1.10 (region) as an exact Decimal — confirmed by
  `test_eu_region_bills_seeded_premium` (unit) + `test_catalog_display_matches_
  billed_price` (DB, catalog<->billing zero-drift)
- [ ] a us/global-region request bills byte-identical to pre-task (upstream x
  1.20 only) — confirmed by `test_us_global_region_byte_identical[us|global]`
- [ ] a tenant's own PUT /admin/region-pricing/eu override wins over the seed,
  and does NOT leak to a different tenant — confirmed by
  `test_tenant_region_override_wins`
- [ ] catalog GET /v1/models price and a fresh billed request resolve the
  IDENTICAL effective multiplier for the same eu-region model — confirmed by
  `test_catalog_display_matches_billed_price`'s cross-check assertion
- [ ] invoice_generator.py sums the already-region-inflated cost_usd verbatim
  and never calls resolve_region_multiplier — confirmed by
  `test_invoice_line_zero_drift` (monkeypatch-to-raise guard)
- [ ] a disconnect-estimate on an eu-region model divides OUT both markup and
  region when back-deriving provider_cost (no drift-monitor over/under-count)
  — confirmed by `test_disconnect_estimate_divides_out_region`
- [ ] OpenRouter cost-recovery on an eu-region model bills the identical
  effective rate a fresh record() call would — confirmed by
  `test_cost_recovery_matches_original_rate`
- [ ] PUT/GET/DELETE /admin/region-pricing enforce RATE_CARDS_MANAGE (403 for
  non-OWNER), reject negative/non-numeric/unknown-region (422), and are
  idempotent (duplicate PUT upserts, DELETE-absent is 204, concurrent PUTs
  never duplicate a row) — confirmed by `test_invalid_multiplier_422`,
  `test_non_owner_403`, `test_duplicate_put_idempotent_upsert`,
  `test_delete_absent_override_204`, `test_concurrent_put_no_race`
- [ ] an unrecognized/NULL model region NEVER blocks a request (fail-open) —
  confirmed by `test_unrecognized_region_defaults_safe`
- [ ] a cache_hit=true request issues ZERO region-pricing DB round trips —
  confirmed by `test_cached_hit_unaffected_no_extra_query`'s `session.executed
  == []` assertion
- [ ] `resolve_markup_pct` stays byte-untouched and every pre-existing
  markup-only regression suite is unmodified and green — confirmed by
  `git diff` showing zero edits to `resolve_markup_pct`'s body + green
  `tiered_rate_cards`/`pricing_units`/`tiered_token_billing`/
  `provider_cost_reconciliation` suites (45+49 passed, this session)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-12

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned, with one deviation from the draft's illustrative §3 SQL: `resolve_region_multiplier` runs the CONTRACT's literal override query first (`... WHERE tenant_id=:t AND region=(SELECT region FROM models WHERE id=:m)`) and only issues a SECOND `SELECT region FROM models` query on a miss (to key the DECIDED seed) — cheaper than a LEFT JOIN in the common no-override case, still O(1) per request (no N+1), and closer to the frozen SQL text than a hand-rolled join would have been. All 8 batches (migration -> resolver -> recorder -> cost_recovery -> catalog repository -> ORM+router -> main.py wiring -> regression sweep) executed in order; no contract friction — region-catalog-dimension's `models.region` (§1 ⚠ assumption #1) was already integrated in this worktree exactly as assumed (str column, us|eu|ap|global, default 'global'), so the forward dependency resolved cleanly with zero adjustment.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

