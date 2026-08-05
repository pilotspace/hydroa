# PLAN: Clear the ruff/pyright/allowlist debt so make ci can go green

slug: lint-type-debt-sweep · created: 2026-07-25 · stage: production
milestone: release-integrity
autonomy: conservative
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `make ci` reaches exit 0 on `main` — clearing the ruff, pyright and dependency-allowlist debt that stands between a restored CI runner and a genuinely green required check.

Grounding — 44 findings, enumerated, and **they are NOT all cosmetic**. The milestone's framing ("#36 ruff-format, #56 pyright — format-only and type-only commits") is WRONG about four of them, and a careless `ruff --fix` sweep would do real damage:

**(a) Safe mechanical — 30 findings.** 17 ruff-autofixable (`I001` unsorted-imports ×6, `RUF100` unused-noqa ×6, `RUF022`/`RUF023` unsorted `__all__`/`__slots__` ×3, `F401` unused-import, `UP037` quoted-annotation), plus `E501` line-too-long ×13. No behavior. `F401`+`I001` in `domain_invite_link_use_cases.py` and pyright's unused `UTC` import there are the SAME defect seen twice.

**(b) FALSE POSITIVES that must be silenced with justification, never "fixed" — 4 findings.**
  · `S608` ×2 in `alerting/application/dispatcher.py:69,73` — flagged as SQL injection. It is not: `_UNDELIVERED_WHERE` is a module-level constant f-string interpolated into `text()`, and the only variable part is the BOUND parameter `:meta_key`. No caller input reaches it. `S608` ×1 in `tests/finetune_broker` is the same shape.
  · **`RUF001` in `tests/bedrock_region_guard/test_bedrock_guard_verify2_dispatch_gate.py:111` — the Cyrillic `е` (U+0435) is DELIBERATE.** It is the payload of `test_unicode_confusable_prefix_never_reaches_bedrock_adapter`, a homoglyph-attack regression test proving an EU-residency model id cannot slip past the catalog gate. `ruff --fix` does not touch RUF001, but a human or agent "cleaning up the ambiguous character" would silently gut a residency security test while leaving it green. This is the single most dangerous finding in the set.

**(c) Needs judgment — 3 findings.** `B904` raise-without-from in `responses_router.py:201` · `B018` useless-expression in `tests/image_edits_variations:539` (a useless expression IN A TEST may be a dropped assertion — read it before deleting) · `ASYNC109` async-def-with-`timeout`-param in `tests/responses_state_store:743`.

**(d) One pyright false positive in SECURITY code — 1 finding.** `mcp_connector/application/use_cases.py:496` "`candidate_body` is possibly unbound". Provably safe by hand: `candidate_body` is bound only under `if not block_relay:` (457–458), and line 496 is reachable only when `block_relay` is False, which is exactly that branch. Pyright cannot narrow it. But this is the PII-mask fail-closed relay path, carrying a `confirmed HARD-STOP, security` comment — the fix must be a narrowing/restructure that provably preserves behavior, NOT a blanket `# type: ignore`, and NOT a default value that could make an unbound case relay an empty body. The other two pyright errors (`_host_only` unused function, `UTC` unused import) are safe deletions.

**(e) Supply-chain allowlist — 4 dependencies.** `dnspython`, `pgvector`, `pytest-rerunfailures`, `pytest-xdist` are in `apps/gateway/pyproject.toml` but absent from `.add/dependencies.allowlist`; `pgvector` entered via #89. Tin's call (2026-07-25): allowlist all four WITH written justification. This is a LAPSED CONTROL being restored, not a formatting task.

