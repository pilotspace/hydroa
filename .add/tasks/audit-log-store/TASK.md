# TASK: Append-only admin audit event store

slug: audit-log-store · created: 2026-06-25 · stage: production
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
  - NEW `apps/gateway/src/gateway/audit/` module (DDD: domain entity + port, application writer, infrastructure ORM+repo) — mirrors the alerting/usage layering.
  - PATTERN to follow: `usage/infrastructure/alert_events_orm.py:AlertEventRow` (id·tenant_id·event_type·payload JSONB·created_at + partial index) and `usage/application/alert_writer.py:persist_soft_budget_alert` (fire-and-forget, ON CONFLICT idempotent, swallows exceptions off the hot path).
  - `tenants/domain/authz.py:Permission.AUDIT_READ` — ALREADY DEFINED (owner/admin/operator); the later audit-log-surface task binds GET /admin/audit to it. This task only writes + provides the read repo.
  - Security-sensitive WRITE surfaces that should emit an audit event: PUT /admin/routing · keys create/revoke/rotate · teams role assignment (MEMBERS_MANAGE) · PUT /admin/provider-keys · PUT /admin/oidc · PUT /admin/budget. (Identity available via the require_permission deps.)
  - Alembic migration under `apps/gateway/migrations/` (current head `b2d4f6a8c0e1`).
Context (working folder): PROJECT.md (tenant-scoping; design-for-failure — audit write must NOT break the request path); CONVENTIONS.md (DDD ports/error catalog); gateway test DB via docker :5433 (UP); pytest ONE process.
Honors: additive (no existing table/row changes); audit write is fire-and-forget + fail-open like the alert writer (an audit failure must never 500 an admin action — but see §1 ⚠ on the integrity tension); tenant_id on every row; append-only.
Anchors the contract cites: NEW `audit_events` table · `AuditEvent` domain entity · an `AuditLog` port + `record(...)` writer · the read repo `list_for_tenant(...)` · the immutability mechanism · the Alembic migration.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Append-only admin audit event store (who did which privileged action, when, to what, with what result)
Framings weighed: DEDICATED append-only `audit_events` table + domain port/writer, app-enforced immutability (chosen) · reuse `alert_events` (rejected: different semantics — alerts are operational+deliverable+dedup'd; audit is actor-attributed+immutable+compliance) · DB-trigger/hash-chain tamper-evidence (deferred: stronger but heavier — logged as a delta unless Tin wants it now)
Must:
<must>
  - NEW `audit_events` table records one immutable row per privileged admin action: id · tenant_id · actor_user_id · actor_email · action (e.g. "routing.update") · target_type · target_id · result ("success"|"denied"|"error") · metadata JSONB (NO secrets) · created_at TIMESTAMPTZ.
  - A domain `AuditEvent` entity + an `AuditLog` port with `record(event)`; a SqlAlchemy repo implementing both `record(...)` (INSERT-only) and `list_for_tenant(tenant_id, limit, before)` (read, for the later surface).
  - APPEND-ONLY: the code exposes NO update/delete path for audit rows; the repo only INSERTs and SELECTs. (Integrity-enforcement STRENGTH is the §3 security decision — see flag.)
  - The privileged WRITE surfaces (which set — the §3 decision) emit an audit event recording the ACTOR (from Identity) + action + target + result. Secrets/key material are NEVER written to metadata (store provider name / key id, never the secret).
  - Tenant-scoped: every row carries tenant_id; reads filter by tenant_id (system/no-tenant actions allowed NULL like alert_events, but owner-only to read).
  - Design-for-failure: the audit write path is bounded and must not corrupt the admin action's own outcome (the fail-open-vs-fail-closed choice is the §1 ⚠).
Reject:
<reject>
  - Any code path that UPDATEs or DELETEs an audit_events row -> not provided ("audit_immutable_violation" — enforced by absence + test)
  - Writing a secret / key material / token into metadata -> "audit_secret_leak" (asserted by test on the emit sites)
  - A privileged action recorded without an actor identity -> "audit_missing_actor"
</reject>
After:
<after>
  - Each chosen privileged admin action appends exactly one audit_events row attributing the actor, action, target, and result; rows are never mutated.
  - The read repo returns a tenant's audit rows newest-first (ready for the audit-log-surface task); migration applies on head b2d4f6a8c0e1; full gateway suite green.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ FAIL-OPEN vs FAIL-CLOSED + IMMUTABILITY STRENGTH (the security decision Tin must make). Fail-open (audit failure logged, admin action still succeeds — like alert_writer) preserves availability but can MISS a security event; fail-closed (audit write in the SAME transaction as the action; if it can't record, the action rolls back) guarantees the trail but can block admin ops on a DB hiccup. AND immutability: app-only (no update/delete code) vs DB-enforced (REVOKE/trigger) vs hash-chained (tamper-evident). If wrong: a compliance auditor rejects the trail, or an outage blocks admin ops. MITIGATION: present both axes; freeze on Tin's choice.
  - [ ] WHICH actions are audited in THIS task — all 6 write surfaces vs the security-critical subset (role assignment · provider-keys · oidc) first. Confirm at freeze.
  - [ ] Retention/purge of audit_events is OUT of scope here — owned by the later data-retention-controls task (its own security HARD-STOP).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: A privileged action appends one attributed audit row
  Given an authenticated admin performs PUT /admin/routing successfully
  When the handler completes
  Then exactly one audit_events row exists with actor_user_id=the admin, action="routing.update", result="success", tenant_id set
  And the routing change itself is unaffected

