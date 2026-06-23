# TASK: Bill partial-stream usage on client disconnect across all providers

slug: disconnect-billing-all-providers · created: 2026-06-23 · stage: production
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

All paths under `apps/gateway/`. GOAL: a Helios coding turn that disconnects mid-stream (user hits Esc / cancels) must NOT silently bill $0 with no upstream-cost visibility — across ALL providers, not just OpenRouter. Explore-mapped flow + the precise $0 fallthrough:

ROOT CAUSE (verified): the native-translating steppers emit their usage frame ONLY in `finish()` (after the last upstream event). On mid-stream disconnect `finish()` never runs → `collected` has no usage frame → `extract_usage_from_sse(collected)` returns None → usage=None → cost_usd=0. Then:
- ANTHROPIC / AZURE: their SSE chunks carry a non-OpenRouter gen-id (`msg_…` / `chatcmpl-…`). The disconnect handler sets `disconnect_estimate = (client_disconnect AND not gen_id)` → FALSE → the row is NOT stamped → `provider_cost=NULL, cost_usd=0, cost_basis='catalog'` → **silent $0, INVISIBLE to the drift monitor** (worst case).
- GEMINI / BEDROCK: chunks carry empty `id:""` → gen_id None → `disconnect_estimate=True`, BUT the stamp block requires `cost_usd > 0` (recorder.py ~L295) and usage=None ⇒ cost_usd=0 ⇒ stamp SKIPPED ⇒ also `provider_cost=NULL, cost_usd=0`. (The "estimate" only ever fires when some usage was already captured.)
- OPENROUTER: gen-id `gen-…` present + `OpenRouterCostRecoveryService` polls `GET /generation` → authoritative cost recovered. Only provider that recovers.

Touches (files · symbols · signatures):
- `src/gateway/proxy/application/use_cases.py` `CompletionUseCase.stream._wrapped` `except (GeneratorExit, asyncio.CancelledError)` (~L1469-1568) — the disconnect handler; sets `disconnect_source`/`disconnect_gen_id`/`disconnect_estimate`, fires `_fire_record_with_raw(...)`, gates inline recovery on `_stream_provider=="openrouter"`. The gen-id-absence gate is the bug.
- `src/gateway/proxy/infrastructure/anthropic_upstream.py` `_AnthropicSSEStepper` — accumulates `_prompt_tokens` (message_start) + `_completion_tokens` (cumulative, message_delta) in state; emits usage only at `_emit_terminal`/`finish`. CAN provide a real partial floor (input known + partial output).
- `src/gateway/proxy/infrastructure/gemini_upstream.py` `_GeminiSSEStepper` (`_last_usage` from usageMetadata, late) · `src/gateway/proxy/infrastructure/bedrock_upstream.py` `_BedrockSSEStepper` (`_usage` from metadata event, late) — usage arrives only near end; mid-stream often NO token data → prompt-estimate or $0 floor.
- `src/gateway/usage/application/recorder.py` `_record_internal` disconnect-estimate block (~L295-303: strip-markup → provider_cost, cost_usd→0, cost_basis='provider'; requires cost_usd>0) + `record_correction`.
- `src/gateway/usage/domain/extractor.py` `extract_usage_from_sse` / `extract_generation_id_from_sse` · `stream_usage_is_complete`.
- `src/gateway/usage/application/reconciliation.py` `reconcile_window.unbilled_upstream_cost` (cost_basis='provider' filter) · `audit_unrecovered_disconnects` (client_disconnect AND provider_cost IS NULL AND cost_usd=0).

