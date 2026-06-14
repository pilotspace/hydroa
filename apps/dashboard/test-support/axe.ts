/**
 * test-support/axe.ts — local vitest-axe replacement on axe-core.
 *
 * v17 task devtool-vitest4-upgrade removes the unmaintained `vitest-axe` 0.1.0
 * (peer-incompatible with vitest 4) and provides the exact API the suite already
 * uses, directly over the `axe-core` dependency:
 *   - `axe(container, options?)`  → axe-core's `run`, returning the full AxeResults
 *     (11 of 12 axe tests assert manually on `results.violations`).
 *   - `axeMatchers.toHaveNoViolations` → the one matcher `a11y.test.tsx` extends.
 *
 * Behavior-preserving: it runs axe-core's DEFAULT rule set (same as vitest-axe's
 * thin wrapper did) and forwards caller options (e.g. disabling color-contrast)
 * verbatim — no rule is silenced here.
 */

import * as axeCore from "axe-core";

export type AxeRunOptions = axeCore.RunOptions;
export type AxeResults = axeCore.AxeResults;

/**
 * Run axe-core against a rendered container. Drop-in for vitest-axe's `axe()`.
 */
export async function axe(
  container: axeCore.ElementContext,
  options: axeCore.RunOptions = {},
): Promise<axeCore.AxeResults> {
  return axeCore.run(container, options);
}

function formatViolations(violations: axeCore.Result[]): string {
  return violations
    .map((v) => {
      const nodes = v.nodes.map((n) => `      ${n.target.join(" ")}`).join("\n");
      return `  [${v.impact ?? "n/a"}] ${v.id}: ${v.help}\n${nodes}`;
    })
    .join("\n");
}

/**
 * vitest-axe-compatible matcher: `expect(results).toHaveNoViolations()` passes iff
 * axe found zero violations; on failure it lists each violation + offending nodes.
 */
export const axeMatchers = {
  toHaveNoViolations(results: axeCore.AxeResults) {
    const violations = results?.violations ?? [];
    const pass = violations.length === 0;
    return {
      pass,
      message: () =>
        pass
          ? "expected accessibility violations, but none were found"
          : `expected no accessibility violations but found ${violations.length}:\n${formatViolations(
              violations,
            )}`,
    };
  },
};
