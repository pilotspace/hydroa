════════════════════════════════════════════════════════════════════════
 v27 · Billing precision — true per-tier cost on every call
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  5/5 met
 GATES     4 PASS             WAIVERS   none

 goal  every proxied call is billed at the provider's true, per-tier
       cost: cached and reasoning tokens priced distinctly,
       provider-reported cost preferred when present, audio and
       streaming calls never silently under-billed

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 tiered-token-billing        done      PASS 0     ●●●●●●●●●
 provider-cost-reconciliati… done      PASS 2†    ●●●●●●●●●
 stt-duration-derivation     done      PASS 3†    ●●●●●●●●●
 stream-usage-completeness   done      PASS 1†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (12 carried)
   • TDD · open · A latent test-isolation bug can hide until a 2nd/3rd
     test of the same kind is added — Alembic's `env.py fileConfig(...)`
     defaulted to `disable_existing_loggers=True`, silently disabling
     every `gateway.*` logger in-process once the migrations suite ran,
     so caplog saw nothing downstream. t1's TT6/TT7 only "passed" by
     collection-order luck. Lesson: when adding a caplog-on-app-logger
     test, treat full-suite ordering as part of the contract; the
     canonical fix is `disable_existing_loggers=False`. (evidence: 3
     caplog tests RED in full suite, green after the env.py one-liner;
     bisected to tests/migrations).
   • TDD · open · Extending a shared adapter ctor with a new attribute
     breaks sibling `__new__`-built test doubles (retry_policy
     `make_upstream`) unless a CLASS-LEVEL default is provided — same
     v26 lesson, now re-confirmed for
     `OpenRouterCompletionUpstream._usage_accounting=False`. Default on
     the class body, not only in __init__. (evidence: 9 retry_policy
     tests AttributeError → green after class-level default).
   • ADD · open · The verify-gate adversarial refute-read again paid for
     itself: confirmed EARNED-GREEN AND surfaced 2 real coverage gaps
     (stream() injection + Settings→upstream wiring) that the scenario
     set under-pinned; both closed before gate (PC13/PC14). Extends the
     t1 refute-read delta.
   • SDD · open · "Capture upstream-reported cost" had a hidden dormancy
     trap: consuming `usage["cost"]` is correct but NEVER fires unless
     the gateway opts into OpenRouter usage accounting. Surfacing that
     at the freeze (the default-off knob) turned a would-be no-op
     feature into a real, operator-flippable one.
   • TDD · open · An inf-via-HTTP test is confounded: Starlette renders
     the echoed body with `allow_nan=False`, so the response RAISES
     before any status assert. Pin the LEDGER instead —
     `pytest.raises(ValueError)` on the call + poll the
     `asyncio.ensure_future` usage-record spy for the billed `quantity`.
     (evidence: SD8 RED showed `Decimal('Infinity')` in the spy, GREEN
     after the guard.)
   • SDD · open · A frozen contract can carry a guard ASYMMETRY: the
     decoder spec had `math.isfinite`, the upstream-branch spec did not
     → an inf upstream billed `Decimal('Infinity')`. The refute-read
     (Finding 1) caught it; the fix was a CHANGE-REQUEST re-freezing §3
     @ v2, not a silent edit. Mirror an invariant across every sibling
     code path when freezing.
   • ADD · open · The verify-gate adversarial refute-read paid off
     again: 2 real findings on a fully-green build (isfinite gap + no
     over-bill cap). EARNED-GREEN ≠ flawless. (evidence: refute-read on
     this task.)
   • ADD · open · FOLLOW-UP (Finding 2, deferred by Tin): no UPPER
     magnitude cap on the billed duration — a lying/corrupt audio header
     (huge declared `data` chunk) over-derives; tinytag trusts the
     header. Harm is tenant-self-inflicted; a sane ceiling needs a
     product-chosen max. Not built here; revisit as a change-request.
   • ADD · open · FOLLOW-UP (separate, pre-existing): an inf/nan
     `duration` in the upstream STT body still 500s on response
     serialization (allow_nan=False), independent of billing.
     Response-passthrough robustness, out of this billing task's scope;
     candidate to sanitize non-finite floats before echoing the upstream
     body.
   • SDD · open · A streaming client DISCONNECT (GeneratorExit raised
     through `_wrapped` before the terminal frame) currently bills $0
     with NO usage_source marker at all — the post-stream record block
     at use_cases.py ~1483 is skipped when the generator is closed
     early, so this is a SILENT $0 distinct from the missing-frame case
     this task closed. Evidence: §0 trace + manual read of `_wrapped`'s
     finally/GeneratorExit handling; out of this task's frozen scope (no
     scenario). Candidate next-loop task: stamp
     `usage_source='client_disconnect'` (or fold into stream_fallback)
     on the disconnect path so EVERY $0 stream row is explained, not
     just missing/partial frames.
   • TDD · open · Refute-read NIT-3 (predicate table missing
     float/negative/bool-with-real-int rows) shows a
     pure-total-predicate test table should ENUMERATE the type-confusion
     axis (bool/float/negative/None/non-dict), not just the value axis
     (0 vs positive). Evidence: SU7 shipped green with 9 params,
     refute-read found the 3 missing type cases; all now covered (12
     params). Foundation: add "type-confusion row per non-int input
     class" to the pure-predicate test checklist.
   • ADD · open · Editing a declared test file during VERIFY (to close
     refute-read NITs) requires the sanctioned tripwire re-cross (`phase
     tests` → `advance` ×2) — doing it in-place would burn a monotonic
     heal attempt. Evidence: re-crossed clean this task. Foundation: the
     refute-read→fix loop should always step back to `tests` before
     editing, never edit-in-verify. (Reconfirms the v25 tamper-tripwire
     ordering learning.)

 DECIDE NEXT  consolidate learnings + archive-milestone v27
════════════════════════════════════════════════════════════════════════