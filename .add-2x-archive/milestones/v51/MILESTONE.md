# MILESTONE: Artifacts on real object storage (MinIO)

goal: An artifact's bytes are persisted to and served from a real self-hosted object store (MinIO), replacing inline Postgres BYTEA, with honest-degrade to inline storage when the store is unconfigured.
rationale: new-major (micro-milestone) — the deferred real-infra tail of v45, picked up FOR REAL. EXTENDS v45 (artifacts BYTEA store): it builds exactly the "S3 / object storage … SCALE delta (needs new infra)" that v45's Scope Out deferred, now tracked as the `[SPEC · open]` delta on task `artifacts-backend`. Confirmed with Tin 2026-06-26: build for real against self-hosted MinIO in the dev compose stack (no cloud creds, live-verifiable today), red/green TDD, design-for-failure. First of two ordered micro-milestones (this, then v52 realtime relay); sequenced before video-jobs-backend.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A new object-store seam in the gateway: an `ObjectStore` port (`put` / `get` / `delete` / `health`) over an S3-compatible backend, plus a real adapter (boto3/S3 wire) targeting self-hosted MinIO. Design-for-failure baked in: explicit per-op timeout, bounded retry on idempotent reads ONLY, and a circuit breaker that opens on repeated failure → a typed 5xx, never a hang or a partial write.
  - MinIO added to `infra/docker-compose.dev.yml` (+ a bucket bootstrap) so the store is fully local and live-verifiable with no cloud account; config knobs (endpoint, bucket, region, access/secret key, enable flag) on `Settings`.
  - An additive migration on the `artifacts` table: `storage_backend ∈ {inline, s3}` + a nullable `object_key`; `content` BYTEA becomes nullable (NULL when bytes live in S3). Existing rows default to `inline` and keep working.
  - Wiring the v45 `/v1/artifacts` upload / download / delete path to store + serve + remove bytes through the port WHEN the store is configured, and to honest-degrade to inline BYTEA when it is not — the REST contract (envelope, raw-bytes download, 404s, size-cap/413, tenant isolation) stays byte-identical either way.
  - A live MinIO round-trip verification (upload → download exact bytes → delete) through the real object store.
Out:
  - Any cloud object store (AWS S3 / Cloudflare R2 / GCS) — MinIO is the only target this milestone; a cloud backend is a config-only delta (same S3 wire).
  - Pre-signed URLs / direct-to-store browser upload / CDN, multipart/resumable upload, versioning, lifecycle/expiry policies, server-side encryption beyond MinIO defaults, dedup, thumbnails/transforms.
  - Backfill/migration of EXISTING inline rows into S3 — new uploads use the configured backend; old `inline` rows stay inline (read path handles both). A bulk migration tool is a delta.
  - Changing the `/v1/artifacts` REST shape, the dashboard `/app/artifacts` UI, or any other route — storage-internal only; additive columns + config.

## Shared decisions & glossary deltas   (living — every task must honor these)
- OBJECT STORE (NEW glossary): a `put(key, bytes, content_type)` / `get(key) -> bytes` / `delete(key)` / `health()` port over an S3-compatible backend; the canonical byte location for an artifact when configured. Keys are tenant-scoped: `artifacts/{tenant_id}/{artifact_id}`.
- STORAGE BACKEND (NEW glossary): an artifact row's `storage_backend ∈ {inline, s3}` records WHERE its bytes live; `object_key` is the S3 key when `s3`; `content` BYTEA is nullable and NULL when `s3`. Read dispatches on this column — both backends are always readable.
- DESIGN-FOR-FAILURE (HARD — the core IO rule raised to the gateway↔object-store tier): every object-store call has an explicit timeout, a bounded retry on idempotent reads ONLY (never blind-retry a put/delete), and a circuit breaker on repeated upstream failure. A store failure renders a typed 5xx error state — never a hang, never a partial/half-written artifact.
- HONEST DEGRADATION (HARD): with the object store UNconfigured (no endpoint/bucket), artifacts transparently use inline BYTEA — the exact v45 behavior — and the REST contract is byte-identical. NEVER a fabricated success and NEVER silent data loss; a write that cannot reach a CONFIGURED store fails loudly (5xx), it does not silently fall back.
- TENANT-ISOLATION (security, HARD — same invariant as v45): the object key is tenant-scoped and derived from the authenticated `tenant_id`; download/delete NEVER cross tenants; a cross-tenant id → 404; another tenant's bytes are never served. The milestone's security-sensitive surface — freeze + independently refute-verify.
- BACKWARD-COMPAT: the migration is additive (new nullable columns + a server default of `inline`); existing v45 rows and the existing tests stay green unchanged; `content` nullability is the only column-type change and is widening (NOT NULL → NULL).
- ATOMICITY: the DB row and the stored object must not diverge — on upload, write the object FIRST then commit the row (a failed commit leaves an orphan object, cleaned best-effort / swept later — never a row pointing at absent bytes); on delete, soft-delete the row then best-effort remove the object.

