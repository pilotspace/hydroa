════════════════════════════════════════════════════════════════════════
 v13 · UI/UX refresh — highest-value dashboard journeys (usage/cost + key/budget governance)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  6/6 met
 GATES     4 PASS             WAIVERS   none

 goal  a tenant owner/developer experiences the usage/cost dashboard and
       the key & budget governance journeys as a polished, consistent,
       accessible, and responsive product — unified design system,
       clearer task flows, WCAG 2.2 AA, and tablet/mobile breakpoints —
       with NO change to the underlying data, BFF, or gateway contracts

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 design-system-foundation    done      PASS 0     ●●●●●●●●●
 usage-cost-ui               done      PASS 0     ●●●●●●●●●
 key-budget-governance-ui    done      PASS 0     ●●●●●●●●●
 ui-ux-verify                done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (10 carried)
   • UDD · open · ADD 1.3.0's UDD token layer fits a real design-system
     task cleanly: the 3-layer fail-closed citation + `add.py check`
     caught zero issues once authored from the sample JSON; the
     named-set (tokens+catalog+prototype) is a good freeze-first
     contract shape (evidence: design set lints clean, all 13 scenarios
     green).
   • TDD · open · `--no-coverage` test runs HID a real coverage
     regression (78.14% < 80%) that only `vitest run --coverage`
     surfaced; the adversarial earned-green subagent caught it. Lesson:
     run the COVERAGE gate (not just `--no-coverage`) before claiming
     "coverage held," and the earned-green refute-read earns its keep
     (evidence: first gate would have shipped a failing CI coverage
     gate).
   • ADD · open · the §5 scope-lock flags transient BUILD ARTIFACTS
     (`.next/`, `coverage/`) as scope violations because they are not in
     the engine's `_SCOPE_EXCLUDE_DIRS`; a frontend task must either
     declare them in §5 Scope or clean them before the gate. Candidate
     engine improvement: add `.next`/`coverage` to the exclude set
     (evidence: gate self-heal attempt 1 tripped on coverage/lcov-report
     html).
   • UDD · open · the frozen contract assumed Radix Dialog exposes
     `aria-modal="true"`; this Radix version signals modality via
     aria-labelledby + focus-guards instead. Asserting the substantive
     guarantees (labelled + focus-trap) is more faithful than the
     version-specific attribute (evidence: probe showed no aria-modal,
     focus moved into the dialog).
   • SDD · open · axe-core color-contrast cannot run under jsdom (canvas
     getContext not implemented) — structural a11y is covered in vitest,
     but real contrast must be verified by browser-axe in the
     ui-ux-verify task (evidence: jsdom canvas error, contrast deferred
     not asserted).
   • TDD · open · a VERIFY-ONLY task can be legitimately
     green-on-first-run (no product code to write); the honest red-first
     is file-absence, and integrity comes from a DISCRIMINATING MUTATION
     check — inject a known-critical violation (img-no-alt) through the
     SAME helper and confirm it's caught — not from manufacturing a red
     (evidence: img-no-alt → `image-alt` caught, then deleted; the 12/12
     green is earned, not vacuous).
   • TDD · open · axe in jsdom must filter on `impact ∈
     {serious,critical}` rather than `toHaveNoViolations()` — the latter
     fails on MODERATE best-practice rules (region/landmark) that fire
     when a component is scanned in isolation, masking the real gate;
     color-contrast must be rule-disabled (no canvas) (evidence:
     `axeSeriousCritical` filters impact + disables color-contrast;
     isolated-state scans pass cleanly).
   • UDD · open · the 4 state patterns + responsive intent are
     jsdom-verifiable only as PRESENCE proxies (role=status/alert,
     Empty, `sm:`/`lg:` classes); true contrast + visual breakpoints are
     browser residue — name the residue under an `unverifiable_claim`
     reject rather than faking a green (evidence: criterion #4/#6 split
     into a jsdom-proven half + a declared browser-residue half).
   • ADD · open · strengthening tests mid-build (after an adversarial
     review finds coverage gaps) requires going BACK to the tests phase
     and RE-CROSSING tests→build to re-snapshot the tripwire — editing
     tests while in build trips `build_tampered` (evidence: phase tests
     → add Shift+Tab + isolated axe scans → advance re-snapshot → gate
     clean).
   • ADD · open · the adversarial earned-green refute-read pays off
     AGAIN on a verify task: it returned EARNED-WITH-GAPS and surfaced 3
     real coverage gaps (Shift+Tab wrap untested on both dialogs,
     isolated state renders un-scanned) that the green would otherwise
     have hidden (evidence: all 3 closed this loop, focus-trap branch
     coverage rose 73.91%→75%).

 DECIDE NEXT  consolidate learnings + archive-milestone v13
════════════════════════════════════════════════════════════════════════