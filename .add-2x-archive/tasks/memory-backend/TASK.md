# TASK: gateway/memory domain: store + embed + cosine search + tenant isolation

slug: memory-backend · created: 2026-06-26 · stage: production
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
  - `apps/gateway/src/gateway/memory/` (NEW domain, mirrors gateway/conversations/ from v43) — `infrastructure/orm.py` (MemoryRow on Base: id, tenant_id, key_id, content TEXT, embedding `ARRAY(Float)` NULL, metadata JSONB NULL, created_at, deleted_at NULL), `infrastructure/repository.py` (MemoryRepository — tenant-scoped create/list/get/soft_delete + `list_with_embeddings(tenant_id)` for search), `application/search.py` (pure cosine_similarity + rank helper), `api/router.py` (memories_router, 4 endpoints), `api/schemas.py`.
  - `apps/gateway/migrations/versions/<rev>_memories.py` (NEW) — create `memories`, `down_revision="c4d6e8f0a2b4"` (current head). Index ix_memories_tenant_created (tenant_id, created_at DESC).
  - `apps/gateway/src/gateway/core/config.py` (MODIFY, additive) — `memory_embedding_model: str = Field(default="")` (empty ⇒ embedding disabled, text-only) + `memory_search_default_top_k: int = Field(default=5, ge=1, le=100)`.
  - `apps/gateway/src/gateway/main.py` (MODIFY) — include_router(memories_router).
  - `apps/gateway/tests/memory/` (NEW) — DB-backed tests (mirror tests/conversations/test_conversations.py for the app+client+seeded-key fixtures; STUB the embed call via a fake on app.state, NEVER hit a live provider).
Context (working folder):
  - Auth seam (REUSE v43 exactly): KeyAuthenticator.authenticate(raw_key) → AuthzResult{tenant_id,key_id,expires_at}; the v43 conversations router's `_authenticate` (sk- key + the expiry gate) is the template — copy it. AUTH_KEY_INVALID/AUTH_KEY_EXPIRED in error_catalog.
  - Embeddings reuse: build an `EmbeddingsUseCase` exactly like `proxy/api/embeddings_deps.get_embeddings_use_case` (repo→authz→authenticator→model_checker→NonChatGovernance→EmbeddingsUseCase). To embed text: `status, body, _ = await uc.execute(raw_key=raw_key, body={"model": cfg.memory_embedding_model, "input": text}, registry=app.state.provider_registry, usage_recorder=app.state.usage_recorder)`; on status==200 parse `body["data"][0]["embedding"]` → list[float]; on ANY exception/non-200/empty-model → return None (best-effort). This bills the tenant for the embed (honest) + runs their governance.
  - DB: get_session (core/db.py:73); ORM subclasses gateway.core.db.Base; index in BOTH __table_args__ AND the migration (v30 lesson); tables auto-create in tests via create_all.
