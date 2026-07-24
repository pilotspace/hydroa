# PLAN: /v1/moderations exposing the ML-moderation evaluator, metered

slug: moderations-endpoint · created: 2026-07-24 · stage: production
milestone: api-surface-parity
autonomy: auto
phase: build
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `POST /v1/moderations` — OpenAI-wire client-facing text-classification endpoint over Hydroa's existing ML-moderation infrastructure (isolated CircuitBreaker, BYOK-resolved OpenAI `/v1/moderations`), billed per_token to the calling tenant even though the upstream call itself is free to Hydroa.

Framings weighed:
  A. **(chosen)** New thin vertical slice (router+deps+use-case, mirrors `embeddings_router.py`/`embeddings_deps.py`/`embeddings_use_case.py`) that additively widens `OpenAiModerationClient` [OBSERVED `apps/gateway/src/gateway/proxy/infrastructure/ml_moderation_evaluator.py`] with one new method returning the FULL upstream wire body, and reuses the SAME `app.state.ml_moderation_provider` singleton [OBSERVED `main.py:1512`] the internal guardrail already calls — one isolated breaker for the whole moderation IO seam, internal-guardrail traffic and client-facing traffic share fate honestly (it IS the same upstream dependency).
  B. Reuse `MlModerationGuardrailEvaluator.evaluate_pre` directly — REJECTED: its return shape is a block/audit/unchecked guardrail verdict (`GuardrailResult`), not the OpenAI wire shape (no `category_scores`, no full 13-key `categories`, no per-tenant fail_open/fail_closed semantics a *client* should ever see — a client that explicitly called `/v1/moderations` wants an honest classification or an honest error, never a silently-degraded "unchecked→pass").
  C. A second, fully independent `OpenAIDirectProvider`+breaker instance dedicated to this endpoint — REJECTED: doubles the moderation-IO blast-radius surface for no isolation benefit (chat/embeddings isolation is what §0 R3 of ml-moderation-layer actually protects; the client-facing and internal-guardrail paths sharing one breaker is correct, not a leak).

