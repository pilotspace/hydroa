# PLAN: Single-source the version, backfill tags, guard the drift

slug: release-provenance · created: 2026-08-07 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: One version source of truth, read at runtime, with a test that goes red the moment
the sources drift — so "what version is in production?" has a defensible answer.

Today it has five answers, and the one every API consumer sees is the most wrong:

```
git tags        v0.8.0 then v0.11.0   (v0.9.0, v0.10.0, v0.12.0, v0.13.0 MISSING)
RELEASES.md     newest row is 0.12.0  (0.13.0 shipped and was never recorded)
pyproject.toml  0.7.0
main.py         0.1.0   <-- FastAPI(version=...), served at /openapi.json
dashboard       0.1.0
```

Framings weighed:
- **Single-source at runtime + a drift guard + backfill the record** (chosen, Tin at the
  2026-08-07 interview). The guard is the part that lasts: without it these five drift
  apart again by the next release, and R8's CC8.1 evidence export has to be re-derived by
  hand every time.
- Fix `main.py` only — rejected as the primary framing though it is the highest-blast-radius
  single line. `/openapi.json` advertising 0.1.0 is what an external consumer pins against,
  but fixing it without the guard just resets the drift clock.
- Defer to R8 — rejected: R8's deliverable is the change-management EVIDENCE, and evidence
  is cheap to export only if provenance is already single-sourced. Building it during the
  compliance milestone means building it under deadline.

Must:
<must>
  - M1 `/openapi.json` reports the package's real version, not a hardcoded literal
  - M2 the gateway version has exactly ONE editable source; `main.py` reads it rather than
    restating it
  - M3 a test FAILS when the sources drift — specifically when `main.py`'s served version
    and `pyproject.toml`'s `[project].version` disagree, or when RELEASES.md's newest entry
    is behind `pyproject.toml`
  - M4 RELEASES.md carries a 0.13.0 entry (shipped, never recorded)
  - M5 the missing git tags v0.9.0, v0.10.0, v0.12.0, v0.13.0 are created against the
    commits Tin confirms — tags are OUTWARD-FACING and pushed, so the commit mapping is
    proposed here and applied only on his word, never inferred silently
</must>
Reject:
<reject>
  - a guard that asserts against git TAGS -> REFUSED as a test. A shallow CI clone has no
    tags, so such a test would be green-because-absent — the worst kind. Tag correctness is
    a documented release STEP with a checklist, not a unit test pretending to cover it.
</reject>
After:
<after>
  - "what version is in production" is answerable from the artifact itself
  - todo #86 closed
</after>
Boundary: version strings only. Both the PEP 440 form in `pyproject.toml` (`0.13.0`) and
the tag form (`v0.13.0`) appear, and the guard must speak both.
<assumptions>
  ⚠ That the DASHBOARD should be versioned independently. `apps/dashboard/package.json` is
  0.1.0 and is a separate deployable; forcing it to track the gateway's number would make
  every gateway patch bump a UI release with no UI change. I am therefore scoping M1–M4 to
  the gateway and leaving the dashboard alone with an explicit note, rather than quietly
  including it. If you want one repo-wide version, say so at the freeze — it changes M2
  from "one source" to "one source, two consumers".
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

Grounding (read in-tree 2026-08-07):
- `apps/gateway/pyproject.toml` — `[project] name = "hydroa-gateway"`, `version = "0.7.0"`.
  The distribution name matters: `importlib.metadata.version()` keys on it, not on the
  import package `gateway`.
- `apps/gateway/src/gateway/main.py:1107` — `FastAPI(title="Hydroa Gateway",
  version="0.1.0", lifespan=lifespan)`. This literal IS `/openapi.json`'s `info.version`.
- `git tag --sort=v:refname` -> `v0.1.0 v0.2.0 v0.3.0 v0.4.0 v0.5.0 v0.6.0 v0.7.0 v0.8.0
  v0.11.0` — nine tags, four gaps.
- `RELEASES.md` — newest section `## 0.12.0 — 2026-07-24`; contains 0.1.0 through 0.12.0.
  Earlier rows note "recorded by add.py release", but the current engine's verb list has no
  `release` command, so the file is hand-maintained now — which is exactly why 0.13.0 was
  missed.
