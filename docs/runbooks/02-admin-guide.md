# 02 — Admin guide

Everything a **tenant admin/owner** does to run a tenant: onboard, mint API keys,
set budgets and rate limits, bring provider keys, manage members and teams,
configure routing/guardrails/SSO, and read usage, audit, and SLO surfaces.

Two ways to do all of it:

- **Dashboard** — `make edge-dashboard` → `http://localhost:3000`, log in, use the
  pages under `/app/*` (`keys`, `spend`, `usage`, `members`, `teams`, `routing`,
  `audit`, `alerts`, `slo`, `health`, `models`, `settings`, …). The dashboard is a
  thin BFF over the same admin API below.
- **Admin API** — `/admin/*` with a tenant JWT. Shown here because it’s scriptable
  and authoritative. All examples were **verified live** against `make edge`.

> **Auth model.** `/admin/*` needs `Authorization: Bearer <JWT>` (from
> `/admin/auth/login`). There are six roles (`owner`, `admin`, `operator`,
> `billing_admin`, `viewer`, `member`) — not a strict hierarchy but a permission
> matrix: `owner` holds everything, `member` holds no admin permissions, and
> `operator`/`billing_admin`/`viewer` are scoped subsets. Each endpoint notes the
> permission it needs. Envoy validates the JWT at the edge for `/admin/*`; signup +
> login are exempt. Full matrix:
> [Multi-tenant guide → Roles & permissions](./04-multi-tenant-guide.md#roles--permissions).

> **`operator` and the legacy guard.** Several write endpoints (`/admin/keys`,
> `/admin/models`, `/admin/cache`, `/admin/guardrails`, `/admin/teams`) and catalog
> sync share a dependency historically named `require_owner_or_admin` — but it is
> implemented as a **permission** check (`keys_manage` / `catalog_sync`), not a literal
> owner/admin check. Because the matrix grants **`operator`** those permissions,
> operator can use these endpoints too. The tables below list the **permission**, not
> the old name — `keys_manage`, `catalog_sync`, and `routing_manage` all resolve to
> **owner / admin / operator**.

- [0. Set `$E` and a JWT](#0-set-e-and-a-jwt)
- [1. Onboard a tenant (signup → login → me)](#1-onboard-a-tenant)
- [2. API keys (create / list / rotate / revoke)](#2-api-keys)
- [3. Budgets & spend caps](#3-budgets--spend-caps)
- [4. Model catalog](#4-model-catalog)
- [5. BYOK — bring your own provider keys](#5-byok--bring-your-own-provider-keys)
- [6. Members & roles](#6-members--roles)
- [7. Teams](#7-teams)
- [8. Routing configuration](#8-routing-configuration)
- [9. Guardrails & cache](#9-guardrails--cache)
- [10. SSO / OIDC](#10-sso--oidc)
- [11. Observability: usage, spend, reconciliation, audit, alerts, SLO, health](#11-observability)
- [12. Operator (cross-tenant) reconciliation](#12-operator-cross-tenant-reconciliation)
- [Admin endpoint reference](#admin-endpoint-reference)

---

## 0. Set `$E` and a JWT

```bash
E=http://127.0.0.1:8080
```

You’ll get the JWT in step 1; reuse it as `$JWT` everywhere below.

---

## 1. Onboard a tenant

`POST /admin/auth/signup` creates the tenant **and** its first user as **owner**.
Then `POST /admin/auth/login` mints the session JWT.

```bash
# Sign up (creates tenant + owner). Password min length 10. Email must be valid.
curl -s -X POST $E/admin/auth/signup -H 'content-type: application/json' \
  -d '{"tenant_name":"Acme","email":"owner@example.com","password":"correct-horse-battery-42"}'
# → {"tenant_id":"019f09ed-7220-74c4-…","user_id":"019f09ed-7220-7ce5-…"}

# Log in → JWT
JWT=$(curl -s -X POST $E/admin/auth/login -H 'content-type: application/json' \
  -d '{"email":"owner@example.com","password":"correct-horse-battery-42"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")

# Who am I?
curl -s $E/admin/auth/me -H "authorization: Bearer $JWT"
# → {"user_id":"…","tenant_id":"…","email":"owner@example.com","role":"owner"}
```

> **Gotcha (verified):** `email` is validated with `EmailStr`. Reserved TLDs like
> `@acme.test` are **rejected** with `422 ERR_PAYLOAD_INVALID`. Use a real domain
> (`@example.com`).

The JWT is an HS256 token carrying `sub`, `tenant_id`, `role`, `email`, `iat`,
`exp`, `iss=ai-proxy`. Default lifetime 24 h.

---

## 2. API keys

API keys (`sk-…`) are what your apps use to call `/v1/*`. They’re tenant-scoped
and carry their own governance (budget, rate limits, model allowlist).

```bash
# Create — the plaintext key is returned EXACTLY ONCE. Store it now.
curl -s -X POST $E/admin/keys -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' \
  -d '{"name":"prod-app","monthly_budget_usd":"25.00","rpm_limit":60}'
```
```json
{
  "key_id": "019f09ed-afd4-7b51-a8a8-fdbb7bea2784",
  "name": "prod-app",
  "key": "sk-019f09edafd47b51a8a8fdbb7bea2784.YsBie9rhV_Og_…",
  "monthly_budget_usd": "25.00", "soft_budget_usd": null, "expires_at": null,
  "model_allowlist": null, "rpm_limit": 60, "tpm_limit": null,
  "team_id": null, "cache_enabled": false
}
```

Optional create/update fields: `soft_budget_usd` (warning threshold ≤ hard),
`expires_at` (ISO-8601), `model_allowlist` (list of model IDs), `tpm_limit`,
`team_id`, `cache_enabled`.

```bash
# List (never returns the secret — only a safe prefix)
curl -s $E/admin/keys -H "authorization: Bearer $JWT"
# → [{"key_id":"…","name":"prod-app","prefix":"sk-019f09ed","rpm_limit":60,
#     "monthly_budget_usd":"25.00", …}]

# Update governance (null clears a field, absent leaves it unchanged)
curl -s -X PATCH $E/admin/keys/$KEY_ID -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"rpm_limit":120,"model_allowlist":["google/gemini-2.5-flash-lite"]}'

# Rotate (atomic: revoke old, issue new; returns new plaintext once)
curl -s -X POST $E/admin/keys/$KEY_ID/rotate -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{}'
# → {"new_key_id":"…","superseded_key_id":"…","key":"sk-…"}

# Revoke (soft-delete)
curl -s -X DELETE $E/admin/keys/$KEY_ID -H "authorization: Bearer $JWT"   # → 204
```

- Secret is **SHA-256-hashed at rest**, never stored or logged in plaintext.
- A key_id from another tenant returns **404**, not 403 (no enumeration leak).
- `key.create` / `key.rotate` / `key.revoke` are written to the [audit log](#11-observability).

---

## 3. Budgets & spend caps

There are **three** budget layers — tenant, team, key — all enforced on the hot
path. A request that would breach any of them returns
`402 ERR_BUDGET_EXCEEDED`.

```bash
# Tenant monthly ceiling
curl -s -X PUT $E/admin/budget -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"budget_usd_monthly":"100.00"}'

# Read it back (spent comes from the Postgres ledger, not a Redis counter)
curl -s $E/admin/budget -H "authorization: Bearer $JWT"
# → {"budget_usd_monthly":"100.00","spent_usd_month":"0.00000468"}
```

- Set `null` to clear the ceiling. Negatives / non-decimal → `422`.
- `budgets_manage` permission (owner / admin / billing_admin).
- Per-key budgets are set on the key itself (§2); per-team budgets on the team (§7).

---

## 4. Model catalog

The catalog is the list of callable models, synced from OpenRouter. Trigger a sync
and you can enable/disable individual models per tenant.

```bash
# Sync (owner/admin/operator — `catalog_sync`). Verified live: returned 339 models.
curl -s -X POST $E/admin/catalog/sync -H "authorization: Bearer $JWT"
# → {"synced":339,"synced_at":"2026-06-27T16:33:34Z"}

# List with per-tenant enabled flags
curl -s $E/admin/models -H "authorization: Bearer $JWT"
# → {"data":[{"id":"google/gemini-2.5-flash-lite","name":"…","context_length":…,"enabled":true,"input_modalities":["text"]}, …]}

# Disable a model for this tenant (model id can contain "/", handled by :path)
curl -s -X PUT "$E/admin/models/google/gemini-2.5-flash-lite" \
  -H "authorization: Bearer $JWT" -H 'content-type: application/json' -d '{"enabled":false}'
```

A model with no override row is **enabled by default**. A disabled model returns
`403 ERR_MODEL_DISABLED` to clients.

`input_modalities` (additive; defaults to `["text"]`) declares which input types
the model accepts — surfaced on both `/admin/models` and `/admin/catalog/models`
for the dashboard's capability badges. When
`GATEWAY_INPUT_MODALITY_GUARD_ENABLED=true` (default `false`), a request whose
content requires a type outside that set is rejected with `400
ERR_UNSUPPORTED_INPUT_MODALITY` before any upstream call or billing.

---

## 5. BYOK — bring your own provider keys

**This is what makes completions work** (see the [encryption-key
gotcha](./01-getting-started.md#-the-one-gotcha-that-bites-everyone-provider-key-encryption)).
Each tenant stores its own provider credentials, **Fernet-encrypted at rest**.
**Owner-only.**

Supported providers: `openrouter`, `openai`, `anthropic`, `google`, `bedrock`,
`azure`.

```bash
# Bearer-style provider (openrouter / openai / anthropic / google)
curl -s -X PUT $E/admin/provider-keys/openrouter -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"secret":"sk-or-...","enabled":true}'
# → {"provider":"openrouter","configured":true,"enabled":true,"auth_mode":null,"updated_at":"…"}

# Status (NEVER returns the secret)
curl -s $E/admin/provider-keys -H "authorization: Bearer $JWT"
# → {"keys":[{"provider":"openrouter","configured":true,"enabled":true, …}]}

# Remove
curl -s -X DELETE $E/admin/provider-keys/openrouter -H "authorization: Bearer $JWT"  # → 204
```

Provider-specific PUT bodies:

| Provider type | Body |
|---------------|------|
| Bearer (`openrouter`,`openai`,`anthropic`,`google`) | `{"secret":"…","enabled":true}` |
| `bedrock` | `{"access_key_id":"…","secret_access_key":"…","region":"us-east-1","session_token":"…?","enabled":true}` |
| `azure` (api key) | `{"mode":"api_key","endpoint":"…","api_key":"…","api_version":"…?","deployment_map":{…}?}` |
| `azure` (AAD) | `{"mode":"aad","endpoint":"…","tenant_id":"…","client_id":"…","client_secret":"…","scope":"…?"}` |

- `tenant_id` always comes from the JWT — cross-tenant writes are impossible.
- Secrets are never echoed in any response and never logged.
- Audited as `provider_key.put`.

---

## 6. Members & roles

```bash
# List users in the tenant
curl -s $E/admin/users -H "authorization: Bearer $JWT"
# → {"users":[{"id":"…","email":"owner@example.com","role":"owner"}, …]}

# Assign a role
curl -s -X PUT $E/admin/users/$USER_ID/role -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"role":"admin"}'
```

Guards (enforced server-side):

- **Self-guard** — you cannot change your own role.
- **Escalation guard** — an `admin` cannot grant `owner` or `admin`.
- Target must be in your tenant (else `404`, no leak).
- Requires `members_manage` (owner / admin).
- Audited as `user.role_assign`.

New users join via signup (first user = owner) or SSO provisioning (always
`member`). See the [Multi-tenant guide](./04-multi-tenant-guide.md).

---

## 7. Teams

Teams group users and optionally share a budget; keys can be attributed to a team.

```bash
curl -s -X POST $E/admin/teams -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"name":"platform"}'
# → {"id":"…","name":"platform","member_count":0,"key_count":0,"team_budget_usd":null, …}

curl -s $E/admin/teams -H "authorization: Bearer $JWT"                       # list
curl -s $E/admin/teams/$TEAM_ID -H "authorization: Bearer $JWT"             # detail + members
curl -s -X PATCH $E/admin/teams/$TEAM_ID -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"team_budget_usd":"500.00"}'      # set/clear budget
curl -s -X POST $E/admin/teams/$TEAM_ID/members -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"email":"dev@example.com","role":"member"}'
curl -s -X DELETE $E/admin/teams/$TEAM_ID/members/$USER_ID -H "authorization: Bearer $JWT"
curl -s -X DELETE $E/admin/teams/$TEAM_ID -H "authorization: Bearer $JWT"    # 204 (nulls keys' team_id)
```

Attach a key to a team by setting `team_id` on the key (§2).

---

## 8. Routing configuration

Routing decides which deployment serves a model alias, and how the proxy retries
and cools down failing upstreams. **owner / admin / operator (`routing_manage`).**

```bash
# Effective config + live cooldown gate states
curl -s $E/admin/routing -H "authorization: Bearer $JWT"
# → {"retry_policy":{…},"cooldown":{…},"model_groups":{…},
#     "candidates":[{"model_id":"…","alias":"…","state":"closed|open|unknown"}],
#     "routing_strategy":"ordered","deployments":{…}}

# Persist a new config (validated by the same rules that run at boot)
curl -s -X PUT $E/admin/routing -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"routing_strategy":"simple-shuffle", "model_groups":{…}}'
```

> **Restart-to-apply.** `PUT /admin/routing` **persists** the config but does
> **not** mutate the live router — changes take effect on the **next gateway
> boot**. The response shows the effective-on-next-boot config. The dashboard
> `/app/routing` page surfaces a “Saved — restart to apply” affordance.

Invalid config → `422` with the validator code in `detail`; nothing is persisted.
`candidates[].state` always reflects the **live** cooldown gate, not the saved
config.

---

## 9. Guardrails & cache

**Guardrails** (`PUT /admin/guardrails`, `keys_manage` — owner/admin/operator) —
prompt-injection detection and PII masking, each with a `block`/`audit` (or
`mask`/`audit`) mode:

```bash
curl -s -X PUT $E/admin/guardrails -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' \
  -d '{"prompt_injection":{"enabled":true,"mode":"block"},
       "pii_mask":{"enabled":true,"mode":"mask",
         "pii_custom_patterns":[{"name":"EMP_ID","pattern":"EMP[0-9]{6}"}]}}'
```
Custom-pattern rules (ReDoS-guarded): ≤ 8 patterns, name `^[A-Z][A-Z0-9_]{0,31}$`,
≤ 256 bytes, valid regex, no backreferences / nested quantifiers. First violation
→ `422`, no write.

**Cache toggle** (`PUT /admin/cache`, `keys_manage` — owner/admin/operator) —
exact-match and semantic response caching per tenant:

```bash
curl -s -X PUT $E/admin/cache -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{"enabled":true,"semantic_enabled":false}'
```

---

## 10. SSO / OIDC

Configure a per-tenant identity provider so members log in with corporate SSO.
**Owner-only.** Requires `GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY` set (else `409`).

```bash
curl -s -X PUT $E/admin/oidc -H "authorization: Bearer $JWT" \
  -H 'content-type: application/json' -d '{
    "issuer":"https://accounts.google.com",
    "client_id":"…","client_secret":"…",
    "token_url":"https://oauth2.googleapis.com/token",
    "jwks_url":"https://www.googleapis.com/oauth2/v3/certs",
    "email_domains":["example.com"],"enabled":true}'
```

- `client_secret` is Fernet-encrypted; GET always returns `"<stored>"`.
- URLs are SSRF-guarded (https-only, no private IPs) unless
  `GATEWAY_OIDC_ALLOW_HTTP_URLS=true` (dev only).
- SSO-provisioned users always get role `member`.
- Login flow + callback details: [Multi-tenant guide → SSO](./04-multi-tenant-guide.md#sso--oidc).

---

## 11. Observability

All read surfaces under `/admin`. Most are `viewer`-readable (`usage_read` /
`ops_read`); `audit` needs the stricter `audit_read`.

```bash
# Usage — all-time totals + 50 newest records
curl -s $E/admin/usage -H "authorization: Bearer $JWT"
# → {"total_cost_usd":"0.00000468","total_requests":1,
#    "total_prompt_tokens":11,"total_completion_tokens":7,
#    "records":[{"model_id":"google/gemini-2.5-flash-lite","cost_usd":"0.00000468","status":200, …}]}

# Spend — windowed buckets (window=day|week|month; group_by=key_id|team_id)
curl -s "$E/admin/spend?window=day" -H "authorization: Bearer $JWT"

# Reconciliation — provider cost vs billed (drift, unbilled upstream)
curl -s "$E/admin/reconciliation?window=month" -H "authorization: Bearer $JWT"

# Audit — append-only actor/action log
curl -s "$E/admin/audit?limit=10" -H "authorization: Bearer $JWT"
# Verified live → three events, newest first:
#   provider_key.put  actor=owner@example.com  result=success  target=provider
#   budget.update     actor=owner@example.com  result=success  target=budget
#   key.create        actor=owner@example.com  result=success  target=api_key

# Alerts — tenant rows + platform system rows (circuit-open, drift, health)
curl -s "$E/admin/alerts?limit=20" -H "authorization: Bearer $JWT"

# SLO — availability / error-rate over a window
curl -s "$E/admin/slo?window_hours=24" -H "authorization: Bearer $JWT"
# → {"window_hours":24,"total_requests":4,"success_count":4,
#    "availability":1.0,"error_rate":0.0,"latency_ms":null}

# Upstream health (only openrouter is actively pinged — others omitted, not faked)
curl -s $E/admin/health/upstreams -H "authorization: Bearer $JWT"

# Live rate-limit / bandwidth counters (Redis; null on Redis outage, never 0)
curl -s $E/admin/ratelimits -H "authorization: Bearer $JWT"
curl -s $E/admin/bandwidth  -H "authorization: Bearer $JWT"
```

Notes:
- `spent_usd_month` / usage totals come from the Postgres ledger; billed cost
  includes the markup (verified: provider `$3.9e-6` → billed `$4.68e-6`).
- `latency_ms` is **always `null`** — no latency is stored (honest omission).
- Audit is **append-only** (trigger-immutable) and purged only by the retention
  sweeper, which respects an audit floor (`GATEWAY_RETENTION_AUDIT_FLOOR_DAYS`).

---

## 12. Operator (cross-tenant) reconciliation

`/ops/*` breaks tenant isolation by design and is reserved for **platform
operators** authenticated by an **mTLS client certificate** (not a JWT). Envoy
terminates TLS and forwards the cert identity via XFCC; the gateway checks the
fingerprint against `GATEWAY_OPS_CERT_FINGERPRINTS` (empty = nobody, fail-closed).

```bash
# Cross-tenant drift across ALL tenants (mTLS, not shown via plain curl)
GET /ops/reconciliation?window=month
# → {…, "by_tenant":[{"tenant_id":"…","provider_cost_total":…,"drift":…}, …]}
```

A valid **tenant** JWT here returns `403 ERR_OPS_FORBIDDEN` (you authenticated,
but this surface is operator-only). Details:
[Multi-tenant guide → Operator access](./04-multi-tenant-guide.md#operator-cross-tenant-access).

---

## Admin endpoint reference

| Method | Path | Permission | Purpose |
|--------|------|-----------|---------|
| POST | `/admin/auth/signup` | public | create tenant + owner |
| POST | `/admin/auth/login` | public | mint session JWT |
| GET | `/admin/auth/me` | any | decode identity |
| GET/POST/PATCH/DELETE | `/admin/keys[...]` | `keys_manage` (GET any) | API key lifecycle |
| POST | `/admin/keys/{id}/rotate` | `keys_manage` | atomic rotate |
| GET/PUT | `/admin/budget` | `budgets_manage` (GET any) | tenant monthly ceiling |
| GET/PUT | `/admin/models[/{id}]` | `keys_manage` | catalog + per-tenant toggles |
| POST | `/admin/catalog/sync` | `catalog_sync` | sync from OpenRouter |
| GET/PUT/DELETE | `/admin/provider-keys[/{provider}]` | **owner** | BYOK provider creds |
| GET/PUT | `/admin/users[/{id}/role]` | `members_manage` | members & roles |
| GET/POST/PATCH/DELETE | `/admin/teams[...]` | `keys_manage` | teams & members |
| GET/PUT | `/admin/routing` | `routing_manage` | routing config (restart-to-apply) |
| GET/PUT | `/admin/guardrails` | `keys_manage` (GET any) | injection / PII policy |
| GET/PUT | `/admin/cache` | `keys_manage` (GET any) | cache toggles |
| GET/PUT | `/admin/oidc` | **owner** | per-tenant SSO config |
| GET | `/admin/usage` | any | totals + recent records |
| GET | `/admin/spend` | any | windowed spend |
| GET | `/admin/reconciliation` | `usage_read` | drift (tenant) |
| GET | `/admin/audit` | `audit_read` | append-only audit log |
| GET | `/admin/alerts` | `ops_read` | alert/event history |
| GET | `/admin/slo` | `ops_read` | availability / error-rate |
| GET | `/admin/health/upstreams` | `ops_read` | upstream up/down |
| GET | `/admin/ratelimits` · `/admin/bandwidth` | `ops_read` | live counters |
| GET | `/ops/reconciliation` | **mTLS operator** | cross-tenant drift |

**Next:** [03 — API client guide](./03-api-client-guide.md) ·
[04 — Multi-tenant guide](./04-multi-tenant-guide.md)
