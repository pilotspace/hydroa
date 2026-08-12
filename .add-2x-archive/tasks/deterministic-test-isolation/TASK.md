# TASK: Deterministic test isolation — kill cross-suite Redis/DB contamination

slug: deterministic-test-isolation · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> BUILT + gate=PASS (2026-06-30). Surfaced as the gateway-health open delta; resolved the same session.
> Trust floor held: FROZEN §3 · red-before (isolation pin 1-failed→3-passed) · §6 gate · 3× deterministic green.

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
tests/conftest.py :: _clear_usage_leaks_if_reachable (autouse `_isolate_stores`, BEFORE each test):
  WIDENED from usage-only clear to FULL per-test Redis (db 9) reset, still surgical:
    - XTRIM usage:events maxlen=0   (clear backlog; PRESERVE the stream + ledger-flusher group)
    - DEL every key WHERE key NOT IN {b"usage:events", "usage:events"}   (scan_iter match="*")
  => no inherited Redis state of ANY namespace (resp-cache:/embed-cache:/ratelimit:/bandwidth:/
     soft_budget:/worker-sweeps/…) bleeds across tests; NO FLUSHDB (consumer group survives).
Root cause: the old clear left every non-usage namespace to accumulate across suites / inherit
  from a prior partial run → stateful suites contaminated each other (flaky full run).
Safety: all fixtures function-scoped (app recreated per test); NO module/session-scoped Redis seed
  (verified — only migration_db + email-domain allowlist are higher-scoped, neither touches Redis).
```
`Least-sure flag surfaced at freeze:` [contract] could full-clear wipe state a suite seeds once and relies on? Refuted — no higher-scoped Redis-seeding fixture exists; broad clear proven by the full suite staying green (flusher/usage/semantic_cache suites included).
Status: FROZEN @ v1 — approved by tindang (auto; test-infra isolation, behavior of src unchanged)

---

## 4 · TESTS — failing-first (red)

`tests/redis_isolation/test_redis_isolation.py` (NEW, not frozen):
- test_a writes `resp-cache:` + `ratelimit:` keys; test_b asserts they were cleared before it ran
  (RED with old fixture: `resp-cache:…` survived `b'cached-body'`); test_c asserts the usage:events
  stream + ledger-flusher consumer group survive the clear (proves no FLUSHDB).
Red→green confirmed: 1 failed→3 passed.

---

## 5 · BUILD — AI writes code

Scope (touched): `tests/conftest.py` (widen the autouse clear) + `tests/redis_isolation/` (new pin).
Strategy actually used: scan_iter match="*" minus the preserved stream → DEL; kept XTRIM usage:events.
Trap dodged: NOT FLUSHDB (would NOGROUP the flusher); NOT editing any frozen test assertion.
Constraints honored: src/gateway behavior UNCHANGED (test-infra only); allow-list packages only.

---

## 6 · VERIFY — evidence + gate
- [x] full `make test` single-process is 0-failures across 3 consecutive runs (deterministic) — **2023 passed / 0 failed ×3** (8:13 / 8:09 / 8:06)
- [x] no FLUSHDB / shared-Redis wipe; no frozen-test assertion edited (only tests/conftest.py + new file)
- [x] no exposed secrets / injection / unexpected deps (security = HARD-STOP) — none
- [x] red→green earned: isolation pin 1-failed→3-passed; victim+flusher suites 77 passed; ruff/format clean

Build expectations: the full single-process suite is now DETERMINISTICALLY green (was flaky: one run had 8 cross-suite-contamination failures). Proven by 3 consecutive 0-failure runs first-hand.

### GATE RECORD
Outcome: PASS
Reviewed by: tindang · date: 2026-06-30
<!-- test-infra isolation only (widened autouse Redis clear); src/gateway behavior unchanged; no FLUSHDB; no frozen-test edits; 3× deterministic green. No security finding. -->
