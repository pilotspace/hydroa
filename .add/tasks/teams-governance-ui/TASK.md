# TASK: Teams governance UI (/teams surface)

slug: teams-governance-ui · created: 2026-06-14 · stage: production
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

Touches (files · symbols · signatures): NEW `/teams` owner/admin surface (presentation-only — consumes the already-frozen teams contract + design system; NO gateway/BFF change). Verified anchors:
- NEW route `apps/dashboard/app/(dashboard)/teams/page.tsx` — server wrapper (`export const metadata = { title: "Hydroa" }` + default that renders `<TeamsPage/>`), mirroring `app/(dashboard)/models/page.tsx`.
- NEW `apps/dashboard/components/teams/TeamsPage.tsx` (`"use client"`) — master-detail on ONE page: teams list + create/delete/budget, and a members panel for the selected team. Decomposed into focused sub-files (700-line limit): `CreateTeamDialog.tsx`, `AddMemberDialog.tsx`, `TeamBudgetForm.tsx`, `TeamMembersPanel.tsx` (final split decided in §3/§5).
- Data seam (BFF passes gateway JSON VERBATIM — NO `{data}` envelope, unlike `/models`): `lib/bff-client.ts` `bffGet<T>(path)` · `bffPost<T>(path, body)` · `bffPatch<T>(path, body)` · `bffDelete(path): Promise<void>`; errors throw `BffError` with `.status` + `.problem.title`.
- Endpoints (frozen teams contract — `apps/gateway/src/gateway/teams/api/router.py`, prefix `/admin/teams`): `GET /admin/teams` → **bare `list[TeamResponse]`** · `POST /admin/teams` 201 `TeamResponse` (409 TEAM_EXISTS) · `PATCH /admin/teams/{id}` 200 `TeamResponse` (set/clear budget) · `GET /admin/teams/{id}` 200 `TeamDetailResponse` (members[]) · `DELETE /admin/teams/{id}` 204 · `POST /admin/teams/{id}/members` 201 `AddMemberResponse` (by `email` — 404 USER_NOT_FOUND, 409 MEMBER_EXISTS, 422 exactly-one-of) · `DELETE /admin/teams/{id}/members/{user_id}` 204.
- Response shapes (`teams/api/schemas.py`): `TeamResponse{id,name,tenant_id,created_at,member_count:int,key_count:int,team_budget_usd: str|None}` · `TeamDetailResponse{…,members: list[MemberResponse]}` · `MemberResponse{user_id,role:str,added_at}` · `AddMemberResponse{team_id,user_id,role,added_at}`. **`team_budget_usd` is a decimal-as-STRING, nullable** (`PatchTeamBudgetRequest.team_budget_usd: str|None`, positive-decimal validated; `null` clears).
- Design system (`components/ui/index.ts` barrel): `Card/CardContent`, `Table/TableHeader/TableBody/TableRow/TableHead/TableCell`, `Button` (variants default|secondary|outline|ghost|destructive; sizes default|sm|lg|icon), `Input` (native pass-through; budget uses `inputMode="decimal"`), `Badge`, `Loading`(role=status), `Empty`(title/description/action), `ErrorState`(role=alert; title/description/onRetry), `Success`. Role picker (lead|member): native `<select>` + `<label>` (jsdom-testable, fully a11y) — NO Radix Select (avoids the jsdom portal/pointer trap; foundation v14 polyfill lesson). NO `Label` primitive — plain `<label className="text-sm font-medium text-foreground" htmlFor>`.
- Dialog pattern (hand-rolled inline, NOT the Radix Dialog): overlay `<div className="fixed inset-0 z-50 …bg-foreground/40">` + panel `<div ref={trapRef} role="dialog" aria-modal="true" aria-labelledby|aria-label>`; `useFocusTrap<HTMLDivElement>(isOpen, onClose)` from `lib/use-focus-trap.ts` (Escape + Tab-wrap + focus-return baked in); `if (!isOpen) return null`; field error `<p role="alert" aria-live="polite" className="text-sm text-destructive">`; submit `disabled={isPending}` with `…` label; `noValidate` on `<form>`.
- NAV: `components/ui/app-shell.tsx:18` `NAV_ITEMS` — add `{ href: "/teams", label: "Teams", icon: Users }` (import `Users` from lucide-react), consistent with the owner/admin `/models` entry. (Role-filtered NAV stays a CARRIED concern — out of scope here.)
- `getErrorTitle(err)` helper: defined INLINE per page (not shared) — `BffError → err.problem.title`, `Error → err.message`, else "An error occurred".

