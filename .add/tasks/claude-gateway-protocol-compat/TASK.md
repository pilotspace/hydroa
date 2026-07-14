# TASK: Claude apps gateway protocol compatibility: Claude Code fronts Hydroa first-class

slug: claude-gateway-protocol-compat · created: 2026-07-14 · stage: production
milestone: agent-gateway-v1
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `.add/tasks/anthropic-messages-ingress/TASK.md` §3 — **FROZEN @ v1** (approved by Tin Dang). This task builds ON that `/v1/messages` + `/v1/messages/count_tokens` wire surface unchanged: same request/response/SSE shapes, same Anthropic error envelope, same `x-api-key`/`Authorization: Bearer` priority (M3 there). This task never edits that contract — a gap discovered against it is recorded as a change-request back to SPECIFY on that task, not patched here.
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py::ModelRow` (`id`, `active`, `provider`, `region`, `input_modalities`) — confirmed (per the sibling's own Ground) `id` is the upstream PROVIDER-NATIVE id string (e.g. Bedrock's `us.anthropic.claude-3-5-sonnet-20241022-v2:0`, OpenRouter's `anthropic/claude-opus-4`) — essentially never already `claude-*`/`anthropic-*` shaped, the exact id-prefix Claude Code's model-discovery filter requires (see external anchor below).
- `apps/gateway/src/gateway/core/config.py::Settings.model_groups` (property ~L851, backing field ~L833-840, `GATEWAY_MODEL_GROUPS` env var) — an OPERATOR-WIDE alias→candidate-list map (v32 "writable routing config," restart-to-apply), NOT per-tenant.
- `apps/gateway/src/gateway/proxy/application/fallback_router.py::FallbackModelRouter` (`model_groups` property ~L188-207, candidate iteration ~L274/476/514/563) — the EXISTING candidate-substitution engine a new tenant policy flag (§1 M8) must gate, not replace or duplicate.
- `apps/gateway/src/gateway/proxy/application/modality_guard.py::resolve_allowed` (L70-87) — precedent for an alias-aware "resolve to the union of candidates" helper; the shape a new tenant-scoped model-enumeration query (§1 M1) should mirror rather than reinvent.
- `apps/gateway/src/gateway/proxy/application/use_cases.py::CompletionUseCase._check_model_catalog` (~L1339-1400) — the existing PER-MODEL entitlement check (alias-aware via `model_groups`). No existing code path enumerates "every model a tenant/key is currently permitted to use" — that is genuinely new ground for `GET /v1/models` (§1 M1, flagged ⚠).
- `apps/gateway/src/gateway/usage/infrastructure/orm.py::UsageRecordRow.raw` (JSONB, append-only) + `apps/gateway/src/gateway/usage/application/recorder.py::RecordingUsageRecorder.record` docstring (~L145-146: `request_id` stored into `raw["request_id"]`, explicitly "NOT a new column (usage_records is FROZEN)") — the EXACT precedent this task's session/subagent cost-attribution (§1 M4) extends: additive JSONB keys, never a schema migration, never a new discriminator alongside `usage_source`'s fixed vocabulary (`frame|stream_fallback|client_disconnect|openrouter_recovered`).
- `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` — the frozen EGRESS Anthropic adapter (`_openai_to_anthropic_request`/`_anthropic_to_openai`/`_anthropic_error_to_openai`). The sibling ingress task's own Constraints line forbids editing it ("frozen contract, separate surface... only mirror their vocabulary"); this task likewise never edits it — §1 M6/M7 are integration-VERIFIED against it (once the sibling is built), not re-implemented.
- `apps/gateway/src/gateway/proxy/infrastructure/composite_key_authenticator.py::CompositeKeyAuthenticator` — reused unmodified for the new `GET /v1/models` authn (mirrors the sibling's own reuse discipline; no new authenticator).
- `apps/gateway/src/gateway/agent_oauth/` (`infrastructure/orm.py`, `domain/ports.py`, `infrastructure/repository.py`) — the EXISTING v39 device-OAuth "agent principal" grant store. CONFIRMED via inspection: this is a DISTINCT concept from Claude Code's own `x-claude-code-agent-id` (a per-spawn, session-scoped SUBAGENT label inside one credential's traffic — "don't treat the agent ID header as a user identifier," per the external protocol reference). This task's §1 M4 must not conflate the two; no `depends-on` edge exists between this task and the sibling `agent-identity-governance` task in MILESTONE.md, so this task cannot assume that task has landed.
- `apps/gateway/src/gateway/core/error_catalog.py::RESIDENCY_NO_ELIGIBLE_REGION` (403, `ERR_RESIDENCY_NO_ELIGIBLE_REGION`, comment: "the request is refused, NEVER silently rerouted out-of-region... never billed and produces no usage record") — the EXACT precedent §1 M8's non-Claude-failover-opt-in refusal (§3 `ERR_NO_ELIGIBLE_ANTHROPIC_CANDIDATE`) mirrors: same status code class, same fail-closed/never-bill shape, applied to a provider-family gate instead of a region gate.

External protocol anchors (WebFetched 2026-07-14 directly from Anthropic's published docs — not assumed, quoted):
- `code.claude.com/docs/en/llm-gateway-protocol` — the authoritative wire contract. Verbatim load-bearing facts: (a) API-formats table: an Anthropic-format gateway serves `/v1/messages`, `/v1/messages/count_tokens` (optional); (b) request-headers table: forward `anthropic-version`/`anthropic-beta` UNCHANGED as an OPEN list ("don't allowlist individual values, because the set changes with Claude Code releases"), forward `anthropic-workspace-id` only for a Claude-Platform-on-AWS upstream, CONSUME (never forward) `Authorization`/`x-api-key` (the developer's gateway credential) and `x-claude-code-session-id`/`x-claude-code-agent-id`/`x-claude-code-parent-agent-id`; (c) feature pass-through table: capabilities that add a body field (`context_management`, `output_config`, tool `strict`/`defer_loading`) PAIR with a beta header and "the pair travels together... only when both halves are absent together does the feature turn off quietly" — a gateway that strips one half produces hard `400`s; (d) "the retry logic matches on the upstream's error wording, so forward error response bodies unmodified. A gateway that wraps upstream errors in its own envelope breaks the recovery path even when it preserves the status code"; (e) system-prompt attribution block: "Forward the `system` array exactly as received, keeping the block first... reordering the array, or converting it to a single string defeats the strip, and the block then reaches the model and the prompt cache key"; (f) model discovery: `GET /v1/models?limit=1000`, 3-second timeout, "any redirect is treated as failure," response `{"data":[{"id","display_name"?}]}`, "ignores entries whose `id` doesn't begin with `claude` or `anthropic`," off by default (`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`) "so that gateways backed by a shared API key don't surface every model the key can access to every user"; (g) best-effort startup traffic (`HEAD /`) "a gateway also sees... it can reject without breaking anything."
- `code.claude.com/docs/en/llm-gateway` — "Any gateway that exposes a supported API format works. Anthropic doesn't endorse, maintain, or audit third-party gateway products, and doesn't support routing Claude Code to non-Claude models through any gateway" — the exact ToS caveat the milestone scope_hints require documented, not silently ignored.
- `code.claude.com/docs/en/claude-apps-gateway` — Anthropic's OWN self-hosted gateway is explicitly "Multi-tenant (multiple OIDC issuers): Not supported. One issuer per gateway. Run separate instances" — CONFIRMS Hydroa's multi-tenant framing is a genuine differentiator, not marketing spin. Its OIDC sign-in, PostgreSQL auth-state, managed-settings delivery, and OTLP telemetry fan-out are THAT product's value-adds — explicitly "Claude apps gateway-specific endpoints," not part of the baseline protocol every third-party gateway must implement (per the protocol-reference page's own framing: "A running Claude apps gateway serves a machine-readable version of THIS contract... plus the Claude apps gateway-specific endpoints"). **Correction to this task's own launch brief**: there is no "OTLP telemetry passthrough" surface in the baseline gateway protocol for Hydroa to build — Claude Code's own OTLP metrics export is a client-side, `OTEL_*`-env-var-governed concern separate from the `/v1/messages` wire path; only Claude-apps-gateway's managed-settings-push feature can override it, which requires the OIDC/managed-settings stack this milestone deliberately does not clone (agents-console/agent-identity-governance are separate, un-depended-on tasks).
- `code.claude.com/docs/en/llm-gateway-connect` — confirms Hydroa's EXISTING positioning already matches the "Other LLM gateway" (third-party, credential-based) connection model exactly: `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`(→`Authorization: Bearer`)/`ANTHROPIC_API_KEY`(→`x-api-key`) — no OIDC, no TLS-fingerprint pinning, no `/login` device flow needed; that whole apparatus belongs to Anthropic's own "Claude apps gateway" product, a different thing Hydroa is not building.

Context (working folder): `docs/roadmap/2026-07-14-enterprise-roadmap.html` §4 (R1 differentiator rationale: "multi-tenant + BYOK + failover to non-Claude providers, which Anthropic's own gateway and ToS won't do"); `.add/milestones/agent-gateway-v1/MILESTONE.md` (Shared decisions: fail-closed default, one billing path, ingress-is-translation-only — all bind this task too, even though this task's surface sits ABOVE the frozen ingress, not inside it); `tmp/r1-design-context.md` (shared design-agent rules).

Honors (patterns / conventions): the sibling's Anthropic-error-envelope carve-out from project-wide RFC 9457 (reused verbatim by the new `GET /v1/models` 401, not a third envelope shape); the `raw["request_id"]`-style additive-JSONB-key convention (never a new column, never a new `usage_source` value); the residency module's "refuse, never silently reroute, never bill" idiom (`RESIDENCY_NO_ELIGIBLE_REGION`) as the template for the new non-Claude-failover refusal; the "Plan-gated feature" GLOSSARY precedent (a tenant capability additionally gated by `plans.feature_flags`) as the leading candidate shape for the new `allow_non_claude_failover` flag; per-modality router-file convention (`embeddings_router.py`) for wherever `GET /v1/models` ends up living.

Seams consulted: none pre-exist — no `.add/SEAMS.md` entry yet covers protocol-header-forwarding-fidelity or model-discovery.

Anchors the contract cites: `ModelRow.id`/`.region` (`catalog/infrastructure/orm.py`); `Settings.model_groups` (`core/config.py`); `FallbackModelRouter` (`fallback_router.py`); `UsageRecordRow.raw` + `RecordingUsageRecorder.record`'s `request_id` precedent (`usage/infrastructure/orm.py`, `usage/application/recorder.py`); `RESIDENCY_NO_ELIGIBLE_REGION` (`core/error_catalog.py`); the sibling `anthropic-messages-ingress` TASK.md §3 (frozen wire shape + Anthropic error envelope); the external `llm-gateway-protocol` reference (headers/feature-pass-through/model-discovery tables, cited above).

Issues/Risks (→ feed §1):
1. `GET /v1/models` needs a brand-new "enumerate every model a tenant/key is currently permitted to use" query — no existing code path does this (only per-model checks exist via `_check_model_catalog`); its cost/shape against real tenant/plan data is unverified and must stay well inside Claude Code's 3-second client-side discovery timeout, or discovery just fails silently (degrades to the cached/built-in model list — not a hard break, but a missed differentiator).
2. The `system`-array round-trip fidelity (attribution block staying first, block count/order unchanged) required by the published protocol cannot be verified against real code yet — `anthropic-messages-ingress` is still at `phase: tests` (not built) as of this draft. This task can only add an integration test for it now and record a change-request if the eventual translator fails it; it must not assert the requirement is already met.
3. Likewise, "forward provider-native error bodies unmodified" (needed for Claude Code's wording-matched auto-retry/auto-compact) cannot yet be verified against the not-yet-built ingress error-mapping code — same integration-test-then-change-request path, not a redesign performed here.
4. `model_groups` (operator-wide, restart-to-apply, v32) and `tenant_model_preset_store` (per-tenant, v56, referenced in the sibling's Ground) are TWO different alias mechanisms; which one (or what precedence between both) supplies `GET /v1/models`'s Claude-style aliases is undecided — flagged in §1.
5. Silently defaulting non-Claude failover to "on" for Anthropic-wire traffic (reusing the sibling's M6 translate-through precedent) would contradict both this milestone's own differentiator framing and the project's "never silent reroute" idiom — resolved in §1 as an explicit, tenant-opt-in, default-OFF policy gate on the EXISTING fallback resolver only, never touching the sibling's M6 (which governs explicitly-named/aliased models, not fallback substitution).

Related intent: `MILESTONE.md` line "claude-gateway-protocol-compat ... implement Anthropic's published gateway protocol so Claude Code fronts Hydroa first-class (multi-tenant, non-Claude failover as the differentiator)" + Exit criterion "Claude Code completes a real session through Hydroa via /v1/messages with an accurate usage record + invoice line"; roadmap rationale = Anthropic ships/publishes but is single-org/Claude-only, Hydroa is the multi-tenant multi-provider implementation. GLOSSARY: no existing term conflicts found; new terms proposed in §3.

Ground SHA: 383f6e8

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Claude apps gateway protocol compatibility — full-fidelity header/beta passthrough, model discovery, session/subagent cost attribution, and an explicit non-Claude-failover opt-in, layered ON the frozen `/v1/messages` ingress surface (never redesigning it)

Framings weighed:
(chosen) A thin BEHAVIORAL/config layer above the frozen ingress surface: zero edits to `/v1/messages`'s wire shape. New work = (a) one new endpoint `GET /v1/models`, (b) header/beta-field forwarding-fidelity rules applied at the SAME ingress boundary, (c) one new tenant policy flag gating the EXISTING `FallbackModelRouter`, (d) integration tests that verify — and, on failure, change-request rather than silently patch — the frozen sibling's round-trip fidelity for the two protocol requirements (`system`-array shape, provider-error wording) the published protocol actually demands.
· Clone Anthropic's own "Claude apps gateway" wholesale (OIDC sign-in, PostgreSQL auth-state, managed-settings delivery, OTLP fan-out) — REJECTED: explicitly out of milestone scope; no SSO/managed-settings surface exists anywhere in Hydroa today, and building one would duplicate Hydroa's EXISTING tenant/API-key authn + usage_records/invoice pipeline (a second billing/telemetry path — violates "one billing path"). Per the published docs' own taxonomy, Hydroa is a third-party "Other LLM gateway" (credential-based), not a clone of Anthropic's first-party product — confirmed compatible with Hydroa's EXISTING `ANTHROPIC_BASE_URL` + bearer/`x-api-key` connection model (external anchor: `llm-gateway-connect`).
· Silently default non-Claude failover to "on" for all Anthropic-wire traffic (reuse the sibling's M6 translate-through precedent for the FALLBACK decision, not just explicitly-named models) — REJECTED: contradicts this milestone's own differentiator framing (a DISCLOSED capability, not a silent default) and the project's "never silent reroute" residency idiom; a tenant that hasn't asked for cross-provider failover on Claude-branded traffic should never be silently switched to a different model family.

Must:
<must>
  - M1: `GET /v1/models` (new endpoint on the Anthropic-format surface) authenticates identically to `/v1/messages` (same `x-api-key`/`Authorization: Bearer` priority, same `CompositeKeyAuthenticator`) and returns `{"data":[{"id","display_name"?}, ...]}` scoped to catalog rows the calling tenant/key is CURRENTLY entitled to (same entitlement semantics `_check_model_catalog` already applies per-model, applied across the tenant's catalog) AND that resolve to a `claude-`/`anthropic-`-prefixed id — either natively, or via a configured `model_groups`/tenant-preset alias. An entitled row with no such alias is OMITTED from the response (never fabricated) — surfacing it is an operator/tenant config change (add an alias), not a code change. Two entitled rows sharing the same alias id collapse to ONE `data` entry (never a duplicate id in the response); which underlying candidate actually serves a later request is decided entirely by the existing fallback/tier resolver, never by discovery. No redirect is ever issued from this route (a redirect fails Claude Code's discovery silently, per the published protocol).
  - M2: `anthropic-version` and `anthropic-beta` request headers are forwarded byte-verbatim, as an OPEN list (never allowlisted to today's known values), from the inbound `/v1/messages`/`/v1/messages/count_tokens` request through to the resolved provider WHEN that provider is the direct Anthropic adapter. Any request body field the published protocol pairs with a specific `anthropic-beta` value (`context_management`, `output_config`, tool `strict`/`defer_loading`, and any future paired field) travels WITH its header — both forwarded together, or both dropped together, never split — generalizing the frozen sibling's M7/M8 thinking/cache_control degrade precedent to the FULL open-ended capability set Claude Code may send, not only the two fields the sibling's egress vocabulary already covers.
  - M3: `anthropic-workspace-id` is forwarded byte-verbatim whenever the resolved provider is a Claude-Platform-on-AWS-shaped adapter — the one header the published protocol calls out as a hard per-upstream requirement distinct from the general open-list rule in M2.
  - M4: `x-claude-code-session-id`, `x-claude-code-agent-id`, `x-claude-code-parent-agent-id` — when present, are CONSUMED (never forwarded upstream) and stamped verbatim into the SAME served request's `usage_records.raw` JSONB as `raw["cc_session_id"]`/`raw["cc_agent_id"]`/`raw["cc_parent_agent_id"]`, mirroring the existing `raw["request_id"]` correlation-key convention — no new column, no parallel ledger, no new `usage_source` value. This is the concrete mapping of the protocol's "attribute usage by developer/session/subagent" concept onto Hydroa's existing tenant/key/usage_records model (the objective's required "multi-tenant mapping... contract-level").
  - M5: any other `ANTHROPIC_CUSTOM_HEADERS`-style developer-set header reaches the ingress boundary inert by default — Hydroa neither requires nor depends on any specific custom header for M1-M9 correctness; a tenant MAY opt in to having one consumed for routing/tagging (reusing the existing `X-Gateway-Tags`-style mechanism), but this is additive and optional, never required.
  - M6 (integration-verified against the sibling once built, never re-implemented here): the `system` request field's array shape and block order survive the ingress→egress round trip UNCHANGED when the resolved provider is the direct Anthropic adapter — verified by an integration test in THIS task's own suite, driving the sibling's (by-then-built) translator with a multi-block `system` array (a leading attribution-style block + a real system prompt) and asserting the outbound Anthropic-shaped request preserves block count/order exactly. A failure is recorded as a NAMED change-request against `anthropic-messages-ingress` (§7 Spec delta), never patched here.
  - M7 (integration-verified, never re-implemented here): a provider-native rejection from the direct Anthropic adapter that Claude Code auto-recovers from by wording-match (a `thinking`-field rejection, a thinking-signature rejection, a mid-conversation system-message rejection) reaches the Claude Code client with the upstream's ORIGINAL error message text verbatim inside the frozen Anthropic error envelope's `message` field — additive precision within the EXISTING `{"type":"error","error":{"type","message"}}` shape, not a new shape. Verified and escalated the same way as M6.
  - M8: a new per-tenant policy flag (proposed name `allow_non_claude_failover`, default **false**) gates ONLY the EXISTING `FallbackModelRouter` candidate-substitution mechanism, for a request that arrives Anthropic-wire naming (directly or via alias) a Claude model: default false means a fallback situation that would otherwise substitute a non-Claude candidate instead REFUSES fail-closed with a structured error rather than silently serving a non-Claude model (mirrors `RESIDENCY_NO_ELIGIBLE_REGION`'s refuse/never-bill/no-usage-row shape exactly); true (explicit tenant opt-in) means the existing fallback resolver behaves EXACTLY as it already does today — no new substitution logic, only a gate on whether the existing one may fire for this wire format. Orthogonal to, and never altering, the frozen sibling's M6 (an EXPLICITLY-named or explicitly-aliased non-Claude model is still served translate-through unconditionally regardless of this flag — M8 gates the FALLBACK/substitution decision only).
  - M9: `HEAD /` and any other unrecognized best-effort startup probe (e.g. a misconfigured Bedrock-format client's `GET /inference-profiles?type=SYSTEM_DEFINED`) return a clean, fast 404/405 — never a 500, a hang, or a redirect — so Claude Code's connectivity probe never itself becomes a diagnostic red herring.
</must>
Reject:
<reject>
  - R1: `GET /v1/models` with a missing/invalid/revoked credential -> "ERR_AUTH_INVALID_KEY" (401, Anthropic-shaped `authentication_error` — reuses the sibling's envelope, same opacity as `/v1/messages`'s equivalent 401).
  - R2: a request whose `anthropic-beta` value pairs a body field (e.g. `context_management`) but resolves to a non-Anthropic-shaped upstream that cannot accept the pair -> NOT a reject; degrades by dropping BOTH the header value and the paired field together (M2) — listed here only to make explicit, as the sibling's R7 does for `thinking`, that this is degrade-class behavior, not reject-class, despite superficially looking like an incompatibility.
  - R3: a tenant has NOT set `allow_non_claude_failover=true` and the existing fallback resolver would otherwise substitute a non-Claude candidate for a request naming (directly/aliased) a Claude model -> "ERR_NO_ELIGIBLE_ANTHROPIC_CANDIDATE" (403, Anthropic-shaped `permission_error`) — refused BEFORE any upstream dial, never billed, no `usage_records` row written (mirrors `RESIDENCY_NO_ELIGIBLE_REGION` exactly).
</reject>
After:
<after>
  - An unmodified Claude Code client (or Agent SDK session) pointed at Hydroa via `ANTHROPIC_BASE_URL` + a Hydroa-issued credential completes real sessions using its FULL current capability set (adaptive/extended thinking, context management, structured outputs, fine-grained tool streaming when enabled) when routed to Hydroa's direct Anthropic adapter — no silently-dropped beta capability, no `400` from an orphaned header/body-field pair.
  - Cost is attributable per Claude Code session and per parallel subagent within one tenant/key, via `usage_records.raw`, with no new billing dimension and no parallel ledger.
  - A tenant can explicitly opt in to non-Claude-model failover for Claude-branded traffic; until they do, a fallback situation that would have substituted a different model family refuses cleanly instead.
  - When an operator/tenant configures a `claude-`/`anthropic-`-prefixed alias for a catalog row, that model appears in Claude Code's `/model` picker via `GET /v1/models` discovery.
  - Hydroa's positioning as a genuinely multi-tenant, multi-provider gateway (vs. Anthropic's own single-issuer, Claude-only "Claude apps gateway") is verifiably true of the shipped surface, not just the roadmap narrative.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] M1's "enumerate every model a tenant/key is currently permitted to use" query is genuinely new ground — no existing code path lists a tenant's full permitted catalog, only checks one model at a time (`_check_model_catalog`). Lowest confidence because its cost/shape (N catalog rows × per-row entitlement check, vs. a batched rewrite) is unverified against real tenant/plan data volumes. If wrong: discovery responses could exceed Claude Code's 3-second client timeout — NOT a hard failure (discovery degrades gracefully to the cached/built-in model list per the published protocol), but a missed differentiator and a confusing "why isn't my model in the picker" support case.
  - [ ] M6/M7's `system`-array and error-wording fidelity claims are UNVERIFIED against real translator code — `anthropic-messages-ingress` is still at `phase: tests` as of this draft. Confirm or deny via this task's own integration tests once the sibling reaches BUILD; do not assume pass. If wrong: Claude Code sessions routed to Hydroa's direct-Anthropic adapter could silently lose prompt-cache efficiency (attribution block leaking into the cache key) or lose auto-retry/auto-compact recovery (generic error wording) — both degrade gracefully (session still works, just less efficiently/more manually) rather than hard-breaking, but are exactly the kind of "why is this slower/flakier through the gateway" defect that's invisible without this specific test.
  - [ ] Whether `model_groups` (operator-wide, restart-to-apply) or `tenant_model_preset_store` (per-tenant, v56) — or both, with a defined precedence — is the right alias source for M1's Claude-style ids is undecided; confirm at BUILD by reading the sibling's (by-then-shipped) preset-resolution code.
  - [ ] The exact storage shape of `allow_non_claude_failover` (M8) — a new `tenants` column vs. reusing the existing `plans.feature_flags` "plan-gated feature" convention (GLOSSARY precedent, no new column) — leaning toward the latter for precedent-consistency, not yet confirmed.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Model discovery returns only entitled, Claude-aliased models   # M1
  Given a tenant whose plan permits models A (native id "us.anthropic.claude-3-5-sonnet..." aliased to "claude-sonnet-4-6" via model_groups) and B (no alias configured), and NOT model C (unentitled)
  When the client sends GET /v1/models with a valid credential
  Then the response is 200 {"data":[{"id":"claude-sonnet-4-6", ...}]}
  And model B is omitted (no alias to a claude-/anthropic-prefixed id) and model C is omitted (not entitled)

Scenario: Model discovery never redirects   # M1
  Given any request to GET /v1/models
  When the route resolves
  Then the response is a direct 200 or 4xx — never a 3xx redirect, matching the published protocol's "any redirect is treated as failure" rule

Scenario: anthropic-beta and anthropic-version forwarded verbatim to the direct Anthropic adapter   # M2
  Given a request carrying anthropic-beta: "context-management-2025-06-27,some-future-beta-xyz" and anthropic-version: "2023-06-01", resolved to the direct Anthropic provider
  When the request is forwarded upstream
  Then both header values reach the upstream call byte-identical, including the unrecognized "some-future-beta-xyz" value (open list, never allowlisted)

Scenario: A beta-paired body field travels with its header, never split   # M2
  Given a request with anthropic-beta carrying the context-management capability AND a context_management body field, resolved to the direct Anthropic provider
  When the request is forwarded upstream
  Then both the header value and the context_management field reach the upstream together, unchanged

Scenario: anthropic-workspace-id forwarded only for the Claude-Platform-on-AWS adapter   # M3
  Given a request resolved to a Claude-Platform-on-AWS-shaped adapter, carrying anthropic-workspace-id: "wrkspc_01ABC"
  When the request is forwarded upstream
  Then the anthropic-workspace-id header reaches the upstream unchanged
  And a request resolved to any OTHER adapter never forwards this header (consumed/ignored instead)

Scenario: Session and subagent headers attribute cost without a new billing dimension   # M4
  Given a request carrying x-claude-code-session-id, x-claude-code-agent-id, and x-claude-code-parent-agent-id
  When the request completes and a usage_records row is written
  Then raw["cc_session_id"], raw["cc_agent_id"], raw["cc_parent_agent_id"] hold the header values verbatim
  And no new usage_records column was added and usage_source's existing vocabulary is untouched
  And none of the three headers were forwarded to the upstream provider

Scenario: Absent session/agent headers leave the row byte-identical to today   # M4 (boundary)
  Given a request carrying none of the x-claude-code-* headers
  When a usage_records row is written
  Then raw contains none of the three keys — identical in shape to a pre-this-task row

Scenario: Unrecognized custom headers are inert   # M5
  Given a request carrying an arbitrary ANTHROPIC_CUSTOM_HEADERS-style header Hydroa has no configured meaning for
  When the request is processed
  Then the request succeeds or fails exactly as it would without that header present — no behavior depends on its presence

Scenario: System array block order survives the ingress-to-egress round trip   # M6 (integration-verified)
  Given a /v1/messages request whose system field is a two-block array (a leading attribution-style block, then a real system-prompt block), resolved to the direct Anthropic provider
  When the request is translated ingress-to-internal-shape and then internal-shape-to-egress by the (built) sibling translator
  Then the outbound Anthropic-shaped request's system array has exactly two blocks in the same order, the first block byte-identical to the input's first block
  And a failure here is recorded as a named change-request against anthropic-messages-ingress, not silently patched

Scenario: Upstream error wording is preserved verbatim for wording-matched auto-retry   # M7 (integration-verified)
  Given the direct Anthropic provider rejects a request's thinking field with a specific error message string
  When the rejection is translated back through the ingress's Anthropic error envelope
  Then the envelope's error.message field contains the upstream's original message text verbatim, not a generic paraphrase
  And a failure here is recorded as a named change-request against anthropic-messages-ingress, not silently patched

Scenario: Fallback substitution to a non-Claude model is refused by default   # M8, R3
  Given a tenant with allow_non_claude_failover unset (default false), sending an Anthropic-wire request naming a Claude model whose only available candidates (per existing fallback/tier-capacity logic) are non-Anthropic
  When the client POSTs /v1/messages
  Then the response is 403 {"type":"error","error":{"type":"permission_error","message":...}} with code ERR_NO_ELIGIBLE_ANTHROPIC_CANDIDATE
  And no upstream dial occurred and no usage_records row was written

Scenario: Fallback substitution proceeds normally once a tenant opts in   # M8
  Given the SAME tenant/request as above, but with allow_non_claude_failover explicitly set to true
  When the client POSTs /v1/messages
  Then the existing fallback resolver behaves exactly as it already does for every other wire format — the request is served by the substituted non-Anthropic candidate
  And this does not change the sibling's M6 behavior for a model EXPLICITLY named/aliased non-Anthropic (still served translate-through regardless of this flag)

Scenario: Explicitly-named non-Claude model is unaffected by the failover flag   # M8 (boundary, confirms no M6 regression)
  Given a tenant with allow_non_claude_failover=false, sending an Anthropic-wire request explicitly naming a model that IS itself a non-Anthropic catalog row (not a fallback substitution)
  When the client POSTs /v1/messages
  Then the request is served normally per the sibling's frozen M6 — the new flag never refuses an explicitly-requested non-Claude model

Scenario: Connectivity probes never 500 or hang   # M9
  Given a bare HEAD / request and, separately, a GET /inference-profiles?type=SYSTEM_DEFINED request
  When either arrives
  Then each returns a clean 404 or 405 promptly — never a 500, a hang, or a redirect

Scenario: Two entitled catalog rows aliased to the same Claude-style id collapse, never duplicate   # M1 (edge case)
  Given a tenant entitled to two distinct catalog rows (e.g. a Bedrock-hosted and an OpenRouter-hosted candidate for the same underlying model) both configured with the alias "claude-sonnet-4-6"
  When the client sends GET /v1/models
  Then the response's data array contains exactly ONE entry with id "claude-sonnet-4-6" — never two entries racing for the same picker slot
  And which underlying candidate actually serves a later /v1/messages call is decided entirely by the EXISTING fallback/tier resolver, never by which one discovery happened to list
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /v1/models
  headers: Authorization: Bearer <key> | x-api-key: <key>     # identical priority to /v1/messages (sibling M3)
  200 -> { "data": [ { "id": "<claude-|anthropic--prefixed id or alias>", "display_name"?: string }, ... ] }
    # scoped to catalog rows the tenant/key is entitled to AND that resolve to a
    # claude-/anthropic- prefixed id (native id or a configured model_groups /
    # tenant-preset alias). An entitled row with no such alias is OMITTED, never fabricated.
  401 -> { "type":"error", "error": {"type":"authentication_error","message":"<m>"} }   "ERR_AUTH_INVALID_KEY"
  # never redirects (a 3xx here fails Claude Code's discovery silently — protocol requirement)

Behavioral contract ADDITIVE to the EXISTING, unmodified POST /v1/messages | /v1/messages/count_tokens
(wire shape frozen by anthropic-messages-ingress TASK.md §3 @ v1 — nothing below changes that shape):
  - anthropic-version, anthropic-beta headers: forwarded byte-verbatim (OPEN list, never allowlisted)
    to a direct-Anthropic-resolved request.
  - anthropic-workspace-id: forwarded byte-verbatim only to a Claude-Platform-on-AWS-resolved request.
  - Any anthropic-beta-paired request body field (context_management, output_config, tool
    strict/defer_loading, and future fields): forwarded together with its header, or both
    dropped together for a non-Anthropic-resolved request — never split (avoids the published
    protocol's documented hard-400 failure mode).
  - x-claude-code-session-id / x-claude-code-agent-id / x-claude-code-parent-agent-id: CONSUMED,
    never forwarded upstream; when present, stamped verbatim into the served request's
    usage_records.raw as cc_session_id / cc_agent_id / cc_parent_agent_id (mirrors the existing
    raw["request_id"] convention — no new column, no new usage_source value).
  - system field: array shape + block order preserved unchanged end-to-end when resolved to the
    direct Anthropic adapter — integration-verified against the sibling; a failure is a named
    change-request against anthropic-messages-ingress, never an edit performed by this task.
  - A provider-native error Claude Code's wording-matched auto-retry/auto-compact depends on
    (thinking rejection, thinking-signature rejection, mid-conversation system-message rejection):
    message text reaches the client verbatim inside the EXISTING Anthropic error envelope shape
    (no new envelope) — integration-verified; a failure is a named change-request, not an edit here.
  - Non-Claude failover: gated by a new tenant policy flag allow_non_claude_failover (default false)
    on the EXISTING FallbackModelRouter substitution path ONLY, for a request naming (directly or
    via alias) a Claude model. Default false ->
      403 { "type":"error", "error": {"type":"permission_error","message":"<m>"} }
      "ERR_NO_ELIGIBLE_ANTHROPIC_CANDIDATE"
    refused before any upstream dial, never billed, no usage_records row (mirrors
    RESIDENCY_NO_ELIGIBLE_REGION exactly). true -> the existing resolver behaves exactly as today.
    Does NOT alter the sibling's frozen M6 (an explicitly-named/aliased non-Anthropic model is
    still served translate-through unconditionally, regardless of this flag).

HEAD /  and any other unrecognized best-effort startup probe -> 404 or 405, never 500/hang/redirect.

Schema:
  usage_records — NO new column. raw JSONB additively gains up to 3 optional keys
    (cc_session_id, cc_agent_id, cc_parent_agent_id) — absent on every pre-existing row and on
    every row from a non-Claude-Code-originated request. usage_source's vocabulary is untouched.
  tenants (or plans.feature_flags — exact storage decided at BUILD, §1 ⚠) — one new policy
    field allow_non_claude_failover: bool, default false.
  models (catalog) — NO new column; GET /v1/models reads existing id/active/provider/region plus
    the existing model_groups config / tenant preset store for alias resolution.
  Access pattern: zero new ports; GET /v1/models reuses CompositeKeyAuthenticator + a NEW
    read-only tenant-catalog-entitlement query (§1 M1, the ⚠ flag) — no write path.
```

