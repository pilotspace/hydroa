# 05 — Troubleshooting & FAQ

Symptom → cause → fix for the failure modes you’ll actually hit, plus a diagnosis
toolkit. Most of these were encountered live while verifying the other runbooks.

- [Diagnosis toolkit](#diagnosis-toolkit)
- [Bring-up & edge problems](#bring-up--edge-problems)
- [`/v1` call failures](#v1-call-failures)
- [Admin / auth problems](#admin--auth-problems)
- [Catalog, budget, rate-limit, model errors](#catalog-budget-rate-limit-model-errors)
- [WebSocket (realtime) close codes](#websocket-realtime-close-codes)
- [Dashboard problems](#dashboard-problems)
- [Clean slate / reset](#clean-slate--reset)

---

## Diagnosis toolkit

```bash
E=http://127.0.0.1:8080

# What's running and healthy?
docker compose -f infra/docker-compose.e2e.yml ps -a

# Gateway logs (the real exception is here, not in the HTTP body)
docker logs hydroa-e2e-gateway-1 2>&1 | tail -40

# Liveness vs readiness (readiness checks Postgres + Redis)
docker exec hydroa-e2e-gateway-1 \
  python -c "import urllib.request as u;print(u.urlopen('http://localhost:8000/internal/health/ready').read().decode())"

# Edge reachable? (000 = nothing listening)
curl -s -o /dev/null -w '%{http_code}\n' $E/health

# Envoy upstream health: is the gateway cluster connecting?
curl -s http://127.0.0.1:9901/clusters | grep -E 'gateway_cluster.*(health_flags|cx_active|cx_connect_fail)'

# Envoy effective config (filters, routes, JWKS)
curl -s http://127.0.0.1:9901/config_dump | head -c 2000
```

In Kubernetes, the equivalents are `kubectl get pods -o wide`,
`kubectl logs deploy/ai-proxy-gateway`, and
`kubectl port-forward svc/ai-proxy-envoy 9901:9901` then curl `:9901/clusters`.

---

## Bring-up & edge problems

### Edge `:8080` returns `000` / connection refused (Envoy never started)

**Cause:** Envoy `depends_on` the gateway becoming healthy. On a cold first build
`docker compose … up --wait` can return *before* Envoy actually starts, leaving it
in `Created` with **no port bindings** (`docker port hydroa-e2e-envoy-1` is empty).

**Fix:** force-recreate just Envoy.
```bash
GATEWAY_JWT_SECRET=e2e-test-secret-change-me \
  docker compose --env-file apps/gateway/.env -f infra/docker-compose.e2e.yml \
  up -d --force-recreate --no-deps envoy
```

### `Bind for 0.0.0.0:8443 failed: port is already allocated`

**Cause:** a leftover **kind** cluster (`ai-proxy-control-plane`) is mapping host
`:8443` → nodePort 30443.
```bash
lsof -nP -iTCP:8443 -sTCP:LISTEN          # shows the holder
docker ps --format '{{.Names}}\t{{.Ports}}' | grep 8443
```
**Fix:** free the port.
```bash
make kind-down          # deletes the kind cluster → frees :8443
```

### Gateway refuses to boot (production/staging)

**Cause:** `GATEWAY_JWT_SECRET` still set to the default `dev-only-secret-change-me`
while `GATEWAY_ENVIRONMENT` is not `dev`/`test` — a deliberate boot guard.
**Fix:** set a real `GATEWAY_JWT_SECRET` (and keep it in sync with Envoy’s JWKS).

### `redis.exceptions.TimeoutError: Timeout reading from redis:6379` in logs

**Cause:** Redis not healthy / wrong `GATEWAY_REDIS_URL`. Rate limits, budgets,
cache, and cooldown read Redis; most are **fail-open** so requests may still
succeed, but counters read as `null`.
**Fix:** `docker compose -f infra/docker-compose.e2e.yml ps` — Redis should be
`healthy`; check the URL.

---

## `/v1` call failures

### `HTTP 500` instantly (~15 ms) on every completion ⭐ most common

```
docker logs … → ProviderCredentialError: ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE
```
**Cause:** the data plane resolves provider keys **per-tenant (BYOK)** and needs
`GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY` to decrypt them. The `e2e` compose doesn’t
set it. `GATEWAY_OPENROUTER_API_KEY` only powers the **catalog sync**, not
completions.
**Fix:** set the encryption key **and** configure a BYOK key — see
[Getting started → the gotcha](./01-getting-started.md#-the-one-gotcha-that-bites-everyone-provider-key-encryption)
and [Admin §5](./02-admin-guide.md#5-byok--bring-your-own-provider-keys).

### `503 ERR_PROVIDER_UNAVAILABLE`

**Cause:** the encryption key is set, but **no enabled BYOK credential** exists for
the model’s provider (e.g. you called an `anthropic/*` model but only configured
`openrouter`).
**Fix:** `PUT /admin/provider-keys/{provider}` for the provider that backs the
model, or call a model whose provider you’ve configured.

### `502 ERR_UPSTREAM_UNAVAILABLE`

**Cause:** the upstream returned 5xx, was unreachable, or the **circuit breaker is
open** for that model. A bad/disabled BYOK secret also lands here.
**Fix:** check `:9901/clusters` for `cx_connect_fail`; verify the BYOK secret is
valid; check `GET /admin/routing` for `state: "open"` candidates (cooldown).

### `401` on `/v1/*` even with a key

**Cause:** the key is wrong/expired/revoked, or the header isn’t
`Authorization: Bearer sk-…`. All failures return the **same** body
(`ERR_AUTH_INVALID_KEY`) by design — no enumeration oracle.
**Fix:** mint a fresh key (`POST /admin/keys`); confirm `expires_at` isn’t past.

---

## Admin / auth problems

### `signup` → `422 ERR_PAYLOAD_INVALID`

**Causes (verified):** `email` fails `EmailStr` validation — **reserved TLDs like
`@acme.test` are rejected** (use `@example.com`); or `password` is shorter than the
10-char minimum; or `tenant_name` is empty/over 120 chars.

### `/admin/*` → `401` / “Jwt is missing”

**Cause:** no/expired `Authorization: Bearer <JWT>` (24 h default TTL), or the JWT
was signed with a different `GATEWAY_JWT_SECRET` than Envoy’s JWKS.
**Fix:** log in again for a fresh token; ensure the gateway and Envoy share the
secret (the compose passes one `GATEWAY_JWT_SECRET` to both).

### `403 ERR_AUTH_FORBIDDEN` on an admin call

**Cause:** your role lacks the permission. BYOK and SSO config are **owner-only**;
budgets need `budgets_manage`; members need `members_manage`. See the
[role matrix](./04-multi-tenant-guide.md#roles--permissions).

### `403 ERR_OPS_FORBIDDEN` on `/ops/*`

**Cause:** you used a tenant JWT. `/ops/*` requires an **mTLS operator cert** whose
fingerprint is in `GATEWAY_OPS_CERT_FINGERPRINTS` (empty = nobody).

---

## Catalog, budget, rate-limit, model errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/admin/models` returns `0` models | catalog never synced | `POST /admin/catalog/sync` (needs a valid `GATEWAY_OPENROUTER_API_KEY`) |
| `POST /admin/catalog/sync` → `502` | catalog source unreachable / bad OpenRouter key | check the env key + network |
| `400 ERR_MODEL_UNKNOWN` | model not in the active catalog | sync the catalog; check the exact id (`google/gemini-2.5-flash-lite`) |
| `403 ERR_MODEL_DISABLED` | model disabled for the tenant | `PUT /admin/models/{id} {"enabled":true}` |
| `403 ERR_MODEL_NOT_ALLOWED` | model not in the key’s `model_allowlist` | widen the key’s allowlist (`PATCH /admin/keys/{id}`) |
| `402 ERR_BUDGET_EXCEEDED` | tenant/team/key monthly cap hit | raise the cap or wait for the month roll-over |
| `429 ERR_RATE_LIMITED` | key `rpm`/`tpm` exceeded | honor `Retry-After`; raise the key limits |
| `503 ERR_BANDWIDTH_EXHAUSTED` | per-key tokens/s bucket drained | honor `Retry-After`; raise `GATEWAY_BANDWIDTH_TOKENS_PER_SEC` |
| `400 ERR_UNSUPPORTED_CONTENT_PART` | external image URL on the Gemini path | use a `data:` URI (SSRF guard) |

---

## WebSocket (realtime) close codes

| Code | Meaning | Fix |
|------|---------|-----|
| `4401` | bad/missing/expired token, or non-auth first frame | send `{"type":"auth","token":"sk-…"}` as the **first** frame |
| `4408` | no auth frame within the timeout, or relay idle timeout | send auth promptly; keep the relay active |
| `4404` | (relay) no realtime provider configured | set `GATEWAY_REALTIME_RELAY_PROVIDER=openai|gemini` |
| `4503` | (relay) upstream unavailable / breaker open | check provider creds + reachability |
| `1011` | (relay) unexpected upstream error | check gateway logs |
| `1000` | clean close | normal |

---

## Dashboard problems

| Symptom | Cause | Fix |
|---------|-------|-----|
| `307` redirect to `/login` on `/app/*` | no `ai_proxy_session` cookie (not logged in / expired) | log in again |
| `401 ERR_AUTH_SESSION_EXPIRED` from the BFF | gateway returned 401 → BFF cleared the cookie | log in again |
| Dashboard pages spin / `502` | `GATEWAY_URL` wrong or the gateway is down | point `GATEWAY_URL` at the edge (`http://127.0.0.1:8080`); check the gateway |
| `413` on upload | request body over `GW_MAX_BODY_BYTES` (32 MiB) | shrink the payload or raise the cap |
| Build fails on `npm run build` | stale `.next` / node modules | `rm -rf apps/dashboard/.next && npm ci` |

See the [Dashboard UI walkthrough](./06-dashboard-ui.md) for the pages themselves.

---

## Clean slate / reset

```bash
# Tear down the edge stack AND its data (Postgres + Redis volumes)
make edge-down VOLUMES=1

# Rebuild from scratch
make edge

# Reset just the database (keep containers): re-run migrations on a fresh DB
make edge-down VOLUMES=1 && make edge

# Local dev datastores
docker compose -f infra/docker-compose.dev.yml down -v
docker compose -f infra/docker-compose.dev.yml up -d --wait && make migrate

# kind
make kind-down && make kind-up
```

> Resetting volumes removes all tenants, keys, usage, and **stored BYOK
> secrets** — exactly what you want before handing the environment to someone else.

---

**Back to:** [README](./README.md) · [01 Getting started](./01-getting-started.md) ·
[02 Admin](./02-admin-guide.md) · [03 API client](./03-api-client-guide.md) ·
[04 Multi-tenant](./04-multi-tenant-guide.md)