Context (working folder): the v15 MILESTONE.md teams-governance-ui task (frontend half of the teams split; backend is the done teams-add-by-email CR). NO gateway/BFF contract change — the member-add CR already landed; this consumes it.

Honors (patterns / conventions): the model-management-ui surface shape (header + sequential state blocks + Card/Table); the hand-rolled dialog + `useFocusTrap` a11y convention; the four state patterns; CLAUDE.md design-for-failure (every contracted ERROR branch — 404/409/422 — gets its own §4 test + an ErrorState surface; mutations disable controls in-flight + invalidate on success); the v13/v15 a11y bar (axe serious/critical zero, keyboard-operable, labelled).

Anchors the contract cites: the NEW `/teams` route + `TeamsPage` master-detail · the frozen 7-endpoint teams contract via `bffGet/bffPost/bffPatch/bffDelete` (bare-list GET, no envelope) · `team_budget_usd` as nullable decimal-string · add-member BY EMAIL (`{email, role}`, 404/409/422 surfaced) · the hand-rolled dialog + `useFocusTrap` · the design-system primitives + native `<select>` role picker · `NAV_ITEMS` + `getErrorTitle`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Teams governance UI — a `/teams` owner/admin surface that lets a tenant manage teams (list · create · delete · set budget) and each team's members (view · add BY EMAIL · remove), consuming the already-frozen teams contract + the v15 design system. Presentation-only; NO gateway/BFF change.
Framings weighed: Master-detail on ONE `/teams` page — selecting a team reveals an inline members panel (chosen — one route, one RTL suite, satisfies the single exit criterion, jsdom-simple, no router-nav to test) · A dedicated `/teams/[id]` detail route (rejected — deep-linkable but doubles the surface + a second test suite for no contract gain here) · A modal team-detail dialog (rejected — nesting an add-member dialog inside a detail dialog is an a11y focus-trap hazard).
Must:
<must>
  - List all teams (`GET /admin/teams` → bare `list[TeamResponse]`, no envelope) in a Card+Table showing name · member_count · key_count · budget (the decimal-string, or an em-dash "—" when null); render the four state patterns (Loading role=status · Empty · ErrorState role=alert · data).
  - Create a team via a hand-rolled dialog (name `Input`, `POST /admin/teams` {name}); blank name is blocked client-side (Zod); on 201 the dialog closes + the list invalidates (refetches); on 409 TEAM_EXISTS the dialog shows an inline `role="alert"` error and the list is unchanged.
  - Set OR clear a team's budget inline per row (`PATCH /admin/teams/{id}` {team_budget_usd: <decimal-string> | null}); the input uses `inputMode="decimal"`; a non-numeric/≤0 value is blocked client-side; on success the row reflects the new budget (or "—" when cleared); the control is disabled in-flight.
  - Delete a team via a confirm dialog (`DELETE /admin/teams/{id}` → 204); on success the list invalidates and — if the deleted team was the selected one — the members panel clears.
  - Select a team to load its detail (`GET /admin/teams/{id}` → `TeamDetailResponse`); a members panel lists each member (user_id · role `Badge` · added_at) with its own Loading/Empty/Error states.
  - Add a member BY EMAIL via a dialog (email `Input` + a native `<select>` role picker lead|member, `POST /admin/teams/{id}/members` {email, role}); blank email blocked client-side; on 201 the dialog closes + the members panel invalidates; on 404 USER_NOT_FOUND the dialog shows "No user with that email in your tenant"; on 409 MEMBER_EXISTS "already a member"; on 422 the validation title.
  - Remove a member via a confirm dialog (`DELETE /admin/teams/{id}/members/{user_id}` → 204); on success the members panel invalidates.
  - Owner/admin-only: the gateway 403s a member on the list/detail GET → surface the `ErrorState` (no client `useCurrentUser` gate — untestable dead code, per the model-mgmt decision). NAV gains a `/teams` entry.
  - a11y/UX: every dialog is `role="dialog" aria-modal="true"` with `useFocusTrap` (Escape closes, Tab wraps, focus returns); every control is labelled (`<label htmlFor>`) + keyboard-operable + focus-visible; axe reports zero serious/critical (color-contrast excluded); every mutation disables its trigger while pending. NO gateway/BFF contract change.
