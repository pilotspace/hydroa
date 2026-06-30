# TASK: Image attachments in the chat composer

slug: chat-attachments · created: 2026-06-29 · stage: production
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
  - `apps/dashboard/lib/hooks/use-chat-stream.ts:ChatMessage` (l.27-34) — `{ role; content: string; tool_calls?; tool_call_id? }`. `content` is a bare `string` today; OpenAI multimodal needs the USER turn to carry an array of content parts, so `content` must widen to `string | MessageContentPart[]`.
  - `apps/dashboard/lib/hooks/use-chat-stream.ts:SendInput` (l.63-82) — the send payload; gains `images?` (the attached data-URLs) alongside text. Omitted-when-unset: no images ⇒ `content` stays a plain string ⇒ byte-identical off path.
  - `apps/dashboard/lib/hooks/use-chat-stream.ts:send()` (l.439-454) — builds `{ role:"user", content: text }`; the one place that must compose `[{type:"text",text},{type:"image_url",image_url:{url}}]` when images are present.
  - `apps/dashboard/lib/hooks/use-chat-stream.ts:runStream()` (l.251-289) — wire body `messages: wire` is `JSON.stringify`-passed; content-parts ride through unchanged (pure pass-through, NO gateway change — same invariant as chat-tools-functions).
  - `apps/dashboard/components/chat/ChatWorkspace.tsx` composer (l.493-503) — a scaffolded `disabled` "Attach image" Paperclip Button literally commented `Scaffold: image attach ships in chat-attachments`; wire it to a hidden file input + thumbnail previews + remove buttons.
  - `apps/dashboard/components/chat/ChatWorkspace.tsx:submit()` (l.308-343) — appends the user turn + calls `send(...)`; passes the staged images and clears them after send.
  - `apps/dashboard/components/chat/ChatWorkspace.tsx:MessageRow` (l.665-722, render l.704) — renders `message.content` assuming a string (`isUser ? content : <MessageMarkdown content={content}/>`), plus `CopyTurnButton getText={() => message.content}` (l.715) and `regenerateFrom` reading `userMsg.content` (l.277) — all three assume string; must handle content-parts (render text + image thumbnails, copy/regenerate the text part).
Context (working folder):
  - `apps/dashboard/tests-bff/chat-tools.test.tsx` · `chat-parameters.test.tsx` · `tests-bff/mocks/server.ts` — the bff vitest project (MSW `/api/gw` handlers, cookie auth) the new `chat-attachments.test.tsx` joins; reuse its `captureChat()` body-capture + SSE-fixture pattern.
  - `apps/dashboard/lib/chat/tool-defs.ts` — sibling seam-module pattern (parse/validate operator input before the wire); the new `lib/chat/attachments.ts` (file→data-URL + size/type guard) mirrors it.
  - `.add/milestones/chat-playground/MILESTONE.md` — Scope: "Multimodal image attachments in the composer (content-part format the wire already accepts)"; exit criterion "An operator can attach an image to a message and the model answers about it".
Honors (patterns / conventions):
  - **Pass-through first** (MILESTONE shared decision) — image content-parts ride the existing `/v1/chat/completions`; NO gateway change.
  - **Omitted-when-unset / byte-identical off path** (v40 invariant, upheld by chat-parameters + chat-tools) — zero attachments ⇒ user `content` is the same plain string as today.
  - **Four UI states + a11y by construction** (MILESTONE) — preview thumbnails get alt text; the attach control + remove buttons are labelled; decorative icons aria-hidden.
  - **Design-for-failure** (MILESTONE) — client-side size guard + MIME-type allowlist BEFORE building a data-URL; reject oversized/unsupported files with a visible message, never a silent drop or a multi-MB body.
Anchors the contract cites:
  - `use-chat-stream.ts:ChatMessage.content` (widened to `string | MessageContentPart[]`) + new `MessageContentPart` type
  - `use-chat-stream.ts:SendInput.images` + `send()` content-part composition
  - `ChatWorkspace.tsx` composer Paperclip control (l.494) + `submit()` (l.308) + `MessageRow` render (l.704)
  - new `lib/chat/attachments.ts` — `fileToAttachment()` / size+type validation (the seam between a picked File and the wire data-URL)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Image attachments in the chat composer (multimodal user turns).
