# TASK: Alembic baseline + CI parity gate

slug: db-migrations · created: 2026-06-10 · stage: mvp
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Alembic baseline migration + CI parity gate
Framings weighed: Alembic with async engine template (chosen) · raw SQL DDL scripts · SQLAlchemy create_all permanent

Must:
<must>
  - Alembic must be initialized under `apps/gateway` with `alembic.ini` at that root and
    `migrations/` as the versions directory (async SQLAlchemy template, asyncpg driver).
  - ONE baseline migration revision must capture the exact current ORM metadata for all six
    tables: tenants (id, name, markup_pct, budget_usd_monthly, created_at), users (id,
    tenant_id, email, password_hash, role, created_at + two check constraints), api_keys
    (id, tenant_id, name, key_hash, created_at, revoked_at + check constraint), models
    (id, name, context_length, active, created_at, updated_at), pricing_snapshots (id,
    model_id, prompt_usd_per_token, completion_usd_per_token, captured_at), usage_records
    (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens, cost_usd, status,
    pricing_snapshot_id, raw, created_at).
  - `alembic upgrade head` applied to an empty database must yield a schema identical to
    what `Base.metadata.create_all` would produce; this parity is asserted programmatically
    in the test suite, not verified by eye.
  - The baseline revision must have a downgrade() function that drops all six tables in
    dependency order; it must carry a data-loss warning in its module docstring.
  - `alembic upgrade head` run a second time (idempotent re-run from head) must be a no-op
    (no error, no schema change).
  - `alembic check` (or autogenerate producing an empty diff) run after `upgrade head` must
    report no pending migrations; this is the CI parity gate.
  - `env.py` must import all four ORM modules
    (gateway.tenants.infrastructure.orm, gateway.keys.infrastructure.orm,
    gateway.catalog.infrastructure.orm, gateway.usage.infrastructure.orm) so autogenerate
    sees the full metadata.
  - `env.py` must read the database URL from the `GATEWAY_DATABASE_URL` environment
    variable (falling back to the value in alembic.ini for local use); it must NOT
    hard-code credentials.
  - `create_all` bootstrap in `main.py` must remain guarded to
    `settings.environment in ("dev", "test")` and must NOT execute when
    `GATEWAY_ENVIRONMENT=production`; production relies on `alembic upgrade head` only.
  - A `make migrate` target must exist that runs `alembic upgrade head` from `apps/gateway`.
  - A `make migrate-check` target must exist that runs `alembic check` from `apps/gateway`;
    CI asserts no pending migrations after head.
  - `alembic` must be added to `pyproject.toml` runtime dependencies (it is already in
    `.add/dependencies.allowlist`).
  - Migration policy must be recorded in `migrations/README.md`: additive-only columns,
    rollback documented per revision, no destructive DDL without a migration per revision.
</must>

Reject:
<reject>
  - Running `alembic upgrade head` in a production-env gateway that still relies on
    create_all for its schema (schema conflict) -> operator error; not a runtime error code
    — the guard in main.py prevents create_all from running; the contract documents the
    operator must run `alembic upgrade head` before starting the service in production.
  - `alembic autogenerate` detecting a diff (model changed without a migration) ->
    CI gate fails; `make migrate-check` exits non-zero.
  - `GATEWAY_DATABASE_URL` unset and alembic.ini sqlalchemy.url is a placeholder ->
    `alembic upgrade head` raises `sqlalchemy.exc.ArgumentError` or equivalent connection
    error; this is expected operator misconfiguration, not a product defect.
</reject>

