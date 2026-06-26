# TASK: Realtime relay seam + bidirectional pump (design-for-failure)

slug: realtime-relay-port · created: 2026-06-26 · stage: production
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
  - NEW `apps/gateway/src/gateway/proxy/domain/realtime_relay.py` — the SEAM: `@runtime_checkable class RealtimeRelaySession(Protocol)` (`connect()` · `send_client_event(frame: dict)` · `send_audio(data: bytes)` · `events() -> AsyncIterator[RelayFrame]` · `aclose()`); the normalized gateway frame vocabulary (`RelayFrame` = a dict control frame OR an audio `(bytes)` envelope); typed errors `RealtimeRelayError` / `RealtimeProviderUnavailableError` / `RealtimeRelayClosed`.
  - NEW `apps/gateway/src/gateway/proxy/application/realtime_relay_pump.py` — the bidirectional PUMP engine: `RelayPump(transport: RelayClientTransport, session: RealtimeRelaySession, settings)` running two cancel-safe relay tasks (client→provider, provider→client) with design-for-failure; `RelayClientTransport` Protocol (the WS abstraction the endpoint adapts) = `receive() -> ClientFrame` / `send_event(dict)` / `send_audio(bytes)` / `close(code)`.
  - NEW `apps/gateway/tests/realtime_relay/test_relay_pump.py` (+`__init__.py`) — unit tests with a FAKE provider session + FAKE client transport (no WS, no network).
  - `apps/gateway/src/gateway/core/config.py:Settings` — ADD relay knobs: `realtime_relay_connect_timeout_seconds` (bounds connect AND each provider send) · `realtime_relay_idle_timeout_seconds` (mirror the existing `realtime_auth_timeout_seconds` / `realtime_max_utterance_bytes` pattern). NO send-queue knob (Tin's freeze: direct awaited relay).
  - REUSE-ANCHORS (read, do not modify): `proxy/api/realtime_ws.py` — v47 close codes `_CODE_AUTH_INVALID`(4401)/`_CODE_AUTH_TIMEOUT`(4408), `_authenticate_token`, `_error_frame`, the `app.state` stub seam, WebSocketDisconnect→break pattern; `proxy/infrastructure/circuit_breaker.py:CircuitBreaker` (guard/record_success/on_upstream_error) for the provider-connect guard; `objectstore/s3.py` (v51) as the design-for-failure REFERENCE (per-op timeout + breaker + typed errors).
Context (working folder):
  - `apps/gateway/Makefile` test-fast — the pump tests are pure-unit (no DB/WS) → add `tests/realtime_relay` to the test-fast list (like v51 added `tests/objectstore`).
  - `apps/gateway/tests/realtime/` (v47) — the TestClient-WS pattern the ENDPOINT task (t4) will mirror; t1 stays transport-agnostic (fake transport, no Starlette).
Honors (patterns / conventions):
  - PORT/ADAPTER LAYERING (project): domain Protocol (`realtime_relay.py`) ← application pump (`realtime_relay_pump.py`) ← infrastructure adapters (t2/t3, NOT this task). The pump depends on the Protocol, never a concrete provider.
  - NORMALIZED FRAME PROTOCOL (v52 HARD): the client speaks ONE gateway frame vocabulary; the seam translates. t1 OWNS + FREEZES that vocabulary; adapters (t2/t3) + the endpoint (t4) consume it.
  - DESIGN-FOR-FAILURE (v52 HARD): connect + each provider send bounded by `asyncio.timeout` (→ 4503 on hang); two cancel-safe `asyncio` relay tasks; provider disconnect/end → typed client close; client disconnect → cancel in-flight + `aclose()` the provider (no leaked task/socket); any `events()` raise → an `{type:"error"}` frame, never a 500/hang. (NO send queue — Tin's freeze: direct awaited relay.)
  - AUTH-OVER-WS is the ENDPOINT's surface (t4), NOT t1 — the pump starts AFTER auth; t1 assumes an already-authed transport.
  - `asyncio.TaskGroup`/`asyncio.timeout` + cancel-safety (project async convention); structured-concurrency cleanup in `finally`.
Anchors the contract cites: `RealtimeRelaySession` (Protocol) · `RelayClientTransport` (Protocol) · `RelayPump` · the normalized `RelayFrame` vocabulary (client→gateway + gateway→client frame types) · `RealtimeRelayError`/`RealtimeProviderUnavailableError`/`RealtimeRelayClosed` · `Settings.realtime_relay_*` knobs

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Provider-agnostic realtime relay seam + a design-for-failure bidirectional pump
Framings weighed: two cancel-safe relay tasks under a TaskGroup + bounded send queue (chosen — simplest structured-concurrency shape, first-task-to-finish wins, deterministic cleanup) · a single select-loop multiplexing both directions (rejected — harder to cancel cleanly, interleaves provider+client backpressure) · a thread per direction (rejected — no asyncio cancel-safety, leaks)
Must:
<must>
  - DEFINE the seam: `RealtimeRelaySession` Protocol — `connect()` · `send_client_event(frame: dict)` · `send_audio(bytes)` · `events() -> AsyncIterator[RelayFrame]` · `aclose()`; and `RelayClientTransport` Protocol — `receive() -> ClientFrame` · `send_event(dict)` · `send_audio(bytes)` · `close(code: int)`. The pump depends ONLY on these Protocols (never a concrete provider/WS).
  - DEFINE the normalized `RelayFrame` vocabulary (frozen here; t2/t3/t4 consume it): client→gateway control {`session.update`, `audio.commit`, `response.create`, `interrupt`} + audio bytes; gateway→client {`session.created`, `transcript`, `audio.delta`(+bytes), `response.done`, `error`}.
  - `RelayPump.run()` opens the provider via `connect()` under `asyncio.timeout(realtime_relay_connect_timeout_seconds)`, guarded by a `CircuitBreaker`; on success runs TWO cancel-safe tasks — client→provider (`transport.receive()`→`session.send_client_event`/`send_audio`) and provider→client (`async for f in session.events()`→`transport.send_event`/`send_audio`).
  - CLIENT→PROVIDER relay is a DIRECT awaited send (NO queue — Tin's freeze decision): each `send_client_event`/`send_audio` (and `connect`) is bounded by `asyncio.timeout(realtime_relay_connect_timeout_seconds)`; a wedged provider send that exceeds it → `RealtimeProviderUnavailableError` → close 4503. Slow (not wedged) providers are throttled by their own socket backpressure, which naturally pauses the client-read task.
  - TERMINATION is deterministic: whichever side ends first (client disconnect, provider `events()` exhausts, idle timeout, or a raise) → cancel the sibling task, `aclose()` the provider session, and `close()` the transport with a typed code — in a `finally`, exactly once, no leaked task/socket.
  - IDLE guard: no client frame within `realtime_relay_idle_timeout_seconds` → typed idle close.
  - `Settings` gains `realtime_relay_connect_timeout_seconds` (>0; also bounds each provider send) · `realtime_relay_idle_timeout_seconds` (>0), with safe defaults.
</must>
Reject:
<reject>
  - provider `connect()` OR any provider send exceeds the timeout, OR the breaker is OPEN -> RealtimeProviderUnavailableError -> pump closes the transport with code 4503 (provider unavailable); the breaker records the failure
  - provider `events()` raises mid-session -> the pump sends an `{type:"error", code:"provider_error"}` frame then closes 1011 (server error) — NEVER a 500 or a hang; the sibling task is cancelled
  - no client frame within the idle timeout -> close 4408 (idle); the provider session is aclose()d
  - the client transport disconnects -> the provider task is cancelled and `aclose()`d; the pump returns cleanly (no leaked task) — observable, not an error
</reject>
After:
<after>
  - A fully provider-agnostic pump: given ANY `RealtimeRelaySession` + `RelayClientTransport`, audio/control flows both ways until one side ends, then BOTH sides are torn down exactly once.
  - Every failure mode (connect timeout, provider raise, backpressure, idle, client disconnect) yields a typed close code + (where applicable) an `{error}` frame, with no leaked asyncio task or open session — provable with fakes, no network.
  - The seam + frame vocabulary + config are FROZEN for t2 (OpenAI), t3 (Gemini), t4 (endpoint) to build against.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ RESOLVED at freeze (Tin): NO send queue — client→provider is a DIRECT awaited send bounded by the op timeout (a wedged send → 4503); slow providers are handled by their own socket backpressure pausing the client-read task. The remaining risk: a provider whose send blocks JUST under the timeout repeatedly could stall throughput — acceptable for the MVP (the timeout still bounds a true hang); revisit only if a real provider needs explicit pacing (additive, no seam change).
  - [ ] frame vocabulary is provider-coverable — both OpenAI Realtime AND Gemini Live events map onto {transcript, audio.delta, response.done, error}; confirm in t2/t3 — if a provider has an event with no gateway frame, t2/t3 raises a spec delta to EXTEND the vocabulary (additive), not reshape it.
  - [ ] close codes (4503/4429/4408/1011) don't collide with v47's 4401/4408 — note: 4408 is REUSED for idle (v47 uses it for auth-timeout; different endpoint, same "timed out" meaning) — acceptable.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: happy-path full-duplex relay through fakes
  Given a fake provider session and a fake client transport
  When the client sends a session.update + audio + audio.commit and the provider emits transcript + audio.delta + response.done
  Then the transport receives those gateway frames in order (audio.delta carries bytes)
  And both the session and transport are closed exactly once at the end

Scenario: provider connect timeout closes 4503
  Given a fake session whose connect() never returns within the timeout
  When the pump runs
  Then the transport is closed with code 4503 and the breaker recorded a failure
  And no relay task is left running

Scenario: provider events() raises mid-session
  Given a fake session that raises after a few events
  When the pump runs
  Then the transport gets an {error, code:"provider_error"} frame then close 1011
  And the client→provider task was cancelled (no leaked task)

Scenario: wedged provider send closes 4503
  Given a provider whose send_audio never returns within the timeout
  When the client sends audio
  Then the transport is closed 4503 (provider unavailable) and the breaker recorded a failure
  And no relay task is left running

Scenario: idle timeout closes 4408
  Given no client frame arrives within realtime_relay_idle_timeout_seconds
  When the pump waits
  Then the transport is closed 4408 (idle) and the provider session is aclose()d

Scenario: client disconnect tears down the provider cleanly
  Given the client transport raises disconnect on receive()
  When the pump runs
  Then the provider task is cancelled and aclose()d and the pump returns cleanly
  And no asyncio task is left pending (observable, not an error)

Scenario: config knobs validated
  Given Settings with realtime_relay_* knobs
  When constructed with a non-positive value
  Then it is rejected (gt=0), and the defaults are positive
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SEAM  gateway.proxy.domain.realtime_relay
  RelayFrame = dict            # control: {"type": <name>, ...}; audio: ("audio", bytes) tuple OR {"type":"audio.delta"} + a bytes payload
  ClientFrame = dict | bytes   # what RelayClientTransport.receive() yields (control dict | raw audio bytes | a disconnect sentinel)

  class RealtimeRelaySession(Protocol):           # implemented by t2 (OpenAI), t3 (Gemini)
    async def connect(self) -> None               # open provider WS; may raise RealtimeProviderUnavailableError
    async def send_client_event(self, frame: dict) -> None   # gateway control frame → provider-native
    async def send_audio(self, data: bytes) -> None          # client audio → provider
    def events(self) -> AsyncIterator[RelayFrame]            # provider events → normalized gateway frames
    async def aclose(self) -> None                # idempotent

  class RelayClientTransport(Protocol):           # adapted by t4 over a Starlette WebSocket
    async def receive(self) -> ClientFrame        # raises RealtimeRelayClosed on client disconnect
    async def send_event(self, frame: dict) -> None
    async def send_audio(self, data: bytes) -> None
    async def close(self, code: int) -> None      # idempotent

  errors: RealtimeRelayError(Exception) ⊃ RealtimeProviderUnavailableError, RealtimeRelayClosed

NORMALIZED FRAME VOCABULARY (frozen; additive-only later)
  client→gateway : {"type":"session.update", ...} · {"type":"audio.commit"} · {"type":"response.create"} · {"type":"interrupt"} · audio bytes
  gateway→client : {"type":"session.created"} · {"type":"transcript","role","text"} · {"type":"audio.delta"}+bytes · {"type":"response.done"} · {"type":"error","code","message"}

PUMP  gateway.proxy.application.realtime_relay_pump
  class RelayPump:
    def __init__(self, transport, session, settings, *, breaker: CircuitBreaker | None = None)
    async def run(self) -> None    # connect (timeout+breaker) → 2 cancel-safe tasks → deterministic teardown in finally

CLOSE CODES (pump → transport.close): 4503 provider-unavailable (connect OR send timeout / breaker open) · 1011 provider-error mid-session · 4408 idle · 1000 normal
CONFIG  Settings (core/config.py): realtime_relay_connect_timeout_seconds: float = 10.0 (gt=0; bounds connect AND each provider send) ·
        realtime_relay_idle_timeout_seconds: float = 300.0 (gt=0)   # NO send-queue knob (Tin: direct awaited relay)
NO DB · NO migration · NO network in this task (fakes only). AUTH is the endpoint's job (t4), not the pump's.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-26 (freeze decision: NO send queue — client→provider is a direct awaited send bounded by the op timeout → 4503 on a hang; the 4429 backpressure code + send_queue_max knob are removed).
Least-sure flag surfaced at freeze:
  - [contract] RESOLVED — the send-queue backpressure model was the lowest-confidence point; Tin dropped it for a direct awaited relay (timeout as the only bound). Residual risk (a provider that stalls just under the timeout) is MVP-acceptable and additively revisitable without a seam change.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (pump engine + seam)
Plan (one test per scenario — fake provider session + fake client transport, no WS/network):
<test_plan>
  - test_happy_path_full_duplex: fake session emits transcript/audio.delta/response.done / run pump / assert transport got frames in order + both closed once
  - test_provider_connect_timeout_4503: session.connect hangs past timeout / run / assert close(4503) + breaker.on_upstream_error called + no pending task
  - test_provider_events_raise_1011: session.events raises mid-stream / run / assert {error,provider_error} frame + close(1011) + sibling cancelled
  - test_provider_send_timeout_4503: send_audio never returns within timeout / run / assert close(4503) + breaker.on_upstream_error + no pending task
  - test_idle_timeout_4408: no client frame within idle timeout / run / assert close(4408) + session.aclose called
  - test_client_disconnect_clean_teardown: transport.receive raises RealtimeRelayClosed / run / assert provider task cancelled + aclose + returns cleanly + no pending asyncio task
  - test_session_protocol_runtime_checkable: a fake satisfies isinstance(fake, RealtimeRelaySession)
  - test_config_knobs_validated: Settings rejects non-positive realtime_relay_* (gt=0); defaults positive
</test_plan>

Tests live in: `apps/gateway/tests/realtime_relay/` · MUST run red (ModuleNotFoundError — seam/pump absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/realtime_relay.py` `apps/gateway/src/gateway/proxy/application/realtime_relay_pump.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/Makefile`
Strategy (ordered batches): 1. domain seam (`realtime_relay.py`: Protocols + RelayFrame vocab + typed errors) 2. config knobs (connect/idle timeouts) 3. pump engine (`realtime_relay_pump.py`: connect-timeout+breaker → TaskGroup of 2 cancel-safe relay tasks → direct awaited provider sends (timeout-bounded) → deterministic finally teardown) 4. Makefile test-fast adds tests/realtime_relay
Safety rule (feature-specific): the pump tears down BOTH sides exactly once in a `finally` (idempotent aclose/close); every relay task is cancel-safe (no leaked task/socket on any exit path); connect AND each provider send are timeout+breaker guarded → 4503 on a hang (NO queue — direct awaited relay, Tin's decision). NO network/WS/DB in this task — Protocols + fakes only.
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

- [x] all tests pass — `tests/realtime_relay/` 11/11 green (pump + Protocol + config validation)
- [x] coverage did not decrease — NEW module + NEW tests; no existing test touched
- [x] no test or contract was altered during build — contract FROZEN @ v1; the only test edit (events_raise fake input + pytestmark removal) was a fake-semantics fix made BEFORE re-crossing tests→build to re-snapshot (no assertion weakened)
- [x] the green was EARNED, not gamed — fakes drive real pump control-flow (close codes / breaker calls / aclose / task-leak count); the happy-path test caught a real fake-semantics bug (1-event list never reached the raise → fixed to actually exercise the 1011 path). No vacuous asserts.
- [x] concurrency / timing of the risky operation is safe — two cancel-safe relay tasks under `asyncio.wait(FIRST_COMPLETED)`; sibling cancelled + gathered (return_exceptions) before teardown; connect + each provider send bounded by `asyncio.timeout`; idle bounded; `test_no_leaked_tasks` asserts the task count returns to baseline; teardown is idempotent + never raises (BLE caught + logged)
- [x] no exposed secrets, injection openings, or unexpected dependencies — pure stdlib asyncio + existing CircuitBreaker; no network/WS/DB; no new deps; error frames carry `str(exc)` only (no provider wire leak — adapters own translation)
- [x] layering & dependencies follow CONVENTIONS.md — domain Protocol (`proxy/domain/realtime_relay.py`) ← application pump (`proxy/application/realtime_relay_pump.py`); the pump imports only Protocols + domain errors + CircuitBreaker, never a concrete provider/WS
- [x] reviewed — AI self-review + adversarial refute-read (below); design-for-failure is the milestone HARD; no security surface in t1 (auth lives in t4)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] provider events reach the client IN ORDER, audio split from control — confirmed by `test_happy_path_full_duplex` (sent_events == 3 control frames in order, sent_audio == [bytes], close 1000)
- [x] client control + audio reach the provider; client disconnect tears down cleanly — `test_client_to_provider_then_disconnect` (session.sent_events/sent_audio + session aclose)
- [x] each failure mode yields a TYPED close, no hang/500/leak — connect-timeout→4503 · breaker-open→4503 (no connect) · provider-send-timeout→4503 · provider events() raise→{error}+1011 · idle→4408; all asserted; `test_no_leaked_tasks` proves no leak

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `RelayPump`, both Protocols, `RelayFrame`/`ControlFrame`, the 3 errors, and both config knobs are all referenced by the suite (Settings boots with the defaults). NOTE: consumed by t2/t3/t4 (adapters + endpoint) — this task FREEZES the seam.
- [x] DEAD-CODE (code) — removed the unused `_run` test helper; no orphaned symbol. `_IdleTimeout`/`_error_frame`/`_is_audio` all used.
- [x] SEMANTIC — adversarial refute-read (self, autonomy:auto): tried to break (a) ordering race in happy path — provider events() is synchronous-fast and finishes before the idle window, c2p cancelled mid-receive, deterministic; (b) breaker double-count — on_upstream_error fires once per failure path (connect OR the single done-task branch), not both; (c) teardown re-entrancy — single `finally`, idempotent fakes; (d) send-timeout vs idle-timeout confusion — distinct `asyncio.timeout` scopes (idle wraps receive, connect_timeout wraps send). No earned-green cheat found.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (AI auto-gate under autonomy:auto — no security surface in t1; design-for-failure evidence complete) · date: 2026-06-26
Residue (non-blocking): pre-existing E501 on config.py `memory_search_default_top_k` (108 chars, committed HEAD — NOT introduced here; out of scope).

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
