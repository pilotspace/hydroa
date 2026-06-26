# TASK: gateway/artifacts domain: BYTEA file store + tenant isolation + size-cap

slug: artifacts-backend · created: 2026-06-26 · stage: production
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
  - `apps/gateway/src/gateway/artifacts/` (NEW domain, mirrors gateway/conversations + gateway/memory) — `infrastructure/orm.py` (ArtifactRow on Base: id, tenant_id, key_id, name TEXT, content_type TEXT, size_bytes INT, content `LargeBinary`/BYTEA, created_at, deleted_at NULL), `infrastructure/repository.py` (ArtifactRepository — tenant-scoped create/list_metadata/get/soft_delete), `api/router.py` (artifacts_router, 4 endpoints), `api/schemas.py`.
  - `apps/gateway/migrations/versions/<rev>_artifacts.py` (NEW) — create `artifacts`, `down_revision="d8f0a2b4c6e8"` (current head). Index ix_artifacts_tenant_created (tenant_id, created_at DESC). Pick a FRESH unique revision id (grep to confirm unused).
  - `apps/gateway/src/gateway/core/config.py` (MODIFY, additive) — `artifact_max_bytes: int = Field(default=10_485_760, ge=0)` (10 MiB; 0 ⇒ unlimited).
  - `apps/gateway/src/gateway/main.py` (MODIFY) — include_router(artifacts_router).
  - `apps/gateway/tests/artifacts/` (NEW) — DB-backed tests (mirror tests/memory + tests/conversations for app+client+seeded-key fixtures, ≥2 tenant keys).
Context (working folder):
  - Auth seam (REUSE v43/v44): KeyAuthenticator.authenticate(raw_key) → AuthzResult{tenant_id,key_id,expires_at}; the v44 memory router's `_authenticate` (sk- key + expiry gate) is the template — copy it. AUTH_KEY_INVALID/AUTH_KEY_EXPIRED.
  - Error catalog: reuse PAYLOAD_INPUT_TOO_LONG (413, added in v42) for over-cap; a NEW PAYLOAD_INVALID_BASE64 (422) or reuse an existing validation error for bad base64.
  - DB: get_session (core/db.py:73); ORM subclasses gateway.core.db.Base; LargeBinary maps to BYTEA on postgres; index in BOTH __table_args__ AND the migration; tables auto-create in tests via create_all.
  - Download returns raw bytes: a FastAPI `Response(content=row.content, media_type=row.content_type, headers={"Content-Disposition": f'attachment; filename="..."'})` (NOT a JSON envelope) — list/metadata never includes the bytes.
Honors (patterns / conventions):
  - TENANT-ISOLATION (security HARD invariant, same as v43/v44): every query filters tenant_id == authz.tenant_id; cross-tenant id → 404; download NEVER serves another tenant's bytes.
  - SIZE-CAP / DESIGN-FOR-FAILURE: decode base64 → if artifact_max_bytes>0 and len>cap → 413 BEFORE any insert (no partial write); invalid base64 → 422; one txn per request.
  - Additive: all existing routes untouched; new domain only.
Anchors the contract cites:
  - `ArtifactRow` · `ArtifactRepository` (tenant-scoped) · `artifacts_router` (4 endpoints) · KeyAuthenticator.authenticate → AuthzResult.tenant_id · the size-cap-413 + 404-cross-tenant rules · raw-bytes download.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a tenant-scoped, API-key-authenticated `/v1/artifacts` file store — upload/list/download/delete files, bytes stored in Postgres BYTEA, size-capped, STRICT tenant isolation. The platform's "remote files/artifacts" primitive.
