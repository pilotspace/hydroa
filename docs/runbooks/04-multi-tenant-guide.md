# 04 — Multi-tenant guide

How Hydroa isolates tenants, who can do what (RBAC), how people and machines
authenticate, and how per-tenant resources (keys, budgets, limits, provider keys)
are scoped. Read this if you’re designing tenancy or reasoning about the security
boundary.

- [Tenancy model](#tenancy-model)
- [Isolation — proven live](#isolation--proven-live)
- [Roles & permissions](#roles--permissions)
- [User auth: password login](#user-auth-password-login)
- [SSO / OIDC](#sso--oidc)
- [API keys](#api-keys)
- [Agent tokens (device flow)](#agent-tokens-device-flow)
- [Per-tenant resource scoping](#per-tenant-resource-scoping)
- [Operator cross-tenant access](#operator-cross-tenant-access)
- [End-to-end: how a request is authenticated](#end-to-end-how-a-request-is-authenticated)

---

## Tenancy model

A **tenant** is the top-level isolation boundary (an organization), identified by a
`uuid`. **Every** domain row — users, API keys, agent grants, usage, OIDC config,
routing config, audit events — carries a `tenant_id` and is created with it.

- A **user** (`id`, `tenant_id`, `email`, `password_hash`, `role`) belongs to
  **exactly one** tenant, fixed at creation; only an admin role-assignment changes
  the role (never the tenant).
- The **first** user of a tenant is created as **owner** by signup.
- Isolation is enforced in **every repository query**: each `WHERE` clause includes
  `tenant_id = <caller’s tenant>`. A row from another tenant resolves to **0 rows**,
  which surfaces as the same `404` as a missing row — no enumeration oracle.

```
tenant A ──┬── owner, admins, members, viewers …
           ├── API keys (sk-…)            scoped to A
           ├── agent tokens               scoped to A
           ├── provider keys (BYOK)       scoped to A, encrypted
           ├── budgets / rate limits      scoped to A
           └── usage / audit / alerts     scoped to A
tenant B ──┴── …completely separate…
```

The **only** surface that deliberately crosses tenants is `/ops/*` (operator
mTLS) — see [below](#operator-cross-tenant-access).

---

## Isolation — proven live

From the live verification (two tenants, A and B):

```
B sees its OWN usage         → requests=0  cost=0          (A's traffic invisible to B)
B sees its OWN keys          → 0 keys                      (A's key invisible to B)
B deletes A's key_id         → HTTP 404                    (no leak, not 403)
A still has its key           → 1 key                      (B could not touch it)
```

A cross-tenant reference is indistinguishable from a non-existent one. This holds
for keys, memories, conversations, artifacts, video jobs, teams, and users.

---

## Roles & permissions

Six roles (`tenants/domain/entities.py`), a frozen permission matrix
(`tenants/domain/authz.py`). `owner` holds every permission (enforced by an
import-time guard). `member` holds **none** of the admin permissions — it is a
pure data-plane principal.

| Permission | owner | admin | operator | billing_admin | viewer | member |
|------------|:---:|:---:|:---:|:---:|:---:|:---:|
| `keys_manage` | ✔ | ✔ | ✔ | — | — | — |
| `routing_manage` | ✔ | ✔ | ✔ | — | — | — |
| `catalog_sync` | ✔ | ✔ | ✔ | — | — | — |
| `budgets_manage` | ✔ | ✔ | — | ✔ | — | — |
| `usage_read` | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| `ops_read` | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| `members_manage` | ✔ | ✔ | — | — | — | — |
| `audit_read` | ✔ | ✔ | ✔ | — | — | — |
| `provider_secrets` (BYOK) | ✔ | — | — | — | — | — |
| `security_config` (SSO etc.) | ✔ | — | — | — | — | — |

`require_permission(perm)` returns `403 ERR_AUTH_FORBIDDEN` when the caller’s role
lacks `perm`. **Only the owner** can manage BYOK provider keys and security config.

**Role-assignment guards:** you can’t change your own role; an `admin` can’t grant
`owner`/`admin`; the target must be in your tenant.

---

## User auth: password login

```
signup  →  create tenant + owner (argon2id password hash)
login   →  verify password (constant-time; dummy-hash on miss to kill timing oracle)  →  JWT
me      →  decode JWT  →  {user_id, tenant_id, email, role}
```

The **JWT** (HS256, secret `GATEWAY_JWT_SECRET`, TTL `GATEWAY_JWT_TTL_SECONDS`)
carries — verified live by decoding a real token:

| Claim | Value |
|-------|-------|
| `sub` | user_id |
| `tenant_id` | tenant uuid |
| `role` | `owner` / `admin` / … |
| `email` | user email |
| `iat` / `exp` | issued / expiry (server-owned; client cannot extend) |
| `iss` | `ai-proxy` (must match Envoy’s `jwt_authn` config) |

Transport: `Authorization: Bearer <jwt>` for the admin API; the dashboard stores it
as the `ai_proxy_session` HttpOnly cookie and its BFF re-attaches it as a Bearer.

> The same secret signs the JWT **and** seeds Envoy’s JWKS, so the edge can
> validate `/admin/*` tokens without calling the gateway. Keep them in sync.

---

## SSO / OIDC

Per-tenant OIDC lets members log in with corporate identity. Configured by the
owner ([Admin §10](./02-admin-guide.md#10-sso--oidc)); requires
`GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY`.

```
GET /auth/oidc/login?domain=example.com
  → resolve tenant config by email domain
  → set short-lived oidc_state / oidc_nonce / oidc_tenant_id cookies (HttpOnly, 300s)
  → 302 to the IdP authorize URL (state + nonce)

GET /auth/oidc/callback?code=…&state=…
  → re-resolve config from the PINNED oidc_tenant_id cookie (never from query)
  → verify state (hmac.compare_digest), exchange code server-side, validate id_token
    (iss, aud, exp, nonce; RS256 via JWKS if configured — alg=none/HS256 rejected)
  → provision/lookup user (NEW SSO users are always role=member)
  → mint ai_proxy_session JWT (same claims as password login), clear the 3 cookies
  → 302 to GATEWAY_OIDC_POST_LOGIN_REDIRECT
```

The `oidc_tenant_id` cookie is pinned at `/login` and the callback never derives
the tenant from query params — this defeats tenant-confusion. SSO never
auto-grants owner/admin.

---

## API keys

Format: `sk-<key_id_hex>.<secret>` — `key_id` is a UUIDv7, `secret` is
`token_urlsafe(32)`. The **secret is SHA-256-hashed at rest**; the plaintext is
shown once on create/rotate and never stored or logged. List responses expose only
a safe `prefix` (`sk-<first 8 hex of key_id>`).

Each key carries governance enforced on the hot path with **zero extra DB reads**
(the fields ride along in the auth result): `monthly_budget_usd`,
`soft_budget_usd`, `expires_at`, `model_allowlist`, `rpm_limit`, `tpm_limit`,
`team_id`, `cache_enabled`. Bound to the tenant by a `NOT NULL FK` — a key_id from
another tenant is `404`. Lifecycle in [Admin §2](./02-admin-guide.md#2-api-keys).

---

## Agent tokens (device flow)

For headless agents. The RFC 8628 device flow mints an **opaque, expiring** token
(not a JWT, not an `sk-` key) bound to the approving user’s tenant. Full sequence
and live capture: [API client §Agent OAuth](./03-api-client-guide.md#agent-oauth-device-flow).

Key properties:
- The `(tenant_id, user_id)` binding comes from the **approver’s JWT**, never the
  request body — cross-tenant grants are impossible.
- Tokens are SHA-256-hashed at rest; single-use mint; optional 30-day refresh.
- Each token has its own **monthly spend cap**
  (`GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD`, default $100).
- Accepted on `/v1/chat/completions`; resolved by the `CompositeKeyAuthenticator`
  (non-`sk-` prefix → agent-token path).

---

## Per-tenant resource scoping

| Resource | Scope | Enforcement |
|----------|-------|-------------|
| API keys | tenant (optionally a team) | FK + per-query `tenant_id` filter |
| Budgets | tenant **and** team **and** key | `402` on any breach; ledger-based |
| Rate limits | per key (`rpm`/`tpm`) | Redis token buckets; `429 + Retry-After` |
| Bandwidth pacing | per key (tokens/s) | Redis bucket; `503 + Retry-After` |
| Provider keys (BYOK) | tenant | Fernet-encrypted; resolver keyed by `(tenant, provider)` |
| Catalog model enable/disable | tenant override | default-enabled if no row |
| Guardrails / cache | tenant | merged config on the key’s auth result |
| Usage / audit / alerts | tenant (alerts also see platform rows) | per-query filter |

---

## Operator cross-tenant access

`/ops/*` is the **only** surface that breaks isolation, for platform operators
running fleet-wide reconciliation. It is **not** reachable with a tenant JWT.

- **Auth = mTLS.** Envoy terminates TLS, validates the client cert, and forwards
  the leaf-cert identity via the `x-forwarded-client-cert` (XFCC) header.
- The gateway’s `OpsCertVerifier` parses the **leaf** cert fingerprint and checks
  it against `GATEWAY_OPS_CERT_FINGERPRINTS` (CSV of SHA-256 hex).
- **Fail-closed:** an empty allowlist authorizes nobody.
- A valid **tenant** JWT → `403 ERR_OPS_FORBIDDEN`; anything else → `401
  ERR_OPS_UNAUTHORIZED` (byte-identical, no oracle).
- Trust rests on topology: the app is only reachable through Envoy, which strips
  any client-supplied XFCC before forwarding.

Current operator endpoint: `GET /ops/reconciliation` (cross-tenant drift, read-only,
30 s bounded). See [Admin §12](./02-admin-guide.md#12-operator-cross-tenant-reconciliation).

---

## End-to-end: how a request is authenticated

**Data plane (`/v1/*`):**

```
1. Envoy ext_authz: every /v1/* call first hits POST /internal/authz on the gateway,
   forwarding the client's Authorization: Bearer header. failure_mode_allow=false.
2. CompositeKeyAuthenticator:
     starts with "sk-"  → SHA-256 the secret, fetch api_keys row by key_id,
                          hmac.compare_digest vs stored hash, check revoked/expired
     otherwise          → SHA-256 the token, resolve_access_token (server-owned `now`),
                          check active/expired/revoked  →  agent-token binding
   Both failure paths raise the SAME 401 (anti-enumeration).
3. On success Envoy forwards x-tenant-id / x-key-id upstream; the proxy enforces
   budget / model-allowlist / rpm / tpm from the auth result — zero extra DB reads.
```

**Control plane (`/admin/*`):**

```
1. Envoy jwt_authn validates the HS256 signature at the edge (login + signup exempt),
   forward=true so the gateway re-verifies.
2. The endpoint's require_permission(...) dependency checks the role's permission set.
3. Every query is tenant-scoped by the JWT's tenant_id.
```

**The boundary in one line:** the gateway is never public; Envoy authenticates
both planes; the tenant is derived from a verified credential, never from request
input; and `/internal/*` (incl. `/internal/authz`) is hard-blocked at the edge.

---

**Back to:** [README](./README.md) · [01 Getting started](./01-getting-started.md) ·
[02 Admin guide](./02-admin-guide.md) · [03 API client guide](./03-api-client-guide.md)
