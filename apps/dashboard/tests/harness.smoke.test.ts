/**
 * Harness smoke test — proves msw + jsdom + RTL work before the 14 RED tests.
 * This test MUST be green even when all component modules are missing.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { VALID_JWT } from "./mocks/handlers";
import React from "react";

describe("harness smoke", () => {
  it("msw intercepts fetch and jsdom/RTL render a div", async () => {
    // Override default handler for this test only
    server.use(
      http.get("http://gateway.test/admin/keys", () =>
        HttpResponse.json([{ key_id: "smoke", name: "smoke-key", prefix: "sk-smoke", created_at: "2026-01-01T00:00:00Z", revoked_at: null }])
      )
    );

    const res = await fetch("http://gateway.test/admin/keys");
    const data = await res.json() as Array<{ name: string }>;
    expect(data[0].name).toBe("smoke-key");

    // RTL renders React
    const Comp = () => React.createElement("div", { "data-testid": "smoke" }, "ok");
    render(React.createElement(Comp));
    expect(screen.getByTestId("smoke")).toHaveTextContent("ok");
  });

  it("localStorage is reset between tests", () => {
    // Previous test left nothing
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
    localStorage.setItem("ai_proxy_token", "something");
    expect(localStorage.getItem("ai_proxy_token")).toBe("something");
    // afterEach in setup.ts will clear it before the next test
  });

  it("VALID_JWT can be base64url-decoded and has a future exp", () => {
    const parts = VALID_JWT.split(".");
    expect(parts).toHaveLength(3);
    const payload = JSON.parse(
      atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))
    ) as { exp: number };
    expect(payload.exp).toBeGreaterThan(Date.now() / 1000);
  });

  it("next/navigation mock is wired — useRouter returns spy functions", async () => {
    // vi.mock() from setupFiles hoists to module scope; use dynamic import to
    // get the mocked module (require() in ESM context may skip the mock).
    const nav = await import("next/navigation");
    const router = nav.useRouter();
    // router.push must be a vi spy (the mock factory returns vi.fn())
    expect(vi.isMockFunction(router.push)).toBe(true);
    router.push("/nav-smoke-test");
    expect(router.push).toHaveBeenCalledWith("/nav-smoke-test");
  });

  it("GATEWAY_URL env is set to http://gateway.test", () => {
    expect(process.env.GATEWAY_URL).toBe("http://gateway.test");
  });
});
