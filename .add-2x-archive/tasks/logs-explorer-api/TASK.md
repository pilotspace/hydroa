# TASK: Tenant-admin request-logs query API (filters, pagination, detail)

slug: logs-explorer-api · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
sensitivity: data
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/logs/infrastructure/orm.py:RequestLogRow` — the FROZEN table shape (payload-capture-store TASK.md §3, FROZEN @ v1): `id, tenant_id (FK RESTRICT), key_id (no FK), team_id (nullable, no FK), model_id, status_code, stream, cached, request_body (JSONB null), response_body (JSONB null), guardrail_verdict (JSONB null — reserved, unpopulated in v1), scrub_status, truncated, cost_usd (NUMERIC(14,8), denormalized display snapshot only), created_at`. Indexes: `ix_request_logs_tenant_created (tenant_id, created_at)`, `ix_request_logs_created_at (created_at)`, `ix_request_logs_tenant_key (tenant_id, key_id)` — the last was pre-added by the sibling task specifically "logs-explorer-api key filter (sibling task)" per its own comment.
- `apps/gateway/src/gateway/logs/domain/entities.py:RequestLog` — frozen read-side dataclass projection, its own docstring states "this entity exists for future read-side consumers (the sibling logs-explorer-api task)" — this task is that consumer.
- `apps/gateway/migrations/versions/a1c5e7f9b3d6_request_logs.py` — current Alembic head (confirmed via `uv run alembic heads` → single head `a1c5e7f9b3d6`); a new ADDITIVE index-only migration this task adds parents here.
- `apps/gateway/src/gateway/audit/api/router.py:export_audit` (+ `_parse_limit`, `_parse_iso_datetime`, `_parse_time_range`, `_encode_cursor`, `_decode_cursor`) — the keyset-cursor pagination pattern this task mirrors: opaque base64 `{created_at, id}` cursor, `(created_at, id) DESC` ordering, fetch `limit+1` to derive `has_more`, `asyncio.timeout` wrapping the DB read mapped to a dedicated timeout error code on expiry.
- `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:AuditRepository.list_for_tenant_keyset` — the exact keyset WHERE-predicate shape (`or_(created_at < cursor_created_at, and_(created_at == cursor_created_at, id < cursor_id))`, decomposed OR/AND rather than `tuple_()` for strict-pyright reasons) this task's new `LogsRepository.list_for_tenant_keyset` mirrors verbatim.
- `apps/gateway/src/gateway/usage/api/router.py:get_audit` / `get_alerts` (`AuditEventItem`, `AlertListResponse`, `_parse_pagination`, `_ALERTS_DEFAULT_LIMIT=50` / `_ALERTS_MAX_LIMIT=100`) — the INTERACTIVE-list-endpoint shape this task's list endpoint mirrors (limit 1..100 default 50), deliberately NOT audit-export's archival-scale limit (1..5000, default 1000) — this is a console table page, not a SIEM export.
- `apps/gateway/src/gateway/keys/api/router.py:rotate_key` (docstring: "Returns 404 for revoked or cross-tenant keys (no information leak)") — the tenant-scoped single-resource 404-invisibility idiom this task's detail-fetch endpoint mirrors exactly for cross-tenant logs.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission`, `ROLE_PERMISSIONS`, `require_permission` — FROZEN @ v1 (rbac-roles TASK.md §3). This task ADDS one additive enum member `LOGS_READ` — same additive precedent already used for `RATE_CARDS_MANAGE` (comment at that line: "auto-holds via ROLE_PERMISSIONS[OWNER] = frozenset(Permission)") — granted to OWNER (automatic), ADMIN, OPERATOR, SUPERADMIN, mirroring `AUDIT_READ`'s exact role set; NOT BILLING_ADMIN/VIEWER/MEMBER (those hold `AUDIT_READ`... actually confirmed: BILLING_ADMIN and VIEWER do NOT hold AUDIT_READ either — only OWNER/ADMIN/OPERATOR/SUPERADMIN do; MEMBER holds nothing).
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec`, `PAYLOAD_INVALID` (422), `CURSOR_INVALID` (422, currently scoped to audit-export by comment but the code string `ERR_CURSOR_INVALID` is generic), `EXPORT_QUERY_TIMEOUT` (504) — reused verbatim for `PAYLOAD_INVALID`/`CURSOR_INVALID`; two NEW catalog entries added: `LOG_NOT_FOUND` (404) and `LOGS_QUERY_TIMEOUT` (504, mirrors `EXPORT_QUERY_TIMEOUT`'s shape).
- `apps/gateway/src/gateway/main.py:1200-1254` (`app.include_router(...)` block, includes `usage_router` and `audit_export_router`) — registration site; this task adds one new `app.include_router(logs_query_router)` line alongside them.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — checked specifically for a correlation key (duration/latency, token counts, or a shared request id) linking a `usage_records` row back to its `request_logs` row — CONFIRMED NONE EXISTS (see Issue 1).
- `apps/gateway/src/gateway/tenants/domain/entities.py:Identity` — `user_id, tenant_id, email, role` (frozen dataclass) — the caller identity every new endpoint scopes its query against (`identity.tenant_id`).

Context (working folder): MILESTONE.md `logs-explorer-guardrails-v2` exit criterion 2 ("A tenant admin can list/filter/paginate logs and fetch one log's full detail; another tenant's logs are 404-invisible"); payload-capture-store TASK.md §3 (FROZEN @ v1) — the full frozen row schema + the two admin config endpoints (`GET/PUT /admin/capture`, `PATCH .../capture_enabled`) which this task does NOT touch (pure read-side consumer, no write path).

Honors (patterns / conventions):
- CONVENTIONS.md clean-architecture layering — this task adds `api/logs_query_router.py` + `application/list_request_logs.py` / `application/get_request_log.py` + `infrastructure/logs_repository.py` to the EXISTING `logs/` bounded context (payload-capture-store already owns `logs/domain`, `logs/infrastructure`, `logs/application`, `logs/api`) rather than opening a new bounded context.
- Cursor-pagination idiom (audit-export, above) — chosen over the offset-pagination idiom (`get_audit`/`get_alerts`) because `request_logs` is a live-appending, potentially high-volume table (every proxied call, not just admin actions).
- Tenant-scoped 404-invisibility idiom (keys router, above).
- CLAUDE.md IO design-for-failure rule (bounded timeout on the query, mirrors `export_audit`'s `asyncio.timeout`).

Seams consulted: none (`.add/SEAMS.md` not present in this repo as of ground time).

Anchors the contract cites:
`logs/infrastructure/orm.py:RequestLogRow` · `logs/domain/entities.py:RequestLog` · `audit/api/router.py:export_audit` (cursor pattern) · `audit/infrastructure/audit_repository.py:list_for_tenant_keyset` · `usage/api/router.py:get_audit` / `AuditEventItem` (interactive-limit pattern) · `keys/api/router.py:rotate_key` (404-invisibility idiom) · `tenants/domain/authz.py:Permission` / `ROLE_PERMISSIONS` · `core/error_catalog.py:ErrorSpec` · `main.py` router-registration block · migration head `a1c5e7f9b3d6`.

Issues/Risks (→ feed §1):
1. **`request_logs` has NO duration/latency column and NO token-count columns, and NO correlation key linking a row back to its `usage_records` billing row.** Confirmed by reading `UsageRecordRow`: it has no `request_logs_id`/correlation field either — both tables are independent fire-and-forget writes from different call sites in `use_cases.py`, no shared id passed between them. The Logs Explorer table therefore CANNOT show latency or token counts in v1. This is a real product-shape gap a typical logs-explorer UI would want but the frozen upstream schema doesn't carry — flagged as the §1 ⚠ lowest-confidence item and surfaced explicitly in the `consumes:` reconciliation with `logs-explorer-ui`.
2. **`model_id` / `status_code` / `cost_usd` have no dedicated index** — only `(tenant_id, created_at)` and `(tenant_id, key_id)` exist. A narrow filter (e.g. one model) combined with a wide/unbounded time range could force the keyset scan to walk many non-matching index-ordered rows before satisfying `limit`. Mitigated two ways: (a) this task's own ADDITIVE migration adds `ix_request_logs_tenant_model_created (tenant_id, model_id, created_at)` — `model_id` is the single highest-value filter for a "debug this model's calls" workflow, the milestone brief names it explicitly; (b) the bounded `asyncio.timeout` (mirrors `EXPORT_QUERY_TIMEOUT`) is the backstop for every other filter combination — an honest 504 under load, never an unbounded scan. `status`/`cost` get no dedicated index in v1 (low-cardinality / low-frequency filters); a spec delta if query timeouts are observed in practice.
3. **`guardrail_verdict` is documented "reserved, unpopulated in v1"** by payload-capture-store — the Logs Explorer detail drawer's "guardrail verdicts" promise (MILESTONE.md scope item 3) will show `null` for every row until a future task populates it. This task passes the column through honestly (never fabricates a verdict) — surfaced here so `logs-explorer-ui` designs an honest empty/absent state, not a fake "no issues found" badge.
4. **Adding `Permission.LOGS_READ` touches the FROZEN `rbac-roles` authz matrix file** (`tenants/domain/authz.py`) — additive-only (new enum member + role-set entries in the existing dict literal), the same precedent already used to land `RATE_CARDS_MANAGE`; not a reopening of that contract's EXISTING entries. The import-time completeness guard (`_missing_roles`) means the build must add `LOGS_READ` to every role's frozenset explicitly (or omit it, meaning "no access") — cannot be forgotten silently, the guard would raise at import.

Related intent: MILESTONE.md `logs-explorer-guardrails-v2` exit criterion 2; MILESTONE.md §Shared decisions ("request log ... never a source of billing truth"; "Security floor: ... logs API are `data`-sensitivity (tenant isolation, payload exposure)"); PROJECT.md cross-tenant floor; CLAUDE.md IO design-for-failure rule.

Ground SHA: `443a33a` (branch `chore/add-housekeeping-clusters`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant-admin request-logs query API — list (filter + keyset-cursor paginate) and single-log detail fetch, read-only over the frozen `request_logs` store.

Framings weighed:
- **(chosen)** New `api/logs_query_router.py` + `application/list_request_logs.py` / `application/get_request_log.py` read use cases + `infrastructure/logs_repository.py` (`LogsRepository`), added to the EXISTING `logs/` bounded context — mirrors `audit/api/router.py:export_audit`'s keyset-cursor shape for the list endpoint, and `keys/api/router.py`'s tenant-scoped 404-invisibility idiom for the detail endpoint.
- (rejected) Reuse `get_audit`/`get_alerts`'s offset-based pagination — rejected because `request_logs` is a live-appending, potentially high-volume table (every proxied call, not just admin actions); offset pagination can skip/duplicate rows under concurrent inserts (the exact defect `audit_repository.py:list_for_tenant_keyset`'s own docstring names); the milestone brief also explicitly asks for "cursor pagination."
- (rejected) Fold the read endpoints into `payload-capture-store`'s existing `capture_config_router.py` — rejected because that router is FROZEN @ v1 and scoped to the opt-in TOGGLE only (`GET/PUT /admin/capture`); this task is read-side DATA access, a distinct concern MILESTONE.md's own task decomposition explicitly owns under `logs-explorer-api`, not `payload-capture-store`.

Must:
<must>
  - `GET /admin/logs` returns ONLY the caller's own tenant's rows (`WHERE tenant_id = identity.tenant_id`), newest-first (`created_at DESC, id DESC`), keyset-cursor paginated (no offset), bounded to `limit` 1..100 (default 50) — mirrors `get_audit`/`get_alerts`'s interactive-page ceiling, not `export_audit`'s archival 1..5000.
  - List supports composable, all-optional filters: `since` / `until` (ISO-8601, inclusive), `model_id` (exact match), `key_id` (exact UUID match), `status` (either an exact 3-digit HTTP status code, e.g. `429`, OR the bucket keyword `success` [`status_code < 400`] / `error` [`status_code >= 400`]), `cost_min` / `cost_max` (decimal string, inclusive bounds on `cost_usd`; a row with `cost_usd IS NULL` never matches either bound — SQL NULL-comparison semantics, no special-casing needed).
  - List response rows are METADATA-ONLY — never `request_body`, `response_body`, or `guardrail_verdict` (the payload-bearing fields) — to keep every page's payload bounded regardless of individual row size; full bodies are available ONLY via the single-log detail fetch.
  - `GET /admin/logs/{log_id}` returns the full row (adds `request_body`, `response_body`, `guardrail_verdict` to every list-item field) for exactly one log owned by the caller's tenant.
  - An unknown `log_id` and a cross-tenant `log_id` return the IDENTICAL 404 `ERR_LOG_NOT_FOUND` — no distinguishable signal between "doesn't exist" and "exists but isn't yours" — mirrors `keys/api/router.py`'s 404-invisibility idiom verbatim.
  - Both endpoints require a new `Permission.LOGS_READ` (additive to the FROZEN `rbac-roles` matrix), granted to OWNER/ADMIN/OPERATOR/SUPERADMIN only — mirrors `AUDIT_READ`'s exact role set (NOT BILLING_ADMIN/VIEWER/MEMBER); these rows are PII-payload-bearing, a stricter floor than `USAGE_READ`'s broader holder set.
  - A SUPERADMIN identity is STILL tenant-scoped to its own (platform) tenant on both endpoints — no cross-tenant reach is wired in this task (`authorize_tenant_scope()` stays dormant, per its own docstring "no repository or endpoint calls this yet"); this is a deliberate scope decision, not an oversight, matching every other role's baseline (non-bypass) behavior.
  - Both endpoints are READ-ONLY: SELECT only, no write path, no side-effect audit-of-read entry (unlike `audit/export`'s self-audit — a per-page "someone viewed logs" audit trail is out of scope for v1, not requested by MILESTONE.md).
  - The list query is wrapped in an explicit `asyncio.timeout` (mirrors `export_audit`'s pattern) mapped to a new `ERR_LOGS_QUERY_TIMEOUT` (504) on expiry — never an unbounded scan, never an unhandled 500.
  - The `model_id` filter is backed by a new ADDITIVE index `ix_request_logs_tenant_model_created (tenant_id, model_id, created_at)`, added in this task's own migration parented on the current head `a1c5e7f9b3d6` — a pure index addition, no column/table-shape change to the frozen `request_logs` contract.
</must>

Reject:
<reject>
  - `GET /admin/logs` called by a role without `Permission.LOGS_READ` (billing_admin/viewer/member) -> "ERR_AUTH_FORBIDDEN"
  - `GET /admin/logs?limit=0` or `limit=101` or a non-integer `limit` -> "ERR_PAYLOAD_INVALID"
  - `GET /admin/logs?cursor=<malformed/undecodable>` -> "ERR_CURSOR_INVALID"
  - `GET /admin/logs?since=<malformed-iso>` or `until=<malformed-iso>` -> "ERR_PAYLOAD_INVALID"
  - `GET /admin/logs?since=<later>&until=<earlier>` (inverted range) -> "ERR_PAYLOAD_INVALID"
  - `GET /admin/logs?status=<neither an int 100-599 nor "success"/"error">` -> "ERR_PAYLOAD_INVALID"
  - `GET /admin/logs?cost_min=<non-decimal>` or `cost_max=<non-decimal>` -> "ERR_PAYLOAD_INVALID"
  - `GET /admin/logs` bounded query exceeds its time budget -> "ERR_LOGS_QUERY_TIMEOUT"
  - `GET /admin/logs/{log_id}` called by a role without `Permission.LOGS_READ` -> "ERR_AUTH_FORBIDDEN"
  - `GET /admin/logs/{log_id}` for an unknown OR another tenant's `log_id` -> "ERR_LOG_NOT_FOUND"
</reject>
After:
<after>
  - A tenant admin (owner/admin/operator/superadmin) can page through their own tenant's request logs newest-first, filtered by any combination of time/model/key/status/cost, never seeing another tenant's rows.
  - Fetching one log by id returns its full request/response/metadata for the caller's own tenant, or an indistinguishable 404 for anyone else's or an unknown id.
  - A member/billing_admin/viewer role gets a clean 403 on both endpoints.
  - No proxied request path is touched, read, or slowed by this task — it is a pure read-side addition over the existing `request_logs` table; capture-write behavior is byte-identical to before this task.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **No latency/token-count data is available for the Logs Explorer table** (Issue 1) — lowest confidence because the milestone's own UI description implies a rich, dashboard-grade log table, but the frozen `request_logs`/`usage_records` schemas have no duration, token-count, or correlation-id columns linking the two — confirmed by direct read of both ORM files, not assumed. If wrong (the UI team actually needs this data for the table or drawer): the fix requires either a NEW correlation-id column added retroactively to BOTH already-frozen `request_logs` and `usage_records` contracts, or computing/storing latency at capture time (a change request back to the FROZEN `payload-capture-store` contract) — either path is a real cross-contract change, not a small addition. Recommend v1 ships WITHOUT latency/tokens; surfaced here explicitly for the `logs-explorer-ui` designer's `consumes:` block to react to now, before their own contract freezes on an assumption this API can't satisfy.
  - [ ] The dual-mode `status` filter (exact 3-digit code OR "success"/"error" bucket, one query param) has no existing precedent anywhere in this codebase — confirm this is the right shape vs. a simpler exact-code-only or bucket-only filter. Recommend keeping dual-mode (one extra parse branch, matches typical logs-explorer UX of both a quick segmented filter and code-level drill-down) — confirm or deny at freeze.
  - [ ] Whether `Permission.LOGS_READ` should also be granted to VIEWER (who already holds `USAGE_READ`+`OPS_READ` but not `AUDIT_READ`) — recommend NO for v1 (the PII-payload floor should mirror `AUDIT_READ`'s stricter set, not `USAGE_READ`'s broader one) — confirm or deny at freeze.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: List returns only the caller's tenant, newest-first   # M1
  Given tenant A has 3 request_logs rows and tenant B has 2, both with an owner-role identity
  When tenant A's owner calls GET /admin/logs with default limit
  Then the response contains exactly tenant A's 3 rows, ordered created_at DESC, id DESC
  And none of tenant B's rows appear anywhere in items

Scenario: Filters compose with AND   # M2
  Given tenant A has rows across 2 models, 2 keys, both success and error status_codes, and a range of cost_usd values
  When GET /admin/logs?model_id=gpt-4o&status=error&cost_min=0.01 is called
  Then every returned item has model_id=gpt-4o AND status_code>=400 AND cost_usd>=0.01
  And any row failing even one of the three conditions is absent from items

Scenario: List rows are metadata-only, never bodies   # M3
  Given a tenant with a request_logs row that has non-null request_body/response_body/guardrail_verdict
  When GET /admin/logs is called
  Then no item in the response contains a request_body, response_body, or guardrail_verdict field
  And every item contains only the metadata fields (id, key_id, team_id, model_id, status_code, stream, cached, scrub_status, truncated, cost_usd, created_at)

Scenario: Detail fetch returns the full row   # M4
  Given a tenant with an existing request_logs row owned by the caller's tenant
  When GET /admin/logs/{log_id} is called with that row's id
  Then the response includes request_body, response_body, and guardrail_verdict alongside every metadata field
  And the values match exactly what is stored in the row (masked content as persisted, not re-processed)

Scenario: Unknown log id is 404   # R10 / tenant-isolation floor
  Given a tenant admin identity and a log_id that does not exist in request_logs at all
  When GET /admin/logs/{log_id} is called
  Then the response is 404 "ERR_LOG_NOT_FOUND"

Scenario: Cross-tenant log id is the SAME 404, not a leak   # M5, R10 / tenant-isolation floor
  Given tenant A's admin identity and a log_id that exists but belongs to tenant B
  When GET /admin/logs/{log_id} is called with tenant B's log_id
  Then the response is 404 "ERR_LOG_NOT_FOUND" — byte-identical in shape to the unknown-id case
  And no field of tenant B's row (not even its existence) is observable in the response

Scenario: Member role is forbidden from the list endpoint   # R1
  Given a member-role identity
  When GET /admin/logs is called
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And no request_logs data is returned

Scenario: Billing-admin and viewer roles are also forbidden   # M6
  Given a billing_admin-role identity, then separately a viewer-role identity
  When each calls GET /admin/logs
  Then both responses are 403 "ERR_AUTH_FORBIDDEN"
  And neither role's existing permissions (USAGE_READ, OPS_READ) are altered by this task

Scenario: Member role is forbidden from the detail endpoint   # R9
  Given a member-role identity and a valid log_id owned by their own tenant
  When GET /admin/logs/{log_id} is called
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And the row's content is not returned

Scenario: Owner/admin/operator/superadmin roles all pass LOGS_READ   # M6
  Given four identities with role owner, admin, operator, and superadmin respectively, each scoped to a tenant with request_logs rows
  When each calls GET /admin/logs
  Then all four receive 200 with their own tenant's items
  And a superadmin identity sees ONLY its own (platform) tenant's rows, never another tenant's, per the dormant authorize_tenant_scope() decision

Scenario: Invalid limit is rejected   # R2
  Given an owner-role identity
  When GET /admin/logs?limit=0 is called, then separately GET /admin/logs?limit=101, then GET /admin/logs?limit=abc
  Then all three responses are 422 "ERR_PAYLOAD_INVALID"
  And no partial/default-limit response is silently substituted

Scenario: Boundary limits are accepted   # edge — boundary
  Given an owner-role identity
  When GET /admin/logs?limit=1 is called, then separately GET /admin/logs?limit=100
  Then both responses are 200 with at most 1 (respectively 100) items
  And has_more is derived correctly from the limit+1 fetch in both cases

Scenario: Malformed cursor is rejected with its own error code   # R3
  Given an owner-role identity
  When GET /admin/logs?cursor=not-valid-base64-or-wrong-shape is called
  Then the response is 422 "ERR_CURSOR_INVALID" (distinct from ERR_PAYLOAD_INVALID)
  And no rows are returned

Scenario: Malformed since/until is rejected   # R4
  Given an owner-role identity
  When GET /admin/logs?since=not-a-date is called, then separately with until=not-a-date
  Then both responses are 422 "ERR_PAYLOAD_INVALID"

Scenario: Inverted time range is rejected   # R5
  Given an owner-role identity
  When GET /admin/logs?since=2026-08-01T00:00:00Z&until=2026-01-01T00:00:00Z is called (since after until)
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: Invalid status filter is rejected   # R6
  Given an owner-role identity
  When GET /admin/logs?status=maybe is called
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: status accepts both exact code and bucket keyword   # M2 (dual-mode)
  Given a tenant with rows at status_code 200, 429, and 500
  When GET /admin/logs?status=429 is called, then separately GET /admin/logs?status=error, then GET /admin/logs?status=success
  Then status=429 returns only the 429 row
  And status=error returns the 429 and 500 rows
  And status=success returns only the 200 row

Scenario: Invalid cost bounds are rejected   # R7
  Given an owner-role identity
  When GET /admin/logs?cost_min=not-a-number is called
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: Rows with null cost_usd never match a cost filter   # edge — partial data
  Given a tenant with one metadata-only row (cost_usd IS NULL, scrub_failed) and one normal row with cost_usd=0.05
  When GET /admin/logs?cost_min=0.00 is called
  Then only the row with cost_usd=0.05 is returned
  And the null-cost metadata-only row is excluded, not errored on

Scenario: Bounded query timeout maps to a dedicated error, never a bare 500   # R8, M9 (design-for-failure)
  Given the list query's underlying DB read exceeds the configured time budget
  When GET /admin/logs is called
  Then the response is 504 "ERR_LOGS_QUERY_TIMEOUT"
  And no unhandled exception or generic 500 is surfaced

Scenario: Empty result set is 200 with an empty list, never 404   # edge — boundary
  Given a tenant with request_logs rows, none matching the given filters
  When GET /admin/logs?model_id=nonexistent-model is called
  Then the response is 200 with items: [] and has_more: false
  And next_cursor is null

Scenario: Last page has no next cursor   # edge — boundary
  Given a tenant with fewer rows than the requested limit
  When GET /admin/logs?limit=50 is called
  Then has_more is false and next_cursor is null
  And every existing row for that tenant (within any active filters) is present in items

Scenario: Concurrent insert during a keyset walk is never duplicated or skipped   # edge — concurrency
  Given a tenant admin has fetched page 1 of a keyset-paginated walk and holds its next_cursor
  When a NEW request_logs row is inserted for that tenant (created_at newer than page 1's boundary) before page 2 is fetched
  Then fetching page 2 with the held cursor never re-surfaces a row already returned on page 1
  And the newly inserted row appears only on a subsequent fetch from the front (no cursor), never retroactively spliced into the in-flight walk

Scenario: Metadata-only (scrub-failed) rows are listed and detailed honestly   # edge — partial failure passthrough
  Given a request_logs row with scrub_status="scrub_failed_metadata_only", request_body=null, response_body=null
  When the row is listed via GET /admin/logs and then fetched via GET /admin/logs/{log_id}
  Then the list item shows scrub_status="scrub_failed_metadata_only"
  And the detail response's request_body and response_body are both null — never fabricated or backfilled content

Scenario: PATCH-adjacent write surfaces are untouched   # After (byte-identical capture path)
  Given the payload-capture-store's existing capture-write path and admin toggle endpoints
  When this task's endpoints are deployed
  Then GET/PUT /admin/capture and PATCH /admin/keys/{id} capture_enabled behavior is byte-identical to before
  And no proxied request's latency or response is affected by this task's read-only additions
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
Decided at freeze (Tin, 2026-07-10): the latency/token-absence flag is RESOLVED by change-request —
Tin chose to add metering rather than ship v1 without. This contract now CONSUMES the FROZEN
`request-log-metering-fields` (v1) columns: `latency_ms`, `prompt_tokens`, `completion_tokens`,
`total_tokens` (added to LogListItem) and `request_id` (added to LogDetailItem, correlating to
`usage_records.raw["request_id"]`). depends-on: payload-capture-store, request-log-metering-fields.
Pre-metering rows carry NULL for these fields (honest null, not zero).

Least-sure flag surfaced at freeze: [contract] latency/token/correlation columns were absent from the
frozen `request_logs`; RESOLVED at freeze via the `request-log-metering-fields` additive change-request
(Tin's decision) — this API exposes the new columns, NULL on pre-metering rows.

### API — read endpoints (new)

```
GET /admin/logs
  auth: Permission.LOGS_READ (NEW additive permission — owner/admin/operator/superadmin; member/billing_admin/viewer -> 403)
  query: limit?: int (1..100, default 50)
         cursor?: string (opaque base64 {created_at, id} keyset cursor — mirrors GET /admin/audit/export)
         since?, until?: string (ISO-8601, inclusive; since > until -> 422)
         model_id?: string (exact match)
         key_id?: string (UUID, exact match)
         status?: string (exact 3-digit code "429", or bucket "success" [<400] / "error" [>=400])
         cost_min?, cost_max?: string (decimal, inclusive bounds on cost_usd; NULL cost_usd rows never match)
  200 -> { items: [LogListItem], next_cursor: string | null, has_more: bool }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  422 -> { error: "ERR_PAYLOAD_INVALID" }        # bad limit/since/until/status/cost_min/cost_max, or inverted range
  422 -> { error: "ERR_CURSOR_INVALID" }         # malformed cursor
  504 -> { error: "ERR_LOGS_QUERY_TIMEOUT" }     # bounded query exceeded its time budget

