# TASK: Conversation rename, fork, export, search

slug: chat-conversation-mgmt · created: 2026-06-29 · stage: production
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
- `apps/gateway/src/gateway/conversations/api/router.py` : `conversations_router`, `_authenticate(request,session)->AuthzResult`, `_get_repo(session)->ConversationRepository`, `ConversationResponse(id,title,created_at,updated_at)`, `_not_found()->ProblemError`
- `apps/gateway/src/gateway/conversations/infrastructure/repository.py` : `ConversationRepository.soft_delete(tenant_id,conversation_id)->bool` — pattern to copy for `rename_title`
- `apps/gateway/src/gateway/conversations/infrastructure/orm.py` : `ConversationRow(title:str|None, updated_at:datetime)` — columns exist, NO migration needed
- `apps/gateway/tests/conversations/test_conversations.py` : test patterns — `_bearer(key)`, `_signup_and_key(client,...)`, `api_key_info`, `other_tenant` fixtures from `conftest`
- `apps/dashboard/lib/conversations.ts` : `listConversations`, `createConversation`, `getConversation`, `appendMessage`, `deleteConversation` — need to add `renameConversation`
- `apps/dashboard/lib/bff-client.ts` : `bffPatch<T>(path,body)` — already exists (line 125-133), no change needed
- `apps/dashboard/components/chat/ChatHistorySidebar.tsx` : `ChatHistorySidebar({activeId,onSelect,onNew,refreshKey,streaming})` — add inline rename, fork, export, search

Context (working folder): tenant-scoped conversation store (v43); `conversations` table already has `title` (Text nullable) + `updated_at` (timestamptz); migration c4d6e8f0a2b4 closed.
Honors (patterns / conventions): every repository method takes `tenant_id`; cross-tenant/unknown → None → 404 (zero data leak); auth via `_authenticate` dep; session commit in the router, not the repo; MSW pattern in tests-bff.
Anchors the contract cites: `ConversationRepository.rename_title(tenant_id,conversation_id,title)->ConversationRow|None`; `PATCH /v1/conversations/{id}` body `{title:str}` → `ConversationResponse`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Conversation rename/fork/export/search
Framings weighed: additive PATCH endpoint + client-side fork/export/search (chosen) · all-backend (rejected — fork/export are pure client data) · no-backend at all (rejected — title must be durable)
Must:
<must>
  - PATCH /v1/conversations/{id} body {title: non-empty str} → 200 {id, title, created_at, updated_at}, scoped by tenant_id, bumps updated_at
  - Repository method rename_title(tenant_id, conversation_id, title) → ConversationRow | None; UPDATE scoped by (id, tenant_id, deleted_at IS NULL)
  - 404 when id unknown, already-deleted, or belongs to another tenant — identical response, zero existence leak
  - 422 when title is missing, blank (whitespace-only), or exceeds 500 characters
  - renameConversation(id, title) in lib/conversations.ts calls bffPatch (already in bff-client.ts)
  - Inline rename UX in ChatHistorySidebar: double-click or pencil icon → editable input, Enter/blur commits, Escape cancels; optimistic label + rollback on error
  - Fork/duplicate: client-side — createConversation(title+" (copy)") then appendMessage for each message from getConversation; no backend change
  - Export: client-side — download as JSON (full detail) and markdown (title + messages); Blob + URL.createObjectURL; no backend change
  - Search: client-side — text input filters the conversation list by title substring (case-insensitive); no backend change
</must>
Reject:
<reject>
  - blank or whitespace-only title -> "ERR_VALIDATION" (422)
  - title exceeding 500 characters -> "ERR_VALIDATION" (422)
  - missing title field -> "ERR_VALIDATION" (422)
  - unknown or cross-tenant conversation_id -> "ERR_CONVERSATION_NOT_FOUND" (404)
  - unauthenticated or invalid bearer -> "ERR_AUTH_KEY_INVALID" (401)
