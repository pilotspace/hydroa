════════════════════════════════════════════════════════════════════════
 catalog-pricing-detail · Catalog pricing detail (OpenAI-compatible per-1M cost fields)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  2/2 met
 GATES     1 PASS             WAIVERS   none

 goal  Both GET /v1/models (client) and GET /admin/catalog/models
       (admin) expose full OpenAI-compatible per-token cost detail
       (input/output/cache) normalized to a familiar per-1M-token
       display, using MiniMax's real cached_tokens usage as the driving
       example
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 catalog-pricing-fields      done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   catalog-pricing-fields   PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 2/2 met

 LEARNINGS (2 carried)
   • ADD · open · a post-freeze correction to a red test or the frozen
     §3 (even a legitimate arithmetic fix, not a weakening) must be
     self-disclosed and re-crossed (`add.py phase tests` → `advance`)
     THE MOMENT it happens, not left for the refute-read gate to catch
     as `build_tampered` — the fix here was correct on the merits, but
     the process gap (undisclosed post-freeze edit) was a genuine
     near-miss on this project's own HARD-STOP tripwire (evidence:
     refute-read agent a7dcf49edf578dec0 independently reproduced the
     md5/mtime mismatch this session; had it not caught it, the task
     would have gated PASS on an unrecrossed tamper flag)
   • ADD · open · the sibling-worktree scope-snapshot-poisoning variant
     (documented once already this session for `minimax-live-verify`)
     recurred here with a materially different signature: instead of
     stale `.pytest_cache`/`.ruff_cache` build artifacts, it was the
     sibling task's own actively-edited SOURCE files
     (`error_catalog.py`, `main.py`, `tenant_model_preset_store.py`) —
     confirming `_scope_walk`'s repo-wide walk (`root.parent.resolve()`)
     has no `.claude/worktrees/` exclusion at all, not just a
     cache-directory gap; the safe remedy (confirm sibling idle via
     `pgrep`, then re-cross) held again, but a permanent engine-level
     fix (exclude `.claude/worktrees/` from `_scope_walk` entirely)
     would remove the need to poll for sibling idleness on every future
     concurrent-worktree task (evidence: `add.py check` flagged 3
     sibling src files as `scope_violation` this session, cleared only
     after the sibling process went idle)

 SPEC DELTAS    220 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              catalog-pricing-detail
════════════════════════════════════════════════════════════════════════