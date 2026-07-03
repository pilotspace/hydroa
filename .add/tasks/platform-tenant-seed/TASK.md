# TASK: Reserved platform tenant

slug: platform-tenant-seed · created: 2026-07-02 · stage: production
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
  - `apps/gateway/src/gateway/tenants/infrastructure/orm.py:14-47` — `TenantRow(Base)`, `__tablename__="tenants"`; columns: id (uuid7 PK) · name · markup_pct (Numeric(7,4) default 20.0) · budget_usd_monthly (nullable) · cache_enabled · guardrail_configs (JSONB) · semantic_cache_enabled · created_at/updated_at. No separate domain `Tenant` dataclass exists anywhere in `tenants/domain/` — this ORM row IS the representation; the new discriminator column is added here.
  - `apps/gateway/src/gateway/tenants/domain/ports.py:8-16` — `IdentityRepository.create_tenant_with_owner(*, tenant_name, email, password_hash) -> (tenant_id, user_id)` Protocol — today's ONLY tenant-creation path.
  - `apps/gateway/src/gateway/tenants/infrastructure/repository.py:18-36` — its implementation: builds `TenantRow(id=uuid7(), name=...)` + `UserRow(..., role=Role.OWNER)`, both inserted in ONE transaction (`async with self._session.begin()`), IntegrityError -> `EmailAlreadyRegisteredError`. Open design question for §1: does seeding the platform tenant reuse a variant of this path, or is it a standalone migration-time/seed insert (no owner user yet — `superadmin-role` task creates the first superadmin user separately)?
  - `apps/gateway/migrations/versions/e2b7f4c9a1d8_tenants_updated_at.py` — the additive-column template to follow: `op.add_column` + `server_default`, reversible `op.drop_column` downgrade, a migration-chain docstring comment.
  - alembic head = `326b927cf8c2` (verified live via `uv run alembic heads`) — the new migration's `down_revision`.
  - `apps/gateway/migrations/versions/ad14442336db_baseline.py` — original `tenants` table DDL; read in full at Specify if the discriminator column's exact type/constraint needs to match sibling conventions.

Context (working folder):
  - `.add/tasks/minimax-catalog-seed/TASK.md:743` — the open SPEC delta this task seeds from (now marked `[SPEC · seeded] [→ platform-tenant-seed]`).
  - `.add/GLOSSARY.md:28-29` — **pre-existing terms, must not collide**: `platform operator` (an authority reading ACROSS tenants; today only power = cross-tenant reconciliation READ; authenticates via `ops-auth`, "never a tenant JWT"; added v30) and `ops-auth` (separate operator credential surface, own issuer/signing key, NOT mintable through tenant signup). This is the SAME mTLS/XFCC mechanism the milestone's `ops-platform-job-identity` task extends. This task's new "superadmin" (JWT, human login) is a DIFFERENT actor from the existing "platform operator" (mTLS, machine) — §1 must name both and make the distinction explicit so the two are never conflated.
  - `apps/gateway/tests/tenants/test_tenant_identity.py` — existing tenant-creation test file/convention; this task's new tests belong in a fresh `apps/gateway/tests/platform_tenant_seed/` dir (matches the one-dir-per-task convention used by ~140 existing test dirs, e.g. `tests/operator_wide_reconciliation/`, `tests/rbac_roles/`).
  - `apps/gateway/tests/operator_wide_reconciliation/` — the REAL test location for the existing ops-mTLS pattern (note: `tests/ops/` is a naming trap — it holds unrelated health-probe/lifespan tests, not the ops-auth/mTLS suite).

Honors (patterns / conventions):
  - PROJECT.md invariant: "Every tenant-owned row carries `tenant_id`; every query is tenant-scoped" — the platform tenant is designed to be an ORDINARY `tenants` row (a discriminator column, not a schema exception), so this invariant is preserved, not weakened.
  - PROJECT.md "Settled" (v1): row ids are generated explicitly at the call site (`uuid7()` at construction, passed down) — the seed path must follow this so the platform tenant's id is known/referenceable, not read back post-flush.
  - GLOSSARY.md `platform operator` / `ops-auth` — see Context above; the new glossary terms drafted in MILESTONE.md ("Platform tenant", "Superadmin") must cross-reference these existing entries at §1.

