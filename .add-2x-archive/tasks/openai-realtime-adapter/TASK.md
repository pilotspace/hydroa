# TASK: OpenAI Realtime adapter behind the relay seam

slug: openai-realtime-adapter · created: 2026-06-26 · stage: production
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
  - NEW `apps/gateway/src/gateway/proxy/infrastructure/realtime_ws_client.py` — a MINIMAL provider-WS seam shared by both adapters: `@runtime_checkable class RealtimeWebSocket(Protocol)` = `send(message: str)` · `recv() -> str` (raise on close) · `aclose()`; plus the REAL connector `connect_websocket(url, headers, *, timeout) -> RealtimeWebSocket` wrapping `websockets.connect` (already available transitively @16.0 — NO new dep). Both providers speak JSON text frames (audio is base64 INSIDE the JSON), so the seam is text-only.
  - NEW `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py` — `class OpenAIRealtimeSession(RealtimeRelaySession)` translating gateway frames ↔ OpenAI Realtime events over an INJECTED `ws_connect` factory (real connector in prod, fake socket in tests). Owns ALL OpenAI wire translation; the pump/endpoint never see OpenAI JSON.
  - NEW `apps/gateway/tests/realtime_relay/test_openai_adapter.py` — unit tests with a FAKE `RealtimeWebSocket` (scripted recv queue + recorded sends); NO network, NO key.
  - REUSE (read, do not modify): `proxy/domain/realtime_relay.py` (the FROZEN seam: `RealtimeRelaySession`, `ControlFrame`/`RelayFrame`, `RealtimeProviderUnavailableError`); `proxy/infrastructure/circuit_breaker.py` (pattern only — the pump owns the breaker, not the adapter).
Context (working folder):
  - OpenAI Realtime wire (translation target): client→server JSON `session.update` · `input_audio_buffer.append`{audio:b64} · `input_audio_buffer.commit` · `response.create` · `response.cancel`; server→client JSON `session.created` · `response.audio.delta`{delta:b64} · `response.audio_transcript.delta`{delta} · `response.done` · `error`. URL `wss://api.openai.com/v1/realtime?model=<m>`, headers `Authorization: Bearer <key>` + `OpenAI-Beta: realtime=v1`.
  - `apps/gateway/Makefile` test-fast already includes `tests/realtime_relay` (added by t1) — the new adapter test rides it.
Honors (patterns / conventions):
  - PORT/ADAPTER LAYERING: the adapter is INFRASTRUCTURE implementing the domain `RealtimeRelaySession` Protocol; it depends on the frozen seam, never the pump.
  - NORMALIZED FRAME PROTOCOL (v52 HARD): the adapter is the ONLY place OpenAI wire format exists — gateway frames in, gateway frames out (dict=control, bytes=audio).
  - DESIGN-FOR-FAILURE (v52 HARD): `connect()` is bounded + any dial failure → `RealtimeProviderUnavailableError`; a provider socket close ends `events()` normally (StopAsyncIteration), a malformed/fatal provider message → the pump's 1011 path (adapter raises); OpenAI `error` events are FORWARDED as a gateway `error` frame (non-fatal, client decides).
  - CREDENTIAL-GATED: NO live key here — the real dial path exists but is exercised only by t5's skip-gated harness; t2 proves translation against a fake socket.