After:
<after>
  - `apps/gateway/alembic.ini` exists and points to `migrations/` script location.
  - `apps/gateway/migrations/env.py` exists with async run_migrations_online() using
    asyncpg, imports all ORM modules, reads GATEWAY_DATABASE_URL.
  - `apps/gateway/migrations/versions/<rev>_baseline.py` exists with upgrade() and
    downgrade() covering all six tables.
  - `apps/gateway/migrations/README.md` exists documenting migration policy.
  - `alembic upgrade head` from an empty DB produces parity with ORM metadata.
  - `alembic check` reports no pending migrations after upgrade head.
  - `make migrate` and `make migrate-check` targets exist in `Makefile`.
  - `alembic` in `apps/gateway/pyproject.toml` dependencies.
  - `GATEWAY_ENVIRONMENT=production` startup never calls `Base.metadata.create_all`.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] alembic's async autogenerate support (run_migrations_online with
    asyncio.run + AsyncEngine.connect) works correctly for all column types used
    (PGUUID, JSONB, Numeric, CheckConstraints, server_defaults) — lowest confidence
    because async env.py templates differ from sync and JSONB/custom types occasionally
    require explicit type comparators to suppress false-positive diffs; if wrong:
    the parity gate test will report spurious diffs and BUILD must add type comparison
    overrides, delaying the task by 1–2 iterations.

  ⚠ [spec] alembic is not yet in pyproject.toml dependencies (confirmed: only in
    .add/dependencies.allowlist, not in project requires); the BUILD step must add it;
    if wrong in the other direction (already present but under a different name):
    the allowlist gate would catch the discrepancy during the build phase.

  - [x] The six tables enumerated in Must are the complete set managed by Base.metadata
    (confirmed by reading all four orm.py files — TenantRow, UserRow, ApiKeyRow,
    ModelRow, PricingSnapshotRow, UsageRecordRow).

  - [x] The existing create_all guard in main.py (`settings.environment in ("dev","test")`)
    is already correct behavior; the contract formalizes it — no code change required for
    this rule (confirmed by reading main.py lines 95–99).

  - [x] Test Postgres is reachable at postgresql+asyncpg://gateway:gateway@localhost:5433
    and the gateway user can CREATE/DROP databases (needed for the migration test's
    dedicated database gateway_migrations_test).

  - [x] `alembic check` is available in Alembic ≥ 1.9; the allowlist does not pin a
    version so BUILD must add `alembic>=1.9` in pyproject.toml.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: upgrade-from-empty-parity
  Given an empty PostgreSQL database gateway_migrations_test (no tables)
  And alembic.ini and migrations/versions/<rev>_baseline.py exist
  When alembic upgrade head is run programmatically
  Then all six tables exist in the database (tenants, users, api_keys, models,
       pricing_snapshots, usage_records)
  And the set of column names, types, nullability, and check constraints for
      each table matches what Base.metadata.create_all would produce
  And the alembic_version table records the baseline revision id

Scenario: autogenerate-empty-diff
  Given the database has been upgraded to head via alembic upgrade head
  When alembic autogenerate is run (alembic check or produce-diff with
       compare_type=True)
  Then the generated migration script body is empty (no detected changes)
  And no new migration file is written

Scenario: second-upgrade-idempotent
  Given the database has already been upgraded to head once
  When alembic upgrade head is run a second time
  Then the command completes without error
  And the schema is unchanged (same tables, columns, constraints)
  And the alembic_version table still records the same revision id

Scenario: downgrade-baseline-drops-cleanly
  Given the database has been upgraded to head
  When alembic downgrade base is run
  Then all six domain tables are dropped
  And the alembic_version table is empty (no current revision)
  And no error is raised

Scenario: create-all-skipped-under-production-env
  Given GATEWAY_ENVIRONMENT is set to "production"
  When the FastAPI application startup hook runs
  Then Base.metadata.create_all is never called
  And the application starts without raising an error
  And no tables are created by the startup hook

Scenario: parity-gate-red-on-model-change-without-migration
  Given the database has been upgraded to head
  And a new column has been added to an ORM model without a corresponding migration
  When alembic check (autogenerate diff detection) is run
  Then the command exits non-zero (reports pending migration)
  And the diff output names the changed table/column
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
TOOLING CONTRACT — Alembic baseline + CI parity gate
(No HTTP endpoints; this is infrastructure tooling, not a domain API.)

