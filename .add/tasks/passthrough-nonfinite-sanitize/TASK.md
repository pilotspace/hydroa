# TASK: Non-finite sanitization on images / embeddings / proxy passthrough routers

slug: passthrough-nonfinite-sanitize · created: 2026-06-23 · stage: production
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

GOAL: carry the v28 STT non-finite fix to the THREE sibling passthrough render sites that share the exact `JSONResponse(content=body)` allow_nan=False risk — an upstream inf/-inf/nan anywhere in an echoed body 500s on RESPONSE serialization (Starlette renders with allow_nan=False). Replace each non-finite with null (degrade, never fail) + WARN once; all-finite bodies unchanged.
Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/application/json_sanitize.py:20 sanitize_non_finite(obj) -> (sanitized_copy, count)` — the FROZEN pure/total helper shipped by v28 (stt-nonfinite-passthrough). Reuse verbatim; no change.
  - `apps/gateway/src/gateway/proxy/api/images_router.py:49 JSONResponse(content=response_body, status_code=status)` — body from `ImagesUseCase.execute() -> (status, resp_body)`.
  - `apps/gateway/src/gateway/proxy/api/embeddings_router.py:64 JSONResponse(content=response_body, status_code=status)` — body from `EmbeddingsUseCase.execute() -> (status, resp_body, x_cache)`.
  - `apps/gateway/src/gateway/proxy/api/router.py:82 JSONResponse(content=response_body, status_code=status)` — chat NON-stream body from `CompletionUseCase.complete() -> (status, resp_body, x_cache)`. Streaming (`StreamingResponse`, line 60) is text/event-stream, NOT the allow_nan path — out of scope.
PLACEMENT DECISION (differs from the v28 STT precedent — recorded here): STT sanitizes inside `audio_use_case.py:261` because that use_case has a SINGLE return and no cache. The chat/embeddings use_cases have 6+ return points (exact/semantic/vector cache HITs at use_cases.py:998/1055/1113 + miss), and a cache HIT body bypasses the use_case's post-call path — ALL non-stream bodies converge only at the ROUTER's `JSONResponse`. So the router is the single correct chokepoint that also catches cache-hit bodies (and chat `logprobs` can legitimately be `-inf`). The API layer calling a pure application helper is a permitted downward dependency.
Context (working folder):
  - `apps/gateway/tests/stt_nonfinite_passthrough/` — the v28 test precedent (inf/nan body → 200 + null, all-finite unchanged).
  - `apps/gateway/tests/images_endpoint/conftest.py` + `embeddings_endpoint/conftest.py` — `FakeUpstreamProvider.set_post_json_response(status, body)` injects an arbitrary body (no JSON round-trip, so a Python `float('inf')` reaches the router render); signup/login/seed helpers. Real Postgres:5433 + Redis:6380.
Honors (patterns / conventions):
  - v28 stt-nonfinite-passthrough §3 (FROZEN): null replacement · response-only · AFTER billing · WARN once when count>0 · all-finite unchanged. Mirror its WARN shape (`_log.warning("<event>", extra={"model":..., "count":...})`).
  - CONVENTIONS.md: degrade-not-fail on a response-serialization hazard; reuse the pure helper, no new dependency.
Anchors the contract cites:
  - `sanitize_non_finite` (reused) · the three `JSONResponse` render sites (`images_router`, `embeddings_router`, chat `router`) · a per-router WARN log event.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Non-finite sanitization on the images / embeddings / chat passthrough render sites — degrade an upstream inf/nan body to null instead of 500ing on serialization (carries the v28 STT fix to its three siblings).
Framings weighed: sanitize at the ROUTER `JSONResponse` chokepoint for all three (chosen — single convergence point for every non-stream body incl. cache HITs; mirrors the v28 behavior; reuses the frozen pure helper) · sanitize in each use_case like audio (rejected — chat/embeddings have 6+ return points incl. cache-hit bodies that bypass the use_case post-call path; would need many sites + miss cache hits) · widen the helper / add a custom JSONResponse subclass (rejected — over-engineering; the helper is frozen and sufficient).
Must:
<must>
  - An images / embeddings / chat-non-stream response whose upstream body contains a non-finite float (inf / -inf / nan) ANYWHERE returns HTTP 200 (or the upstream status) with each non-finite replaced by null — never a 500 on response serialization.
  - When ≥1 substitution happens, the router logs ONE WARN (event + model + count); an all-finite body logs nothing.
  - An all-finite body is returned byte-identical to today (count 0 ⇒ no change, no WARN) — no regression to any existing response.
  - The billing/usage path is untouched — sanitization is response-only, applied at render after the use_case has returned (which already recorded usage).
  - The chat STREAMING path (text/event-stream) is NOT affected — only the JSONResponse (non-stream) render sites are sanitized.
</must>
Reject:
<reject>
  - (no new error path) — this feature REMOVES a failure (the serialization 500). There is no new rejection; a malformed-but-finite body still passes through unchanged.
</reject>
After:
<after>
  - The three `JSONResponse(content=body)` render sites can never 500 on a non-finite upstream float; the response shape is preserved with null sentinels.
  - The non-finite hazard is closed across ALL four passthrough surfaces (STT already done in v28 + these three).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Sanitizing at the router instead of the use_case (diverging from the v28 STT placement) — lowest confidence because a reviewer may expect consistency with audio_use_case. Why router is correct: chat/embeddings cache-HIT bodies return through the router but NOT through the use_case post-call path, so the use_case is not a complete chokepoint; the router is. Cost if wrong: none functional (both placements null the same floats); only a consistency-of-style critique, mitigated by the §0 PLACEMENT DECISION note.
  - [x] `FakeUpstreamProvider.set_post_json_response` returns the Python dict as-is (no JSON round-trip), so a literal `float('inf')` reaches the router render and reproduces the 500. Confirmed from the conftest.
  - [x] The chat non-stream body is the only chat JSON render; streaming uses StreamingResponse (different media type, not allow_nan). Confirmed from router.py:60 vs :82.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: images body with a non-finite float does not 500
  Given the images upstream returns 200 with a body containing nan/inf somewhere
  When POST /v1/images/generations is called
  Then the response is 200 and every non-finite value is null
  And a single WARN is logged with the substitution count

Scenario: embeddings body with a non-finite float does not 500
  Given the embeddings upstream returns 200 with an embedding vector holding inf
  When POST /v1/embeddings is called
  Then the response is 200 and the inf entries are null
  And the usage record still fired (billing untouched)

Scenario: chat non-stream body with a non-finite float does not 500
  Given the chat upstream returns 200 with a logprob of -inf in the body
  When POST /v1/chat/completions is called with stream=false
  Then the response is 200 and the -inf is null

Scenario: an all-finite body is unchanged (no regression)
  Given any of the three upstreams returns an all-finite body
  When the endpoint is called
  Then the response body is byte-identical to today
  And no sanitization WARN is logged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
At each of the three non-stream render sites, between the use_case return and JSONResponse:

  resp_body, _nf = sanitize_non_finite(resp_body)   # reused frozen helper
  if _nf:
      _log.warning("<endpoint>_nonfinite_sanitized", extra={"model": <model_id>, "count": _nf})
  return JSONResponse(content=resp_body, status_code=status)   # (+ x-cache header where present)

Render sites (unchanged signatures, only the body is sanitized first):
  POST /v1/images/generations    images_router.py:49
  POST /v1/embeddings            embeddings_router.py:64   (preserves the x-cache header)
  POST /v1/chat/completions      router.py:82 (non-stream only; preserves the x-cache header)

Behavior: all-finite body → count 0 → byte-identical response, no WARN. Non-finite → null + 1 WARN.
Schema: none — no DB, no migration. No HTTP-contract change (same routes, same status, same shape;
only non-representable floats become null). The streaming path is untouched.
```

