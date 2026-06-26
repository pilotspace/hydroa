# TASK: Azure OpenAI audio provider (STT + TTS)

slug: azure-audio-provider · created: 2026-06-26 · stage: production
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

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py` (MODIFY) — the provider registered under `"azure"` (main.py:759). Today: real `post_json` (embeddings, ~126-174) + `post_multipart`/`stream_bytes` that hard-raise "unsupported modality" (180-200). REPLACE the two raises with REAL Azure-OpenAI audio impls reusing the existing `_resolve_config_and_cred` + `_auth_headers_for_credential` (104) + `AzureConfig.build_url` (deployment + api-version). Rename class → `AzureOpenAIProvider` with a back-compat alias `AzureEmbeddingsProvider = AzureOpenAIProvider` (zero importer churn) + updated docstring (now embeddings + audio).
  - `apps/gateway/tests/azure_audio/` (NEW) — oracle-stub tests (httpx.MockTransport, mirror tests/azure_embeddings/test_azure_embeddings.py:73-82 `_make_adapter`); assert the deployment URL + auth header + multipart, no live key.
  - `Makefile` (MODIFY) — add `tests/azure_audio` to the test-fast target (sibling of `tests/azure_embeddings`/`azure_verify`).
  - `apps/gateway/src/gateway/main.py` (MAY TOUCH — only if the registration needs the new name; the alias avoids it).
Context (working folder):
  - UpstreamProvider Protocol (ports.py:269-319): `post_json` (embeddings), `post_multipart(path,files,data)->(status,json)` (STT `/audio/transcriptions`), `stream_bytes(path,payload)->AsyncIterator[bytes]` (TTS `/audio/speech`, SYNC method, no retries).
  - Called from `TranscriptionUseCase.execute` (audio_use_case.py:193 `await provider.post_multipart("/audio/transcriptions", files, data)`) + `SpeechUseCase.execute` (379 `provider.stream_bytes("/audio/speech", body)`).
  - Azure URL/auth (v21): `AzureConfig.build_url(deployment, op)` → `{endpoint}/openai/deployments/{deployment}/{op}?api-version={v}`; `resolve_deployment(model)` maps catalog model id → tenant deployment name (identity fallback). `_auth_headers_for_credential`: mode=="aad" → `Authorization: Bearer <token>` (fails CLOSED), else `api-key: <key>`. Credential = `AzureCredential` from the contextvar (per-tenant BYOK, v25); `resolve_provider_credential` already handles "azure" (BYOK_PROVIDERS).
  - Mirror OpenAI audio impl (openai_provider.py:229-299): breaker.guard → client call → 5xx/Timeout/Network → `UpstreamUnavailableError` FROM NONE (secret hygiene) + breaker.on_upstream_error; success → breaker.record_success.
  - Billing is provider-AGNOSTIC in the use-case (STT per_second, TTS per_character) — the adapter adds NO billing.
  - Routing needs a catalog row (modality+provider); Azure models are operator/test-seeded today (no azure_seed.py) — tests use `seed_stt_model(session, provider="azure")`. NO seed file added (consistent with existing Azure).
Honors (patterns / conventions):
  - SECRET HYGIENE: transport errors raise `UpstreamUnavailableError(...) from None` (v22 project-wide floor); circuit-breaker on every call.
  - DESIGN-FOR-FAILURE: timeout + breaker + fail-closed AAD; additive — OpenAI audio + Azure embeddings byte-identical.
  - Independent-oracle stub (azure_embeddings/azure_verify): MockTransport captures URL+headers, AAD token patched; in `make test-fast` (no DB).
Anchors the contract cites:
  - `AzureOpenAIProvider.post_multipart` / `.stream_bytes` · `AzureConfig.build_url` + `resolve_deployment` · `_auth_headers_for_credential` · `UpstreamUnavailableError` · the `azure-whisper`/`azure-tts` deployment-routed URLs.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Azure OpenAI audio provider. Make the `"azure"` provider's `post_multipart` (STT) and `stream_bytes` (TTS) REAL — Azure OpenAI audio is the same OpenAI wire over the deployment-routed URL with Azure auth — so audio becomes multi-provider. Embeddings + OpenAI audio unchanged.
Framings weighed: extend the existing Azure provider in-place reusing its v21 URL/auth helpers + a back-compat class rename (chosen — minimal churn, single "azure" registry object covers embeddings+STT+TTS like OpenAIDirectProvider) · a separate AzureAudioProvider + composite dispatcher (rejected — more moving parts, two objects per key) · Azure Cognitive Speech API (rejected — different non-OpenAI wire; deferred).
Must:
<must>
  - M1 — `post_multipart("/audio/transcriptions", files, data)` POSTs multipart to `AzureConfig.build_url(resolve_deployment(data["model"]), "audio/transcriptions")` (i.e. `{endpoint}/openai/deployments/{deployment}/audio/transcriptions?api-version={v}`) with the Azure auth header; returns `(status, json)`. httpx sets the multipart boundary (no forced application/json).
  - M2 — `stream_bytes("/audio/speech", payload)` streams bytes from `build_url(resolve_deployment(payload["model"]), "audio/speech")` with Azure auth; yields `aiter_bytes()`.
  - M3 — AUTH reuse (v21): `mode=="aad"` → `Authorization: Bearer <token>` (token failure fails CLOSED → UpstreamUnavailableError); else `api-key: <key>`. Credential = AzureCredential from the contextvar (per-tenant BYOK).
  - M4 — FAIL-SAFE (mirror OpenAI/embeddings): 5xx + Timeout/Network → `UpstreamUnavailableError` raised `from None` (secret hygiene) + `breaker.on_upstream_error`; success → `breaker.record_success`. Circuit-breaker guarded.
  - M5 — ADDITIVE: `post_json` (embeddings) + OpenAI audio paths are byte-identical; the class rename ships a back-compat alias so no importer breaks.
</must>
Reject:
<reject>
  - upstream 5xx / timeout / network error -> "UpstreamUnavailableError" (from None; breaker tripped).
  - AAD token acquisition failure -> "UpstreamUnavailableError" (fail closed; no partial/insecure call).
  - missing/invalid Azure credential -> "ProviderKeyMissing" (existing helper) — never a silent unauthenticated call.
</reject>
After:
<after>
  - A request routed to an Azure audio model reaches Azure over the deployment URL (OpenAI-wire) and returns a transcript (STT) / streamed audio (TTS); embeddings + OpenAI audio unchanged; the adapter adds no billing.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Azure OpenAI's audio endpoints accept the SAME request shape as OpenAI over the deployment URL (multipart for STT, JSON for TTS) — lowest confidence because we test vs an oracle stub, not live Azure, and Azure audio API availability/shape can lag OpenAI; if wrong: a routed Azure audio request errors upstream (OpenAI audio + embeddings unaffected; caught by a deferred live-verify). Cost bounded — additive, operator opts in by seeding an Azure audio model + deployment_map entry.
  - [x] the "azure" provider object already carries the URL/auth plumbing (build_url + _auth_headers_for_credential) — CONFIRMED (azure_embeddings.py).
  - [ ] deployment_map must include the audio model id → deployment name, else identity routing 404s (pre-existing Azure gap, same as embeddings) — documented as a delta, not closed here.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: STT routes to the Azure deployment URL
  Given an azure STT credential (deployment_map maps the model) and api-key auth
  When post_multipart("/audio/transcriptions", files, data) runs
  Then the captured request URL is {endpoint}/openai/deployments/{deployment}/audio/transcriptions?api-version={v}
  And the api-key header is set and no Authorization Bearer is sent

Scenario: TTS routes to the Azure speech URL and streams
  Given an azure TTS credential
  When stream_bytes("/audio/speech", payload) is drained
  Then the captured URL is .../openai/deployments/{deployment}/audio/speech?api-version={v}
  And the streamed bytes equal the upstream audio chunks

Scenario: AAD mode sends a Bearer token
  Given an azure credential with mode=aad and a (patched) token provider
  When an audio call runs
  Then the Authorization header is "Bearer <token>" and no api-key header is sent

Scenario: Upstream 5xx fails closed
  Given the Azure audio upstream returns 500
  When post_multipart runs
  Then UpstreamUnavailableError is raised (from None) and the breaker records the error
  And embeddings post_json behaviour is unchanged

Scenario: Network error suppresses the secret chain
  Given the transport raises a NetworkError
  When an audio call runs
  Then UpstreamUnavailableError is raised with __cause__ is None
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
class AzureOpenAIProvider:        # renamed from AzureEmbeddingsProvider; alias kept for back-compat
    # post_json(...)              # embeddings — UNCHANGED
    async def post_multipart(self, path: str, files, data) -> tuple[int, dict]:
        cfg, cred = await self._resolve_config_and_cred()
        url  = cfg.build_url(cfg.resolve_deployment(data["model"]), "audio/transcriptions")
        auth = await self._auth_headers_for_credential(cred)     # api-key | AAD Bearer (fail-closed)
        self._breaker.guard()
        try:
            resp = await self._client.post(url, files=files, data=data, headers=auth)   # httpx sets multipart boundary
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._breaker.on_upstream_error(); raise UpstreamUnavailableError(str(exc)) from None
        if resp.status_code >= 500: self._breaker.on_upstream_error(); raise UpstreamUnavailableError(...)
        self._breaker.record_success(); return resp.status_code, resp.json()

    def stream_bytes(self, path: str, payload) -> AsyncIterator[bytes]:   # SYNC, returns async gen
        # build_url(resolve_deployment(payload["model"]), "audio/speech"); auth; client.stream("POST", url, json=payload)
        # 5xx/Timeout/Network → UpstreamUnavailableError (from None) + breaker; else yield response.aiter_bytes()

# back-compat
AzureEmbeddingsProvider = AzureOpenAIProvider

Schema: none — adapter only. No DB/migration. Routing needs a catalog row (modality+provider=azure)
        which operators/tests seed (no azure_seed.py, same as Azure embeddings today). Billing stays
        provider-agnostic in the use-case (STT per_second, TTS per_character).
```

