# System — the SDD spec

project: ai-proxy · seeded: 2026-07-25 · stage: production

> Living document — how it is built: architecture, contracts, data shapes (SDD).
> Keep the sections below CURRENT (state, not history); lessons land under
> Deltas the moment they are learned: `add.py delta-append sdd "<lesson>"`.
> A delta that changes the standing picture is folded UP into the sections
> above it and marked `[folded]` — the Deltas list is the inbox, not the spec.

## Now
<the standing SDD picture — replace this placeholder as the project firms; task-delta updates, never a full re-scan>

## Decisions that bind
<the SDD-lens decisions every task must honor — one line each, with the task/ADR that set it — or leave the placeholder until the first one lands>

## Deltas (newest first)
- [open · 2026-08-07] a workflow file's own header comment is not enforcement: kind-e2e.yml declared itself 'Heavy + opt-in by design ... NOT in the fast ci.yml lane' while its on: block carried a pull_request trigger path-filtered on apps/** — matching essentially every PR, and drifting from the Tin-approved ci-restoration CR v2 that said workflow_dispatch-only. 0 green in 15 attempts trained everyone to ignore checks. A contract amendment that changes CI posture needs a guard test, not a comment (evidence: test_kind_e2e_is_dispatch_only) (task:ci-timeout-and-e2e-scope)
- [open · 2026-07-25] A structural 'one definition site' test must match the EXECUTABLE artifact, not tokens. Its first predicate flagged three legitimate unrelated locks (collapsing them would have been a real defect); its second matched PROSE in docstrings describing the lock. Match the exact SQL/code literal. (evidence: zdr-ingest-lock-heal M3 test, narrowed twice) (task:zdr-ingest-lock-heal)
<!-- prepended by `add.py delta-append sdd "<text>"` — one line per lesson, `- [open · <date>] <lesson>` + the active-task stamp; fold a delta upward, then retag [open]->[folded] -->
