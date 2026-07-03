# TASK: Batches stats — read-only admin savings/volume/status-breakdown page

slug: batch-dashboard-surface · created: 2026-07-03 · stage: production
milestone: v57
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

> **RESCOPED (Tin, 2026-07-03, correction)** — everything below replaces the original submit+monitor
> workspace grounding. Tin: "we no need a playground for batch request, we just provide for admin to
> view statistics of their tenant's user request then system will process batch by group user's
> request as batch." No composer, no job-authoring UI, no toggle (moved to the new sibling task
> `batch-auto-grouping`, which owns the entire automatic-grouping mechanism). This task is now a
> READ-ONLY admin statistics page: savings + volume + status breakdown (picked via AskUserQuestion).
> The original grounding is preserved in git history, not reproduced here.

Touches (files · symbols · signatures):
  Backend, existing (batch-job-store, done/gate=PASS):
  - `apps/gateway/src/gateway/batches/infrastructure/repository.py:BatchJobRepository.list_for_tenant`
    (lines 98-114) — job-level rows only (`tenant_id`, paginated, newest-first); confirmed by reading
    the body, no item-level aggregation.
  - `apps/gateway/src/gateway/batches/infrastructure/repository.py:BatchJobRepository.status_counts`
    (lines 116-129) — per-ITEM-status breakdown, but scoped to ONE job (`job_id: uuid.UUID` param),
    NOT tenant-wide. Confirmed by reading the body: groups `BatchJobItemRow` by status WHERE
    `batch_job_id == job_id`. Neither this nor `list_for_tenant` gives a tenant-wide item-level
    aggregate — this task most likely needs ONE new additive method for that (see Issues/Risks).
  - `apps/gateway/src/gateway/batches/infrastructure/orm.py:BatchJobRow, BatchJobItemRow` — the base
    rows any new aggregate query reads from; `BatchJobItemStatus`'s 5-state vocabulary (pending|
    succeeded|errored|canceled|expired) is the vocabulary a "status breakdown" stat would use.
  - `apps/gateway/src/gateway/batches/api/router.py:batch_router` — existing GET /v1/batches (job
    list) / GET /v1/batches/{id}; a new read-only stats endpoint is additive alongside this.
  Backend, precedents to mirror:
  - `apps/gateway/src/gateway/tenants/api/cache_router.py:cache_router, get_cache` — the GET-any-
    authenticated-role shape (no PUT needed by this task anymore — nothing here is written).
  - `apps/gateway/src/gateway/keys/api/deps.py:require_owner_or_admin` — RBAC dep (owner OR admin);
    pairs with dashboard `minRole:"admin"` for the new read-only stats endpoint.
  Frontend, existing patterns to reuse:
  - `apps/dashboard/components/ui/index.ts` (barrel) + `page-header.tsx` — `StatCard` (labeled KPI
    tile — direct fit for savings/volume), Loading/Empty/ErrorState/Success (the frozen 4-state
    pattern), `Badge` (status-breakdown chips), `PageHeader`.
  - `apps/dashboard/components/ui/app-shell.tsx:NAV_ITEMS, visibleItems, NavItem` — nav registration;
    the new "Batches" entry is `minRole:"admin"` (NOT unrestricted like the playgrounds — this is an
    admin statistics surface, not a tenant-member-facing workspace; see Issues/Risks).
  - `apps/dashboard/lib/hooks/use-current-user.ts:useCurrentUser` — role source (`GET /api/auth/me`).
  - `apps/dashboard/lib/bff-client.ts:bffGet, BffError` — read-only now, so only `bffGet` is needed
    (no `bffPost`/`bffPut` — no submission, no toggle CRUD left in this task).
  - `apps/dashboard/app/api/gw/[...path]/route.ts` — catch-all BFF proxy, unchanged, automatic.
  - `apps/dashboard/tests-bff/nav-role-filter.test.tsx` — existing nav-role-gating test contract
    (`OWNER_ONLY` pattern list); the new admin-gated batches nav entry needs a symmetric assertion
    added here, not a new test file.

Context (working folder):
  - `.add/milestones/v57/MILESTONE.md` — now carries TWO scope-change notes (2026-07-03 widen, then
    2026-07-03 correction reversing it); Tasks list + Exit criteria updated to this narrowed scope. A
    new sibling task `batch-auto-grouping` owns the toggle + the entire automatic-grouping mechanism
    (not this task) — see its own TASK.md for the still-unresolved sync/byte-identical fork.
  - `.add/tasks/batch-auto-grouping/TASK.md` — sibling task; NO hard dependency either direction (this
    stats page ships now against whatever batch-job-store data already exists, honest-empty-state,
    same pattern already accepted for savings) — but its eventual output should stay compatible with
    whatever new aggregate method this task adds, not fork into a second data model.
  - `.add/design/DESIGN.md` — the `batches-workspace` row's CONCEPT/Composer-UX axes are marked
    SUPERSEDED; the published Artifact mock + `prototypes/batches-workspace.json`'s composer/joblist
    subtrees are retired (discarded-direction record only). This task's design-intake has NOT run yet
    for the corrected scope — still outstanding (see Issues/Risks), deliberately not rushed solo.