Anchors the contract cites: `RealtimeWebSocket` (Protocol) · `connect_websocket` · `OpenAIRealtimeSession` (implements `RealtimeRelaySession`) · the gateway↔OpenAI frame mapping table

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenAI Realtime adapter implementing the frozen relay seam
Framings weighed: an injected `ws_connect` factory returning a minimal text-WS Protocol (chosen — testable with a fake socket, real dial swapped in for t5/prod, no network in unit tests) · the adapter calls `websockets.connect` directly (rejected — un-unit-testable without a live socket / monkeypatching) · a full provider SDK (rejected — OpenAI has no realtime SDK seam we need; raw WS + JSON is the contract)
Must:
<must>
  - `OpenAIRealtimeSession(*, model, api_key, ws_connect, ...)` implements `RealtimeRelaySession`; `ws_connect` is an awaitable factory `() -> RealtimeWebSocket` (real connector in prod, fake in tests).
  - `connect()` calls `ws_connect()` (the real connector dials `wss://api.openai.com/v1/realtime?model=<model>` with `Authorization: Bearer` + `OpenAI-Beta: realtime=v1`); stores the socket. Any dial failure → `RealtimeProviderUnavailableError`.
  - `send_client_event(frame)` translates gateway control → OpenAI JSON: `session.update`→`{type:session.update, session:<frame minus type>}` · `audio.commit`→`{type:input_audio_buffer.commit}` · `response.create`→`{type:response.create}` · `interrupt`→`{type:response.cancel}`; unknown control type → forwarded as-is under its `type` (forward-compatible).
  - `send_audio(bytes)` → `{type:input_audio_buffer.append, audio:<base64>}` sent as one text frame.
  - `events()` async-iterates `ws.recv()`, json-decodes each, translates OpenAI→gateway: `session.created`→`{type:session.created}` · `response.audio.delta`{delta:b64}→DECODED audio bytes · `response.audio_transcript.delta`{delta}→`{type:transcript, role:assistant, text:<delta>}` · `response.done`→`{type:response.done}` · `error`→`{type:error, code:"provider_error", message:<...>}` (FORWARDED, non-fatal). The provider socket closing ends the iterator normally.
  - `aclose()` closes the socket; idempotent (safe if never connected).
  - `RealtimeWebSocket` Protocol (`send(str)`/`recv()->str`/`aclose()`) + a real `connect_websocket(url, headers, *, timeout)` wrapping `websockets.connect`.
</must>
Reject:
<reject>
  - the WS dial raises / times out -> `RealtimeProviderUnavailableError` (the pump maps it to close 4503); no half-open socket left
  - a provider message that is not valid JSON -> `RealtimeProviderUnavailableError` raised from `events()` (the pump maps it to {error}+1011); the iterator stops
  - an OpenAI `error` event -> a gateway `{type:error, code:provider_error}` frame is YIELDED (forwarded to the client), NOT a raise — non-fatal, session continues