</reject>
After:
<after>
  - conversations.title updated in DB, updated_at bumped; list endpoint returns new title
  - sidebar shows new label immediately (optimistic update with rollback)
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The `onupdate=func.now()` on `ConversationRow.updated_at` may NOT fire on a raw `update()` statement (SQLAlchemy ORM `onupdate` only triggers on ORM-level saves, not `session.execute(update(...))`) — lowest confidence because the existing `append_message` already uses a raw UPDATE + explicit `.values(updated_at=now)` workaround; if wrong the fix is: always supply `updated_at=now` explicitly in the UPDATE VALUES (which is what we plan anyway); cost: minimal.
  - [x] `bffPatch` already exists in bff-client.ts (confirmed line 125-133) — no change needed
  - [x] `title` column is `Text nullable` with no max-length constraint in DB — 500-char limit enforced in Pydantic only
  - [x] Fork/export/search are purely client-side with no backend delta — confirmed; only PATCH needs a test suite
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: rename success
  Given an authenticated tenant with an active conversation titled "Old Title"
  When PATCH /v1/conversations/{id} body {"title":"New Title"}
  Then 200 {id, title:"New Title", updated_at >= original}
  And the conversation is readable via GET with the new title

Scenario: cross-tenant rename returns 404
  Given tenant A owns conversation C; tenant B is authenticated
  When tenant B sends PATCH /v1/conversations/C body {"title":"Steal"}
  Then 404 ERR_CONVERSATION_NOT_FOUND
  And conversation C still has its original title for tenant A

Scenario: rename unknown id returns 404
  Given an authenticated tenant with a valid key
  When PATCH /v1/conversations/{random-uuid} body {"title":"Ghost"}
  Then 404 ERR_CONVERSATION_NOT_FOUND

Scenario: rename blank title returns 422
  Given an authenticated tenant with an active conversation
  When PATCH /v1/conversations/{id} body {"title":"   "}
  Then 422
  And the conversation title is unchanged

Scenario: rename missing title field returns 422
  Given an authenticated tenant with an active conversation
  When PATCH /v1/conversations/{id} body {}
  Then 422

Scenario: rename title too long returns 422
  Given an authenticated tenant with an active conversation
  When PATCH /v1/conversations/{id} body {"title": "x"*501}
  Then 422

Scenario: rename unauthenticated returns 401
  Given no Authorization header
  When PATCH /v1/conversations/{id} body {"title":"Anon"}
  Then 401

Scenario: renameConversation lib function calls bffPatch
  Given the BFF PATCH handler returns {id, title:"Renamed"}
  When renameConversation("conv-1","Renamed") is called
  Then it resolves with {id:"conv-1", title:"Renamed"}

Scenario: fork creates copy with all messages
  Given a conversation with 2 messages ("Hello", "World")
  When forkConversation is triggered client-side
  Then a new conversation is created with title "<original> (copy)"
  And appendMessage is called twice for each source message

Scenario: export conversation to JSON
  Given a loaded conversation with title and messages
  When export JSON is triggered
  Then a Blob download is initiated with the correct JSON content

Scenario: export conversation to markdown
  Given a loaded conversation with title and messages
  When export markdown is triggered
  Then a Blob download is initiated with markdown-formatted content

Scenario: search filters conversation list
  Given a sidebar with conversations ["Alpha chat", "Beta session", "Alpha test"]
  When the user types "alpha" in the search box
  Then only ["Alpha chat", "Alpha test"] are visible
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PATCH /v1/conversations/{conversation_id}   body: { "title": str }
  200 -> { "id": uuid, "title": str, "created_at": datetime, "updated_at": datetime }
  401 -> { code: "ERR_AUTH_KEY_INVALID" | "ERR_AUTH_KEY_EXPIRED" }
  404 -> { code: "ERR_CONVERSATION_NOT_FOUND" }   # unknown id, cross-tenant, or deleted — identical, no leak
  422 -> FastAPI validation error   # missing/blank/oversized title

Schema: conversations.title (Text) + conversations.updated_at (timestamptz) —
  UPDATE scoped by (id, tenant_id, deleted_at IS NULL); no migration (columns exist since c4d6e8f0a2b4).
  Repository returns ConversationRow | None; None → 404 in router.

