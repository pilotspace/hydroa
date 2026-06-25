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
- [ ] visual-language    depends-on: none             — Establish & FREEZE the elevated design language via the UDD loop (review → research → wireframe → render-capture-confirm). Refresh tokens + restyle shared primitives; Tin confirms a captured reference screen BEFORE build. FREEZES the visual contract.
- [ ] landing-fidelity   depends-on: visual-language  — Apply the language to all (marketing) pages (landing hero/sections, pricing, docs, blog, legal, status) AND the (auth) login + signup screens.
- [ ] admin-fidelity     depends-on: visual-language  — Apply the language to the (app) shell/nav + all 14 admin pages (cards, tables, panels, the four states).

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A captured, Tin-confirmed visual-language reference exists, and the token layer + shared primitives realize it   (← visual-language)
- [ ] Every (marketing) page and the (auth) login/signup screens render in the elevated language with the four-state + a11y bar intact and zero behavior change   (← landing-fidelity)
- [ ] Every (app) page + shell renders in the elevated language with the four-state + a11y bar intact and zero behavior change   (← admin-fidelity)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- dashboard : <token refresh + primitive restyle + per-surface application — filled at close>
- gateway   : <expected untouched — presentation-only; confirm at close>
- tooling / skill / book : <expected untouched>

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit each task on `feat/ui-fidelity` (visual-language → landing-fidelity → admin-fidelity → .add bookkeeping)
- [ ] open PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]])
- [ ] ui-fidelity joins the releasable set; bundle into the next release cut when Tin calls it (release.md)
