# MILESTONE: UI visual fidelity

goal: Every dashboard surface — admin and public — renders at a higher, consistent visual fidelity derived from one confirmed elevated design language, with no behavior, contract, or data change.
rationale: intake=new-major — a "visual fidelity / craft" theme no active milestone's goal covers. EXTENDS the v13 token foundation + v23/v24 enterprise-UI + v38 marketing-site arc by raising their finish (type, color, depth, motion, rhythm) rather than adding features. DEPENDS-ON nothing (consumes existing pages unchanged); OVERLAPS none of the in-flight v40 chat-workspace work (separate worktree/branch). Built in an isolated worktree off main (feat/ui-fidelity).
stage: production · status: active · created: 2026-06-25

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - One elevated, CONFIRMED design language (UDD): refreshed token layer (type scale,
    accent/palette, spacing rhythm, elevation/shadow, radius, motion) + restyled shared
    UI primitives (button, card, badge, stat-card, states, sidebar/shell, data-table).
  - Apply it to ALL (marketing) public pages: landing, pricing, docs, blog, legal, status.
  - Apply it to the (auth) login + signup screens (they bridge marketing → app — Tin
    included them 2026-06-25; carried by `landing-fidelity`).
  - Apply it to ALL (app) admin pages + shell: overview, keys, usage, spend, models,
    routing, health, slo, alerts, audit, members, teams, settings, chat.
Out:
  - Any behavior / data / API / contract change — presentation only; existing logic and
    tests stay green (the v23/v24 presentation-only restyle recipe).
  - New product features, new pages, or copy rewrites beyond visual placeholder polish.
  - Dark mode as a deliverable — light is the only shipped mode; the scaffolded `.dark`
    token block is kept COHERENT with the refresh but is not a milestone deliverable
    (Tin decided light-only 2026-06-25).
  - Net-new illustration / 3D / photography pipeline — token + component craft only.

## Shared decisions & glossary deltas   (living — every task must honor these)
- PRESENTATION-ONLY: no task changes behavior, routes, data, or contracts; every
  existing unit / integration / a11y test stays green by construction (logic
  byte-identical). A real behavior change is a change-request back to Specify.
- IDENTITY IS HUMAN-OWNED (UDD): brand accent, palette, and typeface are surfaced for
  Tin to confirm at the `visual-language` design loop — never auto-picked.
- TOKEN LAYER STAYS THE SINGLE SOURCE: values live in `app/globals.css` ↔
  `.add/design/tokens.json`; every component consumes token-named utilities only
  (bg-primary, text-foreground, …), never raw hex/px.
- DARK STAYS COHERENT: every token added/changed for light gets its `.dark` counterpart
  so the scaffolded dark block never drifts — but dark is not verified/shipped this
  milestone (light-only deliverable).
- A11Y BAR PRESERVED: WCAG 2.2 AA — axe serious|critical in jsdom (color-contrast rule
  disabled, no canvas); true contrast + visual breakpoints remain the NAMED browser-only
  residue per v13. The four UI states (loading · empty · error · success) survive every
  restyle.

## Shared / risky contracts (freeze these first)
- The elevated token set + restyled primitive kit + ONE captured reference screen
  (the UDD design-confirm) -> owning task `visual-language`. Every other task consumes
  it; freeze before any surface is touched.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] visual-language    depends-on: none             — Establish & FREEZE the elevated design language via the UDD loop (review → research → wireframe → render-capture-confirm). Refresh tokens + restyle shared primitives; Tin confirms a captured reference screen BEFORE build. FREEZES the visual contract. — gate PASS (commit 554eb4b)
- [x] landing-fidelity   depends-on: visual-language  — Apply the language to all (marketing) pages (landing hero/sections, pricing, docs, blog, legal, status) AND the (auth) login + signup screens. — gate PASS (commit 1fff422)
- [x] admin-fidelity     depends-on: visual-language  — Apply the language to the (app) shell/nav + all 14 admin pages (cards, tables, panels, the four states). — gate PASS

## Exit criteria (observable; map each to the task that delivers it)
- [x] A captured, Tin-confirmed visual-language reference exists, and the token layer + shared primitives realize it   (← visual-language)   (verify: apps/dashboard/tests/design-system/visual-language.test.ts)
- [x] Every (marketing) page and the (auth) login/signup screens render in the elevated language with the four-state + a11y bar intact and zero behavior change   (← landing-fidelity)   (verify: apps/dashboard/tests/design-system/landing-fidelity.test.tsx)
- [x] Every (app) page + shell renders in the elevated language with the four-state + a11y bar intact and zero behavior change   (← admin-fidelity)   (verify: apps/dashboard/tests/design-system/admin-fidelity.test.tsx)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- dashboard : Aurora token refresh (tokens.json + globals.css: elevation/type/motion/radius + brand-gradient/soft-accent utilities) + restyled shared primitives (card·button·badge·sidebar·stat-card·app-shell·feature-card·auth-shell) + bespoke landing hero/CTA composition. 8 new design-system tests (501→514 suite).
- gateway   : UNTOUCHED — presentation-only; no gateway file in any diff (confirmed: all edits under apps/dashboard + .add).
- tooling / skill / book : UNTOUCHED — only .add/{tasks,milestones,design,state.json} bookkeeping written by the engine/loop.

### Cross-task evidence   (one row per task)
- visual-language  : gate=PASS · tests=508 green (+7) · residue=none (DTCG shadow/easing realised in globals.css, recorded as _elevation_note) · commit 554eb4b
- landing-fidelity : gate=PASS · tests=512 green (+4) · residue=none · real-app captures landing-fidelity.png + auth-fidelity.png · commit 1fff422
- admin-fidelity   : gate=PASS · tests=514 green (+2) · residue=live-KPI data is browser-only (needs authed gateway) · real-app capture admin-fidelity.png

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): EC1←visual-language row (captured reference + token/primitive realisation); EC2←landing-fidelity row (marketing+auth captures, four-state/a11y suites green); EC3←admin-fidelity row (app shell capture, shared-primitive uplift to all 14 pages, console-surfaces/shell suites green)
- goal: Every dashboard surface — admin and public — renders at a higher, consistent visual fidelity from ONE confirmed Aurora language with no behavior/contract/data change — proven by 514/514 vitest green (0 behavioural test touched) + tsc clean + next build exit 0 + three real-app captures, achieved by editing the token graph + shared primitives only (no per-page rewrites).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit each task on `feat/ui-fidelity` (visual-language → landing-fidelity → admin-fidelity → .add bookkeeping)
- [ ] open PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]])
- [ ] ui-fidelity joins the releasable set; bundle into the next release cut when Tin calls it (release.md)
