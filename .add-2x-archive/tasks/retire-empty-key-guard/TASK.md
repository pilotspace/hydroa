# TASK: Retire the vestigial empty-upstream-key boot guard

slug: retire-empty-key-guard · created: 2026-06-17 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- low-risk: deletes confirmed-dead code (a no-op over an empty tuple) + behavior-preserving
     test surgery. The only nuance is preserving the BYOK env-secret-absence invariants the
     deleted constant's assertions weakly approximated — re-pinned against Settings.model_fields. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/core/config.py` — three symbols to DELETE, all dead post-BYOK:
  - `EmptyUpstreamKeyError(ValueError)` (L20) — raised only by the guard below.
  - `_UPSTREAM_KEY_ENV_VARS: Final[tuple[str, ...]] = ()` (L31) — the EMPTY tuple the guard iterates.
  - `validate_upstream_keys(env=None)` (L34) — iterates the empty tuple ⇒ provable no-op.
  - now-unused imports once those go: `import os` (only `os.environ` at L44), `Mapping` (only the sig),
    `Final` (only the constant). `Annotated` STAYS (DeploymentSpec L113). `json` STAYS.
- `apps/gateway/src/gateway/main.py` — `from gateway.core.config import Settings, validate_upstream_keys` (L27)
  and the call `validate_upstream_keys()` (L182). Drop both; the call does nothing today.
- Test references (code-file grep, exhaustive):
  - `apps/gateway/tests/empty_key_boot_guard/test_empty_key_boot_guard.py` — imports the guard;
    4 tests. `test_absent_key_is_allowed` is pure dead-guard (drop). Tests 2 & 3 assert (a) vars not in
    `_UPSTREAM_KEY_ENV_VARS` [moot] AND (b) bedrock/azure secret fields absent from `Settings.model_fields`
    [VALUABLE — keep]. `test_create_app_ok_when_secret_keys_absent` imports only Settings+create_app [VALUABLE — keep].
  - `apps/gateway/tests/credential_resolution_seam/test_credential_resolution_seam.py` —
    `test_bearer_env_removed_boots_clean` (L457) imports `_UPSTREAM_KEY_ENV_VARS`; asserts bearer vars not in it
    [moot] + bearer api_key fields absent from Settings [keep] + 4 bearer adapters registered unconditionally [keep].
  - `apps/gateway/tests/bedrock_sigv4/test_bedrock_sigv4.py` —
    `test_config_boot_guard_excludes_bedrock_keys` (L363) is ENTIRELY about `_UPSTREAM_KEY_ENV_VARS`; rewrite to
    pin the surviving invariant (bedrock secret fields absent from Settings).
  - `apps/gateway/tests/azure_auth_routing/test_azure_config.py` — COMMENTS ONLY (L9 stale CONTRACT note);
    no import/call — won't break; light comment hygiene.

Context (working folder): apps/gateway — pyright-strict; `make test-fast` is the no-DB floor.
Honors (patterns / conventions): BYOK invariant — the platform Settings carries NO provider api_key/secret
field; credentials are per-tenant at request time (credential-resolution-seam §3, dynamic-auth-byok §3).
Anchors the contract cites: `gateway.core.config` (module surface), `Settings.model_fields`, `create_app`.

WHY now: the guard became a no-op when v25 task-3 emptied `_UPSTREAM_KEY_ENV_VARS`; the v25 milestone close
flagged it "Follow-up: delete the vestigial guard function." Tin chose full retire (2026-06-17).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: retire the empty-upstream-key boot guard (dead code) while preserving the BYOK env-secret-absence invariants.
Framings weighed: full retire (chosen — Tin's call; the guard is a no-op and its `_UPSTREAM_KEY_ENV_VARS`
assertions are near-vacuous absence-from-empty-tuple checks) · leave it (rejected: dead code + misleading
"boot guard" name) · repurpose to validate the Fernet key (rejected by Tin — out of cleanup scope).
Must:
<must>
  - DELETE `EmptyUpstreamKeyError`, `_UPSTREAM_KEY_ENV_VARS`, and `validate_upstream_keys` from config.py,
    plus the imports that become unused (`os`, `Mapping`, `Final`). Leave the rest of config.py byte-unchanged.
  - REMOVE the `validate_upstream_keys` import and its `validate_upstream_keys()` call from main.py. create_app's
    observable behavior is unchanged (the call was a no-op).
  - PRESERVE the BYOK invariant in tests, re-expressed without the deleted constant: no provider api_key/secret
    field exists on `Settings.model_fields` (bearer: openrouter/openai/anthropic/google; bedrock: access_key_id/
    secret_access_key/session_token; azure: api_key/client_secret), and create_app boots clean with no env secrets.
  - PRESERVE the unconditional-registration assertion (all chat adapters register without env keys).
