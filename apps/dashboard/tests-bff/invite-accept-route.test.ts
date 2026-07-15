/**
 * tests-bff/invite-accept-route.test.ts — RED suite for the PUBLIC (unauthenticated)
 * invite-accept BFF route, app/api/auth/invite/[token]/route.ts.
 *
 * Mirrors tests-bff/route-handlers.test.ts's pattern: import the handler function
 * directly, construct a NextRequest, call the handler, assert the NextResponse.
 *
 *   GET  /api/auth/invite/{token}         -> passthrough preview: GET /invites/{token}
 *                                             (relays 404/409/410 problem+json verbatim)
 *   POST /api/auth/invite/{token} {password} -> accept -> login -> Set-Cookie
 *                                             ai_proxy_session (mirrors the signup
 *                                             route's two-step chain). Never leaks the
 *                                             JWT in the body. A failure at EITHER step
 *                                             relays the upstream error and skips login
 *                                             entirely (no cookie set, no auto-login).
 *
 * RED before Build: @/app/api/auth/invite/[token]/route does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention.
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── RED: this import fails until Build creates the route handler file ────────
import { GET as previewHandler, POST as acceptHandler } from "@/app/api/auth/invite/[token]/route";

const GATEWAY = "http://gateway.test";

type RouteContext = { params: Promise<{ token: string }> };

function ctx(token: string): RouteContext {
  return { params: Promise.resolve({ token }) };
}

function jsonRequest(url: string, body: unknown): NextRequest {
  return new NextRequest(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function getSetCookie(res: Response): string | null {
  return res.headers.get("set-cookie") ?? res.headers.get("Set-Cookie");
}

describe("GET /api/auth/invite/[token]", () => {
  it("test_invite_preview_happy_passthrough", async () => {
    server.use(
      http.get(`${GATEWAY}/invites/tok-happy`, () =>
        HttpResponse.json({
          tenant_name: "Acme",
          email: "newbie@acme.io",
          role: "member",
          expires_at: "2026-07-22T00:00:00Z",
        }),
      ),
    );

    const req = new NextRequest("http://localhost:3000/api/auth/invite/tok-happy");
    const res = await previewHandler(req, ctx("tok-happy"));

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.tenant_name).toBe("Acme");
    expect(body.email).toBe("newbie@acme.io");
    expect(body.role).toBe("member");
  });

  it("test_invite_preview_404_relayed_verbatim", async () => {
    server.use(
      http.get(`${GATEWAY}/invites/tok-missing`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Invite not found", status: 404, code: "ERR_INVITE_NOT_FOUND" },
          { status: 404 },
        ),
      ),
    );

    const req = new NextRequest("http://localhost:3000/api/auth/invite/tok-missing");
    const res = await previewHandler(req, ctx("tok-missing"));

    expect(res.status).toBe(404);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_INVITE_NOT_FOUND");
  });

  it("test_invite_preview_409_relayed_verbatim", async () => {
    server.use(
      http.get(`${GATEWAY}/invites/tok-used`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Invite already used", status: 409, code: "ERR_INVITE_NOT_PENDING" },
          { status: 409 },
        ),
      ),
    );

    const req = new NextRequest("http://localhost:3000/api/auth/invite/tok-used");
    const res = await previewHandler(req, ctx("tok-used"));

    expect(res.status).toBe(409);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_INVITE_NOT_PENDING");
  });

  it("test_invite_preview_410_relayed_verbatim", async () => {
    server.use(
      http.get(`${GATEWAY}/invites/tok-expired`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Invite expired", status: 410, code: "ERR_INVITE_EXPIRED" },
          { status: 410 },
        ),
      ),
    );

    const req = new NextRequest("http://localhost:3000/api/auth/invite/tok-expired");
    const res = await previewHandler(req, ctx("tok-expired"));

    expect(res.status).toBe(410);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_INVITE_EXPIRED");
  });
});

describe("POST /api/auth/invite/[token]", () => {
  it("test_invite_accept_happy_sets_cookie_no_token_leak", async () => {
    server.use(
      http.post(`${GATEWAY}/invites/tok-accept/accept`, () =>
        HttpResponse.json(
          { tenant_id: "t-1", user_id: "u-9", email: "newbie@acme.io" },
          { status: 200 },
        ),
      ),
      http.post(`${GATEWAY}/admin/auth/login`, async ({ request }) => {
        const body = (await request.json()) as { email: string; password: string };
        expect(body.email).toBe("newbie@acme.io");
        expect(body.password).toBe("hunter12345");
        return HttpResponse.json({
          access_token: "JWT_FROM_INVITE_ACCEPT",
          token_type: "bearer",
          expires_in: 86400,
        });
      }),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/invite/tok-accept", {
      password: "hunter12345",
    });
    const res = await acceptHandler(req, ctx("tok-accept"));

    expect(res.status).toBe(201);
    const body = (await res.json()) as Record<string, unknown>;
    expect(JSON.stringify(body)).not.toContain("JWT_FROM_INVITE_ACCEPT");

    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_session=JWT_FROM_INVITE_ACCEPT");
    expect(setCookie?.toLowerCase()).toContain("httponly");
    expect(setCookie?.toLowerCase()).toContain("samesite=strict");
  });

  it("test_invite_accept_404_relayed_no_login_no_cookie", async () => {
    let loginCalled = false;
    server.use(
      http.post(`${GATEWAY}/invites/tok-bad/accept`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Invite not found", status: 404, code: "ERR_INVITE_NOT_FOUND" },
          { status: 404 },
        ),
      ),
      http.post(`${GATEWAY}/admin/auth/login`, () => {
        loginCalled = true;
        return HttpResponse.json({ access_token: "SHOULD_NOT_BE_MINTED" });
      }),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/invite/tok-bad", {
      password: "hunter12345",
    });
    const res = await acceptHandler(req, ctx("tok-bad"));

    expect(res.status).toBe(404);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_INVITE_NOT_FOUND");
    expect(loginCalled).toBe(false);
    expect(getSetCookie(res)).toBeNull();
  });

  it("test_invite_accept_410_expired_relayed_no_cookie", async () => {
    server.use(
      http.post(`${GATEWAY}/invites/tok-expired/accept`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Invite expired", status: 410, code: "ERR_INVITE_EXPIRED" },
          { status: 410 },
        ),
      ),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/invite/tok-expired", {
      password: "hunter12345",
    });
    const res = await acceptHandler(req, ctx("tok-expired"));

    expect(res.status).toBe(410);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_INVITE_EXPIRED");
    expect(getSetCookie(res)).toBeNull();
  });

  it("test_invite_accept_missing_password_400_no_upstream_call", async () => {
    let upstreamCalled = false;
    server.use(
      http.post(`${GATEWAY}/invites/tok-noop/accept`, () => {
        upstreamCalled = true;
        return HttpResponse.json({}, { status: 500 });
      }),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/invite/tok-noop", {});
    const res = await acceptHandler(req, ctx("tok-noop"));

    expect(res.status).toBe(400);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_BFF_PAYLOAD_INVALID");
    expect(upstreamCalled).toBe(false);
  });
});