Honors (patterns / conventions):
  - TENANT-ISOLATION (security HARD invariant, same as v43): every query (incl. search's list_with_embeddings) filters tenant_id == authz.tenant_id; cross-tenant id → 404; search results NEVER include another tenant's memory.
  - EMBED-BEST-EFFORT / DESIGN-FOR-FAILURE: a write stores the row FIRST, then embeds best-effort (null on failure); an embedding failure NEVER fails the write. Search: cosine over non-null embeddings when the query embeds; null-embedding rows + a failed query-embed → substring text fallback. No partial write (one txn for the row; the embed-update is a follow-up best-effort write).
  - Additive: /v1/embeddings + all existing routes untouched; new domain only.
Anchors the contract cites:
  - `MemoryRow` · `MemoryRepository` (tenant-scoped + list_with_embeddings) · `cosine_similarity`/rank · `memories_router` (4 endpoints) · `EmbeddingsUseCase` reuse for embed · the 404-cross-tenant + embed-best-effort rules.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a tenant-scoped, API-key-authenticated `/v1/memories` semantic store — create/list/search/delete memories, vectorized best-effort via the existing embeddings pipeline, ranked by in-Python cosine; STRICT tenant isolation. The platform's "remote memory" primitive.
Framings weighed: a new `gateway/memory/` domain reusing KeyAuthenticator + the embeddings pipeline + in-Python cosine over a float-array column (chosen — mirrors v43, ZERO new infra on postgres:16-alpine) · pgvector Vector column + ANN index (rejected for MVP — needs a pgvector DB image = real infra; a scale delta) · store-only without embeddings, text search only (rejected — "semantic" is the point; embed-best-effort gives semantic when available + text fallback when not).
Must:
<must>
  - M1 — `POST /v1/memories` (auth: Bearer sk- key) stores {content, metadata?} owned by the authenticated tenant_id/key_id; returns {id, content, created_at}. The row is stored even if embedding fails (embedding best-effort).
  - M2 — `GET /v1/memories` lists ONLY the tenant's non-deleted memories, newest first, paginated (limit default from config cap 200, offset); returns items (NOT the raw embedding vector).
  - M3 — `POST /v1/memories/search` {query, top_k?} embeds the query best-effort, ranks the tenant's memories by cosine similarity (desc), returns the top_k with a `score`; when the query can't be embedded OR for null-embedding rows, falls back to substring text match (score null/0). NEVER returns another tenant's memory.
  - M4 — `DELETE /v1/memories/{id}` soft-deletes the tenant's memory (deleted_at); a deleted/unknown/cross-tenant id → 404; a deleted memory no longer lists or matches search.
  - M5 — TENANT ISOLATION: every endpoint + search filters by authz.tenant_id; a cross-tenant id is indistinguishable from missing (404). Auth absent/invalid/expired → 401.
  - M6 — EMBED-BEST-EFFORT: an embedding-provider failure (non-200, exception, empty configured model) leaves embedding=null and the write still succeeds; search still works (text fallback). No request fails because embedding failed.
</must>
Reject:
<reject>
  - no/invalid/expired Bearer key -> 401.
  - cross-tenant or unknown memory id -> 404 (NOT 403 — no existence leak).
  - empty/whitespace content (create) or empty query (search) -> 422.
  - top_k/limit out of bounds -> clamp (top_k 1..100, limit<=200, offset>=0); never an unbounded scan.
</reject>
After:
<after>
  - An API key holder can store memories, list them, and semantic-search to get their closest memories ranked; another tenant can never see or match them (404 / absent); an embedding outage degrades to a stored-but-text-searchable memory; /v1/embeddings + other routes unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ reusing EmbeddingsUseCase internally (not via HTTP) for the embed — lowest confidence because execute() expects a request-shaped body + runs full governance/billing; if the configured model isn't in the tenant's catalog/allowlist, governance raises → caught as best-effort null (degraded, not broken). Mitigation: wrap the whole embed in try/except → None; tests stub the embed via app.state (never a live provider). Cost if wrong: memories store with null embeddings (text-only search) until the model is configured — no data loss.
  - [x] postgres:16-alpine has no pgvector — CONFIRMED → float-array column + in-Python cosine.
  - [x] KeyAuthenticator + expiry gate pattern exists — CONFIRMED (v43 conversations _authenticate).
  - [ ] embedding storage type — chose `ARRAY(Float)` (native float8[], round-trips list[float]); JSONB is the alt (chose ARRAY for clarity).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Store and list a memory
  Given an authenticated tenant key and a stubbed embedder
  When POST /v1/memories {content:"Paris is the capital of France"} then GET /v1/memories
  Then the memory appears in the list with its content; the raw embedding is NOT exposed

Scenario: Semantic search ranks by cosine
  Given three memories with distinct stub embeddings stored for the tenant
  When POST /v1/memories/search {query:"French capital", top_k:2}
  Then exactly 2 results return, ordered by descending cosine score, closest first

Scenario: Tenant isolation on search and get (the security invariant)
  Given tenant A has memories and tenant B has none
  When tenant B POSTs /v1/memories/search and GETs/DELETEs A's memory id
  Then B's search returns no A memories and B's get/delete of A's id → 404
  And A's memories are intact

Scenario: Embedding failure still stores the memory
  Given the stub embedder raises / returns non-200
  When POST /v1/memories {content:"x"}
  Then the write succeeds (201) with embedding null, and the memory is returned by a substring search for "x"
  And no 5xx is surfaced for the embedding failure

Scenario: Soft-delete hides from list and search
  Given a stored memory
  When DELETE /v1/memories/{id} then GET /v1/memories and POST /v1/memories/search
  Then it is absent from both and GET /v1/memories/{id}-style access → 404

Scenario: Auth and validation rejections
  Given no/invalid Bearer or bad input
  When calling the endpoints
  Then missing key → 401; empty content → 422; empty query → 422
  And no memory row is created
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
ALL routes auth: Bearer sk- → KeyAuthenticator.authenticate → AuthzResult{tenant_id,key_id,expires_at}
  (absent/invalid → 401; expired → 401, same expiry gate as v43). EVERY query filters tenant_id.

POST   /v1/memories          {content: str(non-empty), metadata?: object}  -> 201 {id, content, created_at}
GET    /v1/memories?limit&offset                                           -> 200 {data:[{id, content, created_at, has_embedding: bool}], limit, offset}  (newest created first, deleted_at IS NULL; embedding vector NOT returned)
POST   /v1/memories/search   {query: str(non-empty), top_k?: int}          -> 200 {data:[{id, content, score: float|null, created_at}]}  (top_k clamp 1..100 default cfg; ranked by cosine desc; text fallback score null)
DELETE /v1/memories/{id}                                                   -> 204 (soft: deleted_at=now) | 404

Embed (internal, best-effort): build EmbeddingsUseCase (like embeddings_deps); embed(text)->list[float]|None
  via uc.execute(raw_key, {"model": cfg.memory_embedding_model, "input": text}); 200→data[0].embedding else None.
  cfg.memory_embedding_model == "" ⇒ embed disabled ⇒ always None (text-only). ALL failures → None (never raise).
cosine_similarity(a,b): dot/(||a||*||b||); 0 if either is null/empty or dims differ. rank: filter same-dim non-null,
  sort by score desc, take top_k; if query embed is None → substring(content contains query, case-insensitive), score null.

Schema (NEW, migration down_revision="c4d6e8f0a2b4"):
  memories(id UUID PK, tenant_id UUID NOT NULL, key_id UUID NOT NULL, content TEXT NOT NULL,
           embedding DOUBLE PRECISION[] NULL, metadata JSONB NULL,
           created_at timestamptz default now, deleted_at timestamptz NULL)
    INDEX ix_memories_tenant_created (tenant_id, created_at DESC)  -- in ORM __table_args__ AND migration
  Access: one AsyncSession/request; create row → commit; then best-effort embed → UPDATE embedding (tenant+id scoped,
  separate best-effort write). Search loads the tenant's (id, content, embedding) rows, ranks in Python.
Config (additive): memory_embedding_model: str = "" · memory_search_default_top_k: int = 5 (1..100).
```

Status: FROZEN @ v1 — auto-approved EXCEPT the tenant-isolation security surface (built to the 404/no-cross-tenant rule + INDEPENDENTLY refute-verified at the gate). Full-auto; new additive domain; reuses the proven v43 auth/isolation + the existing embeddings pipeline; the embedding-storage choice (float8[] + in-Python cosine) is the conservative no-new-infra path. 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] TENANT ISOLATION extends to SEARCH — the search must rank ONLY over the authenticated tenant's rows (list_with_embeddings(tenant_id)); a missing tenant filter on the search load = a cross-tenant memory leak in ranked results. Mitigation: the repo's search-load takes tenant_id; an explicit cross-tenant search test; a gate refute-read. Cost if wrong: HIGH (semantic data leak).
  - [spec] embed-best-effort firing — must NEVER raise into the request; if the configured model is absent/governance-blocked, the catch must degrade to null, not 5xx. Tested with a raising stub embedder.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — DB-backed (httpx ASGITransport + real Postgres :5433); mirror tests/conversations/test_conversations.py for app+client+seeded-key fixtures (≥2 tenant keys for isolation). STUB the embedder on app.state (a fake that returns a deterministic vector per text, or raises) — NEVER a live provider.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_store_and_list: POST then GET → content present; response has NO raw embedding array.
  - test_search_ranks_by_cosine: 3 memories with stub vectors; search top_k=2 → 2 results, score-descending, closest first.
  - test_tenant_isolation: tenant B search returns none of A's; B get/delete of A's id → 404; A intact.
  - test_embed_failure_still_stores: stub embedder raises → POST still 201 (embedding null); substring search finds it; no 5xx.
  - test_soft_delete_hides: DELETE → absent from list AND search; cross access → 404.
  - test_auth_and_validation: no Bearer → 401; expired key → 401; empty content → 422; empty query → 422; no row created.
</test_plan>

Tests live in: `apps/gateway/tests/memory/test_memory.py` · MUST run red (missing implementation) before Build. (DB-backed → run `uv run pytest tests/memory`; NOT in make test-fast.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/memory/` · `apps/gateway/migrations/versions/` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/memory/` · `apps/gateway/tests/migrations/test_migrations.py`
  (test_migrations.py: SANCTIONED manifest maintenance — register the new `memories` table (+ retro-register v43's `conversations`/`conversation_messages`) in EXPECTED_TABLES so the upgrade-from-empty parity guard passes. The migrations apply correctly; the allowlist was just stale.)
Strategy (ordered batches): 1. ORM (MemoryRow) + migration (head c4d6e8f0a2b4) + config knobs. 2. repository (tenant-scoped create/list/get/soft_delete/list_with_embeddings) + application/search.py (cosine + rank). 3. router (4 endpoints + _authenticate copied from v43 incl expiry + the best-effort embed helper) + main.py include. 4. DB-backed tests with a stubbed embedder.
Safety rule (feature-specific): TENANT ISOLATION — every repo method (incl. the search load) takes tenant_id and filters on it; no unscoped read exists; cross-tenant/unknown → None → 404. EMBED-BEST-EFFORT — the row is committed BEFORE embedding; the embed helper catches ALL exceptions/non-200 → None; an embedding failure never raises into the request or fails the write. No raw SQL interpolation.
Code lives in: `apps/gateway/`
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

- [x] all tests pass — memory suite 30/30 (DB-backed); no-DB make test-fast 206 (no regression); tests/migrations 6/6 after registering the new tables.
- [x] coverage did not decrease — 30 new behavioral tests (new domain had 0).
- [x] no test or contract was altered during build — new tests only; §3 contract unchanged. The set_embedding deleted_at guard + the EXPECTED_TABLES registration STRENGTHEN. tests/migrations EXPECTED_TABLES is sanctioned manifest maintenance (added conversations/conversation_messages/memories with attribution).
- [x] the green was EARNED — independent refute-read (sonnet) on the memory tenant-isolation + embed-best-effort + migration surface: ALL 5 claims UPHELD, NO BLOCKER, 0.95. Search isolation is structural (repo.load_for_search double-filters tenant_id + deleted_at IS NULL before the in-Python ranker, which has NO DB access). Cosmetic findings only; the one I acted on (set_embedding missing deleted_at guard) is now fixed.
- [x] concurrency / timing safe — the row is committed BEFORE the best-effort embed; an embed failure can't fail/poison the write (two nested except Exception in _embed + an outer catch on create → always 201). set_embedding is now tenant+active scoped (deleted_at IS NULL).
- [x] no exposed secrets / injection / unexpected deps — bound params only; tenant_id ONLY from AuthzResult (no body/query override; the request schemas have no tenant_id field); the raw embedding float[] is NEVER in any response (create/list/search schemas expose content + has_embedding/score only); no new packages.
- [x] layering & dependencies follow CONVENTIONS.md — mirrors gateway/conversations (api/infrastructure/application); reuses the embeddings pipeline; additive (no existing route touched).
- [x] reviewed — full-auto self-review + independent refute-read of the security surface (tenant isolation incl. SEARCH UPHELD). (Outward PR/push deferred.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] semantic search ranks by cosine, top_k, tenant-only — confirmed by test_search_ranks_by_cosine (3 stub vectors, score-descending) + the refute (CLAIM 1 UPHELD: load_for_search filters tenant_id + deleted_at; ranker has no DB access).
- [x] every repo method tenant-scoped incl. the search load — confirmed by reading repository.py: create/list_active/load_for_search/soft_delete/set_embedding all keyword-only tenant_id; cross-tenant → 404 (test_delete_cross_tenant_returns_404; test_search_isolation B sees none of A).
- [x] embed failure still stores (best-effort) — confirmed by test_raising_embedder_yields_201_and_null_embedding (raising stub → 201, embedding null, substring search finds it, no 5xx).
- [x] migration applies + chains + unique id — confirmed by alembic single head d8f0a2b4c6e8, offline SQL render (CREATE TABLE memories float8[] embedding + JSONB metadata + DESC index, version_num c4d6e8f0a2b4→d8f0a2b4c6e8), tests/migrations upgrade-from-empty parity + idempotency GREEN, refute confirmed no duplicate revision.
- [x] expired key rejected — confirmed by the expiry-gate test (force-expire in DB → 401), copied from v43.

### Deep checks
- [x] WIRING (code) — memories_router in main.py (+ ORM side-effect import); 30 tests exercise all 4 endpoints + search + embed-failure end-to-end (real auth → repo → DB, stubbed embedder on app.state).
- [x] DEAD-CODE (code) — no orphaned symbol; pyright 0 + ruff clean on the new domain + tests + migration.
- [x] SEMANTIC — refute-read read router/repository/search/orm/migration in full; verdicts cited. Found + fixed the 2 stale tests/migrations failures (mine: v43+v44 table registration) and confirmed the other 3 full-suite failures (azure×2 v42-residue, guardrails×1) are pre-existing, unrelated to v44 (never touched by my commits).

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto (Tin's "complete all milestones in auto mode") + independent refute-read (sonnet, tenant-isolation incl. SEARCH + embed-best-effort + migration ALL UPHELD, 0.95, no blocker) · date: 2026-06-26

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
