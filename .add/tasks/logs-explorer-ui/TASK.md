# TASK: Console Logs Explorer page (table, detail drawer, replay-in-playground)

slug: logs-explorer-ui · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/audit/AuditPage.tsx` + `AuditTable.tsx` — the master-table read-surface precedent this page mirrors: `useQuery` → `Loading`/`ErrorState`/table four-state render, `DataTable` over `@tanstack/react-table` `ColumnDef[]`, zero-row → shared `Empty`.
- `apps/dashboard/components/alerts/AlertsPage.tsx` + `AlertsTable.tsx` — second sibling of the same pattern; `AlertsTable`'s `Badge`-per-status column (`variant="success"|"secondary"`) is the direct precedent for the Logs table's status/blocked badges.
- `apps/dashboard/components/ui/data-table.tsx:DataTable` — generic sortable table; its OPT-IN `pageSizeOptions` pager is **client-side, page-index based** (paginates an already-fetched, in-memory array) — confirmed NOT reusable as-is for a cursor-paginated server API (see Issues #1). `Table`/`TableHeader`/`TableRow`/`TableCell`/`Empty` (the primitives `DataTable` is built from, `apps/dashboard/components/ui/table.tsx`) ARE reused directly by a new bespoke `LogsTable`.
- `apps/dashboard/components/ui/dialog.tsx:DialogContent` (Radix `@radix-ui/react-dialog` Root/Portal/Overlay/Close) + `apps/dashboard/components/ui/badge.tsx:Badge` variants `success|warning|destructive|secondary` + `apps/dashboard/components/ui/states.tsx:Loading/Empty/ErrorState` + `apps/dashboard/components/ui/select.tsx` + `apps/dashboard/components/ui/input.tsx` + `apps/dashboard/components/ui/page-header.tsx:PageHeader` — the full Aurora primitive set this page composes from. `DialogContent` is a CENTERED modal (`fixed left-1/2 top-1/2 … max-w-lg`) — no edge-anchored/slide-in "drawer" variant exists yet anywhere in the catalog (confirmed: `grep -rl drawer\|Sheet` returns only `sidebar.tsx`/`app-shell.tsx`/`page-header.tsx`/`PlatformCommandPalette.tsx`, none of which are a detail-panel drawer).
- `apps/dashboard/components/settings/GuardrailSettings.tsx` — reads/writes `GET/PUT /admin/guardrails` (`{prompt_injection, pii_mask}`); the existing tenant-level guardrail-config surface this page's drawer verdict panel is downstream of (verdicts are a PER-CALL record, this is the PER-TENANT config that produced them — distinct surfaces, no reuse needed beyond knowing the config vocabulary: `prompt_injection`, `pii_mask`).
- `apps/dashboard/components/ui/app-shell.tsx:NAV_GROUPS` (`"Govern"` group, e.g. `{ href: "/app/alerts", label: "Alerts", icon: Bell, minRole: "admin" }` / `{ href: "/app/audit", ... minRole: "admin" }`) — the exact nav-registration idiom a new `{ href: "/app/logs", label: "Logs", ... minRole: "admin" }` entry follows; `NAV_ITEMS` is derived automatically, no second place to register.
- `apps/dashboard/components/chat/ChatWorkspace.tsx:ChatWorkspaceProps` (currently `{ defaultModel?: string }` ONLY, line 143-148) + `apps/dashboard/app/(app)/app/chat/page.tsx` (a bare server component, no props/searchParams wiring today) — confirmed there is NO existing mechanism to pre-seed the composer/model from outside the component (see Issues #2 — this is new ground, not reuse).
- `apps/dashboard/components/chat/ModelPicker.tsx:ModelPickerProps` (`{ value, onChange, className }`, line 30-36) — takes a raw model-id string; does not itself validate against the live catalog, so an unknown/deprecated id from a replayed log is not rejected here (the picker will simply show it selected-but-possibly-stale — confirms the UI, not the picker, must decide the "unknown model" fallback UX).
- `apps/dashboard/lib/bff-client.ts:bffGet/bffPost/bffPut/bffPatch` — the sole BFF-call surface (`GET /api/gw/...` catch-all proxy); `apps/dashboard/lib/format.ts:formatTimestamp/formatUsd/formatNumber` — shared display formatters this page reuses for `created_at`/`cost_usd`/row counts.
- `apps/dashboard/lib/hooks/use-current-user.ts:useCurrentUser` — role read via `/api/auth/me` (no client JWT decode), the same gate `UsagePage.tsx`'s `canEdit` check uses; this page's Replay-button visibility needs no role gate beyond page-level nav access (replay reuses the caller's own tenant/session, not a privileged action).
- **API precedent for the cursor+filter pattern (grounds the `consumes:` block below):** `apps/gateway/src/gateway/audit/api/router.py:export_audit` (FROZEN @ v1) — opaque base64url cursor keyed on `(created_at, id)` DESC, `limit` (bounded, default+max), `since`/`until` ISO-8601 inclusive bounds, `?format=json` → `{items, next_cursor, has_more}` (deliberately no `total` on a keyset page), a distinct `ERR_CURSOR_INVALID` code separate from `ERR_PAYLOAD_INVALID`, tenant-scoped, `Permission.AUDIT_READ` (owner/admin/operator; other roles → 403). This is the exact shape the sibling `logs-explorer-api` was told to mirror — grounding it here lets this UI design a `consumes:` block the API designer can reconcile against, rather than inventing an unverified shape.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_fire_record_with_raw` (`guardrail_blocked`, `blocked_by: str | None`, `pii_masked` params) + `apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py:_evaluate_pre_inner` (`blocked_by` values observed: `"prompt_injection"`, `"error"`; `ml_moderation_evaluator.py` adds `"ml_moderation"`) — the vocabulary the drawer's Guardrail Verdict panel renders; confirms `guardrail_verdict.blocked_by` is a small, enumerable string set (badge-per-value renderable), not free text.
- `.add/tasks/payload-capture-store/TASK.md` §3 (FROZEN @ v1, Ground SHA `2071046`) — the `request_logs` table this page's data ultimately reads (via the sibling `logs-explorer-api`, not directly): `id, tenant_id, key_id, team_id, model_id, status_code, stream, cached, request_body(JSONB NULL), response_body(JSONB NULL), guardrail_verdict(JSONB NULL — {blocked,blocked_by,pii_masked,patterns_hit,…}), scrub_status(TEXT: 'scrubbed'|'scrub_failed_metadata_only'|'oversize_metadata_only'), truncated(BOOL), cost_usd(NUMERIC NULL — display snapshot only), created_at`. This is the authoritative schema the `consumes:` block below is grounded on.
- `apps/dashboard/app/globals.css` — Aurora token layer: `--success`/`--success-text`, `--warning`/`--warning-foreground`, `--destructive`/`--destructive-text`, `--muted-foreground` (all pre-verified AA-safe per their inline comments) — the ONLY colors this design uses for the status/verdict badges; no new hue is introduced.

