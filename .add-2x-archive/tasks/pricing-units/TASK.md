# TASK: Per-unit pricing model (pricing_unit discriminator + quantity) with recorder dispatch

slug: pricing-units · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-unit pricing model — pricing_unit discriminator (per_token | per_image | per_second | per_character) on pricing_snapshots and usage_records; recorder dispatch on unit; single-bill invariant preserved; v6 per_token billing byte-identical.

Framings weighed:
  - **Additive two-column schema (chosen)**: keep `prompt_usd_per_token` + `completion_usd_per_token` for per_token, add a single generic `unit_usd_per_unit Numeric(20,10)` column used only for non-token units (per_image / per_second / per_character). pricing_unit column added to both pricing_snapshots and usage_records. Existing rows are untouched by the data migration (backfill pricing_unit='per_token', unit_usd_per_unit=NULL for old rows). The per_token cost formula is byte-identical to v6. Non-token cost = quantity × unit_usd_per_unit × (1 + markup). Clean discriminated dispatch; no loss of precision on any unit type.
  - **Single generalized unit-priced column only (rejected)**: replace prompt/completion columns with a single `unit_usd_per_unit`. Breaking for v6 per_token rows; violates additive-only migration rule; forces embeddings (which DO have prompt/completion distinction) to collapse two prices into one, losing information. Cost if wrong: full migration rewrite + every per_token billing path broken.
  - **Per-unit-type dedicated columns (rejected)**: one column per unit type (image_usd_per_image, second_usd_per_second, etc.). Overfits to the four current units; adding a fifth non-token unit requires another migration. No benefit over the generic `unit_usd_per_unit` for the dispatch logic.

Must:
<must>
  - `pricing_snapshots` MUST gain a `pricing_unit` TEXT column NOT NULL DEFAULT 'per_token' and a `unit_usd_per_unit` NUMERIC(20,10) NULL column. All existing rows backfill pricing_unit='per_token'; unit_usd_per_unit remains NULL on existing per_token rows.
  - `usage_records` MUST gain a `pricing_unit` TEXT column NOT NULL DEFAULT 'per_token' and a `quantity` NUMERIC(18,6) NULL column. All existing rows backfill pricing_unit='per_token'; quantity remains NULL on existing rows.
  - The pricing_unit discriminator enum MUST be treated as an open text field in the DB (not a PG enum type) but application code MUST only produce and accept the four values: `per_token` | `per_image` | `per_second` | `per_character`. Unknown values in existing rows default to per_token at the recorder.
  - The recorder's `_fetch_latest_pricing` MUST be extended to return (snapshot_id, prompt_price, completion_price, pricing_unit, unit_price) — unit_price is the new `unit_usd_per_unit` column (may be NULL for per_token rows, safe since per_token never reads it).
  - `RecordingUsageRecorder._record_internal` MUST dispatch cost computation by pricing_unit:
      - per_token:     cost = (prompt_tokens × prompt_price + completion_tokens × completion_price) × (1 + markup/100)  [v6 path — UNCHANGED; byte-identical]
      - per_image:     cost = image_count × unit_usd_per_unit × (1 + markup/100)
      - per_second:    cost = seconds × unit_usd_per_unit × (1 + markup/100)
      - per_character: cost = char_count × unit_usd_per_unit × (1 + markup/100)
    Dispatch is on the explicit pricing_unit TEXT value. NO inspect.signature or hasattr dispatch (v4 typed-extras rule).
  - `UsageRecordExtras` (proxy/domain/ports.py) MUST gain two new fields: `pricing_unit: str` and `quantity: Decimal` (total=False, so both are optional). This is the smallest typed set that the recorder dispatch needs — a single (pricing_unit, quantity) pair is cleanest (avoids per-modality proliferation).
  - `RecordingUsageRecorder.supported_extras` MUST be extended to include `"pricing_unit"` and `"quantity"`.
  - For per_token requests (chat / embeddings), the caller passes NO new extras: token counts come from the `usage` dict as today. The recorder defaults to pricing_unit='per_token' when neither the snapshot nor extras carry a different unit.
  - For non-token requests (images / audio), the caller passes `pricing_unit` + `quantity` via UsageRecordExtras. The recorder reads quantity from extras; prompt_tokens and completion_tokens are 0 (server_default).
  - `usage_records` row MUST store pricing_unit and quantity so the ledger is self-describing without joining to pricing_snapshots.
  - The SINGLE-BILL invariant MUST be preserved: `_fire_record_with_raw` remains the single write call site; this task changes the cost computation and the schema, not the invocation count. One ledger row per request regardless of unit.
  - Budget/spend counters: cost_usd flows into the SAME three INCRBYFLOAT keys (tenant/key/team) unchanged. No new counter.
  - Backward compat (default per_token): a pricing_snapshots row with pricing_unit=NULL or unknown defaults to per_token. A usage event with no pricing_unit extra (i.e. chat/embeddings) defaults to per_token.
  - All arithmetic MUST use Python `Decimal` throughout (no float intermediate). Rounding: no intermediate rounding; final Decimal result stored as-is.
  - A per_token record with cached=True MUST still produce cost_usd=0 (v6 behavior preserved).
  - A non-token record with quantity=0 MUST produce cost_usd=0 (no negative cost possible).
  - Migration: additive only. downgrade() documents the exact reverse DDL (DROP COLUMN / strip DEFAULT). No data is lost on downgrade of pricing_unit (the column is new). Per ADD CONVENTIONS.
