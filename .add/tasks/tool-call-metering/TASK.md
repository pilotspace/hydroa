# TASK: Per-tool-call metering: $/1k-query pricing units through the shared rate-card resolver to invoice lines

slug: tool-call-metering · created: 2026-07-14 · stage: production
milestone: agent-gateway-v1
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder.record` / `.record_with_outcome` / `._record_internal` — the ONE cost-computing/persisting path every billed unit in this codebase goes through (Decimal end-to-end, `cost_basis`/`usage_source` provenance stamps). The non-token branch (`resolved_pricing_unit != "per_token"`, ~L397-432) already computes `cost_usd = resolved_quantity * Decimal(str(unit_usd_per_unit)) * (1 + markup_pct/100)` — this is the EXACT formula tool-call metering reuses verbatim, zero new cost math.
- `recorder.py`'s two `_known_units` frozenset literals (~L333, ~L443: `{"per_token", "per_image", "per_second", "per_character"}`) — the closed dispatch set gating which `pricing_unit` strings the recorder accepts from a caller vs. silently falling back to `"per_token"`. Confirmed these are two independent literal occurrences (pre-existing duplication, not introduced by this task) — both must gain the new string additively.
- `recorder.py:_fetch_latest_pricing` (~L896-965) — `SELECT ... FROM pricing_snapshots WHERE model_id = :model_id ORDER BY captured_at DESC LIMIT 1`, keyed PURELY on the `model_id` string — no join to `models.active`. Confirms a pricing snapshot resolves regardless of the model row's active flag.
- `apps/gateway/src/gateway/usage/application/rate_card_resolver.py:resolve_markup_pct` — layered per-(tenant, model) override else tenant-flat else 0, keyed generically on any `model_id` string; THREE existing callers (recorder, cost_recovery, catalog repository) all resolve through this ONE function (module docstring: "no third-site drift"). Tool-call pricing rides this UNCHANGED — a 4th caller is never introduced; the SAME 3 callers just now also see `model_id="mcp_tool_call"` rows.
- `apps/gateway/src/gateway/tenants/infrastructure/rate_card_orm.py:TenantRateCardEntry` — `UNIQUE(tenant_id, model_id)`, explicitly **NO FK to `models.id`** (docstring: "a markup override may target a model_id absent from the catalog... deliberately NOT rejected as unknown_model"). Confirms a tenant OWNER can `PUT /admin/rate-cards/mcp_tool_call` even before/without ever reading a live tool call.
- `apps/gateway/src/gateway/tenants/api/rate_card_router.py:put_rate_card_entry` / `list_rate_card_entries` / `delete_rate_card_entry` — `PUT/GET/DELETE /admin/rate-cards/{model_id}` (tiered-rate-cards TASK.md §3, FROZEN @ v1), `Permission.RATE_CARDS_MANAGE`-gated, idempotent upsert on `(tenant_id, model_id)`. Zero code change needed: `model_id` is a free path param, never validated against the catalog.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py:ModelRow` (`active: bool` default true, `modality: Text` default "chat", no CHECK constraint on `modality` — free text) and `:PricingSnapshotRow` (`model_id: Text FK -> models.id ondelete=RESTRICT`, `pricing_unit`/`unit_usd_per_unit` columns already exist from pricing-units TASK.md §3, append-only, never UPDATE/DELETE).
- `apps/gateway/src/gateway/catalog/infrastructure/repository.py:CatalogSyncRepository._upsert_model` / `._insert_snapshot` / `.list_active_models_with_markup` — **Ground finding**: `_insert_snapshot` (~L363-378) constructs `PricingSnapshotRow(...)` WITHOUT ever passing `pricing_unit`/`unit_usd_per_unit` (both silently default to `'per_token'`/`NULL`), and `catalog/domain/entities.py:CatalogModel` (the value object every seed module / OpenRouter sync builds) has **no `pricing_unit`/`unit_usd_per_unit` field at all**. Confirmed via `grep` across the whole tree: no code path anywhere writes a non-NULL `unit_usd_per_unit` today. The `per_image`/`per_second`/`per_character` branches in `recorder.py` are therefore currently DORMANT — any model priced through them today would silently bill `$0` + a `unit_price_missing_for_non_token_unit` warning (recorder.py ~L417-426). This task will be the FIRST to actually populate a non-NULL `unit_usd_per_unit` row.
- `repository.py:list_active_models_with_markup` (~L96-186) — `.where(ModelRow.active.is_(True))` is the ONLY filter gating `GET /catalog/models`'s tenant-facing listing (`catalog/application/use_cases.py:ListModelsForTenantUseCase.execute`, `catalog/api/router.py:list_models`) — confirmed **no modality filter** anywhere in that path, so any `active=true` row of ANY modality leaks into every tenant's model list. Separately, `repository.py` ~L90's stale-deactivation sweep (`ModelRow.id.notin_(incoming_ids), ModelRow.modality == "chat"`) only ever touches `modality="chat"` rows — any other modality (embedding/audio/etc., and by the same rule a new one) is exempt from ever being auto-deactivated by a future sync.
- `apps/gateway/src/gateway/catalog/infrastructure/minimax_seed.py` (+ sibling `openai_seed.py`/`vertex_seed.py`/`bedrock_seed.py`/`gpt_realtime_seed.py`) + `main.py:913-916` (`static_models=MINIMAX_SEED_MODELS + GPT_REALTIME_SEED_MODELS + BEDROCK_SEED_MODELS + VERTEX_SEED_MODELS`) — the established "hand-authored, non-upstream-synced model with real pricing" precedent. Rejected as this task's own seeding mechanism (see §1 Framings) because `CatalogModel` cannot carry `pricing_unit`/`unit_usd_per_unit` (the Ground finding above) — extending it is a real but separate fix, out of this task's blast radius.
- `apps/gateway/src/gateway/billing/application/invoice_generator.py:InvoiceGenerator.generate_for_tenant` — `GROUP BY (UsageRecordRow.model_id, .team_id, .key_id, .tags)` (~L189-194), and the existing `model_id="seat"` sentinel row (~L297-313, seat-billing TASK.md §3) is the DIRECT precedent for a non-catalog, reinterpreted `model_id` string flowing cleanly through this UNCHANGED aggregation into its own invoice line(s).
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — `model_id: Text` (**no FK** — confirmed, unlike `pricing_snapshots.model_id`), plus the already-additive `pricing_unit`, `quantity: Numeric(18,6)`, and `tags: JSONB` (cost-attribution-tags TASK.md §3, `GIN` indexed) columns this task reuses verbatim, zero migration needed on this table.
- `apps/gateway/src/gateway/proxy/domain/ports.py:UsageRecorder` (Protocol, ~L175) / `:UsageRecordExtras` (TypedDict, ~L60-102) — `RecordingUsageRecorder.record(...)` already accepts `pricing_unit`, `quantity`, `tags` as first-class keyword args (not routed through the proxy-layer-only `_fire_record_with_raw`/`_dispatch_record` helpers in `proxy/application/use_cases.py`, which this task does NOT touch or import — those are proxy-request-specific; this task calls the injected `UsageRecorder` instance directly, mirroring the "ModelHealthGate/UsageRecorder injectable-port style" the frozen sibling contract itself names).
- `apps/gateway/migrations/versions/c9e2f4a8b1d6_pricing_units_schema.py` — the additive-DDL convention (`ALTER TABLE ... ADD COLUMN ... DEFAULT`, explicit `UPDATE` belt-and-suspenders) this task's new seed migration mirrors.
- Confirmed by `grep` (no match): `usage/application/reconciliation.py`, `usage/api/margin_router.py`, `billing/application/invoice_correction.py` contain NO hardcoded `pricing_unit`/unit-name assumptions — all three operate generically over `cost_usd`/`provider_cost` regardless of unit, so a new `pricing_unit` value is safe by construction against drift/margin/reconciliation tooling.
- **Not yet real code** (honest Ground gap): `.add/tasks/mcp-connector-passthrough/TASK.md` §3 is FROZEN @ v1, but `apps/gateway/src/gateway/mcp_connector/` does not exist in the tree yet (confirmed via `find` — zero hits) — this task's only anchor into that sibling is the FROZEN CONTRACT TEXT (`gateway.mcp_connector.domain.ports.ToolCallObserver.record(*, tenant_id: UUID, key_id: UUID, server_host: str, tool_name: str, status: Literal["success"], latency_ms: int) -> None`), not an opened symbol. MILESTONE.md's own task list states `tool-call-metering depends-on: mcp-connector-passthrough` — this task's Build cannot land its DI wiring (replacing the sibling's `NoopToolCallObserver`) until that sibling's own build lands the real module and `main.py` wiring point first.

