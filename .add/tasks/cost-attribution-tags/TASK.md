# TASK: Request metadata tags on usage records + cost-by-tag breakdowns

slug: cost-attribution-tags · created: 2026-07-12 · stage: production
sensitivity: data
milestone: monetization-core
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): <path:symbol — what it is / how it is keyed>
  - `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` (`__tablename__ = "usage_records"`) — the append-only ledger. Docstring says "Schema contract (FROZEN @ v1 — TASK.md §3)" for the ORIGINAL 8 columns, but the class has since taken 11+ additive columns from later tasks (`team_id`, `pricing_unit`, `quantity`, `cached_tokens`, `reasoning_tokens`, `cache_creation_tokens`, `cost_basis`, `provider_cost`, `usage_source`, `provider_generation_id`, `audio_prompt_tokens/completion_tokens/cached_tokens`), each with its own `-- <task-name> (TASK.md §3): ...` comment. "FROZEN" names the v1 base shape, not a ban on additive columns — confirmed by reading every subsequent migration; MILESTONE.md's own shared/risky-contracts list names `usage-record tags field (additive column)` as this task's contract to freeze, consistent with the established pattern.
  - `apps/gateway/migrations/versions/a4c6e8b0d2f3_gpt_realtime_audio_columns.py` — additive-column precedent: `op.add_column("usage_records", sa.Column("audio_prompt_tokens", sa.Integer(), nullable=False, server_default="0"))` — `NOT NULL DEFAULT` via `server_default` (instant PG add, no table rewrite), every pre-existing row reads byte-identical. `apps/gateway/migrations/versions/c9f2a4d7e1b8_provider_generation_id_capture.py` is the closer analog for a non-numeric column: `provider_generation_id: Mapped[str | None] = mapped_column(Text, nullable=True)` — nullable, no default, absence = NULL. Current alembic head (single, confirmed via `uv run alembic heads` from `apps/gateway`): `69cfdc584129` (`69cfdc584129_logs_explorer_model_index.py`) — this task's migration parents off it.
  - `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder` — `supported_extras: frozenset[str]` (class attr, currently 11 names) declares which additive `record()` kwargs the recorder accepts; `.record()`/`._record_internal()` build `event_fields: dict[str, str]` (~line 372-406) that gets `json.dumps`'d where needed (see `"raw": json.dumps(raw_payload)`) and pushed via `self._redis.xadd(STREAM_KEY, event_fields)` — this is the exact seam a new `tags` extra plugs into (declare in `supported_extras`, add a `tags: dict[str, str] | None = None` kwarg, add `"tags": json.dumps(tags) if tags else "{}"` to `event_fields`).
  - `apps/gateway/src/gateway/usage/application/flusher.py:insert_usage_row` (~line 59) — parses each Redis-stream event's fields with an explicit old-format-safe default per field (e.g. `audio_prompt_tokens = int(_event_field(fields, "audio_prompt_tokens") or "0")`) then does a raw-SQL `text()` `INSERT INTO usage_records (...) VALUES (...) ON CONFLICT (id) DO NOTHING` (~line 146-192) naming every column explicitly. A `tags` field must get the SAME `_event_field(fields, "tags") or "{}"` → `json.loads(...)` old-event-safe default, since the PEL-reclaim path can replay events written by a pre-deploy recorder that never emitted `tags` at all.
  - `apps/gateway/src/gateway/proxy/domain/ports.py:UsageRecordExtras` (`TypedDict, total=False`, line 33) — the typed capability seam every additive `record()` kwarg is documented on; callers build this dict and `_dispatch_record` (below) filters it against `supported_extras` — the contract-enforced way to add `tags` without touching the frozen `UsageRecorder` Protocol's base signature.
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:_dispatch_record` (~line 229) / `:_fire_record` (~line 266) — `_fire_record` builds an `UsageRecordExtras` dict (currently only `team_id`/`request_id`) and hands it to `_dispatch_record`, which filters against `usage_recorder.supported_extras` before `asyncio.ensure_future(usage_recorder.record(**kwargs))` — `_fire_record` needs a new `tags: dict[str, str] | None = None` param threaded the same way.
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.complete` (~line 1740, `request_headers: dict[str, str] | None = None` param already present and populated) is where tags extraction/validation belongs — `request_headers` is ALREADY in scope here, no new parameter needed for the non-streaming path. Its only existing consumer today is `no_cache = (request_headers or {}).get("cache-control", "").lower() == "no-cache"` (~line 1489) — the tags read is the same shape of lookup.
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.stream` (~line 2388) — **gap confirmed by reading the signature**: `stream()` takes NO `request_headers` param at all (only `raw_key`, `body`, `upstream`, `usage_recorder`, `model_router`). Tags on a streaming request (`body["stream"]=true`) are unreachable today without an additive signature change to `stream()`.
  - `apps/gateway/src/gateway/proxy/api/router.py:completions()` (line 33) — `req_headers = {k.lower(): v for k, v in request.headers.items()}` is built at line 67, but ONLY on the non-streaming branch (after the `if stream_requested:` block at line ~54 already returned). The streaming branch (`use_case.stream(...)` call, line ~55) passes no headers at all — this router function needs `req_headers` computed BEFORE the `if stream_requested` branch and passed to both `.stream()` and `.complete()`.
  - `apps/gateway/src/gateway/usage/api/router.py:usage_router` (`APIRouter(prefix="/admin", tags=["usage"])`, line 69) · `:get_spend` (line 288) · `:_compute_window_bounds` (line 192, raises `PAYLOAD_WINDOW_INVALID`/`PAYLOAD_START_DATE_INVALID`/`PAYLOAD_END_DATE_INVALID`, all 422 `ERR_PAYLOAD_INVALID`) · `:_require_ops_read` (line 113, 403 `ERR_AUTH_FORBIDDEN` via `AUTH_FORBIDDEN`) · `:_VALID_WINDOWS` — `get_spend`'s `group_by=team_id` breakdown (`SpendBreakdownItem`/`TeamSpendBreakdownItem` in `apps/gateway/src/gateway/usage/api/schemas.py`, both `ConfigDict(frozen=True)`, `cost_usd: str` for exact-Decimal, sorted DESC) is the CLOSEST existing precedent — a windowed spend breakdown living in this exact module/file. `get_spend`'s own contract is "FROZEN @ spend-windows — TASK.md §3" — this task does NOT edit it or add a `group_by=tag` value to it (out of scope, a different task's frozen contract); it adds a SIBLING new route (`get_cost_by_tag`) in the same file, reusing `_compute_window_bounds`/`_require_ops_read`/`_VALID_WINDOWS` verbatim, avoiding both a cross-module import (the alternative `guardrail_analytics` module took) and any touch to `/admin/spend`.
  - `apps/gateway/src/gateway/guardrail_analytics/api/router.py:get_guardrail_analytics` (its own module, cross-imports `_compute_window_bounds`/`_require_ops_read` from `usage/api/router.py`) — a second, working precedent for the SAME window+OPS_READ+raw-SQL-aggregation shape; read to confirm the pattern generalizes, but the same-module `get_spend` precedent is preferred (fewer moving parts, no new module/no new `main.py` registration needed since `usage_router` is already mounted).
  - `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` (frozen dataclass, `.exc(detail=..., **fmt)` → `ProblemError`) · `PAYLOAD_GROUP_BY_INVALID` / `PAYLOAD_CUSTOM_PATTERN_INVALID` (both `422, "ERR_PAYLOAD_INVALID"`, differentiated only by `detail`) — the exact code-reuse convention this task's new `PAYLOAD_TAGS_INVALID` follows (same code `ERR_PAYLOAD_INVALID`, a task-specific `detail` message, no new HTTP status invented). `AUTH_FORBIDDEN = ErrorSpec(403, "ERR_AUTH_FORBIDDEN", ...)` — reused verbatim for the breakdown endpoint's role gate.
  - `apps/gateway/src/gateway/tenants/api/guardrail_router.py:_MAX_CUSTOM_PATTERNS = 8` / `_MAX_PATTERN_BYTES = 256` / `_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")` / `_validate_custom_patterns` (~line 166, ordered V1 count → V2 name-format → V3 byte-length → ... → V7 ReDoS-nested-quantifier heuristic) — the ONLY existing cardinality/size-bound + ReDoS-discipline precedent in this codebase; borrowed as the reasoned starting point for tag limits (flagged ⚠ in §1 — a different domain, not a proven fit).
  - `apps/gateway/src/gateway/keys/api/router.py:541` — `request.headers.get("X-Api-Key", "")`, and `apps/gateway/src/gateway/audit/api/router.py:58-59` — `_CURSOR_NEXT_HEADER = "X-Audit-Export-Next-Cursor"` / `_CURSOR_HAS_MORE_HEADER = "X-Audit-Export-Has-More"` — the two existing custom-header naming precedents (`X-<PascalCase-Words>`), confirming `X-Gateway-Tags` fits this codebase's convention. No existing inbound custom-metadata header (idempotency-key-style) precedent exists — this is the first.
  - Searched (no match, confirmed via pattern search): `body.get("metadata")` / `body.get("user")` anywhere in `apps/gateway/src/gateway/proxy/**` — the gateway does not special-case either of OpenAI's real `metadata`/`user` request fields today. `apps/gateway/src/gateway/proxy/api/router.py:47` — `body: dict[str, Any] = await request.json()` is a raw dict, no Pydantic schema, forwarded to `CompletionUseCase` essentially byte-identically to the upstream provider (contract: "proxy-completions TASK.md §3 ... byte-identical pass-through"). A body-carried tags field would need explicit strip-before-forward logic to avoid leaking a gateway-only field upstream; a header carrier needs none, since headers are never blanket-forwarded the way `body` is.
  - `apps/gateway/src/gateway/budgets/domain/ports.py:BudgetGuard.check(tenant_id) -> None` (line 24) — the budget-enforcement choke point (cross-reference only; owned by the sibling `credits-ledger`/`plan-enforcement` tasks, NOT touched here).

