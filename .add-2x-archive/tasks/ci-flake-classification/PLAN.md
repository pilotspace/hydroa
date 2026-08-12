# PLAN: Classify the 8 failures the first completed CI run exposed, starting with the egress security control

slug: ci-flake-classification · created: 2026-08-08 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: classify every failure the first completed CI run surfaced as flake-vs-real, fix the flakes at their cause rather than by retrying, and close the one real defect.

Framings weighed: **classify each site individually, convert only positive waits** (chosen — todo #79 already establishes that ~29 of the suite's sleeps are NEGATIVE, where the sleep IS the test; a blanket sweep would silently make those vacuous) · *add `--reruns 1` to test-ci* — rejected: it re-hides exactly the tail this run exposed, and a self-healing gate is the opposite of what R6 is for · *treat all 8 as flakes and move on* — rejected: it would have buried the egress finding, which is real.
Must:
<must>
  - M1: every positive fire-and-forget wait among the 8 failures polls for the awaited state instead of sleeping a fixed interval.
  - M2: every NEGATIVE wait keeps its fixed sleep AND carries a comment saying why, so a later sweep cannot convert it into a vacuous assertion.
  - M3: a MIXED assertion ("exactly one — not zero, not two") preserves both halves: poll for existence, then settle and re-count.
  - M4: CI and dev run the same Python down to the patch, asserted by a guard.
</must>
Reject:
<reject>
  - converting a sleep whose purpose is to prove something NEVER happens -> "vacuous_green"
  - a bare minor-series Python pin ("3.12") in CI -> "interpreter_drift"
</reject>
After:
<after>
  - the 3 previously-unclassified failures are named as flake or real, with evidence
  - the egress failure is understood at root cause and filed, not waved through
  - a re-run of the converted suites is green and no longer interval-dependent
</after>
Boundary: none — no external input; the work is test-timing and version pinning.
<assumptions>
  ⚠ that polling removes these failures rather than merely widening the window. If wrong: they recur in a later full run under heavier contention, and the real cause is elsewhere (ordering, or a genuine lost write). Bounded: poll_until has a 10s ceiling vs the old 0.1-0.3s, ~30-100x more headroom.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
Classification of run 31243949907's 8 failures (4553 passed):
  ALREADY FILED (5)  guardrails/test_guardrails_core        -> todo #74 names the file
                     request_log_metering_fields x2         -> todo #74 names the file
                     realtime STT DeadlockDetectedError     -> Makefile test-parallel comment
                     batches/test_batch_window_grouping     -> v58 milestone notes
  FLAKE, converted   tool_call_metering/test_di_wiring:73   -> poll (flush inside)
                     tool_call_metering/test_di_wiring:229  -> poll Redis
                     agent_identity_governance:1061         -> poll + settle + re-count
  NEGATIVE, kept     tool_call_metering/test_di_wiring:112  -> comment added, sleep retained
  REAL (1)           edge_input_hardening/test_s3_egress_policy
                       root cause: ipaddress.is_reserved differs across 3.12 patches
                       -> pin 3.12.13 in .python-version + ci.yml + guard  (todo #97)

Guard: test_ci_python_version_is_patch_pinned_and_matches_dev  covers: M4
```

Target (measurable): all 3 previously-unclassified failures named with evidence; converted suites green (35 passed); tests/migrations green (33 passed) including the new pin guard; `make lint` + `make typecheck` clean. NOT confirmable by test: that these no longer flake under real CI contention — only a future full run shows that.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/tests/tool_call_metering/test_di_wiring.py` · `apps/gateway/tests/agent_identity_governance/test_agent_identity_governance.py` · `apps/gateway/tests/migrations/test_ci_workflow_parity.py` · `apps/gateway/.python-version` · `.github/workflows/ci.yml`
Regression floor: `apps/gateway/tests/migrations/` + the two converted suites
Persona (optional): `.add/personas/appsec-engineer.md` — the egress finding is a security control whose verdict varied by environment; "verify both failure directions" is exactly what established it was fail-closed rather than a bypass.

Least-sure flag surfaced at freeze: [test] whether polling truly fixes these or merely widens the window. Local runs cannot reproduce CI's 4-way contention, so the proof is a future full CI run, not this task's evidence.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_ci_python_version_is_patch_pinned_and_matches_dev: read .python-version + ci.yml's setup-uv pin; assert the file names an exact patch (regex) and the two agree · covers: M4, R:interpreter_drift
</test_plan>

M1-M3 are conversions of EXISTING tests, not new behavior, so their proof is those tests still passing plus the recorded per-site classification above — not a new red test. The one genuinely new invariant (M4) does get a red-first guard, and it ran red (FileNotFoundError: no .python-version) before the pin landed.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/migrations/test_ci_workflow_parity.py` · ran red before the pin.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned, with one honest process deviation recorded below. The classification was decided by READING each assertion rather than by re-running until something failed — faster and more reliable, since local runs cannot reproduce CI's contention. The egress root cause was found by executing BOTH interpreters (`uv run --python 3.12.3` vs `3.12.13`) against the actual predicates, which turned a suspected timing flake into a proven version dependency.

DEVIATION (process, recorded not hidden): this task was scaffolded and the work was then done before §1-§4 were drafted, so the freeze here is retroactive. The engineering is verified either way, but the bundle was written after the fact rather than driving the build, and that is worth naming.
Code lives in: `apps/gateway/tests/` · `apps/gateway/.python-version` · `.github/workflows/ci.yml`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

Evidence:
- 3 previously-unclassified failures named with evidence; 5 others mapped to existing todos.
- Converted suites: 35 passed. Regression floor `tests/migrations/`: 33 passed (incl. the new guard).
- Pin guard ran RED first (FileNotFoundError, no .python-version), green after.
- `make lint` + `make typecheck` both clean against the REAL gate (not a direct per-file invocation, which bypasses ruff's exclude list).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked: the specific way this task could produce a fake green is by converting a NEGATIVE wait into a poll, which returns on its first iteration and asserts nothing. Every site was therefore read for its assertion direction before conversion, not pattern-matched on `asyncio.sleep`. One site (di_wiring:112, `rows == []`) was found to be exactly that case and deliberately KEPT its sleep with a comment; one (agent_identity:1061, "not zero, not two") was found to be MIXED and kept a settle-then-recount so the duplicate check survives. Separately, the egress conclusion was checked in both directions rather than assumed: `::ffff:169.254.169.254` remains denied on BOTH 3.12.3 and 3.12.13, so the version difference is fail-closed and never a metadata/SSRF bypass.

### GATE RECORD
Reported: no
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-08-09
Residue (filed, not fixed here): todo #97 egress version-dependency analysis · todo #98 deployed image still unpinned (needs a docker build to verify, deliberately not bundled) · todos #74/#79 remaining sleep sites across the suite.

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose **classify each site individually, convert only positive waits**; rejected *add `--reruns 1` to test-ci* — rejected: it re-hides exactly the tail this run exposed, and a self-healing gate is the opposite of what R6 is for · *treat all 8 as flakes and move on* — rejected: it would have buried the egress finding, which is real.
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned, with one honest process deviation recorded below. The classification was decided by READING each assertion rather than by re-running until something failed — faster and more reliable, since local runs cannot reproduce CI's contention. The egress root cause was found by executing BOTH interpreters (`uv run --python 3.12.3` vs `3.12.13`) against the actual predicates, which turned a suspected timing flake into a proven version dependency.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
