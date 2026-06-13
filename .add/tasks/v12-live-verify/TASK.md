# TASK: Live double-pass: exact Gemini-embed billing, non-chat soft alert, empty-key boot guard

slug: v12-live-verify · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: v12 live double-pass — prove the three v12 behavioral fixes END-TO-END through the
TLS edge (the same operator-run pattern as v3–v11 live-verify), two consecutive clean passes.

Closes the milestone exit criterion: "All behavioral items proven LIVE through the TLS edge,
two consecutive clean passes." Mirrors scripts/live_v11_verify.py structure: a stdlib stub in
a daemon thread, seed catalog+pricing, restart gateway so lifespan reads it, run checks with a
fresh tenant+key, run_id = int(time.time()). NO production source change.

Framings weighed: a dedicated `scripts/v12_embed_stub.py` (Gemini embed + :countTokens) + a
`live_v12_verify.py` with checks C1–C4 + a `docker-compose.e2e.v12.yml` overlay (chosen — the
established per-milestone pattern; new stub keeps the frozen v9 stub untouched and adds the
:countTokens verb the v12 mechanism needs) · extend the frozen v9_provider_stub.py with
countTokens (rejected — edits a frozen e2e artifact; the per-milestone-stub convention is a
fresh file) · skip the live pass and rely on unit/blast-radius (rejected — the milestone
REQUIRES a live double-pass; unit tests already passed, live is the independent confirmation).

