# TASK: Dashboard key-governance + spend surfaces

slug: dashboard-govern · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Dashboard governance editor + spend window viewer (BFF-proxied)
Framings weighed:
  - NEW dedicated BFF route handlers per governance surface (chosen) — keeps route logic
    discoverable; each BFF handler owns one gateway endpoint; the catch-all /api/gw/[...path]
    proxy already covers all gateway calls transparently, so no dedicated handlers are
    actually needed; the catch-all continues to proxy PATCH/rotate/spend through the session
    cookie. Alternative: catch-all /api/gw/[...path] already proxies everything — no new BFF
    handlers at all (chosen) — all governance mutations (PATCH /admin/keys/{id},
    POST /admin/keys/{id}/rotate) and GET /admin/spend already pass through the existing
    catch-all BFF proxy with cookie → Bearer transformation. This avoids route explosion.
    Alternative: dedicated typed BFF handlers for each governance endpoint — rejected: adds
    N route files with no security benefit (the catch-all already enforces session-cookie gate).
    DECISION: catch-all proxy handles ALL mutations and reads. New work is ONLY:
    (a) new React components/hooks consuming the already-proxied gateway contracts,
    (b) new MSW handler fixtures for PATCH/PATCH/rotate/spend in the BFF test layer,
    (c) the KeyGovernanceEditor component + SpendPage component.

<must>
  ### Key governance editor surface (owner/admin role only)
  - M1  The keys surface gains a "Governance" section per key row (or expandable form) where
        an owner/admin can view AND set/clear per-key monthly_budget_usd and soft_budget_usd.
        The form calls PATCH /api/gw/admin/keys/{key_id} (proxied by catch-all through the
        existing BFF with session cookie → Bearer). Request body matches the FROZEN contract:
        { "monthly_budget_usd": string | null, "soft_budget_usd": string | null }.
        The gateway responds 200 with the full KeyInfoResponse including governance fields.
  - M2  The governance editor allows setting/clearing expires_at (ISO-8601 date-time string
        or null). Sent in the PATCH body as "expires_at": string | null.
  - M3  The governance editor allows editing model_allowlist: an array of model ID strings
        (or null). The UI provides add/remove chip controls. Sent as
        "model_allowlist": [string] | null in the PATCH body.
  - M4  The keys surface exposes a "Rotate" action per key (owner/admin only). Clicking
        Rotate calls POST /api/gw/admin/keys/{key_id}/rotate (proxied through BFF catch-all).
        On 201 the response body carries a new plaintext "key" field — show it in a
        one-time banner (same PlaintextKeyBanner component already used for create).
        The old key_id is superseded; the keys list is refreshed after rotation.
  - M5  BFF security invariant: ALL governance mutations (PATCH, rotate) require the
        ai_proxy_session cookie. The catch-all /api/gw/[...path] BFF handler already
        rejects absent-cookie requests with 401 ERR_AUTH_NO_SESSION. The new components
        rely on this server-side guard — no client-side token logic.
  - M6  Gateway 403 (ERR_AUTH_FORBIDDEN — member role) is surfaced as an inline error
        in the governance editor form. No navigation, no crash.
  - M7  Gateway 422 (ERR_PAYLOAD_INVALID — e.g. soft > hard budget) is surfaced as an
        inline error. The form does NOT clear or reset values on 422.
  - M8  No JWT or token ever appears in client-fetchable page JS or localStorage. All
        calls use bffPost/bffPut with credentials:"include" through the BFF proxy.
        The Authorization header is only injected server-side in the catch-all route handler.

  ### Spend window viewer surface
  - M9  A new spend page/section calls GET /api/gw/admin/spend (proxied through the BFF
        catch-all) with a window parameter ("day" | "week" | "month") selected by the user.
        The component defaults to window=month on mount.
  - M10 The spend view renders the FROZEN response shape from spend-windows §3 contract:
        totals object (bucket_start, bucket_end, requests, prompt_tokens,
        completion_tokens, cost_usd) and a buckets list.
  - M11 The spend view exposes a window selector (day / week / month). Changing the
        selector re-fetches GET /admin/spend?window={choice} via bffGet through BFF proxy.
  - M12 The spend view includes an optional granularity selector (same "day" | "week" |
        "month" values passed as the `window` param per the frozen contract; the contract
        does NOT have a separate `granularity` param — the `window` param controls bucket
        granularity). Keep UI simple: one selector drives the `window` query param.
  - M13 The spend view shows a zero-state when totals.requests == 0 (no usage in window).
        Never shows 404 — the gateway always returns 200 for empty windows.
  - M14 Gateway 401 from the spend fetch clears the session (handled by bffGet's existing
        401 intercept → window.location.href = "/login"). No new client logic needed.
  - M15 Member role receives gateway 403 on GET /admin/spend; the spend section surfaces
        the error inline (forbidden message) rather than crashing.
  - M16 Deferred surface (model-mgmt dashboard) is explicitly OUT OF SCOPE for this task.
        DECLARE in the component list: no ModelManagementPage, no model enable/disable UI.
