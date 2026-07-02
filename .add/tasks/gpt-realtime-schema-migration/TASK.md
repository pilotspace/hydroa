# TASK: Additive dual-stream (text+audio) pricing columns

slug: gpt-realtime-schema-migration · created: 2026-07-01 · stage: production · risk: high
autonomy: conservative   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
  - `catalog/infrastructure/orm.py:PricingSnapshotRow` (line 44) — currently has ONE scalar price
    per stream: `prompt_usd_per_token`/`completion_usd_per_token` (NOT NULL) plus the tiered-billing
    additions `cached_input_usd_per_token`/`reasoning_usd_per_token`/`cache_creation_usd_per_token`
    (all `Numeric(20,10)`, nullable). Add 3 more nullable `Numeric(20,10)` columns following the
    EXACT same precedent: `audio_prompt_usd_per_token`, `audio_completion_usd_per_token`,
    `audio_cached_usd_per_token` — NULL for every existing/future single-stream model, populated
    only for GPT-Realtime.
  - `usage/infrastructure/orm.py:UsageRecordRow` (line 36) — currently has `prompt_tokens`/
    `completion_tokens` (Integer NOT NULL server_default="0") plus the tiered-billing additions
    `cached_tokens`/`reasoning_tokens`/`cache_creation_tokens` (same shape). Add 3 more Integer
    NOT NULL `server_default="0"` columns: `audio_prompt_tokens`, `audio_completion_tokens`,
    `audio_cached_tokens` — every existing row gets 0 via the server_default, byte-identical.
  - NO Alembic/formal migration tooling exists in this repo (confirmed: no `alembic`/`migrations`
    directory under `apps/gateway`) — every prior additive column in this codebase (modality,
    provider, input_modalities, pricing_unit, cached_input_usd_per_token, etc.) was added purely
    by adding a new `mapped_column(..., nullable=True)` or `nullable=False, server_default=...`
    field to the ORM class; there is no separate migration file to write or run.
  - `usage/application/recorder.py:compute_per_token_cost_usd` (line 523) — OUT OF SCOPE for
    THIS task (belongs to the later `gpt-realtime-relay-billing` task). This task adds ONLY the
    storage columns; no billing math changes here, so ZERO existing behavior can regress —
    the columns are written by nothing and read by nothing until the later 2 tasks land.

Context (working folder): This is task 1 of 3 in the `gpt-realtime-pricing` milestone (schema ->
  catalog-seed -> relay-billing, strict dependency chain). GROUND-phase research for the milestone
  (2026-07-01, subagent trace) confirmed both tables use ONE scalar price/count per token stream
  today — no existing concept of "stream type" (text vs audio) anywhere in the schema. OpenAI's
  real GPT-Realtime pricing has 2 independently-priced streams: text ($4.00/$16.00 per 1M in/out,
  $0.40/1M cached) and audio ($32.00/$64.00 per 1M in/out, $0.40/1M cached) — 6 numbers total,
  none of which fit today's single-stream `prompt_usd_per_token`/`completion_usd_per_token`/
  `cached_input_usd_per_token` triad without conflating two very differently-priced token types.
Honors (patterns / conventions):
  - Additive-only, nullable/server_default-only discipline — every prior schema change in this
    codebase (see `orm.py`'s own docstrings citing provider-seam, pricing-units, tiered-token-
    billing, prompt-cache-passthrough, provider-cost-reconciliation, stream-usage-completeness,
    provider-generation-id-capture) follows this exact shape; NEVER a NOT-NULL-without-default
    column on an existing table, NEVER a column removal/rename.
  - `pricing_snapshots` stays APPEND-ONLY (never UPDATE/DELETE) — unaffected by this task, no
    change to that invariant, just 3 more nullable columns on the same append-only row shape.
  - `usage_records` is also append-only (Redis-stream-flushed ledger) — same discipline: new
    columns, server_default="0", zero impact on any already-flushed row.
Anchors the contract cites: `PricingSnapshotRow.audio_prompt_usd_per_token` /
  `audio_completion_usd_per_token` / `audio_cached_usd_per_token`; `UsageRecordRow
  .audio_prompt_tokens` / `audio_completion_tokens` / `audio_cached_tokens`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Additive dual-stream (text + audio) pricing/usage columns on pricing_snapshots and
  usage_records, laying the storage foundation for GPT-Realtime's real 2-stream billing.
