# TASK: Agents console (UDD): directory, session explorer, MCP allow-list management, kill switch

slug: agents-console · created: 2026-07-14 · stage: production
milestone: agent-gateway-v1
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/ui/app-shell.tsx:NAV_GROUPS` (+ `NAV_ITEMS`) — the 5-workflow-group
  primary nav array; a new `{ href: "/app/agents", label: "Agents", icon: Bot, minRole: "admin" }`
  entry joins the existing "Govern" group (alongside Teams/Members/Alerts/Audit/Logs/Health/SLO/
  Guardrail Analytics) — reuse-before-invent over inventing a 6th nav GROUP.
- `apps/dashboard/components/logs/LogsExplorerPage.tsx` / `LogsFilterBar.tsx` / `LogsTable.tsx` /
  `LogDetailDrawer.tsx` — the FROZEN (logs-explorer-ui/api) master-table + slide-in-drawer idiom
  this task's Sessions tab reuses VERBATIM against `GET /admin/logs` / `GET /admin/logs/{id}`;
  `LogsExplorerPage`'s `keysById` client-side join pattern (fetch `GET /admin/keys` once, map
  `key_id -> name`) is the SAME pattern this task reuses for owner-name resolution against
  `GET /admin/users` (confirmed live at `components/members/MembersPage.tsx:83`).
- `apps/dashboard/components/settings/RetentionZdrSettings.tsx` (+
  `apps/dashboard/components/teams/ConfirmDialog.tsx`) — confirmed by reading the actual dialog
  usage (not just the milestone's naming) that the "ZDR typed-confirm idiom" IS `ConfirmDialog`
  (a plain confirm/cancel modal) given a plain-language, irreversibility-naming `description`
  string — there is no separate "type the resource name to confirm" text-input component
  anywhere in this codebase. This task's kill-switch confirm reuses `ConfirmDialog` unchanged.
- `apps/dashboard/components/ui/badge.tsx:badgeVariants` (`default|secondary|outline|success|
  warning|destructive`) + `apps/dashboard/components/invoices/InvoiceStatusSeal.tsx` — the
  Badge+icon+sr-only idiom for a binary document/lifecycle state (issued/draft) this task mirrors
  for live/killed (never color-only — WCAG 1.4.1).
- `apps/dashboard/components/ui/card.tsx` (`Card/CardHeader/CardTitle/CardContent/CardFooter`,
  `variant: "default"|"soft"|"flat"`) + `apps/dashboard/components/ui/stat-card.tsx` and the
  `grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3` (or `lg:grid-cols-4`) responsive-grid
  precedent repeated across `PlatformPlanCatalog.tsx`, `GuardrailAnalyticsPage.tsx`,
  `SpendPage.tsx`, `SloPage.tsx` — the grid this task's Directory tab reuses for the identity-card
  layout; no new breakpoint scheme.
- `apps/dashboard/components/ui/region-badge.tsx` + `apps/dashboard/components/keys/
  TierSelector.tsx` — both explicitly document the "design a shared visual once, reuse everywhere"
  rule (`RegionBadge`'s own header comment; `TierSelector`'s radio styling "match the residency
  picker in RetentionZdrSettings — ONE shared visual treatment") — this task's Policy tab reuses
  that SAME native-radio "pick one of a few" visual for tenant-vs-per-key inherit/custom, and that
  same "design once" rule for the new `McpAllowListEditor` (mounted twice: tenant scope, key scope).
- `apps/dashboard/components/ui/page-header.tsx:PageHeader` (`eyebrow`/`meta`/`actions`) — page
  chrome, unchanged.
- `apps/dashboard/lib/bff-client.ts:bffGet/bffPost/bffPut/bffDelete/bffPatch` +
  `apps/dashboard/app/api/gw/[...path]` — the ONE BFF pass-through every dashboard page already
  goes through; confirms "no server sk- token in the browser" is true BY CONSTRUCTION for this
  task (unchanged infra, zero new code needed to satisfy it).
- FROZEN parent contracts consumed (read-only; NOT redesigned here):
  `.add/tasks/mcp-connector-passthrough/TASK.md` §3 (FROZEN @ v2) — `PUT/GET /admin/mcp-servers`,
  `PUT/GET/DELETE /admin/keys/{key_id}/mcp-servers`, and the MCP session-trace convention
  (`request_logs.model_id = f"mcp::{server_host}::{tool_name}"`, written via the SAME
  `SqlAlchemyPayloadCapture` seam logs-explorer-api already exposes read-side).
  `.add/tasks/agent-identity-governance/TASK.md` §3 (FROZEN @ v1) — `POST/GET /admin/agents`,
  `POST /admin/agents/{id}/tokens/{token_id}/attach`, `DELETE /admin/agents/{id}/tokens/
  {token_id}`, `POST /admin/agents/{id}/kill`.
  `.add/tasks/tool-call-metering/TASK.md` §3 (FROZEN @ v1) — no new HTTP surface; confirms billed
  tool-call cost lands on `usage_records`/invoice lines grouped by `(mcp_server, mcp_tool)` tags,
  a DIFFERENT row than the `request_logs` trace this console's Sessions tab reads — the two are
  not the same number and this page must not imply they are.

Context (working folder):
- `.add/milestones/agent-gateway-v1/MILESTONE.md` — owning milestone; its UI/UX-in-scope
  paragraph pins the IA (directory → session explorer → policy), the Logs-Explorer-drawer +
  ZDR-typed-confirm idioms, Aurora tokens, and the signature element (principal/owner/spend/
  last-seen/live-killed identity card) verbatim.
- `/Users/tindang/workspaces/tind-repo/ai-proxy/tmp/r1-design-context.md` — shared wave-1 design
  rules (draft-only, no `add.py` state mutation, security tasks get dual verify — n/a to this UI
  task itself, but its 3 parent contracts carry that requirement).
- `apps/dashboard/tests/` — existing Vitest+RTL test file naming convention (`<feature>-<aspect>.
  test.tsx`, e.g. `billing-nav.test.tsx`) this task's own tests (§4, filled at the Tests phase,
  not this bundle) will follow.

Honors (patterns / conventions):
- Reuse-before-invent (ui-designer persona, Critical Rule 1): every visual/interaction element
  above is either reused unmodified or explicitly named as new with a cited reason (`DESIGN.md
  §3`); the ONLY genuinely new components are `AgentIdentityCard`, `CreateAgentDialog`,
  `ManageTokensDialog`, `McpSessionsFilterBar`, `McpAllowListEditor`, and the orchestrator
  `AgentsConsolePage`.
- Fail-closed MCP default (MILESTONE.md shared decision) must be STATED in visible page copy
  (scope_hints), not merely enforced server-side — this task's Policy tab carries the verbatim
  copy "Unlisted servers are refused, not warned…".
- WCAG 2.2 AA is the floor (ui-designer Default Requirement): contrast, `focus-visible`,
  ≥44px hit targets, correct landmark order, redundant (non-color-only) state cues — checked on
  every new element this task introduces, not deferred.
- BFF pass-through only (MILESTONE.md UI shared decision) — see Touches above; true by
  construction, verified not assumed.

Seams consulted: none in `.add/SEAMS.md` name a dashboard-nav-group or admin-CRUD-list-editor
seam yet (checked — file does not have an entry for either).

Anchors the contract cites:
- `app-shell.tsx: NAV_GROUPS, NAV_ITEMS`
- `logs/LogsTable.tsx, LogDetailDrawer.tsx, LogsFilterBar.tsx` (reused verbatim + one new field)
- `teams/ConfirmDialog.tsx`
- `ui/badge.tsx: badgeVariants` · `invoices/InvoiceStatusSeal.tsx` (idiom precedent)
- `ui/card.tsx: Card, CardHeader, CardTitle, CardContent, CardFooter`
- `ui/page-header.tsx: PageHeader`
- `lib/bff-client.ts: bffGet, bffPost, bffPut, bffDelete`
- FROZEN: `mcp-connector-passthrough §3 v2` (`/admin/mcp-servers`, `/admin/keys/{key_id}/
  mcp-servers`), `agent-identity-governance §3 v1` (`/admin/agents*`), `tool-call-metering §3 v1`
  (invoice-line grouping, no HTTP surface of its own)

Issues/Risks (→ feed §1):
1. **Spend field does not exist on the frozen read API.** MILESTONE.md's signature-element
   wording and this task's own dispatch brief both name "spend" as a directory-card field
   ("from usage_records"); `agent-identity-governance`'s FROZEN `GET /admin/agents` response
   carries `monthly_budget_usd` (the CAP) but NO current-month spend figure. The only spend
   tracking that exists (`GovernanceService._check_agent_principal_budget`'s Redis counter
   `usage:spend:agent_principal:{id}:{YYYYMM}`) is write-side internal state, never read-exposed.
   This is a real cross-task gap, not inventable here (out of scope to design a new BE read
   surface) and not safe to silently paper over with a fabricated `$0`. See `DESIGN.md §5`.
2. **No token-enumeration read API.** The frozen `GET /admin/agents` response carries
   `attached_token_count` (a number) but never the attached token/key IDs themselves, and no
   `agent_oauth` admin router lists tokens at all (confirmed: `apps/gateway/src/gateway/
   agent_oauth/api/` has only `device_approval_router.py`/`device_authorize_router.py`/
   `token_router.py` — the v39 mint/approve/deny flow, no list endpoint). This console therefore
   cannot offer a token PICKER for attach/detach or "show me this agent's own sessions" — both
   degrade to an explicit-id form / a server::tool text filter respectively (disclosed, not
   hidden).
3. **`GET /admin/logs`'s `model_id` filter is EXACT-match only** (confirmed by reading
   `logs/infrastructure/logs_repository.py`: `RequestLogRow.model_id == model_id`, no prefix/LIKE
   support) — an MCP trace row's `model_id` is the literal string `mcp::{server_host}::
  {tool_name}`; there is no way to ask the frozen endpoint for "every MCP row" precisely across
   pages. This task's Sessions tab must disclose a page-scoped client-side filter as an honest v1
   degradation, not imply a complete result set.
4. **Trace cost ≠ billed cost.** A session-trace `request_logs` row's `cost_usd` is populated by
   the SAME capture seam every OTHER logged call uses, independent of `tool-call-metering`'s
   SEPARATE `usage_records` row for the SAME call — the two numbers can legitimately differ (one
   is trace metadata, the other is the billed line). The drawer must not present the trace row's
   `cost_usd` as "what this call cost," to avoid contradicting the tenant's actual invoice line.

Related intent: MILESTONE.md `agent-gateway-v1` §Scope ("Agents console (UDD)") and its UI/UX
paragraph (IA = directory → session explorer → policy; Logs-Explorer-drawer + ZDR-typed-confirm
idioms; Aurora tokens; signature element = the agent identity card); Exit criterion "A tenant
admin can see, trace, govern, and kill agents from the Agents console, axe-clean (WCAG 2.2 AA)."
Roadmap `docs/roadmap/2026-07-14-enterprise-roadmap.html` R1 M3 (agent-era gateway). Tin's
standing UI/UX polish bar (`ui-ux-polish-standing-bar` memory) — a designed, polished surface,
never bare CRUD+table.

Ground SHA: `383f6e8` (branch `feat/agent-gateway-r1`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Agents console (UDD) — a single `/app/agents` page (Directory / Sessions / Policy tabs)
consuming three FROZEN read/admin APIs to let a tenant admin see, trace, govern, and kill agents.
Framings weighed:
  **One page, three controlled tabs** (chosen) — mirrors the already-shipped Overview/Breakdown
  tab idiom (`SpendPage`/`GuardrailAnalyticsPage`); each tab keeps its own filter/scroll state,
  no route-level remount, no new IA pattern. · **Three separate routes** (`/app/agents`,
  `/app/agents/sessions`, `/app/agents/policy`) (rejected — MILESTONE.md's own wording reads as
  one page with an internal directory→sessions→policy flow, not three nav entries; a route split
  would also lose in-page state on navigation between them for no benefit). · **A 6th top-level
  nav GROUP dedicated to "Agents"** (rejected for v1 — the existing "Govern" group already houses
  every other tenant-governance surface (Teams/Members/Alerts/Audit/Logs/Health/SLO/Guardrail
  Analytics); adding this task's one nav item there is the reuse-before-invent call, flagged ⚠
  below since it is a genuine, not purely mechanical, IA judgment).

Must:
<must>
  - M1 A new `/app/agents` page is reachable via a `{ href: "/app/agents", label: "Agents", icon:
    Bot, minRole: "admin" }` entry in `app-shell.tsx:NAV_GROUPS`'s "Govern" group; the page hosts
    three controlled Tabs — Directory (default), Sessions, Policy — sharing one `PageHeader`.
  - M2 Directory renders one `AgentIdentityCard` per row of `GET /admin/agents` in a
    `grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3` layout. Each card shows: principal
    name, a resolved owner display name (UI-side join of `owner_user_id` against `GET
    /admin/users`, falling back to the raw id when unresolved, or "No owner set" when null —
    never blank), `monthly_budget_usd` as "Cap: $X/mo" or "No cap set", last-seen
    (`formatTimestamp(last_seen_at)` or "Never authenticated"), `attached_token_count`, and a
    live/killed state Badge (`success`+icon "Live" / `destructive`+icon "Killed" — never
    color-only, WCAG 1.4.1).
  - M3 A "New agent" action opens `CreateAgentDialog` (name required; owner via a `<select>`
    sourced from `GET /admin/users`; optional `monthly_budget_usd`/`rpm_limit`/`tpm_limit`
    numeric inputs) that POSTs `/admin/agents`; a 409/422 response renders an inline field error
    beside the offending input (never closes the dialog, never discards the user's other input).
  - M4 A live card's "Kill" action opens `ConfirmDialog` (reused verbatim) with a plain-language,
    irreversibility-naming `description` (the "ZDR typed-confirm idiom") before `POST
    /admin/agents/{id}/kill` fires; on success the card immediately shows "Killed «timestamp»"
    and the Kill action disappears entirely (never a disabled-but-visible button — a killed
    principal cannot be revived per the frozen contract).
  - M5 `ManageTokensDialog` (opened from any card, live or killed) accepts a pasted token id and
    an Attach or Detach action, calling `POST .../tokens/{token_id}/attach` or `DELETE
    .../tokens/{token_id}` — an explicit-id form, not a picker, because the frozen contract
    exposes no token-enumeration read API (Ground Issue 2).
  - M6 Sessions reuses `LogsTable`/`LogDetailDrawer` verbatim against `GET /admin/logs`/`GET
    /admin/logs/{id}`. Its filter bar (`McpSessionsFilterBar`) offers every field
    `LogsFilterBar` already offers (from/to/key/status/cost) plus one new exact-match "server::
    tool" text field mapped 1:1 onto the existing `model_id` query param (values shaped
    `mcp::{server_host}::{tool_name}`). When that field is empty, the fetched page's rows are
    ADDITIONALLY filtered client-side to `model_id.startsWith("mcp::")` before rendering — a
    disclosed, page-scoped approximation (Ground Issue 3), never presented as a complete result
    set.
  - M7 A visible banner is shown on Sessions whenever the exact server::tool field is empty,
    stating the current page's rows are being narrowed client-side and that a broader,
    server-side filter is a tracked change request (not a silent gap).
  - M8 Opening a Sessions row reuses `LogDetailDrawer` UNCHANGED — its existing Guardrail Verdict
    panel already surfaces `mcp-connector-passthrough`'s M9 block/audit outcome with zero new
    drawer code. The drawer additionally renders one static note: the row's own `cost_usd`
    reflects trace metadata only; the billed amount for that tool call is a separate
    `usage_records`/invoice line grouped by `(mcp_server, mcp_tool)` (Ground Issue 4) — visible on
    the existing Invoices pages, not duplicated here.
  - M9 Policy renders two mount points of ONE new `McpAllowListEditor` component (list of
    `{url,label}` rows, add/remove/Save): the tenant-wide list (`GET`/`PUT /admin/mcp-servers`,
    PUT visible/enabled only for an OWNER) and a per-key override (`GET`/`PUT`/`DELETE
    /admin/keys/{key_id}/mcp-servers`, visible/enabled for owner/admin) selected via a key
    `<select>` sourced from the SAME `GET /admin/keys` list `LogsExplorerPage` already fetches.
  - M10 The tenant allow-list section carries the verbatim, always-visible copy: "Unlisted
    servers are refused, not warned — an agent can only reach a server on this list." The
    per-key section's inherit/custom choice uses the SAME native-radio visual as
    `RESIDENCY_REGION_LABELS`/`TierSelector`, and an explicitly-empty custom list carries the
    verbatim copy: "An empty list blocks this key from every MCP server — this is different from
    inheriting."
  - M11 Every Policy write's 403/404/409/422 response renders as an inline error beside the
    offending row/control (mirrors `LogsFilterBar`'s `fieldErrors` idiom) — the previously-saved
    list stays displayed unchanged until a write actually succeeds; nothing is optimistically
    cleared on a rejected save.
  - M12 Every request this page makes goes through the existing `bffGet/bffPost/bffPut/
    bffDelete` BFF client (`/api/gw/[...path]`) — no raw gateway credential ever reaches the
    browser; true by construction, verified against `lib/bff-client.ts`, not new code.
  - M13 Every interactive element this task adds (card actions, tab triggers, dialog controls,
    table rows, allow-list add/remove rows) has a visible `focus-visible` ring, a ≥44px hit
    target, correct tab order, and no color-only state cue — WCAG 2.2 AA floor, axe-clean.
</must>
Reject:
<reject>
  - R1 `GET /admin/agents` returns 403 (stale/direct URL below the page's role gate) ->
    `<ErrorState title="You don't have access to Agents">`; defense-in-depth, nav item already
    hidden.
  - R2 `POST /admin/agents` -> 409 `agent_principal_name_conflict` -> inline field error beside
    the Name input in `CreateAgentDialog`; dialog stays open, other fields preserved.
  - R3 `POST /admin/agents` -> 422 `invalid_request` (negative/non-numeric budget or limit) ->
    inline field error beside the offending numeric input.
  - R4 `POST .../tokens/{token_id}/attach` -> 404 `agent_token_not_found` /
    `agent_principal_not_found`, or 409 `agent_token_already_attached` /
    `agent_principal_killed` -> inline error inside `ManageTokensDialog`, dialog stays open.
  - R5 `POST .../kill` on a card another admin already killed (stale card) -> the confirm
    dialog's own error path surfaces the API's response, and the card re-syncs from the next
    React Query refetch rather than the client guessing a new state.
  - R6 `PUT /admin/mcp-servers` -> 422 `ERR_MCP_SERVER_URL_INVALID` /
    `ERR_MCP_SERVER_LIST_TOO_LONG` -> inline error beside the offending row / beneath the list;
    the tenant's last-saved list stays displayed, never optimistically cleared.
  - R7 `PUT`/`DELETE /admin/keys/{key_id}/mcp-servers` -> 404 `ERR_KEY_NOT_FOUND` -> the key
    dropdown refreshes and an inline "This key no longer exists" note replaces that editor mount
    point.
  - R8 `GET /admin/logs/{id}` (Sessions drawer) -> 404 -> the drawer body alone renders
    `<ErrorState title="Session not found">`; the table underneath is untouched (mirrors
    logs-explorer-ui's own R2).
  - R9 A Sessions filter combination `GET /admin/logs` rejects (e.g. inverted date range) ->
    inline validation beside the offending control; previously-loaded rows stay displayed.
</reject>
After:
<after>
  - A tenant admin sees every named agent in one glance, including which are dead, without
    leaving the page.
  - A tenant admin can trace what an agent's MCP tool calls actually did down to the same
    guardrail-verdict/PII-scrub detail the Logs Explorer already gives for chat calls.
  - A tenant OWNER can see and change exactly which MCP servers agents may reach, at both tenant
    and per-key granularity, with the fail-closed default stated in plain language before any
    write.
  - A tenant admin can kill a named agent in one guarded, irreversible action, with the
    consequence stated before it fires.
  - No part of this page ever talks to the gateway with anything but the existing BFF
    pass-through.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `GET /admin/agents`'s FROZEN response (agent-identity-governance §3 v1) has NO current-month
  spend field, only `monthly_budget_usd` (the cap) — yet MILESTONE.md's own signature-element
  wording and this task's dispatch brief both name "spend" as a directory-card field. Lowest
  confidence because this is the single most visibly "missing" piece of what was promised, and it
  is not something this task can invent (no new BE read surface is in scope here). Proposed
  resolution: (a) file a change request against `agent-identity-governance` to add
  `spent_usd_month: string | null` to `GET /admin/agents` (an additive read of the SAME Redis
  counter `_check_agent_principal_budget` already maintains), and (b) ship v1 rendering the field
  optionally — "Spend: not available yet" (never a fabricated `$0`) until it lands. If wrong (Tin
  wants this to block the freeze instead): the signature element ships visibly incomplete against
  the milestone's own promise until the CR lands — cheap to add once the field exists (one Card
  row), but real enough to decide now, not discover at build.
  - [ ] No token-enumeration read API exists (Ground Issue 2) — `ManageTokensDialog`'s
    paste-a-token-id form is the only viable v1 UX; confirm this is acceptable, or whether a
    companion change request (`GET /admin/agents/{id}/tokens`, or embedding the id array in the
    existing list response) should land alongside this task.
  - [ ] `GET /admin/logs`'s exact-match-only `model_id` filter (Ground Issue 3) means Sessions
    cannot show "every MCP session" precisely across pages without a change request adding an
    optional `model_prefix` param to logs-explorer-api; confirm whether that CR is worth opening
    now versus shipping the disclosed page-scoped v1 degradation (M6/M7).
  - [ ] Nav placement inside the existing "Govern" group rather than a new dedicated nav GROUP —
    MILESTONE.md's IA wording ("new top-level Agents nav page") is readable either way; I read it
    as "a new page reachable from top nav," not "a new nav section," per reuse-before-invent.
    Confirm this reading at freeze; flipping it is a one-line `app-shell.tsx` change with no
    effect on any other clause in this bundle.
  - [ ] `ManageTokensDialog` assumes an admin has some OTHER way to learn a token's id today
    (e.g. copied at mint time via the v39 device-approval flow); no existing dashboard surface
    lists agent/device-OAuth tokens by id (the Keys page lists `sk-` keys only, a different
    table). Confirm this interim UX is acceptable or whether `agent_oauth`'s own read surface
    needs a companion change request.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Agents nav item appears for an admin, hidden from a member   # M1
  Given an authenticated ADMIN identity
  When the sidebar renders
  Then a "Agents" entry with a Bot icon appears in the "Govern" group, linking to /app/agents
  And the SAME sidebar rendered for a MEMBER identity does not show that entry

Scenario: Directory tab is the default tab   # M1
  Given an authenticated admin navigates to /app/agents
  When the page mounts
  Then the Directory tab is active and its content is rendered
  And the Sessions and Policy tabs are reachable via TabsTrigger without a page reload

Scenario: Directory renders one identity card per agent, owner resolved   # M2
  Given GET /admin/agents returns 2 principals, one with owner_user_id set to a known user
  And GET /admin/users returns that user's display name
  When the Directory tab renders
  Then each principal renders as its own AgentIdentityCard in the responsive grid
  And the card with a resolved owner shows that user's display name, not the raw UUID

Scenario: Unresolved or absent owner never renders blank   # M2 edge case
  Given a principal has owner_user_id set to an id absent from the fetched GET /admin/users list
  And a second principal has owner_user_id null
  When their cards render
  Then the first card shows the raw owner_user_id (never a blank field)
  And the second card shows "No owner set"

Scenario: Spend renders as an honest degrade, never a fabricated zero   # M2, ⚠ Ground Issue 1
  Given GET /admin/agents's response carries no spend field (current frozen shape)
  When a card renders
  Then the spend row shows "Spend: not available yet"
  And it never shows "$0.00" or any computed figure

Scenario: Live vs killed state is never color-only   # M2
  Given one principal has killed_at null and another has killed_at set
  When their cards render
  Then the live card's badge shows a success-tinted "Live" label with an icon
  And the killed card's badge shows a destructive-tinted "Killed" label with a different icon
  And both convey state through icon + text, not tint alone

Scenario: Admin creates a named agent   # M3
  Given an authenticated ADMIN identity
  When they open "New agent", enter name "billing-bot", and submit
  Then POST /admin/agents fires with { name: "billing-bot" }
  And on 200 the new card appears in the Directory grid without a full page reload

Scenario: Duplicate name surfaces inline, dialog stays open   # M3, R2
  Given a tenant already has a principal named "billing-bot"
  When an admin submits "New agent" with name "billing-bot" again
  Then POST /admin/agents returns 409 agent_principal_name_conflict
  And an inline error appears beside the Name input
  And the dialog remains open with the admin's other entered fields preserved

Scenario: Invalid budget surfaces inline, dialog stays open   # M3, R3
  Given an authenticated ADMIN identity
  When they submit "New agent" with monthly_budget_usd = "-5.00"
  Then POST /admin/agents returns 422 invalid_request
  And an inline error appears beside the budget input
  And no card is added to the grid

Scenario: Owner kills a live agent through the typed-confirm idiom   # M4
  Given a live agent principal card
  When the OWNER clicks Kill
  Then ConfirmDialog opens with a plain-language, irreversibility-naming description
  And confirming fires POST /admin/agents/{id}/kill
  And on 200 the card immediately shows "Killed «timestamp»" with the Kill action removed

Scenario: Killed card never shows a disabled Kill button   # M4 edge case
  Given a principal already killed
  When its card renders
  Then no Kill button (enabled or disabled) is present
  And a static "Killed «timestamp»" line is shown instead

Scenario: Admin attaches a token by pasting its id   # M5
  Given a live agent principal and a known, unattached agent-token id
  When an admin opens "Manage tokens", pastes that id, and clicks Attach
  Then POST /admin/agents/{id}/tokens/{token_id}/attach fires
  And on 200 the dialog confirms the attachment and the card's attached-token count increments

Scenario: Attach to an unknown or already-attached token surfaces inline   # M5, R4
  Given a token id that either does not exist or is already attached to a different principal
  When an admin attempts to attach it via "Manage tokens"
  Then the 404 agent_token_not_found or 409 agent_token_already_attached response renders inline
    inside the dialog
  And the dialog stays open, no card's attached-token count changes

Scenario: Sessions reuses the Logs Explorer drawer verbatim   # M6, M8
  Given a session-trace row with model_id="mcp::mcp.acme.example::search"
  When an admin clicks that row in the Sessions tab
  Then LogDetailDrawer opens exactly as it does for a chat log row (same GET /admin/logs/{id})
  And its Overview/Request/Response/Guardrail-Verdict panels render unchanged
  And an added note states the row's cost_usd is trace metadata only, not the billed amount

Scenario: Exact server::tool filter narrows to precisely one MCP call shape   # M6
  Given multiple MCP session rows exist across several servers/tools
  When an admin enters "mcp::mcp.acme.example::search" in the server::tool field and searches
  Then GET /admin/logs is queried with model_id="mcp::mcp.acme.example::search" (exact match)
  And only rows with that literal model_id are returned by the API

Scenario: Empty server::tool filter degrades honestly, page-scoped   # M6, M7, ⚠ Ground Issue 3
  Given the server::tool field is left empty and the fetched page contains both chat and MCP rows
  When the Sessions tab renders that page
  Then only rows whose model_id starts with "mcp::" are shown (client-side filter)
  And a visible banner discloses that only the current page was narrowed, with a broader
    server-side filter tracked as a change request
  And the underlying GET /admin/logs query itself is unchanged (from/to/key/status/cost only)

Scenario: A blocked tool-call result is visible in the drawer with zero new code   # M8
  Given a session row whose guardrail_verdict.blocked is true
  When the drawer opens for that row
  Then the existing Guardrail Verdict panel shows the Blocked badge and blocked_by reason
  And no MCP-specific rendering branch was needed to show it

Scenario: Owner sets the tenant MCP allow-list   # M9, M10
  Given an authenticated OWNER identity on the Policy tab
  When they add a server row and click Save
  Then PUT /admin/mcp-servers fires with the full replace-wholesale list
  And on 200 the editor reflects the saved list and updated_at
  And the fail-closed copy ("Unlisted servers are refused, not warned…") is visible above it

Scenario: Non-owner cannot edit the tenant allow-list   # M9, R1-style forbidden UX
  Given an authenticated ADMIN (non-owner) identity on the Policy tab
  When they attempt to edit the tenant allow-list section
  Then the Save control is disabled/absent for that section (PUT is OWNER-only per the frozen
    contract) and the section is presented read-only, not hidden entirely (GET is any role)

Scenario: Owner/admin sets a per-key override   # M9, M10
  Given a selected key with no existing override
  When an owner/admin selects "Custom list for this key", adds a server, and clicks Save
  Then PUT /admin/keys/{key_id}/mcp-servers fires with the explicit list
  And on 200 the editor shows source="key"

Scenario: Explicitly empty per-key override is visually distinguished from inherit   # M10
  Given an owner/admin selects "Custom list for this key" and saves with zero rows
  When the save succeeds
  Then the editor shows the "empty list blocks this key from every MCP server" copy
  And this state is visually distinct from selecting "Inherit tenant list"

Scenario: Invalid server URL surfaces inline, prior list untouched   # M11, R6
  Given an owner is editing the tenant allow-list
  When they add "http://mcp.acme.example/v1" (non-https) and click Save
  Then PUT /admin/mcp-servers returns 422 ERR_MCP_SERVER_URL_INVALID
  And an inline error appears beside that row
  And the previously-saved tenant list remains displayed unchanged (not optimistically cleared)

Scenario: Key deleted concurrently surfaces inline, editor replaced   # M11, R7
  Given an owner/admin is editing a per-key override
  And that key is revoked by a separate request before Save completes
  When Save is submitted
  Then PUT/DELETE .../mcp-servers returns 404 ERR_KEY_NOT_FOUND
  And the key dropdown refreshes and an inline "This key no longer exists" note replaces the editor

Scenario: Direct URL visit below role gate shows a clear, non-leaking error   # R1
  Given an authenticated identity whose role causes GET /admin/agents to 403
  When they visit /app/agents directly
  Then <ErrorState title="You don't have access to Agents"> renders
  And no partial agent data is shown

Scenario: A killed principal's own re-kill click races another admin   # M4, R5, concurrency edge case
  Given a card whose principal was just killed by a different admin in another tab
  When this admin's own already-in-flight Kill confirm resolves
  Then the response is handled by the confirm dialog's normal error/success path
  And the card converges to the correct killed state on the next background refetch, never a
    stale "Live" render

Scenario: Sessions drawer 404 is scoped to the drawer, table untouched   # R8
  Given a session row whose log was purged by the retention sweep between list and click
  When the drawer opens for that row's id
  Then GET /admin/logs/{id} returns 404 and the drawer body alone renders "Session not found"
  And the Sessions table underneath keeps its previously-loaded rows and filter state

Scenario: Invalid Sessions filter combination is a field error, not a page error   # R9
  Given an admin sets From after To in the Sessions filter bar
  When they attempt to apply the filter
  Then an inline validation message appears beside the To control
  And the previously-loaded session rows remain displayed, unqueried

Scenario: Every new interactive element meets the WCAG 2.2 AA floor   # M13
  Given the Directory, Sessions, and Policy tabs are each rendered
  When an automated accessibility pass (axe) runs against each
  Then zero violations are reported for contrast, focus-visible, hit-target size, and landmark
    order across every element this task introduces

Scenario: No raw gateway credential ever reaches the browser   # M12
  Given any action on this page (list, create, kill, attach, allow-list write)
  When the network request is inspected
  Then every call targets /api/gw/... (the BFF catch-all), never the gateway origin directly
  And no sk- or agent-token value appears in any client-side request header or body
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

### Page route

```
GET /app/agents   (new Next.js App Router page, apps/dashboard/app/(app)/app/agents/page.tsx)
  nav: apps/dashboard/components/ui/app-shell.tsx NAV_GROUPS — new entry in the "Govern" group:
    { href: "/app/agents", label: "Agents", icon: Bot, minRole: "admin" }
