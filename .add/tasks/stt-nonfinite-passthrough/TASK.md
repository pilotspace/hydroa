# TASK: Sanitize non-finite floats in the STT response body

slug: stt-nonfinite-passthrough · created: 2026-06-18 · stage: production
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
  - `apps/gateway/src/gateway/proxy/api/audio_router.py:audio_transcriptions` (lines 36-57) — returns `JSONResponse(content=response_body, status_code=status)` with the upstream body verbatim. `response_body` comes from `TranscriptionUseCase.execute` (`resp_body = await provider_adapter.post_multipart(...)`).
  - Starlette `JSONResponse.render` (verified in-venv) → `json.dumps(content, ensure_ascii=False, allow_nan=False, …)`. `allow_nan=False` means ANY non-finite float (inf/-inf/nan) ANYWHERE in `response_body` raises `ValueError: Out of range float values are not JSON compliant` → the request 500s on RESPONSE serialization (proven: `json.dumps({"duration": float("nan")}, allow_nan=False)` raises). Distinct from billing — even after stt-duration-cap, a non-finite `duration` (or a non-finite `segments[].avg_logprob`, etc.) in the echoed body still 500s.
  - `apps/gateway/src/gateway/proxy/application/audio_use_case.py:TranscriptionUseCase.execute` (Step 7→9) — where the resolved `resp_body` is returned to the router; the sanitization slots in just before `return status, resp_body` so the router stays a thin `JSONResponse(content=body)`.
  - Sibling passthrough routers share the exact risk (`JSONResponse(content=response_body)`): `images_router.py:49`, `embeddings_router.py:64`, `proxy/api/router.py:82`. OUT of scope for this task (STT only) — a SPEC delta.