Honors (patterns / conventions):
  - The 4-state pattern (`Loading`/`Empty`/`ErrorState`/`Success`, `components/ui/states.tsx`) — reuse
    verbatim, unchanged from before.
  - Honest-degradation convention — savings, volume, AND status-breakdown must all be REAL queries
    that happen to return zero/empty right now, never hardcoded stubs (extends the reasoning already
    accepted for savings alone to the two new stats).
  - RBAC minRole convention — `minRole:"admin"` for the WHOLE page now (not unrestricted) — a
    read-only admin statistics surface, consistent with "provide for admin to view statistics," not a
    tenant-member playground.
  - PROJECT.md's TEXT-not-ENUM status-column convention — carries through to any new aggregate query.

Anchors the contract cites:
  - `BatchJobRepository` — likely gains ONE new additive method (tenant-wide item-status + volume
    aggregate); confirmed neither `list_for_tenant` nor `status_counts` covers this today.
  - `batch_router` — where the new read-only stats endpoint mounts (exact path TBD at specify).
  - `require_owner_or_admin` — RBAC dependency for the new endpoint (GET only).
  - `NAV_ITEMS` (app-shell.tsx) — new "Batches" nav entry, `minRole:"admin"`.
  - `StatCard`, 4-state components (`components/ui`) — the display primitives.