Status: FROZEN @ v1 — approved under autonomy:auto (non-security; reuses the v28-frozen pure helper; response-only degrade-not-fail, no contract/schema change)

Least-sure flag surfaced at freeze:
  ⚠ [contract] Placement at the router render site rather than inside each use_case (the v28 STT precedent).
    Why it could be wrong: a reviewer may expect symmetry with audio_use_case. Cost if wrong: none
    functional — both null the same floats; only a style-consistency critique. Decision: the router is the
    single chokepoint that ALSO catches cache-HIT bodies (which bypass the use_case post-call path), so it is
    the more correct placement here; recorded in §0 PLACEMENT DECISION.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: each of the 3 render sites — non-finite→null+200 AND all-finite→unchanged.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_images_nonfinite_body_sanitized_to_null: inject 200 body with nan/inf → POST images → 200, values null
  - test_embeddings_nonfinite_body_sanitized_to_null: inject embedding vector with inf → POST embeddings → 200, null; recorder fired
  - test_chat_nonstream_nonfinite_body_sanitized_to_null: inject chat body with logprob -inf → POST chat stream=false → 200, null
  - test_images_all_finite_body_unchanged: inject all-finite body → POST → 200, body identical (no-regression guard)
</test_plan>

RED result (pytest tests/passthrough_nonfinite_sanitize): 3 failed, 1 passed — red for the RIGHT reason:
  - images/embeddings/chat non-finite tests: raised `ValueError: Out of range float values are not JSON compliant` (the allow_nan=False serialization 500) — exactly the failure the fix removes.
  - the all-finite no-regression test passed pre-build (the path already 200s on a clean body).