Context (working folder):
- No `.add/SEAMS.md` entry exists for pricing-unit dispatch or tool-call metering (checked — none pre-exists).
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission.RATE_CARDS_MANAGE` — reused verbatim, zero change, via the existing `rate_card_router.py`.
- No admin/dashboard UI change is in scope (scope_hints "Out": UI beyond what the existing rate-cards admin already shows) — `GET /admin/rate-cards` already lists whatever `model_id` markup entries exist; `mcp_tool_call` simply becomes one more row a tenant OWNER can see/set there, unchanged code.

Honors (patterns / conventions):
- Billing-precision discipline (persona, PROJECT.md v27/v29/v30/v33 lineage): Decimal end-to-end, explicit `cost_basis`/`usage_source` provenance, never a silent `$0` — this task inherits ALL of it for free by delegating to `RecordingUsageRecorder.record()` rather than computing cost itself.
- MILESTONE.md shared decision "One billing path": every metered unit bills through the ONE shared rate-card resolver into `usage_records`; this task introduces ZERO new tables, ZERO new cost-computation code, and ZERO parallel ledger — the entire feature is a new `model_id` value + a DI-wired observer that forwards into the existing recorder.
- MILESTONE.md shared decision "Ingress is translation-only" / additive-column discipline seen across every prior billing task (team-attribution, cost-attribution-tags, tiered-token-billing, region-pricing, service-tiers): every touched schema element is additive, every pre-existing row/query is byte-identical.
- CLAUDE.md "design for failure": the ONLY new IO this task performs is the same fire-and-forget, swallow-and-log `RecordingUsageRecorder.record()` call every other billed request already makes — no new outbound dial, no new retry/circuit-breaker surface to design (mirrors `cost-attribution-tags`'s own explicit call-out that its write-behind path needs no NEW timeout/retry design because it reuses an already-bounded durability seam).

Seams consulted: none — no `.add/SEAMS.md` entry for pricing-unit dispatch or catalog-model seeding exists yet.

Anchors the contract cites:
- `usage/application/recorder.py: RecordingUsageRecorder.record, _known_units (both occurrences), _fetch_latest_pricing`
- `usage/application/rate_card_resolver.py: resolve_markup_pct`
- `tenants/api/rate_card_router.py: put_rate_card_entry, list_rate_card_entries, delete_rate_card_entry` (unchanged, reused)
- `catalog/infrastructure/orm.py: ModelRow, PricingSnapshotRow`
- `catalog/infrastructure/repository.py: list_active_models_with_markup` (active-flag exclusion behavior)
- `billing/application/invoice_generator.py: InvoiceGenerator.generate_for_tenant` (unchanged, reused GROUP BY)
- `proxy/domain/ports.py: UsageRecorder` (Protocol) — the injected dependency `MeteringToolCallObserver` wraps
- FROZEN (not yet real code): `gateway.mcp_connector.domain.ports.ToolCallObserver` (mcp-connector-passthrough TASK.md §3 v1)

Issues/Risks (→ feed §1):
1. **No idempotency/correlation key on the frozen `ToolCallObserver.record()` signature**: it carries `tenant_id, key_id, server_host, tool_name, status, latency_ms` — nothing that lets THIS task's code detect or dedupe a double-invocation. `_record_internal` mints a fresh `uuid.uuid4()` `event_id` (recorder.py ~L515) on every call, so two invocations for one logical tool call become TWO separate billed `usage_records` rows — a real double-bill, invisible to `reconciliation.py` (which reconciles cost-basis drift, not call-count duplication). Exactly-once is entirely owed by the upstream FROZEN contract's own guarantee (M11: "ONE fire-and-forget call... this task never writes a usage_records row itself") — this task cannot re-verify or defend against a violation of it.
2. **Catalog-listing leak risk**: seeding a `ModelRow` for the synthetic pricing dimension with `active=true` would leak it into every tenant's `GET /catalog/models` response (no modality filter exists there) — confirmed by reading `list_active_models_with_markup`'s `.where(ModelRow.active.is_(True))` and the absence of any modality check downstream. Mitigation (this task, additive, zero risk to other modalities): seed with `active=false` — `_fetch_latest_pricing`/`resolve_markup_pct` still resolve it (neither joins on `models.active`), and `modality="tool_call"` (a new value) also exempts it from the sync's stale-deactivation sweep (which only ever touches `modality="chat"` rows).
3. **`CatalogModel`/`_insert_snapshot` cannot express `pricing_unit`/`unit_usd_per_unit`** (Ground finding above) — this task must NOT attempt to seed through the normal catalog-sync `static_models` path; it needs its own narrow, direct migration-level seed (`ModelRow` + `PricingSnapshotRow` inserted once, outside `CatalogSyncRepository`). Fixing `CatalogModel` generically (so per_image/per_second models could finally be priced too) is a real, separate, out-of-scope fix for a future task/change-request — flagged here so it is not lost.
4. **The exact base $/1k-tool-call price is undecided** — MILESTONE.md/roadmap name the DIMENSION ("$/1k-query pricing units") but no figure, unlike region-pricing's 1.1x or service-tiers' +25% which were DECIDED constants at freeze review.

Related intent: MILESTONE.md `agent-gateway-v1` §Scope ("per-tool-call metering ($/1k-query pricing units → usage_records → invoice lines)") and §Shared decisions ("One billing path" — every new metered unit bills through the ONE shared rate-card resolver into `usage_records`, never a parallel ledger) and §Shared/risky contracts ("tool-call `pricing_unit` + usage_record fields -> owning task tool-call-metering"); glossary delta "tool-call pricing unit ($/1k-query metering dimension)"; depends-on `mcp-connector-passthrough` (its FROZEN §3 `ToolCallObserver` no-op Protocol hand-off seam, per the dispatching agent's brief).

Ground SHA: 9d34911

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tool-call metering — a $/1k-tool-call pricing unit that bills through the SAME shared rate-card resolver into `usage_records`, drill-downable on invoice lines by (MCP server, tool)
Framings weighed:
  **A — compositional DI-only adapter: a new `MeteringToolCallObserver` implementing the FROZEN `ToolCallObserver` Protocol, forwarding into the EXISTING `RecordingUsageRecorder.record()` against ONE synthetic catalog `model_id` ("mcp_tool_call"), reusing rate-card markup override + tags + invoice grouping verbatim; the only shared-code edit is adding `"per_tool_call"` to `recorder.py`'s two known-unit sets** (chosen — smallest blast radius, zero new tables, zero new cost math, "one billing path" honored by construction)
  · B — extend `CatalogModel`/`CatalogSyncRepository._insert_snapshot` with generic `pricing_unit`/`unit_usd_per_unit` fields so ANY catalog-synced model could carry a non-token price (rejected for v1 — this shared catalog-sync code is used by 6 provider seed modules + live OpenRouter sync; fixing the dormant `per_image`/`per_second` $0 gap it would also incidentally fix is real and valuable but belongs to its own task, not smuggled into this one's narrow scope)
  · C — a per-tool-name pricing dimension (price varies by `tool_name`, e.g. `model_id=f"mcp_tool_call:{tool_name}"`) (rejected for v1 — MILESTONE.md names ONE flat "$/1k-query" dimension; unbounded `tool_name` cardinality would explode `pricing_snapshots`/`tenant_rate_card_entries` rows; `tags` already carries `tool_name` for drill-down even under a flat price, so a future per-tool-price task can layer on later without reworking this contract)
Must:
<must>
  - M1 A new `MeteringToolCallObserver` (implements the FROZEN `ToolCallObserver` Protocol verbatim: `record(*, tenant_id, key_id, server_host, tool_name, status, latency_ms)`) is the ONLY production wiring for `ToolCallObserver` once both this task and `mcp-connector-passthrough` are built — replacing the sibling's `NoopToolCallObserver` default in `main.py`'s DI graph, constructed with the SAME `RecordingUsageRecorder` instance already wired for every other billed call (no second Redis/session instance).
  - M2 `MeteringToolCallObserver.record(...)` forwards, byte-for-byte, into `usage_recorder.record(tenant_id=tenant_id, key_id=key_id, model="mcp_tool_call", usage=None, status=200, pricing_unit="per_tool_call", quantity=Decimal("1"), tags={"mcp_server": server_host, "mcp_tool": tool_name})` — no re-derivation of tenant/key identity, no extra DB read beyond what `record()` itself already performs (pricing + markup + region + tier resolution, all pre-existing).
  - M3 `quantity` is ALWAYS the literal `Decimal("1")` per invocation — one `usage_records` row = exactly one metered tool call. The "$/1k" framing lives ENTIRELY in the catalog base price (`unit_usd_per_unit` = the DECIDED $/1k rate ÷ 1000), never in batching multiple calls into one row or in a fractional `quantity`.
  - M4 A new additive migration seeds exactly one `models` row (`id='mcp_tool_call', active=false, modality='tool_call', provider='hydroa', region='global'`) and exactly one `pricing_snapshots` row for it (`pricing_unit='per_tool_call', unit_usd_per_unit=<DECIDED $/1k price ÷ 1000>, prompt_usd_per_token=0, completion_usd_per_token=0`), idempotent (safe to run twice, e.g. guarded by `ON CONFLICT DO NOTHING` / existence check).
  - M5 `active=false` on the seeded row deliberately EXCLUDES `mcp_tool_call` from `GET /catalog/models`'s tenant-facing listing (`list_active_models_with_markup`'s `WHERE active = true` filter) while remaining fully resolvable by `_fetch_latest_pricing`/`resolve_markup_pct` (both key directly on the `model_id` string, independent of `models.active`).
  - M6 `modality='tool_call'` (a new, previously-unused discriminator value) additionally exempts this row from the catalog sync's stale-deactivation sweep (`repository.py`'s `ModelRow.modality == "chat"` filter) — never marked inactive by a future OpenRouter/MiniMax/Bedrock/Vertex sync pass.
  - M7 `usage/application/recorder.py`'s two existing `_known_units` frozenset literals (~L333, ~L443) each gain the additive string `"per_tool_call"` — the ONLY edit this task makes to shared billing-core code; every other `pricing_unit` branch (`per_token`/`per_image`/`per_second`/`per_character`) is untouched.
  - M8 A tenant OWNER can set a per-tenant markup override for tool-call pricing via the EXISTING, UNCHANGED `PUT /admin/rate-cards/mcp_tool_call` endpoint (tiered-rate-cards TASK.md §3, FROZEN @ v1) — zero new code; `resolve_markup_pct(session, tenant_id, "mcp_tool_call")` resolves through the SAME layered (per-model override else tenant-flat else 0) rule every other model already uses.
  - M9 Every metered tool call's `usage_records` row carries `tags={"mcp_server": <server_host>, "mcp_tool": <tool_name>}` via the EXISTING cost-attribution-tags column/seam — `invoice_generator.py`'s existing `GROUP BY (model_id, team_id, key_id, tags)` aggregation (UNCHANGED) therefore emits one invoice line per distinct (server, tool) pair per billing period, mirroring the `model_id="seat"` sentinel precedent, with ZERO changes to `invoice_generator.py`.
  - M10 `MeteringToolCallObserver.record()` never raises and never blocks its caller — it delegates entirely to `RecordingUsageRecorder.record()`, whose own swallow-and-log discipline (`record_with_outcome`/`_record_internal`) is inherited unchanged; a DB/Redis outage during metering degrades to the SAME "logged, swallowed" failure mode every other billed call already has.
  - M11 When the seeded `pricing_snapshots` row for `mcp_tool_call` is (transiently) missing or carries a NULL `unit_usd_per_unit` (e.g. this task's migration has not yet run in some environment), the EXISTING recorder-level guard fires UNCHANGED (`unit_price_missing_for_non_token_unit` warning log, `cost_usd=0`) — an EXPLAINED zero per the billing-precision bar, never a silent one; this task adds NO new silent-zero path.
  - M12 `key_id`/`tenant_id` on the emitted `usage_records` row are exactly the values the (already-authenticated, per `mcp-connector-passthrough` M14) caller identity resolved to — an agent-token-authenticated tool call bills identically to an sk-key-authenticated one (no branch on credential class).
</must>
Reject:
<reject>
  - R1 (no HTTP surface — this hook is an async fire-and-forget observer, never a request/response endpoint; the "code" below names a log/metric event, not a 4xx) `unit_usd_per_unit` is NULL/missing for `mcp_tool_call` when `record()` fires -> logs `"unit_price_missing_for_non_token_unit"` (pre-existing recorder code, unmodified) -> bills $0 for that single call, EXPLAINED (never silent); an operational alarm this task's §7 Observe must watch, not a code path this task newly handles.
  - R2 (same non-HTTP note) A double-invocation of `ToolCallObserver.record()` with the SAME `call_id` (CR-1, upstream contract v2) -> the metering implementation derives its `usage_records` deterministic event id FROM `call_id` (not a fresh uuid4), so the recorder's existing idempotent insert (`ON CONFLICT DO NOTHING` on the deterministic id) collapses the duplicate — billed EXACTLY ONCE. [RESOLVED at freeze by CR-1 (Tin 2026-07-14): call_id added to the upstream Protocol; the double-bill residual is closed structurally.]
</reject>
After:
<after>
  - A successful, non-refused, non-blocked MCP tool call produces exactly one `usage_records` row priced through the SAME shared rate-card resolver every other billed unit uses (Decimal, provenance-stamped, markup-applied).
  - A tenant OWNER can see and override the per-tenant markup on tool-call pricing via the SAME rate-cards admin surface used for every other model, with zero new UI/endpoint.
  - A tenant's monthly invoice carries one drill-down line per distinct (MCP server, tool) pair actually called that period, generated by the SAME unmodified `InvoiceGenerator` aggregation every other line uses.
  - `mcp_tool_call` never appears in a tenant's `GET /catalog/models` listing and is never touched by a future catalog sync's stale-deactivation sweep.
  - No second/parallel usage ledger, cost-computation function, or table exists for tool-call billing — the milestone's "one billing path" invariant holds by construction, not by review.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [RESOLVED at freeze — CR-1 (Tin 2026-07-14) added `call_id: UUID` to the upstream Protocol (mcp-connector-passthrough §3 v2); this task Must dedupe on it via a call_id-derived deterministic event id.] ORIGINAL FLAG: The FROZEN `ToolCallObserver.record()` signature (`tenant_id, key_id, server_host, tool_name, status, latency_ms`) carried no correlation/idempotency key — if the upstream `mcp-connector-passthrough` fire-and-forget call site ever double-invokes it for one tool call (bug, retry-after-timeout race, etc.), this task's code has NO way to detect or dedupe the second call: `_record_internal` mints a fresh `uuid.uuid4()` `event_id` on every invocation (recorder.py ~L515), so a double-fire is a REAL double-bill. Lowest confidence because the signature is ALREADY frozen — this is a change-request back to `mcp-connector-passthrough`'s contract, not something available to fix inside this task. If wrong (a real double-fire ships in production): the cost is a silent per-tenant over-bill, invisible to `reconciliation.py` (which reconciles cost-basis drift, not call-count duplication) — recommend Tin decide at freeze whether to (a) accept this residual risk, leaning on the sibling's own dual adversarial security verify to keep M11 (exactly-once) honest, or (b) open a change request adding a `call_id: UUID` field to the Protocol before either task builds.
  - [ ] The exact $/1k-tool-call BASE price is not specified anywhere in MILESTONE.md/the roadmap (unlike region-pricing's 1.1x or service-tiers' +25%, both DECIDED constants at their own freeze review). DECIDED at freeze (Tin 2026-07-14): **$2.50 per 1k tool calls** (`unit_usd_per_unit = Decimal("0.0025")` per call), tenant-overridable via the existing rate-card endpoint.
  - [ ] Seeding `mcp_tool_call` with `active=false` / `modality='tool_call'` correctly hides it from `GET /catalog/models` while remaining fully priceable — confirmed by READING `list_active_models_with_markup`'s `.where(ModelRow.active.is_(True))` and `_fetch_latest_pricing`'s direct `model_id` keying at Ground time (not yet proven by a live test); assert this explicitly in Tests/Build, not just at Ground.
  - [ ] This task's Build cannot land its `main.py` DI wiring (replacing `NoopToolCallObserver`) until `mcp-connector-passthrough`'s own build has landed the real `gateway/mcp_connector/` module — MILESTONE.md's stated dependency order; confirm the milestone's build sequencing accounts for this before Tests/Build starts on this task.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: A successful MCP tool call is metered exactly once   # M1, M2, M3
  Given the mcp-connector-passthrough build has wired MeteringToolCallObserver as its ToolCallObserver
  And a tenant's agent completes an allow-listed, non-blocked MCP tool call
  When ToolCallObserver.record(tenant_id=T, key_id=K, server_host="mcp.acme.example", tool_name="search", status="success", latency_ms=120) fires
  Then usage_recorder.record(...) is invoked with model="mcp_tool_call", pricing_unit="per_tool_call", quantity=Decimal("1"), tags={"mcp_server":"mcp.acme.example","mcp_tool":"search"}
  And exactly one usage_records row is written for that call

Scenario: Tool-call cost is computed via the shared rate-card resolver, Decimal end-to-end   # M2, M3
  Given the seeded pricing_snapshots row for mcp_tool_call has unit_usd_per_unit = 0.0025
  And the calling tenant has no per-model rate-card override (falls back to tenants.markup_pct = 20)
  When the usage_records row for a successful tool call is computed
  Then cost_usd = Decimal("1") * Decimal("0.0025") * (Decimal("1") + Decimal("20")/Decimal("100")) = Decimal("0.0030")
  And cost_basis = "catalog" and pricing_unit = "per_tool_call" and quantity = Decimal("1") on that row

Scenario: A tenant OWNER overrides tool-call markup via the existing rate-cards admin surface   # M8
  Given an authenticated OWNER identity
  When they PUT /admin/rate-cards/mcp_tool_call with body {markup_pct: 35}
  Then the response is 200 {model_id: "mcp_tool_call", markup_pct: "35"}
  And the next tool-call usage_records row for that tenant bills with markup_pct=35 instead of the tenant flat rate
  And no new endpoint or code path was added to serve this request

Scenario: mcp_tool_call is invisible in the tenant-facing catalog listing   # M4, M5
  Given the seed migration has run (models row id="mcp_tool_call", active=false)
  When any authenticated tenant identity calls GET /catalog/models
  Then the response's model list does NOT contain "mcp_tool_call"
  And a tool call still prices correctly (Scenario above) despite the row being inactive

Scenario: mcp_tool_call survives a catalog sync pass untouched   # M6
  Given a periodic OpenRouter/MiniMax/Bedrock/Vertex catalog sync runs
  And "mcp_tool_call" is absent from every incoming sync payload (it is never a real upstream model)
  When the sync's stale-deactivation sweep executes (WHERE modality = "chat")
  Then the mcp_tool_call row's active flag is NOT changed by the sweep
  And its pricing_snapshots row is untouched (no new snapshot inserted for it by sync)

Scenario: Invoice generation drills down per (MCP server, tool) with zero code change   # M9
  Given a tenant made 3 tool calls to server "mcp.acme.example" tool "search" and 2 calls to tool "fetch" in one billing period
  When InvoiceGenerator.generate_for_tenant runs for that period
  Then the invoice contains one line with model_id="mcp_tool_call", tags={"mcp_server":"mcp.acme.example","mcp_tool":"search"}, request_count=3
  And a second line with tags={"mcp_server":"mcp.acme.example","mcp_tool":"fetch"}, request_count=2
  And InvoiceGenerator's own aggregation SQL (GROUP BY model_id, team_id, key_id, tags) required no change to produce this

Scenario: Metering never raises or blocks on a recorder outage   # M10
  Given the Redis/DB backing RecordingUsageRecorder is unreachable
  When ToolCallObserver.record(...) fires for a successful tool call
  Then MeteringToolCallObserver.record() returns normally, swallowing the failure
  And no exception propagates to the mcp-connector-passthrough caller
  And the proxied MCP response the caller already received is unaffected

Scenario: Missing base price fails to an explained zero, never a silent one   # M11, R1
  Given the mcp_tool_call pricing_snapshots row is absent (migration not yet applied in this environment)
  When a successful tool call is metered
  Then the recorder logs "unit_price_missing_for_non_token_unit"
  And the resulting usage_records row has cost_usd = 0
  And the row still carries pricing_unit="per_tool_call", quantity=Decimal("1"), and the mcp_server/mcp_tool tags — nothing about the call itself is dropped, only its price is $0 and explained

Scenario: Agent-token-authenticated tool calls bill identically to sk-key ones   # M12
  Given a device-OAuth agent token (not an sk- API key) authenticated the tool call
  When ToolCallObserver.record(...) fires with that identity's tenant_id/key_id
  Then the resulting usage_records row is indistinguishable in shape from an sk-key-authenticated row
  And the same rate-card resolution and tags logic applies identically

Scenario: A refused or blocked tool call never reaches this task's code at all   # boundary / integration edge case
  Given an MCP tool call was refused (unlisted server) or blocked (prompt-injection match) by mcp-connector-passthrough
  When that outcome is finalized
  Then ToolCallObserver.record(...) is never invoked (per mcp-connector-passthrough's own M11/M9)
  And no usage_records row for a refused/blocked call is ever written by this task's code
  And no MeteringToolCallObserver code path executes for that request at all

Scenario: A hypothetical double-invocation double-bills (named, accepted residual risk)   # R2, concurrency edge case
  Given a defect in the upstream fire-and-forget call site invokes ToolCallObserver.record(...) twice for the SAME logical tool call
  When both invocations complete
  Then TWO usage_records rows are written (each with its own fresh event id, quantity=Decimal("1"))
  And the metering implementation collapses the duplicate via the call_id-derived deterministic event id (recorder ON CONFLICT DO NOTHING) — billed exactly once (CR-1)
  And this is documented as an accepted residual risk (§1 ⚠), not a defect in this task's own implementation

Scenario: Concurrent tool calls across two different tenants never cross-contaminate billing   # concurrency edge case
  Given tenant A and tenant B each complete one MCP tool call at the same instant
  When both ToolCallObserver.record(...) calls are in flight concurrently
  Then each resulting usage_records row carries its OWN tenant_id/key_id/rate-card resolution
  And tenant A's markup_pct override never influences tenant B's billed cost_usd, and vice versa
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP endpoint (this task adds none — reuses two FROZEN, unchanged surfaces):

PUT/GET/DELETE /admin/rate-cards/{model_id}   (tiered-rate-cards TASK.md §3, FROZEN @ v1, UNCHANGED)
  -- model_id="mcp_tool_call" is now a valid, meaningful target; zero code touched.
  200 -> { model_id: "mcp_tool_call", markup_pct: string }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  422 -> { code: <existing markup_pct validation codes, unchanged> }

gateway.tool_call_metering.infrastructure.observer.MeteringToolCallObserver (NEW class,
  implements the FROZEN gateway.mcp_connector.domain.ports.ToolCallObserver Protocol verbatim —
  mcp-connector-passthrough TASK.md §3 v1):
    async def record(self, *, tenant_id: UUID, key_id: UUID, server_host: str, tool_name: str,
                      status: Literal["success"], latency_ms: int) -> None
    -- forwards, unconditionally and byte-for-byte, into the injected UsageRecorder:
       usage_recorder.record(tenant_id=tenant_id, key_id=key_id, model="mcp_tool_call",
         usage=None, status=200, pricing_unit="per_tool_call", quantity=Decimal("1"),
         tags={"mcp_server": server_host, "mcp_tool": tool_name})
    -- never raises (delegates entirely to RecordingUsageRecorder's own swallow-and-log
       discipline); latency_ms is accepted per the frozen signature but not persisted by v1
       (no usage_records column for it — a future observability task may add one additively).

Schema:
  models                              -- ONE seeded row, migration-inserted, idempotent:
    id='mcp_tool_call', active=false, modality='tool_call', provider='hydroa', region='global'
    -- active=false excludes it from GET /catalog/models (list_active_models_with_markup's
       WHERE active=true filter); modality='tool_call' exempts it from the sync's stale-
       deactivation sweep (repository.py's WHERE modality='chat' filter). Both flags are
       load-bearing (§1 M5/M6), not incidental.
  pricing_snapshots                   -- ONE seeded row, migration-inserted, idempotent, FK to
                                          the models row above (ondelete=RESTRICT, pre-existing):
    model_id='mcp_tool_call', pricing_unit='per_tool_call',
    unit_usd_per_unit=Decimal("0.0025"),  # $2.50/1k, DECIDED by Tin at freeze 2026-07-14
    prompt_usd_per_token=0, completion_usd_per_token=0
  usage_records                       -- NO new column; existing pricing_unit/quantity/tags
                                          columns (pricing-units + cost-attribution-tags,
                                          both pre-existing) carry every tool-call-metering row:
    pricing_unit='per_tool_call', quantity=Decimal("1"),
    tags={"mcp_server": <server_host>, "mcp_tool": <tool_name>}
  usage/application/recorder.py       -- ADDITIVE code edit (not schema): both existing
                                          `_known_units` frozenset literals (~L333, ~L443) gain
                                          the string "per_tool_call"; no other line in this
                                          module's cost-computation path changes.
  tenant_rate_card_entries            -- NO schema change; model_id has no FK (confirmed §0), so
                                          "mcp_tool_call" is a valid override target as-is.

Access pattern: pricing/markup resolution reads are IDENTICAL to every other model_id — keyed
  on the caller's own resolved tenant_id (rate-card admin routes) or the tenant_id/key_id already
  authenticated by mcp-connector-passthrough's CompositeKeyAuthenticator (the metering hook itself
  performs no new authn/authz — it trusts the identity the frozen ToolCallObserver call site
  already resolved). No cross-tenant read/write surface is introduced.
```

