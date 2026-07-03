# TASK: Drain-time DEL of abandoned-claim marker

slug: batch-claim-drain-del · created: 2026-07-03 · stage: production · risk: high
milestone: v57
autonomy: conservative
sensitivity: architecture
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `proxy/infrastructure/batch_window_buffer.py:_CLAIM_DUE_LUA` (lines 120-156) — the
    per-item drain loop (lines 146-154): today, when `existing` (GET on the item's
    result key) is non-nil, the item is skipped (never claimed) but the marker is
    left untouched. AMENDED: add an `else` branch that `DEL`s `result_key` in that
    skip case — draining is unconditional and unrepeatable per window (see below), so
    this is the one moment the marker can be safely retired.
  - `proxy/infrastructure/batch_window_buffer.py:_ABANDONED_TTL_SECONDS` (line 101) and
    its comment — NOT changed by this task (the constant stays; it becomes a pure
    backstop for the pathological case below rather than the mechanism doing real
    work in the common case). The comment's framing may go stale once the fix lands;
    left as-is per scope (comment truth-maintenance is not this task's concern beyond
    what §5 build touches).
  - `proxy/infrastructure/batch_window_buffer.py:BatchWindowBuffer.claim_due` /
    `.try_abandon` (lines 236-310) — Python wrappers, unchanged; only the Lua body
    changes.
  - `tests/batches/test_batch_window_grouping.py:TestConcurrentAbandonVsClaim` (line
    544) and `_FakeClock` (line 82) — the existing race-proof shape this task's new
    test extends; reused verbatim (two `BatchWindowBuffer` instances over the same
    real Redis, raced via `asyncio.gather`).

Context (working folder): none beyond the two files above — no config/data/docs touched.

Honors (patterns / conventions): the module's own stated atomicity convention (single
  Lua EVAL per operation, no GET-then-DELETE from Python) — PROJECT.md's "design for
  failure" instruction plus this module's own docstring (G6/R6) are the anchors; this
  task adds no new pattern, it extends an existing one exactly.

Anchors the contract cites: `_CLAIM_DUE_LUA`, `BatchWindowBuffer.claim_due`,
  `BatchWindowBuffer.try_abandon`.