Tests live in: `apps/gateway/tests/passthrough_nonfinite_sanitize/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/api/images_router.py` `apps/gateway/src/gateway/proxy/api/embeddings_router.py` `apps/gateway/src/gateway/proxy/api/router.py`
Strategy (ordered batches): 1. images_router: sanitize + WARN before JSONResponse · 2. embeddings_router: same, preserve x-cache · 3. chat router: same on the non-stream branch, preserve x-cache. Each imports `sanitize_non_finite` + a module `logging.getLogger`.
Safety rule (feature-specific): response-only, AFTER the use_case return (usage already recorded); reuse the frozen pure helper (no new dependency); all-finite path stays byte-identical (count 0 ⇒ no WARN, same dict object semantics).
Code lives in: the three routers above (+ tests in `apps/gateway/tests/passthrough_nonfinite_sanitize/`)
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

- [x] all tests pass — new suite 4/4 green; full gateway suite green (single-process, ignore tests/edge).
- [x] coverage did not decrease — added 4 tests across all 3 render sites (non-finite + all-finite no-regression).
- [x] no test or contract was altered during build — only the 3 routers changed; §3 frozen v1 untouched.
- [x] the green was EARNED, not gamed — self refute-read (proportionate to ~30 lines across 3 files mirroring a shipped pattern): tests assert observable behavior (200 not 500, the specific non-finite value is null, finite siblings preserved, all-finite body byte-identical, upstream called = path ran). The red genuinely reproduced the serialization 500. No fixture overfit. Edge cases: cache-HIT bodies are covered because the sanitize sits at the router (the use_case stores raw in cache, the router nulls on every render incl. hits); a non-dict body is handled by the total helper; the all-finite path returns count 0 (no WARN).
- [x] concurrency / timing — N/A; synchronous pure transform on an already-returned body, no IO/shared state. Streaming path untouched.
- [x] no exposed secrets, injection openings, or unexpected dependencies — WARN logs only model + count (no body/secret); reuses the frozen pure helper; no new package.
- [x] layering & dependencies follow CONVENTIONS.md — API layer calls the pure application helper (permitted downward dep); §0 PLACEMENT DECISION records why the router (not the use_case) is the correct chokepoint here.
- [x] reviewed and approved — under autonomy:auto; non-security, degrade-not-fail response hardening reusing v28-frozen code → automated quality gate.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] A non-finite float anywhere in an images/embeddings/chat-non-stream body → 200 with that value null (not 500) — confirmed by the 3 red→green tests.
- [x] An all-finite body is returned unchanged with no WARN — confirmed by test_images_all_finite_body_unchanged (resp.json() == body) + full suite (no existing response regressed).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `sanitize_non_finite` + `_log` are referenced in all 3 routers (proven live: the 3 rejection tests turned green only because the router now nulls the value). Exactly one `_log = logging.getLogger(__name__)` per router (grep -c == 1 each).
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; each new import is used; no dead branch.
- [ ] SEMANTIC (prose / non-code) — N/A (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: autonomy:auto · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `*_nonfinite_sanitized` WARNs per endpoint (images/embeddings/chat) — a spike means an upstream is emitting garbage floats and is worth investigating at the source.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · resolved-here] stt-nonfinite-passthrough §7 delta "apply the same non-finite sanitization to images_router/embeddings_router/proxy router" — CLOSED across all 3 sites (router chokepoint, reuses the frozen helper).
  - [SPEC · open] the sanitize deep-copies the body on EVERY non-stream response (the helper rebuilds containers even at count 0). Negligible for normal bodies, but a fast-path `if not _has_nonfinite` short-circuit could avoid the copy on the hot path if profiling ever flags it (evidence: helper always returns a new structure).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
  - [ADD · open] a v28 §7 delta named the FIX SITES verbatim ("images_router:49, embeddings_router:64, proxy/api/router.py:82") — a precise carry-over delta makes the next task's ground nearly free; reward writing fix-site-specific deltas (evidence: §0 came straight from the delta).
  - [TDD · open] the v28 placement (use_case) was NOT the right place for the siblings — re-derive placement from THIS task's control flow (chat/embeddings cache-HIT bodies bypass the use_case → only converge at the router), don't copy the precedent's location blindly (evidence: §0 PLACEMENT DECISION).