</must>

Reject:
<reject>
  - pricing_unit value not in {per_token, per_image, per_second, per_character} in application code → treat as per_token (backward-compat fallback; no error raised — open text field for future extensibility)
  - A non-token request without pricing_unit extra but with quantity extra → pricing_unit defaults to per_token; quantity is ignored (log a WARNING; do not fail)
  - inspect.signature / hasattr dispatch in the recorder for the pricing_unit branch → "TYPED_EXTRAS_NO_DISPATCH" (foundation rule, v4)
  - unit_usd_per_unit NULL on a non-token pricing snapshot row (a bug in catalog seed data) → cost_usd=0, pricing_snapshot_id still recorded; log WARNING "unit_price_missing_for_non_token_unit" — never raise into the proxy path (recorder must-not-raise rule)
  - quantity negative → clamp to 0 (cost_usd=0); log WARNING "negative_quantity_clamped"
</reject>

After:
<after>
  - A per_token completion request: one usage_records row with pricing_unit='per_token', prompt_tokens/completion_tokens populated, quantity=NULL, cost_usd computed by the v6 two-term formula (byte-identical). pricing_snapshots row carries pricing_unit='per_token', unit_usd_per_unit=NULL.
  - A per_image generation request (e.g. 2 images at $0.04/image, markup=20%): one usage_records row with pricing_unit='per_image', quantity=2, prompt_tokens=0, completion_tokens=0, cost_usd = 2 × 0.04 × 1.20 = 0.096.
  - A per_second STT request (12.5 seconds at $0.006/second, no markup): one row with pricing_unit='per_second', quantity=12.5, cost_usd = 12.5 × 0.006 = 0.075.
  - A per_character TTS request (480 chars at $0.000015/char, markup=10%): one row with pricing_unit='per_character', quantity=480, cost_usd = 480 × 0.000015 × 1.10 = 0.00792.
  - Three spend counters (tenant/key/team) incremented by cost_usd regardless of pricing_unit.
  - All existing per_token rows and chat completion paths: byte-identical behavior to v6.
  - UsageRecordExtras TypedDict has `pricing_unit: str` and `quantity: Decimal` fields; supported_extras frozenset on the recorder includes both.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [contract]: Single `quantity` field covers all non-token units — the chosen shape (pricing_unit + quantity as a single generic pair) is the simplest but conflates audio_seconds (float) and char_count (int) and image_count (int) into one `Decimal` field. This works cleanly for the recorder dispatch but requires the endpoint tasks (images/audio) to agree on a single Decimal-typed carrier field. If an endpoint task discovers it needs TWO quantity fields (e.g. both duration AND character count for a TTS-with-transcript response), the seam must be revisited. Cost if wrong: UsageRecordExtras + supported_extras + recorder dispatch must be revised, but the schema change (a single quantity column) stays valid. Tagged [contract] because three endpoint tasks inherit this shape.
  ⚠ SECOND-LOWEST CONFIDENCE [contract]: `quantity NUMERIC(18,6) NULL` precision — 18 digits, 6 decimal places. Per_second audio may have sub-second precision (e.g. 12.5s); per_character is always integer; per_image is always integer. NUMERIC(18,6) covers all cases with room. However if audio duration is measured in milliseconds later, 6 decimal places covers ms (0.001) with 3 spare digits. Risk: if a future unit needs sub-millisecond precision, scale-6 is insufficient. Cost: a further migration to increase scale. Accepted for now.
  - [x] The `pricing_unit` column on pricing_snapshots uses TEXT NOT NULL DEFAULT 'per_token' (not a PG ENUM) so adding new unit types in future migrations is a pure-additive data change. Confirmed correct choice — PG ENUMs require ALTER TYPE to add values, which is DDL-locked and multi-step. Text avoids this.
  - [x] Embeddings use per_token pricing (prompt tokens from the response's usage.prompt_tokens field; completion_tokens=0). The embeddings-endpoint task will confirm this — assumed here and pinned as a per_token path with no new extras.
  - [x] `_fire_record_with_raw` receives `pricing_unit` and `quantity` through the UsageRecordExtras path (same as team_id, guardrail flags). The endpoint tasks pass these via extras. Chat/embedding callers pass nothing new — defaults apply.
  - [x] The recorder's `record()` public signature stays unchanged (frozen v1 Protocol). Only the implementation `_record_internal` and `supported_extras` expand. The UsageRecorder Protocol in ports.py is NOT modified.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: PU1 — per_token snapshot: cost equals v6 two-term formula (byte-identical pin)
  Given a pricing_snapshots row with pricing_unit='per_token', prompt_usd_per_token=0.0000025, completion_usd_per_token=0.00001, unit_usd_per_unit=NULL
  And a tenants row with markup_pct=20
  And a usage dict with prompt_tokens=100, completion_tokens=50
  When RecordingUsageRecorder.record() is called with no pricing_unit or quantity extras
  Then cost_usd = (100×0.0000025 + 50×0.00001) × (1+20/100) = 0.00090000 (Decimal exact)
  And the Redis Stream event carries cost_usd=0.00090000
  And prompt_tokens=100, completion_tokens=50

Scenario: PU2 — per_image snapshot + quantity=2 via extras → cost = 2×unit_price×(1+markup); tokens=0
  Given a pricing_snapshots row with pricing_unit='per_image', unit_usd_per_unit=0.04
  And a tenants row with markup_pct=20
  When RecordingUsageRecorder.record() is called with extras: pricing_unit='per_image', quantity=Decimal("2")
  Then cost_usd = 2 × 0.04 × 1.20 = 0.09600000 (Decimal exact)
  And prompt_tokens=0 and completion_tokens=0 in the event fields
  And the event field pricing_unit='per_image' and quantity='2'

Scenario: PU3 — per_second snapshot + quantity=12.5 → cost = 12.5×unit_price×(1+markup)
  Given a pricing_snapshots row with pricing_unit='per_second', unit_usd_per_unit=0.006
  And markup_pct=0
  When record() is called with extras: pricing_unit='per_second', quantity=Decimal("12.5")
  Then cost_usd = 12.5 × 0.006 × 1.00 = 0.07500000 (Decimal exact)
  And prompt_tokens=0, completion_tokens=0

Scenario: PU4 — per_character snapshot + quantity=480 → cost = 480×unit_price×(1+markup)
  Given a pricing_snapshots row with pricing_unit='per_character', unit_usd_per_unit=0.000015
  And markup_pct=10
  When record() is called with extras: pricing_unit='per_character', quantity=Decimal("480")
  Then cost_usd = 480 × 0.000015 × 1.10 = 0.00792000 (Decimal exact)
  And prompt_tokens=0, completion_tokens=0

Scenario: PU5 — markup applied for every pricing_unit (e.g. markup=20 → ×1.2)
  Given pricing_snapshots rows for per_image (unit=0.04) and per_second (unit=0.006) and per_character (unit=0.00001)
  And markup_pct=20 for the tenant
  When record() is called once for each unit with quantity=1
  Then each cost_usd equals unit_usd_per_unit × 1.2 (markup always applied uniformly)

Scenario: PU6 — usage_records row carries pricing_unit and quantity columns
  Given a per_image request recorded with pricing_unit='per_image', quantity=3
  When the Redis Stream event is flushed to the DB via UsageLedgerFlusher
  Then the usage_records row has pricing_unit='per_image' and quantity=3
  And prompt_tokens=0, completion_tokens=0

Scenario: PU7 — pricing_snapshots table carries pricing_unit column; existing rows have pricing_unit='per_token'
  Given the schema after the v7 pricing-units migration has been applied
  When the existing pricing_snapshots row (created before this migration) is read
  Then it has pricing_unit='per_token' (backfill default)
  And unit_usd_per_unit IS NULL for the pre-existing row

Scenario: PU8 — UsageRecordExtras has typed pricing_unit + quantity; supported_extras includes them; unknown recorder ignores them
  Given UsageRecordExtras TypedDict defined in proxy/domain/ports.py
  And RecordingUsageRecorder.supported_extras frozenset
  When extras = {"pricing_unit": "per_image", "quantity": Decimal("2"), "unknown_key": True}
  Then UsageRecordExtras contains "pricing_unit" and "quantity" as declared fields
  And supported_extras contains "pricing_unit" and "quantity"
  And the _dispatch_record filter passes only keys in supported_extras to the recorder
  And "unknown_key" is NOT passed (typed-seam filtering pin)
  And a v1-Protocol fake recorder (no supported_extras attribute) receives only base kwargs

Scenario: PU9 — token request with NO pricing_unit extra bills exactly as v6 (default per_token)
  Given a pricing_snapshots row with pricing_unit='per_token', prompt=0.0000025, completion=0.00001
  And markup_pct=20; usage dict with prompt_tokens=100, completion_tokens=50
  When record() is called with NO pricing_unit or quantity extras (chat-path behavior)
  Then cost_usd = 0.00090000 (identical to PU1; proves the default path is byte-identical to v6)
  And no new columns are mandatory on the call site for the chat path

Scenario: PU10 — single-bill: one usage_records row per non-token request (recorder called once)
  Given a mock recorder with a call counter
  When _fire_record_with_raw is invoked once for a per_image request
  Then the recorder.record() coroutine is scheduled exactly once
  And a single ledger row exists for this request after flush
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT

  ⚠ [contract] Single `quantity` field covers all non-token units — the (pricing_unit, quantity)
    pair is the simplest typed seam. If an endpoint task requires TWO concurrent quantity
    dimensions per request (e.g. both duration AND output characters for a future TTS-with-
    transcript endpoint), this seam must be revisited. Cost: additive extension to
    UsageRecordExtras + supported_extras + recorder dispatch; schema quantity column stays valid.
    Three endpoint tasks (embeddings, images, audio) inherit this shape — freeze it at the
    milestone level before those tasks begin. Tagged [contract] because changing this post-freeze
    requires a change request on the contract.

  ⚠ [contract] `quantity NUMERIC(18,6)` precision — sufficient for images (int), seconds (3dp),
    characters (int) at current scale. Sub-millisecond audio or very high character counts
    (>999 trillion characters) would require a precision bump. Cost: a further additive migration
    to ALTER COLUMN. Accepted as-is for v7 scope.

INTERNAL SEAM — no new HTTP endpoint (billing axis only)

SCHEMA DELTAS — pricing_snapshots (catalog/infrastructure/orm.py + migration)
  ADD COLUMN pricing_unit  TEXT  NOT NULL DEFAULT 'per_token'
    — discriminator; one of: per_token | per_image | per_second | per_character
    — DEFAULT 'per_token' means all existing rows automatically carry per_token (no explicit backfill DML needed; the DEFAULT covers both pre-migration and future missing values)
    — Application fallback: NULL or unknown value → treated as per_token
  ADD COLUMN unit_usd_per_unit  NUMERIC(20,10)  NULL
    — the per-unit price for non-token rows; NULL for all existing per_token rows
    — precision matches existing prompt_usd_per_token / completion_usd_per_token columns
  ORM addition: PricingSnapshotRow gains:
    pricing_unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="per_token")
    unit_usd_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)

  Backfill: DEFAULT 'per_token' on the column definition is the backfill (no UPDATE DML required;
  PostgreSQL fills the DEFAULT into existing rows when NOT NULL DEFAULT is specified at ADD COLUMN
  for row reads; for strict correctness the migration ALSO issues:
    UPDATE pricing_snapshots SET pricing_unit = 'per_token' WHERE pricing_unit IS NULL
  after the ADD COLUMN so existing rows are explicitly set, not just default-served.

  downgrade(): ALTER TABLE pricing_snapshots DROP COLUMN unit_usd_per_unit;
               ALTER TABLE pricing_snapshots DROP COLUMN pricing_unit;

SCHEMA DELTAS — usage_records (usage/infrastructure/orm.py + migration)
  ADD COLUMN pricing_unit  TEXT  NOT NULL DEFAULT 'per_token'
    — mirrors pricing_snapshots discriminator; set from the resolved pricing_unit at record time
  ADD COLUMN quantity  NUMERIC(18,6)  NULL
    — the billed quantity for this request
    — for per_token: NULL (token counts live in prompt_tokens / completion_tokens)
    — for non-token: the image count | seconds | characters passed via extras
  ORM addition: UsageRecordRow gains:
    pricing_unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="per_token")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

  downgrade(): ALTER TABLE usage_records DROP COLUMN quantity;
               ALTER TABLE usage_records DROP COLUMN pricing_unit;

