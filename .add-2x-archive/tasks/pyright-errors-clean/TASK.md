# TASK: Resolve 12 pyright errors (behavior-preserving)

slug: pyright-errors-clean · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true   <!-- the fast lane: a small task, collapsed flow + minimal template. Omit --fast for full rigor. -->

> Fast lane — one small task, minimal sections, filled top-to-bottom. The trust floor still
> holds: a FROZEN §3 contract · ≥1 red test before build · a recorded §6 gate (security = HARD-STOP).

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `src/gateway/core/config.py` (4 int/float coercion validators) · `src/gateway/main.py:~613` (app.state.video_jobs_tasks annotation) + `~969` (middleware-stack cursor) · `src/gateway/proxy/application/use_cases.py:~1915` (cost-recovery gate) · `src/gateway/proxy/domain/web_search.py:80,119` (defensive isinstance on upstream-parsed blocks).
Context (working folder): `make typecheck` = `uv run pyright` (strict; reportUnknown* off per pyproject).
Honors (patterns / conventions): behavior-preserving; the try/except IS the runtime guard; never silence a real bug with a blanket ignore.
Anchors the contract cites: the 12 pyright diagnostics (7 config, 2 main, 1 use_cases, 2 web_search).

---

## 1 · SPECIFY — the rules

Feature: pyright-clean gateway (make typecheck → 0 errors)
Must:
  - `uv run pyright` exits 0 with no runtime-behavior change.
  - config int/float coercions keep their try/except duck-typing (no isinstance narrowing that changes which inputs coerce).
  - web_search defensive isinstance(dict) checks remain (never-crash-on-malformed-upstream).
Reject:
  - any fix that changes observable behavior or weakens a defensive check -> "behavior_regression"
Accept: Given the gateway src, When `uv run pyright` runs, Then it reports 0 errors and the full pytest stays green (no behavior change).
Assumptions: ⚠ use_cases.py:1915 `recoverable` already implies non-None gen-id — confirmed (`== "openrouter" and bool(disconnect_gen_id)`); the guard is narrowing-only, not a bug fix.

---

## 3 · CONTRACT — freeze the shape

```
config.py: int(v)/float(v) + `# type: ignore[arg-type]`  (try/except is the guard)
main.py:613: drop invalid attr annotation -> app.state.video_jobs_tasks = set()
main.py:969: `# type: ignore[assignment]` on _cur (None before lifespan; loop breaks on None)
use_cases.py:1915: gate += `and disconnect_gen_id is not None` (narrowing; behavior-preserving)
web_search.py: retype untrusted inputs as Any -> defensive isinstance stays meaningful
Result: pyright = 0 errors; behavior unchanged.
```

`Least-sure flag surfaced at freeze:` [contract] use_cases gate guard — could it skip a real recovery? No: recoverable already required bool(gen_id); cost = none (narrowing-only).
Status: FROZEN @ v1 — approved by tindang (auto; behavior-preserving cleanup)

---

## 4 · TESTS — failing-first (red)

Plan: the gate IS the regression suite — `uv run pyright` (was 12 errors, must be 0) AND the full pytest must stay green (proves no behavior change). Red-before = the 12 pre-existing pyright errors.
Tests live in: existing `tests/` (no new test; this is a type-correctness + behavior-preservation task proven by pyright-0 + suite-green).

---

## 5 · BUILD — AI writes code

Scope (may touch): config.py · main.py · use_cases.py · web_search.py.
Strategy & known-problem fixes: as planned in §3; the trap dodged = NOT narrowing config coercions / NOT deleting web_search isinstance.
Strategy actually used: as planned.
Code lives in: `src/gateway/`   ·   Constraints: change no test, no contract.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build
- [x] green was EARNED — pyright 0 errors (was 12); full pytest 2020 passed / 0 failures / 86.87% cov
- [x] no exposed secrets, injection openings, or unexpected dependencies (security = HARD-STOP)

Build expectations: `uv run pyright` → "0 errors, 0 warnings, 0 informations" (confirmed first-hand); behavior-preservation confirmed by the green full suite + the 3 target suites green in isolation.

### GATE RECORD
Outcome: PASS
Reviewed by: tindang · date: 2026-06-30
<!-- behavior-preserving type-cleanup; pyright 12→0; no security finding. -->
