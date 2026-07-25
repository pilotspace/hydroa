# Method — the ADD spec

project: ai-proxy · seeded: 2026-07-24 · stage: production

> Living document — how we work: the loop, autonomy, ceremony budget (ADD).
> Keep the sections below CURRENT (state, not history); lessons land under
> Deltas the moment they are learned: `add.py delta-append add "<lesson>"`.
> A delta that changes the standing picture is folded UP into the sections
> above it and marked `[folded]` — the Deltas list is the inbox, not the spec.

## Now
<the standing ADD picture — replace this placeholder as the project firms; task-delta updates, never a full re-scan>

## Decisions that bind
<the ADD-lens decisions every task must honor — one line each, with the task/ADR that set it — or leave the placeholder until the first one lands>

## Deltas (newest first)
- [open · 2026-07-25] A duplicated load-bearing security primitive WILL drift. Three hand-copies of the same 'SELECT zdr_enabled ... FOR UPDATE' existed; each independently documented why the lock was necessary, and the fourth site that needed it got a plain re-read anyway. When a heal lands, sweep for every sibling of the pattern in the SAME milestone — the recurring failure is not the bug, it is the un-back-applied lesson. (evidence: zdr-toctou, third instance) (task:zdr-ingest-lock-heal)
- [open · 2026-07-25] An independent refuter that reports an anomaly it CANNOT explain is worth more than one that reports clean. Refuter A surfaced its own lowest self-evaluation score (0.75) over one unreproducible data point — and that data point was a real defect in the task's own test. Reward the disclosure; never prompt refuters toward a clean verdict. (evidence: zdr-ingest-lock-heal dual refute) (task:zdr-ingest-lock-heal)
- [open · 2026-07-24] adding a Tin-required test to a FROZEN suite post-freeze trips tamper_detected:build_tampered at the gate — re-cross to re-snapshot the sanctioned addition, then advance+gate (same ordering as editing a frozen test) (task:responses-state-store)
- [open · 2026-07-24] worktree BUILD agents see only COMMITTED state — commit .add/ direction bundles + red suites on the feature branch BEFORE dispatching any isolation:worktree builder, else it reports 'task does not exist'; also commit before re-dispatch after a heal (task:responses-state-store)
<!-- prepended by `add.py delta-append add "<text>"` — one line per lesson, `- [open · <date>] <lesson>` + the active-task stamp; fold a delta upward, then retag [open]->[folded] -->