</reject>
After:
<after>
  - a fake socket scripted with OpenAI server events yields the exactly-translated gateway frames in order (audio decoded to bytes, transcript/control as dicts)
  - client gateway frames are translated to exactly the OpenAI JSON events on the fake socket's recorded sends (audio base64-encoded)
  - `OpenAIRealtimeSession` satisfies `isinstance(x, RealtimeRelaySession)` (runtime_checkable)
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the OpenAI Realtime transcript event name (`response.audio_transcript.delta`) — lowest confidence because OpenAI has revised realtime event names across betas; if wrong: a one-line mapping change, CONTAINED to `events()` translation (the seam + tests are unaffected — the test asserts the MAPPING, and t5's live harness is where a real name mismatch surfaces). Mitigation: translate by a small dispatch dict so the name is a single data point, and also map the generic `response.text.delta` as a fallback transcript source.
  - [ ] base64 for audio in/out (OpenAI uses base64 strings in JSON) — confirmed: OpenAI Realtime `input_audio_buffer.append.audio` and `response.audio.delta.delta` are base64 PCM16.
  - [ ] text-only WS seam is sufficient (no binary WS frames) — confirmed: OpenAI Realtime is JSON-text only; audio rides as base64 in JSON.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: provider events translate to gateway frames in order
  Given a fake socket scripted with session.created, response.audio.delta(b64 "AQID"), response.audio_transcript.delta("hi"), response.done
  When the session events() is iterated
  Then it yields {type:session.created}, then b"\x01\x02\x03", then {type:transcript,role:assistant,text:"hi"}, then {type:response.done}
  And no OpenAI JSON leaks to the caller

Scenario: client frames translate to OpenAI events
  Given a connected session over a fake socket
  When send_client_event({type:session.update,voice:alloy}) then send_audio(b"\x09") then send_client_event({type:audio.commit}) then send_client_event({type:interrupt})
  Then the socket received session.update{session:{voice:alloy}}, input_audio_buffer.append{audio:"CQ=="}, input_audio_buffer.commit, response.cancel
  And the audio was base64-encoded

Scenario: dial failure is provider-unavailable
  Given a ws_connect factory that raises ConnectionError
  When connect() is called
  Then RealtimeProviderUnavailableError is raised
  And no socket is left open

Scenario: malformed provider message stops the stream
  Given a fake socket scripted with the text "not json"
  When events() is iterated
  Then RealtimeProviderUnavailableError is raised from the iterator
  And the iterator stops (no further yields)

Scenario: an OpenAI error event is forwarded, not fatal
  Given a fake socket scripted with error{message:"rate limited"} then response.done
  When events() is iterated
  Then it yields {type:error,code:provider_error,message:"rate limited"} then {type:response.done}
  And the session is NOT torn down by the adapter

Scenario: the adapter satisfies the seam Protocol
  Given an OpenAIRealtimeSession instance
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

RealtimeWebSocket (Protocol, proxy/infrastructure/realtime_ws_client.py):
  async send(message: str) -> None
  async recv() -> str            # raises on socket close (ends events() normally)
  async aclose() -> None
connect_websocket(url: str, headers: dict[str,str], *, timeout: float) -> RealtimeWebSocket   # wraps websockets.connect

OpenAIRealtimeSession(RealtimeRelaySession, proxy/infrastructure/openai_realtime.py):
  __init__(*, model: str, api_key: str, ws_connect: Callable[[], Awaitable[RealtimeWebSocket]],
           url: str = "wss://api.openai.com/v1/realtime")
  connect()                      -> dial via ws_connect; failure -> RealtimeProviderUnavailableError
  send_client_event(frame)       -> OpenAI client event JSON (mapping table below)
  send_audio(bytes)              -> input_audio_buffer.append{audio: base64}
  events() -> AsyncIterator      -> OpenAI server event -> gateway frame (mapping table below)
  aclose()                       -> close socket (idempotent)

MAPPING (gateway ⇄ OpenAI):
  OUT  session.update        -> {type:session.update, session:{...frame∖type}}
       audio.commit          -> {type:input_audio_buffer.commit}
       response.create       -> {type:response.create}
       interrupt             -> {type:response.cancel}
       <audio bytes>         -> {type:input_audio_buffer.append, audio:b64}
  IN   session.created       -> {type:session.created}
       response.audio.delta  -> <decoded bytes>
       response.audio_transcript.delta / response.text.delta -> {type:transcript, role:assistant, text:<delta>}
       response.done         -> {type:response.done}
       error                 -> {type:error, code:provider_error, message:<...>}   (forwarded, non-fatal)
Errors: dial fail / non-JSON message -> RealtimeProviderUnavailableError. NO DB, NO HTTP, NO new dep.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-26 (AI auto-draft under autonomy:auto; seam already frozen by t1, no new decision — credential-gated live-verify deferred to t5).
Least-sure flag surfaced at freeze:
  - [spec] the OpenAI transcript event NAME (`response.audio_transcript.delta`) may differ across betas — CONTAINED to a one-line dispatch entry; mitigated by also mapping `response.text.delta`, and t5's live harness is the real-name oracle. Everything else (base64 audio, text-only WS, the client→server event names) is well-established OpenAI Realtime wire.
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
  - test_provider_events_translate_in_order: fake socket scripted with 4 OpenAI server events / iterate events() / assert the 4 gateway frames in order (audio decoded to bytes)
  - test_client_frames_translate_to_openai: connect over fake / send 4 client frames / assert socket.sent == the 4 OpenAI JSON events (audio base64)
  - test_dial_failure_provider_unavailable: ws_connect raises ConnectionError / connect() / assert RealtimeProviderUnavailableError + no open socket
  - test_malformed_message_raises: fake socket yields "not json" / iterate events() / assert RealtimeProviderUnavailableError, iterator stopped
  - test_openai_error_event_forwarded: fake socket yields error then response.done / iterate / assert {error} frame yielded (not raised) then {response.done}
  - test_satisfies_seam_protocol: isinstance(session, RealtimeRelaySession) True; aclose() before connect() is safe
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/realtime_ws_client.py` `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py` `apps/gateway/tests/realtime_relay/test_openai_adapter.py`
Strategy (ordered batches): 1. `realtime_ws_client.py` (the `RealtimeWebSocket` Protocol + `connect_websocket` real connector) 2. `openai_realtime.py` (the `OpenAIRealtimeSession` translation adapter) 3. tests ride the existing test-fast `tests/realtime_relay`
Safety rule (feature-specific): the adapter is the SOLE owner of OpenAI wire format (no leak past the seam); `connect()`/dial failures and non-JSON messages become `RealtimeProviderUnavailableError` (never a bare exception escaping the seam); `aclose()` is idempotent. NO live network in unit tests (fake socket only); the real `connect_websocket` is dialled only by t5's credential-gated harness.
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

- [x] all tests pass — `tests/realtime_relay/` 17/17 (6 new adapter + 11 t1 pump, no regression)
- [x] coverage did not decrease — NEW modules + NEW tests; nothing existing touched
- [x] no test or contract was altered during build — contract FROZEN @ v1; the only post-freeze code change WIDENED `ws_connect` to optional (additive, non-breaking; tests still inject)
- [x] the green was EARNED — tests assert the actual wire TRANSLATION both ways (base64 round-trip, exact OpenAI JSON event names, decoded audio bytes), dial-failure→typed error, non-JSON→typed error, error-event forwarded-not-fatal. No vacuous asserts; the fake socket records real sends.
- [x] concurrency / timing safe — adapter holds no tasks; `events()` is a plain async-generator over `recv()`; socket-close ends it normally; `aclose()` idempotent (nulls then closes). The pump (t1) owns timeouts/breaker.
- [x] no exposed secrets / injection / unexpected deps — api_key only in the Authorization header of the real dialer; `websockets` is transitive (no new dep), imported lazily; model is URL-quoted (`quote`) before going into the dial URL
- [x] layering follows CONVENTIONS.md — adapter is INFRASTRUCTURE implementing the domain `RealtimeRelaySession`; imports the frozen seam + the WS-client seam, never the pump/endpoint
- [x] reviewed — AI self-review + adversarial refute-read (below); no security surface (auth-over-WS is t4)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] OpenAI server events become gateway frames IN ORDER with audio decoded to bytes — `test_provider_events_translate_in_order` (4-event script → exact 4 gateway frames)
- [x] gateway client frames become exactly the 4 OpenAI JSON events with base64 audio — `test_client_frames_translate_to_openai` (recorded socket sends)
- [x] every failure is a TYPED seam error, never a bare leak — dial→`RealtimeProviderUnavailableError` (`test_dial_failure...`); non-JSON→same from the iterator (`test_malformed_message_raises`); OpenAI `error`→forwarded gateway frame, session intact (`test_openai_error_event_forwarded`)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `RealtimeWebSocket`, `connect_websocket`, `OpenAIRealtimeSession` all referenced (adapter imports the WS seam; the suite drives the adapter). The real `connect_websocket` is the prod/t5 dial path; t4 (endpoint) constructs `OpenAIRealtimeSession`.
- [x] DEAD-CODE (code) — eliminated the original dead `_model/_api_key/_url` state by giving the adapter a real default `_dial_openai` that uses them (ws_connect now optional). No orphaned symbol; `_TRANSCRIPT_EVENTS`/`_DEFAULT_URL` used.
- [x] SEMANTIC — adversarial refute-read (self, autonomy:auto): (a) base64 round-trip — `b"\x09"`→"CQ==" and "AQID"→`b"\x01\x02\x03"` verified by hand, matches asserts; (b) `error` shape — both `{error:{message}}` and flat `{message}` handled; (c) the default self-dialer is UNtested by unit (it dials real net) — acceptable, it's the t5 credential-gated path, and it's a thin URL+headers builder; (d) socket-close vs non-JSON: close (ConnectionError from recv) ends the stream, non-JSON (json.loads fails) RAISES — distinct, both tested. No earned-green cheat.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (AI auto-gate under autonomy:auto — no security surface in t2; translation evidence complete; `ws_connect` widened to optional is additive) · date: 2026-06-26

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
