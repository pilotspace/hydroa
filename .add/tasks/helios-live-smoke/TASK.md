# TASK: Live double-pass: Helios drives a coding session through the proxy

slug: helios-live-smoke · created: 2026-06-23 · stage: production
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
  - `scripts/live_helios_smoke.py` (NEW) — operator-run live verifier; mirrors `scripts/live_v25_verify.py`
    (signup→login→`/admin/keys`→`PUT /admin/provider-keys/{provider}`→chat) and `scripts/live_v9_verify.py`
    (Gemini chat + stream + usage-row poll via psql). Exit 0=reconciled · 1=fail · 2=key absent. Double-pass.
  - `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py:167` — `PUT /admin/provider-keys/{provider}`
    (OWNER-only): create-or-replace BYOK credential. Body `{"secret": <gemini-key>}`. provider key = `"google"`.
  - `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py:138` — `_DEFAULT_BASE_URL =
    https://generativelanguage.googleapis.com/v1beta`; auth = `x-goog-api-key` HEADER from the per-request
    credential contextvar (BearerCredential). No Vertex/ADC — AI Studio key only. Live target = REAL endpoint.
  - `apps/gateway/src/gateway/main.py:570` — `_chat_adapters["google"] = GeminiCompletionUpstream(...)` wired
    UNCONDITIONALLY; credential resolved per-request from the contextvar (BYOK), not a static config field.
  - The v34 src under test (already gate-PASS, tasks 2–6): reasoning/prompt-cache passthrough,
    `_BedrockSSEStepper` parallel-tool fix, `usage/domain/partial_usage.py` disconnect floor,
    `proxy/api/concurrency_guard.py` `GlobalBackPressureMiddleware`. The live pass EXERCISES these end-to-end.
  - Helios (`../helios-mono`, Rust workspace): `crates/helios-providers/src/{google,azure_openai,...}`; speaks
    OpenAI Chat Completions WIRE. Integration is config-ONLY — point its OpenAI-compatible provider's base_url at
    the proxy edge + Bearer = the seeded gateway tenant key. No Helios code change.

Context (working folder):
  - e2e TLS/Envoy stack: `infra/docker-compose.e2e.yml` (+ vN overlays). Edge at `https://localhost:8443`,
    CA `infra/envoy/certs/dev-ca.pem`. Bring-up: `docker compose -f … up --build -d --wait`. [[e2e-edge-stack-ops]]
  - gcloud: project `project-2a9a1b57-aca9-4193-9a9`, account `tindang.ht97@gmail.com` (authed). The
    Generative Language API is NOT yet enabled — must `gcloud services enable generativelanguage.googleapis.com`
    + mint an API key (`gcloud services api-keys create`). Tin runs these interactively (key never logged/echoed).
  - Tin's chosen auth path (AskUserQuestion): "gcloud-provisioned API key (no code change)" — feed the minted
    Generative Language key to the proxy as the BYOK `google` secret; works with today's x-goog-api-key adapter.

Honors (patterns / conventions):
  - Live double-pass close rule (foundation): the verifier runs TWICE in sequence, both exit 0, fresh identities
    each run (run_id = int(time.time())). [[v5-milestone-status]]
  - Secrets NEVER logged/echoed/persisted to shared files — Gemini auth is the x-goog-api-key header only.
  - Independent-oracle/operator-run pattern: script is operator-invoked against the real stack; usage rows
    asserted via psql against `:5433 gateway_test` (ONE pytest/verify process at a time — concurrent runs cross-wipe).

Anchors the contract cites: `scripts/live_helios_smoke.py`; `PUT /admin/provider-keys/google`;
`GeminiCompletionUpstream` (x-goog-api-key → real generativelanguage.googleapis.com); the double-pass exit-0 rule.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Helios live-integration smoke — `scripts/live_helios_smoke.py`, an operator-run verifier that
proves a real OpenAI-wire agent-coding client drives the proxy end-to-end against a REAL provider (Gemini
via gcloud-minted key), exercising the v34 hardening (reasoning · prompt-cache · parallel tool streaming ·
disconnect billing · back-pressure) with accurate usage rows. Closes the v34 milestone's live exit criterion.

