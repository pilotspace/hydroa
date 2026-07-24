# Quality — the TDD spec

project: ai-proxy · seeded: 2026-07-24 · stage: production

> Living document — how we know it works: test strategy, floors, evidence (TDD).
> Keep the sections below CURRENT (state, not history); lessons land under
> Deltas the moment they are learned: `add.py delta-append tdd "<lesson>"`.
> A delta that changes the standing picture is folded UP into the sections
> above it and marked `[folded]` — the Deltas list is the inbox, not the spec.

## Now
<the standing TDD picture — replace this placeholder as the project firms; task-delta updates, never a full re-scan>

## Decisions that bind
<the TDD-lens decisions every task must honor — one line each, with the task/ADR that set it — or leave the placeholder until the first one lands>

## Deltas (newest first)
- [open · 2026-07-24] independent adversarial refute-reads caught 4 real defects 5 green suites missed: split-usage-frame stream zeroed billing (responses), a 20MiB body-cap masking the contracted 413 range (files), fromtimestamp overflow 500 vs contracted 422 (usage), + the shared-breaker cross-tenant availability coupling (moderations, Tin HARD-STOP→CR-1) — the earned-green refute is load-bearing, not ceremony (task:responses-state-store)
<!-- prepended by `add.py delta-append tdd "<text>"` — one line per lesson, `- [open · <date>] <lesson>` + the active-task stamp; fold a delta upward, then retag [open]->[folded] -->
