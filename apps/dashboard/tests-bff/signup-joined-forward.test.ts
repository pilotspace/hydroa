/**
 * tests-bff/signup-joined-forward.test.ts — RED suite for domain-auto-assign-login
 * TASK.md §3 v1, M4 (BFF signup route forwards joined_existing_tenant).
 *
 * POST /api/auth/signup currently reads the gateway's signup SUCCESS body ONLY to
 * discard it (only `signupRes.ok` is checked) — `joined_existing_tenant` is
 * silently dropped before the client ever sees it (Ground: "app/api/auth/signup/
 * route.ts ... on success reads only the login token -> joined_existing_tenant
 * silently dropped").
 *
 * Pattern: import the handler function directly, construct a NextRequest, assert
 * on the handler's OWN JSON response body — mirrors
 * tests-bff/signup-account-type-route.test.ts's own convention (new file, does
 * not edit that suite's existing signup test).
 *
 * RED reason today: the route never parses the signup response body, so
 * `joined_existing_tenant` is always absent from the client-facing JSON —
 * `body.joined_existing_tenant` is `undefined`, never `true`.
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

describe("POST /api/auth/signup — joined_existing_tenant forwarding (M4)", () => {
  it("test_bff_signup_forwards_joined_existing_tenant_true", async () => {
    // covers: M4 — gateway success body {joined_existing_tenant: true} forwarded to the client
    server.use(
      http.post("http://gateway.test/admin/auth/signup", () =>
        HttpResponse.json(
          { tenant_id: "t-1", user_id: "u-1", joined_existing_tenant: true },
          { status: 201 },
        ),
      ),
      http.post("http://gateway.test/admin/auth/login", () =>
        HttpResponse.json(
          { access_token: "fake.jwt.token", token_type: "bearer", expires_in: 86400 },
          { status: 200 },
        ),
      ),
    );

    const req = jsonRequest({
      tenant_name: "ignored-when-joining",
      email: "newhire@acme.com",
      password: "hunter12345",
    });
    const res = await signupHandler(req);

    expect(res.status).toBe(201);
    const body = (await res.json()) as { joined_existing_tenant?: boolean };
    expect(body.joined_existing_tenant).toBe(true);
    // never a token in the body, forwarded flag notwithstanding
    expect(JSON.stringify(body)).not.toContain("fake.jwt.token");
  });

  it("test_bff_signup_forwards_joined_existing_tenant_false", async () => {
    // covers: M4 — a brand-new-tenant signup forwards joined_existing_tenant: false
    // (not merely omitted) so the client can branch on it unambiguously.
    server.use(
      http.post("http://gateway.test/admin/auth/signup", () =>
        HttpResponse.json(
          { tenant_id: "t-2", user_id: "u-2", joined_existing_tenant: false },
          { status: 201 },
        ),
      ),
      http.post("http://gateway.test/admin/auth/login", () =>
        HttpResponse.json(
          { access_token: "fake.jwt.token2", token_type: "bearer", expires_in: 86400 },
          { status: 200 },
        ),
      ),
    );

    const req = jsonRequest({
      tenant_name: "Brand New Co",
      email: "founder@brandnew.com",
      password: "hunter12345",
    });
    const res = await signupHandler(req);

    expect(res.status).toBe(201);
    const body = (await res.json()) as { joined_existing_tenant?: boolean };
    expect(body.joined_existing_tenant).toBe(false);
  });
});