Framings weighed:
  - Self-contained Python verifier that issues OpenAI-wire requests AS Helios would (chosen) — the proxy
    only ever sees an OpenAI Chat Completions client; replaying the exact wire shapes Helios emits proves
    integration readiness without coupling the close to a Rust build/toolchain or Helios's own config quirks.
  - Drive the real Helios binary (`cargo run`) pointed at the proxy (alternative) — truest, but adds a Rust
    build + Helios-side config to the proxy's release gate; brittle, slow, and tests Helios not the proxy.
  - Extend the CI stub harness only (alternative) — already done (tasks 1–6, MockTransport); this task's
    whole point is REAL provider traffic + the double-pass, which a stub cannot give.

Must:
<must>
  - Bring-up-agnostic: read edge base (`SMOKE_BASE`, default `https://localhost:8443`) + CA; assume the
    e2e TLS stack is already up (operator brings it up per the docstring runbook).
  - Provision per run: signup → login (owner JWT) → `POST /admin/keys` (gateway tenant Bearer) → `PUT
    /admin/provider-keys/google {"secret": <gemini-key>}`. Fresh identities each run (run_id=int(time.time())).
  - Read the Gemini key from env (`HELIOS_GEMINI_KEY`); NEVER log/echo/persist it. Absent → exit 2.
  - C1 CODING CHAT (non-stream): an OpenAI chat.completions call with a coding system+user turn → 200 OpenAI
    shape; exactly 1 usage_record row with prompt_tokens>0 AND completion_tokens>0 AND cost_usd>0.
  - C2 STREAMING TOOL-CALLS: a chat request with ≥2 tools + stream=true → OpenAI SSE deltas carrying
    tool_calls (id+name+arguments fragments) AND a terminal usage frame; usage row reconciles (>0/>0).
  - C3 REASONING: a request with reasoning_effort set → 200; response/usage surfaces reasoning (Gemini
    thoughtsTokenCount → usage.completion_tokens_details.reasoning_tokens ≥ 0, present not dropped).
  - C4 PROMPT-CACHE: two back-to-back identical large-context requests → 2nd surfaces cached/creation token
    detail in usage (cached_tokens or cache fields present); both rows billed > 0.
  - C5 DISCONNECT BILLING: start a stream then abort mid-flight → exactly 1 usage row stamped with a partial
    floor (provider_cost estimate present, user cost_usd=0), surfaced not silent-$0.
  - C6 BACK-PRESSURE (only if `GATEWAY_MAX_CONCURRENT_REQUESTS`>0 in the live stack): a concurrent burst over
    the cap → at least one 503 ERR_OVERLOADED + Retry-After, and a subsequent single request succeeds (200).
  - C7 GOVERNANCE: no-Bearer → 401 with 0 usage rows.
  - Exit 0 only if every applicable criterion PASSes; print a per-criterion PASS/FAIL table.
  - DOUBLE-PASS: re-runnable; orchestrator runs it twice, both must exit 0, identities fresh each run.
</must>
Reject:
<reject>
  - `HELIOS_GEMINI_KEY` absent/empty -> exit 2 ("key absent"), no provisioning, no rows created.
  - Any criterion fails (wrong status, missing/zero usage row, secret leak in output) -> exit 1.
  - no-Bearer chat -> 401 ERR_* and 0 usage rows (C7).
