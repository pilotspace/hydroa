# TASK: Console-grade vision workspace rebuild

slug: vision-console-rebuild · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — built in a worktree-isolated agent (parallel multi-milestone build), reconciled to the
> milestone line. Floor held: FROZEN §3 · red tests before build · recorded §6 gate (security review = PASS).

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `apps/dashboard/components/vision/VisionWorkspace.tsx` · `apps/dashboard/lib/vision.ts` (askVision + new askVisionWithMeta/VisionUsage/VisionResult) · `apps/dashboard/app/(app)/app/vision/page.tsx`
Context (working folder): `apps/dashboard/tests-bff/vision-workspace.test.tsx` (8 original behaviors → 12); reuses chat Console language + `components/chat/MessageMarkdown.tsx` (XSS-safe answer rendering).
Honors (patterns / conventions): BFF-only `/api/gw/v1/chat/completions` (image_url/video_url content parts) + `/admin/catalog/models`; render answers ONLY via MessageMarkdown (no raw-HTML injection); media preview from user data URL only; four UI states + WCAG 2.2 AA; pass-through (no gateway change).
Anchors the contract cites: VisionWorkspace, lib/vision.ts (askVision unchanged + askVisionWithMeta), MessageMarkdown, /v1/chat/completions image_url/video_url parts

---

## 1 · SPECIFY — the rules

Feature: Console-grade multimodal vision workspace — media preview + a multi-turn markdown Q&A thread over images/video, Gemini-multimodal model picker.
Must:
  - Media drop/upload zone (image/* and video/*) with inline preview (<img>/<video> from data URL), file name+size, 20MB+ advisory, 413 friendly error; a role="log" conversation thread supporting MULTIPLE turns over the same media (user prompt + media thumbnail on first turn; assistant answer via MessageMarkdown); Gemini model picker (catalog-narrowed); per-response metadata (model·tokens·latency) where available; Ask disabled until model + media + prompt present.
  - Preserve all 8 original behaviors incl the exact image_url vs video_url content-part shape; four UI states; failures non-blocking (thread/preview preserved); one h1 "Vision"; a11y; object/data URLs revoked on change/unmount.
Reject:
  - assistant answers rendered only via MessageMarkdown, never raw-HTML injection -> "no_raw_html_answer"
  - missing model/media/prompt -> Ask disabled, no request -> "guarded_not_ready"
Accept: Given a picked image/video + a Gemini model, When the operator asks multiple questions, Then each answer renders as markdown in the role=log thread with the media attached, over /api/gw/v1/chat/completions content parts, XSS-safe.
Assumptions: none material — biggest risk: an XSS via model answer markdown; mitigated by MessageMarkdown (no rehype-raw), confirmed by security review.

---

## 3 · CONTRACT — freeze the shape

```
VisionWorkspace (components/vision/) multimodal inspector:
  media zone -> image/*|video/* drop/upload -> data URL preview (<img>/<video>, revoked); 20MB advisory; 413 -> friendly error
  thread     -> role="log"; per turn {prompt (+media thumb 1st turn), assistant via MessageMarkdown}; multi-turn over same media
  controls   -> Gemini model picker (GET /admin/catalog/models, narrowed); per-response {model,tokens,latency}
  ask        -> POST /v1/chat/completions {messages:[{role:user, content:[{type:text},{type:image_url|video_url, ...:{url:dataURL}}]}], stream:false}; disabled until model+media+prompt
lib/vision.ts: askVision UNCHANGED + askVisionWithMeta (content + usage).
Pass-through over /api/gw/v1/chat/completions — no gateway change.
```

`Least-sure flag surfaced at freeze:` [contract] answers must render only through MessageMarkdown (no raw HTML); if wrong, a model answer could XSS — pinned by security review + the MessageMarkdown-only path.
Status: FROZEN @ v1 — approved by Tin Dang (project-lead autonomous approval under the standing "ship all playground features" goal; reuses approved Console language)

---

## 4 · TESTS — failing-first (red)

Plan: 8 original behaviors preserved (incl askVision image_url/video_url part shapes) + 4 new (multi_turn_thread, media_preview_image, media_preview_video, inspector_shows_token_count) in `apps/dashboard/tests-bff/vision-workspace.test.tsx`. Red before build, green after.
Tests live in: `apps/dashboard/tests-bff/vision-workspace.test.tsx`

---

## 5 · BUILD — AI writes code

Scope (may touch): `apps/dashboard/components/vision/` `apps/dashboard/lib/vision.ts` `apps/dashboard/app/(app)/app/vision/page.tsx` `apps/dashboard/tests-bff/vision-workspace.test.tsx`
Strategy & known-problem fixes: red tests → VisionWorkspace rebuild (3-panel: topbar/thread+input/inspector) + askVisionWithMeta → green; trap: answer XSS (dodged via MessageMarkdown-only); trap: stale async result (dodged via askSeqRef guard); trap: error rollback (optimistic user turn rolled back, prompt restored).
Strategy actually used: as planned (worktree-isolated agent; askVision left byte-identical).
Code lives in: `apps/dashboard/components/vision/`   ·   Constraints: change no test, no contract; no new deps.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build (12/12 vision; full suite 897/0)
- [x] green was EARNED — agent self-verify + independent security review (vision surface CLEAN: answers via MessageMarkdown no rehype-raw, media preview from user data URL only, original image_url/video_url part shapes intact)
- [x] no exposed secrets, injection openings, or unexpected dependencies (security review = PASS; BFF-only)

Build expectations (from §1 Accept + §3 CONTRACT): media preview + multi-turn markdown Q&A thread + model picker + per-response metadata — confirmed by vision-workspace.test.tsx + the combined-tree suite 897/0.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (orchestrator-driven; independent security review PASS, no blockers) · date: 2026-06-30
<!-- OBSERVE: [SPEC · open] vision MIME guard before readAsDataURL (security audit NIT, defensive UX, not an XSS vector); add a future delta. -->