Issues/Risks (→ feed §1):
  - CONFIRMED (grep, this session): `custom_id = str(uuid.uuid4())` is generated fresh
    per diversion attempt in `batch_diversion.py:126` — never client-supplied, never
    reused across windows. This RULES OUT a cross-window marker-leak scenario (a stale
    abandoned marker for custom_id X poisoning a LATER window's item with the same
    custom_id) — that would have been the strongest correctness argument for this fix,
    and it does not apply here. The real justification is narrower: see §1.
  - RE-DERIVED (not taken on the reviewer's word): "fully closes the race" is an
    overclaim if read as closing the ORIGINAL documented residual (flusher stall
    exceeding the 4h TTL before the eventual drain) — if the TTL already expired
    before drain runs, the marker is gone and the new DEL branch never fires; nothing
    about this fix touches that case. What it DOES close: today, correctness in the
    COMMON case (drain happens while the marker is still alive, which is virtually
    always, per the TTL comment's own "wide enough to cover any realistic operational
    stall") is TTL-dependent only by accident of a generous constant, not structurally.
    After the fix, the marker's active lifetime is bounded by "until this window next
    drains" (an event), matching the constant's own stated intent, rather than by a
    4-hour guess. This is a real but narrower win than "closes the race" — see the §3
    freeze flag.
  - The `_TRY_ABANDON_LUA` script and the `_RESULT_TTL_SECONDS`-keyed "claimed"/
    terminal markers are OUT of scope — only the `_CLAIM_DUE_LUA` skip-branch changes.

Related intent: batch-window-grouping TASK.md §6 VERIFY Residue (Advisor 3-lens,
  Concurrency lens) — this task closes that residue's chosen option; GLOSSARY has no
  existing `## Sensitivity classes` section, so `sensitivity: architecture` uses the
  base-four vocabulary (docs/sensitivity.md) rather than a new domain token.

Ground SHA: `c6349b5` (matches batch-window-grouping's own last-verified Ground SHA —
  no drift since that task's gate PASS).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Drain-time cleanup of the abandoned-claim result marker
Framings weighed: DEL-on-drain inside `_CLAIM_DUE_LUA` (chosen) · shorten
  `_ABANDONED_TTL_SECONDS` instead (rejected — still timing-dependent, just a smaller
  window; doesn't make correctness structural) · a separate cleanup sweep job
  (rejected — new moving part, new failure mode, for a case a single Lua branch
  already covers for free at the one moment it's knowable)
Must:
<must>
  - When `_CLAIM_DUE_LUA` drains a due window and finds an item's result key already
    non-nil (abandoned, by construction the only realistic case — see §0), it MUST
    `DEL` that result key in the same script, in addition to the existing skip
    (never claim, never return it).
  - The claimed-item path (result key nil at drain time) is UNCHANGED: still SET to
    `{"kind":"claimed"}` with `_RESULT_TTL_SECONDS`, still returned.
  - The window's own unconditional housekeeping (`DEL` items/started keys, `SREM`
    active_tenants) is UNCHANGED.
  - `BatchWindowBuffer.claim_due`'s Python signature, return shape, and exception
    behavior are UNCHANGED — this is a Lua-body-only change.
</must>
Reject: none — this task adds a cleanup branch to an existing atomic script; it
  introduces no new caller-facing input, so there is no new rejection surface.
After:
<after>
  - Once a due window drains, every item whose result key was "abandoned" at drain
    time has NO result key left in Redis immediately afterward (was: lingered up to
    `_ABANDONED_TTL_SECONDS`).
  - The `TestConcurrentAbandonVsClaim` mutual-exclusion property (exactly one of
    {abandon, claim} ever wins for a given item) still holds, unchanged, after the
    Lua edit.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  - [x] Scope of the claim, not the mechanism: this fix removes a
    TTL-dependency in the COMMON case (drain happens while the marker is still
    alive — true in virtually every real run per the existing TTL comment) but does
    NOT close the already-documented pathological case (flusher stall exceeding the
    4h TTL itself, so the marker is already gone by the time drain finally runs).
    Lowest confidence because Tin's own framing when asking for this ("implement
    drain-DEL fix now") could be read as "fully closes the residual" — it doesn't,
    and I want that read on record before freeze, not discovered later. ACCEPTED AT
    FREEZE (2026-07-03, Tin's plain-text "freeze" — this exact narrower scope was
    the drafted shape approved); independently re-confirmed by the adversarial
    reviewer at verify (traced the code directly: the DEL branch only executes in
    the non-nil arm, never fires once the TTL has already expired the marker).
    Resolved, not carried forward as an open question — the one still-open item
    this task surfaces at its gate is the separate Concurrency residue in §6.
  - [x] Whether `custom_id` is ever reused across windows (would have made this a
    live correctness bug, not a hardening) — CONFIRMED NO via grep in §0
    (`uuid.uuid4()` fresh per attempt); resolved, not carried forward.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Abandoned item's marker is deleted when its window drains   # M1
  Given a tenant's window has one item, and that item's result key was set to
    "abandoned" (try_abandon won) before the window became due
  When claim_due runs and drains the now-due window
  Then the item is excluded from the claimed list (unchanged behavior)
  And the item's result key no longer exists in Redis (GET returns nil)

Scenario: Claimed item's marker is unaffected   # M2
  Given a tenant's window has one item with no result key set yet
  When claim_due runs and drains the now-due window
  Then the item is included in the claimed list with a "claimed" marker at
    _RESULT_TTL_SECONDS (byte-identical to before this change)

Scenario: Concurrent abandon-vs-claim race still resolves exactly-one-wins   # M2 (regression)
  Given a tenant's window has one item, due
  When try_abandon(custom_id) and claim_due(tenant_id) run concurrently via
    asyncio.gather against the same real Redis (same shape as the existing
    TestConcurrentAbandonVsClaim)
  Then exactly one of {abandon, claim} wins, never both, never neither
  And whichever one won, the item's result-key end state matches that outcome:
    abandon won -> key absent (this task's new DEL fired); claim won -> key holds
    "claimed" (unchanged path)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Internal contract (no HTTP surface — a Redis Lua script body only):

BatchWindowBuffer.claim_due(tenant_id) -> list[dict] | None   (SIGNATURE UNCHANGED)

  _CLAIM_DUE_LUA per-item drain loop, per custom_id in the drained window:
    existing = GET result_key
    if existing is nil:
        SET result_key '{"kind":"claimed"}' EX _RESULT_TTL_SECONDS   # unchanged
        include item in the returned claimed list                    # unchanged
    else:
        DEL result_key                                                # NEW
        (item stays excluded from the returned claimed list — unchanged)

Schema: no SQL/ORM schema touched. Redis keys only —
  batch:window:result:{custom_id} (existing key, new deletion path, no new key
  introduced, no key renamed).
```

Glossary deltas: none — no new domain term; reuses batch-window-grouping's own
  "abandoned marker" / "claimed marker" vocabulary verbatim.
Status: FROZEN @ v1 — approved by Tin Dang (2026-07-03), bundle-approved as drafted
  ("freeze").
  Least-sure flag surfaced at freeze: [contract] this fix closes the common-case
  TTL-dependency (drain-time cleanup replaces a 4h timeout as the marker's real
  lifetime bound) but NOT the already-documented pathological case where a flusher
  stall exceeds the TTL itself before the eventual drain — that stays an accepted
  residual, now tracked as the `[SPEC · open]` delta on batch-window-grouping's §7.
  Accepted as drafted, no changes requested.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new branch (a 3-line Lua `else` — full or nothing)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_abandoned_marker_deleted_on_drain_sibling_claim_unaffected: deterministic
    (sequential, not raced), ONE window with TWO items so both branches of the loop
    fire in the same drain — append item A + item B; try_abandon(A) (await, wins
    deterministically, nothing else touches A yet); advance clock past window;
    claim_due; assert claimed list == [B] only (pre-existing, A excluded); assert
    GET result_key(A) is None (NEW — red pre-fix, since today it still reads
    {"kind":"abandoned"}); assert GET result_key(B) == {"kind":"claimed"} with a
    positive TTL (regression pin on the untouched branch, exercised in the same
    drain as the new behavior rather than a separate always-green test) · covers:
    scenario 1 + 2 / M1+M2
  - test_concurrent_abandon_vs_claim_del_on_drain: extends
    TestConcurrentAbandonVsClaim's exact shape (two BatchWindowBuffer instances,
    same real Redis, asyncio.gather(try_abandon, claim_due)) across N=20 fresh
    tenant/custom_id iterations — every iteration asserts the existing XOR property
    (abandon_won != claim_won); additionally, whichever iterations have
    abandon_won=True assert GET result_key is now None post-race, and the test
    FAILS (not skips) if zero iterations observe abandon_won across all 20 — this
    is what makes the new branch's under-concurrency behavior a proven property,
    not a lucky single sample · covers: scenario 3 / M1+M2 (regression-proof)
</test_plan>

Tests live in: `apps/gateway/tests/batches/test_batch_window_grouping.py` · MUST run
  red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/proxy/infrastructure/batch_window_buffer.py`
  `apps/gateway/tests/batches/test_batch_window_grouping.py`
Strategy (ordered batches):
  1. Write the two red tests (§4) against the CURRENT (unfixed) Lua script; run,
     confirm both fail for the right reason (the new marker-deletion assertions,
     specifically — not a fixture/setup error).
  2. Add the `else: redis.call('DEL', result_key)` branch to `_CLAIM_DUE_LUA`'s
     per-item loop — the smallest possible diff (no new KEYS/ARGV, no new Lua
     locals beyond what's already in scope at that point).
  3. Run the two new tests + the full existing `TestConcurrentAbandonVsClaim` +
     the rest of `test_batch_window_grouping.py`; confirm green.

Persona (optional): none.
Known-problem fixes: the ONE trap worth naming — do not accidentally DEL on the
  nil-existing (normal claim) branch (would delete the "claimed" marker that
  `BatchDiversionAdapter._lifecycle` needs to read); the `else` must attach to
  "existing is non-nil", never restructure the `if not existing` condition itself.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the DEL and the SET in the two branches are
  mutually exclusive by construction (if/else on the same `existing` check) — never
  let both fire for the same item in the same script execution.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/batch_window_buffer.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask
  if unclear. No new Python dependency; no new Redis key; Lua-body-only diff.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite: 2287 passed, 7 skipped, 28 deselected, 0 failed
      (bash `bz2e60e22`, 834.53s); `tests/batches/` directory: 75/75; this file alone: 21/21
- [x] coverage did not decrease — 89.26% overall (floor 80%); the new `else: DEL` branch has
      dedicated direct-assertion coverage from both new tests (not incidental coverage)
- [x] no test or contract was altered during build — both new tests were written and RED
      before the Lua edit existed, never modified after; §3 CONTRACT text unchanged since freeze
- [x] the green was EARNED, not gamed — see Refute-read verdict below (independently,
      empirically confirmed by a dispatched subagent, not just by my own RED→GREEN report)
- [x] concurrency / timing of the risky operation is safe — **qualified**: safe under every
      invariant that holds today; ONE non-blocking residue tracked for the record, not silently
      cleared — see Advisor 3-lens → Concurrency below. Do not read this checkbox as "no findings."
- [x] no exposed secrets, injection openings, or unexpected dependencies — diff touches only an
      existing internal Redis key already owned by this module; no new external input, credential,
      or dependency
- [x] layering & dependencies follow CONVENTIONS.md — confirmed no new consumer of
      `batch:window:result:*` exists anywhere in the repo (repo-wide grep, see Advisor →
      Architecture below); `BatchWindowBuffer.claim_due`'s Python signature/exceptions unchanged
- [ ] a person reviewed and approved the change — PENDING: this is the open human gate this
      section is being prepared for; not yet true

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] an abandoned item's result key no longer exists in Redis immediately after its
      window drains — confirmed by a raw `GET` on `batch:window:result:{custom_id}`
      returning nil right after `claim_due` returns, not merely by the test's own assert
      (`test_abandoned_marker_deleted_on_drain_sibling_claim_unaffected`, direct redis_client.get)
- [x] a sibling item in the SAME drain that was never abandoned is claimed exactly as
      before (marker = `{"kind":"claimed"}`, positive `_RESULT_TTL_SECONDS` TTL) — confirmed
      by the same raw-Redis inspection, not just the returned claimed-list (same test)
- [x] the existing `TestConcurrentAbandonVsClaim` mutual-exclusion property (exactly one
      of {abandon, claim} ever wins) is unchanged after the Lua edit — confirmed by that
      test plus a new 20-iteration extension (`TestConcurrentAbandonVsClaimDelOnDrain`), both
      green, and independently re-derived (not just re-run) by the adversarial reviewer
- [x] the diff to `_CLAIM_DUE_LUA` is the single `else: DEL` branch only — no other line
      in the script changed — **evidence source corrected from what was pre-declared**: this
      file was never committed to git (`batch_window_buffer.py` is `??` untracked, part of the
      still-uncommitted `batch-window-grouping` milestone work), so a literal `git diff` at the
      gate shows the whole file as new, not an incremental diff. Confirmed instead by (a) direct
      re-read of the current file just now — lines 149-159 show only the pre-existing `if not
      existing: SET+insert` arm plus the new `else: DEL` arm, nothing else in the script body
      changed from what §0 GROUND / §3 CONTRACT describe — and (b) the adversarial reviewer's
      independent confirmation (its report point 1: "the diff is exactly what §3 CONTRACT
      promises... no KEYS/ARGV changes, no signature changes").

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — no NEW symbol was introduced (no new function/constant/class); the only
      change is a new branch inline inside the existing `_CLAIM_DUE_LUA` string, which is already
      registered (`redis.register_script`, line 216) and invoked (`self._claim_script(...)` inside
      `claim_due`, line 256) — so the new branch is trivially reachable on every claim_due call,
      confirmed by re-reading both sites directly
- [x] DEAD-CODE (code) — no new symbol exists, so nothing can be orphaned by this change (the
      one actual orphaned-symbol finding this session, `BATCH_REFERENCE_OBJECT`, belonged to the
      parent task's residue in a different file, unrelated to this diff)
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: the whole module docstring plus
      all three Lua script comment blocks (lines 93-172) were re-read just now. The pre-existing
      `_ABANDONED_TTL_SECONDS` comment ("if it expires before the window is drained, a later
      claim_due sees a nil key and silently re-claims...") still reads accurately post-fix — it
      describes the residual pathological (beyond-TTL) case, which this fix does not touch — so
      no stale prose needed correcting beyond the loop-body comment already added at build time.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by direct
      Read just now: `BatchWindowBuffer.claim_due` (line 241, signature unchanged: `(tenant_id:
      uuid.UUID) -> list[dict[str, Any]] | None`), `_CLAIM_DUE_LUA` (line 123), the per-item drain
      loop (lines 149-159), `_RESULT_KEY_PREFIX` / `batch:window:result:` key pattern unchanged
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — no rename;
      the per-item loop's absolute line numbers shifted by a few lines versus §0's citation
      (comment block grew when the loop-body comment was added at build time) — a normal shift
      from inserting lines earlier in the same file, not drift or a moved/renamed symbol

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent-id ac5af5b2ac44b01e2 · adversarially checked: (1) constructed a hypothetical
  duplicate-`custom_id` scenario to try to make the DEL branch erase a live "claimed" marker —
  found it structurally possible in the script alone (no internal `kind` check) but provably
  unreachable today given 3 named external invariants (see Advisor → Concurrency below); (2) went
  beyond static reasoning — temporarily REVERTED the fix, reran all 3 relevant tests, confirmed
  the 2 new tests fail at exactly the marker-deletion assertions (iteration 0, first abandon-win,
  immediately) while the pre-existing `TestConcurrentAbandonVsClaim` stays unaffected, restored the
  file (diff-confirmed byte-identical), reran to confirm 21/21 green again; (3) independently
  traced the code (not trusting TASK.md prose) to confirm the "closes common-case, not pathological
  case" scope claim is accurate — the DEL branch only executes in the non-nil `else` arm, never
  when TTL already expired the marker before drain; (4) confirmed the `abandon_wins_observed > 0`
  guard is real, not decorative — abandon won on the reviewer's very first iteration; (5) grepped
  repo-wide for `_RESULT_KEY_PREFIX` / `batch:window:result:` — no consumer besides this module,
  its own tests, and the two TASK.md docs; also read `BatchDiversionAdapter.try_divert` /
  `wait_for_result` directly and confirmed neither has an `"abandoned"` branch, so whether this key
  is DELeted early or left to expire is invisible to every actual reader today.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: agent-id ac5af5b2ac44b01e2 (adversarial-review subagent; corroborated directly by me
  via the re-reads recorded above)
1. Security: CLEAR — no new external input, secret, or credential; the diff operates only on an
   existing internal Redis key already owned by this module
2. Concurrency: RESIDUE: the `else` branch DELs on *any* non-nil result-key value — it never
   inspects the marker's `kind` field, so the Lua script has zero internal defense-in-depth; safety
   is 100% externally guaranteed by three invariants: (a) `custom_id = uuid.uuid4()` fresh per
   attempt (`batch_diversion.py` `try_divert`), (b) exactly one `append()` per custom_id, (c) a
   claimed item is atomically removed from the list in the same script that claims it. IF a
   duplicate custom_id were ever appended twice into the same window, this fix changes the failure
   mode from harmless (pre-fix: silent skip, marker survives) to active (post-fix: the second
   iteration DELs the first iteration's live "claimed" marker inside the same drain — a
   double-process risk). Unreachable today — `try_divert` always returns `None`, no live
   `BatchProcessor` adapter exists yet, so the whole mechanism is dormant in production.
3. Architecture: CLEAR — repo-wide grep found no other consumer of the result-key namespace;
   `claim_due`'s Python signature, return shape, and exception behavior are all unchanged;
   layering unaffected (change is contained entirely inside one Lua string in one infrastructure
   module)
Verdict: PASS
Residue: the duplicate-custom_id / no-`kind`-check structural gap above — open, tracked, not
  silently absorbed. Not a HARD-STOP (no security exposure; correctness-only; provably unreachable
  given current invariants) — but real enough on a `risk: high` / `sensitivity: architecture` task
  that it is routed to the human gate below rather than self-resolved.
Binding: advisory — architecture (this task is not `sensitivity: mechanical`, so this verdict does
  not auto-relax the gate; the GATE RECORD outcome below is Tin's call under `autonomy:
  conservative`)

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-03

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose DEL-on-drain inside `_CLAIM_DUE_LUA`; rejected shorten
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-03), bundle-approved as drafted)
- [AI] build — strategy used: as planned
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] `_CLAIM_DUE_LUA`'s abandoned-marker DEL branch has no internal
  `kind`-check defense-in-depth — safe today only because `custom_id` is
  guaranteed fresh-per-attempt externally (`batch_diversion.py`). If a future
  `BatchProcessor` adapter or a retry path ever violates that invariant (duplicate
  `custom_id` inside one window), this branch would delete a live "claimed"
  marker instead of silently skipping as it did pre-fix. Revisit BEFORE the
  first live `BatchProcessor` adapter ships — either reconfirm the invariant
  still holds at that point, or add the `kind`-check then (evidence: Advisor
  3-lens Concurrency residue, this task's §6 VERIFY, 2026-07-03; Tin's gate
  decision same date — "PASS as-frozen, residue tracked").

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

- [TDD · open] an adversarial reviewer that only reads code can't tell whether a
  test's guard condition (e.g. `abandon_wins_observed > 0`) is real or vacuous;
  temporarily reverting the fix, confirming the exact expected RED, then
  restoring and reconfirming GREEN is a stronger standard and should be the
  default ask for future adversarial-review dispatches, not an optional extra
  (evidence: agent `ac5af5b2ac44b01e2`'s report, this task's §6 VERIFY, 2026-07-03).
- [ADD · open] a Build-expectations row that pre-declares "confirmed by `git
  diff` at the gate" can silently fail when the touched file was never
  committed (still `??` untracked across a whole prior milestone's work) —
  `git diff` shows the entire file as new, not an incremental diff.
  Pre-declared evidence sources should name a fallback (direct re-read +
  independent corroboration) for files that may still be uncommitted at
  verify time (evidence: this task's §6 VERIFY Build-expectations row 4,
  2026-07-03).