Framings weighed: **triage into four classes and treat each on its own terms** (chosen — the set is heterogeneous; one blanket `ruff --fix` + `# type: ignore` pass would silence a real security test and mask a real narrowing question, which is how debt sweeps cause outages) · *pure mechanical autofix, defer the rest* (rejected — leaves `make ci` red, so the milestone's exit criterion and ci-restoration's M4 both stay blocked; the whole point is exit 0) · *fix the false positives by changing the CODE to satisfy the linter* (rejected — rewriting `dispatcher.py`'s bound-parameter SQL or removing the homoglyph would degrade working, deliberately-written code to appease a heuristic).

Must:
<must>
  - M1 `make lint` exits 0 — every ruff finding either genuinely fixed, or suppressed NARROWLY (a per-line `# noqa: <CODE>`, or a per-file-ignores entry following the repo's existing justified-S608 precedent) with a written reason in every case. No rule removed from `select`, none added to `ignore`, no file-level `# ruff: noqa`, no new `exclude` path.
  - M2 `make typecheck` exits 0 — pyright clean, with the `mcp_connector` narrowing done as a structural fix that preserves the fail-closed relay semantics exactly.
  - M3 `make allowlist` exits 0 — all four dependencies present in `.add/dependencies.allowlist`, each with a justification comment naming what it is for, in the style of the existing `python3-saml` / `reportlab` entries.
  - M4 the deliberate Cyrillic homoglyph in the Bedrock residency test SURVIVES the sweep, byte-for-byte, and is protected against a future well-meaning cleanup.
  - M5 no behavioral change anywhere: every edit is a suppression, a deletion of provably-dead code, a formatting change, or a narrowing that a reader can verify preserves semantics.
</must>
Reject:
<reject>
  - a green `make lint` bought by REMOVING a rule from `[tool.ruff.lint] select`, ADDING one to `ignore`, relaxing a pyright `report*` setting, or a file-level `# ruff: noqa` -> "gate_weakened"
  - an `[tool.ruff] exclude` addition that is NOT a test file, or that dodges a `ruff check` finding rather than a format-only one -> "gate_weakened"
  - a per-file-ignores entry with no written justification, or one that exempts a code the file has not been PROVEN to false-positive on -> "gate_weakened"

  - removing or altering the U+0435 homoglyph, or any other change that reduces a test's assertive power -> "test_weakened"
  - a `# type: ignore` or a default-value assignment on the `candidate_body` path that would let an unbound case relay a body -> "failopen_introduced"
  - a dependency allowlisted with no written justification -> "unjustified_dependency"
</reject>
After:
<after>
  - `make ci` exits 0 end-to-end, save for the `test` target's own separate instability (owned by `suite-stability`).
  - `[tool.ruff.lint] select`/`ignore`, every pyright `report*` setting, and `[tool.ruff] exclude` are UNCHANGED from their frozen values; any per-file-ignores addition carries a justification comment.
  - `test_unicode_confusable_prefix_never_reaches_bedrock_adapter` still contains U+0435 and still passes.
</after>
Boundary: two input shapes — (a) ruff/pyright findings as MACHINE OUTPUT (codes + paths), which the §4 tests consume via subprocess exit codes rather than by parsing prose; (b) `.add/dependencies.allowlist` as a line-oriented text manifest with `#` comments, read by `scripts/check_allowlist.py`.
<assumptions>
  ⚠ that the `mcp_connector` possibly-unbound really is unreachable-when-unbound. I traced it by hand (bound under `if not block_relay:`; the use site is reachable only when `block_relay` is False) but did NOT prove it with a test, and it sits in a path whose own comments record a prior confirmed security HARD-STOP. If wrong, a restructure could convert a would-be crash into a silent relay of an unmasked body — strictly worse than the current state. Mitigation: the fix must FAIL CLOSED by construction (raise, or a sentinel that blocks) and must be reviewed against the existing `test_pii_mask_pii_in_resource_uri_alongside_text_block_*` tests before it is written; if it cannot be made obviously fail-closed, leave the pyright error and gate RISK-ACCEPTED on M2 rather than guess.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape)

```
`make lint` · `make typecheck` · `make allowlist`  ->  exit 0                    (M1,M2,M3)

CLASS (a) mechanical — 30 findings
  ruff --fix for the 17 autofixable codes (I001, RUF100, RUF022, RUF023,
    F401, UP037) + manual wrapping for E501 x13
  pyright: delete unused `_host_only` (alerting/infrastructure/httpx_webhook_sink.py)
    and the unused `UTC` import (tenants/application/domain_invite_link_use_cases.py)

CLASS (b) justified suppressions — NARROW, per-line, each with a reason      (M1, M4)
  alerting/application/dispatcher.py         S608 x2  -> per-file-ignores + why,
    following the repo's OWN precedent: usage/api/router.py and
    guardrail_analytics/api/router.py already carry justified S608 entries for
    the identical "bound params / hardcoded literal — no injection risk" shape
  tests/finetune_broker/…                    S608 x1  -> same, or per-line noqa
  tests/bedrock_region_guard/…:111           RUF001   -> `# noqa: RUF001` + why
    the U+0435 homoglyph is the TEST PAYLOAD and MUST survive byte-for-byte

CLASS (c) judgment — 3 findings, each READ before it is touched
  responses_router.py:201   B904      -> add `from err` / `from None`, whichever
                                         preserves the current error surface
  tests/image_edits_variations:539  B018  -> a useless expression in a test may be
                                         a DROPPED ASSERTION; restore the assert if so,
                                         delete only if provably inert
  tests/responses_state_store:743  ASYNC109 -> `# noqa` or rename; no timeout semantics change

CLASS (d) security-adjacent narrowing — 1 finding                             (M2, R:failopen_introduced)
  mcp_connector/application/use_cases.py  `candidate_body` possibly-unbound
    MUST fail CLOSED by construction. NOT a `# type: ignore`. NOT a default
    value that could relay an empty/unmasked body.

CLASS (e) supply chain — .add/dependencies.allowlist                          (M3)
  + dnspython             # <justification>
  + pgvector              # <justification — entered via #89>
  + pytest-rerunfailures  # <justification>
  + pytest-xdist          # <justification>

FORBIDDEN (each is a §1 Reject):
  any edit to [tool.ruff] / [tool.pyright] / per-file-ignores in pyproject.toml
  any file-level `# ruff: noqa`
  any change to the U+0435 byte sequence
```

CR v2 (Tin, 2026-07-25) — `ruff format` debt admitted to the contract; the `exclude`
list MAY grow for frozen test files, under a narrow rule.
  FOUND AT BUILD, and invisible until now: `make lint` is `ruff check . && ruff format
  --check .`. The `&&` short-circuits, so while `ruff check` was failing on 37 findings
  the FORMAT half never ran even once. With check clean, format reports **93 files**
  (26 src · 63 tests · 4 migrations). Verified pre-existing at 96 before this build —
  this task's edits reduced it by 3, they did not cause it.
  Resolution: run `ruff format` on the 30 non-test files, and extend `[tool.ruff]
  exclude` to cover the 63 frozen TEST files — the exact purpose the existing 49
  entries already serve ("Frozen test files have pre-existing format issues; excluding
  them from format checks avoids violating the never-edit-test-files contract").
  Reformatting them would rewrite test files that other tasks' frozen §4 contracts say
  must not be edited.
  This NARROWS the original Reject rather than deleting it. A new `exclude` path is
  permitted ONLY when: (i) the file is a TEST file, (ii) the only finding against it is
  `ruff format`, never a `ruff check` rule, and (iii) it is added under the existing
  documented comment block. An `exclude` added to dodge a `ruff check` finding, or
  covering a `src/` file, remains R:gate_weakened.
  `test_ruff_exclude_gained_no_new_path` is re-pinned to the post-CR count and keeps
  asserting the list never grows AGAIN without another contract change.

Target (measurable): `make lint` 37 check-findings -> 0 AND `ruff format --check` 93 -> 0 · `make typecheck` 3 errors -> 0 · `make allowlist` 4 missing -> 0, so `make ci` fails only at `test` (owned by `suite-stability`). `git diff apps/gateway/pyproject.toml` shows no linter-config change. The U+0435 byte sequence is still present and its test still passes. Suppression count is bounded: at most 4 new `# noqa`, each on its own line with a reason.
Status: FROZEN @ v4 — approved by Tin Dang
Reported: no

### Build-strategy
Scope (may touch): `./../../../.add/dependencies.allowlist` · `apps/gateway/src/` · `apps/gateway/tests/` · `apps/gateway/migrations/`
Regression floor: the affected suites must stay green — `tests/bedrock_region_guard` (M4's proof), `tests/mcp_connector` incl. `test_pii_mask_pii_in_resource_uri_alongside_text_block_*` (class (d)'s proof), `tests/image_edits_variations`, `tests/responses_state_store`, `tests/responses_api_core`, `tests/finetune_broker`, `tests/alerts_events_viewer`. Run these SPECIFIC suites, not the full board — the full suite is known-unstable and is `suite-stability`'s charter, not a gate this task can pass.
Persona: staff-engineer-code-quality

Least-sure flag surfaced at freeze: [contract] — class (d), the `mcp_connector` narrowing. Everything else in this contract is mechanical or a documented suppression; that one line asks me to restructure control flow in a PII-mask relay path whose own comments record a prior confirmed security HARD-STOP, on the strength of a by-hand reachability trace I did not prove with a test. The contract therefore constrains the SHAPE of the fix (must fail closed by construction, no type-ignore, no default value) rather than the fix itself, and §1 assumptions state the fallback: if it cannot be made obviously fail-closed, leave the pyright error and gate RISK-ACCEPTED on M2 rather than guess.

---

## 4 · TESTS & SCENARIOS — failing-first suite (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_ruff_is_clean: act — run `ruff check .` over apps/gateway as a subprocess; assert — exit 0 and empty finding list. MUST-FAIL-FIRST: 37 errors today. covers: M1
  - test_pyright_is_clean: act — run `pyright --outputjson` as a subprocess; assert — zero errors. MUST-FAIL-FIRST: 3 errors today. covers: M2
  - test_every_dependency_is_allowlisted: arrange — parse dependency names from `apps/gateway/pyproject.toml`; act — parse `.add/dependencies.allowlist`; assert — no dependency is missing. MUST-FAIL-FIRST: dnspython, pgvector, pytest-rerunfailures, pytest-xdist missing. covers: M3, R:unjustified_dependency
  - test_every_allowlist_addition_carries_a_justification: arrange — read the four newly added allowlist lines; assert — each has a non-empty `#` comment, matching the existing python3-saml/reportlab convention. MUST-FAIL-FIRST: the lines do not exist yet. covers: M3, R:unjustified_dependency
  - test_linter_config_was_not_weakened: arrange — read `apps/gateway/pyproject.toml`; assert — (i) `[tool.ruff.lint] select` and `ignore` equal their frozen values, (ii) every `report*` key under `[tool.pyright]` equals its frozen value, (iii) `[tool.ruff] exclude` gained no new path, (iv) no file-level `# ruff: noqa` exists under apps/gateway, and (v) EVERY per-file-ignores entry carries a trailing `#` justification. Deliberately does NOT forbid new per-file-ignores entries — the repo's own convention uses them for proven false positives — but forces each to be justified in writing, which is the property that actually matters. Standing guard for R:gate_weakened. covers: M1, R:gate_weakened
  - test_bedrock_homoglyph_payload_survives: arrange — read `tests/bedrock_region_guard/test_bedrock_guard_verify2_dispatch_gate.py`; assert — the literal contains U+0435 (CYRILLIC SMALL LETTER IE) immediately followed by "u.anthropic". Guards the single most dangerous finding in the set: the homoglyph is the test's PAYLOAD, and a well-meaning "fix the ambiguous character" would gut a residency security regression test while leaving it green. covers: M4, R:test_weakened
  - test_mcp_candidate_body_path_fails_closed: arrange — drive the mcp_connector relay so the guardrail blocks (`block_relay` True); assert — the response is the ERR_MCP_TOOL_RESULT_BLOCKED error body and NO upstream body is relayed; and assert the success path still relays the masked body. Pins the class-(d) semantics BEFORE the narrowing so the restructure cannot silently convert a block into a relay. covers: M5, R:failopen_introduced
</test_plan>

Rigor: M1/M2/M3 are exit-code assertions on the real tools — no re-implementation of ruff's or pyright's judgment, which would drift. M4 and the config guard are STANDING tests: they are the ones that still matter in six months, because they fail on the two ways this debt gets "cleared" dishonestly (loosen the linter · delete the inconvenient test payload). The class-(d) test is characterization-first: it pins existing behavior before the risky edit, which is the only way a no-behavioral-change claim (M5) can be evidenced rather than asserted.

Tests live in: `apps/gateway/tests/repo_hygiene/` · `apps/gateway/tests/mcp_connector/test_relay_failclosed_characterization.py`

RED-BEFORE-BUILD STATUS (verified 2026-07-25, before any fix):
  RED (4) — test_ruff_is_clean (37 findings) · test_pyright_is_clean (3) ·
    test_allowlist_gate_passes (4 missing) · test_new_allowlist_entries_carry_a_justification
  GREEN BY DESIGN (5) — the standing guards and the characterization pair are NOT
    red-first and must not be made so. A guard that starts red is describing a
    defect; these describe an invariant that already holds and must survive the
    sweep: test_linter_config_was_not_weakened · test_ruff_exclude_gained_no_new_path ·
    test_bedrock_homoglyph_payload_survives · the two mcp_connector relay-arm tests.
    Their job is to go RED if the BUILD breaks them — which is exactly the risk here.

Two test bugs were caught and fixed while establishing red, both worth naming:
  · `test_linter_config_was_not_weakened` self-matched — it discusses `# ruff: noqa`
    in prose and its own substring search flagged it. Same class as ci-restoration's
    M3 predicate; fixed by matching the directive only at line start.
  · `test_ruff_exclude_gained_no_new_path` initially compared against a constant
    COMPUTED FROM THE LIVE FILE, so it could never fail. Pinned to the literal 49.
  Both were vacuous-green bugs in tests written to prevent vacuous greens.

FOUND while establishing red (pre-existing, in scope): `tests/routing_strategy/
test_routing_strategy.py:1` carries a file-level `# ruff: noqa: S311` — a blanket
suppression, though a code-scoped and justified one. The guard permits it (codes +
written reason) and forbids bare blanket suppressions.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: the four-class triage as planned, in dependency order — mechanical first (it is the bulk and carries no judgment), judgment calls next, the security narrowing last, behind its characterization tests.
  · (a) `ruff --fix` cleared 17; the remaining 13 E501 were wrapped by hand.
  · (c) B018 in `image_edits_variations` was NOT deleted — the bare `fake_provider.post_json_calls` with its comment "exists on the fake" is an assertion the author never wrote, so it became `assert hasattr(...)`. B904 took `from None` deliberately (the JSON parser's exception must not become a client-facing `__cause__`); the response body is unchanged.
  · (b) S608 in `dispatcher.py` followed the repo's OWN precedent — a justified per-file-ignores entry, matching the two that already exist for the identical bound-parameter shape — rather than inventing a per-line style. RUF001 and ASYNC109 took per-line `# noqa` with written reasons.
  · (d) the `candidate_body` narrowing used a `None` sentinel plus `if block_relay or candidate_body is None:`. Behavior-preserving because `McpDialResult.body` is a non-optional dict (default `{}`), so `None` can never collide with a legitimate upstream body; and fail-closed by construction, so a future edit that breaks the binding coupling BLOCKS rather than relays.
  · (e) all four dependencies allowlisted with justifications; the `pgvector` entry records IN THE FILE that it entered via #89 without passing the gate.
Deviation: CR v2 — see §3. `make lint` is `ruff check && ruff format --check`, and the `&&` had hidden 93 format findings behind the failing check for as long as the check was red.
Code lives in: `apps/gateway/src/` · `apps/gateway/tests/` · `apps/gateway/migrations/` · `apps/gateway/pyproject.toml` · `.add/dependencies.allowlist`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] §4 suite passes — `tests/repo_hygiene` **7/7**. The 4 exit-code gates went RED->GREEN; the 3 standing guards were green throughout and stayed green (their job is to catch the BUILD, not the debt).
- [x] §3 Regression floor GREEN — **320 tests**, every suite the contract named plus the suites covering the 26 reformatted source files:
      · `tests/mcp_connector` + `tests/bedrock_region_guard` + `tests/image_edits_variations` — 125 passed
      · `tests/responses_state_store` + `tests/responses_api_core` + `tests/finetune_broker` + `tests/alerts_events_viewer` — 76 passed
      · `tests/vector_store_files` + `tests/vector_store_core` + `tests/file_search_tool` + `tests/health_alerting` + `tests/migrations` — 112 passed
      · `tests/repo_hygiene` — 7 passed
- [x] the four `make ci` gates: `lint` 37 findings + 93 format -> **0** · `typecheck` 3 -> **0** · `allowlist` 4 missing -> **0** · `allowlist-node` was and stays clean
- [x] coverage did not decrease — the sweep adds tests (9 new) and deletes only one provably-dead duplicate function (`_host_only` in `httpx_webhook_sink.py`; the live copy is in `dispatcher.py`)
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Refute-read verdict — the earned-green check
**Independent adversarial refute — `add-advisor` a8d90ec02d2839b0c (sonnet), default position "refuted=true".** All five claims **NOT-REFUTED**:
  · C1 the `candidate_body` narrowing is behavior-preserving AND fail-closed — verified structurally, against the pinned characterization tests (2/2), and with pyright clean. No caller constructs `McpDialResult.body` as None despite Python not enforcing the annotation.
  · C2 all four suppressions are genuine false positives — **no mislabelled vulnerability**, which was the HIGH-severity thing being hunted. The `dispatcher.py` S608 in particular: `_UNDELIVERED_WHERE` really is a module-level constant and `:meta_key` really is a bound parameter.
  · C3 the B018/B904 fixes preserve behavior (the `hasattr` assertion noted as near-vacuous, but weakening nothing).
  · C4 the 63 exclusions are format-only test files — **actually RUN**, stripping the exclusion and re-checking each of the 63: 0 check findings, 63 format findings. Exactly the CR v2 rule, verified rather than asserted.
  · C5 the make gates exit 0; 193 of the 320 regression tests independently re-run green. The remaining ~112 were not re-run by the refuter — open, not refuted.

**One CONFIRMED finding against my own work (MEDIUM, not security):** the three `# noqa: S607` I added in `tests/repo_hygiene` carried NO written reason — violating this task's own M1 ("written reason in every case") and slipping past `test_linter_config_was_not_weakened`, which only ever checked per-file-ignores. FIXED: all three now carry reasons.
  NOT fixed, deliberately: the guard still does not require justification on per-line noqas generally. The codebase has **410 pre-existing bare noqas** against 143 justified — a blanket guard would either be disabled or would explode this task's scope. Recorded as todo #70 (ratchet: guard new/changed lines, grandfather the rest) rather than pretended away.

Verdict: EARNED.
By: agent a8d90ec02d2839b0c + self · adversarially checked: whether a real vulnerability was annotated as a false positive (no); whether the security narrowing changed a relay decision (no, and it is now fail-closed by construction); whether any exclusion hides a check finding (no, all 63 verified individually).

Environment note: the refuter found the shared `gw_lintsweep` test DB mid-migration and repaired it to get evidence — consistent with `[[shared-test-postgres-no-timeouts]]`, and `suite-stability`'s problem, not a defect in this change.

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-25

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose **triage into four classes and treat each on its own terms**; rejected *pure mechanical autofix, defer the rest* (rejected — leaves `make ci` red, so the milestone's exit criterion and ci-restoration's M4 both stay blocked; the whole point is exit 0) · *fix the false positives by changing the CODE to satisfy the linter* (rejected — rewriting `dispatcher.py`'s bound-parameter SQL or removing the homoglyph would degrade working, deliberately-written code to appease a heuristic).
- [human] freeze — froze §3 @ v4 (approved by Tin Dang)
- [AI] build — strategy used: the four-class triage as planned, in dependency order — mechanical first (it is the bulk and carries no judgment), judgment calls next, the security narrowing last, behind its characterization tests. · (a) `ruff --fix` cleared 17; the remaining 13 E501 were wrapped by hand. · (c) B018 in `image_edits_variations` was NOT deleted — the bare `fake_provider.post_json_calls` with its comment "exists on the fake" is an assertion the author never wrote, so it became `assert hasattr(...)`. B904 took `from None` deliberately (the JSON parser's exception must not become a client-facing `__cause__`); the response body is unchanged. · (b) S608 in `dispatcher.py` followed the repo's OWN precedent — a justified per-file-ignores entry, matching the two that already exist for the identical bound-parameter shape — rather than inventing a per-line style. RUF001 and ASYNC109 took per-line `# noqa` with written reasons. · (d) the `candidate_body` narrowing used a `None` sentinel plus `if block_relay or candidate_body is None:`. Behavior-preserving because `McpDialResult.body` is a non-optional dict (default `{}`), so `None` can never collide with a legitimate upstream body; and fail-closed by construction, so a future edit that breaks the binding coupling BLOCKS rather than relays. · (e) all four dependencies allowlisted with justifications; the `pgvector` entry records IN THE FILE that it entered via #89 without passing the gate.
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