GET /admin/logs/{log_id}
  auth: Permission.LOGS_READ (same gate as list)
  200 -> LogDetailItem
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  404 -> { error: "ERR_LOG_NOT_FOUND" }          # unknown id OR cross-tenant id — identical shape, no leak
```

### Response shapes (Pydantic, `model_config = ConfigDict(frozen=True)`, mirrors `AuditEventItem`/`AlertListResponse`)

```python
class LogListItem(BaseModel):
    """One request_logs row, metadata-only — never a payload-bearing field."""
    id: str
    key_id: str
    team_id: str | None
    model_id: str
    status_code: int
    stream: bool
    cached: bool
    scrub_status: str          # "scrubbed" | "scrub_failed_metadata_only" | "oversize_metadata_only"
    truncated: bool
    cost_usd: str | None       # denormalized display snapshot only — NEVER billing truth
    created_at: str            # ISO-8601
    # metering fields (from request-log-metering-fields, FROZEN @ v1 — additive change-request):
    latency_ms: int | None     # per-call latency from _start_ns; null on pre-metering rows
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None

class LogDetailItem(LogListItem):
    """Full single-log detail — adds the payload-bearing fields + the usage_records correlation key."""
    request_id: str | None     # correlation to usage_records.raw["request_id"] (metering; null on pre-metering rows)
    request_body: dict[str, object] | None    # null when metadata-only
    response_body: dict[str, object] | None   # null when metadata-only, or the call errored pre-response
    guardrail_verdict: dict[str, object] | None  # reserved/unpopulated in v1 — always null today, passed through honestly