```

### New components — `apps/dashboard/components/agents/`

```
AgentsConsolePage.tsx     — orchestrator: PageHeader + controlled Tabs (Directory default /
                             Sessions / Policy); each tab owns its own query state.
AgentIdentityCard.tsx     — the signature element (M2): principal, resolved owner, cap,
                             spend_usd_this_month (real, "0.00" floor), last-seen, attached count,
                             live/killed Badge, Kill (ConfirmDialog) + "Manage tokens" actions.
CreateAgentDialog.tsx     — name/owner/budget/rpm/tpm form -> POST /admin/agents (M3).
ManageTokensDialog.tsx    — token picker (M5): lists GET /admin/agents/{id}/tokens
                             (AgentTokenInfo[], CR-B RESOLVED), each row attach/detach.
McpSessionsFilterBar.tsx  — LogsFilterBar's fields + one exact server::tool field (M6).
McpAllowListEditor.tsx    — {url,label} row list, add/remove/Save; mounted twice (tenant + key
                             scope) by McpPolicyPanel.tsx (M9).
McpPolicyPanel.tsx        — hosts the two McpAllowListEditor mounts + the key <select> + the
                             inherit/custom radio (M9/M10).
McpSessionsPanel.tsx      — wires McpSessionsFilterBar + the REUSED LogsTable/LogDetailDrawer
                             against GET /admin/logs / GET /admin/logs/{id} (M6-M8).
