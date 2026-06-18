# TASK: Admin reconciliation endpoint — observe a window's drift on demand

slug: reconciliation-endpoint · created: 2026-06-18 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - NEW `apps/gateway/src/gateway/usage/application/reconciliation.py:reconcile_window` (v29 t1, FROZEN @ v1) — `async reconcile_window(session, window_from, window_to, tenant_id: uuid.UUID|None=None) -> ReconciliationSummary`. This task is a THIN HTTP handler over it; `ReconciliationSummary`/`SourceBreakdown` are the result dataclasses (provider_cost_total · billed_total · drift · unbilled_upstream_cost · unbilled_rows · catalog_billed_total · by_source).
  - `apps/gateway/src/gateway/usage/api/router.py` — the sibling read endpoints this mirrors: `usage_router = APIRouter(prefix="/admin", tags=["usage"])` (line 53) · `get_spend` (lines 235-472) is the closest analog (window params → `_compute_window_bounds` → aggregate → typed response) · `_compute_window_bounds(window, start, end) -> (window_start, window_end, granularity)` (lines 139-232) REUSED verbatim (422 ProblemError on bad window/date; half-open `[start, end)`; end-inclusive via +1 day) · the asyncpg-naive bind `window_start.replace(tzinfo=None)` (line 284) — but `reconcile_window` already normalizes bounds internally via `_as_naive_utc`, so the handler passes the aware bounds straight through.
  - `apps/gateway/src/gateway/keys/api/deps.py:require_owner_or_admin` (lines 53-59) — the role gate: `Depends(get_identity)` → 401 on bad/absent Bearer, then member → 403 `AUTH_FORBIDDEN`; returns `Identity` (carries `tenant_id` + `role`). The endpoint depends on THIS (not router.py's bare `_extract_identity`, which omits the role check) so the milestone's "owner/admin-scoped" is enforced.
  - `apps/gateway/src/gateway/core/error_catalog.py` — `AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID` (401) · `AUTH_FORBIDDEN` (403 ERR_AUTH_FORBIDDEN) · `PAYLOAD_WINDOW_INVALID`/`PAYLOAD_START_DATE_INVALID`/`PAYLOAD_END_DATE_INVALID` (422 ERR_PAYLOAD_INVALID) — all already raised by the reused helpers.
  - `apps/gateway/src/gateway/usage/api/schemas.py` — the response-model pattern to mirror: `BaseModel` + `model_config = ConfigDict(frozen=True)`, money serialized as `str(Decimal)` (e.g. `SpendWindowResponse`/`SpendTotals`). NEW `ReconciliationResponse` + `ReconciliationSourceItem` added here.
  - `apps/gateway/src/gateway/main.py:647` `app.include_router(usage_router)` — the new route attaches to the EXISTING `usage_router`, so NO new registration is needed.
Context (working folder): the on-demand observation surface for v29's reconciliation metric. v29 t1 added the pure `reconcile_window` aggregate (tenant-scoped or operator-wide via `tenant_id=None`); this task exposes it over HTTP for an owner/admin to observe THEIR tenant's drift on demand. The operator-wide (all-tenants) global leak monitor is the LATER drift-alert task (t3) — a server-side scheduled job with no per-request caller to authorize.
Honors (patterns / conventions): TENANT ISOLATION is the load-bearing invariant — every existing /admin read enforces `WHERE tenant_id = :tenant_id` (get_usage, get_spend). The auth model is strictly per-tenant: `Identity.role ∈ {owner, admin, member}` is bound to ONE `tenant_id`; there is NO platform-operator role spanning tenants. So this endpoint is tenant-scoped (`tenant_id = identity.tenant_id`); exposing all-tenants drift to a single tenant's admin would be a cross-tenant data leak. CLEAN ARCHITECTURE — a thin API handler over the application-layer aggregate, no SQL of its own; money as `str(Decimal)`, never float; reuse the window/auth/error helpers, do not duplicate them.
Anchors the contract cites: `GET /admin/reconciliation` on `usage_router` · `require_owner_or_admin` (401/403) · `_compute_window_bounds` (422) · `reconcile_window(session, window_start, window_end, tenant_id=identity.tenant_id)` · NEW `ReconciliationResponse`/`ReconciliationSourceItem` schemas · the metric fields from the frozen `ReconciliationSummary`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `GET /admin/reconciliation` — an owner/admin observes THEIR tenant's reconciliation drift for a time window on demand (Σ provider_cost vs Σ billed → drift, plus the unbilled-upstream breakdown by usage_source). A thin HTTP surface over the v29 t1 `reconcile_window` aggregate.
Framings weighed: a thin handler ON THE EXISTING `usage_router` reusing `_compute_window_bounds` + `require_owner_or_admin` + `reconcile_window` (chosen — the metric is a sibling read alongside `/admin/usage` and `/admin/spend`; reuses the proven window/auth/error machinery, zero duplicated SQL, the aggregate stays the single source of truth) · a brand-new router/module (rejected — duplicates the `/admin` prefix, the Bearer auth, and the window helpers for one more read) · inline the reconciliation SQL in the handler (rejected — defeats the entire point of t1's SHARED aggregate; the milestone's shared-contract decision forbids re-deriving the metric).
Must:
<must>
  - `GET /admin/reconciliation?window=&start=&end=` (same query params + semantics as `/admin/spend`: `window` default `"month"`, optional ISO `start`/`end` overrides; half-open `[window_start, window_end)`, end-inclusive via +1 day) → owner/admin only → compute the bounds via `_compute_window_bounds` → call `reconcile_window(session, window_start, window_end, tenant_id=identity.tenant_id)` → `200 ReconciliationResponse`.
  - TENANT-SCOPED ALWAYS: `tenant_id = identity.tenant_id` — the handler NEVER passes `tenant_id=None`; one tenant's admin can only ever see their own tenant's reconciliation (the tenant-isolation invariant every sibling /admin read upholds).
  - The response carries every `ReconciliationSummary` field: `window_from`/`window_to` (ISO-8601 UTC strings), `provider_cost_total`/`billed_total`/`drift`/`unbilled_upstream_cost`/`catalog_billed_total` as `str(Decimal)` (never float), `unbilled_rows` (int), and `by_source` = list of `{usage_source, rows, provider_cost}` (provider_cost as `str(Decimal)`).
  - READ-ONLY: the handler issues no writes; an EMPTY window → `200` with explicit zeros + `[]` by_source (never 404).
</must>
Reject:
<reject>
  - missing / malformed Bearer token -> `401` `ERR_AUTH_INVALID_TOKEN` (`AUTH_TOKEN_MISSING` / `AUTH_TOKEN_INVALID`, via `get_identity`).
  - caller role = member -> `403` `ERR_AUTH_FORBIDDEN` (`AUTH_FORBIDDEN`, via `require_owner_or_admin`).
  - invalid `window` value (not day|week|month) -> `422` `ERR_PAYLOAD_INVALID` (`PAYLOAD_WINDOW_INVALID`).
  - invalid ISO `start` / `end` date -> `422` `ERR_PAYLOAD_INVALID` (`PAYLOAD_START_DATE_INVALID` / `PAYLOAD_END_DATE_INVALID`).
  - (the aggregate's inverted-window `ValueError` is NOT user-reachable here — `_compute_window_bounds` always yields `window_start < window_end` for valid inputs; it stays as defense-in-depth in the aggregate, not a contracted endpoint response.)
</reject>
After:
<after>
  - A `200 ReconciliationResponse` for the caller's tenant over `[window_start, window_end)`; the `usage_records` ledger is byte-for-byte unchanged (pure read). No other tenant's rows are ever reflected in the totals.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the OPERATOR-WIDE view: the milestone's t2 line reads "tenant-scoped + an operator-wide view", but grounding shows the auth model has NO cross-tenant platform-operator role — `Identity.role ∈ {owner,admin,member}` is bound to ONE `tenant_id`, and every sibling /admin read is strictly `WHERE tenant_id = :tid`. I am drafting TENANT-SCOPED ONLY; the all-tenants global leak monitor is the server-side drift-alert (t3), which has no per-request caller to authorize and can legitimately span tenants. If wrong (Tin wants an all-tenants endpoint view): it requires a NEW platform-operator authority (a super-admin role/claim or a separate ops-auth surface) — a security-sensitive new surface that is its own task/milestone, not a thin handler. Cost if wrong: re-scope to add that authority; but shipping a cross-tenant view authorized by a tenant-scoped JWT would be a tenant-isolation breach (a security HARD-STOP), so the safe default is tenant-scoped. [→ the freeze decision for Tin]
  - [ ] reuse `/admin/spend`'s `window`/`start`/`end` params (default `month`) rather than inventing raw `from`/`to` ISO datetime params — confirm (keeps the admin read API uniform; the aggregate takes explicit datetimes, the handler adapts via the shared `_compute_window_bounds`).
  - [ ] owner AND admin (not owner-only) may view reconciliation — matches `require_owner_or_admin` (member → 403); confirm the role floor is admin, not owner-only.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: RE1 owner sees their tenant's drift for a window
  Given an owner JWT and provider-basis rows in the window with Σ(provider_cost)=2.00, Σ(cost_usd)=3.00
  When GET /admin/reconciliation?window=month is called with that Bearer
  Then 200 and body.provider_cost_total=="2.00", body.billed_total=="3.00", body.drift=="-1.00"
  And every money field is a JSON string (str(Decimal)), not a float

Scenario: RE2 tenant isolation — another tenant's rows are never included
  Given tenant A (owner JWT, provider_cost 1.00) and tenant B (provider_cost 2.00) both have rows in the window
  When tenant A's owner calls GET /admin/reconciliation
  Then 200 and body.provider_cost_total=="1.00"   # only A's row; B is invisible
  And the response reflects no row belonging to tenant B

Scenario: RE3 an unbilled-upstream row is surfaced by usage_source
  Given an owner JWT and a provider row provider_cost=0.50, cost_usd=0, usage_source="client_disconnect" in the window
  When GET /admin/reconciliation is called
  Then 200 and body.unbilled_upstream_cost=="0.50" and body.unbilled_rows==1
  And body.by_source contains {usage_source:"client_disconnect", rows:1, provider_cost:"0.50"}

Scenario: RE4 an empty window returns 200 with zeros, never 404
  Given an owner JWT for a tenant with no usage_records in the window
  When GET /admin/reconciliation is called
  Then 200 and every money field=="0", unbilled_rows==0, by_source==[]
  And the ledger is unchanged (read-only)

Scenario: RE5 an admin (not only an owner) may view reconciliation
  Given an admin-role JWT for the tenant
  When GET /admin/reconciliation is called
  Then 200 (admin is permitted — the role floor is owner-or-admin)

Scenario: RE6 a member is forbidden
  Given a member-role JWT for the tenant
  When GET /admin/reconciliation is called
  Then 403 ERR_AUTH_FORBIDDEN
  And no reconciliation data is returned

Scenario: RE7 a missing or malformed Bearer is rejected
  Given no Authorization header (or a non-Bearer scheme)
  When GET /admin/reconciliation is called
  Then 401 ERR_AUTH_INVALID_TOKEN
  And no reconciliation data is returned

Scenario: RE8 an invalid window value is rejected
  Given an owner JWT and window="century"
  When GET /admin/reconciliation?window=century is called
  Then 422 ERR_PAYLOAD_INVALID
  And no reconciliation data is returned

Scenario: RE9 an invalid ISO start/end date is rejected
  Given an owner JWT and start="2026-13-40"
  When GET /admin/reconciliation?window=month&start=2026-13-40 is called
  Then 422 ERR_PAYLOAD_INVALID
  And no reconciliation data is returned

Scenario: RE10 catalog rows are reported separately, never folded into drift
  Given an owner JWT, provider rows (Σ provider_cost 1.00 / Σ billed 1.20) and catalog rows (Σ billed 4.00) in the window
  When GET /admin/reconciliation is called
  Then 200 and body.drift=="-0.20" (provider-only) and body.catalog_billed_total=="4.00"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/reconciliation        (on usage_router; Authorization: Bearer <JWT>, owner|admin)
  query: window=day|week|month (default "month") · start=YYYY-MM-DD? · end=YYYY-MM-DD?
         (identical semantics to GET /admin/spend: half-open [window_start, window_end),
          end-inclusive via +1 day; bounds via the shared _compute_window_bounds)

  200 -> ReconciliationResponse {
           window_from: str            # ISO-8601 UTC, inclusive
           window_to: str              # ISO-8601 UTC, exclusive
           provider_cost_total: str    # str(Decimal) — Σ provider_cost over cost_basis='provider'
           billed_total: str           # str(Decimal) — Σ cost_usd     over cost_basis='provider'
           drift: str                  # str(Decimal) — provider_cost_total − billed_total (healthy < 0)
           unbilled_upstream_cost: str # str(Decimal) — Σ provider_cost where provider_cost>0 AND cost_usd=0
           unbilled_rows: int          # COUNT(*) of those rows
           catalog_billed_total: str   # str(Decimal) — Σ cost_usd over cost_basis='catalog' (never in drift)
           by_source: [ ReconciliationSourceItem {
               usage_source: str       # frame | stream_fallback | client_disconnect
               rows: int
               provider_cost: str      # str(Decimal) — Σ provider_cost of unbilled rows with this source
           } ]                         # unbilled rows grouped by usage_source, sorted; [] when none
         }
  401 -> problem+json ERR_AUTH_INVALID_TOKEN   (missing / malformed Bearer)
  403 -> problem+json ERR_AUTH_FORBIDDEN        (role = member)
  422 -> problem+json ERR_PAYLOAD_INVALID       (bad window value, or bad ISO start/end date)

Handler: identity = require_owner_or_admin(...);  (window_start, window_end, _) = _compute_window_bounds(window, start, end);
         summary = await reconcile_window(session, window_start, window_end, tenant_id=identity.tenant_id);
         return ReconciliationResponse(... str(Decimal) money ...).
Scope: ALWAYS tenant_id = identity.tenant_id (never None / never cross-tenant).
Schema: READS usage_records only (via reconcile_window). NO write · NO migration · NO new table/column ·
        NO new dependency. NEW Pydantic models ReconciliationResponse / ReconciliationSourceItem
        (frozen, money as str) in usage/api/schemas.py. Route attaches to the already-registered usage_router.
Invariants: owner/admin-only · tenant-scoped read · half-open [from,to) window · Decimal-as-str money ·
            empty window → 200 zeros (never 404) · ledger unchanged · catalog never folded into drift.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-18, via AskUserQuestion: "Tenant-scoped + add operator view").
Least-sure flag surfaced at freeze: [spec] the OPERATOR-WIDE view. The milestone t2 line said "tenant-scoped
+ an operator-wide view", but grounding shows the auth model has NO cross-tenant platform-operator role
(`Identity.role ∈ {owner,admin,member}` bound to one `tenant_id`; every sibling /admin read is `WHERE
tenant_id=:tid`). So this endpoint is TENANT-SCOPED ONLY — an all-tenants view authorized by a tenant-scoped
JWT would breach tenant isolation (a security HARD-STOP). Tin approved tenant-scoped NOW **plus a SEPARATE
follow-up** to add a cross-tenant operator-wide view behind a NEW platform-operator authority (a super-admin
role/claim or an ops-auth surface) — seeded as a SPEC delta (§7), NOT built in this thin handler; placement
(v29 extra vs a later milestone) TBD. The global all-tenants leak monitor remains the server-side drift-alert
(t3). Cost if wrong: a new auth surface + its own task, not a relabel.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new `get_reconciliation` handler + `ReconciliationResponse`/`ReconciliationSourceItem` (whole-suite ≥80%).
Plan (one test per scenario RE1–RE10; integration via the real-DB `client`/`db_session`/`app` fixtures — sign up a tenant, seed `usage_records` rows directly with controlled created_at/cost/basis/source, call the endpoint with a Bearer, assert the JSON body / status). Admin & member tokens are minted via `app.state.token_service.issue(user_id, tenant_id, role=Role.ADMIN|MEMBER, email)` after a direct `users` insert — the proven same-tenant-role pattern from team_governance):
<test_plan>
  - test_re1_owner_drift: seed provider rows (Σ pcost 2.00 / Σ cost 3.00) / GET with owner Bearer / assert 200, provider_cost_total=="2.00", billed_total=="3.00", drift=="-1.00", money fields are JSON strings.
  - test_re2_tenant_isolation: seed tenant A (pcost 1.00) + tenant B (pcost 2.00) / GET as A's owner / assert 200, provider_cost_total=="1.00" (B invisible).
  - test_re3_unbilled_by_source: seed provider row pcost 0.50 ∧ cost 0, source client_disconnect / GET / assert unbilled_upstream_cost=="0.50", unbilled_rows==1, by_source has that entry.
  - test_re4_empty_window_zeros: no rows / GET / assert 200, all money=="0", unbilled_rows==0, by_source==[].
  - test_re5_admin_allowed: mint admin JWT (same tenant) / GET / assert 200.
  - test_re6_member_forbidden: mint member JWT (same tenant) / GET / assert 403 ERR_AUTH_FORBIDDEN.
  - test_re7_no_bearer_401: GET with no/garbage Authorization / assert 401 ERR_AUTH_INVALID_TOKEN.
  - test_re8_bad_window_422: owner Bearer, window=century / assert 422 ERR_PAYLOAD_INVALID.
  - test_re9_bad_date_422: owner Bearer, start=2026-13-40 / assert 422 ERR_PAYLOAD_INVALID.
  - test_re10_catalog_separate: seed provider rows (drift -0.20) + catalog rows (Σ billed 4.00) / GET / assert drift=="-0.20", catalog_billed_total=="4.00".
