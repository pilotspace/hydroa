---
type: Spec
title: Quality
lens: quality
project: ai-proxy (Hydroa) — the multi-tenant LLM gateway
generated: { by: add/3.2.0, at: 2026-08-12 }
---
## Now

Red/green TDD is the default for every feature and every fix: the test exists, fails for the
right reason, and only then does code get written. ~4,600 gateway tests across ~60 suites plus
~1,100 dashboard tests. `make ci` is the gate (4-way sharded, ~13 min; `ci` is the single
required check). Pyright strict on `src/gateway`; ruff for lint and format.

## Decisions that bind

- **Never weaken a test or edit a frozen contract to make a build pass.** A security finding
  is always a HARD-STOP.
- **A guard must be RED against the tree that motivated it.** Keep a `<fix>^` worktree and
  require the guard to name the original victim. A guard that is green on the pre-fix commit
  is worse than none — and ceremony is a failure mode too: a validator that cries wolf on the
  correct spelling gets deleted, taking the real signal with it.
- **Assert emptiness deliberately.** A green assertion over an empty result set is invisible
  in a green suite; a date bomb turned a tenant-isolation test vacuous for weeks. Ask what the
  now-empty set was supposed to prove, and probe the live response (0 before, 1 after).
- **Prefer behavioural assertions to structural ones.** Count the statements that reach
  Postgres, not the `GROUP BY`s in the SQL; drive the real code path, not the object's
  existence. A regression arm that asserts `app is not None` passes before the feature exists.
- **A config-shape assertion is vacuous by default.** Scope the parse to the job that would
  actually execute, exclude anything behind an `if:`, strip comments, assert equality against
  a pinned literal — then RUN the attack rather than reasoning about it.
- **A masked gate reports green.** Five shapes seen here: ordered behind a failure ·
  invoked by nothing · permanently red and opt-in · a negative poll predicate · a check that
  never reaches a verdict. Prove the verdict, not the invocation.
- **Reproduce load flakes by delay injection, not by re-running**: wrap the deciding seam with
  a sleep loaded as an external `-p` plugin, and force the less common outcome of a legal race.
- **Read the assertion's direction before converting a sleep.** Positive ("the row appears") →
  poll; NEGATIVE ("no record was written") → keep the sleep, or polling returns on the first
  iteration and the assertion becomes vacuous; mixed ("exactly one") → both.
- **One pytest session at a time on this host.** Worker DB isolation holds within a run, not
  across runs; concurrent sessions manufacture failures that look exactly like isolation
  defects (`pg_type_typname_nsp_index` duplicates, xdist `INTERNALERROR`).
- **Independent adversarial refute-reads are load-bearing, not ceremony** — they have caught
  real defects that green suites missed every time they have been run. Never prompt a refuter
  toward a clean verdict; a refuter that reports an anomaly it cannot explain is worth more
  than one that reports clean.
- **Verify against the gate's own entry point** (`make ci` / `make lint`), never a per-file
  invocation that bypasses the project's exclude list.

## Deltas
<!-- the inbox: `- [open · <date>] <lesson>` — fold upward into the sections above, then retag [folded] -->
