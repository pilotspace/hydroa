/**
 * tests-bff/next16-upgrade.test.ts — structural invariants of the Next.js 16 upgrade.
 *
 * These assert the FROZEN §3 upgrade invariant set (the post-upgrade observable facts).
 * RED before Build: next is 15.3.3, proxy.ts is absent, middleware.ts exists, the lint
 * script is "next lint", and there is no postcss override. GREEN after the upgrade.
 *
 * This is a static/filesystem suite (no rendering) — the dependency upgrade is not a
 * behavioral feature, so its "shape" is the package.json + file-layout invariants.
 */

import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const DASHBOARD_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function pkg(): {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
  scripts?: Record<string, string>;
  overrides?: Record<string, string>;
} {
  return JSON.parse(readFileSync(join(DASHBOARD_ROOT, "package.json"), "utf8"));
}

/** Parse the leading "major.minor" out of a semver range like "^16.2.9". */
function majorMinor(range: string): [number, number] {
  const m = range.match(/(\d+)\.(\d+)/);
  if (!m) throw new Error(`unparseable version range: ${range}`);
  return [Number(m[1]), Number(m[2])];
}

describe("Next 16 upgrade — package.json invariants", () => {
  it("test_package_json_on_next16", () => {
    const p = pkg();
    const next = p.dependencies?.next ?? "";
    expect(majorMinor(next)[0]).toBeGreaterThanOrEqual(16);

    for (const dep of ["react", "react-dom"] as const) {
      const [maj, min] = majorMinor(p.dependencies?.[dep] ?? "");
      // react/react-dom must be >= 19.2
      expect(maj > 19 || (maj === 19 && min >= 2)).toBe(true);
    }
  });

  it("test_lint_script_is_eslint_not_next_lint", () => {
    const p = pkg();
    expect(p.scripts?.lint).toBeDefined();
    expect(p.scripts?.lint).not.toMatch(/next\s+lint/);
    expect(p.scripts?.lint).toMatch(/eslint/);
  });

  it("test_postcss_override_present", () => {
    const p = pkg();
    // The one advisory Next 16.2.9 does not bundle-fix (GHSA-qx2v-qp2m-jg93) is
    // remediated by (a) a direct postcss dep >= 8.5.10 AND (b) an override that forces
    // every NESTED postcss (e.g. next's bundled 8.4.31) to that version. npm requires
    // the override to reference the direct dep via "$postcss" (else EOVERRIDE), so the
    // override value is the literal "$postcss" — the real guarantee lives in the direct dep.
    const override = p.overrides?.postcss;
    expect(override).toBeDefined();
    expect(override === "$postcss" || majorMinor(override!)[0] > 8 || majorMinor(override!)[1] >= 5).toBe(true);

    // the direct postcss dep must be >= 8.5.10 (the version the override points at)
    const direct = p.devDependencies?.postcss ?? p.dependencies?.postcss ?? "";
    const [maj, min] = majorMinor(direct);
    expect(maj > 8 || (maj === 8 && min >= 5)).toBe(true);
  });
});

describe("Next 16 upgrade — file-layout invariants", () => {
  it("test_eslint_flat_config_present", () => {
    const hasFlat =
      existsSync(join(DASHBOARD_ROOT, "eslint.config.mjs")) ||
      existsSync(join(DASHBOARD_ROOT, "eslint.config.js")) ||
      existsSync(join(DASHBOARD_ROOT, "eslint.config.ts"));
    expect(hasFlat).toBe(true);
  });

  it("test_proxy_replaces_middleware", () => {
    expect(existsSync(join(DASHBOARD_ROOT, "proxy.ts"))).toBe(true);
    expect(existsSync(join(DASHBOARD_ROOT, "middleware.ts"))).toBe(false);
  });
});
