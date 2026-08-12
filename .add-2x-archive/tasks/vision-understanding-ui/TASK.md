# TASK: Dashboard /app/vision: image/video + prompt -> Gemini chat answer via BFF

slug: vision-understanding-ui · created: 2026-06-26 · stage: production
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
  - `apps/dashboard/lib/vision.ts` (NEW) — `askVision({model, prompt, dataUrl, mediaType}) -> Promise<string>`: builds an OpenAI multimodal message ({role:"user", content:[{type:"text",text:prompt},{type: mediaType==="video"?"video_url":"image_url", [k]:{url:dataUrl}}]}) and bffPost("/v1/chat/completions", {model, messages, stream:false}); returns `res.choices[0].message.content`.
  - `apps/dashboard/components/vision/VisionWorkspace.tsx` (NEW) — a Gemini model picker (reuse ModelPicker, filter to ids containing "gemini") + a file input (image/* , video/*) → FileReader.readAsDataURL → dataUrl + mediaType + a client-side size warning over ~20 MiB + a prompt textarea + an "Ask" button (disabled until model+file+prompt) → askVision → render the answer; loading/error/answer states; best-effort .catch → non-blocking ErrorState.
  - `apps/dashboard/app/(app)/app/vision/page.tsx` (NEW) — mirrors app/(app)/app/memory/page.tsx.
  - `apps/dashboard/components/ui/app-shell.tsx` (MODIFY) — NAV_ITEMS += { href:"/app/vision", label:"Vision", icon:<a lucide icon, e.g. Eye/ImagePlay> } (role-open).
  - `apps/dashboard/tests-bff/vision-workspace.test.tsx` (NEW) — vitest+jsdom+MSW; mirror memory-workspace tests.
  - `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (MODIFY) — bump the nav counts for the new Vision item.
Context (working folder):
  - ModelPicker (components/chat/ModelPicker.tsx): `<ModelPicker value onChange className/>` — fetches GET /v1/models via bffGet, renders a select of model ids. REUSE it (the vision UI filters its value-set to Gemini, or passes a filtered list — simplest: a local Gemini-only select mirroring ModelPicker's fetch, OR reuse ModelPicker and just default to a gemini id). The v46 backend only translates multimodal for the GEMINI adapter, so vision is Gemini-only (honest).
  - bff-client: bffGet/bffPost (credentials:"include"; cookie→Bearer→tenant in the BFF; throw BffError). All gateway calls via /api/gw.
  - Backend (v46 t1, DONE): POST /v1/chat/completions with a Gemini model + a multimodal content array → the model sees the inline image/video and answers; an over-cap inline → 413; a non-data url → 400.
  - Nav filtering: visibleItems drops minRole==="admin" for members; Vision is role-OPEN (like Chat/Memory/Artifacts).
Honors (patterns / conventions):
  - All gateway calls via the BFF; the FE never sends a tenant id. WCAG-AA + v23/v24 tokens + loading/error/answer states (mirror MemoryWorkspace/ArtifactsWorkspace).
  - DESIGN-FOR-FAILURE: a completion failure (BffError, incl. 413/400) shows a non-blocking, readable error; never an unhandled throw. A client-side size warning before sending (the backend hard-caps at 413).
  - Additive: no change to existing surfaces beyond the one nav entry + the nav-count test.
Anchors the contract cites:
  - `lib/vision.ts` askVision · `VisionWorkspace` · the /app/vision route · the NAV_ITEMS Vision entry · the multimodal message shape.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a `/app/vision` dashboard surface — pick an image or short video, type a question, choose a Gemini model, and see the model's understanding — over the v46 multimodal chat path via the BFF.
Framings weighed: a standalone /app/vision workspace (non-streaming POST /v1/chat/completions with a multimodal content array, chosen — simplest, reuses the v46 backend + ModelPicker) · fold into the chat workspace (rejected — the chat composer is text-first; vision is a focused tool) · streaming the answer (rejected for the MVP — a single answer render is simpler + the media round-trip dominates latency).
Must:
<must>
  - M1 — `/app/vision` lets the user pick an image (image/*) or short video (video/*); the file is read as a data URL.
  - M2 — the user types a prompt and picks a Gemini model (from the catalog, filtered to Gemini); "Ask" is disabled until model + file + prompt are all present.
  - M3 — on Ask, POST /v1/chat/completions {model, messages:[{role:"user", content:[{type:"text",text:prompt},{type:"image_url"|"video_url", ...:{url:dataUrl}}]}], stream:false} via the BFF; the assistant's text answer renders.
  - M4 — a "Vision" nav entry (role-open) routes to /app/vision.
  - M5 — a client-side size warning shows when the file exceeds ~20 MiB (the backend hard-caps with 413).
</must>
Reject:
<reject>
  - the completion fails (BffError, incl. 413 over-cap / 400 bad-part / provider error) -> a non-blocking, readable error state; never an unhandled throw.
  - no model / no file / empty prompt -> Ask disabled / no-op (no empty request).
  - no Gemini model in the catalog -> a clear "no Gemini model available" message (vision is Gemini-only in v46).
</reject>
After:
<after>
  - A signed-in user can, in /app/vision, pick an image/short video, ask a question against a Gemini model, and read the model's answer; other surfaces are unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the catalog exposes a Gemini model the tenant can call — lowest confidence because it depends on the deployment's configured models/credentials; if none, the UI shows "no Gemini model available" (honest degrade) rather than failing a call. Cost if wrong: the surface is inert until a Gemini model is in the catalog (a config/ops matter, not a code bug).
  - [x] ModelPicker fetches /v1/models via bffGet — CONFIRMED; reuse or mirror its fetch.
  - [x] FileReader.readAsDataURL works in jsdom — CONFIRMED (used by artifacts-ui).
  - [ ] non-stream chat response shape res.choices[0].message.content — standard OpenAI; the test mocks it.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Ask about an image
  Given a chosen image, a prompt "what is this?", and a Gemini model
  When the user clicks Ask
  Then POST /v1/chat/completions is called with a content array [text, image_url(dataUrl)] and stream:false, and the answer renders

Scenario: Ask about a short video
  Given a chosen video/mp4 and a prompt
  When Ask
  Then the content part is video_url (not image_url) and the answer renders

Scenario: Ask disabled until ready (rejection)
  Given any of {model, file, prompt} missing
  When the form renders
  Then Ask is disabled and no request is sent

Scenario: A failed completion is non-blocking (rejection)
  Given POST /v1/chat/completions returns 413 (over cap) or 400
  When the user asks
  Then a readable error state is shown and the page does not crash

Scenario: No Gemini model (rejection)
  Given /v1/models returns no gemini ids
  When /app/vision mounts
  Then a "no Gemini model available" message shows and Ask is unavailable

Scenario: Vision nav entry routes
  Given any signed-in role
  When the shell renders
  Then a role-open "Vision" entry links to /app/vision
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
lib/vision.ts (BFF, all via /api/gw, cookie-scoped):
  type MediaKind = "image" | "video"
  askVision({model: string, prompt: string, dataUrl: string, mediaType: MediaKind}): Promise<string>
    -> part = mediaType==="video" ? {type:"video_url", video_url:{url:dataUrl}} : {type:"image_url", image_url:{url:dataUrl}}
    -> body = { model, messages:[{role:"user", content:[{type:"text", text:prompt}, part]}], stream:false }
    -> res = bffPost<ChatCompletion>("/v1/chat/completions", body)
    -> return res.choices?.[0]?.message?.content ?? ""   (empty-string fallback, never throw on shape)

VisionWorkspace (client component):
  - Gemini model select: fetch /v1/models (bffGet), filter ids that include "gemini"; empty → "no Gemini model available" + Ask hidden/disabled.
  - File input accept="image/*,video/*"; on change → FileReader.readAsDataURL → {dataUrl, mediaType: file.type.startsWith("video") ? "video" : "image"}; warn if file.size > 20*1024*1024.
  - Prompt textarea. Ask disabled unless model && file && prompt.trim().
  - On Ask: loading → askVision(...) → render the answer text; on BffError → non-blocking ErrorState (readable; 413 → "media too large", else the problem title).

app/(app)/app/vision/page.tsx — mirror app/(app)/app/memory/page.tsx.
app-shell.tsx — NAV_ITEMS += {href:"/app/vision", label:"Vision", icon:<Eye/>} role-open.
```

Status: FROZEN @ v1 — auto-approved (reuse-only MVP per Tin's checkpoint; FE-only, additive, mirrors v44/v45 UI + reuses the v46 backend + ModelPicker fetch). 2026-06-26
Least-sure flag surfaced at freeze:
  - [spec] Gemini-only honesty — v46 wired multimodal only for the Gemini adapter, so the UI restricts the model list to Gemini + shows "no Gemini model available" when absent. If a non-Gemini model were selectable, the media would be silently dropped by that adapter. Mitigation: the filter + the empty-state message. Cost if wrong: a confusing "model ignored my image" — avoided by the filter.
  - [test] non-stream response shape — askVision reads res.choices[0].message.content with optional-chaining + "" fallback so a malformed response never throws; the test mocks the standard OpenAI shape.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — vitest+jsdom+MSW (localhost:3000), mirror tests-bff/memory-workspace.test.tsx.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_ask_about_image: pick image + prompt + gemini model → Ask → POST /v1/chat/completions asserted (content has a text part + an image_url part with the data URL; stream:false) → answer renders.
  - test_ask_about_video: pick video/mp4 → the part is video_url (not image_url).
  - test_ask_disabled_until_ready: missing model/file/prompt → Ask disabled, no POST.
  - test_failure_nonblocking: POST → 413 → readable role="alert" error, no crash.
  - test_no_gemini_model: /v1/models returns no gemini ids → "no Gemini model available", Ask unavailable.
  - test_nav (in nav-role-filter.test.tsx): Vision entry present all roles; counts bumped.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/vision-workspace.test.tsx` + `apps/dashboard/tests-bff/nav-role-filter.test.tsx` · MUST run red before Build. (Run via `node_modules/.bin/vitest run`.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/vision.ts` · `apps/dashboard/components/vision/` · `apps/dashboard/app/(app)/app/vision/` · `apps/dashboard/components/ui/app-shell.tsx` · `apps/dashboard/tests-bff/vision-workspace.test.tsx` · `apps/dashboard/tests-bff/nav-role-filter.test.tsx`
  (mocks/handlers.ts only if a default /v1/models or /v1/chat/completions handler is needed for the shared tests to mount — add the minimal default if so.)
Strategy (ordered batches): 1. lib/vision.ts askVision. 2. VisionWorkspace (model fetch+filter, file→dataUrl, prompt, Ask, states) + page + nav entry. 3. tests (workspace + nav bump). Write tests first (red), then build.
Safety rule (feature-specific): the FE never sends a tenant id (cookie-scoped BFF). Every BFF call has a .catch → non-blocking ErrorState. Gemini-only model filter + honest empty-state. A client-side size warning, but the backend's 413 is the real guard.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; npm allow-list only (lucide + existing UI primitives; no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — vision-workspace 8/8 (3 lib + 5 component) + nav-role-filter 5/5; FULL dashboard suite 600/600 (was 592; +8).
- [x] coverage did not decrease — 8 new behavioral tests; nav test updated in lockstep.
- [x] no test or contract was altered during build — new tests + a default POST /v1/chat/completions MSW handler; §3 contract unchanged.
- [x] the green was EARNED — I read lib/vision.ts in full: it builds EXACTLY the v46 backend's content-part shape ({type:"text",text}, {type:"image_url",image_url:{url}}, {type:"video_url",video_url:{url}}, stream:false) and reads choices[0].message.content with optional-chaining + "" fallback. The FE↔BE integration link is verified by inspection, not just mocks. Tests assert the POST body parts + the video-vs-image switch + the 413 message + the gemini-only empty-state — no vacuous asserts.
- [x] concurrency / timing safe — N/A (single non-streaming request); independent best-effort call with .catch.
- [x] no exposed secrets / injection / unexpected deps — grep confirms NO tenant_id in lib/vision.ts or components/vision (only a doc-comment mentions tenant); all via /api/gw (cookie-scoped); no new npm deps (lucide Eye present).
- [x] layering & dependencies follow CONVENTIONS.md — mirrors v44/v45 workspaces + ModelPicker fetch; reuses UI primitives; one role-open nav entry.
- [x] reviewed — full-auto self-review per Tin's directive: read lib/vision.ts + verified the content-part shape matches the v46 backend + scope clean + no tenant leak. (Outward PR/push deferred.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] an image ask posts a text + image_url content array (stream:false) — test_ask_about_image + my read of askVision (image branch).
- [x] a video ask posts a video_url part (not image_url) — test_ask_about_video; the switch is on mediaType from file.type.startsWith("video").
- [x] the FE shape matches the v46 backend exactly — verified by inspection: text/image_url.url/video_url.url align with _content_to_gemini_parts; so an end-to-end Gemini call would translate to inlineData.
- [x] a 413 (over-cap) renders a readable, non-blocking error — test_failure_nonblocking (role="alert", "media too large").
- [x] no Gemini model → honest empty-state, Ask unavailable — test_no_gemini_model (catalog has only gpt-4o/claude → "No Gemini model available").
- [x] Vision nav entry role-open + routes — nav-role-filter counts bumped (member 8→9, admin/owner/unknown 16→17) + the /app/vision route.

### Deep checks
- [x] WIRING (code) — askVision ← VisionWorkspace; the page renders it; NAV_ITEMS has the Vision entry; 8 tests exercise image/video/disabled/failure/no-gemini end-to-end (MSW).
- [x] DEAD-CODE (code) — no orphaned symbol; tsc 0 + eslint 0 on touched files.
- [x] SEMANTIC — read VisionWorkspace + lib/vision in full; gemini-only honesty + best-effort + BFF-only + the exact backend-matching shape confirmed.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto (Tin's "complete all milestones in auto mode") · date: 2026-06-26

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
