# TASK: Live MinIO round-trip verification

slug: artifacts-s3-live-verify · created: 2026-06-26 · stage: production
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
  - NEW `apps/gateway/tests/artifacts/test_artifacts_s3_live.py` — a skip-gated (on `GATEWAY_OBJECT_STORE_ENDPOINT`) live test that builds a REAL `S3ObjectStore` (task 1) against the docker MinIO and drives the full `/v1/artifacts` HTTP path (signup→key→POST→GET→DELETE) end-to-end, proving the wiring works against a real S3-compatible store — not just the FakeObjectStore unit doubles.
  - `gateway.main.create_app(settings)` — with object-store Settings, `app.state.object_store` is a real `S3ObjectStore` (task 2 wiring); the test constructs its own app + httpx ASGITransport (the shared conftest `app` fixture uses store-less settings).
  - `gateway.core.config.Settings.object_store_*` (task 1) — enabled/endpoint/bucket/region/access_key_id/secret_access_key knobs the test sets to the MinIO container (minioadmin / bucket `artifacts`).
  - `infra/docker-compose.dev.yml` minio + minio-createbucket (task 1) — the live store + the `local/artifacts` bucket bootstrap.
Context (working folder):
  - `apps/gateway/tests/objectstore/test_s3_object_store_live.py` (task 1) — the 4 skip-gated PORT-level live tests; this task adds the HTTP-PATH-level live test on top.
  - `apps/gateway/tests/conftest.py` — `TEST_DATABASE_URL` / `TEST_JWT_SECRET`; schema via `Base.metadata.create_all` (carries the new columns); function-scoped app pattern to mirror.
Honors (patterns / conventions):
  - LIVE-VERIFY IS SKIP-GATED, NEVER WEAKENED (v25 lesson): the live test skips without the MinIO env var so `make test-fast`/CI stay green, but when run it asserts REAL observable truth (exact bytes round-trip + the object physically in MinIO + soft-delete leaves it).
  - SOFT-DELETE HARDENING (task 2): DELETE is soft-only — the live test asserts the object REMAINS in MinIO after a 204 (reaped later by the sweep), matching the frozen contract.
  - DESIGN-FOR-FAILURE already lives in `S3ObjectStore` (timeouts/retries/breaker, task 1) — this task verifies the happy path through real infra, not re-implements policy.
Anchors the contract cites: NEW `test_artifacts_s3_live.py` · `create_app` + `app.state.object_store` (real S3ObjectStore) · `Settings.object_store_*` · MinIO container (`http://localhost:9000`, bucket `artifacts`) · the `/v1/artifacts` POST/GET/DELETE round-trip

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Live MinIO round-trip verification of the /v1/artifacts s3 path
Framings weighed: skip-gated HTTP-path live test against real MinIO (chosen — proves the actual wiring end-to-end) · port-only live test (already covered by task 1) · manual curl script (not repeatable/CI-visible)
Must:
<must>
  - A skip-gated test (skips unless `GATEWAY_OBJECT_STORE_ENDPOINT` is set) builds a REAL S3ObjectStore-backed app and drives POST→GET→DELETE on /v1/artifacts.
  - UPLOAD then DOWNLOAD returns the EXACT uploaded bytes + content-type; the row is storage_backend='s3' with content NULL; the object physically exists in MinIO at `artifacts/{tenant}/{id}`.
  - DELETE returns 204, a subsequent GET is 404, AND the MinIO object STILL EXISTS (soft-only; reaped later by the sweep — the task-2 hardening).
  - The test stays SKIP (not fail) when MinIO is absent, so `make test-fast`/CI remain green.
</must>
Reject:
<reject>
  - MinIO env unset -> the test SKIPS (pytest.skip), never errors or silently passes a no-op
  - (verification task — no new product rejection paths; the /v1/artifacts error contract is owned + tested by task 2)
</reject>
After:
<after>
  - First-hand evidence: the v51 artifacts s3 path works against a REAL S3-compatible store, not just FakeObjectStore doubles — exact bytes in, exact bytes out, object on disk in MinIO, soft-delete leaves it.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the running MinIO already has the `artifacts` bucket (minio-createbucket bootstrap from task 1) — lowest confidence because a fresh `docker compose down -v` wipes it; if wrong: the test's put 500s → re-run `docker compose -f infra/docker-compose.dev.yml up -d` to re-bootstrap the bucket (the createbucket job loops until ready). Cost: a re-run, no code change.
  - [ ] the test can construct its own create_app with object-store Settings + share the test Postgres schema — confirmed: conftest's create_all carries the new columns; the test mirrors the function-scoped app pattern.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: live upload→download round-trips exact bytes through MinIO
  Given a real S3ObjectStore app pointed at the docker MinIO
  When a tenant POSTs an artifact then GETs it
  Then the downloaded bytes equal the uploaded bytes with the stored content-type
  And the row is storage_backend='s3' with content NULL
  And the object physically exists in MinIO at artifacts/{tenant}/{id}

Scenario: live delete is soft-only and leaves the MinIO object
  Given a stored live s3 artifact
  When the tenant DELETEs it
  Then the response is 204 and a subsequent GET is 404
  And the MinIO object STILL EXISTS (reaped later by the sweep, not on delete)