Context (working folder): the STT response-passthrough path; v27 t3 OBSERVE residue flagged the inf/nan-500. NOT the money path (billing already correct + capped); purely response robustness. No DB/migration. tinytag already rejects non-finite DERIVED durations, but an upstream BODY value is echoed unsanitized.
Honors (patterns / conventions): "accuracy is never an availability gate" extended to availability itself — a garbage upstream value DEGRADES (sanitized) to a valid 200, never a 500; passthrough-verbatim is preserved for all FINITE values (byte-identical); pure total transform (never raises) like `derive_duration_seconds`.
Anchors the contract cites: `TranscriptionUseCase.execute` (return site) · a NEW pure `sanitize_non_finite(obj)` helper · `audio_transcriptions` (unchanged) · Starlette `allow_nan=False`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: STT response non-finite sanitization — echo a valid 200 even when the upstream body carries inf/nan.
Framings weighed: sanitize the body at the use-case return with a pure recursive transform (chosen) · a custom JSONResponse with `allow_nan=True` (rejected — emits literal `NaN`/`Infinity`, which is INVALID JSON per the spec; clients/proxies break) · let it 500 / drop the response (rejected — availability gate; the transcription succeeded upstream).
Must:
<must>
  - Before `TranscriptionUseCase.execute` returns `resp_body`, recursively replace every NON-FINITE float (`inf`, `-inf`, `nan`) ANYWHERE in the structure (nested dicts + lists — e.g. `duration`, `segments[].avg_logprob`) with `null` (None). Every other value — finite floats, ints, bools, strings, None — is unchanged.
  - A response whose floats are ALL finite is BYTE-IDENTICAL to today (passthrough verbatim preserved).
  - The transform is TOTAL and PURE — it never raises on any JSON-shaped input (mirrors `derive_duration_seconds`'s never-raise discipline).
  - The HTTP status is preserved (the sanitizer touches only the body); the request returns the upstream 200 with a now-JSON-valid body.
  - When a non-finite value is sanitized, emit ONE WARN `stt_nonfinite_sanitized` carrying `{model, count}` so a garbage-emitting upstream is observable (degrade is never silent).
</must>
Reject:
<reject>
  - non-finite float in the upstream body -> NOT a 500; sanitized to `null` + one `stt_nonfinite_sanitized` WARN (availability is never gated by a garbage upstream value; the body is still echoed, just JSON-valid).
  - (no request-level rejection — this is response-passthrough robustness, not an input guard.)
</reject>
After:
<after>
  - `JSONResponse(content=resp_body)` serializes without raising (`json.dumps(allow_nan=False)` succeeds); finite values are unchanged; each non-finite is `null`; status unchanged; one WARN per sanitized response.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the REPLACEMENT value for a non-finite float is client-visible — `null` vs drop-the-key vs `0` each parse differently downstream (a client reading `duration` sees None, or a missing field, or a misleading 0). `null` is the standard "not representable" sentinel that keeps the response shape; if a client can't tolerate `null` where it expects a number, drop-key would suit better. [→ the freeze decision for Tin]
  - [ ] recursion covers NESTED structures (lists of segment dicts), not just top-level keys — verbose_json nests non-finite floats in `segments[]`; confirm full-tree sanitization.
  - [ ] sanitizing in the use case (vs a shared helper applied in every passthrough router) is the right scope for THIS task — STT only; images/chat/embeddings share the risk but are a separate SPEC delta.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: NF1 a nan top-level duration is sanitized to null, 200, WARNed
  Given an upstream STT 200 body {"text": "hi", "duration": NaN}
  When the transcription returns
  Then the HTTP response is 200 and serializes without error
  And response["duration"] is null and response["text"] == "hi"
  And one "stt_nonfinite_sanitized" WARN is logged with {model, count: 1}

Scenario: NF2 a non-finite nested in segments[] is sanitized (full-tree)
  Given an upstream 200 body {"text": "hi", "segments": [{"id": 0, "avg_logprob": -Infinity}]}
  When the transcription returns
  Then the HTTP response is 200
  And response["segments"][0]["avg_logprob"] is null and response["segments"][0]["id"] == 0

Scenario: NF3 an all-finite body is byte-identical (no sanitize, no WARN)
  Given an upstream 200 body {"text": "hi", "duration": 12.5, "segments": [{"avg_logprob": -0.3}]}
  When the transcription returns
  Then the response body equals the upstream body exactly (12.5 and -0.3 preserved)
  And NO "stt_nonfinite_sanitized" WARN is logged

Scenario: NF4 every non-finite form (nan, inf, -inf) maps to null
  Given an upstream 200 body whose values include NaN, Infinity and -Infinity
  When the transcription returns
  Then each of those becomes null and the count in the WARN equals the number of them

Scenario: NF5 sanitize_non_finite is a pure total transform (unit)
  Given a mixed/nested structure with floats (finite + non-finite), ints, bools, strings, None
  When sanitize_non_finite is called
  Then it returns the same shape with ONLY non-finite floats replaced by None (ints/bools/strings/None unchanged)
  And it never raises, and finite floats (incl. 0.0 and -0.0) and bools (True/False) are left as-is (not treated as non-finite)

Scenario: NF6 the status is preserved and the body still echoes
  Given an upstream 200 body containing a nan
  When the transcription returns
  Then the HTTP status is the upstream 200 (sanitizer never changes status)
  And the non-nan keys are echoed verbatim
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
HTTP contract:  POST /v1/audio/transcriptions
  200 -> upstream transcription body, echoed verbatim EXCEPT every non-finite float
         (inf / -inf / nan, at any depth) is replaced by null. No new field, no new 4xx.
         (Before this task: a non-finite value 500s on Starlette's allow_nan=False render.)

New pure helper (proxy/application/json_sanitize.py):
  sanitize_non_finite(obj: Any) -> tuple[Any, int]
    # Recursively rebuild dict/list structures; for a float that is NOT math.isfinite,
    # substitute None and increment the count. bool is NOT a float-non-finite case
    # (True/False pass through). Returns (sanitized_copy, count_of_substitutions).
    # TOTAL + PURE: never raises on any JSON-shaped input; non-container leaves returned as-is.

Use case (audio_use_case.py : TranscriptionUseCase.execute), just before `return status, resp_body`:
  resp_body, _nf_count = sanitize_non_finite(resp_body)
  if _nf_count:
      _log.warning("stt_nonfinite_sanitized", extra={"model": model_id, "count": _nf_count})

Router (audio_router.py): UNCHANGED — still `JSONResponse(content=response_body, …)`,
  now guaranteed JSON-serializable.

Schema: NONE — no DB, no migration, no new dependency (stdlib `math.isfinite`). Billing
path untouched (sanitization is response-only, after the single _fire_record_with_raw).
Invariants: finite values byte-identical · status preserved · degrade-not-fail · one WARN.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-18, via AskUserQuestion).
Least-sure flag surfaced at freeze: [spec] the REPLACEMENT value for a non-finite float is
client-visible — null vs drop-key vs 0 each parse differently downstream; Tin chose null (the
standard 'not representable' sentinel that keeps the response shape). Sub-confirmations resolved
as drafted: full-tree recursion (verbose_json nests non-finite in segments[]); STT-only scope
(images/chat/embeddings share the risk → a separate SPEC delta).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of `sanitize_non_finite` + the use-case WARN branch (whole-suite ≥80%).
Plan (one test per scenario — HTTP tests assert the rendered body + WARN; NF5 is a pure unit test):
<test_plan>
  - test_nf1_nan_duration_sanitized: provider 200 body {"text":"hi","duration":nan} / POST / assert 200, resp["duration"] is None, resp["text"]=="hi", one "stt_nonfinite_sanitized" WARN {model,count:1}.
  - test_nf2_nested_segments_sanitized: body {"segments":[{"id":0,"avg_logprob":-inf}]} / assert 200, resp["segments"][0]["avg_logprob"] is None, ["id"]==0.
  - test_nf3_all_finite_byte_identical: body {"duration":12.5,"segments":[{"avg_logprob":-0.3}]} / assert resp == body exactly, NO WARN.
  - test_nf4_all_nonfinite_forms_to_null: body with nan, inf, -inf / assert all three → None, WARN count == 3.
  - test_nf5_sanitize_non_finite_pure_unit: call helper on a mixed nested structure / assert only non-finite floats → None; ints/bools(True/False)/strings/None/0.0/-0.0 unchanged; count correct; never raises; input not mutated in place (returns a copy).
  - test_nf6_status_preserved_body_echoed: body with a nan + other keys / assert HTTP status == upstream 200 and the non-nan keys echo verbatim.
</test_plan>

Tests live in: `apps/gateway/tests/stt_nonfinite_passthrough/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/json_sanitize.py` `apps/gateway/src/gateway/proxy/application/audio_use_case.py` `apps/gateway/tests/stt_nonfinite_passthrough/`
Strategy (ordered batches): 1. write the pure `sanitize_non_finite` helper (json_sanitize.py). 2. call it + WARN in TranscriptionUseCase.execute before the return. 3. red suite → green.
Safety rule (feature-specific): the sanitizer is a PURE transform that NEVER raises and runs AFTER the single `_fire_record_with_raw` (response-only; billing untouched); `use_cases.py` and `audio_router.py` stay byte-identical.
Code lives in: `apps/gateway/src/gateway/proxy/application/`
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

- [x] all tests pass — full gateway suite (excl `tests/edge` live-stack) GREEN: **1186 passed**, exit 0 (b80v9vjk9, 125.5s); focused `stt_nonfinite_passthrough/` + updated `test_sd8` = 7 passed.
- [x] coverage did not decrease — `json_sanitize.py` is NEW and 100% exercised (NF5 unit walks dict/list/nested + every non-finite form + scalar leaves + purity; HTTP NF1/NF2/NF4/NF6 drive the use-case WARN branch; NF3 the count-0 no-WARN branch). No line of new code is unhit.
- [x] no test or contract was altered during build — §3 CONTRACT byte-identical (FROZEN @ v1). The one cross-suite edit (v27 `test_sd8`) was made in the TESTS phase (before the build snapshot), not during build, and is a STRENGTHENING: it now pins 200 + sanitized `null` while keeping its ledger-quantity invariant (derived `Decimal('3.0')`) intact. This task's own tests were never weakened. See §7 Spec delta.
- [x] the green was EARNED — adversarial refute-read (sonnet subagent) = EARNED-WITH-NITS, confidence 0.91, BLOCKERS none. The 2 actionable NITs were closed in the tests phase: (1) docstring precision (recurses dict/list only — exhaustive for a json.loads body; non-JSON containers pass through); (2) NF5 nested-container identity asserts (`out["list"] is not src["list"]`). Money path untouched — sanitize runs AFTER the single `_fire_record_with_raw`; `test_sd8` independently proves the ledger records derived `3.0` while the body sanitizes inf→null.
- [x] concurrency / timing — N/A by construction: `sanitize_non_finite` is a pure, synchronous, stateless transform over one response body — no shared state, no `await`, no ordering hazard. It runs after billing, before the return; nothing else observes the intermediate body.
- [x] no exposed secrets, injection openings, or unexpected dependencies — stdlib `math` only; no new package; no eval/secret/user-controlled-format surface (it replaces non-finite floats with `None`).
- [x] layering & dependencies follow CONVENTIONS.md — `json_sanitize.py` is a leaf in `proxy/application` importing only stdlib `math`; the use case imports it downward. No new cross-layer edge; `use_cases.py` and `audio_router.py` stay byte-identical.
- [x] a person reviewed and approved the change — Tin approved the §3 freeze (null replacement) via AskUserQuestion (2026-06-18); verify auto-gates on complete evidence (`autonomy: auto`, non-security, no concurrency/architecture residue).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `sanitize_non_finite` is imported at `audio_use_case.py:49` and called at Step 8b (`audio_use_case.py:261`); `TranscriptionUseCase.execute` is the production STT path (wired via `audio_deps.get_transcription_use_case`). Confirmed by grep + the passing HTTP suite, which fires the WARN through the real DI path.
- [x] DEAD-CODE (code) — no orphaned symbol; the helper's sole caller is `execute()`, and every return branch (dict / list / non-finite float / pass-through leaf) is hit by NF5 + the HTTP tests.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: ADD auto-gate (autonomy: auto, non-security, evidence complete) + Tin (contract freeze, 2026-06-18) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `stt_nonfinite_sanitized` WARN rate — must stay ~0 in
steady state; a spike isolates a specific upstream/model emitting garbage floats. Pair it with
the 5xx rate on POST /v1/audio/transcriptions — this task drives the inf/nan-induced render-500s
to 0 (NF1/NF4/NF6 are the live monitors; NF3 guards the no-false-positive byte-identical path).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] apply the same non-finite sanitization to the sibling passthrough routers that share the exact `JSONResponse(content=body)` allow_nan=False risk: `images_router.py:49`, `embeddings_router.py:64`, `proxy/api/router.py:82` (evidence: §0 GROUND — identical render path; STT-only was this task's declared scope).
- [SPEC · seeded] v29 = billing-reconciliation monitor: Σ(provider_cost) vs Σ(billed) per window with a drift alert, closing the "upstream charges us but we don't charge the user" gap (evidence: Tin's 2026-06-18 decision "finish v28 t3, then scope reconciliation as v29"; v27 added cost_basis/provider_cost + usage_source provenance but NO reconciliation job exists yet).
- [SPEC · dropped] v27 t3's deferred response-passthrough follow-up (inf/nan body → 500) is now CLOSED by this task; `test_sd8` updated from `raises(ValueError)` to pin the 200 + null behavior while keeping its ledger-quantity invariant intact (evidence: test_sd8's own docstring "logged as an observe follow-up").

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [ADD · folded] a frozen test from a CLOSED milestone can legitimately go stale when a later task fixes a behavior that test DEFERRED — the principled handling is a tests-phase STRENGTHENING (preserve the true invariant, update only the stale scaffold), surfaced as a Spec delta, never a silent build-time weakening (evidence: v27 test_sd8 `raises(ValueError)` → 200 + null; its docstring pre-authorized the follow-up). [folded foundation-version 26]
- [TDD · folded] pinning a deferred/out-of-scope concern with a test that asserts the CURRENT (buggy) behavior AND names the follow-up in its docstring turns it into an executable breadcrumb that fails loudly the moment the follow-up lands — forcing the update instead of a silent drift (evidence: test_sd8's scope note surfaced the v28 behavior change in the full suite, not in review). [folded foundation-version 26]