</reject>
After:
<after>
  - Both passes exit 0 → v34 live exit criterion met; the milestone can close.
  - The minted Gemini key lives only in env + the BYOK store (Fernet-at-rest); never in logs/repo/PR.
  - No proxy src change required (Tin's chosen no-code-change path) — task ships only the verifier script.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The live stack's seeded Gemini model id + pricing_snapshot drive cost_usd>0 — lowest confidence because
    cost assertions depend on a `per_token` pricing row existing for the live model id (v9 seeds one via psql);
    if wrong: C1/C4 cost>0 assertions fail spuriously. Mitigation: the script seeds the model+pricing like
    live_v9_verify._seed_v9_models() and restarts the gateway so the resolver picks it up.
  - [ ] Gemini surfaces reasoning tokens for the chosen model/effort — if the live model returns no
    thoughtsTokenCount, C3 weakens to "reasoning field present and ≥0 (may be 0)", never a hard >0.
  - [ ] C6 is conditional on the live stack enabling the cap; if disabled, C6 is SKIPPED (logged), not failed.
  - [ ] Replaying OpenAI-wire shapes == what Helios emits — acceptable: the proxy contract IS the OpenAI wire;
    Helios is a conformant client. If a real Helios shape later differs, that's a new change-request.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Coding chat reconciles (C1)
  Given the e2e stack is up, HELIOS_GEMINI_KEY is set, and a fresh tenant has PUT its google BYOK key
  When the verifier sends an OpenAI chat.completions coding turn (non-stream) for the seeded Gemini model
  Then it returns 200 in OpenAI shape
  And exactly 1 usage_record row exists with prompt_tokens>0, completion_tokens>0, cost_usd>0

Scenario: Streaming parallel tool-calls reconcile (C2)
  Given the same provisioned tenant
  When the verifier sends a stream=true chat with >=2 tools that elicit tool_calls
  Then the SSE stream carries tool_call deltas (id+name+arguments) and a terminal usage frame
  And exactly 1 usage row reconciles with prompt_tokens>0 and completion_tokens>0

Scenario: Reasoning passthrough surfaces (C3)
  Given the provisioned tenant
  When the verifier sends a request with reasoning_effort set
  Then it returns 200
  And usage surfaces reasoning_tokens (present, >=0 — never silently dropped)

Scenario: Prompt cache surfaces on repeat (C4)
  Given the provisioned tenant
  When the verifier sends the same large-context request twice back-to-back
  Then both return 200 and both bill cost_usd>0
  And the second response/usage surfaces cached or cache-creation token detail

Scenario: Mid-stream disconnect is billed, not silent-$0 (C5)
  Given the provisioned tenant
  When the verifier opens a stream then aborts before completion
  Then exactly 1 usage row is stamped with a partial floor (provider_cost estimate present, user cost_usd=0)
  And no second/duplicate row is created for that request

Scenario: Back-pressure sheds excess load (C6, conditional)
  Given GATEWAY_MAX_CONCURRENT_REQUESTS>0 in the live stack
  When the verifier fires a concurrent burst exceeding the cap
  Then at least one request gets 503 ERR_OVERLOADED with a Retry-After header
  And a subsequent single request returns 200 (capacity recovered)

Scenario: Missing key aborts cleanly (reject)
  Given HELIOS_GEMINI_KEY is unset
  When the verifier starts
  Then it exits 2 ("key absent")
  And no tenant is created and no usage rows are written

Scenario: No-bearer is rejected (C7, reject)
  Given the stack is up
  When a chat call is made with no Authorization header
  Then it returns 401
  And 0 usage rows are written for that call
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
ARTIFACT  scripts/live_helios_smoke.py   (operator-run; NO proxy src change)

CLI / ENV:
  env SMOKE_BASE        (default https://localhost:8443)   edge base
  env E2E_CA_CERT       (default infra/envoy/certs/dev-ca.pem)
  env HELIOS_GEMINI_KEY (REQUIRED; absent -> exit 2)        gcloud-minted Generative Language key
  env GATEWAY_MAX_CONCURRENT_REQUESTS (optional; >0 enables C6, else C6 SKIPPED)
  run:  uv run --project apps/gateway python scripts/live_helios_smoke.py   (run TWICE; both exit 0)

PROVISIONING (per run, via the edge):
  POST /admin/auth/signup {email,password}                 -> fresh tenant
  POST /admin/auth/login  {email,password}                 -> { access_token }  (owner JWT)
  POST /admin/keys        {name}                  (JWT)    -> { key }           (gateway Bearer)
  PUT  /admin/provider-keys/google {secret: $HELIOS_GEMINI_KEY}  (JWT)  -> 200/204
  seed: INSERT models + pricing_snapshots (per_token, >0) via psql; restart gateway; wait healthy

CRITERIA (OpenAI-wire calls to the edge with the gateway Bearer):
  C1 POST /v1/chat/completions {model, messages, max_tokens}            -> 200 OpenAI shape; 1 row >0/>0/cost>0
  C2 POST /v1/chat/completions {…, tools:[≥2], tool_choice, stream:true} -> SSE tool_call deltas + usage frame; 1 row
  C3 POST /v1/chat/completions {…, reasoning_effort}                    -> 200; usage reasoning_tokens present (≥0)
  C4 POST /v1/chat/completions {…large ctx} ×2                          -> both 200, both cost>0; 2nd cache detail
  C5 POST /v1/chat/completions {…, stream:true} then abort             -> 1 row partial floor (provider_cost, cost_usd=0)
  C6 N× concurrent POST /v1/chat/completions (if cap>0)                -> ≥1 503 ERR_OVERLOADED+Retry-After; then 200
  C7 POST /v1/chat/completions (no Authorization)                      -> 401; 0 rows

EXIT: 0 = all applicable PASS · 1 = any fail/secret-leak · 2 = HELIOS_GEMINI_KEY absent
Schema (READ-ONLY assert + seed only): usage_records (prompt_tokens, completion_tokens, cost_usd,
  provider_cost, usage_source) polled by api_key_id via psql :5433 gateway_test; models + pricing_snapshots
  seeded (per_token >0). NO migration, NO new table, NO proxy src change.
SECURITY: $HELIOS_GEMINI_KEY never logged/echoed/written to repo; BYOK stores it Fernet-at-rest;
  output redacts secrets; PR diff carries no key material.
```

### v2 AMENDMENT — provider under test redirected Gemini → OpenRouter (change request, Tin-approved 2026-06-23)
Why: the gcloud-provisioned Gemini key hit a hard external wall — the GCP project's Generative Language API
returned `429 RESOURCE_EXHAUSTED "prepayment credits depleted"` (proven by a raw call bypassing the proxy;
the proxy correctly surfaced it). Tin chose to redirect the live double-pass to OpenRouter (a funded key).
OpenRouter is OpenAI-wire PASSTHROUGH, so three criteria re-frame to its REAL semantics (provider-accurate,
NOT weakened); C1/C2/C7 are provider-agnostic and unchanged.

```
CLI / ENV (v2):
  env OPENROUTER_API_KEY (REQUIRED; absent -> exit 2)   funded OpenRouter key (sk-or-v1-…); read at run time
  (SMOKE_BASE / E2E_CA_CERT / GATEWAY_MAX_CONCURRENT_REQUESTS unchanged)
PROVISIONING (v2): PUT /admin/provider-keys/openrouter {secret: $OPENROUTER_API_KEY}  (JWT) -> 200/204
  model = $HELIOS_OR_MODEL (Tin chose "z-ai/glm-5.2" — covers tools+reasoning+automatic-cache at low cost;
  default in code is anthropic/claude-3.7-sonnet); seeded catalog row id = that model string,
  provider="openrouter", per_token pricing >0.
CRITERIA (v2 — deltas from v1):
  C1, C2, C7  UNCHANGED (chat · streaming tool-calls · no-Bearer→401 + 0 rows). NOTE: streaming criteria
       cap max_tokens so a reasoning model's stream completes [DONE]+tool_calls within the read window.
  C3 reasoning  reasoning_effort on a reasoning-capable OR model → 200; usage reasoning_tokens present (≥0).
  C4 prompt-cache  verifier SENDS cache_control (content-blocks; passthrough — auto-inject is native-only) on
       the IDENTICAL large-context prefix, repeated up to 6× with spacing (upstream automatic caching is
       OPPORTUNISTIC and the cache-WRITE must register before a read can hit — back-to-back calls race it).
       Assert (≥2 calls): all 200, all rows cost>0, AND a GENUINE cache hit surfaces (cached_tokens /
       cache_read / cache_creation > 0). Retry tolerates write-then-read latency; a real hit is still required.
  C5 disconnect  OpenRouter is the RECOVERABLE path (use_cases.py:1563 recoverable=openrouter+gen-id):
       start stream then abort → a disconnect row with user cost_usd=0 (never silent-$0); the gen-id is
       captured and the cost-recovery chain is scheduled. Assert: ≥1 row for the key, user cost_usd=0 on the
       disconnect row, NOT a silent zero. (v1's "exactly 1 row + provider_cost partial floor" was the native
       estimate path; OpenRouter defers authoritative cost to recovery, which may append a later row.)
EXIT: 0 = all applicable PASS · 1 = any fail/secret-leak · 2 = OPENROUTER_API_KEY absent.
SECURITY (v2): $OPENROUTER_API_KEY never logged/echoed/committed; read from gitignored tmp/.or_key at run
  time, deleted after; BYOK stores it Fernet-at-rest; output redacts it; PR diff carries no key material.
```

Status: FROZEN @ v2 — approved by Tin (2026-06-23)
Least-sure flag surfaced at freeze (v2): [contract] C4 cache detail depends on OpenRouter/Anthropic honoring
  client-sent cache_control AND a context ≥ the cache minimum — if no cache hit surfaces, that is a real
  passthrough finding (not weakened away). [contract] C5 recovery may append a second row within the poll
  window → assertion is "≥1 row, user cost_usd=0, not silent-$0", not "exactly 1".

— superseded freeze below —
Status: FROZEN @ v1 — approved by Tin (2026-06-23)
Least-sure flag surfaced at freeze: [spec/test] cost_usd>0 depends on a seeded `per_token` pricing row for
  the live Gemini model id — mitigated by seeding model+pricing (live_v9 pattern) + gateway restart; secondary:
  C3 reasoning weakens to "present ≥0" if the live model omits thoughtsTokenCount, C6 SKIPs if cap disabled.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: offline pure-helpers 100%; live criteria are operator-gated (the double-pass IS the evidence).

Two layers, matching prior live-verify tasks (v9/v25 are operator-run scripts, not pytest):
  (a) OFFLINE unit tests (red→green in CI, no stack) for the safety-critical PURE helpers the script exposes —
      these protect the security + control-flow invariants and run in the normal gate.
  (b) LIVE criteria (C1–C7) — the script's own per-criterion record()/exit-code; evidence is the double-pass
      (both runs exit 0). Not pytest; operator-run against the real stack + real Gemini.

Plan (offline, layer a — assert behavior not internals):
<test_plan>
  - test_missing_key_exits_2: arrange HELIOS_GEMINI_KEY unset / act run main()/resolve_gemini_key /
    assert SystemExit code==2 AND no network/provision call attempted
  - test_redact_never_emits_secret: arrange a known secret in a log line / act _redact(line, secret) /
    assert secret substring absent AND replaced by a fixed mask
  - test_criteria_table_fail_sets_exit_1: arrange a results set with one FAIL / act exit_code(results) /
    assert ==1 ; all-PASS -> 0 ; conditional SKIP (C6 disabled) does NOT force non-zero
  - test_c6_skipped_when_cap_unset: arrange GATEWAY_MAX_CONCURRENT_REQUESTS unset / act applicable(C6) /
    assert C6 marked SKIPPED not FAIL (does not block exit 0)
</test_plan>

Tests live in: `apps/gateway/tests/helios_live_smoke/` · MUST run red (missing helpers) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `scripts/live_helios_smoke.py` `apps/gateway/tests/helios_live_smoke/`
Strategy (ordered batches):
  1. Pure helpers in the script (resolve_gemini_key→exit 2, _redact, exit_code, applicable/SKIP) — testable offline.
  2. Offline unit tests (layer a) red→green against those helpers.
  3. Provisioning + seed plumbing (signup/login/keys/PUT provider-keys/google; psql model+pricing seed; restart).
  4. The 7 live criteria functions (C1–C7) using the OpenAI-wire calls; per-criterion record(); double-pass exit rule.
Safety rule (feature-specific): the Gemini key is read from env ONCE, held in memory only, redacted from every
  log/print, and passed solely in the BYOK PUT body + never re-emitted; no key material in repo/PR/output.
Code lives in: `scripts/` (root) + `apps/gateway/tests/helios_live_smoke/`
Constraints: do NOT change any test or the contract; NO proxy src change (Tin's no-code-change path); stdlib +
  already-allow-listed deps only (urllib/httpx as prior scripts use); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — offline layer-a: 18 passed (tests/helios_live_smoke). Full gate (clean single-process):
      1513 passed, 19 deselected, 0 failed.
- [x] coverage did not decrease — 87.40% (identical to task 6); scripts/ is not in the coverage source so the
      live-only functions don't dilute. (Subset run shows artifact 35% — ignore per [[v12-milestone-closed]] no-DB gate note.)
- [x] no test or contract was altered during build — FROZEN §3 untouched; NO proxy src change (git diff = only
      .add/state.json tracked; new files are additive: scripts/live_helios_smoke.py + tests/helios_live_smoke/).
- [x] the green was EARNED — adversarial refute-read (orchestrator) of the script: every output path routes
      through _redact + a final secret-leak sweep in main(); offline tests assert real behavior (exit 2 on
      missing key, redaction, exit-code, C6 SKIP). ONE gap found+closed: C7 was a tautology (didn't assert
      "0 rows") → STRENGTHENED to snapshot global usage_records count before/after the no-Bearer call.
- [x] concurrency / timing — C6 burst uses threads+lock; C5 abort is best-effort (depends on OS TCP flush —
      documented). Live-only; no shared-state risk in the gate.
- [x] no exposed secrets / injection / deps — key read once, in-memory, redacted everywhere, BYOK-body only,
      final leak sweep exits 1 if found. stdlib only (urllib/json/ssl/subprocess) — no new dependency.
- [x] layering follows CONVENTIONS.md — operator script in scripts/ mirrors live_v9/live_v25 idioms exactly
      (psql container hydroa-e2e-postgres-1, -U gateway -d gateway_e2e, key_id poll column — verified vs real schema).
- [ ] a person reviewed and approved the change — PENDING Tin's review at PR; AND the live double-pass (below).

### Build expectations — what "correct" looks like (the live double-pass is the milestone evidence)
- [x] Offline helpers behave per contract — confirmed by the 18 green tests (key-absent exit 2; redaction; exit-code; C6 SKIP).
- [x] Verifier matches the real edge/admin/BYOK/psql surfaces — confirmed by grounding vs provider_keys_admin_router
      (PUT /admin/provider-keys/google), partial_usage.py (C5 target), concurrency_guard.py (C6 503), live_v9 psql.
- [x] LIVE: both passes exit 0 against a REAL provider — DOUBLE-PASS GREEN on z-ai/glm-5.2 via OpenRouter
      (run_ids 1782233615 + 1782233647): each 18 PASS / 1 SKIP (C6, cap disabled) / 0 FAIL. C1 chat (cost>0) ·
      C2 streaming parallel tool-calls (tool_calls + usage frame, row 228/89) · C3 reasoning_tokens=61 ·
      C4 prompt-cache (cached_tokens=4416 surfaced through the proxy) · C5 disconnect recorded user cost_usd=0 ·
      C7 401 + 0 rows. THIS is the v34 live exit criterion — MET.

### Deep checks
- [x] WIRING — every helper/criterion is referenced from main(); main() guarded by `if __name__=="__main__"`.
- [x] DEAD-CODE — removed the dummy `_api_key_no_auth_needed` when strengthening C7 (no dangling refs; grep clean).
- [x] SEMANTIC — full script + offline tests read line-by-line in the refute-read (not skimmed).

### GATE RECORD
Outcome: PASS — offline suite green (18 tests) + full gate 1513 green @ 87.40% + refute-read EARNED + the
  LIVE DOUBLE-PASS is GREEN ×2 on z-ai/glm-5.2 via OpenRouter (18/1/0 each, exit 0). No proxy src change; no
  test/contract weakened (the v2 OpenRouter re-frame was a Tin-approved change request; C4 warmup/retry tolerates
  the upstream's opportunistic-cache write-then-read latency but still requires a GENUINE cache hit). Secrets
  cleaned up (key file deleted, GCP key deleted, repo secret-free).
Reviewed by: Tin (live double-pass driven this session) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): live-smoke per-criterion PASS rate across re-runs; upstream cache-hit
rate on C4; disconnect-row visibility on C5.

### Spec delta
- [SPEC · open] proxy maps an upstream 429 (rate-limit / depleted-credits, e.g. Gemini RESOURCE_EXHAUSTED)
  to a 502 Bad Gateway — loses the retryable/rate-limit signal a client (Helios) needs to back off; consider
  mapping upstream 429 → 429/503 with Retry-After (evidence: raw Gemini 429 surfaced as proxy 502 this session).
- [SPEC · open] Gemini live path was unverified (GCP credits depleted); re-run the smoke against real Gemini
  once credits exist — the verifier is provider-parametrized (HELIOS_OR_MODEL / provider) so only config changes.
- [SPEC · dropped] C5 OpenRouter recovery row: within the 45s poll the recovery chain did not append an
  authoritative-cost row (recovery_cost_present=False) — disconnect row was visible with user cost_usd=0 (the
  contract's bar), so not a blocker; worth a longer-window check that recovery eventually appends the delta.

### Competency deltas
- [TDD · folded] live LLM criteria are non-deterministic: a reasoning model needs bounded max_tokens or its [folded foundation-version 31]
  stream outlasts the read window (C2 [DONE] truncation), and opportunistic upstream caching needs a
  warmup+retry (cache-write must register before a read hits) — bound + retry, never assume (evidence: C2/C4
  flaked until fixed). A retry that breaks on first hit must still honor min-call-count invariants (C4a/C4c
  needed ≥2 calls; a warm-cache pass-2 hit on attempt 1 broke that until guarded).
- [ADD · folded] a hard EXTERNAL wall (provider credits) is not a HARD-STOP of the work — surface it, offer [folded foundation-version 31]
  concrete unblock options (AskUserQuestion), and re-frame the contract as a Tin-approved change request
  (v1 Gemini → v2 OpenRouter) rather than silently editing the frozen shape (evidence: this session).
- [ADD · folded] redirecting the provider-under-test is a contract amendment, not a constant swap: passthrough [folded foundation-version 31]
  (OpenRouter) vs native (Gemini) genuinely changes C4 (cache mechanism) and C5 (recoverable vs estimate) —
  re-frame provider-accurately, never weaken (evidence: v2 amendment).
