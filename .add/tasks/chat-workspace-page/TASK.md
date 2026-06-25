# TASK: Chat workspace page (/app/chat)

slug: chat-workspace-page · created: 2026-06-25 · stage: production
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

> UDD design-confirm (Tin, 2026-06-25): captured screen `.add/design/captures/chat-workspace-page.png` · layout record `.add/design/prototypes/chat-workspace-page.json` · catalog components MessageThread/MessageBubble/Composer/CostReadout/ModelPicker/ModelControls. The picker, cost readout, and system+temperature are SLOTS the sibling tasks fill; this task renders them. Build to match this screen.

Touches (files · symbols · signatures):
  - `apps/dashboard/app/(app)/app/chat/page.tsx` (NEW) — the route; thin Server Component importing the client workspace (mirrors `app/(app)/app/models/page.tsx` → `components/models/ModelsPage.tsx`). Route group `(app)/app/<name>/` is the verified end-user-page convention (siblings: usage, spend, keys, models, …).
  - `apps/dashboard/components/chat/ChatWorkspace.tsx` (NEW, `"use client"`) — multi-turn thread + composer + Stop control + the four UI states; sends to a caller-selected model + params (the picker/controls arrive in the chat-model-controls sibling — this page accepts them).
  - `apps/dashboard/lib/hooks/use-chat-stream.ts` (NEW) — the NET-NEW SSE consumer: `fetch("/api/gw/v1/chat/completions",{method:"POST",credentials:"include",body:{…,stream:true,stream_options:{include_usage:true}}})` → `response.body.getReader()` + `TextDecoder` loop; parses `data: {delta}` → `choices[0].delta.content`, the terminal usage frame (`choices:[]`+`usage`), and the `[DONE]` sentinel; an `AbortController` drives Stop → propagates through the BFF `req.signal` to the gateway's v35 disconnect-billing. OWNS the `ChatMessage` + `StreamingState` types.
  - `apps/dashboard/components/ui/app-shell.tsx:NAV_ITEMS` (lines 43–56) — add `{ href:"/app/chat", label:"Chat", icon:<lucide MessageSquare> }` with NO `minRole` (visible to all roles); `visibleItems()` filter at lines 75–77 only hides `minRole:"admin"` from members.
Context (working folder):
  - BFF `apps/dashboard/app/api/gw/[...path]/route.ts` pipes `text/event-stream` UNBUFFERED since v40 (streaming-bff) — a POST `{stream:true}` now streams to the client; auth is cookie-only (no Authorization header client-side — `lib/bff-client.ts:9`).
  - Gateway OpenAI-wire `/v1/chat/completions`: SSE frames `data: {"choices":[{"delta":{"content":"…"}}]}` + a terminal usage frame `{"choices":[],"usage":{prompt_tokens,completion_tokens,total_tokens}}` + `data: [DONE]` (fixture `apps/gateway/tests/azure_streaming/test_azure_streaming.py:51`); `stream_options:{include_usage:true}` requests the usage frame.
  - Model list (for the later chat-model-controls) `GET /v1/models` → `{object:"list",data:[]}` — named here only as the downstream reader.
  - NO SSE consumer exists today: `lib/bff-client.ts` helpers all `res.json()` (line 89) — the streaming hook is net-new. NO toast system (errors render inline via `<ErrorState>`). NO `ScrollArea`/`Skeleton` primitive (use `overflow-y-auto`; `Loading` spinner pre-first-token). TanStack Query already provided (`app/providers.tsx:15`).
Honors (patterns / conventions):
  - AppShell `NavItem` contract (`minRole` omitted = all roles) · `DashboardShell`/`DashboardLayout` shell wiring (`components/dashboard-shell.tsx`).
  - `lib/bff-client.ts` cookie-only auth (no Authorization / no localStorage client-side); error propagation via `BffError`.
  - `components/ui/states.tsx` four-state contract: `Loading` (`role=status`,`aria-busy`), `ErrorState` (`role=alert`,`onRetry`), `Empty`, data/`Success`.
  - CONVENTIONS.md: stream/wire parsers test fragmentation (split-at-midpoint AND byte-by-byte) by default → the SSE-parse tests must include fragmentation; `within(<section>)` test-scope; axe filter impact ∈ {serious,critical}; WCAG-AA + design tokens (`globals.css` `--primary` etc.; focus-ring `focus-visible:ring-2 ring-ring ring-offset-2`).
  - PROJECT.md IO invariant: bounded handling + disconnect propagation — Stop → `AbortController` → BFF → v35 billing; never swallow the abort.
