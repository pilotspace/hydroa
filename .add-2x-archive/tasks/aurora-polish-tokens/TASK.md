# TASK: Aurora token + primitive visual uplift (app pages)

slug: aurora-polish-tokens · created: 2026-06-28 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/app/globals.css` — the deployed token realization: `:root` semantic vars (lines ~17–84) + `.dark` (~87–138, scaffolded, not shipped) + the Tailwind v4 `@theme inline` bridge (~155–234). Token groups: surfaces/text (`--background/foreground/card/popover/muted`), brand (`--primary`/`--primary-hover`/`--ring`), neutral interactive (`--accent`/`--secondary`), status (`--success/warning/destructive`), lines/focus (`--border/input/ring`), data-viz (`--chart-1..5`), sidebar (`--sidebar*`), Aurora additions (`--accent-soft`/`--accent-soft-border`/`--brand-from`/`--brand-to`), elevation (`--elevation-sm/md/lg/xl` → bridged `--shadow-*`), modular type scale (`--text-caption..hero` w/ line-height + letter-spacing), motion (`--ease-standard/emphasized`, `--duration-fast/base/slow`).
- `/Users/tindang/workspaces/tind-repo/.add/design/tokens.json` — DTCG source of truth (repo ROOT, outside apps/): `primitive/semantic/component`; `semantic.color.accent.$value == #4F46E5`; `_elevation_note` records the box-shadow/cubic-bezier DTCG dialect deviation. (asserted by tokens.test, not directly read here.)
- High-leverage shared seams (token change → propagates everywhere, NO per-page churn): `components/ui/app-shell.tsx` + `sidebar.tsx` (every route); `card.tsx` + `stat-card.tsx` (KPI tiles); `button.tsx` (every CTA, one cva); `states.tsx` (Empty/Loading/Error); `badge.tsx`; `motion.tsx` (Reveal).

Context (working folder):
- This is the token/primitive FOUNDATION task of v54's "upgrade UI" — a token-led visual polish that the per-page redesign tasks (#4 monitoring · #5 governance · #6 ai-feature) then build on. Same branch `feat/v54-ui-refinement`; sits AFTER responsive-app-shell (the shell layout is settled).
- SAFE uplift levers (no contract lock): elevation/shadow depth, base `--radius`, accent saturation/hue (`--primary`/`--primary-hover`), focus ring (`--ring`), canvas↔card↔muted surface separation, type tracking/line-height, sidebar active-item (`--accent-soft`/`-border`), brand gradient, status hues.

Honors (patterns / conventions):
- **R3 — token-only (HARD blocker):** `tokens.test.ts` walks every `components/ui/*.tsx?` and FAILS on any raw hex `#abc` or bare `'\d+px'`. All polish goes through CSS vars / Tailwind token utilities, never literals.
- **R6 — dep allow-list (CI blocker):** `package.json` deps ⊆ `tests/design-system/allowlist.json` (26 entries). No new dependency without a contract change-request.
- **FROZEN, do NOT break:** `semantic.color.accent == #4F46E5` (tokens.test M1) · Button default has `bg-primary` (components M3) · StatCard value `text-3xl`, label `uppercase tracking-*` (admin-fidelity) · AppShell `<main>` has `bg-muted`(/…) · the landmark contract (skip-link `#main`, ONE `Primary` nav, `main#main`) · `Loading` role=status aria-busy / `ErrorState` role=alert (M4). visual-language.test asserts `--shadow-*`, `--text-hero/display/title/caption`, `--ease-*`, `--duration-*` all exist in globals.css + the `@theme` block.

