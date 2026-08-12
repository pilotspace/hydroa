# PLAN: Close the ingest-worker ZDR TOCTOU with a lock-taking re-check

slug: zdr-ingest-lock-heal · created: 2026-07-25 · stage: production
milestone: managed-rag-finetune
autonomy: conservative
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: the vector-store ingest worker's ZDR re-check takes a row lock, so a `zdr_enabled` flip can never land strictly between the re-check and the chunk-write commit.

Framings weighed:
- **Additive shared locked-read port in `retention_policy.py`** (chosen) — add `is_zdr_locked` / `raise_if_zdr_locked` beside the FROZEN v1 `is_zdr` / `raise_if_zdr`, leaving every one of the six existing gated choke points byte-identical (they keep the plain, non-locking read — `persistence.py`'s own docstring is explicit that adding `FOR UPDATE` to the shared port would take a row lock on every unrelated gated write). `ingest_worker` and `responses_store/persistence` then share ONE implementation instead of two hand-copied ones.
- *Copy the local `_is_zdr_locked` helper into `ingest_worker`* — rejected: this is the second call site for the identical primitive; a third copy is how the first heal drifted from the second in the first place. The defect being fixed IS "the lesson was not back-applied".
- *Add `FOR UPDATE` to the frozen `raise_if_zdr`* — rejected: silently adds a tenant-row lock to artifacts/conversations/memories/batch_job_items/video_generation_jobs/files writes, a contention regression on five unrelated hot paths, and a frozen-contract edit.

Must:
<must>
  - M1 the ingest worker's finalize-time ZDR re-check is a LOCK-TAKING read (`SELECT zdr_enabled FROM tenants WHERE id = :tid FOR UPDATE`) issued inside the SAME session/transaction that commits the chunk write and the `in_progress -> completed` CAS.
  - M2 a `zdr_enabled = true` flip that is IN FLIGHT (row-locked, uncommitted) when the worker reaches finalize can never interleave into the write: the worker blocks on ordinary row-lock contention, then observes the committed flip and fails closed — `status="failed"`, `last_error.code="zdr_blocked"`, ZERO `vector_store_chunks` rows.
  - M3 the locked read is a single shared primitive — `ingest_worker`, `responses_store/application/persistence.py` AND `compliance/application/report_schedule_generator.py` (the third copy, surfaced by this Must's own red test at CR v2) all reach the same function; no hand-copied `SELECT zdr_enabled … FOR UPDATE` literal survives anywhere in `src/gateway`. The unrelated `FOR UPDATE OF t` locks on seat-cap and billing-owner are OUT of scope and must survive untouched — they lock different columns for different invariants.
  - M4 the six pre-existing `raise_if_zdr` choke points keep the plain non-locking read — unchanged behavior, no new lock, byte-identical.
  - M5 the non-ZDR majority path stays behaviorally identical: a tenant with `zdr_enabled = false` ingests to `completed` with its chunks and exactly one usage record, as today.
</must>
Reject:
<reject>
  - a ZDR flip committed BEFORE the worker's finalize re-check -> "zdr_blocked" (already covered by the shipped behavior; must not regress)
  - a ZDR flip held uncommitted across the worker's finalize re-check -> "zdr_blocked" (M2 — the defect)
</reject>
After:
<after>
  - `vector_store_chunks` can never hold rows for a tenant whose `zdr_enabled` flip was committed at or before the transaction that wrote them.
  - `grep -c 'FOR UPDATE' ` over the ZDR read paths resolves to exactly one definition site.
</after>
Boundary: none new — the only external input shape is the existing `tenants.zdr_enabled` boolean column, already exercised by the suite's `_set_tenant_zdr` helper.
<assumptions>
  ⚠ that the test can deterministically pin the flip INSIDE the re-check→commit window. The shipped `test_worker_zdr_flip_during_embed_fails_closed_toctou` flips during the *embed*, which the plain re-check already catches — that is precisely why the green suite missed this. If the new test cannot hold the window open, it proves nothing and the heal is unverified. Mitigation: hold the window with an explicit uncommitted `UPDATE tenants … ` in a second session (which takes the row lock) rather than with a sleep — timing-free and deterministic. If wrong: we ship a second unproven heal on the same defect, the exact failure this task exists to correct.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape)

```
gateway.tenants.application.retention_policy   (ADDITIVE — v1 surface untouched)
  async is_zdr_locked(session, tenant_id) -> bool
      SELECT zdr_enabled FROM tenants WHERE id = :tid FOR UPDATE
      Lock scoped to the session's autobegun transaction; held until commit/rollback.
      Unknown tenant_id -> False (parity with is_zdr's documented behavior).
  async raise_if_zdr_locked(session, tenant_id) -> None
      raises ZDR_PAYLOAD_BLOCKED (403 ERR_ZDR_PAYLOAD_BLOCKED) iff is_zdr_locked is True.
  is_zdr / raise_if_zdr — UNCHANGED (plain non-locking read; the six gated choke points
      keep calling them, byte-identical).

gateway.vector_stores.application.ingest_worker.VectorStoreIngestWorker.drive
  finalize block: raise_if_zdr(session, …)  ->  raise_if_zdr_locked(session, …)
  Same session as finalize_completed; ProblemError -> rollback + set_failed(zdr_blocked).

gateway.responses_store.application.persistence          (local _is_zdr_locked -> alias)
gateway.compliance.application.report_schedule_generator (local _is_zdr_locked -> alias)  [CR v2]
  both delegate to the shared retention_policy.is_zdr_locked
  (behavior identical — same SQL, same return type, same unknown-tenant False;
   one definition site — M3)

Schema: none. No DDL, no migration.
```

Target (measurable): the new red test `test_worker_zdr_flip_inflight_at_finalize_fails_closed` fails on the current tree (chunks persist for a flipped tenant) and passes after the change; the regression floor stays green — `tests/vector_store_files/` + `tests/vector_store_core/` + `tests/file_search_tool/` = 58, and the ZDR/compliance/responses floor = 133, and `tests/batches/` + `tests/video/` = 113; `uv run pyright` clean on all touched files; zero new migrations (single head `c7f1a4e83b92`); zero NEW ruff findings (the 4 pre-existing E501s on `ingest_worker.py` are unchanged in count).
CR v2 adds: the TOCTOU test survives ≥10 consecutive runs with zero confound-guard trips, proving it is not measuring the fail-open inline-drive race instead of the lock.
Status: FROZEN @ v2 — approved by Tin Dang
>
Reported: no

### Build-strategy
Scope (may touch): `apps/gateway/src/gateway/tenants/application/retention_policy.py` · `apps/gateway/src/gateway/vector_stores/application/ingest_worker.py` · `apps/gateway/src/gateway/responses_store/application/persistence.py` · `apps/gateway/src/gateway/compliance/application/report_schedule_generator.py` · `apps/gateway/tests/vector_store_files/test_vector_store_files.py`

> **CR v2 (2026-07-25, mid-build — WIDENING, discovered by the M3 red test):** the M3 test
> found a THIRD verbatim copy of `SELECT zdr_enabled FROM tenants WHERE id = :tid FOR UPDATE`
> at `compliance/application/report_schedule_generator.py:251`. M3 as frozen ("no second
> hand-copied FOR UPDATE string survives in the tree") cannot be satisfied without it, and
> weakening M3 to fit the original scope would be exactly the forbidden move. Scope widens by
> one file; no Must/Reject changes. The three unrelated `FOR UPDATE OF t` sites
> (`tenants/application/entitlements.py` seat-cap, `tenants/infrastructure/users_repository.py`
> + `scim/` billing-owner) lock DIFFERENT columns for different invariants and are explicitly
> OUT of scope — collapsing them would be a real defect.
Regression floor: `apps/gateway/tests/vector_store_files/` · `apps/gateway/tests/file_search_tool/` · `apps/gateway/tests/responses_api_core/` · `apps/gateway/tests/vector_store_core/` — plus any suite touching the six pre-existing `raise_if_zdr` choke points (`tests/tenant_retention_zdr/`, `tests/batches*/`, `tests/video*/`) to prove M4.
Persona: security-reviewer

Least-sure flag surfaced at freeze: [test] — the heal itself is three lines and mechanically obvious; ALL of the risk is in whether the red test genuinely pins the flip inside the re-check→commit window. The shipped suite already contains a test that *looks* like it covers this and does not. If the new test passes on the UNPATCHED tree, it is measuring the wrong window and must be rewritten before the heal lands.

---

## 4 · TESTS & SCENARIOS — failing-first suite (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_worker_zdr_flip_inflight_at_finalize_fails_closed:
      arrange — tenant A (zdr false), store + attached file, worker built via `_build_worker`;
        a SECOND session opens a transaction and issues `UPDATE tenants SET zdr_enabled = true
        WHERE id = :tid` WITHOUT committing, so it holds the tenant row lock while the worker runs.
      act — drive the worker concurrently; commit the holding transaction shortly after the worker
        has entered finalize (the worker blocks on the row lock until then).
      assert — the vsf row is `failed` with `last_error.code == "zdr_blocked"`, and
        `_count(app, "vector_store_chunks", tenant_a)` == 0.
      MUST-FAIL-FIRST on the current tree: the plain non-locking re-check reads the pre-flip
        value under read-committed, writes the chunks and commits BEFORE the flip lands
        -> chunk count > 0.
      covers: M1, M2, R:zdr_blocked
      CR v2 CONFOUND GUARD (independent refute A): assert BEFORE the chunk assertion that no
        inline-fallback drive task fired and that exactly ONE embed call happened, each with an
        explicit "TEST CONFOUND, not a security failure" message. The attach router's
        `_enqueue_or_fallback` is FAIL-OPEN: a Redis enqueue failure spawns an unsynchronized
        `asyncio.create_task` drive that can complete BEFORE the flip is in flight, persisting
        chunks with ZERO ZDR violation while the chunk assertion reads it as a security
        regression. Without this guard the single most alarming assertion in the suite cries
        wolf on an infrastructure hiccup.
  - test_zdr_locked_read_is_the_single_definition_site:
      arrange/act — grep the gateway source tree for a locked read OF THE ZDR FLAG
        specifically: a line carrying BOTH `zdr_enabled` and `FOR UPDATE`.
      assert — exactly one definition (retention_policy.is_zdr_locked); persistence.py,
        ingest_worker.py and report_schedule_generator.py all reach it rather than
        restating it.
      covers: M3
      CR v2 predicate narrowing: the v1 predicate ("FOR UPDATE" + "FROM tenants" anywhere
        in the file) also matched three LEGITIMATE, unrelated locks — `FOR UPDATE OF t` on
        the seat-cap (`tenants/application/entitlements.py`) and billing-owner
        (`tenants/infrastructure/users_repository.py`, `scim/`) invariants. Those lock
        DIFFERENT columns for different reasons and must survive untouched; only the
        ZDR-flag read is being deduplicated. The v1 predicate would have driven a real
        defect if satisfied literally.
  - test_plain_raise_if_zdr_takes_no_lock:
      arrange — a second session holds `SELECT … FROM tenants … FOR UPDATE` on tenant A.
      act — call the FROZEN `raise_if_zdr` for tenant A with a short timeout.
      assert — it returns promptly (does NOT block) — proving the six pre-existing choke
        points gained no lock.
      covers: M4
  - test_non_zdr_ingest_completes_unchanged:
      arrange — tenant A with zdr false, attached file. act — drain the worker.
      assert — status `completed`, chunk count > 0, exactly one usage record — identical to
        the pre-change behavior.
      covers: M5
</test_plan>

Rigor: M1/M2 carry the security weight and get the deterministic in-flight-lock test; M3/M4/M5 are the anti-regression floor. Deliberately NOT gated as separate tests (prose build-guidance): the existing `test_worker_zdr_flipped_after_attach_fails_closed` and `test_worker_zdr_flip_during_embed_fails_closed_toctou` must keep passing untouched — they cover the already-closed earlier windows and are the evidence that this heal only WIDENS the guarantee.

Tests live in: `apps/gateway/tests/vector_store_files/` · MUST run red before Build.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned, plus ONE widening CR (v2) the red suite forced. Sequence:
1. Wrote the 4-test red suite FIRST and confirmed it red on the unpatched tree — the key test failed
   with `assert 4 == 0`: four chunk rows of third-party document text at rest for a ZDR tenant. The
   two regression-guard tests (M4, M5) passed both before and after, as intended.
2. Added `is_zdr_locked` / `raise_if_zdr_locked` to `retention_policy.py` (additive; the frozen v1
   `is_zdr` / `raise_if_zdr` untouched, and their docstring now says WHY they stay non-locking).
3. Switched the ingest worker's finalize re-check to `raise_if_zdr_locked`; rewrote the module
   docstring, which still described the superseded plain re-check as if it were sufficient.
4. **CR v2 (widening):** the M3 test found a THIRD verbatim copy of the SQL literal in
   `compliance/application/report_schedule_generator.py:251`. Collapsed it and the `persistence.py`
   copy onto the shared primitive as module-level aliases (`_is_zdr_locked = is_zdr_locked`), so
   every existing call site and docstring reference keeps working unchanged.
5. Narrowed the M3 test predicate TWICE — it first matched three legitimate unrelated `FOR UPDATE
   OF t` locks (seat-cap, billing-owner), then matched PROSE in docstrings describing the lock. It
   now matches the exact executable SQL literal, which is the thing that was actually duplicating.
Code lives in: `apps/gateway/src/gateway/`
Spawn (multi-agent): security sensitivity — the §6 refute-read is a DUAL independent adversarial verify (house rule for security tasks, `[[residency-service-tiers-milestone]]`), by agents other than the builder.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope; keep the Regression floor green; no new dependencies; no migration.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass — including the §3 Regression floor
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED — specifically: the new test was CONFIRMED RED on the unpatched tree before the heal landed (a security test that passes both before and after is a confirmed cheat -> HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe — the lock is held to commit, not released early
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Evidence
- new red suite CONFIRMED RED pre-heal: `test_worker_zdr_flip_inflight_at_finalize_fails_closed`
  failed `assert 4 == 0` (four chunk rows at rest for a ZDR tenant); `test_zdr_locked_read_is_the_
  single_definition_site` failed on 2 definition sites (later 3, once the compliance copy surfaced).
- post-heal: `tests/vector_store_files/` + `tests/vector_store_core/` + `tests/file_search_tool/`
  **58 passed** · `tests/retention_zdr/` + `tests/compliance_report_schedule/` +
  `tests/compliance_bundle/` + `tests/responses_state_store/` + `tests/responses_api_core/` +
  `tests/test_retention_sweep.py` **133 passed** · `tests/batches/` + `tests/video/` **113 passed**.
- pyright: 0 errors across all four touched source files.
- ruff: 4 E501 findings on `ingest_worker.py`, ALL pre-existing — identical count with these changes
  stashed. Zero introduced. (Feeds R6 `lint-type-debt-sweep`, todo #36.)
- no migration; single alembic head unchanged.

### Refute-read verdict
Verdict: EARNED
By: DUAL INDEPENDENT adversarial refute (2026-07-25) + the builder's own isolation refute. Both
independent agents were briefed with default position "refuted=true" and told this codebase has
already shipped two incomplete heals of this exact defect.

**Builder (isolation refute):** reverted ONLY the one-line `raise_if_zdr_locked` → `raise_if_zdr`
substitution, leaving the rewritten test in place — the test returned to `assert 4 == 0`. Rules out
the strongest overfit hypothesis: that the rewritten predicate, not the lock, produced the green.

**Refuter A (concurrency/MVCC lens) — NOT REFUTED.** Verified three independent ways: (1) code trace
confirming the re-check and `finalize_completed`'s DELETE/INSERT×N/UPDATE-CAS share ONE session with
no intervening commit/flush to release the lock early, and that `finalize_completed` never opens its
own transaction; (2) a raw-SQL probe outside all app code confirming Postgres blocks a second
`FOR UPDATE` on an uncommitted UPDATE and then observes the newly-committed value (0.51s);
(3) an INSTRUMENTED rerun — `is_zdr_locked` blocked 0.196s, returned True, rolled back, 0 chunks,
status failed/zdr_blocked. Also confirmed both alternate entry points (`recover_orphans`, the
router's inline fallback) funnel through `drive()` so no path bypasses the locked re-check, and
ruled out lock-order inversion: every `tenants` `FOR UPDATE` path (seat-cap, billing-owner,
compliance, ZDR) takes ONE row and never a second afterwards.

**Refuter B (blast-radius lens) — NOT REFUTED on all four claims C1–C4.** Enumerated all 12
`is_zdr`/`raise_if_zdr` call sites: every one still uses the plain read; only the worker's finalize
re-check moved. Diffed both collapsed local copies character-by-character against the shared
primitive — identical SQL, return type and unknown-tenant behavior; the "caller must already be
inside `session.begin()`" divergence is docstring emphasis, NOT contract (the primitive never calls
`begin()` itself; both real call sites verified to wrap it in a live transaction). Independently
reproduced the builder's one-line revert AND did a full four-file `git stash` revert, both returning
the tests to red. Confirmed the grep test cannot self-match (scoped to `src/gateway`; the test file
lives under `tests/`) and that the M4 timeout genuinely proves non-blocking by reading the holder's
lock-release ordering.

**Refuter A's flagged anomaly — RESOLVED as test fragility, and FIXED.** A saw ONE first-run failure
with the pre-heal signature (`assert 4 == 0`) that it could not reproduce in 15 further runs, and
honestly declined to dismiss it. Its hypothesis was correct and identified a REAL defect in this
task's own test: the attach router's `_enqueue_or_fallback` is FAIL-OPEN, so a Redis enqueue failure
spawns an unsynchronized `asyncio.create_task` drive that can complete BEFORE the flip is in flight —
persisting chunks with zero ZDR violation while the assertion reads it as a security regression.
Fixed by CR v2: the test now asserts no inline-fallback task fired AND exactly one embed call, both
with explicit "TEST CONFOUND, not a security failure" messages. Tie-breaker after the fix: 12/12
targeted runs and 5/5 full-suite runs clean, zero confounds, zero real failures — on top of A's own
15 non-reproducing runs. Consistent with the known shared-infra contamination class (todos #37/#39).

### GATE RECORD
Reported: <yes | no>
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-07-25

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v2 (approved by Tin Dang)
- [AI] build — strategy used: as planned, plus ONE widening CR (v2) the red suite forced. Sequence: 1. Wrote the 4-test red suite FIRST and confirmed it red on the unpatched tree — the key test failed with `assert 4 == 0`: four chunk rows of third-party document text at rest for a ZDR tenant. The two regression-guard tests (M4, M5) passed both before and after, as intended. 2. Added `is_zdr_locked` / `raise_if_zdr_locked` to `retention_policy.py` (additive; the frozen v1 `is_zdr` / `raise_if_zdr` untouched, and their docstring now says WHY they stay non-locking). 3. Switched the ingest worker's finalize re-check to `raise_if_zdr_locked`; rewrote the module docstring, which still described the superseded plain re-check as if it were sufficient. 4. **CR v2 (widening):** the M3 test found a THIRD verbatim copy of the SQL literal in `compliance/application/report_schedule_generator.py:251`. Collapsed it and the `persistence.py` copy onto the shared primitive as module-level aliases (`_is_zdr_locked = is_zdr_locked`), so every existing call site and docstring reference keeps working unchanged. 5. Narrowed the M3 test predicate TWICE — it first matched three legitimate unrelated `FOR UPDATE OF t` locks (seat-cap, billing-owner), then matched PROSE in docstrings describing the lock. It now matches the exact executable SQL literal, which is the thing that was actually duplicating.
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
- `[SPEC · open]` Cross-subsystem tenant-row lock contention under horizontal scaling: three paths
  now take `FOR UPDATE` on the same `tenants` row — `persist_stored_response`, the compliance report
  generator, and (new) ingest-finalize. Single-pod this is inert (one sequential worker task per
  process), but with multiple gateway pods draining the same Redis queue, two finalizes for one
  tenant — or a finalize overlapping a stored-response persist or a compliance run — serialize for
  the hold duration of the finalize transaction (chunk DELETE + up to 2048 INSERTs + 2 UPDATEs).
  Inherent to M1 as frozen; the contract weighed the locking cost only against the six NON-locking
  choke points, never against the two paths that already shared this lock. Accepted tradeoff, not a
  defect. Feeds R6 capacity work. (evidence: independent refute B, C3 finding)
- `[SPEC · open]` The attach router's `_enqueue_or_fallback` fail-open inline drive is
  UNOBSERVABLE to callers and unsynchronized. It is correct for durability, but it makes any test
  that assumes "exactly one drive" silently unsound under Redis contention. Consider surfacing
  whether the fallback fired (a counter or app.state flag) so tests and ops can both see it.
  (evidence: refute A's unreproducible anomaly → CR v2 test hardening)

### Competency deltas
- `[TDD · open]` A security test whose ARRANGE depends on a FAIL-OPEN production path is unsound:
  the fallback firing produces the same observable signature as the vulnerability. Assert the
  confound away FIRST, with a message that says "confound, not a security failure", so the most
  alarming assertion in the suite cannot cry wolf. (evidence: refute A saw the pre-heal signature
  `assert 4 == 0` once and could not reproduce it in 15 runs; the cause was the inline fallback
  racing the flip, not a lock gap)
- `[ADD · open]` An independent refuter that reports an anomaly it CANNOT explain is more valuable
  than one that reports clean. A honestly surfaced its lowest self-evaluation score (0.75) over one
  unreproducible data point — that data point turned out to be a real defect in the task's own test.
  Reward the disclosure, never pressure refuters toward a clean verdict. (evidence: refuter A's
  self-evaluation note + the CR v2 fix it produced)
- `[ADD · open]` A duplicated load-bearing security primitive WILL drift. Three hand-copies of the
  same `SELECT ... FOR UPDATE` existed here; each independently documented why the lock was
  necessary, and the fourth site that needed it got a plain re-read anyway. When a heal lands, sweep
  for every sibling of the pattern in the SAME milestone — the recurring failure is not the bug, it
  is the un-back-applied lesson. (evidence: `[[zdr-toctou-async-write-paths]]`, third instance)