Context (working folder): `.add/milestones/logs-explorer-guardrails-v2/MILESTONE.md` — Scope item 3 (this task's exact deliverable + "signature element" framing), Shared decisions ("request log" bounded concept, scrub-before-persist invariant), Exit criterion 3 (the literal owned outcome, verbatim in the dispatch brief).

Honors (patterns / conventions):
- CONVENTIONS.md / UDD "four-state" pattern (`Loading`/`Empty`/`ErrorState`/populated rendered identically across every dashboard surface) — applied to BOTH the page-level table AND the drawer's own independent loading/error states (the drawer's detail fetch is a second, nested instance of the same pattern, not a bespoke spinner).
- v13 design-system foundation (`.add/PROJECT.md` folded lesson, UDD tokens) — every new visual value must trace to an existing Aurora token or be flagged as new; the drawer's positioning (edge-anchored vs `DialogContent`'s centered) is the one deliberate NEW pattern this task introduces, flagged explicitly below rather than silently diverging.
- `ui-restyle-recipe` folded lesson (`data-slot` marker convention, shell-owns-main a11y, `Button asChild` for nav, refute-read before gate) — the new `DrawerContent` follows the same `data-slot="drawer"` marker convention `DataTable` uses (`data-slot="data-table"`).
- PROJECT.md cross-tenant floor ("another tenant's rows are 404-invisible, never a leak") — the drawer's not-found state (R2) must read as generic "not found," never distinguish "doesn't exist" from "belongs to another tenant."
- WCAG 2.2 AA floor (persona `ui-designer.md` Default Requirement) — contrast ≥4.5:1 body / ≥3:1 large text, visible `focus-visible`, ≥44px hit targets, correct landmark/tab order — checked on every new interactive element this task adds (filter controls, table rows, pager, drawer, Replay button).

Seams consulted: none (`.add/SEAMS.md` not present in this repo as of ground time — matches the sibling `payload-capture-store` task's own finding).

Anchors the contract cites:
`AuditPage.tsx` / `AuditTable.tsx` / `AlertsTable.tsx` (table+state pattern) · `data-table.tsx:DataTable` (primitives reused, pager NOT reused) · `dialog.tsx:DialogContent` (Root/Portal/Overlay/Close reused; Content positioning extended) · `badge.tsx:Badge` variants · `states.tsx:Loading/Empty/ErrorState` · `select.tsx` / `input.tsx` · `page-header.tsx:PageHeader` · `app-shell.tsx:NAV_GROUPS` · `ChatWorkspace.tsx:ChatWorkspaceProps` (extended) · `ModelPicker.tsx:ModelPickerProps` · `bff-client.ts:bffGet` · `format.ts` · `audit/api/router.py:export_audit` (cursor/filter pattern grounding the `consumes:` block) · `payload-capture-store` TASK.md §3 `request_logs` schema (FROZEN @ v1).

Issues/Risks (→ feed §1):
1. **`DataTable`'s built-in pager is client-side/page-index, incompatible with a cursor-only server API.** `logs-explorer-api` is being designed to mirror `audit/api/router.py:export_audit`'s keyset cursor (no offset, no `total`) — `DataTable`'s `pageSizeOptions` prop assumes the full dataset is already in memory. Forcing a fit would mean fetching every page eagerly (defeats the point of cursor pagination) or faking offsets (impossible with an opaque cursor). Resolution: a new `LogsTable` component reuses `DataTable`'s underlying primitives (`Table`/`TableRow`/`TableCell`/`Empty`) directly, with its own cursor-stack pager — NOT a `DataTable` wrapper. Flagged as a deliberate non-reuse with a cited reason (persona rule).
2. **No existing mechanism hands data INTO `ChatWorkspace` from outside.** `ChatWorkspaceProps` is `{ defaultModel? }` only; there's no `initialInput`/`initialMessages` prop, and `/app/chat/page.tsx` does no searchParams/storage wiring. Replay needs SOME cross-page handoff. A URL query param (`?replay=<log_id>`) would require `ChatWorkspace` to itself fetch `GET /admin/logs/{id}` (a second, duplicate detail fetch, and a new BFF call the chat page doesn't otherwise need) OR require the drawer to serialize the full request payload into the URL (unsafe: PII-adjacent content, URL length limits). A `sessionStorage` handoff (drawer writes a small `ReplayPayload` object under a fixed key, navigates to `/app/chat`, `ChatWorkspace` reads-and-clears it once on mount) avoids both — no new BFF call, no URL-embedded payload, self-cleaning (back-navigation never re-triggers a stale replay). This is new ground (no precedent in this codebase) — proposed as the §3 shape, not assumed safe without flagging it.
3. **No existing "drawer" (edge-anchored slide-in panel) component.** Only a centered `Dialog`/`DialogContent` exists. Per persona `ui-designer.md`'s "reuse before invent" rule: the Radix `Dialog` primitives (`Root`/`Portal`/`Overlay`/`Close`, and critically its built-in focus-trap + Escape-to-close + focus-return behavior) ARE reused wholesale — only `DialogContent`'s POSITIONING (centered vs. right-edge-anchored) is new. Proposed as a sibling `DrawerContent` export added to the SAME `dialog.tsx` file (not a new dependency, not a parallel modal implementation).
4. **Multimodal (image/audio) request content cannot be replayed.** `payload-capture-store`'s scrub-before-persist only defines PII-masking for `messages[*].content` TEXT; there is no confirmed handling for image/audio message parts in the captured `request_body`. Replay must degrade gracefully (text-only, with a visible notice) rather than assume the captured body is always plain-text-reconstructable — see §1 M8.
5. **A replayed `model_id` may no longer exist in the live catalog** (deprecated/removed since the call was logged). `ModelPicker` does not itself validate against the catalog (Issue confirmed above) — the page-level replay handoff must check the live `/admin/catalog/models` list itself and fall back visibly, not silently pass a dead id into the picker.
6. **Status filter semantics are unconfirmed against the frozen schema.** `request_logs` has `status_code` (int) and `guardrail_verdict->>'blocked'` (bool) — there is no single "status" enum column. A UI "Status" dropdown (All/Success/Client Error/Server Error/Blocked) must therefore be a CLIENT-DEFINED bucket mapped to query params the API designer must also implement — genuinely a cross-task freeze reconciliation point, not something this task can unilaterally lock (flagged in Assumptions + the `consumes:` block).