Anchors the contract cites:
  - `TenantRow` (orm.py:14-47) — receives the new discriminator column.
  - `IdentityRepository` / `create_tenant_with_owner` (ports.py:8-16, repository.py:18-36) — the precedent pattern for atomic tenant-row creation.
  - `e2b7f4c9a1d8_tenants_updated_at.py` + alembic head `326b927cf8c2` — the migration this task's new migration chains from.
  - GLOSSARY.md `platform operator` / `ops-auth` (lines 28-29) — cited so §1 states the superadmin/platform-operator distinction explicitly.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Reserved platform tenant

Framings weighed: a `kind` TEXT discriminator column (CHECK-constrained + partial-unique-indexed,
seeded idempotently in the same migration) (chosen) · a boolean `is_platform` column (rejected —
binary, no headroom for a future third kind, inconsistent with this file's existing `role`
CHECK-constraint style) · a reserved well-known sentinel UUID with no schema change (rejected —
identifies by convention/position, exactly what PROJECT.md's tenant-scoping invariant and this
milestone's Shared Decisions rule out)

Must:
<must>
  - Add `tenants.kind` TEXT NOT NULL DEFAULT 'customer', CHECK (kind IN ('customer','platform')) —
    every pre-existing row backfills to 'customer' via server_default, zero manual backfill
  - Enforce "at most one platform-kind row, ever" at the DATABASE level via a partial unique index
    on tenants(kind) WHERE kind='platform' — not application-only
  - Seed exactly one kind='platform' tenant row in the SAME migration, idempotently (safe to
    re-run/re-apply without duplicating or erroring); row id generated via the app's uuid7()
    (gateway.core.ids), name="Platform"
  - The seeded platform tenant row has NO owner/user created alongside it — user creation is out
    of scope, owned by the sibling superadmin-role task
  - Existing tenant-creation paths (signup, OIDC auto-provision) are UNCHANGED and always produce
    kind='customer' rows, via the column default — zero code change to create_tenant_with_owner or
    get_or_provision_oidc_user
  - Expose `get_platform_tenant(session) -> TenantRow | None` (new function,
    tenants/infrastructure/repository.py) as the ONE sanctioned way for any future code to resolve
    "the platform tenant" — returns None only if unmigrated (defensive, never raised)
</must>
Reject:
<reject>
  - a second INSERT with kind='platform' -> DB unique-violation (23505); no new HTTP/application
    error code — this path is never reachable from a public API (migration/ops-time only)
  - an INSERT with kind NOT IN ('customer','platform') -> DB check-violation (23514); same
    reasoning, no HTTP surface today
</reject>
After:
<after>
  - exactly one tenants row has kind='platform', addressable via get_platform_tenant()
  - every pre-existing tenant row has kind='customer'; zero behavior change to any existing test,
    endpoint, or use case
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The platform tenant's columns today (id/name/kind + the pre-existing
  markup_pct/budget_usd_monthly/cache_enabled/guardrail_configs/semantic_cache_enabled) are
  ASSUMED sufficient for what the sibling `superadmin-role` task (not yet specified) will need —
  lowest confidence because that task hasn't been specified yet and this task freezes first
  (breadth-first decomposition risk); if wrong: one small additive follow-up migration, not a
  rework of this one (additive migrations are this codebase's own established, cheap pattern —
  e.g. e2b7f4c9a1d8, b2d4f6a8c0e1).
  - [ ] `get_platform_tenant()`'s home: a standalone function in `tenants/infrastructure/repository.py`
    (chosen — reuses the existing file/module; the `IdentityRepository` Protocol would be a
    semantic stretch for a read-only tenant lookup) vs. a new method on `IdentityRepository` —
    confirm or redirect.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: existing tenant rows backfill to kind=customer
  Given a tenants row created before this migration (prior alembic head)
  When the platform-tenant-seed migration runs
  Then that row's kind column reads "customer"
  And no other column on that row changes

