# MILESTONE: Memory Playground

goal: The Memory workspace becomes a Console-grade memory library: searchable/sortable semantic memory with a detail inspector and add/delete, over the existing /v1/memories endpoints.
rationale: new-major — milestone 3 of the "AI feature depth (Console-grade)" program. Pass-through rebuild of the thin memory surface over the existing /v1/memories* endpoints (no backend delta). Built in parallel (worktree-isolated agent), reconciled to the milestone line.
stage: production · status: active · created: 2026-06-30T11:08:38+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A Console-grade memory library at /app/memory — two-pane (searchable/sortable semantic memory list + detail inspector), add/delete, over the existing /v1/memories* endpoints (pass-through).
Out: pgvector / server-side ranking changes; memory editing beyond add/delete; cross-tenant sharing; backend changes (none — pass-through).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Pass-through over /api/gw/v1/memories* (no gateway change); null-score rule (never fabricate a numeric relevance score); four UI states + WCAG 2.2 AA; reuse chat Console language.

## Shared / risky contracts (freeze these first)
- Memory library + inspector + score-bar render contract -> owning task `memory-console-rebuild`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] memory-console-rebuild   depends-on: none   — gate=PASS. Two-pane Console-grade memory library + inspector + add/delete; 28/28 tests; null-score rule held. (Combined Console-grade rebuild, parallel build.)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An operator can search, sort, and browse semantic memories with real relevance scores (never fabricated), inspect a memory's detail/metadata, and add/delete   (← memory-console-rebuild)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : state.json task record only (tracking); add.py/templates untouched.
- skill / book : untouched.
- dashboard (product) : /app/memory rebuilt Console-grade — MemoryWorkspace orchestrator + MemoryLibraryPane (role=listbox, recency/relevance sort, score bars) + MemoryInspectorPane (detail/metadata/guarded delete) + MemoryAddComposer + MemoryScoreBar (null-score → "text match"); lib/memories.ts extended with metadata. Pass-through over /v1/memories*.
- gateway (product) : untouched (pass-through).

### Cross-task evidence   (one row per task)
- memory-console-rebuild : gate=PASS · tests=28 green (14 original + 14 new) · full combined suite 897/0 · security review CLEAN · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): the single exit criterion → memory-console-rebuild row.
- goal: "The Memory workspace becomes a Console-grade memory library …" MET — two-pane searchable/sortable semantic library + inspector + add/delete, real-scores-only, 28 green tests + security PASS prove the surface end-to-end.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Ship in the bundled "AI feature depth playgrounds" PR (voice+memory+artifacts+vision+video) → main; Tin reviews + merges.
- [ ] No migration (pass-through) — rides the normal dashboard release.
- [ ] Fold §7 deltas at release time (none material for memory).
