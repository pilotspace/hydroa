# TASK: Compliance export API over the immutable audit store

slug: compliance-export-api · created: 2026-07-10 · stage: production
milestone: enterprise-identity-compliance
sensitivity: data
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/usage/api/router.py:728 get_audit` — the EXISTING tenant-scoped read this task mirrors envelope conventions from. FROZEN @ v1 (audit-log-store TASK.md §3) — do NOT edit; this task adds a sibling route, never widens the frozen one. `AuditEventItem` (line 704) and `AuditListResponse` (line 719) are the item/envelope shapes to reuse field-for-field.
- `apps/gateway/src/gateway/usage/api/router.py:599 _parse_pagination` — manual limit/offset parsing that raises `PAYLOAD_INVALID` (not FastAPI's 422 auto-shape) on a bad numeric string; this task's own limit/cursor parsing follows the same manual-parse-not-Pydantic-coercion convention so error bodies stay in the `ERR_*` catalog shape.
- `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:27 AuditRepository` — append-only repo; exposes `count_for_tenant`, `list_for_tenant_paged` (offset-based, `ORDER BY created_at DESC, id DESC`, line 100). This task ADDS a new keyset method (e.g. `list_for_tenant_export`) rather than reusing the offset method — offset degrades on a live-appending table (the milestone's own "stable across pages even as new rows append" requirement rules out `.offset()`).
- `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py:44 AuditEventRow`, `:57 __table_args__` — only index today is `audit_events_tenant_created_idx (tenant_id, created_at)`, no `id` in the index and no index on `actor_email`. A keyset predicate on `(created_at, id)` and an `actor_email =` filter will both do a partial index-then-filter scan on this index today — acceptable at current audit volumes, but the contract should sanction a follow-up composite index as an additive migration (see §3).
- `apps/gateway/src/gateway/audit/domain/audit_event.py:26 AuditEvent`, `:64 AuditLog` (Protocol) — frozen dataclass + port; `id` is caller-assigned `uuid.uuid4()` (confirmed at `tenants/api/users_router.py:154` — **not** UUIDv7/time-ordered), so `(created_at, id)` ties are broken by an arbitrary-but-deterministic UUID4, not true insertion order. Deterministic ≠ chronological-on-tie; documented as an accepted limitation (§1 assumption).
- `apps/gateway/src/gateway/tenants/domain/authz.py:54 Permission`, `:76 ROLE_PERMISSIONS`, `:230 require_permission` — `Permission.AUDIT_READ` (line 66) already grants owner/admin/operator, denies billing_admin/viewer/member (lines 76-121). This is a tenant-scoped permission (not a `require_superadmin` operator-wide gate) — the right fit per dispatch note, and per the PROJECT.md-folded invariant that a `Permission` cannot express "excludes OWNER" so a NEW permission would be pointless overhead here; reusing `AUDIT_READ` is the additive, no-new-enum-member move.
- `apps/gateway/src/gateway/audit/application/audit_writer.py:30 record_audit` — fire-and-forget, fail-open (separate session, swallows all exceptions, never blocks/rolls back the caller's HTTP response). Call-site pattern confirmed at `tenants/api/users_router.py:148-171`: `asyncio.ensure_future(record_audit(request.app.state.sessionmaker, AuditEvent(...)))`, using `request.app.state.sessionmaker` as the session factory. This task's own "export access is itself audited" Must reuses this exact seam — no new writer.
- `apps/gateway/src/gateway/usage/application/reconciliation.py:104 _as_naive_utc` — the established fix for the asyncpg aware/naive `created_at` bind mismatch (test schema via `create_all` maps `Mapped[datetime]` to a NAIVE column; prod Alembic column is TIMESTAMPTZ). Any `since`/`until`/cursor `created_at` bound in this task MUST go through the same normalize-to-naive-UTC step before binding, or it 500s only in the test DB (silent prod/test divergence) — a documented, previously-hit gotcha (CONVENTIONS.md TDD delta), not a hypothetical.
- `apps/gateway/src/gateway/core/error_catalog.py:182 PAYLOAD_INVALID`, `:83 AUTH_FORBIDDEN`, `:77/:80 AUTH_TOKEN_MISSING/AUTH_TOKEN_INVALID` — reused verbatim for the shared rejection shapes. Two NEW `ErrorSpec` constants are needed (§3): a malformed-cursor code and an export-query-timeout code — neither exists in the catalog today.
- `apps/gateway/src/gateway/usage/application/retention_sweep.py:9 (module docstring)`, `:110 _SET_AUDIT_PURGE_GUC`, `:358` — the operator-wide retention sweeper CAN physically DELETE aged `audit_events` rows past `effective_audit_window` (`SET LOCAL app.audit_purge='on'`, the one sanctioned bypass of the immutability trigger at `migrations/f2a4c6e8b0d3`). An export cursor referencing a row that the sweeper purges mid-session simply stops matching the keyset predicate — no error, the row silently disappears from later pages. This is an honest-degrade case to state explicitly, not a defect to "fix" (§1/§2).
- `apps/gateway/migrations/versions/511ad8a7b65e_audit_events_actor_key_id.py` — current alembic head (`alembic heads` confirmed single head at ground time: `511ad8a7b65e`). A new migration for the proposed composite export index parents on this revision.
- `apps/gateway/tests/audit_read/conftest.py:87 seed_audit_event`, `:28-31 SIGNUP/LOGIN/AUDIT` constants, `:60 mint_role_token` — the exact fixture/seed pattern this task's own tests will extend (real Postgres+Redis, direct `audit_events` INSERT via `text()`, role tokens minted via `app.state.token_service.issue`). Confirms `created_at` seeds must be naive UTC (`_naive` helper, line 41-43) — same gotcha as above.

Context (working folder): no existing dashboard/admin UI surface for this task — the milestone's own "UI/UX in scope" list (MILESTONE.md line 16) names SCIM/SAML/domain-verification/retention settings only; compliance export is deliberately API-only in this milestone slice. `GLOSSARY.md` has no existing "cursor" or "compliance export" term — new Glossary delta below.

Honors (patterns / conventions):
- `.add/CONVENTIONS.md:13-16` — clean-architecture layering (`domain/` ← `application/` ← `infrastructure/` ← `api/`). The `gateway/audit/` bounded context currently has NO `api/` layer of its own (its only HTTP exposure today is a cross-module import inside `usage/api/router.py:741` and the superadmin variant `tenants/api/platform_audit_router.py`). This task is the first to give `audit/` its own `api/` layer — the architecturally correct home for a NEW audit-owned route, not another cross-import into `usage/api/router.py`.
- `.add/CONVENTIONS.md` (TDD delta, `reconciliation.py:104`) — naive-UTC bind normalization, cited above.
- `.add/PROJECT.md` Invariants — "Every tenant-owned row carries `tenant_id`; every query is tenant-scoped" and "No outbound IO without timeout + bounded retry (idempotent only) + circuit breaker" (the latter scoped to genuine upstream/network IO — this task's only IO is an internal Postgres read, so the applicable sub-slice is "timeout", matching the existing `get_audit`/`get_alerts` precedent of a bare `asyncio.timeout` with no CB/retry wrapper).
- MILESTONE.md shared decision (line 19): "All five surfaces are tenant-scoped config on EXISTING primitives... export reads the existing audit store. No parallel identity or audit store." — honored: no new table, no new store, additive index only.
- MILESTONE.md shared decision (line 23): "Compliance export never mutates... export access is itself audited." — both pinned as Musts below.

Anchors the contract cites: `usage/api/router.py:704 AuditEventItem` (item shape reused), `audit/infrastructure/audit_repository.py:27 AuditRepository` (extended with a new keyset method), `audit/domain/audit_event.py:26 AuditEvent` / `:73 record()` (reused verbatim for audit-of-export), `tenants/domain/authz.py:66 Permission.AUDIT_READ` / `:230 require_permission` (reused verbatim), `usage/application/reconciliation.py:104 _as_naive_utc` (reused pattern), `core/error_catalog.py` (two new `ErrorSpec` constants).

Issues/Risks (→ feed §1):
- Offset pagination is explicitly wrong for this use case (milestone requirement: stable across pages as new rows append) — must be keyset/cursor on `(created_at, id)`, a genuinely new repository method, not a reuse of `list_for_tenant_paged`.
- `id` is `uuid4()`, not time-ordered — cursor determinism holds, but "stable across pages" must be worded as deterministic-total-order, not true insertion-order-on-tie.
- No index covers `actor_email` or includes `id` — an additive composite index is in scope for a correctness-adjacent performance reason (archival page sizes are larger than the interactive 100-row cap), not gold-plating.
- Existing `get_audit`/`get_alerts` let `asyncio.TimeoutError` propagate uncaught (unhandled → framework default 500, not a catalog `ERR_*` shape) — acceptable at a 100-row interactive cap, but this task's larger page ceiling (§1) raises the odds of hitting it; catching it into an honest `ERR_EXPORT_TIMEOUT` 504 is a deliberate, recorded hardening beyond the mirrored precedent (fix-if-strictly-more-correct, PROJECT.md's own folded convention), not a silent copy.
- Retention sweeper can purge rows mid-export (rare but real under a short `retention_audit_floor_days`) — must be an explicit, tested, non-error scenario, not an unstated edge case.
- No "compliance officer" Role/Permission exists distinct from AUDIT_READ's owner/admin/operator set — flagged as a freeze question (§3) rather than silently inventing a narrower gate.

Related intent: MILESTONE.md "enterprise-identity-compliance" §Scope item (5) and Exit criterion "A compliance officer exports the tenant's audit trail filtered by time/actor with stable cursor pagination; the export itself appears in the audit log" (line 44). GLOSSARY.md has no "Audit event" vs "compliance export" distinction yet — new delta below mirrors the already-folded DDD lesson that "audit event" is its own bounded concept (PROJECT.md line 16).
Ground SHA: `2071046`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: GET /admin/audit/export — read-only, cursor-paginated, filtered compliance export over the immutable audit store
Framings weighed:
- **(chosen) new sibling route in a new `gateway/audit/api/` layer**, reusing `AuditEventItem`'s field shape + `AUDIT_READ` permission + `record_audit` writer, but with its OWN keyset-pagination repository method and its OWN envelope (no `total`, cursor instead of offset). Keeps the frozen `GET /admin/audit` v1 contract untouched; gives the audit bounded context its first `api/` layer (clean-architecture-correct).
- (rejected) widen `GET /admin/audit` itself with new query params (`since`/`until`/`actor_email`/`cursor`) — rejected because it is FROZEN @ v1 and archival-scale semantics (page cap 5000 vs interactive 100, NDJSON body) are a different contract, not a widening; touching it would force `total`+offset+cursor to coexist confusingly on one envelope.
- (rejected) async export job + object-store delivery (matches the milestone's explicit P2 backlog note) — rejected for THIS task: synchronous cursor-paginated pull is sufficient for a SIEM connector's incremental poll loop and needs no new infra (queue, object store, delivery webhook); revisit only if a real export exceeds request-lifetime bounds even at page_size=1.

Must:
<must>
  - M1 AUDIT_READ-gated: reuse `Permission.AUDIT_READ` unchanged (owner/admin/operator → 200; billing_admin/viewer/member → 403) — no new Permission enum member (mirrors `tenants/domain/authz.py:66`, avoids the PROJECT.md-documented "excludes OWNER" footgun for a fresh permission).
  - M2 Tenant-scoped: every row returned carries the caller's own `identity.tenant_id`; no filter combination (time range, actor, cursor) can widen visibility to another tenant's rows.
  - M3 Deterministic, append-safe ordering: `ORDER BY created_at DESC, id DESC`; keyset predicate `(created_at, id) < (:cursor_created_at, :cursor_id)` — a row inserted by ANY concurrent write during the export session, with `created_at` newer than the export's first-page boundary, is never re-surfaced or reshuffled into an already-issued page (keyset pagination only walks toward smaller `(created_at, id)`, so it is structurally immune to the classic offset-pagination "duplicate/skip on live append" defect).
  - M4 Cursor pagination only — no `offset` parameter. Cursor is an opaque base64url-encoded token carrying `{created_at, id}` of the last row of the previous page; absent cursor = first page.
  - M5 Page size bounded: `limit` 1..5000, default 1000 (materially larger than the interactive `get_audit` cap of 100 — this is an archival/SIEM-connector surface, not a UI page); non-integer or out-of-range → reject.
  - M6 Optional time-range filter: `since`/`until` (ISO-8601, both inclusive) on `created_at`; normalized via the existing `_as_naive_utc` pattern before binding. Absent = unbounded on that side.
  - M7 Optional actor filter: `actor_email` (exact match, case-insensitive) against `audit_events.actor_email`. Absent = all actors.
  - M8 Two response formats: default NDJSON (`Content-Type: application/x-ndjson`, one `AuditEventItem`-shaped JSON object per line, zero non-audit-row lines in the body); `?format=json` opt-in returns `{items: AuditEventItem[], next_cursor: str|null, has_more: bool}` — no `total` field (a second COUNT over an arbitrary/unfiltered range is a real cost with no archival-loop use; explicit, recorded divergence from `AuditListResponse`'s `{items,total}` shape, not a blind mirror).
  - M9 Cursor delivery for NDJSON: `X-Audit-Export-Next-Cursor` (present iff `has_more`) and `X-Audit-Export-Has-More: true|false` response headers — keeps every NDJSON body line a pure, homogeneous audit record (safe for a SIEM parser that ingests every line as-is; a trailing sentinel line was considered and rejected for exactly this reason).
  - M10 Read-only: the export path issues SELECT only; `audit_events` is never written by the export query itself.
  - M11 Export access is itself audited: on every 200, fire-and-forget one `record_audit` call (existing fail-open writer, `request.app.state.sessionmaker`), `action="audit.export"`, `result="success"`, `metadata={since, until, actor_email, cursor_used: bool, limit, row_count, format}`. An audit-write failure MUST NOT fail or delay the export response (reuses the writer's existing fail-open/separate-session contract verbatim — no new rollback logic needed).
  - M12 Bounded query time: `asyncio.timeout` around the DB read (mirrors `_AUDIT_READ_TIMEOUT_SECONDS` precedent); UNLIKE the mirrored `get_audit`/`get_alerts` precedent (which let `TimeoutError` propagate to an unhandled 500), this task catches it and raises a new catalog `ERR_EXPORT_TIMEOUT` (504) — recorded hardening beyond the mirrored surface, justified by the larger page ceiling (M5) raising real timeout odds; no retry, no circuit breaker (internal Postgres read, not upstream/network IO — matches every existing DB-read route in this codebase, none of which wrap a CB around a SELECT).
  - M13 Honest purge interaction: a row purged by the retention sweeper between two pages of the same export session simply stops matching the keyset predicate on the next page — no error, no gap marker, silently absent (documented behavior, not a defect).
</must>
Reject:
<reject>
  - missing/invalid bearer token -> "ERR_AUTH_INVALID_TOKEN" (401, unchanged auth dependency, mirrors get_audit)
  - caller's role lacks AUDIT_READ (billing_admin/viewer/member) -> "ERR_AUTH_FORBIDDEN" (403)
  - `since`/`until` not valid ISO-8601 -> "ERR_PAYLOAD_INVALID" (422)
  - `since` > `until` (inverted range) -> "ERR_PAYLOAD_INVALID" (422)
  - `limit` non-integer, < 1, or > 5000 -> "ERR_PAYLOAD_INVALID" (422)
  - `format` present and not one of `json`/`ndjson` -> "ERR_PAYLOAD_INVALID" (422)
  - `cursor` present but undecodable / malformed / wrong shape -> "ERR_CURSOR_INVALID" (422)
  - DB read exceeds the bounded query timeout -> "ERR_EXPORT_TIMEOUT" (504)
</reject>
After:
<after>
  - A compliance officer (owner/admin/operator) can pull their tenant's full audit trail, optionally time- and actor-filtered, as a sequence of bounded pages via an opaque cursor, in NDJSON (default, SIEM-ready) or JSON, with zero ability to see another tenant's rows and zero risk of a duplicate/skipped row from concurrent audit writes during the pull.
  - Every successful export page is itself a new `audit_events` row (`action="audit.export"`), fail-open, visible to a subsequent `GET /admin/audit` or export call — satisfying the milestone's "export access is itself audited" exit criterion.
  - The existing `GET /admin/audit` v1 contract, its tests, and its envelope are untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **NDJSON as the DEFAULT format** (JSON via `?format=json` opt-in) is the single lowest-confidence call in this draft — lowest confidence because it partially diverges from the project's stated preference ("the project favors mirroring existing frozen read envelopes") for a persona-driven SIEM-ergonomics reason (concatenation-safe across pages, no whole-array buffering, matches Splunk/Datadog/Elastic bulk-ingest convention) that Tin has not weighed in on; if wrong: a build ships the less-useful default and a follow-up task has to flip it (cheap to reverse pre-freeze, awkward post-freeze since `format` default is part of the frozen contract). See FREEZE-QUESTIONS in §3.
  - [ ] Reusing `Permission.AUDIT_READ` (owner/admin/operator) rather than a narrower "compliance officer" gate — confirm or deny; if a narrower gate is wanted, no such Role/Permission exists today and would be a size-up (new enum member + matrix edit), out of this task's additive scope unless Tin asks for it explicitly.
  - [ ] `limit` bounds (default 1000, max 5000) are a judgment call with no direct precedent in this codebase (the only sibling, `get_audit`, caps at 100 for an interactive UI, not an archival pull) — confirm the ceiling is acceptable given the `ERR_EXPORT_TIMEOUT` (504) safety valve at M12, or should be lower/higher.
  - [ ] Composite index `(tenant_id, created_at, id)` (+ `actor_email` as leaf or separate index) as an additive migration is proposed but not load-tested against production audit volume — confirm at BUILD/VERIFY with `EXPLAIN ANALYZE` on a realistic row count rather than assuming it's needed from the schema alone.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: owner/admin/operator can export   # M1
  Given a tenant with role owner (also: admin, also: operator)
  When they call GET /admin/audit/export
  Then the response is 200
  And the body is a valid NDJSON stream of AuditEventItem-shaped lines

Scenario: billing_admin/viewer/member cannot export   # M1, R2
  Given a tenant with role billing_admin (also: viewer, also: member)
  When they call GET /admin/audit/export
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And no audit_events row is written for this attempt (denied before any DB read)

Scenario: no bearer token   # R1
  Given no Authorization header
  When calling GET /admin/audit/export
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"
  And no audit-of-export row is written

Scenario: tenant isolation under export filters   # M2
  Given tenant A has 2 audit rows and tenant B has 1 audit row
  When tenant A's owner calls GET /admin/audit/export with no filters
  Then only tenant A's 2 rows are returned across all pages
  And tenant B's row never appears, even if actor_email/time-range filters would otherwise match it

Scenario: deterministic keyset ordering survives a concurrent insert   # M3
  Given a tenant with 5 audit rows seeded at distinct timestamps
  When the owner requests page 1 (limit=2), THEN a new audit row is inserted with created_at newer than all 5, THEN page 2 is requested with the cursor from page 1
  Then page 2 contains exactly the 3rd/4th oldest of the ORIGINAL 5 rows (not the new row, not a duplicate/skip of any original row)
  And the new row only ever appears on a fresh page 1 request (never mid-walk)

Scenario: cursor pagination walks the full set with no gaps or dupes   # M3, M4
  Given a tenant with 7 audit rows
  When the owner pages through with limit=3 following next_cursor until has_more=false
  Then the concatenation of all pages' ids equals the 7 seeded ids, each exactly once, in created_at DESC, id DESC order

Scenario: no offset parameter accepted   # M4
  Given a tenant owner
  When calling GET /admin/audit/export?offset=5
  Then offset is silently ignored (not a recognized parameter — no reject; cursor is the only paging mechanism)

Scenario: default and max page size   # M5
  Given a tenant with 1200 audit rows
  When the owner calls GET /admin/audit/export with no limit
  Then the first page returns exactly 1000 items (the default) with has_more=true

Scenario: limit out of bounds   # M5, R5
  Given a tenant owner
  When calling GET /admin/audit/export?limit=0 (also: limit=5001, also: limit=abc)
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no rows are read or returned

Scenario: since/until filter narrows results   # M6
  Given a tenant with audit rows at minute 1, 2, and 3
  When the owner calls GET /admin/audit/export?since=<minute 2 iso>&until=<minute 3 iso>
  Then only the minute-2 and minute-3 rows are returned (minute-1 excluded)

Scenario: malformed time-range value   # R3
  Given a tenant owner
  When calling GET /admin/audit/export?since=not-a-date
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: inverted time range   # R4
  Given a tenant owner
  When calling GET /admin/audit/export?since=<later iso>&until=<earlier iso>
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no rows are read

Scenario: actor_email filter narrows results   # M7
  Given a tenant with audit rows from actor "a@x.io" and actor "b@x.io"
  When the owner calls GET /admin/audit/export?actor_email=a@x.io
  Then only the "a@x.io" rows are returned, case-insensitively (e.g. "A@X.IO" matches too)

Scenario: NDJSON is the default format and is line-pure   # M8, M9
  Given a tenant with 3 audit rows
  When the owner calls GET /admin/audit/export with no format param
  Then Content-Type is application/x-ndjson
  And the body has exactly 3 lines, each a valid JSON object with the AuditEventItem fields, no extra sentinel/marker line
  And the X-Audit-Export-Has-More header is "false" and X-Audit-Export-Next-Cursor is absent

Scenario: JSON format opt-in   # M8
  Given a tenant with 3 audit rows
  When the owner calls GET /admin/audit/export?format=json
  Then Content-Type is application/json
  And the body is {"items": [...3 AuditEventItem...], "next_cursor": null, "has_more": false}
  And the body has no "total" field

Scenario: invalid format value   # R6? (payload-invalid family)
  Given a tenant owner
  When calling GET /admin/audit/export?format=csv
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: malformed cursor   # R7
  Given a tenant owner
  When calling GET /admin/audit/export?cursor=not-valid-base64-or-wrong-shape
  Then the response is 422 "ERR_CURSOR_INVALID"
  And no rows are read

Scenario: export never mutates audit_events   # M10
  Given a tenant with audit rows
  When the owner calls GET /admin/audit/export (any filters, any page)
  Then no audit_events row's existing fields change and no row is deleted as a side effect of the read

Scenario: successful export is itself audited   # M11
  Given a tenant owner exports page 1 successfully
  When a subsequent GET /admin/audit (or GET /admin/audit/export) call is made
  Then it includes one new row with action="audit.export", result="success", actor_user_id = the exporting owner, metadata containing since/until/actor_email/cursor_used/limit/row_count/format

Scenario: audit-of-export failure does not fail the export   # M11 (fail-open)
  Given the audit writer's session/sessionmaker raises on write (simulated)
  When the owner calls GET /admin/audit/export
  Then the export response is still 200 with the correct page body
  And the audit-write failure is only logged, never surfaced to the caller

Scenario: bounded query timeout surfaces honestly   # M12, R8
  Given a query that exceeds the export timeout budget (simulated slow session)
  When the owner calls GET /admin/audit/export
  Then the response is 504 "ERR_EXPORT_TIMEOUT"
  And no partial/truncated NDJSON body is sent as if it were a complete page

Scenario: purge mid-export is a silent, honest gap   # M13
  Given a tenant with rows older than the retention floor eligible for the next sweep
  When the owner holds a cursor from page 1, THEN the retention sweeper purges one of the remaining rows, THEN page 2 is requested
  Then page 2 returns the remaining un-purged rows with no error and no explicit "row missing" marker
  And the exported row count for that session is simply smaller than it would have been pre-purge (documented, not a defect)

Scenario: empty result set   # boundary
  Given a tenant with zero audit rows (or filters matching zero rows)
  When the owner calls GET /admin/audit/export
  Then the response is 200 with an empty NDJSON body (0 lines) and X-Audit-Export-Has-More: false
  And (format=json) {"items": [], "next_cursor": null, "has_more": false}

Scenario: last page boundary — exact multiple of limit
  Given a tenant with exactly 1000 audit rows and the default limit=1000
  When the owner requests page 1
  Then all 1000 rows are returned in one page with has_more=false and no next_cursor
  (i.e. has_more is computed from "was there a 1001st matching row", never inferred from row_count == limit alone)

Scenario: duplicate cursor request is idempotent   # concurrency/retry-safety
  Given the owner has a valid cursor from a prior page
  When the SAME cursor+params request is issued twice (simulating a client-side retry after a dropped response)
  Then both responses return byte-identical page content (absent an intervening purge/insert)
  And two separate audit-of-export rows are written (M11 fires per successful read, retries are not deduped — documented, not a defect)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Least-sure flag surfaced at freeze: [contract] NDJSON as the DEFAULT export body (cursor via response headers) — a deliberate divergence from the project's mirror-existing-envelope convention, chosen for SIEM/archival ergonomics; ?format=json preserves the house envelope. Decided at freeze (Tin, 2026-07-10 batch): all 4 agent recommendations accepted (NDJSON default; AUDIT_READ reuse; 1000/5000 page sizes; two narrow indexes, EXPLAIN at build).

```
GET /admin/audit/export
  query: limit?=1000 (1..5000) · cursor?=<opaque base64url> · since?=<ISO-8601> · until?=<ISO-8601>
       · actor_email?=<string, case-insensitive exact> · format?="ndjson"|"json" (default "ndjson")
  auth: Bearer JWT, Permission.AUDIT_READ (owner/admin/operator; billing_admin/viewer/member -> 403)

  200 (format=ndjson, default) ->
    Content-Type: application/x-ndjson
    Headers: X-Audit-Export-Has-More: "true"|"false"
             X-Audit-Export-Next-Cursor: <opaque>   # present iff Has-More: true
    Body: one JSON object per line, each shaped exactly like AuditEventItem:
      {"id": str, "actor_email": str|null, "action": str, "target_type": str|null,
       "target_id": str|null, "result": str, "metadata": object, "created_at": str}

  200 (format=json) ->
    Content-Type: application/json
    { "items": [ <AuditEventItem...> ], "next_cursor": str|null, "has_more": bool }
    # deliberately NO "total" field — divergence from AuditListResponse{items,total},
    # recorded here: a COUNT over an arbitrary/unfiltered range is a real cost with
    # no archival-loop use.

  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }        # missing/invalid bearer
  403 -> { code: "ERR_AUTH_FORBIDDEN" }             # role lacks AUDIT_READ
  422 -> { code: "ERR_PAYLOAD_INVALID" }            # bad since/until/limit/format, since > until
  422 -> { code: "ERR_CURSOR_INVALID" }             # malformed/undecodable cursor      [NEW]
  504 -> { code: "ERR_EXPORT_TIMEOUT" }             # query exceeded the bounded budget [NEW]

