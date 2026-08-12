# TASK: Credential-gated live full-duplex round-trip

slug: realtime-relay-live-verify · created: 2026-06-26 · stage: production
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
  - NEW `apps/gateway/tests/realtime_relay/test_relay_live.py` — a skip-gated, credential-gated live harness. Module-level `pytestmark = pytest.mark.skipif(not os.getenv("GATEWAY_REALTIME_RELAY_LIVE"), ...)` (mirrors v51 `test_artifacts_s3_live.py`). When a key IS present, it dials the REAL provider realtime WS via the adapter's default `ws_connect` and proves a minimal round-trip (connect → send a session/setup frame → receive at least one provider event → aclose). Ships SKIPPED without a key.
  - REUSE (read, do not modify): `proxy/infrastructure/openai_realtime.py:OpenAIRealtimeSession` + `gemini_live.py:GeminiLiveSession` (t2/t3 — the real default dialers); `proxy/infrastructure/realtime_ws_client.py:connect_websocket` (the real WS dial).
Context (working folder):
  - Env gates: `GATEWAY_REALTIME_RELAY_LIVE` (master on/off) + `GATEWAY_REALTIME_RELAY_LIVE_PROVIDER` (`openai`|`gemini`) + the provider key (`OPENAI_API_KEY` / `GEMINI_API_KEY`). Absent → the whole module SKIPS (a documented HARD-STOP for real verification, NOT a silent gap).
  - `apps/gateway/Makefile` test-fast already includes `tests/realtime_relay` — the live module rides it and SKIPS cleanly in CI (no key).
Honors (patterns / conventions):
  - CREDENTIAL-GATED LIVE-VERIFY (v52 HARD, Tin): the adapters shipped code-complete + unit-tested (t2/t3); THIS is the skip-gated real round-trip, ready to run when a key exists. A SKIP is an honest, recorded outcome — never a fake pass.
  - DESIGN-FOR-FAILURE: the live test bounds the round-trip with `asyncio.timeout` so a hung provider fails the test rather than hanging CI.
Anchors the contract cites: `test_relay_live` skip-gate envs · the `OpenAIRealtimeSession`/`GeminiLiveSession` default dialers · the minimal connect→send→recv→aclose round-trip

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Credential-gated live round-trip harness for the realtime relay adapters
Framings weighed: a module-level `skipif` on the env gates (chosen — mirrors v51's live-verify; the module SKIPS cleanly in CI, runs the real dial only when a key is present) · an always-run test with mocked network (rejected — that's already t2/t3's unit coverage; this task's whole point is the REAL provider) · a standalone script outside pytest (rejected — a skipped pytest module is the documented, discoverable HARD-STOP marker)
Must:
<must>
  - The module SKIPS entirely unless `GATEWAY_REALTIME_RELAY_LIVE` is set (master gate) — so CI / no-key runs ship it SKIPPED, never failed, never a fake pass.
  - When enabled, it reads `GATEWAY_REALTIME_RELAY_LIVE_PROVIDER` (`openai`|`gemini`) + the matching key env (`OPENAI_API_KEY`/`GEMINI_API_KEY`); a missing key for the chosen provider SKIPS with a clear reason.
  - The live test builds the real adapter (default `ws_connect`), and within an `asyncio.timeout` proves a minimal round-trip: `connect()` → send a session/setup control frame → receive AT LEAST ONE provider event via `events()` → `aclose()`.
  - It asserts the received event is a normalized gateway frame (a dict control frame OR audio bytes) — i.e. the adapter translated provider wire → gateway frame end-to-end against the REAL provider.
</must>
Reject:
<reject>
  - `GATEWAY_REALTIME_RELAY_LIVE` unset -> the whole module SKIPS (documented HARD-STOP for real verification); collection still succeeds
  - provider chosen but its key env missing -> SKIP with a clear reason (never a false green)
  - the real provider hangs past the round-trip `asyncio.timeout` -> the test FAILS (not a hang) — surfaces a real breakage
</reject>
After:
<after>
  - with no key (CI): `pytest tests/realtime_relay/test_relay_live.py` collects and reports SKIPPED — zero failures, zero network
  - with a real key: the chosen adapter completes connect→send→recv→aclose and the received frame is a normalized gateway frame
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ a single session/setup frame is enough to elicit at least one provider event within the timeout — lowest confidence because each provider's "first event" trigger differs (OpenAI emits `session.created` on connect; Gemini emits `setupComplete` after the setup frame); if wrong: the test times out → a clear FAIL pointing at the provider/frame, CONTAINED to this test (adapters + endpoint unaffected). Mitigation: accept ANY first event (session.created/setupComplete/audio/transcript) as the round-trip proof, and send the setup frame immediately after connect.
  - [ ] env-gate names — confirmed: mirrors v51's `GATEWAY_*_LIVE`-style gate + standard `OPENAI_API_KEY`/`GEMINI_API_KEY`.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: no key configured → module skips cleanly
  Given GATEWAY_REALTIME_RELAY_LIVE is unset
  When pytest collects tests/realtime_relay/test_relay_live.py
  Then the module is reported SKIPPED with a documented reason
  And no network call is made and no failure is raised

Scenario: provider chosen but its key missing → skip with reason
  Given GATEWAY_REALTIME_RELAY_LIVE=1 and provider=openai but OPENAI_API_KEY unset
  When the live test runs
  Then it SKIPS with a clear "OPENAI_API_KEY not set" reason
  And nothing is dialed

