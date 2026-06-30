# TASK: Deterministic test isolation — kill cross-suite Redis/DB contamination

slug: deterministic-test-isolation · created: 2026-06-30 · stage: production
autonomy: auto
phase: ground   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> SCAFFOLD ONLY — §0/§1 scoped for a future execution session; NOT yet built. Surfaced as the
> gateway-health open delta. Trust floor still applies when built (FROZEN §3 · red-before · §6 gate).

---

## 0 · GROUND — the real codebase

Symptom (observed, gateway-health 2026-06-30): the full single-process `make test` is FLAKY. One run
had 8 failures + 1 error across stateful suites — `response_caching` (×3), `routing_config_store`
(boot-applies-persisted-config, errored), `openrouter_cost_recovery` (idempotent-second-recover),
`pii_v2` (v4-email-literal) — **all green in isolation AND on a clean re-run** (2020 passed / 0 fail).
Trigger: fixing the previously-early-failing azure/guardrails tests made them run to completion, whose
residual Redis/DB state tips later suites over. Root cause = suites share Redis (:6380) + Postgres
(:5433) and don't reset the keys/rows they assert on between modules.

Touches (likely): `tests/conftest.py` (+ per-suite conftests for the contaminating/contaminated suites) ·
the Redis/DB fixtures · whatever seam seeds spend-counters / response-cache keys / the routing_config
singleton / cost-recovery idempotency rows.
Constraints: MANY affected test files are FROZEN (in the pyproject ruff-exclude list — response_caching,
pii_v2, routing_config_store, …). Fix must land in SHARED fixtures / conftest teardown / the
contaminating suite's cleanup — NOT by editing a frozen assertion. Memory rule: selective XTRIM /
targeted DELETE between suites — NEVER FLUSHDB (cross-wipes the shared dev Redis); ONE pytest process
at a time (:5433 cross-wipe).
Anchors: the shared Redis/DB fixture scope; the per-suite state each failing assertion depends on.

---

## 1 · SPECIFY — the rules (scope for the build session)

Feature: deterministic full-suite green (no re-run-on-flake workaround)
Must:
  - `make test` (single process) passes with 0 failures DETERMINISTICALLY across repeated runs (e.g. 3× green).
  - Each stateful suite either resets the shared Redis keys / DB rows it asserts on in its own setup/teardown,
    OR an autouse session/module fixture isolates state between modules — without FLUSHDB and without editing
    frozen test assertions.
Reject:
  - FLUSHDB or any global wipe of the shared dev Redis -> "shared_redis_wiped"
  - editing a frozen-test assertion to dodge contamination -> "frozen_test_edit"
Accept: Given the full suite run 3× back-to-back single-process, When complete, Then every run is 0-failures
  (the 8 known-flaky tests included), proving isolation — assertions unchanged.
Assumptions: ⚠ the contaminator is ONE (or few) suites leaking Redis spend-counters / cache keys / the
  routing_config singleton row — confirm by bisecting (run the contaminated suite preceded by each candidate)
  before designing the fixture; the fix may be a single shared autouse teardown.

Investigation plan (build session): (1) reproduce deterministically — find the ordering that fails (pytest -p
no:randomly with the full collection; or bisect with `--sw`/explicit module order); (2) identify the leaked
state per failing assertion (Redis key prefixes, table rows); (3) add targeted teardown in shared/contaminating
conftest (selective XTRIM/DELETE); (4) prove 3× green.

---

## 3 · CONTRACT — freeze the shape
```
<freeze at the build session, after the contaminator + leaked-state are identified>
```
Status: DRAFT (scaffold — not yet built)

---

## 6 · VERIFY — evidence + gate
- [ ] full `make test` single-process is 0-failures across 3 consecutive runs (deterministic)
- [ ] no FLUSHDB / shared-Redis wipe; no frozen-test assertion edited
- [ ] no exposed secrets / injection / unexpected deps (security = HARD-STOP)

### GATE RECORD
Outcome: <pending — scaffold only>