Framings weighed: inline base64 data-URL content-parts, client-side only (chosen — pass-through, no new infra) · upload to object store + send https URL (rejected — needs a backend/S3 delta, violates pass-through-first) · reference existing artifacts (rejected — couples to the artifacts milestone, out of scope).
Must:
<must>
  - An operator can attach one or more images to the next user message by activating the composer's "Attach image" control (today a disabled Paperclip scaffold) → a native file picker (`accept` = allowed image types).
  - Each staged (not-yet-sent) attachment shows a thumbnail preview with an accessible remove control; removing one un-stages only that file.
  - On Send with ≥1 staged image, the committed user message `content` is the OpenAI content-part array `[{type:"text",text},{type:"image_url",image_url:{url:<dataURL>}},…]` where each dataURL is the base64-encoded file; the array reaches `/v1/chat/completions` unchanged (pass-through).
  - On Send with zero images, `content` stays a plain `string` — byte-identical to today's off path (no content-part array, no behavioral change).
  - The committed user turn renders its text plus its image thumbnails (alt-texted) in the thread; copy/regenerate operate on the text part.
  - After a successful Send the staged-attachment set is cleared (the next turn starts empty).
</must>
Reject:
<reject>
  - A picked file larger than the per-image size cap -> "attachment_too_large" (visible message; the file is NOT staged).
  - A picked file whose MIME type is not in the image allowlist -> "attachment_unsupported_type" (visible message; NOT staged).
  - Staging beyond the per-message attachment count cap -> "attachment_limit" (visible message; the extra files are NOT staged).
</reject>
After:
<after>
  - Valid picks are in component state, previewed and individually removable; invalid picks are rejected with a visible reason and never staged.
  - On Send the wire body carries the content-part array (images present) or a plain string (none); the staged set is cleared.
  - No gateway change, no new runtime dependency; the worst-case encoded body stays under the BFF 32 MiB cap (count cap × per-image cap × ~1.34 base64 inflation).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A base64 **data-URL** in `image_url.url` is accepted end-to-end (BFF → gateway content-part translation → provider) — lowest confidence because the gateway's image handling for non-OpenAI providers (e.g. Anthropic base64, Gemini inlineData) is not exercised by any current dashboard test; if wrong: the attach UX still ships correctly but some providers error at send-time — an honest surfaced error (existing error path), not a silent failure, and OpenAI-family models still work. NOT changing the gateway is the deliberate pass-through stance.
  - [ ] Caps: **5 MB per image**, **image/png·jpeg·webp·gif** allowlist, **4 images per message** (≈27 MiB encoded worst case < 32 MiB BFF cap). Defaults I will draft against; Tin can override at the freeze.
  - [ ] Persistence: attachments are **NOT persisted** to the conversation store in v1 (the best-effort `appendMessage` keeps the text only; a reloaded conversation shows text without images). Recorded as a §7 delta, not built now.
  - [ ] Input is the **file picker only** for v1 (paste-from-clipboard / drag-drop = a later delta).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: attach an image then send as content-parts
  Given the chat composer with text "what is in this image?"
  When the operator picks a valid PNG and clicks Send
  Then the POST /api/gw/v1/chat/completions body's last user message content is
       [{type:"text",text:"what is in this image?"},{type:"image_url",image_url:{url:"data:image/png;base64,…"}}]
  And stream + stream_options.include_usage are still sent (the off-path body is otherwise unchanged)

Scenario: text-only send stays a plain string (byte-identical off path)
  Given the chat composer with text "hello" and NO attachments
  When the operator clicks Send
  Then the last user message content is the string "hello" (not an array)
  And no "image_url" part appears anywhere in the body

Scenario: staged preview is removable before send
  Given the operator has picked two valid images
  When the operator activates the remove control on the first preview
  Then only the second image remains staged (one thumbnail preview shown)
  And no request has been sent