Must:
<must>
  - M1: `POST /v1/moderations` accepts the real OpenAI wire body `{model, input}` where `input` is a `string` or a `list[string]`; validated + governed through `NonChatGovernance.authorize` [OBSERVED `apps/gateway/src/gateway/proxy/application/governance.py::NonChatGovernance.authorize`] — same 9-step gate embeddings/images use, `estimated_tokens=None` (moderation has no TPM dimension, mirrors `images_use_case.py`/`audio_use_case.py`).
  - M2: access is gated ONLY by the model catalog (an active `models` row, `modality='moderation'`) — never by the tenant's `ml_moderation` guardrail config block (that block governs the INTERNAL pre-call check on chat/embeddings/etc.; a client calling this endpoint directly wants a classification, not an audit-policy decision). Framings/assumption ⚠ below.
  - M3: the upstream call is made via `app.state.ml_moderation_provider` [OBSERVED `main.py:1512`, `nonchat_guardrail_deps.py:33`] — the dedicated isolated-breaker/tight-timeout instance — through a NEW additive method on `OpenAiModerationClient` (`moderate_raw`) that returns the FULL parsed JSON body (`id`, `model`, `results[].{flagged,categories,category_scores,category_applied_input_types}`) [OBSERVED via find-docs 2026-07-24, `developers.openai.com/api/docs/api-reference/moderations`: 13 category keys incl. `harassment/threatening`, `hate/threatening`, `illicit`, `illicit/violent`, `self-harm/*`, `sexual/minors`, `violence/graphic`] — NEVER the narrow `ModerationVerdict` (`flagged`+`categories: list[str]`) the guardrail path uses, and NEVER `select_provider`/`ProviderRegistry` (that path is the SHARED chat/embeddings breaker — routing moderation traffic through it would defeat the isolation `ml-moderation-layer` froze).
  - M4: the SAME unconditional PII scrub (`mask_pii_in_messages`) [OBSERVED `ml_moderation_evaluator.py::evaluate_pre` call site] runs over `input` before it leaves the process to the 3rd-party provider — parity with the internal guardrail's own protection, never weaker for the client-facing path.
  - M5: per-tenant BYOK `openai` credential resolved via `resolve_provider_credential` + `platform_credential_fallback` [OBSERVED `use_cases.py::resolve_provider_credential`, reused verbatim by `embeddings_use_case.py`] — a missing/disabled key surfaces as `ERR_PROVIDER_KEY_MISSING` (402), NEVER the internal guardrail's silent `fail_open`/`fail_closed` degrade (that degrade exists to protect an UNRELATED chat/embeddings call from a moderation-check failure; here the moderation call itself IS the whole request).
  - M6: a provider-side failure — circuit OPEN, timeout, network error, or a 200 with a malformed/missing `results` body — raises `ERR_UPSTREAM_UNAVAILABLE` (502). `app.state.ml_moderation_provider` entirely unwired (platform never boot-configured ML-moderation infra) raises `ERR_PROVIDER_UNAVAILABLE` (503, distinct code — "not configured" vs "configured but down"). **NEVER a fabricated `flagged:false`/"safe" result on any failure path** — the milestone's named honesty invariant.
  - M7: exactly ONE `usage_records` row fires on a 2xx response (single-bill invariant, mirrors `embeddings_use_case.py` Step 7); pricing_unit `per_token` (new catalog `pricing_snapshots` row, seed migration mirrors `b64d469b341e_tool_call_metering_seed.py`); ZERO usage rows on any rejection/failure path (never billed for a call that never completed).
  - M8: billed quantity — the real OpenAI moderation response carries NO `usage`/token-count field [OBSERVED via find-docs 2026-07-24 — the example response has no `usage` key; `ml_moderation_evaluator.py`'s own module docstring independently confirms the endpoint is free/uncounted]. Billed `prompt_tokens` = a deterministic word-count estimate over the ACTUAL (PII-scrubbed) input text(s) actually sent — a real, reproducible measurement of real input, never an invented number — tagged `usage_source="estimated_word_count"` on the record so it is provably never confused with a provider-reported count. **Named v1 boundary (advisor-surfaced, folded 2026-07-24):** a naive whitespace split under-counts CJK/Thai/other non-whitespace-delimited scripts toward ~1 "word" for an entire paragraph — the estimator MUST fall back to a char-count-based proxy (`max(whitespace_word_count, len(text) // 4)`, the same ~4-chars/token heuristic industry tokenizers approximate) whenever the whitespace-split count looks implausibly low relative to text length, so non-Latin-script tenants are never near-zero-billed. ⚠ top assumption below.
  - M8b (advisor-surfaced, folded 2026-07-24): `usage_source="estimated_word_count"` disclosure MUST reach further than the internal `usage_records` row alone — "never fabricated internally" is not the same guarantee as "the tenant is never surprised." Spec delta: the tenant-facing usage/invoice line-item for this model SHOULD surface the estimation basis (e.g. an `estimated: true` flag or note wherever `usage_source` already renders today). Deferred as `[SPEC · open]` to §7 rather than gated here — v1 ships the internal disclosure (§4 test_mod3 gates that much); invoice-UI surfacing depends on the sibling `tenant-usage-costs-api` task's read shape (also wave-1, contract not yet frozen) and is out of THIS task's Scope.
  - M9: this endpoint does NOT run `evaluate_nonchat_request_guardrails` over its own input — running the moderation guardrail against a moderation-classification request is circular (the endpoint IS the check); embeddings/images/audio keep their existing guardrail wrap unchanged.
</must>
Reject:
<reject>
  - missing/blank `model` -> "amount_invalid"-style reuse: `ERR_PAYLOAD_MODEL_REQUIRED` (422) [reuse `PAYLOAD_MODEL_REQUIRED`]
  - missing/empty `input` (`None`/`""`/`[]`) -> `ERR_PAYLOAD_INPUT_REQUIRED` (422) [reuse `PAYLOAD_INPUT_REQUIRED`]
  - `input` is a list containing a non-string item (image content-parts / mixed types — image moderation is OUT of v1 scope, ships in the sibling `image-edits-variations` task only for images/edits, never here) -> `ERR_MODERATION_INPUT_UNSUPPORTED` (422, NEW code)
  - `model` unknown/inactive in catalog -> `ERR_MODEL_UNKNOWN` (400) [reuse `MODEL_UNKNOWN`]
  - `model` active but `modality != "moderation"` -> `ERR_MODEL_MODALITY_MISMATCH` (400) [reuse `MODEL_MODALITY_MISMATCH`]
  - missing/invalid API key -> `ERR_AUTH_INVALID_KEY` (401) [reuse, via governance]
  - budget exceeded (per-key/team/tenant) -> `ERR_BUDGET_EXCEEDED` (402) [reuse, via governance]
  - no BYOK credential + no platform fallback -> `ERR_PROVIDER_KEY_MISSING` (402) [reuse, via `resolve_provider_credential`]
  - RPM/TPM limited -> `ERR_RATE_LIMITED` (429) [reuse, via governance — TPM dimension skipped, `estimated_tokens=None`]
  - `app.state.ml_moderation_provider` unwired -> `ERR_PROVIDER_UNAVAILABLE` (503, NEW use of existing `PROVIDER_UNAVAILABLE` spec, `provider="ml_moderation"`)
  - provider circuit open / timeout / malformed 200 body -> `ERR_UPSTREAM_UNAVAILABLE` (502) [reuse `UPSTREAM_UNAVAILABLE`]
</reject>
After:
<after>
  - a well-formed request returns the real OpenAI moderation wire shape (`id`,`model`,`results[]` with all 13 categories + scores), one `results[]` entry per `input` item, in the SAME order.
  - exactly one `usage_records` row exists for the request, `pricing_unit='per_token'`, cost computed from the seeded `omni-moderation-latest` pricing snapshot × the deterministic word-count quantity.
  - on ANY failure (validation, governance, provider outage) zero usage rows exist and the response is one of the named error codes above — never a 200 with a fabricated verdict.
  - every OTHER endpoint (chat/embeddings/images/audio/messages) is byte-identical — zero new plumbing engaged; the only shared-file touches are additive (one new method, one new router mount line, one new migration, one new error code, one widened `Modality` literal).
</after>
Boundary: text-only `input` (`string` or `list[string]`) — image/content-part input shapes (the multimodal moderation variant find-docs surfaced) are explicitly OUT of v1 and rejected with a named code, not silently mis-parsed.
<assumptions>
  ⚠ M8 (billed quantity is a LOCAL word/char-estimate, not provider-reported): lowest confidence because it is the one place this task deviates from "bill on real provider usage" — the real endpoint simply never returns a token count, so ANY per_token billing here is necessarily an estimate. Advisor-pressure-tested 2026-07-24 (`add-advisor`, propose-plan): confirmed the estimate itself is defensible ("never fabricated" — real deterministic function of real input) but flagged its WEAKEST edge (0.7 confidence) was a naive whitespace split under-billing CJK/Thai/non-whitespace-delimited scripts toward ~1 "word" per paragraph — folded as the `max(word_count, ceil(chars/4))` fallback rule now in M8, gated by §4 test_mod16. Mitigated further by disclosure (`usage_source="estimated_word_count"`) rather than silence. If wrong (Tin wants exact provider-grade counting): swap the estimator function only — `usage_source` tag + `pricing_unit` shape are unaffected, a pure internal swap, no contract break.
  ⚠ M2 (catalog-gated access, no `ml_moderation` guardrail-config dependency) — lowest-but-one confidence: plausible alternative reading of "exposing the EXISTING evaluator" is that the endpoint should be gated identically to the guardrail (only reachable when `ml_moderation.enabled` is configured for the tenant). Rejected because that would make a tenant's classification API availability depend on an unrelated audit-policy toggle — but flagging since it is a genuine two-way door. If wrong: adding the gate later is additive (one extra check in `authorize`'s caller), no contract break.
  ⚠ pricing rate: `prompt_usd_per_token` seeded at a PLACEHOLDER `$0.0000001` (~$0.10 / 1M tokens) — ASSUMED, not a business decision this task can make; mirrors `tool_call_metering_seed.py`'s "DECIDED by Tin at freeze" precedent — needs Tin's actual number at/after freeze (migration data-only, trivially re-seedable).
  ⚠ M8b (invoice-facing disclosure of the estimate) — advisor-surfaced 2026-07-24: `usage_source="estimated_word_count"` lands on the internal `usage_records` row only; nothing today requires it reach the tenant-facing invoice/usage line-item. DECIDED at draft: defer as `[SPEC · open]` (§7) rather than gate here — v1 ships the internal disclosure (test_mod3 gates that much); surfacing it on an invoice line depends on the sibling `tenant-usage-costs-api` task's read shape (wave-1, contract not yet frozen), so building it here would reach outside this task's declared Scope.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Grounding (reasoned in-context; not transcribed — see §1 OBSERVED tags for anchors)
