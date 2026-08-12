# TASK: Amazon Titan embeddings via InvokeModel <-> OpenAI /v1/embeddings (exact token accounting)

slug: bedrock-embeddings · created: 2026-06-15 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
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
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py` (NEW) — `BedrockEmbeddingsProvider`
  implementing the `UpstreamProvider` Protocol (ports.py:254). `post_json("/embeddings", payload) -> tuple[int,dict]`
  maps OpenAI /v1/embeddings ⇄ Amazon Titan InvokeModel. SigV4-signed (service="bedrock", raw model_id in the
  /model/{id}/invoke path — same %3A rule as the chat adapter). + pure helper `_titan_to_openai_embeddings`.
- `apps/gateway/src/gateway/main.py` (MODIFY) — register `_providers["bedrock"] = BedrockEmbeddingsProvider(...)`
  inside the existing `if _aws_creds:` block (the chat block already resolves `_aws_creds`), before
  `ProviderRegistry(_providers)` (~main.py:535-549).
- REUSE: `bedrock_sigv4.sign_request` (service="bedrock" — CONFIRMED via botocore signingName) + `resolve_aws_credentials`;
  `CircuitBreaker`; the embeddings_use_case bills from `resp_body["usage"]`.

Context (working folder): apps/gateway. Tests: `cd apps/gateway && uv run pytest -p no:cacheprovider --no-cov
-q tests/bedrock_embeddings`. Provider tests use httpx.MockTransport returning a Titan invoke JSON body; assert
the /invoke path (not /converse), SigV4 headers, OpenAI response shape, exact tokens, and N calls for a list input.
CONFIRMED via botocore: bedrock-runtime signingName="bedrock"; InvokeModel http POST /model/{modelId}/invoke;
Titan response {"embedding":[...float],"inputTextTokenCount":int}.

Honors (patterns / conventions):
- `UpstreamProvider` Protocol (ports.py:269): `async post_json(path, payload) -> tuple[int, dict]` returning the
  OpenAI /v1/embeddings shape {object:"list", data:[{object:"embedding",embedding,index}], model, usage:{prompt_tokens,total_tokens}}.
- EXACT TOKENS (v12): Titan returns `inputTextTokenCount` per call → SUM across the batch loop; NO estimation, NO
  separate :countTokens round-trip (simpler than Gemini). usage={prompt_tokens:total, total_tokens:total}.
- NO NATIVE BATCH: Titan invoke embeds ONE inputText per call; a list[str] input → N sequential invoke calls
  (mirror the per-item loop; the existing OpenAI/Gemini adapters batch natively — Titan cannot).
- SIGV4: sign each invoke with service="bedrock" (oracle-confirmed signingName), raw model_id in the path, the SAME
  url to sign_request + client.post, content=body_bytes so the signed x-amz-content-sha256 matches the wire.
- SECRET DISCIPLINE: secret_access_key never logged; Authorization MAC never logged. DEFAULT-OFF: no creds → no
  "bedrock" provider registered → embeddings to a bedrock model is the normal PROVIDER_UNAVAILABLE path.

Anchors the contract cites: `BedrockEmbeddingsProvider`, `post_json`, `_titan_to_openai_embeddings`;
`sign_request` (service="bedrock"); `resolve_aws_credentials`; the Titan InvokeModel request/response shape +
the OpenAI /v1/embeddings response shape; the ProviderRegistry `_providers["bedrock"]` registration.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Amazon Titan embeddings via Bedrock InvokeModel exposed on the OpenAI /v1/embeddings surface, SigV4-signed,
with exact token accounting, registered as the "bedrock" non-chat provider.

Framings weighed: a dedicated BedrockEmbeddingsProvider implementing UpstreamProvider + per-item invoke loop
(chosen — InvokeModel is a different API/port from Converse chat; Titan has no batch so a loop is required; exact
tokens come free from inputTextTokenCount) · fold embeddings into the chat adapter (rejected — different Protocol,
different endpoint, different response shape) · estimate tokens (rejected — Titan returns exact counts).

Must:
<must>
  - post_json("/embeddings", {model, input}) embeds via Titan InvokeModel: for input:str → ONE POST
    /model/{model_id}/invoke with body {"inputText": text}; for input:list[str] → N sequential invoke calls
    (Titan has no native batch), preserving input order.
  - each invoke is SigV4-signed with service="bedrock" (oracle-confirmed signingName), raw model_id in the path
    (same %3A rule as chat), content=body_bytes so the signed payload hash matches the wire.
  - the response is the OpenAI /v1/embeddings shape: {object:"list", data:[{object:"embedding", embedding:<vec>,
    index:<i>}], model:<model_id>, usage:{prompt_tokens:<T>, total_tokens:<T>}} where T = SUM of each call's
    inputTextTokenCount (EXACT — no estimation).
  - main.py registers _providers["bedrock"]=BedrockEmbeddingsProvider(...) iff resolve_aws_credentials(settings);
    absent creds → no "bedrock" provider → byte-identical (PROVIDER_UNAVAILABLE for a bedrock embed model).
  - if payload carries "dimensions" (Titan v2), it is passed through to the invoke body; otherwise omitted.
</must>
Reject:
<reject>
  - an invoke call returning >=400 -> post_json returns (status, {error:{message,type:"bedrock_error",code:status}})
    (mirror the chat error envelope); a list input fails fast on the first error (no partial OpenAI list).
  - a connect error / timeout opening an invoke -> raise UpstreamUnavailableError (design-for-failure: bounded
    timeout + per-instance circuit breaker; gateway → 502).
  - empty/missing "input" -> the embeddings_use_case already rejects ("" / [] / None) BEFORE the adapter; the
    adapter assumes a validated non-empty input (str or non-empty list).
</reject>
After:
<after>
  - an embeddings request to a Titan model returns an OpenAI-compatible embedding with exact token accounting
    billed once; with no creds the provider is absent and behavior is unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ TITAN INVOKE RESPONSE SHAPE — lowest confidence: the exact response keys ({"embedding":[float],
    "inputTextTokenCount":int}) and request key ("inputText"). Mitigation: pin fixtures from the AWS Titan docs;
    the task-6 live double-pass confirms against a real-shaped stub. If wrong: wrong/empty vectors or token count —
    caught by the mapping tests + live verify. Confidence: 0.85.
  - [x] SigV4 service name = "bedrock" (NOT "bedrock-runtime") — CONFIRMED via botocore bedrock-runtime
    signingName="bedrock"; consistent with the chat adapter. Confidence: 0.97.
  - [x] InvokeModel path = POST /model/{modelId}/invoke — CONFIRMED via botocore operation http. Confidence: 0.97.
  - [x] Titan has no batch endpoint → loop N calls for a list input — documented; exact tokens summed. Confidence: 0.9.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: BE1 — single string → one invoke, OpenAI shape, exact tokens
  Given input "hello" and a MockTransport returning {"embedding":[0.1,0.2,0.3],"inputTextTokenCount":8}
  When post_json("/embeddings", {model:"amazon.titan-embed-text-v1", input:"hello"}) runs
  Then (200, {object:"list", data:[{object:"embedding",index:0,embedding:[0.1,0.2,0.3]}], model, usage:{prompt_tokens:8,total_tokens:8}})
  And the request path ends with /model/amazon.titan-embed-text-v1/invoke (not /converse), Authorization AWS4-HMAC-SHA256

Scenario: BE2 — list input → N invoke calls, summed tokens, ordered
  Given input ["a","b","c"], each invoke returns {"embedding":[...],"inputTextTokenCount":3}
  When post_json runs
  Then exactly 3 invoke calls are made; data has 3 entries with index 0,1,2 in order; usage.total_tokens==9

Scenario: BE3 — SigV4 signs with service "bedrock"
  Given a Titan model id with a ':' suffix (v2 "amazon.titan-embed-text-v2:0")
  When post_json runs
  Then the wire path routes to the exact model id (decoded once); the Authorization credential scope contains "/bedrock/aws4_request"; ':' single-encodes to %3A in the canonical URI

Scenario: BE4 — invoke 4xx → OpenAI error envelope (no partial list)
  Given the first invoke returns 400 {"message":"bad","__type":"ValidationException"}
  When post_json runs on a list input
  Then it returns (400, {error:{message,type:"bedrock_error",code:400}}) and makes no further invoke calls

Scenario: BE5 — connect error → UpstreamUnavailableError
  Given the invoke transport raises httpx.ConnectError
  When post_json runs
  Then UpstreamUnavailableError is raised (design-for-failure)

Scenario: BE6 — dimensions passthrough (Titan v2)
  Given payload includes "dimensions": 256
  When post_json runs
  Then the invoke body includes {"inputText":..., "dimensions":256}

Scenario: BE7 — wiring: bedrock embeddings provider registered iff creds
  Given Settings with full bedrock creds
  When create_app runs
  Then app.state.provider_registry.get("bedrock") is a BedrockEmbeddingsProvider
  And with no creds, registry.get("bedrock") is None (absent)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# NEW apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py
class BedrockEmbeddingsProvider:   # implements ports.UpstreamProvider
    def __init__(self, *, credentials: AwsCredentials, region: str, endpoint_url: str | None = None,
                 metrics_registry=None) -> None
        # self._endpoint = (endpoint_url or f"https://bedrock-runtime.{region}.amazonaws.com").rstrip("/")
        # self._client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10))
        # self._breaker = CircuitBreaker(); store region, credentials.
    async def post_json(self, path: str, payload: dict) -> tuple[int, dict]
        # model_id = payload["model"]; inp = payload["input"]; texts = [inp] if isinstance(inp,str) else list(inp)
        # vectors=[]; total_tokens=0
        # for text in texts:
        #   body = {"inputText": text}; if "dimensions" in payload: body["dimensions"]=payload["dimensions"]
        #   body_bytes = json.dumps(body, separators=(",",":")).encode()
        #   url = f"{self._endpoint}/model/{model_id}/invoke"   # raw model_id; same url to sign + post
        #   self._breaker.guard()
        #   try:
        #     sig = sign_request(method="POST", url=url, body=body_bytes, service="bedrock", region, credentials, now=UTC)
        #     resp = await self._client.post(url, content=body_bytes, headers={**sig, "content-type":"application/json", "accept":"application/json"})
        #   except (ConnectError, TimeoutException, NetworkError) as e: self._breaker.on_upstream_error(); raise UpstreamUnavailableError(str(e))
        #   if resp.status_code >= 400: self._breaker.on_upstream_error(); return resp.status_code, _bedrock_error_to_openai(resp.json(), resp.status_code)
        #   self._breaker.record_success()
        #   j = resp.json(); vectors.append(j["embedding"]); total_tokens += int(j.get("inputTextTokenCount", 0))
        # return 200, _titan_to_openai_embeddings(vectors, model_id=model_id, total_tokens=total_tokens)
    async def post_multipart(self, path, files, data) -> tuple[int, dict]   # not supported → raise/ or return 415-style; mirror sibling providers (likely NotImplementedError or a 400 envelope) — see existing adapters
    def stream_bytes(self, path, payload) -> AsyncIterator[bytes]           # not supported for embeddings → raise NotImplementedError

def _titan_to_openai_embeddings(vectors: list[list[float]], *, model_id: str, total_tokens: int) -> dict
    # {"object":"list","data":[{"object":"embedding","index":i,"embedding":v} for i,v in enumerate(vectors)],
    #  "model":model_id, "usage":{"prompt_tokens":total_tokens,"total_tokens":total_tokens}}

# _bedrock_error_to_openai — REUSE the one in bedrock_upstream.py (import it) OR a local copy (decide in build);
#   shape {"error":{"message","type":"bedrock_error","code":status}}.

# MODIFY apps/gateway/src/gateway/main.py — in the existing `if _aws_creds:` region, before ProviderRegistry(_providers):
#   _providers["bedrock"] = BedrockEmbeddingsProvider(credentials=_aws_creds, region=settings.bedrock_region,
#       endpoint_url=settings.bedrock_endpoint_url or None, metrics_registry=app.state.metrics_registry)
```
Schema: no DB/schema change. embeddings_use_case bills from resp_body["usage"] (unchanged seam). A bedrock embed
model needs a catalog row with provider="bedrock", modality="embedding" (seeded operationally, not in this task).

