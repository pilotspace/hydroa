#!/usr/bin/env bash
# e2e_kind_ui.sh — brings up the kind stack and runs the LIVE browser (Playwright) UI e2e.
#
# This is the task-9 (e2e-ui §3) harness. It proves the dashboard UI works through the LIVE
# Envoy edge: a real login → real BFF → real gateway → real session → a real authed surface.
# The browser analog of scripts/e2e_kind.sh (the API e2e).
#
# Usage:
#   ./scripts/e2e_kind_ui.sh [--no-up] [--down]
#     --no-up   skip `make kind-up` (assume the stack is already Ready)
#     --down    tear the cluster down on exit (default: LEAVE it up — bring-up is slow + reusable)
#
# Design-for-failure: bounded waits (kind-up's rollout waits + Playwright timeouts), idempotent
# (unique tenant per run), zero cloud creds (dashboard + gateway are in-cluster).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_DIR="${REPO_ROOT}/apps/dashboard"

# The edge URL the browser drives — keep in lockstep with KIND_EDGE_PORT (Makefile) + the config default.
KIND_EDGE_PORT="${KIND_EDGE_PORT:-8443}"
export KIND_EDGE_URL="${KIND_EDGE_URL:-https://127.0.0.1:${KIND_EDGE_PORT}}"

DO_UP=1
DO_DOWN=0
for arg in "$@"; do
    case "${arg}" in
        --no-up) DO_UP=0 ;;
        --down)  DO_DOWN=1 ;;
        *) echo "unknown arg: ${arg}" >&2; exit 2 ;;
    esac
done

cleanup() {
    local exit_code=$?
    if [[ "${DO_DOWN}" == "1" ]]; then
        echo ""
        echo "=== e2e_kind_ui.sh: tearing down kind cluster (exit ${exit_code}) ==="
        make -C "${REPO_ROOT}" kind-down 2>&1 || true
    else
        echo ""
        echo "=== e2e_kind_ui.sh: leaving kind stack UP (re-run with --down to remove) — exit ${exit_code} ==="
    fi
    exit "${exit_code}"
}
trap cleanup EXIT

if [[ "${DO_UP}" == "1" ]]; then
    echo "=== e2e_kind_ui.sh: make kind-up (idempotent) ==="
    make -C "${REPO_ROOT}" kind-up
fi

# Guard: the dashboard must be reachable through the edge before we drive a browser at it
# (clear failure, never a silent skip / false green).
echo ""
echo "=== e2e_kind_ui.sh: probing the edge for the dashboard login page (${KIND_EDGE_URL}/login) ==="
code="$(curl -sk -o /dev/null -w '%{http_code}' "${KIND_EDGE_URL}/login" || echo 000)"
if [[ "${code}" != "200" ]]; then
    echo "❌ e2e_kind_ui.sh: ${KIND_EDGE_URL}/login returned ${code} (expected 200) — is the kind stack up?" >&2
    exit 1
fi
echo "✅ edge serves /login (HTTP ${code})"

# Chromium is a ~92MiB runtime download (NOT committed). Idempotent: a present browser is a no-op.
echo ""
echo "=== e2e_kind_ui.sh: ensuring the Playwright Chromium browser is installed ==="
( cd "${DASHBOARD_DIR}" && npx playwright install chromium )

echo ""
echo "=== e2e_kind_ui.sh: running the live browser UI e2e (npm run test:kind) ==="
echo ""
( cd "${DASHBOARD_DIR}" && npm run test:kind )

echo ""
echo "=== e2e_kind_ui.sh: all kind UI e2e tests passed ✅ ==="
