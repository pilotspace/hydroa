# TASK: JSON-mode live double-pass (e2e)

slug: json-mode-live-verify · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: JSON-mode live double-pass — prove response_format end-to-end through the real
Envoy TLS edge for provider=anthropic and provider=google: json_object + json_schema return
JSON-conformant `message.content` (finish "stop"), the Anthropic json_schema path coerces +
UNWRAPS with NO tool_calls leak, streaming JSON content per provider, tools+response_format
composition intact, OpenRouter no-rf byte-identical, governance intact. Operator-run harness;
NO production source change. Mirrors the v10 one-stateless-stub harness.

Framings weighed: ONE json-aware host stub on a NEW port (:9925) that inspects the request
(response_format present / a json_output tool present → return JSON) (chosen — stateless,
exactly the v10 pattern, distinct port avoids colliding with a residual v10 stub) · reuse the
v10 stub (rejected — different response shapes; a dedicated v11 stub is clearer) · stateful
turn tracking (rejected — v10 proved stateless inspection suffices).

Must:
<must>
  - A json-aware stub on 127.0.0.1:9925 (loopback ONLY) path-routes Anthropic /v1/messages
    (returns a `json_output` tool_use block whose input is the JSON object — exercising the
    gateway's coerce+unwrap), Gemini :generateContent/:streamGenerateContent (returns a text
    part that IS a JSON string — exercising responseMimeType), and OpenRouter
    /api/v1/chat/completions (plain no-rf, byte-identical). STATELESS.
  - An additive compose overlay (base+v4+v5+v6+v11) points all three provider base_urls at
    the stub with placeholder keys; the verify seeds 3 provider-tagged chat models + pricing,
    restarts the gateway so provider_resolver.refresh() reads them.
  - Checks through https://localhost:8443 with a fresh tenant+key: C1 Anthropic json_schema
    (message.content is JSON, finish "stop", NO tool_calls — coerce+unwrap proven), C2
    Anthropic json_schema streaming (delta.content JSON fragments + finish "stop", no
    tool_calls), C3 Gemini json_object/json_schema (message.content JSON via responseMimeType),
    C4 Gemini streaming (delta.content JSON fragments), C5 OpenRouter no-rf byte-identical, C6
    governance (bad key → 401).
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
  - The v11 milestone exit criteria are all proven LIVE; both passes exit 0.
  - The harness is committed; the stack composes additively and tears down cleanly.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The gateway's Anthropic coerce+unwrap round-trips LIVE: a json_output tool_use block from
    the stub is unwrapped into message.content with NO tool_calls leak and finish "stop", on
    BOTH the non-stream and stream paths — lowest confidence because it is the milestone's
    deepest new path (3 coordinated helper changes); if wrong: C1/C2 fail loudly (content
    missing or a tool_calls entry present) — no false pass.
  - [ ] The seed-then-restart resolver refresh (v9/v10 mechanism) carries to v11 on port 9925
    — confirm gateway healthy + models resolved after restart (held v9 + v10 first-try).
  - [ ] A stub returning a JSON-string text part for Gemini exercises responseMimeType without
    the gateway needing to validate the JSON — confirm message.content is the JSON string
    verbatim (the gateway forwards, does not re-validate — translate-don't-enforce).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: C1 Anthropic json_schema coerce+unwrap (non-stream)
  Given a provider=anthropic model and a request with response_format json_schema
  When the stub returns a json_output tool_use block whose input is the JSON object
  Then the gateway returns message.content as a JSON string (finish_reason "stop")
  And the response has NO tool_calls entry (the coercion tool did not leak)

Scenario: C2 Anthropic json_schema streaming
  Given a provider=anthropic streamed json_schema request
  When the SSE is read
  Then delta.content fragments carry the JSON (NOT delta.tool_calls) and finish_reason is "stop"

Scenario: C3 Gemini json_object / json_schema (non-stream)
  Given a provider=google model and a request with response_format
  When the stub returns a text part that is a JSON string
  Then the gateway returns message.content as that JSON string (finish_reason "stop")

Scenario: C4 Gemini json streaming
  Given a provider=google streamed json request
  When the SSE is read
  Then delta.content fragments carry the JSON and a terminal usage chunk precedes [DONE]

Scenario: C5 OpenRouter no-rf byte-identical
  Given a provider=openrouter request with NO response_format
  When it is dispatched
  Then it returns the plain response (content ok-openrouter) — byte-identical to v10

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
scripts/v11_json_stub.py — json-aware stub, bind 127.0.0.1:9925 ONLY (STATELESS)
  POST /api/v1/chat/completions                 -> OpenRouter chat.completion, usage 5/3/8, content "ok-openrouter"
  POST /v1/messages                             -> Anthropic Messages; usage 7/4
       request has a tool named "json_output"   -> tool_use block {name:"json_output", input:{city:"Paris", temp:18}}, stop_reason tool_use
       stream:true                              -> SSE: content_block_start(tool_use json_output) + input_json_delta frags + message_delta(stop_reason tool_use) + usage 7/4
       else                                     -> plain text answer
  POST /v1beta/models/{m}:generateContent       -> Gemini; usageMetadata 9/6/15
       request has generationConfig.responseMimeType -> text part = JSON string '{"city":"Paris","temp":18}'
       else                                     -> plain text
  POST /v1beta/models/{m}:streamGenerateContent -> SSE: one chunk text part = JSON string, usageMetadata 9/6/15
  GET  /__health                                -> {status:ok}

infra/docker-compose.e2e.v11.yml — additive overlay (base+v4+v5+v6+v11)
  GATEWAY_{ANTHROPIC,GOOGLE,OPENROUTER}_BASE_URL -> http://host.docker.internal:9925/...
  GATEWAY_{ANTHROPIC,GOOGLE,OPENROUTER}_API_KEY  -> placeholder (NEVER a real secret)

scripts/live_v11_verify.py — double-pass driver (run twice; both exit 0)
  preflight: assert stub addr == 127.0.0.1 else ERR_SECURITY_VIOLATION (HARD-STOP, no check)
  _seed_v11_models(): upsert v11-{anthropic,google,openrouter}-chat (active, provider-tagged) + pricing (idempotent)
  _restart_gateway_and_wait(): restart gateway -> lifespan provider_resolver.refresh() reads seeded models
  checks via https://localhost:8443 with a fresh tenant+key:
    C1 anthropic json_schema : message.content is JSON (parses), finish_reason "stop", NO tool_calls; billed 7/4
    C2 anthropic streaming   : delta.content JSON fragments (no delta.tool_calls), finish_reason "stop", terminal usage 7/4
    C3 gemini json           : message.content is JSON string, finish_reason "stop"; billed 9/6
    C4 gemini streaming      : delta.content JSON fragment + terminal usage 9/6
    C5 openrouter no-rf      : plain response content "ok-openrouter" billed 5/3 — byte-identical to v10
    C6 governance            : bad key -> 401, no usage row
  print "N/N checks passed (run_id=<int>)"; exit 0 only on all-pass
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [scenario] the gateway's Anthropic coerce+unwrap
round-trips LIVE — a json_output tool_use block from the stub is unwrapped into
message.content with NO tool_calls leak and finish "stop", on BOTH non-stream and stream
paths; why: it is the milestone's deepest new path (3 coordinated helper changes); cost if
wrong: C1/C2 fail loudly (content missing or a tool_calls entry present) — no false pass.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: live behavioral — all 6 checks (13 assertions) pass twice, exit 0.
This is an OPERATOR-RUN e2e harness: the "tests" ARE the live checks C1–C6 in
`scripts/live_v11_verify.py`; they run red (no response_format behavior served) against a
gateway built only to v10, and green once the v11 response_format translation (tasks 2+3)
is live. The unit suites that pin the translation shapes already ship with tasks 1–3
(response_format_translation 15, gemini_json_mode 9, anthropic_json_mode 10).
Plan (one check per scenario, asserting behavior not internals):
<test_plan>
  - C1 anthropic json_schema (non-stream): send response_format json_schema / assert message.content parses to JSON + finish "stop" + NO tool_calls (coerce+unwrap proven) + billed 7/4 on served id
  - C2 anthropic json_schema streaming: stream json_schema / read SSE / assert delta.content reassembles to JSON (no delta.tool_calls) + finish "stop" + terminal usage 7/4
  - C3 gemini json (non-stream): send response_format / assert message.content is JSON string + finish "stop" + billed 9/6
  - C4 gemini json streaming: stream response_format / read SSE / assert delta.content JSON fragment + terminal usage 9/6
  - C5 openrouter no-rf: dispatch no response_format / assert content "ok-openrouter" billed 5/3 — byte-identical to v10
  - C6 governance: bad key / assert 401 + no usage row
  - preflight: stub addr != 127.0.0.1 / assert ERR_SECURITY_VIOLATION HARD-STOP before any check
</test_plan>

Tests live in: `scripts/live_v11_verify.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the stub binds 127.0.0.1 ONLY (asserted in the verify
preflight) — a routable bind is an ERR_SECURITY_VIOLATION HARD-STOP before any check; no
placeholder key ever reaches a log/URL/usage row; the coercion tool name is a gateway
constant + the caller's own schema (no secret).
Code lives in: `scripts/v11_json_stub.py`, `scripts/live_v11_verify.py`, `infra/docker-compose.e2e.v11.yml`
Constraints: NO production source change (harness only); do NOT change any test or the
contract; stdlib + existing dev deps (httpx, psql via docker exec) only.

Built (all green, 13/13 ×2):
- scripts/v11_json_stub.py — stateless response_format-aware stub; request-inspection branch
  (_anthropic_has_coercion_tool / _gemini_has_response_mime_type) selects coercion tool_use
  vs JSON text part vs plain; Anthropic SSE with input_json_delta fragments for the
  json_output block; Gemini one-chunk JSON-string text SSE.
- infra/docker-compose.e2e.v11.yml — additive overlay, three provider base_urls → :9925.
- scripts/live_v11_verify.py — preflight loopback guard, idempotent seed, gateway restart
  for resolver refresh, C1–C6 coerce+unwrap + JSON-content + streaming + billing + governance.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — both passes 13/13: run_id=1781335689, run_id=1781335711, both exit 0
- [x] coverage did not decrease — harness-only; the v11 unit suites ship with tasks 1–3 (response_format_translation 15, gemini_json_mode 9, anthropic_json_mode 10); no production src touched here
- [x] no test or contract was altered during build — frozen v9/v10 suites stay green (no-rf byte-identical: C5 ok-openrouter 5/3)
- [x] concurrency / timing of the risky operation is safe — stateless stub (no shared mutable state); double-pass independent run_ids; Envoy 50 req/s honored (EDGE_PACE_S=0.05)
- [x] no exposed secrets, injection openings, or unexpected dependencies — placeholder keys only; loopback bind asserted; usage rows keyed by deployment/model id only; coercion tool name is a gateway constant + caller schema
- [x] layering & dependencies follow CONVENTIONS.md — harness in scripts/+infra/; no production src touched
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-13)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — coercion branch reached for Anthropic (C1 content JSON, finish stop, 0 tool_calls; C2 stub access log POST /v1/messages); responseMimeType branch reached for Gemini (C3/C4 JSON content; stub log :generateContent / :streamGenerateContent?alt=sse); verify imports stub via make_stub_server/start_stub_in_thread; overlay base_urls hit :9925 (stub access log confirmed each call)
- [x] DEAD-CODE (code) — every stub helper (anthropic coercion + text + SSE, gemini json + text + SSE, openrouter) exercised by C1–C6; no orphan symbol
- [x] SEMANTIC (prose / non-code) — read live_v11_verify.py + overlay + stub in full: loopback preflight present, seed idempotent (ON CONFLICT DO UPDATE), restart waits on health, _json_matches asserts content parses AND equals expected, _sse_has_tool_calls guards the no-leak invariant, all 6 checks assert the contracted shapes

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-provider response_format rate, coerce+unwrap
no-tool_calls-leak rate (Anthropic), JSON-content-parses rate, finish_reason="stop" on
structured output, terminal-usage-chunk presence on streamed JSON, billed tokens on served
id, 401 governance rate.
Spec delta for the next loop: the one-stateless-stub harness extends cleanly from tool-use
(v10) to response_format (v11) — request-inspection on the TRANSLATED shape
(json_output tool present / generationConfig.responseMimeType set) reliably selects the
structured-output branch. The Anthropic coerce+unwrap round-trips LIVE on BOTH non-stream
and stream with zero tool_calls leak (the milestone's least-sure flag — confirmed). The
gateway forwards JSON content verbatim without re-validating it (translate-don't-enforce
held). response_format strict-mode rejection and parallel-tool + response_format
co-existence remain unexercised (residual, not in scope here).

### Competency deltas
- [ADD · open] response_format (request-side native for Gemini, tool-coercion for Anthropic, passthrough for OpenRouter) fits the one-stateless-stub live harness with no new infra — evidence: 13/13 ×2, both passes exit 0, port :9925 seed-then-restart resolver refresh worked first-try.
- [TDD · open] the live checks served as the red→green suite for the coerce+unwrap path (red against a v10-only gateway, green after v11 tasks 2+3); the _sse_has_tool_calls guard makes the no-leak invariant observable, not just asserted-absent — evidence: C1/C2 NO tool_calls confirmed on both non-stream and stream.
- [SDD · open] freezing the harness contract (stub surfaces + overlay env + check list) let stub/overlay/verify be built independently and compose first-try — evidence: C1–C6 passed on the first live run after build, no harness rework.
