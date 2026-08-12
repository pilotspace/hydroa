# TASK: Guardrail verdict analytics API + dashboard view

slug: guardrail-analytics · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
autonomy: auto
phase: done

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

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
Decided at freeze (orchestrator auto-mode, 2026-07-10; non-security data task): (1) counts come from a
NEW append-only `guardrail_verdict_events` table, NOT aggregation over `request_logs` (which exists only
for capture-opt-in tenants → would silently zero-out everyone else). (2) "pattern" = the coarse
`guardrail` field (prompt_injection/pii_mask/ml_moderation); true per-regex granularity deferred as an
additive field. (3) response-side `evaluate_post` pii_mask verdicts OUT of v1 — named spec delta.

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
Status: DRAFT
Reported: no — this is the design-team draft; the orchestrator renders the freeze report when Tin reviews.

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

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

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

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 20/20 `apps/gateway/tests/guardrail_analytics` (targeted, isolated `gateway_test_vga`), 6/6 `apps/gateway/tests/migrations`, 69/69 sibling suites sharing the same `use_cases.py` call sites (`payload_capture` + `guardrails` + `request_log_metering_fields`), 5/5 dashboard `tests-bff/guardrail-analytics.test.tsx`
- [x] coverage did not decrease — targeted-suite run only (full-suite coverage not re-run here per the wave-2 brief's "targeted suites only" rule); pyright 0 errors / ruff clean on every touched+new file
- [x] no test or contract was altered during build — §3 CONTRACT block byte-identical to the frozen text; no test file diff beyond the task's own new `tests/guardrail_analytics/`
- [x] the green was EARNED, not gamed — see Refute-read verdict below
- [x] concurrency / timing of the risky operation is safe — see Refute-read + Advisor verdict below
- [x] no exposed secrets, injection openings, or unexpected dependencies — `group_by` is whitelist-validated (`_VALID_GROUP_BY`) BEFORE its value is ever interpolated into the f-string SQL (router.py:141-144, before the SQL built at :161/:209); `tenant_id` in every query comes from server-side `identity.tenant_id`, never from a query param
- [x] layering & dependencies follow CONVENTIONS.md — new `guardrail_analytics/{domain,application,infrastructure,api}` module mirrors `logs/`'s layering exactly; zero touch to frozen sibling modules (`guardrail_evaluator.py`, `ml_moderation_evaluator.py`, `tenants/api/guardrail_router.py`, `keys/api/key_guardrail_router.py`) confirmed via diff
- [ ] a person reviewed and approved the change — pending Tin's review of this VERIFY record

### Build expectations — what "correct" looks like
- [x] A `guardrail_verdict_events` row is written for BOTH streaming and non-streaming completions, independent of payload-capture opt-in — confirmed by `test_nonstream_verdict_recorded_independent_of_capture` + `test_stream_verdict_recorded_closes_metrics_gap` (both pass, real Postgres, real HTTP)
- [x] A verdict-recorder failure (DB error/timeout) NEVER fails or slows the proxied completion — confirmed by `test_verdict_write_failure_never_fails_proxied_request` (monkeypatches `record_guardrail_verdicts` itself to raise; asserts HTTP 200 + zero verdict rows) AND `test_record_guardrail_verdicts_unit_swallows_db_failure` (unit-level, raising session, asserts no exception propagates) — two independent layers both verified, not just the happy path
- [x] Fire-and-forget task is never orphaned/leaked and its exception is never "never retrieved" — confirmed by reading `_dispatch_guardrail_verdicts` (use_cases.py:644-677): `asyncio.ensure_future(...)` result is captured in `task`, `task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)` retrieves (marks-consumed) any exception without re-raising — same idiom `_dispatch_capture` already uses
- [x] Exactly ONE verdict-dispatch call executes per request (no double-count across retry/cache branches) — confirmed by code read: `evaluate_pre` is called exactly once per `complete()`/`stream()` invocation, its 3 call sites (error+block / error+fail-open / success) are mutually exclusive branches of the same try/except/if, and `_run_diverted_fallback`/upstream retries occur strictly AFTER the guardrail block — mirrors the pre-existing `_fire_guardrail_metrics` branching 1:1
- [x] `policy_source` on the recorded row matches `AuthzResult.policy_source` resolved once at auth time (zero extra IO) — confirmed by `test_policy_source_recorded_from_authz_result` (pass) and code read (`getattr(authz, "policy_source", "none")`, defensive default for auth paths that predate the sibling task)
- [x] `GET /admin/guardrails/analytics` is tenant-scoped and OPS_READ-gated; unknown `group_by` -> 422; cross-tenant `key_id` -> 404 with zero data leak — confirmed by `test_tenant_isolation_no_leak`, `test_cross_tenant_key_id_filter_returns_404`, `test_invalid_group_by_rejected`, `test_member_forbidden` (all pass) + manual attack: traced that `group_by` is checked against a fixed 3-value whitelist BEFORE being placed into the SQL text, so it can never be coerced into a cross-tenant SUM or injected
- [x] Counts come from `guardrail_verdict_events`, not `request_logs` (capture-OFF tenants still counted) — confirmed by `test_nonstream_verdict_recorded_independent_of_capture` explicitly asserting no `request_logs` row exists for the call while the verdict row does
- [x] Migration applies/reverts cleanly on the current alembic head with no branching — confirmed via `alembic heads` (single head `69cfdc584129`) and 6/6 `tests/migrations` (upgrade-from-empty parity, autogenerate-empty-diff, idempotent-second-upgrade, clean downgrade, parity-gate-red)

### Deep checks
- [x] WIRING (code) — `guardrail_analytics_router` included in `main.py:1274`; `GuardrailVerdictEventRow` import registers the table on `Base.metadata` (`main.py:92`, `noqa: F401` documented); nav entry wired in `app-shell.tsx:101-107` (Govern group, `minRole: "admin"`); dashboard route at `apps/dashboard/app/(app)/app/guardrail-analytics/page.tsx` — all confirmed present and referenced, ruff/pyright clean (0 findings)
- [x] DEAD-CODE (code) — no orphaned symbol found; every new schema class (`GuardrailAnalyticsResponse`, `*BreakdownItem`) is consumed by either `router.py` or `GuardrailAnalyticsPage.tsx`'s mirrored TS interfaces
- [x] SEMANTIC (prose) — §0/§1/§3 read in full; the two named scope boundaries (coarse-pattern mapping §1⚠#1, `evaluate_post` out-of-v1 §1⚠#3) are correctly NOT implemented (zero touch to `guardrail_evaluator.py`/`ml_moderation_evaluator.py` confirmed by diff) — matches the frozen decision, not silently narrower or wider

### Live-verify evidence
- [x] Every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by successful imports (`pyright` 0 errors on `router.py` importing `_compute_window_bounds`/`_require_ops_read` from `usage/api/router.py`, `KEY_NOT_FOUND_IN_TENANT`/`PAYLOAD_GROUP_BY_INVALID`/`PAYLOAD_KEY_ID_UUID_INVALID` from `core/error_catalog`) and by the full test suite exercising every cited symbol at runtime (all pass)
- [x] No anchor moved/renamed since Ground SHA `443a33a` — one COSMETIC drift found and named, not silent: the migration file's own docstring header says "Revises: a1c5e7f9b3d6" but the actual `down_revision` variable (and `alembic history`) correctly chain from `b3d8e1f4a7c2` (an intervening `tenant_domain_claims` migration landed between design and build) — functionally correct (alembic reads the variable, not the docstring; 6/6 migrations tests pass, single head confirmed), but the stale comment should be fixed in a follow-up touch — 💭 note, not a blocker

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: self (add-verify, appsec-engineer + sre-reliability-engineer lenses) · adversarially checked:
  (1) fail-open guarantee at BOTH layers — monkeypatched `record_guardrail_verdicts` itself (not just its internals) to raise, confirming the OUTER `asyncio.ensure_future` + done-callback dispatch in `use_cases.py` swallows it independent of the recorder's own try/except (defense-in-depth, not a single point of failure);
  (2) cross-tenant leak on `group_by` — traced that the group_by string is whitelist-validated against a fixed 3-value set BEFORE being placed in the f-string SQL (router.py:141-144 precedes :161/:209), so it can never be coerced into a cross-tenant SUM or arbitrary-column read, and confirmed with a live cross-tenant HTTP call (`test_tenant_isolation_no_leak`) seeding 10 real rows for tenant A and asserting tenant B sees exactly zero;
  (3) double-counting across the streaming/non-streaming split and the 3-way error/block/success branching — read the full control flow in both `complete()` and `stream()`, confirmed the 3 `_dispatch_guardrail_verdicts` call sites per path are mutually exclusive branches of one try/except/if (never more than one fires per request), and that no outer retry wrapper re-invokes `complete()`/`stream()`.
  All three held — no bypass found.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self (add-verify)
1. Security: CLEAR — tenant-scoping enforced server-side on every query (`identity.tenant_id`, never a query param); `group_by` whitelist precedes SQL construction (no injection); cross-tenant `key_id` returns the same 404 `ERR_KEY_NOT_FOUND` as unknown-id (no existence-oracle); OPS_READ gate reused verbatim and confirmed by `test_member_forbidden`/`test_missing_token_rejected`. New table is NOT payload-bearing (counts/labels only).
2. Concurrency: CLEAR — fire-and-forget write is fail-open at two independent layers (recorder's own try/except + the outer done-callback), bounded by `asyncio.timeout(2.0)`, uses its own DB session (never shares/rolls back the request's transaction), and never retried (correct choice for a best-effort write under a struggling store). No new task-leak: `task` reference is kept and its exception explicitly retrieved via the done callback.
3. Architecture: CLEAR — new `guardrail_analytics/` module cleanly layered (domain-free here since no new domain logic beyond the ORM row; application/infrastructure/api mirror `logs/`); zero edits to frozen sibling modules; `use_cases.py` touched only with additive call sites mirroring the existing `_fire_guardrail_metrics` pattern 1:1, confirmed by 69/69 passing in the sibling suites that share those same call sites (payload_capture, guardrails, request_log_metering_fields).
Verdict: PASS
Residue: 1 cosmetic — migration file's docstring header revision-id comment is stale (says `a1c5e7f9b3d6`, actual parent is `b3d8e1f4a7c2`); functionally inert (alembic reads the code variable), recommend a trivial follow-up fix, not gate-blocking.
Binding: advisory — non-security data task (per §3 freeze decision: "non-security data task")

### GATE RECORD
Reported: yes — this VERIFY record is the gate report
Outcome: PASS
Reviewed by: add-verify (adversarial pass) · date: 2026-07-11

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose **A — a NEW append-only `guardrail_verdict_events` table, written via a fire-and-forget hook at verdict-emission time (mirrors `record_audit`'s own-session/swallow-all pattern), independent of payload-capture opt-in; read via a new windowed aggregation admin API mirroring `get_spend`'s `date_trunc` + `group_by` shape**; rejected B — aggregate over `request_logs.guardrail_verdict` (rejected: that column is reserved/unpopulated by the sibling payload-capture-store's own frozen contract, AND `request_logs` rows only exist for tenants/keys with capture opted IN — guardrails evaluate on 100% of governed traffic regardless of capture, so this source would silently under-report to zero for every capture-off tenant, exactly the tenants most likely to rely on masking/blocking working invisibly) · C — proxy the existing Prometheus `guardrail_events_total` counter through a new admin endpoint (rejected: no tenant_id/key_id label — adding one is a cardinality-unsafe change to an operator-facing metric — and it would introduce a new Prometheus-query dependency into the tenant-facing admin API that nothing else in this codebase's admin surface does).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by add-verify (adversarial pass))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

