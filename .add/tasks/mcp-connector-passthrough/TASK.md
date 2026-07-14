# TASK: MCP connector egress passthrough with per-tenant allow/deny lists (fail-closed) + session tracing

slug: mcp-connector-passthrough · created: 2026-07-14 · stage: production · sensitivity: security
milestone: agent-gateway-v1
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/core/egress_policy.py:EgressPolicy` (Protocol) · `DenyPrivateAndMetadataEgressPolicy.check` · `AllowAllEgressPolicy` (test-only) · `assert_literal_host_not_denied` · `EgressDeniedError` — the SSRF/IMDS guard this task REUSES verbatim for every MCP-server dial: a synchronous DNS-free literal-IP check at allow-list WRITE time, plus a fresh async DNS-resolving check on every single dial (never cached). S2-4 edge-input-hardening precedent, hardened across 4 adversarial rounds (IPv4-mapped→NAT64→Teredo→6to4).
- `apps/gateway/src/gateway/proxy/infrastructure/composite_key_authenticator.py:CompositeKeyAuthenticator.authenticate` — the unified sk-/agent-token authn dispatch already producing ONE `AuthzResult` for both credential classes (frozen @ agent-token-authn-seam v39); `/v1/mcp/call` reuses this AS-IS.
- `apps/gateway/src/gateway/keys/domain/entities.py:AuthzResult` — the per-request identity+governance bag (`tenant_id`, `key_id`, `model_allowlist`, guardrail fields) resolved ONCE at key-auth time via a LEFT JOIN; this task adds `mcp_allowed_servers: list[str] | None` the SAME additive way `model_allowlist`/`cache_enabled` were added — "Governance fields added additively (all default to None)" per the class's own docstring.
- `apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py:RegexGuardrailEvaluator.evaluate_pre` (public Protocol method, `proxy/domain/ports.py:GuardrailEvaluator`) + its `mask_pii_in_messages` sibling — reused, NOT reimplemented, to (a) scan MCP tool-call RESULT content for the SAME 7 prompt-injection pattern families before relay to the caller, and (b) PII-scrub tool-call args/results before the session-trace write.
- `apps/gateway/src/gateway/logs/infrastructure/sqlalchemy_capture.py:SqlAlchemyPayloadCapture.capture` (+ `apps/gateway/src/gateway/logs/application/capture_writer.py:persist_request_log`) — the FROZEN v1 (payload-capture-store TASK.md §3) session-tracing seam this task calls verbatim: ZDR live-check (fail-closed) + non-blocking bounded-concurrency admission + scrub/truncate/timeout, NEVER raises. MCP tool-call traces are ordinary `request_logs` rows — no new table, no signature change to a frozen port — via `model=f"mcp::{server_host}::{tool_name}"`.
- `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` + `apps/gateway/src/gateway/audit/domain/audit_event.py:AuditEvent` — fire-and-forget, fail-open audit emit reused verbatim for every allow-list CRUD write AND every fail-closed refusal; `AuditEvent.actor_key_id` (realtime-relay-governance precedent) already covers an agent-token-authenticated actor — no new actor field needed.
- `apps/gateway/src/gateway/tenants/api/residency_policy_router.py` (tenant-level idiom: OWNER-only via `Permission.SECURITY_CONFIG`, operates only on `identity.tenant_id`, fire-and-forget audit) + `apps/gateway/src/gateway/keys/api/key_guardrail_router.py` (key-level idiom: `require_owner_or_admin`, key>tenant precedence resolved server-side, 404-collapses unknown/cross-tenant/revoked key, race-safe `UPDATE ... RETURNING id`) — the two admin-CRUD shapes `/admin/mcp-servers` and `/admin/keys/{key_id}/mcp-servers` mirror exactly.
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` + `apps/gateway/src/gateway/core/errors.py:ProblemError/problem_response` (RFC 9457 `application/problem+json`) — every new `ERR_MCP_*` code renders through this, never a raw dict.
- `apps/gateway/migrations/versions/d401ca5a7cde_per_key_guardrail_policies.py` (nullable-JSONB key-override column, NULL=inherit) — the DDL idiom `api_keys.mcp_allowed_servers_override` mirrors.
- `apps/gateway/src/gateway/core/config.py:Settings` — timeout-knob naming convention (`<name>_timeout_seconds: float = Field(default=X, gt=0)`, `GATEWAY_<NAME>` env var) this task's new `mcp_connector_dial_timeout_seconds` follows.

Context (working folder):
- `apps/gateway/pyproject.toml` already vendors `httpx>=0.28`; no MCP SDK dependency exists. This task builds the streamable-HTTP passthrough directly on httpx with `follow_redirects` explicitly disabled — no third-party MCP client library added (keeps the allow-list model transport-agnostic against the 2026-05-21 RC, per the roadmap's own stated risk).
- Repo-wide search confirms NO prior MCP code anywhere in `apps/gateway/src/gateway/` — this is a genuinely new subsystem (module `gateway/mcp_connector/`), not an extension of one.
- The MCP RC's six hardened SEPs (iss validation, DCR application_type, credential-binding-to-issuer) govern the CLIENT-TO-MCP-SERVER OAuth leg — a DIFFERENT leg from the AGENT-TO-HYDROA auth this task reuses unchanged. Framing decision (§1): Hydroa is a pure reverse-proxy passthrough for MCP-server credentials — it forwards whatever auth header the calling agent already attaches for the upstream server; it never brokers or stores a per-tenant MCP-server credential (no BYOK-style store for MCP — matches milestone's "no MCP server marketplace/directory").

