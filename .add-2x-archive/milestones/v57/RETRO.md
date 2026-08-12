════════════════════════════════════════════════════════════════════════
 v57 · Batch-discounted chat completions
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  3/3 met
 GATES     5 PASS             WAIVERS   none

 goal  a tenant's eligible chat-completion requests are automatically
       accumulated into durable per-tenant batch jobs (fixed-tick
       windowed grouping, atomic claim/drain, zero-added-latency when
       disabled or unwired) with a read-only admin view of batch
       activity — NARROWED 2026-07-03, see SCOPE CHANGE note below. The
       original goal (real provider-native batch-discount submission +
       billing + live verify) carries forward unchanged as v58's goal.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 batch-job-store             done      PASS 0     ●●●●●●●●●
 batch-dashboard-surface     done      PASS 0     ●●●●●●●●●
 batch-auto-grouping         done      PASS 0     ●●●●●●●●●
 batch-window-grouping       done      PASS 0     ●●●●●●●●●
 batch-claim-drain-del       done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   batch-job-store          PASS Tin Dang <tindang.ht97@gmail.com>
   batch-dashboard-surface  PASS Tin Dang <tindang.ht97@gmail.com>
   batch-auto-grouping      PASS Tin Dang <tindang.ht97@gmail.com>
   batch-window-grouping    PASS Tin Dang <tindang.ht97@gmail.com>
   batch-claim-drain-del    PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (8 carried)
   • UDD · open · the "build-grounding scrub" (re-checking an approved
     mock against the frozen contract and build-time reality immediately
     before implementing, correcting transparently rather than building
     blindly or silently deviating) held 3-for-3 within this single task
     alone (`savings_usd` constant at build time; the `.toggle-row`
     referencing an unbuilt sibling task's control; the hero-sub copy
     asserting a live query that doesn't exist) — worth naming as a
     standing UDD step rather than rediscovering it ad hoc per task
     (evidence: this task, 2026-07-03).
   • ADD · open · re-crossing the tests→build→verify snapshot to clear a
     genuinely-resolved `scope_violation`/`build_tampered` finding also
     erases the ENGINE'S OWN forcing function that would otherwise make
     a human confront a scope excursion at gate time — after
     re-crossing, `add.py check` reads fully clean and the entire burden
     of surfacing the incident shifts onto the AI's own prose
     discipline, with no engine signal left to fall back on if that
     prose under-reports it (evidence: this task's `scope_violation` on
     `worker.py`/`test_batch_jobs.py` vanished from `add.py check`
     immediately after re-crossing, even though the files had only just
     been touched outside declared scope).
   • ADD · open · on a `risk: high`/`autonomy: conservative` task, an
     advisor pass floating a fix as advisory (not a mandate) should be
     recorded as documented residue, NOT executed immediately —
     expanding blast radius at verify to resolve a smell is exactly the
     kind of call conservative autonomy exists to route through the
     human first, even when the fix itself would be mechanically clean
     (evidence: the first relocation attempt was ruff/pyright-clean and
     107/107-tested, yet still had to be reverted for touching
     undeclared scope).
   • ADD · open · `git diff HEAD`/`git checkout HEAD --` is the wrong
     revert target once a task's OWN build has already made legitimate
     changes to a file being reverted for an unrelated reason — HEAD
     predates the whole task, not just the unwanted edit, so a blind
     revert-to-HEAD can silently discard in-scope work alongside the
     out-of-scope part (evidence: reverting `batches/api/router.py` to
     HEAD initially deleted batch-auto-grouping's own already-built
     `dispatch_batch_job` extraction, caught only by a pyright
     `reportAttributeAccessIssue` on `batch_diversion.py`'s import
     immediately after).
   • TDD · open · this codebase's shared-instance test flakiness is not
     limited to the already-known Postgres DB-name contention — a shared
     Redis stream (`usage:events`) / consumer group (`ledger-flusher`)
     shows the same non-deterministic signature (fail → fail → pass
     across identical runs with zero code changes), suggesting the test
     suite lacks isolation for Redis-backed fixtures the same way it now
     guards Postgres DB names (evidence:
     `test_spend_counter_not_incremented_on_cache_hit`, 2026-07-03,
     three consecutive runs).
   • ADD · open · when a task's own §1 SPECIFY weighs multiple framings,
     the milestone's own goal/rationale text (and any direct human quote
     captured there) should be checked as an explicit cross-reference
     BEFORE a framing is chosen — not just re-read for general color.
     Here, MILESTONE.md's goal ("a SET of requests as ONE batch job")
     and Tin's own quoted words ("group user's request as batch")
     directly named multi-request aggregation, but the framing list at
     specify never included that option, and neither of the two items
     flagged to Tin at the §3 freeze covered the gap — so a bundle
     approval was taken on a design axis the human never actually got to
     react to. The fix isn't "ask more questions at freeze," it's
     checking the milestone's own language against each framing BEFORE
     they're written down, so a framing that contradicts the milestone
     goal is either never on the list or is explicitly flagged as
     "diverges from milestone goal — confirm." (evidence: this session,
     2026-07-03 — full sequence in Build-time findings above and in task
     batch-window-grouping's §0 Related intent.)
   • TDD · open · an adversarial reviewer that only reads code can't
     tell whether a test's guard condition (e.g. `abandon_wins_observed
     > 0`) is real or vacuous; temporarily reverting the fix, confirming
     the exact expected RED, then restoring and reconfirming GREEN is a
     stronger standard and should be the default ask for future
     adversarial-review dispatches, not an optional extra (evidence:
     agent `ac5af5b2ac44b01e2`'s report, this task's §6 VERIFY,
     2026-07-03).
   • ADD · open · a Build-expectations row that pre-declares "confirmed
     by `git diff` at the gate" can silently fail when the touched file
     was never committed (still `??` untracked across a whole prior
     milestone's work) — `git diff` shows the entire file as new, not an
     incremental diff. Pre-declared evidence sources should name a
     fallback (direct re-read + independent corroboration) for files
     that may still be uncommitted at verify time (evidence: this task's
     §6 VERIFY Build-expectations row 4, 2026-07-03).

 SPEC DELTAS    233 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v57
              5 planned not yet scaffolded: batch-cache-prefilter ·
              openai-batch-adapter · anthropic-batch-adapter ·
              batch-billing-accuracy · batch-verify
════════════════════════════════════════════════════════════════════════