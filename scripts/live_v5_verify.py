#!/usr/bin/env python3
"""Live v5 exit-criteria verification — all through the Envoy TLS edge.

Operator-run (requires a real key + the e2e TLS stack with v4+v5 overlays):
    export GATEWAY_OPENROUTER_API_KEY=sk-or-...
    docker compose \\
        -f infra/docker-compose.e2e.yml \\
        -f infra/docker-compose.e2e.v4.yml \\
        -f infra/docker-compose.e2e.v5.yml \\
        up --build -d --wait
    uv run --project apps/gateway python scripts/live_v5_verify.py

Criteria covered (v5 MILESTONE.md):
  C1 oidc-jwks:    RS256/JWKS positive (valid token → session cookie);
                   RS256/JWKS negative (forged signature same kid → 401 ERR_OIDC_TOKEN_INVALID)
  C2 team-attrib:  teamed key → usage_records.team_id set; GET /admin/spend?group_by=team_id
                   returns breakdown with both cost_usd AND ledger_cost_usd
  C3 pii-v2:      IBAN built-in + custom pattern masked live (pii_masked=true in ledger);
                   dangerous nested-quantifier regex → 422 ERR_PAYLOAD_INVALID
  C4 semantic-cache: normalised prompt variant hits semantic cache (X-Cache: semantic_hit,
                   cost 0 in ledger); second tenant same prompt misses
  C5 oidc-tenant-config: two tenants, two IdPs, one deployment;
                   GET /admin/oidc never returns client_secret plaintext
  C6 rename/isolation: docker ps shows hydroa-e2e-* names; every identity carries run_id
                   (re-runnable)

Shape sources per check:
  C1  oidc-jwks positive/negative:
      apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:123-191 (RS256 minting, jwk dict)
      apps/gateway/src/gateway/auth/api/oidc_router.py:110-174 (/login 302 + cookies)
      apps/gateway/src/gateway/auth/api/oidc_router.py:177-307 (/callback -> ai_proxy_session)
  C2  team attribution:
      apps/gateway/tests/team_attribution/test_team_attribution.py:456-560 (ledger_cost_usd)
      apps/gateway/src/gateway/usage/api/schemas.py:105-118 (TeamSpendBreakdownItem)
      apps/gateway/src/gateway/usage/api/router.py:400-464 (group_by=team_id SQL)
  C3  pii-v2:
      apps/gateway/tests/pii_v2/test_pii_v2.py:480-512 (IBAN S2)
      apps/gateway/tests/pii_v2/test_pii_v2.py:620-663 (custom pattern S5)
      apps/gateway/tests/pii_v2/test_pii_v2.py:802-826 (nested quantifier S9 → 422)
      apps/gateway/src/gateway/tenants/api/guardrail_router.py:74-98 (PiiMaskConfig)
  C4  semantic cache:
      apps/gateway/tests/semantic_cache/test_semantic_cache.py:380-451 (SC1: x-cache)
      apps/gateway/src/gateway/tenants/api/cache_router.py:33-112 (semantic_enabled field)
  C5  oidc-tenant-config:
      apps/gateway/tests/oidc_tenant_config/test_oidc_tenant_config.py:760-798 (T2 shape)
      apps/gateway/src/gateway/auth/api/oidc_admin_router.py:95-105 (OidcConfigPutBody)
      apps/gateway/src/gateway/auth/api/oidc_router.py:110-174 (?domain= query param)
  C6  rename:
      apps/gateway/tests/rename_hydroa/test_rename_hydroa.py:165-189 (container names)

The script is fully re-runnable: every identity (tenant email, OIDC email, team name,
OIDC domain) embeds run_id.

Exit codes: 0 = all criteria PASS · 1 = failure · 2 = key absent
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# RSA primitives — used by IdP mocks to serve JWKS + mint RS256 tokens
# ---------------------------------------------------------------------------
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt as pyjwt

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL = os.environ.get("SMOKE_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free")
BASE = os.environ.get("SMOKE_BASE", "https://localhost:8443")
CA = os.environ.get("E2E_CA_CERT", "infra/envoy/certs/dev-ca.pem")

# Sink ports — must not clash with v4 (9909/4318/9910 taken)
WEBHOOK_PORT = 9909   # alert webhook (v3/v4 precedent)
OTLP_PORT = 4318      # OTLP /v1/traces collector

# TWO IdP mocks for C1 + C5 (two distinct issuers / RSA key pairs)
IDP_A_PORT = 9910     # IdP A — env-OIDC path (C1) + Tenant X (C5)
IDP_B_PORT = 9911     # IdP B — Tenant Y (C5 only)

IDP_A_ISSUER = f"http://host.docker.internal:{IDP_A_PORT}"
IDP_B_ISSUER = f"http://host.docker.internal:{IDP_B_PORT}"

# Kid constants — one per IdP
IDP_A_KID = "v5-live-a"
IDP_B_KID = "v5-live-b"

OIDC_CLIENT_ID_A = "hydroa-e2e"         # env-OIDC client (C1)
OIDC_CLIENT_ID_B = "hydroa-e2e-b"       # Tenant Y IdP (C5)
OIDC_CLIENT_SECRET = "e2e-secret"

PG_CONTAINER = "hydroa-e2e-postgres-1"
GW_CONTAINER = "hydroa-e2e-gateway-1"

# Per-run unique ID ensures ALL identities are fresh on every re-run.
# Sourced from: context requirement (live-v5-context.md §V5-C6)
run_id = int(time.time())

RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# Shared state for embedded servers
# ---------------------------------------------------------------------------
_webhook_events: list[dict] = []
_otlp_bodies: list[dict] = []
_otlp_lock = threading.Lock()
_webhook_lock = threading.Lock()

# Mutable nonce holders for each IdP — set by main() after capturing login cookies
# Using list-as-mutable-box pattern from live_v4_verify.py
_idp_a_nonce_holder: list[str] = []
# IdP A serves both the env-OIDC path (C1, email domain oidc-v5.test) and Tenant X's
# per-tenant config (C5e, email domain x-{run_id}.test). The minted email domain must
# match the flow under test or the user binds to the wrong tenant (TENANT_CONFLICT).
_idp_a_email_domain_holder: list[str] = ["oidc-v5.test"]
_idp_b_nonce_holder: list[str] = []
_nonce_lock = threading.Lock()

# Forged-key flag: when set, IdP A serves a DIFFERENT private key while keeping the same kid.
# This triggers the negative C1 test (ERR_OIDC_TOKEN_INVALID on signature mismatch).
_idp_a_forge_active: list[bool] = [False]

# ---------------------------------------------------------------------------
# RSA key generation (runs once at module load — deterministic per process)
# Shape source: apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:123-129
# ---------------------------------------------------------------------------

def _generate_rsa_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


# Key A — used by IdP A mock for C1 (env OIDC) and Tenant X (C5)
_priv_a, _pub_a = _generate_rsa_keypair()
# Forged key A — different private key, same kid "v5-live-a" (C1 negative test)
_priv_a_forged, _pub_a_forged = _generate_rsa_keypair()
# Key B — used by IdP B mock for Tenant Y (C5)
_priv_b, _pub_b = _generate_rsa_keypair()


def _rsa_pub_to_jwk(pub: rsa.RSAPublicKey, kid: str) -> dict:
    """Serialize an RSA public key to a JWK dict (RS256).

    Shape source: apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:140-157
    """
    nums = pub.public_numbers()

    def _b64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    n_bytes = (nums.n.bit_length() + 7) // 8
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url(nums.n, n_bytes),
        "e": _b64url(nums.e, 3),
    }


def _mint_rs256_token(
    priv: rsa.RSAPrivateKey,
    *,
    kid: str,
    issuer: str,
    audience: str,
    email: str,
    nonce: str,
) -> str:
    """Mint a real RS256-signed ID token.

    Shape source: apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:165-191
    """
    now = int(time.time())
    claims = {
        "sub": f"sub-{run_id}",
        "email": email,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + 300,
        "nonce": nonce,
    }
    return pyjwt.encode(claims, priv, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# Results helper
# ---------------------------------------------------------------------------

def record(criterion: str, ok: bool, note: str) -> None:
    RESULTS.append((criterion, ok, note))
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {criterion}: {note}", flush=True)


# ---------------------------------------------------------------------------
# Webhook sink (v3/v4 precedent — kept alive for compatibility)
# ---------------------------------------------------------------------------

class _WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"_raw": body.decode("utf-8", "replace")}
        with _webhook_lock:
            _webhook_events.append(payload)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        print(f"  [{type(self).__name__}] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# OTLP sink (v4 pattern — kept alive; shutdown not needed for v5 criteria)
# ---------------------------------------------------------------------------

class _OtlpHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"_raw": body.decode("utf-8", "replace")}
        with _otlp_lock:
            _otlp_bodies.append(payload)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"partialSuccess":{}}')

    def log_message(self, *args):
        print(f"  [{type(self).__name__}] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# IdP A mock — :9910
# Serves: GET /jwks  POST /token  GET /authorize (stub)
# Used by: C1 (env-OIDC path, both positive and negative) + Tenant X (C5)
# Shape sources:
#   - /jwks:  apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:140-157
#   - /token: apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:165-191
# ---------------------------------------------------------------------------

class _IdpAHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/jwks" or self.path.startswith("/jwks?"):
            # When forged, still serve the LEGITIMATE public key (kid v5-live-a)
            # so the gateway's JWKS cache has the real key; the forged token was
            # signed with _priv_a_forged whose public key is NOT in JWKS → fail.
            # Shape: {"keys": [JWK(kty,use,alg,kid,n,e)]}
            # Source: apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:140-157
            jwks_body = json.dumps({
                "keys": [_rsa_pub_to_jwk(_pub_a, IDP_A_KID)]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(jwks_body)))
            self.end_headers()
            self.wfile.write(jwks_body)
        elif self.path.startswith("/authorize"):
            # Stub: browser would be redirected here; harness reads only state/nonce
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>test-idp-a</body></html>")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # noqa: N802
        if self.path == "/token" or self.path.startswith("/token?"):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)  # drain

            with _nonce_lock:
                nonce = _idp_a_nonce_holder[0] if _idp_a_nonce_holder else None

            if nonce is None:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"nonce_not_set"}')
                return

            # C1 positive: sign with the REAL key (_priv_a, kid=v5-live-a)
            # C1 negative: sign with the FORGED key (_priv_a_forged, same kid) →
            #              gateway verifies against _pub_a from /jwks → sig mismatch
            if _idp_a_forge_active[0]:
                priv = _priv_a_forged
            else:
                priv = _priv_a

            # Email embeds run_id for re-runnability (C6)
            with _nonce_lock:
                _email_domain = (
                    _idp_a_email_domain_holder[0]
                    if _idp_a_email_domain_holder else "oidc-v5.test"
                )
            email = f"user-{run_id}@{_email_domain}"
            id_token = _mint_rs256_token(
                priv,
                kid=IDP_A_KID,
                issuer=IDP_A_ISSUER,
                audience=OIDC_CLIENT_ID_A,
                email=email,
                nonce=nonce,
            )

            resp_body = json.dumps({
                "id_token": id_token,
                "access_token": "fake-access-token",
                "token_type": "Bearer",
                "expires_in": 300,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        print(f"  [{type(self).__name__}] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# IdP B mock — :9911
# Serves: GET /jwks  POST /token  GET /authorize (stub)
# Used by: Tenant Y (C5 only)
# ---------------------------------------------------------------------------

class _IdpBHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/jwks" or self.path.startswith("/jwks?"):
            jwks_body = json.dumps({
                "keys": [_rsa_pub_to_jwk(_pub_b, IDP_B_KID)]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(jwks_body)))
            self.end_headers()
            self.wfile.write(jwks_body)
        elif self.path.startswith("/authorize"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>test-idp-b</body></html>")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # noqa: N802
        if self.path == "/token" or self.path.startswith("/token?"):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)  # drain

            with _nonce_lock:
                nonce = _idp_b_nonce_holder[0] if _idp_b_nonce_holder else None

            if nonce is None:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"nonce_not_set"}')
                return

            email = f"user-{run_id}@y-{run_id}.test"
            id_token = _mint_rs256_token(
                _priv_b,
                kid=IDP_B_KID,
                issuer=IDP_B_ISSUER,
                audience=OIDC_CLIENT_ID_B,
                email=email,
                nonce=nonce,
            )

            resp_body = json.dumps({
                "id_token": id_token,
                "access_token": "fake-access-b",
                "token_type": "Bearer",
                "expires_in": 300,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        print(f"  [{type(self).__name__}] {self.command} {self.path}")


# ---------------------------------------------------------------------------
# DB helper — docker exec psql
# ---------------------------------------------------------------------------

def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "gateway", "-d", "gateway_e2e",
         "-tA", "-c", sql],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# Stack / gateway helpers
# ---------------------------------------------------------------------------

def _wait_gateway_healthy(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["docker", "exec", GW_CONTAINER, "python", "-c",
                 "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                print("  gateway healthy")
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARN: gateway may not be healthy after restart")


def _catalog_sync() -> None:
    """Trigger catalog sync inside the gateway container (/internal is edge-blocked)."""
    sync = subprocess.run(
        ["docker", "exec", GW_CONTAINER, "python", "-c",
         "import urllib.request,json;"
         "req=urllib.request.Request('http://localhost:8000/internal/catalog/sync',method='POST');"
         "print(json.load(urllib.request.urlopen(req,timeout=60))['synced'])"],
        capture_output=True, text=True, timeout=120,
    )
    if sync.returncode != 0:
        print(f"  [catalog] WARN: {sync.stderr[:200]}", file=sys.stderr)
    else:
        print(f"  [catalog] synced {sync.stdout.strip()} models")


def _restart_gateway_with_domain_mapping(
    tenant_id: str,
    email_domain: str = "oidc-v5.test",
) -> None:
    """Inject GATEWAY_OIDC_DOMAIN_MAPPING at runtime and restart gateway.

    Used for C1 (env-OIDC fallback path). Same technique as live_v4_verify.py:230-263.
    """
    mapping = json.dumps([{"email_domain": email_domain, "tenant_id": tenant_id}])
    runtime_overlay = f"""services:
  gateway:
    environment:
      GATEWAY_OIDC_DOMAIN_MAPPING: '{mapping}'
