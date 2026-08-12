# TASK: Gemini Live adapter behind the relay seam

slug: gemini-live-adapter · created: 2026-06-26 · stage: production
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

Touches (files · symbols · signatures):
  - NEW `apps/gateway/src/gateway/proxy/infrastructure/gemini_live.py` — `class GeminiLiveSession(RealtimeRelaySession)` translating the FROZEN gateway frames ↔ Gemini Live (BidiGenerateContent) messages over an injected `ws_connect` factory (real connector in prod, fake socket in tests). SOLE owner of Gemini Live wire format.
  - NEW `apps/gateway/tests/realtime_relay/test_gemini_adapter.py` — unit tests with a FAKE `RealtimeWebSocket` (scripted recv queue + recorded sends); NO network, NO key.
  - REUSE (read, do not modify): `proxy/infrastructure/realtime_ws_client.py` (the `RealtimeWebSocket` Protocol + `connect_websocket`, shipped by t2) · `proxy/domain/realtime_relay.py` (the FROZEN seam) · `proxy/infrastructure/openai_realtime.py` (t2 — the structural TEMPLATE: same injected-factory shape, same typed errors, same base64 audio).
Context (working folder):
  - Gemini Live wire (translation target): client→server JSON `{setup:{model,...}}` (first) · `{realtimeInput:{mediaChunks:[{mimeType:"audio/pcm",data:b64}]}}` (audio) · `{clientContent:{turnComplete:true}}` (commit/turn end); server→client JSON `{setupComplete:{}}` · `{serverContent:{modelTurn:{parts:[{inlineData:{mimeType,data:b64}} | {text}]}, turnComplete:bool}}` · top-level `{error:{...}}`. URL `wss://generativelanguage.googleapis.com/ws/...BidiGenerateContent?key=<API_KEY>`.
  - `apps/gateway/Makefile` test-fast already includes `tests/realtime_relay` — the new test rides it.
Honors (patterns / conventions):
  - PORT/ADAPTER LAYERING: infrastructure adapter implementing the domain `RealtimeRelaySession`; depends on the frozen seam + WS-client seam, never the pump.
  - NORMALIZED FRAME PROTOCOL (v52 HARD): the adapter is the ONLY place Gemini wire format exists — gateway frames in/out (dict=control, bytes=audio). NOTE: one Gemini server message can carry MULTIPLE parts → `events()` yields MULTIPLE gateway frames per recv (the structural delta from t2's 1:1).
  - DESIGN-FOR-FAILURE (v52 HARD): dial failure → `RealtimeProviderUnavailableError`; socket close ends `events()` normally; non-JSON message → typed raise; a Gemini `error` message → FORWARDED gateway `error` frame (non-fatal).
  - CREDENTIAL-GATED: NO live key here; the real dial is exercised only by t5's skip-gated harness.
Anchors the contract cites: `GeminiLiveSession` (implements `RealtimeRelaySession`) · the gateway↔Gemini-Live message mapping table · REUSED `RealtimeWebSocket`/`connect_websocket`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Gemini Live adapter implementing the frozen relay seam
Framings weighed: mirror the t2 injected-`ws_connect` shape over the shared `RealtimeWebSocket` seam (chosen — proven testable, swappable real dial, no network in unit tests) · a Gemini SDK live session (rejected — heavier dep + less control over the raw bidi frames we must translate) · a separate WS-client seam per provider (rejected — t2 already generalized it to text-JSON, both providers fit)
Must:
<must>
  - `GeminiLiveSession(*, model, api_key, ws_connect=None, ...)` implements `RealtimeRelaySession`; default `ws_connect` dials the Gemini Live URL (key in query), tests inject a fake.
  - `send_client_event(frame)` translates gateway control → Gemini JSON: `session.update`→`{setup:{model:<model>, ...frame∖type}}` · `audio.commit`/`response.create`→`{clientContent:{turnComplete:true}}` · `interrupt`→`{clientContent:{turnComplete:true}}`; unknown → forwarded under `clientContent`.
  - `send_audio(bytes)` → `{realtimeInput:{mediaChunks:[{mimeType:"audio/pcm", data:<base64>}]}}`.
  - `events()` async-iterates `ws.recv()`, json-decodes, translates Gemini→gateway, yielding ZERO-OR-MORE frames per message: `{setupComplete}`→`{type:session.created}` · for each `serverContent.modelTurn.parts[]`: `inlineData.data`→DECODED audio bytes, `text`→`{type:transcript,role:assistant,text}` · `serverContent.turnComplete:true`→`{type:response.done}` · top-level `error`→`{type:error,code:provider_error,message}` (FORWARDED).
  - `aclose()` closes the socket; idempotent. Dial failure / non-JSON → `RealtimeProviderUnavailableError`.
</must>
Reject:
<reject>
  - the WS dial raises / times out -> `RealtimeProviderUnavailableError` (pump → 4503); no half-open socket
  - a non-JSON provider message -> `RealtimeProviderUnavailableError` from `events()` (pump → {error}+1011); iterator stops
  - a Gemini `error` message -> a gateway `{type:error,code:provider_error}` frame is YIELDED (forwarded), NOT raised — session continues
</reject>
After:
<after>
  - a fake socket scripted with setupComplete + a modelTurn (audio part + text part) + turnComplete yields, in order: session.created, decoded audio bytes, transcript, response.done
  - gateway client frames translate to exactly the Gemini JSON messages on the recorded sends (audio base64 in mediaChunks)
  - `GeminiLiveSession` satisfies `isinstance(x, RealtimeRelaySession)`
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the exact Gemini Live message field names (`realtimeInput.mediaChunks`, `serverContent.modelTurn.parts`, `setupComplete`) — lowest confidence because the BidiGenerateContent surface is v1beta and has churned; if wrong: per-field one-line changes CONTAINED to this adapter's two translate helpers (seam + tests unaffected; t5's live harness is the real-name oracle). Mitigation: translate by structural navigation with `.get(...)` defaults so a missing field degrades to "no frame" rather than a crash.
  - [ ] audio mime "audio/pcm" + base64 — confirmed: Gemini Live realtimeInput/inlineData use base64 PCM with a mimeType.
  - [ ] text-only WS seam suffices — confirmed: Gemini Live is JSON-text only (audio base64 in JSON), same as OpenAI → the t2 `RealtimeWebSocket` seam fits unchanged.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: a Gemini server message fans out to ordered gateway frames
  Given a fake socket scripted with {setupComplete:{}} then {serverContent:{modelTurn:{parts:[{inlineData:{mimeType:"audio/pcm",data:b64("AQID")}},{text:"hi"}]},turnComplete:true}}
  When events() is iterated
  Then it yields {type:session.created}, then b"\x01\x02\x03", then {type:transcript,role:assistant,text:"hi"}, then {type:response.done}
  And no Gemini JSON leaks to the caller