- Searched `apps/gateway/src/gateway` for `importlib.metadata` and `__version__`: **no
  hits**. Nothing reads a version at runtime today, so there is no existing seam to reuse
  and no consumer to break.
- Proposed tag->commit mapping, for Tin to confirm (M5), taken from RELEASES.md evidence
  lines and the milestone record, NOT guessed:
  RESOLVED, and my first proposal was WRONG. Checking what the two existing tags actually
  point at showed the convention is "tag where that version's notes LANDED on main":
  `v0.8.0 -> 573f01a` (a notes commit direct on main) and `v0.11.0 -> 8daf22c` (the MERGE
  of `chore/release-0-11-0`, NOT its cut commit `cfd85f6`). Every release has a cut commit
  touching RELEASES.md, so the mapping is derivable rather than guessed:
  * `v0.9.0  -> b55f86c` (2026-07-14, "release(0.9.0): Agent gateway") — direct on main
  * `v0.10.0 -> d126a9c` (2026-07-18, "chore(release): cut 0.10.0") — direct on main
  * `v0.12.0 -> 5986d81` (2026-07-24, "Merge PR #88 chore/release-0.12.0") — landed via a
    release PR, so the MERGE, matching v0.11.0's precedent. NOTE this supersedes the
    `71e55c3` I first proposed: that is PR #87, the FEATURE merge, which RELEASES.md cites
    as evidence but which is not where the notes landed.
  * `v0.13.0 -> the commit this task creates when it adds the 0.13.0 row` — there is no cut
    commit for 0.13.0, which is precisely the defect M4 fixes, so M4 produces the very
    commit the convention says to tag.
  CONFIRMED by Tin, 2026-08-07 interview.

```
apps/gateway/src/gateway/__init__.py
  __version__: str = importlib.metadata.version("hydroa-gateway")
     Falls back to a literal ONLY if the distribution is not installed (an editable-install
     edge, e.g. running from a source tree with no metadata) — and the fallback must be the
     SAME string pyproject holds, which is precisely what the M3 guard enforces.

apps/gateway/src/gateway/main.py
  FastAPI(title="Hydroa Gateway", version=__version__, lifespan=lifespan)

apps/gateway/tests/release_provenance/test_release_provenance.py
  test_openapi_version_matches_pyproject()      # M1 — asserts on the SERVED document
  test_main_does_not_hardcode_a_version()       # M2 — no version= literal in main.py
  test_releases_md_is_not_behind_pyproject()    # M3 — newest RELEASES.md >= pyproject
  Version comparison is PEP 440 tuple-wise, never string equality: "0.9.0" < "0.10.0"
  lexicographically is FALSE, and that trap is the whole reason the tag list looks sorted
  when it is not.
```

Target (measurable): the §4 suite runs RED before build (main.py still says 0.1.0) and
GREEN after. `/openapi.json` served by the real app reports the pyproject version —
asserted against the actual ASGI response, not by reading source. `make ci` stays green at
4531 passed. Tag backfill (M5) is confirmed by `git tag --sort=v:refname` showing an
unbroken v0.1.0..v0.13.0 run, recorded in §6 as evidence rather than gated by a test.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Strategy: guard first (it is red for a real reason today), then the single source, then the
record. Tag creation is LAST and separate — it is the only step that touches published
history, so it happens after everything else is green and only with Tin's confirmed mapping.

Scope (may touch): `apps/gateway/src/gateway/__init__.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/pyproject.toml` · `apps/gateway/tests/release_provenance/` · `./../../../RELEASES.md` · `apps/gateway/uv.lock`

Regression floor: full `make ci`. Note `pyproject.toml` moving 0.7.0 -> 0.13.0 is a real
version bump on an installed package; the floor is what proves nothing pinned the old one.
Persona (optional): `appsec-engineer` — "if you cannot evidence which artifact ran, you
cannot evidence anything about it."

DECIDED by Tin, 2026-08-07 interview: `pyproject.toml` -> **0.13.0** (the honest
description of what main is today; the guard forces the question again at the next cut),
and the DASHBOARD stays independently versioned at 0.1.0 — a gateway patch must not ship a
phantom UI release. M1-M4 are gateway-scoped accordingly.

