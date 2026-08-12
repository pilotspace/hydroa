# TASK: Artifact upload content-type allow-policy guard

slug: artifact-upload-validation · created: 2026-06-30 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/artifacts/api/router.py` — `create_artifact(body: CreateArtifactRequest, …)` (POST `/v1/artifacts`, L202). CURRENT order: base64 decode (→422 `PAYLOAD_INVALID_BASE64`, L216) → size cap (`settings.artifact_max_bytes`, →413, L222) → object-store put / inline BYTEA → `session.commit()`. INSERTION POINT: a content-type allow-policy check at the TOP of the handler — BEFORE base64 decode (cheapest-first; a disallowed type need never decode bytes) and therefore before size/store/commit. `CreateArtifactRequest{name, content_type, content_base64}` (L153) — `content_type` is a FREE, UNVALIDATED string today: stored, sent to `store.put(key, decoded, content_type)`, and echoed as the download Content-Type. This is the only model-input gap left (size + base64 already guarded).
- `apps/gateway/src/gateway/core/config.py` — `Settings` (env_prefix `GATEWAY_`). Existing `artifact_max_bytes` (L556, the size half — DONE, do NOT touch). ADD the content-type half: `artifact_allowed_content_types` as a CSV `str` default `""` (NOT list[str] — pydantic-settings parses complex envs as JSON, a known gotcha; CSV-string + a parse helper sidesteps it) ⇒ env `GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES`. Empty = allow-all (byte-identical default).
- `apps/gateway/src/gateway/core/error_catalog.py` — `ErrorSpec(status, code, title_template)` + `.exc(detail=…, **fmt)`. Artifact errors section at L484 (`PAYLOAD_INVALID_BASE64`, `OBJECT_STORE_UNAVAILABLE`). ADD `ARTIFACT_CONTENT_TYPE_NOT_ALLOWED = ErrorSpec(415, "ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED", "content_type '{content_type}' is not allowed")`. Rendered application/problem+json by `core/errors.py:on_problem`.
- existing tests: `apps/gateway/tests/artifacts/test_artifacts.py` — self-contained signup→key fixtures + `client.post("/v1/artifacts", json={name, content_type, content_base64}, headers=_bearer(key))`; the happy-path 201 to mirror is `test_upload_download_roundtrip` (L98). No conftest in `tests/artifacts/` — fixtures come from the top-level `tests/conftest.py`.
Context (working folder): `.add/milestones/v55/MILESTONE.md` (task 4 of 4; depends-on: none — independent of `input_modalities`/the model guard; shares only the milestone's fail-closed/default-OFF posture). Tin 2026-06-30 decisions (AskUserQuestion): opt-in allow-list, default `""` = allow-all (byte-identical); reject status = 415 Unsupported Media Type. This is a DISTINCT, NON-model-capability guard — artifacts are blob storage, not model inference. Upload is base64-in-JSON, NOT multipart.
Honors (patterns / conventions): CLAUDE.md design-for-failure — the guard adds ZERO new IO (pure in-memory CSV-config + string compare on already-parsed request fields); the existing storage IO (object-store put → 503, db commit) is unchanged and keeps its handling. DEFAULT-OFF knob so an empty policy never 4xx's real traffic. PROJECT.md — errors via the `ErrorSpec` catalog only (no ad-hoc JSONResponse); never store/persist a refused upload (reject before decode + before put + before commit). Match-normalize on BOTH sides (media-type only, lowercased, params/whitespace stripped) so `image/PNG; charset=utf-8` matches an allow-list `image/png`.
Anchors the contract cites: `create_artifact` (POST `/v1/artifacts`) · `CreateArtifactRequest.content_type` · `Settings.artifact_allowed_content_types` (+ parse/normalize helper) · `ARTIFACT_CONTENT_TYPE_NOT_ALLOWED` ErrorSpec · `GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES` · existing `artifact_max_bytes` (the untouched size half).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Artifact upload content-type allow-policy — reject an upload whose content_type is not in a configured allow-list, before decode/storage (default-off, byte-identical when unset)
Framings weighed: a guard at the top of the existing `create_artifact` handler fed by a CSV config knob + a pure normalize/match helper (chosen — smallest seam, no new IO, no DB) · a Pydantic `field_validator` on `CreateArtifactRequest.content_type` (rejected: the validator can't read app Settings, so the allow-list can't be config-driven, and it would 422 not 415) · middleware inspecting the body (rejected: re-parses the JSON, can't see the resolved Settings cleanly, duplicates auth ordering)
Must:
<must>
  - Behind `GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES` (CSV; default `""`). EMPTY ⇒ allow ANY content_type — byte-identical to today (no rejection, every existing artifacts test unchanged).
  - When the allow-list is NON-empty, the upload's content_type must match a list entry under normalization (media-type only, lowercased, surrounding whitespace and `;`-parameters stripped on BOTH sides) — e.g. `IMAGE/PNG; charset=utf-8` matches `image/png`.
  - The check runs in `create_artifact` AFTER base64 decode (422) and the size cap (413), but BEFORE any object-store put or DB write/commit. A refused upload is NEVER stored or persisted. (Tin 2026-06-30: 422-first — invalid base64 and oversize both win over a disallowed type.)
  - The size half (`artifact_max_bytes` → 413) and base64 validation (→422) are unchanged and keep firing for allowed uploads.
</must>
Reject:
<reject>
  - allow-list non-empty AND normalize(content_type) ∉ normalized allow-list -> "ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED" (415); detail names the offending content_type
</reject>
After:
<after>
  - An allowed (or any, when the list is empty) upload still returns 201 with the same body and is stored exactly as before.
  - A rejected upload leaves NO new artifact row and triggers NO object-store put (verifiable: list count unchanged; store spy not called).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ none material — precedence RESOLVED by Tin 2026-06-30 (422-first): decode + size run first, so a both-invalid request returns 422 (then 413); the 415 fires only once the payload is well-formed and within size. Biggest residual risk: content_type normalization edge cases (trailing space, `image/png;charset` with no space, an empty content_type) — pinned by the normalize unit test.
  - [x] Upload is base64-in-JSON, not multipart — confirmed by reading `CreateArtifactRequest` + `create_artifact`.
  - [x] Size cap + base64 already guarded — confirmed (artifact_max_bytes L222, PAYLOAD_INVALID_BASE64 L216); this task is content-type ONLY.
  - [x] Download is always `Content-Disposition: attachment` — stored-XSS already mitigated, so an allow-list is a policy control, not the XSS fix.
  - [x] No DB/migration change — content_type column already exists; this is config + handler only.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: allow-list empty (default) accepts any content_type
  Given GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES is "" (default)
  When I POST /v1/artifacts with content_type "application/x-anything" and valid base64
  Then the response is 201 with the artifact metadata
  And the artifact is stored (byte-identical to today)

Scenario: allowed content_type passes when the list is configured
  Given GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES is "image/png,image/jpeg,application/pdf"
  When I POST /v1/artifacts with content_type "image/png" and valid base64
  Then the response is 201
  And the artifact is stored

Scenario: disallowed content_type is rejected before storage
  Given GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES is "image/png,image/jpeg"
  When I POST /v1/artifacts with content_type "text/html" and valid base64
  Then the response is 415 with code "ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED"
  And the detail names "text/html"
  And NO new artifact row exists (the tenant's list count is unchanged)
  And the object store put was never called

Scenario: content_type match is normalized (case + parameters)
  Given GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES is "image/png"
  When I POST /v1/artifacts with content_type "IMAGE/PNG; charset=utf-8" and valid base64
  Then the response is 201
  And the artifact is stored

Scenario: base64 decode precedes the content-type check (422 wins)
  Given GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES is "image/png"
  When I POST /v1/artifacts with content_type "text/html" and INVALID base64
  Then the response is 422 with code "ERR_PAYLOAD_INVALID_BASE64" (decode runs first; the 415 is never reached)
  And NO new artifact row exists
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /v1/artifacts   body: { name, content_type, content_base64 }   (UNCHANGED shape)
  201 -> { id, name, content_type, size_bytes, created_at }          (UNCHANGED)
  415 -> { type, title, status:415, code:"ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED", detail }
         WHEN  settings.artifact_allowed_content_types is non-empty
           AND normalize(content_type) ∉ { normalize(t) for t in allow-list }
         detail names the offending content_type (no allow-list secret — config, safe to echo)
  (existing, unchanged) 401 auth · 422 ERR_PAYLOAD_INVALID_BASE64 · 413 size cap · 503 store unavailable

Order in create_artifact (FROZEN): 1. authenticate  2. base64 decode (422)  3. size cap (413)
  4. content-type allow-policy (NEW — 415)  5. store.put / inline  6. commit
  → the existing decode+size pair is UNCHANGED; the NEW 415 is step 4 — after a well-formed,
    within-size payload, still before any storage or commit (a refused upload is never stored).
  → precedence (Tin 2026-06-30, 422-first): invalid base64 → 422 and oversize → 413 BOTH win over a disallowed type.

normalize(s) = s.split(";",1)[0].strip().lower()      # media-type only, case- and param-insensitive
allow-list parse = [normalize(t) for t in csv.split(",") if t.strip()]   # "" → empty → allow-all

Config: GATEWAY_ARTIFACT_ALLOWED_CONTENT_TYPES  (CSV str, default "")   # NOT list[str] (env-JSON gotcha)
Error:  ARTIFACT_CONTENT_TYPE_NOT_ALLOWED = ErrorSpec(415, "ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED",
          "content_type '{content_type}' is not allowed")
Schema: NONE — no table/column/migration change. content_type column already exists.
Untouched: FallbackModelRouter, artifact_max_bytes (size half), base64 path, GET/DELETE/list, the v55 model guard.
```