Honors (patterns / conventions):
- Fail-closed everywhere (MILESTONE.md shared decision): every refusal is a structured problem+json 4xx emitted BEFORE any upstream dial — mirrors residency's refuse-not-reroute idiom; "Fail-closed MCP default" is itself a named MILESTONE.md shared decision.
- Tenant scoping: every allow-list read/write operates ONLY on `identity.tenant_id`/`identity.key_id` from the authenticated caller — never a client-supplied tenant/key id (residency-policy precedent; no cross-tenant leak surface).
- One billing path / no parallel ledger (MILESTONE.md shared decision): this task emits the tool-call hook `tool-call-metering` bills through; it never writes a `usage_records` row itself.
- Design-for-failure IO rule (CLAUDE.md + PROJECT.md): every outbound MCP dial is `asyncio.wait_for`-bounded, gated by a per-(tenant_id, server-host) circuit breaker (new dimension key on the existing `proxy/infrastructure/circuit_breaker.py` pattern), and is NEVER auto-retried (a tool call is presumed to have side effects; blind retry would double-execute — deliberately NOT extending the `retry-policy` task's idempotent-read retry to this surface).
- Agent principal rides device-OAuth, no new credential class (MILESTONE.md shared decision): `/v1/mcp/call` authenticates via the SAME `CompositeKeyAuthenticator` chat already uses.

Seams consulted: none — first task in this subsystem; no `.add/SEAMS.md` entry pre-exists for MCP.

Anchors the contract cites:
- `egress_policy.py: EgressPolicy, DenyPrivateAndMetadataEgressPolicy, assert_literal_host_not_denied, EgressDeniedError`
- `composite_key_authenticator.py: CompositeKeyAuthenticator.authenticate`
- `keys/domain/entities.py: AuthzResult`
- `guardrail_evaluator.py: RegexGuardrailEvaluator.evaluate_pre` (via `proxy/domain/ports.py: GuardrailEvaluator`)
- `sqlalchemy_capture.py: SqlAlchemyPayloadCapture.capture`
- `audit_writer.py: record_audit` · `audit_event.py: AuditEvent`
- `error_catalog.py: ErrorSpec` · `errors.py: ProblemError`
- `tenants/domain/authz.py: Permission.SECURITY_CONFIG, require_permission, require_owner_or_admin`

Issues/Risks (→ feed §1):
1. **DNS-rebind TOCTOU**: `EgressPolicy.check()` resolves+validates the hostname, but the ACTUAL httpx dial performs its OWN independent DNS resolution microseconds later — an attacker-controlled DNS answer can flip between the two lookups. The existing S2-4 hardening was proven for a FIXED, enterprise-owned Azure endpoint (low attacker-influence); MCP server URLs are tenant/agent-supplied — a materially higher-value rebind target. Must pin the IP `check()` validated and force the actual dial onto that literal IP (no second resolution).
2. **Redirect-based allow-list bypass**: an allow-listed `https://good.example.com` could 3xx-redirect to `http://169.254.169.254/`; httpx follows redirects by default. Must disable auto-follow and treat any 3xx as terminal.
3. **Tool-result reinjection**: an MCP tool-call RESULT becomes part of the NEXT prompt turn in the calling agent's context — a compromised MCP server can inject "ignore previous instructions"-shaped content exactly as a chat message can. Must reuse the EXISTING `prompt_injection` guardrail (not a new guardrail type) against tool-result content before relay.
4. **Credential passthrough leakage**: the gateway forwards the agent's own upstream-MCP-server credential verbatim (pure passthrough) — this header/token must NEVER land in the session-trace row or audit-event metadata (concretizes `audit_event.py`'s existing "metadata is opaque, callers must not write secrets" invariant for a NEW secret class).
5. **Policy-store outage / fail-open risk**: the effective allow-list resolves inside the SAME LEFT JOIN as `guardrail_configs`/`model_allowlist` at key-auth time; any JSONB-parse or resolution failure MUST collapse to an EMPTY list (deny-all) — matches the DNS-failure-fails-closed idiom already in `egress_policy.py`; never "allow", never a stale cached list (resolved fresh every auth call, same as `guardrail_configs` today).
6. **Idempotency**: MCP tool calls are presumed to have side effects; the RC does not yet standardize a per-tool idempotency declaration — the gateway therefore never retries a tool-call dial.

Related intent: MILESTONE.md `agent-gateway-v1` §Scope ("MCP connector egress passthrough with per-tenant allow/deny lists (fail-closed) + session tracing") and §Shared decisions ("Fail-closed MCP default", "Security tasks get TWO independent adversarial verifies", glossary delta "MCP allow-list"); roadmap `docs/roadmap/2026-07-14-enterprise-roadmap.html` R1 M3 ("no vendor owns all five MCP governance primitives" — allow/deny lists, session tracing, agent rate limits, tool metering, session governance; "MCP spec churn... build against the RC, keep the allow-list model transport-agnostic").

