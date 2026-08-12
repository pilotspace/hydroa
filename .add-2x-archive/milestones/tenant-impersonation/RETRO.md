════════════════════════════════════════════════════════════════════════
 tenant-impersonation · Tenant Impersonation
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  4/4 met
 GATES     3 PASS             WAIVERS   none

 goal  A superadmin can act as a specific tenant user in a time-boxed,
       fully audited impersonation session where the real actor stays
       distinguishable in every downstream record
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 impersonation-session-life… done      PASS 0     ●●●●●●●●●
 impersonation-ui            done      PASS 0     ●●●●●●●●●
 impersonation-live-session… done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   impersonation-session-l… PASS Tin Dang <tindang.ht97@gmail.com>
   impersonation-ui         PASS Tin Dang <tindang.ht97@gmail.com>
   impersonation-live-sess… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (4 carried)
   • ADD · folded · coverage.py under-reports statement coverage for
     async router code exercised through [folded foundation-version 48]
     SQLAlchemy's greenlet bridge — 3rd confirmed occurrence this
     session (member-invite-issuance, plan-catalog, now this task: 48%
     router / 39.27% total reported when run in isolation, despite 24/24
     passing tests with real assertions). This time DIRECTLY FALSIFIED
     (not just reasoned about): the add-verify agent instrumented a
     "missing" line with a temporary print statement, confirmed it fires
     during a passing test, then reverted. Worth a repo-wide
     `.coveragerc`/pytest-cov config fix now that it has empirical proof
     behind it, not just pattern-matching across 3 builds.
   • ADD · folded · The "contract-specified-but-unused domain type"
     failure mode recurred (this task's [folded foundation-version 48]
     `ImpersonationSession` plain dataclass, §3 Part D) despite already
     being folded into CONVENTIONS.md from an earlier task (model-mgmt's
     ModelDisabledError/ModelNotFoundError). Worth a sharper trigger at
     Contract-freeze time: ask "which code path actually constructs this
     type?" for every domain entity named in §3, not just error classes
     — the existing lesson's phrasing may be too narrowly scoped to
     catch this class of recurrence.
   • ADD · folded · `add.py advance` state silently lagged actual build
     completion across this [folded foundation-version 48] long-running,
     compacted, parallel-build session: this task's own phase marker was
     still `tests` (not `verify`) despite its code already being fully
     built, tested, and independently investigated by 2 separate verify
     attempts — root cause: the orchestrator built directly against the
     tree in a fast-moving parallel-build sequence and never called the
     2 required `add.py advance` transitions (tests→build, build→verify)
     immediately after the code was done, only discovered via `add.py
     status` at this gate. Lesson: advance state the instant a build
     completes, not deferred until the next check-in — the engine's
     phase marker is the only authoritative source of truth (TASK.md's
     own header is cosmetic) and desyncs silently otherwise, especially
     across compaction boundaries.
   • ADD · folded · A background-suite-dependent `add-verify` dispatch
     can look stalled (a transient API [folded foundation-version 48]
     error, then a long silent gap around a slow full-suite run) while
     actually still being alive and eventually delivering a complete,
     high-quality, more-rigorous-than-the-orchestrator's-own verdict
     (this instance: ~23 min wall-clock, 38 tool_uses, 4 self-built
     forced-race probes, 6 self-built HTTP security probes, one genuine
     new finding). This nuances the pattern logged twice before this
     session (plan-catalog's build agent ×2) that such agents "don't
     truly block" — sometimes they DO eventually deliver, just slower
     than the orchestrator's own patience budget. Lesson: when an
     orchestrator takes over independent verification after presuming a
     stall, treat a later-arriving agent verdict as additional evidence
     to merge, not discard — as done here — rather than assuming the
     takeover was necessarily the only path to a verdict.

 SPEC DELTAS    276 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              tenant-impersonation
════════════════════════════════════════════════════════════════════════