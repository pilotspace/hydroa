/**
 * tests-bff/signup-account-type-route.test.ts — RED suite for activation-quickstart
 * TASK.md §3 v1, M2 + R1 (BFF signup route forwards/rejects account_type).
 *
 * Pattern: import the handler function directly, construct a NextRequest, call the
 * handler, assert the outbound gateway request body captured via a msw override —
 * mirrors tests-bff/route-handlers.test.ts's own convention (new file, does not
 * edit that suite's existing signup test).
 */
import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

import { POST as signupHandler } from "@/app/api/auth/signup/route";

function jsonRequest(body: unknown): NextRequest {
  return new NextRequest("http://localhost:3000/api/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/auth/signup — account_type (activation-quickstart)", () => {
  /**
   * Scenario: Personal signup sends account_type and reaches the free plan (M1, M3 — BFF half)
   */
  it("test_bff_signup_forwards_personal_verbatim", async () => {
    let gatewayBody: Record<string, unknown> | null = null;
    server.use(
      http.post("http://gateway.test/admin/auth/signup", async ({ request }) => {
        gatewayBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ tenant_id: "t-1", user_id: "u-1" }, { status: 201 });
      })
    );

    const req = jsonRequest({
      tenant_name: "Acme",
      email: "ada@acme.io",
      password: "hunter12345",
      account_type: "personal",
    });
    const res = await signupHandler(req);

    expect(res.status).toBe(201);
    expect(gatewayBody).not.toBeNull();
    const body = gatewayBody as unknown as Record<string, unknown>;
    expect(body.account_type).toBe("personal");
  });

  /**
   * Scenario: A non-dashboard caller omitting account_type is unaffected (M2)
   */
  it("test_bff_signup_omitted_account_type_no_key_forwarded", async () => {
    let gatewayBody: Record<string, unknown> | null = null;
    server.use(
      http.post("http://gateway.test/admin/auth/signup", async ({ request }) => {
        gatewayBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ tenant_id: "t-1", user_id: "u-1" }, { status: 201 });
      })
    );

    const req = jsonRequest({ tenant_name: "Acme", email: "ada@acme.io", password: "hunter12345" });
    const res = await signupHandler(req);

    expect(res.status).toBe(201);
    expect(gatewayBody).not.toBeNull();
    const body = gatewayBody as unknown as Record<string, unknown>;
    expect(Object.prototype.hasOwnProperty.call(body, "account_type")).toBe(false);
  });

  /**
   * Scenario: Tampered account_type value is rejected before reaching the gateway (R1)
   */
  it("test_bff_signup_tampered_account_type_rejected", async () => {
    let gatewayCalled = false;
    server.use(
      http.post("http://gateway.test/admin/auth/signup", () => {
        gatewayCalled = true;
        return HttpResponse.json({}, { status: 500 });
      })
    );

    const req = jsonRequest({
      tenant_name: "Acme",
      email: "ada@acme.io",
      password: "hunter12345",
      account_type: "enterprise",
    });
    const res = await signupHandler(req);

    expect(res.status).toBe(400);
    const body = (await res.json()) as { code?: string };
    expect(body.code).toBe("ERR_BFF_PAYLOAD_INVALID");
    expect(gatewayCalled).toBe(false);
  });

  /**
   * Scenario: Business signup stays byte-identical (M1 — BFF half)
   */
  it("test_bff_signup_forwards_business_verbatim", async () => {
    let gatewayBody: Record<string, unknown> | null = null;
    server.use(
      http.post("http://gateway.test/admin/auth/signup", async ({ request }) => {
        gatewayBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ tenant_id: "t-2", user_id: "u-2" }, { status: 201 });
      })
    );

    const req = jsonRequest({
      tenant_name: "Acme",
      email: "biz@acme.io",
      password: "hunter12345",
      account_type: "business",
    });
    const res = await signupHandler(req);

    expect(res.status).toBe(201);
    expect(gatewayBody).not.toBeNull();
    const body = gatewayBody as unknown as Record<string, unknown>;
    expect(body.account_type).toBe("business");
  });
});