Issues/Risks (→ feed §1):
  - RESOLVED (Tin's direct correction, supersedes both auto-decided items from the prior draft): no
    composer, no job-authoring UI, no per-item drill-down debate — none of it applies. The prior
    draft's ⚠-flagged JSONL-composer assumption and per-item-endpoint-deferral assumption are MOOT,
    not merely lower-risk — there is no composer or drill-down surface left to defer or build.
  - RESOLVED (follows directly from the corrected scope): the tenant toggle (CRUD + real enforcement)
    moves OUT of this task entirely, to `batch-auto-grouping`. This task shows stats; it controls
    nothing.
  - NEW: "volume" + "status breakdown" most likely need one new additive `BatchJobRepository` method
    — confirmed by reading both existing methods' bodies (see Touches). Small, additive, read-only,
    no schema change.
  - NEW: nav entry access level flips from "unrestricted, matches every sibling playground" (the
    prior draft's highest-confidence assumption) to `minRole:"admin"` — a direct, necessary
    consequence of this being an admin-only statistics page, not a fresh open question.
  - Design-intake has NOT happened yet for the corrected (read-only stats) scope — the retired
    composer-based mock doesn't transfer. A fresh, much lighter intake + wireframe is needed before
    build (likely closer to a slice of an existing overview page than a new "workspace") —
    deliberately not run solo in this same pass, given the last one was built on a premise that
    turned out wrong; picking it up once there's a live design-confirm check-in.
  - ⚠ Lowest-confidence item left in THIS task (the sync/byte-identical conflict belongs to
    `batch-auto-grouping`, not here): whether "volume"/"status breakdown" are at the REQUEST/ITEM
    level or the JOB level — see §1 Assumptions.

Related intent:
  - `.add/PROJECT.md` §UI/UX foundation — still applies in spirit (genuinely usable, not a thin
    reskin), but for a read-only admin stats page that means clear, scannable, well-labeled numbers,
    not the richer interaction surface the retired workspace direction implied.
  - v57 MILESTONE.md goal — this task is now the READ side of the tenant-facing half; the automatic-
    grouping mechanism (the write/mechanism side) is owned by `batch-auto-grouping`.
  - GLOSSARY: batch_job, batch line item (already declared by batch-job-store) — no new domain terms
    expected from this task; a new repository method is additive, not a new concept.

Ground SHA: `e897cf0` (current HEAD at ground time — no commits since batch-job-store merged; any
  symbol/line reference here is "as of" this commit).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Batches statistics — read-only admin view (savings + volume + status breakdown)

Design (UDD, this task): the earlier design-intake + hi-fi mock (`batches-workspace` row in
  DESIGN.md, `mocks/batches-workspace.html`, `prototypes/batches-workspace.json`) is SUPERSEDED —
  built on the submit+monitor workspace premise Tin explicitly reversed 2026-07-03. Re-ran intake +
  domain review + component research (`batches-stats` row, DESIGN.md): found `components/usage/
  UsagePage.tsx` (`/app/usage`) as a direct, shipped precedent — hero stat + `StatCard` grid, same
  `useQuery`+`bffGet` pattern already grounded in §0 — so this mock is a close derivation, not a
  fresh invention. Hi-fi mock published: `.add/design/mocks/batches-stats.html` (Artifact:
  https://claude.ai/code/artifact/59915859-9d30-47dd-bef2-660486a82e65). Render tree:
  `prototypes/batches-stats.json`. **Design-confirm: CONFIRMED 2026-07-03** — approved by Tin
  together with the §3 contract freeze (one "approve," both gates; see §3 Status).

Framings weighed:
  **chosen** — a read-only admin statistics page: three real-data surfaces (savings, volume, status
  breakdown), `minRole:"admin"`, `StatCard` + the 4-state pattern, sourced from `BatchJobRepository`
  (existing methods + one new additive tenant-wide aggregate) · **rejected** — the original
  submit+monitor workspace (composer, job list, polling) — reversed by Tin's explicit correction, no
  composer/job-authoring UI of any kind survives · **rejected** — folding these stats into the
  existing Settings page as a `CacheSettings`-style tab (the prior draft's plan, back when the toggle
  lived here too) — the toggle has moved entirely to `batch-auto-grouping`, so there's no natural
  settings-tab home left for what is now purely three read-only numbers; a dedicated admin-gated nav
  entry is a cleaner fit than shoehorning a stats-only view into Settings.

Must:
<must>
  - M1: an admin-gated "Batches" nav entry (`NAV_ITEMS`, `minRole:"admin"`) opens a read-only
    statistics page — no other role sees the entry or can reach the page's data via direct API call
    either (matches "provide for admin to view statistics," not a tenant-member-facing playground).
  - M2 (REVISED at build-grounding, 2026-07-03): the page shows a savings `StatCard` reading
    `$0.00`. CORRECTED from "a REAL query, never a hardcoded stub" — grounding for the actual
    build found `list_price_usd` does not exist ANYWHERE in `usage_records` yet (confirmed: zero
    matches repo-wide), and `usage_source` today only takes `'frame'`/`'stream_fallback'`
    (`usage/infrastructure/orm.py`) — `'batch'` is a value batch-billing-accuracy (a separate,
    not-yet-started task) is expected to introduce. Writing `sum(list_price_usd - cost_usd)`
    today would either 500 on a missing column or require THIS task to add that column itself —
    scope creep into batch-billing-accuracy's job, and a real risk of a schema conflict if both
    tasks add it independently. Ships instead as an explicit application-level constant (`"0.00"`,
    code-commented with why), swapped for the real query the moment batch-billing-accuracy lands
    the column — same end-user-visible honesty (accurate $0.00 today, becomes real automatically
    later), corrected mechanism.
  - M3: the page shows a volume `StatCard` — total batched requests/line-items for the tenant —
    computed from a REAL query, honest `0` today.
  - M4: the page shows a status breakdown (succeeded/errored/in-progress counts, using
    `BatchJobItemStatus`'s existing vocabulary) computed from a REAL query, honest all-zero today.
  - M5: M3+M4 are served by ONE new additive `BatchJobRepository` method (tenant-wide item-status
    aggregate) — confirmed no existing method covers this: `list_for_tenant` is job-level only,
    `status_counts` is per-item but scoped to a single job, not tenant-wide. Read-only, no schema
    change.
  - M6: the page reuses the existing 4-state pattern (`Loading`/`Empty`/`ErrorState`/`Success`) for
    its data fetch — no new state-handling pattern invented.
</must>

Reject:
<reject>
  - R1 (inherited RBAC shape, not redefined): a non-admin/owner calls the new stats endpoint -> 403
    (`require_owner_or_admin`, the same dependency every other admin-gated endpoint here uses).
  - R2 (NEW, this task): the tenant has no batch jobs yet -> the page's Empty state, never
    ErrorState — "genuinely nothing yet" is distinct from "the query failed" (the 4-state pattern's
    own Empty vs. ErrorState distinction, applied to three stats instead of one).
</reject>

After:
<after>
  - A1: all three stats are always live-recomputed from real data, never stale/cached/hardcoded —
    becomes accurate automatically as `batch-auto-grouping` and batch-billing-accuracy land, no
    follow-up UI change needed (same principle already accepted for savings, now extended to all three).
  - A2: a non-admin/owner never sees the nav entry, and the underlying endpoint 403s them
    server-side regardless (RBAC enforced at the API, not just hidden client-side).
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ "Volume" and "status breakdown" are at the REQUEST/ITEM level (individual chat-completion
    requests), not the JOB level (how many batch jobs exist) — lowest confidence because Tin's own
    answer ("Savings + volume + status breakdown") didn't specify granularity; item-level was
    inferred from the original correction's own wording ("statistics of their tenant's user
    **request**") plus it being the more useful number to an admin. If wrong (job-level meant
    instead): M3/M4/M5 simplify — `list_for_tenant`'s existing job rows can be counted/grouped by
    `job.status` directly in this task, no new repository method needed at all; a SMALLER change,
    not bigger.
  - A new repository method is required at all (M5) — medium confidence; well-grounded by reading
    both existing methods' bodies (see §0), but the exact aggregate shape (one method returning both
    volume and status breakdown together, vs. two separate methods) is an implementation-detail
    choice, not yet made.
  - Nav entry is a dedicated page rather than a new section on an existing usage/spend dashboard
    view (which already show tenant-wide metrics) — medium confidence; a dedicated "Batches" entry
    matches the milestone's own naming and keeps this self-contained, but wasn't checked against
    folding it into an existing metrics page instead.
  - `minRole:"admin"` for viewing (not the stricter `"owner"` tier) — medium-high confidence; follows
    the same tier already used for `/admin/cache` GET and every other admin-gated read surface here,
    no reason found to pick the stricter tier for a read-only view.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Admin views the batches stats page   # M1
  Given the authenticated user's role is "admin" or "owner"
  When they open the "Batches" nav entry
  Then the read-only statistics page renders
  And no submission, composer, or toggle control is present anywhere on the page

Scenario: Non-admin cannot reach the batches nav entry or its data   # M1, R1
  Given the authenticated user's role is neither "admin" nor "owner"
  When they load the dashboard
  Then the "Batches" nav entry is not rendered
  And a direct call to GET /admin/batches/stats returns 403
  And no batch statistics are ever exposed to that role

Scenario: Savings StatCard shows the honest current value   # M2
  Given list_price_usd does not exist in usage_records yet (batch-billing-accuracy not built)
  When the admin loads the stats page
  Then the savings StatCard reads "$0.00"
  And this is an explicit, documented constant — not a query against a column that doesn't exist —
    to be replaced by a real sum(list_price_usd - cost_usd) query once that task lands the column

Scenario: Volume and status-breakdown StatCards show real, currently-zero numbers   # M3, M4, M5
  Given the tenant has no batch jobs yet
  When the admin loads the stats page
  Then the volume StatCard reads "0"
  And every status-breakdown count reads "0"
  And all of these numbers come from the new tenant-wide aggregate repository method, not a stub

Scenario: Page reflects real batch activity once it exists   # M3, M4, M5, A1
  Given the tenant has some succeeded and some pending batch line items
  When the admin loads the stats page
  Then the volume StatCard reflects the real total item count
  And the status-breakdown counts reflect the real per-status counts
  And no code change was needed for these numbers to become accurate

Scenario: Empty tenant sees the Empty state, not an error   # M6, R2
  Given the tenant has zero batch jobs
  When the admin loads the stats page
  Then the page renders the Empty state (not ErrorState)
  And the honest-zero StatCards remain visible alongside it

Scenario: A query failure shows ErrorState, not a silent zero   # M6, R2
  Given the stats query fails (e.g. a database error)
  When the admin loads the stats page
  Then the page renders ErrorState with a retry option
  And the numbers are NOT silently shown as zero — that would look identical to genuinely-empty
    and mislead the admin into thinking batching produced nothing, rather than that the page broke
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/batches/stats   (no body; tenant scoped via session JWT, same as every other /admin/* route)
  200 -> {
    savings_usd: string,       # ALWAYS "0.00" today -- an explicit application-level constant, NOT
                                # a query. list_price_usd does not exist in usage_records yet
                                # (confirmed repo-wide, 2026-07-03) -- that column is
                                # batch-billing-accuracy's job. Swap this for a real
                                # sum(list_price_usd - cost_usd) query over usage_source="batch" the
                                # moment that task lands the column -- tracked as this task's own
                                # SPEC delta at §7, not silently forgotten.
    total_requests: int,       # count of ALL BatchJobItemRow for this tenant, across every job, every
                                # status -- a REAL query. Honest 0 until any batch job exists.
    status_counts: {
      pending: int, succeeded: int, errored: int, canceled: int, expired: int
      # full BatchJobItemStatus vocabulary, all 5 keys always present (0 when none) -- mirrors
      # the existing per-job status_counts()'s own convention, just tenant-wide instead of per-job.
      # A REAL query. Frontend may visually group these (e.g. show "in progress" as a friendlier
      # label over `pending`) -- that's a presentation choice, not part of this frozen shape.
    }
  }
  403 -> { error: "forbidden" }   # caller is neither owner nor admin (require_owner_or_admin)

Schema: total_requests + status_counts read BatchJobItemRow.tenant_id (denormalized on the item row
  already, per repository.py's create() -- no join needed through BatchJobRow) -- no new table, no
  new column, no migration; the only addition is ONE new BatchJobRepository method (a tenant-wide
  aggregate query). savings_usd reads nothing (constant) until batch-billing-accuracy lands
  list_price_usd -- see the note on that field above.
```

Glossary deltas: none (no new domain terms -- "batch_job" / "batch line item" already declared by
  batch-job-store).
Status: FROZEN @ v1 -- approved by Tin Dang, 2026-07-03 ("approve") -- this same approval also
  confirms the `batches-stats` design mock (§1) — one decision, both gates. The 5-state vocabulary
  (not a 3-bucket simplification) and the `/admin/batches/stats` path both stand as drafted below,
  unchanged by the approval. Original DRAFT reasoning kept verbatim for the record:
  ⚠ whether "status breakdown" should surface the real 5-state vocabulary (pending/succeeded/
  errored/canceled/expired, as drafted above) or a simplified 3-bucket view (succeeded/errored/
  in-progress, the shorthand used when this was originally proposed via AskUserQuestion) is UNDECIDED
  -- Tin confirmed the CONCEPT ("status breakdown"), not the exact field granularity. This draft
  keeps the API honest and complete (5 real states) and treats any 3-bucket simplification as a
  frontend display choice layered on top, not baked into the frozen shape -- reversible either way
  without a contract change if wrong, since the response already contains the finer-grained data a
  coarser UI grouping would need.
  Least-sure flag surfaced at freeze: [contract] the exact endpoint path
  (`GET /admin/batches/stats`) and which router file it lives in are this task's own pick, not
  independently verified against an established `/admin/*` naming convention beyond
  `/admin/cache` -- why: no second `/admin/*` GET-stats precedent existed to check against; cost
  if wrong: low blast radius, frontend-only follow-up (one `lib/batches.ts` URL string + one
  route registration line), no data model change.
  Design-confirm (the `batches-stats` mock/Artifact): CONFIRMED together with this same freeze —
  see §1 (no longer outstanding).
  NOTE (2026-07-03, transparency): the backend slice (§4/§5 below) was already built and is
  GREEN ahead of this formal freeze -- a deliberate acceleration under Tin's "keep going" after
  three consecutive AskUserQuestion timeouts, judged low-risk because the backend is additive,
  invisible (no UI), and behind RBAC. The FRONTEND was NOT built past this point -- see §5's
  "Strategy actually used" -- because it depends on the design-confirm this same freeze carries,
  and a timeout is the absence of approval, not approval. This one approval therefore does double
  duty: it freezes §3 AND confirms the `batches-stats` mock together.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: backend slice 100% of new lines (repository method + route); frontend slice 100%
  of new lines (page component + nav gating) — both now written and green (see §5 status).
Plan (one test per scenario, asserting behavior not internals) — backend AND frontend:
<test_plan>
  - test_owner_gets_all_zero_stats / test_admin_role_also_gets_stats: admin or owner -> 200, honest
    all-zero body · covers: M1, M2, M3, M4
  - test_member_role_403 / test_viewer_role_403 / test_no_bearer_401 / test_invalid_bearer_401:
    every non-owner/admin caller (incl. no/invalid auth) -> 403 or 401, never stats data · covers:
    M1, R1
  - test_stats_reflect_created_items_after_no_processor_cascade: 3 submitted items cascade to
    errored (no-processor default) -> total_requests=3, status_counts.errored=3, savings_usd stays
    "0.00" · covers: M2, M3, M4, M5, A1
  - test_total_requests_sums_across_multiple_jobs: two separate job submissions sum correctly ·
    covers: M3, M5
  - test_stats_scoped_to_calling_tenant_only: a second tenant's stats show all-zero while the
    first tenant's own stats show its real counts · covers: M5 (tenant scoping), PROJECT.md
    tenant-isolation invariant
  - test_admin_views_batches_stats_page: page renders, $0.00 savings visible, no textbox/submit/
    new-batch/create control anywhere · covers: M1
  - test_savings_shows_honest_current_value: savings StatCard stays "$0.00" even against a
    12-request active-tenant fixture (proves it's decoupled from volume, not just untested) ·
    covers: M2
  - test_volume_and_status_breakdown_show_real_zero_numbers: zero-tenant fixture -> volume + all
    5 status counts render "0" · covers: M3, M4, M5
  - test_page_reflects_real_batch_activity: active-tenant fixture (12 total / 7 succeeded / 1
    errored / 3 pending) -> each number renders, Empty text absent · covers: M3, M4, M5, A1
  - test_empty_tenant_sees_empty_state_not_error: zero-tenant fixture -> Empty copy present,
    honest-zero StatCards still visible alongside it, no `role=alert` · covers: M6, R2
  - test_query_failure_shows_error_state_not_silent_zero: 500 response -> `role=alert` +
    error text, NO "0" anywhere, no Empty copy (proves failure never LOOKS like honest-empty) ·
    covers: M6, R2
  - nav-role-filter.test.tsx (edited, not new): `test_admin_sees_admin_tier_but_not_owner_only_links`
    (18->19) / `test_owner_sees_all_links` (19->20) / `test_unknown_role_fails_open` (19->20, fails
    open) all updated for the new admin-gated "Batches" entry; `test_dashboard_shell_filters_from_
    current_user` (member-only, count stays 10) deliberately UNCHANGED — proves a member still
    can't see it · covers: M1, A2
</test_plan>

Tests live in: `apps/gateway/tests/batches/test_batch_stats.py` (backend, 9 tests, written RED
  [404 before the route existed] then GREEN) · `apps/dashboard/tests/batches-stats-page.test.tsx`
  (frontend, 6 tests, new file, written RED [module-not-found] then GREEN) ·
  `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (frontend, edited, RED [wrong nav counts /
  missing link] then GREEN). All three files confirmed red for the right reason before
  implementation, green after — no test was weakened to pass.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/batches/infrastructure/repository.py` (new additive method) ·
  `apps/gateway/src/gateway/batches/api/` (new read-only stats route, new-or-extended router file) ·
  `apps/gateway/src/gateway/main.py` (router registration, only if a new router file is used) ·
  `apps/dashboard/app/(app)/app/batches/` (new route) ·
  `apps/dashboard/components/batches/` (new `BatchesStatsPage.tsx`, modeled on `UsagePage.tsx`) ·
  `apps/dashboard/lib/batches.ts` (new, `bffGet`-only wrapper) ·
  `apps/dashboard/components/ui/app-shell.tsx` (NAV_ITEMS addition) ·
  `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (symmetric admin-gating assertion) ·
  `apps/gateway/tests/batches/` (new backend test file for the stats endpoint + repository method) ·
  `apps/dashboard/tests-bff/` (new or extended frontend test file for the stats page)
Strategy (ordered batches): 1. backend: new `BatchJobRepository` aggregate method + its own unit
  test (red first) 2. backend: `GET /admin/batches/stats` route wired to it + RBAC + its test (red
  first) 3. frontend: `lib/batches.ts` + `BatchesStatsPage.tsx` (mirrors `UsagePage.tsx`'s
  `useQuery`+`bffGet`+4-state shape) 4. frontend: `NAV_ITEMS` entry + the nav-role-filter test
  assertion 5. e2e/manual: admin sees real honest-zero numbers, non-admin gets 403 + no nav entry.

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used (2026-07-03, COMPLETE — all 5 steps done, both slices green):
  Steps 1-2 (backend) DONE, as planned: added `BatchJobRepository.tenant_status_counts` (mirrors
  `status_counts`, tenant-scoped instead of job-scoped, no join needed — `tenant_id` is denormalized
  on `BatchJobItemRow`); added `stats_router.py` (`GET /admin/batches/stats`, `require_owner_or_admin`,
  `savings_usd` as an explicit constant per M2's build-grounding correction); registered in `main.py`.
  Backend test suite written FIRST (confirmed RED — 404, endpoint didn't exist), then GREEN after
  the above (9/9 passed). ruff + pyright clean on all new/touched files. Full-directory regression
  (`tests/batches/`) run 4x total: one run hit an unrelated pre-existing flake (`test_batch_jobs.py
  ::TestRejectMalformedLineItem::test_reject_missing_messages`, 401 instead of 422) that did not
  reproduce across 3 subsequent full-directory runs (42/42 each) nor in isolation (22/22); the
  failing test uses a fixture (`api_key_info`) this task's test file never touches, and this task's
  code never touches the API-key auth path (`_authenticate`/`SqlAlchemyKeyAuthenticator`) at all —
  judged a pre-existing, timing-based cross-suite race (the same class conftest.py's own
  `_isolate_stores` docstring already documents for other suites), not a regression from this task.

  Steps 3-5 (frontend) DONE 2026-07-03, unblocked by Tin's "approve" (froze §3 AND confirmed the
  `batches-stats` mock in one decision — see §3 Status). Built in order: `lib/batches.ts`
  (`getBatchStats`, `bffGet`-only) -> `BatchesStatsPage.tsx` (mirrors `UsagePage.tsx`'s
  `useQuery`+4-state shape, plus an app-level Empty check layered on top per M6/R2) -> the
  `apps/dashboard/app/(app)/app/batches/page.tsx` route -> the `NAV_ITEMS` entry (`minRole:"admin"`)
  + its `nav-role-filter.test.tsx` assertions -> manual dev-server check. Frontend test file written
  FIRST (confirmed RED — module not found), then GREEN after implementation (6/6); nav test edits
  written FIRST (confirmed RED — wrong counts / entry missing), then GREEN after the app-shell edit
  (5/5). Full dashboard suite: 916/916 green, 0 regressions. Typecheck: 0 errors. ESLint on every
  touched/new file: 0 errors (2 benign "file ignored" notices on the two test files, expected —
  vitest specs aren't part of the app's lint target). Manual/e2e: started a real Next.js dev server
  (port 3901, log at the scratchpad path) and curled `/app/batches` unauthenticated -> `307 ->
  /login`, byte-identical redirect behavior to the existing, shipped `/app/audit` admin page hit the
  same way — confirms the route and its auth-guard middleware are wired correctly. A full
  authenticated click-through in a real browser was NOT performed (no browser-automation tool
  available in this environment) — see §6 Live-verify evidence for what stands in for it and why
  that's judged sufficient here.

  Two build-grounding scrubs found against the approved `batches-stats.html` mock while
  implementing (same category as the earlier M2/`savings_usd` correction — a mock/spec-time
  assumption invalidated by build-time grounding, corrected transparently rather than either built
  as-is or silently dropped):
  1. The mock's `.toggle-row` ("Batch processing for this tenant is currently off. Manage in
     Settings →") references a toggle owned by the separate, unbuilt `batch-auto-grouping` task —
     not part of this task's frozen §3 shape, and Settings has no matching control to link to yet.
     Fix: omitted the element entirely from `BatchesStatsPage.tsx`; no equivalent replaces it (the
     toggle isn't this task's to show).
  2. The mock's hero-sub copy ("Recomputed live from list_price_usd − cost_usd across every
     completed batch line item this period") asserts `savings_usd` is a live query — false, per
     M2's own correction: it's a hardcoded constant until `batch-billing-accuracy` lands the missing
     column. Fix: rewrote the line to "An honest $0.00 until real batch traffic has been priced." —
     the mock's separate `hero-note` box already said this correctly and was kept unchanged.
  Both deviations are visual/copy differences from the mock Tin approved as shown — flagged here
  and surfaced directly to Tin in the build report (not just filed in this section) so he can object
  before this gates PASS, per the "human on residue" half of the auto-gate.
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — backend `test_batch_stats.py` 9/9; frontend `batches-stats-page.test.tsx`
  6/6 (new) + `nav-role-filter.test.tsx` 5/5 (edited); full dashboard suite 916/916 (109 files);
  `tests/batches/` full-directory 42/42 across 3 reruns
- [x] coverage did not decrease — every new line (repository method, route, `lib/batches.ts`,
  `BatchesStatsPage.tsx`, nav entry) is exercised by a new/edited test named in §4; no existing
  line lost a covering test
- [x] no test or contract was altered during build — `git diff` confirms `nav-role-filter.test.tsx`
  only gained assertions for the new nav entry (existing ones unchanged in intent, only counts
  bumped to match the new item); `test_batch_stats.py` and `batches-stats-page.test.tsx` are new
  files, nothing pre-existing weakened; §3 CONTRACT text unchanged since the freeze
- [x] the green was EARNED, not gamed — see Refute-read verdict below (EARNED)
- [x] concurrency / timing of the risky operation is safe — see Advisor 3-lens below (CLEAR)
- [x] no exposed secrets, injection openings, or unexpected dependencies — see Advisor 3-lens
  below (CLEAR); no new third-party package added (reuses `httpx`/`sqlalchemy`/`fastapi`/
  `@tanstack/react-query` already in the lockfiles)
- [x] layering & dependencies follow CONVENTIONS.md — see Advisor 3-lens below (CLEAR); repository
  method mirrors the existing `status_counts` shape, stats endpoint lives in its own router
  (JWT/session auth) deliberately separate from `batch_router` (sk-key auth) rather than mixed in
- [x] a person reviewed and approved the change — Tin's "approve" (2026-07-03) froze §3 and
  confirmed the `batches-stats` mock in one decision; the two build-grounding scrubs and the
  browser-verification gap (below) are surfaced in this same turn's report to Tin as this gate's
  residue, per the auto-gate's "human on residue" design (run.md)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] admin/owner calling `GET /admin/batches/stats` sees a real 200 body (`savings_usd`,
  `total_requests`, 5-key `status_counts`) — confirmed by 9 real-Postgres integration tests in
  `test_batch_stats.py` (httpx `AsyncClient` against the real ASGI app, not mocked)
- [x] every non-owner/admin caller (member, viewer, no auth, invalid auth) is refused the data,
  never a degraded/partial body — confirmed by `test_member_role_403` / `test_viewer_role_403` /
  `test_no_bearer_401` / `test_invalid_bearer_401`
- [x] the dashboard shows an admin-gated "Batches" nav entry opening a page with $0.00 savings +
  real volume/status numbers and NO composer, submit, or toggle control anywhere — confirmed by
  `test_admin_views_batches_stats_page`'s `queryByRole("textbox")`/submit-button/new-batch/create
  absence assertions, and by reading `BatchesStatsPage.tsx` in full (no such element authored)
- [x] the Empty state renders ALONGSIDE the honest-zero StatCards (not instead of them), and is
  visibly distinct from ErrorState on a query failure — confirmed by
  `test_empty_tenant_sees_empty_state_not_error` (Empty copy + zeros both present, no
  `role=alert`) and `test_query_failure_shows_error_state_not_silent_zero` (`role=alert` present,
  NO "0" text anywhere, no Empty copy)
- [x] the route is wired end-to-end in a real running server and auth-gates identically to a
  shipped precedent — confirmed by starting a real Next.js dev server and curling `/app/batches`
  unauthenticated: `307 -> /login`, byte-identical to `/app/audit` (existing shipped admin page)
  hit the same way; dev server log showed a clean compile, no errors

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `tenant_status_counts` is called by
  `stats_router.get_batch_stats`; `batch_stats_router` is imported and registered in
  `main.py:37,1053`; `getBatchStats` is called by `BatchesStatsPage`; `BatchesStatsPage` is
  rendered by `app/(app)/app/batches/page.tsx`; the new `NAV_ITEMS` entry is consumed by the
  pre-existing, unchanged `visibleItems` role-filter — all confirmed by grep this turn, none
  orphaned
- [x] DEAD-CODE (code) — no new unused symbol: `BatchStatusCounts`/`BatchStatsData` are both
  consumed (return type + destructuring); no toggle-related code was written and abandoned — the
  toggle was never authored at all (a build-grounding scrub, not dead code left behind)
- [x] SEMANTIC (prose / non-code) — read in full: the approved mock `batches-stats.html` (re-read
  in full immediately before implementing) against the final `BatchesStatsPage.tsx` (read in
  full) — confirmed two deviations (toggle-row omitted, hero-sub copy corrected; both documented
  in §5 and surfaced to Tin directly) and confirmed everything else matches: stat labels, 4-card
  grid layout, hero structure, the accent-soft note box copy

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — re-grepped this turn:
  `get_batch_stats`/`BatchStatsResponse` (`stats_router.py`), `BatchJobRepository.
  tenant_status_counts` (`repository.py`), `require_owner_or_admin` (`keys/api/deps.py:56`),
  `batch_stats_router` registration (`main.py:37,1053`), the `NAV_ITEMS` "Batches" entry
  (`app-shell.tsx`) — all present and correctly wired
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved;
  one stale doc-comment was found and fixed in passing (`stats_router.py`'s module docstring
  still said "Contract DRAFT ... not yet frozen" after the freeze — corrected to "Contract FROZEN
  @ v1" this turn, a one-line hygiene fix, not a behavior change)

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self · adversarially checked: (1) is the savings-constant assertion a tautology? No — the
  test asserts `savings_usd == "0.00"` against a 12-request, 7-succeeded/1-errored ACTIVE tenant
  fixture, proving the value is decoupled from volume, not merely untested against any data at
  all. (2) does the empty-vs-error test actually distinguish the two, or could both pass against
  a single lenient assertion? Checked both test bodies directly: the empty case asserts Empty copy
  present AND no `role=alert`; the error case asserts `role=alert` present AND no "0" text AND no
  Empty copy anywhere — a build that collapsed the two states would fail one or the other, not
  slip through both. (3) is `require_owner_or_admin` actually the dependency wired on the route,
  not just named in a comment? Confirmed via direct read of `stats_router.py`'s `Depends()` call,
  not the docstring. (4) does the "no toggle" frontend assertion check only for the specific
  toggle-row copy (which would trivially pass once that string is merely absent), or would it also
  catch an accidentally-reintroduced composer? Checked: it queries for textbox role, submit
  button, "new batch", and "create" text generically — would fail against either regression, not
  just the one scrubbed element. (5) tenant-scoping test uses a REAL second tenant (separate
  signup, separate JWT), not a mocked tenant_id swap — confirmed by reading the fixture setup.
  No overfit to fixtures, no vacuous asserts, no stubbed-away logic found.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: agent (advisor tool, full-transcript review)
1. Security: CLEAR — query filters `BatchJobItemRow.tenant_id == identity.tenant_id`, gated by
   `require_owner_or_admin`; tenant-isolation and member/viewer->403 both directly tested;
   `savings_usd` is a constant, nothing to leak.
2. Concurrency: CLEAR — read-only single SELECT+GROUP BY, no writes, no caching, no shared
   mutable state; nothing to race.
3. Architecture: CLEAR — the new repository method mirrors the existing `status_counts` shape;
   `stats_router.py` being a separate router from `batch_router` is the correct call, not a
   smell — `stats_router` uses JWT/session `require_owner_or_admin`, while `batch_router` uses
   sk-key `_authenticate`; mixing the two auth schemes in one router would be the actual
   architecture smell.
Verdict: PASS
Residue: one non-blocking note — `require_owner_or_admin`'s underlying check is `KEYS_MANAGE`,
  semantically odd for a read-only stats view though currently correct under the present role
  matrix (only owner/admin hold it). Tracked as a §7 spec delta, not a blocker.
Binding: advisory — this task never declared a `sensitivity:` line on its header (project-level
  `sensitivity: unset` per `add.py status`); recorded as advisory rather than assumed
  mechanical-and-binding, so it does not silently claim a bindingness this task never opted into.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (not applicable — outcome is PASS)
Reviewed by: self (agent), under autonomy: auto — auto-resolved on the complete evidence above.
  Tin's human checkpoint is the §3 freeze + mock-confirm already given ("approve", 2026-07-03)
  plus this gate's residue (two mock deviations, the browser-verification gap, the one
  non-blocking architecture note) surfaced in this same-turn report for his after-the-fact
  objection window, per the auto-gate's "human on residue" design (run.md). · date: 2026-07-03

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 403 rate on `GET /admin/batches/stats` (R1) — should sit near
  zero in practice since the nav entry itself is already gated; a nonzero rate signals either a
  client-side gating bug or direct-API probing, not normal use · 5xx rate on the same endpoint —
  should be zero (one real SELECT+GROUP BY); a sudden nonzero rate signals a DB-layer issue, not
  this task's logic.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-03 ("approve") -- this same approval also)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by self (agent), under autonomy: auto — auto-resolved on the complete evidence above.)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] swap `savings_usd` from the current constant `"0.00"` to a real
  `sum(list_price_usd - cost_usd)` query over `usage_source="batch"` the moment
  `batch-billing-accuracy` lands the `list_price_usd` column on `usage_records` (evidence: §3
  CONTRACT note + M2 build-grounding correction, 2026-07-03) — this task's own contract-cited
  promise, tracked here per its own text, not silently forgotten.
- [SPEC · open] `require_owner_or_admin`'s underlying permission check gates this read via
  `KEYS_MANAGE` — semantically odd for a statistics view, though correct under the present role
  matrix (evidence: Advisor 3-lens architecture pass, 2026-07-03). Revisit if the permission model
  ever grows a role holding `KEYS_MANAGE` that shouldn't see batch stats. Non-blocking.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

- [UDD · folded] the "build-grounding scrub" (re-checking an approved mock against the frozen [folded foundation-version 44]
  contract and build-time reality immediately before implementing, correcting transparently
  rather than building blindly or silently deviating) held 3-for-3 within this single task alone
  (`savings_usd` constant at build time; the `.toggle-row` referencing an unbuilt sibling task's
  control; the hero-sub copy asserting a live query that doesn't exist) — worth naming as a
  standing UDD step rather than rediscovering it ad hoc per task (evidence: this task, 2026-07-03).
