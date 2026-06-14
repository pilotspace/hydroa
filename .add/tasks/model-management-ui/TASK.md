# TASK: Model Management UI

slug: model-management-ui · created: 2026-06-14 · stage: production
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

Touches (files · symbols · signatures): v15 surface task 2 — a NEW owner/admin `/models` page that lets a tenant admin enable/disable individual catalog models, consuming EXISTING gateway endpoints (NO backend change). Verified the contract + the mirror patterns:
- BACKEND CONTRACT (already shipped, `apps/gateway/src/gateway/catalog/api/router.py`): `GET /admin/models` (`router.py:116-154`) → `AdminModelsListResponse { object: "list", data: AdminModelItem[] }` where `AdminModelItem = { id: str, name: str, context_length: int|None, enabled: bool }` (`schemas.py:37-53`); `PUT /admin/models/{model_id:path}` (`router.py:157-208`) body `PutModelRequest { enabled: bool }` (`schemas.py:56-59`) → `AdminModelItem`. RBAC: BOTH endpoints `Depends(require_owner_or_admin)` (`router.py:118` GET, `:161` PUT; `deps.py:64-73`) → member role gets `403 ERR_AUTH_FORBIDDEN` on read AND write — the WHOLE surface is owner/admin-only (members cannot even list). PUT 404s `ERR_MODEL_NOT_FOUND` for an unknown id. `enabled` defaults true when no tenant override row exists (`router.py:149-150`).
- model_id-with-slash (THE edge): real ids are OpenRouter-style `provider/model` (e.g. `openai/gpt-4o`). Backend uses the `:path` converter so the decoded id reaches the handler whole (`router.py:166-168`). The dashboard BFF catch-all reconstructs the path with `pathSegments.join("/")` (`app/api/gw/[...path]/route.ts:53`) → `encodeURIComponent(id)`'s `%2F` decodes to one segment then rejoins to the correct `admin/models/openai/gpt-4o` upstream; uvicorn re-decodes en route. → call `bffPut(`/admin/models/${encodeURIComponent(id)}`, { enabled })`.
- BFF SEAM (`apps/dashboard/lib/bff-client.ts`): `bffGet<T>(path)` (`:93`), `bffPut<T>(path, body)` (`:114`) — same-origin `/api/gw{path}`, `credentials:"include"`, no client Authorization; 401 → `BffError` + redirect to `/login`. `appBase()` Node fallback `http://localhost:3000` (`:33-40`). Catch-all `app/api/gw/[...path]/route.ts` exports GET/POST/PUT/DELETE (PUT present — good; PATCH is NOT, irrelevant here).
- RBAC client hook: `useCurrentUser()` (`lib/hooks/use-current-user.ts:45`) → `{ data: { role: "owner"|"admin"|"member"|null } }` from `GET /api/auth/me`. Precedent gate `const canEdit = role === "owner" || role === "admin"` (`UsagePage.tsx:24-25`).
- MIRROR PATTERN (`apps/dashboard/components/keys/KeysPage.tsx`): `useQuery({queryKey,queryFn:bffGet})` + `useMutation({mutationFn:bff*, onSuccess: invalidateQueries})` + the four-state render (`Loading` role=status `:220`, `ErrorState` role=alert `:228`, `Empty` `:230`, success table `:237`) + `getErrorTitle(err)` (`BffError.problem.title`, `:156-160`). State components `components/ui/states.tsx` (frozen v13). Toggle = the v15 `Switch` (`components/ui/switch.tsx`: `<button role="switch" aria-checked>`, `onCheckedChange(next)`, `disabled`).
- NAV: `components/ui/app-shell.tsx:18-22` static `NAV_ITEMS` (href/label/icon); `activePath` marks `aria-current="page"`. Adding `/models` = one row here (lucide icon). Route shell `app/(dashboard)/<seg>/page.tsx` re-exports the component (`keys/page.tsx` pattern).
- TEST HARNESS (`tests-bff/`, the "bff" vitest project): msw `tests-bff/mocks/{server,handlers}.ts`; default same-origin handlers under `http://localhost:3000/api/gw/:path*` (override per-test with `server.use(...)`); `beforeAll listen({onUnhandledRequest:"error"})` / `afterEach resetHandlers`. `/api/auth/me` defaults role "owner" (`handlers.ts:109-117`) — override to "member" for the RBAC test.

