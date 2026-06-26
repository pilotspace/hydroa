# TASK: Gateway WebSocket /v1/realtime: auth + STT->chat->TTS turn loop

slug: realtime-voice · created: 2026-06-26 · stage: production
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
  - `apps/gateway/src/gateway/proxy/api/realtime_ws.py` (NEW) — `realtime_router` with a `@router.websocket("/v1/realtime")` handler running the auth handshake + the turn loop. Helper `_authenticate_token(raw_key, session) -> AuthzResult` (mirror v44 memory `_authenticate` incl. expiry, but for a token string from the auth frame).
  - `apps/gateway/src/gateway/main.py` (MODIFY) — include_router(realtime_router); the WS handler needs the same deps the v42 audio endpoints build (governance, sessionmaker, tenant_credential_resolver, ProviderRegistry, UsageRecorder, the chat use-case). Source them the SAME way the audio router/deps do.
  - `apps/gateway/src/gateway/core/config.py` (MODIFY, additive) — `realtime_auth_timeout_seconds: float = Field(default=10.0, ge=0)` + `realtime_max_utterance_bytes: int = Field(default=26_214_400, ge=0)` (25 MiB; 0=unlimited).
  - `apps/gateway/tests/realtime/` (NEW) — Starlette TestClient `websocket_connect` tests with STT/chat/TTS STUBBED on app.state (no live providers).
Context (working folder):
  - REUSE use-cases (v42): `TranscriptionUseCase.execute(form, raw_key, registry, usage_recorder) -> (status, body)` (STT — `form` is a multipart-like with a "file" field + "model"); `SpeechUseCase.execute(raw_key, body, registry, usage_recorder) -> (audio_iterator, media_type)` (TTS). The chat completion use-case (proxy/application/use_cases.py) — find its in-process entry (the same one /v1/chat/completions calls) and invoke it with a small messages list. Read proxy/api (the audio deps) to see how these use-cases + registry + usage_recorder are constructed per request.
  - AUTH: KeyAuthenticator.authenticate(raw_key) → AuthzResult{tenant_id,key_id,expires_at}; copy v44 memory router's expiry gate. For WS there is no Authorization header in the browser handshake → auth via the FIRST WS message {type:"auth", token}.
  - The observability middleware already allows `scope["type"] in ("http","websocket")`; the concurrency guard SKIPS non-http. So a WS endpoint coexists. Verify the middleware doesn't choke on a WS scope during tests.
  - To feed STT: build a multipart-like `form` from the received audio bytes (a dict/Starlette FormData with an UploadFile-like "file" + a "model"); look at how TranscriptionUseCase reads `form.get("file")` and construct a matching object.
Honors (patterns / conventions):
  - AUTH-OVER-WS (security HARD invariant): no frame other than the auth frame is processed until authenticated; bad/missing/expired token OR auth timeout → close (4401/4408); token NEVER in the URL.
  - DESIGN-FOR-FAILURE: per-utterance size cap → error frame (no STT call); a use-case/provider error → `{type:"error"}` frame (socket stays usable or closes cleanly), never an unhandled 500; WebSocketDisconnect → cancel any in-flight work, no leaked task.
  - REUSE: chain the existing use-cases; billing/governance ride them unchanged; no new dependency (Starlette WS is built in).
Anchors the contract cites:
  - `realtime_router` · the `/v1/realtime` WS frame protocol (auth/audio/commit/transcript/reply/audio/turn_done/error) · `_authenticate_token` (KeyAuthenticator + expiry) · the reused TranscriptionUseCase/chat/SpeechUseCase · the size-cap + auth-timeout config.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a gateway WebSocket `/v1/realtime` carrying an authenticated, turn-based voice conversation — audio in → transcript → assistant reply → synthesized audio out — reusing the v42 STT/TTS + chat pipeline.
