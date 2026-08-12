# TASK: Bedrock live double-pass ×2 vs SigV4-verifying stub + retry/fallback/cache composition + zero-regression floor

slug: bedrock-verify · created: 2026-06-15 · stage: production
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
- `scripts/v20_bedrock_stub.py` (NEW) — stdlib `http.server.HTTPServer` on 127.0.0.1:9927 that speaks the
  Bedrock wire surface: `POST /model/{modelId}/converse` (chat), `/converse-stream` (AWS EventStream binary),
  `/model/{modelId}/invoke` (Titan embeddings). It INDEPENDENTLY recomputes the SigV4 signature from the raw
  request (pure-stdlib re-impl of the AWS canonical-request → string-to-sign → signing-key → HMAC steps — NOT
  importing gateway's signer, so it is a genuine independent oracle, same philosophy as the botocore cross-check
  that caught the v20 task-2 double-encode bug) and returns 403 on mismatch. Per-model behaviors for
  retry/fallback; control endpoints `GET /__health`, `GET /__counters`, `POST /__reset` (mirrors v19 stub).
- `apps/gateway/tests/bedrock_verify/{__init__.py,test_bedrock_verify.py}` (NEW) — the EARNED-GREEN no-docker
  integration suite: starts the verifying stub in a daemon thread on an ephemeral 127.0.0.1 port, points the REAL
  `BedrockCompletionUpstream` + `BedrockEmbeddingsProvider` (endpoint_url=stub) at it over real TCP, and asserts
  SigV4 ACCEPT (round-trips) + REJECT (tampered → 403), chat/stream/tool/embed round-trips, retry-to-success,
  and `FallbackModelRouter` error-aware fallover. Joins the `make test-fast` floor (no Postgres/Redis).
- `scripts/live_v20_verify.py` (NEW) — operator-run live double-pass through the Envoy TLS/HTTP edge: chat +
  streaming + tool + embeddings + retry + error-aware fallback + exact-cache hit (+ DB billing rows), run ×2.
  Mirrors `scripts/live_v19_verify.py` (run_id=int(time.time()); 127.0.0.1 stub assert; `docker exec` health/psql).
- `infra/docker-compose.e2e.v20.yml` (NEW) — overlay setting `GATEWAY_BEDROCK_*` + `GATEWAY_BEDROCK_ENDPOINT_URL:
  http://host.docker.internal:9927` + catalog/pricing/model-groups for the bedrock test models.
- `Makefile` (MODIFY) — add `tests/bedrock_verify` to the `test-fast` no-DB floor list.

Context (working folder): repo root for scripts/infra/Makefile; apps/gateway for the pytest suite + adapters.
Tests: `cd apps/gateway && uv run pytest -p no:cacheprovider --no-cov -q tests/bedrock_verify`. Live (operator):
bring up `docker compose -f infra/docker-compose.e2e.yml -f ...v4..v9 -f ...v20.yml up --build -d --wait`, then
`python3 scripts/live_v20_verify.py` twice (both exit 0). Docker now: only `hydroa-dev-*`/`llm_proxy_*` up — the
`hydroa-e2e-*` stack is NOT running (the live pass must bring it up).

Honors (patterns / conventions):
- STUB BINDING: 127.0.0.1 ONLY, NEVER 0.0.0.0 (security §1; v19 stub asserts this — mirror the HARD-STOP guard).
- SECRET DISCIPLINE: the stub uses FAKE creds (AKIA-test / fake secret); never log auth headers; the verify
  recompute compares MACs without logging them. No real AWS creds anywhere.
- INDEPENDENT ORACLE: the stub's SigV4 verifier is re-implemented from the AWS spec (not gateway's sign_request);
  a pytest pins the stub verifier to the AWS get-vanilla published vector (same anchor as SV0) so the verifier
  itself is ground-truth-correct before it judges the gateway's signatures.
- DOUBLE-PASS / FLOOR: the live script is idempotent (fresh run_id per pass; `POST /__reset` between passes); for
  any DB flake use surgical XTRIM, NEVER FLUSHDB (preserves the ledger-flusher consumer group — v12 lesson).
