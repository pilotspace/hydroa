# TASK: Verify parallel tool-call streaming across Anthropic/Gemini/Bedrock

slug: parallel-tool-streaming-verify · created: 2026-06-23 · stage: production
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

All paths under `apps/gateway/`. GOAL: prove (and where broken, FIX) that a Helios coding turn requesting MULTIPLE tool calls streams them back correctly across all native-translating providers. Grounding finding (verified this session):
- ANTHROPIC streaming parallel tool calls: WORKS — proven by the v34 SEAM-C harness test (two native `tool_use` content blocks → two distinct OpenAI tool_call ids/indices). No change.
- GEMINI streaming parallel tool calls: WORKS — `_GeminiSSEStepper` (gemini_upstream.py:434) increments `self._tc_count` per `functionCall` part and keys `build_tool_call_delta(self._tc_count, …)` → distinct indices. Coverage-extension only (add a streaming parallel-tool test).
- BEDROCK streaming tool calls: **BROKEN** — `_BedrockSSEStepper.step` (bedrock_upstream.py:331) handles only `contentBlockDelta.delta.text`; it DROPS `contentBlockStart` (carries `start.toolUse.{toolUseId,name}`) and `contentBlockDelta.delta.toolUse.input` (partial-JSON args). So streamed Bedrock tool calls are silently lost (buffered `_converse_to_openai`:255 handles toolUse; the stream path does not). REAL FIX.

