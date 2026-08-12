════════════════════════════════════════════════════════════════════════
 dashboard-hallmark-restyle · Whole-dashboard restyle to the Airier enterprise AI-SaaS theme
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  4/4 met
 GATES     1 PASS             WAIVERS   none

 goal  Every dashboard surface (all 45 routes, public + authed, light +
       dark) reads as a polished, professional enterprise AI-SaaS
       console — the Tin-locked "Airier" direction — with real
       design-system typography and a WCAG-AA floor, driven from the
       shared token layer rather than per-page edits.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 airier-theme-restyle        done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   airier-theme-restyle     PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (3 carried)
   • UDD · open · a green `next build` proves compilation, NOT that a
     font/theme applies — verify computed style on a LIVE render
     (evidence: Geist fell back to ui-sans-serif while the build was
     green; caught only by a playwright probe).
   • UDD · open · an accent hue that works as a solid FILL can fail AA
     as TEXT on its own soft tint — give accent-as-text its own AA-safe
     token (evidence: #2f6df0 on #eef3fe = 4.14:1, failed on ~30 routes
     via the shared active-nav).
   • ADD · open · Tailwind v4 `@theme inline` output is UNLAYERED and
     beats `@layer base :root`; a same-name self-reference collapses to
     empty (evidence: --font-sans: var(--font-sans) dropped Geist —
     [[tailwind-v4-font-token-collision]]).

 SPEC DELTAS    278 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              dashboard-hallmark-restyle
════════════════════════════════════════════════════════════════════════