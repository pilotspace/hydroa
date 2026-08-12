# TASK: B4+B5: usage recorder durable fallback + Redis timeout + PEL reclaim

slug: usage-flusher-durability · created: 2026-07-02 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
  - B4 recorder: `usage/application/recorder.py` — `RecordingUsageRecorder.record()` :74 (swallow try/except :103-130) → `_record_internal()` :132 (XADD `usage:events` :360, no explicit id; spend incrbyfloat :367/374/381). `record_correction()` :383 ALREADY writes an explicit deterministic `id` (event_fields['id'] :417, XADD :440) — the pattern B4's normal path must adopt. NO Redis timeout anywhere; NO durable fallback (a failed XADD is logged + LOST).
  - Never-raise invariant (DOCUMENTED — this is the change-request surface): recorder.py module docstring :8, class :38-39, record() :95; Protocol `UsageRecorder.record()` `proxy/domain/ports.py:157-158` ("Must not raise"); source-of-truth `.add/tasks/usage-metering/TASK.md` §1 L23 / §2 L88-92 / §5 L247. Enforced by FROZEN test `tests/usage/test_usage_metering.py::test_redis_unavailable_completion_still_200` (:417, BrokenRedis :81). The new contract EXTENDS (not weakens) this: still never raises, but a Redis blip no longer silently loses the event.
  - Dispatch: `use_cases.py:238` `asyncio.ensure_future(usage_recorder.record(...))` — fire-and-forget (already off the hot path); a bounded timeout inside record() adds no synchronous latency.
  - B5 flusher: `usage/application/flusher.py` — `flush_once()` :51 uses `xreadgroup(group, consumer, {stream: '>'} , count=100, block=0)` :64-70 → `>` NEVER re-reads PEL entries → crash between delivery and XACK (:229) strands entries forever. NO XAUTOCLAIM/XCLAIM anywhere. `_backlog_size()` :304 uses XPENDING summary. `drain_until_empty()` :237 also can't clear a pre-existing PEL today → the SAME reclaim step fixes both live loop + shutdown drain. Reclaim slot: inside flush_once() before/around :64, feed reclaimed (id, fields) through the same `_process_entry()` (:80-81, shape matches XAUTOCLAIM return).
  - Redis client: `main.py:751` `aioredis.from_url(..., decode_responses=False)` — SHARED across recorder/flusher/budget/ratelimiters/bandwidth → do NOT add client-level socket_timeout (wide blast radius). Use the house per-call idiom `async with asyncio.timeout(N)` (const like `usage/api/router.py:845` `_RATELIMIT_REDIS_TIMEOUT_SECONDS`).
  - cost_recovery: `cost_recovery.py:163` calls `record_correction()` (the explicit-id path) — shares stream/group; unaffected except it must keep its own swallow if B4 adds a shared fallback helper.
  - Flusher dedup: `flusher.py:202` INSERT `ON CONFLICT (id) DO NOTHING`; PK derived `stream_id_to_uuid(entry_id)` (:118, redis_stream.py:18). Constants `STREAM_KEY`/`CONSUMER_GROUP`/`CONSUMER_NAME='flusher-0'` `redis_stream.py:13-15`.
