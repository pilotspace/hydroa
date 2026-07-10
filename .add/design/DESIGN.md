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

- **platform-console-flat-redesign** (milestone `platform-console-flat-redesign` · 2026-07-06 —
  DIRECT iterative design pass, not a fresh AskUserQuestion intake: Tin drove 3 rounds of rapid
  feedback straight on a published concept Artifact — aesthetic reference → spacing polish →
  "formalize that transcription step" as the explicit instruction to lock the converged values.
  This row transcribes those values into tokens.json; it does **not** confirm the milestone itself
  (still `queued --await-confirm`, gating `new-task`) nor resolve the open questions below):
  - FIDELITY — concept-review hi-fi (a published, interactive Artifact mock). No
    `.add/design/captures/*.png` exists yet — same non-blocking `missing_capture` pattern
    `add.py check` already WARNs for `batches-workspace`/`batches-stats`.
  - CONCEPT — flat, borderless, sharp-cornered "SaaS professional" treatment for the platform admin
    console (tenant directory + tenant-detail tabs) ONLY, not a dashboard-wide identity change.
    Visual reference: commandcode.ai (near-black, grain, sharp corners), confirmed via WebFetch —
    but that reference shaped the REVIEW ARTIFACT'S OWN presentation chrome, not the shipped
    recommendation: the actual admin-console content stays in Aurora's existing light theme, per
    the standing "Light-only (Tin 2026-06-25)" decision above. Relationship to Aurora is
    **layered alongside, not replacing** (open question #2 below is still formally unconfirmed, but
    "layer" is this row's working assumption — consistent with every VISUAL row since Aurora shipped).
  - LAYOUT — unchanged IA (tenant directory table, tenant-detail tabbed screen) plus two additive,
    frontend-only elements proposed alongside the visual pass: a Tenant Overview Strip and a global
    Command palette (⌘K) — both reuse already-fetched data / the existing tenant-search endpoint,
    no new contract.
  - VISUAL — the first deliberate exception to the "REUSE Classic-Blue verbatim, no new brand value"
    streak every row above has followed since Aurora shipped, but the exception is narrow: every
    color this recipe needs (canvas/surface/hairline/hairline-strong/text/muted/accent/critical/
    amber-line) already exists, verified 1:1 against `tokens.json`'s slate/blue/red/amber primitives.
    Genuinely new primitives, all additive (`add.py check`: `tokens.json layer-valid` PASS,
    86/87 pass/fail unchanged before vs. after):
    - `primitive.color.emerald.700` `#047857` — a darker, AA-contrast-safe step in the EXISTING
      emerald family (not a new hue) for small on-light-surface success/status text; the existing
      `success`/`emerald.600` is fine for icons/badges but under AA at text sizes.
    - `primitive.radius.2xs` (2px) and `.xs` (3px) — two steps below the existing floor (`sm`=4px),
      for micro tags/badges and buttons/inputs/banners respectively. This recipe's card-level
      surfaces (table/plan-card/adjust-panel) reuse the EXISTING `sm`=4px verbatim — no new value.
    - `primitive.font.family.mono` (ui-monospace stack) — for tabular numeric columns and
      identifier-like text (actor emails, action tags), paired with `tabular-nums` at build time.
    - Spacing: **zero new tokens.** Every internal rhythm value in the actual console content maps
      onto the existing `semantic.space.{inset-xs,inset-md,inset-lg}` (8/16/24px) verbatim — the
      mock's own `6rem` section gap is the REVIEW DOCUMENT'S presentation rhythm (separating
      "Screen 1" from "Screen 2" etc. for a reviewer), not a product value, and is deliberately
      NOT transcribed here.
    - New semantic aliases: `semantic.color.success-text` → `emerald.700`; `semantic.font.family.mono`
      → the new mono primitive (both general-purpose, not flat-recipe-specific — any Aurora page
      needing accessible small-text success color or tabular alignment can use them too);
      `semantic.radius.flat-tag` / `flat-control` / `flat-card` → `2xs`/`xs`/`sm` (deliberately
      named `flat-*` rather than reusing/renaming `control`/`card`, so Aurora's existing
      `control`=6px/`card`=10px radii stay untouched for every non-platform-console page).
  - **Naming collision RESOLVED (2026-07-06)**: `apps/dashboard/components/ui/card.tsx`'s existing
    `Card` `variant="flat"` (MORE shadow/radius: `rounded-2xl border-transparent shadow-lg`) was
    renamed to `variant="soft"` (its one call site, `PlatformTenantDirectory.tsx`, updated;
    behavior byte-identical under the new name, red/green-verified). A genuine `variant="flat"` was
    then added — no border, no shadow, `rounded-[var(--radius-flat-card)]` — component-primitive
    support only, not yet consumed by any screen.
  - **Component-primitive wiring pass (2026-07-06, scoped — NOT the screen build)**: gave the
    formalized tokens real Tailwind-consumable form and fixed two already-identified, concrete
    defects, without presuming any of the 6 open questions below:
    - `globals.css`: `--success-text`/`--radius-flat-tag`/`--radius-flat-control`/`--radius-flat-card`
      added to `:root` (+ `.dark` for `success-text`, reusing the existing dark success value — no
      speculative new dark-mode invention). The 3 radius vars are deliberately NOT given a
      `@theme` mapping — verified empirically that `tailwind-merge` does not recognize a
      custom-named radius utility as conflicting with the native `rounded-lg` scale (both classes
      would silently coexist); consumed instead via the arbitrary-value form
      `rounded-[var(--radius-flat-card)]`, which `tailwind-merge` does correctly resolve.
    - Corrected this row's own `font.family.mono` value in `tokens.json`: the original entry was a
      narrower ad-hoc guess: the real, already-in-production-use value (13+ existing call sites) is
      Tailwind v4's own built-in default (confirmed in `node_modules/tailwindcss/theme.css`), which
      includes `Monaco`/`Liberation Mono`/`Courier New` the original entry dropped. No `globals.css`
      override needed — the built-in default already matches after the correction.
    - `Badge`'s `warning`/`success` variants used `text-warning`/`text-success` directly — under AA
      at badge text size (this row's earlier research measured `warning`'s ~2:1). Fixed: `warning` →
      `text-warning-foreground` (an existing amber-800 token already built for exactly this, just
      unused); `success` → `text-success-text` (new, this pass).
    - `PlatformTenantDirectory.tsx`'s reserved-tenant "Platform" tag used `variant="warning"` — a
      category mismatch (reserved ≠ risky) this row's research had already flagged. Changed to
      `variant="outline"` (neutral); the one existing test asserts a differential
      (`standardBadge.className !== platformBadge.className`), not a literal variant, so it holds.
    - All red/green-verified (new failing tests confirmed before each fix, green after); full
      dashboard suite 1008/1008, eslint clean, `add.py check` unchanged (86/87, tokens.json still
      `layer-valid` PASS). Two pre-existing, unrelated-to-this-pass findings surfaced and
      deliberately NOT touched (out of scope): `globals.css`'s `--radius-sm` (6px) already diverges
      from `tokens.json`'s declared `primitive.radius.sm` (4px) — predates this milestone; and no
      other component references `text-success`/`text-warning` directly (Badge was the only
      consumer, confirmed by repo-wide grep).
    - Explicitly NOT done here (still belongs to the real screen build, once/if confirmed): applying
      `soft`/`flat`/`flat-tag`/`flat-control` to any actual page beyond the one pre-existing `soft`
      call site; Button/Input/Table were left untouched — no concretely-identified defect motivated
      touching them speculatively.
  - Elevation/shadow (not new tokens — composite kinds stay in `globals.css` per the existing
    `_elevation_note`, unchanged convention): default card/table shadow → none, separation comes
    from surface-on-canvas contrast instead; two named exceptions carry over verbatim from the mock's
    own comp-notes — the plan-comparison grid keeps a hairline border (aids cross-row scanning), and
    the command palette keeps a floating shadow (the one surface that legitimately stays "elevated").
  - Capture: `platform-console-concept-v2.html` (scratchpad) + published interactive Artifact.
  - **Design-confirm: NOT YET CONFIRMED.** This row locks the token VALUES per Tin's explicit
    instruction. Still open, unresolved by this row: 3 visual questions (how literal "borderless"
    should be · layer-vs-replace Aurora · whether to quiet the always-on "Platform Admin Mode"
    banner too) and 3 feature questions (command-palette navigate-only vs. execute-actions ·
    Overview-Strip eager-fetch-all-5-fields vs. defer · whether the Tier-3 directory kind-filter
    happens at all, given it reopens the FROZEN `platform_tenants_router.py`) — and
    `milestone-confirm` on `platform-console-flat-redesign` itself, still un-run.
  - **Persona deep-dive (2026-07-06):** 3 parallel agents — `ui-designer`, `ux-researcher`, and
    the newly-seeded `accessibility-auditor` (`.add/personas/accessibility-auditor.md`) — each
    independently researched this row's 6 open questions, plus (accessibility-auditor only) a
    fresh WCAG 2.2 AA sweep of the REAL shipped platform-console pages, not just the concept mock.
    Full synthesis: `persona-dive-report.html` (scratchpad + published Artifact). Verdicts reached
    (still **NOT accepted** — Tin has not yet ruled on any of them): borderless literal for
    card/panel chrome only, not table hairlines; layer alongside Aurora, don't replace; quiet the
    safety banner (the mock already quietly did); command palette navigate-only; Overview Strip
    eager-fetch as 4 independent queries; Tier-3 kind/plan filter does not belong in this
    milestone. Separately, and independent of whether this milestone proceeds at all:
    accessibility-auditor surfaced **13 findings in code already shipping today** (10 live, 1
    latent, 1 test-discipline gap, 1 tooling-coverage gap) — most notably no focus-move + no
    live-region announcement on route change or when the safety/impersonation banners mount, and
    a `Badge variant="destructive"` AA-contrast fail (4.14:1, independently confirmed by both
    ui-designer and accessibility-auditor) following the exact same pattern as the
    warning/success bugs fixed earlier this session.
  - **Contained accessibility fixes applied (2026-07-06)**, after 2 AskUserQuestion timeouts on
    which of the 13 findings to act on — proceeded on the already-flagged "Recommended" option only
    (the contained, low-blast-radius subset), explicitly NOT `milestone-confirm` (Tin's own
    product/visual call, left untouched). 4 findings fixed, all the same defect shape as the
    earlier warning/success Badge fix (a raw semantic color used as literal text, swapped to an
    AA-safe `-text` alias): `Badge` `destructive` variant; `ui/states.tsx`'s `Success`; `ui/
    stat-card.tsx`'s both delta tones (latent — no live caller yet). Also fixed `ui/states.tsx`'s
    `ErrorState` — **caught while implementing, not itemized as its own row in the synthesized
    13-finding report** (the auditor's own raw contrast script had already computed it failing,
    4.47:1/4.28:1, but my synthesis missed pulling it out as a distinct line item; same file, same
    defect as `Success`, fixed alongside it). New token: `primitive.color.red.700` (`#B91C1C`) +
    `semantic.color.destructive-text` (mirrors `success-text`'s existing pattern exactly);
    computed 5.0–6.0:1 across white/app-canvas/solid-muted for every new call site — real headroom,
    no thin margins. Red/green-verified: 4 new tests RED-confirmed (wrong class present) then GREEN;
    full dashboard suite 1012/1012; eslint clean; `add.py check` unchanged (86 passed/87 failed,
    `tokens.json layer-valid` PASS). **Deliberately NOT touched**: the other 10 findings — focus
    not moving on route change, both banners' missing live-regions, confirm-dialogs' button order,
    the heading-level skip, field-errors' missing aria-invalid/describedby, dialogs not hiding
    background from AT, the Keys table's missing accessible name, the size=sm 32px-vs-44px
    discrepancy, the test-name-overpromises gap, and the axe-suite coverage gap — all bigger
    blast-radius or process/tooling findings, left for a dedicated follow-up task. The 6 open
    design questions and `milestone-confirm` remain exactly as open as before this fix pass.
  - **Two ui-designer caveats DECIDED (2026-07-06, Tin, discussed directly):**
    - Q1's plan-card caveat: **keep** the `.plan-card.current` accent-colored border (redundant
      with the "Current plan" badge — good defensive design per WCAG 1.4.1 "not color alone" —
      contrast clean at 8.86:1, and helps at-a-glance scanning in a 3-up comparison grid). It's a
      genuinely new Aurora pattern, so it's now a named, reusable semantic token rather than a
      one-off hex: `semantic.color.selected-border` → `{primitive.color.blue.brand}` (reuses the
      EXISTING accent/Classic-Blue value verbatim — no new primitive color). No new CSS variable
      needed in `globals.css`: the real build can consume the already-wired `border-primary`
      Tailwind utility directly, since `--primary` already equals this exact value.
    - Q3's banner caveat: the quieted safety banner's residual amber accent line (2.15:1, under
      the 3:1 non-text guideline) is **replaced with a plain neutral divider** — reuses the
      EXISTING `hairline-strong` (slate-300) value, no new token at all. Rationale: the line was
      too faint to carry real signal and not neutral enough to genuinely disappear; since the
      actual goal was de-emphasis, a plain neutral more honestly achieves it and sidesteps the
      contrast question entirely. Concept mock (`platform-console-concept-v2.html`) updated to
      match: `.safety-banner`'s `border-left` now uses `var(--hairline-strong)` instead of
      `var(--amber-line)`.
    - Both are refinements within Q1/Q3, not full resolutions of those top-level open questions
      (Q1's literalness elsewhere, Q3's overall "quiet it" call, and the other 4 open questions
      are unaffected).
  - **`milestone-confirm` RUN (2026-07-06)**: Tin accepted the 6 recommendations as the scope's
    working direction ("both milestone-confirm platform-console-flat-redesign"). MILESTONE.md's
    Scope/Tasks/Exit-criteria drafted per the `scope.md` rubric (4 breadth-first tasks:
    `console-flat-visual-pass`, `tenant-overview-strip`, `command-palette`, `tenant-activity-tab`;
    Tier 3 kind/plan filter explicitly OUT). `new-task` is now open for this milestone. The 10
    deferred accessibility findings remain their own, separate follow-up — not folded into these
    4 tasks.
  - **`console-flat-visual-pass` TASK-LEVEL design-intake + concept (2026-07-06)** — the first of
    the 4 tasks; DIRECT (not a fresh AskUserQuestion interview — 3 of 4 axes were already settled
    by this row's own persona-dive + Tin's decided caveats above; stated plainly with rationale,
    open to redirect, per Rule 2's "decide as project lead at 95% confidence" rather than a 3rd
    AskUserQuestion round after 2 prior timeouts this session):
    - FIDELITY — hi-fi mockup of the REAL 6 screens (not the milestone-level review-document
      format above) — matches Tin's own words ("give me design concept") and lets the human judge
      the actual shipped IA, not an abstract comp.
    - CONCEPT — inherited verbatim from this row's own goal/persona verdicts (flat/borderless/
      sharp-cornered), not re-decided at the task level.
    - LAYOUT — byte-identical IA to the shipped screens (directory table + 5-tab shell) — this
      task's own MILESTONE.md Scope line is visual-only; new IA belongs to the 3 sibling tasks.
    - VISUAL — the full locked token set, plus 5 concrete calls this task's own Ground (TASK.md §0
      Issues/Risks) surfaced and resolved: (1) `StatCard` needs a new additive `variant`
      passthrough — none exists today; (2) both banners quieted, not just `PlatformSafetyBanner`
      — `ImpersonationBanner` shares the identical recipe, Scope named only one; (3) the 3 raw
      confirm-dialogs (Keys/Members/Plan) deliberately KEEP their current elevated shadow-lg/
      rounded-lg — modal elevation is a different concern than resting-state flatness, and no
      defect motivates touching them; (4) `PlatformPlanCatalog`'s shared `PlanCard` means this
      pass unavoidably reskins the Screen-1 catalog page too — deliberate reuse spillover, not
      scope creep; (5) Button/Input/Badge get a page-LOCAL sharper-corner override
      (`flat-control`/`flat-tag`) via className, not a change to the shared component files — every
      other dashboard page's controls stay their existing rounded-md default untouched.
    - Two new computed pairings this pass introduces (ui-designer/accessibility-auditor metrics):
      `selected-border` (Classic Blue) vs card = **8.86:1** (≥3:1 non-text floor, real margin);
      banner divider (`hairline-strong`) vs its own warning-tint background = **1.38:1**, but
      decorative-only — the divider carries no state on its own (text + icon already do), matching
      Tin's own rationale for dropping the amber accent line in the first place.
    - Capture: no local headless-capture tooling in this worktree (same constraint as
      `batches-workspace`/`batches-stats`) — substituted with a published interactive Artifact:
      https://claude.ai/code/artifact/c0e480e3-bde9-42a5-b3ce-d543236cb5c4. HTML mock saved
      regardless: `mocks/console-flat-visual-pass.html` · render tree:
      `prototypes/console-flat-visual-pass.json`. `add.py check` will WARN `missing_capture`
      (no `captures/*.png`) — expected, non-blocking, per the same precedent.
    - Persona-evidence checklist (ui-designer + ux-researcher + accessibility-auditor Success
      Metrics, each item confidence-tagged) rendered beside the capture inside the Artifact itself
      — not duplicated here; see the Artifact's own "Persona-evidence checklist" section.
    - **Design-confirm: NOT YET CONFIRMED.** Awaiting Tin's review of the Artifact above — approve
      as-is, or redirect any of the 5 flagged decisions. No `.tsx` file has been touched yet; §3
      CONTRACT freeze and Build both wait on this gate, per the UDD loop's own hard rule
      ("Confirm before build").
    - **VISUAL redirect — v2 (2026-07-06, Tin: "I wanna it become more tech/space/modern/flat
      designer in white luxury")**: a genuine visual-language pivot, not a caveat-level tweak —
      same published Artifact redeployed in place (`label: tech-space-white-luxury-v2`), same
      IA/copy/screens, CONCEPT/LAYOUT axes unchanged. Read "space" as generous/airy (stated
      explicitly in the Artifact's own redirect note so it's cheap to correct if Tin meant
      something more literal). Six concrete moves, each independently reversible:
      1. Canvas moves to near-white (`#ffffff`/`#fafafa`), a scoped LOCAL surface for this console
         only — Aurora's shipped `--background` (slate-tinted) stays untouched everywhere else,
         same "layered alongside" discipline as `flat-card`/`flat-control` already follow. Not yet
         a real token — proposed, pending confirm.
      2. Hairline rules replace card chips as the DEFAULT separator (most groupings now separate
         via a 1px rule + whitespace); an actual bordered boundary is kept only where scanning
         genuinely benefits (the 3-up plan grid, the 2 Guardrails fieldsets).
      3. Monospace (the already-locked `font.family.mono` token) elevated to EVERY number/date/
         identifier — budget figures, table dates, key IDs/prefixes, member emails. Inter stays for
         headings/labels/body. This is the main "tech" signature move.
      4. Exactly ONE precision-instrument detail (a hairline reticle/corner-tick frame), not a
         repeated motif — placed on the Budget tab's "Spent this month" figure only, the single
         truest hero number on the surface.
      5. Classic Blue (brand identity, UNCHANGED per the standing human-owned-identity rule) used
         far more sparingly — current-plan indicator, focus rings, links only; everything else
         near-monochrome ink/muted-grey.
      6. Safety banner: a FURTHER proposal beyond the already-decided divider-only fix — the mock
         now ships a live A/B toggle ("Plain" vs "Tinted") so Tin can compare the amber tint fully
         dropped (text+icon only) against the already-confirmed quieted-divider version directly,
         rather than committing unilaterally to the bigger change.
      Radius deliberately UNCHANGED (`flat-card`/`flat-control`/`flat-tag` stay 4/3/2px) — this
      redirect is achieved via canvas/separator/type/spacing/restraint, not a token-value
      re-litigation; named as an available further lever if the mock still doesn't read sharp
      enough. Persona-evidence checklist re-run against v2 specifically (not a rubber-stamp carry-
      forward) — 2 items honestly marked "would-need-user/browser-validation" rather than
      asserted. **Design-confirm still NOT YET CONFIRMED** — this IS the redirect Tin asked for,
      awaiting reaction to v2.
    - **VISUAL redirect — v3 (2026-07-06, Tin: "look boring")**: v2's restraint had nothing to
      anchor it — uniform type weight, no dark/light contrast, a signature detail (the reticle)
      too small to register. Diagnosed and fixed, same Artifact redeployed in place (label
      `dark-rail-hero-scale-v3`), IA/copy still unchanged:
      1. Added a confident dark rail (`#0a0a0f`, nav only) — content canvas stays white-luxury;
         the missing value-contrast was structural, not a palette problem.
      2. Blew out the type scale — page titles 26px→48px/700; the hero budget figure to 72px,
         framed. The gap between big and small is now doing real work.
      3. The signature motif is now actually visible: the reticle frame is 2× larger with a faint
         tint fill, plus a NEW mono spec-sheet "ref-tag" strip (ID/kind/created-style metadata)
         under every page header.
      4. Classic Blue used decisively again — solid-fill primary buttons (was near-black), a
         visible wash+ring on the current-plan card (was a bare hairline-weight border).
      5. Added quiet atmosphere: a faint dot-grid + soft blue vignette behind the canvas — a
         literal, restrained nod to "space," barely perceptible so it stays flat/luxury rather
         than becoming a texture.
      Re-verified, not carried forward blind: rail text computes 17.9:1 (off-white/near-black),
      dim rail label 4.9:1, active bright-blue-on-dark 5.1:1 (reusing the existing `blue.500`
      primitive, not a new hue) — all real AA margin on the one genuinely new surface (the dark
      rail). Radius still deliberately untouched (4/3/2px) — presence came from scale/contrast/
      atmosphere, not from reopening that decision again. **Design-confirm still NOT YET
      CONFIRMED** — awaiting reaction to v3.
    - **VISUAL POLISH — v4 (2026-07-06, Tin: "polish more :D")**: read as a positive-leaning signal
      on v3's direction (dark rail + hero type scale + visible signature + decisive accent +
      atmosphere), not a redirect — this round raises craft on the SAME direction rather than
      pivoting again, same Artifact redeployed in place (`label: polish-pass-v4`), IA/copy/CONCEPT
      unchanged. Six concrete moves:
      1. Every nav-rail destination gets a matching line icon, plus a bottom identity footer
         (`you@hydroa.io` / Superadmin) — ties back to this whole milestone's cross-tenant-
         superadmin theme (who am I acting as right now), not decoration for its own sake.
      2. The tab bar's per-tab static underline is replaced by ONE animated indicator that slides
         between tabs (JS-measured `offsetLeft`/`offsetWidth`, `aria-hidden`, selection state still
         lives on `aria-selected` — no keyboard-path change).
      3. The Budget hero stat now does real work: a usage bar (62.5% fill) under the $312.47
         figure, plus a trend chip ("↑ 8% vs last month") that reuses the real `StatCard`
         component's own existing delta pattern verbatim (arrow icon + text + sr-only word) —
         reuse, not a new idiom invented for the mock.
      4. Search input gets a leading icon (was bare, an expected affordance that was missing).
      5. Motion throughout — button hover-lift, tab/view fade-in, dialog pop-in — all under one
         `prefers-reduced-motion: reduce` guard, mirroring the real app's own existing global
         convention rather than a separate one-off.
      6. A checkmark icon reinforces the "Current plan" badge (was text + color only).
      Deliberately UNCHANGED from v3: dark-rail palette, 48px/72px type scale, dot-grid/vignette
      atmosphere, all radius values — this pass is additive craft, not a token re-litigation.
      One honest open item: the sliding indicator's measured position depends on rendered font
      metrics, heuristically correct but not yet checked outside this mock. **Design-confirm still
      NOT YET CONFIRMED** — "polish more" is encouragement to keep refining, not itself an approval;
      still awaiting an explicit yes/no reaction to v4.
    - **VISUAL POLISH — v5 (2026-07-06, Tin: screenshot of the Plan tab + "increase contract [read as
      contrast] and add grain noise with background lines as luxury texture")**: two concrete, scoped
      asks — the screenshot pins the first to the current-plan card specifically, not a broader
      re-litigation:
      1. Current-plan card contrast raised on three axes at once: the accent ring 1.5px→2px, the
         background wash changed from a top-fading gradient to one that holds strength across the
         whole card (`.085→.032` alpha, never reaching fully transparent), plus a soft directional
         shadow so the card lifts slightly off the page. The "Current plan" badge changed from
         outline to solid-fill (Classic Blue + white text) — it is the one badge on the surface
         answering "selected," not "classified," so a distinct treatment from the Kind/Status chips
         is deliberate, not an inconsistency. Computed: white-on-accent is the same two colors as the
         already-verified `selected-border` pairing (8.86:1), just swapped — contrast ratio is
         symmetric, so no new computation was needed, only re-citing it correctly.
      2. Luxury texture, two layers: fine engraved-paper hairlines (135° repeating line, ~2.5%
         opacity) added to the canvas background alongside the existing dot-grid/vignette, plus a
         NEW whole-viewport film-grain overlay (SVG `feTurbulence`, `mix-blend-mode:overlay`, ~5.5%
         opacity) so the grain reads consistently over both the white canvas and the dark rail — a
         dedicated fixed/pointer-events-none element, not baked into the canvas background, because
         `.nav-rail` paints its own opaque background and would otherwise block a canvas-only grain
         layer from ever showing there. Both layers are decorative/`aria-hidden`, deliberately tuned
         near-imperceptible at rest — richness without turning "flat" into "busy."
      Housekeeping this round: the Artifact's URL changed — the original link stopped resolving
      server-side mid-session (deleted or expired, not an action either of us took); republished as a
      new file (`mocks/console-flat-visual-pass.html` — this task had cited that path since v1 but
      never actually written it until now, a real gap, closed here) rather than retrying the dead
      binding. Current link: https://claude.ai/code/artifact/93f09a35-c58f-4397-a8e6-460aadd2cab0.
      **Design-confirm still NOT YET CONFIRMED.**
    - **VISUAL TWEAK — v6 (2026-07-06, Tin: "yes" to the recommended radar-arc option, after asking
      "what your advise to enhance luxury texture? replace engraved-paper hairlines")**: advised two
      options (a concentric radar/orbit-ring pattern tying to "space" + the reticle motif, vs. a
      safer brushed-linen micro-crosshatch) before touching code, per the exploratory-question norm
      — Tin confirmed the ring option. Replaced v5's diagonal engraved-paper hairlines with
      `repeating-radial-gradient(circle at 80% -10%, ...)` — the SAME off-canvas point as the
      existing top-right vignette, so the rings read as that light source's own structure made
      visible rather than a second, unrelated texture. Same restraint bar as v5 (~5% alpha,
      near-imperceptible at rest). Grain overlay and dot-grid both untouched. Same Artifact URL
      (redeployed in place this time — no repeat of the v5 dead-link issue). **Design-confirm still
      NOT YET CONFIRMED.**
    - **VISUAL TWEAK — v7 (2026-07-06, Tin: "reduce noise background by grain noise only")**: three
      stacked patterns (dot-grid + rings + grain) read as competing texture, not atmosphere. Read
      "grain noise only" as the one thing to keep — removed the dot-grid (v3) and the concentric
      rings (v6) from the canvas background entirely. Kept the top-right vignette (unchanged since
      v3): it's a single soft glow, not a repeating pattern, so it reads as ambient light rather than
      "noise" and was never part of what Tin flagged across v5/v6. Kept the whole-viewport film-grain
      overlay untouched (the explicitly-named keeper). Net: one texture system (vignette + grain)
      instead of three. Flagged the vignette-keep interpretation transparently in the chat reply in
      case Tin meant to drop that too — cheap to remove next round if so. **Design-confirm still NOT
      YET CONFIRMED.**