Framings weighed:
  - **(chosen)** 6 new nullable/server_default="0" columns, 3 per table, named
    `audio_prompt_*`/`audio_completion_*`/`audio_cached_*` — mirrors the exact naming/nullability
    convention of the pre-existing `cached_input_*`/`reasoning_*`/`cache_creation_*` columns.
  - A generic key-value "extra prices"/"extra token counts" JSONB blob instead of named columns —
    rejected: this codebase has zero precedent for schema-as-JSONB pricing (the `raw` JSONB column
    on usage_records is for the UNSTRUCTURED provider payload, never for structured billing
    quantities that `compute_per_token_cost_usd`-style code needs to read/compare), and it would
    make the append-only ledger's cost math opaque to a straight SQL read.
  - A SEPARATE `pricing_snapshots_dual_stream` / `usage_records_audio` side table (1:1 FK) instead
    of new columns on the existing tables — rejected: adds a JOIN to every read path
    (`list_active_models_with_markup`, `_fetch_latest_pricing`) for a feature only ONE model uses
    today; the flat-column approach costs 6 always-NULL/zero columns on every other row, which is
    exactly the tradeoff this codebase already made for `reasoning_usd_per_token`/
    `cache_creation_usd_per_token` (used by ~0-1 providers each) — precedent says columns win.