Glossary deltas:
- **Tool-call pricing unit**: the `pricing_unit="per_tool_call"` dispatch value (recorder.py `_known_units`) billing exactly `quantity=Decimal("1")` per successfully-dialed, non-refused, non-blocked MCP tool call, priced via `unit_usd_per_unit` on the synthetic catalog row `model_id="mcp_tool_call"` — the concrete instance of MILESTONE.md's named "$/1k-query metering dimension" (the $/1k figure is expressed as the seeded per-call unit price, not as a batching/rounding mechanism).
- **mcp_tool_call (sentinel model_id)**: a first-party, Hydroa-priced (not upstream-catalog-synced) `models`/`pricing_snapshots` row, seeded once via migration, `active=false` so it never appears in a tenant's model catalog listing — the SAME "reinterpreted sentinel model_id" idiom `model_id="seat"` already establishes in `invoice_generator.py`.

**Open decisions for freeze (Tin to confirm — NOT yet decided, this contract is still DRAFT):**
- [x] DECIDED (Tin 2026-07-14): $2.50/1k confirmed.
- [x] DECIDED (Tin 2026-07-14): CR-1 opened and applied — `call_id: UUID` added to the upstream Protocol (v2); this task dedupes on a call_id-derived deterministic event id. Exactly-once is structural, no residual.