```
No new primitive is added to `components/ui/` — `Tabs`, `Card`, `Badge`, `Dialog`,
`ConfirmDialog`, `DrawerContent` (via the reused `LogDetailDrawer`), `Input`, `Button`, `Table`,
`PageHeader`, `Loading`, `ErrorState` are all consumed unmodified.

### Props / data-binding contract

```ts
interface AgentPrincipal {   // GET /admin/agents item shape, per FROZEN agent-identity-governance §3 v2
  id: string; tenant_id: string; name: string; owner_user_id: string | null;
  monthly_budget_usd: string | null; rpm_limit: number | null; tpm_limit: number | null;
  created_at: string; last_seen_at: string | null; killed_at: string | null;
  attached_token_count: number;
  spend_usd_this_month: string;   // CR-2 RESOLVED (Tin, freeze): agent-identity-governance §3 v2
  //   ships this field — a 2-dp string read from the SAME
  //   usage:spend:agent_principal:{id}:{YYYYMM} counter M4 enforces; NEVER null ("0.00" when the
  //   counter hasn't been written yet, never fabricated). AgentIdentityCard renders it directly.
}

interface AgentTokenInfo {   // GET /admin/agents/{id}/tokens item, per FROZEN agent-identity-governance §3 v2
  id: string; name: string;   // `name` maps to the token's OAuth scope (no free-text label column
  //   on RFC-8628 mints) — a disclosed parent-task judgment call (verify residue), rendered as-is.
  created_at: string; revoked_at: string | null; access_expires_at: string;
}

