# TASK: Alerts & events viewer — GET /admin/alerts + dashboard Alerts page

slug: alerts-events-viewer · created: 2026-06-22 · stage: production
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
BACKEND (add the read endpoint to the EXISTING `usage_router` — the `alert_events` ORM already lives in `usage/`, and the closest analogs `/admin/reconciliation` + `/admin/usage` live there; avoids a new module api-layer + main.py wiring):
- `apps/gateway/src/gateway/usage/infrastructure/alert_events_orm.py:AlertEventRow` — ORM for table `alert_events`. Cols: `id` UUID PK · `tenant_id` UUID **nullable** (NULL = system/operator event; FK omitted from ORM so fixtures can insert NULL) · `key_id` UUID null · `event_type` TEXT NOT NULL · `payload` JSONB NOT NULL · `created_at` TIMESTAMPTZ NOT NULL default now() · `delivered_at` TIMESTAMPTZ **null** (NULL=undelivered) · `dedupe_key` TEXT NOT NULL UNIQUE. Partial idx `alert_events_undelivered_idx` on `created_at WHERE delivered_at IS NULL`. Migration `f4a9b3c7e8d2_alert_events.py`. **No `delivery_status` enum — derive delivered vs pending from `delivered_at IS [NOT] NULL`.**
- `apps/gateway/src/gateway/usage/api/router.py:get_reconciliation` (≈479–517) — CLOSEST analog. Sig: `(identity: Annotated[Identity, Depends(require_owner_or_admin)], session: Annotated[AsyncSession, Depends(get_session)], window=..., start=..., end=...) -> ReconciliationResponse`. Copy: auth dep chain, `get_session`, frozen Pydantic response, isoformat datetimes.
- `apps/gateway/src/gateway/usage/api/router.py:get_usage` (≈76–140) — raw `text()` SELECT with `WHERE tenant_id = :tid` + hardcoded `LIMIT 50` (the ONLY existing limit pattern; no offset/cursor anywhere — design limit/offset fresh).
- `apps/gateway/src/gateway/usage/api/router.py:usage_router` — `APIRouter(prefix="/admin", tags=["usage"])`; registered `app.include_router(usage_router)` in `main.py` (≈730). New endpoint slots here → NO new router/registration.
- `apps/gateway/src/gateway/keys/api/deps.py:require_owner_or_admin` (≈53) — decodes Bearer JWT, enforces owner/admin, returns `Identity(tenant_id, role, …)`. **tenant_id comes from the JWT, never a query param.**
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` — `.exc()` for the 422/invalid-pagination reject; add a new spec if needed.
- event_type values that EXIST (all TEXT, never a DB enum), and their tenant_id: `soft_budget_exceeded` (tenant UUID) · `upstream_health_fail`/`upstream_health_recovered` (NULL) · `circuit_breaker_open` (NULL) · `drain_timeout` (NULL) · `reconciliation_drift` (NULL). Writers: `usage/application/alert_writer.py:persist_soft_budget_alert`, `alerting/application/health_checker.py:UpstreamHealthChecker`, `proxy/infrastructure/circuit_breaker.py`, `usage/application/flusher.py`, `usage/application/drift_checker.py:ReconciliationDriftChecker`.

FRONTEND (mirror the usage page pipeline exactly):
- `apps/dashboard/app/(dashboard)/alerts/page.tsx` — NEW thin page (mirror `app/(dashboard)/usage/page.tsx`, 6 lines).
- `apps/dashboard/components/alerts/AlertsPage.tsx` — NEW (`"use client"` + `useQuery` + `apiGet<…>("/admin/alerts")`, props-down loading/error/data; mirror `components/usage/UsagePage.tsx`).
- `apps/dashboard/components/alerts/AlertsTable.tsx` — NEW (`ColumnDef[]` + `<DataTable …>`; mirror `components/usage/UsageTable.tsx`).
- `apps/dashboard/lib/api-client.ts:apiGet` — existing; `apiGet<AlertsListResponse>("/admin/alerts?limit=&offset=")` routes via `/api/gw/admin/alerts` through the existing BFF catch-all `app/api/gw/[...path]/route.ts` — **NO new BFF route.**
- `apps/dashboard/components/ui/data-table.tsx:DataTable` · `components/ui/states.tsx` (`Loading`/`ErrorState`/`Empty`) · `components/ui/card.tsx` (`Card`/`CardHeader`/`CardContent`/`CardTitle`) — reuse.
- `apps/dashboard/components/ui/app-shell.tsx:NAV_ITEMS` (≈43–51) — add `{ href:"/alerts", label:"Alerts", icon: Bell, minRole:"admin" }`.

Context (working folder):
- `.add/milestones/v31/MILESTONE.md` — task row + exit criterion "An owner browses alert history (soft-budget, circuit-open, health) in the dashboard." **← drives the system-events-visibility decision below.**
- Tests to mirror: `apps/dashboard/tests/usage.test.tsx` (msw `http.get(".../api/gw/admin/alerts")`, QueryClientProvider, loading/empty/error/success) · `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (pins which nav links each role sees — adding an admin-only Alerts link will need this updated via the re-cross ritual). Backend: `apps/gateway/tests/` reconciliation/usage admin-read suites.

