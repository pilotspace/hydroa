/**
 * tests-bff/impersonation-gw-proxy-cookie-precedence.test.ts
 *
 * RED suite for the impersonation-ui task's MODIFIED, already-existing
 * /api/gw/[...path]/route.ts (TASK.md §3 CONTRACT Part B): token resolution
 * becomes PATH-AWARE — an admin/platform/* path always uses ai_proxy_session,
 * unconditionally (R5), regardless of whether the impersonation cookie is also
 * present; every other path prefers the impersonation cookie's envelope token
 * when present, else falls back to ai_proxy_session exactly as today (M5). The
 * existing control-plane-401-clears-cookie logic becomes source-aware: it clears
 * whichever cookie actually supplied the rejected token, never unconditionally
 * ai_proxy_session (R6).
 *
 * RED failure mode: the import below SUCCEEDS today (the file already exists) —
 * this reds for a clean LOGIC-ASSERTION reason (the new cookie-precedence
 * behavior does not exist yet), never a MODULE_NOT_FOUND — unlike
 * impersonation-mint-end-status-routes.test.ts, which imports 3 NOT-YET-EXISTING
 * route files. Kept in its own file for exactly that reason (a file mixing a
 * not-yet-existing import with an existing one would fail to load entirely).
 *
 * NOTE on test_gw_proxy_always_uses_original_cookie_for_admin_platform_path and
 * test_gw_proxy_falls_back_to_original_when_no_impersonation_cookie: today's
 * UNMODIFIED code already always resolves ai_proxy_session (it has no concept of
 * a second cookie at all), so both assertions ALSO pass against today's code —
 * this is expected, not a test-design flaw. The first is this suite's explicit
 * regression guard (must stay true unchanged); the second's real
 * regression-catching value is entirely post-Build, guarding against a future
 * change that wrongly makes the admin/platform carve-out also prefer the
 * impersonation cookie. test_mint_relays_original_cookie... and the other two
 * tests in this file exercise genuinely NEW behavior and fail today (verified by
 * actually running this file — see the TASK.md's own run-log evidence).
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

import { GET as proxyGet } from "@/app/api/gw/[...path]/route";

const GATEWAY = "http://gateway.test";
const ORIGINAL_TOKEN = "original-session-jwt-001";
const IMPERSONATION_TOKEN = "impersonation-jwt-002";
const TENANT_ID = "11111111-1111-1111-1111-111111111111";

function buildEnvelope(payload: {
  token: string;
  session_id: string;
  expires_at: number;
}): string {
  return encodeURIComponent(JSON.stringify(payload));
}

function requestWithCookies(url: string, cookies: Record<string, string>): NextRequest {
  const cookieHeader = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
  return new NextRequest(url, {
    method: "GET",
    headers: cookieHeader ? { Cookie: cookieHeader } : {},
  });
}

function getSetCookie(res: Response): string | null {
  return res.headers.get("set-cookie") ?? res.headers.get("Set-Cookie");
}

function bothCookies(): Record<string, string> {
  return {
    ai_proxy_session: ORIGINAL_TOKEN,
    ai_proxy_impersonation_session: buildEnvelope({
      token: IMPERSONATION_TOKEN,
      session_id: "33333333-3333-3333-3333-333333333333",
      expires_at: Date.now() + 15 * 60 * 1000,
    }),
  };
}

describe("GET /api/gw/[...path] — impersonation cookie precedence", () => {
  it("test_gw_proxy_prefers_impersonation_cookie_for_ordinary_admin_path", async () => {
    let capturedAuth: string | null = null;
    server.use(
      http.get(`${GATEWAY}/admin/usage`, ({ request }) => {
        capturedAuth = request.headers.get("Authorization");
        return HttpResponse.json({ total_cost_usd: "0.00", records: [] });
      }),
    );

    const req = requestWithCookies("http://localhost:3000/api/gw/admin/usage", bothCookies());
    const res = await proxyGet(req, { params: Promise.resolve({ path: ["admin", "usage"] }) });

    expect(res.status).toBe(200);
    expect(capturedAuth).toBe(`Bearer ${IMPERSONATION_TOKEN}`);
  });

  it("test_gw_proxy_always_uses_original_cookie_for_admin_platform_path", async () => {
    let capturedAuth: string | null = null;
    server.use(
      http.get(`${GATEWAY}/admin/platform/tenants/${TENANT_ID}`, ({ request }) => {
        capturedAuth = request.headers.get("Authorization");
        return HttpResponse.json({
          id: TENANT_ID,
          name: "Acme",
          kind: "standard",
          created_at: "2026-01-01T00:00:00Z",
        });
      }),
    );

    const req = requestWithCookies(
      `http://localhost:3000/api/gw/admin/platform/tenants/${TENANT_ID}`,
      bothCookies(),
    );
    const res = await proxyGet(req, {
      params: Promise.resolve({ path: ["admin", "platform", "tenants", TENANT_ID] }),
    });

    expect(res.status).toBe(200);
    expect(capturedAuth).toBe(`Bearer ${ORIGINAL_TOKEN}`);
  });

  it("test_gw_proxy_falls_back_to_original_when_no_impersonation_cookie", async () => {
    let capturedAuth: string | null = null;
    server.use(
      http.get(`${GATEWAY}/admin/usage`, ({ request }) => {
        capturedAuth = request.headers.get("Authorization");
        return HttpResponse.json({ total_cost_usd: "0.00", records: [] });
      }),
    );

    const req = requestWithCookies("http://localhost:3000/api/gw/admin/usage", {
      ai_proxy_session: ORIGINAL_TOKEN,
    });
    const res = await proxyGet(req, { params: Promise.resolve({ path: ["admin", "usage"] }) });

    expect(res.status).toBe(200);
    expect(capturedAuth).toBe(`Bearer ${ORIGINAL_TOKEN}`);
  });

  it("test_gw_proxy_control_plane_401_clears_only_the_cookie_actually_used", async () => {
    server.use(
      http.get(`${GATEWAY}/admin/usage`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Token invalid", status: 401, code: "ERR_AUTH_INVALID_TOKEN" },
          { status: 401 },
        ),
      ),
    );

    const req = requestWithCookies("http://localhost:3000/api/gw/admin/usage", bothCookies());
    const res = await proxyGet(req, { params: Promise.resolve({ path: ["admin", "usage"] }) });

    expect(res.status).toBe(401);
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    // Only the cookie that actually supplied the rejected token is cleared — the
    // impersonation cookie was used here (an ordinary admin path), so ONLY it is
    // cleared. ai_proxy_session (the real login session) must stay intact —
    // today's UNMODIFIED code clears ai_proxy_session unconditionally instead,
    // so this assertion fails against today's code (genuinely red).
    expect(setCookie).toContain("ai_proxy_impersonation_session=");
    expect(setCookie).not.toContain("ai_proxy_session=");
  });
});
