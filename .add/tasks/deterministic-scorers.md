---
type: Task
title: deterministic-scorers
status: done
milestone: evals-regression-gate
needs:
  - eval-set-store.md
gives:
  - S1 the scorer port + the four deterministic scorers (exact · contains · regex · JSON-schema valid) — no LLM judge
generated: { by: add/3.2.0, at: 2026-08-12 }
verified:
  - { by: "Tin Dang", at: 2026-08-13, act: freeze, authority: process, direction: "sha256:7986be6c142caa1e" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:62c340d35c23e6df" }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/deterministic-scorers.d/runs/1.md }
  - { by: "Tin Dang", at: 2026-08-13, act: refreeze, authority: process, direction: "sha256:82510f967e2137ad" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:197a643b28a51b73" }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/deterministic-scorers.d/runs/2.md }
  - { by: "Tin Dang", at: 2026-08-13, act: gate, authority: process, outcome: PASS, receipt: /tasks/deterministic-scorers.d/runs/2.md, brief: "sha256:197a643b28a51b73", reason: "Self-driving verify per the frozen contract (refrozen sha256:82510f96 after binding the A1 purity + A5 single-kind probes). 9 tests green binding M1-M6, A1-A6, and all three rejects: determinism, fail-closed on unsupported-kind + malformed-expected, bounded regex (guardrail nested-quantifier/backref heuristic) + JSON, empty-output, payload-free detail, purity (no session/app/network), single-kind result. pyright/ruff clean; grep-verified PURE (no sqlalchemy/fastapi/httpx/redis). Human four-eyes owed at the PR gate." }
advised_by: backend-architect
---
## CARD
goal: score a case deterministically — exact · contains · regex · JSON-schema valid; one scorer port, no LLM judge
why: a regression gate whose own verdict flaps is not a gate; scoring must be identical on a re-run of the same case + response
beat: done · next: add status

## RULES
<must>
- M1 A scorer is a PURE total function `score(assertion, output_text) -> ScoreResult` — no IO, no clock, no randomness, no network, no DB. Identical (assertion, output_text) ALWAYS yields an identical ScoreResult, in the same process or a re-run, on any host/locale. (A gate whose verdict flaps is not a gate.)
- M2 Exactly four scorer kinds ship — `exact` · `contains` · `regex` · `json_schema` — dispatched by `assertion.kind` through a `typing.Protocol` port (`Scorer`) with a zero-network fake usable from a test; no use-case reaches a concrete scorer directly (backend-architect lens). The kind set is a compile-time exhaustive `Literal` alias, never a DB enum.
- M3 `ScoreResult` is `{ passed: bool, kind: str, detail: str | None }` — a boolean verdict plus a human-readable `detail` on a fail (never the raw output echoed back verbatim; a short reason). This is the shape [[eval-run-executor]] persists per case and [[baseline-and-verdict]] aggregates (pass-count / total).
- M4 An `assertion.kind` NOT in the four supported kinds is UNSCOREABLE: `score` returns `ScoreResult(passed=False, kind=<given>, detail="unsupported scorer kind")` — never a crash, never a silent `passed=True`. (eval-set-store A2 deliberately stores any well-formed assertion without kind-validation; the scorer is where an unscoreable kind surfaces, as a FAIL the operator can see.)
- M5 A malformed `expected` for its kind (a non-string `contains` needle, an un-compilable `regex`, an `expected` that is not a valid JSON Schema) is UNSCOREABLE the same way — `passed=False` with a `detail` naming the defect; the scorer never raises out to its caller. (A stored case can carry a bad `expected`; a run must not die on one poisoned case.)
- M6 `regex` and `json_schema` scoring is bounded — a pathological pattern or a deeply-nested schema/JSON cannot hang or blow the stack: the `output_text` is length-capped before matching and matching runs under a bounded step/`recursion` budget, so a single case cannot pin CPU during a run's scoring pass. (appsec/reliability lens: the pattern is tenant-authored; a runaway is at worst a self-DoS but MUST be contained.) A pattern that exceeds the budget is UNSCOREABLE (`passed=False`, `detail` names the budget), not a hang.
</must>
<reject>
- R:UNSCOREABLE_CRASH a scorer raises out to its caller on ANY (kind, expected, output_text) — malformed, unsupported, empty, or adversarial -> "the scorer must total-map every input to a ScoreResult, never an exception"
- R:NONDETERMINISM the same (assertion, output_text) yields two different ScoreResults across calls/runs -> "R:NONDETERMINISM"
- R:SILENT_PASS an unscoreable or errored case scores `passed=True` -> "an unscoreable case must FAIL closed, never pass"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say who owns scoring or whose data it sees; taking "a scorer is a pure library function with NO tenant identity and NO persistence — it receives an assertion + an output string already in hand and returns a verdict; tenant scoping and payload-at-rest are the caller's concern ([[eval-run-executor]]), not the scorer's" -> if wrong, scoring couples to the DB/tenant and loses its purity + re-scorability. · probe: the four scorers are driven in a unit test with NO session, NO app, NO network — pure inputs to a ScoreResult.
- A2 [which] covers: S1 · the request does not say what part of the model response is scored; taking "the scorer scores the ASSISTANT MESSAGE TEXT — the `output_text` string the executor extracts from the completion (choices[0].message.content or the responses-API output text) — NOT the whole JSON envelope; the executor owns extraction and hands a string" -> if wrong, every scorer would re-parse provider-specific envelopes and drift per provider. · probe: `score` takes a `str` output, not a response dict; extraction lives upstream.
- A3 [when] covers: S1 · the request does not say how `exact`/`contains` treat surrounding whitespace/case; taking "byte-level and case-SENSITIVE: `exact` is `output_text == expected` with NO trim/normalize; `contains` is a case-sensitive substring test; determinism and locale-independence beat convenience, and a case author who wants leniency uses `regex`" -> if wrong, two hosts with different default locales could score a case differently. · probe: `exact` fails on a trailing-newline/case difference; `contains` fails on a case difference; documented, not silently lenient.
- A4 [absent] covers: S1 · the request does not say what an empty or absent `output_text` means (a model returned nothing / a timed-out case); taking "empty output is a leg_al input and scores by the same rule — `exact ''` passes only if expected is `''`; `contains`/`regex` against non-empty expected FAIL; the scorer never treats empty as an error" -> if wrong, a timed-out case crashes scoring instead of failing. · probe: `score` on `output_text=''` returns a ScoreResult (fail for a non-empty expected), never raises.
- A5 [order] covers: S1 · the request does not say how a multi-condition assertion is ordered; taking "an assertion carries EXACTLY ONE kind + one expected in R7 — no AND/OR composition, no ordered rule list; a set needing two checks uses two cases" -> if wrong, the scorer would need a composition grammar the store never froze. · probe: the ScoreResult reflects a single kind; there is no combinator field.
- A6 [experience] covers: S1 · the request does not say who reads a fail; taking "the operator reading the console per-case drill-down reads `detail` — so `detail` names WHY it failed (e.g. 'expected substring not found', 'regex did not match', 'JSON did not validate at the failing schema path'), actionable and payload-free, never a bare False" -> if wrong, a failing case is an unactionable red dot. · probe: a fail's `detail` is a non-empty human-readable reason; a pass's `detail` is null.

## PLAN
contract:
```
# new module `gateway/evals/scoring/` — pure, no IO, no tenant identity.
type ScorerKind = Literal["exact", "contains", "regex", "json_schema"]   # compile-time exhaustive

@dataclass(frozen=True)
class ScoreResult:
    passed: bool
    kind: str            # the assertion.kind as given (may be an unsupported string, for M4)
    detail: str | None   # human-readable reason on fail/unscoreable; None on pass

class Scorer(Protocol):                         # the port
    def score(self, *, assertion: Mapping[str, object], output_text: str) -> ScoreResult: ...

# The composed scorer dispatches on assertion["kind"]:
#   exact        -> output_text == expected                    (str; case+whitespace sensitive, A3)
#   contains     -> expected in output_text                    (str needle)
#   regex        -> re.search(expected, output_text) is not None  (bounded, M6; length-capped input)
#   json_schema  -> json.loads(output_text) validates against `expected` as a JSON Schema (bounded, M6)
#   <other>      -> ScoreResult(False, kind, "unsupported scorer kind")            (M4)
# Any malformed `expected` for its kind -> ScoreResult(False, kind, "<why>")       (M5)
# NEVER raises (R:UNSCOREABLE_CRASH); unscoreable/errored => passed=False (R:SILENT_PASS).
```
scope (may touch): `apps/gateway/src/gateway/evals/scoring/` (NEW: `__init__.py` · `ports.py` [Scorer Protocol + ScoreResult] · `scorers.py` [the four + the composed dispatcher + the unsupported/malformed fall-through]) · `apps/gateway/tests/evals/test_deterministic_scorers.py`. NO migration, NO router, NO error_catalog entry (pure library; the executor surfaces results). NO change to `retention_policy.py` or any payload store.
regression floor: `make ci` stays green; the module imports nothing from `sqlalchemy`/`fastapi`/`httpx` (grep-verified inward-only, backend-architect).
resolved (was least-sure): `json_schema` uses `jsonschema` (already `>=4.23,<5` in pyproject AND on `.add/dependencies.allowlist` — no new dep). Validate with `jsonschema.Draft202012Validator(expected).is_valid(parsed)`; a non-schema `expected` is caught by `check_schema` -> UNSCOREABLE (M5). Bound (M6) by length-capping `output_text` before `json.loads` and rejecting an `expected` schema past a max node count.

## EDGES
- E1 An `assertion.kind` outside the four supported kinds (a case stored under eval-set-store A2) -> `ScoreResult(passed=False, detail="unsupported scorer kind")`, never a crash (M4).
- E2 A `regex` whose `expected` is an un-compilable pattern, and one that is a catastrophic-backtracking pattern against a long output -> both UNSCOREABLE (`passed=False`) within the bound, never a hang or a raise (M5/M6).
- E3 A `json_schema` case where `output_text` is not valid JSON, and one where `expected` is not a valid schema -> both `passed=False` with a naming `detail`, never a raise (M5).
- E4 The SAME (assertion, output_text) scored twice in one process and across a simulated re-run yields byte-identical ScoreResults for all four kinds (M1, R:NONDETERMINISM).
- E5 `output_text=""` (a model returned nothing / a case that timed out upstream) -> a ScoreResult by the normal rule (fail for a non-empty expected), never an error (A4).

## CHECKS
- test_four_scorers_pass_and_fail_their_own_case · covers: M2, A2, A3 · each of exact/contains/regex/json_schema is driven with a hand-built (assertion, output_text) it MUST pass and one it MUST fail — pure inputs, no session/app/network (proves the port + the four kinds + case/whitespace sensitivity).
- test_scoring_is_deterministic_across_calls · covers: M1, E4, R:NONDETERMINISM · the same (assertion, output_text) scored twice for every kind returns byte-identical ScoreResults (the gate's core invariant).
- test_unsupported_kind_fails_closed_not_crash · covers: M4, E1, R:SILENT_PASS, R:UNSCOREABLE_CRASH · an assertion whose kind is "totally-unknown" returns ScoreResult(passed=False, detail names it), never raises, never passes.
- test_malformed_expected_fails_closed · covers: M5, E3, R:UNSCOREABLE_CRASH · an un-compilable regex, a non-string contains needle, and a non-schema `expected` each return passed=False with a detail, never raise.
- test_regex_and_json_scoring_is_bounded · covers: M6, E2 · a catastrophic-backtracking regex against a long output, and a deeply nested JSON/schema, each resolve to a ScoreResult within the bound (asserted by completing well under a wall-clock ceiling / step budget), never hang.
- test_empty_output_scores_not_errors · covers: A4, E5 · score on output_text="" returns a ScoreResult (fail for a non-empty expected, pass for exact "") for every kind, never an error.
- test_fail_detail_is_actionable_and_payload_free · covers: A6, M3 · a failing case's detail is a non-empty human-readable reason that does NOT echo the full output verbatim; a passing case's detail is None.
- test_scorers_are_pure_no_io · covers: A1 · every kind is driven from a SYNC test taking no session/app/network fixture — if scoring needed IO it could not run here; purity + re-scorability proven by construction.
- test_score_result_is_single_kind_no_combinator · covers: A5 · ScoreResult's fields are exactly {passed, kind, detail} (no combinator), and a single-kind assertion yields a single-kind result — R7 has no AND/OR composition.
red-first: every check MUST fail first (the `evals/scoring/` module does not exist yet; the two probe checks added at the process-authority refreeze were red against the pre-build tree too).

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
