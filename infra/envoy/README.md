# Envoy edge (placeholder)

Edge responsibilities (decision D3 in `PROJECT.md`):

- TLS termination
- `jwt_authn` filter — validates dashboard JWTs issued by the gateway
- `ext_authz` filter — calls gateway `/internal/authz` to validate proxy API keys
- Rate limiting (local token bucket for MVP; ratelimit service at Production stage)

`envoy.yaml` + docker-compose wiring land with the auth feature slice, after
the auth contract freezes (resolve SETUP-REVIEW.md ⚠ #1 first).