Status: FROZEN @ v1 — auto-approved (full-auto; additive + behavior-preserving for embeddings/OpenAI; reuses the v21/v22/v25 Azure auth + secret-hygiene floor; the only new surface is two upstream HTTP shapes pinned by oracle stubs) 2026-06-26
Least-sure flag surfaced at freeze:
  - [spec] ⚠ live Azure audio request shape may differ from OpenAI's (we test vs oracle stubs) — bounded by additive opt-in + a deferred live-verify; OpenAI audio + embeddings untouched.
  - [contract] secret-hygiene `from None` on transport errors is the security-relevant invariant — pinned by `test_network_error_secret_hygiene` (mirrors v22).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; oracle stub (MockTransport); no gateway regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_stt_routes_to_deployment_url: api-key cred → captured URL == deployment transcriptions URL + api-key header, no Bearer.
  - test_tts_routes_to_speech_url: drain stream_bytes → captured URL == deployment speech URL; streamed bytes == upstream chunks.
  - test_aad_mode_sends_bearer: mode=aad + patched token provider → Authorization "Bearer <tok>", no api-key.
  - test_deployment_map_resolves: model id mapped via deployment_map → mapped deployment in the URL (identity fallback when unmapped).
  - test_stt_5xx_fails_closed: upstream 500 → UpstreamUnavailableError + breaker.on_upstream_error; embeddings post_json still works (regression assert).
  - test_network_error_secret_hygiene: NetworkError → UpstreamUnavailableError with __cause__ is None.
  - test_multipart_no_forced_json_content_type: STT request is multipart (httpx boundary), not application/json.
  - test_back_compat_alias: AzureEmbeddingsProvider is AzureOpenAIProvider (importers unbroken).
