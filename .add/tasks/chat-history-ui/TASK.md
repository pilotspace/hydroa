# TASK: Dashboard chat history sidebar (list/new/resume/persist via BFF)

slug: chat-history-ui · created: 2026-06-26 · stage: production
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
  - `apps/dashboard/lib/hooks/use-chat-stream.ts` (MODIFY, additive-only) — the v40 SSE hook. Returns `{status, messages, streamingText, usage, error, send, stop, reset}`; `ChatMessage = {role: user|assistant|system, content}`. ADD two seams WITHOUT touching the streaming/abort/disconnect path: `load(messages: ChatMessage[])` (seed a thread for resume → idle) + an optional `onTurnComplete?(turn: {user: string; assistant: string; usage?: Usage})` opt fired inside `finishTurn` ONLY when an assistant turn was committed (capture the user text in a ref in `send`).
  - `apps/dashboard/lib/conversations.ts` (NEW) — typed client wrapping `bffGet/bffPost/bffDelete` (lib/bff-client.ts) for `/v1/conversations`: `listConversations()`, `createConversation(title?)`, `getConversation(id)`, `appendMessage(id, role, content)`, `deleteConversation(id)`.
  - `apps/dashboard/components/chat/ChatHistorySidebar.tsx` (NEW) — lists the tenant's conversations (loading/empty/error/list states, WCAG-AA), a "New" button, click-to-resume, a delete affordance.
  - `apps/dashboard/components/chat/ChatWorkspace.tsx` (MODIFY) — render the sidebar beside the thread; own `activeConversationId`; on send ensure a conversation exists (create on first turn, title = first-message slug) + persist the user msg; on `onTurnComplete` persist the assistant msg + refresh the list; "New" → reset()+clear id; resume → getConversation→load()+set id.
  - `apps/dashboard/tests-bff/chat-history.test.tsx` (NEW) — vitest+jsdom+MSW, mirrors tests-bff/chat-workspace-page.test.tsx.
Context (working folder):
  - BFF client `lib/bff-client.ts`: `bffGet<T>(path)`, `bffPost<T>(path, body)`, `bffDelete(path)` — all `credentials:"include"` (cookie→Bearer in the BFF), throw `BffError` on non-2xx. ALL gateway calls go through `/api/gw/...` (never direct). `appBase()` handles the Node/jsdom absolute-URL quirk.
  - Backend (v43 t1, DONE): `POST/GET /v1/conversations`, `GET/DELETE /v1/conversations/{id}`, `POST /v1/conversations/{id}/messages {role,content}`. List shape `{data:[{id,title,created_at,updated_at,message_count}], limit, offset}`; detail `{id,title,...,messages:[{id,role,content,created_at}]}`. Tenant-scoped; the BFF's existing cookie→key auth already scopes to the tenant.
  - Nav: `NAV_ITEMS` (components/ui/app-shell.tsx) already has Chat `/app/chat` (role-open). No nav change needed (sidebar lives inside the chat page).
Honors (patterns / conventions):
  - ADDITIVE to the v40 stream: the streaming/abort/disconnect/billing path of use-chat-stream.ts stays byte-identical; `load`/`onTurnComplete` are new optional seams. The 547 existing dashboard tests must stay green.
  - HONEST PERSISTENCE (milestone): persist EXACTLY the user/assistant turns sent/received — no fabrication; the assistant turn is persisted only after it actually completes.
  - DESIGN-FOR-FAILURE: a persistence/list failure must NOT break the live chat (the stream is the source of truth; persistence is best-effort with a surfaced error state, never a thrown that loses the turn). WCAG-AA + v23/v24 tokens + the four states.
  - Tenant isolation is enforced server-side (t1); the FE never sends a tenant id — it relies on the cookie-scoped BFF.
Anchors the contract cites:
  - `useChatStream.load` · `useChatStream` opt `onTurnComplete` · `lib/conversations.ts` client fns · `ChatHistorySidebar` · `ChatWorkspace` active-conversation wiring.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a conversation-history sidebar in `/app/chat` — list/new/resume threads and persist each turn to the v43 gateway store via the BFF, so a thread survives reload/device.