class LogListResponse(BaseModel):
    items: list[LogListItem]
    next_cursor: str | None
    has_more: bool
```

### New module additions (existing `apps/gateway/src/gateway/logs/` bounded context)

```
logs/
  api/
    logs_query_router.py     # GET /admin/logs, GET /admin/logs/{log_id} — logs_query_router
  application/
    list_request_logs.py     # ListRequestLogsUseCase.execute(tenant_id, filters, limit, cursor) -> (items, has_more)
    get_request_log.py       # GetRequestLogUseCase.execute(tenant_id, log_id) -> RequestLog | None
  infrastructure/
    logs_repository.py       # LogsRepository — list_for_tenant_keyset(...) [mirrors AuditRepository.list_for_tenant_keyset
                              #   verbatim: same OR/AND decomposed keyset predicate, same order-by], get_for_tenant(tenant_id, log_id)
```

### Permission addition (additive to FROZEN `tenants/domain/authz.py`, rbac-roles TASK.md §3)

```python
class Permission(StrEnum):
    ...
    LOGS_READ = "logs_read"   # NEW — same additive precedent as RATE_CARDS_MANAGE

ROLE_PERMISSIONS = {
    Role.OWNER: frozenset(Permission),       # automatic — no change needed
    Role.ADMIN: frozenset({..., Permission.LOGS_READ}),      # add
    Role.OPERATOR: frozenset({..., Permission.LOGS_READ}),   # add
    Role.SUPERADMIN: frozenset(Permission),  # automatic — no change needed
    # Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER: UNCHANGED (no LOGS_READ)
}
```

### Error catalog additions (`core/error_catalog.py`)

```python
LOG_NOT_FOUND = ErrorSpec(404, "ERR_LOG_NOT_FOUND", "Request log not found")
LOGS_QUERY_TIMEOUT = ErrorSpec(504, "ERR_LOGS_QUERY_TIMEOUT", "Logs query exceeded the time budget; narrow the range or reduce limit")
# PAYLOAD_INVALID, CURSOR_INVALID reused verbatim from the existing catalog.
```

### Schema (additive migration only — parents on current head `a1c5e7f9b3d6`)

```sql
-- new migration, no column/table changes to the FROZEN request_logs shape
CREATE INDEX ix_request_logs_tenant_model_created
  ON request_logs (tenant_id, model_id, created_at);