Least-sure flag surfaced at freeze: [spec] check ordering — decided 422-first (decode+size win over a disallowed type); Tin chose this over the fail-fast 415-first variant. Residual: [test] content_type normalization edge cases (trailing space, no-space params, empty content_type) — pinned by the normalize unit test.

Status: FROZEN @ v1 — approved by Tin 2026-06-30 (422-first ordering)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the new helper + handler branch
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_empty_allowlist_accepts_any: settings allow-list "" / POST content_type "application/x-anything" + valid b64 / assert 201 + row stored
  - test_allowed_type_passes: allow-list "image/png,image/jpeg" / POST "image/png" / assert 201 + stored
  - test_disallowed_type_rejected_415: allow-list "image/png" / POST "text/html" / assert 415 + code ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED + detail has "text/html" + list count unchanged + object-store spy.put not called
  - test_normalized_match_case_and_params: allow-list "image/png" / POST "IMAGE/PNG; charset=utf-8" / assert 201 + stored
  - test_base64_precedes_contenttype: allow-list "image/png" / POST "text/html" + INVALID base64 / assert 422 ERR_PAYLOAD_INVALID_BASE64 (not 415) + list count unchanged
  - unit test_normalize: helper lowercases, strips params + whitespace; parse drops blanks, "" → empty (allow-all)