Must:
<must>
  - C1 EXACT Gemini-embed billing: a /v1/embeddings request (provider=google) bills usage on
    the EXACT token count the stub's `:countTokens` returns (e.g. totalTokens=42), NOT
    ceil(chars/4); the usage_records row's prompt/total tokens == 42.
  - C2 NON-CHAT soft-budget alert: an embeddings request on a key with soft_budget_usd set,
    whose per-key spend has crossed it, writes exactly ONE `soft_budget_exceeded` alert_events
    row (dedupe_key `soft_budget:{key_id}:{YYYYMM}`); a repeat does NOT add a second row; the
    request still succeeds (advisory, no 402).
  - C3 EMPTY-KEY boot guard: starting the gateway with a configured-yet-EMPTY upstream key
    (GATEWAY_*_API_KEY="") fails fast at boot (non-zero exit / unhealthy) with a clear
    secret-free error; an ABSENT key boots normally.
  - C4 governance intact: a bad API key on /v1/embeddings is rejected 401 with no usage row
    (regression guard that the non-chat alert seam didn't weaken auth).
  - Double-pass: the script runs twice in sequence; both exit 0. run_id changes per run.
  - NO `src/gateway/**` change; the stub binds 127.0.0.1 only; placeholder keys only.
</must>
Reject:
<reject>
  - the stub omits/!=200 on `:countTokens` -> the gateway fails SAFE to the chars/4 estimate
    (C1 then asserts the estimate path is NOT what we want — so the stub MUST serve countTokens
    so the EXACT path is exercised; a missing-countTokens sub-check documents the fallback).
  - the soft-budget crossing repeats in the same month -> ONE alert_events row only (idempotent
    dedupe_key) — C2 asserts count stays 1.
  - the gateway boots with an empty upstream key -> startup MUST fail (C3); it must NEVER serve
    a request with an empty Bearer (the opaque 500 this milestone fixed).
  - a bad key reaches the embeddings upstream -> 401 before any upstream call / usage row (C4).
</reject>
After:
<after>
  - Two consecutive `live_v12_verify.py` passes exit 0; C1–C4 all green each pass.
  - The exact-token, non-chat-alert, and boot-guard behaviors are confirmed through the real
    TLS edge (not just unit tests), proving the wiring (deps, lifespan) is correct in a built image.
  - `src/gateway/**` byte-identical; no secret printed/committed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The e2e gateway image + TLS edge can be built and run in this environment for the live
    double-pass — lowest confidence because it needs `docker compose --build` (heavy) and the
    edge port wiring to match v11; mitigation: reuse the exact v11 overlay/compose pattern and
    its proven restart/seed helpers; cost if wrong: the artifacts (stub, overlay, script) are
    still authored and committed with the documented run procedure, and the double-pass is run
    as soon as the stack is up (the gate is the live PASS, recorded honestly).
  - [ ] Gemini `:countTokens` is the exact path the gateway calls for embeddings billing
    (confirm against gemini_upstream._count_gemini_tokens added in task 1) — the stub must
    answer the same `/v1beta/models/{model}:countTokens` shape.
  - [ ] driving the per-key spend over soft_budget for an embeddings request is done by seeding
    the Redis spend counter (usage:spend:key:{key_id}:{YYYYMM}) before the request, same as the
    chat soft-budget live checks in earlier milestones.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: C1 — exact Gemini-embed token billing
  Given a google-provider embedding model is seeded and the stub serves
        :countTokens → {"totalTokens": 42} and embedContent → an embedding
  When a tenant POSTs /v1/embeddings (input crosses chars/4 != 42) through the TLS edge
  Then the usage_records row bills total_tokens == 42 (the EXACT count, not ceil(chars/4))

Scenario: C2 — non-chat soft-budget alert fires
  Given a key with soft_budget_usd set and its per-key spend counter seeded past it
  When the tenant POSTs /v1/embeddings through the edge
  Then exactly one soft_budget_exceeded alert_events row exists
       (dedupe_key soft_budget:{key_id}:{YYYYMM}) and the request still returns 200
  And a repeat embeddings request does NOT add a second alert row (idempotent)

Scenario: C3 — empty-key boot guard rejects at startup
  Given the gateway is launched with GATEWAY_OPENROUTER_API_KEY="" (configured-yet-empty)
  When the container starts
  Then it fails fast / never becomes healthy, with a clear secret-free boot error
  And launching with the key ABSENT boots normally (provider disabled)

Scenario: C4 — governance intact on the non-chat path
  Given an invalid API key
  When it POSTs /v1/embeddings through the edge
  Then the response is 401 and NO usage_records row is written
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LIVE-VERIFY contract (operator-run; no production src change). Mirrors live_v11_verify.py.

NEW  scripts/v12_embed_stub.py — stdlib http.server, binds 127.0.0.1:9926 ONLY:
       POST /v1beta/models/{model}:countTokens   -> 200 {"totalTokens": 42}   (EXACT count)
       POST /v1beta/models/{model}:embedContent   -> 200 {"embedding": {"values": [...]}}
       POST /v1beta/models/{model}:batchEmbedContents -> 200 {"embeddings":[{"values":[...]}...]}
       GET  /__health -> 200
     (Auth header: x-goog-api-key — never ?key=. New file; frozen v9 stub untouched.)

NEW  infra/docker-compose.e2e.v12.yml — additive overlay (base + v4 + v5 + v6 + v12):
       gateway.environment:
         GATEWAY_GOOGLE_API_KEY: "stub-google-key"
         GATEWAY_GOOGLE_BASE_URL: "http://host.docker.internal:9926/v1beta"
         GATEWAY_OPENROUTER_API_KEY: "stub-openrouter-key"   # always-on default provider
     Placeholder keys only. The boot-guard check (C3) launches a SEPARATE one-shot gateway
     run with GATEWAY_OPENROUTER_API_KEY="" and asserts non-zero exit / unhealthy.

NEW  scripts/live_v12_verify.py — checks C1–C4 against https://localhost:8443 (envoy TLS):
       helpers reused from v11 pattern: record(), psql(), _restart_gateway_and_wait(),
       _poll_usage_tokens(), _wait_stub_healthy(); _seed_v12_models() seeds a google embed
       model + pricing; a fresh tenant+key per run; run_id = int(time.time()).
       C1: POST /v1/embeddings → _poll_usage_tokens == 42 (exact, not ceil(chars/4)).
       C2: seed usage:spend:key:{key_id}:{YYYYMM} past soft_budget → POST /v1/embeddings →
           psql COUNT(*) alert_events WHERE event_type='soft_budget_exceeded' AND key_id == 1;
           repeat → still 1; both requests 200.
       C3: run `docker compose ... run --rm -e GATEWAY_OPENROUTER_API_KEY="" gateway` (or a
           direct create_app boot) → assert it exits non-zero / logs EmptyUpstreamKeyError;
           absent-key control boots healthy.
       C4: POST /v1/embeddings with a bad key → 401, COUNT(*) usage_records for it == 0.

Schema: none new (usage_records + alert_events exist). HTTP surface: none. src/gateway/**: byte-identical.
Close rule: run live_v12_verify.py TWICE in sequence; BOTH exit 0 (records C1–C4 each pass).
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [contract] whether the e2e gateway image + Envoy TLS edge
build and run cleanly here for the double-pass (it needs `docker compose --build`); mitigation:
the overlay/stub/script copy the proven v9–v11 pattern verbatim (same ports idiom, same
restart/seed helpers), and C3's boot-guard sub-check is a separate one-shot run not the main
stack; if the stack can't come up, the three artifacts are still authored + committed with the
exact run procedure and the live PASS is recorded only when the double-pass actually exits 0
(never pre-stamped). Pure operator tooling: no `src/gateway/**` change.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: live C1–C4 (11 sub-checks) through the TLS edge, double-pass
Plan (the "tests" ARE the live script's recorded checks — red until the artifacts exist):
<test_plan>
  - C1a/b/c: /v1/embeddings 200 · billed exact 42 (not ceil(11/4)=3) · usage model_id correct
  - C2a/b/c/d: 200 (advisory) · 1 alert row · repeat 200 · still 1 (idempotent dedupe_key)
  - C3a/b: empty GATEWAY_OPENROUTER_API_KEY → non-zero exit (EmptyUpstreamKeyError) · absent → boots
  - C4a/b: bad key → 401 · usage_records delta == 0 (governance rejects before billing)
</test_plan>
Note: a unit "red" doesn't apply — this is an operator-run live harness; "red" = the script
fails until v12_embed_stub.py + the overlay + live_v12_verify.py exist and the stack is up.

Tests live in: `scripts/live_v12_verify.py` (+ `scripts/v12_embed_stub.py`, `infra/docker-compose.e2e.v12.yml`) · run via the documented double-pass.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): stub binds 127.0.0.1 ONLY; placeholder keys only; never log a
key (Google auth = x-goog-api-key header, never ?key=). NO `src/gateway/**` change.
Code lives in: `scripts/v12_embed_stub.py`, `scripts/live_v12_verify.py`, `infra/docker-compose.e2e.v12.yml`.
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.
Build-time correction: C4b's first heuristic (count rows for "unknown key_ids in last 60s")
false-positived on pass 2 (caught pass 1's legitimate rows in the shared e2e DB) → replaced
with a before/after total-count DELTA == 0, which isolates the bad-key request. No contract
change (C4b still asserts "no usage row for the bad key"); the assertion was made correct.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — LIVE DOUBLE-PASS through the TLS edge (https://localhost:8443):
      pass 1 = 11/11 (run_id 1781346461), pass 2 = 11/11 (run_id 1781346491); C1–C4 green each
- [x] coverage did not decrease — operator tooling; no unit coverage impact
- [x] no test or contract was altered during build — contract unchanged; C4b assertion
      corrected (delta vs cross-run heuristic), still asserting "no usage row for bad key"
- [x] concurrency / timing safe — script paces edge calls; stub in daemon thread; gateway
      restarted before checks so lifespan reads the seeded catalog; C4b uses a before/after
      delta (robust against the shared e2e DB / background flusher)
- [x] no exposed secrets / injection / deps — stub binds 127.0.0.1 only; placeholder keys
      only; Google auth via x-goog-api-key header (no ?key=); psql via parametrised run-scoped
      ids; no new packages
- [x] layering & deps — operator scripts + e2e overlay only; no `src/gateway/**` change
- [x] reviewed — auto-resolved under delegated auto mode; the live double-pass IS the
      independent evidence; no security finding

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — the v12 overlay routes google→127.0.0.1:9926; the gateway's :countTokens call
      hit the stub (C1 billed exact 42, proving the task-1 path runs in the built image); the
      non-chat alert seam wrote the alert_events row (C2, proving task-3 deps wiring in-image);
      the boot guard rejected empty key (C3, proving task-2 in create_app)
- [x] DEAD-CODE — no orphaned symbol; all stub routes + script helpers exercised by C1–C4
- [x] SEMANTIC — n/a (operator tooling)

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (delegated auto mode) · date: 2026-06-13 · evidence: live double-pass 11/11 ×2

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
