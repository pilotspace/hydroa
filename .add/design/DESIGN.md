# DESIGN — visual-language ("Aurora" elevated direction)

> The UDD design record for the `ui-fidelity` milestone. Identity was delegated to the
> AI in auto mode (Tin, 2026-06-25: "you decide all, complete UI polish for best"); this
> file documents the chosen identity so it is auditable. Captured reference:
> `.add/design/captures/visual-language.png` (rendered from `mocks/visual-language.html`).

## Direction: evolve, don't replace
Keep the v13 anchors (indigo accent · slate neutrals · Inter) for brand continuity, and
add the three things the current FLAT system lacks — **depth**, a **display type scale**,
and a **brand gradient** — plus motion + richer surface layering.

## Identity (the decisions)
- **Brand accent** — indigo-600 `#4F46E5` stays primary; add a brand **gradient**
  `indigo-600 → violet-500` (`#4F46E5 → #7C3AED`) for hero / primary-CTA emphasis only.
- **Neutrals** — slate ramp retained; add an ink `#0B1120` for hero/footer surfaces and
  stronger text hierarchy (title vs body vs caption).
- **Typeface** — Inter (unchanged infra). New **modular type scale**:
  caption 12/16 · body 14/22 · body-lg 16/26 · heading 20/28 · title 24/32 ·
  display 36/40 (-0.02em, 700) · hero 56/60 (-0.03em, 700).
- **Elevation (NEW)** — layered soft shadows:
  sm `0 1px 2px rgba(15,23,42,.06)` ·
  md `0 2px 4px -1px rgba(15,23,42,.06), 0 4px 12px -2px rgba(15,23,42,.08)` ·
  lg `0 8px 24px -4px rgba(15,23,42,.12)` ·
  xl `0 24px 48px -12px rgba(15,23,42,.18)`.
- **Radius** — control 8 (keep) · card 12→14 · xl 20 (marketing) · 2xl 28 (hero panels).
- **Motion (NEW)** — easing standard `cubic-bezier(.2,0,0,1)` · emphasized
  `cubic-bezier(.3,0,0,1)`; durations fast 150 · base 200 · slow 300; neutralised under
  `prefers-reduced-motion: reduce`.
- **Surfaces** — canvas slate-50; cards white + md elevation + hairline slate-200 border;
  marketing hero on a subtle indigo/violet gradient mesh over ink.

## How it maps to tokens (the frozen contract — §3)
- `primitive.shadow.{sm,md,lg,xl}` (NEW) → `semantic.elevation.{card,raised,overlay,hero}`.
- `primitive.font.size.*` expanded → `semantic.font.size.{caption,body,body-lg,heading,title,display,hero}`
  (each carries size + line-height + tracking).
- `primitive.color.violet.*` (NEW) + `semantic.color.brand-gradient-from/to` (NEW).
- `primitive.motion.easing.*` + `primitive.motion.duration.slow` (NEW) →
  `semantic.motion.{ease,ease-emphasized,duration-*}`.
- Realised in `app/globals.css` `:root` + `@theme inline`; `.dark` kept coherent (not shipped).

## Light-only (Tin 2026-06-25)
Dark `.dark` block stays coherent with every new token but is not a verified deliverable.