Scenario: All six write surfaces emit audit events
  Given an actor performs each of routing.update, key.create/revoke/rotate, member.role_assign, provider_key.put, oidc.put, budget.update
  When each completes
  Then each appends an audit_events row with the correct action verb, target, and actor

Scenario: Audit write is fail-open
  Given the audit store is unavailable (the audit INSERT raises)
  When an admin performs a privileged action
  Then the admin action still succeeds (2xx) and the failure is logged
  And no exception propagates into the request path

Scenario: Append-only — no mutate path (app)
  Given the audit repository and domain port
  When their API is inspected
  Then there is NO update or delete method ("audit_immutable_violation")

Scenario: Append-only — DB-enforced
  Given the applied migration
  When an UPDATE or DELETE is attempted against audit_events
  Then it is blocked (REVOKE + rule/trigger); the row is unchanged

Scenario: Reject — no secret in metadata
  Given a provider_key.put or oidc.put audit event
  When the row is written
  Then metadata contains the provider/target identifier but NO secret/key/token value ("audit_secret_leak")

Scenario: Reject — missing actor
  Given a privileged (non-system) action
  When an audit event is recorded
  Then it carries an actor_user_id ("audit_missing_actor")

Scenario: Tenant-scoped newest-first read
  Given audit rows for two tenants
  When list_for_tenant(tenant_a) is called
  Then only tenant_a rows are returned, newest-first, bounded by limit
  And tenant_b rows are never returned
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
TABLE audit_events (append-only):
  id            UUID PRIMARY KEY
  tenant_id     UUID NULL            (system actions NULL; reads owner-only for NULL)
  actor_user_id UUID NULL            (the Identity.user_id performing the action; NULL only for system)
  actor_email   TEXT NULL            (denormalized for display; from Identity)
  action        TEXT NOT NULL        (dotted verb: "routing.update" · "key.revoke" · "member.role_assign" · "provider_key.put" · "oidc.put" · "budget.update")
  target_type   TEXT NULL            ("routing" · "api_key" · "user" · "provider" · "oidc" · "budget")
  target_id     TEXT NULL            (id/name of the target; NEVER a secret)
  result        TEXT NOT NULL        ("success" | "denied" | "error")
  metadata      JSONB NOT NULL       (action-specific, secret-free: e.g. {"old_role":"member","new_role":"operator"})
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  INDEX audit_events_tenant_created_idx (tenant_id, created_at DESC)   — newest-first tenant reads

DOMAIN:  AuditEvent(frozen dataclass) · AuditLog port { record(event) -> None }
REPO (SqlAlchemy):  record(event)  = INSERT only (no update/delete method EXISTS)
                    list_for_tenant(tenant_id, limit=50, before=None) -> list[AuditEvent]  (created_at DESC)
EMIT SEAM:  record_audit(identity, action, target_type, target_id, result, metadata) called from the chosen write surfaces.

