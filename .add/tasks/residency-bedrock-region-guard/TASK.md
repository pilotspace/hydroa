# TASK: Fail-closed Bedrock BYOK region guard for residency

slug: residency-bedrock-region-guard · created: 2026-07-13 · stage: production · sensitivity: security
milestone: residency-service-tiers
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: verify   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py:BedrockCompletionUpstream.complete/stream — the chat adapter; reads `aws = self._get_credentials()`, `endpoint = self._build_endpoint(aws)` (= `https://bedrock-runtime.{aws.region}.amazonaws.com`), then `model_id, _ = _openai_to_converse_request(payload)`. The model_id carries the AWS cross-region-inference-profile prefix (`us.`/`eu.`/`apac.`). aws.region is `BedrockCredential.region` — TENANT-SUPPLIED via PUT /admin/provider-keys/bedrock (body.region), independent of the catalog region tag residency-policy filters on.
  - apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py — the embeddings sibling with the identical `_build_endpoint(aws.region)` shape; same guard applies.
  - apps/gateway/src/gateway/proxy/domain/provider_credentials.py:BedrockCredential.region (str, tenant-set).
  - apps/gateway/src/gateway/proxy/infrastructure/vertex_upstream.py:_parse_vertex_model / _ID_PREFIX_TO_LOCATION — the SIBLING that already fails closed on a region/model mismatch; this task mirrors its "fixed internal map, never tenant input, fail-closed-before-dial" pattern for Bedrock.
  - apps/gateway/src/gateway/catalog/infrastructure/bedrock_seed.py — the 6 seed rows whose ids carry the `us.`/`eu.`/`apac.` prefixes (region us/eu/ap).