</must>
Reject:
<reject>
  - Create with a blank/whitespace name -> client Zod blocks the submit (no request); a duplicate name reaching the server -> 409 "ERR_TEAM_EXISTS" (dialog error, list unchanged)
  - Add a member by an unknown OR cross-tenant email -> 404 "ERR_USER_NOT_FOUND" (dialog error "No user with that email in your tenant", members unchanged)
  - Add a member who is already on the team -> 409 "ERR_MEMBER_EXISTS" (dialog error, members unchanged)
  - Add a member with a blank email -> client blocks the submit; if it reaches the server -> 422 "ERR_PAYLOAD_INVALID" (dialog error)
  - Set a budget to a non-numeric / zero / negative value -> client blocks the submit; if it reaches the server -> 422 "ERR_PAYLOAD_INVALID" (form error, budget unchanged)
  - Any list/detail GET failure, including a member's 403 -> "ErrorState" (role=alert, no crash, no partial render)
  - A client `useCurrentUser` role gate / a new gateway or BFF endpoint / a `{data}` envelope unwrap on the bare-list GET -> "scope_creep"
</reject>
After:
<after>
  - `/teams` lists the tenant's teams; an owner/admin can create + delete teams, set/clear a budget, select a team to see its members, add a member by email, and remove a member; every contracted error branch (409/404/422/403) shows a non-crashing `role="alert"`; mutations invalidate the right query; axe is clean; the full dashboard suite (legacy + bff projects) stays green; no gateway/BFF contract changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Master-detail on ONE page (select-team → inline members panel) is the right navigation model — lowest confidence because the milestone says "team detail" without specifying routing; if wrong (a deep-linkable `/teams/[id]` is wanted), cost = a second route + a split test suite (moderate rework). Mitigation: in-page is the smaller, fully-testable surface that meets the single exit criterion; a route can be added later without touching the contract.
  - [ ] Native `<select>` for the lead|member role picker (vs the Radix `Select` DS primitive) — chosen for jsdom-testability (`userEvent.selectOptions`) + zero-config a11y; if the DS-consistency bar mandates Radix, cost = jsdom pointer/portal shims. (carried to the §3 freeze flag)
  - [ ] Budget edit is an inline per-row form (vs a dialog) — chosen to keep the dialog count low; low risk, isolated to the row.
  - [ ] The `member_count`/`key_count` shown are the list aggregates (not recomputed client-side) — confirmed: `TeamResponse` carries both as ints; the UI only displays them.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: List teams renders the table
  Given GET /admin/teams returns two teams (one with a budget, one with null)
  When the /teams page loads
  Then a row shows each team's name, member_count, key_count
  And the budgeted team shows its decimal-string and the null team shows "—"

Scenario: Empty teams shows the empty state
  Given GET /admin/teams returns []
  When the page loads
  Then the Empty state ("No teams yet") is shown
  And no team row is rendered

Scenario: List GET failure shows an alert
  Given GET /admin/teams returns 500
  When the page loads
  Then a role="alert" ErrorState is shown
  And the page does not crash or partially render a table

Scenario: Member is forbidden from the list
  Given GET /admin/teams returns 403 ERR_FORBIDDEN
  When a member opens /teams
  Then a role="alert" ErrorState is shown
  And no team row is rendered

Scenario: Create a team
  Given the create-team dialog is open with name "platform"
  When the form is submitted and POST /admin/teams returns 201
  Then the dialog closes
  And GET /admin/teams is re-fetched (the list invalidates)

Scenario: Create a team with a blank name is blocked client-side
  Given the create-team dialog is open with an empty name
  When the form is submitted
  Then a role="alert" field error is shown
  And no POST /admin/teams request is made