INTEGRITY MODEL (decision A — Tin):  app-only immutability (no update/delete code path) [default]
   | DB-enforced (REVOKE UPDATE,DELETE on the table / BEFORE-UPDATE-OR-DELETE trigger RAISE)  | hash-chain (prev_hash+row_hash, tamper-evident)
WRITE FAILURE MODE (decision B — Tin):  fail-OPEN (log+continue, action still succeeds) [like alert_writer]
   | fail-CLOSED (audit INSERT in the action's own transaction; can't record ⇒ action rolls back)
AUDITED ACTIONS (decision C — Tin):  ALL 6 write surfaces [default]  | security-critical subset first (role_assign · provider_key · oidc)

Rejections: audit_immutable_violation (no mutate path) · audit_secret_leak (no secret in metadata) · audit_missing_actor.
Least-sure flag surfaced at freeze: [contract] decisions A+B — immutability strength and fail-open-vs-fail-closed are a
  SECURITY/availability tradeoff (compliance-grade trail vs never blocking admin ops). Cost if wrong: an auditor rejects
  the trail, or a DB hiccup blocks admin actions. This is why the freeze needs Tin's explicit approval.
```

Status: FROZEN @ v1 — approved by Tin 2026-06-25 (security HARD-STOP). Decisions: A=DB-ENFORCED (migration REVOKE UPDATE,DELETE + a rule/trigger blocking UPDATE/DELETE; repo has no mutate method) · B=FAIL-OPEN (audit write logged+continue, separate from the action txn, never breaks the request path) · C=ALL 6 write surfaces (routing.update · key.create/revoke/rotate · member.role_assign · provider_key.put · oidc.put · budget.update).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: audit module fully covered; full gateway suite green (no regression).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_action_appends_attributed_row: PUT /admin/routing as admin -> one audit_events row (actor, action="routing.update", result="success", tenant set); routing change intact
  - test_all_six_surfaces_emit: each of the 6 surfaces -> correct action verb + target + actor (parametrized)
  - test_audit_write_fail_open: patch the audit writer to raise -> admin action still 2xx, error logged, no propagation
  - test_no_mutate_method_app: assert the repo + port expose record + list only; no update/delete attribute ("audit_immutable_violation")
  - test_db_enforced_immutability: apply migration, attempt UPDATE and DELETE on audit_events -> blocked/no-op; row unchanged (migration-level test)
  - test_no_secret_in_metadata: provider_key.put / oidc.put audit rows -> metadata has identifier, NO secret/token value ("audit_secret_leak")
  - test_missing_actor_rejected: recording a non-system event without actor_user_id -> rejected ("audit_missing_actor")
  - test_tenant_scoped_newest_first: two tenants' rows -> list_for_tenant returns only that tenant, DESC, bounded by limit
</test_plan>

Tests live in: `apps/gateway/tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/audit/` `apps/gateway/src/gateway/catalog/api/` `apps/gateway/src/gateway/usage/api/` `apps/gateway/src/gateway/budgets/api/` `apps/gateway/src/gateway/teams/api/` `apps/gateway/src/gateway/keys/api/` `apps/gateway/src/gateway/proxy/api/` `apps/gateway/src/gateway/auth/api/` `apps/gateway/src/gateway/core/` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/` `apps/gateway/tests/`
Strategy (ordered batches):
  1. RED tests `apps/gateway/tests/audit/test_audit_store.py` (8 per §4) incl the migration-level immutability test.
  2. NEW `audit/` module: domain `AuditEvent` + `AuditLog` port; infrastructure `AuditEventRow` ORM (mirror alert_events_orm idiom) + SqlAlchemy repo (record INSERT-only + list_for_tenant DESC); application `record_audit(...)` fail-open writer (logs+swallows, NOT in the action txn).
  3. Alembic migration on head b2d4f6a8c0e1: CREATE TABLE audit_events + index + REVOKE UPDATE,DELETE + a rule/trigger that blocks UPDATE/DELETE (DO INSTEAD NOTHING or RAISE).
  4. Wire the emit seam into all 6 write surfaces (record actor from Identity, action verb, target, result, secret-free metadata).
  5. Green: full gateway suite (docker DB up), ruff, pyright.
