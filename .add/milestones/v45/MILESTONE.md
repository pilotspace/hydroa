# MILESTONE: Artifacts / files (tenant-scoped file store)

goal: A user (and any API key holder) can upload, list, download, and delete files/artifacts via a tenant-scoped gateway store, surfaced in the dashboard — the third 'remote' platform capability.
rationale: new-major → milestone 6 of 9 (program v40–v48, "AI Application Platform"). Tin 2026-06-26 "implement all, best decision". The third "remote" capability after sessions (v43) + memory (v44): a tenant-scoped FILE/ARTIFACT store any key holder (agents) can upload to and download from — so agent-generated artifacts (code, docs, data, images) persist and are retrievable. ARCH DECISION (self-made, conservative): the deployed stack is PG + Redis with NO object storage; the MVP stores file bytes in a tenant-scoped Postgres BYTEA column (size-capped), uploaded via a base64 JSON envelope (handles any file type, fully JSON-testable) and downloaded as raw bytes + Content-Type. S3/object-storage = a documented SCALE delta (large blobs). Mirrors v43/v44's domain + tenant-isolation + BFF patterns.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A new `gateway/artifacts/` domain: an `artifacts` table (tenant-scoped) + alembic migration (chained on head d8f0a2b4c6e8) + a repository + a `/v1/artifacts` REST surface authenticated by the same `KeyAuthenticator` (+ expiry gate) as v43/v44. Endpoints: `POST /v1/artifacts` ({name, content_type, content_base64} → decode → size-cap → store) · `GET /v1/artifacts` (list the tenant's metadata, newest first, paginated — NO content) · `GET /v1/artifacts/{id}` (download: raw bytes + Content-Type + Content-Disposition) · `DELETE /v1/artifacts/{id}` (soft delete). STRICT tenant isolation (cross-tenant id → 404). A configurable max size (reject over-cap BEFORE storing → 413).
  - Dashboard: an `/app/artifacts` surface (upload a file, list, download, delete) via the BFF. Role-open.
Out:
  - S3 / object storage / pre-signed URLs / CDN — the MVP stores bytes in Postgres BYTEA (size-capped); object storage is a SCALE delta (needs new infra).
  - Versioning, folders/hierarchy, sharing across tenants, public links, large-file chunked/multipart-resumable upload, virus scanning, thumbnails/transforms.
  - Changing any existing route — additive only.

## Shared decisions & glossary deltas   (living — every task must honor these)
- ARTIFACT (NEW glossary): a tenant-scoped {id, name, content_type, size_bytes, content: bytea, created_at, deleted_at} row owned by `tenant_id` (+ creator `key_id`); an opaque stored file. Keyed by a server UUID.
- TENANT-ISOLATION (security, HARD invariant — same as v43/v44): every artifact query filters by the authenticated `tenant_id`; a cross-tenant id → 404; download NEVER serves another tenant's bytes. The milestone's security-sensitive surface — freeze + independently refute-verify.
- AUTH REUSE: `/v1/artifacts` authenticates with `KeyAuthenticator.authenticate(raw_key)` + the v43/v44 expiry gate (sk- key, not admin JWT).
- SIZE-CAP / DESIGN-FOR-FAILURE: a default-ON max-size (GATEWAY_ARTIFACT_MAX_BYTES) rejects an over-cap upload with 413 BEFORE storing (no partial write); invalid base64 → 422; a download of a missing/cross-tenant id → 404. One transaction per request.
- HONEST STORAGE: bytes are stored + returned verbatim; size_bytes is the real decoded length; content_type is preserved.
- FE honors WCAG-AA + v23/v24 tokens + the four states; the BFF keeps its fail-closed auth + (v42) binary-body handling.

