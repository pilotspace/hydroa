════════════════════════════════════════════════════════════════════════
 v56 · Per-tenant model presets
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  3/3 met
 GATES     5 PASS             WAIVERS   none

 goal  A tenant admin defines named presets that remap model names (opus
       to gpt5-5), selects among multiple presets via a name: prefix
       (cheap:opus), and requests resolve the preset to a concrete model
       before the router while the existing fallback seam stays
       byte-identical.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 tenant-preset-store         done      PASS 4†    ●●●●●●●●●
 preset-resolution-ingress   done      PASS 4†    ●●●●●●●●●
 preset-admin-surface        done      PASS 0     ●●●●●●●●●
 preset-capability-validati… done      PASS 2†    ●●●●●●●●●
 chat-modality-guard         done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   tenant-preset-store      PASS Tin Dang <tindang.ht97@gmail.com>
   preset-resolution-ingre… PASS Tin Dang <tindang.ht97@gmail.com>
   preset-admin-surface     PASS Tin Dang <tindang.ht97@gmail.com>
   preset-capability-valid… PASS Tin Dang <tindang.ht97@gmail.com>
   chat-modality-guard      PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (4 carried)
   • ADD · open · a contract's own §1 ⚠ "mirrors X precedent" claim
     needs the SAME precedent checked on BOTH axes (here: frontend nav
     shape AND backend auth strictness) before freeze — this task's
     v1/v2 SCOPE ADDENDUM 1 asserted "mirrors /app/keys exactly" for nav
     visibility while silently carrying a STRICTER backend gate
     (OWNER-only vs. keys' any-role `get_identity`) than the precedent
     it named; only the adversarial refute-read caught the mismatch, not
     the contract-freeze review itself (evidence: refute-read agent
     affb580a9fa5a2545, finding (g1)).
   • TDD · open · a single clean local test run is not sufficient
     evidence of a green suite's determinism when the harness has known
     shared-resource characteristics (one Postgres instance across all
     tests) — this task's build agent's "15/15 green" self-report rested
     on exactly one run; repeating it 6-8x surfaced a ~25-30% failure
     rate the single run entirely missed (evidence: my own independent
     repeated runs, §5 SCOPE ADDENDUM 2).
   • ADD · open · a task whose safety property depends on another
     subsystem's data invariant (here: catalog sync actually populating
     `modality`) should explicitly declare that dependency at GROUND
     time and gate on it, rather than discovering the gap only at
     refute-read (evidence: this task's guard was contract-correct but
     would have caused a full outage in this stale worktree until
     origin/main's prerequisite fix was merged in).
   • ADD · open · running independent adversarial refute-read subagents
     IN PARALLEL with a developer-driven full-suite verification run
     risks the exact "concurrent pytest processes on a shared test DB"
     hazard this same project already hit and partially hardened against
     earlier this session — evidence: the first full-suite run this
     VERIFY pass showed 32 failed/13 errors purely from this collision,
     resolved only by re-running clean. Future auto-mode parallel
     verification should either serialize test-running agents against
     the main-loop's own full-suite run, or explicitly scope subagents
     to read-only static analysis + a SINGLE small targeted test subset,
     never a second independent full/broad pytest invocation against the
     same shared DB.

 SPEC DELTAS    227 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v56
════════════════════════════════════════════════════════════════════════