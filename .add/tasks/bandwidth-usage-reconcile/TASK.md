# TASK: Estimate-to-real-usage reconciliation + non-stream charge

slug: bandwidth-usage-reconcile · created: 2026-06-24 · stage: production
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
  CONSUMES (frozen by task 1):
  - `rate_limits/infrastructure/redis_token_bucket.py:RedisTokenBucket.reconcile(key_id, grant,
    real_tokens)` (210) — applies signed delta = grant.consumed − real_tokens to the level (Lua
    _RECONCILE clamped [−burst,burst]); FIRE-AND-FORGET (swallows Redis errors, never re-raises);
    `rate≤0 ⇒ no-op`. Net effect: a prior estimate debit of E then reconcile(E, real) ⇒ net = real.
  - `rate_limits/domain/ports.py:BandwidthGrant(key_id, consumed:int, waited_s:float)` — `.consumed`
    is the ESTIMATE charged at acquire. `PassthroughBandwidthBucket.reconcile` = no-op.

  CHANGES (this task owns — proxy/application/use_cases.py):
  - ADD module helper `_fire_bandwidth_reconcile(bucket, key_id, estimate, real_tokens)` — mirrors
    `_fire_record_tpm` (170): asyncio.ensure_future(bucket.reconcile(key_id, BandwidthGrant(...),
    real_tokens)) + done-callback swallow. Schedules ONLY when estimate>0 and real_tokens>0.
  - STREAM `_wrapped`: accumulate `_bw_estimate_total` in the pacing loop (the per-chunk
    max(1,len//4) I already pass to acquire — task 2 @1660-1663) and at the CLEAN post-loop close
    (1887-1891, beside the TPM fire) reconcile against extracted_usage total_tokens. ALSO in the
    DISCONNECT handler (1745) reconcile against the partial `disconnect_usage` total_tokens (Tin
    2026-06-24). NOT on the shed branch (1664) — that over-budget debit stands.
  - NON-STREAM `complete`: after the real usage is known (1369-1373, beside the TPM fire) reconcile
    the pre-flight `_bw_estimate` (task 2 @~905) against usage total_tokens.
  - GATE both fires on pacing being ACTIVE: `not isinstance(self._bandwidth_bucket,
    PassthroughBandwidthBucket)` — default-OFF ⇒ no estimate accrual, no reconcile task ⇒ byte-identical.
  - import `BandwidthGrant` (BandwidthBucket already imported by task 2).

  READ (mirror — NOT changed):
  - `_fire_record_tpm` (170-181) — the exact fire-and-forget shape to copy.
  - the stream post-loop `extracted_usage`/`total_tokens` (1867-1891) + non-stream `usage`/
    `total_tokens` (1357-1373) — where the REAL token count is known at close.

Context (working folder):
  - REAL tokens come from the usage frame (stream) / response body (non-stream) — the v27/v35
    authoritative source. total_tokens absent/0 ⇒ skip reconcile (no truth to correct toward; the
    estimate debit stands, same as a missing-frame $0 is flagged not invented).
  - Net-consumption proof: pacing debited Σ estimates = E; reconcile delta = E − real; Lua adds delta
    to level ⇒ level rises by (E−real) ⇒ net consumed = E − (E−real) = real. Correct even though the
    TTFB chunk is unpaced (E excludes it; we reconcile only what was actually debited).
  - No migration. No new config. No new endpoint. Closes the v36 milestone goal (estimate→truth).

Honors (patterns / conventions):
  - Fire-and-forget at close, never blocks the response, swallows all errors (mirror _fire_record_tpm).
  - Default-OFF byte-identical: Passthrough ⇒ gate false ⇒ zero new behavior.
  - reconcile is idempotent-safe per request (one fire per close); only the shed debit is final.

Anchors the contract cites (§3 may name ONLY these):
  - `_fire_bandwidth_reconcile` helper · RedisTokenBucket.reconcile · BandwidthGrant · the stream
    clean-close reconcile point (post-loop, beside TPM) · the non-stream reconcile point (beside TPM)
    · the PassthroughBandwidthBucket active-gate · `_bw_estimate_total` accumulator.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Reconcile the bandwidth ESTIMATE debited during pacing/pre-flight to the REAL usage at close
Framings weighed:
  - Reconcile on CLEAN close only, fire-and-forget (CHOSEN) — at the post-stream / post-response point
    where the authoritative usage frame is known; mirrors the existing _fire_record_tpm. Shed and
    disconnect debits are FINAL (those requests were over-budget / partial — no truth to reconcile to).
  - Reconcile everywhere incl. shed/disconnect — rejected: the real total is unknown/partial on those
    paths; correcting toward a partial would under-charge a request that was deliberately shed.
  - Capture each acquire() grant and reconcile per-chunk — rejected: needless churn; one net reconcile
    per request (Σ estimate vs real total) is exact and cheap.
Must:
<must>
  - STREAM clean close: accumulate `_bw_estimate_total` = Σ of the per-chunk estimates passed to
    acquire (paced chunks only; TTFB unpaced ⇒ excluded). At the post-loop close, when extracted_usage
    total_tokens is a positive int, fire `bucket.reconcile(key_id, BandwidthGrant(consumed=
    _bw_estimate_total), real_tokens=total_tokens)` — fire-and-forget, beside the TPM fire.
  - NON-STREAM: after the response usage is known, fire reconcile of the pre-flight `_bw_estimate`
    against usage total_tokens (fire-and-forget, beside the TPM fire).
  - DISCONNECT (Tin 2026-06-24): when the client drops, ALSO reconcile `_bw_estimate_total`
    (accumulated up to the drop) against the disconnect's PARTIAL usage total_tokens, when that
    partial total is a positive int — corrects the bucket toward the tokens actually generated.
    Fired in the disconnect handler, beside the disconnect record.
  - Net bucket consumption after reconcile == the known real total_tokens (delta = estimate − real).
  - Fire ONLY when pacing is ACTIVE (bucket is not PassthroughBandwidthBucket) AND estimate>0 AND
    total_tokens>0. Otherwise no reconcile task is scheduled.
  - Fire-and-forget: reconcile never blocks the response and never raises (the bucket swallows Redis
    errors; the scheduling is guarded like _fire_record_tpm).
</must>
Reject:
<reject>
  - total_tokens absent / 0 / non-int (no authoritative truth, incl. a disconnect with no partial
    usage) -> NO reconcile (the estimate debit stands)
  - request was SHED mid-stream (BandwidthExhaustedError) -> NO reconcile (deliberately over-budget;
    the debit is final) — distinct from a disconnect, which DOES reconcile toward its partial usage
  - pacing disabled (PassthroughBandwidthBucket) -> NO reconcile, NO estimate accrual (byte-identical)
</reject>
After:
<after>
  - On a clean enabled request the bucket level reflects the REAL tokens (estimate corrected by the
    signed delta), so the NEXT request for that key paces against true remaining capacity.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The fire-and-forget reconcile task completes before a rapid NEXT request for the same key reads
    the bucket — lowest confidence because ensure_future runs after the response returns; a back-to-back
    request could pace against the un-reconciled (estimate-debited) level for a few ms. COST: a brief
    over/under-pace window that self-heals on the next reconcile; ACCEPTED — reconcile is a correction,
    not a barrier (same eventual-consistency the TPM fire-and-forget already accepts).
  - [x] reconcile no-ops when rate≤0 — confirmed (task-1 reconcile guards `if self._rate<=0: return`).
  - [x] net consumption = real after delta — confirmed by the algebra in §0 Context.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: stream over-estimate is refunded at clean close
  Given an enabled bucket and a stream whose paced chunks debit Σ estimate E, real total_tokens R < E
  When the stream completes cleanly (usage frame present)
  Then reconcile(consumed=E, real=R) is fired and the bucket net consumption == R (refund of E−R)

Scenario: stream under-estimate is debited at clean close
  Given paced Σ estimate E, real total_tokens R > E
  When the stream completes cleanly
  Then reconcile(consumed=E, real=R) is fired (extra debit of R−E); net consumption == R

Scenario: non-stream pre-flight estimate reconciled to real
  Given a non-streaming completion that pre-flight-debited estimate E_pf, response total_tokens R
  When complete() returns 200
  Then reconcile(consumed=E_pf, real=R) is fired beside the TPM record

Scenario: missing usage frame → no reconcile   # REJECTION
  Given a stream whose usage frame is absent (total_tokens missing/0)
  When the stream completes
  Then NO reconcile is fired (the estimate debit stands) and the response is unchanged

Scenario: shed → no reconcile   # REJECTION
  Given a stream that sheds mid-way (BandwidthExhaustedError)
  When the terminal ERR_BANDWIDTH_EXHAUSTED frame fires
  Then NO reconcile is fired (deliberately over-budget; the debit is final) and billing is unchanged

Scenario: disconnect with partial usage → reconcile toward the partial
  Given an enabled stream the client drops after some chunks, with a partial usage total_tokens P
  When the disconnect handler fires its record
  Then reconcile(consumed=_bw_estimate_total, real=P) is fired (corrects the bucket toward P)

Scenario: disconnect with no usage → no reconcile   # REJECTION
  Given a disconnect where no partial usage total is available (total_tokens absent/0)
  When the disconnect handler fires
  Then NO reconcile is fired (the estimate debit stands) and billing is unchanged

Scenario: pacing disabled is byte-identical   # REJECTION
  Given the default PassthroughBandwidthBucket (pacing off)
  When a stream or non-stream completes
  Then NO estimate is accrued, NO reconcile task is scheduled, output byte-identical to today
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# No HTTP surface change. Internal close-path wiring in CompletionUseCase (use_cases.py).
# The observable contract is the bucket-state correction + the fire-and-forget reconcile call.

# --- new module helper ---
_fire_bandwidth_reconcile(bucket: BandwidthBucket, key_id: uuid.UUID, estimate: int,
                          real_tokens: int) -> None
  # schedules asyncio.ensure_future(bucket.reconcile(key_id,
  #     BandwidthGrant(key_id=key_id, consumed=estimate, waited_s=0.0), real_tokens))
  # + done-callback that swallows the task exception. NO-OP guard: returns without scheduling
  # when estimate <= 0 or real_tokens <= 0. Mirrors _fire_record_tpm exactly.

# --- STREAM (_wrapped) ---
  _bw_estimate_total: int  # accumulated += max(1, len(chunk)//4) for each PACED chunk
  # at the CLEAN post-loop close (beside the TPM fire), when extracted_usage total_tokens is a
  # positive int AND pacing is active (not Passthrough):
  #   _fire_bandwidth_reconcile(self._bandwidth_bucket, key_id, _bw_estimate_total, total_tokens)
  # NOT fired on the shed branch or the disconnect handler.

# --- STREAM disconnect handler (Tin 2026-06-24) ---
  # beside the disconnect record, when disconnect_usage total_tokens is a positive int AND active:
  #   _fire_bandwidth_reconcile(self._bandwidth_bucket, key_id, _bw_estimate_total, <partial total>)
  # NOT fired on the shed branch.

# --- NON-STREAM (complete) ---
  # beside the TPM fire (after usage total_tokens known) AND pacing active:
  #   _fire_bandwidth_reconcile(self._bandwidth_bucket, authz.key_id, _bw_estimate, total_tokens)

# --- active gate ---
  _bw_active = not isinstance(self._bandwidth_bucket, PassthroughBandwidthBucket)

Schema: no DB, no Redis key shape change (reuses task-1 reconcile Lua on bandwidth:bucket:{key_id}).
        New import: BandwidthGrant. WRITES (via reconcile): the bandwidth:bucket level only.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-24): reconcile on clean close AND on disconnect (toward
partial usage), fire-and-forget; shed is the only path that does NOT reconcile
Least-sure flag surfaced at freeze: [spec] reconcile is FIRE-AND-FORGET (ensure_future after the
response returns) — a back-to-back request for the same key could pace against the un-reconciled
estimate-debited level for a few ms. WHY least-sure: it is the one place the correction is eventually-
consistent, not synchronous. COST if wrong: a brief over/under-pace window that self-heals on the next
reconcile — ACCEPTED (same model as the existing _fire_record_tpm; a synchronous reconcile would add a
Redis RTT to every response close for no real gain). [scenario] shed/disconnect deliberately do NOT
reconcile (partial debit final) — confirmed correct since the real total is unknown on those paths.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (gateway floor). Unit-level, mirroring tests/stream_bandwidth_pacing harness:
a RecordingBandwidthBucket (captures reconcile(key_id, grant, real) calls) + the streaming_resilience
fakes. Drives uc.stream()/uc.complete() and asserts the reconcile call (or its absence).
<test_plan>
  - test_stream_overestimate_refunds: paced E > real R → one reconcile(consumed=E, real=R)
  - test_stream_underestimate_debits: paced E < real R → one reconcile(consumed=E, real=R)
  - test_nonstream_preflight_reconciled: complete() → reconcile(consumed=E_pf, real=R), R from body usage
  - test_missing_usage_no_reconcile: stream with no usage frame → ZERO reconcile calls
  - test_shed_no_reconcile: mid-stream shed → ZERO reconcile calls (debit final)
  - test_disconnect_reconciles_partial: disconnect WITH partial usage P → one reconcile(consumed=E, real=P)
  - test_disconnect_no_usage_no_reconcile: disconnect with no partial usage → ZERO reconcile calls
  - test_disabled_passthrough_no_reconcile: default Passthrough → ZERO reconcile, output byte-identical
</test_plan>

Tests live in: `apps/gateway/tests/bandwidth_usage_reconcile/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py`
Strategy (ordered batches):
  1. import BandwidthGrant; add the `_fire_bandwidth_reconcile` module helper (mirror _fire_record_tpm).
  2. STREAM: init `_bw_estimate_total = 0` + `_bw_active` gate (beside `_bw_shed_handled`), `+=` each paced estimate, fire reconcile at the clean post-loop close AND in the disconnect handler (toward partial usage); gated active + total_tokens>0.
  3. NON-STREAM: fire reconcile beside the complete() TPM record (gated active + total_tokens>0).
Safety rule (feature-specific): fire-and-forget only (never await on the response path, never raise);
  reconcile on clean close AND disconnect (toward partial), NOT on shed; gate on active bucket so
  default-OFF is byte-identical; the disconnect reconcile sits AFTER the _bw_shed_handled early-return.
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

- [x] all tests pass — reconcile suite 8/8; streaming-path regression 53/53; full suite (run below)
- [x] coverage did not decrease — gateway floor held (full-suite run)
- [x] no test or contract was altered during build — §3 frozen untouched; one test STRENGTHENED per refute-read (pin absolute estimates independently of the fake's record), then re-crossed
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) = UPHOLD, 0 blockers. Verified: reconcile sign arithmetic (net = real), accrual after acquire-success (shed excluded), clean-close/disconnect/shed mutually exclusive (no double-fire), _bw_shed_handled gates disconnect reconcile, fire-and-forget exception-retrieved, _bw_estimate always bound at the reconcile point. 1 MAJOR (tests recomputed expected from the fake) → FIXED by pinning absolute integers.
- [x] concurrency / timing safe — fire-and-forget (ensure_future + done-callback), never awaited on the response path, never raises; reconcile self-heals (eventual-consistent, accepted per §1 ⚠)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; reconcile carries only key_id + integer counts
- [x] layering & dependencies follow CONVENTIONS.md — helper mirrors _fire_record_tpm; reuses task-1 reconcile port; no new surface
- [ ] a person reviewed and approved the change — PENDING Tin (commit/PR held)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] STREAM clean close fires reconcile(consumed=Σ paced estimate, real=total_tokens) — confirmed by test_stream_reconciles_estimate_to_real (absolute integers pinned) + under-estimate variant
- [x] NON-STREAM fires reconcile(pre-flight estimate, body total_tokens) — confirmed by test_nonstream_preflight_reconciled
- [x] DISCONNECT fires reconcile toward PARTIAL usage when present; NOT when absent — confirmed by test_disconnect_reconciles_partial + test_disconnect_no_usage_no_reconcile
- [x] SHED and missing-usage fire NO reconcile — confirmed by test_shed_no_reconcile + test_missing_usage_no_reconcile
- [x] Default-OFF byte-identical (no reconcile task) — confirmed by test_disabled_passthrough_no_reconcile (output == plan)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — _fire_bandwidth_reconcile called at 3 live sites (stream clean close, disconnect handler, non-stream complete); _bw_estimate_total accrued in the pacing loop; BandwidthGrant imported + used; all reached by the 8 tests (RED→green)
- [x] DEAD-CODE (code) — no orphaned symbol; the helper + accumulator + gate are all on live paths
- [x] SEMANTIC — n/a (code task)