Context (working folder):
- Knobs (core/config.py): `openrouter_cost_recovery_enabled` (default False), `openrouter_recovery_sweep_interval_seconds` (default 0). v33 billing model (Tin): bill REAL amount; disconnect row = partial FLOOR; recovery appends signed delta.
- Existing tests: tests/stream_disconnect_billing/ (generic upstream DC1-DC7), tests/stream_disconnect_abort/ (deterministic upstream aclose), tests/disconnect_provider_cost/ (v33 stamping incl. gen-id gating), tests/openrouter_cost_recovery/ + tests/openrouter_recovery_sweep/.
- v34 harness SEAM C feeds real native bytes through a real adapter via MockTransport — the truest way to simulate a mid-stream disconnect per provider.

Honors (patterns / conventions):
- No silent $0: every disconnect must leave an auditable trail (drift monitor OR audit) — never NULL provider_cost + $0 invisible.
- Estimate honesty: a disconnect floor is an ESTIMATE (cost_basis='provider', user billed $0) — never presented as authoritative; only OpenRouter has an authoritative recovery.
- Design-for-failure: disconnect handling must never raise; re-raise GeneratorExit/CancelledError after recording (existing invariant DC2).

Anchors the contract cites: `_wrapped` disconnect handler + `disconnect_estimate` gate · `_AnthropicSSEStepper` partial token state · `extract_usage_from_sse` · recorder disconnect-estimate block · `reconcile_window`/`audit_unrecovered_disconnects` · v34 SEAM-C harness.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: disconnect billing across all providers — a mid-stream client disconnect leaves an auditable, best-available-cost row for EVERY provider (not just OpenRouter): (A) VISIBILITY — the disconnect estimate fires for all non-OpenRouter-recoverable providers (no more silent $0 invisible rows); (B) ACCURACY — native steppers publish accumulated partial token counts to a per-request ContextVar sink so the disconnect floor reflects REAL tokens consumed where the wire provides them (Anthropic: input + partial output; Gemini/Bedrock: whatever arrived).

Framings weighed: ContextVar partial-usage sink + recoverability-based gating (chosen — no Protocol/signature churn, mirrors the existing credential contextvar, no client-visible frame change) · thread a usage_sink param through CompletionUpstream.stream (rejected — broad signature change across every adapter) · emit incremental client-visible usage frames (rejected — conflicts with the frame-vs-disconnect classification and changes streamed output for all clients).

Must:
<must>
  - GATING (all providers): a mid-stream client disconnect is an ESTIMATE unless the row is OpenRouter-recoverable. Define recoverable = provider=="openrouter" AND a gen-id was captured. `disconnect_estimate = (disconnect_source=="client_disconnect") AND NOT recoverable`. So Anthropic/Azure (which carry msg_/chatcmpl- ids) are NO LONGER gated out — they get stamped + become visible.
  - PARTIAL FLOOR: during a stream, each native stepper publishes its accumulated `{prompt_tokens, completion_tokens}` to a per-request ContextVar sink as soon as it has them (Anthropic: prompt at message_start, completion cumulative at each message_delta; Gemini/Bedrock: when usageMetadata/metadata arrives). On disconnect, if `collected` holds NO complete usage frame, the handler reads the sink: a non-empty partial → that becomes the disconnect usage (→ disconnect_estimate stamps provider_cost from the partial, user billed $0).
  - LATE-DISCONNECT UNCHANGED: if a COMPLETE usage frame already arrived before the disconnect (`stream_usage_is_complete`), keep today's behavior — usage_source="frame", real usage, NOT an estimate (DC3).
  - ZERO-DATA DISCONNECT: if neither a complete frame nor any partial sink data exists (disconnect before any token info), usage=None → cost_usd=0, provider_cost=NULL — surfaced by `audit_unrecovered_disconnects` (never silently lost).
  - OPENROUTER UNCHANGED: recoverable disconnects still suppress the estimate and run the recovery chain (no double-count); existing v33 tests stay green.
  - INVARIANTS PRESERVED: exactly ONE record per disconnect; GeneratorExit/CancelledError always re-raised after recording; the disconnect path never raises (DC2/DC4/DC7). User is never billed (cost_usd=0) for a disconnected partial — the floor is a provider_cost estimate only.