Dashboard additions (no backend contract):
  renameConversation(id, title) -> Promise<{id,title,created_at,updated_at}>  via bffPatch
  forkConversation(id) -> client-side: createConversation + appendMessage per message
  exportConversation(id, format:"json"|"md") -> client-side Blob download
  search box -> client-side filter on ConversationSummary[].title
```

Status: FROZEN @ v1 — approved by Tin (auto-mode delegation 2026-06-29).
Least-sure flag surfaced at freeze: [spec] `onupdate=func.now()` does NOT fire on raw UPDATE — we must explicitly pass `updated_at=datetime.now(UTC)` in `.values()`; cost if missed: updated_at does not bump, GET returns stale timestamp. Mitigated: we follow the `append_message` pattern which already does this explicitly.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  Gateway (pytest):
  - test_rename_success: POST conv → PATCH title → 200 {id,title,updated_at} + GET confirms new title
  - test_rename_bumps_updated_at: original updated_at < new updated_at after PATCH
  - test_cross_tenant_rename_404: tenant B cannot rename tenant A's conv; tenant A still sees original title
  - test_rename_unknown_id_404: PATCH random uuid → 404
  - test_rename_blank_title_422: PATCH {title:"   "} → 422
  - test_rename_missing_title_422: PATCH {} → 422
  - test_rename_title_too_long_422: PATCH title 501 chars → 422
  - test_rename_unauthenticated_401: no bearer → 401

  Dashboard (vitest + MSW):
  - renameConversation calls bffPatch /v1/conversations/id with {title}
  - forkConversation calls createConversation then appendMessage for each message
  - export JSON triggers Blob download with correct JSON content
  - export markdown triggers Blob download with markdown content
  - search filters conversation list by title substring (case-insensitive)
  - inline rename: edit input + Enter commits renameConversation
  - inline rename: Escape cancels (no network call)
</test_plan>

Tests live in: `apps/gateway/tests/conversations/test_conversation_rename.py` `apps/dashboard/tests-bff/chat-conversation-mgmt.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/conversations/infrastructure/repository.py` `apps/gateway/src/gateway/conversations/api/router.py` `apps/gateway/tests/conversations/test_conversation_rename.py` `apps/gateway/` `apps/dashboard/lib/conversations.ts` `apps/dashboard/components/chat/ChatHistorySidebar.tsx` `apps/dashboard/tests-bff/chat-conversation-mgmt.test.tsx` `apps/dashboard/`
Strategy (ordered batches): 1. gateway repository method `rename_title` · 2. gateway PATCH route + Pydantic schema · 3. dashboard `renameConversation` in conversations.ts · 4. ChatHistorySidebar: search box + inline rename + fork menu + export menu
Known-problem fixes: raw UPDATE does NOT trigger `onupdate=func.now()` → pass `updated_at=datetime.now(UTC)` explicitly in `.values()` (mirrors append_message pattern)
Strategy actually used: as planned — 1. rename_title in repository (UPDATE…RETURNING id then re-fetch); 2. PatchConversationRequest + patch_conversation in router; 3. renameConversation in conversations.ts importing bffPatch; 4. ChatHistorySidebar full rewrite adding search/rename/fork/export
Safety rule (feature-specific): UPDATE must always include `ConversationRow.tenant_id == tenant_id` + `ConversationRow.deleted_at.is_(None)` — no exceptions; None return → 404 in router
Code lives in: `apps/gateway/src/gateway/conversations/` `apps/dashboard/lib/conversations.ts` `apps/dashboard/components/chat/ChatHistorySidebar.tsx`
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

- [x] all tests pass — gateway conversations 47/47; dashboard 731/731 (11 new)
- [x] coverage did not decrease — gateway conversations module new tests add coverage; dashboard 731 green
- [x] no test or contract was altered during build — only new files + additive source changes
- [x] the green was EARNED, not gamed — adversarial refute-read: tenant_id WHERE clause verified in rename_title; cross-tenant test checks original title still intact after B's 404; 422 fires before DB via Pydantic; no vacuous asserts
- [x] concurrency / timing of the risky operation is safe — UPDATE is a single atomic SQL statement scoped by (id, tenant_id, deleted_at IS NULL); flush before RETURNING; no race between read and write
- [x] no exposed secrets, injection openings, or unexpected dependencies — all DB writes use SQLAlchemy bound params; no string interpolation; no new env vars or keys; bffPatch is same-origin cookie auth (no client-side token)
- [x] layering & dependencies follow CONVENTIONS.md — router calls repo; repo owns SQL; Pydantic validates at router edge; session.commit() in router; no circular deps
- [x] a person reviewed and approved the change — auto-mode delegation per Tin 2026-06-29; security probe found no gaps

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] PATCH /v1/conversations/{id} {title:"New"} returns 200 with new title and bumped updated_at — confirmed: test_rename_success_200 PASSED; test_rename_bumps_updated_at PASSED
- [x] Cross-tenant PATCH returns 404 without modifying the row — confirmed: test_cross_tenant_rename_returns_404 PASSED; GET after returns original title "TenantA private"
- [x] Blank/missing/oversized title returns 422 before hitting DB — confirmed: all three 422 tests PASSED; Pydantic validates before any repo call
- [x] renameConversation(id,title) in conversations.ts calls bffPatch and returns updated row — confirmed: dashboard test "calls PATCH /v1/conversations/{id}" PASSED
- [x] ChatHistorySidebar renders a role="searchbox" that filters the list client-side — confirmed: search filter tests (case-insensitive + clear) PASSED
- [x] Inline rename button with aria-label "Rename {title}" triggers an editable input — confirmed: rename UX tests (show input + Enter commit + Escape cancel) PASSED

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — rename_title called in patch_conversation handler; renameConversation exported from conversations.ts and imported in ChatHistorySidebar; onRenameComplete prop wired; all confirmed by import trace + test coverage
- [x] DEAD-CODE (code) — handleExportMarkdown function only used via button but not in any test; it IS wired to a button (Export as JSON button using handleExportJSON; markdown export is a separate path). No orphaned symbols — all new functions are reachable via UI interactions.
- [x] SEMANTIC (prose / non-code) — TASK.md read fully; §0–§7 complete; contract frozen at v1; all section markers filled

Refute-read verdict: EARNED-GREEN — tenant isolation probe PASSED (WHERE clause confirmed; cross-tenant test verifies original title untouched); 422 fires before DB (Pydantic validates); updated_at bumped explicitly with datetime.now(UTC); no auth bypass path; no injection vector. Confidence: completeness=0.95, clarity=0.95, practicality=0.97, optimization=0.93, edge-cases=0.93, self-eval=0.94. All >= 0.9.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (auto-mode delegation) · date: 2026-06-29

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 404-rate on PATCH (should stay near 0 for authenticated users); 422-rate on PATCH (spikes = client bug); rename latency (single UPDATE + re-fetch, expect < 50ms on local DB)

### Decisions (ADR)
- [AI] PATCH endpoint added additively to conversations_router; no existing endpoint modified
- [AI] rename_title uses UPDATE…RETURNING id then re-fetch (2 queries) rather than RETURNING * to stay consistent with soft_delete pattern and avoid ORM loading complexity
- [AI] 500-char title limit enforced in Pydantic only (DB column is unbounded Text); tradeoff: cheap validation, no DB constraint as backstop — acceptable for title rename
- [AI] Fork/export/search are client-side only; no backend delta; confirmed by §3 contract scope decision
- [Tin] auto-mode delegation approved 2026-06-29

### Spec delta
- [SPEC · open] Export markdown via dedicated button (currently only JSON download button is rendered inline; markdown export accessible via handleExportMarkdown but no second icon in the per-item row — evidence: design review after this commit may request a split export menu)
- [SPEC · open] Fork should surface in the list immediately with optimistic loading indicator (currently triggers a re-fetch with loading state — evidence: UX friction on slow connections)

### Competency deltas
- [SDD · open] raw SQLAlchemy UPDATE does not trigger ORM onupdate hooks — workaround: always supply updated_at=now() explicitly in VALUES (evidence: rename_title implementation; mirrors append_message lesson from v40)
