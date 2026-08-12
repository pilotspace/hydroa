════════════════════════════════════════════════════════════════════════
 v51 · Artifacts on real object storage (MinIO)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  4/4 met
 GATES     3 PASS             WAIVERS   none

 goal  An artifact's bytes are persisted to and served from a real
       self-hosted object store (MinIO), replacing inline Postgres
       BYTEA, with honest-degrade to inline storage when the store is
       unconfigured.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 object-store-port           done      PASS 4†    ●●●●●●●●●
 artifacts-s3-live-verify    done      PASS 0     ●●●●●●●●●
 artifacts-s3-persistence    done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (8 carried)
   • TDD · open · an untyped third-party SDK (aioboto3) is made
     unit-testable by injecting a client_factory (a zero-arg async-ctx
     callable) so tests pass a botocore-faithful fake, while a SEPARATE
     skip-gated live suite proves the real wire — the inject-fake +
     live-gated split keeps the fast lane green without MinIO yet covers
     the real path (evidence: 15 unit green in test-fast + 4 live green
     vs real MinIO) [object-store-port]
   • SDD · open · the existing CircuitBreaker is IO-tier-agnostic — it
     dropped onto a brand-new object-store IO seam unchanged
     (guard/record_success/on_upstream_error), confirming the breaker is
     a reusable primitive, not completion-path-specific (evidence:
     reused verbatim, 0 edits) [object-store-port]
   • ADD · open · running `ruff --fix` on a test file AFTER the
     tests→build snapshot trips `build_tampered` (even for a cosmetic
     autofix) — remedy is to re-cross tests→build to re-snapshot, OR run
     autofix BEFORE crossing; never weaken the test to clear it
     (evidence: gate PASS attempt burned 1 heal, cleared by re-cross)
     [object-store-port]
   • ADD · open · a skip-gated live-verify task is NOT red-first — the
     impl it proves is an already-gated upstream task; the floor is
     honored by SKIP-not-fail + first-hand real-infra assertion,
     recorded explicitly in §4 so it is not mistaken for a missing red
     (evidence: this task ran green immediately against MinIO).
   • TDD · open · pydantic `SecretStr` fields reject a plain `str` under
     pyright even though they coerce at runtime — wrap test-constructed
     secrets in `SecretStr(...)` to keep the zero-new-error bar
     (evidence: live test 48:40 reportArgumentType → fixed).
   • ADD · open · a repository SIGNATURE change ripples to EVERY caller
     — pyright (not a test) caught the 2nd caller (video worker); widen
     §5 scope to the rippled file + keep its call byte-identical, and
     re-pin the change with a follow-up spec delta rather than silently
     expanding behavior (evidence: video/api/router.py:237
     reportCallIssue).
   • TDD · open · "green-by-design" invariant-preservation tests (inline
     path, soft-delete, cross-tenant) legitimately pass BEFORE and AFTER
     the build — they assert an invariant HELD, not new behavior; label
     them so they are not mistaken for missing red (evidence: 3 of 8 new
     tests green at red-run).
   • SDD · open · when HONEST-DEGRADATION is a HARD invariant, even an
     UNREACHABLE corrupt-row state (s3 row with NULL object_key) must
     surface an honest 5xx, never a masking `or ""`/`or b""` that yields
     a misleading 404 or empty 200 (evidence: refute-read NIT → hardened
     the s3 object_key guard).

 SPEC DELTAS    162 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v51
════════════════════════════════════════════════════════════════════════