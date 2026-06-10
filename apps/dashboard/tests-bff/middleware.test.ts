/**
 * tests-bff/middleware.test.ts
 *
 * Tests for apps/dashboard/middleware.ts — the server-side route guard that
 * replaces the client-side isTokenValid checks in KeysPage / UsagePage.
 *
 * RED failure mode: import from "@/middleware" fails with MODULE_NOT_FOUND
 * because middleware.ts does not exist yet.
 *
 * Tests 13–14 (middleware scenarios from §4 test plan):
 *   test_bff_middleware_unauthenticated_redirects_login
 *   test_bff_middleware_with_cookie_passes_through
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";

// ── RED: this import fails until Build creates middleware.ts ──────────────────
import { middleware } from "@/middleware";

// ─────────────────────────────────────────────────────────────────────────────

describe("middleware route guard", () => {
  /**
   * TEST 13 — test_bff_middleware_unauthenticated_redirects_login
   * Scenario: middleware guard — unauthenticated request to /keys redirects to /login
   */
  it("test_bff_middleware_unauthenticated_redirects_login", async () => {
    // Arrange: request to /keys with no ai_proxy_session cookie
    const req = new NextRequest("http://localhost:3000/keys", {
      method: "GET",
      // No Cookie header
    });

    // Act
    const res = await middleware(req);

    // Assert: 307 redirect to /login
    expect(res.status).toBe(307);
    const location = res.headers.get("location");
    expect(location).toContain("/login");
  });

  /**
   * TEST 14 — test_bff_middleware_with_cookie_passes_through
   * Scenario: middleware guard — cookie present allows through to /keys
   */
  it("test_bff_middleware_with_cookie_passes_through", async () => {
    // Arrange: request to /keys WITH a valid session cookie
    const req = new NextRequest("http://localhost:3000/keys", {
      method: "GET",
      headers: {
        Cookie: "ai_proxy_session=some.jwt.token",
      },
    });

    // Act
    const res = await middleware(req);

    // Assert: NOT a redirect (NextResponse.next() does not set Location header)
    // Status 200 or undefined (next() returns an empty response body with no redirect)
    expect(res.status).not.toBe(307);
    expect(res.status).not.toBe(308);
    const location = res.headers.get("location");
    expect(location).toBeNull();
  });

  /**
   * TEST 13b — middleware also guards /usage
   */
  it("test_bff_middleware_usage_unauthenticated_redirects_login", async () => {
    const req = new NextRequest("http://localhost:3000/usage", {
      method: "GET",
    });

    const res = await middleware(req);

    expect(res.status).toBe(307);
    const location = res.headers.get("location");
    expect(location).toContain("/login");
  });
});
