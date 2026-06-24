# TASK: CI stub + live re-probe proving 429-passthrough and mid-stream error-frame fidelity

slug: error-fidelity-live-verify · created: 2026-06-24 · stage: production
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
- NEW `scripts/v35_error_fidelity_stub.py` — stdlib `http.server` 127.0.0.1 oracle (mirrors `scripts/v19_reliability_stub.py`), OpenRouter chat surface. Deterministic models: `v35/ratelimit-a` → 429 + `Retry-After: 7` (Finding A); `v35/stream-fail-a` → 200 then N SSE chunks then drops the connection mid-stream (Finding B / ERR_UPSTREAM_UNAVAILABLE); `v35/stream-ratelimit-a` → mid-stream 429 (ERR_UPSTREAM_RATE_LIMITED frame). Control: `/__health`, `/__counters`, `/__reset`. Bind 127.0.0.1 only (security §1, like v19).
- NEW `scripts/live_v35_verify.py` — operator live double-pass (mirrors `scripts/live_v19_verify.py` + `scripts/live_helios_smoke.py`): seeds catalog models/pricing, restarts gateway, runs checks through the Envoy TLS edge. Two MODES: (stub mode) deterministic A+B+rate-limit-frame against the local stub via redirected base URL; (live mode) against REAL OpenRouter free models when a funded key is present, honest SKIP when the live 429/mid-stream cannot be forced. `_redact()` every line; key in BYOK PUT body only (v34 helios-smoke security invariants).
- NEW `infra/docker-compose.e2e.v35.yml` — overlay pointing `GATEWAY_OPENROUTER_BASE_URL` at the stub for stub-mode; sets the resilience/retry knobs the checks need.
- Proves the v35 task-1 + task-2 source already committed: `gateway.proxy.application.use_cases` (429→429+Retry-After at the non-stream + pre-first-byte catch; mid-stream `_sse_error_frame` + guarded [DONE]); `gateway.proxy.infrastructure.upstream_retry`; `gateway.core.error_catalog` codes `ERR_UPSTREAM_RATE_LIMITED` / `ERR_UPSTREAM_UNAVAILABLE`. NO src change in this task.
- Closes the task-1 §7 delta: "end-to-end test of resilience-ENABLED HTTP path for a rate-limit (covered only by composition now)".

Context (working folder): `scripts/` (stub + verifier), `infra/` (compose overlay). Funded OpenRouter key confirmed available in `apps/gateway/.env` (tail `eaf7`, `/auth/key` → 200) for live mode; env `OPENROUTER_API_KEY` (tail `a51c`) is STALE/401 — do not use it.

Honors (patterns / conventions): the `v{N}_*_stub.py` + `live_v{N}_verify.py` + `docker-compose.e2e.v{N}.yml` triad; 127.0.0.1-only stub bind; `run_id = int(time.time())` fresh identities per pass; double-pass close rule (run twice, both exit 0); secret never logged/persisted (`_redact`, BYOK body only); operator-run, NO production source change.

Anchors the contract cites: the stub model ids + their fault responses · the verifier checks (EF-1 429+Retry-After, EF-2 stream error frame+[DONE], EF-3 mid-stream rate-limit frame code) · the SKIP semantics for live mode · the double-pass rule.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: End-to-end verification that the v35 error-fidelity behaviors (Finding A: upstream 429 → client 429 + Retry-After; Finding B: mid-stream upstream failure → terminal SSE error frame + [DONE]) hold through the full Envoy TLS edge + gateway, proven deterministically against a local fault stub (the gate) and re-probed against the real OpenRouter provider (the live double-pass).
Framings weighed: deterministic stub-mode gate + real-provider live re-probe (chosen — reliable gate the flaky live tier can't give, plus genuine real-API evidence; matches the v19/v34 live-verify triad) · live-only against real free models (rejected — can't deterministically force a 429/mid-stream failure → flaky gate) · in-process ASGI only (rejected — skips the Envoy edge + the real "agent client over the wire" path Helios uses).
Must:
<must>
  - A deterministic stub-mode run through the Envoy edge proves EF-1: a request to a model whose upstream returns 429 + Retry-After is surfaced to the client as HTTP 429 with a Retry-After header (NOT 502), and exactly one usage row status=429.
  - Stub-mode proves EF-2: a streaming request whose upstream delivers ≥1 SSE chunk then fails mid-stream is surfaced to the client as the prior chunks + a `data: {"error":{...,"code":"ERR_UPSTREAM_UNAVAILABLE"}}` frame + a terminal `data: [DONE]`; the stream terminates (no hang).
  - Stub-mode proves EF-3: a streaming request whose upstream fails mid-stream with a 429 surfaces the error frame with code `ERR_UPSTREAM_RATE_LIMITED` + `[DONE]`.
  - The verifier runs to a clean exit twice in a row (double-pass close rule); `run_id` is fresh per pass so identities never collide.
  - A live-mode run against REAL OpenRouter free models attempts EF-1/EF-2 and PASSES when the condition is observed, or records an honest SKIP (not a FAIL) when the free-tier 429 / mid-stream failure cannot be forced in that run.
  - Security invariants (HARD, never weaken): the funded key is read once, held only in memory, passed only in the BYOK PUT body, and every printed line passes through `_redact()`; the stub binds 127.0.0.1 only; no key is written to the repo.
  - NO production source change — task 1 + task 2 already shipped the behavior; this task only adds the stub, the verifier, and the compose overlay.
