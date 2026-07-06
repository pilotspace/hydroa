# TASK: Console flat/borderless visual pass

slug: console-flat-visual-pass · created: 2026-07-06 · stage: production
milestone: platform-console-flat-redesign
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: specify   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `components/platform/PlatformTenantDirectory.tsx:PlatformTenantDirectory` — `Card variant="soft"`
    wrapping `DataTable`; raw pagination `<button>`s (`border border-border bg-card`); `Badge
    variant="outline"|"secondary"` Kind chips.
  - `components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail` — mounts
    `PlatformSafetyBanner` once + `Tabs/TabsList/TabsTrigger/TabsContent` (config·budget·keys·
    members·plan).
  - `components/platform/PlatformConfigTab.tsx:PlatformCacheCard,PlatformGuardrailsCard` — two
    `Card` (variant omitted → `"default"`); Guardrails' 2 `fieldset`s use `rounded-lg border
    border-border`.
  - `components/platform/PlatformBudgetTab.tsx:PlatformBudgetTab` — `grid grid-cols-2` of 2×
    `StatCard`.
  - `components/ui/stat-card.tsx:StatCard` — hardcodes its own internal `<Card data-slot=
    "stat-card">` with NO `variant` passthrough today (see Issues/Risks #1).
  - `components/platform/PlatformKeysTab.tsx:PlatformKeysTab` — one `Card` (default) wrapping
    `Table`; a raw confirm-dialog `div.rounded-lg.border.border-border.bg-card.p-6.shadow-lg`
    (`role="dialog"` + `useFocusTrap`).
  - `components/platform/PlatformMembersTab.tsx:PlatformMembersTab` — `DataTable` (no Card
    wrapper); a 2nd instance of the identical raw dialog shape (impersonate-confirm).
  - `components/platform/PlatformPlanTab.tsx:PlatformPlanTab` — consumes
    `PlatformPlanCatalog.tsx:PlanCard,PlanCardGrid` verbatim (3-up grid); its own non-modal
    inline seat-cap confirm panel `div.rounded-lg.border.border-border.bg-card.p-4`; a 3rd raw
    dialog instance (remove-confirm).
  - `components/platform/PlatformPlanCatalog.tsx:PlanCard,PlanCardGrid,PlatformPlanCatalog` —
    `PlanCard`/`PlanCardGrid` are exported + reused by `PlatformPlanTab`; `PlatformPlanCatalog`
    itself (Screen 1, the catalog page) is not a named tenant-detail tab but shares the same
    `PlanCard` (see Issues/Risks #4).
  - `components/platform/PlatformSafetyBanner.tsx:PlatformSafetyBanner` and
    `components/platform/ImpersonationBanner.tsx:ImpersonationBanner` — both
    `border-warning/40 bg-warning/10 text-warning-foreground`, same visual family, different
    mount points (see Issues/Risks #2).
  - Shared primitives, pre-existing state from earlier this milestone (cited, not re-touched
    unless Anchors below say so): `components/ui/card.tsx:Card` (`variant:
    "default"|"soft"|"flat"`), `components/ui/badge.tsx:badgeVariants`,
    `components/ui/states.tsx:ErrorState,Success`.
Context (working folder):
  - `/private/tmp/.../scratchpad/platform-console-concept-v2.html` (+ its published Artifact) —
    a STANDALONE static mock with its OWN invented `:root` names (`--hairline-strong`,
    `--accent`, `--amber-line`) that do NOT exist in the real app (see Issues/Risks #5).
  - `.add/design/DESIGN.md`'s `platform-console-flat-redesign` row — full decision log
    (naming-collision resolution, persona-dive findings, the 2 decided caveats, 10 deferred
    a11y findings).
  - `.add/milestones/platform-console-flat-redesign/MILESTONE.md` — Scope In/Out, Shared/risky
    contracts, the 4-task breadth-first list (already drafted + confirmed this milestone).
Honors (patterns / conventions):
  - `card.tsx:Card`'s own header comment: the `variant` prop is "ADDITIVE ONLY — omitted...
    renders BYTE-IDENTICAL classes" to every pre-existing caller — the StatCard variant
    passthrough this task adds (Issues/Risks #1) must hold the identical guarantee.
  - The pervasive "mirrors X's own shipped Y convention exactly" cross-reference discipline in
    every `platform/*.tsx` header (e.g. Keys/Members/Plan's 3 dialogs each cite the sibling they
    mirror) — any new pattern this task introduces must be named + cross-referenced the same
    way, not silently duplicated.
  - MILESTONE.md's own "Layered alongside Aurora" line: every other dashboard page's existing
    rounded/shadowed treatment, and every `Badge`/`Input`/`Table` call site, stays untouched —
    only Card-family surfaces + the 2 banners are in play.
Anchors the contract cites:
  - `components/ui/card.tsx:Card` (`variant="flat"` — first real screen consumer)
  - `components/ui/stat-card.tsx:StatCard` (NEW additive `variant` passthrough prop — signature
    change, must be declared explicitly)
  - `components/platform/PlatformSafetyBanner.tsx:PlatformSafetyBanner` and
    `components/platform/ImpersonationBanner.tsx:ImpersonationBanner` (divider treatment, both)
  - `.add/design/tokens.json`'s `semantic.radius.flat-card/flat-control/flat-tag`,
    `semantic.color.selected-border`
  - `app/globals.css`'s `--radius-flat-card` (arbitrary-value only, no `@theme inline` bridge)
    and `--color-primary`/`border-primary` (the real `selected-border` realization)
  - The 6 screens' own root JSX returns + the 3 raw-dialog blocks (Keys/Members/Plan)
Issues/Risks (→ feed §1):
  1. `StatCard` has no `variant` passthrough today — Budget tab's 2 StatCards can't go flat
     without an additive prop change. Resolution (proceeding as project lead, low-risk/
     reversible): add `variant?: CardProps["variant"]`, default omitted — preserves every other
     dashboard-wide StatCard caller byte-identical.
  2. `ImpersonationBanner` shares the exact `border-warning/40 bg-warning/10` recipe as
     `PlatformSafetyBanner`, but MILESTONE.md Scope names only the latter (singular). Leaving
     them inconsistent reads as a bug, not a decision. Resolution: apply the identical quieted-
     divider treatment to both — a scope micro-clarification, not a re-litigation of Q3's larger
     deferred "quiet it" question.
  3. 3 near-identical raw dialogs (Keys revoke / Members impersonate / Plan remove) each
     hand-roll `rounded-lg border border-border bg-card p-6 shadow-lg`. Open question: sharp/
     flat too, or keep elevated-modal convention (shadow/rounding signal "layered above the
     page" — a different concern than resting-state flatness)? Resolution (proceeding as
     project lead, matches MILESTONE.md's own "no concretely-identified defect motivates
     touching X" discipline): dialogs KEEP their current elevated treatment untouched; only
     resting page surfaces (Cards, banners) go flat. Named for transparency, not silently
     decided.
  4. `PlatformPlanCatalog` (Screen 1) isn't a named tenant-detail tab, but its exported
     `PlanCard`/`PlanCardGrid` are reused verbatim by `PlatformPlanTab` (Screen 2, in scope) — a
     flat-variant change to `PlanCard` unavoidably reskins Screen 1 too. Deliberate reuse-over-
     invent spillover, not a scope violation (MILESTONE.md's Out list never protected the
     catalog page); flagged so it's expected at Verify, not a surprise.
  5. The concept mock's CSS var names (`--hairline-strong`, `--accent`, `--amber-line`) are NOT
     real app tokens — the design concept must translate every mock decision to the REAL token
     names (`border-primary`, `--color-primary`, a real Tailwind class for the banner divider),
     never copy-paste the mock's custom properties verbatim.
Related intent: MILESTONE.md's goal ("a superadmin... experiences a flat, borderless,
  SaaS-professional visual language, grounded in real UI/UX research") + rationale (extends
  `platform-admin-console`, follows `admin-console-ui`'s persona-evidence UDD precedent) +
  the `add` skill's UDD trigger for UI features (design-definition loop before build). No new
  GLOSSARY term at this Ground step (Card `flat`/`soft` naming already resolved + glossaried
  by an earlier task this milestone).
Ground SHA: `006f791` (2026-07-06) — cite symbols above, not bare line numbers; any line ref
  elsewhere is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Console flat/borderless visual pass — apply the already-formalized flat/borderless/
  sharp-cornered token treatment to the real tenant directory + all 5 tenant-detail tabs, visual-
  only, no IA/layout/backend-contract change.
Framings weighed: visual-only skin change reusing the shipped `Card` variant infra + formalized
  tokens, informed by a design-concept Artifact the persona-evidence checklist already ran against
  (chosen) · a from-scratch tenant-detail IA redesign (rejected — MILESTONE.md Scope reserves new
  IA to the 3 sibling tasks) · skip the concept step and edit `.tsx` directly (rejected — the UDD
  loop's own hard rule requires a human-confirmed capture before build for any UI feature).
Design concept: published Artifact https://claude.ai/code/artifact/93f09a35-c58f-4397-a8e6-460aadd2cab0
  (URL changed at v5 — the original link stopped resolving server-side; `mocks/
  console-flat-visual-pass.html` (now actually saved — cited since v1 but not written until v5)
  · `prototypes/console-flat-visual-pass.json` · DESIGN.md's `platform-console-flat-redesign`
  row, task-level sub-bullet) — now at v5 (label `contrast-and-grain-v5`) after 4 redirect/polish
  rounds in place: v2 ("tech/space/modern/flat...white luxury" — near-white canvas, hairline
  separators, mono-for-data) → v3 ("look boring" — dark nav rail, hero-scale type, bigger
  signature motif, decisive accent, atmosphere) → v4 ("polish more :D" — rail icons+identity
  footer, sliding tab indicator, budget usage-bar+trend chip, motion under
  `prefers-reduced-motion`) → v5 (screenshot + "increase contract [contrast] ... grain noise with
  background lines as luxury texture" — stronger current-plan card contrast, engraved hairlines +
  whole-page film-grain texture). Full breakdowns in DESIGN.md's four dated "VISUAL
  redirect/POLISH" sub-bullets. NOT YET CONFIRMED at any round.
Must:
<must>
  - M1: `Card variant="flat"` on: PlatformTenantDirectory's wrapping Card (soft→flat),
    PlatformConfigTab's 2 Cards, PlatformKeysTab's Card, the shared `PlanCard` (consumed by both
    PlatformPlanCatalog and PlatformPlanTab).
  - M2: `StatCard` (`components/ui/stat-card.tsx`) gains an additive `variant?: CardProps["variant"]`
    passthrough (default omitted — every existing caller elsewhere stays byte-identical);
    PlatformBudgetTab's 2 StatCard instances pass `variant="flat"`.
  - M3: `PlatformSafetyBanner` AND `ImpersonationBanner` both replace any accent-colored line with
    the plain neutral divider decided this milestone (reuses `hairline-strong`/an equivalent
    already-shipped border token — exact name pinned at contract) — no amber/accent line on either.
  - M4: the current-assigned `PlanCard` renders `selected-border` (Classic Blue, via
    `border-primary`) alongside its existing "Current plan" badge — together, never the border alone.
  - M5: Button/Input/Badge instances WITHIN these 6 screens get a page-local `flat-control`/
    `flat-tag` radius override via className; `components/ui/button.tsx`/`input.tsx`/`badge.tsx`
    themselves are NOT modified — every other dashboard page's controls stay byte-identical.
  - M6: the 3 existing confirm dialogs (Keys revoke, Members impersonate, Plan remove) are NOT
    restyled — unchanged `rounded-lg`/`shadow-lg`/`border-border`, exactly as shipped.
  - M7: `Table`/`DataTable`, and every page outside `components/platform/*`, stay byte-identical —
    Aurora's existing rounded/shadowed treatment is untouched everywhere else.
</must>
Reject:
<reject>
  - restyle the 3 confirm dialogs too -> out of this task's Must (M6); a change request against
    the frozen contract if raised after freeze
  - touch the Tier-3 kind/plan filter, bulk actions, or any new IA -> "not in scope" (MILESTONE.md
    Out) — belongs to a different task or was explicitly not recommended
  - change Button/Input/Badge's SHARED default radius dashboard-wide -> "not in scope" — breaks
    the "Layered alongside Aurora" invariant every other page depends on
</reject>
After:
<after>
  - a superadmin viewing the tenant directory or any tenant-detail tab sees the flat/borderless/
    sharp visual treatment end-to-end across all 6 screens; every other dashboard page's existing
    Aurora treatment is byte-identical to before this task
  - `StatCard`'s new `variant` prop exists, covered by a test asserting every OTHER existing
    dashboard-wide caller still renders its pre-existing classes unchanged
  - both banners render the identical neutral-divider treatment
  - the current-plan `PlanCard` renders both `selected-border` and the "Current plan" badge
    together — a test asserts both, never border alone
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ decision #5 (Button/Input/Badge page-local radius override) — lowest confidence because it's
    the one Ground-time call most likely to read as an unwanted inconsistency rather than a
    deliberate signature, not yet reacted to by Tin; if wrong: revert per-instance className
    strings only, no structural rework, contained cost.
  - [ ] decision #2 (both banners quieted, not just PlatformSafetyBanner) — confirm or deny at
    design-confirm; if wrong, ImpersonationBanner reverts trivially (single className revert).
  - [ ] decision #3 (the 3 dialogs stay elevated/unchanged) — confirm or deny; if wrong, applying
    the flat treatment to 3 dialog `div`s is a small, contained follow-up.
  - [ ] decision #4 (PlanCard spillover onto the Screen-1 catalog page accepted) — confirm or
    deny; if Tin wants Screen-1 excluded, that call site would need an explicit `variant="soft"`
    opt-out, a small named deviation.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: <short name>   # <Must/Reject item this covers, e.g. M1 or R1>
  Given <starting situation>
  When <action>
  Then <expected result>
  And <what must remain unchanged>   # required for every rejection
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
<METHOD> <path>   body: { <fields> }
  200 -> { <success fields> }
  4xx -> { error: "<code>" | "<code>" }
Schema: <tables/fields touched, and access pattern>
```

Glossary deltas: <new domain term(s) this task introduces, `Term: definition` — or "none">
Status: DRAFT
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY (new
     terms declared as a Glossary delta) + the bundle's lowest-confidence flag was surfaced at
     the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features>

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
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
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. The Advisor 3-lens verdict and the Refute-read verdict are both measured by `add.py audit` (`advisor_verdict_unrecorded` · `refute_unrecorded`) — neither is engine-blocked; a human spot-audit is the backstop for any finding the AI did not surface or record. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