Honors (patterns / conventions):
- PROJECT.md tenant-scoping invariant: "every query is tenant-scoped" — the v31 milestone already grants ONE audited cross-tenant exception (operator-wide). **Showing NULL-tenant system events to a tenant owner is a SECOND scoping relaxation → must be an explicit, human-approved contract decision (the freeze flag).**
- CONVENTIONS.md: Clean-Arch layering (api → reads ORM in infrastructure, never reverse) · ErrorSpec.exc() (no raw ProblemError) · datetimes `.isoformat()`, money `str(Decimal)` · response ENVELOPE recorded per-endpoint (no assumed uniformity — decide list shape in §3) · frontend `within(<section>)` test scoping · msw default handlers as `setupServer(...)` initial handlers · `minRole:"admin"` nav gating.

Anchors the contract cites: `AlertEventRow` (+ table `alert_events`, cols `tenant_id`/`event_type`/`payload`/`created_at`/`delivered_at`/`dedupe_key`) · `usage_router` (prefix `/admin`) · `require_owner_or_admin` → `Identity.tenant_id` · `get_session` · `ErrorSpec` · `apiGet` · `DataTable` · `NAV_ITEMS`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Alerts & events viewer — an owner/admin reads their tenant's alert history through `GET /admin/alerts` (paginated, newest-first) and browses it on a dashboard Alerts page.
Framings weighed: read-only history list (chosen) · live-tail/streaming feed (rejected — alerts are low-volume audit records, not a stream; the existing webhook dispatcher already pushes) · per-alert ack/dismiss mutations (rejected — out of scope; this milestone is read-only UI↔BE coverage, mutation is a later task).
Must:
<must>
  - Return the caller's tenant alert rows from `alert_events`, newest-first (`ORDER BY created_at DESC, id DESC` — stable tiebreak for equal timestamps).
  - Resolve tenant from the JWT via `require_owner_or_admin` (owner OR admin); never from a query param.
  - Each row exposes: `id`, `event_type`, `payload` (JSONB object, opaque/untyped), `created_at` (isoformat), `delivered` (boolean, derived `delivered_at IS NOT NULL`), `delivered_at` (isoformat | null).
  - Paginate with `limit` (default 50, 1..100) + `offset` (default 0, ≥0); response carries `total` (count of matching rows) so the UI can page.
  - Visibility (⚠ the freeze decision): include platform system events (`tenant_id IS NULL` — circuit-open, upstream-health, drift) ALONGSIDE the tenant's own `soft_budget_exceeded` rows, so the criterion "browses soft-budget, circuit-open, health" is met. WHERE = `tenant_id = :tid OR tenant_id IS NULL`.
  - Dashboard `/alerts` page (admin-only nav) renders the history in a `DataTable`: type, when, delivered status, payload summary; handles loading/empty/error states.