_fetch_latest_pricing — NEW RETURN SHAPE (FROZEN)
  Returns: tuple[uuid.UUID, Decimal, Decimal, str, Decimal | None] | None
    (snapshot_id, prompt_price, completion_price, pricing_unit, unit_usd_per_unit)
  SQL:
    SELECT id, prompt_usd_per_token, completion_usd_per_token, pricing_unit, unit_usd_per_unit
    FROM pricing_snapshots
    WHERE model_id = :model_id
    ORDER BY captured_at DESC
    LIMIT 1
  Backward-compat: for existing per_token rows, unit_usd_per_unit is NULL — the per_token
  dispatch branch never reads unit_usd_per_unit, so NULL is safe.

RECORDER DISPATCH (FROZEN)
  pricing_unit resolved: extras.get("pricing_unit") if pricing_unit extra is present;
    ELSE the snapshot's pricing_unit column value; ELSE default "per_token".
  quantity resolved: Decimal(str(extras["quantity"])) if "quantity" in extras; ELSE None.

  per_token (v6 path — byte-identical, UNCHANGED):
    prompt_tokens   = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    cost_usd = (prompt_tokens × prompt_price + completion_tokens × completion_price)
               × (1 + markup_pct / 100)
    [Exact Decimal arithmetic throughout]

  per_image / per_second / per_character (new non-token path):
    q = resolved quantity (Decimal, clamped to max(0, q) if negative — log WARNING)
    unit_price = unit_usd_per_unit from snapshot (Decimal | None)
    if unit_price is None: cost_usd = Decimal("0"); log WARNING "unit_price_missing_for_non_token_unit"
    else: cost_usd = q × unit_price × (1 + markup_pct / 100)
    prompt_tokens = 0; completion_tokens = 0

  unknown / NULL pricing_unit: treated as per_token (backward-compat).

  For per_token with cached=True: cost_usd = 0 (v6 behavior, unchanged).

