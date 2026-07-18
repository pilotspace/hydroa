# TASK: Airier theme restyle — fonts, token palette (light+dark), raw-color sweep, AA contrast

slug: airier-theme-restyle · created: 2026-07-17 · stage: production
milestone: dashboard-hallmark-restyle
autonomy: auto
phase: done
sensitivity: architecture

> Presentation-only restyle. The design-definition (UDD) loop ran BEFORE build: 3 captured-screen
> artifacts (v1 Graphite Instrument → Tin "lighter/airier" → v2 → v3 + Geist), Tin LOCKED v3 (ba3c7dd4).

---

## 0 · GROUND — the real codebase

Touches (files · symbols):
- `apps/dashboard/app/layout.tsx:RootLayout` — Geist + Geist_Mono via next/font/google; body className.
- `apps/dashboard/app/globals.css` — the v13 token system: `@layer base :root` + `.dark` custom properties,
  the `@theme inline` Tailwind-v4 bridge (token → utility), `body` base rule.
- `apps/dashboard/components/ui/sidebar.tsx:SidebarItem` — active-nav treatment (token utilities only).
- `apps/dashboard/app/global-error.tsx` — the ONE intentionally inline-styled page (renders when the root
  layout/stylesheet fails, so it cannot use token utilities).
- raw-palette bypass sites: chat/ToolsEditor · memory/{Library,Inspector}Pane · models/ModelCatalogTable ·
  batches/BatchesStatsPage · marketing/{page,status}.
Context: `apps/dashboard` (Next 16, Tailwind v4 CSS-first `@theme inline`, shadcn/ui, lucide-react).
Honors: token discipline (components consume token utilities, never raw hex/px — the v13 design-system rule);
  UDD design-loop first-class ([[ui-ux-polish-standing-bar]]); presentation-only restyle recipe [[ui-restyle-recipe]].
Anchors the contract cites: the `:root`/`.dark` token names; `--font-geist-*` vars; `@theme inline` bridge.
Issues/Risks (→ §1): (a) Tailwind v4 `@theme inline` output is UNLAYERED and beats `@layer base :root` — a
  same-name self-reference collapses to empty (the font-wiring trap); (b) an accent hue used AS TEXT on a soft
  accent fill can fall below AA even when it works as a solid fill.
Related intent: the QUEUED whole-UI hallmark refactor (memory ui-refactor-hallmark-theme); standing UI/UX bar.
Ground SHA: 3c27af5 (branch base) — restyle built on top; anchors re-resolved at verify (see §6 live-verify).

---

## 1 · SPECIFY — the rules

Feature: Airier enterprise AI-SaaS restyle of the whole dashboard, driven from the token layer.
Framings weighed: token-layer value-swap (chosen — re-themes all 45 pages, zero per-page edits) · per-page
  restyle (rejected — 45× the work, drift-prone) · dump hallmark HTML (rejected — abandons our shadcn system).