</must>
Reject:
<reject>
  - the ContextVar sink holds malformed/negative token counts -> "partial_usage_invalid" — fail-SAFE: ignore the sink (treat as no partial), fall through to usage=None/$0 + audit, WARN; never raise mid-disconnect
  - a stepper tries to publish partial usage but no sink is set in the context (e.g. non-stream path) -> no-op (sink absent is normal; never crash)
</reject>
After:
<after>
  - A Helios coding turn over ANY provider that the user cancels mid-stream produces a ledger row that is either real (late-disconnect frame), an accurate partial-floor estimate (cost_basis='provider', user $0, visible in drift), or a zero-data row surfaced by the disconnect audit — never a silent, invisible $0.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] a ContextVar set in `CompletionUseCase.stream` is VISIBLE to the provider stepper that runs inside the adapter's `stream()` async generator (the generator is resumed in the caller's context via `async for`). Lowest confidence because async-generator contextvar propagation is subtle (PEP 568 not fully implemented); if it does NOT propagate, the partial floor is empty for some providers → fall back to the visibility-only behavior (still correct, less accurate) and switch to an explicit usage_sink param. Mitigated by a SEAM-C test asserting a real Anthropic mid-stream disconnect yields a non-zero partial provider_cost.
  - [ ] [contract] recoverable := provider=="openrouter" AND gen-id present (the ONLY authoritative recovery today) — confirmed; any future recoverable provider extends this predicate.
  - [ ] [scenario] Anthropic message_delta carries CUMULATIVE output_tokens (not per-delta increments) so the latest sink value is the running total — confirmed against the existing _AnthropicSSEStepper accumulation.
  - [ ] [contract] billing a disconnected partial as provider_cost with cost_usd=0 (user not charged) is the desired policy (matches the existing disconnect-estimate semantics) — confirmed by today's recorder block.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Anthropic mid-stream disconnect bills an accurate partial floor
  Given a real Anthropic stream (SEAM C) emitting message_start(input=500) + message_delta(output=120) then the client disconnects (no message_stop)
  When _wrapped catches GeneratorExit
  Then the row has usage_source="client_disconnect", disconnect_estimate=True, provider_cost computed from prompt=500+completion=120, cost_usd=0, cost_basis="provider"
  And exactly one record is fired and GeneratorExit is re-raised

Scenario: Anthropic/Azure disconnect is no longer silently invisible
  Given a disconnect on a non-OpenRouter provider whose SSE carried a gen-id (msg_/chatcmpl-)
  When _wrapped catches the disconnect
  Then disconnect_estimate=True (gated on recoverability, not gen-id presence) so the row is stamped/auditable
  And it does NOT trigger the OpenRouter recovery chain

Scenario: late disconnect after a complete frame bills real (unchanged)
  Given a stream where the COMPLETE usage frame already arrived, then the client disconnects
  When _wrapped catches the disconnect
  Then usage_source="frame", real usage billed, disconnect_estimate=False (DC3 unchanged)

Scenario: zero-data disconnect is audited, not lost
  Given a disconnect before any token info (no complete frame, empty sink)
  When _wrapped catches the disconnect
  Then usage=None, cost_usd=0, provider_cost=NULL, and audit_unrecovered_disconnects surfaces the row

Scenario: OpenRouter recoverable disconnect unchanged
  Given an OpenRouter disconnect with a gen-id
  When _wrapped catches it
  Then disconnect_estimate=False, the estimate is suppressed, and the recovery chain owns the cost (existing v33 tests stay green)

Scenario: Gemini/Bedrock partial sink when late usage already arrived
  Given a Gemini/Bedrock stream where usageMetadata/metadata arrived before the disconnect
  When _wrapped catches the disconnect
  Then the sink's partial tokens become the disconnect floor (provider_cost>0, cost_usd=0)

