# PLAN: Tenant-scoped OpenAI-wire usage/costs read API over usage_records

slug: tenant-usage-costs-api · created: 2026-07-24 · stage: production
milestone: api-surface-parity
sensitivity: data
autonomy: auto
component: gateway
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: An OpenAI-organization-usage-style, **API-key-authenticated** read surface —
`GET /v1/organization/usage/completions` and `GET /v1/organization/costs` — returning
time-bucketed token/cost series over the existing append-only `usage_records` ledger,
scoped hard to the calling key's tenant, keyset-paginated, filterable by model / key / group_by.

Framings weighed:
- **OpenAI-wire /v1/organization + API-key auth (CHOSEN)** — the genuine gap. Live grounding
  shows `/admin/usage` + `/admin/spend` ALREADY deliver JWT-authed windowed buckets + group_by
  over `usage_records` (`usage/api/router.py:get_spend`). What is ABSENT (milestone Ground: "no
  tenant-facing usage API anywhere") and what exit-criterion #6 demands ("pull daily token/cost
  series … **with an API key**") is the OpenAI SDK wire (`client.usage`/admin usage) — a `/v1/*`
  path authenticated by the SAME `sk-` key that makes inference calls, not a browser-session JWT.
  This is a NEW auth seam + NEW wire dialect over the same ledger, NOT a duplicate of `/admin/spend`.
- `/admin/spend` extension (rejected) — would bolt an OpenAI wire onto a FROZEN JWT-authed contract
  and still not be API-key-authenticated; violates its frozen shape and misses the SDK-usability point.
- New aggregate table / rollup (rejected) — no new billing mechanism (milestone rule); a pure read
  over the existing ledger + its existing `(tenant_id, created_at DESC, id DESC)` index suffices.

Must:
<must>
  - M1 `GET /v1/organization/usage/completions?start_time=<unix>&bucket_width=1d` returns the OpenAI
    page envelope `{object:"page", data:[{object:"bucket", start_time, end_time, results:[…]}], has_more, next_page}`,
    buckets ordered start_time ASC, each populated bucket's result carrying `input_tokens` (=SUM prompt_tokens),
    `output_tokens` (=SUM completion_tokens), `num_model_requests` (=COUNT(*)), all scoped to the caller's tenant.
  - M2 `GET /v1/organization/costs?start_time=<unix>` returns the same page/bucket envelope; each
    result carries `amount:{value:<billed cost_usd>, currency:"usd"}` — **billed `cost_usd` ONLY**,
    never `provider_cost`/`cost_basis`/markup internals. Aggregation is NUMERIC-exact; the wire `value`
    is a JSON number rendered from the exact Decimal at the edge (no float arithmetic in the SUM).
  - M3 Hard tenant scoping: EVERY query ANDs `tenant_id = :caller_tenant`; a caller can never observe
    another tenant's rows — proven by a two-tenant seeded isolation test (§4).
  - M4 `group_by=model` (completions & costs) and `group_by=api_key_id` (completions) split each bucket's
    `results` into one entry per group, echoing the group key (`model` / `api_key_id` / `line_item`);
    ungrouped ⇒ one aggregate result per bucket.
  - M5 Filters `models=<csv>` and `api_key_ids=<csv>` narrow the scan; they are ALWAYS intersected
    with `tenant_id = :caller_tenant`, so a foreign or unknown id contributes zero rows (see R7).
  - M6 `bucket_width` ∈ {`1m`,`1h`,`1d`} for completions (default `1d`) and {`1d`} for costs (default `1d`);
    buckets computed via `date_trunc('minute'|'hour'|'day', created_at)`.
  - M7 Keyset pagination: `limit` buckets per page (default 7, 1..180); `has_more` true when more
    populated buckets exist; `next_page` an opaque cursor over the last returned bucket start_time;
    following the cursor returns the next page with no overlap/gap.
  - M8 `start_time` inclusive lower bound (Unix seconds), `end_time` exclusive upper bound (default now);
    only populated buckets are emitted (empty buckets are omitted — bounds work under huge spans).
  - M9 Rides the EXISTING Envoy `/v1/` ext_authz route (infra/envoy/envoy.yaml:206) and re-authenticates
    in-app via the existing `AuthzUseCase` (the same seam `/v1/chat|images|embeddings` use); ZERO new
    Envoy route, ZERO new table, ZERO migration.
</must>
Reject:
<reject>
  - R1 missing / malformed / unknown / revoked API key -> 401 "ERR_AUTH_INVALID_KEY"   (existing AUTH_KEY_INVALID)
  - R2 expired API key -> 401 "ERR_AUTH_KEY_EXPIRED"                                    (existing AUTH_KEY_EXPIRED)
  - R3 start_time absent / non-integer / negative -> 422 "ERR_PAYLOAD_INVALID"          (USAGE_START_TIME_INVALID)
  - R4 end_time non-integer or <= start_time -> 422 "ERR_PAYLOAD_INVALID"               (USAGE_END_TIME_INVALID)
  - R5 bucket_width not allowed for the endpoint (e.g. 1w, or 1h on /costs) -> 422 "ERR_PAYLOAD_INVALID" (USAGE_BUCKET_WIDTH_INVALID)
  - R6 group_by contains an unknown field -> 422 "ERR_PAYLOAD_INVALID"                  (USAGE_GROUP_BY_INVALID)
  - R7 requested span exceeds the per-bucket_width cap (1m>7d · 1h>92d · 1d>366d) -> 422 "ERR_PAYLOAD_INVALID" (USAGE_RANGE_TOO_LARGE)
  - R8 malformed `page` cursor -> 422 "ERR_PAYLOAD_INVALID"                             (USAGE_PAGE_INVALID)
  - R9 limit non-integer or outside 1..180 -> 422 "ERR_PAYLOAD_INVALID"                 (USAGE_LIMIT_INVALID)
  - R-FILTER a cross-tenant / nonexistent `api_key_ids` value is NEVER 404 — it yields 200 with that
    id contributing zero rows (anti-enumeration: a 404-vs-empty distinction is an existence oracle;
    sso-login-oracle-closure lesson). The row-level `tenant_id` AND already makes it a non-leak.
</reject>
After:
<after>
  - A read-only 200 page envelope is returned; NO usage_record is written, NO spend counter moves,
    the ledger is byte-identical before and after (append-only, read path only).
</after>
Boundary: timestamps are **Unix seconds as integers** on the wire (start_time/end_time), converted to
naive-UTC `datetime` for the asyncpg query exactly as `/admin/spend` does (`.replace(tzinfo=None)`);
tests speak Unix-second integers. The `page` cursor is an opaque base64 token (tests treat it as opaque —
capture from response, replay verbatim). group_by / models / api_key_ids are comma-separated query values.
<assumptions>
  ⚠ AUTH MODEL — I assume ANY active, non-revoked, non-expired tenant API key may read that tenant's
    AGGREGATE usage/costs (Hydroa keys carry no role/scope flag — roles live only on JWT identities).
    Lowest confidence because it means any key holder sees tenant-wide spend, not just their own key's.
    If wrong: a real leak of tenant-internal cost data to a low-trust key holder → the fix is a new
    per-key `usage_read` scope flag (a new mechanism), so this is the decision most likely to be
    revisited at freeze. Mitigation already in: it is still hard tenant-scoped (never cross-tenant),
    and `api_key_ids=<self>` lets a caller narrow to its own key.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Grounding (reasoned in-context; anchors are the ONLY symbols the Contract may cite)

Anchors (live-confirmed this session, [OBSERVED]):
- `gateway.usage.infrastructure.orm.UsageRecordRow` — append-only ledger; columns used:
  `tenant_id`, `key_id`, `model_id`, `prompt_tokens`, `completion_tokens`, `cost_usd` Numeric(14,8),
  `status`, `created_at`. Index `usage_records_tenant_created_id_idx (tenant_id, created_at DESC, id DESC)`
  backs the tenant+range scan — NO new index needed.
- `gateway.usage.api.router.get_spend` + `_compute_window_bounds` — the PROVEN Decimal-exact,
  tenant-scoped `date_trunc` + `NUMERIC SUM` bucket-aggregation pattern I mirror (different wire, same math).
- `gateway.proxy.api.deps.get_raw_api_key` — Bearer-token extractor (reused verbatim).
- `gateway.proxy.infrastructure.key_authenticator.SqlAlchemyKeyAuthenticator` /
  `gateway.keys.application.use_cases.AuthzUseCase.execute` → `gateway.keys.domain.entities.AuthzResult`
  (`tenant_id`, `id`, `expires_at`, `revoked_at`, `zdr_enabled`); already raises `InvalidApiKeyError`
  on revoked/expired/unknown → the 401 seam for R1/R2 (no re-implementation of key checks).
- `gateway.core.error_catalog` (`AUTH_KEY_INVALID`, `AUTH_KEY_EXPIRED`, `PAYLOAD_INVALID` shape) —
  new `USAGE_*` constants (all HTTP 422, code `ERR_PAYLOAD_INVALID`, distinct messages) are added
  mirroring the existing `PAYLOAD_START_DATE_INVALID`-style family.
- `infra/envoy/envoy.yaml` route `prefix: "/v1/"` (l.206) with ext_authz `route: "v1"` — new paths
  ride it automatically; `/admin/` and `/v1/realtime/` blocks are untouched.
- Layering precedent (backend-architect persona): `usage/domain/` (projection dataclasses + a
  `typing.Protocol` port, zero infra imports) · `usage/application/` (a use-case `execute()`) ·
  `usage/infrastructure/` (SQLAlchemy aggregation repo implementing the port) · `usage/api/` (thin router).

Ground SHA: (engine stamps at freeze)

### Contract (freeze the external shape — HARD, tamper-guarded)

```
GET /v1/organization/usage/completions
  query: start_time:int(req,inclusive,unix-s) · end_time:int(opt,exclusive,default now) ·
         bucket_width:enum{1m,1h,1d}=1d · group_by:csv⊆{model,api_key_id} ·
         models:csv · api_key_ids:csv · limit:int[1..180]=7 · page:str(opaque cursor)
  auth : Authorization: Bearer <sk-…>  (in-app AuthzUseCase → AuthzResult.tenant_id)
  200 -> {
    object: "page",
    data: [ { object:"bucket", start_time:int, end_time:int,
              results: [ { object:"organization.usage.completions.result",
                           input_tokens:int, output_tokens:int, num_model_requests:int,
                           model:str|null, api_key_id:str|null } ] } ],
    has_more: bool, next_page: str|null }
  401 -> { error: "ERR_AUTH_INVALID_KEY" | "ERR_AUTH_KEY_EXPIRED" }
  422 -> { error: "ERR_PAYLOAD_INVALID" }   # R3–R9 (distinct catalog message per case)

GET /v1/organization/costs
  query: start_time:int(req) · end_time:int(opt) · bucket_width:enum{1d}=1d ·
         group_by:csv⊆{line_item} · api_key_ids:csv · limit:int[1..180]=7 · page:str
  auth : Authorization: Bearer <sk-…>
  200 -> {
    object: "page",
    data: [ { object:"bucket", start_time:int, end_time:int,
              results: [ { object:"organization.costs.result",
                           amount:{ value:number, currency:"usd" },
                           line_item:str|null, project_id:null } ] } ],
    has_more: bool, next_page: str|null }
  401 -> { error: "ERR_AUTH_INVALID_KEY" | "ERR_AUTH_KEY_EXPIRED" }
  422 -> { error: "ERR_PAYLOAD_INVALID" }

Schema: READ-ONLY over usage_records (no write/DDL). SELECT date_trunc(<unit>, created_at) AS bucket,
  SUM(prompt_tokens), SUM(completion_tokens), COUNT(*), SUM(cost_usd)  [+ model_id / key_id when grouped]
  WHERE tenant_id = :tid AND created_at >= :start AND created_at < :end  [+ model_id/key_id filters]
  GROUP BY bucket [, group cols]  ORDER BY bucket ASC  — served by usage_records_tenant_created_id_idx.
  Keyset cursor = base64("b:<unix bucket_start>"); page walks buckets with start_time > cursor.
  NO new table · NO migration · NO two-manifest entry (pure read over the frozen ledger).
```

Target (measurable): all §4 red tests green (incl. the two-tenant isolation test M3 and the
anti-enumeration R-FILTER test); cost aggregation Decimal-exact — SUM over emitted buckets equals a
direct `SELECT SUM(cost_usd) WHERE tenant_id … ` to the last of 8 decimal places; zero rows written
during any read (ledger COUNT unchanged); new-module line coverage ≥ 90%; `make ci` (pyright strict)
clean. Boots/wiring confirmed by the app-level test hitting the route through the real router include.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

### Build-strategy
Scope (may touch): `apps/gateway/src/gateway/usage/api/openai_usage_router.py` · `apps/gateway/src/gateway/usage/api/openai_usage_schemas.py` · `apps/gateway/src/gateway/usage/application/openai_usage_query.py` · `apps/gateway/src/gateway/usage/infrastructure/usage_aggregation_repository.py` · `apps/gateway/src/gateway/usage/domain/openai_usage.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/tenant_usage_costs_api/`
  <HARD. All new files EXCEPT two additive edits: `core/error_catalog.py` (+USAGE_* constants) and
  `main.py` (+import & `include_router`). The FROZEN `usage/api/router.py` and `usage/infrastructure/
  usage_repository.py` are NOT touched — the new aggregation repo is a sibling file.>
Regression floor: `apps/gateway/tests/usage/` · `apps/gateway/tests/images_endpoint/` (nearest /v1
  non-chat neighbor) must stay green; run before the gate. Envoy `/v1/` carve-out is unchanged so no
  edge regression is possible.
Strategy (ordered batches):
  1. domain projection dataclasses + `UsageAggregationPort` Protocol (`usage/domain/openai_usage.py`).
  2. aggregation repo (bucketed SUM/COUNT + group_by + keyset) implementing the port.
  3. use-case `execute()` — auth via AuthzUseCase, param parse/validate (R3–R9), cap check, cursor codec.
  4. Pydantic response schemas + thin router; wire `include_router` in main.py; add USAGE_* catalog entries.
Persona: backend-architect (layering/ports) with billing-precision-engineer (Decimal-exact cost, never
  expose provider_cost/markup) as the cost-field lens — both advisory.
Spawn isolation: worktree if fanned out; single-writer otherwise.
Known-problem fixes: shared test postgres :5433 → unique `GATEWAY_TEST_DATABASE_URL`; naive-UTC datetime
  to asyncpg (mirror `/admin/spend`'s `.replace(tzinfo=None)`); S608 false-positives on hardcoded
  `date_trunc`/filter literals — bind every user value as a param, never interpolate.

Least-sure flag surfaced at freeze: [spec] the AUTH MODEL — "any active tenant API key reads tenant-wide
  aggregate usage/costs" (§1 ⚠). It is the one call that could over-expose tenant-internal spend to a
  low-trust key holder; every other part (wire shape, tenant-scoping, anti-enumeration) is mechanically
  forced by the ledger + milestone rules.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive
Verified by: <agent-id> · at: <ISO-8601 UTC>

---

## 4 · TESTS & SCENARIOS — failing-first suite (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_completions_happy_daily_buckets: seed 2 tenant rows on 2 distinct UTC days; GET completions
    bucket_width=1d → page envelope, 2 buckets ASC, input/output tokens + num_model_requests correct · covers: M1, M8
  - test_costs_amount_billed_only_decimal_exact: seed rows with known cost_usd; GET costs → amount.value
    equals SUM(cost_usd) to 8dp, currency "usd"; response carries NO provider_cost/cost_basis/markup key · covers: M2
  - test_tenant_isolation_two_tenants: seed tenant A + tenant B rows in the SAME window; A's key sees ONLY
    A's totals (B's tokens/cost absent) on BOTH endpoints · covers: M3
  - test_group_by_model_splits_results: seed 2 models; group_by=model → one result per model, key echoed · covers: M4
  - test_group_by_api_key_id_completions: seed 2 keys; group_by=api_key_id → per-key results · covers: M4
  - test_api_key_ids_filter_cross_tenant_is_empty_not_404: A passes B's key_id in api_key_ids → 200 with
    that id contributing zero rows, status != 404 (no existence oracle) · covers: R-FILTER, M5
  - test_auth_missing_key_401: no Authorization header → 401 ERR_AUTH_INVALID_KEY (route exists, not 404) · covers: R1
  - test_auth_revoked_key_401: revoked key → 401 ERR_AUTH_INVALID_KEY · covers: R1
  - test_start_time_required_422: omit start_time → 422 ERR_PAYLOAD_INVALID · covers: R3
  - test_end_time_not_after_start_422: end_time <= start_time → 422 · covers: R4
  - test_bucket_width_invalid_422: completions bucket_width=1w → 422; costs bucket_width=1h → 422 · covers: R5, M6
  - test_group_by_unknown_field_422: group_by=project → 422 · covers: R6
  - test_range_too_large_422: bucket_width=1m over a 30-day span → 422 USAGE_RANGE_TOO_LARGE · covers: R7
  - test_limit_out_of_range_422: limit=0 and limit=500 → 422 · covers: R9
  - test_empty_window_returns_empty_page_not_404: window with no rows → 200, data:[], has_more false,
    next_page null · covers: M8
  - test_pagination_keyset_no_overlap: 3 populated daily buckets, limit=1 → has_more true + next_page;
    replay cursor → next bucket, no overlap, final page has_more false · covers: M7
  - test_zdr_key_still_reads_usage: a zdr_enabled key reads its usage (usage is metadata, not payload) · covers: M9(edge)
  - test_read_writes_no_usage_record: usage_records COUNT unchanged across a completions+costs read · covers: After
</test_plan>

Build-guidance (prose, not gated): R2 (expired key → 401 ERR_AUTH_KEY_EXPIRED) is NOT a separate
gated red test — it rides the SAME reused `AuthzUseCase` 401 seam the revoked-key test (R1) already
exercises, differing only in the catalog message; ruled out on purpose to avoid an expired-key
time-seed. future-dated start_time is VALID (returns empty, never an error);
`models` filter mirrors `api_key_ids` (tenant-AND intersected, foreign value → empty, never 404);
`next_page` is null on the last page; the cost wire `value` is a JSON number rendered from the exact
Decimal only at serialization (no float in the SUM). The completions vs costs endpoints share the
use-case/repo; only the result-projection + allowed bucket_width set differ.

Tests live in: `apps/gateway/tests/tenant_usage_costs_api/` · MUST run red (missing implementation) before Build.

RED EVIDENCE (2026-07-24, `uv run pytest tests/tenant_usage_costs_api/`): **20 failed for the right
reason** — the harness is proven (signup→login→create-key→seed all 201; two-tenant + second-key
fixtures work), and every request 404s because `/v1/organization/usage/completions` and
`/v1/organization/costs` do not exist yet. No harness/fixture errors, no import errors — pure
missing-implementation red. Auth-missing test returns 404 (route absent) instead of the contracted
401; the no-write test asserts a real 200 read first so it is red now, not vacuously green.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY>
Code lives in: `apps/gateway/src/gateway/usage/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor for the freeze `--cross` and the §6 refute-read.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (scope_violation); keep the §3 Regression floor green; allow-list packages only; NEVER touch the frozen `usage/api/router.py` or `usage/infrastructure/usage_repository.py`; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass — including the §3 Regression floor (usage/ + images_endpoint/)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed (no overfit to fixtures, vacuous asserts, stubbed-away scoping)
- [ ] concurrency / timing of the risky operation is safe (read-only; append-safe keyset like AuditRepository)
- [ ] no exposed secrets, injection openings, or unexpected dependencies (every user value bound as a param)
- [ ] layering & dependencies follow CONVENTIONS.md (domain→application→infrastructure→api, inward-only)
- [ ] a person reviewed and approved the change

### Refute-read verdict — the earned-green check
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <cross-tenant leak · anti-enumeration · Decimal exactness>

### GATE RECORD
Reported: <yes | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence.

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)`.
</content>
</invoke>
