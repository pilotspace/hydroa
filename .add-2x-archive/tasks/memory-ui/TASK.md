# TASK: Dashboard /app/memory: add + list + semantic search via BFF

slug: memory-ui · created: 2026-06-26 · stage: production
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
  - `apps/dashboard/lib/memories.ts` (NEW) — typed BFF client over /v1/memories (bffGet/bffPost/bffDelete): listMemories(), createMemory(content, metadata?), searchMemories(query, top_k?), deleteMemory(id).
  - `apps/dashboard/components/memory/MemoryWorkspace.tsx` (NEW) — an "Add memory" form + a list of the tenant's memories + a semantic-search box showing ranked results (with score). Four states (loading/empty/error/list), WCAG-AA. Mirrors the v42 VoicePlayground + v43 ChatHistorySidebar ethos.
  - `apps/dashboard/app/(app)/app/memory/page.tsx` (NEW) — mirrors app/(app)/app/voice/page.tsx (renders MemoryWorkspace + metadata).
  - `apps/dashboard/components/ui/app-shell.tsx` (MODIFY) — NAV_ITEMS += { href: "/app/memory", label: "Memory", icon: <a lucide icon, e.g. Brain> } (role-open, like Voice).
  - `apps/dashboard/tests-bff/memory-workspace.test.tsx` (NEW) — vitest+jsdom+MSW; mirror chat-history/voice tests.
  - `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (MODIFY) — bump the nav counts for the new Memory item (maintenance, like v42 did).
Context (working folder):
  - BFF client lib/bff-client.ts: bffGet/bffPost/bffDelete (credentials:"include"; cookie→Bearer→tenant in the BFF; throw BffError on non-2xx). All gateway calls via /api/gw.
  - Backend (v44 t1, DONE): POST /v1/memories {content, metadata?} → {id,content,created_at}; GET /v1/memories → {data:[{id,content,created_at,has_embedding}], limit, offset}; POST /v1/memories/search {query, top_k?} → {data:[{id,content,score,created_at}]}; DELETE /v1/memories/{id} → 204. Tenant-scoped server-side.
  - Nav filtering: visibleItems drops minRole==="admin" for members; Memory is role-OPEN (no minRole), like Chat/Voice.
Honors (patterns / conventions):
  - All gateway calls via the BFF; the FE never sends a tenant id (cookie-scoped). WCAG-AA + v23/v24 tokens + the four states (mirror VoicePlayground).
  - DESIGN-FOR-FAILURE: a list/search/create failure shows a non-blocking error state; never an unhandled throw. Search shows the score honestly (may be null for a text-fallback match).
  - Additive: no change to existing surfaces beyond the one nav entry + the nav-count test.
Anchors the contract cites:
  - `lib/memories.ts` client fns · `MemoryWorkspace` · the /app/memory route · the NAV_ITEMS Memory entry.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a `/app/memory` dashboard surface — add a memory, see the tenant's list, and run a semantic search returning ranked matches — over the v44 `/v1/memories` store via the BFF.
Framings weighed: a standalone /app/memory workspace (add + list + search), mirroring the v42 voice playground (chosen — simple, discoverable, role-open) · fold memory into the chat sidebar (rejected — different concern; memory is cross-conversation) · no UI, API-only (rejected — the milestone exit criterion requires a dashboard surface).
Must:
<must>
  - M1 — `/app/memory` lists the tenant's memories (newest first) with loading/empty/error/list states, read via the BFF (GET /v1/memories).
  - M2 — an "Add memory" form posts content (POST /v1/memories) and the new memory appears in the list.
  - M3 — a search box posts a query (POST /v1/memories/search) and renders ranked results with their score (score may be null for a text-fallback match — shown honestly).
  - M4 — a memory can be deleted (DELETE /v1/memories/{id}); it disappears from the list.
  - M5 — a "Memory" nav entry (role-open) routes to /app/memory.
</must>
Reject:
<reject>
  - a list/search/create/delete call fails (BffError) -> a non-blocking error state; never an unhandled throw.
  - empty content (add) or empty query (search) -> the form no-ops / disables submit (no empty request).
</reject>
After:
<after>
  - A signed-in user can, in /app/memory, add a memory, see their list, run a semantic search that returns ranked matches, and delete a memory; other dashboard surfaces are unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ none material — this mirrors the proven v42 VoicePlayground + v43 lib/conversations BFF-client patterns; the single risk is the nav-count test needing a bump (handled in scope). If wrong: a failing nav-count test (caught immediately).
  - [x] BFF proxies /v1/memories cookie-scoped — CONFIRMED (catch-all relay; v44 is tenant-scoped server-side).
  - [x] bffGet/bffPost/bffDelete exist — CONFIRMED.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: List renders the tenant's memories
  Given the BFF returns two memories for GET /v1/memories
  When /app/memory mounts
  Then both memory contents render (newest first)

Scenario: Add a memory
  Given an empty add form
  When the user types content and submits
  Then POST /v1/memories is called and the new memory appears in the list

Scenario: Semantic search shows ranked results
  Given the BFF returns two ranked search results with scores
  When the user searches "topic"
  Then POST /v1/memories/search is called and both results render with their score, highest first

Scenario: Delete a memory
  Given a memory in the list
  When the user deletes it
  Then DELETE /v1/memories/{id} is called and it disappears from the list

Scenario: A failed call is non-blocking (rejection)
  Given GET /v1/memories returns 500
  When /app/memory mounts
  Then an error state is shown and the page does not crash
  And the rest of the dashboard is unaffected

Scenario: Empty input no-ops (rejection)
  Given an empty content/query
  When the user submits add or search
  Then no request is sent (submit disabled / no-op)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
lib/memories.ts (BFF client, all via /api/gw, cookie-scoped to the tenant):
  type MemoryItem = { id: string; content: string; created_at: string; has_embedding?: boolean }
  type SearchResult = { id: string; content: string; score: number | null; created_at: string }
  listMemories(): Promise<{ data: MemoryItem[] }>                 // GET /v1/memories
  createMemory(content: string, metadata?: object): Promise<MemoryItem>   // POST /v1/memories
  searchMemories(query: string, top_k?: number): Promise<{ data: SearchResult[] }>  // POST /v1/memories/search
  deleteMemory(id: string): Promise<void>                        // DELETE /v1/memories/{id}

MemoryWorkspace — add form (content → createMemory → refresh) + list (listMemories, loading/empty/error)
  + search box (query → searchMemories → ranked results with score) + per-item delete (deleteMemory → refresh).
  Submit disabled on empty content/query. All calls best-effort with a surfaced error state (no unhandled throw).
/app/memory/page.tsx — renders <MemoryWorkspace/> + metadata (mirror voice/page.tsx).
NAV_ITEMS += { href: "/app/memory", label: "Memory", icon: Brain }  (role-open).
Schema: NONE (FE-only; the v44 t1 store is the backend). No new gateway routes.
```