Scenario: Create a duplicate team name
  Given the create-team dialog is open with an existing name
  When POST /admin/teams returns 409 ERR_TEAM_EXISTS
  Then the dialog shows an inline role="alert" error
  And the team list is unchanged

Scenario: Set a team budget
  Given a team row with no budget
  When a decimal value is entered and PATCH /admin/teams/{id} returns 200
  Then the row reflects the new budget
  And the budget control was disabled while the request was in flight

Scenario: Clear a team budget
  Given a team row with a budget
  When the budget is cleared and PATCH /admin/teams/{id} returns 200 with team_budget_usd=null
  Then the row shows "—"

Scenario: Invalid budget is blocked client-side
  Given a team row budget input
  When a non-numeric or zero/negative value is entered and submitted
  Then a role="alert" error is shown
  And no PATCH request is made

Scenario: Delete a team
  Given the delete-confirm dialog is open for a team
  When confirmed and DELETE /admin/teams/{id} returns 204
  Then the list invalidates
  And if that team was selected, the members panel is cleared

Scenario: View a team's members
  Given a team is selected and GET /admin/teams/{id} returns two members
  When the members panel loads
  Then each member's user_id, role badge, and added_at are shown

Scenario: A team with no members shows the empty state
  Given a selected team whose GET /admin/teams/{id} returns members: []
  When the members panel loads
  Then the Empty state ("No members yet") is shown

Scenario: Add a member by email
  Given the add-member dialog is open with email "dev@acme.io" and role "member"
  When POST /admin/teams/{id}/members returns 201
  Then the dialog closes
  And GET /admin/teams/{id} is re-fetched (members invalidate)

Scenario: Add a member by an unknown or cross-tenant email
  Given the add-member dialog is open with an email not in the tenant
  When POST /admin/teams/{id}/members returns 404 ERR_USER_NOT_FOUND
  Then the dialog shows "No user with that email in your tenant"
  And the members list is unchanged

Scenario: Add a member who is already on the team
  Given the add-member dialog is open with an existing member's email
  When POST /admin/teams/{id}/members returns 409 ERR_MEMBER_EXISTS
  Then the dialog shows an inline role="alert" error
  And the members list is unchanged

Scenario: Add a member with a blank email is blocked client-side
  Given the add-member dialog is open with an empty email
  When the form is submitted
  Then a role="alert" field error is shown
  And no POST request is made

Scenario: Remove a member
  Given the remove-confirm dialog is open for a member
  When confirmed and DELETE /admin/teams/{id}/members/{user_id} returns 204
  Then GET /admin/teams/{id} is re-fetched (members invalidate)

Scenario: Dialogs are accessible
  Given any dialog (create / delete / add-member / remove) is open
  When axe runs on the surface
  Then there are zero serious/critical violations
  And the dialog is role="dialog" aria-modal="true" with focus trapped and Escape closing it
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SURFACE  /teams  (owner/admin dashboard page — presentation-only over the frozen teams contract)
  ROUTE  apps/dashboard/app/(dashboard)/teams/page.tsx  -> renders <TeamsPage/>  (metadata.title="Hydroa")
  NAV    components/ui/app-shell.tsx NAV_ITEMS += { href:"/teams", label:"Teams", icon: Users }

DATA SEAM (BFF JSON passes through VERBATIM — no {data} envelope):
  GET    /admin/teams                      -> list[TeamResponse]                 (bare array)
  POST   /admin/teams            {name}    -> 201 TeamResponse | 409 ERR_TEAM_EXISTS | 422 ERR_PAYLOAD_INVALID
  PATCH  /admin/teams/{id}  {team_budget_usd: <decimal-string>|null} -> 200 TeamResponse | 404 ERR_TEAM_NOT_FOUND | 422 ERR_PAYLOAD_INVALID
  DELETE /admin/teams/{id}                 -> 204 | 404 ERR_TEAM_NOT_FOUND
  GET    /admin/teams/{id}                 -> 200 TeamDetailResponse | 404 ERR_TEAM_NOT_FOUND
  POST   /admin/teams/{id}/members  {email, role:"lead"|"member"} -> 201 AddMemberResponse | 404 ERR_USER_NOT_FOUND | 409 ERR_MEMBER_EXISTS | 422 ERR_PAYLOAD_INVALID
  DELETE /admin/teams/{id}/members/{user_id} -> 204 | 404 ERR_MEMBER_NOT_FOUND
  (member role 403 ERR_FORBIDDEN on any GET -> surfaced as ErrorState)

