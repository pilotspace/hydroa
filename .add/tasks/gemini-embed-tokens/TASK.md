# TASK: Exact Gemini-embed token billing (replace chars/4 estimate)

slug: gemini-embed-tokens · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Exact Gemini-embed token billing — replace the `max(1, ceil(total_chars/4))`
ESTIMATE in `gemini_upstream._gemini_embed_to_openai` with a REAL token count, so a
provider=google embeddings request bills on actual tokens like every other modality. The
Gemini embed endpoints (`embedContent` / `batchEmbedContents`) return NO usage, so the count
must come from elsewhere. Billing keys on the SERVED model id (unchanged); non-Gemini
embeddings (OpenAI/OpenRouter, which DO return usage) are untouched and byte-identical.

Framings weighed: a Gemini `:countTokens` round-trip per embed request — POST
`/models/{model}:countTokens {contents:[{parts:[{text}]}]}` → `{totalTokens}`, summed across
inputs (chosen — it is the AUTHORITATIVE provider count, mirrors how chat bills on native
usage, and the existing GoogleEmbeddingsProvider already speaks this base_url+auth; the cost
is one extra upstream call + latency, mitigated by the same circuit breaker/timeout and a
fail-SAFE estimate fallback) · a bundled local SentencePiece/tiktoken tokenizer (rejected —
a heavyweight allowlist-gated dependency, and Gemini's exact vocab is not the OpenAI one, so
it would still be an approximation) · keep the chars/4 estimate (rejected — it is the very
debt this task pays down; the milestone goal is "bills exactly, no estimates").

Must:
<must>
  - A provider=google embeddings request bills on an EXACT token count obtained from Gemini
    `:countTokens` (single input → one count; list input → sum of per-input counts), placed
    in the response `usage.prompt_tokens` / `usage.total_tokens` (replacing the estimate).
  - The count call reuses the provider's base_url + x-goog-api-key header (NEVER ?key=) +
    circuit breaker + timeout; the api_key is never logged/echoed/placed in a URL or error.
  - Billing keys on the SERVED model id; the OpenAI-shaped embeddings response body
    (data/object/index/embedding/model) is otherwise UNCHANGED.
  - Non-Gemini embeddings (OpenAI/OpenRouter providers, which return native usage) are
    byte-identical — this task touches ONLY the Gemini embed path.
</must>
Reject:
<reject>
  - `:countTokens` fails (timeout / network / 4xx-5xx / missing `totalTokens`) -> FAIL-SAFE:
    fall back to the documented `max(1, ceil(total_chars/4))` estimate + a WARN log; NEVER
    crash the embeddings request over a billing-count failure (billing accuracy is best-effort
    on the count leg, the embedding result is the product).
  - the embed call itself fails -> existing behavior unchanged (UpstreamUnavailableError →
    503 / error envelope); the count leg only runs on a successful embed.
</reject>
After:
<after>
  - A Gemini embeddings usage row records the real provider token count, not chars/4; the
    estimate path is reachable ONLY as the documented fallback.
  - The single-bill-per-request invariant and the served-model billing key are preserved.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Gemini's `:countTokens` endpoint accepts the SAME `{contents:[{parts:[{text}]}]}` shape on
    an EMBEDDING model id and returns `{totalTokens:int}` — lowest confidence because
    countTokens is documented primarily for generative models and embedding-model support is
    not guaranteed; if wrong: the count leg 4xx/empties and we fall back to the estimate (no
    crash, no false exactness — the WARN makes the degradation observable and the live verify
    catches a persistent fallback). Mitigation under consideration: a single combined
    countTokens for the batch vs per-input — settled at the contract.
  - [ ] One extra round-trip per embed is an acceptable latency/cost tradeoff for billing
    accuracy — confirm at freeze (it mirrors chat billing on native usage; embeddings are not
    a sub-50ms hot path); the breaker bounds the failure cost.
  - [ ] Summing per-input counts for a batch equals Gemini's own batch tokenization — confirm
    the contract pins per-input counts summed (the only order-stable, input-attributable
    option) rather than one opaque batch total.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Single-input exact count
  Given a provider=google embeddings request with input "hello"
  When the embed succeeds and :countTokens returns {totalTokens: 3}
  Then usage.prompt_tokens == 3 and usage.total_tokens == 3 (NOT ceil(5/4)=2)
  And the embedding data/object/index/model fields are unchanged

