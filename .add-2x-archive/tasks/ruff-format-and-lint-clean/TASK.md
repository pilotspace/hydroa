# TASK: ruff format + fix 5 ruff check errors (make lint green)

slug: ruff-format-and-lint-clean · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — minimal sections; trust floor holds (FROZEN §3 · red signal before build · recorded §6 gate).

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `migrations/env.py:64` (E501) · `src/gateway/objectstore/s3.py:18` + `tests/_helios_harness/__init__.py:19` (I001 import sort) · `tests/_helios_harness/__init__.py:480` (B007 unused loop vars) · ~53 other files needing `ruff format`.
Context (working folder): `make lint` = `uv run ruff check` + `ruff format --check`; line-length 100; frozen-test exclude list in pyproject.
Honors (patterns / conventions): format is whitespace/wrap only — behavior-preserving; do NOT touch frozen-test files in the exclude list; run format LAST so it covers the other gateway-health changes.
Anchors the contract cites: the 5 ruff-check diagnostics + the unformatted file set.

---

## 1 · SPECIFY — the rules

Feature: lint-clean gateway (make lint → exit 0)
Must:
  - `uv run ruff check .` → "All checks passed!"; `uv run ruff format --check .` → all formatted.
  - No behavior change (format = whitespace; check-fixes = import sort / line wrap / unused-loop-var rename).
Reject:
  - editing a frozen-test file in the pyproject exclude list -> "frozen_test_edit"
Accept: Given the gateway, When `make lint` runs, Then it exits 0 with no behavior change (full pytest stays green).
Assumptions: none material — biggest risk: a format diff touches a #42 adapter; mitigated by formatting on current main (audio_use_case already clean from #44).

---

## 3 · CONTRACT — freeze the shape

```
ruff check fixes: env.py:64 wrap (E501) · s3.py + _helios_harness import sort (I001) ·
                  _helios_harness:480 loop vars case/provider -> _case/_provider (B007)
ruff format: whole gateway (~53 files; #42 adapters already clean — no overlap)
Result: make lint exits 0; behavior unchanged.
```

`Least-sure flag surfaced at freeze:` [contract] format clobbering a #42-changed file — refuted: audio_use_case excluded from the apply, re-formatted clean on current base; 655 files already-formatted.
Status: FROZEN @ v1 — approved by tindang (auto; mechanical lint cleanup)

---

## 4 · TESTS — failing-first (red)

Plan: the gate IS the regression suite — `make lint` (was 5 check errors + 54 unformatted, must be clean) AND full pytest stays green. Red-before = the 5 ruff-check errors + unformatted set.
Tests live in: existing `tests/` (no new test; proven by lint-clean + suite-green).

---

## 5 · BUILD — AI writes code

Scope (may touch): migrations/env.py · objectstore/s3.py · tests/_helios_harness/__init__.py · ~53 formatted files. NOT the pyproject frozen-exclude test files.
Strategy & known-problem fixes: fix the 5 check errors, then `ruff format` LAST; trap dodged = no frozen-test edits, no #42-adapter clobber.
Strategy actually used: as planned (applied via net-diff off main, then `ruff format` on current base).
Code lives in: gateway tree   ·   Constraints: change no frozen test, no contract.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no frozen test or contract altered during build
- [x] green was EARNED — `ruff check` All checks passed; `ruff format --check` 655 files formatted; full pytest 2020 passed / 0 failures
- [x] no exposed secrets, injection openings, or unexpected dependencies (security = HARD-STOP)

Build expectations: `make lint` exits 0 (confirmed first-hand); behavior-preservation confirmed by the green full suite.

### GATE RECORD
Outcome: PASS
Reviewed by: tindang · date: 2026-06-30
<!-- mechanical lint/format cleanup; whitespace + import-sort + loop-var rename; no security finding. -->
