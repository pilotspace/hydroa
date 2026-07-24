# PLAN: Register fine-tuned model in tenant catalog + pricing snapshot

slug: finetune-model-registry · created: 2026-07-24 · stage: production
milestone: managed-rag-finetune
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: finetune-model-registry — when a brokered fine-tune job SUCCEEDS, auto-register the resulting `ft:*` model as a tenant-owned catalog ModelRow with a pricing snapshot, so it is callable via the normal proxy path and billed via the ONE shared rate-card resolver.
Framings weighed: listener-into-shared-catalog (chosen — the ft model becomes a first-class ModelRow, so EVERY existing surface — model_checker gate, /v1/models listing, recorder snapshot lookup, resolve_markup_pct — works with zero new billing/routing mechanism; only a tenant-ownership dimension is added) · separate tenant_models table (rejected: every consumer — checker, listing, recorder, resolver — would need a second lookup path = a new mechanism, violating the milestone's "no new billing mechanism") · synchronous registration inside the broker CAS (rejected: the broker §3 is FROZEN — D4 explicitly decouples the listener from the CAS).
Must:
<must>
  - M1 wire a real registrar at the FROZEN extension point `app.state.finetune_completion_listener` (broker code untouched, no re-freeze); a job's winning CAS to "succeeded" registers `fine_tuned_model` as a models row: id=name=`ft:*`, tenant_id=job.tenant_id, provider=job.provider, modality="chat", active=true, region/context_length copied from the base row.
  - M2 registration inserts ONE pricing_snapshots row for the ft model: prompt/completion copied from the BASE model's LATEST snapshot (provider-passthrough basis), pricing_unit="per_token" — exact Decimal, never float.
  - M3 the ft model bills through the EXISTING shared path only: recorder's latest-snapshot lookup + `resolve_markup_pct` (tenant markup) — no new billing mechanism, no new resolver.
  - M4 tenant scoping: a models row with tenant_id set is visible ONLY to its owner — `check_for_tenant` returns UNKNOWN (the model_not_found 404 path) for every other tenant; `/v1/models` listings exclude foreign tenant-owned rows. Existing rows (tenant_id NULL) behave byte-identically.
  - M5 exactly-once: registration is idempotent on the ft model id (`INSERT .. ON CONFLICT (id) DO NOTHING`; snapshot only when the model insert won) — a double-fired listener leaves exactly ONE row + ONE snapshot.
  - M6 partial-failure repair: a listener failure never rolls back the broker CAS (D4, frozen); a `repair_missed()` sweep (periodic task, catalog refresh_scheduler precedent; DB-only, no outbound IO) scans succeeded jobs with fine_tuned_model set but no models row and registers them idempotently.
  - M7 `sync_catalog`'s provider-scoped deactivation sweep excludes tenant-owned rows (`tenant_id IS NULL` ANDed into both sweep WHEREs) — an upstream sync never deactivates a registered ft model.
  - M8 (advisor finding) `DbTenantModelPresetStore.upsert`'s target validation becomes tenant-scoped (`check_for_tenant`, not the tenant-blind frozen `is_active`): the owner may target its own ft model; a foreign tenant gets the byte-identical `ERR_PRESET_TARGET_UNKNOWN` as for a nonexistent model — no cross-tenant existence oracle via preset save.
</must>
Reject:
<reject>
  - succeeded job whose base-model pricing snapshot is unresolvable -> "finetune_registry_pricing_unresolved" (logged event; registration DEFERRED to the repair sweep — an unpriced callable model would mint silent-$0 usage rows)
  - cross-tenant request naming another tenant's ft model -> "ERR_MODEL_NOT_FOUND" (the existing ModelAccess.UNKNOWN wire behavior — never a distinguishable 403)
  - succeeded job with fine_tuned_model NULL (provider anomaly) -> "finetune_registry_model_id_missing" (logged; no registration; job untouched)
</reject>
After:
<after>
  - the owning tenant sees `ft:*` in its /v1/models with marked-up prices, can call it via the normal proxy path (checker ACTIVE → provider registry "openai"), and every such call bills exactly one usage record priced from the ft snapshot × the shared markup; no other tenant can see or resolve the model; the job row itself is never modified by this task.
</after>
Boundary: base-model id format — job.model is the OpenAI-native id (e.g. "gpt-4o-mini-2024-07-18"); pricing-basis lookup tries exact `models.id == job.model` first, then `"openai/" + job.model` (OpenRouter-prefixed catalog variant); both miss ⇒ the pricing_unresolved defer path.
<assumptions>
  ⚠ pricing basis = COPY of the base model's snapshot — OpenAI actually bills ft:* INFERENCE at a premium over the base model; copying the base snapshot systematically UNDER-bills the passthrough component until a real ft rate feed (or a Tin-chosen multiplier knob) exists — if wrong: margin leak on every ft call, silent until reconciliation.
  ⚠ the completions hot path consults `check_for_tenant` (not the frozen tenant-blind `is_active`) — [DERIVED] from model-mgmt's "piggybacking on the existing ModelChecker hot-path hit"; if wrong: the cross-tenant 404 gate has a bypass and M4 needs a second enforcement point — caught by the §4 cross-tenant tests at build.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
No new HTTP endpoint. The contract is the listener→catalog registration flow + two schema deltas.

FinetuneModelRegistrar (new module gateway/finetune_registry/application/registrar.py)
  __init__(session_factory)            # async_sessionmaker — opens its OWN session; the
                                       # listener runs AFTER the broker's commit (D4, frozen)
  async on_succeeded(job) -> None      # implements FinetuneCompletionListener (frozen port,
                                       # gateway/finetune/domain/provider_port.py):
    job.fine_tuned_model is None       -> log "finetune_registry_model_id_missing"; return
    base := models[job.model] else models["openai/"+job.model]; its LATEST pricing_snapshots row
    base snapshot absent               -> log "finetune_registry_pricing_unresolved"; return (defer)
    INSERT models(id=job.fine_tuned_model, name=id, tenant_id=job.tenant_id,
                  provider=job.provider, modality='chat', active=true,
                  region=base.region, context_length=base.context_length)
      ON CONFLICT (id) DO NOTHING      # exactly-once anchor (M5)
    iff that insert won: INSERT pricing_snapshots(model_id=id,
      prompt/completion = base-latest values (Decimal) * settings.finetune_pricing_multiplier,
      pricing_unit='per_token')
      # finetune_pricing_multiplier: NEW Settings field, Decimal, default "1.0" (byte-identical
      # to plain copy-base) — the ⚠ under-billing fix becomes a config flip, never a re-freeze
    ONE transaction — model row + snapshot commit together or not at all
  async repair_missed() -> int         # M6: SELECT finetune_jobs WHERE status='succeeded'
                                       # AND fine_tuned_model IS NOT NULL AND NOT EXISTS
                                       # (models.id = fine_tuned_model) → on_succeeded each;
                                       # returns count registered; DB-only (no breaker needed)

Wiring (main.py): app.state.finetune_completion_listener = FinetuneModelRegistrar(...)
  (replaces the frozen default None — the broker file itself is NOT edited)
  + a periodic repair task (catalog refresh_scheduler precedent, interval ~300s, jittered).

Schema (migration, down_revision = 6f2a9c1e3b7d — current head):
  ALTER TABLE models ADD COLUMN tenant_id UUID NULL
    REFERENCES tenants(id) ON DELETE CASCADE   # NULL = global row (all existing rows)
  + partial index ix_models_tenant ON models(tenant_id) WHERE tenant_id IS NOT NULL
  No new table -> NO EXPECTED_TABLES / guardrails-manifest change (both are table-level).

Tenant-scoping deltas (consumers of the new column):
  proxy/infrastructure/model_checker.py check_for_tenant — a row with tenant_id NOT NULL
    and != caller folds to UNKNOWN (byte-identical to absent; 404-never-leak).
    is_active stays FROZEN/untouched (tenant-blind; not the tenant gate).
  catalog/infrastructure/repository.py list_active_models_with_markup — AND
    (tenant_id IS NULL OR tenant_id = :tenant_id).
  catalog/infrastructure/repository.py sync_catalog — both deactivation sweeps AND
    tenant_id IS NULL (M7).
  proxy/infrastructure/tenant_model_preset_store.py upsert — target guard switches to
    check_for_tenant(target, tenant_id) is ACTIVE; same ERR_PRESET_TARGET_UNKNOWN
    for foreign-owned and nonexistent targets (M8 — closes the preset existence oracle).

Wire-visible behavior (existing codes only — no new wire errors):
  owner: ft:* appears in GET /v1/models (JWT branch) with marked-up prices; callable.
  foreign tenant: GET/completions on ft:* -> the EXISTING ERR_MODEL_NOT_FOUND 404 path.
```

Target (measurable): 10/10 §4 tests green · registration path performs ZERO outbound IO (DB-only; asserted by the fakes recording no provider calls beyond the broker's own poll) · default path byte-identical: full regression floor green with existing rows all tenant_id NULL · exact-Decimal snapshot copy (test asserts equality, not approx) · exactly one models row + one snapshot per ft id under double-fire.
Status: FROZEN @ v1 — approved by Tin
Reported: no — DRAFT; the freeze card is rendered by this direction beat's final report; Tin has not yet approved

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/finetune_registry/` · `apps/gateway/src/gateway/catalog/infrastructure/orm.py` · `apps/gateway/src/gateway/catalog/infrastructure/repository.py` · `apps/gateway/src/gateway/proxy/infrastructure/model_checker.py` · `apps/gateway/src/gateway/proxy/infrastructure/tenant_model_preset_store.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/migrations/versions/` · `apps/gateway/tests/finetune_model_registry/`
Regression floor: `apps/gateway/tests/finetune_broker/` · `apps/gateway/tests/migrations/` · `apps/gateway/tests/guardrails/` · `apps/gateway/tests/catalog/` · `apps/gateway/tests/model_mgmt/` · `apps/gateway/tests/tenant_model_presets/` · `apps/gateway/tests/tiered_rate_cards/` · `apps/gateway/tests/proxy_completions/` (the models-table + checker + resolver + preset consumers) — all green before the gate.
Persona: `.add/personas/billing-precision-engineer.md` (build) — Decimal-only cost paths, no silent-$0, provenance on every priced row.

Grounding anchors (the Contract may cite ONLY these — all [OBSERVED] this session @ 43fb91b):
`FinetuneCompletionListener` + `FinetuneProviderPort` (gateway/finetune/domain/provider_port.py, FROZEN) · `FinetuneBrokerService` CAS/listener fire (gateway/finetune/application/use_cases.py, FROZEN) · `FinetuneJobRow.fine_tuned_model` (gateway/finetune/infrastructure/orm.py, FROZEN) · `app.state.finetune_completion_listener = None` default (gateway/main.py) · `ModelRow` / `PricingSnapshotRow` / `TenantModelOverrideRow` (gateway/catalog/infrastructure/orm.py) · `SqlAlchemyCatalogRepository.sync_catalog` / `list_active_models_with_markup` (gateway/catalog/infrastructure/repository.py) · `SqlAlchemyModelChecker.is_active`(FROZEN) / `check_for_tenant` + `ModelAccess` (gateway/proxy/infrastructure/model_checker.py, gateway/proxy/domain/ports.py) · `resolve_markup_pct` (gateway/usage/application/rate_card_resolver.py, FROZEN @ v1) · recorder latest-snapshot lookup (gateway/usage/application/recorder.py) · `ProviderRegistry`/`select_provider` (gateway/proxy/infrastructure/provider_registry.py) · `CatalogModel` (gateway/catalog/domain/entities.py) · migration head `6f2a9c1e3b7d_finetune_broker.py` · catalog `refresh_scheduler.py` (periodic-task precedent).
Ground SHA: 43fb91b

Least-sure flag surfaced at freeze: [contract] the pricing basis — the ft snapshot COPIES the base model's latest snapshot; OpenAI bills ft:* inference at a PREMIUM over base, so passthrough under-bills until a real ft rate feed or a Tin-chosen multiplier exists (margin leak per ft call, invisible until reconciliation). Freeze options: (a) accept copy-base for v1 (flagged), (b) add a settings multiplier knob Tin names now.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_listener_wired_and_registers_model_on_success: drive a job to the winning CAS via GET; assert a real registrar is wired at app.state.finetune_completion_listener and a tenant-owned active models row exists for ft:* · covers: M1
  - test_registration_creates_pricing_snapshot_from_base: assert the ft snapshot equals the base model's latest snapshot prices (exact Decimal) with pricing_unit per_token · covers: M2
  - test_billing_resolves_through_shared_rate_card_resolver: tenants.markup_pct=25; assert recorder-shape latest-snapshot lookup resolves for ft:* and resolve_markup_pct returns 25 → effective == base × 1.25 exact Decimal · covers: M3
  - test_cross_tenant_model_access_is_unknown: check_for_tenant → owner ACTIVE, foreign tenant UNKNOWN (the 404 path) · covers: M4, R:ERR_MODEL_NOT_FOUND
  - test_listing_excludes_foreign_tenant_models: /v1/models JWT branch — owner lists ft:*, other tenant never does · covers: M4
  - test_double_fire_listener_registers_exactly_once: fire on_succeeded twice more after the HTTP drive → exactly 1 models row + 1 snapshot · covers: M5
  - test_missing_base_pricing_defers_registration: no base snapshot → NO models row AND the job stays "succeeded" (CAS untouched) · covers: R:finetune_registry_pricing_unresolved, M6-precondition
  - test_repair_sweep_registers_missed_model: basis appears after the miss; repair_missed() registers idempotently, returns ≥1 · covers: M6
  - test_catalog_sync_never_deactivates_tenant_models: provider-scoped sync of "openai" models omitting ft:* leaves it active · covers: M7
  - test_preset_target_validation_is_tenant_scoped: owner may preset-target its own ft model; a foreign tenant gets the byte-identical ERR_PRESET_TARGET_UNKNOWN as for a nonexistent id · covers: M8
</test_plan>

Prose build-guidance (no red test, not gated): fine_tuned_model NULL on a succeeded job → "finetune_registry_model_id_missing" logged no-op (provider anomaly, defensive) · the periodic repair task's interval/jitter (wiring detail; the SWEEP itself is gated by M6) · ft rows never re-priced by catalog sync (sync only upserts source models; ft ids never appear in a source batch — structural).
Coverage target: every §1 Must (M1–M8) + primary Rejects red-tested above; suite currently 10/10 RED for missing implementation.

Advisor record (add-advisor, propose-plan/pressure-test, 2026-07-24): CONFIRMED nullable models.tenant_id over a separate table (checked every consumer) and single migration head 6f2a9c1e3b7d with no manifest edit needed (column-only change). DECIDED-in: (a) `finetune_pricing_multiplier` Settings knob default 1.0 — the pricing-basis fix becomes a config flip, not a re-freeze; (b) NEW finding folded in as M8: `DbTenantModelPresetStore.upsert` validates via the tenant-blind frozen `is_active`, which this task's tenant-owned rows would turn into a cross-tenant existence oracle — scope widened by that one file + a red test added. No security HARD-STOP (the oracle is closed by M8 inside this task's own freeze).

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/finetune_model_registry/` · MUST run red (missing implementation) before Build.

RED-RUN EVIDENCE (2026-07-24, DB gateway_test_ftreg created fresh; `uv run pytest tests/finetune_model_registry/ --override-ini="addopts="`):
```
FAILED ...::TestRegistration::test_listener_wired_and_registers_model_on_success
  AssertionError: app.state.finetune_completion_listener is None — the finetune-model-registry
  registrar is not wired at the broker's FROZEN extension point
