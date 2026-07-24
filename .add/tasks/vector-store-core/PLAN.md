# PLAN: /v1/vector_stores CRUD + pgvector substrate

slug: vector-store-core · created: 2026-07-24 · stage: production
milestone: managed-rag-finetune
autonomy: auto   <!-- manual<conservative<auto — lower for high-risk (`add.py autonomy set`); a `component: <name>` line joins that root to §3 Scope; task edges: `--depends-on`/`--extends`/`--relates-to`; high-risk/method-defining? declare `risk: high` on the slug line; headless agent-crossed freeze? declare `gate_mode: ai-plan-verify` here (human floor: security|data|architecture never AI-frozen) -->
phase: build   <!-- direction→build→verify→done; direction drafts §1–§4 (rules · change plan · red suite) to the ONE freeze -->
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: vector-store-core — OpenAI-compatible `/v1/vector_stores` CRUD + the pgvector storage substrate (extension, schema, ANN index) the whole RAG chain builds on.
Framings weighed: substrate-first freeze — tables + extension + CRUD in ONE contract so wave-2/3 never re-freeze (chosen — the Tin-locked pgvector decision IS the load-bearing part; shipping CRUD without the chunk schema would force a second freeze at vector-store-files) · CRUD-only now, chunk schema later (rejected — violates the WAVE1 extension-point mandate) · extend `proxy/infrastructure/vector_cache.py` float8[]+cosine (rejected — DISPLACED prior art, Tin-locked pgvector 2026-07-24).
Must:
<must>
  - M1 create: POST /v1/vector_stores {name?, metadata?} -> 200 OpenAI vector store object (`vs_<32hex>` wire id, object:"vector_store", unix created_at, status:"completed", usage_bytes:0, zeroed file_counts); name and metadata both optional.
  - M2 list: GET /v1/vector_stores?limit&offset -> {object:"list", data:[...], has_more}; tenant-scoped, newest first; limit default 20 cap 100 (OpenAI default), offset >= 0.
  - M3 get: GET /v1/vector_stores/{id} -> the object, tenant-scoped.
  - M4 delete: DELETE /v1/vector_stores/{id} -> {id, object:"vector_store.deleted", deleted:true}; HARD delete — one atomic `DELETE … WHERE id AND tenant_id RETURNING id`, FK CASCADE wipes vector_store_files + vector_store_chunks (payload-at-rest hygiene: no orphan embeddings behind a soft-hidden row).
  - M5 tenant isolation: every query keyed by the authenticated key's tenant_id; cross-tenant/unknown/malformed id -> ONE uniform 404 (no enumeration oracle, files-uploads-api precedent).
  - M6 substrate: migration runs `CREATE EXTENSION IF NOT EXISTS vector` + creates vector_stores / vector_store_files / vector_store_chunks with `embedding vector(1536)` + HNSW `vector_cosine_ops` index; ORM parity (indexes in BOTH __table_args__ and migration, v30 lesson); all three tables in BOTH manifests (tests/migrations EXPECTED_TABLES + guardrails NOT-IN).
  - M7 billing: the CRUD surface has no provider cost -> writes ZERO usage_records (per_query is file-search-tool's, NOT here).
  - M8 ZDR compose: the store CONTAINER is metadata-only -> a ZDR tenant may create/list/get/delete; the payload gate (`raise_if_zdr` first line of the chunk-write choke point) is a named wave-2 obligation pinned by this contract.
  - M9 byte-identical default path: a request not touching /v1/vector_stores engages zero new plumbing (new router + side-effect ORM import only; no middleware, no proxy-path change).
</must>
Reject:
<reject>
  - name > 256 chars -> "ERR_VECTOR_STORE_NAME_TOO_LONG" (422; nothing persisted)
  - metadata not a mapping of <=16 string:string pairs (key<=64, value<=512 chars) -> "ERR_VECTOR_STORE_METADATA_INVALID" (422; nothing persisted)
  - unknown | cross-tenant | malformed vector-store id -> "ERR_VECTOR_STORE_NOT_FOUND" (404, uniform — absent/foreign/garbage indistinguishable)
  - missing/invalid bearer key -> "ERR_AUTH_INVALID_KEY" (401, existing catalog); expired -> "ERR_AUTH_KEY_EXPIRED" (401)
</reject>
After:
<after>
  - Tenant owns N vector_stores rows queryable via the OpenAI SDK (`client.vector_stores.create/list/retrieve/delete`); the chunk substrate (typed vector(1536) column + HNSW cosine index + extension) exists EMPTY, ready for wave-2 attach and wave-3 top-k without any schema change; both table manifests updated; every deploy target's postgres ships pgvector.
</after>
Boundary: wire id `vs_<32hex>` vs internal UUID (mirror of files `wire_id.py`, underscore per OpenAI vs-id shape) · metadata JSON object vs absent (absent -> {}) · limit/offset ints as query strings.
<assumptions>
  ⚠ embedding dimension fixed at 1536 (text-embedding-3-small) — lowest confidence because the tenant's embedding model is a server-side config, and a 3072-dim model (text-embedding-3-large) cannot use a `vector(1536)` column NOR an HNSW index over `vector` >2000 dims; if wrong: a new additive migration (halfvec/second column) + re-embed, but NOT an API-wire change — the wire never exposes the dimension. Mitigated by recording embedding_model + embedding_dim per store row.
  ⚠ swapping every postgres image to `pgvector/pgvector:pg16` is drop-in — if wrong (volume init mismatch, alpine->debian): dev/e2e stacks need a volume reset; data loss is nil (dev/e2e only, prod is operator-managed).
</assumptions>

<!-- §2 (the old standalone SCENARIOS section) was RETIRED — pass/fail cases now live with the tests in §4 · TESTS & SCENARIOS. The §3–§7 numbers are unchanged so the freeze parser and every §-reference keep working; the jump from §1 to §3 is intentional. -->

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

Grounding anchors (the Contract may cite ONLY these — all [OBSERVED] this session @ main):
- `gateway.files.infrastructure.orm.FileRow` + `gateway.files.wire_id.to_wire_id/parse_wire_id` + `gateway.files.api.router` (`_authenticate`, `_not_found`, `_file_object`) — the wire-store module template this task mirrors.
- `gateway.core.error_catalog.ErrorSpec` (FILE_NOT_FOUND shape) · `gateway.core.db.Base` / `get_session`.
- `gateway.tenants.application.retention_policy.raise_if_zdr` — the ZDR choke-point predicate (wave-2 obligation).
- `gateway.main` — router include block (`app.include_router(files_router)`) + side-effect ORM import pattern (`StoredResponseRow` noqa F401 precedent).
- Alembic head `c7e0a4b2d9f1` (stored_responses) — the new migration's down_revision; migration shape mirrors `f9d3b1a7c2e4_files_uploads_api.py`.
- `tests/migrations/test_migrations.py::EXPECTED_TABLES` + `tests/guardrails/test_guardrails_core.py` pg_tables NOT-IN list — the two table manifests.
- `usage/application/retention_sweep.py` (`_purge_files_batch` et al.) — the sweeper wave-2 chunk-sweep registration mirrors.
- DISPLACED prior art [PRIOR, re-confirmed live]: `proxy.infrastructure.vector_cache.cosine_similarity` (Redis response-CACHE, not a product store) · `memory.infrastructure.orm.MemoryRow.embedding` (DOUBLE PRECISION[], best-effort) — cited, never extended.
- Envoy `/v1/*` ext_authz prefix rule (`infra/envoy/envoy.yaml` route table) — /v1/vector_stores rides it; NO Envoy change.
- Postgres image declarations [OBSERVED]: `infra/docker-compose.dev.yml` (:5433, postgres:16-alpine) · `infra/docker-compose.e2e.yml` · `infra/docker-compose.prod.yml` · `charts/ai-proxy/values.yaml` `datastores.postgres.image` (kind/helm).

```
POST /v1/vector_stores   body: { name?: str|null, metadata?: {str:str} }
  200 -> { id:"vs_<32hex>", object:"vector_store", created_at:int, name:str|null,
           usage_bytes:0, status:"completed",
           file_counts:{in_progress:0,completed:0,failed:0,cancelled:0,total:0}, metadata:{} }
  422 -> { error: "ERR_VECTOR_STORE_NAME_TOO_LONG" | "ERR_VECTOR_STORE_METADATA_INVALID" }
  401 -> { error: "ERR_AUTH_INVALID_KEY" | "ERR_AUTH_KEY_EXPIRED" }
GET /v1/vector_stores?limit=20&offset=0   (limit cap 100)
  200 -> { object:"list", data:[<vector_store>...], has_more:bool }   # newest first, tenant-scoped
GET /v1/vector_stores/{vs_id}
  200 -> <vector_store>        404 -> { error: "ERR_VECTOR_STORE_NOT_FOUND" }  (uniform, no oracle)
DELETE /v1/vector_stores/{vs_id}
  200 -> { id, object:"vector_store.deleted", deleted:true }   404 -> uniform as above
  # HARD delete: one atomic DELETE..RETURNING keyed (id, tenant_id); CASCADE wipes vsf + chunks.

Schema (migration <new-rev>, down_revision="c7e0a4b2d9f1"; CREATE EXTENSION IF NOT EXISTS vector FIRST):
  vector_stores(id UUID pk gen_random_uuid, tenant_id UUID NOT NULL, key_id UUID NOT NULL,
    name TEXT NULL, status TEXT NOT NULL DEFAULT 'completed',
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    embedding_dim INT NOT NULL DEFAULT 1536,
    metadata JSONB NULL, usage_bytes BIGINT NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now())
    Index ix_vector_stores_tenant_created (tenant_id, created_at DESC)
  vector_store_files(id UUID pk gen_random_uuid,
    vector_store_id UUID NOT NULL REFERENCES vector_stores(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL, file_id UUID NOT NULL,   -- soft ref to files.id (no cross-module FK, files precedent)
    status TEXT NOT NULL DEFAULT 'in_progress',       -- in_progress|completed|failed (wave-2 drives it)
    last_error JSONB NULL, usage_bytes BIGINT NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vector_store_id, file_id))                -- idempotent attach for wave-2
    Index ix_vector_store_files_store_created (vector_store_id, created_at DESC)
  vector_store_chunks(id BIGINT pk GENERATED ALWAYS AS IDENTITY,
    vector_store_file_id UUID NOT NULL REFERENCES vector_store_files(id) ON DELETE CASCADE,
    vector_store_id UUID NOT NULL,                    -- denormalized top-k scope (wave-3 WHERE)
    tenant_id UUID NOT NULL,                          -- denormalized defense-in-depth
    chunk_index INT NOT NULL, content TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now())
    Index ix_vector_store_chunks_store (vector_store_id)
    Index ix_vector_store_chunks_embedding_hnsw USING hnsw (embedding vector_cosine_ops)
  Access pattern: CRUD keyed (tenant_id, id); wave-3 top-k =
    SELECT … ORDER BY embedding <=> :q WHERE vector_store_id=:vs AND tenant_id=:t LIMIT :k (HNSW-served).

Index decision — HNSW over IVFFlat (justification, Tin-locked "choose + justify"):
  IVFFlat trains its lists from rows PRESENT at index build; this migration creates EMPTY tables,
  so an IVFFlat index would be built untrained (useless recall) and need a REINDEX after every
  large ingest. HNSW builds incrementally — correct from row 1, no training step, better
  recall/latency at query time; its higher build cost/memory is irrelevant at tenant-doc scale.
  Both index types cap at 2000 dims for `vector` -> reinforces dim 1536. Cosine ops per the
  milestone glossary ("queryable by cosine similarity"). ef/m stay pgvector defaults (m=16,
  ef_construction=64) — tuning is a wave-3 measurement, not a freeze.

Extension provisioning — EVERY deploy target (all four postgres declarations [OBSERVED]):
  1. gateway image / codebase: add `pgvector` Python package (SQLAlchemy `Vector` type) to
     apps/gateway pyproject — the ONE new dependency (allow-list addition, approved by this freeze).
  2. infra/docker-compose.dev.yml  postgres:16-alpine -> pgvector/pgvector:pg16   (:5433 shared test DB — REQUIRED
     before the suite can go green: the chunk ORM registers a Vector column on Base.metadata, so EVERY suite's
     create_all needs the extension; root tests/conftest.py gains an idempotent CREATE EXTENSION IF NOT EXISTS vector
     before create_all + in _ensure_worker_database). Operator step: recreate the dev postgres container.
  3. infra/docker-compose.e2e.yml  postgres:16-alpine -> pgvector/pgvector:pg16.
  4. infra/docker-compose.prod.yml postgres:16-alpine -> pgvector/pgvector:pg16.
  5. charts/ai-proxy/values.yaml   datastores.postgres.image: pgvector/pgvector:pg16  (kind stack + helm prod).
  The migration's CREATE EXTENSION IF NOT EXISTS vector is the single provisioning choke point —
  a target whose postgres lacks the extension fails the migration LOUDLY at deploy (fail-closed, honest).

Extension points (designed HERE so siblings never re-freeze this contract):
  - vector-store-files (wave-2) INSERTs vector_store_files + vector_store_chunks rows, flips vsf.status,
    maintains usage_bytes, and MUST call `raise_if_zdr` as the FIRST line of the chunk-write repository
    method (the 6th gated choke point) + register chunk sweep in retention_sweep.py — table shape frozen
    here, no DDL needed there. file_counts in the wire object is COMPUTED (COUNT..GROUP BY vsf.status) so
    wave-2 statuses appear with zero contract change.
  - file-search-tool (wave-3) reads top-k via the `<=>` cosine operator over the HNSW index, scoped
    (vector_store_id, tenant_id) — both columns denormalized onto chunks for exactly this; per_query
    pricing_unit lands there, never here.
  - finetune-broker (parallel wave-1) also chains a migration off head c7e0a4b2d9f1 -> the orchestrator
    resolves the head fork (merge revision), a known ADD pattern, not a contract overlap.
```

Target (measurable): all 17 §4 tests green; `openai` SDK smoke `client.vector_stores.create/list/retrieve/del` round-trips against a live app; migrations suite green with the 3 new EXPECTED_TABLES rows; guardrails NOT-IN suite green; files_uploads_api + responses_store + migrations regression suites stay green; `SELECT indexdef` shows the hnsw index on a migrated DB; zero usage_records rows from CRUD. Not test-showable: dev/e2e/kind postgres image swap boots — confirmed by `make` stack bring-up (e2e compose healthcheck passes on pgvector/pgvector:pg16).
Status: FROZEN @ v1 — approved by Tin
Reported: no — rendered as the wave-1 freeze card; awaiting Tin's freeze

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/vector_stores/` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/migrations/versions/` · `apps/gateway/pyproject.toml` · `apps/gateway/uv.lock` · `apps/gateway/tests/vector_store_core/` · `apps/gateway/tests/migrations/test_migrations.py` · `apps/gateway/tests/guardrails/test_guardrails_core.py` · `apps/gateway/tests/conftest.py` · `infra/docker-compose.dev.yml` · `infra/docker-compose.e2e.yml` · `infra/docker-compose.prod.yml` · `charts/ai-proxy/values.yaml`
Regression floor: `apps/gateway/tests/files_uploads_api/` + `apps/gateway/tests/migrations/` + `apps/gateway/tests/guardrails/` stay green (each run standalone with a unique GATEWAY_TEST_DATABASE_URL; never the full suite in one shot).
Persona: `.add/personas/backend-architect.md` (concurrency primitive named per mutation; re-fetch-after-commit tests; files/ module layout is the shipped wire-store shape — domain/ Protocol port deliberately skipped to mirror `gateway.files`, a conscious precedent-following deviation).
Build order (SOFT): 1 wire_id + ORM + error specs → 2 migration (extension + 3 tables) + both manifests → 3 repository + router + main.py wiring → 4 image swaps + conftest CREATE EXTENSION → 5 green the suite + regression floor.

Least-sure flag surfaced at freeze: [contract] the fixed 1536 embedding dimension + `pgvector/pgvector:pg16` drop-in image swap — the dimension is the one schema cell a future embedding-model change would touch (mitigated: per-store embedding_model/embedding_dim columns recorded, wire never exposes the dim, escape = additive halfvec migration); the image swap makes EVERY test suite depend on the pgvector extension via Base.metadata.create_all (mitigated: idempotent CREATE EXTENSION in root conftest; operator must recreate the :5433 dev container once).

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

<!-- The freeze IS the one approval, led by the bundle's lowest-confidence flag — Contract + Scope (may touch) = HARD (tamper-guarded); Strategy · Regression floor · Persona = SOFT/optional. Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen Contract = change request back to SPECIFY. Scope tokens, backticked: `./…` = this task dir · a "/" token = project root · a bare name = sibling of the previous token's dir · a directory covers its whole subtree · outside-root drops fail-closed · absent line = UNDECLARED (grandfathered, never retro-red). -->

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_create_vector_store_returns_object: signup+key / POST create / assert full OpenAI wire object, vs_ id shape, zeroed file_counts · covers: M1
  - test_create_without_name_or_metadata: POST {} / assert 200, name null, metadata {} · covers: M1
  - test_create_rejects_name_too_long: name 257 chars / assert 422 + code + tenant list stays empty · covers: R:vector_store_name_too_long
  - test_create_rejects_invalid_metadata[5x]: long key / long value / 17 pairs / non-string value / non-object / assert 422 + code + nothing persisted · covers: R:vector_store_metadata_invalid
  - test_list_returns_tenant_stores_newest_first: create 3 / list limit=2 then offset=2 / assert order, has_more true→false · covers: M2
  - test_get_returns_store: create / get / assert round-trip · covers: M3
  - test_cross_tenant_get_and_delete_404: A creates, B gets+deletes / assert uniform 404 code, A's row unchanged, B's list empty · covers: M5, R:vector_store_not_found
  - test_unknown_and_malformed_id_uniform_404: absent-uuid, garbage, wrong-prefix ids / assert ONE 404 code (no oracle) · covers: R:vector_store_not_found
  - test_delete_removes_store_and_rows: create / delete / assert envelope + 404 on re-get + COUNT(vector_stores)=0 via fresh session (re-fetch after commit, persona metric) + second delete 404 · covers: M4
  - test_auth_required_401: no key + garbage key / assert 401 · covers: R:auth_key_invalid
  - test_pgvector_schema_and_hnsw_index: app fixture create_all / assert 3 tables present, pg_extension has vector, embedding is vector(1536), hnsw vector_cosine_ops index exists · covers: M6
  - test_crud_writes_no_usage_records: full CRUD round / assert usage_records COUNT=0 · covers: M7
  - test_zdr_tenant_can_create_store: flip zdr_enabled / create / assert 200 (container is metadata-only) · covers: M8
</test_plan>

Prose build-guidance (not gated): M9 byte-identical default path is enforced structurally (router include + side-effect ORM import only — reviewed at verify, no runtime probe) · alembic-parity of the 3 tables is gated by the EXISTING migrations suite once EXPECTED_TABLES gains the rows (scope-sanctioned edit) · list `has_more` = limit+1 probe, never COUNT(*).

RED EVIDENCE (run 2026-07-24, unique DB `gateway_test_vscore` on :5433):
```
$ cd apps/gateway && GATEWAY_TEST_DATABASE_URL="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test_vscore" \
    uv run pytest tests/vector_store_core/ -q
FAILED …::test_create_vector_store_returns_object      — AssertionError: create failed: 404 {"detail":"Not Found"}
FAILED …::test_create_without_name_or_metadata          FAILED …::test_create_rejects_name_too_long
FAILED …::test_create_rejects_invalid_metadata[×5]      FAILED …::test_list_returns_tenant_stores_newest_first
FAILED …::test_get_returns_store                        FAILED …::test_cross_tenant_get_and_delete_404
FAILED …::test_unknown_and_malformed_id_uniform_404     FAILED …::test_delete_removes_store_and_rows
FAILED …::test_auth_required_401                        — assert 404 == 401 (route absent → auth never reached)
FAILED …::test_pgvector_schema_and_hnsw_index           — assert set() == {'vector_store…'} (tables absent)
FAILED …::test_crud_writes_no_usage_records             FAILED …::test_zdr_tenant_can_create_store
17 failed in 19.80s
```
Every failure is a missing route (FastAPI default 404) or missing table — RED for missing implementation, harness healthy (signup/login/key fixtures all 2xx in captured logs). Coverage target: every §1 Must (M1–M8) + every Reject has ≥1 red test; M9 is prose-reviewed.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/vector_store_core/` · MUST run red (missing implementation) before Build. ✅ ran red 2026-07-24 (17/17, evidence above).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0. The test_plan bullets' `covers:` tails are machine-read too: `add.py locate path::test_name` resolves a failing test to the frozen §3 clause it proves -->
<!-- NON-CODING task (kind: docs · release · infra, or a non-coding project)? §4 is a failing-first ACCEPTANCE CHECK, not a script — verifiable pass/fail evidence (mkdocs build succeeds · §X covers A/B/C · every internal link resolves), red before the artifact exists and green after. Set `Tests live in: evidence` (no `./tests/`). The red→green discipline holds; only the must-be-executable-code requirement is lifted. -->

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `src/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Refute-read verdict is recorded, never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
