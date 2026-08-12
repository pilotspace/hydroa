════════════════════════════════════════════════════════════════════════
 v23 · Enterprise UI Overhaul
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  A user navigates an enterprise-grade dashboard — branded
       collapsible sidebar, an at-a-glance Overview home, a light/dark
       theme toggle, and consistently restyled surfaces + auth pages
       matching the shadcn reference — with every existing data seam
       byte-identical.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 design-system-enterprise-e… done      PASS 0     ●●●●●●●●●
 app-shell-sidebar           done      PASS 0     ●●●●●●●●●
 overview-home               done      PASS 0     ●●●●●●●●●
 console-surfaces-redesign   done      PASS 0     ●●●●●●●●●
 admin-surfaces-redesign     done      PASS 0     ●●●●●●●●●
 auth-pages-redesign         done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (9 carried)
   • ADD · open · The §5 scope baseline walks the working tree (excludes
     only .git/.add/__pycache__/node_modules) — a gitignored build
     artifact dir like `apps/dashboard/coverage/` present at the
     tests→build snapshot pollutes the baseline, so a later `--coverage`
     run (or deleting it) trips `scope_violation` (evidence: WARN after
     `vitest run --coverage`; fixed by removing coverage/ +
     re-snapshotting via phase tests→advance). Candidate engine fix: add
     `coverage` to `_SCOPE_EXCLUDE_DIRS`. Run the gate command (`npm
     test` = no coverage) — keep `--coverage` to a one-off off-baseline
     check.
   • TDD · open · For a presentation-only restyle, frozen behavioral
     suites are the regression net; the NEW red→green suite only needs
     to assert the *adoption* — a stable `data-slot` marker is a
     non-brittle, genuinely-discriminating hook (beats asserting CSS
     classes) (evidence: 8 reds landed exactly on missing
     data-slot/ariaLabel/sortable-header/ChartContainer; refute-read
     confirmed non-vacuous).
   • UDD · open · Not every surface fits every block: Keys' interleaved
     governance expand-row is incompatible with TanStack's flat column
     model, so forcing DataTable would have broken the frozen
     expand→prefill flow — adopting where it fits + documenting where it
     doesn't is the honest call (evidence: §1 framing rejected
     force-DataTable-everywhere; Keys stays on composed Card+Table).
   • ADD · open · tsc's incremental `tsconfig.tsbuildinfo` is the SAME
     scope-baseline pollutant as `coverage/` — any `tsc --noEmit`
     between the tests→build snapshot and the gate trips
     `scope_violation` on a gitignored artifact (evidence: WARN after
     `tsc`; fixed by re-snapshotting tests→advance and running ONLY `npm
     test` for the gate). Reinforces the candidate engine fix: extend
     `_SCOPE_EXCLUDE_DIRS`/files to gitignored build artifacts
     (`coverage`, `*.tsbuildinfo`).
   • UDD · open · DataTable can host fully interactive rows (Switch
     toggle, name-button, inline stateful TeamBudgetForm, delete) via
     in-component `columnDef.cell` closures with `enableSorting:false` —
     adoption no longer means "display-only tables"; row-key stability
     keeps in-cell form state across mutations (evidence:
     teams-governance budget-save + 409 row-count(2) stayed green).
   • TDD · open · For interactive-cell restyles the new red→green suite
     needs only the `data-slot` adoption marker + a couple of
     frozen-hook spot-checks per surface; the dense behavioral frozen
     suites (mutations, validation, dialogs, axe) ARE the safety net
     (evidence: refute-read found zero behavioral drift across all 6
     admin suites — the adoption suite asserts only presentation).
   • UDD · open · A shared "shell" component (AuthShell) is the right
     seam for split-screen brand chrome: it OWNS the single `<main>`
     landmark so each page keeps exactly one main + one form, and the
     decorative panel is `aria-hidden` + heading-free + focusable-free
     so it satisfies BOTH jsdom (no CSS engine, so `hidden lg:flex` does
     not hide it) and real-Chromium axe (which skips aria-hidden
     subtrees incl. color-contrast) (evidence:
     test_auth_shell_brand_panel_decorative + jsdom-axe green; the brand
     panel uses a designed bg-primary/text-primary-foreground pair
     regardless).
   • UDD · open · `Button asChild` (Radix Slot) is the canonical way to
     give a real navigation `<a>` button styling without turning it into
     a `<button>`: the SSO link keeps href + role=link + accessible name
     while gaining buttonVariants classes (evidence:
     test_login_form_card_and_styled_sso asserts tagName==="A" + href +
     `inline-flex`).
   • ADD · open · THIRD recurrence of the gitignored-artifact
     scope-baseline papercut (coverage in task 4, tsbuildinfo in task 5,
     tsbuildinfo again here): `tsc --noEmit` between the tests→build
     snapshot and the gate regenerates `tsconfig.tsbuildinfo` →
     `scope_violation`. Workaround is delete-artifact + re-snapshot
     (tests→advance) + run ONLY `npm test` for the gate. Three strikes ⇒
     the engine fix should ship: extend the scope-walk exclusion to
     gitignored build artifacts (`coverage/`, `*.tsbuildinfo`)
     (evidence: WARN `touched outside §5 Scope:
     apps/dashboard/tsconfig.tsbuildinfo`; cleared by re-snapshot, check
     39/0).

 DECIDE NEXT  consolidate learnings + archive-milestone v23
════════════════════════════════════════════════════════════════════════