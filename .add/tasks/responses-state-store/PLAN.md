# PLAN: Stored responses + previous_response_id chaining (tenant-isolated, ZDR-composing)

slug: responses-state-store · created: 2026-07-24 · stage: production · risk: high
sensitivity: security
milestone: api-surface-parity
autonomy: conservative
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Stored Responses state on the frozen /v1/responses wire — `store:true` persistence, `previous_response_id` conversation chaining, GET/DELETE /v1/responses/{id}; a NEW tenant-scoped payload-at-rest store composing with ZDR (fail-closed 403 reject — Tin's recorded decision 2026-07-24), the retention sweeper, and both table manifests. Grounds on responses-api-core's FROZEN §3 "State extension point": `store`/`previous_response_id` are already accepted-shape fields terminating in ERR_RESPONSES_STORE_UNSUPPORTED; this task takes contracted ownership of their ACCEPTANCE — the field names, types, default (`store:false`), and the Response echo fields frozen there are what this task attaches to [OBSERVED: .add/tasks/responses-api-core/PLAN.md §3 FROZEN @ v1].
Framings weighed: materialized-context row store in a new `responses_store/` bounded context mirroring `conversations/` (chosen — each stored row carries the FULL chat-shaped context it was served with, so chaining is ONE tenant-scoped read: single terminal 404, no multi-row torn-chain concurrency, depth/size caps checkable pre-dial; storage O(chain²) accepted, bounded by caps + retention) · walk-the-chain per-turn rows à la conversation_messages (rejected: chain rebuild is a multi-row walk — a cross-tenant/deleted check per hop and a torn-read window per hop on a security-sensitive read path) · reuse conversations/ tables (rejected: different wire lifecycle + different ZDR posture would fork the frozen conversations contract; the milestone glossary keeps Response object a distinct resource).
Must:
<must>
  - M1 store non-stream: POST /v1/responses `{store:true}` → the UNCHANGED core round-trip serves (one CompletionUseCase dial, one usage_records row), THEN exactly one `stored_responses` row is persisted in-request before the 200 returns: `id` = the wire `resp_*` id, `tenant_id`+`key_id` from the authenticated key, `model` = served model, `status`, `previous_response_id` echo, `chain_depth`, `context_messages` = the exact chat-shaped message list sent upstream, `response_body` = the full Response object returned, `usage` metadata; the 200 body echoes `store:true`.
  - M2 store streaming: `{store:true, stream:true}` persists the same row BEFORE the terminal `response.completed` frame is emitted; a persistence failure at that point emits a terminal `response.failed` carrying ERR_RESPONSES_STORE_FAILED instead of a fabricated completion (the one usage row already recorded inside use_case.stream() stands — the provider was served).
  - M3 GET /v1/responses/{id}: Bearer sk-key auth (conversations idiom) → 200 with the persisted `response_body` (id/output/usage byte-equal to what POST returned); NO usage_records write (no provider cost — milestone billing rule).
  - M4 DELETE /v1/responses/{id}: CASCADE delete (Tin's recorded decision 2026-07-24, reversing the drafted disclose-not-cascade): ONE tenant-scoped transaction removes the row AND every descendant reachable via `previous_response_id` (recursive resolution over `ix_stored_responses_tenant_prev`, tenant_id filtered at every hop) → 200 `{id, object:"response.deleted", deleted:true}` (pinned OpenAI wire shape — no count field); a subsequent GET on the id OR any deleted descendant is 404; second DELETE is 404; NO usage_records write. Bound: depth ≤ the 64 chain cap; breadth is the tenant's own row count — no additional bound (explicitly none). Race semantics: the cascade deletes the descendant set visible in its transaction snapshot; a chain-child committed concurrently either lands before the snapshot (deleted) or after (survives with a dangling pointer — its GET serves; chaining onto its deleted ancestor is 404).
  - M5 chaining: `previous_response_id` (with store true OR false — chaining and storing are independent) resolves the previous row via ONE tenant-scoped SELECT (tenant_id in the same query that checks existence — appsec floor), materializes the upstream input as prev.context_messages + prev output-as-assistant-messages + this request's translated input (order asserted), dials the unchanged core seam once, and echoes `previous_response_id`; same-tenant cross-key chaining is allowed (tenant is the isolation boundary; key_id is attribution — conversations precedent).
  - M6 tenant isolation / anti-enumeration: GET, DELETE, and chaining onto an id that is unknown, another tenant's, deleted, or malformed all return the SINGLE terminal 404 ERR_RESPONSE_NOT_FOUND with byte-identical problem+json status+body (sso-oracle lesson: one contracted code, never an alternation); a cross-tenant DELETE leaves the victim row untouched.
  - M7 ZDR fail-closed (Tin's recorded decision 2026-07-24 — the repo-wide raise_if_zdr idiom, LOUD): a `tenants.zdr_enabled=true` tenant's POST with `store:true` is rejected 403 ERR_ZDR_PAYLOAD_BLOCKED PRE-DIAL — fresh per-call is_zdr check as the FIRST gate on the store path (raise_if_zdr ordering rule), upstream never dialed, zero provider cost, zero usage_records rows, zero stored_responses rows; the SAME tenant's `store:false`/store-absent request (no payload at rest) serves 200 normally with its one usage row — loud exactly where payload would rest, byte-identical stateless path otherwise.
  - M8 retention + ZDR sweeper composition: a per-tenant window pass DELETEs `stored_responses` rows older than COALESCE(tenants.retention_window_days, retention_tenant_window_ceiling_days) in bounded batches (compliance_report_runs additive-pass precedent); the per-tenant ZDR purge pass row-DELETEs every stored_responses row of a zdr_enabled tenant each tick (the `_DELETE_CONVERSATIONS_ZDR_TENANT` pattern — self-healing for the flip-ZDR-on-later case; chaining onto a purged id is naturally 404); `stored_responses` lands in BOTH manifests (tests/migrations EXPECTED_TABLES + guardrails NOT-IN list, SANCTIONED EDIT rows).
  - M9 byte-identical default path: a POST with no `store:true` and no `previous_response_id` performs ZERO stored_responses reads or writes (no row, no SELECT); the GET/DELETE routes add nothing to the chat or stateless-responses path.
</must>
Reject:
<reject>
  - `previous_response_id` unknown / cross-tenant / deleted / malformed -> "ERR_RESPONSE_NOT_FOUND" (404; pre-dial — upstream never dialed, no usage row; byte-identical body across all four causes)
  - chain onto a row with chain_depth >= 64 -> "ERR_RESPONSES_CHAIN_TOO_DEEP" (400; pre-dial, no usage row)
  - materialized context (prev context + prev output + new input) serialized > 1 MiB -> "ERR_RESPONSES_CONTEXT_TOO_LARGE" (413; pre-dial, no usage row)
  - `store: true` from a zdr_enabled tenant -> "ERR_ZDR_PAYLOAD_BLOCKED" (403; existing repo-wide code, fresh is_zdr check FIRST on the store path; pre-dial — upstream never dialed, no usage row, no stored row; the underlying completion does NOT execute)
  - GET/DELETE without a valid Bearer sk-key -> existing 401 posture "ERR_AUTH_INVALID_KEY" (uniform-401 milestone rule; no new code)
  - stored_responses persistence failure after a served non-stream round-trip -> "ERR_RESPONSES_STORE_FAILED" (500 problem+json; the one usage row stands — provider cost was incurred; nothing half-written: the insert is atomic in its transaction)
</reject>
After:
<after>
  - A tenant can round-trip store→GET→chain→DELETE entirely inside its own rows; no request, id probe, or chain hop can observe another tenant's rows; a ZDR tenant never has response payload at rest (the write path REJECTS 403 pre-dial AND the sweeper row-deletes any flip-ZDR-on-later leftovers); a DELETE erases the whole descendant chain in one transaction; every stored row dies by user DELETE (or its ancestor's cascade), retention window, or ZDR purge — and the stateless default path is byte-identical to responses-api-core's frozen behavior.
</after>
Boundary: `previous_response_id` arrives as a JSON string on the frozen wire (non-string types already die in core's ERR_PAYLOAD_INVALID validation [OBSERVED core §3]; any string VALUE, well-formed `resp_*` or garbage, terminates 404 — no format-validation oracle) · GET/DELETE path id likewise any string · stored context is chat-shaped messages (the internal seam shape), never the Responses input-item shape.
<assumptions>
  ⚠ [contract] Cascade-vs-concurrent-create race: a chain-child COMMITTED after the cascade's transaction snapshot survives as a dangling-pointer orphan (its GET serves; chaining onto the deleted ancestor 404s) — lowest confidence because single-transaction snapshot semantics are the honest bound, but a reviewer may expect "no survivors ever"; if wrong: a post-cascade second sweep or SERIALIZABLE isolation on DELETE, contained change.
  ⚠ [spec] MILESTONE.md Exit criterion still reads "a ZDR tenant's stored response is metadata-only" — SUPERSEDED by Tin's recorded decision (2026-07-24, AskUserQuestion): ZDR store:true → loud 403 fail-closed. Milestone doc is orchestrator-owned; flagged so verify reads THIS contract, not the stale criterion (also logged in §7 Spec delta); if unfixed: a verifier chasing the old criterion burns a cycle.
  - DECIDED (Tin, 2026-07-24): ZDR = fail-closed 403 ERR_ZDR_PAYLOAD_BLOCKED pre-dial (repo-wide raise_if_zdr idiom; whole request rejected BEFORE the completion dial, zero provider cost) — the drafted metadata-only branch is REMOVED.
  - DECIDED (Tin, 2026-07-24): DELETE = cascade-delete descendants (tenant-scoped, single transaction) — the drafted disclose-not-cascade residue is REMOVED.
  - DECIDED (Tin, 2026-07-24, by approving flag 3): `store` default stays FALSE (core's frozen divergence from OpenAI's server-default true; privacy-first for a governance gateway).
  - Materialized context costs O(chain²) storage vs walk-the-chain O(n) — [DERIVED] acceptable under the 64-depth/1 MiB caps + retention sweep; if wrong: internal storage-strategy swap behind the same wire, contract unmoved.
  - Retiring the one superseded core test (§3 Ownership-transfer note) is pre-authorized by core's frozen §3 transfer clause — [OBSERVED] — not a tamper; if the tripwire disagrees: re-cross tests→build per the tamper-ordering lesson.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
POST /v1/responses  (frozen core wire, responses-api-core §3 — THIS task flips ONLY the
                     ERR_RESPONSES_STORE_UNSUPPORTED terminal into acceptance)
  body: { …frozen core fields…, store?: bool (=false), previous_response_id?: str }
  200 -> the frozen core Response, `store` and `previous_response_id` echoed truthfully;
         store:true ⇒ one stored_responses row persisted in-request (stream: before the
         terminal response.completed frame); previous_response_id ⇒ upstream context =
         prev.context_messages + prev output-as-assistant-messages + new translated input.
  404 -> problem+json { code: "ERR_RESPONSE_NOT_FOUND" }         (prev id unknown|cross-tenant|deleted|malformed — byte-identical)
  400 -> problem+json { code: "ERR_RESPONSES_CHAIN_TOO_DEEP" }   (prev.chain_depth >= 64)
  413 -> problem+json { code: "ERR_RESPONSES_CONTEXT_TOO_LARGE" }(materialized context > 1 MiB serialized)
  403 -> problem+json { code: "ERR_ZDR_PAYLOAD_BLOCKED" }        (store:true from a zdr_enabled tenant —
         EXISTING repo-wide ErrorSpec; fresh is_zdr check FIRST on the store path, raise_if_zdr
         ordering rule; the completion does NOT execute — Tin's recorded decision 2026-07-24)
  500 -> problem+json { code: "ERR_RESPONSES_STORE_FAILED" }     (persist failed post-serve; usage row stands)
  All rejects pre-dial except STORE_FAILED: upstream never dialed, zero usage rows.
  Evaluation ORDER (anti-enumeration, HARD): the 400/413 chain rejects evaluate ONLY a
  row already resolved by the ONE tenant-scoped SELECT — a foreign, unknown, deleted, or
  malformed id can structurally NEVER reach them; it terminates 404 first. Tenant B chaining
  onto A's depth-capped row is 404, byte-identical to unknown-id (red-gated). The 403 ZDR
  gate keys ONLY on the CALLER's own zdr_enabled flag (no foreign-row knowledge involved).
  DELETE semantics (Tin's recorded decision 2026-07-24): CASCADE — one tenant-scoped
  transaction removes the row and every descendant reachable via previous_response_id;
  response body stays the pinned OpenAI shape {id, object:"response.deleted", deleted:true}
  (no count field); bound = the 64-depth cap, breadth = the tenant's own rows (explicitly no
  additional bound); concurrent chain-children commit before the snapshot (deleted) or after
  (dangling-pointer orphan: GET serves, chaining onto the deleted ancestor 404s).

GET /v1/responses/{response_id}     auth: Bearer sk-key (conversations idiom)
  200 -> the persisted response_body verbatim (payload-NULL rows cannot exist — ZDR writes
         are rejected 403 and the ZDR sweep row-deletes; no metadata-only branch)
  404 -> problem+json { code: "ERR_RESPONSE_NOT_FOUND" }   (unknown|cross-tenant — byte-identical)
  NO usage_records write.

DELETE /v1/responses/{response_id}  auth: Bearer sk-key
  200 -> { id: "<resp_*>", object: "response.deleted", deleted: true }   (pinned wire shape;
         CASCADE hard-delete of the row + all tenant-scoped descendants in ONE transaction,
         tenant_id filtered at the root statement AND every recursive hop; second call 404)
  404 -> problem+json { code: "ERR_RESPONSE_NOT_FOUND" }   (unknown|cross-tenant|already-deleted)
  NO usage_records write.

Schema: NEW table `stored_responses` (migration + __table_args__ indexes BOTH — v30 lesson):
  id TEXT PK ("resp_*" wire id) · tenant_id UUID NOT NULL · key_id UUID NOT NULL ·
  model TEXT NOT NULL · status TEXT NOT NULL · previous_response_id TEXT NULL (bare pointer,
  NO FK — a deleted parent leaves an honest dangling echo, never mutates history) ·
  chain_depth INT NOT NULL DEFAULT 0 · context_messages JSONB NOT NULL · response_body JSONB
  NOT NULL (payload columns NOT NULL — the metadata-only state class no longer exists) ·
  usage JSONB NOT NULL · created_at timestamptz NOT NULL DEFAULT now() ·
  INDEX ix_stored_responses_tenant_created (tenant_id, created_at DESC) ·
  INDEX ix_stored_responses_tenant_prev (tenant_id, previous_response_id) (cascade resolution).
  Every read/write filters tenant_id in the SAME statement that checks existence.
  Two-manifest rule: table added to tests/migrations EXPECTED_TABLES AND the guardrails
  NOT-IN list (SANCTIONED EDIT rows citing this §3).

Retention/ZDR composition (additive passes, compliance_report_runs precedent — never a
  rewrite of the frozen sweeper contract): window pass DELETE via
  COALESCE(tenants.retention_window_days, :operator_ceiling_days) bounded batches;
  ZDR pass per-tenant bounded row-DELETE of every stored_responses row while zdr_enabled
  (the _DELETE_CONVERSATIONS_ZDR_TENANT pattern — idempotent, self-healing every tick,
  covers flip-ZDR-on-later; no scrub/UPDATE variant exists).
  Additive SQL lives inside retention_sweep.py ONLY — stored_responses does NOT join
  NEW_PAYLOAD_TABLES; retention_policy.py is OUT of scope (compliance_report_runs precedent;
  scope_violation trap defused). Dependency wiring lives in responses_store/ or
  responses_router.py — proxy/api/deps.py is OUT of scope.

Ownership-transfer note (pre-authorized by responses-api-core §3 "State extension point"):
  tests/responses_api_core/test_responses_api_core.py::test_reject_store_true_and_previous_response_id
  is SUPERSEDED by this contract — this task retires that ONE test under the transfer clause
  (declared here so the tamper tripwire reads it as contracted, never silent). Every other
  core test is this task's regression floor and must stay green.

Envoy: GET/DELETE ride the existing `/v1/` ext_authz route (core §3: "/v1/* → ext_authz
  enabled") — zero infra change. No new outbound IO anywhere in this task (DB + the existing
  breaker-wrapped upstream only) ⇒ no new timeout/retry/breaker surface; sweeper passes are
  bounded-batch fail-open per the existing discipline.
```

Anchors (the Contract may cite ONLY these — all [OBSERVED] this session; the parent cited as its FROZEN PLAN, never built code):
`.add/tasks/responses-api-core/PLAN.md §3` (frozen wire + State extension point + error posture) · `gateway/conversations/infrastructure/repository.py::ConversationRepository.get_by_id/.soft_delete` (tenant-filter-in-same-query + None→404 idiom) · `gateway/conversations/api/router.py::_authenticate` (Bearer sk-key → AuthzResult{tenant_id,key_id}) · `gateway/tenants/application/retention_policy.py::is_zdr/raise_if_zdr` (fresh per-call ZDR read; NEW_PAYLOAD_TABLES vocabulary) · `gateway/usage/application/retention_sweep.py::RetentionSweeper._sweep_new_payload_window_pass/._sweep_zdr_purge_pass/._delete_batched_extra` (additive-pass seam) · `gateway/core/error_catalog.py::ErrorSpec` (+ `ZDR_PAYLOAD_BLOCKED` posture precedent) · `gateway/core/errors.py::ProblemError` · `gateway/main.py` (router registration + ORM side-effect import point) · `apps/gateway/tests/migrations/test_migrations.py::EXPECTED_TABLES` · `apps/gateway/tests/guardrails/test_guardrails_core.py` (NOT-IN manifest) · `apps/gateway/tests/responses_api_core/conftest.py::FakeCompletionUpstream/FakeUsageRecorder` (app.state injection seam the red suite mirrors).

Target (measurable): all 20 §4 tests green (20 red today); cross-tenant vs unknown-id byte-identity (status+body) asserted in-suite for GET, DELETE, and chaining; 0 usage_records writes asserted on GET/DELETE and every pre-dial reject; upstream.calls==0 asserted on every pre-dial reject; regression floor green = tests/responses_api_core/ (minus the one superseded test) + tests/proxy/ + tests/retention_zdr/ + tests/conversations/; `make ci` Pyright strict clean; DUAL adversarial security verify recorded in the GATE RECORD (milestone Exit criterion).
Status: FROZEN @ v1 — approved by Tin Dang
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/responses_store/` · `apps/gateway/src/gateway/proxy/api/responses_router.py` · `apps/gateway/src/gateway/proxy/infrastructure/openai_responses_ingress.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/usage/application/retention_sweep.py` · `apps/gateway/migrations/versions/` · `apps/gateway/tests/migrations/test_migrations.py` · `apps/gateway/tests/guardrails/test_guardrails_core.py` · `apps/gateway/tests/responses_api_core/test_responses_api_core.py` · `apps/gateway/tests/responses_state_store/`
Regression floor: `apps/gateway/tests/responses_api_core/` (minus the contracted superseded test) + `apps/gateway/tests/proxy/` + `apps/gateway/tests/retention_zdr/` + `apps/gateway/tests/conversations/` — run before the gate.
Persona (optional): `appsec-engineer` (tenant-filter-in-same-query · byte-identical 404 on both probe directions · both failure directions verified) with backend-architect seam discipline.

Strategy (SOFT, ordered): 1) migration + `responses_store/infrastructure/orm.py` (StoredResponseRow) + BOTH manifest SANCTIONED EDITs in the same batch (cross-manifest-drift lesson); 2) `responses_store/infrastructure/repository.py` — StoredResponseRepository (create/get/delete; tenant_id in every statement; is_zdr branch at create) + `responses_store/application/chaining.py` (materialize + depth/size caps, pure functions); 3) `responses_store/api/router.py` GET/DELETE (conversations router shape) + main.py registration + ORM side-effect import; 4) hook POST acceptance in `responses_router.py`/ingress — chain-resolve pre-dial, persist post-serve (stream: persist before the terminal frame; the stream generator uses its OWN session via session_factory — the request session is closed by then, known trap); 5) sweeper additive passes; 6) drive §4 green; retire the superseded core test citing this §3. Waves note: BUILD starts only after responses-api-core merges (its router/ingress files are this task's hook points).
Known-problem fixes: shared :5433 postgres → unique GATEWAY_TEST_DATABASE_URL per run · fixed-sleep flake lesson → poll-until-present for any async assert · scope anchor at build-entry (`add.py phase build` after any scope edit).

Least-sure flag surfaced at freeze: [contract] the cascade-vs-concurrent-create race semantics — a chain-child committed after the cascade's snapshot survives as a dangling-pointer orphan (honest single-transaction bound); if "no survivors ever" is required: post-cascade second sweep or SERIALIZABLE DELETE, a contained change. (The two prior top flags were DECIDED by Tin 2026-07-24: ZDR → loud 403 fail-closed; DELETE → cascade-delete descendants.)

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_store_true_persists_row_and_echoes: POST store:true → 200 echo store:true; stored_responses row (tenant/key/model/context/response_body/usage) exists; exactly 1 usage row · covers: M1
  - test_store_absent_writes_nothing_default_path: plain POST → 200; zero stored_responses rows · covers: M9
  - test_store_true_streaming_persists_before_completed: stream drain → terminal response.completed; row exists with full response_body · covers: M2
  - test_get_returns_stored_response: GET after store → 200 body id/output/usage byte-equal to POST's; usage row count unchanged by GET · covers: M3
  - test_delete_cascades_descendants_then_get_404: store turn-1→turn-2 chain; DELETE turn-1 → pinned {object:"response.deleted",deleted:true}; BOTH rows absent in DB; GET turn-1 AND turn-2 404 ERR_RESPONSE_NOT_FOUND; second DELETE 404; usage rows unchanged · covers: M4
  - test_chaining_materializes_context_in_order: store turn-1 then POST previous_response_id → fake upstream last_payload messages = turn-1 input + turn-1 assistant output + new input, in order; echo previous_response_id · covers: M5
  - test_chain_with_store_false_reads_but_persists_nothing: chain + store:false → 200 served; still exactly 1 stored row (turn-1's) · covers: M5, M9
  - test_cross_tenant_get_and_delete_404_byte_identical: tenant B GET+DELETE tenant A's id → 404 ERR_RESPONSE_NOT_FOUND; status AND body byte-identical to unknown-id resp_ffffffffffff; A's row still present after B's DELETE · covers: M6
  - test_cross_tenant_chain_probe_404_no_dial: tenant B POST previous_response_id=A's id → 404 ERR_RESPONSE_NOT_FOUND byte-identical to unknown-id chain; upstream.calls==0; 0 usage rows for B · covers: M6, R:ERR_RESPONSE_NOT_FOUND
  - test_cross_tenant_capped_row_still_404: tenant B chains onto A's SEEDED depth-64 row → 404 ERR_RESPONSE_NOT_FOUND byte-identical to unknown-id — never 400 (the evaluation-order oracle closure; a tenant-last implementation fails here) · covers: M6, R:ERR_RESPONSE_NOT_FOUND
  - test_chained_store_increments_depth_and_accumulates: store turn-1 then store turn-2 with previous_response_id → turn-2 row has chain_depth==1 and context_messages holding the accumulated context (a never-incrementing build makes the 64-cap vacuous — DoS-adjacent) · covers: M1, M5
  - test_deleted_and_malformed_prev_id_404_no_dial: chain onto a deleted id and onto "not-a-resp-id" → both 404 ERR_RESPONSE_NOT_FOUND byte-identical; no dial; no usage rows · covers: R:ERR_RESPONSE_NOT_FOUND
  - test_chain_depth_cap_rejects_pre_dial: seed row with chain_depth=64 (DB arrange) → chain → 400 ERR_RESPONSES_CHAIN_TOO_DEEP; no dial; no usage row · covers: R:ERR_RESPONSES_CHAIN_TOO_DEEP
  - test_context_size_cap_rejects_pre_dial: seed row whose context+output serialize > 1 MiB → chain → 413 ERR_RESPONSES_CONTEXT_TOO_LARGE; no dial; no usage row · covers: R:ERR_RESPONSES_CONTEXT_TOO_LARGE
  - test_zdr_store_true_403_fail_closed: zdr_enabled tenant POST store:true → 403 ERR_ZDR_PAYLOAD_BLOCKED; upstream.calls==0 (completion NOT executed); 0 usage rows; 0 stored rows (what-stays-unchanged asserted) · covers: M7, R:ERR_ZDR_PAYLOAD_BLOCKED
  - test_zdr_store_false_still_serves_stateless: SAME zdr tenant, store absent → 200 served, exactly 1 usage row, 0 stored rows (loud only where payload would rest) · covers: M7, M9
  - test_sweeper_zdr_pass_deletes_rows: pre-existing full rows + flip zdr_enabled=true + sweep_once() → rows GONE (row-delete, conversations pattern) · covers: M8
  - test_sweeper_window_pass_deletes_aged_rows: row older than the operator ceiling vs fresh row + sweep_once() → old gone, fresh stays · covers: M8
  - test_concurrent_delete_while_chaining_is_atomic: asyncio.gather(cascade-DELETE, chain-POST) races → every POST outcome ∈ {200 served, 404 ERR_RESPONSE_NOT_FOUND}; never 5xx; each 404 outcome leaves zero usage rows for that request; after both settle the target row is GONE (cascade completed regardless of race winner) · covers: M4, M5, M6
  - test_get_delete_unauthenticated_401: no/garbage Bearer on GET+DELETE → 401 ERR_AUTH_INVALID_KEY problem+json (uniform posture) · covers: M6, R:ERR_AUTH_INVALID_KEY
</test_plan>

Prose build-guidance (not gated): ERR_RESPONSES_STORE_FAILED (500 post-serve persist failure, non-stream) and the streaming persist-failure → response.failed branch (M2) are contract-named but not red-gated — they need DB-fault injection; build them via the repository raising through the ProblemError map and cover in build-time unit tests (the dual security verify probes both) · the STORE_FAILED detail text and every store-path log line must never embed context_messages/response_body content (v22 no-payload-in-traceback floor) · the ZDR gate calls raise_if_zdr (fresh per-call, never cached) as the FIRST line of the store path, BEFORE chain resolution and BEFORE the governance dial (fail-closed ordering, retention_policy docstring rule) · cascade uses one WITH RECURSIVE delete over ix_stored_responses_tenant_prev with tenant_id at every hop; depth bounded ≤64 by the chain cap · a dangling previous_response_id echo on a race-surviving orphan is contracted, not a bug · anti-vacuity note: every 404 assert checks the problem+json `code` (FastAPI's route-absent default `{"detail":"Not Found"}` must NEVER satisfy a test — that is what keeps these red today).

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/responses_state_store/` · MUST run red (missing implementation) before Build.
RED evidence (2026-07-24, post-Tin-decisions fold — ZDR 403 fail-closed · cascade DELETE): `GATEWAY_TEST_DATABASE_URL=…responses_state_store uv run pytest tests/responses_state_store/ -q` → **20 failed in 18.60s**, against a tree where responses-api-core has MERGED (`2a7c402`) — store/chain tests fail on the parent's frozen `400 ERR_RESPONSES_STORE_UNSUPPORTED` terminal (the exact extension point this contract flips); DB-arrange/sweeper tests fail `asyncpg.exceptions.UndefinedTableError: relation "stored_responses" does not exist` (missing schema/migration); GET/DELETE tests fail on absent routes (`404 != 401`, `{"detail":"Not Found"}` never satisfies the required `code`); ZDR test fails `expected 403, got 400` (gate unbuilt). Red for the RIGHT reason across all 20; anti-vacuity held (every 404 assert requires `code == "ERR_RESPONSE_NOT_FOUND"`).

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `apps/gateway/src/gateway/responses_store/`
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
Verdict: EARNED (green earned) — DUAL independent adversarial SECURITY verify, both CLEAR
By: two independent add-advisor security agents (fresh context, disjoint lenses, 2026-07-24) on commit 896a736:
- **Verify A — isolation & enumeration (CLEAR):** all five kill targets conceded clean after a genuine adversarial trace — cross_tenant (tenant_id filtered at every GET/DELETE-cascade/chain hop), enumeration (unknown|cross-tenant|deleted|malformed all one byte-identical 404 ERR_RESPONSE_NOT_FOUND), eval_order (foreign id can't reach a 400/409/413 branch before the 404), delete_cascade (recursion tenant-bounded), zdr (403 fail-closed pre-dial, zero rows/zero cost). Two 💭 non-blocking: is_zdr fail-open branch unreachable on the store path; a PK collision surfaces as 500 (not a leak).
- **Verify B — DoS/durability/retention (CLEAR):** chain_caps_dos (depth-64/1-MiB enforced pre-materialization; cycle A↔B bounded by depth cap), cascade_scale (tenant-bounded, no infinite loop), zdr_retention (stored_responses genuinely purged by the sweeper + scrubbed under ZDR; both manifests correct — honest window), durability_concurrency and migration all conceded clean.
- ⚠ MEDIUM non-blocking gap (verify B, for the human): the ERR_RESPONSES_STORE_FAILED (500) post-serve path and the streaming response.failed durability path are correct BY CODE-READING but have no automated test coverage. Follow-up test recommended; not a defect.

### GATE RECORD
Reported: yes — sensitivity: security gate presented to Tin (never auto-passed) with both CLEAR verdicts + the MEDIUM coverage gap, lowest-confidence-first
Outcome (initial): HARD-STOP (Tin, 2026-07-24 — dual security verify both CLEAR, but the MEDIUM store-failure coverage gap must close before ship).
Coverage HEAL (test-only, commit 4daa223): +2 durability tests — non-stream store-failure → 500 ERR_RESPONSES_STORE_FAILED with the served-completion usage row standing + zero stored_responses rows; streaming store-failure → terminal response.failed carrying ERR_RESPONSES_STORE_FAILED (no fabricated response.completed), usage row standing. Product code untouched (no defect); non-vacuity confirmed. Suite 22/22.
Outcome (final): PASS (Tin's HARD-STOP condition met — dual security verify CLEAR + the required coverage now present and green).
Reviewed by: Tin Dang · date: 2026-07-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose materialized-context row store in a new `responses_store/` bounded context mirroring `conversations/`; rejected walk-the-chain per-turn rows à la conversation_messages (rejected: chain rebuild is a multi-row walk — a cross-tenant/deleted check per hop and a torn-read window per hop on a security-sensitive read path) · reuse conversations/ tables (rejected: different wire lifecycle + different ZDR posture would fork the frozen conversations contract; the milestone glossary keeps Response object a distinct resource).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate HARD-STOP (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · open] MILESTONE.md api-surface-parity Exit criterion "a ZDR tenant's stored response is metadata-only" is superseded by Tin's recorded decision (2026-07-24): ZDR store:true → 403 ERR_ZDR_PAYLOAD_BLOCKED fail-closed; orchestrator to update the milestone doc (evidence: this task's §1 M7 + coordinator message relaying AskUserQuestion).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
