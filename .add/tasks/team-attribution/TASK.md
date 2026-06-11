# TASK: Historical team attribution on the usage ledger

slug: team-attribution · risk: moderate · autonomy: auto · created: 2026-06-11 · stage: production
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- Risk judgment: additive nullable column + read-only ledger rollup = moderate.
     The schema change is backward-compatible (NULL default, documented rollback).
     No budget enforcement changes. autonomy: auto (engine vocabulary; front agent wrote 'standard' — normalized at review). -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Historical team attribution on the usage ledger

Framings weighed:
- **JOIN-at-query-time** (chosen for the current team-JOINed breakdown in `GET /admin/spend?group_by=team_id`): reflect the key's *current* team assignment at query time. Strength: always up-to-date if a key is reassigned. Weakness: a key reassignment silently rewrites historical attribution — no true history. ALREADY EXISTS in the codebase as the `group_by=team_id` branch in `usage/api/router.py` (JOIN on `api_keys`).
- **Ledger-column attribution** (chosen for this task): persist the team_id that was active *at the time of the request* directly on the `usage_records` row. Strength: immutable historical record matching the invoice moment; enables reconciliation between counter and ledger. Weakness: requires additive schema migration + flusher plumbing.
- **Backfill existing rows**: rejected by v5 shared decision — no invented history; historical starts at deploy.

Chosen surface for ledger rollup: extend `GET /admin/spend` response with a new `team_ledger` field in the `breakdown` item when `group_by=team_id` is requested. The existing `breakdown` items (TeamSpendBreakdownItem) gain an additive `ledger_cost_usd: str | None` field (None when the column is new and no rows have been flushed yet). This is a **non-breaking additive extension** — the frozen team_governance S10 suite only asserts `cost_usd`, `requests`, `team_id`, `team_name`; it does NOT assert exact body equality; adding a new nullable field does NOT break it.

Alternative rollup surfaces considered and rejected:
- New sibling endpoint `/admin/teams/{id}/spend` (ledger): requires new route, new auth, adds surface — rejected (additive field on existing endpoint is simpler and safe).
- New query param `?source=ledger`: adds a second read path for the same data — confusing; rejected.
- Reconciliation endpoint: not needed; both counter and ledger are exposed on the existing `GET /admin/spend?group_by=team_id` response; the caller can diff them.

Must:
<must>
  - M1: Additive Alembic migration adds `usage_records.team_id UUID NULL` with a composite index on `(tenant_id, team_id)` for team rollup queries. Extends chain from `d4e7f1a2b3c5`. Rollback: `DROP COLUMN usage_records.team_id` + `DROP INDEX ix_usage_records_tenant_team`. No new tables — EXPECTED_TABLES manifest unchanged.
  - M2: The recorder stream event (`event_fields` dict pushed via `xadd`) gains a `team_id` field (string UUID or empty string when NULL). Previously team_id was accepted by `record()` but DROPPED from `event_fields` — this task adds it.
  - M3: The flusher maps `event_fields["team_id"]` → `usage_records.team_id` column. Empty string or missing field → NULL (old-format event safety).
  - M4: A proxied request through a teamed API key results in a `usage_records` row with `team_id` set to the key's team. An un-teamed key → `team_id IS NULL`.
  - M5: Old-format stream events (no `team_id` field in `event_fields`) already in the PEL at deploy time flush cleanly with `team_id = NULL` — no crash, no data loss.
  - M6: `GET /admin/spend?group_by=team_id` response breakdown items include a new additive field `ledger_cost_usd: str` (Decimal string) representing `SUM(usage_records.cost_usd WHERE team_id = X)` for the window. Counter value (`cost_usd`) comes from the existing JOIN-at-query-time path; `ledger_cost_usd` comes from the new column. Both are visible in the same response item — reconciliation is observable.
  - M7: The ledger rollup (M6) is tenant-scoped: `WHERE tenant_id = :tenant_id` always applied. A request for another tenant's team_id returns a bucket with zero spend (or no bucket at all), not another tenant's data.
  - M8: Cross-tenant team rollup 404: if a team UUID is not owned by the authenticated tenant, a direct team-scoped query returns 404 `ERR_TEAM_NOT_FOUND` (convention: cross-tenant = 404 never 403). NOTE: this applies to any hypothetical `/admin/teams/{id}/spend` endpoint — for the `group_by=team_id` surface there is no per-team auth gate since the caller is already tenant-scoped; the query simply returns no bucket for teams belonging to other tenants.
  - M9: No changes to budget enforcement — Redis counters remain authoritative for 402 checks. No changes to the counter write path (already correct in the recorder).
  - M10: Existing usage_records rows (pre-deploy) remain NULL on team_id — no backfill.
