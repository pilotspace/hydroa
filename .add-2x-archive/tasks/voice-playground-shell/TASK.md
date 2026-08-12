# TASK: Console-grade voice workspace shell + design system

slug: voice-playground-shell · created: 2026-06-30 · stage: production
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

Touches (files · symbols · signatures):
- `apps/dashboard/app/(app)/app/voice/page.tsx` — the `/app/voice` route; 6-line shell that renders `<VoicePlayground/>`. Sets page title only.
- `apps/dashboard/components/voice/VoicePlayground.tsx:VoicePlayground` — the CURRENT shallow surface (two side-by-side panels `SttPanel` + `TtsPanel`, own `<header><h1>Voice</h1>`). This task REBUILDS it into a Console-grade 3-region workspace shell (composer+mic region · transcript thread · voice/model controls panel). Renamed/restructured; the sub-panels' STT/TTS logic is consumed by later tasks (voice-mic-capture-stt / voice-tts-playback).
- Chat Console shell to REUSE as the frozen visual language: `apps/dashboard/components/chat/ConversationTopBar.tsx:ConversationTopBar` (top bar pattern + CostReadout slot), `apps/dashboard/components/chat/InspectorPanel.tsx:InspectorPanel` (right controls/metadata panel anatomy), `apps/dashboard/components/chat/ChatWorkspace.tsx` (3-region layout `flex h-full min-h-0` pattern), `apps/dashboard/components/chat/ChatHistorySidebar.tsx` (left session list pattern).
- `apps/dashboard/components/ui/states.tsx` — `Empty` · `Loading` · `ErrorState` (the four-UI-states primitives the shell wires).
- `apps/dashboard/components/ui/{button,textarea,input}.tsx` — the existing primitives.
- `apps/dashboard/components/ui/app-shell.tsx` + `sidebar.tsx` — `NAV_ITEMS` carries `/app/voice` (no `minRole`; `test_voice_nav_role_open` pins it). Shell stays under AppShell.
- `apps/dashboard/lib/hooks/use-catalog-models.ts:useCatalogModels,narrowModels` — catalog-filtered model suggestions (STT `/whisper|transcrib|stt/i`, TTS `/tts|speech|audio/i`).

Context (working folder):
- `.add/milestones/voice-playground/MILESTONE.md` — this milestone (scope · shared decisions · the 5 tasks; shell freezes the layout the rest consume).
- Chat design assets to reuse: `.add/design/captures/chat-playground.png` + `chat-playground-built.png`, `.add/design/prototypes/chat-playground.json`, `.add/design/tokens.json`, `.add/design/catalog.json`, `.add/design/DESIGN.md` — the approved Console language this shell APPLIES (no new identity).
- `apps/dashboard/tests-bff/voice-playground.test.tsx` — current voice tests (7 ids incl `test_voice_nav_role_open`, `test_stt_upload_shows_transcript`, `test_tts_plays_audio`); EVOLVE with the new shell contract, never weaken.
- `apps/dashboard/app/globals.css` + `apps/dashboard/tests/design-system/{tokens,aurora-classic-blue}.test.ts` — the Aurora/Classic-Blue token graph the shell must honor.

Honors (patterns / conventions):
- BFF-only network: every fetch goes to `/api/gw/...` (the data-plane sk- token is minted server-side, never in the browser) — the shell wires no direct-gateway call.
- Four UI states + a11y by construction: one `<h1>`, decorative icons `aria-hidden`, WCAG 2.2 AA, the transcript thread is a `role=log` live region, the mic control announces recording state.
- Console design language is the chat-playground frozen system (reuse, not re-invent) — same tokens, elevation, type scale, ConversationTopBar/InspectorPanel anatomy.
- Pass-through-first (MILESTONE shared decisions): the shell renders structure only; no gateway change.
- Design-before-code (UDD): this task carries the design-confirm gate — a captured voice-workspace screen recorded before build (an application of the approved chat Console language).

