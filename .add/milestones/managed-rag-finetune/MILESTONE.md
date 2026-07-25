# MILESTONE: Managed RAG + fine-tune brokering

goal: a tenant can upload files, build a managed vector store, retrieve over it via `file_search` inside a Responses/chat call, and broker a fine-tune job to its provider — all OpenAI-SDK-compatible, tenant-scoped, and exactly billed
rationale: sub-milestone — R5 of the Tin-approved roadmap refresh (2026-07-24), feeds release 0.13.0. Bucket `sub-milestone`: a slice of the "OpenAI-compatible enterprise platform" theme, too big for one task (5 tasks, two freeze-first contracts).
stage: mvp · status: active · created: 2026-07-24T08:07:15+00:00
relations: depends-on: api-surface-parity · extends: v44, openrouter-embeddings

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/PLAN.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  /v1/vector_stores (create/list/get/delete) + vector_store_files (attach a /v1/files file →
     chunk → embed → index) · the `file_search` server-side tool wired into /v1/responses and
     /v1/chat/completions (retrieve top-k chunks, inject as context, metered $/1k-queries via the
     pricing_unit dispatcher) · /v1/fine_tuning/jobs (create/list/get/cancel + events) brokered to
     the tenant's provider via BYOK, resulting model auto-registered in the tenant catalog with its
     own pricing snapshot · the storage-substrate decision (pgvector vs the existing float8[]+cosine
     path) as the freeze-first contract.
Out: training-data validation/transformation beyond format-check (provider owns it) · non-OpenAI
     fine-tune wire dialects beyond what BYOK providers natively expose · re-ranking / hybrid
     BM25+vector search (→ later) · agentic multi-hop retrieval · streaming-image / realtime
     surfaces · cost-optimizer + evals (→ R6/R7) · dashboard UI for these surfaces (API-first; UI is
     a follow-up milestone) · any change to batch scope.

## Ground   (shared real-code context — gathered ONCE; every task's specify projects from this)
Touches (shared files · symbols): apps/gateway/src/gateway/files/ (R4 file store — RAG/fine-tune
  input source) · memory/infrastructure/orm.py (float8[] embedding store precedent) ·
  proxy/infrastructure/vector_cache.py (cosine-similarity seam) · proxy embeddings path (chunk→embed) ·
  responses_store/ + proxy/api/responses_router.py (file_search host) · catalog/ (fine-tuned model
  registration + pricing snapshot) · usage/ (metering) · batches/ (job-store pattern precedent).
Anchors: the /v1/files File object + ObjectStore port (R4 frozen) · the embeddings ChatTranslator/
  provider seam · pricing_unit dispatcher (per_token + a NEW per_query unit for file_search) ·
  catalog ModelRow + pricing_snapshots · tenant-scoped repository idiom · the batches job-store shape.
Honors (conventions): tenant_id on every row, 404-never-leak · exactly one usage record per billable
  op on the served model · no outbound IO without timeout+retry+breaker · byte-identical default path ·
  ZDR/retention compose on the NEW vector-store payload-at-rest · fine-tune BYOK credentials handled
  like every other BYOK seam (Fernet, never logged) · fail-closed security, honest degradation ·
  new tables go in BOTH manifests (tests/migrations EXPECTED_TABLES + guardrails NOT-IN list).
Issues/Risks (shared): storage substrate (pgvector migration vs float8[] brute-force cosine — recall
  vs infra cost, THE freeze-first call) · vector store is a NEW payload-at-rest store → ZDR/retention
  obligations · file_search retrieval billing must not double-count against the completion bill ·
  fine-tune brokering handles tenant training data (privacy + a real cross-tenant confused-deputy
  surface on the provider credential → security-sensitive) · a brokered fine-tuned model's pricing
  snapshot must resolve through the ONE shared rate-card resolver.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Vector store** (glossary NEW): a tenant-scoped named collection of embedded, chunked file content
  queryable by cosine similarity; distinct from the internal response vector-CACHE (an optimization) —
  this is a product resource.
- **file_search** (glossary NEW): a server-side tool that retrieves top-k chunks from a vector store
  and injects them as context; billed per_query, never a second inference bill.
- **Fine-tune job** (glossary NEW): a tenant-owned brokered training job proxied to a BYOK provider;
  the resulting model is a first-class catalog ModelRow with its own pricing snapshot.
- Billing: file_search = a NEW per_query pricing_unit; fine-tune = pass-through provider cost + the
  tenant's markup via the shared rate-card resolver; no new billing mechanism invented.

## Shared / risky contracts (freeze these first)
- Storage substrate + vector-store schema (**Tin-chose pgvector 2026-07-24** — ANN index, recall/latency at scale; the freeze must add the extension to every deploy target incl. e2e/kind + a migration; chunk/index shape still open) -> owning task vector-store-core
- Fine-tune job store + BYOK brokering contract (credential handling, model registration) -> owning task finetune-broker