Scenario: committed user turn renders text + thumbnails
  Given a sent user turn carrying text plus one image part
  When the thread renders that turn
  Then the turn shows the text and an alt-texted image thumbnail
  And the assistant reply streams and commits normally

Scenario: staged set clears after send
  Given one staged image and composer text
  When the operator clicks Send
  Then after send the composer shows zero staged previews and empty text

Scenario: oversized file is rejected, not staged
  Given the operator picks an image larger than the per-image size cap
  When the picker resolves
  Then a visible "attachment_too_large" message is shown
  And no preview is staged AND no request is sent

Scenario: unsupported type is rejected, not staged
  Given the operator picks a file whose type is not an allowed image type (e.g. application/pdf)
  When the picker resolves
  Then a visible "attachment_unsupported_type" message is shown
  And no preview is staged AND no request is sent

Scenario: exceeding the count cap is rejected
  Given the operator already has the maximum allowed images staged
  When the operator picks one more valid image
  Then a visible "attachment_limit" message is shown
  And the staged set is unchanged AND no request is sent
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Frontend/client contract — no new HTTP endpoint (pass-through over the existing
# POST /api/gw/v1/chat/completions; the gateway is unchanged).

# --- wire shape (use-chat-stream.ts) ---
type MessageContentPart =
  | { type: "text";      text: string }
  | { type: "image_url"; image_url: { url: string } }   # url = "data:<mime>;base64,<…>"
ChatMessage.content : string | MessageContentPart[]      # widened from string (string still the default/off path)
SendInput.images?   : ImageAttachment[]                  # absent/empty ⇒ content built as a plain string (byte-identical)

# send() composes the user turn:
#   images?.length ? [{type:"text",text}, ...images.map(a => ({type:"image_url", image_url:{url:a.dataUrl}}))] : text
# request body messages[] carries that content verbatim (JSON pass-through; no other body key changes).

# --- attachment seam (lib/chat/attachments.ts) ---
type ImageAttachment = { id: string; name: string; mime: string; dataUrl: string; bytes: number }
fileToAttachment(file: File): Promise<{ ok: true; attachment: ImageAttachment }
                                     | { ok: false; error: AttachmentError }>
type AttachmentError = "attachment_too_large" | "attachment_unsupported_type"
# count-cap ("attachment_limit") is enforced by the composer at stage time, not in fileToAttachment.

Limits (defaults — overridable at this freeze):
  MAX_IMAGE_BYTES = 5 * 1024 * 1024        # 5 MiB per image
  MAX_IMAGES_PER_MESSAGE = 4               # ≈ 27 MiB encoded worst case < 32 MiB BFF cap
  ALLOWED_MIME = ["image/png","image/jpeg","image/webp","image/gif"]

Schema: none — no DB, no migration. Attachments are in-memory per live turn and are NOT
        persisted to the conversation store in v1 (appendMessage keeps text only; §7 delta).
```

Status: FROZEN @ v1 — approved by Tin (2026-06-29; "Freeze as drafted", defaults 5 MiB/image · 4 images · png·jpeg·webp·gif · not-persisted-v1 · file-picker only). Least-sure flag surfaced at freeze: [contract] base64 data-URL acceptance end-to-end through the gateway's content-part translation for non-OpenAI providers — least sure because no dashboard test exercises it; cost if wrong: the attach UX still ships and the failure surfaces as an honest send-time error (OpenAI-family verified-by-design), a deliberate pass-through stance, not a silent break.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new modules) · full dashboard suite stays green.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  UI — tests-bff/chat-attachments.test.tsx (ChatWorkspace + MSW captureChat):
  - test_attach_image_sends_content_parts: pick a valid PNG + type text / Send / body's last user content == [{type:text},{type:image_url,image_url:{url:/^data:image\/png;base64,/}}] + stream/include_usage unchanged
  - test_text_only_stays_string: type text, NO attach / Send / last user content === "hello" (string) AND no "image_url" anywhere in body
  - test_remove_staged_preview: pick two images / click remove on the first / exactly one preview remains AND zero requests sent
  - test_committed_turn_renders_text_and_thumb: pick image + text / Send / thread shows the text AND an alt-texted <img> thumbnail in the user turn
  - test_staged_clears_after_send: pick one image / Send / zero staged previews AND empty composer text afterward
  - test_oversized_rejected: pick a >5 MiB file / a visible /too large/i message AND no preview AND no request
  - test_unsupported_type_rejected: pick application/pdf / a visible /unsupported/i message AND no preview AND no request
  - test_count_cap_rejected: stage 4 valid images, pick a 5th / a visible /limit|maximum/i message AND still 4 previews AND no request
  UNIT — tests-bff/attachments.test.ts (fileToAttachment pure seam):
  - test_fileToAttachment_ok: valid png File / { ok:true, attachment:{ mime:"image/png", dataUrl:/^data:image\/png;base64,/, bytes } }
  - test_fileToAttachment_too_large: File.size > MAX_IMAGE_BYTES / { ok:false, error:"attachment_too_large" }
  - test_fileToAttachment_unsupported: type "application/pdf" / { ok:false, error:"attachment_unsupported_type" }
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-attachments.test.tsx` `apps/dashboard/tests-bff/attachments.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/chat/attachments.ts` `apps/dashboard/lib/hooks/use-chat-stream.ts` `apps/dashboard/components/chat/ChatWorkspace.tsx` `apps/dashboard/components/chat/AttachmentPreview.tsx` `apps/dashboard/tests-bff/chat-attachments.test.tsx` `apps/dashboard/tests-bff/attachments.test.ts`
Strategy (ordered batches): 1. `lib/chat/attachments.ts` — types (MessageContentPart/ImageAttachment) + fileToAttachment (size/type guard, FileReader→dataURL) + limit constants. 2. `use-chat-stream.ts` — widen ChatMessage.content to `string | MessageContentPart[]`; add SendInput.images; compose content-parts in send(); keep runStream pass-through. 3. `AttachmentPreview.tsx` — staged thumbnails + remove + committed-turn thumbnails. 4. `ChatWorkspace.tsx` — wire the Paperclip control to a hidden file input, stage/validate/cap, render previews, pass images through submit() + clear, teach MessageRow to render content-parts.
Known-problem fixes: jsdom FileReader.readAsDataURL works but is async → await it in fileToAttachment and the tests. · userEvent JSON-brace gotcha N/A here, but file picks use fireEvent.change/userEvent.upload on the hidden input (give it a stable testid). · MessageRow/CopyTurnButton/regenerateFrom assume string content → add a textOf(content) helper so they keep operating on the text part. · count-cap is a composer concern (not fileToAttachment) — enforce at stage time.
Strategy actually used: as planned (4 batches in order: attachments.ts seam → use-chat-stream widening + composeUserContent in send() → AttachmentPreview.tsx → ChatWorkspace wiring). One unplanned-but-necessary addition: a `url→filename` map (`imageNames`) in ChatWorkspace so a COMMITTED multimodal turn can alt-text its thumbnails (the wire content-part carries only the url, not the name) — and regenerateFrom reconstructs image parts from content so a regenerated turn keeps its images. Found `cn` lives at `@/lib/cn` (not `@/lib/utils`).
Safety rule (feature-specific): validate size+MIME BEFORE building a data-URL; never stage or send an oversized/unsupported file (no multi-MB body, no silent drop).
Code lives in: `apps/dashboard/`
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

- [x] all tests pass — new: attachments.test.ts (4) + chat-attachments.test.tsx (8) green; full dashboard suite 839/839 (103 files); tsc 0, eslint 0 on the 4 changed/new files.
- [x] coverage did not decrease — 12 net-new tests added; no existing test removed or weakened.
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched; only the two NEW test files authored in the tests phase ran red→green.
- [x] the green was EARNED, not gamed — adversarial refute-read recorded below (frontend-expert subagent): EARNED-GREEN 0.91, no security/cheat/stub findings. Its one FLAG (count-cap async race) was CLOSED by hardening (live-count re-check), proven by the new test_count_cap_holds_under_concurrent_picks.
- [x] concurrency / timing of the risky operation is safe — the count cap uses a synchronous local counter per pick + an attachmentsRef snapshot across picks; functional setState accumulates without lost updates; FileReader encode is awaited.
- [x] no exposed secrets, injection openings, or unexpected dependencies — JSON pass-through body; data-URLs render only as img src/alt (React auto-escapes; no eval, no raw-HTML injection); no new runtime dependency (package.json unchanged); no gateway change.
- [x] layering & dependencies follow CONVENTIONS.md — new seam `lib/chat/attachments.ts` mirrors `lib/chat/tool-defs.ts`; presentation in `components/chat/AttachmentPreview.tsx`; wire composition stays in the hook (pass-through).
- [ ] a person reviewed and approved the change — Tin (pending at the gate report).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] With an image staged, the POST body's last user message `content` is the array `[{type:"text",text},{type:"image_url",image_url:{url:"data:image/png;base64,..."}}]` — confirmed by captureChat body assertion in test_attach_image_sends_content_parts (passes).
- [x] With NO image, `content` is the plain string and the serialized body contains no "image_url" — confirmed by test_text_only_stays_string (passes; off path byte-identical).
- [x] A staged image shows a removable alt-texted thumbnail before send; removing one keeps the rest — confirmed by test_remove_staged_preview (passes).
- [x] The committed user turn renders its text + an alt-texted image thumbnail and the assistant reply still streams — confirmed by test_committed_turn_renders_text_and_thumb (passes).
- [x] After Send the staged set + composer text are cleared — confirmed by test_staged_clears_after_send (passes).
- [x] Oversized (>5 MiB), unsupported-MIME, and over-count picks each show a visible message and are NOT staged and send NO request — confirmed by test_oversized_rejected / test_unsupported_type_rejected / test_count_cap_rejected + the fileToAttachment unit rejections (all pass).
- [x] No gateway/DB change and no new runtime dependency — confirmed: working tree is dashboard + .add only; `git status` shows NO apps/gateway, infra, envoy, or package.json change.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: fileToAttachment/attachmentErrorMessage/ATTACHMENT_LIMIT_MESSAGE/MAX_IMAGES_PER_MESSAGE/ALLOWED_MIME/contentText/contentImages/composeUserContent/ImageAttachment/MessageContentPart consumed by ChatWorkspace + use-chat-stream; StagedAttachments/MessageImages rendered in the composer + MessageRow.
- [x] DEAD-CODE (code) — no orphan: attachmentErrorMessage used in onPickFiles; composeUserContent used in send(); contentText/contentImages used in MessageRow + regenerateFrom; tsc/eslint clean (an unused export would not fail, but every symbol has a live caller).
- [x] SEMANTIC (prose / non-code) — n/a (code task); the §3 contract + §2 scenarios were read in full and each Must/Reject maps to a passing test.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED — after closing a real defect the refute-read found (honest trail below)
By: agent a077f06dfee57c7fc (frontend-expert), TWO adversarial passes. Probes both passes AGREED on: byte-identical off path (non-tautological wire assertions), validation order (size→MIME→encode; rejected files never read), XSS/injection (no raw-HTML sink; React-escaped img src/alt; MIME allowlist blocks data:text/html; data-URL never eval'd), render fidelity (alt from the real imageNames map; copy/regenerate use the text part only — no "[object Object]"), stub-check (real jsdom FileReader base64, not hardcoded), clear-after-send (staged+error cleared; imageNames intentionally retained).
  - Pass 1 (conf 0.91): EARNED-GREEN with one FLAG — the count-cap read a stale `attachmentsRef` snapshot at async entry, so concurrent onPickFiles invocations could over-admit a 5th image; noted the original test sidesteps it with an interposed waitFor.
  - Pass 2 (conf 0.87): NOT-EARNED — escalated the SAME race to "test-structure cheat masking a real implementation gap" (worst case ~33 MiB > 32 MiB BFF cap; NOT a security boundary, not exploitable).
  RESOLUTION (not a waiver): the defect was REAL in the first build. Hardened onPickFiles to enforce against the LIVE count (synchronous ref bump on admit + post-await re-check), and REPLACED the sidestep with test_count_cap_holds_under_concurrent_picks that fires 5 picks in one synchronous burst. FALSIFIED the new test against the old stale-snapshot code → it FAILS (all 5 stage); with the fix → caps at 4. So the guard drives THROUGH the race, not around it. Suite re-green 840/840, tsc/eslint clean. The "cheat" characterization no longer holds: implementation fixed + a falsifiable regression test added.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: Tin (gate report) · date: 2026-06-29

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose inline base64 data-URL content-parts, client-side only; rejected upload to object store + send https URL (rejected — needs a backend/S3 delta, violates pass-through-first) · reference existing artifacts (rejected — couples to the artifacts milestone, out of scope).
- [human] freeze — froze §3 @ v1 (approved by Tin (2026-06-29; "Freeze as drafted", defaults 5 MiB/image · 4 images · png·jpeg·webp·gif · not-persisted-v1 · file-picker only). Least-sure flag surfaced at freeze: [contract] base64 data-URL acceptance end-to-end through the gateway's content-part translation for non-OpenAI providers — least sure because no dashboard test exercises it; cost if wrong: the attach UX still ships and the failure surfaces as an honest send-time error (OpenAI-family verified-by-design), a deliberate pass-through stance, not a silent break.)
- [AI] build — strategy used: as planned (4 batches in order: attachments.ts seam → use-chat-stream widening + composeUserContent in send() → AttachmentPreview.tsx → ChatWorkspace wiring). One unplanned-but-necessary addition: a `url→filename` map (`imageNames`) in ChatWorkspace so a COMMITTED multimodal turn can alt-text its thumbnails (the wire content-part carries only the url, not the name) — and regenerateFrom reconstructs image parts from content so a regenerated turn keeps its images. Found `cn` lives at `@/lib/cn` (not `@/lib/utils`).
- [AI] verify — gate PASS (reviewed by Tin (gate report))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] persist image attachments to the conversation store so a reloaded multimodal turn restores its thumbnails (evidence: best-effort appendMessage keeps text only; v1 reload drops images — recorded as the not-persisted-v1 freeze decision).
- [SPEC · open] paste-from-clipboard + drag-drop image attach (evidence: v1 is file-picker only; the freeze scoped paste/drag out).
- [SPEC · open] live cross-provider verification that a base64 data-URL reaches the model for non-OpenAI providers (Anthropic base64 / Gemini inlineData) through the gateway translation (evidence: the freeze least-sure flag — no dashboard test exercises it; a live check is the only real proof).
- [SPEC · open] a body-size pre-flight if MAX_IMAGE_BYTES/MAX_IMAGES_PER_MESSAGE are ever raised toward the 32 MiB BFF cap (evidence: 4×5 MiB defaults are safe today at ~27 MiB encoded, but a future cap raise needs a guard).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [TDD · open] when a refute-read FLAGs "the test sidesteps a race", close it by REPLACING the sidestep with a test that drives THROUGH the race AND falsifying that test against the buggy code (evidence: test_count_cap_holds_under_concurrent_picks fires 5 picks in one synchronous burst — FAILS on the stale-snapshot impl, PASSES on the live-count re-check; that falsification is what rebuts the "test-structure cheat" verdict).
- [TDD · open] an async event handler that enforces a cap by reading a React ref/state snapshot at entry is racy under concurrent invocations; enforce against a LIVE count (synchronous ref bump on admit + post-await re-check), not a per-call local counter (evidence: 5 concurrent onPickFiles each read length=0 and over-admitted).
- [ADD · open] `cli.js update` from the LOCAL plugin marketplace can DOWNGRADE the engine (marketplace stale at 1.12.0 < project 1.13.0) and dirties .add/tooling + .add/docs + .claude/skills + .add/.add-version — restore all from git HEAD (every file is tracked); the npx registry route is unreliable in this env (evidence: this session's downgrade + git-checkout recovery).
- [ADD · open] restoring tracked NON-scope files DURING verify re-trips the scope anchor (the snapshot was taken at build entry while those files were still dirty) → the honest reset is to re-cross tests→build (`add.py phase build`) to re-snapshot the clean tree, then advance + gate (evidence: scope_violation on 5 .claude/skills/add/* files that were clean at gate time).