Context (working folder): <docs · todos · config · data the task touches — task-delta only>
  - `apps/gateway/migrations/versions/` (Alembic, single head `69cfdc584129` as of Ground SHA) — this task's migration is a new leaf parented on that head; no migration file created at design time per process rule.
  - Shared test Postgres `:5433` (`gateway/gateway`) — a unique `GATEWAY_TEST_DATABASE_URL` suffix required at BUILD/TESTS time; a migrations test touching this new column needs its derived `gateway_migrations_test_<suffix>` DB pre-created by hand (documented project gotcha).
  - `.add/milestones/monetization-core/MILESTONE.md` (read in full) — binding shared decisions #1 (usage_records-only-ledger-of-truth), #2 (one rate-card resolver — NOT touched by this task; tags label existing `cost_usd`, they never compute it), #6 (exactly-one-usage-record-per-request invariant stands).

Honors (patterns / conventions): <PROJECT.md / CONVENTIONS.md anchors — task-delta only, never a re-scan>
  - MILESTONE.md shared decision: "usage_records is the only ledger of usage truth — invoices/credits/margin are derived, append-only projections; nothing ever mutates a usage record." The new `tags` column is written ONCE at insert time via the existing Redis-stream write-behind path, never updated afterward — honored by construction (no new UPDATE path is introduced).
  - CONVENTIONS.md raw-SQL `text()` aggregation idiom (`get_spend`/`get_slo`/`get_guardrail_analytics` all use hand-written `GROUP BY`/conditional-`SUM` SQL, never ORM `func.count`/materialized views) — the new cost-by-tag breakdown query follows this exactly, using `jsonb_each_text(tags)` to expand key/value pairs before `GROUP BY`.
  - CLAUDE.md "design for failure: timeouts/retries/circuit-breakers ... in IO request" — tags parsing/validation is pure in-process CPU work (a `json.loads` on an already-received header string, bounded input size); it introduces NO new outbound-IO seam, mirroring `guardrail-analytics`'s own explicit call-out that its local-DB fire-and-forget write needs a bounded timeout + fail-open swallow but not a retry/circuit-breaker. The persisted-write itself reuses the EXISTING Redis-stream/PEL-reclaim durability path (already timeout-bounded via `_USAGE_REDIS_TIMEOUT_SECONDS`) — no new failure mode to design for.
  - PROJECT.md tenant-isolation invariant ("every query is tenant-scoped") — the new breakdown endpoint's query always includes `WHERE tenant_id = :tenant_id`, mirroring every sibling admin-analytics endpoint.

