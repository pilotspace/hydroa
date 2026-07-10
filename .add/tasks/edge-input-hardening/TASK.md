# TASK: S2+S3+S4: XFF last-hop parse, SSRF IMDS deny, body-size cap

slug: edge-input-hardening · created: 2026-07-02 · stage: production · risk: high
autonomy: conservative
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):

**S2 — XFF (3 duplicated, spoofable helpers):**
- `apps/gateway/src/gateway/agent_oauth/api/token_router.py:71-79` — `_resolve_client_ip(request) -> str`. `xff.split(",")[0].strip()` — trusts the **leftmost** (client-supplied, spoofable) token. Feeds `client_ip` → `rate_key = f"token:{client_ip}"` (L141-146).
- `apps/gateway/src/gateway/agent_oauth/api/device_authorize_router.py:62-70` — byte-identical `_resolve_client_ip`. Feeds `limiter.check(ip=client_ip, limit=settings.agent_oauth_authorize_rpm)` (L87-90).
- `apps/gateway/src/gateway/tenants/api/invite_accept_router.py:103-115` — byte-identical `_resolve_client_ip`, docstring literally says "Byte-identical to agent_oauth's device_authorize_router.py::_resolve_client_ip." Feeds `limiter.check(action="preview"/"accept", key=client_ip, ...)` (L141, L184).
- `infra/envoy/envoy.yaml:46,228` · `infra/envoy/envoy-prod.yaml` · `charts/ai-proxy/templates/envoy-configmap.yaml:47,173` — `use_remote_address: true` on every listener's `http_connection_manager`, **no `xff_num_trusted_hops` and no `original_ip_detection` extension set** (defaults to 0 additional trusted hops). Envoy **appends** its own view of the peer address as the LAST XFF token; it does NOT strip client-supplied tokens ahead of it — so the RIGHTMOST token is the one hop we can trust, never the leftmost. Docker-compose (dev + prod, `infra/envoy/*.yaml`) puts Envoy directly internet-facing (no additional LB in the compose topology) → 1 trusted hop. The Helm chart (`charts/ai-proxy/templates/envoy-service.yaml:10`) makes `envoy.service.type` operator-configurable — I could NOT confirm from the chart alone whether a real k8s deployment fronts Envoy with an HTTP(S) LB that would itself append a further XFF hop (flagged ⚠ in §1 Assumptions).
- No existing shared "trusted client IP" helper exists anywhere in `gateway/core/` — this is net-new (there is `gateway/core/config.py:Settings` for the new setting, `gateway/core/errors.py:ProblemError`/`gateway/core/error_catalog.py:ErrorSpec` for error shape precedent).

