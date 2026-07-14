# DESIGN: Agents console (UDD)

persona: ui-designer (`.add/personas/ui-designer.md`) — Aurora shipped-system-as-truth, WCAG 2.2 AA floor, reuse-before-invent, never lowers a gate.
Ground SHA: `383f6e8` (branch `feat/agent-gateway-r1`)
Parent contracts consumed (read-only, NOT redesigned): `mcp-connector-passthrough` FROZEN @ v2, `agent-identity-governance` FROZEN @ v1, `tool-call-metering` FROZEN @ v1.

---

## 1 · Information architecture

One new top-level route, `/app/agents`, added to the existing "Govern" nav group (NOT a new nav
GROUP — reuse-before-invent; it sits alongside Teams/Members/Audit/Logs/Health/SLO/Guardrail
Analytics, the same governance-surface family). Internally it is **one page, three tabs** — this
directly implements MILESTONE.md's stated IA ("directory → session explorer → policy") as a
single controlled-`Tabs` surface, mirroring the Overview/Breakdown tab idiom already shipped on
`SpendPage`/`GuardrailAnalyticsPage` rather than three separate routes (state persists per tab,
no route-level remount cost, no new IA pattern invented):

```
/app/agents
├── Tab: Directory   (default)   — agent identity cards, create, kill
├── Tab: Sessions                — MCP tool-call trace explorer (Logs Explorer idiom, reused)
└── Tab: Policy                  — MCP allow-list management (tenant + per-key)
```

Nav entry (`apps/dashboard/components/ui/app-shell.tsx:NAV_GROUPS`, "Govern" group):
```ts
{ href: "/app/agents", label: "Agents", icon: Bot, minRole: "admin" }
```
`minRole:"admin"` is the same conservative UX-only gate the "Logs" sibling entry already uses
(GET /admin/agents itself allows any authenticated role per the frozen contract, but every WRITE
on this page needs {OWNER,ADMIN} or OWNER-only — gating nav visibility at "admin" avoids showing
a page where every action is greyed out for a member). The gateway remains the real enforcement
boundary; this is UX-only and fails open, per every existing nav entry's own documented rule.

---

## 2 · Wireframe (text)

### Directory tab

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Agents                                                    [+ New agent] │
│ Directory, session traces, and MCP policy for every agent principal.    │
├───────────────────────────────────────────────────────────────────────--┤
│ [Directory] [Sessions] [Policy]                                         │
├───────────────────────────────────────────────────────────────────────--┤
│ ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐│
│ │ billing-bot   [Live]│  │ crawler-01 [Killed] │  │ ops-agent    [Live] ││
│ │ Owner: J. Alvarez   │  │ Owner: No owner set │  │ Owner: T. Dang      ││
│ │ Cap: $50.00/mo      │  │ Cap: No cap set     │  │ Cap: $200.00/mo     ││
│ │ Spend: not available│  │ Killed 2026-07-10   │  │ Spend: not available││
│ │  yet ⓘ              │  │                     │  │  yet ⓘ              ││
│ │ Last seen: 2h ago   │  │ Last seen: 3d ago   │  │ Last seen: 5m ago   ││
│ │ 3 tokens attached    │  │ 1 token attached    │  │ 2 tokens attached   ││
│ │ [Manage tokens][Kill]│  │ [Manage tokens]     │  │ [Manage tokens][Kill]│
│ └─────────────────────┘  └─────────────────────┘  └─────────────────────┘│
└─────────────────────────────────────────────────────────────────────────┘
```
Grid: `grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3` — the SAME responsive grid used by
`StatCard` rows and `PlatformPlanCatalog` (no new breakpoint scheme). A killed card drops the
Kill button entirely (never a disabled-but-visible one — a killed principal cannot be revived)
and shows a static "Killed «timestamp»" line instead.

### Sessions tab

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ⓘ Showing MCP sessions found on the current page only — a broader,      │
│   server-side filter is tracked as a change request (see §ⓘ below).     │
├───────────────────────────────────────────────────────────────────────--┤
│ From [___] To [___]  Key [All ▾]  Status [All ▾]  Cost min/max [__][__] │
│ Server::tool (exact) [mcp::mcp.acme.example::search______________]       │
├───────────────────────────────────────────────────────────────────────--┤
│ Time        Key        Server::tool                 Status  Latency    │
│ 10:41:02    agent-01   mcp::mcp.acme.example::search  200     118ms  ›  │
│ 10:40:55    agent-01   mcp::mcp.acme.example::fetch   403     —      ›  │
│  … (LogsTable, unchanged) …                              [‹ Prev][Next ›]│
└─────────────────────────────────────────────────────────────────────────┘
                                    │ row click
                                    ▼
        LogDetailDrawer (UNCHANGED) — Overview / Request / Response / Guardrail Verdict
        + one added note: "cost_usd on this row is trace metadata only — the billed
          amount for this tool call appears on the tenant's Invoice, grouped by
          (mcp_server, mcp_tool)."
```

