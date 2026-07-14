# MILESTONE: Agent-era gateway — MCP governance, tool metering, Messages ingress

goal: An enterprise can front its agent fleet (Claude Code / Cowork / Agent SDK / MCP clients) through Hydroa — a native /v1/messages-compatible ingress, MCP connector allow-lists, per-tool-call metering, and agent-as-principal governance — inheriting guardrails, budgets, logs, and invoices.
rationale: roadmap M3 of 3 (Tin-confirmed 2026-07-12) — Track D + the NEW anthropic-messages-ingress task (Anthropic officially supports gateway-fronting since Apr 2026; verified absent in Hydroa). Sequenced after monetization-core because tool metering bills through its rails. EXPANDED 2026-07-14 (Tin-approved R1, docs/roadmap/2026-07-14-enterprise-roadmap.html): + claude-gateway-protocol-compat (Anthropic shipped its own single-org Claude-only "Claude apps gateway" Jun 29–Jul 2 2026 and is PUBLISHING the protocol — Hydroa becomes the multi-tenant multi-provider implementation), + agent-identity-governance (agent identity is a named 2026 budget line: Okta GA Apr 30, Auth0 May 21), + agents-console (UDD). Extends: monetization-core (billing rails), v39 device-OAuth (agent principal substrate), logs-explorer-guardrails-v2 (session tracing surface). Target release 0.9.0 "Agent gateway", early Aug 2026.
stage: mvp · status: active · created: 2026-07-12T07:38:33+00:00
release: 0.9.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  native `/v1/messages` ingress (+ token counting) with billing parity; Claude apps gateway protocol compatibility (Claude Code fronts Hydroa first-class); MCP connector egress passthrough with per-tenant allow/deny lists (fail-closed) + session tracing; per-tool-call metering ($/1k-query pricing units → usage_records → invoice lines); agent-as-principal identity on device-OAuth (named agents, per-agent budgets/rate limits, universal kill switch); Agents console (UDD loop).
Out: hosting agent runtimes/sandboxes (no Managed-Agents competitor — gateway-first standing decision); A2A protocol support; an MCP server marketplace/directory; prompt registry / evals / shadow routing (R3); native ingress dialects beyond Anthropic (Gemini-native etc.); session-hour billing of runtimes Hydroa doesn't host; agent-to-agent delegation chains.

UI/UX in scope (agents-console): information architecture = new top-level Agents nav page (directory → session explorer → policy). Interaction patterns = Logs-Explorer detail-drawer idiom for session traces; ZDR typed-confirm idiom for the kill switch (destructive, plain-language consequence line). Visual = Aurora tokens; signature element = the agent identity card (principal, owner, spend, last-seen, live/killed state) as the directory unit. Accessibility floor WCAG 2.2 AA, axe-checked. Runs the full DESIGN.md UDD loop before build.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Fail-closed MCP default**: a call to an unlisted MCP server gets a structured `problem+json` 403 refusal + audit event — never warn-and-allow (mirrors the residency refuse-not-reroute idiom).
- **One billing path**: every new metered unit (tool calls, ingress traffic) bills through the ONE shared rate-card resolver into `usage_records` — never a parallel ledger; invoice lines drill to usage rows like all others.
- **Agent principal rides device-OAuth**: agent identity extends the RFC 8628 grant store (v39) — no new credential class; kill switch = revocation of all grants/sessions for the principal, fail-closed at both authn seams (in-process + Envoy ext_authz).
- **Ingress is translation-only**: after `/v1/messages` wire translation, requests traverse the SAME governance → router → recorder path byte-identically; no Anthropic-dialect bypass of guardrails/budgets/residency.
- **Security tasks get TWO independent adversarial verifies** (mcp-connector-passthrough, agent-identity-governance) — M2 dual-verify lesson; a PASS is reversible on a security finding.
- Glossary deltas: **agent principal** (an identity-bearing non-human actor scoped to a tenant), **MCP allow-list** (tenant-admin policy naming permitted MCP servers), **tool-call pricing unit** ($/1k-query metering dimension).

