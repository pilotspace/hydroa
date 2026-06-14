/**
 * design-system-foundation — automated WCAG 2.2 AA baseline (R5).
 * RED before build: vitest-axe is not installed and AppShell does not exist.
 * Gate: the shell renders with zero serious/critical axe violations.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { axe } from "vitest-axe";
import * as axeMatchers from "vitest-axe/matchers";

import { AppShell } from "@/components/ui/app-shell";

expect.extend(axeMatchers);

// vitest-axe ships matchers but its type augmentation is not auto-applied under
// our tsconfig; declare the one matcher we use so the suite typechecks.
declare module "vitest" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  interface Assertion<T = any> {
    toHaveNoViolations(): T;
  }
  interface AsymmetricMatchersContaining {
    toHaveNoViolations(): unknown;
  }
}

describe("R5 · shell passes an axe baseline (no serious/critical)", () => {
  it("AppShell has no axe violations", async () => {
    const { container } = render(
      <AppShell>
        <h1>Usage</h1>
        <p>Content</p>
      </AppShell>,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
