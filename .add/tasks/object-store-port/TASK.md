# TASK: ObjectStore port + S3/MinIO adapter (design-for-failure)

slug: object-store-port · created: 2026-06-26 · stage: production
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
  - NEW module `apps/gateway/src/gateway/objectstore/` — does not exist yet. Holds: a `port.py` (`ObjectStore` Protocol) + `s3.py` (`S3ObjectStore` adapter over httpx + SigV4) + `errors.py` + `config`/factory wiring. The task's PRIMARY deliverable.
  - `apps/gateway/src/gateway/proxy/infrastructure/bedrock_sigv4.py:sign_request(*, method, url, body, service, region, credentials, timestamp) -> dict[str,str]` + `AwsCredentials(access_key_id, secret_access_key[repr=False], region, session_token=None)` — pure-stdlib SigV4 signer. REUSE verbatim with `service="s3"`: S3 uses the identical SigV4 algorithm, the path canonicalization (`quote(path, safe="/~")`) matches S3 single-encoding, and `x-amz-content-sha256` (payload hash) is already emitted — exactly what S3 requires. Zero new deps.
  - `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py:CircuitBreaker` — reusable per-instance breaker; surface `call_allowed()` · `guard()` (raises `CircuitOpenError`) · `record_success()` · `on_upstream_error()` · `record_failure()`. Threshold 5 / cooldown 30s defaults (ctor-overridable). Imports `CircuitOpenError` from `gateway.proxy.domain.errors`.
  - `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py:execute_with_retry(do_request, render_response, *, breaker, provider, max_retries, backoff_base, deadline_s, policy, metrics_registry)` — the retry PATTERN to mirror (breaker.guard() before each attempt; ConnectError/ConnectTimeout/PoolTimeout = retryable; ReadTimeout/WriteTimeout/NetworkError = TERMINAL, never retried). It is OpenAI-render-coupled, so the adapter mirrors its structure with a thinner S3-shaped retry rather than calling it verbatim.
  - `apps/gateway/src/gateway/core/config.py:Settings` — add a `GATEWAY_OBJECT_STORE_*` knob block (endpoint, bucket, region, access key, secret key [SecretStr], enable flag, timeout, max_retries) mirroring the `artifact_max_bytes` (line 544) / `realtime_*` (546-554) `Field(... )  # GATEWAY_*` style.
  - `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` — add an object-store error (e.g. `OBJECT_STORE_UNAVAILABLE` → 503) mirroring `PAYLOAD_INVALID_BASE64 = ErrorSpec(422, "ERR_…", "…")` (line 489).
