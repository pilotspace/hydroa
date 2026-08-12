# TASK: Dashboard /app/artifacts: upload + list + download + delete via BFF

slug: artifacts-ui · created: 2026-06-26 · stage: production
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
  - `apps/dashboard/lib/artifacts.ts` (NEW) — typed BFF client over /v1/artifacts: listArtifacts(), createArtifact(name, content_type, content_base64), downloadArtifact(id) → Blob, deleteArtifact(id). Uses bffGet/bffPost/bffDelete + a raw fetch for the binary download.
  - `apps/dashboard/components/artifacts/ArtifactsWorkspace.tsx` (NEW) — an upload form (file input → FileReader.readAsDataURL → strip the `data:...;base64,` prefix → POST) + a list (name/type/size/created, four states) + per-item Download (triggers a Blob download) + Delete. Mirrors v44 MemoryWorkspace.
  - `apps/dashboard/app/(app)/app/artifacts/page.tsx` (NEW) — mirrors app/(app)/app/memory/page.tsx.
  - `apps/dashboard/components/ui/app-shell.tsx` (MODIFY) — NAV_ITEMS += { href: "/app/artifacts", label: "Artifacts", icon: <a lucide icon, e.g. FolderArchive/Files> } (role-open).
  - `apps/dashboard/app/api/gw/[...path]/route.ts` (MODIFY — small, additive, SECURITY-ADJACENT) — the buffered fallback (lines ~227-236) currently does `upstream.json()` then `NextResponse.json(...)`, which COERCES a non-JSON body (an artifact download: text/plain, application/octet-stream, image/*, …) to `null`. ADD a branch BEFORE that fallback: if the upstream is non-204, non-401, non-JSON, and not streamed → buffer `upstream.arrayBuffer()` and return it verbatim with the upstream Content-Type + Content-Disposition (dropping hop-by-hop + set-cookie, exactly like the streamed path). The JSON path + the streamed path + the 401/204 paths stay byte-identical.
  - `apps/dashboard/tests-bff/artifacts-workspace.test.tsx` (NEW) — vitest+jsdom+MSW; mirror memory-workspace tests.
  - `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (MODIFY) — bump the nav counts for the new Artifacts item (maintenance, like memory did).
  - `apps/dashboard/tests-bff/mocks/handlers.ts` (MODIFY) — add a default GET /v1/artifacts handler ({data:[]}) so the workspace mounts in shared tests.
Context (working folder):
  - BFF client lib/bff-client.ts: bffGet/bffPost/bffDelete (credentials:"include"; cookie→Bearer→tenant in the BFF; throw BffError). All gateway calls via /api/gw.
  - The BFF binary-download gap is REAL (route.ts:229-236 → `upstream.json()` → null on non-JSON). The fix is required for the download to work; it is additive and does not alter auth (cookie→Bearer happens before the upstream call) or the 401-clear-cookie / set-cookie-strip behavior.
  - Backend (v45 t1, DONE): POST /v1/artifacts {name, content_type, content_base64} → 201 {id,name,content_type,size_bytes,created_at}; GET /v1/artifacts → {data:[{id,name,content_type,size_bytes,created_at}], limit, offset}; GET /v1/artifacts/{id} → raw bytes + Content-Type + Content-Disposition: attachment; DELETE /v1/artifacts/{id} → 204. Tenant-scoped server-side.
  - Nav filtering: visibleItems drops minRole==="admin" for members; Artifacts is role-OPEN (no minRole), like Memory/Voice/Chat.
Honors (patterns / conventions):
  - All gateway calls via the BFF; the FE never sends a tenant id (cookie-scoped). WCAG-AA + v23/v24 tokens + the four states (mirror MemoryWorkspace).
  - DESIGN-FOR-FAILURE: a list/upload/download/delete failure shows a non-blocking error state; never an unhandled throw.
  - Additive: no change to existing surfaces beyond the one nav entry + the nav-count test + the additive BFF binary branch.
Anchors the contract cites:
  - `lib/artifacts.ts` client fns · `ArtifactsWorkspace` · the /app/artifacts route · the NAV_ITEMS Artifacts entry · the BFF binary-passthrough branch.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a `/app/artifacts` dashboard surface — upload a file, see the tenant's files, download a file back, and delete one — over the v45 `/v1/artifacts` store via the BFF, plus the additive BFF binary-passthrough fix the download needs.
Framings weighed: a standalone /app/artifacts workspace mirroring v44 MemoryWorkspace (chosen — simple, discoverable, role-open) · fold files into another surface (rejected — distinct concern) · no UI (rejected — the milestone exit criterion requires a dashboard surface). Download UX: fetch→Blob→objectURL anchor click (chosen — testable in jsdom, shows in-app errors) · a plain `<a download href={bff}>` (rejected — harder to surface errors, still needs the same BFF fix).
Must:
<must>
  - M1 — `/app/artifacts` lists the tenant's artifacts (newest first) with loading/empty/error/list states, read via the BFF (GET /v1/artifacts); each row shows name, content_type, size, created_at.
  - M2 — an upload control reads a chosen File as base64 (FileReader.readAsDataURL → strip the data-URL prefix) and POSTs {name, content_type, content_base64}; the new artifact appears in the list.
  - M3 — a per-row Download fetches GET /v1/artifacts/{id} as a Blob and triggers a browser download (objectURL + anchor) with the artifact name.
  - M4 — a per-row Delete (DELETE /v1/artifacts/{id}) removes it from the list.
  - M5 — an "Artifacts" nav entry (role-open) routes to /app/artifacts.
  - M6 — BFF binary passthrough: GET /api/gw/v1/artifacts/{id} returns the raw upstream bytes + Content-Type + Content-Disposition (not coerced to null JSON); the JSON / streamed / 401 / 204 paths stay byte-identical.
</must>
Reject:
<reject>
  - a list/upload/download/delete call fails (BffError) -> a non-blocking error state; never an unhandled throw.
  - no file chosen (upload) -> the form no-ops / disables submit (no empty request).
</reject>
After:
<after>
  - A signed-in user can, in /app/artifacts, upload a file, see their list, download a file back (exact bytes), and delete one; other dashboard surfaces are unchanged; non-JSON BFF responses now pass through verbatim.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the BFF binary-passthrough change — lowest confidence because route.ts is the shared auth boundary ALL gateway traffic flows through; a careless edit could alter the JSON/stream/401 paths. Mitigation: the new branch is added AFTER the 401/204/stream branches and BEFORE the json() fallback, gated on `!isJson && !shouldStream`; it drops set-cookie + hop-by-hop exactly like the streamed path; a vitest asserts a JSON response is still JSON (regression) AND a binary response passes through. Cost if wrong: a proxy regression for all dashboard traffic. (Reviewed by me directly — security-adjacent.)
  - [x] FileReader.readAsDataURL → base64 in jsdom — CONFIRMED (jsdom supports it; tests stub a small file).
  - [x] bffGet/bffPost/bffDelete exist — CONFIRMED.
  - [ ] objectURL download in jsdom — URL.createObjectURL is stubbed in tests; the test asserts the fetch + anchor target, not an actual OS download.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: List renders the tenant's artifacts
  Given the BFF returns two artifacts for GET /v1/artifacts
  When /app/artifacts mounts
  Then both names render (newest first) with type/size

Scenario: Upload a file
  Given a chosen small file
  When the user submits the upload
  Then POST /v1/artifacts is called with {name, content_type, content_base64} and the new artifact appears in the list

Scenario: Download a file
  Given an artifact in the list
  When the user clicks Download
  Then GET /v1/artifacts/{id} is fetched and a Blob download is triggered with the artifact name

Scenario: Delete a file
  Given an artifact in the list
  When the user deletes it
  Then DELETE /v1/artifacts/{id} is called and it disappears from the list

Scenario: A failed call is non-blocking (rejection)
  Given GET /v1/artifacts returns 500
  When /app/artifacts mounts
  Then an error state is shown and the page does not crash

Scenario: No file no-ops (rejection)
  Given no file is chosen
  When the user submits the upload
  Then no request is sent (submit disabled / no-op)

Scenario: BFF passes a binary download through verbatim
  Given the gateway returns text/plain bytes for GET /v1/artifacts/{id}
  When the BFF proxies it
  Then the client receives the exact bytes + Content-Type (NOT null JSON)
  And a GET that returns application/json is still returned as JSON (unchanged)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
lib/artifacts.ts (BFF client, all via /api/gw, cookie-scoped to the tenant):
  type ArtifactItem = { id: string; name: string; content_type: string; size_bytes: number; created_at: string }
  listArtifacts(): Promise<{ data: ArtifactItem[] }>                       // GET /v1/artifacts
  createArtifact(name, content_type, content_base64): Promise<ArtifactItem> // POST /v1/artifacts
  downloadArtifact(id): Promise<Blob>                                      // GET /v1/artifacts/{id} (raw fetch, res.blob())
  deleteArtifact(id): Promise<void>                                        // DELETE /v1/artifacts/{id}

ArtifactsWorkspace (client component):
  - On mount: listArtifacts() → four states (loading/empty/error/list).
  - Upload: <input type="file"> → FileReader.readAsDataURL → split(",")[1] = base64; name=file.name, content_type=file.type||"application/octet-stream";
    submit disabled until a file is chosen; on success prepend to the list; on BffError → non-blocking ErrorState.
  - Download: downloadArtifact(id) → URL.createObjectURL(blob) → anchor.download=name → click → revokeObjectURL; on error → ErrorState.
  - Delete: deleteArtifact(id) → drop from list; on error → ErrorState.

BFF (app/api/gw/[...path]/route.ts) — ADDITIVE binary branch, inserted after the 204/401/stream branches, before the json() fallback:
  const isBinaryPassthrough = !isJson && !shouldStream && upstream.body != null
  if (isBinaryPassthrough) {
    const buf = await upstream.arrayBuffer()
    const headers = new Headers(upstream.headers); for (h of HOP_BY_HOP_HEADERS) headers.delete(h); headers.delete("set-cookie")
    return new NextResponse(buf, { status: upstream.status, headers })
  }
  // unchanged: the json() fallback, the streamed path, the 401 clear-cookie, the 204.
```

Status: FROZEN @ v1 — auto-approved EXCEPT the BFF route.ts change (shared auth boundary), which is built minimal+additive to the rule above + reviewed by me directly at the gate (diff read in full). Full-auto; FE additive; mirrors v44 memory-ui. 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] BFF binary passthrough — route.ts is the shared boundary ALL gateway traffic flows through; the risk is regressing the JSON/stream/401 paths. Mitigation: the branch is gated on `!isJson && !shouldStream`, drops set-cookie + hop-by-hop like the streamed path, leaves every other path byte-identical; a vitest asserts JSON-stays-JSON AND binary-passes-through; I read the diff. Cost if wrong: proxy regression for all dashboard traffic.
  - [test] download in jsdom — URL.createObjectURL / anchor click aren't real in jsdom; the test stubs createObjectURL + asserts the fetch URL + the anchor download name, not an OS download. Cost if wrong: the download test is shallow (mitigated: the BFF passthrough test covers the byte path; the FE test covers the wiring).
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
  - test_list_renders: MSW returns 2 artifacts → both names render.
  - test_upload: choose a small file → submit → POST /v1/artifacts asserted (body has name + content_base64) → appears in list.
  - test_download: click Download → GET /v1/artifacts/{id} fetched; createObjectURL stubbed → asserted called + anchor download name = artifact name.
  - test_delete: delete → DELETE /v1/artifacts/{id} asserted → gone from list.
  - test_list_failure_nonblocking: GET → 500 → role="alert" ErrorState, component stays mounted.
  - test_no_file_noops: submit with no file → no POST (button disabled / no-op).
  - test_nav_role_filter (in nav-role-filter.test.tsx): Artifacts entry present for all roles; counts bumped.
  - BFF passthrough is covered by the FE download test (MSW returns text/plain bytes through the mocked /api/gw path) + a focused assertion that the client receives a Blob, not null.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/artifacts-workspace.test.tsx` + `apps/dashboard/tests-bff/nav-role-filter.test.tsx` · MUST run red before Build. (Run via `node_modules/.bin/vitest run`.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/artifacts.ts` · `apps/dashboard/components/artifacts/` · `apps/dashboard/app/(app)/app/artifacts/` · `apps/dashboard/components/ui/app-shell.tsx` · `apps/dashboard/app/api/gw/[...path]/route.ts` · `apps/dashboard/tests-bff/artifacts-workspace.test.tsx` · `apps/dashboard/tests-bff/nav-role-filter.test.tsx` · `apps/dashboard/tests-bff/mocks/handlers.ts`
Strategy (ordered batches): 1. lib/artifacts.ts client. 2. ArtifactsWorkspace + page + nav entry. 3. the additive BFF binary branch in route.ts. 4. tests (workspace + nav bump + handlers default). Write tests first (red), then build.
Safety rule (feature-specific): the BFF branch is ADDITIVE — inserted after 204/401/stream, before the json() fallback, gated on `!isJson && !shouldStream`; drop set-cookie + hop-by-hop; leave the JSON/stream/401/204 paths byte-identical. The FE never sends a tenant id. Every BFF call has a .catch → non-blocking ErrorState.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; npm allow-list only (lucide already present, no new deps); ask if unclear.
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — artifacts-workspace 11/11 + nav-role-filter 5/5; FULL dashboard suite 592/592 (was 581; +11). The 3 BFF infra files (proxy/route-handlers/streaming-bff) re-run explicitly = 25/25 green AFTER the route.ts change.
- [x] coverage did not decrease — 11 new behavioral tests; nav test updated in lockstep.
- [x] no test or contract was altered during build — new tests + an additive default MSW handler (GET /v1/artifacts → {data:[]}); §3 contract unchanged.
- [x] the green was EARNED — I read lib/artifacts.ts + ArtifactsWorkspace + the route.ts diff in full. Download fetches /api/gw/v1/artifacts/{id} (raw fetch, res.blob()); list/create/delete via bff-client; the download test stubs createObjectURL + anchor.click and asserts the fetch URL + download name (honest about the jsdom limit, flagged at freeze). No vacuous asserts.
- [x] concurrency / timing safe — N/A (no streaming); independent best-effort promises with .catch.
- [x] no exposed secrets / injection / unexpected deps — grep confirms NO tenant_id in lib/artifacts.ts or components/artifacts/; all calls same-origin via /api/gw; no new npm deps (lucide FolderArchive already present).
- [x] layering & dependencies follow CONVENTIONS.md — mirrors v44 MemoryWorkspace + lib/memories; reuses Empty/Error/Loading/Button primitives; one role-open nav entry.
- [x] reviewed — full-auto self-review per Tin's directive: I PERSONALLY reviewed the security-adjacent BFF diff (route.ts:227-237) — the branch is inserted after stream/401/204, before the json() fallback, gated `!isJson && !shouldStream && body`, drops hop-by-hop + set-cookie; the 401/204/stream/json paths are byte-identical. RULED OUT the problem+json edge: gateway errors are application/problem+json (so isJson=false → they now pass through as raw bytes), but bff-client parses error bodies via res.json() UNCONDITIONALLY (content-type-agnostic, lib/bff-client.ts:68,78) → existing error handling is unaffected; arguably more correct (preserves exact bytes + real content-type). 25 BFF infra tests confirm no regression. (Outward PR/push deferred.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] list renders the tenant's artifacts (four states) — test_list_renders + the loading/empty/error states.
- [x] upload reads a File → base64 → POST {name, content_base64} and the item appears — the upload test asserts the POST body + the new row.
- [x] download fetches /api/gw/v1/artifacts/{id} and triggers a Blob download with the artifact name — the download test (createObjectURL stubbed + anchor.download asserted) + the lib test asserting a Blob is returned (not null).
- [x] the BFF passes non-JSON bytes through verbatim — route.ts binary branch returns upstream.arrayBuffer() with the upstream Content-Type; 25 BFF infra tests green; bff-client unaffected.
- [x] delete removes the row; a failed call is non-blocking — delete test + list_failure_nonblocking (500 → role="alert", component stays mounted).
- [x] Artifacts nav entry is role-open + routes — nav-role-filter counts bumped (member 7→8, admin/owner/unknown 15→16) + the /app/artifacts route.

### Deep checks
- [x] WIRING (code) — lib/artifacts fns consumed by ArtifactsWorkspace; the page renders it; NAV_ITEMS has the Artifacts entry; the BFF binary branch is reached for the download; 11 tests exercise list/upload/download/delete/failure/no-op end-to-end (MSW).
- [x] DEAD-CODE (code) — no orphaned symbol; tsc 0 + eslint 0 on touched files.
- [x] SEMANTIC — read ArtifactsWorkspace + lib/artifacts + the route.ts diff in full; honest download test + best-effort + BFF-only + no-regression confirmed.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto (Tin's "complete all milestones in auto mode"); the security-adjacent BFF diff reviewed directly · date: 2026-06-26

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
