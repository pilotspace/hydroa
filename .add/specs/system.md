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
- [open · 2026-07-25] A structural 'one definition site' test must match the EXECUTABLE artifact, not tokens. Its first predicate flagged three legitimate unrelated locks (collapsing them would have been a real defect); its second matched PROSE in docstrings describing the lock. Match the exact SQL/code literal. (evidence: zdr-ingest-lock-heal M3 test, narrowed twice) (task:zdr-ingest-lock-heal)
<!-- prepended by `add.py delta-append sdd "<text>"` — one line per lesson, `- [open · <date>] <lesson>` + the active-task stamp; fold a delta upward, then retag [open]->[folded] -->