Context (working folder): DB-BOUND task (usage_records + real Redis). Tests: `tests/usage/test_usage_metering.py` (Postgres + redis db9), `tests/ops/test_lifespan.py` (FakeRedisStream — lacks xautoclaim + has an xpending-shape mismatch → B5 fake needs extending, or run against real redis db9). No fake implements XAUTOCLAIM today.
Honors (patterns / conventions): "recorder never raises into the proxy path" (EXTENDED, not broken) · Postgres = billing truth (append-only usage_records, idempotent via ON CONFLICT id) · per-call asyncio.timeout idiom · CLAUDE.md design-for-failure (timeout+fallback+reclaim).
Anchors the contract cites: `RecordingUsageRecorder.record`/`_record_internal`/`record_correction`, `UsageRecorder` Protocol (ports.py:157), `.add/tasks/usage-metering/TASK.md` invariant lines, `UsageLedgerFlusher.flush_once`/`drain_until_empty`, `redis_stream.py` constants + `stream_id_to_uuid`, `flusher.py:202` ON CONFLICT.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Usage events survive a Redis blip (B4: bounded timeout + durable Postgres fallback) and a crash mid-flush (B5: XAUTOCLAIM PEL reclaim) — without ever raising into the proxy path.
Framings weighed: durable Postgres fallback keyed on a deterministic per-event id (chosen — Postgres is billing truth; the flusher already dedups on explicit id via ON CONFLICT, so XADD-flush and fallback converge on ONE row) · bounded-retry-only (rejected — does NOT survive a SUSTAINED outage; the event is still lost, so B4 stays open) · client-level socket_timeout (rejected — the redis_client is shared across budget/ratelimiter/bandwidth; wide blast radius — use the per-call `asyncio.timeout` idiom).
Must:
<must>
  - B4-id: `record()`'s normal path generates a deterministic event id ONCE and writes it into `event_fields["id"]` before the XADD (mirroring `record_correction`), so the stream-flush PK (flusher.py:112) and the fallback PK are IDENTICAL.
  - B4-timeout: every Redis call in the record path is bounded by `async with asyncio.timeout(_USAGE_REDIS_TIMEOUT_SECONDS)` (new module const; per-call, NOT client-level).
  - B4-fallback: on XADD timeout/failure, the usage event is written DIRECTLY to `usage_records` (same id, `ON CONFLICT (id) DO NOTHING`) via a shared INSERT helper — so a Redis blip no longer loses billing. Advisory spend counters (incrbyfloat) stay best-effort (reconciled from the ledger).
  - B4-invariant: ALL of the above lives inside the existing swallow try/except — even a double failure (Redis AND Postgres down) resolves to log-and-swallow; `record()` STILL never raises into the proxy path (extends, never weakens, the documented invariant).
  - B5-reclaim: `flush_once()` runs `XAUTOCLAIM(stream, group, consumer, min_idle_time=_PEL_RECLAIM_IDLE_MS, start="0-0")` before the `>` xreadgroup and feeds reclaimed entries through the SAME `_process_entry`, so a crash-stranded PEL entry is reclaimed + flushed. This also lets `drain_until_empty` clear a pre-existing PEL.
Reject:
<reject>
  - (no new HTTP rejection — durability hardening on a background path)
  - INVARIANT (fail if violated): `record()` / `record_correction()` MUST NOT raise into the caller under ANY store-failure combination; the frozen `test_redis_unavailable_completion_still_200` stays green.
  - INVARIANT (fail if violated): no double-billing — a slow-but-recovered Redis (fallback fired AND the XADD later flushes) yields exactly ONE `usage_records` row (same deterministic id → ON CONFLICT).
</reject>
After:
<after>
  - A Redis outage during `record()` leaves the usage event durably in `usage_records` (via fallback), not lost. A consumer crash between XREADGROUP and XACK is recovered on the next `flush_once` (XAUTOCLAIM), not stranded forever. Completions still return 200 throughout.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] Adding an explicit `id` to the NORMAL record() path changes the flusher's PK source from `stream_id_to_uuid(entry_id)` to the explicit id — lowest confidence because a test may pin the PK to the stream-id derivation; if wrong: that test breaks (not a billing bug — the id is still unique + idempotent). Confirm no test asserts PK == stream_id_to_uuid for normal events before build.
  - [ ] The recorder already holds a DB session (pricing/markup fetch @ recorder.py:172-174) → the fallback INSERT is feasible in-place; confirm the session factory is reachable from the record path.
  - [ ] `_PEL_RECLAIM_IDLE_MS` default (proposed 60000ms) must exceed a normal flush cycle so XAUTOCLAIM never steals a legitimately in-flight entry; the single hardcoded consumer name means self-reclaim (correct for crash-restart).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Redis XADD fails -> event durably written to Postgres (B4 fallback)
  Given the recorder's Redis XADD raises/times out
  When record() is called for a billable completion
  Then a usage_records row exists for that event (via the direct fallback)
  And record() did not raise (completion still 200)

Scenario: Slow-but-recovered Redis does not double-bill (B4 dedup)
  Given the fallback fired for an event AND the same event's XADD later lands and is flushed
  When both the fallback INSERT and the flusher INSERT run
  Then exactly ONE usage_records row exists for that event
  And its id is the deterministic explicit id (both paths key on it, ON CONFLICT DO NOTHING)

Scenario: Redis AND Postgres both down -> swallow, never raise (B4 invariant)
  Given both the Redis XADD and the Postgres fallback fail
  When record() is called
  Then record() logs and swallows (returns None), raising nothing
  And the frozen test_redis_unavailable_completion_still_200 stays green

