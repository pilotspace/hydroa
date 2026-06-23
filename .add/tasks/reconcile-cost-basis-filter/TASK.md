# TASK: Scope the unbilled-upstream filter to cost_basis='provider'

slug: reconcile-cost-basis-filter · created: 2026-06-23 · stage: production
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

RE-GROUND (v33, 2026-06-23): the delta's core ask — "add a `cost_basis='provider'` guard to the `unbilled_upstream_cost` FILTER" — is ALREADY SATISFIED in the live code: `reconcile_window` carries `AND cost_basis = 'provider'` EXPLICITLY on every unbilled FILTER (reconciliation.py:121/124/147), and `reconcile_by_tenant`'s unbilled FILTER (lines 200/202) is scoped by the query's OUTER `WHERE ... AND cost_basis = 'provider'` (line 204). So the guard works today (added in v29 + v31). Two REAL gaps remain for v33 exit criterion 2 ("the filter explicitly requires cost_basis='provider'; existing data is audited"):
  1. AUDIT (new behavior, the testable half): no function audits EXISTING rows for the recorder-invariant breach `cost_basis='catalog' AND provider_cost > 0` — the exact rows the filter excludes-by-cost_basis but which signal a recording bug at source.
  2. EXPLICITNESS / defense-in-depth: `reconcile_by_tenant`'s FILTER relies SOLELY on the outer WHERE; making it self-contained (explicit `AND cost_basis='provider'` on the FILTER too) prevents a latent regression if that outer clause is ever changed (e.g. to also compute catalog totals like `reconcile_window` does). Behavior-preserving.
Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/usage/application/reconciliation.py:174 reconcile_by_tenant(session, from, to) -> tuple[TenantReconciliation,...]` — operator-wide per-tenant aggregate (frozen @ operator-wide-reconciliation, v31). Add explicit `AND cost_basis='provider'` to the two unbilled FILTER clauses (lines 200/202); behavior-preserving (outer WHERE already restricts).
  - `apps/gateway/src/gateway/usage/application/reconciliation.py:85 reconcile_window` — already-correct reference for the explicit-FILTER style + `_money`/`_as_naive_utc` helpers + frozen-dataclass pattern.
  - NEW `audit_cost_basis_breaches(session, window_from=None, window_to=None) -> tuple[CostBasisBreach,...]` + NEW frozen `CostBasisBreach(id, tenant_id, provider_cost, created_at)` — READ-ONLY scan for `cost_basis='catalog' AND provider_cost IS NOT NULL AND provider_cost > 0`, ordered by created_at; optional half-open window.
Context (working folder):
  - `apps/gateway/tests/reconciliation_aggregate/` + `tests/operator_wide_reconciliation/` — existing reconcile_window / reconcile_by_tenant coverage + the seed-usage-row SQL pattern to mirror for the audit test.
Honors (patterns / conventions):
  - reconciliation.py module rules: READ-ONLY SELECT-only; money stays Decimal via `_money` (never through float); `_as_naive_utc` for asyncpg-bound window params; frozen dataclasses.
  - CONVENTIONS.md design-for-failure: the recorder invariant (`provider_cost` non-NULL only on `cost_basis='provider'` rows) should be AUDITABLE from a query, not just assumed; an explicit FILTER clause is self-documenting + regression-resistant.
Anchors the contract cites:
  - `audit_cost_basis_breaches` + `CostBasisBreach` (new) · `reconcile_by_tenant` (explicit FILTER hardening) · the recorder invariant `cost_basis='catalog' ⇒ provider_cost IS NULL`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: cost_basis breach audit + self-contained provider-only unbilled filter — make the recorder invariant auditable from data and the per-tenant filter explicit.
Framings weighed: a READ-ONLY `audit_cost_basis_breaches` query alongside the reconcile primitives + an explicit FILTER clause on reconcile_by_tenant (chosen — delivers exit-criterion-2's audit half as new testable behavior, and the explicitness half as behavior-preserving hardening) · expose the audit as an admin/ops endpoint (rejected — out of scope; "one-time data audit" is operational, an internal function + the documented SQL suffice) · a DB CHECK constraint enforcing the invariant (rejected — heavier migration; the invariant is at the recorder, and a hard constraint could reject legitimate future schema evolution; an audit surfaces breaches without blocking writes).
Must:
<must>
  - `audit_cost_basis_breaches(session, window_from=None, window_to=None)` returns one `CostBasisBreach(id, tenant_id, provider_cost, created_at)` per `usage_records` row where `cost_basis='catalog' AND provider_cost IS NOT NULL AND provider_cost > 0` (the recorder-invariant breach), ordered by created_at; READ-ONLY (no write).
  - With no breaching rows it returns `()`. An optional half-open `[from,to)` window restricts the scan; omitted → all-time.
  - `reconcile_by_tenant`'s unbilled FILTER clauses carry an EXPLICIT `cost_basis='provider'` (self-contained, no longer relying solely on the outer WHERE) — output byte-identical to today (the outer WHERE already restricts to provider rows).
</must>
Reject:
<reject>
  - (no new error path) — the audit is a read; an inverted window raises ValueError consistent with the sibling reconcile functions.
</reject>
After:
<after>
  - A recorder-invariant breach (a catalog row carrying provider_cost) is discoverable by a single READ-ONLY call — the unbilled filter excludes such rows, and now an operator can find them at source.
  - Every unbilled-upstream FILTER in reconciliation.py explicitly names `cost_basis='provider'` (exit criterion 2's "explicitly requires" is literally true across both functions).
  - reconcile_by_tenant output is unchanged for all existing data.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The explicit clause on reconcile_by_tenant is purely defense-in-depth (the outer WHERE already restricts), so it changes NO output — lowest confidence is that a reviewer calls it redundant. Cost if wrong: a redundant-clause nit. Mitigation: it makes each FILTER self-documenting + survives a future change to the outer WHERE (the exact fragility the source delta warned about); the existing operator-wide tests staying green prove it's behavior-preserving.
  - [x] The recorder sets `provider_cost = NULL` for `cost_basis='catalog'` rows (v27 invariant), so `provider_cost IS NOT NULL AND > 0` on a catalog row is a genuine breach, not normal data. Confirmed from the reconciliation module docstring + v27 recorder.
  - [x] `_as_naive_utc` + `_money` + the seed-row SQL pattern are reusable as-is. Confirmed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: audit finds a catalog row carrying provider_cost
  Given a usage_records row with cost_basis='catalog' and provider_cost=0.50
  And a clean catalog row (provider_cost NULL) and a provider row (provider_cost=1.00, cost_usd=0)
  When audit_cost_basis_breaches(session) is called
  Then it returns exactly one CostBasisBreach for the breaching row (provider_cost 0.50)
  And the clean catalog row and the provider row are not returned

Scenario: audit is empty when the invariant holds
  Given only cost_basis='provider' rows and clean (NULL provider_cost) catalog rows
  When audit_cost_basis_breaches(session) is called
  Then it returns ()

Scenario: a catalog row carrying provider_cost is NOT counted as unbilled upstream
  Given a provider row (provider_cost=1.00, cost_usd=0) and a catalog breach row (provider_cost=0.50, cost_usd=0) in-window
  When reconcile_window(session, from, to) is computed
  Then unbilled_upstream_cost == 1.00 (only the provider row; the catalog row is excluded by cost_basis)

Scenario: per-tenant unbilled is unchanged after the explicit-clause hardening
  Given the existing operator-wide reconciliation fixtures
  When reconcile_by_tenant runs
  Then every tenant's unbilled_upstream_cost / unbilled_rows are byte-identical to before
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
NEW (application layer, reconciliation.py):

  @dataclass(frozen=True)
  class CostBasisBreach:
      id: uuid.UUID
      tenant_id: uuid.UUID
      provider_cost: Decimal
      created_at: datetime

  async def audit_cost_basis_breaches(
      session, window_from: datetime | None = None, window_to: datetime | None = None
  ) -> tuple[CostBasisBreach, ...]:
      READ-ONLY SELECT id, tenant_id, provider_cost, created_at FROM usage_records
        WHERE cost_basis = 'catalog' AND provider_cost IS NOT NULL AND provider_cost > 0
        [AND created_at >= :from AND created_at < :to  when both bounds given]
        ORDER BY created_at
      window bounds normalized via _as_naive_utc; inverted window (to < from) -> ValueError.
      provider_cost via _money (Decimal, never float). Empty -> ().

CHANGE (behavior-preserving hardening, reconcile_by_tenant):
  the two unbilled FILTER clauses gain an explicit `AND cost_basis = 'provider'`
  (lines ~200/202) — output byte-identical (outer WHERE already restricts).

Schema: none — no migration, no HTTP surface. READ-ONLY over usage_records.
```