### GATE RECORD
Outcome: PASS
Evidence: reconcile suite 8/8 · streaming-path regression 53/53 · full gateway suite **1576 passed**,
19 deselected (exit 0, ~4m09s) · ruff + pyright clean (only the 1 pre-existing provider_generation_id
error, unrelated) · refute-read (sonnet) UPHOLD, 0 blockers; 1 MAJOR test-coverage gap FIXED (pin
absolute estimates independent of the fake). Closes the v36 milestone goal (estimate→truth).
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a
Reviewed by: AI auto-gate (autonomy:auto) · human approval (Tin) PENDING for commit/PR · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] reconcile is fire-and-forget ⇒ a back-to-back same-key request can pace against the un-reconciled level for a few ms (evidence: §1 ⚠; self-heals; revisit only if a hot key shows pacing jitter)
- [SPEC · open] the v36 live-verify pass (real Envoy + real key, pacing enabled) is not yet run — the milestone closed on unit/integration green (evidence: no e2e bandwidth harness yet; mirrors the v35 live-verify triad pattern)
- [SPEC · open] chars//4 estimate vs a real tokenizer still governs the PRE-reconcile pacing aggressiveness (evidence: carried task-2 delta; reconcile only fixes the bucket AFTER close, not the in-flight pacing)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] a fake that RECORDS the value under test makes assertions that mirror the impl — pin ABSOLUTE expected integers computed independently (here from chunk byte-lengths), or a systematic-scaling bug stays invisible (evidence: refute-read MAJOR on this task). [folded foundation-version 33]
- [ADD · folded] a mid-task contract change (Tin: also-reconcile-on-disconnect) must sweep ALL sections — §0 ground notes + §1 reject + §2 scenarios + §3 + §4, not just the must-list (evidence: caught stale "shed/disconnect final" §0 lines post-freeze). [folded foundation-version 33]
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