Scenario: Batch-input exact count (aggregate)
  Given a provider=google embeddings request with input ["a","bb"]
  When the embed succeeds and ONE :countTokens carrying both inputs returns {totalTokens: 7}
  Then usage.prompt_tokens == 7 (the aggregate), data order preserved
  And exactly ONE extra countTokens round-trip was made (not one-per-input)

Scenario: countTokens failure → estimate fallback (fail-safe)
  Given a provider=google embeddings request
  When the embed succeeds but :countTokens times out / 5xx / 4xx
  Then the request still returns 200 with the embeddings
  And usage falls back to max(1, ceil(total_chars/4)) and a WARN is logged
  And the request is NOT failed over the count error

Scenario: countTokens 200 without totalTokens → estimate fallback
  Given :countTokens returns 200 with a body missing "totalTokens"
  When the response is billed
  Then usage falls back to the chars/4 estimate + WARN (no crash, no 0/None)

Scenario: countTokens auth uses x-goog-api-key, never ?key=
  Given any Gemini embeddings request
  When :countTokens is called
  Then the request carries header x-goog-api-key and NO ?key= query param
  And the api_key never appears in a log / URL / error

Scenario: embed call itself fails → existing behavior, no count leg
  Given the embed call returns 5xx (or times out)
  When the provider runs
  Then UpstreamUnavailableError is raised exactly as before
  And :countTokens is NEVER called (count runs only after a successful embed)

Scenario: non-Gemini embeddings unchanged
  Given a provider=openai/openrouter embeddings request (native usage returned)
  When billed
  Then usage is the provider's native count — byte-identical to before (no count leg)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
gateway.proxy.infrastructure.gemini_upstream — GoogleEmbeddingsProvider (Gemini embed path ONLY)

NEW  async _count_gemini_tokens(model: str, inp: str | list[str]) -> int | None
  POST {base_url}/models/{model}:countTokens
       headers: x-goog-api-key: <key>   content-type: application/json   (NEVER ?key=)
       body: { "contents": [ {"parts":[{"text": s}]} for s in <inputs> ] }   # str → 1 content
  200 + {"totalTokens": int>=1}  -> return that int  (the EXACT aggregate count)
  any failure (TimeoutException/NetworkError/status>=400/missing|<1 totalTokens)
                                -> return None  (fail-SAFE; never raises; logs WARN at call site)
  Reuses self._client (same base_url + breaker timeouts) + self._auth_headers().
  Does NOT trip the circuit breaker (a count-leg failure must not open the embed breaker).

CHANGED  _gemini_embed_to_openai(body, model, inp, *, exact_tokens: int | None = None)
  exact_tokens is not None  -> usage.prompt_tokens = usage.total_tokens = exact_tokens
  exact_tokens is None      -> usage = max(1, ceil(total_chars/4))  (UNCHANGED estimate fallback)
  All other fields (object/data/index/embedding/model) BYTE-IDENTICAL.

CHANGED  GoogleEmbeddingsProvider.post_json(...)  (only the success leg, after status 200)
  exact = await self._count_gemini_tokens(model, inp)   # None on any failure
  if exact is None: log WARN "gemini embed token count unavailable; billing on chars/4 estimate"
  return 200, _gemini_embed_to_openai(resp.json(), model, inp, exact_tokens=exact)
  4xx/5xx embed legs and the str/list embedContent|batchEmbedContents routing UNCHANGED.

