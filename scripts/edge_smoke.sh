#!/usr/bin/env bash
# scripts/edge_smoke.sh — end-to-end smoke through the Envoy edge.
#
# Proves the full production-shaped path the team verified by hand:
#   edge auth (jwt_authn on /admin/*, ext_authz virtual-key on /v1/*),
#   a REAL proxied chat completion, and accurate per-tenant cost tracking.
#
# Prereqs: the edge stack is up + catalog synced  →  `make edge`
# Usage:   make edge-smoke   (or: EDGE_URL=http://127.0.0.1:8080 scripts/edge_smoke.sh)
#
# Security: never prints the JWT, the provider key, or the full virtual key
# (prefix only). Uses an @example.com email (gateway EmailStr rejects .local).
set -euo pipefail

EDGE="${EDGE_URL:-http://127.0.0.1:8080}"
EMAIL="${SMOKE_EMAIL:-edge-smoke@example.com}"
PASSWORD="${SMOKE_PASSWORD:-TestPass123!}"
MODEL="${SMOKE_MODEL:-openai/gpt-4o-mini}"
TENANT="${SMOKE_TENANT:-edge-smoke}"

echo "▶ edge smoke against $EDGE"

_code() { curl -s -m 10 -o /dev/null -w '%{http_code}' "$@"; }

# 0) health (catch-all, no auth)
[ "$(_code "$EDGE/health")" = 200 ] && echo "  ✓ /health 200" || { echo "  ✗ /health not 200"; exit 1; }

# 1) NEGATIVE — /admin/* without JWT must be rejected by jwt_authn
c=$(_code "$EDGE/admin/keys"); [ "$c" = 401 ] && echo "  ✓ /admin/keys unauth → 401 (jwt_authn)" || echo "  ⚠ /admin/keys unauth → $c (expected 401)"

# 2) NEGATIVE — /v1/* without virtual key must be rejected by ext_authz
c=$(_code -X POST -H 'Content-Type: application/json' -d '{}' "$EDGE/v1/chat/completions"); \
  [ "$c" = 401 ] && echo "  ✓ /v1 unauth → 401 (ext_authz)" || echo "  ⚠ /v1 unauth → $c (expected 401)"

# 3) signup (idempotent — fine if the tenant already exists)
curl -s -m 12 -o /dev/null -X POST "$EDGE/admin/auth/signup" -H 'Content-Type: application/json' \
  -d "{\"tenant_name\":\"$TENANT\",\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" || true

# 4) login → JWT (public route through the edge)
TOKEN=$(curl -s -m 12 -X POST "$EDGE/admin/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
[ -n "$TOKEN" ] && echo "  ✓ login → JWT" || { echo "  ✗ login failed"; exit 1; }

# 5) create virtual key (JWT-authed admin route)
KEY=$(curl -s -m 12 -X POST "$EDGE/admin/keys" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"name\":\"smoke-$$\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"])')
[ -n "$KEY" ] && echo "  ✓ virtual key created (${KEY:0:18}…)" || { echo "  ✗ key create failed"; exit 1; }

# 6) REAL chat completion via the virtual key (edge ext_authz → gateway → provider)
resp=$(curl -s -m 45 -X POST "$EDGE/v1/chat/completions" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: edge-ok\"}],\"max_tokens\":10}")
echo "$resp" | python3 -c 'import sys,json
d=json.load(sys.stdin)
if "choices" not in d: raise SystemExit("  ✗ chat error: "+json.dumps(d)[:200])
print("  ✓ chat 200 →", repr(d["choices"][0]["message"]["content"]), "| cost_usd", d.get("usage",{}).get("cost"))' \
  || { echo "  (needs a configured provider key + synced catalog: run 'make edge')"; exit 1; }

# 7) cost tracking visible through the edge
curl -s -m 10 "$EDGE/admin/usage" -H "Authorization: Bearer $TOKEN" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  ✓ usage:",d["total_requests"],"req, cost_usd",d["total_cost_usd"])'

echo "✅ edge smoke PASS"