Context (working folder): the v15 MILESTONE.md "model-management-ui" task (depends-on design-system-extension, now committed); the v13/v15 design system (foundation v14: tokens + four state components + Switch). The existing READ-ONLY `/v1/models` catalog view on `/usage` (`components/models/ModelCatalogTable.tsx`) is a DIFFERENT endpoint (no `enabled`) — this is a new sibling file `components/models/ModelsPage.tsx`, no conflict.

Honors (patterns / conventions): foundation v14 §Users (substantive a11y via labelled keyboard-operable native controls — the Switch); CONVENTIONS.md (data-identical BFF seam, exact field names; four state patterns; axe-in-jsdom impact serious|critical + color-contrast disabled; `within(section)` RTL; coverage gate); milestone shared decisions (consume the design system, no ad-hoc toggle; owner/admin-only surface).

Anchors the contract cites: NEW `app/(dashboard)/models/page.tsx` + `components/models/ModelsPage.tsx` · the EXISTING `GET /admin/models` / `PUT /admin/models/{model_id}` shapes (`AdminModelItem` fields verbatim) · `bffGet`/`bffPut` · `useCurrentUser` role gate · the v13 four state components + the v15 `Switch` · the `app-shell.tsx` `NAV_ITEMS` `/models` entry · the `tests-bff/model-mgmt.test.tsx` suite.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Model Management surface (`/models`) — an owner/admin page that lists the tenant's catalog models and toggles each one enabled/disabled per tenant, calling the EXISTING `GET /admin/models` + `PUT /admin/models/{model_id}` through the BFF seam, built on the v13/v15 design system (four state components + the `Switch`), data-identical (no backend change).
Framings weighed: A dedicated `/models` page mirroring KeysPage (chosen — own surface + nav entry, useQuery/useMutation + four states + Switch toggles, the established surface shape) · Fold model-enable into the existing `/usage` read-only catalog table (rejected — `/usage` reads `/v1/models` which has NO `enabled` flag and is a member-visible runtime view; model management is owner/admin-only mutation on a DIFFERENT admin endpoint — conflating them mixes RBAC scopes and breaks the read-only catalog's contract) · Optimistic local toggle without refetch (rejected — invalidate-on-success refetch is the KeysPage convention and is honest about the persisted server state; optimistic UI adds rollback complexity for no measured latency need).

Must:
<must>
  - List models — `GET /admin/models` via `bffGet`; render each `AdminModelItem` (id, name, context_length, enabled) in a table/Card with the model name + id + context length; the four state patterns (Loading role=status / Empty / ErrorState role=alert / success list) exactly as KeysPage.
  - Toggle enable/disable — each row has a labelled `Switch` (accessible name tied to the model) reflecting `enabled`; flipping it calls `PUT /admin/models/{encodeURIComponent(id)}` body `{ enabled: next }` via `bffPut`, then `queryClient.invalidateQueries(["admin-models"])` on success; the Switch is `disabled` while its mutation is in flight.
  - Slash-id safety — a model id containing `/` (e.g. `openai/gpt-4o`) is `encodeURIComponent`-encoded in the PUT path so the BFF catch-all forwards the whole id to the gateway `:path` route.
  - Owner/admin-only — the surface consumes `useCurrentUser`; a `member` (whose `GET /admin/models` returns 403) sees the ErrorState (role=alert) carrying the BFF error title, NOT a crash and NOT a fabricated list; no Switch is rendered when there is no data.
  - Navigation — a `/models` entry is added to the AppShell `NAV_ITEMS` (labelled "Models", a lucide icon), marked `aria-current="page"` when active; a route shell `app/(dashboard)/models/page.tsx` renders the page component.
  - Design-system + a11y floor — consume the v15 `Switch` (no ad-hoc toggle) + v13 state components + `@theme` token classes only (no raw hex/px); axe scan ZERO serious/critical (color-contrast excluded in jsdom); keyboard-operable (Switch Space/Enter). The full behavioral suite stays green; coverage ≥ 80%; NO gateway/BFF contract change; ZERO new npm dependency.
</must>
Reject:
<reject>
  - A member (or any non-owner/admin) caller whose model list 403s → render ErrorState (role=alert), never a silent empty list or a crash -> "forbidden_surface"
  - A toggle that does not round-trip the EXACT `{ enabled: boolean }` body to `PUT /admin/models/{id}` (wrong field name / wrong/ unencoded path) -> "contract_drift"
  - An ad-hoc toggle/checkbox instead of the frozen `Switch` primitive, or a hardcoded color/space a `@theme` token covers -> "untokened_or_adhoc"
  - A new npm dependency or any gateway/BFF route/field change (this is presentation-only over an existing endpoint) -> "scope_creep"
  - A Switch with a serious/critical axe violation or no accessible name, or not operable by keyboard -> "a11y_inoperable"
</reject>
After:
<after>
  - `/models` lists the tenant's catalog models, each toggleable via an accessible `Switch` that persists through `PUT /admin/models/{id}` and refetches; a member sees a graceful error; the nav exposes `/models`; axe-clean + keyboard-operable + token-driven; the full suite is green, coverage ≥ 80%, lint clean, ZERO new dependency, NO contract change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the slash-containing model id (`openai/gpt-4o`) round-trips correctly through `encodeURIComponent` → the BFF `[...path]` catch-all → the gateway `:path` route — LOWEST confidence because it crosses THREE layers (client encode, Next.js segment decode + `join("/")`, uvicorn re-decode) and jsdom/msw only proves the CLIENT-issued URL, not the real Next→gateway reconstruction (that is browser/integration residue, same class as v13's). If wrong: a one-line change (encode↔raw) in `ModelsPage`, no contract-shape change; the gateway already documents `:path` delivers the decoded id (`router.py:166-168`), and `join("/")` reassembles either form to the same upstream, so the production risk is low but unprovable in jsdom.
  - [ ] both GET and PUT are owner/admin-only (member 403 on read too) — CONFIRMED by reading `router.py:118` + `:161` (`require_owner_or_admin` on both) → the surface is admin-only end-to-end; the member path is an error state, not a read-only list (no untestable `canEdit`-disabled-Switch dead branch).
  - [ ] the response envelope is `{ object, data: AdminModelItem[] }` not a bare array — CONFIRMED from `schemas.py:37-53` (`AdminModelsListResponse`); the query maps `.data` (unlike `/admin/keys` which returns a bare array).
  - [ ] `enabled` defaults true when no tenant override exists — CONFIRMED (`router.py:149-150`); the UI just renders the server-returned boolean, no client default needed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner lists catalog models
  Given an owner session and GET /admin/models returns two models (one enabled, one disabled)
  When the /models page renders
  Then within the models list each model's name + id are shown and each row has a Switch whose aria-checked matches its enabled flag

