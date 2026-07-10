════════════════════════════════════════════════════════════════════════
 platform-identity · Platform Identity Foundation
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  5/5 met
 GATES     5 PASS             WAIVERS   none

 goal  A reserved platform tenant and a superadmin role exist and can
       authenticate via a new JWT role plus the existing ops-mTLS
       mechanism, with no new cross-tenant capability granted yet
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 platform-tenant-seed        done      PASS 0     ●●●●●●●●●
 superadmin-role             done      PASS 4†    ●●●●●●●●●
 ops-platform-job-identity   done      PASS 0     ●●●●●●●●●
 superadmin-login            done      PASS 0     ●●●●●●●●●
 superadmin-audit-foundation done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   platform-tenant-seed     PASS Tin Dang <tindang.ht97@gmail.com>
   superadmin-role          PASS Tin Dang <tindang.ht97@gmail.com>
   ops-platform-job-identi… PASS Tin Dang <tindang.ht97@gmail.com>
   superadmin-login         PASS Tin Dang <tindang.ht97@gmail.com>
   superadmin-audit-founda… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (11 carried)
   • TDD · open · a "returns None, never raises" contract on a degrade
     path needs a test that forces the actual failure branch (a real
     precondition-violating environment), not just a happy-path test
     plus a prose promise — the untested branch was silently wrong
     (evidence: `get_platform_tenant` would have raised
     `ProgrammingError`, not returned `None`, on an unmigrated DB until
     the refute-read caught it and
     `test_get_platform_tenant_returns_none_when_unmigrated` was added).
   • ADD · open · when a refute-read finding requires strengthening a
     frozen test file mid-Build, call `add.py heal --reason "..."`
     BEFORE re-running the suite and gating — not after. Fixing the test
     first and going straight to `gate PASS` still trips the mechanical
     tamper tripwire (it hashes bytes, not intent), which force-returns
     the task to build and burns a heal attempt that a proactive `add.py
     heal` call would have consumed deliberately instead (evidence: this
     task burned 1 of 3 attempts this way — recovered cleanly via
     re-crossing `phase build` to re-snapshot, but the proactive path is
     one step shorter and doesn't rely on the mechanical catch).
   • ADD · open · §5's scope-lock snapshot is tree-wide, not per-task:
     running two sibling tasks' Build phases concurrently in a shared,
     non-worktree-isolated tree causes each task's completing verify
     gate to flag the OTHER task's legitimate files as `scope_violation`
     (evidence: sibling `ops-platform-job-identity`'s `gate PASS`
     attempt was flagged for this task's migration + `users_router.py`,
     consumed 1/3 heal attempts). Recovery: pristine tree (clear
     build-artifact caches) then `add.py phase build <slug>`
     (re-snapshots current state) → `advance` → `gate PASS`, per task,
     back-to-back with no other file-touching activity between snapshot
     and gate. Matches the pre-existing `ADD scope-snapshot poisoning`
     memory gotcha — this is fresh, concrete evidence reinforcing it,
     worth folding into the foundation so future parallel-build waves
     plan around it upfront (either serialize the gate step, or accept
     the recovery cost knowingly).
   • TDD · open · a build subagent's self-reported test/coverage numbers
     should be independently reproduced, not just trusted, for any
     security-sensitive build — doing so here directly caught a tooling
     gotcha that would have gone unnoticed otherwise (evidence:
     `alembic.ini` hardcodes `sqlalchemy.url`; the real override env var
     is `GATEWAY_DATABASE_URL` not `DATABASE_URL`; my first two manual
     alembic-check attempts silently no-op'd against the wrong database
     and reported a false failure, caught only because the orchestrator
     re-ran every Build Expectations checkbox independently rather than
     transcribing the build agent's report)
   • ADD · open · this task's own `gate PASS` was the one that actually
     hit the tree-wide §5 scope-lock cross-contamination and consumed
     1/3 heal attempts — full analysis and recovery pattern recorded in
     sibling task `superadmin-role`'s §7 (same milestone, same root
     cause: both tasks' Build phases ran concurrently,
     non-worktree-isolated, in the shared tree) (evidence: this task's
     `gate PASS` failed with `scope_violation` naming files from
     `superadmin-role`'s build, before the sibling)
   • SDD · open · a frozen §2/§3's prose path string
     (`/admin/auth/oidc-config`) drifted from the real mounted route
     (`/admin/oidc`, `oidc_admin_router.py:45`) even though the §0
     GROUND anchor citing the exact file:line was correct throughout —
     the concrete anchor should be treated as more authoritative than a
     restated path string when drafting contract prose, and ideally the
     restated string should be generated FROM the anchor, not typed
     independently (evidence: this task's build agent caught it by
     cross-checking against `rbac_roles/test_rbac_roles.py`'s existing
     real-route usage, not by the contract text alone).
   • TDD · open · two test-construction bugs (an `EmailStr`-invalid TLD;
     a required Pydantic field omitted from a PUT body) both manifested
     as a 422 that would have made the test pass for the WRONG reason
     (validation failure, not the actual 403 gate check) had the
     assertion been looser (e.g. `assert resp.status_code != 200`) —
     writing the exact expected status+code catches this class of bug
     immediately; a looser assertion would have silently certified
     nothing (evidence: §5 "Strategy actually used", both fixes found on
     the first red→green run via exact-code asserts).
   • TDD · open · an `AsyncMock(spec=SomeClass)` with only ONE method
     configured to raise is a silent trap if the code under test doesn't
     actually call that specific method on that specific failure path —
     it looks like a real failure-injection test and passes, but proves
     nothing. Prefer making the FIRST thing the code under test calls
     raise directly (here: the `session_factory()` call itself, matching
     the scenario's own wording) over mocking deep inside an object
     whose exact call pattern you have to keep re-verifying (evidence:
     found by independently re-deriving `record_audit`'s real call
     sequence before trusting the existing suite's pattern — see the
     Spec delta above).
   • SDD · open · a §3 CONTRACT's illustrative Python is not
     automatically valid Python — this task's own Part C snippet had a
     required param placed after already-defaulted ones with no `*`
     separator (a straightforward syntax error), and a false "already
     imported" note for `Role`. Neither was semantic (no Must/Reject
     changed), both were still worth a real syntax/ import sanity pass
     before freezing a contract's code block, not just a shape/decision
     review (evidence: §5 "Strategy actually used" (2)-(3); mirrors
     `superadmin-login`'s own SDD delta about a prose path string
     drifting from its §0 anchor — the same underlying lesson, contract
     prose/code needs the same rigor as contract decisions).
   • TDD · open · when a scenario can only be driven through an
     HTTP/router round-trip (not direct use-case construction), a
     negative assertion like "0 audit rows" is vacuous pre-build for a
     different reason than the AsyncMock trap above: the feature being
     entirely absent ALSO produces 0 rows, so the test can pass for the
     wrong reason at every stage, not just RED. Fix: a POSITIVE CONTROL
     inside the same test — a genuine audit-worthy action in the same
     test/schema that must itself produce a row before the negative
     assertion is trusted. Applied in
     `test_part_b_password_login_audit.py`'s two negative tests
     (evidence: the build agent's own module docstring names this
     explicitly; independently confirmed by the orchestrator reading the
     test file — both tests open with a control block that itself
     asserts count==1 before proceeding to the real scenario).
   • SDD · open · a contract-widening pass (adding Part C
     mid-freeze-cycle) can update one section's rule (§1 Must) for
     consistency across ALL affected parts, while leaving a SIBLING
     section's illustrative text (§2 scenario prose, §3 Python) stale
     for the part NOT being actively rebuilt in that pass — a distinct
     failure mode from deviation (2)-(3)'s single-snippet syntax error
     above: this is cross-section drift within one freeze, invisible
     unless §1 is read against §2/§3 side-by-side for every part, not
     just the part being changed. Found here: §1's Must for Part B
     silently gained an `auth_method` field when Part C was added, but
     §2/§3's own Part-B text did not (evidence: §5 "Strategy actually
     used" (6)). Suggests a freeze-time checklist item: when widening a
     contract for one part, diff-check whether the widening implies a
     change to any OTHER already-drafted part's text too, not just the
     part being added.

 SPEC DELTAS    231 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              platform-identity
════════════════════════════════════════════════════════════════════════