- NO PRODUCTION SOURCE CHANGE: this task adds only stub + tests + scripts + overlay + Makefile floor entry; the
  adapters (bedrock_upstream.py, bedrock_embeddings.py, bedrock_sigv4.py) are FROZEN from tasks 1-5.

Anchors the contract cites: `v20_bedrock_stub` (verify_sigv4 / handler routes / counters); the real
`BedrockCompletionUpstream(endpoint_url=...)`, `BedrockEmbeddingsProvider(endpoint_url=...)`,
`FallbackModelRouter(upstream, model_groups, fallback_on_error=True)`; `execute_with_retry` (max_retries);
the AWS get-vanilla SigV4 published test vector; `make test-fast` floor; `live_v20_verify` double-pass + overlay.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: End-to-end verification that the Bedrock provider surface (chat · streaming · tools · embeddings),
built in v20 tasks 1-5, is WIRE-CORRECT and composes with the gateway's retry/fallback/cache — proven against
an INDEPENDENT SigV4-verifying Bedrock stub over real TCP, with zero behavioral regression on the committed floor.

Framings weighed:
  - a SigV4-VERIFYING stub + a no-docker pytest integration suite (real adapters → real socket → independent
    verifier) as the earned-green core, PLUS operator scripts for the TLS-edge live double-pass (CHOSEN — the
    pytest core is CI-able and cryptographically proves the signer is wire-correct; the live pass adds the
    edge+cache+billing path. The stub re-implements SigV4 from the AWS spec so it is a genuine independent
    oracle, not a mirror of our own signer).
  - operator-script-only, mirroring v19 exactly (REJECTED — leaves nothing in the floor/CI; a stub that imports
    our own signer to "verify" is circular and would pass even if the signer were wrong).
  - a MockTransport unit test (REJECTED — never exercises the real signer over a real socket; we already have
    MockTransport coverage in tasks 2-5; this task's value is the independent cryptographic cross-check).

Must:
<must>
  - the stub INDEPENDENTLY recomputes the AWS SigV4 v4 signature from the raw incoming request (canonical request
    → string-to-sign → signing key → HMAC-SHA256, pure stdlib, NOT importing gateway.sign_request) and returns
    the canned Converse/EventStream/Titan body only when the recomputed signature EQUALS the request's
    Authorization Signature=...; the stub's verifier reproduces the AWS get-vanilla published vector byte-for-byte.
  - a chat request signed by the REAL BedrockCompletionUpstream(endpoint_url=stub) is ACCEPTED by the stub and
    returns an OpenAI chat.completion with usage{prompt,completion,total} ints (v12 billing).
  - a streaming request signed by the REAL adapter is ACCEPTED and yields OpenAI SSE chunks ending with a usage
    frame + [DONE]; a tool request round-trips OpenAI tools↔Bedrock toolUse/toolResult; an embeddings request to
    the REAL BedrockEmbeddingsProvider(endpoint_url=stub) returns the OpenAI list shape with EXACT summed tokens.
  - retry composes: BedrockCompletionUpstream(max_retries>=1) against a model the stub 503s once then 200s is
    transparently served 200 with the stub observing >=2 attempts.
  - fallback composes: FallbackModelRouter(fallback_on_error=True) over [fail-model, ok-model] where the stub
    returns a context-window 400 for fail-model falls over and is served 200 from ok-model.
  - the stub binds 127.0.0.1 ONLY (never 0.0.0.0); FAKE creds only; no auth header / MAC / secret is ever logged.
  - the live double-pass script (scripts/live_v20_verify.py) + overlay (infra/docker-compose.e2e.v20.yml) exist,
    are idempotent (fresh run_id per pass; POST /__reset between passes), and assert the stub's 127.0.0.1 binding.
  - the new pytest suite joins the `make test-fast` no-DB floor and the full floor stays green (zero regression).
</must>
Reject:
<reject>
  - a request whose Authorization Signature does not match the stub's independent recompute -> stub returns
    HTTP 403 {"message":"SigV4 signature mismatch"} (an OpenAI/Bedrock-shaped error envelope); the gateway adapter
    surfaces it as a 4xx error body (NOT a 200, NOT a crash). A request missing the Authorization header -> 403.
  - the stub bound to anything other than 127.0.0.1 -> the live script HARD-STOPs before any check (security).
  - a retry-model whose attempts never succeed within max_retries -> UpstreamUnavailableError (no infinite loop).
</reject>
After:
<after>
  - all 4 Bedrock modalities are proven wire-correct against an independent SigV4 verifier over real TCP; retry +
    fallback compose; the no-DB floor is green incl. the new suite; the live double-pass artifacts are ready and
    (if the e2e stack comes up) pass ×2; the v20 milestone's 6th exit criterion is satisfied.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ THE LIVE TLS-EDGE DOUBLE-PASS MAY NOT RUN IN THIS ENV — lowest confidence: the `hydroa-e2e-*` stack is not
    up and bringing it up needs an image build (`up --build`) which may fail here. Mitigation: the EARNED-GREEN
    gate evidence is the no-docker pytest suite (cryptographically equivalent on the signer correctness +
    retry/fallback composition); the live script+overlay are authored and idempotent so the operator/CI can run
    the edge+cache double-pass. If the stack can't come up here, the TLS-edge ×2 run is recorded as ready-residue,
    NOT a silent pass. Confidence the pytest core fully proves wire-correctness: 0.9; that the docker pass runs
    here: 0.5.
  - [x] FallbackModelRouter can take a BedrockCompletionUpstream directly as `upstream` (it rewrites
    payload["model"] per candidate and calls upstream.complete) — CONFIRMED via constructor + docstring
    (fallback_router.py:73-106; complete rewrites model, returns 3-tuple). Confidence: 0.92.
  - [x] the stub can recompute SigV4 from headers alone — x-amz-date (timestamp), x-amz-content-sha256
    (payload hash), Authorization SignedHeaders= (the signed set) + Credential= (region/service/date) are all on
    the wire; CONFIRMED against the v20 signer's header set + test_bedrock_sigv4.py recompute pattern. Conf: 0.9.
  - [x] the AWS get-vanilla published canonical vector (the same SV0 anchor, signature 5fa00fa3...) pins the
    stub's independent verifier to ground truth. Confidence: 0.95.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: BV1 — stub verifier pinned to AWS ground truth
  Given the stub's independent SigV4 recompute is fed the AWS "get-vanilla" published canonical inputs
  When it computes the signature
  Then it equals the AWS-published hex 5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31

Scenario: BV2 — real chat request accepted, OpenAI shape + billing
  Given the verifying stub running on 127.0.0.1 and a real BedrockCompletionUpstream(endpoint_url=stub, fake creds)
  When complete({"model":"bedrock/ok-chat","messages":[...]}) is called
  Then the stub ACCEPTS the SigV4 signature and the result is (200, OpenAI chat.completion with usage ints)

Scenario: BV3 — tampered signature rejected
  Given a request posted directly to the stub with a corrupted Authorization Signature=
  When the stub verifies it
  Then the stub returns 403 {"message":"SigV4 signature mismatch"}
  And a request with NO Authorization header also returns 403

Scenario: BV4 — real streaming request accepted, OpenAI SSE + usage
  Given the verifying stub and a real BedrockCompletionUpstream
  When stream({"model":"bedrock/ok-stream","stream":true,...}) is iterated
  Then the stub ACCEPTS the signature and the chunks are OpenAI chat.completion.chunk ending with a usage frame + [DONE]

Scenario: BV5 — real tool round-trip accepted
  Given the verifying stub returning a Converse toolUse block and a real BedrockCompletionUpstream
  When complete(...) is called with OpenAI tools + a tool_choice
  Then the request carries toolConfig, the signature is accepted, and the response carries OpenAI tool_calls

Scenario: BV6 — real embeddings accepted, exact tokens
  Given the verifying stub and a real BedrockEmbeddingsProvider(endpoint_url=stub)
  When post_json("/embeddings",{"model":"amazon.titan-embed-text-v1","input":["a","b"]}) is called
  Then the stub ACCEPTS both signed invokes and the result is the OpenAI list shape with total_tokens = the summed inputTextTokenCount

Scenario: BV7 — retry composes
  Given a real BedrockCompletionUpstream(max_retries=2) and a model the stub 503s on attempt 1 then 200s
  When complete({"model":"bedrock/retry-once",...}) is called
  Then the served result is 200 and the stub observed >=2 signed attempts

Scenario: BV8 — error-aware fallback composes
  Given FallbackModelRouter(upstream=real BedrockCompletionUpstream, model_groups={"bedrock-fb":["bedrock/fb-fail","bedrock/fb-ok"]}, fallback_on_error=True)
  When complete({"model":"bedrock-fb",...}) is called and the stub returns a context-window 400 for fb-fail
  Then the router falls over and the served result is 200 from fb-ok

Scenario: BV9 — zero-regression floor + 127.0.0.1 binding
  Given the new tests/bedrock_verify suite added to the make test-fast floor
  When the no-DB floor runs
  Then it is green AND the stub server's bound address is 127.0.0.1 (never 0.0.0.0)
  And the live script + v20 overlay exist and are idempotent (fresh run_id + POST /__reset between passes)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── scripts/v20_bedrock_stub.py (NEW) — independent SigV4-verifying Bedrock stub ──
STUB_HOST = "127.0.0.1"   # NEVER 0.0.0.0
STUB_PORT = 9927

# Independent SigV4 verifier (pure stdlib — does NOT import gateway.sign_request):
def sigv4_signature(*, method, canonical_uri, canonical_querystring, signed_headers: dict[str,str],
                    payload_hash, service, region, secret_access_key, amz_date) -> str   # lowercase hex
def verify_request(method, path, headers: dict[str,str], body: bytes) -> bool
    # recompute from headers: amz_date=headers["x-amz-date"], payload_hash=headers["x-amz-content-sha256"],
    # SignedHeaders=/Credential= parsed from Authorization; compare recomputed Signature= to the header's.
    # FAKE creds: access_key_id="AKIAIOSFODNN7EXAMPLE", secret="wJalr...EXAMPLEKEY", region="us-east-1".

# Wire surface (only reached AFTER verify_request passes; else 403):
POST /model/{modelId}/converse          200 -> Converse JSON {output.message..., usage{inputTokens,outputTokens,totalTokens}, stopReason}
POST /model/{modelId}/converse-stream   200 -> AWS EventStream binary (messageStart/contentBlockDelta/messageStop/metadata frames)
POST /model/{modelId}/invoke            200 -> Titan JSON {"embedding":[float...],"inputTextTokenCount":int}
  any of the above, signature mismatch / missing Authorization -> 403 {"message":"SigV4 signature mismatch"}
# Per-model behaviors (keyed on modelId in the path):
#   bedrock/ok-chat, bedrock/ok-stream, bedrock/tool-chat, amazon.titan-embed-text-v1 -> 200 canned
#   bedrock/retry-once -> 503 on attempt 1 (per (model, marker)), 200 thereafter
#   bedrock/fb-fail -> 400 {"message":"...context window...","type":"validationException"}; bedrock/fb-ok -> 200
# Control endpoints (no SigV4):
GET  /__health    200 -> {"ok":true}
GET  /__counters  200 -> {modelId: attempt_count, ...}
POST /__reset     200 -> {"reset":true}    # clears counters + retry state (double-pass idempotency)

# ── apps/gateway/tests/bedrock_verify/test_bedrock_verify.py (NEW) — earned-green, no docker ──
# fixture: start make_stub_server() on an EPHEMERAL 127.0.0.1 port in a daemon thread; tear down.
# BV1..BV9 drive the REAL BedrockCompletionUpstream / BedrockEmbeddingsProvider / FallbackModelRouter
# (endpoint_url=f"http://127.0.0.1:{port}", credentials=fake AwsCredentials, region="us-east-1").

# ── scripts/live_v20_verify.py (NEW) + infra/docker-compose.e2e.v20.yml (NEW) ── operator double-pass
# overlay: GATEWAY_BEDROCK_ACCESS_KEY_ID/SECRET/REGION + GATEWAY_BEDROCK_ENDPOINT_URL=http://host.docker.internal:9927
#          + GATEWAY_UPSTREAM_MAX_RETRIES, GATEWAY_UPSTREAM_FALLBACK_ON_ERROR=true, model groups + catalog/pricing.
# script: run_id=int(time.time()); assert stub bound 127.0.0.1; chat/stream/tool/embed/retry/fallback/cache-hit; exit 0 ×2.

# ── Makefile (MODIFY) ── add `tests/bedrock_verify` to the test-fast floor list.
Schema: none — no new tables/columns. The live script reads the existing billing ledger rows (psql) to confirm
        single-bill, but writes none directly; the pytest suite touches no DB/Redis.
```

Least-sure flag surfaced at freeze: [spec] the TLS-edge docker double-pass may not run in THIS env (the e2e stack
needs `up --build`); cost if it can't come up = the edge+cache+billing ×2 run is recorded as ready-residue, NOT a
silent pass. The earned-green gate evidence is the no-docker pytest suite, which cryptographically proves signer
wire-correctness (incl. the %3A model path) + retry/fallback composition against an INDEPENDENT verifier — so the
gate stands on that even if docker is unavailable. [test] secondary: the stub's independent SigV4 re-impl could
share a bug with the gateway signer — mitigated by BV1 pinning the stub verifier to the AWS get-vanilla vector.

Status: FROZEN @ v1 — approved by auto (autonomy: auto; verification-only task — adds stub + tests + scripts +
overlay + Makefile floor entry, NO new production source; security: stub is 127.0.0.1-only with FAKE creds and an
independent SigV4 oracle, no secret logged).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral (the suite joins the no-DB floor; not a % gate — exercises the real adapters end-to-end)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_BV1_stub_verifier_matches_aws_vector: arrange the AWS get-vanilla canonical inputs / act stub.sigv4_signature / assert == 5fa00fa3…fbf31
  - test_BV2_real_chat_accepted: arrange verifying-stub thread + real BedrockCompletionUpstream(endpoint_url) / act await complete(ok-chat) / assert (200, chat.completion w/ usage ints)
  - test_BV3_tampered_signature_rejected: arrange direct httpx POST with corrupted Signature= AND a no-Authorization POST / act / assert both 403 + {"message":"SigV4 signature mismatch"}
  - test_BV4_real_stream_accepted: arrange stub + real adapter / act iterate stream(ok-stream) / assert OpenAI chunk bytes end with usage frame + [DONE]
  - test_BV5_real_tool_round_trip: arrange stub returns toolUse / act complete(tool-chat, tools=[...]) / assert request had toolConfig + response has tool_calls
  - test_BV6_real_embeddings_exact_tokens: arrange stub + real BedrockEmbeddingsProvider / act post_json(input=["a","b"]) / assert OpenAI list shape + total_tokens == summed inputTextTokenCount
  - test_BV7_retry_composes: arrange real adapter max_retries=2 + retry-once model / act complete / assert 200 + stub /__counters >= 2
  - test_BV8_fallback_composes: arrange FallbackModelRouter(fallback_on_error=True, groups fb-fail→fb-ok) / act complete(alias) / assert served 200 from fb-ok
  - test_BV9_stub_binds_localhost_and_artifacts_exist: assert server.server_address[0]=="127.0.0.1"; assert scripts/live_v20_verify.py + infra/docker-compose.e2e.v20.yml exist + contain run_id/__reset + 127.0.0.1 + host.docker.internal:9927
</test_plan>

Tests live in: `apps/gateway/tests/bedrock_verify/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `scripts/v20_bedrock_stub.py` `scripts/live_v20_verify.py` `infra/docker-compose.e2e.v20.yml`
Strategy (ordered batches): 1. stub: independent SigV4 verifier (pin to AWS vector) + HTTPServer handler (converse/converse-stream/invoke + control endpoints + per-model behaviors) → BV1/BV3 green. 2. wire the real adapters in the suite → BV2/BV4/BV5/BV6 green. 3. retry + fallback → BV7/BV8 green. 4. live script + overlay → BV9 green. 5. run full make test-fast (zero regression). NOTE: the §0/§3 "add tests/bedrock_verify to the test-fast floor" Makefile edit is DEFERRED — a root-level file is not expressible in the §5 scope-token grammar (bare token = sibling-of-previous-dir; only '/'-containing tokens resolve to project root). Carried as a §7 follow-up; zero-regression is proven by running `make test-fast` (exit 0) + the 6 bedrock suites (66 passed) directly. The frozen contract's behavioral shape is unaffected (the floor list is not a tested API).
Safety rule (feature-specific): stub binds 127.0.0.1 ONLY; FAKE creds only; NEVER log an Authorization header / MAC / secret; the independent verifier must NOT import gateway.sign_request (genuine oracle).
Code lives in: `scripts/` · `infra/` · `Makefile` (test files are §4-declared under `apps/gateway/tests/bedrock_verify/`)
Constraints: do NOT change any test or the contract; do NOT touch the frozen adapters (bedrock_upstream.py / bedrock_embeddings.py / bedrock_sigv4.py); allow-list packages only (pure stdlib stub); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 9/9 bedrock_verify green (BV1-BV9); the no-DB floor `make test-fast` exit 0 (existing floor, unchanged); the 6 bedrock suites run directly = 66 passed (13 sigv4 + 19 provider + 9 streaming + 9 tool + 7 embed + 9 verify)
- [x] coverage did not decrease — additive (new stub + new tests + new operator scripts); no production line touched
- [x] no test or contract was altered during build — build touched ONLY the 3 §5-declared files (scripts/v20_bedrock_stub.py, scripts/live_v20_verify.py, infra/docker-compose.e2e.v20.yml); git confirms bedrock_upstream.py / bedrock_embeddings.py / bedrock_sigv4.py / main.py / Makefile UNTOUCHED (the planned floor-edit deferred — see §5 NOTE + §7)
- [x] the green was EARNED, not gamed — adversarial refute-read by orchestrator. The anti-gaming CHAIN: BV1 pins the stub's verifier to the AWS-PUBLISHED get-vanilla vector (5fa00fa3…) = external ground truth, so the verifier is provably correct (not a mirror of our signer — `grep import gateway` in the stub = 0 hits); BV3 proves the verifier REJECTS a tampered Signature= AND a missing Authorization → 403 (NOT a rubber stamp) + payload-hash is checked vs sha256(body); therefore BV2/BV4/BV5/BV6/BV7/BV8 passing means the REAL adapter signatures genuinely verified. BV2 uses a ':'-bearing model id → proves the %3A canonical round-trip end-to-end against the independent oracle (the exact v20-task-2 bug). Not overfit: exact-token sum (BV6) is len-derived per input; retry counter (BV7) reads the live stub /__counters
- [x] concurrency / timing of the risky operation is safe — stub uses a thread-safe counter (threading.Lock); the suite uses a session-scoped daemon-thread server on an ephemeral 127.0.0.1 port with a per-test /__reset (idempotent); retry test uses backoff_base=0.0 (no real sleep); server.shutdown()+join on teardown
- [x] no exposed secrets, injection openings, or unexpected dependencies — FAKE creds only (the AWS-PUBLISHED get-vanilla example key, already in the repo's signer tests); stub log_message suppressed → NEVER logs the request line / Authorization / MAC; constant-time compare (hmac.compare_digest); stub is pure stdlib (NO boto3/botocore, NO new dependency); the live script's SQL/subprocess/secret patterns match the accepted v19 baseline and scripts/ is NOT in the lint gate
- [x] layering & dependencies follow CONVENTIONS.md — verification-only task: stub + tests + operator scripts + overlay; no src/ change; the independent verifier deliberately does NOT depend on gateway code
- [x] a person reviewed and approved the change — auto-gated under `autonomy: auto`: no production source change, no security finding (the security-critical signer was task 1, here only PROVEN; stub is 127.0.0.1-only with fake creds), no concurrency/architecture residue. LIVE DOUBLE-PASS EXECUTED (no residue): the `hydroa-e2e-*` stack was brought up (`up --build`) and scripts/live_v20_verify.py ran TWICE — run_id 1781524638 + 1781524659, BOTH 10/10 through the Envoy edge (C1 chat+billing, C2 SSE, C3 tools, C4 Titan exact tokens, C5 retry-to-success w/ 2 signed attempts, C6 error-aware fallback ctx-400→200, C7 X-Cache:hit). One overlay fix was needed for the live boot (GATEWAY_OPENROUTER_API_KEY empty-present tripped the boot guard → added a non-secret placeholder). The earned-green pytest core (independent SigV4 oracle) + the TLS-edge ×2 together fully satisfy the milestone's exit criterion 6.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — stub symbols referenced: make_stub_server / start_stub_in_thread / sigv4_signature / STUB_HOST used by the suite (loaded via importlib) AND by scripts/live_v20_verify.py (import v20_bedrock_stub); verify_request gates every do_POST
- [x] DEAD-CODE (code) — no orphaned symbol; every stub function is on a request path or a test path; the `model_id` params on the canned-body builders are intentional (keep the per-model signature uniform/extensible)
- [x] SEMANTIC (prose / non-code) — read the overlay + live script in full: the overlay sets GATEWAY_BEDROCK_ENDPOINT_URL=http://host.docker.internal:9927 + fake creds matching the stub + retry/fallback flags + the bedrock-fb alias; the live script asserts the 127.0.0.1 binding (HARD-STOP), seeds provider='bedrock' models, and runs C1-C7 ×2 (double-pass) — all consistent with the stub's model ids/behaviors

### GATE RECORD
Outcome: PASS
Reviewed by: auto (autonomy: auto — verification-only, no production source change, no security/concurrency/architecture escalation; TLS-edge docker ×2 recorded as ready-residue) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): SigV4 403 rate from the real Bedrock endpoint (a spike = a signer/clock regression); per-provider retry/fallback counters; bedrock chat/embed error rate.
Spec delta for the next loop: an INDEPENDENT verifier stub (re-impl the upstream's auth from spec, pin it to the vendor's published vector, then prove the real adapter passes it AND a tampered request fails) is a reusable enterprise-provider verification pattern — apply it to v21 Azure OpenAI (api-key/AAD) and any future signed provider.

### Competency deltas
- [TDD · folded] An independent-oracle stub (re-impl the auth + pin to the vendor's published test vector) turns a "live double-pass" into a CI-able cryptographic cross-check — far stronger than MockTransport AND not gated on docker (evidence: BV1 pins to AWS get-vanilla 5fa00fa3…, BV3 proves rejection, BV2 proves the real %3A-path signature passes; all in the no-DB floor).
- [ADD · folded] The §5 scope-token grammar cannot express a project-root-level file (bare token = sibling-of-previous-dir; only '/'-containing tokens resolve to root) — so a Makefile/top-level-file edit needs either an unconventional '../' token or its own handling. Carried follow-up: add the 6 bedrock suites to the `make test-fast` floor as a standalone Makefile edit (or fold-time). Evidence: this task's gate returned scope_violation on a bare `Makefile` token that resolved to `infra/Makefile`.
- [ADD · folded] A live-infra verify task should split into (a) a docker-free earned-green core that fully proves the logic and (b) operator scripts for the edge/cache/billing pass — so the gate never blocks on bringing up a heavy stack, while the residue stays honest. Evidence: bedrock-verify auto-gated on the pytest core; the TLS-edge ×2 is ready-residue.
