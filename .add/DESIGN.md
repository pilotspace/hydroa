# Hydroa — Design (DESIGN.md)

> The design source of truth for **Hydroa** (production) — drafted 2026-06-13,
> **re-frozen to the "Airier" identity 2026-07-17** (milestone `dashboard-hallmark-restyle`,
> approved by Tin Dang). The AI drafts every UI screen against this doc; the JSON
> foundation below is what the validators lint (`.add/design/`).

This doc is the **prose front-door** to the UDD foundation: it carries the design
*identity* and *intent* a human owns, then points at the named-set JSON
(`tokens.json` · `catalog.json` · `prototypes/`) the AI renders from. The detailed
visual-language record lives in `.add/design/DESIGN.md`.

## Identity

Design identity is **human-owned** (Tin Dang). The current identity is **"Airier"** —
a light, calm, premium enterprise AI-SaaS console: cool-biased graphite neutrals, a
single precise azure signal used sparingly, Geist typography, and a real dark theme.
It supersedes the earlier Indigo/Inter "Aurora" identity (v13–ui-fidelity).

- **Brand color** — **azure `#2f6df0`** (dark `#5b8cff`): the single signal, used
  SPARINGLY — active-nav soft-fill, the one primary button per surface, links, focus
  rings, sparkline endpoints, the first chart series. Not a field of blue; a signal.
- **Palette** — accent → azure-500 `#2f6df0` (hover `#2a61d8`, ring `#2f6df0`,
  soft-fill `#eef3fe`, AA text-on-soft `#1c4bb8`) · canvas → `#fbfcfd` (near-white
  cool) · surface → white · muted → `#f4f6f9` · text → graphite `#101720`
  (muted `#48525e`, subtle `#7b8593`) · border → `#e7ebf0` · sidebar rail → `#f7f9fb`.
  Semantic status is kept SEPARATE from the accent: success `#16955f` · warning
  `#bd8410` · destructive `#cc3d37` (each with an AA-safe small-text variant). Neutral
  ramp = **graphite** (cool grey, chosen not inherited).
- **Typeface** — **Geist** (`Geist, system-ui, sans-serif`) for UI and headings, an
  off-Inter technical grotesque; **Geist Mono** for **every metric / numeral** (tabular
  figures) — "mono for all data". Self-hosted via `next/font`. Weights used: 400 · 500 ·
  600 · 700.
- **Voice / tone** — precise · calm · trustworthy (a billing/governance console, not a toy).

These flow into the **semantic** layer of `tokens.json`; the AI wires the primitives
beneath them. **Theme:** light is the default; **dark is SHIPPED** (the `.dark` block in
`globals.css`, a visible toggle in the app-shell top bar, no-flash via the head script).
`tokens.json` encodes the LIGHT canonical set; the per-theme `.dark` value pairs live in
`globals.css` (the DTCG dialect the validator lints is single-valued — see §Foundation).

## Principles

- **Primary user & their job** — a tenant owner/developer who logs in to watch spend,
  govern API keys & budgets, and trust the numbers. The one job: *see accurate cost and
  act on it without friction.*
- **Design principles** — one primary action per surface · never hide state (loading ·
  empty · error · success always rendered) · money & limits are unambiguous · the data
  never changes shape, only its presentation improves · the azure signal is spent in ONE
  place per surface, everything around it stays quiet · summary-before-detail (KPI cards →
  tables); state is encoded in FORM (pills, share-bars, sparklines) not color alone.
- **Accessibility floor** — WCAG 2.2 AA: AA contrast on BOTH themes · an accent hue used
  as TEXT gets its own AA-safe token (azure `#2f6df0` on the soft fill is only 4.1:1, so
  active-nav/pill text uses `--accent-soft-foreground` `#1c4bb8` = 6.9:1; the destructive
  solid fill is `#cc3d37` so a white label clears 4.9:1) · visible `focus-visible` ring on
  every interactive element · hit-target ≥ 44px · keyboard-operable dialogs (focus-trap +
  ESC) · landmarks (skip-link → `<nav>` → `<main id="main">`) · direction never rides on
  color alone (delta chips pair an arrow + sr-only word with the tone).

## Screens

One `.add/design/prototypes/<name>.json` per screen (a flat json-render tree the catalog validates).

| Screen | Prototype | Status |
|--------|-----------|--------|
| Foundation sampler (usage/cost shape) | `.add/design/prototypes/dashboard-foundation.json` | seed |
| Usage & cost (`/usage` + `/spend`) | `design/prototypes/usage-cost.json` | (v13 task usage-cost-ui) |
| Key & budget governance (`/keys`) | `design/prototypes/key-governance.json` | (v13 task key-budget-governance-ui) |

## Foundation

The named-set JSON the validators lint — under `.add/design/`:

- **Tokens** — `.add/design/tokens.json` · 3-layer (primitive · semantic · component),
  DTCG compact dialect. Encodes the **light** Airier set (azure/graphite/Geist). Elevation
  (box-shadow), motion-easing (cubic-bezier), the Geist webfonts, and the `.dark` value
  pairs are realised in `globals.css` (the dialect supports only color/dimension/
  fontFamily/fontWeight/duration). Dialect: `.add/tooling/templates/udd-tokens.md`.
- **Catalog** — `.add/design/catalog.json` · component catalog (typed props + token
  bindings). Component contract is theme-agnostic; unchanged by the restyle. Render
  adapter: `.add/tooling/templates/udd-catalog.md`.
- **Prototypes** — `.add/design/prototypes/<name>.json` · flat json-render screen trees.

`python3 .add/tooling/add.py check` lints this named set in place — a layer/catalog/tree/
cross-file violation goes red with a named code; silent when there is no `design/` set.

## Implementation binding (this repo)

The token layer is wired into the running app as the SINGLE source:
`apps/dashboard/app/globals.css` — `@layer base :root` (light) + `.dark` (shipped dark),
bridged to Tailwind v4 utilities via `@theme inline`. Geist + Geist Mono are loaded by
`next/font/google` in `apps/dashboard/app/layout.tsx` (exposed as `--font-geist-*`, applied
via the `font-sans` utility on `<body>`). Tokens are consumed by
`apps/dashboard/components/ui/*` (shadcn/ui + Radix primitives, `lib/cn.ts` merge) and the
`AppShell`. Charts use Recharts (themeable via the same `--chart-*` vars). Token NAMES are
stable — re-valuing `:root`/`.dark` re-themes all 45 routes with no per-page edits.

## Render

To turn the catalog + a prototype into a live, clickable UI, follow the render recipe in
**`.add/tooling/templates/udd-catalog.md`** (`## Render recipe`): `catalog.json` →
`defineCatalog(...)`, then `catalog.validate(spec)` on the flat tree as-is.