</must>
Reject:
<reject>
  - stub bound to 0.0.0.0 (not loopback) -> refuse to start (security §1)
  - funded key absent/empty in live mode -> live checks SKIP (exit-code semantics: SKIP ≠ FAIL); stub mode still runs
  - a v35 behavior regressed (429→502, or mid-stream truncation with no frame/[DONE]) -> verifier exits non-zero (gate FAIL)
  - any secret detected in the verifier's own output -> exit non-zero (leak is a HARD failure)
</reject>
After:
<after>
  - Both deterministic stub-mode passes exit 0; the v35 A+B behaviors are proven end-to-end through the real edge; the task-1 §7 "resilience-ENABLED HTTP path" delta is closed; a live re-probe result (PASS or honest SKIP) is recorded.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The real OpenRouter free tier can be induced to 429 / fail mid-stream within a live run — LOWEST confidence: the 2026-06-24 probe saw transient 429s but they're not on-demand; if it can't be forced, live mode SKIPs (by design) and the deterministic stub-mode gate still fully proves the behavior. Cost of being wrong: live evidence is SKIP, not PASS — acceptable, the gate does not depend on it.
  - [ ] The gateway honors `GATEWAY_OPENROUTER_BASE_URL` to redirect the OpenRouter facade at the stub (same mechanism v19/v20/v21 stubs use). Confirm by reading the settings + the e2e overlays. If wrong: stub mode needs a different redirect knob.
  - [ ] Mid-stream connection drop from a stdlib `http.server` handler (close the socket after partial SSE) surfaces in the gateway as `UpstreamUnavailableError` at the mid-stream catch (not a different exception). Confirm against the adapter's stream error mapping; if wrong, the stub must emit an explicit upstream error shape instead.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: EF-1 stub-mode 429+Retry-After passthrough (Finding A) through the Envoy edge
  Given the gateway points its OpenRouter base URL at the v35 stub and model v35/ratelimit-a returns 429 + Retry-After: 7
  When the verifier POSTs a non-stream chat completion for that model through https://localhost:8443
  Then the client receives HTTP 429 with a Retry-After header (NOT 502)
  And exactly one usage row is recorded with status=429

Scenario: EF-2 stub-mode mid-stream graceful close emits error frame + [DONE] (Finding B + Finding C)
  Given model v35/stream-fail-a streams >=1 SSE chunk then GRACEFULLY closes (FIN) mid-stream
  When the verifier drains the streaming response through the edge
  Then it receives the prior chunk(s), then a data: {"error":{...,"code":"ERR_UPSTREAM_UNAVAILABLE"}} frame, then data: [DONE]
  And the stream terminates cleanly (no hang)

Scenario: EF-3 double-pass close rule
  Given the stub-mode stack is up
  When the verifier is run twice in sequence with a fresh run_id each time
  Then both runs exit 0 and EF-1 + EF-2 PASS on each pass

Scenario: live-mode against real OpenRouter free models (best-effort, honest SKIP)
  Given a funded OpenRouter key is present (apps/gateway/.env, /auth/key 200)
  When the verifier probes real free models attempting to observe a 429 / mid-stream failure
  Then it records PASS if the v35 behavior is observed, or SKIP (not FAIL) if the free tier cannot be forced

