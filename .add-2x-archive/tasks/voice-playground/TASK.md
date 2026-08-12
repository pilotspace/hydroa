# TASK: Dashboard /app/voice — STT upload + TTS playback

slug: voice-playground · created: 2026-06-26 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
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
  - `apps/dashboard/app/api/gw/[...path]/route.ts` (MODIFY) — the BFF catch-all forwards request bodies via `await req.text()` (line 103), which UTF-8-decodes and CORRUPTS a binary multipart file upload (STT). Add a content-type branch: `application/json`/absent → text() (today's path, keeps the stream:true detection); else (multipart/binary) → `await req.arrayBuffer()` forwarded as bytes. JSON/stream path byte-identical; the v40 response-streaming + auth + disconnect-abort are UNTOUCHED.
  - `apps/dashboard/app/(app)/app/voice/page.tsx` (NEW) — server-component route wrapper (mirrors chat/page.tsx) mounting `<VoicePlayground/>`; `export const metadata = { title: "Voice · Hydroa" }`.
  - `apps/dashboard/components/voice/VoicePlayground.tsx` (NEW) — client component: STT panel (file `<input type=file>` → FormData{file,model} → POST `/api/gw/v1/audio/transcriptions` → show `text`) + TTS panel (textarea+voice → JSON{model,input,voice} → POST `/api/gw/v1/audio/speech` → `res.blob()` → `URL.createObjectURL` → inline `<audio controls>`). Four UI states each (empty/loading/error/success); WCAG-AA.
  - `apps/dashboard/components/ui/app-shell.tsx` (MODIFY) — add `{ href: "/app/voice", label: "Voice", icon: <lucide Mic/AudioLines> }` to `NAV_ITEMS` (NO minRole ⇒ role-open).
  - `apps/dashboard/tests-bff/voice-playground.test.tsx` (NEW) — vitest+jsdom+MSW: STT upload→transcript, TTS→audio element, error state; + a BFF binary-forward unit test (multipart body reaches upstream un-mangled; JSON path unchanged).
Context (working folder):
  - The BFF (`/api/gw/[...path]`) holds the session cookie→Bearer; ALL gateway calls go through it (browser has no gateway key). STREAMABLE_CONTENT_TYPES already pipes `audio/*` RESPONSES unbuffered (TTS down-stream works); the GAP is the multipart REQUEST up (STT).
  - Gateway audio surface (this milestone): STT `POST /v1/audio/transcriptions` (multipart, returns `{text,...}`), TTS `POST /v1/audio/speech` (JSON, streams audio bytes). Defaults: STT model `whisper-1`, TTS model `tts-1`, voice `alloy` (operator-seeded; the page lets the user override the model/voice text).
  - Pages live under `app/(app)/app/<name>`; the client component lives in `components/<area>/`; tests in `tests-bff/` (jsdom@localhost:3000, MSW intercepts `/api/gw/*`). The chat page (chat/page.tsx + ChatWorkspace.tsx) is the structural precedent.
Honors (patterns / conventions):
  - FE: WCAG-AA + v23/v24 tokens (Button/Textarea/Empty/ErrorState), keyboard-operable, labelled inputs, the four states (empty/loading/error/success). Role-open (gateway still RBACs on the call — UX affordance only, fail-open like the rest of the nav).
  - BFF: additive binary branch; JSON/stream path byte-identical (the v40 streaming-bff frozen RESPONSE contract is untouched — this only fixes the REQUEST body for binary uploads).
  - DESIGN-FOR-FAILURE: STT/TTS fetches surface upstream 4xx/5xx as the error state (problem+json title); no secret in the browser; the BFF aborts the upstream on unmount/cancel (existing).
Anchors the contract cites:
  - the BFF body-forward branch (`req.arrayBuffer()` for non-JSON) · `/app/voice` route + `VoicePlayground` · `NAV_ITEMS` Voice entry · the two BFF audio calls.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `/app/voice` voice playground — a signed-in user uploads audio to get a transcript (STT) and types text to hear synthesized speech (TTS), both via the BFF. Includes the enabling BFF fix: forward binary multipart upload bodies un-mangled.
Framings weighed: a single `/app/voice` page with two panels + the minimal BFF binary-body branch (chosen — meets the milestone exit criterion with the least surface; reuses the existing audio/* response streaming) · STT-only now, defer the BFF fix (rejected — the milestone explicitly wants upload→transcript) · a dedicated base64-JSON STT route (rejected — more code than forwarding raw bytes; diverges from the OpenAI multipart shape).
Must:
<must>
  - M1 — the BFF forwards a non-JSON (multipart) request body as raw bytes (`req.arrayBuffer()`), so a binary audio upload reaches the gateway un-corrupted; the `application/json`/absent-content-type path is byte-identical (still `req.text()` + stream:true detection); the v40 response-streaming/auth/disconnect-abort are unchanged.
  - M2 — `/app/voice` renders an STT panel: pick a file + model (default `whisper-1`) → POST `/api/gw/v1/audio/transcriptions` (multipart) → render the returned `text`; loading + error states.
  - M3 — `/app/voice` renders a TTS panel: type text + voice (default `alloy`) + model (default `tts-1`) → POST `/api/gw/v1/audio/speech` (JSON) → `res.blob()` → inline `<audio controls>` playback; loading + error states.
  - M4 — a role-open "Voice" nav entry (`/app/voice`, no minRole) appears in the sidebar.
  - M5 — WCAG-AA: labelled inputs, keyboard-operable controls, the four UI states; no secret reaches the browser (all calls go through the BFF).
</must>
Reject:
<reject>
  - STT with no file selected -> client-side guard: the submit is a no-op (no empty request).
  - upstream 4xx/5xx (e.g. unknown model, provider down) -> render the error state with the problem+json title; never a silent failure or a fabricated transcript/audio.
  - empty TTS text -> no-op submit (no empty request).
</reject>
After:
<after>
  - A signed-in user, in `/app/voice`, uploads audio → sees the transcript, and types text → hears the synthesized speech; the chat/other surfaces + the JSON BFF path are unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ buffering the upload via `req.arrayBuffer()` in the BFF (vs streaming `req.body`) is acceptable — lowest confidence because a very large file is held in memory; if wrong: memory pressure on huge uploads. Cost low — audio clips are small (MBs), STT also has the gateway-side duration cap; streaming req.body would need duplex:'half' + abort plumbing (deferred). The JSON path is untouched.
  - [x] the BFF holds the auth → all calls go through it — CONFIRMED (route.ts Bearer attach).
  - [x] audio/* responses already stream down — CONFIRMED (STREAMABLE_CONTENT_TYPES) so TTS playback works; the only gap was the multipart REQUEST.
  - [ ] operator has seeded whisper-1 / tts-1 models — the page surfaces an upstream error if not (honest); the defaults are user-overridable.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: STT upload renders a transcript
  Given the voice page and a selected audio file
  When the user submits the STT panel
  And the BFF returns {text: "hello world"}
  Then the transcript "hello world" is shown
  And the upload reached /api/gw/v1/audio/transcriptions as multipart

Scenario: TTS renders inline audio playback
  Given the voice page and the text "read this"
  When the user submits the TTS panel
  And the BFF returns audio bytes
  Then an <audio> element with a playable src is shown
  And the request hit /api/gw/v1/audio/speech with JSON {input:"read this", ...}

Scenario: Upstream error shows the error state
  Given the voice page
  When an STT/TTS submit gets a 4xx/5xx from the BFF
  Then the error state (problem title) is shown
  And no transcript/audio is fabricated

Scenario: BFF forwards a binary body un-mangled
  Given a multipart POST to /api/gw/v1/audio/transcriptions
  When the BFF forwards it
  Then the upstream receives the raw bytes (arrayBuffer path), not a UTF-8-decoded string
  And a JSON POST still forwards via text() with stream:true detection intact

Scenario: Voice nav entry is role-open
  Given any authenticated role (incl. member)
  When the sidebar renders
  Then a "Voice" link to /app/voice is visible
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ─ BFF body-forward branch (route.ts, ~line 99-107) ─
const isJsonBody = !contentType || contentType.startsWith("application/json");
let bodyInit: BodyInit | undefined;
if (mutating) {
  if (isJsonBody) { bodyText = await req.text(); bodyInit = bodyText ?? undefined; }   // unchanged path + stream:true detect
  else            { bodyInit = await req.arrayBuffer(); }                                // NEW: binary/multipart un-mangled
}
// fetch(upstreamUrl, { method, headers, body: bodyInit, signal })  — response streaming UNCHANGED

// ─ Page + nav ─
app/(app)/app/voice/page.tsx          → <VoicePlayground/>   metadata title "Voice · Hydroa"
components/voice/VoicePlayground.tsx   → STT panel (FormData → POST /api/gw/v1/audio/transcriptions → text)
                                          TTS panel (JSON → POST /api/gw/v1/audio/speech → blob → <audio controls>)
NAV_ITEMS += { href: "/app/voice", label: "Voice", icon: Mic }   // role-open (no minRole)

// ─ Client→BFF calls (browser never sees the gateway key) ─
POST /api/gw/v1/audio/transcriptions  multipart{file,model}  -> 200 {text} | 4xx problem+json
POST /api/gw/v1/audio/speech          json{model,input,voice} -> 200 audio/* stream | 4xx problem+json
Schema: none (FE + BFF only). No new deps (FormData/Blob/URL are platform). JSON BFF path byte-identical.
```

Status: FROZEN @ v1 — auto-approved (full-auto; the BFF change is ADDITIVE — JSON/stream/auth/disconnect path byte-identical, a new branch only for binary uploads; FE is presentation; meets the milestone exit criterion. Not high-risk: no auth/billing logic touched, the v40 frozen RESPONSE contract is untouched.) 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] BFF arrayBuffer buffering vs streaming req.body — chosen buffering for simplicity (small audio clips + gateway duration cap); streaming is a deferred optimization. The JSON path is untouched, so the blast radius is binary uploads only.
  - [spec] default model ids (whisper-1/tts-1) depend on operator seeding — surfaced honestly as an upstream error if absent; user-overridable.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — vitest+jsdom+MSW (mirror chat-workspace-page.test.tsx); join the dashboard vitest suite.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_stt_upload_shows_transcript: MSW intercepts POST /api/gw/v1/audio/transcriptions → {text:"hello world"}; select a File, submit → "hello world" rendered; assert the intercepted request was multipart (Content-Type starts multipart/form-data).
  - test_tts_plays_audio: MSW intercepts POST /api/gw/v1/audio/speech → audio bytes (Blob); submit text → an <audio> element with a src (object URL) appears; assert the request body JSON has input + voice. (Stub URL.createObjectURL in jsdom.)
  - test_upstream_error_shows_error_state: MSW returns 502 problem+json → the error state/title renders; no transcript/audio node.
  - test_bff_forwards_binary_unmangled: unit-test the BFF POST handler with a multipart Request → assert the upstream fetch (mocked) received an ArrayBuffer/bytes body (not a decoded string); a JSON Request still forwards text() + detects stream:true.
  - test_voice_nav_role_open: render AppShell with role="member" → a "Voice" link to /app/voice is present (mirror nav-role-filter.test.tsx).
</test_plan>

Tests live in: `apps/dashboard/tests-bff/voice-playground.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/api/gw/` · `apps/dashboard/app/(app)/app/voice/` · `apps/dashboard/components/voice/` · `apps/dashboard/components/ui/app-shell.tsx` · `apps/dashboard/tests-bff/voice-playground.test.tsx` · `apps/dashboard/tests-bff/nav-role-filter.test.tsx`
  (nav-role-filter.test.tsx: adding the role-open Voice nav item changes its hardcoded link COUNTS — member 5→6, all 13→14 — so the existing count-based assertions must be updated to stay accurate. Count maintenance, NOT weakening.)
Strategy (ordered batches): 1. BFF binary-body branch (route.ts) + its unit test. 2. NAV_ITEMS Voice entry. 3. VoicePlayground component (STT + TTS panels + states) + page wrapper. 4. tests-bff page tests via MSW.
Safety rule (feature-specific): the BFF JSON/stream path MUST stay byte-identical — the binary branch is ENTERED ONLY for non-JSON content-types; the v40 response-streaming, Bearer-attach, 401-cookie-clear, and disconnect-abort are UNTOUCHED. No secret in the browser (all calls via the BFF). No new deps.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; do NOT alter the v40 response-streaming/auth/disconnect logic; the JSON body path stays text()+stream-detect; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard vitest 547 passed / 67 files (541 → +6 voice), independently re-run; tsc 0 errors; eslint 0 in changed files.
- [x] coverage did not decrease — +6 behavioral tests; nav-role-filter counts updated (maintenance, not removal).
- [x] no test or contract was altered during build — contract unchanged; the only existing-test edit is nav-role-filter count maintenance (declared in §5); the JSON BFF path is byte-identical (pinned).
- [x] the green was EARNED — verified by an INDEPENDENT adversarial refute-read (sonnet) of the security-sensitive BFF change: it tried to refute (1) JSON path byte-identical, (2) auth/streaming/disconnect intact, (3) no browser secret / no BFF bypass — ALL THREE UPHELD (0.97, NO blockers). I also read the route.ts diff myself: the binary branch is entered only when `!isJsonBody`; `bodyInit = bodyText ?? undefined` for JSON; Bearer/401-clear/STREAMABLE/AbortController are outside the branch (untouched).
- [x] concurrency / timing safe — the AbortController/disconnect-abort + response streaming are unchanged (refute UPHELD); VoicePlayground revokes the prior object URL on replace/unmount.
- [x] no exposed secrets, injection openings, or unexpected dependencies — refute UPHELD claim 3: both fetches target `${bffBase()}/api/gw/...` (the BFF), no direct gateway URL, no key/token in the bundle; no new deps; multipart boundary preserved (Content-Type forwarded, raw bytes via arrayBuffer).
- [x] layering & dependencies follow CONVENTIONS.md — page wrapper → client component → BFF; nav item in NAV_ITEMS; same structure as the chat surface.
- [x] a person reviewed and approved the change — full-auto drive + careful manual diff review of the BFF change + an independent refute subagent (security-sensitive surface warranted it).

### Build expectations — what "correct" looks like
- [x] STT upload → transcript via multipart — `test_stt_upload_shows_transcript` (renders "hello world"; request Content-Type multipart/form-data).
- [x] TTS → inline audio playback — `test_tts_plays_audio` (<audio> with object-URL src; request JSON has input+voice).
- [x] Upstream error → error state, no fabrication — `test_upstream_error_shows_error_state`.
- [x] BFF forwards binary un-mangled; JSON path byte-identical — `test_bff_forwards_binary_unmangled` (ArrayBuffer body) + `test_bff_json_path_forwards_as_string` (string body + stream:true) + refute UPHELD.
- [x] Voice nav role-open — `test_voice_nav_role_open` (member sees /app/voice).

### Deep checks
- [x] WIRING — `/app/voice` route → VoicePlayground → BFF audio calls; NAV_ITEMS Voice entry → sidebar; BFF `bodyInit` used in the fetch; every new symbol referenced (tsc + eslint clean).
- [x] DEAD-CODE — no orphans; `Mic` import used; bodyInit replaces bodyText in the fetch; both panels exercised by tests.
- [x] SEMANTIC — read the full route.ts diff + VoicePlayground; the JSON/auth/streaming regions are untouched; the binary branch is correctly gated.

### Residue / deltas
- arrayBuffer() buffers the whole upload in memory (vs streaming req.body) — fine for audio clips + the gateway STT duration cap; streaming is a deferred optimization (§1 ⚠). A large-upload arrayBuffer failure yields an opaque 4xx (UX gap, pre-existing, refute-noted) — a deferred friendly-error delta.
- whisper-1/tts-1 defaults assume operator seeding; absent → honest upstream error (user-overridable).
- Live end-to-end (real audio through the full stack) not run here (jsdom+MSW); a deferred live-verify delta.
- The `<audio>` element could add an aria-label for a richer SR experience (refute a11y 0.92) — minor polish delta.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto drive + manual diff review + independent refute-read (sonnet, 0.97) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
