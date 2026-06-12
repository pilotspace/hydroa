# TASK: Tool-use live double-pass (e2e)

slug: tool-use-live-verify · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tool-use live double-pass — prove a FULL function-calling round-trip
(tools → tool_calls → role:"tool" follow-up → final answer) through the real Envoy
TLS edge for provider=anthropic and provider=google, plus streaming tool-call deltas
per provider, billing on the served id, OpenRouter no-tools byte-identical, and
governance intact. Operator-run harness; NO production source change.

Framings weighed: ONE tool-aware host stub that decides by inspecting the request
(tool-result present → final answer; tools present → tool call) (chosen — stateless,
mirrors the v9 one-stub harness) · a stateful stub tracking turns server-side (rejected
— needs reset logic, flaky across the double-pass) · per-provider stubs (rejected — v9
proved one path-routing stub suffices).

Must:
<must>
  - A tool-aware stub on 127.0.0.1:9924 (loopback ONLY) path-routes Anthropic
    /v1/messages, Gemini :generateContent/:streamGenerateContent, and OpenRouter
    /api/v1/chat/completions; it returns a tool call on turn 1 (tools present, no tool
    result) and a final text answer once a tool result is present — STATELESS.
  - An additive compose overlay (base+v4+v5+v6+v10) points all three provider base_urls
    at the stub with placeholder keys; the verify seeds 3 provider-tagged chat models +
    pricing, restarts the gateway so provider_resolver.refresh() reads them.
  - Checks through https://localhost:8443 with a fresh tenant+key: C1 Anthropic
    round-trip (tool_calls turn1 + final text turn2 + billed 7/4 on served id), C2
    Anthropic streaming (delta.tool_calls fragments + terminal usage), C3 Gemini
    round-trip (synth-id tool_calls + final text + billed 9/6), C4 Gemini streaming
    (one combined fragment), C5 OpenRouter no-tools byte-identical (5/3), C6 governance
    (bad key → 401).
  - TWO consecutive clean passes; each fresh run_id; idempotent seed.
</must>
Reject:
<reject>
  - the stub bound to anything other than 127.0.0.1 -> "ERR_SECURITY_VIOLATION" (HARD-STOP
    before any check; no provider stub may listen on a routable interface).
  - any placeholder key appearing in a log/URL/usage row -> security HARD-STOP (foundation rule).
</reject>
After:
<after>
  - The v10 milestone exit criteria are all proven LIVE; both passes exit 0 (18/18 ×2).
  - The harness is committed; the stack composes additively and tears down cleanly.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A STATELESS request-inspection stub correctly distinguishes turn 1 from turn 2 by
    the presence of a translated tool result (Anthropic tool_result block / Gemini
    functionResponse part) — lowest confidence because it depends on the gateway's
    request translation emitting exactly those shapes; if wrong: the stub returns a tool
    call on the follow-up turn and C1b/C3b fail loudly (no false pass). VALIDATED: 18/18 ×2.
  - [x] The seed-then-restart resolver refresh (the v9 mechanism) carries to v10 with the
    new port — confirmed first-try (gateway healthy after restart, models resolved).
  - [x] One stub on a new port (:9924) avoids colliding with a residual v9 stub — distinct
    port + fresh overlay; no collision observed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: C1 Anthropic tool round-trip
  Given a provider=anthropic chat model and a request with tools+tool_choice
  When turn 1 is sent, then turn 2 with the role:"tool" result
  Then turn 1 returns OpenAI tool_calls (name get_weather, args {city:Paris}, id, finish_reason tool_calls)
  And turn 2 returns the final text answer (finish_reason stop), billed 7/4 on the served id

Scenario: C2 Anthropic tool streaming
  Given a provider=anthropic streamed request with tools
  When the SSE is read
  Then delta.tool_calls fragments stream (first id+name, then arguments) and a terminal usage chunk (7/4) precedes [DONE]

Scenario: C3 Gemini tool round-trip
  Given a provider=google chat model and a request with tools
  When turn 1 then the role:"tool" follow-up are sent
  Then turn 1 returns tool_calls with a SYNTHESIZED id; turn 2 returns the final text; billed 9/6 on the served id

Scenario: C4 Gemini tool streaming
  Given a provider=google streamed request with tools
  When the SSE is read
  Then one combined delta.tool_calls fragment (id+name+args) streams + a terminal usage chunk (9/6)

