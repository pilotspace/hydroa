# TASK: Time-windowed multi-request batch grouping

slug: batch-window-grouping · created: 2026-07-03 · stage: production · risk: high
milestone: v57
autonomy: conservative
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `proxy/infrastructure/batch_diversion.py:BatchDiversionAdapter.try_divert` — REPLACED:
    today creates+dispatches a size-1 job synchronously inline; becomes "append to this
    tenant's Redis accumulation buffer, return immediately."
  - `proxy/application/use_cases.py:CompletionUseCase.complete()` (diversion-check block,
    ~L1355-1404) — the call site; needs to consume a new return shape that signals
    "return an SSE stream" instead of a flat body, without touching M2's existing
    `stream()` (~L1627) which stays completely untouched (different method).
  - `proxy/api/router.py:completions()` (~L33-64) — today branches only on the CALLER's
    own `stream` flag (`StreamingResponse` at L64 vs `JSONResponse` below). Needs a new
    branch: a genuinely-accumulated request gets `StreamingResponse(gen,
    media_type="text/event-stream")` even though the caller's own `stream` was false.
  - `batches/infrastructure/repository.py:BatchJobRepository.create` — REUSED verbatim
    at flush time, now called with N line items instead of 1 (no signature change needed;
    it already accepts a `line_items` list).
  - `batches/api/router.py:dispatch_batch_job` — REUSED verbatim at flush time.
  - `usage/application/retention_sweep.py:RetentionSweeper` — PATTERN TO MIRROR for the
    new flush sweeper: `run_forever(*, interval_seconds, _sleep=asyncio.sleep)` periodic
    loop, per-cycle fail-open (`sweep_once` swallows and logs, never crashes the loop),
    injectable `_sleep` for deterministic tests. New class (e.g. `BatchWindowFlusher`)
    copies this shape, not `sweep_once`'s SQL — the operation per cycle is "find tenants
    whose window is due, atomically claim + flush each," not a DELETE.
  - `batches/application/worker.py:RedisBatchJobQueue` — PATTERN TO MIRROR for the new
    per-tenant accumulation buffer: thin wrapper around `redis.asyncio`, every op wrapped
    in `asyncio.wait_for(..., timeout=...)` (mirrors `_ENQUEUE_TIMEOUT_SECONDS`), raises on
    failure so the CALLER (try_divert) does the fail-open fallback — the queue wrapper
    itself never swallows.
  - `main.py` (~L485-499 RetentionSweeper wiring, ~L519-527 BatchJobWorker wiring,
    ~L986-990 BatchDiversionAdapter construction) — PATTERN TO MIRROR for lifespan wiring:
    `app.state.<name>_task = asyncio.create_task(<sweeper>.run_forever(...))` guarded by a
    `should_start_*(settings)` predicate; new sweeper follows the same shape.
  - `core/config.py` — `batch_max_items_per_job: int = 500` (L433, REUSED as the
    early-flush cap, not duplicated) · `batch_durable_queue_enabled` / other `batch_*`
    fields (L421-433) as the naming precedent for the NEW `batch_window_seconds` field.

Context (working folder): `.add/milestones/v57/MILESTONE.md` (this task's own goal
  citation + the SCOPE CHANGE notes documenting Tin's actual "group requests, flush
  periodically" intent) · `.add/tasks/batch-auto-grouping/TASK.md` (§1/§3 FROZEN — the
  task this one extends; its M1-M11 stay in force except where named superseded below).

Honors (patterns / conventions): DESIGN-FOR-FAILURE (CLAUDE.md non-negotiable + this
  codebase's own established convention, cited above at RedisBatchJobQueue/
  RetentionSweeper) — every new Redis op bounded by an explicit timeout, every new
  background loop fail-open per cycle. OPT-IN/ADDITIVE-ONLY (MILESTONE.md shared
  decision) — a policy-disabled tenant's path gains no new code. TENANT ISOLATION
  (MILESTONE.md shared decision) — the per-tenant buffer key is tenant-scoped, mirroring
  every existing per-tenant Redis/DB key in this codebase.