TYPES (mirror teams/api/schemas.py; budget is a STRING):
  TeamResponse        { id, name, tenant_id, created_at, member_count:number, key_count:number, team_budget_usd: string|null }
  TeamDetailResponse  { ...TeamResponse, members: MemberResponse[] }
  MemberResponse      { user_id, role:string, added_at }
  AddMemberResponse   { team_id, user_id, role, added_at }

QUERY KEYS / MUTATIONS (TanStack):
  useQuery ["admin-teams"]            = bffGet<TeamResponse[]>("/admin/teams")
  useQuery ["admin-team", id]         = bffGet<TeamDetailResponse>(`/admin/teams/${id}`)   (enabled: id != null)
  createTeam  = bffPost("/admin/teams", {name})                         -> onSuccess invalidate ["admin-teams"]
  patchBudget = bffPatch(`/admin/teams/${id}`, {team_budget_usd})       -> onSuccess invalidate ["admin-teams"]
  deleteTeam  = bffDelete(`/admin/teams/${id}`)                         -> onSuccess invalidate ["admin-teams"] + clear selection if selected
  addMember   = bffPost(`/admin/teams/${id}/members`, {email, role})    -> onSuccess invalidate ["admin-team", id]
  removeMember= bffDelete(`/admin/teams/${id}/members/${user_id}`)      -> onSuccess invalidate ["admin-team", id]

OBSERVABLE DOM CONTRACT:
  - teams list: Card+Table; each row -> name, member_count, key_count, budget(string)|"—", a Select button, a budget form, a Delete button
  - state patterns: Loading(role=status) · Empty("No teams yet") · ErrorState(role=alert) — list AND members panel each render their own
  - dialogs (create/delete/add-member/remove): role="dialog" aria-modal="true" + aria-labelledby|aria-label; useFocusTrap (Escape closes, Tab wraps, focus returns); if(!open) return null
  - errors inside a dialog: <p role="alert" aria-live="polite"> ; 404 add-member -> "No user with that email in your tenant"
  - blank name / blank email / invalid budget: blocked client-side (Zod) — NO request issued
  - every mutation trigger: disabled while pending
  - axe: zero serious/critical (color-contrast excluded)
