# TASK: Automatic per-tenant batch grouping

slug: batch-auto-grouping · created: 2026-07-03 · stage: production · risk: high
milestone: v57
autonomy: conservative   <!-- LOWERED from the project default `auto` — this task can change the response
     contract of a live, already-integrated synchronous API (POST /v1/chat/completions) for real
     tenants; a human gate at verify is required, not an auto-PASS. Raise back to `auto` only if
     specify resolves toward a design that provably never touches the sync path (see Issues/Risks). -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/api/router.py:completions` (lines 33-99) — THE existing
    synchronous `POST /v1/chat/completions` handler: non-streaming returns the upstream JSON body
    verbatim, streaming returns an SSE `StreamingResponse` — both fully resolved within the same HTTP
    request/response cycle today, no existing "queue and return a reference" code path. This is the
    exact function the milestone's "stays byte-identical" guarantee is about, and the anchor any
    interception point (if that's where specify lands) has to attach to or explicitly avoid.
  - `apps/gateway/src/gateway/batches/infrastructure/repository.py:BatchJobRepository` — create/get/
    list_for_tenant/status_counts/set_in_progress/set_failed/increment_retry/list_nonterminal_ids.
    Whatever this task builds is expected to feed jobs INTO this store (batch-job-store stays the
    underlying processor) rather than reinvent it.
  - `apps/gateway/src/gateway/batches/api/router.py:batch_router` — the existing EXPLICIT `POST
    /v1/batches` submission path (batch-job-store, merged, gate=PASS). Per Tin's confirmed answer,
    this task is NOT that path and does not resemble it — it's a different, automatic trigger. Whether
    it calls into the same repository underneath (likely) or needs its own schema is open at specify.
  - `apps/gateway/src/gateway/tenants/api/cache_router.py:cache_router` — the per-tenant boolean-
    column toggle precedent (`tenants.cache_enabled`); the shape a new tenant policy control would
    mirror IF specify lands on a simple on/off toggle (not yet decided — depends which fork wins,
    see Issues/Risks).

Context (working folder):
  - `.add/milestones/v57/MILESTONE.md` — the "SCOPE CHANGE (Tin, 2026-07-03, correction)" +
    "UNRESOLVED" note just added there; this task's #1 grounding fact is that document's own stated
    conflict, not something derived independently here.
  - `.add/tasks/batch-dashboard-surface/TASK.md` — sibling task (in-flight, being narrowed to a
    READ-ONLY stats page). No hard dependency either direction, but that page's "volume" and "status
    breakdown" numbers will reflect whatever this task's mechanism produces once it lands — stay
    compatible with `BatchJobRepository.status_counts` and friends, don't invent a parallel data model
    that page would then need a second query path for.

Honors (patterns / conventions):
  - OPT-IN / ADDITIVE ONLY (MILESTONE.md's own Shared decision) — "every new knob ships default-off."
  - TENANT ISOLATION (MILESTONE.md's own Shared decision) — a batch job is submitted under ONE
    tenant's credential only; applies here since this task's output is still a batch-job-store job.
  - DESIGN-FOR-FAILURE (MILESTONE.md's own Shared decision + the user's own global CLAUDE.md rule:
    "MUST design for failure: timeouts, retries, circuit breakers, rollback strategy in IO request") —
    any new outbound IO path this task introduces (a collection timer, a flush-to-batch call, etc.)
    needs the same timeout+bounded-retry+circuit-breaker treatment as every existing upstream call —
    and, uniquely to this task, a rollback story for what happens to a request already in flight if
    the hand-off into async processing itself fails partway.

Anchors the contract cites: TBD at specify — contingent entirely on which fork below resolves; do not
  pre-guess a contract shape while the trigger mechanism itself is undecided.

Issues/Risks (→ feed §1):
  - ⚠ TOP, UNRESOLVED (carried from MILESTONE.md, not decided here): the milestone's own Scope/Out
    line says `/v1/chat/completions` "stays byte-identical" for any tenant, no exception (confirmed at
    intake 2026-07-02, before today). Tin separately confirmed (AskUserQuestion) the trigger is
    "automatic grouping of ordinary requests" via a "per-tenant policy" (not the already-shipped
    explicit `POST /v1/batches` submission). Automatic grouping only means something if SOME request's
    synchronous behavior changes for an opted-in tenant — which contradicts "byte-identical, no
    exception" as literally written. A follow-up AskUserQuestion asking Tin to pick between (a)
    opt-in amends byte-identical to "...for any tenant that hasn't opted in" (sync becomes
    async-shaped only for a tenant that deliberately enables the policy) vs. (b) byte-identical stays
    absolute and the policy instead governs a genuinely separate, always-async traffic path (not
    literal `/v1/chat/completions` traffic) — TIMED OUT TWICE with no reply (once after Tin explicitly
    asked to be re-asked), so neither is picked. Proceeding per AUTO MODE fallback means: NOT deciding
    either way — this stays the task's top open question, to resolve at THIS task's own specify phase
    (Framings weighed) before any Must/Reject/After is written, not guessed at in §0.
  - Supporting evidence for whichever fork wins (found this session, not a decision): NO per-tenant
    webhook/callback delivery mechanism exists anywhere in this codebase today — the only
    `WebhookSink` (`apps/gateway/src/gateway/alerting/domain/ports.py`) is scoped to the `alerting`
    bounded context (operator-facing ops alerts), not tenant-facing result delivery. This makes
    reconciliation (a) CHEAPER than it might look: if a request becomes async for an opted-in tenant,
    result delivery can reuse `GET /v1/batches/{id}` (already built, already polled by nothing new)
    rather than requiring a fresh webhook-push system — the caller's integration changes from "await
    the sync response" to "get a job reference back, then poll the existing endpoint," zero new
    delivery infra. A push/webhook delivery model, if ever wanted instead of polling, would be a
    materially bigger, fresh build. Not a reason to pick (a) over (b) — just a real cost input.
  - Named for completeness, likely NOT what's meant: a third pattern exists that changes nothing about
    sync behavior — short-window request coalescing (hold concurrent calls a few hundred ms, merge
    into one upstream call, return each caller their own slice, still fully synchronous). It does NOT
    reach a provider's native Batch API (OpenAI `/v1/batches` / Anthropic `/v1/messages/batches`, both
    ~24h SLA) and so cannot deliver the ~50% batch-discount this whole milestone exists for — flagged
    here so it isn't quietly reached for as a compromise that satisfies "sync never changes" while
    silently abandoning the milestone's actual cost-savings goal.
  - Second-order risk once the fork resolves: `completions` (proxy/api/router.py) is the ONLY place in
    the codebase that terminates a chat-completion HTTP request today. If fork (a) wins, the
    interception point has to live there or in something upstream of it (middleware/dependency), and
    diverting a request into an async flow mid-request is itself a NEW failure mode — what happens to
    the caller if the hand-off into batch processing fails after the sync path has already been
    abandoned? No existing code answers this; it is not an edge case to patch later, it is core to
    whichever design gets chosen.
  - `risk: high` + `autonomy: conservative` set on this task's header (see above) given the blast
    radius (a live, already-integrated production API's contract) — a human gate at verify regardless
    of how specify resolves, not an auto-PASS.

Related intent:
  - v57 MILESTONE.md's Scope/Out line ("any change to the existing synchronous /v1/chat/completions
    behavior (stays byte-identical)") — confirmed at intake 2026-07-02, now in tension with this
    task's own reason for existing (see Issues/Risks).
  - The original course-correction (Tin, 2026-07-03): "we no need a playground for batch request, we
    just provide for admin to view statistics of their tenant's user request then system will process
    batch by group user's request as batch."
  - The two confirmed AskUserQuestion answers this session: trigger mechanism = "Automatic grouping of
    ordinary requests" (over "the already-shipped explicit backend"); eligibility signal = "Per-tenant
    policy" (over "a per-request flag/param" and "a separate async endpoint").
  - GLOSSARY: batch_job, batch line item (already declared by batch-job-store) — this task will likely
    add a new term for whatever the eligibility/policy construct ends up being called; not named yet.

Ground SHA: `e897cf0` (current HEAD at ground time — no commits since batch-job-store merged; any
  symbol/line reference here is "as of" this commit).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: <name>
Framings weighed: <chosen> (chosen) · <alternative> · <alternative>
Must:
<must>
  - <required behavior>
</must>
Reject:
<reject>
  - <bad input / situation> -> "<error_code>"
</reject>
After:
<after>
  - <state that is true once it succeeds>
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ <the one assumption most likely to be wrong> — lowest confidence because <why>; if wrong: <cost>
  - [ ] <next assumption, ranked> — confirm or deny; never carry an open one forward
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
