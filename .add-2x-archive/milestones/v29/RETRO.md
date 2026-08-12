════════════════════════════════════════════════════════════════════════
 v29 · Billing reconciliation — provider cost vs billed, with drift alert
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     3/3 done           CRITERIA  3/3 met
 GATES     3 PASS             WAIVERS   none

 goal  every dollar an upstream provider charges us is reconciled
       against what we billed the tenant, and drift beyond a configured
       threshold raises an alert — an upstream charge with no matching
       user charge can never go unnoticed

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 reconciliation-aggregate    done      PASS 0     ●●●●●●●●●
 reconciliation-endpoint     done      PASS 0     ●●●●●●●●●
 drift-alert                 done      PASS 1†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 3/3 met

 LEARNINGS (9 carried)
   • TDD · open · a shared PRIMITIVE built before its consumers passes
     WIRING via its test suite alone — record the downstream consumers
     as seeded SPEC deltas so the "every new symbol referenced" check
     reads as deliberate-sequencing, not dead code (evidence:
     reconcile_window has no production caller until v29 t2/t3; the
     refute-read flagged then cleared it).
   • ADD · open · a string-concatenated SQL `tenant_clause` fed by
     implicit-concatenation literals broke once mid-build (a `+ clause`
     between two adjacent string literals silently dropped the following
     `GROUP BY` fragment) — always make the `+ clause +` joins EXPLICIT
     around interpolated fragments in multi-line `text()` (evidence: the
     Query-2 SyntaxError fixed during build).
   • TDD · open · the test/prod `created_at` schema drift (ORM
     `create_all` → naive TIMESTAMP; the migration → TIMESTAMPTZ) means
     a window bound must be normalized to naive UTC before binding — the
     existing `usage/api/router.py:284 # asyncpg expects naive UTC` is
     the canonical pattern `_as_naive_utc` now mirrors; new ledger reads
     should reuse it, not re-discover the asyncpg aware/naive mismatch
     (evidence: the RA-seed DataError fixed by stripping tz in both the
     conftest seed and the window bounds).
   • TDD · open · a thin HTTP handler over a frozen aggregate is best
     tested at the EDGE over real HTTP (route-exists proven by the 401,
     tenant-isolation by a discriminating 1.00-not-3.00 assert) —
     minting same-tenant admin/member tokens via
     `app.state.token_service.issue(...role=...)` after a direct users
     insert is the reusable role-gate test pattern (evidence:
     RE2/RE5/RE6/RE7; team_governance precedent).
   • ADD · open · a milestone task line can over-promise against the
     real auth model — "operator-wide view" assumed a cross-tenant
     authority that doesn't exist; grounding (§0) caught it BEFORE the
     contract, turning it into a freeze decision + a seeded follow-up
     rather than a tenant-isolation breach (evidence: the freeze flag;
     the security-correct default chosen over the literal milestone
     text).
   • ADD · open · sibling lint debt surfaced: v29 t1 `reconciliation.py`
     ships 5 ruff findings (E501 ×2, RUF003 ambiguous `−`, UP017
     `datetime.UTC`, S608 false-positive on the static tenant_clause) —
     out of THIS task's scope to fix, but a `chore(lint)` follow-up
     should clean it (and prefer ASCII `-`/`datetime.UTC`/`# noqa: S608`
     on static-literal SQL in new ledger reads) (evidence: `ruff check`
     on the t1 file during this verify).
   • TDD · open · the adversarial refute-read caught 4 §3-coverage gaps
     a green suite missed (drift-field unasserted · no at-threshold
     boundary · `run_forever` never invoked · default-OFF wiring
     unexercised) — the sanctioned response is STRENGTHEN-then-re-cross,
     never weaken (evidence: t3 refute-read BLOCK → DA5/DA8/DA9/DA10).
   • TDD · open · verify a reviewer's "tighten to exact string" against
     the real column scale before applying — `SUM` over `NUMERIC(20,10)`
     yields `"5.0000000000"`, so Decimal-equality is RIGHT and
     exact-string would be a false assert (evidence: refute-read F10
     refuted by the migration scale).
   • ADD · open · extract a lifespan start-guard into a pure predicate
     (`should_start_drift_checker`) so the default-OFF invariant is
     unit-testable WITHOUT driving the flaky full lifespan (the
     "fixtures never cancel background tasks" foundation rule) — a
     reusable wiring-test pattern for the other checkers (evidence: F1
     closed via the DA10 truth-table, not a lifespan test).

 SPEC DELTAS    10 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v29
════════════════════════════════════════════════════════════════════════