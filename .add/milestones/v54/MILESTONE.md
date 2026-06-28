# MILESTONE: UI refinement — polished, responsive, scalable app pages

goal: Every authenticated dashboard page meets a refreshed UI standard: visually polished, structurally consistent, responsive across screen sizes, and usable at scale.
rationale: new-major — a fresh UI-refinement theme no active milestone's goal covers. EXTENDS the closed `ui-fidelity` (Aurora visual language, foundation v37) and `v50` (landing & admin UI hardening) UI line by carrying the token-led uplift into a full per-page redesign + a responsive pass + a catalog-scale feature. Grounding — **Touches**: `apps/dashboard/` (AppShell, StatCard, `states.tsx`, `tokens.json`, `globals.css`, `app/(app)/*` page routes, `components/*`). **Context**: todos #1–#3, the v54 admin captures (`tmp/captures/`), PROJECT.md §Users(UDD), foundation-v37 ui-fidelity lessons. **Honors**: PROJECT.md UDD invariants (3-layer DTCG tokens fail-closed; byte-identical data seams; four UI states; WCAG 2.2 AA; design-before-code) + CLAUDE.md (red/green TDD; design-for-failure). **Anchors**: existing `AppShell`/`StatCard`/`states.tsx` primitives, the `tokens.json` DTCG set, the BFF query hooks (`use-catalog-models`, usage/spend/slo hooks).
stage: production · status: active · created: 2026-06-28T06:55:05+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  The ~14 authenticated `/app/*` pages (usage · spend · slo · health · keys · members · routing · alerts · audit · settings · models · chat · voice · memory · artifacts · vision · video). The shared `AppShell` + primitive kit + design tokens. A full-height, responsive sidebar (todo #1). Pagination + fuzzy search on the Models catalog (todo #2). All four upgrade dimensions: visual polish (token-led) · per-page layout redesign · responsive/mobile pass · states+consistency audit (todo #3).
Out: Marketing/public surfaces (landing · pricing · docs · legal · status) and auth pages (login · signup) — UI line is a later milestone. Any backend/API/contract change — this is UI-only; every data seam (BFF route · hook · field names) stays byte-identical. New product features beyond catalog paging+search. Real-browser color-contrast + true-viewport a11y verification (the STANDING Playwright residue from v13/v15 — declared once, not re-litigated per task).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Byte-identical data seams** — same BFF routes, query hooks, and field names; the behavioral test floor stays green through every restyle (foundation-v37/v13 lesson: presentation refactors keep the seam byte-identical).
- **Token-led, no per-page churn for shared concerns** — visual values land as DTCG tokens consumed via the shared primitive kit + `@theme` utilities; no surface hardcodes a value a token covers (`add.py check` lints the 3-layer set fail-closed).
- **Four UI states on every page** — loading · empty · error · success, composed from `states.tsx` primitives (reuse, don't re-derive).
- **Responsive asserted as presence-proxies in jsdom** — `sm:`/`lg:` breakpoint classes, not fixed px; the real-viewport check defers to the standing Playwright residue.
- **a11y by construction** — decorative icons carry `aria-hidden`; no accessible-name is a superstring of another; structure-invariant tests (one h1, ordered anchors) keep a frozen page §3 coexisting with the visual uplift.
- **TDD red/green** — each task ships a red test first; UI assertions are structural + state-presence so the freeze never blocks the polish.

## Shared / risky contracts (freeze these first)
- **Refreshed shared-primitive kit + tokens** (PageHeader pattern · StatCard · table affordances · the four-state usage · elevation/spacing tokens) -> owning task `aurora-polish-tokens`. The three per-page redesign tasks CONSUME this frozen kit — freeze it before they build.
- **Responsive AppShell layout contract** (full-height sidebar · breakpoint behavior · mobile nav · existing nav structure) -> owning task `responsive-app-shell`. Single owner of `AppShell` so todo #1 and the responsive pass never collide.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] model-catalog-paging-search   depends-on: none                                   — Paginate the Models catalog + fuzzy-search filter over hundreds of rows (todo #2). Independent file scope (Models page + a search/paging primitive) → own worktree, runs FIRST.
- [ ] responsive-app-shell          depends-on: none                                   — Full-height sidebar (todo #1) + responsive shell across mobile→desktop breakpoints (drawer/collapse, no rail-hidden-without-fallback regressions). Sole owner of `AppShell`.
- [ ] aurora-polish-tokens          depends-on: none                                   — Token + shared-primitive visual uplift (PageHeader, StatCard, table affordances, spacing/elevation) propagating to all app pages with no per-page edits. Establishes the four-state baseline. Same worktree as responsive-app-shell (both touch shared shell/primitives).
- [ ] monitoring-pages-redesign     depends-on: aurora-polish-tokens, responsive-app-shell — Redesign usage · spend · slo · health to the refreshed layout standard; all four UI states present.
- [ ] governance-pages-redesign     depends-on: aurora-polish-tokens, responsive-app-shell — Redesign keys · members · routing · alerts · audit · settings to the refreshed layout standard; all four UI states present.
- [ ] ai-feature-pages-redesign     depends-on: aurora-polish-tokens, responsive-app-shell — Redesign chat · voice · memory · artifacts · vision · video to the refreshed layout standard; all four UI states present.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] User can page through the Models catalog and narrow it with a fuzzy search box (no single unpaged hundreds-row list)   (← model-catalog-paging-search)
- [ ] The sidebar spans the full viewport height and the shell stays usable from mobile to desktop widths (nav reachable at every breakpoint)   (← responsive-app-shell)
- [ ] Every `/app/*` page renders the refreshed Aurora visual language through the shared primitives — no surface hardcodes a token-covered value (`add.py check` clean)   (← aurora-polish-tokens)
- [ ] The monitoring pages (usage · spend · slo · health) show the redesigned layout and each handles loading/empty/error/success   (← monitoring-pages-redesign)
- [ ] The governance pages (keys · members · routing · alerts · audit · settings) show the redesigned layout and each handles loading/empty/error/success   (← governance-pages-redesign)
- [ ] The AI-feature pages (chat · voice · memory · artifacts · vision · video) show the redesigned layout and each handles loading/empty/error/success   (← ai-feature-pages-redesign)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
