#!/usr/bin/env bash
# gen_dev_certs.sh — generate self-signed dev/e2e TLS certificates for ai-proxy
#
# Output files (infra/envoy/certs/):
#   dev-ca.pem      — self-signed CA certificate (trusted by test verify= param)
#   dev-ca-key.pem  — CA private key (kept local; never committed)
#   server.crt      — server certificate signed by dev-ca (SAN: localhost, 127.0.0.1)
#   server.key      — server private key
#   server.csr      — CSR (intermediate; can be removed after generation)
#
# Idempotent: skips generation if server.crt exists and is not expired.
# Usage:
#   ./scripts/gen_dev_certs.sh
#
# Requirements: openssl must be available in PATH.

set -euo pipefail

# Resolve the repo root regardless of where the script is called from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT="${REPO_ROOT}/infra/envoy/certs"

echo "=== gen_dev_certs.sh: output directory: ${OUT} ==="

mkdir -p "${OUT}"

# Idempotent: skip if server cert already exists and is not expired
if [ -f "${OUT}/server.crt" ] && openssl x509 -checkend 0 -noout -in "${OUT}/server.crt" 2>/dev/null; then
    echo "Dev certs already valid — skipping generation"
    echo "  server.crt : ${OUT}/server.crt"
    echo "  dev-ca.pem : ${OUT}/dev-ca.pem"
    exit 0
fi

echo "=== gen_dev_certs.sh: generating new dev certs ==="

# 1. Self-signed CA (10-year)
openssl req -x509 -newkey rsa:4096 -days 3650 -nodes \
    -keyout "${OUT}/dev-ca-key.pem" \
    -out "${OUT}/dev-ca.pem" \
    -subj "/CN=ai-proxy-dev-ca"

echo "  [1/3] CA cert written: ${OUT}/dev-ca.pem"

# 2. Server key + CSR
openssl req -newkey rsa:4096 -nodes \
    -keyout "${OUT}/server.key" \
    -out "${OUT}/server.csr" \
    -subj "/CN=localhost"

echo "  [2/3] Server key + CSR written"

# 3. Server cert signed by dev CA (SAN: localhost, 127.0.0.1)
#    -extfile uses process substitution to pass the SAN extension inline.
openssl x509 -req -days 825 \
    -in "${OUT}/server.csr" \
    -CA "${OUT}/dev-ca.pem" \
    -CAkey "${OUT}/dev-ca-key.pem" \
    -CAcreateserial \
    -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1") \
    -out "${OUT}/server.crt"

echo "  [3/3] Server cert written: ${OUT}/server.crt"
echo ""
echo "=== gen_dev_certs.sh: dev certs written to ${OUT} ==="
echo ""
echo "  CA cert (for test verify=):  ${OUT}/dev-ca.pem"
echo "  Server cert:                 ${OUT}/server.crt"
echo "  Server key:                  ${OUT}/server.key"
echo ""
echo "Trust this CA in curl:"
echo "  curl --cacert ${OUT}/dev-ca.pem https://localhost:8443/health"
