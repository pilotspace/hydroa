# TASK: Routing health UI (/routing read-only)

slug: routing-health-ui · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): NEW `/routing` READ-ONLY page — presentation-only over ONE already-frozen gateway contract; NO gateway/BFF change. Verified anchors:
- NEW route `apps/dashboard/app/(dashboard)/routing/page.tsx` → renders `<RoutingPage/>` (mirrors models/settings route shape; `export const metadata = { title: "Hydroa" }`). NEW `apps/dashboard/components/routing/RoutingPage.tsx` (`"use client"`).
- GATEWAY contract (`apps/gateway/src/gateway/proxy/api/routing_admin_router.py:get_routing_admin`, `GET /admin/routing`, `require_owner_or_admin`): returns 200 with the EXACT shape (all four top-level keys ALWAYS present): `{ retry_policy: {max_retries:int, backoff_base_s:float}, cooldown: {enabled:bool, threshold:int, ttl_s:int, window_s:int}, model_groups: {[alias:string]: string[]}, candidates: [{model_id:string, alias:string, state:string}] }`. A member → 403 ERR_AUTH_FORBIDDEN. Response is secrets-free (only named Settings knobs + model ids). NO PUT — read-only surface.
- CIRCUIT STATE enum (`proxy/infrastructure/redis_cooldown_gate.py:snapshot_state`): `state ∈ "open" | "half_open" | "closed" | "unknown"`. "closed"=healthy (traffic flows), "open"=tripped (failing), "half_open"=probing/recovery, "unknown"=Redis read failed (fail-open, 200 still returned). When `cooldown.enabled` is false (threshold=0, gate None) ALL candidates are "closed" with zero Redis I/O.
- Data seam (BFF verbatim, no envelope): `bffGet<T>("/admin/routing")` returns the object directly (the BFF catch-all `app/api/gw/[...path]/route.ts` passes gateway JSON verbatim; `/admin/routing` is a plain object — NO `.data` unwrap). `BffError{status, problem.title}`; member 403 → `error.status===403`.
- UI primitives (frozen, `components/ui`): `Badge` variants {default,secondary,outline,success,warning,destructive} → map state: closed→success, half_open→warning, open→destructive, unknown→secondary. `Table`/`TableHeader`/`TableBody`/`TableRow`/`TableHead`/`TableCell`/`TableCaption` for the candidates grid. `Card`/`CardHeader`/`CardTitle`/`CardContent` for the retry/cooldown config blocks. `Loading`(role=status)/`Empty`/`ErrorState`(role=alert) — the four state patterns. `getErrorTitle(err)` inline (BffError→problem.title; Error→message; else default).
- NAV: `components/ui/app-shell.tsx` `NAV_ITEMS` += `{href:"/routing", label:"Routing", icon: <lucide health icon>}` (e.g. `Activity` or `Network`).
- Tests: `apps/dashboard/tests-bff/` project, msw at `http://localhost:3000/api/gw/admin/routing`, fresh QueryClient/test, local `axeSeriousCritical` (color-contrast off).

Context (working folder): the v15 MILESTONE.md routing-health-ui task. NO gateway/BFF contract change — one existing admin contract consumed READ-ONLY (GET only).

Honors (patterns / conventions): the model/teams/settings surface shape (header + four state blocks); ModelsPage/TeamsPage query pattern (`useQuery` + Loading/Error/Empty/Data); v13/v15 a11y bar (labelled controls, keyboard, axe zero serious/critical); CLAUDE.md design-for-failure (a settled 403 must not retry-storm → `retry:false`; the gateway already fails-open to "unknown" per candidate so the UI never crashes on partial Redis failure). NO secret surface (read-only, secrets-free response).

