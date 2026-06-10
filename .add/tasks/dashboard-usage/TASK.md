# TASK: Catalog, usage & cost analytics, budget setting

slug: dashboard-usage · created: 2026-06-10 · stage: mvp
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Dashboard — model catalog, usage & cost analytics, budget setting
Framings weighed: one combined /usage page with catalog section + usage section + budget widget (chosen — owner needs cost context alongside catalog reference; flat nav) · separate /usage + /models pages (rejected — /models page is read-only reference with no owner action; two navigations for one workflow adds friction) · /budget as a third page (rejected — tiny single-widget page; budget is part of the cost analytics context)
Must:
<must>
  - /usage page (authenticated, owner or member): on mount calls GET /admin/usage with Bearer token; renders four aggregate cards: Total Requests, Total Prompt Tokens, Total Completion Tokens, Total Cost (USD); renders a table of up to 50 newest usage records with columns: Model, Prompt Tokens, Completion Tokens, Cost (USD), Status, Date
  - /usage page: below the usage section renders a read-only model catalog section by calling GET /v1/models with Bearer token; renders a table with columns: Model ID, Name, Context Length, Prompt/token, Completion/token
  - /usage page: renders a budget widget showing current monthly ceiling (budget_usd_monthly) and current month spend (spent_usd_month) from GET /admin/budget
  - Budget edit (owner only): owner sees an "Edit Budget" button; clicking opens an inline form pre-filled with the current value (or empty for null); submitting PUTs { budget_usd_monthly: str | null } to /admin/budget; on 200 the widget refreshes with the new ceiling
  - Budget edit null/clear: submitting an empty budget field sends { budget_usd_monthly: null } — clears the ceiling to unlimited
  - All authenticated fetch requests include Authorization: Bearer <token> read from localStorage "ai_proxy_token" (via existing lib/api-client.ts)
  - Route guard: navigating to /usage when localStorage "ai_proxy_token" is absent or exp-expired redirects to /login before rendering (same guard as /keys)
  - Every section handles four UI states: loading (skeleton/spinner), empty (no records / no models), error (problem+json title surfaced), success (normal content)
  - 401 from any authenticated call clears the localStorage token and redirects to /login (delegated to existing apiGet/apiPut in lib/api-client.ts)
  - Member role: sees usage, catalog, and budget widget (read-only spend/ceiling); the "Edit Budget" button is NOT rendered for members; a 403 from PUT /admin/budget (if triggered by non-UI means) surfaces the problem+json title
</must>
Reject:
<reject>
  - Budget edit form submitted with a negative number string → inline field error, no API call (client-side Zod: z.number().min(0) after parseFloat; or z.string().regex for decimal)
  - Budget edit form submitted with a non-numeric string (e.g. "abc") → inline field error, no API call
  - 403 from PUT /admin/budget (member calling via non-UI path) → inline error showing problem+json title "ERR_AUTH_FORBIDDEN"; budget widget unchanged
  - 422 from PUT /admin/budget → inline error showing problem+json title; budget widget unchanged
  - Unauthenticated access to /usage → redirects to /login; content never rendered
