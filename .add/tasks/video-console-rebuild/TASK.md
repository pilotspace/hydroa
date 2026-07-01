# TASK: Console-grade video studio rebuild

slug: video-console-rebuild · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — built in a worktree-isolated agent (parallel multi-milestone build), reconciled to the
> milestone line. Floor held: FROZEN §3 · red tests before build · recorded §6 gate (security review = PASS).

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `apps/dashboard/components/video/VideoWorkspace.tsx` · `apps/dashboard/lib/video.ts` (createVideoJob+optional params/getVideoJob/listVideoJobs) · `apps/dashboard/app/(app)/app/video/page.tsx`
Context (working folder): `apps/dashboard/tests-bff/video-workspace.test.tsx` (11 → 13); CONSUMES lib/artifacts.ts downloadArtifact (owned by artifacts task — NOT edited here); reuses chat Console language + lib/hooks/use-catalog-models.
Honors (patterns / conventions): BFF-only `/api/gw/v1/video/generations*` + `/v1/artifacts/{id}` (download); video preview from blob object URL only; four UI states + WCAG 2.2 AA; pass-through (no gateway change).
Anchors the contract cites: VideoWorkspace, lib/video.ts (createVideoJob+params), downloadArtifact (consumed), /v1/video/generations*

---

## 1 · SPECIFY — the rules

Feature: Console-grade video generation studio — prompt+params composer, a job gallery with rich status, inline video preview on success, download.
Must:
  - Composer: catalog-narrowed model picker + prompt + optional params (duration/aspect) + Generate (disabled until model+prompt); a job gallery (newest-first) of cards with status badges (queued/running/succeeded/failed), prompt excerpt, model, timestamps; on succeeded an INLINE <video> preview (downloadArtifact(result_artifact_id)->blob->objectURL, revoked) + download; friendly reason on failed (special-case no_video_provider_configured); polling (pollIntervalMs prop, stop when all terminal/unmount); soft poll-error banner keeps last-good; initial-load error inline.
  - Preserve all 11 original behaviors; do NOT edit lib/artifacts.ts (consume downloadArtifact as-is); four UI states; one h1 "Video"; a11y; object URLs revoked on unmount.
Reject:
  - missing model/prompt -> Generate disabled, no POST -> "guarded_empty_generate"
  - video preview only from blob object URLs (fetched), never untrusted URL -> "blob_only_preview"
Accept: Given the studio, When the operator generates a video, polls to success, previews inline and downloads, Then it works over /api/gw/v1/video/generations* + downloadArtifact, polling stops at terminal, with a blob-only preview.
Assumptions: none material — biggest risk: a poll loop that never stops (leak); mitigated by terminal/unmount stop + injected pollIntervalMs tests.

---

## 3 · CONTRACT — freeze the shape

```
VideoWorkspace (components/video/) generation studio:
  composer -> model picker (useCatalogModels+narrowModels) + prompt + optional params -> POST /v1/video/generations {model,prompt,params?} (params omitted when empty -> byte-identical {model,prompt})
  gallery  -> GET /v1/video/generations (newest-first) cards: status badge, prompt excerpt, model, time
  poll     -> GET /v1/video/generations/{id} every pollIntervalMs; STOP when all terminal/unmount; soft error keeps last-good
  succeed  -> inline <video> via downloadArtifact(result_artifact_id)->blob->objectURL (revoked) + download
  fail     -> friendly reason (no_video_provider_configured special-cased)
lib/video.ts: createVideoJob(+optional params, serialized only when non-empty). lib/artifacts.ts: UNCHANGED (downloadArtifact consumed).
Pass-through over /api/gw/v1/video/generations* — no gateway change.
```

`Least-sure flag surfaced at freeze:` [contract] the poll loop MUST stop at terminal/unmount (no runaway) and preview only blob URLs; if wrong, a leak or untrusted-URL render — pinned by polling_stops + blob-only tests.
Status: FROZEN @ v1 — approved by Tin Dang (project-lead autonomous approval under the standing "ship all playground features" goal; reuses approved Console language)

---

## 4 · TESTS — failing-first (red)

Plan: 11 original behaviors preserved + 2 new (inline video player on success, optional generation params) in `apps/dashboard/tests-bff/video-workspace.test.tsx`. Red before build, green after.
Tests live in: `apps/dashboard/tests-bff/video-workspace.test.tsx`

---

## 5 · BUILD — AI writes code

Scope (may touch): `apps/dashboard/components/video/` `apps/dashboard/lib/video.ts` `apps/dashboard/app/(app)/app/video/page.tsx` `apps/dashboard/tests-bff/video-workspace.test.tsx`
Strategy & known-problem fixes: red tests → VideoWorkspace two-panel rebuild (composer + gallery with inline player) + createVideoJob params → green; trap: double-fetch of blobs (dodged via videoUrlsRef+loadingJobsRef); trap: object-URL leak (revoked on unmount); trap: breaking {model,prompt} callers (params serialized only when non-empty).
Strategy actually used: as planned (worktree-isolated agent; lib/artifacts.ts confirmed untouched).
Code lives in: `apps/dashboard/components/video/`   ·   Constraints: change no test, no contract; no new deps; do NOT edit lib/artifacts.ts.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build (13/13 video; full suite 897/0)
- [x] green was EARNED — agent self-verify + independent security review (video surface CLEAN: <video> from blob object URLs only, revoked on unmount, friendlyError plain string, original assertions intact)
- [x] no exposed secrets, injection openings, or unexpected dependencies (security review = PASS; BFF-only; lib/artifacts.ts untouched)

Build expectations (from §1 Accept + §3 CONTRACT): composer + job gallery with rich status + inline blob video preview + download, polling stops at terminal — confirmed by video-workspace.test.tsx + the combined-tree suite 897/0.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (orchestrator-driven; independent security review PASS, no blockers) · date: 2026-06-30
<!-- OBSERVE: [SPEC · open] none material; surface complete. -->
