# PLAN: Boot-time fail-closed preflight for the pgvector vector extension

slug: vector-extension-preflight · created: 2026-08-05 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A gateway whose database lacks the `vector` extension refuses to boot with a named,
actionable error, instead of booting and serving `/v1/vector_stores` that 500s at first use.

Framings weighed:
- **Startup preflight that refuses to boot** (chosen) — the milestone's own recorded shared
  decision: "The preflight fails CLOSED. A gateway that cannot confirm the `vector` extension
  refuses to boot rather than serving RAG surfaces that 500 at first use." A deploy fault is
  discovered by the operator at rollout, when a rollback is one command, rather than by a
  tenant at 3am.
- Lazy check at first `/v1/vector_stores` call → named 503 — rejected: that IS the status quo's
  failure mode with better wording. A broken deploy stays green on every dashboard until a
  tenant happens to use RAG, which is exactly the silence this task exists to end.
- Warn-only at startup — rejected: a warning in a log nobody reads is how the `pgvector` image
  swap shipped unrunbooked in #89 in the first place.

Must:
<must>
  - M1 entering the app lifespan against a database WITHOUT the `vector` extension raises a
    named preflight error and the application does not become ready
  - M2 the error names the missing extension, the database it checked, and the remedy
    (`CREATE EXTENSION vector`) — an operator can act on it without reading source
  - M3 the preflight runs BEFORE the vector-store ingest worker starts, and before the
    dev/test `Base.metadata.create_all` bootstrap (whose `Vector(1536)` column would
    otherwise fail first with an opaque SQLAlchemy error)
  - M4 a database WITH the extension boots exactly as before — no new startup failure mode
</must>
Reject:
<reject>
  - database unreachable / auth refused at preflight time -> "ERR_VECTOR_PREFLIGHT_UNKNOWN"
    (NOT reported as a missing extension — mirrors `scripts/pg_preflight.py`'s UNKNOWN
    discipline: "could not check" must never be renamed to a different, wrong diagnosis)
</reject>
After:
<after>
  - a gateway that is serving has a confirmed `vector` extension in its own database
  - release-integrity exit criterion 4 is satisfiable by an executable test, not an assertion
</after>
Boundary: none — no external request input. The one input shape is the connected database:
extension present · extension absent · unreachable.
<assumptions>
  ⚠ That refusing to boot is right for EVERY deployment — including one that never uses RAG.
  Today such an operator gets a *partial* degradation (everything works except vector stores);
  after this task they get a *total outage* on upgrade. If wrong: the fail-closed guard is
  itself the incident. Mitigation shape (NOT built here, flagged for the freeze): a
  `vector_extension_preflight_enabled` opt-out, which is only honest if the vector-store
  router also degrades to a named 503 rather than continuing to 500 — otherwise the opt-out
  just restores the bug.
  RESOLVED AT FREEZE (Tin, 2026-08-05, via AskUserQuestion): **fail closed always, NO opt-out.**
  Four shapes were put up — no-opt-out · opt-out that also degrades the router to a named 503 ·
  bare skip flag · warn-only. Tin took the first. Risk accepted with eyes open: the remedy is a
  one-line `CREATE EXTENSION vector` that the error message itself states, and every supported
  deploy target already pins `pgvector/pgvector:pg16` (ci.yml · docker-compose.{dev,e2e,prod}.yml ·
  charts/ai-proxy/values.yaml, parity guarded by `tests/migrations/test_ci_workflow_parity.py`),
  so a vector-less database is out-of-spec rather than a supported configuration. The bare skip
  flag was explicitly advised against — it restores the 500 this task exists to remove.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

Grounding (real symbols, verified in-tree 2026-08-05):
- `main.py::create_app::lifespan` — startup begins at the `# ── Startup ──` marker; the
  dev/test `Base.metadata.create_all` bootstrap is the FIRST thing it does, gated on
  `_settings.environment in ("dev", "test")`. `_engine = app.state.engine` is already bound
  above it. The preflight goes immediately BEFORE that bootstrap (satisfies M3 a fortiori).
- `main.py` — `should_start_vector_store_ingest_worker(_settings)` guards the ingest worker
  (`app.state.vector_store_worker_task`), far later in the same startup body.