Scenario: C5 OpenRouter no-tools byte-identical
  Given a provider=openrouter request with NO tools
  When it is dispatched
  Then it returns the plain v9 response (content ok-openrouter) billed 5/3 — byte-identical to v9

Scenario: C6 governance intact
  Given a bad API key
  When a chat request is sent
  Then it is rejected 401 and no usage row is written

Scenario: stub bound off-loopback is rejected
  Given the stub server address is not 127.0.0.1
  When the verify starts
  Then it HARD-STOPs with ERR_SECURITY_VIOLATION before any check
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Harness contract — operator-run; NO production source change. Three artifacts:

```
scripts/v10_tool_stub.py — tool-aware stub, bind 127.0.0.1:9924 ONLY (STATELESS)
  POST /api/v1/chat/completions                 -> OpenRouter chat.completion, usage 5/3/8, content "ok-openrouter"
  POST /v1/messages                             -> Anthropic Messages; usage 7/4
       request has tool_result block            -> final text  {content:[{type:text,text:"It is sunny in Paris."}], stop_reason:end_turn}
       else request has tools                   -> tool_use     {content:[{type:tool_use,id,name:get_weather,input:{city:Paris}}], stop_reason:tool_use}
       stream:true                              -> SSE: message_start, content_block_start(tool_use), 2× input_json_delta, content_block_stop, message_delta(usage 7/4), message_stop
  POST /v1beta/models/{m}:generateContent       -> Gemini; usageMetadata 9/6/15
       request has functionResponse part        -> final text  {parts:[{text:"It is sunny in Paris."}]}
       else request has tools                   -> functionCall {parts:[{functionCall:{name:get_weather,args:{city:Paris}}}]}
  POST /v1beta/models/{m}:streamGenerateContent -> SSE: one chunk parts:[{functionCall}], usageMetadata 9/6/15
  GET  /__health                                -> {status:ok}

infra/docker-compose.e2e.v10.yml — additive overlay (base+v4+v5+v6+v10)
  GATEWAY_{ANTHROPIC,GOOGLE,OPENROUTER}_BASE_URL -> http://host.docker.internal:9924/...
  GATEWAY_{ANTHROPIC,GOOGLE,OPENROUTER}_API_KEY  -> placeholder (NEVER a real secret)

scripts/live_v10_verify.py — double-pass driver (run twice; both exit 0)
  preflight: assert stub addr == 127.0.0.1 else ERR_SECURITY_VIOLATION (HARD-STOP, no check)
  _seed_v10_models(): upsert v10-{anthropic,google,openrouter}-chat (active, provider-tagged) + pricing (idempotent)
  _restart_gateway_and_wait(): restart gateway -> lifespan provider_resolver.refresh() reads seeded models
  checks via https://localhost:8443 with a fresh tenant+key:
    C1 anthropic round-trip : turn1 tool_calls(get_weather,{city:Paris},id,finish_reason tool_calls); turn2 final text(finish_reason stop); billed 7/4 on served id
    C2 anthropic streaming  : delta.tool_calls fragments (id+name, then args) + terminal usage chunk 7/4 before [DONE]
    C3 gemini round-trip    : turn1 tool_calls SYNTH id; turn2 final text; billed 9/6 on served id
    C4 gemini streaming     : one combined delta.tool_calls fragment (id+name+args) + terminal usage chunk 9/6
    C5 openrouter no-tools  : plain v9 response content "ok-openrouter" billed 5/3 — byte-identical to v9
    C6 governance           : bad key -> 401, no usage row
  print "N/N checks passed (run_id=<int>)"; exit 0 only on all-pass
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [scenario] a STATELESS request-inspection stub
correctly distinguishes turn 1 from turn 2 by the presence of a translated tool result
(Anthropic tool_result block / Gemini functionResponse part) — why: it depends on the
gateway's request translation emitting exactly those shapes; cost if wrong: the stub
returns a tool call on the follow-up turn and C1b/C3b fail loudly (no false pass).
VALIDATED LIVE: 18/18 ×2.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: live behavioral — all 6 checks (18 assertions) pass twice, exit 0.
This is an OPERATOR-RUN e2e harness: the "tests" ARE the live checks C1–C6 in
`scripts/live_v10_verify.py`; they run red (no tool behavior served) against a gateway
built only to v9, and green once the v10 tool translation (tasks 2+3) is live. The unit
suites that pin the translation shapes already ship with tasks 1–3 (tool_translation 16,
anthropic_tool_use 13, gemini_tool_use 11).
Plan (one check per scenario, asserting behavior not internals):
<test_plan>
  - C1 anthropic round-trip: send tools turn1 / send role:tool turn2 / assert tool_calls then final text + billed 7/4 on served id
  - C2 anthropic streaming: stream tools / read SSE / assert delta.tool_calls fragments + terminal usage 7/4 before [DONE]
  - C3 gemini round-trip: send tools turn1 / send role:tool turn2 / assert SYNTH-id tool_calls then final text + billed 9/6
  - C4 gemini streaming: stream tools / read SSE / assert one combined fragment + terminal usage 9/6
  - C5 openrouter no-tools: dispatch no tools / assert content "ok-openrouter" billed 5/3 — byte-identical to v9
  - C6 governance: bad key / assert 401 + no usage row
  - preflight: stub addr != 127.0.0.1 / assert ERR_SECURITY_VIOLATION HARD-STOP before any check
</test_plan>

Tests live in: `scripts/live_v10_verify.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the stub binds 127.0.0.1 ONLY (asserted at module import
AND re-checked in the verify preflight) — a routable bind is an ERR_SECURITY_VIOLATION
HARD-STOP; no placeholder key ever reaches a log/URL/usage row.
Code lives in: `scripts/v10_tool_stub.py`, `scripts/live_v10_verify.py`, `infra/docker-compose.e2e.v10.yml`
Constraints: NO production source change (harness only); do NOT change any test or the
contract; stdlib + existing dev deps (httpx, psycopg via docker exec) only.

