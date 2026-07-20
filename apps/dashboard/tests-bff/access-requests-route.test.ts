/**
 * tests-bff/access-requests-route.test.ts — RED suite for `signup-refusal-router`
 * TASK.md §3 (FROZEN @ v1), M4's BFF half:
 *   POST /api/auth/access-requests   body: { email: string }
 *     -> proxies status + body verbatim to gateway POST /admin/auth/access-requests
 *        (mirrors app/api/auth/signup/route.ts's error-forwarding shape);
 *        never sets a session cookie; never calls /admin/auth/login.
 *
 * Pattern: import the handler function directly, construct a NextRequest, call
 * the handler, assert the NextResponse — mirrors tests-bff/signup-joined-forward.test.ts
 * and tests-bff/join-by-domain-route.test.ts's own established convention.
 *
 * RED reason: @/app/api/auth/access-requests/route does not exist yet ->
 * MODULE_NOT_FOUND, the established true-red convention for a net-new BFF route.
 *
 * SCOPE NOTE (escalated, not silently decided): this file proves the BFF layer
 * does not itself introduce an enumeration asymmetry (it forwards whatever the
 * gateway returns, unmodified, for any email). It does NOT and CANNOT prove
 * the gateway's own anti-enumeration invariant (R3 — "no branch in the
 * request-access code path may read resolve_verified_tenant, any user/tenant
 * table, or any domain-claim state") — that property can only be tested where
 * the actual branching logic could exist: the gateway's own
 * `access_requests` bounded context (pytest). This pass's given execution
 * context (Vitest + Testing Library + axe, "dashboard tests run from
 * apps/dashboard", no pytest/uv tooling named) is dashboard-scoped; the
 * gateway-side RED suite for M4-M7, R1-R2, and the true R3 invariant is
 * flagged in the final report as needing its own dispatch, not silently
 * skipped.
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── RED: this import fails until Build creates the route handler file ────────
import { POST as accessRequestHandler } from "@/app/api/auth/access-requests/route";

const GATEWAY = "http://gateway.test";
const APP = "http://localhost:3000";

function jsonRequest(body: unknown): NextRequest {
  return new NextRequest(`${APP}/api/auth/access-requests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function getSetCookie(res: Response): string | null {
  return res.headers.get("set-cookie") ?? res.headers.get("Set-Cookie");
}

describe("POST /api/auth/access-requests — proxy shape (M4)", () => {
  it("test_bff_access_requests_forwards_email_verbatim", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post(`${GATEWAY}/admin/auth/access-requests`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ ok: true }, { status: 202 });
      }),
    );

    const res = await accessRequestHandler(jsonRequest({ email: "sam@acme.com" }));

    expect(res.status).toBe(202);
    expect(capturedBody).toEqual({ email: "sam@acme.com" });
  });

  it("test_bff_access_requests_forwards_202_uniform_success", async () => {
    // covers: M4, M5 — the gateway's uniform success shape is forwarded verbatim,
    // not re-wrapped or renamed.
    server.use(
      http.post(`${GATEWAY}/admin/auth/access-requests`, () =>
        HttpResponse.json({ ok: true }, { status: 202 }),
      ),
    );

    const res = await accessRequestHandler(jsonRequest({ email: "sam@acme.com" }));

    expect(res.status).toBe(202);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ ok: true });
  });

  it("test_bff_access_requests_forwards_422_validation_error", async () => {
    // covers: R1 — malformed email -> gateway's default pydantic 422 shape
    // forwarded verbatim; nothing captured (asserted at the gateway layer, out
    // of this pass's scope — see file-level SCOPE NOTE).
    server.use(
      http.post(`${GATEWAY}/admin/auth/access-requests`, () =>
        HttpResponse.json(
          { detail: [{ loc: ["body", "email"], msg: "value is not a valid email address", type: "value_error" }] },
          { status: 422 },
        ),
      ),
    );

    const res = await accessRequestHandler(jsonRequest({ email: "not-an-email" }));

    expect(res.status).toBe(422);
    const body = (await res.json()) as { detail?: unknown };
    expect(body.detail).toBeDefined();
  });

  it("test_bff_access_requests_forwards_429_rate_limited", async () => {
    // covers: R2 — reuses the existing generic RATE_LIMITED ErrorSpec verbatim.
    server.use(
      http.post(`${GATEWAY}/admin/auth/access-requests`, () =>
        HttpResponse.json({ type: "about:blank", title: "Rate limit exceeded", status: 429, code: "ERR_RATE_LIMITED" }, { status: 429 }),
      ),
    );

    const res = await accessRequestHandler(jsonRequest({ email: "sam@acme.com" }));

    expect(res.status).toBe(429);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_RATE_LIMITED");
  });

  it("test_bff_access_requests_never_mutates_session", async () => {
    // covers: M4 safety — unlike /api/auth/signup, this route never sets the
    // session cookie and never calls /admin/auth/login (a visitor has no
    // session to create; this is a lead-capture, not an auth event).
    let loginCalled = false;
    server.use(
      http.post(`${GATEWAY}/admin/auth/access-requests`, () =>
        HttpResponse.json({ ok: true }, { status: 202 }),
      ),
      http.post(`${GATEWAY}/admin/auth/login`, () => {
        loginCalled = true;
        return HttpResponse.json({ access_token: "SHOULD_NOT_BE_MINTED" });
      }),
    );

    const res = await accessRequestHandler(jsonRequest({ email: "sam@acme.com" }));

    expect(getSetCookie(res)).toBeNull();
    expect(loginCalled).toBe(false);
  });

  it("test_bff_access_requests_proxy_neutral_across_emails", async () => {
    // covers: R3 boundary (BFF layer only — see file-level SCOPE NOTE). The BFF
    // itself must not introduce ANY asymmetry: given the gateway returns the
    // identical uniform shape for two different emails (mocked identically,
    // since real branching logic is the gateway's job, not this route's), the
    // BFF's own two responses must also be byte-identical.
    server.use(
      http.post(`${GATEWAY}/admin/auth/access-requests`, () =>
        HttpResponse.json({ ok: true }, { status: 202 }),
      ),
    );

    const known = await accessRequestHandler(jsonRequest({ email: "existing@acme.com" }));
    const knownBody = await known.json();
    const unknown = await accessRequestHandler(jsonRequest({ email: "never-seen@example.com" }));
    const unknownBody = await unknown.json();

    expect(known.status).toBe(unknown.status);
    expect(knownBody).toEqual(unknownBody);
  });
});
