════════════════════════════════════════════════════════════════════════
 ui-fidelity · UI visual fidelity
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  3/3 met
 GATES     3 PASS             WAIVERS   none

 goal  Every dashboard surface — admin and public — renders at a higher,
       consistent visual fidelity derived from one confirmed elevated
       design language, with no behavior, contract, or data change.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 visual-language             done      PASS 0     ●●●●●●●●●
 landing-fidelity            done      PASS 0     ●●●●●●●●●
 admin-fidelity              done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (10 carried)
   • UDD · open · the engine DTCG validator allows only
     color/dimension/number/fontFamily/fontWeight/duration — composite
     KINDS (box-shadow, cubic-bezier) are realised in globals.css
     (runtime source) + recorded as a token-graph note, not typed in
     tokens.json (evidence: `add.py check` 10 unknown_type FAILs →
     relocated → layer-valid PASS).
   • UDD · open · a token-led refresh propagates the elevated language
     to EVERY surface via the shared primitive kit + `@theme` utilities
     — 508/508 green touching only 4 primitives + globals.css +
     tokens.json, no per-page edits (evidence: full suite green
     pre-application-tasks).
   • ADD · open · in auto mode the human delegated the
     otherwise-human-owned UDD identity choice, yet the
     render-capture-confirm loop still ran (5 capture rounds) as the
     design gate — identity stays auditable in DESIGN.md (evidence: "you
     decide all" + 4 tuning rounds vs captures).
   • TDD · open · for a presentation-only token refresh, the red test
     asserts the token CONTRACT (globals.css/tokens.json strings) + the
     501-suite is the behaviour regression guard — a legitimate
     red→green without a behavioural unit test (evidence:
     visual-language.test red→green; 501 unchanged).
   • UDD · open · a frozen page §3 (structure: one h1, ordered anchors)
     and a visual uplift coexist cleanly — restyle = className +
     aria-hidden decorative layers, asserted by structure-invariant
     tests, so the freeze never blocks the polish (evidence:
     landing-page.test.tsx stayed green through the Aurora hero).
   • TDD · open · rendering the real component + asserting DOM
     (data-slot, gradient class on the h1 span, panel className +
     aria-hidden) is a stronger red→green than reading source strings —
     and a real-app Playwright capture corroborates what jsdom can't
     (true gradient render) (evidence: 4 tests RED→GREEN + landing/auth
     captures).
   • ADD · open · the R3 guard scopes raw-px bans to components/ui only
     — page-level arbitrary CSS (the hero grid/wash) is legitimately
     allowed in app/(marketing); know the guard's scope before
     relocating decorative CSS (evidence: moved the dot-grid OUT of
     auth-shell to dodge R3, kept it in the page).
   • UDD · open · uplifting two shared primitives (StatCard + AppShell)
     propagates one consistent language to all 14 admin surfaces with no
     per-page edit — the cheapest path to the milestone's "consistent
     fidelity" goal (evidence: 514 green touching 2 files; the
     /app/usage capture shows the canvas+nav+card uplift on an untouched
     page).
   • TDD · open · an auth-gated, data-fetching surface is still
     verifiable: component-render tests for the primitives + a
     cookie-seeded real-shell capture (dataless) prove the chrome, with
     live-data KPIs declared as honest browser-only residue (evidence:
     cookie=capture-only rendered the shell; data states = "Request
     failed").
   • ADD · open · the milestone goal (every surface elevated from ONE
     language) is met by the token-graph + 2-primitive strategy, NOT by
     editing N pages — bias future "apply the design" tasks toward the
     shared seam first (evidence: visual-language tokens + 6
     primitive/2-surface edits covered admin+landing+auth).

 SPEC DELTAS    128 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone ui-fidelity
════════════════════════════════════════════════════════════════════════