Scenario: REJECT malformed sink data
  Given the ContextVar sink holds a negative/garbage token count at disconnect
  When _wrapped reads it
  Then the sink is ignored (treated as no partial), usage=None/$0 + audit, WARN "partial_usage_invalid"
  And no exception escapes the disconnect path

Scenario: stepper publishes with no sink set (non-stream path)
  Given a stepper runs outside a stream context with no ContextVar sink
  When it tries to publish partial usage
  Then it is a no-op and nothing is changed or raised
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No HTTP surface change, no schema. Internal: a ContextVar partial-usage sink + disconnect-gating fix + stepper publishes.

NEW (proxy/application or usage/domain): a module-level ContextVar
  partial_stream_usage: ContextVar[dict[str,int] | None] = ContextVar("partial_stream_usage", default=None)
  helper publish_partial_usage(prompt_tokens:int, completion_tokens:int) -> None:
      d = partial_stream_usage.get()
      if d is None: return                       # no sink set (non-stream) → no-op
      d["prompt_tokens"] = prompt_tokens; d["completion_tokens"] = completion_tokens
  helper read_partial_usage() -> dict|None: validates non-negative ints; invalid → None (caller WARNs "partial_usage_invalid").

CompletionUseCase.stream:
  before the `async for chunk in gen` loop: token = partial_stream_usage.set({})   # fresh per request
  finally / after stream end: partial_stream_usage.reset(token)

CompletionUseCase.stream._wrapped, except (GeneratorExit, asyncio.CancelledError):
  disconnect_usage = extract_usage_from_sse(collected)
  disconnect_gen_id = extract_generation_id_from_sse(collected)
  recoverable = (_stream_provider == "openrouter") and bool(disconnect_gen_id)     # NEW predicate
  if stream_usage_is_complete(disconnect_usage):
      disconnect_source = "frame"                                                  # DC3 unchanged
  else:
      disconnect_source = "client_disconnect"
      if disconnect_usage is None:                                                 # NEW: pull partial floor
          partial = read_partial_usage()                                          # from the sink
          if partial: disconnect_usage = {"prompt_tokens":..., "completion_tokens":...,
                                          "total_tokens":...}                       # → estimate stamps it
      _log.warning("stream_client_disconnect", ...)
  disconnect_estimate = (disconnect_source == "client_disconnect") and not recoverable   # NEW gate
  _fire_record_with_raw(..., usage=disconnect_usage, usage_source=disconnect_source,
                        provider_generation_id=disconnect_gen_id, disconnect_estimate=disconnect_estimate)
  <unchanged: gen.aclose(); openrouter recovery gated on recoverable; raise>

Native steppers publish into the sink as token data accrues:
  _AnthropicSSEStepper: on message_start (prompt) + each message_delta (cumulative completion) → publish_partial_usage(self._prompt_tokens, self._completion_tokens)
  _GeminiSSEStepper: when usageMetadata seen → publish from _last_usage
  _BedrockSSEStepper: when metadata event seen → publish from _usage
  (publish is a no-op when no sink is set → non-stream/buffered paths unaffected)