Anchors the contract cites: the NEW `/routing` read-only page · the ONE frozen contract `GET /admin/routing` (retry_policy · cooldown · model_groups · candidates[state]) via `bffGet` · the 4-state circuit enum → `Badge` variant map · per-surface Loading/Empty/Error + role-gated 403 (member→ErrorState) · `NAV_ITEMS` + `getErrorTitle`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Routing health UI — a `/routing` READ-ONLY page where an owner/admin views the proxy's routing configuration (retry policy · cooldown/circuit-breaker config · model groups) and per-candidate circuit state (open/half_open/closed/unknown). Presentation-only over one frozen gateway contract; NO gateway/BFF change, NO mutation.
Framings weighed: One read-only `/routing` page with config Cards + a candidates Table (chosen — the contract is a single GET with 4 blocks; Cards for the two scalar config blocks + a Table for the per-candidate state grid is the clearest health hierarchy, one route, one RTL suite) · A live-polling dashboard with auto-refresh (rejected — scope creep; the contract is a snapshot GET, polling is a later observability task, not v15 coverage) · Folding routing into /settings as a 4th tab (rejected — routing is read-only health, settings is read-write config; mixing the two muddies the owner-only vs owner/admin role lines and the mental model).
Must:
<must>
  - Render a `/routing` page that on mount issues `GET /admin/routing` (owner/admin) and shows the four blocks: retry_policy, cooldown, model_groups, candidates.
  - retry_policy block: show max_retries and backoff_base_s (labelled, read-only).
  - cooldown block: show enabled (a clear on/off indicator), threshold, ttl_s, window_s (labelled, read-only); when enabled=false the block makes clear the circuit breaker is disabled.
  - model_groups block: list each alias and its ordered candidate model_ids (read-only).
  - candidates block: a Table with one row per {alias, model_id} showing the circuit state as a labelled `Badge` (closed→success, half_open→warning, open→destructive, unknown→secondary); the state text is readable, not color-only (the word "open"/"closed"/etc. is present for a11y).
  - State patterns: Loading while fetching; ErrorState (role=alert) on GET failure; an Empty affordance when model_groups/candidates are empty (no models configured); NAV gains a `/routing` entry.
  - a11y/UX: axe zero serious/critical; every block has an accessible heading/label; the candidates Table has a caption/accessible name; circuit state is never conveyed by color alone. NO gateway/BFF contract change, NO mutation control on the page.
</must>
Reject:
<reject>
  - A member (non owner/admin) opening /routing -> GET 403 "ERR_AUTH_FORBIDDEN" (ErrorState, no config/table rendered) -> "role_leak" if config shown
  - A GET 500 / network failure -> ErrorState (role=alert), no crash -> "unhandled_error"
  - An empty model_groups/candidates (no models configured) -> a clear Empty state, NOT a crash or a blank table -> "empty_unhandled"
  - Conveying circuit state by Badge color ALONE (no text) -> "a11y_color_only"
  - Any write/mutation control (no PUT exists on this contract) -> "scope_creep"
  - A `{data}` envelope unwrap (the BFF passes /admin/routing verbatim as a plain object) -> "scope_creep"
  - Retrying a settled 403 (deterministic) instead of failing to ErrorState -> "retry_storm"