EVENT FIELDS — Redis Stream (additions to existing event_fields dict)
  pricing_unit: str    — e.g. "per_token" | "per_image" | ...
  quantity: str        — str(quantity) if non-token; "" (empty string) for per_token (encodes NULL)

FLUSHER CONTRACT (usage_records INSERT — additions)
  pricing_unit: parsed from event field "pricing_unit"; default "per_token" on missing/empty
  quantity: Decimal(event["quantity"]) if event["quantity"] != "" else None

TYPED EXTRAS SEAM (FROZEN)
  UsageRecordExtras (proxy/domain/ports.py) — additive extension:
    pricing_unit: str       (total=False — optional)
    quantity: Decimal       (total=False — optional)

  RecordingUsageRecorder.supported_extras — extended:
    frozenset({"team_id", "cached", "guardrail_blocked", "blocked_by", "pii_masked",
               "pricing_unit", "quantity"})

  Callers (endpoint tasks) pass extras via _fire_record_with_raw → _dispatch_record filter.
  Chat / embeddings path: no new extras; pricing_unit defaults to per_token from snapshot.

_fire_record_with_raw — SIGNATURE EXTENSION (additive)
  Adds two optional kwargs: pricing_unit: str | None = None, quantity: Decimal | None = None
  These are forwarded into UsageRecordExtras when set:
    if pricing_unit is not None: extras["pricing_unit"] = pricing_unit
    if quantity is not None: extras["quantity"] = quantity

  Single-bill invariant: still the only ledger write call site; no new invocation paths.
  Chat call sites that do NOT pass pricing_unit/quantity: unaffected (defaults None).

