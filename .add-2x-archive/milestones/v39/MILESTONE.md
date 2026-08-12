# MILESTONE: Headless agent authentication (OAuth device flow)

goal: A coding agent can self-authenticate to a signed-up tenant through an OAuth device-authorization flow, then make billable LLM requests with the token it obtains.
rationale: new-major (intake-confirmed 2026-06-25; Tin chose — via AskUserQuestion — to BUILD a
  headless agent OAuth flow, "accept" = tenant signup, and close on BOTH a stubbed red/green suite AND
  a live double-pass). Relationship to map: EXTENDS the auth pillar (v17/v18 session-JWT hardening, v25
  BYOK, the OIDC/SSO `auth/` module) by adding a credential class it has never had — a HEADLESS agent
  token obtained without a browser. DEPENDS-ON the existing data plane (`/v1/...`), usage metering, and
  `_helios_harness`/`live_helios_smoke` (v34). OVERLAPS no existing milestone: the only OAuth today is
  the browser-redirect OIDC for humans; coding agents authenticate with a hand-pasted API key. This
  milestone removes the human-pasted-key step from the agent journey.
  ⚠ LOWEST-CONFIDENCE: that the OAuth 2.0 Device Authorization Grant (RFC 8628) is the right grant for a
  coding agent (vs PKCE authorization-code with a loopback redirect). Lowest confidence because it hinges
  on whether the agent runs truly headless (CI/container, no browser) — device flow assumes yes. If wrong:
  the grant-store + endpoints rework. MITIGATED by freezing the grant choice FIRST in `agent-oauth-grant-store`
  with PKCE named as the explicit alternative at that contract freeze, before any endpoint is built.
stage: production · status: active · created: 2026-06-25

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Device-authorization grant store: pending authorizations (device_code · user_code · status ·
    expiry · poll interval) + issued agent tokens (access [+ refresh] · tenant_id · user_id · scope ·
    expiry), all secrets hashed at rest. Migration.
  - Public device-authorization endpoint (`POST /oauth/device/authorize`) — rate-limited, bounded.
  - User-facing device approval (a signed-up, logged-in user approves a pending user_code, binding it
    to their tenant/user).
  - Agent token endpoint (`POST /oauth/token`, device_code grant) — RFC 8628 polling semantics; mints
    the agent access token (+ refresh).
  - Data-plane authentication seam: `/v1/...` accepts the agent access token, fail-closed, expiry-enforced,
    producing correct billable usage rows.
  - End-to-end harness: stubbed red/green for the full journey AND a live double-pass through Envoy/TLS.
Out:
  - PKCE authorization-code / loopback-redirect flow — OUT unless chosen over device-flow at the
    `agent-oauth-grant-store` freeze.
  - Dynamic client registration (RFC 7591) — a single implicit/pre-registered agent client only.
  - Standalone token introspection/revocation endpoints (RFC 7009/7662) — expiry-based invalidation only;
    explicit revoke endpoint/UI deferred.
  - Dashboard UI to list/manage/revoke agent tokens — a separate FE milestone.
  - Fine-grained OAuth scopes — one coarse scope bound to tenant/user; no per-resource scoping.
  - A real coding-agent binary (helios-mono) — the harness SIMULATES the agent client.
  - Any new LLM provider or data-plane routing change; the tenant signup flow is unchanged.

## Shared decisions & glossary deltas   (living — every task must honor these)
- AGENT ACCESS TOKEN (NEW glossary): a THIRD credential class, distinct from the human *session JWT*
  (v18) and the tenant *API key*. Carries `tenant_id` + `user_id` + one coarse scope; data-plane authz
  is FAIL-CLOSED; expiry enforced server-side.
- DEVICE AUTHORIZATION GRANT / device_code / user_code / verification URI (NEW glossary, RFC 8628): the
  headless grant a coding agent runs. Grant choice is the riskiest contract — freeze first.
- HASH-AT-REST INVARIANT (EXTEND): agent OAuth secrets (device_code, access_token, refresh_token) are
  stored ONLY as SHA-256 hashes at rest — mirrors the API-key invariant (high-entropy secret, hot-path
  authz; argon2 stays for passwords). Plaintext token returned ONCE at mint.
- ADDITIVE-ONLY: existing API-key auth, OIDC/SSO, and the session-JWT path stay BYTE-IDENTICAL. Agent
  OAuth is a new surface; no existing credential path is weakened.
- PUBLIC PRE-AUTH ENDPOINTS: device-authorize + token are unauthenticated → MUST be rate-limited, bounded,
  and designed-for-failure (timeout + bounded retry + circuit-breaker per the IO rule). Anti-abuse on
  user_code guessing = short expiry + rate-limit on poll/approve.
- SECURITY HARD-STOP: every task here touches authentication → each escalates at verify for Tin's
  approval (per the `add` skill `run.md`; auth residue always escalates, never auto-PASS).

## Shared / risky contracts (freeze these first)
- Grant choice (device-authorization RFC 8628 vs PKCE loopback) + token model (opaque-hashed vs JWT;
  refresh yes/no; access/refresh lifetimes)            -> owning task `agent-oauth-grant-store`
