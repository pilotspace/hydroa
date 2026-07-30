# PLAN: A pgvector-bearing release deploys to managed Postgres from a written runbook

slug: pgvector-deploy-runbook · created: 2026-07-29 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: an operator can take a pgvector-bearing release (0.13.0) to a deploy target — in-cluster StatefulSet PVC or managed Postgres — and to a restored backup, from a written runbook, with the collation hazard DETECTED by a command rather than discovered by wrong query results.

Grounding — measured 2026-07-29 on the live dev stack, not inferred:
**The hazard is already present here.** R5 (`4a351bd`, the same commit that made 0.13.0
need pgvector) changed the Postgres image from `postgres:16-alpine` (musl) to
`pgvector/pgvector:pg16` (Debian, glibc 2.36) in compose, the chart and ci.yml. A volume
`initdb`'d under the old image and served by the new one has its text/varchar btree
indexes ordered by musl and queried under glibc. Proved by experiment on
`hydroa-dev-postgres-1`: a database created NOW records `datcollversion = 2.36`, while
every database predating the switch has `datcollversion` SQL NULL, and Postgres's own
`pg_database_collation_actual_version()` for `gateway_test` returns `actual = 2.36`
against `recorded = NULL`.
**Why it is silent.** Postgres warns on a collation-version mismatch only when it has a
recorded version to compare against. musl-era databases have none, so the mismatch
produces NO warning — the failure mode is wrong `ORDER BY` / range-scan results and
unique constraints that no longer detect duplicates, not a crash.
**The documented restore drill is already broken.** `docs/runbooks/backup-rollback.md:64`
restores into stock `postgres:16`, which has no pgvector. Every dump taken after R5
contains `CREATE EXTENSION vector` and `vector(1536)` columns, so the drill as written
fails. `docs/runbooks/01-getting-started.md:47` still documents `postgres:16-alpine`.
**The existing guard does not reach docs.** `tests/migrations/test_ci_workflow_parity.py`
already pins the image across ci.yml, compose and the chart — and covers no `.md` file,
which is exactly where both stale pins survived.

Framings weighed:
- **An executable preflight + a runbook that cites it** (chosen) — the hazard is silent,
  so prose telling an operator to "check collations" is a checkbox nobody can fail. A
  command that exits non-zero on a musl-lineage database is the only form an operator
  can actually run under incident pressure, and the only form a test can prove works.
- *Runbook prose only* (rejected) — it is what we already have for backups, and it went
  stale within one release without anyone noticing. Unverifiable by construction.
- *Force a dump/restore on every upgrade* (rejected as the default) — correct but
  expensive, and wrong for the common case of a database created after the switch. It
  belongs in the runbook as the REMEDY once the preflight says the lineage is musl, not
  as unconditional ceremony.
- *`ALTER DATABASE ... REFRESH COLLATION VERSION`* (rejected as the remedy, kept as a
  documented trap) — it silences the warning by recording the CURRENT version; it does
  not rebuild a single index. Running it instead of REINDEX converts a detectable
  problem into an undetectable one.

Must:
<must>
  - M1 a preflight command reports the collation lineage of a target database and exits
    NON-ZERO when the recorded collation version is absent or differs from the actual —
    the musl-era case Postgres itself cannot warn about.
  - M2 the preflight is honest about the case it cannot judge: a database it cannot
    reach, or a Postgres too old to expose the version, reports UNKNOWN and exits
    non-zero — never a green that means "not checked".
  - M3 (NARROWED, CR v3) a written runbook covers the ONE supported deploy shape, the
    in-cluster StatefulSet PVC (`charts/ai-proxy`), and states the REMEDY when the
    preflight fails (REINDEX vs dump/restore) and the rollback if the remedy is
    interrupted. It must ALSO state plainly that managed Postgres is not a supported
    target — silence would read as "not documented yet" rather than "out of scope".
  - M4 the restore drill in `backup-rollback.md` works for a pgvector-bearing dump: the
    image it names can serve `CREATE EXTENSION vector`.
  - M5 no Postgres image pinned anywhere in `docs/runbooks/` may drift from the single
    deployed pin — the same parity the code paths already enforce.
  - M6 (NARROWED, CR v3) the runbook makes NO managed-provider privilege claim at all.
    With managed Postgres out of scope there is no target to verify against, and the
    honest move is to delete the speculation rather than keep an UNVERIFIED block that
    invites someone to act on it.