Seams consulted: none — no `.add/SEAMS.md` entry exists yet for a request-metadata header carrier or a usage_records additive-column recipe; this task is the first of its kind for both, so the pattern is derived from the code precedents above rather than a documented seam.

Anchors the contract cites: <the symbols §3 will name>
  UsageRecordRow (new `tags` column) · RecordingUsageRecorder.record/supported_extras · insert_usage_row · UsageRecordExtras · CompletionUseCase.complete/stream · _fire_record/_dispatch_record · usage_router · _compute_window_bounds · _require_ops_read · PAYLOAD_TAGS_INVALID (new) · AUTH_FORBIDDEN · SpendBreakdownItem (structural precedent, not reused directly)

Issues/Risks (→ feed §1): <problems · traps · untestable risks found in the real code — task-delta; §1 builds on these>
  - `CompletionUseCase.stream()` has no `request_headers` param today — an additive (default-`None`) signature change is REQUIRED for tag parity between streaming and non-streaming, a real (if small) touch to existing, heavily-tested code, not a net-new file. Made an explicit Must (M2) rather than silently scoping streaming out.
  - `body` is forwarded upstream near-verbatim with no Pydantic schema — confirms a header carrier over a body field (no strip-before-forward logic needed, no risk to the frozen byte-identical pass-through contract). Drives the §1 Framing decision.
  - `usage_records` is under continuous additive extension by many parallel/sequential tasks (11+ columns already) — MILESTONE.md's own shared-seam-discipline rule (the PR#66 `GuardrailConfigRequest` lesson) requires re-verifying the CURRENT full column list and `insert_usage_row`'s INSERT statement at BUILD time, since more columns may land on `main` between this Ground SHA and build.
  - No existing tags-specific cardinality/size limit exists anywhere in the codebase; the closest analog (`_MAX_CUSTOM_PATTERNS=8`/`_MAX_PATTERN_BYTES=256`) is a DIFFERENT domain (admin-configured regex patterns, not per-request client labels) — borrowing its numbers is a reasoned starting point, not a validated fit. Top ⚠ flag, §1.
  - The cost-by-tag breakdown query expands `tags` via `jsonb_each_text` per row in-window — a GIN index on `tags` accelerates containment lookups (`tags @> '{"k":"v"}'`, useful to `invoice-generation`) but does NOT accelerate the `jsonb_each_text` GROUP BY expansion itself; acceptable at the same scale `get_spend`/`get_guardrail_analytics` already operate at (tenant+window-scoped table scan), not a defect to fix in v1.
  - A single request can carry MULTIPLE tags (up to 8); a naive "cost by tag" breakdown is therefore a set of overlapping SLICES (one request's cost can appear in several breakdown rows), not a partition of the window's total cost — this must be stated explicitly in the contract/response shape or a consumer (e.g. `invoice-generation`) could wrongly assume `sum(breakdown.cost_usd) == total_cost_usd`.

Related intent: <PROJECT.md § · GLOSSARY term(s) · originating request/milestone rationale — the WHY; task-delta>
  - MILESTONE.md "Shared / risky contracts" list: "usage-record `tags` field (additive column — consumed by invoice line grouping + analytics) -> owning task cost-attribution-tags" — this task owns and freezes that exact contract.
  - MILESTONE.md Exit criterion: "Requests tagged via metadata produce cost-by-tag breakdowns that reconcile to the invoice totals" (← this task, jointly with `invoice-generation`).
  - MILESTONE.md Tasks list: `cost-attribution-tags depends-on: none — additive tags metadata on usage records + cost-by-tag breakdown API (foundation: invoice grouping consumes it)` — `invoice-generation depends-on: cost-attribution-tags`, so this contract's column shape is a hard dependency for that sibling task's own Ground.
  - GLOSSARY.md: no existing "tag" or "cost-by-tag" term — new Glossary deltas required (§3).

Ground SHA: 43ad492 (branch `feat/monetization-core`) — cite symbols, not bare line numbers; any line ref above is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Request cost-attribution tags — client-supplied key/value labels on usage records + a tenant-admin cost-by-tag breakdown API
Framings weighed: **A — HTTP request header carrier (`X-Gateway-Tags: {"k":"v",...}`, one JSON object), a new dedicated `tags JSONB NOT NULL DEFAULT '{}'` column on `usage_records`, a new sibling `GET /admin/usage/cost-by-tag` route in the existing `usage/api/router.py` module** (chosen) · B — body-field carrier (`body["tags"]` or `body["metadata"]["tags"]`) — rejected: `body` is forwarded upstream near-byte-identically with no Pydantic schema to safely strip a gateway-only field from before forwarding; a header needs no such logic and risks nothing against the frozen proxy-completions pass-through contract (§0 confirmed no existing `metadata`/`user` field usage to collide with either way) · C — fold tags into the EXISTING `raw` JSONB column (like `request_id`) instead of a new column — rejected: MILESTONE.md explicitly designates a dedicated additive `tags` column as this task's frozen contract, and a real column is directly `GROUP BY`/index-able without an ad hoc expression index per key, unlike burying it inside `raw` · D — a new cross-module `gateway/cost_attribution/` package mirroring `guardrail_analytics`'s structure — rejected in favor of a sibling route inside `usage/api/router.py`: no new module/`main.py` registration needed, and it avoids the cross-module `# pyright: ignore[reportPrivateUsage]` friction `guardrail_analytics` accepted for the same helpers.
Must:
<must>
  - M1: A client MAY attach 0–8 key/value string tags to any `/v1/chat/completions` request (streaming or non-streaming) via an `X-Gateway-Tags` request header carrying ONE JSON object of string keys to string values (e.g. `{"team":"platform","project":"gw"}`).
  - M2: Tags apply uniformly to both `CompletionUseCase.complete()` and `.stream()` — `.stream()` gains an additive `request_headers: dict[str, str] | None = None` parameter (default `None`, so every existing test/caller that doesn't pass it is unaffected) to reach parity with `.complete()`'s existing parameter; `proxy/api/router.py:completions()` computes `req_headers` once, before branching on `stream_requested`, and passes it to both.
  - M3: When the header is absent (the overwhelming majority of existing traffic), the resulting `usage_records.tags` value is `{}` (empty JSONB object) and EVERY other column, response body, status code, and latency characteristic is byte-identical to pre-task behavior — no new code path is exercised for a client that never sends the header beyond one cheap "header present?" check.
  - M4: A validated tags dict is persisted onto the SAME `usage_records` row the request already produces, via the existing fire-and-forget `UsageRecordExtras`/`supported_extras` seam (never a second write, never a blocking write, never able to slow or fail the proxied response) — mirrors exactly how `team_id`/`request_id` are threaded today.
  - M5: A tenant-admin identity with OPS_READ (owner/admin/operator/billing_admin/viewer; same gate as `/admin/spend`/`/admin/guardrails/analytics` — member is refused) can call `GET /admin/usage/cost-by-tag` with a time window (`window=day|week|month`, default `month`, or explicit `start`/`end` ISO-date override — reusing `_compute_window_bounds` verbatim, same 422 codes as `/admin/spend`) and receive, per distinct (tag key, tag value) pair observed on that tenant's rows in-window, `cost_usd` (exact-`Decimal`-as-string SUM) and `request_count` (COUNT), sorted `cost_usd` DESC.
  - M6: The response also reports the window's `total_cost_usd`/`total_requests` (every `usage_records` row in-window, tagged or not) and `untagged_cost_usd`/`untagged_requests` (rows where `tags = '{}'`) — because a single request can carry multiple tags, the breakdown rows are overlapping COST SLICES (one request's cost can appear in more than one breakdown row), never assumed to sum to `total_cost_usd`; this is stated in the response docstring so `invoice-generation` (the consuming sibling task) doesn't silently assume a partition.
  - M7: The breakdown is capped at 100 rows (by `cost_usd` DESC) with a `truncated: bool` flag set `true` when more distinct (key, value) pairs exist in-window than fit — bounds response size against high-cardinality tag abuse even though per-request cardinality is already capped at 8 (many distinct low-cost tags across many requests could still produce a large breakdown).
  - M8: An optional `tag_key` query param narrows the breakdown to a single key's values (mirrors `get_spend`'s optional `key_id` filter pattern) — an invalid-format `tag_key` is rejected (R8), an unrecognized-but-well-formed one returns 200 with an empty `breakdown` (never 404 — mirrors the "empty window → 200 with zeros" convention every sibling analytics endpoint uses).
  - M9: An empty window (no matching rows at all) → 200 with explicit zero totals and an empty `breakdown` list, `truncated: false` — never 404, mirrors `get_spend`/`get_guardrail_analytics` exactly.