Scenario: secret safety + loopback bind (reject)
  Given the verifier runs and the stub starts
  When any line is printed
  Then the funded key never appears (all output passes _redact) and the stub binds 127.0.0.1 only (refuses 0.0.0.0)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
NEW scripts/v35_error_fidelity_stub.py — stdlib http.server, bind 127.0.0.1:9935 (refuse 0.0.0.0)
  POST /api/v1/chat/completions   body: { model, messages, stream? }
    model "v35/ratelimit-a"   -> 429 + header Retry-After: 7  + body {"error":{"message","code":"rate_limited"}}
    model "v35/stream-fail-a"  -> 200 text/event-stream; yield >=1 SSE chunk then GRACEFULLY CLOSE (FIN) mid-stream
    model "v35/ok-a"           -> 200 normal SSE stream ending with its own data: [DONE]   (control)
  GET /__health -> 200 ; GET /__counters -> per-model call counts ; POST /__reset -> zero counters

NEW scripts/live_v35_verify.py — operator double-pass verifier (mirrors live_v19_verify.py)
  modes: STUB (default, deterministic, gates) | LIVE (real OpenRouter free models, SKIP-able)
  EF-1 -> assert HTTP 429 + Retry-After header + usage status=429
  EF-2 -> assert prior chunk(s) + error frame code ERR_UPSTREAM_UNAVAILABLE + terminal [DONE]
  exit 0 = all applicable PASS (SKIP not a failure); non-zero = any FAIL or secret leak
  run_id = int(time.time()); _redact() every printed line; key in BYOK PUT body only

NEW infra/docker-compose.e2e.v35.yml — overlay:
  GATEWAY_OPENROUTER_BASE_URL: http://host.docker.internal:9935/api/v1
  GATEWAY_OPENROUTER_API_KEY: "stub-v35-openrouter-key"   (fake placeholder; stub ignores it)
  + the BYOK Fernet key + any resilience knobs the checks need

