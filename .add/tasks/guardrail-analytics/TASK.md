# TASK: Guardrail verdict analytics API + dashboard view

slug: guardrail-analytics · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): <path:symbol — what it is / how it is keyed>
  - `apps/gateway/src/gateway/proxy/domain/entities.py:GuardrailEvent` (frozen dataclass: `guardrail`, `action`, `detail` — no pattern-name field, no key/policy discriminator) and `:GuardrailResult` (`blocked`, `blocked_by`, `masked_messages`, `events: list[GuardrailEvent]`). The ONLY structured verdict shape that exists today.
  - `apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py:RegexGuardrailEvaluator.evaluate_pre`/`_evaluate_pre_inner` (~483-650) — emits one `GuardrailEvent` per configured guardrail per pre-call evaluation (`prompt_injection`: blocked/audited/passed; `pii_mask` request-side: masked/audited/passed/budget_exceeded/error). `:evaluate_post` (~653-680) is the RESPONSE-body PII masking pass and returns `dict[str, Any]` body ONLY — **no events at all**, at any of its 4 call sites (`use_cases.py` ~1453, ~1525, ~1598, ~2153). Response-side pii_mask verdicts are invisible to every counting mechanism that exists today, not just this task's — a pre-existing gap, confirmed by reading the signature.
  - `apps/gateway/src/gateway/proxy/infrastructure/ml_moderation_evaluator.py:MlModerationGuardrailEvaluator` (~171-232) — emits `GuardrailEvent(guardrail="ml_moderation", action="blocked"|"audited"|"passed"|"unchecked", detail=<comma-joined categories, or the honest-degradation reason>)`. `"unchecked"` = provider-outage honest-degradation (ml-moderation-layer TASK.md), a real outcome tenants want visibility into.
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:_fire_guardrail_metrics` (~369-393) — the ONLY existing consumer of `GuardrailEvent` lists today: increments a Prometheus counter `guardrail_events_total{guardrail,mode,action}` (no tenant_id/key_id label — adding one would be a cardinality-unsafe change to an operator-facing metric, not something this task should do). Called at exactly 3 sites in the NON-streaming completions path (~1772 fail-closed-error, ~1809 fail-open-error, ~1820 the main post-`evaluate_pre` success path — all inside the `try/except/finally` block ~1748-1820). The STREAMING pre-call path (`evaluate_pre` call ~2333) has **no** `_fire_guardrail_metrics` call anywhere nearby — confirmed by grep — a pre-existing metrics-coverage gap for streaming requests.
  - `apps/gateway/src/gateway/keys/domain/entities.py:AuthzResult.policy_source` (`"key"|"tenant"|"none"`) — added and threaded end-to-end by the sibling `per-key-guardrail-policies` (FROZEN @ v1, already merged/built) specifically anticipating this task: its own §0 Issues/Risks states verbatim "the sibling `guardrail-analytics` task ... will likely want to attribute hit-counts to 'key policy' vs 'tenant policy'". Confirmed via `grep` that this field has **zero consumers** anywhere in the codebase today — this task is its first reader.
  - `apps/gateway/src/gateway/logs/infrastructure/orm.py:RequestLogRow.guardrail_verdict` (JSONB, nullable) — the payload-capture-store's (sibling, FROZEN @ v1, merged) own docstring marks this column "reserved, unpopulated in v1". Rows in `request_logs` exist ONLY for tenants/keys with payload capture opted IN (MILESTONE.md scope item 1 — opt-in) — guardrails evaluate on 100% of governed traffic regardless of capture setting, so this column is NOT a viable aggregation source for tenant-wide guardrail analytics (would silently under-report to zero for any capture-off tenant). Confirmed by reading the full ORM docstring and the milestone's opt-in scope line.
  - `apps/gateway/src/gateway/usage/api/router.py:get_spend` (~288-525) + `_compute_window_bounds` (~192-286) + `get_alerts`/`_require_ops_read` (~101-122, ~639-693) + `get_slo`'s `SUM(CASE WHEN ... THEN 1 ELSE 0 END)` conditional-aggregation idiom (~1148-1183) — the EXACT aggregation/bucketing/breakdown/permission-gate patterns this task's new endpoint mirrors: `window` (day/week/month, optional `start`/`end` override, `_compute_window_bounds` reused verbatim), single `group_by` whitelist (422 on an unknown value), tenant-scoped `WHERE tenant_id = :tenant_id` on every query, empty-window → 200 with explicit zeros (never 404), and per-action conditional-SUM pivoting (mirrors `get_slo`, not a Python-side pivot).
  - `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` (~30-56) — the fire-and-forget, OWN-session (separate from the caller's transaction), swallow-all-exceptions write pattern (`asyncio.create_task`, never awaited inline) this task's new verdict-recording hook mirrors byte-for-byte.
  - `apps/gateway/src/gateway/tenants/domain/authz.py:Permission.OPS_READ` + `ROLE_PERMISSIONS` (~55-122) — grants owner/admin/operator/billing_admin/viewer, denies member (403) — the SAME gate `/admin/alerts`, `/admin/health/upstreams`, `/admin/ratelimits`, `/admin/bandwidth`, `/admin/slo` already use; reused verbatim rather than inventing a `GUARDRAIL_ANALYTICS_READ` permission.
  - `apps/gateway/src/gateway/tenants/api/guardrail_router.py:guardrail_router` (`APIRouter(prefix="/admin/guardrails", tags=["guardrails"])`, routes `GET ""` / `PUT ""` only — confirmed via grep, no sub-path) — no collision risk with a new `/admin/guardrails/analytics` route.
  - `apps/dashboard/components/spend/SpendPage.tsx` (full file read) + `SpendSparkline.tsx` — the EXACT dashboard analytics-view pattern to mirror: `PageHeader` with window/group-by/key `<select>`s in `actions`, a `StatCard` hero grid for totals (OUTSIDE tabs so it survives tab switches), a Recharts sparkline (`data-testid="..._chart"`, decorative, `accessibilityLayer={false}`, bucket list as the accessible source), controlled `Tabs` (Overview / Breakdown) with a `DataTable` for the active `group_by` breakdown, `bffGet`/`BffError` from `@/lib/bff-client`, `useQuery` + `keepPreviousData` + a `lastGood` render-time state guard so a transient error doesn't blank a populated view.
  - `apps/dashboard/components/ui/app-shell.tsx:NAV_GROUPS` "Govern" group (~91-101: Teams/Members/Alerts/Audit/Health/SLO, every item `minRole: "admin"`) — the nav slot this task's new page joins (guardrail hit-rate is governance/security-posture data, same semantic bucket as Alerts/Audit/Health/SLO, not billing data like the "Insights" group).
  - `apps/dashboard/components/settings/GuardrailSettings.tsx` — the EXISTING tenant Guardrails config tab (GET/PUT `/admin/guardrails`); a sibling surface this task does not touch, cited only so the new page is clearly framed as analytics/read, not a 3rd config surface.

Context (working folder): <docs · todos · config · data the task touches — task-delta only>
  - No task-specific docs/config exist yet; `.add/milestones/logs-explorer-guardrails-v2/MILESTONE.md` (read in full) is the only prior context. The sibling `.add/tasks/per-key-guardrail-policies/TASK.md` (phase: verify, FROZEN @ v1, built) was read in full for the `policy_source` hand-off.

Honors (patterns / conventions): <PROJECT.md / CONVENTIONS.md anchors — task-delta only, never a re-scan>
  - CONVENTIONS.md clean-architecture layering (`domain/` ← `application/` ← `infrastructure/` ← `api/` per module) — this task earns its OWN thin module (`gateway/guardrail_analytics/`), the same way `payload-capture-store` earned `gateway/logs/` for a genuinely new persisted concept, rather than bolting a new table onto an unrelated existing module.
  - PROJECT.md invariant "every tenant-owned row carries tenant_id; every query is tenant-scoped" — the new table and every new query honor this exactly like `usage_records`/`alert_events`/`audit_events`/`request_logs`.
  - The append-only-ledger precedent (`usage_records`, `alert_events`, `audit_events`, `request_logs`): no FK on `key_id` (key deletion must not cascade/block an append-only history row) — this task's new table mirrors that shape exactly.
  - CLAUDE.md "design for failure on every new IO seam" — the new verdict-write hook IS a new seam (a DB write off the hot proxy-response path); it needs a bounded timeout + fail-open swallow, mirroring `record_audit`. It does NOT need a retry/circuit-breaker: it is a local Postgres insert on the gateway's own database (not third-party outbound IO), and `per-key-guardrail-policies` set the exact precedent for calling this out explicitly rather than reflexively adding breaker machinery a local-DB fire-and-forget write doesn't need.
  - Tin's UI/UX polish standing bar (memory: ui-ux-polish-standing-bar) — the dashboard view reuses `SpendPage`'s exact IA (hero + sparkline + tabbed breakdown), not a bare CRUD table.

Anchors the contract cites: <the symbols §3 will name>
  GuardrailEvent · GuardrailResult · AuthzResult.policy_source · _fire_guardrail_metrics · RegexGuardrailEvaluator.evaluate_pre · MlModerationGuardrailEvaluator · get_spend · _compute_window_bounds · record_audit · Permission.OPS_READ · SpendPage · SpendSparkline · NAV_GROUPS · PAYLOAD_GROUP_BY_INVALID · PAYLOAD_WINDOW_INVALID · KEY_NOT_FOUND_IN_TENANT

Issues/Risks (→ feed §1): <problems · traps · untestable risks found in the real code — task-delta; §1 builds on these>
  - `request_logs.guardrail_verdict` is opt-in-only and reserved/unpopulated — NOT a viable aggregation source (drives the §1 Framing decision below).
  - `evaluate_post` (response-side pii_mask masking) returns body only, no events, across 4 call sites — extending its contract is a materially bigger, hot-path-touching change (mirrors exactly the kind of invasive Framing the sibling task explicitly rejected for a different feature). Scoped OUT of this task's v1 — named as a spec delta, not silently dropped (§1 ⚠#3).
  - The streaming pre-call path never calls `_fire_guardrail_metrics` — a pre-existing metrics gap. THIS task's new hook (a separate call site from the Prometheus one) SHOULD cover streaming too, or the dashboard's counts would be silently wrong for any tenant using `stream: true` — made a Must (M1), not left to inherit the old gap.
  - `GuardrailEvent` carries no per-regex/per-category identity — only the coarse guardrail type (`prompt_injection`/`pii_mask`/`ml_moderation`). True "which specific pattern fired" granularity needs evaluator changes across ~10 `GuardrailEvent(...)` call sites in `guardrail_evaluator.py`/`ml_moderation_evaluator.py` — deferred, this is the task's top ⚠ flag (§1).
  - The Prometheus `guardrail_events_total` counter has no tenant/key label and is operator-facing infrastructure telemetry — reusing it for a tenant-facing admin API would require either an unsafe cardinality change or standing up a new Prometheus-query dependency inside the gateway's own admin API; neither is asked for by this milestone.

Related intent: <PROJECT.md § · GLOSSARY term(s) · originating request/milestone rationale — the WHY; task-delta>
  - MILESTONE.md scope item 7: "guardrail analytics — per-policy/per-key hit counts + a dashboard analytics view" and Exit criterion: "A tenant admin can see guardrail hit counts by policy/pattern/key over a time window in the dashboard" (← this task, owned in full).
  - MILESTONE.md "Security floor": logs/capture-store are `data`-sensitivity (payload exposure); this task's rows are NOT payload-bearing (no `request_body`/`response_body`, only counts + a coarse guardrail/action/policy_source label) — a materially lower sensitivity than `payload-capture-store`/`logs-explorer-api`, worth stating explicitly since it justifies OPS_READ (viewer-inclusive) rather than a stricter gate.
  - GLOSSARY.md "Guardrail" (added v4, amended by `per-key-guardrail-policies` for two-level resolution) — this task does not further amend that definition; it introduces new terms (below).
  - `per-key-guardrail-policies` TASK.md §0 Issues/Risks — direct textual evidence that `policy_source` was added FOR this task (see Touches above); honored as the "policy" breakdown dimension.

Ground SHA: 443a33a (branch `feat/enterprise-hardening`) — cite symbols, not bare line numbers; any line ref above is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Guardrail verdict analytics — counters + admin API + dashboard view
Framings weighed: **A — a NEW append-only `guardrail_verdict_events` table, written via a fire-and-forget hook at verdict-emission time (mirrors `record_audit`'s own-session/swallow-all pattern), independent of payload-capture opt-in; read via a new windowed aggregation admin API mirroring `get_spend`'s `date_trunc` + `group_by` shape** (chosen) · B — aggregate over `request_logs.guardrail_verdict` (rejected: that column is reserved/unpopulated by the sibling payload-capture-store's own frozen contract, AND `request_logs` rows only exist for tenants/keys with capture opted IN — guardrails evaluate on 100% of governed traffic regardless of capture, so this source would silently under-report to zero for every capture-off tenant, exactly the tenants most likely to rely on masking/blocking working invisibly) · C — proxy the existing Prometheus `guardrail_events_total` counter through a new admin endpoint (rejected: no tenant_id/key_id label — adding one is a cardinality-unsafe change to an operator-facing metric — and it would introduce a new Prometheus-query dependency into the tenant-facing admin API that nothing else in this codebase's admin surface does).
Must:
<must>
  - M1: Every PRE-CALL guardrail verdict (`prompt_injection` blocked/audited/passed; `pii_mask` request-side masked/audited/passed/budget_exceeded; `ml_moderation` blocked/audited/passed/unchecked; and the evaluator-raised fail-open/fail-closed `"error"` event) is recorded as one row, for BOTH the non-streaming AND the streaming completion paths, via a new fire-and-forget hook mirroring `record_audit` (own session, `asyncio.create_task`, swallow-all-exceptions, never fails/slows/blocks the proxied request) — independent of whether payload capture is enabled for that tenant/key. This closes the pre-existing streaming metrics-coverage gap (§0) as a byproduct, since the new hook is a separate call site from `_fire_guardrail_metrics`, not a wrapper around it.
  - M2: Each recorded row carries `tenant_id`, `key_id` (no FK — append-only-ledger precedent), `team_id` (nullable, no FK), `guardrail` (`"prompt_injection"|"pii_mask"|"ml_moderation"|"error"` — the **pattern** dimension), `action` (`"blocked"|"masked"|"audited"|"passed"|"error"|"unchecked"|"budget_exceeded"`), `policy_source` (`"key"|"tenant"|"none"`, sourced from `AuthzResult.policy_source` resolved once at auth time — the **policy** dimension, zero extra read), `created_at`.
  - M3: `GET /admin/guardrails/analytics` (OPS_READ-gated: owner/admin/operator/billing_admin/viewer pass, member → 403) returns windowed totals + time-bucketed counts, mirroring `/admin/spend`'s `window` param (`day`/`week`/`month`, optional `start`/`end` ISO-date override) and reusing `_compute_window_bounds` verbatim (imported, not duplicated).
  - M4: The endpoint supports an optional `group_by` = `"guardrail"` (the **pattern** dimension) | `"policy_source"` (the **policy** dimension) | `"key_id"` (the **key** dimension) — exactly one breakdown at a time per request, mirroring `/admin/spend`'s single-`group_by` shape (the milestone's own exit-criterion wording "by policy/pattern/key" is satisfied as three selectable single-dimension breakdowns, not a 3-way cross-tab — no existing precedent in this codebase does a multi-dimension cross-tab in one response). Omitted `group_by` → totals + buckets only, `breakdown: null`, never 422.
  - M5: An optional `key_id` query filter narrows every query to that key; a `key_id` not belonging to the caller's tenant (or not existing) → 404, reusing `KEY_NOT_FOUND_IN_TENANT` verbatim — mirrors `/admin/spend`'s own key_id filter behavior exactly.
  - M6: Every query is tenant-scoped (`WHERE tenant_id = :tenant_id` always applied, on both the write path via the authenticated request's own tenant and every read-path query) — a cross-tenant row is never written or returned.
  - M7: An empty window (no matching rows) → 200 with explicit zero totals + empty `buckets`/`breakdown` (never 404) — mirrors `/admin/spend`.
  - M8: The dashboard exposes a new "Guardrail Analytics" page, joining the `NAV_GROUPS` "Govern" section (`minRole: "admin"`, alongside Alerts/Audit/Health/SLO), mirroring `SpendPage`'s exact IA: `PageHeader` with window/group-by/key `<select>`s in `actions`, a `StatCard` hero grid for totals (evaluations, hits, and the per-action counts) OUTSIDE the tabs, a Recharts sparkline (decorative, bucket list as the accessible source) for the time series, controlled `Tabs` (Overview/Breakdown) with a `DataTable` for the active `group_by` breakdown — reusing `@/components/ui` (`Card*`, `StatCard`, `DataTable`, `Tabs*`, `PageHeader`, `Loading`, `ErrorState`, `Empty`) and `@/lib/bff-client` (`bffGet`, `BffError`) verbatim; no new design tokens or components invented.
  - M9: The new write hook introduces NO new outbound-IO seam requiring a timeout/retry/circuit-breaker in the CLAUDE.md sense — it is a local Postgres insert on the gateway's own database (same rationale `per-key-guardrail-policies` used to explicitly rule out a breaker for its own zero-new-IO resolution). It DOES get a bounded async timeout + fail-open exception swallow (mirrors `record_audit`), since an unbounded/blocking write is its own failure mode regardless of "local vs remote".
</must>
Reject:
<reject>
  - `group_by` outside `{"guardrail","policy_source","key_id"}` -> "ERR_PAYLOAD_GROUP_BY_INVALID" (422; reuses `PAYLOAD_GROUP_BY_INVALID` verbatim — R1)
  - `window` outside `{"day","week","month"}` -> "ERR_PAYLOAD_WINDOW_INVALID" (422; reuses `PAYLOAD_WINDOW_INVALID` verbatim — R2)
  - malformed `start`/`end` (not ISO date) -> "ERR_PAYLOAD_START_DATE_INVALID" / "ERR_PAYLOAD_END_DATE_INVALID" (422; reuses both verbatim — R3)
  - `key_id` not a valid UUID -> "ERR_PAYLOAD_KEY_ID_UUID_INVALID" (422; reuses `PAYLOAD_KEY_ID_UUID_INVALID` verbatim — R4)
  - `key_id` a valid UUID but belongs to another tenant, or doesn't exist -> "ERR_KEY_NOT_FOUND" (404; reuses `KEY_NOT_FOUND_IN_TENANT` verbatim, no existence leak — R5)
  - caller lacks OPS_READ (member role) -> "ERR_AUTH_FORBIDDEN" (403 — R6)
  - missing/invalid bearer token -> "ERR_AUTH_TOKEN_MISSING" / "ERR_AUTH_INVALID_TOKEN" (401 — R7)
  - the guardrail-verdict write itself fails (DB error, timeout, pool exhaustion) -> NEVER surfaced to the caller and NEVER retried inline; the proxied completion request succeeds/fails purely on its own merits, the verdict row is silently dropped and a warning is logged (fail-open invariant, mirrors `record_audit` exactly — R8, an invariant rather than an HTTP rejection code)
</reject>
After:
<after>
  - `guardrail_verdict_events` accumulates one row per pre-call guardrail evaluation, for both streaming and non-streaming completions, independent of payload-capture opt-in.
  - A tenant admin (owner/admin/operator/billing_admin/viewer) can see guardrail hit counts broken out by guardrail type, policy source, or key, over day/week/month windows, via `GET /admin/guardrails/analytics` and the new "Guardrail Analytics" dashboard page — satisfying the milestone's exit criterion in full.
  - The proxy's guardrail-evaluation latency and behavior are unchanged for every existing caller — the new hook is fire-and-forget and sits entirely off the request/response path; a verdict-recording failure never surfaces as a proxy error.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 The "pattern" dimension mapping — chosen: the coarse `guardrail` field (`prompt_injection`/`pii_mask`/`ml_moderation`, ZERO evaluator changes) vs. the rejected literal reading: true per-regex/per-category granularity (which of the 7 prompt-injection families, which of the 8 built-in PII patterns, which ml_moderation category matched) — the latter would require extending `GuardrailEvent` with a new field and touching ~10 `GuardrailEvent(...)` call sites across `guardrail_evaluator.py`/`ml_moderation_evaluator.py`, a materially bigger, hot-path-touching lift. Lowest confidence because the milestone's own wording ("by policy/pattern/key") is genuinely compatible with either reading and I've picked the zero-evaluator-cost interpretation over the more literal one. If wrong: the dashboard's "pattern" breakdown reads as guardrail-TYPE-level only (an admin can't distinguish "backreference attempt" from "ignore-previous-instructions" within `prompt_injection`) — cheap to extend later as an ADDITIVE `GuardrailEvent.pattern_id` field + an additive nullable column on the same table, without reshaping this task's API/table (a spec delta, not a rebuild). RECOMMEND: ship the coarse mapping now.
  - [ ] #2 "policy" = `AuthzResult.policy_source` (key-override vs tenant-inherited vs none) — medium-high confidence: direct textual evidence in the sibling `per-key-guardrail-policies` TASK.md §0 (that field was added explicitly anticipating this task), but not spelled out verbatim in this task's own brief. Confirm at freeze.
  - [ ] #3 `evaluate_post` (response-side pii_mask masking) verdicts are OUT OF SCOPE for v1 — a pre-existing gap (that method returns body only, no events, at 4 call sites) that this task does not close, since doing so would touch the evaluator's contract as invasively as the rejected per-pattern-granularity framing above. If wrong: "guardrail hit counts" undercounts `pii_mask` specifically for any tenant relying mainly on response-side masking rather than request-side. RECOMMEND: ship pre-call-only v1 + file a `[SPEC · open]` delta for `evaluate_post` to return events, as a clearly-named follow-up rather than a silently accepted gap.
  - [ ] #4 `group_by` is single-dimension-at-a-time (mirrors `/admin/spend`) rather than a 3-way cross-tab in one response — low-stakes, matches the one existing precedent exactly; a cross-tab has no precedent anywhere in this codebase's admin API and would be a materially bigger API+UI shape.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: non-streaming pre-call verdict is recorded independent of capture   # M1
  Given a tenant with prompt_injection {enabled:true, mode:block} and payload capture OFF for the key
  When a completion request through that key contains a prompt-injection pattern (non-streaming)
  Then a guardrail_verdict_events row is written with guardrail="prompt_injection", action="blocked"
  And no request_logs row exists for this call (capture is off) — the verdict row is independent of it

Scenario: streaming pre-call verdict is recorded (closes the pre-existing metrics gap)   # M1
  Given a tenant with pii_mask {enabled:true, mode:audit}
  When a streaming completion request through that key contains PII
  Then a guardrail_verdict_events row is written with guardrail="pii_mask", action="audited"
  And this parity holds even though the Prometheus guardrail_events_total counter never fires for streaming

Scenario: recorded row carries policy_source from the resolved AuthzResult   # M2
  Given a key with its own guardrail_policy override (policy_source="key" at auth time)
  When a completion request through that key trips a guardrail
  Then the recorded row's policy_source = "key"
  And a sibling key with no override (policy_source="tenant") records policy_source = "tenant" on its own hit

Scenario: windowed totals default to the current month   # M3
  Given a tenant with 3 recorded guardrail_verdict_events rows this month and 1 row from last month
  When an owner calls GET /admin/guardrails/analytics with no query params
  Then 200 is returned with window="month" and totals reflecting only the 3 rows from this month

Scenario: group_by=guardrail returns the pattern-dimension breakdown   # M4
  Given a tenant with 5 prompt_injection rows and 2 pii_mask rows this week
  When an owner calls GET /admin/guardrails/analytics?window=week&group_by=guardrail
  Then 200 is returned with breakdown=[{guardrail:"prompt_injection", evaluations:5, ...}, {guardrail:"pii_mask", evaluations:2, ...}]

Scenario: group_by=policy_source returns the policy-dimension breakdown   # M4
  Given a tenant with 4 rows where policy_source="key" and 6 rows where policy_source="tenant"
  When an owner calls GET /admin/guardrails/analytics?window=week&group_by=policy_source
  Then 200 is returned with breakdown containing one item per policy_source with the correct counts

Scenario: group_by=key_id returns the key-dimension breakdown   # M4
  Given a tenant with rows for key A (3 hits) and key B (7 hits) this week
  When an owner calls GET /admin/guardrails/analytics?window=week&group_by=key_id
  Then 200 is returned with breakdown containing one item per key_id with the correct counts

Scenario: omitted group_by returns totals+buckets only, no breakdown   # M4
  Given a tenant with any recorded rows
  When an owner calls GET /admin/guardrails/analytics?window=week (no group_by)
  Then 200 is returned with breakdown=null and populated totals + buckets

Scenario: key_id filter narrows every query to one key   # M5
  Given a tenant with rows for key A and key B this week
  When an owner calls GET /admin/guardrails/analytics?window=week&key_id=<A's id>
  Then 200 is returned with totals reflecting ONLY key A's rows

Scenario: cross-tenant key_id filter is invisible   # M5, R5
  Given key_id belongs to a different tenant than the caller
  When an owner calls GET /admin/guardrails/analytics?key_id=<foreign key id>
  Then 404 ERR_KEY_NOT_FOUND is returned
  And no cross-tenant data is ever included in the response

Scenario: another tenant's rows are never visible   # M6
  Given tenant A has 10 recorded rows and tenant B has 0
  When tenant B's owner calls GET /admin/guardrails/analytics
  Then 200 is returned with all-zero totals for tenant B — tenant A's rows never leak

Scenario: empty window returns explicit zeros, never 404   # M7
  Given a tenant with zero recorded rows in the queried window
  When an owner calls GET /admin/guardrails/analytics?window=day
  Then 200 is returned with totals.evaluations=0, totals.hits=0, buckets=[]

Scenario: dashboard page renders totals, sparkline, and a breakdown table   # M8
  Given the API returns populated totals/buckets/breakdown for group_by=guardrail
  When a tenant admin opens the Guardrail Analytics page in the Govern nav group
  Then the StatCard hero shows evaluations/hits, the sparkline renders the bucket series
  And the Breakdown tab's DataTable shows one row per guardrail type

Scenario: a verdict-write failure never fails the proxied request   # M9, R8
  Given the guardrail_verdict_events INSERT raises (simulated DB error)
  When a completion request through a key with an active guardrail policy is made
  Then the proxied completion still succeeds/fails purely on its own merits (200/blocked as guardrail dictates)
  And a warning is logged; no exception propagates to the caller; no verdict row exists for that call

Scenario: invalid group_by is rejected   # R1
  Given any tenant
  When an owner calls GET /admin/guardrails/analytics?group_by=team_id
  Then 422 ERR_PAYLOAD_GROUP_BY_INVALID is returned
  And no query executes against guardrail_verdict_events

Scenario: invalid window is rejected   # R2
  Given any tenant
  When an owner calls GET /admin/guardrails/analytics?window=year
  Then 422 ERR_PAYLOAD_WINDOW_INVALID is returned
  And no query executes against guardrail_verdict_events

Scenario: malformed start/end date is rejected   # R3
  Given any tenant
  When an owner calls GET /admin/guardrails/analytics?start=not-a-date
  Then 422 ERR_PAYLOAD_START_DATE_INVALID is returned
  And no query executes against guardrail_verdict_events

Scenario: malformed key_id is rejected   # R4
  Given any tenant
  When an owner calls GET /admin/guardrails/analytics?key_id=not-a-uuid
  Then 422 ERR_PAYLOAD_KEY_ID_UUID_INVALID is returned
  And no query executes against guardrail_verdict_events

Scenario: a member is forbidden from reading analytics   # R6
  Given a member-role caller (below OPS_READ)
  When they call GET /admin/guardrails/analytics
  Then 403 ERR_AUTH_FORBIDDEN is returned
  And no data is returned in the response body

Scenario: missing bearer token is rejected   # R7
  Given a request with no Authorization header
  When GET /admin/guardrails/analytics is called
  Then 401 ERR_AUTH_TOKEN_MISSING is returned
  And no data is returned in the response body
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: DRAFT — awaiting human freeze

Least-sure flag surfaced at freeze: [spec/contract] the "pattern" dimension = the coarse `guardrail` field (prompt_injection/pii_mask/ml_moderation), NOT true per-regex/per-category granularity — a reading of the milestone's "by policy/pattern/key" wording chosen because it costs zero evaluator changes; the literal per-pattern reading would require touching ~10 `GuardrailEvent(...)` call sites in `guardrail_evaluator.py`/`ml_moderation_evaluator.py`. See §1 ⚠#1 for the full tradeoff. A secondary, lower-stakes flag: `evaluate_post` (response-side pii_mask) verdicts are OUT of v1 scope (§1 ⚠#3) — a named, not silent, gap.

```
GET /admin/guardrails/analytics
  query: window="day"|"week"|"month" (default "month"), start?, end? (ISO date, inclusive),
         group_by?="guardrail"|"policy_source"|"key_id", key_id?=<uuid>
  200 -> {
    window: "day"|"week"|"month",
    bucket_size: "day"|"week"|"month",
    totals: {
      bucket_start, bucket_end,
      evaluations: int,       -- total recorded verdicts in window
      hits: int,               -- evaluations - passed (any non-"passed" action)
      blocked: int, masked: int, audited: int, passed: int,
      error: int, unchecked: int, budget_exceeded: int
    },
    buckets: [ { bucket_start, evaluations, hits, blocked, masked, audited, passed,
                 error, unchecked, budget_exceeded }, ... ],
    breakdown: [
      -- group_by="guardrail":     { guardrail: "prompt_injection"|"pii_mask"|"ml_moderation"|"error", <same count fields> }
      -- group_by="policy_source": { policy_source: "key"|"tenant"|"none",                              <same count fields> }
      -- group_by="key_id":        { key_id: <uuid>,                                                    <same count fields> }
    ] | null   -- null when group_by is omitted
  }
  401 -> { error: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_INVALID_TOKEN" }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  404 -> { error: "ERR_KEY_NOT_FOUND" }
  422 -> { error: "ERR_PAYLOAD_WINDOW_INVALID" | "ERR_PAYLOAD_START_DATE_INVALID"
                | "ERR_PAYLOAD_END_DATE_INVALID" | "ERR_PAYLOAD_GROUP_BY_INVALID"
                | "ERR_PAYLOAD_KEY_ID_UUID_INVALID" }

Schema:
  CREATE TABLE guardrail_verdict_events (
    id UUID PRIMARY KEY,                                    -- uuid7, mirrors request_logs/usage_records
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    key_id UUID NOT NULL,                                   -- no FK — append-only-ledger precedent
    team_id UUID NULL,                                      -- no FK — team deletion must not cascade
    guardrail TEXT NOT NULL,                                -- "prompt_injection"|"pii_mask"|"ml_moderation"|"error"
    action TEXT NOT NULL,                                   -- "blocked"|"masked"|"audited"|"passed"|"error"|"unchecked"|"budget_exceeded"
    policy_source TEXT NOT NULL,                             -- "key"|"tenant"|"none" — from AuthzResult.policy_source
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  Index (tenant_id, created_at)          -- window queries
  Index (tenant_id, guardrail, created_at)     -- group_by=guardrail
  Index (tenant_id, key_id, created_at)        -- key_id filter + group_by=key_id
  -- policy_source breakdown reuses the (tenant_id, created_at) index; low cardinality (3 values).

  Access pattern (write):
    A new fire-and-forget helper (mirrors record_audit's own-session/asyncio.create_task/swallow-all
    shape) called at every GuardrailEvent-emission point that already exists:
      - proxy/application/use_cases.py's 3 non-streaming call sites (~1772, ~1809, ~1820 — the SAME
        sites _fire_guardrail_metrics already fires from, called alongside it, not instead of it)
      - the streaming pre-call path (~2333) — a NEW call site (closes the pre-existing streaming
        metrics-coverage gap named in §0)
    Each call writes one row per GuardrailEvent in the result, with tenant_id/key_id/team_id from the
    authenticated AuthzResult already in scope (zero extra lookups) and policy_source from
    AuthzResult.policy_source (zero extra lookups — resolved once at auth time by the sibling task).

  Access pattern (read):
    SELECT date_trunc(:granularity, created_at AT TIME ZONE 'UTC') AS bucket_start,
           COUNT(*) AS evaluations,
           SUM(CASE WHEN action != 'passed' THEN 1 ELSE 0 END) AS hits,
           SUM(CASE WHEN action = 'blocked' THEN 1 ELSE 0 END) AS blocked,
           SUM(CASE WHEN action = 'masked' THEN 1 ELSE 0 END) AS masked,
           SUM(CASE WHEN action = 'audited' THEN 1 ELSE 0 END) AS audited,
           SUM(CASE WHEN action = 'passed' THEN 1 ELSE 0 END) AS passed,
           SUM(CASE WHEN action = 'error' THEN 1 ELSE 0 END) AS error,
           SUM(CASE WHEN action = 'unchecked' THEN 1 ELSE 0 END) AS unchecked,
           SUM(CASE WHEN action = 'budget_exceeded' THEN 1 ELSE 0 END) AS budget_exceeded
      FROM guardrail_verdict_events
     WHERE tenant_id = :tenant_id AND created_at >= :window_start AND created_at < :window_end
       [AND key_id = :key_id]
     GROUP BY bucket_start ORDER BY bucket_start ASC
    (mirrors get_slo's SUM(CASE ...) conditional-aggregation idiom; breakdown query is the same
     shape GROUPed BY guardrail|policy_source|key_id instead of bucket_start, ORDER BY evaluations DESC)

Illustrative shapes (syntax-checked, ast.parse-clean):

  # guardrail_analytics/infrastructure/orm.py — new module, mirrors logs/infrastructure/orm.py
  class GuardrailVerdictEventRow(Base):
      __tablename__ = "guardrail_verdict_events"
      __table_args__ = (
          Index("ix_guardrail_verdict_tenant_created", "tenant_id", "created_at"),
          Index("ix_guardrail_verdict_tenant_guardrail_created", "tenant_id", "guardrail", "created_at"),
          Index("ix_guardrail_verdict_tenant_key_created", "tenant_id", "key_id", "created_at"),
      )
      id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
      tenant_id: Mapped[uuid.UUID] = mapped_column(
          PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
      )
      key_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
      team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
      guardrail: Mapped[str] = mapped_column(Text, nullable=False)
      action: Mapped[str] = mapped_column(Text, nullable=False)
      policy_source: Mapped[str] = mapped_column(Text, nullable=False)
      created_at: Mapped[datetime] = mapped_column(server_default=func.now())

  # guardrail_analytics/application/verdict_recorder.py — mirrors audit_writer.record_audit
  async def record_guardrail_verdicts(
      session_factory: async_sessionmaker[AsyncSession],
      *, tenant_id: uuid.UUID, key_id: uuid.UUID, team_id: uuid.UUID | None,
      policy_source: str, events: list[GuardrailEvent],
  ) -> None:
      """Insert one row per event — fail-open, own session, never awaited inline by the caller."""
      if not events:
          return
      try:
          async with asyncio.timeout(2.0):
              async with session_factory() as session:
                  session.add_all([
                      GuardrailVerdictEventRow(
                          tenant_id=tenant_id, key_id=key_id, team_id=team_id,
                          guardrail=e.guardrail, action=e.action, policy_source=policy_source,
                      )
                      for e in events
                  ])
                  await session.commit()
      except Exception as exc:
          _log.warning("verdict_recorder: failed to persist (swallowed — fail-open)", exc_info=exc)

  # Call site addition (use_cases.py, alongside the existing _fire_guardrail_metrics(...) calls):
  asyncio.create_task(record_guardrail_verdicts(
      session_factory, tenant_id=authz.tenant_id, key_id=authz.key_id, team_id=authz.team_id,
      policy_source=getattr(authz, "policy_source", "none"), events=result.events,
  ))

  # guardrail_analytics/api/router.py
  guardrail_analytics_router = APIRouter(prefix="/admin/guardrails/analytics", tags=["guardrails"])

  @guardrail_analytics_router.get("", response_model=GuardrailAnalyticsResponse)
  async def get_guardrail_analytics(
      identity: Annotated[Identity, Depends(_require_ops_read)],
      session: Annotated[AsyncSession, Depends(get_session)],
      window: Annotated[str, Query()] = "month",
      key_id: Annotated[str | None, Query()] = None,
      group_by: Annotated[str | None, Query()] = None,
      start: Annotated[str | None, Query()] = None,
      end: Annotated[str | None, Query()] = None,
  ) -> GuardrailAnalyticsResponse: ...

  # migration sketch (parent = actual alembic head at build time)
  def upgrade() -> None:
      op.create_table(
          "guardrail_verdict_events",
          sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
          sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                     sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
          sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=False),
          sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
          sa.Column("guardrail", sa.Text(), nullable=False),
          sa.Column("action", sa.Text(), nullable=False),
          sa.Column("policy_source", sa.Text(), nullable=False),
          sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
      )
      op.create_index("ix_guardrail_verdict_tenant_created", "guardrail_verdict_events", ["tenant_id", "created_at"])
      op.create_index("ix_guardrail_verdict_tenant_guardrail_created", "guardrail_verdict_events", ["tenant_id", "guardrail", "created_at"])
      op.create_index("ix_guardrail_verdict_tenant_key_created", "guardrail_verdict_events", ["tenant_id", "key_id", "created_at"])

  def downgrade() -> None:
      op.drop_table("guardrail_verdict_events")