Scenario: Crash-stranded PEL entry is reclaimed on next flush (B5)
  Given an entry was delivered by XREADGROUP but never XACKed (consumer crashed), idle > _PEL_RECLAIM_IDLE_MS
  When flush_once() runs
  Then XAUTOCLAIM reclaims it and it is INSERTed into usage_records and XACKed
  And a normally-flowing (idle < threshold) in-flight entry is NOT stolen

Scenario: drain_until_empty clears a pre-existing PEL (B5)
  Given the PEL holds stranded entries at shutdown
  When drain_until_empty() runs
  Then those entries are reclaimed + flushed within the drain timeout
  And the bounded-time drain guarantees (test_drain_*_within_timeout) still hold

Scenario: Normal record() carries a deterministic explicit id (B4-id)
  Given a normal (non-correction) record() call
  When the event is XADDed and later flushed
  Then event_fields carries an "id" and the flusher uses it as the PK
  And two flushes of that event yield one row (idempotent, unchanged)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Internal durability invariants (NO HTTP shape change). New module consts + one config knob.

RecordingUsageRecorder.record()  (usage/application/recorder.py)
  - generates event_id = uuid4() ONCE; writes event_fields["id"] = str(event_id) before XADD
  - wraps Redis calls:  async with asyncio.timeout(_USAGE_REDIS_TIMEOUT_SECONDS): await redis.xadd(...)
  - on XADD TimeoutError/RedisError -> _fallback_insert(event_id, fields)  # direct usage_records INSERT
      INSERT ... ON CONFLICT (id) DO NOTHING   (shared with / mirrors flusher.py:185-226)
  - the whole body stays inside the existing try/except (:103-130) -> never raises; double-failure = swallow+log
  - advisory spend incrbyfloat stays best-effort (not part of the durable guarantee)

UsageLedgerFlusher.flush_once()  (usage/application/flusher.py)
  - BEFORE the '>' xreadgroup:
      claimed = await redis.xautoclaim(STREAM_KEY, CONSUMER_GROUP, CONSUMER_NAME,
                                       min_idle_time=_PEL_RECLAIM_IDLE_MS, start="0-0", count=100)
      for (entry_id, fields) in claimed: await self._process_entry(entry_id, fields)   # same path, same ACK
  - unchanged: '>' read, _process_entry, XACK-after-INSERT, ON CONFLICT dedup

New consts:  _USAGE_REDIS_TIMEOUT_SECONDS (recorder) · _PEL_RECLAIM_IDLE_MS (flusher, from a new
  Settings knob GATEWAY_USAGE_PEL_RECLAIM_IDLE_MS default 60000)
Schema: usage_records — no DDL change; the recorder now also INSERTs (fallback) with the SAME PK the
  flusher would use. Idempotent via existing ON CONFLICT (id).