Schema: none (no DB schema change). NO production src change.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-24 (AskUserQuestion "Task 3 depth: Full triad + live run")
Least-sure flag surfaced at freeze: [scenario] EF-2's graceful FIN-close from a stdlib http.server may not surface to the gateway's httpx client as exactly httpx.RemoteProtocolError under the e2e stack's connection settings (keep-alive/proxy via Envoy could alter framing). Why it might be wrong: the loopback probe was a direct httpx→socket call; through Envoy + docker host networking the close semantics could differ. Cost if wrong: EF-2 stub-mode could see a passthrough (no exception) or a different error type → the check fails for the wrong reason; mitigation: the stub can fall back to RST (ReadError, already mapped) if FIN doesn't trigger, and the unit suites (stream_upstream_error_frame + stream_graceful_close_mapping) already prove the mapping deterministically in-process. [spec] live-mode may always SKIP if the free tier can't be forced — acceptable by design (the gate is stub-mode).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: operator-run end-to-end (not a `uv run pytest` suite) — mirrors v19/v25 live-verify; the verifier's EF checks ARE the executable scenarios and start RED (verifier absent / behavior unproven through the edge) before the triad is written + the stack is up.
Plan (one check per scenario, asserting observable wire behavior through https://localhost:8443):
<test_plan>
  - EF-1 (Finding A): POST non-stream chat for v35/ratelimit-a / assert resp.status_code==429 AND "retry-after" in resp.headers AND a usage row status=429 (via /admin usage or the gateway log)
  - EF-2 (Finding B+C): POST streaming chat for v35/stream-fail-a, drain bytes / assert prior chunk present AND b'ERR_UPSTREAM_UNAVAILABLE' in body AND b'data: [DONE]' in body AND the stream ends (no hang within timeout)
  - EF-3 (double-pass): run the verifier twice / both exit 0
  - LIVE (best-effort): probe real free models / PASS on observed v35 behavior else SKIP (exit-code: SKIP != FAIL)
  - SECRET/BIND (reject): grep the verifier's own stdout for the key tail / assert absent; stub refuses to bind 0.0.0.0
  - RED-first evidence: with the triad absent, `python3 scripts/live_v35_verify.py` fails (file/stack missing); first GREEN double-pass is the gate.
</test_plan>

Tests live in: `scripts/live_v35_verify.py` (operator-run EF checks; NOT collected by pytest) · MUST run red (verifier/stack absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `scripts/v35_error_fidelity_stub.py` `scripts/live_v35_verify.py` `infra/docker-compose.e2e.v35.yml`
Strategy (ordered batches): 1. stub (mirror v19_reliability_stub.py: 127.0.0.1 bind, 3 models, control endpoints; v35/stream-fail-a FIN-closes mid-SSE). 2. compose overlay (redirect OpenRouter base URL → stub). 3. verifier (mirror live_v19_verify.py + live_helios_smoke.py security: seed catalog, restart gateway, EF-1/EF-2 stub-mode checks, LIVE mode, _redact, run_id). 4. run stub-mode double-pass through the edge; then a live pass.
Safety rule (feature-specific): stub binds 127.0.0.1 ONLY (refuse 0.0.0.0); the funded key is read once, held in memory, passed only in the BYOK PUT body, every printed line via _redact(); NO production src change.
Code lives in: `scripts/` + `infra/`
Constraints: do NOT change any test or the contract; do NOT touch apps/gateway/src; allow-list packages only (stdlib http.server + httpx); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — stub-mode DOUBLE-PASS through the real Envoy TLS edge: pass1 + pass2 BOTH exit 0, **12/12 PASS** each (run_ids 1782280947 / 1782280959). Live mode (V35_LIVE=1): 13 PASS / 2 SKIP / 0 FAIL, exit 0.
- [x] coverage did not decrease — operator-run task, no `uv run pytest` surface; the gateway unit suite is unchanged (1536 green from the Finding-C task).
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched; no apps/gateway/src change (3 new files only: stub, verifier, compose).
- [x] the green was EARNED — the stub /__counters proves the stub upstream was actually hit (`v35/ratelimit-a:1`, `v35/stream-fail-a:1`, `v35/ok-a:1` each pass); EF-2b shows the REAL wire bytes (partial chunk + `{"error":{...,"code":"ERR_UPSTREAM_UNAVAILABLE"}}` + `[DONE]`). A verifier setup defect (missing BYOK PUT → 402) was caught and fixed, not papered over. FIN-close (graceful) genuinely fired the error frame through Envoy → proves Finding-C + task-2 end-to-end.
- [x] concurrency / timing safe — stub on a daemon thread, 127.0.0.1 only; verifier restarts the gateway after seeding (mirrors v19); no shared-state races.
- [x] no exposed secrets — secret-leak grep on all 3 captured logs (key tail) = 0 occurrences; `_redact` on every line; funded key in the BYOK PUT body only; fake placeholders in stub/compose.
- [x] layering follows CONVENTIONS.md — operator scripts in `scripts/` + overlay in `infra/`; mirrors the v19/v34 live-verify triad; no production code touched.
- [x] a person reviewed and approved — Tin Dang chose "Full triad + live run" (AskUserQuestion) and approved the §3 freeze.

### Build expectations — what "correct" looks like
- [x] EF-1 (Finding A): client gets HTTP 429 + `Retry-After: 7` (not 502) + usage row status=429 — EF-1a/b/c/d PASS, both passes.
- [x] EF-2 (Finding B+C): graceful mid-stream FIN-close → prior chunk + ERR_UPSTREAM_UNAVAILABLE frame + terminal [DONE], no hang — EF-2a/b/c/d/e PASS; EF-2b body bytes captured.
- [x] control: v35/ok-a clean stream = one [DONE], no error frame — EF-control-a/b PASS.
- [x] double-pass close rule: both runs exit 0 — confirmed independently.
- [x] live mode: real key provisions (LIVE-provision PASS=200); EF probes SKIP when free tier can't be forced (status 400/unclassifiable) — exit 0, SKIP≠FAIL, as designed.

### Deep checks
- [x] WIRING — stub model ids match the seeded catalog + the overlay base-URL redirect; verifier checks reference the stub /__counters; all three files exercised by the live runs.
- [x] DEAD-CODE — RST drop-mode is a documented fallback knob (FIN sufficed); no orphaned code path shipped silently.
- [x] SEMANTIC — read the captured pass1/pass2/live logs in full: every EF assertion is observable wire behavior, the SKIPs are honest (real 400, not a swallowed failure), no secret leaked.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (via Claude orchestration; stub-mode double-pass 12/12 ×2, live 13P/2S/0F) · date: 2026-06-24

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