Must:
<must>
  - `PricingSnapshotRow` gains exactly 3 new columns: `audio_prompt_usd_per_token`,
    `audio_completion_usd_per_token`, `audio_cached_usd_per_token` — all `Numeric(20,10)`, nullable,
    no server_default (NULL is the correct "no audio stream" value, matching
    `cached_input_usd_per_token`'s own precedent).
  - `UsageRecordRow` gains exactly 3 new columns: `audio_prompt_tokens`, `audio_completion_tokens`,
    `audio_cached_tokens` — all `Integer`, `nullable=False`, `server_default="0"` (matching
    `cached_tokens`/`reasoning_tokens`/`cache_creation_tokens`'s own precedent).
  - Every pre-existing row in both tables is byte-identical after this change: pricing_snapshots
    rows get NULL for the 3 new columns (same as any provider without today's cache/reasoning
    tiers); usage_records rows get 0 (same as `server_default="0"` already does for `cached_tokens`
    etc.).
  - `compute_per_token_cost_usd`/`_fetch_latest_pricing`/`_insert_snapshot`/`_price_changed`/
    `list_active_models_with_markup`/the API schemas/router — NONE of these read the new columns
    yet. This task is storage-only; nothing constructs a `PricingSnapshotRow`/`UsageRecordRow` with
    non-default values for the new fields until `gpt-realtime-pricing-fields`/
    `gpt-realtime-relay-billing` land.
Reject:
<reject>
  - N/A — this is a pure additive schema change with no new endpoint, no new request/response
    shape, and no new validation surface. There is no malformed-input path to reject; the only
    failure mode is a regression in EXISTING behavior, covered by the full regression suite
    (Must #3/#4 above), not a rejection scenario.
</reject>
After:
<after>
  - `PricingSnapshotRow`/`UsageRecordRow` have 6 new additive columns collectively, all inert
    (never written, never read) until the next 2 tasks in this milestone consume them.
  - The full pre-existing test suite passes unmodified — this task adds ZERO new runtime behavior,
    only new nullable/defaulted storage capacity.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ This codebase has no formal migration tool (no Alembic) — confirmed via a repo-wide search
  finding no `alembic`/`migrations` directory under `apps/gateway`. Lowest confidence because I
  could not find an explicit doc stating HOW schema changes reach a real, already-populated
  production database (vs. a fresh test DB via `Base.metadata.create_all()`); if this assumption
  is wrong and there IS a real out-of-band migration/DDL step for production, this task's "just
  add mapped_column fields" approach would be incomplete for prod (though still correct for every
  test in this repo, which is the actual verification surface available to me). Cost if wrong:
  the columns exist in test/dev but a real prod deploy needs an explicit `ALTER TABLE` this task
  didn't write — a deploy-time gap, not a code-correctness gap. Every prior "additive column" task
  in this codebase's history (provider-seam, pricing-units, tiered-token-billing, prompt-cache-
  passthrough, provider-cost-reconciliation, etc.) made the identical assumption without
  correction, so this is a standing, accepted project convention, not a new risk this task
  introduces.
  - [x] Whether `audio_cached_usd_per_token` needs its own fallback semantics distinct from
    `cached_input_usd_per_token`'s (falls back to the base prompt price when NULL) — confirmed NOT
    this task's concern: the fallback/billing-math design belongs entirely to
    `gpt-realtime-relay-billing` (§3 CONTRACT below only freezes the STORAGE shape, not the billing
    formula that will read it).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: GSM1 — PricingSnapshotRow gains 3 nullable audio-price columns
  Given a fresh test DB created from the current ORM metadata
  When a PricingSnapshotRow is inserted with only the pre-existing required fields (no audio_*
       kwargs passed)
  Then the row persists successfully and audio_prompt_usd_per_token/audio_completion_usd_per_token/
       audio_cached_usd_per_token all read back as NULL/None
  And this is byte-identical to today's behavior for cached_input_usd_per_token/
      reasoning_usd_per_token/cache_creation_usd_per_token (same None-default pattern)

Scenario: GSM2 — UsageRecordRow gains 3 NOT-NULL audio-count columns, server_default=0
  Given a fresh test DB created from the current ORM metadata
  When a UsageRecordRow is inserted with only the pre-existing required fields (no audio_*
       kwargs passed)
  Then the row persists successfully and audio_prompt_tokens/audio_completion_tokens/
       audio_cached_tokens all read back as 0
  And this is byte-identical to today's behavior for cached_tokens/reasoning_tokens/
      cache_creation_tokens (same server_default="0" pattern)

Scenario: GSM3 — the 6 new columns can be explicitly populated and round-trip exactly
  Given a fresh test DB
  When a PricingSnapshotRow is inserted with explicit Decimal values for all 3 audio price columns,
       and a UsageRecordRow is inserted with explicit int values for all 3 audio count columns
  Then re-reading each row from the DB returns the exact same values (Decimal precision preserved
       to Numeric(20,10); int counts exact)

Scenario: GSM4 (regression) — the full pre-existing test suite is unaffected
  Given the complete `apps/gateway/tests/` suite as it existed before this task
  When the full suite is run after this task's ORM changes land
  Then every test that passed before still passes, with the exact same count (no new failures,
       no new skips introduced by this change)
  And no existing test needed to be modified to accommodate the new columns (pure addition —
      no test constructs these rows with mandatory new fields)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new endpoint, no new request/response shape — this task is schema-only.

Schema (additive, both tables stay append-only):
  pricing_snapshots gains 3 columns:
    audio_prompt_usd_per_token      Numeric(20,10), nullable=True, no server_default
    audio_completion_usd_per_token  Numeric(20,10), nullable=True, no server_default
    audio_cached_usd_per_token      Numeric(20,10), nullable=True, no server_default

  usage_records gains 3 columns:
    audio_prompt_tokens      Integer, nullable=False, server_default="0"
    audio_completion_tokens  Integer, nullable=False, server_default="0"
    audio_cached_tokens      Integer, nullable=False, server_default="0"

Access pattern: unchanged — no new query, no new index, no new FK. The 6 new columns are inert
  (never selected, never written) until gpt-realtime-pricing-fields/gpt-realtime-relay-billing
  land. No Alembic migration file exists in this repo (confirmed, §0) — the ORM class change IS
  the full deliverable, matching every prior additive-column task's precedent.
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-01, via AskUserQuestion, "Approve as-is")
Least-sure flag surfaced at freeze:
⚠ [spec] Whether a real production deployment of this repo has an out-of-band schema-sync step
beyond `Base.metadata.create_all()` (no Alembic dir found) is unconfirmed — every prior additive
column in this codebase made the same assumption without correction, so this is a standing,
accepted project convention being extended here, not a new risk this task introduces. If wrong,
the gap is a deploy-time DDL step, not a code-correctness gap — this task's tests fully verify the
ORM/DB-layer behavior available to verify in this repo.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every new column round-trips; zero regression in the existing suite
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_gsm1_pricing_snapshot_audio_columns_null_by_default: insert a PricingSnapshotRow via the
    real ORM (no audio_* kwargs) / assert re-read row has all 3 audio price columns None
  - test_gsm2_usage_record_audio_columns_zero_by_default: insert a UsageRecordRow via the real ORM
    (no audio_* kwargs) / assert re-read row has all 3 audio count columns == 0
  - test_gsm3_audio_columns_round_trip_explicit_values: insert both rows WITH explicit non-default
    audio_* values / assert re-read matches exactly (Decimal precision + int exactness)
  - GSM4 (regression) is verified at VERIFY time by running the FULL suite, not a single test —
    matching the tiered-token-billing/minimax-catalog-seed precedent for schema-touching tasks.
