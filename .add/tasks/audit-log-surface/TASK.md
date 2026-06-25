# TASK: Audit log read API + dashboard viewer

slug: audit-log-surface · created: 2026-06-25 · stage: production
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
  - NEW `GET /admin/audit` in `usage/api/router.py` (or a small new audit router) — mirrors `get_alerts` (line ~616): paginated `{items,total}`, manual `_parse_pagination` (limit 1..100 default 50, offset>=0 → PAYLOAD_INVALID on bad), `_coerce_payload` for JSONB.
  - AUTH: `tenants/domain/authz.py:Permission.AUDIT_READ` (owner/admin/operator) via `require_permission(Permission.AUDIT_READ)`.
  - READ: `audit/infrastructure/audit_repository.py:AuditRepository.list_for_tenant(tenant_id, limit, before)` (already built; created_at DESC) — may need an offset/total variant to match the alerts envelope.
  - FE: NEW `apps/dashboard/app/(app)/app/audit/page.tsx` mirroring the `(app)/app/alerts` viewer (table of rows, pagination), reachable from the dashboard nav.
Context (working folder): the alerts viewer (`GET /admin/alerts` + `/app/alerts` page) is the exact analog; dashboard BFF/api-client pattern; vitest. gateway test DB :5433 UP.
Honors: tenant-scoping (list_for_tenant filters tenant_id); read-only (no mutation of audit rows); AUDIT_READ allowlist; dashboard a11y bar (v23/v24); no secret rendered (metadata is already secret-free).
Anchors the contract cites: `GET /admin/audit` · `AuditListResponse`/`AuditEventItem` · `require_permission(AUDIT_READ)` · `AuditRepository` read · the `/app/audit` dashboard page.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Read surface for the audit trail — a tenant-scoped paginated API + a dashboard viewer
Framings weighed: mirror the existing alerts read surface (endpoint + envelope + dashboard table) (chosen) · CSV export only (rejected: no in-app visibility) · raw SQL console (rejected: unsafe)
Must:
<must>
  - `GET /admin/audit?limit&offset` returns `{ items: AuditEventItem[], total }` for the caller's tenant, newest-first, gated by `require_permission(Permission.AUDIT_READ)` (owner/admin/operator pass; billing_admin/viewer/member → 403).
  - Each item exposes id · actor_email · action · target_type · target_id · result · metadata · created_at (metadata already secret-free from audit-log-store).
  - Pagination mirrors alerts: limit 1..100 (default 50), offset >=0; bad value → PAYLOAD_INVALID.
  - Dashboard `/app/audit` page renders the rows in a table with pagination, reachable from nav; read-only; WCAG-AA.
  - Tenant-scoped: a caller only ever sees their tenant's audit rows.
</must>
Reject:
<reject>
  - A role lacking AUDIT_READ (billing_admin/viewer/member) -> 403 "ERR_AUTH_FORBIDDEN"
  - limit/offset out of range or non-integer -> "ERR_PAYLOAD_INVALID"
  - Any attempt to mutate audit rows from this surface -> not provided (read-only)
</reject>
After:
<after>
  - Owner/admin/operator can list their tenant's audit events via API and see them in the dashboard; other roles are 403; pagination bounded; no cross-tenant leakage.
  - gateway suite green; dashboard vitest green; next build exit 0.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ list_for_tenant currently returns a bounded list without a `total` count; the alerts envelope has `{items,total}`. Lowest confidence: whether to add a count query. Decision (auto): add a `count_for_tenant` (or return total via a second query) to match the envelope; cheap. If wrong: drop total and paginate by cursor.
  - [ ] offset vs cursor pagination — use offset to match alerts exactly (simplest, consistent).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner/admin/operator can list their tenant's audit events
  Given audit rows exist for the caller's tenant
  When an owner (and admin, and operator) GET /admin/audit
  Then 200 with {items newest-first, total}; each item has actor_email/action/target/result/created_at

Scenario: Roles without AUDIT_READ are forbidden
  Given a billing_admin, a viewer, and a member caller
  When they GET /admin/audit
  Then each gets 403 ERR_AUTH_FORBIDDEN
  And no audit data is returned

Scenario: Pagination is bounded
  Given limit=0 or limit=500 or offset=-1 or limit="abc"
  When GET /admin/audit
  Then 400 ERR_PAYLOAD_INVALID

Scenario: Tenant isolation
  Given audit rows for tenant A and tenant B
  When tenant A's owner lists audit
  Then only tenant A rows are returned