Context (working folder):
  - `infra/docker-compose.dev.yml` — postgres(:5433) + redis(:6380) today; ADD a `minio` service (+ a one-shot `mc` bucket-bootstrap) so the store is fully local + live-verifiable, healthcheck-gated like the existing services.
  - `apps/gateway/pyproject.toml` — deps are `httpx>=0.28` + `tenacity>=8.2`; NO boto3/botocore/aioboto3 today. DECISION (Tin, at the freeze): ADD `aioboto3` (async S3 SDK; pulls aiobotocore+botocore) to de-risk S3 signing/addressing vs the Bedrock-pinned signer. Fallback if the uv lock clashes: `boto3` sync via `asyncio.to_thread` (same port surface).
  - `.env.example` / CI — add the new GATEWAY_OBJECT_STORE_* env names (unset = honest-degrade, the persistence task's concern).
Honors (patterns / conventions):
  - CLAUDE.md / core IO rule: design-for-failure — explicit timeout, bounded retry (idempotent reads ONLY), circuit breaker; a store failure → typed 5xx, never a hang or partial write (the milestone's HARD shared decision).
  - Secret hygiene: mirror `AwsCredentials` (secret excluded from repr) + the BYOK `SecretStr` config pattern — the secret key never appears in logs/repr.
  - Port/adapter layering (CONVENTIONS.md): a domain Protocol (`ObjectStore`) + an infrastructure adapter (`S3ObjectStore`), mirroring the proxy `CompletionUpstream` Protocol → adapter shape; no API/REST in this task (the port is consumed by the persistence task).
  - Tests: real-MinIO adapter tests (live round-trip) + unit tests for the failure seams (mock httpx transport to force timeout/5xx/breaker-trip) — mirrors the live-stub split used across provider tasks.
Anchors the contract cites: `circuit_breaker.CircuitBreaker` · `core.config.Settings` (SecretStr knobs) · `core.error_catalog.ErrorSpec` · `aioboto3.Session` + `botocore.config.Config` + `botocore.exceptions.{ClientError,EndpointConnectionError,ConnectTimeoutError,ReadTimeoutError}` · NEW `gateway.objectstore.ObjectStore` (Protocol) + `gateway.objectstore.S3ObjectStore` (adapter) + `build_object_store` factory   (NOTE: the Bedrock `sign_request` signer is NO LONGER reused — superseded by the SDK per Tin's freeze decision)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: ObjectStore port + real S3/MinIO adapter with design-for-failure
Framings weighed: aioboto3 (async S3 SDK; chosen — Tin: de-risk S3 signing/addressing with the battle-tested SDK; async-native, non-blocking) · httpx + reused Bedrock SigV4 signer (zero-dep but the signer was Bedrock-pinned — the risk we're retiring) · boto3 sync in a threadpool (blocks/needs offloading) · minio-py (narrower)
Must:
<must>
  - An `ObjectStore` Protocol (domain) with async `put(key, data, content_type) -> None` · `get(key) -> bytes` · `delete(key) -> None` · `health() -> bool`.
  - An `S3ObjectStore` adapter implements it over an injected aioboto3 S3 client (`put_object`/`get_object`/`delete_object`/`head_bucket`) targeting `endpoint_url` (MinIO) with path-style addressing (`s3={"addressing_style":"path"}`).
  - DESIGN-FOR-FAILURE (own policy ON TOP of the SDK — botocore's own retries are DISABLED via `Config(retries={"max_attempts":1})` so the policy is ours, not hidden): every call has an explicit per-op connect+read timeout (`object_store_timeout_seconds`); idempotent reads (`get`/`health`) retry ≤ `object_store_max_retries` on retryable botocore errors (EndpointConnectionError/ConnectTimeoutError/ReadTimeoutError/5xx ClientError); mutations (`put`/`delete`) are AT-MOST-ONCE (never retried).
  - A per-instance `CircuitBreaker` guards every call (`guard()` before the request, `record_success()`/`on_upstream_error()` after); while OPEN, a call raises WITHOUT touching the network.
  - `get` of a missing key raises a distinct `ObjectNotFoundError` (not the generic unavailable).
  - A factory `build_object_store(settings) -> ObjectStore | None` returns the adapter only when fully configured (`enabled AND endpoint AND bucket AND access_key AND secret_key`), else `None` (the honest-degrade signal for the persistence task).
  - The secret access key is `SecretStr` — never in repr/logs (mirror `AwsCredentials`).
  - `infra/docker-compose.dev.yml` gains a `minio` service + a one-shot bucket bootstrap so the adapter is live-verifiable locally.
</must>
Reject:
<reject>
  - a configured-store op that exceeds its timeout OR exhausts read-retries OR returns 5xx -> raise `ObjectStoreUnavailableError` -> "ERR_OBJECT_STORE_UNAVAILABLE" (503)
  - a call issued while the breaker is OPEN -> raise `ObjectStoreUnavailableError` ("ERR_OBJECT_STORE_UNAVAILABLE", 503) with NO network call
  - `get(key)` where the object does not exist -> raise `ObjectNotFoundError` (internal typed exc; the caller maps it to its own 404)
</reject>
After:
<after>
  - after `put(key, data, ct)`: `get(key)` returns the EXACT bytes; the stored content-type is preserved.
  - after `delete(key)`: a subsequent `get(key)` raises `ObjectNotFoundError`; `delete` of an absent key is idempotent (no error).
  - a healthy reachable store -> `health()` is True; repeated failures open the breaker; a success closes it.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ aioboto3/aiobotocore integrates cleanly into the existing dep set and uv lock without a botocore version clash — lowest confidence because aiobotocore hard-pins a botocore range and the repo also has httpx/tenacity/sqlalchemy; if wrong: the lock fails to resolve and we fall back to boto3-in-a-threadpool (sync SDK offloaded via asyncio.to_thread) — CONTAINED to this task's dep step, no contract change (the port/adapter surface is identical).
  - [ ] path-style addressing (`addressing_style=path`) + `endpoint_url` is the correct MinIO config (virtual-host style needs DNS we don't have) — confirm at the adapter live test.
  - [ ] disabling botocore retries (`max_attempts=1`) and layering our own read-only retry is accepted — keeps mutations at-most-once and the failure policy explicit rather than hidden in botocore's adaptive mode.
  - [ ] the completion-path breaker defaults (threshold 5 / cooldown 30s) port cleanly to object-store ops.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: put-then-get round-trips exact bytes
  Given a configured S3ObjectStore against MinIO
  When put("artifacts/t/a", b"\x00bytes\xff", "application/pdf") then get("artifacts/t/a")
  Then get returns b"\x00bytes\xff" exactly
  And the object's content-type is preserved as application/pdf

Scenario: get of a missing key raises ObjectNotFound
  Given a configured store with no object at "missing"
  When get("missing")
  Then ObjectNotFoundError is raised
  And no ObjectStoreUnavailableError is raised (a 404 is not an outage)

Scenario: delete removes the object and is idempotent
  Given a stored object at "k"
  When delete("k") then delete("k") again then get("k")
  Then both deletes succeed without error
  And the final get raises ObjectNotFoundError

Scenario: a timed-out op surfaces as unavailable
  Given a store whose transport always exceeds the per-op timeout
  When get("k")
  Then ObjectStoreUnavailableError is raised (maps to ERR_OBJECT_STORE_UNAVAILABLE / 503)
  And the call did not hang past the configured timeout

Scenario: idempotent reads retry then succeed
  Given a transport that fails with ConnectError once then returns 200
  When get("k") with max_retries>=1
  Then the bytes are returned
  And exactly 2 attempts were made

Scenario: mutations are not retried (at-most-once)
  Given a transport that fails put with ConnectError
  When put("k", data, ct)
  Then ObjectStoreUnavailableError is raised
  And exactly 1 PUT attempt was made (no blind retry of a mutation)

Scenario: open breaker rejects without a network call
  Given a breaker tripped OPEN by prior failures
  When get("k")
  Then ObjectStoreUnavailableError is raised
  And the transport was never invoked

Scenario: factory honest-degrade signal
  Given settings with the object store NOT fully configured
  When build_object_store(settings)
  Then it returns None
  And when fully configured it returns an S3ObjectStore instance

Scenario: secret is never exposed
  Given an S3ObjectStore built with a secret access key
  When repr(store) and repr(its credentials) are rendered
  Then the secret value does not appear in either
  And the access_key_id may appear but the secret is masked
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PORT  gateway.objectstore.port.ObjectStore  (typing.Protocol, async)
  async put(key: str, data: bytes, content_type: str) -> None
  async get(key: str) -> bytes            # raises ObjectNotFoundError | ObjectStoreUnavailableError
  async delete(key: str) -> None          # idempotent; raises ObjectStoreUnavailableError
  async health() -> bool                  # True iff the bucket is reachable; never raises

ADAPTER  gateway.objectstore.s3.S3ObjectStore
  __init__(self, settings: Settings, *, client_factory: Callable[[], AsyncCtxMgr[S3Client]] | None = None,
           breaker: CircuitBreaker | None = None)
  - client_factory default builds aioboto3.Session().client("s3", endpoint_url=settings.object_store_endpoint,
        aws_access_key_id=…, aws_secret_access_key=settings.object_store_secret_access_key.get_secret_value(),
        region_name=settings.object_store_region,
        config=botocore.config.Config(connect_timeout=T, read_timeout=T,
            retries={"max_attempts": 1}, s3={"addressing_style": "path"}))   # botocore retries OFF — policy is ours
    (injectable so unit tests pass a fake async-ctx client; one client opened per op via `async with`)
  - put:    async with client_factory() as s3: await s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
  - get:    await s3.get_object(...) → await resp["Body"].read()        # the exact bytes
  - delete: await s3.delete_object(...)                                  # S3 returns 204 even if absent → idempotent
  - health: await s3.head_bucket(Bucket=bucket) → True; any error → False (never raises)
  - own failure policy: breaker.guard() before each call; reads (get/head) retry ≤ settings.object_store_max_retries
        on EndpointConnectionError|ConnectTimeoutError|ReadTimeoutError|ClientError(5xx); put/delete AT-MOST-ONCE
  - on success breaker.record_success(); on a retryable/terminal failure breaker.on_upstream_error() → ObjectStoreUnavailableError
  - get/head ClientError 404 / NoSuchKey / NoSuchBucket-on-get → ObjectNotFoundError (NOT a breaker failure)

FACTORY  gateway.objectstore.build_object_store(settings) -> ObjectStore | None
  None unless (settings.object_store_enabled and endpoint and bucket and access_key_id and secret_access_key)

ERRORS  gateway.objectstore.errors
  ObjectNotFoundError(Exception)
  ObjectStoreUnavailableError(Exception)   → error_catalog.OBJECT_STORE_UNAVAILABLE = ErrorSpec(503, "ERR_OBJECT_STORE_UNAVAILABLE", "object store is unavailable")

CONFIG  core.config.Settings   (GATEWAY_OBJECT_STORE_*)
  object_store_enabled: bool = False
  object_store_endpoint: str = ""            # e.g. http://localhost:9000
  object_store_bucket: str = ""
  object_store_region: str = "us-east-1"
  object_store_access_key_id: str = ""
  object_store_secret_access_key: SecretStr = SecretStr("")
  object_store_timeout_seconds: float = Field(default=5.0, gt=0)
  object_store_max_retries: int = Field(default=2, ge=0)   # READS only

SCHEMA: none (no DB in this task — consumed by artifacts-s3-persistence).
INFRA: infra/docker-compose.dev.yml — minio (S3 API on :9000) + a one-shot mc bucket-create, healthcheck-gated.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-26).
Least-sure flag surfaced at freeze: [contract] reusing aioboto3/aiobotocore — lock-resolution risk (aiobotocore hard-pins botocore); if it clashes, fallback to boto3 + asyncio.to_thread with an IDENTICAL port surface (no contract change). [spec] read-only retry with mutations at-most-once (botocore's own retries OFF via max_attempts=1) — a failed PUT is not blind-retried. Tin's freeze decision: ADD the SDK (aioboto3) to de-risk S3 signing/addressing rather than reuse the Bedrock-pinned SigV4 signer. (Lock since RESOLVED clean: aioboto3 15.5.0 / botocore 1.40.61 — risk retired.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (objectstore module)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  UNIT (injected fake async-ctx S3 client — join `make test-fast`, no MinIO/network needed):
  - test_get_missing_key_raises_not_found: fake get_object raises ClientError 404/NoSuchKey / assert ObjectNotFoundError, not Unavailable, and breaker NOT failed
  - test_timeout_surfaces_unavailable: fake get_object raises ReadTimeoutError / assert ObjectStoreUnavailableError
  - test_read_retries_then_succeeds: fake get_object raises EndpointConnectionError once then returns body / assert bytes + exactly 2 calls
  - test_mutation_not_retried: fake put_object raises EndpointConnectionError / assert Unavailable + exactly 1 call (no retry)
  - test_open_breaker_no_network: pre-trip breaker / assert Unavailable + the fake client was never opened
  - test_factory_none_when_unconfigured / test_factory_builds_when_configured
  - test_secret_not_in_repr: assert object_store_secret_access_key value absent from repr(store)
  - test_put_passes_body_and_content_type: assert put_object called with Body==data and ContentType==content_type and path-style Bucket/Key
  - test_delete_absent_is_noop: fake delete_object returns 204 for absent key / assert no error
  - test_health_false_on_error_never_raises: fake head_bucket raises / assert health() is False (no exception)
  LIVE (real MinIO via dev compose — skip-marked when GATEWAY_OBJECT_STORE_ENDPOINT unset, like other live tests):
  - test_live_put_get_roundtrip_exact_bytes
  - test_live_delete_removes_then_get_not_found / test_live_delete_absent_is_noop
  - test_live_health_true_when_reachable
</test_plan>

Tests live in: `apps/gateway/tests/objectstore/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/objectstore/` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/pyproject.toml` `infra/docker-compose.dev.yml` `.env.example`
Strategy (ordered batches): 1. add aioboto3 dep + uv lock (fallback boto3+to_thread if lock clashes) 2. errors.py + port.py (Protocol) 3. config knobs + error_catalog ErrorSpec 4. s3.py adapter (client_factory → per-op `async with` → put/get/delete/head → own read-retry/breaker/timeout seams → ClientError 404→NotFound) 5. factory build_object_store 6. docker-compose minio + bucket bootstrap
Safety rule (feature-specific): mutations (put/delete) are AT-MOST-ONCE — never inside the retry loop; botocore's own retries are OFF (max_attempts=1); the secret access key is read via get_secret_value() only at client-build time and never logged.
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

- [x] all tests pass — test-fast 243 passed + 4 skipped (objectstore unit 15 green); live MinIO 4/4 green
- [x] coverage did not decrease — additive module + tests; whole-repo gate unaffected (run via full `make test`)
- [x] no test or contract was altered during build — only ruff-autofix style on my OWN new test (annotation/__all__ sort), no behavior touched
- [x] the green was EARNED, not gamed — refute-read (below) traced each assert to real adapter logic; the 15 unit tests use a botocore-faithful fake AND the 4 live tests prove the REAL aioboto3 wire (no stubbed-away logic)
- [x] concurrency / timing of the risky operation is safe — per-op `async with` client (no shared mutable client); breaker is per-instance asyncio-single-thread; mutations at-most-once (no double-write under retry)
- [x] no exposed secrets, injection openings, or unexpected dependencies — secret is SecretStr, never in repr (test) + only get_secret_value() at client build; aioboto3 added intentionally + uv.lock resolved clean; keys are opaque caller strings (tenant-scoping is task 2's concern)
- [x] layering & dependencies follow CONVENTIONS.md — domain Protocol (port.py) + infra adapter (s3.py) + typed errors; reuses the existing CircuitBreaker; no REST/DB in this task
- [x] a person reviewed and approved the change — Tin approved the contract at the freeze (incl. the SDK decision); build evidence (live MinIO 4/4) reported back

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] put→get returns the EXACT bytes through real MinIO — test_live_put_get_roundtrip_exact_bytes (live, MinIO :9000, 4/4 green)
- [x] missing key → ObjectNotFoundError, NOT a store outage (breaker untouched) — test_get_missing_key_raises_not_found + live test_live_delete_removes_then_get_not_found
- [x] store failure → typed ObjectStoreUnavailableError (→ ERR_OBJECT_STORE_UNAVAILABLE/503), never a hang — test_timeout_surfaces_unavailable + test_open_breaker_rejects_without_client (transport never touched)
- [x] mutations at-most-once, reads retry within bound — test_mutation_not_retried (exactly 1 put) + test_read_retries_then_succeeds (exactly 2 gets)
- [x] secret access key never in repr — test_secret_not_in_repr; live auth succeeding proves the secret is used correctly
- [x] unconfigured store → build_object_store None (honest-degrade signal for task 2) — test_factory_none_when_unconfigured

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — ObjectStore/S3ObjectStore/build_object_store exported from `gateway.objectstore.__init__`; error_catalog.OBJECT_STORE_UNAVAILABLE added; 8 GATEWAY_OBJECT_STORE_* knobs on Settings. All referenced by the 15 unit + 4 live tests. HONEST RESIDUE (by design): `build_object_store` has NO app-code caller yet — it is consumed by task 2 (artifacts-s3-persistence); this is the breadth-first decomposition (port → persistence), not an orphan. Recorded as the task's spec-delta hand-off.
- [x] DEAD-CODE (code) — no unused symbol WITHIN the module; `_default_client_factory`'s inner closure is exercised by the 4 live MinIO tests (real aioboto3 path), the injected fake covers the unit paths.
- [x] SEMANTIC (n/a) — code task; no prose artifact.

### GATE RECORD
Outcome: PASS
Reviewed by: AI refute-read + Tin (contract freeze) · date: 2026-06-26
Evidence: test-fast 243 passed/4 skipped · live MinIO 4/4 green · ruff clean (my files) · pyright 0 new errors (baseline 12 unchanged) · lock resolved clean. Refute-read: each unit assert traced to real adapter logic (retry counts, breaker-guard-before-network, 404≠outage, secret-masked); no overfit/vacuous/stubbed-away green; the live tests prove the real wire. No security finding (no auth/tenant surface in this task; secret masked; keys opaque). One honest residue: port not yet app-wired (task 2's job).

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ERR_OBJECT_STORE_UNAVAILABLE rate · breaker-open episodes · object-store op latency (p95 vs the configured timeout) · read-retry rate.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · seeded] [→ v51/artifacts-s3-persistence] wire build_object_store into the app + route the artifacts upload/download/delete path through the port (the port has no app consumer yet — by design) (evidence: WIRING deep-check residue)
- [SPEC · open] orphan-object cleanup/sweep — on upload, the object is written before the row commits; a failed commit leaves an orphan object. A periodic sweep (mirror the v49 durable-queue recovery pattern) reaps orphans (evidence: ATOMICITY shared-decision in v51 MILESTONE)
- [SPEC · open] cloud-S3 + virtual-host addressing path — only path-style against MinIO is live-verified; AWS S3/R2 (virtual-host, real region endpoints) is a config-only delta but unverified (evidence: live tests cover MinIO path-style only)
- [SPEC · open] surface object-store health() on an /admin health readout (mirror upstream-health-view) (evidence: health() exists but is unconsumed)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · open] an untyped third-party SDK (aioboto3) is made unit-testable by injecting a client_factory (a zero-arg async-ctx callable) so tests pass a botocore-faithful fake, while a SEPARATE skip-gated live suite proves the real wire — the inject-fake + live-gated split keeps the fast lane green without MinIO yet covers the real path (evidence: 15 unit green in test-fast + 4 live green vs real MinIO) [object-store-port]
- [SDD · open] the existing CircuitBreaker is IO-tier-agnostic — it dropped onto a brand-new object-store IO seam unchanged (guard/record_success/on_upstream_error), confirming the breaker is a reusable primitive, not completion-path-specific (evidence: reused verbatim, 0 edits) [object-store-port]
- [ADD · open] running `ruff --fix` on a test file AFTER the tests→build snapshot trips `build_tampered` (even for a cosmetic autofix) — remedy is to re-cross tests→build to re-snapshot, OR run autofix BEFORE crossing; never weaken the test to clear it (evidence: gate PASS attempt burned 1 heal, cleared by re-cross) [object-store-port]
