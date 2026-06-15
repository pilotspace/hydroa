/**
 * tests-bff/auth-me-verify.test.ts — v18 auth-me-session-verify (SECURITY)
 *
 * The hardened GET /api/auth/me is a thin RELAY: it forwards the ai_proxy_session
 * cookie value as `Authorization: Bearer <token>` to the gateway's authoritative
 * verifier GET {GATEWAY_URL}/admin/auth/me (HS256 sig + issuer + required-claims +
 * exp) and trusts ONLY a gateway-verified 200. It is FAIL-CLOSED: any error path
 * returns an error code and ZERO identity claims, holds no signing secret, and
 * never echoes the raw JWT.
 *
 * These tests mock the GATEWAY fetch (never a real upstream). They are RED until
 * Build rewrites the route from local base64-decode into the relay.
 *
 * Frozen §3 contract (v18 auth-me-session-verify @ v1):
 *   200 -> { user_id, tenant_id, email, role, exp: null }   (mapped from MeResponse)
 *   401 -> { code: "ERR_AUTH_NO_SESSION" }       (no / empty cookie — NO upstream call)
 *   401 -> { code: "ERR_AUTH_INVALID_SESSION" }  (gateway 401)
 *   503 -> { code: "ERR_AUTH_UPSTREAM" }         (unreachable / timeout / 5xx — fail-closed)
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { GET as meHandler } from "@/app/api/auth/me/route";

const GATEWAY = "http://gateway.test";
const ME_URL = "http://localhost:3000/api/auth/me";

// A token value is opaque to the BFF relay (the gateway verifies it), so any
// non-empty string works for forwarding assertions.
const TOKEN = "header.payload.signature";

const GW_ME = {
  user_id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "00000000-0000-0000-0000-000000000099",
  email: "ada@acme.io",
  role: "owner",
};

function requestWithCookie(jwt: string): NextRequest {
  return new NextRequest(ME_URL, { method: "GET", headers: { Cookie: `ai_proxy_session=${jwt}` } });
}

describe("GET /api/auth/me — gateway-relay verification (v18)", () => {
  it("test_verified_session_relays_and_maps", async () => {
    // Arrange: gateway verifies and returns the identity; capture the forwarded auth header.
    let captured: string | null = null;
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, ({ request }) => {
        captured = request.headers.get("Authorization");
        return HttpResponse.json(GW_ME);
      }),
    );

    // Act
    const res = await meHandler(requestWithCookie(TOKEN));

    // Assert: 200, mapped body with EXACT keys, exp null, Bearer forwarded, token never echoed.
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({
      user_id: GW_ME.user_id,
      tenant_id: GW_ME.tenant_id,
      email: GW_ME.email,
      role: GW_ME.role,
      exp: null,
    });
    expect(captured).toBe(`Bearer ${TOKEN}`);
    expect(JSON.stringify(body)).not.toContain(TOKEN);
  });

  it("test_response_shape_stable_exp_null", async () => {
    server.use(http.get(`${GATEWAY}/admin/auth/me`, () => HttpResponse.json(GW_ME)));
    const res = await meHandler(requestWithCookie(TOKEN));
    const body = (await res.json()) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["email", "exp", "role", "tenant_id", "user_id"]);
    expect(body.exp).toBeNull();
  });

  it("test_forged_token_rejected_failclosed", async () => {
    // Gateway rejects the (tampered / unsigned / expired) token.
    server.use(http.get(`${GATEWAY}/admin/auth/me`, () => new HttpResponse(null, { status: 401 })));
    const res = await meHandler(requestWithCookie(TOKEN));
    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_INVALID_SESSION");
    // fail-closed: NO identity claim leaks on rejection
    expect(body.user_id).toBeUndefined();
    expect(body.tenant_id).toBeUndefined();
    expect(body.role).toBeUndefined();
    expect(body.email).toBeUndefined();
  });

  it("test_no_cookie_401_no_upstream_call", async () => {
    let called = false;
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, () => {
        called = true;
        return HttpResponse.json(GW_ME);
      }),
    );
    const res = await meHandler(new NextRequest(ME_URL, { method: "GET" }));
    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
    expect(called).toBe(false); // no token → never touches the gateway
  });

  it("test_empty_token_401_no_upstream_call", async () => {
    let called = false;
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, () => {
        called = true;
        return HttpResponse.json(GW_ME);
      }),
    );
    const res = await meHandler(requestWithCookie(""));
    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
    expect(called).toBe(false);
  });

  it("test_upstream_5xx_failclosed_503", async () => {
    server.use(http.get(`${GATEWAY}/admin/auth/me`, () => new HttpResponse(null, { status: 500 })));
    const res = await meHandler(requestWithCookie(TOKEN));
    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_UPSTREAM");
    expect(body.user_id).toBeUndefined();
    expect(body.role).toBeUndefined();
  });

  it("test_upstream_network_error_failclosed_503", async () => {
    // Simulate the gateway being unreachable / connection error.
    server.use(http.get(`${GATEWAY}/admin/auth/me`, () => HttpResponse.error()));
    const res = await meHandler(requestWithCookie(TOKEN));
    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_UPSTREAM");
    expect(body.role).toBeUndefined();
  });

  it("test_upstream_redirect_failclosed_no_claims", async () => {
    // A gateway/infra 3xx must NOT be followed to a 200 from another origin and
    // relayed as a trusted identity. With redirect:"manual" a 3xx is !ok → 503.
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, () =>
        new HttpResponse(null, { status: 302, headers: { Location: "http://evil.test/me" } }),
      ),
    );
    const res = await meHandler(requestWithCookie(TOKEN));
    expect(res.status).toBe(503);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_UPSTREAM");
    expect(body.user_id).toBeUndefined();
    expect(body.role).toBeUndefined();
  });

  it("test_whitespace_token_treated_as_no_session", async () => {
    // A whitespace-only cookie value collapses to "absent" → no-session, never a
    // malformed `Bearer  <ws>` forwarded upstream.
    let called = false;
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, () => {
        called = true;
        return HttpResponse.json(GW_ME);
      }),
    );
    const res = await meHandler(requestWithCookie("   "));
    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
    expect(called).toBe(false);
  });

  it("test_route_holds_no_signing_secret", () => {
    // Structural invariant: verification is delegated to the gateway; the BFF reads no signing
    // secret and performs no local signature/JWT verification. The assertions are PRECISE —
    // they catch a real secret-env read / jwt-lib import / crypto-verify call, NOT the bare word
    // "secret" in an explanatory comment (an over-broad /SECRET/ would false-positive on prose).
    const src = readFileSync(resolve(process.cwd(), "app/api/auth/me/route.ts"), "utf8");
    // no signing-material env read (secret / key / hmac / password / token names)
    expect(src).not.toMatch(/process\.env\.[A-Za-z0-9_]*(secret|key|hmac|password|token)/i);
    expect(src).not.toMatch(/jwt[_-]?secret/i); // no shared signing secret
    expect(src).not.toMatch(/from\s+["'](jsonwebtoken|jose)["']/); // no JWT verification lib
    expect(src).not.toMatch(/createHmac|verifySignature|jwtVerify|jwt\.decode/); // no local verify
  });

  it("test_upstream_io_is_bounded_and_redirect_hardened", () => {
    // Designed-for-failure: the gateway hop MUST be timeout-bounded and MUST NOT
    // follow redirects (else a 3xx could chain to a trusted 200 from another origin).
    const src = readFileSync(resolve(process.cwd(), "app/api/auth/me/route.ts"), "utf8");
    expect(src).toMatch(/AbortSignal\.timeout\(/);
    expect(src).toMatch(/redirect:\s*["']manual["']/);
  });
});
