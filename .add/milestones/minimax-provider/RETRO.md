════════════════════════════════════════════════════════════════════════
 minimax-provider · MiniMax provider integration
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  3/3 met
 GATES     3 PASS             WAIVERS   none

 goal  A client can call the proxy with a MiniMax-hosted model (chat,
       and any other modality MiniMax's OpenAI-compatible API exposes)
       and get a real response back, billed correctly via BYOK
       credentials, live-verified against api.minimax.io/v1.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 minimax-adapter-registry    done      PASS 6†    ●●●●●●●●●
 minimax-catalog-seed        done      PASS 1†    ●●●●●●●●●
 minimax-live-verify         done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   minimax-adapter-registry PASS Tin Dang <tindang.ht97@gmail.com>
   minimax-catalog-seed     PASS Tin Dang <tindang.ht97@gmail.com>
   minimax-live-verify      PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (3 carried)
   • ADD · folded · A contract's blast-radius risk flag (here:
     "`_upsert_model`'s fix changes behavior [folded foundation-version
     41] for EVERY existing provider's re-sync") should trigger running
     the FULL test suite before VERIFY, not just the directly-touched
     test directory — evidence: `tests/catalog/` alone stayed green
     while `tests/catalog_input_modalities/`'s SC5 (a different,
     already-shipped task's frozen no-clobber invariant) was silently
     broken by this task's first-draft `_upsert_model` diff; only a
     full-suite run surfaced it (minimax-catalog-seed TASK.md §5,
     2026-07-01).
   • ADD · folded · a live-verify task's own scope-snapshot can be
     poisoned by an unrelated SIBLING [folded foundation-version 41] git
     worktree's build caches (`.pytest_cache`/`.ruff_cache` under
     `.claude/worktrees/<other>/`), not just caches in the main tree —
     `_scope_walk` doesn't exclude sibling worktree directories
     (evidence: `gate PASS` first returned `scope_violation` listing 21
     `.claude/worktrees/model-preset/...` cache paths, attempt 1 of 3
     burned). Fix was the same documented pattern as
     [[add-scope-snapshot-poisoning]] (re-cross tests→build→verify over
     a quiescent tree) — but ONLY safe once confirmed the sibling
     process was idle (`pgrep` clean) first, since re-snapshotting while
     it's still actively writing would just poison the NEXT gate attempt
     too.
   • TDD · folded · a live-verify task with zero pytest coverage can
     still have its "green" earned or [folded foundation-version 41]
     gamed — the refute-read for this task type should specifically
     check that the harness FAILED LOUDLY at least once on a genuinely
     wrong input (here: the first key's real 401, the DB-race's real
     `ForeignKeyViolationError`) before trusting its final PASS, since a
     script with no prior observed failure gives no evidence it's
     capable of catching a real problem.

 SPEC DELTAS    220 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              minimax-provider
════════════════════════════════════════════════════════════════════════