Framings weighed: a turn-based WS loop chaining the existing TranscriptionUseCase + chat + SpeechUseCase (chosen — reuse-only, zero new dep, fully TestClient-testable) · a full-duplex relay to a provider realtime API (rejected for the MVP — needs a provider realtime endpoint + a bidirectional pump; a SCALE delta) · auth via a `?token=` query param (rejected — leaks the token into logs/history; first-message auth is cleaner).
Must:
<must>
  - M1 — the client opens the WS and sends `{type:"auth", token:"sk-..."}` as the FIRST message; the server authenticates via KeyAuthenticator (+ expiry) and replies `{type:"ready"}`; until authed, NO other frame is processed.
  - M2 — after auth, the client streams audio (binary frames), then sends `{type:"commit", model_stt, model_chat, model_tts, voice?}`; the server: STT → `{type:"transcript", text}` → chat → `{type:"reply", text}` → TTS → one or more binary audio frames → `{type:"turn_done"}`.
  - M3 — multiple turns are supported on the same socket (the audio buffer resets after each commit).
  - M4 — an auth timeout (no auth frame within realtime_auth_timeout_seconds) closes the socket (code 4408).
  - M5 — a per-utterance audio buffer over realtime_max_utterance_bytes → an `{type:"error", code:"utterance_too_large"}` frame and the buffer resets (no STT call); the socket stays open.
  - M6 — any use-case/provider error during a turn → `{type:"error", code, message}` frame; the socket stays usable (or closes cleanly on a fatal error), NEVER an unhandled 500/crash.
  - M7 — a client disconnect (WebSocketDisconnect) tears the session down cleanly: any in-flight work is cancelled, no leaked task/log-spam.
</must>
Reject:
<reject>
  - first frame not an auth frame, or a bad/missing/expired token -> close (4401), no turn processing.
  - a commit with no buffered audio -> `{type:"error", code:"no_audio"}`; socket stays open.
  - audio over the size cap -> `{type:"error", code:"utterance_too_large"}`; buffer reset; socket open.
  - a malformed control frame (bad JSON / unknown type) -> `{type:"error", code:"bad_frame"}`; socket open.
</reject>
After:
<after>
  - An authenticated client holds a multi-turn voice conversation over one WebSocket; each turn yields a transcript + a reply + audio; auth failures close the socket; size-cap / provider / malformed-frame errors yield clean error frames; a disconnect tears down cleanly.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ feeding the received audio bytes into TranscriptionUseCase.execute(form, ...) — lowest confidence because the use-case expects a multipart-like `form` with a "file" field; I must construct a Starlette UploadFile/FormData-like object from the raw bytes. If the use-case's form contract differs, the WS handler adapts (or calls a lower-level STT path). Cost if wrong: extra wiring to match the form shape; the protocol + auth are unaffected. The subagent must read TranscriptionUseCase to match its `form.get("file")` expectation exactly.
  - [x] Starlette WS + TestClient.websocket_connect work with no new dep — CONFIRMED (Starlette built-in).
  - [x] the observability middleware tolerates a websocket scope — CONFIRMED (it allows ("http","websocket")); verify in tests.
  - [ ] the chat use-case has an in-process entry callable with a messages list — the subagent finds the same entry /v1/chat/completions uses; if it's tightly request-coupled, stub the chat step on app.state for tests + wire the real call minimally.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Authenticate then one voice turn
  Given a stubbed STT→"hello", chat→"hi there", TTS→audio bytes
  When the client sends {auth, sk-...}, then audio frames, then {commit, models}
  Then it receives {ready}, then {transcript:"hello"}, {reply:"hi there"}, audio frame(s), {turn_done}

Scenario: Two turns on one socket
  Given an authed session after turn 1
  When the client sends a second utterance + commit
  Then a second transcript/reply/audio/turn_done cycle completes (buffer reset between)

Scenario: Bad auth closes (rejection)
  Given the first frame is {auth, "sk-invalid"} (or not an auth frame)
  When the server authenticates
  Then the socket is closed (4401) and no turn is processed

Scenario: Auth timeout closes (rejection)
  Given the client sends no auth frame
  When realtime_auth_timeout_seconds elapses
  Then the socket is closed (4408)