</must>
<reject>
  - `limit` < 1 or > 100, or non-integer -> "ERR_PAYLOAD_INVALID" (422)
  - `offset` < 0 or non-integer -> "ERR_PAYLOAD_INVALID" (422)
  - missing/invalid Bearer, or a member-role token (not owner/admin) -> existing `require_owner_or_admin` rejection (401/403) — unchanged, not re-specified here
</reject>
<after>
  - A read-only GET returned the tenant-visible alert history newest-first; no row was created, updated, or deleted; `delivered_at` is untouched (this endpoint never marks delivery).
  - An owner sees their soft-budget alerts plus platform system alerts on `/alerts`; a member never sees the Alerts nav link.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **System events (tenant_id IS NULL) SHOULD be visible to every tenant owner.** Lowest confidence because it is a SECOND relaxation of the "every query is tenant-scoped" invariant (the v31 milestone already spent its ONE audited exception on operator-wide). The milestone exit criterion explicitly names "circuit-open, health" — which are NULL-tenant system rows — so the intent reads as yes, but this is a security-posture call that is Tin's to make at the freeze. If wrong (owners must NOT see platform events): WHERE collapses to `tenant_id = :tid`, the page shows only soft-budget alerts, and the criterion wording needs softening. Cheap to flip (one WHERE clause) — but must be decided BEFORE the contract freezes. **Not a cross-tenant leak: system rows belong to no tenant; the risk is operational-info disclosure, not tenant-data disclosure.**
  - [ ] Endpoint belongs on `usage_router` (prefix `/admin`, path `/admin/alerts`) rather than a new `alerting/api` router — chosen for convention-fit (ORM + sibling admin reads live in `usage/`). Low risk; pure placement.
  - [ ] `payload` is returned verbatim as an opaque object (varied schema per event_type) — the UI summarizes, does not type it. Low risk; matches JSONB reality.
  - [ ] Pagination is offset-based (no existing pattern to mirror; `total`+limit/offset is simplest for a low-volume audit list). Low risk; cursor can come later if volume grows.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner reads tenant alert history newest-first
  Given an owner JWT for tenant T, and alert_events has three soft_budget_exceeded rows for T at t1<t2<t3
  When GET /admin/alerts
  Then 200 with items = [t3, t2, t1] (created_at DESC), each carrying id, event_type, payload, created_at, delivered, delivered_at, and total = 3

Scenario: System (NULL-tenant) events are visible alongside tenant rows
  Given an owner JWT for tenant T, one soft_budget_exceeded row for T, and one circuit_breaker_open + one upstream_health_fail row with tenant_id NULL
  When GET /admin/alerts
  Then 200 with items including the tenant's soft-budget row AND both NULL-tenant system rows, total = 3

Scenario: Another tenant's rows never leak
  Given an owner JWT for tenant T, and a soft_budget_exceeded row owned by a DIFFERENT tenant U
  When GET /admin/alerts
  Then 200 and tenant U's row is absent from items

Scenario: delivered flag is derived from delivered_at
  Given an owner JWT for tenant T, one row with delivered_at set and one row with delivered_at NULL
  When GET /admin/alerts
  Then 200 and the first row has delivered=true with delivered_at isoformat, the second has delivered=false with delivered_at=null

Scenario: Pagination with limit and offset
  Given an owner JWT for tenant T with 5 visible alert rows
  When GET /admin/alerts?limit=2&offset=2
  Then 200 with exactly 2 items (the 3rd and 4th newest) and total = 5

Scenario: Default pagination
  Given an owner JWT for tenant T with 60 visible alert rows
  When GET /admin/alerts
  Then 200 with 50 items (default limit) and total = 60

Scenario: Read is side-effect free
  Given an owner JWT for tenant T with one undelivered alert row
  When GET /admin/alerts
  Then 200 and the row's delivered_at is still NULL afterward
  And no row was created, updated, or deleted

Scenario: Reject limit out of range
  Given an owner JWT for tenant T
  When GET /admin/alerts?limit=0  (or limit=101, or limit=abc)
  Then 422 "ERR_PAYLOAD_INVALID"
  And no query executed against alert_events / nothing returned

Scenario: Reject negative offset
  Given an owner JWT for tenant T
  When GET /admin/alerts?offset=-1
  Then 422 "ERR_PAYLOAD_INVALID"
  And nothing returned