## Shared / risky contracts (freeze these first)
- The `ObjectStore` port (`put`/`get`/`delete`/`health`) + its config knobs + the design-for-failure semantics (timeout / bounded-retry-reads-only / circuit-breaker) -> owning task `object-store-port`
- The `artifacts` schema delta (`storage_backend` + `object_key`, `content` nullable) + the inline↔s3 write/read/delete dispatch + the honest-degrade + atomicity rules -> owning task `artifacts-s3-persistence`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] object-store-port        depends-on: none                  — NEW `ObjectStore` port + S3/MinIO adapter (aioboto3 wire) with design-for-failure (timeout · bounded-retry-reads-only · circuit breaker); MinIO in `docker-compose.dev.yml` + bucket bootstrap; `Settings` knobs (endpoint/bucket/region/keys/enable). FREEZES the port + config contract. Adapter tests against real MinIO + unit tests for the failure seams.
- [x] artifacts-s3-persistence depends-on: object-store-port     — additive migration (`storage_backend` + `object_key`, `content` nullable); wire `/v1/artifacts` upload/download/delete through the port when configured, honest-degrade to inline BYTEA when not; tenant-scoped keys; preserve the v45 REST contract + tenant isolation byte-for-byte. DB-backed tests for both backends + the failure/degrade paths. (Freeze hardening: delete is soft-only — object left for the sweep.)
- [x] artifacts-s3-live-verify depends-on: artifacts-s3-persistence — earned-green + a live round-trip (upload → download exact bytes → soft-delete leaves object) through real MinIO via the dev compose stack; proves the object-store path end-to-end.

## Exit criteria (observable; map each to the task that delivers it)
- [x] With MinIO configured, uploading an artifact stores its bytes in the object store (row `storage_backend=s3`, `content` NULL) and `GET /v1/artifacts/{id}` serves the EXACT bytes back with the right Content-Type; delete is SOFT-only (row hidden, object left for the sweep) — proven against real MinIO   (← artifacts-s3-persistence + artifacts-s3-live-verify)
- [x] With the object store UNconfigured, upload / list / download / delete still work via inline Postgres BYTEA (honest-degrade), and the v45 REST contract + tenant isolation are byte-identical (cross-tenant id → 404, over-cap → 413 before storing)   (← artifacts-s3-persistence; 12 v45 tests green unchanged)
- [x] The object-store adapter degrades safely under store failure: an op that exceeds its timeout or trips the breaker returns a typed 5xx (never a hang); reads retry within bound, writes/deletes do not blind-retry; no row ever points at absent bytes (object-first atomicity)   (← object-store-port)
- [x] A live MinIO round-trip (upload → download exact bytes → soft-delete leaves object) passes end-to-end through the real object store in the dev compose stack   (← artifacts-s3-live-verify; 2/2 live green)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : NEW `gateway.objectstore` (port + S3/MinIO adapter, design-for-failure) · `Settings.object_store_*` (8 knobs) · `error_catalog.OBJECT_STORE_UNAVAILABLE` (503) · migration `e2f4a6b8c0d1` (`storage_backend`+`object_key`, `content` nullable) · `ArtifactRow`/`ArtifactRepository.create` extended · `/v1/artifacts` create/download branch on `app.state.object_store` (honest-degrade) · `main.create_app` wiring · v48 video worker call-site updated (inline, byte-identical).
- infra   : `docker-compose.dev.yml` MinIO + bucket-bootstrap; `Makefile` test-fast adds `tests/objectstore`; `pyproject`/`uv.lock` add aioboto3.
- tooling : untouched.
- skill   : untouched.
- book    : untouched.

### Cross-task evidence   (one row per task)
- object-store-port        : gate=PASS · 15 unit + 4 live (MinIO) green · residue=none (3 spec deltas: orphan-sweep, cloud-S3/virtual-host, /admin health)
- artifacts-s3-persistence : gate=PASS · 8 s3 + 12 v45 regression + migration-parity green; make test-fast 243 · independent refute-read NO-BLOCKER (isolation 0.97) · residue=3 spec deltas (video-worker-through-store, deleted-row sweep, downgrade guard)
- artifacts-s3-live-verify : gate=PASS · 2/2 live through real MinIO (exact bytes + DB row + soft-delete leaves object); 2 skipped without env · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which) — criteria 1+4 ← artifacts-s3-live-verify live run; 2 ← artifacts-s3-persistence (v45 regression); 3 ← object-store-port failure-seam units.
- goal: "An artifact's bytes are persisted to and served from a real self-hosted object store (MinIO), replacing inline Postgres BYTEA, with honest-degrade to inline storage when unconfigured." — PROVEN by the live MinIO round-trip (exact bytes in/out, row storage_backend=s3/content NULL) AND the 12 v45 inline tests staying green with the store unconfigured.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] open a PR from this Close ship-review (branch `feat/v51-artifacts-object-store`); Tin reviews + merges to main.
- [ ] deploy note: migration `e2f4a6b8c0d1` is ADDITIVE (no backfill) — run `alembic upgrade head`; set `GATEWAY_OBJECT_STORE_*` (enabled/endpoint/bucket/region/access_key_id/secret) to point at MinIO or a cloud S3; UNSET = inline BYTEA honest-degrade (zero behavior change). Existing rows stay `storage_backend='inline'`.
- [ ] tag / publish / deploy (human-run, per release.md) — bundle with v52 (realtime) or cut standalone, Tin's call.