- `migrations/versions/55dc3f920a38_vector_store_core.py` — runs
  `CREATE EXTENSION IF NOT EXISTS vector`; this is the production install path. The gap this
  task closes is a gateway pointed at a database where that never ran (image override,
  restored/replaced volume, or a managed target that blocks the extension — todo #67).
- `tests/pgvector_deploy/test_pgvector_deploy.py` — existing precedent for provisioning a
  throwaway `CREATE DATABASE` in a test; the red suite reuses that shape for a vector-less DB.
- `scripts/pg_preflight.py` — the COLLATION preflight (todo #66). Different hazard, deliberately
  NOT extended: that one is an operator-run CLI against an arbitrary target; this one is an
  in-process boot guard on the gateway's own connection.

```
Port:   gateway.vector_stores.infrastructure.preflight
          async def assert_vector_extension(engine: AsyncEngine) -> None
            returns None            -> extension confirmed present
            raises VectorExtensionMissingError  -> confirmed ABSENT   (M1/M2)
            raises VectorPreflightUnknownError  -> could not check    (R:unknown)
          Both carry a `code` attribute: "ERR_VECTOR_EXTENSION_MISSING" /
          "ERR_VECTOR_PREFLIGHT_UNKNOWN", and a message naming database + remedy.
Probe:  SELECT 1 FROM pg_extension WHERE extname = 'vector'   (needs no application schema,
          so it is valid before create_all — M3)
Call:   main.py lifespan, immediately before the dev/test create_all bootstrap; the raise
          propagates out of the lifespan, so the ASGI server never reports ready (M1).
Schema: reads pg_extension + current_database() only. Writes nothing.
```

Target (measurable): the §4 suite runs RED before build (the module does not exist) and GREEN
after — 5/5. `make ci` stays green on main's current bar (4514 passed, 0 failed) with the new
suite added. "Refuses to boot" is not directly observable from a passing request, so it is
confirmed by asserting the lifespan context manager RAISES the named error, and that
`app.state.vector_store_worker_task` was never assigned on that path.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Strategy: one new leaf module + one call site. The probe is a single `SELECT` against
`pg_extension`, so the module has no dependency on the vector-store domain and cannot pull the
composition root inward. Build order: red suite first (it provisions a throwaway vector-less
database, reusing `tests/pgvector_deploy`'s `CREATE DATABASE` shape), then the module, then the
one-line lifespan call. The two error classes are separate types rather than one with a flag,
so a caller cannot accidentally treat "could not check" as "confirmed absent".

Scope (may touch): `apps/gateway/src/gateway/vector_stores/infrastructure/preflight.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/vector_extension_preflight/`
Regression floor: `apps/gateway/tests/ops/test_lifespan.py` · `apps/gateway/tests/vector_store_core/` · `apps/gateway/tests/vector_store_files/` · `apps/gateway/tests/realtime/` (the four suites that drive a real lifespan hardest); then full `make ci` before the gate.
Persona (optional): `sre-reliability-engineer` — "Reliability is a feature — verify the environment, degrade safely, never fail silently." This task is that sentence.

Least-sure flag surfaced at freeze: [spec] — whether fail-closed-by-default is correct for a
deployment that does not use RAG at all. Today such an operator has a working gateway minus one
surface; after this task they have no gateway. That converts a partial degradation into a total
outage on upgrade, and it is a SPEC choice, not an implementation detail. §1's ⚠ carries the
mitigation shape and the decision needed at freeze.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_lifespan_refuses_to_boot_without_vector_extension: provision a throwaway database
    with CREATE DATABASE and do NOT create the extension; build a Settings pointing at it;
    assert entering the app lifespan raises VectorExtensionMissingError · covers: M1
  - test_error_names_database_extension_and_remedy: assert the raised error's message contains
    the database name, the string "vector", and "CREATE EXTENSION" — an operator can act on it
    without reading source; assert .code == "ERR_VECTOR_EXTENSION_MISSING" · covers: M2
  - test_ingest_worker_never_starts_when_preflight_fails: on the same vector-less database,
    assert the lifespan raised AND app.state has no vector_store_worker_task — proving the
    preflight ran before the worker wiring, not after it · covers: M3
  - test_boots_normally_when_extension_present: against the ordinary test database (extension
    created by the shared conftest), the app enters and exits its lifespan with no error and
    still serves a request · covers: M4
  - test_unreachable_database_is_unknown_not_missing: point the preflight at a closed port;
    assert VectorPreflightUnknownError with .code == "ERR_VECTOR_PREFLIGHT_UNKNOWN", and
    NOT VectorExtensionMissingError — "could not check" is never renamed to a wrong
    diagnosis · covers: R:ERR_VECTOR_PREFLIGHT_UNKNOWN
</test_plan>

Build-guidance (prose, NOT gated): the probe should be bounded like every other outbound IO in
this codebase (PROJECT.md invariant: "No outbound IO without timeout"); a connect timeout on the
preflight engine is enough — it is one SELECT, not a retry loop. Log one structured line on the
success path so an operator can see the check ran at all.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/vector_extension_preflight/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `src/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — §4 suite 5/5 (RED 5/5 on `ModuleNotFoundError` before build). Regression
      floor 69 passed (`ops/test_lifespan` · `vector_store_core` · `vector_store_files` ·
      `realtime`). Full `make ci` **✅ pipeline green**: 4519 passed / 7 skipped / 1 xfailed /
      **0 failed**, 33m49s. `grep -c "Connect call failed"` = 0, so the run was not an
      infra-dropout artifact ([[make-ci-infra-dropout-looks-like-regression]], todo #83).
- [x] coverage did not decrease — 90.94% vs main's 90.95% pre-task (gate 80%). The −0.01pp is
      the new module's own two unreached defensive branches, not a regression elsewhere.
- [x] no test or contract was altered during build — the only test files touched are this
      task's own new suite; `git status` shows exactly the three declared Scope paths.
- [x] the green was EARNED — see the refute-read below; one vacuous assert was found and
      fixed BEFORE it counted.
- [x] concurrency / timing safe — a single `await` in lifespan startup before any background
      task exists; no shared state; BOUNDED by `asyncio.timeout(10s)` per the PROJECT.md
      invariant "No outbound IO without timeout". `TimeoutError` is caught ahead of the broad
      clause precisely because it subclasses `OSError` and would otherwise be mislabelled.
- [x] no exposed secrets — TESTED, not reasoned: drove both failure paths with a canary
      password `sup3rs3cr3t-pw-CANARY` in the DSN. LEAK=False on bad-password
      (`InvalidPasswordError: password authentication failed for user "gateway"`) and on
      closed-port (`ConnectionRefusedError: [Errno 61] ...`). No new dependency; no SQL
      injection surface (the probe is a module-level constant with no interpolation).
- [x] layering follows CONVENTIONS.md — the module is a leaf under
      `vector_stores/infrastructure/` depending only on sqlalchemy + structlog; `main.py`
      (composition root) imports it, never the reverse. Dependencies point inward.
- [ ] a person reviewed and approved the change — Tin froze §1–§4 (the ONE approval) and made
      the fail-closed spec call; the post-build diff has NOT yet been human-reviewed.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked:
 (a) **Is the arrange real, or vacuous?** The whole suite rests on "a throwaway database has no
     vector extension". VERIFIED against the live image rather than assumed: fresh
     `CREATE DATABASE` -> `pg_extension` count 0, template1 -> 0, `pg_available_extensions` -> 1.
     So the database is genuinely vector-less AND the remedy the error advertises would really
     work there. The fixture now ASSERTS count==0 before yielding, so a future image that ships
     the extension in template1 fails loudly instead of hollowing out all five tests.
 (b) **A vacuous assert was FOUND AND FIXED.** The first M4 test asserted only
     `assert app is not None` — it PASSED while `preflight.py` did not exist, i.e. a green that
     proved nothing (the exact class todo #77/#84 track). Rewritten to drive the real probe
     against the real healthy database. This arm matters more than the happy path: a
     fail-closed guard with a non-zero false-positive rate is a TOTAL outage, so the
     "does not reject a correct database" direction has to be gated, not assumed.
 (c) **Does the test actually prove "refuses to boot", or just that a function raises?** The
     unit tests drive `app.router.lifespan_context`, which is a harness, not a server. So this
     was confirmed END-TO-END against the REAL production entrypoint — `gateway.main:create_app
     --factory`, byte-identical to the Dockerfile CMD — pointed at a vector-less database:
     exit code **3**, `VectorExtensionMissingError` with the remedy naming the actual database,
     `Application startup failed. Exiting.`, and **"Application startup complete" never
     emitted**. The process genuinely does not become ready.
 (d) **Can "could not check" be mistaken for "confirmed absent"?** Two distinct types, and
     `VectorPreflightUnknownError` is deliberately NOT a subclass of the Missing error; the
     bad-password probe in (a) returns UNKNOWN, so a rotated credential does not send an
     operator to run `CREATE EXTENSION` on a perfectly healthy database.
 (e) **Ordering** — asserted directly (`vector_store_worker_task is None` on the reject path),
     not inferred from source position, so moving the call later re-reds the suite.

Residue (recorded, not waved through): `make ci` deselects `e2e` and `kind_e2e` (28 deselected),
so the edge/kind stacks did NOT exercise this preflight. Both pin `pgvector/pgvector:pg16` and
run migration `55dc3f920a38` (which installs the extension), so they are expected to pass — but
that is an expectation, not evidence. `kind-e2e` is a 45-min opt-in job; run it before the next
release cut per CONTRIBUTING.md.

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-08-06

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