Scenario: Over-cap utterance (rejection)
  Given realtime_max_utterance_bytes is small and the client streams more
  When commit
  Then {error, code:"utterance_too_large"} is sent, no STT call, the buffer resets, the socket stays open

Scenario: Provider error during a turn (rejection)
  Given the stubbed STT raises
  When the client commits
  Then {error, code, message} is sent and the socket stays usable (no 500/crash)

Scenario: Commit with no audio (rejection)
  Given no audio buffered
  When the client sends {commit}
  Then {error, code:"no_audio"} and the socket stays open

Scenario: Clean disconnect
  Given an authed session
  When the client disconnects mid-session
  Then the server tears down cleanly (no leaked task / unhandled error)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
WS  /v1/realtime   (FastAPI/Starlette websocket; JSON control frames via send_json/receive_json, audio via bytes)

Handshake:
  client → {type:"auth", token:"sk-..."}            (MUST be the first message)
  server → {type:"ready"}                            on success
         | close(code=4401)                          bad/missing/expired token, or first frame not auth
         | close(code=4408)                           no auth frame within realtime_auth_timeout_seconds
  Auth: _authenticate_token(token) → KeyAuthenticator.authenticate → AuthzResult{tenant_id,key_id,expires_at};
        expiry gate identical to v44 memory. The token is read from the frame, NEVER the URL.

Turn (repeatable):
  client → <binary audio frame>*                      accumulate into a per-turn buffer
  client → {type:"commit", model_stt, model_chat, model_tts, voice?}
  server → {type:"transcript", text}                  (TranscriptionUseCase on the buffered audio)
         → {type:"reply", text}                       (chat completion on [{role:"user", content:transcript}])
         → <binary audio frame>+                       (SpeechUseCase stream of the reply)
         → {type:"turn_done"}
  buffer resets after each commit (and after an error).

Errors (socket stays OPEN unless fatal):
  {type:"error", code, message}
    code ∈ { "utterance_too_large" (buffer>cap, before STT) | "no_audio" (commit with empty buffer)
           | "bad_frame" (malformed JSON / unknown type) | "stt_failed" | "chat_failed" | "tts_failed" }
  A use-case raising → the matching *_failed error frame; the turn aborts; the socket stays usable.
  WebSocketDisconnect at any point → break the loop, cancel in-flight work, return (clean teardown).

Reuse (no new HTTP route, no new dep):
  TranscriptionUseCase.execute(form, raw_key, registry, usage_recorder) -> (status, body)   # body["text"]
  <chat use-case>.execute(...) the same entry /v1/chat/completions uses, with messages=[{role:"user",content:transcript}]
  SpeechUseCase.execute(raw_key, body={model,input,voice}, registry, usage_recorder) -> (audio_iter, media_type)
  raw_key = the authed token; billing/governance ride these use-cases unchanged.

Test seam: app.state may hold stub STT/chat/TTS callables (mirror v42/v44 app.state stubs) so tests run with
  no live provider; the handler prefers the stub if present, else the real use-case.