Scenario: Dashboard renders the audit table
  Given the /app/audit page
  When it renders rows
  Then a table with the audit columns + pagination is shown; axe 0 serious/critical; one h1
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/audit?limit&offset   (auth: require_permission(AUDIT_READ))
  200 -> { items: [ { id, actor_email, action, target_type, target_id, result, metadata, created_at } ], total }
  400 -> { error: "ERR_PAYLOAD_INVALID" }   (bad limit/offset)
  403 -> { error: "ERR_AUTH_FORBIDDEN" }    (role lacks AUDIT_READ)
Reader: AuditRepository.list_for_tenant(tenant_id, limit, offset) + count_for_tenant(tenant_id) (NEW count); tenant-scoped.
Pagination: limit 1..100 default 50 · offset >=0 default 0 (mirrors GET /admin/alerts _parse_pagination).
FE: app/(app)/app/audit/page.tsx — table (actor · action · target · result · time) + pagination; nav link; read-only; WCAG-AA.
Schema: NO DB change (reads audit_events built in audit-log-store). Additive endpoint + dashboard page.
Least-sure flag surfaced at freeze: [contract] adding count_for_tenant to match the {items,total} envelope; cost if wrong = drop total / cursor-paginate.
```

Status: FROZEN @ v1 — auto-frozen (autonomy: auto; non-security READ surface mirroring the frozen GET /admin/alerts; AUDIT_READ already Tin-approved in rbac-roles) 2026-06-25.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: endpoint + repo read fully covered; dashboard vitest green; no regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_audit_read_roles: owner/admin/operator 200; billing_admin/viewer/member 403 (parametrized)
  - test_audit_items_shape: items newest-first with all fields; total correct
  - test_audit_pagination_bounds: limit 0/500/-1/"abc" -> ERR_PAYLOAD_INVALID
  - test_audit_tenant_isolation: tenant A caller sees only A rows
  - test_audit_dashboard_page: /app/audit renders the table + pagination; axe 0 serious/critical; one h1 (vitest)
</test_plan>

Tests live in: `apps/gateway/tests/` `apps/dashboard/tests/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/api/` `apps/gateway/src/gateway/audit/` `apps/gateway/tests/` `apps/dashboard/app/(app)/app/audit/` `apps/dashboard/components/` `apps/dashboard/lib/` `apps/dashboard/tests/` `apps/dashboard/tests-bff/`
Strategy (ordered batches):
  1. RED: gateway test_audit_read.py + dashboard audit-page.test.tsx.
  2. BE: GET /admin/audit (require_permission(AUDIT_READ), _parse_pagination, AuditListResponse) + AuditRepository.count_for_tenant + list_for_tenant offset variant.
  3. FE: /app/audit page (mirror /app/alerts) + nav link + api-client call.
  4. Green: gateway suite + dashboard vitest + tsc + next build.
Safety rule (feature-specific): READ-ONLY — never expose a mutate path; tenant-scoped reads only; render no secret (metadata already secret-free).
Code lives in: `apps/gateway/` + `apps/dashboard/`
Constraints: do NOT change any test or the FROZEN contract; do NOT create tmp/*.txt (inline -m commits); allow-list packages only.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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

### Build expectations — confirmed at gate
- [x] owner/admin/operator 200, billing_admin/viewer/member 403 — test_audit_read_roles (parametrized) green; AUDIT_READ allowlist
- [x] tenant isolation + bounded pagination — test_audit_tenant_isolation (A-only) + test_audit_pagination_bounds (0/500/-1/"abc"→ERR_PAYLOAD_INVALID) green
- [x] dashboard /app/audit renders table+pagination + a11y — dashboard vitest 486 green; nav item (minRole admin); next build compiled

### Deep checks
- [x] WIRING — get_audit endpoint + AuditRepository(count_for_tenant + list_for_tenant_paged) + AuditPage/AuditTable + nav item all referenced; audit_read 15/15
- [x] DEAD-CODE — FIXED: subagent inlined the query leaving repo methods orphaned; orchestrator REFACTORED get_audit to call AuditRepository (commit 2b872f2) → methods now consumed, ruff/pyright clean, 15/15 still green
- [x] SEMANTIC — READ-ONLY surface; metadata already secret-free (audit-log-store); tenant-scoped (only caller's tenant)

### GATE RECORD
Outcome: PASS
Reviewed by: orchestrator independent review (role-gating + tenant isolation re-run 15/15; dead-code refactor to consume the repo seam; ruff/pyright clean) · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 403 rate · pagination errors.

### Spec delta
- [SPEC · open] audit export (CSV) · filter by action/actor/date-range · operator-wide (NULL-tenant) system audit view.

### Competency deltas
- [SDD · folded] read surfaces mirror an existing frozen envelope (alerts) for consistency — cheap and predictable. [folded foundation-version 35]