SPEND COUNTERS (UNCHANGED)
  INCRBYFLOAT on three keys: usage:spend:{tenant_id}:{YYYYMM},
  usage:spend:key:{key_id}:{YYYYMM}, usage:spend:team:{team_id}:{YYYYMM}
  cost_usd flows in regardless of pricing_unit. No new counter.

PLACEMENT
  Schema: migrations/versions/<rev>_pricing_units_schema.py  (build writes this)
  ORM:    catalog/infrastructure/orm.py (PricingSnapshotRow additions)
          usage/infrastructure/orm.py (UsageRecordRow additions)
  Ports:  proxy/domain/ports.py (UsageRecordExtras additions)
  Recorder: usage/application/recorder.py (_record_internal + _fetch_latest_pricing)
  Use cases: proxy/application/use_cases.py (_fire_record_with_raw signature extension)

WIRING REGRESSION TEST
  tests/pricing_units_wiring/ (paired suite; foundation v6 rule)
  — asserts UsageRecordExtras has "pricing_unit" and "quantity" fields
  — asserts RecordingUsageRecorder.supported_extras contains both new keys
  — asserts the flusher writes pricing_unit + quantity columns to usage_records
  (separate from the unit suite in tests/pricing_units/)
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] the single (pricing_unit, quantity) typed pair
carries ALL non-token units — three endpoint tasks (embeddings/images/audio) inherit this
shape. If any endpoint later needs two concurrent quantity dimensions per request, this is a
change request back to SPECIFY; the schema `quantity` column stays valid. Orchestrator
verified against the live recorder (usage/application/recorder.py supported_extras + record(),
_fetch_latest_pricing, _fire_record_with_raw), flusher (UsageLedgerFlusher), and the
PricingSnapshotRow/UsageRecordRow ORM — every referenced symbol exists; the 10-test red suite
fails for the right reasons (TypeError on the new kwarg / missing dispatch / absent columns).
<!-- The freeze IS the one approval. Lowest-confidence flags at top (see above).
     Approved → Status: FROZEN @ vN — approved by <name>.
     Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% of recorder dispatch paths + schema shape assertions + typed-seam filtering + single-bill pin

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_pu1_per_token_cost_byte_identical_to_v6:
      arrange: FakeDB seeded with pricing_snapshot(pricing_unit='per_token', prompt=0.0000025, completion=0.00001); FakeRedis; tenant markup_pct=20; usage={prompt_tokens:100, completion_tokens:50}
      act: await recorder.record(model=..., usage=..., pricing_unit NOT passed in extras)
      assert: stream event cost_usd == Decimal("0.00090000"); prompt_tokens=100; completion_tokens=50
      RED reason: _fetch_latest_pricing does not return pricing_unit/unit_usd_per_unit; dispatch branch absent

  - test_pu2_per_image_cost_and_zero_tokens:
      arrange: pricing_snapshot(pricing_unit='per_image', unit_usd_per_unit=0.04); markup=20; extras={pricing_unit:'per_image', quantity:Decimal("2")}
      act: await recorder.record(model=..., usage=None, extras passed via supported_extras seam)
      assert: cost_usd == Decimal("0.09600000"); prompt_tokens==0; completion_tokens==0; event pricing_unit=='per_image'; quantity=='2'
      RED reason: recorder._record_internal has no per_image dispatch branch; unit_usd_per_unit not fetched

  - test_pu3_per_second_cost_fractional_quantity:
      arrange: pricing_snapshot(pricing_unit='per_second', unit_usd_per_unit=0.006); markup=0; extras={pricing_unit:'per_second', quantity:Decimal("12.5")}
      act: await recorder.record(...)
      assert: cost_usd == Decimal("0.07500000"); prompt_tokens==0; completion_tokens==0
      RED reason: per_second dispatch branch absent

  - test_pu4_per_character_cost:
      arrange: pricing_snapshot(pricing_unit='per_character', unit_usd_per_unit=0.000015); markup=10; extras={pricing_unit:'per_character', quantity:Decimal("480")}
      act: await recorder.record(...)
      assert: cost_usd == Decimal("0.00792000"); prompt_tokens==0; completion_tokens==0
      RED reason: per_character dispatch branch absent

  - test_pu5_markup_applied_uniformly_across_all_units:
      arrange: three separate pricing snapshots (per_image/per_second/per_character); markup=20; quantity=1 for each
      act: record() called once per unit
      assert: each cost_usd == unit_usd_per_unit × Decimal("1.2") exactly
      RED reason: dispatch absent for non-token units

  - test_pu6_usage_records_row_carries_pricing_unit_and_quantity:
      arrange: recorder + real test DB + flusher; per_image request recorded (pricing_unit='per_image', quantity=3)
      act: flush once via UsageLedgerFlusher
      assert: DB row has pricing_unit='per_image', quantity==Decimal("3"), prompt_tokens==0, completion_tokens==0
      RED reason: usage_records table lacks pricing_unit and quantity columns (schema migration not applied)

  - test_pu7_pricing_snapshots_has_pricing_unit_column_backfilled_per_token:
      arrange: real test DB; INSERT a pricing_snapshot row without specifying pricing_unit (pre-migration style)
      act: SELECT pricing_unit FROM pricing_snapshots WHERE id=...
      assert: pricing_unit=='per_token' (DEFAULT applied)
      AND: unit_usd_per_unit IS NULL for this row
      RED reason: pricing_snapshots table lacks pricing_unit column

  - test_pu8_typed_seam_filtering_pricing_unit_and_quantity:
      arrange: import UsageRecordExtras from proxy.domain.ports; import RecordingUsageRecorder
      act: introspect UsageRecordExtras.__annotations__; check supported_extras; build extras dict with pricing_unit + quantity + unknown_key; run _dispatch_record filter against a spy recorder
      assert: "pricing_unit" in UsageRecordExtras.__annotations__; "quantity" in UsageRecordExtras.__annotations__
              "pricing_unit" in RecordingUsageRecorder.supported_extras
              "quantity" in RecordingUsageRecorder.supported_extras
              spy recorder receives pricing_unit + quantity; does NOT receive unknown_key
              v1-Protocol fake (no supported_extras) receives only base kwargs (no extras)
      RED reason: UsageRecordExtras lacks pricing_unit/quantity fields; supported_extras not extended

  - test_pu9_default_per_token_no_extras_identical_to_v6:
      arrange: same as PU1 (per_token snapshot, markup=20, prompt=100, completion=50)
      act: record() called with NO extras at all (chat-path invocation pattern)
      assert: cost_usd == Decimal("0.00090000"); pricing_unit defaulted to per_token
      RED reason: same as PU1 (dispatch absent)
      Note: GREEN-BY-DESIGN once PU1 passes — the default per_token path is the v6 path

  - test_pu10_single_bill_one_row_per_non_token_request:
      arrange: call-counting spy recorder; _fire_record_with_raw called once with pricing_unit='per_image', quantity=Decimal("2")
      act: _fire_record_with_raw(usage_recorder=spy, ..., pricing_unit='per_image', quantity=Decimal("2"))
      assert: spy.record() was scheduled exactly once (asyncio.ensure_future count == 1)
      RED reason: _fire_record_with_raw does not accept pricing_unit/quantity kwargs yet
