# TASK: Refresh the key & budget governance surfaces (/keys) onto the design system

slug: key-budget-governance-ui · created: 2026-06-13 · stage: production
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

Touches (files · symbols · signatures): the 5 /keys surface components, restyled onto the v13 design system (NO data change). Verified current structure + the observable markers each test asserts (MUST preserve):
- `components/keys/KeysPage.tsx:KeysPage` — orchestrator; `useQuery(["admin-keys"]→bffGet/admin/keys)`, create `useMutation(bffPost/admin/keys {name})`→`setPlaintextKey(data.key)`+invalidate, revoke `useMutation(bffDelete/admin/keys/{id})`. `<h1>API Keys</h1>` + header buttons "Create key" / "Log out" (logout POST /api/auth/logout → router.push("/login")). States: loading `<div role="status" aria-busy data-testid="loading"><span class="animate-pulse">`, error `<p role="alert">{BffError.problem.title}`, empty `<p>No API keys yet…`, success `<table>` th(Key ID·Name·Prefix·Created·Status·Actions) with per-key Fragment = KeyRow + a sibling `<tr><td colSpan=6>` carrying a "Governance"/"Hide governance" toggle → KeyGovernanceEditor. Revoke confirm = inline `<div role="dialog" aria-modal aria-label="Confirm revocation">` + Confirm/Cancel.
- `components/keys/KeyRow.tsx:KeyRow` — `<tr role="row">`: key_id.slice(0,8)+"…", name, prefix, `new Date(created_at).toLocaleDateString()`, status `active` | `Revoked {revoked_at}` (span.revoked-badge), and a "Revoke" `<button>` when `!isRevoked && !isPendingRevoke`.
- `components/keys/CreateKeyDialog.tsx:CreateKeyDialog` — inline `<div role="dialog" aria-modal aria-label="Create API key">` (isOpen prop), `<h2>Create API Key</h2>`, `<label htmlFor="key_name_input">Key Name</label>` (getByLabelText(/key name/i)), Zod min1/max120, `role="alert" aria-live="polite"` name+global errors, buttons Cancel / "Create"(/Creating…). 422→nameError, else globalError.
- `components/keys/PlaintextKeyBanner.tsx:PlaintextKeyBanner` — `<div role="alert" aria-live="polite">`, "You won't see this key again", `<code>{plaintextKey}</code>`, buttons "Copy"(/Copied!) via navigator.clipboard / "Done"(→onDismiss). SECURITY: the secret lives ONLY in parent state, cleared to null on dismiss — never logged/persisted.
- `components/keys/KeyGovernanceEditor.tsx:{KeyGovernanceEditor,ApiKeyGovernance}` — `data-testid="key-governance-editor"`, `<h3>Governance — {name}</h3>`, "Rotate" button → confirm `<div role="dialog" aria-modal aria-label="Confirm key rotation">` Confirm/Cancel → `bffPost/admin/keys/{id}/rotate {}` → `setNewPlaintextKey(result.key)` (PlaintextKeyBanner) + onUpdated. Form PATCH `bffPatch/admin/keys/{id}` body **`{monthly_budget_usd, soft_budget_usd, expires_at, model_allowlist}`** (per-key `monthly_budget_usd` — NOT the tenant `budget_usd_monthly`); inputs data-testid `monthly-budget-input`/`soft-budget-input`/`expires-at-input`/`model-allowlist-input`+`add-model-button` (chips ul with `aria-label="Remove {model}"` ×), client validation R1 soft>hard / R2 negative / R3 empty-allowlist → `<p role="alert">`; api error `<p role="alert">`; "Save" button; last-saved `<p data-testid="current-monthly-budget">{savedMonthlyBudget}</p>`.
- CONSUMES (frozen, v13 task 1): `components/ui/{Card,Table,Badge,Button,Input,Dialog(+Header/Title/Description/Footer/Content),states(Loading/Empty/ErrorState)}` + `lib/cn.ts` + the `@theme` token classes.