```

Access pattern: read-only SELECTs against `request_logs`, always `WHERE tenant_id = :tid` first. List query: keyset predicate on `(created_at, id)` DESC + optional `since/until/model_id/key_id/status/cost_min/cost_max` predicates, `LIMIT :limit+1` (has_more derivation, mirrors `export_audit`), wrapped in `asyncio.timeout(...)`. Detail query: `WHERE tenant_id = :tid AND id = :log_id` single-row SELECT; zero rows -> `LOG_NOT_FOUND` (covers both unknown-id and cross-tenant-id identically). No UPDATE/DELETE path — this task is purely additive to the existing read surface; the retention sweep and capture-write path (payload-capture-store, FROZEN) are untouched.

Glossary deltas: none — reuses `Request log` (payload-capture-store TASK.md §3). Note (not a new term, a clarifying addendum): the list/detail split — "list = metadata-only projection", "detail = full payload projection" — is this task's own read-shape decision over the existing `Request log` concept, not a new domain concept.

### Freeze questions (Tin rules on these before Status can move to FROZEN)

1. **Latency/token data absence (the ⚠ flag above).** Recommendation: ship v1 without it; `logs-explorer-ui` designs the table/drawer around metadata that exists (time, model, key, status, cost, cached, stream, scrub_status, truncated) rather than blocking on a cross-contract schema change.
2. **Dual-mode `status` filter (exact code OR success/error bucket) — no existing precedent.** Recommendation: keep dual-mode (one extra parse branch, matches typical logs-explorer UX).
3. **Should `Permission.LOGS_READ` extend to VIEWER?** Recommendation: no — mirror `AUDIT_READ`'s stricter role set (owner/admin/operator/superadmin only), since these rows are PII-payload-bearing.
4. **`guardrail_verdict` will be `null` for every row in v1** (Issue 3, §0) — confirm `logs-explorer-ui` is briefed to design an honest empty/absent state for the drawer's "guardrail verdicts" section, not a fake "no issues found."

Reported: no — awaiting the freeze report / Tin's review of this draft.

---

## Design self-score

- Completeness: 0.92 — every MILESTONE.md scope item for this task (list w/ filters, cursor pagination, single-log detail, tenant-scoped, RBAC-gated, 404-invisible cross-tenant) is addressed with a concrete symbol-level plan mirroring 3 distinct existing precedents (audit-export cursor, get_audit/get_alerts interactive-limit, keys-router 404-invisibility); held below 0.95 because the latency/token-data gap (Issue 1) is a genuine, unresolved product-shape question, not a guess dressed as ground truth.
- Clarity: 0.93 — every rule cites the exact file/symbol/precedent it mirrors or extends; the list-vs-detail body-field split and the dual meaning of 404 (unknown vs cross-tenant) are stated explicitly rather than left implicit.
- Practicality: 0.93 — reuses 4 existing precedents verbatim-in-shape (keyset-cursor encode/decode, keyset WHERE predicate, interactive limit/role-gating shape, 404-invisibility idiom) plus one already-landed additive-enum precedent (RATE_CARDS_MANAGE) for the new Permission; the one genuinely new piece (the dual-mode status filter) is grounded against why no existing precedent covers it and given a concrete recommendation.
- Optimization: 0.90 — bounded `asyncio.timeout` chosen as the general backstop rather than indexing every filter column; one targeted new index (`model_id`) added where the milestone brief signals real demand, avoiding both an unbounded scan risk and index-proliferation over-engineering.
- Edge cases: 0.91 — scenarios cover empty result set, last-page (no next cursor), boundary limits, concurrent-insert-during-walk append-safety, null-cost exclusion, metadata-only-row honest passthrough (both list and detail), and the dual 404 (unknown vs cross-tenant) as literally the same response.
- Self-evaluation: 0.92 — 4 freeze questions each carry an explicit recommendation + rationale; the ⚠ lowest-confidence item names its concrete cost if wrong (a cross-contract change against TWO already-frozen contracts) and is surfaced specifically for the sibling UI designer's `consumes:` reconciliation, not left as a private note.

All dimensions ≥0.90; no refinement pass required before reporting.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new `logs/` read-side module lines; every §2 scenario has exactly one test.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_list_returns_only_callers_tenant: 2 tenants w/ rows / GET /admin/logs / assert only caller's 3 rows, ordered · covers: M1
  - test_filters_compose_with_and: mixed rows / GET ?model_id&status=error&cost_min / assert only rows matching all three · covers: M2
  - test_list_rows_are_metadata_only: row w/ bodies / GET /admin/logs / assert no body/verdict keys in items · covers: M3
  - test_detail_returns_full_row: existing row / GET /admin/logs/{id} / assert bodies+verdict present, match stored values · covers: M4
  - test_unknown_log_id_is_404: nonexistent id / GET /admin/logs/{id} / assert 404 ERR_LOG_NOT_FOUND · covers: R10
  - test_cross_tenant_log_id_is_same_404: tenant A id, tenant B caller / GET /admin/logs/{id} / assert identical 404 shape · covers: M5, R10
  - test_member_forbidden_on_list: member JWT / GET /admin/logs / assert 403 ERR_AUTH_FORBIDDEN · covers: R1
  - test_billing_admin_and_viewer_forbidden_on_list: 2 JWTs / GET /admin/logs / assert both 403 · covers: M6
  - test_member_forbidden_on_detail: member JWT + own-tenant id / GET /admin/logs/{id} / assert 403 · covers: R9
  - test_all_logs_read_roles_pass: owner/admin/operator/superadmin JWTs / GET /admin/logs / assert 200, superadmin sees only own tenant · covers: M6
  - test_invalid_limit_rejected: limit=0,101,abc / GET /admin/logs / assert 422 ERR_PAYLOAD_INVALID ×3 · covers: R2
  - test_boundary_limits_accepted: limit=1, limit=100 / GET /admin/logs / assert 200, correct has_more · covers: edge-boundary
  - test_malformed_cursor_rejected: bad cursor string / GET /admin/logs / assert 422 ERR_CURSOR_INVALID · covers: R3
  - test_malformed_since_until_rejected: bad ISO strings / GET /admin/logs / assert 422 ERR_PAYLOAD_INVALID ×2 · covers: R4
  - test_inverted_time_range_rejected: since>until / GET /admin/logs / assert 422 · covers: R5
  - test_invalid_status_rejected: status=maybe / GET /admin/logs / assert 422 · covers: R6
  - test_status_dual_mode_exact_and_bucket: rows at 200/429/500 / GET ?status=429, ?status=error, ?status=success / assert correct subsets each · covers: M2
  - test_invalid_cost_bounds_rejected: cost_min=not-a-number / GET /admin/logs / assert 422 · covers: R7
  - test_null_cost_rows_excluded_from_cost_filter: 1 null-cost + 1 priced row / GET ?cost_min=0.00 / assert only priced row · covers: edge-partial-data
  - test_query_timeout_maps_to_504: forced timeout in the DB read / GET /admin/logs / assert 504 ERR_LOGS_QUERY_TIMEOUT, no 500 · covers: R8, M9
  - test_empty_result_is_200_empty_list: filters matching nothing / GET /admin/logs / assert 200 items=[] has_more=false · covers: edge-boundary
  - test_last_page_has_no_next_cursor: fewer rows than limit / GET /admin/logs / assert has_more=false, next_cursor=null · covers: edge-boundary
  - test_concurrent_insert_during_keyset_walk_is_safe: page1 cursor held / insert new row / fetch page2 / assert no dup/skip · covers: edge-concurrency
  - test_metadata_only_rows_listed_and_detailed_honestly: scrub_failed row / list + detail / assert scrub_status shown, bodies null not fabricated · covers: edge-partial-failure
  - test_capture_write_path_byte_identical: existing capture-store test suite / re-run unmodified / assert unaffected by this task's additions · covers: After
</test_plan>

Tests live in: `apps/gateway/tests/logs_explorer_api/` (25 tests, 1-2 new files) · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/logs/` · `apps/gateway/migrations/versions/` · `apps/gateway/src/gateway/tenants/domain/authz.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/main.py`

