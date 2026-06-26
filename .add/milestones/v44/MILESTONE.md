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
- [x] memory-backend   depends-on: none            — `gateway/memory/` domain: ORM + migration + repository + `/v1/memories` (store/list/search/delete) auth'd via KeyAuthenticator, embed-best-effort via the existing embeddings pipeline, in-Python cosine, STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + search + isolation contract. (gate PASS, 30 tests)
- [x] memory-ui        depends-on: memory-backend   — dashboard `/app/memory` (add + list + semantic search with ranked results) via the BFF; role-open nav entry. (gate PASS, 14 tests)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An API key holder can POST a memory, list their memories, and POST /v1/memories/search to get their semantically-closest memories ranked — all tenant-scoped; another tenant's memories never appear and a cross-tenant id returns 404; an embedding-provider failure still stores the memory (text-retrievable)   (← memory-backend)
- [x] A signed-in user can, in `/app/memory`, add a memory, see their list, and run a semantic search that returns ranked matches   (← memory-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : NEW `gateway/memory/` domain — a tenant-scoped, API-key-authenticated `/v1/memories` semantic store (create/list/cosine-search/delete) on the existing postgres:16-alpine (NO pgvector). Embeddings stored as float8[]; in-Python cosine over each tenant's rows; vectorization REUSES the existing embeddings pipeline best-effort (config: memory_embedding_model, memory_search_default_top_k). STRICT tenant isolation (incl. search — the ranker only sees tenant-scoped+non-deleted rows; cross-tenant → 404; raw vectors never returned). Embed-best-effort: a write commits before embedding; an embed failure → null embedding, still 201, search degrades to text match. Migration d8f0a2b4c6e8 (chained on c4d6e8f0a2b4); registered memories + (retro) conversations/conversation_messages in the tests/migrations EXPECTED_TABLES manifest (the upgrade-from-empty parity/idempotency guard now passes — fixed a v43 miss). 30 DB-backed tests; no-DB make test-fast 206.
- dashboard : NEW `/app/memory` workspace (add + list + semantic-search-with-score + delete, four states, WCAG-AA) via the BFF; lib/memories.ts BFF client; a role-open "Memory" nav entry. Honest score (null → "text match"); best-effort error handling. vitest 567 → 581 green; tsc 0; eslint 0.
- tooling / skill / book : untouched (only `.add/` task + milestone bookkeeping + the sanctioned EXPECTED_TABLES manifest edit).

### Cross-task evidence   (one row per task)
- memory-backend : gate=PASS · tests=30 green (DB-backed; no-DB make test-fast 206, no regression; tests/migrations 6/6 after manifest registration) · residue=an independent refute-read of tenant-isolation (incl. SEARCH) + embed-best-effort + migration UPHELD all 5 claims (no cross-tenant leak, no embed-failure 5xx, unique revision), 0.95; the one defensive note (set_embedding deleted_at guard) applied. Deltas: pgvector ANN index (scale), RAG auto-inject into chat, memory editing/dedup/decay, an explicit substring-fallback cross-tenant test.
- memory-ui : gate=PASS · tests=14 green (full dashboard 581, +14, zero regression; tsc 0; eslint 0) · residue=metadata-edit + a richer relevance display are deltas.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
  - EC1 (API key holder can store/list/semantic-search, tenant-scoped, embed-failure still stores): memory-backend — 30 DB-backed tests incl. test_search_ranks_by_cosine + test_search_isolation + test_raising_embedder_yields_201_and_null_embedding; refute confirmed no cross-tenant leak.
  - EC2 (signed-in user can add/list/semantic-search in /app/memory): memory-ui — 14 tests incl. add/search-ranked/list, all via the BFF over the EC1 store.
- goal: a user (and any API key holder) can store memories and recall them by semantic similarity via a tenant-scoped gateway store surfaced in the dashboard — proven by 30 gateway + 14 dashboard tests green (581 total dashboard, 206 no-DB gateway, no regression), strict tenant isolation (incl. search) independently refute-verified, ZERO new infra (in-Python cosine on the existing Postgres, reusing the embeddings pipeline), and design-for-failure (embed-best-effort → text fallback).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] v44 commits land on the v40→v44 task stack (committed locally): t1 memory-backend → t2 memory-ui → .add close. PUSH/PR await Tin's go-ahead (outward act).
- [ ] open a PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]]); v40–v44 are a stack — merge in order or retarget.
- [ ] deploy note: run `alembic upgrade head` to apply migration d8f0a2b4c6e8 (creates memories). NO new infra/env beyond setting GATEWAY_MEMORY_EMBEDDING_MODEL to a catalog-present embeddings model (empty ⇒ memory is text-search-only). pgvector is NOT used (in-Python cosine). No feature flag — routes are additive + tenant-scoped.
- [ ] v44 joins the releasable set (v33–v43 already pending); bundle into the next release cut when Tin calls it (release.md).
