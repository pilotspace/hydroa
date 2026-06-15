/**
 * tests-bff/toolchain-vitest4.test.ts — the vitest-4 toolchain ratchet guard.
 *
 * v17 task devtool-vitest4-upgrade bumps the dashboard test toolchain to vitest 4
 * (+ @vitejs/plugin-react 6 → vite 8) to clear the 7 dev-toolchain advisories
 * (all rooted in the esbuild GHSA-gv7w-rqvm-qjhr, pulled transitively via vite),
 * and removes the unmaintained `vitest-axe` 0.1.0 in favour of a local axe-core
 * shim (test-support/axe.ts). The milestone "strict-gate ratchet" decision says:
 * once tightened, the toolchain floor must STAY tightened — a later task may not
 * silently regress to vitest 3 or reintroduce vitest-axe.
 *
 * RED before Build: package.json still pins vitest 3.2.3 / plugin-react 4.5.1 and
 * lists vitest-axe → fails.
 * GREEN after Build: vitest ≥ 4, @vitest/coverage-v8 ≥ 4, @vitejs/plugin-react ≥ 6,
 * and vitest-axe is absent from every dependency map.
 *
 * Static/source suite (the toolchain floor's "shape" is package.json) — mirrors
 * lint-rules-strict.test.ts.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const TESTS_BFF_ROOT = dirname(fileURLToPath(import.meta.url));

interface PackageJson {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
}

function packageJson(): PackageJson {
  const raw = readFileSync(join(TESTS_BFF_ROOT, "..", "package.json"), "utf8");
  return JSON.parse(raw) as PackageJson;
}

/** Parse the leading major version out of a semver range ("^4.1.8" -> 4). */
function major(range: string | undefined): number {
  if (range === undefined) return NaN;
  const digits = range.replace(/^[~^>=<\s]+/, "").match(/\d+/);
  return digits ? Number(digits[0]) : NaN;
}

describe("vitest-4 toolchain ratchet — package.json", () => {
  it("test_toolchain_pins_vitest4", () => {
    const pkg = packageJson();
    const dev = pkg.devDependencies ?? {};
    expect(major(dev["vitest"])).toBeGreaterThanOrEqual(4);
    expect(major(dev["@vitest/coverage-v8"])).toBeGreaterThanOrEqual(4);
    expect(major(dev["@vitejs/plugin-react"])).toBeGreaterThanOrEqual(6);
  });

  it("test_vitest_axe_removed", () => {
    const pkg = packageJson();
    const all = { ...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {}) };
    // The unmaintained vitest-axe is replaced by the local axe-core shim.
    expect(all).not.toHaveProperty("vitest-axe");
    // …and axe-core remains (it is now the direct axe engine).
    expect(all["axe-core"]).toBeDefined();
  });
});