Ground SHA: c948576

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: MCP connector egress passthrough with per-tenant/per-key allow/deny lists (fail-closed) + PII-scrubbed session tracing
Framings weighed: pure reverse-proxy passthrough (chosen) — Hydroa forwards the agent's own MCP-server credentials verbatim, brokers/stores nothing, and adds ONLY allow/deny-list governance + fail-closed refusal + tracing on top · gateway-managed OAuth broker per MCP server (rejected — no BYOK-style MCP credential store is in milestone scope; large surface that can be layered on later without reworking this contract) · allow only anonymous/no-auth MCP servers (rejected — real enterprise MCP servers require auth; would make the feature non-viable for the enterprise buyer this milestone targets)

Must:
<must>
  - M1 A tenant OWNER can `PUT /admin/mcp-servers` a tenant-wide allow-list (`{url, label}` entries, replace-wholesale); `GET /admin/mcp-servers` is available to any authenticated tenant role.
  - M2 An owner/admin can `PUT`/`DELETE /admin/keys/{key_id}/mcp-servers` a PER-KEY override (replace-wholesale, `null` clears back to tenant inheritance); `GET` is available to any authenticated tenant role and reports `source: "key"|"tenant"`. Precedence is key > tenant > default-deny — an explicit EMPTY key override means "this key may call NO server" (never silently reinterpreted as "inherit tenant").
  - M3 Every server URL accepted into either allow-list passes the SAME write-time literal-IP/scheme check `assert_literal_host_not_denied` performs for BYOK Azure endpoints (S2-4 precedent): https-only, no literal metadata/private/loopback/link-local IP host. A non-literal hostname always passes this check (DNS resolution deferred to dial time).
  - M4 `POST /v1/mcp/call` resolves the caller's EFFECTIVE allow-list (key override else tenant list else empty) from the SAME `AuthzResult` populated at key-authentication time — zero extra DB round trip beyond the existing LEFT JOIN, zero caching beyond that per-request resolution. Any caller-supplied `tenant_id`/`key_id`-shaped field in the request body is ignored outright — resolution is ALWAYS keyed off the authenticated identity, never a body field.
  - M5 A call whose `server_url` is not a case-sensitive exact member of the effective allow-list is refused with a structured 403 problem+json BEFORE any DNS lookup or socket connect — zero egress dials — and a fire-and-forget audit event is recorded.
  - M6 A call whose `server_url` passes the allow-list is THEN checked, fresh, by `DenyPrivateAndMetadataEgressPolicy.check()` (never cached, never `AllowAllEgressPolicy` in production) before the real dial; the IP that check validates is the SAME IP the actual TCP connection uses — no independent second DNS resolution — closing the DNS-rebind TOCTOU gap the BYOK case left open (Issue 1).
  - M7 The MCP HTTP client never auto-follows a redirect (`follow_redirects=False`); any 3xx response from the upstream MCP server terminates the call as a refusal, never a silent re-dial to `Location`.
  - M8 The outbound dial is bounded by `mcp_connector_dial_timeout_seconds` (default 30s), gated by a per-(tenant_id, server-host) circuit breaker, and is NEVER automatically retried by the gateway.
  - M9 Every MCP tool-call RESULT's text content is scanned by the tenant/key's EXISTING `prompt_injection` guardrail (`GuardrailEvaluator.evaluate_pre`, unchanged config surface) before relay to the caller; a block-mode match replaces the result with a synthetic JSON-RPC error object (never the raw content) and is recorded `blocked` on both the session-trace row and an audit event. Audit/pass-through mode never mutates the relayed content (mirrors existing guardrail mode semantics).
  - M10 Every `/v1/mcp/call` outcome (dialed, refused, or blocked) writes exactly one `request_logs` row via the EXISTING `SqlAlchemyPayloadCapture.capture()` seam (ZDR-gated, PII-scrubbed via `mask_pii_in_messages`, fire-and-forget, never blocks the proxied response) — `model=f"mcp::{server_host}::{tool_name}"` names the call; no new table.
  - M11 Every successful (non-refused, non-blocked) tool call additionally makes ONE fire-and-forget call to a new `ToolCallObserver.record(...)` Protocol (no-op default in this task) that the sibling `tool-call-metering` task (depends-on this task) wires to the real pricing_unit dispatcher — this task never writes a `usage_records` row itself (one billing path).
  - M12 A DB/JSONB-parse failure while resolving the effective allow-list is treated as an EMPTY list (deny-all) — logged, never raised, never "allow".
  - M13 Neither the session-trace row nor any audit-event metadata ever contains the raw credential/auth header the agent supplied for the upstream MCP server — only `server_host`, `tool_name`, and outcome are recorded.
  - M14 Auth for `/v1/mcp/call` reuses `CompositeKeyAuthenticator` UNCHANGED — an `sk-` API key or a device-OAuth agent token both resolve to one `AuthzResult`; no new credential class.
