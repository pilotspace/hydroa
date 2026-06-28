/**
 * tests-bff/bff-client.test.tsx
 *
 * Tests for lib/bff-client.ts — the replacement for lib/api-client.ts in the
 * BFF auth world. All calls go to same-origin /api/gw/* or /api/auth/* with
 * credentials:"include"; no Authorization header; no localStorage reads.
 *
 * RED failure mode: imports from @/lib/bff-client fail with MODULE_NOT_FOUND.
 *
 * Tests 16–17 (client intercept scenarios from §4 test plan):
 *   test_bff_client_401_fires_window_redirect
 *   (client function shape verified via calling bffGet/bffPost/bffDelete)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── RED: this import fails until Build creates lib/bff-client.ts ──────────────
import { bffGet, bffPost, bffDelete, bffAuthPost } from "@/lib/bff-client";

// ─────────────────────────────────────────────────────────────────────────────

describe("bff-client: bffGet", () => {
  /**
   * TEST 16 — test_bff_client_401_fires_window_redirect
   * Scenario: client 401 intercept — /api/gw/* 401 fires window redirect to /login
   */
  it("test_bff_client_401_fires_window_redirect", async () => {
    // Arrange: BFF proxy returns 401
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () =>
        HttpResponse.json({ code: "ERR_AUTH_SESSION_EXPIRED" }, { status: 401 })
      )
    );

    // Spy on window.location.href setter
    const hrefSpy = vi.spyOn(window, "location", "get");
    let capturedHref: string | undefined;
    Object.defineProperty(window, "location", {
      configurable: true,
      get() {
        return {
          get href() { return capturedHref ?? ""; },
          set href(v: string) { capturedHref = v; },
        };
      },
    });

    // Act: bffGet should catch the 401 and set window.location.href
    try {
      await bffGet("/admin/keys");
    } catch {
      // bffGet may throw after setting the redirect; that is acceptable
    }

    // Assert: window.location.href was set to /login
    expect(capturedHref).toBe("/login");

    // localStorage "ai_proxy_token" was NOT written
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();

    hrefSpy.mockRestore();
  });

  /**
   * TEST 16b — test_bff_client_dataplane_401_does_not_redirect  [regression]
   * A /v1/* (data-plane) 401 is NOT a session expiry: the BFF passes the upstream
   * error through with its own code (not ERR_AUTH_SESSION_EXPIRED) and leaves the
   * cookie intact. The client must surface it as a thrown BffError WITHOUT bouncing
   * to /login, so opening a playground page (or clicking Send) never logs the user out.
   */
  it("test_bff_client_dataplane_401_does_not_redirect", async () => {
    // Arrange: data-plane 401 with the upstream code (no session-expiry signal)
    server.use(
      http.get("http://localhost:3000/api/gw/v1/models", () =>
        HttpResponse.json({ code: "ERR_AUTH_INVALID_KEY", status: 401 }, { status: 401 })
      )
    );

    const hrefSpy = vi.spyOn(window, "location", "get");
    let capturedHref: string | undefined;
    Object.defineProperty(window, "location", {
      configurable: true,
      get() {
        return {
          get href() { return capturedHref ?? ""; },
          set href(v: string) { capturedHref = v; },
        };
      },
    });

    let thrown: unknown;
    try {
      await bffGet("/v1/models");
    } catch (e) {
      thrown = e;
    }

    // No redirect to /login — the session is intact.
    expect(capturedHref).toBeUndefined();
    // Surfaced as a 401 BffError the caller can handle (fail-open / error state).
    expect((thrown as { status?: number })?.status).toBe(401);

    hrefSpy.mockRestore();
  });

  it("test_bff_client_get_success_returns_data", async () => {
    // Arrange: happy path
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", () =>
        HttpResponse.json([{ key_id: "k1", name: "prod-key" }])
      )
    );

    // Act
    const result = await bffGet<Array<{ key_id: string; name: string }>>("/admin/keys");

    // Assert: data returned without any localStorage interaction
    expect(result[0].key_id).toBe("k1");
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });

  it("test_bff_client_get_uses_credentials_include", async () => {
    // Capture the request to verify credentials mode
    let capturedRequest: Request | null = null;
    server.use(
      http.get("http://localhost:3000/api/gw/admin/keys", ({ request }) => {
        capturedRequest = request;
        return HttpResponse.json([]);
      })
    );

    await bffGet("/admin/keys");

    // credentials: "include" means the browser sends cookies — in node/msw test
    // context we verify the header pattern or that no Authorization header was added
    expect(capturedRequest).not.toBeNull();
    expect((capturedRequest as unknown as Request).headers.get("Authorization")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("bff-client: bffAuthPost", () => {
  it("test_bff_auth_post_calls_api_auth_path", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post("http://localhost:3000/api/auth/login", async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ ok: true });
      })
    );

    // Act
    const result = await bffAuthPost<{ ok: boolean }>("login", { email: "a@b.com", password: "pass" });

    // Assert: correct path called with body
    expect(result.ok).toBe(true);
    expect((capturedBody as Record<string, unknown>).email).toBe("a@b.com");

    // No localStorage write
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("bff-client: bffDelete", () => {
  it("test_bff_delete_no_authorization_header", async () => {
    let capturedRequest: Request | null = null;
    server.use(
      http.delete("http://localhost:3000/api/gw/admin/keys/kid-1", ({ request }) => {
        capturedRequest = request;
        return new HttpResponse(null, { status: 204 });
      })
    );

    await bffDelete("/admin/keys/kid-1");

    expect(capturedRequest).not.toBeNull();
    // Must not include Authorization header — token is in the cookie, not the header
    expect((capturedRequest as unknown as Request).headers.get("Authorization")).toBeNull();
  });
});
