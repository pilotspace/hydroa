# TASK: Upstream health view — GET /admin/health/upstreams + dashboard health panel

slug: upstream-health-view · created: 2026-06-23 · stage: production
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
HEALTH STATE SOURCE (the deciding finding): there is NO dedicated health-state store. The `UpstreamHealthChecker` pings ONLY OpenRouter (`_HEALTH_CHECK_URL`), keeps per-replica in-memory counters, and writes durable events to `alert_events` (system rows, tenant_id NULL): `upstream_health_fail` (payload `{consecutive_failures, url}`, dedupe `health_fail:{episode}`) and `upstream_health_recovered` (dedupe `health_recovered:{episode}`). → DERIVE current up/down from `alert_events` (durable, cross-replica) — the same NULL-tenant system rows the alerts task already reads. DO NOT fabricate up=true rows for providers that are never pinged (anthropic/gemini/bedrock/azure/openai have NO health ping — honesty: report only what is monitored).
BACKEND (add to the EXISTING usage_router next to get_alerts — same `alert_events` table, same auth):
- `apps/gateway/src/gateway/usage/api/router.py:get_alerts` (just built) — CLOSEST analog; copy: `Depends(require_owner_or_admin)` (from keys.api.deps), `Depends(get_session)`, raw `text()` SELECT on `alert_events`, frozen Pydantic response, isoformat datetimes. Add `get_upstream_health` here.
- `apps/gateway/src/gateway/usage/infrastructure/alert_events_orm.py:AlertEventRow` — table `alert_events` (event_type/created_at/payload, tenant_id NULL for system events). Read-only.
- `apps/gateway/src/gateway/alerting/application/health_checker.py:UpstreamHealthChecker` — emits the two event types; the single monitored upstream is OpenRouter (payload.url = openrouter.ai). Confirms the only real ping target.
- `apps/gateway/src/gateway/keys/api/deps.py:require_owner_or_admin` — owner/admin; member→403 ERR_AUTH_FORBIDDEN; tenant from JWT (here only used to gate, status is GLOBAL platform state — like GET /admin/routing).
- `apps/gateway/src/gateway/proxy/api/routing_admin_router.py:get_routing_admin` — secondary analog: an owner/admin GET that returns GLOBAL platform (not tenant-scoped) state read from app.state. Confirms "platform-state GET behind owner/admin" is an established shape.
- NO new error spec (read-only, 401/403 inherited from require_owner_or_admin). NO migration, NO new table.

FRONTEND (mirror the alerts page pipeline — new admin-only /health page):
- `apps/dashboard/components/health/HealthPage.tsx` — NEW (`"use client"` + useQuery + `apiGet<UpstreamHealthData>("/admin/health/upstreams")`, section aria-labelledby, Loading/ErrorState inline; mirror `components/alerts/AlertsPage.tsx`).
- `apps/dashboard/components/health/UpstreamsTable.tsx` — NEW (`ColumnDef[]` + DataTable: Upstream / Status (Up|Down) / Last event; mirror `components/alerts/AlertsTable.tsx`).
- `apps/dashboard/app/(dashboard)/health/page.tsx` — NEW thin page (mirror alerts/page.tsx).
- `apps/dashboard/lib/api-client.ts:apiGet` — existing; `apiGet("/admin/health/upstreams")` via BFF catch-all — NO new BFF route. (apiGet is the canonical read helper for read-only admin pages — AlertsPage/UsagePage use it.)
- `apps/dashboard/components/ui/app-shell.tsx:NAV_ITEMS` — add `{ href:"/health", label:"Health", icon:<Heart/Activity-like>, minRole:"admin" }` → nav count 8→9.
- `apps/dashboard/components/ui` — DataTable/states(Loading/ErrorState/Empty)/Card reuse.

