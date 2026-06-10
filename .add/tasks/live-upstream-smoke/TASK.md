# TASK: Live OpenRouter smoke + streaming cost reconciliation

slug: live-upstream-smoke · created: 2026-06-10 · stage: mvp
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Live OpenRouter smoke — real-key streamed completion through TLS Envoy with billing reconciliation
Framings weighed: scripted live smoke + split-frame extractor fix (chosen — proves the whole billing path against the real upstream and fixes the defect the first live run exposed) · mock-only reconciliation (rejected — the v1 streaming fixtures already passed while live billing silently recorded 0 tokens) · synthetic load test (rejected — out of scope; one verified completion suffices for the exit criterion)
Must:
<must>
  - extract_usage_from_sse must parse the JOINED SSE byte stream, not per-network-chunk: a usage frame split across chunk boundaries (the live OpenRouter behavior, evidence 2026-06-10 run) must be extracted; the LAST usage-bearing frame wins; whole-frame-per-chunk input (v1 fixture format) keeps working
  - scripts/live_smoke.py — executable live smoke: reads GATEWAY_OPENROUTER_API_KEY from env (refuses to run with a clear message when absent/empty), drives the e2e TLS stack (https://localhost:8443, dev CA): signup → login → create key → POST /internal/catalog/sync via the gateway container → streamed /v1/chat/completions on a free model (default nvidia/nemotron-3-nano-30b-a3b:free, overridable via SMOKE_MODEL) → asserts SSE 200 + [DONE] → polls GET /admin/usage until the flusher lands the row → asserts exactly one ledger row whose prompt/completion tokens EQUAL the upstream-reported usage and whose cost_usd equals tokens × snapshot prices × (1 + markup_pct/100)
  - infra/docker-compose.e2e.yml passes GATEWAY_OPENROUTER_API_KEY through to the gateway (empty default keeps the stack offline-safe)
  - The smoke is operator-run (requires a real key + spend); it is NOT part of make ci or the default pytest run; the unit-level extractor regression tests ARE part of the default run (tests/smoke/)
  - The key must never be committed, logged, or echoed by the script
</must>
Reject:
<reject>
  - scripts/live_smoke.py with GATEWAY_OPENROUTER_API_KEY unset/empty -> exit 2 with a clear operator message; no network call
  - ledger row token counts diverging from upstream-reported usage -> smoke exits non-zero naming both values (billing defect, not a warning)
  - usage frame split across chunks yielding usage=None -> red unit test (the v1 defect must never regress)
</reject>
After:
<after>
  - A real streamed completion from the free NVIDIA Nemotron model has traversed Envoy TLS end-to-end and produced exactly one ledger row with tokens == upstream usage and reconciled cost (free model: 0.00)
  - The v1 milestone composite-evidence residue is closed; the open SDD question (streaming usage/cost reconciliation semantics) is answered with live evidence
  - The extractor handles arbitrary chunk fragmentation (byte-by-byte worst case)
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ OpenRouter includes the usage frame in streamed responses without stream_options opt-in — lowest confidence because OpenAI-compatible APIs differ here; CONFIRMED live 2026-06-10 (usage arrived in the final frame with cost_details); if a future model/provider omits it: usage=None is recorded honestly and the smoke fails loudly — revisit with stream_options include_usage
  ⚠ The advisory Redis spend counter and ledger may briefly diverge during the flusher window — the smoke polls the LEDGER (source of truth) with a bounded retry; if the row never lands within the window the smoke fails rather than passing on the counter
  - [x] Free-variant model pricing is 0/0 on OpenRouter (confirmed live: nvidia/nemotron-3-nano-30b-a3b:free) — cost reconciliation on the free model is exact-zero; the cost FORMULA is additionally covered by the existing frozen usage-metering tests with non-zero prices
  - [x] The catalog sync persists 339 live models including the free Nemotron variants (confirmed live 2026-06-10)
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: split usage frame extracted (the live defect)
  Given the live OpenRouter SSE byte stream whose final usage frame is split across two network chunks
  When extract_usage_from_sse processes the collected chunks
  Then usage is extracted with prompt_tokens 24 and completion_tokens 73
  And whole-frame-per-chunk input still extracts identically

Scenario: byte-by-byte fragmentation worst case
  Given the same stream delivered one byte per chunk
  When extract_usage_from_sse processes the chunks
  Then usage is extracted identically

Scenario: live smoke happy path
  Given the e2e TLS stack is up with a real GATEWAY_OPENROUTER_API_KEY and the catalog synced
  When scripts/live_smoke.py runs
  Then the streamed completion returns 200 with [DONE] and real model output
  And exactly one usage_records row exists with tokens equal to the upstream-reported usage
  And cost_usd equals tokens x snapshot prices x (1 + markup)

Scenario: smoke refuses to run without a key
  Given GATEWAY_OPENROUTER_API_KEY is unset or empty
  When scripts/live_smoke.py runs
  Then it exits 2 with an operator message
  And no network call was made

Scenario: token divergence fails the smoke
  Given a ledger row whose tokens differ from upstream usage
  When the smoke reconciles
  Then it exits non-zero naming both values
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Unit contract (default test run, tests/smoke/test_extractor_live_format.py):
  extract_usage_from_sse(chunks: list[bytes]) -> dict | None
    - operates on the JOINED byte stream; chunk boundaries carry no meaning
    - last usage-bearing frame wins; [DONE] and malformed frames skipped
    - returns None only when NO complete frame in the joined stream has usage

Operator contract (scripts/live_smoke.py — not in CI):
  env: GATEWAY_OPENROUTER_API_KEY (required, never logged) - SMOKE_MODEL
       (default nvidia/nemotron-3-nano-30b-a3b:free) - E2E_CA_CERT
       (default infra/envoy/certs/dev-ca.pem) - SMOKE_BASE (default https://localhost:8443)
  exit 0: streamed 200 + [DONE] + one ledger row, tokens == upstream usage,
          cost_usd == tokens x snapshot prices x (1 + markup_pct/100)
  exit 2: key absent (no network call)
  exit 1: any reconciliation failure, with both sides printed

Compose: infra/docker-compose.e2e.yml gateway env gains
  GATEWAY_OPENROUTER_API_KEY: "${GATEWAY_OPENROUTER_API_KEY:-}"   (offline-safe default)

Touched src (fix sanctioned by this contract): gateway/usage/domain/extractor.py ONLY —
the frozen usage-metering tests must stay green unmodified.
```

Status: FROZEN @ v2 — approved by Tin Dang (delegated auto mode, 2026-06-11).
Least-sure flag surfaced at freeze:
⚠ [contract] joining the full collected stream buffers all SSE bytes once more inside the extractor — doubles transient memory in the tee for very long streams; acceptable at current scale (KB–MB completions); if wrong: incremental frame-boundary splitting, extractor signature unchanged.
⚠ [spec] reconciliation runs on a FREE model — proves the pipeline, but the non-zero cost formula remains covered only by the frozen usage-metering unit fixtures; if a live paid check is required later, SMOKE_MODEL on a paid model under a tight budget is the contained path.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: extractor change 100% (pure function); smoke scenarios exercised live by the operator run
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_usage_frame_split_across_two_chunks: live frame split at midpoint -> usage extracted (RED: the live defect)
  - test_usage_frame_split_byte_by_byte: one byte per chunk -> usage extracted (RED)
  - test_whole_frame_chunks_still_extract: v1 fixture format -> still extracts (green compat guard)
  - test_last_usage_frame_wins: two usage frames -> the final one is authoritative
  - test_no_usage_anywhere_returns_none: no usage in any frame -> None
  - test_live_frame_is_valid_json_when_joined: fixture sanity guard
  - live smoke scenarios (happy path / no-key refusal / divergence failure): executed by scripts/live_smoke.py against the real stack; operator evidence recorded in §6
</test_plan>

Tests live in: `apps/gateway/tests/smoke/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — tests/smoke 6/6 green (both split-frame defect tests flipped red→green
      by the extractor fix); full suite 104 passed; make ci exit 0; LIVE smoke exit 0:
      "SMOKE OK: model=nvidia/nemotron-3-nano-30b-a3b:free tokens=24/52 cost_usd=0E-8
      (reconciled, markup=20.0000%)" — ledger tokens EQUAL upstream usage, cost formula
      verified against the persisted pricing snapshot + tenant markup via SQL
- [x] coverage did not decrease — make ci floor held; extractor change fully covered
- [x] no test or contract was altered during build — frozen usage-metering tests untouched
      and green (the joined-stream parse is a superset of the v1 per-frame format)
- [x] concurrency / timing of the risky operation is safe — extractor stays a pure function
      (join + reverse line scan); the write-behind flusher window is handled by bounded
      ledger polling in the smoke, never by trusting the advisory counter
- [x] no exposed secrets, injection openings, or unexpected dependencies — key read from env
      only, never echoed/logged/committed (apps/gateway/.env is gitignored); compose default
      is empty (offline-safe); no new dependencies
- [x] layering & dependencies follow CONVENTIONS.md — fix confined to the usage domain pure
      function; smoke script is operator tooling under scripts/
- [x] a person reviewed and approved the change — orchestrator ran the live flow personally
      (first run exposed the 0/0-token defect; second run after the fix reconciled exactly)
      (delegated auto mode, Tin Dang, 2026-06-11)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — extractor consumed by proxy _wrapped() tee (use_cases.py:210); smoke
      script exercised live end-to-end including the exit-2 refusal path
- [x] DEAD-CODE (code) — no new symbols beyond the script entrypoint; nothing orphaned
- [x] SEMANTIC (prose / non-code) — live upstream usage frame captured verbatim into the test
      fixture (incl. cost_details/reasoning_tokens shape); answers the v1 SDD open question:
      OpenRouter DOES send usage in the final streamed frame without stream_options opt-in,
      and frames MUST be parsed across chunk boundaries

### GATE RECORD
Outcome: PASS (auto-resolved — autonomy: auto; live evidence complete; the billing defect was
found by this task doing exactly its job and fixed before gating)
Reviewed by: Claude (orchestrator) under delegated auto mode — Tin Dang · date: 2026-06-11

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ledger-vs-upstream token divergence on live runs · usage=None rate on streamed completions · smoke exit codes in release checklists
Spec delta for the next loop: OpenRouter delivers usage in the final streamed frame without stream_options opt-in; frames MUST be parsed from the joined byte stream — chunk boundaries carry no meaning.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
  - [SDD · open] mock fixtures that mirror an assumed wire format can pass while live billing silently fails — pin at least one VERBATIM live-captured frame per external protocol (evidence: v1 streaming tests green while live ledger recorded 0/0 vs upstream 24/73)
  - [TDD · open] for stream parsers, fragmentation is part of the input domain — include split-at-midpoint and byte-by-byte cases by default (evidence: tests/smoke parametrized fragmentation caught the rewrite regression-free)
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
