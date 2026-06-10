/**
 * tests-bff/route-handlers.test.ts
 *
 * Unit tests for BFF route handlers: POST /api/auth/login, signup, logout,
 * GET /api/auth/me, and GET|DELETE /api/gw/[...path].
 *
 * Pattern: import the handler function directly, construct a NextRequest,
 * call the handler, assert the NextResponse (status, headers, body).
 *
 * RED failure mode: all imports from @/app/api/... will throw MODULE_NOT_FOUND
 * because the route handler files do not exist yet.
 *
 * Tests 1–10 (route-handler scenarios from §4 test plan):
 *   test_bff_login_happy_sets_cookie_redirects
 *   test_bff_login_gateway_401_no_cookie
 *   test_bff_signup_happy_sets_cookie_redirects
 *   test_bff_logout_clears_cookie
 *   test_bff_me_returns_decoded_claims
 *   test_bff_me_absent_cookie_401
 *   test_bff_me_malformed_jwt_401
 *   test_bff_proxy_forwards_bearer_returns_upstream
 *   test_bff_proxy_absent_cookie_401
 *   test_bff_proxy_upstream_401_clears_cookie
 *   test_bff_login_missing_fields_400
 *   test_bff_proxy_no_cookie_delete
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { VALID_SESSION_JWT } from "./mocks/handlers";

// ── RED: these imports fail until Build creates the route handler files ────────
// Each is imported separately so one missing file red-flags its own tests
// rather than the whole suite failing at the first line.
import { POST as loginHandler } from "@/app/api/auth/login/route";
import { POST as signupHandler } from "@/app/api/auth/signup/route";
import { POST as logoutHandler } from "@/app/api/auth/logout/route";
import { GET as meHandler } from "@/app/api/auth/me/route";
import { GET as proxyGet, DELETE as proxyDelete } from "@/app/api/gw/[...path]/route";

// ─────────────────────────────────────────────────────────────────────────────

/** Helper: build a NextRequest with a JSON body */
function jsonRequest(url: string, method: string, body: unknown): NextRequest {
  return new NextRequest(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Helper: build a NextRequest with an ai_proxy_session cookie */
function requestWithCookie(url: string, method: string, jwt: string): NextRequest {
  return new NextRequest(url, {
    method,
    headers: {
      Cookie: `ai_proxy_session=${jwt}`,
    },
  });
}

/** Helper: extract a Set-Cookie header value from a Response */
function getSetCookie(res: Response): string | null {
  // NextResponse.headers may expose Set-Cookie differently across Node versions;
  // try both the standard and the raw header name
  return res.headers.get("set-cookie") ?? res.headers.get("Set-Cookie");
}

// ─────────────────────────────────────────────────────────────────────────────

describe("POST /api/auth/login", () => {
  /**
   * TEST 1 — test_bff_login_happy_sets_cookie_redirects
   * Scenario: login happy path — sets httpOnly cookie, no token in body or localStorage
   */
  it("test_bff_login_happy_sets_cookie_redirects", async () => {
    // Arrange: default gateway handler returns VALID_SESSION_JWT
    const req = jsonRequest(
      "http://localhost:3000/api/auth/login",
      "POST",
      { email: "ada@acme.io", password: "hunter12345" }
    );

    // Act
    const res = await loginHandler(req);

    // Assert: status 200
    expect(res.status).toBe(200);

    // Body is {ok: true} — no token
    const body = await res.json() as Record<string, unknown>;
    expect(body.ok).toBe(true);
    expect(JSON.stringify(body)).not.toContain(VALID_SESSION_JWT);

    // Set-Cookie contains ai_proxy_session with httpOnly, Secure, SameSite=Strict
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain(`ai_proxy_session=${VALID_SESSION_JWT}`);
    expect(setCookie?.toLowerCase()).toContain("httponly");
    expect(setCookie?.toLowerCase()).toContain("samesite=strict");
    expect(setCookie?.toLowerCase()).toContain("path=/");

    // localStorage "ai_proxy_token" must remain absent (tested in XSS scenario)
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });

  /**
   * TEST 2 — test_bff_login_gateway_401_no_cookie
   * Scenario: login gateway 401 — passes through error, no cookie set
   */
  it("test_bff_login_gateway_401_no_cookie", async () => {
    // Arrange: override gateway to return 401
    server.use(
      http.post("http://gateway.test/admin/auth/login", () =>
        HttpResponse.json(
          { type: "about:blank", title: "Invalid credentials", status: 401, code: "ERR_AUTH_INVALID_CREDENTIALS" },
          { status: 401 }
        )
      )
    );

    const req = jsonRequest(
      "http://localhost:3000/api/auth/login",
      "POST",
      { email: "ada@acme.io", password: "wrongpass" }
    );

    // Act
    const res = await loginHandler(req);

    // Assert: 401 forwarded
    expect(res.status).toBe(401);

    // No Set-Cookie header
    const setCookie = getSetCookie(res);
    expect(setCookie).toBeNull();

    // localStorage untouched
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });

  /**
   * TEST 11 — test_bff_login_missing_fields_400
   * Scenario: POST /api/auth/login missing body fields — returns 400
   */
  it("test_bff_login_missing_fields_400", async () => {
    // Arrange: upstream should NOT be called — track this with a spy
    let upstreamCalled = false;
    server.use(
      http.post("http://gateway.test/admin/auth/login", () => {
        upstreamCalled = true;
        return HttpResponse.json({}, { status: 500 });
      })
    );

    const req = jsonRequest(
      "http://localhost:3000/api/auth/login",
      "POST",
      {} // missing email and password
    );

    // Act
    const res = await loginHandler(req);

    // Assert: 400 with code
    expect(res.status).toBe(400);
    const body = await res.json() as Record<string, unknown>;
    expect(body.code).toBe("ERR_BFF_PAYLOAD_INVALID");

    // No upstream call
    expect(upstreamCalled).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("POST /api/auth/signup", () => {
  /**
   * TEST 3 — test_bff_signup_happy_sets_cookie_redirects
   * Scenario: signup happy path — sets cookie, no token visible
   */
  it("test_bff_signup_happy_sets_cookie_redirects", async () => {
    // Arrange: default handlers cover signup→201 and login→200 VALID_SESSION_JWT
    const req = jsonRequest(
      "http://localhost:3000/api/auth/signup",
      "POST",
      { tenant_name: "Acme", email: "ada@acme.io", password: "hunter12345" }
    );

    // Act
    const res = await signupHandler(req);

    // Assert
    expect(res.status).toBe(201);

    const body = await res.json() as Record<string, unknown>;
    expect(body.ok).toBe(true);
    expect(JSON.stringify(body)).not.toContain(VALID_SESSION_JWT);

    // Cookie set with correct attributes
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_session=");
    expect(setCookie?.toLowerCase()).toContain("httponly");
    expect(setCookie?.toLowerCase()).toContain("samesite=strict");
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("POST /api/auth/logout", () => {
  /**
   * TEST 4 — test_bff_logout_clears_cookie
   * Scenario: logout — clears cookie
   */
  it("test_bff_logout_clears_cookie", async () => {
    const req = new NextRequest("http://localhost:3000/api/auth/logout", {
      method: "POST",
    });

    // Act
    const res = await logoutHandler(req);

    // Assert: 200
    expect(res.status).toBe(200);
    const body = await res.json() as Record<string, unknown>;
    expect(body.ok).toBe(true);

    // Cookie cleared — Max-Age=0 or Expires in the past
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_session=");
    const cookieLower = setCookie?.toLowerCase() ?? "";
    const clearedByMaxAge = cookieLower.includes("max-age=0");
    const clearedByExpires = cookieLower.includes("expires=") &&
      (cookieLower.includes("1970") || cookieLower.includes("thu, 01 jan"));
    expect(clearedByMaxAge || clearedByExpires).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("GET /api/auth/me", () => {
  /**
   * TEST 5 — test_bff_me_returns_decoded_claims
   * Scenario: /api/auth/me — returns decoded claims, no token
   */
  it("test_bff_me_returns_decoded_claims", async () => {
    // Arrange: request with valid session cookie
    const req = requestWithCookie(
      "http://localhost:3000/api/auth/me",
      "GET",
      VALID_SESSION_JWT
    );

    // Act
    const res = await meHandler(req);

    // Assert
    expect(res.status).toBe(200);
    const body = await res.json() as Record<string, unknown>;
    expect(body.role).toBe("owner");
    expect(body.email).toBe("ada@acme.io");
    expect(typeof body.exp).toBe("number");

    // Raw JWT must NOT appear in the response body
    expect(JSON.stringify(body)).not.toContain(VALID_SESSION_JWT);
  });

  /**
   * TEST 6 — test_bff_me_absent_cookie_401
   * Scenario: /api/auth/me — absent cookie returns 401
   */
  it("test_bff_me_absent_cookie_401", async () => {
    // Arrange: request with NO cookie
    const req = new NextRequest("http://localhost:3000/api/auth/me", { method: "GET" });

    // Act
    const res = await meHandler(req);

    // Assert
    expect(res.status).toBe(401);
    const body = await res.json() as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
  });

  /**
   * TEST 7 — test_bff_me_malformed_jwt_401
   * Scenario: /api/auth/me — malformed JWT payload returns 401
   */
  it("test_bff_me_malformed_jwt_401", async () => {
    // Arrange: cookie with a structurally wrong JWT (cannot base64-decode payload)
    const req = requestWithCookie(
      "http://localhost:3000/api/auth/me",
      "GET",
      "not.a.validjwt!!!"
    );

    // Act
    const res = await meHandler(req);

    // Assert
    expect(res.status).toBe(401);
    const body = await res.json() as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("GET /api/gw/[...path]", () => {
  /**
   * TEST 8 — test_bff_proxy_forwards_bearer_returns_upstream
   * Scenario: gateway proxy — forwards request with Bearer, returns upstream response
   */
  it("test_bff_proxy_forwards_bearer_returns_upstream", async () => {
    // Arrange: capture the Authorization header sent to the gateway
    let capturedAuthHeader: string | null = null;
    server.use(
      http.get("http://gateway.test/admin/keys", ({ request }) => {
        capturedAuthHeader = request.headers.get("Authorization");
        return HttpResponse.json([
          { key_id: "k1", name: "prod-key", prefix: "sk-1a2b3c", created_at: "2026-01-01T00:00:00Z", revoked_at: null },
        ]);
      })
    );

    const req = requestWithCookie(
      "http://localhost:3000/api/gw/admin/keys",
      "GET",
      VALID_SESSION_JWT
    );

    // Act: call the proxy handler directly
    // The catch-all route receives params: { path: ["admin", "keys"] }
    const res = await proxyGet(req, { params: { path: ["admin", "keys"] } });

    // Assert: upstream received Bearer token
    expect(capturedAuthHeader).toBe(`Bearer ${VALID_SESSION_JWT}`);

    // Response proxied correctly
    expect(res.status).toBe(200);
    const body = await res.json() as Array<Record<string, unknown>>;
    expect(body[0].key_id).toBe("k1");

    // JWT does NOT appear in the response body
    expect(JSON.stringify(body)).not.toContain(VALID_SESSION_JWT);
  });

  /**
   * TEST 9 — test_bff_proxy_absent_cookie_401
   * Scenario: gateway proxy — absent cookie returns 401
   */
  it("test_bff_proxy_absent_cookie_401", async () => {
    // Arrange: track if gateway receives any call
    let upstreamCalled = false;
    server.use(
      http.get("http://gateway.test/admin/keys", () => {
        upstreamCalled = true;
        return HttpResponse.json([]);
      })
    );

    const req = new NextRequest("http://localhost:3000/api/gw/admin/keys", {
      method: "GET",
    });

    // Act
    const res = await proxyGet(req, { params: { path: ["admin", "keys"] } });

    // Assert
    expect(res.status).toBe(401);
    const body = await res.json() as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");

    // No upstream call
    expect(upstreamCalled).toBe(false);
  });

  /**
   * TEST 10 — test_bff_proxy_upstream_401_clears_cookie
   * Scenario: gateway proxy — upstream 401 clears cookie and returns 401
   */
  it("test_bff_proxy_upstream_401_clears_cookie", async () => {
    // Arrange: gateway returns 401
    server.use(
      http.get("http://gateway.test/admin/keys", () =>
        HttpResponse.json(
          { type: "about:blank", title: "Token invalid", status: 401, code: "ERR_AUTH_INVALID_TOKEN" },
          { status: 401 }
        )
      )
    );

    const req = requestWithCookie(
      "http://localhost:3000/api/gw/admin/keys",
      "GET",
      VALID_SESSION_JWT
    );

    // Act
    const res = await proxyGet(req, { params: { path: ["admin", "keys"] } });

    // Assert: BFF returns 401 with ERR_AUTH_SESSION_EXPIRED
    expect(res.status).toBe(401);
    const body = await res.json() as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_SESSION_EXPIRED");

    // Cookie is cleared
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie?.toLowerCase()).toContain("ai_proxy_session=");
    const cookieLower = setCookie?.toLowerCase() ?? "";
    expect(cookieLower.includes("max-age=0") || cookieLower.includes("expires=")).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("DELETE /api/gw/[...path]", () => {
  /**
   * TEST 12 — test_bff_proxy_no_cookie_delete
   * Scenario: /api/gw/[...path] with no cookie — returns 401, no upstream call
   */
  it("test_bff_proxy_no_cookie_delete", async () => {
    let upstreamCalled = false;
    server.use(
      http.delete("http://gateway.test/admin/keys/kid-1", () => {
        upstreamCalled = true;
        return new HttpResponse(null, { status: 204 });
      })
    );

    const req = new NextRequest("http://localhost:3000/api/gw/admin/keys/kid-1", {
      method: "DELETE",
    });

    // Act
    const res = await proxyDelete(req, { params: { path: ["admin", "keys", "kid-1"] } });

    // Assert
    expect(res.status).toBe(401);
    const body = await res.json() as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
    expect(upstreamCalled).toBe(false);
  });
});