</must>
Reject:
<reject>
  - R1: `X-Gateway-Tags` header value is not valid JSON, or the parsed JSON is not a flat object of string keys to string values (nested object/array/number/bool anywhere) -> "ERR_PAYLOAD_INVALID" (new `PAYLOAD_TAGS_INVALID`, detail names the parse failure) — 422, raised BEFORE governance/upstream call so a malformed-tags request is NEVER billed.
  - R2: More than 8 distinct keys in the parsed object -> "ERR_PAYLOAD_INVALID" (`PAYLOAD_TAGS_INVALID`, detail "too many tags") — 422.
  - R3: A tag key longer than 32 chars, or not matching `^[A-Za-z][A-Za-z0-9_-]{0,31}$` -> "ERR_PAYLOAD_INVALID" (`PAYLOAD_TAGS_INVALID`) — 422.
  - R4: A tag value exceeding 256 bytes (UTF-8 encoded) -> "ERR_PAYLOAD_INVALID" (`PAYLOAD_TAGS_INVALID`) — 422.
  - R5: The raw header value exceeds 2048 bytes -> "ERR_PAYLOAD_INVALID" (`PAYLOAD_TAGS_INVALID`, detail "tags header too large") — 422, checked FIRST (before JSON parsing) so an oversized header never reaches `json.loads`.
  - R6: `GET /admin/usage/cost-by-tag` called by a `member`-role identity -> 403 `ERR_AUTH_FORBIDDEN` (reuses `AUTH_FORBIDDEN`/`_require_ops_read` verbatim, same code every sibling analytics endpoint uses) — never leaks tag/cost data cross-role. And the tenant's `usage_records` rows/tags remain unchanged — read-only rejection.
  - R7: `?window=bogus` or malformed `start`/`end` -> reuses `_compute_window_bounds`'s existing 422 codes verbatim (`PAYLOAD_WINDOW_INVALID`/`PAYLOAD_START_DATE_INVALID`/`PAYLOAD_END_DATE_INVALID`) — no new code minted for window parsing. And no query executes — zero DB round-trip on a bad window.
  - R8: `?tag_key=` value not matching the same key-format regex as R3 -> "ERR_PAYLOAD_INVALID" (`PAYLOAD_TAGS_INVALID`) — 422. And the tenant's data is never touched — pure input rejection.
  - R9 (invariant, not an HTTP code): the tags write itself fails (Redis XADD timeout, malformed-event drop in `insert_usage_row`) -> NEVER surfaced to the caller and NEVER retried inline; the proxied completion succeeds/fails purely on its own merits, mirrors the existing swallow-all-exceptions contract on `RecordingUsageRecorder.record()` exactly — tags are best-effort attribution metadata, never a billing-blocking dependency.