Anchors the contract cites: `VoicePlayground` (the rebuilt shell component + its region structure / test ids), `ConversationTopBar`, `InspectorPanel`, `states.tsx` (`Empty`/`Loading`/`ErrorState`), `NAV_ITEMS` (`/app/voice`, no minRole), the Aurora/Classic-Blue design tokens.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Console-grade voice workspace shell (`/app/voice`) — the layout + design system the four later voice tasks consume.
Framings weighed: 3-region Console shell — composer (mic-first) · transcript thread · voice/model controls panel (chosen, reuses chat's frozen Console language) · keep the existing 2-panel STT|TTS split (rejected: not Console-grade, no conversation thread) · full new design identity (rejected: SOUL — identity is the human's; chat's Console language is already approved, reuse it).
Must:
<must>
  - Render `/app/voice` as a Console-grade workspace with three regions in the chat Console visual language: a COMPOSER region (mic-capture control as the primary affordance + a text/upload fallback), a TRANSCRIPT THREAD region (the conversation of spoken turns), and a CONTROLS PANEL region (voice + STT/TTS/chat model pickers + a session metadata/cost slot), inside the existing AppShell.
  - Exactly one `<h1>` ("Voice"); the transcript thread is a `role="log"` live region; every decorative icon is `aria-hidden`; the mic control exposes its recording state to assistive tech; WCAG 2.2 AA.
  - Apply ONLY the frozen chat-playground Console design tokens (Aurora/Classic-Blue) — reuse `ConversationTopBar`/`InspectorPanel` anatomy; introduce no new design identity.
  - Preserve the currently-shipped voice behavior the shell now hosts: STT file-upload still transcribes, TTS still synthesises+plays, catalog model suggestions still narrow, `/app/voice` nav stays open to all members. (The 7 existing `voice-playground.test.tsx` ids keep passing, re-homed onto the new regions.)
  - Every network call goes through the BFF (`/api/gw/...`); the shell wires no direct-gateway request and introduces no gateway change (pass-through-first).
  - Each interactive region surfaces the four UI states (Empty / Loading / Error / Success) from `states.tsx`; an upstream failure renders `ErrorState` with the problem+json `title` and leaves the thread unchanged.
</must>
Reject:
<reject>
  - No microphone / insecure context (no `navigator.mediaDevices`) -> the composer DEGRADES to the text/upload fallback with a non-crash "microphone unavailable" affordance -> "mic_unavailable_degrades" (never an exception/blank screen; deep mic logic is the next task — the shell must only not ASSUME a mic exists)
  - An upstream region error (non-2xx) -> render `ErrorState(title=problem.title)`, thread + other regions unchanged -> "region_error_isolated"
</reject>
After:
<after>
  - `/app/voice` shows the 3-region Console shell; the existing STT/TTS surfaces work inside it; the layout + design contract is frozen for the four dependent tasks to build on.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The 3-region Console layout (composer · transcript thread · controls) is the right IA for VOICE — lowest confidence because voice interaction (record → transcribe → hear) is not chat's text-turn rhythm; if wrong: a shell re-layout reverberates into all four dependent tasks. Mitigated by reusing chat's proven, approved regions and making the composer mic-first.
  - [x] The 7 existing voice tests can be re-homed onto the new shell without weakening any assertion (they assert behavior — transcript text, audio element, nav role, catalog narrowing — not the old 2-panel structure). Confirmed by reading `voice-playground.test.tsx`.
  - [x] Reusing chat's Console tokens needs no new design-confirm identity decision (application of an approved system). Per SOUL: identity is the human's; this is reuse, not a new identity.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Console-grade 3-region shell renders
  Given an authenticated operator on /app/voice
  When the page loads
  Then a composer region with a mic-capture control, a transcript-thread region, and a controls-panel region are all present
  And there is exactly one h1 reading "Voice"

Scenario: transcript thread is an accessible live region
  Given the voice workspace is rendered
  When assistive tech inspects the transcript thread
  Then it exposes role="log"
  And decorative icons are aria-hidden and the mic control announces its recording state

Scenario: applies the frozen Console design tokens
  Given the voice workspace is rendered
  When its surfaces are inspected
  Then they use the Aurora/Classic-Blue tokens and the ConversationTopBar/InspectorPanel anatomy (no new design identity)

Scenario: existing STT upload still transcribes inside the new shell
  Given the rebuilt voice shell
  When an operator uploads an audio file and submits to /api/gw/v1/audio/transcriptions
  Then the returned transcript text renders in the workspace
  And the /app/voice nav stays open to all members and catalog model suggestions still narrow

Scenario: existing TTS still synthesises and plays inside the new shell
  Given the rebuilt voice shell
  When an operator submits text to /api/gw/v1/audio/speech
  Then an audio player (data-testid="audio-player") mounts and can play
  And the call went through the BFF (/api/gw/...), never the gateway directly

Scenario: each region shows the four UI states
  Given any interactive region of the shell
  When it is idle, in-flight, failed, or done
  Then it renders Empty / Loading / ErrorState / Success respectively from states.tsx

Scenario: microphone unavailable degrades (reject)
  Given a context without navigator.mediaDevices (insecure context / no mic)
  When the composer renders
  Then it falls back to the text/upload affordance with a non-crash "microphone unavailable" notice
  And the rest of the workspace renders unchanged (no exception, no blank screen)

Scenario: a region upstream error is isolated (reject)
  Given the workspace is rendered
  When one region's request returns a non-2xx problem+json
  Then that region shows ErrorState with the problem title
  And the transcript thread and the other regions remain unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

UI/component contract (this is a frontend surface — the "shape" is the component tree + render contract, no new HTTP endpoint; pass-through over existing /api/gw/v1/audio/* + /v1/chat/completions):
```
VoicePlayground (components/voice/) — Console-grade 3-region workspace:
  VoiceTopBar    -> <h1>"Voice"</h1> · phase indicator · session-cost pill · abort
  VoiceThread    -> role="log" aria-live="polite"; per-turn {userText, assistant(MessageMarkdown), audio(blob objectURL), meta chip}
  VoiceComposer  -> mic-capture affordance (primary) + text/upload fallback; navigator.mediaDevices absent -> "microphone unavailable" notice (no throw); role="alert" on error
  VoiceInspector -> STT/chat/TTS model pickers (catalog-narrowed) + TTS voice/format + session cost
Network (BFF only, /api/gw/...): STT POST /v1/audio/transcriptions (multipart) · TTS POST /v1/audio/speech (blob) · turn loop POST /v1/chat/completions
  success -> turn appended to thread with metadata; audio object URLs revoked on unmount
  reject  -> "mic_unavailable_degrades" (fallback, no crash) · "region_error_isolated" (ErrorState(problem.title), thread unchanged)
Preserved: the 7 original voice-playground.test.tsx behaviors (STT transcript, TTS audio-player, nav-open, catalog-narrow, BFF binary/json forwarding).
```

Status: FROZEN @ v1 — approved by Tin Dang (project-lead autonomous approval under the standing "ship all playground features" goal; reuses chat's already-approved Console design language — no new identity decision)
Least-sure flag surfaced at freeze: [contract] the 3-region IA fits voice's record→transcribe→hear rhythm — mitigated by reusing chat's proven regions and a mic-first composer; if wrong, a shell re-layout reverberates into the deeper voice capabilities (all delivered in the same combined build, so contained).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: voice suite green (14/14); no behavioural regression in the full dashboard suite.
Plan (one test per scenario, asserting behavior not internals) — written red-first in the worktree build:
<test_plan>
  - 7 PRESERVED: test_stt_upload_shows_transcript · test_tts_plays_audio · test_upstream_error_shows_error_state · test_bff_forwards_binary_unmangled · test_bff_json_path_forwards_as_string · test_voice_nav_role_open · test_model_fields_suggest_catalog_audio_models
  - 7 NEW (Console-grade): test_mic_unavailable_shows_fallback_notice · test_thread_shows_empty_state · test_inspector_chat_model_updates · test_inspector_tts_voice_updates · test_phase_indicator_shows_during_transcription · test_voice_loop_stt_chat_tts_adds_turn · test_per_turn_metadata_shows
</test_plan>

Tests live in: `apps/dashboard/tests-bff/voice-playground.test.tsx` · ran red (new ids fail) before the build, green after.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/voice/` `apps/dashboard/tests-bff/voice-playground.test.tsx` `apps/dashboard/app/(app)/app/voice/page.tsx`
Strategy (ordered batches): 1. red voice tests (7 new) 2. VoiceTopBar/Thread/Composer/Inspector + voice-types 3. VoicePlayground state root (3 network paths, AbortController, URL revocation) 4. green + full-suite check. (Built in a worktree-isolated agent, reconciled to the milestone line.)
Known-problem fixes: object-URL leak → revoke on unmount; mic absent → feature-detect navigator.mediaDevices, never assume; settled-4xx → no retry-storm.
Strategy actually used: as planned (parallel worktree build; combined surface delivering shell + STT + TTS + turn-loop + per-turn metadata).
Safety rule (feature-specific): all audio object URLs created from blobs the app fetched, bulk-revoked on unmount; BFF-only fetches.
Code lives in: `apps/dashboard/components/voice/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps); ask if unclear.

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
- [x] /app/voice renders the 3-region Console shell (TopBar h1 + Thread role=log + Composer + Inspector) — confirmed by test_thread_shows_empty_state + the rendered component tree
- [x] a spoken/typed turn flows STT→chat→TTS and appends to the thread with per-turn metadata — confirmed by test_voice_loop_stt_chat_tts_adds_turn + test_per_turn_metadata_shows
- [x] mic-absent degrades (no crash) and upstream errors are isolated — confirmed by test_mic_unavailable_shows_fallback_notice + test_upstream_error_shows_error_state
- [x] the 7 original voice behaviors still pass (no regression) — confirmed by full suite 897/0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — VoiceTopBar/Thread/Composer/Inspector all referenced by VoicePlayground; page.tsx renders it; confirmed by green render tests + tsc 0
- [x] DEAD-CODE (code) — no orphaned symbol (eslint 0 errors on voice files)
- [x] SEMANTIC — security review read the voice rendering surface: transcript escaped, assistant via MessageMarkdown, audio object URLs blob-only + revoked → CLEAN

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: agent-a9f94ffd (independent security-expert review) + build agent self-verify · adversarially checked: XSS in transcript/markdown/audio rendering (CLEAN), objectURL lifecycle (revoked), BFF-only (confirmed), original test assertions intact (not weakened). Residue: 3 deferrals (real MediaRecorder hold-to-record, TTS autoplay, per-turn cost population) recorded as §7 deltas — non-blocking, surface is functional via fallback/controls.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (orchestrator-driven; independent security review PASS-WITH-NITS, no blockers) · date: 2026-06-30

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose voice/model controls panel; rejected 3-region Console shell — composer (mic-first) · transcript thread · keep the existing 2-panel STT|TTS split (rejected: not Console-grade, no conversation thread) · full new design identity (rejected: SOUL — identity is the human's; chat's Console language is already approved, reuse it).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (project-lead autonomous approval under the standing "ship all playground features" goal; reuses chat's already-approved Console design language — no new identity decision))
- [AI] build — strategy used: as planned (parallel worktree build; combined surface delivering shell + STT + TTS + turn-loop + per-turn metadata).
- [AI] verify — gate PASS (reviewed by Tin Dang (orchestrator-driven; independent security review PASS-WITH-NITS, no blockers))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] real MediaRecorder hold-to-record capture loop (evidence: build deferred actual getUserMedia capture; mic button + unavailable-notice render, turn loop runs via text/upload — needs a jsdom MediaRecorder mock strategy)
- [SPEC · open] TTS reply autoplay after turn (evidence: browser autoplay policy; audio renders with controls, not auto-played)
- [SPEC · open] populate per-turn cost (evidence: BFF /v1/chat/completions does not return per-call pricing; VoiceTurn.meta.cost typed but unpopulated — needs a usage→price lookup like chat's CostReadout)
- [SPEC · open] realtime voice (turn-based /v1/realtime + full-duplex /v1/realtime/relay) (evidence: deferred at milestone scope — needs browser WS transport + browser-token-exposure security decision)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
