# TASK: Redesign AI-feature pages (chat·voice·memory·artifacts·vision·video) to the refreshed standard

slug: ai-feature-pages-redesign · created: 2026-06-28 · stage: production
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

Touches (files · symbols · signatures): the six authenticated AI-feature workspaces, each a `"use client"` component behind a thin server route `app/(app)/app/<page>/page.tsx` (metadata + `return <XWorkspace/>`, no props except video's poll default). UNLIKE the governance/monitoring dashboards, ALL SIX share ONE identical immersive shell — outer `flex h-full min-h-0 flex-col bg-muted/30`, a hand-rolled thin TOOLBAR header `header.border-b.border-border.bg-background.px-6.py-3` holding `h1.text-lg.font-semibold` (text = the page name, NO id, NO aria-labelledby, NO region wrapper), then a scrollable `flex-1 overflow-y-auto` body. NONE use PageHeader/Tabs/StatCard/DataTable; NONE use React Query (bare `useEffect` + local state); all data flows through `lib/bff-client` (bffGet/bffPost/bffDelete, same-origin `/api/gw/*`, cookie creds, no client-side JWT). `Success` from states.tsx is unused everywhere.
- `components/chat/ChatWorkspace.tsx({defaultModel="openai/gpt-4o"})` — MOST COMPLEX. Header h1 "Chat" is a flex sibling to CostReadout + ModelPicker (toolbar, not a page-top header). Children MessageMarkdown·ModelPicker·ModelControls·ChatHistorySidebar·CostReadout. Streaming `useChatStream` POST `/api/gw/v1/chat/completions` (SSE delta.content + usage frame; AbortController aborts on unmount/stop). Conversations via lib/conversations: bffGet/Post `/v1/conversations`(+`/:id`,`/:id/messages`), bffDelete `/v1/conversations/:id`. Catalog `bffGet /admin/catalog/models`. `fetch /api/auth/me` for initials. Four-state: Empty(thread)+ErrorState(stream) from states.tsx; sidebar Loading. FROZEN hooks: testid cost-readout · slot session-cost/model-picker · role log+aria-live polite aria-label Conversation · data-role={role} on each turn (tests use `.closest("[data-role='assistant']")`) · aria-labels Message/Send/Stop/Copy/Regenerate/Model/System prompt/Temperature/Web search/Conversation history/New conversation/`Delete {label}` · aria-current on active convo.
- `components/voice/VoicePlayground.tsx()` — two side-by-side section cards STT|TTS. STT `fetch /api/gw/v1/audio/transcriptions` (FormData→`{text}`); TTS `fetch /api/gw/v1/audio/speech` (JSON→blob→objectURL→audio). `useCatalogModels`. Four-state via states.tsx in BOTH panels. FROZEN: testid audio-player · aria-labels Audio file/STT model/Transcription result/Audio result/Text to speak · buttons Transcribe/Speak · datalist ids stt-model-options/tts-model-options · section headings stt-heading "Speech to Text"/tts-heading "Text to Speech".
- `components/memory/MemoryWorkspace.tsx()` — max-w-2xl, three stacked section cards (add·search·list). lib/memory: bffGet `/v1/memories`, bffPost `/v1/memories`(+`/search`), bffDelete `/v1/memories/:id`; `fetchTick` re-fetch trigger. Four-state via states.tsx (list Loading/Error/Empty; search Empty/Error). FROZEN: aria-labels Memory list/`Delete memory: {content}`/Search results/Relevance score/Memory content/Search memories · buttons Add memory/Search/Delete · headings add-memory-heading/search-heading/list-heading · null score MUST render literal "text match".
- `components/artifacts/ArtifactsWorkspace.tsx()` — max-w-2xl, upload + list sections. lib/artifacts: bffGet/Post `/v1/artifacts`, bffDelete `/v1/artifacts/:id`, raw `fetch /api/gw/v1/artifacts/:id`→blob download. FileReader.readAsDataURL→base64. Four-state via states.tsx. FROZEN: testid artifact-file-input · aria-labels Choose file to upload/Artifact list/`Download {name}`/`Delete {name}` · button Upload · headings upload-heading/list-heading · human sizes "1.0 KB"/"512 B" · fileInputRef reset after upload.
- `components/vision/VisionWorkspace.tsx()` — max-w-2xl, form + answer sections. `bffGet /admin/catalog/models` filtered to Gemini-only; askVision `bffPost /v1/chat/completions` (multimodal content parts image_url|video_url, stream:false). FileReader. FROZEN: testid vision-file-input · aria-labels Choose image or video file/Model/Prompt · button Ask · headings vision-heading/answer-heading · 413→literal "media too large" · "No Gemini model available…" gate text. GAPS: no Empty for idle/answer-idle; no answer-area Loading during ask.
- `components/video/VideoWorkspace.tsx({pollIntervalMs=2000})` — max-w-2xl, generate form + job list. lib/video: bffGet/Post `/v1/video/generations`; lib/artifacts downloadArtifact. `setInterval(pollIntervalMs)` polls while any job non-terminal, cleared on all-terminal/unmount; soft poll errors don't stop it. FROZEN: aria-labels Model/Prompt/`Download video for job {id}` · button Generate · datalist video-model-options · headings video-heading/jobs-heading(sr-only "Video jobs") · `pollIntervalMs` test seam · NO_PROVIDER_CODE "no_video_provider_configured" must NOT appear→maps to "Video generation isn't configured yet." · role=status Loading "Loading jobs…". GAPS: zero-jobs uses hand-rolled p not Empty; poll error hand-rolled p role=alert not ErrorState.
Context (working folder): `.add/milestones/v54/MILESTONE.md` (task `ai-feature-pages-redesign`; deps aurora-polish-tokens + responsive-app-shell DONE; exit criterion "each handles loading/empty/error/success") · the shipped monitoring + governance redesigns (PageHeader precedent, `tmp/{monitoring,governance}-build-spec.md`) · `apps/dashboard/vitest.config.ts` (projects tests/+tests-bff/, base http://localhost:3000, coverage ≥80%). Test files exercising these pages (likely-coevolve set): tests-bff/{chat-workspace-page,chat-cost-readout,chat-model-controls,chat-history,chat-websearch-toggle,chat-visual-parity,voice-playground,memory-workspace,artifacts-workspace,vision-workspace,video-workspace}.test.tsx + tests/chat-message-markdown.test.tsx.
Honors (patterns / conventions): PROJECT.md UDD invariants — 3-layer DTCG tokens fail-closed · byte-identical data seams · four UI states · WCAG 2.2 AA · design-before-code. CONVENTIONS.md — exactly one h1 per route · decorative icons aria-hidden · role=status/role=alert · scope assertions via `within(section)`. v54 shared decisions — byte-identical seams (query keys/BFF paths/field names/frozen testids inviolable) · token-led no-hardcode (`add.py check` fail-closed) · four-state from states.tsx · native select preserved · relocated assertion reached by navigation, NEVER weakened. KEY TENSION (surfaced to Tin at specify): these six are IMMERSIVE full-height workspaces with thin toolbar headers, NOT scrolling document/dashboard pages — so the PageHeader-everywhere treatment that fit governance/monitoring may NOT fit chat/voice; per-page-fit header treatment is the likely answer. RISKS: (1) chat `data-role` DOM traversal — insert NO wrapper between a turn and its parent; (2) SSE/poll lifecycle — do NOT lazy-mount/unmount a workspace via Tabs (aborts streams, restarts polls); (3) chat header co-locates h1+CostReadout+ModelPicker — any header change must keep cost-readout testid + Model combobox reachable.
Anchors the contract cites: `ChatWorkspace` · `VoicePlayground` · `MemoryWorkspace` · `ArtifactsWorkspace` · `VisionWorkspace` · `VideoWorkspace` · `PageHeader` · `states.tsx`(Loading/ErrorState/Empty/Success) · the frozen seam set (BFF paths /api/gw/v1/chat/completions·/v1/conversations·/v1/audio/transcriptions·/v1/audio/speech·/v1/memories·/v1/artifacts·/v1/video/generations·/admin/catalog/models; the per-page frozen testid + aria-label + section-heading + datalist-id union enumerated above; chat data-role + role=log live region; video pollIntervalMs seam + no-provider message).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: AI-feature pages refreshed UI standard (per-page-fit, six immersive/form workspaces)
Framings weighed: per-page-fit header — PageHeader on the four form pages, refreshed compact toolbar on chat+voice (chosen, Tin via AskUserQuestion) · PageHeader on all six · refreshed toolbar on all six
Must:
<must>
  - All six AI-feature pages present the refreshed UI standard: Aurora token treatment, exactly one h1 per page, and all four UI states (loading·empty·error·success) reachable from states.tsx primitives.
  - Document-form pages (memory·artifacts·vision·video) render their h1 + a short description through the shared PageHeader at the top of the scroll body.
  - Immersive pages (chat·voice) keep a COMPACT toolbar header refreshed to the standard tokens — the workspace area still fills height; chat's CostReadout + ModelPicker stay in the toolbar and reachable.
  - Every data seam (BFF path · response field · mutation · streaming/poll endpoint) and every frozen test hook (testids · aria-labels · section-heading ids · datalist ids · chat data-role · role=log/status/alert · video pollIntervalMs seam + no-provider message) stays BYTE-IDENTICAL.
  - Any state gap is closed via states.tsx WITHOUT behavior change: vision gains an idle Empty (no answer yet); video's zero-jobs hand-rolled paragraph becomes Empty. (Other state swaps only where byte-safe.)
  - chat keeps its turn structure with NO new wrapper inserted between a `data-role` turn and its parent; NO workspace is lazily mounted/unmounted by a tab (would abort SSE / restart polling).
</must>
Reject:
<reject>
  - weakening, renaming, or removing any frozen test hook to make a test pass -> "frozen_seam_touched"
  - a tall PageHeader on chat/voice that pushes the workspace down or drops the cost/model toolbar widgets -> "immersive_header_regressed"
  - any of the six pages left without one of the four states -> "state_missing"
</reject>
After:
<after>
  - each of the six pages renders one h1, its per-page-fit header, and all four states; the full dashboard suite (both vitest projects) is green by navigation-only / behavior-preserving co-evolution; tsc + eslint + add.py check clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the chat+voice / form-pages split is the right line — lowest confidence because VOICE is borderline: its body is a max-w-4xl scrolling two-card grid (STT|TTS), structurally MORE like the form pages than like chat's full-height thread, yet Tin grouped it with chat as immersive; if wrong: voice's header reads inconsistent and needs a one-line follow-up swap to PageHeader. RESOLVED AT DESIGN-CONFIRM — Tin reviews all six mocks before the freeze and can redirect voice there.
  - [ ] normalizing video zero-jobs → Empty and adding vision idle → Empty changes NO asserted text — confirm the video/vision suites don't pin the OLD hand-rolled paragraph copy.
  - [ ] chat's toolbar refresh keeps cost-readout testid + Model combobox + Send/Stop reachable by the existing role/testid queries — confirm chat suites pass by navigation/behavior, not by editing an assertion.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Form pages carry a PageHeader
  Given the memory, artifacts, vision, or video page has loaded
  When I read the top of its scroll body
  Then exactly one h1 (the page name) sits inside the shared PageHeader with a short description
  And every data seam (BFF path, response field, mutation) for that page is unchanged

Scenario: Immersive pages keep a compact toolbar header
  Given the chat or voice page has loaded
  When I read its header row
  Then it shows exactly one h1 in a compact toolbar (not a tall PageHeader)
  And chat's CostReadout (testid cost-readout) and ModelPicker (Model combobox) remain reachable in that row

Scenario: Every page presents all four UI states
  Given any of the six pages
  When it is loading, then empty, then errored, then populated in turn
  Then each state renders through a states.tsx primitive (Loading role=status / Empty / ErrorState role=alert / a populated success view)
  And no state path throws or renders blank

Scenario: Vision gains an idle empty state
  Given the vision page has loaded with no answer yet
  When I look at the answer area
  Then an Empty ("no answer yet") is shown
  And the askVision POST body shape and the "media too large" / "No Gemini model available" strings are unchanged

Scenario: Video zero-jobs uses the Empty primitive
  Given the video page has loaded and there are no jobs
  When I look at the jobs area
  Then an Empty is shown (not a bare paragraph)
  And the pollIntervalMs seam, the no-provider message, and the job-list seams are unchanged

Scenario: Frozen seam is never weakened (rejection)
  Given a redesigned page whose relocated control a test asserts
  When the test runs
  Then it passes by reaching the control as rendered (navigation / role query)
  And no testid, aria-label, section-heading id, datalist id, data-role, or message string was renamed or removed

Scenario: Immersive header is not regressed (rejection)
  Given the chat or voice page
  When the redesign is applied
  Then no tall PageHeader is introduced that pushes the workspace down or drops a toolbar widget
  And the SSE/poll lifecycle is unchanged (no lazy mount/unmount of the workspace)

Scenario: No page is left missing a state (rejection)
  Given the six pages after the build
  When each is exercised across loading/empty/error/success
  Then none is missing a state
  And the addition uses states.tsx without altering any data seam
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
UI STRUCTURAL CONTRACT (per-page-fit) — no HTTP/schema change; every data seam BYTE-IDENTICAL.

PageHeader API (consumed, not forked):
  PageHeader({ title, description?, actions?, className?, titleId? })
  -> one header element, exactly one h1 (title), optional description paragraph, optional actions slot.

A. FORM PAGES — PageHeader at top of the scroll body (h1 via PageHeader; the rest unchanged):
  MemoryWorkspace     -> PageHeader title "Memory"    desc (cache/agent memory). Keeps 3 sections add/search/list; ALL seams: bffGet/Post /v1/memories(+/search), bffDelete /v1/memories/:id; aria-labels Memory list/`Delete memory: {content}`/Search results/Relevance score/Memory content/Search memories; buttons Add memory/Search/Delete; section ids add-memory-heading/search-heading/list-heading; null score -> literal "text match"; four states via states.tsx.
  ArtifactsWorkspace  -> PageHeader title "Artifacts" desc. Keeps upload/list sections; seams bffGet/Post /v1/artifacts, bffDelete /v1/artifacts/:id, raw fetch /api/gw/v1/artifacts/:id download; testid artifact-file-input; aria-labels Choose file to upload/Artifact list/`Download {name}`/`Delete {name}`; button Upload; ids upload-heading/list-heading; sizes "1.0 KB"/"512 B"; fileInputRef reset.
  VisionWorkspace     -> PageHeader title "Vision"    desc. Keeps form+answer sections; askVision bffPost /v1/chat/completions (content parts image_url|video_url, stream:false); catalog Gemini-only gate; testid vision-file-input; aria-labels Choose image or video file/Model/Prompt; button Ask; ids vision-heading/answer-heading; 413 -> "media too large"; "No Gemini model available…" gate. ADD: idle Empty in answer area (no answer yet).
  VideoWorkspace      -> PageHeader title "Video"     desc. Keeps generate form + jobs sections; bffGet/Post /v1/video/generations; downloadArtifact; aria-labels Model/Prompt/`Download video for job {id}`; button Generate; datalist video-model-options; ids video-heading/jobs-heading (sr-only "Video jobs"); pollIntervalMs prop seam; "no_video_provider_configured" -> "Video generation isn't configured yet."; role=status "Loading jobs…". ADD: zero-jobs -> Empty.

B. IMMERSIVE PAGES — refreshed COMPACT toolbar header (one h1, token-refreshed, NOT a tall PageHeader):
  ChatWorkspace       -> toolbar header keeps h1 "Chat" + CostReadout (testid cost-readout, slot session-cost) + ModelPicker (slot model-picker, Model combobox) as a row. Body unchanged: ChatHistorySidebar (aria-label Conversation history), thread role=log aria-live polite aria-label Conversation with data-role={role} turns (NO new wrapper between turn and parent), composer (Message/Send/Stop/Copy/Regenerate, ModelControls System prompt/Temperature/Web search). useChatStream POST /api/gw/v1/chat/completions + conversations seams UNCHANGED; AbortController lifecycle unchanged.
  VoicePlayground     -> refreshed compact toolbar header keeps h1 "Voice"; body unchanged: STT|TTS section cards; testid audio-player; aria-labels Audio file/STT model/Transcription result/Audio result/Text to speak; buttons Transcribe/Speak; datalist ids stt-model-options/tts-model-options; section ids stt-heading "Speech to Text"/tts-heading "Text to Speech"; fetch /api/gw/v1/audio/transcriptions + /v1/audio/speech UNCHANGED.  (⚠ borderline — Tin confirms toolbar-vs-PageHeader for voice at the mock review.)

INVARIANTS (all six): exactly one h1/page; every BFF path/field/mutation/streaming-poll endpoint byte-identical; every frozen testid/aria-label/section-id/datalist-id/data-role/role/message-string byte-identical; tokens only (no raw hex/px); four states via states.tsx; co-evolution navigation-only / behavior-preserving (NEVER weaken an assertion).
```

Status: DRAFT
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% per project (both vitest projects, the standing gate) — net structural additions, zero behavioural assertions weakened.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_form_pages_use_page_header: render memory/artifacts/vision/video → each has exactly one h1 inside a header (PageHeader) with a description; assert a representative data seam/testid still present.
  - test_immersive_pages_compact_toolbar: render chat/voice → exactly one h1, NOT inside a PageHeader description block; chat → cost-readout testid + Model combobox + Send present in the header/page.
  - test_each_page_has_four_states: per page, drive loading→empty→error→success (MSW) → assert role=status (loading), an Empty/idle node, role=alert (error), and the populated success node each appear.
  - test_vision_idle_empty: vision loaded, no answer → an Empty ("no answer yet") in the answer area; askVision body shape + "media too large"/"No Gemini model available" unchanged.
  - test_video_zero_jobs_empty: video loaded, zero jobs → an Empty node; pollIntervalMs seam + "Video generation isn't configured yet." unchanged.
  - test_frozen_seam_intact (rejection): assert the full per-page frozen testid/aria-label/section-id/datalist-id/data-role union is byte-present after redesign (no rename/removal).
  - test_immersive_header_not_regressed (rejection): chat header still co-locates h1 + cost-readout + model-picker in one row; no PageHeader description block on chat/voice.
  - test_no_state_missing (rejection): each of the six exposes all four states via states.tsx primitives.
  NEW red suite asserts the above structurally; existing per-page suites co-evolve ONLY by navigation/behavior-preserving edits where a relocated control demands it.
</test_plan>

Tests live in: `apps/dashboard/tests/design-system/ai-feature-redesign.test.tsx` `apps/dashboard/tests-bff/chat-workspace-page.test.tsx` `apps/dashboard/tests-bff/chat-cost-readout.test.tsx` `apps/dashboard/tests-bff/chat-model-controls.test.tsx` `apps/dashboard/tests-bff/chat-history.test.tsx` `apps/dashboard/tests-bff/chat-websearch-toggle.test.tsx` `apps/dashboard/tests-bff/chat-visual-parity.test.tsx` `apps/dashboard/tests/chat-message-markdown.test.tsx` `apps/dashboard/tests-bff/voice-playground.test.tsx` `apps/dashboard/tests-bff/memory-workspace.test.tsx` `apps/dashboard/tests-bff/artifacts-workspace.test.tsx` `apps/dashboard/tests-bff/vision-workspace.test.tsx` `apps/dashboard/tests-bff/video-workspace.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/chat/` `apps/dashboard/components/voice/` `apps/dashboard/components/memory/` `apps/dashboard/components/artifacts/` `apps/dashboard/components/vision/` `apps/dashboard/components/video/` `apps/dashboard/tests/design-system/ai-feature-redesign.test.tsx` `apps/dashboard/tests/design-system/a11y.test.tsx` `apps/dashboard/tests-bff/chat-workspace-page.test.tsx` `apps/dashboard/tests-bff/chat-cost-readout.test.tsx` `apps/dashboard/tests-bff/chat-model-controls.test.tsx` `apps/dashboard/tests-bff/chat-history.test.tsx` `apps/dashboard/tests-bff/chat-websearch-toggle.test.tsx` `apps/dashboard/tests-bff/chat-visual-parity.test.tsx` `apps/dashboard/tests/chat-message-markdown.test.tsx` `apps/dashboard/tests-bff/voice-playground.test.tsx` `apps/dashboard/tests-bff/memory-workspace.test.tsx` `apps/dashboard/tests-bff/artifacts-workspace.test.tsx` `apps/dashboard/tests-bff/vision-workspace.test.tsx` `apps/dashboard/tests-bff/video-workspace.test.tsx`
Strategy (ordered batches): 1. NEW structural red suite `ai-feature-redesign.test.tsx` → red. 2. FORM pages PageHeader swap (mechanical, lowest-risk): memory · artifacts. 3. VISION: PageHeader + ADD idle Empty in the answer area (states.tsx), keep Gemini gate + 413 string. 4. VIDEO: PageHeader + zero-jobs hand-rolled p → Empty, keep pollIntervalMs + no-provider message + poll-error banner. 5. CHAT: refresh the compact toolbar header tokens, keep h1 + CostReadout + ModelPicker in the row, touch NOTHING between a data-role turn and its parent, leave SSE/Abort lifecycle intact. 6. VOICE: refresh the compact toolbar header tokens (or PageHeader if Tin redirects at mock review), body unchanged. 7. green full suite (legacy+bff+design-system) + tsc + eslint + add.py check; capture all six real pages at verify.
Known-problem fixes: chat data-role traversal → insert NO wrapper between a turn and its containing div (tests use `.closest("[data-role='assistant']")`) · SSE/poll lifecycle → do NOT move a workspace behind a lazy tab (would abort the stream / restart polling); these pages do NOT get Tabs · chat header → keep cost-readout testid + Model combobox + Send reachable by role; the toolbar is refreshed in place, not replaced by PageHeader · vision/video Empty additions → keep them inside the SAME section ids; do not alter the asserted strings ("media too large", "No Gemini model available", "Video generation isn't configured yet.") · voice/audio + artifacts/video object-URL + FileReader lifecycles → header change must not remount the panels · R3 no raw hex/px in components/ui/* (PageHeader already token-only) · native select preserved on vision · co-evolve a per-page suite ONLY when a relocated control needs navigation — re-cross `add.py phase build` after any §4-declared test edit.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): every BFF path · response field · mutation · streaming/poll endpoint and every frozen test hook (testids · aria-labels · section ids · datalist ids · data-role · role=log/status/alert · pollIntervalMs seam · message strings) stays BYTE-IDENTICAL; a relocated assertion is reached by navigation/behavior, never by loosening it; chat's SSE/Abort + video's poll lifecycle are untouched.
Code lives in: `apps/dashboard/components/{chat,voice,memory,artifacts,vision,video}`
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

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

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
