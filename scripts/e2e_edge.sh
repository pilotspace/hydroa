#!/usr/bin/env bash
# e2e_edge.sh — brings up the Envoy+gateway compose stack and runs the e2e test suite.
#
# Usage:
#   ./scripts/e2e_edge.sh [--no-build]
#
# Environment:
#   GATEWAY_JWT_SECRET  — shared JWT secret for gateway + Envoy JWKS (default: e2e-test-secret-change-me)
#   E2E_BASE_URL        — Envoy HTTP listener URL seen by tests (default: http://localhost:8080)
#   E2E_TLS_URL         — Envoy HTTPS listener URL seen by TLS tests (default: https://localhost:8443)
#   E2E_CA_CERT         — path to dev CA cert for TLS verification (default: infra/envoy/certs/dev-ca.pem)
#
# Contract §3 requirements:
#   - set -euo pipefail
#   - compose up --build -d --wait (waits for all healthchecks)
#   - trap 'compose down -v' EXIT (teardown on success or failure)
#   - uv run pytest tests/ -m e2e -q --no-cov (from apps/gateway/)
#
# Ports used by this stack:
#   8080 — Envoy HTTP listener (exposed to host)
#   8443 — Envoy TLS listener (exposed to host; requires certs from gen_dev_certs.sh)
#   9901 — Envoy admin interface (exposed to host)
#   Postgres and Redis have NO host ports — internal only (avoids clash with dev stack on 5433/6380)
#
# NOTE: The two e2e test files are run in two separate pytest invocations separated by a
# 2-second pause. This is required because test_e2e_rate_limit_enforced fires 60 concurrent
# requests that exhaust the Envoy :8080 token bucket (50 req/s max), and the TLS tests
# test_https_full_flow_signup_key_v1 (S3) and test_http_8080_still_works_no_regression (S6)
# check :8080 health immediately after. The 2-second pause allows the bucket to fully refill
# (50 tokens/s × 2s = 100 tokens available; max_tokens=50 so bucket is full after 1s).
# This separation does not change the test assertions — both suites run against the same
# live stack; the pause is purely a scheduling concern.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/infra/docker-compose.e2e.yml"
GATEWAY_DIR="${REPO_ROOT}/apps/gateway"

export GATEWAY_JWT_SECRET="${GATEWAY_JWT_SECRET:-e2e-test-secret-change-me}"
export E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:8080}"
export E2E_TLS_URL="${E2E_TLS_URL:-https://localhost:8443}"
export E2E_CA_CERT="${E2E_CA_CERT:-${REPO_ROOT}/infra/envoy/certs/dev-ca.pem}"

BUILD_FLAG="--build"
if [[ "${1:-}" == "--no-build" ]]; then
    BUILD_FLAG=""
fi

echo "=== e2e_edge.sh: starting Envoy+gateway compose stack ==="
echo "    COMPOSE_FILE: ${COMPOSE_FILE}"
echo "    GATEWAY_JWT_SECRET: (set, length=${#GATEWAY_JWT_SECRET})"
echo "    E2E_BASE_URL:  ${E2E_BASE_URL}"
echo "    E2E_TLS_URL:   ${E2E_TLS_URL}"
echo "    E2E_CA_CERT:   ${E2E_CA_CERT}"

# Ensure dev certs exist before starting the stack (Envoy will fail without them)
if [[ ! -f "${REPO_ROOT}/infra/envoy/certs/server.crt" ]]; then
    echo "=== e2e_edge.sh: dev certs not found; running gen_dev_certs.sh ==="
    bash "${REPO_ROOT}/scripts/gen_dev_certs.sh"
fi

# Teardown on exit (success or failure) — ensures no dangling containers/volumes
cleanup() {
    local exit_code=$?
    echo ""
    echo "=== e2e_edge.sh: tearing down stack (exit code: ${exit_code}) ==="
    docker compose -f "${COMPOSE_FILE}" down -v --timeout 15 2>&1 || true
    exit "${exit_code}"
}
trap cleanup EXIT

# Bring up the full stack; --wait blocks until all service healthchecks pass
echo "=== e2e_edge.sh: docker compose up ${BUILD_FLAG} -d --wait ==="
docker compose -f "${COMPOSE_FILE}" up ${BUILD_FLAG} -d --wait

echo ""
echo "=== e2e_edge.sh: stack healthy; running HTTP e2e tests (test_e2e_edge.py) ==="
echo ""

# Run the original HTTP e2e suite
cd "${GATEWAY_DIR}"
uv run pytest tests/edge/test_e2e_edge.py -m e2e -q --no-cov

echo ""
echo "=== e2e_edge.sh: HTTP e2e tests passed ==="
echo ""
echo "=== e2e_edge.sh: waiting 2s for rate limit bucket to refill before TLS tests ==="
sleep 2

echo ""
echo "=== e2e_edge.sh: running TLS e2e tests (test_e2e_tls.py) ==="
echo ""

# Run the TLS e2e suite
uv run pytest tests/edge/test_e2e_tls.py -m e2e -q --no-cov

echo ""
echo "=== e2e_edge.sh: all e2e tests passed ==="