Scenario: Member role is denied
  Given a member-role JWT for tenant T
  When GET /admin/alerts
  Then 403 (require_owner_or_admin rejection)
  And no alert data returned

Scenario: Dashboard Alerts page renders history
  Given the BFF returns two alert rows for the signed-in owner
  When the owner opens /alerts
  Then the page shows a table with both rows (event type, when, delivered status) within the alerts section

Scenario: Dashboard handles empty history
  Given the BFF returns zero alert rows
  When the owner opens /alerts
  Then the page shows the empty state, not an error

Scenario: Alerts nav link is admin-only
  Given a member-role session
  When the dashboard shell renders the nav
  And a member never sees the "Alerts" link; an owner/admin does
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/alerts?limit=<int 1..100, default 50>&offset=<int ≥0, default 0>
  auth: Bearer JWT -> require_owner_or_admin (owner OR admin); tenant_id from token
  200 -> {
    "items": [
      {
        "id": "<uuid str>",
        "event_type": "<str>",          # e.g. soft_budget_exceeded | circuit_breaker_open | upstream_health_fail | reconciliation_drift | …
        "payload": { <opaque JSONB object> },
        "created_at": "<isoformat>",
        "delivered": <bool>,            # derived: delivered_at IS NOT NULL
        "delivered_at": "<isoformat>" | null
      }, …
    ],
    "total": <int>                      # count of all matching rows (for the UI pager)
  }
  422 -> { "code": "ERR_PAYLOAD_INVALID", … }   # limit out of 1..100 or non-int; offset <0 or non-int
  401/403 -> existing require_owner_or_admin rejection (unchanged)

Envelope: object { items: [...], total: int }  (NOT a bare array — matches the paginated-read shape; recorded per the no-assumed-uniformity rule)

Visibility (FREEZE DECISION): WHERE tenant_id = :tid OR tenant_id IS NULL
  → tenant's own soft_budget_exceeded rows + platform system events (circuit/health/drift).
  ORDER BY created_at DESC, id DESC   LIMIT :limit OFFSET :offset
  total = COUNT(*) over the same WHERE (no limit/offset).