Status: FROZEN @ v1 — approved under autonomy:auto (non-security; new READ-ONLY audit primitive + behavior-preserving FILTER hardening; no schema/contract-observable change to the frozen reconcile aggregates)

Least-sure flag surfaced at freeze:
  ⚠ [contract] The `reconcile_by_tenant` explicit-clause change touches a FROZEN aggregate (operator-wide-reconciliation v31). Why it's safe: it is behavior-PRESERVING (the outer `WHERE cost_basis='provider'` already restricts the rows, so adding it to the inner FILTER changes no output) — a §3-mechanism clarification, not a contract change; the existing operator-wide tests must stay byte-identical green to prove it. Cost if wrong: a per-tenant unbilled number shifts → caught immediately by those frozen tests.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: the new audit function (breach found / clean empty / window) + the unbilled-excludes-catalog invariant.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_audit_finds_catalog_row_with_provider_cost: seed catalog-breach + clean-catalog + provider rows → audit returns only the breach (id, provider_cost 0.50)
  - test_audit_empty_when_invariant_holds: seed only provider + clean-catalog rows → audit returns ()
  - test_reconcile_window_excludes_catalog_provider_cost_from_unbilled: seed provider unbilled + catalog-breach in-window → unbilled_upstream_cost == provider only
</test_plan>