Least-sure flag surfaced at freeze: [contract] — what `pyproject.toml`'s version should
BECOME. It says 0.7.0; the last recorded release is 0.12.0; the last SHIPPED release is
0.13.0. Setting it to 0.13.0 declares that the current main IS 0.13.0, which is true today
and stops being true the moment the next milestone merges. The alternative is 0.14.0-dev
with the guard comparing minor-and-above. I lean 0.13.0 for now because it is the honest
description of what is deployed and the guard will force the question at the next cut — but
this is a release-convention call that is yours, not mine.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_openapi_version_matches_pyproject: drive the REAL app and read
    /openapi.json["info"]["version"]; assert it equals pyproject's [project].version.
    Asserted against the served document, never by reading main.py — the whole defect is
    that what is served and what is declared disagree · covers: M1
  - test_main_does_not_hardcode_a_version: assert no version= string literal in the
    FastAPI(...) construction. Without this, someone "fixes" a future drift by editing the
    literal to match and the single source quietly dies · covers: M2
  - test_releases_md_newest_entry_is_not_behind_pyproject: parse the newest `## X.Y.Z`
    heading; assert it is >= pyproject's version · covers: M3, M4
  - test_version_comparison_is_pep440_not_lexicographic: assert 0.9.0 < 0.10.0 < 0.13.0
    under the comparison the guard uses. Lexicographically "0.9.0" > "0.10.0", which is
    exactly why the tag list LOOKS sorted while missing four entries — the bug this task
    exists to fix could re-enter through its own guard · covers: M3
  - test_dashboard_version_is_independent: assert the guard does NOT require
    apps/dashboard/package.json to match. Tin's call, gated so a later reader does not
    "helpfully" couple them · covers: M3
  - test_version_is_importable_without_installed_metadata: force the
    importlib.metadata.PackageNotFoundError path; assert the fallback equals pyproject's
    version rather than raising at import. A gateway that cannot start from a source tree
    would be a worse defect than the one being fixed · covers: M2

  NOT TESTED, BY DESIGN — recorded in §6 as evidence instead (see §1 Reject): git tag
  correctness. A shallow CI clone has no tags, so such a test would be
  green-because-absent. `git tag --sort=v:refname` showing an unbroken v0.1.0..v0.13.0 run
  is the evidence, confirmed by eye at the gate · covers: M5
</test_plan>

RED evidence — run 2026-08-07, `pytest tests/release_provenance -q`:
```
3 failed, 3 passed in 1.20s

test_openapi_version_matches_pyproject
  AssertionError: /openapi.json advertises '0.1.0' while pyproject declares '0.7.0'
test_main_does_not_hardcode_a_version
  AssertionError: main.py still hardcodes a version literal (['version="0.1'])
test_version_is_importable_without_installed_metadata
  AttributeError: module 'gateway' has no attribute '__version__'
```
The three reds are the three halves of the defect: what is served, where it comes from,
and the absence of a single source. Each names the real current value, so none of them is
red merely because a symbol is missing.

The 3 that PASS today are guard-shape arms, and two of them become load-bearing during
build rather than after it:
- `test_releases_md_newest_entry_is_not_behind_pyproject` passes only because pyproject
  still says 0.7.0 while RELEASES.md reaches 0.12.0. The moment M4 sets pyproject to
  0.13.0 this goes RED, and it returns to green when the 0.13.0 row is written. That is
  the intended red->green for M3/M4 and it must be observed, not skipped past.