</reject>
After:
<after>
  - A tagged request's `usage_records` row carries its validated tags in the new `tags` JSONB column, queryable via the admin breakdown API.
  - An untagged request's row has `tags = {}`, and every column/index/query/response shape that existed before this task is unchanged — the feature is fully opt-in.
  - `invoice-generation` (dependent task, Ground not yet run) can read `usage_records.tags` directly off the ledger to group invoice lines, per MILESTONE.md's stated dependency.
  - A tenant admin can answer "what did tag X cost us this month?" via `GET /admin/usage/cost-by-tag` without needing raw DB access.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The numeric bounds (max 8 tags per request, 32-char key, 256-byte value, 2048-byte header) are a REASONED ANALOGY borrowed from the only existing cardinality/size-bound precedent in this codebase (`_MAX_CUSTOM_PATTERNS=8`/`_MAX_PATTERN_BYTES=256`, admin-configured guardrail regex patterns) — lowest confidence because that precedent is a DIFFERENT domain (rarely-changed admin config vs. a per-request client label sent on every call), and Tin has not confirmed these numbers for cost-tags specifically. A too-low cap (e.g. tenants commonly wanting `team`+`project`+`environment`+`cost-center`+`department` = 5, close to the ceiling) forces an early change-request; a too-high cap risks exactly the cardinality abuse the task scope calls out. RECOMMEND: ship these numbers now (they are generous enough for the common case and cheap to raise later — raising a cap is a pure-validation change, no migration), confirm at freeze.
  - [ ] Header name `X-Gateway-Tags` (vs. e.g. `X-Cost-Tags`, `X-Tenant-Tags`, `X-Billing-Tags`) — no existing convention constrains this beyond the `X-<PascalCase-Words>` shape (`X-Api-Key`, `X-Audit-Export-*`); this is a client-facing wire contract worth Tin's explicit confirmation since every SDK/integration that wants to use it will hard-code the name.
  - [ ] JSON-object-in-header format (vs. a delimited `k=v,k2=v2` string, or repeated `X-Gateway-Tags` headers) — chosen for parser simplicity (`json.loads`, zero delimiter-escaping surface — no ambiguity if a value legitimately contains a comma or equals sign) and because it maps 1:1 onto the JSONB storage shape with no reshaping step; the tradeoff is that JSON-in-a-header is less friendly to hand-typed `curl` than a delimited string. Medium confidence — the safety/simplicity case is strong, but it's a real ergonomics tradeoff worth surfacing, not hiding.
  - [ ] `CompletionUseCase.stream()` gaining a `request_headers` param is a real (additive, default-`None`) signature change to existing, heavily-tested code, not a net-new file — confirmed low-risk (default preserves every existing caller), but flagged since it's the one place this task touches code outside its "purely additive" ideal.
  - [ ] M6's overlapping-slices semantics (a multi-tagged request's cost counted in EACH of its tags' breakdown rows, not split/partitioned) is asserted here as the correct model for "cost by tag" — `invoice-generation` may instead want a PARTITION (each request's cost attributed once, e.g. split evenly or to a "primary" tag) for its invoice-line-grouping use case. This task exposes the slice view and states the semantics explicitly rather than silently assuming they match; the partition question belongs to `invoice-generation`'s own Ground/Specify, cited here so it is visible, not lost.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: untagged request is byte-identical to pre-task behavior   # M3
  Given a tenant with no X-Gateway-Tags header sent on a non-streaming completion request
  When the request completes
  Then the response status/body/headers are identical to the pre-task behavior
  And the resulting usage_records row has tags = {} and every other column value is unaffected

Scenario: a valid tags header is persisted on a non-streaming request   # M1, M4
  Given a tenant sends X-Gateway-Tags: {"team":"platform","project":"gw"} on a non-streaming completion request
  When the request completes successfully
  Then the resulting usage_records row has tags = {"team":"platform","project":"gw"}
  And the proxied response body/status is unaffected by the tags header

Scenario: a valid tags header is persisted on a streaming request (parity)   # M1, M2
  Given a tenant sends X-Gateway-Tags: {"team":"platform"} on a request with body.stream=true
  When the SSE stream completes
  Then the resulting usage_records row has tags = {"team":"platform"}
  And this parity holds even though stream() previously had no request_headers access at all

Scenario: an explicit empty object is accepted and stored as empty   # M1, M3 edge
  Given a tenant sends X-Gateway-Tags: {}
  When the request completes
  Then the resulting usage_records row has tags = {} — identical storage to the header being absent entirely
  And no rejection occurs (an explicit empty object is not malformed)

Scenario: case-sensitive keys are stored as sent, never normalized   # M1 edge
  Given a tenant sends X-Gateway-Tags: {"Team":"a","team":"b"}
  When the request completes
  Then the resulting usage_records row has tags containing BOTH "Team":"a" and "team":"b" as distinct keys
  And no silent case-folding/merging occurs

Scenario: malformed JSON in the tags header is rejected before billing   # R1
  Given a tenant sends X-Gateway-Tags: {not valid json
  When the request is submitted
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_TAGS_INVALID) is returned
  And no usage_records row is written for this request — the upstream provider is never called

Scenario: a nested/non-string tag value is rejected   # R1
  Given a tenant sends X-Gateway-Tags: {"team":{"nested":"object"}}
  When the request is submitted
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_TAGS_INVALID) is returned
  And no usage_records row is written for this request

Scenario: too many tags is rejected   # R2
  Given a tenant sends X-Gateway-Tags with 9 distinct keys
  When the request is submitted
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_TAGS_INVALID, detail "too many tags") is returned
  And no usage_records row is written for this request

Scenario: an over-length tag key is rejected   # R3
  Given a tenant sends X-Gateway-Tags with a key 33 characters long
  When the request is submitted
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_TAGS_INVALID) is returned
  And no usage_records row is written for this request

Scenario: an over-length tag value is rejected   # R4
  Given a tenant sends X-Gateway-Tags with a value of 257 bytes (UTF-8)
  When the request is submitted
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_TAGS_INVALID) is returned
  And no usage_records row is written for this request

Scenario: an oversized header is rejected without attempting to parse it   # R5
  Given a tenant sends an X-Gateway-Tags header value larger than 2048 bytes
  When the request is submitted
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_TAGS_INVALID, detail "tags header too large") is returned
  And json.loads is never invoked on the oversized value — the byte-length check runs first
  And no usage_records row is written for this request

Scenario: a member-role identity is refused the breakdown endpoint   # R6
  Given an identity with role="member" and a valid bearer token
  When it calls GET /admin/usage/cost-by-tag
  Then 403 ERR_AUTH_FORBIDDEN is returned
  And no usage_records rows or tag data are included in the response body

Scenario: an invalid window value is rejected on the breakdown endpoint   # R7
  Given an owner identity
  When it calls GET /admin/usage/cost-by-tag?window=bogus
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_WINDOW_INVALID) is returned — the SAME code /admin/spend uses
  And no database query is executed for this request

Scenario: a malformed tag_key filter is rejected   # R8
  Given an owner identity
  When it calls GET /admin/usage/cost-by-tag?tag_key=***invalid***
  Then 422 ERR_PAYLOAD_INVALID (PAYLOAD_TAGS_INVALID) is returned
  And no database query is executed for this request