interface AgentIdentityCardProps {
  agent: AgentPrincipal;
  ownerDisplayName: string | null;   // UI-resolved via usersById join; null -> "No owner set"
  onKill: (id: string) => Promise<void>;
  onManageTokens: (id: string) => void;
}

interface CreateAgentDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (agent: AgentPrincipal) => void;   // appends to the query cache, no refetch required
}

interface ManageTokensDialogProps {
  open: boolean;
  principalId: string | null;   // GET /admin/agents/{principalId}/tokens -> AgentTokenInfo[] picker
  onClose: () => void;
}

interface McpSessionsFilters extends LogsFilters {   // LogsFilters imported unchanged from logs/LogsFilterBar.tsx
  serverTool?: string;   // exact "mcp::<server_host>::<tool_name>" string -> mapped to model_id
}

interface McpAllowListEntry { url: string; label: string; }

interface McpAllowListEditorProps {
  scope: "tenant" | "key";
  entries: McpAllowListEntry[];
  onSave: (entries: McpAllowListEntry[]) => Promise<void>;
  fieldErrors?: Record<number, string>;   // per-row inline error (M11), index-keyed
  readOnly?: boolean;                     // true for a non-owner viewing the tenant section (M9)
}
```

### Observable states (what the freeze approves — not pixels)
- Page: `loading` / `error(403)` / `populated` (three tabs mount only once GET /admin/agents
  resolves for Directory; Sessions/Policy each independently loading/error/populated).
- Directory: `empty` (zero agents) / `populated(grid)`; each card independently renders its
  live/killed variant.
- Create/Kill/Attach/Detach dialogs: `idle` / `submitting` / `field-error(<control>)` / `closed`.
- Sessions: mirrors logs-explorer-ui's page states verbatim, plus a `page-scoped-filter-banner`
  state (visible / hidden based on whether `serverTool` is set).
- Policy: `loading` / `populated(editable)` / `populated(read-only)` (non-owner viewing tenant
  section) / `field-error(<row>)`.

### Consumes — FROZEN parent surfaces (cited exactly, not redesigned)

```
GET/POST /admin/agents, GET /admin/agents/{id}/tokens,
POST .../tokens/{token_id}/attach, DELETE .../tokens/{token_id},
POST .../kill                                   — agent-identity-governance §3, FROZEN @ v2
GET/PUT /admin/mcp-servers,
GET/PUT/DELETE /admin/keys/{key_id}/mcp-servers  — mcp-connector-passthrough §3, FROZEN @ v2
GET /admin/logs, GET /admin/logs/{id}            — logs-explorer-api (unchanged; reused verbatim)
GET /admin/users, GET /admin/keys                — existing (owner-name / key-name resolution)
(tool-call-metering §3 v1 contributes NO HTTP surface this page calls; it is cited only to
 justify the M8 "trace cost ≠ billed cost" disclosure)