Schema (no new table — reads audit_events only):
  audit_events(id, tenant_id, actor_user_id, actor_key_id, actor_email, action,
               target_type, target_id, result, metadata, created_at)   -- existing, unchanged
  Access: SELECT ... WHERE tenant_id = :tid
                       [AND actor_email ILIKE :actor_email]
                       [AND created_at >= :since] [AND created_at <= :until]
                       [AND (created_at, id) < (:cursor_created_at, :cursor_id)]
          ORDER BY created_at DESC, id DESC
          LIMIT :limit + 1                          -- fetch one extra row to derive has_more
  Write (side effect, fire-and-forget): one INSERT into audit_events per successful 200
    via the existing record_audit() writer — action="audit.export".

New repository method (audit/infrastructure/audit_repository.py — additive, AuditRepository
gains one method; list_for_tenant / list_for_tenant_paged / count_for_tenant untouched):
  async def list_for_tenant_keyset(
      self,
      tenant_id: uuid.UUID,
      *,
      limit: int,
      cursor: tuple[datetime, uuid.UUID] | None = None,
      since: datetime | None = None,
      until: datetime | None = None,
      actor_email: str | None = None,
  ) -> list[AuditEvent]:
      """Keyset page over (created_at, id) DESC; caller fetches limit+1 to derive has_more."""
      ...