Scenario: Toggle a model off persists the exact contract body
  Given an owner viewing a model "openai/gpt-4o" with enabled=true
  When the user flips its Switch off
  Then a PUT to /api/gw/admin/models/openai%2Fgpt-4o fires with body exactly { "enabled": false }
  And the admin-models query is invalidated (a refetch is issued) -> else "contract_drift"

Scenario: Toggle a disabled model on
  Given an owner viewing a model with enabled=false
  When the user flips its Switch on
  Then a PUT fires with body exactly { "enabled": true } to that model's encoded id path

Scenario: Loading and empty states
  Given GET /admin/models is pending, then resolves to an empty list
  When the page renders through both
  Then a role=status loading indicator shows first, then an Empty state (no models) — and no Switch is rendered

Scenario: Member is forbidden (owner/admin-only surface)
  Given a member session and GET /admin/models returns 403
  When the /models page renders
  Then a role=alert ErrorState carrying the BFF error title is shown
  And NO model list and NO Switch are rendered -> else "forbidden_surface"

Scenario: Models surface is axe-clean and keyboard-operable
  Given an owner viewing a populated /models list
  When axe scans the container and the user focuses a Switch and presses Space
  Then there are zero serious/critical violations, each Switch has an accessible name, and Space toggles it -> else "a11y_inoperable"

Scenario: Navigation exposes /models
  Given the AppShell nav
  When it renders with activePath="/models"
  Then a "Models" link to /models exists and is marked aria-current="page"

Scenario: No new dependency, design-system consumed
  Given the page source and package.json
  When the toggle is rendered
  Then it is the shared Switch primitive (not an ad-hoc control) and package.json + allowlist.json are UNCHANGED -> else "scope_creep"

Scenario: Behavioral floor stays green
  Given the full vitest suite plus the new model-mgmt tests
  When it runs with coverage
  Then all tests pass and coverage >= 80%, and no existing surface, primitive, or shared setup was modified (other than the additive NAV_ITEMS entry)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# UI-SURFACE contract. The HTTP endpoints are ALREADY FROZEN gateway contracts (consumed
# verbatim, NOT defined here). This freezes the dashboard surface shape + the exact request it issues.