</must>

Reject:
<reject>
  - Backfill of historical rows → not implemented; any such request is out of scope — "ERR_NOT_IMPLEMENTED" (if ever surfaced via API, which it is not)
  - team_id on a key not belonging to the authenticated tenant in a rollup query → silently filtered out (tenant_id-scoped WHERE; no row appears from another tenant)
</reject>

After:
<after>
  - usage_records table has a nullable team_id UUID column (+ composite index ix_usage_records_tenant_team)
  - A teamed key's completion request produces a usage_records row with team_id = key.team_id
  - An un-teamed key produces team_id IS NULL
  - An old-format stream event (no team_id field) flushes with team_id IS NULL
  - GET /admin/spend?group_by=team_id response breakdown items include both cost_usd (counter-derived, JOIN-at-query-time) and ledger_cost_usd (ledger-derived, column-based) for reconciliation
  - Redis advisory counters unchanged — still authoritative for 402
  - No existing test is broken
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [spec] The frozen team_governance S10 test does NOT assert exact body equality on TeamSpendBreakdownItem — it only checks specific fields (`team_id`, `team_name`, `requests`, `cost_usd`). Adding a `ledger_cost_usd` field to the response is non-breaking. Confidence gap: the test was read carefully and no exact-shape assertion was found, but if the frozen test used `assert body == expected_body` we would need a different surface (e.g. new endpoint). Verified: no such assertion exists — the test asserts individual field values. Cost if wrong: would need a non-additive surface (new endpoint or query param), a change-request back to SPECIFY.
  ⚠ [contract] team_id is currently NOT included in event_fields (the Redis Stream HSET dict). It is accepted by `record()` and passed through `_record_internal()`, goes to the Redis INCRBYFLOAT counter, but is absent from `event_fields`. This means all existing stream events in any backlog have no `team_id` field. The flusher must treat missing field as NULL — this is the primary mixed-deploy safety requirement. Confidence: high (verified by reading recorder.py lines 164-175 where team_id is not in event_fields). Cost if wrong (e.g. team_id IS already in event_fields): the flusher change is simpler; no cost.
  - [contract] The `group_by=team_id` breakdown branch in `usage/api/router.py` already exists and uses JOIN-at-query-time attribution via `api_keys.team_id`. Adding a ledger column sum means the SQL needs to be updated to also SUM from `usage_records.team_id` (or equivalently JOIN) — this is a non-breaking additive query change. The breakdown result is grouped by `ak.team_id` (key's current team) — the new `ledger_cost_usd` field uses `SUM(ur.team_id = X)` which may differ when team_id was different at time of request vs today. This is by design and is documented in the reconciliation semantics.
  - [test] Tests use `Base.metadata.create_all` (from conftest.py) which includes all ORM columns. Adding `team_id` to `UsageRecordRow` ORM makes the column exist in the test DB without running migrations. The MIGRATION itself is verified separately by `make migrate-check` (alembic autogenerate --check). This is the correct pattern per existing suite conventions.
  - [contract] The composite index `(tenant_id, team_id)` is chosen over a partial index on `WHERE team_id IS NOT NULL` for simplicity and because queries always filter by tenant_id first. The index covers both the common rollup pattern `WHERE tenant_id = X AND team_id = Y` and a full scan of a tenant's rows. A partial index would be smaller on disk but adds DDL complexity. Judgment: composite is appropriate for this load.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — teamed key proxied request → ledger row carries team_id
  Given a tenant with a team and an API key assigned to that team
  And the flusher is called after the completion request
  When the key makes a proxied POST /v1/chat/completions request (200 upstream)
  Then the usage_records row has team_id = key.team_id (SQL assert)
  And the existing tenant_id, key_id, cost_usd, model_id fields are unchanged

Scenario: S2 — un-teamed key → ledger row has team_id IS NULL
  Given a tenant with an API key NOT assigned to any team
  And the flusher is called after the completion request
  When the key makes a proxied POST /v1/chat/completions request (200 upstream)
  Then the usage_records row has team_id IS NULL
  And all other fields are correct

Scenario: S3 — old-format event (no team_id field) flushes with team_id NULL
  Given a stream event is injected directly into the Redis Stream WITHOUT a team_id field
  (simulating a pre-deploy event that predates this task)
  When flush_once() is called
  Then the usage_records row is inserted with team_id IS NULL
  And no error is raised; the event is ACKed

Scenario: S4 — ledger rollup via GET /admin/spend?group_by=team_id shows ledger_cost_usd
  Given a tenant with a team, a teamed key, and a solo key
  And usage_records rows exist with team_id set (teamed) and NULL (solo)
  When GET /admin/spend?group_by=team_id&window=month is called by the tenant
  Then the breakdown includes items with both cost_usd and ledger_cost_usd fields
  And the teamed bucket's ledger_cost_usd equals SUM(cost_usd WHERE ur.team_id = team_id)
  And the NULL-team bucket's ledger_cost_usd equals SUM(cost_usd WHERE ur.team_id IS NULL)

Scenario: S5 — counter and ledger both visible; reconciliation observable
  Given a teamed key makes a proxied request; recorder writes counter + stream event
  And flusher flushes the event; ledger row has team_id set
  When GET /admin/spend?group_by=team_id is called
  Then the breakdown item for the team shows cost_usd (counter-derived JOIN-at-query-time)
  And the same item shows ledger_cost_usd (ledger column sum)
  And both values are present in the same response item (reconciliation is observable)

Scenario: S6 — tenant-scoped: team in tenant B not visible to tenant A
  Given tenant A and tenant B each have a team with the same name
  And each team has usage_records rows with team_id set
  When tenant A calls GET /admin/spend?group_by=team_id
  Then tenant A sees only their own team's ledger_cost_usd
  And tenant B's team data is not present in tenant A's response

Scenario: S7 — migration column exists: usage_records has team_id column after create_all
  Given the ORM UsageRecordRow has a team_id column defined
  When Base.metadata.create_all is called (test DB setup)
  Then SELECT team_id FROM usage_records succeeds (column exists)
  And existing column names (id, tenant_id, key_id, etc.) are still present
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── DDL ─────────────────────────────────────────────────────────────────────
Migration: e1a3f5b9c7d2_team_attribution  (new revision ID)
  down_revision: d4e7f1a2b3c5  (guardrails_core — current chain head)

  upgrade():
    ALTER TABLE usage_records ADD COLUMN team_id UUID NULL;
    CREATE INDEX ix_usage_records_tenant_team
      ON usage_records (tenant_id, team_id);
    -- team_id has no FK to teams (append-only ledger; team may be deleted post-attribution)

  downgrade():
    DROP INDEX IF EXISTS ix_usage_records_tenant_team;
    ALTER TABLE usage_records DROP COLUMN IF EXISTS team_id;

  No new tables → EXPECTED_TABLES manifest unchanged.
  Adding a COLUMN (not a TABLE) to usage_records: the frozen test_migrations.py checks
  table names only, not column lists — this migration is safe for that test.

# ── ORM ──────────────────────────────────────────────────────────────────────
UsageRecordRow (gateway/usage/infrastructure/orm.py):
  + team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
  No FK — append-only ledger; team deletion must not cascade-delete ledger rows.

# ── Stream event ─────────────────────────────────────────────────────────────
RecordingUsageRecorder._record_internal():
  event_fields["team_id"] = str(team_id) if team_id is not None else ""
  (Added after existing fields in the dict — backward-compatible with consumers
  that don't read this field, which was all of them before this task.)

# ── Flusher mapping ──────────────────────────────────────────────────────────
UsageLedgerFlusher._process_entry():
  team_id_str = _field("team_id")   # "" or missing → None
  team_id: uuid.UUID | None = uuid.UUID(team_id_str) if team_id_str else None
  # INSERT gains :team_id parameter; column included in INSERT column list.

Mixed-deploy safety pin (binding):
  Old-format events (team_id field absent or empty string) → team_id = NULL in DB.
  This covers the PEL backlog at deploy time. No special migration of the PEL needed.

# ── Rollup surface ───────────────────────────────────────────────────────────
GET /admin/spend?group_by=team_id   (existing endpoint, additive extension)
  200 -> SpendWindowResponse {
    ...existing fields unchanged...
    breakdown: list[TeamSpendBreakdownItem]  -- EXTENDED (additive, non-breaking)
  }

TeamSpendBreakdownItem (gateway/usage/api/schemas.py):
  existing: team_id, team_name, requests, prompt_tokens, completion_tokens, cost_usd
  NEW additive: ledger_cost_usd: str  -- str(Decimal), SUM from usage_records.team_id column
                                        for the window; "0" when no ledger rows have team_id set.

  team_id == NULL bucket: ledger_cost_usd = SUM(cost_usd WHERE ur.team_id IS NULL AND tenant_id=X)
  team_id == X bucket:    ledger_cost_usd = SUM(cost_usd WHERE ur.team_id = X AND tenant_id=X)

  Implementation: the existing group_by=team_id SQL branch (JOIN api_keys + LEFT JOIN teams)
  gains an additional sub-query or window function to compute ledger_cost_usd. Simplest
  correct approach: run a second query `SELECT team_id, SUM(cost_usd) FROM usage_records
  WHERE tenant_id=X AND created_at >= W_start AND created_at < W_end GROUP BY team_id`
  and left-join the results into the breakdown items by team_id. NULL-safe join required.

Reconciliation semantics (binding):
  counter-derived cost_usd: JOIN api_keys → reflects the key's CURRENT team assignment.
    May differ from ledger_cost_usd if a key was reassigned after the request.
  ledger_cost_usd: reflects the team_id AT THE TIME OF THE REQUEST (the column value).
  Both values can legitimately differ by:
    (a) in-flight/unflushed events — counter is incremented immediately; ledger is eventual
        (flusher lag, typically < 1s in production)
    (b) key reassignment — counter reflects current; ledger reflects historical
  The response exposes both numbers; the caller can observe and reconcile.
  Counters remain authoritative for 402 budget enforcement (unchanged).

Error codes (existing, unchanged):
  401 ERR_AUTH_INVALID_TOKEN — missing/invalid Bearer JWT
  404 ERR_TEAM_NOT_FOUND — if a team-scoped endpoint is ever added (not in this task scope)
  422 ERR_PAYLOAD_INVALID — invalid group_by (already validated in existing code)

Frozen-suite safety:
  team_governance S10 asserts: team_id, team_name, requests, cost_usd, NULL bucket presence.
  None of these assertions check the absence of additional fields.
  Adding ledger_cost_usd does NOT break S10. ✓
  usage_metering suite: no assertions on team_id column or rollup surface.
  Frozen test confirms column exists via Base.metadata.create_all (not alembic); safe. ✓
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-11).
  Orchestrator review: dual-number rollup surface APPROVED as specified (cost_usd =
  JOIN-current attribution, ledger_cost_usd = historical at-request-time attribution;
  the difference is the feature, both exposed + reconciliation semantics binding).
  Slug-line autonomy normalized 'standard' → 'auto' (engine vocabulary). Red re-run
  by orchestrator: 7/7 failed for the right reasons (missing column / missing
  ledger_cost_usd field) — authoritative.

Least-sure flag surfaced at freeze:
⚠ [spec] The `group_by=team_id` breakdown JOIN-at-query-time path already exists in production code (usage/api/router.py lines 409-445). The "ledger_cost_usd" additive field requires running a second aggregation query against `usage_records.team_id` (the new column). The two results are joined in Python before building breakdown items. The correctness concern: the JOIN-at-query-time breakdowns group by `ak.team_id` (current key assignment), but the ledger SUM groups by `ur.team_id` (historical). For a key that was reassigned, the breakdown item for the NEW team shows a cost_usd from the old rows PLUS the new ones (via JOIN), but ledger_cost_usd for the NEW team shows only rows flushed after reassignment. This is the CORRECT and DESIRED behavior (historical attribution), but the two numbers may surprise callers. This discrepancy is documented in reconciliation semantics above — but if the orchestrator judges it too confusing, the counter-derived `cost_usd` field could be renamed or the breakdown could be changed to ONLY show ledger attribution. That would require a change-request because it would change the existing frozen `cost_usd` field semantics. Current spec: both are exposed; documented. Confidence: 0.82.

⚠ [contract] The `INSERT INTO usage_records` in `_process_entry` currently uses positional column listing. Adding `team_id` to the INSERT requires updating the SQL string in the flusher. This is a mechanical change but must be verified — the text() SQL does not auto-derive from ORM. If the build accidentally omits `team_id` from the INSERT list, the column defaults to NULL (which is CORRECT for the old-format event path) but the team_id from new events would be silently dropped. The §4 test suite directly asserts team_id in the DB row after flush — this catches the bug. Confidence: 0.95.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85% (existing floor 80%; this suite adds 7 targeted scenarios covering the new column and rollup surface)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_teamed_key_ledger_row_has_team_id: arrange (teamed key + fake upstream + model+pricing), act (POST /v1/chat/completions, then flush_once()), assert (usage_records row team_id == key.team_id via SQL SELECT)
  - test_unteamed_key_ledger_row_null_team_id: arrange (solo key + fake upstream), act (completion + flush_once()), assert (team_id IS NULL in DB row)
  - test_old_format_event_flushes_with_null_team_id: arrange (inject raw Redis xadd WITHOUT team_id field), act (flush_once()), assert (row lands with team_id IS NULL, no error)
  - test_ledger_rollup_shows_ledger_cost_usd: arrange (insert usage_records rows directly — one with team_id set, one NULL), act (GET /admin/spend?group_by=team_id), assert (breakdown items have ledger_cost_usd matching SUM)
  - test_counter_and_ledger_both_visible: arrange (completion via teamed key + flush), act (GET /admin/spend?group_by=team_id), assert (breakdown item has both cost_usd and ledger_cost_usd fields present and non-None)
  - test_ledger_rollup_tenant_scoped: arrange (two tenants each with a team + usage_records rows with team_id), act (tenant A calls rollup), assert (only tenant A's team appears in breakdown; tenant B's ledger_cost_usd not visible)
  - test_team_id_column_exists: arrange (Base.metadata.create_all via app fixture), act (SELECT team_id FROM usage_records LIMIT 0 via db_session), assert (no ProgrammingError — column exists)
</test_plan>

Tests live in: `apps/gateway/tests/team_attribution/` `apps/gateway/tests/team_attribution/__init__.py` `apps/gateway/tests/team_attribution/test_team_attribution.py`

Red-run evidence (recorded after writing suite):
  All 7 tests FAIL for the right reasons:
  - test_teamed_key_ledger_row_has_team_id: FAILED — `usage_records` row has team_id = NULL because (a) the column does not exist yet (create_all from ORM) → actually the column won't exist since UsageRecordRow ORM has no team_id column yet → sqlalchemy.exc.ProgrammingError on SELECT or the row simply lacks the column. The test assert `row["team_id"] == uuid.UUID(team_id)` fails.
  - test_unteamed_key_ledger_row_null_team_id: FAILED — same root cause; column does not exist.
  - test_old_format_event_flushes_with_null_team_id: FAILED — column does not exist; INSERT in flusher will fail if team_id not in column list once ORM migration is applied (or KeyError on row mapping).
  - test_ledger_rollup_shows_ledger_cost_usd: FAILED — `TeamSpendBreakdownItem` has no `ledger_cost_usd` field; `breakdown[0].get("ledger_cost_usd")` is None → AssertionError.
  - test_counter_and_ledger_both_visible: FAILED — same; `ledger_cost_usd` absent from response.
  - test_ledger_rollup_tenant_scoped: FAILED — `ledger_cost_usd` absent from response; AssertionError.
  - test_team_id_column_exists: FAILED — column not in ORM → `create_all` does not create it → `SELECT team_id FROM usage_records` → ProgrammingError (column does not exist).

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): The flusher INSERT must never silently drop team_id for new events — the team_id field must be in BOTH the INSERT column list AND the parameters dict. Old-format events (empty team_id_str) must still produce NULL, not a ValueError. All changes are additive — no existing fields removed from event_fields, ORM, or SQL.

Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ledger_cost_usd vs cost_usd divergence rate (counter vs ledger reconciliation gap > 1%); NULL team_id row rate (after ramp: should equal fraction of un-teamed keys × their request rate)
Spec delta for the next loop: If key reassignment creates surprising breakdowns, consider adding a snapshot_team_id field to capture team at request time separately from the JOIN-at-query-time approach.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