```

Least-sure flag surfaced at freeze: [spec] The master-detail-on-ONE-page navigation (select a team → inline members panel) vs a deep-linkable `/teams/[id]` detail route. Why least-sure: the milestone says "team detail" without prescribing routing; an enterprise reviewer might expect a deep-linkable URL per team. Cost if wrong: add a second route + split the RTL suite (~moderate, contract-stable — the data seam above is identical either way). Decision (auto): in-page master-detail — one route, one suite, satisfies the single exit criterion, jsdom-simple; a `/teams/[id]` route can be layered later without changing this contract. Secondary [contract]: the role picker is a native `<select>` (jsdom-testable) not the Radix `Select` DS primitive — a deliberate a11y/testability trade vs DS uniformity.

Status: FROZEN @ v1 — approved by ADD auto (bundle approval delegated per project autonomy=auto; presentation-only over an already-frozen gateway contract; no security surface — JWT lives in the BFF cookie, never in this client code).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (the dashboard global gate; the new surface aims higher — every state + error branch tested)
Plan (one test per scenario, asserting behavior not internals — RTL + msw at `http://localhost:3000/api/gw/...`, fresh QueryClient per test, `axeSeriousCritical` helper):
<test_plan>
  - test_list_renders_rows: GET teams → two teams / render / assert names + counts + budget-string + "—" for null
  - test_empty_shows_empty_state: GET → [] / render / assert "No teams yet" + no row
  - test_list_error_shows_alert: GET → 500 / render / assert role=alert + no table
  - test_member_forbidden_shows_alert: GET → 403 / render / assert role=alert
  - test_create_team_success: open create dialog, type name, POST→201 / assert dialog closes + GET refetched (getCount≥2)
  - test_create_blank_name_blocked: open dialog, submit empty / assert field role=alert + POST never called
  - test_create_duplicate_409: POST→409 ERR_TEAM_EXISTS / assert dialog role=alert + list row count unchanged
  - test_set_budget_success: enter decimal, PATCH→200 / assert row shows new budget + control disabled mid-flight (delay)
  - test_clear_budget_success: clear, PATCH→200 team_budget_usd=null / assert row shows "—"
  - test_invalid_budget_blocked: enter "abc"/"0" / submit / assert role=alert + PATCH never called
  - test_delete_team_success: open confirm, confirm, DELETE→204 / assert GET refetched; selecting-then-deleting clears panel
  - test_view_members: select team, GET detail → 2 members / assert each user_id + role badge + added_at
  - test_members_empty: select team, GET detail → members:[] / assert "No members yet"
  - test_add_member_by_email_success: open add dialog, email+role, POST→201 / assert dialog closes + detail refetched
  - test_add_member_unknown_email_404: POST→404 ERR_USER_NOT_FOUND / assert "No user with that email in your tenant" + members unchanged
  - test_add_member_exists_409: POST→409 ERR_MEMBER_EXISTS / assert dialog role=alert + members unchanged
  - test_add_member_blank_email_blocked: submit empty email / assert field role=alert + POST never called
  - test_remove_member_success: open confirm, confirm, DELETE→204 / assert detail refetched (members invalidate)
  - test_axe_clean: render page + open each dialog / assert axeSeriousCritical == [] + dialog role=dialog aria-modal + Escape closes
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/teams/` `apps/dashboard/app/(dashboard)/teams/` `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/teams-governance-ui/`
<!-- SCOPE NOTE: mirrors model-management-ui's declaration. NEW source = components/teams/ + app/(dashboard)/teams/; the ONLY shared-file edit is app-shell.tsx NAV_ITEMS (one entry). tests-bff/ holds the RTL suite. .next/ + coverage/ + tsconfig.tsbuildinfo are verify-tooling artifacts (vitest --coverage, next lint) — declared so the §5 scope gate doesn't red on them (same precedent as the gateway's .coverage/.ruff_cache). NO gateway/BFF source, NO lib/ change (bff-client + use-focus-trap reused as-is), NO new dependency. -->
Strategy (ordered batches): 1. RED RTL suite `apps/dashboard/tests-bff/teams-governance.test.tsx` (one test per §2 scenario). 2. design-system-only sub-components: `CreateTeamDialog.tsx`, `AddMemberDialog.tsx` (hand-rolled dialog + useFocusTrap), `TeamBudgetForm.tsx` (inline budget edit), `TeamMembersPanel.tsx` (detail query + members table). 3. `TeamsPage.tsx` (list query, selection state, wires the four mutations + invalidation). 4. route `app/(dashboard)/teams/page.tsx` + NAV entry in `app-shell.tsx`. 5. run the bff suite + next lint + full `vitest run --coverage` green.
Safety rule (feature-specific): every mutation `onError` keeps the dialog open and shows the BffError title (never a silent failure — the model-mgmt DEFECT lesson); every mutation trigger is `disabled` while pending (no double-submit); client-side Zod blocks blank name / blank email / invalid budget BEFORE any request; the JWT/bearer is never read or rendered in this client code (it lives only in the BFF cookie).
Code lives in: `apps/dashboard/components/teams/` + `apps/dashboard/app/(dashboard)/teams/`
Constraints: do NOT change any test or the contract; reuse existing primitives + helpers only (NO new npm dependency); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — teams suite 22/22; full dashboard suite 166 passed across 92 files (legacy + bff projects)
- [x] coverage did not decrease — 92.28% statements TOTAL, the 80% gate held (vitest --coverage EXIT=0)
- [x] no test or contract was altered during build — §3 FROZEN untouched; the tests→build tripwire re-snapshotted clean on each re-cross (3 re-crosses: selector fix, then the adversarial DEFECT fix)
- [x] the green was EARNED, not gamed — adversarial refute-read (subagent, sonnet) returned **NOT-EARNED** on first pass, finding a real DEFECT (silent budget-PATCH failure) + 4 GAPs + 3 NITs. DEFECT FIXED via red→green (`test_budget_patch_server_error` was RED — no alert — then GREEN after adding `onError`); GAPs 1/2/3 + NITs 1/2 closed; GAP4 (draft persist) + NIT3 (fixture-title coupling) judged acceptable (findByText matches the span text node, not the input value; the 409 title is the fixture's mandated string)
- [x] concurrency / timing safe — no shared mutable state; each mutation disables its own trigger while pending (verified by `test_set_budget_success` delay assertion + the disabled-while-pending pattern); query invalidation keyed correctly (`["admin-teams"]` for list, `["admin-team", id]` for detail)
- [x] no exposed secrets / injection / unexpected dependencies — NO JWT/bearer read or rendered in client code (auth lives in the BFF cookie via credentials:"include"); no string-interpolated HTML; zero new npm dependencies (reused bff-client + use-focus-trap + design-system primitives)
- [x] layering & dependencies follow CONVENTIONS.md — presentation-only; data flows page→bff-client→BFF; NO gateway/BFF contract change; the bare-list GET is consumed without a `.data` unwrap; component decomposition respects the 700-line limit (7 focused files)
- [x] reviewed under `autonomy: auto` — adversarial subagent (NOT-EARNED → fixes → re-verified) + manual review; security clean (no secret surface; owner/admin enforced server-side, member 403 → ErrorState)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every component referenced: route `page.tsx`→`TeamsPage`→{`CreateTeamDialog`,`ConfirmDialog`,`TeamBudgetForm`,`TeamMembersPanel`→`AddMemberDialog`+`ConfirmDialog`}; NAV `/teams` entry added to `app-shell.tsx` (asserted by `test_nav_exposes_teams`); every mutation/query exercised by a test
- [x] DEAD-CODE (code) — no orphaned symbol; `Success` state intentionally not used (consistent with ModelsPage); all exported types consumed
- [x] SEMANTIC (prose / non-code) — n/a (code change)

### GATE RECORD
Outcome: PASS
Evidence: 166 passed / 92.28% cov (gate held) · next lint clean · teams 22/22 incl. axe(serious/critical=0)+keyboard+4 state patterns · adversarial refute-read NOT-EARNED→DEFECT fixed red→green→re-verified, GAPs/NITs closed or accepted
Reviewed by: ADD auto-gate (adversarial subagent + manual) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): add-member 404 rate (typo'd / cross-tenant emails) vs 201; budget-PATCH 422 rate (client validation too lax if non-trivial); create-team 409 rate (name collisions); the /teams list/detail 403 rate (members hitting an owner/admin surface — a NAV-visibility smell that feeds the role-filtered-NAV follow-up).
Spec delta for the next loop: the `/teams` surface confirms the master-detail-in-page pattern works for owner/admin governance; the role-filtered NAV is now a sharper need (members see /models + /teams links they 403 on). `governance-completion-ui` will add a team_id dropdown sourced from this same `["admin-teams"]` query — reuse it, don't refetch differently.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · open] the silent-mutation-failure DEFECT recurred (budget PATCH had no onError) — the model-mgmt lesson is now a STANDING rule: every useMutation needs an onError that surfaces the BffError title, and every contracted error branch (404/409/422) needs its own red→green test, not just the happy path — evidence: adversarial NOT-EARNED → test_budget_patch_server_error red→green
- [UDD · open] getByLabelText substring-matches aria-label across elements (the budget input + "Save budget for X" button collided) — role-scoped queries (`getByRole("textbox", {name})`) are the disambiguator; design labels so no control's accessible name is a superstring of another's — evidence: 3 budget tests failed on "multiple elements" until role-scoped
- [ADD · open] the bare-list-vs-{data}-envelope difference between `/admin/teams` (bare) and `/admin/models` ({object,data}) is a real footgun — GROUND must record the response envelope per-endpoint, not assume uniformity — evidence: §0 explicitly flagged "BARE array, no {data} unwrap"
- [SDD · open] a master-detail UI satisfies one "view + manage members" exit criterion with one route + one suite (vs a `/teams/[id]` route split) — the in-page selection model is the lighter contract when deep-linking isn't required — evidence: the §3 least-sure flag chose it, all 22 tests in one file