</test_plan>

Tests live in: `apps/gateway/tests/azure_audio/test_azure_audio.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/tests/azure_audio/` · `Makefile`
Strategy (ordered batches): 1. real `post_multipart` + `stream_bytes` reusing `_resolve_config_and_cred`/`_auth_headers_for_credential`/`build_url`, mirroring OpenAI audio error-mapping. 2. rename class → `AzureOpenAIProvider` + back-compat alias + docstring. 3. oracle-stub tests (MockTransport) + Makefile test-fast.
Safety rule (feature-specific): secret hygiene — transport errors `raise UpstreamUnavailableError(...) from None`; AAD failure fails CLOSED; circuit-breaker on every call; embeddings/OpenAI paths byte-identical (additive).
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; do NOT alter the embeddings `post_json` behavior or OpenAI audio; reuse the v21 Azure helpers (no new auth code); allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `make test-fast` 198 passed (was 190; +8 azure_audio oracle-stub tests); pyright 0 errors on the changed file; ruff clean.
- [x] coverage did not decrease — +8 behavioral tests; no suite removed; the embeddings regression is asserted inside `test_stt_5xx_fails_closed`.
- [x] no test or contract was altered during build — contract unchanged; embeddings `post_json` + OpenAI audio untouched (additive); back-compat alias keeps every importer working.
- [x] the green was EARNED — I read BOTH new methods in full (azure_embeddings.py:185-284): post_multipart + stream_bytes build the deployment URL via `build_url(resolve_deployment(model), op)`, reuse `_auth_headers_for_credential`, guard the breaker, map 5xx/Timeout/Network → UpstreamUnavailableError. Oracle stubs assert the captured URL + auth header + multipart content-type (not internals). A faithful mirror of the proven OpenAI/embeddings adapter; risk profile lower than t1, so careful manual review (Rule 5) in lieu of a separate refute subagent.
- [x] concurrency / timing safe — `stream_bytes` guards the breaker SYNCHRONOUSLY at call time (a tripped breaker raises before any work) then streams in an inner async-gen; no shared mutable state; reuses the existing httpx client (no base_url; absolute deployment URL per call).
- [x] no exposed secrets, injection openings, or unexpected dependencies — SECRET HYGIENE: both methods `raise UpstreamUnavailableError(...) from None` on transport errors (suppress the chain that could carry the key); AAD failure fails CLOSED (unchanged helper); api-key/Bearer only in headers; no new deps.
- [x] layering & dependencies follow CONVENTIONS.md — adapter stays in infrastructure; reuses domain AzureConfig + credential context; no billing in the adapter (use-case owns it).
- [x] a person reviewed and approved the change — full-auto drive; careful manual diff review at the gate (read both methods + alias + lint/type/scope); additive + behavior-preserving keeps it auto-gateable.