</must>
Reject:
<reject>
  - R1 `server_url` not an exact member of the effective allow-list -> "ERR_MCP_SERVER_NOT_ALLOWED" (403)
  - R2 allow-list PUT body contains a URL that is non-https, hostless, or a literal metadata/private/loopback/link-local IP -> "ERR_MCP_SERVER_URL_INVALID" (422)
  - R3 allow-list PUT body exceeds 50 entries -> "ERR_MCP_SERVER_LIST_TOO_LONG" (422)
  - R4 `server_url` passes the allow-list but resolves, fresh, to a denied IP at dial time -> "ERR_MCP_EGRESS_DENIED" (403)
  - R5 upstream MCP server responds 3xx -> "ERR_MCP_UPSTREAM_REDIRECT_REJECTED" (502)
  - R6 dial exceeds `mcp_connector_dial_timeout_seconds`, or the circuit for that (tenant, host) is open -> "ERR_MCP_UPSTREAM_UNAVAILABLE" (503)
  - R7 non-owner PUTs `/admin/mcp-servers`; non-owner/admin PUTs or DELETEs `/admin/keys/{key_id}/mcp-servers` -> "ERR_AUTH_FORBIDDEN" (403)
  - R8 `key_id` in `/admin/keys/{key_id}/mcp-servers` is unknown, cross-tenant, or revoked -> "ERR_KEY_NOT_FOUND" (404) — all three collapse identically, no existence leak