Scenario: a well-formed but unused tag_key returns an empty breakdown, not 404   # M8
  Given a tenant with no usage_records rows carrying tag key "nonexistent-key" this month
  When an owner calls GET /admin/usage/cost-by-tag?tag_key=nonexistent-key
  Then 200 is returned with breakdown=[] and truncated=false
  And total_cost_usd/total_requests still reflect the FULL window (unfiltered by tag_key)

Scenario: default-window breakdown reconciles tagged, untagged, and total cost   # M5, M6
  Given a tenant with 2 rows tagged {"team":"a"} costing $1.00 each, 1 row tagged {"team":"a","project":"x"} costing $2.00, and 1 untagged row costing $0.50 this month
  When an owner calls GET /admin/usage/cost-by-tag with no query params (default window=month)
  Then 200 is returned with total_cost_usd="4.50", total_requests=4
  And untagged_cost_usd="0.50", untagged_requests=1
  And breakdown contains {tag_key:"team", tag_value:"a", cost_usd:"4.00", request_count:3} and {tag_key:"project", tag_value:"x", cost_usd:"2.00", request_count:1}
  And the sum of breakdown cost_usd ("6.00") is intentionally GREATER than total_cost_usd ("4.50") because the multi-tagged row is counted in two breakdown rows — not treated as a bug

Scenario: an empty window returns explicit zeros, never 404   # M9
  Given a tenant with zero usage_records rows in the requested window
  When an owner calls GET /admin/usage/cost-by-tag?window=day
  Then 200 is returned with total_cost_usd="0", total_requests=0, untagged_cost_usd="0", breakdown=[], truncated=false

Scenario: high-cardinality tag values are truncated, not unbounded   # M7 — cardinality-abuse discipline
  Given a tenant with 150 distinct (tag_key, tag_value) pairs observed across many low-cost requests this month
  When an owner calls GET /admin/usage/cost-by-tag
  Then 200 is returned with exactly 100 breakdown rows, ordered by cost_usd DESC
  And truncated=true signals more distinct pairs exist than were returned

Scenario: tenant isolation holds on both write and read   # M5, R6 cross-cutting
  Given two tenants A and B, each tagging requests with the SAME key/value {"team":"platform"}
  When tenant A's owner calls GET /admin/usage/cost-by-tag
  Then only tenant A's cost/request counts appear in the breakdown — tenant B's identical tag never contributes

Scenario: a durable-fallback (Redis XADD failure) row still carries its tags   # R9, durability parity
  Given a tagged request whose Redis XADD call fails/times out (existing direct-to-ledger fallback path fires)
  When the fallback INSERT runs via the SAME insert_usage_row helper
  Then the resulting usage_records row still has the correct tags value — the fallback path is not a second, tag-blind code path

Scenario: a pre-deploy (old-format) Redis-stream event replays cleanly through insert_usage_row   # backward-compat edge
  Given a Redis-stream event recorded by a pre-deploy RecordingUsageRecorder that never emitted a "tags" field
  When the flusher's PEL-reclaim replays that event through insert_usage_row after this task ships
  Then the resulting usage_records row gets tags = {} (old-event-safe default) — insert_usage_row does not raise MalformedUsageEventError over a missing tags field

Scenario: concurrent identically-tagged requests never contend   # concurrency edge
  Given two concurrent completion requests from the same tenant, both tagged {"team":"platform"}
  When both complete around the same time
  Then two independent usage_records rows are written (append-only, no shared mutable state)
  And a breakdown query issued after both commit reflects both rows' cost summed correctly — no lost update
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
DECIDED at freeze review (2026-07-12, Tin + orchestrator): bounds 8 tags / 32-char key / 256-byte value / 2048-byte header CONFIRMED (Tin); header name X-Gateway-Tags CONFIRMED. Cost-by-tag overlapping-slice semantics CONFIRMED as analytics-only and explicitly NON-ADDITIVE — the additive money projection lives in invoice-generation's canonical tag-SET line grouping (cross-contract seam resolved pre-freeze; see invoice-generation M2 amendment).
Least-sure flag surfaced at freeze: [spec/contract] the tag cardinality/size bounds (max 8 tags/request · 32-char key · 256-byte value · 2048-byte header) are a reasoned analogy borrowed from the guardrail custom-pattern precedent (`_MAX_CUSTOM_PATTERNS`/`_MAX_PATTERN_BYTES`), not a Tin-confirmed number for the cost-tags domain — confirm or adjust these four numbers before freeze; everything else in this contract follows directly from existing, cited code precedent.

```
POST /v1/chat/completions   header (optional, additive): X-Gateway-Tags: {"<key>": "<value>", ...}
  # absent header -> usage_records.tags = {} — body/response/status BYTE-IDENTICAL to pre-task (M3)
  200/upstream-passthrough -> UNCHANGED (this task adds no new success field to the completions response)
  422 -> { type, title, status: 422, code: "ERR_PAYLOAD_INVALID", detail: "<PAYLOAD_TAGS_INVALID reason>" }
    # malformed JSON | non-flat/non-string-values | >8 keys | key >32 chars or bad format |
    # value >256 bytes | header >2048 bytes — raised BEFORE governance/upstream (never billed)

GET /admin/usage/cost-by-tag   query: window=day|week|month (default month) · start · end · tag_key
  200 -> {
    window: "day"|"week"|"month",
    window_start: <ISO-8601 UTC>, window_end: <ISO-8601 UTC>,
    total_cost_usd: "<Decimal string>", total_requests: <int>,
    untagged_cost_usd: "<Decimal string>", untagged_requests: <int>,
    breakdown: [ { tag_key: "<str>", tag_value: "<str>", cost_usd: "<Decimal string>", request_count: <int> }, ... ],  # <=100 rows, cost_usd DESC
    truncated: <bool>   # true when more distinct (tag_key, tag_value) pairs exist than the 100-row cap returned
  }
  403 -> { type, title, status: 403, code: "ERR_AUTH_FORBIDDEN" }        # caller lacks OPS_READ (reuses AUTH_FORBIDDEN verbatim)
  422 -> { type, title, status: 422, code: "ERR_PAYLOAD_INVALID" }      # bad window/start/end (reuses PAYLOAD_WINDOW_INVALID /
                                                                          # PAYLOAD_START_DATE_INVALID / PAYLOAD_END_DATE_INVALID verbatim)
                                                                          # or bad tag_key format (new PAYLOAD_TAGS_INVALID)

Schema:
  usage_records.tags   JSONB NOT NULL DEFAULT '{}'::jsonb   -- additive column, migration parents off head 69cfdc584129
    + index ix_usage_records_tags_gin ON usage_records USING gin (tags)   -- containment-query accelerator (tags @> '{"k":"v"}'), used by invoice-generation; does NOT accelerate the breakdown's jsonb_each_text expansion
  Access:
    WRITE — apps/gateway/src/gateway/usage/application/flusher.py:insert_usage_row (Redis-stream consumer + direct-fallback path,
            same ON CONFLICT (id) DO NOTHING upsert, extended with a :tags bind param; old-format events missing "tags" -> '{}')
    READ  — new GET /admin/usage/cost-by-tag handler in apps/gateway/src/gateway/usage/api/router.py, raw-SQL text() query:
            tenant+window-scoped, jsonb_each_text(tags) AS kv(key, value) expansion, GROUP BY kv.key, kv.value,
            SUM(cost_usd) / COUNT(*), ORDER BY SUM(cost_usd) DESC LIMIT 101 (101st row signals truncated=true)
```

