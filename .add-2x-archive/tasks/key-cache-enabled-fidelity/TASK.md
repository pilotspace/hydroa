# TASK: Key list cache_enabled fidelity (gateway serializer fix)

slug: key-cache-enabled-fidelity · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): a ONE-LINE corrective fix to a gateway list serializer that drops an already-available field. Verified anchors:
- `apps/gateway/src/gateway/keys/api/router.py:list_keys` (`GET /admin/keys`, lines 129-156): builds `KeyInfoResponse(... team_id=item.team_id)` per item but OMITS `cache_enabled` — so it falls back to the schema default `False` for EVERY key, regardless of the persisted value.
- `KeyInfoResponse` (`apps/gateway/src/gateway/keys/api/schemas.py:254`): `cache_enabled: bool = False` — the field is ALREADY part of the (frozen) response shape; only the list serializer fails to populate it. The PATCH serializer (`patch_key`, router.py:271) DOES set `cache_enabled=updated.cache_enabled`.
- Data IS available upstream: `ApiKeyInfo` domain entity (`keys/domain/entities.py:63`) has `cache_enabled: bool`; the repository `list_for_tenant` (`keys/infrastructure/repository.py:106`) already constructs each `ApiKeyInfo` with `cache_enabled=bool(getattr(row, "cache_enabled", False))` — the TRUE per-key value. The router just forgets to forward it.
- Impact: the v15 governance-completion-ui key editor reads cache_enabled from the GET /admin/keys list; without this fix the cache Switch shows OFF for a caching-ON key and a save could silently disable caching (the footgun Tin approved fixing 2026-06-14).
- Tests: `apps/gateway/tests/keys/test_api_keys.py` — `signup_and_login(client, ...) -> token`, `bearer(token)`, `ADMIN_KEYS="/admin/keys"`; create POST, PATCH /{id}, GET list patterns proven (test_list_keys_returns_all_without_secrets:197). DB-backed `client` fixture from conftest.

Context (working folder): v15 MILESTONE.md key-cache-enabled-fidelity. A bug fix that makes the list FAITHFUL to the existing KeyInfoResponse contract — NOT a contract/shape change.

Honors (patterns / conventions): mirror the patch_key serializer (which already forwards cache_enabled); CLAUDE.md TDD red→green; no migration, no schema change, no new dependency.

