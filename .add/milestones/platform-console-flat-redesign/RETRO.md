════════════════════════════════════════════════════════════════════════
 platform-console-flat-redesign · Platform console flat/borderless visual redesign
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  4/4 met
 GATES     5 PASS             WAIVERS   none

 goal  A superadmin using the platform admin console (tenant directory +
       tenant-detail tabs) experiences a flat, borderless,
       SaaS-professional visual language, grounded in real UI/UX
       research into how superadmins actually scan and act on this
       surface
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 console-flat-visual-pass    done      PASS 0     ●●●●●●●●●
 tenant-overview-strip       done      PASS 0     ●●●●●●●●●
 overview-strip-plan-displa… done      PASS 0     ●●●●●●●●●
 command-palette             done      PASS 0     ●●●●●●●●●
 tenant-activity-tab         done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   console-flat-visual-pass PASS Tin Dang <tindang.ht97@gmail.com>
   tenant-overview-strip    PASS Tin Dang <tindang.ht97@gmail.com>
   overview-strip-plan-dis… PASS Tin Dang <tindang.ht97@gmail.com>
   command-palette          PASS Tin Dang <tindang.ht97@gmail.com>
   tenant-activity-tab      PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (11 carried)
   • ADD · open · the §6 "Build expectations" block is supposed to be
     filled BEFORE dispatching build (per its own guide text), but this
     task's build was dispatched straight off the frozen §2/§3 without
     pausing to transcribe it first — `add.py advance` correctly refused
     the tests->build transition after the fact
     (`build_expectations_unfilled`) and it had to be backfilled once
     code already existed. Mitigated here (content was derived from the
     already-frozen §2 SCENARIOS, not reverse-fitted to the build's
     actual output), but the ORDER the guide prescribes is worth
     actually following next time — fill §6 Build Expectations
     immediately after freeze, before the tests/build dispatch, not
     after (evidence: this task's own `advance` refusal).
   • ADD · open · `_grounded_state`/`_section0_anchors` (add.py's own
     grounded-check) only reads content on the SAME line as "Anchors the
     contract cites:" (regex `Anchors the contract cites:\s*(.*)$`,
     single-line) — but every §0 GROUND section observed across this
     project (including this task's own, and the convention
     `Touches`/`Issues/Risks` fields also follow) puts the real content
     as a bulleted list on the FOLLOWING lines, not after the colon on
     the same line. This makes `_grounded_state` read `False` (looks
     ungrounded) for a fully-grounded §0 written in the project's own
     dominant style, surfacing as a `task_not_grounded` WARN on every
     task that freezes its contract this way — this task's own §0 has 5
     substantive anchor bullets, genuinely grounded, despite the WARN.
     Measure-not-block (never gates), so nothing was blocked, but the
     checker likely under-detects real grounding project-wide (evidence:
     this task's own `add.py check` output + direct regex/content
     inspection).
   • ADD · open · a §2 SCENARIOS tag comment with a SECOND `#` on the
     same line (e.g. `# M3, Issues/Risks #2`) silently breaks
     `_rule_coverage_gaps`' tag parser — `_SCENARIO_TAG_RE` greedily
     matches to the LAST `#` on the line, so the real `M#`/`R:code` tag
     before an earlier `#` gets dropped from the captured group. Caught
     + fixed during this task's own §2 drafting (`add.py check` flagged
     M3 as a coverage gap; M1 had the same collision but was masked by a
     redundant tag elsewhere). Worth a one-line mention in the
     scenarios-writing guide: never put a second `#` on a tagged
     Scenario line (evidence: this task's own pre-fix `add.py check`
     WARN).
   • ADD · open · a design contract's literal value-expression (e.g.
     `plan?.name` vs `.display_name`) can encode a real product-facing
     bug that survives design-draft + orchestrator-review + human-freeze
     undetected when nobody cross-checks the exact field name against
     sibling surfaces at freeze time (evidence: this task's own
     Plan-tile spec-delta above). Suggests a design-phase checklist
     item: when a contract's literal value expression selects one field
     of a multi-field response shape (name vs display_name, id vs slug,
     etc.), explicitly cross-check that field choice against how
     sibling/existing surfaces already render the same shape — not just
     that the types line up.
   • ADD · open · (recurring — same root cause logged in
     `console-flat-visual-pass`) the `task_not_grounded` WARN still
     fires from `_section0_anchors`'s same-line-only regex against this
     task's own multi-line-bulleted §0 GROUND convention, the project's
     own dominant style (evidence: same regex gap first logged in
     `console-flat-visual-pass`'s §7, now recurring here unchanged). Not
     fixed in the engine; logged again purely for visibility/frequency.
   • TDD · open · a `diff`-based self-check inside an adversarial
     mutation test can itself lie — the build agent's own
     mutation-testing `diff` call misreported a still-mutated
     `dashboard-shell.tsx` as "identical" to its pre-mutation backup;
     only a direct `Read`/`grep` of the actual file content caught it
     (evidence: build agent's own self-report, independently reconfirmed
     by the orchestrator re-reading the file as the FIRST verification
     action). Lesson: when adversarially mutating a file to prove a test
     catches a bug, verify the revert with a content read, not a
     diff-tool exit code/summary alone — a diff invocation can itself be
     misconfigured or race a write.
   • ADD · open · pre-filling §5 BUILD Scope completely and accurately
     BEFORE dispatching the build agent (rather than letting the agent
     or a post-hoc pass fill it) fully prevented the
     stale-scope-snapshot false positive that hit both prior tasks this
     milestone (evidence: the `tests`->`build` phase crossing for this
     task produced zero `scope_violation` warning, the first clean
     crossing all milestone). Promote this to the standard sequencing
     for every future full-lane task, not just a reactive fix.
   • UDD · open · a security-critical access-control feature benefits
     from BOTH a static presence/absence test (M1) AND a separate,
     actively-adversarial dynamic test (R2, firing the real trigger and
     asserting no effect) — the static check alone would not have caught
     a fail-open bug where only markup was conditionally hidden but a
     listener was unconditionally attached (evidence: R2's own code
     comment names this exact failure mode; independently confirmed
     sound by the orchestrator reading its implementation).
   • TDD · open · a mutation test can be well-INTENTIONED but
     structurally miss the property it means to prove, if the guarantee
     actually lives in framework wiring (a FastAPI `Depends()` resolving
     before the function body) rather than in-body statement order — the
     build agent caught this itself before mutating, and designed a
     mutation that bypassed BOTH the Depends and the secondary in-memory
     check to cleanly isolate the real property (evidence: the build
     agent's own discovery, independently reasoned-through and accepted
     by the orchestrator during verify). Lesson: before mutating code to
     prove a test catches a bug, confirm the mutation actually removes
     the specific guarantee under test, not just code that looks related
     to it.
   • TDD · open · the repo-wide `asyncio.sleep(0.05)`
     fire-and-forget-drain idiom (used in 4+ test suites now, including
     this task's own new one) has a confirmed load-sensitive failure
     mode under full-suite concurrency (evidence: the one full-suite
     failure this task's own verify pass independently reconfirmed as
     isolation-passing/full-suite-flaky) — worth a dedicated hardening
     follow-up rather than continuing to propagate the same fragile
     idiom into every new audit-write test file.
   • ADD · open · the build-expectations pre-fill gate
     (`build_expectations_unfilled`) rejects even a single BARE `<...>`
     placeholder-style annotation anywhere in the "### Build
     expectations" body, including ones meant as descriptive shorthand
     rather than an unfilled template marker (evidence: this task's own
     tests->build crossing was refused once for exactly this reason,
     fixed by rewording rather than removing content). Lesson: when
     pre-filling Build Expectations before dispatch, avoid bare
     angle-bracket notation entirely in prose — spell it out in words,
     or wrap it in backticks, so the placeholder-detector never has to
     distinguish intent.

 SPEC DELTAS    260 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              platform-console-flat-redesign
════════════════════════════════════════════════════════════════════════