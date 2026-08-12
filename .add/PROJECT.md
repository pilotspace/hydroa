---
type: Project
title: ai-proxy (Hydroa) — the multi-tenant LLM gateway
goal: a user can set up their tenant → log in → call any LLM model through the proxy → see accurate, billable cost tracking
stage: production
profile: code
generated: { by: add/3.2.0, at: 2026-08-12 }
---
## CARD
goal: a tenant signs up, gets a key, calls any model through one OpenAI-compatible wire, and is billed accurately for it
state: production · 0.14.1 shipped · R7 (evals-regression-gate) in direction · SOC 2 audit within months
next: add new milestone evals-regression-gate

## Where this came from
Migrated from an ADD 2.x bundle on 2026-08-12. The complete 2.x record — 94 archived
milestones, 419 tasks, `state.json`, every `PLAN.md` — is preserved byte-identical in
`.add-2x-archive/` (see its `MIGRATION.md`). 3.x deliberately does not translate 2.x state,
because a 2.x `phase: verify` re-materialising as a 3.0 beat would be a gate that reads
green on its heritage rather than on evidence. Nothing here inherits a stamp it did not earn.

The five lens specs beside this file were re-authored from the archive, not copied: in 2.x
their `## Now` and `## Decisions that bind` sections were never filled in, and everything
had accumulated in the Deltas inbox instead. The carried lessons are now folded up into
those sections, which is what the format always asked for.

The 78 open items from the 2.x todo queue were exported to GitHub issues (label
`add-backlog`), because 3.x derives its worklist from task nodes and has no free-text
capture. Their `[add-todo #N]` titles keep the ids that commit messages and notes cite.
