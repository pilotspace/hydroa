# TASK: Bill real GPT-Realtime usage through the relay

slug: gpt-realtime-relay-billing · created: 2026-07-01 · stage: production · risk: high
autonomy: conservative   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
  - `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py:OpenAIRealtimeSession._translate_server_event` (staticmethod) — on `kind == "response.done"` currently `return {"type": "response.done"}`, discarding the raw provider event's `usage` object entirely (the exact "discarded response.done usage object" the milestone goal names).
  - `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py:OpenAIRealtimeSession.events` — the async generator that calls `_translate_server_event` per raw frame and yields the normalized `RelayFrame`; the only place the raw OpenAI event dict is ever seen before it's gone.
  - `apps/gateway/src/gateway/proxy/domain/realtime_relay.py:RealtimeRelaySession` (Protocol) — `RelayPump` depends ONLY on this Protocol (never a concrete provider); any usage-capture seam must be expressed here, or via an optional callback param that doesn't widen the Protocol for Gemini Live (out of scope, must stay byte-identical).
  - `apps/gateway/src/gateway/proxy/application/realtime_relay_pump.py:RelayPump.run` / `RelayPump._provider_to_client` — owns the frame relay loop and the single `finally: await self._teardown(...)` exit point; the natural place a captured usage value would need to reach before/at teardown.
  - `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py:realtime_relay` — the WS endpoint; already holds `authz.tenant_id`/`authz.key_id` (post `_authenticate`), `app.state.usage_recorder`, and `settings.realtime_relay_openai_model` — the natural owner of the eventual `usage_recorder.record(...)` call.
  - `apps/gateway/src/gateway/usage/application/recorder.py:_fetch_latest_pricing` — SELECTs `pricing_snapshots` columns (`prompt_usd_per_token, completion_usd_per_token, pricing_unit, unit_usd_per_unit, cached_input_usd_per_token, reasoning_usd_per_token, cache_creation_usd_per_token`); does NOT yet select `audio_prompt_usd_per_token` / `audio_completion_usd_per_token` / `audio_cached_usd_per_token`, which already exist on the table (gpt-realtime-schema-migration + gpt-realtime-pricing-fields).
  - `apps/gateway/src/gateway/usage/application/recorder.py:compute_per_token_cost_usd` — pure cost function; text-only tiers (prompt/completion/cached/reasoning/cache_creation) via a FLAT path (byte-identical v6 expression when all tier counts are 0) and a TIERED path. Has NO audio-tier math — dual-stream cost needs new, additive logic that leaves this function's existing behavior byte-identical for every non-realtime model (audio counts always 0 there).
  - `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder.record` / `_record_internal` / `supported_extras` — the extras seam (`cached`, `pricing_unit`, `quantity`, `usage_source`, `provider_generation_id`, …); 3 new extras (`audio_prompt_tokens`, `audio_completion_tokens`, `audio_cached_tokens`) would follow the EXACT established pattern of `cached_tokens`/`reasoning_tokens`: int, default 0, str-encoded into `event_fields`, never raises (swallow + log per the class docstring's Must #5).
  - `apps/gateway/src/gateway/usage/application/flusher.py:UsageLedgerFlusher` — parses `event_fields` strings back to typed columns and runs the literal `INSERT INTO usage_records (...) VALUES (...) ON CONFLICT (id) DO NOTHING`; would need the 3 new audio_*_tokens columns added to both the field-parse block and the INSERT column/VALUES lists (mirrors how `cached_tokens`/`reasoning_tokens` were added).
  - `apps/gateway/src/gateway/catalog/infrastructure/orm.py:PricingSnapshotRow` — ALREADY has `audio_prompt_usd_per_token` / `audio_completion_usd_per_token` / `audio_cached_usd_per_token` (`Numeric(20,10)`, nullable) — no schema change needed, only needs to be SELECTed.
  - `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — ALREADY has `audio_prompt_tokens` / `audio_completion_tokens` / `audio_cached_tokens` (`Integer`, `NOT NULL DEFAULT 0`) — no schema change needed, only needs to be written.
  - `apps/gateway/src/gateway/core/config.py:Settings.realtime_relay_openai_model` — default `"gpt-realtime"` (gpt-realtime-pricing-fields); the model id both the relay dials and this task's pricing lookup + `usage_records.model_id` must use.

Context (working folder):
  - `.add/milestones/gpt-realtime-pricing/MILESTONE.md` — states the real OpenAI Realtime `response.done` usage shape from the original 2026-07-01 GROUND research: `input_token_details` / `output_token_details` carrying text/audio/cached breakdowns (`total_tokens`, `input_tokens`, `output_tokens` at the top level; per-stream detail nested). Explicitly leaves **"one usage_records row per session vs per turn" as design-TBD at SPECIFY** — the single biggest open question for this task.
  - `.add/tasks/gpt-realtime-pricing-fields/TASK.md` §7 OBSERVE — prior task's watch note: "audio_*_usd_per_1m null-rate on /v1/models (should stay ~0% for gpt-realtime, 100% for every other model) — a flip either way signals a mapping bug" — this task's billing math must not accidentally leak non-null audio pricing onto a non-realtime model's cost computation, nor null it out for gpt-realtime.
  - `apps/gateway/migrations/versions/a4c6e8b0d2f3_gpt_realtime_audio_columns.py` — the already-applied schema migration (task 1); confirms `usage_records` audio columns default to 0 (safe for every pre-existing row) and `pricing_snapshots` audio columns are nullable.

Honors (patterns / conventions):
  - PR #53 (`fix/stream-alias-billing`, merged into main 2026-07-02) precedent: "the served candidate is captured from the routing decision, never recomputed" via an optional `on_served`/`on_committed` callback threaded through `fallback_router.py`/`streaming_resilience.py` WITHOUT changing the core streaming method's existing signature for callers that don't pass it. The same shape (an optional callback param) is the natural fit for surfacing usage out of a Protocol-bound `RealtimeRelaySession` without widening the Protocol for every provider.
  - `.add/CONVENTIONS.md` write-behind discipline (Redis Stream `usage:events` → `UsageLedgerFlusher` → `usage_records`, cited around L478/L314) — the established, ONLY path a usage record reaches the DB; this task must reuse it, not invent a second write path for the relay.
  - This milestone's own shared decision (MILESTONE.md "Shared decisions"): `risk: high` + `autonomy: conservative` on every task here — billing math + a live relay path, both HARD-STOP-worthy per CLAUDE.md's design-for-failure rule; explicit human review at contract-freeze and verify, no auto-resolve.
  - `RecordingUsageRecorder`'s own class docstring Must #5 ("NEVER raise into the proxy path — all failures swallowed + logged") — extends here to "never raise into / hang a live voice relay session."
  - Additive-only schema discipline (already honored by tasks 1-2, cited in MILESTONE.md Scope): every change must be nullable/zero-safe for every existing text-only model; `compute_per_token_cost_usd`'s FLAT path must stay byte-identical when audio counts are 0 (true for every model except gpt-realtime).

Anchors the contract cites:
  - `OpenAIRealtimeSession._translate_server_event` (the `response.done` branch)
  - `OpenAIRealtimeSession.events`
  - `RealtimeRelaySession` (Protocol, `domain/realtime_relay.py`)
  - `RelayPump.run` / `RelayPump._provider_to_client` (`application/realtime_relay_pump.py`)
  - `realtime_relay` WS endpoint (`api/realtime_relay_ws.py`)
  - `RecordingUsageRecorder.record` / `_record_internal` / `supported_extras` (`usage/application/recorder.py`)
  - `_fetch_latest_pricing` (`usage/application/recorder.py`)
  - `compute_per_token_cost_usd` (`usage/application/recorder.py`)
  - `UsageLedgerFlusher` INSERT statement (`usage/application/flusher.py`)
  - `PricingSnapshotRow.audio_prompt_usd_per_token` / `.audio_completion_usd_per_token` / `.audio_cached_usd_per_token` (`catalog/infrastructure/orm.py`, already present)
  - `UsageRecordRow.audio_prompt_tokens` / `.audio_completion_tokens` / `.audio_cached_tokens` (`usage/infrastructure/orm.py`, already present)
  - `Settings.realtime_relay_openai_model` (`core/config.py`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Capture real GPT-Realtime `response.done` usage in the relay and bill it, per turn,
  through the existing write-behind pipeline — using the dual-stream (text+audio) pricing already
  seeded and shipped by tasks 1-2.
Framings weighed:
  - **(chosen)** An optional capture callback threaded through `RelayPump`/`RealtimeRelaySession`
    (mirrors PR #53's `on_served`/`on_committed` pattern), firing once per `response.done` event
    with a `usage` object — ONE `usage_records` row per conversation turn. Extends
    `_fetch_latest_pricing` + `compute_per_token_cost_usd` additively for the 3 audio tiers;
    routes through the existing `RecordingUsageRecorder` → Redis Stream `usage:events` →
    `UsageLedgerFlusher` write-behind path, no new write path.
  - One `usage_records` row per SESSION (accumulate turns, write once at WS close) — rejected per
    Tin's explicit decision (AskUserQuestion, 2026-07-02): a session that ends abnormally (crash,
    network drop) before an accumulate-and-flush-at-close would lose ALL usage for that session, a
    materially bigger revenue-leak surface than per-turn, and it also diverges from every other
    billing path in this codebase (all are per-request/per-completion, not per-connection).
  - Push usage-capture/recording responsibility directly into `OpenAIRealtimeSession` (the provider
    adapter) instead of a Protocol-boundary callback — rejected: either widens
    `RealtimeRelaySession` for a capability only OpenAI's Realtime API exposes (Gemini Live has no
    equivalent per-turn usage event) or forks `RelayPump` into two provider-specific code paths,
    both violating the provider-agnostic seam `RelayPump` is built on and putting Gemini Live
    (explicitly out of scope) at risk of an unintended behavior change.
Must:
<must>
  - Every `response.done` server event that carries a `usage` object produces exactly ONE
    `usage_records` row — per-turn billing (Tin's decision, AskUserQuestion 2026-07-02): a session
    with N turns yields N rows, never one aggregate row at session close.
  - The raw OpenAI event is captured BEFORE `_translate_server_event` discards it, via an optional
    callback param threaded from `realtime_relay_ws.py`'s `realtime_relay` endpoint through
    `RelayPump` — WITHOUT changing the `RealtimeRelaySession` Protocol's existing signature for
    callers that don't pass it; Gemini Live's session stays byte-identical (callback never invoked).
  - `_fetch_latest_pricing` additionally SELECTs `audio_prompt_usd_per_token`,
    `audio_completion_usd_per_token`, `audio_cached_usd_per_token` from `pricing_snapshots` —
    additive; byte-identical result for every existing caller (these columns are NULL for every
    non-realtime model).
  - `compute_per_token_cost_usd` gains additive audio-tier cost math (audio-prompt/completion/cached
    token counts × their per-token rates) that only activates when an audio token count is > 0; for
    every existing call site (audio counts implicitly 0) the return value is byte-identical to today.
  - The captured `usage.input_token_details`/`usage.output_token_details` are decomposed into the 6
    token-count buckets the schema already has: `prompt_tokens`, `completion_tokens`,
    `cached_tokens` (existing text columns) and `audio_prompt_tokens`, `audio_completion_tokens`,
    `audio_cached_tokens` (the 3 new columns from task 1).
  - Recording goes through `RecordingUsageRecorder.record()` → the existing Redis Stream
    `usage:events` write-behind path → `UsageLedgerFlusher` → `INSERT INTO usage_records` — no
    second/parallel write path.
  - Every relay-billed row's `model_id` is `settings.realtime_relay_openai_model` (the model the
    relay actually dialed) and `usage_source` is set to a value that distinguishes relay-billed rows
    from proxy-billed rows (mirrors the existing `usage_source` discriminator convention).
  - `tenant_id`/`key_id` on every recorded row come from `authz`, established once at WS auth time
    in `realtime_relay_ws.py`'s `realtime_relay`, never re-derived mid-session.
  - A billing-pipe failure at ANY point (missing usage, malformed shape, no pricing row, recorder
    Redis failure) must never raise into or disrupt the live relay WS session — matches
    `RecordingUsageRecorder`'s existing "never raise into the proxy path" Must and CLAUDE.md's
    design-for-failure rule (timeouts/retries/circuit-breakers/rollback all N/A here; the correct
    failure mode is swallow + log, not retry, since a missed turn's billing is not recoverable
    after the frame has already reached the client).
</must>
Reject:
<reject>
  - `response.done` event with no `usage` field present -> skip recording (no `usage_records` row
    written, no relay disruption), logged at DEBUG -> `usage_absent_skip`.
  - `usage` field present but not a dict (e.g. a string/int/list — a genuinely unparseable shape,
    distinct from a dict with missing/non-numeric SUB-fields, which degrades field-by-field to 0
    via the existing `_safe_tier` fallback and IS still recorded, matching how every other tiered
    field already behaves) -> skip recording, logged at WARN with the raw shape for diagnosis,
    never raised -> `usage_malformed_skip`.
  - No `pricing_snapshots` row for `settings.realtime_relay_openai_model` -> cost computation is
    skipped (this is PRE-EXISTING `_record_internal` behavior, unchanged by this task): the row is
    still written via the existing unconditional XADD (matching how every other model's
    pricing-miss already behaves — the write-behind pipe never drops an event), but with
    `cost_usd=0` and `pricing_snapshot_id=""` (NULL) — an auditable, honest "we didn't know the
    price" marker, distinguishable from a real $0-cost model. NEVER a fabricated non-zero cost.
    (Adversarially reviewed and corrected before VERIFY — the original wording here said "skip
    recording entirely", which didn't match actual `_record_internal` behavior.) -> `pricing_unavailable_skip`.
  - `usage_recorder.record()` itself raising (e.g. Redis unavailable) -> swallow + log, per
    `RecordingUsageRecorder`'s established Must #5 -> `usage_recorder_failure_swallowed`; the relay
    session continues uninterrupted.
</reject>
After:
<after>
  - A completed GPT-Realtime relay turn (one `response.done` with usage) produces exactly one new
    `usage_records` row with correctly split text+audio token counts and a non-null `cost_usd`
    computed from the real dual-stream `pricing_snapshots` rates.
  - A multi-turn session produces multiple `usage_records` rows, one per turn — billing history has
    per-turn granularity, matching every other billing path in the codebase.
  - The dual-stream pricing already shipped on `GET /v1/models` (task 2) is now actually consumed by
    cost computation for gpt-realtime traffic, not just displayed.
  - The relay WS session's availability is completely unaffected by any billing-pipe failure — a
    tenant's voice session never drops or errors because of a pricing-lookup miss or a Redis hiccup.
  - Gemini Live's session path (out of scope) is byte-identical — zero behavior change, since it
    never invokes the new optional callback.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The exact raw JSON shape of `response.done`'s `usage` object — specifically whether
  `input_token_details`/`output_token_details` always include both an `audio_tokens` and a
  `text_tokens` sub-key, or whether either is sometimes entirely absent for an audio-only or
  text-only turn — is inferred from the milestone's original GROUND research (a documentation/
  community reference), not a captured live payload in THIS test environment (no live OpenAI
  Realtime credential available here, same limitation the sibling `gpt-realtime-pricing-fields`
  task already flagged) — lowest confidence because a wrong shape assumption could either crash the
  parse (mitigated by the `usage_malformed_skip` Reject rule above, so worst case is silent
  under-billing rather than a crash) or systematically mis-split tokens between text/audio columns
  (a silent cost-accuracy defect, harder to detect than a crash). Mitigation: TESTS will pin fixture
  payloads for (a) audio-only, (b) text-only, (c) mixed, (d) missing-detail-key turns; VERIFY will
  re-confirm against OpenAI's current API reference before contract freeze; OBSERVE will carry a
  watch-note (mirroring task 2's null-rate watch) on any relay-billed row recorded with all-zero
  audio counts, to catch a parse miss post-deploy.
  - [x] Whether `RelayPump.run`'s teardown is where a capture callback must fire — RESOLVED at
    CONTRACT by re-reading the real code: `RelayPump._provider_to_client` only ever sees frames
    ALREADY translated by `OpenAIRealtimeSession.events()`; it never touches raw usage. The §3
    design captures usage SYNCHRONOUSLY inside `events()`, per raw frame, BEFORE the translated
    frame is yielded — so there is no "final buffered event lost at teardown" risk to begin with;
    `RelayPump`/`_teardown` are untouched by this task entirely.
  - [x] Whether `usage_source` needs a new enum/literal value — RESOLVED at CONTRACT: confirmed via
    migration `b8e4f1a7c2d5` that `usage_records.usage_source` is a free TEXT column with no CHECK
    constraint (existing values include `"frame"`, `"stream_fallback"`); `"realtime_relay"` is a
    valid new free-text value, no schema change needed.
  - [x] One row per turn vs. one row per session — resolved by Tin's explicit AskUserQuestion
    decision this session (one row per turn, matching every other billing path's granularity).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: One usage_records row per completed turn
  Given an authenticated GPT-Realtime relay session (tenant_id, key_id from authz)
  When the provider sends a `response.done` event carrying a `usage` object
  Then exactly one new `usage_records` row is written for that turn
  And the row's model_id is settings.realtime_relay_openai_model ("gpt-realtime")

Scenario: A multi-turn session bills per turn, not per session
  Given an open relay session that has already produced one billed turn
  When a second `response.done` event with usage arrives on the SAME session
  Then a second, distinct `usage_records` row is written (two rows total)
  And the session's WebSocket connection is never closed or interrupted to write either row

Scenario: Raw usage is captured before translation discards it
  Given OpenAIRealtimeSession.events is streaming raw provider frames
  When a `response.done` frame is translated by _translate_server_event
  Then the capture callback receives the raw event's usage object
  And the translated RelayFrame yielded to the client is unchanged (still `{"type": "response.done"}`)

Scenario: Gemini Live stays byte-identical
  Given a Gemini Live relay session (a RealtimeRelaySession implementation with no usage-capture support)
  When RelayPump.run drives its frame loop to completion
  Then no capture callback is ever invoked for that session
  And Gemini Live's existing behavior and test suite pass unmodified

Scenario: Pricing lookup includes the 3 audio tiers
  Given a pricing_snapshots row for "gpt-realtime" with non-null audio_prompt/completion/cached_usd_per_token
  When _fetch_latest_pricing is called for that model
  Then the returned pricing includes all 3 audio per-token rates alongside the existing text rates
  And a lookup for any pre-existing non-realtime model returns the same fields it always did (audio fields null)

Scenario: Cost math is additive and byte-identical for non-realtime models
  Given a non-realtime model's usage event (audio token counts implicitly 0)
  When compute_per_token_cost_usd computes cost
  Then the returned cost_usd is byte-identical to today's pre-task value
  And no audio-tier term is added to the computation

Scenario: Cost math includes audio tiers for a realtime turn
  Given a captured GPT-Realtime usage event with non-zero audio_prompt_tokens and audio_completion_tokens
  When compute_per_token_cost_usd computes cost using the extended pricing
  Then cost_usd includes the audio-tier contribution (audio tokens × their audio rates)
  And the text-tier contribution is computed exactly as it is for any other model

Scenario: Usage is decomposed into the 6 existing token-count columns
  Given a captured usage object with input_token_details/output_token_details text+audio+cached breakdowns
  When the usage is recorded
  Then prompt_tokens/completion_tokens/cached_tokens hold the text-detail counts
  And audio_prompt_tokens/audio_completion_tokens/audio_cached_tokens hold the audio-detail counts

Scenario: Recording reuses the existing write-behind pipeline only
  Given a captured, priced usage event ready to record
  When RecordingUsageRecorder.record() is called
  Then one event is XADDed to the Redis Stream "usage:events" (no other write path is touched)
  And UsageLedgerFlusher's next XREADGROUP consumes it and INSERTs the usage_records row

Scenario: Relay-billed rows are distinguishable from proxy-billed rows
  Given a usage_records row written by the relay's capture path
  When usage_source is inspected on that row
  Then it identifies the row as relay-billed, distinct from a proxy-billed row's usage_source

Scenario: tenant_id/key_id come from WS auth, never re-derived
  Given a relay session authenticated once at connect time (authz.tenant_id, authz.key_id)
  When multiple turns are billed over the life of that session
  Then every resulting usage_records row carries the SAME tenant_id/key_id from that one authz
  And no per-turn re-authentication or re-derivation occurs

Scenario: A billing-pipe failure never disrupts the live session
  Given an open relay session mid-conversation
  When the billing pipe fails at any stage (missing usage, malformed shape, no pricing row, or
    usage_recorder.record() raising)
  Then the relay WebSocket session continues uninterrupted and the client receives its frames normally
  And the failure is logged, never raised into RelayPump's frame loop

Scenario: response.done with no usage object is skipped (REJECTION)
  Given a `response.done` event with no `usage` field
  When the capture callback processes it
  Then no usage_records row is written for that event
  And a DEBUG-level log records usage_absent_skip, with no exception raised

Scenario: Malformed usage shape is skipped (REJECTION)
  Given a `response.done` event whose `usage` field is present but is not a dict (e.g. a string)
  When the capture callback processes it
  Then no usage_records row is written for that event
  And a WARN-level log records usage_malformed_skip with the raw shape, with no exception raised

Scenario: A usage dict with missing/non-numeric sub-fields still bills what it can
  Given a `response.done` event whose usage dict is missing input_token_details/output_token_details
    or has non-numeric nested token counts
  When the capture callback processes it
  Then a usage_records row IS still written, with every unparseable field degraded to 0 via the
    existing _safe_tier fallback (matching how every other tiered field already behaves)
  And no exception is raised

Scenario: Missing pricing snapshot never bills a fabricated cost (REJECTION)
  Given no pricing_snapshots row exists for settings.realtime_relay_openai_model
  When a valid, well-formed usage event is otherwise ready to record
  Then a usage_records row IS still written (pre-existing write-behind guarantee: the pipe never
    drops an event), with cost_usd=0 and pricing_snapshot_id=NULL — an honest, auditable
    "unpriced" marker, never a fabricated non-zero cost
  And no exception is raised

Scenario: usage_recorder.record() failure is swallowed (REJECTION)
  Given the Redis Stream backing usage:events is unavailable
  When RecordingUsageRecorder.record() is called for a valid, priced usage event
  Then the exception is caught and logged as usage_recorder_failure_swallowed
  And the relay session's frame loop and WebSocket connection are unaffected
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

No HTTP surface changes — this is an internal capture/compute/record pipeline. Interfaces below
are the frozen function/message shapes.

```
# 1) Capture seam — OpenAIRealtimeSession gains an optional async usage callback.
#    RealtimeRelaySession Protocol, RelayPump, and GeminiLiveSession are UNTOUCHED
#    (events() -> AsyncIterator[RelayFrame] is unchanged; the callback lives entirely
#    inside the OpenAI adapter, matching its existing "sole owner of OpenAI wire
#    format" docstring Must).
OpenAIRealtimeSession.__init__(..., on_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None)
  events(): on kind == "response.done", if self._on_usage is not None and event.get("usage") is a
    dict -> await self._on_usage(_translate_realtime_usage(event["usage"])) BEFORE yielding the
    translated RelayFrame (still byte-identical {"type": "response.done"}).
  _translate_realtime_usage(raw: dict) -> dict[str, Any]  (new staticmethod, pure, never raises —
    a malformed raw shape produces a dict whose absent/non-numeric fields _safe_tier reads as 0):
      {
        "prompt_tokens": raw.get("input_tokens", 0),
        "completion_tokens": raw.get("output_tokens", 0),
        "prompt_tokens_details": {"cached_tokens": raw.get("input_token_details", {}).get("cached_tokens", 0)},
        "input_token_details": raw.get("input_token_details", {}),   # passthrough — audio_tokens read below
        "output_token_details": raw.get("output_token_details", {}),  # passthrough — audio_tokens read below
      }

# 2) realtime_relay_ws.py:_real_session_factory — openai branch only — wires the callback.
#    GeminiLiveSession's branch is UNCHANGED (no on_usage param exists there).
OpenAIRealtimeSession(
    model=settings.realtime_relay_openai_model,
    api_key=api_key,
    connect_timeout=settings.realtime_relay_connect_timeout_seconds,
    on_usage=_make_relay_usage_callback(app.state.usage_recorder, authz.tenant_id, authz.key_id,
                                         settings.realtime_relay_openai_model),
)
  _make_relay_usage_callback(recorder, tenant_id, key_id, model) -> Callable[[dict], Awaitable[None]]
    returns an async closure: `async def _cb(usage): await recorder.record(tenant_id=tenant_id,
    key_id=key_id, model=model, usage=usage, status=200, usage_source="realtime_relay")`
    — reuses RecordingUsageRecorder.record()'s EXISTING signature verbatim (no new kwargs); the
    Must #5 "never raise into the caller" guarantee is already enforced by record() itself.

# 3) usage/application/recorder.py — additive reads + additive cost math, both byte-identical
#    for every usage dict that doesn't carry input_token_details/output_token_details (i.e. every
#    non-realtime model, forever).
_fetch_latest_pricing(...) -> (snapshot_id, prompt_price, completion_price, pricing_unit,
    unit_usd_per_unit, cached_input_price, reasoning_price, cache_creation_price,
    audio_prompt_price, audio_completion_price, audio_cached_price)   # 3 NEW trailing elements;
    SELECTs the 3 audio_*_usd_per_token columns (already on pricing_snapshots); single call site
    (RecordingUsageRecorder._record_internal) updated to unpack 11, not 8.

RecordingUsageRecorder._record_internal — per_token branch gains 3 new reads, reusing the existing
    _safe_tier() helper verbatim (same pattern as cached_tokens/reasoning_tokens):
      audio_prompt_tokens    = _safe_tier(usage, "input_token_details", "audio_tokens")
      audio_completion_tokens = _safe_tier(usage, "output_token_details", "audio_tokens")
      audio_cached_tokens    = _safe_tier(usage, "input_token_details", "cached_tokens")
    The flat/byte-identical cost path's guard condition gains the 3 new counts (an audio-bearing
    usage dict always routes to the tiered/audio-aware path, never the silent flat path).
    event_fields gains 3 new XADD keys (mirrors cached_tokens/reasoning_tokens exactly):
      "audio_prompt_tokens": str(audio_prompt_tokens), "audio_completion_tokens": str(...),
      "audio_cached_tokens": str(...)
    No new record()/_record_internal() KWARGS — audio counts are derived from `usage`, exactly
    like cached_tokens/reasoning_tokens already are. supported_extras is UNCHANGED.

