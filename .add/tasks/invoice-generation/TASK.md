# TASK: Immutable monthly invoices from usage_records with per-line usage evidence

slug: invoice-generation · created: 2026-07-12 · stage: production
sensitivity: data
milestone: monetization-core
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — append-only usage ledger (`__tablename__ = "usage_records"`). Confirmed fields for invoice grouping/amounts: `id` (UUID PK), `tenant_id` (FK RESTRICT), `key_id` (UUID, no FK), `team_id` (UUID, nullable, no FK), `model_id` (Text), `cost_usd` (`Numeric(14,8)`), `prompt_tokens`/`completion_tokens` (Integer), `created_at` (TIMESTAMPTZ, `func.now()`), `raw` (JSONB, carries `request_id` via `raw->>'request_id'`, already indexed at `ix_usage_records_request_id`), `cost_basis`, `provider_cost`. **No `tags` column exists yet** — owned by sibling task `cost-attribution-tags`, which is ALSO `phase: ground` as of this read (not frozen) — see Issue R1.
- `apps/gateway/src/gateway/usage/application/recorder.py:UsageRecorder.record` (lines 174-339) — confirms `cost_usd` is computed ONCE at record time (`resolve_markup_pct` then either `provider_cost * (1 + markup_pct/100)` at line 248-253, or `compute_per_token_cost_usd(...)` at line 253 which itself applies `markup` at line 653) and PERSISTED as the final, already-billed, tenant-facing amount — not a pre-markup catalog cost. This is decisive for §1 M3: invoice generation is a pure `SUM(cost_usd)` aggregation over existing rows, never a second price path.
- `apps/gateway/src/gateway/usage/application/rate_card_resolver.py:resolve_markup_pct` — the ONE shared resolver (MILESTONE.md's binding rule #2); cited as the reason this task does NOT need to call it (§1 M3/M12), not a symbol this task invokes.
- `apps/gateway/src/gateway/usage/application/recorder.py:UsageRecorder.record_correction` (lines 473-566) — the EXACT existing signed-delta append-only correction precedent MILESTONE.md calls "the v33 reconciliation precedent": appends a NEW `usage_records` row with a caller-computed, possibly-NEGATIVE `cost_usd`, `cost_basis='provider'`, `raw={"correction": True, ...}` — never mutates a prior row; advisory Redis spend counters move by the signed delta via `INCRBYFLOAT`. This task's own `invoice_correction` document (§3) mirrors this shape one layer up (new row referencing `invoice_id`, signed `delta_usd`, original `invoice`/`invoice_line` rows never touched).
- `apps/gateway/src/gateway/usage/application/reconciliation.py:_money` (line 98, `Decimal(str(value))`) — the project's own float-avoidance idiom for coercing a DB numeric; this task's every SUM/aggregation reuses it verbatim (never `float()`).
- `apps/gateway/src/gateway/usage/application/flusher.py:165` (`... ON CONFLICT (id) DO NOTHING`, module docstring line 6 "Insert: ON CONFLICT (id) DO NOTHING — exactly-once semantic in ledger") — the project's established idempotent-insert idiom under concurrency; this task's month-close job reuses the identical idiom (`ON CONFLICT (tenant_id, period_start) DO NOTHING`) rather than relying on a bare `UNIQUE` constraint + caught `IntegrityError` (§1 M13).
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow` (lines 69-130) — read in full: `markup_pct` (Numeric(7,4)), `budget_usd_monthly`, `cache_enabled`, `guardrail_configs` — **no `currency` column, no `timezone` column** anywhere on `tenants`. Confirms the entire billing substrate (`cost_usd`, `markup_pct`, `budget_usd_monthly`) is USD-only with no per-tenant clock; invoices inherit both constraints (§1 M1, M4).
- `apps/gateway/src/gateway/usage/application/recorder.py:record` / `record_correction` (lines 438, 547) — `yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")` — the EXISTING month-bucketing convention for Redis spend counters. This task adopts the identical UTC-calendar-month boundary for invoice periods (§1 M1) rather than inventing a second month convention.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission` (line 55, `StrEnum`), `:ROLE_PERMISSIONS` (line 81, FROZEN @ v1 per its own docstring) — `Role.BILLING_ADMIN` (lines 108-114) exists today holding `BUDGETS_MANAGE`/`USAGE_READ`/`OPS_READ` but no permission over a financial-DOCUMENT read surface. This task adds ONE additive `Permission.INVOICES_READ` member (mirrors the `RATE_CARDS_MANAGE`/`LOGS_READ` additive precedent noted in the same file's comments) — granted OWNER (auto, `frozenset(Permission)`)/ADMIN/BILLING_ADMIN/SUPERADMIN; NOT OPERATOR/VIEWER/MEMBER (deliberately diverges from `AUDIT_READ`/`LOGS_READ`'s role set — BILLING_ADMIN, not OPERATOR, is the financial-document persona).
- `apps/gateway/src/gateway/audit/api/router.py:_encode_cursor` / `_decode_cursor` / `export_audit` (lines 120-190) and `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:AuditRepository.list_for_tenant_keyset` (line 109) — the keyset-cursor idiom (opaque base64 `(created_at,id)` DESC tuple, `or_(a < x, and_(a==x, id<y))` decomposed predicate, fetch `limit+1` to derive `has_more`) this task's `GET /admin/invoices` mirrors verbatim, substituting `(period_start,id)` for `(created_at,id)`.
- `apps/gateway/src/gateway/keys/api/router.py:rotate_key` (docstring: "Returns 404 for revoked or cross-tenant keys — no information leak") and `logs-explorer-api` TASK.md §3 `GET /admin/logs/{log_id}` — the tenant-404-invisible single-resource idiom this task's `GET /admin/invoices/{invoice_id}` mirrors exactly (unknown id and cross-tenant id return the identical 404).
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` (line 32, frozen dataclass `status, code, title_template`), `CURSOR_INVALID` (line 914, `ERR_CURSOR_INVALID`, 422), `PAYLOAD_INVALID` (line 188), `AUTH_FORBIDDEN` (line 89), `AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID` (lines 83, 86) — reused verbatim; this task adds three new `ErrorSpec` constants (§3).
- `apps/gateway/src/gateway/main.py:1264-1283` (`app.include_router(...)` block, includes `usage_router`, `audit_export_router`, `budget_router`) — the registration site this task's own `invoices_router` joins.
- `apps/gateway/src/gateway/usage/application/retention_sweep.py:RetentionSweeper`, `should_start_retention_sweep` + `main.py:543-556` (`asyncio.create_task(retention_sweeper.run_forever(...))` gated by `should_start_retention_sweep(_settings)`) — the ONLY existing "conditionally-started background loop" pattern in this codebase (no APScheduler/cron dependency exists). This task's month-close generation job mirrors this shape (a `run_forever()`-style loop with its own `should_start_invoice_generator(settings)` guard) rather than inventing a new scheduling primitive — but note the sweep precedent is AGE-based (days-since), not CALENDAR-boundary-based; this task is the first calendar-month-triggered loop (see Issue R4 / §1 ⚠).
- `apps/gateway/pyproject.toml` dependencies (read in full) — confirms NO PDF-generation library (`reportlab`/`weasyprint`/`fpdf` all absent) exists in this project; CSV needs no new dependency (stdlib `csv`). A PDF library is a genuine new allow-listed dependency this task must add at BUILD (Issue R2).
- `apps/gateway/src/gateway/teams/infrastructure/orm.py:TeamRow` (`name`, `UNIQUE(tenant_id,name)`) and `apps/gateway/src/gateway/keys/infrastructure/orm.py:ApiKeyRow` (`name`) — the tenant-scoped name lookups an invoice line's `team_id`/`key_id` can join against for a human-readable label (PDF/CSV display), queried the same way `logs-explorer-api` already does.
- `apps/gateway/migrations/versions/` — current alembic head confirmed via `uv run alembic heads` → single head `69cfdc584129`. A new invoice-schema migration parents on this revision (not created at design time, per dispatch rule).
- Bounded contexts under `apps/gateway/src/gateway/` (directory listing, confirmed): `agent_oauth, alerting, artifacts, audit, auth, batches, budgets, catalog, conversations, core, domain_capture, guardrail_analytics, keys, logs, memory, objectstore, observability, ops, proxy, rate_limits, scim, teams, tenants, usage, video` — **no `billing/` context exists**. This task opens a NEW `gateway/billing/` context (`domain/`, `application/`, `infrastructure/`, `api/`), following the exact precedent of `audit/`/`logs/` being recently added as new contexts by sibling tasks, rather than overloading `usage/`.

Context (working folder): MILESTONE.md `monetization-core` exit criterion 1 ("A tenant admin can download last month's invoice (PDF/CSV) whose total equals the sum of its usage-derived lines, and every line drills down to the usage rows that produced it"); the milestone's "Shared / risky contracts (freeze these first)" list names `invoice data model + GET /admin/invoices list/detail shape -> owning task invoice-generation` explicitly (line 27). Sibling `cost-attribution-tags` (a `depends-on` per MILESTONE.md line 34) is ALSO `phase: ground` as of this ground read — its `tags` column does not exist yet, so this task's group-by-tag Must is written to activate conditionally rather than hard-depend on an unfrozen sibling contract. Sibling `credits-ledger`/`plan-enforcement` are out of this task's touch surface (no code overlap found).

Honors (patterns / conventions):
- `.add/CONVENTIONS.md` clean-architecture layering (`domain/` ← `application/` ← `infrastructure/` ← `api/`) — honored by opening `gateway/billing/` as a new context with all four layers, matching `audit/`'s precedent (its own TASK.md notes "this task is the first to give `audit/` its own `api/` layer").
- CLAUDE.md IO design-for-failure rule — bounded `asyncio.timeout` on every list/detail/evidence read (mirrors `audit/export_audit`'s pattern); no outbound network IO in this task (internal Postgres only), so no retry/circuit-breaker wrapper is warranted (matches every existing DB-read route in this codebase).
- MILESTONE.md shared decisions (lines 19-23): "usage_records is the only ledger of truth" (honored — zero new usage-truth tables, invoices are a derived projection), "one resolver" (honored by construction, §1 M3), "append-only money... corrections are new signed-delta entries" (honored, §1 M6), "shared-seam discipline... re-check reused Pydantic/table shapes at BUILD time" (flagged for BUILD, not this draft).
- Project-established Decimal/provenance discipline (this codebase's billing-precision conventions, confirmed directly in `recorder.py`/`reconciliation.py` above): all money arithmetic in `Decimal`, never `float`; every persisted money figure traces to a named source row.

Seams consulted: none (`.add/SEAMS.md` not present in this repo, per the same absence noted in `compliance-export-api` TASK.md).

Anchors the contract cites: `usage/infrastructure/orm.py:UsageRecordRow` · `usage/application/recorder.py:record` (cost_usd-is-final-billed proof) · `usage/application/recorder.py:record_correction` (signed-delta shape) · `usage/application/reconciliation.py:_money` · `tenants/domain/authz.py:Permission` / `ROLE_PERMISSIONS` · `audit/api/router.py:_encode_cursor` / `_decode_cursor` · `audit/infrastructure/audit_repository.py:list_for_tenant_keyset` · `keys/api/router.py:rotate_key` (404-invisibility idiom) · `core/error_catalog.py:ErrorSpec` · `main.py` include_router block · `usage/application/retention_sweep.py:RetentionSweeper` / `should_start_retention_sweep` (job-loop precedent) · `teams/infrastructure/orm.py:TeamRow` · `keys/infrastructure/orm.py:ApiKeyRow` · alembic head `69cfdc584129`.

Issues/Risks (→ feed §1):
- R1 **tags-column dependency risk**: `cost-attribution-tags` (sibling, milestone-declared dependency) has not frozen its `usage_records.tags` shape as of this ground read. This task's group-by-tag grouping (§1 M2) is written to be structurally tolerant of the column's absence today and its exact name/type at BUILD time — a real coupling risk, not a hypothetical.
- R2 **no PDF library present** — `reportlab`/`weasyprint`/`fpdf` all absent from `pyproject.toml`; a new allow-listed dependency is required at BUILD (recommend `reportlab`: pure-Python wheel, no system-level cairo/pango dependency unlike `weasyprint` — a judgment call, not load-bearing on this frozen contract's shape).
- R3 **rounding has no existing convention to inherit**: `cost_usd` is stored at `NUMERIC(14,8)` and `reconciliation.py:_money` never rounds — no code path in this repo currently rounds money to cents. This task is the FIRST to need a cents-rounding rule for a customer-facing total; pinned explicitly at §1 M4 (not left implicit).
- R4 **no calendar-month job-trigger precedent**: `RetentionSweeper`/`ReconciliationDriftChecker` are AGE/interval-based loops (`run_forever` + `asyncio.sleep`), not calendar-boundary-based. Designing a month-close trigger + a draft-then-issue lifecycle is genuinely novel to this codebase — flagged as the §1 ⚠ least-sure decision.
- R5 **no existing "document" layer**: `record_correction` establishes the signed-delta pattern at the `usage_records` ROW layer; nothing analogous exists yet at an invoice/document layer (no credit-note concept anywhere in the codebase today). This task originates that pattern from scratch, informed by but not a mechanical reuse of `record_correction`.
- R6 **currency is USD-only by construction** — no per-tenant currency field exists anywhere; multi-currency is explicitly out of this task's scope (matches `cost_usd`'s naming).
- R7 **evidence-link volume**: a monthly invoice line can aggregate thousands of `usage_records` rows (one line = one grouping-key bucket for an entire month). Per-line evidence MUST be a re-query (filter predicate), never a materialized list of row ids stored on the line — pinned at §1 M7 to prevent an unbounded/duplicative evidence column.

Related intent: MILESTONE.md `monetization-core` (roadmap M1, Tin-confirmed 2026-07-12); Glossary deltas `invoice`/`evidence link` (MILESTONE.md line 24, this task is the owning task); PROJECT.md commercial-platform rationale ("a gateway operator bills their downstream tenants").

Ground SHA: 43ad492

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Immutable monthly tenant invoices generated from `usage_records`, with per-line usage evidence, PDF/CSV export, and a tenant-scoped admin list/detail API.

Framings weighed: **Generate-then-auto-issue after a short stabilization window** (chosen) · Generate-on-demand via an explicit admin "close month" action (rejected — no monetization-core task exposes an invoice-issuance admin UI action in wave-1; the milestone's exit criterion implies invoices simply exist without a manual trigger) · Always-current running DRAFT visible throughout the month, freezing only at the boundary (adopted as a REFINEMENT of the chosen framing, not a separate one — the draft is queryable mid-month, not materialized atomically).

Must:
<must>
  - M1 Invoice period = one UTC calendar month `[period_start, period_end)` where `period_start` = first-of-month `00:00:00Z` and `period_end` = first-of-next-month `00:00:00Z` (exclusive) — matches the existing `yyyymm = datetime.now(UTC).strftime("%Y%m")` convention already used for spend counters; no per-tenant timezone (none exists on `tenants`).
  - M2 Invoice-line grouping key is `(model_id, team_id, key_id)` always, PLUS the row's FULL canonical tag-SET as an ADDITIONAL grouping dimension. [AMENDED pre-freeze 2026-07-12, orchestrator, after cross-contract check: sibling `cost-attribution-tags` froze `usage_records.tags` as a JSONB MAP (up to 8 k/v pairs), not a scalar — per-tag line expansion would double-count a multi-tagged row's cost across lines. Money lines must PARTITION usage, so the grouping dimension is the whole tags object under deterministic canonicalization (sorted keys), `{}` = untagged.] Rows with `tags = '{}'` (or a codebase without the column — R1) roll into the base `(model_id, team_id, key_id)` line untouched; a tenant with zero tagged usage produces identical lines to today. The per-tag OVERLAPPING view stays in `cost-attribution-tags`' analytics endpoint, which is explicitly non-additive; invoice lines are the additive projection.
  - M3 Every `invoice_line.raw_amount_usd` is the exact `SUM(usage_records.cost_usd)` for rows matching its grouping key + invoice period — a pure aggregation over already-billed rows. This task NEVER calls `resolve_markup_pct` or any other price-computing function; the only "second price path" that could exist is deliberately absent.
  - M4 Rounding (pinned, no prior convention to inherit — R3): each `invoice_line.amount_usd` is `ROUND_HALF_UP` to 2 decimals (cents) from its full-precision `raw_amount_usd` AT GENERATION TIME and persisted as that fixed value (never recomputed on read). `invoice.total_usd` = the exact `SUM` of the ALREADY-ROUNDED persisted `amount_usd` values — summed-then-rounded is REJECTED in favor of rounded-then-summed specifically so the displayed total always equals the sum of displayed lines (the standard invoicing-UX invariant: a customer must never see rows that don't add up to the total printed below them). `invoice.raw_total_usd` (full-precision pre-rounding SUM) is also persisted, for reconciliation-drift comparison against `/ops/reconciliation`, but is never the billed figure.
  - M5 Immutability: once `invoice.status = 'issued'`, no field on the invoice or any of its `invoice_line` rows is ever UPDATEd or DELETEd, for the life of the system.
  - M6 Corrections are NEW documents, never edits: an `invoice_correction` row references the original `invoice_id`, carries its own signed (possibly negative) `delta_usd` + a required `reason` + actor, mirroring `record_correction`'s signed-delta-append shape one layer up. A tenant's corrected total = original issued `total_usd` + `SUM(invoice_corrections.delta_usd)` for that invoice, computed at READ time, never stored denormalized back onto the frozen invoice row.
  - M7 Every `invoice_line` carries an evidence QUERY, not a materialized row-id list: `(tenant_id, period_start, period_end, model_id, team_id, key_id, tags)` — tags matched by exact canonical-map equality (`{}` for base lines) — is the exact predicate a dedicated evidence endpoint re-runs against `usage_records`, keyset-paginated like `GET /admin/logs` — bounded regardless of how many thousands of usage rows a single line aggregates.
  - M8 `GET /admin/invoices` — tenant-scoped list, keyset-paginated `(period_start, id) DESC`, mirrors `list_for_tenant_keyset`'s pattern (fetch `limit+1` to derive `has_more`, opaque base64 cursor). `GET /admin/invoices/{invoice_id}` — full detail with all lines + corrections. Both are tenant-404-invisible for an unknown OR cross-tenant id (mirrors `rotate_key`/logs-explorer-api's detail-fetch idiom).
  - M9 `GET /admin/invoices/{invoice_id}/export?format=pdf|csv` — PDF renders a statement (tenant name, period, issued date, line table, tax line, total); CSV emits one row per `invoice_line`, stable columns, machine-readable. Both derive from the SAME persisted, already-rounded line/total figures — they can never disagree with each other or with the API detail response.
  - M10 Empty month: a tenant with zero matching `usage_records` for the period still gets an issued invoice (`total_usd = 0.00`, zero lines) — generation NEVER silently skips a tenant; an admin sees "you were billed $0", not an absent invoice indistinguishable from a bug.
  - M11 New `Permission.INVOICES_READ` (additive to the FROZEN rbac matrix) gates all read endpoints; granted OWNER (auto)/ADMIN/BILLING_ADMIN/SUPERADMIN; NOT OPERATOR/VIEWER/MEMBER.
  - M12 A mid-period rate-card change (tenant's `markup_pct` or a `tenant_rate_card_entries` override edited mid-month) requires NO special invoice-generation handling: because each `usage_records.cost_usd` already carries its own point-in-time-resolved value, the invoice line `SUM` is automatically correct/blended across the change with zero extra logic. Stated as a Must precisely because it is the easiest thing to wrongly assume needs special-casing.
  - M13 Generation is idempotent and bounded under concurrency: the `invoices` INSERT uses `ON CONFLICT (tenant_id, period_start) DO NOTHING` (the exact idempotent-insert idiom `flusher.py`'s `ON CONFLICT (id) DO NOTHING` already establishes for `usage_records` — not a bare `UNIQUE` constraint relied on to raise/catch `IntegrityError`, which would make two concurrently-racing generation workers non-idempotent in the ordinary sense). A re-run — whether sequential (already issued) or a genuine concurrent race (two workers reach the same tenant/period simultaneously) — resolves to exactly one row, silently, never a duplicate and never a crash. The per-tenant generation step runs under a bounded `asyncio.timeout`.
  - M14 Extension point for wave-2 seat-billing (NOT designed here, per dispatch scope): `invoice_line.line_type` is an additive `TEXT NOT NULL DEFAULT 'usage'` discriminator column, reserving (not implementing) `'seat'`/`'proration'` values for the sibling `seat-billing` task — added now so that task's migration does not need to alter this frozen table shape.
</must>

Reject:
<reject>
  - no/malformed bearer token on any invoices endpoint -> "ERR_AUTH_INVALID_TOKEN"
  - caller's role lacks `Permission.INVOICES_READ` (operator/viewer/member) -> "ERR_AUTH_FORBIDDEN"
  - `GET /admin/invoices?limit=` non-integer, `< 1`, or `> 100` -> "ERR_PAYLOAD_INVALID"
  - `GET /admin/invoices?cursor=` undecodable / malformed / wrong shape -> "ERR_CURSOR_INVALID"
  - `GET /admin/invoices/{invoice_id}` for an unknown OR another tenant's id -> "ERR_INVOICE_NOT_FOUND"
  - `GET /admin/invoices/{invoice_id}/lines/{line_id}/evidence` for an unknown invoice, unknown line, a line not belonging to that invoice, or a cross-tenant invoice -> "ERR_INVOICE_NOT_FOUND"
  - `GET /admin/invoices/{invoice_id}/export?format=` absent or not one of `pdf`/`csv` -> "ERR_PAYLOAD_INVALID"
  - any list/detail/evidence read exceeds its bounded query timeout -> "ERR_INVOICE_QUERY_TIMEOUT"
</reject>

Note: `ERR_INVOICE_IMMUTABLE` (409) is added to the error catalog now (§3) as a RESERVED code for a future mutating route (e.g. a manual-issue or admin-override surface no wave-1/wave-2 task exposes yet) — deliberately NOT listed as a Reject here since v1 exposes zero route that could trigger it, and an unreachable rejection has no observable scenario (would violate this section's own EXIT gate). M5's immutability invariant is enforced by the DB/application guard itself (§1 M5, tested directly against the write path, not via a nonexistent HTTP route).
After:
<after>
  - A billing_admin/owner/admin/superadmin can list and page their tenant's invoices newest-period-first, open one, see every line with its grouping key and amount, drill any line down to the exact `usage_records` rows behind it, and download the same document as PDF or CSV — API total, PDF total, and CSV row-sum always agree exactly.
  - An issued invoice is provably immutable: byte-identical on every re-read, forever; a billing correction never rewrites history, only appends a new signed-delta document, net-summed at read time.
  - A month with zero usage still produces a $0 invoice, never a gap indistinguishable from a bug.
  - No second price path exists anywhere in this task's code — every dollar traces back to a `usage_records.cost_usd` value the existing recorder already wrote.
  - `seat-billing` can add seat/proration lines to this table later via `line_type` alone, with zero migration to this task's frozen columns.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Auto-issue after a short stabilization window (no manual "issue" action) is the single lowest-confidence call in this draft** — lowest confidence because there is ZERO existing precedent in this codebase for a draft-then-auto-freeze document lifecycle (every append-only precedent, including `record_correction`, writes one final row immediately; no "draft" state exists anywhere in the current schema), and the window's LENGTH is an arbitrary business call with real consequences: too short (e.g. a few hours) risks freezing before a slow/retried request's usage row lands — a wrong invoice, fixable only via a correction document, not a re-generation; too long (e.g. a week) delays "download last month's invoice on the 1st," which reads awkwardly against the milestone's own stated exit criterion. Proposing **72 hours** after `period_end` as the stabilization window before auto-issue. If wrong: flipping to a manual admin-triggered "issue" action after freeze is a real contract change (a new mutating endpoint plus a genuine draft-editing surface that today doesn't need to exist) — cheap to decide now, expensive to reverse once BUILD has shipped the auto-issue job. **Surfaced as the freeze flag.**
  - [ ] Whether `Permission.INVOICES_READ` should also reach OPERATOR (who holds `USAGE_READ`/`OPS_READ`/`AUDIT_READ`/`LOGS_READ` today, but is an infra/ops role, not a financial-document viewer in this project's existing role taxonomy) — recommend NO; confirm or deny at freeze.
  - [ ] The PDF library choice (`reportlab` recommended — pure-Python, no system cairo/pango dependency unlike `weasyprint`) is a BUILD-time allow-list addition, not load-bearing on this frozen contract's shape — confirm at BUILD, non-blocking for this freeze.
  - [ ] Whether an invoice line should also split by `pricing_unit`/`cost_basis` (a tenant mixing per-token and per-request-unit models, or catalog- and provider-basis rows, within one month) — recommend folding these into a single `(model_id, team_id, key_id, tags)` line regardless of internal pricing mechanism (a customer-facing line is about WHO/WHAT was billed, not the internal pricing plumbing) — confirm or deny at freeze.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Generation buckets usage into calendar-month lines   # M1
  Given a tenant with usage_records rows spanning 2026-06-30T23:59:59Z through 2026-07-01T00:00:01Z
  When the month-close job generates the July 2026 invoice
  Then the invoice's period_start is 2026-07-01T00:00:00Z and period_end is 2026-08-01T00:00:00Z
  And the 2026-06-30T23:59:59Z row is excluded while the 2026-07-01T00:00:01Z row is included

Scenario: Untagged usage groups by (model, team, key) only   # M2
  Given a tenant's July usage_records all carry tags = {} (or the tags column does not exist yet)
  When the July invoice is generated
  Then every invoice_line has tags = {} and lines are grouped solely by (model_id, team_id, key_id)
  And no line is dropped or merged incorrectly for the absence of tag data

Scenario: Tagged usage adds a tag-set grouping dimension   # M2
  Given a tenant's July usage_records include rows with tags {"project":"alpha"} and rows with tags {}, same model/team/key
  When the July invoice is generated
  Then a distinct invoice_line exists for the {"project":"alpha"} subset, separate from the untagged subset's line
  And the untagged rows' line amount excludes every cost_usd from the tagged rows

Scenario: Multi-tag rows partition, never double-count   # M2 (pre-freeze amendment)
  Given a July usage_records row with tags {"project":"alpha","env":"prod"} and cost_usd 1.00
  When the July invoice is generated
  Then exactly ONE invoice_line (the {"env":"prod","project":"alpha"} canonical tag-set line) includes that row's 1.00
  And SUM(invoice_lines.raw_amount_usd) over the invoice equals SUM(usage_records.cost_usd) for the period exactly

Scenario: Line amount is a pure sum of already-billed rows   # M3
  Given a tenant has 3 usage_records rows for the same (model,team,key) in July, with cost_usd 1.00000000, 2.00000000, 0.50000000
  When the July invoice is generated
  Then that invoice_line's raw_amount_usd equals 3.50000000
  And resolve_markup_pct is never invoked during generation (no second price path)

Scenario: Rounded-then-summed total matches the printed lines   # M4
  Given a tenant has two invoice_lines with raw_amount_usd 10.005 and 10.005
  When the invoice is generated
  Then each line's amount_usd is 10.01 (ROUND_HALF_UP) and invoice.total_usd is 20.02
  And 20.02 equals the exact sum of the two persisted, displayed line amounts (not 20.01 from summing full precision first)

Scenario: Issued invoice cannot be mutated   # M5
  Given an invoice with status='issued'
  When any code path attempts to UPDATE a field on that invoice or one of its invoice_lines
  Then the write is rejected (DB constraint or application guard, decided at BUILD)
  And a re-read of the invoice returns byte-identical values to before the attempt

Scenario: A correction is a new document, not an edit   # M6
  Given an issued invoice with total_usd = 100.00
  When an operator posts an invoice_correction with delta_usd = -15.00 and reason "duplicate key double-counted"
  Then the original invoice row and all its invoice_lines are unchanged (still total_usd = 100.00)
  And GET /admin/invoices/{id} reports corrected_total_usd = 85.00, computed from invoice.total_usd + SUM(corrections)

Scenario: Evidence drill-down resolves a disputed line to real usage rows   # M7
  Given an invoice_line for (model_id="gpt-4o", team_id=T, key_id=K) covering July, backed by 500 usage_records rows
  When a billing_admin calls GET /admin/invoices/{id}/lines/{line_id}/evidence
  Then the response paginates through exactly those 500 usage_records rows (by tenant+period+grouping-key predicate), newest-first
  And no row belonging to a different model/team/key or a different period appears in any page

Scenario: List returns only the caller's tenant, newest-period-first   # M8
  Given tenant A has 3 issued invoices and tenant B has 2, both queried by an owner-role identity
  When tenant A's owner calls GET /admin/invoices with default limit
  Then the response contains exactly tenant A's 3 invoices, ordered period_start DESC, id DESC
  And none of tenant B's invoices appear anywhere in items

Scenario: PDF, CSV, and API total always agree   # M9
  Given an issued invoice with 4 lines totaling 42.17
  When the same invoice is fetched via GET .../export?format=pdf, format=csv, and the plain detail endpoint
  Then the PDF's printed total, the CSV's summed amount_usd column, and the JSON total_usd are all exactly 42.17

Scenario: Zero-usage month still produces an invoice   # M10
  Given a tenant with zero usage_records rows in August 2026
  When the month-close job runs for August
  Then an issued invoice exists for that tenant with period covering August, total_usd = 0.00, and zero lines
  And GET /admin/invoices lists it like any other invoice, not omitted

Scenario: billing_admin can read invoices, operator cannot   # M11, R2
  Given a tenant identity with role billing_admin (also: owner, also: admin, also: superadmin)
  When they call GET /admin/invoices
  Then the response is 200
  Given a tenant identity with role operator (also: viewer, also: member)
  When they call GET /admin/invoices
  Then the response is 403 "ERR_AUTH_FORBIDDEN"

Scenario: Mid-month markup change requires no special handling   # M12
  Given a tenant's markup_pct is 20% for the first half of July and changed to 30% for the second half
  When the July invoice is generated
  Then the invoice line's raw_amount_usd equals the exact sum of each row's already-resolved cost_usd (blended correctly)
  And generation code performs no markup-aware branching or re-resolution of any kind

Scenario: Re-running month-close for an already-issued period is a no-op   # M13
  Given a tenant already has an issued invoice for July 2026
  When the month-close job is re-run (e.g. after a restart) and reaches that tenant/period again
  Then no second invoice is created and the existing invoice/lines are untouched
  And no error is raised — the run is a silent, idempotent skip

Scenario: Two concurrent generation workers racing the same tenant/period never duplicate   # M13
  Given no invoice yet exists for tenant T's July 2026 period
  When two generation workers both attempt to INSERT the July invoice for tenant T at the same moment
  Then exactly one invoices row exists for (tenant_id=T, period_start=July) afterward
  And neither worker raises an unhandled error (ON CONFLICT DO NOTHING resolves the race silently)

Scenario: seat-billing extension point exists but is inert   # M14
  Given the invoice-generation build has shipped
  When an invoice_line row is inspected
  Then it carries line_type = 'usage' for every row this task produces
  And no 'seat' or 'proration' row is ever written by this task's own code

Scenario: no bearer token   # R1
  Given no Authorization header
  When calling GET /admin/invoices
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"
  And no invoice data is returned in the body

Scenario: invalid limit is rejected   # R3
  Given an authenticated billing_admin
  When calling GET /admin/invoices?limit=0 (also: limit=101, also: limit=abc)
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no invoices are listed or leaked in the error body

Scenario: malformed cursor is rejected   # R4
  Given an authenticated billing_admin
  When calling GET /admin/invoices?cursor=not-valid-base64
  Then the response is 422 "ERR_CURSOR_INVALID"
  And pagination state is unchanged (no partial page is returned)

Scenario: unknown invoice id is 404   # R5
  Given a billing_admin identity and an invoice_id that does not exist at all
  When calling GET /admin/invoices/{invoice_id}
  Then the response is 404 "ERR_INVOICE_NOT_FOUND"

Scenario: cross-tenant invoice id is the SAME 404, not a leak   # R5
  Given tenant A's billing_admin and an invoice_id that belongs to tenant B
  When tenant A's billing_admin calls GET /admin/invoices/{that_id}
  Then the response is 404 "ERR_INVOICE_NOT_FOUND", byte-identical in shape to the unknown-id case
  And no field in the error body distinguishes "exists but not yours" from "doesn't exist"

Scenario: evidence request against a mismatched line is 404   # R6
  Given invoice X owns line L1, and a different invoice Y owns line L2
  When calling GET /admin/invoices/{X}/lines/{L2}/evidence (L2 does not belong to X)
  Then the response is 404 "ERR_INVOICE_NOT_FOUND"

Scenario: bad export format is rejected   # R7
  Given an authenticated billing_admin and an existing invoice
  When calling GET /admin/invoices/{id}/export?format=xlsx
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no partial file is streamed

Scenario: bounded query timeout surfaces as a structured error   # R8
  Given the invoices list query exceeds its bounded asyncio.timeout
  When GET /admin/invoices is called
  Then the response is 504 "ERR_INVOICE_QUERY_TIMEOUT"
  And no partial/inconsistent page is returned
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/invoices?limit=&cursor=
  200 -> { items: InvoiceListItem[], next_cursor: str|null, has_more: bool }
  401 -> { error: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  422 -> { error: "ERR_PAYLOAD_INVALID" | "ERR_CURSOR_INVALID" }
  504 -> { error: "ERR_INVOICE_QUERY_TIMEOUT" }

GET /admin/invoices/{invoice_id}
  200 -> { id, tenant_id, period_start, period_end, status, currency, total_usd, raw_total_usd,
           tax_usd, corrected_total_usd, issued_at, created_at,
           lines: InvoiceLineItem[], corrections: InvoiceCorrectionItem[] }
  401 / 403 -> same as above
  404 -> { error: "ERR_INVOICE_NOT_FOUND" }
  504 -> { error: "ERR_INVOICE_QUERY_TIMEOUT" }

GET /admin/invoices/{invoice_id}/lines/{line_id}/evidence?limit=&cursor=
  200 -> { items: UsageEvidenceItem[], next_cursor: str|null, has_more: bool }
  401 / 403 -> same as above
  404 -> { error: "ERR_INVOICE_NOT_FOUND" }   # unknown invoice, unknown line, cross-tenant, or line not owned by invoice
  422 -> { error: "ERR_CURSOR_INVALID" | "ERR_PAYLOAD_INVALID" }
  504 -> { error: "ERR_INVOICE_QUERY_TIMEOUT" }

GET /admin/invoices/{invoice_id}/export?format=pdf|csv
  200 -> binary body (application/pdf) or text/csv, Content-Disposition: attachment; filename="invoice-{period}.{ext}"
  401 / 403 -> same as above
  404 -> { error: "ERR_INVOICE_NOT_FOUND" }
  422 -> { error: "ERR_PAYLOAD_INVALID" }     # format absent or not pdf/csv

InvoiceListItem: { id, period_start, period_end, status, total_usd, currency, issued_at }
InvoiceLineItem: { id, model_id, team_id, key_id, tags, amount_usd, prompt_tokens, completion_tokens,
                    request_count, line_type }
InvoiceCorrectionItem: { id, delta_usd, reason, created_by, created_at }
UsageEvidenceItem: { usage_record_id, created_at, model_id, prompt_tokens, completion_tokens, cost_usd, request_id }
```

Schema (new tables; migration parents on alembic head `69cfdc584129`, not created at design time):
```
invoices
  id              UUID PK (uuid7)
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
  period_start    TIMESTAMPTZ NOT NULL   -- UTC month start, inclusive
  period_end      TIMESTAMPTZ NOT NULL   -- UTC month start+1, exclusive
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','issued'))
  currency        TEXT NOT NULL DEFAULT 'USD'         -- fixed; extension point only, no multi-currency logic (R6)
  total_usd       NUMERIC(12,2) NOT NULL DEFAULT 0     -- SUM of rounded, persisted invoice_lines.amount_usd (M4)
  raw_total_usd   NUMERIC(14,8) NOT NULL DEFAULT 0     -- pre-rounding SUM, audit/reconciliation-drift only, never billed
  tax_usd         NUMERIC(12,2) NOT NULL DEFAULT 0     -- configurable flat tax line; always 0 in v1 (no tax config exists on tenants)
  issued_at       TIMESTAMPTZ NULL
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  UNIQUE (tenant_id, period_start)                     -- idempotent generation (M13)

invoice_lines
  id                  UUID PK (uuid7)
  invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT
  model_id            TEXT NOT NULL
  team_id             UUID NULL              -- no FK, mirrors usage_records.team_id (append-only, no cascade)
  key_id              UUID NOT NULL          -- mirrors usage_records.key_id (NOT NULL there)
  tags                JSONB NOT NULL DEFAULT '{}'  -- canonical (sorted-key) tag-set grouping dimension; {} = untagged base line (M2, R1; matches cost-attribution-tags' usage_records.tags map shape)
  amount_usd          NUMERIC(12,2) NOT NULL -- rounded, billed, persisted at generation time (M4)
  raw_amount_usd      NUMERIC(14,8) NOT NULL -- pre-rounding SUM(usage_records.cost_usd) for this bucket
  prompt_tokens       BIGINT NOT NULL DEFAULT 0
  completion_tokens   BIGINT NOT NULL DEFAULT 0
  request_count       INTEGER NOT NULL DEFAULT 0
  line_type           TEXT NOT NULL DEFAULT 'usage'    -- seat-billing extension point (M14): reserves 'seat'/'proration', not implemented here
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()

invoice_corrections
  id            UUID PK (uuid7)
  invoice_id    UUID NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT
  delta_usd     NUMERIC(12,2) NOT NULL   -- signed; may be negative (M6, mirrors record_correction)
  reason        TEXT NOT NULL
  created_by    TEXT NOT NULL            -- actor identity (email), audit trail
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
```

Access pattern: `GET /admin/invoices` — keyset `(period_start, id) DESC` filtered `WHERE tenant_id = identity.tenant_id`, `fetch limit+1`, mirrors `AuditRepository.list_for_tenant_keyset`. `GET /admin/invoices/{id}` and the evidence/export endpoints — `WHERE id = :id AND tenant_id = identity.tenant_id` (unmatched row → `ERR_INVOICE_NOT_FOUND`, never a distinguishable leak). Evidence — `usage_records WHERE tenant_id=:t AND created_at >= period_start AND created_at < period_end AND model_id=:m AND key_id=:k AND team_id IS NOT DISTINCT FROM :team_id AND tags = :tags` (exact JSONB map equality; `'{}'` for base lines — rows predating the tags column read as `{}` via the column's server_default), keyset `(created_at, id) DESC`.

RBAC delta: `apps/gateway/src/gateway/tenants/domain/authz.py:Permission` gains `INVOICES_READ = "invoices_read"` (additive); `ROLE_PERMISSIONS[Role.ADMIN]`, `[Role.BILLING_ADMIN]`, `[Role.SUPERADMIN]` gain it (`Role.OWNER` auto-holds via `frozenset(Permission)`); `Role.OPERATOR`/`Role.VIEWER`/`Role.MEMBER` do NOT.

Error-catalog delta: `apps/gateway/src/gateway/core/error_catalog.py` gains three new `ErrorSpec` constants:
```
INVOICE_NOT_FOUND     = ErrorSpec(404, "ERR_INVOICE_NOT_FOUND", "Invoice not found")
INVOICE_QUERY_TIMEOUT = ErrorSpec(504, "ERR_INVOICE_QUERY_TIMEOUT", "Invoice query exceeded its time budget")
INVOICE_IMMUTABLE     = ErrorSpec(409, "ERR_INVOICE_IMMUTABLE", "Issued invoices cannot be modified")
```
`PAYLOAD_INVALID` and `CURSOR_INVALID` are reused verbatim (no new constant).

Registration: `apps/gateway/src/gateway/main.py` gains one `app.include_router(invoices_router)` alongside the existing block at lines 1264-1283.

Glossary deltas: **invoice** — an immutable monthly statement derived from `usage_records`, one row per (tenant, UTC calendar month), issued after a stabilization window. **invoice line** — a grouped, rounded, evidence-linkable amount within an invoice, keyed by (model, team, key, canonical tag-set). **evidence link** — the re-queryable `(tenant, period, grouping-key)` predicate that resolves an invoice line back to its underlying `usage_records` rows, never a materialized id list. **invoice correction** — an append-only, signed-delta document referencing an original invoice, the invoice-layer analog of `record_correction`'s row-layer pattern; never mutates the original.

Status: FROZEN @ v1 — approved by Tin Dang
DECIDED at freeze review (2026-07-12, Tin + orchestrator): auto-issue after 72h stabilization window CONFIRMED (Tin). INVOICES_READ excluded from OPERATOR (rec confirmed). pricing_unit/cost_basis folded into one line per grouping key (rec confirmed). PRE-FREEZE AMENDMENT (orchestrator, cross-contract seam check): scalar `tag` grouping replaced by canonical tag-SET (JSONB map) grouping to match cost-attribution-tags' frozen `tags` shape — money lines partition, never double-count (see M2).
Least-sure flag surfaced at freeze: [spec/contract] Auto-issue after a 72-hour stabilization window (no manual "issue" admin action in v1) — the ONE genuinely novel lifecycle decision in this draft, with zero existing precedent in this codebase to anchor it to. Tin: confirm the 72h window, or direct a manual-issue action instead (a real contract change, cheaper to decide now than after BUILD).
Reported: no — pending Tin's batch freeze review across all four wave-1 monetization-core contracts (per dispatch note: "Tin freezes ALL wave-1 contracts at ONE batch review").
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

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