Anchors the contract cites:
- The specific token vars to be retuned (chosen at §1 after the design-intake), the `@theme` bridge, and the seam primitives that consume them (`card.tsx`/`stat-card.tsx`/`button.tsx`/`sidebar.tsx`/`states.tsx`).
- ⚠ Pending the human design-intake: WHICH levers to turn (and how far) is a taste decision, not derivable from code — asked next.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Hydroa "Classic Blue" luxury rebrand — token-led (accent · gradient · depth)
Framings weighed: re-point the token graph to a new `blue` primitive ramp + soft Apple-luxury elevation, gradient wired into one shared brand surface (chosen) · keep indigo, only add a gradient (rejected — Tin wants the accent itself to be blue) · per-page restyle (rejected — this is the no-churn FOUNDATION; pages come in #4–#6)
Must:
<must>
  - The brand accent becomes Pantone Classic Blue #0F4C81 (the "blue of the year", luxury/trust): `semantic.color.accent` re-points to a new `primitive.color.blue` ramp and `--primary` in globals.css == #0f4c81. Propagates to every `bg-primary` consumer (buttons, active nav, default badge, logo) with NO per-page churn.
  - A deep→bright blue brand gradient: `--brand-from` #0f4c81 → `--brand-to` #2563eb (drops the indigo→violet). Wired into ONE shared, always-present surface (the SidebarBrand logo tile) so it is felt app-wide without per-page edits.
  - Apple-luxury depth: the `--elevation-*` scale becomes soft, diffuse, low-opacity (refined lift, not heavy, not hairline-flat). Smooth radius retained (base 0.5rem). Propagates to every Card/StatCard/Button.
  - Supporting accents move to blue for coherence: `--primary-hover` #155394 · `--ring`/`--sidebar-ring` #2563eb · `--accent-soft` #eaf1f8 · `--accent-soft-border` #cfe0f0 · `--chart-1` #0f4c81. The `.dark` scaffold gets matching blue counterparts.
  - tokens.json (the DTCG source of truth) and globals.css (the realization) stay IN SYNC — the new `primitive.color.blue` ramp + the re-pointed semantic aliases mirror the CSS exactly.
  - WCAG AA preserved: white on #0f4c81 ≈ 8.9:1 (passes); status hues unchanged.
Reject:
<reject>
  - (presentational token change — no runtime inputs) -> failure modes to avoid: breaking a FROZEN class/aria contract (Button bg-primary · StatCard text-3xl/uppercase · main bg-muted · landmarks · Loading/Error roles), adding a raw hex/px to a `components/ui/*` file (R3), or adding a dependency (R6).
</reject>
After:
<after>
  - The dashboard reads as a premium Classic-Blue enterprise product: blue buttons/active-nav/logo, a blue gradient brand mark, soft refined card depth — same Aurora structure, no page-level churn.
  - tokens.test M1 asserts the accent is #0F4C81; the suite is green; the palette preview after-capture matches the confirmed design.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Re-pointing `semantic.color.accent` requires updating the FROZEN M1 assertion (#4F46E5 → #0F4C81) — this is a sanctioned contract change-request (Tin approved the rebrand at the design-confirm), NOT a test weakened to pass a build; the test's INTENT (accent identity is pinned) is preserved at the new value. Cost if mishandled: a silent palette drift. 
  - [ ] The gradient on the SidebarBrand tile reads premium (not noisy) at the small rail size — confirm via the after-capture; easy to dial back to a solid tile if busy.
  - [ ] No `components/ui/*` file needs a raw hex for the gradient (use the `from-brand-from`/`to-brand-to` token utilities) — keeps R3 green. Confirm at verify.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Accent is Classic Blue in the token source
  Given the DTCG token source tokens.json
  When semantic.color.accent is resolved
  Then it resolves to #0F4C81 (via the new primitive.color.blue ramp)
  And the M1 test asserts #0F4C81 (the identity pin moved, not removed)

Scenario: Accent is Classic Blue in the realized CSS
  Given globals.css
  When --primary is read
  Then it equals #0f4c81
  And every bg-primary consumer (button/active-nav/badge) inherits it with no per-page edit

Scenario: Blue brand gradient exists and is wired to a shared surface
  Given globals.css and the SidebarBrand primitive
  When --brand-from / --brand-to are read AND SidebarBrand renders
  Then the gradient is #0f4c81 → #2563eb AND the SidebarBrand logo tile uses the from-brand-from/to-brand-to gradient utilities
  And no raw hex/px is added to any components/ui/* file (R3 stays green)

Scenario: Depth is soft Apple-luxury
  Given globals.css --elevation-md
  When read
  Then it is a soft, diffuse, low-opacity shadow (refined lift)
  And --shadow-sm/md/lg/xl still exist in the @theme block (visual-language test stays green)

Scenario: Frozen contracts preserved
  Given the design-system suite
  When it runs
  Then Button keeps bg-primary, StatCard keeps text-3xl/uppercase, main keeps bg-muted, landmarks + Loading/Error roles hold, deps ⊆ allowlist
  And nothing in those frozen contracts changes
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Token contract (DTCG source ↔ CSS realization ↔ M1 test) — presentational only, NO HTTP/schema.

tokens.json:
  ADD   primitive.color.blue = { brand #0F4C81 · brand-hover #155394 · 50 #EAF1F8 · 100 #CFE0F0 · 500 #3B82F6 · 600 #2563EB · 700 #1D4ED8 }
  REPOINT  semantic.color.accent → {primitive.color.blue.brand}   (was {primitive.color.indigo.600})
           accent-hover→blue.brand-hover · accent-ring/focus-ring→blue.600 · accent-soft→blue.50 ·
           accent-soft-border→blue.100 · brand-from→blue.brand · brand-to→blue.600 · chart-1→blue.brand
globals.css :root (+ matching .dark):
  --primary #0f4c81 · --primary-hover #155394 · --ring #2563eb · --sidebar-ring #2563eb ·
  --accent-soft #eaf1f8 · --accent-soft-border #cfe0f0 · --brand-from #0f4c81 · --brand-to #2563eb · --chart-1 #0f4c81
  --elevation-sm/md/lg/xl → soft diffuse low-opacity (Apple-luxury)
components/ui/sidebar.tsx (SidebarBrand): icon wrapped in a `bg-gradient-to-br from-brand-from to-brand-to` rounded tile, white icon (token utilities only — NO raw hex/px).
tokens.test.ts M1: accent assertion #4F46E5 → #0F4C81.

FROZEN (unchanged): Button bg-primary · StatCard text-3xl/uppercase · main bg-muted · landmarks · Loading/Error roles · deps ⊆ allowlist · --shadow-*/--text-*/--ease-*/--duration-* still present in @theme.
```

Status: FROZEN @ v1 — approved by Tin
<!-- design-confirm via AskUserQuestion + before/after captures (2026-06-28): "Ship it — make it real",
     primary #0F4C81 Classic Blue · gradient #0F4C81→#2563EB · soft Apple-luxury depth · smooth radius.
     CHANGE-REQUEST: the frozen M1 accent pin moves #4F46E5 → #0F4C81 (sanctioned by the approved rebrand;
     the assertion's intent — pin the accent identity — is preserved at the new value, not weakened). -->
Least-sure flag surfaced at freeze: [contract] moving the frozen M1 accent pin (#4F46E5→#0F4C81) — sanctioned by Tin's approved rebrand, intent preserved; [test] the SidebarBrand gradient must use token utilities only or it trips R3 (no raw hex in components/ui).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% lines (dashboard gate) — design-system tests are file-content/contract assertions.
Plan (one test per scenario):
<test_plan>
  - UPDATE tokens.test.ts M1: accent resolves to #0F4C81 (was #4F46E5). (RED: tokens.json still aliases indigo.600.)
  - ADD aurora-classic-blue.test.ts: globals.css `--primary` == #0f4c81; `--brand-from` #0f4c81 + `--brand-to` #2563eb; `--ring` #2563eb; tokens.json has primitive.color.blue.brand == #0F4C81 and semantic.color.accent aliases the blue ramp. (RED.)
  - ADD to that suite: SidebarBrand renders an element with `from-brand-from` AND `to-brand-to` gradient utility classes (the shared gradient surface). (RED.)
  - GUARD (stay green): tokens.test R3 (no raw hex/px in components/ui) + R6 (allowlist) + components.test M3/M4/M5 + admin-fidelity + visual-language (--shadow/--text/--ease/--duration present).
</test_plan>

Tests live in: `tests/design-system/tokens.test.ts` `aurora-classic-blue.test.ts` · MUST run red before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `.add/design/tokens.json` `apps/dashboard/app/globals.css` `apps/dashboard/components/ui/sidebar.tsx` `apps/dashboard/tests/design-system/tokens.test.ts` `apps/dashboard/tests/design-system/aurora-classic-blue.test.ts` `apps/dashboard/tests/design-system/enterprise-ext.test.tsx`
Strategy (ordered batches): 1. update tests RED (M1 #0F4C81 + new aurora suite) 2. tokens.json: add blue ramp + re-point semantic aliases 3. globals.css: apply :root + .dark values + soft elevation; update the file header identity comment 4. sidebar.tsx: gradient tile on SidebarBrand (token utilities only) 5. green suite + tsc + build 6. re-capture palette at 2560 → confirm vs the design-confirm.
Known-problem fixes: R3 (no raw hex in ui/) → gradient via `from-brand-from`/`to-brand-to` utilities, never a literal; keep Button `bg-primary` / StatCard `text-3xl`/`uppercase` / main `bg-muted` untouched; keep `--shadow/--text/--ease/--duration` names in @theme.
Strategy actually used: as planned. tokens.json blue ramp + re-pointed aliases (scripted), globals.css :root+.dark values + soft elevation, SidebarBrand gradient tile (token utilities). One extra frozen pin surfaced during the full-suite run — enterprise-ext.test test_v13_tokens_unchanged also asserted #4F46E5 → re-pinned to #0F4C81 (same sanctioned change-request as M1, intent preserved). Refute-read (frontend-expert, EARNED-GREEN 0.87, NO blockers): WCAG AA verified (white on #0F4C81 = 8.86:1, up from indigo's ~4.6:1) — addressed 3 nits (rootBlock anchored on the :root selector not the comment; no-indigo guard broadened to #4f46e5+#6366f1; chart-1 re-pointed to the chart ramp to remove a dual token path).
Safety rule (feature-specific): tokens.json and globals.css MUST stay in sync; no raw hex/px in any components/ui/* file.
Code lives in: `apps/dashboard/app/globals.css` + `.add/design/tokens.json` + `apps/dashboard/components/ui/sidebar.tsx`
Constraints: do NOT change any test (beyond the §4-declared M1 update + new suite) or the frozen contracts; no new dependency; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] Palette after-capture (2560) matches the confirmed design — `tmp/shellcaps/palette_final.png`: Classic-Blue Deploy/active-nav/badge/logo, blue gradient hero + SidebarBrand tile, soft card depth
- [x] tokens.json ↔ globals.css in sync: accent resolves #0F4C81 both places — M1 + aurora suite green; refute-read confirmed no drift across all 10 re-pointed aliases
- [x] R3 holds: no raw hex/px in any components/ui/* file (gradient via from-brand-from/to-brand-to utilities) — tokens.test R3 green; refute-read confirmed zero hex/px in ui/
- [x] No frozen contract broken: Button bg-primary · StatCard text-3xl/uppercase · main bg-muted · landmarks · Loading/Error roles · deps⊆allowlist — full suite 747 green
- [x] WCAG AA: white on #0F4C81 = 8.86:1 (refute-read computed; UP from indigo's ~4.6:1); primary-hover 7.79:1; active-nav text 7.78:1; ring 4.94–5.17:1 (UI ≥3) — all PASS

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — SidebarBrand gradient tile uses the @theme `--color-brand-from/to` bridge (globals.css:192-193) → `from-brand-from`/`to-brand-to` utilities compile (refute-read confirmed); every re-pointed token consumed by existing bg-primary/ring/brand utilities
- [x] DEAD-CODE — fixed the one orphan the refute-read flagged: semantic.chart-1 re-pointed to the chart ramp (no dual token path); blue ramp fully consumed via the semantic aliases
- [x] SEMANTIC (prose / non-code) — tokens.json DTCG graph read in full: blue ramp added, 10 semantic aliases re-pointed, no indigo alias remains for the brand accent

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: auto-gate (autonomy: auto) on evidence + adversarial refute-read (frontend-expert EARNED-GREEN 0.87, no blockers, 3 nits fixed); design approved by Tin at the design-confirm · date: 2026-06-28
Residue → observe deltas: pre-existing duplicate `prefers-reduced-motion` block in globals.css (not introduced here); chart-2..5 remain the original multi-hue series (intentional data-viz variety).

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose re-point the token graph to a new `blue` primitive ramp + soft Apple-luxury elevation, gradient wired into one shared brand surface; rejected keep indigo, only add a gradient (rejected — Tin wants the accent itself to be blue) · per-page restyle (rejected — this is the no-churn FOUNDATION; pages come in #4–#6)
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: as planned. tokens.json blue ramp + re-pointed aliases (scripted), globals.css :root+.dark values + soft elevation, SidebarBrand gradient tile (token utilities). One extra frozen pin surfaced during the full-suite run — enterprise-ext.test test_v13_tokens_unchanged also asserted #4F46E5 → re-pinned to #0F4C81 (same sanctioned change-request as M1, intent preserved). Refute-read (frontend-expert, EARNED-GREEN 0.87, NO blockers): WCAG AA verified (white on #0F4C81 = 8.86:1, up from indigo's ~4.6:1) — addressed 3 nits (rootBlock anchored on the :root selector not the comment; no-indigo guard broadened to #4f46e5+#6366f1; chart-1 re-pointed to the chart ramp to remove a dual token path).
- [AI] verify — gate PASS (reviewed by auto-gate (autonomy: auto) on evidence + adversarial refute-read (frontend-expert EARNED-GREEN 0.87, no blockers, 3 nits fixed); design approved by Tin at the design-confirm)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