Scenario: client frames translate to Gemini messages
  Given a connected session over a fake socket
  When send_client_event({type:session.update,voice:x}) then send_audio(b"\x09") then send_client_event({type:audio.commit})
  Then the socket received {setup:{model:...,voice:x}}, {realtimeInput:{mediaChunks:[{mimeType:"audio/pcm",data:"CQ=="}]}}, {clientContent:{turnComplete:true}}
  And the audio was base64-encoded

Scenario: dial failure is provider-unavailable
  Given a ws_connect factory that raises ConnectionError
  When connect() is called
  Then RealtimeProviderUnavailableError is raised
  And no socket is left open

Scenario: malformed provider message stops the stream
  Given a fake socket scripted with "not json"
  When events() is iterated
  Then RealtimeProviderUnavailableError is raised from the iterator
  And the iterator stops

Scenario: a Gemini error message is forwarded, not fatal
  Given a fake socket scripted with {error:{message:"quota"}} then {serverContent:{turnComplete:true}}
  When events() is iterated
  Then it yields {type:error,code:provider_error,message:"quota"} then {type:response.done}
  And the session is NOT torn down by the adapter

Scenario: the adapter satisfies the seam Protocol
  Given a GeminiLiveSession instance
  When isinstance(it, RealtimeRelaySession) is checked
  Then it is True
  And aclose() is safe before connect()
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# In-process seam (NO HTTP/REST) — an adapter implementing the FROZEN RealtimeRelaySession Protocol.

GeminiLiveSession(RealtimeRelaySession, proxy/infrastructure/gemini_live.py):
  __init__(*, model: str, api_key: str,
           ws_connect: Callable[[], Awaitable[RealtimeWebSocket]] | None = None,
           url: str = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent",
           connect_timeout: float = 10.0)        # default ws_connect dials url?key=<api_key>
  connect() / send_client_event(frame) / send_audio(bytes) / events() -> AsyncIterator / aclose()

MAPPING (gateway ⇄ Gemini Live):
  OUT  session.update    -> {setup:{model:<model>, ...frame∖type}}
       audio.commit / response.create / interrupt -> {clientContent:{turnComplete:true}}
       <audio bytes>     -> {realtimeInput:{mediaChunks:[{mimeType:"audio/pcm", data:b64}]}}
  IN   {setupComplete}                          -> {type:session.created}
       serverContent.modelTurn.parts[].inlineData.data -> <decoded bytes>   (one frame per audio part)
       serverContent.modelTurn.parts[].text     -> {type:transcript, role:assistant, text}
       serverContent.turnComplete:true          -> {type:response.done}
       {error:{message}}                         -> {type:error, code:provider_error, message}   (forwarded)