Scenario: the live test skips cleanly without MinIO
  Given GATEWAY_OBJECT_STORE_ENDPOINT is unset
  When the suite runs (e.g. make test-fast / CI)
  Then the live test is SKIPPED, not failed
  And the rest of the suite stays green
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
VERIFICATION HARNESS (no product surface — exercises the task-2 contract against real infra):
  Gate: skip unless env GATEWAY_OBJECT_STORE_ENDPOINT is set.
  App:  create_app(Settings(object_store_enabled=True, endpoint=$GATEWAY_OBJECT_STORE_ENDPOINT,
        bucket='artifacts', region='us-east-1', access_key_id='minioadmin', secret='minioadmin', ...))
        → app.state.object_store is a real S3ObjectStore.
  Flow: signup tenant → create key → POST /v1/artifacts {name, content_type, content_base64}
        → 201; GET /v1/artifacts/{id} → 200 raw bytes (== uploaded) + content-type;
        DELETE → 204; GET → 404.
  Asserts (observable):
    - GET bytes == uploaded bytes; content-type preserved; Content-Disposition attachment.
    - DB row: storage_backend='s3', content IS NULL, object_key=='artifacts/{tenant}/{id}'.
    - MinIO HEAD/GET object EXISTS at object_key after upload AND still after DELETE (soft-only).
  No schema/endpoint change — task 2 owns those; this task adds ONLY the test file.
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-26 (lean verification task; no product surface).
Least-sure flag surfaced at freeze:
  - [test] depends on the MinIO `artifacts` bucket existing (createbucket bootstrap) — a `down -v` wipes it; if the put 500s, re-`up` the dev stack to re-bootstrap. No code risk; skip-gated so CI is unaffected.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a (live verification harness — proves real-infra behavior, not unit coverage)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_live_upload_download_roundtrip: build real-store app / POST+GET / assert exact bytes + content-type + DB row(storage_backend='s3', content NULL, object_key) + MinIO object exists
  - test_live_delete_soft_only_leaves_object: DELETE / assert 204 + GET 404 + MinIO object STILL exists
  - test_live_skips_without_minio: (the skip marker itself) — collected + skipped when env unset, proven by the suite staying green in make test-fast
</test_plan>

Tests live in: `apps/gateway/tests/artifacts/` · skip-gated — runs GREEN against real MinIO, SKIPS without it (a live-verify harness is not red-first; the impl it verifies is task 2, already gate-PASS).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/tests/artifacts/test_artifacts_s3_live.py`
Strategy (ordered batches): 1. write the skip-gated live test (build real-store app, signup/key, POST→GET→DELETE, assert bytes + DB row + MinIO object) 2. run it live against the running MinIO 3. confirm it SKIPS under make test-fast
Safety rule (feature-specific): test-only — touches NO product code; the live test must SKIP (never fail/error) when MinIO is absent so CI stays green.
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
- [x] exact bytes round-trip through REAL MinIO — confirmed: live run 2/2, GET content == uploaded bytes, content-type preserved, attachment header.
- [x] DB row routed to s3 (storage_backend='s3', content NULL, object_key='artifacts/{tenant}/{id}') — confirmed by the live test's direct DB assertion.
- [x] object physically in MinIO after upload AND survives DELETE (soft-only) — confirmed: store.get(expected_key)==data both after POST and after the 204 DELETE.
- [x] CI-safe — confirmed: 2 SKIPPED without the env var; make test-fast unaffected.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the test imports real S3ObjectStore via create_app + asserts app.state.object_store is not None; all helpers used.
- [x] DEAD-CODE (code) — no orphaned symbol; both tests + 4 fixtures/helpers referenced.
- [ ] SEMANTIC (prose / non-code) — n/a (test-only task).

### GATE RECORD
Outcome: PASS
Evidence: live run (GATEWAY_OBJECT_STORE_ENDPOINT=http://localhost:9000) = 2 passed FIRST-HAND against real docker MinIO (exact bytes in/out, DB row s3+content-NULL+object_key, soft-delete leaves the object); 2 SKIPPED without the env (CI-safe); ruff clean; pyright 0 errors on the file. Test-only task — no product code touched.
Reviewed by: Tin Dang (orchestrator) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): live-verify pass on each release candidate (run with GATEWAY_OBJECT_STORE_ENDPOINT against the dev MinIO).

### Spec delta
- [SPEC · open] wire the live-verify into the `make edge`/e2e stack (a dedicated compose profile + a `make verify-objectstore` target) so the s3 round-trip is a one-command release gate, not a manual env-var run (evidence: today it needs a hand-set GATEWAY_OBJECT_STORE_ENDPOINT).

### Competency deltas
- [ADD · open] a skip-gated live-verify task is NOT red-first — the impl it proves is an already-gated upstream task; the floor is honored by SKIP-not-fail + first-hand real-infra assertion, recorded explicitly in §4 so it is not mistaken for a missing red (evidence: this task ran green immediately against MinIO).
- [TDD · open] pydantic `SecretStr` fields reject a plain `str` under pyright even though they coerce at runtime — wrap test-constructed secrets in `SecretStr(...)` to keep the zero-new-error bar (evidence: live test 48:40 reportArgumentType → fixed).