Least-sure flag surfaced at freeze: [contract] the Titan invoke request/response keys ("inputText" /
"embedding" / "inputTextTokenCount"); cost if wrong = empty vectors or zero tokens, caught by task-6 live verify.
The SigV4 service="bedrock" + the /model/{id}/invoke path are NOT the risk — both botocore-confirmed.

Status: FROZEN @ v1 — approved by ADD auto-gate (autonomy:auto; non-security; signing+path oracle-confirmed) · 2026-06-15
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% on bedrock_embeddings.py.
Plan (one test per scenario; provider tests via httpx.MockTransport, wiring via create_app):
<test_plan>
  - test_single_string_invoke (BE1): post_json single str → (200, OpenAI list shape), one /invoke call (not /converse), Authorization AWS4-HMAC-SHA256, usage exact (8).
  - test_list_n_calls_summed (BE2): list ["a","b","c"] → call_count==3, data index 0/1/2 ordered, usage.total_tokens==9.
  - test_sigv4_service_bedrock (BE3): v2 model id with ':' → wire raw_path decoded once == /model/<id>/invoke; Authorization contains "/bedrock/aws4_request"; canonical single-encode %3A (no %253A).
  - test_invoke_4xx_envelope (BE4): first invoke 400 → (400, {error:{...,type:"bedrock_error",code:400}}); no further calls (assert call_count==1 on a 2-item list).
  - test_connect_error_raises (BE5): MockTransport raising ConnectError → UpstreamUnavailableError.
  - test_dimensions_passthrough (BE6): payload dimensions:256 → captured invoke body has {"inputText":...,"dimensions":256}.
  - test_wiring_present_and_absent (BE7): create_app with bedrock creds → provider_registry.get("bedrock") is BedrockEmbeddingsProvider; no creds → None.
