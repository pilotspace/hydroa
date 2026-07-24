# PLAN: OpenAI-wire /v1/files + multipart upload on the ObjectStore port

slug: files-uploads-api · created: 2026-07-24 · stage: production
milestone: api-surface-parity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: An OpenAI-SDK-compatible `/v1/files` surface (multipart create · list · retrieve · delete · content) that persists tenant-scoped user uploads on the EXISTING ObjectStore port, with the purpose vocabulary `batch·vision·user_data`, and an ADDITIVE `input_file_id` on `/v1/batches` so a `purpose="batch"` file drives a batch job.
Framings weighed: **new `files/` module mirroring `artifacts/` (chosen — SDK-compat wire, own lifecycle/purpose vocabulary, shares only the ObjectStore port)** · merge into `artifacts/` (rejected — artifacts is a playground/console contract keyed by UUID with base64-JSON ingress; files is multipart + `file-*` ids + purposes — a merge re-freezes artifacts and couples two wires) · a thin adapter over the artifacts router (rejected — divergent ingress/id/response shape makes the adapter bigger than the module).

Must:
<must>
  - M1 · POST /v1/files (multipart/form-data: `file`, `purpose`) stores the bytes via the ObjectStore port (honest-degrade to inline BYTEA when no store is configured, exactly like artifacts) and returns 200 with the OpenAI File object `{id:"file-<hex>", object:"file", bytes, created_at:<unix int>, filename, purpose, status:"processed"}`.
  - M2 · GET /v1/files returns `{object:"list", data:[<File object>...]}` for the caller's tenant, newest first, METADATA ONLY (raw bytes never appear in the envelope).
  - M3 · GET /v1/files/{file_id} returns the File object for a file the caller owns.
  - M4 · GET /v1/files/{file_id}/content returns the exact stored bytes with `Content-Disposition: attachment` (ALWAYS attachment — XSS guard, artifacts precedent) and the stored content-type.
  - M5 · DELETE /v1/files/{file_id} soft-deletes (deleted_at=now, inline bytes cleared, best-effort ObjectStore purge — artifacts precedent) and returns 200 `{id, object:"file", deleted:true}`; the file then reads 404.
  - M6 · The purpose vocabulary is exactly `{batch, vision, user_data}` — each accepted and round-tripped on the File object.
  - M7 · POST /v1/batches ADDITIVELY accepts `input_file_id` (a `file-<hex>` string) as an alternative to `line_items`; given a caller-owned `purpose="batch"` file it validates the reference and creates a batch job that RECORDS `input_file_id` (echoed on the response). The JSONL→line-item EXPANSION and real provider dispatch stay in v58's scope — this task contracts the reference + validation + job creation only.
  - Every proxied-inference surface bills exactly once; /v1/files is a CRUD/storage surface with NO provider cost → it writes NO usage record (milestone billing rule).
  - The `files` payload store composes with governance: ZDR fail-closed on write, retention sweeper + payload-capture inventory register the new table, and the new table lands in BOTH manifests.
</must>

Reject:
<reject>
  - Missing / invalid Bearer key -> "ERR_AUTH_INVALID_KEY" (401)   [reused]
  - Expired key -> "ERR_AUTH_KEY_EXPIRED" (401)                    [reused]
  - purpose not in {batch,vision,user_data} (e.g. fine-tune, assistants) -> "ERR_FILE_PURPOSE_UNSUPPORTED" (422)
  - zero-byte / missing file part -> "ERR_FILE_EMPTY" (422)
  - decoded size > Settings.files_max_bytes (>0) -> "ERR_FILE_TOO_LARGE" (413)  [checked BEFORE any store put or row]
  - upload by a Zero-Data-Retention tenant -> "ERR_ZDR_PAYLOAD_BLOCKED" (403)   [reused; no bytes persisted]
  - ObjectStore configured but unreachable on write/read -> "ERR_OBJECT_STORE_UNAVAILABLE" (503)  [reused; honest, never a 404 lie]
  - GET/DELETE/content on an unknown OR cross-tenant file id -> "ERR_FILE_NOT_FOUND" (404)  [never 403, never a leak]
  - POST /v1/batches input_file_id that is absent OR cross-tenant OR not purpose="batch" OR malformed -> "ERR_BATCH_INPUT_FILE_INVALID" (422)  [ONE uniform code — no enumeration oracle]
  - POST /v1/batches with BOTH line_items and input_file_id, or NEITHER -> "ERR_BATCH_INPUT_AMBIGUOUS" (422)