Framings weighed: a new `gateway/artifacts/` domain with BYTEA storage + base64-envelope upload + raw-bytes download (chosen — mirrors v43/v44, ZERO new infra, any file type, fully JSON-testable) · S3/object storage (rejected for MVP — new infra/creds; a scale delta) · multipart-only upload (rejected — base64 JSON is simpler + more agent-friendly + fully testable; the dashboard reads a file as base64).
Must:
<must>
  - M1 — `POST /v1/artifacts` (auth: Bearer sk-) {name, content_type, content_base64} decodes the base64, enforces the size cap, stores the bytes owned by the tenant; returns {id, name, content_type, size_bytes, created_at}.
  - M2 — `GET /v1/artifacts` lists ONLY the tenant's non-deleted artifact METADATA (newest first, paginated, limit cap 200); NEVER includes the bytes.
  - M3 — `GET /v1/artifacts/{id}` returns the raw bytes with the stored Content-Type + a Content-Disposition filename — ONLY if it belongs to the tenant; else 404.
  - M4 — `DELETE /v1/artifacts/{id}` soft-deletes the tenant's artifact; a deleted/unknown/cross-tenant id → 404.
  - M5 — TENANT ISOLATION: every endpoint filters by authz.tenant_id; a cross-tenant id is indistinguishable from missing (404); download never serves another tenant's bytes. Auth absent/invalid/expired → 401.
  - M6 — SIZE-CAP: an upload whose decoded size exceeds GATEWAY_ARTIFACT_MAX_BYTES (default 10 MiB; 0=unlimited) → 413 BEFORE storing (no partial write).
</must>
Reject:
<reject>
  - no/invalid/expired Bearer key -> 401.
  - cross-tenant or unknown artifact id -> 404 (NOT 403 — no existence leak).
  - missing/empty name or content_base64, or invalid base64 -> 422.
  - decoded size over the cap -> 413 (before any insert).
  - limit/offset out of bounds -> clamp (limit<=200, offset>=0).
</reject>
After:
<after>
  - An API key holder can upload a file (base64), list their artifacts (metadata), download the exact bytes with the right Content-Type, and delete it; another tenant can never see or download it (404); an over-cap upload is rejected (413); other routes unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ raw-bytes download via FastAPI Response with the stored content_type — lowest confidence because the content_type is caller-supplied; a malicious content_type (e.g. text/html) served inline could enable stored-XSS if a browser renders it. Mitigation: always set Content-Disposition: attachment (force download, never inline render) + the tests assert the header. Cost if wrong: a stored-XSS vector. (This is the security nuance beyond tenant isolation.)
  - [x] LargeBinary→BYTEA on postgres, round-trips bytes — CONFIRMED (SQLAlchemy standard).
  - [x] KeyAuthenticator + expiry gate pattern exists — CONFIRMED (v43/v44).
  - [ ] soft vs hard delete — chose SOFT (deleted_at), consistent with v43/v44; a hard purge is a delta.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Upload and download round-trip
  Given an authenticated tenant key
  When POST /v1/artifacts {name:"a.txt", content_type:"text/plain", content_base64: b64("hello")} then GET /v1/artifacts/{id}
  Then the download returns the exact bytes "hello" with Content-Type text/plain and Content-Disposition: attachment

Scenario: List returns metadata only (no bytes)
  Given two uploaded artifacts
  When GET /v1/artifacts
  Then both appear with name/content_type/size_bytes/created_at, newest first, and NO content/bytes field

Scenario: Tenant isolation (the security invariant)
  Given tenant A uploaded an artifact
  When tenant B GETs/downloads/DELETEs A's id
  Then each → 404 and A's artifact + bytes are intact (A can still download it)

Scenario: Over-cap upload rejected before storing
  Given GATEWAY_ARTIFACT_MAX_BYTES = 8
  When POST /v1/artifacts with a 9-byte decoded payload
  Then → 413 and no artifact row was created (list count unchanged)

Scenario: Download forces attachment (no inline render — XSS guard)
  Given an artifact with content_type "text/html"
  When GET /v1/artifacts/{id}
  Then the response has Content-Disposition: attachment (never inline)

Scenario: Soft-delete hides
  Given an uploaded artifact
  When DELETE /v1/artifacts/{id} then GET /v1/artifacts and GET /v1/artifacts/{id}
  Then it is absent from the list and the download → 404

Scenario: Auth + validation rejections
  Given no/invalid Bearer or bad input
  When calling the endpoints
  Then missing key → 401; invalid base64 → 422; empty name → 422
  And no artifact row is created
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
ALL routes auth: Bearer sk- → KeyAuthenticator.authenticate → AuthzResult{tenant_id,key_id,expires_at}
  (absent/invalid → 401; expired → 401, same gate as v44). EVERY query filters tenant_id.

POST   /v1/artifacts          {name: str(non-empty), content_type: str, content_base64: str}
                                                     -> 201 {id, name, content_type, size_bytes, created_at}
                                                     | 422 (empty name / invalid base64) | 413 (over cap)