```

FREEZE QUESTIONS (Tin rules on these at the freeze — see §1 Assumptions for full reasoning):
  1. "pattern" = coarse guardrail-type field (RECOMMENDED, zero evaluator cost) vs true per-regex/per-category granularity (bigger lift, deferred as a spec delta). See §1 ⚠#1.
  2. `evaluate_post` (response-side pii_mask) verdicts: out of v1 scope (RECOMMENDED, filed as a spec delta) vs pulled into this task now (touches 4 evaluator call sites). See §1 ⚠#3.
  3. `hits` semantics = `evaluations - passed` (any non-"passed" action counts as a hit, including "error"/"unchecked"/"budget_exceeded") — cosmetic naming choice, flag if a narrower definition (e.g. only blocked+masked) is preferred.
  4. New dedicated `gateway/guardrail_analytics/` module (RECOMMENDED, mirrors payload-capture-store earning `gateway/logs/`) vs folding the read endpoint into the existing `usage/api/router.py` (where every other tenant-analytics GET already lives) — a placement/organization call, not a behavior call; either is a compatible build choice, named here so the build doesn't silently pick one without it being visible at freeze.

Glossary deltas: <new domain term(s) this task introduces, `Term: definition` — or "none">
  - NEW: `guardrail_verdict_events: an append-only, tenant-scoped ledger table — one row per pre-call
    guardrail evaluation (prompt_injection/pii_mask/ml_moderation), independent of payload-capture
    opt-in. Powers GET /admin/guardrails/analytics and the dashboard's Guardrail Analytics page.
    NOT payload-bearing (no request/response body) — lower sensitivity than request_logs.`
  - NEW: `Guardrail verdict analytics: the tenant-admin-facing aggregation of guardrail_verdict_events
    over a time window, broken out by guardrail type ("pattern"), policy_source ("policy"), or
    key_id ("key") — one dimension per query, mirrors the existing /admin/spend windowed-analytics
    shape.`
Status: DRAFT — awaiting human freeze
Reported: no — this is the design-team draft; the orchestrator renders the freeze report when Tin reviews.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

## Design self-score

- Completeness: 0.92 — every Must has a matching scenario, every Reject has a contracted error
  response + scenario, migration+ORM+recorder+router all sketched and syntax-checked; the two
  deliberate scope boundaries (per-pattern granularity, evaluate_post coverage) are named as spec
  deltas rather than silently left unaddressed.
- Clarity: 0.92 — the policy/pattern/key dimension mapping (the least obvious part of the whole
  task) is stated once precisely in M2/M4 and repeated consistently through scenarios/contract/
  glossary so it can't drift at build time.
- Practicality: 0.93 — reuses `_compute_window_bounds`, `KEY_NOT_FOUND_IN_TENANT`,
  `PAYLOAD_GROUP_BY_INVALID`/`PAYLOAD_WINDOW_INVALID`, `Permission.OPS_READ`, and
  `record_audit`'s exact write shape verbatim — the build is mostly additive plumbing over
  already-proven patterns, matching the sibling task's own practicality bar.
- Optimization: 0.9 — the write hook adds zero extra reads (policy_source and tenant/key/team ids
  are already in scope at every call site); one new table + 3 targeted indexes sized for the
  actual query shapes (window scan, per-guardrail breakdown, per-key breakdown).
- Edge cases: 0.9 — empty window, cross-tenant key_id, omitted group_by, streaming vs non-streaming
  parity, and write-failure fail-open are all covered by named scenarios; the genuinely open
  scope questions (per-pattern granularity, evaluate_post) are pushed to freeze questions rather
  than guessed.
- Self-evaluation: 0.9 — the one real judgment call with weak textual grounding (§1 ⚠#1, the
  policy/pattern/key mapping) is surfaced as the lead freeze flag with a stated alternative and
  cost, not resolved by fiat; the module-placement question (freeze question 4) is flagged as
  low-stakes rather than over-elaborated.

<!-- All six dimensions ≥0.9 — no further refinement needed before freeze. -->

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

Scope (may touch): `apps/gateway/src/gateway/guardrail_analytics/` · `apps/gateway/src/gateway/proxy/application/use_cases.py` (new recorder call sites only — no change to existing guardrail-evaluation logic) · `apps/gateway/src/gateway/main.py` (router wiring) · `apps/gateway/migrations/versions/` (one new migration) · `apps/gateway/tests/guardrail_analytics/` · `apps/dashboard/components/guardrails/` (new `GuardrailAnalyticsPage.tsx` + a sparkline component, mirrors `apps/dashboard/components/spend/`) · `apps/dashboard/components/ui/app-shell.tsx` (one new NAV_GROUPS entry) · `apps/dashboard/app/(app)/app/guardrail-analytics/` (new route) · `apps/dashboard/tests-bff/` / `apps/dashboard/tests/` (new page tests). Zero touch to `guardrail_evaluator.py`, `ml_moderation_evaluator.py`, `tenants/api/guardrail_router.py`, `keys/api/key_guardrail_router.py`, or `RequestLogRow`/`logs/` (frozen sibling contracts, per-pattern-granularity and evaluate_post coverage explicitly deferred — §1 ⚠#1/⚠#3).
Strategy (ordered batches): 1. New module `guardrail_analytics/` (domain/application/infrastructure/api, mirrors `logs/`'s layering) — start with `infrastructure/orm.py` (`GuardrailVerdictEventRow`) + the migration. 2. `application/verdict_recorder.py` (`record_guardrail_verdicts`, mirrors `record_audit`'s own-session/fail-open shape exactly). 3. Wire the recorder into `use_cases.py` at the 3 existing `_fire_guardrail_metrics` call sites (non-streaming) PLUS the streaming `evaluate_pre` call site (new coverage) — additive calls only, zero edits to existing guardrail-evaluation control flow. 4. `api/router.py` (`GET /admin/guardrails/analytics`, mirrors `usage/api/router.py:get_spend`'s window/group_by/breakdown shape + `get_slo`'s conditional-SUM idiom; reuse `_compute_window_bounds`, `KEY_NOT_FOUND_IN_TENANT`, `PAYLOAD_GROUP_BY_INVALID`/`PAYLOAD_WINDOW_INVALID`/`PAYLOAD_KEY_ID_UUID_INVALID` verbatim via import). 5. Wire the router into `main.py`. 6. Dashboard: `GuardrailAnalyticsPage.tsx` cloned from `SpendPage.tsx`'s structure (PageHeader/StatCard hero/sparkline/Tabs/DataTable), a `GuardrailSparkline.tsx` cloned from `SpendSparkline.tsx`; add the nav entry to the "Govern" `NAV_GROUPS` group; add the `/app/guardrail-analytics` route.
Known-problem fixes: streaming pre-call verdicts silently uncounted (pre-existing gap, §0) → the new streaming call site is itself the fix, added as its own Must (M1), not bundled invisibly into "mirror the non-streaming sites". `policy_source` possibly absent/stale on an `AuthzResult` built by a path that predates the sibling task (e.g. the agent-OAuth composite authenticator, confirmed in the sibling's own §0 to default `guardrail_configs` to `{}` with no `policy_source` kwarg) → `getattr(authz, "policy_source", "none")` defensive read at the recorder call site (mirrors the existing `getattr(authz, "guardrail_configs", {})` defensive pattern already used at every one of these call sites today).

Persona (required): Backend Architect (`.add/personas/backend-architect.md`) — ports/layering discipline for the new module + reused raw-`text()`-SQL admin-read precedent (matches `usage/api/router.py`'s own established shape); the dashboard slice is a near-verbatim structural clone of an existing, already-designed page (`SpendPage.tsx`), not new UI/UX design work, so no separate frontend persona is warranted for this task.
Spawn isolation (default): worktree (mirrors every sibling wave-2 build; this design pass ran directly in the shared checkout per the wave-2 brief's own no-worktree instruction for design agents).
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the verdict-write hook MUST be wrapped in a bounded `asyncio.timeout` and scheduled via `asyncio.create_task` (never `await`ed inline) so a slow/erroring insert cannot add latency to, or fail, the proxied completion response — mirrors `record_audit`'s own concurrency posture exactly (own session, separate from the request's transaction, so a request-path rollback can never lose a committed verdict row and a verdict-write failure can never roll back the request).
Code lives in: `apps/gateway/src/gateway/guardrail_analytics/` (+ `apps/dashboard/components/guardrails/` for the UI slice).
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

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