- Approval authz + device↔user/tenant binding rules     -> owning task `device-approval-flow`
- Data-plane credential seam (agent token coexists with / replaces / maps-to-ephemeral API key) -> owning task `agent-token-authn-seam`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] agent-oauth-grant-store    depends-on: none                                  — Domain model + migration: pending device authorizations + issued agent tokens, secrets hashed at rest. FREEZES the grant choice + token model. (FROZE device-flow RFC 8628 over PKCE.)
- [x] device-authorization-endpoint depends-on: agent-oauth-grant-store            — `POST /oauth/device/authorize`: public, rate-limited; returns device_code · user_code · verification_uri · interval · expires_in (RFC 8628 §3.2).
- [x] device-approval-flow       depends-on: agent-oauth-grant-store               — A signed-up, logged-in user approves a pending user_code, binding it to their tenant/user. FREEZES approval authz + binding. (Tin: any member, bind to JWT not body.)
- [x] agent-token-endpoint       depends-on: device-authorization-endpoint, device-approval-flow — `POST /oauth/token` (device_code grant): RFC 8628 polling (authorization_pending · slow_down · expired_token · access_denied) → mints the agent access token (+ refresh). Bounded + rate-limited.
- [x] agent-token-authn-seam     depends-on: agent-token-endpoint                  — `/v1/...` accepts the agent access token (fail-closed, expiry-enforced); produces a correct billable usage row. FREEZES the data-plane credential seam. (Tin: per-token $100/mo budget cap.)
- [x] agent-oauth-harness-e2e    depends-on: agent-token-authn-seam                — Stubbed red/green (full journey) AND live double-pass through Envoy/TLS over signup → device-authorize → approve → token → agent request → usage. (Also widened the edge /internal/authz gate.)

## Exit criteria (observable; map each to the task that delivers it)
- [x] A coding agent can request device_code + user_code from a public device-authorization endpoint and poll a token endpoint that returns the documented RFC 8628 states   (← device-authorization-endpoint, agent-token-endpoint)
- [x] A signed-up, logged-in user can approve a pending device authorization, binding it to their tenant; only an approved authorization mints a token   (← device-approval-flow, agent-oauth-grant-store)
- [x] Agent OAuth secrets exist only as hashes at rest, tokens expire and are enforced fail-closed, and existing API-key/SSO/session paths stay byte-identical   (← agent-oauth-grant-store, agent-token-authn-seam)
- [x] An agent access token authenticates a `/v1/...` request and produces a correct billable usage row   (← agent-token-authn-seam)
- [x] The full journey (signup → device authorize → approve → token → agent request → usage) passes a stubbed red/green suite AND a live double-pass through Envoy/TLS   (← agent-oauth-harness-e2e)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : NEW `agent_oauth/` module (domain/application/infrastructure/api) + migration `b3d5f7a9c1e4` — device
  authorization + agent token store (sha256@rest), public `POST /oauth/device/authorize` + `POST /oauth/token`
  (RFC 8628 §3.2/§3.5), authed `POST /oauth/device/{approve,deny}`; NEW `CompositeKeyAuthenticator` widening BOTH
  the in-process /v1 deps AND the edge `/internal/authz` ext_authz gate to accept agent tokens (fail-closed,
  byte-identical 401, bills key_id=token_id); config knobs (device ttl/interval/scope/rpm, access/refresh ttl,
  token_rpm, default_budget_usd=$100/mo cap). No existing credential path changed.
- dashboard : untouched (device approval UI deferred — a separate FE milestone, see Out).
- tooling / skill / book : untouched. NEW ops/test artifacts: `infra/docker-compose.e2e.v39.yml`,
  `scripts/live_v39_verify.py`, `scripts/v39_upstream_stub.py`.

### Cross-task evidence   (one row per task)
- agent-oauth-grant-store : gate=PASS · froze device-flow(RFC 8628)+opaque-hashed token model · residue=none
- device-authorization-endpoint : gate=PASS · public, bounded 4KB + per-IP fail-open limiter · residue=none
- device-approval-flow : gate=PASS · Tin froze any-member-bind-to-JWT · residue=none
- agent-token-endpoint : gate=PASS · refute UPHELD@0.92 · single-use atomic mint, RFC §3.5 states · residue=greenlet-cov delta
- agent-token-authn-seam : gate=PASS · refute UPHELD@0.91 · composite at 5 /v1 entry points, $100 cap · residue=none
- agent-oauth-harness-e2e : gate=PASS · refute UPHELD@0.83 (NB-1 fixed) · LIVE double-pass 13/13 ×2 re-run FIRST-HAND
  by orchestrator (run_id 1782402015 + 1782402020, exit 0, clean teardown) through the real Envoy edge :8080 (ext_authz
  /internal/authz path; HTTP listener, not the :8443 TLS one) · residue=dead get_authz_use_case (delta)
- whole-suite: 1730 passed, 0 failed, 88.14% (clean run); all 3 BE security HARD-STOPs Tin-approved.

### Goal met?
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: A coding agent self-authenticates to a signed-up tenant via RFC 8628 device flow + makes billable LLM
  requests — PROVEN by the live double-pass (scripts/live_v39_verify.py 13/13 ×2): signup → /oauth/device/authorize →
  approve → /oauth/token mint → /v1/chat/completions through the real Envoy edge (:8080) → billed usage row
  (tenant_id, key_id=token_id); the stub upstream confirmed it accepted the chat request (accepts=1, not short-circuit).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit each task + fold on a `feat/v39-agent-oauth` branch (gateway auth + .add bookkeeping)
- [ ] open PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]])
- [ ] v39 joins the releasable set; bundle into the next release cut when Tin calls it (release.md)