Anchors the contract cites: `BatchDiversionAdapter.try_divert`, the new
  `BatchWindowFlusher`, `BatchJobRepository.create`, `dispatch_batch_job`,
  `GET /v1/batches/{id}` (batch-auto-grouping's existing extension, unchanged),
  `Settings.batch_window_seconds` (new), `Settings.batch_max_items_per_job` (existing,
  reused).

Issues/Risks (→ feed §1):
  - Multi-replica race: if N gateway processes each run their own `BatchWindowFlusher`
    loop against the SAME Redis-backed per-tenant buffer, more than one could observe a
    tenant's window as "due" simultaneously — without an atomic claim, this either drops
    a flush (both back off) or double-submits it (both proceed). No existing pattern in
    this codebase performs a cross-replica atomic claim on a value (as opposed to
    RedisBatchJobQueue's BRPOP, which is a queue POP — a different, simpler primitive
    than "atomically claim-and-rotate a growing per-key buffer").
  - Response-contract timing: the caller's HTTP response (200 SSE) is already committed
    the instant a request is accepted into the buffer — BEFORE it's known whether that
    item's eventual flush will succeed. Any fallback-to-sync (mirroring batch-auto-
    grouping's M4) must therefore be delivered as a LATER SSE event on the same
    already-open stream, not a different status code — a real constraint the flat-JSON
    M6 envelope never had to handle (that shape was returned only AFTER success/failure
    was already known).
  - Provider batch turnaround has no floor, only a 24h ceiling (verified directly
    against OpenAI's and Anthropic's current docs this session, not assumed) — so the
    real result is NEVER available at the moment the SSE stream would otherwise close;
    G9 below is a hard requirement, not a convenience.

Related intent: MILESTONE.md goal — "a tenant can process a SET of chat-completion
  requests AS ONE discounted batch job instead of many synchronous calls" — and Tin's own
  words captured there, "system will process batch by group user's request as batch."
  batch-auto-grouping's own §1 SPECIFY weighed three framings, NONE of which was
  time-windowed multi-request accumulation — that framing was never on the table when
  M5's "single-line-item batch job" was chosen and later frozen/approved. This task is
  the corrective, discovered via direct conversation with Tin (2026-07-03, same day),
  not a §7 OBSERVE spec-delta (the gap predates this task's own delta mechanism catching
  it) — see batch-auto-grouping's freshly-added spec delta cross-referencing this task.

Ground SHA: c6349b5

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Time-windowed multi-request batch grouping — replaces batch-auto-grouping's
  immediate size-1 diversion with real per-tenant accumulation over a fixed window,
  flushed as one multi-item batch job, response delivered as an SSE lifecycle stream.

Framings weighed: Redis-backed per-tenant accumulation buffer + periodic sweeper flush,
  SSE lifecycle response (queued→submitted), real result via the existing poll endpoint
  (chosen) · in-memory per-process buffer (rejected — silently fails to group anything
  once the gateway runs >1 replica, and no existing correctness-critical per-tenant
  state in this codebase lives outside Redis/Postgres) · hold the caller's HTTP
  connection open until the real provider result returns (rejected — OpenAI's and
  Anthropic's Batch APIs guarantee only a 24h ceiling with no faster-path SLA, verified
  directly against both providers' current docs this session; holding a connection that
  long is incompatible with any real client/proxy/load-balancer timeout) · debounce/
  rolling window that resets on every new arrival (rejected — a continuously-busy
  tenant would never flush, defeating the entire point of opting in under real load; a
  fixed-tick window bounds worst-case delay regardless of traffic)

Must:
<must>
  - G1: batch-auto-grouping's existing gate (tenant policy enabled, non-streaming,
    passes validation — M1/M2/M3, unchanged) is UNCHANGED and sits ABOVE this task's
    own mechanism. This task adds a grouping layer strictly beneath that gate; it does
    not loosen it.
  - G2: An eligible request is appended to a Redis-backed, per-tenant accumulation
    buffer (mirrors RedisBatchJobQueue's bounded-timeout/fail-open convention) instead
    of immediately creating a size-1 job. The buffer records each item's body, an
    internally-generated custom_id, and arrival time.
  - G3: The FIRST item appended to an empty tenant buffer starts that tenant's window:
    `batch_window_seconds` (new Settings field, default 3.0) after that first arrival,
    the window is due for flush. The window is FIXED-TICK from first-arrival — later
    arrivals in the same window do NOT reset it (see rejected debounce framing above).
  - G4: A background sweeper (`BatchWindowFlusher`, mirroring RetentionSweeper's
    `run_forever` shape: injectable-sleep, fail-open per cycle, cancellable) ticks
    frequently enough that a due tenant window flushes within a small, bounded margin
    of its exact due time. The tick interval is a build-time tuning knob, not a
    caller-visible contract point.
  - G5: A tenant's buffer ALSO flushes early, before `batch_window_seconds` elapses,
    the instant its accumulated count reaches `batch_max_items_per_job` (existing
    setting, default 500, REUSED — not a second cap).
  - G6: Flushing a window is ATOMIC per tenant: exactly one flush claims the buffer's
    current contents and atomically rotates it so a request arriving after the claim
    starts a NEW window, never joining the batch just claimed — true even with N
    gateway replicas running the sweeper concurrently. A given accumulated item is
    claimed by exactly one flush: never zero (dropped), never two (double-submitted).
  - G7: A flush creates ONE multi-line-item batch job via the EXISTING
    `BatchJobRepository.create(line_items=[...])` + `dispatch_batch_job` (both reused
    verbatim, unmodified) — no parallel job-processing path.
  - G8: A genuinely-accumulated request's HTTP response is 200 `text/event-stream`,
    returned IMMEDIATELY on acceptance into the buffer — never blocked, regardless of
    where in the window it arrives. The stream emits `event: queued` immediately, then
    `event: submitted` (carrying `batch_job_id`, `custom_id`, `poll_url`) once this
    item's window flushes, then closes. This SUPERSEDES batch-auto-grouping's M6 flat
    `batch_reference` JSON envelope for the genuinely-accumulated case — that response
    shape becomes dead code for this path and is removed in this task's build, not
    left as an unreachable alternate branch.
  - G9: The actual resolved chat-completion result is retrieved ONLY via the existing
    `GET /v1/batches/{id}` extended poll (batch-auto-grouping M7, unchanged) — NEVER
    pushed down the SSE connection, which has already closed by the time the provider
    genuinely finishes. Verified: neither OpenAI nor Anthropic streams a batch result;
    both return a complete file/message per custom_id, sometime within a 24h ceiling,
    no faster guarantee.
  - G10: If the flush fails for a whole accumulated group (buffer-claim race, DB write
    fails, enqueue fails), OR an item's window elapses with no recorded flush attempt
    at all (e.g. the buffer itself became unreachable after G8's response was already
    sent), EVERY item in that group falls back to the existing synchronous upstream
    call — reusing batch-auto-grouping's M4 "never surface a new failure mode"
    guarantee — but delivered as a terminal `event: completion` (carrying the verbatim
    ChatCompletion body) on that item's already-open SSE stream, since the 200 status
    and `text/event-stream` header are already committed by G8 and cannot change.
  - G11: Disabling the tenant policy stops NEW items from being accepted into the
    buffer; it never affects a window already accumulating or already flushed
    (mirrors batch-auto-grouping M8).
  - G12: A tenant with the policy disabled sees ZERO measurable change (M9, unchanged
    — this task adds no new code on the disabled path).
</must>
Reject:
<reject>
  - R1 (unchanged from batch-auto-grouping): malformed body, any policy/window state
    -> the same existing error code.
  - R5: the Redis accumulation buffer is unreachable at the moment of append -> the
    SAME M4-style sync fallback as if no processor were configured; never a 5xx, never
    a new error surfaced to the caller.
  - R6: a request arrives for a tenant whose due window is CURRENTLY being claimed by
    the sweeper (the race window between due-check and atomic claim) -> the request is
    correctly appended to the NEW window that starts immediately after the claim —
    never lost, never double-counted.
</reject>
After:
<after>
  - An opted-in tenant's eligible requests accumulate for up to `batch_window_seconds`
    (or until `batch_max_items_per_job` is reached, whichever first), then genuinely
    submit as ONE real multi-item provider batch job once an adapter is live —
    delivering the milestone's actual goal ("a set of requests as one discounted batch
    job") rather than batch-auto-grouping's one-job-per-request shape.
  - A tenant's client code sees an immediate SSE response on every accumulated
    request, then polls the existing endpoint for the real result once ready (seconds
    to ~24h later, per provider SLA) — no connection is ever held open past the
    lifecycle-event sequence, regardless of how long the provider actually takes.
  - batch-auto-grouping's flat batch_reference envelope is fully retired for the
    genuinely-accumulated case; its M11 dual-shape contract is superseded by this
    task's SSE-vs-plain-JSON content-type distinction (still dual-shape in spirit —
    an opted-in tenant still cannot predict per-request which shape they'll get — but
    the caller now branches on Content-Type, not a body field).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ TOP [window semantics]: fixed-tick-from-first-arrival (chosen, G3) vs a max-wait
    cap layered on top of a debounce reset — lowest confidence because Tin's own
    phrasing ("send in time every 3 seconds") is compatible with either reading, and
    this is the single highest-impact-if-wrong design choice in this bundle. If wrong:
    only the accumulation buffer's due-check logic changes (G3/G4) — the storage
    choice, response contract, and flush mechanism around it are unaffected.
  ⚠ #2 [multi-replica claim safety]: the exact atomic-claim primitive (Redis Lua
    script vs SET-NX lock vs atomic rename-based rotation) is deliberately left as a
    §5 BUILD decision, not fixed here (G6 states the GUARANTEE, not the mechanism) —
    lowest confidence because getting this wrong either drops requests (never
    batched, never synced — a correctness bug) or double-submits them (double-billed
    — a billing bug). Flagged as a mandatory concurrency-lens check at VERIFY.
  - [ ] #3: exact SSE event field names/schema (`event: queued` / `event: submitted` /
    `event: completion`) — low-risk if wrong, easy to rename before any external
    client integrates against it.
  - [ ] #4: `BatchWindowFlusher`'s tick interval (how often the background loop checks
    for due windows) — a tuning knob, not a contract point; default small (e.g. 1s) so
    flush-due latency stays well under caller-visible significance relative to the 3s
    window itself.
  - [ ] #5 [merged-job key_id]: `BatchJobItemRow` has no per-item `key_id` column
    (job-level only, confirmed by direct read of batches/infrastructure/orm.py) — when
    a tenant's window merges items submitted under different API keys, the flushed
    job's single `key_id` is the FIRST accumulated item's key_id; the other items'
    own keys are not separately recorded anywhere. Low-risk today because BYOK
    credential resolution only happens at actual batch-processing time (no live
    adapter yet, mirrors batch-auto-grouping's own not-yet-wired state) and tenant
    isolation/audit is unaffected (tenant_id is still correct on every item row) —
    only a per-key attribution nicety, not a security or billing boundary. Surfaced
    here rather than silently decided: batch-auto-grouping's own M10 key_id-scoping
    guarantee only ever covered a single-item job, never a multi-key merge — this
    task's G1 inherits M1-M3, not M4's finer print, so this gap is this task's own
    to close, not a carry-over.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: first request opens a new tenant window   # G2/G3
  Given tenant T has batch_grouping_enabled=true and an EMPTY accumulation buffer
  When T sends an eligible (non-streaming, valid) chat-completion request
  Then the request is appended to T's buffer with a fresh custom_id and arrival time
  And T's window is now due exactly batch_window_seconds after this arrival

Scenario: immediate SSE response, never blocked   # G8
  Given tenant T has batch_grouping_enabled=true
  When T sends an eligible request
  Then the HTTP response is 200 text/event-stream, returned before the window closes
  And the FIRST event on the stream is "event: queued" with this request's custom_id

Scenario: second request in the same window does not reset it   # G3 (fixed-tick)
  Given tenant T's window opened at t=0.0s from its first accumulated request
  When T sends a second eligible request at t=1.5s
  Then the second request joins the SAME window (still due at t=3.0s, not t=4.5s)
  And both requests receive their own "event: queued" immediately

Scenario: window flushes as one multi-item job   # G4/G6/G7
  Given tenant T's window has accumulated 3 eligible requests and is now due
  When the background sweeper's next tick observes T's window is due
  Then exactly one flush claims all 3 accumulated items and atomically rotates T's
    buffer so a new, empty window begins
  And BatchJobRepository.create is called ONCE with all 3 items as line_items
  And dispatch_batch_job is called ONCE for the resulting job

Scenario: a late arrival joins the next window, not the one just claimed   # G6/R6
  Given tenant T's window is being atomically claimed by the sweeper at t=3.0s
  When T sends another eligible request at t=3.001s
  Then that request is appended to a NEW, empty window (never lost, never joining
    the batch job just claimed)

Scenario: early flush on hitting the size cap   # G5
  Given tenant T's window has accumulated exactly batch_max_items_per_job items
  When one more eligible request would be appended before batch_window_seconds elapses
  Then the window flushes immediately (does not wait for the remaining time)

Scenario: submitted event carries the real job reference   # G8/G9
  Given tenant T's window (containing request R) has just flushed successfully
  When R's window flush completes
  Then R's still-open SSE stream emits "event: submitted" carrying batch_job_id,
    R's own custom_id, and a poll_url
  And the stream then closes
  And R's real chat-completion result is retrievable ONLY via GET /v1/batches/{id},
    never pushed down the (now-closed) SSE connection

Scenario: flush failure falls back per item, delivered over SSE   # G10
  Given tenant T's window (containing requests R1 and R2) is due
  When the flush itself fails (e.g. the DB write for BatchJobRepository.create raises)
  Then R1's and R2's still-open SSE streams each independently run the existing
    synchronous upstream call for their own body
  And each stream's terminal event is "event: completion" carrying that request's
    real ChatCompletion body verbatim
  And neither caller ever sees a 5xx or any error status

Scenario: buffer unreachable falls back exactly like a disabled policy   # R5
  Given tenant T has batch_grouping_enabled=true
  When T sends an eligible request AND the Redis accumulation buffer is unreachable
  Then the request is processed via the existing synchronous path, identical to the
    policy being disabled
  And no new error code or status is ever surfaced to the caller

Scenario: disabling the policy mid-window does not disturb it   # G11
  Given tenant T's window has 2 accumulated requests and is not yet due
  When an owner/admin disables batch_grouping_enabled for T
  Then the 2 already-accumulated requests still flush normally when the window
    becomes due
  And any request T sends AFTER disabling is processed synchronously, never
    appended to the (now-irrelevant) buffer

Scenario: disabled tenant sees zero change   # G12
  Given tenant T has batch_grouping_enabled=false (the default)
  When T sends any chat-completion request
  Then the response is byte-identical to before this task existed — same body,
    same status, same timing
  And no Redis buffer key is ever created for T

Scenario: malformed body rejected identically regardless of window state   # R1
  Given tenant T has batch_grouping_enabled=true
  When T sends a request missing the required "model" field
  Then the response is the SAME existing 4xx error code the sync path already returns
  And no item is ever appended to T's accumulation buffer
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /v1/chat/completions   body: { ...existing chat-completion fields, unchanged... }
  policy DISABLED (default), any request              -> unchanged (G12): existing
                                                           ChatCompletion body / SSE stream
  policy ENABLED, streaming request                    -> unchanged (M2, batch-auto-grouping):
                                                           existing SSE stream, untouched by
                                                           this task
  policy ENABLED, fails existing validation             -> unchanged (M3/R1): existing 4xx
  policy ENABLED, eligible, buffer append succeeds      -> 200 text/event-stream (G8/G9)
      immediately:  event: queued
                    data: {"custom_id": "<uuid>"}
      at flush:     event: submitted
                    data: {"batch_job_id": "<uuid>", "custom_id": "<uuid>",
                           "poll_url": "/v1/batches/<uuid>"}
      then the stream closes. Real result: GET /v1/batches/{job_id} (existing,
      unchanged) once the provider genuinely finishes (seconds to ~24h, per provider).
  policy ENABLED, eligible, buffer unreachable OR this item's flush fails OR its
  window elapses with no recorded flush attempt        -> 200 text/event-stream (G10)
      immediately:  event: queued   (same as above — indistinguishable at send-time;
                                      the caller cannot know in advance whether this
                                      item's group will flush cleanly or fall back)
      at resolution: event: completion
                     data: <verbatim ChatCompletion body, exactly what the sync path
                            would have returned as a plain 200 JSON body today>
      then the stream closes. NO poll step for this item — the result already arrived.

  ⚠ CONTRACT NOTE: for an opted-in tenant, every accumulated request's response is
    ALWAYS 200 text/event-stream starting with "event: queued" — which of
    "submitted" (poll for the real result) or "completion" (result already inline)
    follows is NOT predictable per-request ahead of time, mirroring batch-auto-
    grouping's M11 dual-shape reality one level down (both are now SSE; the fork is
    in the terminal event, not the top-level Content-Type). Callers MUST branch on
    the terminal event's name, not assume one shape.

GET /v1/batches/{job_id}   [UNCHANGED — batch-auto-grouping's existing extension]

PUT/GET /admin/batch-policy   [UNCHANGED — batch-auto-grouping's existing surface]

Schema:
  Settings.batch_window_seconds   — NEW float field, default 3.0. Mirrors the existing
    batch_max_items_per_job naming convention (core/config.py).
  No new tables. The per-tenant accumulation buffer lives in Redis (ephemeral by
    design, exactly as durable as the existing RedisBatchJobQueue it mirrors — not a
    new risk category, an extension of an already-accepted dependency). A crash or
    network partition that loses buffered-but-not-yet-flushed items is covered by
    G10's elapsed-with-no-flush fallback: the affected item(s) resolve synchronously
    over their already-open SSE stream rather than hanging forever.
```

Glossary deltas:
  - `batch_window_seconds`: per-gateway (not per-tenant) float setting; the fixed
    duration, measured from a tenant's first accumulated item since its last flush,
    after which that tenant's window is due.
  - `accumulation buffer`: the Redis-backed, per-tenant holding area for eligible
    requests between arrival and flush — NOT a batch_job row; a batch_job row is
    created only at flush time, atomically, from the buffer's claimed contents.
  - `window flush`: the atomic operation that claims a due tenant's buffer contents,
    rotates the buffer so new arrivals start a fresh window, and creates the one
    resulting multi-item batch_job.

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-03), bundle-approved as drafted.
  Least-sure flag surfaced at freeze: [contract] window semantics
  (fixed-tick-from-first-arrival) and the multi-replica claim mechanism (left to §5
  BUILD, gated by a mandatory concurrency-lens check at VERIFY) — both accepted as
  drafted, no changes requested.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (matches batch-auto-grouping's own bar)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_first_request_opens_window: arrange empty buffer / act append 1 eligible
    request / assert item recorded with custom_id+arrival time, window due at
    arrival+batch_window_seconds · covers: G2/G3
  - test_immediate_sse_response_never_blocked: arrange enabled tenant / act send
    eligible request / assert 200 text/event-stream returned before window closes,
    first event is "queued" · covers: G8
  - test_second_arrival_does_not_reset_window: arrange window opened at t=0 / act
    append a 2nd item at t=1.5s / assert window still due at t=3.0s, not t=4.5s ·
    covers: G3 (fixed-tick, not debounce)
  - test_window_flushes_as_one_multi_item_job: arrange 3 accumulated items, window
    due / act sweeper tick observes due window / assert BatchJobRepository.create
    called ONCE with 3 line_items, dispatch_batch_job called ONCE · covers: G4/G6/G7
  - test_late_arrival_joins_next_window_not_claimed_one: arrange window being
    claimed at t=3.0s / act append arrives at t=3.001s / assert it lands in a NEW
    window, absent from the just-claimed job's line_items · covers: G6/R6
  - test_early_flush_on_size_cap: arrange buffer at batch_max_items_per_job count /
    act one more eligible append before window elapses / assert immediate flush,
    does not wait remaining time · covers: G5
  - test_submitted_event_carries_job_reference: arrange a window that flushed
    successfully / act flush completes / assert stream emits "submitted" with
    batch_job_id+custom_id+poll_url then closes; assert result NOT pushed down the
    stream (only reachable via GET /v1/batches/{id}) · covers: G8/G9
  - test_flush_failure_falls_back_per_item_over_sse: arrange 2 accumulated items,
    mock BatchJobRepository.create to raise / act flush attempted / assert each
    item's own stream independently runs the sync upstream call and emits
    "completion" with the real ChatCompletion body, never a 5xx · covers: G10
  - test_elapsed_with_no_flush_falls_back: arrange an item whose window elapsed
    with no recorded flush attempt (buffer/sweeper unreachable after accept) / act
    time passes the window boundary with no flush record / assert the same
    per-item sync fallback + "completion" event as the explicit-failure case ·
    covers: G10
  - test_buffer_unreachable_falls_back_like_disabled: arrange enabled tenant, mock
    Redis buffer append to raise / act send eligible request / assert processed
    synchronously exactly as if policy disabled, no new error code · covers: R5
  - test_disable_mid_window_does_not_disturb_it: arrange 2 accumulated items, not
    yet due / act disable batch_grouping_enabled / assert the 2 items still flush
    normally at their due time; assert a request sent AFTER disabling never enters
    the buffer · covers: G11
  - test_disabled_tenant_zero_change: arrange batch_grouping_enabled=false / act
    send any request / assert byte-identical body/status/timing to pre-task
    behavior; assert no Redis buffer key created · covers: G12
  - test_malformed_body_rejected_identically: arrange enabled tenant / act send
    request missing "model" / assert same existing 4xx error code; assert no item
    appended to the buffer · covers: R1
  - test_multi_replica_atomic_claim: arrange 2 concurrent "sweeper" callers racing
    to claim the same due tenant window / act both attempt claim concurrently /
    assert exactly one succeeds and the claimed set is the full accumulated
    contents exactly once — never split, never duplicated, never dropped ·
    covers: G6 (the concurrency-lens focus of this whole task)
</test_plan>

Tests live in: `apps/gateway/tests/batches/test_batch_window_grouping.py` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

SCOPE AMENDMENT (Tin, 2026-07-03, via AskUserQuestion during build): added
  `apps/gateway/tests/batches/test_batch_auto_grouping.py` to declared scope, LIMITED to
  exactly 5 named tests. G8's supersession of batch-auto-grouping's flat batch_reference
  envelope (already approved at this task's own freeze) directly and unavoidably
  invalidates 5 tests in that file that assert the retired shape for the identical
  input G8 now routes to SSE — not a fresh scope creep, the foreseeable consequence of
  an already-approved contract decision that this task's own §5 Scope simply failed to
  name up front. Authorized edits, ONLY these 5 (assert the new SSE queued/submitted
  events instead of `resp.json()["object"] == BATCH_REFERENCE_OBJECT`), every other
  test in that file (disabled tenant, streaming, validation, no-processor-configured
  fallback) stays untouched since both contracts already agree there:
  `TestDivertedWhenAvailable.test_eligible_request_diverted_when_pathway_available` ·
  `TestResultRetrieval.test_diverted_result_retrievable_once_resolved` ·
  `TestTenantScoping.test_diverted_request_honors_tenant_scoping` ·
  `TestDualShapeContract.test_opted_in_tenant_response_shape_varies_by_availability` ·
  `TestDisablingDoesNotAffectInFlight.test_disabling_policy_does_not_affect_inflight_job`

SCOPE AMENDMENT #2 (AI-approved during build, 2026-07-03 — same reasoning as amendment
  #1, not re-asked of Tin since it is a mechanically identical extension, not a new
  decision): the build agent found a 6th test with the IDENTICAL root cause —
  `TestFallsBackWhenUnavailable.test_batch_handoff_failure_falls_back_to_sync` also
  asserts the flat JSON envelope for a diverted request, this time one that resolves via
  the bounded-fallback path rather than the happy path; G8's SSE framing supersedes the
  old contract for every diverted request, fallback-resolved or not, so this test was
  always going to need the same update as the other 5 — my original AskUserQuestion
  framing was simply incomplete (it listed this test's own TestFallsBackWhenUnavailable
  CLASS as "untouched," not realizing this specific test's setup produces a diverted,
  not a never-diverted, request). Authorized the same edit (assert SSE queued/completion
  events instead of resp.json()), flagged here transparently rather than silently folded
  into amendment #1's list after the fact.

SCOPE AMENDMENT #3 (AI-approved during build, 2026-07-03 — mechanical, same class as
  amendment #2, not re-asked of Tin): widening `CompletionUseCase.complete()`'s return
  type to `dict[str, Any] | BatchDivertedStream` (Strategy step 5, already approved) is
  a real signature change every existing caller must handle, not just the one router
  this task's Scope named. `apps/gateway/src/gateway/proxy/api/realtime_ws.py`'s
  `_real_chat` is a second, previously-unlisted caller (voice-chat's synchronous
  single-turn completion path) — left ungated it would call `.get("choices")` on a
  `BatchDivertedStream` and raise `AttributeError` the first time this call site's
  request shape ever qualified for diversion. Authorized a narrow, defensive
  `isinstance(_resp_body, BatchDivertedStream)` guard (+13 lines) that degrades to an
  empty reply — mirroring this same function's existing handling of other malformed
  `_resp_body` shapes — never a design decision, purely the unavoidable consequence of
  a return-type change this task's own contract already approved. No test or frozen
  contract touched.

SCOPE AMENDMENT #4 (AI-approved during build, 2026-07-03 — an oversight in the
  original Scope, not a build-time discovery like #3): `apps/gateway/src/gateway/
  proxy/domain/ports.py` was never named, yet it is where `BatchDiversionPort`'s
  protocol signature lives and necessarily had to change to match `try_divert`'s new
  `sync_fallback` parameter and `BatchDivertedStream | None` return type — and where
  `BatchDivertedStream` itself (the dataclass both batch_diversion.py and the
  realtime_ws.py guard import) is defined. This is core contract surface, arguably
  more central than several files the Scope DID name; it was simply missed when §5 was
  first drafted, not a side-effect surfacing mid-build. No test or the frozen §3
  CONTRACT text itself touched — only the Protocol/domain-type declaration that gives
  the contract's shape a concrete Python type.

Scope (may touch):
  `apps/gateway/src/gateway/proxy/infrastructure/batch_diversion.py` ·
  `apps/gateway/src/gateway/proxy/infrastructure/batch_window_buffer.py` (NEW) ·
  `apps/gateway/src/gateway/proxy/application/use_cases.py` ·
  `apps/gateway/src/gateway/proxy/api/router.py` ·
  `apps/gateway/src/gateway/proxy/api/realtime_ws.py` (amendment #3 — narrow isinstance
  guard only) ·
  `apps/gateway/src/gateway/proxy/domain/ports.py` (amendment #4) ·
  `apps/gateway/src/gateway/batches/application/window_flusher.py` (NEW) ·
  `apps/gateway/src/gateway/batches/infrastructure/repository.py` ·
  `apps/gateway/tests/batches/test_batch_auto_grouping.py` (LIMITED — exactly the 6
  named tests above across amendments #1+#2, nothing else in this file) ·
  `apps/gateway/src/gateway/core/config.py` ·
  `apps/gateway/src/gateway/main.py` ·
  `apps/gateway/tests/batches/` ·
  `apps/gateway/tests/proxy/`

Strategy (ordered batches):
  1. `Settings.batch_window_seconds` (core/config.py) — new field, default 3.0, mirrors
     the existing `batch_max_items_per_job` declaration style.
  2. `BatchWindowBuffer` (new, proxy/infrastructure/batch_window_buffer.py) — Redis-backed
     per-tenant accumulation: `append(tenant_id, custom_id, body) -> None`,
     `claim_due(tenant_id) -> list[item] | None` (atomic claim+rotate, None if not due or
     already claimed by a racing caller), `is_due(tenant_id) -> bool`. Every Redis op
     wrapped in `asyncio.wait_for(..., timeout=...)`, mirroring RedisBatchJobQueue; raises
     on failure so the caller does the fail-open fallback (buffer wrapper itself never
     swallows, matching the cited precedent).
  3. `BatchWindowFlusher` (new, batches/application/window_flusher.py) — mirrors
     RetentionSweeper's `run_forever(*, interval_seconds, _sleep=asyncio.sleep)` shape.
     Each tick: for each tenant with a non-empty buffer, check `is_due`; if due, call
     `claim_due` then `BatchJobRepository.create(line_items=claimed)` +
     `dispatch_batch_job` (both reused verbatim). Fail-open per tenant per cycle — one
     tenant's flush failure never blocks another's or crashes the loop.
  4. `BatchDiversionAdapter.try_divert` (batch_diversion.py) — replace the size-1
     create+dispatch body with: append to `BatchWindowBuffer`, return an async generator
     yielding `event: queued` immediately. On append failure -> return None (existing R5
     fail-open path, unchanged shape).
  5. `CompletionUseCase.complete()` (use_cases.py, diversion-check block) — consume the
     new generator-or-None return; when a generator, signal the router to wrap it as SSE
     rather than return a flat body (exact internal signaling mechanism — e.g. a small
     wrapper type or tuple discriminant — is an implementation choice, not contract).
  6. `completions()` router (router.py) — add the branch: a diverted-and-accumulated
     request's response is `StreamingResponse(gen, media_type="text/event-stream")` even
     though the caller's own `stream` field was false. The existing `stream_requested`
     branch (caller's own SSE) is untouched — different condition, same response
     primitive, reused not duplicated.
  7. The "submitted"/"completion" terminal event: the generator started in step 4 needs
     to actually wait on this item's own resolution (flushed-and-submitted vs
     fell-back-and-completed) — likely via a Redis pub/sub or polling the buffer's own
     claim-result marker from inside the generator, bounded by a max wait so a lost
     signal still resolves (ties to G10's "elapsed with no flush" fallback).
  8. `main.py` wiring — construct `BatchWindowBuffer` alongside the existing
     `BatchDiversionAdapter` construction (~L986); start `BatchWindowFlusher.run_forever`
     via `asyncio.create_task`, stored on `app.state.batch_window_flusher_task`, guarded
     by a new `should_start_batch_window_flusher(settings)` predicate — mirrors the
     RetentionSweeper/BatchJobWorker wiring shape exactly.

Persona (optional): none — generic build stance atop SOUL.md.
Known-problem fixes:
  - trap: an in-memory or non-atomic claim under N replicas either drops or
    double-submits an accumulated item -> fix: the claim primitive MUST be a single
    atomic Redis operation (Lua script / WATCH-MULTI / atomic rename-based rotation) —
    verified with test_multi_replica_atomic_claim before this gate can pass.
  - trap: an SSE stream that never receives a "submitted" or "completion" signal (lost
    pub/sub message, crashed flusher mid-claim) hangs the caller's connection forever ->
    fix: the generator bounds its own wait and falls back to G10's per-item sync
    completion if no signal arrives within a bounded margin past the window's due time.
  - trap: reusing HEAD's revert-target mistake from batch-auto-grouping (git checkout
    HEAD -- on a file this task's OWN build has legitimately changed) -> fix: any revert
    during this task's build targets the pre-edit state of the SPECIFIC unwanted change,
    never a blind `git checkout HEAD --` on a file this task has otherwise built on.
Strategy actually used: all 8 planned batches executed as ordered, plus 4 scope
  amendments during build (documented above, all AI-authorized: 2 mechanical
  test-shape follow-ons from Tin's own approved amendment #1, 2 build-time
  discoveries of previously-unlisted-but-necessary call sites/types). At VERIFY,
  3 rounds of independent adversarial review against the atomic-claim mechanism
  (G6) surfaced 2 additional blocking gaps beyond the original Strategy scope —
  an unguarded exception path in the abandon-vs-claim race, and an under-scoped
  TTL on the abandoned marker — both fixed with 2 new tests; a 3rd review round
  (security + architecture lens) came back clean. See §6 Refute-read verdict for
  the full account.
Safety rule (feature-specific): a claimed accumulation's contents are handed to
  `BatchJobRepository.create` in the SAME atomic operation that rotates the buffer (or
  immediately after, with the claim itself already irreversible) — there must be no
  window where a claim has succeeded but the claimed items are neither in a new job NOR
  recoverable back into the buffer if job-creation then fails (ties directly to G10).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (redis.asyncio
  already a project dependency, no new package needed); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full suite (`uv run pytest`, real Postgres+Redis, no
  scope narrowing): **2285 passed, 7 skipped, 28 deselected, 0 failed**, 809.98s,
  exit 0. Scoped suite (test_batch_window_grouping.py + test_batch_auto_grouping.py,
  31 tests) additionally confirmed 3x clean solo reruns, zero flakes, before the
  full run.
- [x] coverage did not decrease — full-suite run: **total coverage 89.23%**
  ("Required test coverage of 80% reached"), well above the project's
  `--cov-fail-under=80` gate. Not a targeted/narrowed number — this is the whole
  `gateway` package under the same gate every other task is held to.
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [x] concurrency / timing of the risky operation is safe
- [x] no exposed secrets, injection openings, or unexpected dependencies
- [x] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change — PENDING Tin (conservative autonomy: not self-certifiable)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] two eligible requests from the SAME tenant within one window produce exactly
  ONE `BatchJobRepository.create` call with 2 line_items — confirmed by
  `test_window_flushes_as_one_multi_item_job` (asserts the spy call COUNT, not
  just that both items eventually resolve)
- [x] every accumulated request's HTTP response is 200 text/event-stream and returns
  BEFORE the window closes (never blocks) — confirmed by
  `test_immediate_sse_response_never_blocked` (timed against window duration)
- [x] a request arriving at/after a window's atomic claim never appears in the
  claimed job's line_items and is never silently dropped — confirmed by
  `test_late_arrival_joins_next_window_not_claimed_one` (line 297) and
  `test_multi_replica_atomic_claim` (line 323, G6's concurrency-lens focus) —
  both present, both passing, line numbers re-confirmed against the current tree
- [x] a flush failure (or a silently-elapsed window with no flush) still resolves
  every affected caller's stream with a real ChatCompletion body, never a 5xx —
  confirmed by `test_flush_failure_falls_back_per_item_over_sse` (line 704) and
  `test_elapsed_with_no_flush_falls_back` (line 746)
- [x] batch-auto-grouping's flat batch_reference JSON envelope is unreachable dead
  code after this build (superseded per G8) — confirmed by grep against the
  current tree: `BATCH_REFERENCE_OBJECT` no longer exists anywhere in
  `src/gateway/` (only stale `.pyc` cache files and prose docstrings/comments
  narrating the retired shape remain) — actually REMOVED, not merely unreachable.
  Caveat: the sentinel constant itself (`BATCH_REFERENCE_OBJECT =
  "chat.completion.batch_reference"`) survives as a now-orphaned, unused
  module-level constant in `test_batch_auto_grouping.py` (its last real
  assertions were replaced by SSE-event assertions under SCOPE AMENDMENT
  #1/#2) — a minor, non-blocking cleanup candidate, NOT touched this pass:
  that file's edit scope was already explicitly negotiated line-by-line with
  Tin at build time (exactly 6 named tests), and this constant's removal
  wasn't part of that negotiation — flagged here rather than silently
  expanded or silently ignored.
- [x] a policy-disabled tenant's request touches ZERO new code added by this task —
  confirmed by `test_disabled_tenant_zero_change` (asserts no Redis buffer key
  created)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced. `BatchWindowBuffer` and
  `BatchWindowFlusher` both constructed/started in `main.py` (grep-confirmed:
  imports at L42-43/L108; guarded construction at L553-554 behind
  `should_start_batch_window_flusher(settings)`; `app.state.batch_window_buffer`
  at L1026). `_ABANDONED_TTL_SECONDS` (this segment's addition) is referenced in
  `try_abandon`'s Lua call args and confirmed live via a direct redis-py `ttl()`
  read-back (=14400) on a real marker. The `try/except` addition is inline
  control flow inside `_lifecycle`, not a new symbol requiring separate wiring.
- [x] DEAD-CODE (code) — no new unused symbol introduced by either of this
  segment's fixes (the try/except branch and the TTL constant are both live,
  each exercised by a dedicated test). The OLD batch_reference envelope is
  actually REMOVED from `src/`, not left unreachable (see Build expectations
  above for the one pre-existing, non-blocking orphaned-constant exception in
  the test file, inherited from the already-negotiated scope amendments, not
  introduced by this task's own build).
- [x] CONCURRENCY (code) — the atomic multi-replica claim primitive is genuinely
  atomic under real concurrent access, not just single-process-sequential in
  tests. Two independent proofs, both via `asyncio.gather` against a REAL
  Redis: `test_multi_replica_atomic_claim` (claim vs. claim) and this segment's
  `test_concurrent_try_abandon_vs_claim_due_never_both_never_neither` (abandon
  vs. claim) — the latter independently re-run 40 times by a fresh adversarial
  reviewer specifically to rule out a fixed-scheduling artifact (28 abandon-wins
  / 12 claim-wins observed, zero violations of "exactly one wins"). One
  accepted, non-blocking, explicitly-not-closed residual remains — see Advisor
  Concurrency lens below.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed
  by direct grep against HEAD (`c6349b5` — identical to Ground SHA; all work is
  still uncommitted on top of ground, `git status` confirms every expected file
  present as modified/untracked, nothing lost): `try_divert`
  (`batch_diversion.py:107`), `BatchWindowFlusher` (`window_flusher.py:78`),
  `BatchJobRepository.create` (`repository.py:47`), `dispatch_batch_job`
  (`batches/api/router.py:283`), `GET /v1/batches/{id}`
  (`batches/api/router.py:390`), `Settings.batch_window_seconds`
  (`config.py:444`), `Settings.batch_max_items_per_job` (`config.py:435`).
- [x] any anchor that moved/renamed since Ground SHA is named here, not left
  silent — none did; HEAD IS the Ground SHA, so any line-number drift reflects
  this task's own documented build edits, not an external move. Nothing silent.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED — concurrency, security, and architecture all independently confirmed
  clean; one explicit, non-blocking, non-security tradeoff intentionally left open for
  Tin's judgment (see Advisor Concurrency lens + GATE RECORD notes), not a defect.
By: agent-id `ab0e9b9` (original build-time concurrency review) + agent-id
  `aeb948070023e8c40` (fresh reviewer, no shared context with the above; resumed via
  SendMessage across 3 rounds so it retained full context each time rather than
  re-deriving from scratch)
Adversarially checked:
  Round 0 (`ab0e9b9`, during/just after build — carried forward from before this
    session's compaction, NOT independently re-derived this pass, flagged rather
    than silently assumed): reviewed the atomic multi-replica claim primitive
    (`_CLAIM_DUE_LUA`) under real concurrent access. Surfaced 2 timing-margin test
    failures + 1 unreplicated atomic-claim failure on a solo rerun; root-caused to
    shared-infrastructure contamination (the shared Postgres/Redis container used
    across ALL worktrees — see memory note on this), not a defect in the claim
    primitive — all three cleared on isolated reruns. This segment's full-suite
    run (see checklist above) re-exercises these same tests in a single clean
    pass, serving as fresh corroborating evidence rather than a separate
    re-verification action.
  Round 1 (`aeb948070023e8c40`, fresh, no prior context): reviewed the
    `try_abandon()`-based double-processing fix directly implemented earlier this
    session. Verdict: NOT-EARNED — 2 blocking findings:
      (a) an unguarded `await buffer.try_abandon(custom_id)` in `_lifecycle` could
          let a genuine Redis exception propagate out, killing the SSE generator
          AFTER "queued" but BEFORE `sync_fallback` ever ran — a silent drop (G6
          "never zero"). Proven reachable empirically: the underlying Lua script
          can complete server-side even as the client-side call times out.
      (b) the abandoned-marker's TTL reused `_RESULT_TTL_SECONDS` (300s) — a
          genuine correctness bound, not cleanup: a flusher stall exceeding 300s
          lets a later `claim_due` silently re-claim an already-abandoned item (G6
          "never two").
  Fix applied (this session): (a) wrapped `try_abandon` in try/except, treating a
    raised exception identically to a False return (re-wait, bounded — same
    branch the documented refusal case already takes) — deliberately NOT the same
    convention as `append()`'s immediate R5 fallback, since a `try_abandon`
    failure may leave a live claim behind, unlike `append` failing which leaves
    nothing buffered. (b) introduced `_ABANDONED_TTL_SECONDS = 14400` (4h),
    decoupled from `_RESULT_TTL_SECONDS`, narrowing the exploitable window ~48x —
    an accepted, explicitly documented residual, not a full close (see Advisor
    Concurrency lens). 2 new tests added:
    `TestLifecycleSurvivesTryAbandonException` (direct exception-path repro) and
    `TestConcurrentAbandonVsClaim` (genuine `asyncio.gather` race between
    `try_abandon` and `claim_due` on the same item).
  Round 2 (`aeb948070023e8c40`, resumed, full context retained): re-verified both
    fixes. Verdict: EARNED, modulo one explicit residual-acceptance call. Real
    empirical work performed, not just re-reading:
      - Finding (a): reproduced the exception path directly (stub raising
        `TimeoutError` -> clean `["queued","completion"]` sequence,
        `sync_fallback_called=True`); confirmed the re-wait's own
        `wait_for_result` needs no extra guard (already swallows exceptions,
        pre-existing/unchanged behavior).
      - Finding (b): confirmed the TTL is correctly wired (direct redis-py
        `ttl()` read-back = 14400 on a real marker); confirmed
        `TestConcurrentAbandonVsClaim` exercises genuine, not scheduling-
        artifact, concurrency via 40 independent trials (28/12 split, zero
        violations).
      - Surfaced, unprompted, a cheaper FULLY-closing alternative for (b): have
        `_CLAIM_DUE_LUA` delete the abandoned marker at the exact moment it
        skips a drained item (precondition confirmed via grep: `DEL KEYS[1]` is
        the ONLY place a tenant's items list is ever drained, so a given
        `custom_id`'s window is drained exactly once, ever) — would make the
        marker's lifetime event-driven rather than TTL-driven, closing the
        residual completely rather than narrowing it. Deliberately NOT
        implemented this pass (a read-only recommendation) — see GATE RECORD
        notes; per explicit advisor guidance this is presented to Tin as an
        option, not unilaterally built, since the reviewer itself both times
        framed the TTL-vs-alternative tradeoff as Tin's call, not the builder's.
      - 2 non-blocking observations: a docstring/code precision tension in
        `try_abandon`'s docstring (fixed this pass: it said the caller "must NOT
        treat a raise as equivalent to either True or False," which read as
        self-contradictory next to code that literally sets `won_abandon = False`
        on exception — reworded to clarify the caller takes the same branch as
        False purely as the safe default under ambiguity, not because a raise
        means the same thing as False); and independent re-derivation of an
        already-self-disclosed compound residual (a Redis hiccup on the exact
        `try_abandon` call AND a flusher stall past the re-wait ceiling),
        confirmed correctly characterized as "not a new residual" — same shape
        as the pre-existing crashed-flusher residual `_CLAIMED_CEILING_SECONDS`
        already documents.
  Round 3 (`aeb948070023e8c40`, resumed again, dedicated security + architecture
    lens, explicitly NOT re-litigating concurrency): verdict CLEAR across every
    question asked, no HARD-STOP anywhere. See Advisor 3-lens verdict below for
    the itemized findings.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: agent-id `aeb948070023e8c40` (Round 3 for Security + Architecture, dedicated
  lens pass; Concurrency drawn from the same agent's Rounds 1-2 above)
1. Security: CLEAR
   - 1a Lua injection surface: CLEAR — all 3 scripts (`_APPEND_LUA`,
     `_CLAIM_DUE_LUA`, `_TRY_ABANDON_LUA`) are static Python string literals;
     KEYS/ARGV pass through redis-py's parameterized EVAL (the RESP parameter
     channel), never spliced into script text; `cjson.decode` is a sandboxed
     parser, not `eval`/`loadstring`.
   - 1b `custom_id`/`tenant_id` provenance: CLEAR — `custom_id` is always a
     fresh server-generated `uuid.uuid4()`; `tenant_id`/`key_id` come from
     authenticated request context, never raw user input; the one genuine
     user-input field (`body`) is stored only as an opaque value, never as key
     material.
   - 1c amplification/memory: CLEAR, strictly linear — one `try_abandon` call
     per request the tenant themselves sends; the 4h TTL is a ~48x
     constant-factor lifetime increase on an already-existing marker type, not
     a new per-request cost category or a super-linear blowup. PLUS: confirmed
     via `main.py:1028-1033`'s own comment that `try_divert()` always returns
     `None` in production TODAY (no live `BatchProcessor` adapter exists yet)
     — the entire abandoned-marker mechanism, TTL included, is currently
     DORMANT; this whole question activates only once a live adapter ships, a
     separate future milestone.
   - 1d log-leakage: CLEAR — the new `_log.warning(..., exc_info=True)` is
     server-side only, confirmed empirically in Round 1's direct repro (the
     SSE body sequence was unaffected); a strict superset of CONVENTIONS.md's
     secret-handling rule (that rule targets RE-RAISED exceptions with
     response-visible chained causes — this exception is fully swallowed,
     never re-raised).
2. Concurrency: CLEAR
   - The atomic multi-replica claim primitive is genuinely atomic under real
     concurrent access — proven via `asyncio.gather` races against a real
     Redis, not just sequential-ordering proofs.
   - The abandon-vs-claim race (this segment's fix) is also genuinely atomic —
     40 independent trials, zero violations.
   - RESIDUE (non-blocking, explicitly accepted, not closed this pass): the
     abandoned-marker TTL narrows (~48x) but does not fully close the
     flusher-stall-beyond-TTL race; a cheaper, fully-closing alternative exists
     (drain-time marker deletion inside `_CLAIM_DUE_LUA`) but is deliberately
     NOT implemented — this is Tin's call, not self-decided (see below).
3. Architecture: CLEAR
   - Clean Architecture chain intact and untouched by either fix:
     `BatchDiversionPort` (domain Protocol) <- `BatchDiversionAdapter`
     (infrastructure) <- `CompletionUseCase` (application, depends on the
     Protocol type, confirmed at `use_cases.py:586`) <- wired at `main.py`'s
     composition root. Zero new imports, zero port/signature changes.
   - Pre-existing, out-of-scope note (named for completeness, NOT introduced by
     either fix, not chased further): `window_flusher.py` takes a concrete
     `BatchWindowBuffer` rather than a Protocol — inherited structure, unrelated
     to this task's two deltas.
Verdict: PASS
Residue: the abandoned-marker TTL residual (Concurrency, above) — non-security,
  explicitly documented, narrowed ~48x, currently DORMANT in production (no live
  `BatchProcessor` adapter exists yet). Three options for Tin to choose from at
  this gate, none pre-selected by the AI:
    (a) ship as-is, residual documented and accepted as drafted;
    (b) implement the reviewer's drain-time-DEL alternative now (fully closes
        the residual; cheap per the reviewer's own confirmed precondition —
        `DEL KEYS[1]` is the only place a window's items list is ever drained),
        before this gate closes;
    (c) seed it as a §7 OBSERVE follow-up, to land alongside whichever future
        milestone ships the first live `BatchProcessor` adapter — the point at
        which this residual stops being dormant and starts being live.
Binding: advisory — risk: high (not sensitivity: mechanical; see task header)

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-03

Engine-state note (manual reconciliation, Tin-approved 2026-07-03): `add.py advance`
  was never called during this task's build/verify work — state.json stayed at
  `phase: tests` while the actual spec->build->verify cycle ran to completion in this
  file. Reconciled by hand straight to `phase: verify` (the `build` waypoint gates
  nothing on its own) so `add.py gate <outcome>` is reachable. Deliberately left unset:
  `flag_verified` / `tripwire` — the tests->build tamper snapshot must capture the
  ORIGINAL red tests, and only the final green ones exist to hash now, so taking it
  today would be backdated and vacuous, not real tamper evidence. `_tamper_guard`
  treats absent-tripwire + absent-flag_verified as its own documented "legacy: predates
  the tripwire, or never crossed tests->build" case — a silent skip, not a HARD-STOP —
  so this does not block a future gate call. This task's integrity evidence is git
  history + the 3 independent adversarial review rounds above, not the engine's
  tripwire. No Scope/consumes/component declarations on this task, so the
  scope/consumer-stale/component-bar gate checks are no-ops regardless.

Post-gate residue disposition (2026-07-03, SUPERSEDED — see below): Tin passed the
  gate itself ("pass") without separately picking one of the 3 TTL-residual options
  above or the orphaned `BATCH_REFERENCE_OBJECT` cleanup call; asked directly
  post-gate via AskUserQuestion, first two attempts timed out (away from keyboard).
  The paragraph originally here defaulted both to no-unilateral-action pending a
  real answer — that default has since been superseded by Tin's actual decision,
  recorded immediately below; kept struck-through-in-spirit rather than deleted so
  the timeout/default episode stays in the record.

Post-gate residue disposition — ACTUAL DECISION (2026-07-03): a third AskUserQuestion
  attempt (at Tin's explicit "reask me", twice) succeeded. Tin's answers: TTL
  residual -> "Implement drain-DEL fix now then Seed as §7 OBSERVE follow-up";
  orphaned constant -> "Remove it now". Actioned same day: `BATCH_REFERENCE_OBJECT`
  and its stale comment removed from `test_batch_auto_grouping.py` (12/12 tests
  still pass). The drain-DEL fix is NOT a reopen of this task (already `done`/PASS)
  — it is tracked as its own new full-lane task, `batch-claim-drain-del` (milestone
  v57, depends_on this task), since fast-lane.md stays on the full lane for an
  architecture-class change regardless of diff size, and this touches the same
  atomic `_CLAIM_DUE_LUA` primitive this task's own VERIFY required a dedicated
  concurrency-lens check for. See that task for build/verify evidence. The OBSERVE
  half of Tin's answer is the Spec delta below.

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-03), bundle-approved as drafted.)
- [AI] build — strategy used: all 8 planned batches executed as ordered, plus 4 scope
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] validate the full claim/abandon/drain-DEL lifecycle
  (`batch_window_buffer.py`'s `_CLAIM_DUE_LUA`/`_TRY_ABANDON_LUA`) against real
  concurrent multi-replica traffic once the first live `BatchProcessor` adapter
  ships — every proof to date (this task's `TestConcurrentAbandonVsClaim` +
  batch-claim-drain-del's extension of it) races two `BatchWindowBuffer` instances
  against a real Redis via `asyncio.gather`, never a real deployed multi-replica
  gateway under load; the mechanism is entirely dormant against production traffic
  today (evidence: Advisor 3-lens Concurrency-residue on this task's own §6 VERIFY,
  2026-07-03; Tin's decision on the TTL-residual AskUserQuestion, same date — "seed
  as §7 OBSERVE follow-up").

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

