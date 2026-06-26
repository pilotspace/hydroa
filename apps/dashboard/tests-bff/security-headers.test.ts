/**
 * tests-bff/security-headers.test.ts — v50 security-headers-csp
 *
 * Asserts next.config.ts ships the 6 security headers for every route via the
 * pragmatic static CSP (Tin's freeze: script-src 'self' 'unsafe-inline').
 *
 * RED failure mode (pre-build): nextConfig.headers is undefined.
 */

import { describe, it, expect } from "vitest";
import nextConfig from "@/next.config";

type HeaderRule = { source: string; headers: Array<{ key: string; value: string }> };

async function loadRule(): Promise<HeaderRule> {
  expect(typeof nextConfig.headers).toBe("function");
  const rules = (await nextConfig.headers!()) as unknown as HeaderRule[];
  const r = rules.find((x) => x.source === "/:path*");
  expect(r, 'expected a rule for source "/:path*"').toBeDefined();
  return r as HeaderRule;
}

function headerValue(r: HeaderRule, key: string): string {
  const h = r.headers.find((x) => x.key.toLowerCase() === key.toLowerCase());
  return h?.value ?? "";
}

describe("security headers (next.config)", () => {
  it("test_all_six_headers_present", async () => {
    const r = await loadRule();
    const keys = r.headers.map((h) => h.key.toLowerCase());
    for (const k of [
      "content-security-policy",
      "strict-transport-security",
      "x-frame-options",
      "x-content-type-options",
      "referrer-policy",
      "permissions-policy",
    ]) {
      expect(keys, `missing ${k}`).toContain(k);
    }
  });

  it("test_csp_value_is_pragmatic_policy", async () => {
    const csp = headerValue(await loadRule(), "content-security-policy");
    for (const directive of [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'",
      "frame-ancestors 'none'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "upgrade-insecure-requests",
    ]) {
      expect(csp, `CSP missing: ${directive}`).toContain(directive);
    }
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).not.toContain("default-src *");
  });

  it("test_hsts_one_year_subdomains", async () => {
    const hsts = headerValue(await loadRule(), "strict-transport-security");
    const maxAge = Number(/max-age=(\d+)/.exec(hsts)?.[1] ?? "0");
    expect(maxAge).toBeGreaterThanOrEqual(31_536_000);
    expect(hsts).toContain("includeSubDomains");
  });

  it("test_supporting_headers", async () => {
    const r = await loadRule();
    expect(headerValue(r, "x-frame-options")).toBe("DENY");
    expect(headerValue(r, "x-content-type-options")).toBe("nosniff");
    expect(headerValue(r, "referrer-policy")).toBe("strict-origin-when-cross-origin");
    const pp = headerValue(r, "permissions-policy");
    expect(pp).toContain("camera=()");
    expect(pp).toContain("microphone=()");
    expect(pp).toContain("geolocation=()");
  });
});