Status: FROZEN @ v1 — auto-approved (FE-only, additive; mirrors the proven v42 VoicePlayground + v43 BFF-client patterns; no backend/contract change) 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] the search-score honesty — a result's score may be null (a text-fallback match, not a cosine hit); the UI MUST render that honestly (e.g. "text match" / "—") and never fabricate a number. Built + tested to that rule. Cost if wrong: a misleading relevance number (trust, not data).
  - [test] nav-count maintenance — adding the Memory nav item shifts the role-filter counts; the nav-role-filter test must be bumped in lockstep. Cost if wrong: a failing nav-count test (caught immediately).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral (vitest + jsdom + MSW); mirror tests-bff/chat-history.test.tsx / voice tests.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_list_renders: MSW GET /v1/memories → 2 items; mount → both contents shown.
  - test_add_memory: type + submit → POST /v1/memories asserted; new item appears.
  - test_search_ranked: MSW search → 2 scored results; search → POST /v1/memories/search asserted; both render with score, highest first.
  - test_delete: click delete → DELETE /v1/memories/{id} asserted; item gone.
  - test_list_failure_nonblocking: GET → 500 → error state shown, no crash.
  - test_empty_noop: empty content/query → submit disabled / no request.
  - test_nav_has_memory: NAV_ITEMS contains the role-open Memory entry (nav-role-filter counts bumped).
