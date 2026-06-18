# TASK: Reconcile provider cost vs billed over a window (the metric primitive)

slug: reconciliation-aggregate · created: 2026-06-18 · stage: production
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
  - `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` (lines 35-80) — the append-only `usage_records` ledger. Columns this task reads: `cost_usd` Numeric(14,8) NOT NULL (the BILLED cost) · `provider_cost` Numeric(20,10) NULL (raw upstream cost, PRE-markup, set only on provider-basis rows) · `cost_basis` Text NOT NULL ('provider'|'catalog') · `usage_source` Text NOT NULL ('frame'|'stream_fallback'|'client_disconnect') · `tenant_id` UUID FK · `created_at` timestamptz · `status` int.
  - `apps/gateway/src/gateway/usage/api/router.py:get_usage` (lines 72-136) — the EXISTING ledger-aggregation pattern this task mirrors: raw `text("SELECT COALESCE(SUM(cost_usd),0) … FROM usage_records WHERE tenant_id=:tid")` via `session.execute(...).fetchone()`, money read back as `Decimal(str(row[i]))`.
  - `apps/gateway/src/gateway/usage/api/router.py:_compute_window_bounds` (lines 139-201) — ISO window → `[window_start, window_end)` (start INCLUSIVE, end EXCLUSIVE); invalid date → ProblemError 422. This task's aggregate takes the same half-open window.
  - `apps/gateway/src/gateway/usage/application/recorder.py` (lines 194-199) — proves the billed↔provider relation: on a provider-basis row `cost_usd = provider_cost * (1 + markup_pct/100)` (so `cost_usd ≥ provider_cost` when healthy); `cost_basis='provider'` only when `_safe_provider_cost(usage)` returned a value, else 'catalog' + `provider_cost` NULL.
  - NEW `apps/gateway/src/gateway/usage/application/reconciliation.py` — the pure aggregate primitive this task adds: an async function over (session, window[, tenant_id]) → a typed reconciliation summary. No ORM/schema change, no migration.