</test_plan>

Tests live in: `apps/gateway/tests/pricing_units/` · `apps/gateway/tests/pricing_units/conftest.py` · `apps/gateway/tests/pricing_units/test_pricing_units.py`

Expected red/green at spec phase (before BUILD):
  - PU1–PU5, PU8–PU10: RED for AttributeError / wrong return shape from _fetch_latest_pricing or missing dispatch branch or missing TypedDict fields
  - PU6: RED for DB column absent (OperationalError on pricing_unit / quantity column)
  - PU7: RED for DB column absent (OperationalError on pricing_unit column in pricing_snapshots)
  - PU9: RED for same reason as PU1 (GREEN-BY-DESIGN once dispatch lands)
  All failures are for the RIGHT reason — not skips.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the per_token branch in _record_internal MUST be byte-identical to v6 (same Decimal arithmetic, same operand order, no intermediate rounding). The non-token branch MUST clamp negative quantity to 0 and log WARNING rather than raise. unit_usd_per_unit=NULL on a non-token snapshot MUST produce cost_usd=0 with WARNING (never raise into the proxy path). The flusher must write pricing_unit + quantity from event fields; a missing event field defaults to "per_token" / NULL respectively (backward-compat with pre-v7 events in the stream).

Code lives in:
  - `apps/gateway/src/gateway/proxy/domain/ports.py` (UsageRecordExtras extension)
  - `apps/gateway/src/gateway/proxy/application/use_cases.py` (_fire_record_with_raw signature extension)
  - `apps/gateway/src/gateway/usage/application/recorder.py` (_record_internal + _fetch_latest_pricing + supported_extras)
  - `apps/gateway/src/gateway/catalog/infrastructure/orm.py` (PricingSnapshotRow additions)
  - `apps/gateway/src/gateway/usage/infrastructure/orm.py` (UsageRecordRow additions)
  - `apps/gateway/migrations/versions/<rev>_pricing_units_schema.py` (new migration, additive)
  - Flusher (usage/application/flusher.py — add pricing_unit + quantity field reads)

