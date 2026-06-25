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
- [ ] conversations-backend   depends-on: none                  — `gateway/conversations/` domain: ORM + migration + repository + `/v1/conversations` CRUD authenticated via KeyAuthenticator, STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + isolation contract.
- [ ] chat-history-ui         depends-on: conversations-backend — dashboard `/app/chat` conversation sidebar (list + New + resume + persist each turn) via the BFF.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An API key holder can POST a conversation, append messages, GET it back with its messages, list their conversations, and DELETE it — all tenant-scoped; another tenant's id returns 404   (← conversations-backend)
- [ ] A signed-in user can, in `/app/chat`, start a new conversation, send turns that persist, reload the page, and resume the same thread from a history sidebar   (← chat-history-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