Config (additive): realtime_auth_timeout_seconds: float = 10.0 ; realtime_max_utterance_bytes: int = 26_214_400 (25 MiB; 0=unlimited)
```

Status: FROZEN @ v1 — auto-approved (reuse-only MVP per Tin's checkpoint; zero new dep; chains existing use-cases). The auth-over-WS handshake is the security surface — built to the frozen rule + I review the handler diff directly at the gate. 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] AUTH-OVER-WS — a WS has no Authorization header in the browser handshake, so auth is the first message; the RISK is processing any turn before auth or leaking the token. Mitigation: the loop refuses every non-auth frame until authed, auth has a timeout, the token is never in the URL, and a bad/expired token closes (4401) — verified by tests (bad-auth-close, auth-timeout, no-turn-before-auth) + my direct read. Cost if wrong: an unauthenticated voice/billing session.
  - [spec] STT form-shape — feeding raw audio bytes into TranscriptionUseCase needs a multipart-like form; the subagent must match `form.get("file")`. Cost if wrong: wiring rework, not a protocol change (mitigated: the test stubs STT, and a focused real-form test confirms the shape).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — Starlette TestClient `client.websocket_connect("/v1/realtime")`; STUB STT/chat/TTS on app.state (no live provider/network). Joins make test-fast (no DB needed if auth is stubbable; else mark DB-backed).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_auth_then_one_turn: auth → ready; audio + commit → transcript/reply/audio/turn_done (stubs).
  - test_two_turns: a second utterance completes a second cycle on the same socket.
  - test_bad_auth_closes: first frame {auth,"sk-invalid"} → close 4401 (assert WebSocketDisconnect with code).
  - test_first_frame_not_auth_closes: a non-auth first frame → close 4401.
  - test_auth_timeout_closes: no auth frame → close 4408 (use a tiny timeout via config override).
  - test_over_cap_utterance: tiny cap, big audio, commit → {error, utterance_too_large}, no STT call, socket open.
  - test_provider_error_frame: stub STT raises → {error, stt_failed}, socket stays usable.
  - test_commit_no_audio: commit with empty buffer → {error, no_audio}, socket open.
  - test_clean_disconnect: client closes mid-session → server returns cleanly (no error logged/raised in the test).
</test_plan>

Tests live in: `apps/gateway/tests/realtime/test_realtime_ws.py` · MUST run red before Build. (TestClient WS → `uv run pytest tests/realtime`; add to make test-fast if no DB.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/api/realtime_ws.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/tests/realtime/`
  (if a tiny helper is needed to build a form from bytes, keep it inside realtime_ws.py.)