Built (all green, 18/18 ×2):
- scripts/v10_tool_stub.py — stateless tool-aware stub; request-inspection branch
  (_anthropic_has_tool_result / _gemini_has_function_response) selects tool-call vs final
  text; Anthropic SSE with input_json_delta fragments; Gemini one-chunk functionCall SSE.
- infra/docker-compose.e2e.v10.yml — additive overlay, three provider base_urls → :9924.
- scripts/live_v10_verify.py — preflight loopback guard, idempotent seed, gateway restart
  for resolver refresh, C1–C6 round-trip + streaming + billing + governance checks.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — both passes 18/18: run_id=1781291241, run_id=1781291262
- [x] coverage did not decrease — harness-only; full unit suite 668 passed @ 82.96% (tasks 1–3)
- [x] no test or contract was altered during build — frozen v9 suites stay green (no-tools byte-identical)
- [x] concurrency / timing of the risky operation is safe — stateless stub (no shared mutable state); double-pass independent run_ids; Envoy 50 req/s honored
- [x] no exposed secrets, injection openings, or unexpected dependencies — placeholder keys only; loopback bind; usage rows keyed by deployment/model id only
- [x] layering & dependencies follow CONVENTIONS.md — harness in scripts/+infra/; no production src touched
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-13)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — stub branch reached for both providers (live SSE + JSON observed); verify imports stub via make_stub_server/start_stub_in_thread; overlay base_urls hit :9924 (stub access log confirmed each call)
- [x] DEAD-CODE (code) — every stub helper (anthropic/gemini/openrouter, tool + final + SSE) exercised by C1–C6; no orphan symbol
- [x] SEMANTIC (prose / non-code) — read live_v10_verify.py + overlay in full: loopback preflight present, seed idempotent (ON CONFLICT DO UPDATE), restart waits on health, all 6 checks assert the contracted shapes

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-provider tool-call rate, terminal-usage-chunk
presence on streamed tool calls, finish_reason=tool_calls correctness, billed tokens on
served id, 401 governance rate.
Spec delta for the next loop: a single STATELESS request-inspection stub scales to
multi-turn function-calling across 3 providers — no server-side turn state needed; the
translated tool-result shape (Anthropic tool_result block / Gemini functionResponse part)
is a reliable turn discriminator. Gemini same-name parallel calls remain name-ambiguous on
return (residual freeze risk, not exercised here).

### Competency deltas
- [ADD · open] live e2e proof of a multi-turn protocol fits the one-stateless-stub harness pattern (request-inspection turn discrimination) — evidence: 18/18 ×2, both passes exit 0, no turn-state bug.
- [TDD · open] operator-run live checks served as the red→green suite for cross-provider translation (red against v9-only gateway, green after v10 tasks 2+3) — evidence: C1–C4 failed pre-build, passed post-build; C5 byte-identical throughout.
- [SDD · open] the frozen harness contract (stub surfaces + overlay env + check list) let the stub/overlay/verify be built independently and compose first-try — evidence: seed-then-restart resolver refresh worked first-try on the new :9924 port.
