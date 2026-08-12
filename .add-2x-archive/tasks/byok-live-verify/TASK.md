# TASK: Byok Live Verify

slug: byok-live-verify · created: 2026-06-17 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Task: the v25 BYOK live verification — prove end-to-end that every upstream call authenticates with
the CALLING tenant's OWN resolved credential (resolved per request from the Fernet-encrypted store via
the request-scoped contextvar), for all 6 providers, and that a tenant with NO configured key for the
served provider FAILS CLOSED (`ERR_PROVIDER_KEY_MISSING`, 402, no platform fallback). Delivers v25
exit criteria #2 (every call authenticates with the calling tenant's key, all 6 providers — live
double-pass 6/6 ×2) and #3 (fail-closed). Mirrors the v20 bedrock-verify / v21 azure-verify two-layer
shape: an INDEPENDENT-ORACLE stub + a no-docker earned-green pytest (real adapters → real TCP) in the
CI floor, PLUS an operator live double-pass through the Envoy edge. NO production source change.

Touches (files · symbols · signatures):
- `scripts/v25_bearer_stub.py` (NEW) — pure-stdlib `http.server` on 127.0.0.1 emulating the 4 Bearer-
  flavoured providers behind ONE port, INDEPENDENTLY verifying the per-tenant credential the gateway
  presents (re-implements each provider's auth from spec; does NOT import gateway code):
  - OpenRouter `POST /api/v1/chat/completions` → `Authorization: Bearer` must equal `EXPECTED_OPENROUTER`
  - OpenAI `POST /v1/chat/completions`, `POST /v1/embeddings` → `Authorization: Bearer` == `EXPECTED_OPENAI`
  - Anthropic `POST /v1/messages` → `x-api-key` == `EXPECTED_ANTHROPIC` (NEVER Authorization)
  - Gemini `POST /v1beta/models/{m}:generateContent|:streamGenerateContent|:embedContent` → `x-goog-api-key` == `EXPECTED_GOOGLE`
  wrong/missing credential → 401 (oracle is not a rubber stamp). Counters per provider; `GET /__health`,
  `GET /__counters`, `POST /__reset`. `hmac.compare_digest`; `log_message` suppressed (never log a secret).
- `scripts/v20_bedrock_stub.py` (REUSE — no change) — port 9927, independent SigV4 verifier (proves
  Bedrock BYOK: PUT `access_key_id`/`secret_access_key`/`region` into the store, stub verifies the signature).
- `scripts/v21_azure_stub.py` (REUSE — no change) — port 9921, independent api-key / AAD-minted-token oracle
  (proves Azure BYOK: PUT `endpoint`/`api_key` (+ `authority` for AAD) into the store, stub verifies).
- `apps/gateway/tests/byok_verify/{__init__.py,test_byok_verify.py}` (NEW) — the EARNED-GREEN no-docker
  suite: starts the 3 stubs on ephemeral 127.0.0.1 ports, points the REAL adapters at them over real TCP,
  and for each provider SETS the per-tenant credential in the request-scoped contextvar
  (`set_provider_credential(...)`, the v25 BYOK seam) then calls the adapter — asserting ACCEPT (the stub
  saw the resolved secret), REJECT on a wrong/missing credential, and FAIL-CLOSED (`ProviderKeyMissing`
  when the contextvar is unset). Joins the `make test-fast` no-DB floor.
- `scripts/live_v25_verify.py` (NEW) — operator live double-pass through the Envoy edge: signup→login→key,
  then `PUT /admin/provider-keys/{provider}` for all 6 (BYOK write), then a chat/embeddings call per
  provider (resolved from the encrypted store), plus a fail-closed call for an UNconfigured provider
  (→402). run ×2. Mirrors `scripts/live_v21_verify.py` (fresh run_id; 127.0.0.1 stub assert; `/__reset`).
- `infra/docker-compose.e2e.v25.yml` (NEW) — overlay: `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY` (Fernet) +
  the 5 base-URL knobs → stubs (`GATEWAY_OPENROUTER_BASE_URL`/`GATEWAY_OPENAI_BASE_URL`/
  `GATEWAY_ANTHROPIC_BASE_URL`/`GATEWAY_GOOGLE_BASE_URL` → host.docker.internal:9928;
  `GATEWAY_BEDROCK_ENDPOINT_URL` → :9927); azure endpoint is injected per-tenant via the PUT body, NOT env;
  NO `GATEWAY_*_API_KEY` (BYOK = no env keys) + retry/cache knobs + catalog seed handled by the script.
- `Makefile` (MODIFY) — add `tests/byok_verify` to the `test-fast` no-DB floor list.

Context (working folder): repo root for scripts/infra/Makefile; apps/gateway for the pytest suite. Tests:
`cd apps/gateway && uv run --no-sync pytest -p no:cacheprovider --no-cov -q tests/byok_verify`. Live
(operator): bring up the e2e stack with the v25 overlay then `python3 scripts/live_v25_verify.py` twice.

BYOK seam (the v25 mechanic this task proves, NO source change):
- `proxy/domain/credential_context.py` — `set_provider_credential(cred)` / `get_provider_credential()` ContextVar.
- `proxy/domain/provider_credentials.py` — `BearerCredential` / `BedrockCredential` / `AzureCredential` value-objects; `ProviderKeyMissing(code="ERR_PROVIDER_KEY_MISSING")`.
- `proxy/application/use_cases.py:resolve_provider_credential` — resolver → contextvar; `ProviderKeyMissing` → `ProblemError(402, ...)`.
- Adapter auth knobs (frozen): `GATEWAY_{OPENROUTER,OPENAI,ANTHROPIC,GOOGLE}_BASE_URL`, `GATEWAY_BEDROCK_ENDPOINT_URL`; azure endpoint from `AzureCredential.endpoint`.

Honors (patterns / conventions):
- STUB BINDING: 127.0.0.1 ONLY, NEVER 0.0.0.0 (security §1; mirror the v19/v20/v21 HARD-STOP guard).
- SECRET DISCIPLINE: FAKE creds only; `log_message` suppressed; `hmac.compare_digest`; never log a header.
- INDEPENDENT ORACLE: the new stub does NOT import gateway code; ACCEPT proves the REAL adapter built the
  right header FROM THE CONTEXTVAR credential (the BYOK resolution path), not a boot key.
- DOUBLE-PASS / FLOOR: the live script is idempotent (fresh run_id; `/__reset` between passes; assert
  127.0.0.1); any DB flake uses surgical XTRIM, NEVER FLUSHDB (v12 ledger-flusher lesson).
- NO PRODUCTION SOURCE CHANGE: adds only the new bearer stub + tests + live script + overlay + Makefile
  floor entry; reuses v20/v21 stubs unchanged; all 6 adapters + the BYOK seam are FROZEN (v25 tasks 1-5).

Anchors the contract cites: `v25_bearer_stub` (verify per-provider header / routes / counters); the REAL
`OpenRouterUpstream`/`OpenAIProvider`/`AnthropicUpstream`/`GeminiUpstream`/`BedrockUpstream`/`AzureUpstream`
pointed at stubs with `set_provider_credential(...)`; `ProviderKeyMissing`/`ERR_PROVIDER_KEY_MISSING`;
`v20_bedrock_stub`/`v21_azure_stub` reuse; `make test-fast` floor; `live_v25_verify` double-pass + overlay.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: End-to-end verification that the v25 BYOK loop is WIRE-CORRECT — every upstream call (all 6
providers) authenticates with the CALLING tenant's credential resolved per request from the contextvar
(earned-green, real TCP) and, live, from the Fernet-encrypted store through the Envoy edge — and that a
tenant with NO key for the served provider FAILS CLOSED with no platform fallback. Zero production
source change; the adapters + BYOK seam are frozen from v25 tasks 1-5.

Framings weighed:
  - an auth-VERIFYING bearer stub + a no-docker pytest that drives the REAL adapters from the CONTEXTVAR
    credential over real TCP (earned-green core), REUSING the v20 SigV4 + v21 azure oracles, PLUS operator
    scripts for the live edge double-pass (CHOSEN — mirrors the frozen v20/v21 two-layer shape; the pytest
    core is CI-able and proves the contextvar→adapter→upstream header is wire-correct for all 6).
  - operator-script-only (REJECTED — leaves nothing in the CI floor; v20/v21 set the two-layer precedent).
  - MockTransport unit test (REJECTED — never exercises the real adapter over a real socket; tasks 2-3
    already have MockTransport coverage; this task's value is the real-TCP independent cross-check).

Must:
<must>
  - the new bearer stub INDEPENDENTLY verifies the per-tenant credential on every model request BEFORE
    serving a canned body: OpenRouter/OpenAI `Authorization: Bearer` == the provider's EXPECTED secret;
    Anthropic `x-api-key` == EXPECTED; Gemini `x-goog-api-key` == EXPECTED; wrong/missing → 401.
  - for EACH of the 4 Bearer providers: the REAL adapter, with `set_provider_credential(BearerCredential(
    secret=EXPECTED))` set in the contextvar and `base_url`→stub, is ACCEPTED and returns the provider's
    success shape; the stub counter confirms the request arrived with the resolved secret.
  - Bedrock: the REAL adapter with `set_provider_credential(BedrockCredential(...fake AWS triple...))` and
    endpoint→v20 stub is ACCEPTED (the stub's independent SigV4 verify passes against the resolved creds).
  - Azure: the REAL adapter with `set_provider_credential(AzureCredential(mode=api_key, endpoint=stub,
    api_key=EXPECTED, ...))` is ACCEPTED (the v21 stub's api-key oracle passes); the AAD path is exercised
    by constructing `AzureCredential(mode=aad, authority=stub, ...)` (the stub mints + verifies the token).
  - FAIL-CLOSED: with the contextvar UNSET (or holding the wrong credential type), each adapter raises
    `ProviderKeyMissing` (code `ERR_PROVIDER_KEY_MISSING`) — no upstream request is made.
  - zero-regression floor: the new stub binds 127.0.0.1 (never 0.0.0.0); the live script + overlay exist
    and are idempotent (fresh run_id, `/__reset` between passes, 127.0.0.1 assert, PUT-per-provider, the
    Fernet-key env, no `GATEWAY_*_API_KEY`); `make test-fast` floor stays green.
</must>
Reject:
<reject>
  - wrong Bearer / wrong x-api-key / wrong x-goog-api-key / missing credential -> stub responds 401 (the
    oracle is not a rubber stamp); wrong SigV4 -> v20 stub 403; wrong azure api-key -> v21 stub 401.
  - the contextvar unset for the served provider -> `ProviderKeyMissing`/402 (live), NO platform fallback.
  - stub bound to anything but 127.0.0.1 -> HARD-STOP (security §1).
</reject>
After:
<after>
  - The BYOK loop is proven wire-correct over real TCP for all 6 providers (each adapter builds the right
    auth header FROM the contextvar credential) and fails closed when unset; the live double-pass ×2 is
    GREEN (or recorded as the operator step with exact commands when the docker e2e stack is not runnable
    this session — identical to how v20 bedrock-verify / v21 azure-verify gated).
  - v25 exit criteria #2 (all 6 authenticate with the tenant's resolved key) and #3 (fail-closed) are
    observably satisfied; the milestone goal is met.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The live double-pass ×2 through the Envoy edge can be executed THIS session — lowest confidence: it
    needs the full multi-overlay docker e2e stack up AND a fresh gateway image build (the BYOK source on
    this branch). If the stack is not runnable here, the autonomous proof is the real-TCP earned-green
    suite (which wire-exercises every provider's contextvar→adapter→upstream auth incl. fail-closed), and
    the live pass is recorded as the operator step with exact commands — IDENTICAL to how v20/v21 gated.
  - [x] All 6 adapters read the credential from the contextvar (no boot key) — confirmed by v25 tasks 2-3
    (the `GATEWAY_*_API_KEY` Settings fields were removed; adapters call `get_provider_credential()`).
  - [x] The 5 base-URL knobs redirect the bearer + bedrock adapters; azure endpoint comes from the PUT
    body credential (not env) — confirmed in §0 ground map.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: independent bearer oracle rejects wrong and missing credentials                 # BV1
  Given the v25 bearer stub on 127.0.0.1
  When a raw request is sent per provider with a wrong then a missing credential header
  Then the stub responds 401 every time (Authorization/x-api-key/x-goog-api-key all gated)

Scenario: OpenRouter authenticates with the contextvar credential                          # BV2
  Given set_provider_credential(BearerCredential(EXPECTED_OPENROUTER)) and a REAL OpenRouterUpstream(base_url=stub)
  When complete({model, messages}) is called
  Then the stub ACCEPTS (Authorization: Bearer matched) and returns an OpenAI chat.completion with usage

Scenario: OpenAI authenticates with the contextvar credential                              # BV3
  Given set_provider_credential(BearerCredential(EXPECTED_OPENAI)) and a REAL OpenAIProvider(base_url=stub)
  When complete(...) is called
  Then the stub ACCEPTS (Authorization: Bearer matched) and returns a chat.completion with usage

Scenario: Anthropic authenticates with x-api-key from the contextvar credential            # BV4
  Given set_provider_credential(BearerCredential(EXPECTED_ANTHROPIC)) and a REAL AnthropicUpstream(base_url=stub)
  When complete(...) is called
  Then the stub ACCEPTS (x-api-key matched, NO Authorization header) and returns a translated completion

Scenario: Gemini authenticates with x-goog-api-key from the contextvar credential          # BV5
  Given set_provider_credential(BearerCredential(EXPECTED_GOOGLE)) and a REAL GeminiUpstream(base_url=stub)
  When complete(...) is called
  Then the stub ACCEPTS (x-goog-api-key matched) and returns a translated completion with usage

Scenario: Bedrock authenticates with SigV4 from the contextvar credential                  # BV6
  Given set_provider_credential(BedrockCredential(fake AWS triple)) and a REAL BedrockUpstream(endpoint=v20 stub)
  When complete(...) is called
  Then the v20 stub's independent SigV4 verify PASSES and returns a converse result (not 403)

Scenario: Azure authenticates with api-key (and AAD) from the contextvar credential        # BV7
  Given set_provider_credential(AzureCredential(mode=api_key, endpoint=v21 stub, api_key=EXPECTED, deployment_map))
  When complete(...) is called
  Then the v21 stub's api-key oracle ACCEPTS and returns a chat.completion
  And mode=aad (authority=stub) is ACCEPTED against the stub-minted token (end-to-end AAD)

Scenario: fail-closed when the contextvar is unset                                         # BV8
  Given the request-scoped contextvar holds NO credential (or the wrong type) for a provider
  When the REAL adapter builds its auth headers
  Then it raises ProviderKeyMissing (code ERR_PROVIDER_KEY_MISSING) and makes NO upstream request

Scenario: zero-regression floor — stub binding + live artifacts                            # BV9/BV10
  Given the new stub + live artifacts
  Then the bearer stub binds 127.0.0.1 (never 0.0.0.0)
  And scripts/live_v25_verify.py + infra/docker-compose.e2e.v25.yml exist and are idempotent
      (run_id, /__reset, 127.0.0.1, PUT /admin/provider-keys per provider, GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY,
       host.docker.internal, and NO GATEWAY_*_API_KEY)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
scripts/v25_bearer_stub.py  (pure stdlib; NO gateway import):
  STUB_HOST = "127.0.0.1"   # NEVER 0.0.0.0
  EXPECTED_OPENROUTER, EXPECTED_OPENAI, EXPECTED_ANTHROPIC, EXPECTED_GOOGLE   # fake public test secrets
  verify_bearer(headers, expected) -> bool         # hmac.compare_digest on Authorization: Bearer
  verify_header(headers, name, expected) -> bool   # hmac.compare_digest on x-api-key / x-goog-api-key
  make_stub_server(port=0) -> HTTPServer           # ephemeral port for the no-docker suite
  start_stub_in_thread(server) -> Thread
  routes (verify credential BEFORE any canned body, else 401):
    POST /api/v1/chat/completions                          -> OpenAI chat.completion        (Bearer==OPENROUTER)
    POST /v1/chat/completions                              -> OpenAI chat.completion        (Bearer==OPENAI)
    POST /v1/embeddings                                    -> OpenAI embeddings list        (Bearer==OPENAI)
    POST /v1/messages                                      -> Anthropic message             (x-api-key==ANTHROPIC)
    POST /v1beta/models/{m}:generateContent|:streamGenerateContent|:embedContent
                                                          -> Gemini candidates / embedding (x-goog-api-key==GOOGLE)
    GET /__health · GET /__counters · POST /__reset
  REUSE unchanged: scripts/v20_bedrock_stub.py (:9927 SigV4) · scripts/v21_azure_stub.py (:9921 api-key/AAD)

apps/gateway/tests/byok_verify/test_byok_verify.py:  BV1..BV10 (one per scenario + the floor), no-DB, real TCP,
  driving the REAL adapters from set_provider_credential(...) — joins the make test-fast floor.
scripts/live_v25_verify.py:        operator double-pass — PUT /admin/provider-keys/{p} for all 6, then a call
  per provider through :8080, plus a fail-closed unconfigured-provider call (→402); run_id, /__reset, 127.0.0.1.
infra/docker-compose.e2e.v25.yml:  GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY + 5 base-URL knobs → stubs (azure via
  PUT body) + retry/cache; NO GATEWAY_*_API_KEY.
Makefile:                          test-fast floor += tests/byok_verify

Schema: none (verification only; live pass reads billing rows via psql, never writes source).
```

Least-sure flag surfaced at freeze: [scenario] whether the live double-pass ×2 runs THIS session (needs
the full docker e2e stack + a fresh BYOK gateway image build) — if not runnable here, the autonomous
proof is the real-TCP earned-green BV-suite (every provider's contextvar→adapter→upstream auth + the
fail-closed path) and the live pass is the recorded operator step, exactly as v20 bedrock-verify / v21
azure-verify gated. Cost: edge/cache/billing/store-decrypt operator-confirmed; wire-correctness auto-proven.
Status: FROZEN @ v1 — approved by Tin Dang (auto-mode delegated; verification-only, no production source change, mirrors the frozen v20/v21 verify two-layer shape)

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral (one test per scenario; the stubs + REAL adapters over real TCP, driven from the contextvar)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - BV1_bearer_oracle_rejects_wrong_and_missing: raw httpx → wrong + missing credential per provider → 401
  - BV2_openrouter_contextvar_auth_accepted: real OpenRouterUpstream(base_url=stub) + set_provider_credential → 200 + usage; counter saw Bearer
  - BV3_openai_contextvar_auth_accepted: real OpenAIProvider(base_url=stub) + contextvar → 200 + usage
  - BV4_anthropic_contextvar_auth_accepted: real AnthropicUpstream(base_url=stub) + contextvar → 200 translated; x-api-key matched, no Authorization
  - BV5_gemini_contextvar_auth_accepted: real GeminiUpstream(base_url=stub) + contextvar → 200 translated; x-goog-api-key matched
  - BV6_bedrock_contextvar_sigv4_accepted: real BedrockUpstream(endpoint=v20 stub) + BedrockCredential → SigV4 verified (not 403)
  - BV7_azure_contextvar_apikey_and_aad_accepted: real AzureUpstream(v21 stub) + AzureCredential api_key (+ aad authority=stub) → 200
  - BV8_fail_closed_contextvar_unset: contextvar unset/wrong-type → each adapter raises ProviderKeyMissing(ERR_PROVIDER_KEY_MISSING), no request
  - BV9_localhost_binding: bearer stub host == 127.0.0.1 and STUB_HOST == 127.0.0.1
  - BV10_live_artifacts_exist_and_idempotent: live script + overlay exist; run_id + /__reset + 127.0.0.1 + PUT /admin/provider-keys + GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY + host.docker.internal present; overlay sets no GATEWAY_*_API_KEY
</test_plan>

Tests live in: `apps/gateway/tests/byok_verify/test_byok_verify.py` · MUST run red (missing stub/script/overlay) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `scripts/v25_bearer_stub.py` · `scripts/live_v25_verify.py` · `infra/docker-compose.e2e.v25.yml` · `apps/gateway/tests/byok_verify/` · `Makefile`
Strategy (ordered batches): 1. red suite (importlib-load the not-yet-existing bearer stub + reuse v20/v21 stubs). 2. v25_bearer_stub.py (independent per-provider auth oracle + routes). 3. green BV1-BV10 by pointing the REAL adapters at the stubs from the contextvar. 4. live_v25_verify.py + overlay + Makefile floor entry (BV10 existence/idempotency). 5. attempt the live double-pass if the docker e2e stack is runnable.
Safety rule (feature-specific): every stub binds 127.0.0.1 ONLY; FAKE creds only; never log auth headers; NO production source change (adapters + BYOK seam frozen; v20/v21 stubs reused unchanged).
Code lives in: `scripts/` + `infra/` + `apps/gateway/tests/byok_verify/` + `Makefile`
Constraints: do NOT change any frozen adapter, the BYOK seam, the v20/v21 stubs, or this contract; pure-stdlib new stub (no gateway import); allow-list packages only.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — byok_verify earned-green **10/10** (BV1-BV10, no-DB, joined `make test-fast`) + **LIVE double-pass 17/17 ×2** through Envoy (all 6 providers authenticate with the per-tenant key; fail-closed 402)
- [x] coverage did not decrease — new no-DB suite added; no source removed (task is operator scripts + overlay + Makefile floor entry)
- [x] no test or contract was altered during build — §5 scope only: `scripts/v25_bearer_stub.py` · `scripts/live_v25_verify.py` · `infra/docker-compose.e2e.v25.yml` · `Makefile` (floor entry); v20/v21 stubs reused unchanged; NO gateway src change in THIS task
- [x] the green was EARNED, not gamed — the new stub does NOT import gateway code; ACCEPT proves the REAL adapter built the auth header FROM the contextvar credential; BV1 proves the oracle is not a rubber stamp; BV8 proves fail-closed. The live pass CAUGHT a real v25 regression earned-green missed (openai dispatch 500) → fixed in task `openai-chat-complete` (gate PASS), then live re-ran 17/17 ×2
- [x] concurrency / timing of the risky operation is safe — stubs single-threaded HTTPServer per port; counters lock-guarded; live double-pass resets counters each pass (idempotent)
- [x] no exposed secrets, injection openings, or unexpected dependencies (FAKE creds; hmac.compare_digest; log suppressed; 127.0.0.1 only; overlay sets NO GATEWAY_*_API_KEY)
- [x] layering & dependencies follow CONVENTIONS.md — pure-stdlib stub; live script uses urllib only; no new deps
- [x] a person reviewed and approved the change — Tin authorized the live run + the task-7 fix sequencing (AskUserQuestion); pending final commit review

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every stub route exercised by BV1-BV10 + the live 17/17 (all 6 providers PUT→chat→200, stub counters incremented); live artifacts grepped by BV10
- [x] DEAD-CODE (code) — no orphaned stub branch; every provider route hit by a test or the live pass
- [x] SEMANTIC (prose / non-code) — live script + overlay read in full: env (Fernet key, base-URL→stub :9928/:9927/:9921, no API keys), PUT-per-provider, fail-closed call (tenant B), double-pass /__reset

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (auto-driven; live double-pass 17/17 ×2 @ run_ids 1781671154/1781671170; regression found+fixed via task openai-chat-complete) · date: 2026-06-17

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-provider auth-reject rate; ERR_PROVIDER_KEY_MISSING rate; store-decrypt failures; AAD token-acquisition failures.
Spec delta for the next loop: the v25 BYOK loop is proven end-to-end — every provider authenticates with the calling tenant's per-request resolved credential, and an unconfigured provider fails closed with no platform fallback. The platform holds zero provider env keys.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency (`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