Context (working folder): apps/gateway/src/gateway/proxy/infrastructure — Bedrock adapters only; no migration, no new route, no schema.
Honors (patterns / conventions): fail-closed-before-any-IO (vertex_upstream, _get_credentials); provider-error mapping in the adapters' error catalog; SECURITY: secret_access_key never in a log/exception (existing invariant, untouched).
Anchors the contract cites: BedrockCompletionUpstream.complete, BedrockCompletionUpstream.stream, bedrock_embeddings' embed entry, BedrockCredential.region, the new _assert_region_consistent guard + _PROFILE_PREFIX_TO_GEO map, ERR_BEDROCK_REGION_MISMATCH.
Issues/Risks (→ feed §1): (1) The leak is PAYLOAD TRANSIT — an `eu.`-profile request dialed at bedrock-runtime.us-east-1 sends the prompt to a US endpoint even if AWS then rejects the invocation; the guard must fire BEFORE the httpx call, not rely on an AWS ValidationException. (2) A legacy/unprefixed Bedrock model id (no cross-region profile) must be UNAFFECTED — the guard only constrains ids carrying a recognized geo prefix. (3) Coarse geo match (us-*/eu-*/ap-*) is robust without enumerating every AWS region; `apac.` profile prefix maps to AWS `ap-` region prefix.
Related intent: residency-service-tiers MILESTONE goal (fail-closed EU pin). This closes the M10/EU-pin gap for Bedrock BYOK that residency-policy's security verify surfaced (todo #27). Vertex already avoided it structurally; Bedrock did not.
Ground SHA: ea2ac68

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Fail-closed Bedrock BYOK region guard
Framings weighed: coarse geo-prefix match — the credential's AWS region geo-prefix (us-/eu-/ap-) MUST match the model id's cross-region-profile prefix (us./eu./apac.), fail-closed-before-dial (chosen) · full AWS-region-set enumeration per profile (rejected: brittle, needs maintenance as AWS adds regions, no security gain over the geo-prefix) · rely on AWS ValidationException (rejected: the payload has already transited the wrong-region endpoint by then — the leak is the transit, not the inference)
Must:
<must>
  - M1: BEFORE any Bedrock HTTP dial (chat complete, chat stream, embeddings), if the resolved model id carries a recognized cross-region-profile prefix (us./eu./apac.), assert the credential's AWS region shares the corresponding geo prefix (us.->us-, eu.->eu-, apac.->ap-). On match, proceed unchanged.
  - M2: The guard fires from a single shared helper `_assert_region_consistent(model_id, aws_region)` (+ a fixed `_PROFILE_PREFIX_TO_GEO` map) reused by all three Bedrock entry points — one source of truth, cannot drift (mirrors residency.py's single-predicate discipline).
  - M3: A model id with NO recognized cross-region-profile prefix (legacy/direct Bedrock id) is UNAFFECTED — no constraint, byte-identical to today.
  - M4: The map is a FIXED internal constant — never tenant-supplied, never derived from a URL (mirrors vertex's _ID_PREFIX_TO_LOCATION).
</must>
Reject:
<reject>
  - R1: an `eu.`/`apac.` model id with a credential region whose geo prefix differs (e.g. eu. + us-east-1) -> "ERR_BEDROCK_REGION_MISMATCH" (HTTP 403, problem+json), raised BEFORE any httpx call — no dial, no payload transit, no usage record.
  - R2: an unrecognized geo in the credential region that cannot be classified (e.g. a malformed region string) while the model carries a geo-prefixed profile -> "ERR_BEDROCK_REGION_MISMATCH" (fail-closed, never fail-open on an unclassifiable region).
</reject>
After:
<after>
  - A1: no Bedrock request whose model is a region-pinned cross-region profile is ever dialed against a mismatched-geo endpoint; the prompt never transits the wrong region.
  - A2: existing correctly-configured Bedrock traffic (matching geo, or unprefixed ids) is unchanged — zero new latency on the happy path beyond one dict lookup + string prefix compare.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ AWS cross-region inference profile prefixes are exactly {us, eu, apac} and map to AWS region geo-prefixes {us-, eu-, ap-} respectively — lowest confidence because AWS could introduce a new profile geo (e.g. `sa.`/`ca.`) not in the map; if wrong: a NEW-geo profile id would be treated as unprefixed (M3) and skip the guard — a fail-OPEN gap for that new geo. Mitigation: the map is the single point to extend; add a test asserting the three known prefixes are covered, and a TODO to widen when the catalog seeds a new-geo Bedrock row.
  - [ ] The guard belongs in the adapter (post-credential, pre-dial) rather than at the governance layer — confirmed: only the adapter has both the resolved credential region AND the concrete model id together; residency-policy's governance tier filters the catalog tag but never sees the credential region.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: eu-profile with a us credential is refused before any dial   # R1
  Given a tenant BYOK Bedrock credential with region "us-east-1"
  And a request whose resolved model id is "eu.anthropic.claude-3-5-sonnet-20241022-v2:0"
  When BedrockCompletionUpstream.complete (or .stream, or embeddings) is invoked
  Then it raises ERR_BEDROCK_REGION_MISMATCH (HTTP 403) BEFORE any httpx call
  And no request is dialed, no prompt bytes leave the process, no usage record is written

Scenario: apac-profile with an ap credential proceeds unchanged   # M1
  Given a credential with region "ap-southeast-1"
  And a model id "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
  When the adapter is invoked
  Then the guard passes (apac. -> ap-) and the request dials normally
  And the response is byte-identical to pre-guard behavior

Scenario: us-profile with a us-west credential proceeds   # M1
  Given a credential region "us-west-2" and model id "us.anthropic.claude-3-5-haiku-20241022-v1:0"
  When invoked
  Then the guard passes (us. -> us-, any us-* region satisfies the us geo)

Scenario: an unprefixed legacy model id is unaffected   # M3
  Given a credential region "us-east-1" and a model id "anthropic.claude-3-haiku-20240307-v1:0" (no geo prefix)
  When invoked
  Then the guard imposes no constraint and the request dials exactly as today
  And no ERR_BEDROCK_REGION_MISMATCH is ever raised for an unprefixed id

Scenario: a malformed/unclassifiable credential region fails closed under a geo-prefixed model   # R2
  Given a credential region "" or "not-a-region" and a model id "eu.anthropic...-v2:0"
  When invoked
  Then it raises ERR_BEDROCK_REGION_MISMATCH before any dial (never fail-open on an unclassifiable region)

Scenario: the shared guard is the single source of truth for all three entry points   # M2
  Given the guard helper _assert_region_consistent
  When chat-complete, chat-stream, and embeddings each run
  Then all three call the SAME helper + _PROFILE_PREFIX_TO_GEO map (no duplicated predicate that could drift)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Guard (no HTTP route — an in-adapter pre-dial assertion):
  _PROFILE_PREFIX_TO_GEO: dict[str,str] = {"us": "us", "eu": "eu", "apac": "ap"}   # FIXED, internal
  _assert_region_consistent(model_id: str, aws_region: str) -> None
    - parse the leading "<prefix>." of model_id; if prefix not in _PROFILE_PREFIX_TO_GEO -> return (M3, unconstrained)
    - required_geo = _PROFILE_PREFIX_TO_GEO[prefix]
    - if aws_region does not start with required_geo + "-"  -> raise ERR_BEDROCK_REGION_MISMATCH (403)
  Called at the TOP of BedrockCompletionUpstream.complete, .stream, and bedrock_embeddings' embed,
  AFTER `aws = self._get_credentials()` and model_id resolution, BEFORE _build_endpoint / any httpx call.

Error: ERR_BEDROCK_REGION_MISMATCH -> HTTP 403 problem+json
  { "type": ".../errors/bedrock-region-mismatch", "title": "Bedrock credential region does not match the model's residency region", "status": 403, "code": "ERR_BEDROCK_REGION_MISMATCH" }
  (message names neither the secret nor the full credential; states the model's required geo vs the credential geo only)

Schema: none (no migration, no new column, no new route, no new setting).
```

Glossary deltas: Bedrock cross-region inference profile: an AWS model id prefixed us./eu./apac. that fans out server-side across that geo's AWS regions; the prefix is the residency signal this guard binds the credential's region to.

Least-sure flag surfaced at freeze: ⚠ [spec] the profile-prefix→geo map is assumed complete at {us, eu, apac}. If AWS ships a NEW-geo cross-region profile (e.g. `sa.`/`ca.`) and the catalog seeds it, an id with that unknown prefix falls through M3 (treated as unprefixed) and SKIPS the guard — a fail-OPEN gap for that new geo until the map is widened. Mitigation contracted: a test pins the three known prefixes + a TODO to extend the map whenever a new-geo Bedrock row is seeded. Accepted as the residual because the only Bedrock rows shipping in M2 are us./eu./apac. (bedrock_seed.py) — the gap cannot manifest until a new-geo row is added, which is itself the trigger to widen the map.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new code) — achieved: bedrock_upstream.py new lines 100%, bedrock_embeddings.py new lines 100%, error_catalog.py new entry 100% (scoped coverage run against the touched-file suites).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_R1_complete/stream/embeddings_refused_before_dial: eu.-profile + us-east-1 cred → ProblemError 403 ERR_BEDROCK_REGION_MISMATCH, zero httpx calls (hit-counter==0) on all three entry points · covers: R1
  - test_M1_apac_profile_with_ap_credential_proceeds / test_M1_us_profile_with_us_west_credential_proceeds / test_M1_stream_matching_geo_proceeds / test_M1_embeddings_matching_geo_proceeds: matching geo → dials normally, response byte-identical shape · covers: M1
  - test_M3_unprefixed_model_unaffected_on_complete/_regardless_of_region/_on_embeddings: no recognized prefix → unconstrained regardless of credential region, never raises · covers: M3
  - test_R2_malformed_region_fails_closed_on_complete/stream/embeddings: credential region "not-a-region" (empty string is rejected earlier by BedrockCredential's own validator, so the reachable malformed case is a non-empty unclassifiable string) under a geo-prefixed model → fails closed, zero httpx calls · covers: R2
  - test_M2_all_three_entry_points_share_the_same_helper: bedrock_embeddings' imported `_assert_region_consistent` is object-identical (`is`) to bedrock_upstream's — one source of truth, cannot drift · covers: M2
  - test_helper_*: pure unit tests of `_assert_region_consistent` + `_PROFILE_PREFIX_TO_GEO` (all three prefixes, unconstrained cases, malformed-region fail-closed, error message names no secret) · covers: M1-M4, R1, R2
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
Actual test path: `apps/gateway/tests/bedrock_region_guard/test_bedrock_region_guard.py` (26 tests). RED evidence: `ImportError: cannot import name '_assert_region_consistent' from 'gateway.proxy.infrastructure.bedrock_embeddings'` (collection error — right reason, no implementation existed). Committed alone as `c2a307d test(bedrock): add red suite for fail-closed BYOK region guard`.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py`, `apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py`, `apps/gateway/src/gateway/core/error_catalog.py`, `apps/gateway/tests/bedrock_region_guard/`
Strategy (ordered batches): 1. Ground in the real call sites (complete/stream/post_json) and the vertex_upstream `_parse_vertex_model`/`_ID_PREFIX_TO_LOCATION` precedent. 2. Write the full red suite (helper unit tests + all 3 entry points × all scenarios) and commit alone. 3. Add `BEDROCK_REGION_MISMATCH` to error_catalog.py (403, mirrors RESIDENCY_NO_ELIGIBLE_REGION's section). 4. Add `_PROFILE_PREFIX_TO_GEO` + `_assert_region_consistent` to bedrock_upstream.py (single source, raises the catalog spec directly — matches the established infrastructure-layer pattern of importing ErrorSpec constants and raising `.exc()`, NOT a custom domain exception, since a plain-Exception subclass like VertexRegionUnresolvedError is never caught by FastAPI's `@app.exception_handler(ProblemError)` and would 500 instead of 403). 5. Reorder `complete()`/`stream()`/`post_json()` so model_id resolution + the guard run strictly before `_build_endpoint`/any dial; for `stream()`, hoist model_id resolution out of the lazy `_gen()` closure into the synchronous part of the method (mirrors `VertexCompletionUpstream.stream`'s fail-closed-before-return shape) so the mismatch is provable via a bare `pytest.raises` without iterating the generator. 6. Import the shared helper into bedrock_embeddings.py (same pattern as the existing `_bedrock_error_to_openai` cross-import).

Persona (required): appsec-engineer (.add/personas/appsec-engineer.md) — security-boundary lens for a fail-closed, before-any-IO privilege/residency guard.
Spawn isolation (default): worktree (already the dispatched worktree `wt/build-bedrock-region-guard`).
Known-problem fixes: (1) a plain-Exception domain error (VertexRegionUnresolvedError's own pattern) is never caught by the FastAPI ProblemError handler → fixed by raising the ErrorSpec's `.exc()` (a real ProblemError) directly from the guard, not a custom exception class. (2) BedrockCompletionUpstream.stream's model_id resolution originally lived inside the lazy `async def _gen()` closure, which would defer the guard until first iteration → fixed by hoisting model_id resolution + the guard call to the synchronous part of `stream()`, matching vertex's already-established pattern exactly (confirmed via vertex's own `pytest.raises(...)` around a bare, un-iterated `.stream(...)` call in its test suite).
Strategy actually used: as planned (see ordered batches above — no material deviation).
Safety rule (feature-specific): the guard call is a single, unconditional, synchronous statement placed between model_id resolution and `_build_endpoint`/the first dial in all 3 entry points — no early return, no conditional bypass, no path reaches `_build_endpoint` before the guard has run.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/`, `apps/gateway/src/gateway/core/error_catalog.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear. Honored — zero new dependencies; only stdlib + existing `gateway.core.error_catalog`/`gateway.core.errors` imports (already an established infrastructure-layer import pattern, e.g. vertex_upstream.py, azure_upstream.py, azure_embeddings.py).

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