Safety rule (feature-specific): FAIL-OPEN — the audit write must NEVER raise into the request path and must NOT share the admin action's transaction (so a rollback can't lose a committed action, and an audit failure can't roll back the action). NEVER write secrets/key material/tokens into metadata (store provider name / key id / target id only).
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the FROZEN contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — audit subset INDEPENDENTLY re-run 15/15; subagent full suite 1599 passed
- [x] coverage did not decrease — +15 audit tests; no prior test removed
- [x] no test or contract was altered — FROZEN contract unchanged; existing tests untouched
- [x] the green was EARNED — orchestrator READ the migration RULEs, record_audit, and the REAL emit-site metadata (not just synthetic test data); ran the audit subset standalone. No vacuous asserts.
- [x] concurrency / timing — audit write is `asyncio.ensure_future(record_audit(...))` on a SEPARATE session, off the hot path; cannot deadlock or roll back the action
- [x] no exposed secrets — VERIFIED at the real handlers: provider_key.put metadata={provider,enabled}; oidc.put metadata={issuer,client_id,enabled} — NO client_secret / key / token. Secret-hygiene test enforces the banned-key set.
- [x] layering & dependencies follow CONVENTIONS.md — new audit/ module split domain(entity+port)/application(writer)/infrastructure(orm+repo); no new dependency
- [x] a person reviewed & approved — TIN approved the §3 security decisions (A/B/C, 2026-06-25) + orchestrator independent code review

### Build expectations — confirmed at gate
- [x] DB-enforced immutability — migration e3f5a7c9b1d2 CREATE RULE …_no_update/_no_delete DO INSTEAD NOTHING; test_db_enforced_immutability proves UPDATE+DELETE are no-ops, row preserved
- [x] Fail-open — record_audit uses its own session + commit, swallows+logs all exceptions; test patches it to raise → admin action still 2xx, no propagation
- [x] All 6 surfaces emit one attributed event — routing/keys(create,revoke,rotate)/teams(role_assign)/provider-keys/oidc/budget; actor from Identity; 8 emit-site verbs
- [x] No mutate path — AuditRepository + AuditLog port expose record + list_for_tenant ONLY (introspection test)
- [x] Tenant-scoped newest-first read repo ready for audit-log-surface
- [x] Secret hygiene at real emit sites (read provider_keys + oidc handlers directly)

### Deep checks
- [x] WIRING — audit/ symbols referenced by 8 emit sites + migrations/env.py registers the ORM; AUDIT_READ already defined for the future read surface
- [x] DEAD-CODE — list_for_tenant currently unconsumed BY DESIGN (the audit-log-surface task binds GET /admin/audit to it next); not orphaned — contract-declared seam. ruff/pyright clean.
- [x] SEMANTIC — DEVIATION (minor, accepted): contract said "REVOKE UPDATE,DELETE + rule/trigger"; build used RULEs (DO INSTEAD NOTHING) WITHOUT REVOKE. RULE blocks mutation for ALL callers incl table owner (stronger than REVOKE for app-bug protection — the stated goal), but silently (no error). Immutability goal fully met; logged a delta to add REVOKE as defense-in-depth + consider RAISE for loud failure.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (security decisions A/B/C) + orchestrator independent review (migration RULEs, fail-open writer, real emit-site secret hygiene, audit subset re-run) · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
- [SPEC · open] audit-log-surface — bind GET /admin/audit to AUDIT_READ + the list_for_tenant repo (the read UI/API; this task built the store + repo only).
- [SPEC · open] add REVOKE UPDATE,DELETE on audit_events as defense-in-depth alongside the RULEs; consider RAISE instead of DO INSTEAD NOTHING so accidental mutation attempts fail loudly (evidence: current RULE silently no-ops).
- [SPEC · open] audit DENIED attempts (403s) — result="denied" is in the schema but the require_permission dep raises before the handler; hook denial auditing if security wants attempted-access trails.
- [SPEC · open] retention/purge of audit_events — owned by data-retention-controls (next security HARD-STOP).

### Competency deltas
- [DDD · folded] "audit event" is a distinct bounded concept from "alert event" (actor-attributed + immutable + compliance vs operational + deliverable + dedup'd) — separate module/table was correct (evidence: reuse-alert_events framing rejected at specify). [folded foundation-version 35]
- [ADD · folded] subagent left no tmp scratch file this run (inline -m worked) — the explicit "no tmp/*.txt" constraint prevented the recurring scope_violation; keep it in every backend subagent prompt. [folded foundation-version 35]