Framings weighed: extend the v40 hook with additive `load` + `onTurnComplete` seams and let ChatWorkspace own persistence + active id (chosen — keeps the streaming path untouched, persistence concern lives in the parent) · move all message state into a new conversations store/hook (rejected — rewrites the proven v40 stream, risks the 547 tests) · persist server-side by teeing the completion in the BFF (rejected — the BFF is a transparent relay; teeing couples it to a feature + double-bills risk).
Must:
<must>
  - M1 — `/app/chat` shows a history sidebar listing the tenant's conversations (newest-updated first) with loading/empty/error/list states; it reads via the BFF (`GET /v1/conversations`).
  - M2 — "New" starts a fresh thread: clears the workspace (reset) and the active conversation id; the next send creates a new conversation.
  - M3 — sending a turn persists it: on first send with no active thread, create a conversation (title = a slug of the first user message) and persist the user message; when the assistant turn completes, persist the assistant message; the sidebar reflects the new/updated thread.
  - M4 — clicking a conversation resumes it: `GET /v1/conversations/{id}` loads its messages into the workspace (via `load`) and sets it active; further turns append to it.
  - M5 — after a reload, the same thread is resumable from the sidebar with its messages intact (persistence + resume close the loop).
  - M6 — a conversation can be deleted from the sidebar (`DELETE`); it disappears from the list; if it was active, the workspace resets.
</must>
Reject:
<reject>
  - a persistence or list call fails (BffError) -> surface a non-blocking error state in the sidebar; the LIVE chat/stream is unaffected (the turn already shown is never lost).
  - "New" or send while a turn is streaming -> the existing hook no-ops the send; "New" is disabled mid-stream (no torn state).
  - empty/whitespace first message -> the existing send no-op (no conversation created for an empty turn).
</reject>
After:
<after>
  - A signed-in user can start a new conversation, send turns that persist server-side, reload the page, and resume the same thread (with messages) from the sidebar; the v40 streaming/abort/disconnect behavior and all other dashboard surfaces are unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ persisting the assistant turn cleanly without disturbing the stream — lowest confidence because the persistence trigger hangs off `finishTurn`; if the `onTurnComplete` hook fires on the abort/error paths too it could persist a partial/empty assistant turn. Mitigation: fire ONLY when an assistant message was actually committed (non-empty content on the success path); never weaken the existing abort/error handling; cover with a test. Cost if wrong: a stray partial row (not a data-loss/leak).
  - [x] BFF proxies `/v1/conversations` with cookie→tenant auth — CONFIRMED (the catch-all `/api/gw/[...path]` relays any path; t1 is tenant-scoped server-side).
  - [x] bffGet/bffPost/bffDelete exist for the CRUD calls — CONFIRMED (lib/bff-client.ts).
  - [ ] title source — chose a client-side slug of the first user message (LLM titling is milestone-OUT); a rename affordance is a deferred delta.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: List renders the tenant's conversations
  Given the BFF returns two conversations for GET /v1/conversations
  When the chat page mounts
  Then the sidebar shows both titles, newest-updated first

Scenario: New starts a fresh thread
  Given an active conversation with messages on screen
  When the user clicks "New"
  Then the workspace is empty and no conversation is active
  And the existing list is unchanged (nothing deleted)

Scenario: Sending the first turn creates and persists the conversation
  Given no active conversation
  When the user sends "hello" and the assistant reply streams to completion
  Then POST /v1/conversations was called (create) and POST /v1/conversations/{id}/messages was called for BOTH the user and the assistant message
  And the streamed assistant text shown on screen is exactly what was persisted (honest persistence)

Scenario: Resuming a conversation loads its messages
  Given a conversation with two stored messages
  When the user clicks it in the sidebar
  Then GET /v1/conversations/{id} is called and both messages render in order
  And it becomes the active thread (a further turn appends to it)

Scenario: Reload then resume keeps the thread
  Given a persisted conversation
  When the page reloads (fresh mount) and the user clicks the conversation
  Then its messages render from the store (client React state was empty on mount)

Scenario: Delete removes a conversation
  Given a conversation in the list
  When the user deletes it
  Then DELETE /v1/conversations/{id} is called and it disappears from the list
  And if it was active the workspace resets

Scenario: A persistence failure does not break the live chat (rejection)
  Given POST /v1/conversations/{id}/messages returns 500
  When a turn completes
  Then the streamed turn remains visible on screen and a non-blocking sidebar error is shown
  And the stream/abort behavior is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