Constraints: do NOT change any test or the contract; allow-list packages only; do NOT touch the models table or routing; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — authoritative `make ci` from repo root EXIT=0 (lint + pyright +
      allowlist + allowlist-node + full test suite). tests/pricing_units/ 10/10; full suite
      494 passed / 0 failed / 19 deselected (e2e). pyright 0 errors; ruff check + format clean.
- [x] coverage did not decrease — `make ci` enforces --cov-fail-under=80 and passed (EXIT=0);
      TOTAL ≥ 80% with both v7 builds in.
- [~] no test or contract was altered during build — CONTRACT UNALTERED, NO test altered.
      (The 4 v7 test files were ruff-formatted earlier under the pyright-migration commit —
      whitespace only, no pricing-units assertion changed.)
- [x] the per_token branch is byte-identical to v6 (PU1 + PU9 pin both green) — same operand
      order, same Decimal coercion, no intermediate rounding; orchestrator diff-reviewed the
      branch against the v6 formula line-for-line. Observation (non-blocking): when pricing is
      present but usage is None, pricing_snapshot_id is now populated (v6 left it ""); cost_usd
      is still 0 in that path (zero tokens), so the billing invariant is unchanged and no test
      regresses (494 passed). Recorded as a watch item, arguably more correct.
- [x] non-token cost is Decimal-exact with no float intermediate — quantity × unit_usd_per_unit
      × (1 + markup/100), all Decimal; PU2/PU3/PU4/PU5 green; negative quantity clamps to 0 +
      WARNING; NULL unit_price → cost 0 + WARNING (never raises into the proxy path).
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets touched;
      _fetch_latest_pricing uses a parameterised query (:model_id); migration is additive DDL.
      Dependency change is the pyright/mypy swap (allowlist updated); no runtime dep added.