Reject:
<reject>
  - importing any of the three deleted symbols from `gateway.core.config` -> ImportError / AttributeError
    (the retirement is observable: `hasattr(config, "validate_upstream_keys")` is False).
</reject>
After:
<after>
  - `gateway.core.config` exposes none of the three symbols; `import gateway.core.config` and `create_app` still work.
  - main.py no longer imports or calls `validate_upstream_keys`.
  - Every preserved invariant test stays green; no provider-secret Settings field exists.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ no code path outside the mapped 6 files depends on the three symbols — lowest confidence because a missed
    importer would ImportError at collection; if wrong: a test/module fails to import. Mitigation: the code-file
    grep was exhaustive (only these 6 files match) and `make test-fast` + the broad suite run will surface any miss.
  - [ ] `os` / `Mapping` / `Final` are unused elsewhere in config.py — confirmed by pattern search (only the guard).
  - [ ] the guard is a true no-op — confirmed: it iterates an empty tuple, so it never raises.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked, top ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: the boot-guard symbols are retired
  Given the gateway after this task
  When gateway.core.config is imported
  Then it exposes no validate_upstream_keys, _UPSTREAM_KEY_ENV_VARS, or EmptyUpstreamKeyError
  And main.py neither imports nor calls validate_upstream_keys

Scenario: the gateway still boots
  Given create_app(test settings) with no provider env secrets
  When the app is constructed
  Then it returns a FastAPI app (no boot failure)

Scenario: BYOK invariant preserved — no provider secret field on Settings
  Given Settings.model_fields
  When inspected
  Then it contains none of openrouter/openai/anthropic/google _api_key, bedrock access_key_id/
       secret_access_key/session_token, or azure api_key/client_secret

Scenario: unconditional adapter registration unchanged
  Given create_app with no env keys
  When app.state.chat_adapters is inspected
  Then openrouter, openai, anthropic, google are all registered
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# module-surface contract (deletion; no HTTP/schema change)

gateway.core.config  (AFTER):
  - REMOVED: EmptyUpstreamKeyError, _UPSTREAM_KEY_ENV_VARS, validate_upstream_keys
  - REMOVED imports: os, collections.abc.Mapping, typing.Final
  - UNCHANGED: Settings, Deployment, DeploymentSpec, all other symbols (byte-for-byte)
  - Settings.model_fields contains NO provider api_key/secret field (invariant, already true)

gateway.main.create_app:
  - REMOVED: `validate_upstream_keys` from the config import; the `validate_upstream_keys()` call
  - UNCHANGED: every other behavior (the removed call was a no-op)

Tests (preserved invariants, re-expressed against Settings.model_fields / module surface):
  - empty_key_boot_guard/: drop the pure dead-guard test; keep+rewrite the Settings-field-absence
    invariants (bedrock, azure); keep create_app-boots-clean; ADD the retirement assertion.
  - credential_resolution_seam/test_bearer_env_removed_boots_clean: drop the _UPSTREAM_KEY_ENV_VARS
    assertion; keep the bearer-field-absence + unconditional-registration assertions.
  - bedrock_sigv4/test_config_boot_guard_excludes_bedrock_keys: rewrite to assert bedrock secret
    fields absent from Settings.model_fields.
