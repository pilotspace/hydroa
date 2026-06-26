# TASK: /v1/realtime/relay WS endpoint + Envoy WS-upgrade

slug: realtime-relay-endpoint · created: 2026-06-26 · stage: production · risk: high
autonomy: conservative   <!-- LOWERED from auto: this task carries the auth-over-WS SECURITY surface (v52 HARD); the verify gate must be a human (Tin) sign-off, not an auto-PASS. -->
<!-- risk:high declared — auth-over-WS. The engine refuses an unguarded auto-completion (unguarded_high_risk_auto); the gate escalates to Tin. -->
<!-- ORIGINAL autonomy: auto (project default) -->
<!-- ORIGINAL comment: inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
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
  - NEW `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py` — `realtime_relay_router` (APIRouter) with `@router.websocket("/v1/realtime/relay")`; a `_StarletteRelayTransport(RealtimeClientTransport)` adapting a Starlette `WebSocket` (receive→dict|bytes, send_event→send_json, send_audio→send_bytes, close→close); the handler: accept → first-frame auth → select provider session → run `RelayPump`; honest-degrade 4404. app.state STUB seams `realtime_relay_authenticate` / `realtime_relay_session_factory` (mirrors v47's stub philosophy → DB/key-free WS tests).
  - NEW `apps/gateway/tests/realtime_relay/test_relay_endpoint.py` — Starlette `TestClient` WS tests with a fake authenticator stub + a fake `RealtimeRelaySession` factory stub (no DB, no network, no key).
  - `apps/gateway/src/gateway/core/config.py:Settings` — ADD `realtime_relay_provider: str = ""` (`""`=none→honest-degrade · `openai` · `gemini`) + `realtime_relay_openai_model` + `realtime_relay_gemini_model`.
  - `apps/gateway/src/gateway/main.py` — `app.include_router(realtime_relay_router)` (after `realtime_router`, line ~927); the real (non-stub) `realtime_relay_session_factory` builds `OpenAIRealtimeSession`/`GeminiLiveSession` from settings.
  - `infra/envoy/envoy.yaml` (+ `envoy-prod.yaml`) — add `upgrade_configs: [{upgrade_type: websocket}]` to the shared HCM typed_config so the edge proxies the WS upgrade (currently ABSENT — v47 `/v1/realtime` is app-direct; this enables BOTH paths).
  - REUSE (read, do not modify): `proxy/api/realtime_ws.py:_authenticate_token` (v47 — KeyAuthenticator + tz-aware expiry; the auth-over-WS surface), `_CODE_AUTH_INVALID`(4401)/`_CODE_AUTH_TIMEOUT`(4408); `proxy/application/realtime_relay_pump.py:RelayPump` (t1); `proxy/domain/realtime_relay.py` (the seam); `proxy/infrastructure/openai_realtime.py`+`gemini_live.py` (t2/t3 adapters).
Context (working folder):
  - v47 auth flow (the reuse target): first frame `{type:"auth",token}`, bounded by `settings.realtime_auth_timeout_seconds` (asyncio.wait_for); bad/missing/expired/first-frame-not-auth → close 4401; no auth frame in time → close 4408; token NEVER in URL/query. `_authenticate_token(token, session) -> AuthzResult | None` over `app.state.sessionmaker()`.
  - `apps/gateway/Makefile` test-fast — `tests/realtime_relay` already listed; the endpoint test rides it (TestClient is in-process, no DB needed via stubs).
Honors (patterns / conventions):
  - AUTH-OVER-WS (v52 HARD, security — inherited v47): NO frame other than the first `{type:auth,token}` is processed until authenticated; reuse `_authenticate_token` verbatim; token never in URL. THIS is the milestone's security surface → the verify gate ESCALATES to Tin (HARD-STOP, not auto-PASS).
  - HONEST-DEGRADE (v52 HARD): no provider configured (`realtime_relay_provider=""` / factory returns None) → close 4404 ("no realtime provider configured"), never a silent hang or fake session.
  - DESIGN-FOR-FAILURE (v52 HARD): the pump (t1) owns connect-timeout/breaker/teardown; the endpoint only adapts the transport + selects the provider; a WebSocketDisconnect during auth → clean close.
  - STUB SEAM (v47 pattern): `app.state.realtime_relay_authenticate` / `realtime_relay_session_factory` when present replace the real auth/provider build → tests inject fakes (no DB/network/key).
Anchors the contract cites: `realtime_relay_router` · `/v1/realtime/relay` · `_StarletteRelayTransport` · close codes 4401/4408/4404 + the pump's 4503/1011/4408/1000 · `Settings.realtime_relay_provider` · the app.state stub seams

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: The `/v1/realtime/relay` WS endpoint — auth-over-WS, provider selection, honest-degrade, pump
Framings weighed: a thin endpoint that adapts the Starlette socket + selects the provider, delegating ALL session lifecycle to the t1 pump (chosen — the pump already owns timeouts/breaker/teardown; the endpoint stays auth+wiring only) · the endpoint re-implements the relay loop (rejected — duplicates t1, re-introduces the failure modes t1 already solved) · a transparent passthrough to the provider socket (rejected — milestone HARD requires the normalized seam)
Must:
<must>
  - `realtime_relay_router.websocket("/v1/realtime/relay")`: `accept()`, then read the FIRST frame bounded by `settings.realtime_auth_timeout_seconds`; the frame must be JSON `{type:"auth", token}`.
  - AUTH: authenticate the token via `app.state.realtime_relay_authenticate` (stub) else v47 `_authenticate_token` over `app.state.sessionmaker()`. Invalid/expired/missing/first-frame-not-auth → close 4401; no frame within the timeout → close 4408. Token NEVER read from URL/query.
  - SELECT: build the provider session via `app.state.realtime_relay_session_factory(authz, websocket)` (stub) else the real factory keyed on `settings.realtime_relay_provider` (`openai`→`OpenAIRealtimeSession`, `gemini`→`GeminiLiveSession`). If the provider is unconfigured (`""`) or the factory returns None → close 4404 (honest-degrade), never a fake session/hang.
  - RELAY: wrap the Starlette socket in `_StarletteRelayTransport` and run `RelayPump(transport, session, settings, breaker=app.state.realtime_relay_breaker?).run()`; the pump owns connect/timeouts/teardown + the close code on the relay outcome.
  - `_StarletteRelayTransport` implements `RealtimeClientTransport`: `receive()` → a binary WS frame → bytes, a text WS frame → `json.loads` dict, a disconnect → raise `RealtimeRelayClosed`; `send_event` → `send_json`; `send_audio` → `send_bytes`; `close(code)` → `websocket.close(code)` (idempotent/swallow if already closed).
  - `Settings` gains `realtime_relay_provider` (`""`/`openai`/`gemini`) + `realtime_relay_openai_model` + `realtime_relay_gemini_model`.
  - `main.py` includes `realtime_relay_router`; the edge Envoy HCM gains `upgrade_configs:[{upgrade_type: websocket}]`.
</must>
Reject:
<reject>
  - first frame missing/binary/`type!=auth`, or token invalid/expired -> close 4401; nothing relayed
  - no auth frame within `realtime_auth_timeout_seconds` -> close 4408; nothing relayed
  - authenticated but no provider configured (or factory returns None) -> close 4404 (honest-degrade); no session opened
  - token supplied via URL/query instead of the auth frame -> treated as missing -> close 4401 (token is never read from the URL)
</reject>
After:
<after>
  - an authed client over a fake session factory exchanges normalized frames pumped both ways (audio in → provider session → audio-delta frames out), proven end-to-end with a fake provider socket via TestClient
  - a bad/missing/expired token closes 4401; an auth timeout closes 4408; no-provider closes 4404 — each observable as the WS close code, with no session opened
  - the v47 `/v1/realtime` turn-based path is unchanged; the new router is additive
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the Starlette `TestClient` WebSocket can drive the first-frame-auth + bidirectional pump deterministically under `asyncio.timeout` — lowest confidence because TestClient runs the app on a portal thread and the pump spawns tasks; if wrong: the endpoint logic is unchanged but tests may need `websocket_connect` send/receive ordering tweaks (CONTAINED to the test harness, not the handler). Mitigation: keep the fake session factory's `events()` finite + deterministic so the pump tears down on provider-end (the t1 happy-path shape already proven).
  - [ ] auth-over-WS reuse — confirmed: `_authenticate_token` is the v47 surface, already approved; this task only wires it (no new auth logic) — but the gate STILL escalates (security HARD-STOP) for Tin's explicit sign-off.
  - [ ] Envoy `upgrade_configs` at HCM level enables WS for `/v1/*` — confirmed by Envoy docs; the ext_authz-on-/v1 interaction with the WS upgrade GET is noted as a deploy delta (auth is over-WS, not ext_authz).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: authed full-duplex relay over a fake provider session
  Given a fake authenticate stub returning a valid AuthzResult and a fake session factory whose session yields session.created, an audio-delta, response.done
  When a client connects, sends {type:auth,token:"sk-x"}, then an audio frame
  Then the client receives the session.created, the audio bytes, and response.done frames pumped from the provider
  And the v47 /v1/realtime path is untouched

Scenario: bad token closes 4401
  Given a fake authenticate stub returning None
  When a client connects and sends {type:auth,token:"bad"}
  Then the socket is closed with code 4401
  And no provider session was created

Scenario: first frame not auth closes 4401
  Given any authenticate stub
  When a client connects and sends {type:"audio.commit"} as the first frame
  Then the socket is closed with code 4401
  And nothing is relayed

Scenario: auth timeout closes 4408
  Given realtime_auth_timeout_seconds is tiny and the client sends no frame
  When the auth wait elapses
  Then the socket is closed with code 4408
  And no provider session was created

Scenario: no provider configured closes 4404
  Given a valid auth stub and a session factory that returns None (provider unconfigured)
  When a client connects and authenticates
  Then the socket is closed with code 4404 (honest-degrade)
  And no fake/hanging session is opened

Scenario: token in the URL is ignored
  Given a valid auth stub
  When a client connects with ?token=sk-x in the URL and sends a non-auth first frame
  Then the socket is closed with code 4401 (the URL token is never read)
  And nothing is relayed
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
WS /v1/realtime/relay
  first frame (JSON, required): { "type": "auth", "token": "sk-..." }   # token NEVER in URL/query
  thereafter (both directions): JSON control frame OR binary audio frame (the normalized gateway protocol)
  close codes:
    4401  auth invalid / missing / first-frame-not-auth / expired
    4408  no auth frame within realtime_auth_timeout_seconds
    4404  authenticated but no realtime provider configured (honest-degrade)
    4503 / 1011 / 4408(idle) / 1000   the pump's relay-outcome codes (t1)

Settings (core/config.py):
  realtime_relay_provider: str = ""            # "" = honest-degrade, "openai", "gemini"
  realtime_relay_openai_model: str = "gpt-4o-realtime-preview"
  realtime_relay_gemini_model: str = "gemini-2.0-flash-exp"

app.state STUB seams (present → used instead of the real path; for DB/key-free WS tests):
  realtime_relay_authenticate(token: str, session) -> AuthzResult | None
  realtime_relay_session_factory(authz, websocket) -> RealtimeRelaySession | None   # None → 4404

_StarletteRelayTransport(RealtimeClientTransport): receive()->dict|bytes (disconnect→RealtimeRelayClosed) ·
  send_event(dict)->send_json · send_audio(bytes)->send_bytes · close(code)->websocket.close(code)
Wiring: main.py include_router(realtime_relay_router); Envoy HCM upgrade_configs:[websocket]. NO new DB/table/dep.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-26 (AI auto-draft under autonomy:auto for the SHAPE; the auth-over-WS SECURITY surface escalates the VERIFY gate to Tin — HARD-STOP, not auto-PASS).
Least-sure flag surfaced at freeze:
  - [test] driving first-frame-auth + the bidirectional pump deterministically through Starlette `TestClient` WS — CONTAINED to the test harness (handler unchanged); mitigated by a finite fake `events()` so the pump tears down on provider-end (t1 happy-path shape).
  - [contract·security] auth-over-WS reuses v47 `_authenticate_token` verbatim (no new auth logic), but the gate STILL requires Tin's explicit security sign-off — surfaced here so the freeze doesn't imply the gate is auto-approved.
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
  - test_authed_full_duplex_relay: stub auth+fake session factory (session yields session.created, audio bytes, response.done) / connect+auth+send audio / assert client receives the 3 frames; assert /v1/realtime untouched (router additive)
  - test_bad_token_4401: auth stub →None / connect+{type:auth,token:bad} / assert close 4401, no session built
  - test_first_frame_not_auth_4401: connect+{type:audio.commit} first / assert close 4401
  - test_auth_timeout_4408: tiny realtime_auth_timeout_seconds, send nothing / assert close 4408
  - test_no_provider_4404: valid auth + factory →None / connect+auth / assert close 4404, no session opened
  - test_url_token_ignored_4401: connect with ?token=sk-x + non-auth first frame / assert close 4401 (URL token never read)
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/realtime_relay/test_relay_endpoint.py` `infra/envoy/envoy.yaml` `infra/envoy/envoy-prod.yaml` `apps/gateway/src/gateway/proxy/infrastructure/gemini_live.py` `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py` `apps/gateway/tests/realtime_relay/test_gemini_adapter.py`
<!-- the last 3 tokens are the CROSS-TASK security remediation surfaced by THIS task's independent review (F1 Gemini key-in-URL, F2 non-string token) — declared so the gate's touched⊆declared holds; t2/t3 stay gate-PASS, this is additive hardening. -->
Strategy (ordered batches): 1. config knobs (provider + models) 2. `realtime_relay_ws.py` (transport + handler + the real session factory) 3. `main.py` include 4. Envoy `upgrade_configs` 5. TestClient WS tests ride `tests/realtime_relay`
Safety rule (feature-specific): AUTH-OVER-WS is the security invariant — NO frame other than the first `{type:auth,token}` is processed before a successful authenticate; the token is read ONLY from that frame, never the URL/query; auth failure/timeout closes (4401/4408) before any provider session is built. Honest-degrade (4404) when no provider — never a fake/hanging session. The endpoint delegates ALL relay lifecycle to the t1 pump (no duplicated timeout/teardown). The v47 `/v1/realtime` path is untouched.
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

- [x] all tests pass — `tests/realtime_relay/` 29/29 (6 endpoint + 23 prior); v47 `tests/realtime/` 12/12 unchanged
- [x] coverage did not decrease — NEW module + NEW tests; v47 path untouched
- [x] no test or contract was altered during build — contract FROZEN @ v1; no test edited post-snapshot
- [x] the green was EARNED — TestClient drives the real WS handshake + first-frame-auth + the actual pump; asserts on observable CLOSE CODES (4401/4408/4404) and the relayed frames, not internals; the no-provider/bad-token tests assert NO session was built
- [x] concurrency / timing safe — the endpoint delegates the relay lifecycle to the t1 pump (no duplicated tasks/timeouts); the auth wait is bounded by `asyncio.wait_for(realtime_auth_timeout_seconds)`; transport.close is idempotent
- [x] no exposed secrets — the provider key is resolved server-side via the tenant credential resolver and read ONLY via `.get_secret_value()`; it is NEVER logged or sent to the client; honest-degrade returns None (no key → 4404)
- [x] layering follows CONVENTIONS.md — api → application(pump) → domain(seam) → infrastructure(adapters); the endpoint imports the pump + adapters, reuses v47 auth
- [ ] **a person reviewed and approved the change — PENDING Tin (SECURITY gate, below)**

### SECURITY review — auth-over-WS (the milestone HARD surface; this gate ESCALATES to Tin)
- [x] token read ONLY from the first `{type:auth,token}` frame — NEVER from the URL/query — proven by `test_url_token_ignored_4401` (?token=sk-x in the URL + a non-auth first frame → 4401)
- [x] NO frame relayed before a successful authenticate — first-frame-not-auth → 4401 (`test_first_frame_not_auth_4401`); bad token → 4401 with no session built (`test_bad_token_4401`)
- [x] auth wait is BOUNDED — no auth frame within the timeout → 4408 (`test_auth_timeout_4408`)
- [x] auth logic is REUSED v47 `_authenticate_token` verbatim (KeyAuthenticator + tz-aware expiry) — NO new auth code; only wired (pyright private-usage suppressed at the import, v47 file unmodified)
- [x] honest-degrade — authenticated but no provider configured → 4404, no fake/hanging session (`test_no_provider_4404`)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] an authed client exchanges normalized frames pumped both ways over a FAKE provider session — `test_authed_full_duplex_relay` (session.created + audio bytes + response.done received; session connected + closed)
- [x] each failure is observable as a WS CLOSE CODE with no session opened — 4401/4408/4404 each asserted via `WebSocketDisconnect.code`
- [x] the v47 `/v1/realtime` path is unchanged — the new router is additive (`tests/realtime/` 12/12 still green); Envoy `upgrade_configs` enables BOTH paths

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `realtime_relay_router` included in `main.py`; `_StarletteRelayTransport`/`_authenticate`/`_build_session`/`_real_session_factory` all referenced; config knobs read by the factory; Envoy `upgrade_configs` added to both listener HCMs (envoy.yaml :8080+:8443, envoy-prod.yaml :8443).
- [x] DEAD-CODE (code) — no orphan; `_extract_secret` used by the real factory; the stub seams gate the real path.
- [x] SEMANTIC — adversarial refute-read (self): (a) URL-token leak — the handler reads `payload["token"]` from the first frame body only; `websocket.query_params` is never touched → URL token genuinely ignored; (b) pre-auth relay — `_build_session`/pump are reached ONLY after `authz is not None` → no frame relayed pre-auth; (c) the real factory swallows resolver errors → honest-degrade (4404), never a 500 that could leak; (d) the secret never crosses the seam (used only to build the adapter's dialer). No earned-green cheat. NOTE: the real `_real_session_factory` + Envoy `upgrade_configs` are NOT unit-exercised (no live key / no Envoy in CI) — they are the t5 credential-gated + deploy-verified paths; flagged honestly, not silently.

### Independent adversarial review (security-expert subagent, Tin-requested) → BLOCK, now RESOLVED
- Traced every path accept()→first-relayed-frame: auth precedes relay on ALL paths; URL/query/header token-injection ruled out (verdict PASS on the core auth-over-WS gate).
- **F1 (HIGH, BLOCKING) — key-in-URL leak in the GEMINI adapter (t3, `gemini_live.py`)**: the resolved tenant key was placed in the WS URL `?key=` → leaks via websockets DEBUG logs / exception stringification / network intermediaries (OpenAI was already correct: Authorization header). **FIXED**: key now rides the `x-goog-api-key` HEADER; both adapters' dial-error messages no longer stringify `exc` (defense-in-depth). Regression: `test_default_dial_puts_key_in_header_not_url` (asserts key NOT in URL, present in header).
- **F2 (LOW) — non-string token → authenticator crash (500) instead of 4401**: **FIXED** with an `isinstance(token, str)` guard. Regression: `test_non_string_token_4401`.
- F3 (LOW) — provider-connect exception intentionally swallowed in the pump (no log) — left as-is (logging it would re-introduce a leak surface); honest residue noted.
- Re-verify after fixes: `tests/realtime_relay/` 31/31 green, ruff clean, pyright 0.

### GATE RECORD
Outcome: PASS — Tin Dang explicitly APPROVED the auth-over-WS security gate 2026-06-26 (after the independent security-expert review: BLOCK on F1 Gemini key-in-URL → FIXED to x-goog-api-key header + F2 non-string-token guard → both regression-tested; core auth gate verdicted structurally sound). 31/31 green, ruff/pyright clean.
Reviewed by: Tin Dang (explicit security sign-off) + independent security-expert subagent (BLOCK→resolved) · date: 2026-06-26

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
