/**
 * tests-bff/join-by-domain-route.test.ts — RED suite for the PUBLIC
 * (unauthenticated) domain-invite-link redeem BFF routes:
 *   app/api/auth/join/[token]/route.ts          (phase 1: redeem)
 *   app/api/auth/join/[token]/verify/route.ts    (phase 2: verify + chained login)
 *
 * Mirrors tests-bff/invite-accept-route.test.ts's pattern: import the handler
 * function directly, construct a NextRequest, call the handler, assert the
 * NextResponse. The gateway endpoints are FROZEN (invite-by-domain-ui shared
 * context, 6a core commit 71641c5):
 *   POST /domain-invite-links/{token}/redeem         {email} -> 202 {email}
 *   POST /domain-invite-links/{token}/redeem/verify  {email,code,password}
 *        -> 201 {tenant_id,user_id,email}
 *   POST /admin/auth/login                           {email,password} -> {access_token}
 *
 * RED before Build: @/app/api/auth/join/[token]/route and
 * @/app/api/auth/join/[token]/verify/route do not exist yet -> MODULE_NOT_FOUND,
 * the established true-red convention.
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── RED: these imports fail until Build creates the two route handler files ──
import { POST as redeemHandler } from "@/app/api/auth/join/[token]/route";
import { POST as verifyHandler } from "@/app/api/auth/join/[token]/verify/route";

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

function problem(title: string, status: number, code: string) {
  return HttpResponse.json({ type: "about:blank", title, status, code }, { status });
}

describe("POST /api/auth/join/[token] — phase 1 redeem (M8)", () => {
  it("test_bff_join_forwards_redeem", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post(`${GATEWAY}/domain-invite-links/tok-happy/redeem`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ email: "sam@acme.com" }, { status: 202 });
      }),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/join/tok-happy", {
      email: "sam@acme.com",
    });
    const res = await redeemHandler(req, ctx("tok-happy"));

    expect(res.status).toBe(202);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.email).toBe("sam@acme.com");
    expect(capturedBody).toEqual({ email: "sam@acme.com" });
  });

  it("test_bff_join_domain_mismatch_relayed", async () => {
    server.use(
      http.post(`${GATEWAY}/domain-invite-links/tok-mismatch/redeem`, () =>
        problem("Domain mismatch", 403, "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH"),
      ),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/join/tok-mismatch", {
      email: "jordan@gmail.com",
    });
    const res = await redeemHandler(req, ctx("tok-mismatch"));

    expect(res.status).toBe(403);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_DOMAIN_INVITE_DOMAIN_MISMATCH");
  });

  it("test_bff_join_missing_email_400_no_upstream_call", async () => {
    let upstreamCalled = false;
    server.use(
      http.post(`${GATEWAY}/domain-invite-links/tok-noop/redeem`, () => {
        upstreamCalled = true;
        return HttpResponse.json({}, { status: 500 });
      }),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/join/tok-noop", {});
    const res = await redeemHandler(req, ctx("tok-noop"));

    expect(res.status).toBe(400);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_BFF_PAYLOAD_INVALID");
    expect(upstreamCalled).toBe(false);
  });
});

describe("POST /api/auth/join/[token]/verify — phase 2 verify + chained login (M9, M10)", () => {
  it("test_bff_verify_chains_login_sets_cookie", async () => {
    server.use(
      http.post(`${GATEWAY}/domain-invite-links/tok-verify/redeem/verify`, () =>
        HttpResponse.json(
          { tenant_id: "t-1", user_id: "u-9", email: "sam@acme.com" },
          { status: 201 },
        ),
      ),
      http.post(`${GATEWAY}/admin/auth/login`, async ({ request }) => {
        const body = (await request.json()) as { email: string; password: string };
        expect(body.email).toBe("sam@acme.com");
        expect(body.password).toBe("hunter123456");
        return HttpResponse.json({
          access_token: "JWT_FROM_DOMAIN_JOIN",
          token_type: "bearer",
          expires_in: 86400,
        });
      }),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/join/tok-verify/verify", {
      email: "sam@acme.com",
      code: "123456",
      password: "hunter123456",
    });
    const res = await verifyHandler(req, ctx("tok-verify"));

    expect(res.status).toBe(201);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ ok: true, session: true });
    expect(JSON.stringify(body)).not.toContain("JWT_FROM_DOMAIN_JOIN");

    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_session=JWT_FROM_DOMAIN_JOIN");
    expect(setCookie?.toLowerCase()).toContain("httponly");
    expect(setCookie?.toLowerCase()).toContain("samesite=strict");
  });

  it("test_bff_verify_login_fallback", async () => {
    server.use(
      http.post(`${GATEWAY}/domain-invite-links/tok-loginfail/redeem/verify`, () =>
        HttpResponse.json(
          { tenant_id: "t-1", user_id: "u-9", email: "sam@acme.com" },
          { status: 201 },
        ),
      ),
      http.post(`${GATEWAY}/admin/auth/login`, () =>
        HttpResponse.json({ title: "Invalid credentials", status: 401 }, { status: 401 }),
      ),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/join/tok-loginfail/verify", {
      email: "sam@acme.com",
      code: "123456",
      password: "hunter123456",
    });
    const res = await verifyHandler(req, ctx("tok-loginfail"));

    expect(res.status).toBe(201);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ ok: true, session: false });
    expect(getSetCookie(res)).toBeNull();
  });

  it.each([
    ["tok-invalid", 400, "ERR_MEMBER_VERIFY_CODE_INVALID"],
    ["tok-expired-code", 410, "ERR_MEMBER_VERIFY_CODE_EXPIRED"],
    ["tok-toomany", 429, "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS"],
    ["tok-weakpw", 400, "ERR_AUTH_PASSWORD_WEAK"],
    ["tok-emailtaken", 409, "ERR_TENANT_EMAIL_TAKEN"],
    ["tok-seatcap", 403, "ERR_PLAN_SEAT_CAP_EXCEEDED"],
    ["tok-mismatch2", 403, "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH"],
    ["tok-inactive", 409, "ERR_DOMAIN_INVITE_LINK_INACTIVE"],
    ["tok-inviteexpired", 410, "ERR_INVITE_EXPIRED"],
    ["tok-notfound", 404, "ERR_INVITE_NOT_FOUND"],
    ["tok-ratelimited", 429, "ERR_RATE_LIMITED"],
  ])(
    "test_bff_verify_passes_through_errors[%s -> %i %s]",
    async (token, status, code) => {
      let loginCalled = false;
      server.use(
        http.post(`${GATEWAY}/domain-invite-links/${token}/redeem/verify`, () =>
          problem("Error", status, code),
        ),
        http.post(`${GATEWAY}/admin/auth/login`, () => {
          loginCalled = true;
          return HttpResponse.json({ access_token: "SHOULD_NOT_BE_MINTED" });
        }),
      );

      const req = jsonRequest(`http://localhost:3000/api/auth/join/${token}/verify`, {
        email: "sam@acme.com",
        code: "123456",
        password: "hunter123456",
      });
      const res = await verifyHandler(req, ctx(token));

      expect(res.status).toBe(status);
      const body = (await res.json()) as Record<string, unknown>;
      expect(body.code).toBe(code);
      expect(loginCalled).toBe(false);
      expect(getSetCookie(res)).toBeNull();
    },
  );

  it("test_bff_verify_missing_fields_400_no_upstream_call", async () => {
    let upstreamCalled = false;
    server.use(
      http.post(`${GATEWAY}/domain-invite-links/tok-noop2/redeem/verify`, () => {
        upstreamCalled = true;
        return HttpResponse.json({}, { status: 500 });
      }),
    );

    const req = jsonRequest("http://localhost:3000/api/auth/join/tok-noop2/verify", {
      email: "sam@acme.com",
    });
    const res = await verifyHandler(req, ctx("tok-noop2"));

    expect(res.status).toBe(400);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_BFF_PAYLOAD_INVALID");
    expect(upstreamCalled).toBe(false);
  });
});