Must:
<must>
  - M1: Every route renders the Airier theme (azure #2f6df0 signal live) in BOTH light and dark.
  - M2: Body + headings render in Geist; every metric/numeral renders in Geist Mono (tabular figures).
  - M3: The restyle is driven from the token layer — token NAMES unchanged; per-page edits limited to
    sweeping raw-palette bypasses back onto token utilities.
  - M4: WCAG AA (4.5:1) contrast holds on every restyled surface, verified by axe over all routes.
</must>
Reject:
<reject>
  - R1: an accent-as-text pairing below 4.5:1 -> must not ship (AA floor is hard).
  - R2: a raw hex/named-palette color that bypasses the token layer (except global-error's intentional inline) -> sweep to a token.
</reject>
After:
<after>
  - Both themes shipped; Geist live (verified on a rendered page, not just a green build); axe = 0 color-contrast.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ next/font + Tailwind v4 wire Geist correctly with the existing @theme bridge — LOWEST confidence: the
    @theme font token collided by name with :root and Geist silently fell back to the UA default; cost = the
    headline typography of the whole restyle is wrong while the build stays green. RESOLVED (fixed in f265243).
  - [x] the accent color is legible as active-nav text on the soft fill — DENIED: #2f6df0 on #eef3fe = 4.14:1,
    fails AA; resolved via the --accent-soft-foreground token (19b397f).
  - [x] the destructive fill passes AA with a white label — DENIED at the margin: 4.45:1; darkened to #cc3d37 (4.9:1).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases

<scenarios>

```gherkin
Scenario: every route renders the Airier theme in both themes   # M1
  Given the dashboard built with the Airier token palette
  When any of the 45 routes is rendered under prefers-color-scheme light AND dark
  Then --primary resolves to #2f6df0 (light) / #5b8cff (dark) and the page paints the graphite/azure theme
  And no route retains the old identity palette

Scenario: Geist actually renders   # M2
  Given the app served from a prod-equivalent build
  When getComputedStyle(body).fontFamily is read on a live page
  Then it begins with "Geist" (not ui-sans-serif) and the .font-mono utility resolves to "Geist Mono"
  And a green `next build` alone is NOT accepted as proof

Scenario: AA contrast floor holds   # M4 / R1
  Given axe-core run over every captured route (light)
  When the color-contrast rule is evaluated
  Then zero serious color-contrast violations are reported on restyled surfaces
  And the pre-existing structural findings (nested-interactive, heading-order) are unchanged (out of scope)

Scenario: no token-layer bypass   # M3 / R2
  Given a hex + arbitrary-value + named-palette grep across app/ and components/
  When the results are reviewed
  Then the only raw colors are global-error's intentional inline styles
  And every other surface consumes token utilities
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape

```
Token contract (apps/dashboard/app/globals.css) — the frozen shape of the Airier theme:
  :root / .dark custom properties (NAMES unchanged from v13; VALUES → Airier):
    --background #fbfcfd / #0c1015   --foreground #101720 / #eaeff5   --card #ffffff / …
    --primary #2f6df0 / #5b8cff      --primary-hover #2a61d8          --accent-soft #eef3fe / #16233d
    --success #16955f  --warning #bd8410  --destructive #cc3d37 (light)  … + *-text AA-safe variants
    --font-sans: var(--font-geist-sans), system-ui, …   --font-mono: var(--font-geist-mono), …
  NEW token: --accent-soft-foreground  #1c4bb8 (light, 6.9:1) / #8fb0ff (dark, 7.3:1)
    → AA-safe text on --accent-soft; mirrors the existing --success-text / --destructive-text pattern.
  @theme inline bridge: --color-* / --font-* reference the underlying token DIRECTLY (never self-name).
  Fonts (layout.tsx): Geist + Geist_Mono (next/font/google) as --font-geist-* vars; body gets `font-sans`.
Access pattern: components consume token UTILITIES (bg-primary / text-accent-soft-foreground / font-mono …).
```

Glossary deltas: `accent-soft-foreground: the AA-safe deep/lifted azure used for accent-colored TEXT on the
  soft accent fill (active-nav label, accent pills) — distinct from --primary, which is the solid accent.`
Status: FROZEN @ v1 — approved by Tin Dang (design-definition loop; captured screen v3 ba3c7dd4 LOCKED before build)
Reported: yes — the captured-screen artifacts were the show-before-ask; Tin locked v3.
Least-sure flag surfaced at freeze: [contract] the biggest risk is the next/font ↔ Tailwind-v4 `@theme`
  wiring — the font tokens must NOT self-reference (`--font-sans: var(--font-sans)`), or `@theme`'s unlayered
  output collapses to empty and Geist silently drops to the UA default while `next build` stays green. This
  MUST be verified on a live render (computed body font-family), never on a green build alone.

---

## 4 · TESTS — failing-first suite (red)

Coverage target: every route rendered + axe over all (behavioral, not unit).
Plan (one check per scenario):
<test_plan>
  - live-render probe (playwright): computed body font = Geist / font-mono = Geist Mono / --primary azure · covers M1,M2
  - axe-core capture harness (`e2e-review/capture.spec.ts`, 6 personas × 35 routes, light) · covers M4,R1
  - grep sweep (hex + arbitrary + named-palette) across app/ + components/ · covers M3,R2
</test_plan>
Tests live in: `apps/dashboard/e2e-review/capture.spec.ts` (existing harness, reused) + the live-render probe.
RED-first evidence: the FIRST capture run (before fixes) reported 20 serious color-contrast violations and the
  probe showed body font = `ui-sans-serif` (Geist NOT applied) — a genuine red before the build/verify pass.

---

## 5 · BUILD — AI writes code

Scope (may touch): `apps/dashboard/app/`, `apps/dashboard/components/`
Strategy (ordered batches): 1. font wiring + token palette (foundation, 487f483) · 2. raw-color→token sweep
  (a83c3b5) · 3. font-wiring bug fix once the live probe caught it (f265243) · 4. AA contrast fixes once axe
  caught them (19b397f).
Persona: ui-designer (atop SOUL.md) — the graphite/azure signal discipline + AA floor as a domain stance.
Strategy actually used: as planned; the build was DRIVEN by the verify evidence (probe → font fix; axe → AA fix)
  rather than assumed-correct — the "green build ≠ correct render" lesson.
Safety rule: token NAMES never change (so all 45 pages re-resolve); AA is a hard floor, never a build fudge.
Code lives in: `apps/dashboard/`
Constraints: token utilities only (no raw hex/px except global-error's intentional inline); no new deps
  (Geist ships in Next's font-data.json).

---

## 6 · VERIFY — evidence + non-functional review

- [x] all checks pass (build green; live-render probe green; axe 0 color-contrast)
- [x] coverage did not decrease (n/a — presentation; the axe route coverage is complete, all 35 captured)
- [x] no test or contract altered (the frozen token contract honored; AA fixes ADD a token, don't weaken it)
- [x] the green was EARNED — the first capture run was genuinely red (20 violations + wrong font); fixes were
      driven by that evidence, then re-verified to 0. Not overfit: axe is an independent oracle over real renders.
- [x] concurrency / timing — n/a (static CSS/tokens)
- [x] no exposed secrets / injection / unexpected deps — no new deps; Geist from Next's bundled font data
- [x] layering & dependencies follow CONVENTIONS — token layer is the single source; components consume utilities
- [x] a person reviewed — Tin locked the captured screen (v3 ba3c7dd4) before build; PR review pending push

### Build expectations — what "correct" looks like
- [x] Every route paints graphite/azure in light + dark — confirmed by the persona capture (public + authed shots, both themes).
- [x] Geist + Geist Mono actually render — confirmed by the live-render probe (`Geist, …` / `Geist Mono, …`), NOT just a green build.
- [x] axe over all routes = 0 color-contrast — confirmed by the re-run summary (20 → 0).

### Deep checks
- [x] WIRING — --accent-soft-foreground referenced by sidebar/marketing/batches + emitted via @theme; font-sans applied on body.
- [x] DEAD-CODE — no orphaned token (accent-soft-foreground consumed; the redundant :root font lines are shadowed but documented).
- [x] SEMANTIC — the token contract read in full; names match the GLOSSARY delta.

### Live-verify evidence — the §0/§3 anchors resolve in the current tree
- [x] every symbol §3 cites resolves — layout.tsx/globals.css/sidebar.tsx confirmed at HEAD 19b397f; probe read live values.
- [x] no anchor moved/renamed since ground.

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: self · adversarially checked: ran axe as an INDEPENDENT oracle over real renders (not test names); the
  first pass was genuinely red (20 violations, wrong body font); confirmed the fixes clear it via a clean re-run,
  and confirmed Geist via computed-style (defeating the "green build masks unapplied font" failure mode).

### Advisor 3-lens verdict — sequential
Advisor: self
1. Security: CLEAR (presentation-only; no auth/data/secret surface touched; no new deps).
2. Concurrency: CLEAR (static tokens/CSS).
3. Architecture: CLEAR — the app-wide blast radius (shared @theme/token change) was the risk; contained by
   keeping token NAMES stable and verified by full-route axe + live render.
Verdict: PASS
Residue: none theme-scoped. 5 pre-existing structural a11y findings (nested-interactive ×5, heading-order on
  3 routes) logged OUT of scope — not introduced by the restyle; candidate follow-up task.
Binding: advisory — architecture

### GATE RECORD
Reported: yes — the ship evidence (build green · live-render probe · axe 20→0) rendered before this outcome.
Outcome: PASS
Reviewed by: Tin Dang (design lock pre-build; PR review pending push) · date: 2026-07-17

---

## 7 · OBSERVE — feed the next loop

Watch: on future dashboard PRs, re-run the axe capture (regression guard on the AA floor); re-run the
  live-font probe on any Tailwind/next-font bump (the @theme collision could recur).

### Decisions (ADR)
- [AI] token-layer value-swap over per-page restyle — re-themes all 45 pages with zero per-page edits (§1).
- [AI] new --accent-soft-foreground token (not darkening --primary) — keeps the solid accent bright while
  giving accent-as-text an AA-safe value; mirrors the --success-text/--destructive-text pattern (§3/§6).
- [Tin] LOCKED the "Airier" v3 captured screen (Geist + azure) before build (§3 freeze).

### Spec delta
- [SPEC · seeded] a follow-up task should address the 5 pre-existing STRUCTURAL a11y findings (nested-interactive
  on memory/vision, heading-order on routing/plans) — out of scope for a color/type restyle (evidence: axe re-run).

### Competency deltas
- [UDD · folded] a green `next build` proves compilation, NOT that a font/theme applies — verify computed style on a [folded foundation-version 53]
  LIVE render (evidence: Geist fell back to ui-sans-serif while the build was green; caught only by a playwright probe).
- [UDD · folded] an accent hue that works as a solid FILL can fail AA as TEXT on its own soft tint — give accent-as-text [folded foundation-version 53]
  its own AA-safe token (evidence: #2f6df0 on #eef3fe = 4.14:1, failed on ~30 routes via the shared active-nav).
- [ADD · folded] Tailwind v4 `@theme inline` output is UNLAYERED and beats `@layer base :root`; a same-name [folded foundation-version 53]
  self-reference collapses to empty (evidence: --font-sans: var(--font-sans) dropped Geist — [[tailwind-v4-font-token-collision]]).
