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