## Tasks (breadth-first decomposition; detail lives in each PLAN.md)
- [x] vector-store-core     depends-on: none               — /v1/vector_stores CRUD + the storage-substrate decision; freeze-first
- [x] vector-store-files    depends-on: vector-store-core  — attach a /v1/files file → chunk → embed → index; ingestion job + status
- [x] file-search-tool      depends-on: vector-store-files — file_search server-side tool in /v1/responses + chat; top-k retrieval, per_query metering
- [x] finetune-broker       depends-on: none               — /v1/fine_tuning/jobs brokered to BYOK provider; job store; freeze-first  [sensitivity: security]
- [x] finetune-model-registry depends-on: finetune-broker  — auto-register the resulting fine-tuned model in the tenant catalog with a pricing snapshot  [sensitivity: data]

## Exit criteria (observable; map each to the task that delivers it)
- [x] `client.vector_stores.create(...)` then list/get/delete work tenant-scoped; another tenant can't see the store        (← vector-store-core)
- [x] `client.vector_stores.files.create(vector_store_id, file_id=...)` chunks+embeds+indexes the R4-uploaded file; status reaches completed        (← vector-store-files)
- [x] a Responses/chat call with the `file_search` tool retrieves relevant chunks and bills exactly one per_query line (no double inference bill)        (← file-search-tool)
- [x] `client.fine_tuning.jobs.create(...)` brokers to the tenant's BYOK provider; another tenant's job/credential is never reachable        (← finetune-broker)
- [x] a completed fine-tune's model appears in the tenant catalog and is callable + billed via the shared rate-card resolver        (← finetune-model-registry)

## Strategy   (AI-drafted WITH the human — SOFT/advisory)
- Approach (sequencing): risk-first — freeze the two substrate contracts first (vector-store schema, fine-tune broker), then run two independent chains: RAG (vector-store-core → vector-store-files → file-search-tool) ∥ fine-tune (finetune-broker → finetune-model-registry).
- Freeze-first: vector-store-core §3 (pgvector-vs-float8 substrate — recall/cost tradeoff is the load-bearing decision) and finetune-broker §3 (BYOK credential handling — security).
- Waves (parallel): wave-1 = vector-store-core ∥ finetune-broker (both freeze-first, independent); wave-2 = vector-store-files ∥ finetune-model-registry; wave-3 = file-search-tool.
- Tradeoffs weighed: (a) pgvector migration (better recall at scale, new extension dependency) vs reusing the float8[]+brute-force cosine path (zero new infra, fine at tenant-doc scale, worse at 100k+ chunks) — a genuine freeze decision for Tin, not an AI default; (b) our own re-ranker vs top-k cosine only — deferred; (c) fine-tune brokering now (R4 files shipped, so the input source exists) vs deferring — now.

## Close — ship review   (AI fills when every task is done)
### Ship by domain
- gateway (BE) : /v1/vector_stores CRUD (pgvector HNSW) · /v1/vector_stores/{id}/files async ingestion · file_search tool in /v1/responses+chat with per_query metering · /v1/fine_tuning/jobs BYOK brokering · fine-tuned-model catalog auto-registration. Migrations 55dc3f920a38 → 6f2a9c1e3b7d → b3d8f21ca9e6 → c7f1a4e83b92 (single head).
- infra   : dev/e2e/prod compose + charts moved to pgvector/pgvector:pg16 (the `vector` col on shared Base.metadata makes every suite's create_all need the extension); conftest provisions CREATE EXTENSION on xdist + serial + app-fixture paths.
- book    : no doc-shape drift (no public README/API surface removed; additive endpoints only).
### Cross-task evidence
- vector-store-core — 17✓, refute CLEAR; tenant-scoped 404, pgvector HNSW cosine, `vs_<32hex>`.
- vector-store-files — 21✓; async ingest worker; ZDR TOCTOU HARD-STOP found by refute → healed atomic (dd5373a).
- file-search-tool — 16✓ + e2e through real app; per_query=1 (no double bill); refute found dormant-grounder/M5-leak + a residual ZDR TOCTOU (non-locking re-check) → both healed (grounder wired at deps.py+realtime_ws; FOR UPDATE locked re-check 64bb65b).
- finetune-broker — 22✓; DUAL security refute CLEAR; per-tenant breaker (CR-1 recurrence healed), fail-closed 402.
- finetune-model-registry — 13✓; listener→catalog ModelRow+pricing_snapshot ×1.0 passthrough; ft: preset-parse defect healed.
### Goal met?
- [x] each Exit criterion satisfied by a Cross-task evidence row
- goal: MET — a tenant can build a vector store, ingest R4-uploaded files, retrieve via file_search in a Responses/chat call (exactly one per_query bill), and broker a fine-tune job whose resulting model auto-registers + bills through the shared rate-card resolver; all OpenAI-SDK-compatible, tenant-scoped, exactly billed. Security HARD-STOPs (ZDR ×2, shared breaker) all caught by adversarial refute and healed before gate.

## Release steps   (AI-DEFINED; human gate)
- [ ] full gateway suite (chunked ≤ -n 6) + dashboard suite before PR
- [ ] one PR from the ship-review; Tin reviews + merges
- [ ] live SDK smoke: openai SDK vector_stores + file_search + fine_tuning happy paths
- [ ] hand-write the 0.13.0 RELEASES/CHANGELOG rows after merge (engine has no release subcommand)