File layout (all paths relative to apps/gateway/):
  alembic.ini                        — Alembic config; script_location = migrations;
                                       sqlalchemy.url = placeholder (overridden by env var)
  migrations/env.py                  — async template; reads GATEWAY_DATABASE_URL;
                                       imports all four ORM modules before autogenerate;
                                       target_metadata = Base.metadata;
                                       compare_type = True
  migrations/script.py.mako          — default Alembic mako template (unmodified)
  migrations/README.md               — migration policy: additive-only, rollback per revision,
                                       data-loss warning for destructive ops
  migrations/versions/<rev>_baseline.py
                                     — single baseline revision; upgrade() creates all six
                                       tables in dependency order; downgrade() drops them
                                       in reverse dependency order with a DATA-LOSS docstring

Environment variable:
  GATEWAY_DATABASE_URL               — overrides sqlalchemy.url in env.py (required in CI/prod;
                                       dev may use alembic.ini fallback pointing to localhost:5433)

Dependency:
  alembic>=1.9                       — added to apps/gateway/pyproject.toml [project.dependencies]
                                       (already in .add/dependencies.allowlist)

Makefile targets (added to repo-root Makefile):
  make migrate                       — cd apps/gateway && uv run alembic upgrade head
  make migrate-check                 — cd apps/gateway && uv run alembic check
                                       (exits non-zero if autogenerate detects a diff)

CI step (added to .github/workflows/ci.yml gateway job, after Tests):
  name: Migration parity gate
  run: make migrate-check
  env:
    GATEWAY_DATABASE_URL: postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test

create_all guard (existing; formalized as contract rule):
  main.py startup hook calls Base.metadata.create_all ONLY when
  settings.environment in ("dev", "test"); must NOT execute when
  GATEWAY_ENVIRONMENT=production.

Tables under migration management (alembic_version tracks these):
  tenants           — id, name, markup_pct, budget_usd_monthly, created_at
  users             — id, tenant_id, email, password_hash, role, created_at
                      + CHECK users_role_check + CHECK users_email_lowercase_check
  api_keys          — id, tenant_id, name, key_hash, created_at, revoked_at
                      + CHECK api_keys_name_length_check
  models            — id, name, context_length, active, created_at, updated_at
  pricing_snapshots — id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at
  usage_records     — id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,
                      cost_usd, status, pricing_snapshot_id, raw, created_at

Rejection responses (operator-level, not HTTP):
  Model changed without migration -> `make migrate-check` exits non-zero; CI fails.
  GATEWAY_DATABASE_URL unset       -> alembic raises connection/argument error; operator error.
  create_all in production         -> Forbidden by main.py guard; no runtime error raised by
                                      application code (guard is the control).