### Policy tab

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Unlisted servers are refused, not warned — an agent can only reach a    │
│ server on this list.                                                     │
│                                                                           │
│ Tenant allow-list (owner only to edit)                                  │
│ ┌───────────────────────────────────────────────────┐                  │
│ │ https://mcp.acme.example/v1     Acme          [×] │                  │
│ │ https://mcp.search.example/v1   Search        [×] │                  │
│ │ [+ Add server]                          [Save]    │                  │
│ └───────────────────────────────────────────────────┘                  │
│                                                                           │
│ Per-key override                                                        │
│ Key: [prod-agent-key ▾]           ( ) Inherit tenant list               │
│                                    (•) Custom list for this key         │
│ An empty custom list blocks this key from every MCP server — this is    │
│ different from inheriting.                                              │
│ ┌───────────────────────────────────────────────────┐                  │
│ │ https://mcp.narrow.example       narrow       [×] │                  │
│ │ [+ Add server]                          [Save]    │                  │
│ └───────────────────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────────┘
```
Both list editors are ONE component, `McpAllowListEditor`, mounted twice (tenant scope, key
scope) — mirrors the "design it once, use everywhere" rule `RegionBadge`'s own header comment
states, and the "one shared visual treatment for pick-one-of-a-few" precedent `TierSelector`
already cites against `RetentionZdrSettings`'s residency picker.

---

## 3 · Reused-component map (reuse before invent)

| Need                                   | Reused AS-IS                                              | New |
|-----------------------------------------|-------------------------------------------------------------|-----|
| Destructive confirm + consequence line  | `components/teams/ConfirmDialog.tsx` (the "ZDR typed-confirm idiom" milestone names IS this component + a plain-language `description`, per `RetentionZdrSettings`'s own ZDR-enable dialog — there is no literal type-the-name text input anywhere in this codebase's confirm idiom) | — |
| Session trace list + detail             | `components/logs/LogsTable.tsx`, `components/logs/LogDetailDrawer.tsx` (verbatim — same props, same `GET /admin/logs`/`GET /admin/logs/{id}`) | `McpSessionsFilterBar.tsx` (LogsFilterBar + one exact server::tool field) |
| Live/Killed state marker                | `Badge` variants (`success`/`destructive`) + icon, mirrors `components/invoices/InvoiceStatusSeal.tsx`'s Badge+icon+sr-only idiom | — |
| Grid of cards                           | the `grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3` pattern (StatCard rows, `PlatformPlanCatalog`) | `AgentIdentityCard.tsx` (built from `Card`/`CardHeader`/`CardTitle`/`CardContent`/`CardFooter`, unmodified) |
| Pick-one-of-a-few radio (inherit vs custom) | the `RESIDENCY_REGION_LABELS` / `TierSelector` native-radio visual treatment | — |
| List editor (URL + label rows)          | `Input`, `Button`, `Table` primitives | `McpAllowListEditor.tsx` (new — no existing "editable list of URLs" component in this codebase) |
| Owner / key display-name resolution     | the `keysById` client-side join idiom (`LogsExplorerPage.tsx`, fetch-once + map) against `GET /admin/users` / `GET /admin/keys` | — |
| Field-level write errors                | `LogsFilterBar`'s `fieldErrors` idiom | — |
| Page chrome / four states               | `PageHeader`, `Loading`, `ErrorState` (the same loading/error/empty/populated states every dashboard page uses) | — |
| Tabs                                    | `Tabs`/`TabsList`/`TabsTrigger`/`TabsContent` (already used by Spend/Guardrail Analytics) | — |
| BFF pass-through                        | `bffGet`/`bffPost`/`bffPut`/`bffDelete` (`/api/gw/[...path]`) — no raw token ever reaches the browser, true by construction | — |

---

## 4 · Signature element — the agent identity card

Per MILESTONE.md: "the agent identity card (principal, owner, spend, last-seen, live/killed
state) as the directory unit." `AgentIdentityCard` renders, top to bottom: principal name
(`CardTitle`) + state Badge (top-right, `success`/"Live" or `destructive`/"Killed" + icon — state
is NEVER color-only, an icon + text label always accompanies the tint, WCAG 1.4.1); owner (a
resolved display name, or the raw `owner_user_id`, or "No owner set" — never blank); monthly
budget cap; a **spend row that is honestly degraded** (see §5 below — the frozen contract does
not yet return a spend figure); last-seen (`formatTimestamp` or "Never authenticated"); attached
token count; and, only when live, a `Kill` action gated behind `ConfirmDialog`.

## 5 · The one thing this design could NOT wire end-to-end (read before freeze)

MILESTONE.md's own signature-element wording, and this task's dispatch brief, both name **spend**
as a directory-card field, sourced "from usage_records." Grounding `agent-identity-governance`'s
FROZEN §3 v1 contract line-by-line: `GET /admin/agents`'s response carries `monthly_budget_usd`
(the CAP an admin sets) but **no current-month spend field at all**. The only spend tracking that
exists is `GovernanceService._check_agent_principal_budget`'s Redis counter
(`usage:spend:agent_principal:{principal_id}:{YYYYMM}`) — write-side only, never exposed by any
read API. This is a genuine cross-task interface gap, not a UI oversight: I cannot invent a new
backend read surface here (out of scope per the dispatch brief), and I must not silently render a
fabricated `$0`. The card ships with an honest "Spend: not available yet" state and a change
request is recorded (§1 ⚠ in TASK.md) to add `spent_usd_month` to the frozen contract additively.
A second, related gap: the frozen contract never lists a principal's attached token IDs (only a
`count`), so this console cannot offer a token picker — `ManageTokensDialog` accepts a pasted
token id instead, disclosed as a v1 limitation, not hidden.