- [x] layering & dependencies follow CONVENTIONS.md — UsageRecordExtras in domain (ports);
      dispatch in application (recorder); schema in infrastructure (ORM + migration); the single
      write call site (_fire_record_with_raw) unchanged in count.
- [x] a person reviewed and approved the change — orchestrator line-reviewed the recorder
      dispatch, _fetch_latest_pricing 5-tuple, both ORM additions, the migration, and the
      use_cases/flusher edits per the delegated-auto-mode review duty.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: pricing_unit/quantity flow extras →
      _fire_record_with_raw → record() → _record_internal → event fields → flusher → usage_records
      columns; pricing_unit/unit_usd_per_unit read by _fetch_latest_pricing. Confirmed by the
      green PU1–PU10 chain (unit dispatch, DB columns PU6/PU7, typed-seam filtering PU8).
- [x] DEAD-CODE (code) — no orphaned symbol; supported_extras additions consumed by the
      _dispatch filter; quantity column written by the flusher and asserted by PU6.
- [x] SEMANTIC (prose) — §3 recorder-dispatch table, the per_token byte-identical pin, and the
      single-bill invariant read in full and confirmed against the implementation; backward-compat
      defaults (unknown/NULL pricing_unit → per_token; missing event field → per_token/NULL).

### GATE RECORD
Outcome: PASS
Auto-resolved under delegated auto mode (autonomy: conservative): authoritative `make ci` green
(EXIT=0), per_token byte-identical pinned by PU1+PU9, single-bill pinned by PU10, no security
finding, no test/contract weakened. The usage=None snapshot_id nuance is a documented non-blocking
watch item (cost invariant unchanged).
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-12

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): cost_usd per pricing_unit label; non-token rows in usage_records; spend counter growth for tenant/key/team; pricing_snapshots missing unit_usd_per_unit warnings
Spec delta for the next loop: <what production taught you>

### Competency deltas
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
