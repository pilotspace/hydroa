#!/usr/bin/env bash
# e2e_kind.sh — brings up the kind stack, SEEDS pricing, and runs the live kind e2e suite.
#
# This is the task-7 (e2e-core-flow §3) harness. It proves the FULL money path through
# the LIVE Envoy edge records an ACCURATE, NON-ZERO usage+cost row.
#
# Usage:
#   ./scripts/e2e_kind.sh [--no-up] [--down]
#     --no-up   skip `make kind-up` (assume the stack is already Ready)
#     --down    tear the cluster down on exit (default: LEAVE it up — bring-up is slow + reusable)
#
# What it does (design-for-failure: bounded waits, idempotent seed):
#   1. make kind-up           — idempotent; reuses an existing cluster (task-6 harness)
#   2. SEED pricing in the in-cluster Postgres (the implementation that turns M3 green):
#        - a catalog `models` row for E2E_MODEL and UNPRICED_MODEL (active, routes to the stub)
#        - a `pricing_snapshots` row ONLY for E2E_MODEL (so an unpriced model still bills $0)
#      Without this seed the priced-cost assertion is RED by design (unpriced → $0).
#   3. uv run pytest tests/kind_e2e -m kind_e2e -q --no-cov   (from apps/gateway/)
#
# Seeded constants MUST stay in lockstep with apps/gateway/tests/kind_e2e/conftest.py.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATEWAY_DIR="${REPO_ROOT}/apps/gateway"

PG_POD="${KIND_PG_POD:-ai-proxy-postgres-0}"
PG_USER="${KIND_PG_USER:-gateway}"
PG_DB="${KIND_PG_DB:-gateway}"

# Frozen contract constants (§3) — keep in sync with conftest.py.
E2E_MODEL="e2e-kind-priced"
UNPRICED_MODEL="e2e-kind-unpriced"
PROMPT_PRICE="0.000002"
COMPLETION_PRICE="0.000010"

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
        echo "=== e2e_kind.sh: tearing down kind cluster (exit ${exit_code}) ==="
        make -C "${REPO_ROOT}" kind-down 2>&1 || true
    else
        echo ""
        echo "=== e2e_kind.sh: leaving kind stack UP (re-run with --down to remove) — exit ${exit_code} ==="
    fi
    exit "${exit_code}"
}
trap cleanup EXIT

if [[ "${DO_UP}" == "1" ]]; then
    echo "=== e2e_kind.sh: make kind-up (idempotent) ==="
    make -C "${REPO_ROOT}" kind-up
fi

# Guard: the Postgres pod must be present before we seed (clear failure, never a silent skip).
if ! kubectl get pod "${PG_POD}" >/dev/null 2>&1; then
    echo "❌ e2e_kind.sh: pod ${PG_POD} not found — is the kind stack up? (run without --no-up)" >&2
    exit 1
fi

echo ""
echo "=== e2e_kind.sh: seeding pricing for ${E2E_MODEL} (idempotent) ==="
# -i streams the heredoc to psql stdin; ON_ERROR_STOP makes any failure fatal (no half-seed).
kubectl exec -i "${PG_POD}" -- \
    psql -U "${PG_USER}" -d "${PG_DB}" -v ON_ERROR_STOP=1 <<SQL
-- Catalog rows: both models exist + route (provider 'openrouter' → the in-cluster stub).
INSERT INTO models (id, name, active, provider, modality) VALUES
    ('${E2E_MODEL}',     'e2e kind priced model',   true, 'openrouter', 'chat'),
    ('${UNPRICED_MODEL}','e2e kind unpriced model', true, 'openrouter', 'chat')
ON CONFLICT (id) DO NOTHING;

-- Pricing ONLY for the priced model. Guarded so re-runs add no duplicate snapshot.
INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token, completion_usd_per_token)
SELECT gen_random_uuid(), '${E2E_MODEL}', ${PROMPT_PRICE}, ${COMPLETION_PRICE}
WHERE NOT EXISTS (
    SELECT 1 FROM pricing_snapshots WHERE model_id = '${E2E_MODEL}'
);
SQL

echo ""
echo "=== e2e_kind.sh: running live kind e2e suite (tests/kind_e2e -m kind_e2e) ==="
echo ""
cd "${GATEWAY_DIR}"
uv run pytest tests/kind_e2e -m kind_e2e -q --no-cov

echo ""
echo "=== e2e_kind.sh: all kind e2e tests passed ✅ ==="
