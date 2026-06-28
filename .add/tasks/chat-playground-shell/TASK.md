# TASK: Chat playground — 3-pane Console shell + design system

slug: chat-playground-shell · created: 2026-06-28 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): the chat surface `apps/dashboard/components/chat/` + its route `app/(app)/app/chat/page.tsx`. TODAY (reconnaissance baseline = detached `.add/tasks/ai-feature-pages-redesign/TASK.md` §0 + `tmp/ai-feature-build-spec.md`): `ChatWorkspace.tsx({defaultModel="openai/gpt-4o"})` is a 2-pane immersive shell — outer `flex h-full min-h-0 flex-row bg-muted/30`, a `ChatHistorySidebar` (w-64, aria-label "Conversation history"), then a main column = thin toolbar header (h1 "Chat" + CostReadout + ModelPicker) + thread (`role=log aria-live=polite aria-label="Conversation"`, `data-role={role}` turns) + composer form. Children: MessageMarkdown · ModelPicker · ModelControls (System prompt/Temperature/Web search disclosure) · ChatHistorySidebar · CostReadout. Streaming `useChatStream` POST `/api/gw/v1/chat/completions` (SSE delta.content + usage frame; AbortController). Conversations: bffGet/Post `/v1/conversations`(+`/:id`,`/:id/messages`), bffDelete `/v1/conversations/:id` (NO rename/PATCH today). No React Query. THIS TASK = the SHELL only: a 3-pane Console layout (sessions · conversation · parameters/inspector panel) + the Playground design system + the design-confirm. It RESHAPES the layout and OWNS the frozen shell the sibling tasks (parameters·tools·attachments·metadata·conversation-mgmt) consume; it does NOT yet wire the new params/tools (those are the sibling tasks).
Context (working folder): `.add/milestones/chat-playground/MILESTONE.md` (this milestone; the 6-task breakdown + shared decisions: feature-rebuild-not-byte-identical, pass-through-first, design-before-code, four-state, design-for-failure) · the shipped v54 Aurora design system (`tmp/governance-build-spec.md` PageHeader/states.tsx/tokens; Classic-Blue palette in `tmp/governance-mocks/keys.html`) · the REJECTED thin mocks (`.add/design/captures/aifeature-*.png`) — the anti-pattern this must beat · reference feel: OpenAI Playground / Anthropic Console (Tin's pick — dense, parameter-rich, working-surface).
Honors (patterns / conventions): PROJECT.md UDD invariants — design-before-code (a captured Console-grade screen Tin confirms BEFORE build) · four UI states from states.tsx · WCAG 2.2 AA · one h1 · 3-layer DTCG tokens fail-closed (no raw hex/px). CONVENTIONS.md — decorative icons aria-hidden; role=status/alert; thread stays role=log live region. Milestone shared decisions — feature rebuild (chat tests EVOLVE with new contracts via TDD, never weakened); design-for-failure (AbortController + cancel; no retry-storm).
Anchors the contract cites: `ChatWorkspace` (reshaped to 3-pane) · the new shell regions (sessions rail · conversation column · parameters/inspector panel) · `PageHeader`/compact-toolbar treatment for the conversation top bar · `states.tsx`(Loading/Empty/ErrorState/Success) · the preserved streaming seam `useChatStream` + POST `/api/gw/v1/chat/completions` + conversations seams · the thread `role=log` + `data-role` turn structure carried into the new center pane.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Chat playground — 3-pane Console shell + design system
Framings weighed: 3-pane sessions|conversation|inspector (chosen — Tin-approved captured design `.add/design/captures/chat-playground.png`) · keep today's 2-pane sidebar+main · inspector-as-slide-over drawer
Must:
<must>
  - `/app/chat` renders the Tin-approved 3-pane Console layout, full-height: a SESSIONS rail (left), a CONVERSATION column (center), a PARAMETERS/INSPECTOR panel (right).
  - The conversation column keeps the streaming thread as a `role=log` `aria-live=polite` live region with `data-role={role}` turns, the composer (Message textbox / Send / Stop), and a compact top bar holding the conversation title, the model selector, and the running cost (CostReadout).
  - The existing model controls (System prompt / Temperature / Web search) MOVE into the inspector's Parameters tab; the inspector scaffolds the Parameters · Tools · Code tabs (the sibling tasks wire the new sampling params, tools, and code-view — this task owns the shell + the frozen region anatomy they consume).
  - All existing chat behaviour is PRESERVED, only relocated: `useChatStream` POST `/api/gw/v1/chat/completions` streaming + AbortController; conversation load/save/delete seams; CostReadout; Copy/Regenerate; MessageMarkdown rendering; ModelPicker.
  - Four UI states present via states.tsx: empty thread (starter chips), streaming/loading, error, populated success.
  - Exactly one h1; the thread stays a `role=log` region; decorative icons `aria-hidden`; tokens only (no raw hex/px); responsive — the three panes degrade gracefully on narrow widths (rails collapse, conversation never starved).
</must>
Reject:
<reject>
  - breaking a preserved chat seam or the `role=log` / `data-role` turn structure to achieve the layout -> "seam_broken"
  - the inspector or sessions rail starving the conversation below a usable width with no responsive fallback -> "layout_regressed"
  - silently dropping a control in the move (System prompt / Temperature / Web search) -> "control_lost"
</reject>
After:
<after>
  - `/app/chat` is the approved 3-pane shell; the chat suite is green by co-evolution (controls relocated by navigation, behaviour preserved — never weakened); tsc + eslint + add.py check clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ moving ModelControls (System prompt / Temperature / Web search) into the inspector keeps their existing aria-labels reachable by the chat-model-controls suite — lowest confidence because that suite queries by role+name and the controls now live behind a Parameters tab; if wrong: co-evolve by navigation (open the Parameters tab before the query), NEVER by weakening the assertion.
  - [ ] the sessions rail is the existing ChatHistorySidebar RESTYLED (aria-label "Conversation history" preserved), not a rewrite — confirm its props/seams carry over.
  - [ ] the top bar's title-edit / Fork / Export / View-code affordances are SCAFFOLDS in this task; their real behaviour ships in sibling tasks (chat-conversation-mgmt, chat-run-metadata-cost) — confirm no test asserts them as functional here.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Three-pane Console layout renders
  Given the chat page has loaded
  When I look at /app/chat
  Then a sessions rail, a conversation column, and a parameters/inspector panel are present, full-height
  And exactly one h1 is on the page

Scenario: Streaming thread preserved in the center pane
  Given a conversation with messages
  When the assistant streams a reply
  Then the thread is a role=log aria-live region whose turns carry data-role={role}
  And useChatStream still POSTs /api/gw/v1/chat/completions (seam unchanged)

Scenario: Existing controls relocated to the inspector
  Given the playground shell
  When I open the inspector Parameters tab
  Then System prompt, Temperature, and Web search controls are present and functional
  And their aria-labels are unchanged (reached by navigation, not renamed)

Scenario: Top bar holds model + running cost
  Given the conversation column
  When I read its top bar
  Then the model selector and the running cost (CostReadout, testid cost-readout) are present
  And the cost seam/value is computed exactly as before

Scenario: Four states present
  Given the conversation pane
  When it is empty, then streaming, then errored, then populated
  Then each renders through a states.tsx primitive (empty with starter chips / loading / role=alert / populated thread)
  And no state throws or renders blank

Scenario: A preserved seam is never broken (rejection)
  Given the relocated controls and thread
  When the chat suite runs
  Then it passes by reaching the relocated control as rendered (navigation)
  And no chat seam, role=log, or data-role structure was removed or renamed

Scenario: Layout never starves the conversation (rejection)
  Given a narrow viewport
  When the shell renders
  Then the rails collapse/adapt and the conversation stays usable
  And no pane hard-overlaps or hides the composer

Scenario: No control is lost in the move (rejection)
  Given System prompt, Temperature, Web search existed before
  When the shell ships
  Then all three are still present (in the inspector)
  And each keeps its existing behaviour and aria-label
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
UI STRUCTURAL CONTRACT — the frozen 3-pane shell the sibling tasks consume.
Reference design (approved): .add/design/captures/chat-playground.png

LAYOUT (/app/chat, full-height):
  [ app nav ] [ SESSIONS rail ] [ CONVERSATION column ] [ INSPECTOR panel ]
  - SESSIONS rail: the existing ChatHistorySidebar restyled. aria-label "Conversation history" PRESERVED.
    Header (Conversations + New + search) · scrollable session list (title + model badge + time + active state).
  - CONVERSATION column (flex, fills height):
      top bar  : conversation title (editable affordance — scaffold) · ModelPicker (slot model-picker, Model combobox) ·
                 Fork/Export/View-code (scaffold buttons) · CostReadout (testid cost-readout, slot session-cost) running-cost chip.
      thread   : role=log aria-live=polite aria-label="Conversation"; each turn a div with data-role={role};
                 system prompt shown as a pinned block; MessageMarkdown for content; per-turn footer
                 (model · finish_reason · tokens · latency · cost — values wired by chat-run-metadata-cost sibling);
                 hover actions Copy (aria-label "Copy") / Regenerate (aria-label "Regenerate").
      composer : Textarea aria-label "Message" · Send (aria-label "Send") / Stop (aria-label "Stop") · attach button (scaffold).
  - INSPECTOR panel (right): tablist Parameters(default) · Tools · Code.
      Parameters tab OWNS the relocated controls: System prompt (aria-label "System prompt"),
        Temperature (aria-label "Temperature"), Web search (aria-label "Web search") — MOVED from ModelControls, byte-identical labels;
        plus scaffolded slots for top_p / max_tokens / penalties / seed / stop / response_format (wired by chat-parameters-panel).
      Tools tab: scaffold (wired by chat-tools-functions).  Code tab: scaffold (request/response view — later).

PRESERVED SEAMS (unchanged — relocation only):
  useChatStream → POST /api/gw/v1/chat/completions (SSE delta.content + usage frame; AbortController on stop/unmount).
  Conversations: bffGet/Post /v1/conversations (+ /:id, /:id/messages) · bffDelete /v1/conversations/:id.
  Catalog: bffGet /admin/catalog/models (ModelPicker).  fetch /api/auth/me (initials).
  Frozen hooks carried into the new layout: cost-readout · session-cost · model-picker · role=log Conversation ·
  data-role turns · aria-labels Message/Send/Stop/Copy/Regenerate/Model/System prompt/Temperature/Web search/Conversation history.

INVARIANTS: one h1; thread stays role=log with data-role turns (NO wrapper between a turn and its parent);
  four states via states.tsx; tokens only; responsive (rails collapse, conversation never starved);
  NEW capability (sampling params/tools/code/rename/export) is SCAFFOLDED here, WIRED by the named sibling tasks.
```

Status: FROZEN @ v1 — approved by Tin
Least-sure flag surfaced at freeze: [spec] moving ModelControls (System prompt/Temperature/Web search) behind the inspector Parameters tab — the chat-model-controls suite queries them by role+name, so they'll be reached by navigating to the Parameters tab (co-evolution), never by weakening; cost = a few nav-only test edits. The Tin-approved design (`.add/design/captures/chat-playground.png`) is the visual contract; this is a feature rebuild, so the chat tests evolve WITH it.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% per project (both vitest projects, the standing gate); net structural additions + behavior-preserving co-evolution, zero assertions weakened.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_three_pane_layout_renders: render ChatWorkspace → a sessions region, a conversation region (role=log), and an inspector region (the Parameters tablist) all present; exactly one h1.
  - test_thread_is_log_with_data_role: stream a reply → thread is role=log aria-live region; an assistant turn carries data-role="assistant"; useChatStream still POSTs /api/gw/v1/chat/completions.
  - test_controls_in_inspector_parameters: open the Parameters tab → System prompt / Temperature / Web search present + functional, aria-labels unchanged.
  - test_topbar_model_and_cost: top bar shows the Model combobox + CostReadout (testid cost-readout); cost value computed as before.
  - test_four_states: empty (starter chips) / streaming / role=alert error / populated — each via states.tsx.
  - test_seam_not_broken (rejection): the frozen hook union (cost-readout/model-picker/role=log Conversation/data-role/Message/Send/Stop/Copy/Regenerate/System prompt/Temperature/Web search/Conversation history) is byte-present after the reshape.
  - test_no_control_lost (rejection): System prompt + Temperature + Web search all still present (in the inspector) with their behaviour.
  NEW red suite asserts the shell structure; the existing chat suites co-evolve by NAVIGATION (open the Parameters tab before a relocated-control query) — behaviour-preserving, never weakened.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-playground-shell.test.tsx` `apps/dashboard/tests-bff/chat-workspace-page.test.tsx` `apps/dashboard/tests-bff/chat-cost-readout.test.tsx` `apps/dashboard/tests-bff/chat-model-controls.test.tsx` `apps/dashboard/tests-bff/chat-history.test.tsx` `apps/dashboard/tests-bff/chat-websearch-toggle.test.tsx` `apps/dashboard/tests-bff/chat-visual-parity.test.tsx` `apps/dashboard/tests/chat-message-markdown.test.tsx` · MUST run red (missing implementation) before Build.
<!-- NOTE (tests phase): the new red suite was placed in tests-bff/ (not tests/design-system/) because two of its structural cases stream a reply through useChatStream → POST /api/gw/v1/chat/completions, which only the BFF MSW server (tests-bff/mocks) handles; the legacy tests/ project has no /api/gw handlers. §3 contract unchanged. -->

<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/chat/` `apps/dashboard/tests-bff/chat-playground-shell.test.tsx` `apps/dashboard/tests-bff/chat-workspace-page.test.tsx` `apps/dashboard/tests-bff/chat-cost-readout.test.tsx` `apps/dashboard/tests-bff/chat-model-controls.test.tsx` `apps/dashboard/tests-bff/chat-history.test.tsx` `apps/dashboard/tests-bff/chat-websearch-toggle.test.tsx` `apps/dashboard/tests-bff/chat-visual-parity.test.tsx` `apps/dashboard/tests/chat-message-markdown.test.tsx`
Strategy (ordered batches): 1. NEW red suite `chat-playground-shell.test.tsx` (the 7 structural cases) → red. 2. Build the 3-pane layout scaffold in `ChatWorkspace.tsx` (sessions | conversation | inspector) + an `InspectorPanel` (Parameters/Tools/Code tabs) + a `ConversationTopBar` (title scaffold · ModelPicker · Fork/Export/View-code scaffold · CostReadout chip), reusing the existing thread + composer + ChatHistorySidebar. 3. MOVE ModelControls (System prompt/Temperature/Web search) into the Parameters tab — same components, same aria-labels, same state wiring. 4. Add the four states (empty starter-chips, streaming, error, populated) via states.tsx. 5. Co-evolve the chat suites by NAVIGATION (open Parameters tab before relocated-control queries) — re-cross `add.py phase build` after any §4-declared test edit. 6. green full suite + tsc + eslint + add.py check; capture the real built page at verify.
Known-problem fixes: data-role traversal → keep turns as direct children of the thread log (no wrapper between a turn and its parent) · SSE/Abort lifecycle → reshape layout WITHOUT remounting the streaming subtree (no lazy-mounted tab around the thread) · cost-readout + model-picker → relocate into the top bar but keep testid/slot + Model combobox reachable · ModelControls relocation → the chat-model-controls suite reaches them via the Parameters tab (nav), never weaken · responsive → rails use breakpoint classes (collapse on narrow), conversation flex-1 never starved · tokens only (no raw hex/px; `add.py check`).
Strategy actually used: As planned, with three discoveries. (a) The new red suite was placed in tests-bff/ (not tests/design-system/) because two cases stream a reply and only the BFF MSW server handles /api/gw — §4/§5 paths updated pre-build, §3 untouched. (b) ModelControls was refactored to render its three fields DIRECTLY (the v40 "Model settings" disclosure removed) since the inspector's Parameters tab is now the container; the relocation surfaced a 2nd textbox (System prompt) that forced two honest co-evolutions beyond the planned nav-only ones — name-scoping the bare `getByRole("textbox")` in chat-workspace-page (×2) and scoping chat-visual-parity's token-estimate query past the inspector's new "Max tokens" label. (c) The optimistic top-bar title (slug of the first message) was REVERTED — it echoed the message text and collided with the user bubble; the title is a scaffold (per §1 assumption), so it stays "New chat" for a fresh chat and reflects the conversation title only on resume. Inspector tabs use the shadcn Tabs primitive (role=tablist/tab/tabpanel); the thread lives OUTSIDE any tab so the streaming subtree is never remounted. Real built page captured via a throwaway public route + the dev server (`.add/design/captures/chat-playground-built.png`), then removed.
Safety rule (feature-specific): every preserved chat seam (useChatStream + /v1/chat/completions, conversations seams, CostReadout, ModelPicker) and every frozen hook (testids/slots/role=log/data-role/aria-labels) stays BYTE-IDENTICAL through the reshape; relocated controls are reached by navigation, never by weakening; the streaming subtree is never remounted by the layout.
Code lives in: `apps/dashboard/components/chat`
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

- [x] all tests pass — 801/801 both vitest projects (legacy + bff); the 7-case chat-playground-shell suite + all 6 co-evolved chat suites green.
- [x] coverage did not decrease — net structural additions + 3 new components fully referenced; the ≥80% per-project gate held (full run green).
- [x] no test or contract was altered during build — §3 still `FROZEN @ v1 — approved by Tin` (refute-read probe 5 confirmed); test edits are nav/specificity co-evolutions, not weakenings (probe 1).
- [x] the green was EARNED, not gamed — refute-read VERDICT: EARNED (agent a29ce5f3059a077b8); thread is outside any Tabs (no SSE remount), assertions are meaningful DOM structure, no overfit.
- [x] concurrency / timing — streaming AbortController + cancel preserved byte-identical (useChatStream untouched); inspector tabs don't wrap the streaming subtree → no mid-stream remount.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps (shadcn Tabs + lucide already present); allow-list only; no secrets touched.
- [x] layering & dependencies follow CONVENTIONS.md — presentation-only reshape; state stays lifted in ChatWorkspace; decorative icons aria-hidden; tokens only (add.py tokens.json layer-valid PASS).
- [ ] a person reviewed and approved the change — pending Tin's gate sign-off (design already confirmed).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `/app/chat` renders three side-by-side panes (sessions rail · conversation column · inspector), full-height, with exactly one h1 — confirmed by `.add/design/captures/chat-playground-built.png` (real built page, live Tailwind) + test_three_pane_layout_renders green.
- [x] The inspector is a Parameters(default)·Tools·Code tablist; the Parameters tab shows System prompt / Temperature / Web search with byte-identical aria-labels — confirmed by test_controls_in_inspector_parameters + the co-evolved chat-model-controls/websearch suites green (reached by tab nav, not weakened); visible in the built capture.
- [x] Every preserved seam survives the reshape — role=log `Conversation` thread with data-role turns, cost-readout testid + model-picker/session-cost slots in the top bar, useChatStream still POSTs /api/gw/v1/chat/completions, Copy/Regenerate per assistant turn — confirmed by test_seam_not_broken + the full chat suite green (refute-read probe 2 verified each seam in source).
- [x] Four UI states render via states.tsx (empty starter-chips / streaming Stop / role=alert error / populated) and the streaming subtree is never remounted by the layout — confirmed by test_four_states green + chat-workspace-page SSE/abort cases still green (thread outside any Tabs).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `InspectorPanel` + `ConversationTopBar` imported and rendered in ChatWorkspace.tsx; `ModelControls` rendered by InspectorPanel; `Paperclip` used in the composer attach scaffold; `activeTitle` read by ConversationTopBar's title prop. tsc clean.
- [x] DEAD-CODE (code) — no orphan: removed the old header/ModelControls-disclosure; `slug` still used by createConversation; `setActiveTitle` set in handleNew/handleSelect. eslint full = 0 errors (1 pre-existing unrelated warning in data-table.tsx).
- [x] SEMANTIC (prose / non-code) — read in full: refute-read report (5 probes, EARNED) + the built capture image — confirmed 3-pane structure, controls relocated, seams intact, honest gateway-less degrade.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent-a29ce5f3059a077b8 · adversarially checked: each co-evolved test edit for weakening (all behavior-preserving nav/specificity); every preserved seam present in source (role=log/data-role/testids/slots/aria-labels); all three controls present + functional in the inspector; new suite asserts real DOM structure (not vacuous); thread outside any Tabs (no SSE remount); §3 still FROZEN @ v1 unchanged. Two non-blocking notes recorded as §7 spec deltas (pre-existing centering div inside the log; inspector hidden below xl with no re-open toggle).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: AI auto-gate (autonomy: auto) — Tin sign-off pending · date: 2026-06-28

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): seam_broken rate (chat suite red) · layout_regressed (narrow-viewport usability) · control_lost (a relocated control missing)

### Decisions (ADR)
- [AI] specify — chose 3-pane sessions|conversation|inspector; rejected keep today's 2-pane sidebar+main · inspector-as-slide-over drawer
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: As planned, with three discoveries. (a) The new red suite was placed in tests-bff/ (not tests/design-system/) because two cases stream a reply and only the BFF MSW server handles /api/gw — §4/§5 paths updated pre-build, §3 untouched. (b) ModelControls was refactored to render its three fields DIRECTLY (the v40 "Model settings" disclosure removed) since the inspector's Parameters tab is now the container; the relocation surfaced a 2nd textbox (System prompt) that forced two honest co-evolutions beyond the planned nav-only ones — name-scoping the bare `getByRole("textbox")` in chat-workspace-page (×2) and scoping chat-visual-parity's token-estimate query past the inspector's new "Max tokens" label. (c) The optimistic top-bar title (slug of the first message) was REVERTED — it echoed the message text and collided with the user bubble; the title is a scaffold (per §1 assumption), so it stays "New chat" for a fresh chat and reflects the conversation title only on resume. Inspector tabs use the shadcn Tabs primitive (role=tablist/tab/tabpanel); the thread lives OUTSIDE any tab so the streaming subtree is never remounted. Real built page captured via a throwaway public route + the dev server (`.add/design/captures/chat-playground-built.png`), then removed.
- [AI] verify — gate PASS (reviewed by AI auto-gate (autonomy: auto) — Tin sign-off pending)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] inspector is `hidden xl:flex` — below 1280px it collapses with NO re-open toggle/drawer, so Parameters/Tools/Code are unreachable on md-width screens (evidence: refute-read probe 4). Satisfies the literal "rails collapse, conversation never starved" Must but a narrow-width inspector drawer is a real UX gap → seed a `chat-responsive-inspector` refinement (or fold into chat-parameters-panel).
- [SPEC · seeded] the thread log keeps a pre-existing centering `<div className="mx-auto …">` between `role=log` and the `data-role` turns; traversal is via `.closest()`/`within()` so data-role is intact, but the §3 "NO wrapper between a turn and its parent" wording is stricter than the v40-inherited DOM (evidence: refute-read probe 2). Reconcile the wording or flatten the wrapper in a later pass.
- [SPEC · open] the per-turn footer (model · finish_reason · tokens · latency · cost) + the Tools/Code tabs + real sampling params (top_p/max_tokens/penalties/seed/stop/response_format) are SCAFFOLDED here — wired by chat-run-metadata-cost / chat-tools-functions / chat-parameters-panel (the named sibling tasks).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