</test_plan>

Tests live in: `apps/gateway/tests/bedrock_embeddings/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py` `apps/gateway/src/gateway/main.py`
Strategy (ordered batches): 1. bedrock_embeddings.py (BedrockEmbeddingsProvider + _titan_to_openai_embeddings + post_multipart/stream_bytes stubs mirroring the sibling provider) green BE1-BE6. 2. main.py register _providers["bedrock"] in the existing if _aws_creds: block green BE7.
Safety rule (feature-specific): exact tokens summed from inputTextTokenCount (never estimate); list input fails fast on first invoke error (no partial OpenAI list); design-for-failure (bounded httpx timeout + circuit breaker; connect/timeout → UpstreamUnavailableError); content=body_bytes for hash match; secret/Authorization never logged.
Code lives in: the two declared §5 files.
Constraints: do NOT change any test or the contract; reuse bedrock_sigv4 (service="bedrock") + CircuitBreaker; no new dependency; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 7/7 bedrock_embeddings green (BE1-BE7); regression bedrock_provider + embeddings_endpoint + gemini_embed_tokens 39/39 green; `make test-fast` no-DB floor exit 0
- [x] coverage did not decrease — additive new module + additive registration; no lines removed
- [x] no test or contract was altered during build — `git status` shows only the 2 §5-declared files (bedrock_embeddings.py NEW, main.py M); tests/bedrock_embeddings unchanged since red snapshot
- [x] the green was EARNED, not gamed — adversarial refute-read by orchestrator: fail-fast verified (`return` inside loop on first status>=400, no further invoke calls — BE4 asserts call_count==1); exact tokens summed across N calls (BE2 call_count==3/total_tokens==9, real per-call inputTextTokenCount, not hardcoded); no fixture overfit (loop is data-driven on `texts`); not stubbed away (real httpx.post signed per-call)
- [x] concurrency / timing of the risky operation is safe — per-instance CircuitBreaker.guard()/on_upstream_error()/record_success() mirrors BedrockCompletionUpstream; sequential invoke loop (Titan has no batch); httpx.Timeout(connect=10, read/write=120)
- [x] no exposed secrets, injection openings, or unexpected dependencies — AWS secret_access_key stored private, NEVER logged/echoed/in errors; UpstreamUnavailableError carries only str(httpx exc) (URL has no secret — SigV4 is header-only); content=body_bytes so signed x-amz-content-sha256 matches wire; reuses sign_request/CircuitBreaker/_bedrock_error_to_openai — NO new dependency (no boto3/botocore)
- [x] layering & dependencies follow CONVENTIONS.md — infrastructure adapter implements domain UpstreamProvider Protocol (post_json/post_multipart/stream_bytes); imports only domain.errors + sibling infrastructure; wired in main.py composition root
- [x] a person reviewed and approved the change — auto-gated under `autonomy: auto` (no security finding, no concurrency/architecture residue); additive opt-in adapter, byte-identical when _aws_creds absent

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `BedrockEmbeddingsProvider` referenced in main.py `_providers["bedrock"] = ...` inside `if _aws_creds:` before `ProviderRegistry(_providers)`; resolved at runtime via select_provider(modality=embeddings, provider=bedrock); BE7 asserts isinstance wiring present when creds set, absent when not
- [x] DEAD-CODE (code) — both public symbols used: provider registered in main.py; `_titan_to_openai_embeddings` called by post_json; no orphaned import (pyright 0, ruff clean)
- [x] SEMANTIC (prose / non-code) — n/a (code change)

### GATE RECORD
Outcome: PASS
Reviewed by: auto (autonomy: auto — no security/concurrency/architecture escalation) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