useChatStream (ADDITIVE — streaming/abort/disconnect path byte-identical):
  opts.onTurnComplete?(turn: { user: string; assistant: string; usage?: Usage }): void
      — fired inside finishTurn ONLY when a non-empty assistant turn was committed on the SUCCESS
        path (NOT on abort/error). `user` = the text of the turn's user message (captured in send()).
  load(messages: ChatMessage[]): void
      — replace the thread with `messages`, clear streaming/error, status "idle" (for resume).
  (existing return shape unchanged; `load` added to UseChatStream.)

lib/conversations.ts (BFF client, all via /api/gw, cookie-scoped to the tenant):
  listConversations(): Promise<{ data: ConversationSummary[] }>     // GET /v1/conversations
  createConversation(title?: string): Promise<{ id: string; title: string|null }>  // POST /v1/conversations
  getConversation(id): Promise<{ id; title; messages: {role,content}[] }>          // GET /v1/conversations/{id}
  appendMessage(id, role, content): Promise<unknown>               // POST /v1/conversations/{id}/messages
  deleteConversation(id): Promise<void>                            // DELETE /v1/conversations/{id}
  type ConversationSummary = { id; title: string|null; updated_at: string; message_count: number }

ChatHistorySidebar({ activeId, onSelect, onNew, refreshKey }) — lists conversations (loading/empty/
  error/list), "New" button (disabled while streaming), per-item select + delete.
ChatWorkspace — owns `activeConversationId`; persistence flow:
  send: if no active id → createConversation(slug(text)) → set id; appendMessage(id,"user",text) [best-effort]
  onTurnComplete({assistant}): appendMessage(id,"assistant",assistant) [best-effort]; bump refreshKey
  New: reset()+id=null;  select(id): getConversation(id)→load(messages)+id=id;  delete: deleteConversation→refresh