**S3 — SSRF / credential exfiltration via BYOK Azure `endpoint` + `authority` (the ONLY user-influenced outbound-URL class found; see negative findings below):**
- `apps/gateway/src/gateway/proxy/domain/provider_credentials.py:164-236` — `AzureCredential(BaseModel)`: `endpoint: str` (L179, **required, zero validation** — no `HttpUrl`, no scheme check, no host allowlist) and `authority: str = DEFAULT_AUTHORITY` (L192, same — the AAD **token-minting host**, docstring at L226-233 confirms "the client_secret is POSTed there"). `@model_validator` (L193-206) only checks per-mode field *completeness*, never URL shape/safety.
- `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py` — `PUT /admin/provider-keys/{provider}` (OWNER-only, any tenant). `ProviderKeyPutBody.endpoint: str | None` / `.authority: str | None` (L~90-100) pass straight through to `AzureCredential(**body)` with **no additional guard**. This is the WRITE path: any tenant OWNER can set `endpoint`/`authority` to an arbitrary string today.
- `apps/gateway/src/gateway/proxy/infrastructure/azure_config.py:46-58` — `AzureConfig.build_url(deployment, op)`: `base = self.endpoint.rstrip("/"); return f"{base}/openai/deployments/{quoted}/{op}?api-version=..."` — the tenant-controlled `endpoint` string is concatenated **verbatim** into the URL every adapter dials.
- `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py:74-95,143,181` and `azure_embeddings.py:79-80,159,215,265` — call `cfg.build_url(...)` then `self._client.post(url, ...)` via a plain `httpx.AsyncClient()` (no `follow_redirects=True` set anywhere — confirmed via repo-wide grep, so httpx's safe `follow_redirects=False` default currently holds, but nothing pins it). The request carries the tenant's real `Authorization` header (api_key or AAD bearer) built by `_auth_headers_for_credential` — **the secret is attached before any URL-safety check exists.**
- `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py:42,56,92-93,83-90` — `DEFAULT_AUTHORITY = "https://login.microsoftonline.com"`; `AzureADConfig.authority: str = DEFAULT_AUTHORITY`; `_token_url() -> f"{authority.rstrip('/')}/{tenant_id}/oauth2/v2.0/token"`; `AzureADTokenProviderCache` (constructed in `main.py:873-877`) and `AzureADTokenProvider.__init__` (L68-90) build their own `httpx.AsyncClient` (no `follow_redirects=True`). A tenant-controlled `authority` means the gateway **POSTs the AAD `client_secret` to an attacker-chosen host** — a strictly worse SSRF variant (credential exfiltration, not just a fetch).
- `apps/gateway/src/gateway/proxy/infrastructure/tenant_provider_key_store.py` — the BYOK persistence layer (`upsert`/`get`/`list`/`delete`, L196-360-ish); confirmed **zero URL validation anywhere in the store**.
- `apps/gateway/src/gateway/main.py:870-889` — production wiring: `AzureADTokenProviderCache(...)` (L873) and `AzureCompletionUpstream(token_provider_cache=..., ...)` (L883) are the construction sites where a new `egress_policy` dependency would be injected.
- **Testability constraint (load-bearing):** `apps/gateway/tests/byok_verify/test_byok_verify.py`, `tests/azure_verify/test_azure_verify.py` deliberately point `AzureCredential.endpoint` / `AzureADConfig.authority` at a real local `127.0.0.1:<ephemeral-port>` TCP stub (`test_AV8_localhost_binding`, `test_BV9_localhost_binding` even assert the stub binds `127.0.0.1`) to live-verify the full HTTP path without a real Azure account. Any request-time deny-list that fires unconditionally on loopback WILL break these tests. `tests/provider_config_admin_api/test_provider_config_admin_api.py:82` uses `AZURE_ENDPOINT = "https://my-resource.openai.azure.com"` (a non-resolvable fake hostname) for the admin WRITE-path tests — a write-time check that performs live DNS resolution and fails-closed-on-NXDOMAIN would break this fixture.
- **Negative findings (checked, NOT a gap):** `gemini_upstream.py:220-253` (`_data_url_to_inline`) already hard-rejects any non-`data:` `image_url`/`video_url` with `ValueError("only_data_url_supported")` — the gateway never fetches a caller-supplied remote URL for multimodal content on ANY provider (confirmed: Anthropic/OpenAI/OpenRouter pass `image_url` through to the upstream provider, which does its own fetching — not this gateway's outbound IO). `alerting/application/dispatcher.py:53-60` (`webhook_url`) and `catalog/infrastructure/openrouter_source.py:101` (catalog sync `url`) are both **operator-set** (env var / hardcoded OpenRouter constant), never tenant/request-influenced — out of scope for a user-influenced-URL SSRF task, noted as a spec delta.

**S4 — Body-size caps (no edge cap at all; app cap exists for exactly 3 pre-auth endpoints + 2 unrelated domain caps, NOT for `/v1/*` traffic):**
- `infra/envoy/envoy.yaml`, `envoy-prod.yaml`, `charts/ai-proxy/templates/envoy-configmap.yaml` — grepped for `max_request_bytes`/`per_connection_buffer_limit_bytes`/any `envoy.filters.http.buffer` filter: **none present**. No edge-level cap exists today.
- `apps/gateway/src/gateway/agent_oauth/api/token_router.py:54,120-134` · `device_authorize_router.py:46,98-109` · `device_approval_router.py:57-58,145-157` — the ONLY existing app-level pattern: `_MAX_BODY_BYTES = 4096`, a `Content-Length` pre-check (fail fast) THEN an actual-length fallback check on the read bytes (catches a missing/lying header) → 422. Good pattern to mirror, but hand-duplicated 3x and only applied to these pre-auth endpoints.
- `apps/gateway/src/gateway/proxy/api/router.py:47` (`/v1/chat/completions`, `await request.json()`), `embeddings_router.py:50`, `images_router.py:44` — **unbounded** `await request.json()`, no size guard anywhere upstream of it.
- `apps/gateway/src/gateway/proxy/api/audio_router.py:50,74` (`/v1/audio/transcriptions`, `/v1/audio/translations`, multipart `await request.form()`) — **unbounded** multipart read, no per-file or per-form cap.
- `apps/gateway/src/gateway/core/config.py:605` — `artifact_max_bytes: int = Field(default=10_485_760, ge=0)` (10 MiB) and `:637` `realtime_max_utterance_bytes: int = Field(default=26_214_400, ge=0)` (25 MiB) — existing domain-specific size-cap precedent (artifacts upload, realtime STT utterance) that this task's new caps should sit alongside, numerically and stylistically.
- `apps/gateway/Dockerfile:60-61` — `CMD ["python", "-m", "uvicorn", "gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]` — no body-size-relevant uvicorn flag (uvicorn has no native max-body-size option; the guard has to be app-level).
- `apps/gateway/src/gateway/proxy/api/concurrency_guard.py` (`GlobalBackPressureMiddleware`) and `apps/gateway/src/gateway/main.py:1150-1176` — the exact ASGI-middleware ordering precedent to mirror: `RequestIdMiddleware` added first, then `GlobalBackPressureMiddleware` added AFTER so Starlette's reversed-build-order makes it the OUTERMOST user middleware (runs before auth/routing). A new `BodySizeLimitMiddleware` should register in the same slot (outermost, before back-pressure or after — ordering choice recorded in §3).

Context (working folder): no DB/schema involvement in any of the three fixes — this is entirely config + a handful of new pure-Python/ASGI modules + Envoy YAML deltas. No Alembic migration needed.

Honors (patterns / conventions):
- PROJECT.md invariant: "No outbound IO without timeout + bounded retry (idempotent only) + circuit breaker on OpenRouter" — the new egress-policy DNS resolution is itself outbound IO and MUST carry its own bounded timeout (mirrors `azure_ad.py`'s existing `httpx.Timeout` pattern), independent of any provider adapter's own retry/breaker.
- `.add/personas/appsec-engineer.md` Critical Rules: "SUPERADMIN-shaped guards are checked unconditionally and FIRST... enforced at more than one layer" — the SSRF fix mirrors this exact shape (write-time literal check AND request-time resolved check, neither alone sufficient).
- `core/config.py`'s `_coerce_negative_max_concurrent` (L484-505) — the established "invalid config → coerce to safe default + WARN at startup, never crash" convention this task's new settings (`trusted_proxy_hops`, size caps) should follow.
- `gateway/core/error_catalog.py` — the ONE place every `ProblemError` is constructed from; new error codes (413/422/502) are added here, never raised ad hoc at a call site.
- PROJECT.md "Folded from v1": "Domain ports are `typing.Protocol`s with fakes injected via `app.state`" — the exact shape needed to keep the SSRF guard both fail-closed in production AND unit-testable against the existing `127.0.0.1` live-verify stubs.

Anchors the contract cites: `gateway/core/net.py::resolve_trusted_client_ip` (new) · `token_router.py`/`device_authorize_router.py`/`invite_accept_router.py::_resolve_client_ip` (deleted, replaced by the shared call) · `Settings.trusted_proxy_hops` (new) · `gateway/core/egress_policy.py::EgressPolicy`/`DenyPrivateAndMetadataEgressPolicy`/`AllowAllEgressPolicy` (new) · `AzureCredential.endpoint`/`.authority` (validated at write) · `AzureConfig.build_url`, `azure_ad.py::_token_url` (validated at request time) · `provider_keys_admin_router.py` PUT handler · `error_catalog.py::PROVIDER_ENDPOINT_FORBIDDEN`/`UPSTREAM_EGRESS_DENIED`/`REQUEST_BODY_TOO_LARGE` (new) · `gateway/core/body_size_guard.py::BodySizeLimitMiddleware` (new) · `Settings.max_json_body_bytes`/`max_audio_upload_bytes`/`egress_allow_private_ranges` (new) · `infra/envoy/envoy.yaml`+`envoy-prod.yaml`+`charts/ai-proxy/templates/envoy-configmap.yaml` (buffer filter delta, all three kept in lockstep per the existing `*http_filters`/`*routes` YAML-anchor drift-guard convention already documented in `envoy.yaml`'s own header comments).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Three independent edge/input-hardening fixes bundled under one HARD-STOP security task — (S2) trust only Envoy's own appended XFF hop for every per-IP rate limiter, (S3) deny BYOK Azure `endpoint`/`authority` from ever resolving to a link-local/loopback/private/metadata address at both write time and every request, (S4) bound every request body's size at both Envoy and the app layer.

Framings weighed:
  - **(chosen) Shared primitives, layered enforcement, fail-closed defaults, Protocol-injected test seam** — one `resolve_trusted_client_ip` helper replacing 3 duplicates; one `EgressPolicy` Protocol with a real deny-by-default implementation AND an explicitly-injected fake for the existing `127.0.0.1` live-verify suites (mirrors PROJECT.md's documented Protocol+fake convention); one `BodySizeLimitMiddleware` mirroring `GlobalBackPressureMiddleware`'s ordering. Chosen because it fixes all 3 findings with the smallest, most reviewable surface and reuses proven patterns already in this codebase.
  - Per-call-site patches (fix each of the 3 XFF sites, each Azure adapter, each router independently, no shared symbol) — rejected: this is the EXACT bug class the appsec persona's Critical Rules warn about ("two copies of a rule is a bug waiting for one of them to be edited without the other") — the 3 XFF helpers are ALREADY hand-duplicated once; fixing them independently a second time repeats the mistake.
  - Envoy-only enforcement for all three (rely on Envoy's own XFF/buffer semantics, skip app-layer changes) — rejected: Envoy's `use_remote_address` does NOT retroactively fix the 3 app-level `_resolve_client_ip` bugs (the app re-parses XFF itself), and a single edge-only body cap gives no defense-in-depth if a future Envoy config regresses (mirrors the appsec "enforced at more than one layer" rule). Edge enforcement is ADDED, not substituted.
  - App-only SSRF enforcement at write time only (validate `endpoint`/`authority` once, on PUT) — rejected: does not close DNS rebinding (a domain can resolve to a public IP at write time and a metadata/private IP at request time); request-time re-validation is the authoritative layer, write-time is the cheap first filter.
Must:
<must>
  **S2 — XFF last-hop trust**
  - Behind Envoy, the trusted client IP for EVERY per-IP rate limiter (agent-oauth token issuance, agent-oauth device authorization, invite preview, invite accept) MUST be computed by trusting only the LAST `trusted_proxy_hops` token(s) of `X-Forwarded-For` (default `trusted_proxy_hops=1` → the single rightmost token, the one hop Envoy itself appends) — never the leftmost/client-supplied token.
  - All 4 call sites MUST call ONE shared resolver (`gateway/core/net.py::resolve_trusted_client_ip`) — the 3 existing duplicated `_resolve_client_ip` functions are deleted, not left as dead code.
  - When `X-Forwarded-For` is absent, the resolver MUST fall back to `request.client.host` — byte-identical to today's direct/test-call behavior.
  - When `X-Forwarded-For` has FEWER tokens than `trusted_proxy_hops` (malformed/truncated/tampered), the resolver MUST fail closed to `request.client.host` — it must never trust an earlier, client-supplied hop just because the expected hop is missing.
  - `trusted_proxy_hops` MUST be operator-configurable (`GATEWAY_TRUSTED_PROXY_HOPS`); a configured value `<= 0` MUST be coerced to `1` with a startup WARNING (mirrors `Settings._coerce_negative_max_concurrent`'s established convention).
  - The selected token MUST be validated as a syntactically valid IP address (`ipaddress.ip_address`) before being trusted; an unparseable selected token MUST fail closed to `request.client.host`.

  **S3 — SSRF / IMDS / credential-exfiltration deny for BYOK Azure `endpoint` + `authority`**
  - `PUT /admin/provider-keys/azure` MUST reject (at write time, before persistence) an `endpoint` or `authority` whose scheme is not `https` (or `http` only when `GATEWAY_EGRESS_ALLOW_HTTP_DEV=true`, operator opt-in for local/dev), OR whose host is a LITERAL IP address in a denied range (loopback, link-local incl. `169.254.169.254`/`169.254.170.2`/`fd00:ec2::254`, RFC1918, IPv6 ULA `fc00::/7`, multicast/reserved) unless `GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES=true` — metadata addresses are denied UNCONDITIONALLY, never toggle-able. This check performs NO DNS resolution (string/literal-IP check only) so it stays fast and does not require the hostname to resolve.
  - Independently of the write-time check, EVERY actual outbound dial built from `AzureConfig.build_url` (chat + embeddings + audio adapters) and `azure_ad.py::_token_url` (AAD token mint) MUST re-resolve the target hostname and re-check it against the SAME deny-list, FRESH on every single request — never cached from write time, never skipped because the write-time check already passed once.
  - The tenant's credential (API key header or AAD bearer token) MUST be attached to the outbound request ONLY AFTER the egress check for that request has passed — a denied dial must never carry the secret.
  - DNS resolution failure, timeout, or any resolver error during the request-time check MUST fail closed (deny the dial) — never silently proceed with an unchecked host.
  - Every `httpx.AsyncClient` touching a BYOK-influenced URL (Azure chat/embeddings/audio adapters, the AAD token provider) MUST set `follow_redirects=False` explicitly — closing the redirect-to-IMDS vector by an explicit, tested assertion rather than relying on the library default.
  - The deny-list decision MUST be injectable via a `EgressPolicy` Protocol (`check(host_or_ip) -> None`, raises `EgressDeniedError`) so the existing `127.0.0.1`-stub live-verify test suites (`byok_verify`, `azure_verify`) can inject an explicit `AllowAllEgressPolicy` fake WITHOUT weakening or bypassing the real, deny-by-default production wiring (which always uses `DenyPrivateAndMetadataEgressPolicy` unless a caller EXPLICITLY overrides it).

  **S4 — request body-size caps (edge + app)**
  - Every JSON-body request to `/v1/chat/completions`, `/v1/embeddings`, `/v1/images/*`, and every `/admin/*` write endpoint MUST be rejected with 413 `ERR_REQUEST_BODY_TOO_LARGE` once its body exceeds `GATEWAY_MAX_JSON_BODY_BYTES` (default 20 MiB / 20,971,520 bytes), enforced by ONE shared ASGI middleware (`gateway/core/body_size_guard.py::BodySizeLimitMiddleware`), not per-router duplication.
  - `/v1/audio/transcriptions` and `/v1/audio/translations` (multipart) MUST be rejected with the same 413 shape once the request body exceeds `GATEWAY_MAX_AUDIO_UPLOAD_BYTES` (default 25 MiB / 26,214,400 bytes — matches the existing `realtime_max_utterance_bytes` precedent and OpenAI Whisper's well-known real-world 25 MB file ceiling).
  - The size check MUST catch BOTH (a) a truthful oversized `Content-Length` header — reject BEFORE reading any body bytes — AND (b) a missing or understated `Content-Length` with an actually-oversized streamed body — reject DURING the read, once the running byte count crosses the cap (mirrors the existing `device_authorize_router.py` declared-vs-actual dual-check pattern, generalized into shared middleware).
  - Envoy MUST enforce a coarse OUTER ceiling via the `envoy.filters.http.buffer` HTTP filter (`max_request_bytes`, default 30 MiB / 31,457,280 bytes — comfortably above the largest app-level cap) on every listener (`envoy.yaml`, `envoy-prod.yaml`, `envoy-configmap.yaml`, kept byte-identical across all three per the file's own existing drift-guard convention) — defense-in-depth, not a substitute for the app-level per-route caps.
  - The pre-existing pre-auth 4096-byte caps (`token_router.py`, `device_authorize_router.py`, `device_approval_router.py`) and the pre-existing domain caps (`artifact_max_bytes`, `realtime_max_utterance_bytes`) MUST remain byte-identical — this task adds coverage for the previously-uncapped `/v1/*` JSON + multipart endpoints; it does not touch or re-implement the ones already capped.
  - A legitimate request just under the applicable cap (e.g., a 24 MiB audio file, a multi-MB multimodal chat JSON body with an inline base64 image) MUST be accepted and processed normally — the cap must not be so tight it clips real traffic.
</must>
Reject:
<reject>
  - `PUT /admin/provider-keys/azure` body's `endpoint` or `authority` is a literal loopback/link-local/metadata IP, or a non-`https` scheme without the dev opt-in -> "ERR_PROVIDER_ENDPOINT_FORBIDDEN"
  - A BYOK-influenced outbound dial (Azure chat/embeddings/audio, or an AAD token mint) resolves, AT REQUEST TIME, to a denied address (metadata always; loopback/link-local/private/ULA unless `GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES=true`), or DNS resolution fails/times out -> "ERR_UPSTREAM_EGRESS_DENIED"
  - A `/v1/*` or `/admin/*` request body's declared (`Content-Length`) or actual streamed size exceeds that route's configured cap -> "ERR_REQUEST_BODY_TOO_LARGE"
  - A request body grossly exceeds the Envoy-level outer ceiling -> Envoy's own `envoy.filters.http.buffer` direct response (413, Envoy's native body shape — NOT the gateway's RFC 9457 problem+json; flagged as an accepted inconsistency in the Least-sure flag at §3)
</reject>
After:
<after>
  - Per-IP rate limiting on the agent-oauth token/device-authorize endpoints and the invite preview/accept endpoints is keyed on the REAL client IP as observed by Envoy — an attacker prepending fake `X-Forwarded-For` hops can no longer rotate through synthetic identities to bypass the limiter.
  - No BYOK-configured Azure `endpoint` or `authority` can cause the gateway to dial — or hand a tenant's real secret to — a link-local, loopback, private, or cloud-metadata address, at write time OR at any subsequent request, even if DNS answers differently later (rebinding).
  - Every `/v1/*` and `/admin/*` endpoint has a bounded maximum request-body size enforced at both the Envoy edge and the application layer; a client cannot exhaust gateway memory/bandwidth with an oversized body, and legitimate large-but-bounded payloads (audio uploads, multimodal chat) still succeed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ✅ **k8s trusted-hop count — RESOLVED: ship `trusted_proxy_hops=1` default, operator-tunable** (Tin,
  2026-07-10). Accepted because the cost-if-wrong is a degradation, not a spoof regression: with an
  unaccounted extra LB hop the rate limiter mis-keys by the LB's (non-attacker-controlled) address, and
  the operator retunes `GATEWAY_TRUSTED_PROXY_HOPS` per-deployment with zero code change. No live k8s
  topology audit blocks this freeze.
  ✅ **RFC1918/private-range default-deny — RESOLVED: Tin chose deny-by-default** (2026-07-10,
  AskUserQuestion). `GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES=false` is the frozen secure default; metadata/
  link-local always denied regardless of the flag. An Azure Private Link tenant opts in via the operator
  flag (global for v1). Accepted cost: a Private-Link tenant gets a false-positive 502 until an operator
  flips it. Per-range allowlisting (allow ONE private range without opening all) = documented §7
  spec-delta candidate, not v1 scope.
  - [ ] Full DNS-rebinding closure (resolve-then-CONNECT IP pinning via a custom httpx transport) is explicitly OUT of this task's Must list — the fresh-every-request re-resolution closes the "approved once at write time, exploited via a later rebind" class, but leaves a narrow TOCTOU race between our resolve-check and httpx's own internal resolve+connect on the same call. Documented residual risk + candidate follow-up spec delta (§7), not blocking this freeze — confirm Tin accepts the residual risk at this scope.
  - [ ] The exact byte-size defaults (20 MiB JSON / 25 MiB audio / 30 MiB edge) are reasoned analogically from existing precedent (`artifact_max_bytes`=10 MiB, `realtime_max_utterance_bytes`=25 MiB, OpenAI Whisper's documented 25 MB limit) rather than measured against real production request-size telemetry — confirm they don't clip any current legitimate workload (e.g., a very large tool-definitions + long-history chat request, or a near-cap multi-image multimodal call).
  - [ ] Envoy's native 413 body (plain text / Envoy default) will NOT match the gateway's RFC 9457 problem+json shape for requests that exceed the edge ceiling but would have fit under the app-level cap were they to reach it — accepted as a documented inconsistency (see Reject list) rather than building Envoy-side Lua/local_reply reshaping, which is a materially larger lift for a rarely-hit edge case (only bodies between the app cap and the edge cap ever see the app's own 413; anything under the app cap never reaches Envoy's buffer limit).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# ── S2: XFF last-hop trust ──────────────────────────────────────────────────

Scenario: spoofed XFF chain does not bypass agent-oauth token rate limiting
  Given trusted_proxy_hops=1 and Envoy appends the real peer as the last XFF token
  And an attacker sends X-Forwarded-For: "6.6.6.6, 7.7.7.7, <envoy-appended-real-ip>" rotating the first two tokens per request
  When resolve_trusted_client_ip resolves the client IP for POST /agent-oauth/token
  Then it returns <envoy-appended-real-ip> every time, regardless of the spoofed prefix
  And the per-IP rate limiter keys on that single real IP, so repeated spoofed requests hit the SAME limiter bucket and get 429'd

Scenario: direct/test call with no XFF header falls back to the TCP peer
  Given a request with no X-Forwarded-For header (e.g. a direct test client call)
  When resolve_trusted_client_ip resolves the client IP
  Then it returns request.client.host
  And behavior is byte-identical to the pre-fix direct-call path

Scenario: truncated XFF fails closed rather than trusting an earlier hop
  Given trusted_proxy_hops=2 (operator declares 2 trusted hops)
  And the request's X-Forwarded-For carries only 1 token
  When resolve_trusted_client_ip resolves the client IP
  Then it does NOT trust that single token as the 2nd-from-right hop
  And it falls back to request.client.host
  And no exception is raised (fails closed silently, request proceeds under the safe fallback identity)

Scenario: GATEWAY_TRUSTED_PROXY_HOPS misconfigured to a negative value is coerced, not fatal
  Given GATEWAY_TRUSTED_PROXY_HOPS=-3 at startup
  When Settings is constructed
  Then trusted_proxy_hops is coerced to 1
  And a startup WARNING is logged naming the invalid value
  And the gateway still boots

Scenario: the 3 pre-existing duplicated helpers are gone, one shared resolver remains
  Given the codebase after this task's build
  When grepping token_router.py, device_authorize_router.py, invite_accept_router.py for "_resolve_client_ip"
  Then no per-file duplicate definition exists
  And all 3 call sites import and call gateway.core.net.resolve_trusted_client_ip

# ── S3: SSRF / IMDS / credential-exfiltration deny ──────────────────────────

Scenario: BYOK Azure endpoint pointed at the cloud metadata IP is rejected at write time
  Given an authenticated tenant OWNER
  When they PUT /admin/provider-keys/azure with endpoint="http://169.254.169.254/latest/meta-data/iam/security-credentials/"
  Then the request is rejected 422 ERR_PROVIDER_ENDPOINT_FORBIDDEN
  And no credential row is persisted (upsert never called)
  And the tenant's existing (prior) Azure credential, if any, is left unchanged

Scenario: BYOK Azure authority pointed at an attacker host is rejected at write time (credential-exfiltration vector)
  Given an authenticated tenant OWNER submitting mode="aad"
  When they PUT /admin/provider-keys/azure with authority="https://attacker.example.com" (a syntactically valid https URL)
  Then the write-time literal check does NOT reject it (attacker.example.com is not a literal denied IP — this is the acknowledged write-time coverage gap)
  And the credential IS persisted
  When a subsequent chat request triggers an AAD token mint through that stored authority
  Then the request-time egress check resolves attacker.example.com's current DNS answer and, if that resolves to a private/metadata/loopback address, denies the dial BEFORE the client_secret is ever placed in the POST body, returning 502 ERR_UPSTREAM_EGRESS_DENIED
  And if attacker.example.com resolves to an ordinary public IP, the dial proceeds (this scenario documents that a public-IP-resolving attacker domain that later REBINDS to a private IP is the residual DNS-rebinding risk named in §1's assumptions — the FRESH-every-request re-check bounds it to one request's exposure, not zero)

Scenario: DNS-rebinding — a previously-allowed hostname now resolves to a metadata address
  Given a tenant's Azure endpoint hostname resolved to a public IP when last validated
  And the operator's DNS TTL has since expired and the domain's authoritative answer now returns 169.254.169.254
  When the NEXT chat request dials that endpoint
  Then the request-time egress check re-resolves fresh (not cached) and denies the dial with 502 ERR_UPSTREAM_EGRESS_DENIED
  And the tenant's Azure api_key/AAD token is never attached to the denied request

Scenario: a 3xx response from a BYOK-influenced endpoint is not followed toward IMDS
  Given a tenant's Azure endpoint passes the egress check and the dial proceeds
  And the upstream host responds 302 Location: http://169.254.169.254/latest/meta-data/
  When httpx processes the response
  Then it does NOT follow the redirect (follow_redirects=False, asserted explicitly, not relying on the library default)
  And the 302 response (with the tenant's Authorization header never sent to the redirect target) is returned/mapped as an ordinary upstream response

Scenario: an RFC1918 Azure Private Link endpoint is allowed only when explicitly opted in
  Given GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES=true (operator opt-in for a real Private Link deployment)
  And a tenant's Azure endpoint resolves to 10.20.30.40 (RFC1918)
  When a chat request dials that endpoint
  Then the egress check allows it (private ranges are permitted under the opt-in)
  And a metadata address (169.254.169.254) is STILL denied even with this flag set (metadata is never toggle-able)

Scenario: DNS resolution failure fails closed
  Given a tenant's Azure endpoint hostname no longer resolves (NXDOMAIN) or the resolver times out
  When a chat request attempts to dial it
  Then the egress check denies the dial (502 ERR_UPSTREAM_EGRESS_DENIED), it does NOT proceed with an unchecked/unresolved host

Scenario: existing 127.0.0.1 live-verify test suites are unaffected by the real deny-by-default policy
  Given the byok_verify / azure_verify test suites construct an Azure adapter with an explicitly-injected AllowAllEgressPolicy fake, pointed at their real 127.0.0.1 stub server
  When those tests run in CI
  Then the egress check passes (fake policy, no denial) and the full HTTP round trip against the local stub succeeds exactly as before this task
  And a SEPARATE, new unit test exercises the REAL DenyPrivateAndMetadataEgressPolicy directly (pure function over IP literals — no real network needed) asserting it denies 127.0.0.1, 169.254.169.254, fd00:ec2::254, and 10.0.0.0/8 by default

# ── S4: request body-size caps ───────────────────────────────────────────────

Scenario: oversized JSON body on /v1/chat/completions rejected before parsing, edge case: truthful Content-Length
  Given GATEWAY_MAX_JSON_BODY_BYTES=20971520 (20 MiB)
  And a client sends Content-Length: 25000000 on POST /v1/chat/completions
  When BodySizeLimitMiddleware inspects the request
  Then it rejects immediately with 413 ERR_REQUEST_BODY_TOO_LARGE, BEFORE request.json() is ever called
  And no governance/billing/usage-recording code runs (rejected pre-routing)

Scenario: oversized JSON body with a missing/lying Content-Length caught mid-stream
  Given GATEWAY_MAX_JSON_BODY_BYTES=20971520 (20 MiB)
  And a client sends chunked transfer-encoding with NO Content-Length header, streaming 22 MiB of JSON
  When BodySizeLimitMiddleware reads the body incrementally
  Then it aborts once the running byte count exceeds 20971520, returning 413 ERR_REQUEST_BODY_TOO_LARGE
  And the partial body is discarded, no downstream handler sees it

Scenario: legitimate large audio upload just under the cap succeeds
  Given GATEWAY_MAX_AUDIO_UPLOAD_BYTES=26214400 (25 MiB)
  And a client uploads a real 24 MiB audio file to POST /v1/audio/transcriptions
  When the request is processed
  Then BodySizeLimitMiddleware allows it through (under the cap)
  And TranscriptionUseCase.execute processes the file normally, returning a 200 with the transcript

Scenario: audio upload just over its cap is rejected with the audio-specific limit, not the smaller JSON limit
  Given GATEWAY_MAX_AUDIO_UPLOAD_BYTES=26214400 and GATEWAY_MAX_JSON_BODY_BYTES=20971520
  And a client uploads a 26 MiB audio file to POST /v1/audio/translations
  When BodySizeLimitMiddleware evaluates the request
  Then it applies the 25 MiB AUDIO cap (route-specific), rejecting with 413 ERR_REQUEST_BODY_TOO_LARGE
  And a 21 MiB body on the SAME route would have been allowed (proving the audio cap, not the tighter JSON cap, governs this route)

Scenario: Envoy's coarse edge ceiling rejects a grossly oversized body before it reaches the gateway container
  Given Envoy's envoy.filters.http.buffer filter is configured with max_request_bytes=31457280 (30 MiB)
  And a client sends a 40 MiB body to any /v1/* route
  When Envoy's envoy.filters.http.buffer filter evaluates the request
  Then Envoy itself responds 413 (its own native body, not the gateway's RFC 9457 shape) and the gateway process is never invoked
  And this is a DIFFERENT code path from the app-level 413 above (edge vs. app), both present as defense-in-depth

Scenario: pre-existing pre-auth caps and domain caps are untouched
  Given the codebase after this task's build
  When exercising POST /agent-oauth/token with a >4096-byte body, or an artifact upload over artifact_max_bytes, or a realtime utterance over realtime_max_utterance_bytes
  Then each still rejects at exactly its pre-existing threshold with its pre-existing error code
  And none of their thresholds or error codes changed
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
── Part A: S2 — shared trusted-client-ip resolver ──────────────────────────

gateway/core/net.py  (NEW)
  def resolve_trusted_client_ip(request: Request, trusted_hops: int) -> str
    # Splits X-Forwarded-For on "," (stripped tokens); if absent -> request.client.host
    #   ("unknown" only if request.client is also None, byte-identical to today's helpers).
    # If present: index = len(tokens) - trusted_hops. If index < 0 (fewer tokens than
    #   trusted_hops) OR the selected token fails ipaddress.ip_address() parsing
    #   -> fall back to request.client.host (fail closed, never raises).
    # trusted_hops is read from Settings.trusted_proxy_hops by each call site (not hardcoded).

Settings (apps/gateway/src/gateway/core/config.py)  (NEW field)
  trusted_proxy_hops: int = Field(default=1)   # GATEWAY_TRUSTED_PROXY_HOPS
  @field_validator("trusted_proxy_hops", mode="before")
  def _coerce_nonpositive_trusted_proxy_hops(...) -> object:
    # mirrors _coerce_negative_max_concurrent: v <= 0 -> log WARNING, return 1

Call-site change (3 files, each replaces its local _resolve_client_ip with an import):
  apps/gateway/src/gateway/agent_oauth/api/token_router.py
  apps/gateway/src/gateway/agent_oauth/api/device_authorize_router.py
  apps/gateway/src/gateway/tenants/api/invite_accept_router.py
    client_ip = resolve_trusted_client_ip(request, request.app.state.settings.trusted_proxy_hops)

No new HTTP surface, no new error code — existing 429 RATE_LIMITED path is unchanged; only the
IP fed into it changes.

── Part B: S3 — egress policy for BYOK Azure endpoint/authority ────────────

gateway/core/egress_policy.py  (NEW)
  class EgressDeniedError(Exception): reason: str
  class EgressPolicy(Protocol):
    async def check(self, url: str) -> None   # raises EgressDeniedError; never returns a bool
  class DenyPrivateAndMetadataEgressPolicy(EgressPolicy):
    def __init__(self, *, allow_private_ranges: bool, allow_http: bool,
                 resolve_timeout_s: float, resolver: DnsResolver | None = None) -> None
    async def check(self, url: str) -> None
      # 1. parse scheme; deny if not https (or http when allow_http)
      # 2. resolve host via injected resolver (default: real getaddrinfo wrapped in
      #    asyncio.wait_for(resolve_timeout_s)) -> list[ip]; resolver failure/timeout -> deny
      # 3. for EVERY resolved ip: metadata addrs (169.254.169.254, 169.254.170.2,
      #    fd00:ec2::254) -> ALWAYS deny; loopback/link-local/RFC1918/ULA/multicast/reserved
      #    -> deny unless allow_private_ranges
  class AllowAllEgressPolicy(EgressPolicy):
    async def check(self, url: str) -> None: return   # no-op; TEST-ONLY, never wired by default

  def assert_literal_host_not_denied(url: str) -> None
    # sync, NO DNS: parses scheme + host; if host parses as ipaddress.ip_address literal,
    # apply the same range rules (metadata always denied; private-range per settings) against
    # the LITERAL only. A hostname (non-IP-literal) always passes this check (DNS deferred to
    # the async request-time check). Raises EgressDeniedError.

Settings (NEW fields)
  egress_allow_private_ranges: bool = Field(default=False)   # GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES
  egress_allow_http_dev: bool = Field(default=False)         # GATEWAY_EGRESS_ALLOW_HTTP_DEV
  egress_dns_resolve_timeout_s: float = Field(default=2.0)   # GATEWAY_EGRESS_DNS_RESOLVE_TIMEOUT_S

Write-time wiring — apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py
  In the PUT /admin/provider-keys/azure handler, BEFORE constructing AzureCredential (or
  immediately after, before the store.upsert call): call
    assert_literal_host_not_denied(body.endpoint)
    if body.authority: assert_literal_host_not_denied(body.authority)
  EgressDeniedError -> PROVIDER_ENDPOINT_FORBIDDEN.exc(...) (422), `from None` (mirrors the
  existing PROVIDER_CREDENTIAL_INCOMPLETE from-None convention at this exact call site).

Request-time wiring — new required constructor param, DEFAULTS TO THE REAL POLICY (fail-closed
by default; tests must EXPLICITLY opt out):
  AzureCompletionUpstream.__init__(..., egress_policy: EgressPolicy | None = None)
  AzureOpenAIProvider.__init__(..., egress_policy: EgressPolicy | None = None)
    # apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py:55 — the ONE class
    # covering embeddings + STT (transcriptions/translations) + TTS (speech); confirmed via
    # `grep "^class " azure_embeddings.py` there is no separate per-modality class to wire.
  AzureADTokenProviderCache.__init__(..., egress_policy: EgressPolicy | None = None)
    # None -> constructs DenyPrivateAndMetadataEgressPolicy(settings.egress_allow_private_ranges,
    #         settings.egress_allow_http_dev, settings.egress_dns_resolve_timeout_s) internally.
  Inside build_url call sites (azure_upstream.py:143,181; azure_embeddings.py:159,215,265) and
  azure_ad.py's _acquire (before the POST to _token_url):
    await self._egress_policy.check(url)   # BEFORE _auth_headers_for_credential/token attach
    -> EgressDeniedError -> UPSTREAM_EGRESS_DENIED.exc(...) (502), `from None`
  httpx.AsyncClient(..., follow_redirects=False) added explicitly at every BYOK-influenced
  client construction site (azure_upstream.py:74, azure_embeddings.py:80, azure_ad.py:83).

main.py wiring — 3 construction sites, none pass an egress_policy kwarg (production always takes
the None-default real policy):
  L873  AzureADTokenProviderCache(...)
  L883  _chat_adapters["azure"] = AzureCompletionUpstream(token_provider_cache=..., ...)
  L1008 _providers["azure"] = AzureEmbeddingsProvider(token_provider_cache=..., ...)
        # AzureEmbeddingsProvider is the back-compat alias for AzureOpenAIProvider
        # (azure_embeddings.py:55,299) — the actual class the __init__ change lands on.
        # Shares the SAME _azure_ad_token_provider_cache instance as L883 (one cache, no
        # double AAD minting) — the egress_policy therefore only needs constructing ONCE
        # if wired through the shared cache rather than duplicated per adapter; confirm at
        # build time which shape is cleaner (shared cache carries it vs. each adapter
        # constructs its own default) — implementation detail, not contract-significant.
Tests (byok_verify, azure_verify) pass egress_policy=AllowAllEgressPolicy() explicitly at their
adapter construction sites.

── Part C: S4 — request body-size caps (edge + app) ────────────────────────

gateway/core/body_size_guard.py  (NEW)
  class BodySizeLimitMiddleware:
    def __init__(self, app, *, route_caps: Mapping[str, int], default_cap: int) -> None
    # route_caps: prefix -> max_bytes, longest-prefix-match wins (e.g. "/v1/audio/" -> audio
    #   cap, "/v1/" -> json cap, "/admin/" -> json cap); default_cap applies to anything
    #   unmatched (fail-closed to the smaller JSON cap, never unlimited).
    async def __call__(self, scope, receive, send) -> None
      # 1. Content-Length present + declared > cap -> emit 413 immediately, do not call receive.
      # 2. Otherwise wrap `receive`: accumulate byte count across "http.request" messages;
      #    if running total > cap before "more_body" is False -> emit 413, stop consuming.
      # Registered in main.py create_app() AFTER GlobalBackPressureMiddleware (so it becomes
      # the new OUTERMOST layer — a request too large to even want back-pressure accounting
      # is rejected before that accounting runs).

Settings (NEW fields)
  max_json_body_bytes: int = Field(default=20_971_520, ge=0)     # GATEWAY_MAX_JSON_BODY_BYTES
  max_audio_upload_bytes: int = Field(default=26_214_400, ge=0)  # GATEWAY_MAX_AUDIO_UPLOAD_BYTES

error_catalog.py  (NEW ErrorSpec)
  REQUEST_BODY_TOO_LARGE = ErrorSpec(413, "ERR_REQUEST_BODY_TOO_LARGE",
                                      "Request body exceeds the maximum allowed size")

Envoy — infra/envoy/envoy.yaml, infra/envoy/envoy-prod.yaml,
        charts/ai-proxy/templates/envoy-configmap.yaml (all 3, byte-identical delta, both
        listeners in each file per the existing *http_filters anchor):
  # inserted BEFORE envoy.filters.http.router, position otherwise unconstrained
  - name: envoy.filters.http.buffer
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.filters.http.buffer.v3.Buffer
      max_request_bytes: 31457280   # 30 MiB — literal in the two static compose YAMLs
                                     # (envoy.yaml/envoy-prod.yaml, matching the existing
                                     # literal-numeric convention e.g. local_ratelimit's
                                     # max_tokens: 50); in the Helm chart this is a templated
                                     # value: {{ .Values.envoy.maxRequestBytes | default 31457280 }}
                                     # (add envoy.maxRequestBytes to values.yaml/values-prod.yaml).
                                     # No gateway app-level env var — this is Envoy-only config,
                                     # independent of Settings.max_json_body_bytes/
                                     # max_audio_upload_bytes (the app-level caps).
  # verify step MUST confirm this filter does not interfere with the /v1/realtime and
  # /v1/realtime/relay WebSocket upgrade_configs already present on both listeners
  # (protocol-upgrade requests bypass HTTP body buffering; documented, not yet empirically
  # re-verified post-change — a build/verify checklist item).

── Error catalog additions summary ──────────────────────────────────────────
  422 ERR_PROVIDER_ENDPOINT_FORBIDDEN   -- write-time BYOK endpoint/authority literal denied
  502 ERR_UPSTREAM_EGRESS_DENIED        -- request-time resolved dial denied (or DNS failure)
  413 ERR_REQUEST_BODY_TOO_LARGE        -- app-level body-size cap exceeded (edge-level 413 is
                                            Envoy's own native body, documented inconsistency)

Schema: none — no new table, no new column, no Alembic migration. All three fixes are
config (Settings) + new pure-Python/ASGI modules + Envoy YAML deltas. Access pattern: N/A.
```

Status: FROZEN @ v1 — Tin approved 2026-07-10 (AskUserQuestion). Both least-sure flags RESOLVED as drafted:
- [spec] **k8s trusted-hop count** — RESOLVED: freeze at `trusted_proxy_hops=1`, operator-tunable per
  deployment (cost-if-wrong is a degradation, not a spoof regression). No live k8s topology audit blocks.
- [spec] **RFC1918 default-deny** — RESOLVED: Tin chose deny-by-default
  (`GATEWAY_EGRESS_ALLOW_PRIVATE_RANGES=false`), metadata/link-local always denied, operator opt-in flag
  for Azure Private Link. Per-range opt-in = §7 spec-delta candidate.
- [contract] Part B request-time enforcement (before the outbound POST) is authoritative; write-time
  check is the cheap DNS-free first filter (intentional layering).
- [test] DNS-rebinding IP-pinning NOT built (accepted residual risk → §7 spec-delta).
SECURITY task: the VERIFY gate remains Tin's HARD-STOP (build is authorized by this freeze; verify is NOT).
- [contract] Part B's request-time enforcement point (inside `build_url`/`_token_url` call sites, immediately before the outbound POST) is the authoritative layer; Part B's write-time check (`assert_literal_host_not_denied`) is a cheap, DNS-free, INCOMPLETE first filter (a non-IP hostname always passes it, by design — DNS resolution is deferred to request time). This is intentional layering, not a gap, but a reviewer skimming only the write-time check could mistake it for the full fix — noted here so it isn't missed at review.
- [test] DNS-rebinding closure (resolve-then-CONNECT IP pinning) is explicitly NOT built in this task (§1 assumption) — accepted residual risk, recorded as a candidate follow-up spec delta (§7), not a blocking gap for this freeze.

**Status: FROZEN @ v1 — approved by Tin Dang (2026-07-10).** SECURITY task: this freeze
authorizes BUILD only; the VERIFY gate remained Tin's HARD-STOP (exercised — 4 adversarial
heal rounds, gate PASS approved 2026-07-10). Changing this frozen contract = change request
back to SPECIFY.

Least-sure flag surfaced at freeze: [contract] `allow_private_ranges` is a single GLOBAL toggle,
not per-range — an operator who needs Azure Private Link (RFC1918) relaxes the whole RFC1918/ULA
allow-list at once; a per-range opt-in is deferred to §7. Cost if wrong: an operator relaxes more
than they intend. (Build note: the allow-list is deliberately a POSITIVE list of exactly
RFC1918+ULA, so the toggle can never relax the transition-scheme / metadata classes — that turned
out to be the crux of the whole bypass class.) [test] DNS-rebinding resolve-then-CONNECT IP pinning
is NOT built (accepted residual → §7); request-time FRESH re-resolution narrows the TOCTOU window
but does not fully close it. Cost if wrong: a rebind landing between the check and the socket connect.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/core/net.py`
  `apps/gateway/src/gateway/core/egress_policy.py`
  `apps/gateway/src/gateway/core/body_size_guard.py`
  `apps/gateway/src/gateway/core/config.py`
  `apps/gateway/src/gateway/core/error_catalog.py`
  `apps/gateway/src/gateway/agent_oauth/api/token_router.py`
  `apps/gateway/src/gateway/agent_oauth/api/device_authorize_router.py`
  `apps/gateway/src/gateway/tenants/api/invite_accept_router.py`
  `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py`
  `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py`
  `apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py`
  `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py`
  `apps/gateway/src/gateway/main.py`
  `infra/envoy/envoy.yaml`
  `infra/envoy/envoy-prod.yaml`
  `charts/ai-proxy/templates/envoy-configmap.yaml`
  `apps/gateway/tests/`   (new + updated tests only — never a frozen test weakened)

Strategy (ordered batches):
  1. S4 first (lowest coupling, no security-sensitive DNS/network logic): `body_size_guard.py` +
     Settings fields + `REQUEST_BODY_TOO_LARGE` + main.py middleware wiring + route_caps table.
     Red suite: Content-Length-declared oversize, streamed-oversize, under-cap audio, under-cap
     JSON, pre-existing-cap-untouched regression check.
  2. S2 next (self-contained, no new IO): `core/net.py::resolve_trusted_client_ip` +
     `trusted_proxy_hops` setting + the 3 call-site swaps (delete the 3 duplicated helpers in the
     SAME commit that adds the shared one — never leave both live). Red suite: spoofed-prefix,
     no-XFF fallback, truncated-XFF fail-closed, coercion-and-warn.
  3. S3 last (highest complexity, real IO + the trickiest test-seam): `egress_policy.py` (policy
     classes + literal-check function) first in isolation (pure unit tests, no network), THEN the
     write-time wiring (`provider_keys_admin_router.py`), THEN the request-time wiring (Azure
     adapters + `azure_ad.py` + `main.py` construction sites + `follow_redirects=False`). Update
     `byok_verify`/`azure_verify` test fixtures to inject `AllowAllEgressPolicy()` explicitly in
     the SAME commit that adds the real policy's default — never let the real deny-by-default
     policy land while those suites still construct adapters with no override (they would break).
  4. Envoy YAML deltas last (infra-only, easiest to verify in isolation): add the buffer filter to
     all 3 files identically, then run the existing edge-stack e2e checks (per
     `e2e-edge-stack-ops` memory) including a WebSocket-upgrade smoke check on
     `/v1/realtime`/`/v1/realtime/relay` to confirm the buffer filter doesn't interfere.

Known-problem fixes:
  - Trap: forgetting to delete the 3 duplicated `_resolve_client_ip` functions leaves dead code
    AND a latent re-divergence risk. Fix: delete-and-replace in one commit per batch 2.
  - Trap: wiring the real `DenyPrivateAndMetadataEgressPolicy` as the adapter default WITHOUT
    updating `byok_verify`/`azure_verify` in the same change breaks 2 test suites (they point at
    real `127.0.0.1` stubs). Fix: batch 3's ordering above — policy class first (no consumers
    yet), then BOTH the production wiring AND the test-fixture override land together.
  - Trap: the write-time `assert_literal_host_not_denied` accidentally trying to do DNS
    resolution (would break `AZURE_ENDPOINT = "https://my-resource.openai.azure.com"` in
    `provider_config_admin_api` tests — a non-resolvable fake hostname by design). Fix: keep it
    strictly literal-IP-only, no `socket.getaddrinfo` call in that function.
  - Trap: attaching the tenant's Azure secret to the request BEFORE the request-time egress
    check runs (ordering bug -> secret sent to a denied host even though the response is
    ultimately discarded). Fix: `egress_policy.check(url)` MUST be the first line inside the
    method that builds the request, strictly before `_auth_headers_for_credential`/token attach.
  - Trap: the Envoy buffer filter changing streaming semantics for the WebSocket upgrade routes
    (`/v1/realtime`, `/v1/realtime/relay`) since HTTP buffer filters and protocol upgrades can
    interact unexpectedly. Fix: explicit WS smoke-test in the verify checklist (§6).
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the tenant's real secret (Azure api_key or AAD bearer token) is
  attached to an outbound request ONLY after `egress_policy.check(url)` has returned without
  raising — enforced as a single ordering invariant at every BYOK-influenced call site, not a
  transaction (there is no DB write on this path).
Code lives in: `apps/gateway/src/gateway/` (existing tree; no new bounded context)
Constraints: do NOT change any test or the contract; allow-list packages only (no new third-party
  dependency expected — `ipaddress` and `socket`/`asyncio` DNS resolution are stdlib); ask if
  unclear.

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
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A BYOK Azure URL whose host is a metadata IP (169.254.169.254 / 169.254.170.2 / fd00:ec2::254) is denied at BOTH write-time (`assert_literal_host_not_denied`) AND request-time (`EgressPolicy.check`), regardless of `allow_private_ranges` — confirmed by direct real-code probe + `test_write_time_metadata_ip_denied*` / `test_request_time_denies_metadata_and_private_by_default`.
- [x] Metadata denial holds across EVERY IPv6 encoding of the address (IPv4-mapped, IPv4-compatible, 6to4, NAT64) even under `allow_private_ranges=True` — confirmed by the full adversarial probe matrix (all DENY) + `_MAPPED_METADATA_HOSTS` parametrize (mapped/compat/6to4/NAT64) — the "never toggle-able in any representation" invariant.
- [x] The `allow_private_ranges=True` opt-in relaxes ONLY the RFC1918/ULA positive allow-list; every other private/special range (Teredo 2001::/32, 6to4 2002::/16, discard 100::/64, link-local, reserved, multicast) stays denied, while legitimate Private Link (RFC1918, ULA, canonical IPv4-mapped-RFC1918) and public egress stay allowed — confirmed by `test_write_time_opt_in_relaxes_only_private_class` + probe (zero false-deny).
- [x] A hostname that resolves (DNS) to any denied literal is denied FRESH on every dial (re-resolved, never cached), and any resolver error/timeout/empty-answer fails CLOSED — confirmed by `test_request_time_dns_rebind_denied_fresh_every_call`, `test_request_time_dns_answer_as_mapped_metadata_denied_under_opt_in`, `*_resolver_error_fails_closed`, `*_resolver_timeout_fails_closed`.
- [x] Per-IP identity for rate-limiting uses the RIGHTMOST trusted XFF hop (Envoy's appended peer), never a client-prepended leftmost hop, and combines all XFF header lines; truncated/malformed selection fails closed to `request.client.host` — confirmed by `net.py` re-review + `tests/edge_input_hardening/test_s2_xff_trust.py` (incl. duplicate-header regression).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `DenyPrivateAndMetadataEgressPolicy` built ONCE from settings in `main.py:~879` (`egress_allow_private_ranges` default False, `config.py:~1021`) and injected into all three Azure adapter sites (azure_ad / azure_embeddings / azure_upstream); `resolve_trusted_client_ip` called from the invite + rate-limit paths. Verifier confirmed clean DI, no drift; adapters' no-arg fallback reachable only from direct unit construction.
- [x] DEAD-CODE (code) — no orphaned symbols; `_embedded_ipv4`, `_in_private_link_allowlist`, `_PRIVATE_LINK_ALLOWED`, `_NAT64_WELL_KNOWN`, `_METADATA_IPS` all referenced by `_is_denied_ip`; ruff clean.
- [x] SEMANTIC (prose / non-code) — n/a (code task); module docstring re-read in full and corrected (RFC 8215 local-NAT64 residual now documented honestly, no overclaim).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: 3 independent add-verify agents (a964b59ad42e / a6afc72bc4fb / a39ad9aa3ea4, model sonnet) + self-probe · adversarially checked: real-code SSRF/metadata bypass reproduction at all three enforcement layers (write-time literal, request-time literal, DNS-answer) across the full IPv6 transition-encoding class. Found 4 real bypasses the green suite could not see (IPv4-mapped → NAT64 → Teredo → 6to4-of-RFC1918); each HARD-STOP-healed (41d748e → b9ede95 → ceb5b56 → 831ca49) and re-attacked. Final tree 831ca49: all metadata encodings DENY under opt-in, zero false-deny of legit Private Link/public, new tests confirmed non-vacuous vs pre-heal code. One residual-by-design (operator-configured RFC 8215 local NAT64 in ULA space) documented — not closable by address inspection, needs network-layer enforcement.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-07-10

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-10).** SECURITY task: this freeze)
- [AI] build — strategy used: as planned
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