</test_plan>

Tests live in: `./tests/gpt_realtime_schema_migration/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/catalog/infrastructure/orm.py`
`apps/gateway/src/gateway/usage/infrastructure/orm.py`
`apps/gateway/tests/gpt_realtime_schema_migration/`
`apps/gateway/migrations/versions/`
Strategy (ordered batches):
  1. `catalog/infrastructure/orm.py:PricingSnapshotRow` — add 3 nullable `Numeric(20,10)` columns.
  2. `usage/infrastructure/orm.py:UsageRecordRow` — add 3 `Integer nullable=False server_default="0"`
     columns.
  3. Run GSM1-3 to green; run the full regression suite (GSM4) before VERIFY.
Known-problem fixes: sibling-worktree orphaned `tenant_model_presets` table can block the `app`
  fixture's `drop_all` — confirm `pgrep -fl "worktrees/model-preset"` is idle, then
  `DROP TABLE IF EXISTS tenant_model_presets CASCADE;` before re-running, per the documented project
  gotcha (not a defect in this task's own code).
Strategy actually used: as planned for batches 1-2, PLUS an unplanned batch 4 discovered by GSM4
  itself: `apps/gateway/tests/migrations/test_migrations.py::test_upgrade_from_empty_parity` and
  `::test_autogenerate_empty_diff` FAILED on the first full-suite run. Root cause: §0's "no Alembic
  tooling exists in this repo" finding was WRONG — `apps/gateway/alembic.ini` +
  `apps/gateway/migrations/versions/` (35 prior migrations) exist and are enforced by a real
  ORM<->migration parity gate. Fix: added `migrations/versions/a4c6e8b0d2f3_gpt_realtime_audio_
  columns.py`, mirroring the `f3c8d1a6b9e4_tiered_token_billing.py` precedent exactly (same
  add_column shapes, chained after the true head `c2e4a6f8b0d3`). This does NOT change the frozen
  §3 CONTRACT's delivered shape (same 6 columns/types/defaults) — it corrects HOW an already-frozen
  Must (GSM4: full suite green) is satisfied. Added `migrations/versions/` to Scope above
  retroactively since it was written after the freeze/scope-declare step (see §7 Spec/Competency
  deltas for the corrected assumption).
Safety rule (feature-specific): both tables stay APPEND-ONLY — this task must never add an
  UPDATE/DELETE statement against pricing_snapshots or usage_records; only new columns on the
  existing INSERT-only row shape.
Code lives in: `apps/gateway/src/gateway/catalog/infrastructure/orm.py`,
`apps/gateway/src/gateway/usage/infrastructure/orm.py`
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

- [x] all tests pass — full suite 2110 passed / 7 skipped / 28 deselected / 0 failed (4th run, clean
  environment; runs 2-3 were disrupted by an unrelated sibling-worktree DB-contamination race, see
  Deep checks below)
- [x] coverage did not decrease — 3 new tests added (GSM1-3), zero existing tests removed/weakened
- [x] no test or contract was altered during build — only new files added (2 tests + 1 conftest +
  1 migration); the 2 pre-existing ORM files got pure additions (new columns), no existing line
  changed
- [x] the green was EARNED, not gamed — adversarial refute-read subagent (general-purpose,
  agent a00ad56fbd98c160d) verdict: EARNED (see Refute-read verdict below)
- [x] concurrency / timing of the risky operation is safe — pure additive DDL (ADD COLUMN with
  nullable/server_default), no lock contention beyond Postgres's instant metadata-only ALTER on
  PG 11+; both tables stay append-only (no UPDATE/DELETE introduced, confirmed by refute-read grep)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new dependency added;
  all SQL uses parameterized `text()` bind params, no string-interpolated SQL
- [x] layering & dependencies follow CONVENTIONS.md — additive-only nullable/server_default
  discipline matches every prior schema-change precedent in this codebase (tiered-token-billing,
  prompt-cache-passthrough, etc.)
- [x] a person reviewed and approved the change — Tin Dang approved the frozen §3 CONTRACT twice
  via AskUserQuestion (2026-07-01); GATE RECORD below records the VERIFY-stage review

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] PricingSnapshotRow has 3 new nullable Numeric(20,10) columns, NULL by default — confirmed by
  GSM1 (real INSERT without audio_* kwargs, re-read shows None for all 3) + code read of
  catalog/infrastructure/orm.py