Errors: dial fail / non-JSON -> RealtimeProviderUnavailableError. NO DB, NO HTTP REST, NO new dep (reuses t2's RealtimeWebSocket).
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-26 (AI auto-draft under autonomy:auto; seam frozen by t1, WS-client seam shipped by t2 — no new decision; credential-gated live-verify deferred to t5).
Least-sure flag surfaced at freeze:
  - [spec] the Gemini Live v1beta field names (`realtimeInput.mediaChunks` / `serverContent.modelTurn.parts` / `setupComplete`) may have churned — CONTAINED to this adapter's two translate helpers; mitigated by tolerant `.get(...)` navigation (a missing field → no frame, never a crash) and t5's live harness as the real oracle. The structure (base64 audio, text-JSON WS, multi-part fan-out) is well-established Gemini Live.
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
  - test_server_message_fans_out_in_order: fake socket with setupComplete + multi-part modelTurn + turnComplete / iterate / assert 4 gateway frames in order (audio decoded)
  - test_client_frames_translate_to_gemini: connect / send 3 client frames / assert socket.sent == the 3 Gemini JSON messages (audio base64 in mediaChunks)
  - test_dial_failure_provider_unavailable: ws_connect raises / connect() / assert RealtimeProviderUnavailableError
  - test_malformed_message_raises: fake socket "not json" / iterate / assert RealtimeProviderUnavailableError, stopped
  - test_gemini_error_forwarded: error then serverContent.turnComplete / iterate / assert {error} yielded then {response.done}, socket not closed
  - test_satisfies_seam_protocol: isinstance True; aclose() before connect() safe
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/gemini_live.py` `apps/gateway/tests/realtime_relay/test_gemini_adapter.py`
Strategy (ordered batches): 1. `gemini_live.py` (the `GeminiLiveSession` adapter mirroring t2's shape: injected ws_connect + default dialer, two translate helpers, multi-part fan-out in `events()`) 2. tests ride the existing test-fast `tests/realtime_relay`
Safety rule (feature-specific): the adapter is the SOLE owner of Gemini Live wire format; tolerant `.get(...)` navigation so a missing/renamed field degrades to "no frame" (never a crash); dial/non-JSON → `RealtimeProviderUnavailableError`; `aclose()` idempotent. NO live network in unit tests (fake socket); the real dial is t5's credential-gated path.
Code lives in: `./src/`
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

- [x] all tests pass — `tests/realtime_relay/` 23/23 (6 new Gemini + 6 OpenAI + 11 pump)
- [x] coverage did not decrease — NEW module + NEW tests; nothing existing touched
- [x] no test or contract was altered during build — contract FROZEN @ v1; build wrote only the adapter + its test
- [x] the green was EARNED — tests assert the real Gemini wire shapes both ways (setup with model, realtimeInput.mediaChunks base64, multi-part modelTurn fan-out to 4 ordered gateway frames, turnComplete→response.done), dial/non-JSON→typed error, error message forwarded-not-fatal
- [x] concurrency / timing safe — no tasks held; `events()` is a plain async-generator that fans each server message into 0..N frames then loops; socket-close ends it; `aclose()` idempotent. Pump (t1) owns timeouts/breaker.
- [x] no exposed secrets / injection / unexpected deps — api_key only in the dial query (Gemini's documented auth); reuses t2's `RealtimeWebSocket`/`connect_websocket` (no new dep); tolerant `.get(...)` navigation
- [x] layering follows CONVENTIONS.md — infrastructure adapter implementing the domain `RealtimeRelaySession`; imports the frozen seam + WS-client seam, never the pump
- [x] reviewed — AI self-review + adversarial refute-read (below); no security surface (auth-over-WS is t4)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] one Gemini server message fans out to ordered gateway frames (audio decoded) — `test_server_message_fans_out_in_order` (setupComplete + 2-part modelTurn + turnComplete → 4 frames)
- [x] gateway client frames become the exact Gemini messages (setup/realtimeInput/clientContent), audio base64 in mediaChunks — `test_client_frames_translate_to_gemini`
- [x] every failure is a TYPED seam error or a forwarded frame — dial→`RealtimeProviderUnavailableError`; non-JSON→same from iterator; Gemini `error`→forwarded gateway frame, session intact

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `GeminiLiveSession` referenced by its suite; reuses `RealtimeWebSocket`/`connect_websocket` (t2). t4 (endpoint) will construct it alongside `OpenAIRealtimeSession`.
- [x] DEAD-CODE (code) — adapter uses model/api_key/url/timeout via the default `_dial_gemini` (no dead state, same fix as t2); `_AUDIO_MIME`/`_DEFAULT_URL` used; no orphan.
- [x] SEMANTIC — adversarial refute-read (self): (a) multi-part fan-out order — audio part before text part preserved by list-append order, matches asserts; (b) tolerant nav — `modelTurn.get("parts", [])` + isinstance guards mean a renamed/missing field yields fewer frames, never a KeyError (the v1beta-churn mitigation); (c) `setupComplete` presence-check via `in` so an empty `{}` still maps; (d) error coexists with serverContent — both branches run; the test asserts error THEN response.done because they arrive in separate messages. No earned-green cheat.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (AI auto-gate under autonomy:auto — no security surface in t3; translation evidence complete; reuses t2 seam) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