Related intent: MILESTONE.md `logs-explorer-guardrails-v2` §Scope item 3 ("a console Logs Explorer page — filterable table, detail drawer, replay-into-chat-playground") + "UI/UX in scope" paragraph (Aurora + UDD loop, master-table+drawer IA, WCAG 2.2 AA floor, replay-in-playground as the signature element) + Exit criterion 3 (the literal owned outcome: "A tenant admin can browse logs in the console, open a detail drawer, and replay a logged request into the chat playground"). GLOSSARY/`request log` term (per payload-capture-store's Glossary delta: opt-in, PII-scrubbed, never billing truth). PROJECT.md folded UI-standing-bar note ("user-facing features need designed, polished UI/UX as a first-class deliverable, not bare CRUD+table" — Tin's standing preference, 2nd independent instance).

Ground SHA: `443a33a` (branch `chore/add-housekeeping-clusters`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Console Logs Explorer — filterable master table + detail drawer + replay-into-chat-playground

Framings weighed:
- **(chosen)** Master filterable table (cursor-paginated) + a slide-in detail drawer, reusing `Table`/`Empty` primitives for the table body (a new bespoke `LogsTable`, not `DataTable`'s pager) and a new `DrawerContent` extension of the existing `Dialog` primitive for the detail panel; replay via a `sessionStorage` handoff + client-side navigation to `/app/chat` that PRE-FILLS but never auto-sends.
- (rejected) Reuse `DataTable`'s built-in `pageSizeOptions` client pager — rejected because `logs-explorer-api` is cursor/keyset-paginated (mirrors `audit/api/router.py:export_audit`, no offset, no `total`); `DataTable`'s page-index model assumes a fully in-memory, offset-indexable dataset, which doesn't fit an opaque-cursor API without eagerly fetching every page.
- (rejected) A second full-page route for log detail (`/app/logs/[id]`) instead of a drawer — rejected because MILESTONE.md explicitly pins the IA ("master table + detail drawer pattern, mirrors the existing audit/alerts views … signature element = replay-in-playground affordance FROM the drawer"); a route would also drop the table's filter/scroll state on back-navigation, which a drawer preserves for free (table stays mounted underneath).
- (rejected) Auto-send the replayed request immediately on arrival at `/app/chat` — rejected for v1: replay pre-fills the composer (model + message text) but requires an explicit Send click, because (a) a captured payload may be exactly the content a guardrail already flagged/masked — blindly re-firing it risks re-triggering the same block or resending scrubbed placeholder text as if it were the original, and (b) auto-send would silently create a new billable `usage_record` the admin didn't explicitly choose in that instant. Recorded as the design's ⚠ lowest-confidence item below since "replay it" is a plausible alternate reading of the milestone wording.

Must:
<must>
  - M1. The page renders the canonical four states identically to every other dashboard surface: `Loading` (query in flight), `ErrorState` (query failed), the table's own `Empty` fallback (zero rows for the current filter), populated (filter bar + table) — no bespoke state markup introduced.
  - M2. `LogsFilterBar` exposes five controls — Time range (`From`/`To`, native `<input type="datetime-local">`), Model (`Select`, options sourced from the already-fetched `/admin/catalog/models` catalog), Key (`Select`, options sourced from the already-fetched `/admin/keys` list), Status (`Select`: All / Success / Client Error / Server Error / Blocked), Cost (`Min $`/`Max $` number `Input`s) — any control change re-queries `logs-explorer-api` and resets pagination to the first page (cursor cleared).
  - M3. `LogsTable` is cursor-paginated: "Next" advances using the response's `next_cursor`; "Previous" pops a client-held cursor stack (no server-side "previous" — mirrors the `export_audit` forward-only keyset precedent). "Previous" is disabled on the first page; "Next" is disabled/hidden when `has_more=false`.
  - M4. Clicking a row (or pressing Enter/Space on a focused row, `role="button"`/native `<tr tabIndex=0>` equivalent) opens `LogDetailDrawer` for that row's `id`, fetching `GET /admin/logs/{id}` on open. Closing the drawer (Escape, overlay click, or the close control) returns focus to the row that opened it and leaves the table's filter/scroll state untouched.
  - M5. `LogDetailDrawer` renders four sub-panels: **Overview** (model, key, status badge, cost, timestamp, stream/cached flags, **latency_ms**, **prompt/completion/total tokens** — each rendering "—" when NULL on a pre-metering row, never 0; and the **request_id** correlation key), **Request** (scrubbed request messages, monospace/collapsible), **Response** (scrubbed response content), **Guardrail Verdict** (Blocked/Clean badge + `blocked_by` reason text + a PII-masked indicator when `pii_masked=true`) — each independently handles a null value: `request_body`/`response_body: null` renders "Content unavailable — this call's payload wasn't stored (scrub failed or exceeded the size limit)" instead of a blank panel.
  - M6. The drawer's Replay action pre-fills `/app/chat`'s composer with the log's request messages (text content only) and pre-selects its model — falling back to the chat default model with a visible "Model no longer available — defaulted to {default}" notice when `model_id` isn't in the live `/admin/catalog/models` list — via a `sessionStorage` handoff object consumed exactly once on `ChatWorkspace` mount and cleared immediately after (so back-navigation never re-triggers a stale replay). It never auto-sends; the admin reviews and clicks Send.
  - M7. Replay is disabled (`aria-disabled="true"` + a visible reason, e.g. a tooltip/inline note "Nothing to replay — this call's request wasn't captured") when the fetched detail's `request_body` is `null`.
  - M8. A captured request containing non-text message content (image/audio parts the capture store cannot reconstruct) degrades replay to text-only content plus a visible "Some content couldn't be replayed" notice — never a silent partial replay presented as complete.
  - M9. Every interactive element this task adds (filter controls, table rows/sort headers, pager buttons, drawer close, Replay button) has a visible `focus-visible` ring, a ≥44px hit target, and a correct tab order (filter bar → table → pager; the open drawer traps focus per Radix `Dialog`'s built-in behavior and returns focus to the triggering row on close) — WCAG 2.2 AA floor.
  - M10. The page is reachable via a new `"Govern"`-group nav item (`{ href: "/app/logs", label: "Logs", minRole: "admin" }` in `app-shell.tsx:NAV_GROUPS`, mirroring the Alerts/Audit entries) — the nav's `minRole` is a UX convenience only; the real enforcement boundary is the gateway's read permission on `GET /admin/logs*` (owned by `logs-explorer-api`), never duplicated as client-side authorization logic here.
</must>
Reject:
<reject>
  - R1. `GET /admin/logs` returns 403 (forbidden role) -> the page renders `<ErrorState title="You don't have access to Request Logs">` (never a raw fetch-error message); this is defense-in-depth for a direct URL visit, since the nav item is already hidden for the caller's role.
  - R2. `GET /admin/logs/{id}` returns 404 (cross-tenant id, or a row already purged by the retention sweep) -> the drawer body (not the page) renders `<ErrorState title="Log not found">` scoped to the drawer, table/filter bar untouched; the message never distinguishes "doesn't exist" from "belongs to another tenant" (cross-tenant floor).
  - R3. A filter combination the API rejects as invalid (e.g. an inverted date range, a malformed cost bound) -> `ERR_PAYLOAD_INVALID` surfaces as an inline validation message beside the offending control, NOT a page-level `ErrorState`; the previously-valid result set stays displayed (a catchable input mistake never clears already-loaded rows).
  - R4. Replay attempted against a metadata-only row (`request_body: null`) -> the Replay control stays disabled; any stale/late click is a no-op (no navigation, no partial handoff written to `sessionStorage`).
  - R5. A page-N fetch (Next/Previous, or a filter re-query) times out or 5xx's -> the CURRENTLY-rendered page stays on screen with an inline `<ErrorState onRetry>` beneath the pager, never a full-page error that discards already-loaded rows.
</reject>
After:
<after>
  - A tenant admin with read access can filter/browse the request-log table, open any row's detail drawer, and see its scrubbed request/response/guardrail verdict.
  - Clicking Replay from the drawer lands the admin on `/app/chat` with the model and message text pre-filled, ready to review and send — never auto-sent.
  - A caller without read access never sees the nav item and gets a clear, non-leaking access-denied or not-found state on a direct/stale URL.
  - Every state (loading/empty/error/populated at the page level, plus the drawer's own independent loading/error/populated) is visually and semantically consistent with the already-shipped Audit/Alerts pages — no new state pattern invented.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Replay pre-fills but does NOT auto-send** — lowest confidence because MILESTONE.md's exit-criterion wording ("replay a logged request into the chat playground") is genuinely readable either way, and "replay" colloquially implies re-running, not just pre-filling. I chose pre-fill-only as the safer default (avoids surprise re-billing and blindly resending guardrail-flagged content), but this is a real product-intent call, not a technical one. If wrong: the milestone's own "signature element" ships as a one-more-click experience instead of the one-click "replay" a demo/reviewer may expect — cheap to flip later (an `autoSend` handoff flag), but changes how impressive the headline feature reads at ship review.
  - [ ] Status filter semantics: a client-defined bucket (success/client_error/server_error/blocked, derived from `status_code` ranges + `guardrail_verdict->>'blocked'`) vs. raw `status_code` exact-match — recommend the bucket (matches how an admin actually reasons about a failure), confirm with the `logs-explorer-api` designer at reconciliation since it determines the exact query param(s) `consumes:` below asks for.
  - [ ] Whether `key`/`model` DISPLAY names are resolved API-side (a join into `LogListItem`) or UI-side (cross-referencing the filter bar's own already-fetched `/admin/keys` and `/admin/catalog/models` lists, the same way `UsagePage`/`ModelPicker` already do) — recommend UI-side (zero new API surface); confirms whether `LogListItem` needs a `key_name` field at all.
  - [ ] The exact `Permission` enum name gating `GET /admin/logs*` (`Permission.LOGS_READ` net-new vs. reusing `Permission.AUDIT_READ`) is the API designer's call — this UI only needs to know the effective role tier (recommend: owner/admin/operator, mirroring `AUDIT_READ`) to keep the nav `minRole` gate consistent with the real server-side gate.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Loading state renders while the first page fetches   # M1
  Given a tenant admin navigates to /app/logs
  When the initial GET /admin/logs request is in flight
  Then a Loading indicator (role="status", aria-busy="true") is shown
  And no table, filter bar results, or drawer are rendered yet

Scenario: Populated table renders the current filtered page   # M1, M2
  Given GET /admin/logs resolves with a non-empty items array
  When the page finishes loading
  Then LogsFilterBar and LogsTable both render
  And each row shows model, key, status badge, cost, and timestamp

Scenario: Empty state for a filter combination with no matches   # M1
  Given a filter combination (e.g. a narrow time range + a specific key) matches zero rows
  When the query resolves with items: []
  Then the shared Empty state renders inside the table region ("No logs match these filters" or equivalent)
  And the filter bar remains interactive (not disabled)

Scenario: Changing a filter re-queries and resets pagination   # M2
  Given the table is showing page 2 (a non-empty cursor) of an unfiltered result set
  When the admin selects a Model in the filter bar
  Then a new GET /admin/logs request fires with the model filter applied and no cursor
  And the table returns to page 1 (Previous disabled)

Scenario: Next/Previous cursor pagination   # M3
  Given the current page's response has has_more=true and a next_cursor
  When the admin clicks Next
  Then a new request is made with cursor=<next_cursor> and the new page renders
  And clicking Previous afterward returns to the prior page without a server round trip losing state (client cursor stack)

Scenario: Next is disabled on the last page   # M3
  Given the current page's response has has_more=false
  Then the Next control is disabled (or hidden) and is not focusable as an active control

Scenario: Opening the detail drawer fetches and renders one log's full detail   # M4, M5
  Given a populated LogsTable
  When the admin activates a row (click, or Enter/Space while focused)
  Then GET /admin/logs/{id} fires for that row's id
  And the drawer opens showing Overview, Request, Response, and Guardrail Verdict panels once the fetch resolves

Scenario: Closing the drawer returns focus and preserves table state   # M4
  Given the drawer is open for a row opened from page 2 with a Model filter applied
  When the admin presses Escape (or activates the close control)
  Then the drawer closes, focus returns to the row that opened it
  And the table still shows page 2 with the same Model filter applied (unchanged)

Scenario: Metadata-only row shows an explicit unavailable-content message   # M5
  Given a log detail response with request_body=null and response_body=null (scrub_status=scrub_failed_metadata_only)
  When the drawer renders
  Then the Request and Response panels each show "Content unavailable — this call's payload wasn't stored (scrub failed or exceeded the size limit)"
  And no blank/empty panel is rendered without an explanation

Scenario: Guardrail verdict panel renders a blocked call   # M5
  Given a log detail response with guardrail_verdict={blocked:true, blocked_by:"prompt_injection", pii_masked:false}
  When the drawer renders
  Then the Guardrail Verdict panel shows a "Blocked" badge (destructive variant) and the reason "prompt_injection"
  And no PII-masked indicator is shown (pii_masked is false)

Scenario: Replay pre-fills the chat playground without sending   # M6
  Given the drawer is open for a log with a non-null request_body and a model_id present in the live catalog
  When the admin clicks Replay
  Then a ReplayPayload is written to sessionStorage and the browser navigates to /app/chat
  And /app/chat's composer shows the log's message text and the log's model pre-selected, with no message yet sent
  And the sessionStorage replay entry is cleared after ChatWorkspace consumes it once

Scenario: Replay falls back visibly when the model is no longer available   # M6
  Given a log detail with model_id "openrouter/retired-model-x" not present in the live /admin/catalog/models list
  When the admin clicks Replay and lands on /app/chat
  Then the composer's model defaults to ChatWorkspace's own default model
  And a visible notice states the original model is no longer available and the default was used instead

Scenario: Replay of multimodal content degrades to text-only with a notice   # M8
  Given a log detail whose captured request_body contains a non-text (image) message part alongside text
  When the admin clicks Replay
  Then the composer is pre-filled with the text content only
  And a visible notice states some content could not be replayed

Scenario: Replay is disabled for a metadata-only log   # M7, R4
  Given a log detail response with request_body=null
  When the drawer renders
  Then the Replay control is rendered disabled (aria-disabled="true") with a visible reason
  And clicking it (e.g. a stale re-render) performs no navigation and writes nothing to sessionStorage

Scenario: Forbidden access shows a clear, non-generic-fetch-error state   # R1
  Given a caller whose role lacks logs-read access requests /app/logs directly (nav item hidden but URL visited manually)
  When GET /admin/logs returns 403
  Then the page renders ErrorState "You don't have access to Request Logs"
  And no table or filter bar is rendered

Scenario: Not-found detail never distinguishes cross-tenant from deleted   # R2
  Given a log id belonging to another tenant, or a since-purged id
  When GET /admin/logs/{id} returns 404
  Then the drawer body renders ErrorState "Log not found" (drawer-scoped, not page-level)
  And the underlying table and filter bar are unaffected

Scenario: Invalid filter input keeps the prior valid results visible   # R3
  Given a populated table from a valid prior filter query
  When the admin enters an inverted date range (From after To) and the API returns ERR_PAYLOAD_INVALID
  Then an inline validation message appears beside the date controls
  And the table still shows the last valid result set, not cleared and not a page-level error

Scenario: A mid-pagination failure preserves the current page   # R5
  Given a rendered page of results
  When clicking Next triggers a request that times out or 5xx's
  Then the currently-rendered rows remain visible
  And an inline ErrorState with a Retry action appears beneath the pager

Scenario: Keyboard-only operation reaches every control in order   # M9
  Given a populated page with the drawer closed
  When the admin tabs from the page's top
  Then focus visits the filter controls, then the table rows/sort headers, then the pager, in that order
  And opening the drawer moves focus inside it and traps Tab there until closed

Scenario: Nav item visibility mirrors the real access gate   # M10
  Given a caller whose role is below the nav's minRole="admin" threshold
  When the app shell renders
  Then no "Logs" nav item appears in the Govern group
  And the underlying GET /admin/logs* gate (server-side permission) is what actually enforces access, not this client-side hide
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Least-sure flag surfaced at freeze: [spec] Replay pre-fills the chat composer (model + message text) but does NOT auto-send — MILESTONE.md's exit-criterion wording ("replay a logged request into the chat playground") is genuinely readable either way; pre-fill-only was chosen as the safer default (avoids surprise re-billing + blind resend of guardrail-flagged content) but this is Tin's call to confirm, not a technical default to silently lock in. If flipped to auto-send, only the `onReplay` wiring in `LogsExplorerPage.tsx`/`ChatWorkspace`'s mount-effect changes — the `ReplayPayload` shape and every other clause below is unaffected.

> Status: DRAFT — awaiting human freeze. Every clause below is proposed. This task also declares a `consumes:` block on the sibling `logs-explorer-api` (being designed in parallel, not yet frozen) — the orchestrator reconciles the two at freeze; the exact query-param names/status-bucket semantics may still shift.

### Page route

```
GET /app/logs   (new Next.js App Router page)
  nav: apps/dashboard/components/ui/app-shell.tsx NAV_GROUPS — new entry in the "Govern" group:
    { href: "/app/logs", label: "Logs", icon: <ScrollText | FileSearch, pick one lucide-react icon
      not already used in NAV_GROUPS>, minRole: "admin" }
```

### New components — `apps/dashboard/components/logs/`

```
LogsExplorerPage.tsx   — orchestrator: owns LogsFilters state + a client cursor stack (string[]),
                          wires useQuery(["admin-logs", filters, cursor]) -> bffGet<LogsPage>("/admin/logs?...")
                          per M2/M3; renders PageHeader + LogsFilterBar + LogsTable + LogDetailDrawer;
                          renders the page-level four states (M1) around the useQuery result.
LogsFilterBar.tsx       — controlled filter inputs; emits LogsFilters on change (M2); per-control
                          inline validation display for R3 (never clears the table itself).
LogsTable.tsx            — cursor-paginated list; built from ui/table.tsx's Table/TableRow/TableCell/Empty
                          DIRECTLY (not a DataTable wrapper — Issue #1); row activation -> onRowActivate(id);
                          Next/Previous pager per M3.
LogDetailDrawer.tsx      — drawer host over the new DrawerContent primitive; fetches
                          GET /admin/logs/{id} on open (M4); renders Overview/Request/Response/
                          Guardrail-Verdict sub-panels (M5) + the Replay control (M6/M7/M8).
```

New primitive — `apps/dashboard/components/ui/dialog.tsx` (EXTENDED, not a new file — reuses `DialogPrimitive.Root/Portal/Overlay/Close` wholesale per the persona's reuse-before-invent rule; only `Content` positioning is new):

```tsx
// sibling export alongside the existing DialogContent
const DrawerContent = React.forwardRef<...>(({ className, children, ...props }, ref) => (
  <DialogPortal>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      data-slot="drawer"   // mirrors DataTable's data-slot="data-table" marker convention
      className={cn(
        "fixed inset-y-0 right-0 z-50 h-full w-full max-w-md overflow-y-auto border-l " +
        "border-border bg-card p-6 text-card-foreground shadow-lg " +
        "data-[state=open]:animate-in data-[state=open]:slide-in-from-right " +
        "data-[state=closed]:animate-out data-[state=closed]:slide-out-to-right",
        className,
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <X className="size-4" /><span className="sr-only">Close</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPortal>
));
```

### Props / data-binding contract

```ts
interface LogsFilters {
  from?: string;    // ISO-8601, <input type="datetime-local"> value
  to?: string;
  modelId?: string;
  keyId?: string;
  status?: "all" | "success" | "client_error" | "server_error" | "blocked";  // bucket, Assumption #1
  costMin?: string; // decimal string, USD
  costMax?: string;
}

interface LogsFilterBarProps {
  value: LogsFilters;
  onChange: (next: LogsFilters) => void;
  models: { id: string; label: string }[];  // from the already-fetched /admin/catalog/models
  keys: { id: string; label: string }[];    // from the already-fetched /admin/keys
  fieldErrors?: Partial<Record<keyof LogsFilters, string>>;  // R3 inline validation
  disabled?: boolean;   // true while a query is in flight
}

interface LogListItem {
  id: string; created_at: string; model_id: string; key_id: string;
  status_code: number; cost_usd: string | null; stream: boolean; cached: boolean;
  blocked: boolean;      // forwarded from guardrail_verdict.blocked
  truncated: boolean;
}

interface LogsTableProps {
  items: LogListItem[];
  keysById: Record<string, string>;   // UI-side key_id -> display-name resolution (Assumption #2 default)
  onRowActivate: (id: string) => void;
  hasMore: boolean; onNext: () => void; onPrevious: () => void; canGoPrevious: boolean;
}

interface LogDetail {
  id: string; created_at: string; model_id: string; key_id: string;
  status_code: number; stream: boolean; cached: boolean; cost_usd: string | null;
  request_body: { messages: Array<{ role: string; content: unknown }> } | null;  // null = M5 unavailable state
  response_body: { content: string } | null;
  scrub_status: "scrubbed" | "scrub_failed_metadata_only" | "oversize_metadata_only";
  truncated: boolean;
  guardrail_verdict: { blocked: boolean; blocked_by: string | null; pii_masked: boolean; patterns_hit?: string[] } | null;
}

interface LogDetailDrawerProps {
  logId: string | null;              // null = closed
  onClose: () => void;
  onReplay: (detail: LogDetail) => void;  // writes sessionStorage + router.push("/app/chat")
}

// apps/dashboard/lib/logs-replay.ts (new) — the M6/Issue#2 sessionStorage handoff
const REPLAY_STORAGE_KEY = "hydroa:chat-replay";
interface ReplayPayload {
  text: string;      // flattened text content from request_body.messages (user-role turns), text-only (M8)
  modelId: string;   // the log's model_id AS CAPTURED — catalog-validity is checked by the READER
                      // (ChatWorkspace), not pre-resolved at write time, so a catalog change between
                      // the write and the read is still honored correctly
  degraded: boolean; // true if non-text content was present and dropped (M8)
}
function writeReplayPayload(p: ReplayPayload): void;      // called by onReplay before navigation
function consumeReplayPayload(): ReplayPayload | null;    // reads + immediately clears; idempotent (null after first read)

// apps/dashboard/components/chat/ChatWorkspace.tsx — ADDITIVE extension, byte-identical when
// no replay entry exists (existing bare <ChatWorkspace /> callers unaffected, ChatWorkspaceProps
// UNCHANGED: { defaultModel?: string }).
// On mount (one-time effect): consumeReplayPayload() -> if non-null, seed the composer's input
// state with .text; seed model state with .modelId IF present in the fetched models catalog,
// else defaultModel + a one-time visible "model no longer available" notice (M6); if .degraded,
// show a one-time "some content couldn't be replayed" notice (M8). No new required prop.
```

### Observable states (what the freeze approves — not pixels)
- Page: `loading` / `error(403)` / `error(generic)` / `empty(per-filter)` / `populated`
- Filter bar: `idle` / `field-error(<control>)` (R3) — never blocks other controls
- Pager: `next:enabled|disabled`, `previous:enabled|disabled`
- Drawer: `closed` / `loading` / `error(404)` / `populated` (4 sub-panels, each independently `content` or `unavailable`)
- Replay control: `enabled` / `disabled(no-content)` (M7)
- Chat handoff (on `/app/chat` arrival): `normal` / `model-fallback-notice` / `content-degraded-notice` (either/both may co-occur)

### `consumes:` — the logs-explorer-api shape this task needs (NOT frozen here; the API designer owns final shape)

```
GET /admin/logs
  auth: a read permission gated to roughly owner/admin/operator (recommend mirroring
        Permission.AUDIT_READ's tier exactly — this is a PII-bearing surface, should never be
        broader than audit) — other roles -> 403
  query:
    limit         int, 1..100, default 25            # interactive cap — smaller than audit-export's
                                                        5000 SIEM-connector cap; this is a live page
    cursor        opaque string, keyset on (created_at, id) DESC — mirrors
                  audit/api/router.py:_encode_cursor/_decode_cursor exactly (same base64url shape)
    since, until  ISO-8601, inclusive — maps to created_at range, mirrors export_audit's since/until
    model_id      string, exact match
    key_id        UUID, exact match
    status        "all"|"success"|"client_error"|"server_error"|"blocked"   # bucket (Assumption #1,
                  recommended) — OR status_code exact-match if the API designer prefers; this UI
                  adapts to whichever shape is actually frozen on the sibling contract
    cost_min, cost_max   decimal strings (USD) — maps to cost_usd range
  200 -> { items: LogListItem[], next_cursor: string | null, has_more: boolean }
        # deliberately no `total` — mirrors export_audit's own keyset-page reasoning (an
        # unfiltered COUNT over a live-appending table has no interactive-page use)
  400 -> { error: "ERR_PAYLOAD_INVALID" }   # malformed limit/since/until/cost bound, or since > until
  400 -> { error: "ERR_CURSOR_INVALID" }     # malformed cursor — distinct code, mirrors export_audit
  403 -> { error: "ERR_AUTH_FORBIDDEN" }

GET /admin/logs/{id}
  auth: same gate; tenant-scoped — a cross-tenant id returns 404, NEVER 403 (no existence leak,
        PROJECT.md cross-tenant floor)
  200 -> LogDetail   # shape above; request_body/response_body/guardrail_verdict nullable per the
                     # frozen request_logs schema (payload-capture-store TASK.md §3, FROZEN @ v1)
  404 -> { error: "ERR_LOG_NOT_FOUND" }

Note (Assumption #2, recommended default): LogListItem/LogDetail carry raw model_id/key_id only —
NO key_name/model display-name field. The UI resolves display names client-side against the filter
bar's own already-fetched /admin/keys and /admin/catalog/models lists (mirrors how UsagePage/
ModelPicker already resolve model_id -> display text). This keeps logs-explorer-api's response
shape a direct passthrough of request_logs columns, no extra join required — flagged so the API
designer can object if a join is actually preferred.
```

Glossary deltas:
- **Detail drawer**: the edge-anchored (right-side), slide-in panel pattern (`DrawerContent`) that fetches and displays one table row's full detail without navigating away from — or losing — the underlying table's filter/pagination/scroll state. Distinct from the existing `Dialog`/`DialogContent` (centered, for confirmations/forms).
- **Replay (chat-playground handoff)**: a one-way, `sessionStorage`-mediated pre-fill of `/app/chat`'s composer (model + message text) sourced from one `request_logs` row's captured detail. Never auto-sends, never writes back to the log, never round-trips through a new BFF endpoint — consumed exactly once then cleared.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes — presented for freeze 2026-07-10; reconciled against the now-FROZEN `logs-explorer-api` (v1).
Decided at freeze (Tin + orchestrator auto-mode, 2026-07-10):
(1) Replay PRE-FILLS the composer, never auto-sends — confirmed (option A). Cross-log auto-send deferred.
(2) The `logs-explorer-api` contract is now FROZEN and CONSUMES the `request-log-metering-fields` (v1)
    columns, so this UI's table + drawer Overview panel now DO show latency_ms + prompt/completion/total
    tokens (NULL on pre-metering rows → render "—", never 0), and the detail carries request_id
    (correlation). The `consumes:` block below is reconciled to that frozen envelope.
(3) Status filter = the client-defined bucket (success/client_error/server_error), matching the frozen
    logs-explorer-api dual-mode `status` param.

Least-sure flag surfaced at freeze: [spec] replay pre-fills vs auto-sends — RESOLVED at freeze: pre-fill
only (safer: avoids surprise re-billing + blind resend of guardrail-flagged content); auto-send deferred.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## Design self-score

- Completeness: 0.92 — every MILESTONE.md scope item for this task (filterable table, detail drawer with request/response/metadata/guardrail verdicts, replay-into-chat-playground, WCAG 2.2 AA) is addressed with a concrete component/prop-level plan grounded in real, cited files; held below 0.95 because the Status-filter bucket semantics and the API's exact query-param names are explicitly cross-task-pending (Assumption #1), not yet reconciled.
- Clarity: 0.93 — every component/prop is named, every reused vs. new primitive is distinguished with a cited reason (persona's reuse-before-invent rule applied concretely: `DrawerContent` reuses Radix `Dialog` internals, only `Content` positioning is new); the one genuinely product-level ambiguity (replay pre-fill vs. auto-send) is isolated as the single ⚠ flag rather than buried in a Must.
- Practicality: 0.91 — the design reuses 7 existing precedents verbatim-in-shape (four-state pattern, `DataTable`'s underlying `Table` primitives, `Badge` variants, `Select`/`Input`, `PageHeader`, `app-shell` nav registration, the `export_audit` cursor/filter query pattern) rather than inventing new mechanics; the 3 genuinely new pieces (drawer positioning, sessionStorage replay handoff, cursor-stack pager) are each grounded in why no existing precedent covers them, not assumed novel by default.
- Optimization: 0.90 — `sessionStorage` handoff chosen over a URL-param+refetch design specifically to avoid a duplicate BFF call and PII-adjacent URL payload (Issue #2 reasoning); UI-side display-name resolution (Assumption #2) chosen to avoid forcing a join onto the sibling API — both are reasoned trade-offs, not defaults.
- Edge cases: 0.91 — scenarios cover metadata-only rows, multimodal replay degradation, stale/deprecated model fallback, cross-tenant 404 non-leakage, invalid-filter-preserves-prior-results, mid-pagination failure preserving the current page, and keyboard-only operation; the one deliberately-unresolved area (exact status-bucket wiring on the API side) is flagged rather than silently assumed frozen.
- Self-evaluation: 0.90 — 3 freeze/reconciliation questions ranked with explicit recommendations + rationale, the ⚠ lowest-confidence item names its concrete cost if wrong (a less "one-click" signature feature, not a broken one) and states exactly what changes if flipped (isolated to `onReplay` wiring, no shape change) — a low-blast-radius flag by design.

All dimensions ≥ 0.90; no refinement pass required before reporting.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>

Persona (required): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; name "generic" if no project persona fits yet>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `npx vitest run tests/logs.test.tsx tests-bff/chat-workspace-page.test.tsx`: 38/38 green (2026-07-11)
- [x] coverage did not decrease — no test deleted/weakened; new suite is additive (22 logs.test.tsx scenarios + 5 chat-workspace-page.test.tsx replay-handoff cases)
- [x] no test or contract was altered during build — `git log --follow` on both spec files shows no post-freeze edits by the build agent
- [x] the green was EARNED, not gamed — adversarial refute-read performed (see verdict below); one real defect found (race condition), not a stubbed/vacuous-assert cheat
- [ ] concurrency / timing of the risky operation is safe — RESIDUE found (composer race, see Advisor lens below)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no unsafe raw-HTML-injection API used anywhere in the logs/replay surface; all payload rendering is React text nodes (auto-escaped); no tenant-override param ever sent
- [ ] layering & dependencies follow CONVENTIONS.md — RESIDUE found (status-filter enum silently narrowed from the frozen contract, undocumented as a build deviation)
- [ ] a person reviewed and approved the change — pending (this is the add-verify recommendation, not the human sign-off)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] Replay never fires a chat completion merely by landing on `/app/chat` — confirmed by `tests-bff/chat-workspace-page.test.tsx::test_replay_prefills_composer_and_model_without_sending` (asserts `chatCalled === false` and the empty-state placeholder still shows) + code inspection: no code path in `ChatWorkspace.tsx`'s replay-consume block (lines ~332-361) calls `send()`/`submit()`.
- [x] The sessionStorage replay entry is consumed exactly once, and a second mount (back-nav) does not re-apply it — confirmed by `test_replay_payload_is_consumed_exactly_once` (unmount + remount, second mount's textarea stays empty) + `lib/logs-replay.ts:consumeReplayPayload` removes the key before returning.
- [x] An unknown/deprecated `model_id` falls back to the chat default with a visible notice, never a silent substitution — confirmed by `test_replay_falls_back_to_default_model_when_replayed_model_not_in_live_catalog` (asserts `role="status"` notice text + select value falls back).
- [x] No HTML-injection sink renders attacker-influenced logged payload content — confirmed by grepping `components/logs/` + `components/chat/ChatWorkspace.tsx` + `lib/logs-replay.ts` for the raw-HTML-injection React API → zero hits; `RequestPanel`/`ResponsePanel` (`LogDetailDrawer.tsx:130-172`) render via `{JSON.stringify(...)}` / `{detail.response_body.content}` — React text nodes, auto-escaped.
- [x] Null request/response payloads render the honest "Content unavailable" message, never a blank/crash — confirmed by `test_metadata_only_row_shows_explicit_unavailable_message` + code (`LogDetailDrawer.tsx:136-149,158-169`).
- [x] The UI never sends a tenant-override param — confirmed by reading `buildLogsQuery` (`LogsExplorerPage.tsx:81-99`) and `bffGet` call sites in `LogDetailDrawer.tsx`: no `tenant_id`/tenant param constructed anywhere in the logs surface.
- [x] Cross-tenant / purged log ids read as a generic "Log not found," never distinguishing the two — confirmed by `test_not_found_detail_never_distinguishes_cross_tenant_from_deleted` + code (`LogDetailDrawer.tsx:213,249` — a single `notFound` branch keyed only on `status === 404`).
- [~] jest-axe sweep covers the drawer's populated state, not just the bare page — NOT CONFIRMED: only one `axe()` call exists in the whole suite (`tests/logs.test.tsx:210`), scoped to the page-level table only; no axe run with the drawer open. Real coverage gap, not a proven violation (Radix Dialog's own accessibility posture is well-audited upstream, which mitigates but does not close it).

### Deep checks — do not skim
- [x] WIRING (code) — every new symbol referenced: `LogsExplorerPage` wired into `app/(app)/app/logs` route + `app-shell.tsx` nav; `DrawerContent` consumed by `LogDetailDrawer`; `writeReplayPayload`/`consumeReplayPayload` each have exactly one call site (`LogsExplorerPage.tsx:228`, `ChatWorkspace.tsx:340`) — no orphaned export.
- [x] DEAD-CODE (code) — no unused symbol found in `components/logs/*.tsx` or `lib/logs-replay.ts`.
- [x] SEMANTIC — read `LogsExplorerPage.tsx`, `LogsTable.tsx`, `LogDetailDrawer.tsx`, `LogsFilterBar.tsx`, `lib/logs-replay.ts`, and the relevant `ChatWorkspace.tsx` replay-consume block (lines 290-361) in full, not skimmed.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve
- [x] Every symbol §3 CONTRACT cites still resolves in the current tree: `dialog.tsx:DrawerContent` (added, exports at line 131), `app-shell.tsx` new `/app/logs` nav entry present, `ChatWorkspace.tsx:ChatWorkspaceProps` unchanged (`{ defaultModel?: string }`, additive-only per the contract's own promise) — confirmed by direct read of each file.
- [x] One anchor DIVERGED from the frozen §3 shape, named here rather than left silent: `LogsFilters.status` / the Status `<select>` — §3 froze a 5-way enum (`"all"|"success"|"client_error"|"server_error"|"blocked"`) and the freeze decision note (§3, decided-at-freeze item 3) narrowed it to a 3-way bucket (`success/client_error/server_error`). The SHIPPED code (`LogsFilterBar.tsx:9-16,35`) implements only a 2-way bucket (`"all"|"success"|"error"`), matching the sibling `logs-explorer-api`'s actually-shipped `_STATUS_BUCKETS = ("success", "error")` (`apps/gateway/src/gateway/logs/api/logs_query_router.py:68`). The narrowing is well-reasoned (UI correctly adapts to the real API rather than a stale assumption) but was never recorded as a build deviation in this TASK.md's §5, and no §2 scenario pins the exact enum values, so nothing caught the drift structurally. M2's Must-text ("Status … All / Success / Client Error / Server Error / Blocked") is therefore not literally satisfied by the shipped UI — an admin cannot distinguish a client error (4xx) from a server error (5xx) via the filter, only "error" vs "success."

### Refute-read verdict — the earned-green check
Verdict: **EARNED** (with one confirmed non-cheat defect — see Findings)
By: self (add-verify) · adversarially checked:
1. Replay double-send / StrictMode double-invoke / back-nav re-trigger — held (ref guard + idempotent sessionStorage clear; no `send()`/`submit()` call anywhere in the replay-consume path).
2. Rendering-sink attack via logged request/response payload content — held (no raw-HTML-injection API used; all payload rendering is React-escaped text nodes).
3. Tenant-override / cross-tenant oracle via the list or detail fetch — held (no tenant param ever constructed client-side; 404 response never distinguished from a generic not-found).
4. Replay-vs-user-typing race during the async catalog-fetch window — DID NOT HOLD: reproduced with a throwaway test (delayed `/admin/catalog/models` response + `userEvent.type` during the delay) — the user's own typed composer text is silently overwritten once the catalog resolves and the render-time replay-apply block fires. Not a cheat/stub in the shipped suite (no scenario claims to cover this interleaving) — a genuine, previously-unexercised interaction. Repro test was written to `tests-bff/`, confirmed reproducing, then deleted per instructions (not committed).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self (add-verify)
1. Security: **CLEAR** — no HTML-injection sink, no tenant-override param, no secret exposure, cross-tenant 404 never distinguishes reason, replay structurally cannot auto-send.
2. Concurrency: **RESIDUE** — the composer-race finding above (Finding 1): a slow `/admin/catalog/models` fetch lets user-typed composer text be silently clobbered by a pending replay payload once the catalog resolves. Low blast radius (data entry annoyance, not data leak/billing/security), but real and reproducible.
3. Architecture: **RESIDUE** — the status-filter enum divergence above (Finding 2): shipped UI silently narrows the frozen contract's Status enum from 5-way (then 3-way per freeze decision) to the API's actual 2-way bucket, undocumented as a build deviation, untested at the enum-value level.
Verdict: **PASS** (no security HARD-STOP; both residues are non-security, low/moderate severity, and independently well-understood)
Residue: composer replay-race (Finding 1, MINOR) · status-enum contract drift (Finding 2, MINOR) · drawer-state axe-sweep gap (Finding 3, MINOR — test-coverage gap, not a proven violation) · Replay/pager hit targets measured below 44px in the Tailwind class list (Finding 4, MINOR — jsdom cannot catch this class of defect; a real CSS gap against M9's own text)
Binding: advisory — non-security, non-blocking; recommend as follow-up spec/competency deltas rather than a build-blocking re-heal

### GATE RECORD
Reported: yes — this §6 write-up is the gate report
Outcome: **PASS**
Reviewed by: add-verify (self) · date: 2026-07-11

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