</test_plan>

Tests live in: `tests/artifacts/test_artifact_content_type.py` · MUST run red (missing config field / ErrorSpec / guard) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/artifacts/api/router.py` `apps/gateway/src/gateway/artifacts/content_type_policy.py` `apps/gateway/tests/artifacts/test_artifact_content_type.py` — the config knob, the new ErrorSpec, the guard in create_artifact, the pure normalize/match helper module (`content_type_policy.py`), and the new red test file. No migration, no DB, no FallbackModelRouter, no change to artifact_max_bytes / base64 / GET / DELETE.
Strategy (ordered batches): 1. config: `artifact_allowed_content_types: str = ""` + a parse/normalize helper · 2. error_catalog: `ARTIFACT_CONTENT_TYPE_NOT_ALLOWED` (415) · 3. guard: AFTER the decode+size block in create_artifact, if parsed allow-list non-empty and normalize(content_type) ∉ it → raise the 415 (before store.put/commit) · 4. red→green.
Known-problem fixes: do NOT use `list[str]` for the env field (pydantic-settings parses complex types as JSON → a CSV env raises) — use a CSV `str` + a parse helper. Place the guard AFTER the existing decode+size block (precedence frozen in §3: 422 → 413 → 415) — keep that pair byte-identical, insert the 415 immediately before store.put/commit. Normalize on BOTH sides (strip `;`-params + lowercase) so case/charset variants match. Keep the empty-list path a pure no-op (byte-identical) — do not even build the set when the CSV is "". Reject BEFORE store.put/commit so no orphan object or row.
Strategy actually used: as planned (delegated to backend-expert subagent). The pure helpers landed in a dedicated `apps/gateway/src/gateway/artifacts/content_type_policy.py` (3 functions: normalize/parse/is-allowed) rather than inline in the router — cleaner + unit-testable. Guard inserted at router.py L227 (after the size-cap raise, before `artifact_id`/store/commit), exactly the frozen 422→413→415 order. Subagent self-caught one E501 (moved the env-var name into the block comment).
Safety rule (feature-specific): refuse before any side-effect — the 415 is raised before object-store put and before db commit (after decode+size, which already reject pre-storage); the guard adds no new IO (pure config + string compare), so no timeout/retry/breaker is needed.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 26 passed, 2 skipped (s3-live) in ONE pytest process (new suite + existing artifacts regression), re-run first-hand by the orchestrator
- [x] coverage did not decrease — existing artifacts tests byte-identical with the default "" allow-list; new module + branch covered by 6 tests
- [x] no test or contract was altered during build — only new module + additive guard/config/error; frozen test file untouched; FROZEN §3 unchanged
- [x] the green was EARNED — refute-read below; reject test asserts list-count unchanged AND spy.put_calls==0; 422-first ordering + empty-list parity proven
- [x] concurrency / timing safe — guard is a synchronous pure-CPU check (string normalize + frozenset membership); ZERO new IO, no locks/tasks; existing store.put/commit unchanged
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; detail echoes only caller-supplied content_type + config allow-list (no secret); problem+json via ErrorSpec
- [x] layering & dependencies follow CONVENTIONS.md — pure helper module in artifacts domain, ErrorSpec in core catalog, flag in core/config; router imports the helper; no cross-layer leak
- [ ] a person reviewed and approved the change — Tin (orchestrator auto-PASS under autonomy:auto; surfaced for spot-audit)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] default "" allow-list ⇒ an unusual content_type still uploads 201 (byte-identical) — confirmed: test_empty_allowlist_accepts_any + 12 existing artifacts tests green
- [x] configured allow-list ⇒ a disallowed type returns 415 ERR_ARTIFACT_CONTENT_TYPE_NOT_ALLOWED with NO new row and NO object-store put — confirmed: test_disallowed_type_rejected_415 (list count unchanged, spy.put_calls==0)
- [x] invalid base64 + disallowed type ⇒ 422 (decode wins), not 415 — confirmed: test_base64_precedes_contenttype; matches the FROZEN order (decode→size→content-type)
- [x] normalized match: "IMAGE/PNG; charset=utf-8" ≡ allow-list "image/png" → 201 — confirmed: test_normalized_match_case_and_params + unit test_content_type_policy_helper
- [x] guard sits AFTER decode+size, BEFORE store/commit — confirmed: read router.py diff (insert at L227, after PAYLOAD_INPUT_TOO_LONG, before artifact_id/store)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `is_content_type_allowed` imported into router and called in create_artifact; `ARTIFACT_CONTENT_TYPE_NOT_ALLOWED` raised there; `artifact_allowed_content_types` read via settings. All read first-hand in the diff.
- [x] DEAD-CODE (code) — all 3 helper functions used (router + unit test); no orphan symbol; `parse_allowed_content_types` used by `is_content_type_allowed` and the unit test.
- [x] SEMANTIC — read content_type_policy.py + the router/config/error_catalog diffs + the full test file: logic matches frozen §3 (normalize both sides, empty=allow-all early return, 415, 422-first, no storage on reject).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (orchestrator, post-subagent independent review) · adversarially checked: (1) the reject test is NOT vacuous — it asserts list-count unchanged AND a _SpyStore put_calls==0, so "refused before storage" is proven, not just the 415 status; (2) ordering is real — test_base64_precedes_contenttype sends BOTH a disallowed type AND invalid base64 and gets 422, proving the guard sits after decode (the frozen 422-first); (3) byte-identical default — re-ran the 12 existing artifacts tests with the empty default; all green (26 passed/2 skipped together in one process, NO cross-file deadlock — the subagent's intermittent flake did not reproduce); (4) scope — git status shows only config/error_catalog/router + the new helper + test, no migration/FallbackModelRouter/proxy; (5) ruff clean + pyright 0 errors on the new module + router. RESIDUE (non-blocking): the sync unit test carries the module-level `pytestmark = pytest.mark.asyncio` → one cosmetic warning (same pattern as tasks 1/3). No overfit / stubbed-away logic found.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: orchestrator (auto-PASS, autonomy:auto) · date: 2026-06-30 — surfaced to Tin for spot-audit; no security finding.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose a guard at the top of the existing `create_artifact` handler fed by a CSV config knob + a pure normalize/match helper; rejected a Pydantic `field_validator` on `CreateArtifactRequest.content_type` (rejected: the validator can't read app Settings, so the allow-list can't be config-driven, and it would 422 not 415) · middleware inspecting the body (rejected: re-parses the JSON, can't see the resolved Settings cleanly, duplicates auth ordering)
- [human] freeze — froze §3 @ v1 (approved by Tin 2026-06-30 (422-first ordering))
- [AI] build — strategy used: as planned (delegated to backend-expert subagent). The pure helpers landed in a dedicated `apps/gateway/src/gateway/artifacts/content_type_policy.py` (3 functions: normalize/parse/is-allowed) rather than inline in the router — cleaner + unit-testable. Guard inserted at router.py L227 (after the size-cap raise, before `artifact_id`/store/commit), exactly the frozen 422→413→415 order. Subagent self-caught one E501 (moved the env-var name into the block comment).
- [AI] verify — gate PASS (reviewed by orchestrator (auto-PASS, autonomy:auto))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