</must>

<reject>
  - R1  PATCH /admin/keys/{id} with soft_budget_usd > monthly_budget_usd (both non-null) →
        gateway returns 422 ERR_PAYLOAD_INVALID — surface inline error; no API retry.
        Client-side pre-validation: if user enters soft > hard, show inline error WITHOUT
        calling the gateway.
  - R2  PATCH /admin/keys/{id} with negative monthly_budget_usd or soft_budget_usd →
        client-side validation error before calling gateway.
  - R3  PATCH /admin/keys/{id} with model_allowlist containing empty-string elements →
        client-side validation error; gateway never called.
  - R4  Rotate by a member role → gateway returns 403 ERR_AUTH_FORBIDDEN →
        surface inline error; key remains unchanged.
  - R5  PATCH/rotate request with NO session cookie → BFF catch-all returns
        401 ERR_AUTH_NO_SESSION → bffPost/bffPut catches 401 and fires redirect to /login.
        (Tested via MSW interception of the BFF layer — Authorization header never appears
        client-side.)
  - R6  GET /admin/spend?window=fortnight → gateway returns 422 → component shows
        "invalid window" error inline; no crash.
  - R7  GET /admin/spend with member-role JWT → gateway returns 403 ERR_AUTH_FORBIDDEN →
        spend section shows forbidden error inline.
</reject>

<after>
  - After M1–M3: a PATCH to /api/gw/admin/keys/{id} with governance fields propagates the
    EXACT frozen field names (monthly_budget_usd, soft_budget_usd, expires_at,
    model_allowlist) to the gateway; the updated key row shows new values in the list.
  - After M4: POST rotate returns 201 with new_key_id, superseded_key_id, and plaintext
    "key"; the banner shows the plaintext once then dismisses; the keys list refreshes.
  - After M5: MSW interception of the BFF PATCH/rotate confirms Authorization header is set
    server-side only (captured in the MSW gateway handler, NOT in the client request).
  - After M9: GET /admin/spend?window=month renders totals.cost_usd and a buckets list.
  - After M11: selecting "day" re-calls GET /admin/spend?window=day.
  - After M13: zero requests window renders a zero-state, not an error.
  - After R4: 403 on rotate shows error inline; old key still active in the list.
  - After R5: absent-cookie 401 redirects to /login; the Authorization header never
    appeared in any client-side fetch (MSW confirms no Authorization in component requests).
</after>