</reject>

After:
<after>
  - A created file has a durable metadata row (tenant_id-scoped) + bytes at `files/<tenant_id>/<file_id>` (s3) or inline BYTEA; its `file-<hex>` id round-trips through retrieve/content/list until deleted.
  - A ZDR-blocked or oversize or wrong-purpose upload leaves ZERO rows and ZERO stored bytes.
  - A batch created from `input_file_id` has one batch_jobs row recording the file reference; existing `line_items` submissions remain byte-identical (additive).
  - The default request path (no /v1/files, no input_file_id) engages ZERO new plumbing — byte-identical.
</after>

Boundary: multipart/form-data upload (`file` part + `purpose` form field) — the ONE new external input shape the tests speak (artifacts/images-edits use base64-JSON / multipart respectively; this is multipart on a file part). `created_at` is emitted as a UNIX integer (OpenAI wire), stored as timezone-aware DateTime internally. The `file-<hex>` wire id is the 32-char hex of an internal UUID (reversible; parsed back on retrieve and on batches input_file_id).
<assumptions>
  ⚠ M7 boundary — "the file drives a /v1/batches job" is satisfied by CREATE + VALIDATE + RECORD the reference, NOT by executing the batch here (JSONL expansion + provider dispatch = v58, milestone-confirmed). If the reviewer expects the file's line items materialized/executed in THIS milestone, the batches seam reshapes and v58's boundary moves — cost: re-open the batches contract and possibly a batch_job_items back-fill path. This feeds the §3 least-sure [contract] flag.
  ⚠ `input_file_id` is an ADDITIVE column on the FROZEN batch-job-store (v1) table + an additive optional request field. If additive-extension is judged a re-freeze of that contract (vs the established M7-items additive precedent), the change becomes a change-request against batch-job-store — cost: a second freeze.
  ⚠ File `status` is always `"processed"` (synchronous store; no async validation/scan pipeline). If a purpose later needs async validation (e.g. batch JSONL schema check), a `status` state machine must be added — deferred; contracted as a fixed literal now.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
# ── New module: gateway/files/ (api/router.py · infrastructure/orm.py · infrastructure/repository.py)
#    Auth + tenant-scope + honest-degrade mirror gateway.artifacts.api.router (create_artifact /
#    download_artifact) and gateway.artifacts.infrastructure.repository.ArtifactRepository.

POST /v1/files            (multipart/form-data)  file: <bytes>, purpose: <str>
  200 -> { id:"file-<hex>", object:"file", bytes:<int>, created_at:<unix int>,
           filename:<str>, purpose:"batch"|"vision"|"user_data", status:"processed" }
  401 -> { code:"ERR_AUTH_INVALID_KEY" | "ERR_AUTH_KEY_EXPIRED" }
  403 -> { code:"ERR_ZDR_PAYLOAD_BLOCKED" }
  413 -> { code:"ERR_FILE_TOO_LARGE" }
  422 -> { code:"ERR_FILE_PURPOSE_UNSUPPORTED" | "ERR_FILE_EMPTY" }
  503 -> { code:"ERR_OBJECT_STORE_UNAVAILABLE" }

GET  /v1/files            [?purpose=<filter>]
  200 -> { object:"list", data:[ <File object>, ... ] }   # newest first, metadata only, tenant-scoped
  401 -> { code:"ERR_AUTH_*" }

GET  /v1/files/{file_id}
  200 -> <File object>
  401/404 -> { code:"ERR_AUTH_*" | "ERR_FILE_NOT_FOUND" }