Schema: none touched.
```

Status: FROZEN @ v1 — approved by Tin (project-lead, auto 2026-06-17)
Least-sure flag surfaced at freeze: [test] the only judgment call is re-pinning the BYOK invariant — I delete
the near-vacuous `_UPSTREAM_KEY_ENV_VARS` assertions but REPLACE them with `Settings.model_fields` field-absence
checks (stronger: an empty-tuple membership check is vacuous; a "field does not exist" check actually fails if a
secret field is re-added). Why it could be wrong: if a future provider stores its key in a differently-named
Settings field, the enumerated set would miss it. Cost: a re-introduced env-secret could slip the guard — low,
because BYOK resolution (not Settings) is the live credential path and adapters fail-closed on the contextvar.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: the deletion is observable + every preserved invariant still asserted.
Plan (one test per scenario; the retirement assertion is the RED one, the rest are preserved/green-by-design):
<test_plan>
  - test_boot_guard_symbols_retired (NEW, RED→GREEN): config has no validate_upstream_keys/_UPSTREAM_KEY_ENV_VARS/
    EmptyUpstreamKeyError; main.py source contains no "validate_upstream_keys".
  - test_create_app_ok_when_secret_keys_absent (KEEP): create_app boots with secret env vars unset.
  - test_bedrock_secret_fields_absent_from_settings (rewritten from test 2): no bedrock secret field on Settings.
  - test_azure_secret_fields_absent_from_settings (rewritten from test 3): no azure secret field on Settings.
  - credential_resolution_seam test_bearer_env_removed_boots_clean (rewritten): no bearer api_key field on
    Settings + all 4 bearer adapters registered (drop the _UPSTREAM_KEY_ENV_VARS assertion).
  - bedrock_sigv4 test (rewritten): bedrock secret fields absent from Settings (drop _UPSTREAM_KEY_ENV_VARS).
</test_plan>

Tests live in: `apps/gateway/tests/empty_key_boot_guard/` (+ in-place rewrites of the credential_resolution_seam
and bedrock_sigv4 references) · the NEW retirement assertion MUST run red before Build (symbols still present).

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/empty_key_boot_guard/` `apps/gateway/tests/credential_resolution_seam/test_credential_resolution_seam.py` `apps/gateway/tests/bedrock_sigv4/test_bedrock_sigv4.py` `apps/gateway/tests/azure_auth_routing/test_azure_config.py`
Strategy (ordered batches): 1. (tests phase) rewrite the existing test references off the deleted constant and add the retirement assertion · 2. (build) delete the 3 symbols + unused imports from config.py · 3. (build) drop the import+call in main.py.
Safety rule (feature-specific): touch ONLY the three symbols + their dead imports in config.py — leave Settings and everything else byte-identical. Behavior must be unchanged (the guard was a no-op).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT weaken a preserved invariant (re-express it, don't drop it); allow-list packages only; ask if unclear.

<!-- Scope tokens on the FIRST "Scope (may touch)" line; each contains "/" so each resolves from project root;
     the empty_key_boot_guard/ directory token covers its whole subtree. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — affected suites (empty_key_boot_guard + credential_resolution_seam + bedrock_sigv4 +
      azure_auth_routing) 30/30; no-DB floor + retry_policy_wiring 159/159; FULL tree collected 1125/1144 with
      ZERO import errors (proves no residual importer of the deleted symbols); ruff clean; pyright (src) 0 errors.
- [x] coverage did not decrease — invariants RE-PINNED against Settings.model_fields, not dropped (only the
      vacuous absence-from-empty-tuple checks were removed); refute-read confirmed each is non-vacuous.
- [x] no test or contract was altered during build — the test rewrites were done in the tests phase with a
      re-snapshot; build touched only config.py + main.py (pure deletion).
- [x] the green was EARNED — adversarial refute-read (sonnet, conf 0.97): CLEAN DELETION, NO WEAKENING. Every
      meaningful assertion (Settings-field-absence, unconditional adapter registration, create_app-boots-clean)
      preserved or strengthened; only vacuous _UPSTREAM_KEY_ENV_VARS membership checks dropped; zero live importers.
- [x] no exposed secrets, injection openings, or unexpected dependencies — pure deletion of a no-op + dead imports.
- [x] layering & dependencies follow CONVENTIONS.md — config.py surface shrinks; Settings & all else byte-unchanged.
- [x] a person reviewed and approved the change — Tin (project-lead, auto 2026-06-17); refute-read = adversarial reviewer.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] DEAD-CODE (code) — the 3 symbols + os/Mapping/Final imports fully removed; json/Annotated retained (still used);
      no orphaned reference (ruff + pyright clean; full-tree collect has 0 import errors).
- [x] WIRING (code) — main.py boot path unchanged except the removed no-op validate_upstream_keys() call; create_app
      still builds (test_create_app_ok_when_secret_keys_absent green).
- [x] SEMANTIC (prose / non-code) — n/a (code task).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (project-lead, auto) · date: 2026-06-17

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): boot success; absence of the retired symbols in any future importer.
Spec delta for the next loop: the env-based provider-key boot guard is fully gone; BYOK per-tenant resolution
is the sole credential path. v26 cleanup debt cleared.

### Competency deltas
- [ADD · folded] when retiring dead code whose tests doubled as weak invariant guards, re-express the invariant
  against a live surface (Settings.model_fields) rather than deleting the assertion (evidence: this task).
