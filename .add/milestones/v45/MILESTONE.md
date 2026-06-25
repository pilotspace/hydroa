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
- [ ] artifacts-backend   depends-on: none              — `gateway/artifacts/` domain: ORM + migration + repository + `/v1/artifacts` (upload/list/download/delete) auth'd via KeyAuthenticator, BYTEA storage, size-cap, STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + size-cap + isolation contract.
- [ ] artifacts-ui        depends-on: artifacts-backend   — dashboard `/app/artifacts` (upload + list + download + delete) via the BFF; role-open nav entry.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An API key holder can POST an artifact (base64), list their artifacts, GET /v1/artifacts/{id} to download the exact bytes with the right Content-Type, and DELETE it — all tenant-scoped; another tenant's id returns 404 and their bytes are never served; an over-cap upload is rejected (413) before storing   (← artifacts-backend)
- [ ] A signed-in user can, in `/app/artifacts`, upload a file, see their list, download it back, and delete it   (← artifacts-ui)

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
