════════════════════════════════════════════════════════════════════════
 team-member-invite · Team Member Invite
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     2/2 done           CRITERIA  6/6 met
 GATES     2 PASS             WAIVERS   none

 goal  A tenant owner/admin can invite a new colleague by email into
       their own tenant with a chosen role, and that colleague can
       accept the invite and set their own password — without requiring
       SSO/domain-mapping to be configured
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 member-invite-issuance      done      PASS 0     ●●●●●●●●●
 member-invite-acceptance    done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   member-invite-issuance   PASS Tin Dang <tindang.ht97@gmail.com>
   member-invite-acceptance PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (2 carried)
   • TDD · folded · This repo's `[tool.coverage.run]` config lacks
     `concurrency = greenlet`, making [folded foundation-version 48]
     per-line coverage on SQLAlchemy-async modules structurally
     unreliable for judging which branches actually executed (evidence:
     `invite_repository.py`'s core INSERT logic showed "uncovered"
     despite being required for 30 passing tests; corroborated against a
     known-solid pre-existing file showing the same "impossible"
     under-report). Worth fixing repo-wide so future verifies of async
     code can trust coverage numbers directly instead of hand-building a
     probe.
   • ADD · folded · Worktree isolation (`isolation: "worktree"`)
     branches from the last git COMMIT, not [folded foundation-version
     48] the current working tree — incompatible with a task whose §0
     GROUND anchors (or whose milestone's prerequisite work) exist only
     uncommitted. This task's first build attempt silently ran against a
     stale base missing `Role.SUPERADMIN` entirely and shipped an
     incomplete security guard before being caught and discarded
     (evidence: this task's own Verify history). Future dispatches onto
     a substantially-uncommitted tree should default to no isolation +
     strict sequential ordering when shared files are at stake, not
     isolation-for-safety by default.

 SPEC DELTAS    276 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              team-member-invite
              1 planned not yet scaffolded: member-invite-ui
════════════════════════════════════════════════════════════════════════