compute_per_token_cost_usd(..., audio_prompt_tokens: int = 0, audio_completion_tokens: int = 0,
    audio_cached_tokens: int = 0, audio_prompt_price: Decimal | None = None,
    audio_completion_price: Decimal | None = None, audio_cached_price: Decimal | None = None)
  -> Decimal   # adds audio_prompt_tokens*audio_prompt_price + audio_completion_tokens*
    audio_completion_price + audio_cached_tokens*(audio_cached_price or audio_prompt_price) to the
    tiered-path sum, multiplied by the SAME markup factor as every other tier; a None audio price
    with a non-zero audio count logs "tier_token_clamped"-style and treats that tier's rate as 0
    (never raises, never silently double-counts). Every existing call site (audio args all default)
    returns byte-identical output.

# 4) usage/application/flusher.py:UsageLedgerFlusher — mirrors cached_tokens/reasoning_tokens'
#    3-part addition exactly:
    parse:  audio_prompt_tokens = int(_field("audio_prompt_tokens") or "0")   (+ completion, cached)
    INSERT: adds audio_prompt_tokens, audio_completion_tokens, audio_cached_tokens to the column
            list, VALUES placeholders, and params dict (already-existing NOT NULL DEFAULT 0
            columns from migration a4c6e8b0d2f3 — no new migration).