## Accepted v1 residuals   (verify-surfaced; disclose at milestone close for Tin's risk-acceptance)
- **tool-call-metering exactly-once is fail-open under Redis outage**: the shipped dedupe is a Redis SETNX in `MeteringToolCallObserver` (not the contract's "recorder ON CONFLICT" — recorder's frozen signature has no call_id passthrough), so a genuine duplicate `record()` during a Redis outage double-bills (narrow window; fail-open chosen deliberately — a missed bill judged worse than a rare double-bill; tested). Follow-on fix: a DB-level unique backstop on a call_id-derived tag. NOT a 0.9.0 blocker (verify's own recommendation). Corrects the tool-call-metering §3 "structural, no residual" wording.
- **ingress header/system-array fidelity gaps** (protocol-compat CRs vs frozen ingress): `anthropic-beta`/`anthropic-version` headers are captured/validated but not yet threaded to the direct-Anthropic dial (M2/M3); multi-block `system` array collapses (M6, xfail-proven). Disclosed; fix is a CR back to `anthropic-messages-ingress`.

## Shared / risky contracts (freeze these first)
- `/v1/messages` wire contract (request/response/SSE event mapping + usage frame + error shape) -> owning task `anthropic-messages-ingress`
- MCP allow-list policy shape + refusal error contract -> owning task `mcp-connector-passthrough`
- agent principal model (identity fields, budget/limit attachment, kill-switch semantics) -> owning task `agent-identity-governance`
- tool-call `pricing_unit` + usage_record fields -> owning task `tool-call-metering`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] anthropic-messages-ingress       depends-on: none — native /v1/messages (+ count_tokens) ingress: accept Anthropic-wire requests/SSE, translate at the edge, billing parity through the shared recorder.
- [ ] mcp-connector-passthrough        depends-on: none — MCP egress proxy with per-tenant allow/deny lists, fail-closed on unlisted servers, session tracing into the Logs Explorer. [sensitivity: security · dual-verify]
- [ ] agent-identity-governance        depends-on: none — agent-as-principal on device-OAuth: named agent identities, per-agent budgets/rate limits, universal kill switch. [sensitivity: security · dual-verify]
- [ ] claude-gateway-protocol-compat   depends-on: anthropic-messages-ingress — implement Anthropic's published gateway protocol so Claude Code fronts Hydroa first-class (multi-tenant, non-Claude failover as the differentiator).
- [ ] tool-call-metering               depends-on: mcp-connector-passthrough — per-tool-call pricing units ($/1k-query via the pricing_unit dispatcher) → usage_records → invoice lines.
- [ ] agents-console                   depends-on: mcp-connector-passthrough, agent-identity-governance, tool-call-metering — Agents console (UDD): agent directory, session explorer, MCP allow-list management, kill switch with typed confirm.

## Exit criteria (observable; map each to the task that delivers it)
- [x] Claude Code completes a real session through Hydroa via /v1/messages with an accurate usage record + invoice line        (← anthropic-messages-ingress, claude-gateway-protocol-compat — wire+usage path test-proven; live-edge smoke = post-deploy)
- [x] An agent's call to an unlisted MCP server is refused fail-closed with a structured error and an audit event, zero egress dials        (← mcp-connector-passthrough, dual-verify CLEAN)
- [x] A metered tool call lands as an invoice line priced through the shared rate-card resolver        (← tool-call-metering; fail-open-under-Redis-outage residual disclosed)
- [x] A tenant admin kills a named agent and every one of its sessions/tokens stops authenticating at both authn seams        (← agent-identity-governance, killed_at fail-closed dual-verified on both seams)
- [x] A tenant admin can see, trace, govern, and kill agents from the Agents console, axe-clean (WCAG 2.2 AA)        (← agents-console)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched (engine recorded phase/gate only).
- skill   : untouched.
- book    : untouched.
- gateway (BE): native `/v1/messages` (+count_tokens) ingress w/ edge translation → shared governance/router/recorder; Claude apps gateway protocol-compat (GET /v1/models discovery, verbatim anthropic-beta/version passthrough, x-claude-code-session/agent-id attribution); MCP connector egress passthrough (fail-closed allow-lists, SSRF IP-pin, session tracing) + budget gate; per-tool-call metering (pricing_unit → usage_records → invoice lines); agent-as-principal on device-OAuth (named agents, per-agent budgets, universal kill switch). Migration chain head a2b4c6d8e0f1 (agent_principal attribution) → f5a8c1e3b6d9 (via eu-ai-act). App imports 211 routes.
- dashboard (FE): Agents console (directory / session explorer / MCP allow-list mgmt / kill switch typed-confirm), Aurora tokens, axe-clean.

