#!/usr/bin/env bash
# e2e_edge.sh — brings up the Envoy+gateway compose stack and runs the e2e test suite.
#
# Usage:
#   ./scripts/e2e_edge.sh [--no-build]
#
# Environment:
#   GATEWAY_JWT_SECRET  — shared JWT secret for gateway + Envoy JWKS (default: e2e-test-secret-change-me)
#   E2E_BASE_URL        — Envoy listener URL seen by tests (default: http://localhost:8080)
#
# Contract §3 requirements:
#   - set -euo pipefail
#   - compose up --build -d --wait (waits for all healthchecks)
#   - trap 'compose down -v' EXIT (teardown on success or failure)
#   - uv run pytest tests/ -m e2e -q --no-cov (from apps/gateway/)
#
# Ports used by this stack:
#   8080 — Envoy HTTP listener (exposed to host)
#   9901 — Envoy admin interface (exposed to host)
#   Postgres and Redis have NO host ports — internal only (avoids clash with dev stack on 5433/6380)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/infra/docker-compose.e2e.yml"
GATEWAY_DIR="${REPO_ROOT}/apps/gateway"

export GATEWAY_JWT_SECRET="${GATEWAY_JWT_SECRET:-e2e-test-secret-change-me}"
export E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:8080}"

BUILD_FLAG="--build"
if [[ "${1:-}" == "--no-build" ]]; then
    BUILD_FLAG=""
fi

echo "=== e2e_edge.sh: starting Envoy+gateway compose stack ==="
echo "    COMPOSE_FILE: ${COMPOSE_FILE}"
echo "    GATEWAY_JWT_SECRET: (set, length=${#GATEWAY_JWT_SECRET})"
echo "    E2E_BASE_URL: ${E2E_BASE_URL}"

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
echo "=== e2e_edge.sh: stack healthy; running e2e tests ==="
echo ""

# Run the e2e suite from the gateway project directory
cd "${GATEWAY_DIR}"
uv run pytest tests/ -m e2e -q --no-cov

echo ""
echo "=== e2e_edge.sh: all e2e tests passed ==="