Anchors the contract cites:
  - `useChatStream()` — public shape OWNED here: `ChatMessage{role,content}`, `StreamingState{status,messages,streamingText,usage?,error?}`, actions `send(messages,opts)` + `stop()` (consumed by chat-cost-readout = usage frame, chat-model-controls = model/params).
  - `ChatWorkspace` — thread render + composer + Stop + the four UI states + the role-open nav entry.
  - SSE-parse contract: accumulate `choices[0].delta.content` · usage frame (`choices:[]`+`usage`) · `[DONE]` sentinel · Stop aborts mid-stream.
  - test harness: `tests-bff/chat-workspace-page.test.tsx` (bff project; MSW `textStream` SSE mock per `tests-bff/streaming-bff.test.ts:34`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: In-dashboard chat workspace at `/app/chat` — a signed-in user holds a multi-turn, live-streaming conversation with a caller-selected catalog model. Assistant turns render token-by-token via a NET-NEW `useChatStream` hook that consumes the BFF SSE; a Stop control aborts mid-stream (→ v35 disconnect-billing); the four UI states + a role-open nav entry ship. OWNS the `ChatMessage`/`StreamingState`/`Usage` data contract the two sibling tasks consume.
Framings weighed: imperative `fetch()`+`getReader()` streaming hook (chosen) · `EventSource` (rejected — GET-only, can't POST a body or send cookies) · TanStack-Query `useMutation` (rejected — declarative, doesn't model incremental token accumulation).
Must:
<must>
  - M1 — `useChatStream()` exposes `{ status:'idle'|'streaming'|'error', messages:ChatMessage[], streamingText:string, usage?:Usage, error?:BffError, send(input), stop(), reset() }`. `ChatMessage={role:'user'|'assistant'|'system',content:string}`; `Usage={prompt_tokens,completion_tokens,total_tokens}`. These TYPES are OWNED here (sibling tasks import them) — the frozen seam.
  - M2 — `send(input)` POSTs `/api/gw/v1/chat/completions` (`credentials:'include'`) with `{model, messages:[…history,{role:'user',content:text}], stream:true, stream_options:{include_usage:true}, temperature?}` (a `system` prompt becomes `messages[0] role:'system'`); reads `response.body.getReader()`+`TextDecoder`, parses SSE `data:` frames, accumulates `choices[0].delta.content` into `streamingText` LIVE, captures the terminal usage frame (`choices:[]`+`usage`), ends on `data:[DONE]`; on end appends the assembled assistant `ChatMessage` to `messages`, clears `streamingText`, status→idle.
  - M3 — `stop()` aborts the in-flight fetch via an `AbortController` (BFF propagates to the gateway → v35 billing); the partial `streamingText` so far is COMMITTED as the assistant message (not discarded); status→idle.
  - M4 — `ChatWorkspace` renders the four UI states: empty (no messages → inviting empty state), streaming (live tokens + caret + Stop), error (`<ErrorState role=alert onRetry>`), success (idle populated thread). Keyboard-operable + a11y-landmarked; Enter sends, Shift+Enter newlines, empty submit is a no-op.
  - M5 — a `{href:'/app/chat',label:'Chat',icon:MessageSquare}` NavItem (NO `minRole`) is added to `app-shell.tsx:NAV_ITEMS` → visible to every role; route `app/(app)/app/chat/page.tsx` = thin Server Component → client `ChatWorkspace`.
  - M6 — COST HONESTY: when the usage frame is absent, `usage` stays `undefined` and the cost slot shows an honest placeholder (`—`/"not available"), never a fabricated number. The page passes a caller-selected `model`+`temperature` (sensible defaults until chat-model-controls wires the picker).
</must>
Reject:
<reject>
  - BFF non-2xx or a mid-stream error -> status `'error'` + `error`=`BffError`; any partial `streamingText` is preserved as a stopped assistant message; the thread stays usable (no crash, no fabricated content).
  - empty / whitespace-only composer submit -> no send (Send disabled; `send()` a no-op).
</reject>
After:
<after>
  - A user types, sends, and watches the assistant response stream token-by-token; Stop ends it early KEEPING the partial text; each assistant turn exposes its `usage` for the cost readout.
  - Refresh loses history (client-side only — durable sessions = v43); the Chat nav entry is present for every role.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] The `useChatStream` public shape (`ChatMessage`/`StreamingState`/`Usage` + `send`/`stop`/`reset`) is the FROZEN seam the two sibling tasks consume — if it's wrong, chat-cost-readout (usage) and chat-model-controls (model/params) churn. Lowest confidence because it's a net-new API with no prior consumer. Pinned by tests asserting the hook's emitted state across a streamed fixture.
  - [ ] [contract] SSE frame parsing must reassemble a `data:` line split across reads — the BFF passes bytes through transparently, so the hook's `TextDecoder` buffer must survive chunk boundaries (CONVENTIONS fragmentation rule). Pinned by a fragmentation test.
  - [ ] [scenario] The terminal usage frame is a frame with `choices:[]` + top-level `usage`, arriving BEFORE `[DONE]`; if a provider omits it, `usage` stays undefined (honest placeholder), not an error. Pinned by a usage-present + usage-absent test pair.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: assistant response streams token-by-token            # M2
  Given a signed-in user on /app/chat with model gpt-4o
  When they send "hello" and the BFF streams delta frames then a usage frame then [DONE]
  Then streamingText grows as each delta arrives
  And on [DONE] a complete assistant ChatMessage is appended, streamingText is cleared, status is idle