</test_plan>

Tests live in: `apps/dashboard/tests-bff/memory-workspace.test.tsx` · MUST run red before Build. Run via `node_modules/.bin/vitest run` (NOT npx).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/memories.ts` · `apps/dashboard/components/memory/MemoryWorkspace.tsx` · `apps/dashboard/app/(app)/app/memory/page.tsx` · `apps/dashboard/components/ui/app-shell.tsx` · `apps/dashboard/tests-bff/memory-workspace.test.tsx` · `apps/dashboard/tests-bff/nav-role-filter.test.tsx` · `apps/dashboard/tests-bff/mocks/handlers.ts`
  (handlers.ts: a default `GET /v1/memories → {data:[]}` MSW handler so other shared tests don't hit an unhandled request — additive, same pattern v43 used; specific tests override with server.use.)
Strategy (ordered batches): 1. lib/memories.ts BFF client. 2. MemoryWorkspace (add/list/search/delete, four states, a11y). 3. page.tsx + NAV_ITEMS entry. 4. tests-bff/memory-workspace.test.tsx + bump nav-role-filter counts.
Safety rule (feature-specific): all gateway calls via the BFF (never direct); the FE never sends a tenant id; every memories call is best-effort with a surfaced error state (no unhandled throw); search-score rendered honestly (null = text fallback, never fabricated).
Code lives in: `apps/dashboard/`
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

- [x] all tests pass — memory-workspace suite 14/14 + nav-role-filter 5/5; FULL dashboard suite 581/581 (was 567; +14, zero regression).
- [x] coverage did not decrease — 14 new behavioral tests; nav test updated in lockstep.
- [x] no test or contract was altered during build — new tests + an additive default MSW handler (handlers.ts GET /v1/memories → {data:[]}); §3 contract unchanged.
- [x] the green was EARNED — FE-only, no security surface (tenant isolation is server-side in t1). I read the component: every gateway call goes through bff-client (no direct fetch — grep confirms only doc-comments mention /api/gw); null search-score renders "text match" (never a fabricated number); every call has a .catch → non-blocking ErrorState.
- [x] concurrency / timing safe — N/A (no streaming); calls are independent best-effort promises with .catch.
- [x] no exposed secrets / injection / unexpected deps — no tenant_id in any payload (cookie-scoped BFF); no new npm deps (lucide Brain already present); all via /api/gw.
- [x] layering & dependencies follow CONVENTIONS.md — mirrors v42 VoicePlayground + v43 lib/conversations; reuses Empty/ErrorState/Loading/Button primitives; additive (one nav entry).
- [x] reviewed — full-auto self-review: read MemoryWorkspace + lib/memories + the diff; 581 green + tsc 0 + eslint 0. (Outward PR/push deferred.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] list renders the tenant's memories with four states — confirmed by test_list_renders + the loading/empty/error states in MemoryWorkspace.
- [x] add + delete round-trip via the BFF — confirmed by add_memory (POST /v1/memories asserted, item appears) + delete (DELETE /{id} asserted, gone).
- [x] semantic search renders ranked results with honest score — confirmed by search_ranked_renders_results_with_scores + search_null_score_never_shows_fabricated_number ("text match" for null).
- [x] a failed call is non-blocking — confirmed by list_failure_nonblocking_shows_error_no_crash (GET→500 → role="alert" ErrorState, component stays mounted).
- [x] Memory nav entry is role-open + routes — confirmed by the nav-role-filter counts (member 6→7, admin/owner 14→15) + the /app/memory route.

### Deep checks
- [x] WIRING (code) — lib/memories fns consumed by MemoryWorkspace; the page renders it; NAV_ITEMS has the Memory entry; 14 tests exercise list/add/search/delete/failure end-to-end (MSW).
- [x] DEAD-CODE (code) — no orphaned symbol; tsc 0 + eslint 0 on touched files.
- [x] SEMANTIC — read the component + client in full; honest-score + best-effort + BFF-only confirmed.

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