Touches: `proxy/api/` (new `moderations_router.py`, `moderations_deps.py`), `proxy/application/` (new `moderations_use_case.py`), `proxy/infrastructure/ml_moderation_evaluator.py` (additive `moderate_raw` method only — the frozen `evaluate_pre` 2-arg shape, `ModerationVerdict`, and `MlModerationGuardrailEvaluator`/`CompositeGuardrailEvaluator` are UNTOUCHED), `catalog/domain/entities.py` (additive: widen `Modality` Literal + `VALID_MODALITIES` frozenset with `"moderation"` — confirmed [OBSERVED] this set has zero live enforcement call sites today, so this is documentation-only hygiene, not a behavior change), `core/error_catalog.py` (one new `ERR_MODERATION_INPUT_UNSUPPORTED` spec), `main.py` (mount new router — same pattern as `embeddings_router`), one new Alembic migration (data-only seed, mirrors `b64d469b341e_tool_call_metering_seed.py`).
Anchors (may cite ONLY these): `NonChatGovernance.authorize` (`governance.py`) · `resolve_provider_credential` + `_fire_record_with_raw` (`use_cases.py`) · `OpenAiModerationClient`, `MODERATION_CONNECT_TIMEOUT_S`/`MODERATION_READ_TIMEOUT_S`/`MODERATION_MAX_RETRIES`/`MODERATION_BACKOFF_BASE_S`/`MODERATION_RETRY_DEADLINE_S`, `mask_pii_in_messages` call site (`ml_moderation_evaluator.py`) · `app.state.ml_moderation_provider` (`main.py:1512`) · `select_provider`/`ProviderRegistry` (`provider_registry.py`, explicitly NOT used for the upstream call) · `ModelRow` (`catalog/infrastructure/orm.py`) · `PAYLOAD_MODEL_REQUIRED`/`PAYLOAD_INPUT_REQUIRED`/`MODEL_UNKNOWN`/`MODEL_MODALITY_MISMATCH`/`UPSTREAM_UNAVAILABLE`/`PROVIDER_UNAVAILABLE`/`PROVIDER_KEY_MISSING` (`core/error_catalog.py`) · Envoy `/v1/` prefix match [OBSERVED `infra/envoy/envoy.yaml:206`] — already covers any new `/v1/*` route, zero infra touch needed.
Honors: byte-identical default path (additive-only touches on shared files) · every proxied request → ≤one usage record billed via the existing `pricing_unit` dispatcher · no outbound IO without timeout+retry+breaker (reuses `MODERATION_*` constants + the SAME breaker instance) · tenant scoping (governance `authz.tenant_id` on the fired record) · fail-closed on security, honest degradation everywhere (M6/M8) · Envoy ext_authz already covers `/v1/*`.
Issues/Risks: the `moderate_raw` widen touches a file whose module docstring declares "CONTRACT — FROZEN @ v1" — advisor-confirmed 2026-07-24 (`add-advisor`, propose-plan) that the frozen scope is narrow (`evaluate_pre`'s 2-arg shape, Protocol conformance, dedicated-breaker isolation per §0 R3/R6) and the widen touches none of it; a second, separate breaker instance (rejected Framing C) would be WORSE, not safer — two breakers over one upstream dependency produce incoherent circuit state (one reporting OPEN while the other reports CLOSED). Build adds one docstring line noting "additively extended by moderations-endpoint task" for future-reader clarity (non-gated). Pricing rate is a placeholder (see ⚠ above).
Issues/Risks (advisor-surfaced 2026-07-24): exposing `/v1/moderations` changes the SHARED `app.state.ml_moderation_provider` breaker's traffic profile — previously bounded only by internal chat/embeddings call volume (itself gated by their own governance), now also directly reachable by any tenant hammering this endpoint. A client-side burst could trip the shared breaker OPEN and degrade the INTERNAL guardrail's fail-open protection for unrelated chat/embeddings requests it has nothing to do with. Not rejected as a design flaw (see above — a second breaker is worse), but recorded as a §6 VERIFY watch-item: confirm `NonChatGovernance`'s existing RPM/TPM/budget ladder (already gating this endpoint per M1) bounds the achievable call rate enough that this is a theoretical rather than practical risk before recording an auto-PASS.
Ground SHA: not stamped by this draft (engine-stamped at freeze).

### Contract (freeze the shape — the HARD, tamper-guarded core)

```
POST /v1/moderations   body: { model: string, input: string | string[] }
  200 -> { id: string, model: string, results: [
             { flagged: bool,
               categories: { harassment, harassment/threatening, hate, hate/threatening,
                              illicit, illicit/violent, self-harm, self-harm/instructions,
                              self-harm/intent, sexual, sexual/minors, violence,
                              violence/graphic: bool },
               category_scores: { <same 13 keys>: float },
               category_applied_input_types: { <same 13 keys>: string[] } } ]  }
             — one results[] entry per input item, same order
  400 -> { code: "ERR_MODEL_UNKNOWN" | "ERR_MODEL_MODALITY_MISMATCH" }
  401 -> { code: "ERR_AUTH_INVALID_KEY" | "ERR_AUTH_KEY_EXPIRED" }
  402 -> { code: "ERR_BUDGET_EXCEEDED" | "ERR_PROVIDER_KEY_MISSING" }
  403 -> { code: "ERR_MODEL_NOT_ALLOWED" | "ERR_PLAN_MODEL_NOT_ALLOWED" }
  422 -> { code: "ERR_PAYLOAD_MODEL_REQUIRED" | "ERR_PAYLOAD_INPUT_REQUIRED" | "ERR_MODERATION_INPUT_UNSUPPORTED" }
  429 -> { code: "ERR_RATE_LIMITED" }
  502 -> { code: "ERR_UPSTREAM_UNAVAILABLE" }
  503 -> { code: "ERR_PROVIDER_UNAVAILABLE" }
Schema: `models` row (id="omni-moderation-latest", modality="moderation", provider="openai",
  active=true) + `pricing_snapshots` row (pricing_unit="per_token",
  prompt_usd_per_token=0.0000001 PLACEHOLDER, completion_usd_per_token=0) — additive
  data-only migration, no DDL, no new table. Per-request: ≤1 `usage_records` row
  (existing table/columns, existing recorder — no schema change).
```

Target (measurable): §4 red suite green (18 tests: 1 happy-path shape/billing + 15 reject/edge cases + 2 coordinator-flagged coverage-gap tests, M5/M9) + `make ci` (pyright strict, ruff) clean on all touched files + the embeddings/images/chat regression suites stay green (byte-identical-path proof, run as part of the host floor). Streaming/UI render N/A — pure JSON API.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock
Scope (may touch): `apps/gateway/src/gateway/proxy/api/moderations_router.py` · `apps/gateway/src/gateway/proxy/api/moderations_deps.py` · `apps/gateway/src/gateway/proxy/application/moderations_use_case.py` · `apps/gateway/src/gateway/proxy/infrastructure/ml_moderation_evaluator.py` · `apps/gateway/src/gateway/catalog/domain/entities.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/migrations/versions/` · `apps/gateway/tests/moderations_endpoint/` · `.add/tasks/moderations-endpoint/`

Regression floor: `apps/gateway/tests/embeddings_endpoint/` · `apps/gateway/tests/guardrails/test_ml_moderation.py` (proves the additive widen never regresses the frozen guardrail behavior) · `apps/gateway/tests/pricing_units/` (proves the `per_token` default path unaffected) — run chunked, never the full 12-core-saturating suite in one shot (host lesson).
Persona (optional): generic backend-API-surface persona (no domain-specific `.add/personas/` slug matched this task's `task-kinds`/`use-when`; the objective's own persona brief — 15y backend engineer, API-surfaces-over-existing-domain-services — carried this draft).

Least-sure flag surfaced at freeze: [contract] — M8's billed-quantity estimator (word-count over real input, `usage_source`-disclosed) is the one place this bundle substitutes a local computation for "real provider usage," because the real upstream literally never reports a token count for this endpoint; every other Must/Reject/error-code is a direct, live-grounded reuse of an existing seam.

### AI-verify record (required when gate_mode: ai-plan-verify)
N/A — `gate_mode: ai-plan-verify` not declared on this task's header; the human freeze is the gate.

---

## 4 · TESTS & SCENARIOS — failing-first suite (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_mod1_happy_path_string_input_full_wire_shape: arrange active `omni-moderation-latest` model + fake `ml_moderation_provider` returning a full 13-category body / act POST with `input: "hello"` / assert 200, body has `id`,`model`,`results[0]` with all 13 category+score keys · covers: M1,M3
  - test_mod2_happy_path_array_input_one_result_per_item: arrange same / act POST with `input: ["a","b"]` / assert `results` length 2, order preserved · covers: M1
  - test_mod3_single_usage_record_per_token_word_count_quantity: arrange / act POST `input: "one two three four five"` / assert recorder called exactly once, `usage.prompt_tokens == 5`, `usage_source == "estimated_word_count"` · covers: M7,M8
  - test_mod4_pii_scrub_applied_before_upstream_call: arrange input containing a built-in-pattern PII literal (e.g. an email) / act POST / assert the fake provider's captured payload has the PII redacted, not raw · covers: M4
  - test_mod5_missing_model_422: act POST with no `model` / assert 422 `ERR_PAYLOAD_MODEL_REQUIRED` · covers: R:ERR_PAYLOAD_MODEL_REQUIRED
  - test_mod6_empty_input_422: act POST `input: ""` / assert 422 `ERR_PAYLOAD_INPUT_REQUIRED` · covers: R:ERR_PAYLOAD_INPUT_REQUIRED
  - test_mod7_array_input_non_string_item_422: act POST `input: ["ok", {"type":"image_url"}]` / assert 422 `ERR_MODERATION_INPUT_UNSUPPORTED` · covers: R:ERR_MODERATION_INPUT_UNSUPPORTED
  - test_mod8_unknown_model_400: act POST with an unseeded `model` id / assert 400 `ERR_MODEL_UNKNOWN` · covers: R:ERR_MODEL_UNKNOWN
  - test_mod9_wrong_modality_model_400: arrange an existing chat model id / act POST with it as `model` / assert 400 `ERR_MODEL_MODALITY_MISMATCH` · covers: R:ERR_MODEL_MODALITY_MISMATCH
  - test_mod10_missing_api_key_401: act POST with no Authorization header / assert 401 `ERR_AUTH_INVALID_KEY` · covers: R:ERR_AUTH_INVALID_KEY
  - test_mod11_provider_unwired_503: arrange `app.state.ml_moderation_provider = None` / act POST / assert 503 `ERR_PROVIDER_UNAVAILABLE`, zero usage records fired · covers: M6,R:ERR_PROVIDER_UNAVAILABLE
  - test_mod12_provider_outage_502_no_fabricated_safe_result: arrange fake provider raising `UpstreamUnavailableError` / act POST / assert 502 `ERR_UPSTREAM_UNAVAILABLE`, response body has NO `flagged`/`results` key at all (never a fabricated safe verdict), zero usage records fired · covers: M6
  - test_mod13_budget_exceeded_402: arrange a key at its `monthly_budget_usd` ceiling / act POST / assert 402 `ERR_BUDGET_EXCEEDED`, zero usage records fired (upstream never called) · covers: R:ERR_BUDGET_EXCEEDED
  - test_mod14_no_ml_moderation_guardrail_config_still_reachable: arrange tenant with NO `ml_moderation` guardrail config at all (or `enabled: false`) / act POST / assert 200 — catalog access only, guardrail config is irrelevant to this endpoint · covers: M2
  - test_mod15_chat_regression_untouched: reuse `embeddings_endpoint`-style chat regression fixture / act POST `/v1/chat/completions` / assert 200, byte-identical to pre-task behavior (GREEN-BY-DESIGN floor, proves zero new plumbing on the default path) · covers: After (byte-identical default path)
  - test_mod16_cjk_input_char_fallback_never_near_zero_billed (advisor-surfaced, folded 2026-07-24): arrange / act POST `input: "テストテストテストテストテスト"` (15 CJK chars, 0 whitespace boundaries — a naive `.split()` would count this as 1 "word") / assert `usage.prompt_tokens == ceil(15 / 4) == 4` (the `max(word_count, ceil(chars/4))` fallback engaged, NOT `1`) · covers: M8
  - test_mod17_no_credential_no_fallback_402_provider_key_missing (coordinator-flagged gap, folded 2026-07-24): arrange a `tenant_credential_resolver` that always raises `ProviderKeyMissing("openai")` + `platform_credential_fallback = None` / act POST / assert 402 `ERR_PROVIDER_KEY_MISSING`, zero provider calls, zero usage records — NEVER the internal guardrail's silent fail_open/fail_closed degrade · covers: M5
  - test_mod18_own_input_never_guardrail_blocked (coordinator-flagged gap, folded 2026-07-24): arrange `app.state.guardrail_evaluator` = an evaluator that unconditionally returns `blocked=True` (simulates a tenant with a block-mode guardrail policy configured — the SAME test-injection seam `nonchat_guardrail_deps.py::resolve_guardrail_evaluator` documents) / act POST with an injection-family input string / assert 200 — never 400 `ERR_GUARDRAIL_BLOCKED` — proving the endpoint does not circularly route its own input through `evaluate_nonchat_request_guardrails` · covers: M9
</test_plan>

Rigor: one red test per Must/Reject primary case above; test_mod7 (malformed array item), test_mod14 (no guardrail config), test_mod16 (CJK billing fallback, advisor-surfaced), test_mod17 (no-credential 402, coordinator-flagged), and test_mod18 (own-input-never-guardrail-blocked, coordinator-flagged) are the primary edge cases explicitly named in the objective/review and are gated; the remaining minor edges (e.g. RPM/TPM 429, plan-allowlist 403) are already exercised generically by `NonChatGovernance`'s OWN frozen test suite and are build-guidance here, not re-gated.

Tests live in: `apps/gateway/tests/moderations_endpoint/`

Red evidence [OBSERVED 2026-07-24, `cd apps/gateway && uv run pytest tests/moderations_endpoint/ -q --no-cov -p no:cacheprovider`, re-run AFTER folding the coordinator's `add.py check` coverage-gap findings (M5/M9 had no §4 `covers:`) and adding test_mod17/test_mod18]:
```
17 failed, 1 passed in 12.53s
```
17/17 gated tests (mod1-mod14, mod16-mod18) fail with `AssertionError: expected HTTP <NNN>, got
404: {"detail":"Not Found"}` (or the equivalent 200/quantity assertion also short-circuited by
the same 404) — right-reason red (route does not exist yet), never an import/collection error.
Individually re-run to confirm no import error hid inside the new fakes (`FailingCredentialResolver`,
`AlwaysBlockGuardrailEvaluator`): both mod17/mod18 collected and executed cleanly, failing only on
the 404 assertion. The 1 passed is `test_mod15_chat_regression_untouched` — GREEN-BY-DESIGN (proves
`/v1/chat/completions` already resolves before this task touches anything; must stay green after BUILD).

18 tests total (mod1-mod18); 17 red for the right reason, 1 green-by-design.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY>
Code lives in: `apps/gateway/src/gateway/proxy/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` for the freeze `--cross` and the §6 refute-read.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope; keep the §3 Regression floor green; allow-list packages only (this task adds NO new pyproject dependency — pure stdlib word-count, no tiktoken); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change
- [ ] **watch-item (advisor-surfaced 2026-07-24):** confirm `NonChatGovernance`'s existing RPM/TPM/budget ladder bounds the achievable call rate against the SHARED `app.state.ml_moderation_provider` breaker enough that exposing it to direct external traffic cannot practically trip it OPEN and starve the internal guardrail's fail-open path for unrelated chat/embeddings requests — cite the actual limit values checked, not just "governance runs first"

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