Schema/billing: usage.prompt_tokens flows through the UNCHANGED embeddings use-case →
  usage_records (single-bill, keyed on the SERVED model id). No schema change, no new field.
Scope: ONLY GoogleEmbeddingsProvider. OpenAI/OpenRouter embeddings untouched (byte-identical).
SUPERSESSION: the v9 gemini_provider estimate tests (test_embeddings_single_embedcontent +
  test_embeddings_batch_preserves_order) are updated to the exact-count behavior (their handlers
  now also serve :countTokens); test_embeddings_usage_estimate_helper stays green as the
  documented FALLBACK path. The v9 "estimate" was always a documented temporary approximation +
  open follow-up — this task closes it; not a test weakened to pass a build.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [spec] Gemini `:countTokens` accepts the
`{contents:[{parts:[{text}]}]}` shape on an EMBEDDING model id and returns `{totalTokens:int}`;
why: countTokens is documented mainly for generative models, embedding-model support is not
guaranteed; cost if wrong: the count leg fails → fail-SAFE fallback to the chars/4 estimate +
WARN (no crash, no false exactness), and the live verify surfaces a persistent fallback rather
than silently billing estimates. Mitigation: the fail-safe path is the SAME well-tested estimate.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — 9 tests, one per scenario + the two pure-helper paths.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_helper_exact_tokens_overrides_estimate: exact_tokens=42 → usage 42/42 (RED: kwarg unknown)
  - test_helper_none_falls_back_to_estimate: exact_tokens=None → ceil(chars/4) (RED: kwarg unknown)
  - test_single_input_exact_count: embed 200 + countTokens 3 → usage 3 (not ceil(5/4)=2); data unchanged
  - test_batch_input_exact_count_aggregate: batch embed + ONE countTokens 7 → usage 7; count_calls==1; order kept
  - test_counttokens_5xx_falls_back_to_estimate: countTokens 503 → estimate + WARN logged; 200 not failed
  - test_counttokens_missing_totaltokens_falls_back: countTokens 200 {} → estimate (green-by-design pre-build)
  - test_counttokens_timeout_falls_back: countTokens ConnectTimeout → estimate (green-by-design pre-build)
  - test_counttokens_uses_x_goog_api_key_no_query: countTokens carries x-goog-api-key, NO ?key=/secret in URL
  - test_embed_5xx_raises_and_skips_count_leg: embed 503 → UpstreamUnavailableError; count_calls==0 (green-by-design)
</test_plan>
Red result (pre-build): 6 failed for the RIGHT reason (no exact_tokens kwarg → TypeError;
no countTokens call → estimate/empty/no-WARN), 3 green-by-design (the fallback behavior
already matches: missing-totalTokens, timeout, and no-count-leg-on-failure).