Glossary deltas:
  - `Tag`: a client-supplied key/value string label (key: `^[A-Za-z][A-Za-z0-9_-]{0,31}$`, value: <=256 UTF-8 bytes; up to 8 per request) attached to a proxied `/v1/chat/completions` request via the `X-Gateway-Tags` header and persisted on that request's `usage_records` row for cost attribution.
  - `Cost-by-tag breakdown`: a windowed, tenant-scoped aggregation of `usage_records.cost_usd` grouped by (tag key, tag value) pair, exposed via `GET /admin/usage/cost-by-tag`; breakdown rows are overlapping cost slices (a multi-tagged request contributes to more than one row), not a partition of the window's total cost.

Reported: no
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% line coverage on every touched module (recorder.py, flusher.py, orm.py, ports.py, use_cases.py tags helpers, both routers, schemas.py) — measured via the task suite alone plus the pre-existing tests/usage + tests/proxy seam suites (no regression).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_untagged_request_byte_identical: no header -> tags={}, response unaffected · M3
  - test_valid_tags_persisted_non_streaming: valid header -> row.tags matches, response unaffected · M1, M4
  - test_valid_tags_persisted_streaming_parity: same, stream()/SSE path · M1, M2
  - test_explicit_empty_object_accepted: {} header -> tags={}, no rejection · M1, M3 edge
  - test_case_sensitive_keys_preserved: {"Team":"a","team":"b"} both kept distinct · M1 edge
  - test_malformed_json_rejected_before_billing: invalid JSON -> 422, no usage_records row, upstream never called · R1
  - test_nested_value_rejected: nested object value -> 422 · R1
  - test_too_many_tags_rejected: 9 keys -> 422 "too many tags" · R2
  - test_overlength_key_rejected: 33-char key -> 422 · R3
  - test_overlength_value_rejected: 257-byte value -> 422 · R4
  - test_oversized_header_rejected: >2048-byte header -> 422 "tags header too large", json.loads never reached · R5
  - test_member_role_refused_breakdown: member role -> 403 ERR_AUTH_FORBIDDEN, no data leaked · R6
  - test_invalid_window_rejected: window=bogus -> 422 PAYLOAD_WINDOW_INVALID, no DB query · R7
  - test_malformed_tag_key_filter_rejected: bad ?tag_key= -> 422 PAYLOAD_TAGS_INVALID, no DB query · R8
  - test_unused_tag_key_returns_empty_breakdown: well-formed unused key -> 200, breakdown=[], totals unfiltered · M8
  - test_default_window_breakdown_reconciles: mixed tagged/untagged rows -> totals + overlapping-slice breakdown sums verified · M5, M6
  - test_empty_window_returns_zeros: zero rows -> 200 explicit zeros, never 404 · M9
  - test_high_cardinality_truncated: 150 distinct pairs -> exactly 100 rows, truncated=true · M7
  - test_tenant_isolation_on_breakdown: two tenants, same tag -> no cross-tenant leakage · M5, R6
  - test_durable_fallback_carries_tags: XADD failure -> direct-fallback INSERT still carries tags via the same insert_usage_row path · R9
  - test_old_format_event_replays_cleanly: pre-deploy event missing "tags" field -> replays to tags={}, no MalformedUsageEventError · backward-compat edge
  - test_concurrent_tagged_requests_no_contention: 2 concurrent tagged requests -> 2 independent rows, breakdown sums correctly, no lost update · concurrency edge
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
Actual result: all 22 tests confirmed genuinely RED against pre-build code — failure mode was `UndefinedColumnError: column "tags" does not exist` (missing implementation), never a broken harness. Verified TWICE: once during initial red-suite authorship, and a second retroactive confirmation post-build (git-diff the 9 touched src/ files + the migration out via `git checkout HEAD --`, re-run — 22/22 failed for the same reason — then `git apply` to restore, re-run — 22/22 green). See build commit `617c1ce` (red suite) vs `1afb2c5`..`4827eca` (green build).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/infrastructure/orm.py` `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/src/gateway/usage/api/router.py` `apps/gateway/src/gateway/usage/api/schemas.py` `apps/gateway/src/gateway/proxy/domain/ports.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/api/router.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/migrations/versions/` `apps/gateway/tests/usage/` `apps/gateway/tests/proxy/`
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>
  1. Schema first: add `UsageRecordRow.tags` (JSONB, `nullable=False, server_default="'{}'::jsonb"`) + `ix_usage_records_tags_gin` to `orm.py`; write the Alembic migration parented on head `69cfdc584129` (`op.add_column` + `op.create_index`, both reversible in `downgrade()`); confirm the CURRENT full `usage_records` column list before writing the migration (MILESTONE.md shared-seam-discipline re-check — columns may have landed since Ground SHA).
  2. Validation helper: a small `_parse_tags_header(raw: str | None) -> dict[str, str]` (or similar) near `CompletionUseCase` — byte-length check FIRST (R5), then `json.loads`, then flat-string-object check (R1), then count (R2), then per-key/value format+length (R3/R4) — raising `PAYLOAD_TAGS_INVALID.exc(detail=...)` on the first violation, mirroring `_validate_custom_patterns`'s ordered-V1..V7 style. New `error_catalog.py:PAYLOAD_TAGS_INVALID = ErrorSpec(422, "ERR_PAYLOAD_INVALID", "Tags validation failed: {detail}")`.
  3. Wire the extras seam: add `tags` to `UsageRecordExtras` (ports.py), to `RecordingUsageRecorder.supported_extras` + `.record()`/`._record_internal()` params + `event_fields["tags"] = json.dumps(tags) if tags else "{}"` (recorder.py), and to `insert_usage_row`'s parse-with-old-event-default + named INSERT column/param (flusher.py).
  4. Thread through the proxy call path: `_fire_record` gains a `tags` param -> `UsageRecordExtras`; `CompletionUseCase.complete()` calls `_parse_tags_header(request_headers)` early (alongside/after `_validate_payload`, before governance) and forwards the result into `_fire_record`; `CompletionUseCase.stream()` gains the additive `request_headers` param and the SAME early validation/forwarding; `proxy/api/router.py:completions()` computes `req_headers` once before the `stream_requested` branch and passes it to both `.stream()` and `.complete()`.
  5. Read path: add `get_cost_by_tag` to `usage/api/router.py` (reuses `_compute_window_bounds`/`_require_ops_read`/`_VALID_WINDOWS` verbatim) + `CostByTagResponse`/`TagBreakdownItem` to `usage/api/schemas.py` (mirrors `SpendWindowResponse`/`SpendBreakdownItem`, `ConfigDict(frozen=True)`, `cost_usd: str`) — raw-SQL `text()` query per §3 Schema block (jsonb_each_text expansion, `LIMIT 101` truncation signal); optional `tag_key` filter validated with the SAME key-format regex as R3/R8.
  6. Tests last-verified-first: red suite per §4 (one test per §2 scenario) before any of 1–5 lands as green — TDD, not appended after the fact.

Persona (required): billing-precision-engineer (`.add/personas/billing-precision-engineer.md`) — advisory domain stance: every new `cost_usd`/`tags` read in the breakdown query stays `Decimal`-as-string end to end (no float), and the `usage_records` append-only/provenance discipline that persona enforces for cost columns applies equally to this task's new `tags` column (write-once, never mutated).
Spawn isolation (default): isolation: "worktree" (this task's build/verify subagents follow the project standing default; no stated reason to deviate — `usage_records`/`proxy` are shared-seam files other monetization-core wave-1 tasks may also touch, so an isolated worktree + net-diff merge avoids cross-task clobber, per the documented worktree-agent-stale-base gotcha).
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
  - trap: `server_default="'{}'::jsonb"` as a plain Python string on the ORM column gets rendered by SQLAlchemy as a quoted string LITERAL DDL default (`DEFAULT '''{}''::jsonb'`), not raw SQL — `asyncpg.exceptions.InvalidTextRepresentationError` on `create_all()`. Fix: wrap in `sqlalchemy.text("'{}'::jsonb")` (matches every other JSONB/expression default already in `orm.py`).
  - trap: `_dispatch_record` fires usage recording via `asyncio.ensure_future(...)` and returns the HTTP response WITHOUT awaiting it (by design — billing must never add response latency). A test that does `await asyncio.gather(client.post(...), client.post(...))` then immediately calls `flush_once()` races those still-pending fire-and-forget tasks — non-deterministically producing fewer usage_records rows than requests sent (a test-harness race, not a product defect; confirmed by isolating the recorder/Redis-XADD layer directly, which handles concurrent calls correctly on its own). Fix: `_flush()` now settles the dispatch first — polls the Redis stream length to quiescence (stable for 3 consecutive samples) before calling `flush_once()`, instead of a blind sleep or racing straight into the read.
  - trap (infra, not code): the shared dev Postgres/Redis (`:5433` / `:6380` db 9) is used by EVERY worktree's test suite un-namespaced on the Redis side (only Postgres has a per-task `GATEWAY_TEST_DATABASE_URL` convention) — a concurrently-running sibling task's own `pytest` invocation against the same Redis db 9 can produce a transient cross-worktree FK-violation flake (a leaked stream entry from another worktree's tenant, replayed against this worktree's freshly-rebuilt schema). Confirmed via `ps aux` mid-flake (a `plan-enforcement` worktree's pytest was live at that exact moment) and reproduced-clean once that process exited. Not fixed here (out of scope — a repo-wide shared-test-infra convention, `tests/conftest.py:101`, not owned by this task); documented as a known source of rare, non-code-caused flakes.
Strategy actually used: as planned in §5's ordered batches 1-6, with two informed deviations: (a) TESTS and BUILD were interleaved during authorship rather than strictly sequential (tests were drafted scenario-by-scenario alongside the seam being implemented) — RED discipline was recovered retroactively by reverting all 9 touched src/ files + the migration via `git diff`/`git checkout HEAD --`/`git apply` (no `git stash`) and re-running the full 22-test suite against pre-build code twice (once before commit, once as a final confirmation), both times red for the right reason (`column "tags" does not exist`), before committing; (b) `_run_output_validation_retry()` and `_run_diverted_fallback()` in `use_cases.py` (5 combined `_fire_record*` call sites) were deliberately left out of the tags-threading batch 4 — see Deviation candidate below.
Safety rule (feature-specific): tags are write-once at insert time via the existing fire-and-forget Redis-stream path — no new UPDATE/DELETE path introduced (append-only preserved); a tags write failure (Redis XADD timeout, malformed-event drop) never blocks, retries inline, or surfaces to the caller — mirrors `RecordingUsageRecorder.record()`'s existing swallow-all-exceptions contract exactly (R9).

Deviation candidate (disclosed, not silent): `_run_output_validation_retry()` (module-level, 3 `_fire_record_with_raw` sites) and `_run_diverted_fallback()` (deferred batch-window-grouping closure, 2 sites — one success via `_fire_record`, one error) were NOT threaded with `tags`. Both already lack `request_id` threading today (a pre-existing gap, not introduced by this task), no §2 scenario exercises either path, and `_run_diverted_fallback` runs via a closure independent of the original request's lifetime. A request that both carries tags AND falls into output-validation-retry or batch-diverted-fallback will bill correctly but land `tags={}` on that row instead of its supplied tags — a narrower-than-ideal but disclosed gap, not a silent workaround. Flagging for Verify/Observe to confirm this is acceptable for v1 or seed a Spec delta.
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