Schema: reads alert_events via AlertEventRow (usage/infrastructure). READ-ONLY — no INSERT/UPDATE/DELETE; delivered_at never written here.
Frontend: GET /admin/alerts via apiGet → BFF catch-all (no new route). New /alerts page (admin-only nav). Response items rendered in DataTable.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-22)
Decided at freeze: visibility = `tenant_id = :tid OR tenant_id IS NULL` (Tin chose "show system events too" — owners see soft-budget + platform circuit/health/drift; an intentional, audited SECOND relaxation of strict tenant-scoping, scoped to NULL-tenant platform rows that belong to no tenant — operational-info disclosure, NOT cross-tenant tenant-data).
Least-sure flag surfaced at freeze: [contract] offset-based pagination computes `total = COUNT(*)` over the visibility WHERE on every read. Low confidence it matters because alert_events is low-volume (system events deduped per-day, soft-budget per-key-per-month), so COUNT stays cheap; if wrong at scale, swap to a cursor/keyset page later (the response envelope already isolates `items`/`total`). Cost if wrong: a slow list page — no correctness risk, no contract break to fix.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on new code (backend handler + frontend components)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  BACKEND — `apps/gateway/tests/alerts_events_viewer/test_alerts_events_viewer.py`:
  - test_owner_reads_history_newest_first: seed 3 soft-budget rows t1<t2<t3 for T / GET /admin/alerts / items==[t3,t2,t1], total==3, each row has id/event_type/payload/created_at/delivered/delivered_at
  - test_system_null_tenant_events_visible: seed 1 tenant soft-budget + 1 circuit_breaker_open + 1 upstream_health_fail (tenant_id NULL) / GET / all 3 present, total==3
  - test_other_tenant_rows_absent: seed a row for tenant U / GET as T / U's row absent
  - test_delivered_flag_derived: seed 1 delivered + 1 undelivered / GET / delivered true/false + delivered_at iso/null match
  - test_pagination_limit_offset: seed 5 / GET ?limit=2&offset=2 / exactly 2 items (3rd,4th newest), total==5
  - test_default_pagination: seed 60 / GET / 50 items, total==60
  - test_read_is_side_effect_free: seed 1 undelivered / GET / row delivered_at still NULL, count unchanged
  - test_reject_limit_out_of_range: GET ?limit=0 / ?limit=101 / ?limit=abc -> 422 ERR_PAYLOAD_INVALID
  - test_reject_negative_offset: GET ?offset=-1 -> 422 ERR_PAYLOAD_INVALID
  - test_member_denied: member JWT / GET -> 403
  FRONTEND:
  - `apps/dashboard/tests/alerts.test.tsx`: renders history table (2 rows, type/when/delivered within section) · empty state · error state · loading state (mirror usage.test.tsx, msw http.get /api/gw/admin/alerts)
  - `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (UPDATE existing): add "Alerts" to the admin-only nav set — member never sees it, owner/admin does
</test_plan>

Tests live in: `apps/gateway/tests/alerts_events_viewer/` `apps/dashboard/tests/alerts.test.tsx` `apps/dashboard/tests-bff/nav-role-filter.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/api/router.py` `apps/dashboard/app/(dashboard)/alerts/` `apps/dashboard/components/alerts/` `apps/dashboard/components/ui/app-shell.tsx`
Strategy (ordered batches): 1. backend handler + response schemas in usage/api/router.py (raw text() SELECT mirroring get_usage; manual limit/offset validation → ERR_PAYLOAD_INVALID.exc(); COUNT(*) for total) → red→green backend. 2. frontend AlertsPage + AlertsTable + thin page (mirror usage triad) + add Alerts nav item → red→green frontend. 3. update nav-role-filter test expectation (admin-only Alerts link).
Safety rule (feature-specific): READ-ONLY — the handler issues only SELECT/COUNT; never INSERT/UPDATE/DELETE, never touches delivered_at. Tenant_id always from the JWT identity, never a param. asyncio.timeout guard on the DB read (IO design-for-failure).
Code lives in: backend `apps/gateway/src/gateway/usage/api/router.py` · frontend `apps/dashboard/components/alerts/` + `app/(dashboard)/alerts/` + `components/ui/app-shell.tsx`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps — reuse FastAPI/SQLAlchemy/shadcn/TanStack already present); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — backend full suite 1318 green (single-process, --ignore=tests/edge); dashboard full suite 365 green; alerts suite 15/15
- [x] coverage did not decrease — added 15 backend + 4 frontend tests + 1 nav test update; net new coverage
- [x] no test or contract was altered during build — tests only STRENGTHENED post-refute (re-crossed tests→build→verify); contract FROZEN @ v1 untouched; nav-role-filter update was a TESTS-phase change (superseded count 7→8)
- [x] the green was EARNED — adversarial refute-read (sonnet) UPHELD 0.82, ZERO blockers, no security holes (WHERE parameterized, tenant_id always from JWT, COUNT shares the visibility WHERE). 5 EARNED-GAPs closed by strengthening (EG-1 offset=abc, EG-2 missing-bearer 401, EG-3 admin role, EG-4 id-DESC tiebreak, EG-5 combined own+NULL+other visibility) + NIT-2 (row-count-unchanged assertion). Re-crossed + re-green after.
- [x] concurrency / timing — READ-ONLY GET under `asyncio.timeout(30s)`; no writes, no shared mutable state; idempotent
- [x] no exposed secrets, injection openings, or unexpected dependencies — parameterized binds only (`:tid`/`:limit`/`:offset`); no new packages (FastAPI/SQLAlchemy/shadcn/TanStack reused)
- [x] layering & dependencies follow CONVENTIONS.md — api reads ORM (`alert_events`) via raw text() like sibling get_usage; ErrorSpec.exc() for 422; isoformat datetimes
- [x] a person reviewed and approved the change — Tin approved the security-critical visibility decision at the contract freeze (AskUserQuestion 2026-06-22); auto-gated under autonomy:auto (not risk:high; the one scoping relaxation was human-approved at freeze)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] GET /admin/alerts returns `{items:[...], total:int}` newest-first; each item has id/event_type/payload/created_at/delivered/delivered_at — confirmed by test_owner_reads_history_newest_first (order [t3,t2,t1] + field presence)
- [x] An owner's response includes BOTH their soft-budget rows AND NULL-tenant system rows; a different tenant's rows are absent — confirmed by test_combined_visibility (own+NULL visible, other absent, total=own+NULL) + test_system_null_tenant_events_visible + test_other_tenant_rows_absent
- [x] `delivered` boolean tracks `delivered_at IS NOT NULL`; the GET never mutates delivered_at or row count — confirmed by test_delivered_flag_derived + test_read_is_side_effect_free (re-read row + total count unchanged)
- [x] limit∉1..100 / offset<0|abc → 422 ERR_PAYLOAD_INVALID; member → 403; missing bearer → 401 — confirmed by reject + member + missing-bearer tests
- [x] /alerts page renders the history table (admin-only nav, member can't see link) with loading/empty/error states — confirmed by alerts.test.tsx (4 states, within(region)) + nav-role-filter.test.tsx (8 links admin/owner/unknown, 4 member)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `get_alerts` registered on `usage_router` (already include_router'd in main.py); `AlertsPage`→`app/(dashboard)/alerts/page.tsx`; `AlertsTable`→`AlertsPage`; Bell nav item→NAV_ITEMS. All referenced (tests exercise the live routes).
- [x] DEAD-CODE (code) — no orphans: `AlertEventItem`/`AlertListResponse`/`_parse_pagination`/`_coerce_payload` all used by `get_alerts`; ruff (incl. unused-import/var) + pyright clean on router.py; eslint clean on frontend.
- [x] SEMANTIC (prose / non-code) — n/a (code task; the visibility relaxation decision is recorded in §3 freeze + honored in the WHERE clause).

### GATE RECORD
Outcome: PASS
Reviewed by: AI auto-resolved under autonomy:auto (refute UPHELD 0.82, no blockers) · security-critical visibility decision human-approved by Tin at the §3 freeze (2026-06-22) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): GET /admin/alerts 422-rate (bad pagination) · 403-rate (member probing) · p95 latency of the COUNT(*)+page query as alert_events grows · share of responses whose items are dominated by NULL-tenant system rows (signals whether tenants find their own alerts).

### Spec delta
- [SPEC · open] add event_type / delivered filters + a date-range to GET /admin/alerts (evidence: a busy tenant's soft-budget rows get buried under platform system events; the frozen envelope already isolates items/total so a filter is additive).
- [SPEC · open] swap offset pagination for keyset/cursor if alert_events volume makes COUNT(*) slow (evidence: the freeze flag — low confidence COUNT stays cheap at scale; envelope isolates the change).
- [SPEC · open] operator-view of ALL system alerts (incl. other tenants' soft-budget) behind the /ops mTLS surface (evidence: this task deliberately scoped tenant reads to own+NULL; a true cross-tenant alert view belongs with operator-wide-reconciliation's ops-auth).
- [SPEC · open] surface a payload summary column per event_type on the dashboard (evidence: payload is rendered opaque today; soft_budget shows budget vs spend, circuit shows provider — a typed summary would read better).

### Competency deltas
- [TDD · open] a human-approved invariant RELAXATION needs a single combined test that exercises ALL branches at once (own visible + NULL visible + other hidden + correct total), not just one-branch-each — the refute (EG-5) showed isolated tests let a WHERE mutation survive (evidence: test_combined_visibility added post-refute).
- [SDD · open] when a contract grants a second exception to a core invariant, record the decision verbatim at the §3 freeze AND mirror it as an inline comment at the enforcing WHERE clause — so the relaxation is auditable from the code, not just the TASK (evidence: get_alerts docstring + §3 "Decided at freeze").
- [ADD · open] adding an admin-only nav item supersedes a prior frozen nav-count test (7→8) — update it in the TESTS phase as a declared change, then re-cross; carrying it into build trips build_tampered (evidence: nav-role-filter.test.tsx updated before the snapshot crossing).