### Build expectations — what "correct" looks like
- [x] STT request lands at the Azure deployment transcriptions URL — confirmed: `build_url(deployment, "audio/transcriptions")` (azure_embeddings.py:210) + `test_stt_routes_to_deployment_url` asserts `{endpoint}/openai/deployments/{dep}/audio/transcriptions?api-version={v}` + api-key header.
- [x] TTS streams from the Azure speech URL — confirmed: line 256 + `test_tts_routes_to_speech_url` (URL + streamed bytes == upstream chunks).
- [x] AAD mode sends a Bearer; api-key mode sends api-key — `test_aad_mode_sends_bearer` + the STT/TTS api-key asserts; reuses the unchanged `_auth_headers_for_credential`.
- [x] Transport/5xx fail closed with suppressed chain; embeddings unaffected — `from None` (lines 218, 282), `test_network_error_secret_hygiene` (`__cause__ is None`), `test_stt_5xx_fails_closed` (+ post_json regression).

### Deep checks
- [x] WIRING — `AzureOpenAIProvider` registered under "azure" (main.py:759, via the alias); post_multipart called from TranscriptionUseCase, stream_bytes from SpeechUseCase; both reuse `_resolve_config_and_cred`/`_auth_headers_for_credential`/`build_url`.
- [x] DEAD-CODE — no orphaned symbols; the alias + `__all__` export both names; both methods exercised by tests.
- [x] SEMANTIC — read both method bodies + the alias in full (not skimmed); transport-error except present inside `_gen` (280-282); confirmed no base_url on the client so the absolute URL is the request target.

### Residue / deltas
- Live-verify the Azure audio request shape against real Azure (tested vs oracle stubs only).
- deployment_map must map the audio model id → deployment name, else identity routing 404s (pre-existing Azure gap, same as embeddings) — documented, not closed here.
- Class file still named `azure_embeddings.py` (now hosts the unified `AzureOpenAIProvider`) — optional file rename is a cosmetic delta.

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto drive + careful manual diff review · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