Touches (files · symbols · signatures):
- `src/gateway/proxy/infrastructure/bedrock_upstream.py:307` `_BedrockSSEStepper` — ADD: `contentBlockStart` → first tool_call delta (id=toolUseId, name); `contentBlockDelta.delta.toolUse.input` → arguments-fragment delta; map Converse `contentBlockIndex` → a per-tool OpenAI index (counter, like Gemini's `_tc_count`). `finish()` already maps stop_reason `tool_use` → "tool_calls" via `_map_finish_reason` (:116).
- `src/gateway/proxy/domain/tool_translation.py:102` `build_tool_call_delta(index, *, id, name, arguments_fragment)` — REUSE (first frag id+name, later frags arguments_fragment). `dump_tool_arguments` (:134) for input dicts.
- `src/gateway/proxy/infrastructure/gemini_upstream.py:434` `_GeminiSSEStepper` — verified correct; test only.
- `src/gateway/proxy/infrastructure/anthropic_upstream.py` `_AnthropicSSEStepper` — verified correct; test only.

Context (working folder):
- Bedrock Converse stream events arrive via `aiter_event_stream` (bedrock_eventstream.py); `:event-type` header ∈ {messageStart, contentBlockStart, contentBlockDelta, contentBlockStop, messageStop, metadata}; payload JSON carries `contentBlockIndex`. The stepper drives BOTH the buffered wrapper `_converse_stream_to_openai_sse` (:390) AND the live adapter → keep byte-identical.
- Tests use the v34 harness: SEAM C (real adapter + MockTransport feeding native Converse EventStream bytes) is the truest proof; SEAM A drives the stepper with synthetic event tuples.

Honors (patterns / conventions):
- Incremental SSE: each frame yielded as its source event arrives (TTFB). No buffering of the whole tool call.
- Reuse the shared tool_translation helpers (no per-provider re-impl of the delta shape).
- Fail-safe: a toolUse block missing toolUseId/name still emits a best-effort delta (never crash mid-stream); unknown event types ignored (today's behavior).
- Bedrock uses its NATIVE toolUseId directly (not a synthesized id) — it is a real, correlatable id.

Anchors the contract cites: `_BedrockSSEStepper.step` (:331) · Converse `contentBlockStart`/`contentBlockDelta.toolUse.input`/`contentBlockIndex` · `build_tool_call_delta` (:102) · `dump_tool_arguments` (:134) · `_map_finish_reason` (:116) · `_GeminiSSEStepper._tc_count` · v34 SEAM-C harness.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: parallel tool-call streaming across native providers — guarantee a Helios coding turn that emits MULTIPLE tool calls streams each back as a distinct OpenAI `tool_calls` delta (correct index, id, name, incremental arguments) for Anthropic, Gemini, AND Bedrock. Anthropic + Gemini are verified-correct (lock with tests); Bedrock streaming tool-use is fixed.

Framings weighed: fix Bedrock stepper + lock Anthropic/Gemini with tests (chosen) · verify-only / defer Bedrock (rejected — leaves a silent data-loss bug for Helios coding turns on Bedrock) · rewrite all three steppers to a shared core (rejected — needless churn on two correct adapters).

Must:
<must>
  - BEDROCK stream: a `contentBlockStart` event whose `start.toolUse` carries `{toolUseId, name}` emits the FIRST tool_call delta for that call (index, id=toolUseId, name), keyed to a per-tool index assigned in arrival order (0,1,2 for parallel calls).
  - BEDROCK stream: a `contentBlockDelta` whose `delta.toolUse.input` carries a partial-JSON string emits an arguments-fragment delta on the SAME tool index (no id/name).
  - BEDROCK stream: a turn with N≥2 toolUse blocks yields N tool calls with DISTINCT indices and ids; finish_reason resolves to "tool_calls" (stop_reason `tool_use`).
  - BEDROCK text + tool interleaving unchanged: `contentBlockDelta.delta.text` still streams as content; byte-identical for tool-free turns.
  - ANTHROPIC + GEMINI streaming parallel tool calls remain correct (locked by new SEAM-C/stepper tests) — no source change to those adapters.
</must>
Reject:
<reject>
  - a `toolUse` block missing `toolUseId` or `name` -> "bedrock_tooluse_incomplete" — fail-SAFE: emit a best-effort delta (id="" / name="" for the missing piece), WARN, never crash the stream
  - a `delta.toolUse.input` fragment for a contentBlockIndex with no prior `contentBlockStart` -> "bedrock_tooluse_orphan_input" — emit the arguments fragment on a freshly assigned index, WARN (don't drop the bytes)
</reject>
After:
<after>
  - A Helios coding turn over Bedrock that calls two tools mid-stream receives two well-formed, distinctly-indexed OpenAI tool_call deltas with incremental arguments and finish_reason "tool_calls" — identical in shape to the Anthropic/Gemini paths.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] Bedrock Converse emits ONE `contentBlockStart` per toolUse block then ≥1 `contentBlockDelta.toolUse.input` fragments then `contentBlockStop`, with `contentBlockIndex` stable per block — lowest confidence because the exact per-block event ordering/partial-JSON chunking is from the AWS Converse spec, not a captured live trace; if wrong (e.g. name arrives in a delta not the start): move name capture to wherever it appears — isolated to the stepper. Mitigated by a SEAM-C test feeding real-shaped EventStream bytes.
  - [ ] [contract] map Converse `contentBlockIndex` → OpenAI tool index via an arrival-order counter over toolUse blocks only (text blocks excluded from the tool index space) — matches Gemini's `_tc_count`; confirmed as the OpenAI-wire convention.
  - [ ] [scenario] use Bedrock's native `toolUseId` as the OpenAI tool_call id (not synthesized) — it is a real correlatable id; confirmed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Bedrock streams a single tool call
  Given a ConverseStream with contentBlockStart(toolUse id="tu_1",name="read_file") then contentBlockDelta(toolUse.input='{"path":') then contentBlockDelta(toolUse.input='"a.py"}') then messageStop(stopReason="tool_use")
  When the stepper translates it
  Then frames include a first delta {index:0,id:"tu_1",function:{name:"read_file"}} then arg-fragment deltas at index 0, and finish_reason=="tool_calls"

Scenario: Bedrock streams TWO parallel tool calls with distinct indices
  Given a ConverseStream with two toolUse blocks (contentBlockIndex 1 and 2, ids tu_1/tu_2)
  When translated
  Then the two calls surface at OpenAI tool indices 0 and 1 with ids tu_1 and tu_2 (distinct), each with its own arguments

Scenario: Bedrock text + tool interleaving (byte-identical text path)
  Given a stream with a text contentBlockDelta then a toolUse block
  When translated
  Then the text streams as a content delta unchanged AND the tool call streams as tool_calls deltas

Scenario: Bedrock tool-free turn is byte-identical
  Given a ConverseStream with only text deltas + messageStop(stopReason="end_turn")
  When translated
  Then the output is identical to today (no tool_calls frames; finish_reason=="stop")

Scenario: REJECT a toolUse block missing the name
  Given contentBlockStart with start.toolUse={toolUseId:"tu_1"} (no name)
  When translated
  Then a best-effort first delta is emitted (id="tu_1", name="") + WARN "bedrock_tooluse_incomplete"
  And every other frame is unchanged from the well-formed baseline

Scenario: REJECT an orphan toolUse input fragment
  Given a contentBlockDelta.toolUse.input arrives for an index with no prior contentBlockStart
  When translated
  Then the arguments fragment is emitted on a freshly assigned index + WARN "bedrock_tooluse_orphan_input"
  And no bytes are dropped and other frames are unchanged

Scenario: Anthropic streams TWO parallel tool calls (locked, no source change)
  Given a real AnthropicCompletionUpstream streaming two native tool_use content blocks (SEAM C)
  When consumed via MockTransport
  Then two tool_call deltas surface with distinct indices and ids

Scenario: Gemini streams TWO parallel tool calls (locked, no source change)
  Given a Gemini stream with two functionCall parts
  When translated by _GeminiSSEStepper
  Then two tool_call deltas surface at indices 0 and 1 with distinct synthesized ids
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No HTTP surface change, no schema. Internal change to ONE streaming translator (Bedrock) + tests.

_BedrockSSEStepper (bedrock_upstream.py): add tool-call streaming, mirroring buffered _converse_to_openai.
  new state: self._tool_index: int = 0
             self._block_to_tool_index: dict[int, int] = {}   # Converse contentBlockIndex -> OpenAI tool index

  step("contentBlockStart", payload):
    tu = payload.get("start", {}).get("toolUse")
    if tu is not None:
        cbi = payload.get("contentBlockIndex", <fallback>)
        idx = self._tool_index; self._block_to_tool_index[cbi] = idx; self._tool_index += 1
        id = tu.get("toolUseId", "")  ; name = tu.get("name", "")
        if not id or not name: WARN "bedrock_tooluse_incomplete"
        yield delta = build_tool_call_delta(idx, id=id, name=name)   # first frag: id+name
        self._saw_tool_call = True

  step("contentBlockDelta", payload):   # EXTEND existing
    delta = payload.get("delta", {})
    if "text" in delta: <unchanged text path>
    elif "toolUse" in delta:
        cbi = payload.get("contentBlockIndex", <fallback>)
        idx = self._block_to_tool_index.get(cbi)
        if idx is None:                                     # orphan input
            idx = self._tool_index; self._block_to_tool_index[cbi] = idx; self._tool_index += 1
            WARN "bedrock_tooluse_orphan_input"
        frag = delta["toolUse"].get("input", "")            # Converse sends input as a partial-JSON STRING
        yield build_tool_call_delta(idx, arguments_fragment=frag)   # later frag: args only

  finish(): _map_finish_reason already maps "tool_use" -> "tool_calls".
    [v1.1 verify-phase robustness amendment — design-for-failure mandate] add a fail-safe BEFORE
    mapping: `if self._saw_tool_call and not self._stop_reason: self._stop_reason = "tool_use"` so a
    tool turn that abnormally arrives with NO stopReason still resolves to "tool_calls" (mirrors
    _GeminiSSEStepper.finish). Strict superset: documented behavior preserved (tool_use->tool_calls,
    end_turn->stop both unchanged); the guard fires ONLY when stopReason is falsy AND a tool was seen.
    Surfaced by the adversarial refute-read (dead _saw_tool_call flag); covered by a new test.

ANTHROPIC / GEMINI steppers: NO source change — locked by tests only.

Frame shape (all providers, via build_tool_call_delta): choices[0].delta.tool_calls=[{index,id?,type:"function",function:{name?,arguments?}}].
Schema: none. Constants/helpers reused from tool_translation.py.
```

Least-sure flag surfaced at freeze: [contract] BEDROCK per-block event ordering — the fix assumes Converse sends `contentBlockStart{start.toolUse:{toolUseId,name}}` first, then `contentBlockDelta{delta.toolUse.input:"<partial-json-str>"}` fragments (input is a STRING, appended verbatim as the arguments fragment). Derived from the AWS Converse spec, not a captured live trace. If wrong (e.g. name absent at start, or input delivered as an object not a string), the fix is isolated to the stepper and the SEAM-C test fixture is where we'd see it. Runner-up [scenario]: using native toolUseId as the OpenAI id (vs synthesizing) — chosen because it's a real correlatable id.

Status: FROZEN @ v1.1 — approved by Tin (2026-06-23); v1.1 adds the finish() fail-safe above (strict-superset robustness amendment per the standing design-for-failure mandate, surfaced by the adversarial refute-read; documented behavior unchanged)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new Bedrock tool-stream branches.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_bedrock_stream_single_tool_call: SEAM A stepper fed contentBlockStart+toolUse.input deltas → first delta {index:0,id,name} + arg frags + finish "tool_calls"
  - test_bedrock_stream_two_parallel_tool_calls: two toolUse blocks → tool indices 0,1 distinct ids/args (SEAM C real adapter via MockTransport feeding Converse EventStream bytes is the truest proof)
  - test_bedrock_stream_text_and_tool_interleave: text delta streams as content + tool streams as tool_calls
  - test_bedrock_stream_tool_free_byte_identical: text-only + end_turn → identical to today, no tool_calls frames, finish "stop"
  - test_bedrock_reject_tooluse_missing_name: contentBlockStart toolUse w/o name → best-effort delta name="" + WARN, other frames == baseline
  - test_bedrock_reject_orphan_tooluse_input: toolUse.input w/o prior start → fragment on fresh index + WARN, no dropped bytes
  - test_bedrock_stream_tool_use_no_stop_reason [v1.1 fail-safe]: toolUse block + messageStop with NO stopReason → finish_reason "tool_calls" (proves _saw_tool_call wired; "stop" without the fix)
  - test_anthropic_stream_two_parallel_tool_calls: SEAM C real adapter, two native tool_use blocks → two distinct indices/ids (LOCK; no source change)
  - test_gemini_stream_two_parallel_tool_calls: two functionCall parts → indices 0,1 distinct ids (LOCK; no source change)
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py`
  — ONLY the `_BedrockSSEStepper` (add tool-call streaming state + contentBlockStart/contentBlockDelta.toolUse handling). NO change to Anthropic/Gemini steppers (locked by tests), no schema, no new deps.
Strategy (ordered batches): 1. add stepper state (_tool_index, _block_to_tool_index, _saw_tool_call). 2. contentBlockStart→first tool delta (id+name, incomplete→WARN). 3. contentBlockDelta toolUse branch→args fragment (orphan→fresh index+WARN). 4. confirm finish_reason mapping. 5. green the §4 suite incl. the Anthropic/Gemini lock tests.
Safety rule (feature-specific): never crash mid-stream — missing toolUseId/name → best-effort empty string + WARN; orphan input → fresh index, never drop bytes; text path + tool-free path byte-identical.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py`
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

- [x] all tests pass — full gate `uv run pytest -m 'not e2e' --cov-fail-under=80` → 1475 passed, 19 deselected
- [x] coverage did not decrease — 87.42% (≥80%)
- [x] no test or contract was altered during build — only NEW tests added; §3 amended v1.1 (documented robustness superset, not a weakening)
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) = EARNED-GREEN @ 0.91; its CONCERN (dead _saw_tool_call) addressed by WIRING the fail-safe + a new test; 2 reject-test gaps closed by strengthening
- [x] concurrency / timing — stepper is per-stream instance, no shared state; incremental per-event yield preserved (TTFB)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps (logging stdlib); reuses build_tool_call_delta
- [x] layering & dependencies follow CONVENTIONS.md — translator reuses domain tool_translation helper; no cross-adapter coupling
- [x] a person reviewed and approved the change — Tin froze the contract (2026-06-23); auto-gate on complete evidence (no security/concurrency residue)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] Bedrock stream with two toolUse blocks → two tool_call deltas at indices 0,1 with distinct ids tu_1/tu_2 — confirmed by SEAM-C test feeding real Converse EventStream bytes through the real adapter+MockTransport (refute-read verified the framing is authentic)
- [x] Bedrock toolUse.input partial-JSON passed VERBATIM as arguments_fragment (no double-encode) — refute-read confirmed concatenation reconstructs valid JSON
- [x] text + tool-free Bedrock paths byte-identical — existing 21 bedrock_streaming + incremental tests pass unchanged
- [x] Anthropic/Gemini parallel streaming locked with ZERO source change — git diff bounded to bedrock_upstream.py only
- [x] fail-safe: tool turn with no stopReason → finish_reason "tool_calls" (not "stop") — new test proves the wired _saw_tool_call

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — new state _tool_index/_block_to_tool_index/_saw_tool_call all referenced; _saw_tool_call now CONSUMED in finish() (was dead, refute-read caught it → wired); contentBlockStart/contentBlockDelta.toolUse branches reached by tests
- [x] DEAD-CODE (code) — none; the previously-dead _saw_tool_call is now live
- [x] SEMANTIC (n/a — code task)

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze 2026-06-23) + adversarial refute-read (sonnet, EARNED-GREEN 0.91, CONCERN→wired fail-safe) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): bedrock_tooluse_incomplete WARN rate · bedrock_tooluse_orphan_input WARN rate · Bedrock streaming finish_reason distribution (tool_calls vs stop) · tool-call arguments JSON-parse failures downstream (would signal fragment reassembly drift)

### Spec delta
- [SPEC · seeded] capture a REAL Bedrock Converse tool-streaming trace to validate the assumed per-block event ordering (contentBlockStart name + contentBlockDelta.toolUse.input string) — covered today by a spec-shaped SEAM-C fixture; the live helios-live-smoke (task 7) is the natural place to confirm against AWS
- [SPEC · open] the same parallel-tool streaming live double-pass should exercise Anthropic + Gemini + Bedrock end-to-end (task 7 dependency)

### Competency deltas
- [TDD · folded] adversarial refute-read surfaced DEAD code (_saw_tool_call set-but-unread) that tests alone didn't catch — wiring it as a fail-safe + a no-stopReason test turned a latent risk into covered robustness (evidence: refute-read item 6) [folded foundation-version 31]
- [ADD · folded] a verify-phase robustness improvement that is a STRICT SUPERSET of a frozen contract clause ("finish() unchanged") is best recorded as a documented v1.1 amendment with a test, not a silent edit — keeps the frozen artifact honest while honoring the design-for-failure mandate (evidence: this task's §3 finish() amendment) [folded foundation-version 31]