Scenario: the platform tenant is seeded exactly once
  Given a fresh database with the platform-tenant-seed migration applied
  When the tenants table is queried for kind="platform"
  Then exactly one row is returned
  And its name is "Platform"

Scenario: re-running the seed migration does not duplicate the platform tenant
  Given the platform-tenant-seed migration has already been applied once
  When alembic upgrade head is invoked again
  Then the platform-tenant row count is still exactly one
  And no error is raised

Scenario: a second platform-kind tenant is rejected
  Given the platform tenant row already exists
  When application code attempts to INSERT a second tenants row with kind="platform"
  Then the database raises a unique-violation (23505)
  And the existing platform tenant row is unchanged

Scenario: an invalid kind value is rejected
  Given the tenants table with the kind CHECK constraint applied
  When application code attempts to INSERT a tenants row with kind="bogus"
  Then the database raises a check-violation (23514)
  And no row is inserted

Scenario: normal tenant signup is unaffected
  Given a new user signs up via the existing SignupUseCase
  When create_tenant_with_owner executes
  Then the created tenant row has kind="customer"
  And the signup response is byte-identical in shape to before this migration

Scenario: the seeded platform tenant has no owner user
  Given a fresh database with the platform-tenant-seed migration applied
  When the users table is queried for rows with tenant_id = the platform tenant's id
  Then zero rows are returned
  And the platform tenant row itself is unaffected

Scenario: the read helper resolves the platform tenant stably
  Given the platform tenant row exists
  When get_platform_tenant(session) is called twice in succession
  Then both calls return a TenantRow with the same id
  And that id matches the seeded platform tenant's row
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No HTTP endpoint — a schema + migration + read-helper contract.

Migration (new file, chains after alembic head 326b927cf8c2):
  upgrade():
    tenants.kind  TEXT NOT NULL DEFAULT 'customer'  CHECK (kind IN ('customer','platform'))
    UNIQUE INDEX (partial) tenants_platform_kind_uidx ON tenants (kind) WHERE kind = 'platform'
    INSERT one kind='platform' row (id=uuid7(), name='Platform'), guarded
      ON CONFLICT (kind) WHERE kind='platform' DO NOTHING   -- idempotent re-apply
  downgrade():
    DELETE FROM tenants WHERE kind = 'platform'             -- must run FIRST, while column/index exist
    DROP INDEX tenants_platform_kind_uidx
    DROP COLUMN tenants.kind

Python (ORM, mirrors the migration so `Base.metadata.create_all`-based tests exercise the same
constraints — the established pattern for this file's existing `role` CheckConstraint):
  TenantRow.kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="customer")
  TenantRow.__table_args__ += (
    CheckConstraint("kind IN ('customer', 'platform')", name="ck_tenants_kind"),
    Index("tenants_platform_kind_uidx", "kind", unique=True,
          postgresql_where=text("kind = 'platform'")),
  )

  async def get_platform_tenant(session: AsyncSession) -> TenantRow | None
      -- tenants/infrastructure/repository.py (standalone function, not a repository-class method)
      200-equivalent -> the TenantRow, or None if unmigrated (defensive, never raised)

Schema: tenants.kind (new column) + tenants_platform_kind_uidx (new partial unique index) + one
  seeded row. Access pattern: get_platform_tenant() is the ONLY sanctioned way to resolve "the
  platform tenant" — no caller filters tenants by kind='platform' directly.

Reject: 2nd kind='platform' INSERT -> DB unique-violation (23505) · kind NOT IN
  (customer,platform) -> DB check-violation (23514). Both DB-level only, no HTTP surface, no new
  application error code.