Scenario: Stop commits the partial turn                        # M3
  Given an assistant response mid-stream with some streamingText
  When the user clicks Stop
  Then the fetch is aborted (AbortController) and status returns to idle
  And the partial text is committed as the assistant message (NOT discarded)

Scenario: usage frame feeds the cost readout                   # M1/M6
  Given a stream that includes a terminal usage frame {choices:[],usage:{...}}
  When the turn completes
  Then usage is set to {prompt_tokens,completion_tokens,total_tokens}
  And the cost slot can render a real number

Scenario: absent usage shows an honest placeholder             # M6
  Given a stream that ends at [DONE] with NO usage frame
  When the turn completes
  Then usage stays undefined
  And the cost slot shows an honest placeholder (—/"not available"), never a fabricated number

Scenario: four UI states render                                # M4
  Given the workspace with no messages
  Then an inviting empty state shows
  And while streaming a caret + Stop show; on a BffError an ErrorState(role=alert) with retry shows; idle+populated shows the thread

Scenario: SSE frame split across reads still parses            # parse (fragmentation)
  Given a single data: frame delivered in two reads (split mid-JSON, and byte-by-byte)
  When the hook decodes the stream
  Then the delta content accumulates correctly in order with no dropped or duplicated bytes

Scenario: Chat nav entry is visible to every role              # M5
  Given NAV_ITEMS includes { href:'/app/chat', label:'Chat' } with no minRole
  When visibleItems is computed for a member role
  Then the Chat entry is present

Scenario: stream error preserves partial + stays usable        # Reject
  Given a send whose BFF responds non-2xx OR errors mid-stream
  When the hook handles it
  Then status is 'error' with error=BffError and any partial text is kept as a stopped assistant message
  And the thread does not crash and remains usable (retry available)

Scenario: empty submit is a no-op                              # Reject
  Given an empty / whitespace-only composer
  When the user presses Enter / clicks Send
  Then no request is sent and the thread is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ─ The frozen seam (owned here; chat-cost-readout + chat-model-controls import it) ─
type ChatMessage = { role: 'user' | 'assistant' | 'system'; content: string }
type Usage       = { prompt_tokens: number; completion_tokens: number; total_tokens: number }
type ChatStatus  = 'idle' | 'streaming' | 'error'

useChatStream(opts?: { gatewayPath?: string }) -> {
  status: ChatStatus
  messages: ChatMessage[]        // committed turns (user + assistant), in order
  streamingText: string          // live assistant text for the in-flight turn ('' when idle)
  usage?: Usage                  // last completed turn's usage (undefined ⇒ honest placeholder)
  error?: BffError
  send(input: { model: string; text: string; system?: string; temperature?: number }): void
  stop(): void                   // abort in-flight; commit partial; status→idle
  reset(): void                  // "New chat" — clear messages/usage/error
}