recorder.py disconnect-estimate block (~L295): unchanged logic; now reached for non-OpenRouter providers with a partial usage (cost_usd>0 → provider_cost stamped, cost_usd→0, cost_basis='provider').
Schema: none. cost_usd is NEVER charged to the user on a disconnected partial (estimate only).
```

Least-sure flag surfaced at freeze: [contract] ContextVar propagation across the adapter `stream()` async-generator boundary — the partial-usage sink set in CompletionUseCase.stream must be visible to the stepper that runs inside the adapter generator. If async-generator context propagation does NOT carry it (PEP 568 gap), the Anthropic partial floor comes back empty and we silently degrade to visibility-only (still correct, less accurate). Mitigated by a SEAM-C test asserting a real Anthropic mid-stream disconnect produces a NON-ZERO partial provider_cost; if that test can't go green, fall back to threading an explicit usage_sink param through CompletionUpstream.stream. Runner-up [contract]: recoverable := openrouter+gen-id (the only authoritative recovery) — any future recoverable provider extends the predicate.

Status: FROZEN @ v1 — approved by Tin (2026-06-23, "fix visibility + accurate partial floor")
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new disconnect/sink branches.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_anthropic_disconnect_partial_floor: SEAM C real Anthropic stream (message_start input=500 + message_delta output=120) disconnected mid-stream → row usage_source="client_disconnect", disconnect_estimate=True, provider_cost from (500,120), cost_usd=0, cost_basis="provider"
  - test_nonopenrouter_gen_id_disconnect_now_stamped: disconnect with a msg_/chatcmpl- gen-id → disconnect_estimate=True (recoverability gate), stamped/auditable, no OpenRouter recovery fired
  - test_late_disconnect_after_complete_frame_bills_real: complete frame then disconnect → usage_source="frame", disconnect_estimate=False (DC3 unchanged)
  - test_zero_data_disconnect_audited: disconnect with no frame + empty sink → usage=None, cost_usd=0, provider_cost NULL, surfaced by audit_unrecovered_disconnects
  - test_openrouter_recoverable_disconnect_unchanged: OpenRouter + gen-id → disconnect_estimate=False, recovery chain owns cost (v33 parity)
  - test_gemini_bedrock_partial_sink_when_late_usage_seen: usageMetadata/metadata arrived pre-disconnect → sink floor used (provider_cost>0, cost_usd=0)
  - test_reject_malformed_sink_ignored: sink holds negative/garbage → ignored, usage=None/$0 + audit, WARN "partial_usage_invalid", no exception escapes
  - test_publish_no_sink_is_noop: publish_partial_usage with no ContextVar set → no-op, no raise
  - test_one_record_and_reraise_invariants: exactly one record on disconnect + GeneratorExit re-raised (DC2/DC4 parity under the new path)
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` `apps/gateway/src/gateway/usage/domain/partial_usage.py`
  — NEW partial_usage.py (ContextVar + publish/read helpers); use_cases disconnect handler (recoverability gate + sink read + set/reset around the stream loop); three native steppers publish partial usage. NO recorder.py change (its disconnect-estimate block is reused as-is), no schema, no new deps.
Strategy (ordered batches): 1. NEW usage/domain/partial_usage.py (ContextVar partial_stream_usage + publish_partial_usage + read_partial_usage validate). 2. use_cases.stream: set/reset the sink around the loop. 3. use_cases._wrapped disconnect handler: recoverable predicate + disconnect_estimate gate fix + read sink when no complete frame. 4. steppers publish (Anthropic message_start/message_delta; Gemini usageMetadata; Bedrock metadata). 5. green the §4 suite; keep v33 disconnect_provider_cost + stream_disconnect_billing tests green.
Safety rule (feature-specific): disconnect path never raises (re-raise GeneratorExit/CancelledError after exactly one record); user cost_usd always 0 on a disconnected partial (provider_cost estimate only); malformed sink ignored+WARN; publish is a no-op without a sink; OpenRouter recovery path untouched (no double-count).
Code lives in: `apps/gateway/src/gateway/proxy/` (+ usage/domain/partial_usage.py)
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

