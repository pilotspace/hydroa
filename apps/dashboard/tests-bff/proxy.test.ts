/**
 * tests-bff/proxy.test.ts  (renamed from middleware.test.ts in the Next 16 upgrade)
 *
 * Tests for apps/dashboard/proxy.ts — the server-side route guard. Next.js 16
 * deprecates the `middleware.ts` filename in favour of `proxy.ts` (export `proxy`).
 * Behavior is BYTE-IDENTICAL to the v13 middleware guard: a cookie-presence check
 * that 307-redirects unauthenticated requests to /login and passes authenticated
 * ones through. This file asserts the SAME behavior under the new name.
 *
 * RED failure mode: import from "@/proxy" fails with MODULE_NOT_FOUND until Build
 * renames middleware.ts → proxy.ts (function middleware → proxy).
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";

// ── RED: this import fails until Build renames middleware.ts → proxy.ts ────────
import { proxy } from "@/proxy";

// ─────────────────────────────────────────────────────────────────────────────

describe("proxy route guard (Next 16)", () => {
  it("test_proxy_unauthenticated_redirects_login", async () => {
    // Arrange: request to /keys with no ai_proxy_session cookie
    const req = new NextRequest("http://localhost:3000/keys", { method: "GET" });

    // Act
    const res = await proxy(req);

    // Assert: 307 redirect to /login
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
  });

  it("test_proxy_with_cookie_passes_through", async () => {
    // Arrange: request WITH a session cookie
    const req = new NextRequest("http://localhost:3000/keys", {
      method: "GET",
      headers: { Cookie: "ai_proxy_session=some.jwt.token" },
    });

    // Act
    const res = await proxy(req);

    // Assert: NOT a redirect (NextResponse.next() sets no Location)
    expect(res.status).not.toBe(307);
    expect(res.status).not.toBe(308);
    expect(res.headers.get("location")).toBeNull();
  });

  it("test_proxy_usage_unauthenticated_redirects_login", async () => {
    const req = new NextRequest("http://localhost:3000/usage", { method: "GET" });

    const res = await proxy(req);

    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
  });
});