### Cross-task evidence   (one row per task)
- anthropic-messages-ingress : gate=PASS · tests=29 green · residue=raw thinking/budget_tokens not yet read by egress adapter; multi-block system array collapses (M6) — both in Accepted v1 residuals, fix = CR back to ingress.
- mcp-connector-passthrough : gate=PASS [security · dual-verify] · tests=79 green · residue=**PII relay leak found + CLOSED across 3 rounds** (top-level text → resource.text → structural fields; final fix 04d93c6 = output-based post-mask verification scan, closes the whole class). Budget-governance CR added (§3→v3, 402 refuse-never-bill); RPM/TPM + credit-hold deferred (no MCP settle hook). Dual-verify CLEAN on SSRF/DNS-rebind/allow-list/cross-tenant.
- agent-identity-governance : gate=PASS [security · dual-verify] · tests=24 + 217 sibling green · residue=**kill-switch bypass + cost-recovery budget bypass found + CLOSED** (killed_at fail-closed in resolve_access_token; agent_principal_id threaded through recorder/flusher/recovery_sweep/cost_recovery, migration a2b4c6d8e0f1). 2 low-pri todos: #40 agent tokens can't auth /v1/realtime (pre-existing); #41 chat post-correction live test.
- claude-gateway-protocol-compat : gate=PASS · tests=63 green (w/ ingress+catalog) · residue=2 CRs vs frozen ingress (anthropic-beta/version header channel to direct-Anthropic dial M2/M3; system-array collapse M6, xfail-proven) — in Accepted v1 residuals.
- tool-call-metering : gate=PASS · tests green · residue=**exactly-once is fail-open under Redis outage** (SETNX dedupe, not recorder ON CONFLICT — deliberate: a missed bill judged worse than a rare double-bill; tested) — in Accepted v1 residuals, follow-on = DB-level call_id unique backstop.
- agents-console : gate=PASS [UDD] · tests=dashboard suite green, axe-clean (WCAG 2.2 AA) · residue=ManageTokens enumerates only ATTACHED tokens (follow-on CR).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion satisfied by a Cross-task evidence row (criterion 1 ← ingress+protocol-compat wire path, test-proven end-to-end incl. usage-record parity; criterion 2 ← mcp; criterion 3 ← tool-call-metering; criterion 4 ← identity kill-switch dual-verify; criterion 5 ← agents-console axe-clean). NOTE: criterion 1's "a real Claude Code session" is proven by the 29+63 wire/usage tests; a live Claude-Code-against-the-deployed-edge smoke is a post-deploy validation (setup: ANTHROPIC_BASE_URL→edge + Hydroa key).
- goal: front an agent fleet through Hydroa with native /v1/messages ingress, MCP allow-lists, per-tool-call metering, and agent-as-principal governance inheriting guardrails/budgets/logs/invoices — proven by 206→211-route integration with all 6 tasks green together, single alembic head, and the two security tasks dual-verified with every reproduced defect healed.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] full BE suite + dashboard suite green pre-merge on the milestone branch (cross-task drift check — M1/M2 lesson)
- [ ] open PR from the Close ship-review; admin-merge on local evidence past the org-billing 0-step CI block if needed
- [ ] feeds release 0.9.0 "Agent gateway" alongside eu-ai-act-readiness (human runs tag/publish/deploy per release.md)