Strategy (ordered batches): 1. `tenants/domain/authz.py` — add `Permission.LOGS_READ` + role-set entries (OWNER/SUPERADMIN automatic, ADMIN/OPERATOR explicit); confirm the import-time completeness guard still passes. 2. `core/error_catalog.py` — add `LOG_NOT_FOUND`, `LOGS_QUERY_TIMEOUT`. 3. `logs/infrastructure/logs_repository.py` — `LogsRepository.list_for_tenant_keyset(...)` (mirrors `AuditRepository.list_for_tenant_keyset` verbatim: same decomposed OR/AND keyset predicate, same order-by) + `get_for_tenant(tenant_id, log_id)` (single-row, `WHERE tenant_id = :tid AND id = :id`). 4. `logs/application/list_request_logs.py` + `get_request_log.py` — thin use cases composing the repository + filter-parsing helpers (mirrors `_parse_limit`/`_parse_iso_datetime`/`_parse_time_range`/`_encode_cursor`/`_decode_cursor` from `audit/api/router.py`, plus new `_parse_status`/`_parse_decimal_bound` helpers). 5. `logs/api/logs_query_router.py` — the two routes, `require_permission(Permission.LOGS_READ)` gate, `asyncio.timeout` around the list query mapped to `LOGS_QUERY_TIMEOUT`. 6. new additive migration (`ix_request_logs_tenant_model_created`), parented on the CURRENT head at build time (re-run `uv run alembic heads` — do not trust the ground-time head blindly, per the worktree-agent-stale-base gotcha). 7. `main.py` — `app.include_router(logs_query_router)`. 8. tests + red/green verification.