FAILED ...::TestRegistration::test_registration_creates_pricing_snapshot_from_base
FAILED ...::TestBilling::test_billing_resolves_through_shared_rate_card_resolver
FAILED ...::TestTenantScoping::test_cross_tenant_model_access_is_unknown
  assert <ModelAccess.UNKNOWN> is <ModelAccess.ACTIVE>   (ft model never registered)
FAILED ...::TestTenantScoping::test_listing_excludes_foreign_tenant_models
  AssertionError: owner tenant cannot see its own registered ft model
FAILED ...::TestExactlyOnce::test_double_fire_listener_registers_exactly_once
  ModuleNotFoundError: No module named 'gateway.finetune_registry'
FAILED ...::TestPartialFailure::test_missing_base_pricing_defers_registration
  asyncpg.exceptions.UndefinedColumnError: column "tenant_id" does not exist  [models]
FAILED ...::TestPartialFailure::test_repair_sweep_registers_missed_model
  ModuleNotFoundError: No module named 'gateway.finetune_registry'
FAILED ...::TestSyncSurvival::test_catalog_sync_never_deactivates_tenant_models
  UndefinedColumnError: column "tenant_id" does not exist  [models]
9 failed in 8.57s
```
Post-advisor re-run (M8 test added): 10 failed in 11.52s — the new
TestPresetOracle::test_preset_target_validation_is_tenant_scoped fails at
`ModelPresetError: ERR_PRESET_TARGET_UNKNOWN` on the OWNER's own upsert (the ft model is
never registered — missing implementation), not at import.
Every failure is a missing-implementation symbol/column/wiring — none is a harness break
(signup/login/files/finetune-broker HTTP legs all pass inside each test before the red assert).

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `src/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose listener-into-shared-catalog; rejected separate tenant_models table (rejected: every consumer — checker, listing, recorder, resolver — would need a second lookup path = a new mechanism, violating the milestone's "no new billing mechanism") · synchronous registration inside the broker CAS (rejected: the broker §3 is FROZEN — D4 explicitly decouples the listener from the CAS).
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