</must>
Reject:
<reject>
  - a runbook step whose correctness cannot be checked by running something -> "unverifiable_runbook"
  - upgrading or restoring onto an existing volume with no collation check in the path -> "silent_collation_mismatch"
  - a documented postgres image that cannot serve `CREATE EXTENSION vector` -> "image_without_pgvector"
  - `REFRESH COLLATION VERSION` offered as the remedy for a real mismatch -> "collation_version_laundered"
  - a provider privilege/behaviour claim stated as fact without evidence -> "unverified_provider_claim"
</reject>
After:
<after>
  - the preflight exits 0 against a database created by the current image, and non-zero
    against one whose recorded collation version is absent — both demonstrated.
  - the restore drill, run end-to-end against a dump containing a `vector(1536)` column,
    restores without error.
  - `make ci` exits 0; the new guards are red before the change and green after.
</after>
Boundary: two input shapes the checks must speak — (a) a database created by the CURRENT
image, `datcollversion` recorded (`'2.36'`); (b) a database created by the OLD alpine
image, `datcollversion` **SQL NULL**. Verified directly (`datcollversion IS NULL` -> `t`,
`datcollversion = ''` -> NULL) after a first probe using `nullif(datcollversion,'')`
proved unable to tell the two apart — a check written against `= ''` returns NULL, which
is not true, so it would silently PASS every musl-era database it exists to catch. The
comparison against the actual version must likewise be NULL-safe (`IS DISTINCT FROM`).
<assumptions>
  ⚠ that a NULL `datcollversion` reliably means the musl/alpine lineage. It is
  consistent with everything measured here, but NULL is also what any provider that does
  not populate the field would report — including, possibly, a managed provider we have
  not tested. If wrong: the preflight cries wolf on a healthy managed database and
  an operator learns to ignore it, which is worse than not shipping it. Mitigation: M2's
  UNKNOWN state is distinct from FAIL, and the runbook says how to tell them apart.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
CLI  scripts/pg_preflight.py --database-url <URL> [--json]
     (also reachable as `make pg-preflight DATABASE_URL=...`, mirroring `kind-preflight`)

  exit 0  OK       recorded IS NOT DISTINCT FROM actual
  exit 1  FAIL     recorded IS NULL (musl-era lineage), or recorded <> actual
  exit 2  UNKNOWN  unreachable, auth refused, or server too old to expose
                   pg_database_collation_actual_version()

  stdout (--json): {
    "status": "OK" | "FAIL" | "UNKNOWN",
    "database": "<datname>",
    "collate": "<datcollate>",
    "recorded_version": "<str>" | null,     # NULL = never recorded = musl lineage
    "actual_version": "<str>" | null,
    "server_version": "<str>",
    "libc": "<str>" | null,
    "remedy": "reindex" | "dump_restore" | null,
    "reason": "<one human-readable sentence>"
  }
  Non-JSON stdout is the same fields, one per line, plus the remedy paragraph.

Queries (read-only, no DDL, no writes):
  SELECT datname, datcollate, datctype, datcollversion,
         pg_database_collation_actual_version(oid)
    FROM pg_database WHERE datname = current_database()
  SELECT version()
  NULL-safety: compare with IS DISTINCT FROM; test the musl case with IS NULL.
  Never emits ALTER DATABASE ... REFRESH COLLATION VERSION (R:collation_version_laundered).