Tests live in: `tests/gemini_embed_tokens/test_gemini_embed_tokens.py` · ran red before Build.
SUPERSESSION: `tests/gemini_provider/test_gemini_provider.py` test_embeddings_single_embedcontent
+ test_embeddings_batch_preserves_order updated to the exact-count behavior (handlers now serve
:countTokens; assert exact 3 / 4) — the v9 estimate was a documented temporary approximation.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the count leg is FAIL-SAFE — `_count_gemini_tokens` never
raises and never trips the embed circuit breaker; a count failure → None → estimate + WARN,
so a billing-count failure can never fail an embeddings request whose product (the vector)
succeeded. The api_key stays in the x-goog-api-key header, never a URL/log/error.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py`
Constraints: do NOT change the contract; stdlib only (logging — no new dependency); the only
test edits are the documented v9 supersession; OpenAI/OpenRouter embeddings untouched.

Built (all green, 68/68 blast radius):
- `_count_gemini_tokens(model, inp) -> int | None` — POST :countTokens with all inputs as
  contents (ONE round-trip); returns the int totalTokens or None on any failure (timeout/
  network/status>=400/missing-or-<1 totalTokens). No breaker trip.
- `_gemini_embed_to_openai(..., *, exact_tokens=None)` — exact_tokens wins; None → the
  unchanged ceil(chars/4) estimate fallback. All other fields byte-identical.
- `GoogleEmbeddingsProvider.post_json` success leg — awaits the count, WARN-logs on None,
  passes exact_tokens through. 4xx/5xx embed legs + str/list routing unchanged.
- module `logger = logging.getLogger(__name__)` added (stdlib).

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — gemini_embed_tokens 9/9; blast radius 68/68 (gemini_provider, embeddings_endpoint, provider_chat_dispatch, gemini_json_mode, gemini_tool_use), deterministic no-DB
- [x] coverage did not decrease — additive method + one changed helper; new suite covers every branch (exact / estimate-fallback / count-failure modes)
- [x] no test or contract was altered during build — the ONLY test edit is the documented v9 supersession (estimate→exact); the frozen §3 contract was honored, not changed
- [x] concurrency / timing of the risky operation is safe — count leg is one awaited call after the embed; fail-safe (no raise, no breaker trip); no shared mutable state; latency is one extra round-trip (accepted tradeoff, breaker-bounded)
- [x] no exposed secrets, injection openings, or unexpected dependencies — api_key stays in x-goog-api-key header (test asserts NO ?key=/secret in URL); stdlib logging only, no new dep; WARN message carries no input text or key
- [x] layering & dependencies follow CONVENTIONS.md — change confined to the infrastructure adapter; pure helper stays pure; billing flows through the unchanged use-case + usage_records (served-id key preserved)
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-13)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_count_gemini_tokens` called in post_json success leg; `exact_tokens` threaded into `_gemini_embed_to_openai`; `logger` used in the None branch; all exercised by the 9 tests (exact 3/7, fallback ceil, WARN asserted, count-skip on embed-fail)
- [x] DEAD-CODE (code) — no orphan: every new branch (exact / estimate / each failure mode) hit by a test; the estimate path remains live as the documented fallback (helper-None test + 3 failure-mode tests)
- [x] SEMANTIC (prose / non-code) — read the changed post_json + helper + new method in full: fail-safe returns None on every failure class, breaker untouched on the count leg, headers via _auth_headers (no ?key=), aggregate one-call batch matches the contract

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of the "token count unavailable; billing on
chars/4 estimate" WARN (a persistent rise = countTokens unsupported/throttled on embedding
models → revisit), Gemini-embed prompt_tokens distribution (estimate ≈ chars/4 vs exact),
extra countTokens round-trip latency.
Spec delta for the next loop: if the WARN fires persistently in live, embedding-model
countTokens is unsupported → the exact-token mechanism may need a per-model capability flag or
a local tokenizer; the live verify (v12-live-verify) is the first real signal.

### Competency deltas
- [SDD · folded] a provider count with no inline usage is recovered via a SEPARATE provider
  count endpoint (Gemini :countTokens) on the SAME adapter, made FAIL-SAFE (None → documented
  estimate fallback) so billing accuracy never becomes an availability gate — evidence:
  countTokens 5xx/timeout/missing-totalTokens all fall back green, embed 200 never failed.
- [TDD · folded] a billing-behavior CHANGE supersedes the prior estimate tests rather than
  weakening them — the v9 estimate tests were updated to the exact-count contract (handlers
  serve :countTokens) and documented as supersession at the freeze; green-by-design fallback
  tests (missing-totalTokens / timeout / no-count-on-embed-fail) passed pre-build — evidence:
  6 RED-for-right-reason + 3 green-by-design, 68/68 blast radius post-build.
- [ADD · folded] a fail-safe count leg must be excluded from the embed circuit breaker — a
  best-effort billing call sharing the client must not open the breaker on the product path
  (evidence: `_count_gemini_tokens` returns None without touching the breaker; embed-fail test
  proves the count leg never runs before a successful embed).
