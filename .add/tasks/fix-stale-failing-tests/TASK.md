# TASK: Fix 3 stale tests (azure embeddings x2 + guardrails baseline)

slug: fix-stale-failing-tests · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — minimal sections; trust floor holds (FROZEN §3 · red-before · recorded §6 gate). NEVER weaken a real assertion.

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `src/gateway/proxy/infrastructure/azure_embeddings.py` (post_multipart, stream_bytes) · `tests/guardrails/test_guardrails_core.py` (test_guardrails_core_migration_column_exists — expected-tables manifest).
Context (working folder): dev Postgres :5433 (no azure credential seeded); the guardrails invariant asserts "guardrails-core adds no new tables".
Honors (patterns / conventions): fix env-coupling / stale baseline only — NEVER weaken or delete a real assertion; fixes are correctness-true.
Anchors the contract cites: the canonical UpstreamUnavailableError type; the v40-program table set.

---

## 1 · SPECIFY — the rules

Feature: deterministic stale tests (the 3 pass without depending on accidental env/baseline state)
Must:
  - azure post_multipart / stream_bytes surface the canonical UpstreamUnavailableError when a credential is missing (not the raw ProviderKeyMissing); STT/TTS with credentials still work.
  - the guardrails migration invariant recognizes the v40-program tables (artifacts, memories, video_generation_jobs, conversations, conversation_messages) as known-prior, and still catches any NEW table guardrails-core itself would add.
Reject:
  - making a test pass by deleting/loosening a real assertion -> "test_weakened"
Accept: Given the 3 previously-failing tests, When run in isolation, Then all pass (azure 10 + guardrails 19) with assertions intact and no behavior regressed.
Assumptions: ⚠ azure post_multipart/stream_bytes are credential-conditional (not unconditionally unsupported) — confirmed; the first unconditional-raise attempt broke 8 azure_audio tests, so the wrap-ProviderKeyMissing fix is the correct one.

---

## 3 · CONTRACT — freeze the shape

```
azure_embeddings.post_multipart / stream_bytes:
  try: cfg, cred = self._resolve_config_and_cred()
  except ProviderKeyMissing as exc: raise UpstreamUnavailableError(str(exc)) from None
  (STT/TTS with creds unchanged)
guardrails test: NOT-IN manifest += {artifacts, memories, video_generation_jobs,
                 conversations, conversation_messages}  (invariant still catches new guardrails-core tables)
Result: azure 10 + guardrails 19 pass in isolation; assertions intact.
```

`Least-sure flag surfaced at freeze:` [contract] does wrapping ProviderKeyMissing hide a real config bug? No — callers treat UpstreamUnavailableError as the canonical upstream failure; ProviderKeyMissing was an un-canonical leak. cost: none.
Status: FROZEN @ v1 — approved by tindang (auto; correctness-true test restoration)

---

## 4 · TESTS — failing-first (red)

Plan: the 3 named tests are the red suite (azure test_post_multipart_unsupported / test_stream_bytes_unsupported + test_guardrails_core_migration_column_exists) — all failing before, all green after, assertions unchanged.
Tests live in: existing frozen-allowed `tests/azure_embeddings/`, `tests/guardrails/`.

---

## 5 · BUILD — AI writes code

Scope (may touch): azure_embeddings.py (src) + test_guardrails_core.py (baseline manifest only).
Strategy & known-problem fixes: wrap ProviderKeyMissing->UpstreamUnavailableError in both azure methods; extend the guardrails NOT-IN manifest. Trap dodged = NOT an unconditional raise (would break 8 audio tests); NOT deleting the invariant.
Strategy actually used: as planned (agent self-corrected from a first unconditional-raise attempt).
Code lives in: gateway tree   ·   Constraints: change only the stale test's baseline, not its assertions.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no real assertion weakened
- [x] green was EARNED — azure 10 + guardrails 19 pass in isolation; full pytest 2020 passed / 0 failures
- [x] no exposed secrets, injection openings, or unexpected dependencies (security = HARD-STOP)

Build expectations: the 3 named tests pass in isolation with assertions intact (confirmed first-hand: `pytest tests/azure_embeddings tests/guardrails` = 29 passed).

OBSERVE [SPEC · open]: the full single-process suite is FLAKY under cross-suite Redis/DB contamination (stateful suites pass in isolation + on re-run but can fail together) — a pre-existing test-isolation defect, candidate for a deterministic-isolation follow-up task.

### GATE RECORD
Outcome: PASS
Reviewed by: tindang · date: 2026-06-30
<!-- env-coupling + stale-baseline fix; assertions intact; no security finding. Cross-suite flakiness noted as open delta. -->