GET  /v1/files/{file_id}/content
  200 -> raw bytes  (Content-Disposition: attachment; stored content-type)
  401/404/503 -> { code:"ERR_AUTH_*" | "ERR_FILE_NOT_FOUND" | "ERR_OBJECT_STORE_UNAVAILABLE" }

DELETE /v1/files/{file_id}
  200 -> { id:"file-<hex>", object:"file", deleted:true }
  401/404 -> { code:"ERR_AUTH_*" | "ERR_FILE_NOT_FOUND" }

# ── Additive extension to gateway.batches.api.router (POST /v1/batches) — batch-job-store v1 stays additive
POST /v1/batches   body: { line_items?:[...] , input_file_id?:"file-<hex>",
                           endpoint?:str, completion_window?:str }   # exactly ONE of {line_items, input_file_id}
  200 -> BatchJobResponse + additive field { input_file_id: "file-<hex>" | null }
  422 -> { code:"ERR_BATCH_INPUT_FILE_INVALID" | "ERR_BATCH_INPUT_AMBIGUOUS"
           | "batch_items_empty" | "batch_items_too_many" | "batch_item_invalid" }   # existing codes reused

Schema:
  NEW TABLE `files` (mirrors artifacts ORM; new Alembic migration; id in EXPECTED_TABLES):
    id UUID pk · tenant_id UUID (every query filters) · key_id UUID · filename TEXT ·
    purpose TEXT · bytes INTEGER · content_type TEXT · status TEXT default 'processed' ·
    storage_backend TEXT default 'inline' ('inline'|'s3') · object_key TEXT NULL ·
    content BYTEA NULL · created_at timestamptz · deleted_at timestamptz NULL
    index ix_files_tenant_created (tenant_id, created_at DESC)
    wire id = "file-" + id.hex ; object bytes at files/<tenant_id>/<file_id> when s3.
  ALTER `batch_jobs` ADD input_file_id UUID NULL   (additive; nullable; NOT a hard cross-module FK —
    validated in the router against the caller-owned files row; NULL for every existing line_items job).
  Access pattern: tenant-scoped SELECT/soft-DELETE (404-never-leak idiom). No usage_records written
    (no provider cost). ZDR gate via gateway.tenants.application.retention_policy.raise_if_zdr BEFORE
    any object-store put or row insert (artifacts precedent). Registered payload store #6:
    tenants.application.retention_policy.NEW_PAYLOAD_TABLES += "files" and swept by
    usage.application.retention_sweep (_sweep_new_payload_window_pass / _sweep_zdr_purge_pass,
    mirroring _purge_artifacts_batch for the s3-backed byte purge).
```

Target (measurable): the §4 suite is 20 tests — 19 red now for the missing implementation, 1 (existing-line_items regression guard) green now and must STAY green; ALL 20 green after build. Every §1 Reject has a code-asserting test; cross-tenant GET/DELETE/content = 404; the two `/v1/batches` bad-input_file_id cases return the SAME uniform code (no oracle). Regression floor: `tests/artifacts/` + `tests/batches/` + `tests/retention_zdr/` + `tests/migrations/` stay green. Coverage: new `gateway/files/` ≥ 90% lines; overall coverage does not decrease. Non-test-observable and confirmed by inspection: Envoy `/v1/` prefix already covers `/v1/files` (infra/envoy/envoy-prod.yaml — no edge change) [OBSERVED].
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/files/` `apps/gateway/src/gateway/batches/api/router.py` `apps/gateway/src/gateway/batches/infrastructure/orm.py` `apps/gateway/src/gateway/batches/infrastructure/repository.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/tenants/application/retention_policy.py` `apps/gateway/src/gateway/usage/application/retention_sweep.py` `apps/gateway/migrations/versions/` `apps/gateway/tests/files_uploads_api/` `apps/gateway/tests/migrations/test_migrations.py` `apps/gateway/tests/guardrails/test_guardrails_core.py`
  <scrivener correction 2026-07-24 (orchestrator, pre-gate): the manifest-#2 NOT-IN assertion lives in `tests/guardrails/test_guardrails_core.py`, NOT `tests/retention_zdr/test_retention_zdr.py` (which holds no table manifest — live-grep verified by the build agent; advisor ratified the edit as the frozen §1 "lands in BOTH manifests" Must). Token referent corrected to the real file; scope INTENT unchanged. Re-anchored via `add.py phase build`.>