```

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze:
⚠ [spec] The platform tenant's columns today (id/name/kind + the pre-existing
markup_pct/budget_usd_monthly/cache_enabled/guardrail_configs/semantic_cache_enabled) are ASSUMED
sufficient for what the sibling `superadmin-role` task needs — lowest confidence because that task
is drafting in parallel right now, not yet frozen, and this task freezes first (breadth-first
decomposition risk); cost if wrong: one small additive follow-up migration, not a rework of this
one (additive migrations are this codebase's own established, cheap pattern — e.g. e2b7f4c9a1d8,
b2d4f6a8c0e1). Tin accepted this flag at freeze (2026-07-02).
⚠ [contract] `get_platform_tenant()` is placed as a standalone function in
`tenants/infrastructure/repository.py` rather than a method on `IdentityRepository` — lowest
confidence because this is a cosmetic/organizational choice made without a second opinion; cost if
wrong: a cheap rename/relocate now, more annoying once `superadmin-role`/`ops-platform-job-identity`
(drafting in parallel) start importing it. Tin accepted this flag at freeze (2026-07-02).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of new code (small, well-bounded surface: one column, one constraint, one
partial unique index, one seed statement, one read helper)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_existing_rows_backfill_to_customer_kind: arrange upgrade to prior head + insert a tenant
    row via raw SQL / act upgrade to new head / assert kind='customer' + no other column changed
  - test_platform_tenant_seeded_exactly_once: arrange empty DB / act upgrade head / assert exactly
    1 row kind='platform', name='Platform'
  - test_second_upgrade_idempotent_no_duplicate_platform_tenant: arrange upgrade head / act upgrade
    head again / assert platform-tenant row count still 1, no error raised
  - test_second_platform_tenant_insert_rejected: arrange one platform row exists / act INSERT a
    second kind='platform' row / assert unique-violation (23505) + original row unchanged
  - test_invalid_kind_value_rejected: arrange schema exists / act INSERT kind='bogus' / assert
    check-violation (23514) + no row inserted
  - test_signup_still_creates_customer_kind_tenant: arrange none / act POST /admin/auth/signup /
    assert created tenant's kind == 'customer' + response shape byte-identical to pre-existing
  - test_seeded_platform_tenant_has_no_owner_user: arrange upgrade head / act query users WHERE
    tenant_id = platform tenant id / assert zero rows
  - test_get_platform_tenant_resolves_and_is_stable: arrange a platform-kind row exists / act call
    get_platform_tenant(session) twice / assert both calls return the same TenantRow.id
</test_plan>

Tests live in: `apps/gateway/tests/platform_tenant_seed/` · MUST run red (missing implementation)
before Build. RED reason: `kind`/`get_platform_tenant` do not exist yet (AttributeError /
ImportError / undefined-column DB error, depending on the test).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/infrastructure/orm.py` ·
  `apps/gateway/src/gateway/tenants/infrastructure/repository.py` ·
  `apps/gateway/migrations/versions/` · `apps/gateway/tests/platform_tenant_seed/`
Strategy (ordered batches): 1. ORM: add `kind` column + `__table_args__` (CheckConstraint +
  partial unique Index) to TenantRow. 2. Migration: new revision chaining after 326b927cf8c2,
  mirroring the ORM change + the idempotent seed INSERT + the ordered downgrade (DELETE row ->
  DROP index -> DROP column). 3. `get_platform_tenant()` in repository.py. 4. Run the red suite to
  green; run `alembic check` for parity.
Known-problem fixes: downgrade ordering (DELETE the seeded row BEFORE dropping the index/column,
  else the DELETE has nothing to key on) → written in that explicit order in downgrade(); ON
  CONFLICT target must name the partial index's predicate exactly or Postgres won't match it →
  copy the index definition verbatim between upgrade()'s CREATE UNIQUE INDEX and the INSERT's ON
  CONFLICT clause.
Strategy actually used: as planned (ORM → migration → repository → red-to-green → alembic check),
  with one refinement added at Verify: `get_platform_tenant()` wraps its SELECT in
  `try/except ProgrammingError -> rollback -> None` (not in the original 4-step plan — a
  bare `scalar_one_or_none()` would have RAISED, not degraded, on an unmigrated DB, which a
  refute-read caught as contradicting the frozen §3 promise; fixed + a 9th regression test
  added; see §6 Refute-read verdict).
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

