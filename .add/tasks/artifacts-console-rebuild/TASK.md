# TASK: Console-grade artifact library rebuild

slug: artifacts-console-rebuild · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — built in a worktree-isolated agent (parallel multi-milestone build), reconciled to the
> milestone line. Floor held: FROZEN §3 · red tests before build · recorded §6 gate (security review = PASS).

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `apps/dashboard/components/artifacts/ArtifactsWorkspace.tsx` · `apps/dashboard/lib/artifacts.ts` (listArtifacts/createArtifact/downloadArtifact/deleteArtifact + new fetchArtifactText) · `apps/dashboard/app/(app)/app/artifacts/page.tsx`
Context (working folder): `apps/dashboard/tests-bff/artifacts-workspace.test.tsx` (11 original behaviors → 23); lib/artifacts.ts is SHARED with video (downloadArtifact signature MUST stay stable); reuses chat Console language + tokens.
Honors (patterns / conventions): BFF-only `/api/gw/v1/artifacts*`; XSS-safe previews (escaped text/JSON, blob object URLs only, no raw-HTML injection); four UI states + WCAG 2.2 AA; pass-through (no gateway change).
Anchors the contract cites: ArtifactsWorkspace, lib/artifacts.ts (downloadArtifact stable + fetchArtifactText), /v1/artifacts*

---

## 1 · SPECIFY — the rules

Feature: Console-grade artifact library — file-manager with typed list + search/sort, inline image/text/JSON preview, drag-drop + picker upload, download, delete.
Must:
  - List/grid with type icons, human sizes, timestamps + name/type search-filter + sort (name/size/date, asc/desc); preview pane: image → blob objectURL <img>; text/* + application/json → fetched, size-capped (256 KB), ESCAPED <pre> (no raw-HTML injection); other → download affordance; guarded delete; upload via picker AND drag-drop (FileReader→base64→POST), refreshes list.
  - Preserve all 11 original behaviors; downloadArtifact signature unchanged (shared with video); four UI states; list/preview failures non-blocking; object URLs revoked on change/unmount; one h1 "Artifacts"; a11y.
Reject:
  - text/JSON preview must be escaped React text, never raw-HTML injection -> "no_raw_html_preview"
  - no file selected -> upload disabled, no POST -> "guarded_empty_upload"
Accept: Given two artifacts, When the operator searches/sorts, selects one to preview (image/text/JSON), uploads via drag-drop, downloads and deletes, Then each works over /api/gw/v1/artifacts* with XSS-safe previews and a stable downloadArtifact.
Assumptions: none material — biggest risk: a preview XSS via untrusted artifact bytes; mitigated by escaped <pre> + blob-only object URLs (confirmed by security review).

---

## 3 · CONTRACT — freeze the shape

```
ArtifactsWorkspace (components/artifacts/) file-manager:
  list   -> GET /v1/artifacts?limit&offset (metadata) + client search/sort
  preview-> image: downloadArtifact(id)->blob->objectURL <img> (revoked) · text/*|json: fetchArtifactText(id)->escaped <pre> (256KB cap) · else download affordance
  upload -> picker + drag-drop -> FileReader base64 -> POST /v1/artifacts {name,content_type,content_base64}; disabled w/o file
  download -> downloadArtifact(id) (UNCHANGED signature) -> blob -> objectURL anchor (revoked) · delete -> guarded DELETE /v1/artifacts/{id}
lib/artifacts.ts: + fetchArtifactText(id):Promise<string> (res.text path); downloadArtifact unchanged.
Pass-through over /api/gw/v1/artifacts* — no gateway change.
```

`Least-sure flag surfaced at freeze:` [contract] previews must never inject raw HTML (escaped text + blob object URLs only); if wrong, a malicious artifact could XSS — pinned by security review + escaped-<pre> tests.
Status: FROZEN @ v1 — approved by Tin Dang (project-lead autonomous approval under the standing "ship all playground features" goal; reuses approved Console language)

---

## 4 · TESTS — failing-first (red)

Plan: 11 original behaviors preserved + 12 new (search filter, sort name/size/date + asc/desc, image preview objectURL, text preview, JSON pretty preview, drag-drop upload, delete guard confirm/cancel) in `apps/dashboard/tests-bff/artifacts-workspace.test.tsx`. Red before build, green after.
Tests live in: `apps/dashboard/tests-bff/artifacts-workspace.test.tsx`

---

## 5 · BUILD — AI writes code

Scope (may touch): `apps/dashboard/components/artifacts/` `apps/dashboard/lib/artifacts.ts` `apps/dashboard/app/(app)/app/artifacts/page.tsx` `apps/dashboard/tests-bff/artifacts-workspace.test.tsx`
Strategy & known-problem fixes: red tests → ArtifactsWorkspace rebuild + fetchArtifactText → green; trap: preview XSS (dodged via escaped <pre> + blob-only object URLs + 256KB cap); trap: breaking video's downloadArtifact (dodged by keeping signature stable).
Strategy actually used: as planned (worktree-isolated agent; lib/artifacts.ts downloadArtifact confirmed unchanged).
Code lives in: `apps/dashboard/components/artifacts/`   ·   Constraints: change no test, no contract; no new deps.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no test or contract altered during build (23/23 artifacts; full suite 897/0)
- [x] green was EARNED — agent self-verify + independent security review (artifacts preview surface CLEAN: escaped <pre>, blob-only object URLs revoked, 256KB cap, original assertions intact)
- [x] no exposed secrets, injection openings, or unexpected dependencies (security review = PASS; BFF-only; downloadArtifact signature stable)

Build expectations (from §1 Accept + §3 CONTRACT): file-manager list+search+sort, image/text/JSON preview (XSS-safe), drag-drop upload, download+delete — confirmed by artifacts-workspace.test.tsx + the combined-tree suite 897/0.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (orchestrator-driven; independent security review PASS, no blockers) · date: 2026-06-30
<!-- OBSERVE: [SPEC · open] none material; surface complete. -->
