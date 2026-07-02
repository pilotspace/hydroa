# TASK: B4+B5: usage recorder durable fallback + Redis timeout + PEL reclaim

slug: usage-flusher-durability · created: 2026-07-02 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
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

Scope (may touch): `apps/gateway/src/gateway/usage/application/recorder.py` · `apps/gateway/src/gateway/usage/application/flusher.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/tests/usage/` · `apps/gateway/tests/ops/`
Strategy (ordered batches): 1. add `_USAGE_REDIS_TIMEOUT_SECONDS` const + `GATEWAY_USAGE_PEL_RECLAIM_IDLE_MS` Settings knob. 2. B4: generate explicit id in record() normal path (write event_fields["id"]); wrap Redis calls in asyncio.timeout; add `_fallback_insert` (shared INSERT helper, ON CONFLICT id) invoked on XADD failure — all inside the existing swallow. 3. B5: add XAUTOCLAIM step at the top of flush_once() feeding _process_entry. 4. tests (extend FakeRedisStream with xautoclaim).
Known-problem fixes: double-billing → the SAME deterministic id on XADD-path and fallback-path (flusher already prefers explicit id + ON CONFLICT) · invariant-break → fallback INSIDE the existing try/except so a Postgres-also-down still swallows · premature-steal → min_idle_time threshold > a flush cycle · shared-client-blast-radius → per-call asyncio.timeout, not client socket_timeout · fake/prod xpending shape mismatch (flusher.py:308) — fix the fake while adding xautoclaim.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
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
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