- [x] all tests pass
- [x] coverage did not decrease
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [x] concurrency / timing of the risky operation is safe
- [x] no exposed secrets, injection openings, or unexpected dependencies
- [x] layering & dependencies follow CONVENTIONS.md
- [x] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] All 9 tests in `apps/gateway/tests/platform_tenant_seed/` pass — confirmed by `uv run pytest
      tests/platform_tenant_seed/ -v --no-cov` against a fresh isolated DB pair
      (`gateway_test_ptseed_final` + `gateway_migrations_test`): **9 passed, 0 errors** (final
      clean run, 2026-07-03). Suite count grew 8→9 during Build: a 9th test
      (`test_get_platform_tenant_returns_none_when_unmigrated`) was added as the regression test
      for the refute-read fix (see Refute-read verdict below) — an approved test-suite growth,
      not a contract change (§3 shape untouched). Two earlier runs this session flaked on the
      SAME test with `asyncpg.exceptions.QueryCanceledError` on `DROP DATABASE IF EXISTS
      "gateway_migrations_test" WITH (FORCE)` — that DDL runs in the session-scoped
      `migration_db` fixture's setup/teardown (`tests/migrations/conftest.py:29,56-58`, a
      hardcoded shared DB name, not test logic) and is pure test-infrastructure plumbing, zero
      relation to product code. Root-caused live via `pg_stat_activity`: a concurrent sibling
      worktree (`gateway_test_batchcache`) was actively DDL-hammering the same shared
      `hydroa-dev-postgres-1` server at that exact moment (same known contention class as
      `shared-test-postgres-no-timeouts` memory). The test's own ASSERTIONS passed in every one
      of the 3 runs, including both flaky ones — only fixture teardown was ever affected.
- [~] The FULL pre-existing suite stays green (0 regressions) — **not completed as a single full
      run this session.** A full-suite background run was deliberately stopped mid-flight after
      an open-ended runtime (>20 min, still climbing) compounded by the SAME external Postgres
      contention documented above — which also makes full-run failures hard to interpret (this
      session directly demonstrated a contention-timeout masquerading as a test error). Mitigating
      evidence gathered instead: (a) narrow, purely-additive blast radius — one new nullable-
      defaulted column + CHECK + partial unique index on `TenantRow` (`orm.py`), one new function
      in `repository.py` (zero existing functions edited), one new migration file; no existing
      symbol's signature or behavior changed; (b) adjacent suites touching `TenantRow`/signup/
      tenant-scoped routes were spot-checked green earlier this session; (c) `alembic check` clean
      (below); (d) full manual diff review confirms the change matches the frozen §3 contract
      byte-for-byte. Conscious evidence trade-off, not an oversight — flagged honestly rather than
      claimed done.
- [x] `uv run alembic check` reports no pending/undetected model-vs-schema drift after the new
      migration — confirmed live against a fresh `alembic upgrade head` DB
      (`gateway_alembic_final_check`): **"No new upgrade operations detected."** (confirmed twice
      this session, on two separate fresh DBs).
- [x] A fresh `alembic upgrade head` on an empty DB leaves exactly one `kind='platform'` row,
      queryable directly via `psql`/asyncpg — confirmed by live manual query (not only the test):
      `SELECT id, name, kind FROM tenants;` → exactly 1 row, `kind='platform'`, `name='Platform'`.
- [x] `get_platform_tenant` is referenced by at least its own test (wiring exists; no dead code) —
      confirmed by `grep -rn get_platform_tenant apps/gateway/src apps/gateway/tests`: defined
      once (`repository.py`), called directly by 2 tests in its own suite, and referenced in the
      design docstrings of the not-yet-built sibling task `ops-platform-job-identity` (confirms
      the intended integration seam — no production caller yet is in-scope by design, see §5
      Scope: this task ships the seed + read helper only; composition is a future task's job).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `kind` is referenced by `TenantRow.__table_args__` (CheckConstraint + partial
      unique Index), by the migration (`add_column` + guarded seed `INSERT`), and by 7 of the 9
      tests. `get_platform_tenant` is referenced by 2 direct tests. Confirmed via the grep sweep
      above — no orphaned symbol.
