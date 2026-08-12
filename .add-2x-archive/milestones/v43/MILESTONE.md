# MILESTONE: Remote sessions / conversations

goal: A signed-in user (and any API key holder) can create, list, resume, and delete persistent conversation threads — messages survive reload/device via a tenant-scoped gateway store, surfaced in the dashboard chat.
rationale: new-major → milestone 4 of 9 (program v40–v48, "AI Application Platform"). Tin 2026-06-26 "implement all, best decision". Today the v40 chat workspace holds messages in CLIENT React state only — a reload/device-switch loses the thread. "Remote sessions" = a tenant-scoped, API-accessible (not dashboard-only) persistent conversation store so any key holder (agents included) can create/list/resume/delete threads. First of the three "remote" platform capabilities (sessions → memory v44 → artifacts v45). Reuses the existing gateway DB + KeyAuthenticator auth seam; NO new infra.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A new `gateway/conversations/` domain: `conversations` + `conversation_messages` tables (tenant-scoped), an alembic migration (chained onto head f2a4c6e8b0d3), a repository, and a `/v1/conversations` REST surface authenticated by the existing `KeyAuthenticator` (raw key → AuthzResult tenant_id/key_id) — NO model governance (these are CRUD, not model calls). Endpoints: `POST /v1/conversations` (create, optional title) · `GET /v1/conversations` (list the tenant's, newest first, paginated) · `GET /v1/conversations/{id}` (thread + messages) · `POST /v1/conversations/{id}/messages` (append {role, content}) · `DELETE /v1/conversations/{id}` (soft or hard delete). STRICT tenant isolation — a tenant can never read/write another tenant's conversation (404, not 403, to avoid existence leak).
  - Dashboard: a conversation history sidebar in `/app/chat` (list + "New" + resume a thread + the active thread persists each user/assistant turn) via the BFF. Reuses the v40 chat workspace + the v42 BFF.
Out:
  - Semantic / vector recall over conversation content — that is v44 (Remote memory, pgvector). v43 is exact thread storage + retrieval only.
  - Sharing threads across tenants / users, collaboration, or public links.
  - Server-side summarization / title auto-generation (titles are user-supplied or a simple first-message slug; LLM titling deferred).
  - Editing/branching past messages; full-text search across threads (a deferred delta).
  - Changing the chat COMPLETION path — persistence wraps it; the existing `/v1/chat/completions` is untouched.

## Shared decisions & glossary deltas   (living — every task must honor these)
- CONVERSATION (NEW glossary): a tenant-scoped ordered thread of messages ({role: user|assistant|system, content}). Keyed by a server-generated UUID; owns its messages (cascade delete). `tenant_id` (+ `key_id` of the creator) scopes every row.
- TENANT-ISOLATION (security, HARD invariant): every conversations query filters by the authenticated `tenant_id`; a cross-tenant id resolves to 404 (never 403/200) so existence does not leak. This is the milestone's security-sensitive surface — freeze + independently refute-verify.
- AUTH REUSE: `/v1/conversations` authenticates with the SAME `KeyAuthenticator.authenticate(raw_key)` the proxy uses (Bearer sk- key), NOT the admin JWT — so API key holders (agents) get remote sessions too. No new auth.
- HONEST PERSISTENCE: a message is stored verbatim; no fabrication. The dashboard persists exactly the turns the user sent/received.
- FE honors WCAG-AA + v23/v24 tokens + the four states; the BFF keeps its fail-closed auth + streaming.

## Shared / risky contracts (freeze these first)
- The conversations schema + the `/v1/conversations` REST contract + the tenant-isolation rule -> owning task `conversations-backend`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] conversations-backend   depends-on: none                  — `gateway/conversations/` domain: ORM + migration + repository + `/v1/conversations` CRUD authenticated via KeyAuthenticator, STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + isolation contract. (gate PASS, 35 tests)
- [x] chat-history-ui         depends-on: conversations-backend — dashboard `/app/chat` conversation sidebar (list + New + resume + persist each turn) via the BFF. (gate PASS, 20 tests)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An API key holder can POST a conversation, append messages, GET it back with its messages, list their conversations, and DELETE it — all tenant-scoped; another tenant's id returns 404   (← conversations-backend)
- [x] A signed-in user can, in `/app/chat`, start a new conversation, send turns that persist, reload the page, and resume the same thread from a history sidebar   (← chat-history-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : NEW `gateway/conversations/` domain — a tenant-scoped, API-key-authenticated `/v1/conversations` store (the platform's first "remote session" primitive, usable by ANY sk- key holder incl. agents). ORM (conversations + conversation_messages, FK cascade, list+order indexes) + alembic migration c4d6e8f0a2b4 (chained on f2a4c6e8b0d3) + a strictly tenant-scoped repository (every method keyword-only tenant_id; cross-tenant id → 404, no existence leak) + 5 REST endpoints authenticated by the same KeyAuthenticator the proxy uses + an expiry gate (parity with governance). main.py registers the router. Additive — /v1/chat/completions untouched. 35 DB-backed tests joined the suite.
- dashboard : `/app/chat` gained a conversation-history sidebar (list/new/resume/delete, four states, WCAG-AA) + per-turn persistence via the BFF. use-chat-stream.ts extended ADDITIVELY (new `load()` for resume + an `onTurnComplete` opt fired only on a committed assistant turn) with the v40 streaming/abort/disconnect path byte-identical. New lib/conversations.ts BFF client + ChatHistorySidebar.tsx. vitest 547 → 567 green; tsc 0; eslint 0.
- tooling / skill / book : untouched (only `.add/` task + milestone bookkeeping).

### Cross-task evidence   (one row per task)
- conversations-backend : gate=PASS · tests=35 green (DB-backed; no-DB make test-fast 206, no regression) · residue=an independent refute-read of the tenant-isolation surface UPHELD the invariant AND surfaced 4 real issues (1 deploy BLOCKER: duplicate alembic revision id; 1 MEDIUM: expired-key bypass; 2 LOW), ALL FIXED + re-verified. Deltas: hard-purge (vs soft-delete), full-text search, LLM titling, FK conversations.tenant_id→tenants (omitted per the alert_events pattern).
- chat-history-ui : gate=PASS · tests=20 green (full dashboard suite 567, +20, zero regression; tsc 0; eslint 0) · residue=stopped (aborted) turns aren't persisted (only completed turns — by design); a louder sidebar error surface on a failed assistant-append (currently swallowed best-effort) + a rename affordance are deltas.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
  - EC1 (API key holder can POST/append/GET/list/DELETE, tenant-scoped, cross-tenant → 404): conversations-backend — 35 DB-backed tests incl. test_cross_tenant_get/append/delete_returns_404; the refute-read confirmed no cross-tenant leak is reachable.
  - EC2 (signed-in user can start/persist/reload/resume a thread from a sidebar): chat-history-ui — 20 tests incl. first_turn_creates_and_persists + resume_loads_messages + the reload case; the gateway store (EC1) backs persistence.
- goal: a user (and any API key holder) can create/list/resume/delete persistent conversation threads that survive reload/device via a tenant-scoped gateway store surfaced in the dashboard chat — proven by 35 gateway + 20 dashboard tests green (567 total dashboard, 206 no-DB gateway, no regression), strict tenant isolation independently refute-verified, and the v40 streaming path kept byte-identical.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] v43 commits land on the v40→v43 task stack (committed locally): t1 conversations-backend → t2 chat-history-ui → .add close. PUSH/PR await Tin's go-ahead (outward act).
- [ ] open a PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]]); v40–v43 are a stack — merge in order or retarget.
- [ ] deploy note: run `alembic upgrade head` to apply migration c4d6e8f0a2b4 (creates conversations + conversation_messages). NO new infra/env (reuses the gateway Postgres + KeyAuthenticator). No feature flag — the new routes are additive and tenant-scoped.
- [ ] v43 joins the releasable set (v33–v42 already pending); bundle into the next release cut when Tin calls it (release.md).
