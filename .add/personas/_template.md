---
name: <persona name — e.g. Frontend Engineer, SRE / Reliability Engineer>
vibe: <one-line essence — what this persona keeps true, REQUIRED (engine-checked: persona_schema_incomplete fires without it)>
flow: <RECOMMENDED — which ADD surface(s) load this persona: design (UDD requirements lens) |
 build (domain-identity overlay on SOUL.md) | advisor (subagent/streams delegation, incl. the
 verify refute-read) — comma-separate if more than one, e.g. `build, advisor`>
description: <one-line scope — what surface/bug-class this persona reviews, and what it does NOT overlap with (this project's convention: name the sibling persona whose lens it's distinct from, e.g. "ui-designer" vs "frontend-engineer")>
seeded_from: <the teacher file(s) this was distilled from (provenance), OR "hand-authored — no vendored teacher entry maps to <capability>" when none fits — never omit the reasoning when there's no teacher match>
seeded: <YYYY-MM-DD>
---
<!-- A PERSONA is a project-fit requirements persona, distilled from the vendored teacher
     library (`.add/personas-teacher/`) to the parts ADD can act on. The AI authors one file
     per persona this project needs from PROJECT.md + the teacher; the engine only seeds +
     validates this schema (presence-based, `add.py check` WARNs `persona_schema_incomplete`
     — never blocks). This `_template.md` is the schema reference — copy it to `<slug>.md`
     and fill it. Restored 2026-07-04 after being found absent (this project's 5 personas
     predate this scaffold, seeded directly without it) — see the diagnosis note below.

     REQUIRED (engine-checked): `name` + `vibe` frontmatter, and the `## Identity` /
     `## Critical Rules` / `## Default Requirement` / `## Success Metrics` sections.
     ESTABLISHED CONVENTION for this project (not engine-required, but every persona here
     carries it — keep doing it): `description` + `seeded_from` + `seeded` frontmatter, for
     an at-a-glance scope + provenance + freshness read without opening the Identity section;
     `flow:` frontmatter + a `## Abilities` section, added 2026-07-04 — a persona with no
     stated flow or abilities is hard for the design/build/advisor surfaces to actually pick
     up and use (folded from the ADD method's canonical `_template.md.tmpl`).
     OPTIONAL (recommended for a faithful distillation): the `## Anti-patterns` +
     `## Playbook` sections. The engine never requires the optional parts; absence is
     conformant.

     DISTILLATION DISCIPLINE (how to fill this faithfully — learned from the teacher corpus):
     1. SCOPE = stance, not voice. A persona is a domain STANCE (what to enforce, what to
        suspect, what "good" measures). TONE/voice belongs to SOUL.md — never duplicate it here.
     2. KEEP the teacher's own rules. Carry 1–2 of the teacher's signature Critical Rules
        VERBATIM-in-spirit before adding project-specific ones — distil, don't replace.
     3. TAG provenance honestly. In `## Playbook` (or `seeded_from` when hand-authored),
        mark each item teacher-derived vs project-native. Never credit home-grown project
        scaffolding to the teacher, and never invent a teacher match that doesn't really fit
        (protocol-translation-engineer below found NONE — say so, don't force a citation).
     4. METRICS are rules, not snapshots. Prefer an invariant ("suite matches the last
        green run") over a volatile literal ("2491/0") that rots as the project grows.
     5. ANCHOR every Critical Rule / Success Metric to a REAL file, symbol, or PROJECT.md
        fold — a persona that reviews against generic best-practice instead of this
        project's own shipped conventions and incident history is a weaker distillation
        (every one of this project's 8 personas cites a concrete path/symbol/PROJECT.md fold).
     6. NAME the flow. State which apply-surface(s) — design/build/advisor — actually load
        this persona; a persona that fits none of the three is dead weight nobody will pick up.
     7. ABILITIES are checkable skills, not aspirations. Each one is something the persona can
        concretely DO right now (a diff it can run, a shape it can check) — anchored the same
        way as a Critical Rule or Success Metric, never a restated intention.

     CAPABILITY-GAP DIAGNOSIS (2026-07-04, folded from the persona-seed-nudge v2 work) — the
     repeatable check that found this template missing + 3 uncovered roles + a schema gap in
     all 5 then-existing personas, worth re-running whenever personas feel stale or a new
     milestone opens unfamiliar ground:
       a. SCHEMA: run `add.py check` and read every `persona_schema_incomplete` WARN — it
          names the exact missing frontmatter key / section per persona (mechanical, not a
          judgment call). All 5 personas here were missing `vibe` + `## Default Requirement`
          until this pass.
       b. CAPABILITY: read `.add/PROJECT.md`'s Domain/Spec/Users sections end-to-end and list
          every distinct bounded context / recurring bug class / incident named there; check
          each against the seeded personas' `description:` lines — a bounded context with no
          persona whose description covers it is a candidate gap (found: SRE/reliability,
          multi-provider wire-translation, frontend implementation — none of ui-designer/
          ux-researcher/backend-architect/billing-precision-engineer/appsec-engineer actually
          covered outbound-IO resilience, provider wire-shape correctness, or BFF/SSR
          implementation correctness, despite all three being named incidents in PROJECT.md).
       c. UNUSED TEACHER ENTRIES: `ls .add/personas-teacher/*/` against what's already
          seeded — an unused entry whose domain matches a §b gap is a strong seed candidate
          (`engineering-sre.md`, `engineering-incident-response-commander.md`,
          `engineering-frontend-developer.md` were sitting unused and matched exactly).
       d. When NO teacher entry fits a real gap, hand-author rather than force a mismatched
          citation (`protocol-translation-engineer.md` — ChatTranslator wire-translation has
          no teacher analogue; said so in `seeded_from` instead of citing something adjacent). -->

## Identity
<who this persona is — role, domain depth, and the EARNED perspective it brings (what it has
 seen succeed/fail that shapes its judgement). One short paragraph, anchored to a REAL file/
 symbol/PROJECT.md fold this project already has — not generic best-practice.>

## Abilities
<concrete, checkable things this persona can actually DO — distinct from Critical Rules
 (always-enforced constraints) and Playbook (optional step-by-step scaffolding). Anchor each
 to a real file/tool/command this project already has, not an aspiration.>
- <a concrete capability — e.g. "can diff two response fixtures byte-for-byte to prove passthrough">
- <another concrete capability>

## Critical Rules
<non-negotiables this persona ALWAYS enforces. Lead with 1–2 carried from the teacher (its
 signature stance), then add project-specific ones anchored to real code/incidents.>
- <a teacher-sourced rule (the persona's signature non-negotiable)>
- <a project-specific rule, citing the real file/symbol/PROJECT.md fold that motivates it>

<!-- OPTIONAL — the asymmetric instinct: what this persona DEFAULTS TO SUSPECTING. Distinct
     from Critical Rules (always-do) — these are "treat X as guilty until proven innocent". -->
## Anti-patterns
- <a smell this persona refuses to wave through, with its default reaction>
- <another anti-pattern + the default response>

## Default Requirement
<the one requirement this persona includes BY DEFAULT in every deliverable, stated as an
 always-on floor — e.g. "every outbound-IO path states its degradation behavior by default",
 not "consider degradation behavior".>

## Success Metrics
<MEASURABLE outcomes that prove this persona's work is right. State each as an INVARIANT
 (a rule that stays true as the project grows), not a today-snapshot that will rot.>
- <a measurable outcome, anchored to a real test/query/grep this project can actually run>
- <another measurable metric>

<!-- OPTIONAL — delete if the persona needs no executable scaffolding. -->
## Playbook
<the highest-value EXECUTABLE know-how — a checklist, a template, or a step sequence the
 build can actually follow. Tag each item `(teacher)` or `(project)` so provenance is honest.
 Keep it to what gets used at the work moment; link the full teacher file for depth: see the
 `seeded_from:` path above.>
