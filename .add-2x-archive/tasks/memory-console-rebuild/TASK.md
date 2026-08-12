# TASK: Console-grade memory library rebuild

slug: memory-console-rebuild · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — built in a worktree-isolated agent (parallel multi-milestone build), reconciled to the
> milestone line. The trust floor held: a FROZEN §3 contract · red tests before build · a recorded §6 gate
> (independent security review = PASS, no blockers).

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `apps/dashboard/components/memory/MemoryWorkspace.tsx` (+ new MemoryLibraryPane/MemoryInspectorPane/MemoryAddComposer/MemoryScoreBar) · `apps/dashboard/lib/memories.ts` (listMemories/createMemory/searchMemories/deleteMemory + MemoryItem) · `apps/dashboard/app/(app)/app/memory/page.tsx`
Context (working folder): `apps/dashboard/tests-bff/memory-workspace.test.tsx` (14 original behaviors) + new `memory-workspace-console.test.tsx`; reuses chat Console language (ConversationTopBar/InspectorPanel) + tokens.
Honors (patterns / conventions): BFF-only `/api/gw/v1/memories*`; four UI states + WCAG 2.2 AA; null-score rule (never fabricate a numeric score); pass-through (no gateway change).
Anchors the contract cites: MemoryWorkspace, MemoryScoreBar, lib/memories.ts, /v1/memories*

---

## 1 · SPECIFY — the rules

Feature: Console-grade memory library — two-pane searchable/sortable semantic memory + detail inspector + add/delete.
Must:
  - Two-pane workspace: left a searchable, recency/relevance-sortable memory library with score bars (where score!=null) + result count; right a detail inspector (full content, metadata, created_at, embedding status) with guarded delete; an add composer.
  - Preserve all 14 original memory behaviors; four UI states; list-load + search failures non-blocking (keep last-good, no crash); one h1 "Memory"; keyboard-navigable list; a11y.
Reject:
  - search result with score === null -> render non-numeric "text match", never a number -> "no_fabricated_score"
  - empty/whitespace content or empty query -> submit/search disabled, no request -> "guarded_empty_input"
Accept: Given two memories, When the operator searches and selects a result, Then the ranked list (real scores only) + the inspector detail render, and add/delete mutate the list — all over /api/gw/v1/memories*, no fabricated score.
Assumptions: none material — biggest risk: the null-score rule regressing; pinned by search_null_score_never_shows_fabricated_number.

---

## 3 · CONTRACT — freeze the shape

```
MemoryWorkspace (components/memory/) two-pane:
  MemoryLibraryPane    -> role=listbox; search (POST /v1/memories/search {query,top_k?}); recency|relevance sort; MemoryScoreBar(score) -> role=progressbar when score!=null ELSE "text match"
  MemoryInspectorPane  -> selected {content, metadata(JSON, escaped), created_at, has_embedding}; guarded delete (DELETE /v1/memories/{id})
  MemoryAddComposer    -> POST /v1/memories {content, metadata?}; disabled on blank/whitespace
  list: GET /v1/memories?limit&offset (newest-first); load failure non-blocking
Pass-through over /api/gw/v1/memories* — no gateway change.
```

`Least-sure flag surfaced at freeze:` [contract] the null-score affordance must never coerce null→0 — pinned by a dedicated test; if wrong, the UI fabricates relevance (trust harm).
Status: FROZEN @ v1 — approved by Tin Dang (project-lead autonomous approval under the standing "ship all playground features" goal; reuses approved Console language)

---

## 4 · TESTS — failing-first (red)

Plan: 14 new Console-grade tests in `apps/dashboard/tests-bff/memory-workspace-console.test.tsx` (search ranked + null-score, sort toggle, inspector detail, guarded delete, non-blocking load failure, disabled-empty) + the 14 original behaviors preserved. Ran red before build, green after.
Tests live in: `apps/dashboard/tests-bff/memory-workspace-console.test.tsx`

---

## 5 · BUILD — AI writes code

Scope (may touch): `apps/dashboard/components/memory/` `apps/dashboard/lib/memories.ts` `apps/dashboard/app/(app)/app/memory/page.tsx` `apps/dashboard/tests-bff/memory-workspace-console.test.tsx`
Strategy & known-problem fixes: red tests → MemoryLibraryPane/InspectorPane/AddComposer/ScoreBar → MemoryWorkspace orchestrator → green; trap: null-score→0 coercion (dodged via explicit short-circuit in MemoryScoreBar).
Strategy actually used: as planned (worktree-isolated agent).
Code lives in: `apps/dashboard/components/memory/`   ·   Constraints: change no test, no contract; no new deps.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build (28/28 memory; full suite 897/0)
- [x] green was EARNED — agent self-verify + independent security review (memory surface CLEAN: content/metadata escaped React children, null-score short-circuit confirmed, original assertions intact)
- [x] no exposed secrets, injection openings, or unexpected dependencies (security review = PASS; BFF-only)

Build expectations (from §1 Accept + §3 CONTRACT): two-pane library + inspector render, ranked search shows real scores only ("text match" on null), add/delete mutate the list — confirmed by memory-workspace-console.test.tsx + the combined-tree suite 897/0.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (orchestrator-driven; independent security review PASS, no blockers) · date: 2026-06-30
<!-- OBSERVE: [SPEC · open] none material; surface complete. -->
