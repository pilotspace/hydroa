# PLAN: /v1/fine_tuning/jobs brokered to BYOK provider

slug: finetune-broker · created: 2026-07-24 · stage: production
milestone: managed-rag-finetune
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: finetune-broker — OpenAI-wire /v1/fine_tuning/jobs (create/list/get/cancel + events) brokered to the tenant's OWN BYOK provider credential, with a tenant-scoped job store and a fail-closed confused-deputy boundary on the provider credential.
Framings weighed: broker-with-local-job-store, refresh-on-read (chosen — mirrors the batches/ job-store precedent, no background worker to secure, the provider stays the source of truth) · transparent pass-through proxy (rejected — no tenant-scoped job rows → no 404-never-leak, no registry extension point, no local audit of what credential served what) · background-poller worker (rejected — a long-lived credential-holding daemon widens the confused-deputy surface; deferred until a real need).
Must:
<must>
  - M1 `POST /v1/fine_tuning/jobs` body `{model, training_file, validation_file?, suffix?, hyperparameters?}` returns the OpenAI FineTuningJob wire object: `id:"ftjob-<hex>"`, `object:"fine_tuning.job"`, `model`, `status`, `training_file`, `created_at` (int), `fine_tuned_model` (null until succeeded), `error` (null|object). The row is persisted FIRST, then submitted inline to the provider (bounded timeout, NO retry — submit is non-idempotent); submit success → `status:"queued"` + provider_job_id stored; submit failure → the row persists with `status:"failed"`, `error.code:"finetune_provider_unreachable"` (honest degradation, mirrors batches' no_batch_processor path — never a silent half-write).
  - M2 The outbound submit/poll/cancel authenticates with the credential of the AUTHENTICATED tenant ONLY — resolved by `(authz.tenant_id, provider)` through the ONE shared resolver seam (`app.state.tenant_credential_resolver`, fail-closed `ProviderKeyMissing` → 402). Platform-key fallback is DISABLED for fine-tuning (training data must never ride the platform credential). The provider is derived server-side from `model` (v1: constant `"openai"` via `FINETUNE_CAPABLE_PROVIDERS`) — never read from a client-supplied field. The plaintext credential exists only in the outbound-call frame: never persisted on any finetune row, never logged, never echoed in a response; decrypt failures re-raise `from None` (v22 floor).
  - M3 `GET /v1/fine_tuning/jobs` (paginated `limit`/`after`, newest-first) and `GET /v1/fine_tuning/jobs/{id}` are tenant-scoped: `tenant_id` filtered in the SAME query that checks existence.
  - M4 `POST /v1/fine_tuning/jobs/{id}/cancel` on a non-terminal job cancels at the provider (bounded timeout, 1 idempotent retry) then persists `status:"cancelled"`; a never-submitted job cancels locally with no outbound call.
  - M5 `GET /v1/fine_tuning/jobs/{id}/events` returns `{object:"list", data:[{object:"fine_tuning.job.event", id, level, message, created_at}]}` — broker-recorded lifecycle events (created · submitted · status transitions · failure), tenant-scoped like M3.
  - M6 Schema: NEW tables `finetune_jobs` + `finetune_job_events` (mirror `batch_jobs`/`batch_job_items` shape; columns in §3) via ONE additive migration; BOTH tables land in BOTH manifests — `tests/migrations/test_migrations.py::EXPECTED_TABLES` AND the guardrails NOT-IN inventory in `tests/guardrails/test_guardrails_core.py`. No credential-bearing column exists on either table.
  - M7 Refresh-on-read: `GET .../{id}` on a non-terminal job with a provider_job_id polls the provider (timeout + ≤2 idempotent retries + per-tenant circuit breaker); ANY inability to poll — provider failure OR credential-resolution failure (e.g. the tenant rotated/deleted its BYOK key after create) — serves the last-known LOCAL state (stale-ok, never 5xx and never 402 on a read; writes/cancel still fail closed). Terminal transitions commit via compare-and-set (`UPDATE … WHERE status NOT IN (terminal set)`); on a WINNING CAS to `succeeded` the broker persists `fine_tuned_model` verbatim and invokes the optional `app.state.finetune_completion_listener` (default None) EXACTLY ONCE even under concurrent reads — the finetune-model-registry extension point; registry plugs in WITHOUT re-freezing this contract.
  - M8 `/v1/files` additively accepts `purpose:"fine-tune"` (extends `_SUPPORTED_PURPOSES` + the ERR_FILE_PURPOSE_UNSUPPORTED message); existing purposes byte-identical.
  - M9 Byte-identical default path: a request not hitting /v1/fine_tuning/* or purpose:"fine-tune" engages ZERO new plumbing; the new /v1 routes ride the existing Envoy ext_authz `/v1/` prefix rule (no Envoy change).
</must>
Reject:
<reject>
  - training_file OR validation_file absent OR another tenant's OR wrong purpose (one UNIFORM response across ALL cases and BOTH fields — no enumeration oracle; validation_file is the same T2 deputy surface as training_file) -> "ERR_FINETUNE_TRAINING_FILE_INVALID" (422)
  - model that resolves to no finetune-capable provider -> "ERR_FINETUNE_MODEL_UNSUPPORTED" (422)
  - tenant has no enabled credential for the finetune provider (fallback never consulted; ZERO outbound IO occurs) -> "ERR_PROVIDER_KEY_MISSING" (402, existing spec)
  - unknown job id OR another tenant's job id (get/cancel/events; byte-identical status+body for both) -> "ERR_FINETUNE_JOB_NOT_FOUND" (404)
  - cancel on a terminal job (succeeded|failed|cancelled) -> "ERR_FINETUNE_JOB_NOT_CANCELLABLE" (409)
  - provider unreachable during cancel (status unchanged — cancel stays retryable) -> "ERR_FINETUNE_PROVIDER_UNREACHABLE" (502)
  - missing/invalid/expired API key -> existing 401 auth codes (AUTH_KEY_INVALID / AUTH_KEY_EXPIRED), unchanged
</reject>
After:
<after>
  - A tenant-scoped `finetune_jobs` row exists carrying provider_job_id + OpenAI-vocab status (`validating_files|queued|running|succeeded|failed|cancelled`), with lifecycle events in `finetune_job_events`; the tenant's plaintext credential is recoverable from NO persisted row, log line, or response body; another tenant observing any surface cannot distinguish this job from a nonexistent one.
</after>
Boundary: training_file arrives as the OpenAI wire id `file-<hex>` (files/wire_id.py `parse_wire_id` — malformed wire id folds into the SAME uniform ERR_FINETUNE_TRAINING_FILE_INVALID) · provider status strings map onto the exact OpenAI 6-state job vocabulary (unknown provider statuses fold to `running`, never crash).
<assumptions>
  ⚠ Provider-set v1 = {"openai"} with server-side derivation (`FINETUNE_CAPABLE_PROVIDERS`) — lowest confidence because azure/vertex BYOK tenants may expect fine-tuning through their provider's differing wire; if wrong: those tenants get 422 ERR_FINETUNE_MODEL_UNSUPPORTED until an additive provider adapter lands (extension is additive — the frozenset + port are the seam; no contract re-freeze).
  ⚠ Broker writes NO usage record for the training job itself (BYOK: the provider bills the tenant's own account directly; markup applies to the resulting model's inference pricing, owned by finetune-model-registry) — if wrong: a billing gap fixable additively via the shared rate-card resolver, but it re-opens the milestone billing decision.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
POST /v1/fine_tuning/jobs   body: { model, training_file, validation_file?, suffix?, hyperparameters? }
  200 -> { id:"ftjob-<hex>", object:"fine_tuning.job", model, status, training_file,
           validation_file, fine_tuned_model:null, error:null|{code,message}, created_at,
           finished_at:null, hyperparameters }
  422 -> ERR_FINETUNE_TRAINING_FILE_INVALID | ERR_FINETUNE_MODEL_UNSUPPORTED
  402 -> ERR_PROVIDER_KEY_MISSING   401 -> existing auth codes
  (all error codes render via the existing core/error_catalog.py ErrorSpec problem+json
   envelope: {type,title,status,code} — [OBSERVED] live in the red-run evidence)
GET  /v1/fine_tuning/jobs?limit&after      -> 200 { object:"list", data:[FineTuningJob], has_more }
GET  /v1/fine_tuning/jobs/{id}             -> 200 FineTuningJob | 404 ERR_FINETUNE_JOB_NOT_FOUND
POST /v1/fine_tuning/jobs/{id}/cancel      -> 200 FineTuningJob(status:"cancelled")
  | 404 ERR_FINETUNE_JOB_NOT_FOUND | 409 ERR_FINETUNE_JOB_NOT_CANCELLABLE
  | 502 ERR_FINETUNE_PROVIDER_UNREACHABLE (status unchanged)
GET  /v1/fine_tuning/jobs/{id}/events      -> 200 { object:"list",
  data:[{ id:"ftevent-<hex>", object:"fine_tuning.job.event", level, message, created_at }] }
  | 404 ERR_FINETUNE_JOB_NOT_FOUND

Schema (ONE additive migration; NO credential-bearing column on either table):
  finetune_jobs: id UUID pk gen_random_uuid · tenant_id UUID NOT NULL · key_id UUID NOT NULL
    · provider TEXT NOT NULL · provider_job_id TEXT NULL (set on submit success)
    · model TEXT NOT NULL · training_file_id UUID NOT NULL · validation_file_id UUID NULL
    · suffix TEXT NULL · hyperparameters JSONB NULL
    · status TEXT NOT NULL DEFAULT 'validating_files'  (validating_files|queued|running|succeeded|failed|cancelled — OpenAI exact 6-state vocab)
    · fine_tuned_model TEXT NULL  (the registry EXTENSION POINT — persisted verbatim at succeeded)
    · error TEXT NULL · created_at/updated_at timestamptz · finished_at timestamptz NULL
    · Index (tenant_id, created_at DESC)  — mirrors BatchJobRow (batches/infrastructure/orm.py)
  finetune_job_events: id UUID pk · job_id UUID NOT NULL REFERENCES finetune_jobs(id) ON DELETE CASCADE
    · tenant_id UUID NOT NULL · level TEXT NOT NULL DEFAULT 'info' · message TEXT NOT NULL
    · data JSONB NULL · created_at timestamptz · Index (job_id, created_at)
  Access pattern: every read/write filters tenant_id in the SAME query that checks existence
    (repository idiom; unknown vs cross-tenant byte-identical 404). Both tables → BOTH manifests
    (tests/migrations EXPECTED_TABLES + guardrails NOT-IN inventory).

BYOK brokering flow (which creds · how resolved · how the confused-deputy surface is closed):
  authenticate (Bearer sk-… → AuthzResult, mirrors batches _authenticate)
  → validate training_file AND (when supplied) validation_file through the ONE shared check:
    parse_wire_id (files/wire_id.py) → SELECT … WHERE id=:fid AND tenant_id=:authz.tenant_id
    AND purpose='fine-tune' — same query per field, one uniform 422 on any miss of either field
  → derive provider server-side: FINETUNE_CAPABLE_PROVIDERS = frozenset({"openai"}) (v1);
    never from a client field
  → resolve credential: app.state.tenant_credential_resolver.resolve(authz.tenant_id, provider)
    (CachedTenantCredentialResolver → DbTenantProviderKeyStore, Fernet, fail-CLOSED
    ProviderKeyMissing → 402). Platform fallback (resolve_provider_credential's
    platform_fallback composition / PlatformCredentialFallbackService) is NOT wired here —
    training data never rides the platform credential.
  → persist row → submit via FinetuneProviderPort (protocol: submit/poll/cancel), injected at
    app.state.finetune_provider; real impl OpenAIFinetuneClient (httpx: connect/read timeouts,
    submit=1 attempt no-retry non-idempotent, poll ≤2 retries, cancel 1 retry, per-tenant
    circuit breaker mirroring proxy/infrastructure/circuit_breaker.py + upstream_retry.py).
  Credential lifetime: plaintext exists only inside the port-call frame; never persisted,
    never logged, never in a response; decrypt errors re-raise `from None` (v22 floor).

THREAT MODEL (named; security HARD-STOP floor):
  Asset: every tenant's Fernet-at-rest provider credential + tenant training data (file bytes).
  Deputy: the broker — the one component authorized to decrypt ANY tenant's credential and to
    read tenant files, acting on behalf of a caller.
  T1 confused deputy (credential): caller A induces the broker to sign an outbound call with
    B's (or the platform's) credential. CLOSED: resolution keyed ONLY by authz.tenant_id from
    the authenticated key — no request field names a tenant, key, or provider; platform
    fallback disabled; own-key-or-402.
  T2 confused deputy (training data): caller A attaches B's file_id — as training_file OR
    validation_file — so B's data is shipped to A's provider account. CLOSED: BOTH file-reference
    fields validate through the ONE shared query (tenant_id + purpose filtered in the SAME
    existence query); absent/cross-tenant/wrong-purpose byte-identical for both fields
    (anti-enumeration; advisor pressure-test finding A1).
  T3 job-id enumeration oracle: 404 vs 403 alternation reveals foreign job existence. CLOSED:
    ERR_FINETUNE_JOB_NOT_FOUND for unknown AND cross-tenant on get/cancel/events, byte-identical.
  T4 credential exfiltration at rest / in telemetry: CLOSED: no credential column, no logging of
    the resolved credential, InvalidToken chains stripped `from None`, error bodies carry only
    catalog codes.
  T5 provider-response injection: provider-controlled strings (fine_tuned_model, error, status)
    are stored as opaque TEXT and folded to the closed 6-state vocab; provider_job_id is
    shape-validated (`^[A-Za-z0-9:_.-]+$`) at submit-response time BEFORE it may ever be
    interpolated into an outbound poll/cancel URL path — a hostile provider response cannot
    redirect subsequent calls; nothing provider-controlled reaches queries or auth material.
  T6 resource abuse via unbounded outbound IO: CLOSED: every provider call timeout-bounded,
    retry only idempotent ops, per-tenant breaker (CR-1 precedent).

Extension point (finetune-model-registry — NO re-freeze of this contract):
  (a) `fine_tuned_model` nullable column persisted at observed `succeeded`;
  (b) optional listener port `FinetuneCompletionListener.on_succeeded(job)` consulted at
      app.state.finetune_completion_listener (default None → byte-identical) — registry
      registers the catalog ModelRow + pricing snapshot behind this port, additively.

Anchors (the Contract may cite ONLY these — all [OBSERVED] this session):
  batches/api/router.py::_authenticate · batches/infrastructure/orm.py::BatchJobRow ·
  proxy/infrastructure/cached_tenant_credential_resolver.py::CachedTenantCredentialResolver.resolve ·
  proxy/infrastructure/tenant_provider_key_store.py (_encrypt/_decrypt Fernet floor) ·
  proxy/domain/provider_credentials.py::BYOK_PROVIDERS/ProviderKeyMissing ·
  proxy/application/use_cases.py::resolve_provider_credential (platform_fallback composition —
    deliberately NOT reused) · proxy/application/platform_fallback.py::PlatformCredentialFallbackService ·
  files/wire_id.py::to_wire_id/parse_wire_id · files/api/router.py::_SUPPORTED_PURPOSES/_not_found ·
  core/error_catalog.py::ErrorSpec (BATCH_JOB_NOT_FOUND/FILE_NOT_FOUND precedent) ·
  proxy/infrastructure/circuit_breaker.py · proxy/infrastructure/upstream_retry.py ·
  tests/migrations/test_migrations.py::EXPECTED_TABLES · tests/guardrails/test_guardrails_core.py
    (pg_tables NOT-IN manifest #2) · main.py router wiring (include_router(batch_router/files_router)) ·
  infra/envoy/envoy.yaml `/v1/` prefix rule (covers the new routes; no Envoy change).
```

Target (measurable): all §4 tests green (16/16, currently 16 red for missing-impl); the 3 adversarial tests pass — cross-tenant get/cancel/events 404 byte-identical to unknown-id (status AND body equality), outbound Authorization carries the caller-tenant's secret in 100% of recorded fake-port calls and the foreign secret in 0%, and the plaintext secret appears 0 times across job rows (SQL sweep), response bodies, and captured logs; migration suite green with both manifests updated; batches + files_uploads_api suites stay green (regression floor); `make ci` Pyright strict clean.
Status: FROZEN @ v1 — approved by Tin
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/finetune/` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/files/api/router.py` · `apps/gateway/migrations/versions/` · `apps/gateway/tests/finetune_broker/` · `apps/gateway/tests/migrations/test_migrations.py` · `apps/gateway/tests/guardrails/test_guardrails_core.py`
Regression floor: `apps/gateway/tests/batches/` · `apps/gateway/tests/files_uploads_api/` · `apps/gateway/tests/migrations/` · `apps/gateway/tests/guardrails/`
Persona: `.add/personas/appsec-engineer.md`

Least-sure flag surfaced at freeze: [contract] Provider-set v1 = {"openai"} derived server-side — I trust this least because azure/vertex BYOK tenants may expect fine-tuning via their own provider's wire and get 422 ERR_FINETUNE_MODEL_UNSUPPORTED; the FINETUNE_CAPABLE_PROVIDERS frozenset + FinetuneProviderPort are the additive escape hatch, but if Tin wants azure at v1 the contract shape (provider derivation, port signature) must be decided BEFORE the freeze, not after.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_create_job_returns_openai_wire: upload fine-tune file, fake provider port accepts / POST /v1/fine_tuning/jobs / 200 ftjob-* fine_tuning.job wire, status queued, provider submitted once · covers: M1, M2
  - test_create_submit_failure_persists_failed_job: fake port raises / create / 200 with status "failed" + error code finetune_provider_unreachable — row persisted, never a silent half-write · covers: M1
  - test_create_rejects_unsupported_model: model resolving to no capable provider / create / 422 · covers: R:ERR_FINETUNE_MODEL_UNSUPPORTED
  - test_create_training_file_uniform_reject: absent id vs CROSS-TENANT file vs wrong-purpose file, as training_file AND AGAIN as validation_file (own valid training_file alongside — the A1 deputy bypass) / create ×6 / ALL byte-identical 422 (status+body) AND zero outbound submit · covers: R:ERR_FINETUNE_TRAINING_FILE_INVALID
  - test_create_no_credential_402_zero_outbound: resolver raises ProviderKeyMissing / create / 402 + fake port never called (platform fallback never consulted) · covers: M2, R:ERR_PROVIDER_KEY_MISSING
  - test_outbound_uses_own_tenant_credential_only [ADVERSARIAL confused-deputy]: recording resolver hands DISTINCT secrets to tenants A and B; both create jobs / assert every recorded port call for A carried A's secret, B's carried B's, and neither secret crosses; secrets absent from both response bodies · covers: M2
  - test_get_cross_tenant_404_byte_identical [ADVERSARIAL enumeration-oracle]: A creates job; B GETs A's id AND a random unknown id / both 404 with byte-identical body; same for cancel and events · covers: M3, R:ERR_FINETUNE_JOB_NOT_FOUND
  - test_list_tenant_scoped: A and B each create / A's list never contains B's job and vice-versa; wire {object:"list", data, has_more} · covers: M3
  - test_cancel_non_terminal_job: create (queued) / cancel / 200 status cancelled + provider cancel called · covers: M4
  - test_cancel_terminal_conflict: job driven terminal via fake poll / cancel / 409 AND status unchanged · covers: R:ERR_FINETUNE_JOB_NOT_CANCELLABLE
  - test_cancel_provider_unreachable_502_status_unchanged: fake port cancel raises / cancel / 502 AND status still queued (cancel stays retryable) · covers: R:ERR_FINETUNE_PROVIDER_UNREACHABLE
  - test_missing_or_invalid_key_401: no key + invalid key on the new routes / 401 (existing auth contract unchanged) · covers: R:401-auth
  - test_events_recorded_and_scoped: create / GET events / list wire with created+submitted events; B fetching A's events → 404 · covers: M5
  - test_succeeded_persists_model_and_fires_listener: fake poll returns succeeded+fine_tuned_model / GET job / fine_tuned_model on wire + recorded listener invoked once (extension point) · covers: M7
  - test_credential_never_persisted [ADVERSARIAL leak-sweep]: after create, SQL-sweep finetune_jobs+finetune_job_events rows / plaintext secret occurs 0 times; response bodies clean · covers: M2, M6
  - test_files_purpose_fine_tune_accepted: upload purpose="fine-tune" / 200 File object purpose preserved; purpose "bogus" still 422 · covers: M8
</test_plan>

Prose build-guidance (not gated): the migration's manifest edits (EXPECTED_TABLES + guardrails NOT-IN) are proven by the existing `tests/migrations` + `tests/guardrails` suites in the regression floor, not duplicated here (M6; M9's byte-identical default path likewise rides the regression floor — batches + files_uploads_api suites stay green); refresh-on-read STALE-OK degradation (M7's failure direction: poll failure serves last-known local state, never a 5xx read) is a secondary behavior described here for the builder, not red-gated.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/finetune_broker/` · MUST run red (missing implementation) before Build.

RED-RUN EVIDENCE (2026-07-24, unique DB `gateway_test_ftbroker` on the shared :5433):
```
$ GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test_ftbroker \
    uv run pytest tests/finetune_broker -q
FAILED tests/finetune_broker/test_finetune_broker.py::TestCreateJob::test_create_job_returns_openai_wire
FAILED tests/finetune_broker/test_finetune_broker.py::TestCreateJob::test_create_submit_failure_persists_failed_job
FAILED tests/finetune_broker/test_finetune_broker.py::TestCreateJob::test_create_rejects_unsupported_model
FAILED tests/finetune_broker/test_finetune_broker.py::TestCreateJob::test_create_training_file_uniform_reject
FAILED tests/finetune_broker/test_finetune_broker.py::TestCreateJob::test_create_no_credential_402_zero_outbound
FAILED tests/finetune_broker/test_finetune_broker.py::TestSecurityInvariants::test_outbound_uses_own_tenant_credential_only
FAILED tests/finetune_broker/test_finetune_broker.py::TestSecurityInvariants::test_get_cross_tenant_404_byte_identical
FAILED tests/finetune_broker/test_finetune_broker.py::TestSecurityInvariants::test_credential_never_persisted
FAILED tests/finetune_broker/test_finetune_broker.py::TestJobLifecycle::test_list_tenant_scoped
FAILED tests/finetune_broker/test_finetune_broker.py::TestJobLifecycle::test_cancel_non_terminal_job
FAILED tests/finetune_broker/test_finetune_broker.py::TestJobLifecycle::test_cancel_terminal_conflict
FAILED tests/finetune_broker/test_finetune_broker.py::TestJobLifecycle::test_cancel_provider_unreachable_502_status_unchanged
FAILED tests/finetune_broker/test_finetune_broker.py::TestJobLifecycle::test_missing_or_invalid_key_401
FAILED tests/finetune_broker/test_finetune_broker.py::TestJobLifecycle::test_events_recorded_and_scoped
FAILED tests/finetune_broker/test_finetune_broker.py::TestJobLifecycle::test_succeeded_persists_model_and_fires_listener
FAILED tests/finetune_broker/test_finetune_broker.py::TestFilesFineTunePurpose::test_files_purpose_fine_tune_accepted
16 failed in 13.50s   (16/16 red — full FAILED list retained in the pytest output; second run
                       after adding the R6-502 and 401-auth tests)
Third run (post advisor pressure-test — A1 validation_file leg folded into the uniform-reject
test; suite re-run): `16 failed in 12.27s` — still 16/16 red, same missing-impl reason.
Advisor consulted (add-advisor, propose-plan/pressure-test): verdict AMEND-FIRST — A1
validation_file T2 bypass (applied: §1 R1 + T2 + flow + test), A2 read-path credential-failure
stale-ok (applied: M7), A3 CAS exactly-once listener (applied: M7), T5 provider_job_id
shape-validation (applied). Provider-set {"openai"} + server-side derivation and
refresh-on-read both CONFIRMED as the right frozen shape.
```
Red for the RIGHT reason — first failure (harness sound, implementation absent):
```
AssertionError: training-file upload (purpose=fine-tune) failed — M8 additive purpose missing?
  422: {"type":"about:blank","title":"purpose must be one of: batch, vision, user_data",
        "status":422,"code":"ERR_FILE_PURPOSE_UNSUPPORTED"}
```
(the /v1/fine_tuning/* routes do not exist yet → 404/405 on every route-level assert;
 fixtures signup/login/key-mint all pass — the harness itself is green)
Coverage target: the finetune/ module ≥ 90% branch on the credential-resolution and
tenant-scoping paths; suite-level floor stays the repo's 80% gate.

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
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose broker-with-local-job-store, refresh-on-read; rejected transparent pass-through proxy (rejected — no tenant-scoped job rows → no 404-never-leak, no registry extension point, no local audit of what credential served what) · background-poller worker (rejected — a long-lived credential-holding daemon widens the confused-deputy surface; deferred until a real need).
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · open] A job can reach `succeeded` at the provider yet never be observed (no GET → listener never fires → the ft:* model is never registered); fix belongs to finetune-model-registry (lazy refresh on unknown ft:* at inference) or an additive list-refresh — NOT a broker re-freeze (evidence: advisor pressure-test, 2026-07-24).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