Glossary deltas:
- `Gateway model discovery`: the `GET /v1/models` surface Claude Code queries (opt-in, `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`) to populate its `/model` picker with a tenant's Claude-aliased, entitled catalog rows — distinct from the OpenAI-wire `/v1/models`-style listing conventions elsewhere, if any, in following Anthropic's exact response/filter shape (`id` must be `claude-`/`anthropic-`-prefixed).
- `Claude Code session/subagent attribution`: the `x-claude-code-session-id`/`x-claude-code-agent-id`/`x-claude-code-parent-agent-id` headers, consumed (never forwarded) and stamped into `usage_records.raw` — explicitly NOT a new credential/principal class; distinct from the existing v39 `agent_oauth` "agent principal" grant store (a per-tenant, cross-session, minted-credential concept), never to be conflated with it.
- `Non-Claude failover opt-in` (`allow_non_claude_failover`): a per-tenant policy flag, default false, gating whether the EXISTING fallback/tier-capacity substitution mechanism may ever choose a non-Anthropic candidate for a request that named (directly or via alias) a Claude model over the Anthropic wire — the disclosed, opt-in form of Hydroa's multi-provider-failover differentiator, distinct from the sibling `anthropic-messages-ingress` M6 (which governs explicitly-requested non-Claude models, always served, unconditionally, regardless of this flag).

Least-sure flag surfaced at freeze: ⚠ [contract] M1's tenant-model-enumeration query for GET /v1/models is new, unverified-at-scale ground (no existing code enumerates a tenant's full permitted catalog); a wrong-shaped or slow implementation degrades gracefully to Claude Code's cached/built-in model list (documented client-side fallback) rather than hard-breaking a session, but would quietly cost Hydroa the "aliased models in the picker" differentiator until fixed. M6/M7's round-trip-fidelity requirements are integration-VERIFIED-not-assumed against the still-unbuilt sibling and will surface as a named change-request there if they fail, per §1.

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

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>

Persona (required): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; name "generic" if no project persona fits yet>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

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