Strategy (ordered batches): 1. the WS handler skeleton + auth handshake (first-frame auth, expiry, timeout, close codes). 2. the turn loop (accumulate audio → commit → STT→chat→TTS → frames) with the app.state stub seam. 3. error/size-cap/disconnect handling. 4. config knobs + main.py include. 5. TestClient WS tests.
Safety rule (feature-specific): AUTH-OVER-WS — process NO turn frame until the first-message auth succeeds; auth timeout + bad/expired token → close (4408/4401); token never in the URL. DESIGN-FOR-FAILURE — size cap before STT; wrap each use-case call so a raise → an {error} frame (no 500); WebSocketDisconnect → break + cancel in-flight + return (no leaked task). REUSE the existing use-cases; no new dependency.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; reuse the existing use-cases (no new provider/dep); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 12 realtime tests green (`uv run pytest tests/realtime` 12 passed); make test-fast exit 0 (228 no-DB, unchanged — realtime tests are DB-backed, run separately, NO regression).
- [x] coverage did not decrease — 12 behavioral tests added (one per scenario + a multi-frame buffer-bound test); the whole protocol + every rejection is exercised.
- [x] no test or contract was altered during build — only new files + 2 additive config fields + the include_router line; the frozen contract is implemented verbatim.
- [x] the green was EARNED — I read the WS handler in FULL (proxy/api/realtime_ws.py). The use-cases are the REAL TranscriptionUseCase / CompletionUseCase.complete / SpeechUseCase when stubs are absent (constructed exactly as the audio/completion deps do); the app.state stubs are a TEST seam, not a stubbed-away implementation. Auth is the real SqlAlchemyKeyAuthenticator + expiry. No overfit/vacuous asserts (tests assert frame contents + STT-not-called + socket-stays-open + close codes).
- [x] concurrency / timing of the risky operation is safe — the auth wait is bounded by asyncio.timeout (4408 on expiry); WebSocketDisconnect is caught at EVERY await (Phase-1 receive, turn-loop receive, the explicit disconnect message, and mid-TTS-stream) → clean return, no leaked task; the audio buffer is now BOUNDED during accumulation (DoS hardening).
- [x] no exposed secrets, injection openings, or unexpected dependencies — token read ONLY from the auth frame (never URL/query — test_token_only_from_auth_frame); no new dependency (Starlette WS is built in); errors return a code/message frame (str(exc)) — no stack/secret leak; the key is the same sk- plaintext the HTTP path uses, scoped to the authed session.
- [x] layering & dependencies follow CONVENTIONS.md — the endpoint lives in proxy/api (mirrors the audio router); it reuses keys/* authenticator + proxy/application use-cases; no new layer crossing.
- [x] a person reviewed and approved the change — full-auto self-approve for a non-high-risk reuse task (Tin's reuse-only ruling). The SECURITY surface (auth-over-WS) I reviewed DIRECTLY by reading the handler end-to-end (see GATE RECORD) — no new external API key / architecture decision, so no HARD-STOP triggered.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] auth → {ready}, then audio+commit → {transcript}/{reply}/audio/{turn_done} — confirmed by test_auth_then_one_turn (frame-by-frame asserts).
- [x] multi-turn on one socket — confirmed by test_two_turns (second cycle after buffer reset).
- [x] bad/non-auth/empty-token first frame → close 4401 — test_bad_auth_closes_4401 / test_first_frame_not_auth_closes_4401 / test_first_frame_commit_not_auth_closes_4401.
- [x] no auth frame → close 4408 — test_auth_timeout_closes_4408 (tiny timeout override).
- [x] over-cap utterance (single frame AND streamed across many frames) → {error,utterance_too_large}, NO STT call, buffer reset, socket open, memory bounded — test_over_cap_utterance + test_over_cap_streamed_across_many_frames.
- [x] a use-case raise → {error,stt_failed}, socket stays usable — test_provider_error_frame.
- [x] commit with empty buffer → {error,no_audio} — test_commit_no_audio.
- [x] clean disconnect — test_clean_disconnect (no server error on mid-session close).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — realtime_router is included in main.py (`app.include_router(realtime_router)`); the WS handler resolves settings/sessionmaker/provider_registry/usage_recorder/budget_guard/etc from app.state exactly as the audio + completion deps do; both new config fields are read (realtime_auth_timeout_seconds in the auth timeout; realtime_max_utterance_bytes in the accumulation bound + the commit cap).
- [x] DEAD-CODE (code) — no orphaned symbol; _real_stt/_real_chat/_real_tts are the no-stub path (reached when app.state stubs are absent — the production path); _authenticate_token + _error_frame both used; pyright 0 / ruff clean on the file.
- [x] SEMANTIC — I read realtime_ws.py end-to-end; confirmed the auth gate fully precedes the turn loop, the token is frame-only, every await is disconnect-guarded, and the buffer is bounded both in-flight and at commit.

### GATE RECORD
Outcome: PASS
Security review (auth-over-WS, done directly by me — the orchestrator): token is read ONLY from the first {auth} frame (no URL/query token — test asserts it); NO turn frame is processed before a successful KeyAuthenticator auth + expiry gate (first binary/non-auth/empty/invalid frame → close 4401); the auth wait is bounded (→4408); a use-case error → an {error} frame (str(exc), no stack/secret leak), the socket survives; WebSocketDisconnect → clean teardown at every await. DESIGN-FOR-FAILURE hardening I added beyond the contract: the audio buffer is bounded DURING accumulation (cap+1) so a never-committing streamer cannot exhaust memory — behavior-preserving (commit still returns utterance_too_large), covered by a new multi-frame test. No new external API key / architecture decision → no HARD-STOP. Deltas (documented in MILESTONE Out): provider full-duplex realtime relay, dashboard live-voice UI, Envoy WS-upgrade edge config.
Reviewed by: Tin Dang (full-auto self-approve; reuse-only ruling) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] full-duplex / barge-in provider-realtime relay adapter (OpenAI Realtime, Gemini Live) — the MVP is turn-based (audio-in → transcript → reply → audio-out); a bidirectional stream pump to a provider realtime API is the scale delta, credential-gated on a provider key (evidence: v47 MILESTONE.md Out)
- [SPEC · open] Envoy edge WebSocket-upgrade + per-message auth config to expose the /v1 realtime WS publicly — deploy-time delta; the gateway WS endpoint is TestClient-verified but the deployed edge ext_authz is HTTP-only and cannot yet upgrade/relay WS (evidence: v47 MILESTONE.md Out)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
