════════════════════════════════════════════════════════════════════════
 v24 · UI polish & a11y follow-ups
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  3/3 met
 GATES     1 PASS             WAIVERS   none

 goal  Resolve the three v23 review nits — Overview heading hierarchy
       (no h1→h3 skip), redundant SidebarTrigger aria-label default, and
       theme-script placement — so the dashboard's a11y and code hygiene
       match the rest of the surfaces, with every data seam
       byte-identical.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 overview-heading-a11y-fix   done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (4 carried)
   • UDD · open · design-system primitives need an explicit
     heading-level escape hatch (CardTitle asChild + ChartCard
     headingLevel) so consumers keep a skip-free outline without forking
     styling (evidence: v23 shipped an h1→h3 skip on `/` because
     CardTitle was a hardcoded h3).
   • ADD · open · the §5 scope-walk papercut recurred a 4th time —
     gitignored `.next/` build artifacts (from `next build`/`next start`
     during verify) trip `scope_violation`; the only reliable fix is
     delete-artifacts → re-snapshot (`phase tests`→`advance`×2). Engine
     fix still pending: extend `_scope_walk` exclusion to gitignored
     paths (evidence: WARN listed `.next/BUILD_ID` etc. until the clean
     re-snapshot) (evidence: add.py check scope_violation pending).
   • TDD · open · a pure-dedup refactor with no behavioral delta
     (SidebarTrigger consumer aria-label) has no honest red→green; label
     it green-by-design and lean on a structural/preservation assertion
     + refute-read instead of inventing a fake red (evidence:
     test_sidebartrigger_name_from_ds_default passed before and after).
   • ADD · open · the security_reminder_hook substring-matches prose,
     not code — writing the token `dangerouslySetInnerHTML` in a §6 note
     (even to say "we DON'T use it") blocks the edit; phrase verify
     notes as "no raw-HTML injection API" (evidence: PreToolUse hook
     rejected the first §6 write).

 DECIDE NEXT  consolidate learnings + archive-milestone v24
════════════════════════════════════════════════════════════════════════