Anchors the contract cites: `list_keys` serializer · `KeyInfoResponse.cache_enabled` (already declared) · `ApiKeyInfo.cache_enabled` (already populated by the repository) · GET /admin/keys.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Make `GET /admin/keys` report each key's TRUE `cache_enabled` value (currently always `false` because the list serializer drops the field). A faithfulness bug fix within the existing response shape; no contract change.
Framings weighed: Add the one missing field to the list serializer (chosen — minimal, mirrors patch_key, zero shape change) · Change KeyInfoResponse default / restructure (rejected — the shape is correct and frozen; only population is wrong) · Fix it in the frontend by ignoring the list value (rejected — the data simply isn't there to ignore; the source must be correct).
Must:
<must>
  - `GET /admin/keys` returns, for each key, the `cache_enabled` boolean that reflects that key's persisted value (true for a cache-enabled key, false otherwise) — not a constant.
  - No other field of the list response changes; the response shape (KeyInfoResponse) is unchanged; tenant-scoping and secret-exclusion are preserved.
</must>
Reject:
<reject>
  - The list reporting cache_enabled=false for a key whose persisted cache_enabled is true -> "stale_field" (the bug)
  - Any change to the KeyInfoResponse shape, a new field, or a migration -> "scope_creep"
  - Leaking a secret / breaking tenant scoping while touching the serializer -> "regression"
</reject>
After:
<after>
  - A key PATCHed (or created) with cache_enabled=true appears in GET /admin/keys with cache_enabled=true; a default key appears with cache_enabled=false; no other behavior changes; the gateway suite stays green.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ none material — the data is provably available at every layer below the serializer (domain entity + repository populate it; patch_key already forwards it). The single biggest risk is a hidden test that asserts the OLD (always-false) behavior; mitigation: run the full keys suite, not just the new test.
  - [ ] cache_enabled is settable via create (router.py:108) and PATCH (router.py:232) — confirmed; the test can use either to arrange a true value.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: List reflects a key's true cache_enabled
  Given an owner creates key A and PATCHes it cache_enabled=true, and creates key B (default)
  When the owner GETs /admin/keys
  Then key A appears with cache_enabled true
  And key B appears with cache_enabled false

Scenario: List shape and safety unchanged
  Given the list response
  Then it still excludes key/key_hash/secret and stays tenant-scoped
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
ENDPOINT  GET /admin/keys  (owner/admin/member — get_identity; tenant-scoped)
  RESPONSE  list[KeyInfoResponse]   (SHAPE UNCHANGED — KeyInfoResponse already declares cache_enabled: bool = False)
  FIX       list_keys serializer now forwards cache_enabled=item.cache_enabled (was dropped → defaulted False)
  INVARIANT each item.cache_enabled == the key's persisted ApiKeyInfo.cache_enabled (true|false), not a constant
  UNCHANGED no shape/field/migration change; secrets excluded; tenant-scoped; all other fields identical
```

Least-sure flag surfaced at freeze: [contract] none material — this is a corrective bug fix that brings the runtime response into line with the ALREADY-FROZEN KeyInfoResponse shape (cache_enabled is declared but unpopulated by list_keys). The only residual risk is an existing test pinning the buggy always-false behavior; the gate runs the full keys suite to catch it. No shape change ⇒ no consumer breakage (the field already exists, defaulted False; values just become accurate).

Status: FROZEN @ v1 — approved by ADD auto (Tin pre-approved this exact 1-line fix 2026-06-14; faithfulness bug fix, no shape change, no security surface).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: keys module coverage held (no decrease).
Plan (one test per scenario — pytest, DB-backed `client` fixture):
<test_plan>
  - test_list_keys_reports_true_cache_enabled: signup→token; create key A; PATCH A cache_enabled=true; create key B (default); GET /admin/keys; assert A.cache_enabled is True AND B.cache_enabled is False. RED today (list_keys drops the field → both False).
  - (the existing test_list_keys_returns_all_without_secrets continues to assert shape/secret-exclusion — no regression.)
</test_plan>

Tests live in: `apps/gateway/tests/keys/test_api_keys.py` · MUST run red (cache_enabled always False) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/keys/api/router.py` `apps/gateway/tests/keys/test_api_keys.py` `.add/tasks/key-cache-enabled-fidelity/` `apps/gateway/.coverage` `apps/gateway/.ruff_cache/`
<!-- SCOPE NOTE: one-line serializer edit in router.py + one red→green test. .coverage/.ruff_cache are gateway verify-tooling artifacts (declared so the scope gate doesn't red). NO schema/migration/dependency change. -->
Strategy (ordered batches): 1. RED test in test_api_keys.py. 2. add `cache_enabled=item.cache_enabled` to the list_keys KeyInfoResponse construction. 3. full keys suite + ruff + pyright green.
Safety rule (feature-specific): mirror patch_key's serializer exactly; touch ONLY the list_keys response construction; no shape/migration change.
Code lives in: `apps/gateway/src/gateway/keys/api/router.py`
Constraints: do NOT change the contract shape or any other test; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite 738 passed (19 deselected); keys module 21/21; the new test_list_keys_reports_true_cache_enabled green
- [x] coverage did not decrease — total 83.21% (gate 80%); keys/api/router.py unchanged-or-up (one field added to an existing return)
- [x] no test or contract was altered during build — §3 FROZEN (shape unchanged); the only edit is the 1-line serializer + the new red→green test
- [x] the green was EARNED, not gamed — RED proven (assert False is True: PATCH set true, list reported false), 1-line fix, GREEN; the test asserts A=true AND B=false (per-key, NOT a constant) so it cannot pass on the old always-false code OR an always-true cheat; full suite re-run clean
- [x] concurrency / timing of the risky operation is safe — pure read serializer; no new I/O, no state, no async change
- [x] no exposed secrets, injection openings, or unexpected dependencies — list still excludes key/key_hash/secret (existing test_list_keys_returns_all_without_secrets still green); tenant-scoping unchanged; no new dependency/migration
- [x] layering & dependencies follow CONVENTIONS.md — mirrors patch_key serializer; data sourced from the domain ApiKeyInfo the repository already populates; no layer crossed
- [x] a person reviewed and approved the change — Tin pre-approved this exact 1-line fix (2026-06-14, AskUserQuestion "Fix the 1-line gateway bug"); auto-gated on evidence (no security finding)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `cache_enabled=item.cache_enabled` references ApiKeyInfo.cache_enabled (entities.py:63), populated by repository.list_for_tenant (repository.py:106); exercised by the new test via GET /admin/keys
- [x] DEAD-CODE (code) — no new symbol; one existing field now forwarded
- [x] SEMANTIC (prose / non-code) — n/a (code task)

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: Tin Dang (pre-approved the fix) / ADD auto (evidence-gated) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): n/a runtime monitor — this is a one-shot serializer fidelity fix; the governance-completion-ui RTL suite (cache toggle reflects true state) is the downstream guard.
Spec delta for the next loop: a list serializer and its single-item/PATCH sibling can drift — the list dropped a field the PATCH response forwarded. A schema-level "all declared fields populated" check (or a shared serializer helper) would prevent the recurrence.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
- [SDD · folded] Hand-written per-endpoint serializers drift from their schema — list_keys dropped cache_enabled while patch_key forwarded it, silently defaulting the list to False (evidence: test_list_keys_reports_true_cache_enabled was RED). A shared KeyInfoResponse.from_domain(item) builder would make every endpoint forward every field by construction.
- [TDD · folded] A fidelity test must distinguish "true value forwarded" from "constant returned" — asserting only A=true would pass an always-true cheat; pairing A=true with B=false pins the per-key semantics (evidence: the test asserts both).