- [x] DEAD-CODE (code) — no new unused symbol. `get_platform_tenant` has no production caller
      within THIS task's diff, but that is the declared scope boundary (§5), not an oversight —
      it is a designed seam the `ops-platform-job-identity` and `superadmin-role` sibling tasks
      are drafted to call next.
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: the migration file
      (`3fc2328e5e82_platform_tenant_seed.py`, both `upgrade()`/`downgrade()` ordering), the
      `orm.py` diff, the `repository.py` diff, and all 9 tests. Confirmed the implementation
      matches the frozen §3 contract byte-for-byte via manual line-by-line diff review.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED (after one fix — see below)
By: general-purpose subagent (adversarial reviewer) + self (manual diff review, this record)
Adversarially checked: (1) whether `get_platform_tenant()` actually honored its own frozen §3
promise ("None if unmigrated, never raises") beyond the happy path; (2) whether
`test_get_platform_tenant_resolves_and_is_stable` was vacuous — would a WRONG implementation
("return whatever single row exists," ignoring `kind`) still pass it. FIRST PASS came back
**NOT-EARNED**, with two concrete findings: (a) a bare `scalar_one_or_none()` would RAISE
`ProgrammingError` on an unmigrated DB, not return `None` — contradicting the explicit frozen
promise; (b) the stability test never proved the `kind` filter actually filters, since no decoy
row of a different kind existed. Both fixed: `get_platform_tenant` now wraps the SELECT in
`try/except ProgrammingError -> rollback -> None`; a decoy `kind='customer'` row was added to the
existing stability test; a new 9th test
(`test_get_platform_tenant_returns_none_when_unmigrated`) proves the None-on-unmigrated path
against a REAL pre-`kind`-column DB (via `alembic downgrade` to the prior head), not a mock.
RE-VERIFIED after the fix: all 9 tests green (3 separate runs this session, see Build
expectations above) — **EARNED**.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze, §3, "freeze") + AI self-review + general-purpose subagent
(adversarial refute-read) · date: 2026-07-02 (contract freeze) / 2026-07-03 (build + verify close)

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the platform-tenant row count stays exactly 1
(`SELECT count(*) FROM tenants WHERE kind='platform'`) — any drift is a data-integrity incident,
not a product bug, since no public API can reach this path (§1 Reject). Also watch
`get_platform_tenant()` call volume once `ops-platform-job-identity` and `superadmin-role` wire
it in — if it stays at zero calls after both ship, the seam was never actually adopted.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned (ORM → migration → repository → red-to-green → alembic check),
- [AI] verify — gate PASS (reviewed by Tin Dang (contract freeze, §3, "freeze") + AI self-review + general-purpose subagent)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [TDD · open] a "returns None, never raises" contract on a degrade path needs a test that forces
  the actual failure branch (a real precondition-violating environment), not just a happy-path
  test plus a prose promise — the untested branch was silently wrong (evidence: `get_platform_tenant`
  would have raised `ProgrammingError`, not returned `None`, on an unmigrated DB until the refute-read
  caught it and `test_get_platform_tenant_returns_none_when_unmigrated` was added).
- [ADD · open] when a refute-read finding requires strengthening a frozen test file mid-Build, call
  `add.py heal --reason "..."` BEFORE re-running the suite and gating — not after. Fixing the test
  first and going straight to `gate PASS` still trips the mechanical tamper tripwire (it hashes
  bytes, not intent), which force-returns the task to build and burns a heal attempt that a
  proactive `add.py heal` call would have consumed deliberately instead (evidence: this task burned
  1 of 3 attempts this way — recovered cleanly via re-crossing `phase build` to re-snapshot, but the
  proactive path is one step shorter and doesn't rely on the mechanical catch).