DATA SEAM (consumed, unchanged) ────────────────────────────────────────────────
  GET  /admin/models                          (via bffGet, owner/admin-only)
    200 -> { object: "list", data: AdminModelItem[] }
    403 -> ERR_AUTH_FORBIDDEN (member)         -> surface as ErrorState(role=alert)
  PUT  /admin/models/{model_id:path}           (via bffPut, owner/admin-only)
    body: { enabled: boolean }                 # EXACT field — the only mutable field
    200 -> AdminModelItem
    404 -> ERR_MODEL_NOT_FOUND                  -> surface as ErrorState (mutation error)
  AdminModelItem = { id: string; name: string; context_length: number | null; enabled: boolean }
  PUT path: `/admin/models/${encodeURIComponent(model.id)}`  (slash-safe for openai/gpt-4o)

NEW FILES ──────────────────────────────────────────────────────────────────────
  components/models/ModelsPage.tsx
    export function ModelsPage(): JSX.Element            // "use client"
    - useQuery<AdminModelsListResponse>({ queryKey: ["admin-models"], queryFn: () => bffGet("/admin/models") })
    - useMutation({ mutationFn: ({id,enabled}) => bffPut(`/admin/models/${encodeURIComponent(id)}`, { enabled }),
                    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-models"] }) })
    - four states: Loading(role=status) · Empty · ErrorState(role=alert, title=getErrorTitle(err)) · success table/Card
    - each row: model name + id + context length + <Switch checked={m.enabled}
        aria-label={`Enable ${m.name}`} disabled={mutation.isPending && pendingId===m.id}
        onCheckedChange={(next)=>mutation.mutate({id:m.id,enabled:next})} />
  app/(dashboard)/models/page.tsx
    export default function ModelsRoute(): JSX.Element   // renders <ModelsPage/>, metadata title "Hydroa"

EDIT (additive only) ────────────────────────────────────────────────────────────
  components/ui/app-shell.tsx  NAV_ITEMS += { href: "/models", label: "Models", icon: <lucide icon> }

ACCEPTANCE BAR (the gate): list renders the four states; a toggle issues PUT with body EXACTLY
  { enabled } to the encoded id path + invalidates ["admin-models"]; member 403 → ErrorState (no list,
  no Switch); axe(container) ZERO serious|critical (color-contrast off, jsdom) + Switch keyboard-operable
  + token classes only; package.json/allowlist.json UNCHANGED; full suite green; coverage >= 80%.
Reject codes: forbidden_surface · contract_drift · untokened_or_adhoc · scope_creep · a11y_inoperable
Schema: NONE new — consumes existing catalog endpoints (models / tenant_model_overrides tables owned by the gateway); the dashboard adds NO data, route, or field.
```

Status: FROZEN @ v1 — approved by Tin (delegated auto mode, v15 surface task 2; consumes already-frozen gateway endpoints, presentation-only)

**Least-sure flag surfaced at freeze:** `[spec]` — the slash-containing model id (`openai/gpt-4o`)
round-tripping through `encodeURIComponent` → the Next.js `[...path]` catch-all (`pathSegments.join("/")`)
→ the gateway `:path` route. *Why it's the riskiest call:* the rest of the surface is a mechanical
mirror of KeysPage (useQuery/useMutation + four states), but the encoded-slash path crosses three
decode/encode layers and the jsdom/msw test can only assert the CLIENT-issued URL — not the real
Next→gateway reconstruction (browser/integration residue, same class v13 declared). *Cost if wrong:*
a one-line `encode↔raw` change in `ModelsPage`, no contract-shape change; `join("/")` reassembles either
form to the identical upstream and the gateway documents `:path` delivers the decoded id (`router.py:166-168`),
so the production risk is low — but I am flagging it as the single point I cannot fully prove in jsdom.
Second-most unsure `[contract]`: the response is the `{object,data}` ENVELOPE (map `.data`), not the
bare-array shape `/admin/keys` uses — a wrong unwrap would render Empty for a non-empty list; pinned by
the list-render test asserting the two seeded models appear.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag. -->
<!-- EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze. -->
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% line (held; ModelsPage is render+behavior tested through msw). TRUE-RED reason: `components/models/ModelsPage.tsx` does not exist → the import is MODULE_NOT_FOUND until Build. Suite lives in the "bff" vitest project (msw same-origin handlers) — the surface mutates via `bffPut`, the bff project's lane.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  In `apps/dashboard/tests-bff/model-mgmt.test.tsx` (bff project; QueryClientProvider wrapper, msw server.use overrides):
  - test_owner_lists_models: server.use GET /api/gw/admin/models → {object:"list", data:[{id:"openai/gpt-4o",name:"GPT-4o",context_length:128000,enabled:true},{id:"anthropic/claude-3.5",name:"Claude 3.5",context_length:200000,enabled:false}]} / render / assert within the list both names+ids appear and each row's Switch aria-checked matches enabled (true/false)
  - test_toggle_off_puts_exact_body: seed one enabled model / server.use PUT capturing request → click its Switch / assert a PUT hit /api/gw/admin/models/openai%2Fgpt-4o with JSON body EXACTLY {enabled:false} (capturedBody deep-equal) + assert refetch (GET called again after invalidate)
  - test_toggle_on_puts_true: seed one disabled model / flip Switch / assert PUT body {enabled:true} to its encoded id path
  - test_loading_then_empty: GET delayed then → {object:"list",data:[]} / assert role=status appears, then Empty + assert NO switch role present
  - test_member_forbidden_shows_error: server.use /api/auth/me → role:"member" AND GET /admin/models → 403 problem+json / render / assert role=alert with the error title + assert queryByRole("switch") is null + no model name rendered
  - test_models_axe_and_keyboard: populated list / axeSeriousCritical(container) == [] / focus a Switch, keyboard Space → assert PUT fires (keyboard-operable) + each Switch has an accessible name
  - test_nav_exposes_models: render AppShell activePath="/models" / assert a link "Models" → /models with aria-current="page" (pure-props, no msw)
  - test_no_new_dependency_and_switch_used: assert the rendered toggle is role="switch" (the shared primitive, not a checkbox/ad-hoc) — dependency pin already enforced milestone-wide by test_deps_allowlisted; this asserts the design-system control is consumed
</test_plan>

Tests live in: `model-mgmt.test.tsx` · MUST run red (ModelsPage absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/models/` `apps/dashboard/app/(dashboard)/models/` `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/tests-bff/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/model-management-ui/`
<!-- NEW: components/models/ModelsPage.tsx + app/(dashboard)/models/page.tsx + tests-bff/model-mgmt.test.tsx.
     EDIT (additive only): components/ui/app-shell.tsx NAV_ITEMS (one row + one icon import).
     .next/coverage/tsbuildinfo are the gitignored build artifacts scope-lock flags (engine
     _SCOPE_EXCLUDE_DIRS = .git/.add/__pycache__/node_modules only). NO gateway, NO bff-client,
     NO catch-all route, NO other surface, NO shared setup, NO package.json, NO allowlist.json,
     NO handlers.ts (tests use server.use overrides) — touching those is scope_creep. -->
Strategy (ordered batches): 1. RED suite `tests-bff/model-mgmt.test.tsx` (imports absent ModelsPage). 2. `ModelsPage.tsx` (useQuery `.data` envelope + useMutation encoded-PUT + four states + Switch rows). 3. `app/(dashboard)/models/page.tsx` route shell. 4. add the `/models` NAV_ITEMS row. 5. run the bff suite green, then full-suite + coverage + lint gate.
Safety rule (feature-specific): data-identical — issue the EXACT `{ enabled }` body to the encoded id path; consume the shared `Switch` + state components; token classes only; NO new dependency, NO contract change.
Code lives in: `apps/dashboard/components/models/`
Constraints: do NOT change any test or the contract; allow-list packages only (none added); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 142/142 (140 prior + 2 added during the re-cross: 404-error + disabled-during-mutation); 10/10 in `tests-bff/model-mgmt.test.tsx`.
- [x] coverage did not decrease — 91.65% line (≥ 80% gate held, coverage exit=0; UP from 91.06%); `ModelsPage.tsx` 95.34% line / `app-shell.tsx` 100% (uncovered: the `onSettled` clear + the null-context "—" fallback — minor non-critical edges).
- [x] no test or contract was altered during build — the §3 contract is UNCHANGED post-freeze; the only test file is the NEW `model-mgmt.test.tsx`. The re-cross ritual was used once (added the 404 + disabled tests as RED, re-snapshotted the tripwire at tests→build) so the strengthening is legitimate, not a mid-build test edit.
- [x] the green was EARNED, not gamed — adversarial refute-read (subagent, model sonnet) VERDICT EARNED-WITH-GAPS. It REFUTED nothing on the core contract (PUT body `{enabled}` deep-equal; `encodeURIComponent` slash path `openai%2Fgpt-4o`; `{object,data}` envelope unwrap; per-Switch accessible name; keyboard Space; additive-only nav; zero new dep) but found ONE real DEFECT + 2 GAPs, ALL closed: (1) DEFECT — the frozen §3 "PUT 404 → ErrorState (mutation error)" was unimplemented (a failed toggle was silent) → wired `{toggleModel.isError && <ErrorState .../>}` + `test_toggle_put_404_shows_error`; (2) GAP — no 404 test → added; (3) GAP — disabled-during-mutation untested → added `test_switch_disabled_during_mutation` (delayed PUT + waitFor on both transitions).
- [x] concurrency / timing of the risky operation is safe — the toggle is a single idempotent PUT; the Switch is `disabled` while its own mutation is in flight (`pendingId` guard) so no double-submit; on failure the server state is unchanged and surfaced (no optimistic-rollback race); refetch-on-success keeps the list authoritative.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets; no client Authorization (cookie via `credentials:"include"`); the model id is `encodeURIComponent`-encoded (no path-injection); ZERO new npm dependency (package.json + allowlist.json UNCHANGED; no `handlers.ts` edit — tests use `server.use` overrides); no raw-HTML injection sink (all React-escaped).
- [x] layering & dependencies follow CONVENTIONS.md — data-identical BFF seam (exact gateway field names, NO gateway/bff-client/catch-all change); mirrors KeysPage (useQuery/useMutation + invalidate + four state components); consumes the v15 `Switch` (no ad-hoc toggle) + `@theme` token classes; axe-in-jsdom impact serious|critical + color-contrast disabled.
- [x] a person reviewed and approved the change — Tin (delegated auto mode) + the adversarial subagent refute-read; every actionable finding closed before the gate.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `ModelsPage` is imported by the route shell `app/(dashboard)/models/page.tsx` and by the test; `getErrorTitle` is referenced on BOTH the query-error and mutation-error paths; `pendingId` is set in `handleToggle` and read by the Switch `disabled`; the `/models` `NAV_ITEMS` row + `Boxes` icon are rendered by `AppShell` (test_nav_exposes_models confirms the link + aria-current).
- [x] DEAD-CODE (code) — no orphaned symbol; the deliberate NON-inclusion of `useCurrentUser` avoids an untestable dead `canEdit`-disabled branch (the surface is admin-only on the BACKEND, so the member case is the server 403 → ErrorState, fully reachable + tested). The mutation-error branch is now reachable + tested.
- [x] SEMANTIC (prose / non-code) — read in full: the FROZEN §3 contract + §1 Must/Reject + the gateway `router.py` GET/PUT handlers + the refute-read report. Confirmed the impl honors every GUARANTEE; confirmed the one spec imprecision (§1 "consumes useCurrentUser" vs its own "carrying the BFF error title") resolves to the server-403 mechanism (recorded as a §7 SDD delta), and that the observable forbidden_surface behavior + the §3 acceptance bar are fully met.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (delegated auto) + adversarial refute-read subagent · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): model-toggle PUT error rate (404 ERR_MODEL_NOT_FOUND / 403) now surfaced inline; per-tenant disable rate; member 403 hit-rate on `/admin/models` (signals members reaching an admin-only nav link → candidate for role-filtered nav).
Spec delta for the next loop: nav has no per-role filtering yet — a `member` sees the `/models` link and gets a 403 ErrorState on click. Acceptable for now (graceful, server-authoritative), but the upcoming SSO-owner-only + teams owner/admin surfaces will need real role-based nav visibility; carry a milestone-level "role-filtered NAV_ITEMS" concern into `tenant-settings-ui`/`feature-coverage-verify`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [SDD · folded] a spec rule should name the OBSERVABLE, not a mechanism — §1 said "consumes useCurrentUser" yet ALSO required the member to see "the ErrorState carrying the BFF error title", which only the server 403 produces; naming the hook created a phantom requirement that, if honored literally, would be untestable dead code (evidence: refute-read DEFECT 2; resolved by the server-403 gate). Future surface specs: state "member → role=alert error", let the mechanism follow.
- [TDD · folded] the refute-read caught a frozen-contract clause the build silently skipped — §3's "PUT 404 → ErrorState (mutation error)" had no test AND no impl; a passing suite looked complete because the missing path had no red anchor (evidence: `test_toggle_put_404_shows_error` was added after the fact). Lesson: every contracted error branch needs its own §4 test BEFORE build, not just the happy path + the read-side rejection.
- [UDD · folded] role-based NAV visibility is now a cross-surface need (admin-only `/models`, owner-only SSO) — the static `NAV_ITEMS` shows links a member cannot use; carry into a milestone-level nav-RBAC concern (evidence: §7 spec delta above).