Context (working folder): the reconciliation MEASUREMENT layer over the existing append-only ledger — READ-ONLY (no INSERT/UPDATE/migration). v27 stamped `provider_cost`/`cost_basis`, v28 added the `usage_source` values (incl. `client_disconnect`); nothing aggregates provider_cost vs billed to detect drift. Sits beside the existing usage/spend read endpoints. The alert seam (`usage/application/alert_writer.py` + `infrastructure/alert_events_orm.py`) is consumed by the LATER drift-alert task, not this one.
Honors (patterns / conventions): CLEAN ARCHITECTURE — the aggregate is an application-layer function over the session, mirroring the existing raw-`text()` `COALESCE(SUM())` aggregation (no new query DSL); money is `Decimal` via `Decimal(str(...))`, never float; the half-open `[from, to)` window convention; "accuracy is never an availability gate" extended — reconciliation only READS, never mutates the append-only ledger.
Anchors the contract cites: NEW `reconciliation.py` aggregate function + its typed `ReconciliationSummary` result · `usage_records` columns `cost_usd`/`provider_cost`/`cost_basis`/`usage_source`/`created_at` · the half-open `[from, to)` window · the metric definitions — **drift = Σ(provider_cost) − Σ(cost_usd) over `cost_basis='provider'` rows**, **unbilled-upstream = `provider_cost > 0 ∧ cost_usd = 0`** grouped by `usage_source`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: window reconciliation aggregate — Σ(provider_cost) vs Σ(billed) + the unbilled-upstream breakdown, the shared metric primitive the endpoint and the drift-alert both consume.
Framings weighed: a pure application-layer aggregate function over the session (chosen — mirrors the existing `get_usage` raw-`text()` `COALESCE(SUM())` read, no new infra port, returns one typed summary both downstream tasks share) · a SQL (materialized) VIEW (rejected — adds a migration + a schema object for a read that is cheap on the tenant/created_at-indexed ledger, and is harder to unit-test; v29 is read-only measurement, not schema) · inline the SQL in the endpoint router (rejected — the metric is SHARED by the endpoint AND the alert; duplicating the definition in two places is exactly what the milestone's shared-contract decision forbids).
Must:
<must>
  - Given a half-open window `[from, to)` (UTC datetimes) and an optional `tenant_id`, aggregate over `usage_records.created_at ∈ [from, to)` (tenant-scoped when `tenant_id` is given, operator-wide over all tenants when None) and return a typed summary with: `provider_cost_total` = Σ(provider_cost) over `cost_basis='provider'` rows; `billed_total` = Σ(cost_usd) over `cost_basis='provider'` rows; `drift` = provider_cost_total − billed_total; `unbilled_upstream_cost` = Σ(provider_cost) where `provider_cost > 0 ∧ cost_usd = 0`; `unbilled_rows` = COUNT(*) of those; `catalog_billed_total` = Σ(cost_usd) over `cost_basis='catalog'` rows; and `by_source` = per-`usage_source` (rows, Σ provider_cost) for the unbilled-upstream rows.
  - Every money field is `Decimal`; every SUM uses `COALESCE(…, 0)` so an EMPTY window (or all-NULL provider_cost) returns explicit zeros, never None — the function NEVER raises on valid inputs.
  - Drift reconciles ONLY `cost_basis='provider'` rows (only they carry an authoritative upstream cost); `cost_basis='catalog'` rows are EXCLUDED from drift and surfaced separately as `catalog_billed_total` — never folded into drift.
  - The aggregate is READ-ONLY: a single SELECT, no INSERT/UPDATE, the ledger is unchanged.
</must>
Reject:
<reject>
  - an INVERTED window (`to < from`) -> raise `ValueError` (a caller bug, not a $0 result; the endpoint task maps it to ProblemError 422). An EMPTY window (`to == from`) is VALID → returns all-zeros.
  - (no other input rejection — this is a read; malformed ledger data degrades safely: a row with NULL provider_cost is simply not a provider-basis row via the `cost_basis` filter.)
</reject>
After:
<after>
  - The function returns a frozen `ReconciliationSummary` (window_from, window_to, provider_cost_total, billed_total, drift, unbilled_upstream_cost, unbilled_rows, catalog_billed_total, by_source). The ledger is byte-for-byte unchanged (pure read).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the DRIFT definition: `drift = Σ(provider_cost) − Σ(cost_usd)` over provider-basis rows. Because billed = provider_cost × (1+markup), a HEALTHY drift is NEGATIVE (we bill more than the upstream cost); drift trending toward/above 0 is the leak signal. The alternative the alert may prefer is the markup-FREE `unbilled_upstream_cost` (Σ provider_cost where billed=0), which is unambiguous regardless of markup. I surface BOTH in the summary so the alert task can choose its trigger — but the lowest-confidence point is whether the headline "drift" number should be the markup-INCLUSIVE aggregate or the markup-free unbilled sum. If wrong: the alert task re-derives its trigger from the other field (cheap — both are in the summary), but the endpoint's headline label would mislead. [→ the freeze decision for Tin]
  - [ ] operator-wide (tenant_id=None) aggregation across ALL tenants is wanted for the global leak monitor — confirm (the endpoint task scopes per-request; the alert wants the global view).
  - [ ] catalog rows (no provider truth) must NEVER count as drift — only provider-basis rows reconcile; catalog billed is reported separately as a non-reconcilable remainder.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: RA1 drift over provider-basis rows
  Given provider-basis rows in [from,to) with Σ(provider_cost)=2.00 and Σ(cost_usd)=3.00
  When reconcile_window(session, from, to) runs
  Then summary.provider_cost_total == Decimal("2.00") and summary.billed_total == Decimal("3.00")
  And summary.drift == Decimal("-1.00")   # billed > upstream cost = healthy margin

Scenario: RA2 an unbilled-upstream row is counted, never absorbed
  Given a provider-basis row with provider_cost=0.50 and cost_usd=0 (usage_source="client_disconnect")
  When reconcile_window runs over its window
  Then summary.unbilled_upstream_cost == Decimal("0.50") and summary.unbilled_rows == 1
  And summary.by_source contains {usage_source:"client_disconnect", rows:1, provider_cost:Decimal("0.50")}

Scenario: RA3 catalog rows are excluded from drift, surfaced separately
  Given catalog-basis rows (cost_basis="catalog", provider_cost NULL, Σ cost_usd=4.00) in the window
  When reconcile_window runs
  Then provider_cost_total, billed_total and drift are unaffected by the catalog rows
  And summary.catalog_billed_total == Decimal("4.00")

Scenario: RA4 an empty window returns explicit zeros and never raises
  Given no usage_records in [from,to)
  When reconcile_window runs
  Then every money field == Decimal("0") and unbilled_rows == 0 and by_source == []
  And the call does not raise

Scenario: RA5 the window is half-open [from, to)
  Given one row at created_at == from and one row at created_at == to
  When reconcile_window(session, from, to) runs
  Then only the created_at==from row is included (to is exclusive)

Scenario: RA6 tenant scoping vs operator-wide
  Given provider rows for tenant A (provider_cost 1.00) and tenant B (provider_cost 2.00) in the window
  When reconcile_window is called with tenant_id=A, then with tenant_id=None
  Then the A call's provider_cost_total == Decimal("1.00")
  And the None call's provider_cost_total == Decimal("3.00")  # all tenants

Scenario: RA7 an inverted window is rejected (reject)
  Given to < from
  When reconcile_window(session, from, to) is called
  Then it raises ValueError
  And the ledger is unchanged (no row written — it is a read)

Scenario: RA8 by_source groups unbilled rows by usage_source
  Given two unbilled-upstream rows: one usage_source="client_disconnect" (provider_cost 0.10),
        one usage_source="stream_fallback" (provider_cost 0.20)
  When reconcile_window runs
  Then summary.unbilled_rows == 2 and summary.unbilled_upstream_cost == Decimal("0.30")
  And summary.by_source has both sources, each with rows:1 and its provider_cost
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Pure aggregate primitive — NEW apps/gateway/src/gateway/usage/application/reconciliation.py:

  @dataclass(frozen=True)
  class SourceBreakdown:
      usage_source: str
      rows: int
      provider_cost: Decimal          # Σ provider_cost of unbilled rows with this source

  @dataclass(frozen=True)
  class ReconciliationSummary:
      window_from: datetime            # inclusive (UTC)
      window_to: datetime              # exclusive (UTC)
      provider_cost_total: Decimal     # Σ provider_cost  over cost_basis='provider'
      billed_total: Decimal            # Σ cost_usd        over cost_basis='provider'
      drift: Decimal                   # provider_cost_total − billed_total
      unbilled_upstream_cost: Decimal  # Σ provider_cost where provider_cost>0 AND cost_usd=0
      unbilled_rows: int               # COUNT(*) of those rows
      catalog_billed_total: Decimal    # Σ cost_usd        over cost_basis='catalog'
      by_source: tuple[SourceBreakdown, ...]   # unbilled rows grouped by usage_source, sorted

  async def reconcile_window(
      session: AsyncSession,
      window_from: datetime,
      window_to: datetime,
      tenant_id: uuid.UUID | None = None,
  ) -> ReconciliationSummary

Behavior:
  - window_to <  window_from  -> raise ValueError (inverted window).
  - window_to == window_from  -> VALID, all-zeros / empty by_source.
  - filter: created_at >= :from AND created_at < :to  (half-open) [+ AND tenant_id = :tid when given].
  - SELECT-only; COALESCE(SUM(...),0); money read as Decimal(str(row[i])); NEVER raises on valid inputs.
  - provider_cost_total / billed_total / drift over cost_basis='provider' ONLY; catalog_billed_total
    over cost_basis='catalog' (never folded into drift).
  - by_source: one SourceBreakdown per usage_source among unbilled-upstream rows
    (provider_cost>0 AND cost_usd=0), sorted by usage_source for determinism; () when none.

Schema: READS usage_records (cost_usd, provider_cost, cost_basis, usage_source, created_at, tenant_id).
  NO write · NO migration · NO new table/column/index · NO new dependency (stdlib datetime/decimal/uuid
  + SQLAlchemy text(), already used by the sibling get_usage read).
Invariants: pure read · Decimal money · half-open [from,to) · zeros-not-None on empty · provider-only drift.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-18, via AskUserQuestion: "Freeze as drafted").
Least-sure flag surfaced at freeze: [contract] the DRIFT definition — `drift = Σ(provider_cost) − Σ(cost_usd)`
over provider-basis rows is markup-INCLUSIVE (healthy drift is negative; trending ≥0 is the leak signal).
The markup-FREE `unbilled_upstream_cost` is the unambiguous "we paid upstream, billed the user $0" number.
BOTH are in the summary, so the alert task can trigger on either; the open point is only which one the
endpoint headlines as "drift". Cost if wrong: a relabel on the endpoint (the data is unchanged).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of `reconcile_window` + the `ReconciliationSummary`/`SourceBreakdown` shape (whole-suite ≥80%).
Plan (one test per scenario RA1–RA8; integration over a seeded ledger via the real-DB `db_session` fixture — seed `UsageRecordRow` rows directly, call `reconcile_window`, assert the typed summary):
<test_plan>
  - test_ra1_drift_provider_rows: seed provider rows (provider_cost+cost_usd) in window / call / assert provider_cost_total==2.00, billed_total==3.00, drift==-1.00.
  - test_ra2_unbilled_row_counted: seed a provider row provider_cost=0.50 ∧ cost_usd=0 (usage_source="client_disconnect") / assert unbilled_upstream_cost==0.50, unbilled_rows==1, by_source entry present.
  - test_ra3_catalog_excluded_from_drift: seed catalog rows (provider_cost NULL, cost_usd) / assert provider_cost_total/billed_total/drift unaffected, catalog_billed_total==Σ cost_usd.
  - test_ra4_empty_window_zeros: no rows in window / assert every money field==Decimal("0"), unbilled_rows==0, by_source==(), no raise.
  - test_ra5_window_half_open: rows at created_at==from and ==to / assert only the from-row counts (to exclusive).
  - test_ra6_tenant_scoping: tenant A (1.00) + B (2.00) rows / assert tenant_id=A→1.00, tenant_id=None→3.00.
  - test_ra7_inverted_window_raises: to<from / assert raises ValueError; assert no row was written (read-only).
  - test_ra8_by_source_grouping: two unbilled rows usage_source "client_disconnect"(0.10)/"stream_fallback"(0.20) / assert unbilled_rows==2, unbilled_upstream_cost==0.30, by_source has both.