Regression floor: `apps/gateway/tests/artifacts/` `apps/gateway/tests/batches/` `apps/gateway/tests/retention_zdr/` `apps/gateway/tests/migrations/` — run green before the gate (unique GATEWAY_TEST_DATABASE_URL on :5433).
Persona (optional): `.add/personas/backend-architect.md` (ports-and-adapters + tenant-scoped repository + additive-by-supersession discipline)

Least-sure flag surfaced at freeze: [contract] The `/v1/batches` `input_file_id` seam — (a) the deferral boundary (create+validate+record here; JSONL expansion + execution in v58) and (b) additively extending the FROZEN batch-job-store v1 (new nullable column + optional field). Trust it least because it is the ONE place this task reaches into another task's frozen contract and the ONE place the "file drives a batch" exit criterion could be read more strictly than the milestone's v58 split intends.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_upload_returns_openai_file_object: multipart create -> 200 File object shape (file-* id, object=file, bytes, created_at int, filename, purpose, status) · covers: M1
  - test_list_files_metadata_only: create then GET /v1/files -> {object:list,data:[...]}, no content bytes · covers: M2
  - test_retrieve_file_object: GET /v1/files/{id} -> File object · covers: M3
  - test_download_content_roundtrip: GET /v1/files/{id}/content -> exact bytes, attachment disposition · covers: M4
  - test_delete_file: DELETE -> {deleted:true}; subsequent GET -> 404 · covers: M5
  - test_purposes_vision_and_user_data_accepted: vision + user_data round-trip · covers: M6
  - test_reject_unsupported_purpose: purpose=fine-tune -> 422 ERR_FILE_PURPOSE_UNSUPPORTED · covers: R:ERR_FILE_PURPOSE_UNSUPPORTED
  - test_reject_empty_file: zero-byte upload -> 422 ERR_FILE_EMPTY · covers: R:ERR_FILE_EMPTY
  - test_reject_oversize: over files_max_bytes -> 413 ERR_FILE_TOO_LARGE (checked before write) · covers: R:ERR_FILE_TOO_LARGE
  - test_zdr_tenant_upload_blocked: ZDR tenant -> 403 ERR_ZDR_PAYLOAD_BLOCKED, no bytes · covers: R:ERR_ZDR_PAYLOAD_BLOCKED
  - test_unauthenticated_401: no Bearer -> 401 · covers: R:ERR_AUTH_INVALID_KEY
  - test_content_object_store_unavailable_is_503: s3-backed file, store unreachable on read -> 503 (never a 404 lie) · covers: R:ERR_OBJECT_STORE_UNAVAILABLE
  - test_cross_tenant_retrieve_is_404: tenant B GET tenant A file -> 404 · covers: R:ERR_FILE_NOT_FOUND
  - test_cross_tenant_content_is_404: tenant B content -> 404 · covers: R:ERR_FILE_NOT_FOUND
  - test_cross_tenant_delete_is_404: tenant B delete -> 404; A still reads 200 (delete never happened) · covers: R:ERR_FILE_NOT_FOUND
  - test_batches_accepts_input_file_id: batch file -> POST /v1/batches{input_file_id} -> 200 job, input_file_id echoed, pollable · covers: M7
  - test_batches_input_file_wrong_purpose_rejected: vision file as input_file_id -> 422 ERR_BATCH_INPUT_FILE_INVALID · covers: R:ERR_BATCH_INPUT_FILE_INVALID
  - test_batches_input_file_cross_tenant_rejected_uniformly: cross-tenant AND absent -> SAME code (no oracle) · covers: R:ERR_BATCH_INPUT_FILE_INVALID
  - test_batches_ambiguous_both_or_neither: both/neither line_items+input_file_id -> 422 ERR_BATCH_INPUT_AMBIGUOUS · covers: R:ERR_BATCH_INPUT_AMBIGUOUS
  - test_existing_line_items_path_unchanged: REGRESSION GUARD — frozen line_items path stays 200/byte-identical (green now, must stay green) · covers: M7 (additive-safety)
