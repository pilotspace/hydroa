# TASK: Persist artifact bytes via the ObjectStore (S3) with honest-degrade

slug: artifacts-s3-persistence · created: 2026-06-26 · stage: production
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
  - `apps/gateway/src/gateway/artifacts/infrastructure/orm.py:ArtifactRow` — add `storage_backend: Mapped[str]` (default "inline") + `object_key: Mapped[str | None]`; widen `content: Mapped[bytes | None]` (nullable). Table `artifacts`, keyed by `id` (server uuid), tenant-scoped by `tenant_id`.
  - `apps/gateway/src/gateway/artifacts/infrastructure/repository.py:ArtifactRepository` — `create(*, tenant_id, key_id, name, content_type, size_bytes, content)` (L32) → add explicit `id` + `storage_backend` + `object_key`, make `content` optional; `get_active` (L92) loads the row incl. new cols; `soft_delete(...) -> bool` (L110) STAYS UNCHANGED (Tin's freeze hardening: delete leaves the object for the sweep, no reap).
  - `apps/gateway/src/gateway/artifacts/api/router.py` — `create_artifact` (L194): branch on `request.app.state.object_store`; `download_artifact` (L277, serves `row.content`): branch to `store.get(object_key)` for s3 rows; `delete_artifact` (L306): best-effort `store.delete(object_key)` after soft-delete. `/v1/artifacts` REST shape + tenant-scoped 404s stay byte-identical.
  - `apps/gateway/src/gateway/main.py:618` `app.state.settings = settings` — add `app.state.object_store = build_object_store(settings)` (None when unconfigured; tests override via app.state).
  - `gateway.objectstore` (task 1, gate PASS): `build_object_store(settings) -> ObjectStore | None` · `ObjectStore.put/get/delete` · `ObjectNotFoundError` · `ObjectStoreUnavailableError`.
  - `gateway.core.error_catalog`: `OBJECT_STORE_UNAVAILABLE` (503, task 1) for store failures; `ERR_ARTIFACT_NOT_FOUND` / `PAYLOAD_INVALID_BASE64` / `PAYLOAD_INPUT_TOO_LONG` unchanged.
  - NEW migration on head `d1e3f5a7c9b2` — add the two columns + drop NOT NULL on `content` (additive; existing rows default `storage_backend='inline'`).
Context (working folder):
  - `apps/gateway/tests/artifacts/test_artifacts.py` — the 12 v45 DB-backed tests; ALL must stay green (inline path unchanged). New tests add the s3 path (fake/real store on app.state), honest-degrade (store None), and the failure/atomicity paths.
  - `apps/gateway/tests/migrations/test_migrations.py:EXPECTED_TABLES` — table-NAME manifest; `artifacts` already present; adding COLUMNS does not change it → NO manifest edit.
  - `infra/docker-compose.dev.yml` minio (task 1) — the live store for s3-path DB tests / task 3.
Honors (patterns / conventions):
  - TENANT-ISOLATION (HARD, v45): every repo query filters `tenant_id`; cross-tenant id → 404; the object key is `artifacts/{tenant_id}/{artifact_id}` and download fetches via the tenant-scoped row's `object_key` (never a caller-supplied key) — no cross-tenant bytes.
  - HONEST DEGRADATION (v51 HARD): `object_store is None` → inline BYTEA (exact v45 behavior); a CONFIGURED store that fails → 503 (never silent fallback, never fabricated success).
  - ATOMICITY (v51 HARD): create writes the OBJECT first, then commits the row (failed commit = orphan object, swept later — the task-1 spec delta); a failed put → no row, 503. soft-delete sets `deleted_at` then best-effort removes the object (no undelete surface, so reclaiming bytes is safe).
  - ID-AT-CALL-SITE (project settled lesson): generate `uuid4()` in the router and pass it down, so the object key is known BEFORE the write.
  - additive migration; column declared in BOTH the ORM mapping AND the migration (v30 lesson); REST contract byte-identical (new columns are internal, never in responses).
Anchors the contract cites: `ArtifactRow` (+storage_backend/object_key/content-nullable) · `ArtifactRepository.create/get_active/soft_delete` · `artifacts/api/router.py` 3 endpoints · `main.py` app.state.object_store · `gateway.objectstore.build_object_store/ObjectNotFoundError/ObjectStoreUnavailableError` · `error_catalog.OBJECT_STORE_UNAVAILABLE` · NEW migration (down_revision d1e3f5a7c9b2)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Route artifact bytes through the ObjectStore (S3) with per-row backend dispatch + honest-degrade to inline
Framings weighed: per-row backend column + store-then-commit atomicity (chosen — back-compat, swappable, never a row pointing at absent bytes) · always-S3 no-inline-fallback (breaks honest-degrade + existing rows) · dual-write inline+S3 (storage waste, two sources of truth)
Must:
<must>
  - Additive migration on head d1e3f5a7c9b2: `artifacts.storage_backend TEXT NOT NULL DEFAULT 'inline'` + `artifacts.object_key TEXT NULL`; drop NOT NULL on `artifacts.content`. Existing rows read back as inline, unchanged.
  - `app.state.object_store = build_object_store(settings)` at create_app (None when unconfigured); the router reads it per-request.
  - UPLOAD (store configured): generate `id=uuid4()`, `object_key=f"artifacts/{tenant_id}/{id}"`, `store.put(object_key, decoded, content_type)` FIRST, then insert row(id, storage_backend='s3', object_key, content=NULL); commit AFTER. Store None → insert row(storage_backend='inline', content=decoded) (exact v45). Base64-decode + size-cap checks run BEFORE any store/insert (unchanged).
  - DOWNLOAD: s3 row → `store.get(object_key)`; inline row → `row.content`. Same Response: raw bytes + stored Content-Type + `Content-Disposition: attachment` (XSS guard unchanged).
  - DELETE: soft-delete (`deleted_at=now`) UNCHANGED from v45 — `soft_delete -> bool`, NO object reap. The s3 object is LEFT in place (download already 404s on a soft-deleted row) and reaped later by the orphan/deleted-row sweep; this preserves soft-delete recoverability at the DB level. (Tin's freeze hardening.)
  - Tenant isolation byte-identical: every repo query stays `tenant_id`-filtered; cross-tenant/unknown/deleted id → 404; `object_key` is derived server-side from the tenant-scoped row, NEVER caller-supplied.
  - The `/v1/artifacts` REST shape (request envelope, response models, list-metadata-only, status codes) is byte-identical — `storage_backend`/`object_key` are internal, never serialized.
</must>
Reject:
<reject>
  - a CONFIGURED store put/get fails (timeout / 5xx / breaker open) -> ERR_OBJECT_STORE_UNAVAILABLE (503); upload writes NO row (object-first; failed put → no insert)
  - download of an s3 row whose object is missing (ObjectNotFoundError) -> 404 ERR_ARTIFACT_NOT_FOUND (the artifact's bytes are gone)
  - download of an s3 row while the store is now UNCONFIGURED (None) -> 503 ERR_OBJECT_STORE_UNAVAILABLE (exists but unreachable — honest, never a 404 lie)
  - (unchanged from v45) invalid base64 -> 422; over-cap -> 413 (before store/insert); unknown/cross-tenant/deleted id -> 404
</reject>
After:
<after>
  - s3 upload: row.storage_backend='s3', row.content IS NULL, bytes live in MinIO at object_key; GET returns the exact bytes + content-type.
  - inline upload (store None): row.storage_backend='inline', row.content=bytes — the v45 row shape, all 12 v45 tests green.
  - delete: row.deleted_at set; the s3 object REMAINS in the store (reaped later by the sweep); the store is not called on delete at all — soft-delete stays fully DB-recoverable.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ store-then-commit (write the OBJECT before committing the ROW) is the correct atomicity order — a failed COMMIT leaves an orphan object (reaped by the future sweep, task-1 delta) but a row NEVER points at absent bytes; the inverse (row-first) could 201 then fail the put → a row with no fetchable bytes (a worse, user-visible corruption). if wrong: re-order + add a compensating object-delete on commit failure — CONTAINED to the create path, no schema change.
  - [ ] download of an s3 row when the store is unconfigured → 503 (NOT 404) — the artifact exists but is unreachable; 503 is honest, 404 would falsely claim it never existed.
  - [ ] deferring ALL s3 object cleanup to the sweep (delete leaves the object) is acceptable — soft-delete stays DB-recoverable and the delete path never touches the store; the trade is objects linger until the sweep runs (a storage cost, not a correctness bug). (Tin's freeze hardening.)
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: s3-backed upload then download round-trips exact bytes
  Given an object store is configured on app.state
  When POST /v1/artifacts then GET /v1/artifacts/{id}
  Then the downloaded bytes equal the uploaded bytes with the stored content-type
  And the row has storage_backend='s3', object_key set, content IS NULL
  And the bytes physically live in the store at artifacts/{tenant_id}/{id}

Scenario: honest-degrade to inline when no store configured
  Given app.state.object_store is None
  When POST /v1/artifacts then GET /v1/artifacts/{id}
  Then the round-trip returns the exact bytes (the v45 behavior)
  And the row has storage_backend='inline', content=bytes, object_key IS NULL

Scenario: existing inline rows still download after the migration
  Given an artifact created before this change (storage_backend defaulted 'inline')
  When GET /v1/artifacts/{id}
  Then its bytes are served from row.content
  And no object store call is made

Scenario: configured store failure on upload surfaces 503 with no row
  Given a configured store whose put raises ObjectStoreUnavailableError
  When POST /v1/artifacts
  Then the response is 503 ERR_OBJECT_STORE_UNAVAILABLE
  And NO artifact row was inserted (object-first: a failed put writes nothing)

Scenario: download of an s3 row whose object vanished returns 404
  Given an s3 row whose object the store no longer has (ObjectNotFoundError)
  When GET /v1/artifacts/{id}
  Then the response is 404 ERR_ARTIFACT_NOT_FOUND
  And no bytes are served

Scenario: download of an s3 row when the store is unconfigured returns 503
  Given an s3 row but app.state.object_store is None
  When GET /v1/artifacts/{id}
  Then the response is 503 ERR_OBJECT_STORE_UNAVAILABLE
  And it is NOT a 404 (the artifact exists but is unreachable)

Scenario: delete is soft-only and leaves the object for the sweep
  Given a stored s3 artifact
  When DELETE /v1/artifacts/{id}
  Then the response is 204 and the row's deleted_at is set
  And the store's delete was NEVER called (the object is left for the orphan/deleted-row sweep)
  And a subsequent GET /v1/artifacts/{id} is 404 (soft-deleted)

Scenario: tenant isolation — cross-tenant download is 404, no bytes
  Given tenant A's s3 artifact
  When tenant B GETs /v1/artifacts/{A's id}
  Then the response is 404
  And the store is never asked for A's object_key (the row lookup is tenant-scoped and returns None first)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
REST (UNCHANGED shape — behavior gains s3 dispatch; responses byte-identical):
  POST /v1/artifacts   body: {name, content_type, content_base64}
    201 -> {id, name, content_type, size_bytes, created_at}        # same model; no backend field
    422 ERR_PAYLOAD_INVALID_BASE64 · 413 ERR_PAYLOAD_INPUT_TOO_LONG (before store/insert)
    503 ERR_OBJECT_STORE_UNAVAILABLE   # NEW: configured store put failed (no row written)
  GET  /v1/artifacts/{id}
    200 raw bytes + Content-Type + Content-Disposition: attachment  # s3 → store.get, inline → row.content
    404 ERR_ARTIFACT_NOT_FOUND   # unknown/cross-tenant/deleted OR s3 object vanished (ObjectNotFound)
    503 ERR_OBJECT_STORE_UNAVAILABLE   # NEW: s3 row but store unconfigured/failed
  GET  /v1/artifacts            -> {data:[…metadata…], limit, offset}   # UNCHANGED (no content/backend)
  DELETE /v1/artifacts/{id}     -> 204 (soft-delete ONLY; s3 object left for the sweep, store not called) · 404 otherwise

SCHEMA  artifacts  (additive migration, down_revision d1e3f5a7c9b2):
  + storage_backend TEXT NOT NULL DEFAULT 'inline'   # {'inline','s3'}; existing rows → 'inline'
  + object_key      TEXT NULL                        # 'artifacts/{tenant_id}/{id}' when s3, else NULL
  ~ content         BYTEA NULL                       # was NOT NULL; NULL when storage_backend='s3'
  (ORM ArtifactRow mirrors all three; declared in mapping AND migration — v30 lesson)

REPOSITORY (gateway.artifacts.infrastructure.repository.ArtifactRepository):
  create(*, id: uuid, tenant_id, key_id, name, content_type, size_bytes,
         storage_backend: str, object_key: str | None, content: bytes | None) -> ArtifactRow
  get_active(*, tenant_id, artifact_id) -> ArtifactRow | None     # now also loads storage_backend/object_key
  soft_delete(*, tenant_id, artifact_id) -> bool        # UNCHANGED from v45 (no object reap; sweep handles cleanup)

WIRING  main.py create_app: app.state.object_store = build_object_store(settings)   # None unconfigured
ATOMICITY (router create): id=uuid4() → store.put(object_key,…) → repo.create(…content=None) → commit
         (store None → repo.create(storage_backend='inline', content=bytes) → commit)
TENANT-ISOLATION: object_key derived from the tenant-scoped get_active row; cross-tenant id → row None → 404 BEFORE any store call.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-26 (freeze hardening: delete is soft-only, object left for the sweep — no reap call).
Least-sure flag surfaced at freeze:
  - [contract] store-then-commit atomicity (object before row commit) — a failed COMMIT leaves an orphan object (swept later), but a row never points at absent bytes; the inverse risks a 201 with unfetchable bytes. if wrong: re-order + compensating object-delete, contained to the create path.
  - [spec] s3 row + store now unconfigured → 503 (not 404) — the artifact exists but is unreachable; 503 is honest where 404 would lie.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (artifacts router + repository deltas)
Plan (one test per scenario, asserting behavior not internals — DB-backed, fake store on app.state):
<test_plan>
  - test_s3_upload_download_roundtrip: store=fake; POST then GET / assert exact bytes + row.storage_backend='s3' + content NULL + object_key present
  - test_honest_degrade_inline_when_no_store: app.state.object_store=None; POST then GET / assert exact bytes + storage_backend='inline' + content set
  - test_existing_inline_row_downloads_after_migration: insert legacy-style inline row; GET / assert served from row.content, store never called
  - test_upload_store_failure_503_no_row: fake.put raises ObjectStoreUnavailableError; POST / assert 503 + zero rows inserted
  - test_download_s3_object_missing_404: fake.get raises ObjectNotFoundError; GET / assert 404 ERR_ARTIFACT_NOT_FOUND
  - test_download_s3_row_store_unconfigured_503: s3 row + app.state.object_store=None; GET / assert 503 (not 404)
  - test_delete_soft_only_leaves_object: DELETE / assert 204 + deleted_at set + fake.delete NEVER called + subsequent GET 404
  - test_cross_tenant_download_404_no_store_call: tenant B GETs A's s3 id / assert 404 + fake.get NEVER called
  - (regression) the existing 12 v45 tests stay green unchanged (inline path)
</test_plan>

Tests live in: `apps/gateway/tests/artifacts/` · MUST run red (missing impl) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/artifacts/infrastructure/orm.py` `apps/gateway/src/gateway/artifacts/infrastructure/repository.py` `apps/gateway/src/gateway/artifacts/api/router.py` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/versions/` `apps/gateway/src/gateway/video/api/router.py`
Note: `video/api/router.py` is the SECOND caller of `ArtifactRepository.create()` (the v48 video worker stores the generated video as an artifact). The repo signature change forced a same-PR update to keep it compiling; the call is kept on INLINE storage byte-identically (routing the worker through the store is a §7 spec delta, out of scope here).
Strategy (ordered batches): 1. migration (storage_backend + object_key, content nullable) 2. ORM ArtifactRow mirror cols 3. repository create(+id/backend/object_key)/get_active/soft_delete(return backend+key) 4. main.py app.state.object_store wiring 5. router create/download/delete branch on app.state.object_store + map ObjectStoreUnavailableError→503 / ObjectNotFound→404
Safety rule (feature-specific): create writes the OBJECT before committing the ROW (object-first atomicity); object_key is derived server-side from the tenant-scoped row only — NEVER caller-supplied; delete is soft-only (no store call — object left for the sweep).
Code lives in: `./src/`
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

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] s3 upload writes content=NULL + storage_backend='s3' + object_key, bytes physically in the store — confirmed by test_s3_upload_download_roundtrip asserting the DB row AND fake_store.objects[key].
- [ ] honest-degrade: store None → storage_backend='inline' + content=bytes, exact v45 — confirmed by test_honest_degrade + all 12 v45 tests green unchanged.
- [ ] configured-store put failure → 503 with ZERO rows written (object-first atomicity) — confirmed by test_upload_store_failure_503_no_row (list empty).
- [ ] s3 object vanished → 404; s3 row + store unconfigured → 503 (not 404) — confirmed by test_download_s3_object_missing_404 + test_download_s3_row_store_unconfigured_503.
- [ ] tenant isolation: cross-tenant GET → 404 with NO store call (row lookup returns None first) — confirmed by test_cross_tenant_download_404_no_store_call (fake_store.get_calls == []).
- [ ] migration applies + ORM matches (no drift) — confirmed by test_migrations autogenerate-empty-diff + upgrade/downgrade parity green.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — app.state.object_store wired in create_app + read via _get_object_store in create/download; OBJECT_STORE_UNAVAILABLE referenced; new ORM cols selected by get_active; second caller (video worker) updated. Confirmed: pyright 12-baseline (no missing-arg), full artifacts+video suites green.
- [x] DEAD-CODE (code) — no orphaned symbol; _get_object_store used in 2 endpoints; ObjectNotFoundError/ObjectStoreUnavailableError both caught; OBJECT_STORE_UNAVAILABLE raised in 3 paths.
- [ ] SEMANTIC (prose / non-code) — n/a (code task).

### GATE RECORD
Outcome: PASS
Evidence: artifacts(20)+video+migrations(incl. autogenerate-empty-diff parity)+objectstore = 69 passed / 4 skipped (live-MinIO, task 3); make test-fast 243 passed; ruff clean; pyright 12-baseline (zero new). Independent adversarial refute-read (general-purpose subagent) = NO-BLOCKER (tenant-isolation 0.97, atomicity 0.97, earned-green 0.98, no regression 0.98); its 2 NITs (or ""/or b"" masking) hardened — s3 NULL object_key now surfaces honest 503.
Reviewed by: independent refute-read subagent + Tin Dang (orchestrator) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 503 rate on /v1/artifacts (object-store outages) · 404-on-s3 rate (vanished objects) · ratio of s3 vs inline rows.

### Spec delta
- [SPEC · open] route the v48 video worker (`video/api/router.py`) through the ObjectStore too — today it stores generated video INLINE regardless of object_store config; out of scope for the request path (evidence: pyright caught the 2nd create() caller; kept inline byte-identical here).
- [SPEC · open] the orphan-object sweep must ALSO reap objects of soft-DELETED s3 rows (not just commit-failure orphans) — delete is soft-only and leaves the object (evidence: Tin's freeze hardening; extends the task-1 sweep delta).
- [SPEC · open] guard the migration `downgrade()` against s3 rows (NULL content) before re-applying content NOT NULL, or document the sweep-first runbook step (evidence: refute-read flagged downgrade is only safe on inline-only/empty DB).

### Competency deltas
- [ADD · folded] a repository SIGNATURE change ripples to EVERY caller — pyright (not a test) caught the 2nd caller (video worker); widen §5 scope to the rippled file + keep its call byte-identical, and re-pin the change with a follow-up spec delta rather than silently expanding behavior (evidence: video/api/router.py:237 reportCallIssue). [folded foundation-version 38]
- [TDD · folded] "green-by-design" invariant-preservation tests (inline path, soft-delete, cross-tenant) legitimately pass BEFORE and AFTER the build — they assert an invariant HELD, not new behavior; label them so they are not mistaken for missing red (evidence: 3 of 8 new tests green at red-run). [folded foundation-version 38]
- [SDD · folded] when HONEST-DEGRADATION is a HARD invariant, even an UNREACHABLE corrupt-row state (s3 row with NULL object_key) must surface an honest 5xx, never a masking `or ""`/`or b""` that yields a misleading 404 or empty 200 (evidence: refute-read NIT → hardened the s3 object_key guard). [folded foundation-version 38]