Docs (the runbook is part of the contract — these are the sections M3/M4/M6 are judged on):
  docs/runbooks/pgvector-deploy.md   NEW
    §1 Preflight — run the command; what OK / FAIL / UNKNOWN each mean
    §2 In-cluster StatefulSet PVC — upgrade path across the alpine->pgvector image change
    §3 Managed Postgres — extension provisioning + the privilege it needs per provider
    §4 Remedy — REINDEX (same cluster) vs dump/restore (moving lineage), with the
       interrupted-remedy rollback for each
    §5 The trap — why REFRESH COLLATION VERSION is not the fix
  docs/runbooks/backup-rollback.md   restore-drill image -> the pinned pgvector image
  docs/runbooks/01-getting-started.md   stale `postgres:16-alpine` reference corrected
```

Target (measurable): the preflight exits 0 against a database created by the current
image and 1 against a musl-lineage database (both demonstrated on a real server, not a
mock); the restore drill completes against a dump containing a `vector(1536)` column;
`docs/runbooks/` contains ZERO Postgres image pins other than `pgvector/pgvector:pg16`
(currently 2); `make ci` exits 0. Judged at the gate via --target-hit.
Confirmed outside the tests: that the runbook is USABLE — a human follows §2 or §3 once
on a real target. Tests can prove the commands work; they cannot prove the prose is
followable, so M3 is signed off by the operator who runs it, not by a green suite.
CR v2 (2026-07-29) — SCOPE TOKEN ONLY, no shape change. The v1 Scope ended in a bare
`Makefile`, which the token grammar binds as a SIBLING of the previous token's directory:
it resolved to `apps/gateway/tests/pgvector_deploy/Makefile` and reported MISSING. The
root Makefile is `./../../../Makefile`, the same form the ci-restoration task used for
root files. Nothing about the contract, the Musts or the tests changed — caught at the
freeze output rather than as a scope_violation at the gate.

CR v3 (2026-07-30) — Tin: the deploy target is the in-cluster StatefulSet ONLY; there
is no managed Postgres. NARROWING, decided after the build rather than guessed before it.

M3 loses the managed-Postgres shape and M6 loses the provider-privilege obligation,
because with no managed target there is nothing to verify a claim against. The v2
runbook carried an explicit UNVERIFIED block describing RDS/Cloud SQL/Azure behaviour
from general knowledge; that block is now DELETED rather than left in place. An
UNVERIFIED marker is the right hedge while a target is still undecided — once the answer
is "there is no such target", the same text stops being a hedge and becomes an invitation
to act on unverified guidance about a platform we do not run.

The runbook still SAYS managed Postgres is unsupported. Deleting the section outright
would leave a reader unable to tell "out of scope" from "nobody wrote it yet".

This closes the M6 half of the task's RISK-ACCEPTED gate. The other half — that nobody
has WALKED the runbook on a real target — is unaffected and still open.

Status: FROZEN @ v3 — approved by Tin Dang
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `scripts/` · `docs/runbooks/` · `apps/gateway/tests/pgvector_deploy/` · `./../../../Makefile`
Regression floor: `apps/gateway/tests/migrations/` (owns the existing image-parity guard this
extends — a change that contradicts it must fail there, not quietly diverge) · then the full
gateway suite before the gate, per suite-stability's now-trustworthy `make ci`.
Persona: sre-reliability-engineer

Least-sure flag surfaced at freeze: [spec] that an absent `datcollversion` means the
musl/alpine lineage specifically, rather than "some provider that does not populate the
field". Everything measured here is consistent with it, and the whole preflight rests on
it. It is a SPEC risk rather than a contract one because the shape of the command does not
change if the interpretation is wrong — only the meaning of FAIL does, and M2's separate
UNKNOWN exit is the hedge. What would settle it: running the preflight once against the
real managed target before the runbook claims anything about that target (M6).

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_preflight_exits_zero_on_current_lineage: CREATE DATABASE on the live test server
    (so it is initdb'd by the CURRENT image), run the preflight against it, assert exit 0
    and status OK · covers: M1
  - test_preflight_exits_one_when_recorded_version_is_absent: against a database whose
    recorded version is absent, assert exit 1, status FAIL, and a non-null remedy. Arranged
    by `UPDATE pg_database SET datcollversion = NULL` on a THROWAWAY database — the only way
    to manufacture a musl-era row without an alpine server. If that write is refused, the
    test SKIPS with the reason rather than passing · covers: M1
  - test_preflight_null_recorded_is_not_confused_with_empty_string: a database with
    datcollversion = '' must NOT be reported as the musl case, and NULL must be. Pins the
    §1 Boundary correction — a `= ''` check returns NULL, which is not true, and would pass
    every database this exists to catch · covers: M1, R:silent_collation_mismatch
  - test_preflight_unknown_when_server_unreachable: point it at a closed port; assert exit 2
    and status UNKNOWN, distinct from FAIL, within a bounded connect timeout · covers: M2
  - test_preflight_never_emits_refresh_collation_version: source-level assert on the
    script's own text (ast.unparse output, so a comment cannot satisfy it) that no
    `REFRESH COLLATION VERSION` string is issued · covers: R:collation_version_laundered
  - test_runbook_documents_every_preflight_status: parse docs/runbooks/pgvector-deploy.md;
    assert OK, FAIL and UNKNOWN each appear with a remedy or next action — an operator
    hitting UNKNOWN at 3am must not find it undocumented · covers: M3, R:unverifiable_runbook
  - test_runbook_covers_the_supported_deploy_shape: assert the runbook names the
    in-cluster StatefulSet PVC path with both remedies (REINDEX, dump/restore)
    · covers: M3
  - test_runbook_declares_managed_postgres_unsupported: assert the runbook says managed
    Postgres is NOT supported, so its absence cannot be misread as an omission
    · covers: M3
  - test_runbook_documents_interrupted_remedy_rollback: assert each remedy section states
    what to do if it is interrupted midway · covers: M3
  - test_no_runbook_pins_a_postgres_image_without_pgvector: scan every .md under
    docs/runbooks/ for docker image references matching postgres; assert each equals the
    single pinned pgvector image. RED TODAY on two known files (backup-rollback.md:64
    `postgres:16`, 01-getting-started.md:47 `postgres:16-alpine`) · covers: M4, M5,
    R:image_without_pgvector
  - test_runbook_image_pin_matches_the_deployed_pin: the docs pin equals the one
    tests/migrations/test_ci_workflow_parity.py already enforces across ci.yml, compose
    and the chart — one source of truth, not a second copy that can drift · covers: M5
  - test_restore_drill_accepts_a_vector_bearing_dump: pg_dump a database containing a
    vector(1536) column, restore it into a container started from the image the drill
    names, assert exit 0 and the extension present. Marked slow/docker; skips with a
    reason when the daemon is absent rather than passing · covers: M4
  - test_runbook_makes_no_provider_privilege_claim: the runbook contains no
    managed-provider privilege assertion (rds_superuser, azure.extensions,
    cloudsql.enable_pgvector) at all — there is no target to verify one against
    · covers: M6, R:unverified_provider_claim
</test_plan>

Build-guidance, NOT gated (no `covers:` tag, no red test): the preflight's human-readable
output wording; whether `make pg-preflight` reads DATABASE_URL from the environment as
well as the flag; ordering of the runbook's sections beyond the five §3 names.

The docker-dependent cases (`test_restore_drill_accepts_a_vector_bearing_dump`) and the
pg_database-write case both SKIP WITH A REASON when their precondition is missing. A skip
that reads as a pass is how the current backup runbook rotted for a release without anyone
noticing, so the skip reason is the deliverable when the precondition is absent.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/pgvector_deploy/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned, with one upgrade forced at VERIFY. M4's first test
asserted only that the drill's IMAGE carries `vector.control` — a proxy for "a
vector-bearing dump restores", and one that stays green even if pg_dump emits something
pg_restore cannot take back. Replaced with the whole drill (create vector(1536) -> insert
-> pg_dump -> pg_restore -> verify row + extension). That rewrite is what exposed the
readiness bug below.
Code lives in: `scripts/` (preflight) · `docs/runbooks/` (the runbook itself)
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests (or §4 acceptance checks) pass — 14/14 new (13 at v2, +1 for CR v3's
      "managed Postgres is declared unsupported" guard); regression floor
      `tests/migrations` green; `make ci` exit 0, 4513 passed, 32m44s (2026-07-30 @ v2)
      and re-run at v3 (2026-07-30) covering the CR v3 doc/test change
- [x] coverage did not decrease — `--cov-fail-under=80` holds inside `make ci`
- [x] no test or contract was altered during build — §4 tests were fixed BEFORE the
      build (three guard defects, below); the frozen §3 shape is unchanged. CR v2 was a
      scope-TOKEN correction only. CR v3 changed §1 M3/M6 and therefore the §4 tests —
      recorded as a CR and re-crossed direction->build so the tripwire re-snapshotted,
      NOT edited underneath a frozen build.
- [x] the green was EARNED, not gamed — one vacuous green was found and killed; see
      the refute-read verdict below
- [x] concurrency / timing of the risky operation is safe — the preflight is read-only
      with a bounded connect; the drill polls readiness over TCP, not a fixed sleep
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new
      packages; the preflight only ever executes SELECT (guarded)
- [x] layering & dependencies follow CONVENTIONS.md — an operator script under
      `scripts/`, no gateway import surface touched
- [ ] a person reviewed and approved the change — OPEN, see the gate record

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED — after one NOT-EARNED green was caught and removed.
By: self · adversarially checked:
  * THE VACUOUS GREEN. The first end-to-end drill test passed in 2.84s. That is
    implausibly fast for a container start plus initdb, so it was run by hand: rc=2,
    empty stdout, `FATAL: the database system is shutting down`. `docker-entrypoint.sh`
    runs a TEMPORARY server on the unix socket while initialising, which accepts
    connections and then stops — socket `pg_isready` went green against it and a fast
    drill finished before it vanished. The test was winning a race, not proving a
    restore. Fixed by polling TCP, which the entrypoint opens only after init completes.
    A longer sleep would have papered over it and stayed race-dependent — the same
    fixed-wait defect as todo #82, written up hours earlier.
  * NEGATIVE CONTROL on the drill: the same script against stock `postgres:16` fails
    with `extension "vector" is not available` — the exact error the old runbook would
    have handed an operator mid-incident.
  * DOC GUARDS ATTACKED: reverting both image-pin fixes turns all three doc tests red,
    covering the code-block pin AND the table-cell pin.
  * THREE GUARD DEFECTS were fixed in the tests, not the code: the
    REFRESH-COLLATION-VERSION guard tripped first on pg_preflight's own warning
    DOCSTRING, then on the remedy text it PRINTS (punishing the tool for warning about
    the trap); the doc-pin scanner flagged the new runbook's own prose naming the bad
    image. Each guard was narrowed to the property that is actually checkable.
  * THE PREFLIGHT WAS RUN AGAINST A REAL DATABASE, not a mock, and correctly returns
    FAIL/exit 1 on the live dev volume — which is genuinely musl-lineage.

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: RISK-ACCEPTED
If RISK-ACCEPTED -> owner: Tin Dang · ticket: M3 operator walkthrough on a real target — nobody has WALKED the runbook end to end, and a green suite cannot sign that off (M6 is CLOSED by CR v3: with no managed target there is no provider privilege claim left to verify) · expires: 2026-09-30
Reviewed by: Tin Dang · date: 2026-07-30

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v2 (approved by Tin Dang)
- [AI] build — strategy used: as planned, with one upgrade forced at VERIFY. M4's first test asserted only that the drill's IMAGE carries `vector.control` — a proxy for "a vector-bearing dump restores", and one that stays green even if pg_dump emits something pg_restore cannot take back. Replaced with the whole drill (create vector(1536) -> insert -> pg_dump -> pg_restore -> verify row + extension). That rewrite is what exposed the readiness bug below.
- [AI] verify — gate RISK-ACCEPTED (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
