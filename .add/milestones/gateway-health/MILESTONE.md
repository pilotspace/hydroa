# MILESTONE: Gateway Health

goal: Restore the gateway's static-quality gates and test suite to fully green: make lint (ruff check + format) clean, make typecheck (pyright) clean, and the 3 pre-existing failing tests (azure_embeddings credential-fixture ×2, guardrails-core stale table invariant) fixed — so make ci passes end-to-end once CI billing returns. Pre-existing debt accumulated while CI was billing-blocked; surfaced by the v0.6.0 e2e/harden pass.
rationale: new-major — maintenance/hardening. The v0.6.0 e2e/harden pass found main's gateway static gates have drifted RED (CI billing-blocked → `make ci` not enforced across many milestones). This is repo health, orthogonal to feature milestones: no new behavior, purely restoring green gates + fixing stale/env-coupled tests. All changes must be behavior-preserving (or, for the 12 pyright errors, real-bug-fixes pinned by a test).
stage: mvp · status: active · created: 2026-06-30T13:51:46+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Restore `make ci` (lint + typecheck + test) to green on the gateway: (1) `ruff format` the ~54 unformatted files + fix the 5 `ruff check` errors (migrations/env.py, objectstore/s3.py, _helios_harness/__init__.py); (2) resolve the 12 `pyright` errors (config.py ×7, web_search.py ×2, main.py ×2, use_cases.py ×1) — each either a real fix pinned by a test or a justified narrow ignore; (3) fix the 3 pre-existing failing tests — azure_embeddings test_post_multipart_unsupported / test_stream_bytes_unsupported (decouple from a seeded azure credential so the "unsupported" assertion is reached deterministically), guardrails_core_migration_column_exists (update the stale expected-tables baseline to include the v40-program tables artifacts/memories/conversations/conversation_messages/video_generation_jobs, OR fix the invariant's scope).
Out: any feature/behavior change; new endpoints; the dashboard (already green); the pre-existing pyright errors that are genuinely third-party/untyped and already config-suppressed elsewhere (document, don't churn); wiring/SHA-pinning the CI workflow (separate, gated on billing returning — tracked as a delta).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Behavior-preserving.** `ruff format` is whitespace/wrap only; ruff-check + pyright fixes must not change runtime behavior. Any pyright error that reveals a REAL bug gets a red test first (TDD), then the fix — never a blanket `# type: ignore` to silence a real defect.
- **No test weakening.** The 3 failing tests are fixed by correcting the test's environment-coupling / stale baseline, OR by a real code fix — never by deleting/loosening a real assertion.
- **Full-suite proof.** Done = `make ci` (lint + typecheck + test) exits 0 on the gateway, re-run first-hand (full pytest, single process — the :5433 cross-wipe rule).

## Shared / risky contracts (freeze these first)
- **The `make ci` green bar** (ruff check + ruff format --check + pyright + full pytest all exit 0) -> the milestone's whole-suite acceptance; each task moves one gate to green without regressing the others.
- **azure_embeddings unsupported-op contract** (the adapter must raise the "unsupported operation" error for multipart/stream regardless of credential state) -> owning task `fix-stale-failing-tests`.
- **guardrails-core table invariant** (the expected-tables baseline the guardrails migration test asserts against) -> owning task `fix-stale-failing-tests`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] ruff-format-and-lint-clean   depends-on: none   — `ruff format` the ~54 unformatted files + fix the 5 `ruff check` errors; verify behavior-preserving (full pytest green). Moves `make lint` → exit 0.
- [ ] pyright-errors-clean         depends-on: none   — resolve the 12 pyright errors (config.py/web_search.py/main.py/use_cases.py); real fixes pinned by a test where a bug exists, justified narrow ignores otherwise. Moves `make typecheck` → exit 0.
- [ ] fix-stale-failing-tests      depends-on: none   — azure_embeddings ×2 (decouple from seeded-credential ordering) + guardrails_core_migration (refresh the expected-tables baseline for the v40-program tables). Moves full pytest → 0 failures.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] `make lint` (ruff check + ruff format --check) exits 0 on the gateway   (← ruff-format-and-lint-clean)
- [ ] `make typecheck` (pyright) exits 0 on the gateway   (← pyright-errors-clean)
- [ ] The full gateway pytest suite passes with 0 failures (azure_embeddings ×2 + guardrails_core_migration green)   (← fix-stale-failing-tests)
- [ ] `make ci` exits 0 end-to-end, re-run first-hand   (← all three tasks)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway (product) : <ruff/pyright/test files — what changed>
- tooling / skill / book : <untouched unless noted>

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS> · tests=<n green> · residue=<note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate + the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Open a gateway PR from the cleanup branch → main; Tin reviews + merges.
- [ ] No migration (cleanup only) — rides the next gateway release (patch bump candidate).
- [ ] Once CI billing returns: run + SHA-pin `make ci` in the workflow so this debt can't re-accumulate (separate delta).