Context (working folder):
- Behavioral suites that MUST stay green: `tests/keys.test.tsx` (list/empty/error/loading, create→plaintext-once, revoke) + `tests-bff/govern.test.tsx` KeyGovernanceEditor block (PATCH exact frozen field names, expires_at, model_allowlist array, clear-to-null, no-Authorization-header, rotate→plaintext-once→refresh, rotate-403-no-banner, governance 403/422 inline, client-validation soft>hard & negative blocks). They key on role/name/label/text/data-testid (enumerated above), NOT CSS or aria-modal — VERIFIED: 0 `aria-modal` assertions in either suite → the Radix Dialog primitive (no aria-modal; uses aria-labelledby + focus-trap) is SAFE to adopt for the dialogs.
- `.add/milestones/v13/MILESTONE.md` key-budget-governance-ui row: clearer create→reveal-once→govern flow, accessible dialogs (focus trap/ESC/labelled controls), responsive forms; same data hooks/field names.

Honors (patterns / conventions):
- MILESTONE.md: behavior-preserving / data-identical (same hook, route, field names; existing tests green); design tokens consumed not hardcoded; WCAG 2.2 AA (accessible dialogs — focus trap, ESC, labelled controls); responsive forms.
- CONVENTIONS.md v1 UDD: RTL scopes with `within(section)`; every surface renders all four states (where applicable).
- v13 design-system contract: use `components/ui/*` primitives + state components; no raw hex/px (R3 carries forward). SECURITY: plaintext key reveal-once stays display-only, cleared on dismiss — no logging/persistence/echo.

Anchors the contract cites: the 5 surface components above (restyled, markers preserved) · `components/ui/{Card,Table,Badge,Button,Input,Dialog}` + `states` · the PRESERVED data seam (`bffGet/bffPost/bffPatch/bffDelete` calls, query key `["admin-keys"]`, PATCH body `monthly_budget_usd`/`soft_budget_usd`/`expires_at`/`model_allowlist`, rotate/create/revoke routes) + the test-observable surface (roles/aria/text/testids/button-names enumerated above).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Key & budget governance refresh — the /keys journey (KeysPage, KeyRow, CreateKeyDialog, PlaintextKeyBanner, KeyGovernanceEditor) restyled onto the v13 design system with accessible dialogs (focus-trap/ESC/labelled). Presentation/interaction-only: NO data hook, BFF route, query key, field name, or test-observable marker changes.