```

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze: [contract] B4 no-double-bill depends on the deterministic explicit `id` being used by BOTH the XADD-flush path and the direct-fallback path — VERIFIED reachable: flusher.py:112-120 already prefers `event_fields["id"]` and INSERTs `ON CONFLICT (id) DO NOTHING` (:202). Adding the explicit id to the NORMAL record() path shifts the flusher PK from `stream_id_to_uuid(entry_id)` to the explicit id (idempotent-equivalent) — cost if a test pins PK to the stream-id derivation: that test breaks (not a billing bug). Secondary [contract]: this is a CHANGE-REQUEST to the documented "recorder never raises" invariant (`.add/tasks/usage-metering/TASK.md` §1 L23) — it is EXTENDED (still never raises; now also durable), never weakened; the frozen redis-down test must stay green.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: both sub-fixes fully — B4 (fallback + timeout + no-double-bill + never-raise) and B5 (reclaim + no-steal + drain). DB-bound (usage_records + real redis db9), per the existing usage suite.
Plan (one test per scenario — extends tests/usage/test_usage_metering.py + tests/ops/test_lifespan.py):
<test_plan>
  - test_redis_xadd_failure_falls_back_to_postgres: arrange XADD raises / act record() / assert a usage_records row exists for the event AND record() did not raise
  - test_slow_redis_no_double_bill: arrange fallback INSERT for id X + later flush of the same event (explicit id X) / act both / assert exactly ONE row with id X (ON CONFLICT)
  - test_both_stores_down_swallows_and_stays_200: arrange BrokenRedis + failing session / act record() / assert returns None, raises nothing (extend the frozen redis-down invariant test; it must stay green)
  - test_stranded_pel_entry_reclaimed: arrange an unacked PEL entry idle > _PEL_RECLAIM_IDLE_MS / act flush_once() / assert it is INSERTed + XACKed
  - test_inflight_entry_not_stolen: arrange a PEL entry idle < threshold / act flush_once() / assert it is NOT reclaimed (no premature steal)
  - test_drain_clears_preexisting_pel: arrange stranded PEL at shutdown / act drain_until_empty() / assert reclaimed+flushed within timeout AND bounded-time drain guarantees hold
  - test_normal_record_carries_explicit_id_and_flusher_uses_it: arrange normal record() / act flush / assert event_fields has "id" AND flusher PK == that id AND double-flush = one row
</test_plan>
Fakes: extend tests/ops/test_lifespan.py FakeRedisStream with `xautoclaim` (+ fix its `xpending` shape mismatch) for B5 fast tests; the B4 fallback + no-double-bill tests use the real Postgres + redis-db9 fixtures already in test_usage_metering.py. NOTE (:5433 one-pytest-at-a-time): the DB-bound tests here cannot run concurrently with other DB suites — build serially.

Tests live in: `apps/gateway/tests/usage/` · `apps/gateway/tests/ops/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/recorder.py` · `apps/gateway/src/gateway/usage/application/flusher.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/usage/` · `apps/gateway/tests/ops/`
Scope refinement (build-time, honest): `main.py` ADDED — the lifespan constructs UsageLedgerFlusher (main.py:364); the new `GATEWAY_USAGE_PEL_RECLAIM_IDLE_MS` knob is inert dead-config unless wired into that constructor (one line: `pel_reclaim_idle_ms=_settings.usage_pel_reclaim_idle_ms`). Not a frozen-contract change (§3 is HTTP-shape-free); declared here so the engine's touched⊆declared scope check passes.
Strategy (ordered batches): 1. add `_USAGE_REDIS_TIMEOUT_SECONDS` const + `GATEWAY_USAGE_PEL_RECLAIM_IDLE_MS` Settings knob. 2. B4: generate explicit id in record() normal path (write event_fields["id"]); wrap Redis calls in asyncio.timeout; add `_fallback_insert` (shared INSERT helper, ON CONFLICT id) invoked on XADD failure — all inside the existing swallow. 3. B5: add XAUTOCLAIM step at the top of flush_once() feeding _process_entry. 4. tests (extend FakeRedisStream with xautoclaim).
Known-problem fixes: double-billing → the SAME deterministic id on XADD-path and fallback-path (flusher already prefers explicit id + ON CONFLICT) · invariant-break → fallback INSIDE the existing try/except so a Postgres-also-down still swallows · premature-steal → min_idle_time threshold > a flush cycle · shared-client-blast-radius → per-call asyncio.timeout, not client socket_timeout · fake/prod xpending shape mismatch (flusher.py:308) — fix the fake while adding xautoclaim.
Strategy actually used: As planned, with one refinement the advisor's review forced. B4: recorder generates `event_id = uuid4()` once, stamps `event_fields["id"]` before XADD, wraps XADD in `asyncio.timeout(_USAGE_REDIS_TIMEOUT_SECONDS=5.0)`, and on ANY xadd failure calls `_fallback_insert()` (delegates to the extracted `insert_usage_row` helper, same id → ON CONFLICT (id) DO NOTHING). REFINEMENT: the frozen §1 "B4-timeout" Must says *every* Redis call in the record path is timeout-bounded — I initially wrapped only XADD, but the three advisory `incrbyfloat` calls were bare awaits (unbounded, since the shared client has no socket_timeout and §0 forbids adding one). Fixed: the advisory block is now inside `asyncio.timeout(5s)` + a log-and-swallow except (bounded AND best-effort — no durable fallback; the ledger row is source of truth). B5: `flush_once(reclaim_min_idle_ms=None)` runs XAUTOCLAIM(start_id="0-0", count=100, min_idle_time=idle) after `_ensure_group()` and before the `>` xreadgroup, feeding each claimed entry through `_process_entry`; the reclaim is wrapped in try/except so a reclaim failure logs and does not kill the flush. `drain_until_empty` passes `reclaim_min_idle_ms=0` (idle-agnostic at shutdown). Extracted shared `insert_usage_row` (parse + INSERT, raises `MalformedUsageEventError(ValueError)` on bad tenant/key UUID → caller ACK-drops; propagates DB errors → caller skips ACK). Wired `pel_reclaim_idle_ms=_settings.usage_pel_reclaim_idle_ms` at main.py:364. HEAL (attempt 1/3, from an independent adversarial review — Finding A): the poison classification was too narrow (only tenant/key UUID) and both flush loops were batch-abort, so an unparseable numeric field could starve its whole reclaim batch forever. Widened `insert_usage_row` to classify ANY deterministic required-field parse failure (int/Decimal/UUID) as `MalformedUsageEventError` (drop+ACK, full-payload ERROR log) with `session.execute` kept OUTSIDE the guard (DB errors stay retryable); made BOTH the reclaim and `>` loops per-entry; guarded empty/None entries. Corrected the config comment's false "never stolen" claim (Finding C). Pinned by 2 new RED→GREEN regression tests.
Safety rule (feature-specific): billing truth is the append-only ledger; every write is idempotent on a deterministic id (ON CONFLICT id DO NOTHING). A store write is either committed or left durable for retry — an entry is ACKed ONLY after a successful INSERT or an explicit poison-drop, NEVER on a retryable error.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — target 7 (4 B4 + 3 B5) + 2 new heal regression (poison-starvation, per-entry DB-error) GREEN. usage 13/13 (incl. frozen `test_redis_unavailable_completion_still_200`); ops/lifespan 13/13. Regression across ALL flusher/recorder-exercising suites (~179 tests): field-shape 77 · cost-recovery/governance 43 · reconciliation/cache-alias/budgets 33 · pyright 0 · source ruff-clean. NOTE: the shared :5433 `gateway_test` was contaminated by a concurrent v56-branch worktree (its `tenant_model_presets` table — not in this branch's `Base.metadata` — blocked `drop_all` on `tenants`); verified against an ISOLATED DB `gateway_test_doctor` to remove the cross-worktree footgun. One env-only flake (`team_attribution::test_counter_and_ledger_both_visible`) = shared-redis (`:6380/9`) socket-read timeout under the concurrent load (pass→fail→pass on retry; not a code failure).
- [x] coverage did not decrease — new code (fallback, reclaim, insert_usage_row helper) is exercised by the new tests; no source path left uncovered vs. before (helper is the extraction of already-covered `_process_entry` logic).
- [x] no test or contract was altered during build — no contract touched. The 2 heal regression tests were added during a proper `heal → phase tests → advance` re-cross (which re-snapshots the tamper baseline), NOT edited during build — so the tamper-tripwire baseline matches. No existing test assertion was weakened (all original lifespan/usage test bodies byte-unchanged); the fix only ADDED coverage.
- [x] the green was EARNED, not gamed — refute-read = EARNED (below). The independent backend-expert review found a CRITICAL defect (Finding A) the green suite could not catch; it was healed (attempt 1/3) with two RED→GREEN regression tests that pin the fix. No test weakened, no contract touched. See "Refute-read verdict".
- [x] concurrency / timing of the risky operation is safe — no-double-bill proven by same-deterministic-id + ON CONFLICT (id) (`test_slow_redis_no_double_bill` asserts count==1 after record() AND after flush); XAUTOCLAIM min_idle_time gates reclaim so a fresh in-flight entry is not stolen (`test_inflight_entry_not_stolen`); every Redis call in the record path is `asyncio.timeout`-bounded (no unbounded await). HEAL-HARDENED: the RECLAIM (xautoclaim) loop is now per-entry, directly covered by the 2 new tests (`test_poison_reclaim_entry_does_not_starve_batch`, `test_transient_db_error_does_not_abort_reclaim_batch`, both seed_pel → the reclaim path) — a poison or transient-DB-error entry can never abort its batch / starve siblings. The `>` normal-read loop got the SAME per-entry wrapper as symmetric defense-in-depth (NOT exercised by these tests — they only drive the reclaim path; it is safe untested because a `>`-delivered poison is drop+ACKed inside `_process_entry` by classification and a transient error self-heals via reclaim next cycle). A deterministically-unparseable entry is drop+ACKed (logged with full payload); a retryable DB error skips ACK (redelivered).
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps (asyncio is stdlib); no new secrets; SQL is the pre-existing parameterized INSERT moved verbatim into the helper.
- [x] layering & dependencies follow CONVENTIONS.md — recorder→flusher import is a local (function-body) import to avoid a module cycle, consistent with the existing seam; config knob follows the Settings pattern; no cross-layer leak.
- [~] a person reviewed and approved the change — under `autonomy: auto` this is the human spot-audit backstop; the earned-green refute-read is recorded below in lieu of a blocking human gate (billing-critical → independent subagent review obtained).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] B4 fallback: a failed XADD leaves a durable `usage_records` row and record() does not raise — confirmed by `test_redis_xadd_failure_falls_back_to_postgres` (row present after record()). GREEN.
- [x] B4 no-double-bill: fallback row + later flush of the same (landed) event = exactly ONE row (same deterministic id → ON CONFLICT) — confirmed by `test_slow_redis_no_double_bill` (count==1 after record() AND after flush; non-vacuous). GREEN.
- [x] B4 invariant: Redis AND Postgres both down → record() returns None (never raises) AND the fallback was attempted — confirmed by `test_both_stores_down_swallows_and_stays_200` (result None, factory.calls==1) + frozen `test_redis_unavailable_completion_still_200` stays green. GREEN.
- [x] B4-id: a normal record() writes an explicit `id` field; the flusher uses it as the row PK; double-flush idempotent — confirmed by `test_normal_record_carries_explicit_id_and_flusher_uses_it`. GREEN.
- [x] B5 reclaim: a crash-stranded PEL entry (idle > threshold) is reclaimed by XAUTOCLAIM, INSERTed, and ACKed on the next flush_once — confirmed by `test_stranded_pel_entry_reclaimed`. GREEN.
- [x] B5 no-steal: XAUTOCLAIM respects min_idle_time — reclaims the stale strand, leaves a fresh in-flight entry untouched — confirmed by `test_inflight_entry_not_stolen`. GREEN.
- [x] B5 drain: drain_until_empty clears a pre-existing PEL (idle-agnostic, min_idle=0) within the timeout — confirmed by `test_drain_clears_preexisting_pel`. GREEN.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced (grep-verified across `src`): `insert_usage_row` called by BOTH flusher._process_entry (flusher.py:283) and recorder._fallback_insert (recorder.py:430, `record_id=event_id`); `MalformedUsageEventError` raised at flusher.py:111, caught at flusher.py:284; `_fallback_insert` called at recorder.py:387 (xadd except); `_USAGE_REDIS_TIMEOUT_SECONDS` used at recorder.py:378 (xadd) + 405 (advisory); `usage_pel_reclaim_idle_ms` (config.py:104) → main.py:367 → flusher `pel_reclaim_idle_ms` (flusher.py:186/192) → consumed in flush_once (flusher.py:215). Both durability paths converge on the SAME id (event_fields["id"] on XADD; record_id=event_id on fallback) — verified by reading both call sites.
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; the config knob is wired end-to-end (not inert); pyright 0 errors, ruff clean on source.
- [~] SEMANTIC (prose / non-code) — n/a (this is a code task; the semantic path does not apply).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: NOT-EARNED (first pass) → healing. An independent backend-expert adversarial review of the billing-critical diff found a CRITICAL defect the green suite could not catch (no test seeded a poison reclaim entry): Finding A — the B5 XAUTOCLAIM reclaim loop (flusher.py:216-231) wraps the ENTIRE claim-and-process batch in one try/except, and `_process_entry` only classifies tenant/key UUID failures as `MalformedUsageEventError` (drop+ACK). Any OTHER unparseable field (the `int()`/`Decimal()` parses at flusher.py:75-125) raises a bare ValueError/InvalidOperation that propagates out and ABORTS the whole batch. Because XAUTOCLAIM re-owns + resets idle for the batch at claim time and the poison entry is always oldest (start_id="0-0", never ACKed), it is re-claimed first every cycle → the legitimate entries claimed alongside it STARVE permanently (never billed, no self-heal, no alert). The normal `>` loop (flusher.py:249-251) shares the same batch-abort fragility. → NOT gaming; a genuine correctness gap. Healing per the frozen rule (no test weakened, no contract touched): (1) add a RED regression test that seeds a poison entry ORDERED BEFORE a good entry in one reclaim batch and asserts the good entry is still inserted+ACKed (no starvation) and the poison is dropped+ACKed; (2) fix flusher.py — broaden poison classification to ALL deterministic field-parse failures (drop+ACK, log full raw payload at ERROR for recovery), keep `session.execute` OUTSIDE that guard (DB errors stay retryable → propagate, no ACK), and make BOTH loops per-entry resilient so one entry can never abort the batch.
By: agent a0554d5fa59f03799 (backend-expert, independent) + self-confirmed against the code · adversarially checked: double-bill interleavings, ACK ordering, poison-entry starvation, reclaim theft under multi-replica, never-raise under dual store failure, timeout coverage of every record-path call.

RESOLUTION (heal attempt 1/3) → **EARNED**. Finding A is fixed and pinned by two new RED→GREEN regression tests in `tests/ops/test_lifespan.py`: `test_poison_reclaim_entry_does_not_starve_batch` (poison ordered BEFORE a good entry in one reclaim batch → good entry still INSERTed+ACKed, poison dropped+ACKed) and `test_transient_db_error_does_not_abort_reclaim_batch` (a retryable DB error on entry #1 → entry #2 still processed, #1 NOT acked → retried). Both were confirmed RED against the buggy code for the exact starvation reason, then GREEN after the fix. The fix: (a) `insert_usage_row` wraps ALL required-field parses in one guard → any deterministic parse failure raises `MalformedUsageEventError` (drop+ACK), while `session.execute` stays OUTSIDE the guard (DB errors stay retryable → propagate, no ACK) — the boundary that keeps a recoverable bill from being silently dropped; (b) the drop path logs the FULL raw fields at ERROR for recovery; (c) BOTH the reclaim loop and the normal `>` loop are now per-entry (one entry can never abort the batch); (d) an empty/None entry is guarded (drop+ACK). Finding C (multi-replica shared consumer-name) — behavior left unchanged (idempotency-safe: ON CONFLICT id + no-op XACK); the false "never stolen" config comment was corrected to state the single-consumer assumption + a spec-delta. Finding B (unbounded fallback DB call) — deferred (see GATE RECORD rationale). Re-verified against an ISOLATED test DB (see below): ~179 flusher/recorder-exercising tests GREEN, pyright 0, ruff clean.

### GATE RECORD
Outcome: PASS (auto-gate under autonomy: auto; earned-green refute-read recorded above, EARNED after heal 1/3; no security finding — none of A/B/C is a security gap, so no HARD-STOP required).
Deferred (documented as §7 spec-deltas, NOT blockers):
  - Finding B (unbounded fallback DB insert): frozen §1's timeout mandate is Redis-scoped, and this is a pre-existing systemic gap (`_record_internal`'s pricing fetch is also unbounded). Verified facts: the recorder SHARES the request-path engine/pool (main.py:619/625), but `pool_timeout` is at its SQLAlchemy default (30s, not disabled) — so connection-acquire is bounded and the reviewer's "pins indefinitely" is the once-acquired query-execution window only. Bounding the fallback trades durability for latency (a merely-slow Postgres would LOSE the event on a 5s cap), so it deserves its own contract, not a reflex fix here. → spec-delta.
  - Finding C (multi-replica shared CONSUMER_NAME 'flusher-0'): idempotency-safe today (ON CONFLICT id + no-op XACK); the false "never stolen" config comment was corrected. Per-replica consumer naming → spec-delta.
Reviewed by: self (AI, autonomy: auto) + independent adversarial review by agent a0554d5fa59f03799 (backend-expert) whose BLOCK on Finding A drove this heal · date: 2026-07-02

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `usage_recorder: XADD failed; persisting event directly to the ledger` warnings (fallback engaged → Redis health); rate of `advisory spend increment failed` warnings (budget-counter drift risk → reconcile from ledger); flusher `xautoclaim reclaim failed` warnings + count of reclaimed entries per tick (a rising reclaim count = a consumer is crashing mid-process); PEL depth (`xpending` summary) as a stranded-work gauge.

### Decisions (ADR)
- [AI] specify — chose durable Postgres fallback keyed on a deterministic per-event id; rejected bounded-retry-only (rejected — does NOT survive a SUSTAINED outage; the event is still lost, so B4 stays open) · client-level socket_timeout (rejected — the redis_client is shared across budget/ratelimiter/bandwidth; wide blast radius — use the per-call `asyncio.timeout` idiom).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: As planned, with one refinement the advisor's review forced. B4: recorder generates `event_id = uuid4()` once, stamps `event_fields["id"]` before XADD, wraps XADD in `asyncio.timeout(_USAGE_REDIS_TIMEOUT_SECONDS=5.0)`, and on ANY xadd failure calls `_fallback_insert()` (delegates to the extracted `insert_usage_row` helper, same id → ON CONFLICT (id) DO NOTHING). REFINEMENT: the frozen §1 "B4-timeout" Must says *every* Redis call in the record path is timeout-bounded — I initially wrapped only XADD, but the three advisory `incrbyfloat` calls were bare awaits (unbounded, since the shared client has no socket_timeout and §0 forbids adding one). Fixed: the advisory block is now inside `asyncio.timeout(5s)` + a log-and-swallow except (bounded AND best-effort — no durable fallback; the ledger row is source of truth). B5: `flush_once(reclaim_min_idle_ms=None)` runs XAUTOCLAIM(start_id="0-0", count=100, min_idle_time=idle) after `_ensure_group()` and before the `>` xreadgroup, feeding each claimed entry through `_process_entry`; the reclaim is wrapped in try/except so a reclaim failure logs and does not kill the flush. `drain_until_empty` passes `reclaim_min_idle_ms=0` (idle-agnostic at shutdown). Extracted shared `insert_usage_row` (parse + INSERT, raises `MalformedUsageEventError(ValueError)` on bad tenant/key UUID → caller ACK-drops; propagates DB errors → caller skips ACK). Wired `pel_reclaim_idle_ms=_settings.usage_pel_reclaim_idle_ms` at main.py:364. HEAL (attempt 1/3, from an independent adversarial review — Finding A): the poison classification was too narrow (only tenant/key UUID) and both flush loops were batch-abort, so an unparseable numeric field could starve its whole reclaim batch forever. Widened `insert_usage_row` to classify ANY deterministic required-field parse failure (int/Decimal/UUID) as `MalformedUsageEventError` (drop+ACK, full-payload ERROR log) with `session.execute` kept OUTSIDE the guard (DB errors stay retryable); made BOTH the reclaim and `>` loops per-entry; guarded empty/None entries. Corrected the config comment's false "never stolen" claim (Finding C). Pinned by 2 new RED→GREEN regression tests.
- [AI] verify — gate PASS (reviewed by self (AI, autonomy: auto) + independent adversarial review by agent a0554d5fa59f03799 (backend-expert) whose BLOCK on Finding A drove this heal)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] Extend the durable XADD-failure fallback to `record_correction()` — only `record()` was in the frozen B4 contract, so a correction event still silently vanishes if Redis is down at correction time (evidence: `record_correction` in recorder.py has an unwrapped XADD + no fallback; symmetric revenue-integrity gap to the one B4 just closed for `record()`).
- [SPEC · open] Bound the fallback/reconciliation DB calls (or accept them as unbounded) — §1's timeout mandate is Redis-scoped; `insert_usage_row` (fallback path) and the reconciliation pricing-fetch are unbounded Postgres awaits, so a hung DB can still stall the record task after Redis is bypassed (evidence: advisor review — not a new risk class, `_record_internal`'s pricing fetch was already unbounded; flagged so it is not a silent omission).
- [SPEC · open] Per-provider circuit-breaker isolation (diagnostic B3 fix#1) — a plain model id shared across providers lets one provider's breaker trip contaminate a same-id model on a healthy provider (evidence: enterprise-readiness diagnostic B3). Queued as its own hardening task.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [SDD · folded] A "bound EVERY X call" Must must be verified against EACH call in the path, not the one the pseudocode illustrates — first build wrapped only XADD and left the three advisory `incrbyfloat` awaits bare, partially missing the frozen §1 B4-timeout Must; caught by the pre-gate advisor review, not by tests (no test asserted the advisory-call timeout). Evidence: recorder.py advisory block. [folded foundation-version 49]
- [TDD · folded] "best-effort" and "bounded" are orthogonal properties — a call can be both. The §3 pseudocode's "advisory stays best-effort" was mis-read as "advisory is timeout-exempt". Add a scenario/test that asserts a hung advisory call is bounded (not just swallowed) so this can't regress silently. Evidence: the gap was invisible to the green suite. [folded foundation-version 49]