Persona (required): backend-architect (`.add/personas/backend-architect.md`) — this is a read-side addition to an existing bounded context (`logs/`), squarely the domain/application/infrastructure/api layering + Protocol/repository discipline this persona enforces; no new reliability or security persona needed beyond its own IO-design-for-failure lens (bounded timeout).
Spawn isolation (default): prefer isolation: "worktree" per the repo's worktree-isolated-spawn-default convention.
Known-problem fixes: (a) the `_missing_roles`/`ROLE_PERMISSIONS[OWNER] != frozenset(Permission)` completeness guards in `authz.py` fire at IMPORT time — forgetting to add `LOGS_READ` to even one role's frozenset does NOT silently under-grant, it crashes the whole app at boot; build must add it deliberately to every role (explicit inclusion for ADMIN/OPERATOR, explicit exclusion — i.e. no entry — for BILLING_ADMIN/VIEWER/MEMBER) → run the app locally / import `gateway.tenants.domain.authz` after the edit to confirm no `RuntimeError`. (b) alembic head drift between this draft's ground time and actual build time (sibling tasks in this same wave may land migrations first) → re-check `uv run alembic heads` at build start, not the `a1c5e7f9b3d6` cited here. (c) asyncpg NAIVE-vs-AWARE datetime binding gotcha (`audit/api/router.py:_as_naive_utc`) — `since`/`until` and the cursor's `created_at` must be normalized to naive UTC before binding against the test schema's `Mapped[datetime]` column, mirror that helper verbatim rather than re-deriving.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): every query (list AND detail) MUST bind `tenant_id = identity.tenant_id` as the FIRST predicate, never a filter applied after an unscoped fetch — the detail endpoint's 404-invisibility depends on the WHERE clause itself excluding cross-tenant rows, not on a post-fetch tenant check that could be forgotten on one code path.
Code lives in: `apps/gateway/src/gateway/logs/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new third-party dependency expected — sqlalchemy/fastapi/pydantic only); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `tests/logs_explorer_api` 35/35 green (25 planned + 2 extra positive-path
      tests + rbac_roles-role parametrizations), plus frozen `tests/rbac_roles` 8/8 green (no role
      permission drift). DB: `gateway_test_vla` / `gateway_migrations_test_vla` (dropped after run).
- [x] coverage did not decrease — 100% line coverage on every new file: `logs/api/logs_query_router.py`
      (146/146), `logs/application/list_request_logs.py` (14/14), `logs/application/get_request_log.py`
      (9/9), `logs/infrastructure/logs_repository.py` (44/44); `core/error_catalog.py` 100%;
      `tenants/domain/authz.py` unchanged-lines-covered (75%, pre-existing baseline, no new dead branch).
- [x] no test or contract was altered during build — `git log` shows this task's commits only add
      files under `logs/`, `error_catalog.py`, `authz.py`, `main.py`, one migration; §3 CONTRACT text
      matches the shipped shapes verbatim (response fields, error codes, permission set).
- [x] the green was EARNED, not gamed — see Refute-read verdict below; independently re-attacked with
      live throwaway probes beyond the suite (cursor-smuggle, SQLi-shaped filters, 404 timing/shape),
      not just re-running the existing tests.
- [x] concurrency / timing of the risky operation is safe — `test_concurrent_insert_during_keyset_walk_is_safe`
      passes; keyset predicate is strict `<` on `(created_at, id)` so a newer concurrent insert can never
      be spliced mid-walk (code-confirmed, not just test-confirmed).
- [x] no exposed secrets, injection openings, or unexpected dependencies — live SQLi-shaped `model_id`
      payloads (`' OR '1'='1`, `...DROP TABLE...`, `%`, `*`) all parameterized safely (0 matches, no
      error, table intact, tenant's real row still queryable after). No new third-party dependency.
- [x] layering & dependencies follow CONVENTIONS.md — clean api/application/infrastructure/domain
      split inside the existing `logs/` bounded context; repository exposes no write method.
- [ ] a person reviewed and approved the change — pending Tin's review of this verify report (contract
      freeze was Tin-approved; this GATE RECORD still needs the human sign-off per autonomy:auto).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] `GET /admin/logs` never returns another tenant's rows under any filter/cursor combination,
      including a cursor forged from another tenant's own row id/timestamp — confirmed by live throwaway
      probe (`test_zz_cross_tenant_cursor_smuggle`, deleted after run): tenant A given a cursor built
      from tenant B's newest row still gets back only tenant A's own row, 200, never tenant B's id.
- [x] `GET /admin/logs/{log_id}` returns byte-identical 404 shape for an unknown id and a cross-tenant
      id — confirmed by `test_cross_tenant_log_id_is_same_404` (suite) AND a 15-iteration live probe
      comparing status/headers/body: identical every time; timing 9.82ms (unknown) vs 10.36ms (cross)
      avg — within test-env noise and structurally implausible as an oracle (both paths execute the
      exact same `WHERE tenant_id=:tid AND id=:id` query shape, just a different id literal).
- [x] list rows never carry `request_body`/`response_body`/`guardrail_verdict` in the HTTP response,
      even when the underlying row has large non-null payloads — confirmed by
      `test_list_rows_are_metadata_only` AND code inspection (`LogListItem` never declares those
      fields; the router constructs it field-by-field, never passes the raw entity through).
- [x] the new `Permission.LOGS_READ` grants exactly OWNER/ADMIN/OPERATOR/SUPERADMIN and denies
      BILLING_ADMIN/VIEWER/MEMBER, without altering any other role's existing permission set —
      confirmed by `tests/rbac_roles` 8/8 green (frozen suite, unmodified) + direct read of
      `ROLE_PERMISSIONS` (only `LOGS_READ` added to ADMIN/OPERATOR's sets; OWNER/SUPERADMIN
      auto-hold via `frozenset(Permission)`; BILLING_ADMIN/VIEWER/MEMBER untouched).
- [x] the additive migration lands the exact index on top of the CURRENT (build-time) alembic head,
      not the stale ground-time head — confirmed by running the full migration chain from scratch
      against `gateway_migrations_test_vla`: single head `69cfdc584129`, parented on `b7c9e1a3f5d8`
      (not the ground-time `a1c5e7f9b3d6`), and `\d request_logs` shows
      `ix_request_logs_tenant_model_created btree (tenant_id, model_id, created_at)` present.
- [x] the list query is genuinely bounded by a real `asyncio.timeout`, not a decorative one — confirmed
      by code read: `async with asyncio.timeout(_READ_TIMEOUT_SECONDS): await use_case.execute(...)`
      wraps a real awaited coroutine (structurally sound asyncio usage); `test_query_timeout_maps_to_504`
      confirms the `TimeoutError -> 504 ERR_LOGS_QUERY_TIMEOUT` mapping via fault injection (same
      technique as the audit-export precedent's own tests).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `logs_query_router` imported+included in
      `main.py:101,1253`; `LogsRepository`/`ListRequestLogsUseCase`/`GetRequestLogUseCase` wired
      router->use-case->repository (no orphan layer); `Permission.LOGS_READ` referenced in both
      `ROLE_PERMISSIONS` and the router's `require_permission(Permission.LOGS_READ)` gate on both
      routes; `LOG_NOT_FOUND`/`LOGS_QUERY_TIMEOUT` each raised exactly once, at the one call site the
      contract specifies.
- [x] DEAD-CODE (code) — no orphaned symbol found; `_cost_str` in the router is a trivial pass-through
      (not dead, called twice, harmless — noted, not a defect).
- [ ] SEMANTIC (prose / non-code) — n/a, this task ships no prose/non-code deliverable.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by direct read:
      `logs/infrastructure/orm.py:RequestLogRow` (now extended with the 5 metering columns, still
      resolves), `logs/domain/entities.py:RequestLog`, `audit/api/router.py` cursor helpers (pattern
      mirrored, not imported), `audit/infrastructure/audit_repository.py:list_for_tenant_keyset`
      (predicate shape mirrored verbatim), `keys/api/router.py:rotate_key` (404-idiom mirrored),
      `tenants/domain/authz.py:Permission`/`ROLE_PERMISSIONS`, `core/error_catalog.py:ErrorSpec`,
      `main.py` router-registration block — all resolve; `pyright` clean (0 errors) on every touched
      file.
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — the migration
      parent moved from the ground-time head `a1c5e7f9b3d6` to the actual build-time head
      `b7c9e1a3f5d8` (two sibling tasks — request-log-metering-fields, guardrail-analytics — landed
      migrations first); the build correctly re-checked `alembic heads` rather than trusting the
      ground-time citation, per its own §5 "Known-problem fixes (b)". No other anchor moved.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: add-verify (self) · adversarially checked: (1) cross-tenant cursor smuggling — forged a cursor
from tenant B's own row id/timestamp and used it as tenant A; only A's row ever came back, no B leak,
because `tenant_id = :tid` is always the first WHERE predicate, never a post-fetch filter. (2)
SQL-injection-shaped `model_id` values (`' OR '1'='1`, `...DROP TABLE request_logs;--`, `%`, `*`) — all
safely parameterized, zero matches, table intact, tenant's own row still queryable afterward. (3)
detail-fetch 404 oracle — 15-iteration timing+shape comparison of unknown-id vs cross-tenant-id, status/
headers/body byte-identical every time, no exploitable timing signal (same query shape either way). Also
independently re-derived, from the shipped code, that every §1 Reject rule has a real enforcing branch
(not a vacuous always-pass): `_parse_limit`/`_parse_status`/`_parse_decimal_bound`/`_decode_cursor`/
`_parse_time_range` each have a genuine reject path exercised by a distinct test, and
`LogsRepository.get_for_tenant`'s tenant-scoped WHERE is the only source of the None that maps to 404
(no alternate post-fetch check exists to accidentally bypass).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: add-verify (self)
1. Security: CLEAR — tenant scoping is WHERE-clause-first on both endpoints (never post-fetch),
   404-invisibility is byte-identical and not timing-distinguishable, RBAC matrix matches the frozen
   contract exactly with no drift to other roles, filter inputs are fully parameterized (no injection
   opening found under live attack), list payload-bearing fields are absent from the response by
   construction (not by a filter that could be forgotten).
2. Concurrency: CLEAR — keyset walk uses strict `<` on `(created_at, id)`; a concurrent insert newer
   than the page boundary can never be spliced mid-walk (test + code confirmed); no shared mutable
   state, no fire-and-forget task leak (this task adds no background task).
3. Architecture: RESIDUE — `LogsRepository.list_for_tenant_keyset` runs `select(RequestLogRow)`
   (the full ORM row), so every list call pulls the full `request_body`/`response_body`/
   `guardrail_verdict` JSONB payload from Postgres for up to `limit+1` (≤101) rows, even though the
   HTTP response is metadata-only. The client-facing "payload bounded regardless of row size" promise
   (§1 Must) holds at the network boundary but NOT at the app<->DB boundary — a tenant with many
   large captured bodies (capped at `max_body_bytes`, e.g. 100KB/row in test config) could still pull
   several MB per list call before the response is trimmed server-side. This mirrors the exact same
   pattern as the precedent it mirrors (`AuditRepository.list_for_tenant_keyset` also does
   `select(AuditEventRow)`), so it's a pre-existing repo convention, not a build-introduced defect —
   not a security leak (nothing crosses the trust boundary), not a correctness defect (every test
   passes), a genuine efficiency/architecture gap worth a targeted column-projection follow-up.
Verdict: PASS
Residue: Architecture — list query fetches full JSONB payload columns from Postgres despite serving a
metadata-only response (see above); recommend a follow-up SPEC delta to project only the metadata
columns in `list_for_tenant_keyset`'s SELECT. Non-blocking: no security or correctness impact, bounded
by existing per-row `max_body_bytes`, and the `asyncio.timeout` backstop still fires under real load.
Binding: advisory — sensitivity: data (not the highest tier; no security HARD-STOP triggered)

### GATE RECORD
Reported: yes — this verify report is the gate report rendered before the outcome below.
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (not applicable — outcome is PASS, not RISK-ACCEPTED)
Reviewed by: add-verify (self, adversarial pass) — pending Tin's final human sign-off · date: 2026-07-11

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by add-verify (self, adversarial pass) — pending Tin's final human sign-off)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