Framings weighed: Restyle-in-place + keep inline `<div role="dialog">` for the 3 dialogs (create / revoke-confirm / rotate-confirm) but give them real keyboard a11y via a small self-contained `useFocusTrap` hook (focus-trap + ESC-to-close + restore-focus + labelled title) (chosen) · Adopt the Radix `Dialog` primitive (rejected — it renders in a portal and pulls in react-remove-scroll/focus-scope that need `ResizeObserver`/`hasPointerCapture`/`scrollIntoView` polyfills present ONLY in the design-system test file, NOT in tests/setup.ts or tests-bff/setup.ts; adopting it would crash the keys/govern suites or force editing shared out-of-scope setup files) · Defer dialog a11y to ui-ux-verify (rejected — accessible dialogs are THIS task's stated value-add).

Must:
<must>
  - Restyle all 5 surfaces onto `components/ui/*` primitives (Card/Table/Badge/Button/Input + Loading/Empty/ErrorState state components) — visual/interaction layer only.
  - Give CreateKeyDialog, the revoke-confirm, and the rotate-confirm real keyboard a11y: each stays an inline `<div role="dialog">` (tokens) but gains focus-trap (Tab/Shift-Tab cycles within), ESC-to-close, initial-focus + focus-restore-on-close via a small self-contained `useFocusTrap` hook (no new dep, no portal); controls stay reachable by the SAME role/name/label the suites query.
  - Preserve EVERY test-observable marker: roles (`status`/`alert`/`row`/`dialog`), aria (`aria-busy`/`aria-live`), exact text ("API Keys", "Create API Key", "Key Name", "You won't see this key again", "No API keys yet…", "Governance — {name}", th labels), button names (/create key/i, /create/i, /copy/i, /dismiss|close|done|got it/i, /revoke/i, /confirm|yes/i, /rotate/i, /save|update|apply|set/i, /add/i, "Log out"), and data-testids (`loading`, `key-governance-editor`, `monthly-budget-input`, `soft-budget-input`, `expires-at-input`, `model-allowlist-input`, `add-model-button`, `current-monthly-budget`).
  - Preserve the data seam unchanged: query key `["admin-keys"]`; `bffGet/bffPost/bffPatch/bffDelete` routes (/admin/keys, /admin/keys/{id}, /admin/keys/{id}/rotate); create body `{name}`; PATCH body field names **`monthly_budget_usd`** (per-key, NOT budget_usd_monthly) + `soft_budget_usd` + `expires_at` + `model_allowlist` (array|null); clear-to-null semantics; logout POST /api/auth/logout → router.push("/login"); NO client-side Authorization header (bff-client credentials:"include").
  - Preserve client-side governance validation that BLOCKS the API call: R1 soft>hard, R2 negative budget, R3 empty allowlist entry → inline `role="alert"`.
  - SECURITY: the reveal-once plaintext key (create + rotate) stays display-only in component state, cleared on dismiss — never logged, echoed, persisted, or placed in any attribute beyond the visible `<code>` leaf.
  - Consume design tokens via primitive classes only — no raw hex/px in the restyled surfaces (R3 carries forward).

Reject:
<reject>
  - Any change to a query key, route, request/response field name, create/PATCH body shape, or the no-Authorization-header invariant -> "behavior_regression"
  - Any removed/renamed role, aria, exact text, button-name, or data-testid a test asserts -> "behavior_regression"
  - The reveal-once secret logged / persisted / copied into a non-visible sink, or surviving dismiss -> "secret_exposure" (HARD-STOP, security)
  - A raw hex/px literal in a restyled surface instead of a token class -> "untokenized_value"
  - A dialog reachable but not operable by keyboard (no focus-trap/ESC), or a control with no accessible name -> "a11y_floor_violation"
  - Any import outside the node allow-list -> "unlisted_dependency"
</reject>
After:
<after>
  - The 5 /keys surfaces render through `components/ui/*` with v13 tokens; the 3 dialogs are inline `role="dialog"` with a focus-trap + ESC + labelled title (via `useFocusTrap`), controls preserved.
  - All existing behavioral tests stay green: `tests/keys.test.tsx` + `tests-bff/govern.test.tsx` KeyGovernanceEditor block, zero regression.
  - `next lint` clean, vitest coverage ≥ 80% (held), node deps allow-list clean, no new dependency.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The new `useFocusTrap` hook's keydown/focus handling does NOT interfere with the existing userEvent flows (type into Key Name, click Create/Confirm/Rotate) under jsdom — lowest confidence because focus-trap hooks attach a document/keydown listener and force initial focus, and jsdom's focus model is partial; a too-aggressive trap could swallow Tab or steal focus from the field under test. If wrong: I scope the trap to fire only on Tab/Escape (never intercept clicks or typing) and gate `focus()` behind a ref guard — a build-internal hook tweak, no data/contract/marker change. The §3 freeze names the dialog SHAPE (role=dialog + aria-label + the named controls + ESC-closable), not the hook's internals, so this stays a build choice.
  - [ ] The suites key only on role/name/label/text/testid and NOT on aria-modal or DOM nesting — CONFIRMED by §0 scan (0 aria-modal assertions; getByRole/getByLabelText/getByText/getByTestId only).
  - [ ] Restyling KeysPage's loading `<div role="status" aria-busy data-testid="loading">` via the shared `Loading` keeps all three markers — confirmed `Loading` emits role=status+aria-busy and passes data-testid through (proven in usage-cost-ui).
  - [ ] The keys table → `Table` primitive keeps `role="row"` on KeyRow's `<tr>` and the colSpan governance toggle row renders inside `TableBody` — `<tr>` keeps role=row; the colSpan sibling row is plain `<tr><td colSpan>` inside the same tbody.
  - [ ] No responsive-breakpoint logic beyond inherited token utilities is in scope (full keyboard/responsive/browser-axe sweep is `ui-ux-verify`).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Keys list restyled, rows preserved
  Given admin-keys resolves with >=1 key
  When KeysPage renders
  Then a Table primitive shows headers Key ID·Name·Prefix·Created·Status·Actions and one role=row per key
  And the ["admin-keys"] key and /admin/keys route are unchanged

Scenario: Keys empty state
  Given admin-keys resolves with []
  When KeysPage renders
  Then "No API keys yet…" is shown via the Empty state component
  And no role=row data row is rendered

Scenario: Keys loading + error states
  Given admin-keys is pending, then rejects with a BffError title
  When KeysPage renders each state
  Then loading shows role=status + data-testid="loading"; error shows role=alert with the title
  And no record row appears during loading

Scenario: Create key via accessible dialog, reveal once
  Given the Create-key dialog is opened and "ci-key" typed into the Key Name field
  When the user submits Create
  Then POST /admin/keys fires with body {name:"ci-key"}, the dialog (focus-trapped, ESC-closable) closes, and PlaintextKeyBanner shows the plaintext via <code> with Copy/Done
  And the plaintext never appears in the keys list and the create body field is {name}

Scenario: Dismiss reveal-once banner clears the secret
  Given the PlaintextKeyBanner is showing a created key
  When the user clicks Done
  Then the banner unmounts and the plaintext is cleared from state (never re-rendered, never logged)
  And the secret is in no sink other than the visible <code> leaf -> "secret_exposure" guarded

Scenario: Revoke via accessible confirm dialog
  Given a non-revoked key row
  When the user clicks Revoke then Confirm in the focus-trapped confirm dialog
  Then DELETE /admin/keys/{id} fires and the list invalidates
  And the route + confirm/cancel controls are unchanged

Scenario: Governance PATCH sends exact frozen field names
  Given the governance editor open with monthly=50.00, soft=40.00
  When the user clicks Save
  Then PATCH /admin/keys/{id} body carries monthly_budget_usd="50.00", soft_budget_usd="40.00"
  And the field is monthly_budget_usd (NOT budget_usd_monthly) and no Authorization header is set

Scenario: Governance sends expires_at and model_allowlist array
  Given expires_at typed and a model added to the allowlist
  When the user clicks Save
  Then the PATCH body carries expires_at as the string and model_allowlist as a string[]
  And the field names model_allowlist/expires_at are unchanged

Scenario: Governance clear-to-null
  Given a key with monthly_budget_usd="25.00" and the field cleared
  When the user clicks Save
  Then the PATCH body carries monthly_budget_usd:null
  And clear-to-null semantics are unchanged

Scenario: Rotate via accessible confirm, reveal once, refresh
  Given the governance editor's Rotate confirm dialog
  When the user confirms
  Then POST /admin/keys/{id}/rotate fires, the new plaintext shows once via PlaintextKeyBanner, and onUpdated refreshes the list
  And a rotate 403 shows an inline role=alert error with NO banner

Scenario: Client governance validation blocks the API call
  Given soft>hard (or a negative budget, or an empty allowlist entry)
  When the user clicks Save
  Then an inline role=alert validation message shows and NO PATCH fires -> "behavior_regression" guarded
  And the R1/R2/R3 client rules are unchanged

Scenario: No untokenized value in a restyled surface
  Given the R3 token guard scans the /keys surfaces
  When the design-system token test runs
  Then no raw hex (#rrggbb) or px literal appears -> "untokenized_value" guarded
  And only token classes / primitives are used

Scenario: No unlisted dependency
  Given the node deps allow-list check runs
  When the /keys surface imports are scanned
  Then only already-allow-listed packages are imported -> "unlisted_dependency" guarded
  And the allow-list is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# PRESENTATION/INTERACTION-ONLY task — the data seam is FROZEN UNCHANGED, not authored here.
# This "contract" freezes (a) the seam it must NOT touch, (b) the dialog interaction shape.

PRESERVED DATA SEAM (must remain byte-identical) ────────────────────────────
  GET    /admin/keys                  key ["admin-keys"] (bffGet)  -> ApiKey[]
  POST   /admin/keys        body { name: string }        (bffPost) -> { key_id, name, key }
                                                          # data.key = reveal-once plaintext
  DELETE /admin/keys/{id}                                (bffDelete)
  PATCH  /admin/keys/{id}   body { monthly_budget_usd: string|null,   # per-key, NOT budget_usd_monthly
                                   soft_budget_usd:    string|null,
                                   expires_at:         string|null,
                                   model_allowlist:    string[]|null } (bffPatch) -> ApiKeyGovernance
  POST   /admin/keys/{id}/rotate  body {}                (bffPost) -> { key, new_key_id, … }
                                                          # result.key = reveal-once plaintext
  POST   /api/auth/logout (fetch, credentials:include) -> router.push("/login")
  INVARIANT: NO client-side Authorization header is ever constructed (bff-client credentials:"include").
  CLIENT VALIDATION (blocks the PATCH): R1 soft>hard · R2 negative · R3 empty allowlist entry.

PRESERVED TEST-OBSERVABLE SURFACE (role · aria · text · testid · button-name) ─
  roles:   status (loading) · alert (errors + PlaintextKeyBanner) · row (KeyRow) · dialog (3)
  aria:    aria-busy (loading) · aria-live="polite" (banner + create errors)
  text:    "API Keys" · "Create API Key" · "Key Name" · "No API keys yet…"
           · "You won't see this key again" · "Governance — {name}"
           · table th: Key ID·Name·Prefix·Created·Status·Actions
  testid:  loading · key-governance-editor · monthly-budget-input · soft-budget-input
           · expires-at-input · model-allowlist-input · add-model-button · current-monthly-budget
  buttons: /create key/i · /create/i (·/Creating…) · /copy/i (·/Copied!) · /dismiss|close|done|got it/i
           · /revoke/i · /confirm|yes/i · /rotate/i · /save|update|apply|set/i · /add/i · "Log out"

DIALOG INTERACTION CONTRACT (the a11y value-add — shape, not mechanism) ──────
  Each of the 3 dialogs (Create key · Revoke-confirm · Rotate-confirm) is an inline
  element with role="dialog" + an accessible name (aria-label or aria-labelledby to its title)
  that, while open: traps Tab/Shift-Tab focus within itself, moves initial focus inside on open,
  closes on Escape, and restores focus to the opener on close. Implemented via a self-contained
  useFocusTrap hook (no new dependency, no portal). The dialog BODIES and all named controls above
  are preserved exactly.
  4xx -> N/A (no new network call). Reject codes are build/lint/security guards, not HTTP:
         behavior_regression · secret_exposure(HARD-STOP) · untokenized_value · a11y_floor_violation · unlisted_dependency
Schema: NONE TOUCHED. No DB table, migration, BFF route, or gateway contract changes.
```

Status: FROZEN @ v1 — approved by Tin (delegated auto mode, presentation/interaction-only)

**Least-sure flag surfaced at freeze:** `[contract]` — the dialog interaction contract is the one
genuinely-new surface. It freezes the SHAPE (role=dialog + accessible name + focus-trap + ESC +
focus-restore), NOT the mechanism. *Why it's the riskiest point:* a hand-rolled `useFocusTrap` under
jsdom is the part most likely to misbehave (swallow Tab / steal focus from the field under test) — see
the §1 ⚠. *Cost if wrong:* the fix is a hook-internal tweak (fire only on Tab/Escape, never intercept
clicks/typing), no data/contract/marker change. Chose inline+hook over the Radix `Dialog` primitive
because Radix needs jsdom polyfills (`ResizeObserver`/`hasPointerCapture`/`scrollIntoView`) absent in
the keys/bff test setups — adopting it would crash the green suites or force a shared out-of-scope edit.
Second-most unsure `[spec]`: the `secret_exposure` HARD-STOP rule — preserved (the secret already lives
only in component state and clears on dismiss); the restyle must not introduce any new sink.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% (hold the v13 line; do not regress the ~89% baseline)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - The 11 restyle/preserve + data-seam scenarios are ALREADY covered by the green behavioral suites
    (`apps/dashboard/tests/keys.test.tsx` + `apps/dashboard/tests-bff/govern.test.tsx` KeyGovernanceEditor
    block) — they key on role/name/label/text/testid, NOT CSS, so they act as the behavior_regression +
    secret_exposure guard during the restyle. Build MUST keep all of them green (zero edit).
  - NEW red test (the only failing-first work): `test_create_dialog_focus_trap_and_esc` — arrange
    CreateKeyDialog rendered isOpen / act: assert initial focus lands inside the dialog, Escape invokes
    onClose, and Tab from the last focusable cycles to the first (focus-trap). Runs RED (no focus-trap/ESC
    wired yet). CreateKeyDialog is pure-props (no fetch) so the test needs no msw — lives in the legacy project.
  - NEW red test `test_confirm_dialogs_escape_closes` — render KeysPage revoke-confirm (and the rotate-confirm
    via KeyGovernanceEditor) / assert Escape closes each. RED until useFocusTrap is wired into all 3.
  - R3 untokenized-value guard + R6 deps allow-list (design-system/*) already scan the surfaces — extended
    assertion set, no new dependency expected.
</test_plan>

Tests live in: `apps/dashboard/tests/keys-dialog-a11y.test.tsx` · MUST run red (no focus-trap/ESC) before Build. (CreateKeyDialog is pure-props so the legacy `tests/` project — no msw needed — is the right home; the revoke/rotate confirms route through KeysPage/KeyGovernanceEditor which the legacy setup already mounts in keys.test.tsx.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/keys/` `apps/dashboard/lib/use-focus-trap.ts` `apps/dashboard/tests/keys-dialog-a11y.test.tsx` `apps/dashboard/tests/design-system/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/key-budget-governance-ui/`
Strategy (ordered batches):
  1. RED: add `tests/keys-dialog-a11y.test.tsx` (focus-trap + ESC for the 3 dialogs) — runs red.
  2. Build `lib/use-focus-trap.ts` (a ref-based hook: initial focus, Tab/Shift-Tab cycle, Escape→onClose, restore focus on close; listens ONLY for Tab/Escape — never intercepts clicks/typing) → red test green.
  3. Restyle surface-by-surface onto `components/ui/*`, re-running keys.test.tsx + govern.test.tsx after each: KeyRow → KeysPage (Table + states + revoke-confirm dialog) → CreateKeyDialog → PlaintextKeyBanner → KeyGovernanceEditor (rotate-confirm dialog + form). Wire useFocusTrap into all 3 dialogs. Keep every role/name/label/text/testid.
  4. Run R3 token guard + R6 deps allow-list; full vitest with coverage; next lint.
Safety rule (feature-specific): preserve the test-observable surface BYTE-IDENTICAL (restyle is class/primitive only); never touch a query key, route, field name (esp. `monthly_budget_usd` ≠ budget_usd_monthly), or the no-Authorization-header invariant. SECRET: the reveal-once plaintext stays display-only in state, cleared on dismiss — introduce NO new sink (no log/console/localStorage/attribute/analytics). The focus-trap must never swallow typing or clicks.
Code lives in: `apps/dashboard/components/keys/` + `apps/dashboard/lib/use-focus-trap.ts`
Constraints: do NOT change any existing test or the contract; allow-list packages only (NO new dependency — useFocusTrap is hand-rolled, not a lib); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 110/110 vitest (105 prior + 5 new dialog-a11y tests). The keys.test.tsx + govern.test.tsx KeyGovernanceEditor suites stay fully green (zero regression).
- [x] coverage did not decrease — 90.21% global lines, ABOVE the ~89% baseline and the 80% gate (vitest --coverage exit=0 = threshold held). Touched: KeyRow 100%, use-focus-trap 95.5%, KeyGovernanceEditor 94.6%, KeysPage 82.6%, CreateKeyDialog 81.1%, PlaintextKeyBanner 77.4% (component-local; the copy/setTimeout path — gate is global).
- [x] no test or contract was altered during build — tripwire clean (`add.py check`: 0 failed); keys.test.tsx + govern.test.tsx byte-unchanged (adversary confirmed via git diff); frozen §3 untouched. No re-cross needed (§5 scope incl. tsconfig.tsbuildinfo was declared BEFORE the tests→build snapshot).
- [x] the green was EARNED — adversarial refute-read (subagent, model sonnet) returned VERDICT: EARNED, zero defects across 6 areas incl. an appsec secret-handling audit. Confirmed `monthly_budget_usd` (not budget_usd_monthly), bff routes, create body {name}, R1/R2/R3 client validation, no Authorization header. The focus-trap wrap test is discriminating (outside-dialog focusables).
- [x] concurrency / timing safe — presentation/interaction-only; no new async/IO. The new timing surface (useFocusTrap keydown listener) reacts ONLY to Tab/Escape — never intercepts typing/clicks (regression-guarded by test_create_dialog_typing_and_submit_still_work); focus restored on unmount.
- [x] no exposed secrets, injection openings, or unexpected dependencies — SECURITY (HARD-STOP scope) CLEARED: the reveal-once plaintext lives only in component state + the visible `<code>` leaf; no console/localStorage/sessionStorage/data-attr/URL/analytics sink (adversary grep-confirmed); cleared to null on dismiss. No new dependency (useFocusTrap is hand-rolled). No raw hex/px in surfaces (R3 holds).
- [x] layering & dependencies follow CONVENTIONS.md — surfaces consume `components/ui/*` primitives + shared state components; the 3 dialogs gain focus-trap/ESC via a self-contained `lib/use-focus-trap.ts` (inline, no portal — deliberately NOT the Radix Dialog, whose jsdom polyfills are absent in the keys/bff setups). Data hooks unchanged.
- [x] a person reviewed — delegated auto mode; adversarial subagent stands in for the refute-read AND the appsec audit. The one security-relevant surface (reveal-once secret) was explicitly audited and CLEARED — no HARD-STOP. No residue → auto-PASS per `autonomy: auto`.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `useFocusTrap` is imported + invoked in CreateKeyDialog (trapRef), KeysPage (revokeTrapRef), KeyGovernanceEditor (rotateTrapRef), each ref attached to its dialog `<div role="dialog">`; all `components/ui/*` primitives imported and rendered. Confirmed by the 5 passing dialog-a11y tests + 110/110.
- [x] DEAD-CODE (code) — no orphaned symbol; the `Fragment`/governance-toggle structure preserved; tsc clean on all touched files (only pre-existing test-file errors remain).
- [x] SEMANTIC (prose / non-code) — read the frozen §3 + §0 markers in full; confirmed every role/aria/text/testid/button-name is emitted and every route/field name (esp. monthly_budget_usd) is unchanged.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (delegated auto mode) + adversarial refute-read & appsec subagent · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