## Identity correction — "Classic Blue" (Tin 2026-06-28, shipped v54)
The indigo `#4F46E5` accent above was SUPERSEDED by the v54 "Classic Blue luxury rebrand"
(commit `d1e7e72`): primary is now `#0F4C81` (Classic Blue) over slate neutrals + Inter, with a
`#0F4C81 → #2563eb` brand gradient. The shipped `app/globals.css` + `tmp/governance-mocks/keys.html`
are the live identity reference. Mocks bind to THIS palette, not the indigo above. Identity stays
human-owned (Tin's rebrand) — design mocks REUSE it, never invent a new brand value.

## Design intake — per-feature axes (design.md beat 0)
> Project default = the shipped Aurora Classic-Blue system. Per-screen overrides recorded as the
> `prototypes/<name>.json` note. Each row = the four axes (FIDELITY · CONCEPT · LAYOUT · VISUAL).

- **chat-playground** (program "AI feature depth" · milestone chat-playground · Tin 2026-06-28 via AskUserQuestion):
  - FIDELITY — **production** hi-fi (this is a build target, not a sketch).
  - CONCEPT — a **Console-grade LLM playground** (OpenAI Playground / Anthropic Console feel): dense,
    parameter-rich, a working surface an operator runs real work on — explicitly NOT a CRUD form
    (the thin first mocks were rejected for that, `captures/aifeature-*.png`).
  - LAYOUT — **3-pane**: sessions rail · conversation (top bar + thread + composer) · parameters/inspector panel.
  - VISUAL — REUSE the shipped Classic-Blue identity (no new brand value); Console density (tighter
    rhythm, smaller controls, more info per screen than the dashboard pages).
  - Capture (design-confirm): `captures/chat-playground.png` · render tree: `prototypes/chat-playground.json`.

- **batches-workspace** (milestone v57 · task batch-dashboard-surface · 2026-07-03 — DERIVED, not a
  fresh intake interview: Tin picked "broader — a real batches workspace, mirroring the existing
  chat/voice/memory/artifacts/vision/video playground pattern" via AskUserQuestion; a follow-up
  AskUserQuestion round (per-item detail scope + toggle inclusion) timed out with no reply, so
  these axes are proposed defaults pending reconfirmation at the design-confirm / contract freeze,
  not settled):
  - FIDELITY — **production**-leaning hi-fi (matches every shipped playground), but UNCONFIRMED —
    proposed, not interviewed live.
  - CONCEPT — a genuinely usable **submit + monitor workspace** (memory `aifeature-pages-usable-bar`:
    thin CRUD reskins rejected) — NOT Console-grade-dense like chat-playground; closer to
    VideoWorkspace's plainer async-job feel, since batches has one real interaction (submit JSONL,
    watch status) rather than a rich per-turn parameter surface.
  - LAYOUT — **flat list + polling** (VideoWorkspace shape), NOT ArtifactsWorkspace's master/detail —
    a direct consequence of deferring the per-item-results endpoint (see TASK.md §0 Issues/Risks):
    no per-item drill-down pane this round, so no second pane is needed.
  - VISUAL — REUSE the shipped Classic-Blue identity verbatim (no new brand value) — same rule as
    every prior row, not re-litigated.
  - Composer UX — **JSONL free-text textarea** (mirrors VideoWorkspace's prompt Textarea; also the
    authentic native shape of the OpenAI/Anthropic batch-file format, so it reads as domain-accurate,
    not just fast-to-build) over a structured per-line-item form. This is the single most consequential,
    least-confirmed call in this row — flag it as the headline item at design-confirm.
  - Capture: no local headless-capture tooling in this worktree (node_modules not installed; a full
    monorepo+browser install was judged too invasive to do unilaterally for one screenshot) — substituted
    with a published interactive Artifact instead of a static PNG (arguably a better review medium: the
    human can actually interact with hover/focus states). HTML mock saved regardless for a durable
    record: `mocks/batches-workspace.html` · render tree: `prototypes/batches-workspace.json`.
    `add.py check` will WARN `missing_capture` for this prototype (no `captures/*.png` file) — expected,
    non-blocking, noted here so it isn't mistaken for an oversight.
  - Settings-tab half (tenant toggle + savings StatCard) NOT separately mocked — it's a near-identical
    clone of `CacheSettings.tsx` plus one `StatCard` swap, low enough risk not to warrant its own hi-fi
    render; described structurally in TASK.md §1 instead.
  - **SUPERSEDED (Tin, 2026-07-03, correction)**: the submit+monitor CONCEPT and the JSONL Composer UX
    above are both WRONG — Tin corrected course: "we no need a playground for batch request, we just
    provide for admin to view statistics." No composer, no job-authoring UI, no per-item drill-down
    debate — none of it exists in the corrected feature. The published Artifact mock, this row's
    CONCEPT/LAYOUT/Composer-UX axes, and `prototypes/batches-workspace.json`'s `composer_card`/
    `joblist_card` subtrees are all retired, kept only as a record of the discarded direction (same
    non-destructive-correction convention as the "Classic Blue" identity note above). The corrected
    scope — a read-only admin statistics page (savings + volume + status breakdown) — has NOT been
    through its own design-intake yet; that runs fresh at batch-dashboard-surface's re-specify, as
    its own new row here, not a patch to this one.

- **batches-stats** (milestone v57 · task batch-dashboard-surface · 2026-07-03, re-intake after the
  submit+monitor direction above was reversed):
  - FIDELITY — production hi-fi, matching every shipped dashboard page (unchanged judgment from the
    superseded row).
  - CONCEPT — a plain read-only statistics page, closely modeled on the EXISTING
    `components/usage/UsagePage.tsx` (`/app/usage`, read in full this session): a hero region for the
    single headline number (there: Total Cost; here: dollars saved) + a `StatCard` grid below for the
    supporting numbers (there: requests/prompt-tokens/completion-tokens; here: volume + status
    breakdown) — reuse, not invention, per design.md's "research-components" rule. NOT Console-dense,
    NOT a workspace — exactly one real interaction on this page (view), no composer, no polling loop,
    no drill-down.
  - LAYOUT — hero (savings) + a `grid grid-cols-2 gap-4 sm:grid-cols-4` `StatCard` row below (volume +
    3 status counts: succeeded/errored/in-progress) — `UsagePage`'s Overview-tab shape verbatim,
    minus its `Tabs` wrapper (Records/Catalog/Trends don't apply — no equivalent secondary data to
    tab between for a 4-number page).
  - VISUAL — reuse Classic-Blue verbatim (unchanged from every prior row).
  - Access — a deliberate DIVERGENCE from the `UsagePage`/`SpendPage` precedent: those are open to
    every tenant role (only the in-page Edit action is role-gated); this page is `minRole:"admin"`
    end-to-end, because Tin's own words specifically named "admin" as the viewer ("provide for admin
    to view statistics"), not "every tenant member." Named here so the divergence from the closest
    precedent is visible, not accidental.
  - Capture: `.add/design/mocks/batches-stats.html` (Artifact) · render tree:
    `prototypes/batches-stats.json`. **Design-confirm: CONFIRMED 2026-07-03** — approved by Tin
    together with the §3 contract freeze (one "approve," both gates; see TASK.md §1/§3).