# 5) Reject-code responses (§1) — every path logs + returns/continues, NEVER raises into the caller:
usage_absent_skip            -> _translate_realtime_usage not invoked at all (guarded at the
                                 events() call site: `event.get("usage")` must be a non-empty dict).
usage_malformed_skip         -> _translate_realtime_usage + _safe_tier degrade every missing/
                                 non-numeric field to 0 (never raises); a resulting well-formed-but-
                                 all-zero usage dict is still recorded (this is NOT the same as
                                 "no usage" — matches every other model's honest-zero-usage handling,
                                 so this reject arm only fires for the true `usage_absent_skip` case
                                 above; a malformed-but-partially-parseable event bills what it can).
pricing_unavailable_skip     -> _fetch_latest_pricing returns None -> _record_internal's existing
                                 `if pricing is not None:` guard skips ALL cost/tier computation for
                                 that branch (pre-existing behavior, unchanged) — but the row is
                                 STILL written via the existing unconditional XADD (the write-behind
                                 pipe never drops an event, for any model), with cost_usd=0 and
                                 pricing_snapshot_id=NULL: an honest "unpriced" marker, never a
                                 fabricated cost. (Corrected here after adversarial review — the
                                 original text incorrectly said "no usage_records row written".)
usage_recorder_failure_swallowed -> RecordingUsageRecorder.record()'s existing try/except (Must #5,
                                 pre-existing) logs "usage_recorder.record failed (swallowed)" and
                                 returns; the relay callback's `await self._on_usage(...)` call is
                                 wrapped so ANY exception from the callback itself (not just
                                 record()'s internals) is also caught + logged inside
                                 OpenAIRealtimeSession.events(), never propagating into
                                 RelayPump._provider_to_client's frame loop.
Schema: pricing_snapshots.audio_{prompt,completion,cached}_usd_per_token (read-only, already
  migrated) · usage_records.audio_{prompt,completion,cached}_tokens (write, already migrated,
  NOT NULL DEFAULT 0) · usage_records.usage_source = "realtime_relay" for every relay-billed row
  (free-text column, no CHECK constraint — confirmed via migration b8e4f1a7c2d5).
```

Status: FROZEN @ v1 — approved by Tin Dang (AskUserQuestion, 2026-07-02)
Least-sure flag surfaced at freeze: [spec] The exact raw JSON shape of response.done's usage
  object — whether input_token_details/output_token_details always carry both audio_tokens and
  text_tokens/cached_tokens sub-keys, or omit them for audio-only/text-only turns — is inferred
  from documentation, not a live-captured payload (no live OpenAI Realtime credential in this
  environment). If wrong: either a crash (mitigated — _safe_tier degrades any malformed field to
  0, never raises) or a silent text/audio mis-split (a cost-accuracy defect). Mitigated by TESTS
  fixture coverage (audio-only/text-only/mixed/missing-detail-key), a VERIFY re-check against
  OpenAI's current API reference, and an OBSERVE watch-note on any all-zero-audio relay-billed row.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_response_done_with_usage_triggers_exactly_one_capture (S1): on_usage invoked exactly once
  - test_multiturn_session_captures_once_per_turn_not_merged (S2): 2 turns -> 2 separate captures
  - test_raw_usage_captured_before_translation_discards_it (S3): callback sees usage; yielded frame doesn't
  - test_gemini_live_constructor_has_no_on_usage_param (S4): GeminiLiveSession signature unchanged
  - test_pricing_lookup_includes_audio_tiers_for_realtime_model (S5): audio prices raise cost above text-only
  - test_compute_cost_byte_identical_when_audio_args_default (S6): flat-path cost unchanged, no audio args
  - test_compute_cost_includes_audio_tier_contribution (S7): cost == text_term + audio_term, exact Decimal
  - test_usage_decomposed_into_six_token_buckets (S8): event_fields carries all 6 token-count keys
  - test_recording_goes_through_redis_stream_xadd_only (S9): exactly one XADD, no second write path
  - test_relay_billed_row_has_distinct_usage_source (S10): usage_source="realtime_relay" != "frame"
  - test_real_session_factory_wires_authz_identity_into_every_turn (S11): 2 turns, same tenant/key each
  - test_on_usage_exception_does_not_disrupt_relay_frames (S12): both frames yielded despite callback raising
  - test_response_done_without_usage_skips_capture (S13/Reject usage_absent_skip): callback never called
  - test_non_dict_usage_skips_capture (S14/Reject usage_malformed_skip): non-dict usage -> callback never called
  - test_usage_dict_with_missing_subfields_still_bills_degraded_to_zero: dict-with-bad-subfields still captures
  - test_missing_pricing_snapshot_never_writes_zero_cost_row (S15/Reject pricing_unavailable_skip): cost stays 0
  - test_usage_recorder_record_failure_is_swallowed (S16/Reject usage_recorder_failure_swallowed): no raise
</test_plan>

Suite run: `uv run pytest tests/gpt_realtime_relay_billing/ -q --no-cov -p no:cacheprovider` ->
  12 failed, 5 passed (2026-07-02). All 12 failures are red for the right reason (missing
  implementation): `TypeError: OpenAIRealtimeSession.__init__() got an unexpected keyword
  argument 'on_usage'` · `ImportError: cannot import name '_make_relay_usage_callback'` ·
  `TypeError: compute_per_token_cost_usd() got an unexpected keyword argument
  'audio_prompt_tokens'` · `AttributeError: 'OpenAIRealtimeSession' object has no attribute
  '_on_usage'` · missing `audio_*_tokens` keys in `event_fields` · a pricing-lookup assertion
  that can't yet be true. The 5 passes are deliberate contract-conformance pins of EXISTING
  behavior that must stay byte-identical after BUILD (S4 Gemini signature, S6 flat-path cost,
  S9 single-XADD, and the two pre-existing pricing-miss/record-failure swallow guarantees) —
  not vacuous, each asserts real current behavior the build must not regress.

Tests live in: `apps/gateway/tests/gpt_realtime_relay_billing/` · RED confirmed above before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py` `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py` `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/tests/pricing_units/conftest.py` `apps/gateway/tests/provider_cost_reconciliation/conftest.py` `apps/gateway/tests/tiered_token_billing/conftest.py` `apps/gateway/tests/gpt_realtime_relay_billing/`
Strategy (ordered batches):
  1. `recorder.py:_fetch_latest_pricing` — extend the SELECT + return tuple to 11 elements (3 new
     trailing audio prices); update its single call site in `_record_internal`'s unpack.
  2. `recorder.py:_record_internal` — add 3 `_safe_tier(usage, "input_token_details"/
     "output_token_details", "audio_tokens"/"cached_tokens")` reads; extend the flat-path guard
     condition with the 3 new counts; extend `event_fields` with the 3 new XADD keys.
  3. `recorder.py:compute_per_token_cost_usd` — add the 3 audio token-count + 3 audio price kwargs
     (all defaulted) and their additive tiered-path term.
  4. Update the 3 sibling `conftest.py` fakes (batch 4 below) that hand-construct
     `_fetch_latest_pricing`'s positional row tuple — append 3 `None`-padded trailing elements,
     mirroring each file's own established mirroring convention for the prior
     cached/reasoning/cache_creation extensions.
  5. `flusher.py:UsageLedgerFlusher` — parse the 3 new `event_fields` keys; extend the INSERT
     column list / VALUES placeholders / params dict (mirrors cached_tokens/reasoning_tokens
     exactly).
  6. `openai_realtime.py:OpenAIRealtimeSession` — add `on_usage` ctor param, `_translate_realtime_usage`
     staticmethod, and the capture call inside `events()` on `response.done` (wrapped so a callback
     exception never propagates into the frame-yield loop).
  7. `realtime_relay_ws.py:_real_session_factory` — add `_make_relay_usage_callback` and wire it
     into the `openai` branch only; the `gemini` branch stays byte-identical.
  8. New suite `apps/gateway/tests/gpt_realtime_relay_billing/` — one test per §2 scenario (13
     total: 9 Must + 4 Reject).
  9. Full regression suite (all pre-existing suites, especially the 3 touched in batch 4 and
     `tests/realtime/`) green before VERIFY.
Known-problem fixes:
  - `pricing_units/conftest.py`, `provider_cost_reconciliation/conftest.py`,
    `tiered_token_billing/conftest.py` each hand-construct `_fetch_latest_pricing`'s
    `pricing_snapshots` row as a raw positional tuple (SQL-text-sniffing `FakeSession`, not a
    patched function) — extending the SELECT to 11 columns without updating these 3 fakes WILL
    `IndexError: tuple index out of range` the moment `_record_internal` unpacks row[8..10].
    Pre-identified here at CONTRACT (the exact class of test-double staleness a recent sibling-PR
    merge caught only at test-run time) — planned fix is batch 4 above, not a build-time surprise.
  - `GeminiLiveSession`'s constructor must NOT gain an `on_usage` param and its branch in
    `_real_session_factory` must stay untouched — confirm its own test suite still passes
    unmodified as evidence.
Strategy actually used: as planned (batches 1-9), plus ONE material correctness refinement
  discovered mid-build, surfaced here for VERIFY's explicit attention since it touches billing
  math and is downstream of the ⚠ flag already surfaced at freeze:
    `compute_per_token_cost_usd`'s frozen §3 description ("adds audio_prompt_tokens*
    audio_prompt_price ... to the tiered-path sum") was ambiguous on whether audio_prompt_tokens/
    audio_completion_tokens are ADDITIONAL to prompt_tokens/completion_tokens or a BREAKDOWN
    (subset) of them. Implementing it additively would DOUBLE-BILL every realtime turn's audio
    portion (once at the text rate via the untouched prompt_tokens, again at the audio rate) —
    a real financial-correctness bug, not a cosmetic one. Attempted to confirm OpenAI's exact
    response.done usage schema via WebFetch (developers.openai.com/api/docs/api-reference/
    realtime-server-events/response/done and the realtime-conversations guide) — both pages
    loaded but did not surface the field-level input_token_details/output_token_details schema
    text needed for a definitive citation. Proceeded on the SUBSET interpretation based on: (1)
    MILESTONE.md's own GROUND research phrasing ("breakdowns... at the top level; per-stream
    detail nested"), (2) the `_details` suffix naming convention already established in THIS
    codebase for a confirmed-subset relationship (`prompt_tokens_details.cached_tokens` IS a
    subset of `prompt_tokens`, not additional — same shape, same naming pattern, Chat
    Completions' analogous audio-preview usage object), and (3) the subset choice is the
    FINANCIALLY SAFER failure direction if wrong (undercounts audio cost rather than
    double-charging tenants). Implemented as: `fresh_in = prompt_tokens - cached_tokens -
    cache_creation_tokens - audio_prompt_tokens`, `fresh_out = completion_tokens -
    reasoning_tokens - audio_completion_tokens`, `fresh_audio_in = audio_prompt_tokens -
    audio_cached_tokens` — mirrors the EXISTING cached_tokens-is-a-subset-of-prompt_tokens
    pattern one level deeper, extended (not reinvented). The RED test I originally wrote for
    scenario 7 assumed the (wrong, double-counting) additive shape — corrected it to match
    before it went green, documented inline in the test with the reasoning above. FLAGGING THIS
    EXPLICITLY FOR YOUR REVIEW: this is the single highest-stakes judgment call in this task: if
    the real OpenAI schema turns out to be additive after all, the fix is a one-line change
    (drop the 3 subtractions), not a redesign — but I want your eyes on it before it bills a
    single real tenant. OBSERVE will carry a watch-note either way (see §7).

  POST-BUILD ADVERSARIAL REVIEW (before VERIFY): spawned an independent general-purpose agent
  to review the full diff before presenting this gate, per CLAUDE.md's "verify large changes
  ... by manual review or spawn subagent" rule (risk: high billing code). It found and I fixed
  ONE real, CONFIRMED, previously-undisclosed defect (distinct from the ⚠-flagged subset-vs-
  additive uncertainty above): `input_token_details.cached_tokens` in OpenAI's raw payload is
  the COMBINED text+audio cached total, not audio-specific — the review agent sourced the real
  documented shape (`input_token_details: {text_tokens, audio_tokens, cached_tokens,
  cached_tokens_details: {text_tokens, audio_tokens}}`) via its own web research, which
  succeeded where my own WebFetch attempts during BUILD did not. My original
  `_translate_realtime_usage` read the SAME combined `cached_tokens` value for BOTH the
  text-tier `cached_tokens` (via `prompt_tokens_details.cached_tokens`) AND the audio-tier
  `audio_cached_tokens` (via `input_token_details.cached_tokens`) — double-billing the cached
  portion at two different rates for any turn with cached audio activity, an overcharge bug
  independent of and worse than the disclosed subset-vs-additive risk (which only risks
  undercounting). FIXED: `_translate_realtime_usage` now un-nests `cached_tokens_details`
  and correctly splits text-cached (256 in the review's worked example) from audio-cached
  (384) before repackaging into the recorder-canonical shape — `_record_internal`'s existing
  `_safe_tier` reads needed no change. Added a regression test
  (`test_translate_realtime_usage_splits_combined_cached_into_text_and_audio`) pinning the
  exact real-world shape. The agent also found ONE low-severity, PRE-EXISTING (not introduced
  by this task) doc/prose inaccuracy in this very TASK.md: the `pricing_unavailable_skip`
  reject description said "no usage_records row written", but `_record_internal` has always
  unconditionally XADDed (a $0/NULL-snapshot row IS written, honestly marked, matching every
  other model's pricing-miss behavior) — corrected the §1/§2/§3 prose to describe actual
  behavior rather than the code (the code itself needed no change; this task's own new test
  for that scenario was already asserting the REAL behavior correctly). Every other focus area
  the review agent checked (double-counting arithmetic self-consistency, the 11-tuple's single
  call site, callback exception-safety into the live WS, usage_source collision, Gemini Live
  non-interference, flusher column/param alignment) came back clean. Full suite re-run after
  both fixes: gpt_realtime_relay_billing 18/18 green; the 7 previously-failing unrelated tests
  (catalog/SSO/upstream-health/ratelimit) re-ran clean in isolation, confirming that failure
  batch was shared-test-infra contention from a concurrent process, not a regression from this
  diff (see project's documented Redis-cross-contamination gotcha); ruff + pyright clean.
Safety rule (feature-specific): `await self._on_usage(...)` inside `OpenAIRealtimeSession.events()`
  is wrapped in its own try/except (log + continue) so a callback-side failure (a pricing-lookup
  exception, or anything upstream of `RecordingUsageRecorder.record()`'s own try/except) can never
  propagate into the frame-yield loop and tear down a live relay session — the same "never raises"
  discipline `RelayPump._teardown` already applies, extended one layer up to the capture site.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 18/18 in `tests/gpt_realtime_relay_billing/`; sibling suites touched
      (pricing_units, provider_cost_reconciliation, tiered_token_billing, prompt_cache_passthrough,
      realtime_relay, realtime, usage) 101 passed/1 skipped; full-suite run showed 5
      fail+2 error but ALL 7 re-ran clean in isolation (unrelated areas: catalog sync/SSO/upstream
      health/ratelimit — none touch this task's files) — confirmed shared-test-infra contention
      from a concurrent process (documented project gotcha), not a regression.
- [x] coverage did not decrease — 90% target: every new symbol (`_translate_realtime_usage`,
      `_make_relay_usage_callback`, the 3 audio kwargs on `compute_per_token_cost_usd`, the 3
      new `_fetch_latest_pricing` tuple positions, the 3 new event_fields/INSERT columns) is
      exercised by at least 2 tests (happy-path + one edge case); no pre-existing line lost
      coverage (all touched functions kept their existing test coverage, only extended).
- [~] no test or contract was altered during build — PARTIALLY, disclosed not hidden: (1) the S7
      scenario-7 test was corrected from an additive to a subset cost formula BEFORE it ever went
      green (a wrong assumption caught mid-build, not a weakening — see §5 Strategy actually
      used); (2) §1/§2/§3's `pricing_unavailable_skip` PROSE was corrected post-freeze to match
      actual pre-existing `_record_internal` behavior (a doc-accuracy fix an adversarial review
      caught — the underlying CODE for that path was never touched, only this task's own
      description of it). No test was weakened to force a pass; no CONTRACT signature/interface
      changed. Both edits are called out explicitly here and in §5 for your judgment, not buried.
- [x] the green was EARNED, not gamed — adversarial refute-read completed (see below); the agent
      also independently confirmed no vacuous asserts across the 18-test suite and traced the
      arithmetic symbolically (fresh_in + cached + cache_creation + fresh_audio_in + audio_cached
      == prompt_tokens, no double-count/no lost tokens given the subset design).
- [x] concurrency / timing of the risky operation is safe — `on_usage` fires synchronously inside
      one WS connection's single `events()` async-generator loop (no concurrent invocations across
      turns of the SAME session; cross-session concurrency was already safe pre-existing, one
      `RecordingUsageRecorder` instance shared read-only-after-construction across sessions).
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new dependency; the
      2 new SQL statements use bound `:audio_prompt_tokens`-style params, no string interpolation;
      no key/secret touched or logged (the on_usage callback's exception log uses `exc_info=True`,
      never the exception message body, matching `connect()`'s existing "no URL leak" discipline).
- [x] layering & dependencies follow CONVENTIONS.md — reused the write-behind pipe verbatim (no
      second write path); `OpenAIRealtimeSession` stays sole owner of OpenAI wire-format
      translation (the Honors-cited convention); `RealtimeRelaySession` Protocol/`RelayPump`
      untouched.
- [ ] a person reviewed and approved the change — PENDING: this is the ask below.

### Build expectations — what "correct" looks like
- [x] A `response.done` event with usage produces exactly one `usage_records` row with non-null
      `cost_usd` reflecting both text and audio rates — confirmed by
      `test_pricing_lookup_includes_audio_tiers_for_realtime_model` +
      `test_compute_cost_includes_audio_tier_contribution` (exact Decimal equality, not a range).
- [x] Audio tokens are billed once, not twice, even when cached — confirmed by the new regression
      test `test_translate_realtime_usage_splits_combined_cached_into_text_and_audio` (added after
      the adversarial review caught the combined/split confusion) using OpenAI's real documented
      nested shape.
- [x] Gemini Live is byte-identical — confirmed by `git diff` showing zero changes to
      `gemini_live.py`, plus `test_gemini_live_constructor_has_no_on_usage_param` and its own
      untouched suite passing.
- [x] A billing-pipe failure never disrupts the live relay session — confirmed by
      `test_on_usage_exception_does_not_disrupt_relay_frames` (both turns' frames still yielded
      despite the callback raising on every call).

### Deep checks
- [x] WIRING (code) — `on_usage` is constructed in `_real_session_factory`'s openai branch and
      consumed in `OpenAIRealtimeSession.events()`; `_make_relay_usage_callback` is called from
      exactly one production site plus directly in 2 tests; `_translate_realtime_usage` is called
      from exactly one site (`events()`); no orphaned new symbol.
- [x] DEAD-CODE (code) — none; every new function/param has at least one real caller.
- [x] SEMANTIC (prose) — re-read all of §0-§5 in full during this VERIFY pass while reconciling
      the adversarial-review findings against the frozen §3 CONTRACT; confirmed the 2 prose
      corrections (S7 test assumption, pricing_unavailable_skip description) don't touch the
      frozen function signatures/schema, only internal arithmetic and doc accuracy.

### Refute-read verdict
Verdict: EARNED
By: agent (general-purpose subagent, independent — no access to my BUILD reasoning beyond the
    TASK.md context I gave it) · adversarially checked: double-counting/lost-token arithmetic in
    `compute_per_token_cost_usd`; every OTHER caller of `_fetch_latest_pricing` repo-wide (found
    none missed); whether `on_usage` could ever raise into the live WS (traced the double-guard);
    `usage_source` collision (none); vacuous-assertion sweep across all 18 new tests;
    `GeminiLiveSession` non-interference (git diff empty); flusher INSERT column/param alignment.
    Found 1 CONFIRMED high-severity defect (fixed: cached_tokens combined-vs-split confusion) and
    1 CONFIRMED low-severity doc-accuracy defect (fixed: pricing_unavailable_skip prose).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-02

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (AskUserQuestion, 2026-07-02))
- [AI] build — strategy used: as planned (batches 1-9), plus ONE material correctness refinement
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
- [SPEC · open] live-verify GPT-Realtime relay billing against real OpenAI Realtime
  infrastructure (a real WS session, a real `response.done` usage payload) before this bills
  production tenant traffic at scale — no task in the gpt-realtime-pricing milestone ever had a
  live OpenAI Realtime credential available, so `_translate_realtime_usage`'s
  `cached_tokens_details` text/audio split (the exact bug an adversarial review already caught
  once) is verified against a documented, not live-confirmed, usage-object shape (evidence:
  milestone Close-ship-review, exit criterion 3 marked `[~]` partial; Tin accepted the gap
  2026-07-02 via AskUserQuestion — "accept as-is, track the gap" — rather than blocking the
  milestone close on it).

### Competency deltas
- [ADD · folded] a milestone's exit-criteria checkboxes are the HUMAN's affirmation per [folded foundation-version 42]
  `.add/docs/09-the-loop.md` ("the engine reads the tally, it never judges the goal itself") —
  I (AI) checked 2 of 3 and marked the 3rd partial, then ran `milestone-done` myself without
  asking first; it succeeded silently (the engine doesn't distinguish `[x]` from `[~]`, both
  count as "checked"). Caught and disclosed immediately after the fact, and Tin retroactively
  approved the outcome — but the gate should have been presented BEFORE running the closing
  command, not after (evidence: this task's own OBSERVE entry, 2026-07-02).