</reject>
After:
<after>
  - `/routing` shows retry policy, cooldown config, model groups, and a per-candidate circuit-state table for an owner/admin; a member sees a 403 ErrorState with no config leaked; a GET failure shows an inline alert without crashing; an empty config shows an Empty state; circuit state is readable (text + color); the full dashboard suite stays green; no gateway/BFF contract changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The circuit-state → Badge-variant mapping (closed→success, half_open→warning, open→destructive, unknown→secondary) is a UX choice, not a contract fact. Lowest confidence because "unknown" could read as warning rather than neutral; if wrong, cost = a one-line variant swap (no contract/test-shape change — the test asserts the state TEXT + a labelled badge, not the specific color). Mitigation: assert the readable state text + an accessible badge label in tests, keep the color as a thin presentational layer.
  - [ ] The page is READ-ONLY — the GET /admin/routing contract exposes no PUT, so no edit affordance is in scope (confirmed: router has only a GET handler).
  - [ ] "unknown" state is a normal fail-open value (Redis read error), not an error to surface as an alert — the page shows it as a candidate badge, the page itself is still a 200 success (confirmed by the router's per-candidate try/except → "unknown").
  - [ ] Default role reaching /routing is owner/admin; a member's 403 is handled gracefully (same role-gate pattern as ModelsPage).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Routing page renders the full config + candidates
  Given GET /admin/routing returns retry_policy{max_retries:4,backoff_base_s:0.75}, cooldown{enabled:true,threshold:3,ttl_s:60,window_s:120}, model_groups{fast:[vendor/a,vendor/b]}, candidates[{fast,vendor/a,closed},{fast,vendor/b,open}]
  When the page loads
  Then retry policy max_retries=4 and backoff_base_s=0.75 are shown
  And cooldown shows enabled with threshold 3, ttl 60, window 120
  And model group "fast" lists vendor/a and vendor/b
  And the candidates table shows vendor/a as closed and vendor/b as open with readable state text

Scenario: Cooldown disabled is clearly indicated
  Given GET /admin/routing returns cooldown{enabled:false,threshold:0,...} and all candidates closed
  When the page loads
  Then the cooldown block indicates the circuit breaker is disabled
  And every candidate shows state "closed"

Scenario: Circuit state is readable, not color-only
  Given a candidate with state "open"
  When the row renders
  Then the badge has an accessible label/text containing "open" (not color alone)

Scenario: Unknown state renders without erroring the page
  Given GET /admin/routing returns a candidate with state "unknown"
  When the page loads
  Then the candidate shows state "unknown" and the page is not in an error state

Scenario: Member is forbidden
  Given GET /admin/routing returns 403 ERR_AUTH_FORBIDDEN
  When the page loads
  Then a role="alert" ErrorState ("forbidden") is shown
  And no retry_policy / cooldown / candidates config is rendered

Scenario: GET failure shows an alert
  Given GET /admin/routing returns 500
  When the page loads
  Then a role="alert" ErrorState is shown (no crash)

Scenario: Empty configuration shows an Empty state
  Given GET /admin/routing returns model_groups{} and candidates[]
  When the page loads
  Then an Empty state indicates no models/candidates are configured (no blank table crash)

Scenario: Routing surface is accessible
  Given the page is loaded with data
  When axe runs
  Then there are zero serious/critical violations
  And the candidates table has an accessible name and every block has a heading

Scenario: NAV exposes Routing
  Given the app shell with activePath /routing
  Then the Primary nav has a Routing link to /routing marked aria-current=page
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SURFACE  /routing  (owner/admin dashboard — READ-ONLY over 1 frozen contract)
  ROUTE  apps/dashboard/app/(dashboard)/routing/page.tsx -> <RoutingPage/> (metadata.title="Hydroa")
  NAV    app-shell.tsx NAV_ITEMS += { href:"/routing", label:"Routing", icon: Activity }
  PAGE   <RoutingPage/> ("use client") = config Cards (retry_policy, cooldown) + model_groups list + candidates Table

DATA SEAM (BFF verbatim, no {data} envelope) — bffGet only (READ-ONLY, NO mutation):
  GET  /admin/routing  -> RoutingConf | 403 ERR_AUTH_FORBIDDEN | 500   (owner/admin)

TYPES (mirror gateway get_routing_admin):
  RetryPolicy = { max_retries:number, backoff_base_s:number }
  Cooldown    = { enabled:boolean, threshold:number, ttl_s:number, window_s:number }
  Candidate   = { model_id:string, alias:string, state:"open"|"half_open"|"closed"|"unknown" }
  RoutingConf = { retry_policy:RetryPolicy, cooldown:Cooldown, model_groups:{[alias:string]:string[]}, candidates:Candidate[] }

QUERY KEY:
  useQuery ["admin-routing"] = bffGet("/admin/routing");  retry:false (a 403 is deterministic)
  NO mutation (the contract exposes no PUT)

STATE → BADGE VARIANT (presentational; the state TEXT is the a11y contract):
  closed -> success · half_open -> warning · open -> destructive · unknown -> secondary
  each badge renders the state word (e.g. "closed") so state is never color-only

OBSERVABLE DOM CONTRACT:
  - on mount: Loading(role=status); on success: 4 blocks render
  - retry_policy: labelled max_retries + backoff_base_s
  - cooldown: a clear enabled/disabled indicator + threshold + ttl_s + window_s (all labelled)
  - model_groups: each alias heading + its ordered model_id list
  - candidates: a Table (accessible name/caption) with columns Alias | Model | State; one row per candidate; State is a Badge whose TEXT is the state word
  - role-gate: 403 -> ErrorState(role=alert) "forbidden", NO config rendered
  - failure: GET 500/network -> ErrorState(role=alert), no crash
  - empty: model_groups {} AND candidates [] -> Empty state (no blank-table crash)
  - axe: zero serious/critical (color-contrast excluded); circuit state never color-only
  - NO write/mutation control anywhere on the page
```

Least-sure flag surfaced at freeze: [spec] The circuit-state → Badge-variant color mapping (closed→success, half_open→warning, open→destructive, unknown→secondary) is the least-sure point — it is a UX judgment, not a contract fact (notably "unknown"→secondary vs warning). Why least-sure: a reviewer may prefer "unknown" to read as a warning. Cost if wrong: a one-line variant swap; NO contract or test-shape change because the test asserts the readable state TEXT + a labelled badge (the a11y contract), never the specific color class. Decision (auto): map by health semantics (success/warning/destructive/secondary) with the state word always present; color is a thin presentational layer over the text. Secondary [contract]: the "unknown" state is a NORMAL fail-open value (per-candidate Redis read error) — the PAGE stays a 200 success and shows the badge; it is NOT escalated to a page-level ErrorState (only a GET-level 403/500 is).

Status: FROZEN @ v1 — approved by ADD auto (bundle approval delegated per project autonomy=auto; presentation-only READ-ONLY over an already-frozen gateway contract; NO mutation, NO secret surface — the response is secrets-free by the gateway contract; the one judgment call (state→color) is presentational with the a11y text contract holding regardless).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag. Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the bundle's lowest-confidence flag was surfaced at the freeze. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (dashboard global gate; the new surface aims higher — every block + error/empty branch tested)
Plan (one test per scenario — RTL + msw at `http://localhost:3000/api/gw/admin/routing`, fresh QueryClient/test, `axeSeriousCritical`):
<test_plan>
  - test_renders_full_config_and_candidates: GET full shape → assert retry_policy values + cooldown values + model_groups alias/list + candidates table rows with readable state text (closed/open)
  - test_cooldown_disabled_indicated: GET cooldown.enabled=false → assert the disabled indicator + all candidates closed
  - test_state_is_readable_not_color_only: candidate state "open" → assert a badge with accessible text containing "open" (getByText / role, not a color class)
  - test_unknown_state_no_page_error: candidate state "unknown" → assert the candidate shows "unknown" AND no role=alert page error
  - test_member_forbidden_403: GET→403 → assert ErrorState role=alert + NO retry_policy/cooldown/candidates rendered (queryByText absent)
  - test_get_failure_500_alert: GET→500 → assert role=alert (no crash)
  - test_empty_config_empty_state: GET model_groups{} candidates[] → assert Empty state (no blank-table crash)
  - test_routing_axe_clean: GET full shape → assert axeSeriousCritical==[] + table accessible name + block headings
  - test_nav_exposes_routing: AppShell activePath=/routing → assert Routing link href=/routing aria-current=page
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/routing/` `apps/dashboard/app/(dashboard)/routing/` `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/routing-health-ui/`
<!-- SCOPE NOTE: mirrors model/teams/settings declarations. NEW source = components/routing/ + app/(dashboard)/routing/; the ONLY shared-file edit is app-shell.tsx NAV (one entry). tests-bff/ holds the RTL suite. .next/coverage/tsbuildinfo are verify-tooling artifacts (coverage now also gitignored). NO gateway/BFF source, NO lib/ change (bff-client reused), NO new dependency. -->
Strategy (ordered batches): 1. RED RTL suite `apps/dashboard/tests-bff/routing-health.test.tsx`. 2. `RoutingPage.tsx` (useQuery ["admin-routing"] retry:false; Loading/Error/Empty/Data; retry/cooldown Cards; model_groups list; candidates Table with state Badge). 3. route + NAV. 4. bff suite + next lint + full vitest --coverage green.
Safety rule (feature-specific): READ-ONLY — NO mutation control. Circuit state is never color-only (the state word is always rendered). A settled 403 does not retry (retry:false). "unknown" is a normal fail-open badge, not a page error. NO `{data}` unwrap (verbatim plain object).
Code lives in: `apps/dashboard/components/routing/` + `apps/dashboard/app/(dashboard)/routing/`
Constraints: do NOT change any test or the contract; reuse existing primitives + helpers only (NO new npm dependency); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 10/10 routing-health + 25/25 test files / 192 tests; full vitest --coverage EXIT=0
- [x] coverage did not decrease — All files 93.08% (gate 80%); RoutingPage 97.6%, app-shell 100%
- [x] no test or contract was altered during build — §3 FROZEN untouched; build_tampered tripwire held across both re-cross advances (matcher fixes + GAP-closing assertions done in tests phase, not build)
- [x] the green was EARNED, not gamed — adversarial refute-read (subagent, sonnet) returned EARNED-WITH-GAPS, ZERO DEFECTs; the role-gate is airtight (403→pure early-return ErrorState, no config destructured/rendered). GAPs closed this re-cross: half_open now fixtured+asserted (was the only untested enum state), ttl_s asserted, 403 no-leak strengthened to assert NO card headings, empty model_groups branch asserted. Test assertions are value-bearing (distinct fixture numbers 4/0.75/3/60/120, state words within the table), not vacuous.
- [x] concurrency / timing of the risky operation is safe — single read-only GET; retry:false (a settled 403/500 does not retry-storm); no mutation, no shared mutable state; the gateway already fails-open per-candidate to "unknown" so partial Redis failure never crashes the page
- [x] no exposed secrets, injection openings, or unexpected dependencies — READ-ONLY surface; the gateway response is secrets-free by contract (only named Settings knobs + model ids); no new npm dependency; bff-client reused; NO mutation control anywhere
- [x] layering & dependencies follow CONVENTIONS.md — presentation-only over ONE frozen gateway contract; bffGet seam (verbatim plain object, NO {data} unwrap); NO gateway/BFF/lib change; mirrors ModelsPage/TeamsPage query+state pattern; design-system primitives only (Card/Table/Badge/states)
- [x] a person reviewed and approved the change — auto-resolved under autonomy=auto (delegated): presentation-only READ-ONLY, contract frozen, no secret/mutation surface; adversarial review clean (no DEFECT, no security finding → no HARD-STOP)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — RoutingPage referenced by route app/(dashboard)/routing/page.tsx; NAV_ITEMS += /routing (app-shell.tsx); STATE_VARIANT/Metric used in render; all test-covered (test_nav_exposes_routing, test_renders_full_config_and_candidates)
- [x] DEAD-CODE (code) — no orphaned symbol; every interface/const consumed (STATE_VARIANT complete Record over the 4-state enum, no unreachable branch)
- [x] SEMANTIC (prose / non-code) — §2 scenario example values aligned to the FULL fixture (4/0.75/3/60/120) for traceability (was a stale 2/0.5/60 illustration the reviewer flagged)

ADVERSARIAL VERDICT (this loop): EARNED-WITH-GAPS → GAPs CLOSED. ZERO DEFECTs. Security: role-gate airtight (403 early-returns ErrorState; no config flows past the guard — confirmed code + test).
  CLOSED: half_open fixture+assert; ttl_s assert; 403 asserts NO card headings (not just retry-policy text); empty model_groups branch asserted; nav-icon aria-hidden a11y NIT applied (matches states.tsx convention, axe still clean).
  ACCEPTED: scenario↔fixture wording aligned (doc); no behavioral residue.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: ADD auto (autonomy=auto; read-only surface, adversarial EARNED-WITH-GAPS clean, role-gate airtight) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): GET /admin/routing 403 rate (a member reaching /routing → role-filtered NAV signal); 500/network rate; the per-candidate "unknown" rate (a sustained spike = a Redis health problem the gateway is failing-open on, surfaced here read-only).
Spec delta for the next loop: routing is the second read-only health surface; if owners want live circuit-state refresh, that is a follow-up observability task (polling/SSE), not a contract change. The "unknown" badge is the only window into Redis-gate degradation — a future enhancement could link it to an alert. NAV still shows /routing to every role (graceful 403), role-filtered NAV remains the carried end-state for feature-coverage-verify.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] A complete enum (4 circuit states) needs a fixture per VALUE, not per error-class — the first-pass suite covered closed/open/unknown but silently skipped half_open (the map handled it, so coverage stayed high while a value went untested); the adversarial refute-read caught it (evidence: test_half_open_state_rendered added this re-cross).
- [TDD · folded] A "no config leaked on 403" assertion must enumerate EVERY block heading, not just one — asserting only `/retry policy/i` absent would pass a buggy impl that rendered empty Cooldown/Model-groups/Candidate card shells (evidence: test_member_forbidden_403 strengthened to query all four card headings absent).
- [UDD · folded] Decorative icons paired with a visible text label must carry aria-hidden — the NAV icons lacked it (inconsistent with states.tsx); one attribute removes a redundant SR announcement across all 7 nav items (evidence: app-shell.tsx Icon aria-hidden added, axe still clean).