New API layer (gateway/audit/api/router.py — NEW file, audit's first api/ layer):
  audit_export_router = APIRouter(prefix="/admin/audit", tags=["audit"])

  @audit_export_router.get("/export")
  async def export_audit(
      request: Request,
      identity: Annotated[Identity, require_permission(Permission.AUDIT_READ)],
      session: Annotated[AsyncSession, Depends(get_session)],
      limit: Annotated[str | None, Query()] = None,
      cursor: Annotated[str | None, Query()] = None,
      since: Annotated[str | None, Query()] = None,
      until: Annotated[str | None, Query()] = None,
      actor_email: Annotated[str | None, Query()] = None,
      format: Annotated[str | None, Query()] = None,
  ) -> Response:
      """Mounted in main.py alongside platform_audit_router / usage_router (§0 anchors)."""
      raise NotImplementedError  # build-phase fills this in against the frozen shape above

Mount (main.py, additive import + include_router, mirrors platform_audit_router's own
two-line pattern at main.py:152/1136):
  from gateway.audit.api.router import audit_export_router
  app.include_router(audit_export_router)

Migration sketch (additive, parents on current head 511ad8a7b65e):
  revision: <new> audit_events_export_index
  down_revision: "511ad8a7b65e"
  def upgrade() -> None:
      op.create_index(
          "audit_events_tenant_created_id_idx",
          "audit_events",
          ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
      )
      op.create_index(
          "audit_events_actor_email_idx",
          "audit_events",
          ["actor_email"],
      )
  def downgrade() -> None:
      op.drop_index("audit_events_actor_email_idx", table_name="audit_events")
      op.drop_index("audit_events_tenant_created_id_idx", table_name="audit_events")
  # Confirm-at-BUILD per §1 assumption: EXPLAIN ANALYZE against a realistic row count
  # before committing to both indexes vs one; do not skip the measurement.

New error_catalog.py entries (additive, core/error_catalog.py):
  CURSOR_INVALID = ErrorSpec(422, "ERR_CURSOR_INVALID", "Export cursor is malformed or unrecognized")
  EXPORT_QUERY_TIMEOUT = ErrorSpec(
      504, "ERR_EXPORT_TIMEOUT", "Export query exceeded the time budget; narrow the range or reduce limit"
  )
```

Glossary deltas:
- **Compliance export**: a read-only, cursor-paginated, time/actor-filtered pull over the existing tenant audit trail (`audit_events`), distinct from the interactive `GET /admin/audit` admin-console read — same store, different envelope (no `total`, cursor not offset), different scale ceiling (limit up to 5000 vs 100), and a different default wire format (NDJSON) aimed at SIEM/archival consumers rather than a UI table.
- **Export cursor**: an opaque, base64url-encoded `(created_at, id)` keyset marker naming the last row of the previous export page; NOT a row offset — guarantees deterministic, append-safe paging over a live-appending store.
- **Audit-of-export**: the fire-and-forget `audit_events` row (`action="audit.export"`) that this endpoint itself writes on every successful page read, satisfying "compliance export never mutates [audit_events via the read] / export access is itself audited" (MILESTONE.md shared decision).

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — awaiting human freeze (this draft, plus FREEZE-QUESTIONS below, is the freeze report input)

### Scope (for whoever builds it — non-binding preferred plan, human freezes the shape above, not this list)
May touch:
- `apps/gateway/src/gateway/audit/api/__init__.py`, `apps/gateway/src/gateway/audit/api/router.py` — NEW (audit's first api/ layer)
- `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py` — additive method only
- `apps/gateway/src/gateway/core/error_catalog.py` — two additive `ErrorSpec` constants
- `apps/gateway/src/gateway/main.py` — additive import + `include_router` (mirrors existing two-line pattern)
- `apps/gateway/migrations/versions/<new>_audit_events_export_index.py` — NEW, additive indexes only
- `apps/gateway/tests/audit_export/` — NEW suite (mirrors `tests/audit_read/` fixture pattern)
Must NOT touch: `usage/api/router.py:728 get_audit` (frozen v1), `audit/domain/audit_event.py` (frozen, no signature change needed), `audit/application/audit_writer.py` (reused verbatim).

### FREEZE-QUESTIONS (Tin decides at freeze — each: options + recommendation)
1. **NDJSON-default vs JSON-default** (the ⚠ least-sure flag). Options: (a) NDJSON default, `?format=json` opt-in [recommended — SIEM/archival ergonomics, matches Splunk/Datadog/Elastic bulk-ingest convention, concatenation-safe across pages]; (b) JSON default (mirrors `AuditListResponse` exactly, `?format=ndjson` opt-in) [more consistent with "mirror existing frozen envelopes"]. Recommendation: (a), because the milestone explicitly names SIEMs as the consumer and the interactive-UI envelope precedent (`{items,total}`) doesn't fit a cursor/no-total shape anyway — the mirroring benefit is weaker here than for a same-shape UI read.
2. **AUDIT_READ reuse vs a narrower "compliance officer" gate.** Options: (a) reuse `Permission.AUDIT_READ` unchanged (owner/admin/operator) [recommended — no new enum member, no matrix edit, matches "additive on existing primitives"]; (b) new `Permission.AUDIT_EXPORT` restricted to owner/admin only (bulk pull is a larger exfil surface than a single paginated screen). Recommendation: (a) for this task's `sensitivity: data` framing; if Tin judges bulk export materially riskier than the interactive read, (b) is a small, contained addition (one enum member + 2 matrix rows) that a future task can layer in without touching this contract's shape.
3. **Page-size ceiling (limit default 1000 / max 5000).** Options: (a) as drafted; (b) lower ceiling (e.g. default 500 / max 2000) leaning on the new `ERR_EXPORT_TIMEOUT` less. Recommendation: (a), reversible via a config constant with zero contract-shape change either way — low cost to get "wrong" at freeze.
4. **Composite index scope** — one combined `(tenant_id, created_at DESC, id DESC)` index plus a separate `actor_email` index (as drafted), vs a single wider `(tenant_id, actor_email, created_at DESC, id DESC)` index. Recommendation: as drafted (two narrower indexes) — the actor filter is optional and a leading-column actor_email index would not help the (far more common) no-actor-filter keyset walk; confirm at BUILD with `EXPLAIN ANALYZE`, not by inspection alone (§1 assumption).

---

## Design self-score

Illustrative Python (router signature + repository method + ErrorSpec constants + migration sketch) syntax-checked via `python3 -m py_compile` against the exact §3 snippets — both compiled clean (no placeholder-shaped syntax errors, per the PROJECT.md-folded lesson about unverified contract code).

- Completeness: 0.93 — every M/R item has a scenario and a contracted response; migration, index, mount, and error-catalog additions are all named with exact file targets. Slightly short of 1.0 because the exact `record_audit` metadata field list (M11) and the `has_more` derivation (`limit+1` fetch) are specified but not yet cross-checked against a second sibling paginated-export precedent elsewhere in the codebase (none exists to check against — first of its kind here).
- Clarity: 0.95 — route, envelope, headers, and error codes are each stated once, unambiguously, with the frozen-precedent anchor they mirror or deliberately diverge from (each divergence is labeled, not left implicit).
- Practicality: 0.92 — every piece (repo method, router, migration, error constants) is additive-only against real, currently-existing symbols (§0 anchors), buildable without touching any frozen file. Held below 0.95 because the index strategy (FREEZE-QUESTION 4) is explicitly unverified pending an `EXPLAIN ANALYZE` at build time — a real, named gap, not hidden.
- Optimization: 0.91 — page-size ceiling and dual-index choice are reversible, low-blast-radius judgment calls (flagged, not silently asserted); NDJSON-header-cursor design avoids polluting the export stream, the one place a wrong call would have been costly to unwind post-freeze.
- Edge cases: 0.94 — covers empty result, exact-multiple-of-limit boundary, concurrent insert during pagination, mid-export purge, cursor tampering, timeout, and audit-write fail-open; duplicate-request idempotency is scenario'd explicitly (M11 fires per read, not deduped — documented rather than assumed away).
- Self-evaluation: 0.93 — the one genuinely open judgment call (format default) is surfaced as the ⚠ least-sure flag and echoed as FREEZE-QUESTION 1 with both options argued, not just recommended; three further judgment calls are also surfaced rather than folded silently into the draft.

All six ≥ 0.9 — no refinement pass required before reporting.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of `audit/api/router.py` + the new `AuditRepository.list_for_tenant_keyset` (line-level, this feature's own new code — the project-wide `--cov-fail-under=80` gate is measured across the whole tree at integration, not per-suite).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_export_roles_200[owner|admin|operator]: Given a tenant + 2 seeded rows / When GET /admin/audit/export as owner/admin/operator / Then 200, NDJSON, 2 valid AuditEventItem-shaped lines · covers: M1
  - test_export_roles_403[billing_admin|viewer|member]: Given a tenant / When called as billing_admin/viewer/member / Then 403 ERR_AUTH_FORBIDDEN + assert no audit.export row written · covers: M1, R2
  - test_export_no_bearer_token: Given no Authorization header / When called / Then 401 ERR_AUTH_INVALID_TOKEN + assert no audit.export row · covers: R1
  - test_export_tenant_isolation_under_filters: Given tenant A (2 rows) + tenant B (1 row, same actor_email, overlapping time) / When A's owner exports with matching filters / Then only A's 2 rows, B's row never leaks · covers: M2
  - test_export_keyset_survives_concurrent_insert: Given 5 rows + page1(limit=2) / When a newer row is inserted THEN page2 requested via cursor / Then page2 = original 3rd/2nd-newest rows, new row never appears mid-walk · covers: M3
  - test_export_cursor_walks_full_set_no_gaps_or_dupes: Given 7 rows / When paged with limit=3 until has_more=false / Then concatenated ids == all 7, DESC order, no dup/gap · covers: M3, M4
  - test_export_offset_param_ignored: Given 1 row / When called with ?offset=5 / Then 200 (offset silently ignored, never a reject) · covers: M4
  - test_export_default_page_size: Given 1200 rows / When called with no limit / Then first page = 1000 items, has_more=true · covers: M5
  - test_export_last_page_boundary_exact_multiple_of_limit: Given exactly 1000 rows, default limit / When called / Then all 1000 in one page, has_more=false, no next-cursor header · covers: M5 (boundary)
  - test_export_limit_out_of_bounds["0"|"5001"|"abc"]: When limit is 0/5001/abc / Then 422 ERR_PAYLOAD_INVALID + assert no audit.export row (no rows read) · covers: M5, R5
  - test_export_since_until_filter_narrows_results: Given rows at minute 1/2/3 / When since=min2&until=min3 / Then only min2+min3 returned · covers: M6
  - test_export_malformed_time_range: When since=not-a-date / Then 422 ERR_PAYLOAD_INVALID · covers: R3
  - test_export_inverted_time_range: When since>until / Then 422 ERR_PAYLOAD_INVALID + assert no rows read · covers: R4
  - test_export_actor_email_filter_case_insensitive: Given rows from a@x.io and b@x.io / When actor_email=A@X.IO / Then only a@x.io rows · covers: M7
  - test_export_ndjson_is_default_and_line_pure: Given 3 rows / When no format param / Then application/x-ndjson, exactly 3 valid-JSON lines, Has-More=false header, no Next-Cursor header · covers: M8, M9
  - test_export_json_format_opt_in: Given 3 rows / When format=json / Then application/json {items(3), next_cursor:null, has_more:false}, no "total" key · covers: M8
  - test_export_invalid_format_value: When format=csv / Then 422 ERR_PAYLOAD_INVALID · covers: R6
  - test_export_malformed_cursor / test_export_cursor_wrong_shape_valid_base64: When cursor is garbage / wrong-shape-but-valid-base64 / Then 422 ERR_CURSOR_INVALID + assert no rows read · covers: R7
  - test_export_never_mutates_audit_events: Given 3 seeded rows, snapshot fields / When exported (any page) / Then re-snapshot identical — read causes zero mutation · covers: M10
  - test_export_success_is_itself_audited: Given a successful export / When drained / Then one audit_events row action="audit.export", correct actor_user_id, metadata has since/until/actor_email/cursor_used/limit/row_count/format · covers: M11
  - test_export_audit_write_failure_does_not_fail_export: Given the audit writer's OWN session factory fails (2nd sessionmaker() call only) / When exported / Then response still 200 with correct body, failure only logged · covers: M11 (fail-open)
  - test_export_timeout_surfaces_honestly: Given AsyncSession.execute faulted to raise TimeoutError for the audit_events SELECT / When exported / Then 504 ERR_EXPORT_TIMEOUT, no partial NDJSON body · covers: M12, R8
  - test_export_purge_mid_export_is_silent_honest_gap: Given page1 cursor issued / When a remaining row is deleted THEN page2 requested / Then page2 silently omits the purged row, no error · covers: M13
  - test_export_empty_result_set: Given zero rows / When exported (both formats) / Then 200, empty NDJSON body (0 lines) / {items:[],next_cursor:null,has_more:false} · covers: boundary
  - test_export_duplicate_cursor_request_is_idempotent: Given a valid cursor / When the SAME cursor+params request fires twice / Then byte-identical bodies, 2 separate audit.export rows written · covers: concurrency/retry-safety
</test_plan>

Tests live in: `tests/audit_export` · 32 tests (25 scenario groups, 7 parametrize-expanded) — RAN red (missing implementation, 404 on every case — route not mounted) before Build; confirmed via a temporary git-stash isolation of the build-phase changes, re-run, then restored. All 32 green after Build; `tests/audit_read` (frozen v1, 15 tests) and `tests/migrations` (6 tests, incl. the new revision's autogenerate-empty-diff parity) re-ran green alongside.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/audit/api/` `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py` `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/versions/` `apps/gateway/tests/audit_export/`
Strategy (ordered batches): 1. Ground-read the 9 anchor files (§0) + existing `GET /admin/audit` v1 route, repo, ORM, authz, writer, error_catalog, retention_sweep, audit_read conftest — confirm every symbol still resolves. 2. Additive `ErrorSpec` entries (CURSOR_INVALID, EXPORT_QUERY_TIMEOUT). 3. `AuditRepository.list_for_tenant_keyset` (new method, existing methods untouched) + matching `Index` entries on `AuditEventRow.__table_args__` (parity with the migration, for `alembic check`). 4. New migration (additive, parents on `511ad8a7b65e`). 5. New `audit/api/router.py` (audit's first `api/` layer) + `main.py` mount. 6. Failing-first suite in `tests/audit_export/` (RED confirmed against pre-build code via a temporary git-stash isolation). 7. Green the suite; re-run `tests/audit_read` (frozen v1) + `tests/migrations` (autogenerate parity) alongside; `ruff check` + `pyright` clean; `EXPLAIN ANALYZE` the keyset query against 200k realistic rows.

Persona (required): backend-architect — clean-architecture layering lens (`domain/`←`application/`←`infrastructure/`←`api/`); audit's first `api/` layer follows the shipped bounded-context shape exactly; the router calls `AuditRepository` directly with no intervening use-case class, mirroring the EXISTING `get_audit` (v1) precedent for this same read-only audit surface — not a violation of the persona's port-and-adapter default, a documented parity choice for a simple parameterized read (no cross-aggregate invariant, no mutation beyond the pre-existing fail-open `record_audit` writer).
Spawn isolation (default): worktree (already isolated at `/Users/tindang/workspaces/tind-repo/ai-proxy-builds/compliance-export-api`, branch `build/compliance-export-api`) — no further subagent spawn needed, single-agent build.
Known-problem fixes: naive/aware `created_at` bind mismatch (test schema naive TIMESTAMP vs prod TIMESTAMPTZ) → local `_as_naive_utc` copy (mirrors `reconciliation.py:104`, kept local per the codebase's own "verbatim local copy, not a cross-module private import" convention at `keys/api/platform_keys_router.py`). `alembic check` autogenerate noise on expression/DESC indexes → ascending plain-column composite index instead (see Deviations). SQL-injection-shaped-but-safe test helper `where` clauses → `# noqa: S608` with an inline justification, matching the project's own precedent at `reconciliation.py`/`platform_tenant_config_router.py`.
Strategy actually used: as planned (§ above), plus one unplanned recovery step: `git stash` turned out to be a GLOBALLY SHARED stack across every linked worktree of this repo (all 8 wave-1 build worktrees + the primary checkout share one `.git`), so a stash push/pop used mid-build to prove RED collided with a concurrent stash operation from the sibling `build/per-key-guardrail-policies` builder — my audit changes and their `key_guardrail_router` changes briefly cross-applied into each other's working trees. Recovered by diffing each worktree, copying my own changes back via `git apply`/direct file copy (never touching their untracked files, only reading), restoring their worktree's foreign files to clean HEAD, and NEVER using `git stash` again for the remainder of this build. See Deviations + the report's residue/risks section — this is a build-team infrastructure hazard, not specific to this feature.
Safety rule (feature-specific): read-only surface — no write path exists on the hot request; the one write (`record_audit`, action="audit.export") is fire-and-forget on a SEPARATE session/transaction from the read, so an audit-write failure can never roll back or block the export response (reuses the existing writer's fail-open contract verbatim, no new transaction logic).
Code lives in: `apps/gateway/src/gateway/audit/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear. — honored: zero new third-party dependencies (stdlib `base64`/`json` + already-vendored `sqlalchemy`/`fastapi` only); the frozen §3 contract was not edited; no test was weakened (all 32 confirmed RED for missing-route/404 before any implementation existed, then GREEN after).

### Deviations (strictly-more-correct + harmless, recorded per the fix-and-record rule)
1. **Migration index shape**: §3's sketch used `sa.text("created_at DESC")`/`sa.text("id DESC")` expression-index columns. Built instead with plain ASCENDING columns `(tenant_id, created_at, id)` — Postgres scans an ascending btree index BACKWARD at equal cost for `ORDER BY created_at DESC, id DESC` (confirmed live via `EXPLAIN ANALYZE`: `Index Only Scan Backward using audit_events_tenant_created_id_idx`, 5.5ms over 200k rows), and plain columns keep `AuditEventRow.__table_args__` and the migration's `op.create_index` byte-for-byte comparable by `alembic check` — an expression index is a known source of noisy/silently-skipped autogenerate diffs. Index NAMES and the two-index FREEZE-QUESTION-4 shape ("as drafted") are unchanged.
2. **`actor_email` match**: §3's SQL sketch showed `actor_email ILIKE :actor_email`. Built instead as `lower(actor_email) = lower(:val)` — ILIKE's `%`/`_` wildcard semantics would silently turn an "exact, case-insensitive" filter (M7's own wording) into a pattern match if a caller's email happens to contain either character; the intended behavior is unchanged for every email without those characters, and strictly more correct for ones that have them.
3. **Keyset predicate SQL shape**: `tuple_((created_at, id)) < tuple_((:c, :i))` (SQLAlchemy `tuple_()`) is semantically what §3 describes, but its pyright stubs reject plain literal operands (`reportArgumentType`) under this project's strict pyright gate. Built as the equivalent OR/AND decomposition (`created_at < :c OR (created_at = :c AND id < :i)`) — identical predicate, identical query plan (confirmed via `EXPLAIN ANALYZE`), fully type-checks clean.
4. **ORM parity addition**: `audit_events_orm.py` was not named in §3's "May touch" list, but `AuditEventRow.__table_args__` needed the same two `Index(...)` declarations as the migration — otherwise `tests/migrations/test_migrations.py::test_autogenerate_empty_diff` (an existing, unmodified suite) would fail, since `alembic check` would see the DB-only indexes as pending drops. Necessary, additive-only, and the file IS covered by §0's own anchor (`audit_events_orm.py:44`).

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `tests/audit_export/` (32) + `tests/audit_read/` (15, frozen v1) = 47 passed, `GATEWAY_TEST_DATABASE_URL=gateway_test_vcea`, 2026-07-10
- [x] coverage did not decrease — new code: `audit/api/router.py` 100% (97/97 stmts), `audit/infrastructure/audit_repository.py` 86% (44 stmts, 6 missed = pre-existing untouched `list_for_tenant_paged` lines 69-80, not the new `list_for_tenant_keyset`)
- [x] no test or contract was altered during build — `git diff <Ground SHA 2071046> -- apps/gateway/src/gateway/usage/api/router.py` is EMPTY (frozen v1 route byte-identical); §5 Deviations are additive-only, none touch a test assertion
- [x] the green was EARNED, not gamed — adversarial refute-read done (self, appsec-engineer persona) — see Refute-read verdict below; two throwaway attack tests written and run (deleted after), both HELD
- [x] concurrency / timing of the risky operation is safe — confirmed via a genuine `asyncio.gather` two-walker concurrent-request test (not the builder's sequential page1→insert→page2 pattern) + a same-`created_at`-tie-break test — both PASSED, no dup/skip
- [x] no exposed secrets, injection openings, or unexpected dependencies — cursor values are typed/bound (json→datetime/uuid.UUID), never string-interpolated; malformed cursor fails closed to 422; zero new third-party deps
- [x] layering & dependencies follow CONVENTIONS.md — audit's first `api/` layer calls `AuditRepository` directly (documented parity choice vs the existing `get_audit` v1 precedent, not a violation); wiring confirmed (see Deep checks)
- [ ] a person reviewed and approved the change — Tin approved the FROZEN @ v1 contract (§3); the code-change review/GATE RECORD sign-off is the human step this verify pass feeds, not self-attestable here

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] GET /admin/audit (frozen v1) stays byte-identical — route, envelope, and its 15-test suite untouched and green — confirmed by `git diff 2071046 -- usage/api/router.py` (empty) + `tests/audit_read` re-run (15 passed)
- [x] keyset cursor on (created_at,id) never skips or duplicates rows when rows append between page fetches — confirmed by the builder's sequential concurrent-append test AND by my own TRUE-concurrency probe (two `asyncio.gather`'d overlapping export walks racing 5 concurrent inserts on separate sessions) — zero dup/skip in either walker
- [x] NDJSON default body stays SIEM-parser-pure (cursor via X-Audit-Export-* headers); ?format=json returns {items,next_cursor,has_more} with no total — confirmed by response-shape assertions on both formats (test_export_ndjson_is_default_and_line_pure, test_export_json_format_opt_in)
- [x] every 200 export fires an audit.export audit row via the existing fail-open writer — confirmed by test_export_success_is_itself_audited + test_export_audit_write_failure_does_not_fail_export (both green)
- [~] the keyset query uses the new index efficiently — NOT re-run at 200k rows (out of verify's time budget); instead confirmed the two indexes physically EXIST post-migration (`\d audit_events` on `gateway_migrations_test_vcea` shows `audit_events_tenant_created_id_idx (tenant_id, created_at, id)` + `audit_events_actor_email_idx (actor_email)`) and that ORM/migration parity holds (`test_autogenerate_empty_diff` passed) — the builder's EXPLAIN ANALYZE claim (backward index-only scan, 5.5ms) is mechanically plausible (standard Postgres btree backward-scan behavior) but not independently re-measured by me

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `list_for_tenant_keyset` referenced only by `audit/api/router.py:177`; `CURSOR_INVALID`/`EXPORT_QUERY_TIMEOUT` referenced in router.py imports+raises; `audit_export_router` imported+`include_router`'d in `main.py:31,1239` — confirmed via `grep -rn` across `src/`+`tests/`
- [x] DEAD-CODE (code) — no orphaned symbol; every new helper (`_parse_limit`, `_parse_format`, `_parse_iso_datetime`, `_as_naive_utc`, `_parse_time_range`, `_encode_cursor`, `_decode_cursor`) is called from `export_audit` itself
- [ ] SEMANTIC (prose / non-code) — n/a, this task has no prose/non-code deliverable

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — `AuditRepository.list_for_tenant_keyset`, `audit_export_router`/`export_audit`, `CURSOR_INVALID`/`EXPORT_QUERY_TIMEOUT` (error_catalog.py:865,868), migration `c20d0adece0a` (parents `511ad8a7b65e` as sketched) all read/confirmed directly in this session
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none moved; `usage/api/router.py:728 get_audit`/`:704 AuditEventItem`/`:719 AuditListResponse` all still resolve at the same lines confirmed via the empty ground-diff

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (appsec-engineer persona, verify pass) · adversarially checked: (1) tenant isolation under a cursor decoded from another tenant's own boundary values — structurally impossible to cross since `tenant_id` is bound from JWT `identity.tenant_id` in the SAME query, never from client input; (2) cursor as an injection vector — values are `json.loads`→`datetime.fromisoformat`/`uuid.UUID`-typed and parameter-bound, any malformed/wrong-shape/injection-shaped payload falls into the generic `except Exception` → `ERR_CURSOR_INVALID`, verified with a wrong-shape-valid-base64 case (existing test) plus manual trace of the decode path; (3) same-`created_at` tie-break correctness — wrote and ran a throwaway test seeding 9 rows at the IDENTICAL microsecond timestamp, paginated limit=2, got exactly the `id DESC` order with zero dup/gap (deleted after); (4) TRUE concurrent overlapping requests (not the builder's sequential simulation) — wrote and ran a throwaway `asyncio.gather` test with two full pagination walks racing 5 concurrent inserts on independent sessions, both walkers came back dup-free and gap-free on the pre-existing rows (deleted after); (5) RBAC matrix — confirmed `Permission.AUDIT_READ` in `ROLE_PERMISSIONS` includes OWNER/ADMIN/OPERATOR and excludes BILLING_ADMIN/VIEWER/MEMBER, no duplicate hand-rolled escalation table introduced; (6) frozen v1 route byte-identical — `git diff <Ground SHA>` on `usage/api/router.py` is empty; (7) fail-open audit-of-export — traced `record_audit`'s own session/exception-swallow contract, confirmed reused verbatim (not reimplemented). No vacuous asserts found (`assert_problem` checks status+code; tenant-isolation/idempotency tests assert exact id sets, not just status codes). No stubbed-away logic — the keyset predicate, cursor codec, and timeout wrap are all real, exercised code paths.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (appsec-engineer persona)
1. Security: CLEAR — tenant_id is sourced exclusively from the authenticated `identity`, never from cursor/query input, in the same WHERE clause that runs the keyset predicate; a tampered/wrong-tenant-shaped cursor cannot widen visibility, only fails closed to `ERR_CURSOR_INVALID`. RBAC matrix reused verbatim, no duplicate escalation table. No secret-shaped data in scope (audit rows only).
2. Concurrency: CLEAR — keyset pagination is structurally immune to offset-style skip/dup; independently re-verified under a REAL `asyncio.gather` concurrent-request attack (stronger than the builder's sequential simulation) and a same-`created_at` tie-break attack, both held.
3. Architecture: CLEAR — audit's first `api/` layer follows CONVENTIONS.md layering; router→repository direct call is a documented, precedent-matching parity choice (mirrors the existing `get_audit` v1 shape) not a violation; wiring confirmed, no dead code.
Verdict: PASS
Residue: none blocking this task. Named (out-of-scope, not this task's defect): `tests/migrations/test_migrations.py::test_upgrade_from_empty_parity` FAILs on this integration branch, but root-caused to an `{'request_logs'}` table mismatch owned by `gateway/logs/` (a different wave-1 task's payload-capture feature) — NOT `audit_events`/this task's migration. This task's own cited parity test, `test_autogenerate_empty_diff`, PASSES cleanly in isolation, and the two new indexes were confirmed physically present and byte-parity-matched against the ORM `__table_args__`. Flagging for the orchestrator so it isn't silently absorbed into this task's gate.
Binding: advisory — sensitivity: data (not `mechanical`)

### GATE RECORD
Reported: no — verify evidence gathered this session; the gate report/outcome recording is the orchestrator's step, not filled here per the verify-team dispatch contract (verify agents report a recommendation, the orchestrator records the gate)
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-10

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned (§ above), plus one unplanned recovery step: `git stash` turned out to be a GLOBALLY SHARED stack across every linked worktree of this repo (all 8 wave-1 build worktrees + the primary checkout share one `.git`), so a stash push/pop used mid-build to prove RED collided with a concurrent stash operation from the sibling `build/per-key-guardrail-policies` builder — my audit changes and their `key_guardrail_router` changes briefly cross-applied into each other's working trees. Recovered by diffing each worktree, copying my own changes back via `git apply`/direct file copy (never touching their untracked files, only reading), restoring their worktree's foreign files to clean HEAD, and NEVER using `git stash` again for the remainder of this build. See Deviations + the report's residue/risks section — this is a build-team infrastructure hazard, not specific to this feature.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