<assumptions>
  ⚠ A1 [LOWEST CONFIDENCE — cost: component restructuring] The governance editor is
     added as an extension to the existing KeysPage / KeyRow rather than a new page.
     KeysPage currently reads ApiKey { key_id, name, prefix, created_at, revoked_at }
     via GET /admin/keys. The FROZEN key-governance contract adds governance fields to
     GET /admin/keys response items (monthly_budget_usd, soft_budget_usd, expires_at,
     model_allowlist). The ApiKey interface in KeysPage must be extended to include these
     fields. KeysPage already fetches via apiGet (the OLD client) not bffGet — this MUST be
     migrated to bffGet as part of this task (the auth-bff task migrated the form components
     but KeysPage still uses apiGet as of the current codebase). Cost if wrong: KeysPage
     uses apiGet which reads NEXT_PUBLIC_GATEWAY_URL directly and sends Authorization from
     localStorage — this is the pre-BFF pattern. If the governance tests use bffGet
     handlers but KeysPage still calls apiGet, the test MSW layer won't intercept correctly.
     RESOLUTION: the Build phase must migrate KeysPage to bffGet/bffPost/bffDelete and
     extend the ApiKey interface. The existing keys.test.tsx (tests/ legacy suite) already
     uses the old pattern and will require a compat shim or must be updated to use BFF
     handlers — checked: tests/keys.test.tsx uses `http://localhost:3000/api/gw/admin/keys`
     handlers (BFF proxy URL pattern), so it IS already expecting BFF-layer interception.
     This means KeysPage has already been migrated to bffGet or tests/keys.test.tsx was
     written prospectively. CONFIRMED by checking tests/keys.test.tsx MSW handlers (all
     use http://localhost:3000/api/gw/* not http://gateway.test/*) — tests/keys.test.tsx
     expects bffGet already. The component itself still uses apiGet. The Build migrates the
     component and the tests pass. This is consistent.

  ⚠ A2 [HIGH CONCERN — cost: field mismatch in PATCH body] The governance editor must send
     EXACTLY the frozen field names from key-governance §3 contract:
       monthly_budget_usd, soft_budget_usd, expires_at, model_allowlist
     A typo (e.g. "budget_usd_monthly" which is the TENANT budget field name in the
     existing BudgetWidget) would silently send wrong fields. The tenant budget field is
     "budget_usd_monthly" (PUT /admin/budget contract). The per-key governance PATCH field
     is "monthly_budget_usd". These are DIFFERENT field names for DIFFERENT concepts.
     Cost if wrong: PATCH sends budget_usd_monthly to the gateway, which ignores it
     (unknown field), governance is never saved, tests pass against MSW but production fails.
     RESOLUTION: every MSW fixture for PATCH /admin/keys/{id} asserts the request body
     contains "monthly_budget_usd" (not "budget_usd_monthly"). The test suite must
     capture and assert the request body field names verbatim.

  - A3 [spend window selector state] The spend window selector is controlled state in the
     component; changing it re-queries with the new window param. TanStack Query's queryKey
     must include the window value so changing the window triggers a fresh fetch.
     If wrong: window changes don't re-fetch — the query key doesn't change. Cost: low;
     easy to debug.

  - A4 [No new npm dependencies] zod is already installed for validation. No new packages
     are needed — form state with React useState, validation with zod, queries with
     @tanstack/react-query, fetch with bff-client.ts. Lucide-react for icons is already
     installed. Cost if wrong: make ci fails the node-deps allowlist gate.

  - A5 [bffPatch not in bff-client.ts] bff-client.ts currently exports bffGet, bffPost,
     bffPut, bffDelete, bffAuthPost. PATCH is not exported. The Build phase must add
     bffPatch to bff-client.ts (same pattern as bffPut but method: "PATCH"). This is a
     minimal additive change to an existing file — no new files needed.
     If wrong: the component calls bffPost for PATCH which sends the wrong HTTP method to
     the gateway. Cost: medium — governance updates silently 405.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first,
     top two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── M1–M3: Key governance editor — PATCH governance fields ───────────────────

Scenario: Owner sets monthly_budget and soft_budget on a key via governance editor
  Given the keys list shows a key "prod-key" with monthly_budget_usd=null
  And   the user is owner role (from /api/auth/me)
  When  the owner opens the governance editor for "prod-key"
  And   sets monthly_budget_usd to "50.00" and soft_budget_usd to "40.00"
  And   submits the form
  Then  PATCH /api/gw/admin/keys/{key_id} is called with body:
        { "monthly_budget_usd": "50.00", "soft_budget_usd": "40.00" }
        (field names are the EXACT frozen contract names — NOT "budget_usd_monthly")
  And   the request body NEVER contains an "Authorization" header (Authorization
        is injected server-side by the BFF catch-all proxy only)
  And   the updated key shows monthly_budget_usd="50.00" in the list

Scenario: Owner sets expires_at on a key
  Given the keys list shows a key "expiry-key" with expires_at=null
  When  the owner sets expires_at to "2099-01-01T00:00:00Z" in the governance editor
  And   submits the form
  Then  PATCH /api/gw/admin/keys/{key_id} is called with body containing
        "expires_at": "2099-01-01T00:00:00Z"
  And   the updated key shows the new expires_at in the list

Scenario: Owner sets model_allowlist on a key
  Given the keys list shows a key "allowlist-key" with model_allowlist=null
  When  the owner adds "openai/gpt-4o" and "openai/gpt-3.5-turbo" to the allowlist
  And   submits the governance editor form
  Then  PATCH /api/gw/admin/keys/{key_id} is called with body containing
        "model_allowlist": ["openai/gpt-4o", "openai/gpt-3.5-turbo"]
  And   the key row shows the model_allowlist after refresh

Scenario: Owner clears monthly_budget_usd to null via governance editor
  Given the keys list shows a key with monthly_budget_usd="25.00"
  When  the owner clears the monthly_budget field (empty = null) and submits
  Then  PATCH /api/gw/admin/keys/{key_id} is called with body containing
        "monthly_budget_usd": null
  And   the updated key shows unlimited budget

# ── M4: Key rotation ─────────────────────────────────────────────────────────

Scenario: Owner rotates a key — new secret shown once, keys list refreshed
  Given the keys list shows a key "old-key" (old_key_id)
  When  the owner clicks "Rotate" on "old-key"
  And   confirms the rotation
  Then  POST /api/gw/admin/keys/{old_key_id}/rotate is called
  And   the response 201 body contains new_key_id, superseded_key_id, and plaintext "key"
  And   the plaintext new secret is shown in the one-time banner
  And   dismissing the banner removes the secret from the DOM
  And   the keys list is refreshed (GET /api/gw/admin/keys re-fetched)

# ── M5/R5: BFF security — no token in client JS ──────────────────────────────

Scenario: Governance editor PATCH request carries NO Authorization header client-side
  Given the user is authenticated via ai_proxy_session cookie
  When  the governance editor sends PATCH /api/gw/admin/keys/{key_id}
  Then  the client-side fetch request to /api/gw/admin/keys/{key_id} has NO
        Authorization header (MSW intercepts the BFF-layer request and confirms)
  And   the MSW gateway handler (http://gateway.test/*) receives Authorization:
        Bearer {token} injected server-side by the catch-all BFF proxy

# ── M6: Gateway 403 on governance PATCH ─────────────────────────────────────

Scenario: Member role PATCH governance fields — gateway 403 shown inline
  Given the user is member role
  When  a PATCH /api/gw/admin/keys/{key_id} returns 403 ERR_AUTH_FORBIDDEN
        (this simulates member-role attempting a write the gateway rejects)
  Then  the governance editor shows an inline "Forbidden" error
  And   no navigation occurs (no router.push)
  And   the form remains open with the existing values

# ── M7: Gateway 422 on governance PATCH ─────────────────────────────────────

Scenario: soft_budget > monthly_budget — client-side validation blocks the API call
  Given the governance editor is open for a key
  When  the owner enters monthly_budget_usd="10.00" and soft_budget_usd="15.00"
  And   submits the form
  Then  an inline error "soft budget cannot exceed hard budget" is shown
  And   PATCH is NOT called (no API request sent)

Scenario: Gateway 422 ERR_PAYLOAD_INVALID is surfaced inline
  Given the governance editor submits valid client-side values
  When  the gateway returns 422 ERR_PAYLOAD_INVALID
  Then  the error is displayed inline in the governance editor
  And   the form retains its current values

# ── R4: Rotate by member role ────────────────────────────────────────────────

Scenario: Rotation returns 403 — inline error, key still active
  Given the user is owner (can see Rotate button) and gateway returns 403 on rotate
  When  POST /api/gw/admin/keys/{key_id}/rotate returns 403 ERR_AUTH_FORBIDDEN
  Then  an inline error is shown with the error title
  And   the keys list is NOT refreshed (no stale re-fetch)
  And   no banner appears (no new secret to show)

# ── M9–M12: Spend window view ────────────────────────────────────────────────

Scenario: Spend view renders totals and buckets for the default window (month)
  Given the user navigates to the spend view
  When  the component mounts (default window=month)
  Then  GET /api/gw/admin/spend?window=month is called
  And   totals.cost_usd, totals.requests, totals.prompt_tokens, totals.completion_tokens
        are rendered on screen
  And   each bucket in the buckets list is rendered (bucket_start + cost_usd)

Scenario: Spend view window selector changes the fetch query param
  Given the spend view is showing window=month data
  When  the user selects "day" from the window selector
  Then  GET /api/gw/admin/spend?window=day is called
  And   the spend view updates to show day-window data

Scenario: Spend view window=week renders correctly
  Given the spend view is visible
  When  the user selects "week" from the window selector
  Then  GET /api/gw/admin/spend?window=week is called
  And   the spend view renders week-window totals

# ── M13: Zero-state ──────────────────────────────────────────────────────────

Scenario: Spend view zero-state — no usage in selected window
  Given GET /api/gw/admin/spend?window=month returns
        { "totals": { "requests": 0, "cost_usd": "0", ... }, "buckets": [] }
  When  the spend view renders
  Then  a zero-state message is shown (e.g. "No usage in this period")
  And   the totals show 0 requests and cost "0"
  And   no error state is shown (200 with zeros is not an error)

# ── M15: Spend view 403 ──────────────────────────────────────────────────────

Scenario: Spend view 403 from gateway — shown inline, no crash
  Given GET /api/gw/admin/spend returns 403 ERR_AUTH_FORBIDDEN
  When  the spend view renders
  Then  an inline "Forbidden" error is shown
  And   no crash / no uncaught exception in the component tree

# ── R6: Invalid spend window param ───────────────────────────────────────────

Scenario: Spend view 422 from gateway — shown inline, no crash
  Given the spend view selector is somehow set to an invalid value
  When  GET /api/gw/admin/spend returns 422 ERR_PAYLOAD_INVALID
  Then  an inline "invalid window" or error message is shown
  And   the component does not crash

# ── M16: Model-mgmt surface deferred ─────────────────────────────────────────

Scenario: Model management surface is explicitly absent (deferred)
  Given the dashboard is fully rendered with all v3 components
  Then  no model management toggle or enable/disable UI is present
  And   the dashboard.govern test suite does not import or reference any
        ModelManagementPage or model enable/disable component
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
BFF PROXY — all governance and spend calls go through the EXISTING catch-all:
  /api/gw/[...path]  (apps/dashboard/app/api/gw/[...path]/route.ts)

No NEW BFF route handlers are created. The catch-all already:
  - reads ai_proxy_session cookie
  - injects Authorization: Bearer server-side
  - rejects absent cookie with 401 ERR_AUTH_NO_SESSION
  - passes 403/422/201/200/204 gateway responses verbatim to the client
  - on gateway 401: clears cookie, returns 401 ERR_AUTH_SESSION_EXPIRED

GOVERNANCE EDITOR — client calls through BFF catch-all:

PATCH /api/gw/admin/keys/{key_id}
  client sends: fetch("/api/gw/admin/keys/{key_id}", { method: "PATCH", credentials: "include" })
  body (subset of fields, all optional):
    {
      "monthly_budget_usd": string | null,
      "soft_budget_usd":    string | null,
      "expires_at":         string | null,    -- ISO-8601 UTC
      "model_allowlist":    [string] | null
    }
  Gateway response (proxied verbatim):
    200 -> KeyInfoResponse (all fields including governance fields — per key-governance §3 FROZEN)
    403 -> { "code": "ERR_AUTH_FORBIDDEN" }
    404 -> { "code": "ERR_KEY_NOT_FOUND" }
    422 -> { "code": "ERR_PAYLOAD_INVALID" }
  NOTE: field name "monthly_budget_usd" is DISTINCT from the TENANT budget field
        "budget_usd_monthly" (PUT /admin/budget). These are DIFFERENT concepts.

POST /api/gw/admin/keys/{key_id}/rotate
  client sends: fetch("/api/gw/admin/keys/{key_id}/rotate", { method: "POST", credentials: "include" })
  body: {} (empty — no override fields for this task scope)
  Gateway response (proxied verbatim):
    201 -> {
      "new_key_id":        uuid,
      "superseded_key_id": uuid,
      "key":               string,   -- plaintext new secret, shown ONCE
      "name":              string,
      "monthly_budget_usd": string | null,
      "soft_budget_usd":    string | null,
      "expires_at":         string | null,
      "model_allowlist":    [string] | null
    }
    403 -> { "code": "ERR_AUTH_FORBIDDEN" }
    404 -> { "code": "ERR_KEY_NOT_FOUND" }

GET /admin/keys  (existing, extended by key-governance — FROZEN)
  Response items now carry governance fields:
    "monthly_budget_usd": string | null,
    "soft_budget_usd":    string | null,
    "expires_at":         string | null,
    "model_allowlist":    [string] | null

SPEND VIEW — client calls through BFF catch-all:

GET /api/gw/admin/spend?window={day|week|month}
  client sends: fetch("/api/gw/admin/spend?window=month", { credentials: "include" })
  Gateway response (proxied verbatim — per spend-windows §3 FROZEN):
    200 -> {
      "window": "day" | "week" | "month",
      "bucket_size": "day" | "week" | "month",
      "totals": {
        "bucket_start":       string,    -- ISO-8601 UTC
        "bucket_end":         string,
        "requests":           int,
        "prompt_tokens":      int,
        "completion_tokens":  int,
        "cost_usd":           string     -- str(Decimal)
      },
      "buckets": [
        {
          "bucket_start": string,
          "requests":     int,
          "prompt_tokens": int,
          "completion_tokens": int,
          "cost_usd":     string
        }
      ],
      "breakdown": null    -- not requested by this component (no group_by param)
    }
    401 -> handled client-side by bffGet (redirect to /login)
    403 -> { "code": "ERR_AUTH_FORBIDDEN" }   -- surfaced inline
    422 -> { "code": "ERR_PAYLOAD_INVALID" }  -- surfaced inline

bff-client.ts EXTENSION (additive, no new file):
  + bffPatch<T>(path: string, body: unknown): Promise<T>
    -- same pattern as bffPut but method: "PATCH"
    -- no Authorization header; credentials: "include"

NEW components (files-to-touch):
  apps/dashboard/components/keys/KeyGovernanceEditor.tsx   -- NEW form component
  apps/dashboard/components/keys/KeysPage.tsx              -- EXTEND: migrate to bffGet/bffPost/bffDelete,
                                                              extend ApiKey interface, add governance fields,
                                                              wire rotation
  apps/dashboard/components/spend/SpendPage.tsx            -- NEW page component
  apps/dashboard/app/(dashboard)/spend/page.tsx            -- NEW route page wrapper
  apps/dashboard/lib/bff-client.ts                         -- ADD bffPatch export

FROZEN FILES (must not be modified by Build):
  apps/dashboard/app/api/gw/[...path]/route.ts             -- catch-all BFF proxy already complete
  apps/dashboard/app/api/auth/*/route.ts                   -- auth handlers frozen
  apps/dashboard/middleware.ts                             -- route guard frozen

MSW FIXTURES TO ADD (tests-bff/mocks/handlers.ts must be extended):
  PATCH http://gateway.test/admin/keys/:key_id    -> 200 KeyInfoResponse with governance fields
  POST  http://gateway.test/admin/keys/:key_id/rotate -> 201 rotation response
  GET   http://gateway.test/admin/spend           -> 200 SpendWindowResponse

  IMPORTANT: Component tests (tests-bff/) intercept at http://localhost:3000/api/gw/*
  (BFF layer) NOT at http://gateway.test/* (gateway layer). Route handler unit tests
  (tests-bff/route-handlers.test.ts) test the catch-all by intercepting http://gateway.test/*.
  Component MSW handlers must be at the BFF URL: http://localhost:3000/api/gw/admin/keys/{id}
  etc. — not at the gateway URL.

Test-infra notes:
  - New tests go in tests-bff/govern.test.tsx (component tests; BFF suite; setupFiles:
    tests-bff/setup.ts; uses tests-bff/mocks/server.ts)
  - Pattern: same as tests-bff/bff-forms.test.tsx — import component, render with
    QueryClientProvider wrapper, use MSW server.use() per-test overrides
  - No new test infra files needed; no new npm packages
  - Tests MUST NOT import from tests-bff/route-handlers.test.ts or tests/keys.test.tsx
    (those are frozen)
```

Status: FROZEN @ v3 — approved by Tin Dang (delegated auto mode, 2026-06-11)

Least-sure flag surfaced at freeze:

⚠ [spec] A1 — KeysPage currently uses `apiGet` (old client that reads localStorage token
  and gateway URL directly). The governance tests expect bffGet/bffPatch/bffPost. Build MUST
  migrate KeysPage to bff-client. The existing tests/keys.test.tsx already uses BFF-layer
  MSW URLs (http://localhost:3000/api/gw/*), confirming the expected migration path.
  Cost if wrong: tests for old KeysPage use gateway.test/* handlers and new tests use
  localhost:3000/api/gw/* handlers — one set will always get 500 from unmatched MSW.

⚠ [contract] A2 — Per-key governance PATCH body field names MUST be "monthly_budget_usd"
  NOT "budget_usd_monthly". The existing BudgetWidget/BudgetEditForm uses "budget_usd_monthly"
  for the TENANT budget. These are different concepts with confusingly similar names.
  Every MSW fixture for PATCH /admin/keys asserts request body contains "monthly_budget_usd".
  Cost if wrong: governance is silently never saved; all tests pass against fakes.

<!-- EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY
     + the bundle's lowest-confidence flag was surfaced at the freeze. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new component code paths (governance form CRUD, rotation banner,
  spend view states, validation, error surfaces)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_governance_editor_patch_sends_exact_frozen_field_names:
      arrange: keys list with one key monthly_budget_usd=null (owner role) /
      act: open governance editor, set monthly_budget_usd="50.00" soft_budget_usd="40.00", submit /
      assert: PATCH /api/gw/admin/keys/{id} called; captured body.monthly_budget_usd=="50.00" NOT
      "budget_usd_monthly"; body.soft_budget_usd=="40.00"; updated values visible in list

  - test_governance_editor_patch_sends_expires_at:
      arrange: key with expires_at=null /
      act: set expires_at="2099-01-01T00:00:00Z" and submit /
      assert: PATCH body contains "expires_at": "2099-01-01T00:00:00Z"; list refreshes

  - test_governance_editor_patch_sends_model_allowlist:
      arrange: key with model_allowlist=null /
      act: add "openai/gpt-4o" to allowlist and submit /
      assert: PATCH body contains "model_allowlist": ["openai/gpt-4o"]

  - test_governance_editor_clears_budget_to_null:
      arrange: key with monthly_budget_usd="25.00" /
      act: clear the monthly_budget field (empty string → null), submit /
      assert: PATCH body contains "monthly_budget_usd": null; list shows unlimited

  - test_governance_no_authorization_header_in_client_request:
      arrange: key with governance editor /
      act: submit governance PATCH /
      assert: MSW captures the BFF-layer request to /api/gw/admin/keys/{id};
      that request has NO Authorization header; the BFF handler (gateway.test/*) captures
      Authorization: Bearer {token} injected server-side

  - test_rotation_shows_plaintext_once_then_refreshes_list:
      arrange: key "old-key" in list /
      act: click Rotate, confirm /
      assert: POST /api/gw/admin/keys/{id}/rotate called; 201 body has new_key_id +
      superseded_key_id + "key" plaintext; banner shows the plaintext once;
      dismiss banner removes secret from DOM; keys list re-fetched

  - test_governance_403_surfaces_inline_error:
      arrange: PATCH /api/gw/admin/keys/{id} returns 403 ERR_AUTH_FORBIDDEN /
      act: submit governance form /
      assert: inline error shows "Forbidden" text; no router.push called; form remains open

  - test_governance_422_gateway_surfaces_inline_error:
      arrange: PATCH /api/gw/admin/keys/{id} returns 422 ERR_PAYLOAD_INVALID /
      act: submit governance form with valid client-side values /
      assert: inline error shown; form retains values; no navigation

  - test_governance_client_validation_soft_gt_hard_blocks_api:
      arrange: governance editor open /
      act: enter monthly_budget_usd="10.00" soft_budget_usd="15.00" and submit /
      assert: inline "soft budget cannot exceed hard budget" error; PATCH NOT called

  - test_governance_client_validation_negative_budget_blocks_api:
      arrange: governance editor open /
      act: enter monthly_budget_usd="-5.00" and submit /
      assert: inline validation error; PATCH NOT called

  - test_rotation_403_shows_error_no_banner:
      arrange: POST /api/gw/admin/keys/{id}/rotate returns 403 ERR_AUTH_FORBIDDEN /
      act: trigger rotation /
      assert: inline error with "Forbidden" text; no plaintext banner appears;
      keys list NOT re-fetched with new key

  - test_spend_view_default_month_renders_totals:
      arrange: GET /api/gw/admin/spend?window=month returns SpendWindowResponse with
      cost_usd="1.23", requests=3, prompt_tokens=300, completion_tokens=150, buckets /
      act: render SpendPage (mounts with window=month) /
      assert: "1.23" visible; 3 requests visible; buckets rendered

  - test_spend_view_window_selector_changes_query_param:
      arrange: SpendPage rendered (window=month) /
      act: select "day" from window selector /
      assert: GET /api/gw/admin/spend?window=day is called;
      day-window data is rendered; NOT the month-window data

  - test_spend_view_week_selector:
      arrange: SpendPage rendered /
      act: select "week" from window selector /
      assert: GET /api/gw/admin/spend?window=week is called; week data rendered

  - test_spend_view_zero_state:
      arrange: GET /api/gw/admin/spend?window=month returns { "totals": { "requests": 0,
      "cost_usd": "0", "prompt_tokens": 0, "completion_tokens": 0, ...}, "buckets": [] } /
      act: render SpendPage /
      assert: zero-state message visible; 0 requests shown; no error state

  - test_spend_view_403_shows_inline_error:
      arrange: GET /api/gw/admin/spend returns 403 ERR_AUTH_FORBIDDEN /
      act: render SpendPage /
      assert: inline "Forbidden" error shown; no crash

  - test_spend_view_422_shows_inline_error:
      arrange: GET /api/gw/admin/spend returns 422 ERR_PAYLOAD_INVALID /
      act: render SpendPage /
      assert: inline error shown; no crash

  - test_model_mgmt_surface_absent:
      arrange: render all dashboard pages /
      act: inspect rendered tree /
      assert: no model management toggle/enable-disable UI present
</test_plan>

Tests live in: `apps/dashboard/tests-bff/govern.test.tsx`

TRUE-RED reasoning per test (all fail because KeyGovernanceEditor and SpendPage components
  do not exist — the import line `import { KeyGovernanceEditor } from
  "@/components/keys/KeyGovernanceEditor"` resolves to a missing module at test collect time,
  causing a MODULE_NOT_FOUND error for the entire test file):

IMPORTANT: To avoid the import-error non-behavioral red problem, tests are structured to
  fail on BEHAVIOR not on import error. The test file uses dynamic imports inside each test
  OR wraps component imports in a try/catch at the top to allow individual test assertions
  to fail. Alternatively: follow the existing pattern from tests-bff/route-handlers.test.ts
  where top-level static imports of non-existent modules IS the declared red mechanism —
  "RED failure mode: imports from @/components/.../X fail with MODULE_NOT_FOUND."

DECISION: Follow the existing pattern (static import at top of file). This is the established
  convention in this codebase:
    - tests-bff/route-handlers.test.ts line 37-41: static imports of non-existent handlers
    - tests-bff/bff-client.test.tsx line 20: static import of non-existent bff-client
    - tests-bff/use-current-user.test.tsx line 22: static import of non-existent hook
    - tests/keys.test.tsx line 21: static import of non-existent KeysPage
    - tests/usage.test.tsx line 29: static import of non-existent UsagePage

  The convention is: MODULE_NOT_FOUND on import of a non-existent component is the
  TRUE-RED mechanism. When the component exists but has wrong behavior, the tests fail for
  BEHAVIORAL reasons. The "true-red rule" means the red must be for the TARGET behavior,
  not the CURRENT absent state. Here: a module-not-found IS the right reason — the target
  is a component that exists and behaves correctly; the fail is because it doesn't exist yet.
  This is consistent with the established codebase convention.

Right-reason red evidence (expected):
  FILE: tests-bff/govern.test.tsx
  - tests 1–18: FAIL — MODULE_NOT_FOUND on static import of
    `@/components/keys/KeyGovernanceEditor` (does not exist yet)
    OR `@/components/spend/SpendPage` (does not exist yet)
    When GREEN: components exist + PATCH body assertions pass for the right field names.

  RESIDUAL BEHAVIORAL RED (tests that require MORE than module existence to pass):
  - test_governance_editor_patch_sends_exact_frozen_field_names:
      GREEN requires: component sends "monthly_budget_usd" NOT "budget_usd_monthly"
  - test_governance_no_authorization_header_in_client_request:
      GREEN requires: component uses bffPatch (no Authorization) not apiPatch with token
  - test_rotation_shows_plaintext_once_then_refreshes_list:
      GREEN requires: 201 rotate response → banner shows plaintext → dismiss clears it
  - test_spend_view_window_selector_changes_query_param:
      GREEN requires: queryKey includes window value so selector change triggers re-fetch

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  - Field names: per-key governance PATCH body MUST use "monthly_budget_usd" (frozen key-governance
    §3 contract) — NEVER "budget_usd_monthly" (that is the tenant budget field in the existing
    BudgetEditForm/BudgetWidget — a completely different endpoint and concept).
  - BFF-only: ALL calls to /api/gw/* MUST use bffGet/bffPost/bffPatch/bffDelete
    (credentials: "include") — NEVER construct an Authorization header client-side, NEVER
    read localStorage for a token. The catch-all BFF proxy injects Bearer server-side.
  - Rotation banner: the plaintext key from rotate 201 response MUST be shown exactly once.
    Use the existing PlaintextKeyBanner component pattern (same as create key). After dismiss,
    the key string must be gone from state and DOM.
  - No new npm dependencies. zod, lucide-react, @tanstack/react-query, clsx are all available.
  - bff-client.ts bffPatch: ADDITIVE only — do not modify any existing exported function.

Code lives in: `apps/dashboard/`
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

Watch (reuse scenarios as monitors):
  - PATCH /admin/keys 4xx rate (403/422 from governance editor)
  - POST /admin/keys/{id}/rotate 4xx rate
  - GET /admin/spend 4xx rate (401/403/422)
  - Rotation banner dismiss rate vs rotation call rate (banner always shows once)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