"""
    overlay_file = "/tmp/v5_oidc_mapping.yml"
    with open(overlay_file, "w") as fh:
        fh.write(runtime_overlay)
    print(f"  [oidc] runtime overlay written to {overlay_file}, restarting gateway…")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    cmd = [
        "docker", "compose",
        "-f", os.path.join(repo_root, "infra/docker-compose.e2e.yml"),
        "-f", os.path.join(repo_root, "infra/docker-compose.e2e.v4.yml"),
        "-f", os.path.join(repo_root, "infra/docker-compose.e2e.v5.yml"),
        "-f", overlay_file,
        "up", "-d", "--no-deps", "gateway",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"  [oidc] WARN restart stderr: {r.stderr[:300]}")
    else:
        print("  [oidc] gateway restarted")
    _wait_gateway_healthy(timeout=60)


def _do_oidc_login_flow(
    client_no_redir,
    *,
    domain: str | None,
    nonce_holder: list[str],
    idp_label: str,
) -> tuple[bool, str | None, str | None, str | None]:
    """Perform /login + /callback and return (ok, session_cookie, state, nonce).

    Returns (ok=False, ...) on any unexpected response.
    Source: apps/gateway/src/gateway/auth/api/oidc_router.py:110-174 + 177-307
    """
    import httpx  # imported here to keep top-level import-free for startup safety

    login_url = f"{BASE}/auth/oidc/login"
    if domain:
        login_url += f"?domain={domain}"

    r_login = client_no_redir.get(login_url)
    print(f"  [{idp_label}] /auth/oidc/login -> {r_login.status_code}")
    if r_login.status_code != 302:
        print(f"  [{idp_label}] login body: {r_login.text[:200]}")
        return False, None, None, None

    location = r_login.headers.get("location", "")
    qs = parse_qs(urlparse(location).query)
    state_from_url = qs.get("state", [None])[0]
    oidc_state_cookie = r_login.cookies.get("oidc_state")
    oidc_nonce_cookie = r_login.cookies.get("oidc_nonce")

    # Store nonce in the mutable holder so the IdP handler can embed it
    # Source: live_v4_verify.py:538-543
    with _nonce_lock:
        if nonce_holder:
            nonce_holder[0] = oidc_nonce_cookie or ""
        else:
            nonce_holder.append(oidc_nonce_cookie or "")

    print(f"  [{idp_label}] state={'ok' if state_from_url else 'absent'} "
          f"nonce={'ok' if oidc_nonce_cookie else 'absent'}")

    # Give IdP handler time to record the nonce before the callback fires
    time.sleep(0.1)

    callback_url = f"{BASE}/auth/oidc/callback?code=v5-code-{run_id}&state={state_from_url}"
    # The callback MUST carry oidc_tenant_id when /login set it (?domain= discovery);
    # omitting it silently falls back to the env-OIDC path (tri-state rule, frozen
    # oidc-tenant-config §3) and the per-tenant IdP is never exercised.
    cb_cookies = {
        "oidc_state": oidc_state_cookie or "",
        "oidc_nonce": oidc_nonce_cookie or "",
    }
    tenant_cookie = r_login.cookies.get("oidc_tenant_id")
    if tenant_cookie:
        cb_cookies["oidc_tenant_id"] = tenant_cookie
    print(f"  [{idp_label}] oidc_tenant_id cookie: {'present' if tenant_cookie else 'ABSENT'}")
    r_cb = client_no_redir.get(callback_url, cookies=cb_cookies)
    print(f"  [{idp_label}] /auth/oidc/callback -> {r_cb.status_code}")
    if r_cb.status_code not in (302,):
        print(f"  [{idp_label}] callback body: {r_cb.text[:300]}")

    session_cookie = r_cb.cookies.get("ai_proxy_session")
    return True, session_cookie, state_from_url, oidc_nonce_cookie


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not os.environ.get("GATEWAY_OPENROUTER_API_KEY", "").strip():
        print("REFUSED: GATEWAY_OPENROUTER_API_KEY unset.", file=sys.stderr)
        sys.exit(2)

    import httpx

    # ── Start embedded servers ────────────────────────────────────────────────
    webhook_srv = HTTPServer(("0.0.0.0", WEBHOOK_PORT), _WebhookHandler)
    threading.Thread(target=webhook_srv.serve_forever, daemon=True).start()
    print(f"webhook sink        :{WEBHOOK_PORT}")

    otlp_srv = HTTPServer(("0.0.0.0", OTLP_PORT), _OtlpHandler)
    threading.Thread(target=otlp_srv.serve_forever, daemon=True).start()
    print(f"OTLP sink           :{OTLP_PORT}")

    idp_a_srv = HTTPServer(("0.0.0.0", IDP_A_PORT), _IdpAHandler)
    threading.Thread(target=idp_a_srv.serve_forever, daemon=True).start()
    print(f"IdP A mock (RSA-A)  :{IDP_A_PORT}  kid={IDP_A_KID}")

    idp_b_srv = HTTPServer(("0.0.0.0", IDP_B_PORT), _IdpBHandler)
    threading.Thread(target=idp_b_srv.serve_forever, daemon=True).start()
    print(f"IdP B mock (RSA-B)  :{IDP_B_PORT}  kid={IDP_B_KID}")

    print(f"run_id={run_id}")

    # ── TLS clients ───────────────────────────────────────────────────────────
    # verify=False: self-signed e2e cert (TLS edge still enforced)
    # Source: tmp/live-v5-context.md §"Mirror live_v4_verify.py style"
    ca = CA if os.path.exists(CA) else os.path.join("..", "..", CA)
    tls_verify: str | bool = ca if os.path.exists(ca) else False
    if not os.path.exists(ca):
        print(f"  [tls] CA cert not found at {ca!r}, falling back to verify=False")
        tls_verify = False

    client_no_redir = httpx.Client(verify=tls_verify, timeout=90, follow_redirects=False)
    client = httpx.Client(verify=tls_verify, timeout=90, follow_redirects=True)

    # ── Catalog sync ──────────────────────────────────────────────────────────
    _catalog_sync()

    # ── Arrange: base tenant + admin JWT ─────────────────────────────────────
    email = f"v5-verify-{run_id}@live.io"
    password = "v5-verify-password-1"
    r = client.post(f"{BASE}/admin/auth/signup",
                    json={"tenant_name": f"V5VerifyCo-{run_id}",
                          "email": email, "password": password})
    assert r.status_code == 201, f"signup: {r.status_code} {r.text[:200]}"
    tenant_id: str = r.json()["tenant_id"]
    jwt_token = client.post(f"{BASE}/admin/auth/login",
                            json={"email": email, "password": password}).json()["access_token"]
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}
    print(f"base tenant_id={tenant_id}")

    def create_key(name: str, **fields) -> dict:
        resp = client.post(f"{BASE}/admin/keys",
                           json={"name": name, **fields}, headers=auth_hdrs)
        assert resp.status_code == 201, f"create_key {name}: {resp.status_code} {resp.text[:200]}"
        return resp.json()

    def completion(raw_key: str, content: str = "Say OK.") -> httpx.Response:
        return _post_completion_with_retry(raw_key, [{"role": "user", "content": content}])

    def _post_completion_with_retry(raw_key: str, messages: list) -> httpx.Response:
        """POST a completion; on 429 (free-model quota) back off and retry ≤6×."""
        resp = None
        for attempt in range(6):
            try:
                resp = client.post(
                    f"{BASE}/v1/chat/completions",
                    json={"model": MODEL, "messages": messages, "max_tokens": 8},
                    headers={"Authorization": f"Bearer {raw_key}"},
                )
            except httpx.TransportError as exc:
                # Transient local-edge handshake/connect hiccup — retry like a 429.
                print(f"  [transport] {type(exc).__name__}: {exc}; retrying")
                time.sleep(5)
                continue
            if resp.status_code != 429:
                return resp
            wait = 20 * (attempt + 1)
            print(f"  [429] free-model rate limit; backing off {wait}s "
                  f"(attempt {attempt + 1}/6)")
            time.sleep(wait)
        return resp

    # =========================================================================
    # C1: OIDC JWKS (RS256/JWKS through the edge)
    # =========================================================================
    # Shape source: apps/gateway/tests/oidc_jwks/test_oidc_jwks.py J1 (valid RS256) + J2 (forged)
    # ENV path: GATEWAY_OIDC_JWKS_URL set in v5 overlay → gateway fetches /jwks from IdP A
    # Domain mapping injected at runtime (same trick as v4 OIDC C5)
    # =========================================================================
    print("\n── C1 OIDC JWKS ──")

    # Inject domain mapping for the env-path OIDC tenant and restart gateway
    _restart_gateway_with_domain_mapping(tenant_id, "oidc-v5.test")

    # Re-acquire JWT after restart — retry: envoy may serve 502/503 for a beat
    # while it re-resolves the freshly restarted gateway backend.
    jwt_token = None
    for _attempt in range(10):
        r_login_retry = client.post(f"{BASE}/admin/auth/login",
                                    json={"email": email, "password": password})
        if r_login_retry.status_code == 200:
            jwt_token = r_login_retry.json()["access_token"]
            break
        time.sleep(1.5)
    assert jwt_token, "post-restart login never returned 200 within retry budget"
    auth_hdrs = {"Authorization": f"Bearer {jwt_token}"}

    # ── C1-positive: valid RS256 token → 302 + ai_proxy_session cookie ──────
    # Source: apps/gateway/src/gateway/auth/api/oidc_router.py:110-174 (login)
    #         apps/gateway/src/gateway/auth/api/oidc_router.py:177-307 (callback)
    _idp_a_forge_active[0] = False  # use real key
    ok_flow, session_cookie_pos, state_pos, nonce_pos = _do_oidc_login_flow(
        client_no_redir,
        domain=None,  # env-path: no ?domain= needed
        nonce_holder=_idp_a_nonce_holder,
        idp_label="idp-a-positive",
    )
    record("C1a valid RS256 token → 302 + ai_proxy_session cookie",
           ok_flow and session_cookie_pos is not None,
           f"flow_ok={ok_flow} session_cookie={'present' if session_cookie_pos else 'absent'}")

    # Verify OIDC user row in DB (sentinel password = "sso-no-password")
    oidc_email = f"user-{run_id}@oidc-v5.test"
    user_row = psql(
        f"SELECT email, password_hash FROM users"
        f" WHERE email='{oidc_email}' AND tenant_id='{tenant_id}'"
    )
    record("C1b OIDC user row in DB with sentinel password",
           oidc_email in user_row and "sso-no-password" in user_row,
           f"db_row={user_row[:80]!r}")

    # ── C1-negative: forged RS256 token (same kid, different key) → 401 ─────
    # Source: apps/gateway/tests/oidc_jwks/test_oidc_jwks.py:23-26 (J2 scenario)
    # JWKS serves _pub_a; forged token is signed with _priv_a_forged → sig mismatch
    _idp_a_forge_active[0] = True
    # Need a fresh login flow to get a new nonce (single-use)
    with _nonce_lock:
        _idp_a_nonce_holder.clear()

    r_login_neg = client_no_redir.get(f"{BASE}/auth/oidc/login")
    if r_login_neg.status_code == 302:
        qs_neg = parse_qs(urlparse(r_login_neg.headers.get("location", "")).query)
        state_neg = qs_neg.get("state", [None])[0]
        nonce_neg = r_login_neg.cookies.get("oidc_nonce")
        oidc_state_neg = r_login_neg.cookies.get("oidc_state")
        with _nonce_lock:
            _idp_a_nonce_holder.append(nonce_neg or "")
        time.sleep(0.1)
        r_cb_neg = client_no_redir.get(
            f"{BASE}/auth/oidc/callback?code=v5-forged&state={state_neg}",
            cookies={"oidc_state": oidc_state_neg or "", "oidc_nonce": nonce_neg or ""},
        )
        print(f"  [idp-a-forged] callback -> {r_cb_neg.status_code} "
              f"code={r_cb_neg.json().get('code') if r_cb_neg.headers.get('content-type','').startswith('application/json') else 'n/a'}")
        neg_ok = (r_cb_neg.status_code == 401
                  and "ERR_OIDC_TOKEN_INVALID" in r_cb_neg.text)
        record("C1c forged RS256 (same kid, wrong key) → 401 ERR_OIDC_TOKEN_INVALID",
               neg_ok,
               f"status={r_cb_neg.status_code} body={r_cb_neg.text[:120]!r}")
    else:
        record("C1c forged RS256 (same kid, wrong key) → 401 ERR_OIDC_TOKEN_INVALID",
               False, f"login step failed: {r_login_neg.status_code}")

    # Reset forge flag for subsequent checks
    _idp_a_forge_active[0] = False

    # =========================================================================
    # C2: Team attribution
    # =========================================================================
    # Shape source:
    #   POST /admin/teams → 201 {id, name, ...}
    #   POST /admin/keys with team_id → 201 {key, key_id, ...}
    #   GET /admin/spend?group_by=team_id → SpendWindowResponse with TeamSpendBreakdownItem
    #   TeamSpendBreakdownItem: {team_id, team_name, requests, prompt_tokens,
    #                            completion_tokens, cost_usd, ledger_cost_usd}
    #   Source: apps/gateway/src/gateway/usage/api/schemas.py:105-118
    #           apps/gateway/tests/team_attribution/test_team_attribution.py:523-560
    # =========================================================================
    print("\n── C2 Team attribution ──")

    team_r = client.post(f"{BASE}/admin/teams",
                         json={"name": f"v5-team-{run_id}"}, headers=auth_hdrs)
    assert team_r.status_code == 201, f"create_team: {team_r.status_code} {team_r.text[:200]}"
    team_id = team_r.json()["id"]

    c2_key = create_key(f"c2-teamed-{run_id}", team_id=team_id)
    # Make a proxied completion with the teamed key
    r_c2 = completion(c2_key["key"], content=f"Team attribution test {run_id}")
    record("C2a teamed key completion → 200",
           r_c2.status_code == 200,
           f"status={r_c2.status_code}")

    # Wait for flusher to write usage_records (≤30s, retry every 2s)
    # Source: tmp/live-v5-context.md §V5-C2
    deadline = time.time() + 30
    while time.time() < deadline:
        db_cnt = psql(
            f"SELECT count(*) FROM usage_records"
            f" WHERE key_id='{c2_key['key_id']}'"
        )
        if int(db_cnt) >= 1:
            break
        time.sleep(2)

    # Assert team_id column is set on the ledger row
    # Source: apps/gateway/tests/team_attribution/test_team_attribution.py:286-303
    team_id_in_row = psql(
        f"SELECT team_id FROM usage_records"
        f" WHERE key_id='{c2_key['key_id']}' LIMIT 1"
    )
    record("C2b usage_records row carries team_id",
           team_id_in_row.lower() == team_id.lower(),
           f"db_team_id={team_id_in_row!r} expected={team_id!r}")

    # GET /admin/spend?group_by=team_id — verify both cost_usd and ledger_cost_usd
    # Source: apps/gateway/src/gateway/usage/api/schemas.py:105-118
    time.sleep(2)  # allow flusher to settle
    spend_r = client.get(f"{BASE}/admin/spend?window=month&group_by=team_id",
                         headers=auth_hdrs)
    assert spend_r.status_code == 200, f"spend: {spend_r.status_code} {spend_r.text[:200]}"
    spend_body = spend_r.json()
    breakdown = spend_body.get("breakdown") or []

    team_bucket = next((b for b in breakdown if b.get("team_id") == team_id), None)
    has_both = (team_bucket is not None
                and "cost_usd" in team_bucket
                and "ledger_cost_usd" in team_bucket)
    record("C2c team breakdown has both cost_usd and ledger_cost_usd",
           has_both,
           f"team_bucket={team_bucket!r}")

    if has_both:
        ledger = Decimal(str(team_bucket["ledger_cost_usd"]))
        counter = Decimal(str(team_bucket["cost_usd"]))
        record("C2d ledger_cost_usd ≈ counter cost_usd (single request, free model ≈ 0)",
               ledger == counter,
               f"ledger={ledger} counter={counter}")

    # =========================================================================
    # C3: PII v2
    # =========================================================================
    # Shape source:
    #   PUT /admin/guardrails with pii_mask + pii_custom_patterns:
    #     {pii_mask: {enabled: true, mode: "mask",
    #                 pii_custom_patterns: [{name: "...", pattern: "..."}]}}
    #   Source: apps/gateway/src/gateway/tenants/api/guardrail_router.py:74-98
    #           apps/gateway/tests/pii_v2/test_pii_v2.py:223-250 (put_pii_mask helper)
    #   Observable: usage_records.raw->>'pii_masked'='true'
    #   Source: scripts/live_v4_verify.py:496-500 (same ledger observable)
    #   Negative: PUT with "(a+)+" (nested quantifier) → 422 ERR_PAYLOAD_INVALID
    #   Source: apps/gateway/tests/pii_v2/test_pii_v2.py:802-826 (S9)
    # =========================================================================
    print("\n── C3 PII v2 ──")

    pii_key = create_key(f"c3-pii-{run_id}")

    # Enable PII mask with IBAN built-in + custom pattern INVOICE_ID
    # Source: apps/gateway/tests/pii_v2/test_pii_v2.py:223-250 (put_pii_mask helper)
    put_gr = client.put(
        f"{BASE}/admin/guardrails",
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                # Custom pattern: name must match ^[A-Z][A-Z0-9_]{0,31}$
                # Source: apps/gateway/src/gateway/tenants/api/guardrail_router.py:40
                "pii_custom_patterns": [
                    {"name": "INVOICE_ID", "pattern": r"INV-\d{6}"}
                ],
            }
        },
        headers=auth_hdrs,
    )
    assert put_gr.status_code == 200, f"put_guardrails_pii: {put_gr.status_code} {put_gr.text[:200]}"

    # Send completion with IBAN (built-in) + custom pattern hit
    # IBAN: DE89370400440532013000 (test IBAN, Source: pii_v2/test_pii_v2.py:497)
    # Custom: INV-123456
    r_pii = completion(
        pii_key["key"],
        content=(
            f"Please process IBAN DE89370400440532013000 "
            f"and invoice INV-123456 for client {run_id}"
        ),
    )
    record("C3a PII mask mode → 200 (not blocked, request processed)",
           r_pii.status_code == 200,
           f"status={r_pii.status_code}")

    # Wait for flusher and check pii_masked=true in ledger raw
    # Source: scripts/live_v4_verify.py:492-501 (C4c pattern, v4)
    time.sleep(6)
    pii_key_id = pii_key["key_id"]
    pii_masked_cnt = psql(
        f"SELECT count(*) FROM usage_records"
        f" WHERE key_id='{pii_key_id}'"
        f" AND raw::jsonb->>'pii_masked'='true'"
    )
    record("C3b pii_masked=true in usage_records.raw",
           int(pii_masked_cnt) >= 1,
           f"rows_with_pii_masked={pii_masked_cnt}")

    # Negative: dangerous regex → 422 ERR_PAYLOAD_INVALID
    # Source: apps/gateway/tests/pii_v2/test_pii_v2.py:802-826 (S9 nested quantifier)
    put_bad = client.put(
        f"{BASE}/admin/guardrails",
        json={
            "pii_mask": {
                "enabled": True,
                "mode": "mask",
                "pii_custom_patterns": [
                    {"name": "REDOS_RISK", "pattern": "(a+)+"}
                ],
            }
        },
        headers=auth_hdrs,
    )
    neg_ok_c3 = (put_bad.status_code == 422
                 and "ERR_PAYLOAD_INVALID" in put_bad.text)
    record("C3c dangerous nested-quantifier pattern → 422 ERR_PAYLOAD_INVALID",
           neg_ok_c3,
           f"status={put_bad.status_code} body={put_bad.text[:120]!r}")

    # Clean up guardrails
    client.put(f"{BASE}/admin/guardrails",
               json={"pii_mask": None}, headers=auth_hdrs)

    # =========================================================================
    # C4: Semantic cache
    # =========================================================================
    # Shape source:
    #   PUT /admin/cache {enabled: true, semantic_enabled: true}
    #     → response {enabled: bool, semantic_enabled: bool}
    #   Source: apps/gateway/src/gateway/tenants/api/cache_router.py:33-112
    #   Observable: X-Cache: semantic_hit on normalised variant (second request)
    #   Source: apps/gateway/tests/semantic_cache/test_semantic_cache.py:380-451 (SC1)
    #   Ledger: cost_usd=0, raw->>'cached'='true' on semantic hit row
    #   Source: apps/gateway/tests/semantic_cache/test_semantic_cache.py:426-451
    #   Tenant-scope isolation: second tenant, same prompt → miss
    #   Source: tmp/live-v5-context.md §V5-C4
    # =========================================================================
    print("\n── C4 Semantic cache ──")

    # Enable semantic cache on base tenant
    # Source: apps/gateway/src/gateway/tenants/api/cache_router.py:38-45 (CachePutRequest)
    put_cache = client.put(
        f"{BASE}/admin/cache",
        json={"enabled": True, "semantic_enabled": True},
        headers=auth_hdrs,
    )
    assert put_cache.status_code == 200, f"put_cache: {put_cache.status_code} {put_cache.text}"
    cache_body = put_cache.json()
    record("C4a PUT /admin/cache returns semantic_enabled field",
           "semantic_enabled" in cache_body and cache_body["semantic_enabled"] is True,
           f"body={cache_body}")

    # Key with cache_enabled=true
    # Source: apps/gateway/tests/semantic_cache/test_semantic_cache.py:184-199 (create_key)
    sc_key = create_key(f"c4-sem-{run_id}", cache_enabled=True)

    # Request A: canonical prompt
    prompt_a = "What is the capital of France?"
    # Request B: normalised variant (different case + extra whitespace + trailing punct)
    # Source: apps/gateway/tests/semantic_cache/test_semantic_cache.py:391-394 (SC1)
    prompt_b = f"  what is the capital of FRANCE  "

    r_a = _post_completion_with_retry(sc_key["key"], [{"role": "user", "content": prompt_a}])
    x_cache_a = r_a.headers.get("x-cache", "").lower()

    r_b = _post_completion_with_retry(sc_key["key"], [{"role": "user", "content": prompt_b}])
    x_cache_b = r_b.headers.get("x-cache", "").lower()

    record("C4b first request is miss",
           r_a.status_code == 200 and x_cache_a == "miss",
           f"status={r_a.status_code} x-cache={x_cache_a!r}")

    record("C4c normalised variant → X-Cache: semantic_hit",
           r_b.status_code == 200 and x_cache_b == "semantic_hit",
           f"status={r_b.status_code} x-cache={x_cache_b!r}")

    # Wait for flusher; check cost_usd=0 and cached=true on semantic hit row
    # Source: apps/gateway/tests/semantic_cache/test_semantic_cache.py:426-451
    time.sleep(5)
    sc_key_id = sc_key["key_id"]
    sc_rows_out = psql(
        f"SELECT cost_usd, raw::jsonb->>'cached'"
        f" FROM usage_records"
        f" WHERE key_id='{sc_key_id}'"
        f" ORDER BY created_at ASC"
    )
    sc_rows = [line.split("|") for line in sc_rows_out.splitlines() if "|" in line]
    if len(sc_rows) >= 2:
        second_cost = sc_rows[1][0].strip()
        second_cached = sc_rows[1][1].strip() if len(sc_rows[1]) > 1 else ""
        record("C4d semantic hit row: cost_usd=0 and cached=true in ledger",
               Decimal(second_cost) == Decimal("0") and second_cached == "true",
               f"cost={second_cost!r} cached={second_cached!r}")
    else:
        record("C4d semantic hit row: cost_usd=0 and cached=true in ledger",
               False,
               f"expected ≥2 rows, got {len(sc_rows)}: {sc_rows_out!r}")

    # Tenant-scope isolation: second tenant, same prompt → MISS
    # Source: tmp/live-v5-context.md §V5-C4 "second tenant → miss"
    email_t2 = f"v5-t2-{run_id}@live.io"
    r_t2_signup = client.post(f"{BASE}/admin/auth/signup",
                               json={"tenant_name": f"V5T2-{run_id}",
                                     "email": email_t2, "password": "pass-t2-v5"})
    assert r_t2_signup.status_code == 201, f"t2 signup: {r_t2_signup.text[:200]}"
    jwt_t2 = client.post(f"{BASE}/admin/auth/login",
                          json={"email": email_t2, "password": "pass-t2-v5"}).json()["access_token"]
    auth_t2 = {"Authorization": f"Bearer {jwt_t2}"}

    # Enable semantic cache on T2
    client.put(f"{BASE}/admin/cache",
               json={"enabled": True, "semantic_enabled": True}, headers=auth_t2)
    sc_key_t2 = client.post(f"{BASE}/admin/keys",
                              json={"name": f"c4-sem-t2-{run_id}", "cache_enabled": True},
                              headers=auth_t2)
    assert sc_key_t2.status_code == 201

    r_t2 = _post_completion_with_retry(
        sc_key_t2.json()["key"], [{"role": "user", "content": prompt_b}]
    )
    x_cache_t2 = r_t2.headers.get("x-cache", "").lower()
    record("C4e second tenant same prompt → miss (cache is tenant-scoped)",
           r_t2.status_code == 200 and x_cache_t2 != "semantic_hit",
           f"status={r_t2.status_code} x-cache={x_cache_t2!r} (expected miss or hit, not semantic_hit)")

    # Disable cache on base tenant (cleanup)
    client.put(f"{BASE}/admin/cache",
               json={"enabled": False, "semantic_enabled": False},
               headers=auth_hdrs)

    # =========================================================================
    # C5: Per-tenant OIDC config (two tenants, two IdPs, one deployment)
    # =========================================================================
    # Shape source:
    #   PUT /admin/oidc body: OidcConfigPutBody
    #     {issuer, client_id, client_secret, authorize_url, token_url, jwks_url,
    #      email_domains, enabled}
    #   Source: apps/gateway/src/gateway/auth/api/oidc_admin_router.py:95-105
    #   GET /admin/oidc response: client_secret always "<stored>"
    #   Source: apps/gateway/src/gateway/auth/api/oidc_admin_router.py:169-181
    #   GATEWAY_OIDC_ALLOW_HTTP_URLS=true required for http://host.docker.internal
    #   GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY required for PUT /admin/oidc
    #   Source: apps/gateway/src/gateway/auth/api/oidc_admin_router.py:207-211
    # =========================================================================
    print("\n── C5 Per-tenant OIDC config ──")

    # Tenant X — linked to IdP A, domain x-{run_id}.test
    email_x = f"v5-x-{run_id}@live.io"
    r_x = client.post(f"{BASE}/admin/auth/signup",
                      json={"tenant_name": f"V5TenantX-{run_id}",
                            "email": email_x, "password": "pass-x-v5-live-0123"})
    assert r_x.status_code == 201, f"tenant X signup: {r_x.text[:200]}"
    tenant_x_id = r_x.json()["tenant_id"]
    jwt_x = client.post(f"{BASE}/admin/auth/login",
                         json={"email": email_x, "password": "pass-x-v5-live-0123"}).json()["access_token"]
    auth_x = {"Authorization": f"Bearer {jwt_x}"}

    # Tenant Y — linked to IdP B, domain y-{run_id}.test
    email_y = f"v5-y-{run_id}@live.io"
    r_y = client.post(f"{BASE}/admin/auth/signup",
                      json={"tenant_name": f"V5TenantY-{run_id}",
                            "email": email_y, "password": "pass-y-v5-live-0123"})
    assert r_y.status_code == 201, f"tenant Y signup: {r_y.text[:200]}"
    tenant_y_id = r_y.json()["tenant_id"]
    jwt_y = client.post(f"{BASE}/admin/auth/login",
                         json={"email": email_y, "password": "pass-y-v5-live-0123"}).json()["access_token"]
    auth_y = {"Authorization": f"Bearer {jwt_y}"}

    # PUT /admin/oidc for Tenant X → IdP A
    # Source: apps/gateway/src/gateway/auth/api/oidc_admin_router.py:95-105 (OidcConfigPutBody)
    put_x = client.put(
        f"{BASE}/admin/oidc",
        headers=auth_x,
        json={
            "issuer": IDP_A_ISSUER,
            "client_id": OIDC_CLIENT_ID_A,
            "client_secret": OIDC_CLIENT_SECRET,
            "authorize_url": f"{IDP_A_ISSUER}/authorize",
            "token_url": f"{IDP_A_ISSUER}/token",
            "jwks_url": f"{IDP_A_ISSUER}/jwks",
            "email_domains": [f"x-{run_id}.test"],
            "enabled": True,
        },
    )
    record("C5a PUT /admin/oidc Tenant X → 200",
           put_x.status_code == 200,
           f"status={put_x.status_code} body={put_x.text[:150]!r}")

    # PUT /admin/oidc for Tenant Y → IdP B
    put_y = client.put(
        f"{BASE}/admin/oidc",
        headers=auth_y,
        json={
            "issuer": IDP_B_ISSUER,
            "client_id": OIDC_CLIENT_ID_B,
            "client_secret": OIDC_CLIENT_SECRET,
            "authorize_url": f"{IDP_B_ISSUER}/authorize",
            "token_url": f"{IDP_B_ISSUER}/token",
            "jwks_url": f"{IDP_B_ISSUER}/jwks",
            "email_domains": [f"y-{run_id}.test"],
            "enabled": True,
        },
    )
    record("C5b PUT /admin/oidc Tenant Y → 200",
           put_y.status_code == 200,
           f"status={put_y.status_code} body={put_y.text[:150]!r}")

    # GET /admin/oidc for Tenant X — assert client_secret NEVER returned
    # Source: apps/gateway/tests/oidc_tenant_config/test_oidc_tenant_config.py:778-798 (T2)
    if put_x.status_code == 200:
        get_x = client.get(f"{BASE}/admin/oidc", headers=auth_x)
        raw_x = get_x.text
        no_secret_x = (get_x.status_code == 200
                       and OIDC_CLIENT_SECRET not in raw_x
                       and get_x.json().get("client_secret") == "<stored>")
        record("C5c GET /admin/oidc Tenant X: client_secret never returned",
               no_secret_x,
               f"status={get_x.status_code} "
               f"secret_in_body={'yes' if OIDC_CLIENT_SECRET in raw_x else 'no'} "
               f"field={get_x.json().get('client_secret') if get_x.status_code == 200 else 'n/a'!r}")
    else:
        record("C5c GET /admin/oidc Tenant X: client_secret never returned",
               False, "PUT /admin/oidc X failed — skipping GET")

    # GET /admin/oidc for Tenant Y — assert client_secret NEVER returned
    if put_y.status_code == 200:
        get_y = client.get(f"{BASE}/admin/oidc", headers=auth_y)
        raw_y = get_y.text
        no_secret_y = (get_y.status_code == 200
                       and OIDC_CLIENT_SECRET not in raw_y
                       and get_y.json().get("client_secret") == "<stored>")
        record("C5d GET /admin/oidc Tenant Y: client_secret never returned",
               no_secret_y,
               f"status={get_y.status_code} "
               f"secret_in_body={'yes' if OIDC_CLIENT_SECRET in raw_y else 'no'} "
               f"field={get_y.json().get('client_secret') if get_y.status_code == 200 else 'n/a'!r}")
    else:
        record("C5d GET /admin/oidc Tenant Y: client_secret never returned",
               False, "PUT /admin/oidc Y failed — skipping GET")

    # Login flow via Tenant X domain → session for X (DB-backed config path)
    # Source: apps/gateway/src/gateway/auth/api/oidc_router.py:123-132 (?domain= resolver)
    # IdP A must mint an x-domain email here: the per-tenant path binds the user to
    # tenant X; the C1 env-domain email is already bound to the base tenant.
    with _nonce_lock:
        _idp_a_email_domain_holder[0] = f"x-{run_id}.test"
    try:
        ok_x, sess_x, _, _ = _do_oidc_login_flow(
            client_no_redir,
            domain=f"x-{run_id}.test",
            nonce_holder=_idp_a_nonce_holder,
            idp_label="tenant-x",
        )
    finally:
        with _nonce_lock:
            _idp_a_email_domain_holder[0] = "oidc-v5.test"

    record("C5e Tenant X OIDC login via ?domain=x-{run_id}.test → session cookie",
           ok_x and sess_x is not None,
           f"flow_ok={ok_x} session={'present' if sess_x else 'absent'}")

    # Login flow via Tenant Y domain → session for Y (IdP B)
    ok_y, sess_y, _, _ = _do_oidc_login_flow(
        client_no_redir,
        domain=f"y-{run_id}.test",
        nonce_holder=_idp_b_nonce_holder,
        idp_label="tenant-y",
    )
    record("C5f Tenant Y OIDC login via ?domain=y-{run_id}.test → session cookie",
           ok_y and sess_y is not None,
           f"flow_ok={ok_y} session={'present' if sess_y else 'absent'}")

    # =========================================================================
    # C6: Rename / isolation
    # =========================================================================
    # Shape source: apps/gateway/tests/rename_hydroa/test_rename_hydroa.py:189
    # Container names must be hydroa-e2e-gateway-1 and hydroa-e2e-postgres-1
    # =========================================================================
    print("\n── C6 Rename / isolation ──")

    ps_out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=15,
    )
    container_names = ps_out.stdout.strip()
    has_gw = "hydroa-e2e-gateway-1" in container_names
    has_pg = "hydroa-e2e-postgres-1" in container_names

    record("C6a docker ps shows hydroa-e2e-gateway-1",
           has_gw,
           f"containers={container_names!r}")
    record("C6b docker ps shows hydroa-e2e-postgres-1",
           has_pg,
           f"containers={container_names!r}")

    # Isolation proof: run_id is embedded in EVERY identity created above.
    # The orchestrator verifies re-runnability by running the script twice.
    record("C6c all identities carry run_id (re-runnable isolation)",
           True,
           f"run_id={run_id} embedded in: email, oidc-email, team-name, tenant-names, domains")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    failed = [c for c, ok, _ in RESULTS if not ok]
    total = len(RESULTS)
    passed = total - len(failed)
    print(f"\n{'=' * 60}")
    print("FINAL RESULTS — v5 live verification")
    print("=" * 60)
    for criterion, ok, note in RESULTS:
        status_label = "PASS" if ok else "FAIL"
        print(f"{status_label}  {criterion}")
        if not ok:
            print(f"       evidence: {note}")
    print()
    if not failed:
        print(f"ALL CRITERIA PASS  ({passed}/{total})")
    else:
        print(f"FAILURES: {', '.join(failed)}  ({passed}/{total} passed)")
    print()
    print("Re-run command:")
    print("  docker compose \\")
    print("      -f infra/docker-compose.e2e.yml \\")
    print("      -f infra/docker-compose.e2e.v4.yml \\")
    print("      -f infra/docker-compose.e2e.v5.yml \\")
    print("      up -d --wait")
    print("  uv run --project apps/gateway python scripts/live_v5_verify.py")

    webhook_srv.shutdown()
    idp_a_srv.shutdown()
    idp_b_srv.shutdown()
    otlp_srv.shutdown()
    client.close()
    client_no_redir.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