## Shared / risky contracts (freeze these first)
- The artifacts schema + `/v1/artifacts` REST (upload envelope + raw-bytes download) + the size-cap + tenant-isolation rule -> owning task `artifacts-backend`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] artifacts-backend   depends-on: none              — `gateway/artifacts/` domain: ORM + migration + repository + `/v1/artifacts` (upload/list/download/delete) auth'd via KeyAuthenticator, BYTEA storage, size-cap, STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + size-cap + isolation contract. (gate PASS, 12 tests)
- [x] artifacts-ui        depends-on: artifacts-backend   — dashboard `/app/artifacts` (upload + list + download + delete) via the BFF + an additive BFF binary-passthrough fix; role-open nav entry. (gate PASS, 11 tests)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An API key holder can POST an artifact (base64), list their artifacts, GET /v1/artifacts/{id} to download the exact bytes with the right Content-Type, and DELETE it — all tenant-scoped; another tenant's id returns 404 and their bytes are never served; an over-cap upload is rejected (413) before storing   (← artifacts-backend)
- [x] A signed-in user can, in `/app/artifacts`, upload a file, see their list, download it back, and delete it   (← artifacts-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : NEW `gateway/artifacts/` domain — a tenant-scoped, API-key-authenticated `/v1/artifacts` file store (upload/list/download/delete) on the existing Postgres (NO object storage). Bytes stored in a BYTEA column; upload via a base64 JSON envelope (any file type, fully JSON-testable); download returns raw bytes + Content-Type + Content-Disposition: attachment. Config: GATEWAY_ARTIFACT_MAX_BYTES (default 10 MiB; 0=unlimited). STRICT tenant isolation (cross-tenant id → 404; list uses load_only to exclude the bytes; the download byte-fetch is tenant-scoped). Size-cap enforced AFTER decode but BEFORE insert (413, no partial write). XSS guard: download is ALWAYS attachment (never inline render of a caller-supplied content_type) + sanitized filename. Migration b3e5f9a7c1d4 (chained on d8f0a2b4c6e8); `artifacts` registered in the tests/migrations EXPECTED_TABLES manifest. NEW error code ERR_PAYLOAD_INVALID_BASE64 (422). 12 DB-backed tests; no-DB make test-fast 206.
- dashboard : NEW `/app/artifacts` workspace (upload + list + download + delete, four states, WCAG-AA) via the BFF; lib/artifacts.ts BFF client; a role-open "Artifacts" nav entry. PLUS an additive BFF binary-passthrough branch in app/api/gw/[...path]/route.ts so non-JSON downloads pass through verbatim (the buffered fallback was coercing them to null JSON). vitest 581 → 592 green; tsc 0; eslint 0; the 3 BFF infra suites (25 tests) stay green.
- tooling / skill / book : untouched (only `.add/` bookkeeping + the sanctioned EXPECTED_TABLES manifest edit).

### Cross-task evidence   (one row per task)
- artifacts-backend : gate=PASS · tests=12 green (DB-backed; no-DB make test-fast 206; tests/migrations 6/6 with artifacts registered; single linear migration head b3e5f9a7c1d4, offline --sql renders) · residue=tenant-isolation + XSS-attachment guard + size-cap-before-insert verified by a full manual read of router + repository + tests (a faithful replica of the twice-refuted v43/v44 pattern). Deltas: S3/object storage for large blobs (scale), file versioning, dedup, virus scanning, chunked/resumable upload, thumbnails.
- artifacts-ui : gate=PASS · tests=11 green (full dashboard 592, +11; tsc 0; eslint 0; the security-adjacent BFF diff reviewed directly by me — gateway problem+json errors now pass through as raw bytes but bff-client parses errors content-type-agnostically, so no regression; 25 BFF infra tests green) · residue=the jsdom download test asserts wiring not an OS download (flagged at freeze); a richer file preview is a delta.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
  - EC1 (API key holder can upload/list/download exact bytes/delete, tenant-scoped, cross-tenant 404, over-cap 413 before storing): artifacts-backend — 12 DB-backed tests incl. test_upload_download_roundtrip (exact bytes) + test_tenant_isolation + test_over_cap_413 + test_download_forces_attachment.
  - EC2 (signed-in user can upload/list/download/delete in /app/artifacts): artifacts-ui — 11 tests incl. upload/download/delete over the EC1 store via the BFF (+ the BFF binary-passthrough fix the download needs).
- goal: a user (and any API key holder) can upload files, list them, download the exact bytes back, and delete them via a tenant-scoped gateway store surfaced in the dashboard — proven by 12 gateway + 11 dashboard tests green (592 total dashboard, 206 no-DB gateway, no regression), strict tenant isolation + an XSS-attachment guard, ZERO new infra (BYTEA on the existing Postgres), and design-for-failure (size-cap-before-insert, best-effort FE error states).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [ ] v45 commits land on the v40→v45 task stack (committed locally): t1 artifacts-backend → t2 artifacts-ui → .add close. PUSH/PR await Tin's go-ahead (outward act).
- [ ] open a PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]]); v40–v45 are a stack — merge in order or retarget.
- [ ] deploy note: run `alembic upgrade head` to apply migration b3e5f9a7c1d4 (creates artifacts). NO new infra/env; optionally set GATEWAY_ARTIFACT_MAX_BYTES (default 10 MiB). Routes are additive + tenant-scoped (no feature flag). S3/object storage is NOT used (BYTEA on Postgres) — a documented scale delta for large blobs.
- [ ] v45 joins the releasable set (v33–v44 already pending); bundle into the next release cut when Tin calls it (release.md).