Context (working folder):
- `.add/milestones/v31/MILESTONE.md` criterion "An owner sees per-provider upstream up/down status in the dashboard." ⚠ only OpenRouter is actually pinged → "per-provider" today = the monitored upstream(s); see the freeze flag (honesty: don't fabricate rows for unpinged providers).
- Tests to mirror: `apps/dashboard/tests/alerts.test.tsx` (msw + within(region) + 4 states); `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (currently expects 8 links → 9 after Health); backend `apps/gateway/tests/alerts_events_viewer/` (seed alert_events + owner/member tokens).

Honors (patterns / conventions):
- CONVENTIONS.md: Clean-Arch (api reads ORM, no logic in api) · ErrorSpec/owner-admin gate · datetimes isoformat · frontend within(<section>) · apiGet for read-only admin pages · minRole:"admin" nav gating · NO fabricated/fake-green data (report only monitored upstreams).
- PROJECT.md: upstream health is GLOBAL platform state (singleton checker on app.state; alert_events system rows tenant_id NULL) — owner/admin sees it; NOT a new tenant-scope relaxation (reuses the alerts-approved NULL-tenant read).

Anchors the contract cites: `AlertEventRow`/`alert_events` (event_type upstream_health_fail|upstream_health_recovered, created_at, tenant_id NULL) · `usage_router` · `require_owner_or_admin` · `get_session` · `apiGet` · `DataTable` · `NAV_ITEMS`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Upstream health view — an owner/admin sees the up/down status of each monitored upstream via `GET /admin/health/upstreams`, derived from the durable health events, on a dashboard Health page.
Framings weighed: derive status from `alert_events` (chosen — durable, cross-replica, the system of record; reuses the alerts-approved NULL-tenant read) · read per-replica `app.state.health_checker` in-memory counters (rejected — per-pod, inconsistent across replicas, lost on restart) · fabricate an up/down row per registry provider (rejected — DISHONEST: only OpenRouter is actually pinged; fake green for unmonitored providers).
Must:
<must>
  - Return the status of each MONITORED upstream. Today the only health-pinged upstream is `openrouter` (the checker's single target) — always included.
  - Status per upstream derived from the latest `alert_events` row for that upstream among {`upstream_health_fail`, `upstream_health_recovered`} (system rows, tenant_id NULL): latest is `upstream_health_fail` → `down`; latest is `upstream_health_recovered` → `up`; no health events ever → `up` with `last_event_at: null` (no incident recorded — the checker runs and only writes on sustained failure).
  - Each entry exposes: `name`, `status` (`up`|`down`), `last_event_at` (isoformat | null), `last_event_type` (str | null).
  - Response also carries `checked_at` (isoformat, the query time — "status as of now").
  - Owner/admin only (member → 403); status is GLOBAL platform state, not tenant-scoped.
  - Dashboard `/health` page (admin-only nav) renders the upstreams in a table (Upstream / Status / Last event) with loading/empty/error states; never fabricates a healthy row for an unmonitored provider.
Reject:
  - missing/invalid Bearer -> existing auth rejection (401); member role -> "ERR_AUTH_FORBIDDEN" (403)
After:
  - A read-only GET returned the current status of every monitored upstream derived from the durable event log; no row was created/updated/deleted.
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **"per-provider" is reported as "per-MONITORED-upstream" (today: just `openrouter`)** — lowest confidence because the milestone criterion says "per-provider", but only OpenRouter is health-pinged; fabricating up/down for anthropic/gemini/bedrock/azure/openai (no pinger) would be fake green. Reporting only what is genuinely monitored is the honest read; the response is a LIST so it extends for free when per-provider pingers land (filed as a SPEC delta). If wrong (Tin wants a row per registry provider now): add per-provider pingers — a much larger task, correctly its own slice. Cost if wrong: a follow-up task; no rework of this contract (list shape is forward-compatible).
  - [ ] No-events default = `up` (not `unknown`) — a fail-only alerting checker means "no failure recorded" ≈ healthy; `last_event_at: null` signals "no incident". Low risk; the alternative `unknown` is a cosmetic relabel.
  - [ ] Derive from `alert_events` (durable) rather than live `app.state.health_checker` — chosen for cross-replica correctness. Low risk.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: No incident ever recorded -> monitored upstream reads up
  Given alert_events has NO upstream_health_fail / upstream_health_recovered rows
  And an owner is authenticated
  When the owner GETs /admin/health/upstreams
  Then the response lists openrouter with status "up", last_event_at null, last_event_type null
  And the response carries a checked_at isoformat timestamp

Scenario: Latest health event is a failure -> upstream reads down
  Given a system upstream_health_fail row for openrouter is the latest health event (tenant_id NULL)
  And an owner is authenticated
  When the owner GETs /admin/health/upstreams
  Then openrouter has status "down", last_event_type "upstream_health_fail", last_event_at = that row's created_at

Scenario: Recovery after a failure -> upstream reads up again
  Given an upstream_health_fail row then a LATER upstream_health_recovered row exist for openrouter
  And an owner is authenticated
  When the owner GETs /admin/health/upstreams
  Then openrouter has status "up", last_event_type "upstream_health_recovered", last_event_at = the recovered row's created_at

Scenario: Status is read-only
  Given any alert_events state
  When the owner GETs /admin/health/upstreams
  Then no alert_events row is created, updated, or deleted (row count unchanged)

Scenario: Admin role is allowed
  Given an admin (non-owner) is authenticated
  When the admin GETs /admin/health/upstreams
  Then the request succeeds (200) and returns the upstream status list

Scenario: Member is forbidden
  Given a member (non-admin) is authenticated
  When the member GETs /admin/health/upstreams
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And no alert_events row is created, updated, or deleted

Scenario: Missing bearer is unauthorized
  Given no Authorization header is sent
  When a client GETs /admin/health/upstreams
  Then the response is 401
  And no alert_events row is created, updated, or deleted

Scenario: Dashboard health page renders the upstream status
  Given the API returns openrouter status "up"
  When an admin opens /health
  Then a table shows the upstream name and an "Up" status with no fabricated rows for unpinged providers
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/health/upstreams   body: none
  auth: Bearer (owner or admin) via require_owner_or_admin
  200 -> {
    checked_at: str,            # isoformat (UTC), the query time
    upstreams: [
      {
        name: str,              # monitored upstream id, e.g. "openrouter"
        status: "up" | "down",
        last_event_at: str | null,    # isoformat of the deciding event, null if none
        last_event_type: str | null   # "upstream_health_fail" | "upstream_health_recovered" | null
      }
    ]
  }
  401 -> handled by auth dependency (missing/invalid bearer)
  403 -> { detail: { error: "ERR_AUTH_FORBIDDEN", ... } }  # member role

Derivation (per monitored upstream, today MONITORED = ["openrouter"]):
  latest := the alert_events row with the greatest (created_at, id) where
            tenant_id IS NULL AND event_type IN ('upstream_health_fail','upstream_health_recovered')
            AND payload-identifies-this-upstream (openrouter is the only emitter today; match all such rows)
  status := "down" if latest.event_type == 'upstream_health_fail'
            "up"   if latest.event_type == 'upstream_health_recovered'
            "up"   if no such row (last_event_at = last_event_type = null)

Schema: reads alert_events (event_type, created_at, id, tenant_id) READ-ONLY. NO write, NO migration, NO new table.
Access: raw text() SELECT on usage_router with Depends(get_session) + Depends(require_owner_or_admin).
Frontend: apiGet("/admin/health/upstreams") via existing BFF catch-all (no new BFF route).
```

Status: FROZEN @ v1 — approved by Tin (autonomy:auto; NOT a security freeze — reuses the
alerts-approved NULL-tenant system-event read with NO new tenant-scope relaxation; read-only).

Least-sure flag surfaced at freeze: [contract] **"per-provider" is delivered as "per-MONITORED-upstream"**
— today only `openrouter` is health-pinged, so the response lists exactly that one upstream; fabricating
up/down rows for the 5 unpinged providers would be fake-green and is rejected. The response is a LIST, so it
extends for free when per-provider pingers land (filed as a SPEC delta). Cost if wrong (Tin wants a row per
registry provider NOW): a separate, larger task to add per-provider pingers — no rework of this list-shaped contract.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_no_events_reports_up: seed NO health rows / owner GET / openrouter status=="up", last_event_at is None, last_event_type is None, checked_at present
  - test_latest_fail_reports_down: seed upstream_health_fail (latest) / owner GET / openrouter status=="down", last_event_type=="upstream_health_fail", last_event_at == seeded created_at
  - test_recovery_reports_up: seed fail then LATER recovered / owner GET / status=="up", last_event_type=="upstream_health_recovered", last_event_at == recovered created_at
  - test_read_only_no_row_mutation: seed N rows / owner GET / COUNT(*) unchanged at N
  - test_admin_allowed: admin (non-owner) GET / 200 + upstreams list present
  - test_member_forbidden: member GET / 403 ERR_AUTH_FORBIDDEN + row count unchanged
  - test_missing_bearer_unauthorized: no Authorization / 401 + row count unchanged
  - test_unmonitored_providers_absent: seed only openrouter health rows / owner GET / response.upstreams names == ["openrouter"] (no anthropic/gemini/bedrock/azure/openai fabricated)
  - (frontend) tests/health.test.tsx: 4 states (loading/error/empty-ish/up-row) via msw + within(region)
  - (frontend) tests-bff/nav-role-filter.test.tsx: admin nav now 9 links incl. /health; member excludes it
</test_plan>

Tests live in: `apps/gateway/tests/upstream_health_view` · `apps/dashboard/tests/health.test.tsx` · `apps/dashboard/tests-bff/nav-role-filter.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/api/router.py` · `apps/dashboard/components/health` · `apps/dashboard/app/(dashboard)/health/page.tsx` · `apps/dashboard/components/ui/app-shell.tsx`
Strategy (ordered batches): 1. backend handler get_upstream_health + schemas on usage_router (RED→green) · 2. frontend HealthPage/UpstreamsTable + /health page + nav item · 3. run both suites green.
Safety rule (feature-specific): READ-ONLY — the handler must NEVER write/update/delete an alert_events row; wrap the SELECTs in asyncio.timeout (design-for-failure IO rule).
Code lives in: `./src/`
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

- [x] all tests pass — backend tests/upstream_health_view 11 green; full gateway suite 1335 green (--ignore=tests/edge, single process); dashboard 373 green (was 368, +5).
- [x] coverage did not decrease — 11 backend + 5 frontend tests ADDED; no test removed.
- [x] no test or contract was altered during build — §3 FROZEN unchanged; tests only ADDED/STRENGTHENED (refute earned-gaps), re-crossed tests→build each time.
- [x] the green was EARNED — adversarial refute-read (sonnet) UPHELD 0.88, ZERO blockers. 5 earned-gaps (coverage) closed by STRENGTHENING: EG-1 recovered→fail ordering, EG-2 admin content, EG-3 checked_at isoformat, EG-4 tenant-owned-row-excluded isolation guard; EG-5 (vacuous error-path read-only counts) left as harmless redundancy (auth dep fires pre-handler).
- [x] concurrency / timing — read-only SELECTs wrapped in asyncio.timeout(30s); no write path; no shared mutable state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — parameterized text() binds only; no new deps; payload opaque.
- [x] layering & dependencies follow CONVENTIONS.md — api reads ORM table via text(), owner/admin gate, isoformat datetimes; frontend apiGet + within(section) + minRole nav gating.
- [x] reviewed — autonomy:auto auto-resolved (NOT security: reuses the alerts-approved NULL-tenant read with NO new tenant-scope relaxation; READ-ONLY); refute-read stands in for the adversarial human check.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] No health events → openrouter reads "up" with null last_event_* + a checked_at — confirmed by test_no_events_reports_up + test_checked_at_is_isoformat.
- [x] Latest fail → "down"; recovery (later) → "up"; later fail again → "down" — confirmed by test_latest_fail / test_recovery / test_recovered_then_fail (ORDER BY created_at,id DESC).
- [x] A tenant-owned health row never flips platform status (strict tenant_id IS NULL) — confirmed by test_tenant_owned_health_row_excluded (the isolation guard).
- [x] member → 403 ERR_AUTH_FORBIDDEN, missing bearer → 401, both with no row mutation — confirmed by test_member_forbidden / test_missing_bearer_unauthorized.
- [x] Only monitored upstreams listed (no fabricated providers) — confirmed by test_unmonitored_providers_absent (set equality == {"openrouter"}).
- [x] Dashboard /health renders the up/down row, 4 states, admin-only nav (9 links) — confirmed by tests/health.test.tsx (5) + tests-bff/nav-role-filter.test.tsx (9-link counts).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — get_upstream_health is on usage_router (already registered in main.py); HealthPage→UpstreamsTable→apiGet; /health page route; NAV_ITEMS Health item + HeartPulse import. All referenced (tests exercise each).
- [x] DEAD-CODE (code) — no orphaned symbol; _MONITORED_UPSTREAMS / event constants / timeout all used in the handler.
- [x] SEMANTIC — n/a (code task; the honesty rule is enforced by test_unmonitored_providers_absent, not prose).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a
Reviewed by: autonomy:auto (auto-resolved; refute-read sonnet UPHELD 0.88, 0 blockers) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