// ─ Wire (send) ─
POST /api/gw/v1/chat/completions   credentials: 'include'
  body: { model, messages: [...history, {role:'user',content:text}],
          stream: true, stream_options: { include_usage: true }, temperature?,
          (system ⇒ prepended as messages[0] {role:'system',content:system}) }
  read: response.body.getReader() + TextDecoder; buffer; split frames on "\n\n";
        each frame "data: <json|[DONE]>":
          • json.choices[0]?.delta?.content  → append to streamingText
          • json.usage (with choices:[])      → set usage
          • "[DONE]"                          → end turn
  abort: AbortController.signal → BFF req.signal → gateway v35 disconnect-billing
  error: non-2xx or mid-stream throw ⇒ status 'error' + error=BffError; partial kept

// ─ Files ─
app/(app)/app/chat/page.tsx        Server Component → <ChatWorkspace/>
components/chat/ChatWorkspace.tsx   "use client" — thread (4 states) + composer + Stop; uses useChatStream
lib/hooks/use-chat-stream.ts        the hook above (net-new SSE consumer)
components/ui/app-shell.tsx         NAV_ITEMS += { href:'/app/chat', label:'Chat', icon:MessageSquare }  (no minRole)

Schema: none — client-side only (no DB / no persistence; durable sessions = v43).
Slots rendered here, FILLED by siblings: ModelPicker + ModelControls(system,temperature) → chat-model-controls · CostReadout(turn+session) → chat-cost-readout.
```

Status: FROZEN @ v1 — approved by Tin 2026-06-25
Least-sure flag surfaced at freeze:
  - [contract] The `useChatStream` return shape (`ChatMessage`/`Usage`/`status`/`streamingText` + `send`/`stop`/`reset`) is the seam BOTH sibling tasks import — getting a field name/shape wrong here forces a change-request in chat-cost-readout (reads `usage`) and chat-model-controls (drives `send` model/params). Mitigation: the shape mirrors the OpenAI wire the gateway already speaks; pinned by hook-state tests over a streamed fixture (incl. usage-absent).
  - [scenario] SSE fragmentation — a `data:` frame split across reads must reassemble (TextDecoder buffer across chunks); if mishandled, tokens drop/duplicate. Pinned by a split-at-midpoint + byte-by-byte test.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; no coverage regression on the dashboard.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_streams_token_by_token (M2): mock the BFF (MSW textStream) to emit 2 delta frames + usage + [DONE] → assert streamingText grows, then on [DONE] one assistant ChatMessage is appended + streamingText cleared + status idle.
  - test_stop_commits_partial (M3): mid-stream, call stop() → assert the fetch AbortController fired AND the partial text is committed as the assistant message + status idle.
  - test_usage_feeds_cost (M1/M6): stream with a usage frame → assert usage = {prompt_tokens,completion_tokens,total_tokens}.
  - test_absent_usage_placeholder (M6): stream ending at [DONE] with NO usage frame → assert usage stays undefined (cost slot renders the honest placeholder, never a number).
  - test_four_ui_states (M4): render ChatWorkspace with no messages → empty state; isError → ErrorState(role=alert); streaming → caret + Stop; idle+messages → thread (assert via within(section)).
  - test_sse_fragmentation (parse): deliver one data: frame split mid-JSON AND byte-by-byte → assert content accumulates in order, no drop/dup.
  - test_nav_entry_all_roles (M5): assert NAV_ITEMS has { href:'/app/chat', label:'Chat' } with no minRole → visibleItems('member') includes it.
  - test_stream_error_preserves (Reject): BFF non-2xx / mid-stream throw → status 'error' + error=BffError + partial kept as stopped assistant message + no crash.
  - test_empty_submit_noop (Reject): empty/whitespace composer submit → fetch never called, thread unchanged.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-workspace-page.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/hooks/use-chat-stream.ts` · `apps/dashboard/components/chat/ChatWorkspace.tsx` · `apps/dashboard/app/(app)/app/chat/page.tsx` · `apps/dashboard/components/ui/app-shell.tsx` · `apps/dashboard/tests-bff/chat-workspace-page.test.tsx` (the §4 red suite — declared here so the scope-gate anchor counts it from the tests→build crossing) · `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (sibling-test EVOLUTION: the role-open Chat nav link bumps the asserted nav counts 4→5 / 12→13, per that file's documented per-milestone count-evolution convention — strengthened, not weakened)
Strategy (ordered batches): 1. `use-chat-stream.ts` — the hook (types + send/stop/reset + the SSE reader/decoder/parse loop + AbortController) — the frozen seam first. 2. `ChatWorkspace.tsx` — thread (4 states) + composer + Stop, wired to the hook, with the sibling-task SLOTS rendered. 3. `page.tsx` Server Component → ChatWorkspace. 4. `app-shell.tsx` NAV_ITEMS += Chat (no minRole). Match the confirmed capture.
Safety rule (feature-specific): the SSE reader MUST buffer across chunk boundaries (never assume a frame = one read); `stop()` MUST commit partial text (never discard) AND abort the fetch so v35 billing fires; NEVER fabricate a cost when `usage` is absent (honest placeholder).
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps — Web Streams + fetch); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard suite **526 passed (63 files)**; chat suite **12/12**.
- [x] coverage did not decrease — net-new files + 12 new tests; tsc `--noEmit` exit 0; eslint changed-files exit 0.
- [x] no test or contract was altered during build — the frozen §3 seam is implemented verbatim; the only sibling-test edit (`nav-role-filter.test.tsx`) is a DECLARED count EVOLUTION (4→5 / 12→13, +a Chat assertion = strengthened), per that file's documented per-milestone convention.
- [x] the green was EARNED — adversarial refute-read (sonnet) ran; it found 1 MAJOR (F1 unmount→cost-leak) + honesty/cleanup items. F1 FIXED + pinned by a red-first test (`test_unmount_aborts_in_flight_stream`); F2 FIXED (honest session placeholder); see residue below. No overfit/vacuous asserts found.
- [x] concurrency / timing safe — double-send guarded by `abortRef` set synchronously; stop()=abort+reader.cancel() unblocks the read loop and commits the partial exactly once (no double-commit, refute-confirmed); unmount aborts the in-flight fetch (F1).
- [x] no exposed secrets / injection / unexpected deps — cookie-only auth (`credentials:"include"`, no token client-side); no new deps (Web Streams + fetch only); BffError reused.
- [x] layering & deps follow CONVENTIONS.md — `states.tsx` four-state contract; AppShell NavItem (no minRole = all roles); fragmentation tested (byte-by-byte); design tokens.
- [~] a person reviewed and approved — auto-gate under `autonomy: auto`; contract was human-frozen by Tin; refute-read stood in for the adversarial pass. No security/architecture HARD-STOP raised.

### Build expectations — what "correct" looks like
- [x] Assistant text streams token-by-token then commits one assistant turn — confirmed by `test_streams_token_by_token` + the live streaming bubble→committed bubble transition.
- [x] Stop commits the partial (never discards) AND aborts upstream — confirmed by `test_stop_commits_partial` + `test_unmount_aborts_in_flight_stream` (signal.aborted).
- [x] Absent usage ⇒ honest placeholder, never a fabricated number — confirmed by `test_absent_usage_stays_undefined` + the static "Session cost —" header slot (F2 fix).
- [x] `/app/chat` is a real route, Chat nav visible to ALL roles — confirmed by `next build` route manifest (`○ /app/chat`) + `test_nav_entry_visible_to_all_roles` + nav-role-filter member=5.

### Deep checks
- [x] WIRING — `useChatStream` consumed by `ChatWorkspace`; `ChatWorkspace` by `page.tsx`; `NAV_ITEMS` exported + Chat entry rendered by the shell + asserted by tests. `next build` resolves the route.
- [x] DEAD-CODE — removed the now-unused `usage` destructure from the component (F2); no orphaned symbol; `usage`/`reset` remain on the hook as the frozen seam siblings consume.
- [x] SEMANTIC — refute-read report read in full; F3 (listener) confirmed a non-leak (local controller, GC'd); F4 accepted (see residue).

### RESIDUE (non-blocking, carried to observe / sibling tasks)
- F4 (NIT) index `key={i}` → streaming-cursor bleed ONLY if `reset()` is followed immediately by a new stream; cosmetic, no data-correctness impact. Clean fix needs a per-message id, which the FROZEN `ChatMessage` ({role,content}) excludes → deferred as a polish delta (revisit when chat-cost-readout/sessions touch the message model).
- Markdown/code rendering in assistant bubbles is plain-text (whitespace-pre-wrap) for now; the hi-fi capture's rich code-block is a presentation delta for a later polish pass (no new dep pulled in).

### GATE RECORD
Outcome: PASS
Reviewed by: auto-gate (autonomy:auto) + sonnet refute-read; contract human-frozen by Tin · date: 2026-06-25

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