```
CR-A (spend field) and CR-B (token enumeration) are RESOLVED in agent-identity-governance §3 v2 —
this page consumes `spend_usd_this_month` and `GET /admin/agents/{id}/tokens` directly (above),
NOT the degrade paths the earlier draft carried.

### Consumes — gap / change-request candidates (NOT designed here; flagged for the parent tasks)

```
CR-C (logs-explorer-api): GET /admin/logs gains an optional model_prefix (or mcp_only) filter —
  NOT opened for R1 (Tin freeze decision). Until it lands, Sessions' server::tool filter stays a
  disclosed, page-scoped client-side approximation (M6/M7), not a precise "every MCP session"
  view. This is a designed v1 degrade, not a blocker.
```

Glossary deltas:
- **Agent identity card**: the directory unit MILESTONE.md names — one `AgentIdentityCard` per
  `agent_principal`, showing principal/owner/cap/spend(degraded)/last-seen/live-or-killed state.
- **Session explorer (agents-console)**: the Sessions tab — the SAME Logs Explorer master-table +
  drawer idiom, scoped (imprecisely, pending CR-C) to MCP tool-call trace rows.
- No new backend domain term is introduced by this task (it is presentation-only); the three
  glossary deltas already recorded by its parent contracts (agent principal, MCP allow-list,
  tool-call pricing unit) are unchanged.

**Freeze decisions (Tin-confirmed 2026-07-14):**
- [x] Nav placement: inside the existing **"Govern"** group (§1 ⚠ #3 — "Govern group").
- [x] CR-A (spend field): **spend in v1** — agent-identity-governance §3 amended to v2 with
  `spend_usd_this_month`; this page consumes it directly (no degrade). Shipped + integrated.
- [x] CR-B (token enumeration): resolved in the same v2 — `GET /admin/agents/{id}/tokens`;
  `ManageTokensDialog` is a real picker. Shipped + integrated.
- [x] CR-C (model_prefix filter): **NOT opened for R1** — the page-scoped Sessions approximation
  ships as a disclosed v1 degrade.

Least-sure flag surfaced at freeze: [spec] The Sessions tab's server::tool filter is a page-scoped, client-side approximation (M6/M7) because CR-C (a `model_prefix`/`mcp_only` filter on `GET /admin/logs`) was deliberately deferred out of R1 — so the tab cannot promise "every MCP tool-call session for this tenant", only "the MCP rows visible in the loaded log page, filtered client-side". This is the one place the page's data completeness is bounded by a parent surface it does not own; it is disclosed in-UI (a page-scoped banner, M7) rather than presented as authoritative. Confirm this degrade is acceptable to ship for v1 (the alternative is opening CR-C on logs-explorer-api and blocking this build on it).

Status: FROZEN @ v1 — approved by Tin Dang---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

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

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-14

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang---)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