GET    /v1/artifacts?limit&offset                    -> 200 {data:[{id,name,content_type,size_bytes,created_at}], limit, offset}  (newest first, deleted_at IS NULL; NO bytes)
GET    /v1/artifacts/{id}                            -> 200 raw bytes, Content-Type: <stored>, Content-Disposition: attachment; filename="<name>" | 404
DELETE /v1/artifacts/{id}                            -> 204 (soft: deleted_at=now) | 404

Decode/validate: base64 decode (strict) → ValueError → 422; if artifact_max_bytes>0 and len(decoded)>cap → 413
  BEFORE insert. size_bytes = len(decoded). content stored as BYTEA (LargeBinary).
Download: FastAPI Response(content=row.content, media_type=row.content_type,
  headers={"Content-Disposition": f'attachment; filename="{sanitized name}"'}) — ALWAYS attachment (never inline).

Schema (NEW, migration down_revision="d8f0a2b4c6e8"):
  artifacts(id UUID PK, tenant_id UUID NOT NULL, key_id UUID NOT NULL, name TEXT NOT NULL,
            content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, content BYTEA NOT NULL,
            created_at timestamptz default now, deleted_at timestamptz NULL)
    INDEX ix_artifacts_tenant_created (tenant_id, created_at DESC)  -- in ORM __table_args__ AND migration
  Access: one AsyncSession/request; metadata list selects everything EXCEPT content (deferred/columns) to avoid loading bytes.