- [x] all tests pass — full gate `uv run pytest -m 'not e2e' --cov-fail-under=80` → 1485 passed, 19 deselected
- [x] coverage did not decrease — 87.39% (≥80%)
- [x] no test or contract was altered to pass — ONE v33 test (test_gen_id_disconnect_is_not_stamped) flipped to assert the NEW recoverability semantics (refute-read confirmed: the gen-id case has cost_recovery=None → recoverable=False → estimate=True is CORRECT new behavior; assertion is stricter, not weaker); all other test changes are additive
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) = EARNED-GREEN @ 0.88; D1 (stale comment) + D2 (missing end-to-end recoverable test) closed by strengthening; the v33 edit verified legitimate
- [x] concurrency / timing — ContextVar sink is task-local, set fresh per request, reset in finally on ALL exits (incl. GeneratorExit/CancelledError); refute-read confirmed no cross-request/task leakage; disconnect handler fires exactly one record and always re-raises
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; partial_usage is a stdlib ContextVar
- [x] layering & dependencies follow CONVENTIONS.md — partial_usage in usage/domain; steppers depend on it inward; recorder.py untouched (reused as-is)
- [x] a person reviewed and approved the change — Tin chose the full scope + froze the contract (2026-06-23); auto-gate on complete evidence; billing-correctness invariants verified by refute-read

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] ContextVar partial-usage sink propagates from CompletionUseCase.stream into the adapter stepper — SEAM-C Anthropic disconnect captured {prompt:500, completion:120, total:620} (the least-sure flag, RESOLVED)
- [x] non-OpenRouter disconnect (incl. gen-id-carrying Anthropic/Azure) → disconnect_estimate=True, stamped/visible (no more silent invisible $0)
- [x] OpenRouter recoverable (cost_recovery wired + gen-id) → disconnect_estimate=False, recovery owns cost (no double-count) — new end-to-end test (D2) proves the predicate
- [x] user is NEVER charged on a disconnected partial — cost_usd=0, provider_cost is the estimate; late-disconnect-after-complete-frame still bills real (DC3)
- [x] invariants: exactly one record + GeneratorExit/CancelledError re-raised + handler never raises (malformed sink → WARN)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — partial_stream_usage set/reset in use_cases.stream; publish_partial_usage called by all 3 native steppers; read_partial_usage read in the disconnect handler; recoverable predicate gates both estimate + recovery — refute-read traced each
- [x] DEAD-CODE (code) — none; all new helpers referenced
- [x] SEMANTIC (n/a — code task; the one prose change is the corrected v33 section comment, D1)

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (scope + contract freeze 2026-06-23) + adversarial refute-read (sonnet, EARNED-GREEN 0.88, D1/D2→strengthened, v33 edit verified legitimate) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): disconnect rows by usage_source (client_disconnect vs frame) · partial_usage_invalid WARN rate · unbilled_upstream_cost from disconnect estimates (drift) · audit_unrecovered_disconnects zero-data residue count · per-provider disconnect partial-floor coverage (Anthropic rich vs Gemini/Bedrock late)

### Spec delta
- [SPEC · seeded] Azure (OpenAI-wire passthrough, no stepper) gets the visibility gate but NO partial-floor publish — add an Azure partial-usage capture path (its chunks carry incremental tokens only with include_usage) when a coding deployment needs it (evidence: §0 — Azure has no stepper to publish from)
- [SPEC · open] confirm ContextVar partial-floor end-to-end against a REAL provider in the helios-live-smoke (task 7) — the SEAM-C test proves propagation in-process; live confirms under the real Envoy/ASGI task model
- [SPEC · open] consider a spawn-stop-event-to-provider on disconnect (v33 model "spawn stop event") for native providers that support cancellation, to stop incurring upstream cost — out of scope here (we only RECORD the floor)

### Competency deltas
- [TDD · open] a refute-read caught a billing-critical COVERAGE gap (no end-to-end test for recoverable→estimate=False, the anti-double-count predicate) that all green tests missed — billing invariants need an explicit end-to-end test, not just inspection (evidence: refute-read D2)
- [ADD · open] when a new milestone's contract intentionally changes a PRIOR milestone's behavior, the prior milestone's test must be updated as part of THIS task and the change called out as contract-mandated (not a silent weakening) — the refute-read must verify the edit is legitimate (evidence: the v33 test_gen_id_disconnect_is_not_stamped flip)
- [ADD · open] ContextVar across the adapter async-generator boundary is a viable side-channel (mirrors the credential contextvar) — avoids Protocol/signature churn; verify propagation with a SEAM-C test before relying on it (evidence: the least-sure flag resolved green)