</reject>
After:
<after>
  - A tenant OWNER has full CRUD visibility/control over which MCP servers its agents may reach, at both tenant and per-key granularity, key > tenant > default-deny.
  - Every unlisted-server call is refused with zero egress dials and an audit trail entry.
  - Every dialed call is traced (PII-scrubbed) into the existing Logs Explorer capture store and separately fires the hook `tool-call-metering` will bill through.
  - No SSRF/DNS-rebind/redirect path exists from an agent-supplied MCP URL to a gateway-internal or cloud-metadata address.
  - A compromised/malicious MCP server cannot inject prompt-override content back into the calling agent's context without tripping the tenant's own existing prompt_injection guardrail.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ M6's DNS-rebind close ("the IP `check()` validates is the SAME IP the actual TCP connection uses") requires pinning httpx's connection to a specific IP obtained from `EgressPolicy.check()` — a mechanism this codebase has NOT needed before (the BYOK Azure case is a fixed, low-attacker-influence endpoint whose residual TOCTOU gap the `egress_policy.py` docstring accepts explicitly). The exact httpx-level mechanism (custom transport forcing the connection's peer IP while still sending the original Host/SNI, bypassing normal connection-pool DNS reuse) is a real implementation risk. Lowest confidence because MCP server URLs are the FIRST tenant/agent-fully-controlled (not enterprise-fixed) egress target in this codebase — DNS rebind is the single highest-value attack in the whole task; if wrong, the build likely needs a change-request back to this contract, and a missed pin is a live SSRF into cloud metadata reachable by any tenant that can add ANY hostname-based server to its own allow-list.
  - [ ] The in-band JSON-RPC error shape for M9's tool-result block (a synthetic `{"error": {"code": -32050, ...}}` object inside an otherwise-200 response, rather than an HTTP-level 4xx) is the right layer for a content-level refusal — confirm at freeze; if wrong, the Reject list gains a new HTTP-level rejection and M9's wording changes, but no other contract surface is affected.
  - [ ] `ToolCallObserver` as a no-op-by-default injectable Protocol (rather than a plain domain-event log row `tool-call-metering` polls) is the right hand-off shape — confirm the sibling task's build is comfortable wiring a DI-injected Protocol implementation.
  - [ ] The 50-entry allow-list cap (R3) is an arbitrary hygiene bound, not sized against any real enterprise MCP-fleet count — confirm or adjust at freeze; low cost either way (a config-shape-only change).
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner sets the tenant allow-list   # M1
  Given an authenticated OWNER identity
  When they PUT /admin/mcp-servers with servers=[{url:"https://mcp.acme.example/v1", label:"Acme"}]
  Then the response is 200 with the servers list echoed back and updated_at set
  And a fire-and-forget audit event "mcp_server_policy.put" is recorded

Scenario: Any role can read the tenant allow-list   # M1
  Given an authenticated MEMBER identity of a tenant with a non-empty allow-list
  When they GET /admin/mcp-servers
  Then the response is 200 with the current servers list

Scenario: Owner sets a per-key override   # M2
  Given an authenticated OWNER identity and an active key belonging to their tenant
  When they PUT /admin/keys/{key_id}/mcp-servers with servers=[{url:"https://mcp.narrow.example", label:"narrow"}]
  Then the response is 200 with source="key" and the key's override servers echoed back
  And the tenant-level allow-list is unchanged

Scenario: Explicit empty key override denies everything for that key   # M2
  Given a tenant allow-list containing one server
  And a key with an explicit empty override ([])
  When an agent authenticated as that key calls POST /v1/mcp/call against that tenant-listed server
  Then the call is refused ERR_MCP_SERVER_NOT_ALLOWED
  And no egress dial occurred

Scenario: Key with no override inherits the tenant list   # M2
  Given a tenant allow-list containing server https://mcp.acme.example/v1
  And a key with mcp_allowed_servers_override = NULL
  When they GET /admin/keys/{key_id}/mcp-servers
  Then the response reports source="tenant" and the tenant's servers

Scenario: Key override cleared reverts to tenant inheritance   # M2
  Given a key with a non-null override
  When an owner DELETEs /admin/keys/{key_id}/mcp-servers
  Then the response is 204
  And a subsequent GET reports source="tenant"

Scenario: Write-time literal private-IP host is rejected   # M3, R2
  Given an authenticated OWNER identity
  When they PUT /admin/mcp-servers with a server url of "https://169.254.169.254/mcp"
  Then the response is 422 ERR_MCP_SERVER_URL_INVALID
  And the tenant's stored allow-list is unchanged

Scenario: Write-time non-https scheme is rejected   # M3, R2
  Given an authenticated OWNER identity
  When they PUT /admin/mcp-servers with a server url of "http://mcp.acme.example/v1"
  Then the response is 422 ERR_MCP_SERVER_URL_INVALID
  And the tenant's stored allow-list is unchanged

Scenario: Write-time hostname literal always passes (DNS deferred)   # M3
  Given an authenticated OWNER identity
  When they PUT /admin/mcp-servers with a server url of "https://mcp.acme.example/v1" (a hostname, not a literal IP)
  Then the write-time check passes regardless of what the hostname later resolves to
  And the response is 200

Scenario: Effective allow-list resolved from AuthzResult, no extra query   # M4
  Given an authenticated key identity with a resolved AuthzResult carrying mcp_allowed_servers
  When POST /v1/mcp/call is handled
  Then the effective allow-list used for the membership check is read from that SAME AuthzResult
  And no additional allow-list DB query is issued for that request

Scenario: Caller-supplied tenant_id field is ignored   # M4
  Given an authenticated identity for tenant A
  When POST /v1/mcp/call is sent with an extraneous body field tenant_id belonging to tenant B, targeting a server on tenant A's own allow-list
  Then the call is evaluated using tenant A's identity and allow-list only
  And tenant B's policy is never read or affected

Scenario: Unlisted server refused fail-closed, zero dials   # M5, R1
  Given an effective allow-list that does NOT contain "https://evil.example/mcp"
  When an agent calls POST /v1/mcp/call with server_url="https://evil.example/mcp"
  Then the response is 403 ERR_MCP_SERVER_NOT_ALLOWED
  And no DNS lookup or socket connect was attempted for evil.example
  And a fire-and-forget audit event is recorded with result="refused"

Scenario: Allow-listed hostname that resolves to a private IP is denied at dial time   # M6, R4
  Given a server url "https://rebind.example/mcp" is on the effective allow-list
  And DNS resolution for rebind.example returns 10.0.0.5 (RFC1918)
  When POST /v1/mcp/call is handled
  Then EgressPolicy.check() raises EgressDeniedError
  And the response is 403 ERR_MCP_EGRESS_DENIED
  And no socket connect to 10.0.0.5 occurred

Scenario: The dial connects to the exact IP the egress check validated (DNS-rebind close)   # M6
  Given a server url whose first resolution is a public IP and whose SECOND (simulated racing) resolution would be 169.254.169.254
  When POST /v1/mcp/call dials the allow-listed, egress-checked server
  Then the actual TCP connection is pinned to the IP EgressPolicy.check() validated
  And no second, independent DNS resolution influences the connection target

Scenario: Upstream 3xx redirect is refused, never followed   # M7, R5
  Given an allow-listed, egress-clean server that responds 302 with Location pointing at a private IP
  When POST /v1/mcp/call dials it
  Then the response is 502 ERR_MCP_UPSTREAM_REDIRECT_REJECTED
  And the gateway never issued a request to the Location host

Scenario: Dial timeout bounded and never retried   # M8, R6
  Given an allow-listed server that never responds within mcp_connector_dial_timeout_seconds
  When POST /v1/mcp/call dials it
  Then the response is 503 ERR_MCP_UPSTREAM_UNAVAILABLE after the bound elapses
  And the gateway made exactly one dial attempt, never a second automatic retry

Scenario: Open circuit short-circuits further dials to a failing host   # M8, R6
  Given the per-(tenant,host) circuit breaker for a given server host is OPEN from prior consecutive failures
  When POST /v1/mcp/call targets that same host again
  Then the response is 503 ERR_MCP_UPSTREAM_UNAVAILABLE immediately, with no new dial attempted

Scenario: Tool-call result matching a block-mode injection pattern is neutralized   # M9
  Given the tenant's prompt_injection guardrail is enabled, mode=block
  And an allow-listed MCP server's tool-call result content contains "ignore previous instructions"
  When POST /v1/mcp/call relays that result
  Then the caller receives a synthetic JSON-RPC error object {"error":{"code":-32050,...}} in place of the raw content
  And the session-trace row and an audit event both record the call as "blocked"

Scenario: Tool-call result matching an audit-mode injection pattern passes through unmodified   # M9
  Given the tenant's prompt_injection guardrail is enabled, mode=audit
  And a tool-call result contains an injection-pattern match
  When POST /v1/mcp/call relays that result
  Then the caller receives the ORIGINAL, unmodified result content
  And an audit-mode guardrail event is recorded on the session-trace row

Scenario: Clean tool-call result passes through and is traced   # M9, M10
  Given the tenant's prompt_injection guardrail is enabled, mode=block
  And a tool-call result contains no injection-pattern match
  When POST /v1/mcp/call relays that result
  Then the caller receives the original result content
  And exactly one request_logs row is written with model="mcp::mcp.acme.example::<tool_name>"

Scenario: Refused call still writes exactly one (metadata-shaped) trace row   # M10
  Given an unlisted-server call refused by M5
  When the refusal response is returned
  Then exactly one request_logs row is written recording the refusal outcome
  And the proxied caller response was already sent before that fire-and-forget write completes

Scenario: Session-trace write failure never affects the proxied response   # M10 (partial failure)
  Given the capture-store DB is unreachable when SqlAlchemyPayloadCapture.capture() is invoked
  When a successful MCP tool call completes
  Then the caller still receives the correct 200 proxied response
  And no request_logs row exists for that call, and no exception propagated to the caller

Scenario: ZDR tenant suppresses the session-trace row entirely   # M10
  Given the tenant is under Zero-Data-Retention (ZdrOverridePort.is_zdr returns true)
  When a successful MCP tool call completes
  Then no request_logs row is written for that call
  And the proxied response to the caller is unaffected

Scenario: Successful tool call emits the metering hook exactly once   # M11
  Given an allow-listed, egress-clean, non-blocked tool call completes successfully
  When the response is returned
  Then ToolCallObserver.record(...) is invoked exactly once with tenant_id, key_id, server_host, tool_name, status, latency_ms
  And no usage_records row is written by this task's own code

Scenario: Refused or blocked calls do NOT emit the metering hook   # M11, R1, M9
  Given a call refused for an unlisted server, or blocked for injected tool-call content
  When the response is returned
  Then ToolCallObserver.record(...) is NOT invoked for that call

Scenario: Allow-list resolution failure fails closed, not open   # M12
  Given the stored mcp_allowed_servers_override JSONB is malformed for a key
  When POST /v1/mcp/call is handled for that key
  Then the effective allow-list is treated as empty (deny-all)
  And any server_url is refused ERR_MCP_SERVER_NOT_ALLOWED
  And the parse failure is logged, never raised to the caller as a 500

Scenario: Upstream credential header never appears in the trace or audit metadata   # M13
  Given an agent supplies its own upstream Authorization header for the MCP server
  When the call is traced and audited (success, refusal, or block)
  Then neither the request_logs row nor the audit_events metadata contains that header's value
  And only server_host, tool_name, and outcome are present

Scenario: Agent-token-authenticated caller uses the same allow-list resolution   # M14
  Given a device-OAuth agent token (not an sk- API key) authenticates the caller
  When POST /v1/mcp/call is handled
  Then CompositeKeyAuthenticator resolves ONE AuthzResult exactly as it does for an sk- key
  And the SAME effective-allow-list resolution and fail-closed refusal logic applies identically

Scenario: Non-owner cannot set the tenant allow-list   # R7
  Given an authenticated ADMIN (non-owner) identity
  When they PUT /admin/mcp-servers
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And the tenant's stored allow-list is unchanged

Scenario: Member cannot set or clear a key override   # R7
  Given an authenticated MEMBER identity
  When they PUT or DELETE /admin/keys/{key_id}/mcp-servers
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And the key's stored override is unchanged

Scenario: Unknown, cross-tenant, or revoked key_id collapses to one 404   # R8
  Given a key_id that is either nonexistent, owned by a different tenant, or revoked
  When an owner GETs or PUTs /admin/keys/{key_id}/mcp-servers
  Then the response is 404 ERR_KEY_NOT_FOUND in all three cases, identically
  And no response detail distinguishes which of the three applied

Scenario: Allow-list PUT at exactly the boundary count succeeds   # M3 boundary
  Given an authenticated OWNER identity
  When they PUT /admin/mcp-servers with exactly 50 valid server entries
  Then the response is 200 with all 50 entries stored

Scenario: Allow-list PUT one over the boundary is rejected   # R3 boundary
  Given an authenticated OWNER identity
  When they PUT /admin/mcp-servers with 51 valid server entries
  Then the response is 422 ERR_MCP_SERVER_LIST_TOO_LONG
  And the tenant's stored allow-list is unchanged

Scenario: Duplicate server URLs in one PUT are stored without erroring   # duplicate edge case
  Given an authenticated OWNER identity
  When they PUT /admin/mcp-servers with the same url appearing twice with different labels
  Then the response is 200 (last-write-wins for that url is acceptable; the write is not rejected)

Scenario: Concurrent PUTs to the same key override do not corrupt state   # concurrency edge case
  Given two concurrent PUT /admin/keys/{key_id}/mcp-servers requests with different server lists
  When both complete
  Then the key's stored override equals exactly one of the two submitted lists, never a merged/corrupted mix
  And each request's own response accurately reflects what it wrote

Scenario: Key revoked between allow-list read and write loses the race safely   # concurrency edge case
  Given a key is being PUT with a new mcp-servers override
  And the key is revoked by a separate request in between the fetch and the UPDATE
  When the PUT's UPDATE ... WHERE revoked_at IS NULL matches zero rows
  Then the response is 404 ERR_KEY_NOT_FOUND
  And no partial write occurred
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PUT /admin/mcp-servers   body: { servers: [{ url: str, label: str }] }   (OWNER only — Permission.SECURITY_CONFIG)
  200 -> { servers: [{url, label}], updated_at: str }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  422 -> { code: "ERR_MCP_SERVER_URL_INVALID" | "ERR_MCP_SERVER_LIST_TOO_LONG" }

GET /admin/mcp-servers   (any authenticated tenant role)
  200 -> { servers: [{url, label}], updated_at: str | null }

PUT /admin/keys/{key_id}/mcp-servers   body: { servers: [{ url: str, label: str }] | null }   (owner/admin — require_owner_or_admin)
  200 -> { servers: [{url, label}] | null, source: "key" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_KEY_NOT_FOUND" }
  422 -> { code: "ERR_MCP_SERVER_URL_INVALID" | "ERR_MCP_SERVER_LIST_TOO_LONG" }

GET /admin/keys/{key_id}/mcp-servers   (any authenticated tenant role)
  200 -> { servers: [{url, label}] | null, source: "key" | "tenant" }
  404 -> { code: "ERR_KEY_NOT_FOUND" }

DELETE /admin/keys/{key_id}/mcp-servers   (owner/admin — require_owner_or_admin)
  204 -> (empty body)
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_KEY_NOT_FOUND" }

POST /v1/mcp/call   body: { server_url: str, message: <JSON-RPC 2.0 envelope, opaque>, upstream_headers?: {str: str} }
                     (CompositeKeyAuthenticator — sk- key or agent-oauth token, unchanged)
  200 -> <proxied JSON-RPC response body, streamed verbatim>
       | 200 -> { jsonrpc: "2.0", id: <echoed>, error: { code: -32050, message: "ERR_MCP_TOOL_RESULT_BLOCKED" } }   (M9 in-band block)
  401 -> { code: "ERR_AUTH_INVALID_KEY" }   (existing CompositeKeyAuthenticator failure path, unchanged)
  403 -> { code: "ERR_MCP_SERVER_NOT_ALLOWED" | "ERR_MCP_EGRESS_DENIED" }
  502 -> { code: "ERR_MCP_UPSTREAM_REDIRECT_REJECTED" }
  503 -> { code: "ERR_MCP_UPSTREAM_UNAVAILABLE" }

Schema:
  tenants.mcp_allowed_servers        JSONB NOT NULL DEFAULT '[]'::jsonb   -- list[{url,label}]; empty = deny-all (secure default)
  api_keys.mcp_allowed_servers_override   JSONB NULL DEFAULT NULL         -- NULL=inherit tenant; non-null (incl. explicit []) = wholesale key override
                                                                            -- mirrors api_keys.guardrail_policy (per-key-guardrail-policies precedent) exactly
  -- No new table for tracing: reuses `request_logs` (payload-capture-store, FROZEN v1) verbatim via
  -- SqlAlchemyPayloadCapture.capture(model=f"mcp::{server_host}::{tool_name}", request_body={"messages":[...]},
  -- response_body={"messages":[...]}, guardrail_configs=<effective>, tenant_id=, key_id=, status=, stream=False, cached=False)
  keys.domain.entities.AuthzResult.mcp_allowed_servers   list[str] | None   -- NEW additive field, populated in the
                                                                             -- SAME LEFT JOIN SqlAlchemyKeyAuthenticator/
                                                                             -- CompositeKeyAuthenticator already run for guardrail_configs/model_allowlist
  gateway.mcp_connector.domain.ports.ToolCallObserver (NEW Protocol):
    async def record(self, *, tenant_id: UUID, key_id: UUID, server_host: str, tool_name: str,
                      status: Literal["success"], latency_ms: int) -> None
    -- no-op default implementation shipped by THIS task; tool-call-metering (depends-on this task) wires the
    -- real pricing_unit-dispatcher implementation via DI, mirroring the ModelHealthGate/UsageRecorder injectable-port style.
  gateway.mcp_connector.domain.ports.McpDialer (NEW Protocol, prod impl wraps httpx):
    async def dial(self, *, server_url: str, message: dict, upstream_headers: dict[str, str],
                    egress_policy: EgressPolicy) -> McpDialResult
    -- prod impl: follow_redirects=False, asyncio.wait_for(mcp_connector_dial_timeout_seconds), pins the TCP
    -- connection to the IP egress_policy.check() validated (no second independent DNS resolution — M6/⚠).

Access pattern: every read/write scoped to `identity.tenant_id`/`identity.key_id` from the authenticated request
  only (get_identity / CompositeKeyAuthenticator-derived AuthzResult) — never a tenant_id/key_id accepted from a
  request body or path param that could target another tenant (residency-policy precedent).
```

Glossary deltas:
- **MCP allow-list**: tenant-admin (or per-key override) policy naming the exact MCP server URLs an agent/key may dial; empty is the secure default (deny-all); precedence key > tenant > default-deny (MILESTONE.md-named term, defined precisely here).
- **Tool-call observer**: the no-op-by-default `ToolCallObserver.record(...)` hook this task fires on every successfully-dialed (non-refused, non-blocked) MCP tool call — the ONE seam the sibling `tool-call-metering` task bills through; this task never writes a `usage_records` row itself.
- **MCP session trace**: an ordinary `request_logs` row (no new table) whose `model` column is namespaced `mcp::<server_host>::<tool_name>`, written via the EXISTING `SqlAlchemyPayloadCapture` seam — distinct from a chat completion's `request_logs` row only by that namespacing convention.
- **Egress-checked dial**: an outbound MCP-server connection whose TCP peer IP is the SAME IP `EgressPolicy.check()` validated (not a second, independently-resolved IP) — the specific DNS-rebind-TOCTOU-closing property M6 requires, distinct from the BYOK egress_policy.py precedent which accepts that residual gap for a fixed enterprise endpoint.

Status: DRAFT
Reported: no
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/mcp_connector/` (new module: domain/ports.py, domain/entities.py, application/use_cases.py, infrastructure/httpx_dialer.py, infrastructure/repository.py, api/admin_router.py, api/key_router.py, api/proxy_router.py) · `apps/gateway/src/gateway/keys/domain/entities.py` (additive `AuthzResult.mcp_allowed_servers` field only) · `apps/gateway/src/gateway/proxy/infrastructure/key_authenticator.py` + `composite_key_authenticator.py` (additive LEFT JOIN column read only, no behavior change to existing fields) · `apps/gateway/src/gateway/core/error_catalog.py` (additive `ERR_MCP_*` entries only) · `apps/gateway/src/gateway/core/config.py` (additive `mcp_connector_dial_timeout_seconds` + circuit-breaker knobs only) · `apps/gateway/src/gateway/main.py` (router mount only) · `apps/gateway/migrations/versions/` (one new additive migration) · `./tests/`

Strategy (ordered batches):
  1. Migration + Settings knobs first (additive-only DDL: `tenants.mcp_allowed_servers`, `api_keys.mcp_allowed_servers_override`; `mcp_connector_dial_timeout_seconds` + breaker config) — everything else depends on these existing.
  2. `mcp_connector/domain/` (entities + the two NEW Protocols `McpDialer`/`ToolCallObserver`, plus a no-op `NoopToolCallObserver`) — pure, zero-framework, per CONVENTIONS.md layering; write these against the §2 scenarios directly, no infra yet.
  3. Allow-list resolution: extend `AuthzResult` additively, thread the new column through the SAME LEFT JOIN `SqlAlchemyKeyAuthenticator`/`CompositeKeyAuthenticator` already run (M4/M12) — a fail-closed JSONB-parse-error path (empty list) belongs here, unit-tested in isolation before any router exists.
  4. Admin CRUD routers (`/admin/mcp-servers`, `/admin/keys/{key_id}/mcp-servers`) — copy `residency_policy_router.py` / `key_guardrail_router.py` shape near-verbatim (OWNER-only via `Permission.SECURITY_CONFIG`, `require_owner_or_admin`, `record_audit` fire-and-forget, race-safe `UPDATE ... RETURNING id`); write-time validation calls `assert_literal_host_not_denied` UNCHANGED, imported not reimplemented.
  5. `McpDialer` httpx implementation LAST, in isolation, with its own focused test file — this is the highest-risk unit (M6 DNS-rebind IP pin, M7 no-redirect, M8 timeout+breaker+no-retry). Build and adversarially test this piece alone before wiring the proxy router around it.
  6. `POST /v1/mcp/call` proxy router wiring everything together: allow-list check (fail-closed, zero dials) → `McpDialer.dial()` (egress-checked) → `GuardrailEvaluator.evaluate_pre` on the tool-result content (M9) → `SqlAlchemyPayloadCapture.capture()` fire-and-forget (M10) → `ToolCallObserver.record()` fire-and-forget (M11) → response. Order matters: the allow-list check must be the FIRST thing evaluated, before any other work, so a refusal truly costs zero egress dials.
  7. Full scenario-by-scenario red→green pass, then the adversarial DNS-rebind + redirect-bypass scenarios specifically (M6/M7) — these are the ones a first green pass is most likely to have only weakly asserted (e.g. asserting on a mocked resolver rather than proving the actual connection target).

Persona (required): `appsec-engineer` (`.add/personas/appsec-engineer.md`) — its "verify both failure directions" stance (unauthorized access AND data/secret leak) maps directly onto this task's dual threat model (allow-list bypass AND credential-passthrough leakage); its cross-tenant byte-identical-404 discipline is the same shape M2/R8's 3-way key-not-found collapse needs.
Spawn isolation (default): `worktree` — this task's httpx transport/DNS-pinning work is exploratory and higher-risk than most; isolate it from any concurrent sibling-task build (`anthropic-messages-ingress`, `agent-identity-governance` are running in the same milestone).
Known-problem fixes:
  - trap: httpx connection-pool keep-alive reuse could silently reuse a PRE-pin connection for a different resolved IP on a subsequent call to the same host → planned fix: a fresh, unpooled connection per dial (or a pool keyed on the pinned IP, not just the hostname).
  - trap: a naive "resolve once, pass IP as URL host, Host header = original hostname" approach breaks TLS SNI/cert validation for https → planned fix: use httpx's transport-level connect override (custom `AsyncHTTPTransport`/socket-level connect) so TLS SNI still targets the original hostname while the TCP connect targets the pinned IP — verify this against a real https MCP test-double, not just a mock.
  - trap: `evaluate_pre`'s existing fail-open-on-exception behavior (guardrail_evaluator.py) is correct for CHAT messages but must NOT silently pass an unscanned tool result through in block mode for MCP — confirm the existing exception handling in `_evaluate_pre_inner` already fails closed for block-mode (it does, per Ground read) before assuming test coverage carries over unchanged.
  - trap: writing the session-trace row synchronously (awaiting it) would make M10's contract ("never blocks the proxied response") false under load → planned fix: `asyncio.ensure_future`, mirroring every other fire-and-forget call site in this codebase (residency_policy_router.py, key_guardrail_router.py, capture call sites).
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the allow-list membership check (M5) and the fresh `EgressPolicy.check()` (M6) MUST both complete, in that order, strictly BEFORE any `McpDialer.dial()` call is constructed — no code path may construct the outbound HTTP request object before both gates pass (the "zero egress dials on refusal" guarantee is a construction-order invariant, not just a status-code one; a test must assert the dialer mock was never invoked, not just that the response was a 403).
Code lives in: `apps/gateway/src/gateway/mcp_connector/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new MCP SDK / third-party dependency — httpx only, per Ground); ask if unclear.

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