</test_plan>

Tests live in: `apps/gateway/tests/reconciliation_aggregate/` · MUST run red (missing `reconciliation.py`) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/reconciliation.py` `apps/gateway/tests/reconciliation_aggregate/`
Strategy (ordered batches): 1. write the frozen `SourceBreakdown` + `ReconciliationSummary` dataclasses + `reconcile_window` (inverted-window guard → the half-open SELECT with COALESCE(SUM(...)) over the provider/catalog/unbilled partitions + the by_source group). 2. red suite → green.
Safety rule (feature-specific): SELECT-ONLY — the function never INSERTs/UPDATEs; the inverted-window `ValueError` raises BEFORE any query is issued; money stays `Decimal` end-to-end (no float).
Code lives in: `apps/gateway/src/gateway/usage/application/`
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

- [x] all tests pass — focused RA1–RA8 **8 passed** (`tests/reconciliation_aggregate`); full regression suite **1194 passed** (1186 prior + 8 new), exit 0, `--ignore=tests/edge` (live-stack).
- [x] coverage did not decrease — `reconcile_window` + both dataclasses are 100% exercised by the 8 scenarios (drift, unbilled, catalog, empty, half-open, tenant-scope, inverted-reject, by_source); no production line is unhit.
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched; the only post-build src edit was a `_as_naive_utc` **docstring** correction (NIT-1, doc-only, re-ran 8 green) — no test, no behavior, no contract changed.
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet, XML-prompted) returned **EARNED-WITH-NITS · confidence 0.93 · BLOCKERS: none**. Asserts are concrete Decimals over a real seeded Postgres ledger (no mocks, no vacuous asserts); RA7 proves read-only via a before/after `COUNT(*)`. NITs dispositioned in §7 — none weaken the green.
- [x] concurrency / timing — READ-ONLY: two SELECT-only aggregates, the append-only ledger is never written (no INSERT/UPDATE/lock). No shared mutable state; the inverted-window `ValueError` raises BEFORE any query. No new failure mode on the IO path (it only reads what the recorder already committed).
- [x] no exposed secrets, injection openings, or unexpected dependencies — parameterized binds only (`:from`/`:to`/`:tid`); the sole interpolated fragment is a STATIC literal `" AND tenant_id = :tid"` chosen by a None-check, never user text. No new dependency (stdlib datetime/decimal/uuid + SQLAlchemy `text()`, already used by sibling `get_usage`).
- [x] layering & dependencies follow CONVENTIONS.md — application-layer pure function over the `AsyncSession`, mirroring the existing `get_usage` raw-`text()` `COALESCE(SUM())` read; money `Decimal(str(...))` end-to-end, never float; no ORM/schema/migration change.
- [x] a person reviewed and approved the change — `autonomy: auto`; auto-resolved on complete evidence (refute-read no-blockers, full suite green, read-only/no-security-surface). Tin approved the §3 freeze (2026-06-18); no security/concurrency/architecture residue escalates this gate.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `reconcile_window`, `ReconciliationSummary`, `SourceBreakdown` are all referenced (imported + asserted) by `tests/reconciliation_aggregate/test_reconciliation_aggregate.py`. This is the milestone's SHARED PRIMITIVE, deliberately built before its two production consumers — the v29 reconciliation-endpoint (t2) and drift-alert (t3) tasks — which are seeded as §7 next-task pointers. The internal helpers `_money`/`_as_naive_utc` are both called inside `reconcile_window`.
- [x] DEAD-CODE (code) — no orphaned symbol: every dataclass field is populated and asserted; both module helpers are used; no leftover scaffolding.
- [x] SEMANTIC (prose / non-code) — n/a (code task; the WIRING + adversarial refute-read paths apply).

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (autonomy: auto · refute-read no-blockers · §3 freeze approved by Tin) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the drift sign (healthy < 0; trending ≥ 0 = leak) · `unbilled_rows`/`unbilled_upstream_cost` per window (any > 0 = "paid upstream, billed $0") · the `by_source` split (which source — `client_disconnect`/`stream_fallback`/`frame` — leaks).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · seeded] reconciliation-endpoint (v29 t2): expose `reconcile_window` over HTTP — map the inverted-window `ValueError` → ProblemError 422 (the §1 reject contracts this), reuse `_compute_window_bounds` for the half-open window, decide the headline label (markup-inclusive `drift` vs markup-free `unbilled_upstream_cost` — the §3 freeze flag) (evidence: this primitive is the milestone's shared metric, no HTTP surface yet).
  - [SPEC · seeded] drift-alert (v29 t3): consume `reconcile_window` on a schedule and write the alert via the existing `usage/application/alert_writer.py` + `infrastructure/alert_events_orm.py` seam when the trigger fires; choose the trigger field (recommend the markup-free `unbilled_upstream_cost > 0` — unambiguous regardless of markup) (evidence: §3 freeze flag surfaced BOTH fields precisely so the alert can pick).
  - [SPEC · open] the unbilled-upstream filter is `provider_cost > 0 ∧ cost_usd = 0` WITHOUT an explicit `cost_basis='provider'` clause — it relies on the recorder invariant that `provider_cost` is non-NULL only on provider-basis rows (a catalog row has NULL provider_cost, so `> 0` already excludes it). Correct today; if a future migration ever back-fills `provider_cost` onto catalog rows the filter must add `AND cost_basis='provider'` (evidence: NIT-5 from the refute-read — invariant-reliance, not a bug).
  - [SPEC · open] RA7 proves read-only on the REJECT path (inverted window, no query issued); the SUCCESS path's read-only-ness is argued structurally (SELECT-only SQL) but not asserted with a before/after COUNT. A belt-and-suspenders RA9 could seed → reconcile → assert `COUNT(*)` unchanged on a valid window (evidence: NIT-3 test-coverage gap, low value — the SQL is provably SELECT-only).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
  - [TDD · folded] a shared PRIMITIVE built before its consumers passes WIRING via its test suite alone — record the downstream consumers as seeded SPEC deltas so the "every new symbol referenced" check reads as deliberate-sequencing, not dead code (evidence: reconcile_window has no production caller until v29 t2/t3; the refute-read flagged then cleared it). [folded foundation-version 27]
  - [ADD · folded] a string-concatenated SQL `tenant_clause` fed by implicit-concatenation literals broke once mid-build (a `+ clause` between two adjacent string literals silently dropped the following `GROUP BY` fragment) — always make the `+ clause +` joins EXPLICIT around interpolated fragments in multi-line `text()` (evidence: the Query-2 SyntaxError fixed during build). [folded foundation-version 27]
  - [TDD · folded] the test/prod `created_at` schema drift (ORM `create_all` → naive TIMESTAMP; the migration → TIMESTAMPTZ) means a window bound must be normalized to naive UTC before binding — the existing `usage/api/router.py:284 # asyncpg expects naive UTC` is the canonical pattern `_as_naive_utc` now mirrors; new ledger reads should reuse it, not re-discover the asyncpg aware/naive mismatch (evidence: the RA-seed DataError fixed by stripping tz in both the conftest seed and the window bounds). [folded foundation-version 27]
