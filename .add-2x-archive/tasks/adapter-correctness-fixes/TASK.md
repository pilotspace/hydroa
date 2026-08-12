# TASK: Provider-adapter docs-faithfulness fixes (TDD)

slug: adapter-correctness-fixes · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — built in a worktree-isolated backend agent off main. Floor held: FROZEN §3 (the corrected
> mappings + terminal-error contract) · red tests before each fix · recorded §6 gate. Additive only.

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` (_map_gemini_finish_reason) · `bedrock_upstream.py` (_map_finish_reason) · `anthropic_upstream.py` (_map_finish_reason + _AnthropicSSEStepper.step) · `apps/gateway/src/gateway/proxy/application/audio_use_case.py` (_STT_PASSTHROUGH_FIELDS)
Context (working folder): new tests under tests/{gemini_provider,bedrock_provider,anthropic_provider,audio_endpoints}. Derived from a read-only 5-adapter docs-vs-code audit (see tmp/proxy-correctness-audit.md).
Honors (patterns / conventions): ADDITIVE response-mapping only; reuse the existing terminal-error contract (_anthropic_error_to_openai + `data: [DONE]`); never weaken an existing assertion; no new dep, no migration. bedrock_sigv4 SERVICE is VERIFIED-CORRECT and OFF-LIMITS.
Anchors the contract cites: _map_finish_reason (3 adapters), _AnthropicSSEStepper.step, _STT_PASSTHROUGH_FIELDS

---

## 1 · SPECIFY — the rules

Feature: faithful provider→OpenAI mapping for the deltas the audit found.
Must:
  - Gemini content-policy finishReasons (BLOCKLIST, PROHIBITED_CONTENT, SPII, IMAGE_SAFETY) → "content_filter".
  - Bedrock `model_context_window_exceeded` → "length".
  - Anthropic `refusal` → "content_filter"; `pause_turn` documented (defaults "stop", no OpenAI-native equiv).
  - Anthropic mid-stream `event: error` → terminal error frame (existing contract) + [DONE], no double-[DONE], no hang.
  - OpenAI STT forwards `timestamp_granularities` + `chunking_strategy`.
Reject:
  - any change to bedrock_sigv4 SERVICE / signing name -> "sigv4_untouchable" (verified-correct false-positive)
  - weakening/removing an existing test -> "additive_only"
Accept: each corrected mapping/passthrough is pinned by a NEW red→green test; all existing gateway tests stay green.
Assumptions: malformed_model_output/malformed_tool_use stay "stop" (Bedrock model errors, no clean OpenAI equiv) — documented inline.

---

## 3 · CONTRACT — freeze the shape

```
gemini_upstream._map_gemini_finish_reason: + BLOCKLIST|PROHIBITED_CONTENT|SPII|IMAGE_SAFETY -> "content_filter"
bedrock_upstream._map_finish_reason:        + model_context_window_exceeded -> "length"
anthropic_upstream._map_finish_reason:      + refusal -> "content_filter" (covers non-stream AND streaming message_delta)
anthropic_upstream._AnthropicSSEStepper.step: + elif event=="error": emit _anthropic_error_to_openai terminal chunk + [DONE], set _terminal_emitted; message_stop guards double-[DONE]
audio_use_case._STT_PASSTHROUGH_FIELDS:     + "timestamp_granularities","chunking_strategy"
UNTOUCHED: bedrock_sigv4.py (SERVICE="bedrock" verified vs AWS get-vanilla vectors).
```

`Least-sure flag surfaced at freeze:` [contract] the mid-stream error MUST reuse the existing terminal-error shape (not a new contract) and never double-emit [DONE] — pinned by test_midstream_error_no_double_done_when_followed_by_message_stop.
Status: FROZEN @ v1 — approved by Tin Dang (project-lead autonomous approval under the standing "make sure all LLM proxy correct as docs" directive)

---

## 4 · TESTS — failing-first (red)

Plan (17 new, red→green): gemini_finish_reason_extended (5) · bedrock_finish_reason_extended (3) · anthropic_finish_reason_extended (3) · anthropic_midstream_error (3) · stt_passthrough_fields (3). Each suite includes an "existing mappings unchanged" guard.
Tests live in: `apps/gateway/tests/{gemini_provider,bedrock_provider,anthropic_provider,audio_endpoints}/`

---

## 5 · BUILD — AI writes code

Scope (may touch): the 4 adapter/use-case files + their test dirs ONLY.
Strategy & known-problem fixes: red tests per fix → minimal additive mapping/branch → green; trap: double-[DONE] on error-then-message_stop (dodged via _terminal_emitted guard); trap: "fixing" the Bedrock sigv4 false-positive (explicitly NOT touched).
Strategy actually used: as planned (worktree backend agent off main; 5 atomic commits dc1f7ea/c7e6af7/fd31201/6207d15/e2a0f0c).
Code lives in: `apps/gateway/src/gateway/proxy/`   ·   Constraints: additive only; no new dep; no migration.

---

## 6 · VERIFY — evidence + gate

- [x] all new tests pass · no existing test or contract altered · no test weakened (17/17 new green; existing suite green)
- [x] green was EARNED — orchestrator re-ran the 17 new tests via `uv run pytest` in the worktree FIRST-HAND → 17 passed; diff confirmed gateway-only; bedrock_sigv4 NOT in the diff
- [x] no exposed secrets, injection openings, or unexpected dependencies (additive mapping only; ruff clean; pyright no NEW errors — 4 pre-existing same as main; no new dep)

Build expectations (from §1 Accept + §3 CONTRACT): Gemini/Bedrock/Anthropic finish_reason completeness · Anthropic mid-stream error → terminal frame · OpenAI STT passthrough — confirmed by the 17 new tests. Bedrock SigV4 verified-correct + untouched.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (orchestrator-driven; orchestrator re-ran 17/17 first-hand + verified sigv4 untouched) · date: 2026-06-30
<!-- OBSERVE: [SPEC · open] deferred audit NITs as deltas — OpenAI stream_options.include_usage (handled via v27 stream_fallback), OpenRouter usage:{include} staleness + HTTP-Referer/X-Title headers + native_tokens_reasoning/_completion_images, Anthropic thinking_delta passthrough + message_delta.input_tokens re-read. -->