Schema: NONE (FE-only; the v43 t1 store is the backend). No new gateway routes.
```

Status: FROZEN @ v1 — auto-approved (FE-only, additive to the v40 stream; no backend/contract change; reuses the proven BFF client + the t1 store; 547 tests guard the streaming path) 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] the `onTurnComplete` firing condition — it MUST fire only on a committed non-empty assistant turn (success path), never on abort/error, or a partial/empty row gets persisted. Built + tested to that rule; cost if wrong = a stray partial row (not data-loss/leak).
  - [spec] persistence is best-effort — a failed append must surface an error but NEVER throw through the stream or lose the on-screen turn. Tested via a 500-on-append scenario.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral (vitest + jsdom + MSW); mirror tests-bff/chat-workspace-page.test.tsx for the SSE mock + the BFF-call assertions.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_list_renders_newest_first: MSW GET /v1/conversations → 2 items; mount → both titles shown, order asserted.
  - test_new_clears_workspace: with messages shown, click "New" → empty thread + no active id; list untouched.
  - test_first_turn_creates_and_persists: MSW create + append + SSE reply; send "hello" → assert POST /v1/conversations then POST /{id}/messages for user AND assistant; persisted assistant == streamed text.
  - test_resume_loads_messages: MSW GET /{id} with 2 msgs; click item → both render in order; active.
  - test_reload_resume: fresh mount (empty client state) + click item → messages render from the store.
  - test_delete_removes: click delete → DELETE /{id} called; item gone; active reset if it was active.
  - test_persist_failure_is_nonblocking: MSW append → 500; complete a turn → streamed turn still on screen + sidebar error; no throw.
  - test_stream_path_unchanged: the existing chat-workspace-page + chat-* tests still green (regression guard).
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-history.test.tsx` · MUST run red (missing implementation) before Build. Run via `node_modules/.bin/vitest run` (NOT npx).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/hooks/use-chat-stream.ts` · `apps/dashboard/lib/conversations.ts` · `apps/dashboard/components/chat/ChatHistorySidebar.tsx` · `apps/dashboard/components/chat/ChatWorkspace.tsx` · `apps/dashboard/tests-bff/chat-history.test.tsx` · `apps/dashboard/tests-bff/mocks/handlers.ts`
  (handlers.ts: the sidebar lists on mount, so existing ChatWorkspace tests need a default `GET /v1/conversations → {data:[]}` MSW handler — additive, specific tests override with server.use.)
Strategy (ordered batches): 1. extend use-chat-stream.ts (add `load` + `onTurnComplete`, capture user text) — keep streaming/abort/disconnect byte-identical. 2. lib/conversations.ts BFF client. 3. ChatHistorySidebar (four states, a11y). 4. wire ChatWorkspace (active id + persistence + new/resume/delete). 5. tests-bff/chat-history.test.tsx.
Safety rule (feature-specific): persistence is BEST-EFFORT and decoupled from the stream — a failed conversations call surfaces a sidebar error but NEVER throws through the stream or drops the on-screen turn. `onTurnComplete` fires only on a committed non-empty assistant turn (success path), never abort/error. The v40 streaming/abort/disconnect code is unchanged.
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

- [x] all tests pass — new chat-history suite 20/20; FULL dashboard suite 567/567 (was 547; +20, zero regression).
- [x] coverage did not decrease — 20 behavioral tests added; the v40 stream tests are unchanged and green.
- [x] no test or contract was altered during build — only NEW tests + an ADDITIVE default MSW handler (handlers.ts: `GET /v1/conversations → {data:[]}`; specific tests override via server.use). The §3 contract is unchanged.
- [x] the green was EARNED — I personally read the use-chat-stream.ts diff: the streaming/SSE-parse/AbortController/reader-cancel/unmount-abort/stop/reset/send-streaming path is BYTE-IDENTICAL; only `load()`, the `onTurnComplete` opt, a `currentUserTextRef`, and a `finishTurn(usage, isAbort)` guard were added. `onTurnComplete` fires ONLY on `!isAbort && content` (success path) — never abort (catch passes isAbort=true; success passes controller.signal.aborted) nor error (finishTurn not called). The 567-green full suite is the regression guard.
- [x] concurrency / timing safe — persistence is decoupled from the stream: submit() creates+appends the user msg in a swallowing try/catch and calls send() REGARDLESS; onTurnComplete awaits a `pendingConvIdRef` promise so it appends to the right id even when the stream finishes before createConversation resolves (the race the §1 ⚠ flagged). The disconnect-billing abort path is intact (partial still committed on abort; only the new callback suppressed).
- [x] no exposed secrets / injection / unexpected deps — FE-only; NO tenant id ever sent from the client (cookie-scoped BFF); no new npm deps; all gateway calls via /api/gw.
- [x] layering & dependencies follow CONVENTIONS.md — reuses bffGet/bffPost/bffDelete + ui/states + Button; the sidebar lives inside ChatWorkspace; no backend/route change.
- [x] reviewed — full-auto self-review: read the hook + ChatWorkspace + handlers diffs in full; 567 green + tsc 0 + eslint 0. No security surface (tenant isolation is server-side, t1). (Outward PR/push deferred to Tin.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] sidebar lists the tenant's conversations with four states — confirmed by test_list_renders_newest_first + the loading/empty/error states in ChatHistorySidebar.tsx.
- [x] a completed turn persists user+assistant honestly — confirmed by test_first_turn_creates_and_persists: POST /v1/conversations THEN POST /{id}/messages for BOTH messages; persisted assistant text == streamed text.
- [x] resume loads a thread's messages — confirmed by test_resume_loads_messages (GET /{id} → both render in order) via the hook's load().
- [x] a persistence failure is non-blocking — confirmed by test_persist_failure_is_nonblocking (append→500: streamed turn still on screen, no throw).
- [x] streaming path unchanged — confirmed by the use-chat-stream.ts diff review + 567-green full suite (all v40 chat-* tests still pass).

### Deep checks
- [x] WIRING (code) — load() consumed by handleSelect; onTurnComplete wired in useChatStream({onTurnComplete}); lib/conversations fns called from ChatWorkspace + ChatHistorySidebar; 20 tests exercise list/new/persist/resume/delete/failure end-to-end through the BFF (MSW).
- [x] DEAD-CODE (code) — no orphaned symbol; tsc 0 + eslint 0 on all touched files.
- [x] SEMANTIC — read the hook diff + the ChatWorkspace persistence flow + handlers diff in full; confirmations cited above.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto (Tin's "complete all milestones in auto mode") + personal diff review of the load-bearing v40 stream hook (byte-identical streaming/disconnect path) · date: 2026-06-26

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