- `test_version_comparison_is_pep440_not_lexicographic` and
  `test_dashboard_version_is_independent` are anti-regression arms — they gate the two
  ways this fix could recreate the bug it removes (string ordering; coupling the UI's
  version to the gateway's).

ruff format + check: clean. pyright: clean.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/release_provenance/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned — guard first, then the single source, then the record,
with tag creation held back as a separate step because it is the only part that touches
published history.

GREEN evidence — 2026-08-07:
```
tests/release_provenance -> 6 passed in 0.99s
ruff format + check: All checks passed!   pyright: 0 errors, 0 warnings
```
Verified the version comes from REAL distribution metadata and not the fallback:
`importlib.metadata.version("hydroa-gateway")` -> 0.13.0, `gateway.__version__` -> 0.13.0.
Had the fallback been masking a stale install, M1 would have passed for the wrong reason.

The M3/M4 red was OBSERVED, not stepped past. §4 recorded that
`test_releases_md_newest_entry_is_not_behind_pyproject` passes at freeze only because
pyproject still said 0.7.0, and must go red once pyproject reaches 0.13.0. With the bump
applied and the row still absent:
```
AssertionError: RELEASES.md stops at 0.12.0 but pyproject declares 0.13.0.
                A shipped release with no notes is how 0.13.0 went unrecorded in the
                first place.
1 failed, 5 passed
```
It returns to green with the 0.13.0 row in place. Both transitions were run.

`uv.lock` moves too — one line, `hydroa-gateway 0.7.0 -> 0.13.0`. No dependency drift.

M5 (tags) — mapping RESOLVED against the convention rather than guessed. The two existing
tags either side of the gaps show the rule is "tag where that version's notes LANDED on
main": `v0.8.0 -> 573f01a` (a notes commit direct on main) and `v0.11.0 -> 8daf22c` (the
MERGE of chore/release-0-11-0, not its cut commit). Applying that:
  v0.9.0  -> b55f86c  2026-07-14  release(0.9.0): Agent gateway ...        (direct on main)
  v0.10.0 -> d126a9c  2026-07-18  chore(release): cut 0.10.0 ...           (direct on main)
  v0.12.0 -> 5986d81  2026-07-24  Merge pull request #88 chore/release-0.12.0
  v0.13.0 -> the commit that adds the 0.13.0 row (this task's own commit)
All four commits verified to resolve. NOTE this supersedes an earlier proposal of
`71e55c3` for v0.12.0 — that is PR #87, the FEATURE merge, which RELEASES.md cites as
evidence but which is not where the notes landed. Tags are created locally and NOT pushed
without explicit authorization: pushing a tag is outward-facing and effectively permanent.
Code lives in: `src/`
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

`make ci` GREEN on the full tree — 2026-08-07, exit 0:
```
4559 passed, 7 skipped, 28 deselected, 1 xfailed, 0 failed   37:00
coverage 91.11% (main was 90.94%)   infra-guard trips: 0
```
main carried 4531; +28 = the three suites landed in this run (10 + 12 + 6).

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: self · adversarially checked:
- **Is M1 green because the fallback happens to match?** That would pass for the wrong
  reason. Checked directly: `importlib.metadata.version("hydroa-gateway")` -> 0.13.0, so the
  value comes from real distribution metadata, not `_FALLBACK_VERSION`.
- **Is M1 asserted on source or on behaviour?** On the SERVED `/openapi.json` document via
  the real app. Reading main.py would only prove a literal was edited; the defect is that
  what is served and what is declared disagree.
- **Could this be "fixed" in a way that recreates it?** Two arms refuse the two ways: no
  `version=` literal may remain in main.py (else a future drift gets papered over while the
  single source quietly dies), and comparison is PEP 440 tuple-wise (else the very
  string-ordering bug that hid four missing tags re-enters through its own guard).
- **Was the M3/M4 red observed, or skipped?** Observed. pyproject was set to 0.13.0 with the
  RELEASES.md row absent and the test failed with the intended message; the row then took it
  green. Both transitions were run rather than inferred from a single combined edit.
- **Did the version bump drag anything?** `uv.lock` moves one line (0.7.0 -> 0.13.0). No
  dependency drift; full `make ci` green including the dependency-allowlist gate.
- **Residual — M5 is NOT gated by a test, by design.** A shallow CI clone carries no tags,
  so a tag test would be green-because-absent. Mapping is derived from the existing
  convention rather than guessed, and tags are created locally only; pushing is
  outward-facing and permanent, so it is held for explicit authorization.

### GATE RECORD
Reported: yes
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-08-07

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned — guard first, then the single source, then the record, with tag creation held back as a separate step because it is the only part that touches published history.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