</test_plan>

Tests live in: `apps/gateway/tests/reconciliation_endpoint/` · MUST run red (missing `get_reconciliation` route → 404, and missing response schemas) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/api/router.py` `apps/gateway/src/gateway/usage/api/schemas.py` `apps/gateway/tests/reconciliation_endpoint/`
Strategy (ordered batches): 1. add `ReconciliationSourceItem` + `ReconciliationResponse` (frozen BaseModel, money as `str`) to `usage/api/schemas.py`. 2. add the `get_reconciliation` route to `usage_router` in `usage/api/router.py` — `Depends(require_owner_or_admin)` (401/403) → `_compute_window_bounds(window, start, end)` (422) → `await reconcile_window(session, window_start, window_end, tenant_id=identity.tenant_id)` → map the `ReconciliationSummary` to `ReconciliationResponse` (ISO window strings, `str(Decimal)` money, `by_source` items). 3. red RE1–RE10 → green.
Safety rule (feature-specific): TENANT-SCOPED ALWAYS — `tenant_id = identity.tenant_id`, the handler NEVER passes `tenant_id=None`; READ-ONLY (the aggregate is SELECT-only); money stays `Decimal`, serialized as `str` at the edge (no float).
Code lives in: `apps/gateway/src/gateway/usage/api/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — focused RE1–RE10 **10 passed** (`tests/reconciliation_endpoint`); full regression suite **1204 passed** (1194 prior + 10 new), exit 0, `--ignore=tests/edge` (live-stack). Re-confirmed green after the post-refute test strengthening.
- [x] coverage did not decrease — the new `get_reconciliation` handler + `ReconciliationResponse`/`ReconciliationSourceItem` are exercised by all 10 scenarios (drift, tenant-isolation, unbilled-by-source, empty→zeros, admin-allowed, member-403, no-bearer-401, bad-window-422, bad-date-422, catalog-separate). pyright clean on both src files (0 errors).
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched. Test edits happened in the TESTS phase (formatting) and post-refute STRENGTHENING (added window-bound + by_source-sort assertions to RE1/RE3 — never weakened); BOTH were honestly re-snapshotted via a sanctioned re-cross (tests→build→verify), so the tamper tripwire is clean. The only build-phase src edits were the handler + schemas (declared scope) and a comment/docstring lint fix (E501 + ambiguous-minus, no behavior).
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet, XML-prompted, app-sec + multi-tenant-billing persona) returned **EARNED-WITH-NITS · confidence 0.88 · BLOCKERS: none · TENANT-ISOLATION: CONFIRMED-HOLDS**. Two contract-pinning NITs (window-bound values, by_source sort) were CLOSED by strengthening RE1/RE3; the other two are theoretical (Pydantic str-typing already 500s on a float; drift arithmetic already discriminates) — dispositioned in §7. Asserts run against a real seeded Postgres ledger over HTTP (no mocks).
- [x] concurrency / timing — READ-ONLY request path: the handler issues no writes; `reconcile_window` is two SELECT-only aggregates over the append-only ledger. No shared mutable state, no lock. Auth/window validation raise before the read.
- [x] no exposed secrets, injection openings, or unexpected dependencies — all SQL binds are parameterized (`:tid`/`:from`/`:to`); the handler interpolates NO user text into SQL. No new dependency (reuses `require_owner_or_admin`, `_compute_window_bounds`, `reconcile_window`, SQLAlchemy `text()`). The role gate (member→403) + Bearer gate (401) are enforced by the `require_owner_or_admin` dependency.
- [x] layering & dependencies follow CONVENTIONS.md — a thin API-layer handler over the application-layer aggregate; no SQL of its own; money `str(Decimal)` at the edge, never float; the response models mirror the sibling `SpendWindowResponse` (frozen BaseModel). usage/api importing keys/api/deps + usage/application is consistent with the existing /admin read endpoints.
- [x] a person reviewed and approved the change — Tin approved the §3 freeze AND the tenant-scoped+operator-follow-up scope decision (2026-06-18, via AskUserQuestion). `autonomy: auto`; auto-resolved on complete evidence (refute-read no-blockers, full suite green). **TENANT ISOLATION is the security-critical property and the refute-read CONFIRMED it holds** (handler passes a non-None `identity.tenant_id`; the aggregate always applies `AND tenant_id=:tid`; RE2 is a discriminating probe — 1.00 not 3.00); no security residue escalates this gate.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `get_reconciliation` is registered on `usage_router` (already `include_router`-ed at main.py:647), proven live by RE7's 401 (route exists; a missing route 404s). `ReconciliationResponse`/`ReconciliationSourceItem` are imported + used by the handler and returned as `response_model`; `require_owner_or_admin`/`reconcile_window` are imported and called.
- [x] DEAD-CODE (code) — no orphaned symbol: every response field is populated from the summary and asserted; no leftover scaffolding; the unused `_granularity` from `_compute_window_bounds` is intentionally discarded (reconciliation has no buckets) and named with a leading underscore.
- [x] SEMANTIC (prose / non-code) — n/a (code task; the WIRING + adversarial refute-read paths apply).

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (autonomy: auto · refute-read 0.88 no-blockers, tenant-isolation CONFIRMED · §3 freeze + scope decision approved by Tin) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 403/401 rate on /admin/reconciliation (auth-gate health) · 422 rate (bad window/date params) · per-tenant drift sign + unbilled_rows>0 (the on-demand leak signal an admin would act on).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · seeded] operator-wide-reconciliation-view: a cross-tenant (all-tenants) reconciliation endpoint view behind a NEW platform-operator authority (super-admin role/claim or a separate ops-auth surface) — the milestone t2 line asked for it but the per-tenant auth model can't authorize it without a new authority; Tin approved deferring it to its own security-sensitive task; placement TBD (v29 extra vs a later milestone) (evidence: grounding found no cross-tenant role; freeze flag, MILESTONE.md operator-view note).
  - [SPEC · seeded] drift-alert (v29 t3): the remaining milestone task — a periodic server-side check that consumes `reconcile_window` operator-wide (no per-request caller, so it CAN span tenants) and fires ONE deduped alert via the existing alert_events/webhook seam when drift exceeds `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD`; recommend triggering on the markup-free `unbilled_upstream_cost` (evidence: this endpoint covers exit-criteria 1+2; criterion 3 is the alert).
  - [SPEC · open] add `window_from`/`window_to` value assertions everywhere they matter and a 6-money-field `isinstance(str)` sweep — RE1 now pins the window bounds + drift-as-str, but the str-type pin on the other 5 money fields rests on Pydantic's str-typed response_model (a float would 500 before the body) rather than an explicit assert (evidence: refute-read NIT-2, theoretical only).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
  - [TDD · open] a thin HTTP handler over a frozen aggregate is best tested at the EDGE over real HTTP (route-exists proven by the 401, tenant-isolation by a discriminating 1.00-not-3.00 assert) — minting same-tenant admin/member tokens via `app.state.token_service.issue(...role=...)` after a direct users insert is the reusable role-gate test pattern (evidence: RE2/RE5/RE6/RE7; team_governance precedent).
  - [ADD · open] a milestone task line can over-promise against the real auth model — "operator-wide view" assumed a cross-tenant authority that doesn't exist; grounding (§0) caught it BEFORE the contract, turning it into a freeze decision + a seeded follow-up rather than a tenant-isolation breach (evidence: the freeze flag; the security-correct default chosen over the literal milestone text).
  - [ADD · open] sibling lint debt surfaced: v29 t1 `reconciliation.py` ships 5 ruff findings (E501 ×2, RUF003 ambiguous `−`, UP017 `datetime.UTC`, S608 false-positive on the static tenant_clause) — out of THIS task's scope to fix, but a `chore(lint)` follow-up should clean it (and prefer ASCII `-`/`datetime.UTC`/`# noqa: S608` on static-literal SQL in new ledger reads) (evidence: `ruff check` on the t1 file during this verify).
