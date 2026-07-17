════════════════════════════════════════════════════════════════════════
 commercial-self-serve · Commercial Self Serve
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  4/4 met
 GATES     5 PASS             WAIVERS   none

 goal  A tenant can activate and transact with Hydroa entirely
       self-serve — the signup→first-call→invite→agent-approval→upgrade
       journey completes with zero platform-operator intervention.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 activation-quickstart       done      PASS 0     ●●●●●●●●●
 transactional-email         done      PASS 9†    ●●●●●●●●●
 device-activate-page        done      PASS 0     ●●●●●●●●●
 self-serve-checkout         done      PASS 0     ●●●●●●●●●
 self-serve-plans-catalog    done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   activation-quickstart    PASS Tin Dang <tindang.ht97@gmail.com>
   transactional-email      PASS Tin Dang <tindang.ht97@gmail.com>
   device-activate-page     PASS Tin Dang <tindang.ht97@gmail.com>
   self-serve-checkout      PASS Tin Dang <tindang.ht97@gmail.com>
   self-serve-plans-catalog PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (3 carried)
   • TDD · open · a RED suite that asserts the EXACT non-retry behavior
     (not just "eventually succeeds") caught a real stdlib
     exception-hierarchy trap (SMTPException IS-A OSError since Python
     3.4) that a shape-only test would have missed entirely (evidence:
     R4 test failed with 3 attempts against the contract's literal
     retry-predicate tuple).
   • ADD · open · a later task's frozen contract legitimately extending
     an EARLIER task's response shape requires updating that earlier
     task's own exact-shape test (in-scope per this task's §5 Scope
     line) rather than treating it as an untouchable frozen artifact
     forever — the update is additive-only (one new key) and
     superseded-not-silent (evidence: tests/member_invite_issuance
     test_owner_invites_co_owner comment cites both task IDs).
   • ADD · open · a fixed `asyncio.sleep(0.05)` after a fire-and-forget
     dispatch flakes under a load-shared multi-agent host even for a
     BRAND NEW test — poll-until-present from the first draft, not just
     as a post-hoc fix (evidence: [[fire-and-forget-audit-test-flake]]
     recurred in this task's own first draft before being fixed with
     `_poll_until`).

 SPEC DELTAS    278 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              commercial-self-serve
════════════════════════════════════════════════════════════════════════