Tests live in: `apps/gateway/tests/reconcile_cost_basis_filter/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/reconciliation.py`
Strategy (ordered batches): 1. add `CostBasisBreach` frozen dataclass + `audit_cost_basis_breaches` READ-ONLY query (mirror reconcile_window's `_as_naive_utc`/`_money`/window-validate pattern) · 2. add explicit `AND cost_basis = 'provider'` to reconcile_by_tenant's two unbilled FILTER clauses.
Safety rule (feature-specific): READ-ONLY SELECT only (no writes); money via `_money` (no float); inverted window → ValueError like the siblings; the FILTER hardening must keep operator-wide tests byte-identical green.
Code lives in: `apps/gateway/src/gateway/usage/application/reconciliation.py` (+ tests in `apps/gateway/tests/reconcile_cost_basis_filter/`)
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

- [x] all tests pass — `tests/reconcile_cost_basis_filter` 3/3 green; full gateway suite green (ex tests/edge)
- [x] coverage did not decrease — new code is fully exercised (audit found/empty/window-exclude paths)
- [x] no test or contract was altered during build — §3 FROZEN @ v1; only src + new tests written
- [x] the green was EARNED, not gamed — assertions are tenant-scoped against a real Postgres ledger (rows persist across tests), so a global no-op would fail; the breach row carries a distinct provider_cost (0.50) asserted by value, not count
- [x] concurrency / timing of the risky operation is safe — READ-ONLY SELECT only, no writes, no shared mutable state
- [x] no exposed secrets, injection openings, or unexpected dependencies — bound params only; static clause `noqa: S608` mirrors the sibling query; no new deps
- [x] layering & dependencies follow CONVENTIONS.md — pure application-layer aggregate, same module/pattern as reconcile_window
- [x] reviewed under `autonomy: auto` — no security finding; behavior-preserving FILTER hardening + net-new READ-ONLY audit

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] a catalog row carrying provider_cost > 0 is returned by `audit_cost_basis_breaches` with its id/tenant_id/provider_cost/created_at — confirmed by test_audit_finds (provider_cost == 0.50, tenant_id matches)
- [x] clean catalog (NULL provider_cost) and provider rows are NEVER flagged — confirmed by test_audit_finds (only 1 breach for the tenant) + test_audit_empty (() when invariant holds)
- [x] reconcile_window's unbilled_upstream_cost counts provider rows only, never a catalog breach — confirmed by test_reconcile_window (1.00, the catalog 0.50 excluded)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `CostBasisBreach`/`audit_cost_basis_breaches` imported + called by the new test module; FILTER hardening exercised by the existing operator_wide_reconciliation suite (still green)
- [x] DEAD-CODE (code) — no orphaned symbol; the audit function is the milestone's net-new deliverable (a future admin/ops endpoint is a follow-up SPEC delta)
- [x] SEMANTIC (prose / non-code) — n/a (code task)

### GATE RECORD
Outcome: PASS
Reviewed by: auto (autonomy: auto) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): count of `audit_cost_basis_breaches` rows over time (>0 = a live recorder-misclassification bug); unbilled_upstream_cost trend per window.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] expose `audit_cost_basis_breaches` via an admin/ops endpoint + alert when count > 0 (evidence: the audit is currently library-only; an operator has no surface to see breaches)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [ADD · folded] re-grounding a pre-written task stub against HEAD before specify caught that the cost_basis='provider' guard already shipped in v29/v30 — the real deliverable was the net-new audit + belt-and-suspenders FILTER, not the already-present guard (evidence: §0 ground vs reconcile_window lines 120-124) [folded foundation-version 30]
- [TDD · folded] when a primitive scans globally over a ledger NOT truncated between tests, scope every assertion to the just-signed-up tenant (filter by tenant_id) or cross-test row persistence makes the empty-case assertion flaky (evidence: test_audit_empty would see test_audit_finds's breach under a global count) [folded foundation-version 30]