```

Status: FROZEN @ v2 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] async autogenerate parity for JSONB, PGUUID, and server_default expressions —
  Alembic's async env.py with compare_type=True may emit false-positive diffs for
  PostgreSQL-specific types (JSONB rendered as JSON, PGUUID vs UUID) or server_default
  string comparisons; if wrong, the parity gate test will be permanently red and BUILD
  must add render_as_batch=False overrides or custom type comparators, blocking the CI gate.
⚠ [contract] alembic not yet in pyproject.toml — the BUILD step must add it before any
  alembic command can run; if the allowlist check_allowlist.py script also validates
  pyproject.toml (not just the allowlist file), the CI will fail until both files are
  updated atomically in the same commit.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (migration infrastructure; the six scenarios are the coverage surface)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_upgrade_from_empty_parity:
      arrange: create dedicated DB gateway_migrations_test; build alembic Config pointing to
               apps/gateway/alembic.ini with GATEWAY_DATABASE_URL set to that DB
      act: alembic.command.upgrade(cfg, "head")
      assert: all six tables exist (pg_tables query); alembic_version row exists
      assert: column names match Base.metadata (introspect via sqlalchemy inspect)
      assert: existing 92 tests unaffected (isolated DB)

  - test_autogenerate_empty_diff:
      arrange: database upgraded to head
      act: run alembic check (command.check) or capture autogenerate script body
      assert: command exits without raising CommandError / no diff detected
      assert: no new file written to migrations/versions/

  - test_second_upgrade_idempotent:
      arrange: database already at head
      act: alembic.command.upgrade(cfg, "head") a second time
      assert: no exception raised
      assert: alembic_version still holds same revision id
      assert: table count unchanged

  - test_downgrade_base_drops_cleanly:
      arrange: database upgraded to head
      act: alembic.command.downgrade(cfg, "base")
      assert: none of the six tables exist
      assert: alembic_version table is empty (no current revision)
      assert: no exception raised

  - test_create_all_skipped_under_production_env:
      arrange: Settings(environment="production", database_url=TEST_DATABASE_URL,
               jwt_secret=<valid non-dev secret>); patch Base.metadata.create_all
      act: call create_app(settings) and trigger the startup hook via TestClient or
           direct lifespan invocation
      assert: create_all was NOT called (mock assert_not_called)
      assert: application object returned without error

  - test_parity_gate_red_on_model_change_without_migration:
      arrange: database upgraded to head; temporarily add a column to one ORM model's
               metadata (monkeypatched — do not edit source); run autogenerate diff
      act: alembic.command.check(cfg) or capture MigrationScript ops
      assert: CommandError raised (or diff is non-empty), confirming gate fails on drift
      assert: diff references the patched table name
</test_plan>

Tests live in: `apps/gateway/tests/migrations/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line:
     `apps/gateway/tests/migrations/` = 2 files (conftest.py + test_migrations.py) -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 6/6 migration tests + full suite 98 passed (make ci exit 0,
      re-run by orchestrator); the contracted CI parity sequence reproduced locally on a
      fresh scratch DB: upgrade head → alembic check → "No new upgrade operations detected"
- [x] coverage did not decrease — make ci coverage floor held
- [x] no test or contract was altered during build — `git diff <freeze>..HEAD -- tests .add`
      empty for the build commit; Makefile targets exactly as contracted
- [x] concurrency / timing of the risky operation is safe — env.py runs async migrations in
      a dedicated single-worker thread (fresh event loop; safe under pytest-asyncio and CLI);
      baseline DDL is transactional (PostgresqlImpl transactional DDL)
- [x] no exposed secrets, injection openings, or unexpected dependencies — DB URL from
      GATEWAY_DATABASE_URL env (alembic.ini holds a placeholder, no credentials committed);
      alembic was already allowlisted; mako/markupsafe arrive as its dependencies via lock
- [x] layering & dependencies follow CONVENTIONS.md — migrations are infra tooling under
      apps/gateway; no domain/application code changed; create_all stays dev/test-guarded
- [x] a person reviewed and approved the change — orchestrator review found the builder's CI
      parity step DEFECTIVE (alembic check ran against the unstamped gateway_test schema →
      would fail every CI run) and fixed it before gating: fresh gateway_parity scratch DB +
      make migrate + make migrate-check, validated locally exit 0; also added the missing
      Redis service to the CI gateway job (latent test failure for remote runs)
      (delegated auto mode, Tin Dang, 2026-06-10)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — env.py imports all four ORM modules (tenants/keys/catalog/usage) so
      autogenerate sees full metadata (verified by the empty-diff test catching a simulated
      model change in test 6); Makefile migrate/migrate-check consumed by the CI step
- [x] DEAD-CODE (code) — no orphaned symbols; script.py.mako is alembic's revision template
      (consumed by future autogenerate runs); README.md records the additive-only policy
- [x] SEMANTIC (prose / non-code) — baseline revision read line-by-line against the six ORM
      tables (columns, FKs, server defaults); downgrade carries the contracted DATA-LOSS
      warning; alembic.ini script_location and URL-override semantics match §3

### GATE RECORD
Outcome: PASS (auto-resolved — autonomy: auto; evidence complete; the CI-step defect was
fixed and re-verified before gating, not waived)
Reviewed by: Claude (orchestrator) under delegated auto mode — Tin Dang · date: 2026-06-10

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): CI migration-parity gate result per PR (gateway_parity scratch DB) · alembic current vs head drift at deploy time (runbook check)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