</reject>
After:
<after>
  - After load: owner sees usage totals + records table + model catalog + budget widget all populated (or empty-state where applicable)
  - After budget edit submitted: the budget widget shows the new ceiling; PUT /admin/budget was called exactly once with the correct body
  - After budget cleared (null submit): the widget shows "Unlimited" (or equivalent); PUT /admin/budget was called with { budget_usd_monthly: null }
  - After 401 on any call: localStorage is clear; user is on /login
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Member role is determined client-side by decoding the JWT role claim — lowest confidence because the JWT payload shape (which claim carries the role) is inherited from the tenant-identity task; if the role claim is not "role" or is absent, the "Edit Budget" button show/hide logic breaks; if wrong: fall back to always showing the edit button and relying solely on the 403 response path — minor UX regression (member sees button, hits 403, sees error) but no security issue (gateway enforces authorization)
  ⚠ GET /v1/models requires a valid Bearer JWT (same token as /admin/* calls) — lowest confidence because the model-catalog task TASK.md §3 specifies auth on /v1/models; if the token for a member does not satisfy /v1/models auth (e.g. key-scope vs tenant-scope), the catalog section fails with 401; if wrong: render catalog error state gracefully using the existing error UI pattern — contained, no contract change
  - [x] No charting library added — MVP uses aggregate cards + table only; avoids new dependency maintenance cost; consistent with existing minimal Tailwind aesthetic
  - [x] PUT /admin/budget is called via a new apiPut helper added to lib/api-client.ts (additive, no existing method signature changed)
  - [x] The /usage page lives at app/(dashboard)/usage/page.tsx following the existing route group pattern
  - [x] Components live under components/usage/ (UsagePage, UsageStatsCards, UsageTable, BudgetWidget, BudgetEditForm) and components/models/ (ModelCatalogTable) — consistent with components/keys/ naming
  - [x] All data fetching is client-side TanStack Query (same pattern as KeysPage); no Next.js Server Actions or server-side fetching
  - [x] spent_usd_month and budget_usd_monthly are string-encoded decimals from the gateway; displayed as-is with a currency label, no float conversion needed
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: usage page renders aggregate cards and records table for owner
  Given the user is authenticated as owner (valid JWT in localStorage)
  And GET /admin/usage returns { total_cost_usd: "1.23", total_requests: 3, total_prompt_tokens: 300, total_completion_tokens: 150, records: [{ id: "...", model_id: "openai/gpt-4o", prompt_tokens: 100, completion_tokens: 50, cost_usd: "0.41", status: 200, created_at: "2026-06-10T00:00:00Z" }, ...] }
  When /usage mounts
  Then the page renders cards showing "3" total requests, "300" prompt tokens, "150" completion tokens, "1.23" total cost
  And the records table shows a row with "openai/gpt-4o"
  And the "Edit Budget" button is visible

Scenario: usage page empty state — no records
  Given the user is authenticated
  And GET /admin/usage returns { total_cost_usd: "0", total_requests: 0, total_prompt_tokens: 0, total_completion_tokens: 0, records: [] }
  When /usage mounts
  Then the page renders "0" for all aggregate cards
  And an empty-state message is shown for the records table (e.g. "No usage records yet")

Scenario: usage section error state
  Given the user is authenticated
  And GET /admin/usage responds 500 { title: "Internal server error", status: 500 }
  When /usage mounts
  Then the usage section renders the error title "Internal server error"
  And no records table rows are shown

Scenario: usage page loading state
  Given the user is authenticated
  And GET /admin/usage is pending (never resolves during the test window)
  When /usage mounts
  Then a loading indicator is visible
  And no records rows and no aggregate card values are shown

Scenario: model catalog section renders table rows
  Given the user is authenticated
  And GET /v1/models returns { object: "list", data: [{ id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000, prompt_per_token: 0.0000025, completion_per_token: 0.00001, object: "model" }] }
  When /usage mounts
  Then the model catalog table shows a row with "openai/gpt-4o" and "GPT-4o"
  And the prompt/completion token prices are visible

Scenario: model catalog error state
  Given the user is authenticated
  And GET /v1/models responds 409 { title: "Catalog not synced", status: 409, code: "ERR_CATALOG_EMPTY" }
  When /usage mounts
  Then the model catalog section renders the error title "Catalog not synced"
  And no catalog rows are shown

Scenario: budget widget shows ceiling and spend
  Given the user is authenticated
  And GET /admin/budget returns { budget_usd_monthly: "25.00", spent_usd_month: "10.50" }
  When /usage mounts
  Then the budget widget shows "25.00" as the monthly ceiling
  And "10.50" as the current month spend

Scenario: budget widget shows unlimited when ceiling is null
  Given the user is authenticated
  And GET /admin/budget returns { budget_usd_monthly: null, spent_usd_month: "0.00" }
  When /usage mounts
  Then the budget widget shows "Unlimited" (or equivalent) for the ceiling
  And "0.00" for the spend

Scenario: owner edits budget — happy path
  Given the user is authenticated as owner
  And GET /admin/budget returns { budget_usd_monthly: "25.00", spent_usd_month: "10.00" }
  And PUT /admin/budget with { budget_usd_monthly: "50.00" } returns 200 { budget_usd_monthly: "50.00" }
  When the owner clicks "Edit Budget", changes the value to "50.00", and submits
  Then PUT /admin/budget is called once with body { budget_usd_monthly: "50.00" }
  And the budget widget refreshes to show "50.00" as the ceiling

Scenario: owner clears budget to unlimited
  Given the user is authenticated as owner
  And GET /admin/budget returns { budget_usd_monthly: "25.00", spent_usd_month: "5.00" }
  And PUT /admin/budget with { budget_usd_monthly: null } returns 200 { budget_usd_monthly: null }
  When the owner opens the edit form, clears the value, and submits
  Then PUT /admin/budget is called once with body { budget_usd_monthly: null }
  And the budget widget shows "Unlimited"

Scenario: budget edit rejected — negative value, no API call
  Given the user is authenticated as owner and the budget edit form is open
  When the owner submits "-5.00" as the budget value
  Then an inline field error appears
  And no HTTP request is made to PUT /admin/budget
  And the budget widget value is unchanged

Scenario: budget edit rejected — non-numeric string, no API call
  Given the user is authenticated as owner and the budget edit form is open
  When the owner submits "abc" as the budget value
  Then an inline field error appears
  And no HTTP request is made to PUT /admin/budget

Scenario: budget edit 403 — member role surfaces error
  Given the user is authenticated (JWT decodes to role = "member")
  When PUT /admin/budget responds 403 { title: "Forbidden", code: "ERR_AUTH_FORBIDDEN" }
  Then the error title "Forbidden" is shown inline
  And the budget widget is unchanged
  And the browser does NOT navigate away

Scenario: budget edit 422 — surfaces error inline
  Given the user is authenticated as owner and the budget edit form is open
  And PUT /admin/budget responds 422 { title: "Invalid budget value", code: "ERR_PAYLOAD_INVALID" }
  When the owner submits a value that passes client-side validation but the gateway rejects it
  Then the error title "Invalid budget value" is shown inline
  And the budget widget is unchanged

Scenario: unauthenticated access to /usage redirects to /login
  Given localStorage "ai_proxy_token" is absent
  When the user navigates to /usage
  Then the browser is redirected to /login
  And the /usage content is never rendered

Scenario: member role does not see Edit Budget button
  Given the user is authenticated with a JWT whose role claim is "member"
  And GET /admin/budget returns { budget_usd_monthly: "25.00", spent_usd_month: "5.00" }
  When /usage mounts
  Then the budget widget renders the ceiling and spend values
  And no "Edit Budget" button is visible in the document
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
UI consumes (does NOT own) these gateway contracts — all FROZEN upstream:

─── USAGE ────────────────────────────────────────────────────────────────────

GET NEXT_PUBLIC_GATEWAY_URL/admin/usage   header: Authorization: Bearer <jwt>
  200 -> {
    total_cost_usd:          str,   # str(Decimal) — exact
    total_requests:          int,
    total_prompt_tokens:     int,
    total_completion_tokens: int,
    records: [               # ≤50 newest, ordered created_at DESC
      {
        id:                uuid,
        model_id:          str,
        prompt_tokens:     int,
        completion_tokens: int,
        cost_usd:          str,    # str(Decimal)
        status:            int,
        created_at:        str     # ISO 8601 timestamptz
      }
    ]
  }
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }

─── MODEL CATALOG ────────────────────────────────────────────────────────────

GET NEXT_PUBLIC_GATEWAY_URL/v1/models   header: Authorization: Bearer <jwt>
  200 -> {
    object: "list",
    data: [
      {
        id:                   str,           # OpenRouter model id
        name:                 str,
        context_length:       int | null,
        prompt_per_token:     float,         # upstream × (1 + markup_pct/100)
        completion_per_token: float,
        object:               "model"
      }
    ]
  }
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }
  409 -> problem+json { type: "about:blank", title: str, status: 409, code: "ERR_CATALOG_EMPTY" }

─── BUDGET ───────────────────────────────────────────────────────────────────

GET NEXT_PUBLIC_GATEWAY_URL/admin/budget   header: Authorization: Bearer <jwt>
  200 -> { budget_usd_monthly: str | null, spent_usd_month: str }
         (budget_usd_monthly = null means unlimited; spent_usd_month is ledger SUM,
          "0.00" when no records; both str(Decimal))
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }

PUT NEXT_PUBLIC_GATEWAY_URL/admin/budget   header: Authorization: Bearer <jwt>
  body: { budget_usd_monthly: str | null }
  200 -> { budget_usd_monthly: str | null }   (echo of persisted value)
  401 -> problem+json { type: "about:blank", title: str, status: 401, code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> problem+json { type: "about:blank", title: str, status: 403, code: "ERR_AUTH_FORBIDDEN" }
  422 -> problem+json { type: "about:blank", title: str, status: 422, code: "ERR_PAYLOAD_INVALID" }

─── CLIENT-SIDE ──────────────────────────────────────────────────────────────

Token storage: localStorage key "ai_proxy_token" — same as dashboard-shell
Expiry guard: decode base64url(payload) → check exp < Date.now()/1000 → redirect /login
Role guard: decode base64url(payload) → check role claim === "owner" or "admin" to show Edit Budget
  (fallback: if role claim absent, hide Edit Budget; rely on 403 response path)

Client-side validation (Zod, budget edit form only):
  BudgetEditSchema: z.string().regex(/^\d+(\.\d+)?$/, "Must be a non-negative decimal").optional()
    or z.literal("") (empty = null/clear)
  — negative values and non-decimal strings rejected client-side; no API call made

New lib addition:
  lib/api-client.ts — additive apiPut<T>(path: string, body: unknown): Promise<T>
    follows same pattern as apiPost: POST method replaced with PUT; Authorization header; 401→redirect

Component tree additions:
  app/(dashboard)/usage/page.tsx          — route page, thin wrapper
  components/usage/UsagePage.tsx          — orchestrates all three sections; route guard
  components/usage/UsageStatsCards.tsx    — four aggregate stat cards (loading/empty/error/success)
  components/usage/UsageTable.tsx         — records table ≤50 rows (loading/empty/error/success)
  components/usage/BudgetWidget.tsx       — reads GET /admin/budget; shows ceiling + spend; conditionally renders BudgetEditForm
  components/usage/BudgetEditForm.tsx     — inline edit form; PUT /admin/budget; Zod validation; all four states
  components/models/ModelCatalogTable.tsx — model catalog table from GET /v1/models (loading/empty/error/success)

msw fixture shapes (for tests):
  USAGE_RESPONSE = {
    total_cost_usd: "1.23", total_requests: 3,
    total_prompt_tokens: 300, total_completion_tokens: 150,
    records: [{ id: "rec-1", model_id: "openai/gpt-4o", prompt_tokens: 100,
                completion_tokens: 50, cost_usd: "0.41", status: 200,
                created_at: "2026-06-10T00:00:00Z" }]
  }
  MODELS_RESPONSE = {
    object: "list",
    data: [{ id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000,
             prompt_per_token: 0.000003, completion_per_token: 0.000012, object: "model" }]
  }
  BUDGET_RESPONSE = { budget_usd_monthly: "25.00", spent_usd_month: "10.50" }
  BUDGET_NULL_RESPONSE = { budget_usd_monthly: null, spent_usd_month: "0.00" }
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-10).
Least-sure flag surfaced at freeze:
⚠ [spec] Role claim shape in JWT payload — lowest confidence because the tenant-identity task does not explicitly document which JWT claim key holds the role (e.g. "role", "roles", "scope"); if wrong (claim key differs): the client-side Edit Budget visibility check silently hides the button for all users; the 403 path still enforces authorization correctly, but owners lose the edit affordance until the claim key is corrected — contained fix in BudgetWidget.tsx only, no contract change.
⚠ [contract] GET /v1/models uses the same tenant JWT as /admin/* — lowest confidence because the model-catalog TASK.md §3 specifies Bearer auth on /v1/models, but if the gateway enforces a different audience or scope claim, member-role users may receive 401 on the catalog section; if wrong: render the catalog error state gracefully (problem+json title shown) — no contract change, contained UX regression.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% lines (measured over implemented component files; same floor as dashboard-shell)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_usage_renders_cards_and_table: arrange localStorage JWT (owner) + msw GET /admin/usage→USAGE_RESPONSE + GET /v1/models→MODELS_RESPONSE + GET /admin/budget→BUDGET_RESPONSE / act render UsagePage / assert cards show "3" total requests + "300" prompt tokens + "150" completion tokens + "1.23" cost + table row with "openai/gpt-4o" + "Edit Budget" button visible
  - test_usage_empty_state: arrange localStorage JWT + msw GET /admin/usage→{total_cost_usd:"0",total_requests:0,total_prompt_tokens:0,total_completion_tokens:0,records:[]} / act render UsagePage / assert all card values show "0" + empty-state message for records table
  - test_usage_error_state: arrange localStorage JWT + msw GET /admin/usage→500 {title:"Internal server error"} / act render UsagePage / assert "Internal server error" visible + no table rows
  - test_usage_loading_state: arrange localStorage JWT + msw GET /admin/usage deferred (never resolves) / act render UsagePage / assert loading indicator visible + no record rows
  - test_catalog_renders_rows: arrange localStorage JWT + msw GET /v1/models→MODELS_RESPONSE / act render UsagePage (or ModelCatalogTable directly) / assert row with "openai/gpt-4o" and "GPT-4o" visible
  - test_catalog_error_state: arrange localStorage JWT + msw GET /v1/models→409 {title:"Catalog not synced",code:"ERR_CATALOG_EMPTY"} / act render UsagePage / assert "Catalog not synced" visible + no catalog rows
  - test_budget_widget_shows_ceiling_and_spend: arrange localStorage JWT + msw GET /admin/budget→{budget_usd_monthly:"25.00",spent_usd_month:"10.50"} / act render UsagePage / assert "25.00" + "10.50" both visible in budget widget
  - test_budget_widget_null_shows_unlimited: arrange localStorage JWT + msw GET /admin/budget→{budget_usd_monthly:null,spent_usd_month:"0.00"} / act render UsagePage / assert text matching /unlimited/i visible + "0.00" spend shown
  - test_budget_edit_happy_path: arrange localStorage JWT (owner) + msw GET /admin/budget→{budget_usd_monthly:"25.00",spent_usd_month:"10.00"} + PUT /admin/budget→200 {budget_usd_monthly:"50.00"} + GET /admin/budget (after PUT)→{budget_usd_monthly:"50.00",spent_usd_month:"10.00"} / act render UsagePage + click "Edit Budget" + type "50.00" + submit / assert PUT called once with body {budget_usd_monthly:"50.00"} + widget refreshes to show "50.00"
  - test_budget_edit_clear_to_unlimited: arrange localStorage JWT (owner) + msw GET /admin/budget→{budget_usd_monthly:"25.00",spent_usd_month:"5.00"} + PUT /admin/budget→200 {budget_usd_monthly:null} / act render UsagePage + click "Edit Budget" + clear value + submit / assert PUT called once with body {budget_usd_monthly:null} + widget shows /unlimited/i
  - test_budget_edit_negative_no_api_call: arrange localStorage JWT (owner) + budget edit form open / act submit "-5.00" / assert inline field error visible + zero PUT calls to /admin/budget
  - test_budget_edit_non_numeric_no_api_call: arrange localStorage JWT (owner) + budget edit form open / act submit "abc" / assert inline field error visible + zero PUT calls to /admin/budget
  - test_budget_edit_403_surfaces_error: arrange localStorage JWT + PUT /admin/budget→403 {title:"Forbidden",code:"ERR_AUTH_FORBIDDEN"} / act open edit form + submit valid value / assert "Forbidden" inline error visible + no navigation
  - test_budget_edit_422_surfaces_error: arrange localStorage JWT + PUT /admin/budget→422 {title:"Invalid budget value",code:"ERR_PAYLOAD_INVALID"} / act open edit form + submit value that passes client validation / assert "Invalid budget value" inline error visible + budget widget unchanged
  - test_usage_unauthenticated_redirects_login: arrange localStorage "ai_proxy_token" absent / act render UsagePage / assert redirect to /login + /usage content not rendered
  - test_member_no_edit_budget_button: arrange localStorage JWT with role="member" in payload + msw GET /admin/budget→BUDGET_RESPONSE / act render UsagePage / assert budget ceiling and spend visible + "Edit Budget" button NOT in document
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
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

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
