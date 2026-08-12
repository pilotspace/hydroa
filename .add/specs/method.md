---
type: Spec
title: Method
lens: method
project: ai-proxy (Hydroa) — the multi-tenant LLM gateway
generated: { by: add/3.2.0, at: 2026-08-12 }
---
## Now

ADD 3.2.0 (ABF-1). One atomic task node per change: Direction → Build → Verify, with a single
human approval at the frozen contract and a gate verdict backed by a bound receipt. The human
owns direction and verification; the AI writes the code and reports faithfully.

Around it: `main` is protected — linear history, `ci` required, `enforce_admins: true`, and
since 2026-08-12 `required_approving_review_count: 1`, so every PR needs an approval. Commit
messages go through `tmp/<name>.txt` in `<type>(<scope>): <summary>` form ending
`author: Tin Dang`.

## Decisions that bind

- **Evidence, not assertion.** A claim about behaviour is worth what its receipt is worth.
  Measure before diagnosing, and re-measure after fixing the environment — evidence taken
  while the environment is retargeted is not evidence (two 34-minute runs proved nothing).
- **Fix the environment, then measure.** A foreign container on the test port, a restarted
  Docker daemon, or a second pytest session will each produce a convincing regression that
  does not exist. Assert infra live at both ends of a long run.
- **Commit each task's files as their own commit.** Building several tasks in one working tree
  makes every gate report a scope violation, because each task's anchor sees its siblings' work
  as a leak. Never widen the scope lines to make it pass — that records a lie.
- **A signoff record must say who actually decided.** Never stamp a human's name on a gate an
  agent crossed; name the provenance instead. Fabricated four-eyes is a CC8.1 finding waiting
  to happen, and an automated approval is a one-time call, never standing authority.
- **When a heal lands, sweep for every sibling of the pattern in the same milestone.** The
  recurring failure is not the bug, it is the un-back-applied lesson (ZDR TOCTOU: three
  instances, each documenting why the lock was needed while the fourth site went without).
- **A tool instruction is not a user instruction.** ADD 2.5's own `status` printed "ACTION
  REQUIRED … run the update"; following it replaced the engine with one that cannot read the
  bundle. Verify what a command will do, snapshot first, and check that `--help` is honoured
  (`add update --help` runs the update).
- **CI that has been red for weeks ROTS silently.** Three independent faults had accumulated
  before anyone looked; when CI is down, every merge since also skipped its config review.
  Treat the gates CI stopped enforcing as presumed red.
- **Surface tradeoffs; don't hide confusion.** Interview to ~95% confidence on what is actually
  wanted before building, and prefer a structured question over a guess when two readings lead
  to materially different work.
- **Correct the premise, not just the code.** Two todos in R7 shipped remedies that measurement
  disproved (`ef_search`; "the small tenant is exposed"). Record the correction where the next
  reader will hit it.

## Deltas
<!-- the inbox: `- [open · <date>] <lesson>` — fold upward into the sections above, then retag [folded] -->