- [x] UsageRecordRow has 3 new NOT NULL Integer server_default="0" columns — confirmed by GSM2
  (real INSERT without audio_* kwargs, re-read shows 0 for all 3) + code read of
  usage/infrastructure/orm.py
- [x] the 6 columns round-trip exact explicit values (Decimal precision + int exactness) — confirmed
  by GSM3
- [x] zero regression in existing behavior — confirmed by the full 2110-test suite run (clean) +
  the ORM<->Alembic migration parity suite (tests/migrations/, 6/6 passed) + targeted closest-blast-
  radius suites (tests/usage, tests/catalog, tests/tiered_token_billing: 37/37 passed, run by the
  refute-read agent)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the 6 new columns are referenced only by the 3 new GSM tests today (by
  design — §1 Must #3 contracts them as inert until the next 2 tasks land); confirmed via grep
  across `usage/application/`, `catalog/application/`, and API routers: zero other references.
  This is the CORRECT wiring state for a pure storage-groundwork task, not dead code.
- [x] DEAD-CODE (code) — no unused symbol: the columns are declared exactly where the frozen
  contract requires and are exercised by GSM1-3; the new Alembic migration file is referenced by
  the real `alembic upgrade head` path (confirmed live via tests/migrations/test_migrations.py)
- [x] SEMANTIC (prose/non-code) — read TASK.md §0-§5 in full (self) + independently re-read by the
  refute-read subagent; both confirm the delivered columns match the frozen §3 CONTRACT exactly
  (names/types/nullability/defaults)

Known limitation surfaced mid-build (documented honestly, not hidden): §0's "no Alembic migration
tooling exists in this repo" finding was WRONG — a real `apps/gateway/alembic.ini` +
`apps/gateway/migrations/versions/` (35 prior + this task's 1 new = 36 migrations) exist, enforced
by `tests/migrations/test_migrations.py`'s ORM<->migration parity gate. The initial ORM-only build
broke that parity (2 test failures). Fixed by adding
`migrations/versions/a4c6e8b0d2f3_gpt_realtime_audio_columns.py`, mirroring the
`f3c8d1a6b9e4_tiered_token_billing.py` precedent exactly (verified shape-for-shape by the refute-
read agent, including down_revision chain correctness: `c2e4a6f8b0d3` confirmed as the true prior
head, `a4c6e8b0d2f3` confirmed as the new head with no fork). This did NOT change the frozen §3
CONTRACT's delivered shape (identical 6 columns/types/defaults) — see §7 for the corrected
assumption fed forward as a competency delta.

Also encountered (unrelated to this task's code, documented for the record): 2 full-suite run
attempts (run2, run3) were disrupted by a pre-existing, previously-documented environmental race —
a sibling git worktree (`.claude/worktrees/model-preset`) periodically ran its own pytest suite
against the same shared Postgres (`gateway_test`), orphaning a `tenant_model_presets` table; when
that collided with this run's `drop_all`, `DROP TABLE tenants` failed with
`DependentObjectsStillExistError`, cascading into ~78 failed + ~833 errored tests. Every failure
in both disrupted runs traced to that identical root cause (grep-verified) — zero relation to the
catalog/usage/migration files this task touched. Run 4, in a confirmed-idle window, passed
completely clean (2110/2110).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: agent a00ad56fbd98c160d (general-purpose subagent, adversarial review) · adversarially checked:
  contract fidelity (ORM diff vs frozen §3 — exact match), scope creep (git diff --stat/status —
  none beyond declared + retroactively-declared migrations/ scope), append-only invariant (grepped
  both ORM files + migration for UPDATE/DELETE — none, only pre-existing docstring prose), Alembic
  chain correctness (down_revision graph — single clean chain, `a4c6e8b0d2f3` is the true head),
  test vacuousness (GSM1-3 use real parameterized INSERT/SELECT against the live DB, not stubs;
  migration itself independently validated by tests/migrations/, not just the ORM-facing GSM tests),
  and ran a closest-blast-radius regression slice (usage/catalog/tiered_token_billing: 37/37) plus
  the full migration-parity suite (6/6) live as part of its own review. Its one open item (it did
  not itself re-run the full 2110-test suite) was independently covered by this session's own run 4
  (2110 passed, 0 failed, clean environment) before this verdict was recorded.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (via AskUserQuestion, "Approve PASS", risk:high/autonomy:conservative human
  gate) · date: 2026-07-02

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the 6 new columns stay 100% NULL/0 in production until
  gpt-realtime-pricing-fields/gpt-realtime-relay-billing land — any non-default value appearing
  before then would indicate an out-of-band write and should be investigated.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-01, via AskUserQuestion, "Approve as-is"))
- [AI] build — strategy used: as planned for batches 1-2, PLUS an unplanned batch 4 discovered by GSM4
- [human] verify — gate PASS (reviewed by Tin Dang (via AskUserQuestion, "Approve PASS", risk:high/autonomy:conservative human)

### Spec delta
- [SPEC · open] `gpt-realtime-pricing-fields` and `gpt-realtime-relay-billing` (the next 2 tasks in
  this milestone) MUST each add their own Alembic migration file under
  `apps/gateway/migrations/versions/` for any schema/data change they introduce — do not repeat
  this task's initial "no Alembic exists" mistake (evidence: this task broke
  tests/migrations/test_migrations.py parity until a24c6e8b0d2f3 was added; the parity gate is real
  and enforced by CI-equivalent `add.py` full-suite runs).

### Competency deltas
- [ADD · open] GROUND-phase research (both this task's own §0 and an earlier GROUND-phase subagent
  for the parent milestone) wrongly concluded "no Alembic/formal migration tooling exists in this
  repo" — a repo-wide search missed `apps/gateway/alembic.ini` + `apps/gateway/migrations/versions/`
  (35 prior migrations) entirely, and this false premise was baked into the FROZEN, human-approved
  §3 CONTRACT text before being caught by the very GSM4 regression run the contract itself required.
  Future GROUND-phase research on schema-touching tasks MUST explicitly check for `alembic.ini`
  (via `find <app-root> -iname alembic.ini`) before asserting "no migration tool exists" — a
  directory-listing/grep miss is not equivalent to a confirmed absence (evidence: 2 full-suite
  failures — test_upgrade_from_empty_parity, test_autogenerate_empty_diff — caught the gap; fixed
  via a4c6e8b0d2f3, no contract/behavior change needed).
- [ADD · open] the shared test Postgres (`localhost:5433/gateway_test`) has no isolation between
  concurrent worktree pytest sessions — a sibling worktree's own full-suite run can orphan a table
  (`tenant_model_presets`) mid-run and cascade a single `DROP TABLE` FK failure into hundreds of
  unrelated test failures for the REST of that pytest session. This is the third time this exact
  signature has been hit this session alone (previously: catalog-pricing-fields's build_tampered
  remediation; now twice more here). Worth a real fix (e.g. per-worktree test DB names, like
  tests/migrations/conftest.py already does with its dedicated `gateway_migrations_test` DB) rather
  than continuing to work around it ad hoc — evidence: 2 of 4 full-suite attempts this task alone
  were disrupted by it (78 failed + 833 errors each time, 100% traced to the identical
  DependentObjectsStillExistError root cause, 0% overlap with any file this task touched).