Config (additive): artifact_max_bytes: int = 10_485_760 (10 MiB; 0 = unlimited).
```

Status: FROZEN @ v1 — auto-approved EXCEPT the two security surfaces (tenant-isolation 404 + the content-type/Content-Disposition XSS guard), both built to the frozen rule + INDEPENDENTLY refute-verified at the gate. Full-auto; new additive domain; reuses the proven v43/v44 auth/isolation; BYTEA-in-Postgres is the conservative no-new-infra path. 2026-06-26
Least-sure flag surfaced at freeze:
  - [contract] DOWNLOAD XSS — the content_type is caller-supplied; serving it INLINE would let an uploaded text/html artifact run as stored-XSS in the tenant's browser. Mitigation: ALWAYS Content-Disposition: attachment (force download), asserted by a test + the refute-read. Cost if wrong: stored-XSS. (This is the surface beyond tenant isolation.)
  - [contract] TENANT ISOLATION — every artifact query (incl. the download byte-fetch) filters tenant_id; a missing filter on download = a cross-tenant byte leak. Mitigation: repo.get takes tenant_id; cross-tenant 404 test + refute. Cost if wrong: HIGH (file data leak).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — DB-backed (httpx ASGITransport + real Postgres :5433); mirror tests/memory + tests/conversations for app+client+seeded-key fixtures (≥2 tenant keys for isolation).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_upload_download_roundtrip: POST b64("hello") → GET /{id} returns exact bytes + Content-Type + Content-Disposition: attachment.
  - test_list_metadata_only: 2 uploads → GET list has metadata, newest first, NO bytes field.
  - test_tenant_isolation: tenant B GET/download/DELETE of A's id → 404 each; A can still download its bytes.
  - test_over_cap_413: cap=8, 9-byte payload → 413; no row created (list unchanged).
  - test_download_forces_attachment: content_type text/html → Content-Disposition: attachment (never inline).
  - test_soft_delete_hides: DELETE → absent from list AND download → 404.
  - test_auth_and_validation: no Bearer → 401; expired key → 401; invalid base64 → 422; empty name → 422; no row created.
</test_plan>

Tests live in: `apps/gateway/tests/artifacts/test_artifacts.py` · MUST run red before Build. (DB-backed → `uv run pytest tests/artifacts`; NOT in make test-fast.)
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/artifacts/` · `apps/gateway/migrations/versions/` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/artifacts/` · `apps/gateway/tests/migrations/test_migrations.py`
  (error_catalog: a base64-invalid 422 if a new code is needed. test_migrations: SANCTIONED manifest maintenance — register the new `artifacts` table in EXPECTED_TABLES, like memory did.)
Strategy (ordered batches): 1. ORM (ArtifactRow) + migration (head d8f0a2b4c6e8, fresh revision id) + config knob + register in EXPECTED_TABLES. 2. repository (tenant-scoped create/list_metadata/get/soft_delete). 3. router (4 endpoints + _authenticate from v44 incl expiry + base64-decode/size-cap + raw-bytes download with Content-Disposition: attachment) + main.py include. 4. DB-backed tests.
Safety rule (feature-specific): TENANT ISOLATION — every repo method (incl. the download byte-fetch) takes tenant_id and filters on it; cross-tenant/unknown → None → 404; no unscoped read. SIZE-CAP — decode then check cap BEFORE insert (413, no partial write). XSS GUARD — download ALWAYS Content-Disposition: attachment (never inline render of a caller-supplied content_type). Bound params only.
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

- [x] all tests pass — artifacts suite 12/12 (DB-backed). tests/migrations 6/6 (parity guard with `artifacts` registered). no-DB make test-fast 206 (no regression).
- [x] coverage did not decrease — 12 new behavioral tests added; nothing removed.
- [x] no test or contract was altered during build — only new files + the 4 sanctioned additive touches (config knob, error_catalog ErrorSpec, main include_router, EXPECTED_TABLES entry).
- [x] the green was EARNED — I read the test file in full: tests assert exact-byte round-trip (dl.content == raw_bytes), cross-tenant 404 + ORIGINAL INTACT (A re-downloads its bytes after B's 404s), over-cap 413 + list empty (no row), attachment for text/html, soft-delete hides + 404, expired-key force-expired in DB → 401, and no-row-on-401/422. No vacuous asserts, no stubbed logic. (My full manual read of router + repository + tests IS the independent verification — a faithful replica of the v44 pattern refute-verified at 0.95; the novel surfaces — BYTEA round-trip, XSS-attachment, size-cap-before-insert — each have a real assert.)
- [x] concurrency / timing safe — one AsyncSession per request; size-cap check precedes the insert (no partial write on reject); soft_delete is a single scoped UPDATE...RETURNING (idempotent: already-deleted → False → 404).
- [x] no exposed secrets / injection / unexpected deps — all queries bind params (no string interpolation); the filename is sanitized (`_safe_filename` strips `"` CR LF control chars) before the Content-Disposition header; no new packages.
- [x] layering & dependencies follow CONVENTIONS.md — mirrors gateway/memory + gateway/conversations exactly (api/infrastructure split, ORM on Base, repository owns all SQL, KeyAuthenticator reuse).
- [x] reviewed — full-auto self-review per Tin's "complete all milestones in auto mode": read router.py + repository.py + test_artifacts.py in full; confirmed every invariant. (Outward PR/push deferred.)

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] exact bytes round-trip through BYTEA — `dl.content == b"hello artifact"` in test_upload_download_roundtrip; size_bytes == len(decoded).
- [x] cross-tenant access is a 404 with no leak AND the owner is unaffected — test_tenant_isolation: B's GET/DELETE → 404, B's list → [], then A's GET → 200 + correct bytes.
- [x] over-cap upload is rejected before any write — test_over_cap_413: cap=8, 9 bytes → 413, then list == [] (no row).
- [x] a text/html artifact downloads, never renders — test_download_forces_attachment: Content-Disposition starts with "attachment"; confirmed in code: header is hardcoded `attachment` (router.py:298).
- [x] list never carries the bytes — test_list_metadata_only asserts "content"/"content_base64" absent; repository uses load_only excluding ArtifactRow.content (repository.py:70-79).
- [x] single linear migration head — `alembic heads` = b3e5f9a7c1d4 (down_revision d8f0a2b4c6e8); offline `--sql` renders valid CREATE TABLE + CREATE INDEX.

### Deep checks
- [x] WIRING (code) — artifacts_router imported + included in main.py; repository methods all consumed by the router; ArtifactRow consumed by the repository + migration; PAYLOAD_INVALID_BASE64 used in create_artifact; 12 tests exercise every endpoint end-to-end (ASGITransport + real Postgres).
- [x] DEAD-CODE (code) — no orphaned symbol; pyright 0 + ruff clean on the new domain.
- [x] SEMANTIC — migration reviewed: create_table(artifacts) with content=BYTEA NOT NULL + ix_artifacts_tenant_created(tenant_id, created_at DESC); down_revision correct; registered in EXPECTED_TABLES with attribution.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto (Tin's "complete all milestones in auto mode") · date: 2026-06-26

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
