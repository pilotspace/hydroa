/**
 * public-api-base-url.test.ts — RED suite for activation-quickstart TASK.md §3 v1 #5.
 *
 * lib/public-api-base-url.ts does not exist yet — MODULE_NOT_FOUND is the correct
 * RED failure for the import-based tests below.
 *
 * Also guards the load-bearing invariant this task's own §0 grounding names: a
 * `NEXT_PUBLIC_GATEWAY_URL` of that exact shape was already tried and removed as a
 * security fix (dashboard-chart TASK.md, tests/helm/test_dashboard_chart.py
 * test_bff_no_public_gateway_var) — this new helper and every existing BFF resolver
 * route file must never contain that string.
 */
import { describe, it, expect, afterEach } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";

import { publicApiBaseUrl } from "@/lib/public-api-base-url";

const ROOT = resolve(__dirname, "..");
const ORIGINAL = process.env.NEXT_PUBLIC_API_BASE_URL;

afterEach(() => {
  if (ORIGINAL === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL;
});

describe("publicApiBaseUrl (activation-quickstart M5)", () => {
  it("test_public_api_base_url_returns_configured_value_verbatim", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.hydroa.dev";
    expect(publicApiBaseUrl()).toBe("https://api.hydroa.dev");
  });

  it("test_public_api_base_url_returns_null_when_unset", () => {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    expect(publicApiBaseUrl()).toBeNull();
  });

  it("test_public_api_base_url_returns_null_when_empty_string", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "";
    expect(publicApiBaseUrl()).toBeNull();
  });

  it("test_public_api_base_url_helper_never_references_forbidden_name_or_gateway_url", () => {
    const src = readFileSync(resolve(ROOT, "lib/public-api-base-url.ts"), "utf-8");
    expect(src).not.toMatch(/NEXT_PUBLIC_GATEWAY_URL/);
    // The helper's own CODE must never read GATEWAY_URL (prose comments may name
    // it for contrast — this checks the actual process.env access, not commentary).
    expect(src).not.toMatch(/process\.env\.GATEWAY_URL/);
  });

  it("test_no_bff_resolver_route_gained_the_forbidden_public_gateway_var_name", () => {
    // The 8 BFF resolver route files test_bff_no_public_gateway_var already covers —
    // re-asserted here as a build self-check per this task's own "Known-problem
    // fixes" trap, so a build-time grep failure surfaces in THIS suite too.
    const routeFiles = [
      "app/api/auth/login/route.ts",
      "app/api/auth/signup/route.ts",
      "app/api/auth/logout/route.ts",
      "app/api/auth/me/route.ts",
      "app/api/gw/[...path]/route.ts",
    ];
    for (const rel of routeFiles) {
      const abs = resolve(ROOT, rel);
      if (!existsSync(abs)) continue;
      const src = readFileSync(abs, "utf-8");
      expect(src, `${rel} must never reference NEXT_PUBLIC_GATEWAY_URL`).not.toMatch(
        /NEXT_PUBLIC_GATEWAY_URL/,
      );
    }
  });
});