Scenario: real key → live round-trip proves end-to-end translation
  Given GATEWAY_REALTIME_RELAY_LIVE=1, a provider, and its real key
  When the live test connects, sends a setup frame, and reads events()
  Then it receives at least one NORMALIZED gateway frame (dict control or audio bytes) within the timeout
  And the session is aclose()d cleanly

Scenario: real provider hangs → test fails, not hangs
  Given an enabled live run where the provider never emits an event
  When the round-trip asyncio.timeout elapses
  Then the test FAILS with a timeout (CI is never hung)
  And no socket is leaked
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# A skip-gated pytest module (NO production code, NO new app surface).

tests/realtime_relay/test_relay_live.py
  pytestmark = pytest.mark.skipif(not os.getenv("GATEWAY_REALTIME_RELAY_LIVE"),
                                  reason="live realtime relay not enabled (set GATEWAY_REALTIME_RELAY_LIVE)")
  env: GATEWAY_REALTIME_RELAY_LIVE (master) · GATEWAY_REALTIME_RELAY_LIVE_PROVIDER (openai|gemini) ·
       OPENAI_API_KEY | GEMINI_API_KEY (per provider)
  test_live_round_trip():
    build OpenAIRealtimeSession|GeminiLiveSession (default ws_connect) ·
    async with asyncio.timeout(N): connect() → send setup/session frame → first = next(events()) → aclose()
    assert isinstance(first, dict) or isinstance(first, (bytes, bytearray))   # a normalized gateway frame
Errors: provider key missing → pytest.skip; provider hang → asyncio.timeout → test FAILS. NO new dep/DB/table.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-26 (AI auto-draft under autonomy:auto; low-risk skip-gated harness, ships SKIPPED — the credential-gated HARD-STOP Tin locked at milestone kickoff).
Least-sure flag surfaced at freeze:
  - [test] which provider event arrives FIRST within the timeout — accept ANY first event as round-trip proof (provider-agnostic); CONTAINED to this test, the unit suites + endpoint are unaffected.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a (skip-gated live harness — the artifact IS the test; CI exercises the SKIP path)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_live_round_trip: module-level skipif gate (no GATEWAY_REALTIME_RELAY_LIVE → SKIP); when enabled, build the chosen real adapter, connect→send setup→read first event under asyncio.timeout→aclose, assert the first event is a normalized gateway frame (dict|bytes)
  - (negative paths are SKIP outcomes, not separate tests): no master gate → whole module skips; provider key missing → pytest.skip(reason) inside the test
  - GREEN in CI = the module COLLECTS and reports SKIPPED (no failure, no network)
</test_plan>

Tests live in: `./tests/` · the module COLLECTS green and SKIPS without a key (the "red→green" here = collection succeeds + skip path clean).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/tests/realtime_relay/test_relay_live.py`
Strategy (ordered batches): 1. the skip-gated live module (one round-trip test, provider-selected via env) — rides the existing test-fast `tests/realtime_relay`
Safety rule (feature-specific): the module MUST skip cleanly with NO network and NO failure when the master gate is unset (CI default); the live path bounds the round-trip with `asyncio.timeout` so a hung provider FAILS rather than hangs; no secret is logged (the adapter holds the key).
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

- [x] all tests pass — `tests/realtime_relay/` 31 passed + 1 SKIPPED (the live module); whole-suite green
- [x] coverage did not decrease — a test-only addition; no production code touched
- [x] no test or contract was altered during build — the module IS the artifact (skip-gated harness); nothing else touched
- [x] the green was EARNED — the SKIP is the honest CI outcome (master gate unset → whole module skips, zero network); the live path is real (builds the actual adapter default-dialer, bounds with `asyncio.timeout`, asserts a normalized gateway frame — not a mock). No fake pass.
- [x] concurrency / timing safe — the round-trip is bounded by `asyncio.timeout(30s)`; `aclose()` runs in a `finally` (no leaked socket on timeout/failure)
- [x] no exposed secrets — the key is read from env into the adapter only; never logged; the adapter (post-F1 fix) puts it in a header, not a URL
- [x] layering follows CONVENTIONS.md — a test reusing the t2/t3 adapters' public surface; no new production dependency
- [x] reviewed — AI self-review; trivial low-risk skip-gated harness (no security/concurrency surface beyond the bounded timeout)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] with no key, the module COLLECTS and reports SKIPPED with a documented reason — confirmed: `1 skipped in 0.02s`, zero network
- [x] when enabled, it builds the chosen real adapter, completes connect→send→recv→aclose under a timeout, and asserts a normalized gateway frame — confirmed by reading the test body (the live path is real, not stubbed)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_build_live_session` selects OpenAI/Gemini by env and reuses their default dialers; `test_live_round_trip` exercises the seam (connect/send_client_event/events/aclose).
- [x] DEAD-CODE (code) — no orphan; the helper + the single test are both referenced/run; both provider branches reachable by env.
- [x] SEMANTIC — read in full: the SKIP path is unconditional without the master gate (module-level `pytestmark`); a missing provider key → `pytest.skip(reason)` (honest, not a false green); the timeout makes a provider hang a FAIL, not a CI hang; `aclose()` in `finally` prevents a socket leak. Matches §1/§2/§3.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (AI auto-gate under autonomy:auto — low-risk skip-gated harness, ships SKIPPED per the credential-gated HARD-STOP) · date: 2026-06-26

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