Least-sure flag surfaced at freeze: [contract] This is the FIRST real use of the dormant non-token pricing-unit branch (`unit_usd_per_unit` never populated anywhere in the tree today) — the cost math is reused verbatim, but no production precedent exercises that branch end-to-end; if a latent assumption hides there, the cost is a mis-billed tool-call line item. (The original double-bill flag was RESOLVED at freeze by CR-1 — call_id dedupe is now contract-level.)

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no
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

Scope (may touch): `apps/gateway/src/gateway/tool_call_metering/` (new module: `infrastructure/observer.py` — `MeteringToolCallObserver`) · `apps/gateway/src/gateway/usage/application/recorder.py` (additive: `"per_tool_call"` added to both existing `_known_units` frozenset literals only — no other line) · `apps/gateway/src/gateway/main.py` (DI wiring only: construct `MeteringToolCallObserver` and pass it wherever `mcp-connector-passthrough`'s build wires `ToolCallObserver`, replacing its `NoopToolCallObserver` default) · `apps/gateway/migrations/versions/` (one new additive seed migration) · `./tests/`

Strategy (ordered batches):
  1. Confirm `mcp-connector-passthrough`'s build has landed `gateway/mcp_connector/domain/ports.py:ToolCallObserver` + its `main.py` no-op wiring FIRST (MILESTONE.md dependency order, §1 assumption) — this task's DI-replacement step is a no-op until that symbol exists in the tree; do not guess its exact import path, re-Ground against the sibling's actual landed code before writing the wiring line.
  2. Seed migration first (additive-only, idempotent): insert the one `models` row (`active=false`, `modality='tool_call'`) and its one `pricing_snapshots` row (`pricing_unit='per_tool_call'`, `unit_usd_per_unit=<DECIDED figure>`) — everything else depends on this row existing; write it guarded (`ON CONFLICT DO NOTHING` on `models.id`, then re-check before inserting the snapshot) so a re-run is safe.
  3. `recorder.py`'s two `_known_units` edits — smallest possible diff, unit-tested in isolation (assert `"per_tool_call"` now resolves the non-token cost branch with a fixture `unit_usd_per_unit`) before touching anything else.
  4. `gateway/tool_call_metering/infrastructure/observer.py:MeteringToolCallObserver` — pure composition, zero new IO of its own (delegates entirely to the injected `UsageRecorder`); test it directly against a fake/spy `UsageRecorder` asserting the EXACT forwarded kwargs (model, pricing_unit, quantity, tags) before any DI wiring exists.
  5. `main.py` DI wiring last — construct `MeteringToolCallObserver(usage_recorder=<the same instance already wired>)` and thread it to wherever the sibling task's build expects a `ToolCallObserver` implementation; a live/integration-style test (not just unit) should assert one real tool call round-trip produces one `usage_records` row with the right shape.
  6. Full scenario-by-scenario red→green pass, then specifically the catalog-listing-invisibility scenario (M4/M5) and the swallow-on-outage scenario (M10) — these are the ones most likely to be only weakly asserted on a first pass (e.g. asserting a mock was called rather than that `GET /catalog/models` truly excludes the row).

Persona (required): `billing-precision-engineer` (`.add/personas/billing-precision-engineer.md`) — its Decimal-only, provenance-stamped, never-a-silent-$0 discipline is the exact bar M3/M11/R1 must clear; its reconciliation-detective stance is the right lens for verifying the seeded price actually reaches `cost_usd` correctly end to end.
Spawn isolation (default): `worktree` — isolate from any concurrent sibling-task build in this milestone (`mcp-connector-passthrough`, `agent-identity-governance`), especially since this task's `main.py` DI-wiring edit and the sibling's own `main.py` wiring edit will otherwise collide in a shared tree.
Known-problem fixes:
  - trap: seeding the `models` row with `active=true` (the ORM's own column default) would silently leak "mcp_tool_call" into every tenant's `GET /catalog/models` response → planned fix: the migration must EXPLICITLY set `active=false` (never rely on the column default).
  - trap: seeding with `modality='chat'` (the ORM's own column default) would make a future catalog sync's stale-deactivation sweep (`WHERE modality='chat'`) mark the row inactive the first time "mcp_tool_call" is absent from an OpenRouter payload (it always will be) → planned fix: the migration must EXPLICITLY set `modality='tool_call'`, never rely on the default.
  - trap: routing the observer through `proxy/application/use_cases.py`'s `_fire_record_with_raw`/`_dispatch_record` helpers (the proxy-request-layer convenience wrapper) would create an awkward cross-module import from `tool_call_metering`/`mcp_connector` into `proxy/application` → planned fix: call the injected `UsageRecorder` Protocol instance directly (already supports `pricing_unit`/`quantity`/`tags` as first-class kwargs) — no proxy-layer import needed.
  - trap: forgetting that `pricing_snapshots.model_id` (unlike `usage_records.model_id`) has a real FK to `models.id` (`ondelete=RESTRICT`) → planned fix: the seed migration MUST insert the `models` row before the `pricing_snapshots` row, in that order, in the same migration.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the `models` row insert and the `pricing_snapshots` row insert happen in the SAME migration transaction, models row first — a partial seed (models row present, pricing row absent) must never be observable, since that combination would silently produce the M11 "$0 + warning" path for every tool call until manually repaired.
Code lives in: `apps/gateway/src/gateway/tool_call_metering/`
Constraints: do NOT change any test or the contract; additive-only edits to `recorder.py` (no refactor of the pre-existing `_known_units` duplication beyond adding the new string, even though §0 notes it as a pre-existing wart — out of this task's scope to fix); allow-list packages only (no new dependency — this task needs none); ask if unclear.

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
