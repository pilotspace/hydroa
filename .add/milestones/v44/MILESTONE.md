# MILESTONE: Remote memory (tenant-scoped semantic memory store)

goal: A user (and any API key holder) can store facts/notes as 'memories' and retrieve them by semantic similarity via a tenant-scoped gateway store, surfaced in the dashboard — the second 'remote' platform capability after sessions.
rationale: new-major → milestone 5 of 9 (program v40–v48, "AI Application Platform"). Tin 2026-06-26 "implement all, best decision". The third "remote" capability after sessions (v43): a tenant-scoped semantic MEMORY any key holder (agents) can write facts/notes to and recall by meaning — the substrate for long-running agents that remember across sessions. ARCH DECISION (self-made, conservative): the deployed Postgres is `postgres:16-alpine` (NO pgvector), so the MVP stores embeddings as a float-array column + in-Python cosine over each tenant's (small) memory set — ZERO new infra, reuses the EXISTING embeddings pipeline (EmbeddingsUseCase + OpenAI/Azure/Bedrock/Google) to vectorize. pgvector index = a documented SCALE delta (a pgvector image swap is real infra, deferred). Mirrors v43's domain + tenant-isolation + BFF patterns.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A new `gateway/memory/` domain: a `memories` table (tenant-scoped) + alembic migration (chained on head c4d6e8f0a2b4) + a repository + a `/v1/memories` REST surface authenticated by the same `KeyAuthenticator` as v43 (sk- key → AuthzResult; NOT admin JWT). Endpoints: `POST /v1/memories` ({content, metadata?} → store + best-effort embed) · `GET /v1/memories` (list the tenant's, newest first, paginated) · `POST /v1/memories/search` ({query, top_k} → embed query + rank by cosine over the tenant's memories) · `DELETE /v1/memories/{id}`. STRICT tenant isolation (cross-tenant id → 404; search NEVER crosses tenants). Reuses the existing embeddings provider to vectorize; embedding stored as a float array.
  - Dashboard: a `/app/memory` surface (add a memory, list, semantic search with ranked results) via the BFF. Role-open, mirrors the v42 voice / v43 chat ethos.
Out:
  - pgvector / an ANN index — the MVP does in-Python cosine over a tenant's rows (small sets); pgvector is a SCALE delta (needs a DB image swap = real infra).
  - Auto-injection of recalled memories into the chat completion path (RAG-into-chat) — a delta; v44 is an explicit store + search API + surface only.
  - Memory editing/dedup/decay, cross-tenant or shared memories, summarization/extraction of memories from conversations (LLM memory-extraction is deferred).
  - Changing /v1/embeddings or any existing route — additive only.

## Shared decisions & glossary deltas   (living — every task must honor these)
- MEMORY (NEW glossary): a tenant-scoped {content: str, embedding: float[]|null, metadata?: json, created_at} row, keyed by a server UUID, owned by `tenant_id` (+ creator `key_id`). Retrieval is by semantic similarity (cosine) over the SAME tenant's memories only.
- TENANT-ISOLATION (security, HARD invariant — same as v43): every memory query (incl. SEARCH) filters by the authenticated `tenant_id`; a cross-tenant id → 404; search results NEVER include another tenant's memory. The milestone's security-sensitive surface — freeze + independently refute-verify.
- AUTH REUSE: `/v1/memories` authenticates with `KeyAuthenticator.authenticate(raw_key)` + the same expiry gate v43 added (sk- key, not admin JWT).
- EMBED-BEST-EFFORT (design-for-failure): vectorization reuses the existing embeddings pipeline and is BEST-EFFORT — if it fails (provider down / model absent / budget), the memory still stores (embedding=null) and remains retrievable by a text-substring fallback; a write is NEVER lost to an embedding failure. Cosine search degrades to text match for null-embedding rows.
- HONEST RETRIEVAL: search ranks by real cosine similarity over stored vectors; no fabricated scores; null-embedding rows are matched by substring only (clearly a fallback).
- FE honors WCAG-AA + v23/v24 tokens + the four states; the BFF keeps its fail-closed auth.

## Shared / risky contracts (freeze these first)
- The memories schema + `/v1/memories` REST + the cosine-search + tenant-isolation rule + the embed-best-effort envelope -> owning task `memory-backend`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] memory-backend   depends-on: none            — `gateway/memory/` domain: ORM + migration + repository + `/v1/memories` (store/list/search/delete) auth'd via KeyAuthenticator, embed-best-effort via the existing embeddings pipeline, in-Python cosine, STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + search + isolation contract.
- [ ] memory-ui        depends-on: memory-backend   — dashboard `/app/memory` (add + list + semantic search with ranked results) via the BFF; role-open nav entry.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An API key holder can POST a memory, list their memories, and POST /v1/memories/search to get their semantically-closest memories ranked — all tenant-scoped; another tenant's memories never appear and a cross-tenant id returns 404; an embedding-provider failure still stores the memory (text-retrievable)   (← memory-backend)
- [ ] A signed-in user can, in `/app/memory`, add a memory, see their list, and run a semantic search that returns ranked matches   (← memory-ui)

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
