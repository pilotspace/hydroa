════════════════════════════════════════════════════════════════════════
 v30 · Reconciliation hardening — make leak-detection trustworthy
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     10/10 done         CRITERIA  4/4 met
 GATES     10 PASS            WAIVERS   none

 goal  a platform operator can trust the billing reconciliation signal —
       no nonsense config silently disables it, no false leak from
       catalog rows, no unexplained $0 on disconnect, and drift is
       visible across all tenants

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 drift-threshold-validation  done      PASS 7†    ●●●●●●●●●
 reconcile-cost-basis-filter done      PASS 0     ●●●●●●●●●
 disconnect-provider-cost    done      PASS 0     ●●●●●●●●●
 incremental-sse-translation done      PASS 0     ●●●●●●●●●
 bedrock-incremental-stream  done      PASS 1†    ●●●●●●●●●
 openrouter-cost-recovery    done      PASS 0     ●●●●●●●●●
 openrouter-generation-clie… done      PASS 0     ●●●●●●●●●
 provider-generation-id-cap… done      PASS 3†    ●●●●●●●●●
 openrouter-cost-recovery-w… done      PASS 0     ●●●●●●●●●
 openrouter-recovery-sweep   done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (17 carried)
   • ADD · open · a frozen §3 can encode a wrong MECHANISM while its
     OBSERVABLE is right — the v1 pseudo-code put the guard in an
     after-validator, but Pydantic 2.13 rejects non-finite Decimals at
     type-coercion BEFORE after-validators run. TDD-red surfaced it; the
     fix was a behavior-preserving v2 mechanism clarification
     (mode="before"), NOT a contract weakening (the error code +
     accept/reject conditions were unchanged). A mechanism correction
     inside the bundle that preserves the observable is legitimate
     (evidence: tests-red showed pydantic's `finite_number` message
     instead of the contracted code).
   • TDD · open · ground the framework's DEFAULT validation before
     specifying a guard against it — knowing Pydantic already rejects
     non-finite Decimals would have shaped the §3 mechanism up front and
     avoided the v2 round-trip (evidence: the §3 v2 discovery).
   • ADD · open · a frozen MILESTONE shared contract (v29
     reconcile_window §3) is correctly evolved by the SUPERSESSION
     pattern from a NEW task in a later milestone — record the new shape
     + a supersession note in the new task, leave the archived frozen
     TASK.md untouched; works even though the archived task is detached
     from the active engine registry (evidence:
     `--from-delta`/`drop-delta` rejected the archived slug, so the
     cross-reference was wired by hand in §1/§7).
   • TDD · open · to red-test a DEFENSIVE filter whose guarded condition
     can't occur on conformant data, SEED the prohibited row directly
     (catalog + provider_cost>0) — the seed makes the latent bug
     observable now, turning "future-proofing" into an executable
     red→green (evidence: RA9/RA11 red on the seeded catalog row, green
     after the clause).
   • ADD · open · an adversarial refute-read earns its keep even on a
     3-line change — it surfaced the Exception-vs-BaseException
     (CancelledError) gap that the first green hid (evidence: BUG-1 →
     strengthened test).
   • TDD · open · best-effort cleanup in async-gen close handlers must
     be tested with a BaseException (CancelledError), not just Exception
     — `suppress(Exception)` silently leaks BaseException (evidence: the
     new red test failed against suppress(Exception), passed against
     suppress(BaseException)).
   • ADD · open · the adversarial refute-read caught a real concurrency
     bug (advisory-counter double-increment) that ALL nine green tests
     missed — the DB dedups but INCRBYFLOAT does not; closed by a SET NX
     idempotency guard + a concurrent-double-fire test (evidence: refute
     verdict REFUTED → strengthen → NOT-REFUTED)
   • TDD · open · idempotency tests must exercise the CONCURRENT race
     (no flush between), not just the sequential path — a flush-between
     test only proves the read-side guard, never the write-side dedup
     (evidence: original test_idempotent passed while the counter still
     double-moved)
   • ADD · open · a refute-read on a thin IO primitive still earns a
     contract refinement: 401/403 must not alias "not ready" (None) or
     the caller infinite-re-polls — split not-ready (404) from permanent
     (raise) (evidence: refute MEDIUM → change-request v2).
   • TDD · open · a money-precision test must feed a JSON NUMBER, not a
     string — a str fixture trivially passes Decimal(str(str)) and
     proves nothing about the real float→str→Decimal path (evidence:
     refute LOW).
   • ADD · open · a 10-hop additive field threading is best de-risked by
     a refute-read that traces EVERY hop (the silent-drop failure mode
     hides between Redis event and Postgres column) — refute confirmed
     all 10.
   • SDD · open · mirroring an existing field end-to-end (v27
     usage_source) is the cheapest safe way to add a ledger column —
     same extras seam, same NULL-encoding, same migration shape
     (evidence: byte-for-byte template).
   • ADD · open · hot-path fire-and-forget must follow the file's
     EXISTING ensure_future hygiene (capture task + add_done_callback to
     retrieve exceptions) — a lone suppress(BaseException) only covers
     the synchronous schedule, not the coroutine's later raise
     (evidence: refute Finding 1, closed by matching the 8 sibling sites
     + an async-raising test).
   • TDD · open · a fire-and-forget test must (a) await a settle to
     prove the task RAN, and (b) exercise BOTH a sync-raise (schedule
     guard) and an async-raise (done_callback) — sync-only leaves the
     task-exception path uncovered (evidence: refute Finding 2).
   • TDD · open · ASGITransport does NOT run ASGI lifespan — task
     handles must be pre-initialized to None at create_app construction
     (main.py ~415) for introspection tests; the only way to observe a
     lifespan-created task is `async with
     app.router.lifespan_context(app)` (evidence: 3 wiring tests failed
     until the construction-time default + lifespan_context were used).
   • ADD · open · when a refute-read claims a BLOCKER, adjudicate it
     against the ACTUAL idempotency key, not the abstract risk — the
     gid-global uuid5 write key made a tenant-scoped skip-filter wrong,
     not safer (evidence: Finding 1 refuted by reading
     cost_recovery.recovery_event_id).
   • TDD · open · index changes must land in BOTH the ORM
     `__table_args__` (create_all → test schema) and an Alembic
     migration (prod), with identical name/cols/WHERE, or autogenerate
     drifts (evidence: tests use create_all, prod uses migrations — the
     column-type divergence in Finding 9 is the same root cause).

 SPEC DELTAS    21 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v30
════════════════════════════════════════════════════════════════════════