</test_plan>

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Build-guidance (prose only — not gated): s3-backed byte path + honest-degrade to inline (mirror `tests/artifacts/test_artifacts_s3.py`'s FakeObjectStore for the object-first-then-row + 503-on-put and download-503-when-store-vanished cases) — reuse the artifacts pattern verbatim; the `purpose` query-filter on GET /v1/files; `list` pagination cap (default 50 / max 200, artifacts idiom); delete-while-referenced (OpenAI allows delete; a batch referencing a now-deleted file surfaces an honest error at v58 expansion time — out of this task's execution scope); registering `files` in the retention sweep passes is verified by the existing `tests/retention_zdr/` + `tests/migrations/` regression floor, not a new red test here.

Tests live in: `./tests/files_uploads_api/` · MUST run red (missing implementation) before Build.

RED EVIDENCE (2026-07-24, GATEWAY_TEST_DATABASE_URL=...gateway_test_filesupl on :5433):
`uv run pytest tests/files_uploads_api/ -p no:cacheprovider --no-cov -q` -> **19 failed, 1 passed in 13.90s**.
The 19 fail for the RIGHT reason — `POST /v1/files` returns 404 (route absent; captured: `{"path":"/v1/files","status_code":404}`), so every create-dependent assertion fails at the upload step. The 1 pass is `test_existing_line_items_path_unchanged` — the deliberate additive-regression guard proving the frozen `/v1/batches` line_items path is already green and must stay green.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `apps/gateway/src/gateway/files/` (+ additive edits per §3 Scope)
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear. Batches edits are ADDITIVE only (new nullable column + optional field) — never edit the frozen line_items behavior (persona: change-by-supersession). New table lands in BOTH manifests (EXPECTED_TABLES + retention_zdr NOT-IN unaffected assertion) — the cross-manifest lesson.

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
Verdict: NOT-EARNED (initial) → HEALED, attempt 1 of the bounded self-heal loop, re-verified
By: independent add-advisor refute agent (fresh context, 2026-07-24) · adversarially checked: cross-tenant wire_id oracles, size-cap bypass, purpose validation, batches input_file_id at create-vs-use, retention/ZDR purge honesty, delete-while-referenced.
🔴 FOUND (contract integrity): BodySizeLimitMiddleware's longest-prefix /v1/ JSON cap (20 MiB, main.py route_caps) pre-empted the handler for the whole 20 MiB→files_max_bytes(512 MiB) range → framework 413 ERR_REQUEST_BODY_TOO_LARGE instead of the frozen contracted code; suite never probed above small fixtures. 🟡: cap checked after unbounded file.read().
HEAL (builder, commit 96b13d5 → integrated): dedicated "/v1/files" route cap = files_max_bytes + 1 MiB headroom (mirrors the /v1/audio/ precedent) so the router owns ERR_FILE_TOO_LARGE; bounded read(max_bytes+1) resolves the 🟡; +3 additive tests (above-outer-below-file-cap 200 · above-file-cap contracted 413 · exact-boundary 200) in test_files_uploads_size_cap.py. Integrated evidence: 23/23 (20 frozen + 3 new); artifacts 29 green in builder verification; edge-input/body-size 67 green in builder verification.

### GATE RECORD
Reported: yes — card written to this record (autonomy: auto; refute security lens clean — wire_id reversible no-oracle, ZDR fail-closed confirmed; heal re-verified on the falsifying range)
Outcome: PASS
Reviewed by: auto-resolved (orchestrator run; evidence: 23/23 integrated, two-manifest rule satisfied incl. the corrected guardrails referent, scope re-anchored after the scrivener fix) · date: 2026-07-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by auto-resolved (orchestrator run; evidence: 23/23 integrated, two-manifest rule satisfied incl. the corrected guardrails referent, scope re-anchored after the scrivener fix))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
